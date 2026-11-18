"""Shared abstract base class and factory for high-level reasoning backends.

Modeled after RPent's ``rpent/planner/base.py``, adapted to support per-robot
independent harnesses, Python 3.8+ worker compatibility, and unified provider
resolution across ``api`` (OpenAI / Sub2API / DeepSeek / Ollama), ``codex``,
and ``flash`` (deterministic/scripted replay).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import os
import queue
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple


#: MCP namespace prefix for Comm-Harness tools (``mcp__<server>__<tool>``).
#: Toolkits expose plain tool names; MCP-based planners add/strip this prefix.
SERVER_NAME = "comm_harness"
MCP_TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")

#: Supported provider prefixes for ``--planner api --model <provider>:<model>``.
API_PROVIDERS = ("openai", "openai-chat", "sub2api", "deepseek", "ollama", "anthropic")

DEFAULT_BASE_URLS: Dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openai-chat": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "ollama": "http://127.0.0.1:11434",
}

API_KEY_ENVS: Dict[str, Tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY", "MODEL_API_KEY", "LLM_API_KEY"),
    "openai-chat": ("OPENAI_API_KEY", "MODEL_API_KEY", "LLM_API_KEY"),
    "sub2api": ("SUB2API_API_KEY", "OPENAI_API_KEY", "MODEL_API_KEY", "LLM_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY", "MODEL_API_KEY", "LLM_API_KEY"),
    "ollama": (),
    "anthropic": ("ANTHROPIC_API_KEY", "SUB2API_API_KEY", "MODEL_API_KEY"),
}

BASE_URL_ENVS: Dict[str, Tuple[str, ...]] = {
    "openai": ("OPENAI_BASE_URL", "MODEL_BASE_URL"),
    "openai-chat": ("OPENAI_BASE_URL", "MODEL_BASE_URL"),
    "sub2api": ("SUB2API_BASE_URL", "OPENAI_BASE_URL", "MODEL_BASE_URL"),
    "deepseek": ("DEEPSEEK_BASE_URL", "MODEL_BASE_URL"),
    "ollama": ("OLLAMA_BASE_URL", "MODEL_BASE_URL"),
    "anthropic": ("ANTHROPIC_BASE_URL", "SUB2API_BASE_URL", "MODEL_BASE_URL"),
}


class ModelError(RuntimeError):
    """Raised when a model request or tool-calling round fails unrecoverably."""


class ModelBudgetExceeded(BaseException):
    """Cancel a run when call/token budget is exhausted instead of degrading to fallback."""


class Budget:
    """Thread-safe call and token budget shared by one or more robot harnesses."""

    def __init__(self, max_calls: int = 40, max_tokens: int = 100000) -> None:
        self.max_calls = int(max_calls)
        self.max_tokens = int(max_tokens)
        self.calls = 0
        self.tokens = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.inflight = 0
        self.lock = threading.Lock()

    def reserve(self) -> None:
        with self.lock:
            if self.calls >= self.max_calls or self.tokens >= self.max_tokens:
                raise ModelError("model_budget_exhausted")
            self.calls += 1
            self.inflight += 1

    def finish(self, tokens: int = 0, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        with self.lock:
            total = int(tokens) if tokens else int(input_tokens) + int(output_tokens)
            self.tokens += total
            self.input_tokens += int(input_tokens)
            self.output_tokens += int(output_tokens)
            self.inflight = max(0, self.inflight - 1)

    def summary(self) -> Dict[str, Any]:
        return {
            "requests_attempted": self.calls,
            "total_tokens": self.tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "max_calls": self.max_calls,
            "max_tokens": self.max_tokens,
            "token_limit_semantics": "checked before requests; in-flight calls may overshoot",
        }


def add_mcp_prefix(name: str) -> str:
    """Return the namespaced MCP tool name for a bare tool name."""
    if name.startswith(MCP_TOOL_PREFIX):
        return name
    return f"{MCP_TOOL_PREFIX}{name}"


def strip_mcp_prefix(name: str) -> str:
    """Return the bare tool name, dropping the MCP namespace if present."""
    if name.startswith(MCP_TOOL_PREFIX):
        return name[len(MCP_TOOL_PREFIX) :]
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            return parts[2]
    return name


def resolve_model(planner: str, model: Optional[str]) -> Optional[str]:
    """Resolve the model using the planner's explicit/environment precedence."""
    if model:
        return model
    if planner == "codex":
        return os.environ.get("CODEX_MODEL")
    return os.environ.get("MODEL_NAME") or os.environ.get("OPENAI_MODEL")


