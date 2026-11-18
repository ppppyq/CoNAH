"""Agent-facing tools for the 2D GridWorld platform (`observe_local`, `move_grid`, `claim_found`)."""
from __future__ import annotations

from typing import Optional

from comm_harness.communication.loopback import CommEndpoint, LocalhostCommClient
from comm_harness.communication.tools import build_comm_tools
from comm_harness.session.base import EnvState
from comm_harness.tools import SideEffect, ToolResult, Toolkit, iter_tools, tool
from platforms.grid_world.env_server import GridEnvClient


class GridRobotTools:
    """Platform tools bound to one robot over RPC (`GridEnvClient`)."""

    def __init__(
        self,
        env_client: GridEnvClient,
        *,
        env_state: Optional[EnvState] = None,
        comm_endpoint: Optional[CommEndpoint] = None,
    ) -> None:
        self.env_client = env_client
        self.env_state = env_state
        self.comm_endpoint = comm_endpoint

    @tool(readonly=True, side_effect=SideEffect.READ_ONLY)
    def observe_local(self) -> ToolResult:
        """Observe the robot's current grid coordinates and any objects within 1 cell."""
        obs = self.env_client.observe_local()
        if isinstance(self.comm_endpoint, LocalhostCommClient):
            self.comm_endpoint.update_position(obs["pose"])
        return ToolResult(data=obs)

    @tool(side_effect=SideEffect.WORLD_MUTATING, step_consuming=True, physical_motion=True)
    def move_grid(self, direction: str) -> ToolResult:
        """Step one grid cell in a cardinal direction.

        Args:
            direction: Movement direction ("north", "south", "east", "west", or "stay").
        """
        res = self.env_client.move_grid(direction)
        if isinstance(self.comm_endpoint, LocalhostCommClient):
            self.comm_endpoint.update_position(res["pose"])
        if self.env_state is not None:
            with self.env_state.record_step(
                state={"pose": res["pose"]},
                command={"tool": "move_grid", "direction": direction},
                result=res,
            ):
                pass
        return ToolResult(data=res, consumed_steps=1)

    @tool(side_effect=SideEffect.TERMINAL, step_consuming=True)
    def claim_found(self, category: str) -> ToolResult:
        """Claim that the target object category has been reached (within 1 cell).

        Args:
            category: Name of the target object category (e.g. "vase", "mirror").
        """
        res = self.env_client.claim_found(category)
        return ToolResult(data={**res, "_finish": True, "status": "success"}, consumed_steps=1)


def build_grid_toolkit(
    *,
    env_endpoint: str,
    node_id: str,
    comm_endpoint: Optional[CommEndpoint] = None,
    env_state: Optional[EnvState] = None,
) -> Toolkit:
    """Create a complete per-robot ``Toolkit`` combining GridWorld + Communication tools."""
    env_client = GridEnvClient(env_endpoint, node_id=node_id)
    provider = GridRobotTools(env_client, env_state=env_state, comm_endpoint=comm_endpoint)
    tk = Toolkit(state=env_state)
    tk.add_tools(iter_tools(provider))
    if comm_endpoint is not None:
        tk.add_tools(build_comm_tools(comm_endpoint))
    return tk
