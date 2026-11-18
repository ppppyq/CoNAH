CoNAH 简介
=============

为什么需要“通信原生”的多机智能体框架？
-----------------------------------------

在真实物理世界中，单台机器人的感知视野、行动速度与续航边界有限，长程室内搜索、灾后救援与大范围巡检天然需要多台异构机器人协同完成。然而现有具身智能框架大多存在两类局限：

1. **单机中心化假设**：仅围绕 ``1 Planner ↔ 1 Toolkit ↔ 1 Robot`` 设计，缺少跨机记忆增量同步、空间防冲突与动态任务接力原语。
2. **伪分布式内存共享**：许多多智能体评测脚本在单个 Python 进程内直接读写同一个全局字典，忽略了真实无线电传输中的序列化字节开销、带宽瓶颈、穿墙遮挡与随机丢包，导致算法无法迁移至真实多机硬件。

**CoNAH (Communication-Native Agentic Harness)** 围绕 **服务化、标准化、通信一等公民** 三项原则构建，从架构底层解决上述问题。

.. image:: /_images/arch_zh.png
   :alt: CoNAH 去中心化多机协同架构
   :width: 100%

四大核心设计原则
------------------

1. **每台机器人拥有完全独立的 Harness 闭环**
   每台机器人运行独立的 ``AgentSession`` ，各自维护专属的大模型规划器（ ``Planner`` ）、事件记忆（ ``EventMemory`` ）、局部拓扑世界图（ ``WorldGraph`` ）以及统一工具箱（ ``Toolkit`` ）。

2. **零共享内存与标准字节流信封（Wire Envelope）**
   任何跨机交互都必须通过 ``comm_send`` 或 ``comm_broadcast`` 打包为不可变 UTF-8 字节流信封（ ``Envelope`` ），精确计量每包字节数（ ``serialized_bytes`` ），物理杜绝引用透传与特权真值泄露。

3. **可插拔无线信道仿真与 Sim2Real 同构**
   上层智能体统一面向 ``CommEndpoint`` 抽象编程，底层可在 ``IdealChannel`` （理想无损）、 ``ImpairedChannel`` （带宽令牌桶、MTU 上限、丢包率、时延抖动、欧氏距离截断）、 ``BlackoutChannel`` （断联基线）与真实 **USRP** 电台驱动之间零代码切换。

4. **可审计的动作与通信回执（Verifiable Receipts）**
   ``HarnessRuntime`` 为每一次改变世界状态的物理动作（ ``WORLD_MUTATING`` ）和无线发包（ ``NETWORK_TX`` ）自动签发回执（ ``Receipt`` ），实时审计“运动后未重新观测”或“未闭合调用”等违规行为（确保 ``violations == 0`` ）。
