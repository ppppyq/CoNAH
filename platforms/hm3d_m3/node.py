"""One embodied harness: private memory, observed map, tools, and action state."""
import base64
import io
import json
import math
from pathlib import Path
import numpy as np
from PIL import Image

from comm_harness.memory import WorldGraph, retrieval
from comm_harness.session.base import EnvState
from comm_harness.tools import HarnessRuntime, SideEffect, Toolkit, ToolError, validate
from platforms.hm3d_m3.mapping import ObservedMap


def jpeg_b64(rgb):
    image = rgb if isinstance(rgb, Image.Image) else Image.fromarray(rgb)
    stream = io.BytesIO()
    image.save(stream, format='JPEG', quality=80)
    return base64.b64encode(stream.getvalue()).decode('ascii')


def image_part(encoded):
    return {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + encoded}}


class HarnessNode:
    def __init__(self, node_id, goals, config, transport, planner, emit, run_dir, node_names):
        self.node_id, self.goals, self.config = node_id, goals, config
        self.transport, self.planner, self.emit = transport, planner, emit
        self.node_names = list(node_names)
        self.run_dir = Path(run_dir)
        self.frames_dir = self.run_dir / 'frames' / node_id
        self.frames_dir.mkdir(parents=True)
        self.retrieval = retrieval
        self.graph = WorldGraph()
        self.history, self.inbox, self.claims = [], [], []
        self.team_goals, self.teammates, self.verifications = {}, {}, {}
        self.scan_remaining = 12
        self.scan_frames, self.panorama = [], []
        self.command, self.obs, self.map = None, None, None
        self.candidates, self.selected_ids = [], []
        self.tick, self.decisions, self.errors, self.collisions = 0, 0, 0, 0
        self.cooldown, self.just_claimed, self.halted = 0, False, False
        self.env_state = EnvState(self.run_dir / 'states' / node_id)
        self.runtime = HarnessRuntime(
            emit=lambda event_type, step, **fields: self.emit(event_type, node_id=self.node_id, step=step, **fields),
            trace_phases=True,
        )
        self.toolkit = Toolkit(
            self.log,
            state=self.env_state,
            runtime=self.runtime,
            registry=self.runtime.registry,
            step_fn=lambda: self.tick,
        )
        self._register_tools()
        self.runtime.start_episode(config.get('episode_id', 'episode_0'), step=0, node_id=self.node_id)

    def log(self, event, **fields):
        self.emit(event, node_id=self.node_id, tick=self.tick, **fields)

    def receive(self, messages):
        for msg in messages:
            self.inbox.append(msg)
            if msg['kind'] == 'task_snapshot' and msg['sender'] == 'coordinator':
                for state in msg['payload']['goals']:
                    old = self.team_goals.get(state['goal_id'])
                    if old is None or state['version'] >= old['version']:
                        self.team_goals[state['goal_id']] = dict(state)
            if msg['kind'] == 'status':
                self.teammates[msg['sender']] = dict(msg['payload'], delivered_tick=msg['delivered_tick'])
        self.inbox = self.inbox[-30:]

    def update(self, obs, last_action=None):
        prior = self.obs
        self.obs, self.tick = obs, obs['tick']
        if self.map is None:
            self.map = ObservedMap(obs['position'])
        self.map.update(obs)
        if prior is not None and last_action == 1:
            movement = float(np.linalg.norm(np.asarray(obs['position']) - prior['position']))
            if movement < .035:
                self.collisions += 1
                if self.command:
                    self.command['stalls'] = self.command.get('stalls', 0) + 1
        if self.scan_remaining >= 0 and self.scan_remaining % 3 == 0 and last_action == 3:
            self.scan_frames.append({'rgb': obs['rgb'], 'tick': self.tick,
                                     'observation_id': obs['observation_id'],
                                     'position': obs['position'], 'rotation_matrix': obs['rotation_matrix']})
            if self.scan_remaining == 0:
                self.panorama = self.scan_frames[-4:]
                self.scan_frames = []
                self.scan_remaining = -1
                self.log('panorama_complete', observation_ids=[f['observation_id'] for f in self.panorama])
        if self.cooldown:
            self.cooldown -= 1
        if self.tick == 0:
            self.graph.observe_place(np.asarray(obs['position'])[[0, 2]], step=0,
                                     observation_ids=[obs['observation_id']])
        if getattr(self, 'runtime', None) is not None:
            self.runtime.observation(self.tick, 'rgb_depth', external_id=obs['observation_id'])
            self.runtime.clear_fresh_observation(self.tick, source='sensor_update')
        if getattr(self, 'env_state', None) is not None:
            with self.env_state.record_step(
                state={'tick': self.tick, 'position': obs['position'], 'path_length': obs.get('path_length', 0.0)},
                command={'action': last_action} if last_action is not None else None,
            ):
                pass

    def has_command(self):
        return self.scan_remaining > 0 or self.command is not None or self.cooldown > 0 or self.halted

    def focus(self):
        unfinished = [g for g in self.goals if self.team_goals.get(g['goal_id'], {}).get('state') != 'CLAIMED_FOUND']
        owned = [g for g in unfinished if self.team_goals.get(g['goal_id'], {}).get('owner') == self.node_id]
        pending = [g for g in unfinished if self.team_goals.get(g['goal_id'], {}).get('owner') is None]
        return (owned or pending or unfinished or self.goals)[0]

    def goal_query(self, goal_id):
        goal = next(g for g in self.goals if g['goal_id'] == goal_id)
        return goal_id + ': ' + goal.get('text', goal['category'])

    def prepare(self):
        self.just_claimed = False
        self.decisions += 1
        self.graph.observe_place(np.asarray(self.obs['position'])[[0, 2]], step=self.tick,
                                 observation_ids=[self.obs['observation_id']])
        self.candidates = self.map.candidates(self.tick, self.graph)
        for message in self.inbox[-12:]:
            payload = message['payload']
            if message['kind'] == 'candidate' and payload.get('world_xz') and payload.get('goal_id') == self.focus()['goal_id']:
                candidate = self.map.approach_candidate(payload['world_xz'], 'peer_%s_%d' % (message['sender'], message['sequence']))
                if candidate:
                    candidate['source_message_id'] = message['message_id']
                    candidate['hypothesized_goal_id'] = payload['goal_id']
                    self.candidates.append(candidate)
        for candidate in self.candidates:
            candidate['previously_selected'] = any(np.linalg.norm(np.asarray(candidate['world_xz']) - p) < .55
                                                   for p in self.selected_ids[-12:])
        self.log('decision_context', focus=self.focus()['goal_id'],
                 observation_id=self.obs['observation_id'], candidates=self.candidates,
                 team_goals=self.team_goals, memory=self.graph.stats())

    def prompt_messages(self):
        focus = self.focus()
        goals = [{k: v for k, v in g.items() if k != 'reference_image'} for g in self.goals]
        context = {'node_id': self.node_id, 'tick': self.tick, 'focus_goal': focus['goal_id'],
                   'goals': goals, 'team_state': self.team_state(),
                   'current_observation_id': self.obs['observation_id'],
                   'position': self.obs['position'], 'candidates': self.candidates,
                   'recent_receipts': self.history[-6:],
                   'memory': self.retrieval.recall(self.graph, self.goal_query(focus['goal_id']),
                       current_xz=np.asarray(self.obs['position'])[[0, 2]]).text}
        content = [{'type': 'text', 'text': json.dumps(context, ensure_ascii=False)}]
        recent_panorama = [frame for frame in self.panorama
            if self.tick - frame['tick'] <= 18 and np.linalg.norm(
                np.asarray(frame['position']) - self.obs['position']) < .4]
        for i, frame in enumerate(recent_panorama):
            content += [{'type': 'text', 'text': 'HISTORICAL panorama view %d, evidence=%s. Not current arrival evidence.' % (i, frame['observation_id'])},
                        image_part(jpeg_b64(frame['rgb']))]
        content += [{'type': 'text', 'text': 'CURRENT RGB, evidence=' + self.obs['observation_id']},
                    image_part(jpeg_b64(self.obs['rgb']))]
        Image.fromarray(self.obs['rgb']).save(self.frames_dir / ('%04d_current.jpg' % self.tick))
        local_map = self.map.render(self.candidates)
        local_map.save(self.frames_dir / ('%04d_map.png' % self.tick))
        content += [{'type': 'text', 'text': 'OBSERVED local map: white free, black obstacle, gray unknown; red robot arrow; blue candidate IDs.'},
                    image_part(jpeg_b64(local_map))]
        for goal in self.goals:
            if goal.get('reference_image') and self.team_goals.get(goal['goal_id'], {}).get('state') != 'CLAIMED_FOUND':
                content += [{'type': 'text', 'text': 'TARGET REFERENCE for %s, NOT an observation: %s' % (goal['goal_id'], goal['category'])},
                            image_part(goal['reference_image'])]
        for message in reversed(self.inbox):
            payload = message['payload']
            if (message['kind'] == 'candidate' and payload.get('image_b64')
                    and payload.get('goal_id') == focus['goal_id']):
                content += [{'type': 'text', 'text': 'RECEIVED TEAMMATE HISTORICAL CANDIDATE. Not current arrival evidence. ' +
                              json.dumps({k: v for k, v in payload.items() if k != 'image_b64'})},
                            image_part(payload['image_b64'])]
                break
        system = ('You control ONE robot in a cooperative embodied search task. Your harness executes only tool calls. '
            'Prioritize your focus goal, but opportunistically verify other unresolved goals you actually see. '
            'ON means category; TN requires all textual attributes/relations; IIN means the same instance as the reference image. '
            'Use navigate with an exact candidate ID from your observed map, or look_left/look_right/look_back to turn in place. '
            'Positive bearing is right, negative is left, relative to the current RGB. Unknown map is not traversible. '
            'Choose promising unvisited frontiers using RGB and map; avoid repeated selections and failed locations. '
            'Use observe panorama after entering a new area to inspect surroundings, not repeatedly at the same position. '
            'If a target is CURRENTLY visible, call verify_goal; if it passes, call declare_goal with its verification_id. '
            'Never claim success from a reference image, stale panorama, category-only match for TN/IIN, or teammate evidence. '
            'Publish useful candidates or room descriptions to teammates; recall can retrieve past places for backtracking. '
            'A declared goal is a policy claim, not evaluator success. Team updates arrive only through messages. '
            'After requesting navigation or a scan, wait for execution receipts; issue at most one movement command per decision. '
            'Be concise and use tools rather than a narrative answer.')
        return [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}]

    def next_action(self):
        if self.halted or self.cooldown:
            return 0
        if self.scan_remaining > 0:
            self.scan_remaining -= 1
            return 3
        if self.command is None:
            return 0
        command = self.command
        if command['kind'] == 'turn':
            command['remaining'] -= 1
            action = command['action']
            if command['remaining'] == 0:
                self.command = None
                self.log('command_complete', status='turned')
            return action
        action, status = self.map.next_action(command['world_xz'])
        if self.tick - command['started_tick'] >= 24 or command.get('stalls', 0) >= 2:
            action, status = 0, 'no_progress'
        if status != 'running':
            if status != 'arrived':
                self.graph.record_failure('NO_PROGRESS', self.tick, subgoal=self.goal_query(self.focus()['goal_id']), detail=status,
                                          evidence_ids=[self.obs['observation_id']])
            receipt = {'status': status, 'candidate_id': command['candidate_id']}
            self.history.append({'tick': self.tick, 'command_receipt': receipt})
            self.log('command_complete', **receipt)
            self.command = None
        return action

    def fail_decision(self, message):
        self.errors += 1
        self.cooldown = 3
        self.log('decision_error', error=message)
        self.history.append({'tick': self.tick, 'decision_error': message})

    def _register_tools(self):
        string = {'type': 'string', 'maxLength': 1000}
        gid = {'type': 'string', 'enum': [g['goal_id'] for g in self.goals]}
        self.toolkit.add('observe', 'Read current evidence IDs or execute a 12-tick full panorama.',
            {'mode': {'type': 'string', 'enum': ['current', 'panorama']}}, ['mode'], self.observe,
            side_effect=SideEffect.READ_ONLY)
        self.toolkit.add('recall', 'Retrieve only this robot\'s visited places and goal-specific failures.',
            {'query': string}, ['query'], self.recall,
            side_effect=SideEffect.READ_ONLY)
        self.toolkit.add('navigate', 'Navigate to a listed candidate ID, or look_left/look_right/look_back. One command at a time.',
            {'candidate_id': string, 'reason': string}, ['candidate_id', 'reason'], self.navigate,
            side_effect=SideEffect.WORLD_MUTATING, step_consuming=True, physical_motion=True)
        self.toolkit.add('verify_goal', 'Verify a target in CURRENT RGB using its full modality and depth. Returns a verification_id.',
            {'goal_id': gid}, ['goal_id'], self.verify_goal,
            side_effect=SideEffect.PLANNING)
        self.toolkit.add('declare_goal', 'Declare a goal only using a passing verification from this robot at this tick.',
            {'goal_id': gid, 'verification_id': string}, ['goal_id', 'verification_id'], self.declare_goal,
            side_effect=SideEffect.TERMINAL)
        self.toolkit.add('read_team_state', 'Read only team information delivered to this robot.', {}, [], self.team_state,
            side_effect=SideEffect.READ_ONLY)
        self.toolkit.add('publish', 'Share a useful candidate or exploration summary. An optional image is transmitted and metered.',
            {'kind': {'type': 'string', 'enum': ['candidate', 'exploration_summary']}, 'summary': string,
             'include_image': {'type': 'boolean'}}, ['kind', 'summary', 'include_image'], self.publish,
            side_effect=SideEffect.NETWORK_TX)
        self.toolkit.add('request_assignment', 'Request a versioned claim/release; wait for coordinator acknowledgement.',
            {'goal_id': gid, 'operation': {'type': 'string', 'enum': ['claim', 'release']},
             'expected_version': {'type': 'integer', 'minimum': 1}},
             ['goal_id', 'operation', 'expected_version'], self.request_assignment,
            side_effect=SideEffect.NETWORK_TX)

    def observe(self, mode):
        if mode == 'panorama':
            if self.has_command():
                raise ToolError('movement_command_already_pending')
            if self.panorama and self.tick - self.panorama[-1]['tick'] < 4:
                return {'status': 'recent_panorama_available', 'evidence_ids': [f['observation_id'] for f in self.panorama]}
            self.scan_remaining, self.scan_frames = 12, []
            return {'status': 'scan_queued', 'ticks': 12}
        return {'current_observation_id': self.obs['observation_id'], 'position': self.obs['position']}

    def recall(self, query):
        return {'text': self.retrieval.recall(self.graph, query,
            current_xz=np.asarray(self.obs['position'])[[0, 2]]).text}

    def navigate(self, candidate_id, reason):
        if self.has_command():
            raise ToolError('movement_command_already_pending')
        turns = {'look_left': (2, 3), 'look_right': (3, 3), 'look_back': (3, 6)}
        if candidate_id in turns:
            action, remaining = turns[candidate_id]
            self.command = {'kind': 'turn', 'action': action, 'remaining': remaining}
        else:
            candidate = next((c for c in self.candidates if c['id'] == candidate_id), None)
            if candidate is None:
                raise ToolError('unknown_or_stale_candidate_id')
            self.command = {'kind': 'navigate', 'candidate_id': candidate_id,
                            'world_xz': list(candidate['world_xz']), 'started_tick': self.tick}
            self.selected_ids.append(list(candidate['world_xz']))
        self.graph.annotate_place(semantic_summary=reason)
        return {'status': 'command_queued', 'candidate_id': candidate_id}

    def team_state(self):
        summaries = []
        for msg in self.inbox[-10:]:
            if msg['kind'] in ('candidate', 'exploration_summary', 'assignment_ack', 'goal_claim'):
                payload = {k: v for k, v in msg['payload'].items() if k != 'image_b64'}
                summaries.append({'sender': msg['sender'], 'kind': msg['kind'], 'payload': payload,
                                  'age_ticks': self.tick - msg['created_tick']})
        return {'goals': list(self.team_goals.values()), 'teammates': self.teammates, 'messages': summaries}

    def publish(self, kind, summary, include_image):
        payload = {'summary': summary, 'observation_id': self.obs['observation_id'],
                   'position': self.obs['position']}
        if include_image:
            payload['image_b64'] = jpeg_b64(self.obs['rgb'])
        self.graph.annotate_place(semantic_summary=summary)
        mid = self.transport.send(self.node_id, [n for n in self.node_names if n != self.node_id],
                                  kind, payload, self.tick)
        return {'message_id': mid, 'status': 'queued_for_delivery'}

    def request_assignment(self, goal_id, operation, expected_version):
        mid = self.transport.send(self.node_id, ['coordinator'], 'assignment_request',
              {'goal_id': goal_id, 'operation': operation, 'expected_version': expected_version}, self.tick)
        return {'message_id': mid, 'status': 'awaiting_acknowledgement'}

    def verify_goal(self, goal_id):
        if self.has_command():
            raise ToolError('finish_current_motion_before_verification')
        if not hasattr(self.planner, 'request'):
            raise ToolError('verification_requires_real_model')
        goal = next(g for g in self.goals if g['goal_id'] == goal_id)
        for cached in self.verifications.values():
            if cached['goal_id'] == goal_id and cached['observation_id'] == self.obs['observation_id']:
                return cached
        specification = {k: v for k, v in goal.items() if k != 'reference_image'}
        modality_rules = {
            'ON': 'This is OBJECT CATEGORY navigation. Decide only whether the requested category is identifiable in CURRENT RGB. '
                  'There is no target reference image, no particular instance, and no extra attribute requirement. '
                  'An identifiable partially visible object can match.',
            'TN': 'This is TEXT navigation. Check the complete supplied description, including attributes and spatial relations, '
                  'against CURRENT RGB. There is NO target reference image for this goal; do not require an image match.',
            'IIN': 'This is IMAGE INSTANCE navigation. Compare CURRENT RGB with the labeled reference image and decide '
                   'whether they show the SAME physical object instance. View angle and distance may differ. '
                   'Same category alone is insufficient.'}
        content = [{'type': 'text', 'text': 'Verify this target in CURRENT OBSERVATION: ' + json.dumps(specification) +
            '. ' + modality_rules[goal['modality']] +
            ' Images are rendered from a scanned 3D scene; mesh/texture artifacts are expected. '
            'Assess the visual evidence, not photorealism; still report uncertainty if identity or required relations are unclear. '
            'Return report_verification. bbox=[x1,y1,x2,y2] in absolute pixels of the 640x480 CURRENT image. '
            'Use matched=false when uncertain. The reference image is not current evidence.'}, image_part(jpeg_b64(self.obs['rgb']))]
        if goal.get('reference_image'):
            content += [{'type': 'text', 'text': 'TARGET REFERENCE ONLY'}, image_part(goal['reference_image'])]
        schema = {'type': 'object', 'properties': {
            'matched': {'type': 'boolean'}, 'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
            'bbox': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4},
            'reason': {'type': 'string', 'maxLength': 1200}},
            'required': ['matched', 'confidence', 'bbox', 'reason'], 'additionalProperties': False}
        tools = [{'type': 'function', 'function': {'name': 'report_verification',
                  'description': 'Return the visual verification without executing motion.', 'parameters': schema}}]
        message = self.planner.request([{'role': 'user', 'content': content}], tools,
            self.node_id, purpose='verify_' + goal_id,
            tool_choice={'type': 'function', 'function': {'name': 'report_verification'}})
        calls = message.get('tool_calls') or []
        if len(calls) != 1 or calls[0]['function']['name'] != 'report_verification':
            raise ToolError('invalid_verification_response')
        result = json.loads(calls[0]['function']['arguments'])
        validate(result, schema)
        h, w = self.obs['depth'].shape
        x1, y1, x2, y2 = result['bbox']
        geometry_valid, distance = False, None
        if 0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h and (x2-x1)*(y2-y1) >= 36:
            # Central half reduces background contamination; invalid depth never passes.
            xa, xb = int(x1 + (x2-x1)*.25), int(x2 - (x2-x1)*.25)
            ya, yb = int(y1 + (y2-y1)*.25), int(y2 - (y2-y1)*.25)
            values = self.obs['depth'][ya:yb, xa:xb]
            valid = values[np.isfinite(values) & (values > .12) & (values < self.obs['max_depth']-.05)]
            if valid.size >= max(4, values.size * .4):
                fx = w / (2 * math.tan(math.radians(self.obs['hfov']) / 2))
                distance = float(np.median(valid) * math.sqrt(1 + (((x1+x2)/2 - (w-1)/2)/fx)**2))
                geometry_valid = distance <= self.config.get('claim_max_depth_m', 1.0)
        passed = bool(result['matched'] and result['confidence'] >= .8 and geometry_valid)
        vid = '%s/verify/%d' % (self.obs['observation_id'], len(self.verifications))
        record = {'verification_id': vid, 'goal_id': goal_id, 'tick': self.tick,
                  'node_id': self.node_id, 'observation_id': self.obs['observation_id'],
                  'passed': passed, 'depth_distance_m': distance, 'visual_result': result}
        self.verifications[vid] = record
        self.log('goal_verification', verification=record)
        if result['matched'] and result['confidence'] >= .5 and distance is not None:
            raw_depth = float(np.median(valid))
            local_point = np.asarray([((x1+x2)/2 - (w-1)/2) * raw_depth / fx,
                                      -((y1+y2)/2 - (h-1)/2) * raw_depth / fx, -raw_depth])
            world = np.asarray(self.obs['camera_position']) + np.asarray(self.obs['rotation_matrix']).dot(local_point)
            payload = {'goal_id': goal_id, 'confidence': result['confidence'], 'summary': result['reason'],
                       'observation_id': self.obs['observation_id'], 'world_xz': world[[0, 2]].tolist(),
                       'bbox': result['bbox'], 'image_b64': jpeg_b64(self.obs['rgb']),
                       'status': 'candidate_only'}
            recipients = [n for n in self.node_names if n != self.node_id]
            if recipients:
                self.transport.send(self.node_id, recipients, 'candidate', payload, self.tick)
            candidate = self.map.approach_candidate(payload['world_xz'], 'object_%s_%d' % (goal_id, self.tick))
            if candidate:
                self.candidates.append(candidate)
                record['approach_candidate'] = candidate
        if not passed:
            self.graph.record_failure('STOP_REJECTED', self.tick, subgoal=self.goal_query(goal_id),
                                      detail=result['reason'], evidence_ids=[self.obs['observation_id']])
        return record

    def declare_goal(self, goal_id, verification_id):
        record = self.verifications.get(verification_id)
        if (not record or not record['passed'] or record['goal_id'] != goal_id
                or record['node_id'] != self.node_id or record['tick'] != self.tick
                or record['observation_id'] != self.obs['observation_id'] or self.has_command()):
            raise ToolError('missing_stale_or_invalid_local_verification')
        if any(c['goal_id'] == goal_id for c in self.claims):
            raise ToolError('duplicate_goal_claim')
        claim = {k: record[k] for k in ['goal_id', 'node_id', 'tick', 'observation_id', 'verification_id']}
        self.claims.append(claim)
        self.just_claimed = True
        self.transport.send(self.node_id, ['coordinator'] + [n for n in self.node_names if n != self.node_id],
                            'goal_claim', claim, self.tick)
        self.log('goal_claim', claim=claim)
        return {'status': 'CLAIMED_FOUND', 'goal_id': goal_id, 'evaluation': 'not_yet_scored'}
