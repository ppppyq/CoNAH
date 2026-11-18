.. _home:

Welcome to CoNAH
===================

.. image:: /_images/conah_logo.svg
   :alt: CoNAH
   :class: conah-home-logo

**CoNAH (Communication-Native Agentic Harness)** is an open-source infrastructure for multi-robot embodied intelligence. It unifies distributed perception, reasoning, topological memory (``WorldGraph``), physical execution, and **verifiable channel-aware wireless communication** into a standardized harness, enabling heterogeneous robot teams to collaborate reliably under realistic bandwidth limits, packet loss, and wall attenuation.

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Quickstart
      :link: rst_source/get_started/quickstart
      :link-type: doc

      Run a multi-process cooperative search and channel ablation in 30 seconds without a GPU.

   .. grid-item-card:: CoNAH Overview
      :link: rst_source/get_started/overview
      :link-type: doc

      Learn why multi-robot embodied intelligence requires a communication-native harness.

   .. grid-item-card:: Communication Tools & Channels
      :link: rst_source/guides/communication
      :link-type: doc

      Use the 4 built-in communication tools and switch across ideal, impaired, blackout, or USRP channels.

   .. grid-item-card:: System Architecture
      :link: rst_source/development/architecture
      :link-type: doc

      Understand the boundary between intra-robot control RPC and inter-robot wireless channels.

   .. grid-item-card:: HM3D-M³ 3D Collaborative VLN
      :link: rst_source/platforms/hm3d_m3
      :link-type: doc

      Run N-robot M-goal collaborative visual navigation with cross-robot SE(2) FMM projection.

   .. grid-item-card:: Extend Platforms & Channels
      :link: rst_source/development/add_platform
      :link-type: doc

      Add a new robot platform or custom wireless channel model in ~50 lines of Python.

Choose a platform: :doc:`GridWorld 2D <rst_source/platforms/grid_world>` · :doc:`HM3D-M³ 3D <rst_source/platforms/hm3d_m3>` · :doc:`USRP SDR & Real Robots <rst_source/platforms/real_world>`.

.. toctree::
   :maxdepth: 2
   :includehidden:
   :titlesonly:
   :hidden:
   :caption: Get Started

   CoNAH Overview <rst_source/get_started/overview>
   Quickstart <rst_source/get_started/quickstart>

.. toctree::
   :maxdepth: 2
   :includehidden:
   :titlesonly:
   :hidden:
   :caption: User Guides

   Communication Tools & Channels <rst_source/guides/communication>
   Configure Planners <rst_source/guides/configure_planner>
   CLI, TUI & Live Dashboard <rst_source/guides/dashboard>

.. toctree::
   :maxdepth: 2
   :includehidden:
   :titlesonly:
   :hidden:
   :caption: Simulators & Hardware

   GridWorld 2D Multi-Process <rst_source/platforms/grid_world>
   HM3D-M³ 3D Collaborative VLN <rst_source/platforms/hm3d_m3>
   USRP SDR & Real Robots <rst_source/platforms/real_world>

.. toctree::
   :maxdepth: 2
   :includehidden:
   :titlesonly:
   :hidden:
   :caption: Internals & Development

   System Architecture <rst_source/development/architecture>
   Add a Platform or Channel <rst_source/development/add_platform>
