Communication Tools & Envelope
===============================

CoNAH provides first-class communication tools and immutable wire envelopes in ``comm_harness/communication/``.

1. The Four Standard Communication Tools
-----------------------------------------

Mounting ``build_comm_tools(endpoint)`` exposes four primitives on each robot's ``Toolkit``:

- ``comm_send(recipients, topic, payload)`` (``SideEffect.NETWORK_TX``): Unicast/multicast transmission with verifiable ``Receipt``.
- ``comm_broadcast(topic, payload)`` (``SideEffect.NETWORK_TX``): Broadcast to all registered teammates except self.
- ``comm_poll(max_messages=20, topic="")`` (``SideEffect.READ_ONLY``): Drain arrived, non-expired envelopes from the local inbox.
- ``comm_status()`` (``SideEffect.SPECTRUM_SENSING``): Inspect active peers, inbox depth, packet counts, and channel drop reasons.

2. Immutable Wire ``Envelope``
-------------------------------

Every transmission is serialized to canonical UTF-8 JSON bytes on send and freshly decoded on receive, guaranteeing zero shared mutable references and exact ``serialized_bytes`` metering.
