from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from omnicompany.packages.services.recovery.evidence import plan_recovery, write_plan
from omnicompany.packages.services.recovery.evidence_index import collect_evidence, load_index, write_index
from omnicompany.packages.services.recovery.providers import default_registry
from omnicompany.packages.services.recovery.runtime import apply_plan, rollback, run_probe_spec
from omnicompany.packages.services.recovery import entrypoint


PNG_BYTES = b"\x89PNG\r\n\x1a\nfixture-pixel"
BINARY_BYTES = bytes(range(256))
RECOVER = Path(__file__).resolve().parents[2] / "scripts" / "recover.py"


def test_source_checkout_entrypoint_finds_legacy_cli() -> None:
    assert callable(entrypoint._legacy_cli().main)


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _codex(source: Path, workspace: Path) -> None:
    log = source / "sessions" / "fixture.jsonl"
    pixel = base64.b64encode(PNG_BYTES).decode("ascii")
    _jsonl(log, [
        {"type": "session_meta", "payload": {"id": "codex-fixture", "cwd": str(workspace)}},
        {"timestamp": "2026-01-01T00:00:01Z", "type": "event_msg", "payload": {
            "type": "patch_apply_end", "success": True, "changes": {
                "codex/latest.txt": {"type": "update", "content": "old"},
                "codex/empty.txt": {"type": "add", "content": ""},
                "shared.txt": {"type": "add", "content": "from-codex"},
                "concurrent.txt": {"type": "add", "content": "codex-version"},
                "deleted-after.txt": {"type": "add", "content": "must-not-return"},
            }
        }},
        {"timestamp": "2026-01-01T00:00:02Z", "type": "event_msg", "payload": {
            "type": "patch_apply_end", "success": True,
            "changes": {"codex/latest.txt": {"type": "update", "content": "new"}}
        }},
        {"timestamp": "2026-01-01T00:00:03Z", "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "view_image", "call_id": "image-read",
            "input": json.dumps({"path": str(workspace / "codex" / "pixel.png"), "detail": "original"})
        }},
        {"timestamp": "2026-01-01T00:00:04Z", "type": "response_item", "payload": {
            "type": "custom_tool_call_output", "call_id": "image-read",
            "output": [{"type": "image", "data": pixel, "mimeType": "image/png", "name": "pixel.png"}]
        }},
        {"timestamp": "2026-01-01T00:00:05Z", "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "apply_patch", "call_id": "delete",
            "input": "*** Begin Patch\n*** Delete File: deleted-after.txt\n*** End Patch\n"
        }},
    ])


def _claude(source: Path, workspace: Path) -> None:
    backup = source / "file-history" / "binary.png@v1"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(BINARY_BYTES)
    attachment = base64.b64encode(PNG_BYTES + b"-attachment").decode("ascii")
    log = source / "projects" / "fixture.jsonl"
    _jsonl(log, [
        {"type": "file-history-snapshot", "sessionId": "claude-fixture", "timestamp": "2026-01-01T00:00:01Z", "snapshot": {
            "timestamp": "2026-01-01T00:00:01Z", "trackedFileBackups": {
                str(workspace / "claude" / "read-fallback.bin"): {
                    "backupFileName": "binary.png@v1", "version": 1
                }
            }
        }},
        {"sessionId": "claude-fixture", "timestamp": "2026-01-01T00:00:02Z", "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": "c-write-old", "name": "Write", "input": {
                "file_path": str(workspace / "claude" / "latest.txt"), "content": "old"
            }}]
        }},
        {"sessionId": "claude-fixture", "timestamp": "2026-01-01T00:00:06Z", "message": {
            "role": "assistant", "content": [
                {"type": "tool_use", "id": "c-write-new", "name": "Write", "input": {
                    "file_path": str(workspace / "claude" / "latest.txt"), "content": "new"
                }},
                {"type": "tool_use", "id": "shared-new", "name": "Write", "input": {
                    "file_path": str(workspace / "shared.txt"), "content": "from-claude"
                }}
            ]
        }},
        {"sessionId": "claude-fixture", "timestamp": "2026-01-01T00:00:07Z", "message": {
            "role": "user", "content": [{"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": attachment
            }, "name": "prompt-image.png"}]
        }},
    ])


