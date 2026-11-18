"""First-class communication tools (`comm_send`, `comm_broadcast`, `comm_poll`, `comm_status`)
automatically mountable onto any robot's :class:`~comm_harness.tools.Toolkit`.
"""
from __future__ import annotations

from typing import Any, Dict, List

from comm_harness.communication.loopback import CommEndpoint
from comm_harness.tools.base import Tool, ToolResult, iter_tools, tool
from comm_harness.tools.contracts import SideEffect


class CommToolProvider:
    """Binds a per-robot :class:`CommEndpoint` as standard agent-callable tools.

    - ``comm_send`` / ``comm_broadcast`` carry ``SideEffect.NETWORK_TX`` so
      :class:`~comm_harness.tools.HarnessRuntime` automatically issues verifiable
      transmission receipts (`HARNESS_RECEIPT`).
    - ``comm_poll`` and ``comm_status`` are ``readonly=True`` so they can be called
      at any point without mutating world state.
    """

    def __init__(self, endpoint: CommEndpoint) -> None:
        self.endpoint = endpoint

    @tool(side_effect=SideEffect.NETWORK_TX)
    def comm_send(
        self,
        recipients: List[str],
        topic: str,
        payload: Dict[str, Any],
    ) -> ToolResult:
        """Send a structured message to one or more specific teammate robots.

        Args:
            recipients: List of target robot IDs (e.g. ["robot_1"]).
            topic: Short message category (e.g. "target_sighting", "goal_claim").
            payload: Structured JSON dictionary to transmit.
        """
        receipt = self.endpoint.send(recipients=recipients, topic=topic, payload=payload)
        return ToolResult(data=receipt)

    @tool(side_effect=SideEffect.NETWORK_TX)
    def comm_broadcast(self, topic: str, payload: Dict[str, Any]) -> ToolResult:
        """Broadcast a structured message to all registered teammate robots.

        Args:
            topic: Short message category (e.g. "room_cleared", "target_sighting").
            payload: Structured JSON dictionary to broadcast to teammates.
        """
        receipt = self.endpoint.broadcast(topic=topic, payload=payload)
        return ToolResult(data=receipt)

    @tool(readonly=True, side_effect=SideEffect.READ_ONLY)
    def comm_poll(self, max_messages: int = 20, topic: str = "") -> ToolResult:
        """Read newly arrived messages from this robot's inbox.

        Args:
            max_messages: Maximum number of messages to retrieve in one call.
            topic: Optional topic filter; empty string returns all arrived messages.
        """
        messages = self.endpoint.poll(max_messages=max_messages, topic=topic)
        return ToolResult(
            data={
                "node_id": self.endpoint.node_id,
                "count": len(messages),
                "messages": messages,
            }
        )

    @tool(readonly=True, side_effect=SideEffect.SPECTRUM_SENSING)
    def comm_status(self) -> ToolResult:
        """Inspect active teammate IDs, inbox queue depth, and wireless channel statistics."""
        return ToolResult(data=self.endpoint.status())


def build_comm_tools(endpoint: CommEndpoint) -> List[Tool]:
    """Return the 4 standard communication tools bound to ``endpoint``."""
    return iter_tools(CommToolProvider(endpoint))
