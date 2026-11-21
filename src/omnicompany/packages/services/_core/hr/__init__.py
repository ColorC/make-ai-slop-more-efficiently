# [OMNI] origin=claude-code ts=2026-08-17T00:00:00+08:00 type=package
# [OMNI] summary="LLM 人事系统(hr) 包 —— 离线解析 CLI transcript / 文件系统 / git，产出可追责的归一化事件"
# [OMNI] why="绩效·综评·事故追溯的地基：认出什么文件什么时候被什么模型改"
# [OMNI] tags=hr,attribution,provenance,offline
"""LLM 人事系统（hr）—— 离线归因与绩效基础设施。

权威依据: docs/plans/agent-framework/[2026-08-17]LLM-HR-PERFORMANCE-ATTRIBUTION/plan.md

本包**不接 hook、不进前台热路径**，全部工作在离线批跑中完成（plan.md §2.0）。
共同出口格式见 `schema.py`（唯一真源，实现方不得自造字段）。

P1 并行开发期间本文件保持最小，**不做 re-export** —— 各模块请用完整路径导入，
避免五棒并行时争抢同一个文件。集成阶段由主会话统一补 re-export。
"""

from __future__ import annotations

__all__: list[str] = []
