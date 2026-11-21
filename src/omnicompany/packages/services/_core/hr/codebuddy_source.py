# [OMNI] origin=claude-code ts=2026-08-17T00:00:00+08:00 type=module
# [OMNI] summary="LLM 人事系统 codebuddy 收集器 —— 解析 ~/.codebuddy/projects/**/*.jsonl 产出归一化 HrEvent"
# [OMNI] why="P1 五棒并行开发 B 棒；codebuddy 与 claude/qoder 同构但 model 二次取值、结果跨行关联"
# [OMNI] tags=hr,codebuddy,attribution,provenance
"""codebuddy 会话收集器。

权威依据: docs/plans/agent-framework/[2026-08-17]LLM-HR-PERFORMANCE-ATTRIBUTION/
          P1-implementation-contract.md、P0-coverage-report.md §V3。

真实语料实测(本机 `~/.codebuddy`)核实的字段结构:
    每行 jsonl 顶层: `sessionId` / `timestamp`(epoch 毫秒 int) / `cwd` / `type` / `id` / `parentId`。
    `type="function_call"`:    `name`(Read/Write/Edit/Bash/PowerShell/...)、`callId`、
                               `arguments`(JSON 字符串，需二次 `json.loads`)。
    `type="function_call_result"`: 与对应 `function_call` 共享同一个 `callId`（不是 `parentId`），
                               `status`("completed"/...)、`output.text`、
                               `providerData.toolResult`(`error`/`rawResponse.exitCode`/
                               `rawResponse.sandboxDenied`/`rawResponse.is_error`)。
    `model` 只出现在 `providerData.model`，且只挂在 `reasoning`/`function_call`/
        `function_call_result` 三种记录上，纯 `message` 记录没有 —— 会话内向前继承最近一次
        已知 model，标 `model_confidence="high"`（属于"直读到的"字段，只是转发到同会话内
        没有自带 model 的记录上）；整个会话从未出现过 model 时填 "" + "unknown"。

只提取写类(Write/Edit/MultiEdit/NotebookEdit)与命令类(Bash/PowerShell/Shell/Exec)工具调用，
按 `callId` 把 `function_call` 与其 `function_call_result` 关联后产出一条事件；
纯读类调用(Read/Glob/Grep/...)不落 HrEvent。

扫描范围写死到 `<root>/projects/**/*.jsonl`，不得对 `<root>` 全目录 rglob
（会扫进插件市场、TTF、PDF —— P0 §V3 已实证）。

只许流式逐行读，禁止 `.read()`/`json.load()` 整份文件——底层水位线增量读取复用
`hr/_jsonl_util.py`(A 棒交付, 本模块只 import 不改)。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._jsonl_util import FileWatermark, JsonlTail
from .schema import CollectResult, HrEvent, validate_event

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
COMMAND_TOOLS = {"Bash", "PowerShell", "Shell", "Exec"}
_TRACKED_TOOLS = WRITE_TOOLS | COMMAND_TOOLS

_MAX_FAILURE_SAMPLES = 20

_HOOK_ERROR_RE = re.compile(r"hook error:\s*\[([^\]]+)\]", re.IGNORECASE)


# ── 字段抽取辅助 ─────────────────────────────────────────────────────────


def _ts_to_iso(value: Any) -> str:
    """epoch 毫秒(int/float) -> ISO8601 UTC 毫秒精度字符串；取不到返回 ""。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    try:
        dt = datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _extract_model(obj: dict[str, Any]) -> str:
    provider_data = obj.get("providerData")
    if isinstance(provider_data, dict):
        model = provider_data.get("model")
        if isinstance(model, str) and model:
            return model
    return ""


def _model_confidence(model: str) -> str:
    return "high" if model else "unknown"


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalize_target(raw: str, cwd: str) -> str:
    if not raw:
        return ""
    p = Path(raw)
    if not p.is_absolute() and cwd:
        p = Path(cwd) / raw
    return str(p)


def _detect_denial_kind(text: str) -> str:
    """从结果文本里启发式识别守卫/权限拒绝(参照 claude 的 lock_pretooluse 文本样式)。

    codebuddy 真实语料抽样未命中过一例，属防御性实现——真信号仍以
    `rawResponse.sandboxDenied` 结构化字段为准，这里只是补充兜底。
    """
    if not text:
        return ""
    m = _HOOK_ERROR_RE.search(text)
    if m:
        return m.group(1)
    if "OmniChat blocked" in text or "resolves outside the planned write scope" in text:
        return "permission-rule"
    return ""


