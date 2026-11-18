"""General platform contract modeled on RPent's ``RobotSpec`` while preserving ``PlatformSpec``.

Simulator and hardware implementations live outside core (under ``platforms/<name>/``)
so ``comm_harness`` remains completely platform-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PromptBundle:
    """Platform-specific system and user prompt templates."""

    system: str = ""
    user: str = ""


@dataclass(frozen=True)
class RunConfig:
    """Derived per-run identifiers for a platform session."""

    recipe_tag: str
    output_dir: Path
    prompt_vars: Dict[str, Any] = field(default_factory=dict)
    task_desc: Dict[str, Any] = field(default_factory=dict)
    enable_direct_action: bool = False


@dataclass(frozen=True)
class PlatformSpec:
    """Platform-level extension points for both single-agent and multi-robot sessions."""

    name: str
    environment_factory: Optional[Callable[..., Any]] = None
    node_factory: Optional[Callable[..., Any]] = None
    tools_factory: Optional[Callable[..., Any]] = None
    toolkit_factory: Optional[Callable[..., Any]] = None
    prompts: Optional[PromptBundle] = None
    capabilities: Tuple[str, ...] = ()
    is_real_robot: bool = False
    description: str = ""


REGISTRY: Dict[str, PlatformSpec] = {}


def register(spec: PlatformSpec, *, replace: bool = False) -> PlatformSpec:
    if spec.name in REGISTRY and not replace:
        raise ValueError("duplicate platform: " + spec.name)
    REGISTRY[spec.name] = spec
    return spec


def get_platform(name: str) -> PlatformSpec:
    if name not in REGISTRY:
        raise ValueError("unknown platform: " + name)
    return REGISTRY[name]


def list_platforms() -> List[str]:
    return sorted(REGISTRY.keys())
