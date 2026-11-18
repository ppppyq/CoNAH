接入新机器人或信道模型
========================

在 CoNAH 中扩展一台新机器人平台或一种新的无线信道损伤模型，均只需几十行标准 Python 代码。

1. 用 ``@tool`` 与 ``PlatformSpec`` 接入新机器人平台
------------------------------------------------------

遵循 **“平台只暴露物理感知与控制原语，不做任务成功判定”** 的原则，新建 ``platforms/my_rover/`` ：

.. code-block:: python

   from comm_harness.communication import CommEndpoint, build_comm_tools
   from comm_harness.tools import SideEffect, ToolResult, Toolkit, iter_tools, tool
   from platforms import PlatformSpec, PromptBundle, register


   class MyRoverTools:
       def __init__(self, env_state=None):
           self.pose = [0.0, 0.0]

       @tool(readonly=True, side_effect=SideEffect.READ_ONLY)
       def observe_lidar(self) -> ToolResult:
           """读取当前位姿与激光雷达障碍物距离。"""
           return ToolResult(data={"pose": list(self.pose), "front_clear_m": 3.2})

       @tool(side_effect=SideEffect.WORLD_MUTATING, step_consuming=True, physical_motion=True)
       def drive_step(self, dx: float, dy: float) -> ToolResult:
           """控制底盘位移 (dx, dy) 米。

           Args:
               dx: X 方向位移（米）。
               dy: Y 方向位移（米）。
           """
           self.pose[0] += dx
           self.pose[1] += dy
           return ToolResult(data={"pose": list(self.pose)}, consumed_steps=1)


   def build_my_rover_toolkit(*, comm_endpoint: CommEndpoint = None, env_state=None) -> Toolkit:
       tk = Toolkit(state=env_state)
       tk.add_tools(iter_tools(MyRoverTools(env_state)))
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

框架会自动从函数签名和 Docstring 生成 OpenAI/MCP 兼容的 JSON Schema，并自动为 ``drive_step`` 和 ``comm_send`` 签发 ``HarnessRuntime`` 回执。

2. 自定义无线信道模型（继承 ``ChannelModel`` ）
------------------------------------------------

若需接入对数距离路径损耗（Log-Distance Path Loss）、穿墙次数衰减或外部 **Sionna RT** 射线追踪服务，只需继承 ``ChannelModel`` 并实现 ``evaluate`` 方法：

.. code-block:: python

   from comm_harness.communication import ChannelDecision, ChannelModel, Envelope


   class WallAttenuationChannel(ChannelModel):
       name = "wall_attenuation"

       def evaluate(self, envelope: Envelope, recipient: str, *, now: float) -> ChannelDecision:
           # 可在此调用 3D 场景射线碰撞或外部 Sionna RPC 计算 SINR
           sinr_db = 12.5
           if sinr_db < 5.0:
               return ChannelDecision(deliver=False, drop_reason="low_sinr_wall_blockage")
           return ChannelDecision(deliver=True, delay_s=0.02)
