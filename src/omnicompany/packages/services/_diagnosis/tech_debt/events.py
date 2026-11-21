# [OMNI] origin=claude-code domain=services/tech_debt ts=2026-04-18T00:00:00Z
# [OMNI] material_id="material:diagnosis.tech_debt.arch_change_event_logger.py"
"""tech_debt.events — docs/ARCH-CHANGES.jsonl 统一写入 API。

背景：
  - guardian/registry_updater.py 已写 event_type=violation-found
  - semantic_auditor/routers.py FindingWriterRouter 已写 event_type=finding-generated
  - tech_debt/registry_io.py._append_resolved_event 已写 event_type=violation-resolved
  - 本模块：统一 schema + 提供 append_event() 给未来写入方使用，
    短期内 debt scan 消费本模块写 scan-started / scan-completed 事件

不重构既有 producer（Phase C2 scope 管控，D2 铁律延续）——先统一 schema，
字段对齐；后续再合并历史写入点到本模块。

事件统一 schema（ARCH-CHANGES.jsonl 每行一条）：
  {
    "change_id": "ARCH-YYYY-MM-DD-NNN",    # 日内自增
    "ts": "2026-04-18T...+00:00",            # ISO8601 UTC
    "initiator": "guardian|semantic_auditor|tech_debt|human|<agent-name>",
    "event_type": "violation-found|finding-generated|violation-resolved|"
                  "scan-started|scan-completed",
    "drawer": "services/<name>",
    "related_pipeline": "<pipeline-id 或空字符串>",
    "change": "<简短描述>",
    "payload": {<可选，结构化数据>}          # Phase C2 新增
  }
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_ARCH_RELPATH = "docs/ARCH-CHANGES.jsonl"

# 已知的 event_type（用于文档/测试自查，不强制校验，允许未来扩展）
KNOWN_EVENT_TYPES = frozenset({
    "violation-found",        # Guardian patrol 发现新违规
    "violation-resolved",     # tech_debt resolve 标记解决
    "finding-generated",      # SemanticAuditor 产出新 Finding
    "scan-started",           # omni debt scan 开始
    "scan-completed",         # omni debt scan 结束
})

KNOWN_INITIATORS = frozenset({
    "guardian", "semantic_auditor", "tech_debt", "human",
})  # 其他（claude-code / 具体 agent 名）也允许，仅供参考


@dataclass
class ARCHEvent:
    """ARCH-CHANGES.jsonl 一条事件。"""
    change_id: str
    ts: str
    initiator: str
    event_type: str
    drawer: str = ""
    related_pipeline: str = ""
    change: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_jsonl_line(self) -> str:
        d = asdict(self)
        # payload 为空字典时从输出去掉，减少噪音 + 向后兼容既有事件
        if not d["payload"]:
            d.pop("payload")
        return json.dumps(d, ensure_ascii=False)


def _next_change_id(arch_path: Path, today: str) -> str:
    """扫当前文件同日最大 NNN 序号，返回下一个 ID。"""
    max_n = 0
    prefix = f"ARCH-{today}-"
    if arch_path.exists():
        try:
            for line in arch_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = ev.get("change_id", "")
                if cid.startswith(prefix):
                    try:
                        n = int(cid[len(prefix):])
                        if n > max_n:
                            max_n = n
                    except ValueError:
                        pass
        except OSError:
            pass
    return f"ARCH-{today}-{max_n + 1:03d}"


def append_event(
    project_root: str | Path,
    *,
    event_type: str,
    initiator: str,
    drawer: str = "",
    related_pipeline: str = "",
    change: str = "",
    payload: dict[str, Any] | None = None,
    arch_relpath: str = _DEFAULT_ARCH_RELPATH,
) -> ARCHEvent | None:
    """追加一条事件到 ARCH-CHANGES.jsonl。

    返回写入的 ARCHEvent；写入失败返回 None（不抛异常，不阻塞调用方）。
    """
    root = Path(project_root)
    arch_path = root / arch_relpath

    now_iso = datetime.now(timezone.utc).isoformat()
    today = now_iso[:10]
    change_id = _next_change_id(arch_path, today)

    event = ARCHEvent(
        change_id=change_id,
        ts=now_iso,
        initiator=initiator,
        event_type=event_type,
        drawer=drawer,
        related_pipeline=related_pipeline,
        change=change,
        payload=payload or {},
    )

    try:
        arch_path.parent.mkdir(parents=True, exist_ok=True)
        with arch_path.open("a", encoding="utf-8") as fh:
            fh.write(event.to_jsonl_line() + "\n")
        return event
    except OSError as e:
        logger.warning("ARCH-CHANGES append 失败: %s", e)
        return None


def read_events(
    project_root: str | Path,
    *,
    event_type: str | None = None,
    since_date: str | None = None,
    arch_relpath: str = _DEFAULT_ARCH_RELPATH,
) -> list[dict]:
    """读取 ARCH-CHANGES.jsonl。

    过滤：
      - event_type：只返回指定类型
      - since_date：YYYY-MM-DD，只返回 ts >= 该日期的
    """
    root = Path(project_root)
    arch_path = root / arch_relpath
    if not arch_path.exists():
        return []

    out: list[dict] = []
    try:
        for line in arch_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type is not None and ev.get("event_type") != event_type:
                continue
            if since_date is not None and ev.get("ts", "")[:10] < since_date:
                continue
            out.append(ev)
    except OSError:
        return []
    return out
