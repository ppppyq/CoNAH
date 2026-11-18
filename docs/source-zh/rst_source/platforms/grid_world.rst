GridWorld 2D 多进程协同
=========================

``platforms/grid_world/`` 是 CoNAH 的零 GPU 标杆验证平台，完整实现了 **环境 RPC 服务端 + 独立信道仿真服务端 + 多进程智能体客户端** 的服务化架构。

平台工具列表
--------------

每个接入 ``grid_world`` 的机器人通过 ``build_grid_toolkit`` 自动获得 3 个物理环境工具与 4 个标准通信工具：

- ``observe_local`` （只读感知）：返回机器人当前网格坐标 ``[x, y]`` 以及曼哈顿距离 ``<= 1`` 内的可见物体列表；
- ``move_grid(direction)`` （物理动作，消耗 1 步并生成回执）：向 ``north | south | east | west | stay`` 移动一格，并实时将最新坐标同步给 ``LocalhostCommServer`` 供距离衰减信道模型计算；
- ``claim_found(category)`` （终止动作）：在目标周围 1 格内宣告找到目标；
- ``comm_send`` / ``comm_broadcast`` / ``comm_poll`` / ``comm_status`` ：跨进程无线通信原语。

运行协同基准与单元测试
------------------------

.. code-block:: bash

   # 运行完整单元测试（覆盖序列化隔离、令牌桶限速、距离截断、双进程协同）
   python3 -m unittest tests.test_step1_comm_foundation -v

   # 运行距离受限实验（例如限制通信半径为 4.0 格）
   python3 -m tasks.coop_grid_search.demo --channel impaired --max-range-m 4.0
