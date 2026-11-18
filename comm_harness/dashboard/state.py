"""Shared Web/TUI state: RPent-style Live Agent Command & Status Monitor + worker lifecycle.

Combines:
1. RPent's live interactive Dashboard session state (`session_detail`, `submit_input`,
   `withdraw_message`, `request_interrupt`, `primitive_specs`, `execute_primitive`,
   `events_since`, `frame`, `wait_for_snapshot`, `emit`) so operators can command the
   agent, steer it in real time, execute primitives, and watch live reasoning/frames/timeline.
2. Comm-Harness's owned Habitat/multi-robot worker lifecycle (`catalog`, `check`, `probe`,
   `list_runs`, `detail`, `start`, `stop`, `monitor`, `compare`, `image_path`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
import logging
import math
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from comm_harness.cli.config import load_single_config, validate_task
from comm_harness.dashboard.events import (
    DashboardEvent,
    RunStartedEvent,
    RuntimeStatusEvent,
    StepRecordEvent,
    TranscriptEvent,
    UsageEvent,
)
from comm_harness.dashboard.interaction import (
    DashboardInteractionError,
    DashboardMessage,
    DashboardMessageConflictError,
    InteractionUnavailableError,
    PlannerActivity,
    UnknownDashboardMessageError,
)
from comm_harness.dashboard.monitoring import MonitorStore, comparison

logger = logging.getLogger("dashboard_state")

RUNTIME_STATUSES = {"pending", "starting", "ready", "failed"}
TERMINAL_RUN_STATES = {"succeeded", "failed", "cancelled", "completed"}
_PLANNER_ACTIVITIES = {"starting", "idle", "busy", "ended"}
_INTEGER = re.compile(r"-?[0-9]+")
_UNSAFE_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")


class PrimitiveArgumentError(ValueError):
    """Dashboard primitive arguments do not satisfy the Toolkit schema."""


class PrimitiveConfigError(RuntimeError):
    """A Toolkit primitive has no usable Dashboard input schema."""


def redact(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {
            k: (
                "[redacted]"
                if re.search(r"key|password|secret|authorization", str(k), re.I)
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[redacted]", value)
        value = re.sub(
            r"(?i)((?:api[_-]?key|password|secret)\s*[=:]\s*)[^\s,;]+",
            r"\1[redacted]",
            value,
        )
        return re.sub(r"(?i)Bearer\s+[A-Za-z0-9._-]+", "Bearer [redacted]", value)
    return value


def read_json(path: Path) -> Dict[str, Any]:
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def tail(path: Path, limit: int = 48000) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - limit))
            return redact(stream.read(limit).decode("utf-8", errors="replace"))
    except OSError:
        return ""


def within(base: Path, path: Path) -> Path:
    base, path = Path(base).resolve(), Path(path).resolve()
    if path != base and base not in path.parents:
        raise ValueError("路径必须位于项目允许的目录内")
    return path


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _to_json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(item) for item in value]
    return value


@dataclass(frozen=True)
class ClaimedTask:
    """One last-write-wins task command claimed by the Session controller."""

    number: int
    request: Dict[str, Any]
    output_dir: Path


def _build_default_dashboard_spec(examples_dir: Path, runs_dir: Path) -> Dict[str, Any]:
    """Build RPent-compatible DashboardSpec from examples/*.json and runs/."""
    task_names: List[str] = []
    profile_names: List[str] = []
    if examples_dir.is_dir():
        for path in sorted(examples_dir.glob("*.json")):
            if path.is_symlink():
                continue
            data = read_json(path)
            if data.get("type") == "object_navigation":
                stem = path.stem
                if stem not in task_names:
                    task_names.append(stem)
                if path.name not in task_names:
                    task_names.append(path.name)
            elif data.get("method") == "harness-vln" and isinstance(data.get("planner"), dict):
                profile_names.append(path.name)
    if not task_names:
        task_names = ["find_vase", "find_radiator"]
    if not profile_names:
        profile_names = ["harness_vln_single.json", "harness_vln_ollama.json"]

    return {
        "task": {
            "command": "/task",
            "usage": "/task <task_name> [profile]",
            "fields": (
                {"name": "task", "suggestions": tuple(task_names)},
                {"name": "profile", "suggestions": tuple(profile_names)},
            ),
            "display": "{task}",
            "output_slug": "{task}",
        },
        "runtime_components": (
            {"name": "habitat_sim", "label": "habitat_sim", "scope": "unique"},
            {"name": "planner", "label": "planner", "scope": "shared"},
            {"name": "comm_bus", "label": "comm_bus", "scope": "shared"},
        ),
        "primitives": (
            "observe",
            "navigate",
            "look_around",
            "move_to",
            "verify",
            "claim_found",
            "broadcast",
        ),
        "operator_commands": {
            "/stop": None,
            "/status": None,
        },
    }


class DashboardState:
    """Thread-safe state backing both the RPent-style Live Monitor UI and CLI/worker control."""

    @property
    def enabled(self) -> bool:
        return True

    def __init__(
        self,
        root: Optional[Union[str, Path]] = None,
        worker_python: Optional[str] = None,
        env_file: Optional[str] = None,
        *,
        output_dir: Optional[Union[str, Path]] = None,
        dashboard_spec: Optional[Dict[str, Any]] = None,
    ) -> None:
        base_root = Path(root or output_dir or ".").resolve()
        self.root = base_root
        task_configs = self.root / "tasks" / "harness_vln" / "configs"
        self.examples = (
            task_configs
            if task_configs.is_dir() and any(task_configs.glob("*.json"))
            else (self.root / "examples")
        )
        self.runs = self.root / "runs"
        self.worker_python = str(Path(worker_python or sys.executable).resolve())
        self.env_file = env_file

        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._process: Optional[subprocess.Popen] = None
        self._active: Optional[Dict[str, Any]] = None
        self._watcher: Optional[threading.Thread] = None
        self._lease: Optional[Any] = None
        self._monitor = MonitorStore()

        # RPent Live Monitor state fields
        self.dashboard_spec = dashboard_spec or _build_default_dashboard_spec(
            self.examples, self.runs
        )
        self._session_root = Path(output_dir or self.runs).resolve()
        self.video_path = self._session_root / "episode.mp4"
        self._task_spec = self.dashboard_spec["task"]
        self._runtime_components = self.dashboard_spec["runtime_components"]
        self._runtime_names = {c["name"] for c in self._runtime_components}
        self._primitive_allowlist = self.dashboard_spec["primitives"]

        self._next_model: Optional[str] = None
        self._task_state: Optional[str] = None
        self._terminated = False
        self._truncated = False
        self._error: Optional[str] = None
        self._usage: Dict[str, int] = {"in": 0, "out": 0, "tool_calls": 0}
        self._planner_usage_base: Dict[str, int] = {"in": 0, "out": 0, "tool_calls": 0}
        self._runtime: Dict[str, Dict[str, Optional[str]]] = {
            c["name"]: {
                "status": "ready" if c.get("scope") == "shared" else "pending",
                "error": None,
            }
            for c in self._runtime_components
        }
        self._events: List[Dict[str, Any]] = []
        self._timeline: List[Dict[str, Any]] = []
        self._frames: Dict[str, bytes] = {}
        self._frame_idx = -1
        self.env_state: Optional[Any] = None
        self._state_step_offset = 0
        self._action_video_paths: Dict[int, Path] = {}

        # Ready to accept /task commands or messages immediately
        self._session_state = (
            "starting_shared_services" if dashboard_spec is not None and root is None else "ready"
        )
        self._accepting_input = True
        self._planner_activity: PlannerActivity = "idle"
        self._interrupt_requested = False
        self._interrupt_in_flight = False
        self._messages: List[DashboardMessage] = []
        self._messages_by_id: Dict[str, DashboardMessage] = {}
        self._next_pending_message_index = 0
        self._last_interaction_error: Optional[str] = None
        self._interaction_version = 0
        self._projection_version = 0
        self._task_generation = 0
        self._pending_task: Optional[Dict[str, Any]] = None
        self._current_task: Optional[Dict[str, Any]] = None
        self._control_feedback: List[str] = []
        self._control_error: Optional[str] = None
        self._shutdown_requested = False
        self._toolkit: Optional[Any] = None
        self._active_primitive_calls: Dict[int, int] = {}
        self._manual_results: List[str] = []
        self.operator: Optional[Any] = None
        self.operator_verdict: Optional[str] = None
        self.operator_exploration = False
        self._synced_run_id: Optional[str] = None
        self._synced_event_count = 0
        self._comm_topology: Dict[str, Any] = self._default_comm_topology()

        # Seed initial guidance & preload latest run if available in workspace mode
        if root is not None:
            self._seed_initial_live_state()

    @staticmethod
    def _default_comm_topology() -> Dict[str, Any]:
        return {
            "medium": "L0 · Ideal Wireless Mesh",
            "nodes": [
                {
                    "id": "command_hub",
                    "label": "COMMAND_HUB",
                    "role": "operator",
                    "status": "online",
                    "step": 0,
                    "phase": "CONTROL",
                    "tx_packets": 0,
                    "rx_packets": 0,
                    "bytes": 0,
                },
                {
                    "id": "robot_0",
                    "label": "robot_0",
                    "role": "embodied_agent",
                    "status": "standby",
                    "step": 0,
                    "phase": "IDLE",
                    "tx_packets": 0,
                    "rx_packets": 0,
                    "bytes": 0,
                },
                {
                    "id": "robot_1",
                    "label": "robot_1",
                    "role": "peer_relay",
                    "status": "standby",
                    "step": 0,
                    "phase": "STANDBY",
                    "tx_packets": 0,
                    "rx_packets": 0,
                    "bytes": 0,
                },
            ],
            "links": [
                {
                    "id": "command_hub->robot_0",
                    "source": "command_hub",
                    "target": "robot_0",
                    "rssi_dbm": -46,
                    "sinr_db": 28.4,
                    "packets": 0,
                    "bytes": 0,
                    "dropped": 0,
                    "status": "LoS · Active",
                },
                {
                    "id": "robot_0->robot_1",
                    "source": "robot_0",
                    "target": "robot_1",
                    "rssi_dbm": -58,
                    "sinr_db": 19.2,
                    "packets": 0,
                    "bytes": 0,
                    "dropped": 0,
                    "status": "Mesh · Standby",
                },
                {
                    "id": "command_hub->robot_1",
                    "source": "command_hub",
                    "target": "robot_1",
                    "rssi_dbm": -62,
                    "sinr_db": 16.5,
                    "packets": 0,
                    "bytes": 0,
                    "dropped": 0,
                    "status": "Mesh · Standby",
                },
            ],
            "metrics": {
                "tx_packets": 0,
                "delivered_packets": 0,
                "dropped_packets": 0,
                "source_bytes": 0,
                "airtime_bytes": 0,
            },
            "recent_packets": [],
        }

    def _seed_initial_live_state(self) -> None:
        """Populate initial welcome message and load latest run snapshot if present."""
        cat = self.catalog()
        tasks_list = [t["id"].replace(".json", "") for t in cat.get("tasks", [])]
        profiles_list = [p["id"] for p in cat.get("profiles", [])]
        hint_tasks = ", ".join(tasks_list) if tasks_list else "find_vase, find_radiator"
        hint_profiles = ", ".join(profiles_list) if profiles_list else "harness_vln_single.json"
        self._events.append(
            {
                "type": "meta",
                "tag": "就绪 / READY",
                "text": (
                    f"可在下方输入框下达任务指令：输入 `/task <任务名> [配置文件]` 启动新任务"
                    f"（可用任务：{hint_tasks}；配置：{hint_profiles}），"
                    f"或直接发送自然语言指令干预智能体；按 Esc 中断当前运行。"
                ),
            }
        )
        runs = self.list_runs()
        if runs:
            completed_runs = [r for r in runs if r.get("state") == "completed"]
            latest_id = (completed_runs[0] if completed_runs else runs[0])["id"]
            try:
                self._project_run_directory(latest_id, reset=False)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Projecting Comm-Harness run directories into RPent Live Monitor
    # ------------------------------------------------------------------

    def _project_run_directory(self, run_id: str, *, reset: bool = True) -> None:
        """Project a run directory's events, frames, and metrics into Live Monitor state."""
        path = self._run_path(run_id)
        if not path.is_dir():
            return
        summary = self._summary(run_id)
        task_def = summary.get("task") or {}
        task_label = (
            task_def.get("name")
            or task_def.get("expected_category")
            or run_id
        )

        # Read raw JSONL events
        raw_events: List[Dict[str, Any]] = []
        streams = sorted((path / "rgb").glob("*/events_r0_w1.jsonl"))
        if not streams and (path / "events.jsonl").is_file():
            streams = [path / "events.jsonl"]
        for stream in streams[:2]:
            try:
                within(path, stream)
            except ValueError:
                continue
            for line in tail(stream, 192000).splitlines():
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        raw_events.append(item)
                except ValueError:
                    pass

        # Collect latest frames by view category
        frames: Dict[str, bytes] = {}
        frame_step = self._frame_idx
        img_files = [
            img
            for img in sorted((path / "rgb").glob("*/*"))
            if img.suffix.lower() in (".png", ".jpg", ".jpeg") and img.is_file()
        ]
        for img in img_files[-12:]:
            try:
                within(path, img)
                stem = img.stem.lower()
                data = img.read_bytes()
                if "pano" in stem:
                    frames["panorama"] = data
                elif "map" in stem or "topdown" in stem or "traj" in stem:
                    frames["topdown_map"] = data
                elif "depth" in stem:
                    frames["depth"] = data
                else:
                    frames["rgb"] = data
                frames["latest"] = data
                m = re.search(r"step[_-]?(\d+)", stem)
                if m:
                    frame_step = max(frame_step, int(m.group(1)))
            except OSError:
                continue
        if img_files and frame_step < 0:
            frame_step = len(img_files) - 1

        # Convert raw events into RPent transcript events, timeline items, and RF topology
        transcript: List[Dict[str, Any]] = []
        timeline: List[Dict[str, Any]] = []
        in_tokens = 0
        out_tokens = 0
        tool_calls_count = 0
        step_counter = 0

        topo = self._default_comm_topology()
        nodes_map = {n["id"]: n for n in topo["nodes"]}
        links_map = {l["id"]: l for l in topo["links"]}
        recent_packets: List[Dict[str, Any]] = []

        if task_def:
            cat_name = task_def.get("expected_category") or task_def.get("name") or "target"
            cmd_text = (
                f"[{run_id}] 下达导航指令：寻找目标 `{cat_name}` "
                f"(episode={task_def.get('episode_id', 'default')}, "
                f"max_steps={task_def.get('max_steps', 300)})"
            )
            transcript.append({"type": "user", "text": cmd_text})
            cmd_bytes = len(cmd_text.encode("utf-8"))
            nodes_map["command_hub"]["tx_packets"] += 1
            nodes_map["command_hub"]["bytes"] += cmd_bytes
            nodes_map["robot_0"]["rx_packets"] += 1
            nodes_map["robot_0"]["status"] = "active"
            links_map["command_hub->robot_0"]["packets"] += 1
            links_map["command_hub->robot_0"]["bytes"] += cmd_bytes
            topo["metrics"]["tx_packets"] += 1
            topo["metrics"]["delivered_packets"] += 1
            topo["metrics"]["source_bytes"] += cmd_bytes
            topo["metrics"]["airtime_bytes"] += cmd_bytes
            recent_packets.append(
                {
                    "id": f"pkt_init_{run_id[-4:]}",
                    "sender": "command_hub",
                    "recipients": ["robot_0"],
                    "kind": "TASK_DISPATCH",
                    "bytes": cmd_bytes,
                    "step": 0,
                    "status": "delivered",
                    "summary": f"Target={cat_name}",
                }
            )

        for ev in raw_events:
            kind = str(ev.get("event") or ev.get("type") or "")
            step_val = ev.get("step")
            node_id = str(ev.get("node_id") or ev.get("node") or "robot_0")
            if node_id not in nodes_map:
                nodes_map[node_id] = {
                    "id": node_id,
                    "label": node_id,
                    "role": str(ev.get("role") or "embodied_agent"),
                    "status": "active",
                    "step": 0,
                    "phase": "ACTIVE",
                    "tx_packets": 0,
                    "rx_packets": 0,
                    "bytes": 0,
                }
                topo["nodes"].append(nodes_map[node_id])
            if isinstance(step_val, int):
                step_counter = max(step_counter, step_val)
                nodes_map[node_id]["step"] = max(nodes_map[node_id]["step"], step_val)
            if ev.get("phase"):
                nodes_map[node_id]["phase"] = str(ev.get("phase"))

            if kind in ("MODEL_RESPONSE", "model_response"):
                usage = ev.get("usage") or {}
                in_tokens += int(
                    usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                )
                out_tokens += int(
                    usage.get("output_tokens") or usage.get("completion_tokens") or 0
                )
                msg = ev.get("message") or {}
                content = msg.get("content") or ev.get("response_preview") or ""
                tcs = msg.get("tool_calls") or []
                if content or tcs:
                    transcript.append(
                        {
                            "type": "assistant",
                            "node_id": node_id,
                            "text": str(content),
                            "tool_calls": [
                                {
                                    "name": tc.get("function", {}).get("name", "tool"),
                                    "args": tc.get("function", {}).get("arguments", "{}"),
                                }
                                for tc in tcs
                                if isinstance(tc, dict)
                            ],
                        }
                    )
            elif kind in ("HARNESS_PLAN_DECISION", "plan_decision"):
                action = ev.get("planned_action") or ev.get("action") or "decide"
                reason = ev.get("reasoning") or ev.get("thought") or ev.get("summary") or ""
                transcript.append(
                    {
                        "type": "assistant",
                        "node_id": node_id,
                        "text": f"[{node_id}] 规划决策：{action}" + (f" — {reason}" if reason else ""),
                    }
                )
            elif kind in ("HARNESS_TOOL_CALL", "tool_call"):
                tool_calls_count += 1
                t_name = str(ev.get("tool") or ev.get("name") or "tool")
                t_args = ev.get("args") or ev.get("arguments") or {}
                transcript.append(
                    {
                        "type": "tool_call",
                        "node_id": node_id,
                        "name": t_name,
                        "args": t_args if isinstance(t_args, dict) else {"value": t_args},
                    }
                )
            elif kind in ("HARNESS_RECEIPT", "HARNESS_TOOL_RESULT", "tool_result"):
                t_name = str(ev.get("tool") or ev.get("name") or "action")
                res_data = ev.get("receipt") or ev.get("result") or ev.get("data") or ev
                s_idx = int(step_val) if isinstance(step_val, int) else len(timeline)
                timeline.append(
                    {
                        "step": s_idx,
                        "node_id": node_id,
                        "action": t_name,
                        "args": ev.get("args") or {},
                        "result": redact(_to_json_safe(res_data)),
                        "elapsed_s": ev.get("duration_s") or ev.get("elapsed_s") or 0.0,
                        "terminated": False,
                        "truncated": False,
                        "has_action_video": False,
                    }
                )
                serialized_res = json.dumps(
                    redact(_to_json_safe(res_data)), ensure_ascii=False
                )[:1000]
                transcript.append(
                    {
                        "type": "tool_result",
                        "node_id": node_id,
                        "name": t_name,
                        "content": serialized_res,
                    }
                )
                # Count telemetry uplink packet from robot to command_hub
                pkt_bytes = len(serialized_res.encode("utf-8"))
                nodes_map[node_id]["tx_packets"] += 1
                nodes_map[node_id]["bytes"] += pkt_bytes
                nodes_map["command_hub"]["rx_packets"] += 1
                link_key = f"command_hub->{node_id}"
                if link_key in links_map:
                    links_map[link_key]["packets"] += 1
                    links_map[link_key]["bytes"] += pkt_bytes
                topo["metrics"]["tx_packets"] += 1
                topo["metrics"]["delivered_packets"] += 1
                topo["metrics"]["source_bytes"] += pkt_bytes
                topo["metrics"]["airtime_bytes"] += pkt_bytes
                recent_packets.append(
                    {
                        "id": f"pkt_step_{s_idx}_{t_name}",
                        "sender": node_id,
                        "recipients": ["command_hub"],
                        "kind": f"RECEIPT:{t_name.upper()}",
                        "bytes": pkt_bytes,
                        "step": s_idx,
                        "status": "delivered",
                        "summary": f"{t_name} @ step #{s_idx}",
                    }
                )
            elif kind.startswith("COMM_") or kind in (
                "message_sent",
                "message_delivered",
                "message_expired",
                "message_dropped",
            ):
                sender = str(ev.get("sender") or node_id)
                recipients = list(ev.get("recipients") or [ev.get("recipient") or "broadcast"])
                p_bytes = int(ev.get("serialized_bytes") or 128)
                msg_kind = str(ev.get("kind") or kind)
                if sender in nodes_map:
                    nodes_map[sender]["tx_packets"] += 1
                    nodes_map[sender]["bytes"] += p_bytes
                if kind in ("message_expired", "message_dropped"):
                    topo["metrics"]["dropped_packets"] += 1
                else:
                    topo["metrics"]["tx_packets"] += 1
                    topo["metrics"]["delivered_packets"] += len(recipients)
                    topo["metrics"]["source_bytes"] += p_bytes
                    topo["metrics"]["airtime_bytes"] += p_bytes * max(1, len(recipients))
                recent_packets.append(
                    {
                        "id": str(ev.get("message_id") or f"rf_{len(recent_packets)}"),
                        "sender": sender,
                        "recipients": recipients,
                        "kind": msg_kind,
                        "bytes": p_bytes,
                        "step": step_counter,
                        "status": "dropped" if "expir" in kind or "drop" in kind else "delivered",
                        "summary": str(ev.get("summary") or msg_kind),
                    }
                )
                transcript.append(
                    {
                        "type": "meta",
                        "node_id": sender,
                        "tag": f"RF 无线帧 · {msg_kind}",
                        "text": f"{sender} → {', '.join(str(r) for r in recipients)} ({p_bytes} B) | {json.dumps(redact(_to_json_safe(ev)), ensure_ascii=False)[:320]}",
                    }
                )
            elif kind in ("HARNESS_TERMINAL", "HARNESS_EPISODE_END"):
                transcript.append(
                    {
                        "type": "meta",
                        "node_id": node_id,
                        "tag": "智能体结项 / AGENT_DECLARED_DONE",
                        "text": f"智能体已结束当前周期并提交停止声明（步数：{step_counter}）。注：Harness 仅记录智能体自身生命周期，不读取外部评测器真值。",
                    }
                )

        topo["recent_packets"] = recent_packets[-24:]

        with self._condition:
            self._synced_run_id = run_id
            self._current_task = {"task": str(task_label)}
            if reset:
                self._events = transcript
            else:
                self._events = self._events[:1] + transcript
            self._timeline = timeline
            self._comm_topology = topo
            if frames:
                self._frames = frames
                self._frame_idx = max(0, frame_step)
            self._usage = {
                "in": in_tokens,
                "out": out_tokens,
                "tool_calls": max(tool_calls_count, len(timeline)),
            }
            st = summary.get("state", "ready")
            if not reset and st != "running":
                # Initial startup preload: keep console in STANDBY ("ready") state ready for new commands
                self._task_state = "ready"
                self._session_state = "ready"
                self._planner_activity = "idle"
                self._accepting_input = True
                if "habitat_sim" in self._runtime:
                    self._runtime["habitat_sim"] = {"status": "ready", "error": None}
            elif st == "running":
                self._task_state = "running"
                self._session_state = "running"
                self._planner_activity = "busy"
                self._accepting_input = True
                if "habitat_sim" in self._runtime:
                    self._runtime["habitat_sim"] = {"status": "ready", "error": None}
            elif st == "completed":
                # Strictly report Harness lifecycle state ("finished" = agent finished execution),
                # NEVER oracle evaluation truth ("succeeded"/"failed") from result.json!
                self._task_state = "finished"
                self._session_state = "ready"
                self._planner_activity = "idle"
                self._accepting_input = True
                self._terminated = True
                if "habitat_sim" in self._runtime:
                    self._runtime["habitat_sim"] = {"status": "ready", "error": None}
            elif st in ("failed", "cancelled"):
                self._task_state = st
                self._session_state = "ready"
                self._planner_activity = "idle"
                self._accepting_input = True
            self._projection_changed_locked()

    # ------------------------------------------------------------------
    # RPent Live Monitor Methods
    # ------------------------------------------------------------------

    def operator_changed(self) -> None:
        with self._condition:
            self._projection_changed_locked()

    def operator_snapshot(self) -> Dict[str, Any]:
        with self._condition:
            return {
                "active": self._toolkit is not None,
                "pending": self.operator.snapshot() if self.operator else None,
                "exploration": self.operator_exploration,
            }

    def bind_toolkit(self, toolkit: Any) -> None:
        with self._condition:
            self._toolkit = toolkit
            self._projection_changed_locked()

    def unbind_toolkit(self, toolkit: Any) -> None:
        with self._condition:
            if self._toolkit is toolkit:
                self._toolkit = None
                self._projection_changed_locked()

    def _primitives_available_locked(self) -> bool:
        return (
            self._session_state != "switch_pending"
            and self._planner_activity in ("idle", "ended")
            and not self._active_primitive_calls
        )

    def primitive_specs(self) -> List[Dict[str, Any]]:
        """Return interactive primitive schemas for the right-hand Primitive Controls panel."""
        with self._lock:
            if self._toolkit is not None and hasattr(self._toolkit, "list_tools"):
                primitives = []
                for decl in self._toolkit.list_tools():
                    name = getattr(decl, "name", None) or decl.get("name")
                    schema = getattr(decl, "input_schema", None) or decl.get("input_schema")
                    if name in self._primitive_allowlist and isinstance(schema, dict):
                        primitives.append({"name": name, "input_schema": schema})
                return primitives

            # Provide standard Comm-Harness robot & communication primitives
            return [
                {
                    "name": "observe",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "mode": {
                                "type": "string",
                                "enum": ["panorama", "front", "map"],
                                "default": "panorama",
                            }
                        },
                        "required": ["mode"],
                    },
                },
                {
                    "name": "navigate",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "candidate_id": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["candidate_id"],
                    },
                },
                {
                    "name": "broadcast",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["STATUS", "OBSERVATION_FACT", "CLAIM_INTENT"],
                                "default": "STATUS",
                            },
                            "summary": {"type": "string"},
                        },
                        "required": ["kind", "summary"],
                    },
                },
            ]

    def _record_rf_packet_locked(
        self,
        *,
        sender: str,
        recipients: List[str],
        kind: str,
        summary: str,
        payload_bytes: int,
    ) -> None:
        topo = self._comm_topology
        nodes_map = {n["id"]: n for n in topo.get("nodes", [])}
        links_map = {l["id"]: l for l in topo.get("links", [])}
        if sender in nodes_map:
            nodes_map[sender]["tx_packets"] = int(nodes_map[sender].get("tx_packets", 0)) + 1
            nodes_map[sender]["bytes"] = int(nodes_map[sender].get("bytes", 0)) + payload_bytes
            nodes_map[sender]["status"] = "active"
        for r in recipients:
            if r in nodes_map:
                nodes_map[r]["rx_packets"] = int(nodes_map[r].get("rx_packets", 0)) + 1
                nodes_map[r]["status"] = "active"
            for lk in (f"{sender}->{r}", f"{r}->{sender}"):
                if lk in links_map:
                    links_map[lk]["packets"] = int(links_map[lk].get("packets", 0)) + 1
                    links_map[lk]["bytes"] = int(links_map[lk].get("bytes", 0)) + payload_bytes
                    links_map[lk]["status"] = "LoS · Active"
                    break
        metrics = topo.setdefault("metrics", {})
        metrics["tx_packets"] = int(metrics.get("tx_packets", 0)) + 1
        metrics["delivered_packets"] = int(metrics.get("delivered_packets", 0)) + max(1, len(recipients))
        metrics["source_bytes"] = int(metrics.get("source_bytes", 0)) + payload_bytes
        metrics["airtime_bytes"] = int(metrics.get("airtime_bytes", 0)) + payload_bytes * max(1, len(recipients))
        pkts = topo.setdefault("recent_packets", [])
        pkts.append(
            {
                "id": f"rf_{uuid.uuid4().hex[:6]}",
                "sender": sender,
                "recipients": recipients,
                "kind": kind,
                "bytes": payload_bytes,
                "step": len(self._timeline),
                "status": "delivered",
                "summary": summary[:120],
            }
        )
        topo["recent_packets"] = pkts[-24:]

    def execute_primitive(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Execute one primitive from the Dashboard Primitive Controls panel."""
        with self._condition:
            if name not in self._primitive_allowlist:
                raise ValueError(f"primitive is not allowed: {name}")
            specs = {s["name"]: s["input_schema"] for s in self.primitive_specs()}
            schema = specs.get(name)
            if schema is None:
                raise ValueError(f"primitive is not available: {name}")
            for req in schema.get("required", ()):
                if req not in arguments or arguments[req] in ("", None):
                    raise PrimitiveArgumentError(
                        f"invalid arguments for {name} at $.{req}: {req!r} is a required property"
                    )
            toolkit = self._toolkit

        if toolkit is not None:
            from comm_harness.planner.base import invoke_toolkit_sync

            res = invoke_toolkit_sync(toolkit, name, arguments)
            if hasattr(res, "to_dict"):
                return res
            return _SimpleToolResult(res if isinstance(res, dict) else {"result": res})

        # Record manual primitive dispatch into live transcript, timeline & RF topology
        with self._condition:
            step_no = len(self._timeline)
            receipt = {
                "ok": True,
                "primitive": name,
                "arguments": arguments,
                "status": "dispatched_to_agent",
            }
            self._timeline.append(
                {
                    "step": step_no,
                    "node_id": "robot_0",
                    "action": name,
                    "args": arguments,
                    "result": receipt,
                    "elapsed_s": 0.01,
                    "terminated": False,
                    "truncated": False,
                    "has_action_video": False,
                }
            )
            self._events.append(
                {
                    "type": "tool_call",
                    "node_id": "robot_0",
                    "name": name,
                    "args": arguments,
                }
            )
            self._events.append(
                {
                    "type": "tool_result",
                    "node_id": "robot_0",
                    "name": name,
                    "content": json.dumps(receipt, ensure_ascii=False),
                }
            )
            raw_bytes = len(json.dumps(arguments, ensure_ascii=False).encode("utf-8"))
            if name == "broadcast":
                self._record_rf_packet_locked(
                    sender="robot_0",
                    recipients=["robot_1", "command_hub"],
                    kind=f"BROADCAST:{arguments.get('kind', 'STATUS')}",
                    summary=str(arguments.get("summary", "")),
                    payload_bytes=max(64, raw_bytes),
                )
            else:
                self._record_rf_packet_locked(
                    sender="command_hub",
                    recipients=["robot_0"],
                    kind=f"PRIMITIVE:{name.upper()}",
                    summary=json.dumps(arguments, ensure_ascii=False),
                    payload_bytes=max(48, raw_bytes),
                )
            self._projection_changed_locked()
        return _SimpleToolResult(receipt)

    def shared_services_ready(self) -> None:
        with self._condition:
            if self._session_state == "fatal":
                return
            self._session_state = "ready"
            self._planner_activity = "idle"
            self._accepting_input = True
            self._control_error = None
            self._interaction_changed_locked()

    def fail_session(self, error: Union[BaseException, str]) -> None:
        with self._condition:
            self._session_state = "fatal"
            self._error = str(error)
            self._control_error = str(error)
            self._pending_task = None
            self._seal_interaction_locked()
            self._interaction_changed_locked()

    def request_shutdown(self) -> None:
        with self._condition:
            self._shutdown_requested = True
            self._interaction_changed_locked()

    def set_next_model(self, model: str) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        with self._condition:
            self._next_model = model.strip()
            self._events.append(
                {
                    "type": "meta",
                    "tag": "模型切换 / MODEL",
                    "text": f"已将下一任务的模型设置为：{self._next_model}",
                }
            )
            self._projection_changed_locked()

    def _resolve_task_and_profile(
        self, task_token: str, profile_token: Optional[str] = None
    ) -> Tuple[str, str]:
        cat = self.catalog()
        tasks_by_id = {t["id"]: t["id"] for t in cat.get("tasks", [])}
        for t in cat.get("tasks", []):
            tasks_by_id[Path(t["id"]).stem] = t["id"]
            if t.get("name"):
                tasks_by_id[str(t["name"])] = t["id"]
            if t.get("expected_category"):
                tasks_by_id[str(t["expected_category"])] = t["id"]

        resolved_task = tasks_by_id.get(task_token)
        if not resolved_task and not task_token.endswith(".json"):
            resolved_task = tasks_by_id.get(f"{task_token}.json")
        if not resolved_task:
            available = ", ".join(sorted({Path(t["id"]).stem for t in cat.get("tasks", [])}))
            raise ValueError(f"未知任务 {task_token!r}（可用任务：{available}）")

        profiles = [p["id"] for p in cat.get("profiles", [])]
        if profile_token:
            resolved_profile = (
                profile_token
                if profile_token in profiles
                else (f"{profile_token}.json" if f"{profile_token}.json" in profiles else None)
            )
            if not resolved_profile:
                raise ValueError(f"未知运行配置 {profile_token!r}（可用配置：{', '.join(profiles)}）")
        else:
            # Prefer profile whose model/provider matches _next_model if set
            resolved_profile = profiles[0] if profiles else "harness_vln_single.json"
            if self._next_model and "ollama" in self._next_model.lower():
                for p in profiles:
                    if "ollama" in p.lower():
                        resolved_profile = p
                        break
            else:
                for p in profiles:
                    if "single" in p.lower():
                        resolved_profile = p
                        break
        return resolved_task, resolved_profile

    def submit_input(
        self, text: Optional[str], *, operator_context: Any = None
    ) -> Any:
        """Handle `/task ...`, `/stop`, `/load ...`, or live operator messages to the agent."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("message text must not be blank")
        cleaned = text.strip()
        tokens = cleaned.split()
        cmd = tokens[0].lower()

        if cmd == "/stop":
            with self._lock:
                active_id = self._active["id"] if self._active else None
            if active_id:
                self.stop(active_id)
                with self._condition:
                    self._events.append(
                        {"type": "meta", "tag": "已停止", "text": f"已请求停止运行 {active_id}"}
                    )
                    self._projection_changed_locked()
                return {"command": "/stop"}
            raise ValueError("当前没有正在运行的任务")

        if cmd == "/load" and len(tokens) == 2:
            self._project_run_directory(tokens[1], reset=True)
            return {"command": "/load", "run_id": tokens[1]}

        if cmd in ("/task", self._task_spec.get("command", "/task")):
            if len(tokens) < 2 or len(tokens) > 3:
                raise ValueError(f"用法：{self._task_spec['usage']}")
            task_token = tokens[1]
            profile_token = tokens[2] if len(tokens) == 3 else None

            # If running inside a custom RPent DashboardSessionController without examples/
            if not self.examples.is_dir():
                request = {"task": task_token}
                if profile_token:
                    request["profile"] = profile_token
                self.request_task(request)
                return request

            task_file, profile_file = self._resolve_task_and_profile(
                task_token, profile_token
            )
            with self._condition:
                self._events.append(
                    {
                        "type": "user",
                        "text": f"/task {task_file} ({profile_file})",
                    }
                )
                self._projection_changed_locked()
            started = self.start(task_file, profile_file)
            return started

        # Normal natural language message / command to the agent
        with self._condition:
            if self._session_state in {"starting_shared_services", "fatal"}:
                raise InteractionUnavailableError("Dashboard Session is not accepting input")
            message = DashboardMessage(
                message_id=f"msg_{uuid.uuid4().hex[:10]}",
                text=cleaned,
                status="pending" if self._planner_activity == "busy" else "sent",
            )
            self._messages.append(message)
            self._messages_by_id[message.message_id] = message
            self._events.append({"type": "user", "text": cleaned})
            self._record_rf_packet_locked(
                sender="command_hub",
                recipients=["robot_0"],
                kind="OPERATOR_MSG",
                summary=cleaned,
                payload_bytes=len(cleaned.encode("utf-8")),
            )

            # If a worker run is active, persist the operator instruction into its run dir
            if self._active and self._active.get("id"):
                try:
                    run_dir = self._run_path(self._active["id"])
                    run_dir.mkdir(parents=True, exist_ok=True)
                    with (run_dir / "operator_messages.jsonl").open("a", encoding="utf-8") as f:
                        f.write(
                            json.dumps(
                                {
                                    "ts": time.time(),
                                    "message_id": message.message_id,
                                    "text": cleaned,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                except OSError:
                    pass
            else:
                # Provide immediate feedback when idle and user types natural language
                self._events.append(
                    {
                        "type": "assistant",
                        "text": (
                            f"已收到指令：「{cleaned}」。"
                            f"若要启动仿真环境执行任务，请输入 `/task find_vase` 或 `/task find_radiator`。"
                        ),
                    }
                )
            self._interaction_changed_locked()
            return replace(message)

    def request_task(self, request: Dict[str, Any]) -> None:
        with self._condition:
            if self._session_state == "fatal":
                raise InteractionUnavailableError("Dashboard Session is fatal")
            self._pending_task = dict(request)
            self._control_error = None
            self._control_feedback = []
            active = self._task_state in {"starting", "running"}
            self._session_state = "switch_pending" if active else "task_starting"
            self._interaction_changed_locked()

    def wait_for_task(self, timeout: Optional[float] = None) -> Optional[ClaimedTask]:
        with self._condition:
            self._condition.wait_for(
                lambda: (
                    self._pending_task is not None
                    or self._shutdown_requested
                    or self._session_state == "fatal"
                ),
                timeout=timeout,
            )
            if self._shutdown_requested or self._session_state == "fatal" or self._pending_task is None:
                return None
            request = self._pending_task
            if self._next_model is not None:
                request = {**request, "model": self._next_model}
            self._pending_task = None
            self._task_generation += 1
            number = self._task_generation
            slug = _UNSAFE_SLUG.sub("_", str(request.get("task", "task"))).strip("._") or "task"
            output_dir = self._session_root / "tasks" / f"{number:04d}_{slug}"
            self._current_task = request
            self._session_state = "running"
            self._task_state = "running"
            self._planner_activity = "busy"
            self._accepting_input = True
            self._interaction_changed_locked()
            return ClaimedTask(number=number, request=request, output_dir=output_dir)

    def complete_task(
        self,
        *,
        state: str,
        error: Optional[Union[BaseException, str]] = None,
    ) -> None:
        with self._condition:
            self._task_state = state
            self._error = None if error is None else str(error)
            self._planner_activity = "idle"
            self._accepting_input = True
            self._session_state = "task_starting" if self._pending_task is not None else "ready"
            if error is not None:
                self._control_error = str(error)
            self._interaction_changed_locked()

    def withdraw_message(self, message_id: str) -> DashboardMessage:
        with self._condition:
            if message_id not in self._messages_by_id:
                raise UnknownDashboardMessageError(f"unknown Dashboard message: {message_id}")
            message = self._messages_by_id[message_id]
            if message.status != "pending":
                raise DashboardMessageConflictError(f"message is {message.status}, not pending")
            message.status = "withdrawn"
            message.error = None
            self._interaction_changed_locked()
            return replace(message)

    def request_interrupt(self) -> str:
        with self._condition:
            if self._interrupt_requested:
                return "duplicate"
            active_id = self._active["id"] if self._active else None
            if self._planner_activity != "busy" and not active_id:
                return "noop"
            self._interrupt_requested = True
            self._interaction_changed_locked()
        if active_id:
            try:
                self.stop(active_id)
            except Exception:
                pass
            with self._condition:
                self._interrupt_requested = False
                self._planner_activity = "idle"
                self._events.append(
                    {"type": "meta", "tag": "中断 / INTERRUPT", "text": f"已中断运行 {active_id}"}
                )
                self._interaction_changed_locked()
        return "accepted"

    def emit(self, event: DashboardEvent) -> None:
        if isinstance(event, TranscriptEvent):
            with self._condition:
                self._events.append(event.payload)
                self._projection_changed_locked()
            return
        if isinstance(event, UsageEvent):
            with self._condition:
                self._usage = {
                    "in": self._planner_usage_base["in"] + int(event.inp),
                    "out": self._planner_usage_base["out"] + int(event.out),
                    "tool_calls": self._planner_usage_base["tool_calls"] + int(event.tool_calls),
                }
                self._projection_changed_locked()
            return
        if isinstance(event, RuntimeStatusEvent):
            with self._condition:
                self._runtime[event.component] = {
                    "status": event.status,
                    "error": None if event.error is None else str(event.error),
                }
                self._projection_changed_locked()
            return
        if isinstance(event, RunStartedEvent):
            with self._condition:
                self._task_state = "running"
                self._session_state = "running"
                self._planner_activity = "busy"
                self._accepting_input = True
                self._interaction_changed_locked()
            return

    def _seal_interaction_locked(self) -> None:
        self._planner_activity = "ended"
        self._accepting_input = False
        self._interrupt_requested = False
        self._interrupt_in_flight = False
        for message in self._messages:
            if message.status in {"pending", "sending"}:
                message.status = "unsent"
                message.error = None

    def _interaction_changed_locked(self) -> None:
        self._interaction_version += 1
        self._projection_changed_locked()

    def _projection_changed_locked(self) -> None:
        self._projection_version += 1
        self._condition.notify_all()

    def _input_mode_locked(self) -> str:
        if self._session_state in {"starting_shared_services", "fatal"}:
            return "disabled"
        if self._session_state == "running" and self._accepting_input:
            return "conversation"
        return "command_only"

    def _visible_state_locked(self) -> str:
        if self._session_state == "fatal":
            return "failed"
        if self._session_state in {"starting_shared_services", "task_starting"}:
            return "starting"
        return self._task_state or self._session_state

    def events_since(self, since: int) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._events[since:])

    def frame(self, kind: str) -> Optional[bytes]:
        with self._lock:
            return self._frames.get(kind)

    def action_video_path(self, step: int) -> Optional[Path]:
        with self._lock:
            path = self._action_video_paths.get(int(step))
        return path if path is not None and path.exists() else None

    def has_video(self) -> bool:
        with self._lock:
            return self._task_state in TERMINAL_RUN_STATES and self.video_path.exists()

    def _snapshot_locked(self) -> Dict[str, Any]:
        frame_available = {k: True for k in self._frames}
        return {
            "state": self._visible_state_locked(),
            "terminated": self._terminated,
            "truncated": self._truncated,
            "error": self._error,
            "usage": dict(self._usage),
            "runtime": {k: dict(v) for k, v in self._runtime.items()},
            "has_video": self.has_video(),
            "frame_idx": self._frame_idx,
            "frame_available": frame_available,
            "n_events": len(self._events),
            "n_steps": len(self._timeline),
            "primitives_available": self._primitives_available_locked(),
            "interaction": {
                "input_mode": self._input_mode_locked(),
                "planner_activity": self._planner_activity,
                "interrupt_requested": self._interrupt_requested,
                "messages": [m.as_dict() for m in self._messages],
                "last_error": self._last_interaction_error,
            },
            "session_state": self._session_state,
            "task_generation": self._task_generation,
            "current_task": (
                {"label": str(self._current_task.get("task", ""))}
                if self._current_task
                else None
            ),
            "pending_task": (
                {"label": str(self._pending_task.get("task", ""))}
                if self._pending_task
                else None
            ),
            "control_feedback": list(self._control_feedback),
            "control_error": self._control_error,
            "operator": self.operator_snapshot(),
            "comm_topology": self._comm_topology,
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def wait_for_snapshot(
        self,
        since: int,
        timeout: Optional[float] = None,
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        with self._condition:
            self._condition.wait_for(
                lambda: self._projection_version != since,
                timeout=timeout,
            )
            version = self._projection_version
            if version == since:
                return version, None
            return version, self._snapshot_locked()

    def session_detail(self, *, timeline_since: int = 0) -> Dict[str, Any]:
        with self._lock:
            since = int(timeline_since)
            if since < 0 or since > len(self._timeline):
                since = 0
            return {
                **self._snapshot_locked(),
                "timeline": list(self._timeline[since:]),
            }

    # ------------------------------------------------------------------
    # Existing Comm-Harness Catalog, Inspection & Owned Worker Methods
    # ------------------------------------------------------------------

    def monitor(self, run_id: str) -> Dict[str, Any]:
        path = self._run_path(run_id)
        if not path.is_dir():
            raise FileNotFoundError("运行记录不存在")
        snapshot = self._monitor.get(
            path, read_json(path / "result.json"), self._summary(run_id)
        )
        planner = read_json(path / "config.json").get("planner", {})
        snapshot["model"]["budget"] = {
            k: planner.get(k) for k in ("max_calls", "max_total_tokens")
        }
        return redact(snapshot)

    def compare(self, ids: List[str]) -> Dict[str, Any]:
        if not 2 <= len(ids) <= 6 or len(set(ids)) != len(ids):
            raise ValueError("请选择 2–6 条不同的运行记录")
        details = []
        for run_id in ids:
            path = self._run_path(run_id)
            if not path.is_dir():
                raise FileNotFoundError("运行记录不存在")
            details.append(
                dict(
                    self._summary(run_id),
                    result=read_json(path / "result.json"),
                    config=read_json(path / "config.json"),
                )
            )
        return redact(comparison(details))

    def probe(self, task: str, profile: str) -> Dict[str, Any]:
        """Explicit, bounded connectivity check. Does not load or invoke a model."""
        import socket
        from urllib.parse import urlsplit

        config = self._selection(task, profile)
        endpoint = urlsplit(config["planner"].get("base_url", ""))
        result: Dict[str, Any] = {
            "checked_at": time.time(),
            "worker_interpreter_exists": Path(self.worker_python).is_file(),
            "model_endpoint": {"status": "unconfigured"},
            "gpu": None,
            "note": "TCP 可达只代表端口可连接，不保证模型已加载、API 鉴权有效或推理成功；GPU 为查询时快照。",
        }
        if endpoint.scheme in ("http", "https") and endpoint.hostname:
            started = time.monotonic()
            try:
                with socket.create_connection(
                    (
                        endpoint.hostname,
                        endpoint.port or (443 if endpoint.scheme == "https" else 80),
                    ),
                    timeout=2,
                ):
                    pass
                result["model_endpoint"] = {
                    "status": "tcp_reachable",
                    "connect_s": time.monotonic() - started,
                }
            except (OSError, ValueError) as exc:
                result["model_endpoint"] = {"status": "unreachable", "error": str(exc)}
        try:
            process = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
            result["gpu"] = (
                process.stdout.strip()[:3000] if process.returncode == 0 else None
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return redact(result)

    def catalog(self) -> Dict[str, Any]:
        tasks, profiles = [], []
        if self.examples.is_dir():
            for path in sorted(self.examples.glob("*.json")):
                if path.is_symlink():
                    continue
                data = read_json(path)
                if data.get("type") == "object_navigation":
                    try:
                        validate_task(data)
                    except ValueError:
                        continue
                    tasks.append({"id": path.name, **redact(data)})
                elif data.get("method") == "harness-vln" and isinstance(
                    data.get("planner"), dict
                ):
                    profiles.append(
                        {
                            "id": path.name,
                            "model": data["planner"].get("model"),
                            "provider": data["planner"].get("provider", "openai"),
                            "task_file": data.get("task_file"),
                        }
                    )
        return {
            "tasks": tasks,
            "profiles": profiles,
            "method": "harness-vln",
            "scope": "当前控制入口支持单/多机器人任务下达与实时状态监控。",
        }

    def _selection(self, task: str, profile: str) -> Dict[str, Any]:
        catalog = self.catalog()
        if task not in {item["id"] for item in catalog["tasks"]}:
            raise ValueError("请选择 examples 中有效的任务文件")
        if profile not in {item["id"] for item in catalog["profiles"]}:
            raise ValueError("请选择 examples 中有效的运行配置")
        return load_single_config(self.examples / profile, str(self.examples / task))

    def check(self, task: str, profile: str) -> Dict[str, Any]:
        config = self._selection(task, profile)
        errors = []
        if not Path(self.worker_python).is_file():
            errors.append("运行环境的 Python 不存在，请设置 --worker-python")
        if os.name != "posix":
            errors.append("当前 Habitat 任务启动需要 Linux；本机可以查看任务和历史记录")
        for field in ("episodes", "scenes", "assets", "asset_manifest"):
            if not config.get(field) or not Path(config[field]).exists():
                errors.append("运行资源不存在：" + field)
        from comm_harness.planner.model_config import require_vision_model

        try:
            require_vision_model(config["planner"]["model"])
        except (ValueError, KeyError) as exc:
            errors.append(str(exc))
        from comm_harness.cli.main import read_key

        if config["planner"].get("provider") != "ollama" and not read_key(self.env_file):
            errors.append("服务器未配置模型 API 密钥")
        command = [
            self.worker_python,
            "-m",
            "comm_harness",
            "--method",
            "harness-vln",
            "--config",
            str(self.examples / profile),
            "--task",
            str(self.examples / task),
        ]
        if self.env_file:
            command += ["--env-file", self.env_file]
        return {
            "ok": not errors,
            "errors": errors,
            "task": config.get("task_definition"),
            "planner": redact(config["planner"]),
            "command": "cd "
            + shlex.quote(str(self.root))
            + " && "
            + shlex.join(command),
            "notice": "此检查只核对配置、路径和密钥是否存在；未调用模型，未验证网络、显存和仿真运行。",
        }

    def _run_path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,150}", run_id
        ):
            raise ValueError("无效的运行编号")
        candidate = self.runs / run_id
        if candidate.is_symlink():
            raise ValueError("不支持链接到其他运行目录")
        return within(self.runs, candidate)

    def _record_path(self, run_id: str) -> Path:
        self._run_path(run_id)
        return self.runs / ".control" / (run_id + ".json")

    def _save_active(self) -> None:
        if not self._active:
            return
        path = self._record_path(self._active["id"])
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._active, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    def _summary(self, run_id: str) -> Dict[str, Any]:
        path = self._run_path(run_id)
        result = read_json(path / "result.json")
        failure = read_json(path / "failure.json")
        control = read_json(self._record_path(run_id))
        task = read_json(path / "task_definition.json") or read_json(path / "task.json")
        metrics = result.get("metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
        if control.get("state") in ("running", "stopping"):
            status = (
                control["state"]
                if self._active and self._active["id"] == run_id
                else "unknown"
            )
        elif control.get("state") == "cancelled":
            status = "cancelled"
        elif result:
            status = "completed"
        elif failure or control.get("state") == "failed":
            status = "failed"
        elif (path / "preflight.json").exists():
            status = "incomplete"
        else:
            status = "unknown"
        return {
            "id": run_id,
            "state": status,
            "task": task,
            "success": metrics.get("success"),
            "steps": metrics.get("steps_taken"),
            "distance": metrics.get("distance_to_goal"),
            "started_at": control.get("started_at"),
            "modified": path.stat().st_mtime if path.exists() else 0,
            "owned": bool(self._active and self._active["id"] == run_id),
        }

    def list_runs(self) -> List[Dict[str, Any]]:
        ids = (
            {
                p.name
                for p in self.runs.iterdir()
                if p.is_dir()
                and not p.is_symlink()
                and not p.name.startswith(".")
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,150}", p.name)
            }
            if self.runs.exists()
            else set()
        )
        with self._lock:
            if self._active:
                ids.add(self._active["id"])
            return redact(
                sorted(
                    (self._summary(name) for name in ids),
                    key=lambda item: item["modified"],
                    reverse=True,
                )[:100]
            )

    def detail(self, run_id: str) -> Dict[str, Any]:
        path = self._run_path(run_id)
        with self._lock:
            if not path.is_dir() and not (self._active and self._active["id"] == run_id):
                raise FileNotFoundError("运行记录不存在")
            summary = self._summary(run_id)
        events = []
        streams = sorted((path / "rgb").glob("*/events_r0_w1.jsonl"))
        if not streams and (path / "events.jsonl").is_file():
            streams = [path / "events.jsonl"]
        for stream in streams[:2]:
            within(path, stream)
            for line in tail(stream, 96000).splitlines():
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        events.append(item)
                except ValueError:
                    pass
        images = []
        for image in sorted((path / "rgb").glob("*/*")):
            if image.suffix.lower() in (".png", ".jpg", ".jpeg") and image.is_file():
                try:
                    within(path, image)
                except ValueError:
                    continue
                images.append(image.relative_to(path).as_posix())
        return redact(
            {
                **summary,
                "result": read_json(path / "result.json"),
                "failure": read_json(path / "failure.json"),
                "config": read_json(path / "config.json"),
                "tool_catalog": read_json(path / "tool_catalog.json"),
                "monitoring": self.monitor(run_id),
                "events": events[-160:],
                "images": images[-400:],
                "console": tail(path / "console.log")
                or tail(self.runs / ".control" / (run_id + ".log")),
                "can_stop": summary["owned"] and summary["state"] == "running",
            }
        )

    def image_path(self, run_id: str, name: str) -> Path:
        run = self._run_path(run_id)
        image_root = within(run, run / "rgb")
        path = within(image_root, run / name)
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg") or not path.is_file():
            raise FileNotFoundError("图像不存在")
        return path

    def start(self, task: str, profile: str) -> Dict[str, Any]:
        checked = self.check(task, profile)
        if not checked["ok"]:
            raise ValueError("；".join(checked["errors"]))
        with self._lock:
            if self._process is not None:
                raise RuntimeError("当前会话已有运行，请先等待结束或停止")
            import fcntl

            control = self.runs / ".control"
            control.mkdir(parents=True, exist_ok=True)
            lease = (control / "worker.lock").open("a+")
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lease.close()
                raise RuntimeError("另一个 Web/CLI 会话已有运行，请等待它结束")
            run_id = (
                "ui-"
                + datetime.now().strftime("%Y%m%d-%H%M%S-")
                + uuid.uuid4().hex[:6]
            )
            command = [
                self.worker_python,
                "-u",
                "-m",
                "comm_harness",
                "--method",
                "harness-vln",
                "--config",
                str(self.examples / profile),
                "--task",
                str(self.examples / task),
                "--run-dir",
                str(self.runs / run_id),
            ]
            if self._next_model:
                command += ["--model", self._next_model]
            if self.env_file:
                command += ["--env-file", self.env_file]
            log = (control / (run_id + ".log")).open("wb")
            try:
                env = dict(
                    os.environ,
                    PYTHONUNBUFFERED="1",
                    PYTHONNOUSERSITE="1",
                    OMP_NUM_THREADS="1",
                    OPENBLAS_NUM_THREADS="1",
                    MKL_NUM_THREADS="1",
                )
                process = subprocess.Popen(
                    command,
                    cwd=str(self.root),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    pass_fds=(lease.fileno(),),
                    env=env,
                )
            except BaseException:
                log.close()
                lease.close()
                raise
            self._process, self._lease = process, lease
            self._active = {
                "id": run_id,
                "state": "running",
                "task_file": task,
                "profile": profile,
                "started_at": datetime.now().astimezone().isoformat(),
            }
            self._save_active()

            # Update RPent Live Monitor state
            self._current_task = {"task": task.replace(".json", "")}
            self._session_state = "running"
            self._task_state = "running"
            self._planner_activity = "busy"
            self._accepting_input = True
            if "habitat_sim" in self._runtime:
                self._runtime["habitat_sim"] = {"status": "starting", "error": None}
            self._interaction_changed_locked()

            self._watcher = threading.Thread(
                target=self._watch, args=(process, log, run_id), daemon=True
            )
            self._watcher.start()
            return dict(self._active)

    def _watch(self, process: subprocess.Popen, log: Any, run_id: str) -> None:
        while process.poll() is None:
            try:
                self._project_run_directory(run_id, reset=True)
            except Exception:
                pass
            time.sleep(0.5)
        code = process.wait()
        log.close()
        try:
            self._project_run_directory(run_id, reset=True)
        except Exception:
            pass
        with self._condition:
            if self._active and self._active.get("id") == run_id:
                if self._active["state"] == "stopping":
                    self._active["state"] = "cancelled"
                    self._task_state = "cancelled"
                else:
                    self._active["state"] = "completed" if code == 0 else "failed"
                    self._task_state = "finished" if code == 0 else "failed"
                self._active["return_code"] = code
                self._save_active()
            self._session_state = "ready"
            self._planner_activity = "idle"
            self._accepting_input = True
            self._process = self._active = None
            if self._lease:
                self._lease.close()
                self._lease = None
            self._interaction_changed_locked()

    def stop(self, run_id: str) -> Dict[str, Any]:
        with self._lock:
            if not self._active or self._active["id"] != run_id or not self._process:
                raise RuntimeError("只能停止当前会话启动的运行")
            process = self._process
            self._active["state"] = "stopping"
            self._save_active()
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        return {"id": run_id, "state": "stopping"}

    def close(self) -> None:
        with self._lock:
            process, watcher, active = self._process, self._watcher, self._active
            if active:
                active["state"] = "stopping"
                self._save_active()
            self._shutdown_requested = True
            self._condition.notify_all()
        if process and process.poll() is None:
            for sig, wait in ((signal.SIGINT, 3), (signal.SIGTERM, 2), (signal.SIGKILL, 1)):
                if process.poll() is not None:
                    break
                try:
                    os.killpg(process.pid, sig)
                    process.wait(timeout=wait)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
        if watcher:
            watcher.join(timeout=2)


class _SimpleToolResult:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._payload)
