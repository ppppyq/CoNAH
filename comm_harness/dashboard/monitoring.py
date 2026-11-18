"""Read-only projections of recorded events; never feed evaluator truth to agents."""
from collections import Counter, OrderedDict, deque
import copy
import json
from pathlib import Path
import threading
import time


class Projection:
    def __init__(self):
        self.offsets, self.identities = {}, {}
        self.counts = Counter()
        self.events = deque(maxlen=240)
        self.requests, self.decisions, self.messages = OrderedDict(), OrderedDict(), OrderedDict()
        self.nodes, self.services = {}, {}
        self.phase, self.gpu, self.last_ts = None, None, None
        self.durations = Counter()
        self.usage = Counter()
        self.usage_responses = 0
        self.delivery_sequences = {}
        self.source = 'unavailable'
        self.truncated = False
        self.backlog = False

    @staticmethod
    def bounded(mapping, key):
        item = mapping.setdefault(key, {})
        if len(mapping) > 200:
            mapping.popitem(last=False)
        return item

    def ingest(self, e):
        event = e.get('event')
        if not isinstance(event, str):
            return
        self.counts[event] += 1
        self.last_ts = e.get('ts', self.last_ts)
        if event != 'HEARTBEAT':
            self.events.append(e)
        node_id = e.get('node_id') or e.get('node')
        if node_id:
            node = self.nodes.setdefault(str(node_id), {'id': str(node_id)})
            node['last_event_at'] = e.get('ts')
            if e.get('step') is not None:
                node['step'] = e['step']
            if e.get('phase'):
                node['phase'] = e['phase']
            if event == 'NODE_REGISTERED':
                node.update(role=e.get('role'), registered_at=e.get('ts'))
            if event == 'HEARTBEAT':
                node['heartbeat_at'] = e.get('ts')
            if event == 'NODE_STOPPED':
                node['stopped_at'] = e.get('ts')
        if event == 'PHASE':
            self.phase = {k: e.get(k) for k in ('phase', 'step', 'episode_id', 'ts', 'node_id')}
        if event == 'SERVICE_STATUS':
            self.services[e.get('service')] = e
        if event == 'GPU_SNAPSHOT':
            self.gpu = e
        if event in ('MODEL_REQUEST_START', 'MODEL_REQUEST_END', 'MODEL_RESPONSE'):
            key = e.get('span_id')
            if not key:
                return
            request = self.bounded(self.requests, key)
            request.update({k: v for k, v in e.items() if k not in ('event', 'event_id', 'ts')})
            if event == 'MODEL_REQUEST_START':
                request.update(started_at=e.get('ts'), status='pending')
            elif event == 'MODEL_REQUEST_END':
                request.update(ended_at=e.get('ts'), status=e.get('status'))
                self.durations['model_request_s'] += e.get('duration_s') or 0
                if e.get('status') == 'error':
                    self.counts['model_request_errors'] += 1
            elif event == 'MODEL_RESPONSE' and isinstance(e.get('usage'), dict):
                self.usage_responses += 1
                for k, v in e['usage'].items():
                    if isinstance(v, (int, float)):
                        self.usage[k] += v
        if event == 'DECISION_PARSED':
            self.bounded(self.decisions, e.get('decision_id')).update(e)
        if event == 'PLAN_APPLIED' and e.get('decision_id'):
            self.bounded(self.decisions, e['decision_id']).update(applied_plan=e.get('plan'), applied_step=e.get('step'))
        if event == 'TOOL_EXECUTION_END':
            self.durations['tool_execution_s'] += e.get('duration_s') or 0
        if event.startswith('message_') and e.get('message_id'):
            message = self.bounded(self.messages, e['message_id'])
            if event == 'message_sent':
                self.counts['source_bytes'] += e.get('serialized_bytes') or 0
                self.counts['link_bytes'] += (e.get('serialized_bytes') or 0) * len(e.get('recipients') or [])
                message.update({k: e.get(k) for k in ('message_id', 'sender', 'recipients', 'kind',
                               'serialized_bytes', 'created_tick', 'ttl_ticks', 'sequence')})
                message['sent_at'] = e.get('ts')
            else:
                if event == 'message_delivered' and isinstance(e.get('sequence'), int):
                    sequence_key = (e.get('sender'), e.get('recipient'))
                    previous = self.delivery_sequences.get(sequence_key)
                    self.counts['deliveries_with_sequence'] += 1
                    if previous is not None and e['sequence'] < previous:
                        self.counts['reordered_deliveries'] += 1
                    self.delivery_sequences[sequence_key] = max(previous or 0, e['sequence'])
                deliveries = message.setdefault('delivery_events', [])
                if len(deliveries) < 40:
                    deliveries.append({k: e.get(k) for k in ('event', 'recipient', 'delivered_tick', 'age_ticks', 'ts')})

    def read(self, paths, source):
        self.backlog = False
        self.source = source
        for path in paths:
            stat = path.stat()
            key = (stat.st_dev, stat.st_ino) if stat.st_ino else str(path)
            offset = self.offsets.get(key, 0)
            if stat.st_size < offset:
                # Worker logs are append-only; a replacement requires a fresh projection.
                return False
            with path.open('rb') as stream:
                stream.seek(offset)
                data = stream.read(4 * 1024 * 1024)
            end = data.rfind(b'\n') + 1
            self.offsets[key] = offset + end
            self.backlog |= self.offsets[key] < stat.st_size
            for line in data[:end].splitlines():
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        self.ingest(record)
                except (ValueError, UnicodeError, TypeError):
                    self.counts['invalid_records'] += 1
        return True

    def snapshot(self, result, summary):
        now = time.time()
        ended = summary.get('state') in ('completed', 'failed', 'cancelled')
        nodes = copy.deepcopy(list(self.nodes.values()))
        for node in nodes:
            heartbeat = node.get('heartbeat_at')
            node['heartbeat_age_s'] = max(0, now - heartbeat) if heartbeat else None
            node['status'] = ('ended' if ended or node.get('stopped_at') else
                              'live' if heartbeat and now - heartbeat < 8 else
                              'stale' if heartbeat else 'unrecorded')
        if not nodes:
            raw_nodes = result.get('nodes', [])
            if isinstance(raw_nodes, dict):
                raw_nodes = [dict(v, id=k) for k, v in raw_nodes.items() if isinstance(v, dict)]
            nodes = [dict(n, status='historical', heartbeat_at=None) for n in raw_nodes if isinstance(n, dict)]
        communication = result.get('communication')
        message_count = self.counts['message_sent']
        comm_recorded = message_count > 0 or isinstance(communication, dict)
        counts = dict(self.counts)
        requests = copy.deepcopy(list(self.requests.values()))
        for request in requests:
            if request.get('status') == 'pending':
                request['status'] = 'incomplete' if ended else 'pending'
                request['waiting_s'] = None if ended else max(0, now - request['started_at']) if request.get('started_at') else None
        historical_usage = result.get('model_usage') or {}
        return {'source': self.source, 'last_event_at': self.last_ts, 'backlog': self.backlog,
                'phase': copy.deepcopy(self.phase), 'nodes': nodes,
                'services': copy.deepcopy(list(self.services.values())), 'gpu': self.gpu,
                'counts': counts, 'durations': dict(self.durations),
                'model': {'requests': counts.get('MODEL_REQUEST_START') if self.source == 'live_schema_v1' else None,
                          'retries': counts.get('MODEL_RETRY', 0) if self.source == 'live_schema_v1' else None,
                          'json_retries': counts.get('JSON_RETRY', 0) if self.source == 'live_schema_v1' else None,
                          'errors': counts.get('model_request_errors', 0) if self.source == 'live_schema_v1' else None,
                          'usage': dict(self.usage) if self.usage_responses else None,
                          'usage_responses': self.usage_responses, 'historical_usage': historical_usage,
                          'recent_requests': requests[-80:]},
                'decisions': copy.deepcopy(list(self.decisions.values())[-40:]),
                'communication': {'mode': 'ideal' if comm_recorded else 'not_connected',
                                  'statistics': communication or ({'messages_sent': message_count,
                                      'messages_delivered': counts.get('message_delivered', 0),
                                      'source_bytes': counts.get('source_bytes', 0), 'link_bytes': counts.get('link_bytes', 0),
                                      'expired': counts.get('message_expired', 0)} if comm_recorded else None),
                                  'duplicates_recorded': counts.get('message_duplicate', 0) if comm_recorded else None,
                                  'reordered_deliveries': counts.get('reordered_deliveries', 0) if counts.get('deliveries_with_sequence') else None,
                                  'recent_messages': copy.deepcopy(list(self.messages.values())[-80:]),
                                  'wireless_metrics': None,
                                  'note': '应用层序列化字节与仿真 tick；未测量无线吞吐、RSSI、SINR、丢包率或单向链路时延。'},
                'events': list(self.events),
                'notes': ['请求耗时为客户端调用总耗时；未拆分排队、网络和推理时间。',
                          'prompt_references 表示请求文本引用的观测/回执编号；不证明裁剪后仍包含全部图像。',
                          '历史记录缺少的字段保持空值；服务 ready 是初始化记录，非持续健康承诺。']}


