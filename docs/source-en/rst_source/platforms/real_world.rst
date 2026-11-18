USRP SDR & Real Robots
========================

CoNAH preserves **Sim2Real isomorphism** across software channel simulation and real software-defined radio (SDR) transceivers.

Switching from Localhost Simulation to USRP Hardware
-----------------------------------------------------

All four model-facing communication tools (``comm_send``, ``comm_broadcast``, ``comm_poll``, ``comm_status``) depend solely on the ``CommEndpoint`` interface:

- **In Simulation**: ``CommToolProvider`` binds ``LocalhostCommClient``, forwarding wire ``Envelope`` packets over ``127.0.0.1`` RPC to ``LocalhostCommServer`` (which simulates loss, bandwidth, and range).
- **On Real Robots with USRP**: Implement ``UsrpCommClient(CommEndpoint)`` connecting over local RPC to each robot's onboard UHD / GNU Radio transceiver daemon. Outgoing ``Envelope`` bytes are modulated over RF airwaves, while demodulated CRC-valid frames populate the teammate's ``comm_poll`` inbox alongside real measured RSSI/SNR in ``comm_status``.

**Zero lines of upper-level Planner, Prompt, WorldGraph, or Task Board code need to change.**
