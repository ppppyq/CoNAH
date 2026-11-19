CoNAH Overview
=================

Why a Communication-Native Embodied Harness?
---------------------------------------------

Real-world multi-robot missions—indoor multi-room search, warehouse inspection, and disaster response—inherently require teams of robots working together. Yet existing embodied agent frameworks face two fundamental gaps when moving toward realistic deployments:

1. **Ignoring Non-Ideal Wireless Communication**: Most multi-agent benchmarks implicitly assume instantaneous, lossless, infinite-bandwidth shared memory between robots, overlooking **bandwidth contention, propagation and queueing latency, NLOS wall attenuation, coverage dead zones, and intermittent disconnections**. Policies that depend on idealized synchronization break down immediately in the real world.
2. **Treating Network Infrastructure as Invisible**: Practical multi-robot deployments are not restricted to pure peer-to-peer ad-hoc networks—they commonly rely on **Wi-Fi APs, 5G base stations / MEC edge servers**, or **hybrid AP + relay topologies**. Traditional frameworks treat the AP or edge server as a passive pipe rather than allowing **the AP / edge node itself to participate as a first-class node in the Harness**.

**CoNAH (Communication-Native Agentic Harness)** is built around **non-ideal wireless realism, topology agnosticism, and unified multi-node orchestration**, elevating communication from an implicit assumption to an observable, controllable, and auditable first-class primitive.

.. image:: /_images/arch_en.png
   :alt: CoNAH Multi-Robot Embodied Architecture
   :width: 100%

Four Core Design Principles
----------------------------

1. **Impairment-First Communication Modeling**
   Every cross-node message is serialized into an immutable UTF-8 ``Envelope`` with exact ``serialized_bytes`` metering and evaluated by a pluggable ``ChannelModel`` covering **MTU limits, token-bucket bandwidth caps, packet loss, latency/jitter, wall attenuation, and coverage cutoffs**. Agents inspect live link quality via ``comm_status`` and adapt both payload granularity and physical exploration accordingly.

2. **Topology-Agnostic & Infrastructure-as-a-Node (Wi-Fi AP / 5G / Mesh)**
   CoNAH does not force strictly decentralized P2P topologies. It natively supports **Wi-Fi AP / 5G infrastructure networks, direct D2D / Mesh links, and hybrid relay topologies**. Crucially, **an AP or 5G edge server can join the Harness as a first-class node**—performing store-and-forward caching across coverage dead zones, congestion-aware semantic compression, task-board coordination, or heavy perception/VLM compute offloading.

3. **Service-Oriented Isolation & Sim2Real Isomorphism**
   Each node runs an isolated ``AgentSession``, ``Toolkit``, and local ``WorldGraph``, decoupled from simulators and hardware drivers via lightweight local RPC. Agents program uniformly against ``CommEndpoint``, enabling zero-code switching from software channel simulation (``LocalhostCommServer`` / Sionna RT) to real **Wi-Fi, 5G, or USRP** transceivers.

4. **Verifiable Action & Transmission Receipts**
   ``HarnessRuntime`` automatically issues auditable ``Receipt`` records for every ``WORLD_MUTATING`` physical action and ``NETWORK_TX`` packet transmission, enforcing zero-violation execution loops (``violations == 0``) while keeping privileged ground truth strictly isolated inside server-side evaluators.
