"""Host-side CoNAH tools connecting over ``127.0.0.1`` RPC to the RACER ROS container."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from comm_harness.communication.loopback import CommEndpoint
from comm_harness.communication.tools import build_comm_tools
from comm_harness.rpc import RpcClient
from comm_harness.tools import SideEffect, ToolResult, Toolkit, iter_tools, tool


class RacerPlatformTools:
    """Platform tools talking to ``racer_env_server.py`` over HTTP JSON-RPC."""

    def __init__(self, env_endpoint: str = "http://127.0.0.1:9001") -> None:
        self._rpc = RpcClient(env_endpoint)

    @tool(readonly=True, side_effect=SideEffect.READ_ONLY)
    def racer_observe_swarm(self) -> ToolResult:
        """Read live 3D poses of all UAVs from the RACER ROS simulator."""
        return ToolResult(data=self._rpc.call("get_swarm_state"))

    @tool(side_effect=SideEffect.WORLD_MUTATING, step_consuming=True)
    def racer_trigger_explore(self, x: float = 0.0, y: float = 0.0, z: float = 1.0) -> ToolResult:
        """Trigger RACER swarm exploration by publishing an initial 3D navigation goal.

        Args:
            x: Initial target X coordinate in meters.
            y: Initial target Y coordinate in meters.
            z: Initial target Z coordinate in meters.
        """
        res = self._rpc.call("trigger_exploration", x=x, y=y, z=z)
        return ToolResult(data=res, consumed_steps=1)

    @tool(readonly=True, side_effect=SideEffect.BOOKKEEPING)
    def racer_draw_comm_links(
        self,
        links: List[Dict[str, Any]],
        ap_positions: Optional[Dict[str, List[float]]] = None,
    ) -> ToolResult:
        """Visualize wireless links (good/weak/dropped) and AP/RAN nodes in RViz.

        Args:
            links: List of dicts with keys src, dst, and quality ('good'|'weak'|'dropped').
            ap_positions: Optional dict mapping AP node IDs to [x, y, z] coordinates.
        """
        res = self._rpc.call("publish_comm_links_rviz", links=links, ap_positions=ap_positions)
        return ToolResult(data=res)


def build_racer_toolkit(
    *,
    env_endpoint: str = "http://127.0.0.1:9001",
    comm_endpoint: Optional[CommEndpoint] = None,
    env_state: Optional[Any] = None,
) -> Toolkit:
    tk = Toolkit(state=env_state)
    tk.add_tools(iter_tools(RacerPlatformTools(env_endpoint=env_endpoint)))
    if comm_endpoint is not None:
        tk.add_tools(build_comm_tools(comm_endpoint))
    return tk
