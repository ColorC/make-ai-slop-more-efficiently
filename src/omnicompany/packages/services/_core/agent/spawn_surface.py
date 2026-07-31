# [OMNI] origin=codex domain=services/agent ts=2026-06-13 type=infrastructure
"""Authoritative registry for supported agent spawn entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


AGENT_SPAWN_SURFACE_VERSION = "2026-07-24:T9"

ENTRY_AGENT_TOOL = "agent_tool"
ENTRY_EXTERNAL_WORKER_RUN = "external_worker_run"
ENTRY_EXTERNAL_WORKER_AS_AGENT = "external_worker_as_agent_tool"
ENTRY_CONTROLLER_SPAWN = "controller_spawn"
ENTRY_TEAMRUNNER_NODE = "teamrunner_external_node"
ENTRY_WORKFLOW_RUN = "workflow_run"
ENTRY_INTERNAL_LOOP = "internal_agent_loop"
ENTRY_CONTEXT_FORK = "context_fork_adapter"


@dataclass(frozen=True)
class AgentSpawnEntry:
    entry_id: str
    kind: str
    surface: str
    label: str
    implementation: str
    use_when: str
    new_usage_rule: str
    launch_surface: bool = False


AGENT_SPAWN_ENTRIES: tuple[AgentSpawnEntry, ...] = (
    AgentSpawnEntry(
        entry_id=ENTRY_AGENT_TOOL,
        kind="agent-tool",
        surface="AgentRouter / Agent tool",
        label="In-loop sub-agent spawn",
        implementation="omnicompany.packages.services._core.agent.routers.agent_spawn.AgentRouter",
        use_when=(
            "An AgentNodeLoop needs an isolated in-process sub-agent from the injected "
            "subagent_registry."
        ),
        new_usage_rule=(
            "New internal sub-agents must be registered into ctx.subagent_registry and "
            "spawned through the Agent tool."
        ),
        launch_surface=True,
    ),
    AgentSpawnEntry(
        entry_id=ENTRY_EXTERNAL_WORKER_RUN,
        kind="external-worker",
        surface="ExternalAgentRunRequest / omni worker run",
        label="Synchronous external agent worker",
        implementation=(
            "omnicompany.packages.services._core.agent.external_workers.runner"
            ".run_external_agent_request"
        ),
        use_when=(
            "A CLI, API, or workflow needs an audited native, Pi, Codex, Claude Code, "
            "OpenCode, or Kimi run with explicit cwd, permission mode, timeout, and context."
        ),
        new_usage_rule=(
            "New synchronous local-agent callers construct ExternalAgentRunRequest; they "
            "do not call provider adapters directly."
        ),
        launch_surface=True,
    ),
    AgentSpawnEntry(
        entry_id=ENTRY_CONTROLLER_SPAWN,
        kind="controller-spawn",
        surface="SpawnSubagentRouter / omni worker spawn",
        label="Asynchronous BOSS SIGHT plan worker",
        implementation="omnicompany.dashboard.boss_sight.controller.tools.SpawnSubagentRouter",
        use_when=(
            "The BOSS SIGHT controller starts a long-running plan-bound worker and must "
            "return a subagent id immediately."
        ),
        new_usage_rule=(
            "New async controller workers go through spawn_subagent so plan guard, "
            "standards injection, active_plan, and wakeup events stay consistent."
        ),
        launch_surface=True,
    ),
    AgentSpawnEntry(
        entry_id=ENTRY_WORKFLOW_RUN,
        kind="workflow-orchestrator",
        surface="omni workflow run",
        label="Deterministic workflow fan-out",
        implementation="omnicompany.cli.commands.workflow",
        use_when=(
            "A deterministic workflow coordinates existing worker or controller spawn "
            "surfaces instead of opening a new primitive launch path."
        ),
        new_usage_rule=(
            "New DAG-style orchestration extends workflow nodes and reuses the existing "
            "worker/controller surfaces."
        ),
        launch_surface=True,
    ),
    AgentSpawnEntry(
        entry_id=ENTRY_EXTERNAL_WORKER_AS_AGENT,
        kind="adapter",
        surface="build_external_agent_subagent_registry",
        label="External worker exposed as Agent tool subagent",
        implementation="omnicompany.packages.services._core.agent.external_workers.subagent",
        use_when=(
            "An AgentRouter registry intentionally exposes a registered external harness "
            "as a subagent_type."
        ),
        new_usage_rule=(
            "This remains an adapter under AgentRouter and ExternalAgentRunRequest, not "
            "a fifth launch surface."
        ),
    ),
    AgentSpawnEntry(
        entry_id=ENTRY_CONTEXT_FORK,
        kind="adapter",
        surface="AgentRouter execution_mode=context_fork",
        label="Checkpoint-inheriting Agent tool adapter",
        implementation="omnicompany.packages.services._core.agent.context_fork",
        use_when=(
            "The Agent allocation gate selected context_fork and a parent checkpoint "
            "must be inherited without summary injection."
        ),
        new_usage_rule=(
            "This is an adapter under the existing Agent tool, not a primitive launch "
            "surface. It requires an allocation decision ref and returns only a "
            "structured receipt with trace/artifact references."
        ),
    ),
    AgentSpawnEntry(
        entry_id=ENTRY_TEAMRUNNER_NODE,
        kind="adapter",
        surface="ExternalAgentWorkerNode",
        label="TeamRunner node for external workers",
        implementation=(
            "omnicompany.packages.services._core.agent.external_workers.routers"
            ".workflow_node.ExternalAgentWorkerNode"
        ),
        use_when=(
            "A TeamRunner/PipelineRunner graph needs an external worker node bound to a "
            "deterministic pipeline step."
        ),
        new_usage_rule=(
            "This node wraps ExternalAgentRunRequest; it is not a standalone provider "
            "entry."
        ),
    ),
    AgentSpawnEntry(
        entry_id=ENTRY_INTERNAL_LOOP,
        kind="implementation",
        surface="AgentNodeLoop",
        label="Internal long-running loop implementation",
        implementation="omnicompany.packages.services._core.agent.loop.AgentNodeLoop",
        use_when=(
            "Implementing a new internal multi-turn agent loop. It is a base class and "
            "runtime contract, not a launch command."
        ),
        new_usage_rule=(
            "New AgentNodeLoop subclasses are launched through AgentRouter, Worker, or "
            "workflow surfaces; do not add another direct launcher."
        ),
    ),
)

_ENTRIES_BY_ID = {entry.entry_id: entry for entry in AGENT_SPAWN_ENTRIES}


def list_agent_spawn_entries(*, launch_only: bool = False) -> tuple[AgentSpawnEntry, ...]:
    """Return the known spawn entries, optionally limited to primitive launch surfaces."""

    if not launch_only:
        return AGENT_SPAWN_ENTRIES
    return tuple(entry for entry in AGENT_SPAWN_ENTRIES if entry.launch_surface)


def get_agent_spawn_entry(entry_id: str) -> AgentSpawnEntry:
    """Resolve a spawn entry id or raise KeyError for an unauthorized path."""

    return _ENTRIES_BY_ID[entry_id]


def agent_spawn_metadata(entry_id: str, **extra: Any) -> dict[str, Any]:
    """Build normalized metadata for audit payloads and testable contracts."""

    entry = get_agent_spawn_entry(entry_id)
    payload: dict[str, Any] = {
        "agent_spawn_surface": AGENT_SPAWN_SURFACE_VERSION,
        "agent_spawn_entry": entry.entry_id,
        "agent_spawn_kind": entry.kind,
        "agent_spawn_launch_surface": entry.launch_surface,
    }
    payload.update(extra)
    return payload


def ensure_agent_spawn_metadata(
    default_entry_id: str,
    metadata: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Merge caller metadata while preserving the authoritative spawn entry fields."""

    merged: dict[str, Any] = {}
    if metadata:
        merged.update(metadata)
    merged.update(extra)
    entry_id = str(merged.get("agent_spawn_entry") or default_entry_id)
    payload = agent_spawn_metadata(entry_id)
    payload.update(merged)
    entry = get_agent_spawn_entry(entry_id)
    payload["agent_spawn_surface"] = AGENT_SPAWN_SURFACE_VERSION
    payload["agent_spawn_entry"] = entry.entry_id
    payload["agent_spawn_kind"] = entry.kind
    payload["agent_spawn_launch_surface"] = entry.launch_surface
    return payload


