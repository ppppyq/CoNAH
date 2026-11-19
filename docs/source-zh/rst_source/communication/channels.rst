无线信道模型
=============

CoNAH 将“智能体发什么消息（ ``Envelope`` ）”与“无线介质怎么传输（ ``ChannelModel`` ）”完全解耦。

内置信道模型（ ``comm_harness/communication/channels.py`` ）
------------------------------------------------------------

1. **``IdealChannel`` （理想无损基准）**
   零丢包、零延迟、无限带宽，仅检查消息 TTL 是否过期，用于测定多机协同算法的理论上限。

2. **``ImpairedChannel`` （多维受限无线信道）**
   支持按固定随机种子复现以下 5 类真实通信约束：

   - ``mtu_bytes`` ：单包最大字节上限（超长载荷直接触发 ``mtu_exceeded`` 丢包）；
   - ``bandwidth_bytes_per_sec`` / ``burst_bytes`` ：令牌桶带宽限速（突发耗尽后触发 ``bandwidth_exceeded`` ，随时间平滑恢复）；
   - ``drop_prob`` ：伯努利随机丢包概率（ ``0.0 ~ 1.0`` ）；
   - ``base_latency_s`` / ``jitter_s`` ：基础传输时延与随机抖动；
   - ``max_range_m`` ：基于两机实时物理坐标的欧氏距离截断（超距触发 ``out_of_range`` ）。

3. **``BlackoutChannel`` （零通信消融基线）**
   丢弃全部跨机数据包（ ``channel_blackout`` ），用于量化通信对多机任务成功率的净增益。

面向高保真电磁仿真的扩展路线
------------------------------

由于信道服务端本身以独立 RPC 进程运行（ ``LocalhostCommServer`` ），后续接入依赖 TensorFlow + Mitsuba 3 的 **NVIDIA Sionna RT 三维穿墙射线追踪** 或 C++ **NS-3 网络协议栈仿真** 时，无需修改主框架环境，直接通过 RPC 替换后端信道服务即可。
