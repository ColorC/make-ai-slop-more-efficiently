# [OMNI] origin=codex domain=services/agent ts=2026-07-29 type=infrastructure
# [OMNI] material_id="material:core.agent.model_visible_contract.py"
"""Canonical, byte-comparable contract for everything an Agent model can see."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


MODEL_VISIBLE_CONTRACT_VERSION = "2026-07-29:v1"
OMNI_BUS_AGENT_ENGINE_ID = "omni-bus-agent:v1"


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class ModelVisibleAgentContract:
    """One runtime-independent snapshot of the canonical Agent interface.

    Runtime profile and harness labels are deliberately excluded. If two
    profiles select this same engine with the same task, workspace and model,
    ``canonical_bytes()`` must be byte-identical.
    """

    system_prompt: str
    initial_messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...]
    model: str
    max_tokens: int
    model_options: dict[str, Any]
    context_policy: dict[str, Any]
    retry_policy: dict[str, Any]
    termination_policy: dict[str, Any]
    schema_version: str = MODEL_VISIBLE_CONTRACT_VERSION
    engine_id: str = OMNI_BUS_AGENT_ENGINE_ID
    event_authority: str = "omnicompany.event_bus"

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "engine_id": self.engine_id,
            "event_authority": self.event_authority,
            "system_prompt": self.system_prompt,
            "initial_messages": list(self.initial_messages),
            "tools": list(self.tools),
            "model": self.model,
            "max_tokens": self.max_tokens,
            "model_options": self.model_options,
            "context_policy": self.context_policy,
            "retry_policy": self.retry_policy,
            "termination_policy": self.termination_policy,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.payload())

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def audit_payload(self) -> dict[str, Any]:
        """Return proof without duplicating prompt or task bodies into metadata."""

        return {
            "schema_version": self.schema_version,
            "engine_id": self.engine_id,
            "event_authority": self.event_authority,
            "digest": self.digest(),
            "canonical_bytes": len(self.canonical_bytes()),
            "system_prompt_sha256": _digest(self.system_prompt),
            "initial_messages_sha256": _digest(list(self.initial_messages)),
            "tools_sha256": _digest(list(self.tools)),
            "model_options_sha256": _digest(self.model_options),
            "context_policy_sha256": _digest(self.context_policy),
            "retry_policy_sha256": _digest(self.retry_policy),
            "termination_policy_sha256": _digest(self.termination_policy),
            "initial_message_count": len(self.initial_messages),
            "tool_count": len(self.tools),
            "model": self.model,
            "max_tokens": self.max_tokens,
        }


__all__ = [
    "MODEL_VISIBLE_CONTRACT_VERSION",
    "OMNI_BUS_AGENT_ENGINE_ID",
    "ModelVisibleAgentContract",
]
