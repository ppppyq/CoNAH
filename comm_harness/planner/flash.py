"""Planner that replays a deterministic or recorded plan instead of asking an LLM.

Modeled after RPent's ``rpent/planner/flash.py``, while also providing the
tick-level ``decide(node)`` diagnostic behavior for multi-robot smoke checks.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from comm_harness.planner.base import Planner, PlannerResult, invoke_toolkit_sync
from platforms import get_platform

logger = logging.getLogger("flash")


class FlashPlanner(Planner):
    """Replay a recorded/deterministic plan against the robot's toolkit without LLM calls."""

    def __init__(
        self,
        *,
        recipe_tag: str = "default",
        robot_name: str = "hm3d_m3",
        runner: Optional[Callable[[Any, str, Callable[[str], None]], Dict[str, Any]]] = None,
    ) -> None:
        self._recipe_tag = recipe_tag
        self._robot_name = robot_name
        self._runner = runner

    def decide(self, node: Any) -> None:
        """Protocol/environment diagnostic for tick-stepped multi-robot nodes."""
        candidates = [c for c in getattr(node, "candidates", ()) if c.get("kind") == "frontier"]
        if candidates:
            result = node.toolkit.execute(
                "navigate",
                {
                    "candidate_id": candidates[0]["id"],
                    "reason": "flash/scripted environment diagnostic",
                },
            )
            if isinstance(result, dict) and result.get("ok"):
                return
        node.toolkit.execute("observe", {"mode": "panorama"})

    def solve(
        self,
        *,
        system_prompt: str,
        user_message: Any,
        toolkit: Any,
        max_turns: int,
        input_queue: Optional[Any] = None,
        dashboard_interaction: Optional[Any] = None,
    ) -> PlannerResult:
        """Replay the deterministic program for this robot/cell."""
        run_flash = self._runner
        if run_flash is None:
            try:
                spec = get_platform(self._robot_name)
                run_flash = getattr(spec, "run_flash", None)
            except Exception:
                run_flash = None

        started = time.time()
        if run_flash is not None:
            try:
                outcome = run_flash(toolkit, self._recipe_tag, logger.info)
            except Exception as exc:
                return PlannerResult(error=f"{type(exc).__name__}: {exc}")
            elapsed = time.time() - started
            solved = bool(outcome.get("done"))
            return PlannerResult(
                finish_result={
                    "status": "success" if solved else "failure",
                    "summary": (
                        f"replayed the {outcome.get('program', self._recipe_tag)} program: "
                        f"{outcome.get('plan', 0)} actions"
                    ),
                },
                stats={
                    "backend": "flash",
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "turns_used": 0,
                    "tool_calls": outcome.get("plan", 0),
                    "elapsed_s": round(elapsed, 3),
                },
            )

        # Fallback generic diagnostic when no platform-specific flash recipe is registered
        try:
            invoke_toolkit_sync(toolkit, "observe", {"mode": "panorama"})
            elapsed = time.time() - started
            return PlannerResult(
                finish_result={"status": "success", "summary": "executed scripted observe diagnostic"},
                stats={
                    "backend": "flash",
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "turns_used": 0,
                    "tool_calls": 1,
                    "elapsed_s": round(elapsed, 3),
                },
            )
        except Exception as exc:
            return PlannerResult(error=f"{type(exc).__name__}: {exc}")


#: Backward-compatible alias used by TeamSession (`multi-v0`).
ScriptedPlanner = FlashPlanner
