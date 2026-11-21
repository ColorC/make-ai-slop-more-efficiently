"""CLI for the multi-project AI learning observatory."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import click

from omnicompany.packages.services.ai_learning_observatory import (
    ConversationAttributionService,
    LearningObservatoryService,
    run_batch,
    select_batch_candidates,
    backfill_batch_bindings,
    backfill_batch_bindings_with_semantic_plans,
    latest_batch_manifest,
    generate_profile_batch,
)
from omnicompany.packages.services.ai_learning_observatory.conversation_attribution import (
    ConversationAttributionError,
    list_session_candidates,
    session_event_outline,
    session_event_range,
)


def _emit(value: Any) -> None:
    click.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


@click.group("learning-observatory")
def cmd_learning_observatory() -> None:
    """Observe how AI work changes across real projects without metric gaming."""


@cmd_learning_observatory.command("discover")
def discover_command() -> None:
    """Show the dynamically selected multi-project portfolio."""
    _emit(LearningObservatoryService().discover())


@cmd_learning_observatory.command("collect")
@click.option("--project", "project_ids", multiple=True, help="Project id; defaults to the selected portfolio.")
def collect_command(project_ids: tuple[str, ...]) -> None:
    """Collect and persist bounded evidence without model evaluation."""
    service = LearningObservatoryService()
    discovery = service.discover()
    requested = set(project_ids)
    selected = [row for row in discovery["selected"] if not requested or row["project_id"] in requested]
    missing = sorted(requested - {row["project_id"] for row in selected})
    results = []
    for row in selected:
        snapshot, created = service.collect(row)
        results.append({"project_id": row["project_id"], "snapshot_id": snapshot["snapshot_id"], "created": created})
    _emit({"results": results, "missing_projects": missing})


@cmd_learning_observatory.command("evaluate")
@click.argument("snapshot_id")
@click.option("--project", "project_id", help="Project id, avoiding a store-wide lookup.")
@click.option("--deterministic-only", is_flag=True, help="Skip the semantic model call.")
@click.option("--force", is_flag=True, help="Re-evaluate even if the snapshot already has a successful assessment.")
def evaluate_command(snapshot_id: str, project_id: str | None, deterministic_only: bool, force: bool) -> None:
    """Evaluate one stored snapshot."""
    service = LearningObservatoryService()
    snapshot = service.store.read_snapshot(snapshot_id, project_id=project_id)
    assessment, created = service.evaluate(snapshot, deterministic_only=deterministic_only, force=force)
    _emit({**assessment, "created": created})
    if assessment.get("evaluator", {}).get("status") == "unavailable":
        raise click.exceptions.Exit(2)


@cmd_learning_observatory.command("run")
@click.option("--project", "project_ids", multiple=True, help="Limit the run to named project ids.")
@click.option("--deterministic-only", is_flag=True, help="Collect and score structure without calling Terra.")
@click.option("--dry-run", is_flag=True, help="Discover only; do not write or call a model.")
@click.option("--force-evaluate", is_flag=True, help="Re-run evaluation for unchanged evidence.")
@click.option("--changed-since", help="Only include projects active since this ISO timestamp.")
@click.option("--time-window-days", type=click.IntRange(min=1), help="Override the recent evidence window.")
@click.option("--business", "business_ids", multiple=True, help="Limit the comparison projection to stable Business ids.")
def run_command(
    project_ids: tuple[str, ...],
    deterministic_only: bool,
    dry_run: bool,
    force_evaluate: bool,
    changed_since: str | None,
    time_window_days: int | None,
    business_ids: tuple[str, ...],
) -> None:
    """Run discovery, collection, persistence and evaluation end to end."""
    result = LearningObservatoryService().run(
        project_ids=list(project_ids) or None,
        deterministic_only=deterministic_only,
        dry_run=dry_run,
        force_evaluate=force_evaluate,
        changed_since=changed_since,
        time_window_days=time_window_days,
        business_ids=list(business_ids) or None,
    )
    _emit(result)
    if not dry_run and result.get("status") != "ok":
        raise click.exceptions.Exit(2)


@cmd_learning_observatory.command("status")
def status_command() -> None:
    """Show the latest read-only monitoring projection."""
    _emit(LearningObservatoryService().status())


@cmd_learning_observatory.command("profile-batch")
@click.option("--project", "project_ids", multiple=True, help="Limit the one-off batch to project ids.")
@click.option("--force", is_flag=True, help="Replace an existing valid profile after a fresh official analysis.")
@click.option("--concurrency", type=click.IntRange(min=1, max=4), default=3, show_default=True)
def profile_batch_command(project_ids: tuple[str, ...], force: bool, concurrency: int) -> None:
    """Build project evaluation profiles through today's official Codex/Luna grant."""

    try:
        result = generate_profile_batch(
            LearningObservatoryService(),
            project_ids=list(project_ids) or None,
            force=force,
            concurrency=concurrency,
        )
    except ValueError as exc:
        _emit({"状态": "失败", "原因": str(exc)})
        raise click.exceptions.Exit(2) from exc
    _emit(result)
    if result.get("status") != "ok":
        raise click.exceptions.Exit(2)


