# 任务定义写在哪里

当前“找花瓶”任务定义在 **`examples/find_vase.json`**。服务器路径是 `/home/pyq/comm_harness/examples/find_vase.json`；本机文件在项目的 `code/examples/`。

```json
{
  "name": "find_vase",
  "type": "object_navigation",
  "platform": "hm3d_m3",
  "episode_id": "00849-a8BtkwhxdRV_01257",
  "agent_index": 0,
  "goal_index": 1,
  "expected_category": "vase",
  "max_steps": 300,
  "seed": 1123
}
```

| 内容 | 定义位置 |
| --- | --- |
| 本次找什么、哪个原始任务、哪个 agent、目标编号、步数和种子 | `examples/find_vase.json` |
| 物体导航需要什么能力、允许什么工具、怎样应用停止规则 | `comm_harness/session/object_navigation.py` |
| 使用 Qwen 还是 DeepSeek、模型地址、机器上的资产路径和资源上限 | `examples/harness_vln_ollama.json` / `harness_vln_single.json` |
| 采用 HarnessVLN 的观察、推理、回退和运动编排方法 | `comm_harness/session/harness_vln/` 与 `planner/harness_vln/` |
| 设备或仿真真正能执行的能力 | `platforms/<platform>/tools.py` |

`type` 是任务类型，`method` 是求解方法，`platform` 是执行平台，三者不再用同一个名字表达。当前支持 `object_navigation` + `harness-vln` + `hm3d_m3`，未实现的新组合会明确拒绝。

## 运行同一个任务

在 192.168.31.225 上执行：

```bash
source /home/pyq/hm3d_m3_repro/activate.sh
cd /home/pyq/comm_harness

# Qwen
python -m comm_harness --method harness-vln \
  --config examples/harness_vln_ollama.json \
  --task examples/find_vase.json --run-dir runs/my-vase-qwen

# 同一个任务，改用 DeepSeek
python -m comm_harness --method harness-vln \
  --config examples/harness_vln_single.json \
  --task examples/find_vase.json --run-dir runs/my-vase-deepseek
```

运行配置中的 `task_file` 是默认任务，相对运行配置文件所在目录解析；显式 `--task` 优先，相对当前工作目录解析。Qwen 默认仍为花瓶；DeepSeek 原配置默认 radiator，现在引用 `find_radiator.json`，保持原默认行为。Qwen 连接仍需要已配置的 SSH 桥。

任务字段不重复写在模型/机器配置中，出现重复会报错。运行产物 `task_definition.json` 保存有效任务定义，`config.json` 保存合并配置，`task.json` 保存数据集实际解析出的任务信息。

## 新增同类任务

复制任务 JSON 为另一个名字，修改 `name`、`episode_id`、`agent_index`、`goal_index` 和 `expected_category`。不新增 tools 子目录，也不复制一套 harness。

`goal_index` 是原始 HM3D-M³ episode 中目标的下标，`expected_category` 是校验，不会改写数据集标签。仅把 `vase` 改成 `chair` 而未选择对应的真实目标，会在创建环境前报错。起点和朝向继续由原始 agent 定义，不在任务文件中伪造。

现有 `--episode-id`、`--goal-index`、`--agent-index`、`--max-steps` 参数仍可覆盖任务。若命令行改变 episode 或目标编号，原文件的类别校验会移除，以实际选中目标为准；有效任务快照记录覆盖后的选择。仅修改 agent 或预算不会移除类别校验。

新增任务类型（例如无线测绘）时，需实现相应的任务规则并接入启动分派，必要时补充平台能力；当前不能只把 `type` 字符串改为新名称就运行。公共工具机制始终留在 `comm_harness/tools/`，不会按任务类型增加子目录。

## 本次清理

- 已移除公共工具层内的旧导航兼容包。
- 通用坐标类型移到 `comm_harness/platforms/geometry.py`。
- 带有 HM3D 四向采样约定的全景类型移到 `platforms/hm3d_m3/harness_vln/panorama.py`。
- 物体导航规则移到 `comm_harness/session/object_navigation.py`。
- 源码索引和来源哈希随新位置更新；历史运行产物保持原样。

验证：259 项组件检查和 36 项单元测试通过，包括两种模型配置共用任务、路径解析、覆盖参数、类别一致性和不支持任务的拒绝。

本次启动检查 `runs/hvl-task-cleanup-01` 已完成任务解析、平台能力绑定、场景与模型权重加载，并进入初始导航决策。任务快照对应原始 agent 0 / goal 1（vase），保留起点与有效目标集合。首个模型请求超过 240 秒后开始重试；本轮已主动停止该运行，保留 `failure.json` 和日志，没有完成导航，也没有本次重构后的完整轨迹对比结论。按当前工作安排，后续先理清架构，暂不继续模型联调。
