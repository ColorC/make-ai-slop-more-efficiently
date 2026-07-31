# [OMNI] origin=omnifactory domain=omnifactory/doctor ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:diagnosis.doctor.router.business_logic.legacy.py"
# OMNI-024 ALLOW: _archive/ 归档文件, Router 类不在标准位置属预期 (Phase D 历史参考, Stage 3 已迁完)
# [OMNI] DEPRECATED 2026-04-22 — Stage 3 Clean Migration 完成, 全部 22 个 Router 已迁到
#   workers/{format,router,pipeline}/*.py 独立文件, 不再被 workers/ 继承.
#   保留作为历史参考. 新代码严禁从本文件 import Router 类.
"""doctor.routers — Format 诊断管线的 Router 实现

  FormatExtractorRouter          (HARD) 用 AST 从 formats.py 提取 Format 对象字段
  SignatureDiffRouter            (HARD, Anchor) 校验 Format 对象是否存在；不存在则短路到 HealthWriter
  FiveElementCheckRouter         (HARD) 检查 Format 对象五要素完整性
  TagCoverageRouter              (HARD) 检查 ID 命名规范与 tags 域标签覆盖
  ParentChainRouter              (HARD) 检查管线连通性与 parent 字段合法性
  CompositeFormatCheckRouter     (HARD) 检查 composite Format 的 components 合法性和描述意图
  ExamplePresenceCheckRouter     (HARD) 检查 Format.examples 列表质量
  FormatContextualAuditRouter    (LLM) 全语境语义审计：format + 上下游 Router 源码 + 完整标准
  HealthWriterRouter             (HARD) 汇总所有检查结果生成健康档案
"""

from __future__ import annotations

import ast
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)

# 默认 source root（omnifactory 项目的 src/omnifactory/）
_DEFAULT_SOURCE_ROOT = Path("e:/WindowsWorkspace/omnifactory/src/omnifactory")

# ── HealthArchive 可选集成 ──────────────────────────────────────────────────
try:
    from omnicompany.packages.services._core.registry.archive import (
        HealthArchive as _HealthArchive,
        make_router_snapshot as _make_router_snapshot,
        make_format_snapshot as _make_format_snapshot,
        write_proximity_snapshot as _write_proximity,
    )
    from omnicompany.packages.services._core.registry.scanner import _infer_package as _infer_pkg
    _ARCHIVE_AVAILABLE = True
    _REGISTRY_ARCHIVE_DIR = Path(__file__).parents[5] / "data" / "registry" / "health"
except ImportError:
    _ARCHIVE_AVAILABLE = False
    _REGISTRY_ARCHIVE_DIR = Path(".")

# Format ID 应含域前缀：domain.something
_DOMAIN_PATTERN = re.compile(r"^[a-z][a-z0-9_-]+\.[a-z]")

# 语义类型后缀白名单（用于 TagCoverage ID 命名检查）
_SEMANTIC_SUFFIXES = (
    "-request", "-report", "-record", "-result", "-response",
    "-state", "-action", "-observation", "-context",
    ".fmt.", "fmt.",
)

# 用途关键词：FORMAT_IN / FORMAT_OUT 角色标识
_INPUT_ROLES = ("FORMAT_IN", "format_in", "from_format")
_OUTPUT_ROLES = ("FORMAT_OUT", "format_out", "to_format")


# ════════════════════════════════════════════════════════════════
# AST 工具函数（供 FormatExtractorRouter 使用）
# ════════════════════════════════════════════════════════════════

def _is_format_call(node: ast.Call) -> bool:
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "Format") or (
        isinstance(func, ast.Attribute) and func.attr == "Format"
    )


def _extract_kwargs(call_node: ast.Call) -> dict:
    """从 Format() 调用提取关键字参数（literal_eval；失败记 None）。"""
    kw_dict: dict = {}
    for kw in call_node.keywords:
        if kw.arg is None:
            continue
        try:
            kw_dict[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, TypeError):
            kw_dict[kw.arg] = None
    return kw_dict


def _iter_format_calls(tree: ast.Module, format_id: str):
    """遍历整个 AST，yield 所有 id==format_id 的 Format() 调用节点。
    顶层赋值优先（先 tree.body，再其余节点）。"""
    seen: set[int] = set()
    # Pass 1: 顶层 Assign — 可以提取常量名
    for stmt in tree.body:
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        value = getattr(stmt, "value", None)
        if not isinstance(value, ast.Call) or not _is_format_call(value):
            continue
        kw = _extract_kwargs(value)
        if kw.get("id") == format_id:
            seen.add(id(value))
            yield value
    # Pass 2: 全文 walk — 处理 list/函数内部定义
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_format_call(node):
            continue
        if id(node) in seen:
            continue
        kw = _extract_kwargs(node)
        if kw.get("id") == format_id:
            yield node


def _find_constant_name(tree: ast.Module, target_call: ast.Call) -> str | None:
    """如果 target_call 是顶层 `CONST = Format(...)` 的值，返回常量名，否则 None。"""
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            if stmt.value is target_call and stmt.targets:
                t = stmt.targets[0]
                if isinstance(t, ast.Name):
                    return t.id
        elif isinstance(stmt, ast.AnnAssign):
            if stmt.value is target_call and isinstance(stmt.target, ast.Name):
                return stmt.target.id
    return None


# ════════════════════════════════════════════════════════════════
# FormatExtractorRouter — 用 AST 提取 Format 对象字段 + 用途清单
# ════════════════════════════════════════════════════════════════


class FormatExtractorRouter(Router):
    """扫描 source_root 下所有 formats.py，用 AST 找到 Format(id="...", ...) 实例，
    提取 id/name/description/examples/tags/parent 等关键字参数（format_obj）。
    同时扫描全部 .py 文件，收集 FORMAT_IN/FORMAT_OUT 引用（usages）。
    """

    DESCRIPTION = "用 AST 从 formats.py 提取 Format 对象字段；扫描全部源码收集 FORMAT_IN/OUT 引用"
    FORMAT_IN = "doctor.fmt.request"
    FORMAT_OUT = "doctor.fmt.extracted"
    INPUT_KEYS = ["format_id"]

    def __init__(self, source_root: str | None = None):
        self._source_root = Path(source_root) if source_root else _DEFAULT_SOURCE_ROOT

    def run(self, input_data: Any) -> Verdict:
        format_id: str = input_data["format_id"]
        source_root = Path(input_data.get("source_root", self._source_root))

        format_obj: dict = {}
        constant_name: str | None = None
        defined_in: str | None = None

        # ── Step 1: AST 扫描所有 formats.py，找 Format() 实例 ──
        for py_file in source_root.rglob("formats.py"):
            if "__pycache__" in str(py_file) or "_graveyard" in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # 快速过滤：format_id 必须出现在文件中
            if format_id not in content:
                continue
            try:
                tree = ast.parse(content, filename=str(py_file))
            except SyntaxError:
                continue

            try:
                rel = str(py_file.relative_to(source_root.parent))
            except ValueError:
                rel = str(py_file)

            # 两轮扫描：
            #   Pass 1 — 顶层 Assign，可以提取常量名（FORMAT_X = Format(...)）
            #   Pass 2 — ast.walk 全文，处理 FORMATS = [Format(...)] 等嵌套模式
            for call_node in _iter_format_calls(tree, format_id):
                kw_dict = _extract_kwargs(call_node)
                if kw_dict.get("id") != format_id:
                    continue
                # 尝试找常量名（仅顶层赋值有意义）
                const = _find_constant_name(tree, call_node)
                constant_name = const or "(list/func)"
                format_obj = kw_dict
                defined_in = rel
                break

            if format_obj:
                break  # 找到即停

        # ── Step 2: 扫描全部 .py 文件收集 FORMAT_IN/OUT 引用 ──
        usages: list[dict] = []
        for py_file in source_root.rglob("*.py"):
            if "__pycache__" in str(py_file) or "_graveyard" in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if format_id not in content:
                continue
            try:
                rel = str(py_file.relative_to(source_root.parent))
            except ValueError:
                rel = str(py_file)

            for lineno, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if format_id not in stripped:
                    continue
                if not any(k in stripped for k in (*_INPUT_ROLES, *_OUTPUT_ROLES)):
                    continue
                role_tokens = []
                if any(k in stripped for k in _INPUT_ROLES):
                    role_tokens.append("INPUT")
                if any(k in stripped for k in _OUTPUT_ROLES):
                    role_tokens.append("OUTPUT")
                usages.append({
                    "file": rel,
                    "lineno": lineno,
                    "role": "+".join(role_tokens) or "UNKNOWN",
                    "line": stripped[:120],
                })

        found = bool(format_obj)

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "format_id": format_id,
                "source_root": str(source_root),
                "found": found,
                "defined_in": defined_in,
                "constant_name": constant_name,
                "format_obj": format_obj,
                "usages": usages,
            },
            diagnosis=f"FormatExtractor: {format_id} found={found} usages={len(usages)}",
        )


# ════════════════════════════════════════════════════════════════
# SignatureDiffRouter — 校验 Format 对象是否存在（Anchor，可短路）
# ════════════════════════════════════════════════════════════════


class SignatureDiffRouter(Router):
    """校验 Format ID 是否在某个 formats.py 中有 Format() 对象定义。

    PASS: 找到 Format() 对象 → 进入完整检查链
    FAIL: 未找到 → 直接写入最小健康档案（short-circuit to HealthWriter）
    """

    DESCRIPTION = "校验 Format ID 是否以 Format() 对象形式定义于 formats.py；PASS 透传 extracted，FAIL 短路 EMIT 最小健康档案"
    FORMAT_IN = "doctor.fmt.extracted"
    FORMAT_OUT = "doctor.fmt.extracted"
    INPUT_KEYS = ["format_id", "found"]

    def run(self, input_data: Any) -> Verdict:
        format_id: str = input_data["format_id"]
        found: bool = input_data.get("found", False)
        format_obj: dict = input_data.get("format_obj", {})
        defined_in: str | None = input_data.get("defined_in")
        constant_name: str | None = input_data.get("constant_name")

        if not found or not format_obj:
            detail = (
                "Format ID 在所有 formats.py 中均未找到 Format() 对象定义"
                if not found else
                "找到文件但未能提取 Format 对象字段（AST 解析失败）"
            )
            # 直接输出最小健康档案（EMIT 路由，不进入下游检查链）
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={
                    "format_id": format_id,
                    "source_root": input_data.get("source_root", ""),
                    "checks": [{
                        "check": "sig_diff",
                        "passed": False,
                        "severity": "CRITICAL",
                        "detail": detail,
                    }],
                    "health_score": 0.0,
                    "health_grade": "F",
                    "critical_failures": ["sig_diff"],
                    "summary": f"Format '{format_id}' 未找到 Format 对象定义，无法诊断",
                    "sig_diff_ok": False,
                },
                diagnosis=f"SignatureDiff FAIL: {format_id} — {detail}",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "format_id": format_id,
                "source_root": input_data.get("source_root", ""),
                "extracted": input_data,  # 整个 extraction 结果透传
                "sig_diff_ok": True,
            },
            diagnosis=f"SignatureDiff PASS: {format_id}",
        )


# ════════════════════════════════════════════════════════════════
# FiveElementCheckRouter — Format 对象五要素完整性检查
# ════════════════════════════════════════════════════════════════


class FiveElementCheckRouter(Router):
    """检查 Format 对象五要素是否完整：
      1. id 含域前缀（domain.something）
      2. name 字段非空
      3. description 字段非空
      4. examples 非空列表（[PLANNED] 格式豁免）
      5. tags 非空列表
    """

    DESCRIPTION = "检查 Format 对象五要素：id 域前缀 / name / description / examples / tags 均非空"
    FORMAT_IN = "doctor.fmt.extracted"
    FORMAT_OUT = "doctor.fmt.check.five-element"

    def run(self, input_data: Any) -> Verdict:
        format_id: str = input_data["format_id"]
        extracted = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})

        # 判断是否是 [PLANNED] 格式（豁免 examples 要求）
        desc = format_obj.get("description") or ""
        tags = format_obj.get("tags") or []
        is_planned = "[PLANNED" in desc.upper() or "planned" in [t.lower() for t in tags]

        sub_checks = []

        # 1. id 含域前缀
        has_domain = bool(_DOMAIN_PATTERN.match(format_id))
        sub_checks.append(("id 含域前缀", has_domain,
                           f"'{format_id}' 应以 'domain.something' 形式"))

        # 2. name 非空
        name = format_obj.get("name") or ""
        has_name = bool(name.strip())
        sub_checks.append(("name 非空", has_name,
                           f"name='{name}'" if has_name else "name 字段缺失或为空"))

        # 3. description 非空
        has_desc = bool(desc.strip())
        sub_checks.append(("description 非空", has_desc,
                           f"description 存在（{len(desc)} 字符）" if has_desc else "description 字段缺失或为空"))

        # 4. examples 非空列表 OR json_schema 非空（两者均可满足类型说明要求）
        examples = format_obj.get("examples")
        if examples is None:
            examples = []
        json_schema = format_obj.get("json_schema")
        has_examples = isinstance(examples, list) and len(examples) > 0
        has_schema = isinstance(json_schema, dict) and len(json_schema) > 0
        has_type_info = has_examples or has_schema
        if is_planned and not has_type_info:
            sub_checks.append(("examples/json_schema 非空", True, "[PLANNED] 格式豁免示例要求"))
        else:
            detail_ok = (
                f"共 {len(examples)} 个示例" if has_examples
                else f"json_schema 存在（{len(json_schema)} 个顶层字段）" if has_schema
                else "examples 和 json_schema 均为空（需至少提供一种类型说明）"
            )
            sub_checks.append(("examples/json_schema 非空", has_type_info, detail_ok))

        # 5. tags 非空列表
        has_tags = isinstance(tags, list) and len(tags) > 0
        sub_checks.append(("tags 非空列表", has_tags,
                           f"tags={tags}" if has_tags else "tags 为空列表或缺失"))

        passed_count = sum(1 for _, ok, _ in sub_checks if ok)
        all_pass = passed_count == len(sub_checks)

        check_result = {
            "check": "five_element",
            "passed": all_pass,
            "severity": "HIGH" if not all_pass else "INFO",
            "detail": f"{passed_count}/{len(sub_checks)} 要素通过",
            "sub_checks": [
                {"name": n, "passed": ok, "detail": d}
                for n, ok, d in sub_checks
            ],
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "format_id": format_id,
                "source_root": input_data.get("source_root", ""),
                "sig_diff_ok": input_data.get("sig_diff_ok", True),
                "extracted": input_data.get("extracted", {}),
                "check_five_element": check_result,
            },
            diagnosis=f"FiveElementCheck: {passed_count}/{len(sub_checks)} passed",
        )


# ════════════════════════════════════════════════════════════════
# TagCoverageRouter — ID 命名规范与 tags 域标签覆盖检查
# ════════════════════════════════════════════════════════════════


class TagCoverageRouter(Router):
    """检查 Format 命名和标签覆盖：
      1. ID 全小写无非法字符（允许下划线、连字符、点）
      2. tags 包含与 ID 域前缀匹配的域标签（如 "guardian.*" 应有 "guardian" tag）
    """

    DESCRIPTION = "检查 Format ID 命名规范（小写合法字符）与 tags 域标签覆盖"
    FORMAT_IN = "doctor.fmt.extracted"
    FORMAT_OUT = "doctor.fmt.check.tag-coverage"

    def run(self, input_data: Any) -> Verdict:
        format_id: str = input_data["format_id"]
        extracted = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})
        tags: list = format_obj.get("tags") or []

        sub_checks = []

        # 1. ID 全小写 + 合法字符（允许下划线，与 _DOMAIN_PATTERN 保持一致）
        legal_chars = bool(re.match(r"^[a-z0-9._\-]+$", format_id))
        sub_checks.append(("ID 全小写无非法字符", legal_chars,
                           f"'{format_id}' 含大写或非法字符" if not legal_chars else "OK"))

        # 2. tags 含域标签（ID 第一段域名出现在任意 tag 中，连字符/下划线等价）
        domain = format_id.split(".")[0] if "." in format_id else ""
        if domain:
            domain_norm = domain.replace("-", "_")
            has_domain_tag = any(
                domain in tag or domain_norm in tag.replace("-", "_")
                for tag in tags
            )
        else:
            has_domain_tag = True
        sub_checks.append(("tags 含域标签", has_domain_tag,
                           f"tags={tags} 中未见 '{domain}' 或 '{domain.replace('-','_')}'" if not has_domain_tag
                           else f"OK（tag 含 '{domain}'）"))

        passed_count = sum(1 for _, ok, _ in sub_checks if ok)
        all_pass = passed_count == len(sub_checks)

        check_result = {
            "check": "tag_coverage",
            "passed": all_pass,
            "severity": "MEDIUM" if not all_pass else "INFO",
            "detail": f"{passed_count}/{len(sub_checks)} 命名/标签规范通过",
            "sub_checks": [
                {"name": n, "passed": ok, "detail": d}
                for n, ok, d in sub_checks
            ],
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "format_id": format_id,
                "source_root": input_data.get("source_root", ""),
                "sig_diff_ok": input_data.get("sig_diff_ok", True),
                "extracted": input_data.get("extracted", {}),
                "check_tag_coverage": check_result,
            },
            diagnosis=f"TagCoverage: {passed_count}/{len(sub_checks)} passed",
        )


# ════════════════════════════════════════════════════════════════
# ParentChainRouter — 管线连通性与 parent 字段合法性检查
# ════════════════════════════════════════════════════════════════


