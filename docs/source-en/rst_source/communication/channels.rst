Wireless Channel Models
========================

CoNAH decouples *what* agents transmit (``Envelope``) from *how* the medium delivers, delays, throttles, or drops packets (``ChannelModel``).

Built-In Channel Models (``comm_harness/communication/channels.py``)
---------------------------------------------------------------------

- **``IdealChannel``**: Lossless, zero-latency delivery for upper-bound collaborative evaluation.
- **``ImpairedChannel``**: Configurable ``mtu_bytes``, token-bucket ``bandwidth_bytes_per_sec`` / ``burst_bytes``, Bernoulli ``drop_prob``, ``base_latency_s`` / ``jitter_s``, and Euclidean ``max_range_m`` cutoff.
- **``BlackoutChannel``**: Drops all cross-robot traffic (``No-Comm`` ablation baseline).

External Physics & Network Simulators
--------------------------------------

Because the communication broker runs as a standalone RPC server (``LocalhostCommServer``), heavy external simulators such as **NVIDIA Sionna RT** (3D electromagnetic ray tracing) or **NS-3** can be plugged in via RPC without introducing dependency conflicts into the core harness.
