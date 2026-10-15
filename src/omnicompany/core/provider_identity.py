# [OMNI] origin=codex domain=core ts=2026-08-14 type=service
# [OMNI] material_id="material:core.provider-identity.py"
"""Canonical identities and account homes for CLI agent runtimes.

These ids describe the process/session runtime (Codex, Kimi, Qoder, ...), not
the model vendor or budget route used inside that runtime.  Keeping those axes
separate prevents a qwen-token-plan or the_company-api model route from overwriting
the identity needed to locate and resume the native conversation.

Every table here is derived from the shared agent-environment registry
(``omnicompany.core.agent_environment``, artifact
``config/agents/agent-environments.v1.json``), which LiWork mirrors.  Adding a runtime is one edit to that JSON, not a sweep across this module,
``control_plane`` and the LiWork provider profiles.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from omnicompany.core.agent_environment import (
    AccountHome,
    active_agent_environments,
    agent_environment,
    agent_environments,
    alias_index,
    canonical_environment_id,
)


#: Every registered runtime, retired launchers included. ``gemini`` (individual-plan
#: OAuth stopped 2026-06-18, succeeded by Antigravity) and ``zcode`` (GLM Coding Plan
#: forbids third-party clients since 2026-08-17, now routed through ``codex_glm``)
#: stay here so their existing sessions and home directories remain recognizable.
CLI_AGENT_PROVIDER_IDS = frozenset(env.id for env in agent_environments())

# Ordered, product-visible registry for starting new Agent Workers. Historical
# identities remain in CLI_AGENT_PROVIDER_IDS so old sessions can still be
# rendered and resumed, but retired launchers (gemini/zcode) do not leak back
# into creation UIs. Dashboard and LOFA consume this through
# GET /api/cc/agent-workers/catalog instead of copying provider enums.
AGENT_WORKER_PROVIDER_IDS = tuple(env.id for env in active_agent_environments())

# New-session creation is intentionally narrower than the historical runtime
# registry.  Old sessions still need to be listed/resumed, while the product
# picker exposes only the active accounts the current service uses.
VISIBLE_AGENT_WORKER_PROVIDER_IDS = (
    "codex",
    "claude_code",
    "claude_2",
    "codex_the_company",
    "opencode",
)

# Web-hosted DSH has no PTY byte stream. Every other registered runtime may
# publish terminal activity and can participate in the PTY lifecycle.
PTY_AGENT_PROVIDER_IDS = frozenset(env.id for env in agent_environments() if env.pty)

# These runtimes use the provider-native session id as their durable trace
# identity. Other managed runtimes use the Dashboard PTY id as their trace.
# ``codex_glm`` is the same Codex CLI with process-local Z.ai provider
# overrides, so it resumes through Codex's own session store as well.
# ``codex_2`` also resumes natively, but out of its *own* store — every reader
# must go through ``codex_home_for`` rather than assuming ``~/.codex``.
NATIVE_TRACE_PROVIDER_IDS = frozenset(
    env.id for env in agent_environments() if env.native_trace
)


def _family(family: str) -> frozenset[str]:
    return frozenset(env.id for env in agent_environments() if env.family == family)


def _official(family: str) -> frozenset[str]:
    """Accounts of one CLI, excluding model-route overrides.

    A route override (``codex_the_company`` / ``codex_glm``) authenticates as the primary
    account and only redirects the model provider, so it is not a separate
    subscription and owns no quota of its own.
    """
    return frozenset(
        env.id
        for env in agent_environments()
        if env.family == family and not env.cli_overrides
    )


#: Claude Code runtimes. Both run the same ``claude`` executable; they differ
#: only by ``CLAUDE_CONFIG_DIR`` (i.e. by account), exactly like codex/codex_2.
CLAUDE_FAMILY_PROVIDER_IDS = _family("claude")

#: Official Anthropic subscriptions under distinct credentials. Each owns a
#: separate quota, a separate ``.credentials.json`` and a separate transcript
#: store; every reader must go through ``claude_home_for``.
CLAUDE_OFFICIAL_PROVIDER_IDS = _official("claude")

#: Runtimes that are the official OpenAI Codex subscription under distinct
#: credentials. Each one owns a separate quota and a separate session store.
CODEX_OFFICIAL_PROVIDER_IDS = _official("codex")

#: Codex runtimes in general — official accounts plus model-route overrides.
#: All of them run the same ``codex`` executable.
CODEX_FAMILY_PROVIDER_IDS = _family("codex")


def canonical_cli_provider_id(value: Any, *, default: str = "") -> str:
    """Normalize one runtime id without inventing an identity.

    Registered aliases converge to their canonical id. An unknown explicit id is
    preserved in normalized form so audit/backfill can report it instead of
    silently relabeling the session as Claude or Codex.
    """

    return canonical_environment_id(value, default=default)


def registered_cli_provider(value: Any) -> str:
    provider = canonical_cli_provider_id(value)
    return provider if provider in CLI_AGENT_PROVIDER_IDS else ""


def provider_event_source(value: Any) -> str:
    provider = canonical_cli_provider_id(value, default="unknown")
    return "claude-code" if provider == "claude_code" else provider.replace("_", "-")


def _resolve_account_home(account_home: AccountHome) -> Path:
    """The registry says which env var may relocate this account's home and what the
    directory is called otherwise. Primary accounts declare the CLI's own env var as
    their override, so an ambient ``CODEX_HOME`` / ``CLAUDE_CONFIG_DIR`` keeps working;
    a second account declares its own (``OMNI_CODEX_2_HOME`` / ``OMNI_CLAUDE_2_HOME``).
    """
    override = os.environ.get(account_home.override_env, "").strip()
    return Path(override) if override else Path.home() / account_home.dir_name


def _account_home_for(provider: Any, *, default: str) -> Path:
    """Resolve the credential home one *family's* reader should use for ``provider``.

    An id from another family (or an unregistered one) falls back to ``default``:
    asking for the Codex home of a Claude session is a caller bug, and answering with
    the Claude home would send a Codex reader at the wrong tree.
    """
    fallback = agent_environment(default)
    assert fallback is not None and fallback.account_home is not None
    canonical = canonical_cli_provider_id(provider, default=default)
    environment = agent_environment(canonical)
    if environment is None or environment.family != fallback.family:
        return _resolve_account_home(fallback.account_home)
    if environment.account_home is None:
        return _resolve_account_home(fallback.account_home)
    return _resolve_account_home(environment.account_home)


def codex_home_for(provider: Any = "codex") -> Path:
    """Return the ``CODEX_HOME`` directory owned by one Codex runtime.

    Codex has no account switch of its own: the entire credential lives in
    ``$CODEX_HOME/auth.json``, so "another account" *is* another CODEX_HOME.
    Verified 2026-08-17 against codex-cli 0.147.0 — pointing CODEX_HOME at a
    fresh directory makes ``codex login status`` report "Not logged in".

    Consequences every caller has to respect:

    - ``codex_the_company`` / ``codex_glm`` only override the *model route* with
      process-local ``-c`` flags. They authenticate as the primary account and
      share ``~/.codex``, which is why the old hardcoded paths were correct for
      them.
    - ``codex_2`` is a different account, so its rollout logs, ``logs_2.sqlite``
      and ``state_5.sqlite`` all live elsewhere. Reading native session ids,
      resume targets or rate limits from ``~/.codex`` would silently report the
      primary account's data for a ``codex_2`` session.

    The alt home must not sit under a temporary directory: codex refuses to
    create its PATH helper binaries there ("Refusing to create helper binaries
    under temporary dir"), also observed 2026-08-17.
    """
    return _account_home_for(provider, default="codex")


def claude_home_for(provider: Any = "claude_code") -> Path:
    """Return the ``CLAUDE_CONFIG_DIR`` owned by one Claude Code runtime.

    Claude Code has no account switch of its own either: on Windows/Linux the
    whole credential lives in ``$CLAUDE_CONFIG_DIR/.credentials.json``, so
    "another account" *is* another config dir. Verified 2026-09-01 against the
    installed CLI — pointing ``CLAUDE_CONFIG_DIR`` at a fresh directory makes a
    ``claude -p`` call answer "Not logged in - Please run /login" and creates a
    clean tree there (``.claude.json`` / ``projects`` / ``sessions``).

    Consequences every caller has to respect (same shape as ``codex_home_for``):

    - ``claude_2`` is a different account, so its transcripts
      (``projects/*/<session>.jsonl``), its OAuth token and its usage all live
      elsewhere. Reading them from ``~/.claude`` would silently report the
      primary account's data for a ``claude_2`` session.
    - The primary account follows an ambient ``CLAUDE_CONFIG_DIR`` when one is
      set, so a relocated default home keeps working.
    """
    return _account_home_for(provider, default="claude_code")


def second_account_env(provider: Any) -> dict[str, str]:
    """Env that pins one runtime to its own account; empty for primary accounts.

    ``codex_2`` / ``claude_2`` carry nothing on argv — the account *is* the home
    directory — so every spawn path has to hand this to the child explicitly.
    Classification (``_provider_for_command``) reads exactly this env, which is
    why ``provider_terminal_environment`` cannot be the only place that sets it:
    it runs after classification.
    """
    environment = agent_environment(canonical_cli_provider_id(provider))
    account_home = environment.account_home if environment else None
    if account_home is None or not account_home.is_secondary:
        return {}
    return {account_home.env_var: str(_resolve_account_home(account_home))}


#: Kept for callers that inspect the alias table directly (audit/backfill tools).
_ALIASES = alias_index()


__all__ = [
    "AGENT_WORKER_PROVIDER_IDS",
    "VISIBLE_AGENT_WORKER_PROVIDER_IDS",
    "CLAUDE_FAMILY_PROVIDER_IDS",
    "CLAUDE_OFFICIAL_PROVIDER_IDS",
    "CLI_AGENT_PROVIDER_IDS",
    "CODEX_FAMILY_PROVIDER_IDS",
    "CODEX_OFFICIAL_PROVIDER_IDS",
    "NATIVE_TRACE_PROVIDER_IDS",
    "PTY_AGENT_PROVIDER_IDS",
    "canonical_cli_provider_id",
    "claude_home_for",
    "codex_home_for",
    "provider_event_source",
    "registered_cli_provider",
    "second_account_env",
]