class ParentChainRouter(Router):
    """检查 Format 的 parent 字段合法性，并记录管线连通性（仅注记，不评分）。

    设计原则：
      - 连通性（是否有 FORMAT_IN/OUT 引用）反映管线实现状态，不是 Format 定义质量
        → 连通性仅在 detail 中注记，不作为 pass/fail 子项
      - parent 字段是 Format 定义必要元素：缺失或值不合法才降分
        → 只检查 parent 的存在性和格式合法性

    parent 合法值：'requirement' 或形如 'domain.something' 的合法 Format ID
    """

    DESCRIPTION = "检查 parent 字段合法性；连通性（INPUT/OUTPUT 引用数）仅注记不评分"
    FORMAT_IN = "doctor.fmt.extracted"
    FORMAT_OUT = "doctor.fmt.check.parent-chain"

    def run(self, input_data: Any) -> Verdict:
        format_id: str = input_data["format_id"]
        extracted = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})
        usages: list = extracted.get("usages", [])
        parent: str = format_obj.get("parent") or ""

        input_usages = [u for u in usages if "INPUT" in u.get("role", "")]
        output_usages = [u for u in usages if "OUTPUT" in u.get("role", "")]

        sub_checks = []

        # ── parent 字段合法性（唯一评分项）──
        if parent:
            parent_ok = parent == "requirement" or (
                "." in parent and bool(_DOMAIN_PATTERN.match(parent))
            )
            sub_checks.append(("parent 格式合法", parent_ok,
                               f"parent='{parent}'" if parent_ok
                               else f"parent='{parent}' 不是 'requirement' 或合法 Format ID"))
        else:
            sub_checks.append(("parent 字段存在", False,
                               "Format 对象缺少 parent 字段（应为 'requirement' 或父 Format ID）"))

        passed_count = sum(1 for _, ok, _ in sub_checks if ok)
        all_pass = passed_count == len(sub_checks)

        # 连通性注记（不参与评分，写入 detail）
        conn_note = f"INPUT {len(input_usages)} 处，OUTPUT {len(output_usages)} 处"
        if len(input_usages) == 0 and len(output_usages) == 0:
            conn_note += " [孤立，尚无实现节点]"

        check_result = {
            "check": "parent_chain",
            "passed": all_pass,
            "severity": "HIGH" if not all_pass else "INFO",
            "detail": f"{conn_note}，parent={parent!r}",
            "sub_checks": [
                {"name": n, "passed": ok, "detail": d}
                for n, ok, d in sub_checks
            ],
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "format_id": format_id,
                "source_root": input_data.get("source_root", ""),
                "sig_diff_ok": input_data.get("sig_diff_ok", True),
                "extracted": input_data.get("extracted", {}),
                "check_parent_chain": check_result,
            },
            diagnosis=f"ParentChain: INPUT={len(input_usages)} OUTPUT={len(output_usages)} parent={parent!r}",
        )


# ════════════════════════════════════════════════════════════════
# CompositeFormatCheckRouter — Format.components 组合完整性检查
# ════════════════════════════════════════════════════════════════


class CompositeFormatCheckRouter(Router):
    """检查 composite Format（有 components 字段）的引用合法性：

      - 非 composite Format → 跳过（INFO PASS）
      - composite Format → 检查 description 是否说明了组合意图
        （含"由"/"组合"/"包含"/"汇聚"/"composed"/"contains"等关键词）
    """

    DESCRIPTION = (
        "检查 composite Format（有 components 字段）的 components 合法性和描述完整性；"
        "非 composite Format 跳过"
    )
    FORMAT_IN = "doctor.fmt.extracted"
    FORMAT_OUT = "doctor.fmt.check.composite"

    # 组合意图关键词（中英文）
    _INTENT_KEYWORDS = ("由", "组合", "包含", "汇聚", "composed", "contains", "combines", "aggregates")

    def run(self, input_data: Any) -> Verdict:
        fmt_id: str = input_data.get("format_id", "")
        extracted = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})
        components: list = format_obj.get("components", [])
        description: str = format_obj.get("description", "") or ""
        checks = list(input_data.get("checks", []))

        if not components:
            checks.append({
                "check": "composite_format",
                "severity": "INFO",
                "passed": True,
                "observation": "非 composite Format，跳过组合检查",
                "detail": None,
            })
        else:
            has_intent = any(kw in description for kw in self._INTENT_KEYWORDS)
            checks.append({
                "check": "composite_format",
                "severity": "MEDIUM",
                "passed": has_intent,
                "observation": (
                    f"composite Format，components={components}，description 说明了组合意图 ✓"
                    if has_intent else
                    f"composite Format，components={components}，但 description 未说明组合意图"
                    "（建议补充'由 X/Y/Z 组成'等描述）"
                ),
                "detail": {"components": components, "has_intent": has_intent},
            })

        check_result = checks[-1]  # the one we just appended
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "format_id": fmt_id,
                "source_root": input_data.get("source_root", ""),
                "sig_diff_ok": input_data.get("sig_diff_ok", True),
                "extracted": input_data.get("extracted", {}),
                "check_composite_format": check_result,
            },
            diagnosis=f"CompositeFormatCheck: {fmt_id} components={components}",
        )


# ════════════════════════════════════════════════════════════════
# ExamplePresenceCheckRouter — Format.examples 列表质量检查
# ════════════════════════════════════════════════════════════════


class ExamplePresenceCheckRouter(Router):
    """检查 Format.examples 列表质量：
      1. examples 列表非空
      2. 至少一个示例是含字段的非空 dict

    [PLANNED] 格式豁免（description 含 "[PLANNED" 或 tags 含 "planned"）。
    """

    DESCRIPTION = "检查 Format.examples 列表非空且含有意义的示例 dict（[PLANNED] 格式豁免）"
    FORMAT_IN = "doctor.fmt.extracted"
    FORMAT_OUT = "doctor.fmt.check.example-presence"

    def run(self, input_data: Any) -> Verdict:
        format_id: str = input_data["format_id"]
        extracted = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})
        examples: list = format_obj.get("examples") or []
        json_schema: dict = format_obj.get("json_schema") or {}
        desc: str = format_obj.get("description") or ""
        tags: list = format_obj.get("tags") or []

        # [PLANNED] 豁免
        is_planned = "[PLANNED" in desc.upper() or "planned" in [t.lower() for t in tags]
        if is_planned:
            check_result = {
                "check": "example_presence",
                "passed": True,
                "severity": "INFO",
                "detail": "[PLANNED] Format 豁免示例要求",
                "sub_checks": [],
            }
            return Verdict(
                kind=VerdictKind.PASS,
                confidence=1.0,
                output={
                    "format_id": format_id,
                    "source_root": input_data.get("source_root", ""),
                    "sig_diff_ok": input_data.get("sig_diff_ok", True),
                    "extracted": extracted,
                    "check_example_presence": check_result,
                },
                diagnosis=f"ExamplePresence: PLANNED exemption for {format_id}",
            )

        sub_checks = []
        has_schema = isinstance(json_schema, dict) and len(json_schema) > 0

        # 1. examples 非空 OR json_schema 非空（两者均可满足类型说明要求）
        has_examples = isinstance(examples, list) and len(examples) > 0
        has_type_info = has_examples or has_schema
        sub_checks.append(("examples 或 json_schema 非空", has_type_info,
                           f"共 {len(examples)} 个示例" if has_examples
                           else "json_schema 存在" if has_schema
                           else "examples 和 json_schema 均为空"))

        # 2. 若有 examples，至少一个示例是含字段的 dict
        if has_examples:
            has_meaningful = any(isinstance(e, dict) and len(e) >= 1 for e in examples)
            sub_checks.append(("至少一个示例含字段", has_meaningful,
                               "示例包含有意义的字段" if has_meaningful
                               else "所有示例均为空 dict {}"))

        passed_count = sum(1 for _, ok, _ in sub_checks if ok)
        all_pass = passed_count == len(sub_checks)
        max_fields = max((len(e) for e in examples if isinstance(e, dict)), default=0)

        detail_str = (
            f"示例存在（{len(examples)} 个，最大字段数={max_fields}）"
            if has_examples else
            "json_schema 定义存在（替代示例）"
            if has_schema else
            "示例质量不足（examples 和 json_schema 均为空）"
        )
        check_result = {
            "check": "example_presence",
            "passed": all_pass,
            "severity": "MEDIUM" if not all_pass else "INFO",
            "detail": detail_str,
            "sub_checks": [
                {"name": n, "passed": ok, "detail": d}
                for n, ok, d in sub_checks
            ],
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "format_id": format_id,
                "source_root": input_data.get("source_root", ""),
                "sig_diff_ok": input_data.get("sig_diff_ok", True),
                "extracted": extracted,
                "check_example_presence": check_result,
            },
            diagnosis=f"ExamplePresence: {'OK' if all_pass else 'FAIL'} for {format_id}",
        )


# ════════════════════════════════════════════════════════════════
# FormatContextualAuditRouter — LLM 语义审计（含上下游 Router 源码）
# ════════════════════════════════════════════════════════════════


class FormatContextualAuditRouter(Router):
    """LLM 驱动的全语境 Format 语义审计。

    向 LLM 提供：
    ① Format 完整定义（id/description/schema/examples/tags/parent）
    ② 上游 Router 源码（FORMAT_OUT == format_id 的 Router 类）
    ③ 下游 Router 源码（FORMAT_IN  == format_id 的 Router 类）
    ④ docs/standards/material.md 全文（F-01~F-13 + 4 原则 + FA 反模式）

    审计维度：
    - F-01 五要素（字段语义/枚举/上游承诺/下游用途/最小样例）
    - F-06 schema ↔ description 一致性
    - F-08 semantic_preconditions ↔ required_tags 对称性
    - FA-01/04/05/06/07 反模式检测
    - 上游产出匹配度（Format 是否精确描述上游 Router 的实际产出）
    - 下游期望匹配度（Format 是否精确描述下游 Router 的实际期望）

    LLM 产出详细定性报告，存档到：
      <project_root>/data/doctor/audit/<format_id_safe>/<git_short_hash>.md
    LLM 失败时降级为 SKIP（不阻断管线）。
    """

    DESCRIPTION = "LLM 语义审计：format + 上下游 Router 源码 + 完整标准 → 定性报告 + git 存档"
    FORMAT_IN = "doctor.fmt.extracted"
    FORMAT_OUT = "doctor.fmt.check.llm-audit"

    _MODEL = "qwen3.6-plus"
    _MAX_SRC = 5000   # 单个 Router 源码最大字符数（避免 prompt 过长）

    _SYSTEM = """\
你是 OmniCompany 的 Format 语义审计员。你将获得：
1. 一个 Format 的完整定义
2. 产出该 Format 的上游 Router 源码（可能为空，说明未找到）
3. 消费该 Format 的下游 Router 源码（可能为空，说明未找到）
4. 完整的 Format 健康标准文档

你的任务：对该 Format 的语义质量进行深度定性评估，产出结构化 JSON + 完整 Markdown 报告。

## 重要原则

- 评估基于**语义理解**，不是关键词检测
- 描述中包含某个词不等于满足对应标准；描述需要提供实质信息
- 上游 Router 的 run() 方法中产出的字段 = 该 Format 应描述的内容
- 下游 Router 的 run() 方法中访问的字段 = 该 Format 的消费者期望

## 输出格式

严格输出合法 JSON（不要 markdown 代码块），字段如下：

{
  "f01_field_semantics": true/false,
  "f01_enum_invariants": true/false,
  "f01_upstream_promises": true/false,
  "f01_downstream_usage": true/false,
  "f01_minimal_example": true/false,
  "f06_schema_coherent": true/false,
  "f08_preconditions_symmetric": true/false,
  "fa01_hollow": true/false,
  "fa04_semantic_overload": true/false,
  "fa05_clone_rename": true/false,
  "fa06_semantic_break": true/false,
  "fa07_heterogeneous_mix": true/false,
  "upstream_match": true/false,
  "upstream_match_notes": "上游产出与 Format 描述的差异（若无上游 Router 源码则注明）",
  "downstream_match": true/false,
  "downstream_match_notes": "下游期望与 Format 描述的差异（若无下游 Router 源码则注明）",
  "overall_grade": "A/B/C/D",
  "key_findings": "最关键的 1-3 条发现（具体，有针对性）",
  "improvement_suggestions": "具体改进建议",
  "detailed_report": "完整 Markdown 审计报告，包含各维度分析、上下游匹配分析、综合评级"
}

评级标准（overall_grade）：
- A: F-01 五要素全部满足 + 无高危反模式 + 上下游匹配良好
- B: F-01 部分满足（≥3/5）+ 上下游基本匹配
- C: F-01 不足一半 OR 存在重要不匹配
- D: 描述空洞无信息增益（FA-01）OR 严重误导消费者
"""

    def __init__(self, model: str | None = None):
        self._model = model or self._MODEL

    def run(self, input_data: Any) -> Verdict:
        format_id: str = input_data["format_id"]
        extracted: dict = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})
        usages: list = extracted.get("usages", [])
        source_root = Path(input_data.get("source_root", _DEFAULT_SOURCE_ROOT))

        # 上下游 Router 源码
        upstream_entries = self._load_router_sources(source_root, usages, "OUTPUT")
        downstream_entries = self._load_router_sources(source_root, usages, "INPUT")

        # 标准文档
        standards = self._load_standards(source_root)

        # LLM 审计
        audit_data, raw_report = self._audit(
            format_id, format_obj,
            upstream_entries, downstream_entries, standards,
        )

        # 存档
        audit_path = self._archive_report(format_id, source_root, audit_data, raw_report)

        # 构造 check 结果
        f01_pass = all(audit_data.get(k, False) for k in (
            "f01_field_semantics", "f01_enum_invariants",
            "f01_upstream_promises", "f01_downstream_usage", "f01_minimal_example",
        ))
        has_antipattern = any(audit_data.get(k, False) for k in (
            "fa01_hollow", "fa04_semantic_overload",
            "fa05_clone_rename", "fa06_semantic_break", "fa07_heterogeneous_mix",
        ))
        grade = audit_data.get("overall_grade", "?")
        check_result = {
            "check": "contextual_audit",
            "passed": grade in ("A", "B"),
            "severity": "MEDIUM" if grade in ("C", "D") else "INFO",
            "detail": audit_data.get("key_findings", "LLM 审计跳过"),
            "grade": grade,
            "audit_path": str(audit_path) if audit_path else None,
            "sub_checks": [
                {"name": "f01_five_elements",       "passed": f01_pass},
                {"name": "f06_schema_coherent",      "passed": audit_data.get("f06_schema_coherent", True)},
                {"name": "f08_preconditions",        "passed": audit_data.get("f08_preconditions_symmetric", True)},
                {"name": "no_antipatterns",          "passed": not has_antipattern},
                {"name": "upstream_match",           "passed": audit_data.get("upstream_match", True)},
                {"name": "downstream_match",         "passed": audit_data.get("downstream_match", True)},
            ],
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "format_id": format_id,
                "source_root": input_data.get("source_root", ""),
                "sig_diff_ok": input_data.get("sig_diff_ok", True),
                "extracted": extracted,
                "check_llm_audit": check_result,
            },
            diagnosis=f"ContextualAudit: {format_id} grade={grade}",
        )

    # ── 内部工具 ──────────────────────────────────────────────────

    def _load_standards(self, source_root: Path) -> str:
        """加载 docs/standards/material.md。"""
        project_root = source_root.resolve().parents[1]
        standards_path = project_root / "docs" / "standards" / "material.md"
        try:
            return standards_path.read_text(encoding="utf-8")
        except Exception:
            return "(standards/material.md 未找到)"

    def _load_router_sources(
        self,
        source_root: Path,
        usages: list[dict],
        role: str,  # "INPUT" or "OUTPUT"
    ) -> list[dict]:
        """从 usages 列表中找到 INPUT/OUTPUT 角色的文件，加载对应 Router 类源码。"""
        entries: list[dict] = []
        seen_files: set[str] = set()
        for usage in usages:
            if role not in usage.get("role", ""):
                continue
            file_rel: str = usage["file"]
            if file_rel in seen_files:
                continue
            seen_files.add(file_rel)
            try:
                # file_rel 形如 "src/omnifactory/packages/services/doctor/routers.py"
                # source_root 是 "e:/WindowsWorkspace/omnifactory/src/omnifactory"
                # file_rel 由 FormatExtractorRouter 生成：相对于 source_root.parent（即 src/）
                # 例: "omnifactory/packages/services/.../routers.py"
                full_path = source_root.resolve().parent / file_rel
                content = full_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # 找到该行对应的 Router 类
            class_name = self._class_owning_line(content, usage.get("line", ""))
            src_excerpt = (
                self._extract_class_source(content, class_name)
                if class_name
                else content[:self._MAX_SRC]
            )
            if len(src_excerpt) > self._MAX_SRC:
                src_excerpt = src_excerpt[:self._MAX_SRC] + "\n... [truncated]"
            entries.append({
                "file": file_rel,
                "class": class_name or "(unknown)",
                "source": src_excerpt,
            })
        return entries

    def _class_owning_line(self, content: str, target_line_stripped: str) -> str | None:
        """给定一行内容（stripped），找到该行所在的类名。"""
        lines = content.splitlines()
        class_pat = re.compile(r"^class\s+(\w+)")
        current_class: str | None = None
        for line in lines:
            m = class_pat.match(line)
            if m:
                current_class = m.group(1)
            if target_line_stripped and target_line_stripped[:60] in line:
                return current_class
        return current_class

    def _extract_class_source(self, content: str, class_name: str) -> str:
        """提取指定类的全部源码（从 class 行到下一个顶层 class/def）。"""
        lines = content.splitlines()
        pat = re.compile(r"^class\s+" + re.escape(class_name) + r"\b")
        start: int | None = None
        for i, line in enumerate(lines):
            if pat.match(line):
                start = i
                break
        if start is None:
            return content[:self._MAX_SRC]
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if re.match(r"^class\s", lines[i]) or re.match(r"^def\s", lines[i]):
                end = i
                break
        return "\n".join(lines[start:end])

    def _build_user_msg(
        self,
        format_id: str,
        format_obj: dict,
        upstreams: list[dict],
        downstreams: list[dict],
        standards: str,
    ) -> str:
        """构造 LLM 用户消息。"""
        parts: list[str] = []

        parts.append("# Format 定义")
        parts.append(f"**ID**: {format_id}")
        parts.append(f"**description**: {format_obj.get('description') or '(空)'}")
        if format_obj.get("json_schema"):
            parts.append(f"**json_schema**: {json.dumps(format_obj['json_schema'], ensure_ascii=False)[:800]}")
        if format_obj.get("examples"):
            parts.append(f"**examples**: {json.dumps(format_obj['examples'], ensure_ascii=False)[:400]}")
        parts.append(f"**tags**: {format_obj.get('tags', [])}")
        parts.append(f"**parent**: {format_obj.get('parent') or '(无)'}")

        if upstreams:
            for u in upstreams:
                parts.append("\n# 上游 Router（产出此 Format）")
                parts.append(f"**文件**: {u['file']}  **类**: {u['class']}")
                parts.append(f"```python\n{u['source']}\n```")
        else:
            parts.append("\n# 上游 Router\n(未找到 FORMAT_OUT 引用，可能是管线起点或名称不一致)")

        if downstreams:
            for d in downstreams:
                parts.append("\n# 下游 Router（消费此 Format）")
                parts.append(f"**文件**: {d['file']}  **类**: {d['class']}")
                parts.append(f"```python\n{d['source']}\n```")
        else:
            parts.append("\n# 下游 Router\n(未找到 FORMAT_IN 引用，可能是管线终点或名称不一致)")

        parts.append(f"\n# Format 健康标准文档\n{standards}")
        parts.append("\n请对以上 Format 进行语义审计，输出 JSON 报告。")

        return "\n\n".join(parts)

    def _audit(
        self,
        format_id: str,
        format_obj: dict,
        upstreams: list[dict],
        downstreams: list[dict],
        standards: str,
    ) -> tuple[dict, str]:
        """调用 LLM；返回 (audit_data_dict, raw_llm_text)。失败时返回空 dict。"""
        if not format_obj.get("description"):
            return {}, "(description 缺失，跳过 LLM 审计)"

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            user_msg = self._build_user_msg(format_id, format_obj, upstreams, downstreams, standards)
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=self._SYSTEM,
            )
            raw = resp.content[0].text.strip()
            # 去除可能的 markdown 代码块包装
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw.strip())
            data = json.loads(raw)
            return data, raw
        except Exception as e:
            logger.warning("FormatContextualAudit LLM call failed for %s: %s", format_id, e)
            return {}, f"(LLM 调用失败: {type(e).__name__}: {e})"

    def _get_git_hash(self, source_root: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True,
                cwd=str(source_root.resolve().parents[1]),
                timeout=5,
            )
            return result.stdout.strip() or "unknown"
        except Exception:
            return "unknown"

    def _archive_report(
        self,
        format_id: str,
        source_root: Path,
        audit_data: dict,
        raw_report: str,
    ) -> Path | None:
        """将 Markdown 报告存档到 data/doctor/audit/{safe_id}/{git_hash}.md。"""
        if not audit_data:
            return None
        try:
            git_hash = self._get_git_hash(source_root)
            safe_id = format_id.replace(".", "_")
            audit_dir = source_root.resolve().parents[1] / "data" / "doctor" / "audit" / safe_id
            audit_dir.mkdir(parents=True, exist_ok=True)

            # 构造 Markdown 报告
            detailed = audit_data.get("detailed_report", raw_report)
            report_lines = [
                f"# Format 审计报告: {format_id}",
                "",
                f"**Commit**: `{git_hash}`  **Grade**: {audit_data.get('overall_grade', '?')}",
                "",
                detailed,
            ]
            report_path = audit_dir / f"{git_hash}.md"
            report_path.write_text("\n".join(report_lines), encoding="utf-8")
            return report_path
        except Exception as e:
            logger.warning("Failed to archive audit report for %s: %s", format_id, e)
            return None


