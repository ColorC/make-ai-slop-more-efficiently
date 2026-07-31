# [OMNI] origin=claude-code domain=omnicompany/knowledge ts=2026-04-21T00:00:00Z type=config
# [OMNI] material_id="material:learning.knowledge.worker_helpers.project_root_locator.py"
"""knowledge Workers 共享工具 (Stage 3).

提供 _project_root() 定位 omnicompany 仓库根的工具, 所有 KB Worker 共享。
"""
from __future__ import annotations

from pathlib import Path


def _project_root() -> Path:
    """从当前文件位置向上定位到 omnicompany 仓库根。

    本文件位于 <root>/src/omnicompany/packages/services/knowledge/workers/_shared.py
    所以 parents[6] = <root>
    """
    return Path(__file__).resolve().parents[6]
