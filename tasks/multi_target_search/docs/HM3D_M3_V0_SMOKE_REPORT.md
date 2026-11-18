# HM3D-M³ V0 实现与小规模联调报告

日期：2026-11-08（Asia/Hong_Kong）。实际运行机器：**192.168.31.225**，用户 `pyq`。工程主目录：**`/home/pyq/comm_harness/`**。本机 `H:/Communication-Native Agent Harnesses for Multi-Robot Embodied Intelligence/code/` 为同步源码副本。

**结论：第一版框架已实现，三个机器人可以通过 DeepSeek 工具调用驱动导航并交换候选证据；工程检查通过。此次任务的成功目标为 0/5，尚不能证明导航方法有效。**

## 实现范围

- 原生 DeepSeek `deepseek-flash`：图片输入、工具调用、工具回传、错误记录及请求/token 预算。
- 每机器人独立对话上下文、局部地图、任务视图、验证记录和 HarnessVLN 世界图/检索记忆。
- 8 类工具：observe、recall、navigate、verify_goal、declare_goal、read_team_state、publish、request_assignment。
- RGB-D 建图和 scikit-fmm 距离场导航；前沿探索、历史地点回退、12 个原子 tick 的全景扫描。
- 理想消息传输：序列化隔离、收件人、序号、TTL、去重、交付时间、应用层字节统计。
- 确定性初始分配和带版本的任务领取/释放；候选目标包含图像、观测时间与深度估计位置，经过通信层交付。
- 独立环境进程加载 HM3D-M³，多机器人动作只由 TeamSession 联合推进。
- ON/TN/IIN 使用分别编写的验证条件；声明需要当前节点、当前 tick 的有效验证。私有评测器结束后评分，不能将结果反馈给仍在执行的策略。

未复制数据，也未修改两个参考仓库。运行时直接加载 HarnessVLN 的 `world_graph.py` 和 `retrieval.py`，使用原 M³ 多机器人 simulator 及 `benchmark.metrics`。本版没有移植 HarnessVLN 的完整检测/分割模型、原 evaluator 和全部导航策略。

## 实际任务和环境

- Episode：`00849-a8BtkwhxdRV_01257`，保留原始 3 个机器人起点、5 个目标。
- 目标：TN plant、ON vase、TN mirror、ON radiator、IIN stair。
- Python 3.8.20，已有 m3 环境，RTX 3090。
- 实测传感器：640×480，HFOV 79°，相机高 0.88 m；原始归一化 depth 已转换为米。
- 机器人配置：高度 0.88 m、半径 0.18 m；前进 0.25 m、转向 30°。
- navmesh 使用现有文件加载，没有重新生成；正式比较前仍需核对其生成参数与 oracle 路径是否一致。
- 理想通信、准确定位、公共坐标系、模型请求期间冻结仿真时间。

## 测试与运行结果

9 项单元测试全部通过，覆盖：消息复制隔离和去重、TTL、任务版本与确认、私有目标字段过滤、缺失 IIN 图拒绝、工具参数校验、过期/跨节点证据拒绝、当前证据验证与声明、工具异常回执。

真实 Habitat 环境契约检查通过：HOLD 不移动机器人但推进逻辑 tick；非空声明能够进入 geodesic 计分分支；冒用其他节点观测被拒绝；评分后不能恢复执行。该检查使用人工测试声明，不是导航成绩。

| Run 目录 | 模式 | 联合 tick | API 请求 | Usage tokens | 墙钟秒数 | 结束原因 | 成功目标 |
| --- | --- | ---: | ---: | ---: | ---: | --- | ---: |
| `runs/scripted-check-01` | 环境诊断，无模型 | 24 | 0 | 0 | 4.05 | 步数上限 | 不作为模型成绩 |
| `runs/deepseek-smoke-01` | DeepSeek 初次联调 | 45 | 30 | 104,673 | 47.56 | 模型预算 | 0/5 |
| `runs/deepseek-smoke-02` | 分模态提示、候选分享 | 68 | 26 | 100,550 | 39.78 | 模型预算 | 0/5 |
| `runs/deepseek-smoke-03` | 最终版本联调 | 84 | 38 | 147,312 | 62.99 | 预设 84 tick 上限 | 0/5 |