@dataclass(frozen=True)
class ResolvedEndpoint:
    """Normalized provider, model name, base URL, and API key for an API planner."""

    provider: str
    model: str
    base_url: str
    api_key: str
    credential_env: Optional[str] = None
    base_url_env: Optional[str] = None


def parse_model_spec(
    model: Optional[str],
    default_provider: Optional[str] = None,
) -> Tuple[str, str]:
    """Split ``[provider:]model`` into ``(provider, bare_model)``.

    Note: Ollama models often contain a colon for parameter tags (e.g.
    ``qwen3.8:27b`` or ``ollama:qwen3.8:27b``). A prefix before ``:`` is
    treated as a provider name only when it matches :data:`API_PROVIDERS`.
    """
    raw = (model or "").strip()
    if not raw:
        raise ValueError(
            "a model identifier is required (e.g. 'deepseek:deepseek-flash', "
            "'sub2api:gpt-5.4', 'ollama:qwen3.8:27b')"
        )
    if ":" in raw:
        candidate, rest = raw.split(":", 1)
        low_cand = candidate.lower()
        if low_cand in API_PROVIDERS:
            if not rest.strip():
                raise ValueError(f"missing model name after provider prefix in {raw!r}")
            return low_cand, rest.strip()

    # Unprefixed model name: infer from explicit default_provider, env, or model name.
    provider = (default_provider or os.environ.get("MODEL_PROVIDER") or "").strip().lower()
    if provider in API_PROVIDERS:
        return provider, raw
    low_raw = raw.lower()
    if low_raw.startswith("deepseek"):
        return "deepseek", raw
    if ":" in raw and not low_raw.startswith(("gpt-", "claude-", "o1-", "o3-", "gemini-")):
        # Tags like qwen3.8:27b default to ollama unless another provider was specified.
        return "ollama", raw
    return "openai-chat", raw


def resolve_api_endpoint(
    model: Optional[str],
    *,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    environ: Optional[Dict[str, str]] = None,
) -> ResolvedEndpoint:
    """Resolve provider, bare model, base_url, and api_key for ``api`` planner."""
    env = os.environ if environ is None else environ
    resolved_provider, bare_model = parse_model_spec(model, default_provider=provider)

    # Resolve base_url
    url_env_used: Optional[str] = None
    resolved_url = (base_url or "").strip()
    if not resolved_url:
        for env_name in BASE_URL_ENVS.get(resolved_provider, ()):
            val = env.get(env_name, "").strip()
            if val:
                resolved_url = val
                url_env_used = env_name
                break
    if not resolved_url:
        resolved_url = DEFAULT_BASE_URLS.get(resolved_provider, "")
    if not resolved_url:
        raise ValueError(
            f"provider {resolved_provider!r} requires --base-url or one of "
            f"{BASE_URL_ENVS.get(resolved_provider, ())}"
        )

    # Resolve api_key
    key_env_used: Optional[str] = None
    resolved_key = (api_key or "").strip()
    if resolved_provider == "ollama":
        resolved_key = "ollama"
    elif not resolved_key:
        for env_name in API_KEY_ENVS.get(resolved_provider, ()):
            val = env.get(env_name, "").strip()
            if val:
                resolved_key = val
                key_env_used = env_name
                break
        if not key_env_used and API_KEY_ENVS.get(resolved_provider):
            key_env_used = API_KEY_ENVS[resolved_provider][0]

    return ResolvedEndpoint(
        provider=resolved_provider,
        model=bare_model,
        base_url=resolved_url,
        api_key=resolved_key,
        credential_env=key_env_used,
        base_url_env=url_env_used,
    )


async def _wait_for_cleanup(worker: "asyncio.Future[Any]") -> None:
    """Finish cleanup asynchronously even if the caller is cancelled again."""
    cancelled = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled = True
    worker.result()
    if cancelled:
        raise asyncio.CancelledError


async def cancel_and_wait(cancel: Callable[[], None]) -> None:
    """Drain tools without competing for their potentially saturated worker pool."""
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="tool-control") as pool:
        worker = loop.run_in_executor(pool, cancel)
        await _wait_for_cleanup(worker)


