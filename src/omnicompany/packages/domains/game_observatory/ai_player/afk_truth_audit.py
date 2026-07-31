"""Read-only, fail-closed audit for the AFK stage-1 human-truth gate.

This module does not adjudicate labels, register reviewers, sign truth, or write the
canonical store.  It joins already-frozen candidate artifacts into an exact review
inventory so recognition defects and missing human-truth facilities cannot be
collapsed into one vague blocker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "game-observatory.ai-player.afk-stage1-truth-audit.v1"
AFK_GAME_ID = "afk-journey"
CANDIDATE_LABEL_STATUS = "candidate_unreviewed"


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _load_pinned_json(path: Path, expected_sha256: str) -> tuple[dict[str, Any], str]:
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"detached expected hash does not match {path}: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload, actual_sha256


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _nearest_rank(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def build_afk_stage1_truth_audit(
    *,
    candidate_manifest_path: Path,
    candidate_manifest_sha256: str,
    candidate_validation_path: Path,
    candidate_validation_sha256: str,
    afk_adjudication_path: Path,
    afk_adjudication_sha256: str,
    holdout_fixture_path: Path,
    holdout_fixture_sha256: str,
    holdout_labels_path: Path,
    holdout_labels_sha256: str,
    holdout_result_path: Path,
    holdout_result_sha256: str,
    public_result_path: Path,
    public_result_sha256: str,
) -> dict[str, Any]:
    """Build a deterministic audit without mutating any input or database."""

    candidate, candidate_hash = _load_pinned_json(
        candidate_manifest_path, candidate_manifest_sha256
    )
    validation, validation_hash = _load_pinned_json(
        candidate_validation_path, candidate_validation_sha256
    )
    afk_adjudication, adjudication_hash = _load_pinned_json(
        afk_adjudication_path, afk_adjudication_sha256
    )
    fixture, fixture_hash = _load_pinned_json(
        holdout_fixture_path, holdout_fixture_sha256
    )
    labels, labels_hash = _load_pinned_json(holdout_labels_path, holdout_labels_sha256)
    holdout_result, result_hash = _load_pinned_json(
        holdout_result_path, holdout_result_sha256
    )
    public_result, public_hash = _load_pinned_json(
        public_result_path, public_result_sha256
    )

    _require(candidate.get("benchmark_id") == "afk_hero_growth_v1", "wrong benchmark")
    _require(candidate.get("semantic_status") == "candidate", "candidate status drifted")
    _require(candidate.get("frozen") is False, "candidate falsely claims frozen truth")
    _require(candidate.get("freeze_pass") is False, "candidate falsely passes freeze")
    _require(
        candidate.get("human_truth_signature") is None,
        "candidate unexpectedly contains a human truth signature",
    )
    _require(
        validation.get("manifest_id") == candidate.get("id"),
        "candidate validation points to a different manifest",
    )
    _require(
        validation.get("candidate_structure_pass") is True,
        "candidate structure is not currently valid",
    )
    _require(validation.get("freeze_pass") is False, "validation falsely passes freeze")
    _require(not validation.get("errors"), "candidate validation has structural errors")
    candidate_adjudication_source = candidate.get("source", {}).get(
        "adjudication_fixture", {}
    )
    _require(
        candidate_adjudication_source.get("sha256") == adjudication_hash,
        "candidate adjudication hash drifted",
    )
    _require(
        afk_adjudication.get("human_truth_status") == "not_signed",
        "AFK adjudication unexpectedly claims human truth",
    )
    adjudicated_state_by_artifact: dict[str, str] = {}
    for state in afk_adjudication.get("observed_states", []):
        for artifact_id in state.get("artifact_ids", []):
            _require(
                artifact_id not in adjudicated_state_by_artifact,
                f"artifact has conflicting AFK adjudications: {artifact_id}",
            )
            adjudicated_state_by_artifact[artifact_id] = str(state["id"])

    _require(labels.get("fixture_sha256") == fixture_hash, "labels fixture hash drifted")
    adjudication = labels.get("adjudication", {})
    _require(
        adjudication.get("status") == CANDIDATE_LABEL_STATUS,
        "only the committed unreviewed candidate labels may be audited here",
    )
    _require(adjudication.get("reviewer") is None, "candidate labels name a reviewer")
    _require(adjudication.get("signed_at") is None, "candidate labels claim signing")
    _require(adjudication.get("attestation") is None, "candidate labels claim attestation")

    _require(
        holdout_result.get("fixture_sha256") == fixture_hash,
        "holdout result fixture hash drifted",
    )
    _require(
        holdout_result.get("labels_sha256") == labels_hash,
        "holdout result labels hash drifted",
    )
    _require(
        holdout_result.get("facility_status") == "PASS",
        "real-image holdout facility is not PASS",
    )
    _require(
        holdout_result.get("g02_real_image_holdout_status") == "FAIL",
        "this audit must not silently consume a changed G-02 verdict",
    )
    _require(
        public_result.get("status") == "PASS",
        "public state-counterexample benchmark is not PASS",
    )

    samples = {item["id"]: item for item in fixture.get("samples", [])}
    _require(len(samples) == len(fixture.get("samples", [])), "duplicate fixture sample id")
    all_labels = [*labels.get("reference_labels", []), *labels.get("holdout_labels", [])]
    _require(
        len({item["sample_id"] for item in all_labels}) == len(all_labels),
        "sample is labelled more than once",
    )
    for item in all_labels:
        _require(item["sample_id"] in samples, f"label sample is missing: {item['sample_id']}")
    correction_source = labels.get("candidate_correction_source")
    excluded_evidence_conflicts: list[dict[str, Any]] = []
    if correction_source is not None:
        _require(
            isinstance(correction_source, dict),
            "candidate correction source must be an object",
        )
        _require(
            correction_source.get("sha256") == adjudication_hash,
            "candidate correction source hash drifted from AFK adjudication",
        )
        _require(
            correction_source.get("human_truth_status") == "not_signed",
            "candidate correction source unexpectedly claims human truth",
        )
        excluded_evidence_conflicts = list(correction_source.get("corrections", []))
        _require(
            len(excluded_evidence_conflicts) == 6,
            "candidate correction source must account for six contradicted labels",
        )
        labelled_sample_ids = {item["sample_id"] for item in all_labels}
        for correction in excluded_evidence_conflicts:
            sample_id = str(correction.get("sample_id"))
            artifact_id = str(correction.get("artifact_id"))
            _require(sample_id in samples, f"corrected sample is missing: {sample_id}")
            _require(
                samples[sample_id]["screenshot"]["artifact_id"] == artifact_id,
                f"corrected sample artifact drifted: {sample_id}",
            )
            _require(
                correction.get("adjudicated_state_id")
                == adjudicated_state_by_artifact.get(artifact_id),
                f"corrected state drifted from AFK adjudication: {sample_id}",
            )
            _require(
                correction.get("disposition")
                == "excluded-unpaired-semantic-state",
                f"contradicted sample was not explicitly excluded: {sample_id}",
            )
            _require(
                sample_id not in labelled_sample_ids,
                f"excluded contradicted sample remains labelled: {sample_id}",
            )

    afk_reference_labels = [
        item
        for item in labels.get("reference_labels", [])
        if samples[item["sample_id"]]["game_id"] == AFK_GAME_ID
    ]
    afk_holdout_labels = [
        item
        for item in labels.get("holdout_labels", [])
        if samples[item["sample_id"]]["game_id"] == AFK_GAME_ID
    ]
    result_by_sample = {
        item["sample_id"]: item for item in holdout_result.get("sample_results", [])
    }
    _require(
        {item["sample_id"] for item in afk_holdout_labels}
        == {
            sample_id
            for sample_id in result_by_sample
            if samples.get(sample_id, {}).get("game_id") == AFK_GAME_ID
        },
        "AFK holdout labels and evaluated samples differ",
    )

    policy = holdout_result.get("policy", {})
    match_threshold = float(policy["match_threshold"])
    ambiguity_margin = float(policy["ambiguity_margin"])
    review_rows: list[dict[str, Any]] = []
    confusion_pairs: Counter[tuple[str, str]] = Counter()
    margins: list[float] = []
    best_score_pass_count = 0
    expected_is_top_count = 0

    for label in sorted(afk_holdout_labels, key=lambda item: item["sample_id"]):
        sample = samples[label["sample_id"]]
        result = result_by_sample[label["sample_id"]]
        ranked = result.get("ranked_matches", [])
        _require(len(ranked) >= 2, f"ranked matches are incomplete: {label['sample_id']}")
        best, runner_up = ranked[:2]
        best_score = float(best["score"])
        runner_up_score = float(runner_up["score"])
        margin = best_score - runner_up_score
        margins.append(margin)
        if best_score >= match_threshold:
            best_score_pass_count += 1
        expected_is_top = best["state_id"] == label["semantic_state_id"]
        if expected_is_top:
            expected_is_top_count += 1
        confusion_pairs[(str(best["state_id"]), str(runner_up["state_id"]))] += 1
        _require(
            result.get("candidate_expected_state_id") == label["semantic_state_id"],
            f"result expected label drifted: {label['sample_id']}",
        )
        screenshot_path = Path(sample["screenshot"]["canonical_path"])
        _require(screenshot_path.is_file(), f"screenshot is missing: {screenshot_path}")
        _require(
            _sha256_file(screenshot_path) == sample["screenshot"]["sha256"],
            f"screenshot hash drifted: {sample['screenshot']['artifact_id']}",
        )
        adjudicated_state_id = adjudicated_state_by_artifact.get(
            sample["screenshot"]["artifact_id"]
        )
        known_label_conflict = (
            adjudicated_state_id is not None
            and adjudicated_state_id != label["semantic_state_id"]
        )
        review_rows.append(
            {
                "sample_id": label["sample_id"],
                "source_family_id": sample["source_family_id"],
                "evidence_run_id": sample["evidence_run_id"],
                "evidence_step_id": sample["evidence_step_id"],
                "evidence_role": sample["evidence_role"],
                "screenshot_artifact_id": sample["screenshot"]["artifact_id"],
                "screenshot_path": str(screenshot_path),
                "candidate_expected_state_id": label["semantic_state_id"],
                "evidence_adjudicated_state_id": adjudicated_state_id,
                "known_candidate_label_conflict": known_label_conflict,
                "best_state_id": best["state_id"],
                "best_score": round(best_score, 8),
                "runner_up_state_id": runner_up["state_id"],
                "runner_up_score": round(runner_up_score, 8),
                "best_score_pass": best_score >= match_threshold,
                "expected_is_top_candidate": expected_is_top,
                "ambiguity_margin": round(margin, 8),
                "ambiguity_margin_pass": margin >= ambiguity_margin,
                "predicted_state_id": result.get("predicted_state_id"),
                "disposition": result.get("disposition"),
            }
        )

    _require(review_rows, "AFK holdout is empty")
    coverage = holdout_result["independent_adjudication"]["holdout_coverage_by_game"][
        AFK_GAME_ID
    ]
    unique_holdout_states = len(
        {item["semantic_state_id"] for item in afk_holdout_labels}
    )
    _require(
        coverage["candidate_holdout_samples"] == len(afk_holdout_labels),
        "AFK holdout coverage count drifted",
    )
    _require(
        coverage["candidate_holdout_unique_states"] == unique_holdout_states,
        "AFK holdout state coverage drifted",
    )
    _require(
        holdout_result["g02_gate"]["by_game"][AFK_GAME_ID][
            "measured_independent_accuracy"
        ]
        is None,
        "unreviewed labels unexpectedly produced independent accuracy",
    )

    candidate_counts = candidate["counts"]
    known_label_conflicts = [
        item for item in review_rows if item["known_candidate_label_conflict"]
    ]
    blocker_ids = list(dict.fromkeys(validation.get("freeze_blockers", [])))
    confusion_summary = [
        {
            "best_state_id": best,
            "runner_up_state_id": runner_up,
            "sample_count": count,
        }
        for (best, runner_up), count in sorted(
            confusion_pairs.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    by_expected_state: list[dict[str, Any]] = []
    for state_id in sorted({item["candidate_expected_state_id"] for item in review_rows}):
        state_rows = [
            item for item in review_rows if item["candidate_expected_state_id"] == state_id
        ]
        by_expected_state.append(
            {
                "candidate_expected_state_id": state_id,
                "sample_count": len(state_rows),
                "expected_is_top1_count": sum(
                    item["expected_is_top_candidate"] for item in state_rows
                ),
                "best_score_pass_count": sum(
                    item["best_score_pass"] for item in state_rows
                ),
                "ambiguity_margin_pass_count": sum(
                    item["ambiguity_margin_pass"] for item in state_rows
                ),
                "observed_top1_state_ids": sorted(
                    {str(item["best_state_id"]) for item in state_rows}
                ),
            }
        )
    facility_missing_exit_conditions: list[str] = []
    truth_unsigned_exit_conditions = [
        "由已登记的人类审查者逐项复核 AFK 候选及原始证据，并签发裁决；AI 不参与最终真值决定。",
        "独立签发 AFK 真实图像 reference/holdout 标签，固定下限为 20 个 holdout、10 个状态和 1 个关键状态。",
        "签发后重新评估：独立准确率不低于 97%，关键错误合并为 0，总体错误合并率和过度拆分率均不高于 3%。",
    ]
    if excluded_evidence_conflicts:
        truth_unsigned_exit_conditions.insert(
            0,
            "六个已被证据否定的旧端点标签已显式排除；必须新增至少 2 个来源家族隔离且标签清洁的 AFK holdout，不能降低 20 样本门。",
        )
    else:
        truth_unsigned_exit_conditions.insert(
            0,
            "先纠正或排除审定材料已明确否定的 6 个候选 holdout 标签，禁止把旧的 AI 路线端点元数据直接签成真值。",
        )
    recognition_exit_conditions = [
        "保持固定 match_threshold=0.90 与 ambiguity_margin=0.03，不通过调低阈值消除失败。",
        "在独立真值签发后修复或增强识别特征，使 AFK holdout 中满足分数门且 top1-top2 margin 达标的样本达到正式 G-02 指标。",
        "对每个仍返回 null 或误分类的样本保留本审计中的 evidence step、原图和候选分布，逐例复核。",
    ]

    return {
        "schema": SCHEMA,
        "status": "FAIL",
        "benchmark_id": "afk_hero_growth_v1",
        "scope": "read_only_candidate_and_holdout_audit",
        "inputs": {
            "candidate_manifest": {
                "path": candidate_manifest_path.as_posix(),
                "sha256": candidate_hash,
            },
            "candidate_validation": {
                "path": candidate_validation_path.as_posix(),
                "sha256": validation_hash,
            },
            "afk_evidence_adjudication": {
                "path": afk_adjudication_path.as_posix(),
                "sha256": adjudication_hash,
                "human_truth_status": afk_adjudication["human_truth_status"],
            },
            "holdout_fixture": {
                "path": holdout_fixture_path.as_posix(),
                "sha256": fixture_hash,
            },
            "holdout_labels": {
                "path": holdout_labels_path.as_posix(),
                "sha256": labels_hash,
            },
            "holdout_result": {
                "path": holdout_result_path.as_posix(),
                "sha256": result_hash,
            },
            "public_counterexample_result": {
                "path": public_result_path.as_posix(),
                "sha256": public_hash,
            },
        },
        "facility_status": {
            "candidate_structure": "PASS",
            "public_500_state_counterexamples": "PASS",
            "real_image_holdout_evaluator": "PASS",
            "human_truth_import_and_freeze_path": "PASS",
            "overall_stage1_truth_gate": "FAIL",
        },
        "human_truth_facility": {
            "review_contract": "game-observatory.ai-player.afk-human-truth-review.v1",
            "attestation_contract": (
                "game-observatory.ai-player.afk-human-truth-attestation.v1"
            ),
            "import_contract": "game-observatory.ai-player.afk-human-truth-import.v1",
            "frozen_manifest_contract": (
                "game-observatory.ai-player.afk-frozen-truth-manifest.v1"
            ),
            "required_reviewer_kind": "human_reviewer",
            "signature_algorithm": "ed25519",
            "production_registry_status": "unconfigured",
            "live_human_review_present": False,
            "live_freeze_present": False,
        },
        "candidate_truth_inventory": {
            "states": candidate_counts["state_candidates"],
            "safe_edges": candidate_counts["safe_edge_candidates"],
            "objects": candidate_counts["object_candidates"],
            "routes": candidate_counts["route_fixtures"],
            "controlled_interruptions": candidate_counts[
                "controlled_interruption_fixtures"
            ],
            "boundaries": candidate_counts["boundary_classes"],
            "frozen_items": candidate_counts["frozen_items"],
            "human_truth_signatures": candidate_counts["human_truth_signatures"],
            "freeze_blockers": blocker_ids,
        },
        "afk_holdout_truth": {
            "candidate_reference_labels": len(afk_reference_labels),
            "candidate_holdout_labels": len(afk_holdout_labels),
            "candidate_holdout_unique_states": unique_holdout_states,
            "candidate_holdout_critical_states": coverage[
                "candidate_holdout_critical_states"
            ],
            "independently_signed_holdout_labels": coverage[
                "independently_signed_holdout_samples"
            ],
            "measured_independent_accuracy": None,
            "adjudication_status": adjudication["status"],
            "known_candidate_label_conflict_count": len(known_label_conflicts),
            "known_candidate_label_conflicts": [
                {
                    "sample_id": item["sample_id"],
                    "screenshot_artifact_id": item["screenshot_artifact_id"],
                    "candidate_expected_state_id": item["candidate_expected_state_id"],
                    "evidence_adjudicated_state_id": item[
                        "evidence_adjudicated_state_id"
                    ],
                    "evidence_step_id": item["evidence_step_id"],
                }
                for item in known_label_conflicts
            ],
            "excluded_evidence_conflict_count": len(excluded_evidence_conflicts),
            "excluded_evidence_conflicts": excluded_evidence_conflicts,
        },
        "recognition_diagnostic": {
            "sample_count": len(review_rows),
            "best_score_at_or_above_threshold_count": best_score_pass_count,
            "best_score_below_threshold_count": len(review_rows) - best_score_pass_count,
            "candidate_expected_is_top1_count": expected_is_top_count,
            "candidate_expected_not_top1_count": len(review_rows) - expected_is_top_count,
            "ambiguity_margin_pass_count": sum(
                item["ambiguity_margin_pass"] for item in review_rows
            ),
            "null_prediction_count": sum(
                item["predicted_state_id"] is None for item in review_rows
            ),
            "match_threshold": match_threshold,
            "required_ambiguity_margin": ambiguity_margin,
            "observed_margin_min": round(min(margins), 8),
            "observed_margin_median": round(statistics.median(margins), 8),
            "observed_margin_p95": round(_nearest_rank(margins, 0.95), 8),
            "observed_margin_max": round(max(margins), 8),
            "top_confusion_pairs": confusion_summary,
            "by_candidate_expected_state": by_expected_state,
            "samples": review_rows,
        },
        "separate_exit_conditions": {
            "facility_missing": facility_missing_exit_conditions,
            "truth_unsigned": truth_unsigned_exit_conditions,
            "recognition_ambiguity": recognition_exit_conditions,
        },
        "prohibitions": [
            "本审计器不导入、不裁决、不签发人类真值。",
            "本审计器不修改阈值、fixture、labels 或 canonical SQLite。",
            "候选一致率不得作为独立准确率。",
        ],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "candidate-manifest",
        "candidate-validation",
        "afk-adjudication",
        "holdout-fixture",
        "holdout-labels",
        "holdout-result",
        "public-result",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_afk_stage1_truth_audit(
        candidate_manifest_path=args.candidate_manifest,
        candidate_manifest_sha256=args.candidate_manifest_sha256,
        candidate_validation_path=args.candidate_validation,
        candidate_validation_sha256=args.candidate_validation_sha256,
        afk_adjudication_path=args.afk_adjudication,
        afk_adjudication_sha256=args.afk_adjudication_sha256,
        holdout_fixture_path=args.holdout_fixture,
        holdout_fixture_sha256=args.holdout_fixture_sha256,
        holdout_labels_path=args.holdout_labels,
        holdout_labels_sha256=args.holdout_labels_sha256,
        holdout_result_path=args.holdout_result,
        holdout_result_sha256=args.holdout_result_sha256,
        public_result_path=args.public_result,
        public_result_sha256=args.public_result_sha256,
    )
    _write_json(args.output, payload)


if __name__ == "__main__":
    main()
