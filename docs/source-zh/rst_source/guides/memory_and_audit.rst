单机记忆与回执审计
===================

每台 CoNAH 机器人内部都独立运行一套轻量级、无需大模型调用的空间拓扑记忆与运行审计系统（位于 ``comm_harness/memory/`` 与 ``comm_harness/tools/runtime.py`` ）。

1. 本地拓扑世界图（ ``WorldGraph`` ）与 IDF 检索
------------------------------------------------

- **三类节点**： ``Place`` （已探索房间/路点）、 ``Object`` （已定位物体及多表述别名合并）、 ``Failure`` （某子目标下的探索失败或停止门控拦截记录）。
- **零 API 成本本地召回（ ``recall()`` ）**：综合 IDF 词频余弦相似度、时间新鲜度、显着性与有界失败惩罚，并沿拓扑邻接边做 1-hop 扩展，直接返回带可回溯编号（ ``backtrack_id`` ）的记忆摘要。
- **子目标状态机（ ``TodoMemory`` ）**：维护 ``PENDING`` / ``ACTIVE`` / ``COMPLETED`` / ``BLOCKED`` 四种状态及完成证据校验。

2. 可验证回执与零违规审计（ ``HarnessRuntime`` ）
-------------------------------------------------

``HarnessRuntime`` 在底层监控每一次工具调用，强制维护以下工程不变量：

- 每次工具调用必须成对记录 ``HARNESS_TOOL_CALL`` 与 ``HARNESS_TOOL_RESULT`` （ ``open_tool_calls == 0`` ）；
- 每次物理运动（ ``WORLD_MUTATING`` ）与跨机无线发包（ ``NETWORK_TX`` ）必须签发唯一回执 ``HARNESS_RECEIPT`` ；
- 若机器人在发生物理位移后未重新观测就尝试终止或盲目规划，审计器会明确记录违规事件（ ``HARNESS_VIOLATION`` ），确保实验结果可追溯、可复现。
