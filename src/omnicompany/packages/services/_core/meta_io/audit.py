# [OMNI] origin=ai-ide domain=services/_core/meta_io ts=2026-05-02T07:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="元 IO 调用审计 log - 落 data/_runtime/meta_io_audit/<YYYY-MM-DD>/<trace_id>.jsonl"
# [OMNI] why="跟 LLM 审计 (data/_runtime/llm_audit/) 同形态. 让元 IO 调用可反查 + 审计可视化"
# [OMNI] tags=meta_io,audit,jsonl
# [OMNI] material_id="material:core.meta_io.audit_logger.implementation.py"
"""元 IO 调用审计.

每次 SingleToolRouter 实际跑 _execute() 时记一条审计 (在 SingleToolRouter.run 里挂钩).
轻量, 不阻塞工具调用.

字段:
  - meta_io_id: 操作的元 IO id (从 tool.CONSUMED_META_IO / PRODUCED_META_IO 推断)
  - kind: read / write / mutate
  - tool_name: 调用的 tool
  - trace_id: 当前 session
  - ts / duration_ms: 时间
  - target_resource: 目标资源 (file_path / url 等, 从 tool_args 抽)
  - is_error: 调用是否失败

调用方式:
  emit_meta_io_audit(meta_io_id="meta_io.fs.read_file_text", tool_name="read_file",
                     trace_id="cc_xxx", target_resource="docs/foo.md", duration_ms=12)
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _audit_root() -> Path:
    """跟 cli/commands/llm_audit.py 类似, 落 data/_runtime/meta_io_audit/<YYYY-MM-DD>/."""
    from omnicompany.core.config import resolve_runtime_data_dir
    return resolve_runtime_data_dir("meta_io_audit")


def emit_meta_io_audit(
    *,
    meta_io_id: str,
    tool_name: str,
    trace_id: str | None = None,
    target_resource: str | None = None,
    duration_ms: float | None = None,
    is_error: bool = False,
    extra: dict[str, Any] | None = None,
) -> None:
    """记一条元 IO 调用审计. 失败不阻塞 (容错)."""
    if not meta_io_id or meta_io_id == "*":
        return  # '*' 不审计 (太通用)

    root = _audit_root()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    day_dir = root / today
    try:
        day_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    if trace_id is None:
        try:
            from omnicompany.packages.services._core.identity import resolve_active_trace_id
            trace_id = resolve_active_trace_id()
        except Exception:
            trace_id = "unknown"

    record = {
        "ts": time.time(),
        "ts_iso": datetime.utcnow().isoformat() + "Z",
        "meta_io_id": meta_io_id,
        "tool_name": tool_name,
        "trace_id": trace_id,
        "target_resource": target_resource,
        "duration_ms": duration_ms,
        "is_error": is_error,
    }
    if extra:
        record.update(extra)

    jf = day_dir / f"{trace_id}.jsonl"
    try:
        with jf.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def query_audit(
    *,
    trace_id: str | None = None,
    meta_io_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """查审计记录 (跟 dashboard / CLI 联动用).

    支持 trace_id 跟 meta_io_id 两个过滤维度. 不传则按时间倒序遍历 (限 limit).
    """
    root = _audit_root()
    if not root.exists():
        return []
    out: list[dict] = []
    day_dirs = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
    for day_dir in day_dirs:
        if trace_id:
            jf = day_dir / f"{trace_id}.jsonl"
            files = [jf] if jf.exists() else []
        else:
            files = sorted(day_dir.glob("*.jsonl"), reverse=True)
        for jf in files:
            try:
                with jf.open("r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if meta_io_id and rec.get("meta_io_id") != meta_io_id:
                            continue
                        out.append(rec)
                        if len(out) >= limit:
                            return out
            except OSError:
                continue
    return out
