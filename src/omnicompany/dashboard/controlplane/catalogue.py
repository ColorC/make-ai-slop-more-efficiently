# [OMNI] origin=claude-code ts=2026-05-01 type=infra
# [OMNI] material_id="material:dashboard.catalogue_api.team_material_scanner.py"
"""Teams + Materials catalogue API.

Both Teams and Materials are scattered Python files under packages/.
- Team: `team*.py` (49 files) — contain TeamSpec builders
- Material: `materials.py` + `formats.py` (~63 files) — contain Format/Material defs

For each: id = relative path under packages/ without .py.
"""

from __future__ import annotations

import ast
import difflib
import glob
import hashlib
import importlib
import inspect
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from fastapi import APIRouter, HTTPException, Query

from ._db_helpers import discover_event_dbs, safe_conn

catalogue_router = APIRouter()

SKIP_PARTS = {"__pycache__", "node_modules", "_archive", "_graveyard", ".git"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _packages_root() -> Path:
    return Path(__file__).resolve().parents[2] / "packages"


def _is_skipped(p: Path) -> bool:
    return any(part in SKIP_PARTS for part in p.parts)


def _resolve_catalogue_source(raw_path: Any) -> Path | None:
    if not raw_path:
        return None
    raw = Path(str(raw_path))
    path = raw if raw.is_absolute() else _repo_root() / raw
    try:
        resolved = path.resolve()
        resolved.relative_to(_repo_root().resolve())
    except (OSError, ValueError):
        return None
    if _is_skipped(resolved):
        return None
    return resolved if resolved.is_file() else None


def _scan_files(patterns: list[str]) -> list[dict[str, Any]]:
    root = _packages_root()
    items: list[dict[str, Any]] = []
    if not root.is_dir():
        return items
    seen = set()
    for pattern in patterns:
        for path in root.rglob(pattern):
            if path.name == "__init__.py":
                continue
            rel = path.relative_to(root)
            if _is_skipped(rel):
                continue
            if path.name.startswith("team") and "workers" in rel.parts:
                continue
            if path.name.startswith("team"):
                try:
                    source_probe = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                if "TeamSpec" not in source_probe or "build_" not in source_probe:
                    continue
            rel_str = str(rel.with_suffix("")).replace(os.sep, "/")
            if rel_str in seen:
                continue
            seen.add(rel_str)
            try:
                stat = path.stat()
            except OSError:
                continue
            pkg_dir = path.parent
            design_md = pkg_dir / "DESIGN.md"
            items.append({
                "id": rel_str,
                "name": path.stem,
                "package": str(pkg_dir.relative_to(root)).replace(os.sep, "/"),
                "file_path": str(path),
                "size": stat.st_size,
                "has_design_md": design_md.is_file(),
                "mtime": stat.st_mtime,  # 文件修改时间, 给"最近 team"看板排序用
            })
    items.sort(key=lambda x: x["id"])
    return items


# 2026-05-02: B3 切 G2 优先 — G2 注册中心是主要 source, file_glob 兜底
# 合并来源:
#   - registry (主): G2 InstanceRegistry (含 explicit `omni register` + ast_scan)
#   - file_glob (兜底): 找 .py 文件用于 G2 没有的兜底, 跟补 size / has_design_md 字段
# 列表顺序: 先列 registry 全 entries (含 trace_id), 再附 file_glob 找到但 registry 没的


def _g2_first_catalogue(file_glob_patterns: list[str], registry_kind: str) -> list[dict[str, Any]]:
    """G2 优先 catalogue 视图 (B3 切完 G2 后默认走这条).

    流程:
      1. 查 G2 type=<registry_kind> 全 entries → 列出, 含 trace_id / first_seen_at / registered_via
      2. 跑 file_glob 兜底 → 拿 size / has_design_md 字段补 G2 已有 entries
      3. 加 file_glob 找到但 G2 没的 (registered_via=file_glob_only, 标 G2 缺口)
    """
    items: list[dict[str, Any]] = []
    g2_keys: set[tuple[str, str]] = set()

    # ── 1. G2 主 (优先) ──
    try:
        from omnicompany.packages.services._core.registry import get_registry, query as reg_query
        reg = get_registry()
        for entry in reg_query(reg).type(registry_kind).execute():
            source_path = _resolve_catalogue_source(entry.source_file)
            if registry_kind == "pipeline" and source_path is None:
                continue
            items.append({
                "id": entry.entity_id,
                "name": entry.name,
                "package": entry.package,
                "file_path": str(source_path) if source_path is not None else entry.source_file,
                "size": 0,                                  # 后由 file_glob 补
                "has_design_md": False,                     # 后由 file_glob 补
                "mtime": 0.0,                               # 后由 file_glob 补
                "trace_id": entry.attrs.get("trace_id"),
                "first_seen_at": entry.first_seen_at,
                "registered_via": entry.attrs.get("registered_via", "g2_explicit"),
            })
            g2_keys.add((entry.package, entry.name))
    except ImportError:
        pass
    except Exception:
        pass  # G2 查失败不阻塞, 走 fallback

    # ── 2. file_glob 兜底 (补字段 + 加缺漏) ──
    file_items = _scan_files(file_glob_patterns)
    for fi in file_items:
        key = (fi["package"], fi["name"])
        if key in g2_keys:
            # 已在 G2 — 补 size / has_design_md 字段 (G2 不存这两字段)
            for it in items:
                if (it["package"], it["name"]) == key:
                    it["size"] = fi["size"]
                    it["has_design_md"] = fi["has_design_md"]
                    it["mtime"] = fi["mtime"]
                    break
        else:
            # G2 缺口 (file_glob 找到, G2 没注册 — 标记给 dogfood 看)
            items.append({
                **fi,
                "trace_id": None,
                "first_seen_at": None,
                "registered_via": "file_glob_only",  # 提示这条还没进 G2
            })

    items.sort(key=lambda x: x.get("id", ""))
    return items


@lru_cache(maxsize=1)
def _scan_teams_cached(token: float) -> list[dict[str, Any]]:
    return _g2_first_catalogue(["team*.py"], registry_kind="pipeline")


@lru_cache(maxsize=1)
def _scan_materials_cached(token: float) -> list[dict[str, Any]]:
    # `formats.py` and `materials.py` are conceptually the same in this codebase
    # (the protocol module aliases `Format as Material`). Group together.
    return _g2_first_catalogue(["materials.py", "formats.py"], registry_kind="format")


def _root_token() -> float:
    root = _packages_root()
    return root.stat().st_mtime if root.exists() else 0.0


def _get_one(scan_fn, kind: str, item_id: str) -> dict[str, Any]:
    root = _packages_root()
    py_path = _resolve_item_path(scan_fn, kind, item_id)

    pkg_dir = py_path.parent
    design_md_path = pkg_dir / "DESIGN.md"
    design_md = design_md_path.read_text(encoding="utf-8") if design_md_path.is_file() else None

    try:
        source = py_path.read_text(encoding="utf-8")
    except OSError:
        source = ""

    return {
        "id": item_id,
        "name": py_path.stem,
        "package": str(pkg_dir.relative_to(root)).replace(os.sep, "/"),
        "file_path": str(py_path),
        "design_md_path": str(design_md_path) if design_md else None,
        "design_md": design_md,
        "source": source,
    }


# ── Teams ───────────────────────────────────────────────────────────────


def _resolve_item_path(scan_fn, kind: str, item_id: str) -> Path:
    root = _packages_root()
    candidate = root / (item_id + ".py")
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail=f"invalid {kind} id (path escape)")
    if resolved.is_file():
        return resolved

    items = scan_fn(_root_token())
    item = next((it for it in items if it.get("id") == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail=f"{kind} not found: {item_id}")

    raw_path = item.get("file_path") or item.get("source_file")
    if not raw_path:
        raise HTTPException(status_code=404, detail=f"{kind} has no source file: {item_id}")

    raw = Path(str(raw_path))
    path = raw if raw.is_absolute() else _repo_root() / raw
    resolved = path.resolve()
    try:
        resolved.relative_to(_repo_root().resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid {kind} source (outside repo)")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"{kind} source not found: {item_id}")
    return resolved


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(_enum_value(v)) for v in value]
    return [str(_enum_value(value))]


def _safe_text(value: Any, limit: int = 320) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _module_name_for_path(path: Path) -> str:
    packages_root = _packages_root().resolve()
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(packages_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="team source is not under src/omnicompany/packages")
    if resolved.suffix != ".py":
        raise HTTPException(status_code=400, detail="team source is not a python file")
    return "omnicompany.packages." + ".".join(rel.with_suffix("").parts)


def _looks_like_team_spec(value: Any) -> bool:
    return (
        value is not None
        and isinstance(getattr(value, "nodes", None), list)
        and isinstance(getattr(value, "edges", None), list)
        and isinstance(getattr(value, "entry", None), str)
    )


def _discover_team_builders(module: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    builders: list[dict[str, Any]] = []
    errors: dict[str, Any] = {}

    for name, fn in inspect.getmembers(module, inspect.isfunction):
        if not name.startswith("build"):
            continue
        if getattr(fn, "__module__", None) != module.__name__:
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        required = [
            param for param in sig.parameters.values()
            if param.default is inspect._empty
            and param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
        ]
        if required:
            continue
        try:
            spec = fn()
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"
            continue
        if not _looks_like_team_spec(spec):
            continue
        builders.append({
            "name": name,
            "spec": spec,
            "spec_id": str(getattr(spec, "id", "")),
            "nodes": len(getattr(spec, "nodes", [])),
            "edges": len(getattr(spec, "edges", [])),
            "entry": getattr(spec, "entry", None),
        })

    builders.sort(key=lambda item: item["name"])
    return builders, errors


def _select_builder(builders: list[dict[str, Any]], requested: str | None) -> dict[str, Any]:
    if requested:
        found = next((item for item in builders if item["name"] == requested), None)
        if not found:
            raise HTTPException(status_code=404, detail=f"builder not found: {requested}")
        return found
    for preferred in ("build_team_agent_first", "build_team"):
        found = next((item for item in builders if item["name"] == preferred), None)
        if found:
            return found
    return builders[0]


def _node_formats(node: Any) -> tuple[list[str], str | None]:
    try:
        fmt_in = _as_list(node.format_in)
    except Exception:
        anchor = getattr(node, "anchor", None)
        transformer = getattr(node, "transformer", None)
        if anchor is not None:
            fmt_in = _as_list(getattr(anchor, "format_in", None))
        elif transformer is not None:
            fmt_in = _as_list(getattr(transformer, "from_format", None))
        else:
            fmt_in = []

    try:
        fmt_out = str(_enum_value(node.format_out))
    except Exception:
        anchor = getattr(node, "anchor", None)
        transformer = getattr(node, "transformer", None)
        if anchor is not None:
            fmt_out = str(_enum_value(getattr(anchor, "format_out", "")))
        elif transformer is not None:
            fmt_out = str(_enum_value(getattr(transformer, "to_format", "")))
        else:
            fmt_out = None
    return fmt_in, fmt_out


def _serialize_node(node: Any, entry: str) -> dict[str, Any]:
    anchor = getattr(node, "anchor", None)
    transformer = getattr(node, "transformer", None)
    scatter = getattr(node, "scatter", None)
    fmt_in, fmt_out = _node_formats(node)

    label = getattr(node, "id", "")
    validator_kind = None
    validator_id = None
    method = None
    description = ""
    routes: list[dict[str, Any]] = []

    if anchor is not None:
        label = getattr(anchor, "name", label)
        validator = getattr(anchor, "validator", None)
        if validator is not None:
            validator_kind = _enum_value(getattr(validator, "kind", None))
            validator_id = getattr(validator, "id", None)
            description = getattr(validator, "description", "") or ""
        for verdict, route in getattr(anchor, "routes", {}).items():
            routes.append({
                "verdict": _enum_value(verdict),
                "action": _enum_value(getattr(route, "action", None)),
                "target": getattr(route, "target", None),
                "max_retries": getattr(route, "max_retries", None),
                "feedback": getattr(route, "feedback", None),
            })
    elif transformer is not None:
        label = getattr(transformer, "name", label)
        method = _enum_value(getattr(transformer, "method", None))
        description = getattr(transformer, "description", "") or ""
    elif scatter is not None:
        label = getattr(scatter, "name", label) or label
        description = f"scatter over {getattr(scatter, 'iterable_key', '')}"

    return {
        "id": getattr(node, "id", ""),
        "label": str(label),
        "kind": _enum_value(getattr(node, "kind", None)),
        "maturity": _enum_value(getattr(node, "maturity", None)),
        "maturity_score": getattr(node, "maturity_score", 0),
        "format_in": fmt_in,
        "format_out": fmt_out,
        "validator_kind": validator_kind,
        "validator_id": validator_id,
        "method": method,
        "description": _safe_text(description),
        "routes": routes,
        "is_entry": getattr(node, "id", None) == entry,
    }


def _to_snake_identifier(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "_", value or "").strip("_")
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"_+", "_", text).lower().strip("_")


def _to_camel_identifier(value: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^0-9A-Za-z]+", value or "") if part)


def _safe_literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None


def _source_excerpt(source: str, line_start: int | None, line_end: int | None, *, limit: int = 2600) -> str | None:
    if not line_start:
        return None
    lines = source.splitlines()
    end = min(line_end or line_start, len(lines))
    start = max(line_start - 1, 0)
    text = "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _schema_fields(schema: Any) -> list[dict[str, Any]]:
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = set(schema.get("required") or [])
    fields: list[dict[str, Any]] = []
    for name, spec in properties.items():
        spec_dict = spec if isinstance(spec, dict) else {}
        raw_type = spec_dict.get("type")
        if isinstance(raw_type, list):
            type_text = " | ".join(str(item) for item in raw_type)
        elif raw_type is None:
            type_text = None
        else:
            type_text = str(raw_type)
        fields.append({
            "name": str(name),
            "type": type_text,
            "description": _safe_text(spec_dict.get("description"), 220) if spec_dict else "",
            "required": name in required,
        })
    return fields


def _kind_from_tags(tags: Any, description: str | None = None) -> str | None:
    if isinstance(tags, list):
        for tag in tags:
            text = str(tag)
            if text.startswith("kind."):
                return text.removeprefix("kind.")
    match = re.search(r"\bkind[.:]([A-Za-z_][0-9A-Za-z_]*)\b", description or "")
    return match.group(1) if match else None


def _definition_ref(
    entity_type: str,
    entity_id: str,
    path: Path,
    pkg_dir: Path,
    label: str,
    *,
    symbol: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    summary: str | None = None,
    source_excerpt: str | None = None,
    material: dict[str, Any] | None = None,
    worker: dict[str, Any] | None = None,
) -> dict[str, Any]:
    design_md_path = pkg_dir / "DESIGN.md"
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "file_path": str(path),
        "design_md_path": str(design_md_path) if design_md_path.is_file() else None,
        "has_design_md": design_md_path.is_file(),
        "label": label,
        "symbol": symbol,
        "line_start": line_start,
        "line_end": line_end,
        "summary": summary,
        "source_excerpt": source_excerpt,
        "material": material,
        "worker": worker,
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# ── 源文件解析缓存 ────────────────────────────────────────────────────────
# team-graph 冷请求里同一个 formats.py 会被每个材料解析一遍、worker 文件按节点解析。
# 按 (路径, mtime) 缓存 ast.parse 结果: 文件没变就不重复 read+parse(parse 是 CPU 大头)。
_FILE_PARSE_CACHE: dict[str, tuple[float, str, "ast.Module | None"]] = {}


def _read_and_parse(path: Path) -> tuple[str, "ast.Module | None"]:
    """读源码 + ast.parse, 按 (路径, mtime) 缓存。文件改动(mtime 跳变)即重解析。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return "", None
    key = str(path)
    hit = _FILE_PARSE_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1], hit[2]
    source = _read_text(path)
    try:
        tree = ast.parse(source) if source else None
    except SyntaxError:
        tree = None
    _FILE_PARSE_CACHE[key] = (mtime, source, tree)
    return source, tree


def _class_attr_value(class_node: ast.ClassDef, attr_name: str) -> Any:
    for stmt in class_node.body:
        target: ast.AST | None = None
        value: ast.AST | None = None
        if isinstance(stmt, ast.Assign) and stmt.targets:
            target = stmt.targets[0]
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target = stmt.target
            value = stmt.value
        if isinstance(target, ast.Name) and target.id == attr_name:
            return _safe_literal(value)
    return None


def _ast_fixed_text(value: ast.AST | None, consts: dict[str, str]) -> str | None:
    """从 AST 取**固定文本**: 字符串字面量 / f-string(动态段以 {…} 占位) / 字符串相加 /
    指向模块级字符串常量的名字。取不到(纯运行时拼装)返回 None。

    满足用户(2026-06-20): 有固定 prompt 或固定模板 prompt(里面有动态拼接也要看固定部分)。
    """
    if value is None:
        return None
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.JoinedStr):
        parts: list[str] = []
        for part in value.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                parts.append(part.value)
            else:
                parts.append("{…}")  # 动态拼接段, 只标位置不展开
        return "".join(parts)
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        left = _ast_fixed_text(value.left, consts)
        right = _ast_fixed_text(value.right, consts)
        return (left + right) if (left is not None and right is not None) else None
    if isinstance(value, ast.Name) and value.id in consts:
        return consts[value.id]
    return None


def _module_str_consts(tree: ast.Module) -> dict[str, str]:
    """模块顶层的字符串常量(供 NODE_PROMPT = _SOME_CONST 这种间接引用解析)。"""
    consts: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            text = _ast_fixed_text(stmt.value, consts)
            if text is not None:
                consts[stmt.targets[0].id] = text
    return consts


# worker 类里承载固定 prompt 的类属性名(AgentNodeLoop.NODE_PROMPT 为主, 其余为常见别名)
_PROMPT_ATTRS = ("NODE_PROMPT", "PROMPT_TEMPLATE", "PROMPT", "SYSTEM_PROMPT", "TEMPLATE", "INSTRUCTION", "INSTRUCTIONS")


def _extract_fixed_prompt(tree: ast.Module | None, class_node: ast.ClassDef) -> dict[str, str] | None:
    """抽 worker 的固定 prompt(NODE_PROMPT 等类属性的字符串字面量/模板)。无则 None。"""
    if tree is None:
        return None
    consts = _module_str_consts(tree)
    for attr in _PROMPT_ATTRS:
        for stmt in class_node.body:
            target: ast.AST | None = None
            value: ast.AST | None = None
            if isinstance(stmt, ast.Assign) and stmt.targets:
                target, value = stmt.targets[0], stmt.value
            elif isinstance(stmt, ast.AnnAssign):
                target, value = stmt.target, stmt.value
            if isinstance(target, ast.Name) and target.id == attr and value is not None:
                text = _ast_fixed_text(value, consts)
                if text and text.strip():
                    return {"attr": attr, "text": text.strip()}
    return None


def _worker_definition_ref(path: Path, pkg_id: str, pkg_dir: Path, node: dict[str, Any], class_names: set[str]) -> dict[str, Any]:
    source, tree = _read_and_parse(path)
    class_node: ast.ClassDef | None = None
    if tree is not None:
        classes = [item for item in tree.body if isinstance(item, ast.ClassDef)]
        class_node = next((item for item in classes if item.name in class_names), None)
        if class_node is None:
            worker_classes = [item for item in classes if item.name.endswith(("Worker", "Orchestrator", "Generator"))]
            class_node = worker_classes[0] if worker_classes else (classes[0] if classes else None)

    description = ""
    line_start: int | None = None
    line_end: int | None = None
    symbol: str | None = None
    worker: dict[str, Any] | None = None
    if class_node is not None:
        symbol = class_node.name
        line_start = class_node.lineno
        line_end = getattr(class_node, "end_lineno", None)
        description = (
            ast.get_docstring(class_node)
            or _safe_text(_class_attr_value(class_node, "DESCRIPTION"), 700)
            or str(node.get("description") or "")
        )
        worker = {
            "class_name": class_node.name,
            "description": _safe_text(description, 700),
            "format_in": _class_attr_value(class_node, "FORMAT_IN"),
            "format_in_mode": _class_attr_value(class_node, "FORMAT_IN_MODE"),
            "format_out": _class_attr_value(class_node, "FORMAT_OUT"),
            # 固定 prompt(NODE_PROMPT 等)。前端 worker 浮窗用它显示"提示词"块; 无则 None。
            "prompt": _extract_fixed_prompt(tree, class_node),
        }

    return _definition_ref(
        "worker",
        f"{pkg_id}/workers/{path.stem}",
        path,
        pkg_dir,
        "打开 Worker 源码",
        symbol=symbol,
        line_start=line_start,
        line_end=line_end,
        summary=_safe_text(description or node.get("description"), 700),
        source_excerpt=_source_excerpt(source, line_start, line_end),
        worker=worker,
    )


def _find_worker_definition(pkg_dir: Path, pkg_id: str, node: dict[str, Any]) -> dict[str, Any] | None:
    workers_dir = pkg_dir / "workers"
    if not workers_dir.is_dir():
        return None

    node_id = str(node.get("id") or "")
    label = str(node.get("label") or "")
    raw_names = [
        node_id,
        label,
        label.removesuffix("Worker"),
        node_id.removesuffix("_worker"),
    ]
    class_names = {
        _to_camel_identifier(node_id),
        f"{_to_camel_identifier(node_id)}Worker",
        label,
    }
    class_names = {name for name in class_names if name and re.match(r"^[A-Za-z_][0-9A-Za-z_]*$", name)}
    for raw in raw_names:
        stem = _to_snake_identifier(raw)
        if not stem:
            continue
        candidate = workers_dir / f"{stem}.py"
        if candidate.is_file():
            return _worker_definition_ref(candidate, pkg_id, pkg_dir, node, class_names)

    worker_sources: list[tuple[Path, str]] = []
    for candidate in sorted(workers_dir.glob("*.py")):
        if candidate.name == "__init__.py":
            continue
        source = _read_text(candidate)
        if not source:
            continue
        worker_sources.append((candidate, source))
        if any(re.search(rf"\bclass\s+{re.escape(class_name)}\b", source) for class_name in class_names):
            return _worker_definition_ref(candidate, pkg_id, pkg_dir, node, class_names)
    for candidate, source in worker_sources:
        if node_id and re.search(rf"\b{re.escape(node_id)}\b", source):
            return _worker_definition_ref(candidate, pkg_id, pkg_dir, node, class_names)
    return None


def _material_definition_ref(path: Path, pkg_id: str, pkg_dir: Path, material_id: str) -> dict[str, Any]:
    source, tree = _read_and_parse(path)

    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    summary: str | None = None
    material: dict[str, Any] | None = None

    if tree is not None:
        for stmt in ast.walk(tree):
            if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
                continue
            func = stmt.value.func
            func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if func_name not in {"Material", "Format"}:
                continue
            values = {keyword.arg: _safe_literal(keyword.value) for keyword in stmt.value.keywords if keyword.arg}
            if values.get("id") != material_id:
                continue
            target = stmt.targets[0] if stmt.targets else None
            symbol = target.id if isinstance(target, ast.Name) else None
            line_start = stmt.lineno
            line_end = getattr(stmt, "end_lineno", None)
            name = str(values.get("name") or "")
            description = str(values.get("description") or "")
            schema = values.get("json_schema")
            tags = values.get("tags") if isinstance(values.get("tags"), list) else []
            material = {
                "id": material_id,
                "name": name,
                "description": _safe_text(description, 900),
                "parent": values.get("parent"),
                "kind": _kind_from_tags(tags, description),
                "tags": [str(tag) for tag in tags],
                "fields": _schema_fields(schema),
                "required": list(schema.get("required") or []) if isinstance(schema, dict) else [],
            }
            summary = description or name
            break

    if line_start is None and source:
        lines = source.splitlines()
        for index, line in enumerate(lines, start=1):
            if material_id in line:
                line_start = index
                line_end = min(index + 40, len(lines))
                summary = material_id
                break

    return _definition_ref(
        "material",
        f"{pkg_id}/{path.stem}",
        path,
        pkg_dir,
        "打开 Material 源码",
        symbol=symbol,
        line_start=line_start,
        line_end=line_end,
        summary=_safe_text(summary, 900),
        source_excerpt=_source_excerpt(source, line_start, line_end),
        material=material,
    )


def _find_material_definition(pkg_dir: Path, pkg_id: str, material_id: str) -> dict[str, Any] | None:
    candidates = [pkg_dir / "formats.py", pkg_dir / "materials.py"]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return None
    for candidate in existing:
        source = _read_text(candidate)
        if material_id in source:
            return _material_definition_ref(candidate, pkg_id, pkg_dir, material_id)
    fallback = existing[0]
    return _definition_ref("material", f"{pkg_id}/{fallback.stem}", fallback, pkg_dir, "打开 Material 源码")


def _serialize_team_graph(team_id: str, path: Path, builder: dict[str, Any], builders: list[dict[str, Any]], builder_errors: dict[str, Any]) -> dict[str, Any]:
    spec = builder["spec"]
    pkg_dir = path.parent
    pkg_id = str(pkg_dir.relative_to(_packages_root())).replace(os.sep, "/")
    node_rows = [_serialize_node(node, spec.entry) for node in spec.nodes]
    for node in node_rows:
        node["definition"] = _find_worker_definition(pkg_dir, pkg_id, node)
    node_by_id = {node["id"]: node for node in node_rows}

    producers: dict[str, list[str]] = {}
    consumers: dict[str, list[str]] = {}
    for node in node_rows:
        out = node.get("format_out")
        if out:
            producers.setdefault(out, []).append(node["id"])
        for fmt in node.get("format_in") or []:
            consumers.setdefault(fmt, []).append(node["id"])

    edge_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, edge in enumerate(spec.edges):
        source = getattr(edge, "source", "")
        target = getattr(edge, "target", "")
        source_node = node_by_id.get(source)
        target_node = node_by_id.get(target)
        if source_node is None:
            warnings.append(f"edge {source}->{target} references missing source")
        if target_node is None:
            warnings.append(f"edge {source}->{target} references missing target")
        source_out = source_node.get("format_out") if source_node else None
        target_in = target_node.get("format_in") if target_node else []
        edge_rows.append({
            "id": f"e{index}:{source}->{target}",
            "source": source,
            "target": target,
            "condition": _enum_value(getattr(edge, "condition", None)),
            "condition_expr": getattr(edge, "condition_expr", None),
            "label": getattr(edge, "label", None),
            "feedback": bool(getattr(edge, "feedback", False)),
            "material_id": source_out,
            "source_format": source_out,
            "target_format": target_in,
        })

    if spec.entry not in node_by_id:
        warnings.append(f"entry node missing: {spec.entry}")
    if not node_rows:
        warnings.append("team has no nodes")
    if not edge_rows and len(node_rows) > 1:
        warnings.append("team has multiple nodes but no edges")

    referenced = {getattr(edge, "source", "") for edge in spec.edges} | {getattr(edge, "target", "") for edge in spec.edges} | {spec.entry}
    for node_id in node_by_id:
        if node_id not in referenced:
            warnings.append(f"orphan node: {node_id}")

    material_ids = sorted(set(producers) | set(consumers))
    material_rows = [
        {
            "id": material_id,
            "producers": producers.get(material_id, []),
            "consumers": consumers.get(material_id, []),
            "is_external_input": not producers.get(material_id),
            "is_terminal_output": not consumers.get(material_id),
            "definition": _find_material_definition(pkg_dir, pkg_id, material_id),
        }
        for material_id in material_ids
    ]

    soft_nodes = sum(1 for node in node_rows if node.get("validator_kind") == "soft")
    hard_nodes = sum(1 for node in node_rows if node.get("validator_kind") == "hard")
    feedback_edges = sum(1 for edge in edge_rows if edge.get("feedback"))

    return {
        "team_id": team_id,
        "source_path": str(path),
        "definition": _definition_ref("team", team_id, path, pkg_dir, "打开 Team 定义"),
        "spec_id": str(getattr(spec, "id", "")),
        "name": str(getattr(spec, "name", "")),
        "description": str(getattr(spec, "description", "")),
        "purpose": str(getattr(spec, "purpose", "")),
        "entry": str(getattr(spec, "entry", "")),
        "tags": list(getattr(spec, "tags", []) or []),
        "builders": [
            {
                "name": item["name"],
                "spec_id": item["spec_id"],
                "nodes": item["nodes"],
                "edges": item["edges"],
                "entry": item["entry"],
            }
            for item in builders
        ],
        "selected_builder": builder["name"],
        "nodes": node_rows,
        "edges": edge_rows,
        "materials": material_rows,
        "health": {
            "warnings": warnings,
            "builder_errors": builder_errors,
            "soft_nodes": soft_nodes,
            "hard_nodes": hard_nodes,
            "feedback_edges": feedback_edges,
            "external_inputs": sum(1 for item in material_rows if item["is_external_input"]),
            "terminal_outputs": sum(1 for item in material_rows if item["is_terminal_output"]),
        },
    }


def _load_team_selection(team_id: str, builder: str | None = None) -> tuple[Path, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    path = _resolve_item_path(_scan_teams_cached, "team", team_id)
    module_name = _module_name_for_path(path)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"import failed: {type(exc).__name__}: {exc}")

    builders, builder_errors = _discover_team_builders(module)
    if not builders:
        detail = "no zero-argument build* function returned a TeamSpec"
        if builder_errors:
            detail += f"; builder errors: {builder_errors}"
        raise HTTPException(status_code=422, detail=detail)
    selected = _select_builder(builders, builder)
    return path, builders, builder_errors, selected


# ── team-graph 结果缓存 ───────────────────────────────────────────────────
# 原本每次请求都重跑 build*() + 逐节点/材料解析源码, 零缓存。按 (team_id, builder) 缓存
# 序列化结果; 指纹 = team/worker/material 源文件最新 mtime, 任一改动即失效。
_TEAM_GRAPH_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def _team_source_fingerprint(path: Path) -> float:
    """team 图依赖的源文件最新 mtime(team 文件 + workers/*.py + formats/materials.py)。
    stat 比 read+ast.parse 便宜几个数量级, 用它当缓存失效信号。"""
    pkg_dir = path.parent
    paths = [path, pkg_dir / "formats.py", pkg_dir / "materials.py"]
    workers_dir = pkg_dir / "workers"
    if workers_dir.is_dir():
        paths.extend(sorted(workers_dir.glob("*.py")))
    latest = 0.0
    for p in paths:
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > latest:
            latest = m
    return latest


def _load_team_graph_data(team_id: str, builder: str | None = None) -> dict[str, Any]:
    # _resolve_item_path 便宜(team 扫描已 lru_cache); 命中缓存就跳过 import+build*()+逐节点解析。
    path = _resolve_item_path(_scan_teams_cached, "team", team_id)
    fingerprint = _team_source_fingerprint(path)
    key = (team_id, builder or "")
    cached = _TEAM_GRAPH_CACHE.get(key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    _, builders, builder_errors, selected = _load_team_selection(team_id, builder)
    data = _serialize_team_graph(team_id, path, selected, builders, builder_errors)
    _TEAM_GRAPH_CACHE[key] = (fingerprint, data)
    return data


def _doctor_level_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"blocking": 0, "degrading": 0, "advisory": 0, "info": 0}
    for finding in findings:
        level = str(finding.get("level") or "info")
        counts[level] = counts.get(level, 0) + 1
    return counts


def _doctor_status(counts: dict[str, int]) -> str:
    if counts.get("blocking", 0) > 0:
        return "unhealthy"
    if counts.get("degrading", 0) > 0 or counts.get("advisory", 0) > 0:
        return "degraded"
    return "healthy"


def _edge_ids_for_doctor_location(location: str, graph: dict[str, Any]) -> list[str]:
    if not location.startswith("edge:"):
        return []
    edge_text = location.removeprefix("edge:")
    matches: list[str] = []
    for edge in graph.get("edges", []):
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source and target and source in edge_text and target in edge_text:
            matches.append(str(edge.get("id")))
    return matches


def _node_ids_for_doctor_location(location: str, graph: dict[str, Any]) -> list[str]:
    node_ids = {str(node.get("id")) for node in graph.get("nodes", []) if node.get("id")}
    if location.startswith("node:"):
        node_id = location.removeprefix("node:").strip()
        return [node_id] if node_id in node_ids else []
    if location.startswith("nodes:"):
        return [
            item.strip()
            for item in location.removeprefix("nodes:").split(",")
            if item.strip() in node_ids
        ]
    edge_node_ids: list[str] = []
    for edge_id in _edge_ids_for_doctor_location(location, graph):
        edge = next((item for item in graph.get("edges", []) if item.get("id") == edge_id), None)
        if edge:
            for key in ("source", "target"):
                node_id = str(edge.get(key) or "")
                if node_id and node_id not in edge_node_ids:
                    edge_node_ids.append(node_id)
    return edge_node_ids


def _material_ids_for_doctor_finding(finding: Any, graph: dict[str, Any], edge_ids: list[str]) -> list[str]:
    material_ids: list[str] = []
    for ref in getattr(finding, "cross_refs", []) or []:
        text = str(ref)
        if text.startswith("format:"):
            for material_id in _split_format_tokens(text.removeprefix("format:")):
                if material_id and material_id not in material_ids:
                    material_ids.append(material_id)
    for edge_id in edge_ids:
        edge = next((item for item in graph.get("edges", []) if item.get("id") == edge_id), None)
        material_id = str(edge.get("material_id") or "") if edge else ""
        if material_id and material_id not in material_ids:
            material_ids.append(material_id)
    return material_ids


def _serialize_doctor_finding(finding: Any, index: int, graph: dict[str, Any]) -> dict[str, Any]:
    location = str(getattr(finding, "location", "") or "")
    edge_ids = _edge_ids_for_doctor_location(location, graph)
    node_ids = _node_ids_for_doctor_location(location, graph)
    material_ids = _material_ids_for_doctor_finding(finding, graph, edge_ids)

    if node_ids:
        target_kind = "node"
        target_id = node_ids[0]
    elif edge_ids:
        target_kind = "edge"
        target_id = edge_ids[0]
    elif material_ids:
        target_kind = "material"
        target_id = material_ids[0]
    elif location == "pipeline":
        target_kind = "team"
        target_id = str(graph.get("spec_id") or graph.get("team_id") or "")
    else:
        target_kind = "unknown"
        target_id = location

    return {
        "id": f"{getattr(finding, 'check_id', 'finding')}:{index}",
        "check_id": str(getattr(finding, "check_id", "")),
        "level": str(getattr(finding, "level", "info")),
        "severity": str(getattr(finding, "severity", "INFO")),
        "location": location,
        "target_kind": target_kind,
        "target_id": target_id,
        "node_ids": node_ids,
        "edge_ids": edge_ids,
        "material_ids": material_ids,
        "observation": _safe_text(getattr(finding, "observation", ""), 420),
        "implication": _safe_text(getattr(finding, "implication", ""), 420),
        "cross_refs": [str(item) for item in (getattr(finding, "cross_refs", []) or [])],
    }


def _build_team_doctor_health(team_id: str, builder: str | None = None) -> dict[str, Any]:
    path, builders, builder_errors, selected = _load_team_selection(team_id, builder)
    graph = _serialize_team_graph(team_id, path, selected, builders, builder_errors)
    spec = selected["spec"]

    try:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import PIPELINE_CHECKS, run_pipeline_checks
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"doctor import failed: {type(exc).__name__}: {exc}")

    try:
        raw_findings = run_pipeline_checks(spec)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"doctor check failed: {type(exc).__name__}: {exc}")

    findings = [
        _serialize_doctor_finding(finding, index, graph)
        for index, finding in enumerate(raw_findings)
    ]
    counts = _doctor_level_counts(findings)
    checks = [
        {
            "id": str(getattr(check, "id", "")),
            "description": str(getattr(check, "description", "")),
            "default_on": bool(getattr(check, "default_on", True)),
        }
        for check in PIPELINE_CHECKS
    ]

    return {
        "team_id": team_id,
        "spec_id": graph.get("spec_id"),
        "selected_builder": graph.get("selected_builder"),
        "source_path": graph.get("source_path"),
        "status": _doctor_status(counts),
        "passed": counts.get("blocking", 0) == 0,
        "counts": {
            **counts,
            "total": len(findings),
        },
        "checks": checks,
        "findings": findings,
    }


def _split_format_tokens(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for item in raw:
            out.extend(_split_format_tokens(item))
        return list(dict.fromkeys(out))

    text = str(raw).strip()
    if not text:
        return []
    normalized = text
    for sep in (" + ", "+", ",", "|", "\n", "\r", "\t"):
        normalized = normalized.replace(sep, ",")
    tokens = [
        item.strip().strip("[]'\"")
        for item in normalized.split(",")
        if item.strip().strip("[]'\"")
    ]
    return list(dict.fromkeys(tokens))


def _event_row_to_dict(row: sqlite3.Row, domain: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(row["data"])
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    payload = parsed.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    return {
        "id": parsed.get("id") or row["id"],
        "trace_id": parsed.get("trace_id") or row["trace_id"],
        "parent_id": parsed.get("parent_id") or row["parent_id"],
        "event_type": parsed.get("event_type") or row["event_type"],
        "source": parsed.get("source") or row["source"],
        "timestamp": parsed.get("timestamp") or row["timestamp"],
        "payload": payload,
        "_domain": domain,
    }


def _dedupe_event_db_sources(sources: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    results: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for domain, db_path in sources:
        try:
            key = str(db_path.resolve()).casefold()
        except OSError:
            key = str(db_path).casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append((domain, db_path))
    return results


def _team_builder_workspace_event_dbs() -> list[tuple[str, Path]]:
    """受控兼容旧 TeamBuilder 实战样本库；不恢复 data/** 全量 rglob。"""
    root = _repo_root() / "data" / "_workspaces" / "team_builder"
    if not root.is_dir():
        return []
    root_resolved = root.resolve()
    results: list[tuple[str, Path]] = []
    for db_path in sorted(root.glob("*/data/events.db")):
        try:
            resolved = db_path.resolve()
            resolved.relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        if _is_skipped(resolved):
            continue
        workspace_name = db_path.parents[1].name
        results.append((f"team_builder:{workspace_name}", db_path))
    return results


def _team_run_event_db_sources() -> list[tuple[str, Path]]:
    return _dedupe_event_db_sources([*discover_event_dbs(), *_team_builder_workspace_event_dbs()])


def _iter_recent_events(
    max_events_per_db: int = 20000,
    event_dbs: list[tuple[str, Path]] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for domain, db_path in (event_dbs if event_dbs is not None else discover_event_dbs()):
        conn = safe_conn(db_path)
        if conn is None:
            continue
        try:
            rows = conn.execute(
                """
                SELECT id, trace_id, parent_id, event_type, source, timestamp, data
                FROM events
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (max_events_per_db,),
            ).fetchall()
            for row in rows:
                ev = _event_row_to_dict(row, domain)
                if ev:
                    events.append(ev)
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    return events


def _load_trace_events(trace_id: str, event_dbs: list[tuple[str, Path]] | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for domain, db_path in (event_dbs if event_dbs is not None else discover_event_dbs()):
        conn = safe_conn(db_path)
        if conn is None:
            continue
        try:
            rows = conn.execute(
                """
                SELECT id, trace_id, parent_id, event_type, source, timestamp, data
                FROM events
                WHERE trace_id=?
                ORDER BY timestamp
                """,
                (trace_id,),
            ).fetchall()
            for row in rows:
                ev = _event_row_to_dict(row, domain)
                if ev:
                    events.append(ev)
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    events.sort(key=lambda item: item.get("timestamp") or "")
    return events


def _node_ids_for_event(event: dict[str, Any], node_ids: set[str]) -> list[str]:
    payload = event.get("payload") or {}
    found: list[str] = []

    raw_node = payload.get("node") or payload.get("node_id") or payload.get("worker")
    if raw_node and str(raw_node) in node_ids:
        found.append(str(raw_node))

    source = str(event.get("source") or "")
    source_candidates = [source]
    if source.startswith("agent."):
        source_candidates.append(source.removeprefix("agent."))
    if ":" in source:
        source_candidates.append(source.rsplit(":", 1)[-1])
    if "." in source:
        source_candidates.append(source.rsplit(".", 1)[-1])
    for candidate in source_candidates:
        if candidate in node_ids and candidate not in found:
            found.append(candidate)

    return found


def _event_materials(event: dict[str, Any]) -> tuple[list[str], list[str]]:
    payload = event.get("payload") or {}
    return _split_format_tokens(payload.get("format_in")), _split_format_tokens(payload.get("format_out"))


def _team_trace_tokens(graph: dict[str, Any]) -> set[str]:
    tokens = {
        str(graph.get("team_id") or ""),
        str(graph.get("spec_id") or ""),
        str(graph.get("name") or ""),
    }
    team_id = str(graph.get("team_id") or "")
    parts = [part for part in team_id.split("/") if part]
    tokens.update(parts[-3:])
    return {token.lower() for token in tokens if token}


def _event_matches_team(event: dict[str, Any], graph: dict[str, Any], node_ids: set[str], material_ids: set[str]) -> bool:
    if _node_ids_for_event(event, node_ids):
        return True

    payload = event.get("payload") or {}
    tokens = _team_trace_tokens(graph)
    candidates = [
        event.get("source"),
        payload.get("pipeline"),
        payload.get("team"),
        payload.get("team_id"),
        payload.get("spec_id"),
    ]
    for candidate in candidates:
        if candidate is not None and str(candidate).lower() in tokens:
            return True

    inputs, outputs = _event_materials(event)
    return bool((set(inputs) | set(outputs)) & material_ids)


def _tool_call_count(events: list[dict[str, Any]]) -> int:
    count = sum(1 for event in events if event.get("event_type") == "agent.tool.call")
    for event in events:
        payload = event.get("payload") or {}
        tool_calls = payload.get("tool_calls")
        if isinstance(tool_calls, list):
            count += len(tool_calls)
        elif isinstance(tool_calls, int):
            count += tool_calls
    return count


def _trace_status(events: list[dict[str, Any]]) -> str:
    if any(str(event.get("event_type") or "").endswith(".error") for event in events):
        return "error"
    if any(str((event.get("payload") or {}).get("verdict") or "").lower() in {"fail", "failed", "error"} for event in events):
        return "error"
    if any(event.get("event_type") in {"task.finish", "agent.loop.finish", "agent_loop.finish", "task.completed"} for event in events):
        return "finished"
    try:
        ended = max(event.get("timestamp") or "" for event in events)
        ended_at = datetime.fromisoformat(ended.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - ended_at > timedelta(minutes=5):
            return "finished"
    except (ValueError, TypeError):
        pass
    return "running"


def _task_desc_for_events(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        payload = event.get("payload") or {}
        if event.get("event_type") == "task.intent":
            desc = payload.get("instruction") or payload.get("task_desc")
            if desc:
                return _safe_text(desc, 220)
            pipeline = payload.get("pipeline")
            entry = payload.get("entry")
            if pipeline:
                return _safe_text(f"{pipeline}: {entry or ''}".strip(), 220)
    for event in events:
        payload = event.get("payload") or {}
        desc = payload.get("description") or payload.get("instruction") or payload.get("task_desc")
        if desc:
            return _safe_text(desc, 220)
    return None


def _verdict_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        verdict = (event.get("payload") or {}).get("verdict")
        if verdict:
            key = str(verdict)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _summarize_team_trace(trace_id: str, events: list[dict[str, Any]], matched_events: list[dict[str, Any]], node_ids: set[str]) -> dict[str, Any]:
    matched_nodes = sorted({
        node_id
        for event in matched_events
        for node_id in _node_ids_for_event(event, node_ids)
    })
    timestamps = [event.get("timestamp") for event in events if event.get("timestamp")]
    sources = sorted({str(event.get("source") or "") for event in matched_events if event.get("source")})
    return {
        "trace_id": trace_id,
        "task_desc": _task_desc_for_events(events),
        "source": sources[0] if sources else "",
        "domains": sorted({event.get("_domain") for event in events if event.get("_domain")}),
        "started_at": min(timestamps) if timestamps else None,
        "ended_at": max(timestamps) if timestamps else None,
        "event_count": len(events),
        "matched_event_count": len(matched_events),
        "matched_nodes": matched_nodes,
        "total_nodes": len(node_ids),
        "tool_calls": _tool_call_count(events),
        "llm_calls": sum(1 for event in events if event.get("event_type") == "agent.llm.request"),
        "agent_turns": sum(1 for event in events if event.get("event_type") == "agent.turn.end"),
        "status": _trace_status(events),
        "verdict_counts": _verdict_counts(matched_events),
        "last_event": events[-1].get("event_type") if events else None,
    }


def _collect_team_runs(
    graph: dict[str, Any],
    max_events_per_db: int = 20000,
    event_dbs: list[tuple[str, Path]] | None = None,
) -> list[dict[str, Any]]:
    node_ids = {str(node.get("id")) for node in graph.get("nodes", []) if node.get("id")}
    material_ids = {str(material.get("id")) for material in graph.get("materials", []) if material.get("id")}

    grouped: dict[str, list[dict[str, Any]]] = {}
    matched: dict[str, list[dict[str, Any]]] = {}
    for event in _iter_recent_events(max_events_per_db=max_events_per_db, event_dbs=event_dbs):
        trace_id = str(event.get("trace_id") or "")
        if not trace_id:
            continue
        grouped.setdefault(trace_id, []).append(event)
        if _event_matches_team(event, graph, node_ids, material_ids):
            matched.setdefault(trace_id, []).append(event)

    runs = [
        _summarize_team_trace(trace_id, sorted(events, key=lambda item: item.get("timestamp") or ""), matched.get(trace_id, []), node_ids)
        for trace_id, events in grouped.items()
        if matched.get(trace_id)
    ]
    runs.sort(key=lambda item: item.get("started_at") or "", reverse=True)
    return runs


def _node_statuses_for_events(events: list[dict[str, Any]], node_ids: set[str]) -> list[dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for event in events:
        for node_id in _node_ids_for_event(event, node_ids):
            status = statuses.setdefault(node_id, {
                "node_id": node_id,
                "event_count": 0,
                "first_at": None,
                "last_at": None,
                "event_types": {},
                "verdict_counts": {},
            })
            timestamp = event.get("timestamp")
            status["event_count"] += 1
            if timestamp and (status["first_at"] is None or timestamp < status["first_at"]):
                status["first_at"] = timestamp
            if timestamp and (status["last_at"] is None or timestamp > status["last_at"]):
                status["last_at"] = timestamp
            event_type = str(event.get("event_type") or "")
            status["event_types"][event_type] = status["event_types"].get(event_type, 0) + 1
            verdict = (event.get("payload") or {}).get("verdict")
            if verdict:
                key = str(verdict)
                status["verdict_counts"][key] = status["verdict_counts"].get(key, 0) + 1
    return sorted(statuses.values(), key=lambda item: item["node_id"])


def _team_run_timeline(events: list[dict[str, Any]], node_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        event_nodes = _node_ids_for_event(event, node_ids)
        inputs, outputs = _event_materials(event)
        payload = event.get("payload") or {}
        rows.append({
            "id": event.get("id"),
            "trace_id": event.get("trace_id"),
            "timestamp": event.get("timestamp"),
            "event_type": event.get("event_type"),
            "source": event.get("source"),
            "node_ids": event_nodes,
            "description": _safe_text(payload.get("description") or payload.get("instruction") or payload.get("task_desc"), 280),
            "verdict": payload.get("verdict"),
            "format_in": inputs,
            "format_out": outputs,
            "input_signal": _safe_text(payload.get("input_signal"), 160),
            "output_signal": _safe_text(payload.get("output_signal"), 160),
            "diagnosis": _safe_text(payload.get("diagnosis"), 240),
            "tool_calls": payload.get("tool_calls") if isinstance(payload.get("tool_calls"), list) else None,
        })
    return rows


def _material_observations(events: list[dict[str, Any]], node_ids: set[str]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for event in events:
        event_nodes = _node_ids_for_event(event, node_ids)
        if not event_nodes:
            continue
        inputs, outputs = _event_materials(event)
        if not inputs and not outputs:
            continue
        for node_id in event_nodes:
            observations.append({
                "node_id": node_id,
                "event_type": event.get("event_type"),
                "timestamp": event.get("timestamp"),
                "inputs": inputs,
                "outputs": outputs,
                "verdict": (event.get("payload") or {}).get("verdict"),
            })
    return observations


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _materialization_candidate_kind_from_target(target: str) -> str:
    if "=" not in target:
        return "resource"
    key = target.split("=", 1)[0].strip().lower()
    if key in {"path", "file_path", "filepath"}:
        return "file"
    if key in {"glob", "pattern"}:
        return key
    if key in {"command", "cmd", "argv"}:
        return "command"
    if key == "query":
        return "query"
    return "resource"


def _materialization_normalized_target(target: str) -> str:
    value = target.split("=", 1)[1] if "=" in target else target
    normalized = value.replace("\\", "/").strip()
    marker = "omnicompany/"
    marker_idx = normalized.lower().find(marker)
    if marker_idx >= 0:
        normalized = normalized[marker_idx + len(marker):]
    return normalized


def _materialization_target_value(target: str) -> str:
    return (target.split("=", 1)[1] if "=" in target else target).strip().strip("'\"")


def _materialization_workspace_path(target: str, normalized_target: str) -> Path | None:
    root = _repo_root().resolve()
    values = [_materialization_target_value(target), normalized_target]
    for value in values:
        if not value:
            continue
        raw = Path(value)
        candidates = [raw] if raw.is_absolute() else [root / value.replace("\\", "/")]
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if resolved.is_file() and not _is_skipped(resolved):
                return resolved
    return None


def _materialization_workspace_dir(target: str, normalized_target: str) -> Path | None:
    root = _repo_root().resolve()
    values = [_materialization_target_value(target), normalized_target]
    for value in values:
        if not value:
            continue
        raw = Path(value)
        candidates = [raw] if raw.is_absolute() else [root / value.replace("\\", "/")]
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if resolved.is_dir() and not _is_skipped(resolved):
                return resolved
    return None


def _materialization_glob_paths(target: str) -> list[Path]:
    key = target.split("=", 1)[0].strip().lower() if "=" in target else ""
    if key not in {"glob", "pattern"}:
        return []
    value = _materialization_target_value(target).replace("\\", "/")
    if not value or not any(token in value for token in ("*", "?", "[")):
        return []
    if "/" not in value:
        return []
    root = _repo_root().resolve()
    if Path(value).is_absolute():
        raw_matches = [Path(item) for item in glob.glob(value)]
    else:
        raw_matches = list(root.glob(value))
    matches: list[Path] = []
    for item in raw_matches:
        try:
            resolved = item.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file() and not _is_skipped(resolved):
            matches.append(resolved)
    return sorted(set(matches), key=lambda path: path.as_posix())[:30]


def _materialization_file_summary(path: Path) -> tuple[str, str, str]:
    try:
        rel = path.resolve().relative_to(_repo_root().resolve()).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")[:12000]
        size = path.stat().st_size
    except OSError:
        return path.name, f"工作区文件：{path.name}。", ""

    suffix = path.suffix.lower()
    if suffix == ".py":
        names = re.findall(r"^(?:class|def)\s+([A-Za-z_]\w*)", text, re.MULTILINE)
        visible = "、".join(names[:5])
        summary = f"工作区 Python 源码文件，路径 {rel}，大小 {size} 字节。"
        if visible:
            summary += f" 可见定义：{visible}。"
    elif suffix in {".md", ".markdown"}:
        headings = [item.strip("# ").strip() for item in re.findall(r"^#{1,3}\s+(.+)$", text, re.MULTILINE)]
        visible = "、".join(headings[:4])
        summary = f"工作区 Markdown 文档，路径 {rel}，大小 {size} 字节。"
        if visible:
            summary += f" 可见标题：{visible}。"
    elif suffix in {".json", ".jsonl", ".yaml", ".yml", ".toml"}:
        summary = f"工作区结构化数据文件，路径 {rel}，大小 {size} 字节。"
    else:
        summary = f"工作区文件，路径 {rel}，大小 {size} 字节。"

    lines = [line.strip() for line in text.splitlines() if line.strip()][:4]
    excerpt = "\n".join(lines)
    return path.name, summary, excerpt


def _materialization_declared_material_ids(target: str, normalized_target: str) -> list[str]:
    path = _materialization_workspace_path(target, normalized_target)
    paths = [path] if path else _materialization_glob_paths(target)
    found_ids: set[str] = set()
    for item in paths:
        try:
            text = item.read_text(encoding="utf-8", errors="ignore")[:4096]
        except OSError:
            continue
        found = re.findall(r"material_id\s*=\s*['\"]([^'\"]+)['\"]", text)
        found_ids.update(item.strip() for item in found if item.strip())
    return sorted(found_ids)


def _materialization_relpath(path: Path) -> str:
    try:
        return path.resolve().relative_to(_repo_root().resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


def _materialization_material_ids_in_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    except OSError:
        return []
    found = re.findall(r"material_id\s*=\s*['\"]([^'\"]+)['\"]", text)
    return sorted({_safe_text(item, 220) for item in found if _safe_text(item, 220)})[:6]


def _materialization_iter_files(root: Path, *, limit: int = 120) -> list[Path]:
    files: list[Path] = []
    try:
        resolved_root = root.resolve()
        resolved_root.relative_to(_repo_root().resolve())
    except (OSError, ValueError):
        return files
    if not resolved_root.is_dir() or _is_skipped(resolved_root):
        return files
    for dirpath, dirnames, filenames in os.walk(resolved_root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_PARTS)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if _is_skipped(path) or not path.is_file():
                continue
            files.append(path)
            if len(files) >= limit:
                return files
    return files


def _materialization_file_review_example(path: Path, *, line_no: int | None = None, excerpt: str = "") -> dict[str, Any]:
    material_ids = _materialization_material_ids_in_file(path)
    example = {
        "path": _materialization_relpath(path),
        "kind": path.suffix.lower().lstrip(".") or "file",
        "material_ids": material_ids,
    }
    if line_no:
        example["line"] = line_no
    if excerpt:
        example["excerpt"] = _safe_text(excerpt, 180)
    elif material_ids:
        example["excerpt"] = f"文件头声明 material_id: {'、'.join(material_ids[:3])}"
    return example


def _materialization_human_title(
    *,
    material_id: str,
    registration_status: str,
    target: str,
    normalized_target: str,
    candidate_kind: str,
    rel_path: str,
) -> str:
    if registration_status == "generated-candidate":
        value = rel_path or normalized_target or material_id
        return f"生成产物：{Path(value).name}" if value else "生成产物"
    if material_id and registration_status != "candidate":
        return material_id.rsplit(".", 1)[-1].replace("_", " ")
    path = _materialization_workspace_path(target, normalized_target)
    if path:
        return f"工作区文件：{path.name}"
    directory = _materialization_workspace_dir(target, normalized_target)
    if directory:
        return f"工作区目录：{directory.name}"
    value = normalized_target or _materialization_target_value(target) or rel_path or material_id
    if candidate_kind in {"glob", "pattern"}:
        return f"文件匹配：{Path(value).name or value}"
    if candidate_kind in {"command", "cmd", "argv"}:
        return "命令读取线索"
    if candidate_kind == "query":
        return "查询读取线索"
    return Path(value).name or value or "读取线索"


def _materialization_evidence_summary(
    *,
    registration_status: str,
    target_key: str,
    normalized_target: str,
    candidate_kind: str,
    matched_material_ids: list[str],
) -> str:
    if registration_status == "generated-candidate":
        return "外部代理这次生成了这个文件，因此先把它登记成生成产物候选。"
    if registration_status in {"declared", "registered"}:
        return "这是 Team/Worker 定义里声明的 material 读写关系，不是从运行日志里猜出来的。"
    if registration_status == "declared-in-file":
        return "实战读取到的文件里声明了 OMNI material_id，因此可以作为较强的 material 读取证据。"
    target_desc = normalized_target or "未记录具体目标"
    if matched_material_ids:
        return f"实战读取目标命中了已知 material 标识：{'、'.join(matched_material_ids[:3])}。"
    if candidate_kind in {"file", "glob", "pattern"}:
        return f"实战记录里出现了 {target_key or candidate_kind}，目标指向 {target_desc}；目前只说明 worker 接触过这个工作区资料。"
    if candidate_kind in {"command", "cmd", "argv"}:
        return f"实战记录里出现了命令型读取线索；命令目标归一化为 {target_desc}，需要进一步解析命令输出才能确认具体 material。"
    if candidate_kind == "query":
        return f"实战记录里出现了查询型读取线索；查询目标归一化为 {target_desc}，需要进一步确认数据表或结果是否对应 material。"
    return f"实战记录里出现了资源读取线索：{target_desc}；目前只是待确认的证据。"


def _compact_materialization_link(link: Any) -> dict[str, Any]:
    data = _dict_value(link)
    material_id = _safe_text(data.get("material_id"), 220)
    target = _safe_text(data.get("target"), 240)
    registration_status = _safe_text(data.get("registration_status"), 64)
    candidate_kind = _safe_text(data.get("candidate_kind"), 64)
    if not candidate_kind and registration_status == "candidate":
        candidate_kind = _materialization_candidate_kind_from_target(target)
    normalized_target = _safe_text(data.get("normalized_target"), 240)
    if not normalized_target and target:
        normalized_target = _safe_text(_materialization_normalized_target(target), 240)
    workspace_dir = _materialization_workspace_dir(target, normalized_target)
    if workspace_dir and candidate_kind in {"file", "resource"}:
        candidate_kind = "directory"
    candidate_reason = _safe_text(data.get("candidate_reason"), 420)
    if not candidate_reason and registration_status == "candidate":
        resource_kind = _safe_text(data.get("resource_kind"), 64) or "workspace"
        material_id = _safe_text(data.get("material_id"), 220)
        candidate_reason = f"旧实战记录没有写入详细解释；dashboard 根据运行读取目标把它展示为 {resource_kind} {candidate_kind or 'resource'} 待确认 material 线索：{material_id}"
    promotion_hint = _safe_text(data.get("promotion_hint"), 360)
    if not promotion_hint and registration_status == "candidate":
        promotion_hint = "后续需要解析工具参数、文件内容或 OMNI material_id header，才能把这条线索升级为正式 material 读取边。"
    target_key = _safe_text(data.get("target_key"), 64)
    rel_path = _safe_text(data.get("rel_path"), 220)
    declared_material_ids = _materialization_declared_material_ids(target, normalized_target)
    matched_material_ids = sorted({
        _safe_text(item, 220)
        for item in [*_list_value(data.get("matched_material_ids")), *declared_material_ids]
        if _safe_text(item, 220)
    })[:8]
    declared_material_ids = [_safe_text(item, 220) for item in declared_material_ids[:8]]
    workspace_path = _materialization_workspace_path(target, normalized_target)
    target_title = ""
    target_summary = ""
    target_excerpt = ""
    target_exists = False
    if workspace_path:
        target_exists = True
        target_title, target_summary, target_excerpt = _materialization_file_summary(workspace_path)
    elif workspace_dir:
        target_exists = True
        try:
            rel_dir = workspace_dir.resolve().relative_to(_repo_root().resolve()).as_posix()
        except ValueError:
            rel_dir = workspace_dir.name
        target_title = workspace_dir.name
        target_summary = f"工作区目录，路径 {rel_dir}。目录级线索表示 worker 接触了一个资料空间，但不能唯一对应到某个 material。"
    human_title = _safe_text(data.get("human_title"), 180) or _safe_text(
        _materialization_human_title(
            material_id=material_id,
            registration_status=registration_status,
            target=target,
            normalized_target=normalized_target,
            candidate_kind=candidate_kind,
            rel_path=rel_path,
        ),
        180,
    )
    human_summary = _safe_text(data.get("human_summary"), 520) or _safe_text(
        target_summary
        or _materialization_evidence_summary(
            registration_status=registration_status,
            target_key=target_key,
            normalized_target=normalized_target,
            candidate_kind=candidate_kind,
            matched_material_ids=matched_material_ids,
        ),
        520,
    )
    evidence_summary = _safe_text(data.get("evidence_summary"), 520) or _safe_text(
        "读取目标文件头声明了 OMNI material_id，可以升级为较强的 material 读取证据。"
        if declared_material_ids and registration_status == "candidate"
        else _materialization_evidence_summary(
            registration_status=registration_status,
            target_key=target_key,
            normalized_target=normalized_target,
            candidate_kind=candidate_kind,
            matched_material_ids=matched_material_ids,
        ),
        520,
    )
    if declared_material_ids and registration_status == "candidate":
        candidate_reason = "读取目标文件头声明了 OMNI material_id，因此这条工作区读取线索可以生成确认读取边。"
        promotion_hint = "已从文件头 material_id 升级为确认读取；仍保留候选节点用于核对原始资料入口。"
    return {
        "material_id": material_id,
        "direction": _safe_text(data.get("direction"), 32),
        "confidence": _safe_text(data.get("confidence"), 32),
        "basis": _safe_text(data.get("basis"), 180),
        "registration_status": registration_status,
        "resource_kind": _safe_text(data.get("resource_kind"), 64),
        "target": target,
        "target_key": target_key,
        "normalized_target": normalized_target,
        "candidate_kind": candidate_kind,
        "candidate_reason": candidate_reason,
        "promotion_hint": promotion_hint,
        "candidate_material_id": _safe_text(data.get("candidate_material_id"), 220),
        "matched_material_ids": matched_material_ids,
        "declared_material_ids": declared_material_ids,
        "human_title": human_title,
        "human_summary": human_summary,
        "evidence_summary": evidence_summary,
        "target_title": _safe_text(target_title, 180),
        "target_summary": _safe_text(target_summary, 520),
        "target_excerpt": _safe_text(target_excerpt, 520),
        "target_exists": target_exists,
        "rel_path": rel_path,
        "content_kind": _safe_text(data.get("content_kind"), 64),
        "bytes": data.get("bytes") if isinstance(data.get("bytes"), int) else None,
        "evidence": [_safe_text(item, 180) for item in _list_value(data.get("evidence"))[:5]],
    }


def _compact_static_field_access(value: Any) -> dict[str, Any]:
    data = _dict_value(value)
    input_reads = _dict_value(data.get("input_field_reads"))
    missing_input = _dict_value(data.get("missing_input_required"))
    return {
        "input_field_reads": {
            _safe_text(material_id, 220): [_safe_text(field, 80) for field in _list_value(fields)]
            for material_id, fields in input_reads.items()
        },
        "missing_input_required": {
            _safe_text(material_id, 220): [_safe_text(field, 80) for field in _list_value(fields)]
            for material_id, fields in missing_input.items()
        },
        "missing_output_required": [_safe_text(field, 80) for field in _list_value(data.get("missing_output_required"))],
        "output_field_writes": [_safe_text(field, 80) for field in _list_value(data.get("output_field_writes"))],
    }


def _compact_materialization_tool_event(value: Any, index: int) -> dict[str, Any]:
    data = _dict_value(value)
    return {
        "index": index,
        "tool": _safe_text(data.get("tool"), 80),
        "tool_use_id": _safe_text(data.get("tool_use_id"), 120),
        "event_type": _safe_text(data.get("event_type"), 80),
        "read_like": bool(data.get("read_like")),
        "targets": [_safe_text(item, 260) for item in _list_value(data.get("targets"))[:8]],
        "result_paths": [_safe_text(item, 260) for item in _list_value(data.get("result_paths"))[:12]],
        "result_path_evidence_kind": _safe_text(data.get("result_path_evidence_kind"), 80),
        "result_excerpt": _safe_text(data.get("result_excerpt"), 500),
    }


def _synthesize_declared_file_read_links(
    resource_material_links: list[dict[str, Any]],
    existing_links: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen = {
        (_safe_text(link.get("material_id"), 220), _safe_text(link.get("target"), 240))
        for link in existing_links
    }
    synthesized: list[dict[str, Any]] = []
    for resource_link in resource_material_links:
        candidate_material_id = _safe_text(resource_link.get("material_id"), 220)
        declared_ids = [_safe_text(item, 220) for item in _list_value(resource_link.get("declared_material_ids"))]
        for material_id in declared_ids:
            target = _safe_text(resource_link.get("target"), 240)
            key = (material_id, target)
            if not material_id or key in seen:
                continue
            seen.add(key)
            synthesized.append({
                "material_id": material_id,
                "direction": "read",
                "confidence": "high",
                "basis": "read target file declares OMNI material_id",
                "registration_status": "declared-in-file",
                "resource_kind": _safe_text(resource_link.get("resource_kind"), 64),
                "target": target,
                "target_key": _safe_text(resource_link.get("target_key"), 64),
                "normalized_target": _safe_text(resource_link.get("normalized_target"), 240),
                "candidate_kind": _safe_text(resource_link.get("candidate_kind"), 64),
                "candidate_material_id": candidate_material_id,
                "matched_material_ids": [material_id],
                "declared_material_ids": [material_id],
                "human_title": f"已确认读取：{material_id.rsplit('.', 1)[-1].replace('_', ' ')}",
                "human_summary": f"worker 读取的工作区文件头声明了 `{material_id}`，因此这条线索可以视为确认 material 读取。",
                "evidence_summary": "确认依据是读取目标文件顶部的 OMNI material_id 声明，不是单纯路径相似。",
                "target_summary": _safe_text(resource_link.get("target_summary"), 520),
                "target_excerpt": _safe_text(resource_link.get("target_excerpt"), 520),
                "promotion_hint": "已升级为确认读取边；候选工作区节点仍保留，用于查看原始文件入口。",
                "evidence": [_safe_text(item, 180) for item in _list_value(resource_link.get("evidence"))[:5]],
            })
    return synthesized


def _compact_materialization_review_issue(issue: Any) -> dict[str, Any]:
    data = _dict_value(issue)
    return {
        "worker_id": _safe_text(data.get("worker_id"), 120),
        "severity": _safe_text(data.get("severity"), 32),
        "category": _safe_text(data.get("category"), 120),
        "issue": _safe_text(data.get("issue"), 420),
        "fix_hint": _safe_text(data.get("fix_hint"), 360),
        "format_in": [_safe_text(item, 220) for item in _list_value(data.get("format_in"))],
        "required_not_read": [_safe_text(item, 80) for item in _list_value(data.get("required_not_read"))],
    }


def _latest_team_builder_materialization(
    worker: str | None = None,
    material: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    root = _repo_root() / "_scratch" / "team_builder_real_material_validation"
    if not root.is_dir():
        return {"available": False, "reason": "未找到 TeamBuilder 实战验证目录。"}

    candidates = [
        path
        for path in root.iterdir()
        if path.is_dir() and (path / "summary.json").is_file() and not _team_builder_is_provider_baseline_run(path)
    ]
    if not candidates:
        return {"available": False, "reason": "验证目录下还没有 summary.json。"}

    latest = max(candidates, key=lambda path: ((path / "summary.json").stat().st_mtime, path.name))
    summary_path = latest / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"读取最新 material 化 summary 失败: {exc}") from exc

    materials = _dict_value(summary.get("materials"))
    verification = _dict_value(summary.get("verification"))
    worker_bundle = _dict_value(materials.get("worker_code_files_bundle"))
    review = _dict_value(materials.get("code_review_report"))
    external_runs = _list_value(verification.get("external_agent_runs")) or _list_value(worker_bundle.get("external_agent_runs"))

    worker_runs: list[dict[str, Any]] = []
    for run in external_runs:
        data = _dict_value(run)
        material_io_links = [_compact_materialization_link(item) for item in _list_value(data.get("material_io_links"))]
        produced_content_materials = [_compact_materialization_link(item) for item in _list_value(data.get("produced_content_materials"))]
        resource_material_links = [_compact_materialization_link(item) for item in _list_value(data.get("resource_material_links"))]
        inferred_material_read_links = [_compact_materialization_link(item) for item in _list_value(data.get("inferred_material_read_links"))]
        inferred_material_read_links.extend(
            _synthesize_declared_file_read_links(resource_material_links, inferred_material_read_links)
        )
        worker_runs.append({
            "worker_id": _safe_text(data.get("worker_id"), 120),
            "status": _safe_text(data.get("status"), 80),
            "parse_status": _safe_text(data.get("parse_status"), 120),
            "provider": _safe_text(data.get("provider"), 80),
            "run_id": _safe_text(data.get("run_id"), 140),
            "rel_path": _safe_text(data.get("rel_path"), 220),
            "changed_files": [_safe_text(item, 220) for item in _list_value(data.get("changed_files"))],
            "observed_read_targets": [_safe_text(item, 240) for item in _list_value(data.get("observed_read_targets"))[:30]],
            "tool_events": [
                _compact_materialization_tool_event(item, index)
                for index, item in enumerate(_list_value(data.get("tool_events"))[:40])
            ],
            "material_io_links": material_io_links,
            "produced_content_materials": produced_content_materials,
            "resource_material_links": resource_material_links,
            "inferred_material_read_links": inferred_material_read_links,
            "static_field_access": _compact_static_field_access(data.get("static_field_access")),
        })

    def _link_matches_filter(link: dict[str, Any]) -> bool:
        if material and material not in str(link.get("material_id") or ""):
            return False
        if target and target not in str(link.get("target") or "") and target not in str(link.get("rel_path") or ""):
            return False
        return True

    if worker or material or target:
        filtered_runs: list[dict[str, Any]] = []
        for run in worker_runs:
            if worker and worker != run.get("worker_id"):
                continue
            next_run = dict(run)
            if material or target:
                for key in ("material_io_links", "produced_content_materials", "resource_material_links", "inferred_material_read_links"):
                    next_run[key] = [link for link in run[key] if _link_matches_filter(link)]
                if not any(next_run[key] for key in ("material_io_links", "produced_content_materials", "resource_material_links", "inferred_material_read_links")):
                    continue
            filtered_runs.append(next_run)
        worker_runs = filtered_runs

    total_declared = sum(len(run["material_io_links"]) for run in worker_runs)
    total_generated = sum(len(run["produced_content_materials"]) for run in worker_runs)
    total_resources = sum(len(run["resource_material_links"]) for run in worker_runs)
    missing_required = sum(
        1
        for run in worker_runs
        if any(run["static_field_access"]["missing_input_required"].values())
        or run["static_field_access"]["missing_output_required"]
    )

    return {
        "available": True,
        "run_id": latest.name,
        "summary_path": str(summary_path.relative_to(_repo_root())),
        "provider": _safe_text(summary.get("provider"), 80),
        "started_at_local": _safe_text(summary.get("started_at_local"), 80),
        "team_name": _safe_text(summary.get("team_name"), 160),
        "review": {
            "kind": _safe_text(review.get("kind") or review.get("verdict"), 40),
            "verdict": _safe_text(review.get("verdict"), 40),
            "critical_count": review.get("critical_count") if isinstance(review.get("critical_count"), int) else 0,
            "warning_count": review.get("warning_count") if isinstance(review.get("warning_count"), int) else 0,
            "diagnosis": _safe_text(review.get("diagnosis"), 500),
            "issues": [_compact_materialization_review_issue(item) for item in _list_value(review.get("issues"))],
        },
        "counts": {
            "worker_success_count": verification.get("worker_success_count") if isinstance(verification.get("worker_success_count"), int) else worker_bundle.get("success_count", 0),
            "worker_fail_count": verification.get("worker_fail_count") if isinstance(verification.get("worker_fail_count"), int) else worker_bundle.get("fail_count", 0),
            "compile_fail_count": verification.get("compile_fail_count") if isinstance(verification.get("compile_fail_count"), int) else 0,
            "declared_material_links": total_declared,
            "generated_candidates": total_generated,
            "resource_candidates": total_resources,
            "workers_with_missing_required": missing_required,
        },
        "worker_runs": worker_runs,
    }


def _attribution_link_item(link: dict[str, Any], *, kind: str, kind_label: str, worker_id: str) -> dict[str, Any]:
    material_id = _safe_text(link.get("material_id"), 220)
    target = _safe_text(link.get("target"), 260)
    rel_path = _safe_text(link.get("rel_path"), 220)
    title = _safe_text(link.get("human_title"), 180) or material_id or rel_path or target or kind_label
    summary = _safe_text(link.get("human_summary"), 620) or _safe_text(link.get("candidate_reason"), 620)
    evidence_summary = _safe_text(link.get("evidence_summary"), 620) or _safe_text(link.get("basis"), 240)
    if not summary:
        summary = f"{kind_label}：{title}"
    if not evidence_summary:
        evidence_summary = "原始 run 里没有提供更细的判断依据，需要打开原始记录核对。"
    return {
        "kind": kind,
        "kind_label": kind_label,
        "title": title,
        "material_id": material_id,
        "direction": _safe_text(link.get("direction"), 32),
        "confidence": _safe_text(link.get("confidence"), 32),
        "registration_status": _safe_text(link.get("registration_status"), 64),
        "resource_kind": _safe_text(link.get("resource_kind"), 64),
        "target": target,
        "rel_path": rel_path,
        "summary": summary,
        "evidence_summary": evidence_summary,
        "target_summary": _safe_text(link.get("target_summary"), 620),
        "target_excerpt": _safe_text(link.get("target_excerpt"), 620),
        "promotion_hint": _safe_text(link.get("promotion_hint"), 420),
        "matched_material_ids": [_safe_text(item, 220) for item in _list_value(link.get("matched_material_ids"))[:8]],
        "declared_material_ids": [_safe_text(item, 220) for item in _list_value(link.get("declared_material_ids"))[:8]],
        "evidence": [_safe_text(item, 180) for item in _list_value(link.get("evidence"))[:5]],
        "source_filter": {
            "worker": worker_id,
            "material": material_id,
            "target": target or rel_path,
        },
    }


def _attribution_gate(
    gate_id: str,
    name: str,
    status: str,
    summary: str,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "name": name,
        "status": status,
        "summary": summary,
        "evidence": [_safe_text(item, 180) for item in (evidence or [])[:8]],
    }


def _attribution_unique_text(values: Iterable[Any], *, limit: int = 8, text_limit: int = 220) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _safe_text(value, text_limit)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _attribution_group_sample_targets(links: list[dict[str, Any]]) -> list[str]:
    return _attribution_unique_text(
        (
            link.get("rel_path")
            or link.get("target")
            or link.get("title")
            or link.get("target_summary")
            for link in links
        ),
        limit=6,
    )


def _attribution_group_material_ids(links: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for link in links:
        values.append(_safe_text(link.get("material_id"), 220))
        values.extend(_safe_text(item, 220) for item in _list_value(link.get("matched_material_ids")))
        values.extend(_safe_text(item, 220) for item in _list_value(link.get("declared_material_ids")))
    return _attribution_unique_text(values, limit=12)


def _attribution_group_evidence(links: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for link in links:
        values.append(_safe_text(link.get("evidence_summary"), 240))
        values.extend(_safe_text(item, 180) for item in _list_value(link.get("evidence")))
    return _attribution_unique_text(values, limit=6)


def _attribution_read_group(
    *,
    worker_id: str,
    group_kind: str,
    links: list[dict[str, Any]],
) -> dict[str, Any]:
    samples = _attribution_group_sample_targets(links)
    material_ids = _attribution_group_material_ids(links)
    evidence = _attribution_group_evidence(links)
    count = len(links)
    if group_kind == "confirmed":
        title = f"{worker_id} 的确认读取关系"
        status = "confirmed"
        summary = (
            f"{worker_id} 有 {count} 条读取关系已经进入事实层。"
            f"{' 已命中 ' + str(len(material_ids)) + ' 个 material。' if material_ids else ' 这些关系来自明确读取证据。'}"
        )
        decision = "可作为本次 run 的事实读取边使用。"
        next_action = "后续 doctor 和 repair 可以消费这些事实边；抽查时优先看样例路径和原始 worker JSON。"
    elif group_kind == "content_mentions":
        title = f"{worker_id} 的内容提及线索"
        status = "explanatory"
        summary = (
            f"{worker_id} 有 {count} 条线索来自已读取内容里的路径提及。"
            f"{' 这些提及可解释 ' + str(len(material_ids)) + ' 个 material 候选。' if material_ids else ' 当前没有可解释的 material。'}"
        )
        decision = "这是语义解释线索，不是 worker 直接读取或搜索命中的事实边。"
        next_action = "保留在观察台供人工理解上下文；除非后续工具命中或明确 Read 目标出现，否则不进入 doctor/repair 缺口。"
    elif group_kind == "unconfirmed":
        title = f"{worker_id} 的待确认读取线索"
        status = "candidate"
        summary = (
            f"{worker_id} 有 {count} 条工具读取或工作区接触线索仍停留在候选层。"
            f"{' 静态扫描命中 ' + str(len(material_ids)) + ' 个 material，但仍需工具输出确认。' if material_ids else ' 当前还没有可升级的 material 命中。'}"
        )
        decision = "只能作为审阅线索，不能当作事实 material 读边。"
        next_action = "继续补工具结果路径、Read 目标或文件头 material_id 证据；确认前保持候选状态。"
    else:
        title = f"{worker_id} 的工具读取线索组"
        status = "evidence"
        summary = (
            f"{worker_id} 有 {count} 条工具读取、搜索结果或工作区接触线索。"
            f"{' 其中可看到 ' + str(len(material_ids)) + ' 个 material 标识。' if material_ids else ' 当前没有直接 material 标识。'}"
        )
        decision = "这是工具接触证据汇总；是否已经成为事实读边，要看确认读取组和待确认线索。"
        next_action = "用它快速抽查工具到底接触了哪些资料；不要仅凭本组就判定正式读取关系。"
    return {
        "id": f"read_group:{worker_id}:{group_kind}",
        "worker_id": worker_id,
        "group_kind": group_kind,
        "status": status,
        "title": title,
        "summary": summary,
        "decision": decision,
        "next_action": next_action,
        "count": count,
        "material_count": len(material_ids),
        "sample_targets": samples,
        "sample_material_ids": material_ids,
        "evidence": evidence,
        "source_filter": {
            "worker": worker_id,
            "material": "",
            "target": samples[0] if samples else "",
        },
    }


def _field_contract_report(static_field_access: dict[str, Any]) -> dict[str, Any]:
    input_reads = _dict_value(static_field_access.get("input_field_reads"))
    missing_input = _dict_value(static_field_access.get("missing_input_required"))
    missing_output = [_safe_text(item, 80) for item in _list_value(static_field_access.get("missing_output_required"))]
    output_writes = [_safe_text(item, 80) for item in _list_value(static_field_access.get("output_field_writes"))]
    missing_input_total = sum(len(_list_value(fields)) for fields in missing_input.values())
    status = "fail" if missing_input_total or missing_output else "pass"
    if status == "pass":
        summary = f"required 字段读写通过：读取 {sum(len(_list_value(fields)) for fields in input_reads.values())} 个输入字段，写出 {len(output_writes)} 个输出字段。"
    else:
        summary = f"字段契约缺失：缺 {missing_input_total} 个输入字段读取，缺 {len(missing_output)} 个输出字段写入。"
    return {
        "status": status,
        "summary": summary,
        "input_field_reads": input_reads,
        "missing_input_required": missing_input,
        "missing_output_required": missing_output,
        "output_field_writes": output_writes,
    }


def _material_attribution_report(
    worker: str | None = None,
    material: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    cacheable = not worker and not material and not target
    cache_key = ""
    input_mtime = 0.0
    if cacheable:
        run_dir, _reason = _team_builder_latest_run_dir()
        cacheable = run_dir is not None
        if cacheable:
            cache_key = f"{_repo_root()}::{run_dir.name}"
            input_mtime = _team_builder_report_input_mtime(run_dir)
            cached = _TEAM_BUILDER_MATERIAL_REPORT_CACHE.get(cache_key)
            if cached and cached[0] >= input_mtime:
                return json.loads(json.dumps(cached[1], ensure_ascii=False))
    materialization = _latest_team_builder_materialization(worker=worker, material=material, target=target)
    if not materialization.get("available"):
        result = {
            "available": False,
            "reason": _safe_text(materialization.get("reason"), 300) or "暂无 TeamBuilder material 化实战结果。",
            "quality_gates": [],
            "worker_reports": [],
            "read_groups": [],
            "open_questions": [],
            "source": {"materialization_endpoint": "/api/team-builder-materialization/latest"},
        }
        if cacheable:
            _TEAM_BUILDER_MATERIAL_REPORT_CACHE[cache_key] = (input_mtime, json.loads(json.dumps(result, ensure_ascii=False)))
        return result

    worker_runs = [_dict_value(run) for run in _list_value(materialization.get("worker_runs"))]
    review = _dict_value(materialization.get("review"))
    review_issues = [_dict_value(issue) for issue in _list_value(review.get("issues"))]
    issues_by_worker: dict[str, list[dict[str, Any]]] = {}
    for issue in review_issues:
        issues_by_worker.setdefault(_safe_text(issue.get("worker_id"), 120), []).append(issue)

    worker_reports: list[dict[str, Any]] = []
    read_groups: list[dict[str, Any]] = []
    open_questions: list[dict[str, Any]] = []
    for run in worker_runs:
        worker_id = _safe_text(run.get("worker_id"), 120)
        declared = [
            _attribution_link_item(link, kind="declared_io", kind_label="声明输入/输出", worker_id=worker_id)
            for link in _list_value(run.get("material_io_links"))
        ]
        generated = [
            _attribution_link_item(link, kind="generated_artifact", kind_label="生成产物", worker_id=worker_id)
            for link in _list_value(run.get("produced_content_materials"))
        ]
        read_clues = [
            _attribution_link_item(link, kind="read_clue", kind_label="实战读取线索", worker_id=worker_id)
            for link in _list_value(run.get("resource_material_links"))
        ]
        confirmed_reads = [
            _attribution_link_item(link, kind="confirmed_read", kind_label="已确认读取", worker_id=worker_id)
            for link in _list_value(run.get("inferred_material_read_links"))
        ]
        field_contract = _field_contract_report(_dict_value(run.get("static_field_access")))
        risks: list[str] = []
        next_actions: list[str] = []
        worker_issues = issues_by_worker.get(worker_id, [])
        unconfirmed_read_clues = [
            link
            for link in read_clues
            if not link.get("declared_material_ids") and not link.get("matched_material_ids")
        ]
        content_mention_read_clues: list[dict[str, Any]] = []
        material_gap_read_clues: list[dict[str, Any]] = []
        tool_events = _list_value(run.get("tool_events"))
        for link in unconfirmed_read_clues:
            target_text = _safe_text(link.get("target") or link.get("rel_path"), 260)
            confirmation = _team_builder_tool_event_confirmation(worker_id, target_text, tool_events)
            if _safe_text(confirmation.get("status"), 120) == "content_mention_path_without_scope_event":
                confirmed_materials = [_dict_value(item) for item in _list_value(confirmation.get("confirmed_materials"))]
                mention_link = dict(link)
                mention_link["matched_material_ids"] = [
                    _safe_text(item.get("material_id"), 220)
                    for item in confirmed_materials
                    if _safe_text(item.get("material_id"), 220)
                ]
                mention_link["evidence_summary"] = _safe_text(confirmation.get("summary"), 360) or _safe_text(link.get("evidence_summary"), 360)
                mention_link["evidence"] = [
                    *[_safe_text(item, 180) for item in _list_value(link.get("evidence"))],
                    *[
                        _safe_text(item.get("basis"), 180)
                        for item in confirmed_materials
                        if _safe_text(item.get("basis"), 180)
                    ],
                ]
                content_mention_read_clues.append(mention_link)
            else:
                material_gap_read_clues.append(link)
        if read_clues:
            read_groups.append(_attribution_read_group(worker_id=worker_id, group_kind="tool_clues", links=read_clues))
        if content_mention_read_clues:
            read_groups.append(_attribution_read_group(worker_id=worker_id, group_kind="content_mentions", links=content_mention_read_clues))
        if material_gap_read_clues:
            read_groups.append(_attribution_read_group(worker_id=worker_id, group_kind="unconfirmed", links=material_gap_read_clues))
        if confirmed_reads:
            read_groups.append(_attribution_read_group(worker_id=worker_id, group_kind="confirmed", links=confirmed_reads))
        if _safe_text(run.get("status"), 80) != "succeeded":
            risks.append("worker 没有成功完成，本次产物不能视为完整闭环。")
            next_actions.append("先查看原始记录里的 provider 错误，再决定是否重跑或降级。")
        if field_contract["status"] != "pass":
            risks.append(field_contract["summary"])
            next_actions.append("优先修复 required 字段读写缺口，再重新运行 CodeReviewer。")
        if worker_issues:
            risks.extend(_safe_text(issue.get("issue"), 260) for issue in worker_issues if issue.get("issue"))
            next_actions.extend(_safe_text(issue.get("fix_hint"), 260) for issue in worker_issues if issue.get("fix_hint"))
        if material_gap_read_clues:
            risks.append(f"仍有 {len(material_gap_read_clues)} 条读取线索只作为候选，不能当成已确认 material 读边。")
            next_actions.append("目录线索先保留为资源空间；纯正则线索需要结合 grep 结果或工具输出再拆成具体 material。")
            open_questions.append({
                "worker_id": worker_id,
                "summary": f"{worker_id} 有 {len(material_gap_read_clues)} 条读取线索仍未升级为正式 material 读取边。",
                "next_action": "继续解析目录内实际访问文件、grep 结果或命令输出；不能确认前保持候选状态。",
            })
        if content_mention_read_clues:
            next_actions.append(f"{len(content_mention_read_clues)} 条路径来自读取内容里的提及，保留为解释线索，不进入 repair 缺口。")
        if not generated:
            risks.append("没有生成产物候选；如果这是代码生成 worker，需要核对 changed_files 是否丢失。")
        if not next_actions:
            next_actions.append("保留当前证据，后续在 repair 或模型比较阶段复用。")

        status = "fail" if any("缺失" in risk or "没有成功" in risk for risk in risks) else ("warning" if risks else "pass")
        if _safe_text(run.get("status"), 80) == "succeeded" and generated:
            summary = f"该 worker 成功完成，生成 {len(generated)} 个产物，留下 {len(read_clues)} 条读取线索。"
        else:
            summary = f"该 worker 状态为 {_safe_text(run.get('status'), 80) or '未知'}，需要结合风险项判断。"
        worker_reports.append({
            "worker_id": worker_id,
            "worker_name": worker_id,
            "status": status,
            "summary": summary,
            "declared_io": declared,
            "generated_artifacts": generated,
            "read_clues": read_clues,
            "confirmed_reads": confirmed_reads,
            "field_contract": field_contract,
            "tool_events": _list_value(run.get("tool_events")),
            "risks": risks,
            "next_actions": next_actions,
            "source": {
                "worker_run_id": _safe_text(run.get("run_id"), 140),
                "provider": _safe_text(run.get("provider"), 80),
                "rel_path": _safe_text(run.get("rel_path"), 220),
            },
        })

    worker_count = len(worker_reports)
    declared_count = sum(len(item["declared_io"]) for item in worker_reports)
    generated_count = sum(len(item["generated_artifacts"]) for item in worker_reports)
    read_clue_count = sum(len(item["read_clues"]) for item in worker_reports)
    confirmed_count = sum(len(item["confirmed_reads"]) for item in worker_reports)
    read_group_count = len(read_groups)
    content_mention_read_clue_count = sum(
        group["count"]
        for group in read_groups
        if group.get("group_kind") == "content_mentions"
    )
    field_fail_count = sum(1 for item in worker_reports if item["field_contract"]["status"] != "pass")
    failed_worker_count = sum(1 for item in worker_reports if item["status"] == "fail")
    unconfirmed_read_clue_count = sum(
        group["count"]
        for group in read_groups
        if group.get("group_kind") == "unconfirmed"
    )
    workers_without_generated = [item["worker_id"] for item in worker_reports if not item["generated_artifacts"]]
    workers_without_declared = [item["worker_id"] for item in worker_reports if not item["declared_io"]]
    read_clues_missing_summary = [
        link["title"]
        for item in worker_reports
        for link in item["read_clues"]
        if not link.get("summary") or not link.get("evidence_summary")
    ]
    review_kind = _safe_text(review.get("kind") or review.get("verdict"), 40)
    critical_count = review.get("critical_count") if isinstance(review.get("critical_count"), int) else 0
    warning_count = review.get("warning_count") if isinstance(review.get("warning_count"), int) else 0

    gates = [
        _attribution_gate(
            "declared_io",
            "声明输入输出可见",
            "pass" if worker_count and not workers_without_declared else "warning",
            "所有 worker 都有声明读写。" if not workers_without_declared else f"{len(workers_without_declared)} 个 worker 没有声明读写，需要确认是否合理。",
            [f"declared_io={declared_count}"] + workers_without_declared[:5],
        ),
        _attribution_gate(
            "materialized_writes",
            "生成产物已 material 化",
            "pass" if worker_count and not workers_without_generated else "fail",
            "所有 worker 的生成文件都进入生成产物候选。" if not workers_without_generated else f"{len(workers_without_generated)} 个 worker 没有生成产物候选。",
            [f"generated_artifacts={generated_count}"] + workers_without_generated[:5],
        ),
        _attribution_gate(
            "runtime_read_clues",
            "实战读取线索可解释",
            "pass" if read_clue_count and not read_clues_missing_summary else ("warning" if not read_clue_count else "fail"),
            "读取线索都有标题和判断依据。" if read_clue_count and not read_clues_missing_summary else ("没有实战读取线索。" if not read_clue_count else "部分读取线索缺少人读解释。"),
            [f"read_clues={read_clue_count}"] + read_clues_missing_summary[:5],
        ),
        _attribution_gate(
            "confirmed_reads",
            "正式读取边不伪装",
            "pass" if confirmed_count and not unconfirmed_read_clue_count else "warning",
            (
                f"已有 {confirmed_count} 条确认读取边，仍有 {unconfirmed_read_clue_count} 条读取线索只作为候选。"
                if confirmed_count and unconfirmed_read_clue_count
                else f"已有 {confirmed_count} 条确认读取边；{content_mention_read_clue_count} 条内容提及线索保留为解释层，不伪装成事实边。"
                if confirmed_count
                else "本次没有确认读取边；候选线索保持为线索，没有伪装成事实。"
            ),
            [f"confirmed_reads={confirmed_count}"],
        ),
        _attribution_gate(
            "field_contract",
            "字段级读写契约通过",
            "pass" if not field_fail_count else "fail",
            "所有 required 字段读写通过。" if not field_fail_count else f"{field_fail_count} 个 worker 存在 required 字段缺口。",
            [f"field_fail_workers={field_fail_count}"],
        ),
        _attribution_gate(
            "review_result",
            "生成审查通过",
            "pass" if review_kind == "pass" and critical_count == 0 else ("warning" if critical_count == 0 else "fail"),
            f"CodeReviewer 结果 {review_kind or '未知'}，critical {critical_count}，warning {warning_count}。",
            [f"review={review_kind}", f"critical={critical_count}", f"warning={warning_count}"],
        ),
    ]
    verdict = "fail" if failed_worker_count or any(gate["status"] == "fail" for gate in gates) else ("warning" if any(gate["status"] == "warning" for gate in gates) else "pass")
    summary = (
        f"本次 TeamBuilder run 覆盖 {worker_count} 个 worker，生成 {generated_count} 个产物，记录 {read_clue_count} 条实战读取线索。"
        f" 结论为 {verdict}。"
    )
    result = {
        "available": True,
        "run_id": _safe_text(materialization.get("run_id"), 140),
        "team_name": _safe_text(materialization.get("team_name"), 160),
        "provider": _safe_text(materialization.get("provider"), 80),
        "started_at_local": _safe_text(materialization.get("started_at_local"), 80),
        "summary": summary,
        "verdict": verdict,
        "quality_gates": gates,
        "counts": {
            "workers": worker_count,
            "declared_io": declared_count,
            "generated_artifacts": generated_count,
            "read_clues": read_clue_count,
            "confirmed_reads": confirmed_count,
            "read_groups": read_group_count,
            "unconfirmed_read_clues": unconfirmed_read_clue_count,
            "content_mention_read_clues": content_mention_read_clue_count,
            "field_contract_failures": field_fail_count,
            "review_issues": len(review_issues),
        },
        "worker_reports": worker_reports,
        "read_groups": read_groups,
        "open_questions": open_questions[:12],
        "source": {
            "summary_path": _safe_text(materialization.get("summary_path"), 260),
            "materialization_endpoint": "/api/team-builder-materialization/latest",
            "filters": {
                "worker": worker or "",
                "material": material or "",
                "target": target or "",
            },
        },
    }
    if cacheable:
        _TEAM_BUILDER_MATERIAL_REPORT_CACHE[cache_key] = (input_mtime, json.loads(json.dumps(result, ensure_ascii=False)))
    return result


def _team_builder_latest_run_dir() -> tuple[Path | None, str]:
    root = _repo_root() / "_scratch" / "team_builder_real_material_validation"
    if not root.is_dir():
        return None, "未找到 TeamBuilder 实战验证目录。"
    candidates = [
        path
        for path in root.iterdir()
        if path.is_dir() and (path / "summary.json").is_file() and not _team_builder_is_provider_baseline_run(path)
    ]
    if not candidates:
        return None, "验证目录下还没有 summary.json。"
    latest = max(candidates, key=lambda path: ((path / "summary.json").stat().st_mtime, path.name))
    return latest, ""


def _team_builder_report_input_mtime(run_dir: Path | None) -> float:
    latest = 0.0
    paths = [Path(__file__)]
    if run_dir:
        paths.extend([
            run_dir / "summary.json",
            run_dir / "materials" / "code_package.json",
            run_dir / "materials" / "team_llm_replay_result.json",
        ])
    for path in paths:
        if path is None:
            continue
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            pass
    return latest


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _test_gate(gate_id: str, name: str, status: str, summary: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": gate_id,
        "name": name,
        "status": status,
        "summary": _safe_text(summary, 520),
        "evidence": [_safe_text(item, 220) for item in (evidence or [])[:10]],
    }


def _safe_remove_tree(path: Path, root: Path) -> None:
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        raise RuntimeError(f"拒绝删除非 scratch 路径: {path}")
    if resolved.is_dir():
        shutil.rmtree(resolved)


def _copy_generated_package_for_test(code_root: Path, package_name: str, run_id: str) -> Path:
    scratch_root = (_repo_root() / "_scratch" / "team_builder_test_reports").resolve()
    target_parent = scratch_root / run_id
    package_dir = target_parent / package_name
    target_parent.mkdir(parents=True, exist_ok=True)
    if package_dir.exists():
        _safe_remove_tree(package_dir, scratch_root)
    shutil.copytree(code_root, package_dir)
    return package_dir


_TEAM_BUILDER_TEST_REPORT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TEAM_BUILDER_MATERIAL_REPORT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TEAM_BUILDER_READ_CLUE_RESOLUTION_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TEAM_BUILDER_REPAIR_PROBE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TEAM_BUILDER_REPAIR_DRY_RUN_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _team_builder_test_report_input_mtime(run_dir: Path, code_root: Path) -> float:
    latest = 0.0
    for path in (run_dir / "summary.json", run_dir / "materials" / "code_package.json", Path(__file__)):
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            pass
    if code_root.is_dir():
        for path in code_root.rglob("*"):
            if path.is_file() and not _is_skipped(path):
                try:
                    latest = max(latest, path.stat().st_mtime)
                except OSError:
                    pass
    contract_root = _repo_root() / "tests" / "teams"
    if contract_root.is_dir():
        for path in contract_root.rglob("test_contract.py"):
            if path.is_file() and not _is_skipped(path):
                try:
                    latest = max(latest, path.stat().st_mtime)
                except OSError:
                    pass
    return latest


def _run_generated_package_smoke(package_dir: Path, package_name: str) -> dict[str, Any]:
    script = r"""
import importlib
import json
import sys
from pathlib import Path

package_parent = Path(sys.argv[1])
package_name = sys.argv[2]
sys.path.insert(0, str(package_parent))
result = {"ok": False, "nodes": [], "binding_keys": [], "error": ""}
try:
    team_mod = importlib.import_module(f"{package_name}.team")
    run_mod = importlib.import_module(f"{package_name}.run")
    spec = team_mod.build_team()
    bindings = run_mod.build_bindings({})
    nodes = [getattr(node, "id", "") for node in getattr(spec, "nodes", [])]
    binding_keys = sorted(str(key) for key in bindings.keys())
    missing_bindings = sorted(node_id for node_id in nodes if node_id not in bindings)
    result.update({
        "ok": not missing_bindings and bool(nodes),
        "team_id": getattr(spec, "id", ""),
        "entry": getattr(spec, "entry", ""),
        "nodes": nodes,
        "edge_count": len(getattr(spec, "edges", []) or []),
        "binding_keys": binding_keys,
        "missing_bindings": missing_bindings,
    })
except Exception as exc:
    result["error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(result, ensure_ascii=False))
"""
    env = dict(os.environ)
    src_path = str(_repo_root() / "src")
    env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [os.sys.executable, "-c", script, str(package_dir.parent), package_name],
        cwd=str(_repo_root()),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
    )
    stdout = proc.stdout.strip()
    parsed: dict[str, Any] = {}
    if stdout:
        try:
            parsed = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            parsed = {"ok": False, "error": stdout[-500:]}
    return {
        "returncode": proc.returncode,
        "stdout": _safe_text(stdout, 1200),
        "stderr": _safe_text(proc.stderr, 1200),
        "result": parsed,
    }


def _run_generated_worker_run_smoke(package_dir: Path, package_name: str) -> dict[str, Any]:
    script = r"""
import importlib
import inspect
import json
import sys
from pathlib import Path

package_parent = Path(sys.argv[1])
package_name = sys.argv[2]
repo_root = Path(sys.argv[3])
sys.path.insert(0, str(package_parent))


def _format_tokens(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, (list, tuple, set)):
        out = []
        for item in raw:
            out.extend(_format_tokens(item))
        return list(dict.fromkeys(str(item) for item in out if item))
    return [str(raw)] if raw else []


def _kind_text(verdict):
    kind = getattr(verdict, "kind", "")
    return str(getattr(kind, "value", kind) or "")


def _jsonable(value, depth=0):
    if depth > 4:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item, depth + 1) for item in list(value)[:40]]
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(), depth + 1)
        except Exception:
            pass
    return str(value)


def _brief_output(value):
    data = _jsonable(value)
    if isinstance(data, dict):
        return {
            "type": "dict",
            "keys": list(data.keys())[:12],
            "size": len(data),
        }
    if isinstance(data, list):
        return {"type": "list", "size": len(data)}
    return {"type": type(data).__name__, "value": str(data)[:220]}


def _finding(check_id, level, worker_id, observation, implication, cross_refs=None):
    severity = {
        "blocking": "CRITICAL",
        "degrading": "HIGH",
        "advisory": "MEDIUM",
        "info": "INFO",
    }.get(level, "INFO")
    return {
        "check_id": check_id,
        "level": level,
        "severity": severity,
        "location": f"node:{worker_id}" if worker_id else "team",
        "target_kind": "node" if worker_id else "team",
        "target_id": worker_id or package_name,
        "node_ids": [worker_id] if worker_id else [],
        "material_ids": [],
        "observation": observation,
        "implication": implication,
        "cross_refs": cross_refs or [],
    }


LLM_STUB_CALLS = []


def _stub_call_llm_json(**kwargs):
    system = str(kwargs.get("system") or "")
    user = str(kwargs.get("user") or "")
    expected_keys = ["summary_cn", "risks", "next_checks"]
    LLM_STUB_CALLS.append({
        "model": str(kwargs.get("model") or ""),
        "max_tokens": kwargs.get("max_tokens"),
        "system_chars": len(system),
        "user_chars": len(user),
        "system_preview": system[:240],
        "user_preview": user[:700],
        "expected_output_keys": expected_keys,
        "stub_response_keys": expected_keys,
        "has_json_instruction": "JSON" in system.upper() or "JSON" in user.upper(),
        "has_chinese_instruction": "中文" in system or "中文" in user,
    })
    return {
        "summary_cn": "模型桩验证通过：SOFT worker 已收到输入并完成本地输出结构处理。",
        "risks": ["这不是一次真实模型质量验证。"],
        "next_checks": ["使用受控 LLM smoke 或真实模型回放验证内容质量。"],
    }


class _StubTextBlock:
    def __init__(self, text):
        self.text = text


class _StubLLMResponse:
    def __init__(self, text):
        self.content = [_StubTextBlock(text)]


class _StubLLMClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def call(self, messages=None, system=""):
        user = "\n".join(str(item.get("content", "")) for item in (messages or []) if isinstance(item, dict))
        payload = _stub_call_llm_json(
            model=self.kwargs.get("model") or self.kwargs.get("role") or "stubbed-llm-client",
            max_tokens=self.kwargs.get("max_tokens"),
            system=system,
            user=user,
        )
        return _StubLLMResponse(json.dumps(payload, ensure_ascii=False))


result = {
    "ok": False,
    "status": "warning",
    "team_id": "",
    "executed_workers": [],
    "stubbed_workers": [],
    "skipped_workers": [],
    "failed_workers": [],
    "seed_materials": [],
    "produced_materials": [],
    "llm_stub_calls": [],
    "doctor_findings": [],
    "error": "",
}

try:
    team_mod = importlib.import_module(f"{package_name}.team")
    run_mod = importlib.import_module(f"{package_name}.run")
    spec = team_mod.build_team()
    bindings = run_mod.build_bindings({})
    result["team_id"] = str(getattr(spec, "id", "") or package_name)
    node_ids = [str(getattr(node, "id", "")) for node in getattr(spec, "nodes", []) if getattr(node, "id", "")]
    if not node_ids:
        node_ids = sorted(str(key) for key in bindings.keys())

    material_values = {}
    seed_payload = {
        "team_id": result["team_id"],
        "workspace_root": str(repo_root),
        "event_sources": [
            "src/omnicompany/packages/services/_core/team_builder/team.py",
            "_scratch/team_builder_real_material_validation",
        ],
        "question": "验证生成 team 的最小运行链路。",
    }

    for worker_id in node_ids:
        worker = bindings.get(worker_id)
        if worker is None:
            result["skipped_workers"].append({
                "worker_id": worker_id,
                "reason": "missing_binding",
                "summary": "build_bindings 没有返回这个节点的 worker 实例。",
                "missing_inputs": [],
            })
            result["doctor_findings"].append(_finding(
                "team_builder.worker_run_smoke.missing_binding",
                "blocking",
                worker_id,
                "业务 run smoke 无法执行：build_bindings 缺少这个节点。",
                "生成 team 不能被可靠调度，后续测试和修复都缺少实际 worker 实例。",
            ))
            continue

        run_method = getattr(worker, "run", None)
        if not callable(run_method):
            result["skipped_workers"].append({
                "worker_id": worker_id,
                "reason": "no_run_method",
                "summary": "worker 实例没有可调用的 run 方法。",
                "missing_inputs": [],
            })
            result["doctor_findings"].append(_finding(
                "team_builder.worker_run_smoke.no_run_method",
                "degrading",
                worker_id,
                "业务 run smoke 跳过：worker 没有 run 方法。",
                "这个节点目前只能通过结构检查，不能验证真实业务行为。",
            ))
            continue

        try:
            source = inspect.getsource(worker.__class__)
        except Exception:
            source = ""
        requires_llm = "call_llm" in source or "LLMClient" in source or "client.call(" in source

        format_in = _format_tokens(getattr(worker, "FORMAT_IN", []))
        input_payload = {}
        for material_id in format_in:
            if material_id not in material_values and "observation_request" in material_id:
                material_values[material_id] = dict(seed_payload)
                result["seed_materials"].append(material_id)
            if material_id in material_values:
                input_payload[material_id] = material_values[material_id]
        missing_inputs = [material_id for material_id in format_in if material_id not in input_payload]
        if missing_inputs:
            result["skipped_workers"].append({
                "worker_id": worker_id,
                "reason": "missing_input",
                "summary": "上游 material 不足，无法安全调用 worker.run。",
                "missing_inputs": missing_inputs,
            })
            result["doctor_findings"].append(_finding(
                "team_builder.worker_run_smoke.missing_input",
                "degrading",
                worker_id,
                "业务 run smoke 跳过：缺少输入 material。",
                "生成 team 的数据流还不能把这个节点自然驱动起来，需要补齐测试样例或上游产物映射。",
                missing_inputs,
            ))
            continue

        try:
            run_mode = "real"
            llm_stub_kind = ""
            llm_call_start = len(LLM_STUB_CALLS)
            if requires_llm:
                result["skipped_workers"].append({
                    "worker_id": worker_id,
                    "reason": "requires_llm",
                    "summary": "worker 会调用 LLM，当前 smoke 不进行真实模型调用。",
                    "missing_inputs": [],
                })
                result["doctor_findings"].append(_finding(
                    "team_builder.worker_run_smoke.requires_llm",
                    "advisory",
                    worker_id,
                    "业务 run smoke 跳过：这个 worker 需要调用 LLM。",
                    "当前验证只能证明上游确定性链路可运行；完整端到端通过还需要受控 LLM smoke 或真实模型回放。",
                ))
                module = sys.modules.get(worker.__class__.__module__)
                if module is not None and hasattr(module, "call_llm_json"):
                    setattr(module, "call_llm_json", _stub_call_llm_json)
                    run_mode = "llm_stub"
                    llm_stub_kind = "call_llm_json"
                elif module is not None and hasattr(module, "LLMClient"):
                    setattr(module, "LLMClient", _StubLLMClient)
                    run_mode = "llm_stub"
                    llm_stub_kind = "LLMClient.call"
                else:
                    continue

            verdict = run_method(input_payload)
            kind = _kind_text(verdict)
            output = getattr(verdict, "output", None)
            diagnosis = getattr(verdict, "diagnosis", "") or ""
            format_out = str(getattr(worker, "FORMAT_OUT", "") or "")
            if format_out:
                material_values[format_out] = output
                result["produced_materials"].append(format_out)
            item = {
                "worker_id": worker_id,
                "kind": kind,
                "input_materials": format_in,
                "output_material": format_out,
                "diagnosis": diagnosis,
                "output_summary": _brief_output(output),
            }
            if run_mode == "llm_stub":
                item["stub"] = llm_stub_kind or "llm_stub"
                item["llm_stub_calls"] = LLM_STUB_CALLS[llm_call_start:]
                result["stubbed_workers"].append(item)
            else:
                result["executed_workers"].append(item)
            if kind == "fail":
                result["failed_workers"].append(item)
                result["doctor_findings"].append(_finding(
                    "team_builder.worker_run_smoke.stub_failed" if run_mode == "llm_stub" else "team_builder.worker_run_smoke.failed",
                    "blocking",
                    worker_id,
                    f"业务 run smoke 执行失败：{diagnosis or 'worker 返回 fail。'}",
                    "生成 team 的真实业务链路已经出现可复现失败，应进入 doctor/repair 阶段。",
                    format_in + ([format_out] if format_out else []),
                ))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            item = {
                "worker_id": worker_id,
                "kind": "exception",
                "input_materials": format_in,
                "output_material": str(getattr(worker, "FORMAT_OUT", "") or ""),
                "diagnosis": error,
                "output_summary": {},
            }
            result["failed_workers"].append(item)
            result["doctor_findings"].append(_finding(
                "team_builder.worker_run_smoke.exception",
                "blocking",
                worker_id,
                f"业务 run smoke 抛出异常：{error}",
                "生成 worker 代码存在运行时问题，不能进入修复前的通过状态。",
                format_in,
            ))

    if result["failed_workers"]:
        result["status"] = "fail"
    elif result["skipped_workers"]:
        result["status"] = "warning"
    elif result["executed_workers"]:
        result["status"] = "pass"
    else:
        result["status"] = "warning"
        result["doctor_findings"].append(_finding(
            "team_builder.worker_run_smoke.no_executed_worker",
            "degrading",
            "",
            "业务 run smoke 没有执行任何 worker。",
            "当前只完成结构验证，尚未验证真实业务行为。",
        ))
    result["ok"] = result["status"] == "pass"
    result["llm_stub_calls"] = LLM_STUB_CALLS
except Exception as exc:
    result["status"] = "fail"
    result["error"] = f"{type(exc).__name__}: {exc}"
    result["doctor_findings"].append(_finding(
        "team_builder.worker_run_smoke.crashed",
        "blocking",
        "",
        f"业务 run smoke 自身失败：{result['error']}",
        "测试设施无法完成业务验证，需要先修复测试入口或生成包结构。",
    ))

print(json.dumps(result, ensure_ascii=False))
"""
    env = dict(os.environ)
    src_path = str(_repo_root() / "src")
    env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [os.sys.executable, "-c", script, str(package_dir.parent), package_name, str(_repo_root())],
        cwd=str(_repo_root()),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=45,
    )
    stdout = proc.stdout.strip()
    parsed: dict[str, Any] = {}
    if stdout:
        try:
            parsed = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            parsed = {"ok": False, "status": "fail", "error": stdout[-500:]}
    return {
        "returncode": proc.returncode,
        "stdout": _safe_text(stdout, 1600),
        "stderr": _safe_text(proc.stderr, 1600),
        "result": parsed,
    }


def _team_builder_test_finding(
    check_id: str,
    level: str,
    location: str,
    observation: str,
    implication: str,
    *,
    node_ids: list[str] | None = None,
    material_ids: list[str] | None = None,
    cross_refs: list[str] | None = None,
) -> dict[str, Any]:
    severity = {
        "blocking": "CRITICAL",
        "degrading": "HIGH",
        "advisory": "MEDIUM",
        "info": "INFO",
    }.get(level, "INFO")
    target_kind = "node" if location.startswith("node:") else "team"
    target_id = location.split(":", 1)[1] if ":" in location else location
    return {
        "id": f"{check_id}:{target_id}",
        "check_id": check_id,
        "level": level,
        "severity": severity,
        "location": location,
        "target_kind": target_kind,
        "target_id": target_id,
        "node_ids": [_safe_text(item, 160) for item in (node_ids or [])],
        "edge_ids": [],
        "material_ids": [_safe_text(item, 220) for item in (material_ids or [])],
        "observation": _safe_text(observation, 420),
        "implication": _safe_text(implication, 420),
        "cross_refs": [_safe_text(item, 220) for item in (cross_refs or [])],
    }


def _team_builder_test_doctor_findings(
    package_name: str,
    gates: list[dict[str, Any]],
    worker_run_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for gate in gates:
        status = str(gate.get("status") or "")
        if status not in {"fail", "warning"}:
            continue
        gate_id = _safe_text(gate.get("id"), 120)
        level = "blocking" if status == "fail" else "advisory"
        findings.append(_team_builder_test_finding(
            f"team_builder.test.{gate_id}",
            level,
            f"team:{package_name}",
            f"{gate.get('name')}: {gate.get('summary')}",
            "这条发现来自 TeamBuilder 生成包验证，可作为 doctor/repair 的输入。",
            cross_refs=[gate_id],
        ))

    seen = {(item["check_id"], item["location"], item["observation"]) for item in findings}
    for raw in _list_value(worker_run_payload.get("doctor_findings")):
        item = _dict_value(raw)
        check_id = _safe_text(item.get("check_id"), 160) or "team_builder.worker_run_smoke"
        location = _safe_text(item.get("location"), 160) or f"team:{package_name}"
        observation = _safe_text(item.get("observation"), 420)
        key = (check_id, location, observation)
        if key in seen:
            continue
        seen.add(key)
        findings.append(_team_builder_test_finding(
            check_id,
            _safe_text(item.get("level"), 40) or "advisory",
            location,
            observation,
            _safe_text(item.get("implication"), 420),
            node_ids=[_safe_text(node_id, 160) for node_id in _list_value(item.get("node_ids"))],
            material_ids=[_safe_text(material_id, 220) for material_id in _list_value(item.get("material_ids"))],
            cross_refs=[_safe_text(ref, 220) for ref in _list_value(item.get("cross_refs"))],
        ))
    return findings


def _team_builder_contract_findings(package_name: str, contract_coverage: dict[str, Any]) -> list[dict[str, Any]]:
    if not contract_coverage.get("available"):
        return []
    counts = _dict_value(contract_coverage.get("counts"))
    matching_contracts = int(counts.get("matching_contracts") or 0)
    executed_contracts = int(counts.get("executed_contracts") or 0)
    if matching_contracts <= 0:
        return [
            _team_builder_test_finding(
                "team_builder.contract.coverage_missing",
                "advisory",
                f"team:{package_name}",
                "当前 generated team 没有同名 tests/teams contract；不能把 smoke test 等同于 acceptance。",
                "需要先补充同名 contract，定义输入样例、期望输出和失败样例；这不是代码缺陷，不应直接改 worker。",
                cross_refs=["contract_coverage", "tests/teams"],
            )
        ]
    if executed_contracts <= 0:
        return [
            _team_builder_test_finding(
                "team_builder.contract.execution_not_run",
                "advisory",
                f"team:{package_name}",
                f"当前 generated team 已匹配 {matching_contracts} 个 contract，但尚未显式执行。",
                "需要通过显式执行入口运行 contract，并把结果回写为 material；页面刷新不能自动执行 pytest。",
                cross_refs=["contract_coverage", "contract_execution"],
            )
        ]
    if _safe_text(contract_coverage.get("verdict"), 40) != "fail":
        return []

    latest_execution = _dict_value(contract_coverage.get("latest_execution"))
    contracts = [_dict_value(item) for item in _list_value(latest_execution.get("contracts"))]
    failed_contracts = [item for item in contracts if _safe_text(item.get("status"), 40) != "pass"]
    if not failed_contracts:
        failed_contracts = [{
            "slug": package_name,
            "path": _safe_text(_dict_value(latest_execution.get("source")).get("contract_execution_material"), 260),
            "returncode": "",
            "stdout_tail": _safe_text(latest_execution.get("summary"), 520),
            "stderr_tail": "",
        }]
    findings: list[dict[str, Any]] = []
    for item in failed_contracts:
        slug = _safe_text(item.get("slug"), 120) or package_name
        path = _safe_text(item.get("path"), 260)
        returncode = _safe_text(item.get("returncode"), 40)
        stdout_tail = _safe_text(item.get("stdout_tail"), 420)
        stderr_tail = _safe_text(item.get("stderr_tail"), 420)
        evidence = stderr_tail or stdout_tail or _safe_text(latest_execution.get("summary"), 420)
        findings.append(_team_builder_test_finding(
            "team_builder.contract.execution_failed",
            "blocking",
            f"team:{package_name}:{slug}",
            f"contract 执行失败: {path or slug}，返回码 {returncode or '未知'}。{evidence}",
            "acceptance 已明确失败，应进入 repair_required 的补丁计划和人工审阅流程；仍不能自动修改真实 generated code。",
            cross_refs=[path, "contract_execution", _safe_text(item.get("command"), 260)],
        ))
    return findings


def _team_builder_material_read_group_findings(material_report: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not material_report.get("available"):
        return findings
    for raw_group in _list_value(material_report.get("read_groups")):
        group = _dict_value(raw_group)
        if _safe_text(group.get("group_kind"), 80) != "unconfirmed":
            continue
        worker_id = _safe_text(group.get("worker_id"), 160)
        count = group.get("count") if isinstance(group.get("count"), int) else 0
        sample_material_ids = [_safe_text(item, 220) for item in _list_value(group.get("sample_material_ids"))[:6]]
        sample_targets = [_safe_text(item, 220) for item in _list_value(group.get("sample_targets"))[:4]]
        observation = (
            f"{_safe_text(group.get('title'), 180) or worker_id}: "
            f"{_safe_text(group.get('summary'), 300)}"
        )
        if sample_targets:
            observation = f"{observation} 样例: {'; '.join(sample_targets[:2])}"
        implication = (
            f"{_safe_text(group.get('decision'), 240)} "
            f"{_safe_text(group.get('next_action'), 240)}"
        ).strip()
        findings.append(_team_builder_test_finding(
            "team_builder.material.unconfirmed_read_group",
            "advisory",
            f"node:{worker_id}" if worker_id else "team:material_attribution",
            observation or f"仍有 {count} 条读取线索未确认。",
            implication or "material 归因仍有验证缺口，不能直接作为 repair 事实依据。",
            node_ids=[worker_id] if worker_id else [],
            material_ids=sample_material_ids,
            cross_refs=[_safe_text(group.get("id"), 220), "read_groups", "material_report"],
        ))
    return findings


def _team_builder_target_candidate_paths(target: str) -> list[Path]:
    normalized = _materialization_normalized_target(target)
    direct = _materialization_workspace_path(target, normalized)
    candidates: list[Path] = []
    if direct:
        candidates.append(direct)
    value = _materialization_target_value(target).replace("\\", "/")
    marker = "src/omnicompany/packages/services/"
    if marker in value:
        tail = value.split(marker, 1)[1].strip("/")
        services_root = _repo_root() / "src" / "omnicompany" / "packages" / "services"
        tail_candidates = [tail]
        if tail.startswith("workflow_factory/"):
            suffix = tail.removeprefix("workflow_factory/")
            tail_candidates.extend([
                f"_core/team_builder/{suffix}",
                f"_core/workflow_factory/{suffix}",
            ])
        if tail.startswith("_core/workflow_factory/"):
            suffix = tail.removeprefix("_core/workflow_factory/")
            tail_candidates.append(f"_core/team_builder/{suffix}")
        if tail.startswith("team_builder/"):
            suffix = tail.removeprefix("team_builder/")
            tail_candidates.append(f"_core/team_builder/{suffix}")
        if services_root.is_dir() and tail:
            for path in services_root.rglob(Path(tail).name):
                if _is_skipped(path) or not path.is_file():
                    continue
                rel = path.relative_to(services_root).as_posix()
                if any(rel.endswith(candidate) for candidate in tail_candidates):
                    candidates.append(path)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique[:8]


def _team_builder_target_resolution_note(target: str, candidates: list[Path]) -> tuple[str, str]:
    value = _materialization_target_value(target).replace("\\", "/")
    rels = [_materialization_relpath(path) for path in candidates]
    if "services/workflow_factory/" in value and any("services/_core/team_builder/" in rel for rel in rels):
        return (
            "renamed_alias",
            "旧 workflow_factory 路径已改名并迁移到 team_builder；当前仓库通过兼容 shim 保留 import 路径，本验证映射到当前 team_builder 实现文件。",
        )
    if "services/_core/workflow_factory/" in value and any("services/_core/team_builder/" in rel for rel in rels):
        return (
            "renamed_alias",
            "旧 _core/workflow_factory shim 子模块指向 team_builder；本验证映射到当前 team_builder 实现文件。",
        )
    if candidates:
        normalized = _materialization_normalized_target(target).replace("\\", "/")
        direct_rels = [_materialization_relpath(path).replace("\\", "/") for path in candidates]
        if any(rel.endswith(normalized) for rel in direct_rels if normalized):
            return ("direct_path", "目标路径直接解析到当前仓库文件。")
        return ("relocated_path", "目标路径未直接存在，但可按当前仓库目录迁移规则解析到同名文件。")
    return ("missing_path", "目标路径在当前仓库中没有找到可解释的当前文件。")


def _team_builder_material_gap_validation_report() -> dict[str, Any]:
    material_report = _material_attribution_report()
    if not material_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(material_report.get("reason"), 500),
            "run_id": _safe_text(material_report.get("run_id"), 160),
            "team_name": _safe_text(material_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无 material 归因报告，无法验证未确认读取组。",
            "counts": {"groups": 0, "targets": 0, "resolved_targets": 0, "material_id_hits": 0, "missing_targets": 0},
            "groups": [],
            "source": {"material_report_endpoint": "/api/team-builder-materialization/report/latest"},
        }

    groups: list[dict[str, Any]] = []
    target_count = 0
    resolved_targets = 0
    material_hit_count = 0
    missing_targets = 0
    relocated_targets = 0
    for raw_group in _list_value(material_report.get("read_groups")):
        group = _dict_value(raw_group)
        if _safe_text(group.get("group_kind"), 80) != "unconfirmed":
            continue
        target_results: list[dict[str, Any]] = []
        for raw_target in _list_value(group.get("sample_targets")):
            target = _safe_text(raw_target, 260)
            if not target:
                continue
            target_count += 1
            candidates = _team_builder_target_candidate_paths(target)
            examples = [_materialization_file_review_example(path) for path in candidates[:4]]
            material_ids = sorted({
                _safe_text(material_id, 220)
                for example in examples
                for material_id in _list_value(example.get("material_ids"))
                if _safe_text(material_id, 220)
            })
            if candidates:
                resolved_targets += 1
            else:
                missing_targets += 1
            if material_ids:
                material_hit_count += 1
            status = "material_id_found" if material_ids else "path_resolved_no_material_id" if candidates else "target_not_found"
            resolution_kind, resolution_note = _team_builder_target_resolution_note(target, candidates)
            if resolution_kind in {"renamed_alias", "relocated_path"}:
                relocated_targets += 1
            target_results.append({
                "target": target,
                "status": status,
                "resolution_kind": resolution_kind,
                "resolution_note": resolution_note,
                "resolved_paths": [_materialization_relpath(path) for path in candidates[:4]],
                "material_ids": material_ids[:8],
                "examples": examples,
                "decision": (
                    f"{resolution_note} 当前文件能解析且有 material_id；仍需真实工具输出证明该文件确实被本次 worker 命中。"
                    if material_ids
                    else f"{resolution_note} 目标路径可解析但未发现 material_id；只能保留为 workspace 资源线索。"
                    if candidates
                    else f"{resolution_note} 可能是旧路径、生成产物路径或工具输出不完整。"
                ),
            })
        groups.append({
            "id": _safe_text(group.get("id"), 220),
            "worker_id": _safe_text(group.get("worker_id"), 160),
            "title": _safe_text(group.get("title"), 180),
            "status": "partial" if target_results and any(item["status"] == "material_id_found" for item in target_results) else "unresolved",
            "summary": f"复核 {len(target_results)} 个样例目标，{sum(1 for item in target_results if item['material_ids'])} 个找到 material_id，{sum(1 for item in target_results if item['status'] == 'target_not_found')} 个当前路径缺失。",
            "targets": target_results,
        })

    verdict = "pass" if target_count and material_hit_count == target_count else "warning" if material_hit_count else "fail" if target_count else "pass"
    run_id = _safe_text(material_report.get("run_id"), 160)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(material_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": (
            f"未确认读取组只读验证: {target_count} 个样例目标，"
            f"{resolved_targets} 个能解析到当前仓库文件，{material_hit_count} 个找到 material_id，"
            f"{missing_targets} 个当前路径缺失。"
        ),
        "counts": {
            "groups": len(groups),
            "targets": target_count,
            "resolved_targets": resolved_targets,
            "relocated_targets": relocated_targets,
            "material_id_hits": material_hit_count,
            "missing_targets": missing_targets,
        },
        "groups": groups,
        "source": {
            "material_report_endpoint": "/api/team-builder-materialization/report/latest",
            "material_gap_validation_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_material_gap_validation_report.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        out_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_material_gap_validation_report.json"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_contract_coverage_report(package_name: str, run_id: str, run_dir: Path | None) -> dict[str, Any]:
    contract_root = _repo_root() / "tests" / "teams"
    available_contracts: list[dict[str, Any]] = []
    if contract_root.is_dir():
        for path in sorted(contract_root.glob("*/test_contract.py")):
            if _is_skipped(path):
                continue
            slug = path.parent.name
            pipeline_name = ""
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            match = re.search(r"PIPELINE_NAME\s*=\s*['\"]([^'\"]+)['\"]", text)
            if match:
                pipeline_name = _safe_text(match.group(1), 160)
            available_contracts.append({
                "slug": _safe_text(slug, 160),
                "pipeline_name": pipeline_name,
                "path": _materialization_relpath(path),
                "mode": "pytest_team_contract",
                "status": "available",
            })

    normalized_names = {
        _safe_text(package_name, 160),
        _safe_text(package_name, 160).replace("-", "_"),
        _safe_text(package_name, 160).replace("_", "-"),
    }
    matching_contracts = [
        item for item in available_contracts
        if item["slug"] in normalized_names
        or item["pipeline_name"] in normalized_names
        or item["pipeline_name"].replace("-", "_") in normalized_names
        or item["pipeline_name"].replace("_", "-") in normalized_names
    ]
    execution_report = _read_json_file(run_dir / "materials" / "team_contract_execution_result.json") if run_dir else {}
    execution_counts = _dict_value(execution_report.get("counts"))
    executed_contracts = int(execution_counts.get("executed_contracts") or 0)
    execution_verdict = _safe_text(execution_report.get("verdict"), 40)
    if matching_contracts and execution_report.get("available"):
        status = "executed"
        verdict = execution_verdict if execution_verdict in {"pass", "warning", "fail"} else "warning"
        summary = (
            f"找到 {len(matching_contracts)} 个与 {package_name} 匹配的 team contract；"
            f"最近一次显式执行结果为 {verdict}，执行 {executed_contracts} 个 contract。"
        )
    elif matching_contracts:
        status = "configured"
        verdict = "warning"
        summary = (
            f"找到 {len(matching_contracts)} 个与 {package_name} 匹配的 team contract；"
            "但还没有显式执行结果 material。"
        )
    elif available_contracts:
        status = "missing_contract"
        verdict = "warning"
        summary = (
            f"当前 generated team `{package_name}` 没有同名 tests/teams contract；"
            f"仓库已有 {len(available_contracts)} 个其他 team contract，可作为格式参考。"
        )
    else:
        status = "no_contract_registry"
        verdict = "warning"
        summary = "当前仓库没有发现 tests/teams/<team>/test_contract.py，team acceptance 覆盖尚未建立。"

    gates = [
        _test_gate(
            "contract_registry_visible",
            "contract 注册表可见",
            "pass" if contract_root.is_dir() else "warning",
            f"发现 {len(available_contracts)} 个 tests/teams contract。"
            if contract_root.is_dir() else "没有 tests/teams 目录或 contract 文件。",
            [item["path"] for item in available_contracts[:6]],
        ),
        _test_gate(
            "generated_team_contract_configured",
            "当前 generated team 有 contract",
            "pass" if matching_contracts else "warning",
            f"{package_name} 已匹配 contract: {', '.join(item['slug'] for item in matching_contracts)}。"
            if matching_contracts else f"{package_name} 还没有同名 contract；不能把 smoke test 等同于 acceptance。",
            [item["slug"] for item in matching_contracts],
        ),
        _test_gate(
            "contract_execution_explicit",
            "contract 执行需要显式触发",
            "pass" if execution_report.get("available") else "warning",
            "已发现显式 contract 执行结果；覆盖报告本身仍不会在页面刷新时自动执行 pytest。"
            if execution_report.get("available") else "覆盖报告只识别 contract，不在页面刷新时自动执行 pytest 或真实 pipeline。",
            ["pytest --team-mode=programmatic tests/teams/<team>/test_contract.py"],
        ),
    ]
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": package_name,
        "verdict": verdict,
        "status": status,
        "summary": summary,
        "counts": {
            "available_contracts": len(available_contracts),
            "matching_contracts": len(matching_contracts),
            "executed_contracts": executed_contracts,
            "missing_contracts": 0 if matching_contracts else 1,
        },
        "quality_gates": gates,
        "matching_contracts": matching_contracts,
        "available_contracts": available_contracts,
        "latest_execution": execution_report if execution_report.get("available") else {},
        "next_action": (
            "查看最近一次 contract execution material，并把失败项作为 doctor 输入。"
            if execution_report.get("available")
            else "用匹配 contract 显式运行 pytest，并把结果回写为 test/doctor material。"
            if matching_contracts
            else f"为 {package_name} 新增 tests/teams/{_to_snake_identifier(package_name)}/test_contract.py，定义输入样例、期望输出和失败样例。"
        ),
        "source": {
            "contract_root": _materialization_relpath(contract_root) if contract_root.exists() else "",
            "contract_coverage_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_contract_coverage.json"
            ).relative_to(_repo_root())) if run_id else "",
            "contract_execution_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_contract_execution_result.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_dir and run_id:
        out_path = run_dir / "materials" / "team_contract_coverage.json"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_execute_contracts_report() -> dict[str, Any]:
    run_dir, reason = _team_builder_latest_run_dir()
    if not run_dir:
        return {"available": False, "reason": reason, "quality_gates": [], "source": {}}
    run_id = run_dir.name
    code_package = _read_json_file(run_dir / "materials" / "code_package.json")
    package_name = _safe_text(code_package.get("team_name"), 120) or "generated_team"
    coverage = _team_builder_contract_coverage_report(package_name, run_id, run_dir)
    matching_contracts = [_dict_value(item) for item in _list_value(coverage.get("matching_contracts"))]
    if not matching_contracts:
        report = {
            "available": True,
            "run_id": run_id,
            "team_name": package_name,
            "verdict": "warning",
            "status": "no_matching_contract",
            "summary": f"{package_name} 没有同名 tests/teams contract，无法执行 acceptance。",
            "counts": {
                "matching_contracts": 0,
                "executed_contracts": 0,
                "passed_contracts": 0,
                "failed_contracts": 0,
            },
            "contracts": [],
            "quality_gates": [
                _test_gate(
                    "matching_contract_present",
                    "存在同名 contract",
                    "warning",
                    f"{package_name} 没有同名 contract；需要先新增 tests/teams/{_to_snake_identifier(package_name)}/test_contract.py。",
                    [],
                )
            ],
            "source": {
                **(_dict_value(coverage.get("source"))),
                "contract_coverage_endpoint": "/api/team-builder-materialization/test-report/latest",
            },
        }
    else:
        env = dict(os.environ)
        src_path = str(_repo_root() / "src")
        env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        contract_results: list[dict[str, Any]] = []
        for item in matching_contracts:
            rel_path = _safe_text(item.get("path"), 320)
            cmd = [
                os.sys.executable,
                "-m",
                "pytest",
                "-q",
                rel_path,
                "--team-mode=programmatic",
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(_repo_root()),
                    env=env,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=180,
                )
                returncode = proc.returncode
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
                status = "pass" if returncode == 0 else "fail"
            except subprocess.TimeoutExpired as exc:
                returncode = -1
                stdout = exc.stdout if isinstance(exc.stdout, str) else ""
                stderr = exc.stderr if isinstance(exc.stderr, str) else ""
                status = "fail"
                stderr = (stderr + "\nTimeoutExpired: contract execution exceeded 180s").strip()
            contract_results.append({
                "slug": _safe_text(item.get("slug"), 160),
                "pipeline_name": _safe_text(item.get("pipeline_name"), 160),
                "path": rel_path,
                "status": status,
                "returncode": returncode,
                "command": " ".join(cmd),
                "stdout_tail": _safe_text(stdout[-1600:], 1600),
                "stderr_tail": _safe_text(stderr[-1600:], 1600),
            })
        failed = [item for item in contract_results if item["status"] != "pass"]
        verdict = "fail" if failed else "pass"
        report = {
            "available": True,
            "run_id": run_id,
            "team_name": package_name,
            "verdict": verdict,
            "status": "executed",
            "summary": (
                f"contract 显式执行 {verdict}: 匹配 {len(matching_contracts)} 个，"
                f"通过 {len(contract_results) - len(failed)} 个，失败 {len(failed)} 个。"
            ),
            "counts": {
                "matching_contracts": len(matching_contracts),
                "executed_contracts": len(contract_results),
                "passed_contracts": len(contract_results) - len(failed),
                "failed_contracts": len(failed),
            },
            "contracts": contract_results,
            "quality_gates": [
                _test_gate(
                    "matching_contract_present",
                    "存在同名 contract",
                    "pass",
                    f"找到 {len(matching_contracts)} 个同名 contract。",
                    [item["path"] for item in matching_contracts],
                ),
                _test_gate(
                    "contract_pytest_passed",
                    "contract pytest 通过",
                    "pass" if not failed else "fail",
                    "所有匹配 contract 已通过。"
                    if not failed else f"{len(failed)} 个 contract 执行失败。",
                    [item["path"] for item in failed],
                ),
            ],
            "source": {
                **(_dict_value(coverage.get("source"))),
                "contract_coverage_endpoint": "/api/team-builder-materialization/test-report/latest",
            },
        }
    out_path = run_dir / "materials" / "team_contract_execution_result.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        report["source"]["contract_execution_material"] = str(out_path.relative_to(_repo_root()))
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    _TEAM_BUILDER_TEST_REPORT_CACHE.pop(str(run_dir.resolve()), None)
    return report


def _team_builder_latest_contract_execution_report() -> dict[str, Any]:
    run_dir, reason = _team_builder_latest_run_dir()
    if not run_dir:
        return {"available": False, "reason": reason, "quality_gates": [], "source": {}}
    report = _read_json_file(run_dir / "materials" / "team_contract_execution_result.json")
    if report.get("available"):
        return report
    code_package = _read_json_file(run_dir / "materials" / "code_package.json")
    package_name = _safe_text(code_package.get("team_name"), 120) or "generated_team"
    return {
        "available": False,
        "reason": "还没有显式执行 team contract；请调用 POST /api/team-builder-materialization/contract-execution/execute。",
        "run_id": run_dir.name,
        "team_name": package_name,
        "verdict": "not_run",
        "status": "not_run",
        "counts": {
            "matching_contracts": 0,
            "executed_contracts": 0,
            "passed_contracts": 0,
            "failed_contracts": 0,
        },
        "contracts": [],
        "quality_gates": [],
        "source": {
            "contract_execution_material": str((run_dir / "materials" / "team_contract_execution_result.json").relative_to(_repo_root())),
        },
    }


def _team_builder_test_report() -> dict[str, Any]:
    run_dir, reason = _team_builder_latest_run_dir()
    if not run_dir:
        return {"available": False, "reason": reason, "quality_gates": [], "source": {}}
    run_id = run_dir.name
    code_root = run_dir / "code_package_files"
    if not code_root.is_dir():
        return {
            "available": False,
            "reason": "最新 TeamBuilder run 没有 code_package_files 目录。",
            "run_id": run_id,
            "quality_gates": [],
            "source": {"run_dir": str(run_dir.relative_to(_repo_root()))},
        }
    input_mtime = _team_builder_test_report_input_mtime(run_dir, code_root)
    cache_key = str(run_dir.resolve())
    cached = _TEAM_BUILDER_TEST_REPORT_CACHE.get(cache_key)
    if cached and cached[0] >= input_mtime:
        return json.loads(json.dumps(cached[1], ensure_ascii=False))

    code_package = _read_json_file(run_dir / "materials" / "code_package.json")
    package_name = _safe_text(code_package.get("team_name"), 120) or "generated_team"
    files = sorted(
        path.relative_to(code_root).as_posix()
        for path in code_root.rglob("*")
        if path.is_file() and not _is_skipped(path)
    )
    required_files = ["formats.py", "team.py", "run.py", "__init__.py", "workers/__init__.py", "DESIGN.md", ".omni/workspace.yaml"]
    missing_required = [name for name in required_files if name not in files]
    worker_files = [name for name in files if name.startswith("workers/") and name.endswith(".py") and name != "workers/__init__.py"]
    py_files = [code_root / name for name in files if name.endswith(".py")]

    syntax_failures: list[dict[str, str]] = []
    for path in py_files:
        try:
            compile(path.read_text(encoding="utf-8", errors="ignore"), str(path), "exec")
        except SyntaxError as exc:
            syntax_failures.append({
                "file": path.relative_to(code_root).as_posix(),
                "error": f"{exc.__class__.__name__}: {exc.msg} at line {exc.lineno}",
            })
        except OSError as exc:
            syntax_failures.append({
                "file": path.relative_to(code_root).as_posix(),
                "error": f"{exc.__class__.__name__}: {exc}",
            })

    smoke_result: dict[str, Any] = {}
    package_dir: Path | None = None
    if not missing_required and not syntax_failures:
        try:
            package_dir = _copy_generated_package_for_test(code_root, package_name, run_id)
            smoke_result = _run_generated_package_smoke(package_dir, package_name)
        except Exception as exc:
            smoke_result = {
                "returncode": -1,
                "stdout": "",
                "stderr": _safe_text(f"{type(exc).__name__}: {exc}", 1200),
                "result": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            }
    else:
        smoke_result = {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "result": {"ok": False, "error": "文件清单或语法检查未通过，跳过导入构建 smoke。"},
        }

    smoke_payload = _dict_value(smoke_result.get("result"))
    missing_bindings = [_safe_text(item, 120) for item in _list_value(smoke_payload.get("missing_bindings"))]
    worker_run_result: dict[str, Any]
    if package_dir and smoke_result.get("returncode") == 0 and smoke_payload.get("ok") and not missing_bindings:
        try:
            worker_run_result = _run_generated_worker_run_smoke(package_dir, package_name)
        except Exception as exc:
            worker_run_result = {
                "returncode": -1,
                "stdout": "",
                "stderr": _safe_text(f"{type(exc).__name__}: {exc}", 1600),
                "result": {
                    "ok": False,
                    "status": "fail",
                    "error": f"{type(exc).__name__}: {exc}",
                    "executed_workers": [],
                    "stubbed_workers": [],
                    "skipped_workers": [],
                    "failed_workers": [],
                    "llm_stub_calls": [],
                    "doctor_findings": [],
                },
            }
    else:
        worker_run_result = {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "result": {
                "ok": False,
                "status": "fail",
                "error": "导入构建 smoke 未通过，跳过 worker 业务 run smoke。",
                "executed_workers": [],
                "stubbed_workers": [],
                "skipped_workers": [],
                "failed_workers": [],
                "llm_stub_calls": [],
                "doctor_findings": [],
            },
        }
    worker_run_payload = _dict_value(worker_run_result.get("result"))
    worker_run_status = _safe_text(worker_run_payload.get("status"), 40) or "fail"
    if worker_run_result.get("returncode") not in (0, None) and worker_run_status != "fail":
        worker_run_status = "fail"
    if worker_run_status not in {"pass", "warning", "fail"}:
        worker_run_status = "fail"
    executed_workers = [_dict_value(item) for item in _list_value(worker_run_payload.get("executed_workers"))]
    stubbed_workers = [_dict_value(item) for item in _list_value(worker_run_payload.get("stubbed_workers"))]
    skipped_workers = [_dict_value(item) for item in _list_value(worker_run_payload.get("skipped_workers"))]
    failed_workers = [_dict_value(item) for item in _list_value(worker_run_payload.get("failed_workers"))]
    worker_run_summary = (
        f"已执行 {len(executed_workers)} 个 worker，模型桩验证 {len(stubbed_workers)} 个，跳过真实调用 {len(skipped_workers)} 个，失败 {len(failed_workers)} 个。"
    )
    gates = [
        _test_gate(
            "package_manifest",
            "生成包文件清单完整",
            "pass" if not missing_required and worker_files else "fail",
            f"发现 {len(files)} 个文件、{len(worker_files)} 个 worker 文件。"
            if not missing_required and worker_files else f"缺少必要文件: {', '.join(missing_required) or 'worker 文件'}",
            files[:10],
        ),
        _test_gate(
            "syntax_compile",
            "Python 语法编译通过",
            "pass" if not syntax_failures else "fail",
            f"{len(py_files)} 个 Python 文件语法检查通过。" if not syntax_failures else f"{len(syntax_failures)} 个 Python 文件语法失败。",
            [f"{item['file']}: {item['error']}" for item in syntax_failures],
        ),
        _test_gate(
            "import_build_team",
            "隔离导入和 build_team 通过",
            "pass" if smoke_result.get("returncode") == 0 and smoke_payload.get("team_id") else "fail",
            f"build_team 返回 {smoke_payload.get('team_id')}, 节点 {len(_list_value(smoke_payload.get('nodes')))}, 边 {smoke_payload.get('edge_count', 0)}。"
            if smoke_result.get("returncode") == 0 and smoke_payload.get("team_id") else f"导入或 build_team 失败: {smoke_payload.get('error') or smoke_result.get('stderr')}",
            [_safe_text(smoke_result.get("stderr"), 300)] if smoke_result.get("stderr") else [],
        ),
        _test_gate(
            "build_bindings",
            "build_bindings 覆盖 team 节点",
            "pass" if smoke_payload.get("ok") and not missing_bindings else "fail",
            f"bindings 覆盖 {len(_list_value(smoke_payload.get('binding_keys')))} 个节点。"
            if smoke_payload.get("ok") else f"bindings 缺失: {', '.join(missing_bindings) or smoke_payload.get('error') or '未知错误'}",
            _list_value(smoke_payload.get("binding_keys"))[:10],
        ),
        _test_gate(
            "worker_run_smoke",
            "worker 业务 run smoke",
            worker_run_status,
            worker_run_summary
            if worker_run_result.get("returncode") in (0, None) else f"worker 业务 run smoke 设施失败: {worker_run_payload.get('error') or worker_run_result.get('stderr')}",
            [
                *[
                    f"{_safe_text(item.get('worker_id'), 120)}={_safe_text(item.get('kind'), 40)}"
                    for item in executed_workers
                ],
                *[
                    f"{_safe_text(item.get('worker_id'), 120)} 模型桩={_safe_text(item.get('kind'), 40)}"
                    for item in stubbed_workers
                ],
                *[
                    f"{_safe_text(item.get('worker_id'), 120)} 跳过: {_safe_text(item.get('reason'), 80)}"
                    for item in skipped_workers
                ],
                *[
                    f"{_safe_text(item.get('worker_id'), 120)} 失败: {_safe_text(item.get('diagnosis'), 140)}"
                    for item in failed_workers
                ],
            ],
        ),
    ]
    verdict = "fail" if any(gate["status"] == "fail" for gate in gates) else "warning" if any(gate["status"] == "warning" for gate in gates) else "pass"
    summary = (
        f"生成包测试 {verdict}: 文件 {len(files)} 个, Python {len(py_files)} 个, "
        f"worker {len(worker_files)} 个；业务 smoke 执行 {len(executed_workers)} 个，模型桩 {len(stubbed_workers)} 个，跳过真实调用 {len(skipped_workers)} 个。"
    )
    doctor_findings = _team_builder_test_doctor_findings(package_name, gates, worker_run_payload)
    contract_coverage = _team_builder_contract_coverage_report(package_name, run_id, run_dir)
    contract_coverage_source = _dict_value(contract_coverage.get("source"))
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": package_name,
        "summary": summary,
        "verdict": verdict,
        "quality_gates": gates,
        "counts": {
            "files": len(files),
            "python_files": len(py_files),
            "worker_files": len(worker_files),
            "syntax_failures": len(syntax_failures),
            "nodes": len(_list_value(smoke_payload.get("nodes"))),
            "bindings": len(_list_value(smoke_payload.get("binding_keys"))),
            "executed_workers": len(executed_workers),
            "stubbed_workers": len(stubbed_workers),
            "skipped_workers": len(skipped_workers),
            "failed_workers": len(failed_workers),
            "doctor_findings": len(doctor_findings),
        },
        "smoke": {
            "team_id": _safe_text(smoke_payload.get("team_id"), 160),
            "entry": _safe_text(smoke_payload.get("entry"), 120),
            "nodes": [_safe_text(item, 120) for item in _list_value(smoke_payload.get("nodes"))],
            "binding_keys": [_safe_text(item, 120) for item in _list_value(smoke_payload.get("binding_keys"))],
            "missing_bindings": missing_bindings,
            "error": _safe_text(smoke_payload.get("error"), 500),
        },
        "worker_run_smoke": {
            "status": worker_run_status,
            "executed_workers": executed_workers,
            "stubbed_workers": stubbed_workers,
            "skipped_workers": skipped_workers,
            "failed_workers": failed_workers,
            "seed_materials": [_safe_text(item, 220) for item in _list_value(worker_run_payload.get("seed_materials"))],
            "produced_materials": [_safe_text(item, 220) for item in _list_value(worker_run_payload.get("produced_materials"))],
            "llm_stub_calls": [_dict_value(item) for item in _list_value(worker_run_payload.get("llm_stub_calls"))],
            "error": _safe_text(worker_run_payload.get("error") or worker_run_result.get("stderr"), 700),
        },
        "doctor_findings": doctor_findings,
        "contract_coverage": contract_coverage,
        "source": {
            "code_package_files": str(code_root.relative_to(_repo_root())),
            "test_package_dir": str(package_dir.relative_to(_repo_root())) if package_dir else "",
            "report_material": str((run_dir / "materials" / "team_test_report.json").relative_to(_repo_root())),
            "doctor_findings_material": str((run_dir / "materials" / "team_doctor_findings.json").relative_to(_repo_root())),
            "contract_coverage_material": _safe_text(contract_coverage_source.get("contract_coverage_material"), 300),
            "contract_execution_material": _safe_text(contract_coverage_source.get("contract_execution_material"), 300),
        },
    }
    report_path = run_dir / "materials" / "team_test_report.json"
    doctor_findings_path = run_dir / "materials" / "team_doctor_findings.json"
    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    try:
        doctor_findings_path.write_text(
            json.dumps({
                "run_id": run_id,
                "team_name": package_name,
                "source_report": str(report_path.relative_to(_repo_root())),
                "findings": doctor_findings,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    _TEAM_BUILDER_TEST_REPORT_CACHE[cache_key] = (input_mtime, json.loads(json.dumps(report, ensure_ascii=False)))
    return report


def _team_builder_latest_doctor_findings_report() -> dict[str, Any]:
    report = _team_builder_test_report()
    if not report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(report.get("reason"), 500),
            "run_id": _safe_text(report.get("run_id"), 160),
            "team_name": _safe_text(report.get("team_name"), 160),
            "findings": [],
            "counts": {"total": 0, "blocking": 0, "degrading": 0, "advisory": 0, "info": 0},
            "source": report.get("source") if isinstance(report.get("source"), dict) else {},
        }

    findings = [_dict_value(item) for item in _list_value(report.get("doctor_findings"))]
    replay_result = _team_builder_latest_llm_replay_result()
    if replay_result.get("verdict") == "pass":
        covered_workers = {
            _safe_text(item.get("worker_id"), 160)
            for item in _list_value(_dict_value(replay_result.get("counts")).get("executed_llm_workers"))
            if _safe_text(item.get("worker_id"), 160)
        }
        worker_run = _dict_value(report.get("worker_run_smoke"))
        skipped_workers = [_dict_value(item) for item in _list_value(worker_run.get("skipped_workers"))]
        skipped_llm_workers = {
            _safe_text(item.get("worker_id"), 160)
            for item in skipped_workers
            if _safe_text(item.get("reason"), 80) == "requires_llm" and _safe_text(item.get("worker_id"), 160)
        }
        llm_gap_covered = bool(skipped_llm_workers) and skipped_llm_workers.issubset(covered_workers)
        if llm_gap_covered:
            findings = [
                item for item in findings
                if _safe_text(item.get("check_id"), 160)
                not in {"team_builder.worker_run_smoke.requires_llm", "team_builder.test.worker_run_smoke"}
            ]
    findings.extend(_team_builder_contract_findings(
        _safe_text(report.get("team_name"), 160),
        _dict_value(report.get("contract_coverage")),
    ))
    material_report = _material_attribution_report()
    findings.extend(_team_builder_material_read_group_findings(material_report))
    counts = _doctor_level_counts(findings)
    total_findings = len(findings)
    doctor_verdict = "fail" if counts.get("blocking", 0) else "warning" if total_findings else "pass"
    doctor_summary = (
        f"doctor finding {total_findings} 条；blocking {counts.get('blocking', 0)}，"
        f"degrading {counts.get('degrading', 0)}，advisory {counts.get('advisory', 0)}。"
        if total_findings
        else "当前 doctor 没有发现需要处理的问题。"
    )
    return {
        "available": True,
        "run_id": _safe_text(report.get("run_id"), 160),
        "team_name": _safe_text(report.get("team_name"), 160),
        "verdict": doctor_verdict,
        "summary": _safe_text(doctor_summary, 520),
        "counts": {
            **counts,
            "total": total_findings,
        },
        "findings": findings,
        "source": {
            **(report.get("source") if isinstance(report.get("source"), dict) else {}),
            "material_report_endpoint": "/api/team-builder-materialization/report/latest",
        },
    }


def _team_builder_repair_safety_policy() -> dict[str, Any]:
    rules = [
        {
            "id": "validation_gap_no_code_change",
            "name": "验证缺口不改代码",
            "match_check_ids": [
                "team_builder.worker_run_smoke.requires_llm",
                "team_builder.test.worker_run_smoke",
                "team_builder.material.unconfirmed_read_group",
                "team_builder.contract.coverage_missing",
                "team_builder.contract.execution_not_run",
            ],
            "match_levels": ["advisory", "info"],
            "category": "validation_gap",
            "automation_level": "none",
            "auto_safe": False,
            "next_action": "补受控 LLM 回放或测试样例，不改生成代码。",
            "rationale": "这类 finding 说明验证没有覆盖完整链路，不证明生成代码错误。",
        },
        {
            "id": "runtime_failure_patch_plan_only",
            "name": "运行失败只生成补丁计划",
            "match_check_ids": [
                "team_builder.worker_run_smoke.failed",
                "team_builder.worker_run_smoke.exception",
                "team_builder.worker_run_smoke.stub_failed",
            ],
            "match_levels": ["blocking", "degrading"],
            "category": "repair_required",
            "automation_level": "patch_plan_only",
            "auto_safe": False,
            "next_action": "定位源码、输入 material 和失败诊断，生成补丁计划；必须人工确认后才能改代码。",
            "rationale": "运行时失败可能需要业务语义判断，不能只凭异常文本自动修改生成代码。",
        },
        {
            "id": "contract_failure_patch_plan_only",
            "name": "contract 失败只生成补丁计划",
            "match_check_ids": [
                "team_builder.contract.execution_failed",
            ],
            "match_levels": ["blocking", "degrading"],
            "category": "repair_required",
            "automation_level": "patch_plan_only",
            "auto_safe": False,
            "next_action": "定位失败 contract、输入样例、期望输出和 generated worker 源码，生成补丁计划；必须人工确认后才能改代码。",
            "rationale": "acceptance 失败说明行为不满足契约，但修复方式仍需要业务语义判断，不能自动改真实 generated code。",
        },
        {
            "id": "binding_or_input_gap_patch_plan_only",
            "name": "绑定或输入缺口只生成补丁计划",
            "match_check_ids": [
                "team_builder.worker_run_smoke.missing_binding",
                "team_builder.worker_run_smoke.no_run_method",
                "team_builder.worker_run_smoke.missing_input",
            ],
            "match_levels": ["blocking", "degrading"],
            "category": "repair_required",
            "automation_level": "patch_plan_only",
            "auto_safe": False,
            "next_action": "先生成最小补丁计划，并要求重跑 build_bindings、worker run smoke 和 doctor。",
            "rationale": "这类问题接近代码结构修复，但仍可能牵涉 TeamSpec、FORMAT_IN/OUT 和样例输入契约。",
        },
        {
            "id": "advisory_observe_only",
            "name": "普通建议只观察",
            "match_check_ids": ["*"],
            "match_levels": ["advisory", "info"],
            "category": "observe_only",
            "automation_level": "none",
            "auto_safe": False,
            "next_action": "保留为观察项，除非后续测试失败升级，否则不触发 repair。",
            "rationale": "建议级 finding 不应直接触发代码修改。",
        },
    ]
    return {
        "version": "2026-05-17.v1",
        "summary": "当前策略允许自动分类和补丁计划生成，但不允许在没有人工确认的情况下直接修改生成代码。",
        "rules": rules,
        "default_rule": {
            "id": "default_manual_review",
            "category": "repair_required",
            "automation_level": "manual_review",
            "auto_safe": False,
            "next_action": "先人工审阅 finding 和源码，再决定是否生成补丁计划。",
            "rationale": "未知 finding 不进入自动修复。",
        },
    }


def _team_builder_repair_rule_for_finding(finding: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    check_id = _safe_text(finding.get("check_id"), 160)
    level = _safe_text(finding.get("level"), 40) or "info"
    for raw_rule in _list_value(policy.get("rules")):
        rule = _dict_value(raw_rule)
        check_ids = [_safe_text(item, 160) for item in _list_value(rule.get("match_check_ids"))]
        levels = [_safe_text(item, 40) for item in _list_value(rule.get("match_levels"))]
        if ("*" in check_ids or check_id in check_ids) and (not levels or level in levels):
            return rule
    return _dict_value(policy.get("default_rule"))


def _team_builder_validation_action(
    action_id: str,
    title: str,
    summary: str,
    *,
    action_kind: str,
    endpoint: str = "",
    command: str = "",
    expected_result: str = "",
    safety: str = "",
) -> dict[str, Any]:
    return {
        "id": action_id,
        "title": _safe_text(title, 160),
        "summary": _safe_text(summary, 420),
        "action_kind": _safe_text(action_kind, 80),
        "endpoint": _safe_text(endpoint, 220),
        "command": _safe_text(command, 260),
        "expected_result": _safe_text(expected_result, 420),
        "safety": _safe_text(safety, 240),
    }


def _team_builder_validation_actions_for_finding(finding: dict[str, Any]) -> list[dict[str, Any]]:
    check_id = _safe_text(finding.get("check_id"), 160)
    location = _safe_text(finding.get("location"), 180)
    if check_id == "team_builder.material.unconfirmed_read_group":
        return [
            _team_builder_validation_action(
                "validate_material_read_group:inspect_resolution",
                "查看读取线索消解计划",
                "打开读取线索消解接口，确认该 worker 的未确认组仍缺哪类证据：工具结果路径、Read 目标还是文件头 material_id。",
                action_kind="api_probe",
                endpoint="/api/team-builder-materialization/read-clue-resolution/latest",
                expected_result="对应 action 应保留为 unresolved，且说明候选 material、工具事件确认状态和下一步复核方式。",
                safety="只读接口；不会修改生成代码或 material 注册。",
            ),
            _team_builder_validation_action(
                "validate_material_read_group:tool_output",
                "补工具输出证据",
                "对 finding 中的样例目标补充真实工具输出或 Read 证据；只有命中文件和 material_id 都明确时，才允许升级为事实读取边。",
                action_kind="controlled_replay",
                endpoint="/api/team-builder-materialization/material-gap-validation/latest",
                expected_result="验证报告应列出目标是否能解析到当前文件、是否有 material_id，以及是否仍缺真实工具输出证据。",
                safety="验证动作只能补证据，不直接改 worker 代码。",
            ),
        ]
    if check_id in {"team_builder.worker_run_smoke.requires_llm", "team_builder.test.worker_run_smoke"}:
        return [
            _team_builder_validation_action(
                "validate_llm_gap:inspect_replay_plan",
                "查看受控 LLM 回放计划",
                f"检查 {location or '当前 finding'} 对应的 LLM 桩调用是否具备模型、JSON 输出键、中文约束和执行前置条件。",
                action_kind="api_probe",
                endpoint="/api/team-builder-materialization/llm-replay-plan/latest",
                expected_result="回放计划应为 ready_for_controlled_replay；若 execution_preflight.can_execute=false，则先补开关或凭据。",
                safety="该接口只生成计划，不调用真实模型。",
            ),
            _team_builder_validation_action(
                "validate_llm_gap:controlled_replay",
                "执行真实模型前置确认",
                "只有在明确允许真实模型调用后，设置 OMNI_ALLOW_TEAM_BUILDER_LLM_REPLAY=1 并确保 THE_COMPANY_API_KEY 可用，再执行受控回放。",
                action_kind="manual_gate",
                endpoint="/api/team-builder-materialization/llm-replay-plan/latest",
                expected_result="真实回放完成后，重新生成 test-report/doctor-findings，LLM gap 应从 validation_gap 中移除或变成具体运行失败。",
                safety="需要人工确认成本和凭据；默认不自动执行。",
            ),
        ]
    if check_id == "team_builder.contract.coverage_missing":
        return [
            _team_builder_validation_action(
                "validate_contract_coverage:create_contract",
                "补同名 contract",
                "为当前 generated team 新增 tests/teams/<team>/test_contract.py，覆盖输入样例、期望输出、错误样例和关键业务语义。",
                action_kind="manual_authoring",
                command="新增 tests/teams/<team>/test_contract.py 后运行 pytest --team-mode=programmatic tests/teams/<team>/test_contract.py",
                expected_result="test-report 的 contract_coverage 应从 missing_contract 变为 configured 或 executed。",
                safety="补测试契约，不修改 generated worker 代码。",
            )
        ]
    if check_id == "team_builder.contract.execution_not_run":
        return [
            _team_builder_validation_action(
                "validate_contract_execution:explicit_execute",
                "显式执行 contract",
                "通过受控 POST 入口运行匹配到的 team contract，并把 pytest 结果写回 material。",
                action_kind="manual_gate",
                endpoint="/api/team-builder-materialization/contract-execution/execute",
                expected_result="contract-execution/latest 应返回 pass 或 fail，并写入 team_contract_execution_result.json。",
                safety="只执行测试；不会自动修改 generated code。",
            )
        ]
    if check_id == "team_builder.contract.execution_failed":
        return [
            _team_builder_validation_action(
                "validate_contract_failure:inspect_execution",
                "查看 contract 失败详情",
                "打开最近一次 contract execution material，确认失败 contract、pytest 摘要、输入样例和失败断言。",
                action_kind="api_probe",
                endpoint="/api/team-builder-materialization/contract-execution/latest",
                expected_result="报告应列出失败 contract 的路径、返回码、命令和 stdout/stderr 摘要。",
                safety="只读失败报告；不会修改 generated code。",
            ),
            _team_builder_validation_action(
                "validate_contract_failure:repair_plan",
                "生成人工审阅补丁计划",
                "根据 contract 失败项定位 generated worker 源码和对应输入/输出 material，生成补丁候选，但保持人工确认门。",
                action_kind="repair_plan",
                endpoint="/api/team-builder-materialization/repair-patch-candidates/latest",
                expected_result="repair plan 应进入 repair_required，候选补丁仍要求人工确认和回查。",
                safety="只生成候选和审阅材料，不自动应用真实补丁。",
            ),
        ]
    return []


def _team_builder_next_action_for_finding(
    finding: dict[str, Any],
    rule: dict[str, Any],
    validation_actions: list[dict[str, Any]],
) -> str:
    check_id = _safe_text(finding.get("check_id"), 160)
    if check_id == "team_builder.material.unconfirmed_read_group":
        return "先执行读取线索消解计划检查和工具输出证据补充；确认前只保留 validation gap，不改生成代码。"
    if check_id == "team_builder.worker_run_smoke.requires_llm":
        return "先查看受控 LLM 回放计划；只有人工打开真实模型回放开关并满足凭据后，才执行真实回放。"
    if check_id == "team_builder.test.worker_run_smoke" and validation_actions:
        return "先处理 worker smoke 的验证缺口：查看 LLM 回放计划和未确认读取组；不要把验证缺口当作代码缺陷。"
    if check_id == "team_builder.contract.coverage_missing":
        return "先补同名 tests/teams contract；这只是验收覆盖缺口，不直接修改 generated worker。"
    if check_id == "team_builder.contract.execution_not_run":
        return "通过显式 contract execution 入口运行 pytest，并把结果 material 化；页面刷新不自动执行。"
    if check_id == "team_builder.contract.execution_failed":
        return "先查看 contract execution 失败详情，再生成人工审阅补丁计划；真实改码必须经过修复应用门。"
    return _safe_text(rule.get("next_action"), 360)


def _team_builder_latest_repair_safety_policy() -> dict[str, Any]:
    run_dir, _reason = _team_builder_latest_run_dir()
    run_id = run_dir.name if run_dir else ""
    policy = _team_builder_repair_safety_policy()
    result = {
        "available": True,
        "run_id": run_id,
        "version": policy["version"],
        "summary": policy["summary"],
        "counts": {
            "rules": len(_list_value(policy.get("rules"))),
            "auto_safe_rules": sum(1 for rule in _list_value(policy.get("rules")) if _dict_value(rule).get("auto_safe")),
            "patch_plan_only_rules": sum(1 for rule in _list_value(policy.get("rules")) if _dict_value(rule).get("automation_level") == "patch_plan_only"),
            "manual_or_none_rules": sum(1 for rule in _list_value(policy.get("rules")) if _dict_value(rule).get("automation_level") in {"manual_review", "none"}),
        },
        "rules": policy["rules"],
        "default_rule": policy["default_rule"],
        "source": {
            "repair_safety_policy_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_safety_policy.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        policy_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_safety_policy.json"
        try:
            policy_path.parent.mkdir(parents=True, exist_ok=True)
            policy_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return result


def _team_builder_pattern_search_roots() -> list[Path]:
    root = _repo_root()
    candidates = [
        root / "src" / "omnicompany" / "packages" / "services" / "_core" / "team_builder",
        root / "src" / "omnicompany" / "packages" / "services" / "_authoring",
    ]
    return [path for path in candidates if path.is_dir() and not _is_skipped(path)]


def _team_builder_pattern_examples(pattern: str, *, limit: int = 6) -> tuple[list[dict[str, Any]], int]:
    examples: list[dict[str, Any]] = []
    scanned = 0
    regex: re.Pattern[str] | None = None
    try:
        regex = re.compile(pattern)
    except re.error:
        regex = None
    lowered_terms = [term.lower() for term in pattern.split("|") if term.strip()]
    seen: set[Path] = set()
    for search_root in _team_builder_pattern_search_roots():
        for path in _materialization_iter_files(search_root, limit=180):
            if path in seen or path.suffix.lower() not in {".py", ".md", ".yaml", ".yml", ".json", ".toml"}:
                continue
            seen.add(path)
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[:24000]
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                hit = bool(regex.search(line)) if regex else any(term in line.lower() for term in lowered_terms)
                if not hit:
                    continue
                examples.append(_materialization_file_review_example(path, line_no=line_no, excerpt=line.strip()))
                break
            if len(examples) >= limit:
                return examples, scanned
    return examples, scanned


def _team_builder_read_clue_review_details(worker_id: str, link: dict[str, Any]) -> dict[str, Any]:
    target = _safe_text(link.get("target") or link.get("rel_path"), 320)
    normalized = _safe_text(_materialization_normalized_target(target), 260) if target else ""
    workspace_path = _materialization_workspace_path(target, normalized)
    workspace_dir = _materialization_workspace_dir(target, normalized)
    examples: list[dict[str, Any]] = []
    material_id_hits: set[str] = set()
    if workspace_path:
        examples = [_materialization_file_review_example(workspace_path)]
        material_id_hits.update(examples[0].get("material_ids", []))
        return {
            "target": f"文件 {_materialization_relpath(workspace_path)}",
            "summary": "这条线索已经指到具体文件；若文件头没有 material_id，就只能说明 worker 读过工作区文件。",
            "examples": examples,
            "material_id_hits": sorted(material_id_hits),
        }
    candidate_paths = _team_builder_target_candidate_paths(target)
    if candidate_paths:
        examples = [_materialization_file_review_example(path) for path in candidate_paths[:6]]
        for example in examples:
            material_id_hits.update(example.get("material_ids", []))
        resolution_kind, resolution_note = _team_builder_target_resolution_note(target, candidate_paths)
        return {
            "target": f"文件 {' / '.join(_materialization_relpath(path) for path in candidate_paths[:3])}",
            "summary": (
                f"{resolution_note} 这条线索已经能解析到当前仓库文件；若文件头有 material_id，"
                "只能先作为候选 material，仍需真实工具输出证明 worker 本次确实命中了它。"
            ),
            "examples": examples,
            "material_id_hits": sorted(material_id_hits),
            "resolution_kind": resolution_kind,
            "resolution_note": resolution_note,
        }
    if workspace_dir:
        files = _materialization_iter_files(workspace_dir, limit=80)
        for path in files:
            example = _materialization_file_review_example(path)
            if len(examples) < 6:
                examples.append(example)
            material_id_hits.update(example.get("material_ids", []))
        scope = _materialization_relpath(workspace_dir)
        material_hit_count = len(material_id_hits)
        return {
            "target": f"目录 {scope}",
            "summary": (
                f"这条线索只指向目录空间，快速展开前 {len(files)} 个文件后发现 "
                f"{material_hit_count} 个 material_id 命中。目录本身仍不能代表某个确定 material。"
            ),
            "examples": examples,
            "material_id_hits": sorted(material_id_hits)[:8],
        }
    key = target.split("=", 1)[0].strip().lower() if "=" in target else ""
    pattern = _materialization_target_value(target)
    if key == "pattern":
        examples, scanned = _team_builder_pattern_examples(pattern)
        for example in examples:
            material_id_hits.update(example.get("material_ids", []))
        return {
            "target": f"pattern {pattern}",
            "summary": (
                f"按 pattern 在 TeamBuilder 相关源码范围试展开，扫描 {scanned} 个文件，命中 {len(examples)} 个示例。"
                "这些示例只是候选展开结果，需要和真实工具输出交叉确认后才能成为正式读边。"
            ),
            "examples": examples,
            "material_id_hits": sorted(material_id_hits)[:8],
        }
    return {
        "target": target or f"worker {worker_id} 的未知读取目标",
        "summary": "这条线索没有足够结构化目标，只能保留为人工复核项。",
        "examples": [],
        "material_id_hits": [],
    }


def _team_builder_candidate_materials_from_review(
    *,
    worker_id: str,
    action_id: str,
    category: str,
    review: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_candidate(material_id: str, example: dict[str, Any] | None = None) -> None:
        material_id = _safe_text(material_id, 220)
        if not material_id:
            return
        path = _safe_text((example or {}).get("path"), 260)
        if material_id in seen:
            return
        seen.add(material_id)
        if category == "expand_directory":
            basis = "目录静态展开命中 material_id，但真实工具事件尚未说明具体读到哪一个文件。"
        elif category == "expand_pattern":
            basis = "模式静态展开命中 material_id，但仍需和真实 grep/read 输出交叉确认。"
        else:
            basis = "读取线索扫描命中 material_id，但证据层级仍是候选。"
        candidate = {
            "id": f"{action_id}:candidate:{len(candidates)}",
            "worker_id": worker_id,
            "material_id": material_id,
            "path": path,
            "line": (example or {}).get("line"),
            "kind": _safe_text((example or {}).get("kind"), 64),
            "confidence": "low",
            "status": "candidate_material",
            "basis": basis,
            "needs_confirmation": True,
        }
        excerpt = _safe_text((example or {}).get("excerpt"), 220)
        if excerpt:
            candidate["excerpt"] = excerpt
        candidates.append(candidate)

    for example in _list_value(review.get("examples")):
        example_data = _dict_value(example)
        for material_id in _list_value(example_data.get("material_ids")):
            add_candidate(_safe_text(material_id, 220), example_data)

    for material_id in _list_value(review.get("material_id_hits")):
        add_candidate(_safe_text(material_id, 220), None)

    return candidates[:12]


def _team_builder_targets_equivalent(left: str, right: str) -> bool:
    left = _safe_text(left, 320)
    right = _safe_text(right, 320)
    if not left or not right:
        return False
    if left == right:
        return True
    left_key = left.split("=", 1)[0].strip().lower() if "=" in left else ""
    right_key = right.split("=", 1)[0].strip().lower() if "=" in right else ""
    path_keys = {"path", "dir_path", "file_path", "filepath"}
    if left_key and right_key and left_key != right_key and not ({left_key, right_key} <= path_keys):
        return False
    return _materialization_normalized_target(left).lower() == _materialization_normalized_target(right).lower()


def _team_builder_tool_event_matches_target(event: dict[str, Any], target: str) -> bool:
    return any(_team_builder_targets_equivalent(_safe_text(item, 320), target) for item in _list_value(event.get("targets")))


def _team_builder_tool_event_scope_dirs(event: dict[str, Any]) -> list[Path]:
    dirs: list[Path] = []
    for raw_target in _list_value(event.get("targets")):
        target = _safe_text(raw_target, 320)
        normalized = _materialization_normalized_target(target)
        directory = _materialization_workspace_dir(target, normalized)
        if directory:
            dirs.append(directory)
    return dirs


def _team_builder_tool_event_read_files(tool_events: list[Any]) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_event in tool_events:
        event = _dict_value(raw_event)
        if not event.get("read_like"):
            continue
        for raw_target in _list_value(event.get("targets")):
            target = _safe_text(raw_target, 320)
            normalized = _materialization_normalized_target(target)
            path = _materialization_workspace_path(target, normalized)
            if not path:
                continue
            rel = _materialization_relpath(path)
            key = f"{rel}|{event.get('index')}"
            if key in seen:
                continue
            seen.add(key)
            reads.append({
                "path": path,
                "rel_path": rel,
                "tool": _safe_text(event.get("tool"), 80),
                "event_index": event.get("index"),
                "target": target,
                "evidence_kind": "tool_target_read",
            })
        for raw_path in _list_value(event.get("result_paths")):
            target = f"file_path={_safe_text(raw_path, 320)}"
            paths = _team_builder_target_candidate_paths(target)
            if not paths:
                continue
            evidence_kind = _safe_text(event.get("result_path_evidence_kind"), 80)
            if not evidence_kind:
                tool_name = _safe_text(event.get("tool"), 80).lower()
                evidence_kind = "content_mention_path" if "read" in tool_name else "tool_result_path"
            for path in paths:
                rel = _materialization_relpath(path)
                key = f"{rel}|result|{event.get('index')}"
                if key in seen:
                    continue
                seen.add(key)
                reads.append({
                    "path": path,
                    "rel_path": rel,
                    "tool": f"{_safe_text(event.get('tool'), 80)} result",
                    "event_index": event.get("index"),
                    "target": target,
                    "evidence_kind": evidence_kind,
                })
    return reads


def _team_builder_file_matches_pattern(path: Path, pattern: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:80000]
    except OSError:
        return False
    try:
        return bool(re.search(pattern, text, re.IGNORECASE))
    except re.error:
        terms = [term.strip().lower() for term in re.split(r"[|\\s]+", pattern) if term.strip()]
        lowered = text.lower()
        return any(term in lowered for term in terms)


def _team_builder_read_path_supports_target(
    path: Path,
    target: str,
    matching_events: list[dict[str, Any]],
) -> bool:
    normalized = _materialization_normalized_target(target)
    workspace_path = _materialization_workspace_path(target, normalized)
    if workspace_path:
        try:
            return path.resolve() == workspace_path.resolve()
        except OSError:
            return False
    candidate_paths = _team_builder_target_candidate_paths(target)
    for candidate in candidate_paths:
        try:
            if path.resolve() == candidate.resolve():
                return True
        except OSError:
            continue
    workspace_dir = _materialization_workspace_dir(target, normalized)
    if workspace_dir:
        try:
            path.resolve().relative_to(workspace_dir.resolve())
            return True
        except (OSError, ValueError):
            return False
    key = target.split("=", 1)[0].strip().lower() if "=" in target else ""
    if key == "pattern":
        scope_dirs: list[Path] = []
        for event in matching_events:
            scope_dirs.extend(_team_builder_tool_event_scope_dirs(event))
        if scope_dirs:
            in_scope = False
            for directory in scope_dirs:
                try:
                    path.resolve().relative_to(directory.resolve())
                    in_scope = True
                    break
                except (OSError, ValueError):
                    continue
            if not in_scope:
                return False
        return _team_builder_file_matches_pattern(path, _materialization_target_value(target))
    return False


def _team_builder_tool_event_confirmation(
    worker_id: str,
    target: str,
    tool_events: list[Any],
) -> dict[str, Any]:
    events = [_dict_value(event) for event in tool_events if _dict_value(event).get("read_like")]
    matching_events = [event for event in events if _team_builder_tool_event_matches_target(event, target)]
    confirmed_materials: list[dict[str, Any]] = []
    seen: set[str] = set()
    for read in _team_builder_tool_event_read_files(events):
        path = read.get("path")
        if not isinstance(path, Path):
            continue
        if not _team_builder_read_path_supports_target(path, target, matching_events):
            continue
        example = _materialization_file_review_example(path)
        for material_id in _list_value(example.get("material_ids")):
            material_id = _safe_text(material_id, 220)
            if not material_id or material_id in seen:
                continue
            seen.add(material_id)
            evidence_kind = _safe_text(read.get("evidence_kind"), 80)
            if evidence_kind == "content_mention_path":
                basis = "同一 worker 读取的工具结果内容里提到了这个路径，且当前文件头声明了 material_id；这只能证明路径提及线索可解释，不等于该文件被读取或搜索命中。"
            elif evidence_kind in {"search_hit_path", "command_output_path", "tool_result_path"}:
                basis = "同一 worker 的工具输出明确提到了这个文件路径，且当前文件头声明了 material_id；这证明线索可解释，但仍不是直接 Read 目标。"
            else:
                basis = "同一 worker 的真实工具事件明确 Read 了这个文件，且文件头声明了 material_id。"
            confirmed_materials.append({
                "material_id": material_id,
                "path": _safe_text(example.get("path"), 260),
                "kind": _safe_text(example.get("kind"), 64),
                "tool": _safe_text(read.get("tool"), 80),
                "event_index": read.get("event_index"),
                "evidence_kind": evidence_kind,
                "basis": basis,
            })
    if matching_events and confirmed_materials:
        status = "scope_and_read_confirmed"
        summary = (
            f"真实工具事件确认 {worker_id} 执行过这条读取/搜索动作，并且同一 worker 明确 Read 了 "
            f"{len(confirmed_materials)} 个可 material 化文件；仍需 grep 命中输出确认是否还有其他命中文件。"
        )
    elif matching_events:
        status = "scope_confirmed_only"
        summary = "真实工具事件确认这条读取/搜索动作发生过，但当前记录没有保存命中文件输出，不能确认具体 material。"
    elif confirmed_materials:
        evidence_kinds = {_safe_text(item.get("evidence_kind"), 80) for item in confirmed_materials}
        only_content_mentions = evidence_kinds == {"content_mention_path"}
        only_tool_output = evidence_kinds <= {"search_hit_path", "command_output_path", "tool_result_path"}
        status = (
            "content_mention_path_without_scope_event"
            if only_content_mentions
            else "tool_output_path_confirmed_without_scope_event"
            if only_tool_output
            else "read_confirmed_without_scope_event"
        )
        summary = (
            f"没有直接匹配到这条线索的工具调用参数；同一 worker 读取到的内容提到了 {len(confirmed_materials)} 个可 material 化路径，但这不是工具命中输出。"
            if only_content_mentions
            else f"没有直接匹配到这条线索的工具调用参数，但同一 worker 的工具输出提到了 {len(confirmed_materials)} 个可 material 化文件。"
            if only_tool_output
            else f"没有直接匹配到这条线索的工具事件，但同一 worker 明确 Read 了 {len(confirmed_materials)} 个相关文件。"
        )
    else:
        status = "not_confirmed"
        summary = "当前真实工具事件没有提供足够证据确认这条候选线索。"
    return {
        "status": status,
        "summary": summary,
        "matching_events": [
            {
                "index": event.get("index"),
                "tool": _safe_text(event.get("tool"), 80),
                "targets": [_safe_text(item, 260) for item in _list_value(event.get("targets"))[:6]],
            }
            for event in matching_events[:4]
        ],
        "confirmed_materials": confirmed_materials[:8],
    }


def _team_builder_read_clue_resolution_action(
    worker_id: str,
    link: dict[str, Any],
    index: int,
    tool_events: list[Any] | None = None,
) -> dict[str, Any]:
    target = _safe_text(link.get("target") or link.get("rel_path"), 260)
    title = _safe_text(link.get("title") or link.get("human_title"), 180)
    evidence_summary = _safe_text(link.get("evidence_summary"), 360)
    review = _team_builder_read_clue_review_details(worker_id, link)
    action_id = f"read_clue_resolution:{index}"
    if target.startswith("pattern=") or "*" in target:
        category = "expand_pattern"
        automation_level = "auto_expand_then_review"
        next_action = "按 pattern 展开命中文件，逐个扫描 OMNI material_id；只有命中文件级 material_id 后才升级为确认读取。"
    elif target.startswith("dir_path=") or "directory" in target.lower() or "目录" in title:
        category = "expand_directory"
        automation_level = "auto_expand_then_review"
        next_action = "展开目录内实际读取文件或结合后续 grep/read 事件；目录本身不直接升级为 material 事实边。"
    elif target.startswith("command=") or "grep" in target.lower() or "bash" in target.lower():
        category = "tool_trace_replay"
        automation_level = "trace_replay_required"
        next_action = "回放工具命令摘要，提取真实命中文件或 material_id；不能只凭命令文本升级。"
    elif _safe_text(link.get("resource_kind"), 80) == "workspace":
        category = "workspace_review"
        automation_level = "manual_or_header_scan"
        next_action = "检查目标文件是否有 OMNI material_id；若没有，只保留为 workspace 资源读取线索。"
    else:
        category = "manual_review"
        automation_level = "manual_review"
        next_action = "人工审阅原始线索和工具事件，再决定是否能归入 material。"
    candidate_materials = _team_builder_candidate_materials_from_review(
        worker_id=worker_id,
        action_id=action_id,
        category=category,
        review=review,
    )
    tool_confirmation = _team_builder_tool_event_confirmation(worker_id, target, _list_value(tool_events))
    return {
        "id": action_id,
        "worker_id": worker_id,
        "title": title,
        "target": target,
        "category": category,
        "automation_level": automation_level,
        "status": "candidate_materialized" if candidate_materials else "unresolved",
        "evidence_summary": evidence_summary,
        "reason": "没有 matched_material_ids 或 declared_material_ids，不能作为确认 material 读边。",
        "next_action": next_action,
        "review_target": review["target"],
        "review_summary": review["summary"],
        "review_examples": review["examples"],
        "material_id_hits": review["material_id_hits"],
        "candidate_materials": candidate_materials,
        "tool_confirmation": tool_confirmation,
        "raw_evidence": [_safe_text(item, 220) for item in _list_value(link.get("evidence"))[:5]],
        "source_filter": _dict_value(link.get("source_filter")),
    }


def _team_builder_latest_read_clue_resolution_plan(report: dict[str, Any] | None = None) -> dict[str, Any]:
    cacheable = report is None
    cache_key = ""
    input_mtime = 0.0
    if cacheable:
        run_dir, _reason = _team_builder_latest_run_dir()
        cacheable = run_dir is not None
        if cacheable:
            cache_key = f"{_repo_root()}::{run_dir.name}"
            input_mtime = _team_builder_report_input_mtime(run_dir)
            cached = _TEAM_BUILDER_READ_CLUE_RESOLUTION_CACHE.get(cache_key)
            if cached and cached[0] >= input_mtime:
                return json.loads(json.dumps(cached[1], ensure_ascii=False))
    report = report if report is not None else _material_attribution_report()
    if not report.get("available"):
        result = {
            "available": False,
            "reason": _safe_text(report.get("reason"), 500),
            "run_id": _safe_text(report.get("run_id"), 160),
            "team_name": _safe_text(report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无 material 归因报告，无法生成读取线索消解计划。",
            "counts": {
                "read_clues": 0,
                "confirmed": 0,
                "confirmed_read_edges": 0,
                "unresolved": 0,
                "candidate_materialized": 0,
                "candidate_materials": 0,
                "unexpanded": 0,
                "tool_scope_confirmed": 0,
                "tool_read_confirmed_materials": 0,
                "content_mention_path_clues": 0,
                "auto_expandable": 0,
                "trace_replay_required": 0,
                "manual_review": 0,
            },
            "actions": [],
            "content_mention_actions": [],
            "quality_gates": [],
            "source": report.get("source") if isinstance(report.get("source"), dict) else {},
        }
        if cacheable:
            _TEAM_BUILDER_READ_CLUE_RESOLUTION_CACHE[cache_key] = (input_mtime, json.loads(json.dumps(result, ensure_ascii=False)))
        return result

    actions: list[dict[str, Any]] = []
    content_mention_actions: list[dict[str, Any]] = []
    read_clues = 0
    confirmed = 0
    for worker_report in _list_value(report.get("worker_reports")):
        worker = _dict_value(worker_report)
        worker_id = _safe_text(worker.get("worker_id"), 160)
        tool_events = _list_value(worker.get("tool_events"))
        for raw_link in _list_value(worker.get("read_clues")):
            link = _dict_value(raw_link)
            read_clues += 1
            if link.get("declared_material_ids") or link.get("matched_material_ids"):
                confirmed += 1
                continue
            action = _team_builder_read_clue_resolution_action(
                worker_id,
                link,
                len(actions) + len(content_mention_actions),
                tool_events=tool_events,
            )
            confirmation_status = _safe_text(_dict_value(action.get("tool_confirmation")).get("status"), 120)
            if confirmation_status == "content_mention_path_without_scope_event":
                action["status"] = "content_mention_explained"
                action["category"] = "content_mention"
                action["automation_level"] = "observe_only"
                action["reason"] = "这条线索来自已读取内容里的路径提及，只解释为什么出现该路径；不能证明 worker 直接读取或搜索命中该 material。"
                action["next_action"] = "保留为内容提及解释层；除非后续出现工具命中或明确 Read 目标，否则不进入 doctor/repair 缺口。"
                content_mention_actions.append(action)
            else:
                actions.append(action)

    auto_expandable = sum(1 for action in actions if action["automation_level"] == "auto_expand_then_review")
    trace_replay_required = sum(1 for action in actions if action["automation_level"] == "trace_replay_required")
    manual_review = sum(1 for action in actions if action["automation_level"] in {"manual_or_header_scan", "manual_review"})
    candidate_materialized = sum(1 for action in actions if _list_value(action.get("candidate_materials")))
    candidate_material_keys = {
        (
            _safe_text(candidate.get("worker_id"), 120),
            _safe_text(candidate.get("material_id"), 220),
            _safe_text(candidate.get("path"), 260),
        )
        for action in actions
        for candidate in _list_value(action.get("candidate_materials"))
    }
    candidate_material_count = len(candidate_material_keys)
    unexpanded = len(actions) - candidate_materialized
    tool_scope_confirmed = sum(
        1
        for action in actions
        if _dict_value(action.get("tool_confirmation")).get("matching_events")
    )
    confirmed_material_items = [
        (action, _dict_value(candidate))
        for action in actions
        for candidate in _list_value(_dict_value(action.get("tool_confirmation")).get("confirmed_materials"))
    ]
    content_mention_material_items = [
        (action, _dict_value(candidate))
        for action in content_mention_actions
        for candidate in _list_value(_dict_value(action.get("tool_confirmation")).get("confirmed_materials"))
    ]
    tool_read_confirmed_keys = {
        (
            _safe_text(candidate.get("worker_id") or action.get("worker_id"), 120),
            _safe_text(candidate.get("material_id"), 220),
            _safe_text(candidate.get("path"), 260),
        )
        for action, candidate in confirmed_material_items
        if _safe_text(candidate.get("evidence_kind"), 80) != "content_mention_path"
    }
    content_mention_path_keys = {
        (
            _safe_text(candidate.get("worker_id") or action.get("worker_id"), 120),
            _safe_text(candidate.get("material_id"), 220),
            _safe_text(candidate.get("path"), 260),
        )
        for action, candidate in content_mention_material_items
        if _safe_text(candidate.get("evidence_kind"), 80) == "content_mention_path"
    }
    tool_read_confirmed_materials = len(tool_read_confirmed_keys)
    content_mention_path_materials = len(content_mention_path_keys)
    report_counts = _dict_value(report.get("counts"))
    confirmed_read_edges = report_counts.get("confirmed_reads") if isinstance(report_counts.get("confirmed_reads"), int) else 0
    gates = [
        _test_gate(
            "read_clues_visible",
            "候选读取线索可见",
            "pass" if read_clues else "warning",
            f"共有 {read_clues} 条实战读取线索。" if read_clues else "没有实战读取线索。",
            [f"read_clues={read_clues}"],
        ),
        _test_gate(
            "candidate_material_expansion",
            "候选 material 已展开",
            "pass" if not actions or candidate_materialized == len(actions) else "warning",
            (
                f"{candidate_materialized}/{len(actions)} 条未确认线索已展开为候选 material，共 {candidate_material_count} 个候选。"
                if actions
                else "没有待展开候选。"
            ),
            [f"candidate_materials={candidate_material_count}", f"unexpanded={unexpanded}"],
        ),
        _test_gate(
            "tool_event_confirmation",
            "工具事件确认",
            "pass" if not actions or tool_scope_confirmed == len(actions) else "warning",
            (
                f"{tool_scope_confirmed}/{len(actions)} 条未确认线索能在真实工具事件中找到对应动作；"
                f"{tool_read_confirmed_materials} 个候选 material 有工具命中或明确 Read 证据，"
                f"{content_mention_path_materials} 个只是读取内容里的路径提及。"
                if actions
                else "没有待确认工具事件。"
            ),
            [
                f"tool_scope_confirmed={tool_scope_confirmed}",
                f"tool_read_confirmed_materials={tool_read_confirmed_materials}",
                f"content_mention_path_materials={content_mention_path_materials}",
            ],
        ),
        _test_gate(
            "content_mention_layer",
            "内容提及线索分层",
            "pass",
            (
                f"{len(content_mention_actions)} 条线索只作为内容提及解释层，"
                f"涉及 {content_mention_path_materials} 个 material 候选，不进入事实读边或 repair 缺口。"
                if content_mention_actions
                else "没有需要单独分层的内容提及线索。"
            ),
            [action["title"] for action in content_mention_actions[:5]],
        ),
        _test_gate(
            "unresolved_not_promoted",
            "未确认线索不伪装",
            "warning" if actions else "pass",
            f"{len(actions)} 条线索仍未确认，继续保持候选状态。" if actions else "没有证据缺口型未确认线索；内容提及线索不伪装成事实读边。",
            [action["title"] for action in actions[:5]],
        ),
        _test_gate(
            "resolution_next_action",
            "消解动作已给出",
            "pass" if all(action["next_action"] for action in actions) else "fail",
            "每条未确认线索都有下一步确认动作。" if actions else "没有待消解线索。",
            [],
        ),
    ]
    verdict = "warning" if actions else "pass"
    run_id = _safe_text(report.get("run_id"), 160)
    plan = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(report.get("team_name"), 160),
        "verdict": verdict,
        "summary": (
            f"读取线索消解计划: {len(actions)} 条候选仍需确认，"
            f"{candidate_materialized} 条已展开为候选 material，共 {candidate_material_count} 个候选；"
            f"{tool_scope_confirmed} 条能对上真实工具事件，{tool_read_confirmed_materials} 个候选有工具命中或明确 Read 证据，"
            f"{len(content_mention_actions)} 条内容提及线索保留为解释层，涉及 {content_mention_path_materials} 个 material 候选；"
            f"{auto_expandable} 条可自动展开后复核，"
            f"{trace_replay_required} 条需要工具回放，{manual_review} 条需要人工或文件头复核。"
        ),
        "counts": {
            "read_clues": read_clues,
            "confirmed": confirmed,
            "confirmed_read_edges": confirmed_read_edges,
            "unresolved": len(actions),
            "candidate_materialized": candidate_materialized,
            "candidate_materials": candidate_material_count,
            "unexpanded": unexpanded,
            "tool_scope_confirmed": tool_scope_confirmed,
            "tool_read_confirmed_materials": tool_read_confirmed_materials,
            "content_mention_path_clues": len(content_mention_actions),
            "content_mention_path_materials": content_mention_path_materials,
            "auto_expandable": auto_expandable,
            "trace_replay_required": trace_replay_required,
            "manual_review": manual_review,
        },
        "quality_gates": gates,
        "actions": actions,
        "content_mention_actions": content_mention_actions,
        "source": {
            "material_report_endpoint": "/api/team-builder-materialization/report/latest",
            "read_clue_resolution_plan_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_read_clue_resolution_plan.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        plan_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_read_clue_resolution_plan.json"
        try:
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    if cacheable:
        _TEAM_BUILDER_READ_CLUE_RESOLUTION_CACHE[cache_key] = (input_mtime, json.loads(json.dumps(plan, ensure_ascii=False)))
    return plan


def _team_builder_repair_action_from_finding(finding: dict[str, Any], index: int, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or _team_builder_repair_safety_policy()
    rule = _team_builder_repair_rule_for_finding(finding, policy)
    check_id = _safe_text(finding.get("check_id"), 160)
    level = _safe_text(finding.get("level"), 40) or "info"
    location = _safe_text(finding.get("location"), 180)
    observation = _safe_text(finding.get("observation"), 420)
    category = _safe_text(rule.get("category"), 80) or "repair_required"
    auto_safe = bool(rule.get("auto_safe"))
    validation_actions = _team_builder_validation_actions_for_finding(finding)
    next_action = _safe_text(_team_builder_next_action_for_finding(finding, rule, validation_actions), 420)
    rationale = _safe_text(rule.get("rationale"), 360)

    return {
        "id": f"repair_action:{index}",
        "finding_id": _safe_text(finding.get("id"), 220),
        "check_id": check_id,
        "level": level,
        "location": location,
        "category": category,
        "policy_rule_id": _safe_text(rule.get("id"), 120),
        "automation_level": _safe_text(rule.get("automation_level"), 80),
        "auto_safe": auto_safe,
        "observation": observation,
        "rationale": rationale,
        "next_action": next_action,
        "validation_actions": validation_actions,
        "node_ids": [_safe_text(item, 160) for item in _list_value(finding.get("node_ids"))],
        "material_ids": [_safe_text(item, 220) for item in _list_value(finding.get("material_ids"))],
        "cross_refs": [_safe_text(item, 260) for item in _list_value(finding.get("cross_refs"))],
    }


def _team_builder_latest_repair_plan() -> dict[str, Any]:
    findings_report = _team_builder_latest_doctor_findings_report()
    if not findings_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(findings_report.get("reason"), 500),
            "run_id": _safe_text(findings_report.get("run_id"), 160),
            "team_name": _safe_text(findings_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无可用 doctor finding，无法生成修复准备计划。",
            "counts": {"actions": 0, "repair_required": 0, "validation_gap": 0, "observe_only": 0, "auto_safe": 0},
            "actions": [],
            "source": findings_report.get("source") if isinstance(findings_report.get("source"), dict) else {},
        }

    findings = [_dict_value(item) for item in _list_value(findings_report.get("findings"))]
    policy = _team_builder_repair_safety_policy()
    actions = [_team_builder_repair_action_from_finding(finding, index, policy) for index, finding in enumerate(findings)]
    repair_required = sum(1 for action in actions if action["category"] == "repair_required")
    validation_gap = sum(1 for action in actions if action["category"] == "validation_gap")
    observe_only = sum(1 for action in actions if action["category"] == "observe_only")
    auto_safe = sum(1 for action in actions if action["auto_safe"])
    if repair_required:
        verdict = "repair_required"
        summary = f"发现 {repair_required} 条需要修复准备的 finding；当前没有自动安全修复动作。"
    elif validation_gap:
        verdict = "validation_gap"
        summary = f"当前主要是 {validation_gap} 条验证缺口；不应直接修改生成代码。"
    else:
        verdict = "clean"
        summary = "当前 doctor findings 不要求 repair。"

    source = findings_report.get("source") if isinstance(findings_report.get("source"), dict) else {}
    run_id = _safe_text(findings_report.get("run_id"), 160)
    plan = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(findings_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "actions": len(actions),
            "repair_required": repair_required,
            "validation_gap": validation_gap,
            "observe_only": observe_only,
            "auto_safe": auto_safe,
        },
        "actions": actions,
        "source": {
            **source,
            "repair_safety_policy_endpoint": "/api/team-builder-materialization/repair-safety-policy/latest",
            "repair_plan_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_plan.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        repair_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_plan.json"
        try:
            repair_path.parent.mkdir(parents=True, exist_ok=True)
            repair_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return plan


def _team_builder_worker_source_candidates(worker_id: str, test_report: dict[str, Any]) -> list[dict[str, Any]]:
    source = _dict_value(test_report.get("source"))
    roots: list[Path] = []
    for key in ["test_package_dir", "code_package_files"]:
        raw = _safe_text(source.get(key), 320)
        if not raw:
            continue
        path = Path(raw)
        root = path if path.is_absolute() else _repo_root() / path
        if root.is_dir():
            roots.append(root)
    stems = list(dict.fromkeys([
        worker_id,
        _to_snake_identifier(worker_id),
        _to_snake_identifier(worker_id.removesuffix("_worker")),
    ]))
    candidates: list[Path] = []
    for root in roots:
        for stem in stems:
            if not stem:
                continue
            candidates.append(root / "workers" / f"{stem}.py")
        workers_dir = root / "workers"
        if workers_dir.is_dir():
            for path in workers_dir.glob("*.py"):
                if path.name == "__init__.py":
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")[:12000]
                except OSError:
                    continue
                if worker_id in text or _to_camel_identifier(worker_id) in text:
                    candidates.append(path)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for path in candidates:
        try:
            resolved = path.resolve()
            key = str(resolved)
            resolved.relative_to(_repo_root().resolve())
        except (OSError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        exists = resolved.is_file()
        example = _materialization_file_review_example(resolved) if exists else {}
        out.append({
            "path": _materialization_relpath(resolved),
            "exists": exists,
            "material_ids": [_safe_text(item, 220) for item in _list_value(example.get("material_ids"))],
            "excerpt": _safe_text(example.get("excerpt"), 700),
        })
    return out[:6]


def _team_builder_source_candidate(path: Path) -> dict[str, Any] | None:
    try:
        resolved = path.resolve()
        key = str(resolved)
        resolved.relative_to(_repo_root().resolve())
    except (OSError, ValueError):
        return None
    exists = resolved.is_file()
    example = _materialization_file_review_example(resolved) if exists else {}
    return {
        "path": _materialization_relpath(resolved),
        "exists": exists,
        "material_ids": [_safe_text(item, 220) for item in _list_value(example.get("material_ids"))],
        "excerpt": _safe_text(example.get("excerpt"), 700),
        "_key": key,
    }


def _team_builder_contract_source_candidates(action: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for raw in _list_value(action.get("cross_refs")):
        text = _safe_text(raw, 320)
        if not text or not text.endswith(".py"):
            continue
        path = Path(text)
        resolved = path if path.is_absolute() else _repo_root() / path
        candidate = _team_builder_source_candidate(resolved)
        if candidate:
            candidates.append({k: v for k, v in candidate.items() if k != "_key"})
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in candidates:
        key = _safe_text(item.get("path"), 320)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:4]


def _team_builder_generated_source_candidates(test_report: dict[str, Any]) -> list[dict[str, Any]]:
    source = _dict_value(test_report.get("source"))
    roots: list[Path] = []
    for key in ["test_package_dir", "code_package_files"]:
        raw = _safe_text(source.get(key), 320)
        if not raw:
            continue
        path = Path(raw)
        root = path if path.is_absolute() else _repo_root() / path
        if root.is_dir():
            roots.append(root)
    paths: list[Path] = []
    for root in roots:
        paths.extend([root / "team.py", root / "run.py"])
        workers_dir = root / "workers"
        if workers_dir.is_dir():
            paths.extend(sorted(path for path in workers_dir.glob("*.py") if path.name != "__init__.py"))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for path in paths:
        candidate = _team_builder_source_candidate(path)
        if not candidate:
            continue
        key = _safe_text(candidate.get("_key"), 320)
        if key in seen:
            continue
        seen.add(key)
        out.append({k: v for k, v in candidate.items() if k != "_key"})
    return out[:8]


def _team_builder_repair_patch_candidate_from_action(
    action: dict[str, Any],
    index: int,
    *,
    test_report: dict[str, Any],
) -> dict[str, Any]:
    category = _safe_text(action.get("category"), 80)
    check_id = _safe_text(action.get("check_id"), 160)
    node_ids = [_safe_text(item, 160) for item in _list_value(action.get("node_ids")) if _safe_text(item, 160)]
    worker_id = node_ids[0] if node_ids else (
        _safe_text(action.get("location"), 180).removeprefix("node:")
        if _safe_text(action.get("location"), 180).startswith("node:")
        else ""
    )
    contract_sources: list[dict[str, Any]] = []
    if check_id == "team_builder.contract.execution_failed":
        source_candidates = _team_builder_generated_source_candidates(test_report)
        contract_sources = _team_builder_contract_source_candidates(action)
    else:
        source_candidates = _team_builder_worker_source_candidates(worker_id, test_report) if worker_id else []
    located = [item for item in source_candidates if item.get("exists")]
    applicable = category == "repair_required"
    if not applicable:
        status = "not_applicable"
        summary = "这条 action 不是 repair_required；不生成代码补丁候选。"
    elif check_id == "team_builder.contract.execution_failed" and located and contract_sources:
        status = "source_located"
        summary = "已定位失败 contract 和 generated package 源码，可进入人工确认或 AI 补丁生成。"
    elif located:
        status = "source_located"
        summary = f"已定位 {worker_id} 的 worker 源文件，可进入人工确认或 AI 补丁生成。"
    else:
        status = "needs_source_locator"
        summary = (
            "需要先定位失败 contract 和 generated package 源码。"
            if check_id == "team_builder.contract.execution_failed"
            else f"需要先定位 {worker_id or '目标 worker'} 的 generated worker 源文件。"
        )
    return {
        "id": f"repair_patch_candidate:{index}",
        "status": status,
        "finding_id": _safe_text(action.get("finding_id"), 220),
        "check_id": check_id,
        "worker_id": worker_id,
        "category": category,
        "policy_rule_id": _safe_text(action.get("policy_rule_id"), 120),
        "automation_level": _safe_text(action.get("automation_level"), 80),
        "auto_safe": bool(action.get("auto_safe")),
        "summary": summary,
        "observation": _safe_text(action.get("observation"), 420),
        "next_action": (
            "先审阅定位到的 worker 源码和输入输出 material；生成最小补丁计划后，只能在 scratch 或人工确认路径应用并重跑验证。"
            if applicable and located
            and check_id != "team_builder.contract.execution_failed"
            else "先审阅失败 contract、输入样例和 generated 源码；补丁只能改 generated package，不能为了通过而改 contract。"
            if applicable and located and check_id == "team_builder.contract.execution_failed"
            else _safe_text(action.get("next_action"), 420)
        ),
        "source_candidates": source_candidates,
        "contract_sources": contract_sources,
        "proposed_patch": {
            "mode": "manual_or_ai_generated",
            "scope": "generated_package_only" if check_id == "team_builder.contract.execution_failed" else "generated_worker_only",
            "changed_files": [item["path"] for item in located[:3 if check_id == "team_builder.contract.execution_failed" else 1]],
            "diff": "",
            "reason": (
                "contract 失败的补丁需要同时审阅失败样例和 generated package 业务语义；当前层只产出候选补丁计划，不直接改代码。"
                if check_id == "team_builder.contract.execution_failed"
                else "真实 generated worker 的补丁需要基于源码和业务语义生成；当前层只产出候选补丁计划，不直接改代码。"
            ),
        },
        "verification_commands": [
            *(
                ["GET /api/team-builder-materialization/contract-execution/latest"]
                if check_id == "team_builder.contract.execution_failed" else []
            ),
            "GET /api/team-builder-materialization/test-report/latest",
            "GET /api/team-builder-materialization/doctor-findings/latest",
            "GET /api/team-builder-materialization/closure/latest",
        ],
        "safety": {
            "dry_run_first": True,
            "requires_human_confirmation": True,
            "auto_apply_allowed": False,
            "reason": "repair_required 表示确有运行失败，但仍不能绕过人工确认直接修改真实 generated code。",
        },
    }


def _team_builder_repair_patch_candidates_report() -> dict[str, Any]:
    repair_plan = _team_builder_latest_repair_plan()
    test_report = _team_builder_test_report()
    if not repair_plan.get("available"):
        return {
            "available": False,
            "reason": _safe_text(repair_plan.get("reason"), 500),
            "run_id": _safe_text(repair_plan.get("run_id"), 160),
            "team_name": _safe_text(repair_plan.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无 repair plan，无法生成候选补丁计划。",
            "counts": {
                "actions": 0,
                "candidates": 0,
                "source_located": 0,
                "source_missing": 0,
                "dry_run_verified": 0,
                "auto_safe": 0,
                "manual_required": 0,
            },
            "quality_gates": [],
            "candidates": [],
            "source": repair_plan.get("source") if isinstance(repair_plan.get("source"), dict) else {},
        }

    actions = [_dict_value(item) for item in _list_value(repair_plan.get("actions"))]
    candidates = [
        _team_builder_repair_patch_candidate_from_action(action, index, test_report=test_report)
        for index, action in enumerate(actions)
        if _safe_text(action.get("category"), 80) == "repair_required"
    ]
    source_located = sum(1 for item in candidates if item.get("status") == "source_located")
    dry_run = _team_builder_repair_dry_run_report()
    dry_run_verified = 1 if dry_run.get("verdict") == "pass" else 0
    auto_safe = sum(1 for item in candidates if _dict_value(item.get("safety")).get("auto_apply_allowed"))
    manual_required = sum(
        1
        for item in candidates
        if _dict_value(item.get("safety")).get("requires_human_confirmation")
    )
    source_missing = max(0, len(candidates) - source_located)
    if candidates and source_located == len(candidates) and dry_run_verified:
        verdict = "ready_for_manual_patch"
    elif candidates:
        verdict = "needs_locator_or_dry_run"
    else:
        verdict = "clean"
    gates = [
        _test_gate(
            "repair_required_inputs",
            "repair_required 输入明确",
            "pass" if not actions or candidates else "warning",
            f"{len(candidates)} 条 repair_required action 进入候选补丁计划。"
            if candidates else "当前 repair plan 没有 repair_required action。",
            [_safe_text(item.get("finding_id"), 180) for item in candidates[:5]],
        ),
        _test_gate(
            "source_locator",
            "源码和 contract 可定位",
            "pass" if not candidates or source_located == len(candidates) else "warning",
            f"{source_located}/{len(candidates)} 条候选已定位可审阅源码。"
            if candidates else "没有需要定位的 repair 候选。",
            [
                _safe_text(candidate.get("worker_id") or candidate.get("finding_id"), 180)
                for candidate in candidates
                if candidate.get("status") != "source_located"
            ][:5],
        ),
        _test_gate(
            "dry_run_reference",
            "受控干跑参考通过",
            "pass" if dry_run_verified else "warning",
            "受控可修复故障已在 scratch 内完成补丁干跑并清零 finding。"
            if dry_run_verified else "还没有通过的 repair dry-run 参考。",
            [f"repair_dry_run={_safe_text(dry_run.get('verdict'), 40)}"],
        ),
        _test_gate(
            "auto_apply_blocked",
            "自动改码仍然阻断",
            "pass" if auto_safe == 0 else "fail",
            "候选补丁计划只允许人工确认或 scratch 干跑，不允许自动修改真实 generated code。",
            [f"auto_safe={auto_safe}"],
        ),
    ]
    run_id = _safe_text(repair_plan.get("run_id") or test_report.get("run_id"), 160)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(repair_plan.get("team_name") or test_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": (
            f"修复候选补丁计划 {verdict}: repair action {len(actions)} 条，"
            f"repair_required 候选 {len(candidates)} 条，已定位源码 {source_located} 条，"
            f"受控干跑参考 {'已通过' if dry_run_verified else '未通过'}。"
        ),
        "counts": {
            "actions": len(actions),
            "candidates": len(candidates),
            "source_located": source_located,
            "source_missing": source_missing,
            "dry_run_verified": dry_run_verified,
            "auto_safe": auto_safe,
            "manual_required": manual_required,
        },
        "quality_gates": gates,
        "candidates": candidates,
        "dry_run_reference": {
            "verdict": _safe_text(dry_run.get("verdict"), 40),
            "summary": _safe_text(dry_run.get("summary"), 520),
            "counts": _dict_value(dry_run.get("counts")),
            "source": _dict_value(dry_run.get("source")),
        },
        "source": {
            **(_dict_value(repair_plan.get("source"))),
            "test_report_endpoint": "/api/team-builder-materialization/test-report/latest",
            "repair_dry_run_endpoint": "/api/team-builder-materialization/repair-dry-run/latest",
            "repair_patch_candidates_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_patch_candidates.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        out_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_patch_candidates.json"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repair_apply_gate_report() -> dict[str, Any]:
    candidates_report = _team_builder_repair_patch_candidates_report()
    if not candidates_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(candidates_report.get("reason"), 500),
            "run_id": _safe_text(candidates_report.get("run_id"), 160),
            "team_name": _safe_text(candidates_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无候选补丁计划，无法生成修复应用门。",
            "counts": {
                "candidates": 0,
                "source_located": 0,
                "dry_run_verified": 0,
                "manual_required": 0,
                "auto_apply_allowed": 0,
                "review_items": 0,
                "apply_ready": 0,
            },
            "quality_gates": [],
            "review_items": [],
            "source": candidates_report.get("source") if isinstance(candidates_report.get("source"), dict) else {},
        }

    candidates = [_dict_value(item) for item in _list_value(candidates_report.get("candidates"))]
    source_located = sum(1 for item in candidates if _safe_text(item.get("status"), 80) == "source_located")
    dry_run_verified = int(_dict_value(candidates_report.get("counts")).get("dry_run_verified") or 0)
    manual_required = sum(
        1
        for item in candidates
        if _dict_value(item.get("safety")).get("requires_human_confirmation")
    )
    auto_apply_allowed = sum(
        1
        for item in candidates
        if _dict_value(item.get("safety")).get("auto_apply_allowed")
    )
    review_items: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        check_id = _safe_text(candidate.get("check_id"), 160)
        is_contract_failure = check_id == "team_builder.contract.execution_failed"
        source_candidates = [_dict_value(item) for item in _list_value(candidate.get("source_candidates"))]
        located_sources = [item for item in source_candidates if item.get("exists")]
        contract_sources = [_dict_value(item) for item in _list_value(candidate.get("contract_sources"))]
        located_contract_sources = [item for item in contract_sources if item.get("exists")]
        changed_files = [
            _safe_text(item, 320)
            for item in _list_value(_dict_value(candidate.get("proposed_patch")).get("changed_files"))
            if _safe_text(item, 320)
        ]
        verification_commands = [
            _safe_text(item, 260)
            for item in _list_value(candidate.get("verification_commands"))
            if _safe_text(item, 260)
        ]
        blocked_reasons: list[str] = []
        if _safe_text(candidate.get("status"), 80) != "source_located":
            blocked_reasons.append(
                "还没有同时定位失败 contract 和 generated package 源码。"
                if is_contract_failure
                else "还没有定位到可审阅的 generated worker 源文件。"
            )
        if is_contract_failure and not located_contract_sources:
            blocked_reasons.append("contract 执行失败缺少可审阅的 contract 定义文件。")
        if not changed_files:
            blocked_reasons.append("候选补丁还没有明确目标文件。")
        if is_contract_failure and any(
            item.replace("\\", "/").startswith("tests/teams/")
            for item in changed_files
        ):
            blocked_reasons.append("contract 失败修复不能把 contract 文件列为补丁目标。")
        if not verification_commands:
            blocked_reasons.append("候选补丁缺少回查命令。")
        if not dry_run_verified:
            blocked_reasons.append("受控 repair dry-run 尚未通过。")
        if _dict_value(candidate.get("safety")).get("auto_apply_allowed"):
            blocked_reasons.append("候选意外允许自动应用，违反当前安全策略。")
        status = "ready_for_human_review" if not blocked_reasons else "blocked"
        if is_contract_failure:
            required_confirmations = [
                "确认 contract 失败仍然可复现，且失败来自 generated package 行为而不是 contract 本身错误。",
                "确认 contract 文件只作为验收定义审阅，不作为补丁目标。",
                "确认补丁 diff 只触碰候选 generated package 文件，不能为了通过而修改 contract。",
                "确认回查命令覆盖 contract-execution、test-report、doctor-findings 和 closure。",
                "确认人工批准后才允许进入真实补丁生成或应用流程。",
            ]
        else:
            required_confirmations = [
                "确认 finding 仍然可复现，且确实指向该 worker。",
                "确认源码文件、输入 material、输出 material 与候选补丁范围一致。",
                "确认补丁 diff 只触碰候选 generated worker 文件。",
                "确认回查命令至少覆盖 test-report、doctor-findings 和 closure。",
                "确认人工批准后才允许进入真实补丁生成或应用流程。",
            ]
        review_items.append({
            "id": f"repair_apply_gate:{index}",
            "candidate_id": _safe_text(candidate.get("id"), 160),
            "status": status,
            "check_id": check_id,
            "worker_id": _safe_text(candidate.get("worker_id"), 160),
            "finding_id": _safe_text(candidate.get("finding_id"), 220),
            "policy_rule_id": _safe_text(candidate.get("policy_rule_id"), 120),
            "changed_files": changed_files,
            "source_files": [
                _safe_text(item.get("path"), 320)
                for item in located_sources
                if _safe_text(item.get("path"), 320)
            ],
            "contract_files": [
                _safe_text(item.get("path"), 320)
                for item in located_contract_sources
                if _safe_text(item.get("path"), 320)
            ],
            "required_confirmations": required_confirmations,
            "verification_commands": verification_commands,
            "apply_modes": [
                {
                    "id": "scratch_preview",
                    "name": "scratch 预览",
                    "allowed": True,
                    "summary": "允许在 scratch 或临时副本中生成补丁 diff 并重跑验证。",
                },
                {
                    "id": "manual_patch",
                    "name": "人工确认后应用",
                    "allowed": False,
                    "summary": "当前接口不执行真实改码；需要后续人工确认协议或显式执行接口。",
                },
            ],
            "blocked_reasons": blocked_reasons,
            "safety": {
                "auto_apply_allowed": False,
                "requires_human_confirmation": True,
                "reason": "修复应用门只负责审阅放行条件，不会自动修改真实 generated code。",
            },
        })

    ready_items = sum(1 for item in review_items if item["status"] == "ready_for_human_review")
    if not candidates:
        verdict = "clean"
        summary = "当前真实 run 没有 repair_required 候选；修复应用门保持关闭，等待真实故障。"
    elif ready_items == len(candidates) and dry_run_verified and auto_apply_allowed == 0:
        verdict = "ready_for_human_review"
        summary = f"{ready_items} 条候选已满足人工审阅前置条件；仍不允许自动应用真实补丁。"
    else:
        verdict = "blocked"
        summary = f"{len(candidates) - ready_items} 条候选仍缺源码定位、回查命令或受控干跑证据；修复应用门保持阻断。"
    gates = [
        _test_gate(
            "candidate_review_scope",
            "候选范围可审阅",
            "pass" if not candidates or source_located == len(candidates) else "warning",
            f"{source_located}/{len(candidates)} 条候选已定位源码。"
            if candidates else "当前没有需要审阅的候选补丁。",
            [
                _safe_text(item.get("worker_id"), 120)
                for item in candidates
                if _safe_text(item.get("status"), 80) != "source_located"
            ][:5],
        ),
        _test_gate(
            "dry_run_reference_required",
            "受控干跑是前置条件",
            "pass" if dry_run_verified else "warning",
            "受控 repair dry-run 已通过，可以作为真实修复前置参考。"
            if dry_run_verified else "受控 repair dry-run 尚未通过，不能进入真实补丁审阅。",
            [f"dry_run_verified={dry_run_verified}"],
        ),
        _test_gate(
            "manual_review_required",
            "人工确认仍然必需",
            "pass" if not candidates or manual_required == len(candidates) else "fail",
            f"{manual_required}/{len(candidates)} 条候选要求人工确认。"
            if candidates else "当前无候选；人工确认门未开启。",
            [f"manual_required={manual_required}"],
        ),
        _test_gate(
            "auto_apply_blocked",
            "真实自动应用被阻断",
            "pass" if auto_apply_allowed == 0 else "fail",
            "当前应用门不允许自动修改真实 generated code。",
            [f"auto_apply_allowed={auto_apply_allowed}"],
        ),
        _test_gate(
            "verification_commands_present",
            "回查命令完整",
            "pass" if not candidates or all(_list_value(item.get("verification_commands")) for item in review_items) else "warning",
            "候选补丁均列出 test-report、doctor-findings 和 closure 等回查入口。"
            if candidates else "当前无候选；无需回查命令。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in review_items
                if not _list_value(item.get("verification_commands"))
            ][:5],
        ),
    ]
    run_id = _safe_text(candidates_report.get("run_id"), 160)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(candidates_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "candidates": len(candidates),
            "source_located": source_located,
            "dry_run_verified": dry_run_verified,
            "manual_required": manual_required,
            "auto_apply_allowed": auto_apply_allowed,
            "review_items": len(review_items),
            "apply_ready": ready_items,
        },
        "quality_gates": gates,
        "review_items": review_items,
        "source": {
            **(_dict_value(candidates_report.get("source"))),
            "repair_patch_candidates_endpoint": "/api/team-builder-materialization/repair-patch-candidates/latest",
            "repair_apply_gate_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_apply_gate.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        out_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_apply_gate.json"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repo_file_from_relpath(rel_path: str) -> Path | None:
    if not rel_path:
        return None
    raw = Path(rel_path)
    path = raw if raw.is_absolute() else _repo_root() / raw
    try:
        resolved = path.resolve()
        resolved.relative_to(_repo_root().resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _team_builder_diff_text(rel_path: str, before: str, after: str) -> str:
    normalized = rel_path.replace("\\", "/")
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"before/{normalized}",
        tofile=f"after/{normalized}",
    ))


def _team_builder_normalize_diff_file_path(raw_path: str) -> str:
    text = _safe_text(raw_path, 500).strip().strip('"')
    if "\t" in text:
        text = text.split("\t", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    text = text.replace("\\", "/")
    for prefix in ("a/", "b/", "before/", "after/"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text.lstrip("/")


def _team_builder_split_unified_diff_by_file(diff_text: str) -> dict[str, str]:
    lines = diff_text.splitlines(keepends=True)
    starts: list[int] = []
    for index, line in enumerate(lines):
        if line.startswith("diff --git "):
            starts.append(index)
        elif line.startswith("--- ") and index + 1 < len(lines) and lines[index + 1].startswith("+++ "):
            starts.append(index)
    if not starts:
        return {}

    blocks: dict[str, str] = {}
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        block_lines = lines[start:end]
        rel_path = ""
        if block_lines and block_lines[0].startswith("diff --git "):
            parts = block_lines[0].strip().split()
            if len(parts) >= 4:
                rel_path = _team_builder_normalize_diff_file_path(parts[3])
        for line in block_lines:
            if line.startswith("+++ "):
                rel_path = _team_builder_normalize_diff_file_path(line[4:])
                break
        if rel_path and rel_path != "dev/null":
            blocks[rel_path] = "".join(block_lines)
    return blocks


def _team_builder_deterministic_patch_diff(candidate: dict[str, Any]) -> dict[str, Any]:
    proposed_patch = _dict_value(candidate.get("proposed_patch"))
    changed_files = [
        _safe_text(item, 320)
        for item in _list_value(proposed_patch.get("changed_files"))
        if _safe_text(item, 320)
    ]
    if not changed_files:
        return {"status": "needs_ai_or_human_diff", "reason": "候选补丁缺少目标文件，无法生成 diff。"}
    check_id = _safe_text(candidate.get("check_id"), 160)
    if check_id == "team_builder.contract.execution_failed":
        return {
            "status": "needs_ai_or_human_diff",
            "reason": "contract failure 需要理解失败样例和业务语义，当前不生成机械 diff。",
        }
    replacement_groups = [
        [
            ("kind=VerdictKind.FAIL", "kind=VerdictKind.PASS"),
            ("'probe': 'controlled_failure'", "'probe': 'repaired_success'"),
            (
                "controlled failure: repair probe worker returned FAIL on purpose",
                "repair dry-run success: probe worker returned PASS after scoped patch",
            ),
        ],
        [
            ("'kind': VerdictKind.FAIL", "'kind': VerdictKind.PASS"),
            ("'probe': 'controlled_failure'", "'probe': 'repaired_success'"),
            (
                "controlled failure: repair probe worker returned FAIL on purpose",
                "repair dry-run success: probe worker returned PASS after scoped patch",
            ),
        ],
    ]
    for rel_path in changed_files[:3]:
        source_path = _team_builder_repo_file_from_relpath(rel_path)
        if source_path is None:
            continue
        try:
            before = source_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for replacements in replacement_groups:
            if not all(old in before for old, _new in replacements):
                continue
            after = before
            for old, new in replacements:
                after = after.replace(old, new, 1)
            if after != before:
                return {
                    "status": "diff_ready",
                    "changed_file": rel_path,
                    "diff": _safe_text(_team_builder_diff_text(rel_path, before, after), 12000),
                    "replacements": len(replacements),
                    "reason": "命中受控失败探针的确定性补丁规则；这里只生成 diff，不修改真实文件。",
                }
    return {
        "status": "needs_ai_or_human_diff",
        "reason": "没有命中可安全机械替换的已知失败模式，需要 AI 或人工基于源码语义生成补丁 diff。",
    }


def _team_builder_repair_patch_diff_proposal_report() -> dict[str, Any]:
    candidates_report = _team_builder_repair_patch_candidates_report()
    apply_gate = _team_builder_repair_apply_gate_report()
    if not candidates_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(candidates_report.get("reason"), 500),
            "run_id": _safe_text(candidates_report.get("run_id"), 160),
            "team_name": _safe_text(candidates_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无候选补丁计划，无法生成补丁 diff proposal。",
            "counts": {
                "candidates": 0,
                "diff_ready": 0,
                "needs_ai_or_human_diff": 0,
                "blocked": 0,
                "unsafe_targets": 0,
            },
            "quality_gates": [],
            "proposals": [],
            "source": candidates_report.get("source") if isinstance(candidates_report.get("source"), dict) else {},
        }

    candidates = [_dict_value(item) for item in _list_value(candidates_report.get("candidates"))]
    review_items = [_dict_value(item) for item in _list_value(apply_gate.get("review_items"))]
    review_by_candidate = {
        _safe_text(item.get("candidate_id"), 160): item
        for item in review_items
        if _safe_text(item.get("candidate_id"), 160)
    }
    proposals: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        candidate_id = _safe_text(candidate.get("id"), 160)
        check_id = _safe_text(candidate.get("check_id"), 160)
        review = review_by_candidate.get(candidate_id, {})
        proposed_patch = _dict_value(candidate.get("proposed_patch"))
        changed_files = [
            _safe_text(item, 320)
            for item in _list_value(proposed_patch.get("changed_files"))
            if _safe_text(item, 320)
        ]
        existing_diff = _safe_text(proposed_patch.get("diff"), 12000)
        unsafe_target = check_id == "team_builder.contract.execution_failed" and any(
            item.replace("\\", "/").startswith("tests/teams/")
            for item in changed_files
        )
        missing: list[str] = []
        if _safe_text(candidate.get("status"), 80) != "source_located":
            missing.append("候选还没有定位到可审阅源码。")
        if _safe_text(review.get("status"), 80) == "blocked":
            missing.append("修复应用门仍然阻断该候选。")
        if not changed_files:
            missing.append("候选补丁缺少目标文件。")
        if unsafe_target:
            missing.append("contract failure 的补丁目标不能是 tests/teams 下的 contract 文件。")
        diff_result: dict[str, Any] = {}
        if missing:
            status = "blocked"
            reason = "；".join(missing)
            diff_text = ""
            diff_source = ""
        elif existing_diff.strip():
            status = "diff_ready"
            reason = "候选补丁已经带有 diff；当前层只复核并透传。"
            diff_text = existing_diff
            diff_source = "candidate_existing_diff"
        else:
            diff_result = _team_builder_deterministic_patch_diff(candidate)
            status = _safe_text(diff_result.get("status"), 80) or "needs_ai_or_human_diff"
            reason = _safe_text(diff_result.get("reason"), 520)
            diff_text = _safe_text(diff_result.get("diff"), 12000)
            diff_source = "deterministic_rule" if status == "diff_ready" else ""
        proposals.append({
            "id": f"repair_patch_diff_proposal:{index}",
            "candidate_id": candidate_id,
            "status": status,
            "check_id": check_id,
            "worker_id": _safe_text(candidate.get("worker_id"), 160),
            "finding_id": _safe_text(candidate.get("finding_id"), 220),
            "changed_files": changed_files,
            "diff": diff_text,
            "diff_source": diff_source,
            "reason": reason,
            "missing_requirements": missing,
            "patch_request": {
                "summary": "基于源码语义生成最小 diff；不得修改 contract；生成后必须回到执行就绪检查。",
                "context_files": [
                    *[_safe_text(item, 320) for item in _list_value(review.get("contract_files")) if _safe_text(item, 320)],
                    *changed_files,
                ][:8],
                "verification_commands": [
                    _safe_text(item, 260)
                    for item in _list_value(candidate.get("verification_commands"))
                    if _safe_text(item, 260)
                ],
            },
            "safety": {
                "writes_files": False,
                "applies_to_real_code": False,
                "requires_human_confirmation": True,
                "reason": "diff proposal 只生成审阅材料，不写入真实文件，也不绕过显式批准。",
            },
        })

    diff_ready = sum(1 for item in proposals if item.get("status") == "diff_ready")
    blocked = sum(1 for item in proposals if item.get("status") == "blocked")
    needs_ai = sum(1 for item in proposals if item.get("status") == "needs_ai_or_human_diff")
    unsafe_targets = sum(
        1
        for item in proposals
        if any("tests/teams" in text for text in _list_value(item.get("missing_requirements")))
    )
    if not proposals:
        verdict = "clean"
        summary = "当前没有 repair_required 候选；无需生成补丁 diff。"
    elif blocked or unsafe_targets:
        verdict = "blocked"
        summary = f"{blocked} 条候选无法生成安全 diff；需要先修正应用门或目标范围。"
    elif diff_ready == len(proposals):
        verdict = "diff_ready"
        summary = f"{diff_ready} 条候选已有可审阅 diff；仍需显式人工批准后才能执行。"
    else:
        verdict = "needs_ai_or_human_diff"
        summary = f"{needs_ai} 条候选需要 AI 或人工生成语义补丁 diff。"
    gates = [
        _test_gate(
            "candidate_scope_ready",
            "候选范围可用于生成 diff",
            "pass" if not proposals or blocked == 0 else "warning",
            "候选均已通过基础审阅范围检查。"
            if blocked == 0 else f"{blocked} 条候选仍被阻断。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in proposals
                if item.get("status") == "blocked"
            ][:5],
        ),
        _test_gate(
            "target_scope_safe",
            "补丁目标范围安全",
            "pass" if unsafe_targets == 0 else "fail",
            "没有候选把 contract 文件列为 diff 目标。"
            if unsafe_targets == 0 else f"{unsafe_targets} 条候选目标文件越界。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in proposals
                if any("tests/teams" in text for text in _list_value(item.get("missing_requirements")))
            ][:5],
        ),
        _test_gate(
            "diff_available",
            "可审阅 diff 已生成",
            "pass" if not proposals or diff_ready == len(proposals) else "warning",
            f"{diff_ready}/{len(proposals)} 条候选已有 diff。"
            if proposals else "当前没有候选需要 diff。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in proposals
                if item.get("status") != "diff_ready"
            ][:5],
        ),
        _test_gate(
            "no_real_write",
            "不写真实文件",
            "pass",
            "diff proposal 只写 material 报告，不修改 generated package。",
            ["writes_files=false"],
        ),
    ]
    run_id = _safe_text(candidates_report.get("run_id") or apply_gate.get("run_id"), 160)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(candidates_report.get("team_name") or apply_gate.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "candidates": len(proposals),
            "diff_ready": diff_ready,
            "needs_ai_or_human_diff": needs_ai,
            "blocked": blocked,
            "unsafe_targets": unsafe_targets,
        },
        "quality_gates": gates,
        "proposals": proposals,
        "source": {
            **(_dict_value(candidates_report.get("source"))),
            "repair_patch_candidates_endpoint": "/api/team-builder-materialization/repair-patch-candidates/latest",
            "repair_apply_gate_endpoint": "/api/team-builder-materialization/repair-apply-gate/latest",
            "repair_patch_diff_proposal_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_patch_diff_proposal.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        out_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_patch_diff_proposal.json"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_diff_sha256(diff_text: str) -> str:
    return hashlib.sha256(diff_text.encode("utf-8")).hexdigest() if diff_text else ""


def _team_builder_repair_approval_records_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_approval_records.json"


def _team_builder_read_repair_approval_records(run_id: str) -> list[dict[str, Any]]:
    path = _team_builder_repair_approval_records_path(run_id)
    if path is None or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = _list_value(payload.get("records")) if isinstance(payload, dict) else _list_value(payload)
    return [_dict_value(item) for item in records]


def _team_builder_write_repair_approval_records(run_id: str, records: list[dict[str, Any]]) -> str:
    path = _team_builder_repair_approval_records_path(run_id)
    if path is None:
        return ""
    payload = {
        "run_id": _safe_text(run_id, 160),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(_repo_root()))


def _team_builder_repair_approval_report() -> dict[str, Any]:
    diff_report = _team_builder_repair_patch_diff_proposal_report()
    if not diff_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(diff_report.get("reason"), 500),
            "run_id": _safe_text(diff_report.get("run_id"), 160),
            "team_name": _safe_text(diff_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无补丁 diff proposal，无法检查人工批准。",
            "counts": {
                "proposals": 0,
                "approvable": 0,
                "approved": 0,
                "awaiting_approval": 0,
                "stale_or_mismatch": 0,
            },
            "quality_gates": [],
            "approval_items": [],
            "source": diff_report.get("source") if isinstance(diff_report.get("source"), dict) else {},
        }
    run_id = _safe_text(diff_report.get("run_id"), 160)
    records = _team_builder_read_repair_approval_records(run_id)
    records_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        candidate_id = _safe_text(record.get("candidate_id"), 160)
        if candidate_id:
            records_by_candidate.setdefault(candidate_id, []).append(record)
    approval_items: list[dict[str, Any]] = []
    for proposal in [_dict_value(item) for item in _list_value(diff_report.get("proposals"))]:
        candidate_id = _safe_text(proposal.get("candidate_id"), 160)
        diff_text = _safe_text(proposal.get("diff"), 20000)
        diff_sha256 = _team_builder_diff_sha256(diff_text)
        matching_records = [
            record for record in records_by_candidate.get(candidate_id, [])
            if bool(record.get("approved")) and _safe_text(record.get("diff_sha256"), 80) == diff_sha256
        ]
        stale_records = [
            record for record in records_by_candidate.get(candidate_id, [])
            if _safe_text(record.get("diff_sha256"), 80) and _safe_text(record.get("diff_sha256"), 80) != diff_sha256
        ]
        latest_match = matching_records[-1] if matching_records else {}
        approvable = _safe_text(proposal.get("status"), 80) == "diff_ready" and bool(diff_sha256)
        approval_valid = approvable and bool(matching_records)
        if approval_valid:
            status = "approved"
            summary = "当前 diff 已有匹配的显式人工批准。"
        elif not approvable:
            status = "not_approvable"
            summary = "候选还没有可批准 diff。"
        elif stale_records:
            status = "stale_or_mismatch"
            summary = "已有批准记录，但 diff sha256 与当前 proposal 不一致，需要重新批准。"
        else:
            status = "awaiting_approval"
            summary = "当前 diff 尚未记录显式人工批准。"
        approval_items.append({
            "candidate_id": candidate_id,
            "proposal_id": _safe_text(proposal.get("id"), 160),
            "status": status,
            "approval_valid": approval_valid,
            "approvable": approvable,
            "diff_sha256": diff_sha256,
            "approved_by": _safe_text(latest_match.get("approved_by"), 120),
            "approved_at": _safe_text(latest_match.get("approved_at"), 120),
            "summary": summary,
            "changed_files": [
                _safe_text(item, 320)
                for item in _list_value(proposal.get("changed_files"))
                if _safe_text(item, 320)
            ],
            "stale_records": len(stale_records),
        })
    approvable_count = sum(1 for item in approval_items if item.get("approvable"))
    approved_count = sum(1 for item in approval_items if item.get("approval_valid"))
    stale_count = sum(1 for item in approval_items if item.get("status") == "stale_or_mismatch")
    awaiting_count = sum(1 for item in approval_items if item.get("status") == "awaiting_approval")
    if not approval_items:
        verdict = "clean"
        summary = "当前没有可批准的补丁 diff。"
    elif approved_count == approvable_count and approvable_count > 0:
        verdict = "approved"
        summary = f"{approved_count}/{approvable_count} 条可批准 diff 已记录显式人工批准。"
    elif stale_count:
        verdict = "stale_or_mismatch"
        summary = f"{stale_count} 条批准记录与当前 diff 不匹配，需要重新批准。"
    else:
        verdict = "awaiting_approval"
        summary = f"{awaiting_count}/{approvable_count} 条可批准 diff 仍等待显式人工批准。"
    gates = [
        _test_gate(
            "diff_is_approvable",
            "存在可批准 diff",
            "pass" if not approval_items or approvable_count == len(approval_items) else "warning",
            f"{approvable_count}/{len(approval_items)} 条 proposal 可批准。"
            if approval_items else "当前没有 proposal 需要批准。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in approval_items
                if not item.get("approvable")
            ][:5],
        ),
        _test_gate(
            "approval_matches_diff",
            "批准绑定当前 diff",
            "pass" if stale_count == 0 else "fail",
            "没有发现 diff sha256 不匹配的批准记录。"
            if stale_count == 0 else f"{stale_count} 条批准记录已过期或不匹配。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in approval_items
                if item.get("status") == "stale_or_mismatch"
            ][:5],
        ),
        _test_gate(
            "explicit_approval_recorded",
            "显式人工批准已记录",
            "pass" if not approval_items or approved_count == approvable_count else "warning",
            f"{approved_count}/{approvable_count} 条可批准 diff 已批准。"
            if approval_items else "当前没有可批准 diff。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in approval_items
                if item.get("status") == "awaiting_approval"
            ][:5],
        ),
        _test_gate(
            "no_apply_side_effect",
            "批准记录不应用补丁",
            "pass",
            "approval 只写入批准 material，不修改 generated code。",
            ["writes_files=false"],
        ),
    ]
    records_material = str(_team_builder_repair_approval_records_path(run_id).relative_to(_repo_root())) if _team_builder_repair_approval_records_path(run_id) else ""
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(diff_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "proposals": len(approval_items),
            "approvable": approvable_count,
            "approved": approved_count,
            "awaiting_approval": awaiting_count,
            "stale_or_mismatch": stale_count,
        },
        "quality_gates": gates,
        "approval_items": approval_items,
        "source": {
            **(_dict_value(diff_report.get("source"))),
            "repair_patch_diff_proposal_endpoint": "/api/team-builder-materialization/repair-patch-diff-proposal/latest",
            "repair_approval_records_material": records_material,
            "repair_approval_report_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_approval_report.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        out_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_approval_report.json"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_record_repair_approval(payload: dict[str, Any]) -> dict[str, Any]:
    diff_report = _team_builder_repair_patch_diff_proposal_report()
    if not diff_report.get("available"):
        raise HTTPException(status_code=409, detail="暂无可批准的补丁 diff proposal。")
    candidate_id = _safe_text(payload.get("candidate_id"), 160)
    if not candidate_id:
        raise HTTPException(status_code=400, detail="缺少 candidate_id。")
    proposals = [_dict_value(item) for item in _list_value(diff_report.get("proposals"))]
    proposal = next((item for item in proposals if _safe_text(item.get("candidate_id"), 160) == candidate_id), None)
    if proposal is None:
        raise HTTPException(status_code=404, detail="找不到对应 candidate 的 diff proposal。")
    if _safe_text(proposal.get("status"), 80) != "diff_ready":
        raise HTTPException(status_code=409, detail="该候选还没有可批准 diff。")
    diff_text = _safe_text(proposal.get("diff"), 20000)
    diff_sha256 = _team_builder_diff_sha256(diff_text)
    expected_hash = _safe_text(payload.get("diff_sha256"), 80)
    if expected_hash and expected_hash != diff_sha256:
        raise HTTPException(status_code=409, detail="diff_sha256 与当前 proposal 不一致。")
    if payload.get("approved") is not True:
        raise HTTPException(status_code=400, detail="必须显式传入 approved=true。")
    approved_by = _safe_text(payload.get("approved_by"), 120)
    if not approved_by:
        raise HTTPException(status_code=400, detail="缺少 approved_by。")
    reason = _safe_text(payload.get("reason"), 520)
    if not reason:
        raise HTTPException(status_code=400, detail="缺少批准理由 reason。")
    run_id = _safe_text(diff_report.get("run_id"), 160)
    records = _team_builder_read_repair_approval_records(run_id)
    record = {
        "id": f"repair_approval:{candidate_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "run_id": run_id,
        "team_name": _safe_text(diff_report.get("team_name"), 160),
        "candidate_id": candidate_id,
        "proposal_id": _safe_text(proposal.get("id"), 160),
        "approved": True,
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "diff_sha256": diff_sha256,
        "changed_files": [
            _safe_text(item, 320)
            for item in _list_value(proposal.get("changed_files"))
            if _safe_text(item, 320)
        ],
        "confirmations": [
            _safe_text(item, 300)
            for item in _list_value(payload.get("confirmations"))
            if _safe_text(item, 300)
        ],
    }
    records.append(record)
    _team_builder_write_repair_approval_records(run_id, records)
    return _team_builder_repair_approval_report()


def _team_builder_repair_execution_readiness_report() -> dict[str, Any]:
    candidates_report = _team_builder_repair_patch_candidates_report()
    apply_gate = _team_builder_repair_apply_gate_report()
    diff_proposal = _team_builder_repair_patch_diff_proposal_report()
    approval_report = _team_builder_repair_approval_report()
    if not apply_gate.get("available"):
        return {
            "available": False,
            "reason": _safe_text(apply_gate.get("reason"), 500),
            "run_id": _safe_text(apply_gate.get("run_id"), 160),
            "team_name": _safe_text(apply_gate.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无修复应用门，无法判断真实修复执行就绪状态。",
            "counts": {
                "candidates": 0,
                "review_ready": 0,
                "diff_ready": 0,
                "approval_recorded": 0,
                "execution_ready": 0,
                "blocked": 0,
            },
            "quality_gates": [],
            "execution_items": [],
            "source": apply_gate.get("source") if isinstance(apply_gate.get("source"), dict) else {},
        }

    candidates = [_dict_value(item) for item in _list_value(candidates_report.get("candidates"))]
    review_items = [_dict_value(item) for item in _list_value(apply_gate.get("review_items"))]
    review_by_candidate = {
        _safe_text(item.get("candidate_id"), 160): item
        for item in review_items
        if _safe_text(item.get("candidate_id"), 160)
    }
    diff_by_candidate = {
        _safe_text(item.get("candidate_id"), 160): _dict_value(item)
        for item in _list_value(diff_proposal.get("proposals"))
        if _safe_text(_dict_value(item).get("candidate_id"), 160)
    }
    approval_by_candidate = {
        _safe_text(item.get("candidate_id"), 160): _dict_value(item)
        for item in _list_value(approval_report.get("approval_items"))
        if _safe_text(_dict_value(item).get("candidate_id"), 160)
    }
    execution_items: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        candidate_id = _safe_text(candidate.get("id"), 160)
        review = review_by_candidate.get(candidate_id, {})
        proposal = diff_by_candidate.get(candidate_id, {})
        approval_item = approval_by_candidate.get(candidate_id, {})
        check_id = _safe_text(candidate.get("check_id"), 160)
        proposed_patch = _dict_value(candidate.get("proposed_patch"))
        changed_files = [
            _safe_text(item, 320)
            for item in _list_value(proposed_patch.get("changed_files"))
            if _safe_text(item, 320)
        ]
        diff_text = _safe_text(proposed_patch.get("diff"), 20000) or _safe_text(proposal.get("diff"), 20000)
        verification_commands = [
            _safe_text(item, 260)
            for item in _list_value(candidate.get("verification_commands"))
            if _safe_text(item, 260)
        ]
        approval_recorded = bool(approval_item.get("approval_valid"))
        missing: list[str] = []
        if _safe_text(review.get("status"), 80) != "ready_for_human_review":
            missing.append("修复应用门尚未允许人工审阅。")
        if _safe_text(proposal.get("status"), 80) == "blocked":
            missing.append("补丁 diff proposal 仍然阻断该候选。")
        if not changed_files:
            missing.append("候选补丁缺少目标文件。")
        contract_as_target = check_id == "team_builder.contract.execution_failed" and any(
            item.replace("\\", "/").startswith("tests/teams/")
            for item in changed_files
        )
        if contract_as_target:
            missing.append("contract failure 的补丁目标不能是 tests/teams 下的 contract 文件。")
        if not diff_text.strip():
            missing.append("候选补丁还没有实际 diff，不能进入真实应用。")
        if not verification_commands:
            missing.append("候选补丁缺少修复后的回查命令。")
        if not approval_recorded:
            missing.append("尚未记录显式人工批准。")
        if (
            contract_as_target
            or _safe_text(review.get("status"), 80) == "blocked"
            or _safe_text(proposal.get("status"), 80) == "blocked"
        ):
            status = "blocked"
        elif not diff_text.strip():
            status = "waiting_for_patch_diff"
        elif not approval_recorded:
            status = "awaiting_explicit_approval"
        elif missing:
            status = "blocked"
        else:
            status = "ready_for_explicit_apply"
        execution_items.append({
            "id": f"repair_execution_readiness:{index}",
            "candidate_id": candidate_id,
            "status": status,
            "check_id": check_id,
            "worker_id": _safe_text(candidate.get("worker_id"), 160),
            "finding_id": _safe_text(candidate.get("finding_id"), 220),
            "changed_files": changed_files,
            "contract_files": [
                _safe_text(item, 320)
                for item in _list_value(review.get("contract_files"))
                if _safe_text(item, 320)
            ],
            "review_item_status": _safe_text(review.get("status"), 80),
            "has_diff": bool(diff_text.strip()),
            "diff_source": _safe_text(proposal.get("diff_source"), 80) or (
                "candidate_existing_diff" if _safe_text(proposed_patch.get("diff"), 20000).strip() else ""
            ),
            "approval_recorded": approval_recorded,
            "approval_status": _safe_text(approval_item.get("status"), 80),
            "approval_diff_sha256": _safe_text(approval_item.get("diff_sha256"), 80),
            "missing_requirements": missing,
            "verification_commands": verification_commands,
            "safety": {
                "auto_apply_allowed": False,
                "requires_explicit_approval": True,
                "reason": "真实修复执行必须同时具备可审阅 diff、应用门通过、显式人工批准和回查命令。",
            },
        })

    review_ready = sum(1 for item in execution_items if item.get("review_item_status") == "ready_for_human_review")
    diff_ready = sum(1 for item in execution_items if item.get("has_diff"))
    approval_recorded = sum(1 for item in execution_items if item.get("approval_recorded"))
    execution_ready = sum(1 for item in execution_items if item.get("status") == "ready_for_explicit_apply")
    blocked = sum(1 for item in execution_items if item.get("status") == "blocked")
    waiting_for_diff = sum(1 for item in execution_items if item.get("status") == "waiting_for_patch_diff")
    awaiting_approval = sum(1 for item in execution_items if item.get("status") == "awaiting_explicit_approval")
    bad_targets = sum(
        1
        for item in execution_items
        if any("contract 文件" in text or "tests/teams" in text for text in _list_value(item.get("missing_requirements")))
    )
    candidates_count = len(candidates)
    if not candidates:
        verdict = "clean"
        summary = "当前没有 repair_required 候选；真实修复执行未开启。"
    elif bad_targets or blocked:
        verdict = "blocked"
        summary = f"{blocked} 条候选被执行就绪检查阻断；需要先修正目标文件、应用门或回查条件。"
    elif waiting_for_diff:
        verdict = "waiting_for_patch_diff"
        summary = f"{waiting_for_diff} 条候选还没有实际 diff；只能停留在审阅和补丁生成阶段。"
    elif awaiting_approval:
        verdict = "awaiting_explicit_approval"
        summary = f"{awaiting_approval} 条候选已有 diff 前置条件，但尚未记录显式人工批准。"
    else:
        verdict = "ready_for_explicit_apply"
        summary = f"{execution_ready} 条候选已满足真实修复执行前置条件。"
    gates = [
        _test_gate(
            "apply_gate_review_ready",
            "应用门已允许审阅",
            "pass" if not candidates or review_ready == candidates_count else "warning",
            f"{review_ready}/{candidates_count} 条候选已通过应用门人工审阅前置条件。"
            if candidates else "当前没有 repair_required 候选。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in execution_items
                if item.get("review_item_status") != "ready_for_human_review"
            ][:5],
        ),
        _test_gate(
            "patch_diff_present",
            "实际 diff 已生成",
            "pass" if not candidates or diff_ready == candidates_count else "warning",
            f"{diff_ready}/{candidates_count} 条候选已有实际 diff。"
            if candidates else "当前没有需要生成 diff 的候选。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in execution_items
                if not item.get("has_diff")
            ][:5],
        ),
        _test_gate(
            "target_scope_safe",
            "补丁目标范围安全",
            "pass" if bad_targets == 0 else "fail",
            "没有候选把 contract 文件列为修复目标。"
            if bad_targets == 0 else f"{bad_targets} 条候选目标文件越界。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in execution_items
                if any("tests/teams" in text for text in _list_value(item.get("missing_requirements")))
            ][:5],
        ),
        _test_gate(
            "explicit_approval_recorded",
            "显式人工批准",
            "pass" if not candidates or approval_recorded == candidates_count else "warning",
            f"{approval_recorded}/{candidates_count} 条候选已记录人工批准。"
            if candidates else "当前没有候选需要人工批准。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in execution_items
                if not item.get("approval_recorded")
            ][:5],
        ),
        _test_gate(
            "auto_apply_blocked",
            "自动应用仍被阻断",
            "pass",
            "执行就绪检查只报告条件，不会自动修改真实 generated code。",
            ["auto_apply_allowed=0"],
        ),
    ]
    run_id = _safe_text(apply_gate.get("run_id") or candidates_report.get("run_id"), 160)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(apply_gate.get("team_name") or candidates_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "candidates": candidates_count,
            "review_ready": review_ready,
            "diff_ready": diff_ready,
            "approval_recorded": approval_recorded,
            "execution_ready": execution_ready,
            "blocked": blocked,
        },
        "quality_gates": gates,
        "execution_items": execution_items,
        "source": {
            **(_dict_value(apply_gate.get("source"))),
            "repair_apply_gate_endpoint": "/api/team-builder-materialization/repair-apply-gate/latest",
            "repair_patch_candidates_endpoint": "/api/team-builder-materialization/repair-patch-candidates/latest",
            "repair_patch_diff_proposal_endpoint": "/api/team-builder-materialization/repair-patch-diff-proposal/latest",
            "repair_approval_endpoint": "/api/team-builder-materialization/repair-approval/latest",
            "repair_patch_diff_proposal_material": _safe_text(
                _dict_value(diff_proposal.get("source")).get("repair_patch_diff_proposal_material"), 320
            ),
            "repair_approval_report_material": _safe_text(
                _dict_value(approval_report.get("source")).get("repair_approval_report_material"), 320
            ),
            "repair_approval_records_material": _safe_text(
                _dict_value(approval_report.get("source")).get("repair_approval_records_material"), 320
            ),
            "repair_execution_readiness_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_execution_readiness.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        out_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_execution_readiness.json"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_apply_unified_diff_to_text(before: str, diff_text: str) -> str:
    before_lines = before.splitlines(keepends=True)
    diff_lines = diff_text.splitlines(keepends=True)
    out: list[str] = []
    pointer = 0
    index = 0
    hunk_re = re.compile(r"@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")
    while index < len(diff_lines):
        line = diff_lines[index]
        if not line.startswith("@@"):
            index += 1
            continue
        match = hunk_re.match(line)
        if not match:
            raise ValueError(f"无法解析 diff hunk: {line.strip()}")
        old_start = int(match.group("old_start"))
        target_pointer = max(0, old_start - 1)
        if target_pointer < pointer:
            raise ValueError("diff hunk 顺序不合法。")
        out.extend(before_lines[pointer:target_pointer])
        pointer = target_pointer
        index += 1
        while index < len(diff_lines) and not diff_lines[index].startswith("@@"):
            hunk_line = diff_lines[index]
            if hunk_line.startswith("\\"):
                index += 1
                continue
            marker = hunk_line[:1]
            content = hunk_line[1:]
            if marker == " ":
                if pointer >= len(before_lines) or before_lines[pointer] != content:
                    raise ValueError("diff 上下文与当前文件不匹配。")
                out.append(before_lines[pointer])
                pointer += 1
            elif marker == "-":
                if pointer >= len(before_lines) or before_lines[pointer] != content:
                    raise ValueError("diff 删除行与当前文件不匹配。")
                pointer += 1
            elif marker == "+":
                out.append(content)
            elif hunk_line.startswith(("---", "+++", "diff ")):
                pass
            else:
                raise ValueError(f"未知 diff 行: {hunk_line.strip()}")
            index += 1
    out.extend(before_lines[pointer:])
    return "".join(out)


def _team_builder_repair_apply_preview_report() -> dict[str, Any]:
    readiness = _team_builder_repair_execution_readiness_report()
    diff_report = _team_builder_repair_patch_diff_proposal_report()
    if not readiness.get("available"):
        return {
            "available": False,
            "reason": _safe_text(readiness.get("reason"), 500),
            "run_id": _safe_text(readiness.get("run_id"), 160),
            "team_name": _safe_text(readiness.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无执行就绪报告，无法生成应用预览。",
            "counts": {
                "items": 0,
                "preview_ready": 0,
                "blocked": 0,
                "files_written": 0,
                "files_previewed": 0,
                "multi_file_preview_ready": 0,
                "real_writes": 0,
            },
            "quality_gates": [],
            "preview_items": [],
            "source": readiness.get("source") if isinstance(readiness.get("source"), dict) else {},
        }
    proposals_by_candidate = {
        _safe_text(item.get("candidate_id"), 160): _dict_value(item)
        for item in _list_value(diff_report.get("proposals"))
        if _safe_text(_dict_value(item).get("candidate_id"), 160)
    }
    run_id = _safe_text(readiness.get("run_id"), 160)
    scratch_root = _repo_root() / "_scratch" / "team_builder_repair_apply_preview" / (run_id or "standalone")
    preview_items: list[dict[str, Any]] = []
    files_written = 0
    for index, item in enumerate([_dict_value(raw) for raw in _list_value(readiness.get("execution_items"))]):
        candidate_id = _safe_text(item.get("candidate_id"), 160)
        proposal = proposals_by_candidate.get(candidate_id, {})
        diff_text = _safe_text(proposal.get("diff"), 60000)
        diff_blocks = _team_builder_split_unified_diff_by_file(diff_text)
        safe_candidate_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate_id or f"candidate_{index}")[:120].strip("._")
        if not safe_candidate_id:
            safe_candidate_id = f"candidate_{index}"
        changed_files = [
            _safe_text(path, 320)
            for path in _list_value(item.get("changed_files"))
            if _safe_text(path, 320)
        ]
        blocked_reasons: list[str] = []
        if _safe_text(item.get("status"), 80) != "ready_for_explicit_apply":
            blocked_reasons.append("执行就绪检查尚未放行该候选。")
        if not diff_text.strip():
            blocked_reasons.append("缺少可应用的 diff。")
        if not changed_files:
            blocked_reasons.append("缺少目标文件。")
        missing_diff_blocks = [
            path for path in changed_files
            if len(changed_files) > 1
            and _team_builder_normalize_diff_file_path(path) not in diff_blocks
        ]
        if missing_diff_blocks:
            blocked_reasons.append(f"多文件 diff 缺少逐文件块：{'；'.join(missing_diff_blocks[:5])}")
        if any(path.replace("\\", "/").startswith("tests/teams/") for path in changed_files):
            blocked_reasons.append("目标文件包含 contract，不能生成应用预览。")
        written_paths: list[str] = []
        before_paths: list[str] = []
        file_previews: list[dict[str, Any]] = []
        status = "blocked" if blocked_reasons else "preview_ready"
        if not blocked_reasons:
            try:
                for rel_path in changed_files:
                    source_path = _team_builder_repo_file_from_relpath(rel_path)
                    if source_path is None:
                        raise ValueError(f"找不到目标文件: {rel_path}")
                    normalized_rel = _team_builder_normalize_diff_file_path(rel_path)
                    file_diff = diff_blocks.get(normalized_rel) or (diff_text if len(changed_files) == 1 else "")
                    if not file_diff.strip():
                        raise ValueError(f"找不到 {rel_path} 对应的 diff 块。")
                    source_rel_path = source_path.relative_to(_repo_root().resolve())
                    before = source_path.read_text(encoding="utf-8")
                    after = _team_builder_apply_unified_diff_to_text(before, file_diff)
                    if before == after:
                        raise ValueError(f"{rel_path} 的 diff 应用后没有产生内容变化。")
                    before_path = scratch_root / safe_candidate_id / "before" / source_rel_path
                    after_path = scratch_root / safe_candidate_id / "after" / source_rel_path
                    before_path.parent.mkdir(parents=True, exist_ok=True)
                    after_path.parent.mkdir(parents=True, exist_ok=True)
                    before_path.write_text(before, encoding="utf-8")
                    after_path.write_text(after, encoding="utf-8")
                    before_paths.append(str(before_path.relative_to(_repo_root())))
                    written_paths.append(str(after_path.relative_to(_repo_root())))
                    files_written += 2
                    file_previews.append({
                        "changed_file": str(source_rel_path).replace("\\", "/"),
                        "before_preview_file": str(before_path.relative_to(_repo_root())),
                        "after_preview_file": str(after_path.relative_to(_repo_root())),
                        "before_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                        "after_sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
                        "diff_sha256": _team_builder_diff_sha256(file_diff),
                    })
            except Exception as exc:
                status = "blocked"
                blocked_reasons.append(f"应用预览失败: {type(exc).__name__}: {exc}")
                written_paths = []
                before_paths = []
                file_previews = []
        preview_items.append({
            "id": f"repair_apply_preview:{index}",
            "candidate_id": candidate_id,
            "status": status,
            "changed_files": changed_files,
            "file_count": len(changed_files),
            "multi_file": len(changed_files) > 1,
            "before_preview_files": before_paths,
            "after_preview_files": written_paths,
            "file_previews": file_previews,
            "blocked_reasons": blocked_reasons,
            "diff_sha256": _team_builder_diff_sha256(diff_text),
            "safety": {
                "scope": "scratch_only",
                "writes_real_files": False,
                "requires_final_apply_confirmation": True,
                "reason": "应用预览只写入 _scratch 副本，不修改真实 generated code。",
            },
        })
    preview_ready = sum(1 for item in preview_items if item.get("status") == "preview_ready")
    blocked = sum(1 for item in preview_items if item.get("status") == "blocked")
    files_previewed = sum(len(_list_value(item.get("file_previews"))) for item in preview_items)
    multi_file_preview_ready = sum(
        1 for item in preview_items
        if item.get("status") == "preview_ready" and bool(item.get("multi_file"))
    )
    if not preview_items:
        verdict = "clean"
        summary = "当前没有可执行候选；无需生成应用预览。"
    elif blocked:
        verdict = "blocked"
        summary = f"{blocked} 条候选无法生成安全应用预览。"
    else:
        verdict = "preview_ready"
        if multi_file_preview_ready:
            summary = f"{preview_ready} 条候选已在 scratch 中生成应用预览，其中 {multi_file_preview_ready} 条为多文件文件集预览；真实文件未修改。"
        else:
            summary = f"{preview_ready} 条候选已在 scratch 中生成应用预览；真实文件未修改。"
    gates = [
        _test_gate(
            "execution_ready_required",
            "执行就绪已放行",
            "pass" if not preview_items or blocked == 0 else "warning",
            "所有应用预览候选都来自 ready_for_explicit_apply。"
            if blocked == 0 else f"{blocked} 条候选尚未就绪或预览失败。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in preview_items
                if item.get("status") == "blocked"
            ][:5],
        ),
        _test_gate(
            "scratch_only",
            "只写 scratch 副本",
            "pass",
            "应用预览只写 _scratch before/after 文件，不写真实 generated code。",
            [f"files_written={files_written}", "real_writes=0"],
        ),
        _test_gate(
            "preview_files_created",
            "预览文件已生成",
            "pass" if not preview_items or preview_ready == len(preview_items) else "warning",
            f"{preview_ready}/{len(preview_items)} 条候选生成 before/after 预览。"
            if preview_items else "当前没有候选需要预览。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in preview_items
                if item.get("status") != "preview_ready"
            ][:5],
        ),
        _test_gate(
            "multi_file_scratch_preview",
            "多文件 scratch 预览可展开",
            "pass" if not any(bool(item.get("multi_file")) for item in preview_items) or multi_file_preview_ready > 0 else "warning",
            "多文件候选已按文件集生成 before/after 预览，真实应用仍不放开。"
            if multi_file_preview_ready else "当前没有可预览的多文件候选。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in preview_items
                if bool(item.get("multi_file"))
            ][:5],
        ),
    ]
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(readiness.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "items": len(preview_items),
            "preview_ready": preview_ready,
            "blocked": blocked,
            "files_written": files_written,
            "files_previewed": files_previewed,
            "multi_file_preview_ready": multi_file_preview_ready,
            "real_writes": 0,
        },
        "quality_gates": gates,
        "preview_items": preview_items,
        "source": {
            **(_dict_value(readiness.get("source"))),
            "repair_execution_readiness_endpoint": "/api/team-builder-materialization/repair-execution-readiness/latest",
            "repair_apply_preview_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_apply_preview.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        out_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_apply_preview.json"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _team_builder_apply_record_file_records(record: dict[str, Any]) -> list[dict[str, Any]]:
    file_records = [
        _dict_value(item)
        for item in _list_value(record.get("file_records"))
        if _dict_value(item)
    ]
    if file_records:
        return [
            {
                "changed_file": _safe_text(item.get("changed_file"), 320),
                "before_sha256": _safe_text(item.get("before_sha256"), 80),
                "after_sha256": _safe_text(item.get("after_sha256"), 80),
                "before_preview_file": _safe_text(item.get("before_preview_file"), 520),
                "after_preview_file": _safe_text(item.get("after_preview_file"), 520),
                "diff_sha256": _safe_text(item.get("diff_sha256"), 80),
                "real_writes": int(item.get("real_writes") or 1),
            }
            for item in file_records
            if _safe_text(item.get("changed_file"), 320)
        ]
    changed_file = _safe_text(record.get("changed_file"), 320)
    if not changed_file:
        return []
    return [{
        "changed_file": changed_file,
        "before_sha256": _safe_text(record.get("before_sha256"), 80),
        "after_sha256": _safe_text(record.get("after_sha256"), 80),
        "before_preview_file": _safe_text(record.get("before_preview_file"), 520),
        "after_preview_file": _safe_text(record.get("after_preview_file"), 520),
        "diff_sha256": _safe_text(record.get("diff_sha256"), 80),
        "real_writes": int(record.get("real_writes") or 1),
    }]


def _team_builder_rollback_record_file_records(record: dict[str, Any]) -> list[dict[str, Any]]:
    file_records = [
        _dict_value(item)
        for item in _list_value(record.get("file_records"))
        if _dict_value(item)
    ]
    if file_records:
        return [
            {
                "changed_file": _safe_text(item.get("changed_file"), 320),
                "rollback_from_sha256": _safe_text(item.get("rollback_from_sha256"), 80),
                "rollback_to_sha256": _safe_text(item.get("rollback_to_sha256"), 80),
                "before_preview_file": _safe_text(item.get("before_preview_file"), 520),
                "real_writes": int(item.get("real_writes") or 1),
            }
            for item in file_records
            if _safe_text(item.get("changed_file"), 320)
        ]
    changed_file = _safe_text(record.get("changed_file"), 320)
    if not changed_file:
        return []
    return [{
        "changed_file": changed_file,
        "rollback_from_sha256": _safe_text(record.get("rollback_from_sha256"), 80),
        "rollback_to_sha256": _safe_text(record.get("rollback_to_sha256"), 80),
        "before_preview_file": _safe_text(record.get("before_preview_file"), 520),
        "real_writes": int(record.get("real_writes") or 1),
    }]


def _team_builder_repair_apply_execution_records_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_apply_execution_records.json"


def _team_builder_read_repair_apply_execution_records(run_id: str) -> list[dict[str, Any]]:
    path = _team_builder_repair_apply_execution_records_path(run_id)
    if path is None or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = _list_value(payload.get("records")) if isinstance(payload, dict) else _list_value(payload)
    return [_dict_value(item) for item in records]


def _team_builder_write_repair_apply_execution_records(run_id: str, records: list[dict[str, Any]]) -> str:
    path = _team_builder_repair_apply_execution_records_path(run_id)
    if path is None:
        return ""
    payload = {
        "run_id": _safe_text(run_id, 160),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(_repo_root()))


def _team_builder_repair_apply_execution_report() -> dict[str, Any]:
    preview = _team_builder_repair_apply_preview_report()
    if not preview.get("available"):
        return {
            "available": False,
            "reason": _safe_text(preview.get("reason"), 500),
            "run_id": _safe_text(preview.get("run_id"), 160),
            "team_name": _safe_text(preview.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无应用预览，无法检查真实应用记录。",
            "counts": {
                "items": 0,
                "preview_ready": 0,
                "applied": 0,
                "blocked": 0,
                "stale_or_mismatch": 0,
                "real_writes": 0,
            },
            "quality_gates": [],
            "apply_items": [],
            "records": [],
            "source": preview.get("source") if isinstance(preview.get("source"), dict) else {},
        }
    run_id = _safe_text(preview.get("run_id"), 160)
    records = _team_builder_read_repair_apply_execution_records(run_id)
    records_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        candidate_id = _safe_text(record.get("candidate_id"), 160)
        if candidate_id:
            records_by_candidate.setdefault(candidate_id, []).append(record)

    apply_items: list[dict[str, Any]] = []
    for index, preview_item in enumerate([_dict_value(item) for item in _list_value(preview.get("preview_items"))]):
        candidate_id = _safe_text(preview_item.get("candidate_id"), 160)
        candidate_records = records_by_candidate.get(candidate_id, [])
        latest_record = candidate_records[-1] if candidate_records else {}
        changed_files = [
            _safe_text(path, 320)
            for path in _list_value(preview_item.get("changed_files"))
            if _safe_text(path, 320)
        ]
        file_records = _team_builder_apply_record_file_records(latest_record) if latest_record else []
        current_file_records: list[dict[str, Any]] = []
        if file_records:
            for file_record in file_records:
                changed_file = _safe_text(file_record.get("changed_file"), 320)
                target_path = _team_builder_repo_file_from_relpath(changed_file)
                current_file_records.append({
                    **file_record,
                    "current_sha256": _team_builder_file_sha256(target_path) if target_path is not None else "",
                })
        else:
            for changed_file in changed_files:
                target_path = _team_builder_repo_file_from_relpath(changed_file)
                current_file_records.append({
                    "changed_file": changed_file,
                    "current_sha256": _team_builder_file_sha256(target_path) if target_path is not None else "",
                })
        current_sha = _safe_text(current_file_records[0].get("current_sha256"), 80) if current_file_records else ""
        applied_after_sha = _safe_text(latest_record.get("after_sha256"), 80)
        record_diff_sha = _safe_text(latest_record.get("diff_sha256"), 80)
        preview_diff_sha = _safe_text(preview_item.get("diff_sha256"), 80)
        blocked_reasons = [
            _safe_text(reason, 420)
            for reason in _list_value(preview_item.get("blocked_reasons"))
            if _safe_text(reason, 420)
        ]
        if file_records:
            files_match_after = all(
                _safe_text(item.get("current_sha256"), 80)
                and _safe_text(item.get("current_sha256"), 80) == _safe_text(item.get("after_sha256"), 80)
                for item in current_file_records
            )
        else:
            files_match_after = bool(current_sha and applied_after_sha and current_sha == applied_after_sha)
        if latest_record and files_match_after and record_diff_sha == preview_diff_sha:
            status = "applied"
            summary = "当前目标文件内容与最近一次显式应用后的内容一致。"
        elif latest_record:
            status = "stale_or_mismatch"
            summary = "存在应用记录，但当前目标文件或 diff sha256 已不匹配，需要重新检查。"
        elif _safe_text(preview_item.get("status"), 80) == "preview_ready":
            status = "ready_for_explicit_apply"
            summary = "应用预览已生成，仍需要显式执行请求才会写真实文件。"
        else:
            status = "blocked"
            summary = "应用预览尚未通过，不能真实应用。"
        apply_items.append({
            "id": f"repair_apply_execution:{index}",
            "candidate_id": candidate_id,
            "status": status,
            "summary": summary,
            "changed_files": changed_files,
            "preview_status": _safe_text(preview_item.get("status"), 80),
            "diff_sha256": preview_diff_sha,
            "applied_at": _safe_text(latest_record.get("applied_at"), 120),
            "applied_by": _safe_text(latest_record.get("applied_by"), 120),
            "target_current_sha256": current_sha,
            "applied_after_sha256": applied_after_sha,
            "real_writes": int(latest_record.get("real_writes") or 0) if latest_record else 0,
            "file_set": len(changed_files) > 1,
            "file_count": len(changed_files),
            "file_records": current_file_records,
            "blocked_reasons": blocked_reasons,
        })

    preview_ready = sum(1 for item in apply_items if item.get("status") == "ready_for_explicit_apply")
    applied = sum(1 for item in apply_items if item.get("status") == "applied")
    blocked = sum(1 for item in apply_items if item.get("status") == "blocked")
    stale = sum(1 for item in apply_items if item.get("status") == "stale_or_mismatch")
    real_writes = sum(int(item.get("real_writes") or 0) for item in apply_items)
    file_set_ready = sum(1 for item in apply_items if item.get("status") == "ready_for_explicit_apply" and bool(item.get("file_set")))
    file_set_applied = sum(1 for item in apply_items if item.get("status") == "applied" and bool(item.get("file_set")))
    if not apply_items:
        verdict = "clean"
        summary = "当前没有可执行候选；真实应用未开启。"
    elif stale:
        verdict = "stale_or_mismatch"
        summary = f"{stale} 条真实应用记录与当前目标文件或 diff 不匹配。"
    elif blocked:
        verdict = "blocked"
        summary = f"{blocked} 条候选尚未通过应用预览，不能真实应用。"
    elif applied:
        verdict = "applied"
        summary = f"{applied} 条候选已显式应用到真实目标文件。"
    else:
        verdict = "ready_for_explicit_apply"
        summary = f"{preview_ready} 条候选已预览通过，等待显式应用请求。"
    gates = [
        _test_gate(
            "preview_required",
            "必须先通过应用预览",
            "pass" if not apply_items or blocked == 0 else "warning",
            "所有候选都已通过 preview 或已有应用记录。"
            if blocked == 0 else f"{blocked} 条候选仍被 preview 阻断。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in apply_items
                if item.get("status") == "blocked"
            ][:5],
        ),
        _test_gate(
            "explicit_execute_only",
            "只允许显式执行",
            "pass",
            "GET 报告接口不会写真实文件；只有 POST execute 且确认 token 齐全时才写入目标文件。",
            ["get_writes_files=false", "post_requires=confirm_real_file_write"],
        ),
        _test_gate(
            "apply_record_matches_current",
            "应用记录匹配当前文件",
            "pass" if stale == 0 else "fail",
            "没有发现已应用记录与当前目标文件不匹配。"
            if stale == 0 else f"{stale} 条应用记录已失效或不匹配。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in apply_items
                if item.get("status") == "stale_or_mismatch"
            ][:5],
        ),
    ]
    records_material = str(_team_builder_repair_apply_execution_records_path(run_id).relative_to(_repo_root())) if _team_builder_repair_apply_execution_records_path(run_id) else ""
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(preview.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "items": len(apply_items),
            "preview_ready": preview_ready,
            "applied": applied,
            "blocked": blocked,
            "stale_or_mismatch": stale,
            "real_writes": real_writes,
            "file_set_ready": file_set_ready,
            "file_set_applied": file_set_applied,
        },
        "quality_gates": gates,
        "apply_items": apply_items,
        "records": records,
        "source": {
            **(_dict_value(preview.get("source"))),
            "repair_apply_preview_endpoint": "/api/team-builder-materialization/repair-apply-preview/latest",
            "repair_apply_execution_records_material": records_material,
            "repair_apply_execution_report_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_apply_execution_report.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        out_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_apply_execution_report.json"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_execute_repair_apply(payload: dict[str, Any]) -> dict[str, Any]:
    preview = _team_builder_repair_apply_preview_report()
    if not preview.get("available"):
        raise HTTPException(status_code=409, detail="暂无可用于真实应用的预览报告。")
    candidate_id = _safe_text(payload.get("candidate_id"), 160)
    if not candidate_id:
        raise HTTPException(status_code=400, detail="缺少 candidate_id。")
    if payload.get("apply") is not True:
        raise HTTPException(status_code=400, detail="必须显式传入 apply=true。")
    applied_by = _safe_text(payload.get("applied_by"), 120)
    if not applied_by:
        raise HTTPException(status_code=400, detail="缺少 applied_by。")
    reason = _safe_text(payload.get("reason"), 520)
    if not reason:
        raise HTTPException(status_code=400, detail="缺少执行理由 reason。")
    confirmations = [
        _safe_text(item, 300)
        for item in _list_value(payload.get("confirmations"))
        if _safe_text(item, 300)
    ]
    if "confirm_real_file_write" not in confirmations:
        raise HTTPException(status_code=400, detail="缺少确认 token: confirm_real_file_write。")
    preview_items = [_dict_value(item) for item in _list_value(preview.get("preview_items"))]
    preview_item = next((item for item in preview_items if _safe_text(item.get("candidate_id"), 160) == candidate_id), None)
    if preview_item is None:
        raise HTTPException(status_code=404, detail="找不到对应 candidate 的应用预览。")
    if _safe_text(preview_item.get("status"), 80) != "preview_ready":
        raise HTTPException(status_code=409, detail="该候选尚未通过应用预览，不能真实应用。")
    diff_sha256 = _safe_text(preview_item.get("diff_sha256"), 80)
    expected_hash = _safe_text(payload.get("diff_sha256"), 80)
    if expected_hash and expected_hash != diff_sha256:
        raise HTTPException(status_code=409, detail="diff_sha256 与当前应用预览不一致。")
    if not expected_hash:
        raise HTTPException(status_code=400, detail="必须传入当前应用预览的 diff_sha256。")
    changed_files = [
        _safe_text(path, 320)
        for path in _list_value(preview_item.get("changed_files"))
        if _safe_text(path, 320)
    ]
    if not changed_files:
        raise HTTPException(status_code=409, detail="缺少真实应用目标文件。")
    if len(changed_files) > 1 and "confirm_file_set_write" not in confirmations:
        raise HTTPException(status_code=400, detail="多文件真实应用缺少确认 token: confirm_file_set_write。")
    if any(path.replace("\\", "/").startswith("tests/teams/") for path in changed_files):
        raise HTTPException(status_code=409, detail="不能把 contract 文件作为真实应用目标。")
    proposals = [_dict_value(item) for item in _list_value(_team_builder_repair_patch_diff_proposal_report().get("proposals"))]
    proposal = next((item for item in proposals if _safe_text(item.get("candidate_id"), 160) == candidate_id), None)
    if proposal is None or _safe_text(proposal.get("status"), 80) != "diff_ready":
        raise HTTPException(status_code=409, detail="当前 diff proposal 不可应用。")
    diff_text = _safe_text(proposal.get("diff"), 60000)
    if _team_builder_diff_sha256(diff_text) != diff_sha256:
        raise HTTPException(status_code=409, detail="当前 diff proposal 与预览 diff 不一致。")
    diff_blocks = _team_builder_split_unified_diff_by_file(diff_text)
    before_reports = {
        "contract_execution": _team_builder_latest_contract_execution_report(),
        "doctor_findings": _team_builder_latest_doctor_findings_report(),
        "repair_plan": _team_builder_latest_repair_plan(),
        "closure": _team_builder_latest_closure_status(),
    }

    file_previews = [
        _dict_value(item)
        for item in _list_value(preview_item.get("file_previews"))
        if _dict_value(item)
    ]
    if not file_previews:
        before_preview_files = [
            _safe_text(path, 520)
            for path in _list_value(preview_item.get("before_preview_files"))
            if _safe_text(path, 520)
        ]
        after_preview_files = [
            _safe_text(path, 520)
            for path in _list_value(preview_item.get("after_preview_files"))
            if _safe_text(path, 520)
        ]
        if len(before_preview_files) != len(changed_files) or len(after_preview_files) != len(changed_files):
            raise HTTPException(status_code=409, detail="before/after 预览文件数量与目标文件数量不一致。")
        file_previews = [
            {
                "changed_file": changed_file,
                "before_preview_file": before_preview_files[index],
                "after_preview_file": after_preview_files[index],
            }
            for index, changed_file in enumerate(changed_files)
        ]
    previews_by_path = {
        _team_builder_normalize_diff_file_path(_safe_text(item.get("changed_file"), 320)): item
        for item in file_previews
        if _safe_text(item.get("changed_file"), 320)
    }
    staged_files: list[dict[str, Any]] = []
    for rel_path in changed_files:
        normalized_rel = _team_builder_normalize_diff_file_path(rel_path)
        preview_record = previews_by_path.get(normalized_rel)
        if preview_record is None:
            raise HTTPException(status_code=409, detail=f"缺少 {rel_path} 的逐文件预览记录。")
        target_path = _team_builder_repo_file_from_relpath(rel_path)
        if target_path is None:
            raise HTTPException(status_code=409, detail=f"找不到真实应用目标文件: {rel_path}")
        before_preview_file = _safe_text(preview_record.get("before_preview_file"), 520)
        after_preview_file = _safe_text(preview_record.get("after_preview_file"), 520)
        preview_before_path = (_repo_root() / before_preview_file).resolve()
        preview_after_path = (_repo_root() / after_preview_file).resolve()
        try:
            preview_before_path.relative_to((_repo_root() / "_scratch" / "team_builder_repair_apply_preview").resolve())
            preview_after_path.relative_to((_repo_root() / "_scratch" / "team_builder_repair_apply_preview").resolve())
        except ValueError:
            raise HTTPException(status_code=409, detail="before/after 预览文件不在允许的 scratch 目录。")
        if not preview_before_path.is_file():
            raise HTTPException(status_code=409, detail=f"before 预览文件不存在: {before_preview_file}")
        if not preview_after_path.is_file():
            raise HTTPException(status_code=409, detail=f"after 预览文件不存在: {after_preview_file}")
        before_text = target_path.read_text(encoding="utf-8")
        preview_before_text = preview_before_path.read_text(encoding="utf-8")
        if before_text != preview_before_text:
            raise HTTPException(status_code=409, detail=f"{rel_path} 当前内容与 before 预览不一致，不能真实应用。")
        file_diff = diff_blocks.get(normalized_rel) or (diff_text if len(changed_files) == 1 else "")
        if not file_diff.strip():
            raise HTTPException(status_code=409, detail=f"找不到 {rel_path} 的 diff 块。")
        after_text = _team_builder_apply_unified_diff_to_text(before_text, file_diff)
        preview_after_text = preview_after_path.read_text(encoding="utf-8")
        if after_text != preview_after_text:
            raise HTTPException(status_code=409, detail=f"{rel_path} 重新应用 diff 的结果与 after 预览不一致。")
        if before_text == after_text:
            raise HTTPException(status_code=409, detail=f"{rel_path} diff 应用后没有内容变化。")
        staged_files.append({
            "changed_file": str(target_path.relative_to(_repo_root())).replace("\\", "/"),
            "target_path": target_path,
            "before_text": before_text,
            "after_text": after_text,
            "before_sha256": _team_builder_file_sha256(target_path),
            "after_sha256": "",
            "before_preview_file": before_preview_file,
            "after_preview_file": after_preview_file,
            "diff_sha256": _team_builder_diff_sha256(file_diff),
        })

    written: list[dict[str, Any]] = []
    try:
        for staged in staged_files:
            target_path = staged["target_path"]
            target_path.write_text(str(staged.get("after_text") or ""), encoding="utf-8")
            if target_path.read_text(encoding="utf-8") != str(staged.get("after_text") or ""):
                raise ValueError(f"{staged['changed_file']} 写入后内容校验失败。")
            staged["after_sha256"] = _team_builder_file_sha256(target_path)
            written.append(staged)
    except Exception as exc:
        for staged in written:
            try:
                staged["target_path"].write_text(str(staged.get("before_text") or ""), encoding="utf-8")
            except OSError:
                pass
        raise HTTPException(status_code=409, detail=f"文件集应用失败，已尝试恢复已写文件: {type(exc).__name__}: {exc}")

    run_id = _safe_text(preview.get("run_id"), 160)
    records = _team_builder_read_repair_apply_execution_records(run_id)
    file_records = [
        {
            "changed_file": _safe_text(staged.get("changed_file"), 320),
            "before_sha256": _safe_text(staged.get("before_sha256"), 80),
            "after_sha256": _safe_text(staged.get("after_sha256"), 80),
            "before_preview_file": _safe_text(staged.get("before_preview_file"), 520),
            "after_preview_file": _safe_text(staged.get("after_preview_file"), 520),
            "diff_sha256": _safe_text(staged.get("diff_sha256"), 80),
            "real_writes": 1,
        }
        for staged in staged_files
    ]
    first_file = file_records[0]
    record = {
        "id": f"repair_apply_execution:{candidate_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "run_id": run_id,
        "team_name": _safe_text(preview.get("team_name"), 160),
        "candidate_id": candidate_id,
        "applied": True,
        "applied_by": applied_by,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "diff_sha256": diff_sha256,
        "changed_file": _safe_text(first_file.get("changed_file"), 320),
        "changed_files": [_safe_text(item.get("changed_file"), 320) for item in file_records],
        "before_sha256": _safe_text(first_file.get("before_sha256"), 80),
        "after_sha256": _safe_text(first_file.get("after_sha256"), 80),
        "before_preview_file": _safe_text(first_file.get("before_preview_file"), 520),
        "after_preview_file": _safe_text(first_file.get("after_preview_file"), 520),
        "file_set": len(file_records) > 1,
        "file_count": len(file_records),
        "file_records": file_records,
        "before_reports": before_reports,
        "confirmations": confirmations,
        "real_writes": len(file_records),
    }
    records.append(record)
    _team_builder_write_repair_apply_execution_records(run_id, records)
    return _team_builder_repair_apply_execution_report()


def _team_builder_repair_post_apply_verification_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_post_apply_verification_result.json"


def _team_builder_repair_post_apply_verification_report() -> dict[str, Any]:
    apply_report = _team_builder_repair_apply_execution_report()
    if not apply_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(apply_report.get("reason"), 500),
            "run_id": _safe_text(apply_report.get("run_id"), 160),
            "team_name": _safe_text(apply_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无真实应用记录，无法检查应用后验证。",
            "counts": {"applied": 0, "verified": 0, "pending": 0, "failed": 0},
            "quality_gates": [],
            "verification_items": [],
            "source": apply_report.get("source") if isinstance(apply_report.get("source"), dict) else {},
        }
    run_id = _safe_text(apply_report.get("run_id"), 160)
    applied_items = [
        _dict_value(item)
        for item in _list_value(apply_report.get("apply_items"))
        if _safe_text(_dict_value(item).get("status"), 80) == "applied"
    ]
    path = _team_builder_repair_post_apply_verification_path(run_id)
    existing = _read_json_file(path) if path else {}
    applied_ids = {
        _safe_text(item.get("candidate_id"), 160)
        for item in applied_items
        if _safe_text(item.get("candidate_id"), 160)
    }
    verified_ids = {
        _safe_text(item.get("candidate_id"), 160)
        for item in _list_value(existing.get("applied_records"))
        if _safe_text(_dict_value(item).get("candidate_id"), 160)
    }
    if not applied_items:
        verdict = "clean"
        summary = "当前没有已应用补丁；无需执行应用后验证。"
        verification_items: list[dict[str, Any]] = []
        verified = 0
        pending = 0
        failed = 0
    elif existing.get("available") and applied_ids and applied_ids.issubset(verified_ids):
        return existing
    else:
        verification_items = [
            {
                "id": f"repair_post_apply_verification:{index}",
                "candidate_id": _safe_text(item.get("candidate_id"), 160),
                "status": "pending_verification",
                "summary": "补丁已真实应用，但尚未重新执行 contract/doctor/closure 验证。",
                "changed_files": [
                    _safe_text(path_item, 320)
                    for path_item in _list_value(item.get("changed_files"))
                    if _safe_text(path_item, 320)
                ],
                "required_commands": [
                    "POST /api/team-builder-materialization/repair-post-apply-verification/execute",
                    "POST /api/team-builder-materialization/contract-execution/execute",
                    "GET /api/team-builder-materialization/doctor-findings/latest",
                    "GET /api/team-builder-materialization/closure/latest",
                ],
            }
            for index, item in enumerate(applied_items)
        ]
        verdict = "awaiting_verification"
        summary = f"{len(applied_items)} 条已应用补丁等待应用后验证。"
        verified = 0
        pending = len(applied_items)
        failed = 0
    gates = [
        _test_gate(
            "applied_records_present",
            "存在真实应用记录",
            "pass" if not applied_items or applied_ids else "warning",
            f"{len(applied_items)} 条真实应用记录需要验证。"
            if applied_items else "当前没有已应用补丁。",
            list(applied_ids)[:5],
        ),
        _test_gate(
            "post_apply_verification_executed",
            "应用后验证已执行",
            "pass" if not applied_items else "warning",
            "当前没有已应用补丁需要验证。"
            if not applied_items else "存在已应用补丁但尚未执行验证。",
            list(applied_ids)[:5],
        ),
    ]
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(apply_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "applied": len(applied_items),
            "verified": verified,
            "pending": pending,
            "failed": failed,
        },
        "quality_gates": gates,
        "verification_items": verification_items,
        "source": {
            **(_dict_value(apply_report.get("source"))),
            "repair_apply_execution_endpoint": "/api/team-builder-materialization/repair-apply-execution/latest",
            "repair_post_apply_verification_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if run_id:
        out_path = _team_builder_repair_post_apply_verification_path(run_id)
        try:
            if out_path:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_execute_repair_post_apply_verification(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("verify") is not True:
        raise HTTPException(status_code=400, detail="必须显式传入 verify=true。")
    verified_by = _safe_text(payload.get("verified_by"), 120)
    if not verified_by:
        raise HTTPException(status_code=400, detail="缺少 verified_by。")
    reason = _safe_text(payload.get("reason"), 520)
    if not reason:
        raise HTTPException(status_code=400, detail="缺少验证理由 reason。")
    confirmations = [
        _safe_text(item, 300)
        for item in _list_value(payload.get("confirmations"))
        if _safe_text(item, 300)
    ]
    if "confirm_post_apply_verification" not in confirmations:
        raise HTTPException(status_code=400, detail="缺少确认 token: confirm_post_apply_verification。")
    apply_report = _team_builder_repair_apply_execution_report()
    if not apply_report.get("available"):
        raise HTTPException(status_code=409, detail="暂无真实应用记录，不能执行应用后验证。")
    applied_items = [
        _dict_value(item)
        for item in _list_value(apply_report.get("apply_items"))
        if _safe_text(_dict_value(item).get("status"), 80) == "applied"
    ]
    if not applied_items:
        return _team_builder_repair_post_apply_verification_report()
    contract_report = _team_builder_execute_contracts_report()
    test_report = _team_builder_test_report()
    doctor_report = _team_builder_latest_doctor_findings_report()
    repair_plan = _team_builder_latest_repair_plan()
    closure = _team_builder_latest_closure_status()
    contract_counts = _dict_value(contract_report.get("counts"))
    doctor_counts = _dict_value(doctor_report.get("counts"))
    repair_counts = _dict_value(repair_plan.get("counts"))
    failed_gates = []
    if _safe_text(contract_report.get("verdict"), 80) != "pass":
        failed_gates.append("contract")
    if int(doctor_counts.get("blocking") or 0) or _safe_text(doctor_report.get("verdict"), 80) == "fail":
        failed_gates.append("doctor")
    if _safe_text(repair_plan.get("verdict"), 80) != "clean":
        failed_gates.append("repair_plan")
    if _safe_text(closure.get("verdict"), 80) != "pass":
        failed_gates.append("closure")
    verdict = "pass" if not failed_gates else "fail"
    summary = (
        f"应用后验证通过：contract、doctor、repair plan 和 closure 均已回到健康状态。"
        if verdict == "pass"
        else f"应用后验证失败：{', '.join(failed_gates)} 未通过。"
    )
    run_id = _safe_text(apply_report.get("run_id"), 160)
    path = _team_builder_repair_post_apply_verification_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(apply_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "verified_by": verified_by,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "counts": {
            "applied": len(applied_items),
            "verified": len(applied_items) if verdict == "pass" else 0,
            "pending": 0,
            "failed": len(applied_items) if verdict != "pass" else 0,
            "contract_failed": int(contract_counts.get("failed_contracts") or 0),
            "doctor_findings": int(doctor_counts.get("total") or 0),
            "repair_required": int(repair_counts.get("repair_required") or 0),
        },
        "quality_gates": [
            _test_gate(
                "contract_rerun_passed",
                "contract 重新执行通过",
                "pass" if _safe_text(contract_report.get("verdict"), 80) == "pass" else "fail",
                _safe_text(contract_report.get("summary"), 520),
                [
                    _safe_text(item.get("path"), 320)
                    for item in _list_value(contract_report.get("contracts"))
                    if _safe_text(_dict_value(item).get("status"), 80) != "pass"
                ][:5],
            ),
            _test_gate(
                "doctor_clean_after_apply",
                "doctor 应用后清零",
                "pass" if int(doctor_counts.get("total") or 0) == 0 else "fail",
                _safe_text(doctor_report.get("summary"), 520),
                [
                    _safe_text(item.get("id") or item.get("check_id"), 220)
                    for item in _list_value(doctor_report.get("findings"))
                ][:5],
            ),
            _test_gate(
                "repair_plan_clean_after_apply",
                "repair plan 应用后清零",
                "pass" if _safe_text(repair_plan.get("verdict"), 80) == "clean" else "fail",
                _safe_text(repair_plan.get("summary"), 520),
                [],
            ),
            _test_gate(
                "closure_pass_after_apply",
                "closure 应用后通过",
                "pass" if _safe_text(closure.get("verdict"), 80) == "pass" else "fail",
                _safe_text(closure.get("summary"), 520),
                _list_value(closure.get("missing"))[:5],
            ),
        ],
        "applied_records": [
            {
                "candidate_id": _safe_text(item.get("candidate_id"), 160),
                "changed_files": [
                    _safe_text(path_item, 320)
                    for path_item in _list_value(item.get("changed_files"))
                    if _safe_text(path_item, 320)
                ],
                "diff_sha256": _safe_text(item.get("diff_sha256"), 80),
            }
            for item in applied_items
        ],
        "reports": {
            "contract_execution": contract_report,
            "test_report": test_report,
            "doctor_findings": doctor_report,
            "repair_plan": repair_plan,
            "closure": closure,
        },
        "source": {
            **(_dict_value(apply_report.get("source"))),
            "repair_apply_execution_endpoint": "/api/team-builder-materialization/repair-apply-execution/latest",
            "repair_post_apply_verification_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repair_outcome_reconciliation_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_outcome_reconciliation.json"


def _team_builder_finding_key(finding: dict[str, Any]) -> str:
    finding_id = _safe_text(finding.get("id"), 240)
    if finding_id:
        return finding_id
    parts = [
        _safe_text(finding.get("check_id"), 160),
        _safe_text(finding.get("worker_id") or finding.get("location"), 160),
        _safe_text(finding.get("observation"), 260),
    ]
    return "|".join(part for part in parts if part)


def _team_builder_findings_by_key(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    findings: dict[str, dict[str, Any]] = {}
    for raw in _list_value(report.get("findings")):
        finding = _dict_value(raw)
        key = _team_builder_finding_key(finding)
        if key:
            findings[key] = finding
    return findings


def _team_builder_repair_outcome_reconciliation_report() -> dict[str, Any]:
    apply_report = _team_builder_repair_apply_execution_report()
    verification = _team_builder_repair_post_apply_verification_report()
    if not apply_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(apply_report.get("reason"), 500),
            "run_id": _safe_text(apply_report.get("run_id"), 160),
            "team_name": _safe_text(apply_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无真实应用记录，无法做补丁前后对账。",
            "counts": {
                "applied": 0,
                "reconciled": 0,
                "missing_baseline": 0,
                "resolved_findings": 0,
                "introduced_findings": 0,
                "persistent_findings": 0,
                "pending_verification": 0,
            },
            "quality_gates": [],
            "reconciliation_items": [],
            "source": apply_report.get("source") if isinstance(apply_report.get("source"), dict) else {},
        }
    run_id = _safe_text(apply_report.get("run_id"), 160)
    records = [
        _dict_value(record)
        for record in _list_value(apply_report.get("records"))
        if bool(_dict_value(record).get("applied"))
    ]
    verification_reports = _dict_value(verification.get("reports"))
    after_doctor = _dict_value(verification_reports.get("doctor_findings"))
    after_repair = _dict_value(verification_reports.get("repair_plan"))
    after_closure = _dict_value(verification_reports.get("closure"))
    after_findings = _team_builder_findings_by_key(after_doctor)
    verification_ready = _safe_text(verification.get("verdict"), 80) in {"pass", "fail"}

    items: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        candidate_id = _safe_text(record.get("candidate_id"), 160)
        before_reports = _dict_value(record.get("before_reports"))
        before_doctor = _dict_value(before_reports.get("doctor_findings"))
        before_repair = _dict_value(before_reports.get("repair_plan"))
        before_closure = _dict_value(before_reports.get("closure"))
        before_findings = _team_builder_findings_by_key(before_doctor)
        resolved_keys = sorted(set(before_findings) - set(after_findings)) if verification_ready else []
        introduced_keys = sorted(set(after_findings) - set(before_findings)) if verification_ready else []
        persistent_keys = sorted(set(before_findings) & set(after_findings)) if verification_ready else []
        missing_baseline = not before_reports or not before_doctor.get("available")
        if not verification_ready:
            status = "pending_verification"
            summary = "真实应用已记录，但应用后验证还没有完成，不能做前后对账。"
        elif missing_baseline:
            status = "missing_baseline"
            summary = "真实应用记录缺少应用前 doctor/repair/closure 快照，只能看到应用后状态。"
        elif introduced_keys:
            status = "regression"
            summary = f"应用后新增 {len(introduced_keys)} 条 finding，需要回滚或继续诊断。"
        elif persistent_keys:
            status = "partial"
            summary = f"应用后仍保留 {len(persistent_keys)} 条原有 finding。"
        else:
            status = "reconciled"
            summary = f"应用前 {len(before_findings)} 条 finding 已清零，未发现新增 finding。"
        items.append({
            "id": f"repair_outcome_reconciliation:{index}",
            "candidate_id": candidate_id,
            "status": status,
            "summary": summary,
            "changed_file": _safe_text(record.get("changed_file"), 320),
            "changed_files": [
                _safe_text(file_record.get("changed_file"), 320)
                for file_record in _team_builder_apply_record_file_records(record)
                if _safe_text(file_record.get("changed_file"), 320)
            ],
            "file_set": bool(record.get("file_set")),
            "file_count": int(record.get("file_count") or len(_team_builder_apply_record_file_records(record)) or 0),
            "diff_sha256": _safe_text(record.get("diff_sha256"), 80),
            "before": {
                "doctor_verdict": _safe_text(before_doctor.get("verdict"), 80),
                "doctor_findings": len(before_findings),
                "repair_verdict": _safe_text(before_repair.get("verdict"), 80),
                "repair_required": int(_dict_value(before_repair.get("counts")).get("repair_required") or 0),
                "closure_verdict": _safe_text(before_closure.get("verdict"), 80),
            },
            "after": {
                "doctor_verdict": _safe_text(after_doctor.get("verdict"), 80),
                "doctor_findings": len(after_findings) if verification_ready else 0,
                "repair_verdict": _safe_text(after_repair.get("verdict"), 80),
                "repair_required": int(_dict_value(after_repair.get("counts")).get("repair_required") or 0),
                "closure_verdict": _safe_text(after_closure.get("verdict"), 80),
            },
            "resolved_findings": [
                {
                    "key": key,
                    "check_id": _safe_text(before_findings[key].get("check_id"), 160),
                    "observation": _safe_text(before_findings[key].get("observation"), 420),
                }
                for key in resolved_keys[:20]
            ],
            "introduced_findings": [
                {
                    "key": key,
                    "check_id": _safe_text(after_findings[key].get("check_id"), 160),
                    "observation": _safe_text(after_findings[key].get("observation"), 420),
                }
                for key in introduced_keys[:20]
            ],
            "persistent_findings": [
                {
                    "key": key,
                    "check_id": _safe_text(after_findings[key].get("check_id"), 160),
                    "observation": _safe_text(after_findings[key].get("observation"), 420),
                }
                for key in persistent_keys[:20]
            ],
        })

    missing_baseline = sum(1 for item in items if item.get("status") == "missing_baseline")
    pending = sum(1 for item in items if item.get("status") == "pending_verification")
    regressions = sum(1 for item in items if item.get("status") == "regression")
    partial = sum(1 for item in items if item.get("status") == "partial")
    reconciled = sum(1 for item in items if item.get("status") == "reconciled")
    resolved_total = sum(len(_list_value(item.get("resolved_findings"))) for item in items)
    introduced_total = sum(len(_list_value(item.get("introduced_findings"))) for item in items)
    persistent_total = sum(len(_list_value(item.get("persistent_findings"))) for item in items)
    if not records:
        verdict = "clean"
        summary = "当前没有已应用补丁；无需做补丁前后对账。"
    elif pending:
        verdict = "awaiting_verification"
        summary = f"{pending} 条已应用补丁等待应用后验证，暂不能对账。"
    elif regressions:
        verdict = "regression"
        summary = f"{regressions} 条补丁应用后新增 finding，需要回滚或继续修复。"
    elif partial:
        verdict = "partial"
        summary = f"{partial} 条补丁应用后仍保留原有 finding。"
    elif missing_baseline:
        verdict = "missing_baseline"
        summary = f"{missing_baseline} 条应用记录缺少应用前快照。"
    else:
        verdict = "pass"
        summary = f"{reconciled} 条补丁前后对账通过；已消除 {resolved_total} 条 finding，未新增 finding。"
    gates = [
        _test_gate(
            "baseline_available",
            "应用前快照存在",
            "pass" if not records or missing_baseline == 0 else "warning",
            "所有真实应用记录都有应用前 doctor/repair/closure 快照。"
            if missing_baseline == 0 else f"{missing_baseline} 条记录缺少应用前快照。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in items
                if item.get("status") == "missing_baseline"
            ][:5],
        ),
        _test_gate(
            "post_apply_verification_available",
            "应用后验证可用",
            "pass" if not records or pending == 0 else "warning",
            "应用后验证结果已可用于对账。"
            if pending == 0 else f"{pending} 条记录仍等待应用后验证。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in items
                if item.get("status") == "pending_verification"
            ][:5],
        ),
        _test_gate(
            "no_new_findings",
            "没有新增 finding",
            "pass" if introduced_total == 0 else "fail",
            "应用后没有新增 doctor finding。"
            if introduced_total == 0 else f"应用后新增 {introduced_total} 条 finding。",
            [
                _safe_text(finding.get("check_id"), 160)
                for item in items
                for finding in _list_value(item.get("introduced_findings"))
            ][:5],
        ),
        _test_gate(
            "original_findings_resolved",
            "原 finding 已消除",
            "pass" if not records or persistent_total == 0 else "warning",
            "没有原有 finding 在应用后残留。"
            if persistent_total == 0 else f"仍有 {persistent_total} 条原有 finding 残留。",
            [
                _safe_text(finding.get("check_id"), 160)
                for item in items
                for finding in _list_value(item.get("persistent_findings"))
            ][:5],
        ),
    ]
    path = _team_builder_repair_outcome_reconciliation_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(apply_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "applied": len(records),
            "reconciled": reconciled,
            "missing_baseline": missing_baseline,
            "resolved_findings": resolved_total,
            "introduced_findings": introduced_total,
            "persistent_findings": persistent_total,
            "pending_verification": pending,
        },
        "quality_gates": gates,
        "reconciliation_items": items,
        "source": {
            **(_dict_value(verification.get("source"))),
            "repair_apply_execution_endpoint": "/api/team-builder-materialization/repair-apply-execution/latest",
            "repair_post_apply_verification_endpoint": "/api/team-builder-materialization/repair-post-apply-verification/latest",
            "repair_outcome_reconciliation_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repair_rollback_readiness_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_rollback_readiness.json"


def _team_builder_before_preview_file_for_record(record: dict[str, Any]) -> str:
    before_preview_file = _safe_text(record.get("before_preview_file"), 520)
    if before_preview_file:
        return before_preview_file
    after_preview_file = _safe_text(record.get("after_preview_file"), 520).replace("\\", "/")
    marker = "/after/"
    if marker in after_preview_file:
        return after_preview_file.replace(marker, "/before/", 1)
    return ""


def _team_builder_repair_rollback_readiness_report() -> dict[str, Any]:
    apply_report = _team_builder_repair_apply_execution_report()
    if not apply_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(apply_report.get("reason"), 500),
            "run_id": _safe_text(apply_report.get("run_id"), 160),
            "team_name": _safe_text(apply_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无真实应用记录，无法检查回滚就绪。",
            "counts": {
                "applied": 0,
                "rollback_ready": 0,
                "blocked": 0,
                "stale_or_mismatch": 0,
                "missing_before_snapshot": 0,
                "real_writes": 0,
            },
            "quality_gates": [],
            "rollback_items": [],
            "source": apply_report.get("source") if isinstance(apply_report.get("source"), dict) else {},
        }
    run_id = _safe_text(apply_report.get("run_id"), 160)
    records = [
        _dict_value(record)
        for record in _list_value(apply_report.get("records"))
        if bool(_dict_value(record).get("applied"))
    ]
    rollback_items: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        candidate_id = _safe_text(record.get("candidate_id"), 160)
        file_records = _team_builder_apply_record_file_records(record)
        checked_files: list[dict[str, Any]] = []
        blocked_reasons: list[str] = []
        for file_record in file_records:
            changed_file = _safe_text(file_record.get("changed_file"), 320)
            normalized_changed_file = changed_file.replace("\\", "/")
            before_sha = _safe_text(file_record.get("before_sha256"), 80)
            after_sha = _safe_text(file_record.get("after_sha256"), 80)
            target_path = _team_builder_repo_file_from_relpath(changed_file)
            current_sha = _team_builder_file_sha256(target_path) if target_path is not None else ""
            before_preview_file = _safe_text(file_record.get("before_preview_file"), 520) or _team_builder_before_preview_file_for_record(file_record)
            before_snapshot_sha = ""
            before_snapshot_exists = False
            before_snapshot_safe = False
            if before_preview_file:
                before_snapshot_path = (_repo_root() / before_preview_file).resolve()
                try:
                    before_snapshot_path.relative_to((_repo_root() / "_scratch" / "team_builder_repair_apply_preview").resolve())
                    before_snapshot_safe = True
                except (OSError, ValueError):
                    before_snapshot_safe = False
                if before_snapshot_safe and before_snapshot_path.is_file():
                    before_snapshot_exists = True
                    before_snapshot_sha = _team_builder_file_sha256(before_snapshot_path)
            target_scope_safe = bool(target_path) and not normalized_changed_file.startswith("tests/teams/")
            current_matches_after = bool(current_sha and after_sha and current_sha == after_sha)
            before_snapshot_valid = bool(before_sha and before_snapshot_sha and before_sha == before_snapshot_sha)
            if not target_scope_safe:
                blocked_reasons.append(f"{normalized_changed_file}: 目标文件不存在或不在允许的 generated/source 范围内。")
            if normalized_changed_file.startswith("tests/teams/"):
                blocked_reasons.append(f"{normalized_changed_file}: contract 文件不允许作为自动回滚目标。")
            if not current_matches_after:
                blocked_reasons.append(f"{normalized_changed_file}: 当前文件 sha 与应用记录的 after sha 不一致。")
            if not before_snapshot_exists:
                blocked_reasons.append(f"{normalized_changed_file}: 缺少应用前 before 快照文件。")
            elif not before_snapshot_valid:
                blocked_reasons.append(f"{normalized_changed_file}: before 快照 sha 与应用记录不一致。")
            if not before_snapshot_safe:
                blocked_reasons.append(f"{normalized_changed_file}: before 快照不在允许的 scratch 预览目录。")
            checked_files.append({
                "changed_file": normalized_changed_file,
                "before_sha256": before_sha,
                "after_sha256": after_sha,
                "current_sha256": current_sha,
                "before_preview_file": before_preview_file,
                "before_snapshot_sha256": before_snapshot_sha,
                "target_scope_safe": target_scope_safe,
                "current_matches_after": current_matches_after,
                "before_snapshot_valid": before_snapshot_valid,
            })
        changed_file = _safe_text(checked_files[0].get("changed_file"), 320) if checked_files else _safe_text(record.get("changed_file"), 320)
        before_sha = _safe_text(checked_files[0].get("before_sha256"), 80) if checked_files else _safe_text(record.get("before_sha256"), 80)
        after_sha = _safe_text(checked_files[0].get("after_sha256"), 80) if checked_files else _safe_text(record.get("after_sha256"), 80)
        current_sha = _safe_text(checked_files[0].get("current_sha256"), 80) if checked_files else ""
        before_preview_file = _safe_text(checked_files[0].get("before_preview_file"), 520) if checked_files else ""
        before_snapshot_sha = _safe_text(checked_files[0].get("before_snapshot_sha256"), 80) if checked_files else ""
        target_scope_safe = bool(checked_files) and all(bool(item.get("target_scope_safe")) for item in checked_files)
        current_matches_after = bool(checked_files) and all(bool(item.get("current_matches_after")) for item in checked_files)
        before_snapshot_valid = bool(checked_files) and all(bool(item.get("before_snapshot_valid")) for item in checked_files)
        before_snapshot_exists = bool(checked_files) and all(_safe_text(item.get("before_snapshot_sha256"), 80) for item in checked_files)
        before_snapshot_safe = bool(checked_files) and all(_safe_text(item.get("before_preview_file"), 520) for item in checked_files)

        if current_matches_after and before_snapshot_valid and target_scope_safe and before_snapshot_safe:
            status = "ready_for_explicit_rollback"
            summary = "当前文件仍等于应用后的内容，before 快照可用；可进入显式回滚执行门。"
        elif not current_matches_after:
            status = "stale_or_mismatch"
            summary = "当前文件已经不等于应用后的内容，禁止自动回滚。"
        elif not before_snapshot_valid:
            status = "missing_before_snapshot"
            summary = "缺少可校验的应用前快照，禁止自动回滚。"
        else:
            status = "blocked"
            summary = "回滚目标未通过安全范围检查。"
        rollback_items.append({
            "id": f"repair_rollback_readiness:{index}",
            "candidate_id": candidate_id,
            "status": status,
            "summary": summary,
            "changed_file": changed_file,
            "changed_files": [_safe_text(item.get("changed_file"), 320) for item in checked_files],
            "file_set": len(checked_files) > 1,
            "file_count": len(checked_files),
            "file_records": checked_files,
            "diff_sha256": _safe_text(record.get("diff_sha256"), 80),
            "applied_at": _safe_text(record.get("applied_at"), 120),
            "applied_by": _safe_text(record.get("applied_by"), 120),
            "before_sha256": before_sha,
            "after_sha256": after_sha,
            "current_sha256": current_sha,
            "before_preview_file": before_preview_file,
            "before_snapshot_sha256": before_snapshot_sha,
            "target_scope_safe": target_scope_safe,
            "current_matches_after": current_matches_after,
            "before_snapshot_valid": before_snapshot_valid,
            "blocked_reasons": blocked_reasons,
        })

    ready = sum(1 for item in rollback_items if item.get("status") == "ready_for_explicit_rollback")
    stale = sum(1 for item in rollback_items if item.get("status") == "stale_or_mismatch")
    missing_before = sum(1 for item in rollback_items if item.get("status") == "missing_before_snapshot")
    blocked = sum(1 for item in rollback_items if item.get("status") == "blocked")
    real_writes = sum(int(record.get("real_writes") or 0) for record in records)
    if not records:
        verdict = "clean"
        summary = "当前没有已应用补丁；无需准备回滚。"
    elif stale:
        verdict = "stale_or_mismatch"
        summary = f"{stale} 条应用记录与当前文件不匹配，禁止自动回滚。"
    elif missing_before:
        verdict = "missing_before_snapshot"
        summary = f"{missing_before} 条应用记录缺少可校验 before 快照，禁止自动回滚。"
    elif blocked:
        verdict = "blocked"
        summary = f"{blocked} 条应用记录未通过回滚安全范围检查。"
    else:
        verdict = "ready_for_explicit_rollback"
        summary = f"{ready} 条应用记录已具备显式回滚前置条件；GET 不执行回滚。"
    gates = [
        _test_gate(
            "explicit_rollback_only",
            "只允许显式回滚",
            "pass",
            "GET 报告接口不会写真实文件；后续真实回滚必须走 POST execute 和确认 token。",
            ["get_writes_files=false", "post_requires=confirm_real_file_rollback"],
        ),
        _test_gate(
            "current_file_matches_applied_after",
            "当前文件等于应用后内容",
            "pass" if not records or stale == 0 else "fail",
            "所有已应用记录的当前文件 sha 都等于 after sha。"
            if stale == 0 else f"{stale} 条记录当前文件已变化。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in rollback_items
                if item.get("status") == "stale_or_mismatch"
            ][:5],
        ),
        _test_gate(
            "before_snapshot_available",
            "应用前快照可用",
            "pass" if not records or missing_before == 0 else "fail",
            "所有已应用记录都有可校验 before 快照。"
            if missing_before == 0 else f"{missing_before} 条记录缺少可校验 before 快照。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in rollback_items
                if item.get("status") == "missing_before_snapshot"
            ][:5],
        ),
        _test_gate(
            "rollback_target_scope_safe",
            "回滚目标范围安全",
            "pass" if not records or blocked == 0 else "fail",
            "没有发现越界或缺失目标文件。"
            if blocked == 0 else f"{blocked} 条记录目标范围不安全。",
            [
                _safe_text(item.get("changed_file"), 320)
                for item in rollback_items
                if item.get("status") == "blocked"
            ][:5],
        ),
    ]
    path = _team_builder_repair_rollback_readiness_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(apply_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "applied": len(records),
            "rollback_ready": ready,
            "blocked": blocked,
            "stale_or_mismatch": stale,
            "missing_before_snapshot": missing_before,
            "real_writes": real_writes,
        },
        "quality_gates": gates,
        "rollback_items": rollback_items,
        "source": {
            **(_dict_value(apply_report.get("source"))),
            "repair_apply_execution_endpoint": "/api/team-builder-materialization/repair-apply-execution/latest",
            "repair_rollback_readiness_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repair_rollback_execution_records_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_rollback_execution_records.json"


def _team_builder_read_repair_rollback_execution_records(run_id: str) -> list[dict[str, Any]]:
    path = _team_builder_repair_rollback_execution_records_path(run_id)
    if path is None or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = _list_value(payload.get("records")) if isinstance(payload, dict) else _list_value(payload)
    return [_dict_value(item) for item in records]


def _team_builder_write_repair_rollback_execution_records(run_id: str, records: list[dict[str, Any]]) -> str:
    path = _team_builder_repair_rollback_execution_records_path(run_id)
    if path is None:
        return ""
    payload = {
        "run_id": _safe_text(run_id, 160),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(_repo_root()))


def _team_builder_repair_rollback_execution_report() -> dict[str, Any]:
    readiness = _team_builder_repair_rollback_readiness_report()
    if not readiness.get("available"):
        return {
            "available": False,
            "reason": _safe_text(readiness.get("reason"), 500),
            "run_id": _safe_text(readiness.get("run_id"), 160),
            "team_name": _safe_text(readiness.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无回滚就绪报告，无法检查回滚执行记录。",
            "counts": {
                "items": 0,
                "ready": 0,
                "rolled_back": 0,
                "blocked": 0,
                "stale_or_mismatch": 0,
                "real_writes": 0,
            },
            "quality_gates": [],
            "rollback_items": [],
            "records": [],
            "source": readiness.get("source") if isinstance(readiness.get("source"), dict) else {},
        }
    run_id = _safe_text(readiness.get("run_id"), 160)
    records = _team_builder_read_repair_rollback_execution_records(run_id)
    records_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        candidate_id = _safe_text(record.get("candidate_id"), 160)
        if candidate_id:
            records_by_candidate.setdefault(candidate_id, []).append(record)
    rollback_items: list[dict[str, Any]] = []
    for index, item in enumerate([_dict_value(raw) for raw in _list_value(readiness.get("rollback_items"))]):
        candidate_id = _safe_text(item.get("candidate_id"), 160)
        candidate_records = records_by_candidate.get(candidate_id, [])
        latest_record = candidate_records[-1] if candidate_records else {}
        changed_file = _safe_text(item.get("changed_file"), 320)
        readiness_file_records = [
            _dict_value(file_record)
            for file_record in _list_value(item.get("file_records"))
            if _dict_value(file_record)
        ]
        rollback_file_records = _team_builder_rollback_record_file_records(latest_record) if latest_record else []
        current_file_records: list[dict[str, Any]] = []
        source_file_records = rollback_file_records or readiness_file_records
        for file_record in source_file_records:
            record_changed_file = _safe_text(file_record.get("changed_file"), 320)
            target_path = _team_builder_repo_file_from_relpath(record_changed_file)
            current_file_records.append({
                **file_record,
                "current_sha256": _team_builder_file_sha256(target_path) if target_path is not None else "",
            })
        target_path = _team_builder_repo_file_from_relpath(changed_file)
        current_sha = (
            _safe_text(current_file_records[0].get("current_sha256"), 80)
            if current_file_records else (_team_builder_file_sha256(target_path) if target_path is not None else "")
        )
        rollback_to_sha = _safe_text(latest_record.get("rollback_to_sha256"), 80)
        rollback_from_sha = _safe_text(latest_record.get("rollback_from_sha256"), 80)
        expected_after_sha = _safe_text(item.get("after_sha256"), 80)
        if rollback_file_records:
            files_match_rollback = all(
                _safe_text(file_record.get("current_sha256"), 80)
                and _safe_text(file_record.get("current_sha256"), 80) == _safe_text(file_record.get("rollback_to_sha256"), 80)
                for file_record in current_file_records
            )
        else:
            files_match_rollback = bool(current_sha and rollback_to_sha and current_sha == rollback_to_sha)
        if latest_record and files_match_rollback:
            status = "rolled_back"
            summary = "当前文件内容与最近一次显式回滚后的 before 快照一致。"
        elif latest_record:
            status = "stale_or_mismatch"
            summary = "存在回滚记录，但当前目标文件与回滚结果不一致，需要人工检查。"
        elif _safe_text(item.get("status"), 80) == "ready_for_explicit_rollback":
            status = "ready_for_explicit_rollback"
            summary = "回滚前置条件已满足，仍需要显式 POST execute 才会写真实文件。"
        else:
            status = "blocked"
            summary = _safe_text(item.get("summary"), 520) or "回滚就绪检查未通过，不能执行真实回滚。"
        rollback_items.append({
            "id": f"repair_rollback_execution:{index}",
            "candidate_id": candidate_id,
            "status": status,
            "summary": summary,
            "changed_file": changed_file,
            "changed_files": [
                _safe_text(file_record.get("changed_file"), 320)
                for file_record in (current_file_records or readiness_file_records)
                if _safe_text(file_record.get("changed_file"), 320)
            ],
            "file_set": bool(item.get("file_set")) or len(current_file_records or readiness_file_records) > 1,
            "file_count": len(current_file_records or readiness_file_records) or (1 if changed_file else 0),
            "file_records": current_file_records or readiness_file_records,
            "before_sha256": _safe_text(item.get("before_sha256"), 80),
            "after_sha256": expected_after_sha,
            "current_sha256": current_sha,
            "rolled_back_at": _safe_text(latest_record.get("rolled_back_at"), 120),
            "rolled_back_by": _safe_text(latest_record.get("rolled_back_by"), 120),
            "rollback_from_sha256": rollback_from_sha,
            "rollback_to_sha256": rollback_to_sha,
            "real_writes": int(latest_record.get("real_writes") or 0) if latest_record else 0,
            "blocked_reasons": [
                _safe_text(reason, 420)
                for reason in _list_value(item.get("blocked_reasons"))
                if _safe_text(reason, 420)
            ],
        })
    ready = sum(1 for item in rollback_items if item.get("status") == "ready_for_explicit_rollback")
    rolled_back = sum(1 for item in rollback_items if item.get("status") == "rolled_back")
    blocked = sum(1 for item in rollback_items if item.get("status") == "blocked")
    stale = sum(1 for item in rollback_items if item.get("status") == "stale_or_mismatch")
    real_writes = sum(int(item.get("real_writes") or 0) for item in rollback_items)
    file_set_ready = sum(1 for item in rollback_items if item.get("status") == "ready_for_explicit_rollback" and bool(item.get("file_set")))
    file_set_rolled_back = sum(1 for item in rollback_items if item.get("status") == "rolled_back" and bool(item.get("file_set")))
    if not rollback_items:
        verdict = "clean"
        summary = "当前没有已应用补丁；真实回滚未开启。"
    elif stale:
        verdict = "stale_or_mismatch"
        summary = f"{stale} 条回滚记录与当前目标文件不匹配。"
    elif rolled_back:
        verdict = "rolled_back"
        summary = f"{rolled_back} 条补丁已显式回滚到应用前内容。"
    elif blocked:
        verdict = "blocked"
        summary = f"{blocked} 条应用记录尚未通过回滚就绪检查，不能真实回滚。"
    else:
        verdict = "ready_for_explicit_rollback"
        summary = f"{ready} 条应用记录已具备显式回滚条件。"
    gates = [
        _test_gate(
            "rollback_readiness_required",
            "必须先通过回滚就绪检查",
            "pass" if not rollback_items or blocked == 0 else "warning",
            "所有回滚项都已通过就绪检查或已有回滚记录。"
            if blocked == 0 else f"{blocked} 条回滚项仍被就绪检查阻断。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in rollback_items
                if item.get("status") == "blocked"
            ][:5],
        ),
        _test_gate(
            "explicit_rollback_execute_only",
            "只允许显式执行回滚",
            "pass",
            "GET 报告接口不会写真实文件；只有 POST execute 且确认 token 齐全时才写入目标文件。",
            ["get_writes_files=false", "post_requires=confirm_real_file_rollback"],
        ),
        _test_gate(
            "rollback_record_matches_current",
            "回滚记录匹配当前文件",
            "pass" if stale == 0 else "fail",
            "没有发现回滚记录与当前目标文件不匹配。"
            if stale == 0 else f"{stale} 条回滚记录已失效或不匹配。",
            [
                _safe_text(item.get("candidate_id"), 160)
                for item in rollback_items
                if item.get("status") == "stale_or_mismatch"
            ][:5],
        ),
    ]
    records_material = str(_team_builder_repair_rollback_execution_records_path(run_id).relative_to(_repo_root())) if _team_builder_repair_rollback_execution_records_path(run_id) else ""
    report_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_rollback_execution_report.json" if run_id else None
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(readiness.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "items": len(rollback_items),
            "ready": ready,
            "rolled_back": rolled_back,
            "blocked": blocked,
            "stale_or_mismatch": stale,
            "real_writes": real_writes,
            "file_set_ready": file_set_ready,
            "file_set_rolled_back": file_set_rolled_back,
        },
        "quality_gates": gates,
        "rollback_items": rollback_items,
        "records": records,
        "source": {
            **(_dict_value(readiness.get("source"))),
            "repair_rollback_readiness_endpoint": "/api/team-builder-materialization/repair-rollback-readiness/latest",
            "repair_rollback_execution_records_material": records_material,
            "repair_rollback_execution_report_material": str(report_path.relative_to(_repo_root())) if report_path else "",
        },
    }
    if report_path:
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_execute_repair_rollback(payload: dict[str, Any]) -> dict[str, Any]:
    readiness = _team_builder_repair_rollback_readiness_report()
    if not readiness.get("available"):
        raise HTTPException(status_code=409, detail="暂无可用于真实回滚的就绪报告。")
    candidate_id = _safe_text(payload.get("candidate_id"), 160)
    if not candidate_id:
        raise HTTPException(status_code=400, detail="缺少 candidate_id。")
    if payload.get("rollback") is not True:
        raise HTTPException(status_code=400, detail="必须显式传入 rollback=true。")
    rolled_back_by = _safe_text(payload.get("rolled_back_by"), 120)
    if not rolled_back_by:
        raise HTTPException(status_code=400, detail="缺少 rolled_back_by。")
    reason = _safe_text(payload.get("reason"), 520)
    if not reason:
        raise HTTPException(status_code=400, detail="缺少回滚理由 reason。")
    confirmations = [
        _safe_text(item, 300)
        for item in _list_value(payload.get("confirmations"))
        if _safe_text(item, 300)
    ]
    if "confirm_real_file_rollback" not in confirmations:
        raise HTTPException(status_code=400, detail="缺少确认 token: confirm_real_file_rollback。")
    readiness_items = [_dict_value(item) for item in _list_value(readiness.get("rollback_items"))]
    item = next((raw for raw in readiness_items if _safe_text(raw.get("candidate_id"), 160) == candidate_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="找不到对应 candidate 的回滚就绪项。")
    if _safe_text(item.get("status"), 80) != "ready_for_explicit_rollback":
        raise HTTPException(status_code=409, detail="该候选尚未通过回滚就绪检查，不能真实回滚。")
    readiness_file_records = [
        _dict_value(file_record)
        for file_record in _list_value(item.get("file_records"))
        if _dict_value(file_record)
    ]
    if not readiness_file_records:
        readiness_file_records = [{
            "changed_file": _safe_text(item.get("changed_file"), 320),
            "before_sha256": _safe_text(item.get("before_sha256"), 80),
            "after_sha256": _safe_text(item.get("after_sha256"), 80),
            "current_sha256": _safe_text(item.get("current_sha256"), 80),
            "before_preview_file": _safe_text(item.get("before_preview_file"), 520),
        }]
    if len(readiness_file_records) > 1 and "confirm_file_set_rollback" not in confirmations:
        raise HTTPException(status_code=400, detail="多文件真实回滚缺少确认 token: confirm_file_set_rollback。")
    expected_after_sha = _safe_text(payload.get("after_sha256"), 80)
    current_after_sha = _safe_text(item.get("after_sha256"), 80)
    if not expected_after_sha:
        raise HTTPException(status_code=400, detail="必须传入当前应用记录的 after_sha256。")
    if expected_after_sha != current_after_sha:
        raise HTTPException(status_code=409, detail="after_sha256 与当前回滚就绪报告不一致。")
    expected_before_sha = _safe_text(payload.get("before_sha256"), 80)
    current_before_sha = _safe_text(item.get("before_sha256"), 80)
    if not expected_before_sha:
        raise HTTPException(status_code=400, detail="必须传入当前应用记录的 before_sha256。")
    if expected_before_sha != current_before_sha:
        raise HTTPException(status_code=409, detail="before_sha256 与当前回滚就绪报告不一致。")
    staged_files: list[dict[str, Any]] = []
    for file_record in readiness_file_records:
        changed_file = _safe_text(file_record.get("changed_file"), 320)
        if changed_file.replace("\\", "/").startswith("tests/teams/"):
            raise HTTPException(status_code=409, detail="不能把 contract 文件作为真实回滚目标。")
        target_path = _team_builder_repo_file_from_relpath(changed_file)
        if target_path is None:
            raise HTTPException(status_code=409, detail=f"找不到真实回滚目标文件: {changed_file}")
        file_after_sha = _safe_text(file_record.get("after_sha256"), 80)
        file_before_sha = _safe_text(file_record.get("before_sha256"), 80)
        current_sha = _team_builder_file_sha256(target_path)
        if current_sha != file_after_sha:
            raise HTTPException(status_code=409, detail=f"{changed_file} 当前目标文件已不等于应用后的 after_sha256，不能自动回滚。")
        before_preview_file = _safe_text(file_record.get("before_preview_file"), 520)
        before_preview_path = (_repo_root() / before_preview_file).resolve()
        try:
            before_preview_path.relative_to((_repo_root() / "_scratch" / "team_builder_repair_apply_preview").resolve())
        except ValueError:
            raise HTTPException(status_code=409, detail="before 快照不在允许的 scratch 目录。")
        if not before_preview_path.is_file():
            raise HTTPException(status_code=409, detail=f"before 快照文件不存在: {before_preview_file}")
        if _team_builder_file_sha256(before_preview_path) != file_before_sha:
            raise HTTPException(status_code=409, detail=f"{changed_file} before 快照 sha 与应用记录不一致。")
        staged_files.append({
            "changed_file": str(target_path.relative_to(_repo_root())).replace("\\", "/"),
            "target_path": target_path,
            "current_text": target_path.read_text(encoding="utf-8"),
            "before_text": before_preview_path.read_text(encoding="utf-8"),
            "rollback_from_sha256": file_after_sha,
            "rollback_to_sha256": file_before_sha,
            "before_preview_file": before_preview_file,
        })
    written: list[dict[str, Any]] = []
    try:
        for staged in staged_files:
            target_path = staged["target_path"]
            target_path.write_text(str(staged.get("before_text") or ""), encoding="utf-8")
            if _team_builder_file_sha256(target_path) != staged["rollback_to_sha256"]:
                raise ValueError(f"{staged['changed_file']} 回滚写入后 sha 校验失败。")
            written.append(staged)
    except Exception as exc:
        for staged in written:
            try:
                staged["target_path"].write_text(str(staged.get("current_text") or ""), encoding="utf-8")
            except OSError:
                pass
        raise HTTPException(status_code=409, detail=f"文件集回滚失败，已尝试恢复已写文件: {type(exc).__name__}: {exc}")
    run_id = _safe_text(readiness.get("run_id"), 160)
    records = _team_builder_read_repair_rollback_execution_records(run_id)
    file_records = [
        {
            "changed_file": _safe_text(staged.get("changed_file"), 320),
            "rollback_from_sha256": _safe_text(staged.get("rollback_from_sha256"), 80),
            "rollback_to_sha256": _safe_text(staged.get("rollback_to_sha256"), 80),
            "before_preview_file": _safe_text(staged.get("before_preview_file"), 520),
            "real_writes": 1,
        }
        for staged in staged_files
    ]
    first_file = file_records[0]
    record = {
        "id": f"repair_rollback_execution:{candidate_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "run_id": run_id,
        "team_name": _safe_text(readiness.get("team_name"), 160),
        "candidate_id": candidate_id,
        "rolled_back": True,
        "rolled_back_by": rolled_back_by,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "changed_file": _safe_text(first_file.get("changed_file"), 320),
        "changed_files": [_safe_text(file_record.get("changed_file"), 320) for file_record in file_records],
        "diff_sha256": _safe_text(item.get("diff_sha256"), 80),
        "rollback_from_sha256": _safe_text(first_file.get("rollback_from_sha256"), 80),
        "rollback_to_sha256": _safe_text(first_file.get("rollback_to_sha256"), 80),
        "before_preview_file": _safe_text(first_file.get("before_preview_file"), 520),
        "file_set": len(file_records) > 1,
        "file_count": len(file_records),
        "file_records": file_records,
        "confirmations": confirmations,
        "real_writes": len(file_records),
    }
    records.append(record)
    _team_builder_write_repair_rollback_execution_records(run_id, records)
    return _team_builder_repair_rollback_execution_report()


def _team_builder_repair_rollback_post_verification_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_rollback_post_verification_result.json"


def _team_builder_repair_rollback_post_verification_report() -> dict[str, Any]:
    rollback_report = _team_builder_repair_rollback_execution_report()
    if not rollback_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(rollback_report.get("reason"), 500),
            "run_id": _safe_text(rollback_report.get("run_id"), 160),
            "team_name": _safe_text(rollback_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无回滚执行记录，无法做回滚后验证。",
            "counts": {"rolled_back": 0, "verified": 0, "pending": 0, "failed": 0},
            "quality_gates": [],
            "verification_items": [],
            "source": rollback_report.get("source") if isinstance(rollback_report.get("source"), dict) else {},
        }
    run_id = _safe_text(rollback_report.get("run_id"), 160)
    rolled_items = [
        _dict_value(item)
        for item in _list_value(rollback_report.get("rollback_items"))
        if _safe_text(_dict_value(item).get("status"), 80) == "rolled_back"
    ]
    path = _team_builder_repair_rollback_post_verification_path(run_id)
    existing = _read_json_file(path) if path else {}
    rolled_ids = {
        _safe_text(item.get("candidate_id"), 160)
        for item in rolled_items
        if _safe_text(item.get("candidate_id"), 160)
    }
    verified_ids = {
        _safe_text(item.get("candidate_id"), 160)
        for item in _list_value(existing.get("rolled_back_records"))
        if _safe_text(_dict_value(item).get("candidate_id"), 160)
    }
    if not rolled_items:
        verdict = "clean"
        summary = "当前没有已回滚补丁；无需执行回滚后验证。"
        verification_items: list[dict[str, Any]] = []
        verified = 0
        pending = 0
        failed = 0
    elif existing.get("available") and rolled_ids and rolled_ids.issubset(verified_ids):
        return existing
    else:
        verification_items = [
            {
                "id": f"repair_rollback_post_verification:{index}",
                "candidate_id": _safe_text(item.get("candidate_id"), 160),
                "status": "pending_verification",
                "summary": "补丁已真实回滚，但尚未重新采集 contract/doctor/closure 状态。",
                "changed_files": [_safe_text(item.get("changed_file"), 320)] if _safe_text(item.get("changed_file"), 320) else [],
                "required_commands": [
                    "POST /api/team-builder-materialization/repair-rollback-post-verification/execute",
                    "POST /api/team-builder-materialization/contract-execution/execute",
                    "GET /api/team-builder-materialization/doctor-findings/latest",
                    "GET /api/team-builder-materialization/closure/latest",
                ],
            }
            for index, item in enumerate(rolled_items)
        ]
        verdict = "awaiting_verification"
        summary = f"{len(rolled_items)} 条已回滚补丁等待回滚后验证。"
        verified = 0
        pending = len(rolled_items)
        failed = 0
    gates = [
        _test_gate(
            "rolled_back_records_present",
            "存在真实回滚记录",
            "pass" if not rolled_items or rolled_ids else "warning",
            f"{len(rolled_items)} 条真实回滚记录需要验证。"
            if rolled_items else "当前没有已回滚补丁。",
            list(rolled_ids)[:5],
        ),
        _test_gate(
            "post_rollback_verification_executed",
            "回滚后验证已执行",
            "pass" if not rolled_items else "warning",
            "当前没有已回滚补丁需要验证。"
            if not rolled_items else "存在已回滚补丁但尚未执行验证。",
            list(rolled_ids)[:5],
        ),
    ]
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(rollback_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "rolled_back": len(rolled_items),
            "verified": verified,
            "pending": pending,
            "failed": failed,
        },
        "quality_gates": gates,
        "verification_items": verification_items,
        "source": {
            **(_dict_value(rollback_report.get("source"))),
            "repair_rollback_execution_endpoint": "/api/team-builder-materialization/repair-rollback-execution/latest",
            "repair_rollback_post_verification_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_execute_repair_rollback_post_verification(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("verify") is not True:
        raise HTTPException(status_code=400, detail="必须显式传入 verify=true。")
    verified_by = _safe_text(payload.get("verified_by"), 120)
    if not verified_by:
        raise HTTPException(status_code=400, detail="缺少 verified_by。")
    reason = _safe_text(payload.get("reason"), 520)
    if not reason:
        raise HTTPException(status_code=400, detail="缺少验证理由 reason。")
    confirmations = [
        _safe_text(item, 300)
        for item in _list_value(payload.get("confirmations"))
        if _safe_text(item, 300)
    ]
    if "confirm_post_rollback_verification" not in confirmations:
        raise HTTPException(status_code=400, detail="缺少确认 token: confirm_post_rollback_verification。")
    rollback_report = _team_builder_repair_rollback_execution_report()
    if not rollback_report.get("available"):
        raise HTTPException(status_code=409, detail="暂无真实回滚记录，不能执行回滚后验证。")
    rolled_items = [
        _dict_value(item)
        for item in _list_value(rollback_report.get("rollback_items"))
        if _safe_text(_dict_value(item).get("status"), 80) == "rolled_back"
    ]
    if not rolled_items:
        return _team_builder_repair_rollback_post_verification_report()
    stale_items = [
        item for item in rolled_items
        if _safe_text(item.get("current_sha256"), 80) != _safe_text(item.get("rollback_to_sha256"), 80)
    ]
    contract_report = _team_builder_execute_contracts_report()
    test_report = _team_builder_test_report()
    doctor_report = _team_builder_latest_doctor_findings_report()
    repair_plan = _team_builder_latest_repair_plan()
    closure = _team_builder_latest_closure_status()
    doctor_counts = _dict_value(doctor_report.get("counts"))
    repair_counts = _dict_value(repair_plan.get("counts"))
    verdict = "pass" if not stale_items else "fail"
    summary = (
        "回滚后验证通过：目标文件仍等于 before 快照；回滚后的 contract/doctor/repair/closure 状态已重新采集。"
        if verdict == "pass"
        else f"回滚后验证失败：{len(stale_items)} 个目标文件已不等于回滚后的 before sha。"
    )
    run_id = _safe_text(rollback_report.get("run_id"), 160)
    path = _team_builder_repair_rollback_post_verification_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(rollback_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "verified_by": verified_by,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "counts": {
            "rolled_back": len(rolled_items),
            "verified": len(rolled_items) if verdict == "pass" else 0,
            "pending": 0,
            "failed": len(rolled_items) if verdict != "pass" else 0,
            "doctor_findings": int(doctor_counts.get("total") or 0),
            "repair_required": int(repair_counts.get("repair_required") or 0),
        },
        "quality_gates": [
            _test_gate(
                "rollback_file_state_restored",
                "目标文件已回到 before sha",
                "pass" if not stale_items else "fail",
                "所有回滚记录的当前文件 sha 都等于 rollback_to_sha。"
                if not stale_items else f"{len(stale_items)} 个目标文件不匹配。",
                [_safe_text(item.get("candidate_id"), 160) for item in stale_items][:5],
            ),
            _test_gate(
                "post_rollback_diagnostics_collected",
                "回滚后诊断已采集",
                "pass" if all(_dict_value(report_item).get("available") for report_item in [contract_report, test_report, doctor_report, repair_plan, closure]) else "warning",
                "已重新采集 contract/test/doctor/repair/closure。这个门不要求回滚后业务健康，只要求状态可见。",
                [],
            ),
            _test_gate(
                "post_rollback_findings_visible",
                "回滚后 finding 可见",
                "pass" if int(doctor_counts.get("total") or 0) == 0 else "warning",
                _safe_text(doctor_report.get("summary"), 520),
                [
                    _safe_text(item.get("id") or item.get("check_id"), 220)
                    for item in _list_value(doctor_report.get("findings"))
                ][:5],
            ),
            _test_gate(
                "post_rollback_repair_status_visible",
                "回滚后 repair 状态可见",
                "pass" if _safe_text(repair_plan.get("verdict"), 80) == "clean" else "warning",
                _safe_text(repair_plan.get("summary"), 520),
                [],
            ),
        ],
        "rolled_back_records": [
            {
                "candidate_id": _safe_text(item.get("candidate_id"), 160),
                "changed_files": [_safe_text(item.get("changed_file"), 320)] if _safe_text(item.get("changed_file"), 320) else [],
                "rollback_from_sha256": _safe_text(item.get("rollback_from_sha256"), 80),
                "rollback_to_sha256": _safe_text(item.get("rollback_to_sha256"), 80),
            }
            for item in rolled_items
        ],
        "reports": {
            "contract_execution": contract_report,
            "test_report": test_report,
            "doctor_findings": doctor_report,
            "repair_plan": repair_plan,
            "closure": closure,
        },
        "source": {
            **(_dict_value(rollback_report.get("source"))),
            "repair_rollback_execution_endpoint": "/api/team-builder-materialization/repair-rollback-execution/latest",
            "repair_rollback_post_verification_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repair_closure_rollup_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_closure_rollup.json"


def _team_builder_repair_closure_rollup_report() -> dict[str, Any]:
    def int_count(report: dict[str, Any], key: str) -> int:
        value = _dict_value(report.get("counts")).get(key)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def stage(
        stage_id: str,
        name: str,
        status: str,
        summary: str,
        endpoint: str,
        counts: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": stage_id,
            "name": name,
            "status": status,
            "summary": _safe_text(summary, 800),
            "endpoint": endpoint,
            "counts": counts or {},
        }

    repair_plan = _team_builder_latest_repair_plan()
    patch_candidates = _team_builder_repair_patch_candidates_report()
    apply_gate = _team_builder_repair_apply_gate_report()
    diff_proposal = _team_builder_repair_patch_diff_proposal_report()
    approval = _team_builder_repair_approval_report()
    execution_readiness = _team_builder_repair_execution_readiness_report()
    apply_preview = _team_builder_repair_apply_preview_report()
    apply_execution = _team_builder_repair_apply_execution_report()
    post_apply = _team_builder_repair_post_apply_verification_report()
    outcome = _team_builder_repair_outcome_reconciliation_report()
    rollback_readiness = _team_builder_repair_rollback_readiness_report()
    rollback_execution = _team_builder_repair_rollback_execution_report()
    rollback_post = _team_builder_repair_rollback_post_verification_report()
    safety_policy = _team_builder_repair_safety_policy()

    run_id = (
        _safe_text(repair_plan.get("run_id"), 160)
        or _safe_text(patch_candidates.get("run_id"), 160)
        or _safe_text(apply_execution.get("run_id"), 160)
        or _safe_text(rollback_execution.get("run_id"), 160)
        or _safe_text(rollback_post.get("run_id"), 160)
    )
    team_name = (
        _safe_text(repair_plan.get("team_name"), 160)
        or _safe_text(patch_candidates.get("team_name"), 160)
        or _safe_text(apply_execution.get("team_name"), 160)
        or _safe_text(rollback_post.get("team_name"), 160)
    )

    repair_required = int_count(repair_plan, "repair_required")
    validation_gap = int_count(repair_plan, "validation_gap")
    candidates = int_count(patch_candidates, "candidates")
    located_sources = int_count(patch_candidates, "source_located")
    review_items = int_count(apply_gate, "review_items")
    diff_ready = int_count(diff_proposal, "diff_ready")
    proposals = int_count(approval, "proposals")
    approved = int_count(approval, "approved")
    execution_ready = int_count(execution_readiness, "execution_ready")
    execution_blocked = int_count(execution_readiness, "blocked")
    preview_ready = int_count(apply_preview, "preview_ready")
    apply_preview_blocked = int_count(apply_preview, "blocked")
    applied = int_count(apply_execution, "applied")
    apply_blocked = int_count(apply_execution, "blocked")
    apply_real_writes = int_count(apply_execution, "real_writes")
    post_apply_pending = int_count(post_apply, "pending")
    post_apply_failed = int_count(post_apply, "failed")
    reconciled = int_count(outcome, "reconciled")
    missing_baseline = int_count(outcome, "missing_baseline")
    introduced_findings = int_count(outcome, "introduced_findings")
    persistent_findings = int_count(outcome, "persistent_findings")
    pending_reconciliation = int_count(outcome, "pending_verification")
    rollback_ready = int_count(rollback_readiness, "rollback_ready")
    rollback_stale = int_count(rollback_readiness, "stale_or_mismatch") + int_count(rollback_execution, "stale_or_mismatch")
    rollback_blocked = int_count(rollback_readiness, "blocked") + int_count(rollback_execution, "blocked")
    rolled_back = int_count(rollback_execution, "rolled_back")
    rollback_real_writes = int_count(rollback_execution, "real_writes")
    rollback_post_pending = int_count(rollback_post, "pending")
    rollback_post_failed = int_count(rollback_post, "failed")
    candidate_items = [_dict_value(item) for item in _list_value(patch_candidates.get("candidates"))]
    changed_files_by_candidate = [
        [
            _safe_text(path, 320)
            for path in _list_value(_dict_value(candidate.get("proposed_patch")).get("changed_files"))
            if _safe_text(path, 320)
        ]
        for candidate in candidate_items
    ]
    multi_file_candidate_count = sum(1 for paths in changed_files_by_candidate if len(paths) > 1)
    multi_candidate_count = len(candidate_items)
    generalization_blockers: list[str] = []
    if multi_candidate_count > 1:
        generalization_blockers.append("候选数量超过 1；需要排序、批量审阅和逐候选验证结果对账。")
    if multi_file_candidate_count:
        generalization_blockers.append("存在多文件候选；必须通过文件集预览、文件集确认 token、逐文件记录和回滚前置检查，不能自动批量写入。")
    if any(path.replace("\\", "/").startswith("tests/teams/") for paths in changed_files_by_candidate for path in paths):
        generalization_blockers.append("候选目标包含 contract 路径；contract 只能作为验收定义，不能作为修复写入目标。")
    generalization_summary = (
        "当前没有候选补丁；下一阶段应构造多文件、多候选样本来验证排序、审阅、预览、应用、回滚和对账。"
        if not candidate_items else
        f"当前候选 {multi_candidate_count} 个，其中多文件候选 {multi_file_candidate_count} 个；必须先显式展示泛化风险，再考虑真实写入扩展。"
    )

    plan_status = "clean"
    if repair_required:
        plan_status = "repair_required"
    elif validation_gap:
        plan_status = "validation_gap"

    candidate_status = "no_candidate"
    if candidates and review_items:
        candidate_status = "review_required"
    elif candidates:
        candidate_status = "candidate_ready"

    approval_status = "no_patch"
    if proposals and approved:
        approval_status = "approved"
    elif diff_ready or proposals:
        approval_status = "approval_required"

    execution_status = "not_started"
    if execution_blocked or apply_preview_blocked or apply_blocked:
        execution_status = "blocked"
    elif execution_ready or preview_ready:
        execution_status = "ready"
    elif applied:
        execution_status = "applied"

    apply_status = "not_started"
    if post_apply_failed or introduced_findings or persistent_findings:
        apply_status = "regression_or_persistent"
    elif post_apply_pending or pending_reconciliation or missing_baseline:
        apply_status = "pending_verification"
    elif applied:
        apply_status = "verified"

    rollback_status = "not_needed"
    if rollback_stale or rollback_post_failed:
        rollback_status = "stale_or_failed"
    elif rollback_post_pending:
        rollback_status = "pending_verification"
    elif rolled_back:
        rollback_status = "verified"
    elif rollback_ready:
        rollback_status = "ready_for_explicit_rollback"
    elif rollback_blocked:
        rollback_status = "blocked"

    stages = [
        stage(
            "diagnosis_to_plan",
            "诊断到修复计划",
            plan_status,
            _safe_text(repair_plan.get("summary"), 800),
            "/api/team-builder-materialization/repair-plan/latest",
            {"repair_required": repair_required, "validation_gap": validation_gap},
        ),
        stage(
            "candidate_review",
            "候选补丁审阅",
            candidate_status,
            _safe_text(patch_candidates.get("summary"), 800),
            "/api/team-builder-materialization/repair-patch-candidates/latest",
            {"candidates": candidates, "located_sources": located_sources, "review_items": review_items},
        ),
        stage(
            "diff_and_approval",
            "diff 与显式批准",
            approval_status,
            _safe_text(approval.get("summary") or diff_proposal.get("summary"), 800),
            "/api/team-builder-materialization/repair-approval/latest",
            {"diff_ready": diff_ready, "proposals": proposals, "approved": approved},
        ),
        stage(
            "execution_preflight",
            "执行前置与预览",
            execution_status,
            _safe_text(execution_readiness.get("summary") or apply_preview.get("summary"), 800),
            "/api/team-builder-materialization/repair-execution-readiness/latest",
            {"execution_ready": execution_ready, "preview_ready": preview_ready, "blocked": execution_blocked + apply_preview_blocked + apply_blocked},
        ),
        stage(
            "apply_and_verify",
            "应用后验证与对账",
            apply_status,
            _safe_text(post_apply.get("summary") or outcome.get("summary") or apply_execution.get("summary"), 800),
            "/api/team-builder-materialization/repair-outcome-reconciliation/latest",
            {
                "applied": applied,
                "real_writes": apply_real_writes,
                "pending": post_apply_pending + pending_reconciliation,
                "failed": post_apply_failed,
                "reconciled": reconciled,
                "introduced_findings": introduced_findings,
                "persistent_findings": persistent_findings,
            },
        ),
        stage(
            "rollback_and_verify",
            "回滚与回滚后验证",
            rollback_status,
            _safe_text(rollback_post.get("summary") or rollback_execution.get("summary"), 800),
            "/api/team-builder-materialization/repair-rollback-post-verification/latest",
            {
                "rollback_ready": rollback_ready,
                "rolled_back": rolled_back,
                "real_writes": rollback_real_writes,
                "pending": rollback_post_pending,
                "failed": rollback_post_failed,
                "blocked": rollback_blocked,
            },
        ),
    ]

    next_actions: list[dict[str, Any]] = []
    if repair_required or validation_gap:
        next_actions.append({
            "id": "inspect_repair_plan",
            "title": "先看诊断和修复计划",
            "summary": "当前仍有需要修复或验证的 finding，先确认它们是代码问题、验证缺口还是只读观察。",
            "endpoint": "/api/team-builder-materialization/repair-plan/latest",
        })
    elif candidates and review_items:
        next_actions.append({
            "id": "review_patch_candidates",
            "title": "审阅候选补丁和应用门",
            "summary": "已有候选补丁，但仍需要人工确认目标文件、验证命令和安全边界。",
            "endpoint": "/api/team-builder-materialization/repair-apply-gate/latest",
        })
    elif diff_ready and not approved:
        next_actions.append({
            "id": "approve_patch_diff",
            "title": "显式批准 diff",
            "summary": "diff 已生成，但尚未形成显式批准记录。",
            "endpoint": "/api/team-builder-materialization/repair-approval/latest",
        })
    elif preview_ready and not applied:
        next_actions.append({
            "id": "explicit_apply",
            "title": "按需显式应用补丁",
            "summary": "scratch 预览已准备；真实写入仍必须走显式 POST execute 和确认 token。",
            "endpoint": "/api/team-builder-materialization/repair-apply-execution/latest",
        })
    elif applied and (post_apply_pending or pending_reconciliation or missing_baseline):
        next_actions.append({
            "id": "verify_applied_patch",
            "title": "执行应用后验证和前后对账",
            "summary": "已有真实应用记录，但验证或 finding 级对账还没有完全闭合。",
            "endpoint": "/api/team-builder-materialization/repair-post-apply-verification/latest",
        })
    elif introduced_findings or persistent_findings or post_apply_failed:
        next_actions.append({
            "id": "inspect_reconciliation",
            "title": "检查应用后回归或残留 finding",
            "summary": "补丁应用后仍有新增、残留或验证失败，需要回到诊断和候选补丁审阅。",
            "endpoint": "/api/team-builder-materialization/repair-outcome-reconciliation/latest",
        })
    elif rollback_ready and not rolled_back:
        next_actions.append({
            "id": "explicit_rollback_if_needed",
            "title": "按需显式回滚",
            "summary": "存在已应用补丁且具备回滚前置条件；是否回滚仍需要人工决策和显式 POST。",
            "endpoint": "/api/team-builder-materialization/repair-rollback-execution/latest",
        })
    elif rolled_back and rollback_post_pending:
        next_actions.append({
            "id": "verify_rollback",
            "title": "执行回滚后验证",
            "summary": "已有真实回滚记录，但还没重新采集回滚后的诊断状态。",
            "endpoint": "/api/team-builder-materialization/repair-rollback-post-verification/latest",
        })
    else:
        next_actions.append({
            "id": "scan_real_run_repair_candidates",
            "title": "扫描真实 TeamBuilder 失败 run 候选",
            "summary": "文件集应用、验证和回滚样本已经具备；下一步从已有真实 run 里分清失败、验证缺口和可修复候选，避免继续停留在 scratch 演示。",
            "endpoint": "/api/team-builder-materialization/repair-real-run-candidate-scan/latest",
        })

    fail_statuses = {"blocked", "regression_or_persistent", "stale_or_failed"}
    pending_statuses = {
        "repair_required",
        "validation_gap",
        "review_required",
        "candidate_ready",
        "approval_required",
        "ready",
        "pending_verification",
        "ready_for_explicit_rollback",
    }
    failed_stages = [item for item in stages if _safe_text(item.get("status"), 80) in fail_statuses]
    pending_stages = [item for item in stages if _safe_text(item.get("status"), 80) in pending_statuses]
    if failed_stages:
        verdict = "blocked"
        summary = f"修复闭环有 {len(failed_stages)} 个阻断或回归阶段；先看总览里的失败阶段和对应入口。"
    elif pending_stages:
        verdict = "action_required"
        summary = f"修复闭环有 {len(pending_stages)} 个阶段需要人工审阅、显式执行或补验证。"
    else:
        verdict = "clean"
        summary = "当前修复闭环没有待处理补丁、待验证应用或待验证回滚；可以进入多文件、多候选和真实 worker 泛化。"

    path = _team_builder_repair_closure_rollup_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": team_name,
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "stages": len(stages),
            "pending_stages": len(pending_stages),
            "failed_stages": len(failed_stages),
            "repair_required": repair_required,
            "validation_gap": validation_gap,
            "candidates": candidates,
            "review_items": review_items,
            "diff_ready": diff_ready,
            "approved": approved,
            "execution_ready": execution_ready,
            "preview_ready": preview_ready,
            "applied": applied,
            "apply_real_writes": apply_real_writes,
            "post_apply_pending": post_apply_pending,
            "post_apply_failed": post_apply_failed,
            "reconciled": reconciled,
            "rollback_ready": rollback_ready,
            "rolled_back": rolled_back,
            "rollback_real_writes": rollback_real_writes,
            "rollback_post_pending": rollback_post_pending,
            "rollback_post_failed": rollback_post_failed,
            "multi_candidate_count": multi_candidate_count,
            "multi_file_candidate_count": multi_file_candidate_count,
        },
        "quality_gates": [
            _test_gate(
                "repair_subreports_available",
                "修复子报告可读取",
                "pass" if all(_dict_value(report_item).get("available") for report_item in [
                    repair_plan,
                    patch_candidates,
                    apply_gate,
                    diff_proposal,
                    approval,
                    execution_readiness,
                    apply_preview,
                    apply_execution,
                    post_apply,
                    outcome,
                    rollback_readiness,
                    rollback_execution,
                    rollback_post,
                ]) else "warning",
                "总览已读取候选、应用、验证、对账、回滚和回滚后验证报告。",
                [],
            ),
            _test_gate(
                "real_writes_are_explicit",
                "真实写入只来自显式执行",
                "pass",
                "总览只读；真实应用和真实回滚仍分别由 explicit apply/rollback POST 记录证明。",
                [
                    f"apply_real_writes={apply_real_writes}",
                    f"rollback_real_writes={rollback_real_writes}",
                ],
            ),
            _test_gate(
                "post_apply_closed",
                "应用后验证闭合",
                "pass" if not applied or (post_apply_pending == 0 and post_apply_failed == 0 and missing_baseline == 0) else "warning",
                "没有已应用补丁，或已应用补丁已完成应用后验证和 finding 对账。"
                if not applied or (post_apply_pending == 0 and post_apply_failed == 0 and missing_baseline == 0)
                else "存在已应用补丁尚未完全验证或缺少应用前基线。",
                [],
            ),
            _test_gate(
                "rollback_closed",
                "回滚后验证闭合",
                "pass" if not rolled_back or (rollback_post_pending == 0 and rollback_post_failed == 0) else "warning",
                "没有已回滚补丁，或已回滚补丁已完成回滚后验证。"
                if not rolled_back or (rollback_post_pending == 0 and rollback_post_failed == 0)
                else "存在已回滚补丁尚未完成回滚后验证。",
                [],
            ),
            _test_gate(
                "multi_file_multi_candidate_risk_visible",
                "多文件多候选风险可见",
                "pass" if not generalization_blockers else "warning",
                "当前没有多候选或多文件风险。"
                if not generalization_blockers else "总览已显式暴露多候选、多文件或 contract 目标风险。",
                generalization_blockers,
            ),
        ],
        "stages": stages,
        "generalization": {
            "summary": generalization_summary,
            "candidate_count": multi_candidate_count,
            "multi_file_candidate_count": multi_file_candidate_count,
            "single_file_execution_limit": False,
            "blockers": generalization_blockers,
            "next_validation": "构造真实 generated worker 的多文件、多候选候选集，验证审阅排序、scratch 文件集预览、显式文件集应用、回滚和前后对账。",
        },
        "next_actions": next_actions,
        "source": {
            **(_dict_value(safety_policy.get("source"))),
            "repair_closure_rollup_material": str(path.relative_to(_repo_root())) if path else "",
            "repair_plan_endpoint": "/api/team-builder-materialization/repair-plan/latest",
            "repair_patch_candidates_endpoint": "/api/team-builder-materialization/repair-patch-candidates/latest",
            "repair_apply_gate_endpoint": "/api/team-builder-materialization/repair-apply-gate/latest",
            "repair_apply_execution_endpoint": "/api/team-builder-materialization/repair-apply-execution/latest",
            "repair_post_apply_verification_endpoint": "/api/team-builder-materialization/repair-post-apply-verification/latest",
            "repair_outcome_reconciliation_endpoint": "/api/team-builder-materialization/repair-outcome-reconciliation/latest",
            "repair_rollback_execution_endpoint": "/api/team-builder-materialization/repair-rollback-execution/latest",
            "repair_rollback_post_verification_endpoint": "/api/team-builder-materialization/repair-rollback-post-verification/latest",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repair_generalization_trial_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_generalization_trial.json"


def _team_builder_repair_generalization_trial_report() -> dict[str, Any]:
    run_dir, reason = _team_builder_latest_run_dir()
    run_id = run_dir.name if run_dir else "standalone-repair-generalization-trial"
    team_name = "team_observer_material_trial" if run_dir else ""
    controlled_candidates = [
        {
            "id": "generalization_candidate:multi_file_worker_contract",
            "priority": 1,
            "title": "多文件 worker 修复候选",
            "summary": "模拟一个真实 generated worker 需要同时修改 worker 源码和格式/辅助文件的情况。",
            "changed_files": [
                "src/generated/workers/report_writer.py",
                "src/generated/formats.py",
            ],
            "risk": "multi_file_real_write_not_enabled",
            "expected_handling": "只能进入候选审阅和 scratch 试验；真实应用、回滚和前后对账必须扩展到文件集后才能放行。",
        },
        {
            "id": "generalization_candidate:single_file_alternative",
            "priority": 2,
            "title": "单文件备选修复候选",
            "summary": "模拟另一个只修改单个 worker 的备选方案，用于验证多候选排序和逐候选审阅。",
            "changed_files": [
                "src/generated/workers/material_mapper.py",
            ],
            "risk": "needs_candidate_ranking_and_isolated_verification",
            "expected_handling": "即使是单文件，也必须和多文件候选分开生成 diff、预览、批准、应用和验证记录。",
        },
        {
            "id": "generalization_candidate:contract_target_rejected",
            "priority": 3,
            "title": "错误 contract 目标候选",
            "summary": "模拟候选误把 tests/teams contract 当成修复写入目标的情况。",
            "changed_files": [
                "tests/teams/team_observer_material_trial/test_contract.py",
            ],
            "risk": "contract_target_must_be_rejected",
            "expected_handling": "contract 只能作为验收定义和诊断证据，不能被自动修复当成写入目标。",
        },
    ]
    candidate_count = len(controlled_candidates)
    multi_file_candidate_count = sum(1 for item in controlled_candidates if len(_list_value(item.get("changed_files"))) > 1)
    contract_target_count = sum(
        1
        for item in controlled_candidates
        if any(_safe_text(path, 320).replace("\\", "/").startswith("tests/teams/") for path in _list_value(item.get("changed_files")))
    )
    blocked_for_real_apply = contract_target_count
    trial_cases = [
        {
            "id": "candidate_ordering",
            "name": "多候选排序必须可见",
            "status": "pass",
            "summary": "受控样本包含 3 个候选，并按 priority 展示；后续真实候选不能只展示第一个。",
            "evidence": [item["id"] for item in controlled_candidates],
        },
        {
            "id": "multi_file_preview_guard",
            "name": "多文件补丁必须走文件集安全门",
            "status": "pass",
            "summary": "多文件候选必须先进入 scratch 文件集预览；真实 apply 还要同时满足 confirm_real_file_write 和 confirm_file_set_write。",
            "evidence": ["repair_apply_preview: multi-file -> scratch file set", "repair_apply_execution: confirm_file_set_write required", f"multi_file_candidates={multi_file_candidate_count}"],
        },
        {
            "id": "contract_target_guard",
            "name": "contract 目标必须被拒绝",
            "status": "pass",
            "summary": "候选目标位于 tests/teams 时只能作为验收定义审阅，不能作为修复写入目标。",
            "evidence": [f"contract_target_candidates={contract_target_count}"],
        },
        {
            "id": "no_real_write",
            "name": "泛化试验不写真实文件",
            "status": "pass",
            "summary": "本报告只写 material，不调用 apply/rollback POST，不写 generated code。",
            "evidence": ["real_writes=0", "post_execute_called=false"],
        },
    ]
    verdict = "guarded_trial_ready"
    summary = (
        "多候选/多文件泛化试验已建立：候选排序、文件集显式安全门、contract 目标拒绝和只读边界均可审阅；"
        "下一步应使用真实 generated worker 多文件候选验证完整文件集修复闭环。"
    )
    path = _team_builder_repair_generalization_trial_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": team_name,
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "candidate_count": candidate_count,
            "multi_file_candidate_count": multi_file_candidate_count,
            "contract_target_count": contract_target_count,
            "blocked_for_real_apply": blocked_for_real_apply,
            "scratch_preview_required": candidate_count,
            "real_writes": 0,
        },
        "quality_gates": [
            _test_gate(
                "candidate_ordering_visible",
                "候选排序可见",
                "pass",
                "受控样本包含多个候选，并显式展示 priority。",
                [item["id"] for item in controlled_candidates],
            ),
            _test_gate(
                "multi_file_real_apply_guarded",
                "多文件真实应用受文件集门保护",
                "pass",
                "真实多文件应用不再按单文件模型偷跑；必须先有逐文件预览，再同时确认 confirm_real_file_write 和 confirm_file_set_write。",
                [f"multi_file_candidate_count={multi_file_candidate_count}"],
            ),
            _test_gate(
                "contract_target_rejected",
                "contract 不作为修复目标",
                "pass",
                "contract 文件只能作为验收定义，不进入真实修复写入目标。",
                [f"contract_target_count={contract_target_count}"],
            ),
            _test_gate(
                "trial_is_read_only",
                "试验只读",
                "pass",
                "GET 报告只写 material 产物，不执行真实 apply/rollback。",
                ["real_writes=0"],
            ),
        ],
        "trial_cases": trial_cases,
        "controlled_candidates": controlled_candidates,
        "next_actions": [
            {
                "id": "validate_real_generated_file_set_trial",
                "title": "验证真实 generated worker 文件集修复",
                "summary": "文件集 apply/rollback 记录模型已具备显式确认和逐文件记录；下一步要用真实 generated worker 多文件候选跑完应用、验证、对账、回滚和回滚后验证。",
                "endpoint": "/api/team-builder-materialization/repair-real-generated-file-set-trial/latest",
            },
            {
                "id": "add_candidate_set_reconciliation",
                "title": "补候选集级对账",
                "summary": "对每个候选分别记录应用、验证、回滚和回滚后验证状态，避免多个候选共享一个单文件状态。",
                "endpoint": "/api/team-builder-materialization/repair-closure-rollup/latest",
            },
        ],
        "source": {
            "latest_run_reason": "" if run_dir else reason,
            "repair_generalization_trial_material": str(path.relative_to(_repo_root())) if path else "",
            "repair_closure_rollup_endpoint": "/api/team-builder-materialization/repair-closure-rollup/latest",
            "repair_apply_preview_endpoint": "/api/team-builder-materialization/repair-apply-preview/latest",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_real_generated_file_set_trial_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_generated_file_set_trial.json"


def _team_builder_repair_real_run_candidate_scan_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_candidate_scan.json"


def _team_builder_is_provider_baseline_run(run_dir: Path) -> bool:
    summary = _read_json_file(run_dir / "summary.json")
    return _safe_text(summary.get("mode"), 80) == "snapshot_provider_baseline"


def _team_builder_real_materialization_run_dirs(*, include_provider_baselines: bool = False) -> list[Path]:
    root = _repo_root() / "_scratch" / "team_builder_real_material_validation"
    if not root.is_dir():
        return []
    candidates = [
        path
        for path in root.iterdir()
        if path.is_dir()
        and (path / "summary.json").is_file()
        and (include_provider_baselines or not _team_builder_is_provider_baseline_run(path))
    ]
    return sorted(candidates, key=lambda path: ((path / "summary.json").stat().st_mtime, path.name), reverse=True)


def _team_builder_provider_trial_run_dirs() -> list[Path]:
    root = _repo_root() / "_scratch" / "team_builder_provider_trials"
    if not root.is_dir():
        return []
    candidates = [
        path
        for path in root.iterdir()
        if path.is_dir() and (path / "summary.json").is_file() and not _team_builder_is_provider_baseline_run(path)
    ]
    return sorted(candidates, key=lambda path: ((path / "summary.json").stat().st_mtime, path.name), reverse=True)


def _team_builder_count_value(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def _team_builder_rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_repo_root().resolve()).as_posix()
    except (OSError, ValueError):
        return str(path).replace("\\", "/")


def _team_builder_real_run_source_files(run_dir: Path, code_package: dict[str, Any]) -> tuple[list[str], int]:
    code_root = run_dir / "code_package_files"
    files: list[str] = []
    if code_root.is_dir():
        allowed_suffixes = {".py", ".md", ".yaml", ".yml", ".json", ".toml"}
        for path in sorted(code_root.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_file() and path.suffix.lower() in allowed_suffixes:
                try:
                    files.append(path.relative_to(code_root).as_posix())
                except ValueError:
                    files.append(path.name)
    if not files:
        material_files = _dict_value(code_package.get("files"))
        files = sorted(_safe_text(name, 260) for name in material_files.keys() if _safe_text(name, 260))
    return files[:12], len(files)


def _team_builder_repair_real_run_candidate_scan_report() -> dict[str, Any]:
    run_dirs = _team_builder_real_materialization_run_dirs()
    latest_run = run_dirs[0] if run_dirs else None
    report_run_id = latest_run.name if latest_run else "standalone-real-run-candidate-scan"
    material_path = _team_builder_repair_real_run_candidate_scan_path(report_run_id)
    if not run_dirs:
        report = {
            "available": False,
            "run_id": report_run_id,
            "team_name": "",
            "verdict": "unavailable",
            "summary": "还没有可扫描的 TeamBuilder 实战 run；需要先产生至少一个 summary.json。",
            "counts": {
                "runs_scanned": 0,
                "failure_candidates": 0,
                "repair_ready_candidates": 0,
                "validation_gap_runs": 0,
                "clean_runs": 0,
                "source_ready_candidates": 0,
                "doctor_ready_candidates": 0,
                "patch_candidate_sets": 0,
                "real_repo_writes": 0,
            },
            "quality_gates": [
                _test_gate("real_runs_available", "真实 run 可扫描", "fail", "没有找到 _scratch/team_builder_real_material_validation 下带 summary.json 的 run。", []),
            ],
            "candidates": [],
            "run_summaries": [],
            "next_actions": [
                {
                    "id": "capture_real_team_builder_run",
                    "title": "先捕获真实 TeamBuilder run",
                    "summary": "没有真实 run 时不能判断真实失败候选；先跑一次 TeamBuilder 实战并产出 summary/materials。",
                    "endpoint": "/api/team-builder-materialization/latest",
                },
            ],
            "source": {
                "scan_material": str(material_path.relative_to(_repo_root())) if material_path else "",
                "materialization_root": "_scratch/team_builder_real_material_validation",
            },
        }
        return report

    run_summaries: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    validation_gap_runs = 0
    clean_runs = 0
    repair_ready_candidates = 0
    source_ready_candidates = 0
    doctor_ready_candidates = 0
    patch_candidate_sets = 0

    for run_dir in run_dirs[:12]:
        materials_dir = run_dir / "materials"
        summary = _read_json_file(run_dir / "summary.json")
        code_review = _read_json_file(materials_dir / "code_review_report.json")
        team_test = _read_json_file(materials_dir / "team_test_report.json")
        doctor_findings = _read_json_file(materials_dir / "team_doctor_findings.json")
        repair_plan = _read_json_file(materials_dir / "team_repair_plan.json")
        patch_candidates = _read_json_file(materials_dir / "team_repair_patch_candidates.json")
        code_package = _read_json_file(materials_dir / "code_package.json")
        source_files, source_file_count = _team_builder_real_run_source_files(run_dir, code_package)

        review_issues = _list_value(code_review.get("issues"))
        doctor_items = _list_value(doctor_findings.get("findings"))
        repair_actions = _list_value(repair_plan.get("actions"))
        patch_items = _list_value(patch_candidates.get("candidates"))
        repair_counts = _dict_value(repair_plan.get("counts"))
        test_counts = _dict_value(team_test.get("counts"))

        critical_count = _team_builder_count_value(code_review.get("critical_count"))
        warning_count = _team_builder_count_value(code_review.get("warning_count"))
        repair_required = _team_builder_count_value(repair_counts.get("repair_required"))
        if repair_required == 0:
            repair_required = sum(1 for item in repair_actions if _safe_text(_dict_value(item).get("category"), 80) == "repair_required")
        validation_gap = _team_builder_count_value(repair_counts.get("validation_gap"))
        if validation_gap == 0:
            validation_gap = sum(1 for item in repair_actions if _safe_text(_dict_value(item).get("category"), 80) == "validation_gap")
        failed_workers = _team_builder_count_value(test_counts.get("failed_workers"))
        if failed_workers == 0:
            failed_workers = len(_list_value(team_test.get("failed_workers")))
        doctor_count = len(doctor_items)
        patch_count = len(patch_items)

        review_verdict = _safe_text(code_review.get("verdict") or code_review.get("kind"), 80)
        test_verdict = _safe_text(team_test.get("verdict"), 80)
        summary_verdict = _safe_text(summary.get("verdict") or summary.get("kind"), 80)
        has_failure = (
            review_verdict == "fail"
            or test_verdict == "fail"
            or summary_verdict == "fail"
            or critical_count > 0
            or failed_workers > 0
        )
        has_validation_gap = validation_gap > 0 or (doctor_count > 0 and not has_failure)
        source_ready = source_file_count > 0
        doctor_ready = doctor_count > 0 or bool(repair_plan)
        candidate_ready = repair_required > 0 and source_ready and doctor_ready
        if candidate_ready:
            classification = "repair_ready"
            repair_ready_candidates += 1
        elif has_failure:
            classification = "failure_without_repair_plan"
        elif has_validation_gap:
            classification = "validation_gap_only"
            validation_gap_runs += 1
        else:
            classification = "clean"
            clean_runs += 1

        if (has_failure or repair_required > 0) and source_ready:
            source_ready_candidates += 1
        if doctor_ready and (has_failure or repair_required > 0 or has_validation_gap):
            doctor_ready_candidates += 1
        if patch_count:
            patch_candidate_sets += 1

        evidence: list[str] = []
        if review_verdict:
            evidence.append(f"code_review={review_verdict}, critical={critical_count}, warning={warning_count}")
        if test_verdict:
            evidence.append(f"team_test={test_verdict}, failed_workers={failed_workers}")
        if doctor_count:
            evidence.append(f"doctor_findings={doctor_count}")
        if repair_required or validation_gap:
            evidence.append(f"repair_required={repair_required}, validation_gap={validation_gap}")
        if patch_count:
            evidence.append(f"patch_candidates={patch_count}")
        if source_ready:
            evidence.append(f"source_files={source_file_count}")

        material_links = []
        for label, filename in [
            ("代码审查报告", "code_review_report.json"),
            ("team 测试报告", "team_test_report.json"),
            ("doctor 发现", "team_doctor_findings.json"),
            ("修复计划", "team_repair_plan.json"),
            ("候选补丁", "team_repair_patch_candidates.json"),
        ]:
            path = materials_dir / filename
            material_links.append({
                "label": label,
                "path": _team_builder_rel_path(path),
                "available": path.is_file(),
            })

        run_item = {
            "run_id": run_dir.name,
            "team_name": _safe_text(team_test.get("team_name") or repair_plan.get("team_name") or summary.get("team_name"), 160),
            "classification": classification,
            "summary": (
                "真实失败已经出现，但还没有形成可直接执行的 repair_required 修复候选。"
                if classification == "failure_without_repair_plan"
                else "已经具备 repair_required、源码和诊断输入，可进入真实候选 diff 生成前审阅。"
                if classification == "repair_ready"
                else "这是验证覆盖缺口，不应直接改生成代码。"
                if classification == "validation_gap_only"
                else "当前 run 没有发现真实失败或待修复候选。"
            ),
            "counts": {
                "critical": critical_count,
                "warnings": warning_count,
                "failed_workers": failed_workers,
                "doctor_findings": doctor_count,
                "repair_required": repair_required,
                "validation_gap": validation_gap,
                "patch_candidates": patch_count,
                "source_files": source_file_count,
            },
            "source_ready": source_ready,
            "doctor_ready": doctor_ready,
            "candidate_ready": candidate_ready,
            "evidence": evidence,
            "source_files": source_files,
            "materials": material_links,
        }
        run_summaries.append(run_item)
        if classification in {"repair_ready", "failure_without_repair_plan"}:
            candidates.append(run_item)

    failure_candidates = sum(1 for item in candidates if item["classification"] == "failure_without_repair_plan")
    if repair_ready_candidates:
        verdict = "candidate_ready"
        summary_text = f"扫描 {len(run_summaries)} 个真实 TeamBuilder run，发现 {repair_ready_candidates} 个可进入真实修复候选审阅的 run。"
    elif failure_candidates:
        verdict = "failure_candidate_needs_doctor"
        summary_text = f"扫描 {len(run_summaries)} 个真实 TeamBuilder run，发现 {failure_candidates} 个真实失败候选，但还缺 doctor/repair plan 消解。"
    elif validation_gap_runs:
        verdict = "validation_gap_only"
        summary_text = f"扫描 {len(run_summaries)} 个真实 TeamBuilder run，当前主要是 {validation_gap_runs} 个验证覆盖缺口，不应直接改生成代码。"
    else:
        verdict = "no_real_failure_candidate"
        summary_text = f"扫描 {len(run_summaries)} 个真实 TeamBuilder run，没有发现需要真实修复的失败候选。"

    source_missing = sum(1 for item in candidates if not item["source_ready"])
    quality_gates = [
        _test_gate("real_runs_scanned", "真实 run 已扫描", "pass", f"已扫描 {len(run_summaries)} 个带 summary.json 的 TeamBuilder 实战 run。", [item["run_id"] for item in run_summaries[:5]]),
        _test_gate("failure_and_validation_gap_separated", "失败与验证缺口已分开", "pass", "报告把 code review/worker 失败、repair_required 和 validation_gap 分开计数，避免把验证缺口误当作修代码任务。", [f"failure_candidates={failure_candidates}", f"validation_gap_runs={validation_gap_runs}"]),
        _test_gate("repair_requires_explicit_candidate", "真实修复必须有 repair_required 候选", "pass", "没有 repair_required、源码和诊断输入三者同时满足时，不进入真实 diff/apply。", [f"repair_ready_candidates={repair_ready_candidates}"]),
        _test_gate("source_package_visible", "候选源码入口可见", "pass" if source_missing == 0 else "warning", "所有真实失败候选都能看到 generated source 入口。" if source_missing == 0 else "部分真实失败候选缺少 generated source 入口。", [f"source_missing={source_missing}"]),
        _test_gate("scan_is_read_only", "扫描只读", "pass", "本接口只读取已有 run/material 并写扫描 material，不执行 apply/rollback，也不修改真实 generated code。", ["real_repo_writes=0"]),
    ]
    if repair_ready_candidates:
        next_actions = [
            {
                "id": "generate_real_run_patch_diff",
                "title": "为真实失败候选生成 diff 前审阅",
                "summary": "已有 repair_required 候选；下一步应按候选 run 的源码、doctor 发现和测试报告生成可审阅 diff，仍不自动应用。",
                "endpoint": "/api/team-builder-materialization/repair-patch-diff-proposal/latest",
            }
        ]
    elif failure_candidates:
        next_actions = [
            {
                "id": "replay_failed_run_to_repair_plan",
                "title": "把真实失败 run 消解成 doctor/repair plan",
                "summary": "已有真实失败证据，但还没有 repair_required 候选；下一步要回放该 run 的测试、代码审查和源码包，生成 doctor finding 与修复计划。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-replay-plan/latest",
            }
        ]
    else:
        next_actions = [
            {
                "id": "capture_real_failing_team_builder_run",
                "title": "捕获一个真实失败 TeamBuilder run",
                "summary": "当前没有真实修复候选；下一步要用真实 TeamBuilder 流水线产生一个可失败、可诊断、可修复的 generated worker run。",
                "endpoint": "/api/team-builder-materialization/latest",
            }
        ]

    report = {
        "available": True,
        "run_id": report_run_id,
        "team_name": "team_builder",
        "verdict": verdict,
        "summary": summary_text,
        "counts": {
            "runs_scanned": len(run_summaries),
            "failure_candidates": failure_candidates,
            "repair_ready_candidates": repair_ready_candidates,
            "validation_gap_runs": validation_gap_runs,
            "clean_runs": clean_runs,
            "source_ready_candidates": source_ready_candidates,
            "doctor_ready_candidates": doctor_ready_candidates,
            "patch_candidate_sets": patch_candidate_sets,
            "real_repo_writes": 0,
        },
        "quality_gates": quality_gates,
        "candidates": candidates[:6],
        "run_summaries": run_summaries[:12],
        "next_actions": next_actions,
        "source": {
            "scan_material": str(material_path.relative_to(_repo_root())) if material_path else "",
            "materialization_root": "_scratch/team_builder_real_material_validation",
            "latest_run": latest_run.name if latest_run else "",
        },
    }
    if material_path:
        try:
            material_path.parent.mkdir(parents=True, exist_ok=True)
            material_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repair_real_run_replay_plan_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_replay_plan.json"


def _team_builder_repair_real_run_replay_plan_report() -> dict[str, Any]:
    scan = _team_builder_repair_real_run_candidate_scan_report()
    candidates = _list_value(scan.get("candidates"))
    selected = next(
        (
            _dict_value(item)
            for item in candidates
            if _safe_text(_dict_value(item).get("classification"), 80) in {"failure_without_repair_plan", "repair_ready"}
        ),
        {},
    )
    run_id = _safe_text(selected.get("run_id"), 160)
    if not run_id:
        return {
            "available": False,
            "run_id": _safe_text(scan.get("run_id"), 160),
            "team_name": "team_builder",
            "verdict": "no_failed_candidate",
            "summary": "当前没有可消解的真实失败候选。",
            "counts": {
                "code_review_issues": 0,
                "repair_required": 0,
                "source_located": 0,
                "source_missing": 0,
                "diffs_generated": 0,
                "real_repo_writes": 0,
            },
            "quality_gates": [
                _test_gate("failed_candidate_selected", "已选择真实失败候选", "warning", "候选扫描没有返回真实失败候选。", []),
            ],
            "findings": [],
            "repair_actions": [],
            "next_actions": [
                {
                    "id": "capture_real_failing_team_builder_run",
                    "title": "捕获真实失败 run",
                    "summary": "需要先产生真实失败候选，才能消解成 doctor/repair plan。",
                    "endpoint": "/api/team-builder-materialization/repair-real-run-candidate-scan/latest",
                }
            ],
            "source": {
                "candidate_scan_endpoint": "/api/team-builder-materialization/repair-real-run-candidate-scan/latest",
                "replay_plan_material": "",
            },
        }

    run_dir = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id
    materials_dir = run_dir / "materials"
    code_review = _read_json_file(materials_dir / "code_review_report.json")
    issues = [_dict_value(item) for item in _list_value(code_review.get("issues"))]
    source_files = set(_safe_text(item, 320) for item in _list_value(selected.get("source_files")))
    findings: list[dict[str, Any]] = []
    repair_actions: list[dict[str, Any]] = []
    source_missing = 0

    for index, issue in enumerate(issues):
        worker_id = _safe_text(issue.get("worker_id"), 120)
        category = _safe_text(issue.get("category"), 120)
        required_not_read = [_safe_text(item, 120) for item in _list_value(issue.get("required_not_read"))]
        source_rel = f"workers/{worker_id}.py" if worker_id else ""
        source_path = run_dir / "code_package_files" / source_rel if source_rel else None
        source_located = bool(source_path and source_path.is_file())
        if not source_located and source_rel not in source_files:
            source_missing += 1
        severity = _safe_text(issue.get("severity"), 80) or "unknown"
        repair_category = "repair_required" if severity == "critical" and category == "input_key_not_read" and source_located else "needs_manual_triage"
        finding_id = f"team_builder.real_run.code_review:{run_id}:{index}"
        finding = {
            "id": finding_id,
            "check_id": "team_builder.real_run.code_review",
            "level": "error" if repair_category == "repair_required" else "advisory",
            "severity": severity,
            "target_kind": "worker",
            "target_id": worker_id,
            "location": f"node:{worker_id}" if worker_id else "run",
            "category": repair_category,
            "observation": _safe_text(issue.get("issue"), 620),
            "implication": (
                "worker 没有读取输入 material 的必需字段，真实 generated team 的数据血缘图可能丢失输入文件信息。"
                if repair_category == "repair_required"
                else "该问题还不能自动归入可修复代码缺陷，需要人工确认。"
            ),
            "source_file": _team_builder_rel_path(source_path) if source_path else source_rel,
            "required_not_read": required_not_read,
            "format_in": [_safe_text(item, 160) for item in _list_value(issue.get("format_in"))],
            "evidence": [
                _team_builder_rel_path(materials_dir / "code_review_report.json"),
                _team_builder_rel_path(source_path) if source_path else source_rel,
            ],
        }
        findings.append(finding)
        if repair_category == "repair_required":
            repair_actions.append({
                "id": f"repair_action:{len(repair_actions)}",
                "finding_id": finding_id,
                "category": "repair_required",
                "automation_level": "patch_plan_only",
                "auto_safe": False,
                "worker_id": worker_id,
                "changed_files": [_team_builder_rel_path(source_path)] if source_path else [],
                "required_input_fields": required_not_read,
                "proposed_change": "在 worker 的 run() 中显式读取并使用输入 material 的必需字段；本候选是 diff 生成输入，不在此接口直接改文件。",
                "verification": [
                    "重新执行 generated package code review，确认 input_key_not_read 清零。",
                    "重新执行 team_test_report/doctor_findings，确认该 finding 不再出现。",
                ],
            })

    material_path = _team_builder_repair_real_run_replay_plan_path(run_id)
    repair_required = len(repair_actions)
    verdict = "repair_plan_ready" if repair_required else "no_repair_action"
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(selected.get("team_name"), 160),
        "verdict": verdict,
        "summary": (
            f"真实失败 run {run_id} 已消解出 {repair_required} 条 repair_required 修复计划；仍然只生成计划，不生成 diff、不应用。"
            if repair_required
            else f"真实失败 run {run_id} 已读取，但没有形成可自动进入 patch plan 的 repair_required。"
        ),
        "counts": {
            "code_review_issues": len(issues),
            "repair_required": repair_required,
            "source_located": len(repair_actions),
            "source_missing": source_missing,
            "diffs_generated": 0,
            "real_repo_writes": 0,
        },
        "quality_gates": [
            _test_gate("failed_candidate_selected", "已选择真实失败候选", "pass", f"候选来自 run {run_id}。", [run_id]),
            _test_gate("code_review_issue_consumed", "代码审查问题已消费", "pass" if issues else "fail", f"读取 code_review issues {len(issues)} 条。", [_team_builder_rel_path(materials_dir / "code_review_report.json")]),
            _test_gate("source_target_located", "修复目标源码已定位", "pass" if source_missing == 0 and repair_actions else "warning", "repair_required action 均已定位到 generated worker 源码。" if source_missing == 0 and repair_actions else "仍有问题缺少源码入口或未形成 repair_required。", [item for action in repair_actions for item in _list_value(action.get("changed_files"))]),
            _test_gate("replay_plan_is_read_only", "消解报告只读", "pass", "本接口只写 replay plan material，不生成 diff、不执行 apply/rollback。", ["diffs_generated=0", "real_repo_writes=0"]),
        ],
        "findings": findings,
        "repair_actions": repair_actions,
        "next_actions": [
            {
                "id": "generate_reviewable_real_run_diff",
                "title": "为真实失败候选生成可审阅 diff",
                "summary": "repair plan 已明确目标 worker、必需字段和验证方式；下一步才能生成 diff 预览，仍需人工审阅和显式确认。",
                "endpoint": "/api/team-builder-materialization/repair-patch-diff-proposal/latest",
            }
        ] if repair_required else [
            {
                "id": "manual_triage_real_run_failure",
                "title": "人工复核真实失败候选",
                "summary": "没有形成 repair_required action；需要人工确认 schema、代码审查规则或失败语义。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-candidate-scan/latest",
            }
        ],
        "source": {
            "candidate_scan_endpoint": "/api/team-builder-materialization/repair-real-run-candidate-scan/latest",
            "code_review_report": _team_builder_rel_path(materials_dir / "code_review_report.json"),
            "candidate_run_dir": _team_builder_rel_path(run_dir),
            "replay_plan_material": str(material_path.relative_to(_repo_root())) if material_path else "",
        },
    }
    if material_path:
        try:
            material_path.parent.mkdir(parents=True, exist_ok=True)
            material_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repair_real_run_diff_preview_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_diff_preview.json"


def _team_builder_repair_real_run_diff_review_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_diff_review.json"


def _team_builder_repair_real_run_apply_gate_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_apply_gate.json"


def _team_builder_repair_real_run_apply_preview_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_apply_preview.json"


def _team_builder_repair_real_run_apply_execution_records_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_apply_execution_records.json"


def _team_builder_repair_real_run_apply_execution_report_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_apply_execution_report.json"


def _team_builder_repair_real_run_apply_rehearsal_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_apply_rehearsal.json"


def _team_builder_repair_real_run_auto_apply_policy_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_auto_apply_policy.json"


def _team_builder_repair_real_run_auto_apply_execution_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_auto_apply_execution.json"


def _team_builder_repair_real_run_post_apply_verification_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_post_apply_verification_result.json"


def _team_builder_repair_real_run_outcome_reconciliation_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_outcome_reconciliation.json"


def _team_builder_repair_real_run_rollback_readiness_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_rollback_readiness.json"


def _team_builder_repair_real_run_rollback_execution_records_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_rollback_execution_records.json"


def _team_builder_repair_real_run_rollback_execution_report_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_rollback_execution_report.json"


def _team_builder_repair_real_run_rollback_post_verification_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_rollback_post_verification_result.json"


def _team_builder_repair_real_run_closure_rollup_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_repair_real_run_closure_rollup.json"


def _team_builder_high_standard_audit_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_builder_high_standard_audit.json"


def _team_builder_provider_coverage_audit_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_builder_provider_coverage_audit.json"


def _team_builder_provider_same_input_trial_plan_path(run_id: str) -> Path | None:
    run_id = _safe_text(run_id, 160)
    if not run_id:
        return None
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_builder_provider_same_input_trial_plan.json"


def _team_builder_source_reads_field(source_text: str, field_name: str) -> bool:
    field_name = _safe_text(field_name, 120)
    if not field_name:
        return False
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == field_name:
                    return True
            if isinstance(node, ast.Subscript):
                key_node = node.slice
                if isinstance(key_node, ast.Constant) and key_node.value == field_name:
                    return True
    return (
        f'.get("{field_name}"' in source_text
        or f".get('{field_name}'" in source_text
        or f'["{field_name}"]' in source_text
        or f"['{field_name}']" in source_text
    )


def _team_builder_real_run_after_text(before: str, required_fields: list[str]) -> tuple[str, list[str]]:
    after = before
    changes: list[str] = []
    if "files" in required_fields and "bundle.get(\"files\"" not in after and "bundle.get('files'" not in after:
        anchor = '        events: list[dict] = bundle.get("events", [])\n'
        if anchor in after:
            after = after.replace(anchor, anchor + '        files: list[dict] = bundle.get("files", [])\n', 1)
            changes.append("读取输入 material 的 files 字段")
    if "files" in required_fields and "for item in files:" not in after:
        anchor = (
            '        # Add workspace root nodes\n'
            '        for root in workspace_roots:\n'
            '            _ensure_node(f"ws:{root}", "workspace", root)\n'
            '\n'
        )
        if anchor in after:
            replacement = (
                anchor
                + '        # Add declared files from the run artifact bundle so the input material keeps its file list.\n'
                + '        for item in files:\n'
                + '            if isinstance(item, dict):\n'
                + '                file_path = item.get("path") or item.get("file") or item.get("rel_path")\n'
                + '                if file_path:\n'
                + '                    file_label = str(file_path)\n'
                + '                    _ensure_node(f"file:{file_label}", "workspace", file_label)\n'
                + '                    confidence_notes.append(f"declared_file: {file_label}")\n'
                + '\n'
            )
            after = after.replace(anchor, replacement, 1)
            changes.append("把 files 内容写入 lineage graph 节点与证据说明")
    return after, changes


def _team_builder_repair_real_run_diff_preview_report() -> dict[str, Any]:
    replay = _team_builder_repair_real_run_replay_plan_report()
    run_id = _safe_text(replay.get("run_id"), 160) or "standalone-real-run-diff-preview"
    material_path = _team_builder_repair_real_run_diff_preview_path(run_id)
    actions = [_dict_value(item) for item in _list_value(replay.get("repair_actions"))]
    records: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []
    preview_root = _repo_root() / "_scratch" / "team_builder_repair_apply_preview" / run_id / "real_run_diff_preview"

    for action in actions:
        action_id = _safe_text(action.get("id"), 120)
        changed_files = [_safe_text(item, 420) for item in _list_value(action.get("changed_files")) if _safe_text(item, 420)]
        required_fields = [_safe_text(item, 120) for item in _list_value(action.get("required_input_fields"))]
        if _safe_text(action.get("category"), 80) != "repair_required":
            blocked_items.append({"action_id": action_id, "reason": "不是 repair_required action。"})
            continue
        if not changed_files:
            blocked_items.append({"action_id": action_id, "reason": "缺少目标文件。"})
            continue
        for changed_file in changed_files:
            target_path = (_repo_root() / changed_file).resolve()
            try:
                target_path.relative_to(_repo_root().resolve())
            except (OSError, ValueError):
                blocked_items.append({"action_id": action_id, "changed_file": changed_file, "reason": "目标文件不在仓库范围内。"})
                continue
            if not target_path.is_file():
                blocked_items.append({"action_id": action_id, "changed_file": changed_file, "reason": "目标源码不存在。"})
                continue
            if not changed_file.replace("\\", "/").startswith("_scratch/team_builder_real_material_validation/"):
                blocked_items.append({"action_id": action_id, "changed_file": changed_file, "reason": "当前真实 run diff 预览只允许指向候选 run 的 scratch generated package。"})
                continue
            try:
                before = target_path.read_text(encoding="utf-8")
            except OSError as exc:
                blocked_items.append({"action_id": action_id, "changed_file": changed_file, "reason": f"读取源码失败: {exc}"})
                continue
            after, changes = _team_builder_real_run_after_text(before, required_fields)
            if after == before or not changes:
                blocked_items.append({"action_id": action_id, "changed_file": changed_file, "reason": "没有命中可解释的最小改动规则，需要 AI 或人工生成 diff。"})
                continue
            diff_text = _team_builder_diff_text(changed_file.replace("\\", "/"), before, after)
            diff_blocks = _team_builder_split_unified_diff_by_file(diff_text)
            diff_block = diff_blocks.get(_team_builder_normalize_diff_file_path(changed_file), diff_text)
            try:
                applied = _team_builder_apply_unified_diff_to_text(before, diff_block)
            except Exception as exc:
                blocked_items.append({"action_id": action_id, "changed_file": changed_file, "reason": f"diff 回放失败: {type(exc).__name__}: {exc}"})
                continue
            if applied != after:
                blocked_items.append({"action_id": action_id, "changed_file": changed_file, "reason": "diff 回放结果与 after 预览不一致。"})
                continue
            before_preview = preview_root / "before" / changed_file
            after_preview = preview_root / "after" / changed_file
            try:
                before_preview.parent.mkdir(parents=True, exist_ok=True)
                after_preview.parent.mkdir(parents=True, exist_ok=True)
                before_preview.write_text(before, encoding="utf-8")
                after_preview.write_text(after, encoding="utf-8")
            except OSError as exc:
                blocked_items.append({"action_id": action_id, "changed_file": changed_file, "reason": f"写预览失败: {exc}"})
                continue
            records.append({
                "id": f"real_run_diff_preview:{len(records)}",
                "action_id": action_id,
                "worker_id": _safe_text(action.get("worker_id"), 160),
                "changed_file": changed_file.replace("\\", "/"),
                "required_input_fields": required_fields,
                "change_summary": changes,
                "diff": _safe_text(diff_text, 16000),
                "diff_sha256": _team_builder_diff_sha256(diff_text),
                "before_sha256": _team_builder_file_sha256(before_preview),
                "after_sha256": _team_builder_file_sha256(after_preview),
                "before_preview_file": _team_builder_rel_path(before_preview),
                "after_preview_file": _team_builder_rel_path(after_preview),
            })

    diff_ready = len(records)
    blocked = len(blocked_items)
    verdict = "diff_preview_ready" if diff_ready and not blocked else "blocked" if blocked else "no_repair_actions"
    summary = (
        f"真实失败候选已生成 {diff_ready} 个可审阅 diff 预览；仍然没有写回候选源码。"
        if verdict == "diff_preview_ready"
        else f"真实失败候选 diff 预览仍有 {blocked} 个阻断项。"
        if verdict == "blocked"
        else "当前没有 repair_required action 可生成 diff 预览。"
    )
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(replay.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "repair_actions": len(actions),
            "diff_ready": diff_ready,
            "files_previewed": diff_ready,
            "blocked": blocked,
            "real_repo_writes": 0,
        },
        "quality_gates": [
            _test_gate("repair_plan_consumed", "已消费真实失败修复计划", "pass" if actions else "warning", f"读取 repair actions {len(actions)} 条。", [run_id]),
            _test_gate("target_scope_is_scratch_candidate", "目标限制在候选 generated 包", "pass" if not blocked or diff_ready else "warning", "diff 预览只指向真实 run 的 scratch generated package，不触碰业务源码。", [_safe_text(item.get("reason"), 220) for item in blocked_items[:5]]),
            _test_gate("diff_replay_verified", "diff 可回放", "pass" if diff_ready else "warning", f"{diff_ready} 个 diff 已在内存中回放到 after 预览。", [record["changed_file"] for record in records[:5]]),
            _test_gate("preview_is_read_only", "预览只读", "pass", "本接口只写 before/after 预览和 material，不改候选源码，不执行 apply/rollback。", ["real_repo_writes=0"]),
        ],
        "diff_records": records,
        "blocked_items": blocked_items,
        "next_actions": [
            {
                "id": "review_real_run_diff_preview",
                "title": "审阅真实失败候选 diff 预览",
                "summary": "diff 已生成 before/after 预览；下一步应做人工审阅和显式批准，然后才能进入文件集应用预览和验证。",
                "endpoint": "/api/team-builder-materialization/repair-approval/latest",
            }
        ] if diff_ready else [
            {
                "id": "generate_ai_or_human_diff",
                "title": "由 AI 或人工补 diff",
                "summary": "当前最小规则没有生成可回放 diff，需要基于源码语义补齐。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-replay-plan/latest",
            }
        ],
        "source": {
            "replay_plan_endpoint": "/api/team-builder-materialization/repair-real-run-replay-plan/latest",
            "candidate_run_dir": _safe_text(_dict_value(replay.get("source")).get("candidate_run_dir"), 320),
            "diff_preview_material": str(material_path.relative_to(_repo_root())) if material_path else "",
            "preview_root": _team_builder_rel_path(preview_root),
        },
    }
    if material_path:
        try:
            material_path.parent.mkdir(parents=True, exist_ok=True)
            material_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repair_real_run_diff_review_report() -> dict[str, Any]:
    preview = _team_builder_repair_real_run_diff_preview_report()
    run_id = _safe_text(preview.get("run_id"), 160) or "standalone-real-run-diff-review"
    material_path = _team_builder_repair_real_run_diff_review_path(run_id)
    records = [_dict_value(item) for item in _list_value(preview.get("diff_records"))]
    upstream_blocked = [_dict_value(item) for item in _list_value(preview.get("blocked_items")) if _dict_value(item)]
    review_items: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []

    for record in records:
        record_id = _safe_text(record.get("id"), 160)
        changed_file = _safe_text(record.get("changed_file"), 420).replace("\\", "/")
        reasons: list[str] = []
        target_path = (_repo_root() / changed_file).resolve() if changed_file else _repo_root().resolve()
        before_preview_file = _safe_text(record.get("before_preview_file"), 520)
        after_preview_file = _safe_text(record.get("after_preview_file"), 520)
        before_preview_path = (_repo_root() / before_preview_file).resolve() if before_preview_file else _repo_root().resolve()
        after_preview_path = (_repo_root() / after_preview_file).resolve() if after_preview_file else _repo_root().resolve()

        target_scope_safe = changed_file.startswith("_scratch/team_builder_real_material_validation/")
        if not target_scope_safe:
            reasons.append("目标不在真实 run 的 scratch generated package 内。")
        try:
            target_path.relative_to(_repo_root().resolve())
        except (OSError, ValueError):
            reasons.append("目标文件逃逸仓库范围。")
        if not target_path.is_file():
            reasons.append("目标源码不存在。")
        if not before_preview_path.is_file() or not after_preview_path.is_file():
            reasons.append("before/after 预览文件不完整。")

        before_sha = _safe_text(record.get("before_sha256"), 96)
        after_sha = _safe_text(record.get("after_sha256"), 96)
        diff_sha = _safe_text(record.get("diff_sha256"), 96)
        current_source_sha = _team_builder_file_sha256(target_path)
        source_matches_before = bool(before_sha and current_source_sha == before_sha)
        if not before_sha or not after_sha or not diff_sha:
            reasons.append("diff/before/after sha 证据不完整。")
        if before_sha and after_sha and before_sha == after_sha:
            reasons.append("before 与 after 内容没有变化。")
        if current_source_sha and before_sha and current_source_sha != before_sha:
            reasons.append("目标源码已不等于 before 预览，不能按该 diff 继续应用。")

        change_summary = [_safe_text(item, 220) for item in _list_value(record.get("change_summary")) if _safe_text(item, 220)]
        required_fields = [_safe_text(item, 120) for item in _list_value(record.get("required_input_fields")) if _safe_text(item, 120)]
        review_status = "ready_for_explicit_review" if not reasons else "blocked"
        if reasons:
            blocked_items.append({"record_id": record_id, "changed_file": changed_file, "reasons": reasons})

        review_items.append({
            "id": f"real_run_diff_review:{len(review_items)}",
            "record_id": record_id,
            "worker_id": _safe_text(record.get("worker_id"), 160),
            "status": review_status,
            "summary": (
                "diff 证据完整、目标范围受限，已具备进入人工或 AI 审阅的条件；仍未批准、未应用。"
                if review_status == "ready_for_explicit_review"
                else "diff 预览还不能进入批准或应用。"
            ),
            "changed_file": changed_file,
            "change_summary": change_summary,
            "required_input_fields": required_fields,
            "target_scope_safe": target_scope_safe,
            "source_matches_before": source_matches_before,
            "current_source_sha256": current_source_sha,
            "before_sha256": before_sha,
            "after_sha256": after_sha,
            "diff_sha256": diff_sha,
            "before_preview_file": before_preview_file,
            "after_preview_file": after_preview_file,
            "risk_notes": [
                "当前目标限制在 _scratch 里的真实 TeamBuilder generated package，不触碰业务源码。",
                "该 diff 只补充输入 material 的 files 字段读取和 declared_file 血缘证据，不应改变 worker 的外部接口。",
                "仍需审阅者确认 files 的语义映射是否正确，并在应用后重新跑 code review、team test 和 doctor。",
            ],
            "review_questions": [
                "是否接受把输入 bundle.files 注册为 workspace 文件节点和 declared_file 证据？",
                "是否需要同时覆盖 path、file、rel_path 以外的文件字段别名？",
                "应用后是否能让 input_key_not_read finding 清零且不引入新的 material 读写缺口？",
            ],
            "evidence_links": [
                changed_file,
                before_preview_file,
                after_preview_file,
                _safe_text(_dict_value(preview.get("source")).get("diff_preview_material"), 420),
            ],
            "blocked_reasons": reasons,
        })

    ready = sum(1 for item in review_items if item["status"] == "ready_for_explicit_review")
    blocked = len(blocked_items) + len(upstream_blocked)
    verdict = "review_ready" if ready and not blocked else "blocked" if blocked else "no_diff_preview"
    summary = (
        f"真实失败候选已有 {ready} 个 diff 具备审阅条件；仍需要人工或 AI 明确批准后才能进入应用预览。"
        if verdict == "review_ready"
        else f"真实失败候选 diff 审阅门发现 {blocked} 个阻断项，不能进入批准或应用。"
        if verdict == "blocked"
        else "当前没有可审阅的真实失败候选 diff。"
    )
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(preview.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "diff_records": len(records),
            "ready_for_review": ready,
            "blocked": blocked,
            "requires_explicit_approval": ready,
            "real_repo_writes": 0,
        },
        "quality_gates": [
            _test_gate("diff_preview_consumed", "已消费 diff 预览", "pass" if records else "warning", f"读取 diff records {len(records)} 条。", [run_id]),
            _test_gate("target_scope_checked", "目标范围已检查", "pass" if ready and not blocked else "warning", "所有待审 diff 都限制在真实 run 的 scratch generated package。" if ready and not blocked else "存在目标范围、文件存在性或上游阻断问题。", [item["changed_file"] for item in review_items[:5]]),
            _test_gate("sha_evidence_checked", "sha 证据已检查", "pass" if ready and not blocked else "warning", "目标源码当前 sha 与 before 预览一致，diff/before/after sha 完整。" if ready and not blocked else "部分 diff 缺少 sha 证据或目标源码已漂移。", [f"source_matches_before={item['source_matches_before']}" for item in review_items[:5]]),
            _test_gate("explicit_approval_required", "仍需显式批准", "pass", "审阅门只生成结论和问题清单，不批准、不 apply、不 rollback。", ["real_repo_writes=0"]),
        ],
        "review_items": review_items,
        "blocked_items": blocked_items + upstream_blocked,
        "next_actions": [
            {
                "id": "build_real_run_explicit_apply_gate",
                "title": "建立真实失败 run 显式应用门",
                "summary": "审阅通过后仍需显式批准 token、目标 sha 复查、文件集预览和应用后回放验证，不能直接写候选源码。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-apply-gate/latest",
            }
        ] if ready else [
            {
                "id": "fix_or_regenerate_real_run_diff_preview",
                "title": "修正真实失败 run diff 预览",
                "summary": "先补齐 diff 预览、sha 证据或目标范围，再重新进入审阅门。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-diff-preview/latest",
            }
        ],
        "source": {
            "diff_preview_endpoint": "/api/team-builder-materialization/repair-real-run-diff-preview/latest",
            "diff_preview_material": _safe_text(_dict_value(preview.get("source")).get("diff_preview_material"), 420),
            "diff_review_material": str(material_path.relative_to(_repo_root())) if material_path else "",
        },
    }
    if material_path:
        try:
            material_path.parent.mkdir(parents=True, exist_ok=True)
            material_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repair_real_run_apply_gate_report() -> dict[str, Any]:
    review = _team_builder_repair_real_run_diff_review_report()
    run_id = _safe_text(review.get("run_id"), 160) or "standalone-real-run-apply-gate"
    material_path = _team_builder_repair_real_run_apply_gate_path(run_id)
    review_items = [_dict_value(item) for item in _list_value(review.get("review_items"))]
    apply_items: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []

    for item in review_items:
        item_id = _safe_text(item.get("id"), 160)
        changed_file = _safe_text(item.get("changed_file"), 420)
        reasons: list[str] = []
        if _safe_text(item.get("status"), 120) != "ready_for_explicit_review":
            reasons.append("diff 审阅门尚未通过。")
        if item.get("target_scope_safe") is not True:
            reasons.append("目标范围未通过 scratch generated package 限制。")
        if item.get("source_matches_before") is not True:
            reasons.append("目标源码当前 sha 不等于 before，必须重新生成 diff 预览。")
        if not _safe_text(item.get("diff_sha256"), 96):
            reasons.append("缺少 diff sha。")
        if not _safe_text(item.get("before_sha256"), 96) or not _safe_text(item.get("after_sha256"), 96):
            reasons.append("缺少 before/after sha。")

        status = "ready_for_explicit_apply_preview" if not reasons else "blocked"
        if reasons:
            blocked_items.append({"review_item_id": item_id, "changed_file": changed_file, "reasons": reasons})
        apply_items.append({
            "id": f"real_run_apply_gate:{len(apply_items)}",
            "review_item_id": item_id,
            "record_id": _safe_text(item.get("record_id"), 160),
            "worker_id": _safe_text(item.get("worker_id"), 160),
            "status": status,
            "summary": (
                "已具备进入显式应用预览的前置条件；仍必须携带确认 token，并在应用后回放验证。"
                if status == "ready_for_explicit_apply_preview"
                else "不允许进入真实应用预览。"
            ),
            "changed_file": changed_file,
            "required_input_fields": [_safe_text(field, 120) for field in _list_value(item.get("required_input_fields"))],
            "diff_sha256": _safe_text(item.get("diff_sha256"), 96),
            "before_sha256": _safe_text(item.get("before_sha256"), 96),
            "after_sha256": _safe_text(item.get("after_sha256"), 96),
            "current_source_sha256": _safe_text(item.get("current_source_sha256"), 96),
            "before_preview_file": _safe_text(item.get("before_preview_file"), 520),
            "after_preview_file": _safe_text(item.get("after_preview_file"), 520),
            "required_confirmations": [
                "confirm_real_run_diff_review",
                "confirm_real_run_file_set_write",
                "confirm_post_apply_replay_required",
            ],
            "post_apply_verification": [
                "重新执行 generated package code review，确认 input_key_not_read 清零。",
                "重新执行 team_test_report 与 doctor_findings，确认不引入新失败。",
                "重新执行 closure rollup，确认应用后状态可解释。",
            ],
            "rollback_requirement": "应用前必须保存 before sha 和 before 预览，应用后必须能按 before sha 回滚。",
            "blocked_reasons": reasons,
        })

    ready = sum(1 for item in apply_items if item["status"] == "ready_for_explicit_apply_preview")
    blocked = len(blocked_items)
    verdict = "ready_for_explicit_apply_preview" if ready and not blocked else "blocked" if blocked else "no_review_ready_diff"
    summary = (
        f"真实失败候选已有 {ready} 个 diff 通过应用门前置检查；下一步只能生成显式应用预览，不能自动写文件。"
        if verdict == "ready_for_explicit_apply_preview"
        else f"真实失败候选应用门发现 {blocked} 个阻断项。"
        if verdict == "blocked"
        else "当前没有通过审阅门的真实失败候选 diff。"
    )
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(review.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "review_items": len(review_items),
            "apply_preview_ready": ready,
            "blocked": blocked,
            "required_confirmation_tokens": 3 if ready else 0,
            "real_repo_writes": 0,
        },
        "quality_gates": [
            _test_gate("diff_review_consumed", "已消费 diff 审阅门", "pass" if review_items else "warning", f"读取 review items {len(review_items)} 条。", [run_id]),
            _test_gate("source_sha_preflight", "源码 sha 预检通过", "pass" if ready and not blocked else "warning", "所有待应用项当前源码仍等于 before sha。" if ready and not blocked else "存在未通过审阅或 sha 漂移的应用项。", [f"{item['changed_file']}:{item['current_source_sha256']}" for item in apply_items[:5]]),
            _test_gate("confirmation_tokens_required", "确认 token 已列出", "pass", "真实失败 run 应用必须显式携带 diff 审阅、文件集写入和应用后回放确认。", ["confirm_real_run_diff_review", "confirm_real_run_file_set_write", "confirm_post_apply_replay_required"]),
            _test_gate("get_apply_gate_is_read_only", "GET 应用门只读", "pass", "本接口只生成应用门 material，不批准、不应用、不回滚。", ["real_repo_writes=0"]),
        ],
        "apply_items": apply_items,
        "blocked_items": blocked_items,
        "next_actions": [
            {
                "id": "generate_real_run_apply_preview",
                "title": "生成真实失败 run 显式应用预览",
                "summary": "下一步应在不写真实文件的前提下，把审阅通过项展开为文件集 before/after 预览、确认 token 和应用后回放计划。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-apply-preview/latest",
            }
        ] if ready else [
            {
                "id": "review_real_run_diff_preview",
                "title": "回到 diff 审阅门",
                "summary": "先让 diff 审阅门通过，再进入应用门。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-diff-review/latest",
            }
        ],
        "source": {
            "diff_review_endpoint": "/api/team-builder-materialization/repair-real-run-diff-review/latest",
            "diff_review_material": _safe_text(_dict_value(review.get("source")).get("diff_review_material"), 420),
            "apply_gate_material": str(material_path.relative_to(_repo_root())) if material_path else "",
        },
    }
    if material_path:
        try:
            material_path.parent.mkdir(parents=True, exist_ok=True)
            material_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_repair_real_run_apply_preview_report() -> dict[str, Any]:
    apply_gate = _team_builder_repair_real_run_apply_gate_report()
    run_id = _safe_text(apply_gate.get("run_id"), 160) or "standalone-real-run-apply-preview"
    material_path = _team_builder_repair_real_run_apply_preview_path(run_id)
    preview_root = _repo_root() / "_scratch" / "team_builder_repair_apply_preview" / run_id / "real_run_apply_preview"
    apply_items = [_dict_value(item) for item in _list_value(apply_gate.get("apply_items"))]
    preview_items: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []

    for index, item in enumerate(apply_items):
        item_id = _safe_text(item.get("id"), 160)
        changed_file = _safe_text(item.get("changed_file"), 420).replace("\\", "/")
        safe_item_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", item_id or f"real_run_apply_{index}")[:120].strip("._") or f"real_run_apply_{index}"
        reasons: list[str] = []
        if _safe_text(item.get("status"), 120) != "ready_for_explicit_apply_preview":
            reasons.append("应用门尚未通过。")
        target_path = (_repo_root() / changed_file).resolve() if changed_file else _repo_root().resolve()
        before_preview_file = _safe_text(item.get("before_preview_file"), 520)
        after_preview_file = _safe_text(item.get("after_preview_file"), 520)
        before_preview_path = (_repo_root() / before_preview_file).resolve() if before_preview_file else _repo_root().resolve()
        after_preview_path = (_repo_root() / after_preview_file).resolve() if after_preview_file else _repo_root().resolve()
        try:
            target_path.relative_to(_repo_root().resolve())
        except (OSError, ValueError):
            reasons.append("目标文件逃逸仓库范围。")
        if not changed_file.startswith("_scratch/team_builder_real_material_validation/"):
            reasons.append("目标不在真实 run 的 scratch generated package 内。")
        if not target_path.is_file():
            reasons.append("目标源码不存在。")
        if not before_preview_path.is_file() or not after_preview_path.is_file():
            reasons.append("应用预览缺少上游 before/after 文件。")

        file_records: list[dict[str, Any]] = []
        before_files: list[str] = []
        after_files: list[str] = []
        if not reasons:
            try:
                before_text = target_path.read_text(encoding="utf-8")
                before_preview_text = before_preview_path.read_text(encoding="utf-8")
                after_text = after_preview_path.read_text(encoding="utf-8")
                current_sha = _team_builder_file_sha256(target_path)
                before_sha = _safe_text(item.get("before_sha256"), 96)
                after_sha = _safe_text(item.get("after_sha256"), 96)
                if before_sha and current_sha != before_sha:
                    reasons.append("目标源码当前 sha 已不等于 before，必须重新生成 diff。")
                if before_text != before_preview_text:
                    reasons.append("目标源码内容与 before 预览不一致。")
                actual_after_sha = _team_builder_file_sha256(after_preview_path)
                if after_sha and actual_after_sha != after_sha:
                    reasons.append("after 预览 sha 与应用门记录不一致。")
                if before_text == after_text:
                    reasons.append("before 与 after 没有内容差异。")
                if not reasons:
                    before_out = preview_root / safe_item_id / "before" / changed_file
                    after_out = preview_root / safe_item_id / "after" / changed_file
                    before_out.parent.mkdir(parents=True, exist_ok=True)
                    after_out.parent.mkdir(parents=True, exist_ok=True)
                    before_out.write_text(before_text, encoding="utf-8")
                    after_out.write_text(after_text, encoding="utf-8")
                    before_rel = _team_builder_rel_path(before_out)
                    after_rel = _team_builder_rel_path(after_out)
                    before_files.append(before_rel)
                    after_files.append(after_rel)
                    file_records.append({
                        "changed_file": changed_file,
                        "before_preview_file": before_rel,
                        "after_preview_file": after_rel,
                        "before_sha256": _team_builder_file_sha256(before_out),
                        "after_sha256": _team_builder_file_sha256(after_out),
                        "diff_sha256": _safe_text(item.get("diff_sha256"), 96),
                        "source_current_sha256": current_sha,
                    })
            except OSError as exc:
                reasons.append(f"生成应用预览失败: {exc}")

        status = "preview_ready" if not reasons and file_records else "blocked"
        if reasons:
            blocked_items.append({"apply_item_id": item_id, "changed_file": changed_file, "reasons": reasons})
        preview_items.append({
            "id": f"real_run_apply_preview:{len(preview_items)}",
            "apply_item_id": item_id,
            "worker_id": _safe_text(item.get("worker_id"), 160),
            "status": status,
            "summary": (
                "真实失败 run 文件集应用预览已生成；仍未写目标文件。"
                if status == "preview_ready"
                else "真实失败 run 文件集应用预览被阻断。"
            ),
            "changed_files": [changed_file] if changed_file else [],
            "file_set": True,
            "file_count": len(file_records),
            "before_preview_files": before_files,
            "after_preview_files": after_files,
            "file_records": file_records,
            "required_confirmations": [_safe_text(token, 160) for token in _list_value(item.get("required_confirmations"))],
            "post_apply_verification": [_safe_text(step, 260) for step in _list_value(item.get("post_apply_verification"))],
            "rollback_requirement": _safe_text(item.get("rollback_requirement"), 420),
            "blocked_reasons": reasons,
            "safety": {
                "scope": "scratch_only_preview",
                "writes_real_files": False,
                "requires_final_apply_confirmation": True,
                "reason": "该接口只写 _scratch 应用预览和 material，不修改真实 generated package。"
            },
        })

    preview_ready = sum(1 for item in preview_items if item["status"] == "preview_ready")
    blocked = len(blocked_items)
    files_previewed = sum(len(_list_value(item.get("file_records"))) for item in preview_items)
    verdict = "preview_ready" if preview_ready and not blocked else "blocked" if blocked else "no_apply_gate_ready_item"
    summary = (
        f"真实失败 run 已生成 {preview_ready} 个文件集应用预览；真实目标文件未修改。"
        if verdict == "preview_ready"
        else f"真实失败 run 文件集应用预览发现 {blocked} 个阻断项。"
        if verdict == "blocked"
        else "当前没有通过应用门的真实失败 run 项。"
    )
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(apply_gate.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "apply_items": len(apply_items),
            "preview_ready": preview_ready,
            "files_previewed": files_previewed,
            "blocked": blocked,
            "required_confirmation_tokens": int(_dict_value(apply_gate.get("counts")).get("required_confirmation_tokens") or 0),
            "real_repo_writes": 0,
        },
        "quality_gates": [
            _test_gate("apply_gate_consumed", "已消费显式应用门", "pass" if apply_items else "warning", f"读取 apply items {len(apply_items)} 条。", [run_id]),
            _test_gate("source_sha_rechecked", "源码 sha 已复查", "pass" if preview_ready and not blocked else "warning", "所有预览项当前源码仍等于 before 预览。" if preview_ready and not blocked else "存在源码漂移或预览证据不完整。", [record["changed_file"] for item in preview_items for record in _list_value(item.get("file_records"))][:5]),
            _test_gate("file_set_preview_created", "文件集预览已生成", "pass" if preview_ready and files_previewed else "warning", f"生成 {files_previewed} 个 before/after 文件预览。", [path for item in preview_items for path in _list_value(item.get("after_preview_files"))][:5]),
            _test_gate("real_run_preview_is_read_only", "真实 run 预览只读", "pass", "本接口只写 scratch 应用预览和 material，不批准、不应用、不回滚。", ["real_repo_writes=0"]),
        ],
        "preview_items": preview_items,
        "blocked_items": blocked_items,
        "next_actions": [
            {
                "id": "execute_real_run_apply_with_confirmations",
                "title": "显式应用真实失败 run 修复",
                "summary": "只有在审阅者确认 token、目标 sha 仍匹配且准备好应用后回放时，才能执行真实写入。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-apply-preview/latest",
            }
        ] if preview_ready else [
            {
                "id": "return_to_real_run_apply_gate",
                "title": "回到真实失败 run 应用门",
                "summary": "先让应用门和 sha 复查通过，再生成应用预览。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-apply-gate/latest",
            }
        ],
        "source": {
            "apply_gate_endpoint": "/api/team-builder-materialization/repair-real-run-apply-gate/latest",
            "apply_gate_material": _safe_text(_dict_value(apply_gate.get("source")).get("apply_gate_material"), 420),
            "apply_preview_material": str(material_path.relative_to(_repo_root())) if material_path else "",
            "preview_root": _team_builder_rel_path(preview_root),
        },
    }
    if material_path:
        try:
            material_path.parent.mkdir(parents=True, exist_ok=True)
            material_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_read_real_run_apply_execution_records(run_id: str) -> list[dict[str, Any]]:
    path = _team_builder_repair_real_run_apply_execution_records_path(run_id)
    if path is None or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = _list_value(payload.get("records")) if isinstance(payload, dict) else _list_value(payload)
    return [_dict_value(item) for item in records]


def _team_builder_write_real_run_apply_execution_records(run_id: str, records: list[dict[str, Any]]) -> str:
    path = _team_builder_repair_real_run_apply_execution_records_path(run_id)
    if path is None:
        return ""
    payload = {
        "run_id": _safe_text(run_id, 160),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(_repo_root()))


def _team_builder_real_run_apply_execution_report() -> dict[str, Any]:
    preview = _team_builder_repair_real_run_apply_preview_report()
    run_id = _safe_text(preview.get("run_id"), 160)
    records = _team_builder_read_real_run_apply_execution_records(run_id)
    records_by_apply_item: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        apply_item_id = _safe_text(record.get("apply_item_id"), 160)
        if apply_item_id:
            records_by_apply_item.setdefault(apply_item_id, []).append(record)

    apply_items: list[dict[str, Any]] = []
    seen_apply_item_ids: set[str] = set()
    for index, preview_item in enumerate([_dict_value(item) for item in _list_value(preview.get("preview_items"))]):
        apply_item_id = _safe_text(preview_item.get("apply_item_id"), 160)
        if apply_item_id:
            seen_apply_item_ids.add(apply_item_id)
        latest_record = (records_by_apply_item.get(apply_item_id) or [])[-1] if records_by_apply_item.get(apply_item_id) else {}
        preview_file_records = [_dict_value(item) for item in _list_value(preview_item.get("file_records"))]
        record_file_records = [_dict_value(item) for item in _list_value(latest_record.get("file_records"))] if latest_record else []
        source_records = record_file_records or preview_file_records
        current_file_records: list[dict[str, Any]] = []
        for file_record in source_records:
            changed_file = _safe_text(file_record.get("changed_file"), 420)
            target_path = (_repo_root() / changed_file).resolve() if changed_file else None
            current_file_records.append({
                **file_record,
                "current_sha256": _team_builder_file_sha256(target_path) if target_path is not None else "",
            })
        files_match_after = bool(current_file_records) and all(
            _safe_text(item.get("current_sha256"), 96)
            and _safe_text(item.get("current_sha256"), 96) == _safe_text(item.get("after_sha256"), 96)
            for item in current_file_records
        )
        files_match_before = bool(current_file_records) and all(
            _safe_text(item.get("current_sha256"), 96)
            and _safe_text(item.get("current_sha256"), 96) == _safe_text(item.get("before_sha256"), 96)
            for item in current_file_records
        )
        if latest_record and files_match_after:
            status = "applied"
            summary = "当前真实失败 run 目标文件与显式应用后的 after 内容一致。"
        elif latest_record:
            status = "stale_or_mismatch"
            summary = "存在应用记录，但当前目标文件与记录的 after sha 不一致。"
        elif _safe_text(preview_item.get("status"), 80) == "preview_ready" and files_match_before:
            status = "ready_for_explicit_apply"
            summary = "文件集应用预览已通过，等待 POST execute 显式写入。"
        elif _safe_text(preview_item.get("status"), 80) == "preview_ready":
            status = "stale_or_mismatch"
            summary = "文件集应用预览存在，但当前源码已不等于 before，不能执行。"
        else:
            status = "blocked"
            summary = "文件集应用预览尚未通过，不能执行。"
        apply_items.append({
            "id": f"real_run_apply_execution:{index}",
            "apply_item_id": apply_item_id,
            "worker_id": _safe_text(preview_item.get("worker_id"), 160),
            "status": status,
            "summary": summary,
            "changed_files": [_safe_text(path, 420) for path in _list_value(preview_item.get("changed_files"))],
            "file_set": bool(preview_item.get("file_set")),
            "file_count": len(current_file_records),
            "file_records": current_file_records,
            "required_confirmations": [_safe_text(token, 160) for token in _list_value(preview_item.get("required_confirmations"))],
            "applied_at": _safe_text(latest_record.get("applied_at"), 120),
            "applied_by": _safe_text(latest_record.get("applied_by"), 120),
            "real_writes": int(latest_record.get("real_writes") or 0) if latest_record else 0,
            "blocked_reasons": [_safe_text(reason, 420) for reason in _list_value(preview_item.get("blocked_reasons"))],
        })
    for record_apply_item_id, item_records in records_by_apply_item.items():
        if record_apply_item_id in seen_apply_item_ids:
            continue
        latest_record = item_records[-1]
        record_file_records = [_dict_value(item) for item in _list_value(latest_record.get("file_records"))]
        current_file_records: list[dict[str, Any]] = []
        for file_record in record_file_records:
            changed_file = _safe_text(file_record.get("changed_file"), 420)
            target_path = (_repo_root() / changed_file).resolve() if changed_file else None
            current_file_records.append({
                **file_record,
                "current_sha256": _team_builder_file_sha256(target_path) if target_path is not None else "",
            })
        files_match_after = bool(current_file_records) and all(
            _safe_text(item.get("current_sha256"), 96)
            and _safe_text(item.get("current_sha256"), 96) == _safe_text(item.get("after_sha256"), 96)
            for item in current_file_records
        )
        status = "applied" if files_match_after else "stale_or_mismatch"
        apply_items.append({
            "id": f"real_run_apply_execution:record:{len(apply_items)}",
            "apply_item_id": record_apply_item_id,
            "worker_id": _safe_text(latest_record.get("worker_id"), 160),
            "status": status,
            "summary": "当前预览链路已变化，但执行记录仍可证明目标文件处于 after 状态。" if status == "applied" else "执行记录存在，但当前文件不再匹配 after。",
            "changed_files": [_safe_text(path, 420) for path in _list_value(latest_record.get("changed_files"))],
            "file_set": bool(latest_record.get("file_set")),
            "file_count": len(current_file_records),
            "file_records": current_file_records,
            "required_confirmations": [_safe_text(token, 160) for token in _list_value(latest_record.get("confirmations"))],
            "applied_at": _safe_text(latest_record.get("applied_at"), 120),
            "applied_by": _safe_text(latest_record.get("applied_by"), 120),
            "real_writes": int(latest_record.get("real_writes") or 0),
            "blocked_reasons": [],
        })

    ready = sum(1 for item in apply_items if item["status"] == "ready_for_explicit_apply")
    applied = sum(1 for item in apply_items if item["status"] == "applied")
    blocked = sum(1 for item in apply_items if item["status"] == "blocked")
    stale = sum(1 for item in apply_items if item["status"] == "stale_or_mismatch")
    real_writes = sum(int(item.get("real_writes") or 0) for item in apply_items)
    if not apply_items:
        verdict = "clean"
        summary = "当前没有真实失败 run 应用预览项。"
    elif stale:
        verdict = "stale_or_mismatch"
        summary = f"{stale} 条真实失败 run 应用项与当前文件不匹配。"
    elif blocked:
        verdict = "blocked"
        summary = f"{blocked} 条真实失败 run 应用项尚未通过预览。"
    elif applied:
        verdict = "applied"
        summary = f"{applied} 条真实失败 run 应用项已显式写入。"
    else:
        verdict = "ready_for_explicit_apply"
        summary = f"{ready} 条真实失败 run 应用项等待显式执行。"
    records_path = _team_builder_repair_real_run_apply_execution_records_path(run_id)
    report_path = _team_builder_repair_real_run_apply_execution_report_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(preview.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "items": len(apply_items),
            "ready": ready,
            "applied": applied,
            "blocked": blocked,
            "stale_or_mismatch": stale,
            "real_writes": real_writes,
        },
        "quality_gates": [
            _test_gate("apply_preview_consumed", "已消费文件集应用预览", "pass" if apply_items else "warning", f"读取 preview items {len(apply_items)} 条。", [run_id]),
            _test_gate("explicit_execute_required", "必须显式执行", "pass", "GET 只展示状态；POST execute 需要确认 token、理由、执行人和 before/after sha 匹配。", ["confirm_real_run_file_set_write"]),
            _test_gate("apply_records_match_current", "应用记录匹配当前文件", "pass" if stale == 0 else "fail", "没有发现应用记录或预览与当前文件不匹配。" if stale == 0 else f"{stale} 条记录不匹配。", []),
        ],
        "apply_items": apply_items,
        "records": records,
        "next_actions": [
            {
                "id": "verify_real_run_after_apply",
                "title": "验证真实失败 run 应用后状态",
                "summary": "显式应用后必须重新跑 code review、team test、doctor 和 closure，再进入对账与回滚就绪检查。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/latest",
            }
        ] if applied else [
            {
                "id": "post_real_run_apply_execute",
                "title": "POST 显式应用真实失败 run 修复",
                "summary": "仅当审阅者提供所有确认 token 且当前 sha 匹配时才会写目标文件。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/execute",
            }
        ],
        "source": {
            **(_dict_value(preview.get("source"))),
            "apply_preview_endpoint": "/api/team-builder-materialization/repair-real-run-apply-preview/latest",
            "apply_execution_records_material": str(records_path.relative_to(_repo_root())) if records_path else "",
            "apply_execution_report_material": str(report_path.relative_to(_repo_root())) if report_path else "",
        },
    }
    if report_path:
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_execute_real_run_apply(payload: dict[str, Any]) -> dict[str, Any]:
    preview = _team_builder_repair_real_run_apply_preview_report()
    run_id = _safe_text(preview.get("run_id"), 160)
    apply_item_id = _safe_text(payload.get("apply_item_id"), 160)
    if not apply_item_id:
        raise HTTPException(status_code=400, detail="缺少 apply_item_id。")
    if payload.get("apply") is not True:
        raise HTTPException(status_code=400, detail="必须显式传入 apply=true。")
    applied_by = _safe_text(payload.get("applied_by"), 120)
    reason = _safe_text(payload.get("reason"), 520)
    if not applied_by:
        raise HTTPException(status_code=400, detail="缺少 applied_by。")
    if not reason:
        raise HTTPException(status_code=400, detail="缺少执行理由 reason。")
    confirmations = [_safe_text(item, 200) for item in _list_value(payload.get("confirmations")) if _safe_text(item, 200)]
    for token in ["confirm_real_run_diff_review", "confirm_real_run_file_set_write", "confirm_post_apply_replay_required"]:
        if token not in confirmations:
            raise HTTPException(status_code=400, detail=f"缺少确认 token: {token}。")
    preview_item = next(
        (
            _dict_value(item)
            for item in _list_value(preview.get("preview_items"))
            if _safe_text(_dict_value(item).get("apply_item_id"), 160) == apply_item_id
        ),
        None,
    )
    if preview_item is None:
        raise HTTPException(status_code=404, detail="找不到对应 apply_item 的应用预览。")
    if _safe_text(preview_item.get("status"), 80) != "preview_ready":
        raise HTTPException(status_code=409, detail="该项尚未通过文件集应用预览。")
    file_records = [_dict_value(item) for item in _list_value(preview_item.get("file_records"))]
    if not file_records:
        raise HTTPException(status_code=409, detail="缺少逐文件预览记录。")
    staged: list[dict[str, Any]] = []
    for file_record in file_records:
        changed_file = _safe_text(file_record.get("changed_file"), 420)
        target_path = (_repo_root() / changed_file).resolve()
        try:
            target_path.relative_to(_repo_root().resolve())
        except (OSError, ValueError):
            raise HTTPException(status_code=409, detail="目标文件逃逸仓库范围。")
        if not changed_file.replace("\\", "/").startswith("_scratch/team_builder_real_material_validation/"):
            raise HTTPException(status_code=409, detail="真实 run 应用目标必须位于 scratch generated package。")
        before_preview_file = _safe_text(file_record.get("before_preview_file"), 520)
        after_preview_file = _safe_text(file_record.get("after_preview_file"), 520)
        before_preview_path = (_repo_root() / before_preview_file).resolve()
        after_preview_path = (_repo_root() / after_preview_file).resolve()
        if not before_preview_path.is_file() or not after_preview_path.is_file():
            raise HTTPException(status_code=409, detail="before/after 预览文件不存在。")
        if _team_builder_file_sha256(target_path) != _safe_text(file_record.get("before_sha256"), 96):
            raise HTTPException(status_code=409, detail=f"{changed_file} 当前 sha 不等于 before，不能应用。")
        if _team_builder_file_sha256(after_preview_path) != _safe_text(file_record.get("after_sha256"), 96):
            raise HTTPException(status_code=409, detail=f"{changed_file} after 预览 sha 不匹配。")
        staged.append({
            "changed_file": changed_file,
            "target_path": target_path,
            "before_text": target_path.read_text(encoding="utf-8"),
            "after_text": after_preview_path.read_text(encoding="utf-8"),
            "before_preview_file": before_preview_file,
            "after_preview_file": after_preview_file,
            "before_sha256": _safe_text(file_record.get("before_sha256"), 96),
            "after_sha256": _safe_text(file_record.get("after_sha256"), 96),
            "diff_sha256": _safe_text(file_record.get("diff_sha256"), 96),
        })
    written: list[dict[str, Any]] = []
    try:
        for item in staged:
            item["target_path"].write_text(str(item.get("after_text") or ""), encoding="utf-8")
            if _team_builder_file_sha256(item["target_path"]) != _safe_text(item.get("after_sha256"), 96):
                raise ValueError(f"{item['changed_file']} 写入后 sha 校验失败。")
            written.append(item)
    except Exception as exc:
        for item in written:
            try:
                item["target_path"].write_text(str(item.get("before_text") or ""), encoding="utf-8")
            except OSError:
                pass
        raise HTTPException(status_code=409, detail=f"真实失败 run 应用失败，已尝试恢复: {type(exc).__name__}: {exc}")

    records = _team_builder_read_real_run_apply_execution_records(run_id)
    record_file_records = [
        {
            "changed_file": _safe_text(item.get("changed_file"), 420),
            "before_sha256": _safe_text(item.get("before_sha256"), 96),
            "after_sha256": _safe_text(item.get("after_sha256"), 96),
            "before_preview_file": _safe_text(item.get("before_preview_file"), 520),
            "after_preview_file": _safe_text(item.get("after_preview_file"), 520),
            "diff_sha256": _safe_text(item.get("diff_sha256"), 96),
            "real_writes": 1,
        }
        for item in staged
    ]
    records.append({
        "id": f"real_run_apply_execution:{apply_item_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "run_id": run_id,
        "team_name": _safe_text(preview.get("team_name"), 160),
        "apply_item_id": apply_item_id,
        "worker_id": _safe_text(preview_item.get("worker_id"), 160),
        "applied": True,
        "applied_by": applied_by,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "confirmations": confirmations,
        "changed_files": [_safe_text(item.get("changed_file"), 420) for item in staged],
        "file_set": True,
        "file_count": len(record_file_records),
        "file_records": record_file_records,
        "real_writes": len(record_file_records),
    })
    _team_builder_write_real_run_apply_execution_records(run_id, records)
    return _team_builder_real_run_apply_execution_report()


def _team_builder_real_run_apply_rehearsal_report() -> dict[str, Any]:
    apply_report = _team_builder_real_run_apply_execution_report()
    run_id = _safe_text(apply_report.get("run_id"), 160) or "standalone-real-run-apply-rehearsal"
    material_path = _team_builder_repair_real_run_apply_rehearsal_path(run_id)
    rehearsal_root = _repo_root() / "_scratch" / "team_builder_repair_apply_preview" / run_id / "real_run_apply_rehearsal"
    apply_items = [_dict_value(item) for item in _list_value(apply_report.get("apply_items"))]
    required_fields_by_file = _team_builder_real_run_required_fields_by_file(run_id)
    rehearsal_items: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []
    scratch_writes = 0
    required_field_checks = 0
    missing_required_fields = 0
    files_without_required_contract = 0

    for index, item in enumerate(apply_items):
        apply_item_id = _safe_text(item.get("apply_item_id"), 160)
        if _safe_text(item.get("status"), 120) != "ready_for_explicit_apply":
            continue
        safe_item_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", apply_item_id or f"real_run_apply_{index}")[:120].strip("._") or f"real_run_apply_{index}"
        file_checks: list[dict[str, Any]] = []
        item_reasons: list[str] = []

        for record in [_dict_value(record) for record in _list_value(item.get("file_records"))]:
            changed_file = _safe_text(record.get("changed_file"), 520).replace("\\", "/")
            before_sha = _safe_text(record.get("before_sha256"), 96)
            after_sha = _safe_text(record.get("after_sha256"), 96)
            before_preview_file = _safe_text(record.get("before_preview_file"), 520)
            after_preview_file = _safe_text(record.get("after_preview_file"), 520)
            target_path = (_repo_root() / changed_file).resolve() if changed_file else _repo_root().resolve()
            before_preview_path = (_repo_root() / before_preview_file).resolve() if before_preview_file else _repo_root().resolve()
            after_preview_path = (_repo_root() / after_preview_file).resolve() if after_preview_file else _repo_root().resolve()
            rehearsal_file = rehearsal_root / safe_item_id / changed_file
            reasons: list[str] = []
            required_fields = required_fields_by_file.get(changed_file, [])
            required_fields_replayed: list[str] = []
            missing_fields: list[str] = []
            semantic_check_status = "blocked" if required_fields else "no_required_fields"
            if required_fields:
                required_field_checks += len(required_fields)
            else:
                files_without_required_contract += 1
            try:
                target_path.relative_to(_repo_root().resolve())
            except (OSError, ValueError):
                reasons.append("目标文件逃逸仓库范围。")
            if not changed_file.startswith("_scratch/team_builder_real_material_validation/"):
                reasons.append("目标文件不在真实 run 的 scratch generated package 内。")
            if not target_path.is_file():
                reasons.append("目标文件不存在。")
            if not before_preview_path.is_file() or not after_preview_path.is_file():
                reasons.append("before/after 预览文件不完整。")

            current_sha = _team_builder_file_sha256(target_path)
            if before_sha and current_sha != before_sha:
                reasons.append("当前目标文件 sha 不等于 before sha，演练拒绝继续。")
            if before_sha and _team_builder_file_sha256(before_preview_path) != before_sha:
                reasons.append("before 预览 sha 与记录不一致。")
            if after_sha and _team_builder_file_sha256(after_preview_path) != after_sha:
                reasons.append("after 预览 sha 与记录不一致。")

            applied_matches_after = False
            rollback_matches_before = False
            syntax_ok = False
            rehearsal_rel = _team_builder_rel_path(rehearsal_file)
            if not reasons:
                try:
                    before_text = before_preview_path.read_text(encoding="utf-8")
                    after_text = after_preview_path.read_text(encoding="utf-8")
                    rehearsal_file.parent.mkdir(parents=True, exist_ok=True)
                    rehearsal_file.write_text(before_text, encoding="utf-8")
                    scratch_writes += 1
                    if _team_builder_file_sha256(rehearsal_file) != before_sha:
                        reasons.append("演练 before 副本 sha 不等于 before sha。")
                    rehearsal_file.write_text(after_text, encoding="utf-8")
                    scratch_writes += 1
                    applied_matches_after = bool(after_sha and _team_builder_file_sha256(rehearsal_file) == after_sha)
                    if not applied_matches_after:
                        reasons.append("演练 apply 后 sha 不等于 after sha。")
                    if rehearsal_file.suffix == ".py":
                        try:
                            ast.parse(after_text)
                            syntax_ok = True
                        except SyntaxError as exc:
                            reasons.append(f"after 预览 Python 语法失败: {exc.msg}")
                    else:
                        syntax_ok = True
                    if required_fields:
                        missing_fields = [field for field in required_fields if not _team_builder_source_reads_field(after_text, field)]
                        required_fields_replayed = [field for field in required_fields if field not in missing_fields]
                        semantic_check_status = "pass" if not missing_fields else "fail"
                        if missing_fields:
                            reasons.append(f"after 预览仍未读取必读字段: {', '.join(missing_fields)}。")
                    rehearsal_file.write_text(before_text, encoding="utf-8")
                    scratch_writes += 1
                    rollback_matches_before = bool(before_sha and _team_builder_file_sha256(rehearsal_file) == before_sha)
                    if not rollback_matches_before:
                        reasons.append("演练 rollback 后 sha 不等于 before sha。")
                except OSError as exc:
                    reasons.append(f"演练读写失败: {exc}")

            if reasons:
                item_reasons.extend(reasons)
            missing_required_fields += len(missing_fields)
            file_checks.append({
                "changed_file": changed_file,
                "rehearsal_file": rehearsal_rel,
                "current_sha256": current_sha,
                "before_sha256": before_sha,
                "after_sha256": after_sha,
                "applied_matches_after": applied_matches_after,
                "rollback_matches_before": rollback_matches_before,
                "syntax_ok": syntax_ok,
                "required_fields": required_fields,
                "required_fields_replayed": required_fields_replayed,
                "missing_fields": missing_fields,
                "semantic_check_status": semantic_check_status,
                "blocked_reasons": reasons,
            })

        status = "pass" if file_checks and not item_reasons else "blocked"
        if item_reasons:
            blocked_items.append({"apply_item_id": apply_item_id, "worker_id": _safe_text(item.get("worker_id"), 160), "reasons": item_reasons})
        rehearsal_items.append({
            "id": f"real_run_apply_rehearsal:{len(rehearsal_items)}",
            "apply_item_id": apply_item_id,
            "worker_id": _safe_text(item.get("worker_id"), 160),
            "status": status,
            "summary": (
                "before/after 预览已在独立 scratch 副本中完成 apply 和 rollback 演练。"
                if status == "pass"
                else "应用演练发现阻断项，不能作为放行依据。"
            ),
            "changed_files": [_safe_text(path, 520) for path in _list_value(item.get("changed_files"))],
            "file_count": len(file_checks),
            "file_checks": file_checks,
            "blocked_reasons": item_reasons,
        })

    ready = sum(1 for item in apply_items if _safe_text(item.get("status"), 120) == "ready_for_explicit_apply")
    passed = sum(1 for item in rehearsal_items if item["status"] == "pass")
    blocked = len(blocked_items)
    if not ready:
        verdict = "awaiting_apply_preview"
        summary = "当前没有 ready_for_explicit_apply 项，无法做真实失败 run 应用演练。"
    elif blocked:
        verdict = "blocked"
        summary = f"真实失败 run 应用演练发现 {blocked} 个阻断项；不要批准 POST apply。"
    elif passed == ready:
        verdict = "pass"
        summary = f"{passed} 个待应用项已在独立 scratch 副本中完成 apply 与 rollback 演练；真实目标文件未修改。"
    else:
        verdict = "warning"
        summary = f"真实失败 run 应用演练只覆盖 {passed}/{ready} 个待应用项。"

    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(apply_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "ready": ready,
            "passed": passed,
            "blocked": blocked,
            "scratch_writes": scratch_writes,
            "real_repo_writes": 0,
            "required_field_checks": required_field_checks,
            "missing_required_fields": missing_required_fields,
            "files_without_required_contract": files_without_required_contract,
        },
        "quality_gates": [
            _test_gate("ready_items_consumed", "已消费待应用项", "pass" if ready else "warning", f"读取 ready_for_explicit_apply {ready} 项。", [run_id]),
            _test_gate("rehearsal_apply_matches_after", "演练 apply 匹配 after", "pass" if ready and passed == ready and not blocked else "warning", "所有待应用项在副本中应用后 sha 等于 after sha。" if passed == ready and ready else "仍有待应用项未通过 after 演练。", []),
            _test_gate("rehearsal_rollback_matches_before", "演练 rollback 匹配 before", "pass" if ready and passed == ready and not blocked else "warning", "所有待应用项在副本中回滚后 sha 等于 before sha。" if passed == ready and ready else "仍有待应用项未通过 before 回滚演练。", []),
            _test_gate("rehearsal_required_fields_replayed", "演练后必读字段已回放", "pass" if ready and passed == ready and required_field_checks and missing_required_fields == 0 else "warning" if not required_field_checks else "fail" if missing_required_fields else "warning", f"检查 required 字段 {required_field_checks} 个，缺失 {missing_required_fields} 个；无字段契约文件 {files_without_required_contract} 个。", [f"{_safe_text(_dict_value(check).get('changed_file'), 520)} missing={','.join(_safe_text(field, 120) for field in _list_value(_dict_value(check).get('missing_fields')))}" for item in rehearsal_items for check in _list_value(_dict_value(item).get("file_checks")) if _list_value(_dict_value(check).get("missing_fields"))][:5]),
            _test_gate("rehearsal_is_read_only", "演练不写真实目标", "pass", "本接口只写独立 scratch 演练副本和 material，不执行真实 apply/rollback。", ["real_repo_writes=0"]),
        ],
        "rehearsal_items": rehearsal_items,
        "blocked_items": blocked_items,
        "next_actions": [
            {
                "id": "review_real_run_apply_decision",
                "title": "审阅真实失败 run 显式应用决策",
                "summary": "演练通过也不能替代显式审批；如决定继续，仍需 POST apply 和确认 token。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-closure-rollup/latest",
            }
        ],
        "source": {
            "apply_execution_endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/latest",
            "apply_rehearsal_material": str(material_path.relative_to(_repo_root())) if material_path else "",
            "rehearsal_root": _team_builder_rel_path(rehearsal_root),
        },
    }
    if material_path:
        try:
            material_path.parent.mkdir(parents=True, exist_ok=True)
            material_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_real_run_build_auto_apply_policy(
    *,
    run_id: str,
    team_name: str,
    approval_items: list[dict[str, Any]],
    post_preflight: dict[str, Any],
    apply_rehearsal: dict[str, Any],
    ready_to_apply: int,
    applied: int,
) -> dict[str, Any]:
    max_apply_items = 1
    max_changed_files = 3
    required_apply_tokens = {
        "confirm_real_run_diff_review",
        "confirm_real_run_file_set_write",
        "confirm_post_apply_replay_required",
    }
    if applied and ready_to_apply <= 0 and not approval_items:
        return {
            "available": True,
            "run_id": _safe_text(run_id, 160),
            "team_name": _safe_text(team_name, 160),
            "verdict": "already_applied",
            "eligible": False,
            "summary": "已有真实 apply 记录，自动 apply 策略不重复写入；后续只需要查看验证、对账或回滚就绪状态。",
            "counts": {
                "candidate_items": 0,
                "eligible_items": 0,
                "blocked_items": 0,
                "total_changed_files": 0,
                "max_apply_items": max_apply_items,
                "max_changed_files": max_changed_files,
                "required_field_checks": 0,
                "missing_required_fields": 0,
                "real_repo_writes": 0,
            },
            "quality_gates": [
                _test_gate("auto_apply_not_repeated", "不重复自动写入", "pass", f"applied={applied}", []),
                _test_gate("auto_apply_already_closed_for_apply", "自动 apply 阶段已结束", "pass", "当前没有 ready_for_explicit_apply 项，策略不会再次写入。", []),
            ],
            "policy": {
                "version": "2026-05-18.low-risk-generated-package.v1",
                "write_scope": "_scratch/team_builder_real_material_validation/.../code_package_files",
                "max_apply_items": max_apply_items,
                "max_changed_files": max_changed_files,
                "requires_semantic_rehearsal": True,
                "requires_rollback_snapshot": True,
                "after_apply_actions": ["post_apply_verification", "outcome_reconciliation", "rollback_readiness"],
                "auto_rollback": False,
            },
            "candidate_items": [],
            "blockers": ["已有真实 apply 记录；自动 apply 不会重复写入。"],
            "warnings": [],
            "execute_endpoint": "/api/team-builder-materialization/repair-real-run-auto-apply-execution/execute",
            "required_confirmation": "confirm_team_builder_low_risk_auto_apply",
        }
    blockers: list[str] = []
    warnings: list[str] = []
    candidate_items: list[dict[str, Any]] = []
    total_changed_files = 0

    if applied:
        blockers.append("已有真实 apply 记录；自动 apply 不会重复写入。")
    if ready_to_apply <= 0 or not approval_items:
        blockers.append("当前没有 ready_for_explicit_apply 项。")
    if len(approval_items) > max_apply_items:
        blockers.append(f"当前有 {len(approval_items)} 个待应用项；v1 自动 apply 只允许 1 个。")
    if _safe_text(post_preflight.get("status"), 80) != "ready_to_post":
        blockers.append("POST 前置检查未全部通过。")
    for blocker in _list_value(post_preflight.get("blockers")):
        blocker_text = _safe_text(blocker, 420)
        if blocker_text:
            blockers.append(blocker_text)

    condition_status = {
        _safe_text(_dict_value(condition).get("id"), 120): _safe_text(_dict_value(condition).get("status"), 80)
        for condition in _list_value(post_preflight.get("conditions"))
    }
    for condition_id in [
        "target_scope_safe",
        "current_matches_before",
        "after_preview_verified",
        "rollback_snapshot_verified",
        "required_confirmations_declared",
        "apply_rehearsal_passed",
        "semantic_rehearsal_passed",
    ]:
        if condition_status.get(condition_id) != "pass":
            blockers.append(f"前置条件未通过: {condition_id}")

    rehearsal_counts = _dict_value(apply_rehearsal.get("counts"))
    rehearsal_required_fields = int(rehearsal_counts.get("required_field_checks") or 0)
    rehearsal_missing_fields = int(rehearsal_counts.get("missing_required_fields") or 0)
    rehearsal_real_writes = int(rehearsal_counts.get("real_repo_writes") or 0)
    if _safe_text(apply_rehearsal.get("verdict"), 80) != "pass":
        blockers.append("应用前演练未通过。")
    if rehearsal_real_writes:
        blockers.append("应用前演练出现真实写入，不允许自动 apply。")
    if rehearsal_required_fields <= 0:
        blockers.append("应用前演练没有证明 required 字段读取。")
    if rehearsal_missing_fields:
        blockers.append(f"应用前演练仍缺失 required 字段 {rehearsal_missing_fields} 个。")

    for item in approval_items:
        apply_item_id = _safe_text(item.get("apply_item_id"), 160)
        changed_files = [_safe_text(path, 520).replace("\\", "/") for path in _list_value(item.get("changed_files")) if _safe_text(path, 520)]
        file_count = int(item.get("file_count") or len(changed_files) or 0)
        total_changed_files += file_count
        item_blockers: list[str] = []
        if _safe_text(item.get("status"), 120) != "ready_for_explicit_apply":
            item_blockers.append("状态不是 ready_for_explicit_apply。")
        if file_count <= 0:
            item_blockers.append("没有可写文件。")
        if file_count > max_changed_files:
            item_blockers.append(f"文件数 {file_count} 超过自动 apply 上限 {max_changed_files}。")
        declared_tokens = {_safe_text(token, 160) for token in _list_value(item.get("required_confirmations"))}
        if not required_apply_tokens.issubset(declared_tokens):
            item_blockers.append("待应用项未声明完整 apply confirmation token。")
        for changed_file in changed_files:
            if not changed_file.startswith("_scratch/team_builder_real_material_validation/"):
                item_blockers.append(f"目标越界: {changed_file}")
            if "/code_package_files/" not in changed_file:
                item_blockers.append(f"目标不是 generated package 文件: {changed_file}")
        if item_blockers:
            blockers.extend(f"{apply_item_id}: {reason}" for reason in item_blockers)
        candidate_items.append({
            "apply_item_id": apply_item_id,
            "worker_id": _safe_text(item.get("worker_id"), 160),
            "status": "eligible" if not item_blockers else "blocked",
            "file_count": file_count,
            "changed_files": changed_files,
            "required_input_fields": [_safe_text(field, 120) for field in _list_value(item.get("required_input_fields"))],
            "blockers": item_blockers,
        })

    if total_changed_files > max_changed_files:
        blockers.append(f"总文件数 {total_changed_files} 超过自动 apply 上限 {max_changed_files}。")
    if total_changed_files == 0 and approval_items:
        blockers.append("待应用项没有文件集记录。")
    if not blockers and rehearsal_required_fields <= total_changed_files - len(approval_items):
        warnings.append("required 字段检查数量偏少；当前仍允许自动 apply，但后续应扩大语义检查口径。")

    eligible = not blockers
    verdict = (
        "eligible"
        if eligible
        else "already_applied"
        if applied and ready_to_apply == 0
        else "not_ready"
        if not approval_items
        else "blocked"
    )
    summary = (
        f"{len(candidate_items)} 个真实失败 run 修复项满足低风险自动 apply 策略；执行后会自动做应用后验证和结果对账。"
        if eligible else
        "已有真实 apply 记录，自动 apply 策略不重复执行。"
        if verdict == "already_applied" else
        "当前没有可自动 apply 的真实失败 run 修复项。"
        if verdict == "not_ready" else
        f"低风险自动 apply 策略发现 {len(blockers)} 个阻断项。"
    )
    return {
        "available": True,
        "run_id": _safe_text(run_id, 160),
        "team_name": _safe_text(team_name, 160),
        "verdict": verdict,
        "eligible": eligible,
        "summary": summary,
        "counts": {
            "candidate_items": len(candidate_items),
            "eligible_items": sum(1 for item in candidate_items if item["status"] == "eligible"),
            "blocked_items": sum(1 for item in candidate_items if item["status"] == "blocked"),
            "total_changed_files": total_changed_files,
            "max_apply_items": max_apply_items,
            "max_changed_files": max_changed_files,
            "required_field_checks": rehearsal_required_fields,
            "missing_required_fields": rehearsal_missing_fields,
            "real_repo_writes": 0,
        },
        "quality_gates": [
            _test_gate("auto_apply_preflight_ready", "自动 apply 前置检查通过", "pass" if condition_status and _safe_text(post_preflight.get("status"), 80) == "ready_to_post" else "warning", _safe_text(post_preflight.get("summary"), 520), []),
            _test_gate("auto_apply_scope_low_risk", "自动 apply 范围低风险", "pass" if candidate_items and total_changed_files <= max_changed_files and not [item for item in candidate_items if item["blockers"]] else "warning", "目标必须限制在 scratch generated package，且文件数不超过自动 apply 上限。", [path for item in candidate_items for path in item["changed_files"]][:5]),
            _test_gate("auto_apply_semantic_rehearsal_ready", "语义演练已通过", "pass" if rehearsal_required_fields and rehearsal_missing_fields == 0 else "fail", f"required 字段检查 {rehearsal_required_fields} 个，缺失 {rehearsal_missing_fields} 个。", []),
            _test_gate("auto_apply_rollback_snapshot_ready", "回滚快照可用", "pass" if condition_status.get("rollback_snapshot_verified") == "pass" else "fail", "自动 apply 前必须能校验 before 快照。", []),
            _test_gate("auto_apply_not_repeated", "不重复自动写入", "pass" if not applied else "fail", f"applied={applied}", []),
        ],
        "policy": {
            "version": "2026-05-18.low-risk-generated-package.v1",
            "write_scope": "_scratch/team_builder_real_material_validation/.../code_package_files",
            "max_apply_items": max_apply_items,
            "max_changed_files": max_changed_files,
            "requires_semantic_rehearsal": True,
            "requires_rollback_snapshot": True,
            "after_apply_actions": ["post_apply_verification", "outcome_reconciliation", "rollback_readiness"],
            "auto_rollback": False,
        },
        "candidate_items": candidate_items,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": warnings,
        "execute_endpoint": "/api/team-builder-materialization/repair-real-run-auto-apply-execution/execute",
        "required_confirmation": "confirm_team_builder_low_risk_auto_apply",
    }


def _team_builder_real_run_auto_apply_policy_report() -> dict[str, Any]:
    closure = _team_builder_real_run_closure_rollup_report()
    run_id = _safe_text(closure.get("run_id"), 160) or "standalone-real-run-auto-apply"
    approval_packet = _dict_value(closure.get("approval_packet"))
    policy = _team_builder_real_run_build_auto_apply_policy(
        run_id=run_id,
        team_name=_safe_text(closure.get("team_name"), 160),
        approval_items=[_dict_value(item) for item in _list_value(approval_packet.get("items"))],
        post_preflight=_dict_value(approval_packet.get("post_preflight")),
        apply_rehearsal=_dict_value(approval_packet.get("apply_rehearsal")),
        ready_to_apply=int(_dict_value(closure.get("counts")).get("ready_to_apply") or 0),
        applied=int(_dict_value(closure.get("counts")).get("applied") or 0),
    )
    path = _team_builder_repair_real_run_auto_apply_policy_path(run_id)
    policy["source"] = {
        "closure_endpoint": "/api/team-builder-materialization/repair-real-run-closure-rollup/latest",
        "auto_apply_policy_material": str(path.relative_to(_repo_root())) if path else "",
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return policy


def _team_builder_execute_real_run_auto_apply(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("auto_apply") is not True:
        raise HTTPException(status_code=400, detail="必须显式传入 auto_apply=true。")
    executed_by = _safe_text(payload.get("executed_by"), 120)
    reason = _safe_text(payload.get("reason"), 520)
    if not executed_by:
        raise HTTPException(status_code=400, detail="缺少 executed_by。")
    if not reason:
        raise HTTPException(status_code=400, detail="缺少自动 apply 理由 reason。")
    confirmations = [_safe_text(item, 220) for item in _list_value(payload.get("confirmations")) if _safe_text(item, 220)]
    if "confirm_team_builder_low_risk_auto_apply" not in confirmations:
        raise HTTPException(status_code=400, detail="缺少确认 token: confirm_team_builder_low_risk_auto_apply。")

    policy = _team_builder_real_run_auto_apply_policy_report()
    if policy.get("eligible") is not True:
        raise HTTPException(status_code=409, detail={
            "message": "当前真实失败 run 不满足低风险自动 apply 策略。",
            "verdict": _safe_text(policy.get("verdict"), 80),
            "blockers": _list_value(policy.get("blockers")),
        })
    candidates = [_dict_value(item) for item in _list_value(policy.get("candidate_items")) if _safe_text(_dict_value(item).get("status"), 80) == "eligible"]
    if len(candidates) != 1:
        raise HTTPException(status_code=409, detail="v1 自动 apply 只允许一个 eligible apply item。")
    apply_item_id = _safe_text(candidates[0].get("apply_item_id"), 160)
    apply_report = _team_builder_execute_real_run_apply({
        "apply": True,
        "apply_item_id": apply_item_id,
        "applied_by": executed_by,
        "reason": f"低风险自动 apply: {reason}",
        "confirmations": [
            "confirm_real_run_diff_review",
            "confirm_real_run_file_set_write",
            "confirm_post_apply_replay_required",
        ],
    })
    verification = _team_builder_execute_real_run_post_apply_verification({
        "verify": True,
        "verified_by": executed_by,
        "reason": f"低风险自动 apply 后验证: {reason}",
        "confirmations": ["confirm_real_run_post_apply_replay"],
    })
    outcome = _team_builder_real_run_outcome_reconciliation_report()
    rollback_readiness = _team_builder_real_run_rollback_readiness_report()

    verification_verdict = _safe_text(verification.get("verdict"), 80)
    outcome_verdict = _safe_text(outcome.get("verdict"), 80)
    verification_failed = int(_dict_value(verification.get("counts")).get("failed") or 0)
    introduced_findings = int(_dict_value(outcome.get("counts")).get("introduced_findings") or 0)
    persistent_findings = int(_dict_value(outcome.get("counts")).get("persistent_findings") or 0)
    warning_count = int(_dict_value(verification.get("counts")).get("warnings") or 0) + int(_dict_value(outcome.get("counts")).get("warnings") or 0)
    needs_review = bool(verification_failed or introduced_findings or persistent_findings)
    verdict = "needs_review" if needs_review else "auto_applied_with_warnings" if warning_count else "auto_applied"
    run_id = _safe_text(policy.get("run_id"), 160)
    path = _team_builder_repair_real_run_auto_apply_execution_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(policy.get("team_name"), 160),
        "verdict": verdict,
        "summary": (
            "低风险自动 apply 已执行，但应用后验证或对账发现需要人工复核的问题。"
            if verdict == "needs_review" else
            "低风险自动 apply 已执行，核心验证和对账通过，但仍有非阻断警告。"
            if verdict == "auto_applied_with_warnings" else
            "低风险自动 apply 已执行，应用后验证和对账通过。"
        ),
        "executed_by": executed_by,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "counts": {
            "applied": int(_dict_value(apply_report.get("counts")).get("applied") or 0),
            "real_writes": int(_dict_value(apply_report.get("counts")).get("real_writes") or 0),
            "verified": int(_dict_value(verification.get("counts")).get("verified") or 0),
            "verification_failed": verification_failed,
            "resolved_findings": int(_dict_value(outcome.get("counts")).get("resolved_findings") or 0),
            "introduced_findings": introduced_findings,
            "persistent_findings": persistent_findings,
            "rollback_ready": int(_dict_value(rollback_readiness.get("counts")).get("rollback_ready") or 0),
            "warnings": warning_count,
        },
        "quality_gates": [
            _test_gate("auto_apply_policy_eligible", "自动 apply 策略已放行", "pass", _safe_text(policy.get("summary"), 520), []),
            _test_gate("auto_apply_execution_applied", "自动 apply 已写入", "pass" if _safe_text(apply_report.get("verdict"), 80) == "applied" else "fail", _safe_text(apply_report.get("summary"), 520), [f"apply_item_id={apply_item_id}"]),
            _test_gate("auto_apply_post_verify", "自动 apply 后验证已执行", "pass" if verification_failed == 0 else "fail", _safe_text(verification.get("summary"), 520), [verification_verdict]),
            _test_gate("auto_apply_outcome_reconciled", "自动 apply 结果已对账", "pass" if not introduced_findings and not persistent_findings else "fail", _safe_text(outcome.get("summary"), 520), [outcome_verdict]),
            _test_gate("auto_apply_rollback_ready", "自动 apply 后回滚可用", "pass" if int(_dict_value(rollback_readiness.get("counts")).get("rollback_ready") or 0) else "warning", _safe_text(rollback_readiness.get("summary"), 520), []),
            _test_gate("auto_apply_does_not_auto_rollback", "自动 apply 不自动回滚", "pass", "修复成功后只检查回滚就绪，不撤回已修复内容。", []),
        ],
        "policy": policy,
        "apply_report": {
            "verdict": _safe_text(apply_report.get("verdict"), 80),
            "counts": _dict_value(apply_report.get("counts")),
            "source": _dict_value(apply_report.get("source")),
        },
        "post_apply_verification": {
            "verdict": verification_verdict,
            "counts": _dict_value(verification.get("counts")),
            "source": _dict_value(verification.get("source")),
        },
        "outcome_reconciliation": {
            "verdict": outcome_verdict,
            "counts": _dict_value(outcome.get("counts")),
            "source": _dict_value(outcome.get("source")),
        },
        "rollback_readiness": {
            "verdict": _safe_text(rollback_readiness.get("verdict"), 80),
            "counts": _dict_value(rollback_readiness.get("counts")),
            "source": _dict_value(rollback_readiness.get("source")),
        },
        "source": {
            "auto_apply_policy_endpoint": "/api/team-builder-materialization/repair-real-run-auto-apply-policy/latest",
            "auto_apply_execution_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_real_run_required_fields_by_file(run_id: str) -> dict[str, list[str]]:
    replay_path = _team_builder_repair_real_run_replay_plan_path(run_id)
    replay = _read_json_file(replay_path) if replay_path else {}
    if not replay.get("available"):
        replay = _team_builder_repair_real_run_replay_plan_report()
    by_file: dict[str, list[str]] = {}
    for action in [_dict_value(item) for item in _list_value(replay.get("repair_actions"))]:
        required_fields = [_safe_text(item, 120) for item in _list_value(action.get("required_input_fields")) if _safe_text(item, 120)]
        if not required_fields:
            continue
        for changed_file in [_safe_text(item, 420) for item in _list_value(action.get("changed_files")) if _safe_text(item, 420)]:
            key = changed_file.replace("\\", "/")
            by_file.setdefault(key, [])
            for field in required_fields:
                if field not in by_file[key]:
                    by_file[key].append(field)
    return by_file


def _team_builder_real_run_post_apply_verification_report() -> dict[str, Any]:
    apply_report = _team_builder_real_run_apply_execution_report()
    run_id = _safe_text(apply_report.get("run_id"), 160)
    path = _team_builder_repair_real_run_post_apply_verification_path(run_id)
    if not apply_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(apply_report.get("reason"), 500),
            "run_id": run_id,
            "team_name": _safe_text(apply_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无真实失败 run 显式应用记录，无法检查应用后回放验证。",
            "counts": {"applied": 0, "verified": 0, "pending": 0, "failed": 0, "warnings": 0, "ready": 0, "real_repo_writes": 0},
            "quality_gates": [],
            "verification_items": [],
            "next_actions": [],
            "source": _dict_value(apply_report.get("source")),
        }
    applied_items = [
        _dict_value(item)
        for item in _list_value(apply_report.get("apply_items"))
        if _safe_text(_dict_value(item).get("status"), 80) == "applied"
    ]
    ready = int(_dict_value(apply_report.get("counts")).get("ready") or 0)
    existing = _read_json_file(path) if path else {}
    applied_ids = {_safe_text(item.get("apply_item_id"), 160) for item in applied_items if _safe_text(item.get("apply_item_id"), 160)}
    verified_ids = {
        _safe_text(item.get("apply_item_id"), 160)
        for item in _list_value(existing.get("applied_records"))
        if _safe_text(_dict_value(item).get("apply_item_id"), 160)
    }
    if applied_items and existing.get("available") and applied_ids and applied_ids.issubset(verified_ids):
        return existing

    if not applied_items:
        verdict = "awaiting_apply" if ready else "clean"
        summary = (
            f"{ready} 条真实失败 run 修复已准备显式应用；应用后回放验证等待 POST apply 执行。"
            if ready else "当前没有已应用的真实失败 run 修复；无需执行应用后回放验证。"
        )
        pending = 0
        verification_items: list[dict[str, Any]] = []
    else:
        verdict = "awaiting_replay_verification"
        summary = f"{len(applied_items)} 条真实失败 run 修复已应用，等待应用后回放验证。"
        pending = len(applied_items)
        verification_items = [
            {
                "id": f"real_run_post_apply_verification:{index}",
                "apply_item_id": _safe_text(item.get("apply_item_id"), 160),
                "worker_id": _safe_text(item.get("worker_id"), 160),
                "status": "pending_verification",
                "summary": "目标文件已处于 after 状态，但尚未重新检查字段读取、包结构和 worker smoke。",
                "changed_files": [_safe_text(file, 420) for file in _list_value(item.get("changed_files"))],
                "required_commands": [
                    "POST /api/team-builder-materialization/repair-real-run-post-apply-verification/execute",
                    "GET /api/team-builder-materialization/repair-real-run-apply-execution/latest",
                ],
            }
            for index, item in enumerate(applied_items)
        ]
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(apply_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "applied": len(applied_items),
            "verified": 0,
            "pending": pending,
            "failed": 0,
            "warnings": 0,
            "ready": ready,
            "real_repo_writes": 0,
        },
        "quality_gates": [
            _test_gate("real_run_apply_required", "先完成显式应用", "pass" if applied_items or not ready else "warning", "应用后验证只消费已应用记录；不会主动写入目标文件。", [f"ready={ready}", f"applied={len(applied_items)}"]),
            _test_gate("post_apply_replay_is_explicit", "回放验证必须显式触发", "pass", "GET 只展示状态；POST execute 需要确认 token、执行人和理由。", ["confirm_real_run_post_apply_replay"]),
        ],
        "verification_items": verification_items,
        "next_actions": [
            {
                "id": "execute_real_run_post_apply_replay",
                "title": "执行真实失败 run 应用后回放验证",
                "summary": "验证会复查目标文件 sha、required 字段读取、generated package 导入和 worker smoke；仍不改目标文件。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-post-apply-verification/execute",
            }
        ] if applied_items else [
            {
                "id": "post_real_run_apply_execute",
                "title": "先显式应用真实失败 run 修复",
                "summary": "尚无已应用记录时，不能做应用后回放验证。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/execute",
            }
        ],
        "source": {
            **(_dict_value(apply_report.get("source"))),
            "apply_execution_endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/latest",
            "post_apply_verification_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_execute_real_run_post_apply_verification(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("verify") is not True:
        raise HTTPException(status_code=400, detail="必须显式传入 verify=true。")
    verified_by = _safe_text(payload.get("verified_by"), 120)
    reason = _safe_text(payload.get("reason"), 520)
    if not verified_by:
        raise HTTPException(status_code=400, detail="缺少 verified_by。")
    if not reason:
        raise HTTPException(status_code=400, detail="缺少验证理由 reason。")
    confirmations = [_safe_text(item, 220) for item in _list_value(payload.get("confirmations")) if _safe_text(item, 220)]
    if "confirm_real_run_post_apply_replay" not in confirmations:
        raise HTTPException(status_code=400, detail="缺少确认 token: confirm_real_run_post_apply_replay。")

    apply_report = _team_builder_real_run_apply_execution_report()
    if not apply_report.get("available"):
        raise HTTPException(status_code=409, detail="暂无真实失败 run 应用执行报告，不能执行应用后验证。")
    applied_items = [
        _dict_value(item)
        for item in _list_value(apply_report.get("apply_items"))
        if _safe_text(_dict_value(item).get("status"), 80) == "applied"
    ]
    if not applied_items:
        return _team_builder_real_run_post_apply_verification_report()

    run_id = _safe_text(apply_report.get("run_id"), 160)
    run_dir = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id
    code_root = run_dir / "code_package_files"
    code_package = _read_json_file(run_dir / "materials" / "code_package.json")
    package_name = _safe_text(code_package.get("team_name"), 120) or _safe_text(apply_report.get("team_name"), 120) or "generated_team"
    required_fields_by_file = _team_builder_real_run_required_fields_by_file(run_id)

    verification_items: list[dict[str, Any]] = []
    field_checks: list[dict[str, Any]] = []
    file_state_failed = 0
    missing_required_fields = 0
    fields_checked = 0
    fields_without_contract = 0
    for index, item in enumerate(applied_items):
        item_missing: list[str] = []
        item_checked = 0
        file_records = [_dict_value(record) for record in _list_value(item.get("file_records"))]
        for record in file_records:
            changed_file = _safe_text(record.get("changed_file"), 420)
            normalized = changed_file.replace("\\", "/")
            current_sha = _safe_text(record.get("current_sha256"), 96)
            after_sha = _safe_text(record.get("after_sha256"), 96)
            if not current_sha or current_sha != after_sha:
                file_state_failed += 1
            fields = required_fields_by_file.get(normalized, [])
            if not fields:
                fields_without_contract += 1
            target_path = (_repo_root() / changed_file).resolve() if changed_file else None
            try:
                source_text = target_path.read_text(encoding="utf-8") if target_path and target_path.is_file() else ""
            except OSError:
                source_text = ""
            missing_for_file = [field for field in fields if not _team_builder_source_reads_field(source_text, field)]
            item_missing.extend(missing_for_file)
            item_checked += len(fields)
            fields_checked += len(fields)
            field_checks.append({
                "changed_file": changed_file,
                "required_fields": fields,
                "missing_fields": missing_for_file,
                "current_sha256": current_sha,
                "after_sha256": after_sha,
                "status": "pass" if fields and not missing_for_file else "warning" if not fields else "fail",
            })
        missing_required_fields += len(item_missing)
        verification_items.append({
            "id": f"real_run_post_apply_verification:{index}",
            "apply_item_id": _safe_text(item.get("apply_item_id"), 160),
            "worker_id": _safe_text(item.get("worker_id"), 160),
            "status": "verified" if not item_missing else "failed",
            "summary": (
                f"已复查 {item_checked} 个 required 字段读取，当前文件与 after sha 一致。"
                if not item_missing else f"仍有 required 字段未读: {', '.join(item_missing)}"
            ),
            "changed_files": [_safe_text(file, 420) for file in _list_value(item.get("changed_files"))],
            "required_fields_checked": item_checked,
            "missing_required_fields": item_missing,
        })

    syntax_failures: list[dict[str, str]] = []
    py_files = sorted(code_root.rglob("*.py")) if code_root.is_dir() else []
    for py_file in py_files:
        try:
            compile(py_file.read_text(encoding="utf-8", errors="ignore"), str(py_file), "exec")
        except SyntaxError as exc:
            syntax_failures.append({"file": _team_builder_rel_path(py_file), "error": f"{exc.__class__.__name__}: {exc.msg} at line {exc.lineno}"})
        except OSError as exc:
            syntax_failures.append({"file": _team_builder_rel_path(py_file), "error": f"{exc.__class__.__name__}: {exc}"})

    required_files = ["formats.py", "team.py", "run.py", "__init__.py", "workers/__init__.py"]
    files = sorted(path.relative_to(code_root).as_posix() for path in code_root.rglob("*") if path.is_file() and not _is_skipped(path)) if code_root.is_dir() else []
    missing_package_files = [name for name in required_files if name not in files]
    package_smoke: dict[str, Any] = {"returncode": None, "result": {"ok": False, "error": "缺少完整 generated package 文件，未执行导入 smoke。"}}
    worker_smoke: dict[str, Any] = {"returncode": None, "result": {"status": "warning", "error": "未执行 worker smoke。"}}
    if code_root.is_dir() and not syntax_failures and not missing_package_files:
        try:
            package_dir = _copy_generated_package_for_test(code_root, package_name, f"{run_id}-real-run-post-apply")
            package_smoke = _run_generated_package_smoke(package_dir, package_name)
            package_payload = _dict_value(package_smoke.get("result"))
            if package_smoke.get("returncode") == 0 and package_payload.get("ok"):
                worker_smoke = _run_generated_worker_run_smoke(package_dir, package_name)
        except Exception as exc:
            package_smoke = {"returncode": -1, "stderr": _safe_text(f"{type(exc).__name__}: {exc}", 1200), "result": {"ok": False, "error": f"{type(exc).__name__}: {exc}"}}

    package_payload = _dict_value(package_smoke.get("result"))
    worker_payload = _dict_value(worker_smoke.get("result"))
    package_ok = package_smoke.get("returncode") == 0 and bool(package_payload.get("ok"))
    worker_status = _safe_text(worker_payload.get("status"), 40) or "warning"
    hard_failed = bool(file_state_failed or missing_required_fields or syntax_failures or (not package_ok and not missing_package_files))
    warnings = 0
    if fields_without_contract:
        warnings += fields_without_contract
    if missing_package_files:
        warnings += 1
    if worker_status == "warning":
        warnings += 1
    if worker_status == "fail":
        hard_failed = True
    verdict = "fail" if hard_failed else "warning" if warnings else "pass"
    path = _team_builder_repair_real_run_post_apply_verification_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(apply_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": (
            "真实失败 run 应用后回放验证通过：目标文件处于 after 状态，required 字段读取已补齐，generated package 可导入。"
            if verdict == "pass" else
            "真实失败 run 应用后核心回放通过，但仍有非阻断警告需要继续收敛。"
            if verdict == "warning" else
            "真实失败 run 应用后回放验证失败，需要进入对账或回滚检查。"
        ),
        "verified_by": verified_by,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "counts": {
            "applied": len(applied_items),
            "verified": len(applied_items) if verdict in {"pass", "warning"} else 0,
            "pending": 0,
            "failed": len(applied_items) if verdict == "fail" else 0,
            "warnings": warnings,
            "ready": 0,
            "real_repo_writes": 0,
            "fields_checked": fields_checked,
            "missing_required_fields": missing_required_fields,
            "syntax_failures": len(syntax_failures),
        },
        "quality_gates": [
            _test_gate("real_run_file_state_after_apply", "目标文件保持 after 状态", "pass" if file_state_failed == 0 else "fail", "逐文件 current sha 必须等于 after sha。", [f"mismatch={file_state_failed}"]),
            _test_gate("required_fields_replayed", "required 字段读取已回放", "pass" if fields_checked and missing_required_fields == 0 else "warning" if not fields_checked else "fail", f"检查 required 字段 {fields_checked} 个，缺失 {missing_required_fields} 个。", [f"{item['changed_file']} missing={','.join(item['missing_fields'])}" for item in field_checks if item.get("missing_fields")][:5]),
            _test_gate("generated_package_syntax_after_apply", "应用后 Python 语法通过", "pass" if not syntax_failures else "fail", f"检查 {len(py_files)} 个 Python 文件。", [f"{item['file']}: {item['error']}" for item in syntax_failures[:5]]),
            _test_gate("generated_package_import_after_apply", "应用后 generated package 可导入", "pass" if package_ok else "warning" if missing_package_files else "fail", "完整 generated package 可导入并 build_team/build_bindings。" if package_ok else f"缺少包文件或导入失败: {', '.join(missing_package_files) or package_payload.get('error')}", missing_package_files[:5]),
            _test_gate("worker_smoke_after_apply", "应用后 worker smoke 未阻断", "pass" if worker_status == "pass" else "warning" if worker_status == "warning" else "fail", _safe_text(worker_payload.get("error") or f"worker smoke status={worker_status}", 520), []),
            _test_gate("verification_is_read_only", "验证不改目标文件", "pass", "本接口只写验证 material 和测试 scratch，不执行 apply/rollback。", ["real_repo_writes=0"]),
        ],
        "verification_items": verification_items,
        "field_checks": field_checks,
        "applied_records": [
            {
                "apply_item_id": _safe_text(item.get("apply_item_id"), 160),
                "worker_id": _safe_text(item.get("worker_id"), 160),
                "changed_files": [_safe_text(file, 420) for file in _list_value(item.get("changed_files"))],
            }
            for item in applied_items
        ],
        "replay": {
            "package_smoke": package_smoke,
            "worker_smoke": worker_smoke,
        },
        "next_actions": [
            {
                "id": "reconcile_real_run_repair_outcome",
                "title": "对账真实失败 run 修复结果",
                "summary": "把应用前 code review finding 与应用后回放结果逐项对账，确认是否需要继续修复或准备回滚。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-post-apply-verification/latest",
            }
        ],
        "source": {
            **(_dict_value(apply_report.get("source"))),
            "apply_execution_endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/latest",
            "post_apply_verification_material": str(path.relative_to(_repo_root())) if path else "",
            "code_package_files": _team_builder_rel_path(code_root),
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_real_run_outcome_reconciliation_report() -> dict[str, Any]:
    apply_report = _team_builder_real_run_apply_execution_report()
    run_id = _safe_text(apply_report.get("run_id"), 160)
    path = _team_builder_repair_real_run_outcome_reconciliation_path(run_id)
    if not apply_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(apply_report.get("reason"), 500),
            "run_id": run_id,
            "team_name": _safe_text(apply_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无真实失败 run 显式应用报告，无法做结果对账。",
            "counts": {
                "applied": 0,
                "reconciled": 0,
                "missing_baseline": 0,
                "resolved_findings": 0,
                "introduced_findings": 0,
                "persistent_findings": 0,
                "pending_verification": 0,
                "warnings": 0,
                "ready": 0,
                "real_repo_writes": 0,
            },
            "quality_gates": [],
            "reconciliation_items": [],
            "next_actions": [],
            "source": _dict_value(apply_report.get("source")),
        }

    applied_items = [
        _dict_value(item)
        for item in _list_value(apply_report.get("apply_items"))
        if _safe_text(_dict_value(item).get("status"), 80) == "applied"
    ]
    ready = int(_dict_value(apply_report.get("counts")).get("ready") or 0)
    verification = _team_builder_real_run_post_apply_verification_report()
    verification_verdict = _safe_text(verification.get("verdict"), 80)
    verification_ready = verification_verdict in {"pass", "warning", "fail"}
    replay = _team_builder_repair_real_run_replay_plan_report()
    baseline_findings = [_dict_value(item) for item in _list_value(replay.get("findings"))]
    baseline_by_key = {
        _team_builder_finding_key(finding): finding
        for finding in baseline_findings
        if _team_builder_finding_key(finding)
    }
    verification_items_by_apply_id = {
        _safe_text(item.get("apply_item_id"), 160): _dict_value(item)
        for item in _list_value(verification.get("verification_items"))
        if _safe_text(_dict_value(item).get("apply_item_id"), 160)
    }
    failed_gates = [
        _dict_value(gate)
        for gate in _list_value(verification.get("quality_gates"))
        if _safe_text(_dict_value(gate).get("status"), 80) == "fail"
    ]
    warning_gates = [
        _dict_value(gate)
        for gate in _list_value(verification.get("quality_gates"))
        if _safe_text(_dict_value(gate).get("status"), 80) == "warning"
    ]
    introduced_gate_failures = [
        gate for gate in failed_gates
        if _safe_text(gate.get("id"), 120) not in {"required_fields_replayed"}
    ]
    items: list[dict[str, Any]] = []
    for index, apply_item in enumerate(applied_items):
        apply_item_id = _safe_text(apply_item.get("apply_item_id"), 160)
        verification_item = verification_items_by_apply_id.get(apply_item_id, {})
        missing_required_fields = [
            _safe_text(field, 120)
            for field in _list_value(verification_item.get("missing_required_fields"))
            if _safe_text(field, 120)
        ]
        missing_baseline = not bool(baseline_by_key)
        persistent_keys = sorted(baseline_by_key) if missing_required_fields else []
        introduced = [
            {
                "key": _safe_text(gate.get("id"), 160),
                "check_id": _safe_text(gate.get("id"), 160),
                "observation": _safe_text(gate.get("summary"), 420),
            }
            for gate in introduced_gate_failures
        ]
        if not verification_ready:
            status = "pending_verification"
            summary = "真实失败 run 已应用，但应用后回放验证尚未完成，不能确认修复结果。"
            resolved_keys: list[str] = []
        elif missing_baseline:
            status = "missing_baseline"
            summary = "缺少 replay plan/code review 基线 finding，无法证明应用前后问题已对应消解。"
            resolved_keys = []
        elif introduced:
            status = "regression"
            summary = f"应用后新增 {len(introduced)} 个验证失败，需要继续诊断或准备回滚。"
            resolved_keys = []
        elif missing_required_fields:
            status = "partial"
            summary = f"应用后仍有 required 字段未确认读取: {', '.join(missing_required_fields)}。"
            resolved_keys = []
        else:
            resolved_keys = sorted(baseline_by_key)
            if warning_gates or verification_verdict == "warning":
                status = "reconciled_with_warnings"
                summary = f"原始 finding 已消解，但仍有 {len(warning_gates)} 个非阻断验证警告。"
            else:
                status = "reconciled"
                summary = f"原始 {len(resolved_keys)} 个 finding 已通过应用后回放验证消解。"
        items.append({
            "id": f"real_run_outcome_reconciliation:{index}",
            "apply_item_id": apply_item_id,
            "worker_id": _safe_text(apply_item.get("worker_id"), 160),
            "status": status,
            "summary": summary,
            "changed_files": [_safe_text(file, 420) for file in _list_value(apply_item.get("changed_files"))],
            "file_set": bool(apply_item.get("file_set")),
            "file_count": int(apply_item.get("file_count") or 0),
            "before": {
                "baseline_findings": len(baseline_by_key),
                "repair_required": int(_dict_value(replay.get("counts")).get("repair_required") or 0),
                "replay_plan_verdict": _safe_text(replay.get("verdict"), 80),
            },
            "after": {
                "verification_verdict": verification_verdict,
                "missing_required_fields": len(missing_required_fields),
                "failed_gates": len(failed_gates),
                "warning_gates": len(warning_gates),
            },
            "resolved_findings": [
                {
                    "key": key,
                    "check_id": _safe_text(baseline_by_key[key].get("check_id"), 160),
                    "observation": _safe_text(baseline_by_key[key].get("observation"), 420),
                }
                for key in resolved_keys[:20]
            ],
            "introduced_findings": introduced[:20],
            "persistent_findings": [
                {
                    "key": key,
                    "check_id": _safe_text(baseline_by_key[key].get("check_id"), 160),
                    "observation": _safe_text(baseline_by_key[key].get("observation"), 420),
                }
                for key in persistent_keys[:20]
            ],
            "warnings": [
                {
                    "key": _safe_text(gate.get("id"), 160),
                    "summary": _safe_text(gate.get("summary"), 420),
                }
                for gate in warning_gates[:20]
            ],
        })

    missing_baseline = sum(1 for item in items if item.get("status") == "missing_baseline")
    pending = sum(1 for item in items if item.get("status") == "pending_verification")
    regressions = sum(1 for item in items if item.get("status") == "regression")
    partial = sum(1 for item in items if item.get("status") == "partial")
    reconciled = sum(1 for item in items if item.get("status") in {"reconciled", "reconciled_with_warnings"})
    warning_items = sum(1 for item in items if item.get("status") == "reconciled_with_warnings")
    resolved_total = sum(len(_list_value(item.get("resolved_findings"))) for item in items)
    introduced_total = sum(len(_list_value(item.get("introduced_findings"))) for item in items)
    persistent_total = sum(len(_list_value(item.get("persistent_findings"))) for item in items)
    if not applied_items:
        verdict = "awaiting_apply" if ready else "clean"
        summary = (
            f"{ready} 条真实失败 run 修复等待显式应用；结果对账尚不能开始。"
            if ready else "当前没有已应用的真实失败 run 修复；无需做结果对账。"
        )
    elif pending:
        verdict = "awaiting_verification"
        summary = f"{pending} 条真实失败 run 修复等待应用后回放验证。"
    elif regressions:
        verdict = "regression"
        summary = f"{regressions} 条真实失败 run 修复应用后出现新增验证失败。"
    elif partial:
        verdict = "partial"
        summary = f"{partial} 条真实失败 run 修复仍有原始 required 字段问题残留。"
    elif missing_baseline:
        verdict = "missing_baseline"
        summary = f"{missing_baseline} 条真实失败 run 修复缺少应用前 finding 基线。"
    elif warning_items:
        verdict = "warning"
        summary = f"{reconciled} 条真实失败 run 修复 finding 已消解，但仍有非阻断验证警告。"
    else:
        verdict = "pass"
        summary = f"{reconciled} 条真实失败 run 修复前后对账通过，消解 {resolved_total} 条 finding。"

    awaiting_real_apply = not applied_items and ready > 0
    if awaiting_real_apply:
        post_apply_verified_status = "warning"
        post_apply_verified_summary = "尚未执行真实 apply，不能做应用后回放验证。"
        no_new_failures_status = "warning"
        no_new_failures_summary = "尚未执行真实 apply，不能判断是否产生新增验证失败。"
        original_findings_resolved_status = "warning"
        original_findings_resolved_summary = "尚未执行真实 apply，不能判断原始 finding 是否已消解。"
    elif pending:
        post_apply_verified_status = "warning"
        post_apply_verified_summary = f"{pending} 条记录仍待回放验证。"
        no_new_failures_status = "warning"
        no_new_failures_summary = "应用后回放验证尚未完成，不能判断是否产生新增验证失败。"
        original_findings_resolved_status = "warning"
        original_findings_resolved_summary = "应用后回放验证尚未完成，不能判断原始 finding 是否已消解。"
    else:
        post_apply_verified_status = "pass"
        post_apply_verified_summary = "应用后回放验证已可用于对账。"
        no_new_failures_status = "pass" if introduced_total == 0 else "fail"
        no_new_failures_summary = "应用后没有新增阻断验证失败。" if introduced_total == 0 else f"新增 {introduced_total} 个验证失败。"
        original_findings_resolved_status = "pass" if not applied_items or persistent_total == 0 else "warning"
        original_findings_resolved_summary = "原始 code review/repair finding 未在应用后残留。" if persistent_total == 0 else f"仍有 {persistent_total} 条原始 finding 残留。"

    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(apply_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "applied": len(applied_items),
            "reconciled": reconciled,
            "missing_baseline": missing_baseline,
            "resolved_findings": resolved_total,
            "introduced_findings": introduced_total,
            "persistent_findings": persistent_total,
            "pending_verification": pending,
            "warnings": len(warning_gates),
            "ready": ready,
            "real_repo_writes": 0,
        },
        "quality_gates": [
            _test_gate("real_run_baseline_available", "应用前 finding 基线存在", "pass" if not applied_items or missing_baseline == 0 else "warning", "replay plan/code review finding 可用于前后对账。" if missing_baseline == 0 else f"{missing_baseline} 条记录缺少基线。", [f"baseline_findings={len(baseline_by_key)}"]),
            _test_gate("real_run_post_apply_verified", "应用后回放验证可用", post_apply_verified_status, post_apply_verified_summary, [verification_verdict]),
            _test_gate("real_run_no_new_failures", "没有新增验证失败", no_new_failures_status, no_new_failures_summary, [_safe_text(item.get("check_id"), 160) for item in [finding for row in items for finding in _list_value(row.get("introduced_findings"))]][:5]),
            _test_gate("real_run_original_findings_resolved", "原始 finding 已消解", original_findings_resolved_status, original_findings_resolved_summary, [_safe_text(item.get("check_id"), 160) for item in [finding for row in items for finding in _list_value(row.get("persistent_findings"))]][:5]),
            _test_gate("real_run_reconciliation_is_read_only", "对账只读", "pass", "本接口只写对账 material，不执行 apply/rollback，也不改目标文件。", ["real_repo_writes=0"]),
        ],
        "reconciliation_items": items,
        "next_actions": [
            {
                "id": "verify_real_run_after_apply_first",
                "title": "先执行应用后回放验证",
                "summary": "结果对账依赖应用后回放验证；验证完成后才能判断是否通过、部分通过或回滚。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-post-apply-verification/latest",
            }
        ] if applied_items and pending else [
            {
                "id": "post_real_run_apply_execute",
                "title": "先显式应用真实失败 run 修复",
                "summary": "尚无已应用记录时，对账只能停在等待应用。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/execute",
            }
        ] if not applied_items else [
            {
                "id": "prepare_real_run_rollback_readiness",
                "title": "进入真实失败 run 回滚就绪检查",
                "summary": "对账后应根据结果判断是否需要准备回滚，或者继续推进闭环总览。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest",
            }
        ],
        "source": {
            **(_dict_value(verification.get("source"))),
            "apply_execution_endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/latest",
            "post_apply_verification_endpoint": "/api/team-builder-materialization/repair-real-run-post-apply-verification/latest",
            "replay_plan_endpoint": "/api/team-builder-materialization/repair-real-run-replay-plan/latest",
            "outcome_reconciliation_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_real_run_rollback_readiness_report() -> dict[str, Any]:
    apply_report = _team_builder_real_run_apply_execution_report()
    run_id = _safe_text(apply_report.get("run_id"), 160)
    path = _team_builder_repair_real_run_rollback_readiness_path(run_id)
    if not apply_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(apply_report.get("reason"), 500),
            "run_id": run_id,
            "team_name": _safe_text(apply_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无真实失败 run 应用记录，无法检查回滚就绪。",
            "counts": {
                "applied": 0,
                "rollback_ready": 0,
                "blocked": 0,
                "stale_or_mismatch": 0,
                "missing_before_snapshot": 0,
                "ready": 0,
                "real_repo_writes": 0,
            },
            "quality_gates": [],
            "rollback_items": [],
            "next_actions": [],
            "source": _dict_value(apply_report.get("source")),
        }

    ready_to_apply = int(_dict_value(apply_report.get("counts")).get("ready") or 0)
    records = [
        _dict_value(record)
        for record in _list_value(apply_report.get("records"))
        if bool(_dict_value(record).get("applied"))
    ]
    rollback_items: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        apply_item_id = _safe_text(record.get("apply_item_id") or record.get("id"), 160)
        worker_id = _safe_text(record.get("worker_id"), 160)
        file_records = _team_builder_apply_record_file_records(record)
        checked_files: list[dict[str, Any]] = []
        blocked_reasons: list[str] = []
        for file_record in file_records:
            changed_file = _safe_text(file_record.get("changed_file"), 420)
            normalized_changed_file = changed_file.replace("\\", "/")
            before_sha = _safe_text(file_record.get("before_sha256"), 96)
            after_sha = _safe_text(file_record.get("after_sha256"), 96)
            target_path = _team_builder_repo_file_from_relpath(changed_file)
            current_sha = _team_builder_file_sha256(target_path) if target_path is not None else ""
            before_preview_file = _safe_text(file_record.get("before_preview_file"), 520) or _team_builder_before_preview_file_for_record(file_record)
            before_snapshot_sha = ""
            before_snapshot_exists = False
            before_snapshot_safe = False
            if before_preview_file:
                before_snapshot_path = (_repo_root() / before_preview_file).resolve()
                try:
                    before_snapshot_path.relative_to((_repo_root() / "_scratch" / "team_builder_repair_apply_preview").resolve())
                    before_snapshot_safe = True
                except (OSError, ValueError):
                    before_snapshot_safe = False
                if before_snapshot_safe and before_snapshot_path.is_file():
                    before_snapshot_exists = True
                    before_snapshot_sha = _team_builder_file_sha256(before_snapshot_path)
            target_scope_safe = (
                bool(target_path)
                and normalized_changed_file.startswith("_scratch/team_builder_real_material_validation/")
                and "/code_package_files/" in normalized_changed_file
            )
            current_matches_after = bool(current_sha and after_sha and current_sha == after_sha)
            before_snapshot_valid = bool(before_sha and before_snapshot_sha and before_sha == before_snapshot_sha)
            if not target_scope_safe:
                blocked_reasons.append(f"{normalized_changed_file}: 目标文件不存在或不在真实失败 run code_package_files 允许范围内。")
            if not current_matches_after:
                blocked_reasons.append(f"{normalized_changed_file}: 当前文件 sha 与应用记录的 after sha 不一致。")
            if not before_snapshot_exists:
                blocked_reasons.append(f"{normalized_changed_file}: 缺少应用前 before 快照文件。")
            elif not before_snapshot_valid:
                blocked_reasons.append(f"{normalized_changed_file}: before 快照 sha 与应用记录不一致。")
            if not before_snapshot_safe:
                blocked_reasons.append(f"{normalized_changed_file}: before 快照不在允许的 scratch 预览目录。")
            checked_files.append({
                "changed_file": normalized_changed_file,
                "before_sha256": before_sha,
                "after_sha256": after_sha,
                "current_sha256": current_sha,
                "before_preview_file": before_preview_file,
                "before_snapshot_sha256": before_snapshot_sha,
                "target_scope_safe": target_scope_safe,
                "current_matches_after": current_matches_after,
                "before_snapshot_valid": before_snapshot_valid,
            })
        current_matches_after = bool(checked_files) and all(bool(item.get("current_matches_after")) for item in checked_files)
        before_snapshot_valid = bool(checked_files) and all(bool(item.get("before_snapshot_valid")) for item in checked_files)
        target_scope_safe = bool(checked_files) and all(bool(item.get("target_scope_safe")) for item in checked_files)
        before_snapshot_safe = bool(checked_files) and all(_safe_text(item.get("before_preview_file"), 520) for item in checked_files)
        if current_matches_after and before_snapshot_valid and target_scope_safe and before_snapshot_safe:
            status = "ready_for_explicit_rollback"
            summary = "当前文件仍等于真实失败 run 应用后的内容，before 快照可用；可进入显式回滚执行门。"
        elif not current_matches_after:
            status = "stale_or_mismatch"
            summary = "当前文件已经不等于应用后的内容，禁止自动回滚。"
        elif not before_snapshot_valid:
            status = "missing_before_snapshot"
            summary = "缺少可校验的应用前快照，禁止自动回滚。"
        else:
            status = "blocked"
            summary = "回滚目标未通过真实失败 run 安全范围检查。"
        rollback_items.append({
            "id": f"real_run_rollback_readiness:{index}",
            "apply_item_id": apply_item_id,
            "worker_id": worker_id,
            "status": status,
            "summary": summary,
            "changed_files": [_safe_text(item.get("changed_file"), 420) for item in checked_files],
            "file_set": len(checked_files) > 1,
            "file_count": len(checked_files),
            "file_records": checked_files,
            "applied_at": _safe_text(record.get("applied_at"), 120),
            "applied_by": _safe_text(record.get("applied_by"), 120),
            "real_writes": int(record.get("real_writes") or 0),
            "blocked_reasons": blocked_reasons,
        })

    rollback_ready = sum(1 for item in rollback_items if item.get("status") == "ready_for_explicit_rollback")
    stale = sum(1 for item in rollback_items if item.get("status") == "stale_or_mismatch")
    missing_before = sum(1 for item in rollback_items if item.get("status") == "missing_before_snapshot")
    blocked = sum(1 for item in rollback_items if item.get("status") == "blocked")
    real_repo_writes = sum(int(record.get("real_writes") or 0) for record in records)
    if not records:
        verdict = "awaiting_apply" if ready_to_apply else "clean"
        summary = (
            f"{ready_to_apply} 条真实失败 run 修复等待显式应用；回滚就绪只能等待应用记录。"
            if ready_to_apply else "当前没有已应用的真实失败 run 修复；无需准备回滚。"
        )
    elif stale:
        verdict = "stale_or_mismatch"
        summary = f"{stale} 条真实失败 run 应用记录与当前文件不匹配，禁止自动回滚。"
    elif missing_before:
        verdict = "missing_before_snapshot"
        summary = f"{missing_before} 条真实失败 run 应用记录缺少可校验 before 快照，禁止自动回滚。"
    elif blocked:
        verdict = "blocked"
        summary = f"{blocked} 条真实失败 run 应用记录未通过回滚安全范围检查。"
    else:
        verdict = "ready_for_explicit_rollback"
        summary = f"{rollback_ready} 条真实失败 run 应用记录具备显式回滚前置条件；GET 不执行回滚。"

    outcome = _team_builder_real_run_outcome_reconciliation_report()
    outcome_verdict = _safe_text(outcome.get("verdict"), 80)
    outcome_ready_for_decision = outcome_verdict in {"pass", "warning", "partial", "regression", "missing_baseline"}
    waiting_for_apply = not records and ready_to_apply > 0
    waiting_summary = "尚未执行真实 apply，不能判断回滚文件状态。"
    gates = [
        _test_gate(
            "real_run_explicit_rollback_only",
            "只允许显式回滚",
            "pass",
            "GET 报告接口只检查回滚就绪，不写目标文件；后续真实回滚必须另走显式执行门。",
            ["get_writes_files=false", "future_post_requires=confirm_real_run_file_rollback"],
        ),
        _test_gate(
            "real_run_current_file_matches_after",
            "当前文件等于应用后内容",
            "warning" if waiting_for_apply else ("pass" if not records or stale == 0 else "fail"),
            waiting_summary if waiting_for_apply else (
                "所有已应用记录的当前文件 sha 都等于 after sha。"
                if stale == 0 else f"{stale} 条记录当前文件已经变化。"
            ),
            [_safe_text(item.get("apply_item_id"), 160) for item in rollback_items if item.get("status") == "stale_or_mismatch"][:5],
        ),
        _test_gate(
            "real_run_before_snapshot_available",
            "应用前快照可用",
            "warning" if waiting_for_apply else ("pass" if not records or missing_before == 0 else "fail"),
            waiting_summary if waiting_for_apply else (
                "所有已应用记录都有可校验 before 快照。"
                if missing_before == 0 else f"{missing_before} 条记录缺少可校验 before 快照。"
            ),
            [_safe_text(item.get("apply_item_id"), 160) for item in rollback_items if item.get("status") == "missing_before_snapshot"][:5],
        ),
        _test_gate(
            "real_run_rollback_target_scope_safe",
            "回滚目标范围安全",
            "warning" if waiting_for_apply else ("pass" if not records or blocked == 0 else "fail"),
            waiting_summary if waiting_for_apply else (
                "回滚目标均限制在真实失败 run code_package_files 内。"
                if blocked == 0 else f"{blocked} 条记录目标范围不安全。"
            ),
            [_safe_text(file, 420) for item in rollback_items if item.get("status") == "blocked" for file in _list_value(item.get("changed_files"))][:5],
        ),
        _test_gate(
            "real_run_outcome_reviewed_before_rollback",
            "回滚前先看修复对账",
            "warning" if waiting_for_apply or (records and not outcome_ready_for_decision) else "pass",
            "尚未应用时还没有修复结果对账。" if waiting_for_apply else (
                "修复结果对账已可用于判断是否需要回滚。"
                if outcome_ready_for_decision else "应用后验证或结果对账尚未完成，回滚只能作为安全预案。"
            ),
            [outcome_verdict],
        ),
    ]
    next_actions = [
        {
            "id": "post_real_run_apply_execute",
            "title": "先显式应用真实失败 run 修复",
            "summary": "没有已应用记录时，回滚就绪只能停在等待应用。",
            "endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/execute",
        }
    ] if waiting_for_apply else [
        {
            "id": "build_real_run_rollback_execution_gate",
            "title": "建立真实失败 run 显式回滚执行门",
            "summary": "回滚就绪只证明 before 快照和当前 sha 可校验；真正回滚仍需要独立 POST 执行门和确认 token。",
            "endpoint": "/api/team-builder-materialization/repair-real-run-rollback-readiness/latest",
        }
    ] if records else []
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(apply_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "applied": len(records),
            "rollback_ready": rollback_ready,
            "blocked": blocked,
            "stale_or_mismatch": stale,
            "missing_before_snapshot": missing_before,
            "ready": ready_to_apply,
            "real_repo_writes": real_repo_writes,
        },
        "quality_gates": gates,
        "rollback_items": rollback_items,
        "next_actions": next_actions,
        "source": {
            **(_dict_value(apply_report.get("source"))),
            "apply_execution_endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/latest",
            "outcome_reconciliation_endpoint": "/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest",
            "real_run_rollback_readiness_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_read_real_run_rollback_execution_records(run_id: str) -> list[dict[str, Any]]:
    path = _team_builder_repair_real_run_rollback_execution_records_path(run_id)
    if path is None or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = _list_value(payload.get("records")) if isinstance(payload, dict) else _list_value(payload)
    return [_dict_value(item) for item in records]


def _team_builder_write_real_run_rollback_execution_records(run_id: str, records: list[dict[str, Any]]) -> str:
    path = _team_builder_repair_real_run_rollback_execution_records_path(run_id)
    if path is None:
        return ""
    payload = {
        "run_id": _safe_text(run_id, 160),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(_repo_root()))


def _team_builder_real_run_rollback_execution_report() -> dict[str, Any]:
    readiness = _team_builder_real_run_rollback_readiness_report()
    run_id = _safe_text(readiness.get("run_id"), 160)
    records_path = _team_builder_repair_real_run_rollback_execution_records_path(run_id)
    report_path = _team_builder_repair_real_run_rollback_execution_report_path(run_id)
    if not readiness.get("available"):
        return {
            "available": False,
            "reason": _safe_text(readiness.get("reason"), 500),
            "run_id": run_id,
            "team_name": _safe_text(readiness.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无真实失败 run 回滚就绪报告，无法检查回滚执行记录。",
            "counts": {
                "items": 0,
                "ready": 0,
                "rolled_back": 0,
                "blocked": 0,
                "stale_or_mismatch": 0,
                "real_repo_writes": 0,
            },
            "quality_gates": [],
            "rollback_items": [],
            "records": [],
            "source": _dict_value(readiness.get("source")),
        }
    records = _team_builder_read_real_run_rollback_execution_records(run_id)
    records_by_apply_item: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        apply_item_id = _safe_text(record.get("apply_item_id"), 160)
        if apply_item_id:
            records_by_apply_item.setdefault(apply_item_id, []).append(record)

    rollback_items: list[dict[str, Any]] = []
    for index, item in enumerate([_dict_value(raw) for raw in _list_value(readiness.get("rollback_items"))]):
        apply_item_id = _safe_text(item.get("apply_item_id"), 160)
        latest_record = (records_by_apply_item.get(apply_item_id) or [{}])[-1]
        readiness_file_records = [_dict_value(raw) for raw in _list_value(item.get("file_records")) if _dict_value(raw)]
        rollback_file_records = _team_builder_rollback_record_file_records(latest_record) if latest_record else []
        source_records = rollback_file_records or readiness_file_records
        current_file_records: list[dict[str, Any]] = []
        for file_record in source_records:
            changed_file = _safe_text(file_record.get("changed_file"), 420)
            target_path = _team_builder_repo_file_from_relpath(changed_file)
            current_file_records.append({
                **file_record,
                "current_sha256": _team_builder_file_sha256(target_path) if target_path is not None else "",
            })
        if latest_record and current_file_records:
            files_match_rollback = all(
                _safe_text(file_record.get("current_sha256"), 96)
                and _safe_text(file_record.get("current_sha256"), 96) == _safe_text(file_record.get("rollback_to_sha256"), 96)
                for file_record in current_file_records
            )
        else:
            files_match_rollback = False
        if latest_record and files_match_rollback:
            status = "rolled_back"
            summary = "当前文件内容与最近一次真实失败 run 显式回滚后的 before 快照一致。"
        elif latest_record:
            status = "stale_or_mismatch"
            summary = "存在回滚记录，但当前目标文件与回滚结果不一致，需要人工检查。"
        elif _safe_text(item.get("status"), 80) == "ready_for_explicit_rollback":
            status = "ready_for_explicit_rollback"
            summary = "回滚前置条件已满足，仍需要显式 POST execute 才会写目标文件。"
        else:
            status = "blocked"
            summary = _safe_text(item.get("summary"), 520) or "回滚就绪检查未通过，不能执行真实回滚。"
        rollback_items.append({
            "id": f"real_run_rollback_execution:{index}",
            "apply_item_id": apply_item_id,
            "worker_id": _safe_text(item.get("worker_id"), 160),
            "status": status,
            "summary": summary,
            "changed_files": [
                _safe_text(file_record.get("changed_file"), 420)
                for file_record in (current_file_records or readiness_file_records)
                if _safe_text(file_record.get("changed_file"), 420)
            ],
            "file_set": bool(item.get("file_set")) or len(current_file_records or readiness_file_records) > 1,
            "file_count": len(current_file_records or readiness_file_records),
            "file_records": current_file_records or readiness_file_records,
            "rolled_back_at": _safe_text(latest_record.get("rolled_back_at"), 120),
            "rolled_back_by": _safe_text(latest_record.get("rolled_back_by"), 120),
            "real_writes": int(latest_record.get("real_writes") or 0) if latest_record else 0,
            "blocked_reasons": [_safe_text(reason, 420) for reason in _list_value(item.get("blocked_reasons")) if _safe_text(reason, 420)],
        })

    ready = sum(1 for item in rollback_items if item.get("status") == "ready_for_explicit_rollback")
    rolled_back = sum(1 for item in rollback_items if item.get("status") == "rolled_back")
    blocked = sum(1 for item in rollback_items if item.get("status") == "blocked")
    stale = sum(1 for item in rollback_items if item.get("status") == "stale_or_mismatch")
    real_repo_writes = sum(int(item.get("real_writes") or 0) for item in rollback_items)
    if not rollback_items:
        verdict = "awaiting_apply" if _safe_text(readiness.get("verdict"), 80) == "awaiting_apply" else "clean"
        summary = "真实失败 run 修复尚未应用，回滚执行未开启。" if verdict == "awaiting_apply" else "当前没有已应用的真实失败 run 修复；无需真实回滚。"
    elif stale:
        verdict = "stale_or_mismatch"
        summary = f"{stale} 条真实失败 run 回滚记录与当前目标文件不匹配。"
    elif rolled_back:
        verdict = "rolled_back"
        summary = f"{rolled_back} 条真实失败 run 修复已显式回滚到应用前内容。"
    elif blocked:
        verdict = "blocked"
        summary = f"{blocked} 条真实失败 run 应用记录尚未通过回滚就绪检查。"
    else:
        verdict = "ready_for_explicit_rollback"
        summary = f"{ready} 条真实失败 run 应用记录已具备显式回滚条件。"
    waiting_for_apply = not rollback_items and _safe_text(readiness.get("verdict"), 80) == "awaiting_apply"
    gates = [
        _test_gate(
            "real_run_rollback_readiness_required",
            "必须先通过回滚就绪检查",
            "warning" if waiting_for_apply else ("pass" if not rollback_items or blocked == 0 else "warning"),
            "真实失败 run 修复尚未应用，回滚执行必须等待回滚就绪检查。"
            if waiting_for_apply else (
                "所有回滚项都已通过就绪检查或已有回滚记录。"
                if blocked == 0 else f"{blocked} 条回滚项仍被就绪检查阻断。"
            ),
            [_safe_text(item.get("apply_item_id"), 160) for item in rollback_items if item.get("status") == "blocked"][:5],
        ),
        _test_gate(
            "real_run_explicit_rollback_execute_only",
            "只允许显式执行回滚",
            "pass",
            "GET 报告接口不会写目标文件；只有 POST execute 且确认 token 齐全时才写入目标文件。",
            ["get_writes_files=false", "post_requires=confirm_real_run_file_rollback"],
        ),
        _test_gate(
            "real_run_rollback_record_matches_current",
            "回滚记录匹配当前文件",
            "pass" if stale == 0 else "fail",
            "没有发现回滚记录与当前目标文件不匹配。" if stale == 0 else f"{stale} 条回滚记录已经失效或不匹配。",
            [_safe_text(item.get("apply_item_id"), 160) for item in rollback_items if item.get("status") == "stale_or_mismatch"][:5],
        ),
    ]
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(readiness.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "items": len(rollback_items),
            "ready": ready,
            "rolled_back": rolled_back,
            "blocked": blocked,
            "stale_or_mismatch": stale,
            "real_repo_writes": real_repo_writes,
        },
        "quality_gates": gates,
        "rollback_items": rollback_items,
        "records": records,
        "source": {
            **(_dict_value(readiness.get("source"))),
            "rollback_readiness_endpoint": "/api/team-builder-materialization/repair-real-run-rollback-readiness/latest",
            "rollback_execution_records_material": str(records_path.relative_to(_repo_root())) if records_path else "",
            "rollback_execution_report_material": str(report_path.relative_to(_repo_root())) if report_path else "",
        },
    }
    if report_path:
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_execute_real_run_rollback(payload: dict[str, Any]) -> dict[str, Any]:
    readiness = _team_builder_real_run_rollback_readiness_report()
    if not readiness.get("available"):
        raise HTTPException(status_code=409, detail="暂无可用于真实失败 run 回滚的就绪报告。")
    apply_item_id = _safe_text(payload.get("apply_item_id") or payload.get("rollback_item_id"), 160)
    if not apply_item_id:
        raise HTTPException(status_code=400, detail="缺少 apply_item_id。")
    if payload.get("rollback") is not True:
        raise HTTPException(status_code=400, detail="必须显式传入 rollback=true。")
    rolled_back_by = _safe_text(payload.get("rolled_back_by"), 120)
    if not rolled_back_by:
        raise HTTPException(status_code=400, detail="缺少 rolled_back_by。")
    reason = _safe_text(payload.get("reason"), 520)
    if not reason:
        raise HTTPException(status_code=400, detail="缺少回滚理由 reason。")
    confirmations = [_safe_text(item, 300) for item in _list_value(payload.get("confirmations")) if _safe_text(item, 300)]
    if "confirm_real_run_file_rollback" not in confirmations:
        raise HTTPException(status_code=400, detail="缺少确认 token: confirm_real_run_file_rollback。")
    item = next((
        _dict_value(raw)
        for raw in _list_value(readiness.get("rollback_items"))
        if _safe_text(_dict_value(raw).get("apply_item_id"), 160) == apply_item_id
    ), None)
    if item is None:
        raise HTTPException(status_code=404, detail="找不到对应 apply_item 的回滚就绪项。")
    if _safe_text(item.get("status"), 80) != "ready_for_explicit_rollback":
        raise HTTPException(status_code=409, detail="该项尚未通过回滚就绪检查，不能真实回滚。")
    readiness_file_records = [_dict_value(raw) for raw in _list_value(item.get("file_records")) if _dict_value(raw)]
    if len(readiness_file_records) > 1 and "confirm_real_run_file_set_rollback" not in confirmations:
        raise HTTPException(status_code=400, detail="多文件真实失败 run 回滚缺少确认 token: confirm_real_run_file_set_rollback。")

    staged_files: list[dict[str, Any]] = []
    for file_record in readiness_file_records:
        changed_file = _safe_text(file_record.get("changed_file"), 420)
        normalized_changed_file = changed_file.replace("\\", "/")
        if not (
            normalized_changed_file.startswith("_scratch/team_builder_real_material_validation/")
            and "/code_package_files/" in normalized_changed_file
        ):
            raise HTTPException(status_code=409, detail=f"{normalized_changed_file}: 不在真实失败 run code_package_files 回滚范围内。")
        target_path = _team_builder_repo_file_from_relpath(changed_file)
        if target_path is None:
            raise HTTPException(status_code=409, detail=f"找不到真实失败 run 回滚目标文件: {changed_file}")
        file_after_sha = _safe_text(file_record.get("after_sha256"), 96)
        file_before_sha = _safe_text(file_record.get("before_sha256"), 96)
        current_sha = _team_builder_file_sha256(target_path)
        if current_sha != file_after_sha:
            raise HTTPException(status_code=409, detail=f"{changed_file} 当前目标文件已不等于应用后的 after_sha256，不能自动回滚。")
        before_preview_file = _safe_text(file_record.get("before_preview_file"), 520)
        before_preview_path = (_repo_root() / before_preview_file).resolve()
        try:
            before_preview_path.relative_to((_repo_root() / "_scratch" / "team_builder_repair_apply_preview").resolve())
        except ValueError:
            raise HTTPException(status_code=409, detail="before 快照不在允许的 scratch 目录。")
        if not before_preview_path.is_file():
            raise HTTPException(status_code=409, detail=f"before 快照文件不存在: {before_preview_file}")
        if _team_builder_file_sha256(before_preview_path) != file_before_sha:
            raise HTTPException(status_code=409, detail=f"{changed_file} before 快照 sha 与应用记录不一致。")
        staged_files.append({
            "changed_file": str(target_path.relative_to(_repo_root())).replace("\\", "/"),
            "target_path": target_path,
            "current_text": target_path.read_text(encoding="utf-8"),
            "before_text": before_preview_path.read_text(encoding="utf-8"),
            "rollback_from_sha256": file_after_sha,
            "rollback_to_sha256": file_before_sha,
            "before_preview_file": before_preview_file,
        })
    written: list[dict[str, Any]] = []
    try:
        for staged in staged_files:
            target_path = staged["target_path"]
            target_path.write_text(str(staged.get("before_text") or ""), encoding="utf-8")
            if _team_builder_file_sha256(target_path) != staged["rollback_to_sha256"]:
                raise ValueError(f"{staged['changed_file']} 回滚写入后 sha 校验失败。")
            written.append(staged)
    except Exception as exc:
        for staged in written:
            try:
                staged["target_path"].write_text(str(staged.get("current_text") or ""), encoding="utf-8")
            except OSError:
                pass
        raise HTTPException(status_code=409, detail=f"真实失败 run 回滚失败，已尝试恢复已写文件: {type(exc).__name__}: {exc}")

    run_id = _safe_text(readiness.get("run_id"), 160)
    records = _team_builder_read_real_run_rollback_execution_records(run_id)
    file_records = [
        {
            "changed_file": _safe_text(staged.get("changed_file"), 420),
            "rollback_from_sha256": _safe_text(staged.get("rollback_from_sha256"), 96),
            "rollback_to_sha256": _safe_text(staged.get("rollback_to_sha256"), 96),
            "before_preview_file": _safe_text(staged.get("before_preview_file"), 520),
            "real_writes": 1,
        }
        for staged in staged_files
    ]
    records.append({
        "id": f"real_run_rollback_execution:{apply_item_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "run_id": run_id,
        "team_name": _safe_text(readiness.get("team_name"), 160),
        "apply_item_id": apply_item_id,
        "worker_id": _safe_text(item.get("worker_id"), 160),
        "rolled_back": True,
        "rolled_back_by": rolled_back_by,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "changed_files": [_safe_text(file_record.get("changed_file"), 420) for file_record in file_records],
        "file_set": len(file_records) > 1,
        "file_count": len(file_records),
        "file_records": file_records,
        "confirmations": confirmations,
        "real_writes": len(file_records),
    })
    _team_builder_write_real_run_rollback_execution_records(run_id, records)
    return _team_builder_real_run_rollback_execution_report()


def _team_builder_real_run_rollback_post_verification_report() -> dict[str, Any]:
    rollback_report = _team_builder_real_run_rollback_execution_report()
    run_id = _safe_text(rollback_report.get("run_id"), 160)
    path = _team_builder_repair_real_run_rollback_post_verification_path(run_id)
    if not rollback_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(rollback_report.get("reason"), 500),
            "run_id": run_id,
            "team_name": _safe_text(rollback_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无真实失败 run 回滚执行记录，无法做回滚后验证。",
            "counts": {"rolled_back": 0, "verified": 0, "pending": 0, "failed": 0, "real_repo_writes": 0},
            "quality_gates": [],
            "verification_items": [],
            "source": _dict_value(rollback_report.get("source")),
        }
    rolled_items = [
        _dict_value(item)
        for item in _list_value(rollback_report.get("rollback_items"))
        if _safe_text(_dict_value(item).get("status"), 80) == "rolled_back"
    ]
    existing = _read_json_file(path) if path else {}
    rolled_ids = {_safe_text(item.get("apply_item_id"), 160) for item in rolled_items if _safe_text(item.get("apply_item_id"), 160)}
    verified_ids = {
        _safe_text(item.get("apply_item_id"), 160)
        for item in _list_value(existing.get("rolled_back_records"))
        if _safe_text(_dict_value(item).get("apply_item_id"), 160)
    }
    if rolled_items and existing.get("available") and rolled_ids and rolled_ids.issubset(verified_ids):
        return existing
    if not rolled_items:
        rollback_verdict = _safe_text(rollback_report.get("verdict"), 80)
        verdict = "awaiting_apply" if rollback_verdict == "awaiting_apply" else "clean"
        summary = (
            "真实失败 run 修复尚未应用，回滚后验证未开启。"
            if verdict == "awaiting_apply" else "当前没有已回滚的真实失败 run 修复；无需执行回滚后验证。"
        )
        verification_items: list[dict[str, Any]] = []
        verified = 0
        pending = 0
        failed = 0
    else:
        verification_items = [
            {
                "id": f"real_run_rollback_post_verification:{index}",
                "apply_item_id": _safe_text(item.get("apply_item_id"), 160),
                "worker_id": _safe_text(item.get("worker_id"), 160),
                "status": "pending_verification",
                "summary": "真实失败 run 已回滚，但尚未显式验证当前文件是否等于应用前 before 快照。",
                "changed_files": [_safe_text(file, 420) for file in _list_value(item.get("changed_files")) if _safe_text(file, 420)],
                "required_commands": [
                    "POST /api/team-builder-materialization/repair-real-run-rollback-post-verification/execute",
                    "GET /api/team-builder-materialization/repair-real-run-rollback-execution/latest",
                ],
            }
            for index, item in enumerate(rolled_items)
        ]
        verdict = "awaiting_verification"
        summary = f"{len(rolled_items)} 条真实失败 run 回滚记录等待回滚后验证。"
        verified = 0
        pending = len(rolled_items)
        failed = 0
    gates = [
        _test_gate(
            "real_run_rolled_back_records_present",
            "存在真实失败 run 回滚记录",
            "pass" if rolled_items else ("warning" if verdict == "awaiting_apply" else "pass"),
            f"{len(rolled_items)} 条真实失败 run 回滚记录需要验证。" if rolled_items else summary,
            list(rolled_ids)[:5],
        ),
        _test_gate(
            "real_run_post_rollback_verification_executed",
            "回滚后验证已执行",
            "warning" if rolled_items or verdict == "awaiting_apply" else "pass",
            "存在已回滚记录但尚未执行验证。"
            if rolled_items else ("尚未应用时没有回滚后验证可执行。" if verdict == "awaiting_apply" else "当前没有已回滚记录需要验证。"),
            list(rolled_ids)[:5],
        ),
        _test_gate(
            "real_run_post_rollback_is_read_only",
            "回滚后验证只读",
            "pass",
            "GET 和 POST 验证只写验证 material，不执行 apply/rollback，也不改目标文件。",
            ["real_repo_writes=0"],
        ),
    ]
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(rollback_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "rolled_back": len(rolled_items),
            "verified": verified,
            "pending": pending,
            "failed": failed,
            "real_repo_writes": 0,
        },
        "quality_gates": gates,
        "verification_items": verification_items,
        "source": {
            **(_dict_value(rollback_report.get("source"))),
            "rollback_execution_endpoint": "/api/team-builder-materialization/repair-real-run-rollback-execution/latest",
            "rollback_post_verification_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_execute_real_run_rollback_post_verification(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("verify") is not True:
        raise HTTPException(status_code=400, detail="必须显式传入 verify=true。")
    verified_by = _safe_text(payload.get("verified_by"), 120)
    if not verified_by:
        raise HTTPException(status_code=400, detail="缺少 verified_by。")
    reason = _safe_text(payload.get("reason"), 520)
    if not reason:
        raise HTTPException(status_code=400, detail="缺少验证理由 reason。")
    confirmations = [_safe_text(item, 300) for item in _list_value(payload.get("confirmations")) if _safe_text(item, 300)]
    if "confirm_real_run_post_rollback_verification" not in confirmations:
        raise HTTPException(status_code=400, detail="缺少确认 token: confirm_real_run_post_rollback_verification。")

    rollback_report = _team_builder_real_run_rollback_execution_report()
    if not rollback_report.get("available"):
        raise HTTPException(status_code=409, detail="暂无真实失败 run 回滚记录，不能执行回滚后验证。")
    rolled_items = [
        _dict_value(item)
        for item in _list_value(rollback_report.get("rollback_items"))
        if _safe_text(_dict_value(item).get("status"), 80) == "rolled_back"
    ]
    if not rolled_items:
        return _team_builder_real_run_rollback_post_verification_report()

    verification_items: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []
    rolled_back_records: list[dict[str, Any]] = []
    for index, item in enumerate(rolled_items):
        file_checks: list[dict[str, Any]] = []
        item_failed = False
        for file_record in [_dict_value(raw) for raw in _list_value(item.get("file_records")) if _dict_value(raw)]:
            changed_file = _safe_text(file_record.get("changed_file"), 420)
            target_path = _team_builder_repo_file_from_relpath(changed_file)
            current_sha = _team_builder_file_sha256(target_path) if target_path is not None else ""
            expected_before_sha = _safe_text(file_record.get("rollback_to_sha256") or file_record.get("before_sha256"), 96)
            matches_before = bool(current_sha and expected_before_sha and current_sha == expected_before_sha)
            if not matches_before:
                item_failed = True
            file_checks.append({
                "changed_file": changed_file,
                "current_sha256": current_sha,
                "expected_before_sha256": expected_before_sha,
                "matches_before": matches_before,
            })
        status = "fail" if item_failed else "pass"
        if item_failed:
            failed_items.append(item)
        verification_items.append({
            "id": f"real_run_rollback_post_verification:{index}",
            "apply_item_id": _safe_text(item.get("apply_item_id"), 160),
            "worker_id": _safe_text(item.get("worker_id"), 160),
            "status": status,
            "summary": "当前文件已回到应用前 before 快照。" if status == "pass" else "当前文件未完全匹配应用前 before 快照。",
            "changed_files": [_safe_text(file, 420) for file in _list_value(item.get("changed_files")) if _safe_text(file, 420)],
            "file_checks": file_checks,
        })
        rolled_back_records.append({
            "apply_item_id": _safe_text(item.get("apply_item_id"), 160),
            "worker_id": _safe_text(item.get("worker_id"), 160),
            "changed_files": [_safe_text(file, 420) for file in _list_value(item.get("changed_files")) if _safe_text(file, 420)],
            "file_checks": file_checks,
        })

    verdict = "pass" if not failed_items else "fail"
    summary = (
        f"{len(rolled_items)} 条真实失败 run 回滚记录已验证恢复到 before 快照。"
        if verdict == "pass" else f"{len(failed_items)} 条真实失败 run 回滚记录未匹配 before 快照。"
    )
    run_id = _safe_text(rollback_report.get("run_id"), 160)
    path = _team_builder_repair_real_run_rollback_post_verification_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(rollback_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "verified_by": verified_by,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "counts": {
            "rolled_back": len(rolled_items),
            "verified": len(rolled_items) if verdict == "pass" else 0,
            "pending": 0,
            "failed": len(failed_items),
            "real_repo_writes": 0,
        },
        "quality_gates": [
            _test_gate(
                "real_run_rollback_file_state_restored",
                "目标文件已回到 before sha",
                "pass" if not failed_items else "fail",
                "所有回滚记录的当前文件 sha 都等于 rollback_to_sha。"
                if not failed_items else f"{len(failed_items)} 条回滚记录不匹配。",
                [_safe_text(item.get("apply_item_id"), 160) for item in failed_items][:5],
            ),
            _test_gate(
                "real_run_post_rollback_is_read_only",
                "回滚后验证只读",
                "pass",
                "本验证只写 material，不执行 apply/rollback，也不改目标文件。",
                ["real_repo_writes=0"],
            ),
        ],
        "verification_items": verification_items,
        "rolled_back_records": rolled_back_records,
        "source": {
            **(_dict_value(rollback_report.get("source"))),
            "rollback_execution_endpoint": "/api/team-builder-materialization/repair-real-run-rollback-execution/latest",
            "rollback_post_verification_material": str(path.relative_to(_repo_root())) if path else "",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_real_run_closure_rollup_report() -> dict[str, Any]:
    def int_count(report: dict[str, Any], key: str) -> int:
        value = _dict_value(report.get("counts")).get(key)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def stage(
        stage_id: str,
        name: str,
        status: str,
        summary: str,
        endpoint: str,
        counts: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": stage_id,
            "name": name,
            "status": status,
            "summary": _safe_text(summary, 800),
            "endpoint": endpoint,
            "counts": counts or {},
        }

    candidate_scan = _team_builder_repair_real_run_candidate_scan_report()
    replay_plan = _team_builder_repair_real_run_replay_plan_report()
    diff_preview = _team_builder_repair_real_run_diff_preview_report()
    diff_review = _team_builder_repair_real_run_diff_review_report()
    apply_gate = _team_builder_repair_real_run_apply_gate_report()
    apply_preview = _team_builder_repair_real_run_apply_preview_report()
    apply_execution = _team_builder_real_run_apply_execution_report()
    apply_rehearsal = _team_builder_real_run_apply_rehearsal_report()
    post_apply = _team_builder_real_run_post_apply_verification_report()
    outcome = _team_builder_real_run_outcome_reconciliation_report()
    rollback_readiness = _team_builder_real_run_rollback_readiness_report()
    rollback_execution = _team_builder_real_run_rollback_execution_report()
    rollback_post = _team_builder_real_run_rollback_post_verification_report()

    run_id = (
        _safe_text(candidate_scan.get("run_id"), 160)
        or _safe_text(apply_execution.get("run_id"), 160)
        or _safe_text(rollback_post.get("run_id"), 160)
    )
    team_name = (
        _safe_text(candidate_scan.get("team_name"), 160)
        or _safe_text(apply_execution.get("team_name"), 160)
        or _safe_text(rollback_post.get("team_name"), 160)
    )
    failure_candidates = int_count(candidate_scan, "failure_candidates")
    validation_gap_runs = int_count(candidate_scan, "validation_gap_runs")
    repair_required = int_count(replay_plan, "repair_required")
    diff_ready = int_count(diff_preview, "diff_ready")
    ready_for_review = int_count(diff_review, "ready_for_review")
    apply_ready = int_count(apply_gate, "apply_ready")
    preview_ready = int_count(apply_preview, "preview")
    ready_to_apply = int_count(apply_execution, "ready")
    applied = int_count(apply_execution, "applied")
    apply_real_writes = int_count(apply_execution, "real_writes")
    rehearsal_ready = int_count(apply_rehearsal, "ready")
    rehearsal_passed = int_count(apply_rehearsal, "passed")
    rehearsal_blocked = int_count(apply_rehearsal, "blocked")
    post_apply_pending = int_count(post_apply, "pending")
    post_apply_failed = int_count(post_apply, "failed")
    verified = int_count(post_apply, "verified")
    reconciled = int_count(outcome, "reconciled")
    persistent_findings = int_count(outcome, "persistent_findings")
    introduced_findings = int_count(outcome, "introduced_findings")
    pending_reconciliation = int_count(outcome, "pending_verification")
    rollback_ready = int_count(rollback_readiness, "rollback_ready")
    rollback_stale = int_count(rollback_readiness, "stale_or_mismatch") + int_count(rollback_execution, "stale_or_mismatch")
    rollback_blocked = int_count(rollback_readiness, "blocked") + int_count(rollback_execution, "blocked")
    rolled_back = int_count(rollback_execution, "rolled_back")
    rollback_real_writes = int_count(rollback_execution, "real_repo_writes")
    rollback_post_pending = int_count(rollback_post, "pending")
    rollback_post_failed = int_count(rollback_post, "failed")
    rollback_verified = int_count(rollback_post, "verified")

    candidate_status = "no_failure"
    if failure_candidates:
        candidate_status = "failure_candidate"
    elif validation_gap_runs:
        candidate_status = "validation_gap"

    plan_status = "not_needed"
    if repair_required:
        plan_status = "repair_required"

    review_status = "not_ready"
    if ready_for_review and apply_ready and preview_ready:
        review_status = "reviewed_and_previewed"
    elif diff_ready or ready_for_review or apply_ready or preview_ready:
        review_status = "review_or_preview_pending"

    apply_status = "not_started"
    if applied:
        apply_status = "applied"
    elif ready_to_apply or preview_ready:
        apply_status = "ready_for_explicit_apply"

    post_apply_status = "not_started"
    if post_apply_failed:
        post_apply_status = "failed"
    elif post_apply_pending or pending_reconciliation:
        post_apply_status = "pending_verification"
    elif verified or reconciled:
        post_apply_status = "verified"
    elif _safe_text(post_apply.get("verdict"), 80) == "awaiting_apply":
        post_apply_status = "awaiting_apply"

    outcome_status = "not_started"
    if introduced_findings or persistent_findings:
        outcome_status = "regression_or_persistent"
    elif pending_reconciliation:
        outcome_status = "pending_verification"
    elif reconciled:
        outcome_status = "reconciled"
    elif _safe_text(outcome.get("verdict"), 80) == "awaiting_apply":
        outcome_status = "awaiting_apply"

    rollback_status = "not_started"
    if rollback_stale or rollback_post_failed:
        rollback_status = "stale_or_failed"
    elif rollback_post_pending:
        rollback_status = "pending_post_verification"
    elif rollback_verified or rolled_back:
        rollback_status = "verified"
    elif rollback_ready:
        rollback_status = "ready_for_explicit_rollback"
    elif rollback_blocked:
        rollback_status = "blocked"
    elif _safe_text(rollback_readiness.get("verdict"), 80) == "awaiting_apply":
        rollback_status = "awaiting_apply"

    stages = [
        stage(
            "real_run_candidate",
            "真实失败 run 候选",
            candidate_status,
            _safe_text(candidate_scan.get("summary"), 800),
            "/api/team-builder-materialization/repair-real-run-candidate-scan/latest",
            {"failure_candidates": failure_candidates, "validation_gap_runs": validation_gap_runs},
        ),
        stage(
            "real_run_replay_plan",
            "失败 run 消解计划",
            plan_status,
            _safe_text(replay_plan.get("summary"), 800),
            "/api/team-builder-materialization/repair-real-run-replay-plan/latest",
            {"repair_required": repair_required},
        ),
        stage(
            "real_run_diff_review",
            "diff 审阅与应用预览",
            review_status,
            _safe_text(diff_review.get("summary") or apply_preview.get("summary"), 800),
            "/api/team-builder-materialization/repair-real-run-diff-review/latest",
            {"diff_ready": diff_ready, "ready_for_review": ready_for_review, "apply_ready": apply_ready, "preview_ready": preview_ready},
        ),
        stage(
            "real_run_apply",
            "显式应用",
            apply_status,
            _safe_text(apply_execution.get("summary"), 800),
            "/api/team-builder-materialization/repair-real-run-apply-execution/latest",
            {"ready": ready_to_apply, "applied": applied, "real_writes": apply_real_writes},
        ),
        stage(
            "real_run_post_apply",
            "应用后回放验证",
            post_apply_status,
            _safe_text(post_apply.get("summary"), 800),
            "/api/team-builder-materialization/repair-real-run-post-apply-verification/latest",
            {"verified": verified, "pending": post_apply_pending, "failed": post_apply_failed},
        ),
        stage(
            "real_run_outcome",
            "修复结果对账",
            outcome_status,
            _safe_text(outcome.get("summary"), 800),
            "/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest",
            {"reconciled": reconciled, "introduced_findings": introduced_findings, "persistent_findings": persistent_findings},
        ),
        stage(
            "real_run_rollback",
            "回滚与回滚后验证",
            rollback_status,
            _safe_text(rollback_post.get("summary") or rollback_execution.get("summary") or rollback_readiness.get("summary"), 800),
            "/api/team-builder-materialization/repair-real-run-rollback-post-verification/latest",
            {"rollback_ready": rollback_ready, "rolled_back": rolled_back, "verified": rollback_verified, "real_writes": rollback_real_writes},
        ),
    ]

    repair_actions_by_worker: dict[str, dict[str, Any]] = {}
    for raw_action in _list_value(replay_plan.get("repair_actions")):
        action = _dict_value(raw_action)
        worker_id = _safe_text(action.get("worker_id"), 160)
        if worker_id and worker_id not in repair_actions_by_worker:
            repair_actions_by_worker[worker_id] = action
    findings_by_worker: dict[str, dict[str, Any]] = {}
    for raw_finding in _list_value(replay_plan.get("findings")):
        finding = _dict_value(raw_finding)
        worker_id = _safe_text(finding.get("target_id") or finding.get("worker_id"), 160)
        if worker_id and worker_id not in findings_by_worker:
            findings_by_worker[worker_id] = finding
    review_by_worker: dict[str, dict[str, Any]] = {}
    for raw_review in _list_value(diff_review.get("review_items")):
        review_item = _dict_value(raw_review)
        worker_id = _safe_text(review_item.get("worker_id"), 160)
        if worker_id and worker_id not in review_by_worker:
            review_by_worker[worker_id] = review_item
    diff_by_worker: dict[str, dict[str, Any]] = {}
    for raw_diff in _list_value(diff_preview.get("diff_records")):
        diff_record = _dict_value(raw_diff)
        worker_id = _safe_text(diff_record.get("worker_id"), 160)
        if worker_id and worker_id not in diff_by_worker:
            diff_by_worker[worker_id] = diff_record

    preview_by_apply_item: dict[str, dict[str, Any]] = {}
    for raw_item in _list_value(apply_preview.get("preview_items")):
        preview_item = _dict_value(raw_item)
        preview_apply_item_id = _safe_text(preview_item.get("apply_item_id"), 160)
        if preview_apply_item_id:
            preview_by_apply_item[preview_apply_item_id] = preview_item
    approval_items: list[dict[str, Any]] = []
    for item in [_dict_value(item) for item in _list_value(apply_execution.get("apply_items"))]:
        if _safe_text(item.get("status"), 120) != "ready_for_explicit_apply":
            continue
        apply_item_id = _safe_text(item.get("apply_item_id"), 160)
        worker_id = _safe_text(item.get("worker_id"), 160)
        preview_item = preview_by_apply_item.get(apply_item_id, {})
        repair_action = repair_actions_by_worker.get(worker_id, {})
        finding = findings_by_worker.get(worker_id, {})
        review_item = review_by_worker.get(worker_id, {})
        diff_record = diff_by_worker.get(worker_id, {})
        required_fields = [
            _safe_text(field, 120)
            for field in (
                _list_value(repair_action.get("required_input_fields"))
                or _list_value(review_item.get("required_input_fields"))
                or _list_value(diff_record.get("required_input_fields"))
            )
            if _safe_text(field, 120)
        ]
        approval_items.append({
            "apply_item_id": apply_item_id,
            "worker_id": worker_id,
            "status": _safe_text(item.get("status"), 120),
            "summary": _safe_text(item.get("summary"), 520),
            "changed_files": [_safe_text(path, 520) for path in _list_value(item.get("changed_files"))],
            "file_count": int(item.get("file_count") or 0),
            "file_records": [
                {
                    "changed_file": _safe_text(record.get("changed_file"), 520),
                    "before_sha256": _safe_text(record.get("before_sha256"), 96),
                    "after_sha256": _safe_text(record.get("after_sha256"), 96),
                    "current_sha256": _safe_text(record.get("current_sha256"), 96),
                    "before_preview_file": _safe_text(record.get("before_preview_file"), 520),
                    "after_preview_file": _safe_text(record.get("after_preview_file"), 520),
                }
                for record in [_dict_value(record) for record in _list_value(item.get("file_records"))]
            ],
            "required_confirmations": [_safe_text(token, 160) for token in _list_value(item.get("required_confirmations"))],
            "required_input_fields": required_fields,
            "problem_statement": _safe_text(finding.get("observation"), 620) or (
                f"{worker_id} 没有读取输入 material 的必需字段: {', '.join(required_fields)}。"
                if required_fields else f"{worker_id} 有真实失败 run 的 code review finding。"
            ),
            "impact_summary": _safe_text(finding.get("implication"), 620) or "真实 generated team 的 material 血缘可能缺失输入文件信息。",
            "intended_change": _safe_text(repair_action.get("proposed_change"), 620) or "显式读取输入 material 的必需字段，并把读到的文件信息纳入 worker 输出。",
            "change_summary": [_safe_text(change, 260) for change in _list_value(diff_record.get("change_summary")) if _safe_text(change, 260)],
            "review_questions": [_safe_text(question, 320) for question in _list_value(review_item.get("review_questions")) if _safe_text(question, 320)],
            "risk_notes": [_safe_text(note, 320) for note in _list_value(review_item.get("risk_notes")) if _safe_text(note, 320)],
            "evidence_links": [
                {
                    "label": "真实失败 run 候选扫描",
                    "kind": "endpoint",
                    "target": "/api/team-builder-materialization/repair-real-run-candidate-scan/latest",
                    "summary": "确认它是失败候选，不是普通验证缺口。",
                },
                {
                    "label": "code review finding",
                    "kind": "material",
                    "target": _safe_text(_dict_value(replay_plan.get("source")).get("code_review_report"), 520),
                    "summary": "原始失败依据，包含 input_key_not_read 与 required_not_read 字段。",
                },
                {
                    "label": "消解计划",
                    "kind": "endpoint",
                    "target": "/api/team-builder-materialization/repair-real-run-replay-plan/latest",
                    "summary": "把失败 finding 消解成 repair_required action。",
                },
                {
                    "label": "diff 审阅门",
                    "kind": "endpoint",
                    "target": "/api/team-builder-materialization/repair-real-run-diff-review/latest",
                    "summary": "复查目标范围、before/after sha、diff sha 和审阅问题。",
                },
                {
                    "label": "应用预览 after 文件",
                    "kind": "file",
                    "target": _safe_text((_list_value(preview_item.get("after_preview_files")) or [""])[0], 520),
                    "summary": "批准后会写入目标文件的 after 内容来源。",
                },
            ],
            "post_apply_verification": [_safe_text(step, 320) for step in _list_value(preview_item.get("post_apply_verification"))],
            "rollback_requirement": _safe_text(preview_item.get("rollback_requirement"), 520),
        })
    decision_dossier = {
        "title": "审批前决策说明",
        "decision_question": (
            f"是否批准把 {approval_items[0]['worker_id']} 的真实失败 run 修复写入 scratch generated package？"
            if approval_items else "当前没有待批准的真实失败 run 修复。"
        ),
        "why_now": (
            "前置链路已经完成候选扫描、code review finding 消解、diff 预览、审阅门、应用门和 before/after 文件集预览；剩余动作必须由显式 POST 才能继续。"
            if approval_items else "没有 ready_for_explicit_apply 项，不需要审批。"
        ),
        "write_scope": (
            "仅限 _scratch/team_builder_real_material_validation/.../code_package_files 下的真实 TeamBuilder run 生成包，不触碰业务源码或 TeamBuilder 框架源码。"
            if approval_items else ""
        ),
        "expected_effect": (
            "应用后应让 input_key_not_read / required_not_read 类 finding 消解，并让 material_usage_mapper 读到输入 bundle.files 中的 workspace 文件信息。"
            if approval_items else ""
        ),
        "do_not_use_as_completion": "审批包只是 apply 前证据；没有 POST apply、应用后回放、结果对账和回滚后验证前，不能宣称完整闭环完成。",
        "post_approval_sequence": [
            "POST 显式 apply，并记录 before/after sha、执行人、理由和确认 token。",
            "POST 应用后回放验证，复查 required 字段读取、语法、导入和 worker smoke。",
            "读取修复结果对账，确认原始 finding 已消解且没有新增失败。",
            "按需要进入显式 rollback，并执行回滚后验证。",
        ] if approval_items else [],
        "human_review_focus": [
            "required_input_fields 是否确实代表该 worker 必须读取的 material 字段。",
            "after 预览是否只补充读取和血缘记录，不改变外部接口或业务含义。",
            "当前 sha 是否仍等于 before sha，避免把过期 diff 写入目标文件。",
        ] if approval_items else [],
    }
    first_apply_item_id = approval_items[0]["apply_item_id"] if approval_items else ""
    execution_playbook = {
        "available": bool(approval_items),
        "status": "awaiting_explicit_approval" if approval_items else "not_ready",
        "title": "批准后的执行剧本",
        "summary": (
            "这是一张只读顺序表；它说明批准后每一步该调用哪个端点、是否写目标文件、预期看什么结果。"
            if approval_items else "当前没有待执行项，暂不生成执行剧本。"
        ),
        "safety_note": "只有 apply 和 rollback 两个 POST 会写目标文件；验证、对账和总览 GET/POST 验证步骤不得写目标文件。",
        "steps": [
            {
                "id": "apply_real_run_patch",
                "order": 1,
                "method": "POST",
                "endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/execute",
                "title": "显式应用真实失败 run 修复",
                "summary": "写入 after 预览内容，并记录 before/after sha、执行人、理由和确认 token。",
                "writes_target_files": True,
                "can_execute_now": bool(approval_items),
                "required_confirmations": [
                    "confirm_real_run_diff_review",
                    "confirm_real_run_file_set_write",
                    "confirm_post_apply_replay_required",
                ],
                "payload_template": {
                    "apply": True,
                    "apply_item_id": first_apply_item_id,
                    "applied_by": "<审批人或执行代理>",
                    "reason": "<批准这次真实失败 run 修复的原因>",
                    "confirmations": [
                        "confirm_real_run_diff_review",
                        "confirm_real_run_file_set_write",
                        "confirm_post_apply_replay_required",
                    ],
                },
                "expected_next_verdict": "applied",
            },
            {
                "id": "verify_after_apply",
                "order": 2,
                "method": "POST",
                "endpoint": "/api/team-builder-materialization/repair-real-run-post-apply-verification/execute",
                "title": "应用后回放验证",
                "summary": "复查 after sha、required 字段读取、语法、导入和 worker smoke。",
                "writes_target_files": False,
                "can_execute_now": bool(applied),
                "required_confirmations": ["confirm_real_run_post_apply_replay"],
                "payload_template": {
                    "verify": True,
                    "verified_by": "<验证人或执行代理>",
                    "reason": "<验证真实失败 run 应用后状态的原因>",
                    "confirmations": ["confirm_real_run_post_apply_replay"],
                },
                "expected_next_verdict": "pass_or_warning",
            },
            {
                "id": "review_reconciliation",
                "order": 3,
                "method": "GET",
                "endpoint": "/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest",
                "title": "读取修复结果对账",
                "summary": "确认原始 finding 是否消解、是否新增失败、是否还有残留 finding。",
                "writes_target_files": False,
                "can_execute_now": bool(applied),
                "required_confirmations": [],
                "payload_template": {},
                "expected_next_verdict": "pass_or_warning",
            },
            {
                "id": "rollback_if_needed",
                "order": 4,
                "method": "POST",
                "endpoint": "/api/team-builder-materialization/repair-real-run-rollback-execution/execute",
                "title": "按需显式回滚",
                "summary": "只有在需要撤回或验证回滚链路时执行；会把目标文件恢复到 before 内容。",
                "writes_target_files": True,
                "can_execute_now": bool(rollback_ready),
                "required_confirmations": ["confirm_real_run_file_rollback"],
                "payload_template": {
                    "rollback": True,
                    "apply_item_id": first_apply_item_id,
                    "rolled_back_by": "<回滚人或执行代理>",
                    "reason": "<回滚或验证回滚链路的原因>",
                    "confirmations": ["confirm_real_run_file_rollback"],
                },
                "expected_next_verdict": "rolled_back",
            },
            {
                "id": "verify_after_rollback",
                "order": 5,
                "method": "POST",
                "endpoint": "/api/team-builder-materialization/repair-real-run-rollback-post-verification/execute",
                "title": "回滚后验证",
                "summary": "只验证当前目标文件是否回到 before sha，不执行 apply 或 rollback。",
                "writes_target_files": False,
                "can_execute_now": bool(rolled_back),
                "required_confirmations": ["confirm_real_run_post_rollback_verification"],
                "payload_template": {
                    "verify": True,
                    "verified_by": "<验证人或执行代理>",
                    "reason": "<验证真实失败 run 回滚后的原因>",
                    "confirmations": ["confirm_real_run_post_rollback_verification"],
                },
                "expected_next_verdict": "pass",
            },
        ] if approval_items else [],
    }
    expected_confirmation_tokens = {
        "confirm_real_run_diff_review",
        "confirm_real_run_file_set_write",
        "confirm_post_apply_replay_required",
    }
    all_file_records = [
        _dict_value(record)
        for approval_item in approval_items
        for record in _list_value(approval_item.get("file_records"))
        if _dict_value(record)
    ]
    current_matches_before = bool(all_file_records) and all(
        _safe_text(record.get("current_sha256"), 96)
        and _safe_text(record.get("current_sha256"), 96) == _safe_text(record.get("before_sha256"), 96)
        for record in all_file_records
    )
    after_previews_match = bool(all_file_records)
    before_snapshots_match = bool(all_file_records)
    target_scope_safe = bool(all_file_records)
    target_files_exist = bool(all_file_records)
    for record in all_file_records:
        changed_file = _safe_text(record.get("changed_file"), 520).replace("\\", "/")
        target_scope_safe = target_scope_safe and changed_file.startswith("_scratch/team_builder_real_material_validation/")
        target_path = (_repo_root() / changed_file).resolve() if changed_file else _repo_root().resolve()
        try:
            target_path.relative_to(_repo_root().resolve())
        except (OSError, ValueError):
            target_scope_safe = False
        target_files_exist = target_files_exist and target_path.is_file()
        before_preview_path = _repo_root() / _safe_text(record.get("before_preview_file"), 520)
        after_preview_path = _repo_root() / _safe_text(record.get("after_preview_file"), 520)
        before_sha = _safe_text(record.get("before_sha256"), 96)
        after_sha = _safe_text(record.get("after_sha256"), 96)
        before_snapshots_match = before_snapshots_match and before_preview_path.is_file() and bool(before_sha) and _team_builder_file_sha256(before_preview_path) == before_sha
        after_previews_match = after_previews_match and after_preview_path.is_file() and bool(after_sha) and _team_builder_file_sha256(after_preview_path) == after_sha
    confirmations_declared = all(
        expected_confirmation_tokens.issubset(set(_safe_text(token, 160) for token in _list_value(item.get("required_confirmations"))))
        for item in approval_items
    ) if approval_items else False
    rehearsal_ready = bool(approval_items) and rehearsal_passed == len(approval_items) and rehearsal_blocked == 0
    rehearsal_required_field_checks = int_count(apply_rehearsal, "required_field_checks")
    rehearsal_missing_required_fields = int_count(apply_rehearsal, "missing_required_fields")
    rehearsal_semantic_ready = rehearsal_ready and rehearsal_required_field_checks > 0 and rehearsal_missing_required_fields == 0
    preflight_conditions = [
        {
            "id": "ready_item_present",
            "name": "存在待应用项",
            "status": "pass" if approval_items else "warning",
            "summary": f"当前 ready_for_explicit_apply 项 {len(approval_items)} 个。",
            "evidence": [f"ready_to_apply={ready_to_apply}"],
        },
        {
            "id": "not_already_applied",
            "name": "尚未应用",
            "status": "pass" if not applied else "fail",
            "summary": "没有已应用记录，可以进入审批决策。" if not applied else "已有应用记录，不能重复按同一审批包 apply。",
            "evidence": [f"applied={applied}"],
        },
        {
            "id": "target_scope_safe",
            "name": "目标范围安全",
            "status": "pass" if target_scope_safe else "fail",
            "summary": "所有目标都限制在真实 run 的 scratch generated package 范围内。" if target_scope_safe else "存在目标越界或不在允许范围内。",
            "evidence": [_safe_text(record.get("changed_file"), 520) for record in all_file_records[:5]],
        },
        {
            "id": "current_matches_before",
            "name": "当前内容等于 before",
            "status": "pass" if current_matches_before else "fail",
            "summary": "当前目标文件 sha 仍等于 before sha，可以应用当前 after 预览。" if current_matches_before else "当前目标文件已漂移，必须重新生成预览。",
            "evidence": [f"{_safe_text(record.get('current_sha256'), 12)}={_safe_text(record.get('before_sha256'), 12)}" for record in all_file_records[:5]],
        },
        {
            "id": "after_preview_verified",
            "name": "after 预览可校验",
            "status": "pass" if after_previews_match else "fail",
            "summary": "after 预览文件存在且 sha 与记录一致。" if after_previews_match else "after 预览缺失或 sha 不一致。",
            "evidence": [_safe_text(record.get("after_preview_file"), 520) for record in all_file_records[:5]],
        },
        {
            "id": "rollback_snapshot_verified",
            "name": "回滚快照可校验",
            "status": "pass" if before_snapshots_match else "fail",
            "summary": "before 预览文件存在且 sha 与记录一致，后续可回滚。" if before_snapshots_match else "before 快照缺失或 sha 不一致，不能安全回滚。",
            "evidence": [_safe_text(record.get("before_preview_file"), 520) for record in all_file_records[:5]],
        },
        {
            "id": "required_confirmations_declared",
            "name": "确认 token 已声明",
            "status": "pass" if confirmations_declared else "fail",
            "summary": "apply 所需三个确认 token 已在待应用项中声明。" if confirmations_declared else "待应用项缺少必要确认 token。",
            "evidence": sorted(expected_confirmation_tokens),
        },
        {
            "id": "apply_rehearsal_passed",
            "name": "应用前演练通过",
            "status": "pass" if rehearsal_ready else "warning",
            "summary": "独立 scratch 副本已完成 apply 与 rollback 演练。" if rehearsal_ready else "应用前演练未通过或不可用。",
            "evidence": [f"passed={rehearsal_passed}", f"blocked={rehearsal_blocked}"],
        },
    ]
    preflight_conditions.append({
        "id": "semantic_rehearsal_passed",
        "name": "必读字段演练已回放",
        "status": "pass" if rehearsal_semantic_ready else "fail" if rehearsal_missing_required_fields else "warning",
        "summary": "after 预览已经在演练中确认会读取 required 字段。" if rehearsal_semantic_ready else "演练尚未证明 after 预览会读取 required 字段。",
        "evidence": [f"required_field_checks={rehearsal_required_field_checks}", f"missing_required_fields={rehearsal_missing_required_fields}"],
    })
    preflight_blockers = [
        _safe_text(condition.get("summary"), 520)
        for condition in preflight_conditions
        if _safe_text(condition.get("status"), 40) != "pass"
    ]
    post_preflight = {
        "available": bool(approval_items),
        "status": "ready_to_post" if approval_items and not preflight_blockers else "blocked" if approval_items else "not_ready",
        "summary": (
            "POST apply 前置检查通过；仍需人工明确批准并携带 required confirmations。"
            if approval_items and not preflight_blockers
            else f"POST apply 前置检查仍有 {len(preflight_blockers)} 个阻断或警告。"
            if approval_items else "当前没有待应用项，前置检查未开启。"
        ),
        "conditions": preflight_conditions,
        "blockers": preflight_blockers,
    }
    auto_apply_policy = _team_builder_real_run_build_auto_apply_policy(
        run_id=run_id,
        team_name=team_name,
        approval_items=approval_items,
        post_preflight=post_preflight,
        apply_rehearsal=apply_rehearsal,
        ready_to_apply=ready_to_apply,
        applied=applied,
    )
    approval_packet = {
        "available": bool(approval_items),
        "status": "ready_for_decision" if approval_items else "not_ready",
        "title": "真实失败 run 显式应用审批包",
        "summary": (
            f"{len(approval_items)} 个真实失败 run 应用项已具备显式审批条件；审批后才允许 POST 写目标文件。"
            if approval_items else "当前没有可审批的真实失败 run 应用项。"
        ),
        "post_endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/execute",
        "approval_requirements": ["apply=true", "apply_item_id", "applied_by", "reason"],
        "required_confirmations": [
            "confirm_real_run_diff_review",
            "confirm_real_run_file_set_write",
            "confirm_post_apply_replay_required",
        ] if approval_items else [],
        "payload_template": {
            "apply": True,
            "apply_item_id": approval_items[0]["apply_item_id"] if approval_items else "",
            "applied_by": "<审批人或执行代理>",
            "reason": "<批准这次真实失败 run 修复的原因>",
            "confirmations": [
                "confirm_real_run_diff_review",
                "confirm_real_run_file_set_write",
                "confirm_post_apply_replay_required",
            ],
        } if approval_items else {},
        "decision_dossier": decision_dossier,
        "post_preflight": post_preflight,
        "auto_apply_policy": auto_apply_policy,
        "apply_rehearsal": {
            "available": bool(apply_rehearsal.get("available")),
            "verdict": _safe_text(apply_rehearsal.get("verdict"), 120),
            "summary": _safe_text(apply_rehearsal.get("summary"), 700),
            "counts": {
                "ready": rehearsal_ready,
                "passed": rehearsal_passed,
                "blocked": rehearsal_blocked,
                "scratch_writes": int_count(apply_rehearsal, "scratch_writes"),
                "real_repo_writes": int_count(apply_rehearsal, "real_repo_writes"),
                "required_field_checks": rehearsal_required_field_checks,
                "missing_required_fields": rehearsal_missing_required_fields,
                "files_without_required_contract": int_count(apply_rehearsal, "files_without_required_contract"),
            },
            "material": _safe_text(_dict_value(apply_rehearsal.get("source")).get("apply_rehearsal_material"), 520),
            "rehearsal_root": _safe_text(_dict_value(apply_rehearsal.get("source")).get("rehearsal_root"), 520),
        },
        "execution_playbook": execution_playbook,
        "items": approval_items,
        "safety_checks": [
            "目标文件必须限制在真实失败 run 的 _scratch/team_builder_real_material_validation/.../code_package_files 范围内。",
            "POST 执行前必须复查当前目标文件 sha 等于 before sha。",
            "POST 写入内容必须来自已生成的 after 预览文件，且 after sha 匹配。",
            "应用后必须执行真实失败 run 应用后回放验证和结果对账。",
            "应用记录必须保留 before sha 和 before 预览，后续才能显式回滚。",
        ],
        "safety_note": "审批包和总览只读；只有显式 POST execute 才可能写目标文件。",
    }

    next_actions: list[dict[str, Any]] = []
    if failure_candidates and repair_required and ready_to_apply and not applied:
        next_actions.append({
            "id": "execute_real_run_apply_with_confirmations",
            "title": "等待你批准真实失败 run 显式应用",
            "summary": "diff、审阅门和应用预览已经就绪；真正写目标文件必须由 POST execute 和确认 token 触发。",
            "endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/latest",
            "post_endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/execute",
            "required_confirmations": [
                "confirm_real_run_diff_review",
                "confirm_real_run_file_set_write",
                "confirm_post_apply_replay_required",
            ],
            "approval_requirements": [
                "apply=true",
                "apply_item_id",
                "applied_by",
                "reason",
            ],
            "safety_note": "只能由显式 POST 写入真实失败 run 的 scratch generated package；总览和 dashboard 页面刷新都不会写目标文件。",
        })
    elif applied and _safe_text(post_apply.get("verdict"), 80) in {"awaiting_replay_verification"}:
        next_actions.append({
            "id": "verify_real_run_after_apply",
            "title": "执行应用后回放验证",
            "summary": "已有真实应用记录，需要显式验证 after sha、required 字段读取、语法、导入和 worker smoke。",
            "endpoint": "/api/team-builder-materialization/repair-real-run-post-apply-verification/latest",
        })
    elif applied and (pending_reconciliation or _safe_text(outcome.get("verdict"), 80) == "awaiting_verification"):
        next_actions.append({
            "id": "review_real_run_outcome",
            "title": "查看应用后结果对账",
            "summary": "应用后验证已经可用，需要按原始 finding、required 字段和验证 gate 判断是否真的消解。",
            "endpoint": "/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest",
        })
    elif rollback_ready and not rolled_back:
        next_actions.append({
            "id": "rollback_real_run_if_needed",
            "title": "按需显式回滚真实失败 run 修复",
            "summary": "before 快照和当前 after sha 已可校验；是否回滚需要人工决策和 POST 确认。",
            "endpoint": "/api/team-builder-materialization/repair-real-run-rollback-execution/latest",
        })
    elif rolled_back and rollback_post_pending:
        next_actions.append({
            "id": "verify_real_run_rollback",
            "title": "执行回滚后验证",
            "summary": "已有回滚记录，需要显式确认当前目标文件已恢复到 before sha。",
            "endpoint": "/api/team-builder-materialization/repair-real-run-rollback-post-verification/latest",
        })
    else:
        next_actions.append({
            "id": "scan_more_real_runs",
            "title": "继续扫描真实 TeamBuilder run",
            "summary": "当前真实失败 run 安全闭环没有待执行动作时，继续扩大样本覆盖和泛化验证。",
            "endpoint": "/api/team-builder-materialization/repair-real-run-candidate-scan/latest",
        })

    fail_statuses = {"failed", "regression_or_persistent", "stale_or_failed", "blocked"}
    pending_statuses = {
        "failure_candidate",
        "repair_required",
        "review_or_preview_pending",
        "ready_for_explicit_apply",
        "pending_verification",
        "ready_for_explicit_rollback",
        "pending_post_verification",
    }
    failed_stages = [item for item in stages if _safe_text(item.get("status"), 80) in fail_statuses]
    pending_stages = [item for item in stages if _safe_text(item.get("status"), 80) in pending_statuses]
    if failed_stages:
        verdict = "blocked"
        summary = f"真实失败 run 修复闭环有 {len(failed_stages)} 个失败或阻断阶段；先处理对应阶段。"
    elif pending_stages:
        verdict = "action_required"
        summary = f"真实失败 run 修复闭环有 {len(pending_stages)} 个阶段需要审阅、显式执行或补验证。"
    else:
        verdict = "clean"
        summary = "当前真实失败 run 修复闭环没有待执行动作；可以扩大真实 run 样本。"

    path = _team_builder_repair_real_run_closure_rollup_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": team_name,
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "stages": len(stages),
            "pending_stages": len(pending_stages),
            "failed_stages": len(failed_stages),
            "failure_candidates": failure_candidates,
            "repair_required": repair_required,
            "ready_to_apply": ready_to_apply,
            "apply_rehearsal_passed": rehearsal_passed,
            "apply_rehearsal_blocked": rehearsal_blocked,
            "apply_rehearsal_required_fields": rehearsal_required_field_checks,
            "apply_rehearsal_missing_required_fields": rehearsal_missing_required_fields,
            "applied": applied,
            "apply_real_writes": apply_real_writes,
            "verified": verified,
            "reconciled": reconciled,
            "rollback_ready": rollback_ready,
            "rolled_back": rolled_back,
            "rollback_verified": rollback_verified,
            "rollback_real_writes": rollback_real_writes,
        },
        "quality_gates": [
            _test_gate(
                "real_run_repair_subreports_available",
                "真实 run 修复子报告可读取",
                "pass" if all(_dict_value(report_item).get("available") for report_item in [
                    candidate_scan,
                    replay_plan,
                    diff_preview,
                    diff_review,
                    apply_gate,
                    apply_preview,
                    apply_execution,
                    apply_rehearsal,
                    post_apply,
                    outcome,
                    rollback_readiness,
                    rollback_execution,
                    rollback_post,
                ]) else "warning",
                "总览已读取真实失败 run 的候选、消解、diff、应用、验证、对账、回滚和回滚后验证报告。",
                [],
            ),
            _test_gate(
                "real_run_apply_rehearsal_passed",
                "应用前演练通过",
                "pass" if not ready_to_apply or (rehearsal_passed == ready_to_apply and rehearsal_blocked == 0) else "warning",
                "待应用项已在独立 scratch 副本中完成 apply 和 rollback 演练。"
                if ready_to_apply and rehearsal_passed == ready_to_apply and rehearsal_blocked == 0
                else "没有待应用项，或演练仍有阻断项。",
                [f"ready={ready_to_apply}", f"rehearsal_passed={rehearsal_passed}", f"rehearsal_blocked={rehearsal_blocked}", f"required_fields={rehearsal_required_field_checks}", f"missing_required_fields={rehearsal_missing_required_fields}"],
            ),
            _test_gate(
                "real_run_writes_are_explicit",
                "真实写入只来自显式执行",
                "pass",
                "总览只读；真实应用和真实回滚分别只能由 explicit apply/rollback POST 记录证明。",
                [f"apply_real_writes={apply_real_writes}", f"rollback_real_writes={rollback_real_writes}"],
            ),
            _test_gate(
                "real_run_post_apply_closed",
                "应用后验证与对账闭合",
                "pass" if not applied or (post_apply_pending == 0 and post_apply_failed == 0 and pending_reconciliation == 0) else "warning",
                "尚未应用，或已应用记录完成应用后验证和结果对账。"
                if not applied or (post_apply_pending == 0 and post_apply_failed == 0 and pending_reconciliation == 0)
                else "存在已应用记录尚未完成应用后验证或结果对账。",
                [],
            ),
            _test_gate(
                "real_run_rollback_closed",
                "回滚后验证闭合",
                "pass" if not rolled_back or (rollback_post_pending == 0 and rollback_post_failed == 0) else "warning",
                "尚未回滚，或已回滚记录完成回滚后验证。"
                if not rolled_back or (rollback_post_pending == 0 and rollback_post_failed == 0)
                else "存在已回滚记录尚未完成回滚后验证。",
                [],
            ),
        ],
        "stages": stages,
        "approval_packet": approval_packet,
        "next_actions": next_actions,
        "source": {
            "real_run_closure_rollup_material": str(path.relative_to(_repo_root())) if path else "",
            "candidate_scan_endpoint": "/api/team-builder-materialization/repair-real-run-candidate-scan/latest",
            "apply_execution_endpoint": "/api/team-builder-materialization/repair-real-run-apply-execution/latest",
            "post_apply_verification_endpoint": "/api/team-builder-materialization/repair-real-run-post-apply-verification/latest",
            "outcome_reconciliation_endpoint": "/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest",
            "rollback_readiness_endpoint": "/api/team-builder-materialization/repair-real-run-rollback-readiness/latest",
            "rollback_execution_endpoint": "/api/team-builder-materialization/repair-real-run-rollback-execution/latest",
            "rollback_post_verification_endpoint": "/api/team-builder-materialization/repair-real-run-rollback-post-verification/latest",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _write_team_builder_file_set_trial_package(package_dir: Path) -> None:
    if package_dir.exists():
        _safe_remove_tree(package_dir, (_repo_root() / "_scratch").resolve())
    (package_dir / "workers").mkdir(parents=True, exist_ok=True)
    (package_dir / ".omni").mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text('"""TeamBuilder file-set repair trial package."""\n', encoding="utf-8")
    (package_dir / "workers" / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / ".omni" / "workspace.yaml").write_text(
        "workspace_id: team_builder_file_set_trial\n"
        "purpose: controlled generated worker file-set repair trial\n",
        encoding="utf-8",
    )
    (package_dir / "DESIGN.md").write_text(
        "# TeamBuilder 文件集修复实战样本\n\n"
        "这个包用于验证 generated worker 多文件补丁的应用、验证、对账、回滚和回滚后验证。它只写入 _scratch。\n",
        encoding="utf-8",
    )
    (package_dir / "formats.py").write_text(
        "from __future__ import annotations\n\n"
        "REPORT_STATUS = 'ready'\n"
        "REPORT_SCHEMA_VERSION = 1\n\n"
        "def normalize_status(value: str | None = None) -> str:\n"
        "    return value or REPORT_STATUS\n\n"
        "def register_formats(registry):\n"
        "    return registry\n",
        encoding="utf-8",
    )
    (package_dir / "team.py").write_text(
        "from __future__ import annotations\n\n"
        "from omnicompany.protocol.anchor import AnchorSpec, Route, RouteAction, ValidatorKind, ValidatorSpec, VerdictKind\n"
        "from omnicompany.protocol.team import NodeKind, NodeMaturity, TeamEdge, TeamNode, TeamSpec\n\n"
        "def build_team() -> TeamSpec:\n"
        "    node = TeamNode(\n"
        "        id='file_set_report_worker',\n"
        "        kind=NodeKind.ANCHOR,\n"
        "        maturity=NodeMaturity.GROWING,\n"
        "        anchor=AnchorSpec(\n"
        "            id='a_file_set_report_worker',\n"
        "            name='file_set_report_worker',\n"
        "            format_in='team_builder_file_set.input.observation_request',\n"
        "            format_out='team_builder_file_set.material.health_report',\n"
        "            validator=ValidatorSpec(id='v_file_set_report_worker', kind=ValidatorKind.HARD, description='文件集修复验证'),\n"
        "            routes={VerdictKind.PASS: Route(action=RouteAction.EMIT), VerdictKind.FAIL: Route(action=RouteAction.HALT)},\n"
        "        ),\n"
        "    )\n"
        "    return TeamSpec(\n"
        "        id='team_builder_file_set_trial',\n"
        "        name='team_builder_file_set_trial',\n"
        "        description='验证 generated worker 多文件修复闭环。',\n"
        "        entry='file_set_report_worker',\n"
        "        nodes=[node],\n"
        "        edges=[],\n"
        "        tags=['team_builder', 'repair_probe', 'file_set'],\n"
        "    )\n",
        encoding="utf-8",
    )
    (package_dir / "run.py").write_text(
        "from __future__ import annotations\n\n"
        "from omnicompany.packages.services._core.omnicompany import Worker\n"
        "from omnicompany.protocol.format import create_builtin_registry\n"
        "from .formats import register_formats\n"
        "from .workers.file_set_report_worker import FileSetReportWorker\n\n"
        "def build_bindings(input_dict: dict | None = None) -> dict[str, Worker]:\n"
        "    registry = create_builtin_registry()\n"
        "    register_formats(registry)\n"
        "    return {'file_set_report_worker': FileSetReportWorker()}\n",
        encoding="utf-8",
    )
    (package_dir / "workers" / "file_set_report_worker.py").write_text(
        "from __future__ import annotations\n\n"
        "from typing import Any, ClassVar\n\n"
        "from omnicompany.packages.services._core.omnicompany import Worker\n"
        "from omnicompany.protocol.anchor import Verdict, VerdictKind\n"
        "from ..formats import REPORT_SCHEMA_VERSION, normalize_status\n\n"
        "class FileSetReportWorker(Worker):\n"
        "    DESCRIPTION: ClassVar[str] = '文件集修复样本 worker。'\n"
        "    FORMAT_IN: ClassVar[str] = 'team_builder_file_set.input.observation_request'\n"
        "    FORMAT_OUT: ClassVar[str] = 'team_builder_file_set.material.health_report'\n"
        "    EXPECTED_STATUS: ClassVar[str] = 'ready-v2'\n"
        "    EXPECTED_SCHEMA_VERSION: ClassVar[int] = 2\n\n"
        "    def run(self, input_data: Any) -> Verdict:\n"
        "        status = normalize_status(None)\n"
        "        if status != self.EXPECTED_STATUS or REPORT_SCHEMA_VERSION != self.EXPECTED_SCHEMA_VERSION:\n"
        "            return Verdict(\n"
        "                kind=VerdictKind.FAIL,\n"
        "                output={'status': status, 'schema_version': REPORT_SCHEMA_VERSION},\n"
        "                diagnosis='file-set trial mismatch: worker expectation and format schema are not aligned',\n"
        "            )\n"
        "        return Verdict(\n"
        "            kind=VerdictKind.PASS,\n"
        "            output={'status': status, 'schema_version': REPORT_SCHEMA_VERSION, 'file_set_patch': True},\n"
        "            diagnosis='file-set trial passed after coordinated worker+format patch',\n"
        "        )\n",
        encoding="utf-8",
    )


def _team_builder_real_generated_file_set_trial_report() -> dict[str, Any]:
    latest_run, _reason = _team_builder_latest_run_dir()
    run_id = f"{latest_run.name}-file-set-trial" if latest_run else "standalone-file-set-trial"
    package_name = "team_builder_file_set_trial"
    trial_root = _repo_root() / "_scratch" / "team_builder_real_generated_file_set_trial" / run_id
    package_dir = trial_root / package_name
    materials_dir = trial_root / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)
    material_path = _team_builder_real_generated_file_set_trial_path(run_id)

    def smoke_status(payload: dict[str, Any]) -> str:
        return _safe_text(_dict_value(payload.get("result")).get("status"), 40)

    try:
        _write_team_builder_file_set_trial_package(package_dir)
        before_package_smoke = _run_generated_package_smoke(package_dir, package_name)
        before_worker_smoke = _run_generated_worker_run_smoke(package_dir, package_name)

        target_paths = [
            package_dir / "formats.py",
            package_dir / "workers" / "file_set_report_worker.py",
        ]
        before_texts = {path: path.read_text(encoding="utf-8") for path in target_paths}
        after_texts = dict(before_texts)
        after_texts[package_dir / "formats.py"] = before_texts[package_dir / "formats.py"].replace(
            "REPORT_SCHEMA_VERSION = 1",
            "REPORT_SCHEMA_VERSION = 2",
            1,
        )
        after_texts[package_dir / "workers" / "file_set_report_worker.py"] = before_texts[package_dir / "workers" / "file_set_report_worker.py"].replace(
            "EXPECTED_STATUS: ClassVar[str] = 'ready-v2'",
            "EXPECTED_STATUS: ClassVar[str] = 'ready'",
            1,
        )
        rel_paths = [str(path.relative_to(_repo_root())).replace("\\", "/") for path in target_paths]
        diff_text = "".join(
            _team_builder_diff_text(str(path.relative_to(_repo_root())).replace("\\", "/"), before_texts[path], after_texts[path])
            for path in target_paths
        )
        diff_blocks = _team_builder_split_unified_diff_by_file(diff_text)
        preview_root = _repo_root() / "_scratch" / "team_builder_repair_apply_preview" / run_id / "real_generated_file_set_trial"
        file_records: list[dict[str, Any]] = []
        for path in target_paths:
            rel = str(path.relative_to(_repo_root())).replace("\\", "/")
            block = diff_blocks.get(_team_builder_normalize_diff_file_path(rel), "")
            if not block:
                raise ValueError(f"缺少逐文件 diff 块: {rel}")
            applied = _team_builder_apply_unified_diff_to_text(before_texts[path], block)
            if applied != after_texts[path]:
                raise ValueError(f"逐文件 diff 应用结果与预期不一致: {rel}")
            before_preview = preview_root / "before" / rel
            after_preview = preview_root / "after" / rel
            before_preview.parent.mkdir(parents=True, exist_ok=True)
            after_preview.parent.mkdir(parents=True, exist_ok=True)
            before_preview.write_text(before_texts[path], encoding="utf-8")
            after_preview.write_text(after_texts[path], encoding="utf-8")
            file_records.append({
                "changed_file": rel,
                "before_sha256": _team_builder_file_sha256(before_preview),
                "after_sha256": _team_builder_file_sha256(after_preview),
                "before_preview_file": str(before_preview.relative_to(_repo_root())).replace("\\", "/"),
                "after_preview_file": str(after_preview.relative_to(_repo_root())).replace("\\", "/"),
                "diff_sha256": _team_builder_diff_sha256(block),
            })

        for path in target_paths:
            path.write_text(after_texts[path], encoding="utf-8")
        after_apply_worker_smoke = _run_generated_worker_run_smoke(package_dir, package_name)
        after_apply_hashes = {
            str(path.relative_to(_repo_root())).replace("\\", "/"): _team_builder_file_sha256(path)
            for path in target_paths
        }

        for path in target_paths:
            path.write_text(before_texts[path], encoding="utf-8")
        after_rollback_worker_smoke = _run_generated_worker_run_smoke(package_dir, package_name)
        after_rollback_hashes = {
            str(path.relative_to(_repo_root())).replace("\\", "/"): _team_builder_file_sha256(path)
            for path in target_paths
        }
        for record in file_records:
            changed_file = _safe_text(record.get("changed_file"), 320)
            record["after_apply_sha256"] = after_apply_hashes.get(changed_file, "")
            record["after_rollback_sha256"] = after_rollback_hashes.get(changed_file, "")

        package_ok = bool(_dict_value(before_package_smoke.get("result")).get("ok"))
        before_failed = smoke_status(before_worker_smoke) == "fail"
        applied_passed = smoke_status(after_apply_worker_smoke) == "pass"
        rollback_restored = (
            smoke_status(after_rollback_worker_smoke) == "fail"
            and all(record["after_rollback_sha256"] == record["before_sha256"] for record in file_records)
        )
        file_set_integrity = all(
            record["after_apply_sha256"] == record["after_sha256"]
            and record["after_rollback_sha256"] == record["before_sha256"]
            for record in file_records
        )
        verdict = "pass" if package_ok and before_failed and applied_passed and rollback_restored and file_set_integrity else "fail"
        gates = [
            _test_gate("generated_package_imports", "生成包可导入", "pass" if package_ok else "fail", "生成包 build_team/build_bindings 可运行。", []),
            _test_gate("failure_reproduced", "修复前失败可复现", "pass" if before_failed else "fail", "修复前 worker smoke 返回 fail，具备真实 repair 输入。", [f"before_status={smoke_status(before_worker_smoke)}"]),
            _test_gate("file_set_preview_ready", "文件集预览已展开", "pass" if len(file_records) == 2 else "fail", "两份目标文件都生成 before/after 预览和逐文件 diff sha。", rel_paths),
            _test_gate("file_set_apply_verified", "文件集应用后验证通过", "pass" if applied_passed and file_set_integrity else "fail", "两份目标文件写入 after 内容后，worker smoke 通过。", [f"after_apply_status={smoke_status(after_apply_worker_smoke)}"]),
            _test_gate("file_set_rollback_verified", "文件集回滚后恢复", "pass" if rollback_restored else "fail", "两份目标文件回滚到 before 内容，worker smoke 恢复到修复前失败状态。", [f"after_rollback_status={smoke_status(after_rollback_worker_smoke)}"]),
            _test_gate("scratch_only_scope", "只写 scratch generated 包", "pass", "本试验只写 _scratch 下的 generated package、预览和 material，不修改仓库业务源码。", ["real_repo_writes=0"]),
        ]
        report = {
            "available": True,
            "run_id": run_id,
            "team_name": package_name,
            "verdict": verdict,
            "summary": (
                "真实 generated worker 文件集试验通过：修复前失败、两文件补丁应用后通过、文件集回滚后恢复。"
                if verdict == "pass"
                else "真实 generated worker 文件集试验失败：请查看质量门和 smoke 结果。"
            ),
            "counts": {
                "changed_files": len(file_records),
                "files_previewed": len(file_records),
                "files_applied": sum(1 for record in file_records if record.get("after_apply_sha256") == record.get("after_sha256")),
                "files_rolled_back": sum(1 for record in file_records if record.get("after_rollback_sha256") == record.get("before_sha256")),
                "before_failures": 1 if before_failed else 0,
                "post_apply_passed": 1 if applied_passed else 0,
                "rollback_restored": 1 if rollback_restored else 0,
                "scratch_generated_writes": len(file_records) * 2,
                "real_repo_writes": 0,
            },
            "quality_gates": gates,
            "changed_files": rel_paths,
            "file_records": file_records,
            "smoke": {
                "before_package": _dict_value(before_package_smoke.get("result")),
                "before_worker": _dict_value(before_worker_smoke.get("result")),
                "after_apply_worker": _dict_value(after_apply_worker_smoke.get("result")),
                "after_rollback_worker": _dict_value(after_rollback_worker_smoke.get("result")),
            },
            "next_actions": [
                {
                    "id": "scan_real_run_repair_candidates",
                    "title": "扫描真实 TeamBuilder 失败 run 候选",
                    "summary": "受控 generated worker 文件集已经完成失败复现、应用验证和回滚验证；下一步转向真实 run 候选发现。",
                    "endpoint": "/api/team-builder-materialization/repair-real-run-candidate-scan/latest",
                }
            ],
            "source": {
                "trial_package_dir": str(package_dir.relative_to(_repo_root())).replace("\\", "/"),
                "repair_real_generated_file_set_trial_material": str(material_path.relative_to(_repo_root())) if material_path else "",
            },
        }
    except Exception as exc:
        report = {
            "available": True,
            "run_id": run_id,
            "team_name": package_name,
            "verdict": "fail",
            "summary": f"真实 generated worker 文件集试验设施失败：{type(exc).__name__}: {exc}",
            "counts": {
                "changed_files": 0,
                "files_previewed": 0,
                "files_applied": 0,
                "files_rolled_back": 0,
                "before_failures": 0,
                "post_apply_passed": 0,
                "rollback_restored": 0,
                "scratch_generated_writes": 0,
                "real_repo_writes": 0,
            },
            "quality_gates": [
                _test_gate("trial_infrastructure", "文件集试验设施可运行", "fail", f"{type(exc).__name__}: {exc}", []),
            ],
            "changed_files": [],
            "file_records": [],
            "smoke": {},
            "source": {
                "trial_package_dir": str(package_dir.relative_to(_repo_root())).replace("\\", "/"),
                "repair_real_generated_file_set_trial_material": str(material_path.relative_to(_repo_root())) if material_path else "",
            },
        }
    if material_path:
        try:
            material_path.parent.mkdir(parents=True, exist_ok=True)
            material_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _write_team_builder_repair_probe_package(package_dir: Path) -> None:
    if package_dir.exists():
        _safe_remove_tree(package_dir, (_repo_root() / "_scratch").resolve())
    (package_dir / "workers").mkdir(parents=True, exist_ok=True)
    (package_dir / ".omni").mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text('"""TeamBuilder repair probe package."""\n', encoding="utf-8")
    (package_dir / "workers" / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / ".omni" / "workspace.yaml").write_text(
        "workspace_id: team_builder_repair_probe\n"
        "purpose: controlled failure probe for doctor and repair readiness\n",
        encoding="utf-8",
    )
    (package_dir / "DESIGN.md").write_text(
        "# TeamBuilder 修复探针\n\n"
        "这个包只用于验证失败 worker 是否能进入 doctor finding 和 repair plan。它不是业务示例 team。\n",
        encoding="utf-8",
    )
    (package_dir / "formats.py").write_text(
        "from __future__ import annotations\n\n"
        "def register_formats(registry):\n"
        "    return registry\n",
        encoding="utf-8",
    )
    (package_dir / "team.py").write_text(
        "from __future__ import annotations\n\n"
        "from omnicompany.protocol.anchor import AnchorSpec, Route, RouteAction, ValidatorKind, ValidatorSpec, VerdictKind\n"
        "from omnicompany.protocol.team import NodeKind, NodeMaturity, TeamEdge, TeamNode, TeamSpec\n\n"
        "def build_team() -> TeamSpec:\n"
        "    node = TeamNode(\n"
        "        id='failure_probe_worker',\n"
        "        kind=NodeKind.ANCHOR,\n"
        "        maturity=NodeMaturity.GROWING,\n"
        "        anchor=AnchorSpec(\n"
        "            id='a_failure_probe_worker',\n"
        "            name='failure_probe_worker',\n"
        "            format_in='team_builder_probe.input.observation_request',\n"
        "            format_out='team_builder_probe.material.failed_report',\n"
        "            validator=ValidatorSpec(id='v_failure_probe_worker', kind=ValidatorKind.HARD, description='受控失败探针'),\n"
        "            routes={VerdictKind.PASS: Route(action=RouteAction.EMIT), VerdictKind.FAIL: Route(action=RouteAction.HALT)},\n"
        "        ),\n"
        "    )\n"
        "    return TeamSpec(\n"
        "        id='team_builder_repair_probe',\n"
        "        name='team_builder_repair_probe',\n"
        "        description='受控失败探针：验证 worker 运行失败能被 doctor 和 repair plan 消费。',\n"
        "        entry='failure_probe_worker',\n"
        "        nodes=[node],\n"
        "        edges=[],\n"
        "        tags=['team_builder', 'repair_probe'],\n"
        "    )\n",
        encoding="utf-8",
    )
    (package_dir / "run.py").write_text(
        "from __future__ import annotations\n\n"
        "from omnicompany.packages.services._core.omnicompany import Worker\n"
        "from omnicompany.protocol.format import create_builtin_registry\n"
        "from .formats import register_formats\n"
        "from .workers.failure_probe_worker import FailureProbeWorker\n\n"
        "def build_bindings(input_dict: dict | None = None) -> dict[str, Worker]:\n"
        "    registry = create_builtin_registry()\n"
        "    register_formats(registry)\n"
        "    return {'failure_probe_worker': FailureProbeWorker()}\n",
        encoding="utf-8",
    )
    (package_dir / "workers" / "failure_probe_worker.py").write_text(
        "from __future__ import annotations\n\n"
        "from typing import Any, ClassVar\n\n"
        "from omnicompany.packages.services._core.omnicompany import Worker\n"
        "from omnicompany.protocol.anchor import Verdict, VerdictKind\n\n"
        "class FailureProbeWorker(Worker):\n"
        "    DESCRIPTION: ClassVar[str] = '受控失败探针 worker。'\n"
        "    FORMAT_IN: ClassVar[str] = 'team_builder_probe.input.observation_request'\n"
        "    FORMAT_OUT: ClassVar[str] = 'team_builder_probe.material.failed_report'\n\n"
        "    def run(self, input_data: Any) -> Verdict:\n"
        "        return Verdict(\n"
        "            kind=VerdictKind.FAIL,\n"
        "            output={'probe': 'controlled_failure', 'received_keys': sorted(input_data.keys()) if isinstance(input_data, dict) else []},\n"
        "            diagnosis='controlled failure: repair probe worker returned FAIL on purpose',\n"
        "        )\n",
        encoding="utf-8",
    )


def _team_builder_repair_probe_report() -> dict[str, Any]:
    latest_run, _reason = _team_builder_latest_run_dir()
    run_id = f"{latest_run.name}-repair-probe" if latest_run else "standalone-repair-probe"
    input_mtime = 0.0
    for path in [Path(__file__), latest_run / "summary.json" if latest_run else None]:
        if path is None:
            continue
        try:
            input_mtime = max(input_mtime, path.stat().st_mtime)
        except OSError:
            pass
    cached = _TEAM_BUILDER_REPAIR_PROBE_CACHE.get(run_id)
    if cached and cached[0] >= input_mtime:
        return json.loads(json.dumps(cached[1], ensure_ascii=False))
    scratch_root = _repo_root() / "_scratch" / "team_builder_repair_probe" / run_id
    package_name = "team_builder_repair_probe"
    package_dir = scratch_root / package_name
    materials_dir = scratch_root / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)
    try:
        _write_team_builder_repair_probe_package(package_dir)
        package_smoke = _run_generated_package_smoke(package_dir, package_name)
        worker_smoke = _run_generated_worker_run_smoke(package_dir, package_name)
    except Exception as exc:
        report = {
            "available": True,
            "run_id": run_id,
            "team_name": package_name,
            "verdict": "fail",
            "summary": f"故障修复探针设施失败：{type(exc).__name__}: {exc}",
            "counts": {
                "captured_failures": 0,
                "doctor_findings": 0,
                "repair_required": 0,
                "validation_gap": 0,
                "auto_safe": 0,
            },
            "quality_gates": [
                _test_gate("probe_infrastructure", "探针设施可运行", "fail", f"{type(exc).__name__}: {exc}", []),
            ],
            "doctor_findings": [],
            "repair_plan": {"verdict": "unavailable", "counts": {"actions": 0}, "actions": []},
            "source": {"repair_probe_material": str((materials_dir / "team_repair_probe_report.json").relative_to(_repo_root()))},
        }
        try:
            (materials_dir / "team_repair_probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return report

    package_payload = _dict_value(package_smoke.get("result"))
    worker_payload = _dict_value(worker_smoke.get("result"))
    failed_workers = [_dict_value(item) for item in _list_value(worker_payload.get("failed_workers"))]
    doctor_findings = _team_builder_test_doctor_findings(package_name, [], worker_payload)
    policy = _team_builder_repair_safety_policy()
    actions = [_team_builder_repair_action_from_finding(finding, index, policy) for index, finding in enumerate(doctor_findings)]
    repair_required = sum(1 for action in actions if action.get("category") == "repair_required")
    validation_gap = sum(1 for action in actions if action.get("category") == "validation_gap")
    auto_safe = sum(1 for action in actions if action.get("auto_safe"))
    captured_failure = any(
        _safe_text(item.get("check_id"), 160) == "team_builder.worker_run_smoke.failed"
        for item in doctor_findings
    )
    repair_verdict = "repair_required" if repair_required else "validation_gap" if validation_gap else "clean"
    gates = [
        _test_gate(
            "probe_package_imports",
            "故障探针包可导入",
            "pass" if package_smoke.get("returncode") == 0 and package_payload.get("ok") else "fail",
            f"build_team 返回 {package_payload.get('team_id')}, 节点 {len(_list_value(package_payload.get('nodes')))}。"
            if package_smoke.get("returncode") == 0 and package_payload.get("ok")
            else f"探针包导入失败: {package_payload.get('error') or package_smoke.get('stderr')}",
            _list_value(package_payload.get("nodes"))[:10],
        ),
        _test_gate(
            "worker_failure_captured",
            "worker 失败被 smoke 捕获",
            "pass" if captured_failure and failed_workers else "fail",
            f"捕获 {len(failed_workers)} 个失败 worker，并生成 doctor finding。"
            if captured_failure and failed_workers
            else "没有捕获到预期的 team_builder.worker_run_smoke.failed finding。",
            [
                f"{_safe_text(item.get('worker_id'), 120)}: {_safe_text(item.get('diagnosis'), 180)}"
                for item in failed_workers
            ],
        ),
        _test_gate(
            "repair_classified_as_plan_only",
            "repair 进入补丁计划而非自动改码",
            "pass" if repair_required and not auto_safe else "fail",
            f"repair_required={repair_required}, auto_safe={auto_safe}；策略要求只生成补丁计划。"
            if repair_required and not auto_safe
            else "repair 分类未达到预期：运行失败必须进入 repair_required，且不能自动改代码。",
            [action.get("policy_rule_id", "") for action in actions],
        ),
    ]
    verdict = "pass" if all(gate["status"] == "pass" for gate in gates) else "fail"
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": package_name,
        "verdict": verdict,
        "summary": (
            f"故障修复探针 {verdict}: 实际执行 1 个受控失败 worker，"
            f"捕获失败 {len(failed_workers)} 个，doctor finding {len(doctor_findings)} 条，"
            f"repair_required {repair_required} 条，auto_safe {auto_safe} 条。"
        ),
        "counts": {
            "captured_failures": len(failed_workers),
            "doctor_findings": len(doctor_findings),
            "repair_required": repair_required,
            "validation_gap": validation_gap,
            "auto_safe": auto_safe,
        },
        "quality_gates": gates,
        "worker_run_smoke": {
            "status": _safe_text(worker_payload.get("status"), 40),
            "failed_workers": failed_workers,
            "executed_workers": [_dict_value(item) for item in _list_value(worker_payload.get("executed_workers"))],
            "error": _safe_text(worker_payload.get("error") or worker_smoke.get("stderr"), 700),
        },
        "doctor_findings": doctor_findings,
        "repair_plan": {
            "verdict": repair_verdict,
            "summary": (
                "受控失败已被归类为需要修复准备；当前安全策略只允许生成补丁计划，不允许自动修改生成代码。"
                if repair_required
                else "未发现需要修复准备的受控失败。"
            ),
            "counts": {
                "actions": len(actions),
                "repair_required": repair_required,
                "validation_gap": validation_gap,
                "auto_safe": auto_safe,
            },
            "actions": actions,
        },
        "source": {
            "probe_package_dir": str(package_dir.relative_to(_repo_root())),
            "repair_probe_material": str((materials_dir / "team_repair_probe_report.json").relative_to(_repo_root())),
        },
    }
    try:
        (materials_dir / "team_repair_probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    _TEAM_BUILDER_REPAIR_PROBE_CACHE[run_id] = (input_mtime, json.loads(json.dumps(report, ensure_ascii=False)))
    return report


def _team_builder_repair_probe_input_mtime(latest_run: Path | None) -> float:
    input_mtime = 0.0
    for path in [Path(__file__), latest_run / "summary.json" if latest_run else None]:
        if path is None:
            continue
        try:
            input_mtime = max(input_mtime, path.stat().st_mtime)
        except OSError:
            pass
    return input_mtime


def _team_builder_patch_failure_probe_worker(worker_path: Path) -> dict[str, Any]:
    before = worker_path.read_text(encoding="utf-8")
    after = before
    replacements = [
        ("kind=VerdictKind.FAIL", "kind=VerdictKind.PASS"),
        ("'probe': 'controlled_failure'", "'probe': 'repaired_success'"),
        (
            "controlled failure: repair probe worker returned FAIL on purpose",
            "repair dry-run success: probe worker returned PASS after scoped patch",
        ),
    ]
    applied: list[str] = []
    for old, new in replacements:
        if old in after:
            after = after.replace(old, new, 1)
            applied.append(old)
    if len(applied) != len(replacements):
        missing = [old for old, _new in replacements if old not in before]
        raise RuntimeError(f"受控补丁无法定位预期片段: {', '.join(missing)}")
    worker_path.write_text(after, encoding="utf-8")
    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="before/workers/failure_probe_worker.py",
        tofile="after/workers/failure_probe_worker.py",
    ))
    return {
        "changed": before != after,
        "replacements": len(applied),
        "diff": _safe_text(diff, 2400),
    }


def _team_builder_repair_dry_run_report() -> dict[str, Any]:
    latest_run, _reason = _team_builder_latest_run_dir()
    run_id = f"{latest_run.name}-repair-dry-run" if latest_run else "standalone-repair-dry-run"
    input_mtime = _team_builder_repair_probe_input_mtime(latest_run)
    cached = _TEAM_BUILDER_REPAIR_DRY_RUN_CACHE.get(run_id)
    if cached and cached[0] >= input_mtime:
        return json.loads(json.dumps(cached[1], ensure_ascii=False))

    scratch_root = _repo_root() / "_scratch" / "team_builder_repair_dry_run" / run_id
    package_name = "team_builder_repair_probe"
    package_dir = scratch_root / package_name
    materials_dir = scratch_root / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)
    report_path = materials_dir / "team_repair_dry_run_report.json"

    try:
        _write_team_builder_repair_probe_package(package_dir)
        before_package_smoke = _run_generated_package_smoke(package_dir, package_name)
        before_worker_smoke = _run_generated_worker_run_smoke(package_dir, package_name)
        before_worker_payload = _dict_value(before_worker_smoke.get("result"))
        before_failed_workers = [_dict_value(item) for item in _list_value(before_worker_payload.get("failed_workers"))]
        before_findings = _team_builder_test_doctor_findings(package_name, [], before_worker_payload)
        policy = _team_builder_repair_safety_policy()
        before_actions = [
            _team_builder_repair_action_from_finding(finding, index, policy)
            for index, finding in enumerate(before_findings)
        ]
        repair_required = sum(1 for action in before_actions if action.get("category") == "repair_required")

        worker_path = package_dir / "workers" / "failure_probe_worker.py"
        patch_result = _team_builder_patch_failure_probe_worker(worker_path)
        after_package_smoke = _run_generated_package_smoke(package_dir, package_name)
        after_worker_smoke = _run_generated_worker_run_smoke(package_dir, package_name)
        after_worker_payload = _dict_value(after_worker_smoke.get("result"))
        after_failed_workers = [_dict_value(item) for item in _list_value(after_worker_payload.get("failed_workers"))]
        after_executed_workers = [_dict_value(item) for item in _list_value(after_worker_payload.get("executed_workers"))]
        after_findings = _team_builder_test_doctor_findings(package_name, [], after_worker_payload)
    except Exception as exc:
        report = {
            "available": True,
            "run_id": run_id,
            "team_name": package_name,
            "verdict": "fail",
            "summary": f"修复干跑探针设施失败：{type(exc).__name__}: {exc}",
            "counts": {
                "before_failures": 0,
                "before_findings": 0,
                "repair_required": 0,
                "patch_files": 0,
                "after_failures": 0,
                "after_findings": 0,
                "fixed_workers": 0,
                "auto_safe": 0,
            },
            "quality_gates": [
                _test_gate("dry_run_infrastructure", "修复干跑设施可运行", "fail", f"{type(exc).__name__}: {exc}", []),
            ],
            "patch_plan": {},
            "before": {},
            "after": {},
            "source": {"repair_dry_run_material": str(report_path.relative_to(_repo_root()))},
        }
        try:
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return report

    before_package_payload = _dict_value(before_package_smoke.get("result"))
    after_package_payload = _dict_value(after_package_smoke.get("result"))
    changed_file_rel = (worker_path.relative_to(package_dir).as_posix())
    scratch_confined = True
    try:
        worker_path.resolve().relative_to(scratch_root.resolve())
    except (OSError, ValueError):
        scratch_confined = False
    after_clean = not after_failed_workers and not after_findings and _safe_text(after_worker_payload.get("status"), 40) == "pass"
    before_failure_captured = any(
        _safe_text(finding.get("check_id"), 160) == "team_builder.worker_run_smoke.failed"
        for finding in before_findings
    ) and bool(before_failed_workers)
    patch_plan = {
        "id": "repair_patch_plan:failure_probe_worker:dry_run",
        "title": "把受控失败 worker 改为通过返回",
        "summary": "这是 scratch 内干跑补丁：只修改探针包的 failure_probe_worker.py，把可复现失败改成 PASS 返回，用于验证 repair 后重跑链路。",
        "finding_ids": [_safe_text(finding.get("id"), 220) for finding in before_findings],
        "policy_rule_ids": sorted({
            _safe_text(action.get("policy_rule_id"), 120)
            for action in before_actions
            if _safe_text(action.get("policy_rule_id"), 120)
        }),
        "changed_files": [changed_file_rel],
        "dry_run_applied": True,
        "scope": "scratch_only",
        "auto_safe": False,
        "rationale": "运行失败能自动生成最小补丁计划，但真实 generated code 仍需人工确认；本次只在 scratch 探针包内应用。",
        "verification_commands": [
            "python -m pytest -q tests\\dashboard\\test_catalogue_material_attribution.py",
            "GET /api/team-builder-materialization/repair-dry-run/latest",
        ],
        "diff": _safe_text(patch_result.get("diff"), 2400),
    }
    gates = [
        _test_gate(
            "before_failure_captured",
            "修复前失败可复现",
            "pass" if before_failure_captured else "fail",
            f"修复前捕获失败 {len(before_failed_workers)} 个，doctor finding {len(before_findings)} 条。"
            if before_failure_captured
            else "修复前没有捕获到可复现 worker 失败。",
            [_safe_text(item.get("diagnosis"), 180) for item in before_failed_workers],
        ),
        _test_gate(
            "patch_plan_generated",
            "最小补丁计划已生成",
            "pass" if patch_result.get("changed") and repair_required else "fail",
            f"补丁计划修改 {len(patch_plan['changed_files'])} 个文件，repair_required={repair_required}。"
            if patch_result.get("changed") and repair_required
            else "没有生成可执行的最小补丁计划，或失败未进入 repair_required。",
            patch_plan["changed_files"],
        ),
        _test_gate(
            "patch_scope_confined",
            "补丁限制在 scratch 探针包",
            "pass" if scratch_confined and patch_plan["changed_files"] == ["workers/failure_probe_worker.py"] else "fail",
            "补丁只触碰 scratch 中的 workers/failure_probe_worker.py。"
            if scratch_confined
            else "补丁目标不在 scratch 范围内。",
            [str(worker_path.relative_to(_repo_root()))],
        ),
        _test_gate(
            "after_worker_smoke_passed",
            "修复后 worker smoke 通过",
            "pass" if not after_failed_workers and after_executed_workers else "fail",
            f"修复后执行 {len(after_executed_workers)} 个 worker，失败 {len(after_failed_workers)} 个。",
            [
                f"{_safe_text(item.get('worker_id'), 120)}={_safe_text(item.get('kind'), 40)}"
                for item in after_executed_workers
            ],
        ),
        _test_gate(
            "after_doctor_clean",
            "修复后 doctor finding 清零",
            "pass" if after_clean else "fail",
            f"修复后 doctor finding {len(after_findings)} 条，worker status={_safe_text(after_worker_payload.get('status'), 40)}。",
            [_safe_text(item.get("check_id"), 160) for item in after_findings],
        ),
    ]
    verdict = "pass" if all(gate["status"] == "pass" for gate in gates) else "fail"
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": package_name,
        "verdict": verdict,
        "summary": (
            f"修复干跑探针 {verdict}: 修复前失败 {len(before_failed_workers)} 个，"
            f"doctor finding {len(before_findings)} 条；应用 scratch 补丁 {len(patch_plan['changed_files'])} 个文件后，"
            f"失败 {len(after_failed_workers)} 个，doctor finding {len(after_findings)} 条。"
        ),
        "counts": {
            "before_failures": len(before_failed_workers),
            "before_findings": len(before_findings),
            "repair_required": repair_required,
            "patch_files": len(patch_plan["changed_files"]),
            "after_failures": len(after_failed_workers),
            "after_findings": len(after_findings),
            "fixed_workers": max(0, len(before_failed_workers) - len(after_failed_workers)),
            "auto_safe": 0,
        },
        "quality_gates": gates,
        "patch_plan": patch_plan,
        "before": {
            "package_smoke": {
                "ok": bool(before_package_payload.get("ok")),
                "team_id": _safe_text(before_package_payload.get("team_id"), 160),
            },
            "worker_run_smoke": {
                "status": _safe_text(before_worker_payload.get("status"), 40),
                "failed_workers": before_failed_workers,
                "doctor_findings": before_findings,
            },
            "repair_actions": before_actions,
        },
        "after": {
            "package_smoke": {
                "ok": bool(after_package_payload.get("ok")),
                "team_id": _safe_text(after_package_payload.get("team_id"), 160),
            },
            "worker_run_smoke": {
                "status": _safe_text(after_worker_payload.get("status"), 40),
                "executed_workers": after_executed_workers,
                "failed_workers": after_failed_workers,
                "doctor_findings": after_findings,
            },
        },
        "source": {
            "probe_package_dir": str(package_dir.relative_to(_repo_root())),
            "patched_file": str(worker_path.relative_to(_repo_root())),
            "repair_dry_run_material": str(report_path.relative_to(_repo_root())),
        },
    }
    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    _TEAM_BUILDER_REPAIR_DRY_RUN_CACHE[run_id] = (input_mtime, json.loads(json.dumps(report, ensure_ascii=False)))
    return report


def _team_builder_llm_replay_execution_preflight(actions: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv()
    except Exception:
        pass
    enabled = os.environ.get("OMNI_ALLOW_TEAM_BUILDER_LLM_REPLAY", "").strip().lower() in {"1", "true", "yes", "on"}
    has_the_company_key = bool(os.environ.get("THE_COMPANY_API_KEY"))
    models = sorted({_safe_text(action.get("model"), 120) for action in actions if _safe_text(action.get("model"), 120)})
    credential_ready = has_the_company_key
    if not actions:
        status = "not_needed"
        summary = "当前没有需要真实回放的 LLM 调用。"
        next_action = "无需执行真实模型回放。"
    elif not enabled:
        status = "blocked_by_switch"
        summary = "真实 LLM 回放开关未打开，dashboard 不会因刷新页面而产生模型调用。"
        next_action = "确认要产生真实模型调用后，设置 OMNI_ALLOW_TEAM_BUILDER_LLM_REPLAY=1，再执行受控回放。"
    elif not credential_ready:
        status = "blocked_by_credentials"
        summary = "真实 LLM 回放开关已打开但缺少模型凭据。"
        next_action = "为 qwen 模型配置 THE_COMPANY_API_KEY，然后重新执行受控回放。"
    else:
        status = "ready_to_execute"
        summary = "执行开关和模型凭据都已满足，可以进入受控真实回放。"
        next_action = "执行真实回放后，校验 JSON 输出键、中文字段和 doctor finding 是否变化。"
    return {
        "status": status,
        "enabled": enabled,
        "can_execute": status == "ready_to_execute",
        "has_the_company_api_key": has_the_company_key,
        "models": models,
        "summary": summary,
        "next_action": next_action,
    }


def _team_builder_latest_llm_replay_plan() -> dict[str, Any]:
    test_report = _team_builder_test_report()
    if not test_report.get("available"):
        return {
            "available": False,
            "reason": _safe_text(test_report.get("reason"), 500),
            "run_id": _safe_text(test_report.get("run_id"), 160),
            "team_name": _safe_text(test_report.get("team_name"), 160),
            "verdict": "unavailable",
            "summary": "暂无生成包测试报告，无法生成 LLM 回放计划。",
            "counts": {"calls": 0, "ready": 0, "blocked": 0},
            "quality_gates": [],
            "actions": [],
            "source": test_report.get("source") if isinstance(test_report.get("source"), dict) else {},
        }

    worker_run = _dict_value(test_report.get("worker_run_smoke"))
    stubbed_workers = [_dict_value(item) for item in _list_value(worker_run.get("stubbed_workers"))]
    actions: list[dict[str, Any]] = []
    for worker in stubbed_workers:
        worker_id = _safe_text(worker.get("worker_id"), 160)
        calls = [_dict_value(item) for item in _list_value(worker.get("llm_stub_calls"))]
        for call_index, call in enumerate(calls):
            expected_keys = [_safe_text(item, 80) for item in _list_value(call.get("expected_output_keys"))]
            missing_contract: list[str] = []
            if not call.get("model"):
                missing_contract.append("model")
            if not expected_keys:
                missing_contract.append("expected_output_keys")
            if not call.get("has_json_instruction"):
                missing_contract.append("json_instruction")
            if not call.get("has_chinese_instruction"):
                missing_contract.append("chinese_instruction")
            ready = not missing_contract
            actions.append({
                "id": f"llm_replay:{worker_id}:{call_index}",
                "worker_id": worker_id,
                "call_index": call_index,
                "model": _safe_text(call.get("model"), 120),
                "max_tokens": call.get("max_tokens"),
                "status": "ready" if ready else "blocked",
                "missing_contract": missing_contract,
                "expected_output_keys": expected_keys,
                "stub_response_keys": [_safe_text(item, 80) for item in _list_value(call.get("stub_response_keys"))],
                "system_chars": call.get("system_chars") if isinstance(call.get("system_chars"), int) else 0,
                "user_chars": call.get("user_chars") if isinstance(call.get("user_chars"), int) else 0,
                "system_preview": _safe_text(call.get("system_preview"), 240),
                "user_preview": _safe_text(call.get("user_preview"), 700),
                "human_summary": (
                    f"{worker_id} 已具备受控回放契约: 模型 {_safe_text(call.get('model'), 120)}, "
                    f"输出键 {', '.join(expected_keys) or '未声明'}。"
                    if ready else f"{worker_id} 的 LLM 回放契约不完整: {', '.join(missing_contract)}。"
                ),
                "next_action": "允许在受控开关下执行真实 LLM 回放，并校验输出键、JSON 结构、中文摘要和 doctor finding 变化。"
                if ready else "先补齐模型名、输出键、JSON/中文约束，再允许真实 LLM 回放。",
            })

    ready_count = sum(1 for action in actions if action["status"] == "ready")
    blocked_count = sum(1 for action in actions if action["status"] == "blocked")
    execution_preflight = _team_builder_llm_replay_execution_preflight(actions)
    gates = [
        _test_gate(
            "stub_evidence_present",
            "模型桩证据存在",
            "pass" if actions else "warning",
            f"发现 {len(actions)} 个 LLM 桩调用证据。" if actions else "当前没有 LLM 桩调用证据。",
            [action["id"] for action in actions],
        ),
        _test_gate(
            "prompt_contract_visible",
            "prompt 契约可审阅",
            "pass" if actions and blocked_count == 0 else "fail" if blocked_count else "warning",
            f"{ready_count} 个调用具备模型名、输出键、JSON/中文约束。"
            if actions and blocked_count == 0 else f"{blocked_count} 个调用契约不完整。",
            [f"{action['worker_id']}: {', '.join(action['missing_contract'])}" for action in actions if action["missing_contract"]],
        ),
        _test_gate(
            "real_replay_not_run",
            "真实模型回放未执行",
            "warning" if actions else "pass",
            "本计划只定义受控回放门槛，尚未调用真实模型。" if actions else "没有需要真实回放的 LLM 调用。",
            [],
        ),
        _test_gate(
            "execution_preflight",
            "执行前置条件",
            "pass" if execution_preflight["can_execute"] or not actions else "warning",
            execution_preflight["summary"],
            [
                f"enabled={execution_preflight['enabled']}",
                f"has_the_company_api_key={execution_preflight['has_the_company_api_key']}",
                f"models={','.join(execution_preflight['models'])}",
            ],
        ),
    ]
    verdict = "blocked" if blocked_count else "ready_for_controlled_replay" if actions else "no_llm_call"
    summary = (
        f"LLM 回放计划: {ready_count} 个调用可进入受控真实回放，{blocked_count} 个契约不完整；当前未调用真实模型。"
        if actions else "当前生成包测试没有发现需要回放的 LLM 调用。"
    )
    source = test_report.get("source") if isinstance(test_report.get("source"), dict) else {}
    run_id = _safe_text(test_report.get("run_id"), 160)
    plan = {
        "available": True,
        "run_id": run_id,
        "team_name": _safe_text(test_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "calls": len(actions),
            "ready": ready_count,
            "blocked": blocked_count,
        },
        "quality_gates": gates,
        "actions": actions,
        "execution_preflight": execution_preflight,
        "source": {
            **source,
            "llm_replay_plan_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_llm_replay_plan.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        replay_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_llm_replay_plan.json"
        try:
            replay_path.parent.mkdir(parents=True, exist_ok=True)
            replay_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return plan


def _team_builder_llm_replay_result_path(run_id: str) -> Path:
    return _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_llm_replay_result.json"


def _run_generated_llm_replay(package_dir: Path, package_name: str) -> dict[str, Any]:
    script = r"""
import importlib
import inspect
import json
import re
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

package_parent = Path(sys.argv[1])
package_name = sys.argv[2]
repo_root = Path(sys.argv[3])
sys.path.insert(0, str(package_parent))


def _format_tokens(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, (list, tuple, set)):
        out = []
        for item in raw:
            out.extend(_format_tokens(item))
        return list(dict.fromkeys(str(item) for item in out if item))
    return [str(raw)] if raw else []


def _kind_text(verdict):
    kind = getattr(verdict, "kind", "")
    return str(getattr(kind, "value", kind) or "")


def _jsonable(value, depth=0):
    if depth > 4:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item, depth + 1) for item in list(value)[:80]]
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(), depth + 1)
        except Exception:
            pass
    return str(value)


def _brief_output(value):
    data = _jsonable(value)
    if isinstance(data, dict):
        return {
            "type": "dict",
            "keys": list(data.keys())[:16],
            "size": len(data),
            "sample": {str(k): data[k] for k in list(data.keys())[:4]},
        }
    if isinstance(data, list):
        return {"type": "list", "size": len(data)}
    return {"type": type(data).__name__, "value": str(data)[:220]}


def _has_chinese(text):
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def _is_llm_worker(worker):
    try:
        source = inspect.getsource(worker.__class__)
    except Exception:
        source = ""
    return "call_llm" in source or "LLMClient" in source or "client.call(" in source


result = {
    "ok": False,
    "status": "fail",
    "team_id": "",
    "executed_workers": [],
    "skipped_workers": [],
    "failed_workers": [],
    "contract_failures": [],
    "seed_materials": [],
    "produced_materials": [],
    "error": "",
}

try:
    team_mod = importlib.import_module(f"{package_name}.team")
    run_mod = importlib.import_module(f"{package_name}.run")
    spec = team_mod.build_team()
    bindings = run_mod.build_bindings({})
    result["team_id"] = str(getattr(spec, "id", "") or package_name)
    node_ids = [str(getattr(node, "id", "")) for node in getattr(spec, "nodes", []) if getattr(node, "id", "")]
    if not node_ids:
        node_ids = sorted(str(key) for key in bindings.keys())

    seed_payload = {
        "team_id": result["team_id"],
        "workspace_root": str(repo_root),
        "event_sources": [
            "src/omnicompany/packages/services/_core/team_builder/team.py",
            "_scratch/team_builder_real_material_validation",
        ],
        "question": "验证生成 team 的最小运行链路。",
    }
    material_values = {}

    for worker_id in node_ids:
        worker = bindings.get(worker_id)
        if worker is None:
            result["skipped_workers"].append({
                "worker_id": worker_id,
                "reason": "missing_binding",
                "summary": "build_bindings 没有返回这个节点的 worker 实例。",
            })
            continue

        run_method = getattr(worker, "run", None)
        if not callable(run_method):
            result["skipped_workers"].append({
                "worker_id": worker_id,
                "reason": "no_run_method",
                "summary": "worker 实例没有可调用的 run 方法。",
            })
            continue

        format_in = _format_tokens(getattr(worker, "FORMAT_IN", []))
        input_payload = {}
        for material_id in format_in:
            if material_id not in material_values and "observation_request" in material_id:
                material_values[material_id] = dict(seed_payload)
                result["seed_materials"].append(material_id)
            if material_id in material_values:
                input_payload[material_id] = material_values[material_id]
        missing_inputs = [material_id for material_id in format_in if material_id not in input_payload]
        if missing_inputs:
            result["skipped_workers"].append({
                "worker_id": worker_id,
                "reason": "missing_input",
                "summary": "上游 material 不足，无法安全调用 worker.run。",
                "missing_inputs": missing_inputs,
            })
            continue

        is_llm_worker = _is_llm_worker(worker)
        try:
            verdict = run_method(input_payload)
            kind = _kind_text(verdict)
            output = getattr(verdict, "output", None)
            diagnosis = getattr(verdict, "diagnosis", "") or ""
            format_out = str(getattr(worker, "FORMAT_OUT", "") or "")
            if format_out:
                material_values[format_out] = output
                result["produced_materials"].append(format_out)
            item = {
                "worker_id": worker_id,
                "kind": kind,
                "is_llm_worker": is_llm_worker,
                "input_materials": format_in,
                "output_material": format_out,
                "diagnosis": diagnosis,
                "output_summary": _brief_output(output),
            }
            result["executed_workers"].append(item)
            if kind != "pass":
                result["failed_workers"].append(item)
            if is_llm_worker:
                payload = output if isinstance(output, dict) else {}
                for key in ["summary_cn", "risks", "next_checks"]:
                    if key not in payload:
                        result["contract_failures"].append(f"{worker_id}: missing {key}")
                if not _has_chinese(payload.get("summary_cn", "")):
                    result["contract_failures"].append(f"{worker_id}: summary_cn lacks Chinese content")
                if not isinstance(payload.get("risks", []), list):
                    result["contract_failures"].append(f"{worker_id}: risks is not list")
                if not isinstance(payload.get("next_checks", []), list):
                    result["contract_failures"].append(f"{worker_id}: next_checks is not list")
        except Exception as exc:
            item = {
                "worker_id": worker_id,
                "kind": "exception",
                "is_llm_worker": is_llm_worker,
                "input_materials": format_in,
                "output_material": str(getattr(worker, "FORMAT_OUT", "") or ""),
                "diagnosis": f"{type(exc).__name__}: {exc}",
                "output_summary": {},
            }
            result["failed_workers"].append(item)
            result["executed_workers"].append(item)

    llm_workers = [item for item in result["executed_workers"] if item.get("is_llm_worker")]
    if result["failed_workers"]:
        result["status"] = "fail"
    elif not llm_workers:
        result["status"] = "warning"
        result["error"] = "没有执行到 LLM worker。"
    elif result["contract_failures"]:
        result["status"] = "fail"
    else:
        result["status"] = "pass"
    result["ok"] = result["status"] == "pass"
except Exception as exc:
    result["ok"] = False
    result["status"] = "fail"
    result["error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps(result, ensure_ascii=False))
"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [os.sys.executable, "-c", script, str(package_dir.parent), package_name, str(_repo_root())],
        cwd=str(_repo_root()),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
    )
    stdout = proc.stdout.strip()
    parsed: dict[str, Any] = {}
    if stdout:
        try:
            parsed = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            parsed = {"ok": False, "status": "fail", "error": stdout[-1200:]}
    return {
        "returncode": proc.returncode,
        "stdout": _safe_text(stdout, 1600),
        "stderr": _safe_text(proc.stderr, 1600),
        "result": parsed,
    }


def _team_builder_execute_llm_replay() -> dict[str, Any]:
    plan = _team_builder_latest_llm_replay_plan()
    run_id = _safe_text(plan.get("run_id"), 160)
    team_name = _safe_text(plan.get("team_name"), 160)
    preflight = _dict_value(plan.get("execution_preflight"))
    source = _dict_value(plan.get("source"))
    result_path = _team_builder_llm_replay_result_path(run_id) if run_id else None

    def _blocked(verdict: str, summary: str) -> dict[str, Any]:
        report = {
            "available": True,
            "run_id": run_id,
            "team_name": team_name,
            "verdict": verdict,
            "summary": summary,
            "counts": {
                "planned_calls": _dict_value(plan.get("counts")).get("calls", 0),
                "executed_workers": 0,
                "executed_llm_workers": [],
                "failed_workers": 0,
                "contract_failures": 0,
            },
            "quality_gates": [
                _test_gate("execution_preflight", "执行前置条件", "warning", _safe_text(preflight.get("summary"), 520), []),
            ],
            "execution_preflight": preflight,
            "actions": _list_value(plan.get("actions")),
            "source": {**source, "llm_replay_result_material": str(result_path.relative_to(_repo_root())) if result_path else ""},
        }
        if result_path:
            try:
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        return report

    if not plan.get("available"):
        return _blocked("unavailable", _safe_text(plan.get("summary") or plan.get("reason"), 520) or "暂无 LLM 回放计划。")
    if not _list_value(plan.get("actions")):
        return _blocked("no_llm_call", "当前没有需要真实回放的 LLM 调用。")
    if not preflight.get("can_execute"):
        return _blocked(_safe_text(preflight.get("status"), 80) or "blocked", _safe_text(preflight.get("summary"), 520))

    package_dir_text = _safe_text(source.get("test_package_dir"), 260)
    package_dir = (_repo_root() / package_dir_text).resolve() if package_dir_text else None
    try:
        if package_dir is None:
            raise RuntimeError("回放计划缺少 test_package_dir。")
        package_dir.relative_to((_repo_root() / "_scratch" / "team_builder_test_reports").resolve())
    except (OSError, ValueError, RuntimeError) as exc:
        return _blocked("blocked_by_package", f"无法定位隔离测试包: {exc}")
    if not package_dir.is_dir():
        return _blocked("blocked_by_package", f"隔离测试包不存在: {package_dir_text}")

    run_result = _run_generated_llm_replay(package_dir, team_name)
    payload = _dict_value(run_result.get("result"))
    executed_workers = [_dict_value(item) for item in _list_value(payload.get("executed_workers"))]
    executed_llm_workers = [item for item in executed_workers if item.get("is_llm_worker")]
    failed_workers = [_dict_value(item) for item in _list_value(payload.get("failed_workers"))]
    contract_failures = [_safe_text(item, 220) for item in _list_value(payload.get("contract_failures"))]
    subprocess_ok = run_result.get("returncode") == 0 and bool(payload)
    status = _safe_text(payload.get("status"), 40) or "fail"
    gates = [
        _test_gate("execution_preflight", "执行前置条件", "pass", _safe_text(preflight.get("summary"), 520), []),
        _test_gate(
            "subprocess_execution",
            "隔离进程真实执行",
            "pass" if subprocess_ok else "fail",
            "隔离进程完成真实 worker run。" if subprocess_ok else f"隔离进程失败: {payload.get('error') or run_result.get('stderr')}",
            [],
        ),
        _test_gate(
            "llm_worker_executed",
            "LLM worker 已执行",
            "pass" if executed_llm_workers else "fail",
            f"真实执行 {len(executed_llm_workers)} 个 LLM worker。",
            [_safe_text(item.get("worker_id"), 160) for item in executed_llm_workers],
        ),
        _test_gate(
            "output_contract",
            "输出契约通过",
            "pass" if not contract_failures and status == "pass" else "fail",
            "LLM 输出包含 summary_cn、risks、next_checks，且 summary_cn 为中文。"
            if not contract_failures and status == "pass" else "LLM 输出契约不完整。",
            contract_failures,
        ),
    ]
    verdict = "fail" if any(gate["status"] == "fail" for gate in gates) or failed_workers else "pass"
    summary = (
        f"受控 LLM 回放 {verdict}: 执行 {len(executed_workers)} 个 worker，其中 LLM worker {len(executed_llm_workers)} 个；"
        f"失败 {len(failed_workers)} 个，契约问题 {len(contract_failures)} 个。"
    )
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": team_name,
        "verdict": verdict,
        "summary": summary,
        "counts": {
            "planned_calls": _dict_value(plan.get("counts")).get("calls", 0),
            "executed_workers": len(executed_workers),
            "executed_llm_workers": [
                {"worker_id": _safe_text(item.get("worker_id"), 160), "kind": _safe_text(item.get("kind"), 40)}
                for item in executed_llm_workers
            ],
            "failed_workers": len(failed_workers),
            "contract_failures": len(contract_failures),
        },
        "quality_gates": gates,
        "execution_preflight": preflight,
        "executed_workers": executed_workers,
        "failed_workers": failed_workers,
        "contract_failures": contract_failures,
        "raw_execution": run_result,
        "source": {**source, "llm_replay_result_material": str(result_path.relative_to(_repo_root())) if result_path else ""},
    }
    if result_path:
        try:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_latest_llm_replay_result() -> dict[str, Any]:
    run_dir, reason = _team_builder_latest_run_dir()
    if not run_dir:
        return {
            "available": False,
            "reason": reason,
            "run_id": "",
            "team_name": "",
            "verdict": "unavailable",
            "summary": "暂无 TeamBuilder 实战 run，无法读取 LLM 回放结果。",
            "counts": {"planned_calls": 0, "executed_workers": 0, "executed_llm_workers": [], "failed_workers": 0, "contract_failures": 0},
            "quality_gates": [],
            "source": {},
        }
    run_id = run_dir.name
    path = _team_builder_llm_replay_result_path(run_id)
    if not path.is_file():
        plan = _team_builder_latest_llm_replay_plan()
        return {
            "available": False,
            "reason": "尚未执行受控 LLM 回放。",
            "run_id": run_id,
            "team_name": _safe_text(plan.get("team_name"), 160),
            "verdict": "not_run",
            "summary": "LLM 回放计划已存在，但还没有真实回放结果 material。",
            "counts": {"planned_calls": _dict_value(plan.get("counts")).get("calls", 0), "executed_workers": 0, "executed_llm_workers": [], "failed_workers": 0, "contract_failures": 0},
            "quality_gates": [],
            "execution_preflight": _dict_value(plan.get("execution_preflight")),
            "source": {
                **(_dict_value(plan.get("source"))),
                "llm_replay_result_material": str(path.relative_to(_repo_root())),
            },
        }
    report = _read_json_file(path)
    return report if report else {
        "available": False,
        "reason": "LLM 回放结果 material 无法解析。",
        "run_id": run_id,
        "team_name": "",
        "verdict": "unavailable",
        "summary": "LLM 回放结果 material 存在但不是有效 JSON。",
        "counts": {"planned_calls": 0, "executed_workers": 0, "executed_llm_workers": [], "failed_workers": 0, "contract_failures": 0},
        "quality_gates": [],
        "source": {"llm_replay_result_material": str(path.relative_to(_repo_root()))},
    }


def _team_builder_stage(status: str, name: str, summary: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": name,
        "name": name,
        "status": status if status in {"pass", "warning", "fail"} else "warning",
        "summary": _safe_text(summary, 520),
        "evidence": [_safe_text(item, 260) for item in (evidence or [])[:8]],
    }


def _team_builder_latest_closure_status() -> dict[str, Any]:
    material_report = _material_attribution_report()
    test_report = _team_builder_test_report()
    doctor_report = _team_builder_latest_doctor_findings_report()
    repair_plan = _team_builder_latest_repair_plan()
    llm_replay_plan = _team_builder_latest_llm_replay_plan()
    llm_replay_result = _team_builder_latest_llm_replay_result()
    read_clue_resolution_plan = _team_builder_latest_read_clue_resolution_plan()

    stages: list[dict[str, Any]] = []
    missing: list[str] = []

    test_counts = _dict_value(test_report.get("counts"))
    test_gates = [_dict_value(item) for item in _list_value(test_report.get("quality_gates"))]
    structure_failures = [gate.get("id") for gate in test_gates if gate.get("id") != "worker_run_smoke" and gate.get("status") == "fail"]
    stages.append(_team_builder_stage(
        "fail" if structure_failures else "pass" if test_report.get("available") else "fail",
        "建立 team",
        f"生成包文件 {test_counts.get('files', 0)} 个，worker {test_counts.get('worker_files', 0)} 个；结构门 {'失败' if structure_failures else '通过'}。",
        [f"run_id={test_report.get('run_id')}", f"team={test_report.get('team_name')}"],
    ))

    material_counts = _dict_value(material_report.get("counts"))
    material_status = _safe_text(material_report.get("verdict"), 40) or "fail"
    stages.append(_team_builder_stage(
        material_status if material_status in {"pass", "warning", "fail"} else "warning",
        "material 归因",
        _safe_text(material_report.get("summary"), 520) or "暂无 material 归因报告。",
        [
            f"confirmed_reads={material_counts.get('confirmed_reads', 0)}",
            f"read_clues={material_counts.get('read_clues', 0)}",
        ],
    ))
    read_clue_counts = _dict_value(read_clue_resolution_plan.get("counts"))
    unresolved_read_clues = read_clue_counts.get("unresolved", 0) or material_counts.get("unconfirmed_read_clues", 0)
    if unresolved_read_clues:
        candidate_materialized = read_clue_counts.get("candidate_materialized", 0)
        candidate_materials = read_clue_counts.get("candidate_materials", 0)
        tool_read_confirmed_materials = read_clue_counts.get("tool_read_confirmed_materials", 0)
        content_mention_path_materials = read_clue_counts.get("content_mention_path_materials", 0)
        unexpanded = read_clue_counts.get("unexpanded", unresolved_read_clues)
        if candidate_materialized and not unexpanded:
            missing.append(
                f"读取线索已展开为 {candidate_materials} 个候选 material；"
                f"其中 {tool_read_confirmed_materials} 个已有工具命中或明确 Read 证据，"
                f"{content_mention_path_materials} 个只是读取内容里的路径提及，"
                f"{unresolved_read_clues} 条仍缺完整工具命中输出，暂不升级为事实读边。"
            )
        else:
            missing.append(
                f"读取线索消解计划已生成；{unresolved_read_clues} 条候选仍需展开、回放或人工确认，"
                f"其中 {candidate_materialized} 条已候选 material 化。"
            )

    worker_run = _dict_value(test_report.get("worker_run_smoke"))
    skipped_workers = [_dict_value(item) for item in _list_value(worker_run.get("skipped_workers"))]
    skipped_llm_workers = {
        _safe_text(item.get("worker_id"), 160)
        for item in skipped_workers
        if _safe_text(item.get("reason"), 80) == "requires_llm" and _safe_text(item.get("worker_id"), 160)
    }
    replay_counts = _dict_value(llm_replay_result.get("counts"))
    replay_executed_llm_workers = {
        _safe_text(item.get("worker_id"), 160)
        for item in _list_value(replay_counts.get("executed_llm_workers"))
        if _safe_text(item.get("worker_id"), 160)
    }
    llm_replay_covers_gap = (
        llm_replay_result.get("verdict") == "pass"
        and bool(skipped_llm_workers)
        and skipped_llm_workers.issubset(replay_executed_llm_workers)
    )
    test_stage_status = _safe_text(test_report.get("verdict"), 40) if test_report.get("available") else "fail"
    if llm_replay_covers_gap and test_stage_status == "warning" and not test_counts.get("failed_workers", 0):
        test_stage_status = "pass"
    test_stage_summary = _safe_text(test_report.get("summary"), 520) or "暂无生成包测试报告。"
    if llm_replay_result.get("available"):
        test_stage_summary = f"{test_stage_summary} 受控 LLM 回放: {_safe_text(llm_replay_result.get('verdict'), 80)}。"
    contract_coverage = _dict_value(test_report.get("contract_coverage"))
    contract_counts = _dict_value(contract_coverage.get("counts"))
    contract_matching = int(contract_counts.get("matching_contracts") or 0)
    contract_executed = int(contract_counts.get("executed_contracts") or 0)
    contract_verdict = _safe_text(contract_coverage.get("verdict"), 40)
    if contract_coverage.get("available"):
        test_stage_summary = (
            f"{test_stage_summary} contract 覆盖: 同名 {contract_matching} 个，已执行 {contract_executed} 个。"
        )
        if contract_verdict == "fail":
            missing.append("当前 generated team 的 contract 已显式执行但失败；应把失败项作为 doctor 输入。")
            test_stage_status = "fail"
        elif contract_matching <= 0:
            missing.append(
                "当前 generated team 还没有同名 tests/teams contract；不能把 smoke test 等同于 acceptance。"
            )
            if test_stage_status == "pass":
                test_stage_status = "warning"
        elif contract_executed <= 0:
            missing.append(
                f"当前 generated team 已配置 {contract_matching} 个 contract，但尚未显式执行；acceptance 结果还没有回写为 material。"
            )
            if test_stage_status == "pass":
                test_stage_status = "warning"
    stages.append(_team_builder_stage(
        test_stage_status,
        "测试 team",
        test_stage_summary,
        [
            f"executed={test_counts.get('executed_workers', 0)}",
            f"stubbed={test_counts.get('stubbed_workers', 0)}",
            f"skipped={test_counts.get('skipped_workers', 0)}",
            f"failed={test_counts.get('failed_workers', 0)}",
            f"llm_replay={_safe_text(llm_replay_result.get('verdict'), 40)}",
            f"contract_matching={contract_matching}",
            f"contract_executed={contract_executed}",
        ],
    ))
    if skipped_workers:
        plan_counts = _dict_value(llm_replay_plan.get("counts"))
        replay_preflight = _dict_value(llm_replay_plan.get("execution_preflight"))
        if llm_replay_covers_gap:
            pass
        elif llm_replay_result.get("verdict") == "fail":
            missing.append("真实 LLM 回放已执行但失败；应把结果作为 doctor 输入定位具体运行或输出契约问题。")
        elif plan_counts.get("ready", 0):
            if replay_preflight.get("can_execute"):
                missing.append("真实 LLM 调用尚未回放；回放计划和执行前置条件都已满足，等待受控执行。")
            else:
                missing.append("真实 LLM 调用尚未回放；回放计划已生成，但执行开关或模型凭据未满足。")
        else:
            missing.append("真实 LLM 调用尚未回放；当前只有本地模型桩验证。")

    doctor_counts = _dict_value(doctor_report.get("counts"))
    doctor_status = "fail" if doctor_counts.get("blocking", 0) else "warning" if doctor_counts.get("total", 0) else "pass"
    stages.append(_team_builder_stage(
        doctor_status,
        "诊断分析",
        f"doctor finding {doctor_counts.get('total', 0)} 条；blocking {doctor_counts.get('blocking', 0)}，advisory {doctor_counts.get('advisory', 0)}。",
        [f"doctor_findings={doctor_counts.get('total', 0)}"],
    ))

    repair_counts = _dict_value(repair_plan.get("counts"))
    repair_verdict = _safe_text(repair_plan.get("verdict"), 80)
    repair_status = "fail" if repair_verdict == "repair_required" else "warning" if repair_verdict == "validation_gap" else "pass" if repair_verdict == "clean" else "warning"
    stages.append(_team_builder_stage(
        repair_status,
        "修复准备",
        _safe_text(repair_plan.get("summary"), 520) or "暂无修复准备计划。",
        [
            f"repair_required={repair_counts.get('repair_required', 0)}",
            f"validation_gap={repair_counts.get('validation_gap', 0)}",
            f"auto_safe={repair_counts.get('auto_safe', 0)}",
        ],
    ))
    if repair_counts.get("actions", 0) and not repair_counts.get("auto_safe", 0):
        missing.append("修复安全策略已定义；当前 finding 不满足自动改代码条件。")

    verdict = "fail" if any(stage["status"] == "fail" for stage in stages) else "warning" if missing or any(stage["status"] == "warning" for stage in stages) else "pass"
    run_id = _safe_text(test_report.get("run_id") or material_report.get("run_id"), 160)
    closure = {
        "available": bool(test_report.get("available") or material_report.get("available")),
        "run_id": run_id,
        "team_name": _safe_text(test_report.get("team_name") or material_report.get("team_name"), 160),
        "verdict": verdict,
        "summary": f"TeamBuilder 闭环状态 {verdict}: {len([s for s in stages if s['status'] == 'pass'])}/{len(stages)} 个阶段通过，{len(missing)} 个缺口待处理。",
        "stages": stages,
        "missing": list(dict.fromkeys(missing)),
        "source": {
            "material_report_endpoint": "/api/team-builder-materialization/report/latest",
            "test_report_endpoint": "/api/team-builder-materialization/test-report/latest",
            "doctor_findings_endpoint": "/api/team-builder-materialization/doctor-findings/latest",
            "repair_plan_endpoint": "/api/team-builder-materialization/repair-plan/latest",
            "repair_safety_policy_endpoint": "/api/team-builder-materialization/repair-safety-policy/latest",
            "read_clue_resolution_endpoint": "/api/team-builder-materialization/read-clue-resolution/latest",
            "llm_replay_plan_endpoint": "/api/team-builder-materialization/llm-replay-plan/latest",
            "llm_replay_result_endpoint": "/api/team-builder-materialization/llm-replay-result/latest",
            "closure_status_material": str((
                _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_closure_status.json"
            ).relative_to(_repo_root())) if run_id else "",
        },
    }
    if run_id:
        closure_path = _repo_root() / "_scratch" / "team_builder_real_material_validation" / run_id / "materials" / "team_closure_status.json"
        try:
            closure_path.parent.mkdir(parents=True, exist_ok=True)
            closure_path.write_text(json.dumps(closure, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return closure


def _team_builder_provider_coverage_audit_report() -> dict[str, Any]:
    run_dirs = _team_builder_real_materialization_run_dirs(include_provider_baselines=True)
    trial_dirs = _team_builder_provider_trial_run_dirs()
    latest_run = run_dirs[0] if run_dirs else None
    run_id = latest_run.name if latest_run else "standalone-provider-coverage-audit"
    path = _team_builder_provider_coverage_audit_path(run_id)
    tracked_external_providers = ["claude-code", "codex"]

    provider_stats: dict[str, dict[str, Any]] = {
        provider: {
            "provider": provider,
            "runs": 0,
            "successful_workers": 0,
            "failed_workers": 0,
            "compile_failures": 0,
            "critical_reviews": 0,
            "same_input_trials": 0,
            "trial_successful_workers": 0,
            "trial_failed_workers": 0,
            "trial_parse_failures": 0,
            "passing_evidence": 0,
            "latest_run_id": "",
            "latest_trial_id": "",
            "team_names": [],
            "sample_runs": [],
            "sample_trials": [],
        }
        for provider in tracked_external_providers
    }
    run_records: list[dict[str, Any]] = []
    trial_records: list[dict[str, Any]] = []
    model_records: list[dict[str, Any]] = []
    run_team_by_id: dict[str, str] = {}

    for run_dir in run_dirs[:20]:
        summary = _read_json_file(run_dir / "summary.json")
        if not summary:
            continue
        verification = _dict_value(summary.get("verification"))
        materials = _dict_value(summary.get("materials"))
        worker_bundle = _dict_value(materials.get("worker_code_files_bundle"))
        review = _dict_value(materials.get("code_review_report"))
        external_runs = _list_value(verification.get("external_agent_runs")) or _list_value(worker_bundle.get("external_agent_runs"))
        external_providers = sorted({
            _safe_text(_dict_value(item).get("provider"), 80)
            for item in external_runs
            if _safe_text(_dict_value(item).get("provider"), 80)
        })
        if not external_providers:
            fallback_provider = _safe_text(summary.get("provider"), 80)
            if fallback_provider:
                external_providers = [fallback_provider]

        worker_success = _team_builder_count_value(verification.get("worker_success_count") or worker_bundle.get("success_count"))
        worker_fail = _team_builder_count_value(verification.get("worker_fail_count") or worker_bundle.get("fail_count"))
        compile_fail = _team_builder_count_value(verification.get("compile_fail_count"))
        critical_reviews = _team_builder_count_value(review.get("critical_count"))
        run_status = "pass" if worker_fail == 0 and compile_fail == 0 and critical_reviews == 0 else "warning"
        run_record = {
            "run_id": run_dir.name,
            "team_name": _safe_text(summary.get("team_name"), 160),
            "summary_provider": _safe_text(summary.get("provider"), 80),
            "external_providers": external_providers,
            "status": run_status,
            "successful_workers": worker_success,
            "failed_workers": worker_fail,
            "compile_failures": compile_fail,
            "critical_reviews": critical_reviews,
            "summary_path": _team_builder_rel_path(run_dir / "summary.json"),
        }
        run_records.append(run_record)
        if run_record["team_name"]:
            run_team_by_id[run_dir.name] = run_record["team_name"]

        for provider in external_providers:
            stats = provider_stats.setdefault(provider, {
                "provider": provider,
                "runs": 0,
                "successful_workers": 0,
                "failed_workers": 0,
                "compile_failures": 0,
                "critical_reviews": 0,
                "same_input_trials": 0,
                "trial_successful_workers": 0,
                "trial_failed_workers": 0,
                "trial_parse_failures": 0,
                "passing_evidence": 0,
                "latest_run_id": "",
                "latest_trial_id": "",
                "team_names": [],
                "sample_runs": [],
                "sample_trials": [],
            })
            stats["runs"] += 1
            stats["successful_workers"] += worker_success
            stats["failed_workers"] += worker_fail
            stats["compile_failures"] += compile_fail
            stats["critical_reviews"] += critical_reviews
            if run_status == "pass":
                stats["passing_evidence"] += 1
            if not stats["latest_run_id"]:
                stats["latest_run_id"] = run_dir.name
            if run_record["team_name"] and run_record["team_name"] not in stats["team_names"]:
                stats["team_names"].append(run_record["team_name"])
            if len(stats["sample_runs"]) < 5:
                stats["sample_runs"].append(run_record)

        test_report = _read_json_file(run_dir / "materials" / "team_test_report.json")
        worker_smoke = _dict_value(test_report.get("worker_run_smoke"))
        for call in _list_value(worker_smoke.get("llm_stub_calls"))[:8]:
            data = _dict_value(call)
            model = _safe_text(data.get("model"), 120)
            if not model:
                continue
            model_records.append({
                "run_id": run_dir.name,
                "model": model,
                "evidence_kind": "受控 LLM 回放桩",
                "summary": "该记录证明生成 team 内部 LLM worker 的调用形状可回放；不等同于 WorkerCodeOrchestrator 外部 codegen provider 实战。",
                "expected_output_keys": [_safe_text(item, 80) for item in _list_value(data.get("expected_output_keys"))[:8]],
            })

    for trial_dir in trial_dirs[:20]:
        summary = _read_json_file(trial_dir / "summary.json")
        if not summary:
            continue
        provider = _safe_text(summary.get("provider"), 80)
        if not provider:
            continue
        output = _dict_value(summary.get("output"))
        external_runs = _list_value(output.get("external_agent_runs"))
        parse_status_counts: dict[str, int] = {}
        for item in external_runs:
            parse_status = _safe_text(_dict_value(item).get("parse_status"), 80)
            if parse_status:
                parse_status_counts[parse_status] = parse_status_counts.get(parse_status, 0) + 1
        parse_failures = sum(
            count
            for status_name, count in parse_status_counts.items()
            if status_name in {"no_worker_source", "syntax_error", "invalid_verdict_kind"}
        )
        worker_success = _team_builder_count_value(output.get("success_count"))
        worker_fail = _team_builder_count_value(output.get("fail_count"))
        trial_status = "pass" if worker_fail == 0 and parse_failures == 0 and _safe_text(summary.get("verdict_kind"), 80) == "pass" else "warning"
        baseline_run_id = _safe_text(summary.get("baseline_run_id"), 160)
        trial_team_name = (
            _safe_text(_dict_value(summary.get("plan")).get("team_name"), 160)
            or run_team_by_id.get(baseline_run_id, "")
        )
        trial_record = {
            "trial_id": trial_dir.name,
            "baseline_run_id": baseline_run_id,
            "team_name": trial_team_name,
            "provider": provider,
            "permission": _safe_text(summary.get("permission"), 80),
            "model_policy": _safe_text(summary.get("model_policy"), 80),
            "status": trial_status,
            "verdict_kind": _safe_text(summary.get("verdict_kind"), 80),
            "successful_workers": worker_success,
            "failed_workers": worker_fail,
            "parse_failures": parse_failures,
            "parse_statuses": parse_status_counts,
            "summary_path": _team_builder_rel_path(trial_dir / "summary.json"),
        }
        trial_records.append(trial_record)

        stats = provider_stats.setdefault(provider, {
            "provider": provider,
            "runs": 0,
            "successful_workers": 0,
            "failed_workers": 0,
            "compile_failures": 0,
            "critical_reviews": 0,
            "same_input_trials": 0,
            "trial_successful_workers": 0,
            "trial_failed_workers": 0,
            "trial_parse_failures": 0,
            "passing_evidence": 0,
            "latest_run_id": "",
            "latest_trial_id": "",
            "team_names": [],
            "sample_runs": [],
            "sample_trials": [],
        })
        stats["same_input_trials"] += 1
        stats["trial_successful_workers"] += worker_success
        stats["trial_failed_workers"] += worker_fail
        stats["trial_parse_failures"] += parse_failures
        if trial_status == "pass":
            stats["passing_evidence"] += 1
        if not stats["latest_trial_id"]:
            stats["latest_trial_id"] = trial_dir.name
        if trial_team_name and trial_team_name not in stats["team_names"]:
            stats["team_names"].append(trial_team_name)
        if len(stats["sample_trials"]) < 5:
            stats["sample_trials"].append(trial_record)

    provider_rows: list[dict[str, Any]] = []
    for provider in sorted(provider_stats):
        stats = provider_stats[provider]
        has_runs = stats["runs"] > 0
        has_trials = stats["same_input_trials"] > 0
        has_evidence = has_runs or has_trials
        has_passing_evidence = stats.get("passing_evidence", 0) > 0
        status = "pass" if has_runs and stats["failed_workers"] == 0 and stats["compile_failures"] == 0 and stats["critical_reviews"] == 0 else "missing" if not has_runs else "warning"
        summary = (
            f"已有 {stats['runs']} 个 TeamBuilder 实战 run，成功 worker {stats['successful_workers']} 个。"
            if has_runs else "当前没有同口径 TeamBuilder codegen 实战 run。"
        )
        status = (
            "missing"
            if not has_evidence
            else "pass"
            if has_passing_evidence
            else "warning"
        )
        if has_runs and has_trials:
            summary = (
                f"已有 {stats['runs']} 个 TeamBuilder 实战 run 和 {stats['same_input_trials']} 条同口径试验；"
                f"实战成功 worker {stats['successful_workers']} 个，试验成功 {stats['trial_successful_workers']} 个，"
                f"试验失败 {stats['trial_failed_workers']} 个。"
            )
        elif has_runs:
            summary = f"已有 {stats['runs']} 个 TeamBuilder 实战 run，成功 worker {stats['successful_workers']} 个。"
        elif has_trials:
            summary = (
                f"已有 {stats['same_input_trials']} 条同口径试验；成功 worker {stats['trial_successful_workers']} 个，"
                f"失败 {stats['trial_failed_workers']} 个，源码解析失败 {stats['trial_parse_failures']} 个。"
            )
        else:
            summary = "当前没有同口径 TeamBuilder codegen 实战 run 或 provider trial。"
        provider_rows.append({
            "provider": provider,
            "label": "Claude Code" if provider == "claude-code" else "Codex" if provider == "codex" else provider,
            "role": "外部 codegen provider",
            "status": status,
            "summary": summary,
            "runs": stats["runs"],
            "successful_workers": stats["successful_workers"],
            "failed_workers": stats["failed_workers"],
            "compile_failures": stats["compile_failures"],
            "critical_reviews": stats["critical_reviews"],
            "same_input_trials": stats["same_input_trials"],
            "trial_successful_workers": stats["trial_successful_workers"],
            "trial_failed_workers": stats["trial_failed_workers"],
            "trial_parse_failures": stats["trial_parse_failures"],
            "passing_evidence": stats["passing_evidence"],
            "latest_run_id": stats["latest_run_id"],
            "latest_trial_id": stats["latest_trial_id"],
            "team_type_count": len(stats["team_names"]),
            "team_names": stats["team_names"],
            "sample_runs": stats["sample_runs"],
            "sample_trials": stats["sample_trials"],
        })

    qwen_role_covered = any(record["model"] in {"qwen-3.6-plus", "ide_agent", "runtime_main"} for record in model_records)
    model_rows = [{
        "provider": "qwen-3.6-plus",
        "label": "Qwen 3.6 Plus",
        "role": "内部 LLM 默认模型/role，不是 WorkerCodeOrchestrator external provider",
        "status": "warning" if qwen_role_covered else "missing",
        "summary": (
            "已有受控 LLM 回放桩证明内部 LLM worker 的调用形状；仍缺与 Claude Code/Codex 同一输入下的 codegen provider 对比。"
            if qwen_role_covered else "当前没有可归因到 qwen-3.6-plus 的 TeamBuilder 回放证据。"
        ),
        "runs": len({record["run_id"] for record in model_records}),
        "sample_records": model_records[:5],
    }]

    external_with_runs = [row["provider"] for row in provider_rows if row["runs"] > 0]
    external_with_evidence = [row["provider"] for row in provider_rows if row["runs"] > 0 or row.get("same_input_trials", 0) > 0]
    provider_by_id = {row["provider"]: row for row in provider_rows}
    same_team_names = sorted({
        _safe_text(item.get("team_name"), 160)
        for item in [*run_records, *trial_records]
        if _safe_text(item.get("team_name"), 160)
    })
    provider_team_type_counts = {
        provider: int(provider_by_id.get(provider, {}).get("team_type_count") or 0)
        for provider in tracked_external_providers
    }
    all_tracked_providers_cover_two_team_types = all(count >= 2 for count in provider_team_type_counts.values())
    missing: list[str] = []
    if len(external_with_evidence) < 2:
        missing.append("尚未形成 Claude Code 与 Codex 在同一 TeamBuilder 输入、同一权限、同一验证命令下的 codegen 质量对比。")
    missing.append("qwen-3.6-plus 当前只作为内部 LLM/role 或受控回放证据出现，还不是 WorkerCodeOrchestrator external provider 的同口径 codegen 实战证据。")
    if len(external_with_evidence) >= 2 and any(provider_by_id.get(provider, {}).get("status") != "pass" for provider in tracked_external_providers):
        missing.append("Codex 同口径试验已经执行，但产物没有解析成可用 worker 源码；下一步要修输出契约或解析器后重跑。")
    if len(same_team_names) < 2:
        missing.append("真实样本仍集中在少数 team 类型；还缺多类型 generated team 的 provider 覆盖。")
    elif not all_tracked_providers_cover_two_team_types:
        missing.append("第二类 team 类型还没有被 Claude Code 与 Codex 都覆盖；需要在同一 repo_absorption 设计输入上补齐 provider trial。")
    boundary_notes = [
        "qwen-3.6-plus 当前只作为内部 LLM/role 或受控回放证据出现；它不是 WorkerCodeOrchestrator external provider，不阻塞 Claude Code/Codex 外部 provider 对比。"
    ]
    missing = [item for item in missing if not item.startswith("qwen-3.6-plus")]
    missing = list(dict.fromkeys(item for item in missing if item))

    quality_gates = [
        _test_gate("external_provider_registry", "外部 provider 注册", "pass", "当前执行器边界已有 Claude Code 与 Codex；不需要为对比重新造执行器。", tracked_external_providers),
        _test_gate("claude_code_real_evidence", "Claude Code 实战样本", "pass" if "claude-code" in external_with_runs else "warning", "Claude Code 已有 TeamBuilder readonly/codegen 实战样本。" if "claude-code" in external_with_runs else "Claude Code 尚无 TeamBuilder 实战样本。", []),
        _test_gate("codex_same_path_evidence", "Codex 同口径样本", "pass" if "codex" in external_with_runs else "warning", "Codex 已有同口径 TeamBuilder codegen 样本。" if "codex" in external_with_runs else "Codex 执行器存在，但还没有 TeamBuilder 同口径 codegen 实战样本。", []),
        _test_gate(
            "team_type_coverage",
            "Team 类型覆盖",
            "pass" if all_tracked_providers_cover_two_team_types else "warning",
            (
                "Claude Code 与 Codex 都已覆盖至少两类 team。"
                if all_tracked_providers_cover_two_team_types
                else f"当前各 provider 覆盖的 team 类型数: {provider_team_type_counts}；需要 Claude Code 与 Codex 都覆盖第二类 team。"
            ),
            same_team_names,
        ),
        _test_gate("qwen_boundary_clear", "Qwen 边界清晰", "warning", "qwen-3.6-plus 是内部 LLM 默认模型/role 证据，不应被混同为 external codegen provider 证据。", []),
    ]
    codex_row = provider_by_id.get("codex", {})
    if codex_row.get("same_input_trials", 0) > 0:
        for gate in quality_gates:
            if gate["id"] == "codex_same_path_evidence":
                gate["status"] = "pass" if codex_row.get("status") == "pass" else "warning"
                gate["summary"] = (
                    "Codex 同口径试验已执行且通过。"
                    if gate["status"] == "pass"
                    else "Codex 同口径试验已执行，但输出没有解析成可用 worker 源码。"
                )
                gate["evidence"] = [_safe_text(codex_row.get("latest_trial_id"), 220)]
                break
    verdict = "comparison_ready" if not missing else "needs_more_evidence"
    report = {
        "available": bool(run_records or trial_records),
        "run_id": run_id,
        "verdict": verdict,
        "comparison_ready": verdict == "comparison_ready",
        "summary": (
            "Provider 覆盖已具备同口径比较证据。"
            if verdict == "comparison_ready" else f"Provider 覆盖仍不足：{len(missing)} 个缺口阻止宣称泛用。"
        ),
        "counts": {
            "runs_scanned": len(run_records),
            "same_input_trials_scanned": len(trial_records),
            "tracked_external_providers": len(tracked_external_providers),
            "external_providers_with_real_runs": len(external_with_runs),
            "external_providers_with_evidence": len(external_with_evidence),
            "internal_model_records": len(model_records),
            "team_types_seen": len(same_team_names),
            "provider_team_type_counts": provider_team_type_counts,
        },
        "providers": provider_rows,
        "internal_models": model_rows,
        "recent_runs": run_records[:8],
        "recent_same_input_trials": trial_records[:8],
        "quality_gates": quality_gates,
        "missing": missing,
        "boundary_notes": boundary_notes,
        "next_actions": [
            {
                "id": "run_codex_same_input_trial",
                "title": "补一条 Codex 同口径 TeamBuilder 样本",
                "summary": "用同一观察型 team 需求、同一 readonly/workspace 策略和同一测试/doctor/repair 验证命令跑 Codex，再与 Claude Code 样本对比。",
                "endpoint": "/api/team-builder-materialization/provider-same-input-trial/latest",
            },
        ],
        "source": {
            "provider_coverage_material": str(path.relative_to(_repo_root())) if path else "",
            "materialization_root": "_scratch/team_builder_real_material_validation",
            "provider_trial_root": "_scratch/team_builder_provider_trials",
        },
    }
    if verdict == "comparison_ready":
        report["next_actions"][0] = {
            "id": "provider_matrix_ready",
            "title": "Provider 覆盖已就绪，转入高标准审计",
            "summary": "Claude Code 与 Codex 已覆盖两类 team；下一步不再补 provider 样本，而是回到真实修复 apply/verify/rollback 决策。",
            "endpoint": "/api/team-builder-materialization/high-standard-audit/latest",
        }
    elif codex_row.get("same_input_trials", 0) > 0 and codex_row.get("status") != "pass":
        report["next_actions"][0] = {
            "id": "fix_codex_worker_output_contract",
            "title": "修 Codex worker 源码输出契约",
            "summary": "Codex 已经跑过同口径输入，但结果没有被解析成 worker；先保留输出摘要，再决定改 prompt、解析器或外部 agent 输出包装。",
            "endpoint": "/api/team-builder-materialization/provider-coverage/latest",
        }
    elif (
        len(external_with_evidence) >= 2
        and all(provider_by_id.get(provider, {}).get("status") == "pass" for provider in tracked_external_providers)
        and not all_tracked_providers_cover_two_team_types
    ):
        report["next_actions"][0] = {
            "id": "add_second_team_type_provider_sample",
            "title": "补第二类 TeamBuilder provider 样本",
            "summary": "Claude Code 和 Codex 已各有可用证据；下一步要换一类更复杂或不同结构的 generated team，复用同样的 codegen/test/doctor 审计口径。",
            "endpoint": "/api/team-builder-materialization/provider-coverage/latest",
        }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_provider_same_input_trial_plan_report() -> dict[str, Any]:
    run_dirs = _team_builder_real_materialization_run_dirs()
    root = _repo_root()
    baseline_run: Path | None = None
    baseline_summary: dict[str, Any] = {}
    for run_dir in run_dirs:
        summary = _read_json_file(run_dir / "summary.json")
        verification = _dict_value(summary.get("verification"))
        materials = _dict_value(summary.get("materials"))
        worker_bundle = _dict_value(materials.get("worker_code_files_bundle"))
        external_runs = _list_value(verification.get("external_agent_runs")) or _list_value(worker_bundle.get("external_agent_runs"))
        providers = {
            _safe_text(_dict_value(item).get("provider"), 80)
            for item in external_runs
            if _safe_text(_dict_value(item).get("provider"), 80)
        }
        if "claude-code" in providers or _safe_text(summary.get("provider"), 80) == "claude-code":
            baseline_run = run_dir
            baseline_summary = summary
            break
    run_id = baseline_run.name if baseline_run else (run_dirs[0].name if run_dirs else "standalone-provider-same-input-trial")
    material_path = _team_builder_provider_same_input_trial_plan_path(run_id)
    if baseline_run is None:
        report = {
            "available": False,
            "run_id": run_id,
            "verdict": "blocked",
            "ready": False,
            "summary": "没有找到可作为同口径基线的 Claude Code TeamBuilder run。",
            "counts": {"workers": 0, "materials": 0, "baseline_external_runs": 0, "missing": 1},
            "workers": [],
            "missing": ["需要先产生至少一个包含 claude-code external_agent_runs 的 TeamBuilder materialization summary。"],
            "safety_gates": [
                _test_gate("baseline", "Claude Code 基线", "fail", "缺少可复用基线 run。", []),
            ],
            "command": "",
            "next_actions": [
                {
                    "id": "produce_claude_code_baseline",
                    "title": "先产生 Claude Code 基线 run",
                    "summary": "同口径比较必须先有可复用的 baseline summary。",
                    "endpoint": "/api/team-builder-materialization/provider-coverage/latest",
                }
            ],
            "source": {
                "same_input_trial_plan_material": str(material_path.relative_to(root)) if material_path else "",
            },
        }
    else:
        from omnicompany.packages.services._core.team_builder.scripts.provider_same_input_trial import (
            build_trial_plan,
        )

        plan = build_trial_plan(
            root=root,
            baseline_run_dir=baseline_run,
            summary=baseline_summary,
            provider="codex",
            permission="readonly",
            model_policy="cheap",
            timeout_s=900.0,
        )
        report = {
            **plan,
            "run_id": run_id,
            "title": "Codex 同口径 TeamBuilder 试验计划",
            "next_actions": [
                {
                    "id": "execute_codex_same_input_trial",
                    "title": "执行只读 Codex 同口径试验",
                    "summary": "人工在终端执行计划命令；结果只写 _scratch/team_builder_provider_trials，并回到 provider 覆盖审计复查。",
                    "endpoint": "/api/team-builder-materialization/provider-coverage/latest",
                    "command": _safe_text(plan.get("command"), 900),
                }
            ],
            "source": {
                **_dict_value(plan.get("source")),
                "same_input_trial_plan_material": str(material_path.relative_to(root)) if material_path else "",
            },
        }
    if material_path:
        try:
            material_path.parent.mkdir(parents=True, exist_ok=True)
            material_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


def _team_builder_high_standard_audit_report() -> dict[str, Any]:
    material_report = _material_attribution_report()
    test_report = _team_builder_test_report()
    doctor_report = _team_builder_latest_doctor_findings_report()
    repair_plan = _team_builder_latest_repair_plan()
    closure = _team_builder_latest_closure_status()
    repair_closure = _team_builder_repair_closure_rollup_report()
    generalization = _team_builder_repair_generalization_trial_report()
    file_set_trial = _team_builder_real_generated_file_set_trial_report()
    real_run_closure = _team_builder_real_run_closure_rollup_report()
    llm_result = _team_builder_latest_llm_replay_result()
    provider_audit = _team_builder_provider_coverage_audit_report()

    run_id = (
        _safe_text(real_run_closure.get("run_id"), 160)
        or _safe_text(closure.get("run_id"), 160)
        or _safe_text(test_report.get("run_id"), 160)
        or _safe_text(material_report.get("run_id"), 160)
    )
    team_name = (
        _safe_text(closure.get("team_name"), 160)
        or _safe_text(test_report.get("team_name"), 160)
        or _safe_text(material_report.get("team_name"), 160)
    )

    closure_stages = {_safe_text(item.get("name"), 80): _dict_value(item) for item in _list_value(closure.get("stages"))}
    material_counts = _dict_value(material_report.get("counts"))
    test_counts = _dict_value(test_report.get("counts"))
    doctor_counts = _dict_value(doctor_report.get("counts"))
    repair_counts = _dict_value(repair_plan.get("counts"))
    real_counts = _dict_value(real_run_closure.get("counts"))
    file_set_counts = _dict_value(file_set_trial.get("counts"))
    generalization_counts = _dict_value(generalization.get("counts"))
    provider_comparison_ready = _safe_text(provider_audit.get("verdict"), 80) == "comparison_ready"

    def deliverable(
        item_id: str,
        name: str,
        status: str,
        summary: str,
        evidence: list[str],
        endpoint: str,
        next_action: str = "",
    ) -> dict[str, Any]:
        return {
            "id": item_id,
            "name": name,
            "status": status if status in {"pass", "warning", "fail"} else "warning",
            "summary": _safe_text(summary, 700),
            "evidence": [_safe_text(item, 260) for item in evidence[:8]],
            "endpoint": endpoint,
            "next_action": _safe_text(next_action, 420),
        }

    build_stage = _dict_value(closure_stages.get("建立 team"))
    test_stage = _dict_value(closure_stages.get("测试 team"))
    doctor_stage = _dict_value(closure_stages.get("诊断分析"))
    repair_stage = _dict_value(closure_stages.get("修复准备"))
    real_ready_to_apply = int(real_counts.get("ready_to_apply") or 0)
    real_applied = int(real_counts.get("applied") or 0)
    real_verified = int(real_counts.get("verified") or 0)
    real_reconciled = int(real_counts.get("reconciled") or 0)
    real_rollback_ready = int(real_counts.get("rollback_ready") or 0)
    real_rolled_back = int(real_counts.get("rolled_back") or 0)
    real_apply_rehearsal_passed = int(real_counts.get("apply_rehearsal_passed") or 0)
    real_apply_rehearsal_blocked = int(real_counts.get("apply_rehearsal_blocked") or 0)
    real_apply_rehearsal_required_fields = int(real_counts.get("apply_rehearsal_required_fields") or 0)
    real_apply_rehearsal_missing_required_fields = int(real_counts.get("apply_rehearsal_missing_required_fields") or 0)

    deliverables = [
        deliverable(
            "build_team",
            "建立 team",
            _safe_text(build_stage.get("status"), 40) or "warning",
            _safe_text(build_stage.get("summary"), 700) or "生成包结构状态未知。",
            [
                f"files={test_counts.get('files', 0)}",
                f"worker_files={test_counts.get('worker_files', 0)}",
                f"team={team_name}",
            ],
            "/api/team-builder-materialization/test-report/latest",
        ),
        deliverable(
            "materialize_team",
            "全内容 material 化",
            _safe_text(material_report.get("verdict"), 40) if material_report.get("verdict") in {"pass", "warning", "fail"} else "warning",
            _safe_text(material_report.get("summary"), 700) or "material 归因报告不可用。",
            [
                f"confirmed_reads={material_counts.get('confirmed_reads', 0)}",
                f"read_clues={material_counts.get('read_clues', 0)}",
                f"unconfirmed={material_counts.get('unconfirmed_read_clues', 0)}",
            ],
            "/api/team-builder-materialization/report/latest",
            "继续把未确认读取线索升级、保留为候选或明确作废。" if material_report.get("verdict") != "pass" else "",
        ),
        deliverable(
            "test_team",
            "测试 team",
            _safe_text(test_stage.get("status"), 40) or "warning",
            _safe_text(test_stage.get("summary"), 700) or "测试报告状态未知。",
            [
                f"executed_workers={test_counts.get('executed_workers', 0)}",
                f"failed_workers={test_counts.get('failed_workers', 0)}",
                f"llm_replay={_safe_text(llm_result.get('verdict'), 40)}",
            ],
            "/api/team-builder-materialization/closure/latest",
        ),
        deliverable(
            "diagnose_team",
            "诊断分析 team",
            _safe_text(doctor_stage.get("status"), 40) or "warning",
            _safe_text(doctor_stage.get("summary"), 700) or "doctor 状态未知。",
            [
                f"doctor_total={doctor_counts.get('total', 0)}",
                f"blocking={doctor_counts.get('blocking', 0)}",
                f"advisory={doctor_counts.get('advisory', 0)}",
            ],
            "/api/team-builder-materialization/doctor-findings/latest",
        ),
        deliverable(
            "repair_team_preparation",
            "修复准备与安全网",
            _safe_text(repair_stage.get("status"), 40) or "warning",
            _safe_text(repair_plan.get("summary"), 700) or "修复准备计划状态未知。",
            [
                f"repair_required={repair_counts.get('repair_required', 0)}",
                f"validation_gap={repair_counts.get('validation_gap', 0)}",
                f"repair_closure={_safe_text(repair_closure.get('verdict'), 80)}",
            ],
            "/api/team-builder-materialization/repair-closure-rollup/latest",
        ),
        deliverable(
            "repair_real_failed_run",
            "真实失败 run 修复闭环",
            "warning" if real_ready_to_apply and not real_applied else "pass" if real_applied and real_rolled_back else "warning",
            _safe_text(real_run_closure.get("summary"), 700) or "真实失败 run 修复总览不可用。",
            [
                f"ready_to_apply={real_ready_to_apply}",
                f"apply_rehearsal_passed={real_apply_rehearsal_passed}",
                f"apply_rehearsal_blocked={real_apply_rehearsal_blocked}",
                f"apply_rehearsal_required_fields={real_apply_rehearsal_required_fields}",
                f"apply_rehearsal_missing_required_fields={real_apply_rehearsal_missing_required_fields}",
                f"applied={real_applied}",
                f"rolled_back={real_rolled_back}",
                f"next={_safe_text((_list_value(real_run_closure.get('next_actions')) or [{}])[0].get('id'), 160)}",
            ],
            "/api/team-builder-materialization/repair-real-run-closure-rollup/latest",
            "应用前演练已通过时也只是安全网；仍等待明确批准后执行一次真实失败 run 显式 apply，再做应用后回放、对账和回滚验收。" if real_ready_to_apply and not real_applied else "",
        ),
        deliverable(
            "generalization",
            "泛用性与文件集试验",
            "warning",
            (
                "provider 已覆盖两类 team；当前剩余泛化风险主要是真实失败 run 仍只验证到一个候选 apply 前状态。"
                if provider_comparison_ready
                else "已有受控泛化和文件集试验，但真实失败 run 仍只验证到一个候选 apply 前状态，provider 同口径覆盖也未完成。"
            ),
            [
                f"generalization_candidates={generalization_counts.get('candidate_count', 0)}",
                f"multi_file_candidates={generalization_counts.get('multi_file_candidate_count', 0)}",
                f"file_set_trial={_safe_text(file_set_trial.get('verdict'), 80)}",
                f"file_set_changed_files={file_set_counts.get('changed_files', 0)}",
                f"provider_audit={_safe_text(provider_audit.get('verdict'), 80)}",
            ],
            "/api/team-builder-materialization/repair-generalization-trial/latest",
            "继续扩大真实失败 run 样本；provider 同口径覆盖已就绪后不再把补 Codex 当作当前阻塞项。" if provider_comparison_ready else "继续扩大真实失败 run 样本，并对不同 provider 的生成质量做同口径比较。",
        ),
    ]

    missing = [_safe_text(item, 520) for item in _list_value(closure.get("missing"))]
    if real_ready_to_apply and not real_applied:
        missing.append("真实失败 run 修复仍停在显式 apply 审批前；尚未完成应用后回放验证、结果对账和回滚验收。")
    if _safe_text(material_report.get("verdict"), 40) != "pass":
        missing.append("material 读写归因仍有 warning；未确认读取线索不能当成事实 material 读边。")
    if _safe_text(provider_audit.get("verdict"), 80) != "comparison_ready":
        provider_missing = _list_value(provider_audit.get("missing"))
        missing.append(_safe_text(provider_missing[0], 520) if provider_missing else "还没有多 provider、多类型 team 的高标准对比。")
    missing = list(dict.fromkeys(item for item in missing if item))

    quality_gates = [
        _test_gate(
            "genericity",
            "泛用性",
            "warning",
            (
                "provider 同口径对比已覆盖两类 team；真实失败 run 样本仍少，仍需完成真实 apply/verify/rollback 后再宣称高标准完成。"
                if provider_comparison_ready
                else "已有泛化试验和文件集试验，但真实失败 run 样本仍少，provider 同口径对比仍未完成。"
            ),
            [f"candidate_count={generalization_counts.get('candidate_count', 0)}", f"provider_audit={_safe_text(provider_audit.get('verdict'), 80)}", f"apply_rehearsal_passed={real_apply_rehearsal_passed}"],
        ),
        _test_gate("observability", "观测面", "pass", "dashboard 已提供 material 归因、测试、doctor、repair、真实失败 run 总览和本审计报告。", ["dashboard_cards=true"]),
        _test_gate("facilities", "设施", "pass", "主要报告都有固定 API、material 路径和回归测试；真实写入仍由显式 POST 控制。", ["pytest", "playwright", "material_reports"]),
        _test_gate(
            "robustness",
            "鲁棒性",
            "warning" if real_ready_to_apply and not real_applied else "pass",
            (
                "应用前演练可验证 before/after/rollback 和 required 字段读取语义，但真实 apply 之前仍不会伪装成已修复；等待应用状态保持 warning/action_required。"
                if real_apply_rehearsal_passed
                else "真实 apply 之前不会伪装成已修复；等待应用状态保持 warning/action_required。"
            ),
            [f"real_ready_to_apply={real_ready_to_apply}", f"real_applied={real_applied}", f"apply_rehearsal_passed={real_apply_rehearsal_passed}", f"apply_rehearsal_blocked={real_apply_rehearsal_blocked}", f"apply_rehearsal_required_fields={real_apply_rehearsal_required_fields}", f"apply_rehearsal_missing_required_fields={real_apply_rehearsal_missing_required_fields}"],
        ),
    ]
    deliverable_by_id = {item["id"]: item for item in deliverables}
    gate_by_id = {item["id"]: item for item in quality_gates}

    def audit_item(
        item_id: str,
        requirement: str,
        artifact: str,
        status: str,
        evidence: list[str],
        conclusion: str,
        *,
        covered_by_tests: list[str] | None = None,
        gap: str = "",
    ) -> dict[str, Any]:
        return {
            "id": item_id,
            "requirement": requirement,
            "artifact": artifact,
            "status": status,
            "evidence": [_safe_text(item, 520) for item in evidence],
            "covered_by_tests": covered_by_tests or [],
            "conclusion": _safe_text(conclusion, 900),
            "gap": _safe_text(gap, 900),
        }

    completion_audit_items = [
        audit_item(
            "objective_build_team",
            "TeamBuilder 可以建立 team，且生成包结构可被 dashboard 和测试报告读取。",
            "/api/team-builder-materialization/test-report/latest",
            _safe_text(deliverable_by_id.get("build_team", {}).get("status"), 40) or "warning",
            _list_value(deliverable_by_id.get("build_team", {}).get("evidence")),
            _safe_text(deliverable_by_id.get("build_team", {}).get("summary"), 700),
            covered_by_tests=["tests/dashboard/test_catalogue_material_attribution.py", "tests/e2e/team_graph.spec.ts"],
        ),
        audit_item(
            "objective_test_team",
            "TeamBuilder 可以测试 team，包括 contract、worker smoke、LLM 回放边界和 closure。",
            "/api/team-builder-materialization/closure/latest",
            _safe_text(deliverable_by_id.get("test_team", {}).get("status"), 40) or "warning",
            _list_value(deliverable_by_id.get("test_team", {}).get("evidence")),
            _safe_text(deliverable_by_id.get("test_team", {}).get("summary"), 700),
            covered_by_tests=["tests/teams/team_observer_material_trial/test_contract.py", "tests/e2e/team_graph.spec.ts"],
        ),
        audit_item(
            "objective_diagnose_team",
            "TeamBuilder 可以诊断分析 team，把 finding 定位到 worker/material/边/运行事件。",
            "/api/team-builder-materialization/doctor-findings/latest",
            _safe_text(deliverable_by_id.get("diagnose_team", {}).get("status"), 40) or "warning",
            _list_value(deliverable_by_id.get("diagnose_team", {}).get("evidence")),
            _safe_text(deliverable_by_id.get("diagnose_team", {}).get("summary"), 700),
            covered_by_tests=["tests/dashboard/test_catalogue_material_attribution.py", "tests/e2e/team_graph.spec.ts"],
        ),
        audit_item(
            "objective_repair_team",
            "TeamBuilder 可以修复 team：finding 驱动、显式应用、应用后验证、结果对账、回滚和回滚后验证。",
            "/api/team-builder-materialization/repair-real-run-closure-rollup/latest",
            _safe_text(deliverable_by_id.get("repair_real_failed_run", {}).get("status"), 40) or "warning",
            _list_value(deliverable_by_id.get("repair_real_failed_run", {}).get("evidence")),
            _safe_text(deliverable_by_id.get("repair_real_failed_run", {}).get("summary"), 700),
            covered_by_tests=["tests/dashboard/test_catalogue_material_attribution.py"],
            gap=_safe_text(deliverable_by_id.get("repair_real_failed_run", {}).get("next_action"), 900),
        ),
        audit_item(
            "quality_genericity",
            "每阶段必须具备泛用性，不只在单一样本上成立。",
            "/api/team-builder-materialization/provider-coverage/latest",
            _safe_text(gate_by_id.get("genericity", {}).get("status"), 40) or "warning",
            _list_value(gate_by_id.get("genericity", {}).get("evidence")),
            _safe_text(gate_by_id.get("genericity", {}).get("summary"), 900),
            covered_by_tests=["tests/dashboard/test_catalogue_material_attribution.py", "tests/team_builder/test_provider_baseline_from_snapshot.py"],
            gap="provider 已覆盖两类 team；真实失败 run 样本和真实 apply/rollback 仍未完成。" if provider_comparison_ready else "还需要补齐 provider 同口径覆盖和更多真实失败 run 样本。",
        ),
        audit_item(
            "quality_observability",
            "每阶段必须有可观测面，dashboard 能解释结构、健康、证据和下一步。",
            "dashboard TeamBuilder 页面",
            _safe_text(gate_by_id.get("observability", {}).get("status"), 40) or "warning",
            _list_value(gate_by_id.get("observability", {}).get("evidence")),
            _safe_text(gate_by_id.get("observability", {}).get("summary"), 900),
            covered_by_tests=["tests/e2e/team_graph.spec.ts"],
        ),
        audit_item(
            "quality_facilities",
            "每阶段必须有设施支撑，包括固定 API、material、测试和显式安全门。",
            "catalogue API + materials + pytest/playwright",
            _safe_text(gate_by_id.get("facilities", {}).get("status"), 40) or "warning",
            _list_value(gate_by_id.get("facilities", {}).get("evidence")),
            _safe_text(gate_by_id.get("facilities", {}).get("summary"), 900),
            covered_by_tests=["python -m py_compile", "python -m pytest", "npx tsc -b", "npx playwright test"],
        ),
        audit_item(
            "quality_robustness",
            "每阶段必须有鲁棒性，不能把演练、预览或普通 closure 误判为完整修复。",
            "/api/team-builder-materialization/high-standard-audit/latest",
            _safe_text(gate_by_id.get("robustness", {}).get("status"), 40) or "warning",
            _list_value(gate_by_id.get("robustness", {}).get("evidence")),
            _safe_text(gate_by_id.get("robustness", {}).get("summary"), 900),
            covered_by_tests=["tests/dashboard/test_catalogue_material_attribution.py"],
            gap="真实 apply 前必须保持 in_progress；应用前演练通过也不能替代 POST apply、回放验证、对账和回滚验收。" if real_ready_to_apply and not real_applied else "",
        ),
    ]
    prompt_to_artifact_checklist = {
        "objective": "完整建立 team、测试 team、诊断分析 team，并修复 team；每阶段同时满足泛用性、观测面、设施和鲁棒性。",
        "completion_rule": "只有全部 checklist status 为 pass，且 missing 为空，且 quality_gates 全部为 pass，才允许标记 complete。",
        "status": "complete" if all(item["status"] == "pass" and not item["gap"] for item in completion_audit_items) and not missing else "not_complete",
        "items": completion_audit_items,
        "uncovered_or_incomplete": [_safe_text(item, 900) for item in missing],
    }
    completion_ready = not missing and all(_safe_text(item.get("status"), 40) == "pass" for item in quality_gates)
    verdict = "complete" if completion_ready else "in_progress"
    path = _team_builder_high_standard_audit_path(run_id)
    report = {
        "available": True,
        "run_id": run_id,
        "team_name": team_name,
        "verdict": verdict,
        "completion_ready": completion_ready,
        "summary": (
            "TeamBuilder 高标准闭环已满足当前目标。"
            if completion_ready else f"TeamBuilder 高标准闭环仍在推进中：{len(missing)} 个缺口阻止宣称完整完成。"
        ),
        "deliverables": deliverables,
        "quality_gates": quality_gates,
        "prompt_to_artifact_checklist": prompt_to_artifact_checklist,
        "missing": missing,
        "next_actions": [
            {
                "id": "review_real_run_apply_decision",
                "title": "审阅真实失败 run 显式应用审批包",
                "summary": "先由人审阅目标文件、确认 token、应用后验证和回滚要求，再决定是否执行 POST apply。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-closure-rollup/latest",
            }
        ] if real_ready_to_apply and not real_applied else [
            {
                "id": "expand_real_run_samples",
                "title": "扩大真实 run 样本与 provider 对比",
                "summary": "继续增加真实失败 run、不同 team 类型和不同 external provider 的同口径验证。",
                "endpoint": "/api/team-builder-materialization/repair-real-run-candidate-scan/latest",
            }
        ],
        "source": {
            "high_standard_audit_material": str(path.relative_to(_repo_root())) if path else "",
            "closure_endpoint": "/api/team-builder-materialization/closure/latest",
            "repair_closure_endpoint": "/api/team-builder-materialization/repair-closure-rollup/latest",
            "real_run_closure_endpoint": "/api/team-builder-materialization/repair-real-run-closure-rollup/latest",
            "provider_coverage_endpoint": "/api/team-builder-materialization/provider-coverage/latest",
        },
    }
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return report


@catalogue_router.get("/teams")
async def list_teams() -> dict[str, Any]:
    items = _scan_teams_cached(_root_token())
    return {"items": items, "total": len(items)}


@catalogue_router.get("/teams/{team_id:path}")
async def get_team(team_id: str) -> dict[str, Any]:
    return _get_one(_scan_teams_cached, "team", team_id)


@catalogue_router.get("/team-graph/{team_id:path}")
async def get_team_graph(team_id: str, builder: str | None = None) -> dict[str, Any]:
    return _load_team_graph_data(team_id, builder)


@catalogue_router.get("/team-doctor/{team_id:path}")
async def get_team_doctor(team_id: str, builder: str | None = None) -> dict[str, Any]:
    return _build_team_doctor_health(team_id, builder)


@catalogue_router.get("/team-runs/{team_id:path}")
async def get_team_runs(
    team_id: str,
    builder: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    scan_limit: int = Query(20000, ge=100, le=50000),
) -> dict[str, Any]:
    graph = _load_team_graph_data(team_id, builder)
    runs = _collect_team_runs(graph, max_events_per_db=scan_limit, event_dbs=_team_run_event_db_sources())
    return {
        "team_id": team_id,
        "spec_id": graph.get("spec_id"),
        "selected_builder": graph.get("selected_builder"),
        "items": runs[:limit],
        "total": len(runs),
    }


@catalogue_router.get("/team-run-detail/{team_id:path}")
async def get_team_run_detail(
    team_id: str,
    trace_id: str = Query(..., min_length=1),
    builder: str | None = None,
) -> dict[str, Any]:
    graph = _load_team_graph_data(team_id, builder)
    node_ids = {str(node.get("id")) for node in graph.get("nodes", []) if node.get("id")}
    material_ids = {str(material.get("id")) for material in graph.get("materials", []) if material.get("id")}
    events = _load_trace_events(trace_id, event_dbs=_team_run_event_db_sources())
    matched_events = [
        event
        for event in events
        if _event_matches_team(event, graph, node_ids, material_ids)
    ]
    summary = _summarize_team_trace(trace_id, events, matched_events, node_ids) if events else {
        "trace_id": trace_id,
        "task_desc": None,
        "source": "",
        "domains": [],
        "started_at": None,
        "ended_at": None,
        "event_count": 0,
        "matched_event_count": 0,
        "matched_nodes": [],
        "total_nodes": len(node_ids),
        "tool_calls": 0,
        "llm_calls": 0,
        "agent_turns": 0,
        "status": "missing",
        "verdict_counts": {},
        "last_event": None,
    }
    active_nodes = sorted({
        node_id
        for event in matched_events
        for node_id in _node_ids_for_event(event, node_ids)
    })
    return {
        "team_id": team_id,
        "spec_id": graph.get("spec_id"),
        "selected_builder": graph.get("selected_builder"),
        "trace_id": trace_id,
        "summary": summary,
        "active_nodes": active_nodes,
        "inactive_nodes": sorted(node_ids - set(active_nodes)),
        "node_statuses": _node_statuses_for_events(matched_events, node_ids),
        "material_observations": _material_observations(matched_events, node_ids),
        "timeline": _team_run_timeline(events, node_ids),
    }


@catalogue_router.get("/team-builder-materialization/latest")
async def get_latest_team_builder_materialization(
    worker: str | None = None,
    material: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    return _latest_team_builder_materialization(worker=worker, material=material, target=target)


@catalogue_router.get("/team-builder-materialization/report/latest")
async def get_latest_team_builder_materialization_report(
    worker: str | None = None,
    material: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    return _material_attribution_report(worker=worker, material=material, target=target)


@catalogue_router.get("/team-builder-materialization/test-report/latest")
async def get_latest_team_builder_test_report() -> dict[str, Any]:
    return _team_builder_test_report()


@catalogue_router.get("/team-builder-materialization/contract-execution/latest")
async def get_latest_team_builder_contract_execution() -> dict[str, Any]:
    return _team_builder_latest_contract_execution_report()


@catalogue_router.post("/team-builder-materialization/contract-execution/execute")
async def execute_latest_team_builder_contracts() -> dict[str, Any]:
    return _team_builder_execute_contracts_report()


@catalogue_router.get("/team-builder-materialization/doctor-findings/latest")
async def get_latest_team_builder_doctor_findings() -> dict[str, Any]:
    return _team_builder_latest_doctor_findings_report()


@catalogue_router.get("/team-builder-materialization/repair-plan/latest")
async def get_latest_team_builder_repair_plan() -> dict[str, Any]:
    return _team_builder_latest_repair_plan()


@catalogue_router.get("/team-builder-materialization/repair-probe/latest")
async def get_latest_team_builder_repair_probe() -> dict[str, Any]:
    return _team_builder_repair_probe_report()


@catalogue_router.get("/team-builder-materialization/repair-dry-run/latest")
async def get_latest_team_builder_repair_dry_run() -> dict[str, Any]:
    return _team_builder_repair_dry_run_report()


@catalogue_router.get("/team-builder-materialization/repair-patch-candidates/latest")
async def get_latest_team_builder_repair_patch_candidates() -> dict[str, Any]:
    return _team_builder_repair_patch_candidates_report()


@catalogue_router.get("/team-builder-materialization/repair-patch-diff-proposal/latest")
async def get_latest_team_builder_repair_patch_diff_proposal() -> dict[str, Any]:
    return _team_builder_repair_patch_diff_proposal_report()


@catalogue_router.get("/team-builder-materialization/repair-approval/latest")
async def get_latest_team_builder_repair_approval() -> dict[str, Any]:
    return _team_builder_repair_approval_report()


@catalogue_router.post("/team-builder-materialization/repair-approval/record")
async def record_team_builder_repair_approval(payload: dict[str, Any]) -> dict[str, Any]:
    return _team_builder_record_repair_approval(payload)


@catalogue_router.get("/team-builder-materialization/repair-apply-gate/latest")
async def get_latest_team_builder_repair_apply_gate() -> dict[str, Any]:
    return _team_builder_repair_apply_gate_report()


@catalogue_router.get("/team-builder-materialization/repair-execution-readiness/latest")
async def get_latest_team_builder_repair_execution_readiness() -> dict[str, Any]:
    return _team_builder_repair_execution_readiness_report()


@catalogue_router.get("/team-builder-materialization/repair-apply-preview/latest")
async def get_latest_team_builder_repair_apply_preview() -> dict[str, Any]:
    return _team_builder_repair_apply_preview_report()


@catalogue_router.get("/team-builder-materialization/repair-apply-execution/latest")
async def get_latest_team_builder_repair_apply_execution() -> dict[str, Any]:
    return _team_builder_repair_apply_execution_report()


@catalogue_router.post("/team-builder-materialization/repair-apply-execution/execute")
async def execute_team_builder_repair_apply(payload: dict[str, Any]) -> dict[str, Any]:
    return _team_builder_execute_repair_apply(payload)


@catalogue_router.get("/team-builder-materialization/repair-post-apply-verification/latest")
async def get_latest_team_builder_repair_post_apply_verification() -> dict[str, Any]:
    return _team_builder_repair_post_apply_verification_report()


@catalogue_router.post("/team-builder-materialization/repair-post-apply-verification/execute")
async def execute_team_builder_repair_post_apply_verification(payload: dict[str, Any]) -> dict[str, Any]:
    return _team_builder_execute_repair_post_apply_verification(payload)


@catalogue_router.get("/team-builder-materialization/repair-outcome-reconciliation/latest")
async def get_latest_team_builder_repair_outcome_reconciliation() -> dict[str, Any]:
    return _team_builder_repair_outcome_reconciliation_report()


@catalogue_router.get("/team-builder-materialization/repair-rollback-readiness/latest")
async def get_latest_team_builder_repair_rollback_readiness() -> dict[str, Any]:
    return _team_builder_repair_rollback_readiness_report()


@catalogue_router.get("/team-builder-materialization/repair-rollback-execution/latest")
async def get_latest_team_builder_repair_rollback_execution() -> dict[str, Any]:
    return _team_builder_repair_rollback_execution_report()


@catalogue_router.post("/team-builder-materialization/repair-rollback-execution/execute")
async def execute_team_builder_repair_rollback(payload: dict[str, Any]) -> dict[str, Any]:
    return _team_builder_execute_repair_rollback(payload)


@catalogue_router.get("/team-builder-materialization/repair-rollback-post-verification/latest")
async def get_latest_team_builder_repair_rollback_post_verification() -> dict[str, Any]:
    return _team_builder_repair_rollback_post_verification_report()


@catalogue_router.post("/team-builder-materialization/repair-rollback-post-verification/execute")
async def execute_team_builder_repair_rollback_post_verification(payload: dict[str, Any]) -> dict[str, Any]:
    return _team_builder_execute_repair_rollback_post_verification(payload)


@catalogue_router.get("/team-builder-materialization/repair-closure-rollup/latest")
async def get_latest_team_builder_repair_closure_rollup() -> dict[str, Any]:
    return _team_builder_repair_closure_rollup_report()


@catalogue_router.get("/team-builder-materialization/repair-generalization-trial/latest")
async def get_latest_team_builder_repair_generalization_trial() -> dict[str, Any]:
    return _team_builder_repair_generalization_trial_report()


@catalogue_router.get("/team-builder-materialization/repair-real-generated-file-set-trial/latest")
async def get_latest_team_builder_repair_real_generated_file_set_trial() -> dict[str, Any]:
    return _team_builder_real_generated_file_set_trial_report()


@catalogue_router.get("/team-builder-materialization/repair-real-run-candidate-scan/latest")
async def get_latest_team_builder_repair_real_run_candidate_scan() -> dict[str, Any]:
    return _team_builder_repair_real_run_candidate_scan_report()


@catalogue_router.get("/team-builder-materialization/repair-real-run-replay-plan/latest")
async def get_latest_team_builder_repair_real_run_replay_plan() -> dict[str, Any]:
    return _team_builder_repair_real_run_replay_plan_report()


@catalogue_router.get("/team-builder-materialization/repair-real-run-diff-preview/latest")
async def get_latest_team_builder_repair_real_run_diff_preview() -> dict[str, Any]:
    return _team_builder_repair_real_run_diff_preview_report()


@catalogue_router.get("/team-builder-materialization/repair-real-run-diff-review/latest")
async def get_latest_team_builder_repair_real_run_diff_review() -> dict[str, Any]:
    return _team_builder_repair_real_run_diff_review_report()


@catalogue_router.get("/team-builder-materialization/repair-real-run-apply-gate/latest")
async def get_latest_team_builder_repair_real_run_apply_gate() -> dict[str, Any]:
    return _team_builder_repair_real_run_apply_gate_report()


@catalogue_router.get("/team-builder-materialization/repair-real-run-apply-preview/latest")
async def get_latest_team_builder_repair_real_run_apply_preview() -> dict[str, Any]:
    return _team_builder_repair_real_run_apply_preview_report()


@catalogue_router.get("/team-builder-materialization/repair-real-run-apply-execution/latest")
async def get_latest_team_builder_repair_real_run_apply_execution() -> dict[str, Any]:
    return _team_builder_real_run_apply_execution_report()


@catalogue_router.post("/team-builder-materialization/repair-real-run-apply-execution/execute")
async def execute_team_builder_repair_real_run_apply_execution(payload: dict[str, Any]) -> dict[str, Any]:
    return _team_builder_execute_real_run_apply(payload)


@catalogue_router.get("/team-builder-materialization/repair-real-run-apply-rehearsal/latest")
async def get_latest_team_builder_repair_real_run_apply_rehearsal() -> dict[str, Any]:
    return _team_builder_real_run_apply_rehearsal_report()


@catalogue_router.get("/team-builder-materialization/repair-real-run-auto-apply-policy/latest")
async def get_latest_team_builder_repair_real_run_auto_apply_policy() -> dict[str, Any]:
    return _team_builder_real_run_auto_apply_policy_report()


@catalogue_router.post("/team-builder-materialization/repair-real-run-auto-apply-execution/execute")
async def execute_team_builder_repair_real_run_auto_apply_execution(payload: dict[str, Any]) -> dict[str, Any]:
    return _team_builder_execute_real_run_auto_apply(payload)


@catalogue_router.get("/team-builder-materialization/repair-real-run-post-apply-verification/latest")
async def get_latest_team_builder_repair_real_run_post_apply_verification() -> dict[str, Any]:
    return _team_builder_real_run_post_apply_verification_report()


@catalogue_router.post("/team-builder-materialization/repair-real-run-post-apply-verification/execute")
async def execute_team_builder_repair_real_run_post_apply_verification(payload: dict[str, Any]) -> dict[str, Any]:
    return _team_builder_execute_real_run_post_apply_verification(payload)


@catalogue_router.get("/team-builder-materialization/repair-real-run-outcome-reconciliation/latest")
async def get_latest_team_builder_repair_real_run_outcome_reconciliation() -> dict[str, Any]:
    return _team_builder_real_run_outcome_reconciliation_report()


@catalogue_router.get("/team-builder-materialization/repair-real-run-rollback-readiness/latest")
async def get_latest_team_builder_repair_real_run_rollback_readiness() -> dict[str, Any]:
    return _team_builder_real_run_rollback_readiness_report()


@catalogue_router.get("/team-builder-materialization/repair-real-run-rollback-execution/latest")
async def get_latest_team_builder_repair_real_run_rollback_execution() -> dict[str, Any]:
    return _team_builder_real_run_rollback_execution_report()


@catalogue_router.post("/team-builder-materialization/repair-real-run-rollback-execution/execute")
async def execute_team_builder_repair_real_run_rollback_execution(payload: dict[str, Any]) -> dict[str, Any]:
    return _team_builder_execute_real_run_rollback(payload)


@catalogue_router.get("/team-builder-materialization/repair-real-run-rollback-post-verification/latest")
async def get_latest_team_builder_repair_real_run_rollback_post_verification() -> dict[str, Any]:
    return _team_builder_real_run_rollback_post_verification_report()


@catalogue_router.post("/team-builder-materialization/repair-real-run-rollback-post-verification/execute")
async def execute_team_builder_repair_real_run_rollback_post_verification(payload: dict[str, Any]) -> dict[str, Any]:
    return _team_builder_execute_real_run_rollback_post_verification(payload)


@catalogue_router.get("/team-builder-materialization/repair-real-run-closure-rollup/latest")
async def get_latest_team_builder_repair_real_run_closure_rollup() -> dict[str, Any]:
    return _team_builder_real_run_closure_rollup_report()


@catalogue_router.get("/team-builder-materialization/repair-safety-policy/latest")
async def get_latest_team_builder_repair_safety_policy() -> dict[str, Any]:
    return _team_builder_latest_repair_safety_policy()


@catalogue_router.get("/team-builder-materialization/material-gap-validation/latest")
async def get_latest_team_builder_material_gap_validation() -> dict[str, Any]:
    return _team_builder_material_gap_validation_report()


@catalogue_router.get("/team-builder-materialization/read-clue-resolution/latest")
async def get_latest_team_builder_read_clue_resolution() -> dict[str, Any]:
    return _team_builder_latest_read_clue_resolution_plan()


@catalogue_router.get("/team-builder-materialization/llm-replay-plan/latest")
async def get_latest_team_builder_llm_replay_plan() -> dict[str, Any]:
    return _team_builder_latest_llm_replay_plan()


@catalogue_router.get("/team-builder-materialization/llm-replay-result/latest")
async def get_latest_team_builder_llm_replay_result() -> dict[str, Any]:
    return _team_builder_latest_llm_replay_result()


@catalogue_router.post("/team-builder-materialization/llm-replay/execute")
async def execute_team_builder_llm_replay() -> dict[str, Any]:
    return _team_builder_execute_llm_replay()


@catalogue_router.get("/team-builder-materialization/closure/latest")
async def get_latest_team_builder_closure_status() -> dict[str, Any]:
    return _team_builder_latest_closure_status()


@catalogue_router.get("/team-builder-materialization/high-standard-audit/latest")
async def get_latest_team_builder_high_standard_audit() -> dict[str, Any]:
    return _team_builder_high_standard_audit_report()


@catalogue_router.get("/team-builder-materialization/provider-coverage/latest")
async def get_latest_team_builder_provider_coverage_audit() -> dict[str, Any]:
    return _team_builder_provider_coverage_audit_report()


@catalogue_router.get("/team-builder-materialization/provider-same-input-trial/latest")
async def get_latest_team_builder_provider_same_input_trial_plan() -> dict[str, Any]:
    return _team_builder_provider_same_input_trial_plan_report()


# ── Materials ───────────────────────────────────────────────────────────


@catalogue_router.get("/materials")
async def list_materials() -> dict[str, Any]:
    items = _scan_materials_cached(_root_token())
    return {"items": items, "total": len(items)}


@catalogue_router.get("/materials/{material_id:path}")
async def get_material(material_id: str) -> dict[str, Any]:
    return _get_one(_scan_materials_cached, "material", material_id)
