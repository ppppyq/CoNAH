"""Execute registered capabilities without importing a task, model or platform."""
from comm_harness import telemetry
from dataclasses import dataclass, field
from typing import Any, Dict

from comm_harness.tools import ToolError, validate
from comm_harness.tools.contracts import safe_value


@dataclass
class ToolOutcome:
    # Native values stay in-process. Only the explicitly selected trace fields
    # enter the event log; sensor arrays and private evaluator metrics do not.
    value: Any = None
    trace: Dict = field(default_factory=dict)
    consumed_steps: int = 0


class ToolExecutor:
    def __init__(self, registry, runtime=None, allowed_tools=None):
        self.registry = registry
        self.runtime = runtime
        self.allowed_tools = None if allowed_tools is None else frozenset(allowed_tools)
        self.bindings = {}

    def bind(self, spec, handler, *, owner, parameters=None):
        if spec.name in self.bindings:
            raise ToolError('tool already bound: ' + spec.name)
        existing = self.registry.get(spec.name)
        if existing is None:
            self.registry.register(spec)
        elif existing != spec:
            raise ToolError('tool declaration differs from registered contract: ' + spec.name)
        self.bindings[spec.name] = (handler, owner, parameters)

    def catalog(self):
        return [{'name': name, 'owner': owner,
                 'allowed': self.allowed_tools is None or name in self.allowed_tools,
                 'side_effect': self.registry.get(name).side_effect.value,
                 'step_consuming': self.registry.get(name).step_consuming}
                for name, (_, owner, _) in sorted(self.bindings.items())]

    def execute(self, name, arguments, *, step, reason=None, metadata=None, trace_args=None):
        if name not in self.bindings:
            raise ToolError('unbound tool: ' + name)
        if self.allowed_tools is not None and name not in self.allowed_tools:
            raise ToolError('tool is not allowed by this task: ' + name)
        if not isinstance(arguments, dict):
            raise ToolError('tool arguments must be a dictionary')
        spec = self.registry.get(name)
        if spec.missing_args(arguments):
            raise ToolError('missing tool arguments: ' + ', '.join(spec.missing_args(arguments)))
        handler, _, schema = self.bindings[name]
        if schema is not None:
            validate(arguments, schema)
        call = None
        if self.runtime is not None:
            call = self.runtime.call(name, step,
                args=safe_value(arguments if trace_args is None else trace_args),
                read_only=not (spec.mutates_world or spec.physical_motion), reason=reason)
        try:
            telemetry.phase('EXECUTE' if spec.mutates_world or spec.physical_motion else 'OBSERVE', step=step)
            with telemetry.span('TOOL_EXECUTION', tool_name=name,
                                call_id=call.call_id if call else None, step=step):
                outcome = handler(**arguments)
                if not isinstance(outcome, ToolOutcome):
                    raise TypeError('tool handler must return ToolOutcome: ' + name)
                if (not isinstance(outcome.consumed_steps, int) or isinstance(outcome.consumed_steps, bool)
                        or outcome.consumed_steps < 0
                        or (outcome.consumed_steps and not spec.step_consuming)):
                    raise ToolError('tool returned an invalid step count: ' + name)
        except BaseException as exc:
            if call is not None:
                self.runtime.result(call, step, ok=False, error=str(exc))
                self.runtime.violation(step, 'tool_execution_failed', str(exc), tool_name=name)
            raise
        if call is not None:
            after = step + outcome.consumed_steps
            if outcome.consumed_steps:
                self.runtime.receipt(call, step, after, mutation=name,
                                     metadata=safe_value(metadata or {}))
            result_trace = dict(outcome.trace)
            if outcome.consumed_steps:
                result_trace.setdefault('step_after', after)
            self.runtime.result(call, after, result=safe_value(result_trace))
        return outcome.value
