# 公共 harness、任务、平台：工具边界整理

2026-11-08。本轮在原有目录建立真实的工具调用边界，保留 HarnessVLN 导航策略、停止门限及原始任务。模型在 `10.249.42.188`，项目和仿真在 `192.168.31.225:/home/pyq/comm_harness`。

## 当前文件组织

```text
comm_harness/
  tools/
    contracts.py       公共调用、结果、回执结构
    registry.py        公共工具声明和注册
    executor.py        实现绑定、任务许可、参数检查、执行
    runtime.py         事件、观测证据、回执和诊断
  session/
    object_navigation.py  导航能力要求和停止规则
    harness_vln/
      evaluator.py     导航阶段编排
      runner.py        实验装配、日志和结果汇总
      ...              TODO、世界图、检索、导航状态
  planner/harness_vln/  模型适配、提示词、决策解析
  platforms/           平台注册，含 tools_factory；通用 geometry.py
platforms/
  hm3d_m3/
    tools.py           动作、深度读取、坐标投影的实现
    harness_vln/       感知、建图、FMM、全景类型和数据适配
    habitat/           仿真与评测扩展
  uav/                 预留
  ackermann/           预留
  usrp/                预留
```

没有增加顶层目录。公共协议和运行时从原导航工具子包抽出；过渡兼容入口已清理，新通用运行时默认使用空工具表。

## 公共 harness 的职责

`ToolExecutor` 检查工具已绑定、任务允许、必需参数存在，并应用提供的参数 schema。它围绕真实 handler 记录调用与结果；handler 抛出异常时闭合失败记录并继续抛出，不虚构成功动作回执。

实现返回 `ToolOutcome(value, trace, consumed_steps)`：`value` 是进程内结果，`trace` 是明确选择的日志字段，消耗步数用于生成回执。Habitat 私有目标指标和传感器数组不自动进入工具结果日志，也不自动发送给模型。

公共执行器不导入任务、Habitat 或平台包。新测试用一个模拟无线测量能力证明执行器可以绑定另一种能力；这不代表已经实现 USRP 驱动。

注册、参数及任务许可属于执行前阻断检查。原运行时的观测新鲜度等契约仍主要记录诊断，并非所有违规都会阻断。单工具超时尚未通用化；仍使用模型请求超时及 launcher 整轮期限。模型调用预算保留在客户端。

## 任务层的职责

`ObjectNavigationTask` 声明必须的平台能力和工具许可集合，缺少能力时在装配阶段失败。平台注册工具不自动赋予任务使用权限。

几何停止规则从 `evaluator.py` 移入 `session/object_navigation.py`：新鲜深度、投影目标回退、目标过期、深度稳定性和各自的距离门限均保留。任务通过注入的深度查询读取事实，不导入 Habitat，也不接受真实目标位置。

目标类别的视觉复核仍在 `planner/harness_vln/agent.py`；`evaluator.py` 组合视觉判断与几何门控。原预算自动停止的视觉覆盖行为保留。本轮没有修改成功条件来提高得分。

任务类型规则在 `session/object_navigation.py`，实例定义在 `examples/find_vase.json`；导航方法代码仍在 `session/harness_vln/` 和 `planner/harness_vln/`。推理层已移除对 Habitat logger 和具体可视化类的导入；使用标准 logging，由 runner 连接到实验日志。可视化实例仍由外部注入。

## 平台层的职责

HM3D 通过平台注册结构中的 `tools_factory` 提供 `HabitatTools`。当前七个已绑定能力为：

| 工具 | 实现职责 |
| --- | --- |
| `move`、`turn`、`action`、`stop` | 执行匹配的 Habitat 动作；工具名与动作不匹配时拒绝执行 |
| `observe_panorama` | 全景采集中的一次左转；完整全景仍由编排器连续调用 12 次组成 |
| `query_depth` | 读取目标框处的米制深度和局部标准差，不决定任务成功 |
| `project_target` | 利用像素、深度、位姿、内参调用已有坐标投影实现 |

深度转换及无效读数处理也放在平台模块，保持作者数值行为。投影以前在调用前写逻辑成功记录；现在执行器围绕真实调用记录结果或错误。停止检查的深度读取增加独立工具事件。

