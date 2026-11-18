# HarnessVLN 单机迁移与联调记录

日期：2026-11-08。实际机器：**192.168.31.225**，实际工程：`/home/pyq/comm_harness/`。

## 结论

工程迁移、真实环境运行和闭环审计已完成；**导航任务成功尚未验证通过**。6 次使用视觉模型 `deepseek-flash` 的开发运行均未成功。它们跨越了不同修复版本，且一轮含旧版末端指标问题，不能把这些开发记录当作正式 benchmark 成绩。暂不扩展多机协作。

这次运行的是迁入当前工程的作者 HarnessVLN 组件，包括 VLMReasoningAgent、TODO、世界图/检索、HarnessRuntime、GroundingDINO/RepViT-SAM、语义建图、FusionMapPolicy/FMM、回退和停止复核。运行证明不是简单调用原参考仓库入口；`preflight.json` 记录了实际导入路径，原 `vlnce_baselines` / `habitat_extensions` Python 包未加载。权重、场景和已有第三方环境继续复用。

范围是 **一个原始机器人起点 + 一个原始 ON 类别目标**。保留其位置、朝向和全部类别 `reach_points`，没有向策略提供目标坐标，没有改终点或放宽 Habitat 成功阈值。TN/IIN、多目标和多机器人尚未接入本方法。可选 NavDP 未使用。

## 工程验证

| 检查 | 结果 |
| --- | --- |
| 作者 HarnessRuntime 检查 | 15/15 |
| 作者 Harness 包检查 | 244/244 |
| 新增接入测试 | 8/8 |
| 原工程契约测试 | 9/9 |
| 全景中途终止的真实场景检查 | `hvl-terminal-check-01`，实际 5 步 = 指标 5 步，0 模型请求 |
| 最新完整运行回执审计 | `hvl-vase-agent2-01`：348 次工具调用全部闭合，275 个连续动作回执，1 次终止，0 API 错误、0 Harness 诊断违规 |
| 模型资产 | 7 个权重/分词文件大小与 SHA-256 检查通过 |
| 凭据 | 源码无 API key 字面量，继续从现有环境文件读取 |

环境沿用 Python 3.8.20、PyTorch 2.4.1、Habitat-Sim 0.1.7、RTX 3090。正常运行的 PyTorch 峰值 reserved 约 1.71 GiB；该数字不包含全部渲染/CUDA 开销。

## 实际导航运行

除 pillow 外的五轮来自 `00849-a8BtkwhxdRV_01257`；pillow 来自 `00827-BAbdmeyTvMZ_00362`。下表的 agent/goal 为原始数据中的零起始索引。

| 运行目录（位于 `runs/`） | agent / goal | 目标 | 实际步数 | 路径 m | 结束距目标 m | success | 模型请求 / tokens |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `hvl-radiator-03` | 0 / 3 | radiator | 46 | 1.250 | 5.251 | 0 | 8 / 14886 |
| `hvl-vase-01` | 0 / 1 | vase | 300† | 19.037† | 6.442† | 0† | 29 / 134653 |
| `hvl-vase-agent1-01` | 1 / 1 | vase | 275 | 4.520 | 9.465 | 0 | 31 / 162638 |
| `hvl-radiator-agent1-01` | 1 / 3 | radiator | 119 | 1.675 | 6.569 | 0 | 14 / 28591 |
| `hvl-pillow-01` | 0 / 4 | pillow | 156 | 0.563 | 16.483 | 0 | 22 / 45075 |
| `hvl-vase-agent2-01` | 2 / 1 | vase | 275 | 12.587 | 6.062 | 0 | 30 / 136041 |

† `hvl-vase-01` 的动作流实际执行 300 步，但当时作者 evaluator 在全景旋转中结束后使用旧 `infos`，原 `result.json` 只记了 278 步；带 † 的距离、路径和 success 是当时原始文件值，未篡改成“修复后结果”。现已修正三个全景终止分支，并用真实 5 步测试和回归测试验证。新 runner 会拒绝最终步数与动作流不一致的结果。

每轮均保留配置、源码哈希、模型图文请求/回复、地图、轨迹和事件。原始完整资料位于服务器；本机 `code/runs/<name>/` 同步了汇总、配置、日志、事件和部分图像，便于审阅。

## 本次修复

1. **离线感知模型加载**：明确 BERT 本地路径，兼容已安装 GroundingDINO 的模型工厂。早期 `hvl-radiator-01/02` 是模型初始化失败，无模型 API 导航调用；修复后完整启动。
2. **终点状态没有进入定位提示词**：作者 `query_llm` 传入 `planned_action`，但原 prompt 未使用，模型可能在 TODO 完成后改选门口。现明确 STOP 阶段继续定位任务目标。
3. **JSON/坐标约定**：DeepSeek JSON 阶段使用 JSON 输出；定位框显式要求像素坐标。停止复核保持独立的 0–1000 归一化框约定。
4. **全景中途终止指标过期**：传播最后一步环境信息；最终结果对齐事件步数。
5. **视觉能力校验**：在启动和创建模型客户端时拒绝不具备视觉的 DeepSeek 型号，避免“API 返回成功但没有看图”的静默失效。

## 无效的 Pro 尝试

开发时误把 `/models` 中可选且能返回响应的 `deepseek-v4-pro` 当成视觉后端，进行了 `hvl-vase-pro-01`、`hvl-refrigerator-pro-01` 两轮，以及中途取消的 `hvl-vase-agent2-pro-01`。随后核对 [DeepSeek 官方模型能力表](https://api-docs.deepseek.com/quick_start/pricing/) 和 [视觉接口文档](https://api-docs.deepseek.com/guides/vision/)：**Flash 支持视觉，V4 Pro 不支持**。

三轮均已保存 `VALIDITY.json` 标记为无效；取消的进程只终止了本任务自己的进程组，并保存取消原因。这些记录不能用于视觉导航成功率、oracle success 或模型优劣比较。默认配置始终为 `deepseek-flash`；现在启动时会阻止上述误用。

## 尚未解决的单机问题

- radiator/pillow 出现模型视觉确认与评测有效目标位置不一致的情况；需要区分物体误识别、标注实例覆盖与停止位置，而不能用真值目标坐标帮助策略“导航成功”。
- 花瓶任务出现重复探索、目标超时与回退，尚未有效搜索到目标。最长一轮路径约 19 m，并不能证明搜索覆盖充分。
- 当前保留作者预算自动停止逻辑：在配置允许时，可以用地图距离覆盖视觉否定。这解释了部分 `budget_autostop` 退出；它是终止机制，不是任务成功证据。真实成功始终看 `metrics.success`。
- 论文总体性能、完整 HM3D-M³、多模态 TN/IIN、多机器人和真实通信均未验证。

下一步应继续修复单机的探索与终点验证，并建立有成功案例的固定回归任务集；当前结果不支持直接宣称多机扩展基础已经成熟。

## 复查入口

```bash
source /home/pyq/hm3d_m3_repro/activate.sh
cd /home/pyq/comm_harness
python tests/check_harness_vln_run.py runs/hvl-vase-agent2-01
python -m comm_harness --method harness-vln --agent-index 2 --goal-index 1 --run-dir runs/my-vase-agent2
```

文件位置和阅读顺序见 [逐文件索引](HARNESS_VLN_FILES.md)，运行与方法说明见 [HARNESS_VLN_SINGLE.md](HARNESS_VLN_SINGLE.md)。
