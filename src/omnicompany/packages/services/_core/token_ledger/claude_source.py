# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=module summary="token_ledger claude 会话收集器: 扫 ~/.claude/projects/<编码>/*.jsonl 顶层会话(排除 subagents/workflows 子目录), 按会话聚合 by_model usage, 水位线按字节增量" why="overnight-run.md 第六节错误样本㊀㊁: 水位线不重复计费+子agent产物不虚增顶层会话数(侦察结论 85→6700+ 的虚增根因)" tags=token-ledger,claude,usage,watermark
"""claude 会话用量收集器。

目录形状(侦察已核实):
    ~/.claude/projects/<路径编码>/<session_id>.jsonl   —— 顶层会话(唯一权威)
    ~/.claude/projects/<路径编码>/subagents/*.jsonl     —— 子agent产物, 必须排除
    ~/.claude/projects/<路径编码>/workflows/*.jsonl     —— workflow 产物, 必须排除

usage 记账语义: assistant 消息的 message.usage 字段是"一次 API 调用"的用量,
同一条消息可能有多个 content block, 但 usage 只在消息级出现一次 —— 不可逐 block 累加,
一条 assistant 消息 = calls 里的一次。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._jsonl_util import parse_jsonl_lines, read_new_lines

_EXCLUDED_DIR_NAMES = {"subagents", "workflows"}

_USAGE_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _empty_model_bucket() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


@dataclass
class ClaudeSessionUsage:
    """一条 claude 顶层会话的用量聚合(按 by_model 分列)。"""

    session_id: str
    source_path: str
    cwd: str = ""
    by_model: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: int = 0
    started: str | None = None
    ended: str | None = None
    provider: str = "claude"


@dataclass
class ClaudeCollectResult:
    sessions: list[ClaudeSessionUsage] = field(default_factory=list)
    new_watermark_state: dict[str, Any] = field(default_factory=dict)
    skipped_bad_lines: int = 0


def _is_excluded(project_dir: Path, jsonl_path: Path) -> bool:
    """判断某 jsonl 文件是否落在 subagents/ 或 workflows/ 子目录下(顶层会话必须排除这些)。"""
    try:
        rel = jsonl_path.relative_to(project_dir)
    except ValueError:
        return False
    return any(part in _EXCLUDED_DIR_NAMES for part in rel.parts[:-1])


def _iter_top_level_session_files(root: Path) -> list[Path]:
    """列出 root(projects 目录)下每个项目子目录里的顶层会话 jsonl(排除 subagents/workflows)。"""
    out: list[Path] = []
    if not root.is_dir():
        return out
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for jsonl_path in sorted(project_dir.glob("**/*.jsonl")):
            if _is_excluded(project_dir, jsonl_path):
                continue
            out.append(jsonl_path)
    return out


def _apply_usage(bucket: dict[str, Any], usage: dict[str, Any]) -> None:
    bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
    bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
    bucket["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
    bucket["cache_creation_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)


def _process_records(records: list[dict[str, Any]]) -> ClaudeSessionUsage | None:
    """把一批(可能只是增量新增的)assistant/user 记录折叠成一份会话用量增量。

    没有任何 assistant usage 的记录集返回 None(没有新调用值得记)。
    """
    session_id: str | None = None
    cwd = ""
    by_model: dict[str, dict[str, Any]] = {}
    calls = 0
    started: str | None = None
    ended: str | None = None

    for rec in records:
        rec_session_id = rec.get("sessionId")
        if rec_session_id and not session_id:
            session_id = rec_session_id
        rec_cwd = rec.get("cwd")
        if rec_cwd and not cwd:
            cwd = rec_cwd
        ts = rec.get("timestamp")
        if ts:
            if started is None:
                started = ts
            ended = ts

        if rec.get("type") != "assistant":
            continue
        message = rec.get("message") or {}
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        model = str(message.get("model") or "unknown")
        bucket = by_model.setdefault(model, _empty_model_bucket())
        _apply_usage(bucket, usage)
        calls += 1

    if calls == 0 and session_id is None:
        return None

    return ClaudeSessionUsage(
        session_id=session_id or "unknown-session",
        source_path="",
        cwd=cwd,
        by_model=by_model,
        calls=calls,
        started=started,
        ended=ended,
    )


def _merge_sessions(base: ClaudeSessionUsage | None, new: ClaudeSessionUsage) -> ClaudeSessionUsage:
    if base is None:
        return new
    for model, bucket in new.by_model.items():
        dest = base.by_model.setdefault(model, _empty_model_bucket())
        for k, v in bucket.items():
            dest[k] = dest.get(k, 0) + v
    base.calls += new.calls
    if not base.cwd and new.cwd:
        base.cwd = new.cwd
    if new.started and (base.started is None or new.started < base.started):
        base.started = new.started
    if new.ended and (base.ended is None or new.ended > base.ended):
        base.ended = new.ended
    return base


def collect_claude_sessions(
    root: Path,
    *,
    watermark_state: dict[str, Any] | None = None,
) -> ClaudeCollectResult:
    """扫 root(默认应传 ~/.claude/projects)下顶层会话 jsonl, 按字节水位线增量聚合用量。

    Args:
        root: `projects` 目录(测试传 tmp fixture, 生产传 Path.home()/".claude"/"projects")。
        watermark_state: 上次 collect 返回的 new_watermark_state, 省略则视为从头全量扫描。
    """
    watermark_state = watermark_state or {}
    new_state: dict[str, Any] = dict(watermark_state)
    result = ClaudeCollectResult(new_watermark_state=new_state)

    for jsonl_path in _iter_top_level_session_files(root):
        key = str(jsonl_path)
        prev_offset = int(watermark_state.get(key, 0) or 0)
        lines, new_offset, _start = read_new_lines(jsonl_path, prev_offset=prev_offset)
        new_state[key] = new_offset
        if not lines:
            continue
        records, skipped = parse_jsonl_lines(lines)
        result.skipped_bad_lines += skipped
        session = _process_records(records)
        if session is None:
            continue
        session.source_path = str(jsonl_path)
        result.sessions.append(session)

    return result


__all__ = ["ClaudeSessionUsage", "ClaudeCollectResult", "collect_claude_sessions"]
