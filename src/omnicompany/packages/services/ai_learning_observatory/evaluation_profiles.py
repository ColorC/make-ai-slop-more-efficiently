# [OMNI] origin=codex created_by=codex intent=semantic-first-project-evaluation-contracts
"""Project-specific, semantic-first evaluation contracts.

Profiles describe what users actually care about for a project.  They are not
scorecards: correctness is a gate, the five requested dimensions stay
separate, and raw efficiency changes are never compared across unlike work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from omnicompany.core.config import omni_workspace_root


_SCHEMA_PATH = Path(__file__).with_name("schemas") / "evaluation_profile.schema.json"
EVALUATION_PROFILE_SCHEMA: dict[str, Any] = json.loads(
    _SCHEMA_PATH.read_text(encoding="utf-8")
)

DIMENSION_IDS = (
    "time",
    "token_cost",
    "user_expectation",
    "objective_outcome",
    "human_attention",
)

HARD_COMPARISON_KEYS = (
    "project_id",
    "business_id",
    "task_class_id",
    "acceptance_standard_id",
    "risk_tier",
    "environment_family",
)


class EvaluationProfileError(ValueError):
    """Raised when a profile or comparison violates the evaluation contract."""


def profile_root(config: dict[str, Any]) -> Path:
    setting = (
        (config.get("evaluation_profiles") or {}).get("root")
        or "data/services/ai_learning_observatory/evaluation_profiles"
    )
    path = Path(str(setting)).expanduser()
    if not path.is_absolute():
        path = omni_workspace_root() / path
    return path.resolve()


def profile_path(project_id: str, config: dict[str, Any]) -> Path:
    return profile_root(config) / f"{project_id}.json"


def profile_markdown_root(config: dict[str, Any]) -> Path:
    setting = (
        (config.get("evaluation_profiles") or {}).get("markdown_root")
        or "docs/projects/ai-learning-observatory/evaluation-profiles"
    )
    path = Path(str(setting))
    if not path.is_absolute():
        path = omni_workspace_root() / path
    return path.resolve()


def profile_markdown_path(project_id: str, config: dict[str, Any]) -> Path:
    return profile_markdown_root(config) / f"{project_id}.md"


def load_project_profile(
    project_id: str,
    config: dict[str, Any],
    *,
    required: bool = False,
) -> dict[str, Any] | None:
    path = profile_path(project_id, config)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise EvaluationProfileError(f"missing evaluation profile: {path}")
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationProfileError(f"cannot read evaluation profile {path}: {exc}") from exc
    issues = validate_profile(value, expected_project_id=project_id)
    if issues:
        raise EvaluationProfileError(f"invalid evaluation profile {path}: {'; '.join(issues)}")
    value["_profile_path"] = str(path)
    return value


def validate_profile(
    value: Any,
    *,
    expected_project_id: str | None = None,
) -> list[str]:
    """Validate the invariants that matter even outside Codex structured output."""

    if not isinstance(value, dict):
        return ["profile must be an object"]
    issues: list[str] = []
    required = set(EVALUATION_PROFILE_SCHEMA["required"])
    missing = sorted(required - set(value))
    issues.extend(f"$.{key}: required" for key in missing)
    extra = sorted(set(value) - set(EVALUATION_PROFILE_SCHEMA["properties"]))
    issues.extend(f"$.{key}: additional property" for key in extra)
    if value.get("schema_version") != "project-evaluation-profile-v1":
        issues.append("$.schema_version: expected project-evaluation-profile-v1")
    summary = str(value.get("summary") or "")
    if any(marker in summary for marker in ("我将", "将以只读", "稍后核对", "准备检查")):
        issues.append("$.summary: must report the current evaluated contract, not future work")
    if expected_project_id and value.get("project_id") != expected_project_id:
        issues.append(
            f"$.project_id: expected {expected_project_id}, got {value.get('project_id')}"
        )

    dimensions = value.get("dimensions")
    if not isinstance(dimensions, list):
        issues.append("$.dimensions: expected array")
    else:
        observed = [str(row.get("id") or "") for row in dimensions if isinstance(row, dict)]
        if sorted(observed) != sorted(DIMENSION_IDS):
            issues.append(
                "$.dimensions: must contain each of time, token_cost, "
                "user_expectation, objective_outcome, human_attention exactly once"
            )
        issues.extend(_profile_object_issues(dimensions, "$.dimensions"))

    policy = value.get("comparison_policy")
    if not isinstance(policy, dict):
        issues.append("$.comparison_policy: expected object")
    else:
        for key in (
            "absolute_before_relative",
            "no_composite_score",
            "missing_is_unknown",
            "absence_is_not_success",
        ):
            if policy.get(key) is not True:
                issues.append(f"$.comparison_policy.{key}: must be true")

    gate = value.get("correctness_gate")
    if not isinstance(gate, dict) or not gate.get("must_hold"):
        issues.append("$.correctness_gate.must_hold: non-empty correctness gate required")
    for key in ("user_concerns", "task_classes", "source_connections"):
        rows = value.get(key)
        if not isinstance(rows, list) or not rows:
            issues.append(f"$.{key}: non-empty array required")
        elif any(not isinstance(row, dict) for row in rows):
            issues.append(f"$.{key}: every item must be an object")
    baseline = value.get("current_baseline") or {}
    if not isinstance(baseline, dict) or len(baseline.get("facts") or []) < 2:
        issues.append("$.current_baseline.facts: at least two evidence-derived facts required")
    return issues


def _profile_object_issues(rows: list[Any], path: str) -> list[str]:
    issues: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(f"{path}[{index}]: expected object")
            continue
        for key in (
            "id",
            "user_question",
            "objective_observations",
            "semantic_judgment",
            "comparability_conditions",
            "anti_gaming",
            "unknown_when",
        ):
            if key not in row:
                issues.append(f"{path}[{index}].{key}: required")
    return issues


def comparison_qualification(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Decide whether raw before/after values share a meaningful denominator.

    Complexity and scale mismatches are never silently averaged away.  A caller
    may later supply a domain-approved normalization, but until then the raw
    pair is explicitly not comparable.
    """

    missing: list[str] = []
    mismatches: list[dict[str, Any]] = []
    for key in HARD_COMPARISON_KEYS:
        left = baseline.get(key)
        right = candidate.get(key)
        if left in (None, "") or right in (None, ""):
            missing.append(key)
        elif left != right:
            mismatches.append({"key": key, "baseline": left, "candidate": right})

    scale_mismatches = _axis_mismatches(
        baseline.get("scale") or {}, candidate.get("scale") or {}
    )
    complexity_mismatches = _axis_mismatches(
        baseline.get("complexity") or {}, candidate.get("complexity") or {}
    )
    if missing:
        status = "unknown"
        reason = "关键比较键缺失，不能把缺失值当作同尺度。"
    elif mismatches:
        status = "not_comparable"
        reason = "任务身份、验收、风险或环境不同。"
    elif scale_mismatches or complexity_mismatches:
        status = "requires_normalization"
        reason = "任务规模或复杂度不同；只有项目档案认可的分层/归一化后才能比较。"
    else:
        status = "comparable"
        reason = "关键身份、质量标准、风险、环境、规模与复杂度口径一致。"
    return {
        "status": status,
        "reason": reason,
        "missing_keys": missing,
        "hard_mismatches": mismatches,
        "scale_mismatches": scale_mismatches,
        "complexity_mismatches": complexity_mismatches,
        "raw_efficiency_comparison_allowed": status == "comparable",
    }


