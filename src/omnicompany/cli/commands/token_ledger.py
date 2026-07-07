# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=cli summary="omni token-ledger — token 记账 v1 CLI(run 落盘增量收集/show 只读打印), 不含自由路径参数(数据源/落盘位置写死, 与ledgers.yaml token-ledger登记一致)" why="overnight-run.md 第六节:触发=omni CLI 按需跑+定时任务gov-token-ledger-daily兜底(纯扫描不调LLM)" tags=token-ledger,cli,usage
"""omni token-ledger —— claude/codex 会话用量 + 内部调用账三路归并 CLI。

    跑一遍并落盘(带水位线增量): omni token-ledger run
    只读打印当前聚合(不落盘):     omni token-ledger show [--json]

数据源与落盘位置均写死(与 config/ledgers.yaml 的 token-ledger 登记一致), 不接受
自由路径参数——这是"记录工具本身不接受路径参数"铁律的延伸(见 ledgers.yaml 顶部注释)。
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from omnicompany.core.config import omni_workspace_root

_LLM_HINTS = ("commit-run", "decisions-run", "history-run", "docs-timeliness", "plans-run")


def _claude_projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _codex_sessions_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def _meter_path() -> Path:
    return omni_workspace_root() / "data" / "llm" / "meter.jsonl"


def _out_dir() -> Path:
    return omni_workspace_root() / "data" / "llm" / "token_ledger"


def _load_prior_watermark(out_dir: Path) -> dict:
    p = out_dir / "watermark.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_prior_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def _merge_sessions(prior: list[dict], new: list[dict]) -> list[dict]:
    """按 session_id 合并: 新一批命中同 session_id 时按模型累加 token 数(增量续跑同一份会话),
    否则追加为新行。落盘账本要保留跨多次 run 的完整历史, 不能被下一次增量结果整份覆盖。"""
    by_id = {row["session_id"]: dict(row) for row in prior}
    for row in new:
        sid = row["session_id"]
        if sid not in by_id:
            by_id[sid] = dict(row)
            continue
        base = by_id[sid]
        base_models = dict(base.get("by_model") or {})
        for model, bucket in (row.get("by_model") or {}).items():
            dest = dict(base_models.get(model) or {})
            for k, v in bucket.items():
                dest[k] = dest.get(k, 0) + v
            base_models[model] = dest
        base["by_model"] = base_models
        base["calls"] = int(base.get("calls") or 0) + int(row.get("calls") or 0)
        if row.get("ended") and (not base.get("ended") or row["ended"] > base["ended"]):
            base["ended"] = row["ended"]
        if row.get("started") and (not base.get("started") or row["started"] < base["started"]):
            base["started"] = row["started"]
        by_id[sid] = base
    return list(by_id.values())


def _merge_internal_rows(prior: list[dict], new: list[dict]) -> list[dict]:
    """按 (caller, day, model) 合并累加, 语义同 _merge_sessions。"""
    by_key = {(r["caller"], r["day"], r.get("model", "")): dict(r) for r in prior}
    for row in new:
        key = (row["caller"], row["day"], row.get("model", ""))
        if key not in by_key:
            by_key[key] = dict(row)
            continue
        base = by_key[key]
        for field in ("call_count", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
            base[field] = base.get(field, 0) + row.get(field, 0)
        base["cost_usd"] = round(base.get("cost_usd", 0.0) + row.get("cost_usd", 0.0), 6)
        by_key[key] = base
    return list(by_key.values())


def _known_and_all_cron_task_names() -> tuple[list[str], list[str]]:
    try:
        from omnicompany.packages.services._governance import scheduler

        names = [t.get("name") for t in scheduler.load_tasks() if t.get("name")]
    except Exception:  # noqa: BLE001
        names = []
    known = [n for n in names if any(h in n for h in _LLM_HINTS)]
    return known, names


@click.group("token-ledger")
def cmd_token_ledger() -> None:
    """token 记账: claude/codex 会话用量 + 内部调用账归并(见 config/ledgers.yaml token-ledger 条)。"""


@cmd_token_ledger.command("run")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON 输出摘要")
def token_ledger_run(as_json: bool) -> None:
    """按水位线增量跑一遍三路收集 + 聚合, 与已落盘的历史合并后重写 data/llm/token_ledger/。

    合并语义: 会话按 session_id、内部调用按 (caller, day, model) 累加, 不会被本次
    增量批次(只含水位线之后的新内容)整份覆盖掉此前已落盘的历史行。
    """
    from omnicompany.packages.services._core.token_ledger import (
        aggregate_token_ledger, collect_claude_sessions, collect_codex_sessions,
        collect_internal_usage, write_token_ledger,
    )

    out_dir = _out_dir()
    prior = _load_prior_watermark(out_dir)
    prior_sessions = _load_prior_jsonl(out_dir / "sessions.jsonl")
    prior_internal = _load_prior_jsonl(out_dir / "internal_by_caller_day.jsonl")

    claude = collect_claude_sessions(_claude_projects_root(), watermark_state=prior.get("claude"))
    codex = collect_codex_sessions(_codex_sessions_root(), watermark_state=prior.get("codex"))
    internal = collect_internal_usage(_meter_path(), watermark_state=prior.get("internal"))

    known_llm_tasks, all_tasks = _known_and_all_cron_task_names()
    agg = aggregate_token_ledger(
        claude, codex, internal,
        known_llm_cron_tasks=known_llm_tasks, cron_task_names=all_tasks,
    )

    agg.by_session = _merge_sessions(prior_sessions, agg.by_session)
    agg.internal_by_caller_day = _merge_internal_rows(prior_internal, agg.internal_by_caller_day)

    new_watermark = {
        "claude": claude.new_watermark_state,
        "codex": codex.new_watermark_state,
        "internal": internal.new_watermark_state,
    }
    paths = write_token_ledger(agg, out_dir=out_dir, watermark_state=new_watermark)

    summary = {
        "new_sessions_this_run": len({s.session_id for s in claude.sessions} | {s.session_id for s in codex.sessions}),
        "total_sessions": len(agg.by_session),
        "new_internal_rows_this_run": len(internal.by_caller_day),
        "total_internal_rows": len(agg.internal_by_caller_day),
        "unlinked": sum(1 for row in agg.by_session if not row.get("project")),
        "skipped_bad_lines": {
            "claude": claude.skipped_bad_lines,
            "codex": codex.skipped_bad_lines,
            "internal": internal.skipped_bad_lines,
        },
        "codex_skipped_dirty_rounds_this_run": codex.skipped_dirty_rounds,
        "out_dir": str(out_dir),
        "paths": {k: str(v) for k, v in paths.items()},
    }
    if as_json:
        click.echo(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    click.echo(f"本次新增会话: {summary['new_sessions_this_run']} · 累计会话: {summary['total_sessions']}")
    click.echo(f"本次新增内部调用行: {summary['new_internal_rows_this_run']} · 累计: {summary['total_internal_rows']} · 未关联: {summary['unlinked']}")
    click.echo(f"坏行跳过: {summary['skipped_bad_lines']} · 本次 codex 脏轮次跳过: {summary['codex_skipped_dirty_rounds_this_run']}")
    click.echo(f"落盘: {out_dir}")


@cmd_token_ledger.command("show")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON 输出")
def token_ledger_show(as_json: bool) -> None:
    """只读打印落盘产物聚合(读 data/llm/token_ledger/, 与看板 /api/boss-sight/token-ledger 同一份读法;
    未跑过 `omni token-ledger run` 时 available=false, 不会现场重扫 GB 级原始日志)。"""
    from omnicompany.dashboard.boss_sight.token_ledger_view import build_token_ledger_view

    view = build_token_ledger_view()
    if as_json:
        click.echo(json.dumps(view, ensure_ascii=False, indent=2))
        return
    if not view["available"]:
        click.echo(f"尚无落盘产物({view['out_dir']})。先跑: omni token-ledger run")
        return
    click.echo(f"生成于: {view['generated_at']}")
    click.echo(f"会话: {len(view['by_session'])} · 按天: {len(view['by_day'])} · 按项目: {len(view['by_project'])}")
    click.echo(f"未关联: {len(view['unlinked_bucket'])} · 内部调用行: {len(view['internal_by_caller_day'])}")


__all__ = ["cmd_token_ledger"]
