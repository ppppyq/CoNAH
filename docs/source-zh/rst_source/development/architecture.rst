系统架构与通信边界
====================

理解 CoNAH 的内部设计，最关键的是区分 **“机内控制面 RPC”** 与 **“机间无线信道数据面”** 这两层完全不同性质的通信。

三段式端到端信号流（机内 RPC vs. 机间无线信道）
-------------------------------------------------

当 **机器人 0** 调用 ``comm_broadcast`` 向 **机器人 1** 发送目标线索时，数据流严格遵循三段式物理边界：

.. code-block:: text

   【机器人 0 体内（独立进程 A）】              【机间无线信道媒介】              【机器人 1 体内（独立进程 B）】
   ┌────────────────┐  ① 本机 RPC   ┌───────────────────────┐  ③ 本机 RPC   ┌────────────────┐
   │ Robot 0 大脑   │ ────────────▶ │ 仿真: LocalhostCommSrv│ ────────────▶ │ Robot 1 大脑   │
   │ (AgentSession) │  (可靠控制线) │ 真机: USRP 射频电磁波 │  (可靠控制线) │ (AgentSession) │
   │ 调 comm_send   │               │ (带宽限速/穿墙丢包)   │               │ 调 comm_poll   │
   └────────────────┘               └───────────────────────┘               └────────────────┘

- **第 ①、③ 段（机内控制面 RPC）** ：相当于机器人体内的神经线或工控机连接传感器/电台的 USB 数据线。它连接大脑进程与底层硬件驱动/仿真服务端，要求无损可靠，并解决不同库（如 Python 3.8 Habitat、TensorFlow Sionna、C++ UHD 驱动）之间的环境冲突。
- **第 ② 段（机间数据面无线信道）** ：相当于机器人天线之间的空气电磁波。所有带宽受限、MTU 截断、距离衰减与随机丢包均发生在这一段；严禁任何机器人绕过第 ② 段直接通过 RPC 读写另一台机器人的内存。

仓库三大顶层模块边界
----------------------

CoNAH 仓库在顶层严格划分为三个正交目录，单向依赖由自动化单元测试强制守护：

.. code-block:: text

   CoNAH/
   ├── comm_harness/      # 1. 纯通用智能体 Harness 内核（零仿真器依赖、零特定任务依赖）
   │   ├── cli/           #    命令行入口 (conah, conah-check-llm) 与终端控制台
   │   ├── communication/ #    Envelope 信封、ChannelModel、本机跨进程总线与 4 个通信 Tool
   │   ├── dashboard/     #    Web 实时状态监控与交互控制面板
   │   ├── memory/        #    WorldGraph 拓扑记忆、IDF 本地检索与 TodoMemory
   │   ├── planner/       #    多后端大模型 Tool-Calling 循环与 Token/Call 预算门控
   │   ├── session/       #    AgentSession 单机闭环、TeamSession 与事件流落盘
   │   ├── tools/         #    @tool 声明、Toolkit 并发调度、HarnessRuntime 回执审计
   │   └── rpc.py         #    零外部依赖的 HTTP JSON-RPC 服务端与客户端
   ├── platforms/         # 2. 平台契约注册表 (PlatformSpec) 与具体仿真器/真机实现
   │   ├── grid_world/    #    2D 多进程协同网格环境
   │   └── hm3d_m3/       #    HM3D 3D 室内多机导航与感知底层工具
   └── tasks/             # 3. 任务规则、多机协同协议与评测基准
       ├── coop_grid_search/
       ├── harness_vln/
       └── multi_harness_vln/
