"""Unit tests for the unified RPent-style comm_harness.planner layer."""

from __future__ import annotations

import base64
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib import request as urllib_request

from comm_harness.planner import (
    ApiAgentLoop,
    Budget,
    CodexPlanner,
    FlashPlanner,
    HttpMcpServer,
    LlmCheckRequest,
    PlannerResult,
    add_mcp_prefix,
    build_planner,
    check_llm,
    parse_model_spec,
    redact_secrets,
    resolve_api_endpoint,
    strip_mcp_prefix,
)


class _DummyToolOutcome:
    """RPent-style ToolResult double with .to_text(), .data, and .images."""

    def __init__(self, data: dict, images: tuple[bytes, ...] = ()):
        self.data = data
        self.images = images
        self.is_error = False

    def to_text(self) -> str:
        return json.dumps(self.data, ensure_ascii=False)


class _DummyToolkit:
    """Minimal RPent/Comm-Harness Toolkit double for testing Planner.solve()."""

    def __init__(self, image_bytes: bytes | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.image_bytes = image_bytes

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "look_around",
                    "description": "Capture panoramic RGB observation.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "move_to",
                    "description": "Move robot to a waypoint.",
                    "parameters": {
                        "type": "object",
                        "properties": {"waypoint": {"type": "integer"}},
                        "required": ["waypoint"],
                    },
                },
            },
        ]

    def execute_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        if name == "look_around":
            imgs = (self.image_bytes,) if self.image_bytes else ()
            return _DummyToolkitOutcome(
                {"status": "ok", "visible_objects": ["chair", "table"]},
                images=imgs,
            )
        if name == "move_to":
            return {"ok": True, "data": {"moved_to": arguments.get("waypoint")}}
        if name == "observe":
            return {"ok": True, "data": {"mode": arguments.get("mode", "panorama")}}
        return {"ok": False, "error": f"unknown tool {name}"}


_DummyToolkitOutcome = _DummyToolOutcome


class _FakeHttpResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _FakeRequestsSession:
    """Fake requests.Session returning scripted Chat Completions HTTP responses."""

    def __init__(self, responses: list[_FakeHttpResponse]):
        self._responses = list(responses)
        self.requests: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: tuple):
        self.requests.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if not self._responses:
            raise AssertionError("No more scripted responses in _FakeRequestsSession")
        return self._responses.pop(0)


