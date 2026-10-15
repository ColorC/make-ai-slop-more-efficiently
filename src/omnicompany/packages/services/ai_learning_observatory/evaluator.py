# [OMNI] origin=codex created_by=codex intent=quality-gated-five-dimension-evaluation
"""Deterministic checks plus evidence-bound Agent assessment."""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentEvent,
    ExternalAgentPermissionMode,
    ExternalAgentResult,
    ExternalAgentRunSpec,
    ExternalAgentStatus,
)
from omnicompany.runtime.llm.structured import validate_json_schema

from .agent_runtime import (
    agent_tool_calls,
    agent_usage,
    the_company_codex_config_overrides,
    run_codex_agent_sync,
)
from .evaluation_profiles import (
    EvaluationProfileError,
    load_project_profile,
    profile_summary,
    semantic_guard_issues,
)

_SCHEMA_PATH = Path(__file__).with_name("schemas") / "assessment.schema.json"
ASSESSMENT_SCHEMA: dict[str, Any] = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


AgentRunner = Callable[[ExternalAgentRunSpec], ExternalAgentResult]


def evaluate_snapshot(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    deterministic_only: bool = False,
    agent_runner: AgentRunner | None = None,
    snapshot_path: str | Path | None = None,
    previous_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    evaluation_cfg = config["evaluation"]
    project_id = str(snapshot["project"]["id"])
    profile_error = None
    try:
        profile = load_project_profile(project_id, config)
    except EvaluationProfileError as exc:
        profile = None
        profile_error = str(exc)
    deterministic = _deterministic_assessment(snapshot, previous, profile)
    profile_projection = profile_summary(profile)
    if profile_error:
        profile_projection["status"] = "invalid"
        profile_projection["error"] = profile_error
    base = {
        "schema_version": 1,
        "project_id": project_id,
        "snapshot_id": snapshot.get("snapshot_id"),
        "previous_assessment_id": previous.get("assessment_id") if previous else None,
        "evaluated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "evaluation_state": "unknown",
        "evaluation_profile": profile_projection,
        "deterministic": deterministic,
        "observation_followup": _observation_followup(previous, snapshot),
        "calibration": _calibration(previous, snapshot),
        "semantic": None,
        "evaluator": {
            "harness": evaluation_cfg.get("harness") or "codex",
            "provider": evaluation_cfg["provider"],
            "model": evaluation_cfg["model"],
            "prompt_version": evaluation_cfg["prompt_version"],
            "schema_version": evaluation_cfg["schema_version"],
            "status": "deterministic_only" if deterministic_only else "pending",
            "status_zh": "仅确定性检查" if deterministic_only else "等待 Agent 评价",
            "fallback_used": False,
            "attempts": 0,
            "latency_ms": 0,
            "estimated_cost_usd": None,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0},
            "error": None,
        },
    }
    if deterministic_only:
        return base

    valid_refs = _all_evidence_refs(snapshot)
    runner = agent_runner or run_codex_agent_sync
    start = time.monotonic()
    result = _recover_agent_result(
        previous,
        snapshot=snapshot,
        evaluation_cfg=evaluation_cfg,
        longitudinal_available=deterministic["signals"][
            "longitudinal_comparison_available"
        ],
    )
    if result is not None:
        base["evaluator"]["attempts"] = 0
        base["evaluator"]["recovered_from_agent_event_log"] = True
        base["evaluator"]["event_log_path"] = str(
            (previous or {}).get("evaluator", {}).get("event_log_path") or ""
        )
    else:
        base["evaluator"]["attempts"] = 1
    try:
        if result is None:
            with tempfile.TemporaryDirectory(prefix="omni-learning-evaluation-") as temp_dir:
                temp_root = Path(temp_dir)
                material_snapshot_path = _materialize_source(
                    snapshot, snapshot_path, temp_root / "snapshot.json"
                )
                material_previous_path = (
                    _materialize_source(previous, previous_path, temp_root / "previous.json")
                    if previous
                    else None
                )
                spec = _agent_spec(
                    snapshot=snapshot,
                    snapshot_path=material_snapshot_path,
                    previous_path=material_previous_path,
                    profile=profile,
                    evaluation_cfg=evaluation_cfg,
                    longitudinal_available=deterministic["signals"][
                        "longitudinal_comparison_available"
                    ],
                )
                base["evaluator"]["event_log_path"] = str(
                    spec.metadata["event_log_path"]
                )
                result = runner(spec)
        base["evaluator"]["agent_run_id"] = result.run_id
        base["evaluator"]["usage"] = agent_usage(result)
        if result.normalized_status() != ExternalAgentStatus.SUCCEEDED:
            raise RuntimeError(result.error or "Codex Agent 未完成语义评价")
        if not isinstance(result.structured_output, dict):
            raise ValueError("Codex Agent 未提交结构化评价对象")
        tool_calls = agent_tool_calls(result)
        base["evaluator"]["tool_call_count"] = len(tool_calls)
        if not tool_calls:
            raise ValueError("Codex Agent 未使用只读工具检索快照或工程真源")
        semantic = result.structured_output
        normalized_ref_count = int(
            result.raw.get("evidence_ref_normalizations") or 0
        ) + _normalize_evidence_digest_refs(semantic, snapshot)
        base["evaluator"]["evidence_ref_normalizations"] = normalized_ref_count
        issues = _validate_schema(semantic)
        if (
            not deterministic["signals"]["longitudinal_comparison_available"]
            and (semantic.get("comparison") or {}).get("status")
            != "not_comparable"
        ):
            issues.append(
                "$.comparison.status: must be not_comparable without a different prior snapshot"
            )
        issues.extend(semantic_guard_issues(semantic))
        bad_refs = _invalid_refs(semantic, valid_refs)
        if issues or bad_refs:
            detail = "; ".join(
                issues
                + ([f"unknown evidence refs: {sorted(bad_refs)}"] if bad_refs else [])
            )
            raise ValueError(detail)
        base["semantic"] = semantic
        base["evaluation_state"] = semantic["evaluation_state"]
        base["evaluator"]["status"] = "ok"
        base["evaluator"]["status_zh"] = "Agent 评价完成"
    except Exception as exc:  # noqa: BLE001 - persist semantic unavailability explicitly.
        if result is not None:
            base["evaluator"]["usage"] = agent_usage(result)
            base["evaluator"]["agent_run_id"] = result.run_id
        base["evaluator"]["status"] = "unavailable"
        base["evaluator"]["status_zh"] = "Agent 评价不可用"
        base["evaluator"]["error"] = str(exc)
    usage = base["evaluator"]["usage"]
    if any(usage.values()):
        base["evaluator"]["estimated_cost_usd"] = _estimated_cost(
            evaluation_cfg["model"], usage
        )
    base["evaluator"]["latency_ms"] = round((time.monotonic() - start) * 1000, 2)
    return base


