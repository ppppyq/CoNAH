"""Standalone command-line check for the configured LLM backend (modeled on RPent).

Backs `python -m comm_harness.cli.check_llm` and `python -m comm_harness --check-llm`.
Uses :mod:`comm_harness.planner.check` so the CLI and Web Dashboard share the exact
same connectivity probe and status classification.
"""

from __future__ import annotations

import argparse
import json
from typing import List, Optional, Tuple

from comm_harness.planner.check import (
    CHECK_PLANNERS,
    DEFAULT_TIMEOUT_S,
    STATUS_AUTH_FAILED,
    STATUS_INVALID_MODEL,
    STATUS_MISSING_API_KEY,
    STATUS_MISSING_CONFIG,
    STATUS_NETWORK_ERROR,
    STATUS_PROVIDER_ERROR,
    STATUS_SDK_ERROR,
    STATUS_UNSUPPORTED_PROVIDER,
    LlmCheckRequest,
    LlmCheckResult,
    check_llm,
)


def _parser() -> argparse.ArgumentParser:
    default_timeouts = ", ".join(
        f"{planner}: {seconds}s" for planner, seconds in DEFAULT_TIMEOUT_S.items()
    )
    parser = argparse.ArgumentParser(
        prog="comm-harness-check-llm",
        description="Check that the configured LLM backend answers a minimal request.",
    )
    parser.add_argument(
        "--planner",
        default="api",
        choices=list(CHECK_PLANNERS),
        help="LLM backend to check: api | codex.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Model id. For the 'api' planner, prefix the provider "
            "(e.g. sub2api:gpt-5.4, deepseek:deepseek-flash, ollama:qwen3.8:27b)."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="API base URL override.",
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=None,
        help=f"Wall-clock cap for the check ({default_timeouts}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the structured result as JSON instead of a report.",
    )
    return parser


def _remediation(result: LlmCheckResult) -> str:
    credential = result.credential_env or "the backend's API key"
    base_url_env = result.base_url_env or "the backend's base URL variable"
    hints = {
        STATUS_MISSING_CONFIG: (
            "Pass --model with a provider prefix, e.g. "
            "--model sub2api:gpt-5.4, --model deepseek:deepseek-flash, or --model ollama:qwen3.8:27b."
        ),
        STATUS_UNSUPPORTED_PROVIDER: (
            "Use a supported provider prefix: sub2api:, deepseek:, ollama:, openai:, or anthropic:."
        ),
        STATUS_MISSING_API_KEY: f"Set {credential} in this shell or --env-file, then retry.",
        STATUS_INVALID_MODEL: (
            f"The provider rejected the model id. Check that --model exists "
            f"for this backend and that {credential} has access to it."
        ),
        STATUS_AUTH_FAILED: (
            f"The provider rejected {credential}. Check the key and that it "
            f"matches the endpoint in {base_url_env} / --base-url."
        ),
        STATUS_NETWORK_ERROR: (
            f"Nothing answered in time. Check connectivity, proxy, and "
            f"{base_url_env} / --base-url."
        ),
        STATUS_PROVIDER_ERROR: (
            "The provider was reached but refused the request. Check the model "
            "id, quota, and rate limits."
        ),
        STATUS_SDK_ERROR: (
            "The backend SDK failed before a reply arrived. Confirm the SDK is installed."
        ),
    }
    return hints.get(result.status, "")


def _render(result: LlmCheckResult) -> str:
    rows: List[Tuple[str, str]] = [("planner", result.planner)]
    if result.model:
        rows.append(("model", result.model))
    if result.provider:
        rows.append(("provider", result.provider))
    if result.credential_env:
        state = "set" if result.credential_present else "not set"
        rows.append(("credentials", f"{result.credential_env} ({state})"))
    if result.base_url_env:
        endpoint = result.base_url or "default"
        rows.append(("endpoint", f"{endpoint} (from {result.base_url_env})"))
    elif result.base_url:
        rows.append(("endpoint", result.base_url))

    latency = f" ({result.latency_s:.2f}s)" if result.latency_s is not None else ""
    verdict = "OK" if result.ok else f"FAILED [{result.status}]"
    rows.append(("result", f"{verdict}{latency}"))
    if result.ok and result.reply:
        rows.append(("reply", result.reply))
    if not result.ok and result.detail:
        rows.append(("detail", result.detail))
        hint = _remediation(result)
        if hint:
            rows.append(("hint", hint))

    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label:<{width}} : {value}" for label, value in rows)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    result = check_llm(
        LlmCheckRequest(
            planner=args.planner,
            model=args.model,
            base_url=args.base_url,
            timeout_s=args.timeout_s,
        )
    )
    if args.as_json:
        print(json.dumps(result.as_dict(), indent=2), flush=True)
    else:
        print(_render(result), flush=True)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
