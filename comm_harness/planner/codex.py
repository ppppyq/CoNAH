"""Codex SDK planner backend.

Modeled after RPent's ``rpent/planner/codex.py``. Exposes the robot's
``Toolkit`` via an in-process HTTP MCP server (``HttpMcpServer``) so Codex
can reach the shared toolkit without spawning a separate tool subprocess.
Supports connecting Codex to official OpenAI endpoints or to custom
Responses-compatible gateways such as Sub2API / vLLM via ``CODEX_BASE_URL``
and ``CODEX_API_KEY``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from comm_harness import telemetry
from comm_harness.planner.base import (
    REASONING_EFFORTS,
    SERVER_NAME,
    Planner,
    PlannerResult,
    strip_mcp_prefix,
)
from comm_harness.planner.utils.http_mcp_server import HttpMcpServer

logger = logging.getLogger("codex")

PROVIDER_ID = "comm_harness_proxy"
PROVIDER_ENV_KEY = "COMM_HARNESS_CODEX_PROVIDER_KEY"
_LOOPBACK_NO_PROXY_HOSTS = ("127.0.0.1", "localhost")


def _codex_environment() -> Dict[str, str]:
    """Build the Codex child environment with direct access to its local MCP server."""
    env = dict(os.environ)
    entries: List[str] = []
    for key in ("NO_PROXY", "no_proxy"):
        entries.extend(item.strip() for item in env.get(key, "").split(","))
    deduped = list(
        dict.fromkeys(item for item in (*entries, *_LOOPBACK_NO_PROXY_HOSTS) if item)
    )
    bypass = ",".join(deduped)
    env["NO_PROXY"] = bypass
    env["no_proxy"] = bypass
    return env


def _codex_mcp_config_overrides(
    *,
    mcp_url: Optional[str],
    base_url: Optional[str],
) -> List[str]:
    config: List[Tuple[str, Any]] = []
    if mcp_url:
        config.append((f"mcp_servers.{SERVER_NAME}.url", mcp_url))
    if base_url:
        normalized = base_url.rstrip("/")
        if not normalized.endswith("/v1"):
            normalized = normalized + "/v1"
        config.extend(
            [
                ("model_provider", PROVIDER_ID),
                (f"model_providers.{PROVIDER_ID}.name", PROVIDER_ID),
                (f"model_providers.{PROVIDER_ID}.base_url", normalized),
                (f"model_providers.{PROVIDER_ID}.wire_api", "responses"),
                (f"model_providers.{PROVIDER_ID}.env_key", PROVIDER_ENV_KEY),
            ]
        )
    model_context_window = os.environ.get("CODEX_MODEL_CONTEXT_WINDOW")
    if model_context_window is not None:
        config.append(("model_context_window", int(model_context_window)))
    auto_compact_token_limit = os.environ.get("CODEX_AUTO_COMPACT_TOKEN_LIMIT")
    if auto_compact_token_limit is not None:
        config.append(("model_auto_compact_token_limit", int(auto_compact_token_limit)))
    return [f"{key}={json.dumps(value)}" for key, value in config]


def build_codex_config(
    *,
    mcp_url: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    cwd: str,
) -> Any:
    """Build the ``openai_codex.CodexConfig`` shared by the planner and connectivity check."""
    import openai_codex

    env = _codex_environment()
    if api_key:
        env[PROVIDER_ENV_KEY] = api_key
    config_overrides = _codex_mcp_config_overrides(mcp_url=mcp_url, base_url=base_url)
    config_overrides.append("project_doc_max_bytes=0")
    kwargs: Dict[str, Any] = {
        "config_overrides": tuple(config_overrides),
        "cwd": cwd,
        "env": env,
        "experimental_api": True,
    }
    codex_bin = os.environ.get("CODEX_BIN")
    if codex_bin:
        kwargs["codex_bin"] = codex_bin
    return openai_codex.CodexConfig(**kwargs)


class CodexPlanner(Planner):
    """Planner backed by the OpenAI Codex Python SDK (or custom Sub2API/vLLM Responses endpoint)."""

    def __init__(
        self,
        *,
        output_dir: str = ".",
        repo_root: Optional[str | Path] = None,
        timeout_s: int = 600,
        output_path: Optional[str | Path] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        reasoning_effort: str = "none",
        emit: Optional[Callable[..., None]] = None,
        node_id: str = "robot_0",
    ) -> None:
        if reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"unsupported reasoning effort: {reasoning_effort}")
        self._output_dir = str(output_dir)
        self._repo_root = (
            str(repo_root)
            if repo_root
            else str(Path(__file__).resolve().parents[2])
        )
        self._timeout_s = int(timeout_s)
        self._output_path = Path(output_path) if output_path else None
        self._model = model or os.environ.get("CODEX_MODEL")
        self._base_url = (
            base_url
            or os.environ.get("CODEX_BASE_URL")
            or os.environ.get("SUB2API_BASE_URL")
        )
        self._api_key = (
            api_key
            or os.environ.get("CODEX_API_KEY")
            or os.environ.get("SUB2API_API_KEY")
        )
        self._service_tier = os.environ.get("CODEX_SERVICE_TIER")
        self._reasoning_effort = reasoning_effort
        self._emit = emit or (lambda *a, **k: None)
        self._node_id = node_id

    def solve(
        self,
        *,
        system_prompt: str,
        user_message: Any,
        toolkit: Any,
        max_turns: int,
        input_queue: Optional["queue.Queue[Optional[str]]"] = None,
        dashboard_interaction: Optional[Any] = None,
    ) -> PlannerResult:
        """Run one or more Codex SDK turns against the in-process HTTP MCP server."""
        if isinstance(user_message, list):
            text_parts = [
                p.get("text", "")
                for p in user_message
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            user_text = "\n".join(t for t in text_parts if t)
        else:
            user_text = str(user_message or "")

        prompt = f"{system_prompt}\n\n{user_text}" if system_prompt else user_text
        output_path, raw_stream_path, last_message_path = self._output_paths()
        recorder = _Recorder(max_turns=max_turns, emit=self._emit, node_id=self._node_id)
        state: Dict[str, Any] = {}

        mcp_server = HttpMcpServer(toolkit)
        mcp_url = mcp_server.start()
        logger.info("mcp http endpoint: %s", mcp_url)

        started = time.time()
        worker = threading.Thread(
            target=self._run_session,
            args=(
                prompt,
                output_path,
                raw_stream_path,
                last_message_path,
                recorder,
                state,
                mcp_url,
                toolkit,
                input_queue,
            ),
            name=f"codex-sdk-{self._node_id}",
            daemon=True,
        )
        worker.start()
        error: Optional[str] = None
        try:
            worker.join(timeout=self._timeout_s)
            if worker.is_alive():
                error = f"Codex SDK timed out after {self._timeout_s}s"
                cancel_fn = getattr(toolkit, "cancel_active_and_wait", None)
                if callable(cancel_fn):
                    try:
                        cancel_fn()
                    except Exception as cancel_error:
                        logger.warning("failed to cancel toolkit work: %s", cancel_error)
                _interrupt(state)
                worker.join(timeout=15)
            elif "error" in state:
                exc = state["error"]
                error = f"{type(exc).__name__}: {exc}"
        finally:
            cancel_fn = getattr(toolkit, "cancel_active_and_wait", None)
            if callable(cancel_fn):
                try:
                    cancel_fn()
                except Exception as exc:
                    error = error or f"Toolkit cleanup failed: {exc}"
            mcp_server.stop()

        elapsed = time.time() - started
        text = state.get("text", "") or (
            output_path.read_text(errors="replace") if output_path.is_file() else ""
        )
        error = error or recorder.error
        return PlannerResult(
            finish_result=recorder.finish_result,
            messages=[{"role": "codex_sdk", "content": text}],
            stats={
                "backend": "codex_sdk",
                "elapsed_s": round(elapsed, 1),
                "output_chars": len(text),
                "output_path": str(output_path),
                "raw_stream_path": str(raw_stream_path),
                "last_message_path": str(last_message_path),
                "last_message_chars": len(recorder.final_response or ""),
                **recorder.stats(),
            },
            error=error,
        )

    def _output_paths(self) -> Tuple[Path, Path, Path]:
        if self._output_path is None:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".out", prefix="codex_sdk_task_", delete=False
            ) as file_obj:
                output_path = Path(file_obj.name)
        else:
            output_path = self._output_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
        return (
            output_path,
            output_path.with_suffix(output_path.suffix + ".stream.jsonl"),
            output_path.with_suffix(output_path.suffix + ".last"),
        )

    def _run_session(
        self,
        prompt: str,
        output_path: Path,
        raw_stream_path: Path,
        last_message_path: Path,
        recorder: "_Recorder",
        state: Dict[str, Any],
        mcp_url: str,
        toolkit: Any,
        input_queue: Optional["queue.Queue[Optional[str]]"] = None,
    ) -> None:
        try:
            import openai_codex
            from openai_codex.generated.v2_all import ReasoningEffort

            thread_options: Dict[str, Any] = {
                "approval_mode": openai_codex.ApprovalMode.deny_all,
                "cwd": self._repo_root,
                "model": self._model,
                "sandbox": openai_codex.Sandbox.full_access,
            }
            if self._service_tier:
                thread_options["service_tier"] = self._service_tier
            turn_options: Dict[str, Any] = {
                **thread_options,
                "effort": getattr(ReasoningEffort, self._reasoning_effort),
            }

            chunks: List[str] = []
            config = build_codex_config(
                mcp_url=mcp_url,
                base_url=self._base_url,
                api_key=self._api_key,
                cwd=self._repo_root,
            )
            with openai_codex.Codex(config=config) as codex:
                state["codex"] = codex
                thread = codex.thread_start(**thread_options)
                state["thread"] = thread
                with open(output_path, "w", encoding="utf-8") as out_f, open(
                    raw_stream_path, "w", encoding="utf-8"
                ) as raw_f:
                    turn = thread.turn(prompt, **turn_options)
                    state["turn"] = turn
                    limit_reached = False
                    for event in turn.stream():
                        _write_jsonl(raw_f, _message_to_json(event))
                        rendered = recorder.observe(event)
                        if rendered:
                            chunks.append(rendered)
                            out_f.write(rendered)
                            out_f.flush()
                        if (
                            str(_get(event, "method", "")) != "turn/completed"
                            and not limit_reached
                            and recorder.finish_result is None
                            and recorder.turns >= recorder.max_turns
                        ):
                            limit_reached = True
                            try:
                                turn.interrupt()
                            except Exception:
                                pass
            state["text"] = "".join(chunks)
            if recorder.final_response is not None:
                last_message_path.write_text(recorder.final_response, encoding="utf-8")
        except Exception as exc:
            state["error"] = exc


@dataclass
class _Recorder:
    """Consume Codex SDK events, emit telemetry/text, and accumulate usage stats."""

    max_turns: int
    emit: Callable[..., None] = lambda *a, **k: None
    node_id: str = "robot_0"
    turns: int = 0
    _seen_usage: Set[Tuple[int, ...]] = field(default_factory=set)
    tool_calls: int = 0
    usage: Dict[str, int] = field(
        default_factory=lambda: {
            "total_input_tokens": 0,
            "total_cached_input_tokens": 0,
            "total_output_tokens": 0,
            "total_reasoning_output_tokens": 0,
        }
    )
    final_response: Optional[str] = None
    finish_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def stats(self) -> Dict[str, int]:
        return {"turns_used": self.turns, "tool_calls": self.tool_calls, **self.usage}

    def observe(self, event: Any) -> str:
        method = str(_get(event, "method", ""))
        payload = _get(event, "payload")
        if method in {"thread/started", "turn/started"}:
            return f"[codex-system] {method}\n"
        if method == "item/completed":
            return self._render_item(_get(payload, "item"))
        if method == "thread/tokenUsage/updated":
            if self._set_usage(_get(payload, "token_usage")):
                return f"\n[agent] === turn {self.turns}/{self.max_turns} ===\n"
            return ""
        if method == "turn/completed":
            return self._render_turn_completed(_get(payload, "turn"))
        if method in {"error", "fatal"}:
            detail = _short_json(_jsonable(payload), limit=500)
            if not bool(_get(payload, "will_retry", False)):
                self.error = detail
            return f"[codex-error] {detail}\n"
        return ""

    def _render_item(self, item: Any) -> str:
        item = _unwrap(item)
        item_type = str(_get(item, "type", ""))
        if item_type == "agentMessage":
            text = str(_get(item, "text", "")).strip()
            if not text:
                return ""
            self.final_response = text
            return f"\n[codex] {text}\n"
        if _is_tool_item(item):
            self.tool_calls += 1
            if item_type in {"mcpToolCall", "dynamicToolCall"}:
                name = strip_mcp_prefix(str(_get(item, "tool", item_type)))
                self._maybe_capture_finish(name, item)
            elif item_type == "commandExecution":
                name = str(_get(item, "command", item_type))
            else:
                name = "fileChange"
            return f"[tool<-] {name}\n"
        return ""

    def _render_turn_completed(self, turn: Any) -> str:
        status = str(_get(_get(turn, "status"), "value", _get(turn, "status", "")))
        err = _get(turn, "error")
        if err:
            self.error = str(_get(err, "message", str(err)))
        return f"[codex-result] {status}\n"

    def _set_usage(self, usage: Any) -> bool:
        if usage is None:
            return False
        total = _get(usage, "total", usage)
        updated = {
            "total_input_tokens": _int_attr(total, "input_tokens"),
            "total_cached_input_tokens": _int_attr(total, "cached_input_tokens"),
            "total_output_tokens": _int_attr(total, "output_tokens"),
            "total_reasoning_output_tokens": _int_attr(total, "reasoning_output_tokens"),
        }
        key = tuple(updated.values())
        if not any(key) or key in self._seen_usage:
            return False
        self._seen_usage.add(key)
        self.usage = updated
        self.turns += 1
        telemetry.emit(
            "MODEL_RESPONSE",
            node_id=self.node_id,
            purpose="codex_turn",
            empty=False,
            usage={
                "input_tokens": updated["total_input_tokens"],
                "output_tokens": updated["total_output_tokens"],
                "total_tokens": updated["total_input_tokens"] + updated["total_output_tokens"],
            },
        )
        return True

    def _maybe_capture_finish(self, name: str, item: Any) -> None:
        if self.finish_result is not None or name.lower() not in ("finish", "declare_goal"):
            return
        status = _status(item)
        if status and status != "completed":
            return
        if _get(item, "error") not in (None, ""):
            return
        data = _jsonable(item)
        if not isinstance(data, dict):
            return
        args = _json_object(data.get("arguments"))
        if isinstance(args, dict):
            self.finish_result = {"_finish": True, **args}


def _interrupt(state: Dict[str, Any]) -> None:
    turn = state.get("turn")
    if turn is not None:
        try:
            turn.interrupt()
        except Exception:
            pass
    codex = state.get("codex")
    if codex is not None:
        try:
            codex.close()
        except Exception:
            pass


def _write_jsonl(file_obj: Any, value: Dict[str, Any]) -> None:
    file_obj.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
    file_obj.flush()


def _message_to_json(message: Any) -> Dict[str, Any]:
    return {
        "method": _get(message, "method", ""),
        "payload": _jsonable(_get(message, "payload")),
    }


def _jsonable(value: Any) -> Any:
    value = _unwrap(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return {"type": "bytes", "size": len(value)}
    return value


def _unwrap(value: Any) -> Any:
    return getattr(value, "root", value)


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _is_tool_item(item: Any) -> bool:
    return str(_get(_unwrap(item), "type", "")) in {
        "mcpToolCall",
        "dynamicToolCall",
        "commandExecution",
        "fileChange",
    }


def _status(item: Any) -> str:
    value = _get(item, "status", "")
    return str(getattr(value, "value", value))


def _json_object(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _short_json(value: Any, *, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...(+{len(text) - limit})"


def _int_attr(value: Any, key: str) -> int:
    return int(_get(value, key, 0) or 0)
