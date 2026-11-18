"""Tool declarations derived from Python functions, instance methods, or explicit schemas.

Modeled on ``rpent/tools/base.py`` while remaining dependency-light (compatible
with Python 3.8+ without requiring ``pydantic_ai``) and carrying Comm-Harness
``SideEffect`` and ``ToolSpec`` metadata.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
import inspect
import json
import re
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union

from comm_harness.tools.contracts import SideEffect
from comm_harness.tools.registry import ToolSpec

MAX_TOOL_TEXT_BYTES = 60000


@dataclass
class ToolResult:
    """Planner-facing tool payload and PNG/JPEG images; errors stay in ``data['error']``."""

    data: Dict[str, Any] = field(default_factory=dict)
    images: List[bytes] = field(default_factory=list)
    consumed_steps: int = 0

    @property
    def is_error(self) -> bool:
        return self.data.get("error") is not None or self.data.get("ok") is False

    def to_dict(self) -> Dict[str, Any]:
        """Return a shallow copy of the public JSON payload."""
        return dict(self.data)

    def to_text(self) -> str:
        """Encode the tool payload as bounded JSON text for LLM/MCP consumption."""
        text = json.dumps(
            self.to_dict(), indent=2, allow_nan=False, ensure_ascii=False, default=str
        )
        if len(text) <= MAX_TOOL_TEXT_BYTES:
            return text
        suffix = "\n[truncated]"
        return text[: MAX_TOOL_TEXT_BYTES - len(suffix)] + suffix


def tool_error(code: str, message: str, **details: Any) -> ToolResult:
    """Build a structured tool error with a code, message, and optional details."""
    return ToolResult(
        data={
            "error": {"code": code, "message": message, **details},
            "ok": False,
            "success": False,
        }
    )


def _parse_google_docstring(doc: Optional[str]) -> Tuple[str, Dict[str, str]]:
    """Extract main description and per-parameter descriptions from a Google docstring."""
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).splitlines()
    summary_lines: List[str] = []
    param_descs: Dict[str, str] = {}
    in_args = False
    current_param: Optional[str] = None
    for line in lines:
        stripped = line.strip()
        if stripped in ("Args:", "Arguments:", "Parameters:"):
            in_args = True
            current_param = None
            continue
        if in_args and re.match(r"^[A-Z][A-Za-z0-9_ ]*:$", stripped):
            in_args = False
            current_param = None
            continue
        if not in_args:
            summary_lines.append(line)
        else:
            m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\([^)]*\))?\s*:\s*(.*)$", stripped)
            if m:
                current_param = m.group(1)
                param_descs[current_param] = m.group(2).strip()
            elif current_param and stripped:
                param_descs[current_param] = (param_descs[current_param] + " " + stripped).strip()
    return "\n".join(summary_lines).strip(), param_descs


def _annotation_to_json_schema(annotation: Any) -> Dict[str, Any]:
    """Convert a Python type annotation into a JSON Schema property dict."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}
    if annotation is str or annotation == "str":
        return {"type": "string"}
    if annotation is int or annotation == "int":
        return {"type": "integer"}
    if annotation is float or annotation == "float":
        return {"type": "number"}
    if annotation is bool or annotation == "bool":
        return {"type": "boolean"}
    if annotation is dict or annotation == "dict":
        return {"type": "object"}
    if annotation is list or annotation == "list":
        return {"type": "array"}
    if isinstance(annotation, str):
        s = annotation.strip()
        if s.startswith("Optional[") and s.endswith("]"):
            return _annotation_to_json_schema(s[9:-1])
        if s.startswith(("Dict[", "dict[", "Mapping[")):
            return {"type": "object"}
        if s.startswith(("List[", "list[", "Sequence[", "Tuple[", "tuple[")):
            inner = s[s.find("[") + 1 : -1].split(",")[0].strip()
            return {"type": "array", "items": _annotation_to_json_schema(inner) if inner else {}}

    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())
    # Literal[...] support
    if str(origin).endswith("Literal") and args:
        first_type = type(args[0])
        json_type = "string"
        if first_type is int:
            json_type = "integer"
        elif first_type is float:
            json_type = "number"
        elif first_type is bool:
            json_type = "boolean"
        return {"type": json_type, "enum": list(args)}
    # Optional[T] / Union[T, None]
    if origin is Union and args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _annotation_to_json_schema(non_none[0])
    if origin in (list, List, tuple, Tuple):
        item_schema = _annotation_to_json_schema(args[0]) if args else {}
        return {"type": "array", "items": item_schema}
    if origin in (dict, Dict):
        return {"type": "object"}
    return {"type": "string"}