def _axis_mismatches(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    keys = sorted(set(left) | set(right))
    return [
        {"axis": key, "baseline": left.get(key), "candidate": right.get(key)}
        for key in keys
        if left.get(key) != right.get(key)
    ]


def semantic_guard_issues(value: Any) -> list[str]:
    """Reject metric wins that bypass correctness or comparability."""

    if not isinstance(value, dict):
        return ["semantic assessment must be an object"]
    issues: list[str] = []
    gate = value.get("correctness_gate") or {}
    comparison = value.get("comparison") or {}
    dimensions = value.get("dimensions") or []
    observed = [str(row.get("dimension") or "") for row in dimensions if isinstance(row, dict)]
    if sorted(observed) != sorted(DIMENSION_IDS):
        issues.append("$.dimensions: each five-dimensional assessment is required exactly once")

    gate_met = gate.get("status") == "met"
    comparable = comparison.get("status") in {"comparable", "partially_comparable"}
    for index, row in enumerate(dimensions):
        if not isinstance(row, dict):
            continue
        status = row.get("status")
        dimension = row.get("dimension")
        if status in {"better", "worse", "stable", "mixed"} and not comparable:
            issues.append(
                f"$.dimensions[{index}].status: directional claim requires comparable work"
            )
        if dimension in {"time", "token_cost", "human_attention"} and status == "better":
            if not gate_met:
                issues.append(
                    f"$.dimensions[{index}].status: efficiency cannot be better before correctness gate is met"
                )
            quality_directions = {
                str(item.get("dimension")): item.get("status")
                for item in dimensions
                if isinstance(item, dict)
            }
            for quality_dimension in ("user_expectation", "objective_outcome"):
                if quality_directions.get(quality_dimension) not in {"better", "stable"}:
                    issues.append(
                        f"$.dimensions[{index}].status: efficiency win blocked while "
                        f"{quality_dimension} is not stable or better"
                    )
    return issues


def render_profile_markdown(profile: dict[str, Any], *, provenance: dict[str, Any]) -> str:
    """Render a readable docs projection of the data-service JSON source."""

    lines = [
        f"# {profile['project_name']} 评测档案",
        "",
        "> 本文是 data service 中同名 JSON 评测契约的人读投影。它定义如何评测，不宣称项目已经变好。",
        "",
        f"- Project：`{profile['project_id']}`",
        f"- 工作类型：`{profile['work_type']}`",
        f"- 生成渠道：{provenance.get('provider')} / `{provenance.get('model')}` / `{provenance.get('harness')}`",
        f"- 一次性授权日期：`{provenance.get('authorized_date')}`",
        f"- Agent run：`{provenance.get('agent_run_id')}`",
        "",
        profile["summary"],
        "",
        "## 正确性与智能度门槛",
        "",
        profile["correctness_gate"]["primary_question"],
        "",
    ]
    lines.extend(f"- {item}" for item in profile["correctness_gate"]["must_hold"])
    lines.extend(["", "## 用户真正关心什么", ""])
    for item in profile["user_concerns"]:
        lines.extend(
            [
                f"### {item['label']}",
                "",
                item["user_question"],
                "",
                f"接受语义：{item['semantic_acceptance']}",
                "",
            ]
        )
    lines.extend(["## 可比较任务类型", ""])
    for item in profile["task_classes"]:
        lines.extend(
            [
                f"### {item['label']} (`{item['id']}`)",
                "",
                f"工作单位：{item['unit_of_work']}",
                "",
                "绝对质量门：",
                "",
                *(f"- {bar}" for bar in item["absolute_quality_bar"]),
                "",
            ]
        )
    lines.extend(["## 五个独立维度", ""])
    labels = {
        "time": "更快",
        "token_cost": "更少 token / 成本",
        "user_expectation": "更符合用户预期",
        "objective_outcome": "客观/业务数据更好",
        "human_attention": "用户观看与精力更少",
    }
    for item in profile["dimensions"]:
        lines.extend(
            [
                f"### {labels[item['id']]}",
                "",
                item["user_question"],
                "",
                f"语义判断：{item['semantic_judgment']}",
                "",
                "防异化：",
                "",
                *(f"- {rule}" for rule in item["anti_gaming"]),
                "",
            ]
        )
    lines.extend(["## 当前接通状态", ""])
    for item in profile["source_connections"]:
        lines.append(
            f"- `{item['source_id']}`：{item['current_strength']}；{item['role']}"
        )
    lines.extend(["", "## 当前已知边界", ""])
    lines.extend(f"- 事实：{item}" for item in profile["current_baseline"]["facts"])
    lines.extend(f"- 推断：{item}" for item in profile["current_baseline"]["inferences"])
    lines.extend(f"- 未知：{item}" for item in profile["current_baseline"]["unknowns"])
    lines.extend(
        [
            "",
            "## 固定比较纪律",
            "",
            "- 先做绝对语义评价，再讨论相对变化。",
            "- 不同任务类型、复杂度、规模、风险、环境或验收标准不直接比较。",
            "- 缺失就是未知；没有观看/纠正记录不等于用户没有花精力。",
            "- 更快、更省 token、看得更少，只有在正确性成立且用户预期和客观结果不退化时才可记为更好。",
            "- 不生成跨维度总分。",
            "",
        ]
    )
    return "\n".join(lines)


def profile_summary(profile: dict[str, Any] | None) -> dict[str, Any]:
    if profile is None:
        return {
            "status": "missing",
            "profile_path": None,
            "task_class_count": 0,
            "user_concern_count": 0,
            "dimensions": list(DIMENSION_IDS),
        }
    return {
        "status": "connected",
        "profile_path": profile.get("_profile_path"),
        "task_class_count": len(profile.get("task_classes") or []),
        "user_concern_count": len(profile.get("user_concerns") or []),
        "dimensions": [row["id"] for row in profile.get("dimensions") or []],
        "no_composite_score": True,
    }


def unique_nonempty(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


__all__ = [
    "DIMENSION_IDS",
    "EVALUATION_PROFILE_SCHEMA",
    "EvaluationProfileError",
    "comparison_qualification",
    "load_project_profile",
    "profile_markdown_path",
    "profile_markdown_root",
    "profile_path",
    "profile_root",
    "profile_summary",
    "render_profile_markdown",
    "semantic_guard_issues",
    "validate_profile",
]
