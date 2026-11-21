# [OMNI] origin=claude-code domain=runtime/info_audit/audit_store ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:runtime.info_audit.llm_call_audit.persistence.py"
"""统一 LLM 调用审计存储 (Phase 2.5, D2 锁定)。

每次 LLMClient.call() 都会记录一条 LLMAuditRecord, 落到:

  {resolve_runtime_data_dir('llm_audit')}/{date}/{trace_id_or_default}.jsonl

存储策略:
  - **异步非阻塞**: 写入失败只打 WARN, 不影响 LLM 调用返回值
  - **append-only JSONL**: 天然支持并发 append, 无需锁
  - **按日期分目录**: 方便清理 / 归档 / 按天审计
  - **trace_id 维度**: 同一 run 的所有 LLM 调用落一个文件, 便于关联

读取: `load_historical_llm_calls(pipeline_id, node_id)` 给 dry-run 的
info_audit_probe 提供"真实历史 prompt",比只看类变量 FORMAT_IN 准得多。

**不记录**:
  - API key / base_url (敏感)
  - 用户个人身份信息
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any



@dataclass
class LLMAuditRecord:
    """单次 LLM 调用的完整审计记录 (prompt + 响应 + 元数据)。

    与现有 `LLMCallRecord` (llm.py 里的 cost meter) 的区别:
      - `LLMCallRecord`: 内存单例, 只存 token/cost 聚合指标
      - `LLMAuditRecord`: 落盘 jsonl, 存真实 prompt/response, 用于:
          1. info_audit dry-run probe 读历史真 prompt (P2.5.5)
          2. 事后复盘幻觉/截断/工具误用
          3. 跨 run diff 同一节点的 prompt 漂移
    """

    ts: float = 0.0
    trace_id: str = ""  # runner 传入, 空串则归为 "adhoc"
    node_id: str = ""  # caller 里的节点标识, 如 "req_analyzer"
    pipeline_id: str = ""  # 可空, runner 上下文已知时填
    role: str = ""  # llm role, 如 "runtime_main"
    model: str = ""  # 实际使用的模型
    caller: str = ""  # llm.py 内部 caller 字符串

    system_prompt: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)

    response_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""

    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0

    # Phase 2 产物
    info_audit_mode: str = "off"  # off | piggyback | strict
    info_audit: dict[str, Any] | None = None  # 序列化的 InfoAuditReport

    def __post_init__(self) -> None:
        if self.ts == 0.0:
            self.ts = time.time()


def _audit_root() -> Path:
    """统一 audit 根目录: data/_runtime/llm_audit/ (2026-04-21 B4 迁移).

    原路径 data/llm_audit/ 违反 archmap.yaml data.forbid_new_subdirs.
    迁至 _runtime/ 符合 "runtime 可写但不属特定 service/domain" 语义.
    """
    from omnicompany.core.config import resolve_runtime_data_dir
    return resolve_runtime_data_dir("llm_audit")


def _audit_file_for(rec: LLMAuditRecord) -> Path:
    day = datetime.fromtimestamp(rec.ts).strftime("%Y-%m-%d")
    trace = rec.trace_id or "adhoc"
    # trace_id 可能含非法字符, 做最小清洗
    safe_trace = "".join(c if c.isalnum() or c in "-_." else "_" for c in trace)[:64]
    d = _audit_root() / day
    d.mkdir(parents=True, exist_ok=True)
    # 2026-04-21 B7: 懒归档 - 每次写入时顺手扫一眼看有没有 >30 天的目录该归档
    _rotate_old_audit_dirs_lazy()
    return d / f"{safe_trace}.jsonl"


# 2026-04-21 B7: llm_audit 归档阈值 (30 天保留期, 老目录归 _archive/llm_audit_rotation/)
_LLM_AUDIT_RETENTION_DAYS: int = 30
_LLM_AUDIT_ROTATE_CHECK_CACHE: dict[str, bool] = {"checked": False}


def _rotate_old_audit_dirs_lazy() -> None:
    """懒归档: 首次 record_llm_call 时检查 <audit_root>/ 下的 date 目录,
    把超过 _LLM_AUDIT_RETENTION_DAYS 的归档到 _archive/llm_audit_rotation/.

    只在进程生命周期内执行一次 (cache flag), 不做持续扫描。
    失败只 WARN, 不阻塞正常审计写入。
    """
    if _LLM_AUDIT_ROTATE_CHECK_CACHE["checked"]:
        return
    _LLM_AUDIT_ROTATE_CHECK_CACHE["checked"] = True
    try:
        import shutil
        root = _audit_root()
        if not root.exists():
            return
        cutoff = datetime.now().date()
        archive_root = root.parent.parent / "_archive" / "llm_audit_rotation"
        moved = 0
        for d in root.iterdir():
            if not d.is_dir():
                continue
            # 目录名应是 YYYY-MM-DD
            try:
                day = datetime.strptime(d.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            age = (cutoff - day).days
            if age > _LLM_AUDIT_RETENTION_DAYS:
                archive_root.mkdir(parents=True, exist_ok=True)
                shutil.move(str(d), str(archive_root / d.name))
                moved += 1
        if moved:
            import warnings
            warnings.warn(
                f"llm_audit 归档: {moved} 个 >{_LLM_AUDIT_RETENTION_DAYS} 天目录已移到 {archive_root}",
                RuntimeWarning,
                stacklevel=2,
            )
    except Exception as e:
        import warnings
        warnings.warn(f"llm_audit rotate check failed: {e}", RuntimeWarning, stacklevel=2)


def record_llm_call(rec: LLMAuditRecord) -> None:
    """落盘一条 LLMAuditRecord。失败只 WARN, 不抛。

    **必须永不阻塞正常路径**: 这是 §9.2 风险对策的硬约束。
    """
    try:
        path = _audit_file_for(rec)
        # 只存摘要 + 必要原文, 过长字段截断避免失控
        d = asdict(rec)
        d["system_prompt"] = _truncate(d.get("system_prompt", ""), 20000)
        d["response_text"] = _truncate(d.get("response_text", ""), 20000)
        # messages: 只保留 role + text 预览, 避免把 base64 图像这种大物落盘
        d["messages"] = [_truncate_message(m) for m in d.get("messages", [])]
        # tools: 保留名称 + 简短描述
        d["tools"] = [
            {
                "name": (t.get("name") or t.get("function", {}).get("name", "?")),
                "description_preview": _truncate(
                    t.get("description") or t.get("function", {}).get("description", ""),
                    200,
                ),
            }
            for t in d.get("tools", [])
        ]
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    except Exception as e:
        # 明确不 raise, 只留个警告
        import warnings
        warnings.warn(f"LLMAuditRecord persist failed: {e}", RuntimeWarning, stacklevel=2)


def _truncate(s: str, limit: int) -> str:
    if not isinstance(s, str):
        return s
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n... <truncated {len(s) - limit} chars>"


def _truncate_message(m: dict[str, Any]) -> dict[str, Any]:
    """把 message 的 content 缩成可审计但不爆盘的形式。"""
    out = {"role": m.get("role", "user")}
    content = m.get("content")
    if isinstance(content, str):
        out["content_preview"] = _truncate(content, 4000)
    elif isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if not isinstance(b, dict):
                parts.append(str(b)[:200])
                continue
            btype = b.get("type", "text")
            if btype == "text":
                parts.append(b.get("text", "")[:1000])
            elif btype in ("image", "image_url"):
                parts.append(f"[image:{btype}]")
            elif btype == "tool_use":
                parts.append(f"[tool_use:{b.get('name', '?')}]")
            elif btype == "tool_result":
                parts.append(f"[tool_result:{str(b.get('content', ''))[:500]}]")
            else:
                parts.append(f"[{btype}]")
        out["content_preview"] = _truncate("\n".join(parts), 4000)
    else:
        out["content_preview"] = ""
    return out


# ---------------------------------------------------------------------------
# 历史查询 (为 dry-run probe 供料)
# ---------------------------------------------------------------------------


def load_historical_llm_calls(
    *,
    pipeline_id: str | None = None,
    node_id: str | None = None,
    last_n: int = 5,
) -> list[dict[str, Any]]:
    """按 pipeline_id + node_id 过滤历史 LLM 调用,返回最近 N 条 dict。

    用于 dry-run info_audit_probe 在没有真实运行时, 读历史真 prompt
    喂给独立审计 LLM, 避免仅看 FORMAT_IN/OUT 类变量的粗略审计。

    扫描策略: 倒序扫最近 7 天目录, 每个 jsonl 从后往前读, 匹配即收。
    未找到返回 []。
    """
    root = _audit_root()
    if not root.exists():
        return []

    # 最近 7 天
    days = sorted(
        [p for p in root.iterdir() if p.is_dir()],
        reverse=True,
    )[:7]

    out: list[dict[str, Any]] = []
    for day_dir in days:
        for jf in sorted(day_dir.glob("*.jsonl"), reverse=True):
            try:
                lines = jf.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if pipeline_id and rec.get("pipeline_id") != pipeline_id:
                    continue
                if node_id and rec.get("node_id") != node_id:
                    continue
                out.append(rec)
                if len(out) >= last_n:
                    return out
    return out
