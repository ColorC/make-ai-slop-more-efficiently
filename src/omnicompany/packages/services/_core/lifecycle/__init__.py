# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-06-25T00:00:00Z type=infra status=active
# [OMNI] summary="工作生命周期服务: note 只读读取器(读 overlay-note-store 浅路径,不建第二套) + task 一等对象 + plan→task 拆分"
# [OMNI] why="WORK-LIFECYCLE-AND-DISPATCH 计划: note→plan→task→material→review 流水线的对象层"
# [OMNI] tags=lifecycle,note,task,pipeline
# [OMNI] material_id="material:services._core.lifecycle.__init__.py"
"""工作生命周期服务 (note / task / split)。

设计铁律:
- **note 不搞两套**: note 唯一真源是 overlay-note-store(C:/workspace/overlay-note-store),
  本服务只读它(read_note_source),绝不在 omni 另建 note 存储。
- **task 也不搞两套**(TASK-SSOT-UNIFICATION 2026-07-05): 任务唯一真源是 progress-service(:8230),
  执行 task 以计划级 task 的子 task 形态存在 whatnow.json 里; TaskStore 只是它的客户端。
- LLM 流水线(note→plan / plan→task)走统一 AgentNodeLoop(run_json_agent),不 fork agent。
"""
from __future__ import annotations

from omnicompany.packages.services._core.lifecycle.note_source import (
    OverlayNote,
    OverlayNoteSource,
    PoofNote,
    PoofNoteSource,
    overlay_note_store_dir,
    poof_notes_dir,
    read_note_source,
)
from omnicompany.packages.services._core.lifecycle.task import (
    Task,
    TaskStore,
    VALID_PRIORITY,
    VALID_STATUS,
)
from omnicompany.packages.services._core.lifecycle.claim_route import (
    TaskPositionClaimReceipt,
    TaskPositionClaimRequest,
    claim_task_to_position,
)

__all__ = [
    "OverlayNote",
    "OverlayNoteSource",
    "overlay_note_store_dir",
    "PoofNote",
    "PoofNoteSource",
    "poof_notes_dir",
    "read_note_source",
    "Task",
    "TaskStore",
    "TaskPositionClaimReceipt",
    "TaskPositionClaimRequest",
    "claim_task_to_position",
    "VALID_STATUS",
    "VALID_PRIORITY",
]
