HM3D-M³ 3D Collaborative VLN
==============================

``platforms/hm3d_m3/`` and ``tasks/multi_harness_vln/`` enable ``N`` autonomous robots to collaboratively locate ``M`` multimodal goals (ObjectNav / TextNav) in realistic 3D indoor scans.

Key Capabilities
-----------------

1. **Shared GPU Open-Vocabulary Perception (``SharedGroundedSAM``)**: Thread-locked weight sharing across ``N`` robots (~1.8 GB peak VRAM).
2. **Dynamic Task Board & Goal Relay**: Automatic ``PENDING -> IN_PROGRESS -> FOUND`` relay and ``N > M`` co-search.
3. **Cross-Robot 3D Sighting & SE(2) FMM Projection**: Opportunistic target detections are unprojected to global 3D XZ coordinates, broadcast via ``OBJECT_SIGHTING``, and transformed directly into teammates' local FMM navigation grids.
4. **Narrated Split-Screen MP4 Export**: Automatically renders synchronized multi-robot rollout videos.

Run Example
------------

.. code-block:: bash

   /path/to/m3/bin/python -m comm_harness \
     --method multi-harness-vln \
     --config tasks/multi_harness_vln/configs/multi_harness_vln_2robots_3goals.json \
     --model qwen3.8-max \
     --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
     --run-dir runs/hm3d-2robots-3goals
