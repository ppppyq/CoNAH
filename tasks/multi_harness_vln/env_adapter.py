"""Synchronized Multi-Agent HM3D Simulator Coordinator & Per-Robot Env Proxies.

Allows K independent ``RobotHarnessVLN(ZeroShotVlnEvaluatorMP)`` instances to run
concurrently in separate threads while advancing the shared ``MultiAgentSimulator``
(``Sim-MA-v1``) in deterministic lockstep ticks.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import quaternion
import habitat_sim
from habitat.sims import make_sim
from habitat.sims.habitat_simulator.actions import HabitatSimActions

import platforms.hm3d_m3.habitat  # Registers Sim-MA-v1
from platforms.hm3d_m3.habitat.pose_utils import (
    get_pose_change,
    get_start_sim_location,
)
from tasks.multi_harness_vln.collaboration import CollaborativeMemoryHub


@dataclass
class EpisodeProxy:
    episode_id: str
    scene_id: str
    object_category: str
    start_position: List[float]
    start_rotation: List[float]
    goals: List[Any] = None


class _SimProxy:
    def __init__(self, coordinator: "MultiAgentSimCoordinator", agent_id: int) -> None:
        self._coordinator = coordinator
        self._agent_id = agent_id

    def get_agent_state(self):
        return self._coordinator.get_agent_state(self._agent_id)


class _EnvInnerProxy:
    def __init__(self, coordinator: "MultiAgentSimCoordinator", agent_id: int) -> None:
        self._sim = _SimProxy(coordinator, agent_id)


class SingleRobotEnvProxy:
    """Per-robot view into ``MultiAgentSimCoordinator`` matching ``VectorEnv`` interface."""

    def __init__(self, coordinator: "MultiAgentSimCoordinator", agent_id: int) -> None:
        self.coordinator = coordinator
        self.agent_id = int(agent_id)
        self.number_of_episodes = [1]
        self._inner = _EnvInnerProxy(coordinator, self.agent_id)
        start = coordinator.agent_starts[self.agent_id]
        init_cat = coordinator.initial_goal_category(self.agent_id)
        self._episode = EpisodeProxy(
            episode_id="0",
            scene_id=coordinator.scene_path,
            object_category=init_cat,
            start_position=list(start["start_position"]),
            start_rotation=list(start["start_rotation"]),
            goals=[],
        )

    def update_active_category(self, category: str) -> None:
        self._episode.object_category = str(category)

    def current_episodes(self) -> List[EpisodeProxy]:
        return [self._episode]

    def call_at(self, index: int, attr_name: str) -> Any:
        if attr_name == "_env":
            return self._inner
        raise AttributeError(attr_name)

    def reset(self) -> List[Dict[str, Any]]:
        return [self.coordinator.get_initial_obs(self.agent_id)]

    def step(self, actions: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], float, bool, Dict[str, Any]]]:
        act = actions[0]
        action_id = int(act["action"] if isinstance(act, dict) else act)
        return [self.coordinator.step_agent(self.agent_id, action_id)]

    def close(self) -> None:
        self.coordinator.mark_agent_done(self.agent_id)


class MultiAgentSimCoordinator:
    """Owns the shared ``Sim-MA-v1`` instance and lockstep tick barrier for K robots."""

    def __init__(
        self,
        *,
        habitat_config: Any,
        episode: Dict[str, Any],
        scenes_dir: str,
        num_robots: int,
        max_steps: int,
        hub: CollaborativeMemoryHub,
        selected_goals: List[Dict[str, Any]],
    ) -> None:
        self.episode = episode
        self.num_robots = int(num_robots)
        self.max_steps = int(max_steps)
        self.hub = hub
        self.selected_goals = selected_goals
        self.success_distance = float(episode.get("success_distance", 1.0))

        scene_rel = episode["scene_id"].split("hm3d/", 1)[-1]
        scene_file = Path(scenes_dir) / scene_rel
        if not scene_file.is_file():
            raise FileNotFoundError(f"Scene mesh not found: {scene_file}")
        self.scene_path = str(scene_file)

        sim_cfg = habitat_config.TASK_CONFIG.SIMULATOR.clone()
        sim_cfg.defrost()
        sim_cfg.TYPE = "Sim-MA-v1"
        sim_cfg.SCENE = self.scene_path
        sim_cfg.NUM_AGENTS = self.num_robots
        sim_cfg.AGENTS = [f"AGENT_{i}" for i in range(self.num_robots)]
        for i in range(self.num_robots):
            setattr(sim_cfg, f"AGENT_{i}", sim_cfg.AGENT_0.clone())
        dataset_cfg = Path(scenes_dir) / "hm3d_annotated_basis.scene_dataset_config.json"
        if dataset_cfg.is_file():
            sim_cfg.SCENE_DATASET = str(dataset_cfg)
        sim_cfg.freeze()

        self.sim = make_sim(id_sim=sim_cfg.TYPE, config=sim_cfg)
        if not self.sim.pathfinder.is_loaded:
            settings = habitat_sim.NavMeshSettings()
            settings.set_defaults()
            settings.agent_height = sim_cfg.AGENT_0.HEIGHT
            settings.agent_radius = sim_cfg.AGENT_0.RADIUS
            if not self.sim.recompute_navmesh(self.sim.pathfinder, settings):
                raise RuntimeError("Failed to recompute navmesh for multi-agent scene")

        self.sim.reset()
        self.agent_starts = list(episode["agents"][: self.num_robots])
        if len(self.agent_starts) < self.num_robots:
            base_start = episode["agents"][0]
            base_pos = np.asarray(base_start["start_position"], dtype=np.float32)
            for extra_idx in range(len(self.agent_starts), self.num_robots):
                sampled_pos = base_pos.copy()
                if getattr(self.sim, "pathfinder", None) is not None and self.sim.pathfinder.is_loaded:
                    try:
                        cand = self.sim.pathfinder.get_random_navigable_point_near(
                            base_pos, 2.0, max_tries=50
                        )
                        if cand is not None and np.all(np.isfinite(cand)):
                            sampled_pos = np.asarray(cand, dtype=np.float32)
                    except Exception:
                        pass
                yaw = 2.0 * np.pi * (extra_idx / max(self.num_robots, 1))
                half_yaw = 0.5 * yaw
                rot = [0.0, float(np.sin(half_yaw)), 0.0, float(np.cos(half_yaw))]
                self.agent_starts.append({
                    "start_position": [float(v) for v in sampled_pos],
                    "start_rotation": rot,
                })
        self.start_sim_locations: List[tuple] = []
        self.last_sim_locations: List[tuple] = []
        for i, start in enumerate(self.agent_starts):
            pos = [float(v) for v in start["start_position"]]
            rot = [float(v) for v in start["start_rotation"]]
            self.sim.set_agent_state(pos, rot, agent_id=i)
            q_rot = np.quaternion(rot[-1], *rot[:-1])
            loc = get_start_sim_location(pos, q_rot)
            self.start_sim_locations.append(loc)
            self.last_sim_locations.append(loc)

        self.tick = 0
        self.agent_steps = [0 for _ in range(self.num_robots)]
        self.path_lengths = [0.0 for _ in range(self.num_robots)]
        self.prev_positions = [
            np.asarray(self.sim.get_agent_state(i).position, dtype=np.float32).copy()
            for i in range(self.num_robots)
        ]
        self.cached_agent_states = [
            self.sim.get_agent_state(i) for i in range(self.num_robots)
        ]
        self.trajectories: List[List[List[float]]] = [
            [p.tolist()] for p in self.prev_positions
        ]
        self.min_goal_distances: Dict[str, float] = {}
        self._init_oracle_distances()

        raw_obs_list = self.sim._per_agent_obs()
        self._initial_obs: List[Dict[str, Any]] = []
        for i in range(self.num_robots):
            ob = dict(raw_obs_list[i])
            ob["sensor_pose"] = [0.0, 0.0, 0.0]
            self._initial_obs.append(ob)
            pos = self.prev_positions[i]
            self.hub.update_robot_pose(f"robot_{i}", 0, (float(pos[0]), float(pos[2])))

        self._latest_outputs: List[Tuple[Dict[str, Any], float, bool, Dict[str, Any]]] = [
            (self._initial_obs[i], 0.0, False, self._build_info(i))
            for i in range(self.num_robots)
        ]

        # Lockstep condition barrier state (OpenGL sim steps always run on main thread)
        self._cond = threading.Condition()
        self._active_agents: Set[int] = set(range(self.num_robots))
        self._pending_actions: Dict[int, int] = {}
        self._step_requested = False
        self._generation = 0
        self._closed = False

    def initial_goal_category(self, agent_id: int) -> str:
        if agent_id < len(self.selected_goals):
            return str(self.selected_goals[agent_id]["category"])
        return str(self.selected_goals[0]["category"])

    def _goal_reach_points(self, goal_index: int) -> List[List[float]]:
        g = self.episode["goals_set"][goal_index]
        if g.get("modality") == "ON":
            return g.get("reach_points", [])
        return g.get("instance_reach_points") or g.get("reach_points", [])

    def _geodesic_to_goal(self, pos: np.ndarray, goal_index: int) -> float:
        pts = self._goal_reach_points(goal_index)
        if not pts:
            return float("inf")
        dists = [
            float(self.sim.geodesic_distance(pos, np.asarray(p, dtype=np.float32)))
            for p in pts
        ]
        finite = [d for d in dists if np.isfinite(d)]
        return min(finite) if finite else float("inf")

    def _init_oracle_distances(self) -> None:
        self.initial_goal_distances: Dict[str, float] = {}
        for g in self.selected_goals:
            gid = str(g["goal_id"])
            gidx = int(g["goal_index"])
            d0 = min(self._geodesic_to_goal(p, gidx) for p in self.prev_positions)
            self.initial_goal_distances[gid] = d0
            self.min_goal_distances[gid] = d0

    def _update_oracle_distances(self) -> None:
        for g in self.selected_goals:
            gid = str(g["goal_id"])
            gidx = int(g["goal_index"])
            d_curr = min(self._geodesic_to_goal(p, gidx) for p in self.prev_positions)
            if d_curr < self.min_goal_distances.get(gid, float("inf")):
                self.min_goal_distances[gid] = d_curr

    def _build_info(self, agent_id: int) -> Dict[str, Any]:
        node_id = f"robot_{agent_id}"
        active_gid = self.hub.robot_states.get(node_id, {}).get("active_goal_id")
        gidx = None
        if active_gid and active_gid in self.hub.goals:
            gidx = self.hub.goals[active_gid]["goal_index"]
        if gidx is None:
            gidx = self.selected_goals[min(agent_id, len(self.selected_goals) - 1)]["goal_index"]
        d2g = self._geodesic_to_goal(self.prev_positions[agent_id], int(gidx))
        succ = 1.0 if d2g <= self.success_distance else 0.0
        min_d = self.min_goal_distances.get(active_gid, d2g) if active_gid else d2g
        oracle_succ = 1.0 if min_d <= self.success_distance else 0.0
        return {
            "steps_taken": self.agent_steps[agent_id],
            "distance_to_goal": float(d2g) if np.isfinite(d2g) else 99.0,
            "success": succ,
            "oracle_success": oracle_succ,
            "path_length": float(self.path_lengths[agent_id]),
            "spl": succ,
            "ndtw": 0.0,
            "sdtw": 0.0,
        }

    def get_initial_obs(self, agent_id: int) -> Dict[str, Any]:
        return self._initial_obs[agent_id]

    def get_agent_state(self, agent_id: int = 0):
        return self.cached_agent_states[agent_id]

    def step_agent(self, agent_id: int, action_id: int) -> Tuple[Dict[str, Any], float, bool, Dict[str, Any]]:
        with self._cond:
            if self._closed or agent_id not in self._active_agents:
                return self._latest_outputs[agent_id]

            my_gen = self._generation
            self._pending_actions[agent_id] = int(action_id)

            if self._active_agents.issubset(self._pending_actions.keys()):
                self._step_requested = True
                self._cond.notify_all()

            self._cond.wait_for(lambda: self._generation > my_gen or self._closed)
            return self._latest_outputs[agent_id]

    def mark_agent_done(self, agent_id: int) -> None:
        with self._cond:
            self._active_agents.discard(int(agent_id))
            if self._active_agents and self._active_agents.issubset(self._pending_actions.keys()):
                self._step_requested = True
            self._cond.notify_all()

    def serve_on_main_thread(self, futures: List[Any]) -> None:
        """Run on the main thread (which owns the OpenGL context) until all robot futures complete."""
        while True:
            with self._cond:
                self._cond.wait_for(
                    lambda: self._step_requested or not self._active_agents or self._closed or all(f.done() for f in futures),
                    timeout=0.2,
                )
                if self._step_requested and self._active_agents:
                    self._step_requested = False
                    self._execute_joint_step_locked()
                    self._cond.notify_all()
                if not self._active_agents or self._closed or all(f.done() for f in futures):
                    self._closed = True
                    self._cond.notify_all()
                    break

    def _execute_joint_step_locked(self) -> None:
        joint_actions = [
            int(self._pending_actions.get(i, int(HabitatSimActions.STOP)))
            for i in range(self.num_robots)
        ]
        # Note: in multi-agent mode, an individual agent emitting STOP (0) at a goal
        # does not terminate the simulator; MultiAgentSimulator treats 0 as a hold.
        raw_obs_list = self.sim.step(joint_actions)
        self.tick += 1

        for i in range(self.num_robots):
            if i in self._pending_actions:
                self.agent_steps[i] += 1
            st_i = self.sim.get_agent_state(i)
            self.cached_agent_states[i] = st_i
            pos = np.asarray(st_i.position, dtype=np.float32).copy()
            self.path_lengths[i] += float(np.linalg.norm(pos - self.prev_positions[i]))
            self.prev_positions[i] = pos
            self.trajectories[i].append(pos.tolist())
            self.hub.update_robot_pose(f"robot_{i}", self.tick, (float(pos[0]), float(pos[2])))

        self._update_oracle_distances()
        self.hub.deliver_tick(self.tick)

        for i in range(self.num_robots):
            dx, dy, do, curr_loc = get_pose_change(self.sim, self.last_sim_locations[i], agent_id=i)
            self.last_sim_locations[i] = curr_loc
            ob = dict(raw_obs_list[i])
            ob["sensor_pose"] = [float(dx), float(dy), float(do)]
            info_i = self._build_info(i)
            done_i = bool(self.agent_steps[i] >= self.max_steps)
            self._latest_outputs[i] = (ob, 0.0, done_i, info_i)

        self._pending_actions.clear()
        self._generation += 1

    def evaluate_team(self) -> Dict[str, Any]:
        """Compute private oracle multi-goal evaluation metrics after the episode ends."""
        per_goal_results: List[Dict[str, Any]] = []
        found_count = 0
        oracle_found_count = 0
        spl_terms: List[float] = []

        for g in self.selected_goals:
            gid = str(g["goal_id"])
            gidx = int(g["goal_index"])
            hub_goal = self.hub.goals[gid]
            found_by = hub_goal.get("found_by")
            found_step = hub_goal.get("found_step")

            # Check all verified claims for this goal, plus final positions if unclaimed
            claims_for_gid = [c for c in self.hub.verified_claims if c.get("goal_id") == gid]
            eval_dist = float("inf")
            if claims_for_gid:
                for c in claims_for_gid:
                    c_node = str(c["node_id"])
                    c_idx = int(c_node.split("_")[-1])
                    c_step = int(c.get("step") or len(self.trajectories[c_idx]) - 1)
                    trace = self.trajectories[c_idx]
                    pos_at_claim = np.asarray(trace[min(c_step, len(trace) - 1)], dtype=np.float32)
                    d_claim = self._geodesic_to_goal(pos_at_claim, gidx)
                    if c.get("world_xz") is not None:
                        wx, wz = c["world_xz"]
                        pos_xz = np.array([float(wx), float(pos_at_claim[1]), float(wz)], dtype=np.float32)
                        d_xz = self._geodesic_to_goal(pos_xz, gidx)
                        d_claim = min(d_claim, d_xz)
                    if c.get("object_world_xz") is not None:
                        ox, oz = c["object_world_xz"]
                        for alpha in (1.0, 0.85, 0.7, 0.5):
                            ix = float(pos_at_claim[0]) + alpha * (float(ox) - float(pos_at_claim[0]))
                            iz = float(pos_at_claim[2]) + alpha * (float(oz) - float(pos_at_claim[2]))
                            pos_obj = np.array([ix, float(pos_at_claim[1]), iz], dtype=np.float32)
                            d_obj = self._geodesic_to_goal(pos_obj, gidx)
                            if np.isfinite(d_obj):
                                d_claim = min(d_claim, d_obj)
                                break
                    if d_claim < eval_dist:
                        eval_dist = d_claim
                        found_by = c_node
                        found_step = c_step
            else:
                # Distance of closest robot at episode end
                eval_dist = min(self._geodesic_to_goal(p, gidx) for p in self.prev_positions)

            is_success = bool(np.isfinite(eval_dist) and eval_dist <= self.success_distance)
            min_d = self.min_goal_distances.get(gid, float("inf"))
            is_oracle = bool(np.isfinite(min_d) and min_d <= self.success_distance)
            if is_success:
                found_count += 1
            if is_oracle:
                oracle_found_count += 1

            d0 = self.initial_goal_distances.get(gid, 1.0)
            if found_by is not None:
                agent_idx = int(found_by.split("_")[-1])
                agent_path = max(self.path_lengths[agent_idx], 1e-6)
            else:
                agent_path = max(sum(self.path_lengths) / max(len(self.path_lengths), 1), 1e-6)
            goal_spl = (1.0 if is_success else 0.0) * (d0 / max(d0, agent_path)) if np.isfinite(d0) else 0.0
            spl_terms.append(float(goal_spl))

            per_goal_results.append({
                "goal_id": gid,
                "goal_index": gidx,
                "category": g["category"],
                "modality": g.get("modality", "ON"),
                "claimed_by": found_by,
                "claimed_step": found_step,
                "distance_to_goal": round(float(eval_dist), 4) if np.isfinite(eval_dist) else None,
                "min_distance_to_goal": round(float(min_d), 4) if np.isfinite(min_d) else None,
                "success": 1.0 if is_success else 0.0,
                "oracle_success": 1.0 if is_oracle else 0.0,
                "spl": round(float(goal_spl), 4),
            })

        num_goals = max(len(self.selected_goals), 1)
        team_sr = found_count / num_goals
        team_oracle_sr = oracle_found_count / num_goals
        team_spl = sum(spl_terms) / num_goals

        per_robot_results = []
        for i in range(self.num_robots):
            per_robot_results.append({
                "node_id": f"robot_{i}",
                "steps_taken": int(self.agent_steps[i]),
                "path_length": round(float(self.path_lengths[i]), 4),
                "goals_claimed": [
                    r["goal_id"] for r in per_goal_results if r["claimed_by"] == f"robot_{i}"
                ],
                "goals_succeeded": [
                    r["goal_id"] for r in per_goal_results
                    if r["claimed_by"] == f"robot_{i}" and r["success"] == 1.0
                ],
            })

        return {
            "ticks_taken": int(self.tick),
            "num_robots": int(self.num_robots),
            "num_goals": int(len(self.selected_goals)),
            "goals_succeeded": int(found_count),
            "team_success_rate": round(float(team_sr), 4),
            "all_goals_succeeded": 1.0 if found_count == len(self.selected_goals) else 0.0,
            "any_goal_succeeded": 1.0 if found_count > 0 else 0.0,
            "team_oracle_success_rate": round(float(team_oracle_sr), 4),
            "team_spl": round(float(team_spl), 4),
            "total_path_length": round(float(sum(self.path_lengths)), 4),
            "mean_path_length": round(float(sum(self.path_lengths) / max(self.num_robots, 1)), 4),
            "per_goal": per_goal_results,
            "per_robot": per_robot_results,
            # Top-level compatibility keys for single/multi comparison dashboards
            "steps_taken": float(self.tick),
            "success": round(float(team_sr), 4),
            "oracle_success": round(float(team_oracle_sr), 4),
            "spl": round(float(team_spl), 4),
            "path_length": round(float(sum(self.path_lengths) / max(self.num_robots, 1)), 4),
            "distance_to_goal": round(
                float(
                    np.mean([
                        r["distance_to_goal"] for r in per_goal_results
                        if r["distance_to_goal"] is not None
                    ] or [99.0])
                ),
                4,
            ),
        }

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        if getattr(self, "sim", None) is not None:
            try:
                self.sim.close()
            except Exception:
                pass
            self.sim = None
