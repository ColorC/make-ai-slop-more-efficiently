# [OMNI] origin=claude-code domain=frontend_design ts=2026-07-01T00:00:00Z type=paths status=design
# [OMNI] summary="frontend_design domain 产物路径单点真源。runs/rulers/reviews/reports 的根。"
# [OMNI] why="路径散落易漂移; 单点定义, routers 共用。Guardian 揪散落文件。"
# [OMNI] tags=frontend_design,paths
"""frontend_design domain 产物路径(单点真源)。"""

from __future__ import annotations

from pathlib import Path

# frontend_design/_paths.py → parents: frontend_design[0]/domains[1]/packages[2]/omnicompany[3]/src[4]/仓根[5]
_OMNI_ROOT = Path(__file__).resolve().parents[5]

DATA_ROOT = _OMNI_ROOT / "data" / "domains" / "frontend_design"
RUNS_ROOT = DATA_ROOT / "runs"        # 单次审查的截图/DOM快照/门禁结果/评审证据
RULERS_ROOT = DATA_ROOT / "rulers"    # 标尺快照(从外部真源固化下来的可判定项清单)
REVIEWS_ROOT = DATA_ROOT / "reviews"  # 评审记录(证据列表, 不打分)
REPORTS_ROOT = DATA_ROOT / "reports"  # 汇总改进建议 markdown


def ensure_dirs() -> None:
    for p in (RUNS_ROOT, RULERS_ROOT, REVIEWS_ROOT, REPORTS_ROOT):
        p.mkdir(parents=True, exist_ok=True)
