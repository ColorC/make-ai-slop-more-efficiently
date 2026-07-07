# [OMNI] origin=ai-ide domain=decisions ts=2026-06-27T00:00:00Z type=paths status=active
# [OMNI] summary="exploration 子域产物路径单点真源:因果边 sidecar / 缺口回填台账 / 投影缓存。"
# [OMNI] why="路径散落易漂移;projection/backfill/causal_extract 共用。复用 decisions/_paths 的 DATA_ROOT。"
# [OMNI] tags=decisions,exploration,paths
"""exploration 子域产物路径(单点真源)。

因果边/回填台账落在 decisions 数据根下的 exploration/ 子目录,与统一决策库 records.jsonl 并列。
因果边不塞进 decision.record.links(那只有 rests_on/supersedes/parent/related,改它会和
DECISION-MEMORY 的 schema 扩展撞车),单独 sidecar 存,投影时与 links 合并。
"""

from __future__ import annotations

from .._paths import DATA_ROOT

EXPLORATION_ROOT = DATA_ROOT / "exploration"
CAUSAL_EDGES_PATH = EXPLORATION_ROOT / "causal_edges.jsonl"   # 因果边 sidecar(refines/critiques/...)
BACKFILL_LEDGER_PATH = EXPLORATION_ROOT / "backfill_ledger.jsonl"  # 回填台账(节点→真实 material_id)
GRAPH_CACHE_PATH = EXPLORATION_ROOT / "graph_cache.json"     # 投影缓存(带 source token)


def ensure_dirs() -> None:
    EXPLORATION_ROOT.mkdir(parents=True, exist_ok=True)
