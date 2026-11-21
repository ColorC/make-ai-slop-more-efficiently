# [OMNI] origin=claude-code ts=2026-08-17T00:00:00+08:00 type=module summary="LLM 人事系统 claude + qoder 采集器: 两家 transcript 近乎同构, 共用一套解析逻辑抽出 write/command/denial 事件; qoder 另有一路结构完全不同的 logs/sessions 事件流做次级支持" why="P0 实测 claude/qoder 的 message.content[].tool_use + toolUseResult + toolDenialKind 字段路径完全同名同构, 契约要求共用一个内部解析函数、两个薄入口; 但实现中发现 qoder 还有 ~/.qoder/logs/sessions/**/*.jsonl 这一路完全不同 schema 的运行时事件日志, 也计入契约里'qoder 真会话 38MB'的口径, 已按次级/低置信度支持一并接入(见交付说明)" tags=hr,claude,qoder,collector
"""claude / qoder 会话采集器 —— 归一化为 `HrEvent`(write/command/denial)。

真源目录形状(P0 报告 + 本次实现前实测核实, claude 与 qoder 完全一致):
    <root>/<workspace-编码>/<session-uuid>.jsonl              —— 顶层(主)会话
    <root>/<workspace-编码>/<session-uuid>/subagents/*.jsonl  —— 子agent会话
        (只有存在同名 `<session-uuid>.jsonl` 兄弟文件的目录才当会话目录看待,
         用于排除 `memory/`、`goals/` 这类非会话辅助目录 —— 均为实测确认存在)

    两家的 `root` 参数统一约定为该 CLI 的**安装根目录**(`~/.claude`、`~/.qoder`),
    子路径由 collector 内部写死拼出, 调用方不需要记住每家不同的会话子路径
    (主会话裁决: 五家 collector 一律如此, 与 token_ledger 只有两家时的旧惯例不同):
        claude: `root/projects/**`
        qoder:  `root/projects/**` + `root/logs/sessions/**`
            —— qoder 除了与 claude 同构的 `projects/**/*.jsonl`, 契约口径下的
            "真会话"还包含 `logs/sessions/**/*.jsonl` 这一路完全不同 schema 的
            运行时事件日志(`{ts,seq,type,turn_id,data:{...}}`, 而不是 claude 式的
            `message.content[].tool_use`)。此为实现中新发现、P0 报告未展开描述的
            一层, 已向主会话报告并按其裁决(provider 仍是 "qoder", 只是
            model_confidence 统一降为 "low")接入。

字段填写纪律(与 schema.py 一致, 不重复展开): 取不到的一律 ""/"unknown",
不落文件正文/命令全文, `evidence_ref` 必填 "<abs_path>#L<lineno>"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._jsonl_util import FileWatermark, JsonlTail
from .schema import CollectResult, HrEvent, validate_event

_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_COMMAND_TOOLS = frozenset({"Bash", "PowerShell"})

_MAX_FAILURE_SAMPLES = 20

# 从守卫拒绝文本里启发式解析出守卫名, 如
#   '...hooks.lock_pretooluse]: OmniChat blocked Write: ...' -> "lock_pretooluse"
# 解析不出时一律回填 "", 不得编造(schema.py 字段填写纪律)。
_GUARD_BRACKET_RE = re.compile(r"\[([^\[\]]+)\]\s*:")


def _extract_guard(*texts: Any) -> str:
    """从若干候选文本里解析守卫名。解析不出返回 ""。"""
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        m = _GUARD_BRACKET_RE.search(text)
        if not m:
            continue
        tail = m.group(1).rsplit(".", 1)[-1].strip().strip("\"'")
        if tail.isidentifier():
            return tail
    return ""


@dataclass(frozen=True)
class _SessionFile:
    path: Path
    session_id: str
    is_subagent: bool
    parent_session_id: str


def _discover_session_files(projects_root: Path) -> list[_SessionFile]:
    """列出 `<projects_root>/<workspace>/` 下的顶层会话 + 子会话 jsonl 文件。

    判断"这个子目录是不是会话目录"的依据是"是否存在同名 `<uuid>.jsonl` 兄弟文件"
    (实测 claude 下的 `memory/`、qoder 下的 `goals/` 都没有对应的 `.jsonl` 兄弟文件,
    用这条规则天然排除, 不需要为每家单独列黑名单)。
    """
    out: list[_SessionFile] = []
    if not projects_root.is_dir():
        return out
    for workspace_dir in sorted(p for p in projects_root.iterdir() if p.is_dir()):
        for jsonl_path in sorted(workspace_dir.glob("*.jsonl")):
            out.append(_SessionFile(jsonl_path, jsonl_path.stem, False, ""))
        for sess_dir in sorted(p for p in workspace_dir.iterdir() if p.is_dir()):
            sibling = workspace_dir / f"{sess_dir.name}.jsonl"
            if not sibling.exists():
                continue
            subagents_dir = sess_dir / "subagents"
            if not subagents_dir.is_dir():
                continue
            for jsonl_path in sorted(subagents_dir.glob("*.jsonl")):
                out.append(_SessionFile(jsonl_path, jsonl_path.stem, True, sess_dir.name))
    return out


def _record_failure(result: CollectResult, path: Path, exc: BaseException) -> None:
    result.files_failed += 1
    if len(result.failures) < _MAX_FAILURE_SAMPLES:
        result.failures.append(f"{path}: {type(exc).__name__}: {exc}")


def _finalize_pending(
    entry: dict[str, Any],
    *,
    provider: str,
    session_id: str,
    is_subagent: bool,
    parent_session_id: str,
    path: Path,
    verdict: str,
) -> HrEvent:
    return HrEvent(
        ts=entry["ts"],
        provider=provider,
        session_id=session_id,
        kind=entry["kind"],
        evidence_ref=f"{path}#L{entry['lineno']}",
        operation_id=entry.get("operation_id", ""),
        model=entry["model"],
        model_confidence="high" if entry["model"] else "unknown",
        parent_session_id=parent_session_id,
        is_subagent=is_subagent,
        cwd=entry["cwd"],
        git_branch=entry["git_branch"],
        tool=entry["name"],
        target=entry["target"],
        target_confidence=entry["target_confidence"],
        verdict=verdict,
    )


def _collect_from_transcripts(
    files: list[_SessionFile],
    *,
    provider: str,
    watermark_state: dict[str, Any],
    result: CollectResult,
    new_watermark: dict[str, Any],
) -> set[str]:
    """claude / qoder 共用的核心解析: 逐行流式扫 `message.content[].tool_use` 结构。

    单文件内用一个很小的 `pending` dict(键是 tool_use_id, 值只存名字/路径/时间戳等
    短字符串, 不存内容)把"看到调用"和"看到结果"关联起来 —— 大小恒为"当前未结束的
    工具调用数", 不随文件体积增长, 不违反"绝不整份读进内存"的约束。
    """
    productive_main_sessions: set[str] = set()
    for sf in files:
        result.files_seen += 1
        old_state = watermark_state.get(str(sf.path))
        old_state = old_state if isinstance(old_state, dict) else {}
        wm = FileWatermark.from_dict(old_state)
        tail = JsonlTail(sf.path, wm)

        raw_pending = old_state.get("pending")
        pending = {
            str(key): dict(value)
            for key, value in (raw_pending.items() if isinstance(raw_pending, dict) else [])
            if isinstance(value, dict)
        }
        newly_pending: set[str] = set()
        reset_handled = False
        events_before = len(result.events)
        cwd = ""
        git_branch = ""

        try:
            for lineno, rec in tail:
                if tail.was_reset and not reset_handled:
                    pending.clear()
                    newly_pending.clear()
                    reset_handled = True
                if rec.get("cwd"):
                    cwd = rec["cwd"]
                if rec.get("gitBranch"):
                    git_branch = rec["gitBranch"]

                msg = rec.get("message")
                model = ""
                if isinstance(msg, dict) and isinstance(msg.get("model"), str):
                    model = msg["model"]

                content = msg.get("content") if isinstance(msg, dict) else None
                denial_tool_use_id: str | None = None
                denial_text_candidates: list[Any] = []
                result_tool_use_ids: list[tuple[str, bool]] = []  # (id, is_error)

                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "tool_use":
                            name = block.get("name") or ""
                            tid = block.get("id")
                            if not tid:
                                continue
                            inp = block.get("input") or {}
                            kind = None
                            target = ""
                            target_confidence = "none"
                            if name in _WRITE_TOOLS:
                                kind = "write"
                                target = str(inp.get("file_path") or inp.get("notebook_path") or "")
                                target_confidence = "direct" if target else "none"
                            elif name in _COMMAND_TOOLS:
                                kind = "command"
                            pending[tid] = {
                                "operation_id": tid,
                                "name": name,
                                "kind": kind,
                                "target": target,
                                "target_confidence": target_confidence,
                                "ts": rec.get("timestamp") or "",
                                "lineno": lineno,
                                "model": model,
                                "cwd": cwd,
                                "git_branch": git_branch,
                            }
                            newly_pending.add(tid)
                        elif btype == "tool_result":
                            tid = block.get("tool_use_id")
                            if not tid:
                                continue
                            if rec.get("toolDenialKind"):
                                denial_tool_use_id = tid
                                denial_text_candidates.append(block.get("content"))
                            else:
                                result_tool_use_ids.append((tid, bool(block.get("is_error"))))

                denial_kind = rec.get("toolDenialKind") or ""
                if denial_kind and denial_tool_use_id:
                    entry = pending.pop(denial_tool_use_id, None)
                    newly_pending.discard(denial_tool_use_id)
                    tool_use_result = rec.get("toolUseResult")
                    if isinstance(tool_use_result, str):
                        denial_text_candidates.append(tool_use_result)
                    guard = _extract_guard(*denial_text_candidates)
                    if entry is not None:
                        event = HrEvent(
                            ts=entry["ts"],
                            provider=provider,
                            session_id=sf.session_id,
                            kind="denial",
                            evidence_ref=f"{sf.path}#L{entry['lineno']}",
                            operation_id=entry.get("operation_id", denial_tool_use_id),
                            model=entry["model"],
                            model_confidence="high" if entry["model"] else "unknown",
                            parent_session_id=sf.parent_session_id,
                            is_subagent=sf.is_subagent,
                            cwd=entry["cwd"],
                            git_branch=entry["git_branch"],
                            tool=entry["name"],
                            target=entry["target"],
                            target_confidence="none",
                            verdict="denied",
                            denial_kind=denial_kind,
                            guard=guard,
                        )
                    else:
                        # 找不到对应的 tool_use(比如水位线正好切在两者之间), 仍如实记一条
                        # 拒绝事件, 只是 tool/target 取不到。
                        event = HrEvent(
                            ts=rec.get("timestamp") or "",
                            provider=provider,
                            session_id=sf.session_id,
                            kind="denial",
                            evidence_ref=f"{sf.path}#L{lineno}",
                            parent_session_id=sf.parent_session_id,
                            is_subagent=sf.is_subagent,
                            cwd=cwd,
                            git_branch=git_branch,
                            verdict="denied",
                            denial_kind=denial_kind,
                            guard=guard,
                        )
                    problems = validate_event(event)
                    if not problems:
                        result.events.append(event)
                else:
                    for tid, is_error in result_tool_use_ids:
                        entry = pending.pop(tid, None)
                        newly_pending.discard(tid)
                        if entry is None or entry.get("kind") is None:
                            continue
                        event = _finalize_pending(
                            entry,
                            provider=provider,
                            session_id=sf.session_id,
                            is_subagent=sf.is_subagent,
                            parent_session_id=sf.parent_session_id,
                            path=sf.path,
                            verdict="error" if is_error else "applied",
                        )
                        problems = validate_event(event)
                        if not problems:
                            result.events.append(event)

            # 本批次读到 EOF 时仍未收到结果的调用(比如被 backgrounded 或截断),
            # 如实记为 verdict=unknown, 不编造。
            for tid in newly_pending:
                entry = pending.get(tid)
                if entry is None:
                    continue
                if entry.get("kind") is None:
                    continue
                event = _finalize_pending(
                    entry,
                    provider=provider,
                    session_id=sf.session_id,
                    is_subagent=sf.is_subagent,
                    parent_session_id=sf.parent_session_id,
                    path=sf.path,
                    verdict="unknown",
                )
                problems = validate_event(event)
                if not problems:
                    result.events.append(event)

        except OSError as exc:
            _record_failure(result, sf.path, exc)
            continue

        if tail.was_reset and not reset_handled:
            pending.clear()
        produced_events = bool(old_state.get("produced_events")) or len(result.events) > events_before
        state = tail.watermark.to_dict()
        state["pending"] = {key: value for key, value in pending.items() if value.get("kind") is not None}
        state["produced_events"] = produced_events
        new_watermark[str(sf.path)] = state
        result.lines_scanned += tail.lines_read
        if produced_events and not sf.is_subagent:
            productive_main_sessions.add(sf.session_id)

        if tail.error is not None:
            _record_failure(result, sf.path, tail.error)
        elif tail.is_empty:
            result.files_empty += 1
        else:
            result.files_parsed += 1

    return productive_main_sessions


def _collect_claude_or_qoder(
    projects_root: Path,
    *,
    provider: str,
    watermark_state: dict[str, Any] | None,
) -> CollectResult:
    watermark_state = watermark_state or {}
    new_watermark: dict[str, Any] = dict(watermark_state)
    result = CollectResult(provider=provider, watermark=new_watermark)
    files = _discover_session_files(projects_root)
    _collect_from_transcripts(
        files,
        provider=provider,
        watermark_state=watermark_state,
        result=result,
        new_watermark=new_watermark,
    )
    return result


def collect_claude_sessions(
    root: Path,
    *,
    watermark_state: dict[str, Any] | None = None,
) -> CollectResult:
    """采集 claude 会话。

    `root` 传 claude 安装根目录本体(如 `~/.claude`), **不是** `projects` 子目录
    —— 与 `collect_qoder_sessions` 的 `root` 语义保持一致(主会话裁决: 五家
    collector 一律接收 CLI 安装根, 会话子路径由 collector 内部写死拼出)。
    """
    return _collect_claude_or_qoder(root / "projects", provider="claude", watermark_state=watermark_state)


# ── qoder: projects/**/*.jsonl 与 claude 同构的部分 ─────────────────────────


def collect_qoder_sessions(
    root: Path,
    *,
    watermark_state: dict[str, Any] | None = None,
) -> CollectResult:
    """采集 qoder 会话。

    `root` 传 qoder 安装根目录本体(如 `~/.qoder`), **不是** `projects` 子目录
    —— 因为除了与 claude 同构的 `projects/**/*.jsonl`, qoder 契约口径下的
    "真会话"还包含 `logs/sessions/**/*.jsonl` 这一路结构完全不同的运行时事件日志
    (`tool.requested` / `tool.execution.finished` / `permission.resolved` 等)。

    去重规则(实测发现: 对本机真实语料, `projects/` 下每一个主会话 100% 都有一份
    对应的 `logs/sessions/<同一 session-uuid>/segments/*.jsonl` —— 同一批动作被
    两种格式各记了一遍, 不去重会让 write/command/denial 计数系统性翻倍):
        * 若某 `session_id` 在 `projects/` 下已有主会话transcript, 只信
          `projects/` 抽出的事件(字段直读, `model_confidence` 可为 "high");
          对应的 `logs/sessions/<session_id>/segments/*.jsonl` 整批跳过,
          不计入 files_seen(不是"覆盖不到", 是"本来就不该数它")。
        * 只有在 `projects/` 下**找不到**同名主会话的 `session_id`(实测约有
          这样的会话——大概率是早退/未正常收尾、没能落成一份 transcript 的会话),
          才用 `logs/sessions/` 的低置信度数据补上, 让这批会话不至于完全没有
          归因数据。
    """
    watermark_state = watermark_state or {}
    new_watermark: dict[str, Any] = dict(watermark_state)
    result = CollectResult(provider="qoder", watermark=new_watermark)

    files = _discover_session_files(root / "projects")
    productive_main_sessions = _collect_from_transcripts(
        files,
        provider="qoder",
        watermark_state=watermark_state,
        result=result,
        new_watermark=new_watermark,
    )

    _collect_qoder_segments(
        root / "logs" / "sessions",
        watermark_state=watermark_state,
        result=result,
        new_watermark=new_watermark,
        skip_session_ids=productive_main_sessions,
    )

    return result


# ── qoder: logs/sessions/**/*.jsonl —— 与 claude 完全不同的事件流 schema ────
#
# 目录形状(实测): <root>/<workspace-编码>/<session-uuid>/segments/<ts>-<pid>.jsonl
# 记录形状: {"ts","seq","level","type","turn_id"?,"data":{...}}
# 与 claude 式 message.content[].tool_use 结构无关, 不能复用上面的共享解析函数。
#
# 去重(实测: 本机语料里 projects/ 下的主会话 100% 都能在 logs/sessions/ 下找到
# 同一 session-uuid 的 segments/ 目录, 是同一批动作的两份录像): `collect_qoder_sessions`
# 只把 `session_id` 不在 `projects/` 已知主会话集合里的 segments/ 文件传进本函数
# (见 `skip_session_ids` 调用点), 已经在 projects/ 出现过的会话不会再被这里重复计入。
#
# 置信度显著低于 projects/ 路径的主解析:
#   * model 是"最近一次在本文件里见到的 turn.started/session.config.loaded 里的
#     model"这种跨记录滚动状态, 不是与工具调用同一条记录里直读, 因此固定标
#     model_confidence="low"(即便取到了非空值)。
#   * is_subagent 同理, 用同一套跨记录滚动状态从 `turn.started`/
#     `input.prompt.submitted` 的 `data.is_subagent` 字段读取, 不保证覆盖所有
#     交错场景(fork 出的子 agent 是否严格嵌套未逐一验证)。
#   * 找不到对应会话根 session_id 就用目录结构里的 `<session-uuid>` 段。


def _discover_segment_files(segments_root: Path) -> list[tuple[Path, str]]:
    """列出 `<segments_root>/<workspace>/<session-uuid>/segments/*.jsonl`。

    Returns: (jsonl_path, session_id) 列表, session_id 取自 `<session-uuid>` 段
    (即 `segments/` 目录的上一级目录名)。
    """
    out: list[tuple[Path, str]] = []
    if not segments_root.is_dir():
        return out
    for workspace_dir in sorted(p for p in segments_root.iterdir() if p.is_dir()):
        for sess_dir in sorted(p for p in workspace_dir.iterdir() if p.is_dir()):
            seg_dir = sess_dir / "segments"
            if not seg_dir.is_dir():
                continue
            for jsonl_path in sorted(seg_dir.glob("*.jsonl")):
                out.append((jsonl_path, sess_dir.name))
    return out


def _collect_qoder_segments(
    segments_root: Path,
    *,
    watermark_state: dict[str, Any],
    result: CollectResult,
    new_watermark: dict[str, Any],
    skip_session_ids: frozenset[str] | set[str] = frozenset(),
) -> None:
    for path, session_id in _discover_segment_files(segments_root):
        if session_id in skip_session_ids:
            # 去重: 这个 session 在 projects/ 下已经有一份更可靠(字段直读)的
            # transcript 了, 不重复计入 files_seen —— 这不是"没扫到", 是
            # "本来就不该数它", 避免同一批 write/command/denial 被算两遍。
            continue
        result.files_seen += 1
        old_state = watermark_state.get(str(path))
        old_state = old_state if isinstance(old_state, dict) else {}
        wm = FileWatermark.from_dict(old_state)
        tail = JsonlTail(path, wm)

        raw_pending = old_state.get("pending")
        pending = {
            str(key): dict(value)
            for key, value in (raw_pending.items() if isinstance(raw_pending, dict) else [])
            if isinstance(value, dict)
        }
        newly_pending: set[str] = set()
        reset_handled = False
        current_model = ""
        current_is_subagent = False

        try:
            for lineno, rec in tail:
                if tail.was_reset and not reset_handled:
                    pending.clear()
                    newly_pending.clear()
                    reset_handled = True
                data = rec.get("data")
                data = data if isinstance(data, dict) else {}
                rtype = rec.get("type")

                if rtype in ("turn.started", "session.config.loaded", "input.prompt.submitted"):
                    if isinstance(data.get("model"), str) and data["model"]:
                        current_model = data["model"]
                    if isinstance(data.get("is_subagent"), bool):
                        current_is_subagent = data["is_subagent"]

                elif rtype == "tool.requested":
                    tid = rec.get("tool_call_id")
                    if not tid:
                        continue
                    name = data.get("tool_name") or ""
                    args = data.get("args") or {}
                    kind = None
                    target = ""
                    target_confidence = "none"
                    if name in _WRITE_TOOLS:
                        kind = "write"
                        target = str(args.get("file_path") or args.get("notebook_path") or "")
                        target_confidence = "direct" if target else "none"
                    elif name in _COMMAND_TOOLS:
                        kind = "command"
                    pending[tid] = {
                        "operation_id": tid,
                        "name": name,
                        "kind": kind,
                        "target": target,
                        "target_confidence": target_confidence,
                        "ts": rec.get("ts") or "",
                        "lineno": lineno,
                        "model": current_model,
                        "is_subagent": current_is_subagent,
                    }
                    newly_pending.add(tid)

                elif rtype == "permission.resolved":
                    if data.get("allowed") is False:
                        tid = rec.get("tool_call_id")
                        entry = pending.pop(tid, None) if tid else None
                        newly_pending.discard(tid)
                        name = (entry or {}).get("name") or data.get("tool_name") or ""
                        target = (entry or {}).get("target") or ""
                        ts_val = (entry or {}).get("ts") or rec.get("ts") or ""
                        decision_reason = data.get("decision_reason")
                        guard_text = (
                            decision_reason.get("reason")
                            if isinstance(decision_reason, dict)
                            else decision_reason
                        )
                        event = HrEvent(
                            ts=ts_val,
                            provider="qoder",
                            session_id=session_id,
                            kind="denial",
                            evidence_ref=f"{path}#L{lineno}",
                            operation_id=str(tid or ""),
                            model=current_model,
                            model_confidence="low" if current_model else "unknown",
                            is_subagent=current_is_subagent,
                            tool=name,
                            target=target,
                            target_confidence="none",
                            verdict="denied",
                            denial_kind=str(data.get("decision_behavior") or "denied"),
                            guard=_extract_guard(guard_text),
                        )
                        problems = validate_event(event)
                        if not problems:
                            result.events.append(event)

                elif rtype == "tool.execution.finished":
                    tid = rec.get("tool_call_id")
                    entry = pending.pop(tid, None) if tid else None
                    newly_pending.discard(tid)
                    if entry is None or entry.get("kind") is None:
                        continue
                    is_error = bool(data.get("is_error")) or data.get("status") == "error"
                    event = HrEvent(
                        ts=entry["ts"],
                        provider="qoder",
                        session_id=session_id,
                        kind=entry["kind"],
                        evidence_ref=f"{path}#L{entry['lineno']}",
                        operation_id=entry.get("operation_id", str(tid or "")),
                        model=entry["model"],
                        model_confidence="low" if entry["model"] else "unknown",
                        is_subagent=entry["is_subagent"],
                        tool=entry["name"],
                        target=entry["target"],
                        target_confidence=entry["target_confidence"],
                        verdict="error" if is_error else "applied",
                    )
                    problems = validate_event(event)
                    if not problems:
                        result.events.append(event)

            for operation_id in newly_pending:
                entry = pending.get(operation_id)
                if entry is None:
                    continue
                if entry.get("kind") is None:
                    continue
                event = HrEvent(
                    ts=entry["ts"],
                    provider="qoder",
                    session_id=session_id,
                    kind=entry["kind"],
                    evidence_ref=f"{path}#L{entry['lineno']}",
                    operation_id=entry.get("operation_id", ""),
                    model=entry["model"],
                    model_confidence="low" if entry["model"] else "unknown",
                    is_subagent=entry["is_subagent"],
                    tool=entry["name"],
                    target=entry["target"],
                    target_confidence=entry["target_confidence"],
                    verdict="unknown",
                )
                problems = validate_event(event)
                if not problems:
                    result.events.append(event)

        except OSError as exc:
            _record_failure(result, path, exc)
            continue

        if tail.was_reset and not reset_handled:
            pending.clear()
        state = tail.watermark.to_dict()
        state["pending"] = {key: value for key, value in pending.items() if value.get("kind") is not None}
        new_watermark[str(path)] = state
        result.lines_scanned += tail.lines_read

        if tail.error is not None:
            _record_failure(result, path, tail.error)
        elif tail.is_empty:
            result.files_empty += 1
        else:
            result.files_parsed += 1


__all__ = ["collect_claude_sessions", "collect_qoder_sessions"]
