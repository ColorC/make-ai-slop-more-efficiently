# [OMNI] origin=claude-code domain=project_atlas ts=2026-06-21 type=paths status=active
# [OMNI] summary="project_atlas 产物路径单点真源。staging(AI 起草待人审)/skills(批准 canonical)/runs/名录草稿。"
# [OMNI] why="路径散落易漂移;单点定义,routers 共用。AI 产出先进 staging,人审批准才进 canonical(护人工 grep 唯一可信)。"
# [OMNI] tags=project_atlas,paths
"""project_atlas domain 产物路径(单点真源)。"""

from __future__ import annotations

from pathlib import Path

# project_atlas/_paths.py → parents: project_atlas[0]/domains[1]/packages[2]/omnicompany[3]/src[4]/仓根[5]
_OMNI_ROOT = Path(__file__).resolve().parents[5]

DATA_ROOT = _OMNI_ROOT / "data" / "domains" / "project_atlas"
RUNS_ROOT = DATA_ROOT / "runs"            # 单次收集中间态(clues/classified/report)
STAGING_ROOT = DATA_ROOT / "staging"      # ⭐AI 起草的 object-SKILL,待人审(不直进 canonical)
SKILLS_ROOT = DATA_ROOT / "skills"        # ⭐人审批准后的 canonical object-SKILL(export 源)
PLAN_DIR = DATA_ROOT / "plan"             # 对象清单 <space>.objects.json —— 断点续跑的真源(enumerate 一次, 反复消费)
ATLAS_PATH = DATA_ROOT / "atlas.jsonl"    # 项目速览名录草稿(并入 omni project 前的暂存)


def repo_root() -> Path:
    return _OMNI_ROOT


def ensure_dirs() -> None:
    for p in (RUNS_ROOT, STAGING_ROOT, SKILLS_ROOT, PLAN_DIR):
        p.mkdir(parents=True, exist_ok=True)
