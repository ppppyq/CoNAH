Communication Ablations & Results
==================================

This page summarizes measured ``v0.1.0`` results on the **GridWorld 2D Multi-Process Channel Ablation** and **HM3D-M³ 3D 2-Robot 3-Goal Collaborative VLN**.

1. GridWorld 2D Multi-Process Ablation (``tasks/coop_grid_search``)
--------------------------------------------------------------------

Two independent OS processes (``robot_0`` and ``robot_1``) connect over ``127.0.0.1`` to ``GridEnvServer`` and ``LocalhostCommServer``, sharing opportunistic target sightings via ``comm_broadcast`` / ``comm_poll``:

.. list-table::
   :header-rows: 1
   :widths: 24 14 16 18 14 14

   * - Channel Model
     - Team SR
     - Steps / Robot
     - Sent / Delivered
     - Total Bytes
     - Violations
   * - ``IdealChannel``
     - **1.00**
     - 6 / 6
     - 4 / 2
     - 1,251 B
     - **0**
   * - ``BlackoutChannel`` (No-Comm)
     - 0.00
     - 16 / 16
     - 2 / 0
     - 650 B (all dropped)
     - **0**

2. HM3D-M³ 3D Indoor 2-Robot 3-Goal Experiment
-----------------------------------------------

Episode ``00849-a8BtkwhxdRV_01257``, ``num_robots = 2``, goals ``[vase (ON), mirror (TN), radiator (ON)]``, planner ``qwen3.8-max``, shared perception ``SharedGroundedSAM`` (~1.8 GB peak VRAM):

.. list-table::
   :header-rows: 1
   :widths: 22 16 16 18 28

   * - Goal (Modality)
     - Outcome
     - Final Dist
     - Min Dist
     - Key Behavior
   * - ``g2: mirror (TN)``
     - **Success (1.0)**
     - ``0.69 m``
     - ``0.07 m``
     - ``WorldGraph`` backtrack (``backtrack to 3``) + 2D BBox grounding (``SPL = 0.4382``)
   * - ``g3: radiator (ON)``
     - Near-threshold
     - ``1.03 m``
     - ``1.03 m``
     - Within ``3.1 cm`` of the ``1.0 m`` threshold
   * - ``g1: vase (ON)``
     - Exploring
     - ``2.94 m``
     - ``2.94 m``
     - Cross-room exploration and semantic mapping

Total inter-robot traffic across the full 3D episode was ~20 KB.
