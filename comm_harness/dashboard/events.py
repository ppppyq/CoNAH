"""Lightweight event boundary for Dashboard live updates (modeled on RPent)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional, Union

try:
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore[misc,assignment]

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class TranscriptEvent:
    """Append one frontend transcript payload."""

    payload: Dict[str, Any]


@dataclass(frozen=True)
class UsageEvent:
    """Replace the cumulative planner usage counters."""

    inp: int
    out: int
    tool_calls: int


@dataclass(frozen=True)
class RuntimeStatusEvent:
    """Update one robot-side runtime component."""

    component: str
    status: str
    error: Optional[Union[BaseException, str]] = None


@dataclass(frozen=True)
class StepRecordEvent:
    """Publish one recorded robot step and its source environment state."""

    record: Any
    env_state: Any = None


@dataclass(frozen=True)
class RunStartedEvent:
    """Mark startup complete and the agent run active."""


DashboardEvent = Union[
    TranscriptEvent,
    UsageEvent,
    RuntimeStatusEvent,
    StepRecordEvent,
    RunStartedEvent,
]


class DashboardEventSink(Protocol):
    """Consumer used by planners, toolkits, and robot runtimes."""

    @property
    def enabled(self) -> bool:
        """Whether Dashboard-only projections and artifacts are needed."""
        ...

    def emit(self, event: DashboardEvent) -> None:
        """Consume one Dashboard event."""
        ...


@dataclass(frozen=True)
class NullDashboardEventSink:
    """No-op sink used when the Dashboard is disabled."""

    @property
    def enabled(self) -> bool:
        return False

    def emit(self, event: DashboardEvent) -> None:
        return None
