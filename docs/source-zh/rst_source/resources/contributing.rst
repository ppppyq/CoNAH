贡献指南
=========

欢迎提交 Issue、Pull Request、新机器人平台适配器或无线信道模型！

开发环境与测试检查
--------------------

提交代码前，请在仓库根目录运行核心与跨进程单元测试：

.. code-block:: bash

   # 1. 运行 Step-1 通信底座与双进程本机 IPC 测试（无需 GPU）
   python3 -m unittest tests.test_step1_comm_foundation -v

   # 2. 运行核心契约、工具三层边界与规划器测试
   python3 -m unittest tests.test_contracts tests.test_planner tests.test_tool_layers tests.test_task_config -v

文档编写与本地预览
--------------------

CoNAH 的中英文文档分别位于 ``docs/source-zh/`` 与 ``docs/source-en/`` ，请保持双语页面相对路径完全一致，并使用严格模式（ ``-W`` ）确保零警告通过：

.. code-block:: bash

   pip install -r docs/requirements.txt
   cd docs
   sphinx-build -W --keep-going source-zh build/html-zh
   sphinx-build -W --keep-going source-en build/html-en
   # 本地热更新预览中文文档：
   bash autobuild.sh zh 8001
