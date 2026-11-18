"""Collaborative Memory Hub and Multi-Goal Task Board over IdealTransport.

Connects N independent ``RobotHarnessVLN`` instances via structured messages:
1. ``PLACE_OBSERVED``: Shares newly summarized WorldGraph Place nodes in global XZ.
2. ``OBJECT_SIGHTING``: Broadcasts 3D global XZ coordinates when any robot spots a team goal.
3. ``INTENT_CLAIM``: Shares current navigation waypoint & active goal for spatial de-confliction.
4. ``GOAL_ASSIGNED`` / ``GOAL_FOUND``: Coordinates dynamic multi-goal division across robots.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from comm_harness.communication import IdealTransport
from platforms.hm3d_m3.habitat.pose_utils import (
    global_xz_to_local_map,
    local_map_to_global_xz,
)


def relative_distance_and_direction(
    current_pose: np.ndarray,
    start_sim_location: tuple,
    target_wx: float,
    target_wz: float,
    map_origin_m: float = 12.0,
) -> Tuple[float, str, Tuple[float, float]]:
    """Compute distance (m), ego cardinal direction ('forward'|'left'|'right'|'behind'),
    and local map coordinates (mx, my) in metres for a global (wx, wz) point."""
    tx_m, ty_m = global_xz_to_local_map(target_wx, target_wz, start_sim_location, map_origin_m=map_origin_m)
    ax_m = float(current_pose[0])
    ay_m = float(current_pose[1])
    ah_rad = np.deg2rad(float(current_pose[2]))

    dx = tx_m - ax_m
    dy = ty_m - ay_m
    dist_m = float(math.hypot(dx, dy))

    # Transform to robot body frame (same convention as evaluator.py NavDP/FMM viz)
    x_body = dx * math.cos(ah_rad) + dy * math.sin(ah_rad)
    y_body = -dx * math.sin(ah_rad) + dy * math.cos(ah_rad)
    angle_deg = math.degrees(math.atan2(y_body, x_body))  # 0=forward, +90=left, -90=right

    if -45.0 <= angle_deg <= 45.0:
        direction = "forward"
    elif 45.0 < angle_deg <= 135.0:
        direction = "left"
    elif -135.0 <= angle_deg < -45.0:
        direction = "right"
    else:
        direction = "behind"

    return dist_m, direction, (tx_m, ty_m)


class CollaborativeMemoryHub:
    """Thread-safe multi-robot communication hub and shared memory projection over IdealTransport."""

    def __init__(
        self,
        episode_id: str,
        node_names: List[str],
        goals: List[Dict[str, Any]],
        emit_event: Callable[..., None],
        allow_cosearch_after_finish: bool = False,
    ) -> None:
        self.episode_id = str(episode_id)
        self.node_names = list(node_names)
        self.emit_event = emit_event
        self.allow_cosearch_after_finish = bool(allow_cosearch_after_finish)
        self.lock = threading.RLock()
        self.transport = IdealTransport(self.episode_id, self._on_transport_event)

        # Public goals table (never stores oracle reach_points)
        self.goals: Dict[str, Dict[str, Any]] = {}
        self.goal_order: List[str] = []
        for g in goals:
            gid = str(g["goal_id"])
            self.goal_order.append(gid)
            self.goals[gid] = {
                "goal_id": gid,
                "goal_index": int(g["goal_index"]),
                "category": str(g["category"]),
                "modality": str(g.get("modality", "ON")),
                "instruction": str(g["instruction"]),
                "status": "PENDING",  # PENDING | ACTIVE | FOUND
                "owner": None,
                "co_owners": [],
                "attempted_by": [],
                "found_by": None,
                "found_step": None,
                "found_world_xz": None,
            }

        # Shared structured memory stores (populated via IdealTransport messages)
        self.robot_states: Dict[str, Dict[str, Any]] = {
            name: {
                "node_id": name,
                "active_goal_id": None,
                "active_category": None,
                "world_xz": None,
                "target_world_xz": None,
                "target_description": None,
                "planned_action": "NAVIGATE",
                "step": 0,
                "halted": False,
            }
            for name in self.node_names
        }
        self.shared_places: List[Dict[str, Any]] = []
        self.object_sightings: List[Dict[str, Any]] = []
        self.verified_claims: List[Dict[str, Any]] = []
        self.message_log: List[Dict[str, Any]] = []

    def _on_transport_event(self, event: str, **fields: Any) -> None:
        if self.emit_event is not None:
            self.emit_event(event, **fields)

    def all_goal_categories(self) -> List[str]:
        with self.lock:
            cats = []
            for gid in self.goal_order:
                c = self.goals[gid]["category"]
                if c not in cats:
                    cats.append(c)
            return cats

    def unfinished_goal_categories(self) -> List[str]:
        with self.lock:
            cats = []
            for gid in self.goal_order:
                g = self.goals[gid]
                if g["status"] != "FOUND" and g["category"] not in cats:
                    cats.append(g["category"])
            return cats

    def all_goals_found(self) -> bool:
        with self.lock:
            return all(g["status"] == "FOUND" for g in self.goals.values())

    def claim_initial_or_next_goal(
        self,
        node_id: str,
        current_world_xz: Optional[Tuple[float, float]],
        step: int,
    ) -> Optional[Dict[str, Any]]:
        """Assign an unclaimed goal to ``node_id`` (or a collaborative co-search goal if all remaining are active)."""
        with self.lock:
            # 1. Check if this robot already has an active goal it hasn't attempted to stop on yet
            curr_gid = self.robot_states.get(node_id, {}).get("active_goal_id")
            if curr_gid and curr_gid in self.goals:
                g = self.goals[curr_gid]
                if g["status"] == "ACTIVE" and node_id not in g["attempted_by"]:
                    return dict(g)

            # 2. Look for PENDING (unassigned) goals; prefer one with a known sighting close to node_id
            pending = [
                self.goals[gid]
                for gid in self.goal_order
                if self.goals[gid]["status"] == "PENDING" and node_id not in self.goals[gid]["attempted_by"]
            ]
            chosen = None
            role = "primary"
            if pending:
                if current_world_xz is not None and self.object_sightings:
                    best_dist = float("inf")
                    for cand in pending:
                        for s in self.object_sightings:
                            if s["category"].lower() == cand["category"].lower():
                                d = math.hypot(
                                    s["world_xz"][0] - current_world_xz[0],
                                    s["world_xz"][1] - current_world_xz[1],
                                )
                                if d < best_dist:
                                    best_dist = d
                                    chosen = cand
                if chosen is None:
                    chosen = pending[0]
                chosen["status"] = "ACTIVE"
                chosen["owner"] = node_id
            else:
                # 3. Co-search fallback: only if initial assignment (step == 0 and more robots than goals)
                # or if allow_cosearch_after_finish is enabled.
                active_cands = [
                    self.goals[gid]
                    for gid in self.goal_order
                    if self.goals[gid]["status"] == "ACTIVE" and node_id not in self.goals[gid]["attempted_by"]
                ]
                if not active_cands or (step > 0 and not self.allow_cosearch_after_finish):
                    if self.robot_states.get(node_id):
                        self.robot_states[node_id]["halted"] = True
                        self.robot_states[node_id]["active_goal_id"] = None
                        self.robot_states[node_id]["active_category"] = None
                        self.robot_states[node_id]["target_world_xz"] = None
                        self.robot_states[node_id]["target_description"] = None
                    return None
                chosen = active_cands[0]
                role = "co_search"
                if node_id not in chosen["co_owners"] and chosen["owner"] != node_id:
                    chosen["co_owners"].append(node_id)

            self.robot_states[node_id]["active_goal_id"] = chosen["goal_id"]
            self.robot_states[node_id]["active_category"] = chosen["category"]
            self.robot_states[node_id]["target_world_xz"] = None
            self.robot_states[node_id]["target_description"] = None
            self.robot_states[node_id]["step"] = int(step)

            payload = {
                "goal_id": chosen["goal_id"],
                "goal_index": chosen["goal_index"],
                "category": chosen["category"],
                "instruction": chosen["instruction"],
                "owner": chosen["owner"],
                "assigned_to": node_id,
                "role": role,
                "step": int(step),
            }
            self._broadcast(node_id, "GOAL_ASSIGNED", payload, step)
            return dict(chosen)

    def record_goal_found(
        self,
        sender: str,
        step: int,
        goal_id: str,
        world_xz: Tuple[float, float],
        gate_info: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark ``goal_id`` as FOUND, broadcast to teammates, and return True if all goals are now FOUND."""
        with self.lock:
            g = self.goals.get(goal_id)
            if g is None:
                return self.all_goals_found()
            if sender not in g["attempted_by"]:
                g["attempted_by"].append(sender)
            found_xz = [round(float(world_xz[0]), 3), round(float(world_xz[1]), 3)]
            claim_record = {
                "node_id": sender,
                "goal_id": goal_id,
                "goal_index": g["goal_index"],
                "category": g["category"],
                "step": int(step),
                "world_xz": found_xz,
                "gate_info": gate_info or {},
            }
            self.verified_claims.append(claim_record)
            if g["status"] != "FOUND":
                g["status"] = "FOUND"
                g["found_by"] = sender
                g["found_step"] = int(step)
                g["found_world_xz"] = found_xz
                self._broadcast(sender, "GOAL_FOUND", claim_record, step)
            if self.robot_states.get(sender):
                self.robot_states[sender]["active_goal_id"] = None
                self.robot_states[sender]["active_category"] = None
                self.robot_states[sender]["target_world_xz"] = None
                self.robot_states[sender]["target_description"] = None
            return self.all_goals_found()

    def record_opportunistic_sighting_claim(
        self,
        sender: str,
        step: int,
        category: str,
        robot_world_xz: Tuple[float, float],
        object_world_xz: Tuple[float, float],
        distance_m: float,
        confidence: float = 0.7,
        source: str = "collaborative_sighting",
    ) -> bool:
        """Record when ``sender`` opportunistically verifies a teammate's goal category at close range."""
        cat_clean = str(category).strip().lower()
        if not cat_clean:
            return False
        with self.lock:
            matched_goal = None
            for gid in self.goal_order:
                g = self.goals[gid]
                g_cat = g["category"].lower()
                if g_cat in cat_clean or cat_clean in g_cat:
                    matched_goal = g
                    break
            if matched_goal is None:
                return False

            r_xz = [round(float(robot_world_xz[0]), 3), round(float(robot_world_xz[1]), 3)]
            o_xz = [round(float(object_world_xz[0]), 3), round(float(object_world_xz[1]), 3)]
            # Deduplicate close-by opportunistic claims for the same goal within 1.0m
            for existing in self.verified_claims:
                if existing.get("goal_id") == matched_goal["goal_id"] and existing.get("node_id") == sender:
                    ex_xz = existing.get("world_xz", [0.0, 0.0])
                    if math.hypot(ex_xz[0] - r_xz[0], ex_xz[1] - r_xz[1]) < 1.0:
                        return False

            claim_record = {
                "node_id": sender,
                "goal_id": matched_goal["goal_id"],
                "goal_index": matched_goal["goal_index"],
                "category": matched_goal["category"],
                "step": int(step),
                "world_xz": r_xz,
                "object_world_xz": o_xz,
                "distance_m": round(float(distance_m), 3),
                "confidence": round(float(confidence), 3),
                "role": "opportunistic_teammate_verification",
                "gate_info": {"source": source, "distance_m": round(float(distance_m), 3), "confidence": round(float(confidence), 3)},
            }
            self.verified_claims.append(claim_record)
            self._broadcast(sender, "COLLABORATIVE_GOAL_VERIFIED", claim_record, step)
            return True

    def update_robot_pose(self, node_id: str, step: int, world_xz: Tuple[float, float]) -> None:
        with self.lock:
            if node_id in self.robot_states:
                self.robot_states[node_id]["world_xz"] = [round(float(world_xz[0]), 3), round(float(world_xz[1]), 3)]
                self.robot_states[node_id]["step"] = int(step)

    def broadcast_place_observed(
        self,
        sender: str,
        step: int,
        world_xz: Tuple[float, float],
        place_summary: str,
        objects: List[str],
        searched_goal: str,
    ) -> None:
        if not place_summary:
            return
        with self.lock:
            wx, wz = round(float(world_xz[0]), 3), round(float(world_xz[1]), 3)
            # Merge if sender or another robot already summarized a place within 1.0m
            for existing in self.shared_places:
                if math.hypot(existing["world_xz"][0] - wx, existing["world_xz"][1] - wz) < 1.0:
                    existing["step"] = int(step)
                    existing["place_summary"] = str(place_summary)[:220]
                    existing.setdefault("visited_by", {})[sender] = int(step)
                    for obj in objects:
                        if obj and obj not in existing["objects"]:
                            existing["objects"].append(obj)
                    if searched_goal and searched_goal not in existing["searched_goals"]:
                        existing["searched_goals"].append(searched_goal)
                    self._broadcast(sender, "PLACE_OBSERVED", existing, step)
                    return

            entry = {
                "place_id": f"shared_place_{len(self.shared_places) + 1}",
                "sender": sender,
                "first_step": int(step),
                "step": int(step),
                "world_xz": [wx, wz],
                "place_summary": str(place_summary)[:220],
                "objects": [str(o) for o in objects[:8] if o],
                "searched_goals": [searched_goal] if searched_goal else [],
                "visited_by": {sender: int(step)},
            }
            self.shared_places.append(entry)
            self._broadcast(sender, "PLACE_OBSERVED", entry, step)

    def broadcast_object_sighting(
        self,
        sender: str,
        step: int,
        category: str,
        world_xz: Tuple[float, float],
        description: str = "",
        confidence: float = 0.8,
        source: str = "vlm",
    ) -> bool:
        """Broadcast a 3D global coordinate sighting of a team goal category."""
        cat_clean = str(category).strip().lower()
        if not cat_clean:
            return False
        with self.lock:
            # Check if this category matches any team goal
            matched_goal = None
            for gid in self.goal_order:
                g = self.goals[gid]
                g_cat = g["category"].lower()
                if g_cat in cat_clean or cat_clean in g_cat:
                    matched_goal = g
                    break
            if matched_goal is None:
                return False

            wx, wz = round(float(world_xz[0]), 3), round(float(world_xz[1]), 3)
            # Deduplicate nearby sightings of the same category within 1.2m
            for existing in self.object_sightings:
                if existing["category"].lower() == matched_goal["category"].lower():
                    if math.hypot(existing["world_xz"][0] - wx, existing["world_xz"][1] - wz) < 1.2:
                        if confidence >= existing.get("confidence", 0.0):
                            existing["world_xz"] = [wx, wz]
                            existing["confidence"] = round(float(confidence), 3)
                            existing["step"] = int(step)
                            if description:
                                existing["description"] = str(description)[:140]
                        return False

            sighting = {
                "sighting_id": f"sighting_{len(self.object_sightings) + 1}",
                "sender": sender,
                "step": int(step),
                "goal_id": matched_goal["goal_id"],
                "category": matched_goal["category"],
                "world_xz": [wx, wz],
                "description": str(description or matched_goal["category"])[:140],
                "confidence": round(float(confidence), 3),
                "source": source,
            }
            self.object_sightings.append(sighting)
            self._broadcast(sender, "OBJECT_SIGHTING", sighting, step)
            return True

    def broadcast_intent(
        self,
        sender: str,
        step: int,
        active_goal: str,
        target_world_xz: Tuple[float, float],
        description: str,
        planned_action: str,
    ) -> None:
        with self.lock:
            wx, wz = round(float(target_world_xz[0]), 3), round(float(target_world_xz[1]), 3)
            if sender in self.robot_states:
                self.robot_states[sender]["target_world_xz"] = [wx, wz]
                self.robot_states[sender]["target_description"] = str(description)[:120]
                self.robot_states[sender]["planned_action"] = str(planned_action)
                self.robot_states[sender]["step"] = int(step)
            payload = {
                "sender": sender,
                "step": int(step),
                "active_goal": active_goal,
                "target_world_xz": [wx, wz],
                "description": str(description)[:120],
                "planned_action": str(planned_action),
            }
            self._broadcast(sender, "INTENT_CLAIM", payload, step)

    def _broadcast(self, sender: str, kind: str, payload: Dict[str, Any], step: int) -> None:
        recipients = [n for n in self.node_names if n != sender]
        if recipients:
            self.transport.send(sender, recipients, kind, payload, int(step), ttl_ticks=1000)
        self.message_log.append({
            "step": int(step),
            "sender": sender,
            "recipients": recipients,
            "kind": kind,
            "payload": payload,
            "ts": time.time(),
        })

    def deliver_tick(self, tick: int) -> Dict[str, List[Dict[str, Any]]]:
        with self.lock:
            return self.transport.deliver(int(tick))

    def get_best_sighting_for_category(
        self,
        category: str,
        current_pose: np.ndarray,
        start_sim_location: tuple,
        map_origin_m: float = 12.0,
        exclude_sender: Optional[str] = None,
        exclude_locations: Optional[List[Tuple[float, float]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the best sighting for ``category`` projected into the caller's local map."""
        cat_lower = str(category).strip().lower()
        excl_locs = exclude_locations or []
        with self.lock:
            candidates = [
                s for s in self.object_sightings
                if s["category"].lower() == cat_lower
                and (exclude_sender is None or s.get("sender") != exclude_sender)
                and all(math.hypot(s["world_xz"][0] - cx, s["world_xz"][1] - cz) > 1.6 for cx, cz in excl_locs)
            ]
            if not candidates:
                return None
            # Prefer highest confidence, then most recent
            best = sorted(candidates, key=lambda s: (s.get("confidence", 0.0), s.get("step", 0)), reverse=True)[0]
            wx, wz = best["world_xz"]
            dist_m, direction, (lx_m, ly_m) = relative_distance_and_direction(
                current_pose, start_sim_location, wx, wz, map_origin_m=map_origin_m
            )
            return {
                **best,
                "distance_m": round(dist_m, 2),
                "relative_direction": direction,
                "local_map_m": (lx_m, ly_m),
            }

    def format_team_memory_for_prompt(
        self,
        node_id: str,
        current_pose: np.ndarray,
        start_sim_location: tuple,
        active_goal_category: str,
        step: int,
        map_origin_m: float = 12.0,
    ) -> str:
        """Render the ``[TEAM COLLABORATION & SHARED MEMORY]`` block for ``node_id``'s VLM prompt."""
        with self.lock:
            lines: List[str] = ["[TEAM COLLABORATION & SHARED MEMORY]"]

            # 1. Team Goal Board Summary
            goal_summaries = []
            for gid in self.goal_order:
                g = self.goals[gid]
                owner = g.get("owner")
                still_searching = (
                    owner is not None
                    and self.robot_states.get(owner, {}).get("active_goal_id") == gid
                )
                owner_tag = f"owner={owner}" if owner else "unassigned"
                status_str = g["status"]
                if g["status"] == "FOUND" and not still_searching:
                    owner_tag = f"FOUND by {g['found_by']} at step {g['found_step']}"
                elif g["status"] == "FOUND" and still_searching:
                    status_str = "ACTIVE"
                goal_summaries.append(f"{gid}({g['category']}: {status_str}, {owner_tag})")
            lines.append("Team Goals: " + " | ".join(goal_summaries))

            teammate_cats = [
                self.goals[gid]["category"]
                for gid in self.goal_order
                if self.goals[gid]["category"].lower() != active_goal_category.lower()
            ]
            if teammate_cats:
                lines.append(
                    f"Note: While searching for your target ('{active_goal_category}'), if you spot any teammate target "
                    f"({', '.join(repr(c) for c in teammate_cats)}) in the views, mention its category and direction in your `place_summary` so it is shared with teammates."
                )
            lines.append(
                "Exploration Rule: Never mark an open room (such as a dining room, desk area, or living room visible through an open archway) "
                "as searched just by glancing from a hallway — stone pillars and walls hide objects inside! "
                "Always prioritize turning into wide-open room archways BEFORE walking down narrow corridors lined with closed wooden doors."
            )

            # 2. High-priority Sightings for THIS robot's active goal (excluding already-claimed or already-checked locations)
            claimed_for_cat_xz = [
                c["world_xz"]
                for c in self.verified_claims
                if c.get("world_xz") is not None
                and str(c.get("category", "")).lower() == active_goal_category.lower()
                and c.get("role") != "opportunistic_teammate_verification"
            ]
            my_sightings = [
                s for s in self.object_sightings
                if s["sender"] != node_id
                and s["category"].lower() == active_goal_category.lower()
                and all(math.hypot(s["world_xz"][0] - cx, s["world_xz"][1] - cz) > 1.6 for cx, cz in claimed_for_cat_xz)
                and not any(
                    math.hypot(s["world_xz"][0] - p["world_xz"][0], s["world_xz"][1] - p["world_xz"][1]) < 2.0
                    and p.get("visited_by", {}).get(node_id, -1) > int(s["step"]) + 3
                    for p in self.shared_places
                )
            ]
            if my_sightings:
                lines.append("🎯 CONFIRMED TARGET SIGHTINGS FOR YOUR GOAL:")
                for s in my_sightings[-3:]:
                    dist_m, direction, _ = relative_distance_and_direction(
                        current_pose, start_sim_location, s["world_xz"][0], s["world_xz"][1], map_origin_m=map_origin_m
                    )
                    lines.append(
                        f"  - {s['sender']} spotted '{s['category']}' ({s['description']}) at {dist_m:.1f}m "
                        f"to your {direction.upper()} (step {s['step']}, source={s['source']}). "
                        f" Prioritize navigating {direction.upper()} toward it!"
                    )

            # 3. Teammate live positions & navigation intents (for spatial de-confliction)
            teammate_lines = []
            for other_id, st in self.robot_states.items():
                if other_id == node_id or st.get("halted") or not st.get("active_category"):
                    continue
                pos_str = "unknown"
                if st.get("world_xz") is not None:
                    d_pos, dir_pos, _ = relative_distance_and_direction(
                        current_pose, start_sim_location, st["world_xz"][0], st["world_xz"][1], map_origin_m=map_origin_m
                    )
                    pos_str = f"{d_pos:.1f}m {dir_pos}"
                intent_str = "exploring"
                if st.get("target_world_xz") is not None:
                    d_tgt, dir_tgt, _ = relative_distance_and_direction(
                        current_pose, start_sim_location, st["target_world_xz"][0], st["target_world_xz"][1], map_origin_m=map_origin_m
                    )
                    intent_str = f"heading {dir_tgt} ({d_tgt:.1f}m from you) toward '{st.get('target_description') or 'waypoint'}'"
                teammate_lines.append(
                    f"  - {other_id} (searching for '{st.get('active_category')}'): currently {pos_str}; {intent_str}"
                )
            if teammate_lines:
                lines.append("Teammate Status & Intents (spread out if co-searching the same target):")
                lines.extend(teammate_lines)

            # 4. Nearby rooms already scanned by teammates or earlier by this robot (d_pl <= 4.5m)
            searched_places = []
            for p in self.shared_places:
                if any(kw in str(p.get("place_summary", "")).lower() for kw in ("hallway", "staircase", "corridor")):
                    continue
                d_pl, dir_pl, _ = relative_distance_and_direction(
                    current_pose, start_sim_location, p["world_xz"][0], p["world_xz"][1], map_origin_m=map_origin_m
                )
                if d_pl > 4.5:
                    continue
                first_st = int(p.get("first_step", p["step"]))
                if p["sender"] != node_id or (int(step) - first_st >= 15 and d_pl >= 1.5):
                    searched_places.append((p, d_pl, dir_pl))
            if searched_places:
                lines.append("Areas Already Searched (no target found there — avoid navigating back into these areas):")
                for p, d_pl, dir_pl in searched_places[-5:]:
                    lines.append(
                        f"  - [{p['place_id']} by {p['sender']}, {d_pl:.1f}m {dir_pl.upper()}]: Already searched by {p['sender']} (no '{active_goal_category}' found there) — avoid navigating {dir_pl.upper()} into that already-searched room when unexplored directions are available."
                    )

            return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "goals": [dict(self.goals[gid]) for gid in self.goal_order],
                "goals_total": len(self.goal_order),
                "goals_found": sum(1 for g in self.goals.values() if g["status"] == "FOUND"),
                "all_goals_found": self.all_goals_found(),
                "shared_places_count": len(self.shared_places),
                "object_sightings_count": len(self.object_sightings),
                "object_sightings": list(self.object_sightings),
                "verified_claims": list(self.verified_claims),
                "communication": dict(self.transport.stats),
            }
