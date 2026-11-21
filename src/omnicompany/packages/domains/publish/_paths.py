# [OMNI] origin=ai-ide domain=publish ts=2026-06-15T00:00:00Z type=paths status=active
# [OMNI] summary="publish domain 产物路径单点真源。各发布目标的 git 暂存克隆 + 每次运行的清单/diff。"
# [OMNI] why="路径散落易漂移; 单点定义, routers/snapshot 共用。"
# [OMNI] tags=publish,paths,backup
"""publish domain 产物路径(单点真源)。"""

from __future__ import annotations

from pathlib import Path

# publish/_paths.py → parents: publish[0]/domains[1]/packages[2]/omnicompany[3]/src[4]/仓根[5]
_OMNI_ROOT = Path(__file__).resolve().parents[5]

DATA_ROOT = _OMNI_ROOT / "data" / "domains" / "publish"
STAGING_ROOT = DATA_ROOT / "staging"   # 各发布目标的 git 工作克隆(暂存树, 跟远端分支对齐)
RUNS_ROOT = DATA_ROOT / "runs"         # 每次运行的清单 + diff 留痕


def ensure_dirs() -> None:
    for p in (STAGING_ROOT, RUNS_ROOT):
        p.mkdir(parents=True, exist_ok=True)
