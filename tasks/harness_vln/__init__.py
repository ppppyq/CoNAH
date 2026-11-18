"""HarnessVLN single-agent visual object navigation task (`tasks/harness_vln`)."""

from tasks import TaskSpec, register_task
from tasks.harness_vln.launcher import launch_single
from tasks.object_navigation import ObjectNavigationTask

SPEC = register_task(
    TaskSpec(
        name="harness_vln",
        description="Single-robot zero-shot visual object navigation with WorldGraph & TodoMemory.",
        supported_platforms=frozenset({"hm3d_m3"}),
        required_capabilities=ObjectNavigationTask.required_capabilities,
        launcher=launch_single,
        default_config="tasks/harness_vln/configs/harness_vln_single.json",
        multi_agent=False,
    )
)

__all__ = ["ObjectNavigationTask", "SPEC", "launch_single"]
