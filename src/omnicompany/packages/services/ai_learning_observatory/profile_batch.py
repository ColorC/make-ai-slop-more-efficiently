# [OMNI] origin=codex created_by=codex intent=one-day-official-luna-profile-bootstrap
"""One-day official Codex/Luna project evaluation-profile bootstrap."""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentPermissionMode,
    ExternalAgentResult,
    ExternalAgentRunSpec,
    ExternalAgentStatus,
)

from .agent_runtime import agent_tool_calls, agent_usage, run_codex_agent_sync
from .evaluation_profiles import (
    EvaluationProfileError,
    profile_markdown_path,
    profile_path,
    render_profile_markdown,
    validate_profile,
)


AgentRunner = Callable[[ExternalAgentRunSpec], ExternalAgentResult]
SINGAPORE = ZoneInfo("Asia/Singapore")


def authorize_profile_analysis(
    config: dict[str, Any],
    *,
    run_date: date | None = None,
) -> dict[str, Any]:
    """Enforce the user's one-day, non-recurring official-channel grant."""

    cfg = config.get("profile_analysis")
    if not isinstance(cfg, dict):
        raise EvaluationProfileError("profile_analysis configuration is missing")
    actual = run_date or datetime.now(SINGAPORE).date()
    authorized_raw = str(cfg.get("authorized_date") or "")
    try:
        authorized = date.fromisoformat(authorized_raw)
    except ValueError as exc:
        raise EvaluationProfileError(
            f"invalid profile_analysis authorized_date: {authorized_raw}"
        ) from exc
    if actual != authorized:
        raise EvaluationProfileError(
            f"official Luna profile analysis was authorized only for {authorized}; today is {actual}"
        )
    if cfg.get("recurring") is not False:
        raise EvaluationProfileError("profile_analysis.recurring must be false")
    if str(cfg.get("harness") or "") != "codex":
        raise EvaluationProfileError("profile analysis must use the Codex CLI harness")
    if str(cfg.get("provider") or "") != "openai":
        raise EvaluationProfileError("profile analysis must use the official OpenAI provider")
    if str(cfg.get("model") or "") != "gpt-5.6-luna":
        raise EvaluationProfileError("profile analysis must preserve gpt-5.6-luna")
    return {**cfg, "authorized_date": authorized.isoformat()}


