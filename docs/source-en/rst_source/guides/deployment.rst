Distributed & Remote RPC Deployment
====================================

Built on standard HTTP JSON-RPC (``comm_harness/rpc.py``), CoNAH scales smoothly across three deployment topologies without changing agent code:

1. **Single-Machine Loopback (127.0.0.1)**: Environment server, channel broker, and ``N`` robot worker processes run on one workstation with sub-millisecond RPC latency.
2. **Multi-Server / Multi-GPU**: Host heavy 3D simulators (e.g., Habitat Python 3.8) and ray-tracing channel servers (e.g., Sionna Python 3.11) on separate servers/containers connected via ``--env-endpoint`` and ``--comm-endpoint``.
3. **Multi-Robot Real Hardware**: Each onboard robot computer runs its own ``AgentSession`` process controlling local actuators and an onboard **USRP** SDR transceiver daemon over local RPC, while robots communicate strictly over RF airwaves.
