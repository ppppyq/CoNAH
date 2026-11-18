# 使用 Comm Harness 运行 HM3D-M³

本文对应 2026-11-08 的工程实现，操作目标为 **192.168.31.225**。以已有 Linux 环境为基础，不包含从零安装 Habitat、下载完整数据集的步骤。主线是迁入本框架的 HarnessVLN 单机器人目标导航；末尾另列早期多机 V0 的入口。

## 1. 运行前认识几个目录

| 内容 | 服务器位置 |
| --- | --- |
| 本框架 | `/home/pyq/comm_harness/` |
| Habitat 任务 Python | `/home/pyq/hm3d_m3_repro/envs/m3/bin/python` |
| 环境激活脚本 | `/home/pyq/hm3d_m3_repro/activate.sh` |
| HM3D-M³ 原工程 | `/home/pyq/hm3d_m3_repro/hm3d-m3-code/` |
| 场景网格与语义数据 | `/home/pyq/hm3d_m3_repro/hm3d-m3-code/sim-code/data/scene_datasets/hm3d-1.0/hm3d_v0.2/` |
| 感知模型资源 | `/home/pyq/hm3d_m3_repro/harnessvln-code/data/grounded_sam/` |
| 资源校验清单 | `/home/pyq/hm3d_m3_repro/harness-assets.json` |
| Web / TUI Python | `/home/pyq/comm_harness/.venv-ui/bin/python` |
| 每次实验输出 | `/home/pyq/comm_harness/runs/<运行编号>/` |

当前部署为 100 个验证场景网格，其中 36 个有本任务需要的语义标注，并非完整训练集。任务所选 episode 必须能对应到已部署场景及其语义资源。

## 2. 登录并进入已有运行环境

在本机终端登录：

```bash
ssh pyq@192.168.31.225
```

以下命令都在这台服务器执行：

```bash
source /home/pyq/hm3d_m3_repro/activate.sh
cd /home/pyq/comm_harness
python -c "import sys; print(sys.executable)"
nvidia-smi
```

Python 应指向 `envs/m3/bin/python`。当前单机感知实现依赖 CUDA；运行器会检查可用显存并限制分配。UI 使用单独的 Python 3.10 环境，实际导航继续由 Habitat 的 Python 3.8 环境执行。

## 3. 选择任务与模型配置

任务定义放在工程根目录的 `examples/`，不写进平台工具。现有花瓶任务是 `examples/find_vase.json`：

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

`agent_index` 与 `goal_index` 是原始 episode 中从 0 开始的索引。这里运行 agent 0 寻找 goal 1 对应的花瓶类别；不是把 M³ 完整多机器人、多目标任务全部跑完。`expected_category` 用于检查目标是否选对。

| 运行配置 | 当前接入方式 | 默认引用任务 |
| --- | --- | --- |
| `examples/harness_vln_single.json` | 已配置的 DeepSeek API，模型名为 `deepseek-flash` | `find_radiator.json` |
| `examples/harness_vln_ollama.json` | Ollama，模型标签为 `qwen3.8:27b` | `find_vase.json` |

运行配置还定义 `episodes`、`scenes`、`assets`、资源清单、超时、显存额度和请求预算。**比较不同模型时显式传入同一个 `--task`**，避免默认配置分别运行 radiator 和 vase。

DeepSeek 路径读取环境变量 `DEEPSEEK_API_KEY`，或默认密钥文件 `/home/pyq/hm3d_m3_repro/secrets.env`。自定义文件可用 `--env-file /绝对路径/secrets.env`。不要把实际密钥写进任务 JSON、文档或版本库。该视觉方法要求支持图像输入的模型；接口可返回文本不代表具备视觉能力。

Ollama 配置目前指向仿真机上的 `http://127.0.0.1:11436`，它依赖到 `10.249.42.188` 模型机的 SSH 桥，并非仿真机本地安装的模型服务。桥断开时需先恢复，详见 [Ollama 联调与连接说明](../../examples/HARNESS_VLN_OLLAMA_REPORT.md)。Ollama 路径不转发 DeepSeek 密钥。

## 4. 先检查，再运行单机

### 4.1 完整预检查

在激活的 Habitat 环境中执行：

```bash
python -m comm_harness --method harness-vln \
  --config examples/harness_vln_single.json \
  --task examples/find_vase.json \
  --preflight
```

