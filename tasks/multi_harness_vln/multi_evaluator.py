"""Per-robot independent HarnessVLN instance with SharedGroundedSAM and CollaborativeMemoryHub."""
from __future__ import annotations

import math
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from habitat import logger

from comm_harness import telemetry
from platforms.hm3d_m3.habitat.pose_utils import (
    global_xz_to_local_map,
    local_map_to_global_xz,
)
from platforms.hm3d_m3.navigation.data_utils import OrderedSet
from platforms.hm3d_m3.navigation.depth_utils import get_world_xz_from_pixel
from platforms.hm3d_m3.navigation.mapping import Semantic_Mapping
from platforms.hm3d_m3.navigation.policy import FusionMapPolicy
from platforms.hm3d_m3.navigation.semantic_prediction import GroundedSAM
from tasks.harness_vln.evaluator import ZeroShotVlnEvaluatorMP
from tasks.multi_harness_vln.collaboration import CollaborativeMemoryHub
from tasks.multi_harness_vln.env_adapter import SingleRobotEnvProxy


class SharedGroundedSAM:
    """Thread-safe shared GroundedSAM instance so N robots share 1 set of GPU weights."""

    def __init__(self, config: Any, device: Any) -> None:
        self._lock = threading.Lock()
        with telemetry.service("perception"):
            self._sam = GroundedSAM(config, device)

    def segment(self, image: np.ndarray, **kwargs: Any):
        with self._lock:
            return self._sam.segment(image, **kwargs)


