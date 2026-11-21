from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


CLI = Path(__file__).parents[2] / "scripts" / "session_recovery_cli.py"


def _run(*arguments: object, cwd: Path) -> dict:
    process = subprocess.run(
        [sys.executable, str(CLI), *map(str, arguments)],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "workspace"
    snapshot = tmp_path / "snapshots" / "73fd947-fixture"
    workspace.mkdir()
    snapshot.mkdir(parents=True)
    (workspace / "a.py").write_text("A = 1\n", encoding="utf-8")
    (workspace / "c.py").write_text("C = 2\n", encoding="utf-8")
    (workspace / "e.py").write_text("E = 2\n", encoding="utf-8")
    (workspace / "f.py").write_text("F = 2\n", encoding="utf-8")
    (snapshot / "a.py").write_text("A = 1\n", encoding="utf-8")
    (snapshot / "b.py").write_text("B = 1\n", encoding="utf-8")
    (snapshot / "c.py").write_text("C = 1\n", encoding="utf-8")
    (snapshot / "e.py").write_text("E = 1\n", encoding="utf-8")
    (snapshot / "f.py").write_text("F = 1\n", encoding="utf-8")
    (snapshot / "g.py").write_text("G = 1\n", encoding="utf-8")

    def entry(name: str) -> dict:
        path = snapshot / name
        import hashlib
        return {
            "path": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        }

    payload = {
        "schema": "fixture.snapshot.v1",
        "source_manifest_sha256": "73fd947-fixture",
        "files": [entry(name) for name in ("a.py", "b.py", "c.py", "e.py", "f.py", "g.py")]
        + [{"path": "d.py", "sha256": "0" * 64, "size_bytes": 4}],
    }
    manifest = snapshot / "snapshot-manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    baseline_time = time.time() - 100
    os.utime(manifest, (baseline_time, baseline_time))
    os.utime(workspace / "c.py", (baseline_time + 50, baseline_time + 50))
    os.utime(workspace / "e.py", (baseline_time - 50, baseline_time - 50))
    os.utime(workspace / "f.py", (baseline_time + 50, baseline_time + 50))

    # Unlike an mtime, this materialized session post-image has a source
    # timestamp and can prove that f.py is a real post-snapshot overlay.
    session_root = tmp_path / "ledgers"
    session_artifact = tmp_path / "f-session.py"
    session_artifact.write_text("F = 2\n", encoding="utf-8")
    import hashlib
    ledger = session_root / "recent" / "LATEST-COMPLETE-FILES.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"files": [{
        "path": str(workspace / "f.py"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(baseline_time + 50)),
        "source": "codex",
        "session_id": "fixture-session",
        "candidate_artifact": str(session_artifact),
        "candidate_sha256": hashlib.sha256(session_artifact.read_bytes()).hexdigest(),
    }]}), encoding="utf-8")
    raw_session = session_root / "sessions" / "2026" / "07" / "28" / "rollout-2026-07-28T01-00-00-fixture.jsonl"
    raw_session.parent.mkdir(parents=True)
    raw_session.write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(baseline_time + 75)),
        "payload": {
            "type": "custom_tool_call", "status": "completed", "name": "apply_patch",
            "input": f"*** Begin Patch\n*** Delete File: {workspace / 'g.py'}\n*** End Patch\n",
        },
    }) + "\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "a.py"], cwd=workspace, check=True)
    commit_env = os.environ.copy()
    commit_stamp = time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(baseline_time - 200))
    commit_env.update({"GIT_AUTHOR_DATE": commit_stamp, "GIT_COMMITTER_DATE": commit_stamp})
    subprocess.run(
        ["git", "commit", "-q", "-m", "fixture"],
        cwd=workspace,
        env=commit_env,
        check=True,
    )
    return workspace, snapshot.parent, session_root


