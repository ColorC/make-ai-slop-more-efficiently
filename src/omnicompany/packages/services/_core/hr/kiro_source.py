# [OMNI] origin=claude-code ts=2026-08-17T00:00:00+08:00 type=module
# [OMNI] summary="LLM 人事系统 kiro 收集器 —— 解析 ~/.kiro/sessions/cli/*.jsonl + 配套 .json 产出归一化 HrEvent"
# [OMNI] why="P1 五棒并行开发 B 棒；kiro 最大单文件实测 620MB，且 model/timestamp 大量降级/缺失"
# [OMNI] tags=hr,kiro,attribution,provenance
"""kiro 会话收集器。

权威依据: docs/plans/agent-framework/[2026-08-17]LLM-HR-PERFORMANCE-ATTRIBUTION/
          P1-implementation-contract.md、P0-coverage-report.md §V3。

真实语料实测(本机 `~/.kiro/sessions/cli`)核实的字段结构，且**修正了 P0 报告未展开的细节**
(以下均用本机真实文件逐行 json.loads 核对过，非猜测):

  * `<uuid>.jsonl` 每行顶层: `{"version":"v1","kind":..., "data": {...}}`。
    - `kind="Prompt"`(用户输入): `data.meta.timestamp` 是 epoch **秒**。
    - `kind="AssistantMessage"`: `data.content[]` 里 `kind="toolUse"` 的项，
      `data={toolUseId,name,input}`；写工具 `name` 含 "write"(实测就是 `"write"`)，
      `input.path`+`input.command∈{create,insert}`；命令工具 `name=="shell"`，
      `input.command` 是完整 shell 文本(按契约"只提字段不复制内容"，本收集器不落此文本)。
      **实测这类记录没有 `data.meta`，即没有可用的逐条 timestamp。**
    - `kind="ToolResults"`: `data.content[]` 里 `kind="toolResult"` 的项，
      `data={toolUseId, content, status}`，`status` 实测取值 `"success"`/`"error"`，
      按 `toolUseId` 与对应 `AssistantMessage` 里的 toolUse 关联，同样没有 `data.meta`。
  * `session_id`/`cwd`/`created_at`/`parent_session_id`/`session_created_reason` 不在 jsonl，
    在配套同名 `<uuid>.json`（本机 66 个 `.jsonl` 对 66 个 `.json`，一一配套，体积 ~1-2KB，
    可整份读，不受"禁止整份 read()"约束——那条约束针对的是几十/几百 MB 的会话 jsonl 本体）。
  * **timestamp 降级(比 P0 报告描述更彻底)**：抽样多个会话，`AssistantMessage`/`ToolResults`
    记录完全不带任何 timestamp 字段；kiro 唯一可靠的时间锚点是配套 `.json` 的
    `created_at`(ISO8601，纳秒精度)。本收集器对同一会话内的全部事件统一使用这个
    **会话级** ts（在 `note` 字段标注 `ts=session_created_at`，不冒充逐条实测时间）；
    配套 `.json` 缺失时退化到 jsonl 文件的 mtime（同样标注来源，绝不留空/编造）。
  * **model 降级**：`session_state.rts_model_state.model_info` 抽样 15 个会话里 11 个为
    `null`；非空时形如 `{"model_id":"claude-opus-5", ...}`，取 `model_id`。
    取到就填并标 `model_confidence="low"`（契约明定：kiro 源整体不可靠，即使拿到也不能
    和 claude/codebuddy 同等对待）；取不到一律 `"" + "unknown"`，**严禁用
    `~/.kiro/settings/cli.json` 的当前默认模型回填历史会话**（本模块完全不读那个文件）。

只提取写类(`name` 含 "write")与命令类(`name=="shell"`)工具调用，按 `toolUseId` 关联对应
`ToolResults`；纯读类/检索类调用(`read`/`grep`/`web_search`/`web_fetch`/`introspect`/
`todo_list`/`summary`/...)不落 HrEvent。

扫描范围写死到 `<root>/sessions/cli/*.jsonl`（真实语料实测口径，P0 §V3）。

只许流式逐行读大 jsonl 本体，禁止 `.read()`/`json.load()` 整份文件——kiro 最大单文件实测
620MB，且真实语料里出现过单行 29MB 的记录(嵌了大段 WebFetch/搜索结果)，逐行流式读是
唯一能把峰值内存维持在"远小于文件体量"而非"随文件体量线性增长"的做法。底层水位线增量
读取复用 `hr/_jsonl_util.py`(A 棒交付, 本模块只 import 不改)。
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

_MAX_FAILURE_SAMPLES = 20
_MAX_SIDECAR_BYTES = 5_000_000  # 真实语料 ~1-2KB；防御性上限，超出则视为不可用

_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z?$")

# 保守启发式：kiro 自身权限系统(session_state.permissions)拒绝写入时的疑似文本标记。
# 本机真实语料抽样未命中过一例(kiro 未观测到类似 claude "OmniChat blocked" 的 hook
# 拒绝文本落盘)，属未经真实样本验证的防御性实现，交付说明里已标注。
_DENIAL_MARKERS = (
    "permission denied",
    "not allowed to",
    "denied by policy",
    "outside the allowed write paths",
)


# ── 会话级上下文(配套 .json，可整份读) ──────────────────────────────────


@dataclass
class _SessionContext:
    session_id: str
    cwd: str
    parent_session_id: str
    is_subagent: bool
    ts: str
    ts_note: str
    model: str
    model_confidence: str


def _normalize_iso_ms(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    m = _ISO_RE.match(raw.strip())
    if not m:
        return ""
    base, frac = m.group(1), m.group(2) or ""
    ms = (frac + "000")[:3]
    return f"{base}.{ms}Z"


def _mtime_iso(path: Path) -> str:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _load_sidecar(jsonl_path: Path) -> dict[str, Any] | None:
    sidecar = jsonl_path.with_suffix(".json")
    try:
        if sidecar.stat().st_size > _MAX_SIDECAR_BYTES:
            return None
        raw = sidecar.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _build_session_context(jsonl_path: Path) -> _SessionContext:
    sidecar = _load_sidecar(jsonl_path)

    session_id = ""
    cwd = ""
    parent_session_id = ""
    is_subagent = False
    ts = ""
    ts_note = ""
    model = ""
    model_confidence = "unknown"

    if sidecar is not None:
        sid = sidecar.get("session_id")
        if isinstance(sid, str) and sid:
            session_id = sid
        cwd_val = sidecar.get("cwd")
        if isinstance(cwd_val, str):
            cwd = cwd_val
        parent = sidecar.get("parent_session_id")
        if isinstance(parent, str):
            parent_session_id = parent
        is_subagent = sidecar.get("session_created_reason") == "subagent"

        ts = _normalize_iso_ms(sidecar.get("created_at"))
        if ts:
            ts_note = "ts=session_created_at"

        session_state = sidecar.get("session_state")
        session_state = session_state if isinstance(session_state, dict) else {}
        rts = session_state.get("rts_model_state")
        rts = rts if isinstance(rts, dict) else {}
        model_info = rts.get("model_info")
        if isinstance(model_info, dict):
            model_id = model_info.get("model_id")
            if isinstance(model_id, str) and model_id:
                model = model_id
                model_confidence = "low"

    if not session_id:
        session_id = jsonl_path.stem  # 目录命名约定：文件名本身就是 session uuid

    if not ts:
        ts = _mtime_iso(jsonl_path)
        ts_note = "ts=file_mtime(sidecar_unavailable)"

    return _SessionContext(
        session_id=session_id,
        cwd=cwd,
        parent_session_id=parent_session_id,
        is_subagent=is_subagent,
        ts=ts,
        ts_note=ts_note,
        model=model,
        model_confidence=model_confidence,
    )


# ── 事件抽取 ────────────────────────────────────────────────────────────


def _normalize_target(raw: str, cwd: str) -> str:
    if not raw:
        return ""
    p = Path(raw)
    if not p.is_absolute() and cwd:
        p = Path(cwd) / raw
    return str(p)


def _resolve_verdict(result_data: dict[str, Any]) -> tuple[str, bool, str]:
    status = result_data.get("status", "")
    content = result_data.get("content")
    text_parts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                data_val = item.get("data")
                if isinstance(data_val, str):
                    text_parts.append(data_val)
    text = " ".join(text_parts).lower()

    denial_kind = ""
    if status == "error" and any(marker in text for marker in _DENIAL_MARKERS):
        denial_kind = "permission-rule"

    if denial_kind:
        return "denied", True, denial_kind
    if status == "success":
        return "applied", False, ""
    if status == "error":
        return "error", False, ""
    return "unknown", False, ""


@dataclass
class _PendingToolUse:
    lineno: int
    tool: str
    base_kind: str  # "write" | "command"
    target: str
    target_confidence: str
    operation_id: str = ""


def _finalize_event(
    pc: _PendingToolUse,
    ctx: _SessionContext,
    *,
    evidence_path: str,
    verdict: str,
    kind_override: str | None,
    denial_kind: str,
    result_lineno: int | None,
) -> HrEvent:
    note = ctx.ts_note
    if result_lineno is not None:
        note = f"{note};result@L{result_lineno}" if note else f"result@L{result_lineno}"
    else:
        note = f"{note};no_result_by_eof" if note else "no_result_by_eof"
    return HrEvent(
        ts=ctx.ts,
        provider="kiro",
        session_id=ctx.session_id,
        kind=kind_override or pc.base_kind,
        evidence_ref=f"{evidence_path}#L{pc.lineno}",
        operation_id=pc.operation_id,
        model=ctx.model,
        model_confidence=ctx.model_confidence,
        parent_session_id=ctx.parent_session_id,
        is_subagent=ctx.is_subagent,
        cwd=ctx.cwd,
        tool=pc.tool,
        target=pc.target,
        target_confidence=pc.target_confidence,
        verdict=verdict,
        denial_kind=denial_kind,
        note=note,
    )


def collect_kiro_sessions(
    root: Path, *, watermark_state: dict[str, Any] | None = None
) -> CollectResult:
    """扫描 `<root>/sessions/cli/*.jsonl` + 配套 `.json`，产出归一化 kiro HrEvent。

    Args:
        root: kiro 家目录(生产环境是 `~/.kiro`；测试用 `tmp_path` 注入)。
        watermark_state: 上次运行留下的水位线，形如 `{"<abs_path>": FileWatermark.to_dict()}`
            (与 `hr/_jsonl_util.py` 的 `FileWatermark`/`JsonlTail` 约定一致)。
    """
    root = Path(root)
    watermark_state = watermark_state or {}
    new_watermark: dict[str, Any] = dict(watermark_state)
    result = CollectResult(provider="kiro", watermark=new_watermark)

    files = sorted(root.glob("sessions/cli/*.jsonl"))
    for fp in files:
        key = str(fp)
        result.files_seen += 1

        ctx = _build_session_context(fp)
        old_state = watermark_state.get(key)
        old_state = old_state if isinstance(old_state, dict) else {}
        wm = FileWatermark.from_dict(old_state)
        tail = JsonlTail(fp, wm)
        pending: dict[str, _PendingToolUse] = {}
        raw_pending = old_state.get("pending")
        if isinstance(raw_pending, dict):
            for operation_id, item in raw_pending.items():
                if not isinstance(item, dict):
                    continue
                try:
                    pending[str(operation_id)] = _PendingToolUse(**item)
                except TypeError:
                    continue
        newly_pending: set[str] = set()
        reset_handled = False

        for lineno, obj in tail:
            if tail.was_reset and not reset_handled:
                pending.clear()
                newly_pending.clear()
                reset_handled = True
            kind = obj.get("kind", "")
            data = obj.get("data")
            if not isinstance(data, dict):
                continue

            if kind == "AssistantMessage":
                content = data.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict) or item.get("kind") != "toolUse":
                        continue
                    tu = item.get("data")
                    if not isinstance(tu, dict):
                        continue
                    name = tu.get("name", "")
                    if not isinstance(name, str) or not name:
                        continue
                    lname = name.lower()
                    is_write = "write" in lname
                    is_command = lname == "shell"
                    if not (is_write or is_command):
                        continue
                    tool_use_id = tu.get("toolUseId", "")
                    input_obj = tu.get("input")
                    input_obj = input_obj if isinstance(input_obj, dict) else {}
                    base_kind = "write" if is_write else "command"
                    target = ""
                    if is_write:
                        target_raw = input_obj.get("path", "")
                        target_raw = target_raw if isinstance(target_raw, str) else ""
                        target = _normalize_target(target_raw, ctx.cwd)
                        if not target:
                            # "write-without-target:" 前缀与解析失败区分开：这是上游
                            # 格式没给路径，不是我们的 parser 挂了，混在一起会掩盖真实
                            # 的格式漂移(参见 codebuddy_source.py 同名注释)。
                            if len(result.failures) < _MAX_FAILURE_SAMPLES:
                                result.failures.append(
                                    f"write-without-target: {key}#L{lineno} "
                                    f"(toolUseId={tool_use_id})"
                                )
                            continue
                    pc = _PendingToolUse(
                        lineno=lineno,
                        tool=name,
                        base_kind=base_kind,
                        target=target,
                        target_confidence="direct" if is_write else "none",
                        operation_id=tool_use_id,
                    )
                    if tool_use_id:
                        pending[tool_use_id] = pc
                        newly_pending.add(tool_use_id)

            elif kind == "ToolResults":
                content = data.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict) or item.get("kind") != "toolResult":
                        continue
                    td = item.get("data")
                    if not isinstance(td, dict):
                        continue
                    tool_use_id = td.get("toolUseId", "")
                    pc = pending.pop(tool_use_id, None) if tool_use_id else None
                    newly_pending.discard(tool_use_id)
                    if pc is None:
                        continue
                    verdict, is_denial, denial_kind = _resolve_verdict(td)
                    event = _finalize_event(
                        pc,
                        ctx,
                        evidence_path=key,
                        verdict=verdict,
                        kind_override="denial" if is_denial else None,
                        denial_kind=denial_kind,
                        result_lineno=lineno,
                    )
                    problems = validate_event(event)
                    if problems:
                        if len(result.failures) < _MAX_FAILURE_SAMPLES:
                            result.failures.append(f"{key}#L{lineno}: invalid event {problems}")
                        continue
                    result.events.append(event)

        for operation_id in newly_pending:
            pc = pending.get(operation_id)
            if pc is None:
                continue
            event = _finalize_event(
                pc,
                ctx,
                evidence_path=key,
                verdict="unknown",
                kind_override=None,
                denial_kind="",
                result_lineno=None,
            )
            problems = validate_event(event)
            if problems:
                if len(result.failures) < _MAX_FAILURE_SAMPLES:
                    result.failures.append(f"{key}#L{pc.lineno}: invalid event {problems}")
                continue
            result.events.append(event)

        if tail.was_reset and not reset_handled:
            pending.clear()
        state = tail.watermark.to_dict()
        state["pending"] = {operation_id: asdict(pc) for operation_id, pc in pending.items()}
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


__all__ = ["collect_kiro_sessions"]
