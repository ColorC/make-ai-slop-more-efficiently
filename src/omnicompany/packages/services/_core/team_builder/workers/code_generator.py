# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.legacy_per_file_codegen.fallback.py"
"""CodeGen*Worker — workflow_factory Team 的 4 个 per-file code-gen 子 Worker.

**注**: 当前 pipeline.py 实际使用的是 CodeGenLoop (AgentNodeLoop, 见
       `../routers_codegen.py`). 这 4 个旧 per-file 子 Router 作为可选 fallback
       保留, 未被活跃管线引用, 但仍是 Clean Migration 必须 re-export 的 Worker
       (14 保持 14 的约束).

共享基类 (_CodeGenBaseRouter) 抽到 `_shared.py`, 通过 Diamond 挂 Worker.

4 子 Worker:
  - CodeGenFormatsWorker    · FILE_KEY=formats.py   · FORMAT_IN=wf.node_plan_augmented
  - CodeGenPipelineWorker   · FILE_KEY=pipeline.py  · FORMAT_IN=wf.code_gen_state
  - CodeGenRoutersWorker    · FILE_KEY=routers.py   · FORMAT_IN=wf.code_gen_state
  - CodeGenRunWorker        · FILE_KEY=run.py       · FORMAT_OUT=wf.project_skeleton
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import (
    CodeGenFormatsRouter as _LegacyFormats,
    CodeGenPipelineRouter as _LegacyPipeline,
    CodeGenRoutersRouter as _LegacyRouters,
    CodeGenRunRouter as _LegacyRun,
)


class CodeGenFormatsWorker(Worker, _LegacyFormats):
    """生成 formats.py (Format 类型定义文件, P7.2 拆分第一步)."""
    pass


class CodeGenPipelineWorker(Worker, _LegacyPipeline):
    """生成 pipeline.py (TeamSpec 拓扑定义)."""
    pass


class CodeGenRoutersWorker(Worker, _LegacyRouters):
    """生成 routers.py (每个节点的 Router 类实现)."""
    pass


class CodeGenRunWorker(Worker, _LegacyRun):
    """生成 run.py + 收敛 state 为 wf.project_skeleton (P7.2 最终步)."""
    pass
