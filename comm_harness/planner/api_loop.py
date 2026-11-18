"""API planner for OpenAI-compatible (Sub2API, DeepSeek, OpenAI, vLLM) and Ollama endpoints.

Modeled after RPent's ``rpent/planner/api_loop.py``, implemented without
Python 3.10-only dependencies so that every robot harness can run the exact same
``ApiAgentLoop`` inside both Python 3.8 (Habitat simulator workers) and Python 3.10+.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable, Dict, List, Optional, Sequence
import requests

from comm_harness import telemetry
from comm_harness.planner.base import (
    REASONING_EFFORTS,
    Budget,
    ModelError,
    Planner,
    PlannerResult,
    ResolvedEndpoint,
    invoke_toolkit_sync,
    resolve_api_endpoint,
)
from comm_harness.planner.ollama_client import OllamaClient

DEFAULT_FINISH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["success", "failure", "stuck"],
            "description": "Final task outcome reported by the agent.",
        },
        "summary": {
            "type": "string",
            "maxLength": 2000,
            "description": "Concise summary of what was accomplished and why the run is ending.",
        },
    },
    "required": ["status", "summary"],
    "additionalProperties": False,
}


def _extract_toolkit_schemas(toolkit: Any) -> List[Dict[str, Any]]:
    """Derive OpenAI-compatible function tool schemas from any supported toolkit."""
    if hasattr(toolkit, "schemas") and callable(toolkit.schemas):
        return list(toolkit.schemas())
    if hasattr(toolkit, "list_tools") and callable(toolkit.list_tools):
        schemas: List[Dict[str, Any]] = []
        for decl in toolkit.list_tools():
            name = getattr(decl, "name", None) or decl.get("name")
            desc = getattr(decl, "description", "") or decl.get("description", "")
            params = (
                getattr(decl, "input_schema", None)
                or getattr(decl, "parameters", None)
                or (decl.get("input_schema") if isinstance(decl, dict) else None)
                or {"type": "object", "properties": {}}
            )
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": str(name),
                        "description": str(desc),
                        "parameters": params,
                    },
                }
            )
        return schemas
    return []


def _strip_images_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Replace inline ``image_url`` blocks with a text notice when ``no_images=True``."""
    cleaned: List[Dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            cleaned.append(msg)
            continue
        new_parts: List[Dict[str, Any]] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                new_parts.append(
                    {"type": "text", "text": "[Image omitted: --no-images is enabled.]"}
                )
            else:
                new_parts.append(part)
        cleaned.append({**msg, "content": new_parts})
    return cleaned


def _sliding_window_messages(
    messages: List[Dict[str, Any]],
    *,
    max_messages: int = 80,
    keep_messages: int = 40,
) -> List[Dict[str, Any]]:
    """Compact conversation history while keeping the system prompt, opening user prompt,
    and the most recent ``keep_messages`` intact (aligned with RPent's SlidingWindowCompaction).
    """
    if len(messages) <= max_messages:
        return messages
    head: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(messages) and messages[idx].get("role") == "system":
        head.append(messages[idx])
        idx += 1
    if idx < len(messages) and messages[idx].get("role") == "user":
        head.append(messages[idx])
        idx += 1
    tail = messages[-keep_messages:]
    # Avoid splitting a tool response from its preceding assistant tool_calls message
    while tail and tail[0].get("role") == "tool" and len(tail) < len(messages) - idx:
        keep_messages += 1
        tail = messages[-keep_messages:]
    return head + tail


class ApiAgentLoop(Planner):
    """Unified multi-turn tool-calling planner for ChatGPT/OpenAI-compatible APIs
    (Sub2API, DeepSeek, OpenAI, vLLM) and Ollama.

    Can be invoked via RPent's standard :meth:`solve` interface or via :meth:`decide`
    for tick-stepped multi-robot nodes (`HarnessNode`).
    """

    def __init__(
        self,
        config_or_endpoint: Optional[Any] = None,
        key: str = "",
        budget: Optional[Budget] = None,
        emit: Optional[Callable[..., None]] = None,
        *,
        endpoint: Optional[ResolvedEndpoint] = None,
        max_tokens: int = 4096,
        reasoning_effort: str = "none",
        no_images: bool = False,
        timeout_s: float = 120.0,
        max_tool_rounds: int = 5,
        node_id: str = "robot_0",
        session: Optional[Any] = None,
        ollama_client: Optional[OllamaClient] = None,
    ) -> None:
        if reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"unsupported reasoning effort: {reasoning_effort}")
        self.emit = emit or (lambda *a, **k: None)
        self.no_images = bool(no_images)
        self.reasoning_effort = reasoning_effort
        self.node_id = node_id
        self._session = session
        self._ollama_client = ollama_client

        # Support legacy DeepSeekPlanner(config, key, budget, emit) positional constructor
        if isinstance(config_or_endpoint, ResolvedEndpoint):
            endpoint = config_or_endpoint
            self.config: Dict[str, Any] = {}
        elif isinstance(config_or_endpoint, dict):
            self.config = dict(config_or_endpoint)
        else:
            self.config = {}

        if endpoint is not None:
            self.endpoint = endpoint
            self.key = endpoint.api_key
            self.max_tokens = int(self.config.get("max_output_tokens", max_tokens))
            self.timeout_s = float(self.config.get("timeout_s", timeout_s))
            self.max_tool_rounds = int(self.config.get("max_tool_rounds", max_tool_rounds))
        else:
            provider = self.config.get("provider") or self.config.get("mode") or "deepseek"
            if provider == "api":
                provider = None
            model_name = self.config.get("model", "deepseek-flash")
            base_url = self.config.get("base_url", "https://api.deepseek.com")
            self.key = key or self.config.get("api_key", "")
            self.endpoint = resolve_api_endpoint(
                model_name,
                provider=provider,
                base_url=base_url,
                api_key=self.key or " placeholder ",
            )
            if not self.key and self.endpoint.provider == "ollama":
                self.key = "ollama"
            self.max_tokens = int(self.config.get("max_output_tokens", 768))
            self.timeout_s = float(self.config.get("timeout_s", 45.0))
            self.max_tool_rounds = int(self.config.get("max_tool_rounds", max_tool_rounds))

        self.budget = budget or Budget(
            max_calls=int(self.config.get("max_calls", 60)),
            max_tokens=int(self.config.get("max_total_tokens", 250000)),
        )

    def _build_chat_url(self) -> str:
        base = self.endpoint.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _request_ollama(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        node_id: str,
        purpose: str,
    ) -> Dict[str, Any]:
        client = self._ollama_client or OllamaClient(
            self.endpoint.base_url,
            num_ctx=int(self.config.get("num_ctx", 32768)),
            num_predict=int(self.config.get("num_predict", self.max_tokens)),
            seed=int(self.config.get("seed", 1123)),
        )
        self.budget.reserve()
        usage_dict: Dict[str, int] = {}
        start = time.monotonic()
        try:
            with telemetry.span(
                "MODEL_REQUEST",
                attempt=1,
                model=self.endpoint.model,
                provider="ollama",
                node_id=node_id,
                purpose=purpose,
            ):
                resp = client.create(
                    model=self.endpoint.model,
                    messages=messages,
                    temperature=float(self.config.get("temperature", 0.0)),
                    max_tokens=self.max_tokens,
                    timeout=self.timeout_s,
                    tools=tools if tools else None,
                )
            usage = getattr(resp, "usage", None)
            in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
            out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
            tot_tok = int(getattr(usage, "total_tokens", in_tok + out_tok) or 0)
            usage_dict = {
                "prompt_tokens": in_tok,
                "completion_tokens": out_tok,
                "total_tokens": tot_tok,
            }
            choice = resp.choices[0]
            msg_obj = choice.message
            message: Dict[str, Any] = {
                "role": "assistant",
                "content": getattr(msg_obj, "content", "") or "",
            }
            if getattr(msg_obj, "tool_calls", None):
                message["tool_calls"] = list(msg_obj.tool_calls)
            telemetry.emit(
                "MODEL_RESPONSE",
                node_id=node_id,
                purpose=purpose,
                empty=not bool(message["content"] or message.get("tool_calls")),
                usage={
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "total_tokens": tot_tok,
                },
            )
            self.emit(
                "model_response",
                node_id=node_id,
                purpose=purpose,
                model=getattr(resp, "model", self.endpoint.model),
                usage=usage_dict,
                finish_reason=getattr(choice, "finish_reason", "stop"),
                latency_s=round(time.monotonic() - start, 3),
                message=message,
            )
            return message
        except Exception as exc:
            self.emit("model_error", node_id=node_id, purpose=purpose, error=str(exc))
            raise ModelError(f"ollama_request_failed: {exc}") from exc
        finally:
            self.budget.finish(
                usage_dict.get("total_tokens", 0),
                input_tokens=usage_dict.get("prompt_tokens", 0),
                output_tokens=usage_dict.get("completion_tokens", 0),
            )

    def request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        node_id: Optional[str] = None,
        purpose: str = "decision",
        tool_choice: Any = "auto",
    ) -> Dict[str, Any]:
        """Send one Chat Completions request (supporting Sub2API, DeepSeek, OpenAI, and Ollama)."""
        nid = node_id or self.node_id
        if self.no_images:
            messages = _strip_images_from_messages(messages)

        if self.endpoint.provider == "ollama":
            return self._request_ollama(messages, tools, nid, purpose)

        if not self.key:
            env_hint = self.endpoint.credential_env or "DEEPSEEK_API_KEY"
            raise ModelError(f"{env_hint} is not configured")

        payload: Dict[str, Any] = {
            "model": self.endpoint.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": float(self.config.get("temperature", 0)),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        # Provider-specific reasoning controls
        if self.endpoint.provider == "deepseek" or self.endpoint.model.lower().startswith("deepseek"):
            if self.reasoning_effort == "none":
                payload["thinking"] = {"type": "disabled"}
        elif self.reasoning_effort != "none":
            payload["reasoning_effort"] = self.reasoning_effort

        post_fn = self._session.post if self._session is not None else requests.post
        url = self._build_chat_url()

        for attempt in range(2):
            self.budget.reserve()
            usage: Dict[str, Any] = {}
            start = time.monotonic()
            try:
                with telemetry.span(
                    "MODEL_REQUEST",
                    attempt=attempt + 1,
                    model=self.endpoint.model,
                    provider=self.endpoint.provider,
                    node_id=nid,
                    purpose=purpose,
                ):
                    response = post_fn(
                        url,
                        headers={"Authorization": "Bearer " + self.key},
                        json=payload,
                        timeout=(10, self.timeout_s),
                    )
                if response.status_code != 200:
                    self.emit(
                        "model_error",
                        node_id=nid,
                        purpose=purpose,
                        http_status=response.status_code,
                        attempt=attempt,
                    )
                    # Auto-repair if an upstream Sub2API/OpenAI gateway rejects a non-standard field
                    if response.status_code == 400 and attempt == 0:
                        body_text = getattr(response, "text", "") or ""
                        if "thinking" in payload and "thinking" in body_text:
                            payload.pop("thinking", None)
                            continue
                        if "reasoning_effort" in payload and "reasoning_effort" in body_text:
                            payload.pop("reasoning_effort", None)
                            continue
                        if "max_tokens" in payload and "max_completion_tokens" in body_text:
                            payload["max_completion_tokens"] = payload.pop("max_tokens")
                            continue
                    if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                        time.sleep(1)
                        continue
                    raise ModelError("model_http_%d" % response.status_code)

                data = response.json()
                usage = data.get("usage") or {}
                choice = data["choices"][0]
                message = choice["message"]
                finish_reason = choice.get("finish_reason")
                telemetry.emit(
                    "MODEL_RESPONSE",
                    node_id=nid,
                    purpose=purpose,
                    empty=not bool(message.get("content") or message.get("tool_calls")),
                    usage={
                        "input_tokens": usage.get("prompt_tokens"),
                        "output_tokens": usage.get("completion_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                    }
                    if usage
                    else None,
                )
                self.emit(
                    "model_response",
                    node_id=nid,
                    purpose=purpose,
                    model=data.get("model"),
                    usage=usage,
                    finish_reason=finish_reason,
                    latency_s=round(time.monotonic() - start, 3),
                    message=message,
                )
                if finish_reason == "length":
                    raise ModelError("model_response_truncated")
                return message
            except requests.RequestException:
                self.emit("model_error", node_id=nid, purpose=purpose, error="network_error")
                if attempt:
                    raise ModelError("model_network_error")
                time.sleep(1)
            except (KeyError, ValueError, TypeError) as exc:
                if isinstance(exc, ModelError):
                    raise
                raise ModelError("invalid_model_response") from exc
            finally:
                in_tok = int(usage.get("prompt_tokens", 0) or 0)
                out_tok = int(usage.get("completion_tokens", 0) or 0)
                tot_tok = int(usage.get("total_tokens", in_tok + out_tok) or 0)
                self.budget.finish(tot_tok, input_tokens=in_tok, output_tokens=out_tok)
        raise ModelError("model_request_failed")

    def decide(self, node: Any) -> None:
        """Run one decision turn for a tick-stepped multi-robot ``HarnessNode``."""
        messages = node.prompt_messages()
        for _ in range(self.max_tool_rounds):
            message = self.request(messages, node.toolkit.schemas(), node.node_id)
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Use an available tool to navigate, observe, or verify. "
                            "Natural language alone does not execute an action."
                        ),
                    }
                )
                continue
            for call in calls:
                f = call["function"]
                result = node.toolkit.execute(f["name"], f["arguments"])
                node.history.append({"tick": node.tick, "tool": f["name"], "result": result})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", f["name"]),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            if node.has_command() or node.just_claimed:
                return
        raise ModelError("decision_tool_round_limit")

    def solve(
        self,
        *,
        system_prompt: str,
        user_message: Any,
        toolkit: Any,
        max_turns: int,
        input_queue: Optional[Any] = None,
        dashboard_interaction: Optional[Any] = None,
    ) -> PlannerResult:
        """Run RPent's standard ``solve()`` loop for an independent robot harness."""
        if max_turns < 1:
            raise ValueError("max_turns must be positive")

        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if user_message is not None:
            messages.append({"role": "user", "content": user_message})

        tools = _extract_toolkit_schemas(toolkit)
        tool_names = {t["function"]["name"] for t in tools if "function" in t}
        inject_finish = "finish" not in tool_names and "declare_goal" not in tool_names
        if inject_finish:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "finish",
                        "description": "Complete the current task and report the final outcome.",
                        "parameters": DEFAULT_FINISH_SCHEMA,
                    },
                }
            )

        finish_result: Optional[Dict[str, Any]] = None
        error: Optional[str] = None
        turns_used = 0
        tool_calls_count = 0
        start_in_tok = self.budget.input_tokens
        start_out_tok = self.budget.output_tokens
        started = time.monotonic()

        try:
            for _ in range(max_turns):
                if input_queue is not None:
                    while not input_queue.empty():
                        try:
                            user_line = input_queue.get_nowait()
                        except Exception:
                            break
                        if user_line is None:
                            return PlannerResult(
                                finish_result=finish_result,
                                messages=messages,
                                stats=self._build_stats(
                                    turns_used,
                                    tool_calls_count,
                                    start_in_tok,
                                    start_out_tok,
                                    started,
                                ),
                                error=None,
                            )
                        messages.append({"role": "user", "content": str(user_line)})

                windowed = _sliding_window_messages(messages)
                assistant_msg = self.request(windowed, tools, self.node_id, purpose="solve_turn")
                turns_used += 1
                messages.append(assistant_msg)

                calls = assistant_msg.get("tool_calls") or []
                if not calls:
                    # If no tools exist at all, a single assistant reply completes the turn
                    if not tools:
                        break
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Please call an available tool to proceed, or call finish "
                                "when the task is complete."
                            ),
                        }
                    )
                    continue

                pending_images: List[Dict[str, Any]] = []
                for call in calls:
                    fn = call["function"]
                    name = fn["name"]
                    raw_args = fn.get("arguments", "{}")
                    try:
                        parsed_args = (
                            json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
                        )
                    except Exception as exc:
                        tool_calls_count += 1
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.get("id", name),
                                "content": json.dumps(
                                    {"ok": False, "error": f"Invalid JSON arguments: {exc}"}
                                ),
                            }
                        )
                        continue

                    tool_calls_count += 1
                    if name == "finish" and inject_finish:
                        finish_result = {
                            "_finish": True,
                            "status": parsed_args.get("status", "success"),
                            "summary": parsed_args.get("summary", ""),
                        }
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.get("id", name),
                                "content": json.dumps(finish_result, ensure_ascii=False),
                            }
                        )
                        break

                    raw_result = invoke_toolkit_sync(toolkit, name, parsed_args)
                    text_out, images_out, is_finish, finish_payload = self._normalize_tool_result(
                        name, raw_result
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", name),
                            "content": text_out,
                        }
                    )
                    if not self.no_images and images_out:
                        for img_b64 in images_out:
                            pending_images.append(
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                                }
                            )
                    if is_finish:
                        finish_result = finish_payload
                        break

                if pending_images:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Observation images from tool execution:"},
                                *pending_images,
                            ],
                        }
                    )

                if finish_result is not None:
                    break
                should_yield = getattr(toolkit, "should_yield_turn", None)
                if callable(should_yield) and should_yield():
                    break
        except ModelError as exc:
            if str(exc) == "model_budget_exhausted":
                error = None
            elif str(exc) == "model_response_truncated":
                error = f"planner response truncated (length): {exc}"
            else:
                error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        return PlannerResult(
            finish_result=finish_result,
            messages=messages,
            stats=self._build_stats(
                turns_used, tool_calls_count, start_in_tok, start_out_tok, started
            ),
            error=error,
        )

    def _build_stats(
        self,
        turns_used: int,
        tool_calls: int,
        start_in_tok: int,
        start_out_tok: int,
        started: float,
    ) -> Dict[str, Any]:
        in_tok = max(0, self.budget.input_tokens - start_in_tok)
        out_tok = max(0, self.budget.output_tokens - start_out_tok)
        return {
            "backend": "api",
            "provider": self.endpoint.provider,
            "model": self.endpoint.model,
            "total_input_tokens": in_tok,
            "total_output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "turns_used": turns_used,
            "tool_calls": tool_calls,
            "elapsed_s": round(time.monotonic() - started, 3),
        }

    @staticmethod
    def _normalize_tool_result(
        name: str, raw_result: Any
    ) -> Sequence[Any]:
        """Extract ``(text, base64_images, is_finish, finish_payload)`` from a tool outcome."""
        images_b64: List[str] = []
        is_finish = False
        finish_payload: Optional[Dict[str, Any]] = None

        # RPent-style ToolResult (has .to_text(), .data, .images)
        if hasattr(raw_result, "to_text") and hasattr(raw_result, "data"):
            text = raw_result.to_text()
            data = getattr(raw_result, "data", {}) or {}
            for img in getattr(raw_result, "images", ()) or ():
                if isinstance(img, (bytes, bytearray)):
                    images_b64.append(base64.b64encode(img).decode("ascii"))
                elif isinstance(img, str):
                    images_b64.append(img)
            if name == "finish" and data.get("_finish"):
                is_finish = True
                finish_payload = dict(data)
            return text, images_b64, is_finish, finish_payload

        # Comm-Harness dict receipt {'ok': True, 'data': ...}
        if isinstance(raw_result, dict):
            text = json.dumps(raw_result, ensure_ascii=False)
            inner = raw_result.get("data") if "data" in raw_result else raw_result
            if isinstance(inner, dict):
                if inner.get("_finish") or inner.get("status") == "CLAIMED_FOUND":
                    is_finish = True
                    finish_payload = dict(inner)
            return text, images_b64, is_finish, finish_payload

        text = json.dumps({"ok": True, "result": str(raw_result)}, ensure_ascii=False)
        return text, images_b64, is_finish, finish_payload


#: Backward-compatible alias used by TeamSession (`multi-v0`).
DeepSeekPlanner = ApiAgentLoop