@cmd_learning_observatory.command("show")
@click.argument("record_id")
@click.option("--project", "project_id", help="Project id for snapshot/assessment lookup.")
def show_command(record_id: str, project_id: str | None) -> None:
    """Show one immutable snapshot, assessment, or run."""
    _emit(LearningObservatoryService().show(record_id, project_id=project_id))


@cmd_learning_observatory.command("sessions")
@click.option("--limit", type=click.IntRange(min=1, max=200), default=20, show_default=True)
def sessions_command(limit: int) -> None:
    """列出最近的 Codex 原始会话候选，不调用模型。"""
    rows = list_session_candidates(limit=limit)
    _emit(
        {
            "状态": "完成",
            "会话数量": len(rows),
            "会话": rows,
        }
    )


@cmd_learning_observatory.command("inspect-session")
@click.argument("session_path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--outline", is_flag=True, help="输出完整事件索引，不输出正文。")
@click.option("--start-event", help="读取范围的起始事件，例如 e000001。")
@click.option("--end-event", help="读取范围的结束事件，例如 e000050。")
@click.option("--search", help="在完整事件正文中搜索，不截断命中正文。")
def inspect_session_command(
    session_path: Path,
    outline: bool,
    start_event: str | None,
    end_event: str | None,
    search: str | None,
) -> None:
    """供只读 Agent 自主浏览完整会话；外层不预切块、不预截断。"""

    try:
        if outline or not (start_event or end_event or search):
            result = session_event_outline(session_path)
        else:
            result = session_event_range(
                session_path,
                start_event=start_event,
                end_event=end_event,
                search=search,
            )
    except ConversationAttributionError as exc:
        _emit({"状态": "失败", "原因": str(exc), "会话路径": str(session_path)})
        raise click.exceptions.Exit(2) from exc
    _emit(result)


@cmd_learning_observatory.command("attribute-session")
@click.argument("session_path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--force", is_flag=True, help="忽略相同源哈希的既有完成目录并重新运行。")
def attribute_session_command(
    session_path: Path,
    force: bool,
) -> None:
    """用只读 DeepSeek-Codex 提取并编纂一个完整历史会话。"""
    try:
        result = asyncio.run(
            ConversationAttributionService().attribute_session(
                session_path,
                force=force,
            )
        )
    except ConversationAttributionError as exc:
        _emit({"状态": "失败", "原因": str(exc), "会话路径": str(session_path)})
        raise click.exceptions.Exit(2) from exc
    _emit(
        {
            "状态": result["status_zh"],
            "会话": result["session_id"],
            "Agent运行次数": result.get("agent_run_count", 0),
            "失败项": result.get("failure_count", 0),
            "全局Codex配置未变": result.get("global_codex_config_unchanged", True),
            "清单": result["manifest_path"],
            "目录": result.get("catalog_path") or "尚未生成",
        }
    )
    if result["status"] not in {"complete", "reused"}:
        raise click.exceptions.Exit(2)


@cmd_learning_observatory.command("attribute-batch")
@click.option("--limit", type=click.IntRange(min=1, max=100), default=10, show_default=True)
@click.option("--candidate-limit", type=click.IntRange(min=10, max=2000), default=240, show_default=True)
@click.option("--concurrency", type=click.IntRange(min=1, max=4), default=2, show_default=True)
@click.option("--max-size-mb", type=click.FloatRange(min=0.1, max=100), default=2.0, show_default=True)
@click.option("--quiet-minutes", type=click.IntRange(min=1), default=10, show_default=True)
@click.option("--selection-only", is_flag=True, help="只显示确定性选择，不调用模型。")
@click.option("--force", is_flag=True, help="忽略可复用投影并重新运行模型。")
def attribute_batch_command(
    limit: int,
    candidate_limit: int,
    concurrency: int,
    max_size_mb: float,
    quiet_minutes: int,
    selection_only: bool,
    force: bool,
) -> None:
    """选择稳定真实会话，批量归因并生成谨慎的纵向汇总。"""

    candidates = select_batch_candidates(
        limit=limit,
        candidate_limit=candidate_limit,
        max_size_bytes=int(max_size_mb * 1024 * 1024),
        quiet_seconds=quiet_minutes * 60,
    )
    if selection_only:
        _emit({"状态": "完成", "候选数": len(candidates), "候选": candidates})
        return
    if len(candidates) < limit:
        _emit(
            {
                "状态": "失败",
                "原因": f"只找到 {len(candidates)} 个满足稳定性边界的真实主会话，少于要求的 {limit} 个",
                "候选": candidates,
            }
        )
        raise click.exceptions.Exit(2)
    result = asyncio.run(
        run_batch(
            ConversationAttributionService(),
            candidates,
            concurrency=concurrency,
            force=force,
        )
    )
    _emit(
        {
            "状态": result["status_zh"],
            "批次": result["batch_id"],
            "请求会话": result["requested"],
            "完成会话": result["completed"],
            "部分完成会话": result["partial"],
            "失败会话": result["failed"],
            "批次清单": result["manifest_path"],
            "纵向分析": result["analysis_path"],
        }
    )
    if result["status"] != "complete":
        raise click.exceptions.Exit(2)


