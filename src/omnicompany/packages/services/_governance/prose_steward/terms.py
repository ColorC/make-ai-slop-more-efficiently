# [OMNI] origin=claude-code domain=services/_governance/prose_steward ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="语言治理单一真源加载器。读 docs/standards/prose_terms.yaml, 供 lang/term/compress 三检查器与 Vale/CSpell 规则生成统一取数, 别在多处各写术语表。"
# [OMNI] why="术语表散落多处必漂移;单一真源 + 全派生是 SSOT 的工程实现。"
# [OMNI] tags=governance,terminology,loader,single-source-of-truth
# [OMNI] material_id="material:governance.prose_steward.terms.py"
"""prose_terms.yaml 单一真源加载器(进程内缓存)。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root


def terms_path(root: Path | None = None) -> Path:
    base = root or omni_workspace_root()
    return base / "docs" / "standards" / "prose_terms.yaml"


@lru_cache(maxsize=4)
def _load(path_str: str) -> dict[str, Any]:
    import yaml
    p = Path(path_str)
    if not p.is_file():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def load_terms(root: Path | None = None) -> dict[str, Any]:
    return _load(str(terms_path(root)))


def english_whitelist(root: Path | None = None) -> set[str]:
    """允许保留英文的 token(小写)。"""
    data = load_terms(root)
    return {str(w).lower() for w in (data.get("english_whitelist") or [])}


def forbidden_aliases(root: Path | None = None) -> dict[str, str]:
    """en(小写) → 建议中文。"""
    out: dict[str, str] = {}
    for item in load_terms(root).get("forbidden_aliases") or []:
        if isinstance(item, dict) and item.get("en"):
            out[str(item["en"]).lower()] = str(item.get("zh", ""))
    return out


def term_consistency(root: Path | None = None) -> list[dict[str, Any]]:
    return list(load_terms(root).get("term_consistency") or [])


def easily_outdated(root: Path | None = None) -> list[dict[str, Any]]:
    return list(load_terms(root).get("easily_outdated") or [])


def abbrev_expansions(root: Path | None = None) -> list[dict[str, Any]]:
    return list(load_terms(root).get("abbrev_expansions") or [])
