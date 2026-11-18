#!/usr/bin/env python3
"""Unit tests for the dependency-light HarnessRuntime.

Run: python3 tests/test_harness_runtime.py
"""
import importlib.util
import os
import sys
import types


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from comm_harness.tools.runtime import HarnessRuntime
from tasks.object_navigation import ObjectNavigationTask
from platforms.hm3d_m3.tools import PLATFORM_TOOLS


EVENTS = []
RESULTS = []


def emit(event_type, step, **fields):
    EVENTS.append((event_type, step, fields))


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name + ((": " + detail) if detail and not cond else ""))


def main():
    EVENTS.clear()
    rt = HarnessRuntime(emit, registry=ObjectNavigationTask(types.SimpleNamespace()).registry(PLATFORM_TOOLS))
    rt.start_episode("ep-test")

    c1 = rt.call("observe_panorama", 0, args={"views": 12})
    rt.receipt(c1, 0, 12, "turn_left_x12", metadata={"sim_steps": 12})
    rt.result(c1, 12, result={"frames": 12})
    check("call/result counted", rt.summary()["tool_calls"] == 1 and rt.summary()["tool_results"] == 1)
    check("receipt counted", rt.summary()["receipts"] == 1)
    check("fresh obligation set", rt.summary()["fresh_observation_required"] is True)

    rt.clear_fresh_observation(12, "panorama_frames")
    check("fresh obligation cleared", rt.summary()["fresh_observation_required"] is False)

    c2 = rt.call("verify_stop", 12, read_only=True)
    rt.result(c2, 12, result={"should_stop": False})
    check("read-only tool has no receipt", rt.summary()["receipts"] == 1)

    ok_first = rt.terminal(12, "test_done")
    ok_second = rt.terminal(13, "duplicate")
    check("terminal emitted once", ok_first and not ok_second)
    check("duplicate terminal records violation", rt.summary()["violations"] == 1)

    event_names = [e[0] for e in EVENTS]
    for expected in [
        "HARNESS_EPISODE_START",
        "HARNESS_TOOL_CALL",
        "HARNESS_RECEIPT",
        "HARNESS_TOOL_RESULT",
        "HARNESS_OBLIGATION_SET",
        "HARNESS_OBLIGATION_CLEARED",
        "HARNESS_TERMINAL",
        "HARNESS_VIOLATION",
    ]:
        check(f"emitted {expected}", expected in event_names)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
