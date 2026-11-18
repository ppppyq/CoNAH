"""Unified agent Toolkit combining RPent's scheduler/lifecycle with Comm-Harness's ToolExecutor & HarnessRuntime.

Supports:
1. RPent Planner & Dashboard contract:
   - ``add_tool(Tool)``, ``add_tools(Iterable[Tool])``, ``list_tools() -> tuple[Tool, ...]``
   - ``execute_tool(name, input_dict) -> ToolResult``
   - ``cancel_active_and_wait()``, ``resume_calls()``, ``raise_if_cancelled()``, ``close()``
2. Comm-Harness Runtime & Executor contract:
   - ``self.registry`` (``ToolRegistry``) and ``self.runtime`` (``HarnessRuntime``)
   - Automatic ``HARNESS_TOOL_CALL``, ``HARNESS_RECEIPT``, and ``HARNESS_TOOL_RESULT`` tracing
3. Backward-compatible ``Toolkit(emit)`` contract:
   - ``add(name, description, properties, required, handler)``
   - ``schemas()`` (OpenAI function-calling format)
   - ``execute(name, arguments)`` -> ``{'ok': bool, 'data' | 'error': ...}``
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import math
import threading
import time
import traceback
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from comm_harness.tools.base import Tool, ToolResult, iter_tools
from comm_harness.tools.contracts import SideEffect, safe_value
from comm_harness.tools.registry import ToolRegistry
from comm_harness.tools.runtime import HarnessRuntime


class ToolError(ValueError):
    """Raised when tool arguments or execution preconditions fail."""


class ToolCancelled(Exception):
    """Raised when an environment or toolkit reaches a safe cancellation boundary."""


def validate(value: Any, schema: Dict[str, Any], path: str = "arguments") -> None:
    """Validate tool arguments against a JSON Schema subset."""
    kind = schema.get("type")
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
    }
    if kind:
        if isinstance(kind, list):
            non_null = [k for k in kind if k != "null"]
            if value is None and "null" in kind:
                return
            if len(non_null) == 1:
                kind = non_null[0]
        if isinstance(kind, str) and kind in types:
            if not isinstance(value, types[kind]) or (
                kind in ("number", "integer") and isinstance(value, bool)
            ):
                raise ToolError(path + ": expected " + kind)
    if kind == "number" and isinstance(value, (int, float)) and not math.isfinite(value):
        raise ToolError(path + ": non-finite number")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(path + ": value outside enum")
    if kind == "object" and isinstance(value, dict):
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - set(value):
            raise ToolError(path + ": missing required fields")
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ToolError(path + ": unexpected fields")
        for key, item in value.items():
            if key in props:
                validate(item, props[key], path + "." + key)
    elif kind == "array" and isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10000):
            raise ToolError(path + ": invalid array length")
        for item in value:
            validate(item, schema.get("items", {}), path + "[]")
    elif kind == "string" and isinstance(value, str) and len(value) > schema.get("maxLength", 4096):
        raise ToolError(path + ": string too long")
    if kind in ("number", "integer") and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", -float("inf")) or value > schema.get("maximum", float("inf")):
            raise ToolError(path + ": out of range")


@dataclass(eq=False)
class _ToolOperation:
    readonly: bool
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done: bool = False


class _Scheduler:
    """Share readonly calls; run queued exclusive calls in arrival order."""

    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.pending: List[_ToolOperation] = []
        self.active: Set[_ToolOperation] = set()
        self.paused = False
        self.closed = False

    def acquire(self, *, readonly: bool) -> _ToolOperation:
        with self.condition:
            if self.closed:
                raise ToolCancelled("Toolkit is closed.")
            if self.paused:
                raise ToolCancelled("Tool calls are paused.")
            call = _ToolOperation(readonly=readonly)
            self.pending.append(call)
            self.condition.notify_all()
            try:
                while True:
                    if call.cancel_event.is_set():
                        raise ToolCancelled("tool operation interrupted")
                    exclusive = next(
                        (item for item in self.pending if not item.readonly), None
                    )
                    if (
                        readonly
                        and exclusive is None
                        and all(item.readonly for item in self.active)
                    ) or (not readonly and not self.active and call is exclusive):
                        self.pending.remove(call)
                        self.active.add(call)
                        return call
                    self.condition.wait()
            except BaseException:
                if call in self.pending:
                    self.pending.remove(call)
                call.done = True
                self.condition.notify_all()
                raise

    def release(self, call: _ToolOperation) -> None:
        with self.condition:
            self.active.discard(call)
            call.done = True
            self.condition.notify_all()

    def cancel(self, *, close: bool = False) -> List[_ToolOperation]:
        with self.condition:
            self.paused = True
            self.closed |= close
            calls = [*self.pending, *self.active]
            for call in calls:
                call.cancel_event.set()
            self.condition.notify_all()
            return calls

    def wait(self, calls: List[_ToolOperation]) -> None:
        with self.condition:
            self.condition.wait_for(lambda: all(call.done for call in calls))

    def resume(self) -> None:
        with self.condition:
            if self.active or self.pending:
                raise RuntimeError("Wait for tool cleanup before resuming.")
            if not self.closed:
                self.paused = False


class Toolkit:
    """Agent-facing tool container unifying RPent's Toolkit with Comm-Harness's ToolRegistry & HarnessRuntime."""

    def __init__(
        self,
        emit: Callable[..., Any] = lambda *a, **k: None,
        *,
        dashboard_events: Optional[Any] = None,
        state: Optional[Any] = None,
        memory: Optional[Any] = None,
        runtime: Optional[HarnessRuntime] = None,
        registry: Optional[ToolRegistry] = None,
        allowed_tools: Optional[Iterable[str]] = None,
        step_fn: Optional[Callable[[], int]] = None,
    ) -> None:
        self.emit = emit
        self.entries: Dict[str, Tuple[str, Dict[str, Any], Callable[..., Any]]] = {}
        self._tools: Dict[str, Tool] = {}
        self._dashboard_events = dashboard_events
        self._state = state
        self._memory = memory
        self.registry = registry if registry is not None else ToolRegistry()
        self.runtime = runtime
        self.allowed_tools = None if allowed_tools is None else frozenset(allowed_tools)
        self._step_fn = step_fn
        self._step_counter = 0
        self._scheduler = _Scheduler()

    # ------------------------------------------------------------------
    # Registration (both RPent Tool objects and legacy .add())
    # ------------------------------------------------------------------

    def add_tool(self, tool_obj: Tool, *, replace_existing: bool = False) -> None:
        """Register a bound ``Tool`` declaration and sync it with ``ToolRegistry``."""
        if tool_obj._unbound_method:
            raise TypeError("Register an instance's tool method, not an unbound method")
        if tool_obj.name in self._tools and not replace_existing:
            raise ValueError(f"Tool already registered: {tool_obj.name}")
        self._tools[tool_obj.name] = tool_obj
        schema = tool_obj.input_schema
        self.entries[tool_obj.name] = (tool_obj.description, schema, tool_obj.handler)
        spec = tool_obj.to_spec()
        if not self.registry.has(spec.name):
            self.registry.register(spec)
        elif replace_existing:
            self.registry.tools[spec.name] = spec

    def add_tools(self, tools: Iterable[Tool], *, replace_existing: bool = False) -> None:
        """Register multiple ``Tool`` declarations."""
        for t in tools:
            self.add_tool(t, replace_existing=replace_existing)

    def add(
        self,
        name: str,
        description: str,
        properties: Dict[str, Any],
        required: List[str],
        handler: Callable[..., Any],
        *,
        side_effect: SideEffect = SideEffect.WORLD_MUTATING,
        step_consuming: bool = False,
        physical_motion: bool = False,
    ) -> Tool:
        """Register a tool using explicit JSON Schema properties (backward-compatible)."""
        schema = {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        }
        tool_obj = Tool(
            name=name,
            description=description,
            handler=handler,
            _input_schema=schema,
            readonly=side_effect.read_only,
            side_effect=side_effect,
            step_consuming=step_consuming,
            physical_motion=physical_motion,
        )
        self.add_tool(tool_obj, replace_existing=True)
        return tool_obj

    # ------------------------------------------------------------------
    # Schema & Discovery API
    # ------------------------------------------------------------------

    @property
    def memory(self) -> Any:
        return self._memory

    @property
    def state(self) -> Any:
        if self._state is None:
            raise RuntimeError("toolkit has no environment state")
        return self._state

    def list_tools(self) -> Tuple[Tool, ...]:
        """Return registered ``Tool`` declarations filtered by ``allowed_tools``."""
        return tuple(
            t
            for t in self._tools.values()
            if self.allowed_tools is None or t.name in self.allowed_tools
        )

    def schemas(self) -> List[Dict[str, Any]]:
        """Return OpenAI function-calling tool schemas."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self.list_tools()
        ]

    # ------------------------------------------------------------------
    # Execution API (RPent execute_tool + legacy execute)
    # ------------------------------------------------------------------

    def _current_step(self) -> int:
        if self._step_fn is not None:
            try:
                return int(self._step_fn())
            except Exception:
                pass
        state_steps = 0
        if self._state is not None and hasattr(self._state, "_steps"):
            state_steps = len(self._state._steps)
        return max(self._step_counter, state_steps)

    def execute_tool(self, name: str, input_dict: Dict[str, Any]) -> ToolResult:
        """Validate one call, execute it through the scheduler & runtime, and capture state."""
        tool_obj = self._tools.get(name)
        if tool_obj is None:
            return ToolResult(data={"error": f"unknown tool: {name}", "ok": False})
        if self.allowed_tools is not None and name not in self.allowed_tools:
            return ToolResult(
                data={"error": f"tool is not allowed by this task: {name}", "ok": False}
            )
        if not isinstance(input_dict, dict):
            return ToolResult(
                data={"error": f"bad arguments for {name}: expected object", "ok": False}
            )

        schema = tool_obj.input_schema
        try:
            validate(input_dict, schema)
        except ToolError as exc:
            return ToolResult(
                data={"error": f"bad arguments for {name}: {exc}", "ok": False}
            )

        # Fill default values from schema properties if omitted
        kwargs = dict(input_dict)
        for prop_name, prop_schema in (schema.get("properties") or {}).items():
            if prop_name not in kwargs and "default" in prop_schema:
                kwargs[prop_name] = prop_schema["default"]

        try:
            operation = self._scheduler.acquire(readonly=tool_obj.readonly)
        except ToolCancelled as exc:
            return ToolResult(
                data={"error": str(exc), "code": "tool_cancelled", "interrupted": True, "ok": False}
            )

        step_before = self._current_step()
        call_record = None
        if self.runtime is not None:
            call_record = self.runtime.call(
                name,
                step_before,
                args=safe_value(kwargs),
                read_only=tool_obj.readonly,
            )

        try:
            started = time.perf_counter()
            try:
                self.raise_if_cancelled()
                raw = tool_obj(**kwargs)
                if isinstance(raw, ToolResult):
                    native = raw
                elif isinstance(raw, dict):
                    native = ToolResult(data=dict(raw))
                else:
                    native = ToolResult(data={"result": raw})
            except ToolCancelled as exc:
                native = ToolResult(
                    data={
                        "error": str(exc),
                        "code": "tool_cancelled",
                        "interrupted": True,
                        "ok": False,
                    }
                )
            except (ToolError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                native = ToolResult(data={"error": str(exc)[:500], "ok": False})
            except Exception as exc:
                native = ToolResult(
                    data={"error": str(exc)[:500], "traceback": traceback.format_exc(), "ok": False}
                )

            elapsed_s = round(time.perf_counter() - started, 2)
            consumed = int(native.consumed_steps or (1 if tool_obj.step_consuming and not native.is_error else 0))
            step_after = step_before + consumed
            if consumed > 0:
                self._step_counter = max(self._step_counter, step_after)

            if not tool_obj.readonly and self._state is not None:
                command = {"action": name, **input_dict}
                try:
                    observed = self.get_env_state(
                        command=command,
                        result=native.to_dict(),
                        elapsed_s=elapsed_s,
                    )
                except NotImplementedError:
                    observed = None
                except Exception as exc:
                    native.data["state_capture_error"] = str(exc)
                    observed = None
                if observed is not None:
                    record = (
                        self._state.latest_record()
                        if hasattr(self._state, "latest_record")
                        else None
                    )
                    if native.is_error:
                        for key, val in native.data.items():
                            observed.data.setdefault(key, val)
                        observed.data["error"] = native.data["error"]
                    native = ToolResult(
                        data=observed.data,
                        images=list(native.images) + list(observed.images),
                        consumed_steps=consumed,
                    )
                    if record is not None:
                        self._publish_step(record)

            if call_record is not None and self.runtime is not None:
                if not native.is_error and (
                    consumed > 0
                    or tool_obj.side_effect.mutates_world
                    or tool_obj.side_effect.mutates_network
                ):
                    self.runtime.receipt(
                        call_record,
                        step_before,
                        step_after,
                        mutation=name,
                        metadata=safe_value({"elapsed_s": elapsed_s}),
                    )
                self.runtime.result(
                    call_record,
                    step_after,
                    ok=not native.is_error,
                    result=safe_value(native.to_dict()),
                    error=str(native.data.get("error")) if native.is_error else None,
                )

            return native
        finally:
            self._scheduler.release(operation)

    def execute(self, name: str, arguments: Any) -> Dict[str, Any]:
        """Execute a tool and return ``{'ok': bool, 'data' | 'error': ...}`` (legacy API)."""
        parsed_args = arguments
        try:
            if name not in self.entries:
                raise ToolError("unknown tool: " + name)
            if isinstance(arguments, str):
                parsed_args = json.loads(arguments)
            if not isinstance(parsed_args, dict):
                raise ToolError("arguments: expected object")
            _, schema, handler = self.entries[name]
            validate(parsed_args, schema)
            step_before = self._current_step()
            call_record = None
            tool_obj = self._tools.get(name)
            if self.runtime is not None:
                call_record = self.runtime.call(
                    name,
                    step_before,
                    args=safe_value(parsed_args),
                    read_only=tool_obj.readonly if tool_obj else False,
                )
            data = handler(**parsed_args)
            if isinstance(data, ToolResult):
                if data.is_error:
                    raise ToolError(str(data.data.get("error", "tool failed")))
                data = data.to_dict()
            result: Dict[str, Any] = {"ok": True, "data": data}
            if call_record is not None and self.runtime is not None:
                step_after = step_before + (1 if (tool_obj and tool_obj.step_consuming) else 0)
                if tool_obj and (
                    tool_obj.step_consuming
                    or tool_obj.side_effect.mutates_world
                    or tool_obj.side_effect.mutates_network
                ):
                    self.runtime.receipt(
                        call_record,
                        step_before,
                        step_after,
                        mutation=name,
                        metadata=safe_value(data if isinstance(data, dict) else {}),
                    )
                self.runtime.result(call_record, step_after, ok=True, result=safe_value(result))
        except (ToolError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            result = {"ok": False, "error": str(exc)[:500]}
            if "call_record" in locals() and call_record is not None and self.runtime is not None:
                self.runtime.result(
                    call_record, step_before, ok=False, error=result["error"]
                )
        self.emit("tool_receipt", tool=name, arguments=parsed_args, result=result)
        return result

    def _publish_step(self, record: Any) -> None:
        if self._dashboard_events is not None:
            from comm_harness.dashboard.events import StepRecordEvent

            self._dashboard_events.emit(
                StepRecordEvent(record=record, env_state=self._state)
            )

    def get_env_state(
        self,
        *,
        command: Dict[str, Any],
        result: Dict[str, Any],
        elapsed_s: float,
    ) -> Optional[ToolResult]:
        """Capture and return the observation produced by a stateful tool."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Lifecycle & Cancellation Hooks
    # ------------------------------------------------------------------

    def cancel_active_and_wait(self) -> None:
        """Pause admission, cancel queued and active calls, and wait for cleanup."""
        self._scheduler.wait(self._scheduler.cancel())

    def resume_calls(self) -> None:
        """Reopen admission after interrupted calls and their workers have drained."""
        self._scheduler.resume()

    def raise_if_cancelled(self) -> None:
        """Raise at an environment-defined safe cancellation boundary."""
        with self._scheduler.condition:
            if any(call.cancel_event.is_set() for call in self._scheduler.active):
                raise ToolCancelled("tool operation interrupted")

    def close(self) -> None:
        """Close admission and drain calls before subclasses release resources."""
        self._scheduler.cancel(close=True)
        self.cancel_active_and_wait()

    def exploration_continuation(self, *, explicit: bool = False) -> Optional[str]:
        return None

    def solved(self) -> bool:
        """Whether the agent/environment has finished the episode."""
        return False

    def evaluation_metrics(self, *, success: Optional[bool] = None) -> Dict[str, Any]:
        """Collect post-run evaluator metrics (kept strictly outside the Harness loop)."""
        return {}
