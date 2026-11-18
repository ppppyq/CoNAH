# Web 与 CLI

两个入口使用同一个 `comm_harness/dashboard/state.py` 读取任务、检查配置、启动现有 CLI 子进程、查看运行记录。任务实例仍定义在 `examples/*.json`，任务规则与执行流程仍由 `session/`、`planner/` 和平台实现负责。

```text
comm_harness/
├── cli/
│   ├── __init__.py       # 保持原有 main / read_key 导入兼容
│   ├── main.py           # 普通任务、--dashboard、--tui 的统一入口
│   ├── tui.py            # 终端输入与命令展示
│   ├── config.py         # 任务与模型配置装配
│   └── harness_vln.py    # 现有单机 worker 启动与回收
└── dashboard/
    ├── __init__.py
    ├── server.py         # HTTP 参数验证、API 和静态文件
    ├── state.py          # 两个界面共用的任务目录、运行状态与进程管理
    ├── index.html        # 页面结构
    └── static/
        ├── dashboard.css
        └── dashboard.js  # API 请求、图像/日志展示、3 秒状态刷新
```

目录分工与 RPent 的 CLI / Dashboard 思路相近。这一版为本项目实现，不依赖 RPent 包，也不会把上游的机器人定义、工具或规划逻辑复制进界面。

## 安装与启动

UI 使用 Python 3.10+ 的独立环境。第一次安装：

```bash
cd /home/pyq/comm_harness
python3 -m venv .venv-ui
.venv-ui/bin/python -m pip install -r requirements-ui.txt
```

Web（服务器局域网地址，供同一可信网络访问）：

```bash
cd /home/pyq/comm_harness
.venv-ui/bin/python -m comm_harness --dashboard \
  --dashboard-host 192.168.31.225 --dashboard-port 8091 \
  --worker-python /home/pyq/hm3d_m3_repro/envs/m3/bin/python
```

打开 <http://192.168.31.225:8091>。默认参数只监听 `127.0.0.1`；这版没有账户登录功能。界面环境与 Habitat Python 3.8 环境分开，`--worker-python` 指向实际运行任务的解释器。

CLI 交互：

```bash
cd /home/pyq/comm_harness
.venv-ui/bin/python -m comm_harness --tui \
  --worker-python /home/pyq/hm3d_m3_repro/envs/m3/bin/python
```

```text
/tasks
/profiles
/check find_vase.json harness_vln_ollama.json
/runs
/show hvl-tool-layers-02
/monitor hvl-tool-layers-02
/probe find_vase.json harness_vln_ollama.json
/compare hvl-tool-layers-02 scripted-check-01
/run find_vase.json harness_vln_ollama.json
/stop <本终端启动的运行编号>
/exit
```

`/check` 只检查任务、资源路径、模型配置和密钥是否存在，不测试模型网络连通性或 GPU。`/run` 和 Web 的“启动任务”执行真实任务，可能调用模型 API；查看页面、记录或执行配置检查不会调用模型。

## 当前范围与状态含义

- 新任务控制只支持已有的单机 HarnessVLN / HM3D Object Navigation；原有多机 CLI 入口保持可用，尚未接入界面控制。
- Web 展示现有运行的评测、模型用量、保存的观测/轨迹图、工具事件、任务与工具目录。日志截取末尾约 48 KB，事件最多展示最近 160 条，图像列表最多加载 400 项；不是实时相机流。
- “运行结束”表示运行产出了结果；任务是否成功单独读取 `metrics.success`。历史的 112 步 vase 测试会显示“未成功”。
- 本版本没有自然语言对话引导、运行中改任务、直接机器人操作或视频播放器。任务文件和配置文件通过现有目录添加；界面不写任务内容。
- 同一项目通过进程锁限制 Web/TUI 同时启动一个任务。只允许停止当前界面会话启动的进程；其他会话或重启前的未结束记录标记为“状态待确认”，不能根据历史 PID 直接停止。
- 退出 CLI 或停止 Web 服务时，会请求结束该会话启动的任务。普通命令行手动启动的其他任务不受此锁管理。
- 密钥来自已有环境变量或 `--env-file`，不通过浏览器输入。接口屏蔽配置中的密钥字段与常见日志凭据，并限制图像访问到所选运行目录。

## 验证

```bash
# UI 边界、真实隔离子进程启停与 HTTP 检查；httpx 只用于测试。
.venv-ui/bin/python -m pip install httpx
.venv-ui/bin/python -m unittest discover -s tests -p test_interfaces.py -v
.venv-ui/bin/python -m unittest discover -s tests -p test_task_config.py -v
```

