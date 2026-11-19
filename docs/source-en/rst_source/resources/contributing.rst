Contributing
=============

Contributions of new robot platforms, wireless channel simulators, collaborative protocols, and documentation improvements are warmly welcome.

Running Tests
--------------

.. code-block:: bash

   python3 -m unittest tests.test_step1_comm_foundation -v
   python3 -m unittest tests.test_contracts tests.test_planner tests.test_tool_layers tests.test_task_config -v

Building Documentation Locally
-------------------------------

Keep ``docs/source-en/`` and ``docs/source-zh/`` at matching relative paths and verify strict zero-warning builds before opening a PR:

.. code-block:: bash

   pip install -r docs/requirements.txt
   cd docs
   sphinx-build -W --keep-going source-en build/html-en
   sphinx-build -W --keep-going source-zh build/html-zh
