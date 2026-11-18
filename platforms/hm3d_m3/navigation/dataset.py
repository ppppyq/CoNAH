"""Project an original M3 agent/category goal into Habitat ObjectNav format."""
import gzip
import json
from pathlib import Path


def prepare_single_episode(config, output_dir):
    data = json.loads(Path(config['episodes']).read_text())
    episodes = data['episodes'] if isinstance(data, dict) else data
    selected = [e for e in episodes if str(e['episode_id']) == str(config['episode_id'])]
    if len(selected) != 1:
        raise ValueError('episode_id must identify exactly one original episode')
    original = selected[0]
    ai, gi = int(config['agent_index']), int(config['goal_index'])
    if not (0 <= ai < len(original['agents']) and 0 <= gi < len(original['goals_set'])):
        raise ValueError('agent_index or goal_index is outside the original episode')
    goal, start = original['goals_set'][gi], original['agents'][ai]
    if goal.get('modality') != 'ON':
        raise ValueError('This single-agent ObjectNav adapter accepts ON only; TN/IIN must not silently become category tasks')
    if not goal.get('reach_points') or not start.get('start_rotation'):
        raise ValueError('Missing original reach points or start rotation')
    scene = original['scene_id'].split('hm3d/', 1)[-1]
    if not (Path(config['scenes']) / scene).is_file():
        raise FileNotFoundError('Original scene is unavailable')
    category = goal['object_category']
    expected = config.get('expected_category')
    if expected is not None and expected.strip().casefold() != str(category).strip().casefold():
        raise ValueError('Task expects category %r, but selected dataset goal is %r; '
                         'update episode_id/goal_index together with expected_category' % (expected, category))
    episode = {'episode_id': '0', 'scene_id': scene,
               'start_position': list(start['start_position']), 'start_rotation': list(start['start_rotation']),
               'object_category': category, 'goals': [],
               'info': {'source_episode': original['episode_id'], 'source_agent_index': ai, 'source_goal_index': gi}}
    object_goal = {'object_id': str(goal['object_id']).rsplit('_', 1)[-1],
                   'object_category': category, 'position': goal['position'],
                   'view_points': [{'agent_state': {'position': p, 'rotation': [0, 0, 0, 1]}, 'iou': 0.0}
                                   for p in goal['reach_points']]}
    projected = {'episodes': [episode], 'category_to_task_category_id': {category: 0},
                 'category_to_scene_annotation_category_id': {category: 0},
                 'goals_by_category': {Path(scene).name + '_' + category: [object_goal]}}
    path = Path(output_dir) / 'single_episode.json.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as stream:
        json.dump(projected, stream)
    public = {'source_episode_id': original['episode_id'], 'source_agent_index': ai,
              'source_goal_index': gi, 'category': category, 'modality': 'ON',
              'converted_episode_id': '0', 'preserved_start_pose': True,
              'preserved_category_reach_union': True, 'viewpoint_count': len(goal['reach_points']),
              'task_scope': 'one original agent and one category goal; not a complete M3 episode'}
    return path, public, scene
