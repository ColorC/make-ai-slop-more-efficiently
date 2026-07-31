from __future__ import annotations

import json

from omnicompany.packages.domains.game_observatory.proofs import PHASES, PhaseProofBuilder
from omnicompany.packages.domains.game_observatory.runtime import GameObservatory


def test_phase_proofs_match_v03_serial_gates_and_fail_closed(tmp_path):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    evidence = {
        "provenance-audit.json": {
            "schema": "game-observatory.provenance-audit.v1",
            "reports": [],
        },
        "editorial-validation.json": {"schema": "editorial", "ok": True},
        "public-site-browser-evidence.json": {
            "schema": "game-observatory.browser-quality-evidence.v2",
            "reports": {},
            "console_errors": [],
            "failed_requests": [],
            "http_errors": [],
        },
        "public-site-quality-validation.json": {
            "schema": "quality",
            "ok": False,
            "site_shell_ready": True,
            "archive_complete": False,
        },
        "afk-mumu-hero-upgrade-observation.json": {"schema": "afk", "ok": False},
        "minecraft-first-night-fire-food.json": {"schema": "minecraft", "ok": False},
        "monitor.json": {"ok": False},
        "recovery-drill.json": {"schema": "recovery", "ok": False},
    }
    for name, payload in evidence.items():
        (facility.store.export_root / name).write_text(json.dumps(payload), encoding="utf-8")

    result = PhaseProofBuilder(facility.store).build()

    assert [phase.number for phase in PHASES] == [0, 1, 2, 3, 4, 5]
    assert result["schema"] == "game-observatory.phase-proof-index.v2"
    assert result["ok"] is False
    assert result["review_ready"] is False
    assert result["review_pending"] == []
    assert result["technical_passed"] == [1]
    assert result["overall_passed"] == [1]
    assert result["phases"][3]["technical_status"] == "blocked"
    assert "AFK Journey 真实设计案未发布" in result["phases"][3]["failure_samples"]
    markdown = (facility.store.export_root / "phase-proofs" / "phase-5.md").read_text(
        encoding="utf-8"
    )
    assert "## 人工节省与有效性" in markdown
    assert "非开发者理解/使用" in markdown


def test_phase_proof_review_semantics_require_only_gate_five_human_verdict(tmp_path, monkeypatch):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    builder = PhaseProofBuilder(facility.store)
    monkeypatch.setattr(builder, "_technical_status", lambda phase: ("passed", [], []))

    result = builder.build()

    assert result["ok"] is True
    assert result["review_ready"] is True
    assert result["technical_passed"] == [0, 1, 2, 3, 4, 5]
    assert result["overall_passed"] == [0, 1, 2, 3, 4]
    assert result["review_pending"] == [5]
    assert result["phases"][4]["effectiveness_status"] == "validated_by_objective_evidence"
    assert result["phases"][5]["effectiveness_status"] == "pending_non_developer_review"
    assert result["phases"][5]["decision"] == "submit_for_review"