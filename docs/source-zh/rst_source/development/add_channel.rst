自定义信道与通信协议
=====================

CoNAH 允许通信与网络研究者在不修改任何机器人控制代码的前提下，快速插拔自定义信道模型与高层协同协议。

1. 自定义无线信道模型（继承 ``ChannelModel`` ）
------------------------------------------------

以下示例展示如何编写一个基于距离对数衰减与墙体遮挡的自定义信道模型，并直接挂入 ``LocalhostCommServer`` 对外提供多进程服务：

.. code-block:: python

   from comm_harness.communication import (
       ChannelDecision,
       ChannelModel,
       Envelope,
       InMemoryCommBus,
       LocalhostCommServer,
   )


   class WallAttenuationChannel(ChannelModel):
       name = "wall_attenuation"

       def __init__(self, min_sinr_db: float = 5.0):
           self.min_sinr_db = min_sinr_db
           self.position_resolver = None

       def evaluate(self, envelope: Envelope, recipient: str, *, now: float) -> ChannelDecision:
           # 可在此调用 3D 场景射线碰撞或外部 Sionna RT RPC 计算实时 SINR
           sinr_db = 12.5
           if sinr_db < self.min_sinr_db:
               return ChannelDecision(deliver=False, drop_reason="low_sinr_wall_blockage")
           return ChannelDecision(deliver=True, delay_s=0.02)


   bus = InMemoryCommBus(episode_id="custom_rf", channel=WallAttenuationChannel())
   server = LocalhostCommServer(bus, host="127.0.0.1", port=9100)
   server.start_background()

2. 扩展自定义通信工具（例如链路探测 ``comm_ping`` ）
-----------------------------------------------------

只需按照 ``CommToolProvider`` 的写法，新增带 ``@tool`` 装饰器的方法并挂载进 ``Toolkit`` ，大模型即可在决策循环中自主调用新工具（如在发送高字节语义地图前先探测链路质量）。
