"""Dashboard package — RPent-style Live Agent Command & Status Monitor."""

from __future__ import annotations

from comm_harness.dashboard.events import (
    DashboardEvent,
    DashboardEventSink,
    NullDashboardEventSink,
    RunStartedEvent,
    RuntimeStatusEvent,
    StepRecordEvent,
    TranscriptEvent,
    UsageEvent,
)
from comm_harness.dashboard.server import DashboardServer, create_app, serve
from comm_harness.dashboard.session import DashboardSessionController
from comm_harness.dashboard.state import DashboardState, redact

__all__ = [
    "DashboardEvent",
    "DashboardEventSink",
    "DashboardServer",
    "DashboardSessionController",
    "DashboardState",
    "NullDashboardEventSink",
    "RunStartedEvent",
    "RuntimeStatusEvent",
    "StepRecordEvent",
    "TranscriptEvent",
    "UsageEvent",
    "create_app",
    "redact",
    "serve",
]
