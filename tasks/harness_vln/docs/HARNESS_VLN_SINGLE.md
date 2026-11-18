# HarnessVLN 单机接入

运行机器为 **192.168.31.225**，目录 `/home/pyq/comm_harness/`。本机 `code/` 为同步源码。此入口运行已迁入本工程的作者 HarnessVLN 导航组件，不调用原参考仓库的 Python 包；仅复用已有模型权重、数据和第三方 Python 环境。

## 当前实现的方法

1. 从 HM3D-M³ 原始 episode 中选择一个 agent 和一个 ON 类别目标，保持起点位置、朝向和该类别的全部 `reach_points`。
2. Habitat 提供 RGB、深度、位姿。全景通过实际旋转获取，旋转消耗步数。
3. `VLMReasoningAgent` 将图片和任务/TODO/历史/世界图检索结果发送给配置中的视觉模型（DeepSeek 或 Ollama Qwen）。模型返回 JSON，分别完成 TODO 更新、方向/回退决策、bbox 定位和停止复核。
4. GroundingDINO + RepViT-SAM 做语义检测/分割，深度投影建立地图，FusionMapPolicy/FMM 产生离散运动动作。
5. HarnessRuntime 记录工具调用、回执、观测证据、状态转换；TodoMemory 和世界图跨步骤保存历史。作者的部分契约属于诊断记录，不等于所有违规都会被硬性阻断。
6. 普通停止经过视觉复核及原版几何门控；预算自动停止按作者配置可能覆盖视觉否定，见下文边界。Habitat 最后独立计算 success、SPL、距目标距离和路径长度。模型声称找到目标或运行结束都不代表评测成功。

上游模型为 `deepseek-flash`，通过 OpenAI 兼容 Chat Completions 接口发送多模态消息；不是嵌套 Codex。这里沿用作者的分阶段 JSON 决策，不强行换成上一版 V0 的原生 function calling 循环。

另有 `examples/harness_vln_ollama.json`，接入 `10.249.42.188` 的 `qwen3.8:27b`，通过 Ollama 原生 `/api/chat` 保留图片和上下文参数。仿真机器和数据路径不变。两台服务器目前不能直接连通，启动需要本机 SSH 桥，详见 [Qwen 接入与测试报告](HARNESS_VLN_OLLAMA_REPORT.md)。该入口不读取或发送 DeepSeek 密钥。

