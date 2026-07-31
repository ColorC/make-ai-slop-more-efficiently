# [OMNI] origin=human domain=services/_utility type=module agent=ai-ide-2b20d28d ts=2026-07-26T09:07:27Z
# [OMNI] summary="Stable repository-path discovery for runtime-test-builder."
# [OMNI] why="_utility/runtime_test 测试设施组件"
# [OMNI] tags=_utility,runtime_test,builder,module
"""Stable repository-path discovery for runtime-test-builder."""

from __future__ import annotations

from pathlib import Path


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    candidates = [current.parent, *current.parents]
    for candidate in candidates:
        if (
            (candidate / "pyproject.toml").is_file()
            and (candidate / "src" / "omnicompany").is_dir()
        ):
            return candidate
    raise RuntimeError(f"cannot locate Omnicompany project root from {current}")


PROJECT_ROOT = find_project_root()


__all__ = ["PROJECT_ROOT", "find_project_root"]