class MonitorStore:
    def __init__(self):
        self.lock, self.cache = threading.RLock(), OrderedDict()

    def get(self, path, result, summary):
        with self.lock:
            if (path / 'monitor.jsonl').is_file():
                paths, source = [path / 'monitor.jsonl'], 'live_schema_v1'
            else:
                paths = sorted((path / 'rgb').glob('*/events_r0_w1.jsonl'))
                if not paths and (path / 'events.jsonl').is_file():
                    paths = [path / 'events.jsonl']
                source = 'legacy' if paths else 'unavailable'
            # Enforce the same run boundary as images/logs, including symlinks.
            for item in paths:
                if path.resolve() not in item.resolve().parents:
                    raise ValueError('监控日志必须位于运行目录内')
            key = str(path)
            identities = {(p.stat().st_dev, p.stat().st_ino) for p in paths}
            projection = self.cache.get(key)
            if projection is None or projection.source != source or projection.identities != identities:
                projection = self.cache[key] = Projection()
                projection.identities = identities
            if not projection.read(paths, source):
                projection = self.cache[key] = Projection()
                projection.identities = identities
                projection.read(paths, source)
            self.cache.move_to_end(key)
            if len(self.cache) > 12:
                self.cache.popitem(last=False)
            return projection.snapshot(result, summary)


