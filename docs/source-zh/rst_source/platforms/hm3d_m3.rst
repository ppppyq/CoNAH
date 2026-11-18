HM3D-M³ 3D 多机协同导航
=========================

``platforms/hm3d_m3/`` 与 ``tasks/multi_harness_vln/`` 在真实扫描室内三维场景（Habitat-Matterport 3D）上支持任意 ``N`` 台机器人对 ``M`` 个多模态目标（ObjectNav / TextNav）展开动态分工与跨房间协同导航。

.. image:: /_images/layers_zh.png
   :alt: HM3D-M³ 多机协同分层工程架构
   :width: 100%

核心协同机制
--------------

1. **单份 GPU 权重共享感知（SharedGroundedSAM）**
   多台机器人通过线程安全锁共享同一份 ``GroundingDINO-SwinT + RepViT-SAM`` 开放词表分割权重， ``N`` 机并行时显存峰值仅约 ``1.8 GB`` 。

2. **动态任务板与自动接力（Task Board Relay）**
   支持 ``N <= M`` （初始各自认领 ``PENDING`` 目标，先完成的机器人自动接力下一个未完成目标）以及 ``N > M`` （空闲机器人自动加入 ``Co-Search`` 协同合搜）。

3. **跨机 3D 目标线索与 SE(2) 局部 FMM 直接投影**
   当 ``robot_0`` 在探索自身目标途中顺路识别到队友的目标物体时，自动结合深度图反投影出全局三维坐标并通过 ``OBJECT_SIGHTING`` 广播；接收端经 ``SE(2)`` 刚体变换直接投影至自身局部 ``Semantic_Mapping`` FMM 栅格地图引导跨房导航。

4. **双机同屏与慢放中文实时解说视频合成**
   运行结束后自动生成 ``multi_robot_rollout.mp4`` ，底部附带 4 行高对比度中文实时解说横幅（展示每步调用的工具、大模型推理思考、跨机广播与距离门控状态）。

运行示例（2 机 3 目标协同搜索）
---------------------------------

.. code-block:: bash

   /path/to/m3/bin/python -m comm_harness \
     --method multi-harness-vln \
     --config tasks/multi_harness_vln/configs/multi_harness_vln_2robots_3goals.json \
     --model qwen3.8-max \
     --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
     --run-dir runs/hm3d-2robots-3goals
