Communication Tools & Channels
=================================

CoNAH provides first-class communication tools and channel impairment models in ``comm_harness/communication/``.

The Four Standard Communication Tools
--------------------------------------

Calling ``tk.add_tools(build_comm_tools(endpoint))`` equips a robot's ``Toolkit`` with four model-callable primitives:

.. list-table::
   :header-rows: 1
   :widths: 22 25 53

   * - Tool Name
     - Side Effect & Auditing
     - Parameters & Behavior
   * - ``comm_send``
     - ``SideEffect.NETWORK_TX`` (issues ``Receipt``)
     - ``recipients: list[str], topic: str, payload: dict`` — Send unicast/multicast structured JSON to specific teammates.
   * - ``comm_broadcast``
     - ``SideEffect.NETWORK_TX`` (issues ``Receipt``)
     - ``topic: str, payload: dict`` — Broadcast a packet to all registered teammates except self.
   * - ``comm_poll``
     - ``SideEffect.READ_ONLY`` (concurrent-safe)
     - ``max_messages: int = 20, topic: str = ""`` — Drain arrived, non-expired envelopes from the robot's inbox.
   * - ``comm_status``
     - ``SideEffect.SPECTRUM_SENSING`` (read-only)
     - Query active peers, inbox queue depth, transmitted/received packet counts, and channel drop reasons.

Pluggable Channel Models (``ChannelModel``)
--------------------------------------------

- **IdealChannel**: Lossless, zero-latency delivery for upper-bound collaborative evaluation.
- **ImpairedChannel**: Configurable MTU limits (``mtu_bytes``), token-bucket bandwidth caps (``bandwidth_bytes_per_sec``), packet loss probability (``drop_prob``), latency/jitter, and Euclidean range cutoffs (``max_range_m``).
- **BlackoutChannel**: Drops all cross-robot packets (``No-Comm`` ablation baseline).