def _resolve_verdict(result_obj: dict[str, Any]) -> tuple[str, bool, str]:
    """返回 (verdict, is_denial, denial_kind)。

    信号优先级(均为 P0 抽样实证的真实字段路径):
      1. `providerData.toolResult.rawResponse.sandboxDenied` / 文本启发式 -> denied
      2. `status in (failed, error)` -> error
      3. `providerData.toolResult.error` 非空 / `rawResponse.is_error` -> error
      4. `output.text` 以 `<tool_use_error>` 开头 -> error
      5. `rawResponse.exitCode` 非零(Bash/PowerShell) -> error
      6. `status == "completed"` -> applied
      7. 都取不到 -> unknown
    """
    provider_data = result_obj.get("providerData")
    provider_data = provider_data if isinstance(provider_data, dict) else {}
    tool_result = provider_data.get("toolResult")
    tool_result = tool_result if isinstance(tool_result, dict) else {}
    raw_response = tool_result.get("rawResponse")
    raw_response = raw_response if isinstance(raw_response, dict) else {}
    output = result_obj.get("output")
    output_text = output.get("text") if isinstance(output, dict) else ""
    output_text = output_text if isinstance(output_text, str) else ""
    status = result_obj.get("status", "")

    error_text = tool_result.get("error")
    error_text = error_text if isinstance(error_text, str) else ""
    is_error_flag = bool(raw_response.get("is_error"))
    sandbox_denied = bool(raw_response.get("sandboxDenied"))
    exit_code = raw_response.get("exitCode")

    combined = " ".join(t for t in (error_text, output_text) if t)
    denial_kind = _detect_denial_kind(combined)

    if sandbox_denied or denial_kind:
        return "denied", True, (denial_kind or "sandbox_denied")
    if status in ("failed", "error"):
        return "error", False, ""
    if error_text or is_error_flag:
        return "error", False, ""
    if output_text.startswith("<tool_use_error>"):
        return "error", False, ""
    if isinstance(exit_code, int) and exit_code != 0:
        return "error", False, ""
    if status == "completed":
        return "applied", False, ""
    return "unknown", False, ""


@dataclass
class _PendingCall:
    lineno: int
    ts: str
    session_id: str
    cwd: str
    tool: str
    base_kind: str  # "write" | "command"
    target: str
    target_confidence: str
    model: str
    model_confidence: str
    operation_id: str = ""
    call_id: str = ""
    msg_id: str = ""


def _finalize_event(
    pc: _PendingCall,
    *,
    evidence_path: str,
    verdict: str,
    kind_override: str | None,
    denial_kind: str,
    note: str,
) -> HrEvent:
    return HrEvent(
        ts=pc.ts,
        provider="codebuddy",
        session_id=pc.session_id,
        kind=kind_override or pc.base_kind,
        evidence_ref=f"{evidence_path}#L{pc.lineno}",
        operation_id=pc.operation_id,
        model=pc.model,
        model_confidence=pc.model_confidence,
        cwd=pc.cwd,
        tool=pc.tool,
        target=pc.target,
        target_confidence=pc.target_confidence,
        verdict=verdict,
        denial_kind=denial_kind,
        note=note,
    )