def _deterministic_assessment(
    snapshot: dict[str, Any],
    previous: dict[str, Any] | None,
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    coverage = snapshot.get("coverage") or {}
    sources = snapshot.get("sources") or {}
    available = int(coverage.get("available") or 0)
    gaps = [name for name, row in sources.items() if row.get("status") != "available"]
    evidence_count = sum(len(row.get("items") or []) for row in sources.values())
    has_human = bool((sources.get("human_intervention") or {}).get("items"))
    has_cost = bool((sources.get("efficiency_and_cost") or {}).get("items"))
    has_outcome = bool((sources.get("actual_outcome") or {}).get("items"))
    longitudinal = bool(previous and previous.get("snapshot_id") != snapshot.get("snapshot_id"))
    efficiency_items = (sources.get("efficiency_and_cost") or {}).get("items") or []
    numeric_usage_count = sum(
        1
        for item in efficiency_items
        if any(
            isinstance((item.get("data") or {}).get(key), (int, float))
            and not isinstance((item.get("data") or {}).get(key), bool)
            for key in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "total_cost",
                "duration_seconds",
            )
        )
    )
    human_items = (sources.get("human_intervention") or {}).get("items") or []
    outcome_items = (sources.get("actual_outcome") or {}).get("items") or []
    return {
        "summary": (
            f"观测中心查询了 {coverage.get('queried', len(sources))} 类既有证据，"
            f"当前能读取 {available} 类，另有 {len(gaps)} 类暂未提取或关联。"
            "这些计数只描述可观测程度；是否做对、是否符合用户预期以及客观结果如何，仍需按项目档案作语义评价。"
        ),
        "evidence_count": evidence_count,
        "available_categories": available,
        "gap_categories": gaps,
        "signals": {
            "human_intervention_observed": has_human,
            "efficiency_metadata_observed": has_cost,
            "numeric_usage_observed": numeric_usage_count > 0,
            "outcome_evidence_observed": has_outcome,
            "longitudinal_comparison_available": longitudinal,
            "project_evaluation_profile_available": profile is not None,
        },
        "observed_counts": {
            "human_intervention_items": len(human_items),
            "outcome_items": len(outcome_items),
            "efficiency_items": len(efficiency_items),
            "numeric_usage_items": numeric_usage_count,
        },
        "absolute_status": "unknown" if gaps or not has_outcome else "mixed",
        "comparison_status": "not_comparable" if not longitudinal else "requires_semantic_check",
    }