def test_automatic_baseline_discovery_classifies_without_writes(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    plan_path = tmp_path / "plan.json"
    result = _run(
        "baseline", "scan",
        "--workspace-root", workspace,
        "--snapshot-root", snapshots,
        "--snapshot-prefix", "73fd947",
        "--session-root", sessions,
        "--workers", "99",
        "--out", plan_path,
        cwd=workspace,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    queue_by_path = {
        item["path"]: name
        for name, items in plan["queues"].items()
        for item in items
    }
    assert result["mode"] == "query-only"
    assert result["production_files_written"] is False
    assert "runnability" in result["evidence_contract"]
    assert result["resource_policy"]["workers"] == 2
    assert result["resource_policy"]["tests_or_builds_started"] is False
    assert result["inventories"]["snapshot_manifests"][0]["source_id"] == "73fd947-fixture"
    assert result["inventories"]["snapshot_manifests"][0]["complete_source_candidates"] == 6
    assert queue_by_path["a.py"] == "identical_converged"
    assert queue_by_path["b.py"] == "safe_promote"
    # A later worktree mtime alone is not provenance and cannot prove overlay.
    assert queue_by_path["c.py"] == "conflict_manual"
    assert queue_by_path["d.py"] == "missing_source"
    assert queue_by_path["e.py"] == "conflict_manual"
    assert queue_by_path["f.py"] == "post_baseline_overlay"
    assert queue_by_path["g.py"] == "intentionally_removed"
    recovered = next(item for item in plan["queues"]["safe_promote"] if item["path"] == "b.py")
    assert recovered["recovery_status"]["source_recovery"] == "exact_source_selected"
    assert recovered["recovery_status"]["current_tree_runnability"] == "not_evaluated"
    assert plan["inventories"]["session_tombstones"]["tombstones"] == 1
    efficiency = plan["recovery_efficiency"]
    assert efficiency["latest_fullest_baseline"]["source_id"] == "73fd947-fixture"
    assert efficiency["latest_fullest_baseline"]["complete_baseline"] is False
    assert efficiency["paths_considered"] == 7
    assert efficiency["recommended_next_batches"][0]["queue"] == "safe_promote"
    assert "chain" not in next(item for item in plan["queues"]["safe_promote"] if item["path"] == "b.py")
    assert not (workspace / "b.py").exists()


def test_git_clean_filter_converges_crlf_worktree_with_lf_git_blob(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=workspace, check=True)
    (workspace / "a.py").write_bytes(b"A = 1\r\n")
    assert subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", "a.py"], cwd=workspace, check=False,
    ).returncode == 0

    plan_path = tmp_path / "crlf-plan.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--path-prefix", "a.py", "--out", plan_path, cwd=workspace,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    converged = {item["path"] for item in plan["queues"]["identical_converged"]}
    assert "a.py" in converged
    assert plan["inventories"]["git_worktree_comparison"]["changed_tracked_paths"] == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows path identity contract")
def test_windows_case_aliases_collapse_onto_git_path(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    artifact = tmp_path / "case-a.py"
    artifact.write_text("A = 1\n", encoding="utf-8")
    import hashlib
    ledger = sessions / "case-alias" / "LATEST-COMPLETE-FILES.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"files": [{
        "path": "A.py",
        "timestamp": "2026-08-01T00:00:00+00:00",
        "session_id": "case-alias-session",
        "candidate_artifact": str(artifact),
        "candidate_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }]}), encoding="utf-8")
    plan_path = tmp_path / "case-plan.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--session-ledgers", "always",
        "--include-unreferenced-git", "--out", plan_path, cwd=workspace,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    all_paths = [item["path"] for rows in plan["queues"].values() for item in rows]
    assert [path for path in all_paths if path.casefold() == "a.py"] == ["a.py"]
    assert plan["inventories"]["path_identity"]["case_aliases_merged"] >= 1


def test_auto_defers_session_walk_only_for_byte_converged_snapshot_cohort(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    snapshot = snapshots / "73fd947-fixture"
    contents = {
        "a.py": "A = 1\n", "b.py": "B = 1\n", "c.py": "C = 1\n",
        "d.py": "D = 1\n", "e.py": "E = 1\n", "f.py": "F = 1\n", "g.py": "G = 1\n",
    }
    import hashlib
    for name, value in contents.items():
        (snapshot / name).write_text(value, encoding="utf-8")
        (workspace / name).write_text(value, encoding="utf-8")
    manifest = snapshot / "snapshot-manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["files"] = [
        {
            "path": name,
            "sha256": hashlib.sha256((snapshot / name).read_bytes()).hexdigest(),
            "size_bytes": (snapshot / name).stat().st_size,
        }
        for name in contents
    ]
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    deferred_path = tmp_path / "deferred.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--out", deferred_path, cwd=workspace,
    )
    deferred = json.loads(deferred_path.read_text(encoding="utf-8"))
    inventory = deferred["inventories"]
    discovery = inventory["session_ledger_discovery"]
    assert discovery["mode"] == "deferred_fast_snapshot_converged"
    assert discovery["snapshot_paths_checked"] == len(contents)
    assert discovery["snapshot_paths_matching_worktree"] == len(contents)
    # The fixture contains both a complete-file ledger and a later raw delete,
    # but neither is read in this bounded fast phase.  It is explicitly not a
    # claim that those later session sources do not exist.
    assert discovery["complete_file_recovery"] == "not_evaluated"
    assert inventory["session_ledgers"] == []
    assert inventory["session_tombstones"]["state"] == "deferred_fast_snapshot_converged"
    assert deferred["queue_counts"]["intentionally_removed"] == 0

    forced_path = tmp_path / "forced.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--session-ledgers", "always", "--out", forced_path, cwd=workspace,
    )
    forced = json.loads(forced_path.read_text(encoding="utf-8"))
    assert forced["inventories"]["session_ledger_discovery"]["mode"] == "completed"
    assert len(forced["inventories"]["session_ledgers"]) == 1
    assert forced["inventories"]["session_tombstones"]["tombstones"] == 1