def build_profile_agent_spec(
    project: dict[str, Any],
    config: dict[str, Any],
    *,
    evidence_packet: dict[str, Any] | None = None,
    run_date: date | None = None,
) -> ExternalAgentRunSpec:
    policy = authorize_profile_analysis(config, run_date=run_date)
    workspace = omni_workspace_root().resolve()
    schema_setting = policy.get("output_schema_path")
    schema_path = (
        (workspace / str(schema_setting)).resolve()
        if schema_setting
        else (
            Path(__file__).with_name("schemas") / "evaluation_profile.schema.json"
        ).resolve()
    )
    registry = project.get("registry") or {}
    compact_project = {
        "project_id": project.get("project_id") or registry.get("id"),
        "name": project.get("name") or registry.get("name"),
        "work_type": project.get("work_type"),
        "roots": registry.get("roots") or [],
        "index_path": registry.get("index_path"),
        "plan_categories": registry.get("plan_categories") or [],
        "selection_reasons": project.get("selection_reasons") or [],
        "last_active": project.get("last_active"),
    }
    project_id = str(compact_project["project_id"] or "")
    packet = evidence_packet or {"availability": "not_collected"}
    prompt = f"""你是 OmniCompany 工作评测体系的一次性、只读档案分析 Agent。

今天的任务不是证明项目变好了，也不是提出让数字好看的优化建议，而是为一个真实 Project 建立可长期复核的评测契约。先用只读工具检查项目入口、当前计划、用户反馈/Review、测试/构建/runtime/发布等已有真源，再提交结构化档案。

项目索引：
{json.dumps(compact_project, ensure_ascii=False, indent=2)}

观测中心已经从既有真源只读采集的证据包（必须实际分析，不得只复述模板）：
{json.dumps(packet, ensure_ascii=False, indent=2)}

证据包已经包含建立档案所需的中心快照。优先直接据此完成；不要读取与本项目无关的 skill，
不要调用会启动子 Agent、扫描全仓或执行项目命令的工具。只有某个关键语义无法从证据包判断时，
才可只读打开该项目 source_ref 明确指向的单个文件；工具失败后保留未知，不要反复尝试。

固定原则：
1. “事情做得正确、理解准确、选择智能”是第一门槛；完成、数量多、测试多、Review 多都不自动代表好。
2. 五个变化维度必须分别定义：time、token_cost、user_expectation、objective_outcome、human_attention。不得产生总分、权重或跨维排名。
3. 每个任务类型单独写清工作单位、复杂度、规模、风险、绝对质量门和完成证据。不同性质、复杂度、规模、环境、风险或验收标准的任务不得直接比较。
4. 更快、更少 token、用户看得/纠正得更少，只有在正确性门槛成立，且用户预期与客观结果不退化时才可能是更好。
5. “没有记录”只能是未知。尤其没有观看、反馈、纠正、Review 或测试记录，不能推断用户零消耗或结果成功。
6. 客观观测必须解释对象、单位、分母、时间窗和权威源；指标只辅助语义判断，不能代替“用户究竟在乎什么”。
7. 区分 current_baseline.facts、inferences、unknowns；不把计划、自述、文件名或产物存在冒充结果。
8. next_connections 只能写观测中心应从哪些既有真源继续接通，不得要求目标项目为了评测新增填表、留痕、测试或人工流程。
9. source_connections.current_strength 必须诚实使用 connected / partial / discovered_only / missing；发现文件名但未解析结果只能算 discovered_only。
10. 全部人读文本使用自然、具体的中文；内部 id 使用稳定英文短名。

篇幅纪律：user_concerns 取 2-4 个；task_classes 取 2-5 个；每个字符串列表优先 2-5 条；current_baseline 只保留最关键事实、推断、未知与接通项。不要写“我将检查/稍后核对”，而要基于上面的已采集证据给出当前档案；证据不足就明确未知。若证据包仍不能回答项目语义，可再用只读工具打开 source_ref 或项目真源。

在输出前自检：五个维度恰好各一次；comparison_policy 四个布尔值均为 true；project_id 必须是 {project_id}；没有任何综合分或“多就是好”的暗示。"""
    run_id = f"evaluation-profile-{project_id}-{uuid.uuid4().hex}"
    return ExternalAgentRunSpec(
        provider="codex",
        harness_id="codex",
        model_provider="openai",
        prompt=prompt,
        cwd=workspace,
        permission_mode=ExternalAgentPermissionMode.READONLY,
        run_id=run_id,
        model="gpt-5.6-luna",
        timeout_s=float(policy.get("timeout_seconds") or 900),
        output_schema_path=schema_path,
        metadata={
            "entrypoint": "ai_learning_observatory.profile_batch",
            # This makes Codex use built-in official auth/provider defaults and
            # prevents user/provider configuration from redirecting the call.
            "minimal_context": True,
            "official_openai_channel": True,
            "authorized_date": policy["authorized_date"],
            "recurring": False,
            "event_log_redact_tool_output": True,
            "event_log_path": (
                workspace
                / "data"
                / "services"
                / "ai_learning_observatory"
                / "agent_runs"
                / f"{run_id}.jsonl"
            ),
            "idle_timeout_s": float(policy.get("idle_timeout_seconds") or 240),
            "codex_config": {
                "model_reasoning_effort": json.dumps(
                    str(policy.get("reasoning_effort") or "high")
                )
            },
        },
    )


