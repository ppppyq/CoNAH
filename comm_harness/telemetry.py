"""Small, optional execution telemetry. No model, simulator or UI dependencies.

One writer per worker/run; elapsed times use that process's monotonic clock.
Records describe application events, never wireless measurements or hidden reasoning.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

_context = ContextVar('harness_trace', default={})
_recorder = None


def clean(value, depth=0):
    if depth > 8:
        return '[depth limit]'
    if isinstance(value, dict):
        return {str(k): ('[redacted]' if re.search(r'password|secret|authorization|api_key|image_b64', str(k), re.I)
                         else clean(v, depth + 1)) for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple)):
        return [clean(v, depth + 1) for v in value[:80]]
    if isinstance(value, str):
        value = re.sub(r'data:image/[^\s"\']+', '[image omitted]', value)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[redacted]', value)
        value = re.sub(r'(?i)Bearer\s+[^\s,;]+', 'Bearer [redacted]', value)
        value = re.sub(r'(?i)((?:api[_-]?key|password|secret)\s*[=:]\s*)[^\s,;]+', r'\1[redacted]', value)
        return value[:6000]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return '<{}>'.format(type(value).__name__)


class Recorder:
    def __init__(self, run_dir, node_id, role='robot', heartbeat=True):
        self.node_id, self.role = node_id, role
        self.run_id = Path(run_dir).name
        self.session_id = uuid.uuid4().hex[:12]
        self.path = Path(run_dir) / 'monitor.jsonl'
        self.lock, self.stop_event = threading.RLock(), threading.Event()
        self.seq, self.phase, self.step = 0, 'BOOTSTRAP', None
        self.thread = None
        self.write('NODE_REGISTERED', role=role, status='starting')
        if heartbeat:
            self.thread = threading.Thread(target=self._heartbeat, daemon=True)
            self.thread.start()

    def write(self, event, **fields):
        try:
            with self.lock:
                self.seq += 1
                record = dict(_context.get(), **fields)
                record.update(schema_version=1, event=event,
                              event_id='{}:{}'.format(self.session_id, self.seq),
                              run_id=self.run_id, node_id=fields.get('node_id', self.node_id),
                              process_id=os.getpid(), clock_domain=self.session_id,
                              ts=time.time(), monotonic_ns=time.monotonic_ns())
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(clean(record), ensure_ascii=False, allow_nan=False) + '\n')
        except Exception:
            pass  # Diagnostics must not change a control decision.

    def _heartbeat(self):
        while not self.stop_event.wait(2):
            self.write('HEARTBEAT', phase=self.phase, step=self.step)

    def close(self, status='stopped'):
        self.stop_event.set()
        if self.thread:
            self.thread.join(3)
        self.write('NODE_STOPPED', status=status)


def configure(run_dir, node_id='robot-0', role='robot', heartbeat=True):
    global _recorder
    if _recorder:
        _recorder.close()
    _recorder = Recorder(run_dir, node_id, role, heartbeat)
    _context.set({})
    return _recorder


def close(status='stopped'):
    global _recorder
    if _recorder:
        _recorder.close(status)
        _recorder = None
    _context.set({})


def emit(event, **fields):
    if _recorder:
        _recorder.write(event, **fields)


def context(**fields):
    _context.set(dict(_context.get(), **fields))


def phase(name, **fields):
    if _recorder:
        _recorder.phase = name
        if fields.get('step') is not None:
            _recorder.step = fields['step']
    context(phase=name, **fields)
    emit('PHASE', phase=name, **fields)


@contextmanager
def span(kind, **fields):
    span_id = uuid.uuid4().hex[:16]
    token = _context.set(dict(_context.get(), **fields, span_id=span_id,
                              parent_span_id=_context.get().get('span_id')))
    started = time.monotonic()
    emit(kind + '_START')
    try:
        yield span_id
    except BaseException as exc:
        emit(kind + '_END', status='error', error_type=type(exc).__name__,
             error=str(exc), duration_s=time.monotonic() - started)
        raise
    else:
        emit(kind + '_END', status='ok', duration_s=time.monotonic() - started)
    finally:
        _context.reset(token)


def stage(name):
    def decorate(fn):
        signature = inspect.signature(fn)
        @wraps(fn)
        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs).arguments
            if args and hasattr(args[0], 'model'):
                args[0].last_decision_id = None
            old = _context.get().copy()
            phase(name, **{k: bound[k] for k in ('episode_id', 'step') if k in bound})
            try:
                with span('STAGE', operation=fn.__name__):
                    return fn(*args, **kwargs)
            finally:
                phase(old.get('phase', 'IDLE'), **{k: old.get(k) for k in ('episode_id', 'step')})
        return wrapped
    return decorate


def model_call(fn):
    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        with span('MODEL_CALL', model=self.model_name, provider=self.provider) as call_id:
            self.last_call_id = call_id
            token = _context.set(dict(_context.get(), model_call_id=call_id))
            try:
                result = fn(self, *args, **kwargs)
                emit('MODEL_OUTPUT', status='api_failure' if str(result).startswith('Error: ') else 'returned',
                     response=result)  # Public response only; no provider reasoning field.
                return result
            finally:
                _context.reset(token)
    return wrapped


def request_model(client, payload, attempt):
    messages = payload.get('messages', [])
    texts, image_count = [], 0
    for message in messages:
        content = message.get('content', '')
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text')
            image_count += sum(1 for p in content if isinstance(p, dict) and p.get('type') == 'image_url')
    # These IDs are text references, not a claim that all corresponding images survived trimming.
    refs = sorted(set(re.findall(r'\b(?:ob|rc)-\d+\b', '\n'.join(texts))))
    with span('MODEL_REQUEST', attempt=attempt, image_count=image_count,
              prompt_references=refs, model=payload.get('model')):
        response = client.chat.completions.create(**payload)
        usage = getattr(response, 'usage', None)
        choices = getattr(response, 'choices', None) or []
        content = response if isinstance(response, str) else getattr(choices[0].message, 'content', None) if choices else None
        emit('MODEL_RESPONSE', empty=not bool(isinstance(content, str) and content.strip()),
             usage={k: getattr(usage, v, None) for k, v in (
                 ('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'),
                 ('total_tokens', 'total_tokens'))} if usage else None)
        return response


def decision_call(fn):
    signature = inspect.signature(fn)
    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        purpose = signature.bind(self, *args, **kwargs).arguments.get('purpose', 'LLM')
        with span('DECISION', purpose=purpose) as decision_id:
            parsed, raw = fn(self, *args, **kwargs)
            self.last_decision_id = decision_id
            emit('DECISION_PARSED', decision_id=decision_id, purpose=purpose,
                 model_call_id=getattr(self.model, 'last_call_id', None),
                 parsed=parsed, status='parsed' if parsed is not None else 'unusable')
            return parsed, raw
    return wrapped


def apply_plan(agent, plan, step):
    decision_id = getattr(agent, 'last_decision_id', None)
    context(decision_id=decision_id, step=step)
    emit('PLAN_APPLIED', decision_id=decision_id, plan=plan)


@contextmanager
def service(name):
    emit('SERVICE_STATUS', service=name, status='starting')
    try:
        yield
    except BaseException as exc:
        emit('SERVICE_STATUS', service=name, status='failed', error=str(exc))
        raise
    else:
        emit('SERVICE_STATUS', service=name, status='ready')
