# [OMNI] origin=claude-code domain=services/_learning/gddecon ts=2026-06-27T00:00:00Z type=worker status=active
"""gddecon workers —— 事件型引擎 (MaterialDispatcher) 的 worker 清单。"""
from .deconstruction_worker import DeconstructionWorker
from .gap_worker import GapReportWorker
from .ui_standard_worker import UiStandardWorker
from .ui_build_worker import UiBuildWorker
from .info_hierarchy_worker import InfoHierarchyWorker
from .interaction_model_worker import InteractionModelWorker

ALL_WORKERS = [DeconstructionWorker]   # gddecon-aspect-tree 事件型 worker 清单
GAP_WORKERS = [GapReportWorker]        # gddecon-gap-report 事件型 worker 清单
UI_STANDARD_WORKERS = [UiStandardWorker]  # gddecon-ui-standard 事件型 worker 清单
UI_BUILD_WORKERS = [UiBuildWorker]     # gddecon-ui-build 事件型 worker 清单
INFO_HIER_WORKERS = [InfoHierarchyWorker]  # gddecon-info-hierarchy 事件型 worker 清单
INTERACTION_WORKERS = [InteractionModelWorker]  # gddecon-interaction-model 事件型 worker 清单

__all__ = [
    "DeconstructionWorker", "GapReportWorker", "UiStandardWorker", "UiBuildWorker",
    "InfoHierarchyWorker", "InteractionModelWorker",
    "ALL_WORKERS", "GAP_WORKERS", "UI_STANDARD_WORKERS", "UI_BUILD_WORKERS",
    "INFO_HIER_WORKERS", "INTERACTION_WORKERS",
]
