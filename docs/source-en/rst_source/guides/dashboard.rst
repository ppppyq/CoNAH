CLI, TUI & Live Dashboard
============================

CoNAH provides unified CLI, Terminal UI (``--tui``), and Web Live Monitor (``--dashboard``) interfaces backed by ``DashboardState``.

Launch the Live Dashboard
--------------------------

.. code-block:: bash

   pip install -e ".[ui]"
   conah --dashboard --dashboard-language en --dashboard-port 8091

Open ``http://127.0.0.1:8091`` in your browser to launch/stop runs, inspect live tool calls, receipts, camera observations, and compare multi-run metrics.

Launch the Terminal Console
----------------------------

.. code-block:: bash

   conah --tui
