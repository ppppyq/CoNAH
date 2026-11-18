Configure Planners
====================

Select the agentic reasoning backend via ``--planner {api,codex,flash}``:

- ``api`` (``ApiAgentLoop``): Native multi-turn tool-calling loop with sliding-window compaction and ``Budget`` call/token enforcement. Supports OpenAI, Qwen (DashScope), DeepSeek, Claude, vLLM, and local Ollama.
- ``codex`` (``CodexPlanner``): Exposes the robot's ``Toolkit`` over an in-process Streamable HTTP MCP server to the OpenAI Codex SDK.
- ``flash`` (``FlashPlanner``): Deterministic policy runner for sub-second integration testing without LLM API calls.

Connectivity Check (``conah-check-llm``)
-----------------------------------------

Always verify model credentials before launching heavy simulator sessions:

.. code-block:: bash

   export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
   export OPENAI_API_KEY="sk-xxx"
   conah-check-llm --planner api --model openai-chat:qwen3.8-max
