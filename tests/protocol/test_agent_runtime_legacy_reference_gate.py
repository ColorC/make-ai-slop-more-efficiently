from __future__ import annotations

from pathlib import Path
import json

from scripts.agent_runtime_legacy_reference_gate import check_baseline, scan


def test_scanner_counts_semantic_consumers_and_separates_authority(tmp_path: Path) -> None:
    source = tmp_path / "src" / "omnicompany"
    consumer = source / "feature.py"
    authority = source / "packages" / "services" / "_core" / "agent" / "loop.py"
    graveyard = source / "_graveyard" / "old.py"
    for path in (consumer, authority, graveyard):
        path.parent.mkdir(parents=True, exist_ok=True)
    consumer.write_text(
        "from x import AgentNodeLoop, AgentNodeLoopWithRetry, run_json_agent\n"
        "class A(AgentNodeLoop): pass\n"
        "class B(AgentNodeLoopWithRetry): pass\n"
        "run_json_agent(task='x')\n",
        encoding="utf-8",
    )
    authority.write_text(
        "class AgentNodeLoop: pass\nAgentNodeLoop()\n",
        encoding="utf-8",
    )
    graveyard.write_text("run_json_agent()\n", encoding="utf-8")

    report = scan(source)

    assert report["consumer_reference_count"] == 6
    assert report["consumer_file_count"] == 1
    assert report["authority_reference_count"] == 1
    assert report["authority_file_count"] == 1
    assert report["parse_errors"] == []


def test_active_repository_does_not_grow_legacy_agent_dependencies() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    baseline = json.loads(
        (
            repo_root
            / "contracts"
            / "agent-runtime"
            / "v0"
            / "legacy-reference-baseline.json"
        ).read_text(encoding="utf-8")
    )

    report = scan(repo_root / "src" / "omnicompany")

    assert check_baseline(report, baseline) == []
