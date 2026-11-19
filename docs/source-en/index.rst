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

      Run a multi-process cooperative search and wireless channel ablation in 30 seconds without a GPU.

   .. grid-item-card:: Benchmarks & Results
      :link: rst_source/benchmarks/results
      :link-type: doc

      Explore Team SR, communication byte budgets, and Ideal / Impaired / Blackout ablations.

   .. grid-item-card:: Communication & Channels
      :link: rst_source/communication/tools_and_envelope
      :link-type: doc

      Use the 4 first-class communication tools, wire Envelopes, and pluggable wireless channel models.

   .. grid-item-card:: System Architecture
      :link: rst_source/development/architecture
      :link-type: doc

      Understand the three-segment boundary between intra-robot control RPC and inter-robot RF channels.

   .. grid-item-card:: HM3D-M³ 3D Collaborative VLN
      :link: rst_source/platforms/hm3d_m3
      :link-type: doc

      Run N-robot M-goal indoor visual navigation with cross-robot SE(2) FMM sighting projection.

   .. grid-item-card:: Extend Platforms & Channels
      :link: rst_source/development/add_platform
      :link-type: doc

      Add a new robot platform or custom wireless channel simulator in ~50 lines of Python.

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
   :caption: Benchmarks & Demos

   Evaluation Metrics <rst_source/benchmarks/metrics>
   Communication Ablations & Results <rst_source/benchmarks/results>

.. toctree::
   :maxdepth: 2
   :includehidden:
   :titlesonly:
   :hidden:
   :caption: Communication & Channels

   Communication Tools & Envelope <rst_source/communication/tools_and_envelope>
   Wireless Channel Models <rst_source/communication/channels>
   Collaboration Protocols & Memory Sync <rst_source/communication/protocols>

.. toctree::
   :maxdepth: 2
   :includehidden:
   :titlesonly:
   :hidden:
   :caption: User Guides

   Configure Planners <rst_source/guides/configure_planner>
   Local Memory & Receipt Auditing <rst_source/guides/memory_and_audit>
   CLI, TUI & Live Dashboard <rst_source/guides/dashboard>
   Distributed & Remote RPC Deployment <rst_source/guides/deployment>

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
   Core Interfaces <rst_source/development/interfaces>
   Add a Robot Platform <rst_source/development/add_platform>
   Add a Channel or Protocol <rst_source/development/add_channel>

.. toctree::
   :maxdepth: 2
   :includehidden:
   :titlesonly:
   :hidden:
   :caption: Resources

   Roadmap & Releases <rst_source/resources/roadmap>
   Contributing <rst_source/resources/contributing>
