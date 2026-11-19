快速开始
============

本页带你从克隆安装开始，在 **无需 GPU 和大型三维仿真器** 的条件下，30 秒内运行一个真实的本机多进程（ ``127.0.0.1`` ）双机器人协同搜索任务，并完成无线信道损伤消融对比。

准备工作
------------

- Linux / macOS，Python 3.8 及以上（推荐 Python 3.10 / 3.11）。
- 若需运行真实大模型在线推理（可选），准备任意兼容 OpenAI Chat Completions 的 API Key（如通义千问 Qwen、OpenAI、DeepSeek 或本地 Ollama）。

1. 安装 CoNAH
---------------

克隆仓库并安装轻量级核心包：

.. code-block:: bash

   git clone https://github.com/ppppyq/CoNAH.git
   cd CoNAH
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .

2. 运行第一个本机跨进程多机协同任务
--------------------------------------

运行 ``tasks.coop_grid_search.demo`` 。该脚本会在后台拉起两个独立 RPC 服务（ ``GridEnvServer`` 环境服务与 ``LocalhostCommServer`` 信道仿真服务），并启动两个完全独立的操作系统子进程（ ``robot_0`` 与 ``robot_1`` ）：

- ``robot_0`` 出生在 ``(0, 0)`` ，负责寻找 ``mirror`` （位于 ``(3, 4)`` ），但出门顺路就能看到队友的目标 ``vase`` （位于 ``(1, 0)`` ）；
- ``robot_1`` 出生在 ``(4, 4)`` ，负责寻找 ``vase`` ，出门顺路能看到 ``mirror`` 。

执行理想信道运行命令：

.. code-block:: bash

   python3 -m tasks.coop_grid_search.demo --channel ideal

终端会输出完整的评测与通信审计 JSON：

- ``evaluation.team_sr`` 为 ``1.0`` （两台机器人互换顺路发现的目标坐标，仅用 6 步直达各自目标）；
- ``communication.source_bytes`` 精确统计了全队累计发包字节数；
- ``workers.robot_0.harness.violations`` 与 ``robot_1`` 均为 ``0`` 。

3. 运行无线信道受限与断联消融实验
------------------------------------

保持智能体策略完全不变，仅切换底层信道参数观察通信对协同成功率的因果影响：

.. code-block:: bash

   # 模拟 30% 随机丢包率 + 500 Byte/s 令牌桶带宽上限
   python3 -m tasks.coop_grid_search.demo --channel impaired --drop-prob 0.3 --bandwidth-bps 500

   # 模拟通信完全中断（No-Comm 消融基线 -> team_sr 降为 0.0）
   python3 -m tasks.coop_grid_search.demo --channel blackout

4. 切换为真实大模型在线规划（可选）
--------------------------------------

先用 ``conah-check-llm`` 验证大模型 API 连通性，通过后再驱动两个机器人进程实时自主调用通信工具：

.. code-block:: bash

   export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
   export OPENAI_API_KEY="YOUR_API_KEY"

   conah-check-llm --planner api --model openai-chat:qwen3.8-max

   python3 -m tasks.coop_grid_search.demo \
     --planner api --model openai-chat:qwen3.8-max

5. 查看运行产物
------------------

每次运行均会在 ``runs/step1-ipc-<timestamp>/`` 下生成结构化审计记录：

- ``summary.json`` ：团队评测成功率（ ``team_sr`` ）、信道收发/丢包汇总与每机回执统计；
- ``comm_trace.jsonl`` ：逐包记录每一次无线发送（ ``comm_tx`` ）、送达（ ``comm_rx`` ）与信道丢包原因（ ``comm_drop`` ）；
- ``robot_0/events.jsonl`` 、 ``robot_1/events.jsonl`` ：每台机器人独立的工具调用与 ``HARNESS_RECEIPT`` 审计流。
