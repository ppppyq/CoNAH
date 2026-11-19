Evaluation Metrics
===================

CoNAH jointly evaluates **task completion**, **wireless communication cost**, and **harness execution compliance**, while keeping all privileged ground-truth coordinates locked inside server-side evaluators.

1. Multi-Robot Task Metrics
----------------------------

- **Team SR**: Fraction of mission goals successfully reached and verified by at least one robot (``[0.0, 1.0]``).
- **Team Oracle SR**: Fraction of goals where at least one robot approached within the success threshold along its trajectory.
- **Team SPL**: Path-efficiency-weighted team success rate.

2. Communication Efficiency Metrics
------------------------------------

- **Source Bytes / Delivered Bytes**: Exact UTF-8 wire payload size summed from ``Envelope.serialized_bytes``.
- **Packet Delivery Ratio (PDR)**: Delivered links divided by attempted links, broken down by drop reason (``mtu_exceeded``, ``bandwidth_exceeded``, ``out_of_range``, ``packet_loss``, ``channel_blackout``).
- **Bytes-per-Success**: Average cross-robot bytes transmitted per completed goal.

3. Harness Compliance Metrics
------------------------------

- **Harness Violations**: Runtime audit count tracked by ``HarnessRuntime`` (healthy runs maintain ``violations == 0``).
- **Receipts**: Verifiable ``HARNESS_RECEIPT`` records issued for every ``WORLD_MUTATING`` action and ``NETWORK_TX`` transmission.