# 向后兼容别名（run.py 引用了旧名称时不会立刻崩溃）
DescriptionEvaluatorRouter = FormatContextualAuditRouter


# ════════════════════════════════════════════════════════════════
# HealthWriterRouter — 汇总所有检查，生成健康档案
# ════════════════════════════════════════════════════════════════


class HealthWriterRouter(Router):
    """汇总所有检查结果，计算健康评分，输出最终健康档案。

    健康档案包含：
    - format_id / source_root
    - checks: 所有检查结果列表
    - health_score: 0.0~1.0（通过检查数 / 总检查数，跳过的不计）
    - health_grade: A/B/C/D/F
    - critical_failures: CRITICAL 级别未通过的检查列表
    - summary: 一句话摘要
    """

    DESCRIPTION = "汇总检查结果，计算健康评分和等级，生成最终健康档案"
    FORMAT_IN = "doctor.fmt.checks"
    FORMAT_OUT = "doctor.fmt.health-record"

    # fan-in 时各 checker 将结果存入这些 key
    _KNOWN_CHECK_KEYS = [
        "check_five_element",
        "check_tag_coverage",
        "check_parent_chain",
        "check_composite_format",
        "check_example_presence",
        "check_llm_audit",
    ]

    def run(self, input_data: Any) -> Verdict:
        format_id: str = input_data["format_id"]
        extracted: dict = input_data.get("extracted", {})
        checks = [input_data[k] for k in self._KNOWN_CHECK_KEYS if k in input_data]
        format_obj: dict = extracted.get("format_obj", {})

        # 计算评分（跳过 passed=None 的检查）
        counted = [c for c in checks if c.get("passed") is not None]
        passed_count = sum(1 for c in counted if c.get("passed"))
        total_count = len(counted)
        score = (passed_count / total_count) if total_count > 0 else 0.0

        # 评级
        if score >= 0.95:
            grade = "A"
        elif score >= 0.80:
            grade = "B"
        elif score >= 0.60:
            grade = "C"
        elif score >= 0.40:
            grade = "D"
        else:
            grade = "F"

        # CRITICAL 失败
        critical_failures = [
            c["check"] for c in checks
            if not c.get("passed") and c.get("severity") == "CRITICAL"
        ]

        # 摘要
        if not input_data.get("sig_diff_ok", True):
            summary = f"Format '{format_id}' 未找到 Format 对象定义，无法完整诊断"
        elif grade in ("A", "B"):
            summary = f"Format '{format_id}' 健康状况良好（{grade} 级，{score:.0%}）"
        else:
            issues = [c["check"] for c in checks if not c.get("passed")]
            summary = (
                f"Format '{format_id}' 存在问题（{grade} 级，{score:.0%}）："
                + "、".join(issues[:3])
            )

        # 被评对象的 Format 定义字段（与健康档案一起输出，便于阅读时对照原始定义）
        format_def = {
            k: format_obj[k]
            for k in ("id", "name", "description", "parent", "tags", "examples", "json_schema")
            if format_obj.get(k) is not None
        }

        health_record = {
            "format_id": format_id,
            "source_root": input_data.get("source_root", ""),
            "format_def": format_def,
            "checks": checks,
            "health_score": round(score, 3),
            "health_grade": grade,
            "critical_failures": critical_failures,
            "summary": summary,
            "sig_diff_ok": input_data.get("sig_diff_ok", True),
            "extracted": extracted,  # 保留给下游（repair 链定位源文件用），完整输出，不截断
        }

        self._save_format_health(format_id, health_record, extracted, input_data)

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=health_record,
            diagnosis=f"HealthWriter: {format_id} grade={grade} score={score:.2f}",
        )

    def _save_format_health(
        self, format_id: str, health_record: dict, extracted: dict, input_data: dict
    ) -> None:
        """中央 + 就近双写格式健康档案（静默失败）。"""
        if not _ARCHIVE_AVAILABLE:
            return
        try:
            source_root = input_data.get("source_root", "")
            defined_in = extracted.get("defined_in", "")
            fmt_source_file = (
                str(Path(source_root).parent / defined_in)
                if (source_root and defined_in) else source_root
            )
            _archive = _HealthArchive(_REGISTRY_ARCHIVE_DIR)
            _snapshot = _make_format_snapshot(f"format:{format_id}", health_record, fmt_source_file, _archive)
            _write_proximity(fmt_source_file, "formats", format_id, _snapshot)
        except Exception as _e:
            logger.debug("HealthArchive write skipped for %s: %s", format_id, _e)


# ════════════════════════════════════════════════════════════════
# Router 诊断管线
# ════════════════════════════════════════════════════════════════
#
#   RouterExtractorRouter          (RULE) AST 提取 Router 类结构 + 7 类衍生信号
#   RouterSignatureRouter          (HARD, Anchor) 存在性校验；失败则短路
#   RouterContextCollectorRouter   (RULE) 跨 source_root 收集 Format 定义 + 邻居 + Pipeline
#   RouterDeterministicCheckRouter (RULE) 11 项确定性检查
#   RouterHealthWriterRouter       (RULE) 汇总评分，生成健康档案
#   RouterContextualAuditRouter    (LLM) 全语境语义审计（层 A/B/C/D）
#

# ── AST 工具函数（Router 提取专用）──────────────────────────────

# 已知模型名称模式（R-11 检测硬编码模型名）
_KNOWN_MODEL_PATTERNS = (
    "gpt-4", "gpt-3.5", "gpt4", "gpt3",
    "claude-3", "claude-2", "claude-1",
    "qwen", "deepseek", "gemini", "mistral", "llama",
    "text-davinci", "o1-preview", "o3-",
)

# LLM 调用的方法名模式（检测 LLMClient 使用）
_LLM_CALL_METHODS = ("client.call", "llm.call", "self.client.call", "self.llm.call", "LLMClient(")

# 直接 LLM import（R-04 检测）
_DIRECT_LLM_IMPORTS = (
    "import openai", "import anthropic",
    "from openai import", "from anthropic import",
)

# 文件写操作（R-06 检测）
_FILE_WRITE_PATTERNS = (
    "open(", ".write_text(", ".write_bytes(",
    "shutil.copy(", "shutil.move(",
)

# LLM 协议泄漏模式（R-12 检测）
_PROTOCOL_LEAK_PATTERNS = (
    'block.type == "tool_use"', "block.type == 'tool_use'",
    "response.choices[", ".choices[0].message",
    ".message.tool_calls",
)


def _get_source_lines(source_root: Path) -> list[str]:
    """获取 source 文件内容行（用于行号上下文提取）。"""
    return []


def _classify_self_assignment(var_name: str, context: str) -> str:
    """将 self.xxx = ... 分类为 INFO / SUSPICIOUS / LIKELY_VIOLATION。"""
    # 合法基础设施：日志、工具属性、LLMRouter 基类模式
    info_patterns = (
        "_logger", "_log", "logger", "log",
        "last_token_count", "last_tokens", "_model", "_client",
        "_source_root", "_default", "_config",
    )
    violation_patterns = (
        "cache", "history", "counter", "count",
        "last_result", "last_output", "session",
        "state", "pending", "buffer",
    )
    lower = var_name.lower()
    for p in info_patterns:
        if p in lower:
            return "INFO"
    for p in violation_patterns:
        if p in lower:
            return "LIKELY_VIOLATION"
    return "SUSPICIOUS"


def _extract_router_ast(
    class_node: ast.ClassDef,
    source_lines: list[str],
) -> dict:
    """从 ClassDef AST 节点提取 Router 类的完整信号。"""
    # ── 类变量字面量 ──
    class_vars: dict = {}
    class_var_kinds: dict[str, str] = {}  # FORMAT_IN/OUT 的表达式类型

    for stmt in class_node.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if not (isinstance(t, ast.Name) and t.id in (
                    "DESCRIPTION", "FORMAT_IN", "FORMAT_OUT",
                    "INPUT_KEYS", "OUTPUT_KEYS", "PASSTHROUGH",
                )):
                    continue
                if t.id in ("FORMAT_IN", "FORMAT_OUT"):
                    if isinstance(stmt.value, ast.JoinedStr):
                        class_vars[t.id] = None
                        class_var_kinds[t.id] = "fstring"
                    elif isinstance(stmt.value, ast.List):
                        try:
                            class_vars[t.id] = ast.literal_eval(stmt.value)
                        except Exception:
                            class_vars[t.id] = None
                        class_var_kinds[t.id] = "list"
                    else:
                        try:
                            class_vars[t.id] = ast.literal_eval(stmt.value)
                            class_var_kinds[t.id] = "literal"
                        except Exception:
                            class_vars[t.id] = None
                            class_var_kinds[t.id] = "dynamic"
                else:
                    try:
                        class_vars[t.id] = ast.literal_eval(stmt.value)
                    except Exception:
                        class_vars[t.id] = None

    # ── __init__ 参数 ──
    init_params: list[str] = []
    run_source: str = ""
    run_line_count: int = 0
    run_start_line: int = 0
    run_is_async: bool = False

    _FN_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

    for stmt in class_node.body:
        if not isinstance(stmt, _FN_TYPES):
            continue
        if stmt.name == "__init__":
            init_params = [
                a.arg for a in stmt.args.args if a.arg != "self"
            ]
        elif stmt.name == "run":
            run_is_async = isinstance(stmt, ast.AsyncFunctionDef)
            run_start_line = stmt.lineno
            if source_lines:
                # 提取 run() 的完整源码文本
                end_line = stmt.end_lineno if hasattr(stmt, "end_lineno") else stmt.lineno
                run_source = "\n".join(source_lines[stmt.lineno - 1 : end_line])
                run_line_count = end_line - stmt.lineno + 1
            else:
                run_line_count = _count_run_lines(stmt)

    # ── 7 类 AST 衍生信号 ──
    llm_calls: list[dict] = []
    self_assignments: list[dict] = []
    input_keys_accessed: list[str] = []
    output_keys_produced: list[str] = []
    verdict_patterns: list[dict] = []
    exception_patterns: list[dict] = []

    # 注意：llm_calls 扫描类内所有方法（不限 run()），
    # 因为 LLM 调用常被封装在 _audit() / _call() 等辅助方法中。
    # 其余信号（self_assignments / input_keys / verdict_patterns / exceptions）
    # 仍只扫描 run()，因为它们对应 Router 契约的直接实现。
    for stmt in class_node.body:
        if not isinstance(stmt, _FN_TYPES):
            continue
        is_run = stmt.name == "run"

        for node in ast.walk(stmt):
            # llm_calls: 扫描类内所有方法，捕捉封装在辅助方法里的 LLM 调用
            if isinstance(node, ast.Call):
                func_repr = _get_call_repr(node.func)
                if any(pat in func_repr for pat in ("client.call", "llm.call", "LLMClient")):
                    ctx = _get_line_context(source_lines, getattr(node, "lineno", 0), 2)
                    llm_calls.append({
                        "line": getattr(node, "lineno", 0),
                        "context": ctx,
                        "method": stmt.name,
                    })

            # 以下信号只在 run() 方法中扫描（契约实现的直接体现）
            if not is_run:
                continue

            # self_assignments: run() 内 self.xxx = ...
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                        var_name = t.attr
                        ctx = _get_line_context(source_lines, getattr(node, "lineno", 0), 1)
                        classification = _classify_self_assignment(var_name, ctx)
                        self_assignments.append({
                            "var": var_name,
                            "line": getattr(node, "lineno", 0),
                            "classification": classification,
                            "context": ctx,
                        })

            # input_keys_accessed: input_data["key"] / input_data.get("key")
            if isinstance(node, ast.Subscript):
                if isinstance(node.value, ast.Name) and node.value.id == "input_data":
                    try:
                        key = ast.literal_eval(node.slice)
                        if isinstance(key, str) and key not in input_keys_accessed:
                            input_keys_accessed.append(key)
                    except Exception:
                        pass
            if isinstance(node, ast.Call):
                func_repr = _get_call_repr(node.func)
                if func_repr in ("input_data.get",) and node.args:
                    try:
                        key = ast.literal_eval(node.args[0])
                        if isinstance(key, str) and key not in input_keys_accessed:
                            input_keys_accessed.append(key)
                    except Exception:
                        pass

            # output_keys_produced: Verdict(output={...}) 的顶层 key
            if isinstance(node, ast.Call):
                func_repr = _get_call_repr(node.func)
                if func_repr == "Verdict":
                    for kw in node.keywords:
                        if kw.arg == "output" and isinstance(kw.value, ast.Dict):
                            for k in kw.value.keys:
                                try:
                                    key = ast.literal_eval(k)
                                    if isinstance(key, str) and key not in output_keys_produced:
                                        output_keys_produced.append(key)
                                except Exception:
                                    pass

            # verdict_patterns: return Verdict(...) 的 kind/confidence/diagnosis/granted_tags
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
                func_repr = _get_call_repr(node.value.func)
                if func_repr == "Verdict":
                    vp = _extract_verdict_pattern(node.value)
                    verdict_patterns.append(vp)

            # verdict_kind_variable_assigns: 追踪 kind = VerdictKind.PASS [if ... else VerdictKind.FAIL]
            # 处理 `return Verdict(kind=kind, ...)` 中 kind 是变量的情况
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        inferred = _extract_vk_from_expr(node.value)
                        for kind_val in inferred:
                            verdict_patterns.append({
                                "kind": kind_val,
                                "confidence": None,
                                "diagnosis": None,
                                "granted_tags": [],
                                "_inferred_from_variable": True,
                            })

        # exception_patterns: 只扫 run() 方法的 except 块
        if is_run:
            for node in ast.walk(stmt):
                if isinstance(node, ast.ExceptHandler):
                    exc_type = "Exception"
                    if node.type is not None:
                        if isinstance(node.type, ast.Name):
                            exc_type = node.type.id
                        elif isinstance(node.type, ast.Attribute):
                            exc_type = f"{ast.dump(node.type)}"
                    handling = _classify_except_handling(node)
                    ctx = _get_line_context(source_lines, getattr(node, "lineno", 0), 2)
                    exception_patterns.append({
                        "exception_type": exc_type,
                        "handling": handling,
                        "context": ctx,
                    })

    router_kind = "LLM" if llm_calls else "RULE"

    return {
        "description": class_vars.get("DESCRIPTION"),
        "format_in": class_vars.get("FORMAT_IN"),
        "format_out": class_vars.get("FORMAT_OUT"),
        "format_in_kind": class_var_kinds.get("FORMAT_IN", "literal"),
        "format_out_kind": class_var_kinds.get("FORMAT_OUT", "literal"),
        "input_keys": class_vars.get("INPUT_KEYS"),
        "output_keys": class_vars.get("OUTPUT_KEYS"),
        "passthrough": class_vars.get("PASSTHROUGH"),
        "init_params": init_params,
        "run_is_async": run_is_async,
        "run_source": run_source,
        "run_line_count": run_line_count,
        "ast_signals": {
            "router_kind": router_kind,
            "llm_calls": llm_calls,
            "self_assignments": self_assignments,
            "input_keys_accessed": input_keys_accessed,
            "output_keys_produced": output_keys_produced,
            "verdict_patterns": verdict_patterns,
            "exception_patterns": exception_patterns,
        },
    }