@cmd_learning_observatory.command("backfill-bindings")
@click.option(
    "--batch-manifest",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="指定批次 manifest；默认使用最新批次。",
)
@click.option("--dry-run", is_flag=True, help="只显示将补什么，不写会话绑定账本。")
@click.option("--semantic-plans", is_flag=True, help="用 DeepSeek 为缺失 Plan 生成候选绑定。")
@click.option("--concurrency", type=click.IntRange(min=1, max=4), default=2, show_default=True)
def backfill_bindings_command(
    batch_manifest: Path | None,
    dry_run: bool,
    semantic_plans: bool,
    concurrency: int,
) -> None:
    """从已校验会话目录补齐事件范围 Project/Plan/Task 绑定。"""

    workspace = Path.cwd().resolve()
    manifest = batch_manifest or latest_batch_manifest(workspace)
    if semantic_plans:
        result = asyncio.run(
            backfill_batch_bindings_with_semantic_plans(
                manifest,
                workspace=workspace,
                dry_run=dry_run,
                concurrency=concurrency,
            )
        )
    else:
        result = backfill_batch_bindings(manifest, workspace=workspace, dry_run=dry_run)
    _emit(result)
    if result["状态"] != "完成":
        raise click.exceptions.Exit(2)


@cmd_learning_observatory.command("refresh-conversations")
@click.option("--limit", type=click.IntRange(min=1, max=100), default=10, show_default=True)
@click.option("--candidate-limit", type=click.IntRange(min=10, max=2000), default=240, show_default=True)
@click.option("--concurrency", type=click.IntRange(min=1, max=4), default=2, show_default=True)
@click.option("--max-size-mb", type=click.FloatRange(min=0.1, max=100), default=2.0, show_default=True)
@click.option("--quiet-minutes", type=click.IntRange(min=1), default=10, show_default=True)
def refresh_conversations_command(
    limit: int,
    candidate_limit: int,
    concurrency: int,
    max_size_mb: float,
    quiet_minutes: int,
) -> None:
    """增量归因近期稳定会话，并回填事件范围工作归属。"""

    candidates = select_batch_candidates(
        limit=limit,
        candidate_limit=candidate_limit,
        max_size_bytes=int(max_size_mb * 1024 * 1024),
        quiet_seconds=quiet_minutes * 60,
        prefer_untracked=True,
    )
    if len(candidates) < limit:
        _emit(
            {
                "状态": "失败",
                "原因": f"只找到 {len(candidates)} 个稳定会话，少于要求的 {limit} 个",
            }
        )
        raise click.exceptions.Exit(2)
    batch = asyncio.run(
        run_batch(
            ConversationAttributionService(),
            candidates,
            concurrency=concurrency,
        )
    )
    if batch["status"] != "complete":
        _emit({"状态": batch["status_zh"], "批次": batch})
        raise click.exceptions.Exit(2)
    binding_result = asyncio.run(
        backfill_batch_bindings_with_semantic_plans(
            batch["manifest_path"],
            workspace=Path.cwd().resolve(),
            concurrency=concurrency,
        )
    )
    _emit(
        {
            "状态": binding_result["状态"],
            "批次": batch["batch_id"],
            "完成会话": batch["completed"],
            "确认分段": binding_result["确认分段"],
            "候选分段": binding_result["候选分段"],
            "失败": binding_result["失败"],
            "批次清单": batch["manifest_path"],
            "纵向分析": batch["analysis_path"],
        }
    )
    if binding_result["状态"] != "完成":
        raise click.exceptions.Exit(2)


__all__ = ["cmd_learning_observatory"]