不指定 `--run-dir` 时会自动生成带时间戳的目录。预检查会装配任务、检查资源完整性、导入运行依赖，并保存 `preflight.json`；不会进入导航循环。Ollama 配置还会查询模型能力元数据。DeepSeek 的该步骤不执行真实推理，也不能证明远端鉴权或图像理解一定成功；实际 CUDA 可用性与显存分配检查在正式运行阶段执行。

### 4.2 正式运行花瓶任务

```bash
python -m comm_harness --method harness-vln \
  --config examples/harness_vln_single.json \
  --task examples/find_vase.json
```

执行此命令会调用真实模型并运行仿真。需要限定小规模联调预算时：

```bash
python -m comm_harness --method harness-vln \
  --config examples/harness_vln_single.json \
  --task examples/find_vase.json \
  --max-steps 80 --max-model-calls 12
```

小预算适合检查链路，预算耗尽可能产生失败记录，不能将其当作完整任务成绩。`max_total_tokens`、模型请求超时与总运行超时在配置 JSON 中调整。

如果要使用 Ollama，在连接恢复且模型服务可用后运行同一个任务：

```bash
python -m comm_harness --method harness-vln \
  --config examples/harness_vln_ollama.json \
  --task examples/find_vase.json
```

可加 `--run-dir runs/自定义名称`。运行目录必须尚不存在；预检查和正式运行也必须使用不同目录。终端按 Ctrl+C 会触发 launcher 回收其启动的 worker。

**务必指定 `--method harness-vln`**：普通 CLI 的默认方法仍是早期 `multi-v0`。

## 5. 从 Web 或交互终端启动

现有控制台地址：<http://192.168.31.225:8091>。已经能打开时直接使用，不重复启动占用同一端口。

若 Web 服务尚未运行，在服务器另开终端：

```bash
cd /home/pyq/comm_harness
.venv-ui/bin/python -m comm_harness --dashboard \
  --dashboard-host 192.168.31.225 --dashboard-port 8091 \
  --worker-python /home/pyq/hm3d_m3_repro/envs/m3/bin/python
```

页面操作：选择 `find_vase` → 选择模型配置 → 检查配置 → 按需检测连接与 GPU → 启动任务。配置检查只检查路径、配置和凭据是否存在；连接检测只检查 TCP 端口及 GPU 快照，都不能替代真实模型调用。

交互终端入口：

```bash
cd /home/pyq/comm_harness
.venv-ui/bin/python -m comm_harness --tui \
  --worker-python /home/pyq/hm3d_m3_repro/envs/m3/bin/python
```

进入后可依次输入：

```text
/tasks
/profiles
/check find_vase.json harness_vln_single.json
/run find_vase.json harness_vln_single.json
/runs
/show <运行编号>
/monitor <运行编号>
/stop <本终端启动的运行编号>
/exit
```

Web/TUI 共用进程锁，限制它们同时启动一个任务；普通 CLI 手动启动不受这个锁管理，使用时自行避免重复占用 GPU。停止按钮或 `/stop` 只能停止当前界面会话启动的任务。退出 TUI 或关闭 Web 服务会请求停止其仍在运行的任务。

## 6. 查看结果与监控

运行开始时终端会打印 `run_dir`。单机输出主要有：

```text
runs/<运行编号>/
├── config.json             # 本次装配后的配置
├── task_definition.json    # 原任务定义（通过任务文件启动时）
├── task.json               # 转换为单机后的任务信息
├── source_manifest.json    # 运行时源码哈希
├── preflight.json          # 资源、代码来源及运行前检查信息
├── tool_catalog.json       # 本任务绑定的工具
├── console.log             # worker 标准输出和错误
├── monitor.jsonl           # 新版阶段、请求、心跳、工具关联事件
├── rgb/                    # 保存的图像、轨迹与 episode 事件
├── stats/                  # 评测、导航与模型用量统计
├── result.json             # 正常结束后的评测与审计汇总
└── failure.json            # 异常/中断时可能产生；不保证所有文件都存在
```

在 Web 中选中运行记录，可以查看图像、工具回执、模型请求、预算、组件初始化状态和节点心跳；勾选 2–6 条记录可以对比实验条件和结果。`/monitor` 读取相同监控数据。