@dataclass(frozen=True)
class Tool:
    """A callable tool and its JSON schema & side-effect contract.

    Follows RPent's ``Tool`` descriptor protocol while also converting cleanly
    to Comm-Harness's ``ToolSpec`` for ``ToolRegistry`` / ``ToolExecutor``.
    """

    name: str
    description: str
    handler: Callable[..., Any]
    _input_schema: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}, "required": [], "additionalProperties": False})
    readonly: bool = False
    side_effect: SideEffect = SideEffect.WORLD_MUTATING
    step_consuming: bool = False
    physical_motion: bool = False
    requires_stop_evidence: bool = False
    _unbound_method: bool = False

    @property
    def input_schema(self) -> Dict[str, Any]:
        """Return a deep copy of the JSON Schema for model/MCP parameters."""
        return deepcopy(self._input_schema)

    def to_spec(self) -> ToolSpec:
        """Return the ``ToolSpec`` contract for ``ToolRegistry`` and ``ToolExecutor``."""
        req = tuple(self._input_schema.get("required") or ())
        return ToolSpec(
            name=self.name,
            side_effect=self.side_effect,
            description=self.description,
            required_args=req,
            step_consuming=self.step_consuming,
            physical_motion=self.physical_motion,
            requires_stop_evidence=self.requires_stop_evidence,
        )

    def with_handler(self, handler: Callable[..., Any]) -> "Tool":
        """Bind resources or an execution guard while retaining the declaration."""
        return replace(self, handler=handler, _unbound_method=False)

    def __get__(self, instance: Any, owner: Optional[type] = None) -> "Tool":
        if instance is None:
            return self
        return replace(
            self,
            handler=self.handler.__get__(instance, owner),
            _unbound_method=False,
        )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.handler(*args, **kwargs)


def tool(
    function: Optional[Callable[..., Any]] = None,
    /,
    *,
    name: Optional[str] = None,
    readonly: bool = False,
    side_effect: Optional[SideEffect] = None,
    step_consuming: bool = False,
    physical_motion: bool = False,
    requires_stop_evidence: bool = False,
    exclude: Tuple[str, ...] = (),
    schema: Optional[Dict[str, Any]] = None,
    json_schema_extra: Optional[Dict[str, Any]] = None,
) -> Any:
    """Declare a tool from a Python function or instance method.

    Supports both RPent's ``@tool(readonly=True)`` style and Comm-Harness's
    explicit ``side_effect=SideEffect.NETWORK_TX`` / ``WORLD_MUTATING`` metadata.
    """
    if function is None:
        return lambda handler: _declare_tool(
            handler,
            tool_name=name,
            readonly=readonly,
            side_effect=side_effect,
            step_consuming=step_consuming,
            physical_motion=physical_motion,
            requires_stop_evidence=requires_stop_evidence,
            exclude=exclude,
            explicit_schema=schema,
            json_schema_extra=json_schema_extra,
        )
    return _declare_tool(
        function,
        tool_name=name,
        readonly=readonly,
        side_effect=side_effect,
        step_consuming=step_consuming,
        physical_motion=physical_motion,
        requires_stop_evidence=requires_stop_evidence,
        exclude=exclude,
        explicit_schema=schema,
        json_schema_extra=json_schema_extra,
    )


def _declare_tool(
    function: Callable[..., Any],
    *,
    tool_name: Optional[str],
    readonly: bool,
    side_effect: Optional[SideEffect],
    step_consuming: bool,
    physical_motion: bool,
    requires_stop_evidence: bool,
    exclude: Tuple[str, ...],
    explicit_schema: Optional[Dict[str, Any]],
    json_schema_extra: Optional[Dict[str, Any]],
) -> Tool:
    parameters = dict(inspect.signature(function).parameters)
    unbound_method = next(iter(parameters), None) == "self"
    if unbound_method:
        parameters.pop("self")
    for ex in exclude:
        parameters.pop(ex, None)

    summary, param_descs = _parse_google_docstring(inspect.getdoc(function))
    resolved_effect = side_effect
    if resolved_effect is None:
        resolved_effect = SideEffect.READ_ONLY if readonly else SideEffect.WORLD_MUTATING
    resolved_readonly = bool(readonly or resolved_effect.read_only)

    if explicit_schema is not None:
        input_schema = deepcopy(explicit_schema)
    else:
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for pname, param in parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            prop = _annotation_to_json_schema(param.annotation)
            if pname in param_descs:
                prop["description"] = param_descs[pname]
            if param.default is inspect.Parameter.empty:
                required.append(pname)
            else:
                prop["default"] = param.default
            properties[pname] = prop
        input_schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
    if json_schema_extra:
        input_schema.update(json_schema_extra)

    return Tool(
        name=tool_name or function.__name__,
        description=summary or (tool_name or function.__name__),
        handler=function,
        _input_schema=input_schema,
        readonly=resolved_readonly,
        side_effect=resolved_effect,
        step_consuming=step_consuming,
        physical_motion=physical_motion,
        requires_stop_evidence=requires_stop_evidence,
        _unbound_method=unbound_method,
    )


def iter_tools(*owners: Any) -> Iterator[Tool]:
    """Collect decorated tools from modules, classes, or instances in definition order."""
    for owner in owners:
        cls = owner if isinstance(owner, type) else type(owner)
        members: Dict[str, Any] = {}
        for base in reversed(cls.__mro__):
            members.update(vars(base))
        if not isinstance(owner, type):
            members.update(getattr(owner, "__dict__", {}))
        for attribute, value in members.items():
            if isinstance(value, Tool):
                yield getattr(owner, attribute)
