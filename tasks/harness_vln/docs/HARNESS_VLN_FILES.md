# HarnessVLN 原文件到本工程的对应表

源版本：本机已下载并核对的 HarnessVLN 发布副本。完整源文件逐项列在下表；Python 导入路径已经改为本工程命名空间。模型权重和场景继续复用服务器现有数据。

## 先看这条调用链

`comm_harness/__main__.py` → `cli/__init__.py` → `cli/harness_vln.py` → `session/harness_vln/runner.py` → `session/harness_vln/evaluator.py`。

`runner.py` 配置单个原始起点/目标并创建环境；`evaluator.py` 执行全景观察 → TODO/方向决策 → bbox 定位 → 深度投影/建图 → FMM 行走 → 重新观察或回退 → 停止复核。模型决策在 `planner`，状态/记忆在 `session`，工具契约在 `tools`，仿真/感知/运动在具体 `platforms`。

沿用现有四个一级目录，不新增顶层框架。各职责目录中的 `harness_vln/` 是这套方法的实现子包，不是一份独立参考仓库。`communication/` 暂不参与单机执行。

## 本次新增的接入文件

| 文件 | 层级与职责 |
| --- | --- |
| `comm_harness/cli/__init__.py` | 现有 CLI；用 `--method harness-vln` 选择单机方法 |
| `comm_harness/cli/harness_vln.py` | CLI 层：读取运行配置、注入凭据、启动/清理本次工作进程、收集输出 |
| `comm_harness/session/harness_vln/runner.py` | Session 层：检查权重、配置设备、注册环境、运行一轮、写评测结果 |
| `platforms/hm3d_m3/harness_vln/dataset.py` | 平台层：将 M³ 的一个原始 agent 和一个 ON 目标转换为单机 ObjectNav episode |
| `comm_harness/planner/harness_vln/__init__.py` | Planner 子包标识 |
| `platforms/hm3d_m3/harness_vln/__init__.py` | 平台方法子包标识 |
| `examples/find_vase.json` / `find_radiator.json` | 独立任务实例：原始 episode、agent、目标编号、类别校验和步数预算 |
| `comm_harness/cli/config.py` | 解析任务文件、校验类型、合并运行配置及命令行覆盖 |
| `examples/harness_vln_single.json` | 数据位置、DeepSeek 与资源预算；默认引用 radiator 任务 |
| `examples/harness_vln_ollama.json` | Ollama 连接与推理预算；默认引用 vase 任务 |
| `comm_harness/planner/harness_vln/ollama_client.py` | Planner 层：原生 Ollama 图片/JSON 请求、关闭 thinking、能力与用量检查 |
| `examples/ollama_model_server.py` | 部署辅助：模型机上启动/停止独立 GPU Ollama 进程，复用已有权重 |
| `examples/ollama_ssh_bridge.py` | 部署辅助：在本机连接两台服务器，将模型端口转发给仿真机；不属于机器人通信策略 |
| `examples/probe_ollama_vision.py` | 联调辅助：用图片中的随机字符与定位框验证模型确实能看图及坐标单位 |
| `examples/harness_vln_objectnav.yaml` | 作者导航参数；由 runner 覆盖本机路径、步数和输出目录 |
| `examples/harness_vln_source_manifest.json` | 每个迁移文件的来源及原始/当前 SHA-256 |
| `examples/HARNESS_VLN_FILES.md` | 本逐文件索引 |
| `examples/HARNESS_VLN_SINGLE.md` | 方法、运行方式、适配说明和单机范围 |
| `examples/HARNESS_VLN_SINGLE_REPORT.md` | 真实联调结果与失败记录 |
| `examples/HARNESS_VLN_OLLAMA_REPORT.md` | Qwen 的接入方法、复现命令和真实失败记录 |
| `tests/test_harness_vln_integration.py` | 原始起点/目标集保持、模态拒绝、DeepSeek 请求和预算检查 |
| `tests/check_harness_vln_run.py` | 审计真实运行的回执连续性、工具闭合、终止次数及最终步数一致性 |
| `tests/test_ollama_adapter.py` | 图片传递、请求参数、能力拒绝、完整回复及普通定位框坐标转换检查 |
| `HARNESS_VLN_LICENSE` | 迁入代码的原许可证 |
| `HARNESS_VLN_THIRD_PARTY.md` | 原第三方说明和本次迁移说明 |

`__pycache__/` 是 Python 自动生成的缓存，不属于工程设计；`runs/` 是运行产物，不存源码或模型权重。

