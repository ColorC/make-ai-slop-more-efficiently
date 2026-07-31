from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from omnicompany.packages.domains.game_observatory.ai_player.external_agent_continuity import (
    ExternalAgentContinuousSessionV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.external_agent_runtime import (
    ContinuousExternalAgentRunner,
    ExternalAgentSessionLedger,
)


def _session(provider: str = "codex-cli") -> ExternalAgentContinuousSessionV1:
    return ExternalAgentContinuousSessionV1(
        id=f"external-session.{provider}.test",
        provider=provider,
        model_selector="test-model",
        requested_effort="medium",
        actual_effort="unreported",
        permission_mode="workspace-write",
        environment_id="environment.test",
        phase_id="EA-2.test",
        facility_contract_sha256="a" * 64,
        task_ids=["task.test"],
        started_at="2026-07-17T09:00:00+08:00",
        last_heartbeat_at="2026-07-17T09:00:00+08:00",
        updated_at="2026-07-17T09:00:00+08:00",
    )


def _fake_provider(path: Path) -> None:
    path.write_text(
        """
import json
import sys

provider, operation, session_id = sys.argv[1:4]
prompt = sys.stdin.read()
if provider == "codex-cli":
    print(json.dumps({"type": "thread.started", "thread_id": session_id}))
    print(json.dumps({"type": "turn.started"}))
    print(json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": f"done:{operation}:{len(prompt)}"
    }}))
    print(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 10, "cached_input_tokens": 4,
        "output_tokens": 3, "reasoning_output_tokens": 2
    }}))
else:
    print(json.dumps({
        "type": "system", "subtype": "init", "session_id": session_id,
        "model": "claude-sonnet-5", "effort": "medium"
    }))
    print(json.dumps({
        "type": "result", "session_id": session_id,
        "result": f"done:{operation}:{len(prompt)}", "is_error": False,
        "usage": {"input_tokens": 12, "cache_read_input_tokens": 5, "output_tokens": 4}
    }))
""".strip()
        + "\
",
        encoding="utf-8",
    )


def _patch_command(runner: ContinuousExternalAgentRunner, script: Path) -> None:
    def command(session, *, operation, cwd, message_path, provider_session_id):
        del cwd, message_path
        session_id = provider_session_id or "codex-native-session-1"
        return [sys.executable, str(script), session.provider, operation, session_id]

    runner._build_command = command  # type: ignore[method-assign]


def test_codex_session_reuses_one_native_session_and_accumulates_turn_usage(
    tmp_path: Path,
) -> None:
    script = tmp_path / "fake_provider.py"
    _fake_provider(script)
    ledger = ExternalAgentSessionLedger(tmp_path / "observatory")
    runner = ContinuousExternalAgentRunner(ledger, heartbeat_interval_seconds=0.01)
    _patch_command(runner, script)

    started, first = asyncio.run(
        runner.start(
            _session(),
            prompt="load facility contract once",
            cwd=tmp_path,
            timeout_seconds=5,
        )
    )
    resumed, second = asyncio.run(
        runner.resume(
            started.id,
            prompt="perform the next semantic action",
            cwd=tmp_path,
            timeout_seconds=5,
        )
    )

    assert first.status == second.status == "succeeded"
    assert started.external_session_id == "codex-native-session-1"
    assert resumed.external_session_id == started.external_session_id
    assert resumed.version == 3
    assert resumed.invocation_count == 2
    assert resumed.input_tokens == 20
    assert resumed.cached_input_tokens == 8
    assert resumed.output_tokens == 6
    assert resumed.reasoning_tokens == 4
    assert [item.operation for item in ledger.list_invocations(resumed.id)] == [
        "start",
        "resume",
    ]
    assert ledger.read_heartbeat(resumed.id)["sequence"] == 2
    assert all(
        (ledger.root / item.event_log_path).is_file()
        for item in ledger.list_invocations(resumed.id)
    )


def test_claude_session_records_returned_model_effort_and_cache_usage(tmp_path: Path) -> None:
    script = tmp_path / "fake_provider.py"
    _fake_provider(script)
    ledger = ExternalAgentSessionLedger(tmp_path / "observatory")
    runner = ContinuousExternalAgentRunner(ledger)
    _patch_command(runner, script)

    session, invocation = asyncio.run(
        runner.start(
            _session("claude-code-cli"),
            prompt="understand the AFKJ benchmark",
            cwd=tmp_path,
            timeout_seconds=5,
        )
    )

    assert invocation.status == "succeeded"
    assert invocation.resolved_model_id == "claude-sonnet-5"
    assert invocation.actual_effort == "medium"
    assert invocation.usage.model_dump() == {
        "input_tokens": 12,
        "cached_input_tokens": 5,
        "output_tokens": 4,
        "reasoning_tokens": 0,
    }
    assert session.external_session_id == invocation.external_session_id
    assert session.status == "active"


def test_failed_turn_suspends_the_same_session_and_keeps_failure_evidence(
    tmp_path: Path,
) -> None:
    script = tmp_path / "fail_provider.py"
    script.write_text(
        "import sys\
sys.stdin.read()\
print('provider failed', file=sys.stderr)\
raise SystemExit(7)\
",
        encoding="utf-8",
    )
    ledger = ExternalAgentSessionLedger(tmp_path / "observatory")
    runner = ContinuousExternalAgentRunner(ledger)
    _patch_command(runner, script)

    session, invocation = asyncio.run(
        runner.start(
            _session(),
            prompt="attempt one turn",
            cwd=tmp_path,
            timeout_seconds=5,
        )
    )

    assert invocation.status == "failed"
    assert invocation.exit_code == 7
    assert "provider failed" in (invocation.error or "")
    assert session.status == "suspended"
    assert session.last_error == invocation.error
    stored = json.loads((ledger.invocation_path(session.id, 1)).read_text(encoding="utf-8"))
    assert stored["event_log_sha256"] == invocation.event_log_sha256