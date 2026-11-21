# [OMNI] origin=codex domain=services/session-companion ts=2026-08-11 type=service
# [OMNI] summary="伴随会话维护编排层：生命周期入队、低成本 agent 续接与既有真源的确定性写入"
# [OMNI] why="把会话结束/压缩后的摘要、范围、材料和反馈维护收成一个幂等闭环，且不复制业务真源"
# [OMNI] tags=session,companion,maintenance,reviewstage,identity
"""Session companion runtime.

Only orchestration state lives here.  User-facing summaries, scope bindings,
materials, comments and verdicts remain in their established stores.
"""

from .models import CompanionEvent, build_event
from .service import CompanionService, enqueue_lifecycle_event
from .store import CompanionStore

__all__ = [
    "CompanionEvent",
    "CompanionService",
    "CompanionStore",
    "build_event",
    "enqueue_lifecycle_event",
]

