"""Step-1 Runnable Demo: Multi-Process Intra-Machine Communication & Cooperative Search.

Usage:
    python3 -m tasks.coop_grid_search.demo
    python3 -m tasks.coop_grid_search.demo --channel impaired --drop-prob 0.2
    python3 -m tasks.coop_grid_search.demo --channel blackout
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, Optional

from comm_harness.communication import (
    BlackoutChannel,
    IdealChannel,
    ImpairedChannel,
    InMemoryCommBus,
    LocalhostCommServer,
)
from platforms.grid_world import GridEnvServer, GridWorldState


def run_coop_grid_demo(
    *,
    channel_mode: str = "ideal",
    drop_prob: float = 0.0,
    bandwidth_bps: Optional[float] = None,
    max_range_m: Optional[float] = None,
    run_dir: Optional[Path] = None,
    planner: str = "flash",
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Launch ``GridEnvServer`` + ``LocalhostCommServer`` on ``127.0.0.1`` and run
    two independent OS worker processes (`robot_0` and `robot_1`) to completion.
    """
    root = Path(__file__).resolve().parents[2]
    if run_dir is None:
        run_dir = root / "runs" / ("step1-ipc-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    # Scenario:
    # - robot_0 starts at (0, 0), assigned 'mirror' (located at (3, 4)), but starts right beside 'vase' (1, 0)!
    # - robot_1 starts at (4, 4), assigned 'vase' (located at (1, 0)), but starts right beside 'mirror' (3, 4)!
    # With intra-machine communication enabled, they immediately exchange sightings and walk straight to their goals!
    # Under blackout (No-Comm), patrolling only to (2, 2) will NOT find their targets at opposite corners!
    world = GridWorldState(
        width=5,
        height=5,
        robot_starts={"robot_0": (0, 0), "robot_1": (4, 4)},
        objects={"vase": (1, 0), "mirror": (3, 4)},
    )
    env_server = GridEnvServer(world)

    if channel_mode == "ideal":
        channel = IdealChannel()
    elif channel_mode == "blackout":
        channel = BlackoutChannel()
    elif channel_mode == "impaired":
        channel = ImpairedChannel(
            drop_prob=drop_prob,
            bandwidth_bytes_per_sec=bandwidth_bps,
            max_range_m=max_range_m,
            seed=7,
        )
    else:
        raise ValueError(f"Unsupported channel_mode: {channel_mode}")

    comm_events = []
    bus = InMemoryCommBus(
        episode_id="step1_coop_grid",
        channel=channel,
        emit=lambda event, **fields: comm_events.append({"event": event, **fields}),
    )
    comm_server = LocalhostCommServer(bus)

    env_ep = env_server.start_background()
    comm_ep = comm_server.start_background()

    try:
        # Pre-register both robot IDs on the broker so broadcast knows all peers immediately
        bus.register_node("robot_0", position=(0, 0))
        bus.register_node("robot_1", position=(4, 4))

        specs = [
            ("robot_0", "mirror", 2, 2),
            ("robot_1", "vase", 2, 2),
        ]
        procs = []
        env_vars = dict(os.environ)
        env_vars["PYTHONPATH"] = str(root) + (
            os.pathsep + env_vars["PYTHONPATH"] if env_vars.get("PYTHONPATH") else ""
        )

        for nid, target, px, py in specs:
            cmd = [
                sys.executable,
                "-m",
                "tasks.coop_grid_search.worker",
                "--node-id",
                nid,
                "--target",
                target,
                "--patrol-x",
                str(px),
                "--patrol-y",
                str(py),
                "--env-endpoint",
                env_ep,
                "--comm-endpoint",
                comm_ep,
                "--output-dir",
                str(run_dir / nid),
                "--planner",
                planner,
            ]
            if model:
                cmd += ["--model", model]
            if base_url:
                cmd += ["--base-url", base_url]
            if api_key:
                cmd += ["--api-key", api_key]
            procs.append((nid, subprocess.Popen(cmd, cwd=str(root), env=env_vars)))

        worker_Results: Dict[str, Any] = {}
        for nid, proc in procs:
            rc = proc.wait(timeout=60)
            if rc != 0:
                raise RuntimeError(f"Worker {nid} exited with code {rc}")
            res_file = run_dir / nid / "worker_result.json"
            worker_Results[nid] = json.loads(res_file.read_text(encoding="utf-8"))

        evaluation = world.evaluate_team()
        comm_summary = bus.summary()
        summary = {
            "run_dir": str(run_dir),
            "endpoints": {"env_server": env_ep, "comm_server": comm_ep},
            "evaluation": evaluation,
            "communication": comm_summary,
            "workers": {
                nid: {
                    "completed": w["completed"],
                    "harness": w["harness_lifecycle"]["harness"],
                }
                for nid, w in worker_Results.items()
            },
        }
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "comm_trace.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in comm_events) + ("\n" if comm_events else ""),
            encoding="utf-8",
        )
        return summary
    finally:
        comm_server.close()
        env_server.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Step-1 Multi-Process Localhost IPC Demo")
    parser.add_argument("--channel", default="ideal", choices=["ideal", "impaired", "blackout"])
    parser.add_argument("--drop-prob", type=float, default=0.0)
    parser.add_argument("--bandwidth-bps", type=float, default=None)
    parser.add_argument("--max-range-m", type=float, default=None)
    parser.add_argument("--planner", default="flash", choices=["flash", "api"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    out = run_coop_grid_demo(
        channel_mode=args.channel,
        drop_prob=args.drop_prob,
        bandwidth_bps=args.bandwidth_bps,
        max_range_m=args.max_range_m,
        planner=args.planner,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
