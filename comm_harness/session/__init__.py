"""Session orchestration layer: single-agent ``AgentSession``, multi-robot ``TeamSession``, and ``EnvState``."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time

from comm_harness.communication import IdealTransport, TaskBoard
from comm_harness.planner import Budget, DeepSeekPlanner, ScriptedPlanner, ModelError
from platforms import get_platform
from comm_harness.session.agent_session import AgentSession
from comm_harness.session.base import EnvState, StepRecord


def _safe(value, key=''):
    if key in ('reference_image', 'image_b64') and isinstance(value, str):
        return {'omitted_from_log': True, 'base64_bytes': len(value),
                'sha256': hashlib.sha256(value.encode()).hexdigest()}
    if isinstance(value, dict):
        return {k: _safe(v, k) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe(v) for v in value]
    return value


class EventLog:
    def __init__(self, path):
        self.file = Path(path).open('w', encoding='utf-8')
        self.lock = threading.Lock()
        self.sequence = 0

    def emit(self, event, **fields):
        with self.lock:
            self.sequence += 1
            item = {'event': event, 'event_sequence': self.sequence, 'ts': time.time(), **_safe(fields)}
            self.file.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + '\n')
            self.file.flush()

    def close(self):
        self.file.close()


class TeamSession:
    def __init__(self, config, run_dir, key=''):
        self.config, self.run_dir, self.key = config, Path(run_dir), key
        self.log = EventLog(self.run_dir / 'events.jsonl')

    def save(self, name, data):
        (self.run_dir / name).write_text(json.dumps(_safe(data), ensure_ascii=False,
                                        indent=2, allow_nan=False), encoding='utf-8')

    def run(self):
        spec = get_platform(self.config['platform'])
        environment = None
        start = time.monotonic()
        budget = Budget(self.config['planner']['max_calls'], self.config['planner']['max_total_tokens'])
        try:
            self.save('config.json', self.config)
            environment = spec.environment_factory(self.config, self.run_dir)
            initial = environment.initial
            metadata, goals, all_observations = initial['metadata'], initial['goals'], initial['observations']
            num_robots = self.config.get('num_robots')
            observations = all_observations[:int(num_robots)] if num_robots else all_observations
            total_sim_agents = len(all_observations)
            self.save('environment.json', metadata)
            self.save('goals.json', goals)
            names = [o['node_id'] for o in observations]
            transport = IdealTransport(metadata['episode_id'], self.log.emit)
            board = TaskBoard(goals, names, transport)
            if self.config['planner']['mode'] in ('deepseek', 'api'):
                planner = DeepSeekPlanner(self.config['planner'], self.key, budget, self.log.emit)
            else:
                planner = ScriptedPlanner()
            nodes = [spec.node_factory(name, goals, self.config, transport, planner,
                                      self.log.emit, self.run_dir, names) for name in names]
            board.broadcast(0)
            for node, obs in zip(nodes, observations):
                node.update(obs)
            print(json.dumps({'status': 'environment_ready', 'robots': len(nodes), 'goals': len(goals),
                              'episode_id': metadata['episode_id']}), flush=True)
            stop_reason = 'max_ticks'
            tick = 0
            with ThreadPoolExecutor(max_workers=min(len(nodes), self.config.get('model_workers', 3))) as pool:
                while tick < self.config['max_ticks']:
                    inboxes = transport.deliver(tick)
                    board.ingest(inboxes.pop('coordinator', []), tick)
                    # Coordinator responses are a separate deterministic delivery phase.
                    responses = transport.deliver(tick)
                    for node in nodes:
                        node.receive(inboxes.get(node.node_id, []) + responses.get(node.node_id, []))
                    if board.complete():
                        stop_reason = 'all_policy_goals_claimed'
                        break
                    if self.config['planner']['mode'] in ('deepseek', 'api') and (
                            budget.calls >= budget.max_calls or budget.tokens >= budget.max_tokens):
                        stop_reason = 'model_budget'
                        break
                    futures = []
                    for node in nodes:
                        path_budget = metadata.get('path_budget_m') if self.config.get('path_budget', True) else None
                        if path_budget and node.obs['path_length'] >= path_budget:
                            node.halted = True
                        if not node.has_command():
                            node.prepare()
                            futures.append((node, pool.submit(planner.decide, node)))
                    for node, future in futures:
                        try:
                            future.result()
                        except ModelError as exc:
                            node.fail_decision(str(exc))
                    if all(n.halted for n in nodes):
                        stop_reason = 'all_nodes_path_budget'
                        break
                    node_actions = [node.next_action() for node in nodes]
                    actions = node_actions + [0] * (total_sim_agents - len(node_actions))
                    response = environment.step_joint(actions)
                    tick += 1
                    self.log.emit('joint_step', tick=tick, requested_actions=node_actions,
                                  executed_actions=response['executed_actions'][:len(nodes)])
                    for node, obs, action in zip(nodes, response['observations'][:len(nodes)], response['executed_actions'][:len(nodes)]):
                        node.update(obs, action)
                        self.log.emit('observation_receipt', node_id=node.node_id, tick=tick,
                                      observation_id=obs['observation_id'], position=obs['position'],
                                      path_length=obs['path_length'])
                        if tick % 12 == 0 and len(names) > 1:
                            transport.send(node.node_id, [n for n in names if n != node.node_id], 'status',
                                {'position': obs['position'], 'focus_goal': node.focus()['goal_id'],
                                 'observation_tick': tick}, tick)
                    if tick % 12 == 0 or futures:
                        print(json.dumps({'tick': tick, 'paths_m': [round(n.obs['path_length'], 2) for n in nodes],
                                          'model_calls': budget.calls, 'tokens': budget.tokens}), flush=True)
            claims = [claim for node in nodes for claim in node.claims]
            for node in nodes:
                if getattr(node, 'runtime', None) is not None:
                    node.runtime.terminal(tick, reason=stop_reason)
            result = environment.finish(claims, transport.stats)
            result.update(stop_reason=stop_reason, ticks=tick,
                          elapsed_s=round(time.monotonic() - start, 3),
                          planner_mode=self.config['planner']['mode'], model=budget.summary(),
                          communication=transport.stats, pending_messages=len(transport.pending),
                          nodes=[{'node_id': n.node_id, 'decisions': n.decisions, 'errors': n.errors,
                                  'collisions': n.collisions, 'memory': n.graph.stats(),
                                  'observed_free_cells': int(n.map.free.sum()),
                                  'verification_count': len(n.verifications),
                                  'harness_runtime': n.runtime.summary() if getattr(n, 'runtime', None) else None}
                                 for n in nodes])
            for node in nodes:
                self.save(node.node_id + '_memory.json', node.graph.to_dict())
                self.save(node.node_id + '_verifications.json', list(node.verifications.values()))
            self.save('result.json', result)
            self.save('metrics.json', result['metrics'])
            self.save('aggregate.json', result['aggregate'])
            render_trajectories(result['trajectories'][:len(nodes)], self.run_dir / 'trajectories.png')
            self.log.emit('episode_finished', ticks=tick, stop_reason=stop_reason, metrics=result['metrics'])
            print(json.dumps({'status': 'finished', 'run_dir': str(self.run_dir), 'ticks': tick,
                              'stop_reason': stop_reason, 'metrics': result['metrics']}), flush=True)
            return result
        except BaseException as exc:
            self.save('failure.json', {'type': type(exc).__name__, 'message': str(exc)[-6000:]})
            raise
        finally:
            if environment is not None:
                environment.close()
            self.log.close()


def render_trajectories(trajectories, path):
    from PIL import Image, ImageDraw
    import numpy as np
    points = np.concatenate([np.asarray(t)[:, [0, 2]] for t in trajectories])
    lo, hi = points.min(axis=0) - .5, points.max(axis=0) + .5
    scale = 620 / max(hi - lo)
    image = Image.new('RGB', (800, 740), 'white')
    draw = ImageDraw.Draw(image)
    colors = ['#D73C41', '#2079C3', '#2B9467', '#9A59B5']
    draw.text((25, 15), 'HM3D-M3 harness trajectories (observed robot poses only)', fill='black')
    for i, trajectory in enumerate(trajectories):
        xy = ((np.asarray(trajectory)[:, [0, 2]] - lo) * scale + [70, 60]).astype(int)
        coordinates = [tuple(p) for p in xy]
        color = colors[i % len(colors)]
        if len(coordinates) >= 2:
            draw.line(coordinates, fill=color, width=3)
        for j, label in [(0, 'start'), (-1, 'end')]:
            x, y = coordinates[j]
            draw.ellipse((x-4, y-4, x+4, y+4), fill=color)
            draw.text((x+6, y+4), '%d %s' % (i, label), fill=color)
        draw.text((35 + i*170, 705), 'robot_%d' % i, fill=color)
    image.save(path)


__all__ = [
    "AgentSession",
    "EnvState",
    "EventLog",
    "StepRecord",
    "TeamSession",
    "render_trajectories",
]
