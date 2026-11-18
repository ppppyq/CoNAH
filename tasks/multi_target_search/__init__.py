"""Multi-robot / single-robot (K>=1) communication-native target search task."""

from tasks import TaskSpec, register_task

SPEC = register_task(
    TaskSpec(
        name="multi_target_search",
        description="Communication-native multi-robot (or K=1 single-robot) collaborative target search.",
        supported_platforms=frozenset({"hm3d_m3", "ackermann", "uav"}),
        default_config="tasks/multi_target_search/configs/hm3d_m3_smoke.json",
        multi_agent=True,
    )
)

__all__ = ["SPEC"]
