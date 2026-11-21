# [OMNI] origin=codex created_by=codex intent=read-only-observatory-agent-runtime
"""Shared Codex Agent runtime for observatory semantic work.

The observatory never calls a model as a prompt/response function.  Every
semantic operation runs through the existing audited Codex external-worker
facility so the Agent can inspect authoritative material with read-only tools.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentResult,
    ExternalAgentRunSpec,
)
from omnicompany.packages.services._core.agent.external_workers.codex import (
    CodexExecWorker,
)


async def run_codex_agent(spec: ExternalAgentRunSpec) -> ExternalAgentResult:
    """Run one complete audited Codex Agent session."""

    return await CodexExecWorker().run(spec)


def run_codex_agent_sync(spec: ExternalAgentRunSpec) -> ExternalAgentResult:
    """Synchronous bridge for the currently synchronous evaluation service."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_codex_agent(spec))
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(run_codex_agent(spec))).result()


def the_company_codex_config_overrides(
    model_catalog_path: str | Path | None = None,
) -> dict[str, str]:
    """Build process-local Codex overrides without changing global config."""

    quote = lambda value: json.dumps(str(value), ensure_ascii=False)  # noqa: E731
    overrides = {
        "model_provider": quote("the_company"),
        "model_providers.the_company.name": quote("the_company proxy"),
        "model_providers.the_company.base_url": quote(
            "https://internal-llm-proxy.example.com/v1"
        ),
        "model_providers.the_company.env_key": quote("THE_COMPANY_API_KEY"),
        "model_providers.the_company.wire_api": quote("responses"),
        "model_providers.the_company.supports_websockets": "false",
    }
    if model_catalog_path:
        catalog = Path(model_catalog_path).expanduser().resolve().as_posix()
        overrides["model_catalog_json"] = quote(catalog)
    return overrides


def agent_usage(result: ExternalAgentResult) -> dict[str, int]:
    """Read normalized token usage from the Codex Agent event stream."""

    usage: dict[str, Any] = {}
    for event in result.events:
        if event.type == "turn.completed" and isinstance(
            event.payload.get("usage"), dict
        ):
            usage = event.payload["usage"]
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(
            usage.get("cached_input_tokens")
            or usage.get("cache_read_input_tokens")
            or 0
        ),
        "cache_creation_tokens": int(usage.get("cache_creation_input_tokens") or 0),
    }


def agent_tool_calls(result: ExternalAgentResult) -> list[dict[str, str]]:
    """Extract actual Codex tool calls, excluding the outer process launch."""

    calls: list[dict[str, str]] = []
    for event in result.events:
        item = event.payload.get("item")
        if event.type != "item.started" or not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type not in {"command_execution", "file_change"}:
            continue
        calls.append(
            {
                "type": item_type,
                "command": str(item.get("command") or ""),
            }
        )
    return calls


__all__ = [
    "agent_tool_calls",
    "agent_usage",
    "the_company_codex_config_overrides",
    "run_codex_agent",
    "run_codex_agent_sync",
]
