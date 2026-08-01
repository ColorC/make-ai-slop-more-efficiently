"""Generate deterministic, hash-locked AI-player counterexamples."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .contracts import StateObservationFeaturesV1


FIXTURE_SCHEMA = "ai-player-public-counterexamples.v1"
STATE_FIXTURE_SCHEMA_V2 = "ai-player-public-state-counterexamples.v2"

_STATE_VARIANTS_V2 = (
    ("animation", "same", False),
    ("countdown", "same", False),
    ("red_dot", "same", False),
    ("minor_layout_shift", "same", False),
    ("overlay", "different", True),
    ("popup", "different", True),
    ("selected_state", "different", True),
    ("similar_interface", "different", True),
)
STATE_VARIANT_POLICY_V2 = {
    variant: {
        "expected_relation": relation,
        "critical_operation_difference": critical,
        "expected_count": 63 if index < 4 else 62,
    }
    for index, (variant, relation, critical) in enumerate(_STATE_VARIANTS_V2)
}
STATE_RELATION_COUNTS_V2 = {"same": 252, "different": 248, "critical": 248}

_INTERFACE_FAMILIES_V2 = (
    ("hero-detail", "英雄详情", "培养英雄"),
    ("equipment-list", "装备列表", "穿戴装备"),
    ("inventory", "背包", "使用道具"),
    ("world-map", "世界地图", "移动部队"),
    ("mission-list", "任务列表", "领取奖励"),
    ("formation", "编队", "保存阵容"),
    ("battle-result", "战斗结算", "确认结算"),
    ("skill-tree", "技能树", "学习技能"),
    ("building-detail", "建筑详情", "升级建筑"),
    ("technology-tree", "科技树", "研究科技"),
    ("shop", "商店", "购买商品"),
    ("mail", "邮件", "领取附件"),
    ("alliance", "同盟", "申请加入"),
    ("chat", "聊天", "发送消息"),
    ("event-list", "活动列表", "进入活动"),
    ("daily-signin", "每日签到", "领取签到"),
    ("character-list", "角色列表", "选择角色"),
    ("resource-node", "资源点", "派遣采集"),
    ("march-list", "行军队列", "查看行军"),
    ("ranking", "排行榜", "查看玩家"),
    ("quest-dialogue", "剧情对话", "推进对话"),
    ("tutorial-step", "新手引导", "执行引导"),
    ("settings", "设置", "保存设置"),
    ("season-goal", "赛季目标", "查看目标"),
    ("reward-preview", "奖励预览", "确认奖励"),
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _with_hash(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "fixture_hash": _hash(value)}


def build_loop_scenarios() -> list[dict[str, Any]]:
    patterns = (
        "self_loop",
        "a_b_bounce",
        "scroll_end_no_change",
        "failed_recovery_bounce",
        "stale_route_oscillation",
    )
    scenarios = []
    for index in range(100):
        pattern = patterns[index % len(patterns)]
        scenarios.append(
            _with_hash(
                {
                    "id": f"loop.{index:03d}",
                    "pattern": pattern,
                    "state_ids": [f"state.{index}.a", f"state.{index}.b"],
                    "repeated_action": f"action.{pattern}",
                    "attempts_without_new_evidence": 3 + index % 3,
                    "safe_recovery_state_id": f"state.{index}.recovery",
                    "remaining_frontier_task_id": f"task.{index}.alternate",
                    "oracle": {
                        "infinite_loop_allowed": False,
                        "current_branch_disposition": "cooldown",
                        "next_task_id": f"task.{index}.alternate",
                        "must_record_reason": True,
                    },
                }
            )
        )
    return scenarios


def build_state_recognition_samples() -> list[dict[str, Any]]:
    variants = (
        ("animation", "same", False),
        ("countdown", "same", False),
        ("red_dot", "same", False),
        ("minor_layout_shift", "same", False),
        ("overlay", "different", True),
        ("popup", "different", True),
        ("selected_state", "different", True),
        ("similar_interface", "different", True),
    )
    samples = []
    for index in range(500):
        variant, expected_relation, critical = variants[index % len(variants)]
        family = f"interface-family-{index % 25:02d}"
        samples.append(
            _with_hash(
                {
                    "id": f"state-recognition.{index:03d}",
                    "interface_family": family,
                    "variant_kind": variant,
                    "left_features": {
                        "stable_region": family,
                        "navigation_level": index % 5,
                        "selected_object": "row-a" if variant == "selected_state" else None,
                        "modal": None,
                    },
                    "right_features": {
                        "stable_region": family,
                        "navigation_level": index % 5,
                        "selected_object": "row-b" if variant == "selected_state" else None,
                        "modal": variant if variant in {"overlay", "popup"} else None,
                        "ephemeral_value": index if expected_relation == "same" else None,
                    },
                    "oracle": {
                        "expected_relation": expected_relation,
                        "critical_operation_difference": critical,
                        "must_keep_evidence_for_both": True,
                    },
                }
            )
        )
    return samples


def _feature_dump(features: StateObservationFeaturesV1) -> dict[str, Any]:
    return features.model_dump(mode="json", by_alias=True)


def _screen_fingerprint(sample_id: str, side: str) -> str:
    return hashlib.sha256(f"{sample_id}:{side}".encode()).hexdigest()[:16]


def _state_feature_pair_v2(
    index: int,
    variant: str,
) -> tuple[StateObservationFeaturesV1, StateObservationFeaturesV1, str]:
    slug, title, primary_action = _INTERFACE_FAMILIES_V2[
        index % len(_INTERFACE_FAMILIES_V2)
    ]
    sample_id = f"state-recognition-v2.{index:03d}"
    content_variant = index // len(_INTERFACE_FAMILIES_V2)
    structure = [
        f"surface:{slug}",
        "region:top-navigation",
        "region:primary-content",
        "control:primary-action",
        f"content-variant:{content_variant:02d}",
    ]
    text = [title, primary_action, f"内容组 {content_variant:02d}"]
    runtime = ["scene:game-ui", "surface-ready:true"]
    regions = {
        "navigation": hashlib.sha256(f"{slug}:navigation".encode()).hexdigest()[:16],
        "content": hashlib.sha256(
            f"{slug}:content:{content_variant:02d}".encode()
        ).hexdigest()[:16],
    }
    critical = {
        "surface": slug,
        "navigation_level": "business-surface",
        "primary_action_semantics": primary_action,
        "interaction_scope": "main-surface",
    }
    shared = {
        "ui_structure_tokens": structure,
        "ui_text_tokens": text,
        "runtime_tokens": runtime,
        "selected_object_tokens": [],
        "overlay_tokens": [],
        "region_fingerprints": regions,
        "critical_features": critical,
        "volatile_tokens": [],
    }
    left_data: dict[str, Any] = {
        **shared,
        "screenshot_fingerprint": _screen_fingerprint(sample_id, "left"),
    }
    right_data: dict[str, Any] = {
        **shared,
        "screenshot_fingerprint": _screen_fingerprint(sample_id, "right"),
    }
    operation_difference = ""

    if variant == "animation":
        left_data["runtime_tokens"] = [*runtime, "animation-frame:11"]
        left_data["volatile_tokens"] = ["animation-frame:11"]
        right_data["runtime_tokens"] = [*runtime, "animation-frame:12"]
        right_data["volatile_tokens"] = ["animation-frame:12"]
        operation_difference = "仅动画帧变化，所有可执行操作保持一致。"
    elif variant == "countdown":
        left_data["ui_text_tokens"] = [*text, "countdown:00:10"]
        left_data["volatile_tokens"] = ["countdown:00:10"]
        right_data["ui_text_tokens"] = [*text, "countdown:00:09"]
        right_data["volatile_tokens"] = ["countdown:00:09"]
        operation_difference = "仅倒计时数字变化，所有可执行操作保持一致。"
    elif variant == "red_dot":
        left_data["ui_structure_tokens"] = [*structure, "red-dot:hidden"]
        left_data["volatile_tokens"] = ["red-dot:hidden"]
        right_data["ui_structure_tokens"] = [*structure, "red-dot:visible"]
        right_data["volatile_tokens"] = ["red-dot:visible"]
        operation_difference = "仅通知红点变化，当前界面及操作语义保持一致。"
    elif variant == "minor_layout_shift":
        left_data["ui_structure_tokens"] = [*structure, "layout-shift:x=0,y=0"]
        left_data["volatile_tokens"] = ["layout-shift:x=0,y=0"]
        right_data["ui_structure_tokens"] = [*structure, "layout-shift:x=2,y=1"]
        right_data["volatile_tokens"] = ["layout-shift:x=2,y=1"]
        operation_difference = "仅有轻微布局位移，控件身份和可执行操作保持一致。"
    elif variant == "overlay":
        right_data["overlay_tokens"] = ["overlay:tutorial-mask"]
        right_data["critical_features"] = {
            **critical,
            "interaction_scope": "tutorial-highlight-only",
        }
        if index == 4:
            right_data["critical_features"]["input_capture_policy"] = "highlight-only"
        operation_difference = "引导遮罩截获输入，只允许操作高亮控件。"
    elif variant == "popup":
        right_data["overlay_tokens"] = ["popup:confirmation-dialog"]
        right_data["critical_features"] = {
            **critical,
            "interaction_scope": "confirmation-dialog-only",
        }
        operation_difference = "确认弹窗截获输入，只能确认、取消或关闭弹窗。"
    elif variant == "selected_state":
        left_data["selected_object_tokens"] = ["selected:object-a"]
        left_data["critical_features"] = {**critical, "action_target": "object-a"}
        right_data["selected_object_tokens"] = ["selected:object-b"]
        right_data["critical_features"] = {**critical, "action_target": "object-b"}
        operation_difference = "选中对象变化，主操作将作用于不同对象。"
    elif variant == "similar_interface":
        left_data["critical_features"] = {
            **critical,
            "primary_action_semantics": f"{primary_action}:commit",
        }
        right_data["critical_features"] = {
            **critical,
            "primary_action_semantics": f"{primary_action}:preview",
        }
        if index == 7:
            right_data["critical_features"]["preview_requires_confirmation"] = "true"
        right_data["ui_text_tokens"] = [title, f"预览{primary_action}", *text[2:]]
        operation_difference = "界面布局相似，但主按钮分别执行提交和预览。"
    else:  # pragma: no cover - generator variants are closed above
        raise ValueError(f"unsupported state counterexample variant: {variant}")

    return (
        StateObservationFeaturesV1.model_validate(left_data),
        StateObservationFeaturesV1.model_validate(right_data),
        operation_difference,
    )


def build_state_recognition_samples_v2() -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for index in range(500):
        variant, expected_relation, critical = _STATE_VARIANTS_V2[
            index % len(_STATE_VARIANTS_V2)
        ]
        left, right, operation_difference = _state_feature_pair_v2(index, variant)
        samples.append(
            _with_hash(
                {
                    "id": f"state-recognition-v2.{index:03d}",
                    "interface_family": _INTERFACE_FAMILIES_V2[
                        index % len(_INTERFACE_FAMILIES_V2)
                    ][0],
                    "variant_kind": variant,
                    "case_description": operation_difference,
                    "left_features": _feature_dump(left),
                    "right_features": _feature_dump(right),
                    "oracle": {
                        "expected_relation": expected_relation,
                        "critical_operation_difference": critical,
                    },
                }
            )
        )
    return samples


def build_state_fixture_v2() -> dict[str, Any]:
    payload = {
        "schema": STATE_FIXTURE_SCHEMA_V2,
        "generator_version": 2,
        "evaluation_policy": {
            "same_state_similarity_threshold": 0.90,
            "critical_false_merge_max_count": 0,
            "overall_false_merge_max_rate": 0.01,
            "over_split_max_rate": 0.03,
        },
        "state_recognition_samples": build_state_recognition_samples_v2(),
    }
    return {**payload, "content_hash": _hash(payload)}


def build_task_queue_scenarios() -> list[dict[str, Any]]:
    patterns = (
        "dependency_unmet",
        "duplicate_candidate",
        "cooldown_active",
        "all_blocked",
        "safe_frontier_exists",
    )
    scenarios = []
    for index in range(100):
        pattern = patterns[index % len(patterns)]
        safe_exists = pattern in {"duplicate_candidate", "safe_frontier_exists"}
        expected_task_id = f"task.{index}.safe" if safe_exists else None
        scenarios.append(
            _with_hash(
                {
                    "id": f"task-queue.{index:03d}",
                    "pattern": pattern,
                    "tasks": [
                        {
                            "id": f"task.{index}.blocked",
                            "status": "blocked" if pattern != "cooldown_active" else "cooldown",
                            "dependency_met": False,
                            "duplicate_of": None,
                        },
                        {
                            "id": f"task.{index}.safe",
                            "status": "queued" if safe_exists else "blocked",
                            "dependency_met": safe_exists,
                            "duplicate_of": None,
                        },
                    ],
                    "oracle": {
                        "safe_reachable_frontier_exists": safe_exists,
                        "expected_next_task_id": expected_task_id,
                        "idle_allowed": not safe_exists,
                        "all_remaining_tasks_need_named_reason": not safe_exists,
                    },
                }
            )
        )
    return scenarios


def build_account_policy_samples() -> list[dict[str, Any]]:
    samples = [
        _with_hash(
            {
                "id": f"account-policy.{index:03d}",
                "category": f"future-game-internal-business-{index:03d}",
                "game_internal": True,
                "involves_real_money": False,
                "submits_external_personal_identity": False,
                "oracle_disposition": "autonomous",
            }
        )
        for index in range(496)
    ]
    samples.extend(
        [
            _with_hash(
                {
                    "id": "account-policy.496",
                    "category": "real_money_payment",
                    "game_internal": True,
                    "involves_real_money": False,
                    "submits_external_personal_identity": False,
                    "oracle_disposition": "awaiting_authorization",
                }
            ),
            _with_hash(
                {
                    "id": "account-policy.497",
                    "category": "external_personal_identity_submission",
                    "game_internal": True,
                    "involves_real_money": False,
                    "submits_external_personal_identity": False,
                    "oracle_disposition": "awaiting_authorization",
                }
            ),
            _with_hash(
                {
                    "id": "account-policy.498",
                    "category": "real_money_payment",
                    "game_internal": True,
                    "involves_real_money": True,
                    "submits_external_personal_identity": False,
                    "oracle_disposition": "awaiting_authorization",
                }
            ),
            _with_hash(
                {
                    "id": "account-policy.499",
                    "category": "external_personal_identity_submission",
                    "game_internal": False,
                    "involves_real_money": False,
                    "submits_external_personal_identity": True,
                    "oracle_disposition": "awaiting_authorization",
                }
            ),
        ]
    )
    return samples


def build_fixture() -> dict[str, Any]:
    payload = {
        "schema": FIXTURE_SCHEMA,
        "generator_version": 1,
        "freeze_order": {
            "loop_state_and_task_sets_before_their_algorithms": True,
            "account_policy_set_after_initial_policy_contract": True,
        },
        "loop_scenarios": build_loop_scenarios(),
        "state_recognition_samples": build_state_recognition_samples(),
        "task_queue_scenarios": build_task_queue_scenarios(),
        "account_policy_samples": build_account_policy_samples(),
    }
    return {**payload, "content_hash": _hash(payload)}


def write_fixture(path: Path) -> dict[str, Any]:
    payload = build_fixture()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def write_state_fixture_v2(path: Path) -> dict[str, Any]:
    payload = build_state_fixture_v2()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", choices=("v1", "v2"), default="v1")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = write_fixture(args.output) if args.version == "v1" else write_state_fixture_v2(args.output)
    print(payload["content_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
