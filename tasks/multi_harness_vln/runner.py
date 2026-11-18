"""Multi-Robot Collaborative HarnessVLN Worker Runner.

Instantiates K independent ``RobotHarnessVLN`` instances over a shared
``MultiAgentSimCoordinator`` and ``CollaborativeMemoryHub`` (IdealTransport),
running all K robots in parallel lockstep ticks.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback
from typing import Any, Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from comm_harness import telemetry


def _load_episode_and_goals(settings: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]], int]:
    dataset_path = Path(settings["episodes"])
    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    episodes = raw["episodes"] if isinstance(raw, dict) else raw
    ep_id = str(settings["episode_id"])
    matches = [e for e in episodes if str(e["episode_id"]) == ep_id]
    if len(matches) != 1:
        raise ValueError(f"Episode {ep_id!r} not found uniquely in {dataset_path}")
    ep = matches[0]

    max_agents = int(ep["num_agents"])
    num_robots = int(settings.get("num_robots", min(2, max_agents)))
    if not (1 <= num_robots <= max_agents):
        raise ValueError(f"num_robots={num_robots} out of bounds for episode with {max_agents} agents")

    goal_indices = settings.get("goal_indices")
    if not goal_indices:
        # Default to all ON / TN goals in the episode
        goal_indices = [
            i for i, g in enumerate(ep["goals_set"])
            if g.get("modality") in ("ON", "TN")
        ]
        if not goal_indices:
            goal_indices = list(range(len(ep["goals_set"])))

    selected_goals: List[Dict[str, Any]] = []
    for idx in goal_indices:
        idx = int(idx)
        g = ep["goals_set"][idx]
        cat = str(g["object_category"])
        modality = str(g.get("modality", "ON"))
        if modality == "TN" and g.get("tn_text"):
            instr = str(g["tn_text"]).strip()
            if instr.lower().startswith("instruction:"):
                instr = instr.split(":", 1)[1].strip()
        else:
            instr = f"Find the {cat}"
        selected_goals.append({
            "goal_id": f"g{idx}",
            "goal_index": idx,
            "category": cat,
            "modality": modality,
            "instruction": instr,
        })
    return ep, selected_goals, num_robots


def _draw_multi_robot_trajectories(
    out_path: Path,
    episode: Dict[str, Any],
    selected_goals: List[Dict[str, Any]],
    trajectories: List[List[List[float]]],
    team_eval: Dict[str, Any],
) -> None:
    try:
        fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
        colors = ["#2563eb", "#16a34a", "#ea580c", "#9333ea"]
        for i, traj in enumerate(trajectories):
            pts = np.asarray(traj, dtype=np.float32)
            if len(pts) == 0:
                continue
            c = colors[i % len(colors)]
            ax.plot(pts[:, 0], pts[:, 2], "-", color=c, linewidth=2.2, label=f"robot_{i} path")
            ax.scatter(pts[0, 0], pts[0, 2], marker="o", s=70, color=c, edgecolors="black", zorder=5)
            ax.scatter(pts[-1, 0], pts[-1, 2], marker="^", s=90, color=c, edgecolors="black", zorder=6)

        goal_colors = ["#dc2626", "#d97706", "#0891b2", "#7c3aed", "#be185d"]
        for j, g in enumerate(selected_goals):
            gidx = int(g["goal_index"])
            raw_goal = episode["goals_set"][gidx]
            rpts = np.asarray(raw_goal.get("reach_points", []), dtype=np.float32)
            gc = goal_colors[j % len(goal_colors)]
            if len(rpts) > 0:
                ax.scatter(
                    rpts[:, 0],
                    rpts[:, 2],
                    marker="*",
                    s=140,
                    color=gc,
                    edgecolors="black",
                    label=f"goal {g['goal_id']} ({g['category']})",
                    zorder=7,
                )

        ax.set_title(
            f"Multi-Robot HarnessVLN ({episode['episode_id']}) | "
            f"Team SR={team_eval['team_success_rate']:.2f}, SPL={team_eval['team_spl']:.2f}"
        )
        ax.set_xlabel("World X (m)")
        ax.set_ylabel("World Z (m)")
        ax.axis("equal")
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.legend(loc="best", fontsize=8)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.tight_layout()
        fig.savefig(str(out_path))
        plt.close(fig)
    except Exception:
        pass


def _parse_step_narration_data(run_dir: Path, num_robots: int) -> Tuple[List[Dict[int, Dict[str, Any]]], Dict[int, List[str]]]:
    """Extract per-robot, per-step tool calls, VLM decisions, low-level actions, and team messages."""
    per_robot_info: List[Dict[int, Dict[str, Any]]] = [{} for _ in range(num_robots)]
    comm_by_step: Dict[int, List[str]] = {}

    action_map_zh = {
        0: "STOP (停止并请求目标确认)",
        1: "FORWARD (前进 0.25m)",
        2: "TURN_LEFT (左转 30°)",
        3: "TURN_RIGHT (右转 30°)",
    }

    # 1. Parse events.jsonl for cross-robot communication
    events_path = run_dir / "events.jsonl"
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("event") == "message_sent":
                s = int(ev.get("created_tick", 0))
                sender = ev.get("sender", "")
                kind = ev.get("kind", "")
                payload = ev.get("payload", {}) or {}
                if kind == "GOAL_ASSIGNED":
                    msg = f"📡 [{sender}] 认领任务目标 {payload.get('goal_id')} ({payload.get('category')}): {payload.get('instruction', '')[:48]}"
                elif kind == "PLACE_OBSERVED":
                    msg = f"📡 [{sender}] 共享房间拓扑记忆 ({payload.get('place_id')} @ {payload.get('world_xz')}): {payload.get('place_summary', '')[:65]}"
                elif kind == "OBJECT_SIGHTING":
                    msg = f"📡 [{sender}] 🔥 广播队友目标 3D 线索 [{payload.get('category')}] @ 全局坐标 {payload.get('world_xz')} (来源: {payload.get('source')})"
                elif kind == "COLLABORATIVE_GOAL_VERIFIED":
                    msg = f"📡 [{sender}] ✅ 顺路近距验证队友目标 [{payload.get('category')}] @ 全局坐标 {payload.get('world_xz')}"
                elif kind == "INTENT_CLAIM":
                    msg = f"📡 [{sender}] 广播导航意图 -> 前往「{payload.get('description', '')[:42]}」@ {payload.get('target_world_xz')} ({payload.get('planned_action')})"
                elif kind == "GOAL_FOUND":
                    msg = f"📡 [{sender}] 🎯 宣告完成目标 {payload.get('goal_id')} ({payload.get('category')}) @ {payload.get('world_xz')}"
                else:
                    msg = f"📡 [{sender}] 发送 {kind}"
                comm_by_step.setdefault(s, []).append(msg)

    # 2. Parse monitor.jsonl for per-step low-level tool calls, receipts, and stop gate events
    monitor_path = run_dir / "monitor.jsonl"
    if monitor_path.is_file():
        for line in monitor_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            rank = rec.get("rank")
            step = rec.get("step")
            if rank is None or step is None or not (0 <= int(rank) < num_robots):
                continue
            r_idx = int(rank)
            s_idx = int(step)
            info = per_robot_info[r_idx].setdefault(s_idx, {})
            ev_name = rec.get("event")
            if ev_name == "HARNESS_TOOL_CALL":
                tname = rec.get("name", "")
                reason = rec.get("reason", "")
                args = rec.get("args", {}) or {}
                acts = args.get("actions", [])
                act_id = int(acts[0]) if acts else -1
                act_zh = action_map_zh.get(act_id, str(act_id))
                info["tool_name"] = tname
                info["tool_reason"] = reason
                info["low_action_zh"] = act_zh
            elif ev_name == "TARGET_SET":
                info["target_desc"] = rec.get("target_description", "")
                info["target_map"] = rec.get("target_map")
                info["planned_action"] = rec.get("planned_action", "NAVIGATE")
            elif ev_name == "STOP_GATE_FAIL":
                info["key_event"] = True
                d_m = rec.get("distance_m", 0.0)
                th_m = rec.get("threshold_m", 1.0)
                f_reason = rec.get("fail_reason", "")
                info["gate_msg"] = f"🛡️ STOP 几何门控拦截: 实测距离 {d_m:.2f}m > 阈值 {th_m:.1f}m ({f_reason})，拒绝误停并继续探索！"
            elif ev_name == "AUTOSTOP":
                info["key_event"] = True
                d_m = rec.get("distance_m", 0.0)
                info["gate_msg"] = f"🎯 预算收尾视觉确认通过 (AUTOSTOP): 前方深度实测距离 {d_m:.2f}m <= 2.5m，执行最终停止！"

    # 3. Parse debug_logs for exact VLM calls (todo, planning, replan, grounding, verify)
    for r_idx in range(num_robots):
        node_id = f"robot_{r_idx}"
        ep_debug = run_dir / "debug_logs" / node_id / "0"
        if not ep_debug.is_dir():
            continue
        for step_dir in sorted(ep_debug.glob("step_*"), key=lambda p: int(p.name.split("_")[-1])):
            s_idx = int(step_dir.name.split("_")[-1])
            info = per_robot_info[r_idx].setdefault(s_idx, {})
            vlm_calls = []
            for sub in sorted(step_dir.iterdir()):
                if not sub.is_dir() or "cached" in sub.name:
                    continue
                resp_file = sub / "response.txt"
                if not resp_file.is_file():
                    continue
                raw = resp_file.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"\{[\s\S]*\}", raw)
                parsed = {}
                if m:
                    cand = m.group()
                    last_open = raw.rfind("\n{")
                    for c in ([raw[last_open + 1:], cand] if last_open != -1 else [cand]):
                        fixed = re.sub(
                            r":\s*None\b", ": null",
                            re.sub(r":\s*False\b", ": false", re.sub(r":\s*True\b", ": true", c)),
                        )
                        try:
                            parsed = json.loads(fixed)
                            break
                        except Exception:
                            pass
                kind = sub.name.split("_")[0]
                if kind == "todo":
                    vlm_calls.append("generate_todo_list(初始化任务清单)")
                elif kind in ("planning", "replan"):
                    info["key_event"] = True
                    fn_label = "navigate_or_backtrack(环视高层规划)" if kind == "planning" else "replan_at_backtrack(回溯重规划)"
                    vlm_calls.append(fn_label)
                    act_str = str(parsed.get("action") or parsed.get("direction") or "")
                    stop_flag = parsed.get("stop", False)
                    info["vlm_action"] = f"{act_str} (stop={stop_flag})"
                    if parsed.get("place_summary"):
                        info["place_summary"] = str(parsed["place_summary"])
                    if parsed.get("reasoning_action") or parsed.get("progress_analysis"):
                        info["vlm_reasoning"] = str(parsed.get("reasoning_action") or parsed.get("progress_analysis"))
                elif kind == "grounding":
                    info["key_event"] = True
                    vlm_calls.append("query_llm_bbox(2D视觉框选定位)")
                    bbox = parsed.get("bbox_2d")
                    tgt = parsed.get("target", "")
                    info["grounding_info"] = f"框选目标: 「{tgt}」 BBox={bbox}"
                    if parsed.get("reasoning") and not info.get("vlm_reasoning"):
                        info["vlm_reasoning"] = str(parsed["reasoning"])
                elif kind == "verify":
                    info["key_event"] = True
                    vlm_calls.append("double_check_stop(STOP四向视觉复核)")
                    dec = parsed.get("decision", "")
                    view = parsed.get("target_view", "")
                    bbox = parsed.get("bbox_2d", [])
                    info["verify_info"] = f"复核结论: {dec} (方向={view}, BBox={bbox})"
                    if parsed.get("analysis"):
                        info["vlm_reasoning"] = str(parsed["analysis"])
            if vlm_calls:
                info["vlm_calls"] = vlm_calls

    return per_robot_info, comm_by_step


def _render_narration_banner(
    width: int,
    height: int,
    *,
    node_id: str,
    step: int,
    state: Dict[str, Any],
    is_key_step: bool,
    recent_comm: str,
    font_bold: Any,
    font_reg: Any,
    font_small: Any,
) -> np.ndarray:
    """Render a high-contrast dark narration banner with Chinese explanations of tools, actions, and reasoning."""
    from PIL import Image, ImageDraw

    bg_color = (28, 24, 14) if is_key_step else (18, 22, 30)
    pil_img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(pil_img)

    # Top border line (gold for key decision hold, cyan for normal step)
    border_col = (245, 185, 35) if is_key_step else (56, 139, 253)
    draw.rectangle([(0, 0), (width, 4)], fill=border_col)

    # Line 1: Step + Stage badge + Tool Call + Low-level Action
    vlm_calls = state.get("curr_vlm_calls", [])
    low_act = state.get("low_action_zh", "FORWARD (前进 0.25m)")
    tool_name = state.get("tool_name", "fmm_navigate")
    tool_reason = state.get("tool_reason", "")

    if vlm_calls:
        stage_zh = f"🧠 VLM 高层推理解算 (暂停慢放讲解) | 调用: {' + '.join(vlm_calls)}"
        badge_col = (255, 210, 70)
    elif tool_name == "observe_panorama" or "panorama" in str(tool_reason):
        stage_zh = f"🔄 360° 环视扫描采集 (observe_panorama) | 动作: {low_act} (旋转采集4方向RGB-D观测)"
        badge_col = (110, 210, 255)
    elif "backtrack" in str(tool_name) or "backtrack" in str(tool_reason):
        stage_zh = f"⏪ WorldGraph 拓扑路径回溯 ({tool_name}) | 动作: {low_act}"
        badge_col = (255, 160, 90)
    else:
        stage_zh = f"🚶 FMM 局部避障导航 ({tool_name}) | 底层动作: {low_act}"
        badge_col = (130, 235, 155)

    header_text = f"[{node_id.upper()} | Step {step:03d}]  {stage_zh}"
    draw.text((18, 10), header_text[:115], font=font_bold, fill=badge_col)

    # Line 2: High-level VLM Decision + Grounded Target / Gate Status
    vlm_act = state.get("last_vlm_action", "初始环视建图中...")
    ground_str = state.get("last_grounding", "")
    verify_str = state.get("curr_verify") or state.get(" curr_gate") or ""
    if state.get("curr_gate"):
        line2 = f"⚡ {state['curr_gate']}"
        col2 = (255, 120, 120) if "拦截" in state["curr_gate"] else (100, 255, 140)
    elif verify_str:
        line2 = f"🔍 停止确认 (Verify): {verify_str}  |  高层规划: {vlm_act}"
        col2 = (255, 220, 110)
    else:
        line2 = f"🎯 高层决策: {vlm_act}   |   {ground_str or '等待视觉框选定位...'}"
        col2 = (235, 240, 248)
    draw.text((18, 46), line2[:125], font=font_reg, fill=col2)

    # Line 3: VLM Reasoning & Place Summary
    reasoning = state.get("last_reasoning", "")
    place_sum = state.get("last_place_summary", "")
    if reasoning:
        r_clean = " ".join(reasoning.split())
        line3 = f"💬 VLM 推理依据: {r_clean[:130]}"
    elif place_sum:
        line3 = f"🏠 房间记忆 (PlaceNode): {place_sum[:130]}"
    else:
        line3 = "🏠 正在构建局部占据栅格地图 (Semantic_Mapping) 与拓扑世界图 (WorldGraph)..."
    draw.text((18, 82), line3, font=font_small, fill=(195, 205, 220))

    # Line 4: Cross-Robot Communication over IdealTransport
    comm_line = recent_comm or "📡 跨机通信 (IdealTransport): 双机保持实时位置、房间记忆 (PLACE_OBSERVED) 与目标线索 (OBJECT_SIGHTING) 同步"
    draw.text((18, 114), comm_line[:135], font=font_small, fill=(125, 215, 255))

    # Convert RGB PIL to BGR numpy for OpenCV VideoWriter
    return np.asarray(pil_img, dtype=np.uint8)[:, :, ::-1].copy()


def _compile_multi_robot_videos(run_dir: Path, num_robots: int, fps: int = 10) -> Dict[str, str]:
    """Compile slow-paced, Chinese-narrated MP4 videos from combined_stepXXXX.png frames and logs."""
    import subprocess
    import cv2
    from PIL import ImageFont

    video_dir = run_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    generated: Dict[str, str] = {}

    font_path_bold = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"
    font_path_reg = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    try:
        font_bold = ImageFont.truetype(font_path_bold, 22)
        font_reg = ImageFont.truetype(font_path_reg, 19)
        font_small = ImageFont.truetype(font_path_reg, 16)
    except Exception:
        font_bold = font_reg = font_small = ImageFont.load_default()

    per_robot_info, comm_by_step = _parse_step_narration_data(run_dir, num_robots)

    per_robot_frames: List[Dict[int, Path]] = []
    for i in range(num_robots):
        node_id = f"robot_{i}"
        rgb_root = run_dir / "rgb" / node_id
        step_map: Dict[int, Path] = {}
        if rgb_root.is_dir():
            for p in sorted(rgb_root.rglob("combined_step*.png")):
                m = re.search(r"combined_step(\d+)\.png$", p.name)
                if m:
                    step_map[int(m.group(1))] = p
        per_robot_frames.append(step_map)

    banner_h = 148
    normal_hold_frames = 4    # 0.4s per normal walking/turning step (2.5 steps/sec, easy to watch)
    key_hold_frames = 28      # 2.8s freeze hold on key VLM reasoning / grounding / verify / comm steps

    # 1. Per-robot narrated HD videos (1920 x 1228)
    for i in range(num_robots):
        node_id = f"robot_{i}"
        step_map = per_robot_frames[i]
        if not step_map:
            continue
        raw_mp4 = video_dir / f"{node_id}_raw.mp4"
        final_mp4 = video_dir / f"{node_id}.mp4"
        ordered_steps = sorted(step_map.keys())
        first_img = cv2.imread(str(step_map[ordered_steps[0]]))
        if first_img is None:
            continue
        h, w = first_img.shape[:2]
        out_h = h + banner_h
        vw = cv2.VideoWriter(str(raw_mp4), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (w, out_h))

        run_state: Dict[str, Any] = {}
        last_comm = ""
        for idx_s, s in enumerate(ordered_steps):
            im = cv2.imread(str(step_map[s]))
            if im is None:
                continue
            # Accumulate any comm messages up to step s
            if s in comm_by_step:
                last_comm = comm_by_step[s][-1]
            s_info = per_robot_info[i].get(s, {})
            run_state["tool_name"] = s_info.get("tool_name", "fmm_navigate")
            run_state["tool_reason"] = s_info.get("tool_reason", "")
            run_state["low_action_zh"] = s_info.get("low_action_zh", "FORWARD (前进 0.25m)")
            run_state["curr_vlm_calls"] = s_info.get("vlm_calls", [])
            run_state["curr_verify"] = s_info.get("verify_info", "")
            run_state["curr_gate"] = s_info.get("gate_msg", "")
            if s_info.get("vlm_action"):
                run_state["last_vlm_action"] = s_info["vlm_action"]
            if s_info.get("grounding_info"):
                run_state["last_grounding"] = s_info["grounding_info"]
            elif s_info.get("target_desc"):
                run_state["last_grounding"] = f"追踪目标: 「{s_info['target_desc']}」 (地图网格 {s_info.get('target_map')})"
            if s_info.get("vlm_reasoning"):
                run_state["last_reasoning"] = s_info["vlm_reasoning"]
            if s_info.get("place_summary"):
                run_state["last_place_summary"] = s_info["place_summary"]

            is_key = bool(s_info.get("key_event") or s in comm_by_step or idx_s == len(ordered_steps) - 1)
            banner = _render_narration_banner(
                w, banner_h,
                node_id=node_id,
                step=s,
                state=run_state,
                is_key_step=is_key,
                recent_comm=last_comm,
                font_bold=font_bold,
                font_reg=font_reg,
                font_small=font_small,
            )
            combined_frame = np.vstack([im, banner])
            repeat = key_hold_frames if is_key else normal_hold_frames
            for _ in range(repeat):
                vw.write(combined_frame)
        vw.release()
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(raw_mp4),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22",
                    str(final_mp4),
                ],
                check=True,
            )
            raw_mp4.unlink(missing_ok=True)
            generated[node_id] = str(final_mp4)
        except Exception:
            if raw_mp4.is_file():
                raw_mp4.rename(final_mp4)
                generated[node_id] = str(final_mp4)

    # 2. Combined synchronized multi-robot narrated video
    all_steps = sorted({s for sm in per_robot_frames for s in sm.keys()})
    if all_steps and num_robots >= 1:
        raw_team = video_dir / "multi_robot_rollout_raw.mp4"
        final_team = video_dir / "multi_robot_rollout.mp4"
        panel_w, img_h = 1440, 810
        sub_banner_h = 148
        panel_total_h = img_h + sub_banner_h
        total_h = panel_total_h * num_robots
        vw = cv2.VideoWriter(str(raw_team), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (panel_w, total_h))

        last_imgs: List[Any] = [np.full((img_h, panel_w, 3), 255, dtype=np.uint8) for _ in range(num_robots)]
        run_states: List[Dict[str, Any]] = [{} for _ in range(num_robots)]
        last_comm = ""

        for idx_s, s in enumerate(all_steps):
            if s in comm_by_step:
                last_comm = comm_by_step[s][-1]
            any_key = bool(s in comm_by_step or idx_s == len(all_steps) - 1)
            panels = []
            for i in range(num_robots):
                node_id = f"robot_{i}"
                if s in per_robot_frames[i]:
                    im = cv2.imread(str(per_robot_frames[i][s]))
                    if im is not None:
                        last_imgs[i] = cv2.resize(im, (panel_w, img_h), interpolation=cv2.INTER_AREA)
                s_info = per_robot_info[i].get(s, {})
                st = run_states[i]
                st["tool_name"] = s_info.get("tool_name", st.get("tool_name", "fmm_navigate"))
                st["tool_reason"] = s_info.get("tool_reason", "")
                st["low_action_zh"] = s_info.get("low_action_zh", "FORWARD (前进 0.25m)")
                st["curr_vlm_calls"] = s_info.get("vlm_calls", [])
                st["curr_verify"] = s_info.get("verify_info", "")
                st["curr_gate"] = s_info.get("gate_msg", "")
                if s_info.get("vlm_action"):
                    st["last_vlm_action"] = s_info["vlm_action"]
                if s_info.get("grounding_info"):
                    st["last_grounding"] = s_info["grounding_info"]
                elif s_info.get("target_desc"):
                    st["last_grounding"] = f"追踪目标: 「{s_info['target_desc']}」 (网格 {s_info.get('target_map')})"
                if s_info.get("vlm_reasoning"):
                    st["last_reasoning"] = s_info["vlm_reasoning"]
                if s_info.get("place_summary"):
                    st["last_place_summary"] = s_info["place_summary"]

                is_key_i = bool(s_info.get("key_event"))
                if is_key_i:
                    any_key = True
                banner_i = _render_narration_banner(
                    panel_w, sub_banner_h,
                    node_id=node_id,
                    step=s,
                    state=st,
                    is_key_step=is_key_i or (s in comm_by_step),
                    recent_comm=last_comm,
                    font_bold=font_bold,
                    font_reg=font_reg,
                    font_small=font_small,
                )
                panels.append(np.vstack([last_imgs[i], banner_i]))

            stacked = np.vstack(panels)
            repeat = key_hold_frames if any_key else normal_hold_frames
            for _ in range(repeat):
                vw.write(stacked)
        vw.release()
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(raw_team),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22",
                    str(final_team),
                ],
                check=True,
            )
            raw_team.unlink(missing_ok=True)
            generated["team_video"] = str(final_team)
        except Exception:
            if raw_team.is_file():
                raw_team.rename(final_team)
                generated["team_video"] = str(final_team)

    return generated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    settings = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[2]
    os.chdir(str(root))

    coordinator = None
    telemetry.configure(run_dir, "team-coordinator", role="coordinator")
    telemetry.phase("BOOTSTRAP")
    telemetry.emit("SERVICE_STATUS", service="worker", status="running")
    started = time.monotonic()

    event_lock = threading.Lock()
    events_file = (run_dir / "events.jsonl").open("w", encoding="utf-8")
    event_seq = [0]

    def emit_shared_event(event_type: str, **fields: Any) -> None:
        with event_lock:
            event_seq[0] += 1
            rec = {
                "event": event_type,
                "event_sequence": event_seq[0],
                "ts": time.time(),
                **fields,
            }
            events_file.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            events_file.flush()

    try:
        episode, selected_goals, num_robots = _load_episode_and_goals(settings)
        task_meta = {
            "source_episode_id": str(episode["episode_id"]),
            "num_robots": num_robots,
            "selected_goals": selected_goals,
            "categories": [g["category"] for g in selected_goals],
            "task_scope": f"{num_robots} independent HarnessVLN robots collaborating on {len(selected_goals)} goals",
        }
        (run_dir / "task.json").write_text(json.dumps(task_meta, indent=2), encoding="utf-8")
        telemetry.emit("SERVICE_STATUS", service="dataset", status="ready")

        assets = Path(settings["assets"])
        expected = json.loads(Path(settings["asset_manifest"]).read_text(encoding="utf-8"))
        checked_assets = []
        for relative, info in expected.items():
            path = assets / relative
            if not path.is_file() or path.stat().st_size != info["size"]:
                raise ValueError("Missing or incomplete model asset: " + relative)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != info["sha256"]:
                raise ValueError("Asset integrity mismatch: " + relative)
            checked_assets.append(relative)
        telemetry.emit("SERVICE_STATUS", service="assets", status="ready")
        telemetry.emit(
            "SERVICE_STATUS",
            service="model",
            status="configured",
            model=settings["planner"].get("model"),
            provider=settings["planner"].get("provider"),
        )

        import torch
        import platforms.hm3d_m3.habitat
        import platforms.hm3d_m3.navigation.environments
        from platforms.hm3d_m3.navigation.config import get_config
        from platforms.hm3d_m3.navigation.misc import get_device, seed_everything
        from comm_harness.planner.model_config import ModelConfig
        from tasks.harness_vln.agent import VLMReasoningAgent
        from tasks.multi_harness_vln.collaboration import CollaborativeMemoryHub
        from tasks.multi_harness_vln.env_adapter import MultiAgentSimCoordinator, SingleRobotEnvProxy
        from tasks.multi_harness_vln.multi_evaluator import RobotHarnessVLN, SharedGroundedSAM

        ModelConfig.from_env()
        yaml_path = root / "tasks/harness_vln/configs/harness_vln_objectnav.yaml"
        base_cfg = get_config(str(yaml_path))
        base_cfg.defrost()
        base_cfg.local_rank, base_cfg.world_size = 0, 1
        base_cfg.EXP_NAME = run_dir.name
        base_cfg.EVAL_DATA_ROOT = str(run_dir.parent)
        base_cfg.NUM_ENVIRONMENTS = 1
        base_cfg.TORCH_GPU_ID, base_cfg.TORCH_GPU_IDS, base_cfg.SIMULATOR_GPU_IDS = 0, [0], [0]
        base_cfg.USE_FMM, base_cfg.USE_NAVDP = True, False
        base_cfg.DEBUG_LOGGING = True

        detector_config, replacements = re.subn(
            r"^text_encoder_type\s*=.*$",
            "text_encoder_type = " + repr(str(assets / "bert-base-uncased")),
            (assets / "GroundingDINO_SwinT_OGC.py").read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        if replacements != 1:
            raise ValueError("Expected one text_encoder_type in detector config")
        detector_config_path = run_dir / "grounding_dino_config.py"
        detector_config_path.write_text(detector_config, encoding="utf-8")

        base_cfg.MAP.GROUNDING_DINO_CONFIG_PATH = str(detector_config_path)
        base_cfg.MAP.GROUNDING_DINO_CHECKPOINT_PATH = str(assets / "groundingdino_swint_ogc.pth")
        base_cfg.MAP.RepViTSAM_CHECKPOINT_PATH = str(assets / "repvit_sam.pt")
        base_cfg.MAP.REPVITSAM = 1
        base_cfg.TASK_CONFIG.DATASET.DATA_PATH = str(settings["episodes"])
        base_cfg.TASK_CONFIG.DATASET.SCENES_DIR = str(settings["scenes"])
        base_cfg.TASK_CONFIG.ENVIRONMENT.MAX_EPISODE_STEPS = int(settings["max_steps"])
        base_cfg.TASK_CONFIG.SEED = int(settings.get("seed", 1123))
        base_cfg.EVAL.HARNESS_ENABLED = True
        base_cfg.EVAL.SAVE_RESULTS = True
        base_cfg.EVAL.EPISODE_COUNT = 1
        base_cfg.EVAL.MAX_IMAGES_PER_REQUEST = int(settings["planner"].get("max_images_per_request", 50))
        base_cfg.freeze()

        provenance = {
            "evaluator": inspect.getfile(RobotHarnessVLN),
            "agent": inspect.getfile(VLMReasoningAgent),
            "semantic_perception": str(root / "platforms/hm3d_m3/harness_vln/semantic_prediction.py"),
            "model_assets_checked": checked_assets,
            "task": task_meta,
            "upstream_python_packages_loaded": [
                m for m in sys.modules if m in ("vlnce_baselines", "habitat_extensions")
            ],
        }
        if provenance["upstream_python_packages_loaded"]:
            raise RuntimeError("Unexpected imports from original repository namespaces")

        if args.preflight:
            (run_dir / "preflight.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
            return

        if not torch.cuda.is_available():
            raise RuntimeError("Multi-agent HarnessVLN requires CUDA")
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        telemetry.emit("GPU_SNAPSHOT", free_bytes=free_bytes, total_bytes=total_bytes)
        reserve = 768 * 1024 ** 2
        allowance = min(settings.get("max_gpu_memory_gb", 6) * 1024 ** 3, free_bytes - reserve)
        torch.cuda.set_per_process_memory_fraction(allowance / total_bytes, 0)
        provenance["gpu_allocation_limit_bytes"] = int(allowance)
        provenance["gpu_free_bytes_at_start"] = free_bytes
        (run_dir / "preflight.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")

        seed_everything(base_cfg.TASK_CONFIG.SEED)
        torch.set_num_threads(1)
        torch.backends.cudnn.benchmark = False

        from habitat import logger
        (run_dir / "logs").mkdir(exist_ok=True)
        logger.add_filehandler(str(run_dir / "logs" / "multi_harness_vln.log"))
        planner_logger = logging.getLogger("habitat")
        if planner_logger is not logger:
            planner_logger.handlers = list(logger.handlers)
            planner_logger.setLevel(logger.level)
            planner_logger.propagate = False

        node_names = [f"robot_{i}" for i in range(num_robots)]
        hub = CollaborativeMemoryHub(
            episode_id=str(episode["episode_id"]),
            node_names=node_names,
            goals=selected_goals,
            emit_event=emit_shared_event,
        )

        with telemetry.service("simulator"):
            coordinator = MultiAgentSimCoordinator(
                habitat_config=base_cfg,
                episode=episode,
                scenes_dir=str(settings["scenes"]),
                num_robots=num_robots,
                max_steps=int(settings["max_steps"]),
                hub=hub,
                selected_goals=selected_goals,
            )

        device = get_device(base_cfg.TORCH_GPU_ID)
        shared_sam = SharedGroundedSAM(base_cfg, device)

        robots: List[RobotHarnessVLN] = []
        for i in range(num_robots):
            node_id = f"robot_{i}"
            rcfg = base_cfg.clone()
            rcfg.defrost()
            rcfg.local_rank = i
            rcfg.world_size = num_robots
            for attribute, directory in [
                ("CHECKPOINT_FOLDER", f"stats/{node_id}"),
                ("EVAL_CKPT_PATH_DIR", f"stats/{node_id}"),
                ("RESULTS_DIR", f"eval_results/{node_id}"),
                ("VIDEO_DIR", f"video/{node_id}"),
                ("TENSORBOARD_DIR", f"tensorboard/{node_id}"),
                ("DEBUG_LOG_DIR", f"debug_logs/{node_id}"),
                ("RGB_SAVE_DIR", f"rgb/{node_id}"),
                ("RUNNING_LOG_DIR", f"logs/{node_id}"),
            ]:
                p = run_dir / directory
                p.mkdir(parents=True, exist_ok=True)
                setattr(rcfg, attribute, str(p))
            rcfg.freeze()

            proxy = SingleRobotEnvProxy(coordinator, agent_id=i)
            robot = RobotHarnessVLN(
                rcfg,
                agent_id=i,
                node_id=node_id,
                env_proxy=proxy,
                shared_segment_module=shared_sam,
                hub=hub,
                start_sim_location=coordinator.start_sim_locations[i],
            )
            robot._collect_val_traj()
            robot._initialize_policy()
            robot.agent.reset()
            robots.append(robot)

        telemetry.phase("OBSERVE")
        emit_shared_event(
            "MULTI_HARNESS_START",
            episode_id=str(episode["episode_id"]),
            num_robots=num_robots,
            goals=selected_goals,
        )

        def _run_one_robot(robot_inst: RobotHarnessVLN) -> Dict[str, Any]:
            telemetry.context(node_id=robot_inst.node_id)
            try:
                robot_inst.state_eps = {}
                robot_inst.rollout()
                return {
                    "node_id": robot_inst.node_id,
                    "ok": True,
                    "model_usage": robot_inst.agent.model.get_usage_stats()["model"],
                    "navigation": robot_inst._nav_stats_dict(),
                    "harness": robot_inst.harness.summary() if robot_inst.harness else {},
                }
            finally:
                coordinator.mark_agent_done(robot_inst.agent_id)

        robot_summaries: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=num_robots) as pool:
            futures = [pool.submit(_run_one_robot, r) for r in robots]
            coordinator.serve_on_main_thread(futures)
            for fut in futures:
                robot_summaries.append(fut.result())

        telemetry.phase("FINALIZE")
        team_eval = coordinator.evaluate_team()
        collab_summary = hub.summary()

        _draw_multi_robot_trajectories(
            out_path=run_dir / "eval_results" / "multi_robot_trajectory.png",
            episode=episode,
            selected_goals=selected_goals,
            trajectories=coordinator.trajectories,
            team_eval=team_eval,
        )
        videos_generated = _compile_multi_robot_videos(run_dir, num_robots=num_robots)

        total_calls = sum(int(r["model_usage"].get("calls", 0)) for r in robot_summaries)
        total_requests = sum(int(r["model_usage"].get("requests", 0)) for r in robot_summaries)
        total_in = sum(int(r["model_usage"].get("input_tokens", 0)) for r in robot_summaries)
        total_out = sum(int(r["model_usage"].get("output_tokens", 0)) for r in robot_summaries)
        total_tok = sum(int(r["model_usage"].get("total_tokens", 0)) for r in robot_summaries)

        model_usage = {
            "model_name": settings["planner"]["model"],
            "model": {
                "calls": total_calls,
                "requests": total_requests,
                "input_tokens": total_in,
                "output_tokens": total_out,
                "total_tokens": total_tok,
            },
            "per_robot": {r["node_id"]: r["model_usage"] for r in robot_summaries},
            "total_calls": total_calls,
            "total_tokens": total_tok,
        }

        agg_nav: Dict[str, int] = {}
        for r in robot_summaries:
            for k, v in r["navigation"].items():
                agg_nav[k] = agg_nav.get(k, 0) + int(v)

        total_violations = sum(int(r["harness"].get("violations", 0)) for r in robot_summaries)
        audit = {
            "terminal_step": team_eval["ticks_taken"],
            "per_robot_harness": {r["node_id"]: r["harness"] for r in robot_summaries},
            "violations": total_violations,
            "api_error_count": (run_dir / "console.log").read_text(errors="replace").count("API Call error"),
        }

        result = {
            "method": "multi-harness-vln-mode-b",
            "task": task_meta,
            "metrics": team_eval,
            "collaboration": collab_summary,
            "videos": videos_generated,
            "model_usage": model_usage,
            "navigation": agg_nav,
            "audit": audit,
            "elapsed_s": round(time.monotonic() - started, 3),
            "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "gpu_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
            "completed": True,
        }
        (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (run_dir / "collaboration_summary.json").write_text(
            json.dumps(collab_summary, indent=2), encoding="utf-8"
        )
        emit_shared_event("MULTI_HARNESS_FINISHED", metrics=team_eval, collaboration=collab_summary)
        telemetry.phase("TERMINAL")
        telemetry.emit("RUN_FINISHED", status="completed", success=team_eval["team_success_rate"])
    except BaseException as exc:
        try:
            _compile_multi_robot_videos(run_dir, num_robots=int(settings.get("num_robots", 2)))
        except Exception:
            pass
        telemetry.emit("RUN_FINISHED", status="failed", error_type=type(exc).__name__, error=str(exc))
        failure = {
            "error_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "elapsed_s": round(time.monotonic() - started, 3),
        }
        (run_dir / "failure.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        raise
    finally:
        if coordinator is not None:
            coordinator.close()
        events_file.close()
        telemetry.close()


if __name__ == "__main__":
    main()
