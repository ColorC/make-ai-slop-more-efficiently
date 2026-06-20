# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-20T00:00:00Z type=helper status=active
# [OMNI] summary="slidecast 路径常量: runs/(每次产物) + _studio/(共用 slidev node_modules)。产物在 data/domains/slidecast。"
# [OMNI] why="边界: 管线代码在域, 产物/运行态在 data/domains/slidecast(gitignore)。集中路径一处。"
# [OMNI] tags=slidecast,paths,helper
"""slidecast 路径常量。"""

from __future__ import annotations

from pathlib import Path

from omnicompany.core.config import omni_workspace_root


def root() -> Path:
    return omni_workspace_root() / "data" / "domains" / "slidecast"


def runs_root() -> Path:
    return root() / "runs"


def studio_root() -> Path:
    """共用 slidev 工程(node_modules 装一次, 各 run 复用)。"""
    return root() / "_studio"


def ensure_dirs() -> None:
    runs_root().mkdir(parents=True, exist_ok=True)