def test_apply_is_dry_run_then_hash_guarded_and_journaled(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    plan_path = tmp_path / "plan.json"
    _run(
        "baseline", "scan",
        "--workspace-root", workspace,
        "--snapshot-root", snapshots,
        "--snapshot-prefix", "73fd947",
        "--session-root", sessions,
        "--out", plan_path,
        cwd=workspace,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    selected = next(item["selected"] for item in plan["queues"]["safe_promote"] if item["path"] == "b.py")
    frozen = Path(selected["frozen_artifact"])
    assert frozen.is_file()
    (snapshots / "73fd947-fixture" / "b.py").unlink()
    snapshot_root = tmp_path / "pre-apply"
    dry_run = _run(
        "baseline", "apply", "--plan", plan_path,
        "--workspace-root", workspace,
        "--confirm-workspace", workspace,
        "--snapshot-root", snapshot_root,
        cwd=workspace,
    )
    assert dry_run["mode"] == "dry-run"
    assert dry_run["safe_promote_actions"] == 1
    assert not (workspace / "b.py").exists()

    applied = _run(
        "baseline", "apply", "--plan", plan_path,
        "--workspace-root", workspace,
        "--confirm-workspace", workspace,
        "--snapshot-root", snapshot_root,
        "--apply",
        cwd=workspace,
    )
    assert applied["applied"] == 1
    assert (workspace / "b.py").read_text(encoding="utf-8") == "B = 1\n"
    assert Path(applied["pre_apply_snapshot"]).is_file()
    journal = Path(applied["journal"]).read_text(encoding="utf-8")
    assert '"event": "prepared"' in journal
    assert '"event": "applied"' in journal


def test_apply_materializes_a_frozen_git_blob_without_checkout(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    git_only = workspace / "git-only.py"
    git_only.write_text("VALUE = 'from-git'\n", encoding="utf-8")
    subprocess.run(["git", "add", "git-only.py"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "git post-image"], cwd=workspace, check=True)
    git_only.unlink()

    plan_path = tmp_path / "git-plan.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--include-unreferenced-git", "--out", plan_path, cwd=workspace,
    )
    dry_run = _run(
        "baseline", "apply", "--plan", plan_path,
        "--workspace-root", workspace, "--confirm-workspace", workspace,
        "--path-prefix", "git-only.py", cwd=workspace,
    )
    assert dry_run["safe_promote_actions"] == 1
    assert dry_run["actions"][0]["candidate"] is None
    assert dry_run["actions"][0]["candidate_source"] == "git-head"

    applied = _run(
        "baseline", "apply", "--plan", plan_path,
        "--workspace-root", workspace, "--confirm-workspace", workspace,
        "--path-prefix", "git-only.py", "--snapshot-root", tmp_path / "pre-apply",
        "--apply", cwd=workspace,
    )
    assert applied["applied"] == 1
    assert git_only.read_text(encoding="utf-8") == "VALUE = 'from-git'\n"


def test_apply_path_prefix_is_an_explicit_allow_list(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    package = snapshots / "73fd947-fixture" / "package"
    package.mkdir()
    candidate = package / "missing.py"
    candidate.write_text("VALUE = 7\n", encoding="utf-8")
    import hashlib
    manifest = snapshots / "73fd947-fixture" / "snapshot-manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["files"].append({
        "path": "package/missing.py",
        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "size_bytes": candidate.stat().st_size,
    })
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    _run(
        "baseline", "scan",
        "--workspace-root", workspace,
        "--snapshot-root", snapshots,
        "--snapshot-prefix", "73fd947",
        "--session-root", sessions,
        "--out", plan_path,
        cwd=workspace,
    )
    dry_run = _run(
        "baseline", "apply", "--plan", plan_path,
        "--workspace-root", workspace,
        "--confirm-workspace", workspace,
        "--path-prefix", "package",
        cwd=workspace,
    )
    assert dry_run["path_prefixes"] == ["package"]
    assert dry_run["safe_promote_actions"] == 1
    assert dry_run["actions"][0]["path"] == "package/missing.py"


def test_missing_module_with_extant_tsx_sibling_requires_review(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    (workspace / "panel.tsx").write_text("export default 1\n", encoding="utf-8")
    candidate = snapshots / "73fd947-fixture" / "panel.ts"
    candidate.write_text("export default 2\n", encoding="utf-8")
    import hashlib
    manifest = snapshots / "73fd947-fixture" / "snapshot-manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["files"].append({
        "path": "panel.ts", "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "size_bytes": candidate.stat().st_size,
    })
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--out", plan_path, cwd=workspace,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert any(item["path"] == "panel.ts" for item in plan["queues"]["module_shadowed_manual"])


