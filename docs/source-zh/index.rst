.. _home:

欢迎使用 CoNAH
==================

.. image:: /_images/conah_logo.svg
   :alt: CoNAH
   :class: conah-home-logo

**CoNAH (Communication-Native Agentic Harness)** 是一个面向多机器人具身智能的通信原生智能体基础设施。它将多机分布式感知（Perception）、推理（Reasoning）、拓扑记忆（WorldGraph）、物理执行（Execution）与**可验证机间无线通信（Channel-Aware Communication）**整合到统一的控制框架中，使异构机器人集群能够在带宽受限、存在丢包与穿墙遮挡的真实物理世界中协同演进。

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: 快速开始
      :link: rst_source/get_started/quickstart
      :link-type: doc

      无需 GPU，30 秒内启动本机多进程协同搜索与信道损伤消融实验。

   .. grid-item-card:: CoNAH 简介
      :link: rst_source/get_started/overview
      :link-type: doc

      理解为什么多机具身智能需要“通信原生”的智能体控制底座。

   .. grid-item-card:: 通信工具与信道仿真
      :link: rst_source/guides/communication
      :link-type: doc

      配置 4 个一等公民通信 Tool，切换理想、受限、断联或 USRP 无线信道。

   .. grid-item-card:: 系统架构与通信边界
      :link: rst_source/development/architecture
      :link-type: doc

      深入“机内控制面 RPC”与“机间无线信道数据面”的三段式解耦设计。

   .. grid-item-card:: HM3D-M³ 3D 多机导航
      :link: rst_source/platforms/hm3d_m3
      :link-type: doc

      在真实室内 3D 场景中运行 N 机 M 目标动态接力与跨机 SE(2) FMM 投影导航。

   .. grid-item-card:: 扩展新平台与信道
      :link: rst_source/development/add_platform
      :link-type: doc

      使用 ``@tool`` 与 ``PlatformSpec`` 快速接入新机器人或自定义信道模型。

选择运行平台： :doc:`GridWorld 2D <rst_source/platforms/grid_world>` · :doc:`HM3D-M³ 3D <rst_source/platforms/hm3d_m3>` · :doc:`USRP 与真机部署 <rst_source/platforms/real_world>` 。

.. toctree::
   :maxdepth: 2
   :includehidden:
   :titlesonly:
   :hidden:
   :caption: 开始使用

   CoNAH 简介 <rst_source/get_started/overview>
   快速开始 <rst_source/get_started/quickstart>

.. toctree::
   :maxdepth: 2
   :includehidden:
   :titlesonly:
   :hidden:
   :caption: 使用指南

   通信工具与信道仿真 <rst_source/guides/communication>
   规划器配置 <rst_source/guides/configure_planner>
   CLI、TUI 与实时面板 <rst_source/guides/dashboard>

.. toctree::
   :maxdepth: 2
   :includehidden:
   :titlesonly:
   :hidden:
   :caption: 仿真与真机平台

   GridWorld 2D 多进程协同 <rst_source/platforms/grid_world>
   HM3D-M³ 3D 多机协同导航 <rst_source/platforms/hm3d_m3>
   USRP 软件无线电与真机接入 <rst_source/platforms/real_world>

.. toctree::
   :maxdepth: 2
   :includehidden:
   :titlesonly:
   :hidden:
   :caption: 原理与开发

   系统架构与通信边界 <rst_source/development/architecture>
   接入新机器人或信道模型 <rst_source/development/add_platform>
