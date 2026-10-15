# [OMNI] origin=ai-ide ts=2026-05-31 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.controller.worker_contract.py"
"""BOSS SIGHT worker-axis constants.

These are separate axes:
- worker_kind selects task shape.
- provider selects execution backend.
- caller_identity selects permission identity.
"""

from __future__ import annotations

from omnicompany.core.caller_identity import (
    CALLER_CONTROLLER,
    CALLER_EXTERNAL,
    CALLER_SUBAGENT,
)

WORKER_KIND_TEAM = "team_worker"
WORKER_KIND_STANDALONE = "standalone_plan_worker"
WORKER_KINDS = (WORKER_KIND_TEAM, WORKER_KIND_STANDALONE)

PROVIDER_CLAUDE_CODE = "claude_code"
PROVIDER_CODEX = "codex"
PROVIDER_OMNI_AGENT = "omni_agent"
PROVIDER_KIRO = "kiro"
PROVIDER_QODER = "qoder"
PROVIDER_KIMI = "kimi"
PROVIDER_OPENCODE = "opencode"
PROVIDER_CODEBUDDY = "codebuddy"
STANDALONE_WORKER_PROVIDERS = (
    PROVIDER_CLAUDE_CODE,
    PROVIDER_CODEX,
    PROVIDER_KIRO,
    PROVIDER_QODER,
    PROVIDER_KIMI,
    PROVIDER_OPENCODE,
    PROVIDER_CODEBUDDY,
)

WORKER_AXIS_DOC = (
    "worker_kind selects task shape; provider selects execution backend; "
    "caller_identity selects permission identity."
)


__all__ = [
    "CALLER_CONTROLLER",
    "CALLER_EXTERNAL",
    "CALLER_SUBAGENT",
    "WORKER_AXIS_DOC",
    "WORKER_KIND_TEAM",
    "WORKER_KIND_STANDALONE",
    "WORKER_KINDS",
    "PROVIDER_CLAUDE_CODE",
    "PROVIDER_CODEX",
    "PROVIDER_OMNI_AGENT",
    "PROVIDER_KIRO",
    "PROVIDER_QODER",
    "PROVIDER_KIMI",
    "PROVIDER_OPENCODE",
    "PROVIDER_CODEBUDDY",
    "STANDALONE_WORKER_PROVIDERS",
]
