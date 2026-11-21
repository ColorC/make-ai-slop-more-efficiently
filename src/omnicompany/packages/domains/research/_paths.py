# [OMNI] origin=ai-ide domain=research ts=2026-06-14T00:00:00Z type=paths status=active
# [OMNI] summary="research domain 产物路径单点真源。统一研究库/runs/reports/coverage 的根。"
# [OMNI] why="路径散落各处易漂移; 单点定义,routers/library 共用。"
# [OMNI] tags=research,paths
"""research domain 产物路径(单点真源)。"""

from __future__ import annotations

from pathlib import Path

# research/_paths.py → parents: research[0]/domains[1]/packages[2]/omnicompany[3]/src[4]/仓根[5]
_OMNI_ROOT = Path(__file__).resolve().parents[5]

DATA_ROOT = _OMNI_ROOT / "data" / "domains" / "research"
LIBRARY_ROOT = DATA_ROOT / "library"          # 统一研究库(累积、查重对象)
RECORDS_PATH = LIBRARY_ROOT / "records.jsonl"  # 一行一条研究记录(append-only,墓碑软删)
INDEX_PATH = LIBRARY_ROOT / "index.json"       # 倒排: topic_norm/keyword → record_id(查重用)
RUNS_ROOT = DATA_ROOT / "runs"                 # 单次调研原始片段/中间态
REPORTS_ROOT = DATA_ROOT / "reports"           # 待发布 markdown

# 统一本地资产 catalog(研究记录 + 已拉 repo + 资料,别名召回,"先查本地"的真源)
CATALOG_JSONL = LIBRARY_ROOT / "catalog.jsonl"  # append-only 真源(repo/material 条目)
CATALOG_JSON = LIBRARY_ROOT / "catalog.json"    # 聚合视图(可直接 grep)
SNAPSHOTS_ROOT = LIBRARY_ROOT / "snapshots"     # 源原文快照(内容寻址 <sha1(url)>.txt)


def ensure_dirs() -> None:
    for p in (LIBRARY_ROOT, RUNS_ROOT, REPORTS_ROOT, SNAPSHOTS_ROOT):
        p.mkdir(parents=True, exist_ok=True)
