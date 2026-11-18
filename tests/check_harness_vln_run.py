"""Audit a saved single-agent run using its real action receipts and final metrics."""
import argparse
from collections import Counter
import json
from pathlib import Path


def audit(path):
    path = Path(path)
    result = json.loads((path / 'result.json').read_text())
    config = json.loads((path / 'config.json').read_text())
    provenance = json.loads((path / 'preflight.json').read_text())
    if (path / 'VALIDITY.json').exists():
        assert json.loads((path / 'VALIDITY.json').read_text())['valid_visual_navigation_experiment'], 'invalid model capability experiment'
    assert not (path / 'failure.json').exists(), 'run has a failure artifact'
    assert not provenance['upstream_python_packages_loaded'], 'reference repository was imported'
    streams = list((path / 'rgb').glob('*/events_r0_w1.jsonl'))
    assert len(streams) == 1, 'need one event stream'
    events = [json.loads(line) for line in streams[0].read_text().splitlines() if line.strip()]
    terminal = [e for e in events if e['event'] == 'HARNESS_TERMINAL']
    assert len(terminal) == 1, 'episode must terminate exactly once'
    final = terminal[0]
    receipts = [e for e in events if e['event'] == 'HARNESS_RECEIPT']
    calls = [e for e in events if e['event'] == 'HARNESS_TOOL_CALL']
    returns = [e for e in events if e['event'] == 'HARNESS_TOOL_RESULT']
    assert Counter(e['call_id'] for e in calls) == Counter(e['call_id'] for e in returns), 'unmatched tools'
    assert len({e['receipt_id'] for e in receipts}) == len(receipts), 'duplicate receipts'
    assert all(e['step_after'] == i + 1 and e['step_before'] == i for i, e in enumerate(receipts)), 'step sequence has gaps'
    assert len(receipts) == final['step'] <= config['max_steps'], 'step budget or receipt count mismatch'
    assert result['metrics']['steps_taken'] == final['step'], 'stale final metrics'
    assert final['summary']['open_tool_calls'] == 0, 'unclosed tool call'
    assert result['model_usage']['model']['requests'] <= config['planner']['max_calls'], 'request budget exceeded'
    assert result['task']['preserved_start_pose'] and result['task']['preserved_category_reach_union']
    catalog_path = path / 'tool_catalog.json'
    if catalog_path.exists():
        catalog = json.loads(catalog_path.read_text())
        bound = {tool['name']: tool for tool in catalog['bound_tools']}
        assert set(catalog['required_platform_capabilities']) <= set(bound), 'missing platform bindings'
        assert all(bound[r['tool_name']]['owner'] == 'platform:hm3d_m3' for r in receipts), 'action bypassed platform catalog'
        assert all(bound[r['tool_name']]['allowed'] for r in receipts), 'task used a disallowed action'
    return {'run': path.name, 'audit_passed': True, 'success': result['metrics']['success'],
            'steps': final['step'], 'terminal_reason': final['reason'],
            'tool_calls': len(calls), 'receipts': len(receipts),
            'diagnostic_violations': final['summary']['violations']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir')
    args = parser.parse_args()
    print(json.dumps(audit(args.run_dir), indent=2))
