"""Interface regressions: records, path boundaries, shared CLI, owned processes."""
import json
import os
from pathlib import Path
import sys
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from comm_harness.cli.tui import dispatch
from comm_harness.dashboard.state import DashboardState, redact


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'examples').mkdir()
        self.run = self.root/'runs'/'history'
        (self.run/'rgb'/'episode').mkdir(parents=True)
        self.task = {'name':'find_vase', 'type':'object_navigation', 'platform':'hm3d_m3',
            'episode_id':'episode', 'agent_index':0, 'goal_index':1, 'max_steps':10,
            'expected_category':'vase'}
        self.write('examples/task.json', self.task)
        self.write('examples/profile.json', {'method':'harness-vln', 'task_file':'task.json',
            'planner':{'provider':'ollama', 'model':'vision', 'base_url':'http://localhost:11434'},
            **{field:str(self.root) for field in ('episodes','scenes','assets','asset_manifest')}})
        self.state = DashboardState(self.root, worker_python=sys.executable)
        self.addCleanup(self.state.close)

    def write(self, path, data):
        (self.root/path).write_text(json.dumps(data), encoding='utf-8')

    def test_catalog_and_cli_keep_task_identity(self):
        self.assertEqual(dispatch(self.state, '/tasks')[0]['goal_index'], 1)
        checked = dispatch(self.state, '/check task.json profile.json')
        self.assertEqual(checked['task'], self.task)
        self.assertEqual(json.loads((self.root/'examples/task.json').read_text()), self.task)
        self.assertFalse(any((self.root/'runs').glob('ui-*')))

    def test_finished_run_does_not_imply_navigation_success(self):
        self.write('runs/history/result.json', {'completed':True, 'metrics':{'success':0, 'steps_taken':112}})
        data = self.state.detail('history')
        self.assertEqual(data['state'], 'completed')
        self.assertEqual(data['success'], 0)
        self.assertEqual(data['steps'], 112)

    def test_paths_and_task_names_cannot_escape_roots(self):
        for run_id in ('../examples', '/etc', '.control', 'a/b'):
            with self.subTest(run_id=run_id), self.assertRaises(ValueError):
                self.state.detail(run_id)
        with self.assertRaises(ValueError):
            self.state.check('../examples/task.json', 'profile.json')
        with self.assertRaises(ValueError):
            self.state.image_path('history', '../../examples/task.json')

    @unittest.skipUnless(os.name == 'posix', 'Linux filesystem links')
    def test_image_symlink_cannot_expose_other_files(self):
        (self.run/'rgb'/'escape.png').symlink_to(self.root/'examples/task.json')
        with self.assertRaises(ValueError):
            self.state.image_path('history', 'rgb/escape.png')

    def test_live_partial_events_and_credentials_are_handled(self):
        events = self.run/'rgb/episode/events_r0_w1.jsonl'
        events.write_text('{"event":"HARNESS_TOOL_CALL","step":1}\n{"event":', encoding='utf-8')
        self.write('runs/history/config.json', {'planner':{'api_key':'private-value'}})
        (self.run/'console.log').write_text('key sk-1234567890abcdef password=private-value', encoding='utf-8')
        data = self.state.detail('history')
        self.assertEqual(len(data['events']), 1)
        self.assertNotIn('private-value', json.dumps(data))
        self.assertNotIn('sk-1234567890', json.dumps(data))
        self.assertIsNone(redact(float('nan')))

    def test_unowned_run_cannot_be_stopped(self):
        with self.assertRaises(RuntimeError):
            self.state.stop('history')

    def test_ui_does_not_silently_ignore_run_overrides(self):
        from comm_harness.cli import main
        with patch('sys.stderr'), self.assertRaises(SystemExit) as raised:
            main(['--tui', '--task', 'examples/task.json'])
        self.assertEqual(raised.exception.code, 2)

    @unittest.skipUnless(os.name == 'posix', 'Linux worker lifecycle')
    def test_owned_run_is_exclusive_cancellable_and_releases_lease(self):
        package = self.root/'comm_harness'
        package.mkdir()
        (package/'__init__.py').write_text('')
        (package/'__main__.py').write_text('''import json,sys,time
from pathlib import Path
p=Path(sys.argv[sys.argv.index('--run-dir')+1]); p.mkdir()
(p/'ready').write_text('ready')
try:
    time.sleep(30)
except KeyboardInterrupt:
    (p/'failure.json').write_text(json.dumps({'message':'interrupted fixture'}))
    sys.exit(130)
''')
        other = DashboardState(self.root, worker_python=sys.executable)
        self.addCleanup(other.close)
        first = self.state.start('task.json', 'profile.json')
        deadline = time.monotonic()+5
        while not (self.root/'runs'/first['id']/'ready').exists() and time.monotonic()<deadline:
            time.sleep(.03)
        self.assertTrue((self.root/'runs'/first['id']/'ready').exists())
        with self.assertRaises(RuntimeError):
            other.start('task.json', 'profile.json')
        self.state.stop(first['id'])
        self.state.stop(first['id'])  # An already requested stop is idempotent.
        self.state.close()
        self.assertEqual(self.state.detail(first['id'])['state'], 'cancelled')
        second = other.start('task.json', 'profile.json')
        self.assertNotEqual(first['id'], second['id'])
        other.close()

    @unittest.skipUnless(os.name == 'posix', 'Linux worker signals')
    def test_interrupt_escalates_only_the_owned_habitat_worker(self):
        from tasks.harness_vln.launcher import launch_single
        process = Mock(pid=12345)
        process.poll.return_value = None
        process.wait.side_effect = [KeyboardInterrupt(), subprocess.TimeoutExpired('worker', 10), 0]
        config = {'assets':str(self.root), 'planner':{'model':'vision', 'base_url':'http://localhost',
                  'max_calls':1, 'max_total_tokens':10}, 'goal_index':1}
        with patch('subprocess.Popen', return_value=process), patch('subprocess.check_output', side_effect=OSError), patch('os.killpg') as kill:
            with self.assertRaises(KeyboardInterrupt):
                launch_single(config, self.root/'interrupted', 'test-key')
        self.assertEqual([call.args for call in kill.call_args_list],
                         [(12345, signal.SIGTERM), (12345, signal.SIGKILL)])


    def test_http_validates_selection_and_rejects_cross_origin_writes(self):
        try:
            from fastapi.testclient import TestClient
            from comm_harness.dashboard.server import create_app
        except ImportError:
            self.skipTest('Install UI dependencies and httpx for HTTP checks')
        with TestClient(create_app(self.state)) as client:
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(client.get('/api/catalog').json()['tasks'][0]['id'], 'task.json')
            checked = client.post('/api/check', json={'task':'task.json','profile':'profile.json'})
            self.assertEqual(checked.status_code, 200)
            self.assertEqual(checked.json()['task'], self.task)
            self.assertEqual(client.post('/api/check', json={'task':'bad','profile':'profile.json'}).status_code, 400)
            self.assertEqual(client.post('/api/runs', headers={'origin':'https://unrelated.invalid'},
                json={'task':'task.json','profile':'profile.json'}).status_code, 403)
            self.assertEqual(client.post('/api/check', json={'task':'task.json','profile':'profile.json','command':'other'}).status_code, 422)
            self.assertEqual(client.get('/api/runs/history/image?path=../../examples/task.json').status_code, 400)
            self.assertEqual(client.post('/api/runs/history/stop').status_code, 409)
            self.assertEqual(client.get('/api/runs/history/monitor').json()['source'], 'unavailable')
            self.assertEqual(client.get('/api/compare?ids=history,history').status_code, 400)
            (self.root/'runs'/'second').mkdir()
            compared = client.get('/api/compare?ids=history,second').json()
            self.assertEqual(len(compared['rows']), 2)
            self.assertFalse(compared['matched_recorded_conditions'])
            with patch('socket.create_connection', side_effect=OSError('offline')), patch('subprocess.run', side_effect=FileNotFoundError):
                probe = client.post('/api/probe', json={'task':'task.json','profile':'profile.json'})
            self.assertEqual(probe.json()['model_endpoint']['status'], 'unreachable')
            self.assertIsNone(probe.json()['gpu'])
            self.assertEqual(client.post('/api/probe', json={'task':'task.json','profile':'unknown'}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
