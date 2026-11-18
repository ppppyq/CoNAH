"""Task specification registry and built-in embodied tasks.

Decouples **what** the agent or multi-robot team is trying to accomplish (`tasks/`)
from **which** physical or simulated carrier executes the primitives (`platforms/`)
and **how** the core Harness runtime orchestrates reasoning, memory, and communication (`comm_harness/`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional

from tasks.object_navigation import ObjectNavigationTask, TASK_TOOLS


@dataclass(frozen=True)
class TaskSpec:
    """Declarative specification for an embodied single-robot or multi-robot task."""

    name: str
    description: str = ""
    supported_platforms: FrozenSet[str] = field(default_factory=lambda: frozenset({"hm3d_m3"}))
    required_capabilities: FrozenSet[str] = field(default_factory=frozenset)
    launcher: Optional[Callable[..., Any]] = None
    default_config: Optional[str] = None
    multi_agent: bool = False


_TASKS: Dict[str, TaskSpec] = {}


def register_task(spec: TaskSpec) -> TaskSpec:
    _TASKS[spec.name] = spec
    return spec


def get_task(name: str) -> TaskSpec:
    if name not in _TASKS:
        if name == "harness_vln" or name == "harness-vln":
            import tasks.harness_vln  # noqa: F401
        elif name == "multi_target_search" or name == "multi-v0":
            import tasks.multi_target_search  # noqa: F401
        elif name in ("multi_harness_vln", "multi-harness-vln"):
            import tasks.multi_harness_vln  # noqa: F401
    key = (
        "harness_vln"
        if name == "harness-vln"
        else ("multi_target_search" if name == "multi-v0" else ("multi_harness_vln" if name == "multi-harness-vln" else name))
    )
    if key not in _TASKS:
        raise KeyError(f"Unknown task: {name!r}. Registered: {sorted(_TASKS)}")
    return _TASKS[key]


def list_tasks() -> List[str]:
    return sorted(_TASKS.keys())


__all__ = [
    "ObjectNavigationTask",
    "TASK_TOOLS",
    "TaskSpec",
    "get_task",
    "list_tasks",
    "register_task",
]
