"""Lightweight zero-GPU 2D GridWorld platform registered via ``PlatformSpec``."""
from platforms import PlatformSpec, PromptBundle, register
from platforms.grid_world.env_server import GridEnvClient, GridEnvServer, GridWorldState
from platforms.grid_world.tools import GridRobotTools, build_grid_toolkit

register(
    PlatformSpec(
        name="grid_world",
        toolkit_factory=build_grid_toolkit,
        prompts=PromptBundle(
            system=(
                "You are an autonomous robot collaborating with teammates over a wireless channel. "
                "Use observe_local, comm_poll, comm_broadcast, move_grid, and claim_found."
            ),
            user="Find your assigned target object and share any teammate targets you spot.",
        ),
        capabilities=(
            "observe_local",
            "move_grid",
            "claim_found",
            "comm_send",
            "comm_broadcast",
            "comm_poll",
            "comm_status",
        ),
        is_real_robot=False,
        description="Zero-dependency 2D multi-robot grid world over localhost RPC",
    ),
    replace=True,
)

__all__ = [
    "GridEnvClient",
    "GridEnvServer",
    "GridRobotTools",
    "GridWorldState",
    "build_grid_toolkit",
]