三轮模型请求合计 94 次、352,535 usage tokens。前两轮的 token 上限为 100,000；第三轮调整为 150,000，请求上限仍为 40。token 阈值在请求前检查，并发中的请求可能导致有限超出；不是严格的服务端计费额度限制。

不同轮次修改过实现/配置，属于开发联调记录，不能将它们当作受控性能消融。每个 run 保存了对应配置与源文件哈希。

最终一轮详细结果：

- 3 个机器人执行 84 次联合步进，产生 252 条节点观测回执。
- 路程分别为 7.500、7.058、5.249 m。
- 21 次 navigate、5 次 observe、6 次 verify_goal；6 次目标验证均未通过，未提交目标完成声明。
- 无模型接口错误、无节点决策异常；这不表示没有导航失败，世界图中仍记录了 NO_PROGRESS 和 STOP_REJECTED。
- 发送 27 条源消息：1 条任务快照、21 条状态、5 条候选证据；累计 49 次接收交付。最后一个 tick 产生的 3 条状态消息在结束时仍待交付，已单独记录。
- 源端应用层字节数 374,298；按接收链路累计 749,259 bytes。包含实际编码图像附件，不包含无线物理层开销。
- SR=0、Progress=0、SRAT=0、MSPL=0、false_positives=0。零误报源于未提交完成声明，不能解释为验证器准确率已获证实。
- 日志一致性检查通过：联合 tick 连续、各节点观测完整、消息无重复交付、路径长度可由轨迹重算、通信字节统计一致、token 统计一致、声明与评测计分分离。

## 已定位的限制

1. 目标验证仍偏保守。多次已看到植物/楼梯等候选，但模型自报置信度不足或深度距离不满足门槛；未通过的候选没有被记为成功。置信度阈值需要独立校准，不能为这一个 episode 临时降低。
2. 导航目前是轻量 RGB-D 前沿/FMM 控制器，尚未整合完整的语义检测、分割和更强的目标导向探索。部分导航命令产生 NO_PROGRESS，真实任务效果需要继续改进。
3. 世界图目前主要记录地点、语义摘要和失败；候选物体保存在验证记录和通信消息中，尚未合并成持久的 object graph 节点。
4. 任务板的版本领取/释放路径已通过测试，但这次模型运行没有主动触发这些工具；不能据此声称动态分配已经带来实验收益。
5. 没有跑 mini100 批量评测，没有比较通信消融，也没有注入真实无线延迟、带宽或丢包。

下一步优先改进目标候选定位、观察位置选择、失败后的探索策略和验证校准。在任务能力建立后，再比较消息内容/时机和非理想网络条件。

## 复查与再次运行

在 `pyq@192.168.31.225` 登录后：

```bash
source /home/pyq/hm3d_m3_repro/activate.sh
cd /home/pyq/comm_harness
python -m unittest discover -s tests -v
PYTHONPATH=. python tests/probe_environment.py
python tests/check_run.py runs/deepseek-smoke-03

# 会调用真实 API；目录必须是一个新的 run 名称。
python -m comm_harness --max-ticks 84 --run-dir runs/deepseek-next
```

主要结果：

- `runs/deepseek-smoke-03/result.json`：全部结果、逐节点摘要、轨迹和通信统计。
- `runs/deepseek-smoke-03/events.jsonl`：模型工具调用、候选消息、动作与观测回执。
- `runs/deepseek-smoke-03/trajectories.png`：三个机器人的真实轨迹，仅使用自身位姿，没有叠加目标真值。
- `runs/deepseek-smoke-03/frames/`：关键决策处的 RGB 和已观测局部地图。
- `runs/deepseek-smoke-03/environment.log`：底层环境日志。

密钥仅从已有环境变量/服务器 secrets.env 读取，没有写入本项目源码或结果。
