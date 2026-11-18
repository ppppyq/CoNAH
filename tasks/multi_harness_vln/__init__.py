"""Multi-robot collaborative HarnessVLN task package (Mode B: Multi-Goal + Shared Memory)."""

from tasks import TaskSpec, register_task
from tasks.multi_harness_vln.launcher import launch_multi_harness_vln

SPEC = register_task(
    TaskSpec(
        name="multi_harness_vln",
        description="Multi-robot collaborative HarnessVLN where each robot runs an independent HarnessVLN instance and shares WorldGraph places, target sightings, and goal assignments over IdealTransport.",
        supported_platforms=frozenset({"hm3d_m3"}),
        launcher=launch_multi_harness_vln,
        default_config="tasks/multi_harness_vln/configs/multi_harness_vln_2robots.json",
        multi_agent=True,
    )
)

__all__ = ["SPEC", "launch_multi_harness_vln"]
