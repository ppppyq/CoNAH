import copy
import json
from pathlib import Path
import unittest
import numpy as np
from unittest.mock import Mock

from comm_harness.communication import IdealTransport, TaskBoard
from comm_harness.tools import Toolkit, ToolError
from platforms.hm3d_m3.bridge import public_goals
from platforms.hm3d_m3.node import HarnessNode


class CommunicationTests(unittest.TestCase):
    def test_messages_are_copied_metered_and_delivered_once(self):
        bus = IdealTransport('ep')
        payload = {'position': [1, 2, 3]}
        mid = bus.send('a', ['b', 'c'], 'candidate', payload, 2)
        payload['position'][0] = 999
        bus.pending.append(copy.deepcopy(bus.pending[0]))  # Duplicate transport delivery.
        messages = bus.deliver(3)
        self.assertEqual(len(messages['b']), 1)
        self.assertEqual(messages['b'][0]['payload']['position'][0], 1)
        messages['b'][0]['payload']['position'][0] = 800
        self.assertEqual(messages['c'][0]['payload']['position'][0], 1)
        self.assertEqual(messages['b'][0]['message_id'], mid)
        self.assertEqual(bus.stats['link_bytes'], bus.stats['source_bytes'] * 2)
        self.assertEqual(bus.deliver(4), {})

    def test_expired_messages_do_not_reach_receiver(self):
        bus = IdealTransport('ep')
        bus.send('a', ['b'], 'status', {}, 1, ttl_ticks=1)
        self.assertEqual(bus.deliver(3), {})
        self.assertEqual(bus.stats['expired'], 1)

    def test_assignment_requires_version_and_acknowledgement(self):
        bus = IdealTransport('ep')
        board = TaskBoard([{'goal_id': 'g0'}], ['a', 'b'], bus)
        bus.send('a', ['coordinator'], 'assignment_request',
                 {'goal_id': 'g0', 'operation': 'release', 'expected_version': 1}, 1)
        board.ingest(bus.deliver(2)['coordinator'], 2)
        self.assertEqual(board.goals['g0']['version'], 2)
        bus.deliver(2)
        bus.send('b', ['coordinator'], 'assignment_request',
                 {'goal_id': 'g0', 'operation': 'claim', 'expected_version': 1}, 2)
        board.ingest(bus.deliver(3)['coordinator'], 3)
        reply = bus.deliver(3)['b'][0]
        self.assertFalse(reply['payload']['accepted'])
        self.assertIsNone(board.goals['g0']['owner'])


