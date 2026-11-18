from platforms import PlatformSpec, PromptBundle, register


def environment_factory(*args, **kwargs):
    from platforms.hm3d_m3.bridge import EnvironmentBridge
    return EnvironmentBridge(*args, **kwargs)


def node_factory(*args, **kwargs):
    from platforms.hm3d_m3.node import HarnessNode
    return HarnessNode(*args, **kwargs)


def tools_factory(*args, **kwargs):
    from platforms.hm3d_m3.tools import HabitatTools
    return HabitatTools(*args, **kwargs)


register(
    PlatformSpec(
        name="hm3d_m3",
        environment_factory=environment_factory,
        node_factory=node_factory,
        tools_factory=tools_factory,
        prompts=PromptBundle(
            system="You control ONE robot in an embodied navigation task. Execute only tool calls.",
            user="Navigate to the target goal and verify before claiming.",
        ),
        capabilities=(
            "observe_panorama",
            "query_depth",
            "project_target",
            "move",
            "turn",
            "action",
            "stop",
        ),
        is_real_robot=False,
        description="HM3D-M3 Habitat single/multi-robot embodied navigation platform",
    ),
    replace=True,
)
