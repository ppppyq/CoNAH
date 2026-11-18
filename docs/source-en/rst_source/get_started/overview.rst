CoNAH Overview
=================

Why a Communication-Native Multi-Agent Harness?
------------------------------------------------

Real-world embodied missions—indoor multi-room search, disaster response, and large-scale inspection—naturally require teams of robots working in parallel. Existing embodied agent frameworks typically suffer from two limitations:

1. **Single-Agent Centricity**: Built strictly around ``1 Planner <-> 1 Toolkit <-> 1 Robot``, lacking primitives for cross-robot topological memory sync, spatial deconfliction, and dynamic task relay.
2. **Shared-Memory Illusion**: Multi-agent scripts often share mutable Python dictionaries inside a single process, ignoring serialization byte budgets, bandwidth caps, NLOS wall blockage, and packet drops—causing immediate failure during Sim2Real deployment.

**CoNAH (Communication-Native Agentic Harness)** addresses both challenges by elevating **inter-robot communication** to a first-class, auditable harness primitive.

.. image:: /_images/arch_en.png
   :alt: CoNAH Decentralized Multi-Robot Architecture
   :width: 100%

Core Design Principles
-----------------------

1. **Decentralized Per-Robot Harnesses**
   Every robot runs an independent ``AgentSession`` owning its own ``Planner``, ``EventMemory``, local ``WorldGraph``, and ``Toolkit``.

2. **Zero Shared Memory & Wire Envelopes**
   All inter-robot messages are serialized into immutable UTF-8 ``Envelope`` packets, accurately metering ``serialized_bytes`` and eliminating shared-reference leaks.

3. **Pluggable Channel Simulation & Sim2Real Isomorphism**
   Agents program against a uniform ``CommEndpoint`` contract while the underlying medium switches cleanly across ``IdealChannel``, ``ImpairedChannel`` (MTU, token-bucket bandwidth, loss, latency, range), ``BlackoutChannel``, and real **USRP** transceivers.

4. **Verifiable Action & Transmission Receipts**
   ``HarnessRuntime`` issues cryptographically structured ``Receipt`` records for every world-mutating physical action and ``NETWORK_TX`` packet transmission, enforcing zero-violation execution loops.