## 工具三层整理后的实现位置

下方原始源码表已更新到清理后的实际位置；旧兼容入口已移除。当前实现如下：

| 当前文件 | 职责 |
| --- | --- |
| `comm_harness/tools/contracts.py` | 从导航子包抽出的公共调用/返回/回执结构 |
| `comm_harness/tools/registry.py` | 公共工具声明和注册，不预置任务或平台 |
| `comm_harness/tools/runtime.py` | 公共回执、观测证据与事件记录；默认空工具表 |
| `comm_harness/tools/executor.py` | 绑定真实实现，检查任务许可与参数，执行并记录结果 |
| `comm_harness/session/object_navigation.py` | 导航能力要求、许可集合和停止规则 |
| `platforms/hm3d_m3/tools.py` | HM3D 动作、深度测量、坐标投影的真实实现 |
| `comm_harness/platforms/__init__.py` | 原平台注册结构增加 `tools_factory` |
| `platforms/hm3d_m3/__init__.py` | 注册工具工厂，旧多机工厂按需导入 |
| `tests/test_tool_layers.py` | 三层边界、执行失败、参数拒绝、许可和任务判定测试 |
| `examples/HARNESS_LAYERS.md` | 当前边界、未完成部分和真实回归结果 |

`runner.py` 每轮生成 `tool_catalog.json`，列出实际绑定工具的所有者与任务许可。详细调用关系见 [三层说明](HARNESS_LAYERS.md)。

## 迁入的作者源码

