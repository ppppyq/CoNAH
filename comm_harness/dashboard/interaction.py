"""Shared types and the planner-facing Dashboard interaction port (modeled on RPent)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    from typing import Literal, Protocol
except ImportError:  # pragma: no cover
    from typing_extensions import Literal, Protocol  # type: ignore

PlannerActivity = Literal["starting", "idle", "busy", "ended"]
DashboardMessageStatus = Literal[
    "pending",
    "sending",
    "sent",
    "withdrawn",
    "failed",
    "unsent",
]


@dataclass
class DashboardMessage:
    """One user message submitted through a Dashboard Session."""

    message_id: str
    text: str
    status: DashboardMessageStatus
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        """Return the JSON-safe representation exposed to the frontend."""
        return {
            "message_id": self.message_id,
            "text": self.text,
            "status": self.status,
            "error": self.error,
        }


class PlannerSessionDriver(Protocol):
    """Backend operations used by the Dashboard planner control."""

    async def submit(self, message: DashboardMessage) -> int:
        ...

    async def interrupt(self) -> int:
        ...


class DashboardInteractionPort(Protocol):
    """Planner-facing access to one Dashboard interaction Session."""

    @property
    def planner_activity(self) -> PlannerActivity:
        ...

    @property
    def interaction_version(self) -> int:
        ...

    def wait_for_interaction_change(
        self,
        since: int,
        timeout: Optional[float] = None,
    ) -> int:
        ...

    def claim_next_pending_message(self) -> Optional[DashboardMessage]:
        ...

    def mark_message_sent(self, message_id: str) -> DashboardMessage:
        ...

    def mark_message_failed(
        self,
        message_id: str,
        error: str,
    ) -> DashboardMessage:
        ...

    def mark_message_unsent(self, message_id: str) -> DashboardMessage:
        ...

    def claim_interrupt_request(self) -> bool:
        ...

    def complete_interrupt(self, error: Optional[str] = None) -> None:
        ...

    def set_planner_activity(
        self,
        activity: PlannerActivity,
        *,
        accepting_input: Optional[bool] = None,
    ) -> None:
        ...

    def seal_interaction(self) -> None:
        ...

    @property
    def task_replacement_requested(self) -> bool:
        ...

    def complete_task_replacement(self, error: Optional[str] = None) -> None:
        ...


class DashboardInteractionError(RuntimeError):
    """Base class for invalid Dashboard interaction operations."""


class InteractionUnavailableError(DashboardInteractionError):
    """The Dashboard Session is not currently accepting interaction requests."""


class UnknownDashboardMessageError(DashboardInteractionError):
    """No message with the requested ID exists in this Dashboard Session."""


class DashboardMessageConflictError(DashboardInteractionError):
    """The message cannot transition from its current status."""
