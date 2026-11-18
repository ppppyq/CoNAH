# Ollama Qwen 单机联调记录

日期：2026-11-08。结论：`10.249.42.188` 上的 `qwen3.8:27b` 已接入现有 HarnessVLN 单机入口，图像输入、定位坐标转换、实际导航与回执审计均跑通；这次花瓶任务失败，不能据此宣称导航成功或复现论文成绩。

## 两台服务器分别做什么

| 位置 | 本次职责 |
| --- | --- |
| `pyq@192.168.31.225:/home/pyq/comm_harness` | HarnessVLN、Habitat、HM3D 数据、GroundingDINO/分割、建图和运动 |
| `pyq@10.249.42.188` | Ollama 推理；主机名 `uwb-Precision-7920-Tower`，RTX 3090 + RTX 3080 |
| 当前 Windows 本机 | SSH 桥；连接两台服务器，不运行导航决策 |

从仿真机无法直接访问模型机，本次实际请求路径是：

```text
192.168.31.225:127.0.0.1:11436
  → SSH → Windows → SSH → 10.249.42.188:127.0.0.1:11437
```

原模型服务 `11434` 在安装运行组件前启动过，日志显示启动时 GPU 发现失败，本轮调用仍走 CPU。新建独立 Ollama 服务 `11437` 后正确发现 CUDA GPU，并复用 `/data_sda/pyq/qwen38-models` 的权重。原服务和其他 GPU 进程保持运行；未修改系统服务或防火墙。仿真机原有 `11435` 被其他进程占用，所以本项目选用 `11436`。

本次结束时保留独立服务和 SSH 桥，方便继续试验；模型请求设置 `keep_alive=5m`，空闲后可卸载权重。桥依赖当前 Windows 进程，未安装开机自启。断开后按下文重建。它仅解决模型服务连接，不代表后续多机器人通信模型。

## 实际模型和适配