def _kimi(source: Path, workspace: Path) -> None:
    log = source / "sessions" / "session_kimi_fixture" / "agents" / "main" / "wire.jsonl"
    pixel = base64.b64encode(PNG_BYTES).decode("ascii")
    _jsonl(log, [
        {"event": {"type": "tool.call", "uuid": "k-write", "name": "Write", "time": "2026-01-01T00:00:02Z", "args": {
            "path": str(workspace / "kimi" / "latest.txt"), "content": "kimi-new"
        }}},
        {"event": {"type": "tool.call", "uuid": "k-read-old", "name": "Read", "time": "2026-01-01T00:00:03Z", "args": {
            "path": str(workspace / "kimi" / "disagrees.txt")
        }}},
        {"event": {"type": "tool.call", "uuid": "k-write-disagrees", "name": "Write", "time": "2026-01-01T00:00:04Z", "args": {
            "path": str(workspace / "kimi" / "disagrees.txt"), "content": "mutation-winner"
        }}},
        {"event": {"type": "tool.call", "uuid": "k-read-late", "name": "Read", "time": "2026-01-01T00:00:05Z", "args": {
            "path": str(workspace / "kimi" / "disagrees.txt")
        }}},
        {"event": {"type": "tool.result", "parentUuid": "k-read-late", "time": "2026-01-01T00:00:06Z", "result": {
            "complete": True, "content": "stale-read"
        }}},
        {"event": {"type": "tool.call", "uuid": "k-image", "name": "read_binary", "time": "2026-01-01T00:00:07Z", "args": {
            "path": str(workspace / "kimi" / "pixel.png")
        }}},
        {"event": {"type": "tool.result", "parentUuid": "k-image", "time": "2026-01-01T00:00:08Z", "result": {
            "content": [{"type": "image", "data": pixel, "mimeType": "image/png", "name": "pixel.png"}]
        }}},
        {"event": {"type": "tool.call", "uuid": "k-concurrent", "name": "Write", "time": "2026-01-01T00:00:01Z", "args": {
            "path": str(workspace / "concurrent.txt"), "content": "kimi-version"
        }}},
        {"event": {"type": "tool.call", "uuid": "k-shell", "name": "PowerShell", "time": "2026-01-01T00:00:09Z", "args": {
            "command": "Remove-Item important.txt"
        }}},
    ])


def _opencode(source: Path, workspace: Path) -> None:
    source.mkdir(parents=True, exist_ok=True)
    database = source / "opencode.db"
    connection = sqlite3.connect(database)
    try:
        connection.executescript("""
            CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT NOT NULL, title TEXT NOT NULL);
            CREATE TABLE part (
                id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL
            );
        """)
        connection.execute("INSERT INTO session VALUES (?, ?, ?)", ("open-fixture", str(workspace), "fixture"))
        rows = [
            ("o-empty", 1000, {"type": "tool", "tool": "write", "state": {
                "status": "completed", "input": {"filePath": str(workspace / "opencode" / "empty.txt"), "content": ""}
            }}),
            ("o-latest-old", 2000, {"type": "tool", "tool": "write", "state": {
                "status": "completed", "input": {"filePath": str(workspace / "opencode" / "latest.txt"), "content": "old"}
            }}),
            ("o-latest-new", 3000, {"type": "tool", "tool": "write", "state": {
                "status": "completed", "input": {"filePath": str(workspace / "opencode" / "latest.txt"), "content": "new"}
            }}),
            ("o-read-only", 4000, {"type": "tool", "tool": "read", "state": {
                "status": "completed", "input": {"filePath": str(workspace / "opencode" / "read-only.txt")},
                "output": "read fallback", "metadata": {"truncated": False, "exact_bytes": True}
            }}),
        ]
        for row_id, created, data in rows:
            connection.execute(
                "INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)",
                (row_id, f"msg-{row_id}", "open-fixture", created, created, json.dumps(data)),
            )
        connection.commit()
    finally:
        connection.close()