任务启动/停止测试使用临时目录中的隔离进程，不请求模型、不加载仿真器。完整导航效果沿用已有记录，本轮没有重新运行模型实验。

2026-11-08 实际验证：10 项界面/进程/HTTP 测试、6 项原任务配置测试通过；原任务配置测试也在 Habitat 的 Python 3.8 环境通过。真实终端已验证 `/tasks`、`/check`、`/runs` 和 `/exit`；Web 已读取历史评测、图像和事件，检查前后均为 25 条运行记录。前端 DOM 测试通过初始化、配置检查按钮、切换任务后使检查失效、历史失败标记和结果页签。浏览器自动操作被本机设置禁用，未做截图视觉验收。

当前后台 Web 的日志与 PID 保存在 `runs/.control/dashboard.log`、`runs/.control/dashboard.pid`；接口和终端验证记录为同目录的 `ui-verification.json`，前端逻辑验证记录为 `frontend-verification.json`。

## 运行监控补充（2026-11-08）

Web 与 CLI 共享以下新增能力，不增加任务专用的顶层目录：

| 能力 | 数据来源与含义 |
| --- | --- |
| 阶段与节点 | 新单机 worker 的实际阶段事件和每 2 秒心跳；8 秒未收到心跳标记超时，任务结束与心跳超时分开显示。心跳不能证明控制循环未卡住。 |
| 模型请求 | 每次客户端请求的开始/结束、模型、尝试编号、图像数、耗时、错误、API/JSON 重试、服务端已报告 Token 和配置预算。耗时包含网络和服务端等待，不拆成无法测得的推理/传输耗时。 |
| 决策关联 | 请求文本中的观测/回执编号 → 公开回复解析出的决策 → 最终采用的计划 → 工具执行与回执。`decision_id` 表示当前采用计划的上下文，不代表之后每一步动作都由该条回复直接生成。 |
| 服务状态 | 任务、资源、仿真器、感知、地图等组件实际初始化记录。手动“检测连接与 GPU”仅建立 TCP 连接并查询 nvidia-smi，不加载或请求模型推理。端口可连接不保证 API 或模型可用。 |
| 通信 | 旧多机理想传输的发送方/接收方、消息种类、序号、应用层序列化字节、投递/过期/重复记录；有序号时统计乱序，`age_ticks` 表示投递时的信息年龄。新单机导航尚未接入通信。 |
| 实验对比 | 勾选 2–6 个已有运行，对照成功、步数、路程、SPL、总耗时、模型用量、通信源字节，并明确任务、模型、预算、种子等已知差异与缺失条件；支持 JSON 导出。 |

来源文件：`comm_harness/telemetry.py`；视图投影：`comm_harness/dashboard/monitoring.py`；页面：`dashboard/index.html` 与 `dashboard/static/monitor.js`；命令：`comm_harness/cli/tui.py`。任务实例位置与启动路径不变。

`monitor.jsonl` 每条事件即时落盘，页面每 3 秒读取；每次增量读取最多 4 MiB，积压时会显示尚未读完。页面保留最近 80 次模型请求、40 个决策、80 条消息、240 条监控事件，导出是当前视图快照，完整审计记录留在运行目录中。已有 episode 事件格式及其 flush 策略不变。

当前未采集无线 RSSI/SINR、真实吞吐、空口丢包、时钟同步后的单向时延，也未记录消息实际进入模型决策的时间；这些字段保持未接入。旧实验没有逐请求计时、阶段或心跳时不会补造；API 调用成功、程序完成和任务评测成功分别展示。没有新增自然语言改任务或直接控制真机的入口。

```bash
# 在已有任务 Python 环境测试监控；使用本地替身响应，不调用远程模型。
/home/pyq/hm3d_m3_repro/envs/m3/bin/python -m unittest discover -s tests -p test_monitoring.py -v
```

本次验证：监控 9 项、接口 10 项、模型/平台集成 9 项、公共契约 9 项、工具边界 8 项测试通过，原 HarnessRuntime 的 15 项检查通过。真实 Web API 和终端 `/monitor`、`/compare` 验证通过；前端 DOM 检查覆盖历史缺失数据、理想通信、实验对比、请求异常、文本安全展示和刷新时保留展开项。运行记录仍为 25 条，未启动模型或导航实验。浏览器自动操作设置仍关闭，未做截图视觉验收；下一次真实导航会生成新的逐请求监控记录。
