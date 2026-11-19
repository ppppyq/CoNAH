Core Interfaces
================

Summary of the five core extension interfaces in CoNAH:

1. **``CommEndpoint``** (``comm_harness/communication/loopback.py``): Uniform per-robot communication handle (``send``, ``broadcast``, ``poll``, ``peers``, ``status``) implemented by ``InMemoryNodeEndpoint``, ``LocalhostCommClient``, and future ``UsrpCommClient``.
2. **``ChannelModel``** (``comm_harness/communication/channels.py``): Evaluates per-link delivery, latency, and drop reasons (``evaluate(envelope, recipient, now) -> ChannelDecision``).
3. **``PlatformSpec``** (``platforms/__init__.py``): Registers simulator or real-robot factories, prompt bundles, and declared capability names.
4. **``Toolkit`` & ``@tool``** (``comm_harness/tools/``): Declares JSON schemas, side effects, concurrency locks, and automatic ``HarnessRuntime`` receipt tracking.
5. **``Planner``** (``comm_harness/planner/base.py``): Unified reasoning loop contract (``solve(system_prompt, user_message, toolkit, max_turns) -> PlannerResult``).
