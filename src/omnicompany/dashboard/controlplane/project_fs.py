# [OMNI] origin=claude-code domain=omnicompany/dashboard ts=2026-07-06 type=infra status=active
# [OMNI] summary="项目文件目录树 API: /api/projects/{id}/fs 懒加载列根/列一层子项(带 material 身份与 [OMNI] 头概要), /fs/detail 单文件详情。"
# [OMNI] why="2026-07-06 用户: 项目页「文件」页签要从 roots 快捷方式列表升级成真目录树(可切换看所有项目目录、突出本项目、旁注 material/功能注释、单击开详情)。此前 dashboard 无任何列目录 API; 懒加载(每请求一层)扛住 quant-lab/webworks 这类几万文件目录, 越权防护用「已注册项目 roots 白名单」而非仓根前缀(项目目录本就跨盘跨仓)。"
# [OMNI] tags=dashboard,controlplane,filetree,projects,material
"""controlplane/project_fs.py — 项目详情页「文件」目录树 API(只读)。

三个端点:
- GET /api/projects/{id}/fs            → 树根列表(all=0 只列本项目 roots; all=1 列全部注册项目 roots, related 标本项目)
- GET /api/projects/{id}/fs?path=<dir> → 该目录一层子项(dirs 优先), 每项带 related/material/omni 概要
- GET /api/projects/{id}/fs/detail?path=<p> → 单文件/目录详情([OMNI] 头全字段 + material 注册身份 + registry 实体)

注解三源(独立拼接, 目前无统一封装):
- material: boss_sight.material_registry 的 path 字段(全量建 path→item 索引, 60s 缓存)
- [OMNI] 头: core.omnimark.parse_omnimark(前 30 行), 只对代码/文档类扩展名解析, 每层列表限额防慢
- registry 实体: services._core.registry 的 source_file(omnicompany 仓相对路径), 仅 detail 时查
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from omnicompany.core.projects_registry import list_projects, parse_index_file

project_fs_router = APIRouter(tags=["project-fs"])

# [OMNI] 头值得一试的扩展名(与 omnimark 规范覆盖面一致); 其他类型不读文件
_OMNI_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".rs", ".md", ".ps1", ".sh",
              ".vbs", ".yaml", ".yml", ".html", ".css", ".toml", ".cmd", ".bat"}
_MAX_CHILDREN = 500          # 单层列表上限(node_modules 之类), 超出置 truncated
_MAX_OMNI_PARSE = 150        # 单层最多解析多少个文件头(每个是一次小文件读)
_OS_JUNK = {"$recycle.bin", "system volume information"}

_LOCK = threading.Lock()
_MAT_CACHE: dict[str, Any] = {"ts": 0.0, "map": None}
_ROOTS_CACHE: dict[str, Any] = {"ts": 0.0, "allowed": None}
_TTL_S = 60.0


def _cheap_norm(p: str | Path) -> str:
    """比较键: normpath + 小写 + 正斜杠(不 resolve —— 大批量路径逐个 resolve 太慢)。"""
    return os.path.normpath(str(p)).replace("\\", "/").rstrip("/").lower()


def _is_within(child_norm: str, root_norm: str) -> bool:
    return child_norm == root_norm or child_norm.startswith(root_norm + "/")


def _index_roots(proj: dict) -> list[dict[str, Any]]:
    """本项目声明的目录: index frontmatter roots({path,note} 或纯字符串) + 注册表 roots 兜底补充。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(path: str, note: str | None) -> None:
        if not path:
            return
        key = _cheap_norm(path)
        if key in seen:
            return
        seen.add(key)
        if Path(path).is_dir():
            out.append({"path": str(Path(path)), "note": note})

    fm: dict[str, Any] = {}
    idx = proj.get("index_path")
    if idx:
        try:
            fm = (parse_index_file(idx) or {}).get("data") or {}
        except Exception:  # noqa: BLE001
            fm = {}
    for x in (fm.get("roots") or []):
        if isinstance(x, dict) and x.get("path"):
            _add(str(x["path"]), (str(x["note"]) if x.get("note") else None))
        elif isinstance(x, str):
            _add(x, None)
    for r in (proj.get("roots") or []):
        if isinstance(r, str):
            _add(r, None)
    return out


