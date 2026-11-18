Quickstart
=============

This guide walks through installing CoNAH and running a multi-process (``127.0.0.1``) two-robot cooperative search with wireless channel ablations in **under 30 seconds without a GPU**.

Prerequisites
--------------

- Linux or macOS with Python 3.8+ (Python 3.10 or 3.11 recommended).
- *(Optional)* An OpenAI-compatible API key (Qwen, OpenAI, DeepSeek, Claude, or local Ollama) when testing live LLM reasoning.

1. Install CoNAH
-----------------

.. code-block:: bash

   git clone https://github.com/your-org/CoNAH.git
   cd CoNAH
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .

2. Run Multi-Process Cooperative Search
----------------------------------------

``tasks.coop_grid_search.demo`` launches two standalone localhost RPC servers (``GridEnvServer`` and ``LocalhostCommServer``) alongside two separate OS worker processes (``robot_0`` and ``robot_1``):

.. code-block:: bash

   python3 -m tasks.coop_grid_search.demo --channel ideal

Both robots opportunistically spot each other's assigned targets, exchange coordinates over ``LocalhostCommServer`` via ``comm_broadcast`` / ``comm_poll``, and achieve ``team_sr = 1.0`` with ``violations = 0``.

3. Run Channel Impairment & Blackout Ablations
-----------------------------------------------

.. code-block:: bash

   # Simulate 30% packet loss and a 500 Byte/s token-bucket bandwidth cap
   python3 -m tasks.coop_grid_search.demo --channel impaired --drop-prob 0.3 --bandwidth-bps 500

   # Simulate total communication blackout (No-Comm baseline -> team_sr = 0.0)
   python3 -m tasks.coop_grid_search.demo --channel blackout

4. Run with a Live LLM Planner (Optional)
------------------------------------------

.. code-block:: bash

   export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
   export OPENAI_API_KEY="YOUR_API_KEY"

   conah-check-llm --planner api --model openai-chat:qwen3.8-max

   python3 -m tasks.coop_grid_search.demo \
     --planner api --model openai-chat:qwen3.8-max

5. Inspect Run Artifacts
-------------------------

Every run writes auditable artifacts under ``runs/step1-ipc-<timestamp>/``:

- ``summary.json``: Team success rate (``team_sr``), byte/drop telemetry, and per-robot harness receipt counts.
- ``comm_trace.jsonl``: Packet-by-packet log of ``comm_tx``, ``comm_rx``, and ``comm_drop`` events.
- ``robot_0/events.jsonl``, ``robot_1/events.jsonl``: Per-robot tool calls and ``HARNESS_RECEIPT`` traces.