模型能力已核对 [DeepSeek 官方表](https://api-docs.deepseek.com/quick_start/pricing/)（2026-11-08）：`deepseek-flash` 支持视觉，`deepseek-v4-pro` 不支持。框架会在启动及创建模型客户端时拒绝不支持视觉的 DeepSeek 型号，不能以 HTTP 请求成功来判断模型能看图。开发期误选 Pro 的运行已标记无效，详见联调报告。

## 启动

任务实例独立定义在 `examples/find_vase.json` / `find_radiator.json`，用 `--task` 选择；运行配置负责模型和服务器参数。完整说明见 [任务定义](TASKS.md)。

```bash
source /home/pyq/hm3d_m3_repro/activate.sh
cd /home/pyq/comm_harness

# 检查导入、数据、模型资产；不调用 DeepSeek，也不运行导航
python -m comm_harness --method harness-vln --preflight --run-dir runs/my-preflight

# 单机，原始 agent 0；goal 3 是 radiator，goal 1 是 vase
python -m comm_harness --method harness-vln --goal-index 1 --run-dir runs/my-vase

# 覆盖步数、模型请求预算、选择另一个原始 agent
python -m comm_harness --method harness-vln --agent-index 1 --goal-index 1 \
  --max-steps 300 --max-model-calls 60 --run-dir runs/my-agent1-vase

# 切换原数据集中的 episode 和目标，不改原始起点
python -m comm_harness --method harness-vln --episode-id 00827-BAbdmeyTvMZ_00362 \
  --agent-index 0 --goal-index 4 --run-dir runs/my-pillow
```

每次使用新的输出目录；已有目录拒绝覆盖。默认复用 `/home/pyq/hm3d_m3_repro/secrets.env` 的密钥，或读取 `DEEPSEEK_API_KEY`。凭据不写入配置和日志。`--method multi-v0` 保留之前多机试验的入口。

## 文件与产物

完整文件位置看 [逐文件索引](HARNESS_VLN_FILES.md)。建议按 `cli/harness_vln.py → runner.py → evaluator.py → agent.py → policy.py` 阅读。

工具调用已拆成公共 `tools/executor.py`、任务 `session/object_navigation.py` 和平台 `platforms/hm3d_m3/tools.py`，见 [三层边界](HARNESS_LAYERS.md)。导航主循环仍在 `evaluator.py`。

每轮 `runs/<name>/` 包含：

| 文件/目录 | 用途 |
| --- | --- |
| `config.json`、`resolved_config.yaml` | 用户配置和解析后的实际配置 |
| `task_definition.json` | 本轮有效任务定义，已应用命令行覆盖 |
| `task.json`、`single_episode.json.gz` | 来源任务说明与仿真器使用的单机 episode；后者包含仅供环境评测的目标位置 |
| `source_manifest.json`、`preflight.json` | 本轮源码哈希、资产检查、实际导入路径和显存限制 |
| `tool_catalog.json` | 实际绑定的平台工具、任务许可与仅记录的任务逻辑操作 |
| `grounding_dino_config.py` | 本轮生成的离线 BERT 路径配置 |
| `console.log`、`logs/` | 运行日志和单 worker 任务分配 |
| `debug_logs/` | 每步图片、模型提示词/回复、地图 |
| `rgb/` | 全景、轨迹图、工具与停止事件 JSONL |
| `stats/`、`result.json` | 原版统计及汇总结果 |
| `failure.json` | 异常/预算/超时信息；正常完成则不存在 |

## 本次适配与边界

- 重排内部导入路径，代码按职责迁入既有目录；原始与迁入文件的哈希可追溯。
- DeepSeek 和 Ollama 均禁用 thinking；JSON 决策阶段要求 JSON 输出。普通定位框按模型配置转换：DeepSeek 使用像素坐标，本次 Qwen 使用 0–1000 归一化坐标（`bbox_scale=1000`）。停止复核框保持作者要求的 0–1000 归一化坐标，不重复缩放。
- 修正定位提示词未传递 `planned_action` 的问题：接近终点时定位任务目标，避免因 TODO 已完成而改选门口。
- 修正全景旋转中途结束时仍使用旧评测指标的问题；runner 进一步核对最终指标步数与回执终止步数，发现不一致则报错。
- GroundingDINO 的 tokenizer 读取本地 BERT，兼容旧版只接受模型名称的工厂函数；只在本进程模型构造时适配，恢复原工厂，不改共享依赖或参考仓库。
- 保留作者 FMM 导航、停止门限与回退机制。未迁入可选 NavDP；本次没有使用它。
- 保留作者预算自动停止的视觉覆盖选项；因此 `budget_autostop` 是退出原因，不是已找到目标的承诺。该逻辑仍需后续单机实验改进。
- 默认最多 300 仿真步、60 次模型 HTTP 尝试、累计 250000 tokens 的请求前阈值；最后一次请求可能使 token 总量超过阈值。工作进程有 1200 秒期限；超时只清理本次进程组。
- PyTorch 分配器按可用显存设置上限，给渲染与其他 CUDA 开销预留空间；这不是整个 GPU 进程的绝对显存硬上限。
- 当前只实现和验证 M³ 的 **单 agent、单 ON 目标投影**。TN/IIN 明确拒绝，未退化成类别导航；未完成整个多机、多目标 M³ benchmark，也未声称复现论文总体成绩。
- 原版 VLN 提示词/部分 Habitat 配置随源码保留，尚未接入 R2R/RxR 数据和评测入口。`tasks.py` 只登记本次可用单机任务。

单机的记忆、工具、平台边界已经分开。多机时再决定共享什么记忆、谁推进联合动作以及如何模拟通信；本轮不增加协作策略。

## 检查

```bash
python tests/test_harness_vln_runtime.py
python tests/test_harness_vln_package.py
python -m unittest discover -s tests -p 'test_harness_vln_integration.py' -v
python -m unittest discover -s tests -p 'test_contracts.py' -v
python -m unittest discover -s tests -p 'test_ollama_adapter.py' -v
python -m unittest discover -s tests -p 'test_tool_layers.py' -v
python tests/check_harness_vln_run.py runs/my-single-run
```