def _observation_followup(
    previous: dict[str, Any] | None,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    semantic = (previous or {}).get("semantic") or {}
    changed = bool(previous and previous.get("snapshot_id") != snapshot.get("snapshot_id"))
    return {
        "previous_assessment_id": previous.get("assessment_id") if previous else None,
        "new_snapshot_available": changed,
        "prior_unknowns": list(semantic.get("unknowns") or []),
        "prior_next_observations": list(semantic.get("next_observations") or []),
        "status": "recheck_against_new_evidence" if changed else "no_new_snapshot",
        "note": (
            "新证据只触发重新观察；它不预设此前判断应被证明、也不预设工作必然变好。"
            if changed
            else "没有新的不同快照，不能制造趋势或把重复材料当作进步。"
        ),
    }


def _calibration(previous: dict[str, Any] | None, snapshot: dict[str, Any]) -> dict[str, Any]:
    revised = bool(previous and previous.get("snapshot_id") != snapshot.get("snapshot_id"))
    return {
        "previous_assessment_id": previous.get("assessment_id") if previous else None,
        "revision_triggered": revised,
        "status": "pending_external_validation" if previous else "cold_start",
        "note": (
            "新证据触发了评价修订，但评价器不能自行证明此前判断是否正确。"
            if revised else "尚无后续独立证据可以复核这次评价。"
        ),
    }


def _all_evidence_refs(snapshot: dict[str, Any]) -> set[str]:
    """Collect every stable evidence id; semantic input is never pre-truncated."""

    return {
        str(item["id"])
        for source in (snapshot.get("sources") or {}).values()
        for item in source.get("items") or []
        if item.get("id")
    }


def _materialize_source(
    value: dict[str, Any],
    existing_path: str | Path | None,
    fallback_path: Path,
) -> Path:
    if existing_path:
        path = Path(existing_path).expanduser().resolve()
        if path.is_file():
            return path
    fallback_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return fallback_path.resolve()


def _agent_spec(
    *,
    snapshot: dict[str, Any],
    snapshot_path: Path,
    previous_path: Path | None,
    profile: dict[str, Any] | None,
    evaluation_cfg: dict[str, Any],
    longitudinal_available: bool,
) -> ExternalAgentRunSpec:
    workspace = omni_workspace_root().resolve()
    schema_setting = evaluation_cfg.get("output_schema_path")
    schema_path = (
        (workspace / str(schema_setting)).resolve()
        if schema_setting
        else _SCHEMA_PATH.resolve()
    )
    model_catalog_setting = evaluation_cfg.get("model_catalog_path")
    model_catalog_path = (
        (workspace / str(model_catalog_setting)).resolve()
        if model_catalog_setting
        else None
    )
    previous_line = (
        f"- 上一次评价：{previous_path}\n" if previous_path else "- 上一次评价：无\n"
    )
    profile_line = (
        f"- 项目专属评测档案：{profile.get('_profile_path')}\n"
        if profile and profile.get("_profile_path")
        else "- 项目专属评测档案：尚未接通；必须把项目语义门缺失列为未知\n"
    )
    comparison_rule = (
        "存在不同快照，但仍须确认任务类型、复杂度、规模、风险、环境和验收标准相容后才可作方向判断。"
        if longitudinal_available
        else "不存在不同的既往快照，comparison.status 必须是 not_comparable，五个维度不得写方向变化。"
    )
    prompt = (
        "你是 OmniCompany 工作评测中心的只读语义评价 Agent。任务是诚实描述工作当前怎样、相对可比工作怎样变化；"
        "不是证明 AI 变好，也不是生成优化建议。\n\n"
        "权威入口：\n"
        f"- 当前完整快照：{snapshot_path}\n"
        f"{previous_line}"
        f"{profile_line}"
        f"- 工程工作区：{workspace}\n"
        f"- 当前项目：{snapshot.get('project', {}).get('id')}\n\n"
        "请使用只读工具自行读取完整快照，并沿其中的 source_ref、项目根目录、计划、决策、验证和结果真源查找足以完成判断的上下文。"
        "在 Windows 上读取 UTF-8 文本必须显式使用 `Get-Content -Encoding utf8`，避免中文乱码。"
        "不要等待外层替你切块或装配上下文；你决定需要追查哪些真源。除本提示明确标注的当前快照外，打开每个工程文件前必须先用 `Get-Item` 检查长度；"
        "超过 200 KB 的文件禁止 `Get-Content -Raw`，改用 `rg -m 20`、结构化字段选择或带范围的分段读取，并可继续发起多次读取直到覆盖你判断所需的内容。"
        "每条搜索命令都要限定目录、文件类型和返回数量，禁止把整个工作区或大型产物一次输出。"
        "快照里的摘要只是索引，不是完整上下文；需要判断时必须回到可访问真源。\n\n"
        "评价约束：\n"
        "1. 第一问始终是事情是否做对、是否真正理解用户目标、判断是否足够智能。correctness_gate 不成立或未知时，"
        "不得把更快、更省 token 或更少人工介入记为 better。\n"
        "2. 五个维度必须恰好各一次并分别评价：time、token_cost、user_expectation、objective_outcome、human_attention；"
        "不得生成总分、权重、排名或用某一指标覆盖另一维度。\n"
        "3. 用户预期必须回到用户原话、Review 理由、后来纠正/接管/重写和最终裁决；Review 数量或 accepted 标签本身不能替代语义。\n"
        "4. 客观数据必须说明对象、单位、分母、时间窗和权威源。测试/构建绿只证明其覆盖范围，不自动证明产品、内容或体验成功。\n"
        "5. 没看到只能写 unknown；没有观看、纠正、反馈或 token 记录不等于零，也不等于好。计划、提交、文件名、产物存在或自述不等于结果。\n"
        "6. 事实与推断分开；每项 facts、inferences 和方向性维度都只能引用当前快照 sources.*.items[].id 中真实存在的编号。"
        "推断必须可证伪。\n"
        "7. next_observations 只写观测中心应从哪些已有真源继续查询，不得要求目标项目为了评测新增填表、留痕、测试或人工验收。\n"
        f"8. {comparison_rule}\n"
        "9. 更快、更少 token、用户观看或精力更少，仅在 correctness_gate=met，且 user_expectation 与 objective_outcome 均为 stable 或 better 时才可记为 better。\n"
        "10. 所有人读自由文本使用清楚、自然、具体的中文；内部枚举按输出 schema。\n\n"
        "完成资料检索、证据核对和自检后，提交符合输出 schema 的结构化对象。"
    )
    return ExternalAgentRunSpec(
        provider="codex",
        harness_id=str(evaluation_cfg.get("harness") or "codex"),
        model_provider=str(evaluation_cfg["provider"]),
        prompt=prompt,
        cwd=workspace,
        permission_mode=ExternalAgentPermissionMode.READONLY,
        run_id=f"ai-learning-evaluation-{uuid.uuid4().hex}",
        model=str(evaluation_cfg["model"]),
        timeout_s=float(evaluation_cfg.get("timeout_seconds") or 900),
        output_schema_path=schema_path,
        metadata={
            "entrypoint": "ai_learning_observatory.evaluate",
            "minimal_context": True,
            "event_log_redact_tool_output": True,
            "event_log_path": (
                workspace
                / "data"
                / "services"
                / "ai_learning_observatory"
                / "agent_runs"
                / f"evaluation-{uuid.uuid4().hex}.jsonl"
            ),
            "idle_timeout_s": float(
                evaluation_cfg.get("idle_timeout_seconds") or 180
            ),
            "codex_config": the_company_codex_config_overrides(model_catalog_path),
        },
    )


def _validate_schema(value: Any) -> list[str]:
    # The shared validator intentionally supports a subset and does not resolve local $refs.
    issues = [f"{item.path}: {item.message}" for item in validate_json_schema(value, ASSESSMENT_SCHEMA)]
    if not isinstance(value, dict):
        return issues
    issues.extend(
        _object_issues(
            value.get("correctness_gate"),
            "$.correctness_gate",
            {"status", "reason", "evidence_refs"},
            refs_may_be_empty=True,
        )
    )
    issues.extend(
        _object_issues(
            value.get("comparison"),
            "$.comparison",
            {"status", "matched_task_class", "scale_handling", "confounders"},
        )
    )
    for index, row in enumerate(value.get("dimensions") or []):
        issues.extend(
            _object_issues(
                row,
                f"$.dimensions[{index}]",
                {"dimension", "status", "reason", "measurement_scope", "evidence_refs"},
                refs_may_be_empty=True,
            )
        )
    for index, row in enumerate(value.get("facts") or []):
        issues.extend(
            _object_issues(row, f"$.facts[{index}]", {"claim", "evidence_refs"})
        )
    for index, row in enumerate(value.get("inferences") or []):
        issues.extend(_object_issues(
            row, f"$.inferences[{index}]",
            {"claim", "confidence", "evidence_refs", "disconfirming_evidence"},
        ))
    for index, row in enumerate(value.get("next_observations") or []):
        issues.extend(_object_issues(
            row, f"$.next_observations[{index}]",
            {"question", "why", "existing_source_candidates"},
        ))
    return issues


def _object_issues(
    value: Any,
    path: str,
    required: set[str],
    *,
    refs_may_be_empty: bool = False,
) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path}: expected object"]
    issues = [f"{path}.{key}: required" for key in sorted(required - set(value))]
    extra = set(value) - required
    issues.extend(f"{path}.{key}: additional property" for key in sorted(extra))
    refs = value.get("evidence_refs")
    if "evidence_refs" in required and (
        not isinstance(refs, list) or (not refs and not refs_may_be_empty)
    ):
        issues.append(
            f"{path}.evidence_refs: {'array' if refs_may_be_empty else 'non-empty array'} required"
        )
    return issues