def generate_profile_batch(
    service: Any,
    *,
    project_ids: list[str] | None = None,
    force: bool = False,
    concurrency: int = 3,
    run_date: date | None = None,
    agent_runner: AgentRunner | None = None,
) -> dict[str, Any]:
    """Generate profiles through bounded parallel official Codex Agents."""

    config = service.config
    policy = authorize_profile_analysis(config, run_date=run_date)
    discovery = service.discover()
    rows = discovery.get("selected") or []
    if project_ids:
        requested = set(project_ids)
        candidates = rows + (discovery.get("candidates") or [])
        by_id = {str(row.get("project_id")): row for row in candidates}
        missing = sorted(requested - set(by_id))
        if missing:
            raise EvaluationProfileError(f"unknown projects: {', '.join(missing)}")
        rows = [by_id[item] for item in project_ids]
    runner = agent_runner or run_codex_agent_sync
    batch_id = f"profile-batch-{uuid.uuid4().hex[:20]}"
    started = datetime.now(SINGAPORE).isoformat()
    results: list[dict[str, Any]] = []
    generated: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    jobs: list[tuple[dict[str, Any], dict[str, Any], ExternalAgentRunSpec, Path]] = []

    for project in rows:
        project_id = str(project.get("project_id") or "")
        target = profile_path(project_id, config)
        markdown_target = profile_markdown_path(project_id, config)
        if target.is_file() and not force:
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
                issues = validate_profile(existing, expected_project_id=project_id)
            except (OSError, json.JSONDecodeError) as exc:
                issues = [str(exc)]
            if not issues:
                results.append(
                    {
                        "project_id": project_id,
                        "status": "reused",
                        "profile_path": str(target),
                        "markdown_path": str(markdown_target),
                        "usage": _zero_usage(),
                    }
                )
                continue

        snapshot, _ = service.collect(project)
        evidence_packet = _profile_evidence_packet(project, snapshot)
        spec = build_profile_agent_spec(
            project,
            config,
            evidence_packet=evidence_packet,
            run_date=run_date,
        )
        jobs.append((project, snapshot, spec, target))

    max_workers = max(1, min(int(concurrency), 4, len(jobs) or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending = {
            executor.submit(_run_profile_agent, runner, job): job
            for job in jobs
        }
        for future in as_completed(pending):
            project, snapshot, spec, target = pending[future]
            project_id = str(project.get("project_id") or "")
            try:
                profile, result, elapsed_ms = future.result()
                issues = validate_profile(profile, expected_project_id=project_id)
                if issues:
                    raise EvaluationProfileError("; ".join(issues))
                provenance = {
                    "provider": "OpenAI official",
                    "model": policy["model"],
                    "harness": "Codex CLI",
                    "authorized_date": policy["authorized_date"],
                    "agent_run_id": result.run_id,
                    "event_log_path": str(spec.metadata["event_log_path"]),
                    "official_channel": {
                        "ignore_user_config": True,
                        "custom_model_provider_injected": False,
                        "recurring": False,
                    },
                }
                row = {
                    "project_id": project_id,
                    "status": "generated",
                    "profile_path": str(target),
                    "markdown_path": str(profile_markdown_path(project_id, config)),
                    "agent_run_id": result.run_id,
                    "event_log_path": provenance["event_log_path"],
                    "tool_call_count": len(agent_tool_calls(result)),
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "evidence_item_count": sum(
                        len(source.get("items") or [])
                        for source in (snapshot.get("sources") or {}).values()
                    ),
                    "usage": agent_usage(result),
                    "latency_ms": elapsed_ms,
                }
                results.append(row)
                generated.append((profile, provenance, row))
            except Exception as exc:  # noqa: BLE001 - preserve per-project failure.
                result = getattr(exc, "agent_result", None)
                results.append(
                    {
                        "project_id": project_id,
                        "status": "failed",
                        "profile_path": str(target),
                        "usage": (
                            agent_usage(result)
                            if isinstance(result, ExternalAgentResult)
                            else _zero_usage()
                        ),
                        "error": str(exc),
                    }
                )

    order = {str(row.get("project_id")): index for index, row in enumerate(rows)}
    results.sort(key=lambda row: order.get(str(row.get("project_id")), 10_000))

    # Persist only after Agent work is finished so read-only worker baselines
    # cannot mistake another run's profile output for a child write.
    for profile, provenance, row in generated:
        target = Path(row["profile_path"])
        markdown_target = Path(row["markdown_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        markdown_target.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(target, profile)
        _write_text_atomic(
            markdown_target,
            render_profile_markdown(profile, provenance=provenance),
        )

    finished = datetime.now(SINGAPORE).isoformat()
    total_usage = {
        key: sum(int((row.get("usage") or {}).get(key) or 0) for row in results)
        for key in _zero_usage()
    }
    failed = [row for row in results if row["status"] == "failed"]
    manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "started_at": started,
        "finished_at": finished,
        "authorized_date": policy["authorized_date"],
        "one_off": True,
        "recurring": False,
        "harness": "codex",
        "provider": "openai",
        "official_channel": {
            "ignore_user_config": True,
            "custom_model_provider_injected": False,
        },
        "model": "gpt-5.6-luna",
        "reasoning_effort": policy.get("reasoning_effort") or "high",
        "concurrency": max_workers,
        "project_ids": [str(row.get("project_id")) for row in rows],
        "results": results,
        "usage": total_usage,
        "status": "ok" if results and not failed else "partial" if results else "failed",
    }
    manifest_root = (
        omni_workspace_root()
        / "data"
        / "services"
        / "ai_learning_observatory"
        / "profile_runs"
    )
    manifest_root.mkdir(parents=True, exist_ok=True)
    manifest_path = (manifest_root / f"{batch_id}.json").resolve()
    manifest["manifest_path"] = str(manifest_path)
    _write_json_atomic(manifest_path, manifest)
    return manifest


def _zero_usage() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def _run_profile_agent(
    runner: AgentRunner,
    job: tuple[dict[str, Any], dict[str, Any], ExternalAgentRunSpec, Path],
) -> tuple[dict[str, Any], ExternalAgentResult, float]:
    _project, _snapshot, spec, _target = job
    start = time.monotonic()
    result = runner(spec)
    elapsed_ms = round((time.monotonic() - start) * 1000, 2)
    if result.normalized_status() != ExternalAgentStatus.SUCCEEDED:
        error = EvaluationProfileError(result.error or "official Codex Agent failed")
        error.agent_result = result  # type: ignore[attr-defined]
        raise error
    profile = result.structured_output
    if not isinstance(profile, dict):
        error = EvaluationProfileError("official Codex Agent returned no structured profile")
        error.agent_result = result  # type: ignore[attr-defined]
        raise error
    return profile, result, elapsed_ms


def _profile_evidence_packet(
    project: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    registry = project.get("registry") or {}
    index_path = Path(str(registry.get("index_path") or ""))
    index_excerpt = ""
    if index_path.is_file():
        try:
            size = index_path.stat().st_size
            if size <= 120_000:
                index_excerpt = index_path.read_text(encoding="utf-8", errors="replace")
            else:
                index_excerpt = "\n".join(
                    index_path.read_text(encoding="utf-8", errors="replace").splitlines()[:400]
                )
        except OSError:
            index_excerpt = ""
    sources: dict[str, Any] = {}
    for name, source in (snapshot.get("sources") or {}).items():
        items = source.get("items") or []
        sources[name] = {
            "status": source.get("status"),
            "errors": source.get("errors") or [],
            "items": [
                {
                    "id": item.get("id"),
                    "kind": item.get("kind"),
                    "source_ref": item.get("source_ref"),
                    "summary": item.get("summary"),
                    "data": item.get("data"),
                }
                for item in items[:40]
            ],
            "omitted_item_count": max(0, len(items) - 40),
        }
    return {
        "snapshot_id": snapshot.get("snapshot_id"),
        "collected_at": snapshot.get("collected_at"),
        "coverage": snapshot.get("coverage"),
        "project": snapshot.get("project"),
        "work_units": snapshot.get("work_units"),
        "project_index_path": str(index_path) if str(index_path) else None,
        "project_index_excerpt": index_excerpt[:120_000],
        "sources": sources,
        "interpretation_rule": (
            "items are bounded source-linked projections; filenames and declared plans are not outcomes"
        ),
    }


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    _write_text_atomic(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _write_text_atomic(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


__all__ = [
    "authorize_profile_analysis",
    "build_profile_agent_spec",
    "generate_profile_batch",
]
