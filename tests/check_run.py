"""Validate recorded integration invariants; does not call models or change results."""
import json
from pathlib import Path
import sys
from collections import Counter
import numpy as np


def check_run(path):
    path = Path(path)
    events = [json.loads(line) for line in (path / 'events.jsonl').read_text().splitlines()]
    result = json.loads((path / 'result.json').read_text())
    metadata = json.loads((path / 'environment.json').read_text())
    goals = json.loads((path / 'goals.json').read_text())
    n, ticks = metadata['num_agents'], result['ticks']
    steps = [e for e in events if e['event'] == 'joint_step']
    assert [e['tick'] for e in steps] == list(range(1, ticks + 1)), 'non-atomic environment clock'
    assert all(len(e['executed_actions']) == n for e in steps), 'wrong joint action size'
    receipts = [e for e in events if e['event'] == 'observation_receipt']
    assert len(receipts) == n * ticks, 'missing node observation receipts'
    assert len(set((e['node_id'], e['tick']) for e in receipts)) == n * ticks, 'duplicate node tick'
    assert all(('/%s/%d/current' % (e['node_id'], e['tick'])) in e['observation_id'] for e in receipts)
    forbidden = {'position', 'object_id', 'rep_point', 'reach_points', 'instance_reach_points', 'iin_viewpoint', 'routing'}
    assert all(not (forbidden & set(g)) for g in goals), 'private goal information escaped projection'
    sent = {e['message_id']: e for e in events if e['event'] == 'message_sent'}
    delivered = [e for e in events if e['event'] == 'message_delivered']
    assert len(set((e['message_id'], e['recipient']) for e in delivered)) == len(delivered), 'duplicate delivery'
    assert all(e['message_id'] in sent and e['recipient'] in sent[e['message_id']]['recipients']
               and e['delivered_tick'] >= sent[e['message_id']]['created_tick'] for e in delivered), 'invalid message delivery'
    assert result['communication']['messages_sent'] == len(sent)
    assert result['communication']['messages_delivered'] == len(delivered)
    assert result['communication']['source_bytes'] == sum(e['serialized_bytes'] for e in sent.values())
    assert result['communication']['link_bytes'] == sum(e['serialized_bytes']*len(e['recipients']) for e in sent.values())
    for trajectory, agent in zip(result['trajectories'], result['per_agent']):
        trajectory = np.asarray(trajectory)
        assert len(trajectory) == ticks + 1
        length = float(np.linalg.norm(np.diff(trajectory, axis=0), axis=1).sum())
        assert abs(length - agent['path_length']) < 1e-4, 'path accounting mismatch'
    valid_goals = {int(c['goal_id'][1:]) for c in result['claims'] if c['evaluator_valid']}
    metric_goals = {g for a in result['per_agent'] for g in a['goals_found']}
    assert valid_goals == metric_goals, 'claims confused with evaluator success'
    responses = [e for e in events if e['event'] == 'model_response']
    assert sum(e['usage'].get('total_tokens', 0) for e in responses) == result['model']['total_tokens'], 'token accounting mismatch'
    errors = [e for e in events if e['event'] in ('decision_error', 'model_error')]
    summary = {'run': str(path), 'invariants': 'passed', 'ticks': ticks, 'robots': n,
               'goals': len(goals), 'model_errors': len(errors),
               'tool_counts': dict(Counter(e['tool'] for e in events if e['event'] == 'tool_receipt')),
               'message_kinds': dict(Counter(e['kind'] for e in sent.values())),
               'verified_goals': len(valid_goals), 'model_usage': result['model']}
    return summary


if __name__ == '__main__':
    print(json.dumps(check_run(sys.argv[1]), ensure_ascii=False, indent=2))