def _allowed_roots() -> list[str]:
    """越权白名单: 全部注册项目的 roots + index 所在目录(60s 缓存)。"""
    now = time.time()
    with _LOCK:
        if _ROOTS_CACHE["allowed"] is not None and now - _ROOTS_CACHE["ts"] < _TTL_S:
            return _ROOTS_CACHE["allowed"]
    allowed: set[str] = set()
    for p in list_projects():
        for r in (p.get("roots") or []):
            if isinstance(r, str) and r.strip():
                allowed.add(_cheap_norm(r))
        idx = p.get("index_path")
        if idx:
            allowed.add(_cheap_norm(Path(idx).parent))
    out = sorted(allowed)
    with _LOCK:
        _ROOTS_CACHE["ts"] = now
        _ROOTS_CACHE["allowed"] = out
    return out


def _check_path_allowed(raw: str, extra_roots: list[str]) -> Path:
    """把不可信 path 参数 resolve 后按白名单校验(防 .. 穿越); 返回 resolve 后的 Path。"""
    try:
        p = Path(raw).resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="非法路径")
    norm = _cheap_norm(p)
    for root in [*_allowed_roots(), *extra_roots]:
        if _is_within(norm, root):
            return p
    raise HTTPException(status_code=403, detail="路径不在任何已注册项目目录内")


def _material_map() -> dict[str, dict[str, Any]]:
    """path(归一) → material 概要。全量遍历 material 注册表建索引, 60s 缓存。"""
    now = time.time()
    with _LOCK:
        if _MAT_CACHE["map"] is not None and now - _MAT_CACHE["ts"] < _TTL_S:
            return _MAT_CACHE["map"]
    m: dict[str, dict[str, Any]] = {}
    try:
        from omnicompany.dashboard.boss_sight.material_registry import (
            _gather_registry_items, _is_deleted, _workspace_root,
        )
        root = _workspace_root(None)
        by_dir: dict[str, list[dict[str, Any]]] = {}
        for it in _gather_registry_items(root):
            if not it.path or _is_deleted(it):
                continue
            ap = Path(it.path)
            if not ap.is_absolute():
                ap = root / it.path
            norm = _cheap_norm(ap)
            entry = {"id": it.id, "title": it.title, "kind": it.kind, "status": it.status}
            m[norm] = entry
            parent = norm.rsplit("/", 1)[0] if "/" in norm else ""
            if parent:
                by_dir.setdefault(parent, []).append(entry)
        # 上卷: 目录内恰好一个 material(典型=计划文件夹里的 plan.md)时, 目录本身也顶这个身份
        # (树里先看到的是文件夹; 多个 material 的目录不标, 避免歧义)。
        for parent, entries in by_dir.items():
            if len(entries) == 1 and parent not in m:
                m[parent] = entries[0]
    except Exception:  # noqa: BLE001 — 注册表坏了不拖垮文件树
        pass
    with _LOCK:
        _MAT_CACHE["ts"] = now
        _MAT_CACHE["map"] = m
    return m


def _omni_brief(p: Path) -> dict[str, Any] | None:
    """[OMNI] 头概要(列表旁注用): summary/type/status。无头返回 None。"""
    try:
        from omnicompany.core.omnimark import parse_omnimark
        f = parse_omnimark(p)
    except Exception:  # noqa: BLE001
        return None
    if f is None or not (f.summary or f.type):
        return None
    return {"summary": f.summary or "", "type": f.type or "", "status": f.status or ""}