def test_tombstone_cache_reuses_unchanged_raw_transcript(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    cache_path = tmp_path / "tombstone-cache.json"
    first_plan = tmp_path / "first.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--tombstone-cache", cache_path,
        "--write-tombstone-cache", "--out", first_plan, cwd=workspace,
    )
    assert cache_path.is_file()
    first = json.loads(first_plan.read_text(encoding="utf-8"))
    assert first["inventories"]["session_tombstones"]["cache_misses"] >= 1
    second_plan = tmp_path / "second.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--tombstone-cache", cache_path,
        "--out", second_plan, cwd=workspace,
    )
    second = json.loads(second_plan.read_text(encoding="utf-8"))
    inventory = second["inventories"]["session_tombstones"]
    assert inventory["cache_hits"] >= 1
    assert inventory["tombstones"] == 1


def test_explicit_tombstone_include_extends_but_does_not_replace_bounded_scan(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    extra = tmp_path / "extra-session.jsonl"
    extra.write_text(json.dumps({
        "timestamp": "2026-08-01T00:00:00Z",
        "payload": {
            "type": "custom_tool_call", "status": "completed", "name": "apply_patch",
            "input": "*** Begin Patch\n*** Delete File: x.py\n*** End Patch\n",
        },
    }) + "\n", encoding="utf-8")
    plan_path = tmp_path / "explicit-tombstone.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--raw-tombstone-include", extra,
        "--out", plan_path, cwd=workspace,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    inventory = plan["inventories"]["session_tombstones"]
    assert str(extra.resolve()) in inventory["explicit_includes"]
    assert inventory["candidate_sessions"] >= 2


def test_progress_file_is_evidence_only(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    progress_path = tmp_path / "progress.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--progress-file", progress_path, cwd=workspace,
    )
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["production_files_written"] is False
    assert progress["last_completed_phase"] == "classification"


def test_path_prefix_bounds_discovery_batch(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    plan_path = tmp_path / "bounded.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--path-prefix", "a.py", "--out", plan_path, cwd=workspace,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["path_prefixes"] == ["a.py"]
    assert {item["path"] for entries in plan["queues"].values() for item in entries} == {"a.py"}


def test_volatile_e2e_and_hashed_runtime_assets_do_not_become_promotions(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    snapshot = snapshots / "73fd947-fixture"
    report = snapshot / "frontend" / "test-results" / "case" / "error-context.md"
    runtime = snapshot / "static" / "assets" / "Editor-C7W5eJO8.css"
    report.parent.mkdir(parents=True)
    runtime.parent.mkdir(parents=True)
    report.write_text("temporary E2E context\n", encoding="utf-8")
    runtime.write_text(".old-build{}\n", encoding="utf-8")
    import hashlib
    manifest = snapshot / "snapshot-manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    for candidate in (report, runtime):
        payload["files"].append({
            "path": candidate.relative_to(snapshot).as_posix(),
            "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "size_bytes": candidate.stat().st_size,
        })
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    plan_path = tmp_path / "volatile.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--out", plan_path, cwd=workspace,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    discovered = {item["path"] for rows in plan["queues"].values() for item in rows}
    assert report.relative_to(snapshot).as_posix() not in discovered
    assert runtime.relative_to(snapshot).as_posix() not in discovered


def test_one_off_dashboard_probe_is_retained_without_promotion(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    candidate = snapshots / "73fd947-fixture" / "src" / "omnicompany" / "dashboard" / "frontend" / ".audit-debug.mjs"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("console.log('one-off')\n", encoding="utf-8")
    import hashlib
    manifest = snapshots / "73fd947-fixture" / "snapshot-manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["files"].append({
        "path": candidate.relative_to(snapshots / "73fd947-fixture").as_posix(),
        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "size_bytes": candidate.stat().st_size,
    })
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    plan_path = tmp_path / "probe.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--out", plan_path, cwd=workspace,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert any(item["path"].endswith(".audit-debug.mjs") for item in plan["queues"]["ephemeral_evidence"])


def test_ephemeral_session_post_image_does_not_wait_for_tombstone_scan(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    artifact = tmp_path / "e2e-session.mjs"
    artifact.write_text("console.log('probe')\n", encoding="utf-8")
    import hashlib
    ledger = sessions / "unscanned" / "LATEST-COMPLETE-FILES.json"
    ledger.parent.mkdir(parents=True)
    probe_path = workspace / "src" / "omnicompany" / "dashboard" / "frontend" / "_e2e-session.mjs"
    ledger.write_text(json.dumps({"files": [{
        "path": str(probe_path),
        "timestamp": "2026-08-01T00:00:00+00:00",
        "source": "codex",
        "session_id": "not-in-bounded-tombstone-window",
        "candidate_artifact": str(artifact),
        "candidate_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }]}), encoding="utf-8")
    plan_path = tmp_path / "ephemeral-session-plan.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--session-ledgers", "always", "--out", plan_path, cwd=workspace,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    relative = "src/omnicompany/dashboard/frontend/_e2e-session.mjs"
    assert any(item["path"] == relative for item in plan["queues"]["ephemeral_evidence"])
    assert not any(item["path"] == relative for item in plan["queues"]["awaiting_tombstone_scan"])


def test_ledger_cache_reuses_unchanged_materialized_ledger(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    cache_path = tmp_path / "ledger-cache.json"
    first_plan = tmp_path / "ledger-first.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--ledger-cache", cache_path,
        "--write-ledger-cache", "--out", first_plan, cwd=workspace,
    )
    assert cache_path.is_file()
    first = json.loads(first_plan.read_text(encoding="utf-8"))
    assert first["inventories"]["session_ledger_cache"]["misses"] >= 1
    second_plan = tmp_path / "ledger-second.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--ledger-cache", cache_path,
        "--out", second_plan, cwd=workspace,
    )
    second = json.loads(second_plan.read_text(encoding="utf-8"))
    assert second["inventories"]["session_ledger_cache"]["hits"] >= 1


def test_ledger_cache_does_not_cross_workspace_normalization(tmp_path: Path) -> None:
    workspace, snapshots, sessions = _fixture(tmp_path)
    cache_path = tmp_path / "ledger-cache.json"
    _run(
        "baseline", "scan", "--workspace-root", workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--ledger-cache", cache_path,
        "--write-ledger-cache", cwd=workspace,
    )
    other_workspace = tmp_path / "other-workspace"
    other_workspace.mkdir()
    other_plan = tmp_path / "other-plan.json"
    _run(
        "baseline", "scan", "--workspace-root", other_workspace,
        "--snapshot-root", snapshots, "--snapshot-prefix", "73fd947",
        "--session-root", sessions, "--ledger-cache", cache_path, "--out", other_plan, cwd=other_workspace,
    )
    other = json.loads(other_plan.read_text(encoding="utf-8"))
    # The command has a cache miss rather than reusing paths normalized to the
    # first workspace.
    assert other["inventories"]["session_ledger_cache"]["hits"] == 0


def test_provider_closure_detects_a_recovered_provider_missing_registry_wiring(tmp_path: Path) -> None:
    """Provider files alone are not a usable recovered ChatUI integration."""
    workspace = tmp_path / "workspace"
    chatui = workspace / "src" / "omnicompany" / "dashboard" / "chatui"
    provider = chatui / "server" / "modules" / "providers" / "list" / "alpha"
    provider.mkdir(parents=True)
    (provider / "alpha.provider.ts").write_text("export class AlphaProvider {}\n", encoding="utf-8")
    required = {
        "server/shared/types.ts": "export type LLMProvider = 'alpha';\n",
        "src/types/app.ts": "export type LLMProvider = 'alpha';\n",
        "server/modules/providers/provider.registry.ts": "  alpha: new AlphaProvider(),\n",
        "server/modules/providers/provider.routes.ts": "normalized === 'alpha'\n",
        "server/modules/providers/services/provider-capabilities.service.ts": "  alpha: { provider: 'alpha' },\n",
        "server/modules/providers/services/session-synchronizer.service.ts": "  alpha: 0,\n",
        "src/components/llm-logo-provider/SessionProviderLogo.tsx": "if (provider === 'alpha') return null;\n",
    }
    for relative, content in required.items():
        path = chatui / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    command = [sys.executable, str(CLI), "baseline", "closure", "--workspace-root", str(workspace)]
    healthy = subprocess.run(command, cwd=workspace, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert healthy.returncode == 0, healthy.stderr
    payload = json.loads(healthy.stdout)
    assert payload["closure_passed"] is True
    assert payload["evidence_classification"]["complete_file_recovery"] == "not_evaluated"
    assert payload["evidence_classification"]["post_recovery_runnability"] == "passed"

    registry = chatui / "server/modules/providers/provider.registry.ts"
    registry.write_text("", encoding="utf-8")
    broken = subprocess.run(command, cwd=workspace, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    payload = json.loads(broken.stdout)
    assert broken.returncode == 1
    assert {item["integration_point"] for item in payload["missing"]} == {"registry"}