当前是进程内 Python 能力接口，允许传递已有传感器数组。尚未把这些接口直接作为跨网络或模型 function calling 的 JSON schema；以后开放给模型时需要观测 ID/图像句柄，不能直接暴露仿真对象。

## 实际调用与后续扩展

```text
任务选择动作
→ ToolExecutor 检查许可和参数
→ 已绑定的 HabitatTools 执行
→ 返回 ToolOutcome
→ 公共运行时保存回执和结果
→ 任务更新状态并继续决策
```

每轮新增 `tool_catalog.json`，列出七个真正绑定的平台 handler 和六项任务逻辑操作。规划、定位、回忆、停止复核及复合导航操作仍由任务编排器调用或记录，不代表十三项都能由模型自由调用。

新增平台时，把实现放在对应 `platforms/<name>/tools.py`，声明 `ToolSpec`、绑定 handler，注册 `tools_factory`。新增任务时，选择能力集合并实现上下文、进度和完成规则；公共执行器、协议与回执可复用。

尚未完成的部分：`evaluator.py` 中的环境生命周期、感知、地图和控制器仍依赖 HM3D；模型仍使用分阶段 JSON 决策；旧多机 V0 的 Toolkit/TeamSession 尚未与新执行器合并。无人机、小车、USRP 仍预留，通信仍是旧 V0 的理想网络。本轮不宣称任意任务或平台只换配置即可运行。

## 验证记录

- 原有组件检查：15 + 244 项通过。
- 新边界测试：8 项通过，覆盖独立导入、第二种模拟能力、任务许可、参数拒绝、异常回执、传感器事实与任务规则分离。
- 现有单机接入 8 项、Ollama 4 项、旧多机契约 9 项通过。
- 第一次实际运行 `hvl-tool-layers-01` 完成：与重构前 Qwen 的步数、路径、距离、调用数和 tokens 完全相同。该开发运行的模型控制台日志未接到 Habitat 的独立 logger，原始回复文件仍保留；已修复并再次运行最终版本。

最终版本 `hvl-tool-layers-02` 的任务仍为 episode `00849-a8BtkwhxdRV_01257`、agent 0、goal 1（vase），最大 300 步。

| 验证项 | 最终结果 |
| --- | --- |
| 实际步数 / 动作回执 | 112 / 112 |
| 工具调用 / 对应返回 | 211 / 211 |
| 违规 / 未闭合调用 / API 错误 | 0 / 0 / 0 |
| 模型请求 / 总 tokens | 14 / 32,467 |
| 路径 / 最终距目标 | 2.5000 m / 8.2755 m |
| success / oracle_success / SPL | 0 / 0 / 0 |
| runner 耗时 | 100.873 s |

与原 `hvl-qwen-vase-01` 逐项比较：全部指标、模型用量、导航统计，以及 112 个动作回执的动作顺序和 metadata 完全相同。新增一次真正的 `query_depth` 调用使工具总数由 210 变成 211。实际执行了 80 次坐标投影、96 次全景旋转、10 次前进、5 次普通转向及一次 STOP。最终版本的模型控制台回复已确认恢复。

审计通过，并核对了每个动作回执对应的平台绑定与任务许可。比较文件保存在 `runs/hvl-tool-layers-02/comparison.json`。这证明本例的重构保持了导航行为，不代表任务成功或导航性能提高；误认锅罐为花瓶的问题仍存在。累计通过 259 项组件检查和 29 项单元测试。

```bash
source /home/pyq/hm3d_m3_repro/activate.sh
cd /home/pyq/comm_harness
python -m unittest discover -s tests -p test_tool_layers.py -v
python -m comm_harness --method harness-vln \
  --config examples/harness_vln_ollama.json --run-dir runs/my-tool-layers
python tests/check_harness_vln_run.py runs/my-tool-layers
```

当前模型连接需要本机 SSH 桥，见 [Ollama 运行说明](HARNESS_VLN_OLLAMA_REPORT.md)。

后续清理已完成，任务实例、运行配置和类型规则的位置及最新验证见 [TASKS.md](TASKS.md)。
