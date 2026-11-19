路线图与版本说明
=================

v0.1.0（当前已发布）
---------------------

- **三大正交顶层目录架构落地**： ``comm_harness/`` （零仿真器依赖内核）、 ``platforms/`` （平台注册与工具）、 ``tasks/`` （任务与协同协议）。
- **原生本机跨进程通信底座**：实现标准 ``Envelope`` 、 ``IdealChannel`` / ``ImpairedChannel`` / ``BlackoutChannel`` 、 ``LocalhostCommServer`` （ ``127.0.0.1`` RPC）与 4 个标准通信 Tool（ ``comm_send`` / ``comm_broadcast`` / ``comm_poll`` / ``comm_status`` ）。
- **零 GPU 双进程验证环境**： ``GridWorld 2D`` + ``coop_grid_search`` 完整跑通理想与断联信道消融对比（ ``74/74`` 单元测试全部通过）。
- **HM3D-M³ 3D 多机协同导航**：支持 ``N`` 机 ``M`` 目标动态分工、 ``SharedGroundedSAM`` 、跨机 3D 线索 ``SE(2)`` FMM 投影及慢放中文解说视频合成。
- **双语文档站上线**：中英文 Sphinx 文档站自动部署至 Read the Docs。

v0.2.0（下一阶段规划）
-----------------------

- **HM3D-M³ 全面服务化（RPC 拆分）**：将 ``MultiAgentSimCoordinator`` 与 ``SharedGroundedSAM`` 拆分为独立 Python 3.8 RPC 服务，实现 HM3D 场景下多进程机器人与无线信道仿真器的全解耦运行。
- **分布式拓扑世界图同步（Distributed WorldGraph）**：支持跨机增量图同步（ ``ΔG`` ）与公共交汇节点拓扑缝合。
- **Dashboard 通信抓包视图**：在 Web 实时面板中增加多机通信拓扑与逐包丢包可视化。

v1.0.0（长期愿景）
-------------------

- **高保真无线仿真接入**：对接 NVIDIA Sionna RT 3D 电磁射线追踪与 NS-3 网络仿真器。
- **多机真机 + USRP 实测**：在阿克曼小车与无人机集群上完成基于 USRP 软件无线电的真实物理层 Sim2Real 协同导航演示。