def describe_agent_spawn_surface() -> list[dict[str, Any]]:
    """Return a serializable view for docs, APIs, and tests."""

    return [
        {
            "entry_id": entry.entry_id,
            "kind": entry.kind,
            "surface": entry.surface,
            "label": entry.label,
            "implementation": entry.implementation,
            "use_when": entry.use_when,
            "new_usage_rule": entry.new_usage_rule,
            "launch_surface": entry.launch_surface,
        }
        for entry in AGENT_SPAWN_ENTRIES
    ]


__all__ = [
    "AGENT_SPAWN_ENTRIES",
    "AGENT_SPAWN_SURFACE_VERSION",
    "ENTRY_AGENT_TOOL",
    "ENTRY_CONTROLLER_SPAWN",
    "ENTRY_CONTEXT_FORK",
    "ENTRY_EXTERNAL_WORKER_AS_AGENT",
    "ENTRY_EXTERNAL_WORKER_RUN",
    "ENTRY_INTERNAL_LOOP",
    "ENTRY_TEAMRUNNER_NODE",
    "ENTRY_WORKFLOW_RUN",
    "AgentSpawnEntry",
    "agent_spawn_metadata",
    "describe_agent_spawn_surface",
    "ensure_agent_spawn_metadata",
    "get_agent_spawn_entry",
    "list_agent_spawn_entries",
]