判断导航是否成功看 `result.json` 的 `metrics.success`，同时检查 `distance_to_goal`、`steps_taken`、`path_length`、`spl`。`completed: true` 仅表示程序产出结果。历史 `hvl-tool-layers-02` 是 112 步、任务成功率 0、距有效目标约 8.28 米：它证明运行链路接通，不能证明任务完成。

新监控记录每条即时落盘，页面约每 3 秒刷新。旧实验缺少逐请求计时或心跳时显示未记录。单机 HarnessVLN 没有节点间消息传输，因此通信面板显示未接入；不能把模型服务连接时间当成无线网络时延。

## 7. 数据和调用如何经过 Harness

```text
examples/任务.json + examples/模型配置.json
  → comm_harness/cli/config.py              装配任务与配置
  → comm_harness/cli/harness_vln.py         启动独立 worker
  → comm_harness/session/harness_vln/runner.py
      → platforms/hm3d_m3/harness_vln/dataset.py 选取 agent / goal
      → session/harness_vln/evaluator.py        推进导航闭环
          → planner/harness_vln/agent.py        组织图像、记忆与提示
          → planner/harness_vln/model_client.py 调用上游模型
          → comm_harness/tools/                执行工具并记录回执
          → platforms/hm3d_m3/                 感知、建图、运动、仿真
  → runs/运行编号/                            记录与评测
```

新任务优先在 `examples/` 新建任务 JSON，再用 `--task` 指定。更换任务类型需要相应任务规则与能力支持，不能仅更改 `type` 字符串。平台工具放在本目录，公共模型接口和工具执行机制留在 `comm_harness/`。

## 8. 常见问题

| 现象 | 检查位置与处理 |
| --- | --- |
| 找不到 Habitat 或模块导入失败 | 确认使用 `envs/m3/bin/python`，并在 `/home/pyq/comm_harness` 下执行；UI 的 Python 不用来直接启动普通导航命令。 |
| 场景/语义文件缺失 | 检查 `episodes`、`scenes` 和所选 episode；当前只有部分语义场景，换任务前核对资源。 |
| 模型资源校验失败 | 检查 `assets` 和 `asset_manifest` 对应文件，依据 `console.log` 修复缺失或不匹配资源。 |
| API 密钥或鉴权报错 | 检查密钥环境变量、`--env-file` 和模型地址；配置检查通过不代表鉴权已通过。 |
| Ollama 连接拒绝/超时 | 先核对 11436 SSH 桥和模型机服务，再查看请求超时；不要在仿真机假设这个端口对应本地 Ollama。 |
| 显存不足/EGL 初始化失败 | 检查 GPU 占用和 `console.log`；已有 launcher 会尝试匹配本机驱动的 EGL 库。 |
| 目录已存在 | 换一个 `--run-dir`，或省略该参数使用自动时间戳目录。 |
| 8091 已被占用 | 先访问现有页面；需要独立实例时选不同端口。 |
| 运行结束但没找到目标 | 查看 `metrics.success`、最终图像和停止验证；不要仅依据 `completed` 或模型声称成功。 |

## 9. 早期多机 V0（独立入口）

这条路线保留原 M³ 多机器人、多目标任务与理想通信，但使用早期轻量导航实现；不是把上面的完整 HarnessVLN 单机循环直接扩成多机。

激活同一个 Habitat 环境后，可先运行不调用模型的环境诊断：

```bash
cd /home/pyq/comm_harness
python -m comm_harness --method multi-v0 \
  --config examples/hm3d_m3_smoke.json \
  --mode scripted --max-ticks 24
```

需要真正调用模型的多机实验时：

```bash
python -m comm_harness --method multi-v0 \
  --config examples/hm3d_m3_smoke.json \
  --mode deepseek --max-ticks 120 --max-model-calls 40
```

该路线使用自己的配置、模型接口与 `events.jsonl`；`--task`、`--max-steps` 属于单机路线。通信目前只有 `ideal`，应用层消息量可查看，尚未实现真实无线链路的带宽、丢包和时延。完整 benchmark 评测与多机扩展需要另行设计，不由上述 smoke 命令自动完成。

更多背景：[任务定义](../../examples/TASKS.md)、[单机实现](../../examples/HARNESS_VLN_SINGLE.md)、[界面和监控](../../examples/INTERFACES.md)、[多机 V0 设计](../../examples/HM3D_M3_V0_DESIGN.md)。本文仅整理启动方式，编写过程中未启动新的模型或导航实验。
