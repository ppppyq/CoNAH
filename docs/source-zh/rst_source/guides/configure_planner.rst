规划器配置
============

CoNAH 通过统一的 ``--planner`` 参数选择每台机器人的高层决策后端：

.. code-block:: text

   --planner {api,codex,flash}

三种规划器共享完全一致的 ``Toolkit`` 工具表与 ``HarnessRuntime`` 回执审计逻辑：

.. list-table::
   :header-rows: 1
   :widths: 18 42 40

   * - ``--planner``
     - 实现机制
     - 适用场景
   * - ``api``
     - 原生多轮 Tool-Calling 循环（ ``ApiAgentLoop`` ），内置历史对话滑动窗口裁剪与 Token 预算控制（ ``Budget`` ）。
     - 直接连接通义千问 Qwen、OpenAI、DeepSeek、Claude 或本地 Ollama / vLLM。
   * - ``codex``
     - 在进程内启动本地 Streamable HTTP MCP 服务，将 ``Toolkit`` 暴露给 Codex SDK 执行。
     - 复用 Codex CLI/SDK 推理循环完成长程多步规划。
   * - ``flash``
     - 确定性策略函数执行器（ ``FlashPlanner`` ），无需外部大模型请求。
     - 秒级验证平台工具、通信信道与多进程闭环正确性。

配置模型提供商与连通性检查
----------------------------

使用 ``--planner api`` 时，可通过 ``<provider>:<model>`` 前缀指定服务商，并在启动重型仿真前运行 ``conah-check-llm`` 验证连通性：

.. code-block:: bash

   # 1. OpenAI 兼容端点（如阿里云百炼 DashScope Qwen3.8-Max）
   export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
   export OPENAI_API_KEY="sk-xxx"
   conah-check-llm --planner api --model openai-chat:qwen3.8-max

   # 2. DeepSeek 官方端点
   export DEEPSEEK_API_KEY="sk-xxx"
   conah-check-llm --planner api --model deepseek:deepseek-flash

   # 3. 本地 Ollama 多模态模型（无需 API Key）
   conah-check-llm --planner api --model ollama:qwen3.8:27b --base-url http://127.0.0.1:11434