class PolicyBoundaryTests(unittest.TestCase):
    def test_episode_public_projection_drops_oracle_fields(self):
        private = {'episode_id': 'e', 'routing': {'oracle_assignment': [1]}, 'goals_set': [
            {'modality': 'ON', 'object_category': 'vase', 'position': [1, 2, 3], 'object_id': 'secret',
             'reach_points': [[1, 2, 3]], 'iin_viewpoint': {'position': [0, 0, 0]}},
            {'modality': 'TN', 'object_category': 'mirror', 'tn_text': 'next to the door', 'object_id': 8}]}
        goals = public_goals(private, {})
        self.assertEqual(goals, [{'goal_id': 'g0', 'modality': 'ON', 'category': 'vase'},
                                {'goal_id': 'g1', 'modality': 'TN', 'category': 'mirror', 'text': 'next to the door'}])

    def test_missing_instance_reference_does_not_become_category_task(self):
        with self.assertRaises(ValueError):
            public_goals({'episode_id': 'e', 'goals_set': [
                {'modality': 'IIN', 'object_category': 'stair', 'goal_index': 0}]}, {})

    def test_invalid_tool_arguments_cannot_execute_handler(self):
        effects = []
        toolkit = Toolkit()
        toolkit.add('act', 'test', {'steps': {'type': 'integer', 'minimum': 1, 'maximum': 3}},
                    ['steps'], lambda steps: effects.append(steps))
        for arguments in [{'steps': True}, {'steps': 4}, {'steps': 1, 'override': True}, {}, '{bad']:
            self.assertFalse(toolkit.execute('act', arguments)['ok'])
        self.assertEqual(effects, [])
        self.assertTrue(toolkit.execute('act', {'steps': 2})['ok'])
        self.assertEqual(effects, [2])

    def test_tool_failure_is_an_explicit_receipt(self):
        emit = Mock()
        toolkit = Toolkit(emit)
        def broken():
            raise RuntimeError('model_network_error')
        toolkit.add('verify', 'test', {}, [], broken)
        result = toolkit.execute('verify', {})
        self.assertEqual(result, {'ok': False, 'error': 'model_network_error'})
        self.assertEqual(emit.call_args[0][0], 'tool_receipt')

    def test_stale_and_other_robot_verifications_are_rejected(self):
        node = object.__new__(HarnessNode)
        node.node_id, node.tick = 'robot_0', 10
        node.obs = {'observation_id': 'e/robot_0/10/current'}
        node.scan_remaining, node.command, node.cooldown, node.halted = -1, None, 0, False
        base = {'passed': True, 'goal_id': 'g0', 'node_id': 'robot_0', 'tick': 10,
                'observation_id': 'e/robot_0/10/current'}
        for override in [{'tick': 9}, {'node_id': 'robot_1'}, {'goal_id': 'g1'}, {'passed': False},
                         {'observation_id': 'e/robot_0/10/reference'}]:
            node.verifications = {'v': dict(base, **override)}
            with self.assertRaises(ToolError):
                node.declare_goal('g0', 'v')

    def test_modality_verifier_and_claim_use_current_local_evidence(self):
        node = object.__new__(HarnessNode)
        node.node_id, node.tick = 'robot_0', 10
        node.node_names = ['robot_0', 'robot_1']
        node.goals = [{'goal_id': 'g0', 'modality': 'TN', 'category': 'plant',
                       'text': 'hanging above a brown pot'}]
        node.obs = {'observation_id': 'e/robot_0/10/current', 'depth': np.full((480, 640), .6),
                    'rgb': np.zeros((480, 640, 3), dtype=np.uint8), 'hfov': 79, 'max_depth': 5,
                    'rotation_matrix': np.eye(3).tolist(), 'camera_position': [0, .88, 0]}
        node.config = {'claim_max_depth_m': 1.0}
        node.scan_remaining, node.command, node.cooldown, node.halted = -1, None, 0, False
        node.verifications, node.claims, node.candidates = {}, [], []
        node.graph, node.emit = Mock(), Mock()
        node.map = Mock()
        node.map.approach_candidate.return_value = None
        node.transport = IdealTransport('e')
        node.planner = Mock()
        report = {'matched': True, 'confidence': .95, 'bbox': [250, 150, 390, 350], 'reason': 'matches the full text'}
        node.planner.request.return_value = {'tool_calls': [{'function': {
            'name': 'report_verification', 'arguments': json.dumps(report)}}]}
        verification = node.verify_goal('g0')
        self.assertTrue(verification['passed'])
        node.verify_goal('g0')
        self.assertEqual(node.planner.request.call_count, 1)  # Same observation is not billed twice.
        prompt = node.planner.request.call_args[0][0][0]['content'][0]['text']
        self.assertIn('hanging above a brown pot', prompt)
        self.assertIn('NO target reference image', prompt)
        result = node.declare_goal('g0', verification['verification_id'])
        self.assertEqual(result['status'], 'CLAIMED_FOUND')
        self.assertEqual(result['evaluation'], 'not_yet_scored')
        inbox = node.transport.deliver(11)
        self.assertEqual(inbox['coordinator'][0]['kind'], 'goal_claim')
        self.assertEqual(inbox['robot_1'][0]['kind'], 'candidate')


if __name__ == '__main__':
    unittest.main()
