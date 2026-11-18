"""Real Habitat contract probe. Synthetic claims here are tests, never benchmark scores."""
import json
from pathlib import Path
import tempfile
import numpy as np

from platforms.hm3d_m3.bridge import EnvironmentBridge


def main():
    config = json.loads(Path('tasks/multi_target_search/configs/hm3d_m3_smoke.json').read_text())
    with tempfile.TemporaryDirectory(prefix='comm-harness-env-probe-') as directory:
        bridge = EnvironmentBridge(config, directory)
        try:
            initial = bridge.initial['observations']
            result = bridge.step_joint([0] * len(initial))
            for old, new in zip(initial, result['observations']):
                assert new['tick'] == 1
                assert np.allclose(old['position'], new['position']), 'HOLD moved the robot'
            valid_evidence = {'goal_id': 'g0', 'node_id': 'robot_0', 'tick': 0,
                              'observation_id': initial[0]['observation_id'],
                              'verification_id': 'synthetic_test_only'}
            invalid_evidence = dict(valid_evidence, node_id='robot_1')
            score = bridge.finish([valid_evidence, invalid_evidence], {})
            assert isinstance(score['claims'][0]['evaluator_valid'], bool)
            assert score['claims'][1]['evaluator_valid'] is False
            assert score['claims'][1]['evaluator_error'] == 'invalid_claim'
            try:
                bridge.step_joint([0] * len(initial))
            except RuntimeError as exc:
                assert 'cannot step after evaluation' in str(exc)
            else:
                raise AssertionError('environment continued after private evaluation')
            print(json.dumps({'environment_probe': 'passed', 'robots': len(initial),
                'checks': ['HOLD preserves pose while advancing logical tick',
                           'nonempty claim reaches geodesic scorer',
                           'cross-node observation forgery rejected',
                           'execution cannot resume after scoring'],
                'not_a_navigation_result': True}))
        finally:
            bridge.close()


if __name__ == '__main__':
    main()
