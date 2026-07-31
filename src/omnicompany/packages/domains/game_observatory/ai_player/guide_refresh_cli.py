"""Click commands for the trigger-scoped guide refresh request/receipt queue."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from .contracts import EvidenceReferenceV1
from .guide_refresh import (
    GuideResearchResultBundleV1,
    build_refresh_request,
    build_retry_request,
    build_refresh_work_item,
    contradicted_successor,
)


TRIGGER_CHOICES = (
    "first_entry_to_new_system",
    "high_value_hard_to_reverse_choice",
    "version_or_season_change",
    "two_consecutive_failures",
)


def _emit(context: Any, payload: dict[str, Any], summary: str) -> None:
    body = {"ok": True, **payload}
    if getattr(context, "as_json", False):
        click.echo(
            json.dumps(
                body,
                ensure_ascii=False,
                indent=2,
                default=lambda value: value.model_dump(mode="json", by_alias=True),
            )
        )
    else:
        click.echo(summary)


def _evidence_from_step(context: Any, environment_id: str, step_id: str) -> EvidenceReferenceV1:
    step = context.facility().store.get_evidence_step(step_id)
    if step is None:
        raise click.ClickException(f"EvidenceStep 不存在：{step_id}")
    reference = EvidenceReferenceV1(
        environment_id=environment_id,
        artifact_ids=step.artifact_ids,
        evidence_run_ids=[step.evidence_run_id],
        evidence_step_ids=[step.id],
        trace_run_ids=step.observation_run_ids,
        note="时效攻略触发时的当前实机证据。",
    )
    try:
        context.player().resolve_evidence_references([reference])
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    return reference


@click.group("refresh")
def guide_refresh_group() -> None:
    """触发式时效攻略刷新；A2 只提交请求，不等待研究 worker。"""


@guide_refresh_group.command("submit")
@click.option("--environment", "environment_id", required=True)
@click.option("--task", "task_id", required=True)
@click.option("--trigger", type=click.Choice(TRIGGER_CHOICES), required=True)
@click.option("--query", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--current-state", "current_state_id", default=None)
@click.option("--game-version", default=None)
@click.option("--season", default=None)
@click.option("--server-stage", default=None)
@click.option("--consecutive-failures", type=click.IntRange(0), default=0)
@click.pass_obj
def submit_refresh(
    context: Any,
    environment_id: str,
    task_id: str,
    trigger: str,
    query: str,
    source_step_id: str,
    current_state_id: str | None,
    game_version: str | None,
    season: str | None,
    server_stage: str | None,
    consecutive_failures: int,
) -> None:
    """把当前环境、任务、证据、查询和触发器写入持久队列后立即返回。"""

    player = context.player()
    environment = player.get_environment(environment_id)
    if environment is None:
        raise click.ClickException(f"AI 玩家环境不存在：{environment_id}")
    task = player.get_task(environment_id, task_id)
    if task is None:
        raise click.ClickException(f"前沿任务不存在：{task_id}")
    evidence = _evidence_from_step(context, environment_id, source_step_id)
    try:
        request = build_refresh_request(
            environment=environment,
            task=task,
            trigger=trigger,
            query=query,
            evidence_refs=[evidence],
            game_version=game_version,
            season=season,
            server_stage=server_stage,
            current_state_id=current_state_id,
            consecutive_failures=consecutive_failures,
        )
        stored = player.append_guide_refresh_request(request)
    except (KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.guide-refresh-submit-result.v1",
            "request": stored,
            "pending": player.get_guide_refresh_receipt(stored.id) is None,
        },
        f"攻略刷新请求已入队：{stored.id}",
    )


@guide_refresh_group.command("pending")
@click.option("--environment", "environment_id", required=True)
@click.pass_obj
def list_pending(context: Any, environment_id: str) -> None:
    """列出尚无终态回执的刷新请求，供独立 worker 消费。"""

    try:
        requests = context.player().list_guide_refresh_requests(
            environment_id,
            pending_only=True,
        )
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.guide-refresh-pending-list.v1",
            "requests": requests,
        },
        f"待研究攻略请求：{len(requests)} 条",
    )


@guide_refresh_group.command("work-item")
@click.option("--request", "request_id", required=True)
@click.pass_obj
def show_work_item(context: Any, request_id: str) -> None:
    """输出 research.run 的严格消费合同；本命令不启动、不等待 worker。"""

    player = context.player()
    request = player.get_guide_refresh_request(request_id)
    if request is None:
        raise click.ClickException(f"攻略刷新请求不存在：{request_id}")
    receipt = player.get_guide_refresh_receipt(request_id)
    if receipt is not None:
        raise click.ClickException(f"攻略刷新请求已有终态：{receipt.status}")
    work_item = build_refresh_work_item(request)
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.guide-refresh-work-item-result.v1",
            "work_item": work_item,
        },
        f"攻略研究工作项：{request.id}",
    )


@guide_refresh_group.command("complete")
@click.option("--request", "request_id", required=True)
@click.option(
    "--file",
    "bundle_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.pass_obj
def complete_refresh(context: Any, request_id: str, bundle_path: Path) -> None:
    """消费独立 worker 的严格结果包；缺来源或适用性声明会失败关闭。"""

    try:
        bundle = GuideResearchResultBundleV1.model_validate_json(
            bundle_path.read_text(encoding="utf-8")
        )
        if bundle.request_id != request_id:
            raise ValueError("guide research bundle request_id does not match --request")
        receipt, counts = context.player().complete_guide_refresh_request(bundle)
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.guide-refresh-complete-result.v1",
            "receipt": receipt,
            "inserted": counts,
        },
        f"攻略刷新已完成：{request_id}",
    )


@guide_refresh_group.command("finish")
@click.option("--request", "request_id", required=True)
@click.option(
    "--status",
    type=click.Choice(["offline", "source_unavailable", "failed"]),
    required=True,
)
@click.option("--detail", required=True)
@click.pass_obj
def finish_refresh(context: Any, request_id: str, status: str, detail: str) -> None:
    """为离线、来源不可用或研究失败写入明确终态，避免悬空。"""

    try:
        receipt = context.player().terminate_guide_refresh_request(
            request_id,
            status=status,
            detail=detail,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.guide-refresh-finish-result.v1",
            "receipt": receipt,
        },
        f"攻略刷新终态：{status}",
    )


@guide_refresh_group.command("retry")
@click.option("--request", "request_id", required=True)
@click.pass_obj
def retry_refresh(context: Any, request_id: str) -> None:
    """从离线、来源不可用或失败终态建立新尝试，保留旧请求与回执。"""

    player = context.player()
    request = player.get_guide_refresh_request(request_id)
    if request is None:
        raise click.ClickException(f"攻略刷新请求不存在：{request_id}")
    receipt = player.get_guide_refresh_receipt(request_id)
    if receipt is None:
        raise click.ClickException("攻略刷新请求仍在等待研究，无需重试。")
    try:
        retry = build_retry_request(request, receipt)
        stored = player.append_guide_refresh_request(retry)
    except (KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.guide-refresh-retry-result.v1",
            "request": stored,
            "previous_receipt": receipt,
        },
        f"攻略刷新已重新入队：{stored.id}",
    )


@guide_refresh_group.command("contradict")
@click.option("--environment", "environment_id", required=True)
@click.option("--guide", "guide_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--summary", required=True)
@click.pass_obj
def contradict_refresh_guide(
    context: Any,
    environment_id: str,
    guide_id: str,
    source_step_id: str,
    summary: str,
) -> None:
    """以实机反例追加 contradicted 版本，保留来源和原建议。"""

    player = context.player()
    guide = player.get_guide_knowledge(environment_id, guide_id)
    if guide is None:
        raise click.ClickException(f"攻略知识不存在：{guide_id}")
    evidence = _evidence_from_step(context, environment_id, source_step_id)
    try:
        successor = contradicted_successor(
            guide,
            summary=summary,
            evidence_refs=[evidence],
        )
        stored = player.append_guide_knowledge(successor)
    except (KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.guide-contradiction-result.v1",
            "guide": stored,
        },
        f"攻略已按实机反例降级：{stored.id}@{stored.version}",
    )


__all__ = ["guide_refresh_group"]
