协同协议与跨机记忆同步
=======================

在标准底层通信原语之上，CoNAH 为多机具身探索提供了 4 类结构化协同协议载荷（详见 ``tasks/multi_harness_vln/collaboration.py`` ）：

.. list-table::
   :header-rows: 1
   :widths: 26 32 42

   * - 协议消息类型
     - 传输字段摘要
     - 接收端消费方式与协同收益
   * - ``GOAL_ASSIGNED`` / ``GOAL_FOUND``
     - ``{goal_id, category, owner, state}``
     - 同步动态多目标任务板：先完成自身目标的机器人自动接力认领下一个 ``PENDING`` 目标；当机器人数多于目标数（ ``N > M`` ）时自动加入协同合搜。
   * - ``OBJECT_SIGHTING``
     - ``{category, global_xz, confidence}``
     - 当机器人顺路发现队友目标时广播全局 3D 坐标；队友接收后经 ``SE(2) Global <-> Local`` 刚体变换直接投影进自身局部 FMM 地图引导跨房导航。
   * - ``PLACE_OBSERVED``
     - ``{room_type, objects_seen, global_xz}``
     - 接收端将队友已搜房间的全局坐标换算为相对自身当前位姿的距离与方位（前/左/右/后），注入 ``[TEAM_SHARED_MEMORY]`` 大模型提示词。
   * - ``INTENT_CLAIM``
     - ``{robot_id, claimed_room_xz, direction}``
     - 空间防冲突声明，避免多台机器人扎堆重复探索同一房间。
