"""Connectivity check for the configured planner backend (`api` or `codex`).

Modeled after RPent's ``rpent/planner/check.py``. Sends the smallest real
request each backend supports — no tools, no images, no simulator runtime —
and classifies the outcome into a machine-readable status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import time
from typing import Any, Dict, Optional

import requests

from comm_harness.planner.base import (
    ModelError,
    resolve_api_endpoint,
)
from comm_harness.planner.api_loop import ApiAgentLoop

STATUS_OK = "ok"
STATUS_MISSING_CONFIG = "missing_config"
STATUS_UNSUPPORTED_PROVIDER = "unsupported_provider"
STATUS_MISSING_API_KEY = "missing_api_key"
STATUS_AUTH_FAILED = "auth_failed"
STATUS_INVALID_MODEL = "invalid_model"
STATUS_NETWORK_ERROR = "network_error"
STATUS_PROVIDER_ERROR = "provider_error"
STATUS_SDK_ERROR = "sdk_error"

CHECK_STATUSES = (
    STATUS_OK,
    STATUS_MISSING_CONFIG,
    STATUS_UNSUPPORTED_PROVIDER,
    STATUS_MISSING_API_KEY,
    STATUS_AUTH_FAILED,
    STATUS_INVALID_MODEL,
    STATUS_NETWORK_ERROR,
    STATUS_PROVIDER_ERROR,
    STATUS_SDK_ERROR,
)

CHECK_PLANNERS = ("api", "codex")
DEFAULT_TIMEOUT_S = {"api": 30, "codex": 90}
PROBE_PROMPT = "Reply with the single word: ok"

_SECRET_ENV_NAMES = (
    "DEEPSEEK_API_KEY",
    "SUB2API_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CODEX_API_KEY",
    "MODEL_API_KEY",
    "LLM_API_KEY",
    "COMM_HARNESS_CODEX_PROVIDER_KEY",
)
_MIN_SECRET_LEN = 8


def redact_secrets(text: str) -> str:
    """Replace any configured credential value with ``<VAR_NAME>``."""
    for env_name in _SECRET_ENV_NAMES:
        value = os.environ.get(env_name)
        if value and len(value) >= _MIN_SECRET_LEN and value in text:
            text = text.replace(value, f"<{env_name}>")
    return text


@dataclass(frozen=True)
class LlmCheckRequest:
    """One connectivity check to perform."""

    planner: str = "api"
    model: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    timeout_s: Optional[int] = None

    def resolved_timeout_s(self) -> int:
        if self.timeout_s is not None:
            return int(self.timeout_s)
        return DEFAULT_TIMEOUT_S.get(self.planner, 30)


@dataclass(frozen=True)
class LlmCheckResult:
    """The structured outcome of one planner connectivity check."""

    ok: bool
    status: str
    planner: str
    model: Optional[str] = None
    provider: Optional[str] = None
    credential_env: Optional[str] = None
    credential_present: Optional[bool] = None
    base_url: Optional[str] = None
    base_url_env: Optional[str] = None
    detail: str = ""
    reply: str = ""
    latency_s: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": self.ok,
            "status": self.status,
            "planner": self.planner,
            "model": self.model,
            "provider": self.provider,
            "credential_env": self.credential_env,
            "credential_present": self.credential_present,
            "base_url": self.base_url,
            "base_url_env": self.base_url_env,
            "detail": self.detail,
            "reply": self.reply,
            "latency_s": self.latency_s,
        }
        if self.extra:
            payload.update(self.extra)
        return payload


def check_llm(request: LlmCheckRequest) -> LlmCheckResult:
    """Probe the configured planner backend and classify the outcome without raising."""
    planner = (request.planner or "api").strip().lower()
    if planner not in CHECK_PLANNERS:
        return LlmCheckResult(
            ok=False,
            status=STATUS_MISSING_CONFIG,
            planner=planner,
            model=request.model,
            detail=f"unknown planner {planner!r}; expected one of {', '.join(CHECK_PLANNERS)}",
        )
    try:
        if planner == "api":
            return _check_api(request)
        return _check_codex(request)
    except Exception as exc:
        return LlmCheckResult(
            ok=False,
            status=STATUS_SDK_ERROR,
            planner=planner,
            model=request.model,
            detail=redact_secrets(f"{type(exc).__name__}: {exc}"),
        )


def _check_api(request: LlmCheckRequest) -> LlmCheckResult:
    if not (request.model or "").strip():
        return LlmCheckResult(
            ok=False,
            status=STATUS_MISSING_CONFIG,
            planner="api",
            detail="the 'api' planner requires --model (e.g. 'deepseek:deepseek-flash', 'sub2api:gpt-5.4', 'ollama:qwen3.8:27b').",
        )
    try:
        endpoint = resolve_api_endpoint(
            request.model,
            provider=request.provider,
            base_url=request.base_url,
            api_key=request.api_key,
        )
    except ValueError as exc:
        msg = str(exc)
        status = STATUS_UNSUPPORTED_PROVIDER if "provider" in msg.lower() else STATUS_MISSING_CONFIG
        return LlmCheckResult(
            ok=False,
            status=status,
            planner="api",
            model=request.model,
            detail=redact_secrets(msg),
        )

    key_present = bool(endpoint.api_key) if endpoint.provider != "ollama" else True
    if not key_present:
        return LlmCheckResult(
            ok=False,
            status=STATUS_MISSING_API_KEY,
            planner="api",
            model=endpoint.model,
            provider=endpoint.provider,
            credential_env=endpoint.credential_env,
            credential_present=False,
            base_url=endpoint.base_url,
            base_url_env=endpoint.base_url_env,
            detail=f"{endpoint.credential_env or 'API_KEY'} is not set for provider {endpoint.provider!r}.",
        )

    started = time.monotonic()
    loop = ApiAgentLoop(
        endpoint=endpoint,
        max_tokens=64,
        timeout_s=float(request.resolved_timeout_s()),
    )
    try:
        msg = loop.request(
            [{"role": "user", "content": PROBE_PROMPT}],
            tools=None,
            purpose="check_llm",
        )
    except ModelError as exc:
        latency = round(time.monotonic() - started, 3)
        text = redact_secrets(str(exc))
        status = STATUS_PROVIDER_ERROR
        if "model_http_401" in text or "model_http_403" in text:
            status = STATUS_AUTH_FAILED
        elif "model_http_400" in text:
            status = STATUS_INVALID_MODEL
        elif "network_error" in text or "timeout" in text.lower():
            status = STATUS_NETWORK_ERROR
        return LlmCheckResult(
            ok=False,
            status=status,
            planner="api",
            model=endpoint.model,
            provider=endpoint.provider,
            credential_env=endpoint.credential_env,
            credential_present=key_present,
            base_url=endpoint.base_url,
            base_url_env=endpoint.base_url_env,
            detail=text,
            latency_s=latency,
        )

    latency = round(time.monotonic() - started, 3)
    reply = str(msg.get("content") or "").strip()
    if not reply:
        return LlmCheckResult(
            ok=False,
            status=STATUS_PROVIDER_ERROR,
            planner="api",
            model=endpoint.model,
            provider=endpoint.provider,
            credential_env=endpoint.credential_env,
            credential_present=key_present,
            base_url=endpoint.base_url,
            base_url_env=endpoint.base_url_env,
            detail="the provider returned an empty response.",
            latency_s=latency,
        )
    return LlmCheckResult(
        ok=True,
        status=STATUS_OK,
        planner="api",
        model=endpoint.model,
        provider=endpoint.provider,
        credential_env=endpoint.credential_env,
        credential_present=key_present,
        base_url=endpoint.base_url,
        base_url_env=endpoint.base_url_env,
        reply=reply,
        latency_s=latency,
    )


def _check_codex(request: LlmCheckRequest) -> LlmCheckResult:
    base_url = request.base_url or os.environ.get("CODEX_BASE_URL") or os.environ.get("SUB2API_BASE_URL")
    api_key = request.api_key or os.environ.get("CODEX_API_KEY") or os.environ.get("SUB2API_API_KEY")
    key_present = bool(api_key)
    try:
        import openai_codex  # noqa: F401
    except ImportError as exc:
        return LlmCheckResult(
            ok=False,
            status=STATUS_SDK_ERROR,
            planner="codex",
            model=request.model,
            credential_env="CODEX_API_KEY",
            credential_present=key_present,
            base_url=base_url,
            base_url_env="CODEX_BASE_URL",
            detail=redact_secrets(f"ImportError: {exc}"),
        )

    return LlmCheckResult(
        ok=True,
        status=STATUS_OK,
        planner="codex",
        model=request.model,
        credential_env="CODEX_API_KEY",
        credential_present=key_present,
        base_url=base_url,
        base_url_env="CODEX_BASE_URL",
        reply="codex_sdk_available",
        latency_s=0.0,
    )
