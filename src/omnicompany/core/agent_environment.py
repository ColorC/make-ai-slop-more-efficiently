# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-09-02 type=service
# [OMNI] material_id="material:omnicompany.core.agent-environment.py"
"""Shared multi-provider agent *environment* registry.

One declaration of every CLI agent runtime OmniCompany and LiWork can start, plus
the account/route isolation each one needs.  ``config/agents/agent-environments.v1.json``
is the canonical artifact and is mirrored in LiWork
(``src/shared/agent-environment/agent-environments.v1.json``), so both products agree
on ids, aliases, account homes and CLI overrides.  The copies are compared by
``digest`` rather than by bytes: each repository normalizes line endings on its own.

This module lives in ``omnicompany.core`` on purpose: ``dashboard/ccdaemon`` (identity,
PTY env) and ``packages/services/_core/agent`` (launch contract) both need it, both
directions are allowed from here under arch_gate R1, and neither pays for the other's
import weight.

What the registry deliberately does *not* own: the argv a TUI host adds to enter
interactive mode.  ``executables`` / ``process_args`` are recorded so the two products
can be cross-checked, not so one overwrites the other's flags.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


REGISTRY_RELATIVE_PATH = ("config", "agents", "agent-environments.v1.json")


@dataclass(frozen=True)
class AccountHome:
    """How one environment is pinned to its own credentials.

    Codex and Claude Code keep the *whole* credential inside their home directory, so
    "another account" is another home, never an argv flag.
    """

    env_var: str
    role: str
    profile_id: str | None
    override_env: str
    dir_name: str

    @property
    def is_secondary(self) -> bool:
        return self.role == "secondary"


@dataclass(frozen=True)
class AgentEnvironment:
    id: str
    family: str
    status: str
    label: str
    catalog_label: str
    aliases: tuple[str, ...]
    executables: tuple[str, ...]
    process_args: tuple[str, ...]
    model_flag: str | None
    default_model: str | None
    default_effort: str | None
    pins_model_on_argv: bool
    cli_overrides: tuple[tuple[str, str], ...]
    api_key_env: str | None
    account_home: AccountHome | None
    agent: str | None
    default_surface: str
    surfaces: tuple[str, ...]
    native_chat: str
    resume_strategy: str
    native_trace: bool
    pty: bool
    session_host: str

    @property
    def is_active(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True)
class AgentEnvironmentRegistry:
    schema_version: int
    revision: str
    digest: str
    environments: tuple[AgentEnvironment, ...]

    def by_id(self, environment_id: str) -> AgentEnvironment | None:
        return _index(self).get(environment_id)

    @property
    def active(self) -> tuple[AgentEnvironment, ...]:
        return tuple(env for env in self.environments if env.is_active)


def registry_path() -> Path:
    """Locate the registry artifact.

    ``omni_workspace_root()`` is the authority, but this module is imported at
    ``provider_identity`` import time, and a sandboxed caller (tests, a spawned
    worker) can point ``OMNI_WORKSPACE_ROOT`` at a scratch tree that has no
    ``config/``. Fall back to this module's own source checkout or packaged
    artifact. Do not search unrelated ancestors, which may belong to a host
    checkout around a worktree or an installed environment.
    """
    from omnicompany.core.config import omni_workspace_root

    preferred = omni_workspace_root().joinpath(*REGISTRY_RELATIVE_PATH)
    if preferred.is_file():
        return preferred
    package = Path(__file__).resolve().parent.parent
    source = package.parent
    if source.name == "src":
        candidate = source.parent.joinpath(*REGISTRY_RELATIVE_PATH)
        if candidate.is_file():
            return candidate
    packaged = package.joinpath(*REGISTRY_RELATIVE_PATH)
    if packaged.is_file():
        return packaged
    return preferred


def _account_home(raw: Mapping[str, Any] | None) -> AccountHome | None:
    if not raw:
        return None
    return AccountHome(
        env_var=str(raw["env_var"]),
        role=str(raw["role"]),
        profile_id=raw["profile_id"],
        override_env=str(raw["override_env"]),
        dir_name=str(raw["dir_name"]),
    )


def _environment(raw: Mapping[str, Any]) -> AgentEnvironment:
    return AgentEnvironment(
        id=str(raw["id"]),
        family=str(raw["family"]),
        status=str(raw["status"]),
        label=str(raw["label"]),
        catalog_label=str(raw["catalog_label"]),
        aliases=tuple(str(alias) for alias in raw["aliases"]),
        executables=tuple(str(name) for name in raw["executables"]),
        process_args=tuple(str(arg) for arg in raw["process_args"]),
        model_flag=raw["model_flag"],
        default_model=raw["default_model"],
        default_effort=raw["default_effort"],
        pins_model_on_argv=bool(raw["pins_model_on_argv"]),
        cli_overrides=tuple((str(pair[0]), str(pair[1])) for pair in raw["cli_overrides"]),
        api_key_env=raw["api_key_env"],
        account_home=_account_home(raw["account_home"]),
        agent=raw["agent"],
        default_surface=str(raw["default_surface"]),
        surfaces=tuple(str(surface) for surface in raw["surfaces"]),
        native_chat=str(raw["native_chat"]),
        resume_strategy=str(raw["resume_strategy"]),
        native_trace=bool(raw["native_trace"]),
        pty=bool(raw["pty"]),
        session_host=str(raw["session_host"]),
    )


def digest_of(providers: list[Any]) -> str:
    """Digest input both products hash: the provider array, minified, non-ASCII kept.

    ``json.dumps(..., separators=(",", ":"), ensure_ascii=False)`` reproduces
    JavaScript's ``JSON.stringify`` byte for byte for this payload.
    """
    payload = json.dumps(providers, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def load_registry() -> AgentEnvironmentRegistry:
    raw = json.loads(registry_path().read_text(encoding="utf-8"))
    return AgentEnvironmentRegistry(
        schema_version=int(raw["schema_version"]),
        revision=str(raw["revision"]),
        digest=str(raw["digest"]),
        environments=tuple(_environment(row) for row in raw["providers"]),
    )


@lru_cache(maxsize=1)
def _index(registry: AgentEnvironmentRegistry) -> dict[str, AgentEnvironment]:
    return {env.id: env for env in registry.environments}


def agent_environments() -> tuple[AgentEnvironment, ...]:
    """Every registered environment, retired ones included, so old sessions stay readable."""
    return load_registry().environments


def active_agent_environments() -> tuple[AgentEnvironment, ...]:
    """Environments that may start a *new* session, in registry order."""
    return load_registry().active


def agent_environment(environment_id: str) -> AgentEnvironment | None:
    return load_registry().by_id(environment_id)


@lru_cache(maxsize=1)
def alias_index() -> dict[str, str]:
    """Registered alias → canonical id, including each id as its own alias."""
    index: dict[str, str] = {}
    for env in agent_environments():
        index[env.id] = env.id
        for alias in env.aliases:
            index[alias] = env.id
    return index


def canonical_environment_id(value: Any, *, default: str = "") -> str:
    """Normalize one runtime id without inventing an identity.

    A registered alias converges to its canonical id.  An unknown explicit id is
    preserved in normalized form so audit/backfill can report it instead of silently
    relabeling the session as Claude or Codex.
    """
    normalized = str(value or "").strip().casefold().replace("-", "_")
    if not normalized:
        return default
    return alias_index().get(normalized, normalized)


def environments_in_family(family: str) -> tuple[AgentEnvironment, ...]:
    return tuple(env for env in agent_environments() if env.family == family)


def secondary_account_environments() -> tuple[AgentEnvironment, ...]:
    return tuple(
        env for env in agent_environments() if env.account_home and env.account_home.is_secondary
    )


__all__ = [
    "AccountHome",
    "AgentEnvironment",
    "AgentEnvironmentRegistry",
    "REGISTRY_RELATIVE_PATH",
    "active_agent_environments",
    "agent_environment",
    "agent_environments",
    "alias_index",
    "canonical_environment_id",
    "digest_of",
    "environments_in_family",
    "load_registry",
    "registry_path",
    "secondary_account_environments",
]
