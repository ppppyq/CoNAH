"""Execution telemetry with fake backends, plus historical/live projections."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from comm_harness import telemetry
from comm_harness.communication import IdealTransport
from comm_harness.dashboard.monitoring import MonitorStore, comparison


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        telemetry.configure(self.path, 'robot-2', heartbeat=False)
        self.addCleanup(telemetry.close)
        self.store = MonitorStore()

    def records(self):
        return [json.loads(s) for s in (self.path / 'monitor.jsonl').read_text(encoding='utf-8').splitlines()]

    def snapshot(self, state='running'):
        return self.store.get(self.path, {}, {'state': state})

    def client(self, replies):
        from comm_harness.planner.model_client import ModelClient
        replies = iter(replies)
        def create(**kwargs):
            reply = next(replies)
            if isinstance(reply, BaseException):
                raise reply
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
                                   usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12))
        return ModelClient('unused', 'https://example.invalid', 'deepseek-flash',
                           client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))

    def test_real_client_retry_usage_and_reference_links(self):
        client = self.client([TimeoutError('password=private-value'), '', '{"action":"stop"}'])
        telemetry.phase('PLAN', step=3, episode_id='episode')
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'ob-0001 rc-0002 JSON'},
                     {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,private-image'}}]}]
        with patch('time.sleep'):
            self.assertEqual(client.generate(messages, max_retries=2), '{"action":"stop"}')
        snap = self.snapshot()
        requests = snap['model']['recent_requests']
        self.assertEqual(len(requests), 3)
        self.assertEqual([r['status'] for r in requests], ['error', 'ok', 'ok'])
        self.assertTrue(requests[1]['empty'])
        self.assertEqual(snap['model']['usage']['total_tokens'], 24)
        self.assertEqual(snap['model']['retries'], 2)
        self.assertEqual(requests[0]['prompt_references'], ['ob-0001', 'rc-0002'])
        self.assertEqual(len({r['model_call_id'] for r in requests}), 1)
        self.assertTrue(all(r['duration_s'] >= 0 and r['step'] == 3 for r in requests))
        self.assertNotIn('private-value', json.dumps(self.records()))
        self.assertNotIn('private-image', json.dumps(self.records()))

    def test_budget_and_interrupt_close_call_without_phantom_request(self):
        from comm_harness.planner.model_client import ModelBudgetExceeded
        client = self.client([KeyboardInterrupt()])
        with patch.dict(os.environ, {'COMM_HVL_MAX_CALLS': '0'}), self.assertRaises(ModelBudgetExceeded):
            client.generate([])
        self.assertFalse(self.snapshot()['model']['recent_requests'])
        with self.assertRaises(KeyboardInterrupt):
            client.generate([])
        self.assertEqual(self.snapshot()['model']['recent_requests'][0]['error_type'], 'KeyboardInterrupt')

    def test_agent_json_retry_plan_tool_receipt_correlation(self):
        from tasks.harness_vln.agent import VLMReasoningAgent
        from comm_harness.tools.registry import ToolRegistry, ToolSpec
        from comm_harness.tools.contracts import SideEffect
        from comm_harness.tools.runtime import HarnessRuntime
        from comm_harness.tools.executor import ToolExecutor, ToolOutcome
        agent = VLMReasoningAgent.__new__(VLMReasoningAgent)
        agent.model = self.client(['not JSON', '{"action":"move"}'])
        telemetry.phase('PLAN', episode_id='e1', step=4)
        parsed, _ = agent._generate_json([], 10, .1, purpose='Navigate', max_json_retries=1)
        telemetry.apply_plan(agent, parsed, 4)
        runtime = HarnessRuntime(lambda name, step, **fields: telemetry.emit(name, step=step, **fields))
        runtime.start_episode('e1')
        registry = ToolRegistry()
        executor = ToolExecutor(registry, runtime)
        spec = ToolSpec('move_test', SideEffect.WORLD_MUTATING, step_consuming=True)
        executor.bind(spec, lambda: ToolOutcome('done', {}, 1), owner='test')
        self.assertEqual(executor.execute('move_test', {}, step=4), 'done')
        events = self.records()
        decision = next(e for e in events if e['event'] == 'DECISION_PARSED')
        tool = next(e for e in events if e['event'] == 'TOOL_EXECUTION_START')
        receipt = next(e for e in events if e['event'] == 'HARNESS_RECEIPT')
        self.assertEqual(tool['decision_id'], decision['decision_id'])
        self.assertEqual(receipt['call_id'], tool['call_id'])
        self.assertEqual(self.snapshot()['model']['json_retries'], 1)
        self.assertEqual(self.snapshot()['decisions'][0]['applied_plan'], parsed)

    def test_partial_append_is_consumed_once_and_missing_usage_stays_null(self):
        path = self.path / 'monitor.jsonl'
        with path.open('a') as stream:
            stream.write('{"event":"MODEL_REQUEST_START","span_id":"pending","ts":1')
        self.assertFalse(self.snapshot()['model']['recent_requests'])
        with path.open('a') as stream:
            stream.write('}\n')
        self.assertEqual(len(self.snapshot()['model']['recent_requests']), 1)
        self.assertEqual(len(self.snapshot()['model']['recent_requests']), 1)
        self.assertIsNone(self.snapshot()['model']['usage'])
        self.assertEqual(self.snapshot('failed')['model']['recent_requests'][0]['status'], 'incomplete')

    def test_heartbeat_stale_and_end_status_are_distinct(self):
        with patch('comm_harness.telemetry.time.time', return_value=10):
            telemetry.emit('HEARTBEAT')
        self.assertEqual(self.snapshot()['nodes'][0]['status'], 'stale')
        self.assertEqual(self.snapshot('completed')['nodes'][0]['status'], 'ended')

    def test_ideal_transport_fanout_expiry_duplicate_and_bytes(self):
        transport = IdealTransport('e1', emit=telemetry.emit)
        transport.send('robot-0', ['robot-1', 'ap-0'], 'map', {'x': 1}, 1)
        saved = transport.pending[0].copy()
        transport.deliver(2)
        transport.pending.append(saved)
        transport.deliver(3)
        transport.send('robot-0', ['ap-0'], 'old-map', {}, 0, ttl_ticks=0)
        transport.deliver(3)
        comm = self.snapshot()['communication']
        self.assertEqual(comm['mode'], 'ideal')
        self.assertEqual(comm['statistics']['messages_sent'], 2)
        self.assertEqual(comm['statistics']['messages_delivered'], 2)
        self.assertEqual(comm['statistics']['expired'], 1)
        self.assertEqual(comm['duplicates_recorded'], 2)
        self.assertEqual(comm['statistics']['source_bytes'], transport.stats['source_bytes'])
        self.assertIsNone(comm['wireless_metrics'])
        self.assertEqual(comm['recent_messages'][0]['delivery_events'][0]['age_ticks'], 1)

    def test_legacy_records_do_not_fabricate_latency_heartbeats_or_radio(self):
        legacy = self.path / 'legacy'
        legacy.mkdir()
        (legacy / 'events.jsonl').write_text('{"event":"observation_receipt","node_id":"robot_0"}\n')
        snap = self.store.get(legacy, {'model_usage': {'total_calls': 14}}, {'state': 'completed'})
        self.assertEqual(snap['source'], 'legacy')
        self.assertIsNone(snap['model']['requests'])
        self.assertNotIn('model_request_s', snap['durations'])
        self.assertNotIn('heartbeat_at', snap['nodes'][0])
        self.assertEqual(snap['communication']['mode'], 'not_connected')

    def test_comparison_reports_task_model_budget_differences(self):
        base = {'id': 'a', 'state': 'completed', 'result': {'metrics': {'success': 0}},
                'config': {'method': 'harness-vln', 'planner': {'model': 'v1', 'provider': 'x', 'max_calls': 1,
                           'max_total_tokens': 100}},
                'task': {'name': 'find_vase', 'seed': 1, 'platform': 'hm3d_m3'}}
        other = json.loads(json.dumps(base)); other['id'] = 'b'
        self.assertTrue(comparison([base, other])['matched_recorded_conditions'])
        other['config']['planner']['model'] = 'v2'
        self.assertEqual(comparison([base, other])['differing_conditions'], ['model'])
        other['config']['planner'].pop('max_calls')
        self.assertIn('max_calls', comparison([base, other])['missing_conditions'])

    @unittest.skipUnless(os.name == 'posix', 'Linux symlinks')
    def test_monitor_symlink_cannot_read_outside_run(self):
        telemetry.close()
        (self.path / 'monitor.jsonl').unlink()
        (self.path / 'monitor.jsonl').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            self.snapshot()


if __name__ == '__main__':
    unittest.main()
