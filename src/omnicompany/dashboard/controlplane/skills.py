# [OMNI] origin=claude-code domain=omnicompany/dashboard ts=2026-07-06 type=infra status=active
# [OMNI] summary="GET /api/skills —— atlas object-SKILL(canonical+staging)与 omni run 管线注册表的合并清单, 项目详情页「技能」页签数据源。"
# [OMNI] why="2026-07-06 用户: 项目页低频的常用工作选项删掉, 换成集合技能与管线、链接 atlas 的「技能」页签; atlas/pipelines 此前只有 CLI 入口无任何 HTTP API, 这层薄路由补上(只读, 复用 atlas._iter_objects 与 core.registry.list_all)。"
# [OMNI] tags=dashboard,controlplane,skills,atlas,pipelines
"""controlplane/skills.py — 技能(atlas)+管线(omni run 注册表)清单 API。

数据源(都是只读消费, 权威不在这):
- 技能: data/domains/project_atlas/{skills,staging}/<space>/<obj>/SKILL.md
  (canonical=已人审批准, staging=待审; 同名以 canonical 为准)
- 管线: omnicompany.core.registry discover()+list_all()(与 `omni pipelines` 同源)
- spaces: project_atlas.spaces.SPACES —— 前端拿它对项目 roots 做"本项目空间"标注

discover() 首次调用要把 core/pipelines.py 的懒注册块全过一遍(秒级), 故整份响应
带 60s TTL 缓存; fresh=1 穿透(与 /api/projects 的刷新语义一致)。
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter

skills_router = APIRouter(tags=["skills"])

_TTL_S = 60.0
_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _skill_frontmatter(path: Path) -> dict[str, Any]:
    """解析 SKILL.md frontmatter(字段只有 name/description, description 常用 >- 折叠块, 需真 YAML)。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    m = _FM_RE.match(text)
    if not m:
        return {}
    try:
        import yaml
        data = yaml.safe_load(m.group(1)) or {}
    except Exception:  # noqa: BLE001 — 单个坏 frontmatter 不拖垮整份清单
        return {}
    return data if isinstance(data, dict) else {}


def _collect() -> dict[str, Any]:
    from omnicompany.cli.commands.atlas import _iter_objects
    from omnicompany.packages.domains.project_atlas._paths import SKILLS_ROOT, STAGING_ROOT
    from omnicompany.packages.domains.project_atlas.spaces import SPACES

    skills: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    # canonical 优先; staging 里同名(=旧副本/待重审)不重复列
    for status, root in (("canonical", SKILLS_ROOT), ("staging", STAGING_ROOT)):
        for space, name, sk in _iter_objects(root):
            if (space, name) in seen:
                continue
            seen.add((space, name))
            fm = _skill_frontmatter(sk)
            skills.append({
                "space": space,
                "name": name,
                "status": status,
                "description": str(fm.get("description") or "").strip(),
                "path": str(sk),
            })

    pipelines: list[dict[str, Any]] = []
    try:
        from omnicompany.core import registry as preg
        preg.discover()
        for e in preg.list_all():
            pipelines.append({
                "name": e.name,
                "domain": e.domain,
                "description": e.description,
                "aliases": list(e.aliases or ()),
            })
    except Exception:  # noqa: BLE001 — 管线注册链路出问题时技能部分仍可用
        pass

    spaces = {k: {"root": str(v.get("root") or ""), "group": v.get("group")} for k, v in SPACES.items()}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skills": skills,
        "pipelines": pipelines,
        "spaces": spaces,
    }


@skills_router.get("/skills")
def get_skills(fresh: bool = False) -> dict[str, Any]:
    """技能 + 管线全量清单(全局, 无项目过滤 —— 项目相关性由前端按 spaces.root 对 roots 判定)。"""
    now = time.time()
    with _LOCK:
        cached = _CACHE["data"]
        if not fresh and cached is not None and now - _CACHE["ts"] < _TTL_S:
            return cached
    data = _collect()
    with _LOCK:
        _CACHE["ts"] = now
        _CACHE["data"] = data
    return data
