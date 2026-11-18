通信工具与信道仿真
======================

在 CoNAH 中，通信不是写在某个特定任务里的临时变量传递，而是由 ``comm_harness/communication/`` 提供的标准内核原语。

四个标准通信 Tool
--------------------

通过调用 ``tk.add_tools(build_comm_tools(endpoint))`` ，每台机器人的 ``Toolkit`` 都会自动挂载以下四个大模型可直接调用的通信工具：

.. list-table::
   :header-rows: 1
   :widths: 22 25 53

   * - 工具名称
     - 副作用与回执策略
     - 参数与功能说明
   * - ``comm_send``
     - ``SideEffect.NETWORK_TX`` （自动签发 ``Receipt`` ）
     - ``recipients: list[str], topic: str, payload: dict`` — 向一个或多个指定队友定向发送结构化数据包。
   * - ``comm_broadcast``
     - ``SideEffect.NETWORK_TX`` （自动签发 ``Receipt`` ）
     - ``topic: str, payload: dict`` — 向当前通信网络中除自己以外的所有注册队友广播数据包。
   * - ``comm_poll``
     - ``SideEffect.READ_ONLY`` （只读并发安全）
     - ``max_messages: int = 20, topic: str = ""`` — 从本机接收缓冲区取出已到达且未过期的消息（可按主题过滤）。
   * - ``comm_status``
     - ``SideEffect.SPECTRUM_SENSING`` （只读感知）
     - 查询在线队友列表、本机收件箱待读深度、累计收发包数及信道丢包原因分布。

标准消息信封（Envelope）
--------------------------

所有发出的消息在进入信道前都会封装为 ``Envelope`` 并编码为 UTF-8 JSON 字节流：

.. code-block:: json

   {
     "schema_version": 1,
     "message_id": "step1_coop_grid/robot_0/1",
     "episode_id": "step1_coop_grid",
     "sender": "robot_0",
     "recipients": ["robot_1"],
     "topic": "target_sighting",
     "payload": {"category": "vase", "pos": [1, 0]},
     "sequence": 1,
     "created_at": 1795006487.12,
     "ttl_s": 60.0,
     "priority": 0
   }

可插拔信道损伤模型（ChannelModel）
------------------------------------

``LocalhostCommServer`` 与 ``InMemoryCommBus`` 通过 ``ChannelModel`` 决定每一条单播链路 ``(sender → recipient)`` 是否送达及到达时延：

- **IdealChannel**：无损、零延迟、无限带宽，用于测定协同策略性能上限。
- **ImpairedChannel**：支持多维受限无线环境建模：

  - ``mtu_bytes`` ：单包最大字节上限（超长包触发 ``mtu_exceeded`` 丢弃，倒逼智能体压缩记忆增量）；
  - ``bandwidth_bytes_per_sec`` / ``burst_bytes`` ：令牌桶带宽限速（超速触发 ``bandwidth_exceeded`` ）；
  - ``drop_prob`` ：独立伯努利随机丢包率（ ``0.0 ~ 1.0`` ）；
  - ``base_latency_s`` / ``jitter_s`` ：基础传播与处理延迟及随机抖动；
  - ``max_range_m`` ：基于机器人实时物理坐标的欧氏通信半径截断（超距触发 ``out_of_range`` ）。

- **BlackoutChannel**：丢弃全部跨机流量（ ``channel_blackout`` ），用于零通信（ ``No-Comm`` ）消融基线实验。
