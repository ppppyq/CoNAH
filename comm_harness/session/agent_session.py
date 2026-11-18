"""Platform-agnostic single-robot AgentSession, modeled on RPent's session runner.

Connects:
- ``Planner`` (``ApiAgentLoop`` / ``CodexPlanner`` / ``FlashPlanner`` via ``build_planner``)
- ``Toolkit`` (with ``ToolRegistry`` & ``HarnessRuntime`` receipts)
- ``EnvState`` (step records and artifacts in ``run_dir``)
- ``PlatformSpec`` (any registered embodied or radio platform)

Strictly separates the Harness execution lifecycle from post-run Evaluator metrics.
"""
from __future__ import annotations

import json
from pathlib import Path
import queue
import time
from typing import Any, Dict, Optional, Union

from comm_harness.planner import Budget, Planner, PlannerResult, build_planner
from comm_harness.session.base import EnvState
from comm_harness.tools.runtime import HarnessRuntime
from comm_harness.tools.toolkit import Toolkit


class AgentSession:
    """Run one embodied agent's Harness loop over any ``Toolkit`` and ``Planner``."""

    def __init__(
        self,
        *,
        run_dir: Union[Path, str],
        toolkit: Toolkit,
        planner: Optional[Planner] = None,
        planner_config: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        env_state: Optional[EnvState] = None,
        runtime: Optional[HarnessRuntime] = None,
        dashboard_events: Optional[Any] = None,
        node_id: str = "robot_0",
        episode_id: str = "episode_0",
    ) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.node_id = node_id
        self.episode_id = episode_id
        self.env_state = env_state if env_state is not None else EnvState(self.run_dir)
        self.events_path = self.run_dir / "events.jsonl"
        self._event_seq = 0

        if runtime is None:
            runtime = HarnessRuntime(
                emit=self._emit_runtime_event,
                registry=toolkit.registry,
                trace_phases=True,
            )
        self.runtime = runtime
        self.toolkit = toolkit
        if self.toolkit.runtime is None:
            self.toolkit.runtime = self.runtime
        if self.toolkit._state is None:
            self.toolkit._state = self.env_state
        self.dashboard_events = dashboard_events

        if planner is None:
            cfg = dict(planner_config or {})
            mode = cfg.get("planner") or cfg.get("mode") or "api"
            model_name = cfg.get("model") or "deepseek:deepseek-flash"
            budget = Budget(
                max_calls=int(cfg.get("max_calls", 40)),
                max_tokens=int(cfg.get("max_total_tokens", 150000)),
            )
            planner = build_planner(
                mode,
                model=model_name,
                provider=cfg.get("provider"),
                base_url=cfg.get("base_url"),
                api_key=api_key,
                output_dir=self.run_dir,
                max_tokens=int(cfg.get("max_output_tokens", 2048)),
                planner_timeout_s=float(cfg.get("timeout_s", 60.0)),
                no_images=bool(cfg.get("no_images", False)),
                budget=budget,
                emit=self._emit_planner_event,
                node_id=self.node_id,
            )
        self.planner = planner

    def _write_event_line(self, payload: Dict[str, Any]) -> None:
        self._event_seq += 1
        record = {
            "event_sequence": self._event_seq,
            "ts": time.time(),
            "node_id": self.node_id,
            **payload,
        }
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _emit_runtime_event(self, event_type: str, event_step: int, **fields: Any) -> None:
        self._write_event_line({"event": event_type, "step": int(event_step), **fields})

    def _emit_planner_event(self, event_type: str, **fields: Any) -> None:
        step = self.toolkit._current_step()
        self._write_event_line({"event": event_type.upper(), "step": int(step), **fields})

    def solve(
        self,
        *,
        system_prompt: str,
        user_message: Any,
        max_turns: int = 30,
        input_queue: Optional["queue.Queue[Optional[str]]"] = None,
        dashboard_interaction: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Execute the agent's Harness cycle and record traces without oracle leakage."""
        started = time.monotonic()
        self.runtime.start_episode(self.episode_id, step=0, node_id=self.node_id)
        catalog = [
            {
                "name": t.name,
                "side_effect": t.side_effect.value,
                "readonly": t.readonly,
                "step_consuming": t.step_consuming,
            }
            for t in self.toolkit.list_tools()
        ]
        (self.run_dir / "tool_catalog.json").write_text(
            json.dumps({"node_id": self.node_id, "tools": catalog}, indent=2),
            encoding="utf-8",
        )
        try:
            outcome: PlannerResult = self.planner.solve(
                system_prompt=system_prompt,
                user_message=user_message,
                toolkit=self.toolkit,
                max_turns=max_turns,
                input_queue=input_queue,
                dashboard_interaction=dashboard_interaction,
            )
            done = bool(
                outcome.finish_result is not None
                and outcome.finish_result.get("done", True)
            )
            final_step = self.toolkit._current_step()
            self.runtime.terminal(
                final_step,
                reason="agent_finished" if done else "cycle_ended",
                done=done,
            )
            harness_summary = {
                "node_id": self.node_id,
                "episode_id": self.episode_id,
                "done": done,
                "steps_executed": final_step,
                "elapsed_s": round(time.monotonic() - started, 3),
                "planner_stats": outcome.stats,
                "planner_error": outcome.error,
                "harness": self.runtime.summary(),
            }
            # Evaluator metrics are collected only AFTER the Harness loop finishes
            eval_metrics = self.toolkit.evaluation_metrics(success=None)
            result_payload = {
                "completed": True,
                "harness_lifecycle": harness_summary,
                "metrics": eval_metrics,
            }
            (self.run_dir / "result.json").write_text(
                json.dumps(result_payload, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            return result_payload
        finally:
            self.toolkit.close()
