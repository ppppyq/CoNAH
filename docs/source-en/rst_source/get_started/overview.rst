CoNAH Overview
=================

Collaborative Exploration in Embodied Intelligence Under Real-World Communication
----------------------------------------------------------------------------------

**Embodied Intelligence** in the physical world—such as collaborative exploration of unknown 3D buildings, long-horizon vision-language navigation, multi-robot search and rescue, and wide-area inspection—requires agents to perceive through ego-centric sensors, build spatial memory, and act physically in the environment. When missions exceed a single robot's field of view, mobility range, or onboard compute, multi-agent collaborative exploration becomes essential.

However, transitioning embodied intelligence from single-agent settings to real-world collaborative exploration exposes two core limitations in existing frameworks:

1. **Idealized Communication Assumptions**: Many multi-agent benchmarks implicitly assume instantaneous, lossless, infinite-bandwidth shared memory or raw image sharing, ignoring real physical-world wireless constraints such as **bandwidth contention, propagation/queueing latency, NLOS wall attenuation, coverage dead zones, and intermittent disconnections**.
2. **Separating Mobile Robots from Physical Network Infrastructure (AP / RAN)**: In real deployments, **Wi-Fi APs, 5G RAN nodes, and edge servers are themselves physically embodied in the environment**—anchored at concrete 3D locations with physical antenna coverage, RF/channel-state sensing (CSI / RSSI / spectrum awareness), and resource/beam/relay actuation. Treating AP/RAN nodes as invisible pipes prevents joint decision-making across physical exploration and wireless connectivity.

**CoNAH (Communication-Native Agentic Harness)** unifies **mobile robots** and **AP / RAN infrastructure** as **heterogeneous embodied agents**, integrating perception, reasoning, spatial memory, physical actuation, and **pluggable communication capabilities** into a standardized harness designed for non-ideal wireless environments.

.. image:: /_images/arch_en.png
   :alt: CoNAH Communication-Native Architecture for Multi-Robot Embodied Exploration
   :width: 100%

Four Core Design Principles
----------------------------

1. **Generalized Embodiment: Mobile Robots and AP / RAN Nodes in One Harness**
   CoNAH treats **Wi-Fi APs, 5G RAN, and edge nodes as a first-class class of embodied agents** alongside mobile robots (UGVs, UAVs, indoor navigation robots). While mobile robots explore physical space via cameras, depth sensors, and locomotion actuators, AP / RAN nodes operate from their physical locations via RF sensing, uplink/downlink scheduling, store-and-forward caching across dead zones, semantic compression, and compute offloading. The framework natively supports **Wi-Fi / 5G infrastructure, D2D / Mesh, and hybrid relay** topologies.

2. **Impairment-Aware Collaborative Exploration**
   Just like walls and gravity, wireless propagation impairments (bandwidth limits, packet loss, latency/jitter, NLOS blockage, and coverage holes) belong to the **external physical environment**. Rather than hiding these realities, CoNAH exposes live link state and exact ``Envelope`` byte costs so agents make communication-aware embodied decisions—compressing memory deltas under congestion, exploring autonomously through dead zones while buffering discoveries, or repositioning into coverage when team synchronization is needed.

3. **Pluggable Communication Modules & Capabilities**
   From the Harness perspective, what plugs into an embodied agent is not the physical channel itself, but the agent's **communication modules and communication capabilities** (e.g., Wi-Fi / 5G RAN interfaces, USRP SDR transceivers, unicast/broadcast tools, link-state sensing, semantic codecs, and relay capabilities). Exposed as standard Tools (``comm_send``, ``comm_broadcast``, ``comm_poll``, ``comm_status``) inside each node's ``Toolkit`` over a uniform ``CommEndpoint`` contract, they enable zero-code Sim2Real transfer from local simulation to real **Wi-Fi / 5G / USRP** hardware.

4. **Isolated Loops & Verifiable Receipts**
   Each embodied node runs an independent ``AgentSession``, ``Planner``, and local topological memory (``WorldGraph``), decoupled from simulators and hardware drivers via lightweight local RPC. ``HarnessRuntime`` issues auditable ``Receipt`` records for every ``WORLD_MUTATING`` action and ``NETWORK_TX`` transmission, enforcing zero-violation execution (``violations == 0``) while keeping privileged evaluation truth strictly isolated on the server.
