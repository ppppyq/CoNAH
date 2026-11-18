"""Task selection must remain separate from model configuration and dataset truth."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from comm_harness.cli.config import load_single_config, apply_task_overrides, validate_task


class TaskConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = {'name': 'find_vase', 'type': 'object_navigation', 'platform': 'hm3d_m3',
                     'episode_id': 'scene_1', 'agent_index': 0, 'goal_index': 1,
                     'expected_category': 'vase', 'max_steps': 300, 'seed': 1123}
        self.task_path = self.root / 'find_vase.json'
        self.task_path.write_text(json.dumps(self.task), encoding='utf-8')
        self.config_path = self.root / 'qwen.json'
        self.write_config({'task_file': self.task_path.name,
                           'planner': {'provider': 'ollama', 'model': 'qwen3.8:27b'}})

    def write_config(self, config):
        self.config_path.write_text(json.dumps(config), encoding='utf-8')

    def test_relative_task_path_is_resolved_beside_run_config(self):
        config = load_single_config(self.config_path)
        self.assertEqual(config['task_definition'], self.task)
        self.assertEqual(config['task_file'], str(self.task_path.resolve()))
        self.assertEqual(config['goal_index'], 1)
        self.assertEqual(config['planner']['model'], 'qwen3.8:27b')

    def test_one_task_definition_works_with_both_model_profiles(self):
        configs = Path(__file__).resolve().parents[1] / 'tasks' / 'harness_vln' / 'configs'
        task = str(configs / 'find_vase.json')
        qwen = load_single_config(configs / 'harness_vln_ollama.json', task)
        deepseek = load_single_config(configs / 'harness_vln_single.json', task)
        self.assertEqual(qwen['task_definition'], deepseek['task_definition'])
        self.assertNotEqual(qwen['planner']['model'], deepseek['planner']['model'])

    def test_overrides_are_effective_and_do_not_modify_source_task(self):
        config = load_single_config(self.config_path)
        apply_task_overrides(config, {'max_steps': 5, 'goal_index': 1})
        self.assertEqual(config['task_definition']['max_steps'], 5)
        self.assertEqual(config['expected_category'], 'vase')
        apply_task_overrides(config, {'goal_index': 3})
        self.assertEqual(config['task_definition']['goal_index'], 3)
        self.assertNotIn('expected_category', config)
        self.assertNotIn('expected_category', config['task_definition'])
        self.assertEqual(json.loads(self.task_path.read_text()), self.task)

    def test_ambiguous_configuration_is_rejected(self):
        self.write_config({'task_file': self.task_path.name, 'goal_index': 3})
        with self.assertRaisesRegex(ValueError, 'Move task fields'):
            load_single_config(self.config_path)

    def test_unsupported_or_malformed_task_is_not_silently_accepted(self):
        for override in ({'type': 'radio_survey'}, {'platform': 'uav'}, {'max_steps': 0},
                         {'goal_index': -1}, {'agent_index': True}, {'expected_category': ''},
                         {'epiosde_id': 'typo'}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                validate_task(dict(self.task, **override))

    def test_cli_passes_resolved_task_and_budget_to_launcher(self):
        from comm_harness.cli import main
        argv = ['comm_harness', '--method', 'harness-vln', '--config', str(self.config_path),
                '--task', str(self.task_path), '--max-steps', '5', '--run-dir', str(self.root/'run')]
        with patch('sys.argv', argv), patch('tasks.harness_vln.launcher.launch_single') as launch:
            main()
        config, _, key = launch.call_args[0]
        self.assertEqual(config['max_steps'], 5)
        self.assertEqual(config['task_definition']['max_steps'], 5)
        self.assertEqual(config['expected_category'], 'vase')
        self.assertEqual(key, 'ollama')


if __name__ == '__main__':
    unittest.main()