def _make_chat_http_response(
    content: str = "",
    tool_calls: list[tuple[str, str, dict]] | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    finish_reason: str = "stop",
) -> _FakeHttpResponse:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for tc_id, name, args in tool_calls
        ]
    return _FakeHttpResponse(
        200,
        {
            "model": "gpt-5.4",
            "choices": [{"message": msg, "finish_reason": finish_reason}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
    )


class PlannerEndpointAndFactoryTests(unittest.TestCase):
    def test_parse_and_resolve_sub2api_deepseek_ollama_openai(self):
        self.assertEqual(parse_model_spec("sub2api:gpt-5.4"), ("sub2api", "gpt-5.4"))
        self.assertEqual(
            parse_model_spec("ollama:qwen3.8:27b"), ("ollama", "qwen3.8:27b")
        )
        self.assertEqual(
            parse_model_spec("qwen3.8:27b"), ("ollama", "qwen3.8:27b")
        )
        self.assertEqual(
            parse_model_spec("deepseek-chat", default_provider="deepseek"),
            ("deepseek", "deepseek-chat"),
        )

        ep_sub2 = resolve_api_endpoint(
            "sub2api:gpt-5.4",
            api_key="sk-sub2",
            base_url="http://127.0.0.1:8317/v1",
        )
        self.assertEqual(ep_sub2.provider, "sub2api")
        self.assertEqual(ep_sub2.model, "gpt-5.4")
        self.assertEqual(ep_sub2.api_key, "sk-sub2")
        self.assertEqual(ep_sub2.base_url, "http://127.0.0.1:8317/v1")

        ep_ollama = resolve_api_endpoint("ollama:qwen3.8:27b")
        self.assertEqual(ep_ollama.provider, "ollama")
        self.assertEqual(ep_ollama.model, "qwen3.8:27b")
        self.assertEqual(ep_ollama.api_key, "ollama")

    def test_mcp_prefix_helpers(self):
        prefixed = add_mcp_prefix("move_to")
        self.assertEqual(prefixed, "mcp__comm_harness__move_to")
        self.assertEqual(strip_mcp_prefix(prefixed), "move_to")
        self.assertEqual(strip_mcp_prefix("mcp__rpent__move_to"), "move_to")

    def test_build_planner_dispatches_api_codex_flash(self):
        p_api = build_planner(
            "api",
            model="sub2api:gpt-4o",
            api_key="sk-test",
            base_url="http://127.0.0.1:8317/v1",
        )
        self.assertIsInstance(p_api, ApiAgentLoop)
        self.assertEqual(p_api.endpoint.provider, "sub2api")
        self.assertEqual(p_api.endpoint.model, "gpt-4o")

        p_codex = build_planner("codex", model="gpt-5.4", api_key="sk-test")
        self.assertIsInstance(p_codex, CodexPlanner)

        p_flash = build_planner("flash", recipe_tag="smoke", robot_name="hm3d_m3")
        self.assertIsInstance(p_flash, FlashPlanner)


class ApiAgentLoopSolveTests(unittest.TestCase):
    def test_solve_executes_multi_turn_tool_calls_and_stops_on_finish(self):
        fake_session = _FakeRequestsSession(
            [
                _make_chat_http_response(
                    content="Scanning surroundings first.",
                    tool_calls=[("call_1", "look_around", {})],
                ),
                _make_chat_http_response(
                    content="Moving to waypoint 2 and finishing.",
                    tool_calls=[
                        ("call_2", "move_to", {"waypoint": 2}),
                        (
                            "call_3",
                            "finish",
                            {"status": "success", "summary": "Reached the chair"},
                        ),
                    ],
                ),
            ]
        )
        endpoint = resolve_api_endpoint(
            "sub2api:gpt-5.4",
            api_key="sk-test",
            base_url="http://127.0.0.1:8317/v1",
        )
        planner = ApiAgentLoop(
            endpoint=endpoint,
            session=fake_session,
        )
        toolkit = _DummyToolkit(image_bytes=b"\x89PNG\r\n\x1a\nfake")
        result = planner.solve(
            system_prompt="You are robot_0.",
            user_message="Find the chair.",
            toolkit=toolkit,
            max_turns=5,
        )

        self.assertIsInstance(result, PlannerResult)
        self.assertIsNone(result.error)
        self.assertEqual(result.stats["turns_used"], 2)
        self.assertEqual(result.stats["tool_calls"], 3)
        self.assertEqual(result.stats["total_input_tokens"], 20)
        self.assertEqual(result.stats["total_output_tokens"], 10)
        self.assertEqual(result.finish_result["status"], "success")
        self.assertEqual(result.finish_result["summary"], "Reached the chair")
        self.assertEqual(
            toolkit.calls,
            [
                ("look_around", {}),
                ("move_to", {"waypoint": 2}),
            ],
        )
        # Verify observation image from look_around was injected as a multimodal user message in turn 2
        second_req_messages = fake_session.requests[1]["json"]["messages"]
        has_image_url = any(
            isinstance(m.get("content"), list)
            and any(b.get("type") == "image_url" for b in m["content"])
            for m in second_req_messages
        )
        self.assertTrue(has_image_url)

    def test_solve_respects_no_images_and_call_budget(self):
        fake_session = _FakeRequestsSession(
            [
                _make_chat_http_response(
                    content="Turn 1",
                    tool_calls=[("call_1", "look_around", {})],
                    prompt_tokens=100,
                    completion_tokens=50,
                ),
                _make_chat_http_response(
                    content="Turn 2",
                    tool_calls=[("call_2", "look_around", {})],
                    prompt_tokens=100,
                    completion_tokens=50,
                ),
            ]
        )
        endpoint = resolve_api_endpoint(
            "deepseek:deepseek-chat",
            api_key="sk-test",
        )
        planner = ApiAgentLoop(
            endpoint=endpoint,
            no_images=True,
            budget=Budget(max_calls=1, max_tokens=10000),
            session=fake_session,
        )
        toolkit = _DummyToolkit(image_bytes=b"\x89PNG\r\n\x1a\nfake")
        result = planner.solve(
            system_prompt="System",
            user_message="Go",
            toolkit=toolkit,
            max_turns=5,
        )
        # Budget exhausted stops cleanly after 1 request without sending a second HTTP call
        self.assertEqual(len(fake_session.requests), 1)
        self.assertEqual(result.stats["turns_used"], 1)
        self.assertIsNone(result.error)

    def test_solve_with_ollama_tool_calls(self):
        class _FakeOllamaClient:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                tc = {
                    "id": "ollama_call_1",
                    "type": "function",
                    "function": {
                        "name": "finish",
                        "arguments": json.dumps(
                            {"status": "success", "summary": "Done via Ollama"}
                        ),
                    },
                }
                msg = SimpleNamespace(content="Finishing.", tool_calls=[tc])
                choice = SimpleNamespace(message=msg, finish_reason="stop")
                usage = SimpleNamespace(
                    prompt_tokens=12, completion_tokens=6, total_tokens=18
                )
                return SimpleNamespace(
                    model="qwen3.8:27b", choices=[choice], usage=usage
                )

        fake_ollama = _FakeOllamaClient()
        endpoint = resolve_api_endpoint("ollama:qwen3.8:27b")
        planner = ApiAgentLoop(endpoint=endpoint, ollama_client=fake_ollama)
        toolkit = _DummyToolkit()
        result = planner.solve(
            system_prompt="You are a local Ollama robot planner.",
            user_message="Finish immediately.",
            toolkit=toolkit,
            max_turns=3,
        )
        self.assertEqual(len(fake_ollama.calls), 1)
        self.assertIsNotNone(fake_ollama.calls[0]["tools"])
        self.assertEqual(result.finish_result["summary"], "Done via Ollama")
        self.assertEqual(result.stats["provider"], "ollama")


class FlashPlannerAndHttpMcpServerTests(unittest.TestCase):
    def test_flash_planner_replays_runner(self):
        toolkit = _DummyToolkit()

        def custom_runner(tk, tag, log_fn):
            tk.execute_tool("look_around", {})
            tk.execute_tool("move_to", {"waypoint": 3})
            return {"done": True, "program": tag, "plan": 2}

        planner = FlashPlanner(recipe_tag="demo_recipe", runner=custom_runner)
        res = planner.solve(
            system_prompt="sys",
            user_message="usr",
            toolkit=toolkit,
            max_turns=5,
        )
        self.assertEqual(res.finish_result["status"], "success")
        self.assertEqual(res.stats["tool_calls"], 2)
        self.assertEqual(
            toolkit.calls,
            [("look_around", {}), ("move_to", {"waypoint": 3})],
        )

    def test_http_mcp_server_jsonrpc_list_and_call(self):
        toolkit = _DummyToolkit(image_bytes=b"png_bytes")
        with HttpMcpServer(toolkit) as server:
            self.assertTrue(server.url.startswith("http://127.0.0.1:"))

            def post_rpc(payload: dict) -> dict:
                req = urllib_request.Request(
                    server.url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib_request.urlopen(req, timeout=5) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            init_res = post_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            self.assertEqual(
                init_res["result"]["serverInfo"]["name"], "comm_harness"
            )

            list_res = post_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tool_names = [t["name"] for t in list_res["result"]["tools"]]
            self.assertIn("look_around", tool_names)
            self.assertIn("move_to", tool_names)

            call_res = post_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "mcp__comm_harness__look_around",
                        "arguments": {},
                    },
                }
            )
            self.assertFalse(call_res["result"]["isError"])
            contents = call_res["result"]["content"]
            self.assertEqual(contents[0]["type"], "text")
            self.assertEqual(contents[1]["type"], "image")
            self.assertEqual(
                contents[1]["data"],
                base64.b64encode(b"png_bytes").decode("ascii"),
            )
            self.assertEqual(toolkit.calls, [("look_around", {})])


class LlmCheckTests(unittest.TestCase):
    def test_redact_secrets_and_missing_api_key(self):
        with patch.dict(
            os.environ,
            {"SUB2API_API_KEY": "sk-super-secret-sub2api-token"},
            clear=True,
        ):
            text = "Error with key sk-super-secret-sub2api-token on gateway"
            redacted = redact_secrets(text)
            self.assertNotIn("sk-super-secret-sub2api-token", redacted)
            self.assertIn("<SUB2API_API_KEY>", redacted)

        with patch.dict(os.environ, {}, clear=True):
            res = check_llm(
                LlmCheckRequest(
                    planner="api",
                    model="deepseek:deepseek-chat",
                    api_key=None,
                )
            )
            self.assertFalse(res.ok)
            self.assertEqual(res.status, "missing_api_key")


if __name__ == "__main__":
    unittest.main()
