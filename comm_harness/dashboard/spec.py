"""Lightweight types for robot-owned Dashboard configuration (modeled on RPent)."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

try:
    from typing import Literal, TypedDict
except ImportError:  # pragma: no cover
    from typing_extensions import Literal, TypedDict  # type: ignore


class TaskFieldSpecRequired(TypedDict):
    name: str


class TaskFieldSpec(TaskFieldSpecRequired, total=False):
    kind: Literal["integer"]
    minimum: int
    choices: Tuple[str, ...]
    suggestions: Tuple[str, ...]


class TaskSpec(TypedDict):
    command: str
    usage: str
    fields: Tuple[TaskFieldSpec, ...]
    display: str
    output_slug: str


class RuntimeComponentSpecRequired(TypedDict):
    name: str
    label: str
    scope: Literal["shared", "unique"]


class RuntimeComponentSpec(RuntimeComponentSpecRequired, total=False):
    planners: Tuple[str, ...]


class DashboardSpecRequired(TypedDict):
    task: TaskSpec
    runtime_components: Tuple[RuntimeComponentSpec, ...]
    primitives: Tuple[str, ...]


class DashboardSpec(DashboardSpecRequired, total=False):
    external_env: bool
    operator_commands: Dict[str, Optional[str]]