def _count_run_lines(func_node: ast.FunctionDef) -> int:
    """估算 run() 行数（无 source_lines 时）。"""
    if hasattr(func_node, "end_lineno"):
        return func_node.end_lineno - func_node.lineno + 1
    return 0


def _get_call_repr(node: ast.expr) -> str:
    """将 Call.func AST 节点转换为可读字符串，如 'self.client.call'。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_get_call_repr(node.value)}.{node.attr}"
    return ""


def _get_line_context(source_lines: list[str], lineno: int, radius: int) -> str:
    """提取 lineno 行前后 radius 行的上下文（1-indexed）。"""
    if not source_lines or lineno <= 0:
        return ""
    start = max(0, lineno - 1 - radius)
    end = min(len(source_lines), lineno + radius)
    return "\n".join(source_lines[start:end])


def _extract_vk_from_expr(node: ast.expr) -> list[str]:
    """从表达式中递归提取所有 VerdictKind.XXX 的 XXX 值（支持三元/if-else）。

    用于追踪 `kind = VerdictKind.PASS if ... else VerdictKind.FAIL` 这类变量赋值，
    将其产生的 kind 值注入 verdict_patterns，补全 R-05 检查对变量间接传递的识别。
    """
    kinds = []
    if isinstance(node, ast.Attribute):
        # VerdictKind.PASS → "PASS"
        if isinstance(node.value, ast.Name) and node.value.id in ("VerdictKind",):
            kinds.append(node.attr)
    elif isinstance(node, ast.IfExp):
        # ternary: body if test else orelse
        kinds.extend(_extract_vk_from_expr(node.body))
        kinds.extend(_extract_vk_from_expr(node.orelse))
    elif isinstance(node, ast.BoolOp):
        for v in node.values:
            kinds.extend(_extract_vk_from_expr(v))
    return kinds


def _extract_verdict_pattern(call_node: ast.Call) -> dict:
    """从 Verdict(...) 调用提取 kind/confidence/diagnosis/granted_tags。"""
    result = {"kind": None, "confidence": None, "diagnosis": None, "granted_tags": []}
    for kw in call_node.keywords:
        if kw.arg == "kind":
            if isinstance(kw.value, ast.Attribute):
                result["kind"] = kw.value.attr
            elif isinstance(kw.value, ast.Name):
                result["kind"] = kw.value.id
        elif kw.arg == "confidence":
            try:
                result["confidence"] = ast.literal_eval(kw.value)
            except Exception:
                pass
        elif kw.arg == "diagnosis":
            try:
                result["diagnosis"] = ast.literal_eval(kw.value)
            except Exception:
                result["diagnosis"] = "(f-string or expr)"
        elif kw.arg == "granted_tags":
            try:
                result["granted_tags"] = ast.literal_eval(kw.value)
            except Exception:
                result["granted_tags"] = []
    return result


def _classify_except_handling(handler: ast.ExceptHandler) -> str:
    """分类 except 块的处理方式。"""
    for node in ast.walk(handler):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            repr_ = _get_call_repr(node.value.func)
            if repr_ == "Verdict":
                for kw in node.value.keywords:
                    if kw.arg == "kind":
                        if isinstance(kw.value, ast.Attribute) and kw.value.attr == "PASS":
                            return "return_pass"
                        if isinstance(kw.value, ast.Attribute) and kw.value.attr == "FAIL":
                            return "return_fail"
        if isinstance(node, ast.Raise):
            return "raise"
    # 检查是否只有日志操作
    has_log = any(
        isinstance(n, ast.Call) and "log" in _get_call_repr(n.func).lower()
        for n in ast.walk(handler)
        if isinstance(n, ast.Call)
    )
    if has_log:
        return "log_only"
    if not list(handler.body):
        return "ignore"
    return "log_only"


def _is_router_class(class_node: ast.ClassDef, source_text: str) -> bool:
    """判断是否是 Router 子类（Router/LLMRouter/AgentNodeLoop）。"""
    router_bases = {"Router", "LLMRouter", "AgentNodeLoop"}
    for base in class_node.bases:
        name = _get_call_repr(base)
        if name in router_bases:
            return True
    # 也检查文件中是否有 Router 基类的字符串特征
    return (
        "DESCRIPTION" in source_text
        and "FORMAT_IN" in source_text
        and "FORMAT_OUT" in source_text
        and "def run" in source_text
    )


# ════════════════════════════════════════════════════════════════
# RouterExtractorRouter — 纯 AST 提取 Router 类结构
# ════════════════════════════════════════════════════════════════


class RouterExtractorRouter(Router):
    """打开 source_file（或目录），AST 解析，提取目标 router_class 的
    全部结构信息和 7 类衍生信号。

    PASS: 文件存在且 AST 可解析（found=true 或 found=false 均返回 PASS）
    FAIL: 文件/目录不存在，或 AST 解析遇到语法错误
    """

    DESCRIPTION = "AST 提取目标 Router 类的结构（DESCRIPTION/FORMAT_IN/OUT/run源码/行数）和 7 类衍生信号（llm_calls/self_assignments/verdict_patterns 等）"
    FORMAT_IN = "diag.rtr.request"
    FORMAT_OUT = "diag.rtr.extracted"
    INPUT_KEYS = ["router_class", "source_file", "source_root"]

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data["router_class"]
        source_file = Path(input_data["source_file"])
        source_root = Path(input_data.get("source_root", _DEFAULT_SOURCE_ROOT))

        # 确定要扫描的文件列表
        if not source_file.exists():
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={
                    "router_class": router_class,
                    "source_file": str(source_file),
                    "source_root": str(source_root),
                    "found": False,
                    "description": None,
                    "format_in": None,
                    "format_out": None,
                    "input_keys": None,
                    "output_keys": None,
                    "passthrough": None,
                    "init_params": [],
                    "run_source": "",
                    "run_line_count": 0,
                    "ast_signals": {
                        "router_kind": "RULE",
                        "llm_calls": [],
                        "self_assignments": [],
                        "input_keys_accessed": [],
                        "output_keys_produced": [],
                        "verdict_patterns": [],
                        "exception_patterns": [],
                    },
                },
                diagnosis=f"RouterExtractor FAIL: {source_file} 不存在",
            )

        py_files: list[Path] = []
        if source_file.is_dir():
            py_files = [f for f in source_file.rglob("*.py") if "__pycache__" not in str(f)]
        else:
            py_files = [source_file]

        extracted_data: dict | None = None
        found = False

        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.warning("RouterExtractor: cannot read %s: %s", py_file, e)
                continue

            if router_class not in content:
                continue

            try:
                tree = ast.parse(content, filename=str(py_file))
            except SyntaxError as e:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    confidence=1.0,
                    output=self._empty_output(router_class, source_file, source_root),
                    diagnosis=f"RouterExtractor FAIL: {py_file} AST 解析失败: {e}",
                )

            source_lines = content.splitlines()

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if node.name != router_class:
                    continue

                data = _extract_router_ast(node, source_lines)
                found = True
                extracted_data = data
                break

            if found:
                break

        base = {
            "router_class": router_class,
            "source_file": str(source_file),
            "source_root": str(source_root),
            "found": found,
        }

        if found and extracted_data:
            base.update(extracted_data)
        else:
            base.update({
                "description": None,
                "format_in": None,
                "format_out": None,
                "input_keys": None,
                "output_keys": None,
                "passthrough": None,
                "init_params": [],
                "run_source": "",
                "run_line_count": 0,
                "ast_signals": {
                    "router_kind": "RULE",
                    "llm_calls": [],
                    "self_assignments": [],
                    "input_keys_accessed": [],
                    "output_keys_produced": [],
                    "verdict_patterns": [],
                    "exception_patterns": [],
                },
            })

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=base,
            diagnosis=f"RouterExtractor: {router_class} found={found}",
        )

    def _empty_output(self, router_class: str, source_file: Path, source_root: Path) -> dict:
        return {
            "router_class": router_class,
            "source_file": str(source_file),
            "source_root": str(source_root),
            "found": False,
            "description": None,
            "format_in": None,
            "format_out": None,
            "input_keys": None,
            "output_keys": None,
            "passthrough": None,
            "init_params": [],
            "run_source": "",
            "run_line_count": 0,
            "ast_signals": {
                "router_kind": "RULE",
                "llm_calls": [],
                "self_assignments": [],
                "input_keys_accessed": [],
                "output_keys_produced": [],
                "verdict_patterns": [],
                "exception_patterns": [],
            },
        }


# ════════════════════════════════════════════════════════════════
# RouterSignatureRouter — 存在性校验（Anchor，可短路）
# ════════════════════════════════════════════════════════════════


class RouterSignatureRouter(Router):
    """校验 Router 类是否存在且有基础元数据（DESCRIPTION / FORMAT_IN / FORMAT_OUT）。

    PASS: 全部存在 → 创建 diag.rtr.acc 累加器，进入完整诊断链
    FAIL: 任一缺失 → EMIT 最小健康档案（短路，跳过后续节点）
    """

    DESCRIPTION = "校验 Router 类存在且有 DESCRIPTION/FORMAT_IN/FORMAT_OUT；任一缺失则 EMIT 最小健康档案"
    FORMAT_IN = "diag.rtr.extracted"
    FORMAT_OUT = "diag.rtr.sig-checked"
    INPUT_KEYS = ["router_class", "found", "description", "format_in", "format_out"]

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data["router_class"]
        found: bool = input_data.get("found", False)
        description: str | None = input_data.get("description")
        format_in: str | None = input_data.get("format_in")
        format_out: str | None = input_data.get("format_out")
        format_in_kind: str = input_data.get("format_in_kind", "literal")
        format_out_kind: str = input_data.get("format_out_kind", "literal")

        # 区分"真正缺失"和"f-string 无法静态解析"
        truly_missing: list[str] = []
        fstring_fields: list[str] = []

        if not found:
            truly_missing.append("class_not_found")
        if not description:
            truly_missing.append("DESCRIPTION_empty")
        if not format_in:
            if format_in_kind == "fstring":
                fstring_fields.append("FORMAT_IN")
            else:
                truly_missing.append("FORMAT_IN_empty")
        if not format_out:
            if format_out_kind == "fstring":
                fstring_fields.append("FORMAT_OUT")
            else:
                truly_missing.append("FORMAT_OUT_empty")

        if truly_missing:
            detail_msg = "; ".join(truly_missing)
            obs_parts = []
            if "class_not_found" in truly_missing:
                obs_parts.append(f"Router 类 '{router_class}' 在目标文件中不存在")
            else:
                if "DESCRIPTION_empty" in truly_missing:
                    obs_parts.append("DESCRIPTION 为空")
                if "FORMAT_IN_empty" in truly_missing:
                    obs_parts.append("FORMAT_IN 为空")
                if "FORMAT_OUT_empty" in truly_missing:
                    obs_parts.append("FORMAT_OUT 为空")
            observation = "; ".join(obs_parts)

            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={
                    "router_class": router_class,
                    "source_file": input_data.get("source_file", ""),
                    "source_root": input_data.get("source_root", ""),
                    "extracted": input_data,
                    "sig_ok": False,
                    "checks": [{
                        "check": "signature",
                        "standard": "R-01/R-02 基础元数据存在性",
                        "severity": "CRITICAL",
                        "passed": False,
                        "observation": observation,
                        "detail": {"missing": truly_missing},
                    }],
                },
                diagnosis=f"RouterSignature FAIL: {router_class} — {detail_msg}",
            )

        # PASS 路径：构建签名检查（含 f-string 附加 warning）
        desc_len = len(description or "")
        fin_display = "f-string" if format_in_kind == "fstring" else f"'{format_in}'"
        fout_display = "f-string" if format_out_kind == "fstring" else f"'{format_out}'"
        observation = (
            f"DESCRIPTION {desc_len} chars ✓; "
            f"FORMAT_IN={fin_display} ✓; "
            f"FORMAT_OUT={fout_display} ✓"
        )

        sig_checks: list[dict] = [{
            "check": "signature",
            "standard": "R-01/R-02 基础元数据存在性",
            "severity": "CRITICAL",
            "passed": True,
            "observation": observation,
            "detail": None,
        }]
        if fstring_fields:
            sig_checks.append({
                "check": "R-02-fstring",
                "standard": "FORMAT_IN/OUT 必须是字符串字面量，f-string 不可静态分析",
                "severity": "HIGH",
                "passed": False,
                "observation": (
                    f"{' / '.join(fstring_fields)} 使用 f-string，"
                    "Doctor 无法做上下游搜索和契约验证"
                ),
                "detail": {"fstring_fields": fstring_fields},
            })

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "router_class": router_class,
                "source_file": input_data.get("source_file", ""),
                "source_root": input_data.get("source_root", ""),
                "extracted": input_data,
                "sig_ok": True,
                "checks": sig_checks,
            },
            diagnosis=f"RouterSignature PASS: {router_class}",
        )


# ════════════════════════════════════════════════════════════════
# RouterContextCollectorRouter — 跨 source_root 收集上下文
# ════════════════════════════════════════════════════════════════


class RouterContextCollectorRouter(Router):
    """根据 FORMAT_IN/OUT 在整个 source_root 搜索上下文信息：
    1. FORMAT_IN / FORMAT_OUT 的 Format 对象定义（来自任何 formats.py）
    2. 上游 Router（FORMAT_OUT == 本 Router 的 FORMAT_IN 的其他 Router）
    3. 下游 Router（FORMAT_IN == 本 Router 的 FORMAT_OUT 的其他 Router）
    4. Pipeline 引用（哪条 pipeline.py 用到了本 Router 类）

    永远返回 PASS；搜索失败只记录到 context_gaps。
    """

    DESCRIPTION = "跨 source_root 收集 FORMAT_IN/OUT 定义 + 上下游 Router DESCRIPTION + Pipeline 引用；永远 PASS"
    FORMAT_IN = "diag.rtr.sig-checked"
    FORMAT_OUT = "diag.rtr.context"
    INPUT_KEYS = ["router_class", "source_root", "extracted"]

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data["router_class"]
        source_root = Path(input_data.get("source_root", _DEFAULT_SOURCE_ROOT))
        extracted: dict = input_data.get("extracted", {})

        # 防御：FORMAT_IN/OUT 可能是 list（多入口 Router），取第一个元素做搜索
        _raw_in = extracted.get("format_in")
        _raw_out = extracted.get("format_out")
        format_in_id: str | None = _raw_in[0] if isinstance(_raw_in, list) else _raw_in
        format_out_id: str | None = _raw_out[0] if isinstance(_raw_out, list) else _raw_out

        context_gaps: list[str] = []
        if isinstance(_raw_in, list):
            context_gaps.append(
                f"FORMAT_IN 为列表（{_raw_in}），多入口 Router；上游搜索仅用第一个元素"
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
                    f"FORMAT_IN 定义未找到（{format_in_id} 不在任何 formats.py 中）"
                )
        if format_out_id:
            format_out_def = self._find_format_def(source_root, format_out_id)
            if format_out_def is None:
                context_gaps.append(
                    f"FORMAT_OUT 定义未找到（{format_out_id} 不在任何 formats.py 中）"
                )

        # ── 搜索 2: 上下游 Router ──
        if format_in_id or format_out_id:
            upstream_routers, downstream_routers = self._find_neighbors(
                source_root, format_in_id, format_out_id, router_class
            )
            if not upstream_routers and format_in_id:
                context_gaps.append(
                    f"无上游 Router（无 Router 的 FORMAT_OUT={format_in_id}）"
                )

        # ── 搜索 3: Pipeline 引用 ──
        format_in_kind = extracted.get("format_in_kind", "literal")
        format_out_kind = extracted.get("format_out_kind", "literal")
        pipeline_briefs = self._find_pipeline_ref(
            source_root, router_class, format_in_id, format_out_id
        )
        # 向后兼容：pipeline_brief 保留第一个命中（单数），pipeline_briefs 是完整列表
        pipeline_brief = pipeline_briefs[0] if pipeline_briefs else None
        if not pipeline_briefs:
            has_fstring_format = (format_in_kind == "fstring" or format_out_kind == "fstring")
            if has_fstring_format:
                context_gaps.append(
                    "FORMAT_IN/OUT 为 f-string，无法静态确认 pipeline 归属（不一定孤立）"
                )
            else:
                # Router 有字面量 FORMAT，检查同 package 是否有 pipeline 文件
                # （pipeline 自身可能使用 f-string format IDs，导致无法静态匹配）
                _src_file = Path(input_data.get("source_file", "") or "")
                _has_local_pipeline = False
                if _src_file.parent.is_dir():
                    _has_local_pipeline = any(
                        "pipeline" in p.name.lower()
                        for p in _src_file.parent.iterdir()
                        if p.suffix == ".py"
                    )
                if _has_local_pipeline:
                    context_gaps.append(
                        "同 package 有 pipeline 文件，但 format IDs 不可静态匹配"
                        "（pipeline 可能使用 f-string format IDs）"
                    )
                else:
                    context_gaps.append("未在任何 pipeline.py 中使用")

        # ── composite Format 感知 ──
        is_composite_format_in = False
        composite_components: list[str] = []
        if format_in_def and format_in_def.get("components"):
            is_composite_format_in = True
            composite_components = format_in_def["components"]
            # 提取上游节点的 format_out，供 pipeline 覆盖检查
            upstream_format_outs = [
                r.get("format_out") for r in upstream_routers
                if r.get("format_out")
            ]
            missing_components = [
                c for c in composite_components if c not in upstream_format_outs
            ]
            if missing_components:
                context_gaps.append(
                    f"FORMAT_IN '{format_in_id}' 是 composite Format（components={composite_components}），"
                    f"但上游未覆盖这些 component：{missing_components}"
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
            diagnosis=f"RouterContextCollector: {router_class} gaps={len(context_gaps)}",
        )

    def _find_format_def(self, source_root: Path, format_id: str) -> dict | None:
        """在 source_root 下所有 formats.py / formats/*.py 中查找 Format 定义。"""
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
            if format_id not in content:
                continue
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue

            for call_node in _iter_format_calls(tree, format_id):
                kw = _extract_kwargs(call_node)
                if kw.get("id") == format_id:
                    return {
                        "id": kw.get("id"),
                        "name": kw.get("name"),
                        "description": kw.get("description"),
                        "examples": kw.get("examples"),
                        "tags": kw.get("tags"),
                        "json_schema": kw.get("json_schema"),
                        "parent": kw.get("parent"),
                    }
        return None

    def _find_neighbors(
        self,
        source_root: Path,
        format_in_id: str | None,
        format_out_id: str | None,
        self_class: str,
    ) -> tuple[list[dict], list[dict]]:
        """在 source_root 下所有 routers.py / routers/*.py 中查找上下游 Router。"""
        upstream: list[dict] = []
        downstream: list[dict] = []

        router_files: list[Path] = []
        for p in source_root.rglob("routers.py"):
            if "__pycache__" not in str(p) and "_graveyard" not in str(p):
                router_files.append(p)
        for p in source_root.rglob("routers/*.py"):
            if "__pycache__" not in str(p) and "_graveyard" not in str(p):
                router_files.append(p)

        for py_file in router_files:
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # 快速过滤
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
                    continue  # 跳过自身

                # 提取该类的 FORMAT_IN / FORMAT_OUT / DESCRIPTION
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

                # 上游：它的 FORMAT_OUT == 我的 FORMAT_IN
                if format_in_id and cls_format_out == format_in_id:
                    upstream.append({"class": node.name, "description": cls_desc or ""})

                # 下游：它的 FORMAT_IN == 我的 FORMAT_OUT
                if format_out_id and cls_format_in == format_out_id:
                    downstream.append({"class": node.name, "description": cls_desc or ""})

        return upstream, downstream

    def _find_pipeline_ref(
        self,
        source_root: Path,
        router_class: str,
        format_in: str | None = None,
        format_out: str | None = None,
    ) -> list[dict]:
        """在 source_root 下的 pipeline 文件中查找本 Router 的所有引用。

        返回所有命中的 pipeline 简述列表（可能为空）。
        每个元素包含: pipeline_id, node_id, node_kind, purpose

        策略 1 — 类名搜索（直接 import 风格的老式管线）
        策略 2 — FORMAT_IN/OUT 匹配（AnchorSpec/TransformerSpec 风格管线）
        """
        pipeline_files: list[Path] = []
        _seen_pipeline: set[Path] = set()
        for _pat in ("pipeline.py", "*_pipeline.py", "pipeline_*.py"):
            for p in source_root.rglob(_pat):
                if "__pycache__" not in str(p) and "_graveyard" not in str(p) and p not in _seen_pipeline:
                    pipeline_files.append(p)
                    _seen_pipeline.add(p)

        results: list[dict] = []

        # ── 策略 1：类名搜索 ──
        for py_file in pipeline_files:
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(content)
            except Exception:
                continue
            if router_class not in content:
                continue
            pipeline_id = self._extract_pipeline_id_from_tree(tree) or py_file.stem
            purpose = self._extract_pipeline_purpose_from_tree(tree)
            node_id = None
            for line in content.splitlines():
                stripped = line.strip()
                if router_class in stripped:
                    m = re.search(r'id\s*=\s*["\']([^"\']+)["\']', stripped)
                    if m:
                        node_id = m.group(1)
            results.append({
                "pipeline_id": pipeline_id,
                "node_id": node_id,
                "node_kind": None,
                "purpose": purpose,
            })

        # ── 策略 2：FORMAT_IN/OUT 匹配（AnchorSpec/TransformerSpec 风格管线）──
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
        """AST 解析 pipeline 文件，在 AnchorSpec/TransformerSpec 调用中按 FORMAT_IN/OUT 匹配。
        只处理字面量 format_in/format_out（f-string 跳过）。
        返回所有命中节点的列表（去重 pipeline 级别）。
        """
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
            func_name = _get_call_repr(node.func)
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
                        # list format_in — 检查是否有元素命中
                        for elt in val.elts:
                            if isinstance(elt, ast.Constant) and format_in and elt.value == format_in:
                                node_format_in = elt.value
                elif kw.arg == "format_out":
                    if isinstance(val, ast.Constant):
                        node_format_out = val.value

            # 两端都要匹配（有值时）
            in_match = (format_in is None) or (node_format_in == format_in)
            out_match = (format_out is None) or (node_format_out == format_out)

            if in_match and out_match and (node_format_in is not None or node_format_out is not None):
                results.append({
                    "pipeline_id": pipeline_id,
                    "node_id": node_id,
                    "node_kind": "anchor" if func_name == "AnchorSpec" else "transformer",
                    "purpose": purpose,
                })
                # 每个 pipeline 文件只返回第一个命中节点（防止同 pipeline 多次出现）
                break

        return results

    def _extract_pipeline_id_from_tree(self, tree: ast.Module) -> str | None:
        """从 PipelineSpec(id=...) 调用中提取 pipeline ID。"""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _get_call_repr(node.func) == "PipelineSpec":
                for kw in node.keywords:
                    if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                        return kw.value.value
        return None

    def _extract_pipeline_purpose_from_tree(self, tree: ast.Module) -> str:
        """从 PipelineSpec(purpose=...) 调用中提取 purpose 字段（业务目标描述）。"""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _get_call_repr(node.func) == "PipelineSpec":
                for kw in node.keywords:
                    if kw.arg == "purpose" and isinstance(kw.value, ast.Constant):
                        return kw.value.value
        return ""


# ════════════════════════════════════════════════════════════════
# RouterDeterministicCheckRouter — 11 项确定性检查
# ════════════════════════════════════════════════════════════════


class RouterDeterministicCheckRouter(Router):
    """对 Router 类执行 11 项确定性检查（R-01/R-04/R-05/R-06/R-10/R-11/R-12/R-13/R-17）
    以及 R-07 的 AST 信号格式化。永远返回 PASS，问题记录到 checks。
    """

    DESCRIPTION = "对 Router run() 源码执行 12 项确定性检查（R-01/04/04-async/02-list/05/06/10/11/12/13/17/18 + R-07 信号）。R-18 FieldCoverage：run() 访问字段 vs FORMAT_IN json_schema properties 双向覆盖检查"
    FORMAT_IN = "diag.rtr.context"
    FORMAT_OUT = "diag.rtr.det-checks"
    INPUT_KEYS = ["router_class", "extracted"]

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data["router_class"]
        extracted: dict = input_data.get("extracted", {})
        run_source: str = extracted.get("run_source", "") or ""
        run_line_count: int = extracted.get("run_line_count", 0)
        description: str = extracted.get("description") or ""
        ast_signals: dict = extracted.get("ast_signals", {})
        router_kind: str = ast_signals.get("router_kind", "RULE")
        llm_calls: list = ast_signals.get("llm_calls", [])
        self_assignments: list = ast_signals.get("self_assignments", [])
        verdict_patterns: list = ast_signals.get("verdict_patterns", [])
        exception_patterns: list = ast_signals.get("exception_patterns", [])

        # 读取模块级 import（需要从 source_file 扫描）
        source_file = Path(input_data.get("source_file", ""))
        module_imports = self._read_module_imports(source_file)

        new_checks: list[dict] = []

        # ── R-01: DESCRIPTION 长度 ──
        desc_len = len(description)
        new_checks.append({
            "check": "R-01",
            "standard": "DESCRIPTION ≥ 50 字符",
            "severity": "HIGH",
            "passed": desc_len >= 50,
            "observation": f"DESCRIPTION {desc_len} chars，阈值 50 {'✓' if desc_len >= 50 else '✗'}",
            "detail": {"measured": desc_len, "threshold": 50},
        })

        # ── R-04: 统一 LLMClient（无直接 import openai/anthropic）──
        # 注意：只检查 module-level imports，不在 run_source 中搜索 endpoint 字符串。
        # run_source 搜索会产生自参照假阳性（R-04 检测代码本身含有这些字符串字面量）。
        r04_violations = [imp for imp in module_imports if any(pat in imp for pat in _DIRECT_LLM_IMPORTS)]
        new_checks.append({
            "check": "R-04",
            "standard": "统一 LLMClient，无直接 openai/anthropic import",
            "severity": "CRITICAL",
            "passed": len(r04_violations) == 0,
            "observation": (
                "无直接 LLM import ✓"
                if not r04_violations
                else f"发现直接 LLM import: {', '.join(r04_violations)}"
            ),
            "detail": {"violations": r04_violations} if r04_violations else None,
        })

        # ── R-04-async: run() 不应为 async ──
        run_is_async = extracted.get("run_is_async", False)
        new_checks.append({
            "check": "R-04-async",
            "standard": "run() 不应为 async（LAP 同步协议，PipelineRunner 用 to_thread 包装）",
            "severity": "MEDIUM",
            "passed": not run_is_async,
            "observation": (
                "run() 定义为 async def，违反同步协议 ✗"
                if run_is_async else
                "run() 是同步方法 ✓"
            ),
            "detail": None,
        })

        # ── R-02-list: FORMAT_IN/OUT 不应为列表 ──
        format_in_kind = extracted.get("format_in_kind", "literal")
        format_out_kind = extracted.get("format_out_kind", "literal")
        list_fields = []
        if format_in_kind == "list":
            list_fields.append("FORMAT_IN")
        if format_out_kind == "list":
            list_fields.append("FORMAT_OUT")
        if list_fields:
            _raw_in = extracted.get("format_in")
            _raw_out = extracted.get("format_out")
            _obs_parts = []
            if "FORMAT_IN" in list_fields:
                _obs_parts.append(f"FORMAT_IN={_raw_in}")
            if "FORMAT_OUT" in list_fields:
                _obs_parts.append(f"FORMAT_OUT={_raw_out}")
            new_checks.append({
                "check": "R-02-list",
                "standard": "FORMAT_IN/OUT 应为单一 Format ID 字符串，不应是列表",
                "severity": "HIGH",
                "passed": False,
                "observation": (
                    f"{' / '.join(list_fields)} 为列表（{'; '.join(_obs_parts)}）。"
                    "正确做法：① AnchorSpec(format_in=[...]) 在 pipeline 中声明 fan-in；"
                    "② 定义 composite Format（Format.components=[...]），Router 类 FORMAT_IN 保持单字符串指向该复合 Format。"
                ),
                "detail": {"list_fields": list_fields, "format_in": _raw_in, "format_out": _raw_out},
            })

        # ── R-05: PASS + FAIL 双覆盖 ──
        kinds = {vp.get("kind") for vp in verdict_patterns if vp.get("kind")}
        has_pass = "PASS" in kinds
        has_fail = "FAIL" in kinds
        r05_passed = has_pass and has_fail
        r05_obs = []
        if has_pass:
            r05_obs.append("PASS ✓")
        else:
            r05_obs.append("PASS 缺失 ✗")
        if has_fail:
            r05_obs.append("FAIL ✓")
        else:
            r05_obs.append("FAIL 缺失 ✗")
        new_checks.append({
            "check": "R-05",
            "standard": "Verdict 覆盖 PASS 和 FAIL",
            "severity": "HIGH",
            "passed": r05_passed,
            "observation": "; ".join(r05_obs),
            "detail": {"kinds_found": list(kinds)},
        })

        # ── R-06: 不直接写文件 ──
        r06_violations: list[str] = []
        for line_no, line in enumerate(run_source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat in _FILE_WRITE_PATTERNS:
                if pat in stripped:
                    # 排除 guarded_write 调用
                    if "guarded_write" in stripped:
                        continue
                    # 排除 'r' / 'rb' 模式的 open
                    if pat == "open(" and ("'r'" in stripped or '"r"' in stripped or "'rb'" in stripped):
                        continue
                    r06_violations.append(f"L{line_no}: {stripped[:80]}")
        new_checks.append({
            "check": "R-06",
            "standard": "不直接写文件（需走 guarded_write）",
            "severity": "HIGH",
            "passed": len(r06_violations) == 0,
            "observation": (
                "无直接文件写操作 ✓"
                if not r06_violations
                else f"发现直接写操作: {'; '.join(r06_violations[:3])}"
            ),
            "detail": {"violations": r06_violations} if r06_violations else None,
        })

        # ── R-10: run() ≤ 80 行 ──
        new_checks.append({
            "check": "R-10",
            "standard": "run() ≤ 80 行",
            "severity": "MEDIUM",
            "passed": run_line_count <= 80,
            "observation": (
                f"run() 共 {run_line_count} 行，阈值 80 {'✓' if run_line_count <= 80 else '✗'}"
                + (f"（超出 {run_line_count - 80} 行）" if run_line_count > 80 else "")
            ),
            "detail": {"measured": run_line_count, "threshold": 80},
        })

        # ── R-11: 无硬编模型名 ──
        r11_violations: list[str] = []
        for line_no, line in enumerate(run_source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat in _KNOWN_MODEL_PATTERNS:
                if pat in stripped and ('"' in stripped or "'" in stripped):
                    r11_violations.append(f"L{line_no}: 含 '{pat}'")
                    break
        new_checks.append({
            "check": "R-11",
            "standard": "无硬编模型名（详见 _KNOWN_MODEL_PATTERNS）",
            "severity": "MEDIUM",
            "passed": len(r11_violations) == 0,
            "observation": (
                "无硬编模型名 ✓"
                if not r11_violations
                else f"发现硬编模型名: {'; '.join(r11_violations[:3])}"
            ),
            "detail": {"violations": r11_violations} if r11_violations else None,
        })

        # ── R-12: 无 LLM 协议泄漏 ──
        r12_violations: list[str] = []
        for line_no, line in enumerate(run_source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat in _PROTOCOL_LEAK_PATTERNS:
                if pat in stripped:
                    r12_violations.append(f"L{line_no}: {stripped[:80]}")
        new_checks.append({
            "check": "R-12",
            "standard": "无 LLM 协议泄漏（block.type/choices[0] 等）",
            "severity": "MEDIUM",
            "passed": len(r12_violations) == 0,
            "observation": (
                "无协议泄漏 ✓"
                if not r12_violations
                else f"发现协议泄漏: {'; '.join(r12_violations[:3])}"
            ),
            "detail": {"violations": r12_violations} if r12_violations else None,
        })

        # ── R-13: RULE Router confidence = 1.0 ──
        if router_kind == "RULE":
            bad_conf = [
                vp for vp in verdict_patterns
                if vp.get("confidence") is not None and vp.get("confidence") != 1.0
            ]
            new_checks.append({
                "check": "R-13",
                "standard": "确定性 Router confidence = 1.0",
                "severity": "MEDIUM",
                "passed": len(bad_conf) == 0,
                "observation": (
                    "所有 Verdict.confidence = 1.0 ✓"
                    if not bad_conf
                    else f"发现非 1.0 置信度: {[vp.get('confidence') for vp in bad_conf]}"
                ),
                "detail": {"bad_confidence": bad_conf} if bad_conf else None,
            })

        # ── R-input-unused: run() 未从 input_data 读取任何键（可能是 stub 实现）──
        # 触发条件：非 LLM Router + FORMAT_IN≠FORMAT_OUT + input_keys_accessed 为空
        # 豁免：整体传递 input_data 作为输出（output_keys 为空，用 input_data 直接传递）
        _input_keys = ast_signals.get("input_keys_accessed", [])
        _output_keys = ast_signals.get("output_keys_produced", [])
        _is_llm_router = bool(llm_calls)
        _format_in_val = extracted.get("format_in")
        _format_out_val = extracted.get("format_out")
        _is_passthrough = (
            _format_in_val and _format_out_val and _format_in_val == _format_out_val
        )
        _is_whole_passthrough = (not _output_keys)  # 可能直接 output=input_data
        if (
            not _is_llm_router
            and not _is_passthrough
            and not _is_whole_passthrough
            and not _input_keys
            and run_line_count > 5
            and format_in_kind == "literal"
        ):
            new_checks.append({
                "check": "R-input-unused",
                "standard": "RULE Router 的 run() 应从 input_data 读取键值（d_rule_output_precise）",
                "severity": "HIGH",
                "passed": False,
                "observation": (
                    f"run() 未访问 input_data 的任何键（input_keys_accessed=[]），"
                    f"但 FORMAT_IN({_format_in_val}) ≠ FORMAT_OUT({_format_out_val})。"
                    "可能是 stub 实现（硬编码输出）或使用了整体传递模式。需 LLM 审计确认。"
                ),
                "detail": {
                    "input_keys_accessed": _input_keys,
                    "output_keys_produced": _output_keys,
                    "run_line_count": run_line_count,
                },
            })

        # ── R-17: 异常不假通过 ──
        r17_violations = [
            ep for ep in exception_patterns if ep.get("handling") == "return_pass"
        ]
        new_checks.append({
            "check": "R-17",
            "standard": "异常不假通过（except → FAIL/raise，不能 → PASS）",
            "severity": "HIGH",
            "passed": len(r17_violations) == 0,
            "observation": (
                "无 except→PASS 模式 ✓"
                if not r17_violations
                else f"发现 {len(r17_violations)} 处 except→PASS: {[ep.get('exception_type') for ep in r17_violations]}"
            ),
            "detail": {"violations": r17_violations} if r17_violations else None,
        })

        # ── R-18: FieldCoverage — 访问字段 vs FORMAT_IN json_schema 覆盖度 ──
        # 仅在 FORMAT_IN json_schema 有 properties 且 router 不是 LLM 类型时检查。
        # 两个方向：
        #   A. required 字段声明了但 run() 从未访问 → 过度声明（advisory）
        #   B. run() 访问了但 json_schema.properties 未声明 → schema 不完整（advisory）
        _format_in_def: dict = input_data.get("format_in_def") or {}
        _fmt_schema: dict = _format_in_def.get("json_schema") or {}
        _fmt_props: dict = _fmt_schema.get("properties") or {}
        _fmt_required: list = _fmt_schema.get("required") or []
        _accessed: list[str] = ast_signals.get("input_keys_accessed", [])

        if (
            _fmt_props
            and not _is_llm_router
            and format_in_kind == "literal"
            and run_line_count > 3
        ):
            # A. required 字段从未被 run() 访问（可能是 schema 过度声明）
            never_accessed = [k for k in _fmt_required if k not in _accessed]
            # B. run() 访问了 schema 未声明的字段（可能是 schema 不完整）
            undeclared_accesses = [k for k in _accessed if k not in _fmt_props]
            # 噪音过滤：排除通用 passthrough 键（如 format_id / source_root / checks 等）
            _PASSTHROUGH_KEYS = {
                "format_id", "source_root", "checks", "extracted", "sig_diff_ok",
                "reports", "router_class", "source_file", "format_in_def", "format_out_def",
            }
            undeclared_accesses = [k for k in undeclared_accesses if k not in _PASSTHROUGH_KEYS]

            if never_accessed or undeclared_accesses:
                parts = []
                if never_accessed:
                    parts.append(f"required 字段声明但未访问: {never_accessed}")
                if undeclared_accesses:
                    parts.append(f"访问了 schema 未声明字段: {undeclared_accesses}")
                new_checks.append({
                    "check": "R-18",
                    "standard": "FORMAT_IN json_schema 的 required 字段应在 run() 中被访问；run() 访问的字段应在 schema 中声明",
                    "severity": "MEDIUM",
                    "passed": False,
                    "observation": "；".join(parts),
                    "detail": {
                        "never_accessed_required": never_accessed,
                        "undeclared_accesses": undeclared_accesses,
                        "schema_properties": list(_fmt_props.keys()),
                        "accessed_keys": _accessed,
                    },
                })
            else:
                new_checks.append({
                    "check": "R-18",
                    "standard": "FORMAT_IN json_schema 的 required 字段应在 run() 中被访问",
                    "severity": "MEDIUM",
                    "passed": True,
                    "observation": (
                        f"required 字段全部被访问（{len(_fmt_required)} 项）"
                        + ("，无未声明访问 ✓" if not undeclared_accesses else "")
                    ),
                    "detail": None,
                })

        # ── R-07 信号: self 赋值分类（不做硬判定，passed=null，供 LLM 解读）──
        for sa in self_assignments:
            if sa.get("classification") in ("SUSPICIOUS", "LIKELY_VIOLATION"):
                sev = "MEDIUM" if sa.get("classification") == "SUSPICIOUS" else "HIGH"
                new_checks.append({
                    "check": "R-07-signal",
                    "standard": "跨调用状态（信号，非判定）",
                    "severity": sev,
                    "passed": None,
                    "observation": (
                        f"run() 第 {sa.get('line', '?')} 行 self.{sa.get('var')} = ... "
                        f"（分类: {sa.get('classification')}），交由语义审计判断严重性"
                    ),
                    "detail": sa,
                })

        # 追加到 acc
        output = dict(input_data)
        output["checks"] = list(input_data.get("checks", [])) + new_checks

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=(
                f"RouterDetChecker: {router_class} "
                f"passed={sum(1 for c in new_checks if c.get('passed') is True)}"
                f"/{sum(1 for c in new_checks if c.get('passed') is not None)} checks"
            ),
        )

    def _read_module_imports(self, source_file: Path) -> list[str]:
        """读取源文件的模块级 import 语句。"""
        if not source_file.exists():
            return []
        try:
            content = source_file.read_text(encoding="utf-8", errors="ignore")
            imports = []
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    imports.append(stripped)
            return imports
        except Exception:
            return []


# ════════════════════════════════════════════════════════════════
# RouterHealthWriterRouter — 汇总评分，生成 Router 健康档案
# ════════════════════════════════════════════════════════════════


class RouterHealthWriterRouter(Router):
    """汇总 acc.checks，计算加权健康评分（CRITICAL=4/HIGH=3/MEDIUM=2/LOW=1/INFO=0），
    优先采用 LLM 审计的 overall_grade，否则按分数映射评级，输出 diag.rtr.health-record。
    """

    DESCRIPTION = "汇总 acc.checks 加权评分（CRITICAL=4/HIGH=3/MEDIUM=2/LOW=1），优先取 LLM 审计等级，生成 Router 健康档案"
    FORMAT_IN = "diag.rtr.audit"
    FORMAT_OUT = "diag.rtr.health-record"
    INPUT_KEYS = ["router_class", "checks"]

    _SEVERITY_WEIGHTS = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data["router_class"]
        checks: list[dict] = input_data.get("checks", [])
        context: dict = input_data.get("context", {})
        audit_path: str | None = input_data.get("audit_path")

        # ── 1. 加权评分 ──
        total_weight = 0
        passed_weight = 0
        for check in checks:
            passed = check.get("passed")
            if passed is None:  # 信号类或审计跳过，不计入
                continue
            sev = check.get("severity", "LOW")
            weight = self._SEVERITY_WEIGHTS.get(sev, 0)
            if weight == 0:  # INFO 不计入分母
                continue
            total_weight += weight
            if passed:
                passed_weight += weight

        health_score = (passed_weight / total_weight) if total_weight > 0 else 1.0

        # ── 2. 评级（优先 LLM 审计 overall_grade）──
        llm_grade: str | None = None
        for check in checks:
            if check.get("check") == "contextual_audit" and check.get("detail"):
                llm_grade = check["detail"].get("overall_grade")
                break

        if llm_grade in ("A", "B", "C", "D"):
            health_grade = llm_grade
        elif health_score >= 0.90:
            health_grade = "A"
        elif health_score >= 0.75:
            health_grade = "B"
        elif health_score >= 0.55:
            health_grade = "C"
        else:
            health_grade = "D"

        # ── 3. 失败分组 ──
        critical_failures: list[str] = []
        high_failures: list[str] = []
        medium_failures: list[str] = []

        for check in checks:
            if check.get("passed") is False:
                name = f"{check.get('check', '?')}: {check.get('observation', '')}"
                sev = check.get("severity", "")
                if sev == "CRITICAL":
                    critical_failures.append(name)
                elif sev == "HIGH":
                    high_failures.append(name)
                elif sev == "MEDIUM":
                    medium_failures.append(name)

        # ── 4. 孤立 Router 检测 ──
        context_gaps = context.get("context_gaps", [])
        is_isolated = any("未在任何 pipeline.py 中使用" in g for g in context_gaps)

        # ── 5. 摘要 ──
        if not input_data.get("sig_ok", True):
            summary = f"Router '{router_class}' 基础元数据缺失，无法完整诊断"
        elif health_grade in ("A", "B"):
            issue_note = ""
            if medium_failures:
                issue_note = f"：{'; '.join(medium_failures[:2])}"
            summary = f"Router '{router_class}' 健康状况良好（{health_grade} 级，{health_score:.0%}）{issue_note}"
        else:
            all_issues = critical_failures + high_failures + medium_failures
            summary = (
                f"Router '{router_class}' 存在问题（{health_grade} 级，{health_score:.0%}）："
                + "；".join(all_issues[:3])
            )

        if is_isolated:
            summary += "【孤立 Router：未被任何 pipeline 使用】"

        health_record = {
            "router_class": router_class,
            "source_file": input_data.get("source_file", ""),
            "source_root": input_data.get("source_root", ""),
            "sig_ok": input_data.get("sig_ok", True),
            "health_score": round(health_score, 3),
            "health_grade": health_grade,
            "is_isolated": is_isolated,
            "checks": checks,
            "critical_failures": critical_failures,
            "high_failures": high_failures,
            "medium_failures": medium_failures,
            "audit_path": audit_path or "",
            "summary": summary,
        }

        self._save_router_health(router_class, health_record, input_data)

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=health_record,
            diagnosis=f"RouterHealthWriter: {router_class} grade={health_grade} score={health_score:.2f}",
        )

    def _save_router_health(
        self, router_class: str, health_record: dict, input_data: dict
    ) -> None:
        """中央 + 就近双写 Router 健康档案（静默失败）。"""
        if not _ARCHIVE_AVAILABLE:
            return
        try:
            source_file = input_data.get("source_file", "")
            source_root = input_data.get("source_root", "")
            pkg = _infer_pkg(Path(source_file), Path(source_root)) if (source_file and source_root) else "unknown"
            _archive = _HealthArchive(_REGISTRY_ARCHIVE_DIR)
            _snapshot = _make_router_snapshot(f"router:{pkg}.{router_class}", health_record, source_file, _archive)
            _write_proximity(source_file, "routers", router_class, _snapshot)
        except Exception as _e:
            logger.debug("HealthArchive write skipped for %s: %s", router_class, _e)


# ════════════════════════════════════════════════════════════════
# RouterContextualAuditRouter — LLM 全语境语义审计（层 A/B/C/D）
# ════════════════════════════════════════════════════════════════


class RouterContextualAuditRouter(Router):
    """对 Router 进行全语境语义审计：注入 Router 源码 + FORMAT 定义 + 邻居 DESCRIPTION
    + Pipeline 简述 + 确定性检查失败摘要 + AST 信号 + router.md 标准节选，
    由 LLM 评估层 A（前提）/ B（执行质量）/ C（产出忠实度）/ D（本职手艺）四层，
    产出等级 A/B/C/D 和改进建议，报告存档到 data/doctor/audit/rtr_<ClassName>/。

    RULE Router 使用 Schema B（精简），LLM Router 使用 Schema A（完整）。
    LLM 失败最多 RETRY 2 次；最终失败不阻断管线，追加 passed=null check。
    """

    DESCRIPTION = "LLM 全语境审计：Router 源码 + FORMAT 定义 + 邻居 + 确定性检查结果 → 层 A/B/C/D 评级 + 改进建议 + git 存档"
    FORMAT_IN = "diag.rtr.det-checks"
    FORMAT_OUT = "diag.rtr.audit"
    INPUT_KEYS = ["router_class", "extracted", "context", "checks"]

    _SYSTEM = (
        "你是一位资深软件工程师，正在对一个 Router 类进行代码审计。"
        "Router 是一个处理节点，接受结构化输入（FORMAT_IN），产出结构化输出（FORMAT_OUT）。"
        "请根据给定的信息，对 Router 进行四层评估："
        "层 A（信息前提：Router 是否有足够信息做好本职工作）、"
        "层 B（执行质量：Router 是否做对了、做完整了）、"
        "层 C（产出忠实度：输出是否与 FORMAT_OUT 契约一致）、"
        "层 D（本职手艺：LLM Router 的 prompt 设计；RULE Router 的边界完整性）。"
        "请严格按照指定 JSON schema 输出，不要输出其他内容。"
        "对于三值字段（'true'|'false'|'uncertain'），当信息不足以判断时输出 'uncertain'，不要强行给结论。"
    )

    _SCHEMA_A_TEMPLATE = """\
输出格式（LLM Router，严格 JSON）：
{
  "a_info_sufficient": "true | false | uncertain",
  "a_info_gaps": "具体描述信息缺口，或 'none'",
  "a_implicit_assumptions": "隐式假设列表（逗号分隔），或 'none'",
  "a_budget_feasible": "true | false | uncertain",
  "a_budget_notes": "token 预算评估，或 'none'",
  "b_r03_homogeneous": "true | false | uncertain",
  "b_r03_notes": "多 LLM 调用同质性分析",
  "b_r08_intermediates_ok": "true | false",
  "b_r08_candidates": "有独立价值的中间变量列表，或 'none'",
  "b_r16_generic_extracted": "true | false",
  "b_r16_candidates": "可提取为 Tool 的通用逻辑，或 'none'",
  "b_error_paths_complete": "true | false | uncertain",
  "b_error_notes": "错误路径覆盖评估",
  "b_hallucination_risk": "low | medium | high",
  "b_hallucination_notes": "幻觉风险评估",
  "c_r14_diagnosis_quality": "true | false | uncertain",
  "c_r14_notes": "diagnosis 字符串质量评估",
  "c_r15_tags_accurate": "true | false | N/A",
  "c_r15_notes": "granted_tags 与验证行为一致性",
  "c_format_out_aligned": "true | false | uncertain",
  "c_format_out_notes": "Verdict.output 与 FORMAT_OUT 描述的对齐情况",
  "c_confidence_calibrated": "true | false | N/A",
  "c_confidence_notes": "confidence 校准评估",
  "d_honesty": "true | false",
  "d_honesty_notes": "prompt 是否允许 LLM 表达不确定性",
  "d_precision": "true | false",
  "d_precision_notes": "输出 schema 字段与 FORMAT_OUT 对齐情况",
  "d_efficiency": "true | false | uncertain",
  "d_efficiency_notes": "是否避免让 LLM 做确定性任务",
  "d_judgment": "true | false | uncertain",
  "d_judgment_notes": "prompt 是否提供了评级框架和灰色地带处理指引",
  "p_should_split": "true | false | uncertain",
  "p_split_reason": "拆分建议，或 'none'",
  "p_could_merge": "true | false",
  "p_merge_notes": "合并建议，或 'none'",
  "overall_grade": "A | B | C | D",
  "key_findings": ["发现 1", "发现 2"],
  "improvement_suggestions": ["建议 1", "建议 2"],
  "detailed_report": "完整 Markdown 审计报告"
}"""

    _SCHEMA_B_TEMPLATE = """\
输出格式（RULE Router，严格 JSON）：
{
  "a_info_sufficient": "true | false | uncertain",
  "a_info_gaps": "具体描述信息缺口，或 'none'",
  "a_implicit_assumptions": "隐式假设列表（逗号分隔），或 'none'",
  "a_budget_feasible": "true | false | uncertain",
  "a_budget_notes": "复杂度评估（RULE Router 不调 LLM，但可能有高计算成本）",
  "b_r08_intermediates_ok": "true | false",
  "b_r08_candidates": "有独立价值的中间变量列表，或 'none'",
  "b_r16_generic_extracted": "true | false",
  "b_r16_candidates": "可提取为 Tool 的通用逻辑，或 'none'",
  "b_error_paths_complete": "true | false | uncertain",
  "b_error_notes": "错误路径覆盖评估",
  "c_r14_diagnosis_quality": "true | false | uncertain",
  "c_r14_notes": "diagnosis 字符串质量评估",
  "c_r15_tags_accurate": "true | false | N/A",
  "c_r15_notes": "granted_tags 与验证行为一致性",
  "c_format_out_aligned": "true | false | uncertain",
  "c_format_out_notes": "Verdict.output 与 FORMAT_OUT 描述的对齐情况",
  "c_confidence_calibrated": "true | false | N/A",
  "c_confidence_notes": "confidence 校准（RULE Router 应全 1.0）",
  "d_rule_boundary_complete": "true | false",
  "d_rule_boundary_notes": "边界条件（空输入/类型错误/字段缺失）是否全有 FAIL 路径",
  "d_rule_output_precise": "true | false",
  "d_rule_output_notes": "Verdict.output 字段与 FORMAT_OUT 严格对齐情况",
  "p_should_split": "true | false | uncertain",
  "p_split_reason": "拆分建议，或 'none'",
  "p_could_merge": "true | false",
  "p_merge_notes": "合并建议，或 'none'",
  "overall_grade": "A | B | C | D",
  "key_findings": ["发现 1", "发现 2"],
  "improvement_suggestions": ["建议 1", "建议 2"],
  "detailed_report": "完整 Markdown 审计报告"
}"""

    def __init__(self, model: str | None = None):
        self._model = model or "qwen3.6-plus"

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data["router_class"]
        source_root = Path(input_data.get("source_root", _DEFAULT_SOURCE_ROOT))
        extracted: dict = input_data.get("extracted", {})
        context: dict = input_data.get("context", {})
        checks: list[dict] = input_data.get("checks", [])
        router_kind: str = extracted.get("ast_signals", {}).get("router_kind", "RULE")

        # 加载 router.md 标准节选
        standards = self._load_standards(source_root)

        # 构建 LLM 消息
        user_msg = self._build_user_msg(
            router_class, extracted, context, checks, router_kind, standards
        )
        schema_template = self._SCHEMA_A_TEMPLATE if router_kind == "LLM" else self._SCHEMA_B_TEMPLATE

        # 调用 LLM（最多 RETRY 2 次）
        audit_data: dict = {}
        raw_text: str = ""
        for attempt in range(3):
            audit_data, raw_text = self._audit(user_msg, schema_template)
            if audit_data.get("overall_grade") in ("A", "B", "C", "D"):
                break
            if attempt < 2:
                logger.warning(
                    "RouterContextualAudit attempt %d failed for %s, retrying",
                    attempt + 1, router_class,
                )

        # 存档报告
        audit_path: str = ""
        if audit_data:
            archived = self._archive_report(router_class, router_kind, source_root, audit_data)
            if archived:
                audit_path = str(archived)

        # 追加 check 记录
        if audit_data.get("overall_grade"):
            grade = audit_data["overall_grade"]
            observation = (
                f"grade={grade}; "
                + "; ".join(f"{k}={v}" for k, v in list(audit_data.items())[:6]
                            if k not in ("overall_grade", "key_findings", "improvement_suggestions", "detailed_report")
                            and isinstance(v, str))[:200]
            )
            audit_check = {
                "check": "contextual_audit",
                "standard": "层 A/B/C/D + 管线信号",
                "severity": "INFO",
                "passed": True,
                "observation": observation,
                "detail": audit_data,
            }
        else:
            audit_check = {
                "check": "contextual_audit",
                "standard": "层 A/B/C/D + 管线信号",
                "severity": "INFO",
                "passed": None,
                "observation": "LLM 审计失败（3 次重试后仍无效响应），需人工复核",
                "detail": {"raw_text": raw_text[:500] if raw_text else "(无响应)"},
            }

        output = dict(input_data)
        output["checks"] = list(checks) + [audit_check]
        if audit_path:
            output["audit_path"] = audit_path

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=(
                f"RouterContextualAudit: {router_class} grade={audit_data.get('overall_grade', 'N/A')}"
            ),
        )

    def _build_user_msg(
        self,
        router_class: str,
        extracted: dict,
        context: dict,
        checks: list[dict],
        router_kind: str,
        standards: str,
    ) -> str:
        parts: list[str] = [f"# Router 审计请求: {router_class} ({router_kind} Router)\n"]

        # Router 源码
        run_source = extracted.get("run_source", "")
        parts.append(f"## Router 完整源码\n```python\nclass {router_class}(Router):\n"
                     f"    DESCRIPTION = {repr(extracted.get('description', ''))}\n"
                     f"    FORMAT_IN = {repr(extracted.get('format_in', ''))}\n"
                     f"    FORMAT_OUT = {repr(extracted.get('format_out', ''))}\n\n"
                     f"{run_source}\n```")

        # FORMAT_IN / FORMAT_OUT 定义
        fmt_in_def = context.get("format_in_def")
        if fmt_in_def:
            parts.append(f"## FORMAT_IN 定义 ({extracted.get('format_in')})\n"
                         f"description: {fmt_in_def.get('description', '(未找到)')}\n"
                         f"tags: {fmt_in_def.get('tags', [])}\n"
                         f"examples: {json.dumps(fmt_in_def.get('examples', []), ensure_ascii=False)[:500]}")
        else:
            parts.append(f"## FORMAT_IN 定义\n(未找到 {extracted.get('format_in')} 的 Format 定义)")

        fmt_out_def = context.get("format_out_def")
        if fmt_out_def:
            parts.append(f"## FORMAT_OUT 定义 ({extracted.get('format_out')})\n"
                         f"description: {fmt_out_def.get('description', '(未找到)')}\n"
                         f"tags: {fmt_out_def.get('tags', [])}\n"
                         f"examples: {json.dumps(fmt_out_def.get('examples', []), ensure_ascii=False)[:500]}")
        else:
            parts.append(f"## FORMAT_OUT 定义\n(未找到 {extracted.get('format_out')} 的 Format 定义)")

        # 上下游邻居
        upstreams = context.get("upstream_routers", [])
        if upstreams:
            parts.append("## 上游 Router（生产 FORMAT_IN 的节点）")
            for u in upstreams:
                parts.append(f"- **{u['class']}**: {u.get('description', '')}")
        else:
            parts.append("## 上游 Router\n(未找到，可能是管线入口)")

        downstreams = context.get("downstream_routers", [])
        if downstreams:
            parts.append("## 下游 Router（消费 FORMAT_OUT 的节点）")
            for d in downstreams:
                parts.append(f"- **{d['class']}**: {d.get('description', '')}")
        else:
            parts.append("## 下游 Router\n(未找到，可能是管线出口)")

        # Pipeline 简述
        pipeline_briefs = context.get("pipeline_briefs", [])
        pb = context.get("pipeline_brief")
        if pipeline_briefs:
            pipeline_purpose = context.get("pipeline_purpose", "")
            pipeline_lines = [f"pipeline_id: {b.get('pipeline_id', '?')} | node_id: {b.get('node_id', '?')}"
                              for b in pipeline_briefs]
            pipeline_summary = "\n".join(pipeline_lines)
            if pipeline_purpose:
                pipeline_summary += f"\n业务目标: {pipeline_purpose}"
            parts.append(f"## Pipeline 简述\n{pipeline_summary}")
        elif pb:
            parts.append(f"## Pipeline 简述\n"
                         f"pipeline_id: {pb.get('pipeline_id', '?')} | "
                         f"node_id: {pb.get('node_id', '?')}")
        else:
            parts.append("## Pipeline 简述\n(未在任何 pipeline.py 中找到引用)")

        # 确定性检查失败摘要
        failed_checks = [c for c in checks if c.get("passed") is False]
        if failed_checks:
            parts.append("## 确定性检查失败项（已有结论，无需重复判断）")
            for c in failed_checks:
                parts.append(f"- [{c.get('severity')}] {c.get('check')}: {c.get('observation')}")
        else:
            parts.append("## 确定性检查\n所有确定性检查已通过")

        # AST 衍生信号
        ast_signals = extracted.get("ast_signals", {})
        llm_calls = ast_signals.get("llm_calls", [])
        self_asgs = ast_signals.get("self_assignments", [])
        input_keys = ast_signals.get("input_keys_accessed", [])
        output_keys = ast_signals.get("output_keys_produced", [])
        verdict_pats = ast_signals.get("verdict_patterns", [])

        parts.append(f"## AST 衍生信号\n"
                     f"- router_kind: {router_kind}\n"
                     f"- llm_calls ({len(llm_calls)} 处): {[c.get('line') for c in llm_calls]}\n"
                     f"- input_keys_accessed: {input_keys}\n"
                     f"- output_keys_produced: {output_keys}\n"
                     f"- verdict_patterns: {[vp.get('kind') for vp in verdict_pats]}\n"
                     f"- self_assignments (SUSPICIOUS/LIKELY_VIOLATION): "
                     f"{[sa for sa in self_asgs if sa.get('classification') != 'INFO']}")

        # 标准节选
        if standards:
            parts.append(f"## Router 标准（节选）\n{standards}")

        parts.append(f"\n请输出严格 JSON，按以下 schema：\n{self._SCHEMA_A_TEMPLATE if router_kind == 'LLM' else self._SCHEMA_B_TEMPLATE}")

        return "\n\n".join(parts)

    def _load_standards(self, source_root: Path) -> str:
        """从 docs/standards/worker.md 加载 LLM 专用标准节选。"""
        # 从 source_root 向上找 docs/standards/worker.md
        for parent in [source_root, *source_root.parents[:4]]:
            candidate = parent / "docs" / "standards" / "worker.md"
            if candidate.exists():
                try:
                    content = candidate.read_text(encoding="utf-8")
                    # 提取 LLM 相关部分：四原则 + R-03/08/14/15/16/18/19/20 + RA 反模式 + 附录 B/C
                    # 跳过已由确定性检查覆盖的 R-01/02/04/05/06/07/10/11/12/13/17
                    return self._filter_standards(content)
                except Exception:
                    pass
        return "(router.md 未找到)"

    def _filter_standards(self, content: str) -> str:
        """从 router.md 提取 LLM 需要的部分，跳过确定性检查已覆盖的条目。"""
        lines = content.splitlines()
        result: list[str] = []
        skip_items = {
            "**R-01**", "**R-02**", "**R-04**", "**R-05**",
            "**R-06**", "**R-07**", "**R-10**", "**R-11**",
            "**R-12**", "**R-13**", "**R-17**",
        }
        in_skip = False
        for line in lines:
            # 跳过已由确定性检查覆盖的标准条目
            if any(skip in line for skip in skip_items):
                in_skip = True
            elif line.startswith("**R-") or line.startswith("### ") or line.startswith("## "):
                in_skip = False

            if not in_skip:
                result.append(line)

        return "\n".join(result)

    def _audit(self, user_msg: str, schema_template: str) -> tuple[dict, str]:
        """调用 LLM；返回 (audit_data, raw_text)。失败返回 ({}, error_msg)。"""
        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=self._SYSTEM,
            )
            raw = resp.content[0].text.strip()
            # 去除 markdown 代码块包装
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw.strip())
            data = json.loads(raw)
            return data, raw
        except Exception as e:
            logger.warning("RouterContextualAudit LLM call failed: %s", e)
            return {}, f"(LLM 调用失败: {type(e).__name__}: {e})"

    def _get_git_hash(self, source_root: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True,
                cwd=str(source_root.resolve().parents[1]),
                timeout=5,
            )
            return result.stdout.strip() or "unknown"
        except Exception:
            return "unknown"

    def _archive_report(
        self,
        router_class: str,
        router_kind: str,
        source_root: Path,
        audit_data: dict,
    ) -> Path | None:
        """将 Markdown 报告存档到 data/doctor/audit/rtr_<ClassName>/<git_hash>.md。"""
        try:
            git_hash = self._get_git_hash(source_root)
            safe_name = f"rtr_{router_class}"
            audit_dir = source_root.resolve().parents[1] / "data" / "doctor" / "audit" / safe_name
            audit_dir.mkdir(parents=True, exist_ok=True)

            grade = audit_data.get("overall_grade", "?")
            detailed = audit_data.get("detailed_report", "(无详细报告)")
            findings = audit_data.get("key_findings", [])
            suggestions = audit_data.get("improvement_suggestions", [])

            report_lines = [
                f"# Router 审计报告: {router_class}",
                "",
                f"**Commit**: `{git_hash}`  **Grade**: {grade}  **Kind**: {router_kind}",
                "",
                "## 关键发现",
                *[f"- {f}" for f in findings],
                "",
                "## 改进建议",
                *[f"- {s}" for s in suggestions],
                "",
                detailed,
            ]
            report_path = audit_dir / f"{git_hash}.md"
            report_path.write_text("\n".join(report_lines), encoding="utf-8")
            return report_path
        except Exception as e:
            logger.warning("Failed to archive router audit report for %s: %s", router_class, e)
            return None


# ════════════════════════════════════════════════════════════════
# Pipeline 拓扑诊断管线 Routers
# ════════════════════════════════════════════════════════════════
#
#   PipelineSpecLoaderRouter         (HARD Anchor) 加载 pipeline.py → PipelineSpec 对象列表
#   PipelineStructuralCheckRouter    (RULE) 结构合法性：no_entry/isolated/dead_end/cycle/duplicate_edge
#   PipelineFormatContractCheckRouter(RULE) Format 契约：format_break/composite_missing/granted_tag_chain
#   PipelineMaturityCheckRouter      (RULE) 成熟度一致性：maturity_consistency（短板原则）
#   PipelineSoftHardCheckRouter      (RULE) 软硬配对：P-07 LLM 节点须有 RULE/ANCHOR 下游验证
#   PipelineTopoHealthWriterRouter   (RULE) 汇总所有检查 Finding，计算健康等级，输出健康档案
#


def _load_specs_from_input(input_data: dict) -> "list[Any]":
    """从 input_data 的 specs_data 字段还原 PipelineSpec 列表（内部工具函数）。"""
    from omnicompany.protocol.pipeline import PipelineSpec
    specs_data = input_data.get("specs_data", [])
    specs = []
    for d in specs_data:
        try:
            specs.append(PipelineSpec.model_validate(d))
        except Exception:
            pass
    return specs


def _serialize_findings(findings: "list[Any]", pipeline_id: str) -> "list[dict]":
    """Finding 列表转 dict 列表（内部工具函数）。"""
    return [
        {
            "pipeline_id": pipeline_id,
            "check_id":    f.check_id,
            "level":       f.level,
            "severity":    f.severity,
            "location":    f.location,
            "observation": f.observation,
            "implication": f.implication,
            "cross_refs":  f.cross_refs,
        }
        for f in findings
    ]


class PipelineSpecLoaderRouter(Router):
    """从 pipeline.py 文件加载所有 PipelineSpec 对象（ANCHOR，可短路）。

    加载成功 → PASS（进入 4 个并行检查器）。
    文件不存在 / 无 build_*() 返回 PipelineSpec / 加载异常 → FAIL（EMIT 最小健康档案）。

    支持通过 pipeline_id 过滤，只加载指定管线。
    """
    DESCRIPTION = (
        "从 pipeline.py 文件加载所有 PipelineSpec 对象（调用所有无参数 build_*() 函数）。"
        "加载成功 → PASS 进入 4 个并行拓扑检查器；"
        "文件不存在/无有效 PipelineSpec/加载异常 → FAIL EMIT 最小健康档案。"
    )
    FORMAT_IN  = "diag.pipeline.request"
    FORMAT_OUT = "diag.pipeline.extracted"

    def run(self, input_data: dict) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import load_pipeline_from_file

        pipeline_file = input_data.get("pipeline_file", "")
        if not pipeline_file:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"pipeline_file": "", "load_error": "pipeline_file 未提供", "specs_data": []},
                diagnosis="PipelineSpecLoader: pipeline_file 为空",
            )

        try:
            specs = load_pipeline_from_file(pipeline_file)
        except FileNotFoundError:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"pipeline_file": pipeline_file, "load_error": f"文件不存在: {pipeline_file}", "specs_data": []},
                diagnosis=f"PipelineSpecLoader: {pipeline_file} 不存在",
            )
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"pipeline_file": pipeline_file, "load_error": str(exc), "specs_data": []},
                diagnosis=f"PipelineSpecLoader: 加载失败 — {exc}",
            )

        if not specs:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"pipeline_file": pipeline_file, "load_error": "文件中未找到 PipelineSpec（无有效 build_*() 函数）", "specs_data": []},
                diagnosis=f"PipelineSpecLoader: {pipeline_file} 无 build_* 函数",
            )

        filter_id = input_data.get("pipeline_id")
        if filter_id:
            specs = [s for s in specs if s.id == filter_id]
            if not specs:
                return Verdict(
                    kind=VerdictKind.FAIL, confidence=1.0,
                    output={"pipeline_file": pipeline_file, "load_error": f"未找到 pipeline_id='{filter_id}'", "specs_data": []},
                    diagnosis=f"PipelineSpecLoader: {filter_id} 不在文件中",
                )

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "pipeline_file": pipeline_file,
                "specs_data":    [s.model_dump() for s in specs],
                "pipeline_ids":  [s.id for s in specs],
                "spec_count":    len(specs),
                "load_error":    None,
            },
            diagnosis=f"PipelineSpecLoader: 加载 {len(specs)} 个管线（{pipeline_file}）",
        )


class PipelineStructuralCheckRouter(Router):
    """Pipeline 结构合法性检查（RULE）。

    检查项：no_entry（blocking）/ isolated（degrading）/ dead_end（advisory）/
            cycle（blocking）/ duplicate_edge（advisory）。

    这 5 项是纯拓扑检查，不依赖 Format 定义，最快且最基础。
    任何 blocking 级别 Finding 表示管线无法正确执行。
    """
    DESCRIPTION = (
        "Pipeline 结构合法性检查：no_entry（入口节点存在性）/ isolated（孤立节点）/ "
        "dead_end（悬空终端）/ cycle（非 feedback 边成环）/ duplicate_edge（重复边）。"
        "输出 check_structural 字段，blocking Finding 表示管线无法正确执行。"
    )
    FORMAT_IN  = "diag.pipeline.extracted"
    FORMAT_OUT = "diag.pipeline.check.structural"

    _CHECKS = ["no_entry", "isolated", "dead_end", "cycle", "duplicate_edge"]

    def run(self, input_data: dict) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import run_pipeline_checks

        specs = _load_specs_from_input(input_data)
        all_findings: list[dict] = []

        for spec in specs:
            findings = run_pipeline_checks(spec, enabled=self._CHECKS)
            all_findings.extend(_serialize_findings(findings, spec.id))

        has_blocking  = any(f["level"] == "blocking"  for f in all_findings)
        has_degrading = any(f["level"] == "degrading" for f in all_findings)

        output = dict(input_data)
        output["check_structural"] = {
            "check":      "structural",
            "checks_run": self._CHECKS,
            "passed":     not has_blocking,
            "severity":   "CRITICAL" if has_blocking else "HIGH" if has_degrading else "INFO",
            "findings":   all_findings,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=(
                f"PipelineStructuralCheck: {len(all_findings)} findings "
                f"({'blocking' if has_blocking else 'ok'})"
            ),
        )


class PipelineFormatContractCheckRouter(Router):
    """Pipeline Format 契约检查（RULE）。

    检查项：format_break（blocking）/ composite_missing（degrading）/
            granted_tag_chain（degrading，需 FormatRegistry）。

    这 3 项检查相邻边的 Format 连续性和标签承诺链完整性。
    format_break 是运行时 KeyError 的直接来源；
    granted_tag_chain 是语义假设违约的来源。
    """
    DESCRIPTION = (
        "Pipeline Format 契约检查：format_break（相邻边 Format 断裂，blocking）/ "
        "composite_missing（composite Format 上游覆盖缺失，degrading）/ "
        "granted_tag_chain（required_tags 被上游 tags 静态覆盖，degrading）。"
        "输出 check_format_contract 字段。"
    )
    FORMAT_IN  = "diag.pipeline.extracted"
    FORMAT_OUT = "diag.pipeline.check.format-contract"

    _CHECKS = ["format_break", "composite_missing", "granted_tag_chain"]

    def run(self, input_data: dict) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import run_pipeline_checks

        specs = _load_specs_from_input(input_data)

        # 尝试加载 FormatRegistry（供 composite_missing / granted_tag_chain 使用）
        format_registry = None
        try:
            from omnicompany.core.registry import discover
            from omnicompany.protocol.format import _default_registry  # type: ignore
            discover()
            format_registry = _default_registry
        except Exception:
            pass

        all_findings: list[dict] = []
        for spec in specs:
            findings = run_pipeline_checks(spec, enabled=self._CHECKS, format_registry=format_registry)
            all_findings.extend(_serialize_findings(findings, spec.id))

        has_blocking  = any(f["level"] == "blocking"  for f in all_findings)
        has_degrading = any(f["level"] == "degrading" for f in all_findings)

        output = dict(input_data)
        output["check_format_contract"] = {
            "check":                  "format_contract",
            "checks_run":             self._CHECKS,
            "passed":                 not has_blocking,
            "severity":               "CRITICAL" if has_blocking else "HIGH" if has_degrading else "INFO",
            "format_registry_loaded": format_registry is not None,
            "findings":               all_findings,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=(
                f"PipelineFormatContractCheck: {len(all_findings)} findings "
                f"(registry={'ok' if format_registry else 'unavailable'})"
            ),
        )


class PipelineMaturityCheckRouter(Router):
    """Pipeline 成熟度一致性检查（RULE）— 短板原则。

    检查项：maturity_consistency（degrading）。

    CRYSTALLIZED 节点不应直接依赖 GROWING/HYPOTHETICAL 上游节点。
    若依赖，CRYSTALLIZED 声明具有误导性，实际可靠性受上游制约。
    """
    DESCRIPTION = (
        "Pipeline 成熟度一致性检查（短板原则）：CRYSTALLIZED 节点不应直接依赖 GROWING/HYPOTHETICAL 上游。"
        "违反则 maturity_consistency=degrading，表示 CRYSTALLIZED 声明具有误导性。"
        "输出 check_maturity 字段。"
    )
    FORMAT_IN  = "diag.pipeline.extracted"
    FORMAT_OUT = "diag.pipeline.check.maturity"

    _CHECKS = ["maturity_consistency"]

    def run(self, input_data: dict) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import run_pipeline_checks

        specs = _load_specs_from_input(input_data)
        all_findings: list[dict] = []

        for spec in specs:
            findings = run_pipeline_checks(spec, enabled=self._CHECKS)
            all_findings.extend(_serialize_findings(findings, spec.id))

        has_degrading = any(f["level"] == "degrading" for f in all_findings)

        output = dict(input_data)
        output["check_maturity"] = {
            "check":      "maturity",
            "checks_run": self._CHECKS,
            "passed":     not has_degrading,
            "severity":   "HIGH" if has_degrading else "INFO",
            "findings":   all_findings,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=f"PipelineMaturityCheck: {len(all_findings)} findings",
        )


class PipelineSoftHardCheckRouter(Router):
    """Pipeline P-07 软硬配对检查（RULE）。

    检查项：soft_hard_pairing（degrading）。

    LLM 节点（method=LLM）的直接下游中，应存在至少一个 RULE 或 ANCHOR 节点作为验证器。
    否则 LLM 输出无确定性验证，语义错误将静默传递到下游。
    """
    DESCRIPTION = (
        "P-07 软硬配对检查：LLM 节点（method=LLM）的直接下游应有 RULE 或 ANCHOR 节点作为验证器。"
        "无 HARD 后继则 soft_hard_pairing=degrading，表示 LLM 输出无确定性保障。"
        "输出 check_soft_hard 字段。"
    )
    FORMAT_IN  = "diag.pipeline.extracted"
    FORMAT_OUT = "diag.pipeline.check.soft-hard"

    _CHECKS = ["soft_hard_pairing"]

    def run(self, input_data: dict) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import run_pipeline_checks

        specs = _load_specs_from_input(input_data)
        all_findings: list[dict] = []

        for spec in specs:
            findings = run_pipeline_checks(spec, enabled=self._CHECKS)
            all_findings.extend(_serialize_findings(findings, spec.id))

        has_degrading = any(f["level"] == "degrading" for f in all_findings)

        output = dict(input_data)
        output["check_soft_hard"] = {
            "check":      "soft_hard",
            "checks_run": self._CHECKS,
            "passed":     not has_degrading,
            "severity":   "HIGH" if has_degrading else "INFO",
            "findings":   all_findings,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=f"PipelineSoftHardCheck: {len(all_findings)} findings",
        )


class PipelineTopoHealthWriterRouter(Router):
    """汇总所有 Pipeline 拓扑检查 Finding，计算健康等级，输出健康档案（RULE）。

    从 fan-in 输入中收集 4 个检查器的结果：
      check_structural / check_format_contract / check_maturity / check_soft_hard

    健康等级（与 Finding.level 对齐）：
      PASS — 无任何 Finding
      INFO — 只有 advisory Finding
      WARN — 有 degrading Finding（无 blocking）
      FAIL — 有任意 blocking Finding

    输出 diag.pipeline.health-record 格式的健康档案。
    """
    DESCRIPTION = (
        "Pipeline 拓扑诊断汇总：从 5 个并行检查器（structural/format_contract/maturity/soft_hard/narrative）"
        "的 fan-in 输入中收集所有 Finding，计算健康等级（PASS/INFO/WARN/FAIL），输出健康档案。"
        "blocking → FAIL，degrading → WARN，advisory-only → INFO，无 Finding → PASS。"
    )
    FORMAT_IN  = "diag.pipeline.checks"
    FORMAT_OUT = "diag.pipeline.health-record"

    _KNOWN_CHECK_KEYS = [
        "check_structural",
        "check_format_contract",
        "check_maturity",
        "check_soft_hard",
        "check_narrative",
    ]

    def run(self, input_data: dict) -> Verdict:
        pipeline_file = input_data.get("pipeline_file", "")
        pipeline_ids  = input_data.get("pipeline_ids", [])

        # 收集所有检查器输出中的 findings
        all_findings: list[dict] = []
        checks_summary: list[dict] = []
        for key in self._KNOWN_CHECK_KEYS:
            check = input_data.get(key)
            if check:
                checks_summary.append({
                    "check":    check.get("check", key),
                    "passed":   check.get("passed", True),
                    "severity": check.get("severity", "INFO"),
                    "count":    len(check.get("findings", [])),
                })
                all_findings.extend(check.get("findings", []))

        has_blocking  = any(f.get("level") == "blocking"  for f in all_findings)
        has_degrading = any(f.get("level") == "degrading" for f in all_findings)
        has_advisory  = any(f.get("level") == "advisory"  for f in all_findings)

        if has_blocking:
            grade = "FAIL"
        elif has_degrading:
            grade = "WARN"
        elif has_advisory:
            grade = "INFO"
        else:
            grade = "PASS"

        # 按 pipeline 分组统计
        per_pipeline: dict[str, list[dict]] = {}
        for f in all_findings:
            pid = f.get("pipeline_id", "unknown")
            per_pipeline.setdefault(pid, []).append(f)

        per_pipeline_summary = {
            pid: {
                "finding_count": len(flist),
                "has_blocking":  any(f.get("level") == "blocking"  for f in flist),
                "has_degrading": any(f.get("level") == "degrading" for f in flist),
                "has_advisory":  any(f.get("level") == "advisory"  for f in flist),
            }
            for pid, flist in per_pipeline.items()
        }

        # 按 level 优先级排序
        _LEVEL_ORDER = {"blocking": 0, "degrading": 1, "advisory": 2, "info": 3}
        sorted_findings = sorted(
            all_findings,
            key=lambda f: (_LEVEL_ORDER.get(f.get("level", "info"), 9),
                           f.get("pipeline_id", ""),
                           f.get("check_id", "")),
        )

        summary = (
            f"Pipeline 拓扑检查 {len(pipeline_ids)} 个管线，"
            f"共 {len(all_findings)} 个 Finding。"
            f"{'有 blocking 问题（FAIL）' if has_blocking else '有 degrading 问题（WARN）' if has_degrading else '有 advisory 建议（INFO）' if has_advisory else '全部健康（PASS）'}"
        )

        health_record = {
            "pipeline_file":    pipeline_file,
            "pipeline_ids":     pipeline_ids,
            "health_grade":     grade,
            "total_findings":   len(all_findings),
            "findings":         sorted_findings,
            "per_pipeline":     per_pipeline_summary,
            "checks_summary":   checks_summary,
            "summary":          summary,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=health_record,
            diagnosis=f"PipelineTopoHealthWriter: {grade} ({len(all_findings)} findings)",
        )


class PipelineNarrativeCheckerRouter(Router):
    """L4 整管线语义连贯性审计（LLM）。

    向 LLM 提供：
    ① 管线 purpose（来自 PipelineManifest 或 PipelineSpec.purpose）
    ② 完整 Format 链：每条边的 format_in → format_out（格式 ID + 描述）
    ③ 所有节点 DESCRIPTION（按执行顺序）
    ④ design_rationale（来自 PipelineManifest，若存在）

    审计维度：
    - 叙事连贯性：从入口到出口，每一步是否有清晰的信息增量？
    - 语义跳跃：哪条边两侧的信息差距过大，难以解释？
    - 意图对齐：整体结构是否服务了 purpose 声明的业务目标？
    - 节点单一性：哪个节点做了超出其 DESCRIPTION 声明的事情？

    LLM 失败时降级为 SKIP（passed=None），不阻断管线。
    """

    DESCRIPTION = (
        "L4 整管线叙事审计（LLM）：给定完整 Format 链 + 所有节点 DESCRIPTION + purpose/design_rationale，"
        "评估叙事连贯性（信息增量是否可解释）、语义跳跃（哪条边信息差距过大）、意图对齐（结构是否服务业务目标）。"
        "LLM 失败时降级为 SKIP（passed=None），不阻断管线。"
        "输出 check_narrative 字段，advisory 级别 Finding。"
    )
    FORMAT_IN  = "diag.pipeline.extracted"
    FORMAT_OUT = "diag.pipeline.check.narrative"

    _MODEL = "qwen3.6-plus"

    _SYSTEM = """\
你是 OmniCompany 管线叙事审计员。你将获得一条管线的完整语义信息。
你的任务：评估这条管线的叙事连贯性，找出语义问题，产出结构化 JSON。

## 审计重点

1. **叙事连贯性**：从入口到出口，每一步节点是否有清晰的信息增量？
   - 每个节点消费什么 → 产出什么 → 下游用它做什么？
   - 信息流是否自然流动，还是存在不必要的数据来回传递？

2. **语义跳跃**：某条边两侧的信息差距是否过大，难以用节点 DESCRIPTION 解释？
   - 格式突变（如：原始 CSV → 完整 Schema，中间是否遗漏了关键步骤？）
   - 信息增量不可解释（节点声称做一件事，但实际上必须做更多）

3. **意图对齐**：整体结构是否服务了 purpose 声明的业务目标？
   - 是否有节点完全游离于 purpose 之外？
   - purpose 声明的关键输出，是否真的由终端节点产出？

4. **节点单一性**：某个节点的 DESCRIPTION 是否承担了过多职责？

## 输出格式

严格输出合法 JSON（不要 markdown 代码块）：

{
  "narrative_coherent": true/false,
  "has_semantic_jump": true/false,
  "semantic_jump_locations": ["edge:node_a→node_b（跳跃描述）"],
  "purpose_aligned": true/false,
  "purpose_alignment_notes": "意图对齐分析",
  "violation_nodes": ["node_id（问题描述）"],
  "overall_grade": "A/B/C/D",
  "key_findings": ["最关键的 1-3 条发现，具体有针对性"],
  "improvement_suggestions": ["具体改进建议"],
  "summary": "一句话总结（中文，≤50字）"
}

评级标准：
- A: 叙事连贯 + 无语义跳跃 + 意图对齐
- B: 基本连贯，有轻微跳跃或局部不对齐
- C: 存在明显语义跳跃 OR 部分游离于 purpose
- D: 叙事断裂 OR 结构完全不服务声明意图
"""

    def __init__(self, model: str | None = None):
        self._model = model or self._MODEL

    def run(self, input_data: dict) -> Verdict:
        specs_data: list[dict] = input_data.get("specs_data", [])
        pipeline_file: str = input_data.get("pipeline_file", "")

        if not specs_data:
            output = dict(input_data)
            output["check_narrative"] = {
                "check": "narrative",
                "passed": None,
                "severity": "INFO",
                "detail": "无 PipelineSpec 数据，跳过叙事审计",
                "findings": [],
            }
            return Verdict(kind=VerdictKind.PASS, confidence=1.0, output=output,
                           diagnosis="PipelineNarrativeChecker: 无数据，跳过")

        # 构建用户消息（逐个管线）
        all_audit_results: list[dict] = []
        for spec_data in specs_data:
            audit = self._audit_spec(spec_data, pipeline_file)
            all_audit_results.append(audit)

        # 汇总
        any_fail = any(r.get("overall_grade") in ("C", "D") for r in all_audit_results)
        findings = []
        for r in all_audit_results:
            grade = r.get("overall_grade", "?")
            pid = r.get("pipeline_id", "unknown")
            if r.get("has_semantic_jump"):
                for loc in r.get("semantic_jump_locations", []):
                    findings.append({
                        "pipeline_id": pid,
                        "check_id": "narrative_semantic_jump",
                        "level": "advisory",
                        "location": loc,
                        "observation": f"语义跳跃：{loc}",
                    })
            if not r.get("purpose_aligned"):
                findings.append({
                    "pipeline_id": pid,
                    "check_id": "narrative_purpose_misalign",
                    "level": "advisory",
                    "location": f"pipeline:{pid}",
                    "observation": f"意图不对齐：{r.get('purpose_alignment_notes', '')}",
                })
            for vn in r.get("violation_nodes", []):
                findings.append({
                    "pipeline_id": pid,
                    "check_id": "narrative_node_overload",
                    "level": "advisory",
                    "location": f"node:{vn.split('（')[0]}",
                    "observation": f"节点职责过重：{vn}",
                })

        output = dict(input_data)
        output["check_narrative"] = {
            "check": "narrative",
            "passed": not any_fail,
            "severity": "MEDIUM" if any_fail else "INFO",
            "audit_results": all_audit_results,
            "findings": findings,
        }
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=f"PipelineNarrativeChecker: {len(all_audit_results)} pipelines, {len(findings)} findings",
        )

    def _audit_spec(self, spec_data: dict, pipeline_file: str) -> dict:
        """对单个 PipelineSpec 执行叙事审计。"""
        try:
            from omnicompany.protocol.pipeline import PipelineSpec
            spec = PipelineSpec.model_validate(spec_data)
        except Exception:
            return {"pipeline_id": spec_data.get("id", "?"), "overall_grade": "?",
                    "error": "spec 反序列化失败"}

        # 尝试加载 manifest（获取 purpose / design_rationale）
        manifest = None
        try:
            from omnicompany.protocol.manifest import load_manifest
            manifest = load_manifest(pipeline_file)
        except Exception:
            pass

        purpose = (
            (manifest.purpose if manifest else None)
            or spec.purpose
            or spec.description
            or ""
        )
        design_rationale = (manifest.design_rationale if manifest else "") or ""

        # 构建 Format 链描述
        node_map = {n.id: n for n in spec.nodes}
        format_chain_lines: list[str] = []
        for edge in spec.edges:
            src = node_map.get(edge.source)
            tgt = node_map.get(edge.target)
            if not src or not tgt:
                continue
            try:
                src_fmt = src.format_out
                tgt_fmt = tgt.format_in
            except Exception:
                continue
            cond = f"[{edge.condition.value}]" if edge.condition else ""
            fb = "[feedback]" if edge.feedback else ""
            format_chain_lines.append(
                f"  {edge.source}({src_fmt}) →{cond}{fb} {edge.target}({tgt_fmt})"
            )

        # 节点 DESCRIPTION 列表（按执行顺序，从 entry 开始 BFS）
        node_descs: list[str] = []
        visited: set[str] = set()
        queue = [spec.entry]
        out_edges_map: dict[str, list[str]] = {}
        for e in spec.edges:
            out_edges_map.setdefault(e.source, []).append(e.target)
        while queue:
            nid = queue.pop(0)
            if nid in visited:
                continue
            visited.add(nid)
            node = node_map.get(nid)
            if node:
                desc = ""
                if node.anchor:
                    desc = node.anchor.validator.description if node.anchor.validator else ""
                elif node.transformer:
                    desc = node.transformer.description or ""
                node_descs.append(f"  [{nid}] {desc[:120]}")
            for nxt in out_edges_map.get(nid, []):
                if nxt not in visited:
                    queue.append(nxt)

        user_msg = (
            f"## 管线 ID: {spec.id}\n\n"
            f"## Purpose（业务目标）\n{purpose or '（未声明）'}\n\n"
            + (f"## Design Rationale（设计理由）\n{design_rationale}\n\n" if design_rationale else "")
            + "## Format 链（边级别）\n" + "\n".join(format_chain_lines) + "\n\n"
            + "## 节点 DESCRIPTION（执行顺序）\n" + "\n".join(node_descs) + "\n"
        )

        try:
            from omnicompany.llm.client import LLMClient
            client = LLMClient(model=self._model)
            response = client.call(
                system=self._SYSTEM,
                user=user_msg,
            )
            import json as _json
            audit_data = _json.loads(response)
            audit_data["pipeline_id"] = spec.id
            return audit_data
        except Exception as exc:
            return {
                "pipeline_id": spec.id,
                "overall_grade": "?",
                "narrative_coherent": None,
                "has_semantic_jump": False,
                "semantic_jump_locations": [],
                "purpose_aligned": None,
                "purpose_alignment_notes": "",
                "violation_nodes": [],
                "key_findings": [],
                "improvement_suggestions": [],
                "summary": f"LLM 审计失败（{type(exc).__name__}）",
                "error": str(exc),
            }
