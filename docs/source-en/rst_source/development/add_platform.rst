Add a Platform or Channel
===========================

Adding a Robot Platform (``PlatformSpec`` & ``@tool``)
-------------------------------------------------------

Define sensor and actuator primitives using ``@tool`` and register a ``PlatformSpec`` under ``platforms/<name>/``:

.. code-block:: python

   from comm_harness.communication import CommEndpoint, build_comm_tools
   from comm_harness.tools import SideEffect, ToolResult, Toolkit, iter_tools, tool
   from platforms import PlatformSpec, PromptBundle, register


   class MyRoverTools:
       def __init__(self):
           self.pose = [0.0, 0.0]

       @tool(readonly=True, side_effect=SideEffect.READ_ONLY)
       def observe_lidar(self) -> ToolResult:
           """Read current rover pose and front obstacle clearance."""
           return ToolResult(data={"pose": list(self.pose), "front_clear_m": 3.2})

       @tool(side_effect=SideEffect.WORLD_MUTATING, step_consuming=True, physical_motion=True)
       def drive_step(self, dx: float, dy: float) -> ToolResult:
           """Drive the rover by (dx, dy) meters.

           Args:
               dx: Displacement along X in meters.
               dy: Displacement along Y in meters.
           """
           self.pose[0] += dx
           self.pose[1] += dy
           return ToolResult(data={"pose": list(self.pose)}, consumed_steps=1)


   def build_my_rover_toolkit(*, comm_endpoint: CommEndpoint = None, env_state=None) -> Toolkit:
       tk = Toolkit(state=env_state)
       tk.add_tools(iter_tools(MyRoverTools()))
       if comm_endpoint is not None:
           tk.add_tools(build_comm_tools(comm_endpoint))
       return tk


   register(
       PlatformSpec(
           name="my_rover",
           toolkit_factory=build_my_rover_toolkit,
           prompts=PromptBundle(system="You control an autonomous rover.", user="Complete the mission."),
           capabilities=("observe_lidar", "drive_step", "comm_send", "comm_broadcast", "comm_poll"),
           is_real_robot=True,
       ),
       replace=True,
   )

Adding a Custom Wireless Channel Model
---------------------------------------

Subclass ``ChannelModel`` to plug in analytical path-loss formulas or external ray-tracing RPC servers (e.g., Sionna RT):

.. code-block:: python

   from comm_harness.communication import ChannelDecision, ChannelModel, Envelope


   class WallAttenuationChannel(ChannelModel):
       name = "wall_attenuation"

       def evaluate(self, envelope: Envelope, recipient: str, *, now: float) -> ChannelDecision:
           sinr_db = 12.5
           if sinr_db < 5.0:
               return ChannelDecision(deliver=False, drop_reason="low_sinr_wall_blockage")
           return ChannelDecision(deliver=True, delay_s=0.02)
