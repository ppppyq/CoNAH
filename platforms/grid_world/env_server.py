"""Standalone 2D multi-robot grid environment RPC server (`127.0.0.1`) for Step-1 verification.

Models RPent's service separation (`env_server` <-> `BaseEnvClient`):
- Holds true object positions privately inside the server.
- Exposes only local 1-hop sensor observations (`observe_local`), motion (`move_grid`),
  and target claims (`claim_found`) to robot toolkits.
- Evaluates ground-truth `Team SR` only when `evaluate_team()` is called by the runner.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

from comm_harness.rpc import RpcClient, RpcServer


DELTAS: Dict[str, Tuple[int, int]] = {
    "north": (0, 1),
    "south": (0, -1),
    "east": (1, 0),
    "west": (-1, 0),
    "stay": (0, 0),
}


class GridWorldState:
    """Thread-safe multi-robot 2D grid world with private object locations."""

    def __init__(
        self,
        width: int = 5,
        height: int = 5,
        *,
        robot_starts: Optional[Dict[str, Tuple[int, int]]] = None,
        objects: Optional[Dict[str, Tuple[int, int]]] = None,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self._lock = threading.RLock()
        self._poses: Dict[str, Tuple[int, int]] = dict(
            robot_starts or {"robot_0": (0, 0), "robot_1": (4, 4)}
        )
        # Private oracle map: category -> (x, y)
        self._objects: Dict[str, Tuple[int, int]] = dict(
            objects or {"vase": (1, 0), "mirror": (3, 4)}
        )
        self._steps: Dict[str, int] = {rid: 0 for rid in self._poses}
        self._claims: List[Dict[str, Any]] = []

    def observe_local(self, node_id: str) -> Dict[str, Any]:
        """Return robot's current pose and visible objects within Manhattan distance <= 1."""
        with self._lock:
            if node_id not in self._poses:
                raise ValueError(f"unknown robot: {node_id}")
            rx, ry = self._poses[node_id]
            visible: List[Dict[str, Any]] = []
            for category, (ox, oy) in sorted(self._objects.items()):
                if abs(ox - rx) + abs(oy - ry) <= 1:
                    visible.append({"category": category, "pos": [ox, oy]})
            return {
                "node_id": node_id,
                "pose": [rx, ry],
                "steps": self._steps[node_id],
                "visible_objects": visible,
                "grid_bounds": [self.width, self.height],
            }

    def move_grid(self, node_id: str, direction: str) -> Dict[str, Any]:
        """Move one cell in ``north | south | east | west | stay``."""
        d = str(direction).strip().lower()
        if d not in DELTAS:
            raise ValueError(f"invalid direction {direction!r}; expected one of {sorted(DELTAS)}")
        dx, dy = DELTAS[d]
        with self._lock:
            if node_id not in self._poses:
                raise ValueError(f"unknown robot: {node_id}")
            rx, ry = self._poses[node_id]
            nx = max(0, min(self.width - 1, rx + dx))
            ny = max(0, min(self.height - 1, ry + dy))
            collided = (nx, ny) == (rx, ry) and d != "stay"
            self._poses[node_id] = (nx, ny)
            self._steps[node_id] += 1
            return {
                "node_id": node_id,
                "direction": d,
                "pose": [nx, ny],
                "collided_boundary": collided,
                "steps": self._steps[node_id],
            }

    def claim_found(self, node_id: str, category: str) -> Dict[str, Any]:
        """Record a robot's claim at its current position (without leaking oracle success)."""
        with self._lock:
            if node_id not in self._poses:
                raise ValueError(f"unknown robot: {node_id}")
            pose = list(self._poses[node_id])
            record = {
                "node_id": node_id,
                "category": str(category).strip().lower(),
                "claimed_at_pose": pose,
                "step": self._steps[node_id],
            }
            self._claims.append(record)
            return {"recorded": True, **record}

    def evaluate_team(self) -> Dict[str, Any]:
        """Evaluator-only ground-truth check (Manhattan distance <= 1 at claim time)."""
        with self._lock:
            per_target: Dict[str, Dict[str, Any]] = {}
            for cat, (ox, oy) in self._objects.items():
                matched = [
                    c
                    for c in self._claims
                    if c["category"] == cat
                    and abs(c["claimed_at_pose"][0] - ox) + abs(c["claimed_at_pose"][1] - oy) <= 1
                ]
                per_target[cat] = {
                    "success": 1.0 if matched else 0.0,
                    "claimed_by": matched[0]["node_id"] if matched else None,
                }
            team_sr = (
                sum(v["success"] for v in per_target.values()) / len(per_target)
                if per_target
                else 0.0
            )
            return {
                "team_sr": round(team_sr, 4),
                "all_succeeded": team_sr == 1.0,
                "targets": per_target,
                "robot_steps": dict(self._steps),
                "claims": list(self._claims),
            }


class GridEnvServer:
    """Localhost HTTP RPC wrapper around :class:`GridWorldState`."""

    def __init__(
        self,
        world: Optional[GridWorldState] = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.world = world or GridWorldState()
        self.rpc = RpcServer(host=host, port=port, name="grid_env_server")
        self.rpc.register("observe_local", self.world.observe_local)
        self.rpc.register("move_grid", self.world.move_grid)
        self.rpc.register("claim_found", self.world.claim_found)
        self.rpc.register("evaluate_team", self.world.evaluate_team)

    @property
    def endpoint(self) -> str:
        return self.rpc.endpoint

    def start_background(self) -> str:
        return self.rpc.start_background()

    def close(self) -> None:
        self.rpc.close()


class GridEnvClient:
    """RPC client used by each robot's toolkit to interact with :class:`GridEnvServer`."""

    def __init__(self, endpoint: str, node_id: str) -> None:
        self.node_id = str(node_id)
        self._rpc = RpcClient(endpoint)

    def observe_local(self) -> Dict[str, Any]:
        return self._rpc.call("observe_local", node_id=self.node_id)

    def move_grid(self, direction: str) -> Dict[str, Any]:
        return self._rpc.call("move_grid", node_id=self.node_id, direction=direction)

    def claim_found(self, category: str) -> Dict[str, Any]:
        return self._rpc.call("claim_found", node_id=self.node_id, category=category)