- Ollama：`0.40.14`；模型标签：`qwen3.8:27b`，本机元数据为 27.3B、Q4_K_M，权重约 17.74 GB。
- 本机模型 digest：`aaee06c39dcf2437cde036998d960e1fc1494b8191be7cc9657d01e509097813`。这里只记录机器实际安装内容，不将标签名推断为其他官方发布版本。
- `/api/show` 返回 vision、completion、tools、thinking 能力。另做真实图片测试，避免只凭接口成功判断视觉可用。
- 使用 [Ollama 原生 Chat API](https://docs.ollama.com/api/chat)：图片转为 `images` 数组；JSON 决策阶段设 `format=json`；`think=false`；`num_ctx=32768`；每次输出最多 2048 tokens；每次请求最多保留 8 张图片。
- Ollama 路径不读取 DeepSeek 密钥，请求无 Authorization 头，不会自动回退云模型。
- **坐标适配是必要改动**：该模型在本次探测中，即使被要求像素坐标，仍输出 0–1000 坐标。配置 `bbox_scale=1000` 后，普通定位提示词明确该单位，再转成图像像素供分割/深度投影。停止复核原本就是 0–1000 坐标，保持单独转换。

`runs/ollama-vision-probe-03/` 的测试图在实际场景图上叠加随机六位字符和已知矩形，字符未写入提示词。两次请求均识别正确；按配置解释定位框的 IoU 分别为 0.9411、0.9598，响应约 1.81、1.53 秒。这证明本次能看图及定位接口可用，不代表自然场景目标分类准确率。

前一次 `ollama-vision-probe-02` 正是用于发现像素坐标假设错误的诊断，保留记录但不作为导航成绩；最初 CPU 服务探测没有完成，不算导航运行。

## 本次真实任务与结果

配置：`examples/harness_vln_ollama.json`；产物：`runs/hvl-qwen-vase-01/`。

原始 episode 为 `00849-a8BtkwhxdRV_01257`，agent 0，goal 1（vase / ON）。保留原始起点、朝向和该类别全部三个可达目标点；最大 300 步。这里只运行 M³ 中一个 agent 的一个类别目标，不是完整多机多目标 episode。

| 指标 | 结果 |
| --- | ---: |
| Habitat success / oracle_success / SPL | 0 / 0 / 0 |
| 最终距有效目标 | 8.2755 m |
| 实际运动路径长度 | 2.5000 m |
| 实际步数（含转动和 STOP） | 112 |
| 模型请求 / API 错误 | 14 / 0 |
| 输入 / 输出 tokens | 29,694 / 2,773 |
| 总 tokens | 32,467 |
| runner 记录的耗时 | 128.881 s |
| 工具调用及对应返回 / 动作回执 | 210 / 112 |
| 审计违规 / 未闭合工具调用 | 0 / 0 |

机器人接近石台上的带把黑色锅罐，将它多次描述为花瓶。停止复核曾两次否定；其中一次明确说它是 cauldron 而非 vase，但后续规划仍沿用记忆中的“黑色花瓶”。第三次复核将该物体归为 vase/pot 并接受 STOP。此时新鲜深度约 0.750 m，通过作者的 2.5 m 停止门限；环境中距有效目标仍有 8.28 m。

因此，几何门控验证了错误候选物的距离，未纠正类别误认。当前主要问题是目标类别核验及错误记忆的修正，不能把该次失败归因于模型接口断开或没有传入图片。没有为让本任务通过而修改停止门限、标注或成功判定。

证据：`console.log` 的最后三次停止回复、`rgb/0_failed/events_r0_w1.jsonl` 的 `STOP_GATE_PASS` 与最终 STOP 回执，以及最终图像：

![Qwen 最终停在锅罐前，Habitat 判定失败](../runs/hvl-qwen-vase-01/rgb/0_failed/combined_step0112.png)

此前 Flash 在同一原始起点/花瓶任务也失败。但本次模型、历史图片上限（8 对 50）、token 计数方式不同，旧 Flash 运行还有已修复的终止指标记录问题；这些开发记录不能当成严格的模型优劣比较。

## 后续如何运行

当前服务和桥仍在运行时，只需在 **192.168.31.225** 执行，每次使用新的 run 名称：

```bash
source /home/pyq/hm3d_m3_repro/activate.sh
cd /home/pyq/comm_harness
python -m comm_harness --method harness-vln \
  --config examples/harness_vln_ollama.json \
  --run-dir runs/my-qwen-vase
```

只检查资源、模型视觉能力及连通性，可加 `--preflight`。配置中的 `127.0.0.1:11436` 是仿真机的转发端口，实际模型仍在 `10.249.42.188`。

如果独立模型服务已停止，在 **10.249.42.188** 执行（服务正在运行时不要重复启动）：

```bash
python3 /home/pyq/comm_harness_ollama/ollama_model_server.py start
```

该脚本源码保存在本项目 `examples/ollama_model_server.py`，部署副本在上面的路径；服务器日志在 `/home/pyq/comm_harness_ollama/serve.log`。

如果 SSH 桥已断，在 **Windows 项目总目录** 新开终端执行，按隐藏提示输入 SSH 密码。需要 Python 和 Paramiko，保持该终端运行：

```powershell
python code/examples/ollama_ssh_bridge.py --port 11436 --model-port 11437 `
  --stop-file code/runs/qwen-bridge.stop --state-file code/runs/qwen-bridge.json
```

若已有同名停止标记，选择新的 stop-file/state-file 名称。端口已占用时先检查是否已有本项目桥，勿终止其他进程。当前测试桥状态保存在项目根目录 `tmp/ollama-bridge-03.json`；需要停止它时创建 `tmp/ollama-bridge-03.stop`。自行启动的桥可 Ctrl-C 退出。停止独立模型服务则在模型机运行同一脚本的 `stop` 子命令，它核验 PID 所有权，不停止原 `11434` 服务。

DeepSeek 原配置仍保留；后续使用 Qwen 请显式指定上面的 `--config`。CLI 默认方法仍为早期 `multi-v0`，所以 `--method harness-vln` 也需要保留。

## 验证和文件归属

4 项 Ollama 适配测试通过：图片顺序/JSON/关闭思考/上下文/用量、拒绝不支持视觉或非内联图像、拒绝不完整回复、实际 agent 普通定位坐标转换及 DeepSeek 像素路径。8 项现有单机接入测试通过。真实运行审计通过：210 个工具调用全部闭合、112 个回执连续、终止唯一、最终指标步数一致。

```bash
python -m unittest discover -s tests -p 'test_ollama_adapter.py' -v
python -m unittest discover -s tests -p 'test_harness_vln_integration.py' -v
python tests/check_harness_vln_run.py runs/hvl-qwen-vase-01
```

新模型客户端放在 `comm_harness/planner/harness_vln/ollama_client.py`，启动参数由现有 `cli` 传入，能力检查在现有 `session/harness_vln/runner.py`；连接脚本、探测和配置放 `examples/`，测试放 `tests/`。未新增顶层框架目录。完整映射见 [文件索引](HARNESS_VLN_FILES.md)。
