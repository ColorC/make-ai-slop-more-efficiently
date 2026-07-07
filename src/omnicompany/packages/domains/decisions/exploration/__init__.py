# [OMNI] origin=ai-ide domain=decisions ts=2026-06-27T00:00:00Z type=package status=active
# [OMNI] summary="探索路径可视化 / 决策树自动构建 子包。把决策库+物料注册表投影成 material-centric 探索 DAG(散落根+版本链),供前端历史展示与丰富跳转。"
# [OMNI] why="poof note-edsspn3 + 用户 2026-06-27 拍板:图=真本体的投影(不另建手维护数据集),做版本化(supersedes 成 DAG),保持散落根(主干自然涌现)。权威=docs/plans/[2026-06-27]EXPLORATION-PATH-VIZ/plan.md。"
# [OMNI] tags=decisions,exploration,decision-tree,projection,visualization
"""探索路径可视化 / 决策树自动构建。

模块:
  - projection.py   从真本体(决策库 + 因果边 sidecar + 物料注册表)投影出 material-centric 图。
  - version.py      版本号/版本族解析 + 从 supersedes 边拼版本链(让图成 DAG)。
  - backfill.py     把真本体里缺的产物/设施/真源/根理念注册回填进来。
  - causal_extract.py  从对话散文抽 refines/critiques/responds_to_critique + rationale 因果边。

铁律:图是对真本体的投影,节点主键直接用真本体 id,可下钻回原文;真本体缺的显式标缺口。
"""

from __future__ import annotations

from . import projection, version

__all__ = ["projection", "version"]
