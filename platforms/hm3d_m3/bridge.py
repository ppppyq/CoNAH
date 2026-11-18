"""Isolated HM3D-M3 simulator and private evaluator; no policy imports this worker's state."""
import base64
import hashlib
import io
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import traceback


def public_goals(episode, image_lookup):
    from PIL import Image
    result = []
    for index, goal in enumerate(episode['goals_set']):
        item = {'goal_id': 'g%d' % index, 'modality': goal['modality'],
                'category': goal['object_category']}
        if item['modality'] == 'TN':
            if not goal.get('tn_text'):
                raise ValueError('missing TN text: ' + item['goal_id'])
            item['text'] = goal['tn_text']
        elif item['modality'] == 'IIN':
            path = image_lookup.get((str(episode['episode_id']), int(goal.get('goal_index', index))))
            if path is None:
                raise ValueError('missing IIN reference: ' + item['goal_id'])
            with Image.open(path) as image:
                image.load()
                buffer = io.BytesIO()
                image.convert('RGB').save(buffer, format='JPEG', quality=90)
            item['reference_image'] = base64.b64encode(buffer.getvalue()).decode('ascii')
        result.append(item)
    return result


def _worker(connection, config, run_dir):
    sim = None
    try:
        # Native Habitat logs can be verbose; keep them in this run's own file.
        log = open(str(Path(run_dir) / 'environment.log'), 'a', buffering=1)
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        root = Path(config['m3_root'])
        os.chdir(str(root))
        sys.path.insert(0, str(root))
        import numpy as np
        import quaternion
        import habitat_sim
        import habitat_extensions
        import habitat_extensions.multi_agent
        from habitat.sims import make_sim
        from habitat_extensions.config.default import get_extended_config
        from benchmark.metrics import team_metrics, aggregate

        dataset_path = Path(config['episodes'])
        data = json.loads(dataset_path.read_text())
        episodes = data['episodes'] if isinstance(data, dict) else data
        matches = [e for e in episodes if str(e['episode_id']) == str(config['episode_id'])]
        if len(matches) != 1:
            raise ValueError('episode must resolve exactly once')
        episode = matches[0]
        n = int(episode['num_agents'])
        if len(episode['agents']) != n:
            raise ValueError('episode start count mismatch')
        image_dir = Path(config['iin_images'])
        manifest = json.loads((image_dir / 'manifest.json').read_text())
        lookup = {}
        for item in manifest.get('items', []):
            raw = Path(item['image'])
            candidates = [raw, root / raw, image_dir / raw.name]
            found = next((p for p in candidates if p.is_file()), None)
            if found is not None:
                lookup[(str(item['episode_id']), int(item['goal_index']))] = found
        goals = public_goals(episode, lookup)
        scene_relative = episode['scene_id'].split('hm3d/', 1)[-1]
        scene = Path(config['scenes']) / scene_relative
        if not scene.is_file():
            raise ValueError('scene mesh is missing')
        cfg = get_extended_config(str(root / 'habitat_extensions/config/multi_objectnav_v2.yaml'))
        cfg = cfg.SIMULATOR.clone()
        cfg.defrost()
        cfg.SCENE = str(scene)
        cfg.NUM_AGENTS = n
        cfg.AGENTS = ['AGENT_%d' % i for i in range(n)]
        for i in range(n):
            setattr(cfg, 'AGENT_%d' % i, cfg.AGENT_0.clone())
        if 'SCENE_DATASET' in cfg:
            cfg.SCENE_DATASET = str(Path(config['scenes']) / 'hm3d_annotated_basis.scene_dataset_config.json')
        cfg.freeze()
        sim = make_sim(id_sim=cfg.TYPE, config=cfg)
        navmesh_source = 'loaded'
        if not sim.pathfinder.is_loaded:
            settings = habitat_sim.NavMeshSettings()
            settings.set_defaults()
            settings.agent_height = cfg.AGENT_0.HEIGHT
            settings.agent_radius = cfg.AGENT_0.RADIUS
            if not sim.recompute_navmesh(sim.pathfinder, settings):
                raise RuntimeError('cannot generate navigable mesh')
            navmesh_source = 'recomputed_from_config'
        sim.reset()
        rng = np.random.RandomState(config.get('seed', 1123))
        for i, start in enumerate(episode['agents']):
            rotation = start.get('start_rotation')
            if rotation is None:
                yaw = float(rng.uniform(0, 2 * np.pi))
                rotation = [0, float(np.sin(yaw / 2)), 0, float(np.cos(yaw / 2))]
            sim.set_agent_state([float(v) for v in start['start_position']],
                                [float(v) for v in rotation], agent_id=i)
        tick, history = 0, {}
        paths = [0.0] * n
        previous = [np.asarray(sim.get_agent_state(i).position).copy() for i in range(n)]
        traces = [[p.tolist()] for p in previous]
        normalized = bool(getattr(cfg.DEPTH_SENSOR, 'NORMALIZE_DEPTH', True))
        finished = False

        def observations():
            raw = sim._per_agent_obs()
            out = []
            for i in range(n):
                state = sim.get_agent_state(i)
                sensor = state.sensor_states.get('depth') or state.sensor_states.get('rgb')
                rotation = sensor.rotation if sensor else state.rotation
                matrix = quaternion.as_rotation_matrix(rotation)
                depth = np.asarray(raw[i]['depth'], dtype=np.float32).squeeze().copy()
                if normalized:
                    depth = cfg.DEPTH_SENSOR.MIN_DEPTH + depth * (cfg.DEPTH_SENSOR.MAX_DEPTH - cfg.DEPTH_SENSOR.MIN_DEPTH)
                pos = np.asarray(state.position, dtype=float)
                history[(tick, i)] = pos.copy()
                out.append({'node_id': 'robot_%d' % i, 'tick': tick,
                    'observation_id': '%s/robot_%d/%d/current' % (episode['episode_id'], i, tick),
                    'rgb': np.asarray(raw[i]['rgb'])[:, :, :3].copy(), 'depth': depth,
                    'position': pos.tolist(), 'rotation_matrix': matrix.tolist(),
                    'camera_position': (np.asarray(sensor.position).tolist() if sensor else
                        (pos + np.array([0, cfg.DEPTH_SENSOR.POSITION[1], 0])).tolist()),
                    'hfov': float(cfg.RGB_SENSOR.HFOV), 'max_depth': float(cfg.DEPTH_SENSOR.MAX_DEPTH),
                    'path_length': paths[i]})
            return out

        metadata = {'episode_id': episode['episode_id'], 'num_agents': n, 'num_goals': len(goals),
            'dataset_sha256': hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            'sensor': {'width': cfg.RGB_SENSOR.WIDTH, 'height': cfg.RGB_SENSOR.HEIGHT,
                       'hfov': cfg.RGB_SENSOR.HFOV, 'camera_height': cfg.DEPTH_SENSOR.POSITION[1],
                       'depth_was_normalized': normalized},
            'embodiment': {'height': cfg.AGENT_0.HEIGHT, 'radius': cfg.AGENT_0.RADIUS,
                          'forward_step': cfg.FORWARD_STEP_SIZE, 'turn_degrees': cfg.TURN_ANGLE},
            'navmesh_source': navmesh_source, 'path_budget_m': episode.get('budget_geo'),
            'evaluation_protocol': 'policy_claim_then_private_reach_check_v0'}
        connection.send({'ok': True, 'metadata': metadata, 'goals': goals, 'observations': observations()})
        while True:
            command = connection.recv()
            operation = command['operation']
            if operation == 'close':
                break
            if operation == 'step':
                if finished:
                    raise ValueError('cannot step after evaluation')
                actions = command['actions']
                if len(actions) != n or any(type(a) is not int or a not in (0, 1, 2, 3) for a in actions):
                    raise ValueError('invalid joint action')
                budget = episode.get('budget_geo') if config.get('path_budget', True) else None
                actions = [0 if budget and paths[i] >= budget else a for i, a in enumerate(actions)]
                sim.step(actions)
                tick += 1
                for i in range(n):
                    pos = np.asarray(sim.get_agent_state(i).position)
                    paths[i] += float(np.linalg.norm(pos - previous[i]))
                    previous[i] = pos.copy()
                    traces[i].append(pos.tolist())
                connection.send({'ok': True, 'observations': observations(), 'executed_actions': actions})
            elif operation == 'finish':
                if finished:
                    raise ValueError('evaluation already finished')
                finished = True
                per_agent = [{'path_length': p, 'goals_found': []} for p in paths]
                checked, found = [], set()
                for claim in command.get('claims', []):
                    record = dict(claim)
                    try:
                        i = int(claim['node_id'].split('_')[-1])
                        j = int(claim['goal_id'][1:])
                        claim_tick = int(claim['tick'])
                        expected = '%s/robot_%d/%d/current' % (episode['episode_id'], i, claim_tick)
                        if claim.get('observation_id') != expected or not (0 <= j < len(goals)):
                            raise ValueError('claim evidence mismatch')
                        pos = history[(claim_tick, i)]
                        goal = episode['goals_set'][j]
                        points = goal['reach_points'] if goal['modality'] == 'ON' else goal.get('instance_reach_points', goal['reach_points'])
                        distances = [float(sim.geodesic_distance(pos, np.asarray(p, dtype=np.float32))) for p in points]
                        distance = min(distances) if distances else float('inf')
                        valid = bool(np.isfinite(distance) and distance <= float(episode.get('success_distance', 1.0)))
                        record.update(evaluator_valid=valid, evaluator_distance_m=distance if np.isfinite(distance) else None)
                        if valid and j not in found:
                            per_agent[i]['goals_found'].append(j)
                            found.add(j)
                    except (KeyError, ValueError, IndexError, TypeError):
                        record.update(evaluator_valid=False, evaluator_error='invalid_claim')
                    checked.append(record)
                result = {'per_agent': per_agent, 'false_positives': sum(not c['evaluator_valid'] for c in checked),
                          'comm_volume': command.get('communication', {}).get('link_bytes', 0)}
                metric = team_metrics(episode, result)
                connection.send({'ok': True, 'metrics': metric, 'aggregate': aggregate([metric]),
                                 'claims': checked, 'per_agent': per_agent, 'trajectories': traces})
            else:
                raise ValueError('unknown environment operation')
    except EOFError:
        pass
    except BaseException:
        try:
            connection.send({'ok': False, 'error': traceback.format_exc()[-6000:]})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if sim is not None:
            sim.close()
        connection.close()


class EnvironmentBridge:
    def __init__(self, config, run_dir):
        context = mp.get_context('spawn')
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_worker, args=(child, config, str(run_dir)))
        self.process.start()
        child.close()
        try:
            self.initial = self._receive(180)
        except BaseException:
            self.close()
            raise

    def _receive(self, timeout=60):
        if not self.connection.poll(timeout):
            raise RuntimeError('environment response timeout')
        response = self.connection.recv()
        if not response.get('ok'):
            raise RuntimeError(response.get('error', 'environment failed'))
        return response

    def step_joint(self, actions):
        self.connection.send({'operation': 'step', 'actions': actions})
        return self._receive()

    def finish(self, claims, communication):
        self.connection.send({'operation': 'finish', 'claims': claims, 'communication': communication})
        return self._receive()

    def close(self):
        try:
            if self.process.is_alive():
                self.connection.send({'operation': 'close'})
                self.process.join(5)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(5)
        except (BrokenPipeError, EOFError, OSError):
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(5)
        finally:
            self.connection.close()
