"""RACER (2023 IEEE T-RO Best Paper) ROS + RViz Multi-UAV Simulator Platform."""
from platforms import PlatformSpec, PromptBundle, register
from platforms.racer_ros.tools import RacerPlatformTools, build_racer_toolkit

register(
    PlatformSpec(
        name="racer_ros",
        toolkit_factory=build_racer_toolkit,
        prompts=PromptBundle(
            system=(
                "You coordinate a multi-UAV swarm in the RACER 3D RViz simulator over "
                "non-ideal wireless communication links."
            ),
            user="Explore the unknown 3D environment collaboratively while adapting to link quality.",
        ),
        capabilities=(
            "racer_observe_swarm",
            "racer_trigger_explore",
            "racer_draw_comm_links",
            "comm_send",
            "comm_broadcast",
            "comm_poll",
            "comm_status",
        ),
        is_real_robot=False,
        description="RACER (2023 IEEE T-RO Best Paper) ROS Noetic + RViz Multi-UAV Explorer over RPC",
    ),
    replace=True,
)

__all__ = ["RacerPlatformTools", "build_racer_toolkit"]
