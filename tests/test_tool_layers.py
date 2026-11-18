"""Behavioral checks for the common executor, task policy, RPent-style Toolkit, EnvState, AgentSession, and platform boundary."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

from comm_harness.planner import FlashPlanner
from platforms import PlatformSpec, PromptBundle, get_platform, list_platforms, register
from comm_harness.session.agent_session import AgentSession
from comm_harness.session.base import EnvState
from tasks.object_navigation import ObjectNavigationTask
from comm_harness.tools import (
    HarnessRuntime,
    SideEffect,
    ToolError,
    ToolExecutor,
    ToolOutcome,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    Toolkit,
    iter_tools,
    tool,
)
from platforms.hm3d_m3.tools import HabitatTools


class ToolBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.task = ObjectNavigationTask(SimpleNamespace())
        self.steps = []
        self.private_outputs = [({'rgb': 'frame'}, 0, False, {'distance_to_goal': 9.9})]
        def step(actions):
            self.steps.append(actions)
            return self.private_outputs
        self.platform = HabitatTools(lambda: SimpleNamespace(step=step))
        self.registry = self.task.registry(self.platform.specs)
        self.runtime = HarnessRuntime(lambda event, step, **fields: self.events.append((event, step, fields)),
                                      registry=self.registry)
        self.runtime.start_episode('test')
        self.executor = ToolExecutor(self.registry, self.runtime, self.task.allowed_tools)
        self.platform.register(self.executor)

    def test_action_has_one_receipt_and_never_logs_private_metrics(self):
        result = self.executor.execute('move', {'actions': [1]}, step=0)
        self.assertIs(result, self.private_outputs)
        self.assertEqual(self.steps, [[{'action': 1}]])
        self.assertEqual(self.runtime.summary()['receipts'], 1)
        self.assertEqual(self.runtime.summary()['open_tool_calls'], 0)
        self.assertEqual(self.runtime.summary()['violations'], 0)
        self.assertNotIn('distance_to_goal', json.dumps(self.events))

    def test_unknown_missing_or_wrong_arguments_cannot_move(self):
        for name, args in [('absent', {}), ('move', {}), ('move', {'actions': [True]}),
                           ('move', {'actions': [1, 1]}), ('move', {'actions': [0]}),
                           ('action', {'actions': [0]})]:
            with self.subTest(name=name, args=args), self.assertRaises(ToolError):
                self.executor.execute(name, args, step=0)
        self.assertEqual(self.steps, [])
        self.assertEqual(self.runtime.summary()['receipts'], 0)
        self.assertEqual(self.runtime.summary()['open_tool_calls'], 0)

    def test_failed_platform_call_closes_trace_without_fabricating_receipt(self):
        def fail(actions):
            raise RuntimeError('sensor disconnected')
        self.platform.environment = lambda: SimpleNamespace(step=fail)
        with self.assertRaisesRegex(RuntimeError, 'sensor disconnected'):
            self.executor.execute('turn', {'actions': [2]}, step=0)
        self.assertEqual(self.runtime.summary()['tool_results'], 1)
        self.assertEqual(self.runtime.summary()['receipts'], 0)
        self.assertEqual(self.runtime.summary()['open_tool_calls'], 0)
        returned = [v for e, _, v in self.events if e == 'HARNESS_TOOL_RESULT']
        self.assertFalse(returned[0]['ok'])

    def test_same_executor_accepts_different_capability_and_task_allowlist(self):
        spec = ToolSpec('measure_spectrum', SideEffect.READ_ONLY, required_args=('frequency_hz',))
        calls = []
        def measure(frequency_hz):
            calls.append(frequency_hz)
            return ToolOutcome({'power_dbm': -70}, {'samples': 1})
        survey = ToolExecutor(ToolRegistry(), allowed_tools={'measure_spectrum'})
        survey.bind(spec, measure, owner='test:radio')
        self.assertEqual(survey.execute('measure_spectrum', {'frequency_hz': 2.4e9}, step=0),
                         {'power_dbm': -70})
        self.executor.bind(spec, measure, owner='test:radio')
        with self.assertRaisesRegex(ToolError, 'not allowed'):
            self.executor.execute('measure_spectrum', {'frequency_hz': 2.4e9}, step=0)
        self.assertEqual(calls, [2.4e9])
        with self.assertRaisesRegex(ValueError, 'lacks navigation'):
            self.task.registry((spec,))

    def test_sensor_measures_distance_without_deciding_success(self):
        frames = [{'depth': np.full((10, 10, 1), (1.0 - 0.1) / 4.9, np.float32)}]
        original = frames[0]['depth'].copy()
        evidence = {'view_idx': 0, 'target_view': 'forward', 'bbox_norm': [100, 100, 800, 800]}
        distance, facts = self.executor.execute('query_depth',
            {'frames': frames, 'evidence': evidence}, step=0,
            trace_args={'view_idx': 0, 'bbox_norm': evidence['bbox_norm']})
        self.assertAlmostEqual(distance, 1.0, places=5)
        self.assertNotIn('success', facts)
        self.assertNotIn('should_stop', facts)
        self.assertEqual(self.runtime.summary()['receipts'], 0)
        np.testing.assert_array_equal(frames[0]['depth'], original)
        self.assertNotIn('ndarray', json.dumps(self.events))

    def test_core_imports_no_task_or_platform_and_task_imports_no_habitat(self):
        root = Path(__file__).resolve().parents[1]
        script = (
            'import sys; from comm_harness.tools.executor import ToolExecutor; '
            'from comm_harness.tools.runtime import HarnessRuntime; '
            'assert not any(m.startswith(("platforms", "habitat", "comm_harness.session")) for m in sys.modules); '
            'from tasks.object_navigation import ObjectNavigationTask; '
            'assert not any(m.startswith(("platforms", "habitat", "torch")) for m in sys.modules)')
        subprocess.run([sys.executable, '-c', script], cwd=str(root), check=True)


class StopPolicyTests(unittest.TestCase):
    def check_stop(self, task, depth, std=0, target=None, step=20):
        return task.check_stop(step=step, current_pose=[0, 0, 0], agent_map_x=0, agent_map_y=0,
            reason='test', last_grounded_target=target,
            query_depth=lambda *a: (depth, {'distance_fresh_depth_m': depth,
                'depth_window_std_m': std, **({'depth_invalid_reason': 'missing'} if depth is None else {})}))

    def test_same_sensor_fact_yields_different_task_distance_rules(self):
        normal = ObjectNavigationTask(SimpleNamespace())
        strict = ObjectNavigationTask(SimpleNamespace(STOP_GATE_DEPTH_DISTANCE_M=0.5))
        self.assertTrue(self.check_stop(normal, 1.0)[0])
        self.assertFalse(self.check_stop(strict, 1.0)[0])
        self.assertFalse(self.check_stop(normal, 1.0, std=0.6)[0])

    def test_missing_depth_uses_fresh_projected_target_but_not_stale_target(self):
        target = {'step': 10, 'world': [0.5, 0], 'map': [1, 1], 'source': 'grounding'}
        task = ObjectNavigationTask(SimpleNamespace())
        ok, info = self.check_stop(task, None, target=target)
        self.assertTrue(ok)
        self.assertEqual(info['distance_basis'], 'map_target')
        self.assertFalse(self.check_stop(task, None, target=target, step=100)[0])
        self.assertFalse(self.check_stop(task, None)[0])


class UnifiedToolkitAndSessionTests(unittest.TestCase):
    def test_decorator_tools_env_state_and_agent_session_across_platforms(self):
        class GenericRoverTools:
            def __init__(self, env_state):
                self.env_state = env_state
                self.pose = [0.0, 0.0]

            @tool(readonly=True, side_effect=SideEffect.SPECTRUM_SENSING)
            def sense_channel(self, channel_id: str = "mesh_0") -> ToolResult:
                """Measure wireless link SINR and RSSI on a channel.

                Args:
                    channel_id: Wireless channel identifier.
                """
                return ToolResult(data={"channel_id": channel_id, "rssi_dbm": -48, "sinr_db": 24.5})

            @tool(side_effect=SideEffect.WORLD_MUTATING, step_consuming=True, physical_motion=True)
            def drive(self, dx: float, dy: float) -> ToolResult:
                """Drive the rover by a delta displacement.

                Args:
                    dx: Delta X in meters.
                    dy: Delta Y in meters.
                """
                self.pose[0] += dx
                self.pose[1] += dy
                with self.env_state.record_step(
                    state={"pose": list(self.pose)},
                    command={"action": "drive", "dx": dx, "dy": dy},
                    result={"pose": list(self.pose)},
                ):
                    self.env_state.save("pose.json", {"pose": list(self.pose)})
                return ToolResult(data={"pose": list(self.pose)}, consumed_steps=1)

            @tool(side_effect=SideEffect.NETWORK_TX)
            def broadcast_beacon(self, summary: str) -> ToolResult:
                """Broadcast a status beacon over the wireless mesh.

                Args:
                    summary: Status payload text.
                """
                return ToolResult(data={"sent": True, "bytes": len(summary)})

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            env_state = EnvState(run_dir)
            rover = GenericRoverTools(env_state)
            toolkit = Toolkit(state=env_state)
            toolkit.add_tools(iter_tools(rover))

            register(
                PlatformSpec(
                    name="test_ackermann_rover",
                    toolkit_factory=lambda: toolkit,
                    prompts=PromptBundle(system="Rover system", user="Rover task"),
                    capabilities=("sense_channel", "drive", "broadcast_beacon"),
                ),
                replace=True,
            )
            self.assertIn("test_ackermann_rover", list_platforms())
            spec = get_platform("test_ackermann_rover")
            self.assertEqual(spec.capabilities[0], "sense_channel")

            def flash_runner(tk, tag, note):
                r1 = tk.execute_tool("sense_channel", {"channel_id": "ch_1"})
                self.assertFalse(r1.is_error)
                r2 = tk.execute_tool("drive", {"dx": 1.5, "dy": -0.5})
                self.assertFalse(r2.is_error)
                r3 = tk.execute_tool("broadcast_beacon", {"summary": "at waypoint 1"})
                self.assertFalse(r3.is_error)
                return {"done": True}

            session = AgentSession(
                run_dir=run_dir,
                toolkit=toolkit,
                planner=FlashPlanner(runner=flash_runner, recipe_tag="rover_test"),
                env_state=env_state,
                node_id="rover_0",
                episode_id="ep_rover_1",
            )
            result = session.solve(system_prompt="sys", user_message="go")
            self.assertTrue(result["completed"])
            self.assertTrue(result["harness_lifecycle"]["done"])
            self.assertEqual(result["harness_lifecycle"]["steps_executed"], 1)
            self.assertEqual(result["harness_lifecycle"]["harness"]["tool_calls"], 3)
            self.assertEqual(result["harness_lifecycle"]["harness"]["receipts"], 2)  # drive (WORLD_MUTATING) + broadcast_beacon (NETWORK_TX)
            self.assertEqual(result["harness_lifecycle"]["harness"]["violations"], 0)
            self.assertEqual(env_state.load("pose.json", step=0), {"pose": [1.5, -0.5]})
            self.assertTrue((run_dir / "states.json").is_file())
            self.assertTrue((run_dir / "events.jsonl").is_file())


if __name__ == '__main__':
    unittest.main()
