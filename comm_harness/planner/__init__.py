"""Planner — high-level reasoning/planning backends for the robot agent loop.

Follows RPent's ``rpent/planner/`` structure:
- ``base.py``: ``Planner(ABC)``, ``PlannerResult``, ``Budget``, ``build_planner()``, endpoint resolution
- ``api_loop.py``: ``ApiAgentLoop`` (unified ChatGPT/OpenAI-compatible loop for Sub2API, DeepSeek, OpenAI, and Ollama)
- ``codex.py``: ``CodexPlanner`` (Codex SDK backend bridged via in-process HTTP MCP server)
- ``flash.py``: ``FlashPlanner`` (deterministic/scripted replay backend without LLM calls)
- ``check.py``: ``check_llm()`` connectivity probe
- ``utils/http_mcp_server.py``: ``HttpMcpServer`` exposing ``Toolkit`` over MCP
"""

from __future__ import annotations

from comm_harness.planner.api_loop import ApiAgentLoop, DeepSeekPlanner
from comm_harness.planner.base import (
    API_PROVIDERS,
    MCP_TOOL_PREFIX,
    REASONING_EFFORTS,
    Budget,
    ModelBudgetExceeded,
    ModelError,
    Planner,
    PlannerResult,
    ResolvedEndpoint,
    add_mcp_prefix,
    build_planner,
    cancel_and_wait,
    execute_tool,
    invoke_toolkit_sync,
    is_recoverable_agent_error,
    parse_model_spec,
    resolve_api_endpoint,
    resolve_model,
    strip_mcp_prefix,
)
from comm_harness.planner.check import (
    CHECK_PLANNERS,
    CHECK_STATUSES,
    LlmCheckRequest,
    LlmCheckResult,
    check_llm,
    redact_secrets,
)
from comm_harness.planner.codex import CodexPlanner
from comm_harness.planner.flash import FlashPlanner, ScriptedPlanner
from comm_harness.planner.utils.http_mcp_server import HttpMcpServer

__all__ = [
    "API_PROVIDERS",
    "ApiAgentLoop",
    "Budget",
    "CHECK_PLANNERS",
    "CHECK_STATUSES",
    "CodexPlanner",
    "DeepSeekPlanner",
    "FlashPlanner",
    "HttpMcpServer",
    "LlmCheckRequest",
    "LlmCheckResult",
    "MCP_TOOL_PREFIX",
    "ModelBudgetExceeded",
    "ModelError",
    "Planner",
    "PlannerResult",
    "REASONING_EFFORTS",
    "ResolvedEndpoint",
    "ScriptedPlanner",
    "add_mcp_prefix",
    "build_planner",
    "cancel_and_wait",
    "check_llm",
    "execute_tool",
    "invoke_toolkit_sync",
    "is_recoverable_agent_error",
    "parse_model_spec",
    "redact_secrets",
    "resolve_api_endpoint",
    "resolve_model",
    "strip_mcp_prefix",
]