@project_fs_router.get("/projects/{project_id}/fs")
def project_fs(project_id: str, path: str | None = None, all: bool = False) -> dict[str, Any]:
    """无 path=列树根; 有 path=列该目录一层子项(懒加载)。all=1 树根扩到全部注册项目。"""
    projects = list_projects()
    proj = next((p for p in projects if p.get("id") == project_id), None)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"未注册的项目: {project_id}")
    own = _index_roots(proj)
    own_norms = [_cheap_norm(x["path"]) for x in own]

    if not path:
        if not all:
            roots = [{"name": Path(x["path"]).name or x["path"], "path": x["path"], "dir": True,
                      "related": True, "note": x.get("note"), "projects": [project_id]} for x in own]
        else:
            merged: dict[str, dict[str, Any]] = {}
            for p in projects:
                pid = p.get("id") or "?"
                for x in _index_roots(p):
                    key = _cheap_norm(x["path"])
                    ent = merged.setdefault(key, {
                        "name": Path(x["path"]).name or x["path"], "path": x["path"], "dir": True,
                        "related": key in own_norms or any(_is_within(key, r) or _is_within(r, key) for r in own_norms),
                        "note": None, "projects": [],
                    })
                    if pid not in ent["projects"]:
                        ent["projects"].append(pid)
                    if pid == project_id and x.get("note"):
                        ent["note"] = x["note"]
            roots = sorted(merged.values(), key=lambda r: (not r["related"], r["path"].lower()))
        return {"project": project_id, "roots": roots}

    target = _check_path_allowed(path, own_norms)
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="path 不是目录")
    try:
        entries = sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"读目录失败: {e}")
    entries = [e for e in entries if e.name.lower() not in _OS_JUNK]
    truncated = len(entries) > _MAX_CHILDREN
    entries = entries[:_MAX_CHILDREN]

    mat = _material_map()
    items: list[dict[str, Any]] = []
    parsed = 0
    for e in entries:
        is_dir = e.is_dir()
        norm = _cheap_norm(e)
        item: dict[str, Any] = {
            "name": e.name,
            "path": str(e),
            "dir": is_dir,
            "related": any(_is_within(norm, r) for r in own_norms),
        }
        if norm in mat:
            item["material"] = mat[norm]
        if not is_dir and parsed < _MAX_OMNI_PARSE and e.suffix.lower() in _OMNI_EXTS:
            parsed += 1
            brief = _omni_brief(e)
            if brief:
                item["omni"] = brief
        items.append(item)
    return {"project": project_id, "path": str(target), "items": items, "truncated": truncated}


@project_fs_router.get("/projects/{project_id}/fs/detail")
def project_fs_detail(project_id: str, path: str) -> dict[str, Any]:
    """单个文件/目录的完整注解: [OMNI] 头全字段 + material 身份 + registry 实体(source_file 反查)。"""
    projects = list_projects()
    proj = next((p for p in projects if p.get("id") == project_id), None)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"未注册的项目: {project_id}")
    own_norms = [_cheap_norm(x["path"]) for x in _index_roots(proj)]
    p = _check_path_allowed(path, own_norms)
    if not p.exists():
        raise HTTPException(status_code=404, detail="路径不存在")

    out: dict[str, Any] = {"project": project_id, "path": str(p), "dir": p.is_dir()}
    try:
        st = p.stat()
        out["size"] = st.st_size
        out["mtime"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))
    except OSError:
        pass

    if p.is_file() and p.suffix.lower() in _OMNI_EXTS:
        try:
            from omnicompany.core.omnimark import parse_omnimark
            f = parse_omnimark(p)
        except Exception:  # noqa: BLE001
            f = None
        if f is not None:
            out["omni"] = {
                "origin": f.origin, "ts": f.ts, "type": f.type, "status": f.status,
                "domain": f.domain, "summary": f.summary, "why": f.why, "tags": list(f.tags),
                "material_id": (f.extra or {}).get("material_id"),
            }

    norm = _cheap_norm(p)
    m = _material_map().get(norm)
    if m:
        out["material"] = m

    # registry 实体(Format/Router 等)按 source_file 反查 —— source_file 是 omnicompany 仓相对路径
    try:
        from omnicompany.core.config import omni_workspace_root
        from omnicompany.packages.services._core.registry import get_registry, query as reg_query
        repo = _cheap_norm(omni_workspace_root())
        if _is_within(norm, repo):
            rel = norm[len(repo):].lstrip("/")
            hits = []
            for entry in reg_query(get_registry()).execute():
                sf = (entry.source_file or "").replace("\\", "/").lstrip("/").lower()
                if sf == rel:
                    hits.append({"entity_id": entry.entity_id, "type": entry.type,
                                 "name": entry.name, "package": entry.package})
                    if len(hits) >= 10:
                        break
            if hits:
                out["instances"] = hits
    except Exception:  # noqa: BLE001
        pass
    return out
