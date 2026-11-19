核心接口参考
=============

本页汇总二次开发或接入新模块时涉及的 5 个核心抽象接口。

1. ``CommEndpoint`` （ ``comm_harness/communication/loopback.py`` ）
----------------------------------------------------------------------

所有本机内存总线、跨进程 ``127.0.0.1`` 客户端与真机 USRP 驱动客户端必须实现的统一接口：

.. code-block:: python

   class CommEndpoint(ABC):
       node_id: str
       def send(self, recipients, topic, payload, *, ttl_s=60.0, priority=0) -> dict: ...
       def broadcast(self, topic, payload, *, ttl_s=60.0, priority=0) -> dict: ...
       def poll(self, *, max_messages=20, topic="") -> list[dict]: ...
       def peers(self) -> list[str]: ...
       def status(self) -> dict: ...

2. ``ChannelModel`` （ ``comm_harness/communication/channels.py`` ）
----------------------------------------------------------------------

决定每条单播链路 ``(envelope, recipient)`` 是否送达及延迟：

.. code-block:: python

   class ChannelModel(ABC):
       name: str
       def evaluate(self, envelope: Envelope, recipient: str, *, now: float) -> ChannelDecision: ...

3. ``PlatformSpec`` （ ``platforms/__init__.py`` ）
-----------------------------------------------------

向顶层平台注册表声明一个仿真器或真机平台：

.. code-block:: python

   register(PlatformSpec(
       name="my_platform",
       toolkit_factory=build_my_toolkit,
       prompts=PromptBundle(system="...", user="..."),
       capabilities=("observe", "move", "comm_send", "comm_poll"),
       is_real_robot=False,
   ))

4. ``Toolkit`` 与 ``@tool`` （ ``comm_harness/tools/`` ）
-----------------------------------------------------------

- 使用 ``@tool(readonly=..., side_effect=..., step_consuming=...)`` 声明工具函数；
- 通过 ``Toolkit.add_tools(iter_tools(provider))`` 注册；
- 所有工具返回 ``ToolResult(data=..., images=..., consumed_steps=...)`` 。

5. ``Planner`` （ ``comm_harness/planner/base.py`` ）
-------------------------------------------------------

.. code-block:: python

   class Planner(ABC):
       def solve(self, *, system_prompt: str, user_message: Any, toolkit: Any, max_turns: int, ...) -> PlannerResult: ...
