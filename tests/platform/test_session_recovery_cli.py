from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "session_recovery_cli.py"
SPEC = importlib.util.spec_from_file_location("session_recovery_cli", SCRIPT)
assert SPEC and SPEC.loader
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


def test_source_specs_cover_four_providers() -> None:
    assert set(recovery.SOURCE_SPECS) == {"codex", "claude", "kimi", "opencode"}
    assert recovery.matches("sessions/example/wire.jsonl", ["sessions/**"])
    assert recovery.matches("credentials/token.json", ["credentials/**"])


def test_archive_is_content_addressed_and_verifiable(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source"
    (source / "sessions" / "one").mkdir(parents=True)
    (source / "sessions" / "one" / "wire.jsonl").write_text('{"ok":true}\n', encoding="utf-8")
    original = recovery.SOURCE_SPECS["kimi"]
    recovery.SOURCE_SPECS["kimi"] = {
        "roots": [str(source)],
        "includes": ["sessions/**"],
        "excludes": [],
    }
    try:
        args = type("Args", (), {
            "provider": ["kimi"],
            "archive_root": tmp_path / "archive",
            "since": None,
            "dry_run": False,
        })()
        assert recovery.archive_create(args) == 0
        result = json.loads(capsys.readouterr().out)
        manifest = Path(result["manifest"])
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        record = payload["records"][0]
        blob = tmp_path / "archive" / "objects" / "sha256" / record["blob_sha256"][:2] / record["blob_sha256"]
        assert blob.read_bytes() == (source / "sessions" / "one" / "wire.jsonl").read_bytes()
        verify_args = type("Args", (), {"manifest": manifest, "sample": 0})()
        assert recovery.archive_verify(verify_args) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
    finally:
        recovery.SOURCE_SPECS["kimi"] = original


def test_archive_dry_run_writes_nothing(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source"
    (source / "sessions").mkdir(parents=True)
    (source / "sessions" / "wire.jsonl").write_text("{}\n", encoding="utf-8")
    original = recovery.SOURCE_SPECS["codex"]
    recovery.SOURCE_SPECS["codex"] = {
        "roots": [str(source)],
        "includes": ["sessions/**"],
        "excludes": [],
    }
    try:
        archive_root = tmp_path / "must-not-exist"
        args = type("Args", (), {
            "provider": ["codex"],
            "archive_root": archive_root,
            "since": None,
            "dry_run": True,
        })()
        assert recovery.archive_create(args) == 0
        assert json.loads(capsys.readouterr().out)["mode"] == "dry-run"
        assert not archive_root.exists()
    finally:
        recovery.SOURCE_SPECS["codex"] = original


def _opencode_fixture(tmp_path: Path) -> Path:
    database = tmp_path / "opencode.db"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                directory TEXT NOT NULL,
                title TEXT NOT NULL
            );
            CREATE TABLE part (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                time_updated INTEGER NOT NULL,
                data TEXT NOT NULL
            );
            INSERT INTO session(id, directory, title)
            VALUES ('ses_fixture', 'C:/workspace/omnicompany', 'Recovery fixture');
            """
        )
        records = [
            (
                "write-empty",
                1000,
                {
                    "type": "tool",
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "C:/workspace/omnicompany/empty.txt", "content": ""},
                        "time": {"start": 1100, "end": 1200},
                    },
                },
            ),
            (
                "edit",
                2000,
                {
                    "type": "tool",
                    "tool": "edit",
                    "state": {
                        "status": "completed",
                        "input": {
                            "filePath": "C:/workspace/omnicompany/app.py",
                            "oldString": "old",
                            "newString": "new",
                        },
                        "time": {"end": 2200},
                    },
                },
            ),
            (
                "patch",
                3000,
                {
                    "type": "tool",
                    "tool": "apply_patch",
                    "state": {
                        "status": "completed",
                        "input": {
                            "patchText": "*** Begin Patch\n*** Update File: src/one.py\n*** Move to: src/two.py\n*** End Patch"
                        },
                        "time": {"end": 3200},
                    },
                },
            ),
            (
                "bash",
                4000,
                {
                    "type": "tool",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "python generate.py", "workdir": "C:/workspace/omnicompany"},
                        "time": {"end": 4200},
                    },
                },
            ),
            (
                "read",
                5000,
                {
                    "type": "tool",
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "C:/workspace/omnicompany/app.py"},
                        "output": "new file contents",
                        "metadata": {"truncated": False},
                        "time": {"end": 5200},
                    },
                },
            ),
        ]
        for record_id, created, data in records:
            connection.execute(
                "INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    f"msg-{record_id}",
                    "ses_fixture",
                    created,
                    created,
                    json.dumps(data),
                ),
            )
        connection.execute(
            "INSERT INTO part VALUES ('invalid', 'msg-invalid', 'ses_fixture', 6000, 6000, '{')"
        )
        connection.commit()
    finally:
        connection.close()
    return database


def test_opencode_query_is_newest_first_and_classifies_evidence(tmp_path: Path) -> None:
    database = _opencode_fixture(tmp_path)
    records, invalid = recovery.opencode_query_records(database, limit=20)

    assert invalid == 1
    assert [record["tool"] for record in records] == [
        "read",
        "bash",
        "apply_patch",
        "edit",
        "write",
    ]
    by_tool = {record["tool"]: record for record in records}
    assert by_tool["write"]["candidate_kind"] == "complete-file"
    assert by_tool["write"]["complete_post_image"] is True
    assert by_tool["write"]["replay_disposition"]["source_replay_state"] == "eligible_exact_post_image"
    assert by_tool["write"]["runnability_evidence"] == "not_evaluated"
    # Empty files are exact post-images, not mistaken for missing content.
    assert by_tool["write"]["payload"]["content"]["utf8_bytes"] == 0
    assert by_tool["edit"]["candidate_kind"] == "hunk-requires-baseline"
    assert by_tool["edit"]["replay_disposition"]["automatic_apply_eligible"] is False
    assert by_tool["apply_patch"]["target_paths"] == ["src/one.py", "src/two.py"]
    assert by_tool["bash"]["candidate_kind"] == "lead-only"
    assert by_tool["read"]["candidate_kind"] == "corroboration-only"


def test_opencode_query_scope_since_and_payload_controls(tmp_path: Path) -> None:
    database = _opencode_fixture(tmp_path)
    records, _ = recovery.opencode_query_records(
        database,
        tools=["write", "edit"],
        scopes=["empty.txt"],
        since="1970-01-01T00:00:01Z",
        limit=5,
        include_payloads=True,
    )

    assert [record["evidence_id"] for record in records] == ["write-empty"]
    assert records[0]["payload"]["content"]["text"] == ""


def test_terminal_write_evidence_is_never_promoted_without_a_post_image() -> None:
    assert recovery._terminal_write_shape("Set-Content -LiteralPath demo.txt -Value 'x'") == "powershell-content-cmdlet"
    assert recovery._terminal_write_shape("cat <<'EOF' > demo.txt\nvalue\nEOF") == "shell-redirection-or-heredoc"
    assert recovery._terminal_write_shape("python generate.py") is None
    disposition = recovery._replay_disposition("terminal-write-requires-post-image", "terminal-write-intent")
    assert disposition["automatic_apply_eligible"] is False
    assert disposition["source_replay_state"] == "terminal_write_needs_materialized_post_image"


def test_opencode_query_and_dry_run_leave_database_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = _opencode_fixture(tmp_path)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    args = type(
        "Args",
        (),
        {
            "database": database,
            "tool": ["write"],
            "scope": [],
            "since": None,
            "limit": 10,
            "include_payloads": False,
            "dry_run": True,
        },
    )()

    assert recovery.opencode_query(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "dry-run"
    assert result["source_opened_read_only"] is True
    assert result["historical_commands_executed"] is False
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    connection = recovery._opencode_connection(database)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM part")
    finally:
        connection.close()


def test_query_path_normalizes_windows_separators_and_rehashes_current(
    tmp_path: Path,
) -> None:
    workspace_file = tmp_path / "omnicompany" / "src" / "ShellRail.tsx"
    workspace_file.parent.mkdir(parents=True)
    workspace_file.write_text("current\n", encoding="utf-8")
    candidate = tmp_path / "candidate" / "ShellRail.tsx"
    candidate.parent.mkdir()
    candidate.write_text("current\n", encoding="utf-8")
    ledger = tmp_path / "LATEST-COMPLETE-FILES.json"
    ledger.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": str(workspace_file),
                        "normalized_path": str(workspace_file).replace("\\", "/"),
                        "timestamp": "2026-07-31T01:02:03Z",
                        "source": "kimi",
                        "candidate_artifact": str(candidate),
                        "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                        "decision": "already-present-latest-complete",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    records, ledgers = recovery.query_path_records(
        targets=[r"omnicompany\src\shellrail.tsx"],
        ledgers=[ledger],
    )

    assert ledgers == [str(ledger.resolve())]
    assert len(records) == 1
    assert records[0]["candidate_exists"] is True
    assert records[0]["exact_current_match"] is True


def test_recover_compatibility_entrypoint_supports_dry_run_path_query(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "LATEST-COMPLETE-FILES.json"
    ledger.write_text('{"files": []}', encoding="utf-8")
    wrapper = SCRIPT.with_name("recover.py")

    completed = subprocess.run(
        [
            sys.executable,
            str(wrapper),
            "query",
            "path",
            "--target",
            "ShellRail.tsx",
            "--ledger",
            str(ledger),
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["mode"] == "dry-run"
    assert payload["production_files_written"] is False


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.name", "Recovery Test")
    _git(repo, "config", "user.email", "recovery@example.invalid")
    _git(repo, "config", "core.autocrlf", "false")


def test_query_git_finds_blob_only_present_in_old_reachable_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    target = repo / "src" / "feature.txt"
    target.parent.mkdir()
    target.write_bytes(b"old exact bytes\x00\n")
    _git(repo, "add", "src/feature.txt")
    _git(repo, "commit", "-m", "old version")
    old_commit = _git(repo, "rev-parse", "HEAD")
    candidate = tmp_path / "candidate.bin"
    candidate.write_bytes(target.read_bytes())
    old_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()

    target.write_text("new version\n", encoding="utf-8")
    _git(repo, "add", "src/feature.txt")
    _git(repo, "commit", "-m", "new version")
    before_index = hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()
    before_status = _git(repo, "status", "--porcelain=v1")

    result = recovery.query_git_records(
        repo=repo,
        targets=[r"src\feature.txt"],
        candidate_file=candidate,
        limit=20,
    )

    assert result["candidate"]["effective_sha256"] == old_sha
    assert result["candidate"]["git_reachable"] is True
    old_record = next(record for record in result["records"] if record["blob_sha256"] == old_sha)
    assert any(entry["commit"] == old_commit for entry in old_record["commits"])
    assert {ref["kind"] for ref in old_record["refs"]} >= {"head"}
    assert hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest() == before_index
    assert _git(repo, "status", "--porcelain=v1") == before_status
    assert _git(repo, "stash", "list") == ""


def test_query_git_cli_aggregates_mirror_ref_and_reports_candidate_reachable(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    target = repo / "src" / "feature.txt"
    target.parent.mkdir()
    target.write_text("main only\n", encoding="utf-8")
    _git(repo, "add", "src/feature.txt")
    _git(repo, "commit", "-m", "main")

    mirror_source = tmp_path / "mirror-source"
    _init_git_repo(mirror_source)
    mirror_target = mirror_source / "src" / "feature.txt"
    mirror_target.parent.mkdir()
    mirror_target.write_text("mirror archive version\n", encoding="utf-8")
    _git(mirror_source, "add", "src/feature.txt")
    _git(mirror_source, "commit", "-m", "archive")
    _git(mirror_source, "branch", "archive")
    mirror = tmp_path / "archive.git"
    subprocess.run(
        ["git", "clone", "--mirror", str(mirror_source), str(mirror)],
        check=True,
        capture_output=True,
        text=True,
    )
    candidate = tmp_path / "mirror-candidate.txt"
    candidate.write_bytes(mirror_target.read_bytes())
    candidate_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
    repo_status = _git(repo, "status", "--porcelain=v1")
    mirror_refs = _git(mirror, "show-ref")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT.with_name("recover.py")),
            "query",
            "git",
            "--repo",
            str(repo),
            "--target",
            "src/feature.txt",
            "--candidate-file",
            str(candidate),
            "--candidate-sha256",
            candidate_sha,
            "--mirror",
            str(mirror),
            "--include-reflog",
            "--limit",
            "20",
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["mode"] == "dry-run"
    assert payload["candidate"]["git_reachable"] is True
    assert payload["production_files_written"] is False
    assert payload["worktree_modified"] is False
    matched = next(
        record for record in payload["records"] if record["blob_sha256"] == candidate_sha
    )
    assert {entry["role"] for entry in matched["repositories"]} == {"mirror"}
    assert any(ref["name"] == "refs/heads/archive" for ref in matched["refs"])
    assert _git(repo, "status", "--porcelain=v1") == repo_status
    assert _git(mirror, "show-ref") == mirror_refs
