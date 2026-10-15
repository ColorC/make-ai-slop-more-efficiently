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

from .catalogue_common import (
    SKIP_PARTS,
    _repo_root,
    _packages_root,
    _is_skipped,
    _safe_text,
    _to_camel_identifier,
    _to_snake_identifier,
    _doctor_level_counts,
    _read_json_file,
    _dict_value,
    _list_value,
)

catalogue_router = APIRouter()









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




























































































# services 树 basename->[paths] 索引(按 services 根 mtime 缓存)。取代每次 rglob 整棵树:
# team-builder closure 状态会按每条工具事件反复调用候选路径解析, 原来每次都 rglob 整个 services
# 包树, 是事件循环上的头号 CPU 占用源(stackdump 5 次抓到 3 次)。建一次索引, 后续 O(1) 查。


























































































































































































































































































































@catalogue_router.get("/teams")
def list_teams() -> dict[str, Any]:
    items = _scan_teams_cached(_root_token())
    return {"items": items, "total": len(items)}


@catalogue_router.get("/teams/{team_id:path}")
def get_team(team_id: str) -> dict[str, Any]:
    return _get_one(_scan_teams_cached, "team", team_id)


@catalogue_router.get("/team-graph/{team_id:path}")
def get_team_graph(team_id: str, builder: str | None = None) -> dict[str, Any]:
    return _load_team_graph_data(team_id, builder)


@catalogue_router.get("/team-doctor/{team_id:path}")
def get_team_doctor(team_id: str, builder: str | None = None) -> dict[str, Any]:
    return _build_team_doctor_health(team_id, builder)


@catalogue_router.get("/team-runs/{team_id:path}")
def get_team_runs(
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
def get_team_run_detail(
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
























































































































# ── Materials ───────────────────────────────────────────────────────────


@catalogue_router.get("/materials")
def list_materials() -> dict[str, Any]:
    items = _scan_materials_cached(_root_token())
    return {"items": items, "total": len(items)}


@catalogue_router.get("/materials/{material_id:path}")
def get_material(material_id: str) -> dict[str, Any]:
    return _get_one(_scan_materials_cached, "material", material_id)


