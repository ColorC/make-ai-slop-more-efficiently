# [OMNI] origin=claude-code domain=omnicompany/knowledge ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:learning.knowledge.kb_entry_writer.worker.py"
"""KBWriteWorker — OmniKB 条目写入 (Stage 3 独立文件).

Worker 协议:
  FORMAT_IN  = kb.entry_draft
  FORMAT_OUT = kb.entry_committed
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.packages.services._learning.knowledge import (
    KBStore,
    entry_class_for,
)
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import _project_root


class KBWriteWorker(Worker):
    """写入一个 entry。

    input_data = {
        "entry": {...},       # dict 形式的 entry (必须含 omnikb_type)
        "body": "...",        # 可选, markdown 正文
        "overwrite": False,   # 可选, True 时覆盖整个文件
    }

    返回 output 含落盘路径 + 是否新建/更新。
    """

    DESCRIPTION = "OmniKB 写入 Worker: 构造 entry → guarded_write 到磁盘"
    FORMAT_IN = "kb.entry_draft"
    FORMAT_OUT = "kb.entry_committed"

    def __init__(self, *, project_root: Path | None = None) -> None:
        self._project_root = project_root or _project_root()

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="KBWriteWorker 需要 dict 输入",
            )

        entry_data = input_data.get("entry")
        if not isinstance(entry_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="input.entry 必须是 dict",
            )

        omnikb_type = entry_data.get("omnikb_type")
        cls = entry_class_for(omnikb_type or "")
        if cls is None:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"未知或缺失 omnikb_type: {omnikb_type!r}",
            )

        try:
            entry = cls(**entry_data)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"entry 构造失败: {e}",
            )

        body = input_data.get("body", "") or ""
        overwrite = bool(input_data.get("overwrite", False))

        store = KBStore(self._project_root)
        was_existing = store.find_by_id(entry.id) is not None

        try:
            path = store.write_entry(entry, body=body, overwrite=overwrite)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"写盘失败: {e}",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "id": entry.id,
                "omnikb_type": entry.omnikb_type,
                "path": str(path),
                "was_update": was_existing,
                "was_new": not was_existing,
            },
            confidence=1.0,
            diagnosis=(
                f"{'updated' if was_existing else 'created'} {entry.id} at {path.name}"
            ),
            granted_tags=["domain.knowledge", "stage.written"],
        )
