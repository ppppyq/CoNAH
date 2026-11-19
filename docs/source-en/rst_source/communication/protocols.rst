Collaboration Protocols & Memory Sync
======================================

Built atop the transport layer, ``tasks/multi_harness_vln/collaboration.py`` implements four structured collaboration payloads:

- **GOAL_ASSIGNED / GOAL_FOUND**: Dynamic multi-goal Task Board relay (automatic claim of the next ``PENDING`` goal and ``N > M`` co-search).
- **OBJECT_SIGHTING**: Broadcasts opportunistic 3D target detections ``{category, global_xz, confidence}`` for direct ``SE(2)`` projection into teammates' local FMM maps.
- **PLACE_OBSERVED**: Shares room summaries and seen objects, transformed into relative bearing/distance and injected into ``[TEAM_SHARED_MEMORY]``.
- **INTENT_CLAIM**: Spatial deconfliction preventing redundant multi-robot exploration of the same room.
