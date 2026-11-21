# [OMNI] origin=codex domain=services/agent ts=2026-07-29 type=infrastructure
# [OMNI] material_id="material:core.agent.external_worker.runtime_contract.py"
"""Canonical context, capability, and harness identity for Agent runtimes.

External compatibility harnesses execute work; they do not own Omnicompany's
context or permission semantics. Pi is a pinned behavior specification for the
single native EventBus Agent, never another runtime consuming this contract.
This module deliberately contains no CLI-specific flags.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


RUNTIME_CONTRACT_VERSION = "2026-07-29:v1"


class AgentRuntimeProfile(str, Enum):
    """Explicit runtime lanes. Selection never silently falls back."""

    STABLE_PI = "stable_pi"
    NATIVE_BUS = "native_bus"
    COMPAT = "compat"


@dataclass(frozen=True)
class AgentContextEnvelope:
    """Small, provenance-bearing context selected outside the harness.

    ``inline_context`` is a bounded compatibility carrier for already
    materialized text. Large material stays external and is referenced through
    ``context_refs`` or ``artifact_refs``.
    """

    schema_version: str = RUNTIME_CONTRACT_VERSION
    session_identity: str = ""
    plan_dir: str = ""
    entry_files: tuple[str, ...] = ()
    context_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    checkpoint_ref: str = ""
    inline_context: tuple[str, ...] = ()
    max_inline_bytes: int = 64 * 1024
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_inline_bytes <= 0:
            raise ValueError("context max_inline_bytes must be positive")
        _reject_blank_items("entry_files", self.entry_files)
        _reject_blank_items("context_refs", self.context_refs)
        _reject_blank_items("artifact_refs", self.artifact_refs)
        _reject_blank_items("provenance", self.provenance)
        total = sum(len(item.encode("utf-8")) for item in self.inline_context)
        if total > self.max_inline_bytes:
            raise ValueError(
                "materialized context exceeds max_inline_bytes: "
                f"{total} > {self.max_inline_bytes}"
            )

    @classmethod
    def from_legacy(
        cls,
        attached_context: Iterable[str],
        *,
        provenance: Iterable[str] = ("legacy:attached_context",),
    ) -> "AgentContextEnvelope":
        """Convert the old string list at the boundary, not inside adapters."""

        return cls(
            inline_context=tuple(str(item) for item in attached_context),
            provenance=tuple(str(item) for item in provenance),
        )

    def materialized_text(self) -> str:
        return "\n\n".join(self.inline_context)

    def digest(self) -> str:
        encoded = json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def audit_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_identity": self.session_identity,
            "plan_dir": self.plan_dir,
            "entry_files": list(self.entry_files),
            "context_refs": list(self.context_refs),
            "artifact_refs": list(self.artifact_refs),
            "checkpoint_ref": self.checkpoint_ref or None,
            "inline_context_count": len(self.inline_context),
            "inline_context_bytes": sum(
                len(item.encode("utf-8")) for item in self.inline_context
            ),
            "provenance": list(self.provenance),
            "digest": self.digest(),
        }


@dataclass(frozen=True)
class AgentCapabilityGrant:
    """Fail-closed tool and workspace authority shared by every harness."""

    schema_version: str = RUNTIME_CONTRACT_VERSION
    grant_id: str = ""
    policy_ref: str = ""
    permission_mode: str = "readonly"
    allowed_tools: tuple[str, ...] = ("read",)
    allowed_read_roots: tuple[str, ...] = ()
    allowed_write_paths: tuple[str, ...] = ()
    allowed_write_roots: tuple[str, ...] = ()
    allowed_shell_roots: tuple[str, ...] = ()
    allowed_shell_command_prefixes: tuple[str, ...] = ()
    network_access: bool = False

    @classmethod
    def for_workspace(
        cls,
        cwd: Path | str,
        *,
        permission_mode: str,
        grant_id: str = "",
        policy_ref: str = "",
        allowed_shell_command_prefixes: Iterable[str] = (),
    ) -> "AgentCapabilityGrant":
        root = str(Path(cwd).expanduser().resolve())
        raw_prefixes = allowed_shell_command_prefixes
        prefixes = (
            (str(raw_prefixes),)
            if isinstance(raw_prefixes, str)
            else tuple(str(item) for item in raw_prefixes)
        )
        if permission_mode == "readonly":
            return cls(
                grant_id=grant_id,
                policy_ref=policy_ref,
                permission_mode=permission_mode,
                allowed_tools=("read",),
                allowed_read_roots=(root,),
            )
        if permission_mode == "workspace-write":
            return cls(
                grant_id=grant_id,
                policy_ref=policy_ref,
                permission_mode=permission_mode,
                allowed_tools=(
                    "read",
                    "bash",
                    "edit",
                    "write",
                ),
                allowed_read_roots=(root,),
                allowed_write_roots=(root,),
                allowed_shell_roots=(root,),
                allowed_shell_command_prefixes=prefixes,
            )
        raise ValueError(
            "canonical capability grant supports readonly or workspace-write; "
            "trusted-bypass must stay provider-specific and explicitly reviewed"
        )

    def validate(self, cwd: Path | str) -> None:
        root = Path(cwd).expanduser().resolve()
        if self.permission_mode not in {"readonly", "workspace-write"}:
            raise ValueError(
                "capability permission_mode must be readonly or workspace-write"
            )
        if not self.grant_id.strip():
            raise ValueError("capability grant_id is required")
        if not self.policy_ref.strip():
            raise ValueError("capability policy_ref is required")
        if not self.allowed_tools:
            raise ValueError("capability allowed_tools cannot be empty")
        _reject_blank_items("allowed_tools", self.allowed_tools)
        for label, values in (
            ("allowed_read_roots", self.allowed_read_roots),
            ("allowed_write_paths", self.allowed_write_paths),
            ("allowed_write_roots", self.allowed_write_roots),
            ("allowed_shell_roots", self.allowed_shell_roots),
        ):
            for item in values:
                path = Path(item).expanduser().resolve()
                try:
                    path.relative_to(root)
                except ValueError as exc:
                    raise ValueError(f"{label} must stay under cwd: {path}") from exc
        if not self.allowed_read_roots:
            raise ValueError("capability allowed_read_roots cannot be empty")
        if self.permission_mode == "readonly":
            if self.allowed_write_paths or self.allowed_write_roots or self.allowed_shell_roots:
                raise ValueError(
                    "readonly capability grant cannot contain write or shell authority"
                )
            forbidden = {"write", "edit", "bash", "powershell"}
            overlap = forbidden.intersection(self.allowed_tools)
            if overlap:
                raise ValueError(
                    "readonly capability grant contains mutating tools: "
                    + ", ".join(sorted(overlap))
                )

    def audit_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_tools"] = list(self.allowed_tools)
        payload["allowed_read_roots"] = list(self.allowed_read_roots)
        payload["allowed_write_paths"] = list(self.allowed_write_paths)
        payload["allowed_write_roots"] = list(self.allowed_write_roots)
        payload["allowed_shell_roots"] = list(self.allowed_shell_roots)
        payload["allowed_shell_command_prefixes"] = list(
            self.allowed_shell_command_prefixes
        )
        return payload


@dataclass(frozen=True)
class HarnessCapabilities:
    """Feature declaration used for routing and fail-closed compatibility."""

    harness_id: str
    rpc: bool = False
    native_event_stream: bool = False
    native_session_persistence: bool = False
    native_compaction: bool = False
    native_sandbox: bool = False
    omni_tool_bridge: bool = False
    images: bool = False
    structured_output: bool = False
    terminal_event: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)


HARNESS_CAPABILITIES: dict[str, HarnessCapabilities] = {
    "omni-native": HarnessCapabilities(
        harness_id="omni-native",
        native_event_stream=True,
        native_session_persistence=True,
        native_compaction=True,
        native_sandbox=False,
        omni_tool_bridge=True,
        terminal_event="agent_settled",
        notes=("Pi 0.82.1 is the behavior reference, not a runtime dependency",),
    ),
    "opencode": HarnessCapabilities(
        harness_id="opencode",
        native_event_stream=True,
        native_session_persistence=True,
        native_sandbox=False,
        terminal_event="process_exit",
    ),
    "codex": HarnessCapabilities(
        harness_id="codex",
        native_event_stream=True,
        native_sandbox=True,
        images=True,
        structured_output=True,
        terminal_event="process_exit",
    ),
    "claude-code": HarnessCapabilities(
        harness_id="claude-code",
        native_event_stream=True,
        native_session_persistence=True,
        terminal_event="sdk_result",
    ),
    "kimi": HarnessCapabilities(
        harness_id="kimi",
        native_event_stream=True,
        native_session_persistence=True,
        terminal_event="process_exit",
    ),
}


def get_harness_capabilities(harness_id: str) -> HarnessCapabilities:
    try:
        return HARNESS_CAPABILITIES[harness_id]
    except KeyError as exc:
        available = ", ".join(sorted(HARNESS_CAPABILITIES))
        raise KeyError(
            f"unknown harness {harness_id!r}; available={available}"
        ) from exc


def _reject_blank_items(label: str, values: Iterable[str]) -> None:
    if any(not str(item).strip() for item in values):
        raise ValueError(f"{label} cannot contain blank values")


__all__ = [
    "AgentCapabilityGrant",
    "AgentContextEnvelope",
    "AgentRuntimeProfile",
    "HARNESS_CAPABILITIES",
    "HarnessCapabilities",
    "RUNTIME_CONTRACT_VERSION",
    "get_harness_capabilities",
]