def invoke_toolkit_sync(toolkit: Any, name: str, arguments: Dict[str, Any]) -> Any:
    """Dispatch one tool call synchronously across RPent or Comm-Harness toolkits."""
    if hasattr(toolkit, "execute_tool"):
        return toolkit.execute_tool(name, arguments)
    if hasattr(toolkit, "execute"):
        return toolkit.execute(name, arguments)
    raise TypeError(f"Unsupported toolkit type {type(toolkit).__name__}: missing execute_tool/execute")


async def execute_tool(toolkit: Any, name: str, arguments: Dict[str, Any]) -> Any:
    """Run one tool call in a worker thread, preserving cancellation cleanup."""
    loop = asyncio.get_running_loop()
    worker = loop.run_in_executor(None, invoke_toolkit_sync, toolkit, name, arguments)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancel_fn = getattr(toolkit, "cancel_active_and_wait", None)
        try:
            if callable(cancel_fn):
                await cancel_and_wait(cancel_fn)
        finally:
            await _wait_for_cleanup(worker)
        raise


@dataclass
class PlannerResult:
    """Result returned by a planner invocation (aligned with RPent's ``PlannerResult``)."""

    finish_result: Optional[Dict[str, Any]] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finish_result": self.finish_result,
            "messages": self.messages,
            "stats": self.stats,
            "error": self.error,
        }


def is_recoverable_agent_error(agent_error: Optional[str]) -> bool:
    """Whether the error allows a fresh exploration session."""
    lowered = (agent_error or "").lower()
    return "timed out" in lowered or "planner response truncated" in lowered


class Planner(ABC):
    """A planner selects or supplies the actions used by a robot harness to solve a task.

    Given a system prompt, an initial user message (str or multimodal content list),
    and the robot's bound ``toolkit``, it runs the multi-turn agent loop and returns
    a :class:`PlannerResult` after the task finishes or the turn budget is exhausted.
    """

    @abstractmethod
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
        """Run the multi-turn agent loop until completion or turn budget."""
        raise NotImplementedError


def build_planner(
    planner_type: str = "api",
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    output_dir: Optional[ str | Path ] = None,
    recipe_tag: str = "default",
    robot_name: str = "hm3d_m3",
    max_tokens: int = 4096,
    planner_timeout_s: Optional[float] = None,
    reasoning_effort: str = "none",
    no_images: bool = False,
    budget: Optional[Budget] = None,
    emit: Optional[Callable[..., None]] = None,
    node_id: str = "robot_0",
) -> Planner:
    """Build a planner for the given backend (`api`, `codex`, or `flash`/`scripted`)."""
    kind = (planner_type or "api").strip().lower()
    if kind in ("deepseek", "sub2api", "ollama", "openai", "openai-chat"):
        # Allow shorthand where planner_type directly names an API provider
        provider = provider or kind
        kind = "api"

    if kind == "api":
        from comm_harness.planner.api_loop import ApiAgentLoop

        endpoint = resolve_api_endpoint(
            model, provider=provider, base_url=base_url, api_key=api_key
        )
        timeout_s = (
            float(planner_timeout_s)
            if planner_timeout_s is not None
            else float(os.environ.get("CELL_TIMEOUT_S", "120"))
        )
        return ApiAgentLoop(
            endpoint=endpoint,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            no_images=no_images,
            timeout_s=timeout_s,
            budget=budget,
            emit=emit,
            node_id=node_id,
        )

    if kind == "codex":
        from comm_harness.planner.codex import CodexPlanner

        cx_timeout_s = (
            int(planner_timeout_s)
            if planner_timeout_s is not None
            else int(os.environ.get("CODEX_TIMEOUT_S", os.environ.get("CELL_TIMEOUT_S", "600")))
        )
        return CodexPlanner(
            output_dir=str(output_dir or "."),
            model=resolve_model("codex", model),
            base_url=base_url,
            api_key=api_key,
            timeout_s=cx_timeout_s,
            reasoning_effort=reasoning_effort,
            emit=emit,
            node_id=node_id,
        )

    if kind in ("flash", "scripted"):
        from comm_harness.planner.flash import FlashPlanner

        return FlashPlanner(
            recipe_tag=recipe_tag,
            robot_name=robot_name,
        )

    raise ValueError(f"unknown planner_type: {planner_type!r} (expected 'api', 'codex', or 'flash')")