class RobotHarnessVLN(ZeroShotVlnEvaluatorMP):
    """Independent HarnessVLN instance for a single robot in a multi-robot team."""

    def __init__(
        self,
        config: Any,
        *,
        agent_id: int,
        node_id: str,
        env_proxy: SingleRobotEnvProxy,
        shared_segment_module: SharedGroundedSAM,
        hub: CollaborativeMemoryHub,
        start_sim_location: tuple,
    ) -> None:
        self.agent_id = int(agent_id)
        self.node_id = str(node_id)
        self._env_proxy = env_proxy
        self._shared_segment_module = shared_segment_module
        self.hub = hub
        self.start_sim_location = start_sim_location
        self.active_goal: Optional[Dict[str, Any]] = None
        self.claimed_locations: List[Tuple[float, float]] = []
        self._nav_ref = None
        super().__init__(config)
        self._init_envs()
        self.agent.goal_vocabulary = self.hub.all_goal_categories()
        self.visualizer.dataset_label = f"{self.node_id} / {getattr(config, 'TASK_TYPE', 'OBJNAV')}"

    def _init_envs(self) -> None:
        self.envs = self._env_proxy
        self.detected_classes = OrderedSet()

    def _initialize_policy(self) -> None:
        self.segment_module = self._shared_segment_module
        with telemetry.service("mapping"):
            self.mapping_module = Semantic_Mapping(self.config.MAP).to(self.device)
        self.mapping_module.eval()
        self.visualizer.update_map(self.mapping_module)
        self.policy = FusionMapPolicy(self.config, self.mapping_module.map_shape[0])
        self.policy.reset()

    def _current_global_xz(self) -> Tuple[float, float]:
        state = self._env_proxy.coordinator.get_agent_state(self.agent_id)
        pos = state.position
        return float(pos[0]), float(pos[2])

    def _local_pose(self) -> np.ndarray:
        if getattr(self.mapping_module, "full_pose", None) is not None:
            fp = self.mapping_module.full_pose.detach().cpu().numpy()
            if fp.ndim >= 2:
                return fp[0]
            return fp
        return np.array([12.0, 12.0, 0.0], dtype=np.float32)

    def _assign_or_switch_goal(self, step: int, is_initial: bool = False) -> bool:
        wx, wz = self._current_global_xz()
        next_goal = self.hub.claim_initial_or_next_goal(self.node_id, (wx, wz), step)
        if next_goal is None:
            return False
        self.active_goal = next_goal
        self.instruction = str(next_goal["instruction"])
        self.destination = str(next_goal["category"])
        self._env_proxy.update_active_category(self.destination)

        # Ensure all team goal categories are in self.classes for GroundedSAM
        for cat in self.hub.all_goal_categories():
            if cat not in self.classes:
                self.classes.append(cat)

        if not is_initial:
            logger.info(
                f"[{self.node_id}] Switched to next team goal {next_goal['goal_id']} "
                f"({self.destination}): {self.instruction}"
            )
            self._emit_event(
                "TEAM_GOAL_SWITCHED",
                step,
                node_id=self.node_id,
                goal_id=next_goal["goal_id"],
                category=self.destination,
                instruction=self.instruction,
            )
            # Reset goal-specific TodoList & stop state while keeping Semantic_Mapping & WorldGraph!
            self.claimed_locations.clear()
            if getattr(self.agent, "todo", None) is not None:
                self.agent.todo.reset()
            if getattr(self, "_nav_state", None) is not None:
                self._nav_state.__init__()
            self.stop_feedback = (
                f"System: Previous goal recorded! You are now assigned to team goal "
                f"{next_goal['goal_id']} ({self.destination}): {self.instruction}"
            )
            self.consecutive_stop_failures = 0
            self.last_grounded_target = None
            self.last_stop_rejection_step = None
        return True

    def _maps_initialization(self):
        self._assign_or_switch_goal(0, is_initial=True)
        for cat in self.hub.all_goal_categories():
            if cat not in self.base_classes:
                self.base_classes.append(cat)
        obs, full_pose = super()._maps_initialization()
        if self.active_goal is not None:
            self.instruction = str(self.active_goal["instruction"])
            self.destination = str(self.active_goal["category"])
        for cat in self.hub.all_goal_categories():
            if cat not in self.classes:
                self.classes.append(cat)
        return obs, full_pose

    def _preprocess_state(self, state: np.ndarray) -> np.ndarray:
        out = super()._preprocess_state(state)
        try:
            self._check_grounded_sam_team_sightings(state)
        except Exception as e:
            logger.debug(f"[{self.node_id}] GroundedSAM sighting check skipped: {e}")
        return out

    def _check_grounded_sam_team_sightings(self, raw_state: np.ndarray) -> None:
        detections = getattr(self, "current_detections", None)
        if detections is None or len(detections) == 0:
            return
        team_cats = [c.lower() for c in self.hub.all_goal_categories()]
        if not team_cats:
            return

        # Reconstruct metric depth image (in metres) from raw_state
        s_hwc = raw_state.transpose(1, 2, 0)
        raw_depth = s_hwc[:, :, 3:4]
        min_d = self.config.TASK_CONFIG.SIMULATOR.DEPTH_SENSOR.MIN_DEPTH
        max_d = self.config.TASK_CONFIG.SIMULATOR.DEPTH_SENSOR.MAX_DEPTH
        depth_m = self._preprocess_depth(raw_depth.copy(), min_d, max_d) / 100.0

        cls2 = self.classes.copy()
        if self.agent.stair:
            cls2 = ["stairs"]
        elif self.current_step <= 12 and "stairs" not in cls2:
            cls2.append("stairs")

        pose = self._local_pose()
        intrinsics = self._get_camera_intrinsics()
        robot_wx, robot_wz = self._current_global_xz()

        spawn_wx, spawn_wz = local_map_to_global_xz(12.0, 12.0, self.start_sim_location)
        for idx in range(len(detections)):
            class_id = detections.class_id[idx] if detections.class_id is not None else None
            conf = float(detections.confidence[idx]) if detections.confidence is not None else 0.0
            if class_id is None or class_id >= len(cls2) or conf < 0.52:
                continue
            cat_name = str(cls2[class_id]).strip()
            if cat_name.lower() not in team_cats:
                continue
            box = detections.xyxy[idx]
            u = int(np.clip((box[0] + box[2]) / 2.0, 0, self.width - 1))
            v = int(np.clip((box[1] + box[3]) / 2.0, 0, self.height - 1))
            d_val = float(depth_m[v, u])
            if not (0.4 <= d_val <= 3.5):
                continue
            target_local = get_world_xz_from_pixel(
                pixel_coords=(u, v),
                depth_image=depth_m,
                full_pose=pose,
                camera_intrinsics=intrinsics,
            )
            if np.allclose(target_local, [12.0, 12.0]):
                continue
            wx, wz = local_map_to_global_xz(
                float(target_local[0]), float(target_local[1]), self.start_sim_location
            )
            # Skip detections when the robot is still inside the starting area or near already-claimed primary goals
            if math.hypot(robot_wx - spawn_wx, robot_wz - spawn_wz) < 2.0 and math.hypot(wx - spawn_wx, wz - spawn_wz) < 2.5:
                continue
            if any(
                c.get("world_xz") is not None
                and c.get("role") != "opportunistic_teammate_verification"
                and math.hypot(wx - c["world_xz"][0], wz - c["world_xz"][1]) < 1.6
                for c in self.hub.verified_claims
            ):
                continue
            # Only broadcast to teammates if high-confidence or if it's a teammate's goal category
            if conf >= 0.55 or cat_name.lower() != self.destination.lower():
                sent = self.hub.broadcast_object_sighting(
                    sender=self.node_id,
                    step=self.current_step,
                    category=cat_name,
                    world_xz=(wx, wz),
                    description=f"{cat_name} detected by GroundedSAM (conf={conf:.2f}, dist={d_val:.2f}m)",
                    confidence=conf,
                    source="grounded_sam",
                )
                if sent:
                    self._emit_event(
                        "TEAM_SIGHTING_BROADCAST",
                        self.current_step,
                        node_id=self.node_id,
                        category=cat_name,
                        world_xz=[round(wx, 3), round(wz, 3)],
                        confidence=round(conf, 3),
                        source="grounded_sam",
                    )
            if cat_name.lower() != self.destination.lower() and d_val <= 2.2:
                self.hub.record_opportunistic_sighting_claim(
                    sender=self.node_id,
                    step=self.current_step,
                    category=cat_name,
                    robot_world_xz=(robot_wx, robot_wz),
                    object_world_xz=(wx, wz),
                    distance_m=d_val,
                    confidence=conf,
                    source="grounded_sam",
                )

    def _recall_memory(
        self,
        step: int,
        agent_map_x: int,
        agent_map_y: int,
        available_waypoint_ids=None,
    ) -> str:
        if self.active_goal is not None:
            base_inst = str(self.active_goal.get("instruction") or f"Find the {self.destination}")
            if self.claimed_locations:
                wx, wz = self._current_global_xz()
                min_d = min(math.hypot(wx - cx, wz - cz) for cx, cz in self.claimed_locations)
                if min_d < 2.0:
                    self.instruction = (
                        f"{base_inst} in another room (the {self.destination} in this room is already recorded; "
                        f"navigate through the open doorway into the hallway to explore other rooms)"
                    )
                else:
                    self.instruction = (
                        f"{base_inst} in another room (do NOT return to rooms where a candidate "
                        f"{self.destination} has already been recorded)"
                    )
            else:
                self.instruction = base_inst
        own_memory = super()._recall_memory(
            step, agent_map_x, agent_map_y, available_waypoint_ids=available_waypoint_ids
        )
        team_memory = self.hub.format_team_memory_for_prompt(
            node_id=self.node_id,
            current_pose=self._local_pose(),
            start_sim_location=self.start_sim_location,
            active_goal_category=self.destination,
            step=step,
        )
        if own_memory and team_memory:
            return own_memory + "\n\n" + team_memory
        return own_memory or team_memory

    def _annotate_current_place(self, step: int) -> None:
        raw_summary = str(getattr(self.agent, "last_place_summary", "") or "")
        super()._annotate_current_place(step)
        if raw_summary:
            wx, wz = self._current_global_xz()
            objs = list(self.detected_classes)[-8:] if self.detected_classes else []
            self.hub.broadcast_place_observed(
                sender=self.node_id,
                step=step,
                world_xz=(wx, wz),
                place_summary=raw_summary,
                objects=objs,
                searched_goal=self.destination,
            )
            spawn_wx, spawn_wz = local_map_to_global_xz(12.0, 12.0, self.start_sim_location)
            summary_lower = raw_summary.lower()
            is_corridor = any(kw in summary_lower for kw in ("hallway", "staircase", "corridor"))
            if not is_corridor and math.hypot(wx - spawn_wx, wz - spawn_wz) >= 2.2:
                for cat in self.hub.all_goal_categories():
                    c_low = cat.lower()
                    if c_low == self.destination.lower():
                        continue
                    if c_low in summary_lower:
                        negated = re.search(rf"\b(no|not|without|neither)\b[^.;,]*\b{re.escape(c_low)}\b", summary_lower)
                        if not negated:
                            self.hub.broadcast_object_sighting(
                                sender=self.node_id,
                                step=step,
                                category=cat,
                                world_xz=(wx, wz),
                                description=f"Confirmed in room by {self.node_id}: {raw_summary[:100]}",
                                confidence=0.75,
                                source="vlm_place_summary",
                            )
                            self.hub.record_opportunistic_sighting_claim(
                                sender=self.node_id,
                                step=step,
                                category=cat,
                                robot_world_xz=(wx, wz),
                                object_world_xz=(wx, wz),
                                distance_m=1.0,
                                confidence=0.75,
                                source="vlm_place_summary",
                            )

    def _record_grounded_target(
        self,
        step: int,
        target_map_x: int,
        target_map_y: int,
        target_world_x: float,
        target_world_y: float,
        bbox: dict,
        source: str,
        planned_action: str,
    ) -> None:
        robot_wx, robot_wz = self._current_global_xz()
        wx, wz = local_map_to_global_xz(
            float(target_world_x), float(target_world_y), self.start_sim_location
        )
        desc = str((bbox.get("target") if isinstance(bbox, dict) else "") or "")
        nav = getattr(self, "_nav_state", None)

        # If a teammate has broadcast a confirmed 3D sighting for this robot's active goal
        # in another room (>1.6m from claimed_locations), project it into this robot's local FMM map.
        if nav is not None:
            best_sighting = self.hub.get_best_sighting_for_category(
                category=self.destination,
                current_pose=self._local_pose(),
                start_sim_location=self.start_sim_location,
                exclude_sender=self.node_id,
                exclude_locations=self.claimed_locations,
            )
            if best_sighting is not None:
                sx, sz = best_sighting["world_xz"]
                if math.hypot(wx - sx, wz - sz) > 1.8:
                    orig_lx_m, orig_ly_m = best_sighting["local_map_m"]
                    lx_m, ly_m = orig_lx_m, orig_ly_m
                    mx, my = self._world_to_map_cell((lx_m, ly_m))
                    if np.any(self.traversable) and self.traversable[int(my), int(mx)] == 0:
                        from platforms.hm3d_m3.navigation.map_utils import get_nearest_nonzero_waypoint
                        sy, sx_cell = get_nearest_nonzero_waypoint(self.traversable, (int(my), int(mx)))
                        mx, my = int(sx_cell), int(sy)
                        lx_m, ly_m = self._map_cell_to_world_m(mx, my)
                    snapped_dist = math.hypot(lx_m - orig_lx_m, ly_m - orig_ly_m)
                    nav.target_map_x, nav.target_map_y = int(mx), int(my)
                    target_map_x, target_map_y = int(mx), int(my)
                    target_world_x, target_world_y = float(lx_m), float(ly_m)
                    wx, wz = local_map_to_global_xz(float(lx_m), float(ly_m), self.start_sim_location)
                    if snapped_dist <= 1.0 and math.hypot(robot_wx - sx, robot_wz - sz) <= 3.0:
                        nav.planned_action = "STOP"
                        nav.going_to_stop = True
                        planned_action = "STOP"
                    else:
                        nav.planned_action = "NAVIGATE"
                        nav.going_to_stop = False
                        planned_action = "NAVIGATE"

        super()._record_grounded_target(
            step=step,
            target_map_x=target_map_x,
            target_map_y=target_map_y,
            target_world_x=target_world_x,
            target_world_y=target_world_y,
            bbox=bbox,
            source=source,
            planned_action=planned_action,
        )
        self.hub.broadcast_intent(
            sender=self.node_id,
            step=step,
            active_goal=self.destination,
            target_world_xz=(wx, wz),
            description=desc,
            planned_action=planned_action,
        )
        # Only broadcast sighting if the grounded description mentions a TEAMMATE's goal category
        for cat in self.hub.all_goal_categories():
            if cat.lower() != self.destination.lower() and cat.lower() in desc.lower():
                self.hub.broadcast_object_sighting(
                    sender=self.node_id,
                    step=step,
                    category=cat,
                    world_xz=(wx, wz),
                    description=desc,
                    confidence=0.80 if planned_action == "STOP" else 0.70,
                    source="vlm_grounding",
                )

    def _stop_geometric_gate(
        self,
        step: int,
        current_pose: np.ndarray,
        agent_map_x: int,
        agent_map_y: int,
        reason: str,
        stop_evidence=None,
        panorama_frames=None,
    ):
        wx, wz = self._current_global_xz()
        for cx, cz in self.claimed_locations:
            d_prev = math.hypot(wx - cx, wz - cz)
            if d_prev < 1.6:
                return False, {
                    "reason": reason,
                    "fail_reason": "already_claimed_in_this_room",
                    "distance_m": round(float(d_prev), 3),
                    "threshold_m": 1.6,
                }
        return super()._stop_geometric_gate(
            step=step,
            current_pose=current_pose,
            agent_map_x=agent_map_x,
            agent_map_y=agent_map_y,
            reason=reason,
            stop_evidence=stop_evidence,
            panorama_frames=panorama_frames,
        )

    def _harness_step(self, actions, step: int, tool_name=None, reason=None, metadata=None):
        values = [action.get("action") if isinstance(action, dict) else action for action in actions]
        is_stop_action = any(int(v) == 0 for v in values)

        if is_stop_action and self.active_goal is not None:
            wx, wz = self._current_global_xz()
            self.claimed_locations.append((wx, wz))
            all_done = self.hub.record_goal_found(
                sender=self.node_id,
                step=step,
                goal_id=self.active_goal["goal_id"],
                world_xz=(wx, wz),
                gate_info=metadata if isinstance(metadata, dict) else {},
            )
            self._emit_event(
                "TEAM_GOAL_FOUND",
                step,
                node_id=self.node_id,
                goal_id=self.active_goal["goal_id"],
                category=self.destination,
                world_xz=[round(wx, 3), round(wz, 3)],
                all_goals_found=bool(all_done),
            )
            max_ep_steps = int(self.config.TASK_CONFIG.ENVIRONMENT.MAX_EPISODE_STEPS)
            # 1. If there is another unclaimed PENDING goal, switch to it.
            if reason != "budget_autostop" and not all_done:
                if self._assign_or_switch_goal(step + 1, is_initial=False):
                    outputs = super()._harness_step(
                        actions, step, tool_name=tool_name, reason=reason, metadata=metadata
                    )
                    obs, rewards, _, infos = [list(x) for x in zip(*outputs)]
                    return [(obs[0], rewards[0], False, infos[0])]
            # 2. Otherwise, if budget remains, keep exploring OTHER rooms (>2.5m away)
            # to verify any additional instances of this robot's assigned goal category.
            if reason != "budget_autostop" and step < max_ep_steps - 60:
                self.hub.robot_states[self.node_id]["active_goal_id"] = self.active_goal["goal_id"]
                self.hub.robot_states[self.node_id]["active_category"] = self.destination
                self.stop_feedback = (
                    f"System: Candidate '{self.destination}' #{len(self.claimed_locations)} in this room has been recorded! "
                    f"Do NOT stop in this room again. Leave this room through the doorway/hallway to explore OTHER rooms "
                    f"for additional '{self.destination}' instances (or navigate toward teammate sightings in other rooms)."
                )
                self.agent.todo.reset()
                self.history_images.clear()
                self.last_grounded_target = None
                self.last_stop_rejection_step = step
                outputs = super()._harness_step(
                    actions, step, tool_name=tool_name, reason=reason, metadata=metadata
                )
                obs, rewards, _, infos = [list(x) for x in zip(*outputs)]
                return [(obs[0], rewards[0], False, infos[0])]

        outputs = super()._harness_step(
            actions, step, tool_name=tool_name, reason=reason, metadata=metadata
        )
        if is_stop_action:
            obs, rewards, _, infos = [list(x) for x in zip(*outputs)]
            return [(obs[0], rewards[0], True, infos[0])]
        return outputs
