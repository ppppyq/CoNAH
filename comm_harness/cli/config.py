"""Resolve a task instance separately from model and machine configuration."""
import json
from pathlib import Path

TASK_FIELDS = ('episode_id', 'agent_index', 'goal_index', 'expected_category', 'max_steps', 'seed')
TASK_KEYS = frozenset(('name', 'type', 'platform') + TASK_FIELDS)


def validate_task(task):
    if not isinstance(task, dict):
        raise ValueError('Task definition must be a JSON object')
    if set(task) - TASK_KEYS:
        raise ValueError('Unknown task fields: ' + ', '.join(sorted(set(task) - TASK_KEYS)))
    required = {'name', 'type', 'platform', 'episode_id', 'agent_index', 'goal_index', 'max_steps'}
    if required - set(task):
        raise ValueError('Missing task fields: ' + ', '.join(sorted(required - set(task))))
    if task['type'] != 'object_navigation' or task['platform'] != 'hm3d_m3':
        raise ValueError('This entry currently supports object_navigation on hm3d_m3 only')
    for key in ('name', 'episode_id', 'expected_category'):
        if key in task and (not isinstance(task[key], str) or not task[key].strip()):
            raise ValueError(key + ' must be a nonempty string')
    for key in ('agent_index', 'goal_index', 'max_steps', 'seed'):
        if key in task and (type(task[key]) is not int or task[key] < (1 if key == 'max_steps' else 0)):
            raise ValueError(key + ' must be a valid nonnegative integer (max_steps > 0)')


def load_single_config(config_path, task_path=None):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    reference = task_path or config.get('task_file')
    if not reference:
        # Existing saved/standalone run configurations remain usable.
        return config
    path = Path(reference)
    if not path.is_absolute():
        path = (Path.cwd() if task_path else config_path.parent) / path
    path = path.resolve()
    task = json.loads(path.read_text(encoding='utf-8'))
    validate_task(task)
    # Task fields have one owner. Do not silently mix a task file with stale
    # selections copied into a machine/model configuration.
    if any(key in config for key in TASK_FIELDS):
        raise ValueError('Move task fields out of the run configuration and into task_file')
    config.update({key: task[key] for key in TASK_FIELDS if key in task})
    config['task_file'] = str(path)
    config['task_definition'] = dict(task)
    return config


def apply_task_overrides(config, overrides):
    overrides = {key: value for key, value in overrides.items() if value is not None}
    if set(overrides) - set(TASK_FIELDS):
        raise ValueError('Unsupported task override')
    selection_changed = any(key in overrides and overrides[key] != config.get(key)
                            for key in ('episode_id', 'goal_index'))
    if selection_changed:
        # Preserve the existing CLI's ability to select another dataset goal.
        # The original profile's category assertion no longer describes it.
        config.pop('expected_category', None)
    config.update(overrides)
    if 'task_definition' in config:
        task = dict(config['task_definition'])
        for key in TASK_FIELDS:
            if key in config:
                task[key] = config[key]
            else:
                task.pop(key, None)
        if selection_changed:
            task['name'] += '-cli-selection'
        validate_task(task)
        config['task_definition'] = task
