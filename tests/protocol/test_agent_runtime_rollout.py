from __future__ import annotations

import pytest

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.exec.agent_runtime_rollout import (
    AgentRuntimeRolloutMode,
    AgentRuntimeRolloutPolicy,
    AgentRuntimeRolloutRouter,
)
from omnicompany.runtime.routing.router import Router


class _Router(Router):
    FORMAT_IN = "task.request"
    FORMAT_OUT = "task.result"

    def __init__(self, label: str, *, fail: bool = False) -> None:
        self.label = label
        self.fail = fail
        self.calls = 0

    def run(self, input_data):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.label} failed")
        return Verdict(kind=VerdictKind.PASS, output={"runtime": self.label})


@pytest.mark.asyncio
async def test_canary_selects_only_exact_team_position() -> None:
    policy = AgentRuntimeRolloutPolicy(
        AgentRuntimeRolloutMode.CANARY,
        frozenset({"team:a/agent"}),
    )
    legacy = _Router("legacy")
    external = _Router("external")
    selected = AgentRuntimeRolloutRouter(
        team_id="team:a",
        position_id="agent",
        legacy=legacy,
        external=external,
        policy=policy,
    )

    verdict = await selected.run({})

    assert verdict.output == {"runtime": "external"}
    assert external.calls == 1
    assert legacy.calls == 0


@pytest.mark.asyncio
async def test_non_canary_position_remains_legacy() -> None:
    policy = AgentRuntimeRolloutPolicy(
        AgentRuntimeRolloutMode.CANARY,
        frozenset({"team:a/other"}),
    )
    legacy = _Router("legacy")
    external = _Router("external")
    selected = AgentRuntimeRolloutRouter(
        team_id="team:a",
        position_id="agent",
        legacy=legacy,
        external=external,
        policy=policy,
    )

    verdict = await selected.run({})

    assert verdict.output == {"runtime": "legacy"}
    assert legacy.calls == 1
    assert external.calls == 0


@pytest.mark.asyncio
async def test_external_failure_never_silently_falls_back() -> None:
    legacy = _Router("legacy")
    external = _Router("external", fail=True)
    selected = AgentRuntimeRolloutRouter(
        team_id="team:a",
        position_id="agent",
        legacy=legacy,
        external=external,
        policy=AgentRuntimeRolloutPolicy(AgentRuntimeRolloutMode.EXTERNAL),
    )

    with pytest.raises(RuntimeError, match="external failed"):
        await selected.run({})

    assert external.calls == 1
    assert legacy.calls == 0


def test_canary_environment_requires_explicit_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("OMNI_AGENT_RUNTIME_MODE", "canary")
    monkeypatch.delenv("OMNI_AGENT_RUNTIME_CANARY_POSITIONS", raising=False)

    with pytest.raises(ValueError, match="requires"):
        AgentRuntimeRolloutPolicy.from_environment()
