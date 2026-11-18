"""Agent tool declarations, validation, scheduler toolkit, and explicit receipts.

Unifies RPent's ``Tool`` / ``Toolkit`` / ``@tool`` interface with Comm-Harness's
``ToolRegistry`` / ``ToolExecutor`` / ``HarnessRuntime`` / ``SideEffect`` contracts.
"""
from comm_harness.tools.base import Tool, ToolResult, iter_tools, tool, tool_error
from comm_harness.tools.contracts import EpisodeState, Phase, Receipt, SideEffect, safe_value
from comm_harness.tools.registry import ToolRegistry, ToolSpec
from comm_harness.tools.runtime import HarnessRuntime
from comm_harness.tools.toolkit import ToolCancelled, ToolError, Toolkit, validate
from comm_harness.tools.executor import ToolExecutor, ToolOutcome

__all__ = [
    "EpisodeState",
    "HarnessRuntime",
    "Phase",
    "Receipt",
    "SideEffect",
    "Tool",
    "ToolCancelled",
    "ToolError",
    "ToolExecutor",
    "ToolOutcome",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "Toolkit",
    "iter_tools",
    "safe_value",
    "tool",
    "tool_error",
    "validate",
]
