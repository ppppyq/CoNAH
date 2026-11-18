import io
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from comm_harness.planner.ollama_client import OllamaClient


class FakeEndpoint:
    def __init__(self, result):
        self.result, self.requests = result, []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return io.BytesIO(json.dumps(self.result).encode())


class OllamaAdapterTests(unittest.TestCase):
    def test_grounding_uses_normalized_qwen_boxes_and_keeps_pixel_backend_working(self):
        import numpy as np
        from tasks.harness_vln.agent import VLMReasoningAgent
        for scale, answer in [('1000', [100, 200, 600, 800]), ('pixel', [64, 96, 384, 384])]:
            with self.subTest(scale=scale), patch.dict(os.environ, {
                    'MODEL_API_KEY': 'test-key', 'MODEL_BASE_URL': 'http://127.0.0.1:11436',
                    'MODEL_NAME': 'qwen3.8:27b', 'MODEL_BBOX_SCALE': scale}), \
                    patch('tasks.harness_vln.agent.ModelClient'):
                visualizer = SimpleNamespace(height=480, _save_rgb_with_bbox=Mock())
                agent = VLMReasoningAgent(visualizer, task_type='OBJNAV', use_todo_list=False)
                agent._generate_json = Mock(return_value=({'bbox_2d': answer, 'target': 'vase'}, '{}'))
                bbox = agent.query_llm('Find the vase', [], np.zeros((480, 640, 3), dtype=np.uint8),
                    np.zeros((480, 640, 1), dtype=np.float32), 640, 480, 1, planned_action='STOP')
                self.assertEqual([bbox[k] for k in ('x1', 'y1', 'x2', 'y2')], [64, 96, 384, 384])
                self.assertEqual(bbox['target'], 'vase')
                prompt = agent._generate_json.call_args.kwargs['messages'][0]['content'][-1]['text']
                self.assertIn('normalized to 0-1000' if scale == '1000' else 'absolute image pixels', prompt)

    def test_native_payload_preserves_all_views_and_disables_thinking(self):
        endpoint = FakeEndpoint({'done': True, 'message': {'content': '{"stop":false}'},
                                 'prompt_eval_count': 700, 'eval_count': 20})
        client = OllamaClient('http://127.0.0.1:11436/v1', num_ctx=32768, num_predict=2048, opener=endpoint)
        messages = [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'View one'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,Zmlyc3Q='}},
            {'type': 'text', 'text': 'View two'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,c2Vjb25k'}}]}]
        response = client.create(model='qwen3.8:27b', messages=messages,
            response_format={'type': 'json_object'}, max_tokens=10240)
        request, timeout = endpoint.requests[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'http://127.0.0.1:11436/api/chat')
        self.assertFalse(payload['think'])
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['format'], 'json')
        self.assertEqual(payload['messages'][0]['images'], ['Zmlyc3Q=', 'c2Vjb25k'])
        self.assertEqual(payload['messages'][0]['content'], 'View one\nView two')
        self.assertEqual(payload['options']['num_ctx'], 32768)
        self.assertEqual(payload['options']['num_predict'], 2048)
        self.assertNotIn('Authorization', request.headers)
        self.assertEqual(response.usage.total_tokens, 720)
        self.assertEqual(response.choices[0].message.content, '{"stop":false}')

    def test_missing_vision_and_non_inline_images_fail_explicitly(self):
        client = OllamaClient('http://127.0.0.1:11436', opener=FakeEndpoint({'capabilities': ['completion']}))
        with self.assertRaisesRegex(ValueError, 'does not advertise vision'):
            client.verify_vision('text-only')
        with self.assertRaisesRegex(ValueError, 'inline base64'):
            client.convert_messages([{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': 'http://example.invalid/x.png'}}]}])

    def test_incomplete_response_cannot_be_a_valid_model_decision(self):
        client = OllamaClient('http://127.0.0.1:11436', opener=FakeEndpoint({'done': False}))
        with self.assertRaisesRegex(RuntimeError, 'complete non-streaming'):
            client.create(model='qwen3.8:27b', messages=[])


if __name__ == '__main__':
    unittest.main()
