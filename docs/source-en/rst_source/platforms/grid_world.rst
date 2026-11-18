GridWorld 2D Multi-Process
============================

``platforms/grid_world/`` is CoNAH's lightweight reference platform implementing full OS-process separation between ``GridEnvServer`` (environment RPC), ``LocalhostCommServer`` (wireless channel broker), and independent ``AgentSession`` worker processes.

Capabilities
-------------

- ``observe_local``: Read current grid coordinates ``[x, y]`` and objects within Manhattan distance ``<= 1``.
- ``move_grid(direction)``: Step one cell in ``north | south | east | west | stay`` and sync pose with the channel server for range modeling.
- ``claim_found(category)``: Claim target completion within 1 cell.
- ``comm_send`` / ``comm_broadcast`` / ``comm_poll`` / ``comm_status``: Cross-process communication primitives.

Run Tests & Range Ablation
---------------------------

.. code-block:: bash

   python3 -m unittest tests.test_step1_comm_foundation -v
   python3 -m tasks.coop_grid_search.demo --channel impaired --max-range-m 4.0
