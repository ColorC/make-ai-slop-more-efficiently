# [OMNI] origin=claude-code domain=services/_governance ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="语义空间健康治理共享抑制名单/baseline。对'反复被提示但人已判定可接受'的点建抑制名单,进度/语言两轨共用;定时跑不再刷同一批旧噪声。"
# [OMNI] why="定时巡检反模式=每次刷一样的旧噪声(调研点名)。lint 的 inline ignore / baseline 思路:人确认可接受的点入抑制名单, 后续跳过。"
# [OMNI] tags=governance,suppress,baseline,semantic-space-health
# [OMNI] material_id="material:governance.health_suppress.py"
"""共享抑制名单(两轨共用)。key 形如 'doc:line:category' 或 'token:<x>'。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from omnicompany.core.config import omni_workspace_root


def _path(facility: str, root: Path | None = None) -> Path:
    d = (root or omni_workspace_root()) / "data" / "governance" / facility
    d.mkdir(parents=True, exist_ok=True)
    return d / "suppress.json"


def load_suppressions(facility: str, root: Path | None = None) -> dict[str, dict]:
    p = _path(facility, root)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def is_suppressed(facility: str, key: str, root: Path | None = None) -> bool:
    return key in load_suppressions(facility, root)


def add_suppression(facility: str, key: str, note: str = "", root: Path | None = None) -> dict:
    data = load_suppressions(facility, root)
    data[key] = {"note": note, "at": datetime.now(timezone.utc).isoformat()}
    _path(facility, root).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "key": key, "total": len(data)}


def remove_suppression(facility: str, key: str, root: Path | None = None) -> dict:
    data = load_suppressions(facility, root)
    existed = data.pop(key, None) is not None
    _path(facility, root).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "removed": existed, "total": len(data)}