def comparison(details):
    rows, signatures = [], []
    for data in details:
        result, config, task = data['result'], data['config'], data['task']
        metrics, planner = result.get('metrics', {}), config.get('planner', {})
        if not isinstance(metrics, dict):
            metrics = {}
        conditions = {'task': task, 'model': planner.get('model'), 'provider': planner.get('provider'),
                      'seed': config.get('seed', task.get('seed')),
                      'max_calls': planner.get('max_calls'), 'max_total_tokens': planner.get('max_total_tokens'),
                      'method': config.get('method'), 'platform': config.get('platform', task.get('platform'))}
        signatures.append(conditions)
        usage = result.get('model_usage', {})
        rows.append({'id': data['id'], 'state': data['state'], 'conditions': conditions,
                     'success': metrics.get('success'), 'steps': metrics.get('steps_taken'),
                     'path_length_m': metrics.get('path_length'), 'spl': metrics.get('spl'),
                     'elapsed_s': result.get('elapsed_s'), 'model_calls': usage.get('total_calls'),
                     'tokens': usage.get('total_tokens'), 'communication': result.get('communication')})
    differing = [k for k in signatures[0] if any(s[k] != signatures[0][k] for s in signatures[1:])]
    missing = [k for k in signatures[0] if any(s[k] is None or s[k] == {} for s in signatures)]
    return {'rows': rows, 'differing_conditions': differing, 'missing_conditions': missing,
            'matched_recorded_conditions': not differing and not missing,
            'note': '仅比较已有记录；条件相同也不代表统计显著。旧实验缺失预算、种子等条件时不能判定为严格对照。'}
