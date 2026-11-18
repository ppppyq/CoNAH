"""Generic spatial, topological, and task memory for embodied agents.

Provides simulator-independent memory primitives:
- ``WorldGraph``: Topological place/object/failure graph (`world_graph.py`)
- ``recall``: IDF-weighted semantic retrieval and reflection (`retrieval.py`)
- ``TodoMemory``: Evidence-backed structured task memory (`todo.py`)
"""

from comm_harness.memory import retrieval, todo, world_graph
from comm_harness.memory.retrieval import recall
from comm_harness.memory.todo import (
    ACTIVE,
    BLOCKED,
    COMPLETED,
    PENDING,
    TODO_STATUSES,
    TodoMemory,
)
from comm_harness.memory.world_graph import WorldGraph


def load_memory_modules(harnessvln_root=None):
    """Return the built-in ``(world_graph, retrieval)`` modules."""
    return [world_graph, retrieval]


__all__ = [
    "ACTIVE",
    "BLOCKED",
    "COMPLETED",
    "PENDING",
    "TODO_STATUSES",
    "TodoMemory",
    "WorldGraph",
    "load_memory_modules",
    "recall",
    "retrieval",
    "todo",
    "world_graph",
]
