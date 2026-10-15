# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=module summary="token_ledger claude 会话收集器: 扫 ~/.claude/projects/<编码>/*.jsonl 顶层会话(排除 subagents/workflows 子目录), 按会话聚合 by_model usage, 水位线按字节增量" why="overnight-run.md 第六节错误样本㊀㊁: 水位线不重复计费+子agent产物不虚增顶层会话数(侦察结论 85→6700+ 的虚增根因)" tags=token-ledger,claude,usage,watermark
# [OMNI] origin=claude-code ts=2026-09-25T00:00:00Z type=module summary="两点硬化: ①按 message.id 去重(没有 id 退 requestId, 再没有才按行), 修同一 message.id 连续写多行 assistant 导致按行计数虚增(实测 1940 行只对应 779 条消息) ②<项目目录>/<session>/subagents/agent-*.jsonl 单独扫描记账(会话id=文件名, parent_session_id=上层会话目录名), 不再被顶层过滤连带丢弃" why="真实数据核实: 同一 message.id 会连续出现在 3 行里 usage 完全相同(疑似客户端多次 checkpoint 落盘); subagents/agent-*.jsonl 的 sessionId 字段恒等于父会话 id, 不能靠记录内容区分子代理自己的身份, 只能靠文件名" tags=token-ledger,claude,usage,dedup,subagent
"""claude 会话用量收集器。

目录形状(侦察已核实):
    ~/.claude/projects/<路径编码>/<session_id>.jsonl              —— 顶层会话(唯一权威)
    ~/.claude/projects/<路径编码>/<session_id>/subagents/agent-*.jsonl —— 子agent产物,
        与同名顶层会话文件是"文件+同名目录"并存关系(目录下还有 tool-results/ 等其他内容)。
        子agent自己的记录里 sessionId 字段恒等于父会话 id(区分不出子agent自己的身份),
        只能靠文件名 agent-<id> 当子agent自己的会话 id, 单独记账、不得并入父会话
        (2026-09-25 硬化), 也不得像旧逻辑一样被顶层过滤连带整个丢弃。
    ~/.claude/projects/<路径编码>/workflows/*.jsonl                —— workflow 产物, 仍然排除,
        不属于本次任务范围。

usage 记账语义: assistant 消息的 message.usage 字段是"一次 API 调用"的用量,
同一条消息可能有多个 content block, 但 usage 只在消息级出现一次 —— 不可逐 block 累加,
一条 assistant 消息 = calls 里的一次。

去重语义(2026-09-25 硬化): 同一个 message.id 在真实转录里会连续写多行 assistant(usage
字段完全相同, 疑似客户端多次 checkpoint 落盘), 按行计数会把同一次调用重复计费——按
message.id 去重, 没有 id 的行退到 requestId, 两者都没有就按行(等价不去重, 保留原样)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._jsonl_util import parse_jsonl_lines, read_new_lines

_EXCLUDED_DIR_NAMES = {"subagents", "workflows"}
_SUBAGENTS_DIR_NAME = "subagents"

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
    """一条 claude 会话的用量聚合(按 by_model 分列)。"""

    session_id: str
    source_path: str
    cwd: str = ""
    by_model: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: int = 0
    started: str | None = None
    ended: str | None = None
    provider: str = "claude"
    # 子代理归属(2026-09-25 新增, 默认 None 不破坏现有调用方): 仅当这条会话来自
    # <session>/subagents/agent-*.jsonl 时非空, 值 = 其上层"会话"目录名(父会话 id)。
    parent_session_id: str | None = None


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


def _iter_subagent_files(root: Path) -> list[tuple[Path, str]]:
    """列出 root 下每个项目每个"会话目录"里的 subagents/agent-*.jsonl(子agent产物),
    与其 parent_session_id(其上层"会话"目录名)配对。

    目录形状(侦察已核实): <project_dir>/<session_id>/subagents/agent-*.jsonl —— 与顶层
    会话文件 <project_dir>/<session_id>.jsonl 同名但是一个目录(并存, 不冲突), 目录下还有
    tool-results/ 等其他子目录(与本次任务无关, 不扫描)。
    """
    out: list[tuple[Path, str]] = []
    if not root.is_dir():
        return out
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for session_dir in sorted(p for p in project_dir.iterdir() if p.is_dir()):
            if session_dir.name in _EXCLUDED_DIR_NAMES:
                # "subagents"/"workflows" 这两个保留名不会是真实会话目录名, 跳过避免
                # 把它们自己当成候选"会话目录"去看有没有 subagents/ 子目录。
                continue
            subagents_dir = session_dir / _SUBAGENTS_DIR_NAME
            if not subagents_dir.is_dir():
                continue
            for jsonl_path in sorted(subagents_dir.glob("agent-*.jsonl")):
                out.append((jsonl_path, session_dir.name))
    return out


def _apply_usage(bucket: dict[str, Any], usage: dict[str, Any]) -> None:
    bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
    bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
    bucket["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
    bucket["cache_creation_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)


def _record_dedup_key(rec: dict[str, Any], fallback_index: int) -> str:
    """assistant 记录去重 key: 优先 message.id, 没有则 requestId, 都没有则按行(等价不去重)。

    实测同一个 message.id 会连续写多行 assistant(每行 usage 完全相同, 疑似客户端多次
    checkpoint 落盘), 按行计数会把同一次调用记多次(1940 行只对应 779 条消息)。
    """
    message = rec.get("message")
    if isinstance(message, dict):
        mid = message.get("id")
        if mid:
            return f"id:{mid}"
    req = rec.get("requestId")
    if req:
        return f"req:{req}"
    return f"line:{fallback_index}"


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
    seen: set[str] = set()

    for index, rec in enumerate(records):
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

        dedup_key = _record_dedup_key(rec, index)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

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
    """扫 root(默认应传 ~/.claude/projects)下顶层会话 jsonl + 子agent会话 jsonl,
    按字节水位线增量聚合用量。

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

    # 子agent产物(2026-09-25 新增): 独立记账, 会话 id 取文件名(不信任记录内容里恒等于
    # 父会话 id 的 sessionId 字段), parent_session_id = 其上层"会话"目录名。
    for jsonl_path, parent_session_id in _iter_subagent_files(root):
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
        session.session_id = jsonl_path.stem
        session.parent_session_id = parent_session_id
        session.source_path = str(jsonl_path)
        result.sessions.append(session)

    return result


__all__ = ["ClaudeSessionUsage", "ClaudeCollectResult", "collect_claude_sessions"]
