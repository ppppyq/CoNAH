System Architecture
=====================

CoNAH strictly separates **intra-robot control RPC** (the nervous system inside one robot) from **inter-robot wireless communication** (the air medium between different robots).

Three-Segment Signal Flow
--------------------------

.. code-block:: text

   [Robot 0 Process]              [Inter-Robot Air Medium]             [Robot 1 Process]
   ┌────────────────┐  ① Local RPC  ┌───────────────────────┐  ③ Local RPC  ┌────────────────┐
   │ Robot 0 Brain  │ ────────────▶ │ Sim: LocalhostCommSrv │ ────────────▶ │ Robot 1 Brain  │
   │ (AgentSession) │  (Reliable)   │ Real: USRP RF Waves   │  (Reliable)   │ (AgentSession) │
   │ comm_send(...) │               │ (Loss / BW / Range)   │               │ comm_poll(...) │
   └────────────────┘               └───────────────────────┘               └────────────────┘

- **Segments ① & ③ (Intra-Robot Control RPC)**: Reliable local IPC connecting an agent's Python reasoning loop to its own sensor/actuator/transceiver drivers—eliminating Python/CUDA version conflicts across simulators and SDKs.
- **Segment ② (Inter-Robot Wireless Medium)**: Physical or simulated RF propagation where MTU caps, bandwidth throttling, wall blockage, and packet loss occur. Agents can never bypass Segment ② to read another robot's memory.

Three Orthogonal Top-Level Pillars
-----------------------------------

- ``comm_harness/``: Pure platform-agnostic & task-agnostic core (``cli/``, ``communication/``, ``dashboard/``, ``memory/``, ``planner/``, ``session/``, ``tools/``, ``rpc.py``).
- ``platforms/``: ``PlatformSpec`` registry, coordinate geometry, and concrete platform packages (``grid_world/``, ``hm3d_m3/``).
- ``tasks/``: Task rules, collaborative protocols, and benchmark runners (``coop_grid_search/``, ``harness_vln/``, ``multi_harness_vln/``).
