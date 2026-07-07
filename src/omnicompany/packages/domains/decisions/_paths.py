# [OMNI] origin=ai-ide domain=decisions ts=2026-06-18T00:00:00Z type=paths status=active
# [OMNI] summary="decisions domain 产物路径单点真源。统一决策库/索引/runs 的根。"
# [OMNI] why="路径散落易漂移; 单点定义,library/catalog/cli 共用。照 research/_paths 范式。"
# [OMNI] tags=decisions,paths
"""decisions domain 产物路径(单点真源)。

契约(schema/管线代码)在本 domain;记录产物(统一决策库)落在仓根 data/domains/decisions。
"""

from __future__ import annotations

from pathlib import Path

# decisions/_paths.py → parents: decisions[0]/domains[1]/packages[2]/omnicompany[3]/src[4]/仓根[5]
_OMNI_ROOT = Path(__file__).resolve().parents[5]

DATA_ROOT = _OMNI_ROOT / "data" / "domains" / "decisions"
LIBRARY_ROOT = DATA_ROOT / "library"             # 统一决策库
RECORDS_PATH = LIBRARY_ROOT / "records.jsonl"    # 一行一条 decision.record(append-only,墓碑软删,最新行权威)
INDEX_PATH = LIBRARY_ROOT / "index.json"         # 聚合视图:id→statement + by_alias/by_tag/by_project(可 grep)
RUNS_ROOT = DATA_ROOT / "runs"                   # 抽取管线单次 run 的 observation 原始片段/中间态
VERB_ANNOTATIONS_PATH = DATA_ROOT / "verb_annotations.jsonl"  # 标准化动词层边标注(ledgers.yaml: verb-annotations)


def ensure_dirs() -> None:
    for p in (LIBRARY_ROOT, RUNS_ROOT):
        p.mkdir(parents=True, exist_ok=True)
