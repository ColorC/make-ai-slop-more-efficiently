# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:core.team_builder.router_compatibility.shim.py"
"""workflow_factory/routers.py — 向后兼容 shim (Clean Migration 2026-04-20).

真实 Worker 实现在 `workers/` 目录 (14 Worker, Diamond 继承 · 业务代码在
`routers_legacy.py`, 2026-07-26 OMNI-040 Stage 3 已迁回正式位置).

兼容入口:
  - 新名 `*Worker`: from workers 重导出
  - 旧名 `*Router` (别名): 指向对应 `*Worker` 类
  - 模块级辅助函数 (`_wf_no_trunc` / `_extract_json_obj` / `check_format_in_consumption`
    / `_GLOBAL_FIX_LIMIT` / `_check_global_fix_iter` / `_wf_extract_python_code`):
    从 `routers_legacy.py` re-export

不要往本文件加新逻辑; 新增 Worker 请直接写 `workers/<name>.py`.
旧代码 `from ...workflow_factory.routers import ErrorRouteAuditorRouter` 继续可用.
"""
from __future__ import annotations

# ─── Worker 类 (新名, 推荐使用) ─────────────────────────────────────────
from .workers import (
    ReqAnalyzerWorker,
    FormatDesignerWorker,
    NodePlannerWorker,
    NodePlanAuditorWorker,
    FrameworkContextLoaderWorker,
    CodeGenFormatsWorker,
    CodeGenPipelineWorker,
    CodeGenRoutersWorker,
    CodeGenRunWorker,
    SyntaxFixerWorker,
    DeterministicFixerWorker,
    AutoFixerWorker,
    CompileCheckerWorker,
    ErrorRouteAuditorWorker,
    IntegrationTesterWorker,
    FinalizerWorker,
    ALL_WORKERS,
)

# ─── 模块级辅助 (re-export 自 routers_legacy) ───────────────────────────
from .routers_legacy import (
    _wf_no_trunc,
    _extract_json_obj,
    check_format_in_consumption,
    _GLOBAL_FIX_LIMIT,
    _check_global_fix_iter,
    _wf_extract_python_code,
    _CodeGenBaseRouter,  # 共享基类, 子类化 path
)


# ─── 旧名别名 (兼容) ────────────────────────────────────────────────────
ReqAnalyzerRouter = ReqAnalyzerWorker
FormatDesignerRouter = FormatDesignerWorker
NodePlannerRouter = NodePlannerWorker
NodePlanAuditorRouter = NodePlanAuditorWorker
FrameworkContextLoaderRouter = FrameworkContextLoaderWorker
CodeGenFormatsRouter = CodeGenFormatsWorker
CodeGenPipelineRouter = CodeGenPipelineWorker
CodeGenRoutersRouter = CodeGenRoutersWorker
CodeGenRunRouter = CodeGenRunWorker
SyntaxFixerRouter = SyntaxFixerWorker
DeterministicFixerRouter = DeterministicFixerWorker
AutoFixerRouter = AutoFixerWorker
CompileCheckerRouter = CompileCheckerWorker
ErrorRouteAuditorRouter = ErrorRouteAuditorWorker
IntegrationTesterRouter = IntegrationTesterWorker
# 2026-07-03 批4: LAP 九维检查器(旧 verifier 节点)显式废止, 旧名别名一并摘除。
FinalizerRouter = FinalizerWorker


__all__ = [
    # 新名 (推荐)
    "ReqAnalyzerWorker",
    "FormatDesignerWorker",
    "NodePlannerWorker",
    "NodePlanAuditorWorker",
    "FrameworkContextLoaderWorker",
    "CodeGenFormatsWorker",
    "CodeGenPipelineWorker",
    "CodeGenRoutersWorker",
    "CodeGenRunWorker",
    "SyntaxFixerWorker",
    "DeterministicFixerWorker",
    "AutoFixerWorker",
    "CompileCheckerWorker",
    "ErrorRouteAuditorWorker",
    "IntegrationTesterWorker",
    "FinalizerWorker",
    "ALL_WORKERS",
    # 旧名 (兼容)
    "ReqAnalyzerRouter",
    "FormatDesignerRouter",
    "NodePlannerRouter",
    "NodePlanAuditorRouter",
    "FrameworkContextLoaderRouter",
    "CodeGenFormatsRouter",
    "CodeGenPipelineRouter",
    "CodeGenRoutersRouter",
    "CodeGenRunRouter",
    "SyntaxFixerRouter",
    "DeterministicFixerRouter",
    "AutoFixerRouter",
    "CompileCheckerRouter",
    "ErrorRouteAuditorRouter",
    "IntegrationTesterRouter",
    "FinalizerRouter",
    # 辅助函数 / 共享基类
    "_wf_no_trunc",
    "_extract_json_obj",
    "check_format_in_consumption",
    "_GLOBAL_FIX_LIMIT",
    "_check_global_fix_iter",
    "_wf_extract_python_code",
    "_CodeGenBaseRouter",
]