def collect_codebuddy_sessions(
    root: Path, *, watermark_state: dict[str, Any] | None = None
) -> CollectResult:
    """扫描 `<root>/projects/**/*.jsonl`，产出归一化 codebuddy HrEvent。

    Args:
        root: codebuddy 家目录(生产环境是 `~/.codebuddy`；测试用 `tmp_path` 注入)。
        watermark_state: 上次运行留下的水位线，形如 `{"<abs_path>": FileWatermark.to_dict()}`
            (与 `hr/_jsonl_util.py` 的 `FileWatermark`/`JsonlTail` 约定一致)。
            首次运行传 None 即为全量首解析。
    """
    root = Path(root)
    watermark_state = watermark_state or {}
    new_watermark: dict[str, Any] = dict(watermark_state)
    result = CollectResult(provider="codebuddy", watermark=new_watermark)

    files = sorted(root.glob("projects/**/*.jsonl"))
    for fp in files:
        key = str(fp)
        result.files_seen += 1

        old_state = watermark_state.get(key)
        old_state = old_state if isinstance(old_state, dict) else {}
        wm = FileWatermark.from_dict(old_state)
        tail = JsonlTail(fp, wm)

        session_last_model = ""
        pending_by_call_id: dict[str, _PendingCall] = {}
        pending_by_msg_id: dict[str, _PendingCall] = {}
        raw_pending = old_state.get("pending")
        if isinstance(raw_pending, list):
            for item in raw_pending:
                if not isinstance(item, dict):
                    continue
                try:
                    restored = _PendingCall(**item)
                except TypeError:
                    continue
                if restored.call_id:
                    pending_by_call_id[restored.call_id] = restored
                if restored.msg_id:
                    pending_by_msg_id[restored.msg_id] = restored
        newly_pending: set[str] = set()
        reset_handled = False

        for lineno, obj in tail:
            if tail.was_reset and not reset_handled:
                pending_by_call_id.clear()
                pending_by_msg_id.clear()
                newly_pending.clear()
                reset_handled = True
            rtype = obj.get("type", "")
            session_id = obj.get("sessionId", "")
            cwd = obj.get("cwd", "")

            if rtype in ("reasoning", "function_call", "function_call_result"):
                model = _extract_model(obj)
                if model:
                    session_last_model = model

            if rtype == "function_call":
                name = obj.get("name", "")
                if name not in _TRACKED_TOOLS:
                    continue
                call_id = obj.get("callId", "")
                msg_id = obj.get("id", "")
                base_kind = "write" if name in WRITE_TOOLS else "command"
                args = _parse_arguments(obj.get("arguments"))
                target_raw = args.get("file_path") or args.get("notebook_path") or args.get("path") or ""
                target = _normalize_target(target_raw, cwd) if base_kind == "write" else ""
                if base_kind == "write" and not target:
                    # 写类调用取不到目标路径：无法产出合法 write 事件(schema 要求
                    # target_confidence 必须是 direct/parsed)，如实计入失败样例而不是编造。
                    # "write-without-target:" 前缀与解析失败区分开：这是上游格式没给路径，
                    # 不是我们的 parser 挂了——两者混在一起会掩盖真实的格式漂移。
                    if len(result.failures) < _MAX_FAILURE_SAMPLES:
                        result.failures.append(
                            f"write-without-target: {key}#L{lineno} (callId={call_id})"
                        )
                    continue
                ts_iso = _ts_to_iso(obj.get("timestamp"))
                model = _extract_model(obj) or session_last_model
                pc = _PendingCall(
                    lineno=lineno,
                    ts=ts_iso,
                    session_id=session_id,
                    cwd=cwd,
                    tool=name,
                    base_kind=base_kind,
                    target=target,
                    target_confidence="direct" if base_kind == "write" else "none",
                    model=model,
                    model_confidence=_model_confidence(model),
                    operation_id=str(call_id or msg_id),
                    call_id=str(call_id or ""),
                    msg_id=str(msg_id or ""),
                )
                if call_id:
                    pending_by_call_id[call_id] = pc
                if msg_id:
                    pending_by_msg_id[msg_id] = pc
                newly_pending.add(pc.operation_id)

            elif rtype == "function_call_result":
                call_id = obj.get("callId", "")
                pc = pending_by_call_id.pop(call_id, None) if call_id else None
                if pc is None and not call_id:
                    parent_id = obj.get("parentId", "")
                    pc = pending_by_msg_id.pop(parent_id, None) if parent_id else None
                    if pc is not None:
                        for pending_call_id, cand in list(pending_by_call_id.items()):
                            if cand is pc:
                                pending_by_call_id.pop(pending_call_id, None)
                elif pc is not None:
                    # 同一个 pc 可能同时挂在 by_msg_id 索引下，一并摘除防止 EOF 重复 flush。
                    for msg_id, cand in list(pending_by_msg_id.items()):
                        if cand is pc:
                            pending_by_msg_id.pop(msg_id, None)
                if pc is None:
                    continue  # 结果对应的调用不在追踪范围内(如 Read/Glob)，忽略
                newly_pending.discard(pc.operation_id)
                verdict, is_denial, denial_kind = _resolve_verdict(obj)
                event = _finalize_event(
                    pc,
                    evidence_path=key,
                    verdict=verdict,
                    kind_override="denial" if is_denial else None,
                    denial_kind=denial_kind,
                    note=f"result@L{lineno}",
                )
                problems = validate_event(event)
                if problems:
                    if len(result.failures) < _MAX_FAILURE_SAMPLES:
                        result.failures.append(f"{key}#L{lineno}: invalid event {problems}")
                    continue
                result.events.append(event)

        # 文件读完仍未等到结果的调用：如实标 unknown，不静默丢弃。
        remaining = {pc.operation_id: pc for pc in (*pending_by_call_id.values(), *pending_by_msg_id.values())}
        for operation_id in newly_pending:
            pc = remaining.get(operation_id)
            if pc is None:
                continue
            event = _finalize_event(
                pc,
                evidence_path=key,
                verdict="unknown",
                kind_override=None,
                denial_kind="",
                note="no_result_by_eof",
            )
            problems = validate_event(event)
            if problems:
                if len(result.failures) < _MAX_FAILURE_SAMPLES:
                    result.failures.append(f"{key}#L{pc.lineno}: invalid event {problems}")
                continue
            result.events.append(event)

        if tail.was_reset and not reset_handled:
            remaining.clear()
        state = tail.watermark.to_dict()
        state["pending"] = [asdict(pc) for pc in remaining.values()]
        new_watermark[key] = state
        result.lines_scanned += tail.lines_read

        if tail.error is not None:
            result.files_failed += 1
            if len(result.failures) < _MAX_FAILURE_SAMPLES:
                result.failures.append(f"{key}: read failed: {tail.error}")
        elif tail.is_empty:
            result.files_empty += 1
        else:
            result.files_parsed += 1
            if tail.skipped and len(result.failures) < _MAX_FAILURE_SAMPLES:
                result.failures.append(f"{key}: {tail.skipped} bad json line(s) skipped")

    result.watermark = new_watermark
    return result


__all__ = ["collect_codebuddy_sessions", "WRITE_TOOLS", "COMMAND_TOOLS"]