def _invalid_refs(value: Any, valid_refs: set[str]) -> set[str]:
    invalid: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "evidence_refs" and isinstance(child, list):
                invalid.update(str(ref) for ref in child if str(ref) not in valid_refs)
            else:
                invalid.update(_invalid_refs(child, valid_refs))
    elif isinstance(value, list):
        for child in value:
            invalid.update(_invalid_refs(child, valid_refs))
    return invalid


def _normalize_evidence_digest_refs(
    value: Any,
    snapshot: dict[str, Any],
) -> int:
    """Repair only exact full-digest evidence refs to their canonical item ids."""

    by_digest: dict[str, str] = {}
    ambiguous: set[str] = set()
    for source in (snapshot.get("sources") or {}).values():
        for item in source.get("items") or []:
            digest = str(item.get("digest") or "")
            evidence_id = str(item.get("id") or "")
            if not digest or not evidence_id:
                continue
            if digest in by_digest and by_digest[digest] != evidence_id:
                ambiguous.add(digest)
            else:
                by_digest[digest] = evidence_id
    for digest in ambiguous:
        by_digest.pop(digest, None)

    changed = 0

    def visit(node: Any) -> None:
        nonlocal changed
        if isinstance(node, dict):
            refs = node.get("evidence_refs")
            if isinstance(refs, list):
                for index, ref in enumerate(refs):
                    raw = str(ref)
                    digest = raw.removeprefix("ev-")
                    canonical = by_digest.get(digest)
                    if canonical and canonical != raw:
                        refs[index] = canonical
                        changed += 1
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return changed


