"""Integration contracts for the new dataset and DeepSeek adapters; no API calls."""
import gzip
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from platforms.hm3d_m3.navigation.dataset import prepare_single_episode


class DatasetContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scene.glb').touch()
        self.episode = {'episode_id': 'original', 'scene_id': 'scene.glb',
            'agents': [{'start_position': [1, 2, 3], 'start_rotation': [0, 0.6, 0, 0.8]}],
            'goals_set': [{'modality': 'ON', 'object_category': 'radiator',
                'object_id': 'object_7', 'position': [4, 2, 3],
                'reach_points': [[4, 2, 3], [5, 2, 3]]}]}
        self.config = {'episodes': str(self.root / 'episodes.json'), 'scenes': str(self.root),
                       'episode_id': 'original', 'agent_index': 0, 'goal_index': 0}

    def prepare(self):
        Path(self.config['episodes']).write_text(json.dumps({'episodes': [self.episode]}))
        return prepare_single_episode(self.config, self.root)

    def test_original_pose_and_all_category_reach_points_survive(self):
        path, metadata, scene = self.prepare()
        with gzip.open(path, 'rt') as stream:
            data = json.load(stream)
        converted = data['episodes'][0]
        for key in ('start_position', 'start_rotation'):
            self.assertEqual(converted[key], self.episode['agents'][0][key])
        goal = data['goals_by_category']['scene.glb_radiator'][0]
        self.assertEqual([v['agent_state']['position'] for v in goal['view_points']],
                         self.episode['goals_set'][0]['reach_points'])
        self.assertEqual(converted['goals'], [])
        self.assertEqual(scene, 'scene.glb')
        self.assertNotIn('position', metadata)
        self.assertEqual(metadata['source_episode_id'], 'original')

    def test_instance_and_text_targets_are_not_silently_weakened(self):
        for modality in ('TN', 'IIN'):
            with self.subTest(modality=modality):
                self.episode['goals_set'][0]['modality'] = modality
                with self.assertRaisesRegex(ValueError, 'ON only'):
                    self.prepare()

    def test_task_category_must_match_the_selected_dataset_goal(self):
        self.config['expected_category'] = 'vase'
        with self.assertRaisesRegex(ValueError, 'selected dataset goal'):
            self.prepare()
        self.assertFalse((self.root / 'single_episode.json.gz').exists())
        self.config['expected_category'] = 'radiator'
        _, metadata, _ = self.prepare()
        self.assertEqual(metadata['category'], 'radiator')

    def test_empty_reach_points_and_invalid_agent_fail(self):
        self.episode['goals_set'][0]['reach_points'] = []
        with self.assertRaisesRegex(ValueError, 'reach points'):
            self.prepare()
        self.config['agent_index'] = -1
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.prepare()


class PanoramaTerminalTests(unittest.TestCase):
    def test_terminal_panorama_preserves_final_environment_metrics(self):
        from tasks.harness_vln.evaluator import ZeroShotVlnEvaluatorMP
        evaluator = ZeroShotVlnEvaluatorMP.__new__(ZeroShotVlnEvaluatorMP)
        evaluator.panorama_step_cost = 12
        evaluator.harness_enabled = False
        info = {'steps_taken': 300, 'distance_to_goal': 4.2, 'success': 0.0}
        evaluator._harness_step = lambda *a, **kw: [({'final': True}, 0.0, True, info)]
        result = evaluator.get_panorama({}, 299)
        self.assertTrue(result['episode_finished'])
        self.assertEqual(result['infos'], [info])
        self.assertEqual(evaluator._last_info, info)
        self.assertEqual(evaluator._last_panorama_steps_taken, 1)


class ModelContractTests(unittest.TestCase):
    def test_text_only_deepseek_is_rejected_before_backend_initialization(self):
        from comm_harness.planner.model_client import ModelClient
        with patch('comm_harness.planner.model_client.OpenAI') as backend:
            with self.assertRaisesRegex(ValueError, 'does not support vision'):
                ModelClient('test-key', 'https://api.deepseek.com', 'deepseek-v4-pro')
        backend.assert_not_called()

    def setUp(self):
        from comm_harness.planner.model_client import ModelClient, ModelBudgetExceeded
        self.budget_error = ModelBudgetExceeded
        self.payloads = []
        def create(**payload):
            self.payloads.append(payload)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"stop": false}'))],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=5, total_tokens=105))
        self.client = ModelClient('test-key', 'https://example.invalid', 'deepseek-flash',
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))

    def test_images_and_thinking_control_reach_backend(self):
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Return JSON'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,dGVzdA=='}}]}]
        self.client.generate(messages, reasoning_effort='high')
        self.assertEqual(self.payloads[0]['messages'], messages)
        self.assertEqual(self.payloads[0]['extra_body']['thinking'], {'type': 'disabled'})
        self.assertNotIn('reasoning_effort', self.payloads[0])
        self.assertEqual(self.payloads[0]['response_format'], {'type': 'json_object'})
        self.assertEqual(self.client.get_usage_stats()['total_tokens'], 105)

    def test_budget_escapes_broad_agent_fallback_without_second_request(self):
        with patch.dict(os.environ, {'COMM_HVL_MAX_CALLS': '1', 'COMM_HVL_MAX_TOKENS': '1000'}):
            self.client.generate([{'role': 'user', 'content': 'JSON please'}])
            with self.assertRaises(self.budget_error):
                try:
                    self.client.generate([])
                except Exception:
                    self.fail('Budget exhaustion must not become an agent fallback')
        self.assertEqual(len(self.payloads), 1)

    def test_token_threshold_prevents_next_request(self):
        self.client.stats['total_tokens'] = 105
        with patch.dict(os.environ, {'COMM_HVL_MAX_TOKENS': '105'}):
            with self.assertRaises(self.budget_error):
                self.client.generate([])
        self.assertEqual(self.payloads, [])


if __name__ == '__main__':
    unittest.main()
