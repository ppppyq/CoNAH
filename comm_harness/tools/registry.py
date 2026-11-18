"""Tool metadata and validation for navigation and wireless communication traces.

Each tool declares its side effects, required arguments and simulator-step
usage. Validation reports unknown tools and contract violations; the runtime
records these findings without refusing the action.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from comm_harness.tools.contracts import SideEffect


@dataclass(frozen=True)
class ToolSpec:
    name: str
    side_effect: SideEffect
    description: str = ""
    required_args: Tuple[str, ...] = ()
    # Consumes simulator steps even when the tool does not change task state.
    step_consuming: bool = False
    # Physically moves the agent even if its *information* side effect is
    # READ_ONLY. A panorama rotates the robot and produces action receipts
    # even though its information-level operation only observes the scene.
    physical_motion: bool = False
    # Requires a passed verify_stop before execution.
    requires_stop_evidence: bool = False

    @property
    def mutates_world(self) -> bool:
        return self.side_effect.mutates_world

    @property
    def mutates_network(self) -> bool:
        return self.side_effect.mutates_network

    def missing_args(self, args: Optional[Dict]) -> List[str]:
        provided = set((args or {}).keys())
        return [a for a in self.required_args if a not in provided]


@dataclass
class ToolRegistry:
    tools: Dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self.tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self.tools[spec.name] = spec
        return spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self.tools.get(name)

    def has(self, name: str) -> bool:
        return name in self.tools

    def names(self) -> List[str]:
        return sorted(self.tools)

    def names_by_side_effect(self, side_effect: SideEffect) -> List[str]:
        return sorted(n for n, s in self.tools.items() if s.side_effect == side_effect)

    def validate(self, name: str, args: Optional[Dict] = None,
                 read_only: Optional[bool] = None) -> List[Tuple[str, str]]:
        """Return a list of ``(kind, message)`` problems; empty means valid."""
        problems: List[Tuple[str, str]] = []
        spec = self.get(name)
        if spec is None:
            problems.append(('unregistered_tool', f"tool '{name}' is not in the registry"))
            return problems
        missing = spec.missing_args(args)
        if missing:
            problems.append(('missing_args', f"tool '{name}' missing args: {', '.join(missing)}"))
        if read_only is not None:
            if bool(read_only) and (spec.mutates_world or spec.mutates_network):
                problems.append((
                    'side_effect_mismatch',
                    f"tool '{name}' is {spec.side_effect.value} but was called read_only",
                ))
            if not bool(read_only) and not (spec.mutates_world or spec.mutates_network or spec.physical_motion):
                problems.append((
                    'side_effect_mismatch',
                    f"tool '{name}' is {spec.side_effect.value} but was called as a mutation",
                ))
        return problems