def _recover_agent_result(
    previous: dict[str, Any] | None,
    *,
    snapshot: dict[str, Any],
    evaluation_cfg: dict[str, Any],
    longitudinal_available: bool,
) -> ExternalAgentResult | None:
    """Reuse a completed Agent event log when deterministic validation evolved."""

    if not previous or previous.get("snapshot_id") != snapshot.get("snapshot_id"):
        return None
    evaluator = previous.get("evaluator") or {}
    if (
        evaluator.get("status") != "unavailable"
        or not str(evaluator.get("error") or "").startswith("unknown evidence refs:")
        or evaluator.get("model") != evaluation_cfg.get("model")
        or evaluator.get("prompt_version") != evaluation_cfg.get("prompt_version")
        or evaluator.get("schema_version") != evaluation_cfg.get("schema_version")
    ):
        return None
    raw_path = evaluator.get("event_log_path")
    if not raw_path:
        return None
    workspace = omni_workspace_root().resolve()
    allowed_root = (
        workspace / "data" / "services" / "ai_learning_observatory" / "agent_runs"
    ).resolve()
    event_path = Path(str(raw_path)).expanduser().resolve()
    try:
        event_path.relative_to(allowed_root)
    except ValueError:
        return None
    if not event_path.is_file():
        return None

    events: list[ExternalAgentEvent] = []
    structured: dict[str, Any] | None = None
    try:
        for line in event_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            event = ExternalAgentEvent(
                type=str(row.get("type") or ""),
                message=str(row.get("message") or ""),
                payload=row.get("payload") if isinstance(row.get("payload"), dict) else {},
                ts=float(row.get("ts") or 0.0),
            )
            events.append(event)
            item = event.payload.get("item")
            if (
                event.type == "item.completed"
                and isinstance(item, dict)
                and item.get("type") == "agent_message"
            ):
                candidate = json.loads(str(item.get("text") or ""))
                if isinstance(candidate, dict):
                    structured = candidate
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if structured is None or not agent_tool_calls(
        ExternalAgentResult(
            run_id=str(evaluator.get("agent_run_id") or "recovered-agent-run"),
            provider="codex",
            status=ExternalAgentStatus.SUCCEEDED,
            structured_output=structured,
            events=events,
        )
    ):
        return None

    normalization_count = _normalize_evidence_digest_refs(structured, snapshot)
    if _validate_schema(structured) or _invalid_refs(
        structured, _all_evidence_refs(snapshot)
    ):
        return None
    if (
        not longitudinal_available
        and (structured.get("comparison") or {}).get("status")
        != "not_comparable"
    ):
        return None
    return ExternalAgentResult(
        run_id=str(evaluator.get("agent_run_id") or "recovered-agent-run"),
        provider="codex",
        status=ExternalAgentStatus.SUCCEEDED,
        structured_output=structured,
        events=events,
        duration_ms=float(evaluator.get("latency_ms") or 0.0),
        raw={
            "recovered_from_event_log": str(event_path),
            "evidence_ref_normalizations": normalization_count,
        },
    )


def _estimated_cost(model: str, usage: dict[str, int]) -> float | None:
    try:
        from omnicompany.runtime.llm.llm import _estimate_cost
        return _estimate_cost(
            model,
            usage["input_tokens"],
            usage["output_tokens"],
            usage["cache_read_tokens"],
            usage["cache_creation_tokens"],
        )
    except Exception:  # noqa: BLE001 - metering absence must not invalidate the assessment.
        return None


__all__ = ["ASSESSMENT_SCHEMA", "evaluate_snapshot"]