| 原文件 | 本工程文件 |
| --- | --- |
| `vlnce_baselines/agent.py` | `comm_harness/planner/harness_vln/agent.py` |
| `vlnce_baselines/config/default.py` | `platforms/hm3d_m3/harness_vln/config.py` |
| `vlnce_baselines/env/env_utils.py` | `platforms/hm3d_m3/harness_vln/env_utils.py` |
| `vlnce_baselines/env/environments.py` | `platforms/hm3d_m3/harness_vln/environments.py` |
| `vlnce_baselines/harness/__init__.py` | `comm_harness/session/harness_vln/__init__.py` |
| `vlnce_baselines/harness/contracts.py` | `comm_harness/tools/contracts.py` |
| `vlnce_baselines/harness/geometry.py` | `comm_harness/platforms/geometry.py` |
| `vlnce_baselines/harness/panorama.py` | `platforms/hm3d_m3/harness_vln/panorama.py` |
| `vlnce_baselines/harness/registry.py` | `comm_harness/tools/registry.py` |
| `vlnce_baselines/harness/retrieval.py` | `comm_harness/session/harness_vln/retrieval.py` |
| `vlnce_baselines/harness/runtime.py` | `comm_harness/tools/runtime.py` |
| `vlnce_baselines/harness/state.py` | `comm_harness/session/harness_vln/state.py` |
| `vlnce_baselines/harness/todo.py` | `comm_harness/session/harness_vln/todo.py` |
| `vlnce_baselines/harness/trace.py` | `comm_harness/session/harness_vln/trace.py` |
| `vlnce_baselines/harness/world_graph.py` | `comm_harness/session/harness_vln/world_graph.py` |
| `vlnce_baselines/harness_runtime.py` | 旧兼容入口已移除，使用公共 contracts/runtime |
| `vlnce_baselines/map/mapping.py` | `platforms/hm3d_m3/harness_vln/mapping.py` |
| `vlnce_baselines/map/RepViTSAM/repvit.py` | `platforms/hm3d_m3/harness_vln/repvit.py` |
| `vlnce_baselines/map/RepViTSAM/setup_repvit_sam.py` | `platforms/hm3d_m3/harness_vln/setup_repvit_sam.py` |
| `vlnce_baselines/map/semantic_prediction.py` | `platforms/hm3d_m3/harness_vln/semantic_prediction.py` |
| `vlnce_baselines/model_config.py` | `comm_harness/planner/harness_vln/model_config.py` |
| `vlnce_baselines/models/fmm_planner.py` | `platforms/hm3d_m3/harness_vln/fmm_planner.py` |
| `vlnce_baselines/models/Policy.py` | `platforms/hm3d_m3/harness_vln/policy.py` |
| `vlnce_baselines/models/super_pixel_policy.py` | `platforms/hm3d_m3/harness_vln/super_pixel_policy.py` |
| `vlnce_baselines/models/superpixel_waypoint_selector.py` | `platforms/hm3d_m3/harness_vln/superpixel_waypoint_selector.py` |
| `vlnce_baselines/objnav_categories.py` | `comm_harness/planner/harness_vln/categories.py` |
| `vlnce_baselines/prompts/__init__.py` | `comm_harness/planner/harness_vln/prompts.py` |
| `vlnce_baselines/prompts/prompts_objnav.py` | `comm_harness/planner/harness_vln/prompts_objnav.py` |
| `vlnce_baselines/prompts/prompts_vln.py` | `comm_harness/planner/harness_vln/prompts_vln.py` |
| `vlnce_baselines/tasks.py` | `platforms/hm3d_m3/harness_vln/tasks.py` |
| `vlnce_baselines/usage.py` | `comm_harness/planner/harness_vln/usage.py` |
| `vlnce_baselines/utils/acyclic_enforcer.py` | `platforms/hm3d_m3/harness_vln/acyclic_enforcer.py` |
| `vlnce_baselines/utils/api.py` | `comm_harness/planner/harness_vln/model_client.py` |
| `vlnce_baselines/utils/constant.py` | `platforms/hm3d_m3/harness_vln/constant.py` |
| `vlnce_baselines/utils/data_utils.py` | `platforms/hm3d_m3/harness_vln/data_utils.py` |
| `vlnce_baselines/utils/depth_utils.py` | `platforms/hm3d_m3/harness_vln/depth_utils.py` |
| `vlnce_baselines/utils/map_utils.py` | `platforms/hm3d_m3/harness_vln/map_utils.py` |
| `vlnce_baselines/utils/misc.py` | `platforms/hm3d_m3/harness_vln/misc.py` |
| `vlnce_baselines/utils/pose.py` | `platforms/hm3d_m3/harness_vln/pose.py` |
| `vlnce_baselines/utils/rotation_utils.py` | `platforms/hm3d_m3/harness_vln/rotation_utils.py` |
| `vlnce_baselines/utils/stats.py` | `comm_harness/session/harness_vln/stats.py` |
| `vlnce_baselines/utils/visualization.py` | `platforms/hm3d_m3/harness_vln/visualization.py` |
| `vlnce_baselines/ZS_Evaluator_mp.py` | `comm_harness/session/harness_vln/evaluator.py` |
| `habitat_extensions/__init__.py` | `platforms/hm3d_m3/habitat/__init__.py` |
| `habitat_extensions/config/__init__.py` | `platforms/hm3d_m3/habitat/config/__init__.py` |
| `habitat_extensions/config/default.py` | `platforms/hm3d_m3/habitat/config/default.py` |
| `habitat_extensions/habitat_simulator.py` | `platforms/hm3d_m3/habitat/habitat_simulator.py` |
| `habitat_extensions/maps.py` | `platforms/hm3d_m3/habitat/maps.py` |
| `habitat_extensions/measures.py` | `platforms/hm3d_m3/habitat/measures.py` |
| `habitat_extensions/pose_utils.py` | `platforms/hm3d_m3/habitat/pose_utils.py` |
| `habitat_extensions/sensors.py` | `platforms/hm3d_m3/habitat/sensors.py` |
| `habitat_extensions/task.py` | `platforms/hm3d_m3/habitat/task.py` |
| `habitat_extensions/config/objectnav_mp3d.yaml` | `platforms/hm3d_m3/habitat/config/objectnav_mp3d.yaml` |
| `habitat_extensions/config/objectnav_ovon.yaml` | `platforms/hm3d_m3/habitat/config/objectnav_ovon.yaml` |
| `habitat_extensions/config/objectnav_v2.yaml` | `platforms/hm3d_m3/habitat/config/objectnav_v2.yaml` |
| `habitat_extensions/config/r2r.yaml` | `platforms/hm3d_m3/habitat/config/r2r.yaml` |
| `habitat_extensions/config/rxr.yaml` | `platforms/hm3d_m3/habitat/config/rxr.yaml` |
| `vlnce_baselines/config/objectnav_v2.yaml` | `examples/harness_vln_objectnav.yaml` |
| `LICENSE` | `HARNESS_VLN_LICENSE` |
| `THIRD_PARTY.md` | `HARNESS_VLN_THIRD_PARTY.md` |
| `tests/test_harness_package.py` | `tests/test_harness_vln_package.py` |
| `tests/test_harness_runtime.py` | `tests/test_harness_vln_runtime.py` |

任务实例在 `examples/find_vase.json` 和 `find_radiator.json`；装配在 `comm_harness/cli/config.py`，验证在 `tests/test_task_config.py`。见 [任务说明](TASKS.md)。
