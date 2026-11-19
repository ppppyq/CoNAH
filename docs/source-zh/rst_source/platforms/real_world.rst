USRP 软件无线电与真机接入
===========================

CoNAH 的核心优势在于 **仿真与真机共享完全相同的上层通信工具契约（Sim2Real Isomorphism）** 。

从软件信道仿真平滑切换到真实 USRP 电台
----------------------------------------

在 CoNAH 中，大模型调用的 4 个通信工具（ ``comm_send`` 、 ``comm_broadcast`` 、 ``comm_poll`` 、 ``comm_status`` ）只依赖抽象接口 ``CommEndpoint`` ：

1. **纯电脑仿真阶段** ：
   ``CommToolProvider`` 绑定 ``LocalhostCommClient`` ，通过本机回环 RPC 将字节流发往 ``LocalhostCommServer`` （软件模拟无线衰减、带宽限制与丢包）。

2. **真实多机 + USRP 硬件阶段** ：
   每台移动机器人（阿克曼底盘或无人机机载工控机）通过 USB 3.0 / 千兆网口连接一台 **USRP B210 / X310** 软件无线电。只需编写一个继承 ``CommEndpoint`` 的 ``UsrpCommClient`` ，通过本机 RPC 对接机载 GNU Radio / UHD 基带收发守护进程：

   - 调用 ``comm_send`` 时，机载 USRP 将 ``Envelope`` 字节流调制成真实射频波形从天线发射；
   - 信号在真实室内环境中经历多径衰落、穿墙损耗与同频干扰；
   - 队友机载 USRP 解调并通过 CRC 校验的帧进入本机接收队列，供队友大模型通过 ``comm_poll`` 读取，同时 ``comm_status`` 直接上报真实物理层测得的 RSSI / SNR 与误帧率。

**整个迁移过程中，智能体的提示词、Planner 循环、WorldGraph 记忆同步与任务分工代码无需修改任何一行。**
