Roadmap & Releases
===================

v0.1.0 (Current)
-----------------

- Orthogonal 3-pillar architecture (``comm_harness/``, ``platforms/``, ``tasks/``) with automated boundary tests.
- Intra-machine cross-process communication stack: ``Envelope``, ``IdealChannel`` / ``ImpairedChannel`` / ``BlackoutChannel``, ``LocalhostCommServer`` (``127.0.0.1`` RPC), and 4 standard communication tools.
- Zero-GPU ``GridWorld 2D`` multi-process cooperative search and channel ablation suite (``74/74`` unit tests passing).
- ``HM3D-M³`` 3D multi-robot collaborative VLN with ``SharedGroundedSAM``, dynamic goal relay, and cross-robot ``SE(2)`` FMM sighting projection.
- Bilingual EN / ZH Sphinx documentation hosted on Read the Docs.

v0.2.0 (Next)
--------------

- Full RPC service separation for ``HM3D-M³`` (`hm3d_env_server` + `perception_server`) paired with ``LocalhostCommServer``.
- Distributed ``WorldGraph`` incremental delta synchronization (``ΔG``) and cross-robot loop closure.
- Real-time packet-capture & topology visualization tab in the Live Dashboard.

v1.0.0 (Vision)
----------------

- High-fidelity 3D RF ray-tracing integration (**NVIDIA Sionna RT**) and **NS-3** network stack simulation.
- Real multi-robot (**Ackermann UGV / UAV**) + **USRP** software-defined radio Sim2Real deployment.
