CLI、TUI 与实时面板
======================

CoNAH 提供命令行批处理、终端交互控制台（TUI）以及 Web 实时监控面板（Dashboard）三种操作入口，三者底层共用同一套 ``DashboardState`` 状态机。

1. 启动 Web 实时监控面板（ ``--dashboard`` ）
---------------------------------------------

安装可选 UI 依赖后启动本地监控服务：

.. code-block:: bash

   pip install -e ".[ui]"
   conah --dashboard --dashboard-language zh-cn --dashboard-port 8091

浏览器打开 ``http://127.0.0.1:8091`` 即可：

- 选择任务与模型运行配置并一键下达执行或安全中断；
- 实时观察智能体工具调用序列、动作回执（ ``Receipt`` ）、视角画面与 Token 消耗；
- 只读回放 ``runs/`` 目录下的历史实验轨迹与多轮指标对比。

2. 启动终端交互控制台（ ``--tui`` ）
------------------------------------

在无图形界面的远程 SSH 服务器上，可使用终端控制台管理任务：

.. code-block:: bash

   conah --tui

常用交互指令：

- ``/tasks`` ：列出当前可用任务定义与模型运行配置；
- ``/check <task> <profile>`` ：启动前预检数据路径、Python 环境与模型配置；
- ``/run <task> <profile>`` ：启动受控工作进程；
- ``/stop`` ：安全终止当前拥有的工作进程组。
