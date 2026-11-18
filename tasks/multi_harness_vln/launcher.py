"""Launch a multi-robot collaborative HarnessVLN session in an isolated GPU worker process."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Dict


def launch_multi_harness_vln(config: Dict[str, Any], run_dir: Path | str, key: str, preflight: bool = False) -> Dict[str, Any]:
    from comm_harness.planner.model_config import require_vision_model

    require_vision_model(config["planner"]["model"])
    root = Path(__file__).resolve().parents[2]
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    if "task_definition" in config:
        (run_dir / "task_definition.json").write_text(
            json.dumps(config["task_definition"], indent=2), encoding="utf-8"
        )

    source_hashes = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for parent in ("comm_harness", "platforms", "tasks")
        if (root / parent).exists()
        for p in (root / parent).rglob("*.py")
    }
    (run_dir / "source_manifest.json").write_text(json.dumps(source_hashes, indent=2), encoding="utf-8")

    env = os.environ.copy()
    env.update(
        MODEL_API_KEY=key,
        MODEL_BASE_URL=config["planner"]["base_url"],
        MODEL_NAME=config["planner"]["model"],
        MODEL_PROVIDER=config["planner"].get("provider", "openai"),
        MODEL_BBOX_SCALE=str(config["planner"].get("bbox_scale", "")),
        MODEL_REQUEST_TIMEOUT=str(config["planner"].get("request_timeout_s", 120)),
        OLLAMA_NUM_CTX=str(config["planner"].get("num_ctx", 32768)),
        OLLAMA_NUM_PREDICT=str(config["planner"].get("num_predict", 2048)),
        OLLAMA_SEED=str(config.get("seed", 1123)),
        MODEL_TOKEN_PARAMETER="max_tokens",
        COMM_HVL_MAX_CALLS=str(config["planner"].get("max_calls_per_robot", config["planner"].get("max_calls", 60))),
        COMM_HVL_MAX_TOKENS=str(config["planner"].get("max_total_tokens", 300000)),
        BERT_LOCAL_PATH=str(Path(config["assets"]) / "bert-base-uncased"),
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
        EGL_PLATFORM="surfaceless",
        PYTHONUNBUFFERED="1",
    )
    env.pop("DISPLAY", None)
    try:
        driver = (
            subprocess.check_output(
                ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                text=True,
            )
            .splitlines()[0]
            .strip()
        )
        egl = Path.home() / "nvidia-egl" / ("NVIDIA-Linux-x86_64-" + driver)
        if (egl / "libEGL_nvidia.so.0").exists():
            env["LD_LIBRARY_PATH"] = str(egl) + ":" + env.get("LD_LIBRARY_PATH", "")
            env["__EGL_VENDOR_LIBRARY_DIRS"] = str(egl)
    except (OSError, subprocess.SubprocessError, IndexError):
        pass

    command = [sys.executable, "-m", "tasks.multi_harness_vln.runner", "--run-dir", str(run_dir)]
    if preflight:
        command.append("--preflight")

    started = time.monotonic()
    with (run_dir / "console.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        print(
            json.dumps(
                {
                    "status": "started",
                    "method": "multi-harness-vln",
                    "pid": process.pid,
                    "run_dir": str(run_dir),
                    "num_robots": config.get("num_robots", 2),
                    "goal_indices": config.get("goal_indices", [1, 3]),
                }
            ),
            flush=True,
        )
        try:
            returncode = process.wait(timeout=config.get("timeout_s", 1800))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            (run_dir / "failure.json").write_text(
                json.dumps({"status": "timeout", "elapsed_s": time.monotonic() - started}),
                encoding="utf-8",
            )
            raise RuntimeError("Multi-agent HarnessVLN run exceeded its time limit: " + str(run_dir))
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
            raise

    if returncode:
        if not (run_dir / "failure.json").exists():
            (run_dir / "failure.json").write_text(
                json.dumps(
                    {
                        "status": "worker_exited",
                        "returncode": returncode,
                        "elapsed_s": time.monotonic() - started,
                    }
                ),
                encoding="utf-8",
            )
        print((run_dir / "console.log").read_text(errors="replace")[-5000:])
        raise RuntimeError("Multi-agent HarnessVLN worker failed; see " + str(run_dir / "failure.json"))

    result_path = run_dir / ("preflight.json" if preflight else "result.json")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": "preflight_passed" if preflight else "finished",
                "run_dir": str(run_dir),
                "result": result,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return result
