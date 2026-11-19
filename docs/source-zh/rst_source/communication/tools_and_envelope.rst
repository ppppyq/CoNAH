通信工具与信封契约
===================

在 CoNAH 中，机间通信由 ``comm_harness/communication/`` 提供统一内核实现，包含 **标准线缆信封（Envelope）** 与 **4 个一等公民通信工具（CommTools）**。

1. 四个标准通信 Tool（ ``comm_harness/communication/tools.py`` ）
-----------------------------------------------------------------

通过 ``tk.add_tools(build_comm_tools(endpoint))`` ，任何机器人的 ``Toolkit`` 都会自动向大模型暴露以下 4 个标准工具：

.. list-table::
   :header-rows: 1
   :widths: 20 26 54

   * - 工具名称
     - 副作用与回执审计
     - 参数与功能说明
   * - ``comm_send``
     - ``SideEffect.NETWORK_TX`` （签发 ``Receipt`` ）
     - ``recipients: list[str], topic: str, payload: dict`` — 向指定队友列表发送单播或组播消息。
   * - ``comm_broadcast``
     - ``SideEffect.NETWORK_TX`` （签发 ``Receipt`` ）
     - ``topic: str, payload: dict`` — 向当前网络中除自身外的所有已注册队友广播消息。
   * - ``comm_poll``
     - ``SideEffect.READ_ONLY`` （只读）
     - ``max_messages: int = 20, topic: str = ""`` — 拉取本机收件箱中已到达且未过期的消息。
   * - ``comm_status``
     - ``SideEffect.SPECTRUM_SENSING`` （只读）
     - 查询在线队友列表、待读收件箱深度、累计收发包数及信道丢包原因统计。

2. 不可变字节流信封（ ``Envelope`` ）
---------------------------------------

所有跨机载荷在进入信道前都会通过 ``Envelope.create()`` 编码为规范化 UTF-8 JSON 字节流，并在接收端重新解码生成独立字典副本，从而在物理上杜绝进程内/跨进程的对象引用共享：

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
