"""Standalone single-robot worker process for Step-1 intra-machine IPC verification.

Each worker runs in its own OS process, connects only via ``http://127.0.0.1``
endpoints (`--env-endpoint` and `--comm-endpoint`), and executes its own
`AgentSession` + `Toolkit` + `Planner`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any, Dict, Optional, Tuple

from comm_harness.communication import LocalhostCommClient
from comm_harness.planner import FlashPlanner, build_planner
from comm_harness.session import AgentSession, EnvState
from platforms.grid_world import build_grid_toolkit


def _step_toward(curr: Tuple[int, int], goal: Tuple[int, int]) -> Optional[str]:
    cx, cy = curr
    gx, gy = goal
    if abs(gx - cx) + abs(gy - cy) <= 1:
        return None
    if gx > cx:
        return "east"
    if gx < cx:
        return "west"
    if gy > cy:
        return "north"
    if gy < cy:
        return "south"
    return None


def make_cooperative_policy(node_id: str, assigned_target: str, patrol_goal: Tuple[int, int]):
    """Deterministic tool-calling policy used by ``FlashPlanner`` to verify the
    full ``AgentSession <-> Toolkit <-> LocalhostCommServer <-> GridEnvServer`` loop.
    """

    def _runner(toolkit: Any, recipe_tag: str, note: Optional[str]) -> Dict[str, Any]:
        known_target_pos: Optional[Tuple[int, int]] = None
        broadcasted_categories = set()

        for _ in range(16):
            # 1. Sense local environment
            obs_res = toolkit.execute_tool("observe_local", {})
            obs = obs_res.data
            pose = (int(obs["pose"][0]), int(obs["pose"][1]))

            for item in obs.get("visible_objects", ()):
                cat = item["category"]
                pos = (int(item["pos"][0]), int(item["pos"][1]))
                if cat == assigned_target:
                    known_target_pos = pos
                elif cat not in broadcasted_categories:
                    # Opportunistic sighting of a teammate's target -> broadcast over localhost IPC!
                    toolkit.execute_tool(
                        "comm_broadcast",
                        {
                            "topic": "target_sighting",
                            "payload": {
                                "category": cat,
                                "pos": list(pos),
                                "spotted_by": node_id,
                            },
                        },
                    )
                    broadcasted_categories.add(cat)

            # 2. Poll inbox for teammate sightings
            poll_res = toolkit.execute_tool("comm_poll", {"max_messages": 20, "topic": "target_sighting"})
            for msg in poll_res.data.get("messages", ()):
                payload = msg.get("payload") or {}
                if payload.get("category") == assigned_target and "pos" in payload:
                    known_target_pos = (int(payload["pos"][0]), int(payload["pos"][1]))

            # 3. If within 1 cell of our assigned target, claim & finish!
            if known_target_pos is not None:
                if abs(known_target_pos[0] - pose[0]) + abs(known_target_pos[1] - pose[1]) <= 1:
                    claim_res = toolkit.execute_tool("claim_found", {"category": assigned_target})
                    toolkit.execute_tool(
                        "comm_broadcast",
                        {
                            "topic": "goal_completed",
                            "payload": {"category": assigned_target, "pose": list(pose)},
                        },
                    )
                    return claim_res.data

            # 4. Move toward known target sighting if available; otherwise explore patrol_goal
            dest = known_target_pos if known_target_pos is not None else patrol_goal
            direction = _step_toward(pose, dest)
            if direction is None:
                # Reached patrol_goal without receiving sighting yet; wait briefly & poll again
                time.sleep(0.03)
                toolkit.execute_tool("move_grid", {"direction": "stay"})
            else:
                toolkit.execute_tool("move_grid", {"direction": direction})

        return {"status": "max_steps_reached", "assigned_target": assigned_target}

    return _runner


def run_robot_worker(
    *,
    node_id: str,
    assigned_target: str,
    patrol_goal: Tuple[int, int],
    env_endpoint: str,
    comm_endpoint: str,
    output_dir: Path,
    planner_type: str = "flash",
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    env_state = EnvState(output_dir)
    comm_client = LocalhostCommClient(comm_endpoint, node_id=node_id)
    toolkit = build_grid_toolkit(
        env_endpoint=env_endpoint,
        node_id=node_id,
        comm_endpoint=comm_client,
        env_state=env_state,
    )

    if planner_type == "flash":
        planner = FlashPlanner(
            runner=make_cooperative_policy(node_id, assigned_target, patrol_goal),
            recipe_tag=f"coop_{node_id}",
            robot_name="grid_world",
        )
    else:
        planner = build_planner(
            planner_type,
            model=model,
            base_url=base_url,
            api_key=api_key,
            output_dir=output_dir,
            robot_name="grid_world",
            node_id=node_id,
            no_images=True,
        )

    system_prompt = (
        f"You are {node_id} in a 5x5 grid world (x in 0..4, y in 0..4). "
        f"Your assigned target is '{assigned_target}'. "
        "1) Call observe_local to inspect your pose and nearby objects. "
        "2) If you see an object that is NOT your target, call comm_broadcast with topic='target_sighting' "
        "and payload={'category': <name>, 'pos': [x, y]} so your teammate knows where it is! "
        "3) Call comm_poll to check if a teammate already spotted your target. "
        "4) Use move_grid('north'|'south'|'east'|'west') to reach within Manhattan distance <= 1 of your target, "
        "then call claim_found(category)."
    )
    user_message = f"Find and claim '{assigned_target}'. Collaborate with your teammate via comm_broadcast and comm_poll."

    session = AgentSession(
        run_dir=output_dir,
        toolkit=toolkit,
        planner=planner,
        env_state=env_state,
        node_id=node_id,
        episode_id="step1_coop_grid",
    )
    result = session.solve(system_prompt=system_prompt, user_message=user_message, max_turns=20)
    (output_dir / "worker_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Single-robot IPC worker")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--patrol-x", type=int, default=2)
    parser.add_argument("--patrol-y", type=int, default=2)
    parser.add_argument("--env-endpoint", required=True)
    parser.add_argument("--comm-endpoint", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--planner", default="flash", choices=["flash", "api"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    run_robot_worker(
        node_id=args.node_id,
        assigned_target=args.target,
        patrol_goal=(args.patrol_x, args.patrol_y),
        env_endpoint=args.env_endpoint,
        comm_endpoint=args.comm_endpoint,
        output_dir=Path(args.output_dir),
        planner_type=args.planner,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
    )


if __name__ == "__main__":
    main()
