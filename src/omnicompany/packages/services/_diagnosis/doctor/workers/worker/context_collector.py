# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.worker.cross_source_context_collector.py"
"""WorkerContextCollector — 跨 source_root 上下文收集 (HARD, Stage 3 2026-04-22).

Worker 协议:
  FORMAT_IN  = diag.worker.sig-checked
  FORMAT_OUT = diag.worker.context

诊断目标: 根据 FORMAT_IN/OUT 在整个 source_root 搜索上下文信息:
  1. FORMAT_IN / FORMAT_OUT 的 Format 对象定义 (来自任何 formats.py)
  2. 上游 Router (FORMAT_OUT == 本 Router 的 FORMAT_IN 的其他 Router)
  3. 下游 Router (FORMAT_IN == 本 Router 的 FORMAT_OUT 的其他 Router)
  4. Pipeline 引用 (哪条 pipeline.py 用到了本 Router 类)

永远返回 PASS; 搜索失败只记录到 context_gaps.

扩展: Clean Migration 后 Router 类可能分散到 _archive/*_legacy.py 或
workers/*.py, 本 Worker 同时扫 routers.py / routers/*.py / workers/**/*.py /
_archive/*_legacy.py, 避免拓扑发现遗漏.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import DEFAULT_SOURCE_ROOT, get_call_repr

# 从 material 子域复用 AST 工具 (Material/Format 定义提取)
from ..material._shared import extract_kwargs, iter_format_calls


class WorkerContextCollector(Worker):
    """跨 source_root 收集 Format 定义 + 上下游 Router + Pipeline 引用."""

    DESCRIPTION = "跨 source_root 收集 FORMAT_IN/OUT 定义 + 上下游 Router DESCRIPTION + Pipeline 引用; 永远 PASS"
    FORMAT_IN = "diag.worker.sig-checked"
    FORMAT_OUT = "diag.worker.context"
    INPUT_KEYS = ["worker_class", "source_root", "extracted"]

    def run(self, input_data: Any) -> Verdict:
        worker_class: str = input_data["worker_class"]
        source_root = Path(input_data.get("source_root", DEFAULT_SOURCE_ROOT))
        extracted: dict = input_data.get("extracted", {})

        # 防御: FORMAT_IN/OUT 可能是 list (多入口 Router), 取第一个元素做搜索
        _raw_in = extracted.get("format_in")
        _raw_out = extracted.get("format_out")
        format_in_id: str | None = _raw_in[0] if isinstance(_raw_in, list) else _raw_in
        format_out_id: str | None = _raw_out[0] if isinstance(_raw_out, list) else _raw_out

        context_gaps: list[str] = []
        if isinstance(_raw_in, list):
            context_gaps.append(
                f"FORMAT_IN 为列表 ({_raw_in}), 多入口 Router; 上游搜索仅用第一个元素"
            )

        format_in_def: dict | None = None
        format_out_def: dict | None = None
        upstream_routers: list[dict] = []
        downstream_routers: list[dict] = []
        pipeline_brief: dict | None = None

        # ── 搜索 1: Format 定义 ──
        if format_in_id:
            format_in_def = self._find_format_def(source_root, format_in_id)
            if format_in_def is None:
                context_gaps.append(
                    f"FORMAT_IN 定义未找到 ({format_in_id} 不在任何 formats.py 中)"
                )
        if format_out_id:
            format_out_def = self._find_format_def(source_root, format_out_id)
            if format_out_def is None:
                context_gaps.append(
                    f"FORMAT_OUT 定义未找到 ({format_out_id} 不在任何 formats.py 中)"
                )

        # ── 搜索 2: 上下游 Router ──
        if format_in_id or format_out_id:
            upstream_routers, downstream_routers = self._find_neighbors(
                source_root, format_in_id, format_out_id, worker_class
            )
            if not upstream_routers and format_in_id:
                context_gaps.append(
                    f"无上游 Router (无 Router 的 FORMAT_OUT={format_in_id})"
                )

        # ── 搜索 3: Pipeline 引用 ──
        format_in_kind = extracted.get("format_in_kind", "literal")
        format_out_kind = extracted.get("format_out_kind", "literal")
        pipeline_briefs = self._find_pipeline_ref(
            source_root, worker_class, format_in_id, format_out_id
        )
        pipeline_brief = pipeline_briefs[0] if pipeline_briefs else None
        if not pipeline_briefs:
            has_fstring_format = (format_in_kind == "fstring" or format_out_kind == "fstring")
            if has_fstring_format:
                context_gaps.append(
                    "FORMAT_IN/OUT 为 f-string, 无法静态确认 pipeline 归属 (不一定孤立)"
                )
            else:
                _src_file = Path(input_data.get("source_file", "") or "")
                _has_local_pipeline = False
                if _src_file.parent.is_dir():
                    _has_local_pipeline = any(
                        "pipeline" in p.name.lower() or "team" in p.name.lower()
                        for p in _src_file.parent.iterdir()
                        if p.suffix == ".py"
                    )
                if _has_local_pipeline:
                    context_gaps.append(
                        "同 package 有 pipeline/team 文件, 但 format IDs 不可静态匹配"
                        " (pipeline 可能使用 f-string format IDs)"
                    )
                else:
                    context_gaps.append("未在任何 pipeline.py 中使用")

        # ── composite Format 感知 ──
        is_composite_format_in = False
        composite_components: list[str] = []
        if format_in_def and format_in_def.get("components"):
            is_composite_format_in = True
            composite_components = format_in_def["components"]
            upstream_format_outs = [
                r.get("format_out") for r in upstream_routers
                if r.get("format_out")
            ]
            missing_components = [
                c for c in composite_components if c not in upstream_format_outs
            ]
            if missing_components:
                context_gaps.append(
                    f"FORMAT_IN '{format_in_id}' 是 composite Format (components={composite_components}), "
                    f"但上游未覆盖这些 component: {missing_components}"
                )

        context = {
            "format_in_def": format_in_def,
            "format_out_def": format_out_def,
            "upstream_routers": upstream_routers,
            "downstream_routers": downstream_routers,
            "pipeline_brief": pipeline_brief,
            "pipeline_briefs": pipeline_briefs,
            "pipeline_purpose": pipeline_brief.get("purpose", "") if pipeline_brief else "",
            "context_gaps": context_gaps,
            "is_composite_format_in": is_composite_format_in,
            "composite_components": composite_components,
        }

        output = dict(input_data)
        output["context"] = context

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=f"RouterContextCollector: {worker_class} gaps={len(context_gaps)}",
        )

    def _find_format_def(self, source_root: Path, material_id: str) -> dict | None:
        """在 source_root 下所有 formats.py / formats/*.py 中查找 Format 定义."""
        candidates: list[Path] = []
        for p in source_root.rglob("formats.py"):
            if "__pycache__" not in str(p) and "_graveyard" not in str(p):
                candidates.append(p)
        for p in source_root.rglob("formats/*.py"):
            if "__pycache__" not in str(p) and "_graveyard" not in str(p):
                candidates.append(p)

        for py_file in candidates:
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if material_id not in content:
                continue
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue

            for call_node in iter_format_calls(tree, material_id):
                kw = extract_kwargs(call_node)
                if kw.get("id") == material_id:
                    return {
                        "id": kw.get("id"),
                        "name": kw.get("name"),
                        "description": kw.get("description"),
                        "examples": kw.get("examples"),
                        "tags": kw.get("tags"),
                        "json_schema": kw.get("json_schema"),
                        "parent": kw.get("parent"),
                        "components": kw.get("components"),
                    }
        return None

    def _find_neighbors(
        self,
        source_root: Path,
        format_in_id: str | None,
        format_out_id: str | None,
        self_class: str,
    ) -> tuple[list[dict], list[dict]]:
        """扫 routers.py / routers/*.py / workers/**/*.py / _archive/*_legacy.py 查上下游 Router."""
        upstream: list[dict] = []
        downstream: list[dict] = []

        router_files: list[Path] = []
        for p in source_root.rglob("routers.py"):
            if "__pycache__" not in str(p) and "_graveyard" not in str(p):
                router_files.append(p)
        for p in source_root.rglob("routers/*.py"):
            if "__pycache__" not in str(p) and "_graveyard" not in str(p):
                router_files.append(p)
        # Stage 3 Clean Migration: Router 业务代码在 workers/ 独立文件
        for p in source_root.rglob("workers/**/*.py"):
            if "__pycache__" not in str(p) and "_graveyard" not in str(p):
                router_files.append(p)
        # Stage 2 Diamond 过渡期: 部分业务仍在 _archive
        for p in source_root.rglob("_archive/*_legacy.py"):
            if "__pycache__" not in str(p) and "_graveyard" not in str(p):
                router_files.append(p)

        for py_file in router_files:
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            has_relevant = (format_in_id and format_in_id in content) or (
                format_out_id and format_out_id in content
            )
            if not has_relevant:
                continue
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if node.name == self_class:
                    continue

                cls_vars: dict = {}
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Name) and t.id in ("FORMAT_IN", "FORMAT_OUT", "DESCRIPTION"):
                                try:
                                    cls_vars[t.id] = ast.literal_eval(stmt.value)
                                except Exception:
                                    cls_vars[t.id] = None

                cls_format_in = cls_vars.get("FORMAT_IN")
                cls_format_out = cls_vars.get("FORMAT_OUT")
                cls_desc = cls_vars.get("DESCRIPTION", "")

                if format_in_id and cls_format_out == format_in_id:
                    upstream.append({"class": node.name, "description": cls_desc or "",
                                      "format_in": cls_format_in, "format_out": cls_format_out})

                if format_out_id and cls_format_in == format_out_id:
                    downstream.append({"class": node.name, "description": cls_desc or "",
                                        "format_in": cls_format_in, "format_out": cls_format_out})

        # 去重 (同一 class 可能在多处被发现)
        def _dedup(items: list[dict]) -> list[dict]:
            seen: set[str] = set()
            out = []
            for item in items:
                if item["class"] not in seen:
                    seen.add(item["class"])
                    out.append(item)
            return out

        return _dedup(upstream), _dedup(downstream)

    def _find_pipeline_ref(
        self,
        source_root: Path,
        worker_class: str,
        format_in: str | None = None,
        format_out: str | None = None,
    ) -> list[dict]:
        """在 source_root 下的 pipeline/team 文件中查找本 Router 的所有引用.

        返回所有命中的 pipeline 简述列表 (可能为空).
        策略 1: 类名搜索 (直接 import 风格的老式管线)
        策略 2: FORMAT_IN/OUT 匹配 (AnchorSpec/TransformerSpec/TeamNode 风格)
        """
        pipeline_files: list[Path] = []
        _seen: set[Path] = set()
        for _pat in ("pipeline.py", "*_pipeline.py", "pipeline_*.py", "team.py", "*_team.py"):
            for p in source_root.rglob(_pat):
                if "__pycache__" not in str(p) and "_graveyard" not in str(p) and p not in _seen:
                    pipeline_files.append(p)
                    _seen.add(p)

        results: list[dict] = []

        # ── 策略 1: 类名搜索 ──
        for py_file in pipeline_files:
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(content)
            except Exception:
                continue
            if worker_class not in content:
                continue
            pipeline_id = self._extract_pipeline_id_from_tree(tree) or py_file.stem
            purpose = self._extract_pipeline_purpose_from_tree(tree)
            node_id = None
            for line in content.splitlines():
                stripped = line.strip()
                if worker_class in stripped:
                    m = re.search(r'id\s*=\s*["\']([^"\']+)["\']', stripped)
                    if m:
                        node_id = m.group(1)
            results.append({
                "pipeline_id": pipeline_id,
                "node_id": node_id,
                "node_kind": None,
                "purpose": purpose,
            })

        # ── 策略 2: FORMAT_IN/OUT 匹配 ──
        if format_in or format_out:
            matched_pipelines = {r["pipeline_id"] for r in results}
            for py_file in pipeline_files:
                matches = self._match_pipeline_by_format(py_file, format_in, format_out)
                for m in matches:
                    if m["pipeline_id"] not in matched_pipelines:
                        results.append(m)
                        matched_pipelines.add(m["pipeline_id"])

        return results

    def _match_pipeline_by_format(
        self,
        pipeline_file: Path,
        format_in: str | None,
        format_out: str | None,
    ) -> list[dict]:
        """AST 解析 pipeline 文件, 按 AnchorSpec/TransformerSpec 的 FORMAT_IN/OUT 匹配."""
        try:
            content = pipeline_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content)
        except Exception:
            return []

        pipeline_id = self._extract_pipeline_id_from_tree(tree) or pipeline_file.stem
        purpose = self._extract_pipeline_purpose_from_tree(tree)
        results: list[dict] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = get_call_repr(node.func)
            if func_name not in ("AnchorSpec", "TransformerSpec"):
                continue

            node_format_in: str | None = None
            node_format_out: str | None = None
            node_id: str | None = None

            for kw in node.keywords:
                val = kw.value
                if kw.arg == "id" and isinstance(val, ast.Constant):
                    node_id = val.value
                elif kw.arg == "format_in":
                    if isinstance(val, ast.Constant):
                        node_format_in = val.value
                    elif isinstance(val, ast.List):
                        for elt in val.elts:
                            if isinstance(elt, ast.Constant) and format_in and elt.value == format_in:
                                node_format_in = elt.value
                elif kw.arg == "format_out":
                    if isinstance(val, ast.Constant):
                        node_format_out = val.value

            in_match = (format_in is None) or (node_format_in == format_in)
            out_match = (format_out is None) or (node_format_out == format_out)

            if in_match and out_match and (node_format_in is not None or node_format_out is not None):
                results.append({
                    "pipeline_id": pipeline_id,
                    "node_id": node_id,
                    "node_kind": "anchor" if func_name == "AnchorSpec" else "transformer",
                    "purpose": purpose,
                })
                break

        return results

    def _extract_pipeline_id_from_tree(self, tree: ast.Module) -> str | None:
        """从 TeamSpec/TeamSpec(id=...) 调用中提取 pipeline ID."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = get_call_repr(node.func)
                if func_name in ("TeamSpec", "TeamSpec"):
                    for kw in node.keywords:
                        if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                            return kw.value.value
        return None

    def _extract_pipeline_purpose_from_tree(self, tree: ast.Module) -> str:
        """从 TeamSpec/TeamSpec(purpose=... / description=...) 提取业务目标."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = get_call_repr(node.func)
                if func_name in ("TeamSpec", "TeamSpec"):
                    for kw in node.keywords:
                        if kw.arg in ("purpose", "description") and isinstance(kw.value, ast.Constant):
                            return kw.value.value
        return ""
