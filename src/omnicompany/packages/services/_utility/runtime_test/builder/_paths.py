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