def _fixtures(tmp_path: Path) -> tuple[Path, list[tuple[str, Path]]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sources = tmp_path / "sources"
    codex, claude, kimi, opencode = (sources / name for name in ("codex", "claude", "kimi", "opencode"))
    _codex(codex, workspace)
    _claude(claude, workspace)
    _kimi(kimi, workspace)
    _opencode(opencode, workspace)
    return workspace, [("codex", codex), ("claude", claude), ("kimi", kimi), ("opencode", opencode)]


def _action_map(plan) -> dict[str, object]:
    return {
        str(Path(action.target_path).relative_to(plan.workspace_root)).replace("\\", "/"): action
        for action in plan.actions if action.target_path
    }


def test_four_provider_byte_recovery_end_to_end(tmp_path: Path) -> None:
    workspace, sources = _fixtures(tmp_path)
    records = collect_evidence(default_registry(load_entry_points=False), sources)
    assert {record.provider for record in records} == {"codex", "claude", "kimi", "opencode"}
    assert any(record.has_exact_bytes and record.content_length == 0 for record in records)
    assert any(record.mime_type == "image/png" and record.has_exact_bytes for record in records)

    index_dir = tmp_path / "index"
    manifest = write_index(records, index_dir)
    assert manifest["providers"] == ["claude", "codex", "kimi", "opencode"]
    loaded = load_index(index_dir)
    assert [record.content_sha256 for record in loaded] == [record.content_sha256 for record in records]

    plan = plan_recovery(loaded, workspace, generated_at="2026-01-02T00:00:00+00:00")
    actions = _action_map(plan)
    assert actions["codex/latest.txt"].decision == "restore-missing-mutation"
    assert actions["codex/empty.txt"].candidate_length == 0
    assert actions["codex/pixel.png"].decision == "restore-missing-read-fallback"
    assert actions["claude/read-fallback.bin"].decision == "restore-missing-read-fallback"
    assert actions["opencode/read-only.txt"].decision == "restore-missing-read-fallback"
    assert actions["shared.txt"].winner_evidence_id != actions["codex/latest.txt"].winner_evidence_id
    assert actions["kimi/disagrees.txt"].decision == "conflict-isolated"
    assert "later-read-disagrees-with-mutation-winner" in actions["kimi/disagrees.txt"].blockers
    assert actions["deleted-after.txt"].decision == "conflict-isolated"
    assert actions["concurrent.txt"].decision == "conflict-isolated"
    assert any(action.decision == "quarantine-exact-artifact" for action in plan.actions)

    plan_path = tmp_path / "plan.json"
    write_plan(plan, plan_path)
    snapshot = tmp_path / "snapshot"
    artifacts = tmp_path / "artifacts"
    result = apply_plan(
        plan_path, snapshot_dir=snapshot, confirm_workspace=str(workspace.resolve()), artifact_dir=artifacts
    )
    assert result["applied"] >= 9
    assert (workspace / "codex" / "latest.txt").read_bytes() == b"new"
    assert (workspace / "codex" / "empty.txt").read_bytes() == b""
    assert (workspace / "codex" / "pixel.png").read_bytes() == PNG_BYTES
    assert (workspace / "claude" / "read-fallback.bin").read_bytes() == BINARY_BYTES
    assert (workspace / "opencode" / "latest.txt").read_bytes() == b"new"
    assert (workspace / "shared.txt").read_bytes() == b"from-claude"
    assert not (workspace / "kimi" / "disagrees.txt").exists()
    assert not (workspace / "deleted-after.txt").exists()
    assert not (workspace / "concurrent.txt").exists()
    assert list(artifacts.rglob("prompt-image.png"))

    repeated = apply_plan(
        plan_path, snapshot_dir=snapshot, confirm_workspace=str(workspace.resolve()), artifact_dir=artifacts
    )
    assert repeated["applied"] == 0
    assert repeated["already_applied"] == result["applied"] + result["quarantined_artifacts"]

    probe = run_probe_spec(workspace, {
        "semantic_review_required": True,
        "probes": [
            {"id": "shared-bytes", "kind": "file-sha256", "path": "shared.txt",
             "sha256": hashlib.sha256(b"from-claude").hexdigest()},
            {"id": "python-runnable", "kind": "command", "argv": ["python", "-c", "from pathlib import Path; assert Path('shared.txt').read_bytes() == b'from-claude'"]},
        ],
    })
    assert probe["passed"] is True
    assert probe["semantic_review_required"] is True

    journal = snapshot / "journal.jsonl"
    preview = rollback(
        journal, confirm_workspace=str(workspace.resolve()),
        confirm_artifact_root=str(artifacts.resolve()), dry_run=True,
    )
    assert any(row["status"] == "would-remove" for row in preview["results"])
    rolled_back = rollback(
        journal, confirm_workspace=str(workspace.resolve()),
        confirm_artifact_root=str(artifacts.resolve()), dry_run=False,
    )
    assert any(row["status"] == "rolled-back" for row in rolled_back["results"])
    assert not (workspace / "shared.txt").exists()


@pytest.mark.parametrize("failpoint,written", [
    ("after-snapshot", False),
    ("after-journal-intent:0", False),
    ("before-replace:0", False),
    ("after-replace:0", True),
    ("before-journal-result:0", True),
])
def test_fault_injection_leaves_recoverable_state(tmp_path: Path, failpoint: str, written: bool) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from omnicompany.packages.services.recovery.evidence import EvidenceRecord

    record = EvidenceRecord.from_bytes(
        evidence_id="fault-record", provider="fixture", session_id="fault-session",
        timestamp="2026-01-01T00:00:00Z", sequence=1, kind="write",
        authority="mutation-postimage", source_locator="fixture:1",
        content=b"recoverable", path=str(workspace / "file.txt"),
    )
    plan = plan_recovery([record], workspace, generated_at="2026-01-02T00:00:00Z")
    snapshot = tmp_path / "snapshot"
    with pytest.raises(RuntimeError, match="injected recovery failure"):
        apply_plan(plan, snapshot_dir=snapshot, confirm_workspace=str(workspace.resolve()), failpoint=failpoint)
    assert (workspace / "file.txt").exists() is written
    journal = snapshot / "journal.jsonl"
    if journal.exists():
        result = rollback(journal, confirm_workspace=str(workspace.resolve()), dry_run=False)
        if written:
            assert any(row["status"] == "rolled-back" for row in result["results"])
            assert not (workspace / "file.txt").exists()
        else:
            assert any(row["status"] == "intent-not-applied" for row in result["results"])


def test_local_provider_manifest_is_session_extensible(tmp_path: Path) -> None:
    config = tmp_path / "providers.d"
    config.mkdir()
    (config / "developer-agent.json").write_text(json.dumps({
        "name": "developer-agent",
        "roots": [str(tmp_path / "sessions")],
        "includes": ["**/*.jsonl"],
        "excludes": ["credentials/**"],
    }), encoding="utf-8")
    registry = default_registry(config_dirs=[config], load_entry_points=False)
    assert registry.definitions["developer-agent"].origin.endswith("developer-agent.json")
    assert "developer-agent" not in registry.adapters


def test_public_cli_runs_four_provider_fixture_through_probe_and_rollback(tmp_path: Path) -> None:
    workspace, sources = _fixtures(tmp_path)
    index = tmp_path / "cli-index"
    plan = tmp_path / "cli-plan.json"
    snapshot = tmp_path / "cli-snapshot"
    artifacts = tmp_path / "cli-artifacts"

    def run(*args: str, expected: int = 0) -> dict:
        completed = subprocess.run(
            [sys.executable, str(RECOVER), *args], cwd=RECOVER.parents[1],
            capture_output=True, text=True, check=False,
        )
        assert completed.returncode == expected, completed.stderr or completed.stdout
        return json.loads(completed.stdout)

    index_result = run(
        "index", "build",
        *[value for provider, path in sources for value in ("--source", f"{provider}={path}")],
        "--out", str(index),
    )
    assert index_result["record_count"] >= 20
    plan_result = run(
        "plan", "--index", str(index), "--workspace-root", str(workspace), "--out", str(plan)
    )
    assert plan_result["decision_counts"]["conflict-isolated"] == 3
    snapshot_result = run("snapshot", "create", "--plan", str(plan), "--out", str(snapshot))
    assert snapshot_result["records"] >= 10
    dry_run = run(
        "apply", "--plan", str(plan), "--snapshot", str(snapshot),
        "--confirm-workspace", str(workspace.resolve()), "--artifact-dir", str(artifacts),
    )
    assert dry_run["production_files_written"] is False
    assert not (workspace / "shared.txt").exists()
    applied = run(
        "apply", "--plan", str(plan), "--snapshot", str(snapshot),
        "--confirm-workspace", str(workspace.resolve()), "--artifact-dir", str(artifacts), "--apply",
    )
    assert applied["applied"] >= 9
    probe_spec = tmp_path / "probe.yaml"
    probe_spec.write_text(json.dumps({
        "semantic_review_required": True,
        "probes": [{
            "id": "shared", "kind": "file-sha256", "path": "shared.txt",
            "sha256": hashlib.sha256(b"from-claude").hexdigest(),
        }],
    }), encoding="utf-8")
    probed = run("probe", "--workspace-root", str(workspace), "--spec", str(probe_spec))
    assert probed["passed"] is True
    rollback_preview = run(
        "rollback", "--journal", str(snapshot / "journal.jsonl"),
        "--confirm-workspace", str(workspace.resolve()),
        "--confirm-artifact-root", str(artifacts.resolve()),
    )
    assert rollback_preview["mode"] == "dry-run"
    rolled_back = run(
        "rollback", "--journal", str(snapshot / "journal.jsonl"),
        "--confirm-workspace", str(workspace.resolve()),
        "--confirm-artifact-root", str(artifacts.resolve()), "--apply",
    )
    assert any(row["status"] == "rolled-back" for row in rolled_back["results"])
    assert not (workspace / "shared.txt").exists()
