Local Memory & Receipt Auditing
================================

Each CoNAH robot maintains its own local topological memory (``comm_harness/memory/``) and runtime compliance auditor (``comm_harness/tools/runtime.py``).

1. ``WorldGraph`` & Local IDF Retrieval
----------------------------------------

- Maintains ``Place``, ``Object`` (with alias merging), and ``Failure`` (per-subgoal penalty) nodes and navigable edges.
- ``recall()`` ranks historical places locally without any LLM call using IDF cosine similarity, recency, salience, and 1-hop topological expansion.
- ``TodoMemory`` tracks subgoal lifecycles (``PENDING`` / ``ACTIVE`` / ``COMPLETED`` / ``BLOCKED``) with evidence checks.

2. ``HarnessRuntime`` Zero-Violation Auditing
----------------------------------------------

Every tool call is traced as paired ``HARNESS_TOOL_CALL`` / ``HARNESS_TOOL_RESULT`` events; every ``WORLD_MUTATING`` action and ``NETWORK_TX`` packet issues a ``HARNESS_RECEIPT`` so runs can be audited for ``violations == 0``.
