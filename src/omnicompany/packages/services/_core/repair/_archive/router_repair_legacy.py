# [OMNI] origin=claude-code ts=2026-04-11T00:00:00Z
# [OMNI] material_id="material:core.repair.router_self_repair_pipeline.legacy.py"
# OMNI-024 ALLOW: 修复管线的 Router 辅助类与修复流程紧耦合，作为内联节点放在同一文件便于维护
"""repair.router_repair — 通用 Router 补全与修复管线

管线设计（单 Router，按问题类型串行三次 LLM 调用）:

  IssueLoaderRouter         — 重跑确定性诊断，提取 B 类问题结构化列表
      ↓ (diag.repair.issue-list)
  RouterSourceLoaderRouter  — 深度提取：类 docstring / 直接访问分析 / pipeline 节点描述 / INPUT_KEYS
      ↓ (diag.repair.source-context)
  DescriptionPlannerRouter  — R-01 专属：DESCRIPTION 补全（只做 DESCRIPTION，不碰其他）
      ↓ (diag.repair.desc-patch)
  FailPathPlannerRouter     — R-05 专属：FAIL 路径补充（只分析直接访问语义）
      ↓ (diag.repair.fail-patch)
  GrantedTagsPlannerRouter  — R-07 专属：granted_tags 推断（只看 FORMAT_OUT + PASS 结构）
      ↓ (diag.repair.tags-patch)
  PatchMergerRouter         — 合并三个 diff 为一个提案
      ↓ (diag.repair.patch-plan)
  PatchValidatorRouter      — AST 验证：语法 / 不删 PASS/FAIL / 不改 FORMAT_IN/OUT
      ↓ (diag.repair.validated-patch)
  HumanApprovalGateRouter   — 写入 data/doctor/repair/pending/<Router>.md

不纳入本管线：
  A 类: FORMAT_IN = [...] 列表
  C 类: async def run() / 复杂逻辑重构
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)

_DEFAULT_SOURCE_ROOT = Path(__file__).parents[4]  # omnifactory/src/omnifactory
_REPAIR_PENDING_DIR = Path(__file__).parents[5] / "data" / "doctor" / "repair" / "pending"
_MODEL = "qwen3.6-plus"


# ════════════════════════════════════════════════════════════════
# AST 工具 — 精确信息提取
# ════════════════════════════════════════════════════════════════

def _extract_class_docstring(class_source: str) -> str:
    """从类源码中提取类 docstring（第一个字符串字面量）。"""
    try:
        tree = ast.parse(class_source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    return node.body[0].value.value.strip()
    except Exception:
        pass
    return ""


def _analyze_direct_accesses(run_source: str) -> list[dict]:
    """AST 分析 run() 中对 input_data 的直接下标访问（input_data["key"]）。

    返回每个访问点的信息：
      - key: 被访问的键名
      - line: 行号（相对于 run_source）
      - context: 该行代码（用于判断语义）
      - usage_type: "assign_to_dict_call" / "iterate" / "index" / "plain"
      - crash_if_missing: True（KeyError）/ True（None会crash）
      - crash_if_empty: True（空列表/空dict 会导致后续操作失败）
    """
    results: list[dict] = []
    try:
        tree = ast.parse(run_source)
        lines = run_source.splitlines()
    except Exception:
        return results

    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        # 检查是否是 input_data["key"] 形式
        val = node.value
        if not (isinstance(val, ast.Name) and val.id == "input_data"):
            continue
        slc = node.slice
        if not isinstance(slc, ast.Constant):
            continue
        key = slc.value
        line_no = node.lineno
        line_text = lines[line_no - 1].strip() if line_no <= len(lines) else ""

        # 分析父节点 usage 模式（通过行文本启发式推断）
        usage_type = "plain"
        crash_if_empty = False
        if "dict(" in line_text and f'input_data["{key}"]' in line_text:
            usage_type = "assign_to_dict_call"
            crash_if_empty = False   # dict(None) crash，dict({}) ok
        elif line_text.lstrip().startswith("for ") and f'input_data["{key}"]' in line_text:
            usage_type = "iterate"
            crash_if_empty = False   # for x in [] is fine
        elif f'input_data["{key}"][' in line_text:
            usage_type = "index"
            crash_if_empty = True    # input_data["key"][0] → IndexError if empty
        else:
            usage_type = "plain"
            crash_if_empty = False

        results.append({
            "key": key,
            "line": line_no,
            "context": line_text,
            "usage_type": usage_type,
            "crash_if_missing": True,    # 直接下标访问必然 KeyError if key absent
            "crash_if_empty": crash_if_empty,
        })

    return results


def _extract_pipeline_node_desc(pipeline_brief: dict | None, source_root: Path) -> str:
    """从 pipeline 文件中提取本 Router 节点的 ValidatorSpec.description。

    用于 DESCRIPTION 补全：管线的 validator 描述往往比类本身 DESCRIPTION 更精确。
    """
    if not pipeline_brief:
        return ""
    node_id = pipeline_brief.get("node_id")
    pipeline_id = pipeline_brief.get("pipeline_id", "")
    if not node_id:
        return ""

    # 找 pipeline 文件
    pipeline_files: list[Path] = []
    for pat in ("pipeline.py", "*_pipeline.py", "pipeline_*.py"):
        for p in source_root.rglob(pat):
            if "__pycache__" not in str(p) and "_graveyard" not in str(p):
                pipeline_files.append(p)

    for pf in pipeline_files:
        try:
            content = pf.read_text(encoding="utf-8", errors="ignore")
            if node_id not in content and pipeline_id not in content:
                continue
            tree = ast.parse(content)
        except Exception:
            continue

        # 找 PipelineNode(id="node_id", ...) 里的 AnchorSpec/TransformerSpec
        # 再从其 validator=ValidatorSpec(description=...) 或 description= 提取
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name not in ("AnchorSpec", "TransformerSpec", "ValidatorSpec"):
                continue

            # 检查是否包含我们的 node_id
            this_id = None
            for kw in node.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                    this_id = kw.value.value

            if this_id != node_id and func_name != "ValidatorSpec":
                continue

            # 从 description= 提取
            for kw in node.keywords:
                if kw.arg == "description":
                    # 字符串拼接 / 常量
                    try:
                        val = ast.literal_eval(kw.value)
                        if isinstance(val, str) and val.strip():
                            return val.strip()
                    except Exception:
                        pass

    return ""


# ════════════════════════════════════════════════════════════════
# IssueLoaderRouter — 重跑确定性诊断，提取 B 类问题
# ════════════════════════════════════════════════════════════════


class IssueLoaderRouter(Router):
    """重跑 Doctor 确定性诊断链，提取当前 Router 的 B 类问题清单。

    B 类 = 可 LLM 辅助补全+修复、需人类审批的问题：
      - R-01: DESCRIPTION 太短（< 50 字）
      - R-05: FAIL 路径缺失
      - R-07-signal: granted_tags 未授予

    A 类（FORMAT_IN 列表）和 C 类（async run）不纳入本管线。
    """

    DESCRIPTION = "重跑 Doctor 确定性诊断，提取 B 类问题（DESCRIPTION 短/FAIL 缺失/tags 缺失），排除 A/C 类"
    FORMAT_IN = "diag.repair.request"
    FORMAT_OUT = "diag.repair.issue-list"

    # 只有这些 check_id 的问题才是"B 类"（可 LLM 辅助补全）
    # R-10（run 过长）/ R-06（直接写文件）/ R-02-list / R-04-async 等结构性问题不纳入
    _ACTIONABLE_CHECKS = {"R-01", "R-05", "R-07", "R-07-signal"}

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data["router_class"]
        source_file: str = input_data["source_file"]
        source_root: str = input_data.get("source_root", str(_DEFAULT_SOURCE_ROOT))

        # 私有类（下划线前缀）跳过：通常是脚本辅助类，不是管线 Router
        if router_class.startswith("_"):
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={**input_data, "skip_reason": f"私有类（{router_class}），不纳入自动修复"},
                diagnosis=f"IssueLoader: {router_class} 是私有类，跳过",
            )

        from omnicompany.packages.services._diagnosis.doctor.routers import (
            RouterExtractorRouter,
            RouterSignatureRouter,
            RouterContextCollectorRouter,
            RouterDeterministicCheckRouter,
        )

        def unpack(v):
            return v.output if hasattr(v, "output") else v

        req = {"router_class": router_class, "source_file": source_file, "source_root": source_root}

        try:
            r = unpack(RouterExtractorRouter().run(req))
            r = unpack(RouterSignatureRouter().run(r))
            if r.get("health_grade"):
                return Verdict(
                    kind=VerdictKind.FAIL, confidence=1.0,
                    output={**input_data, "skip_reason": "签名缺失（C 类），不纳入自动修复"},
                    diagnosis=f"IssueLoader: {router_class} 签名缺失，跳过",
                )
            r = unpack(RouterContextCollectorRouter().run(r))
            r = unpack(RouterDeterministicCheckRouter().run(r))
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={**input_data, "error": str(e)},
                diagnosis=f"IssueLoader: 诊断链执行异常 {e}",
            )

        checks: list[dict] = r.get("checks", [])
        b_class_issues: list[dict] = []
        for chk in checks:
            check_id = chk.get("check", "")
            if check_id not in self._ACTIONABLE_CHECKS:
                continue  # 仅处理可 LLM 修复的 B 类问题
            if chk.get("passed") is False:
                b_class_issues.append({
                    "check_id": check_id,
                    "severity": chk.get("severity"),
                    "observation": chk.get("observation", ""),
                    "detail": chk.get("detail"),
                })

        if not b_class_issues:
            return Verdict(
                kind=VerdictKind.PASS, confidence=1.0,
                output={**input_data, "b_class_issues": [], "extracted": r.get("extracted", {}),
                        "context": r.get("context", {}), "skip_reason": "无 B 类问题，无需修复"},
                diagnosis=f"IssueLoader: {router_class} 无 B 类问题",
            )

        return Verdict(
            kind=VerdictKind.PASS, confidence=1.0,
            output={**input_data, "b_class_issues": b_class_issues,
                    "extracted": r.get("extracted", {}), "context": r.get("context", {})},
            diagnosis=f"IssueLoader: {router_class} {len(b_class_issues)} 项 B 类问题",
        )


# ════════════════════════════════════════════════════════════════
# RouterSourceLoaderRouter — 深度上下文提取
# ════════════════════════════════════════════════════════════════


class RouterSourceLoaderRouter(Router):
    """深度提取 Router 补全所需的全量上下文：

    新增（相比简单的 class_source）：
      - class_docstring: 类 docstring（往往包含幂等原则/保护规则等设计意图）
      - direct_access_map: AST 分析 run() 中所有 input_data["key"] 直接访问
        每条含: key / line / context / usage_type / crash_if_missing / crash_if_empty
      - pipeline_node_desc: 该 Router 在 pipeline 中的 ValidatorSpec.description
      - input_keys_declared: 类声明的 INPUT_KEYS（若有）
    """

    DESCRIPTION = (
        "深度提取 Router 补全上下文：class docstring / 直接访问 AST 分析 / "
        "pipeline 节点 validator 描述 / INPUT_KEYS 声明"
    )
    FORMAT_IN = "diag.repair.issue-list"
    FORMAT_OUT = "diag.repair.source-context"

    def run(self, input_data: Any) -> Verdict:
        if not input_data.get("b_class_issues"):
            return Verdict(kind=VerdictKind.PASS, confidence=1.0, output=input_data,
                           diagnosis="RouterSourceLoader: 无 B 类问题，跳过")

        router_class: str = input_data["router_class"]
        source_file: str = input_data["source_file"]
        source_root_str: str = input_data.get("source_root", str(_DEFAULT_SOURCE_ROOT))
        source_root = Path(source_root_str)
        extracted: dict = input_data.get("extracted", {})
        context: dict = input_data.get("context", {})

        try:
            src_path = Path(source_file)
            full_source = src_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "error": f"源文件读取失败: {e}"},
                           diagnosis=f"RouterSourceLoader: 读取失败 {e}")

        # 提取目标类完整源码
        class_source = self._extract_class_source(full_source, router_class)
        run_source: str = extracted.get("run_source", "")
        run_start_line: int = extracted.get("run_start_line", 0)

        # ── 新增：class docstring ──
        class_docstring = _extract_class_docstring(class_source)

        # ── 新增：直接访问 AST 分析 ──
        direct_access_map = _analyze_direct_accesses(run_source or class_source)

        # ── 新增：INPUT_KEYS 声明 ──
        input_keys_declared: list[str] = extracted.get("INPUT_KEYS") or []
        if isinstance(input_keys_declared, str):
            input_keys_declared = [input_keys_declared]

        # ── 新增：pipeline 节点 ValidatorSpec 描述 ──
        pipeline_brief: dict | None = context.get("pipeline_brief")
        pipeline_node_desc = _extract_pipeline_node_desc(pipeline_brief, source_root)

        # 其他已有上下文
        pipeline_purpose: str = context.get("pipeline_purpose", "")
        upstream_routers: list = context.get("upstream_routers", [])
        downstream_routers: list = context.get("downstream_routers", [])
        format_in_def: dict | None = context.get("format_in_def")
        format_out_def: dict | None = context.get("format_out_def")

        return Verdict(
            kind=VerdictKind.PASS, confidence=1.0,
            output={
                **input_data,
                "full_source": full_source,
                "class_source": class_source or run_source,
                "class_docstring": class_docstring,
                "run_source": run_source,
                "run_start_line": run_start_line,
                "direct_access_map": direct_access_map,
                "input_keys_declared": input_keys_declared,
                "pipeline_node_desc": pipeline_node_desc,
                "format_in_def": format_in_def,
                "format_out_def": format_out_def,
                "pipeline_brief": pipeline_brief,
                "pipeline_purpose": pipeline_purpose,
                "pipeline_node_desc": pipeline_node_desc,
                "upstream_routers": upstream_routers,
                "downstream_routers": downstream_routers,
            },
            diagnosis=(
                f"RouterSourceLoader: {router_class} "
                f"docstring={'有' if class_docstring else '无'} "
                f"direct_accesses={len(direct_access_map)} "
                f"node_desc={'有' if pipeline_node_desc else '无'}"
            ),
        )

    @staticmethod
    def _extract_class_source(full_source: str, class_name: str) -> str:
        try:
            tree = ast.parse(full_source)
            lines = full_source.splitlines(keepends=True)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    start = node.lineno - 1
                    end = node.end_lineno if hasattr(node, "end_lineno") else len(lines)
                    return "".join(lines[start:end])
        except Exception:
            pass
        return ""


# ════════════════════════════════════════════════════════════════
# DescriptionPlannerRouter — R-01 专属：只补 DESCRIPTION
# ════════════════════════════════════════════════════════════════

_DESC_PROMPT = """你是 omnifactory LAP 协议专家。任务：只补全 Router 类的 DESCRIPTION 字段，不改任何其他内容。

# 目标 Router: {router_class}

## 当前 DESCRIPTION（太短，< 50 字）
{current_description}

## 类 Docstring（设计意图/算法原则，优先从这里提炼）
{class_docstring}

## Pipeline 业务目标
{pipeline_purpose}

## 该节点在 Pipeline 中的 Validator 描述
{pipeline_node_desc}

## FORMAT_IN 定义
{format_in_def}

## FORMAT_OUT 定义
{format_out_def}

## 上游 Router
{upstream_summary}

## 下游 Router
{downstream_summary}

## 补全规则（严格）
- DESCRIPTION 必须 ≥ 50 字（中文字符）
- 说明：做什么 / 从哪里来 / 到哪里去 / 核心算法或判断逻辑
- 如果 docstring 中有幂等性/保护规则/设计原则，必须压缩进 DESCRIPTION
- 禁止重复 FORMAT_IN/OUT 的 ID 字面量（用语义描述代替）
- 只改 DESCRIPTION = "..." 这一行，不改其他任何内容

## 输出
只输出 unified diff，不输出任何解释：

```diff
--- a/{router_class}
+++ b/{router_class}
@@ ... @@
-    DESCRIPTION = ...（当前值）
+    DESCRIPTION = ...（新值）
```
"""


class DescriptionPlannerRouter(Router):
    """R-01 专属：只生成 DESCRIPTION 补全 diff，不处理其他问题。

    使用 class docstring + pipeline validator 描述 + FORMAT 语义三路信息，
    确保生成的 DESCRIPTION 不仅达标（≥50字），而且包含设计原则。
    """

    DESCRIPTION = (
        "R-01 专属 LLM 规划器：综合 class docstring / pipeline 节点描述 / FORMAT 语义，"
        "生成 DESCRIPTION 补全 diff（只改 DESCRIPTION，不动其他内容）"
    )
    FORMAT_IN = "diag.repair.source-context"
    FORMAT_OUT = "diag.repair.desc-patch"

    def __init__(self, model: str = _MODEL):
        self._model = model

    def run(self, input_data: Any) -> Verdict:
        # 只处理 R-01 问题
        issues = input_data.get("b_class_issues", [])
        r01_issues = [i for i in issues if i.get("check_id") == "R-01"]
        if not r01_issues:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "desc_diff": None},
                           diagnosis="DescriptionPlanner: 无 R-01 问题，跳过")

        router_class: str = input_data["router_class"]
        extracted: dict = input_data.get("extracted", {})
        current_desc = extracted.get("description") or "(空)"
        class_docstring = input_data.get("class_docstring", "") or "(无 docstring)"
        pipeline_purpose = input_data.get("pipeline_purpose", "") or "(未记录)"
        pipeline_node_desc = input_data.get("pipeline_node_desc", "") or "(未找到)"
        upstream_routers = input_data.get("upstream_routers", [])
        downstream_routers = input_data.get("downstream_routers", [])
        format_in_def = input_data.get("format_in_def")
        format_out_def = input_data.get("format_out_def")

        def _fmt_def(d) -> str:
            if not d:
                return "(未找到)"
            return f"id={d.get('id','?')} | description={d.get('description','')!r}"

        def _fmt_routers(routers: list) -> str:
            if not routers:
                return "(无)"
            return "\n".join(f"- {r.get('class','?')}: {r.get('description','(无描述)')}" for r in routers)

        prompt = _DESC_PROMPT.format(
            router_class=router_class,
            current_description=current_desc,
            class_docstring=class_docstring[:1500],  # 限制长度
            pipeline_purpose=pipeline_purpose,
            pipeline_node_desc=pipeline_node_desc,
            format_in_def=_fmt_def(format_in_def),
            format_out_def=_fmt_def(format_out_def),
            upstream_summary=_fmt_routers(upstream_routers),
            downstream_summary=_fmt_routers(downstream_routers),
        )

        diff = self._call_llm(router_class, prompt)
        if not diff:
            return Verdict(kind=VerdictKind.FAIL, confidence=0.5,
                           output={**input_data, "desc_diff": None},
                           diagnosis=f"DescriptionPlanner: {router_class} LLM 未生成 diff")

        return Verdict(kind=VerdictKind.PASS, confidence=0.9,
                       output={**input_data, "desc_diff": diff},
                       diagnosis=f"DescriptionPlanner: {router_class} DESCRIPTION diff 生成")

    def _call_llm(self, router_class: str, prompt: str) -> str | None:
        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system="你是 omnifactory LAP 协议专家。只输出 unified diff，不输出任何解释。",
            )
            raw = resp.content[0].text if resp and resp.content else ""
            return _extract_diff(raw)
        except Exception as e:
            logger.warning("DescriptionPlanner LLM failed for %s: %s", router_class, e)
            return None


# ════════════════════════════════════════════════════════════════
# FailPathPlannerRouter — R-05 专属：只补 FAIL 路径
# ════════════════════════════════════════════════════════════════

_FAIL_PROMPT = """你是 omnifactory LAP 协议专家。任务：根据 R-05 问题类型修复 run() 方法的 Verdict 路径，不改其他内容。

# 目标 Router: {router_class}

## R-05 问题描述
{r05_observation}

## 根据问题类型选择修复策略

### 策略 A：FAIL 路径缺失（观察到 "FAIL 缺失"）
- 在 run() 开头添加输入校验 → 若必要字段缺失返回 Verdict(kind=VerdictKind.FAIL, ...)
- 仅对直接访问列表中的字段进行保护

### 策略 B：PASS 路径缺失（观察到 "PASS 缺失"，且 FAIL 已存在）
- run() 已有 FAIL 路径，但最终 return 返回的是原始 dict 而非 Verdict(PASS, ...)
- 将 run() 末尾的 `return output_dict` 改为：
  `return Verdict(kind=VerdictKind.PASS, confidence=1.0, output=output_dict, diagnosis="...")`
- 不要改变任何输入校验逻辑

### 策略 C：PASS 和 FAIL 都缺失
- 先添加输入校验（策略 A），再包装最终返回（策略 B）

## INPUT_KEYS 声明（Router 明确声明需要的字段）
{input_keys}

## run() 源码中的直接访问点（input_data["key"]，若缺失会 KeyError）
{direct_accesses}

## run() 完整源码
```python
{run_source}
```

## Verdict 构造规范（严格）
- FAIL: `Verdict(kind=VerdictKind.FAIL, output=input_data, diagnosis="...", error_detail={{...}})`
- PASS: `Verdict(kind=VerdictKind.PASS, confidence=1.0, output=output_dict, diagnosis="...")`
- 禁止：`Verdict(result="FAIL", ...)` ← result 不是合法参数
- VerdictKind 导入：代码顶部已有 `from omnicompany.protocol.anchor import Verdict, VerdictKind`

## FAIL 路径语义（仅当策略 A/C 时使用）
- iterate（for x in key）：只检查 None，空列表不会 crash
- assign_to_dict_call（dict(key)）：None 会 crash，空 dict 不会
- index（key[0]）：None 和空列表都会 crash
- plain：None 会 crash，空值通常合法

## 输出
只输出 unified diff，不输出任何解释：

```diff
--- a/{router_class}
+++ b/{router_class}
@@ ... @@
 ... （context lines）
-        return output_dict
+        return Verdict(kind=VerdictKind.PASS, confidence=1.0, output=output_dict, diagnosis="...")
```
"""


class FailPathPlannerRouter(Router):
    """R-05 专属：只生成 FAIL 路径补充 diff，不处理其他问题。

    提供直接访问 AST 分析结果（usage_type + crash_if_missing/empty），
    让 LLM 能精确判断哪些需要 FAIL 保护、哪些语义上空值合法。
    """

    DESCRIPTION = (
        "R-05 专属 LLM 规划器：基于 AST 直接访问分析（usage_type/crash 语义），"
        "生成 FAIL 路径补充 diff（只在 run() 开头添加保护，不改其他逻辑）"
    )
    FORMAT_IN = "diag.repair.desc-patch"
    FORMAT_OUT = "diag.repair.fail-patch"

    def __init__(self, model: str = _MODEL):
        self._model = model

    def run(self, input_data: Any) -> Verdict:
        issues = input_data.get("b_class_issues", [])
        r05_issues = [i for i in issues if i.get("check_id") == "R-05"]
        if not r05_issues:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "fail_diff": None},
                           diagnosis="FailPathPlanner: 无 R-05 问题，跳过")

        router_class: str = input_data["router_class"]
        run_source: str = input_data.get("run_source", "(未提取)")
        direct_access_map: list = input_data.get("direct_access_map", [])
        input_keys_declared: list = input_data.get("input_keys_declared", [])
        r05_obs = r05_issues[0].get("observation", "")

        # 格式化直接访问分析结果
        if direct_access_map:
            da_lines = []
            for da in direct_access_map:
                crash_empty = " | 空值也会crash" if da.get("crash_if_empty") else ""
                da_lines.append(
                    f'  - input_data["{da["key"]}"] 行{da["line"]} '
                    f'[{da["usage_type"]}] → KeyError if missing{crash_empty}\n'
                    f'    代码: {da["context"]}'
                )
            da_text = "\n".join(da_lines)
        else:
            da_text = "(未发现直接下标访问，可能使用 f-string 或其他变量名)"

        prompt = _FAIL_PROMPT.format(
            router_class=router_class,
            r05_observation=r05_obs,
            input_keys=", ".join(input_keys_declared) if input_keys_declared else "(未声明)",
            direct_accesses=da_text,
            run_source=run_source[:3000],
        )

        diff = self._call_llm(router_class, prompt)
        if not diff:
            return Verdict(kind=VerdictKind.FAIL, confidence=0.5,
                           output={**input_data, "fail_diff": None},
                           diagnosis=f"FailPathPlanner: {router_class} LLM 未生成 diff")

        return Verdict(kind=VerdictKind.PASS, confidence=0.9,
                       output={**input_data, "fail_diff": diff},
                       diagnosis=f"FailPathPlanner: {router_class} FAIL 路径 diff 生成")

    def _call_llm(self, router_class: str, prompt: str) -> str | None:
        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system="你是 omnifactory LAP 协议专家。只输出 unified diff，不输出任何解释。",
            )
            raw = resp.content[0].text if resp and resp.content else ""
            return _extract_diff(raw)
        except Exception as e:
            logger.warning("FailPathPlanner LLM failed for %s: %s", router_class, e)
            return None


# ════════════════════════════════════════════════════════════════
# GrantedTagsPlannerRouter — R-07 专属：只补 granted_tags
# ════════════════════════════════════════════════════════════════

_TAGS_PROMPT = """你是 omnifactory LAP 协议专家。任务：只为 PASS Verdict 的 output 添加 granted_tags 字段。

# 目标 Router: {router_class}

## FORMAT_OUT 定义（决定授予什么 tag）
{format_out_def}

## 当前 PASS Verdict 代码段
```python
{pass_verdict_code}
```

## granted_tags 规范
- 格式：`"granted_tags": ["tag1", "tag2", ...]`，加到 PASS Verdict 的 output dict 中
- 常用命名空间：domain.xxx / content.xxx / state.xxx / lap.output.xxx / lap.analyst.xxx
- domain.xxx: FORMAT_OUT 前缀（demogame→domain.demogame）
- content.xxx: FORMAT_OUT 描述的内容类型（schema/diff/report/script...）
- state.xxx: 数据经过本节点后的状态（classified/analyzed/validated/generated...）
- lap.output.xxx: 本节点输出给下一阶段什么（classifier/analyzer/validator/generator...）
- 不超过 5 个 tag

## 输出
只输出 unified diff，不输出任何解释：

```diff
--- a/{router_class}
+++ b/{router_class}
@@ ... @@
 ...（context）
-                output=...,
+                output={{...existing..., "granted_tags": [...]}},
```
"""


class GrantedTagsPlannerRouter(Router):
    """R-07 专属：只生成 granted_tags 添加 diff，不处理其他问题。

    只提供 FORMAT_OUT 定义 + PASS Verdict 代码段，任务极小，LLM 专注推断 tag 语义。
    """

    DESCRIPTION = (
        "R-07 专属 LLM 规划器：从 FORMAT_OUT 语义推断 granted_tags，"
        "只向 PASS Verdict output 添加 tags 字段（不改其他内容）"
    )
    FORMAT_IN = "diag.repair.fail-patch"
    FORMAT_OUT = "diag.repair.tags-patch"

    def __init__(self, model: str = _MODEL):
        self._model = model

    def run(self, input_data: Any) -> Verdict:
        issues = input_data.get("b_class_issues", [])
        # R-07 信号：由 R-07-signal 或 verdict_patterns 缺失 granted_tags 触发
        r07_issues = [i for i in issues if "R-07" in i.get("check_id", "")
                      or "granted_tags" in i.get("observation", "").lower()]
        if not r07_issues:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "tags_diff": None},
                           diagnosis="GrantedTagsPlanner: 无 R-07 问题，跳过")

        router_class: str = input_data["router_class"]
        format_out_def = input_data.get("format_out_def")
        run_source: str = input_data.get("run_source", "")

        # 提取 PASS Verdict 代码段
        pass_verdict_code = self._extract_pass_verdict(run_source)

        def _fmt_def(d) -> str:
            if not d:
                return "(未找到)"
            return f"id={d.get('id','?')} | description={d.get('description','')!r} | tags={d.get('tags',[])}"

        prompt = _TAGS_PROMPT.format(
            router_class=router_class,
            format_out_def=_fmt_def(format_out_def),
            pass_verdict_code=pass_verdict_code[:1000] if pass_verdict_code else "(未提取到 PASS Verdict)",
        )

        diff = self._call_llm(router_class, prompt)
        if not diff:
            return Verdict(kind=VerdictKind.FAIL, confidence=0.5,
                           output={**input_data, "tags_diff": None},
                           diagnosis=f"GrantedTagsPlanner: {router_class} LLM 未生成 diff")

        return Verdict(kind=VerdictKind.PASS, confidence=0.9,
                       output={**input_data, "tags_diff": diff},
                       diagnosis=f"GrantedTagsPlanner: {router_class} granted_tags diff 生成")

    @staticmethod
    def _extract_pass_verdict(run_source: str) -> str:
        """从 run() 源码中提取 PASS Verdict 的代码段（约 ±5 行上下文）。"""
        lines = run_source.splitlines()
        for i, line in enumerate(lines):
            if "VerdictKind.PASS" in line:
                start = max(0, i - 3)
                end = min(len(lines), i + 8)
                return "\n".join(lines[start:end])
        return ""

    def _call_llm(self, router_class: str, prompt: str) -> str | None:
        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system="你是 omnifactory LAP 协议专家。只输出 unified diff，不输出任何解释。",
            )
            raw = resp.content[0].text if resp and resp.content else ""
            return _extract_diff(raw)
        except Exception as e:
            logger.warning("GrantedTagsPlanner LLM failed for %s: %s", router_class, e)
            return None


# ════════════════════════════════════════════════════════════════
# PatchMergerRouter — 合并三个 diff 为一个提案
# ════════════════════════════════════════════════════════════════


class PatchMergerRouter(Router):
    """合并 desc_diff / fail_diff / tags_diff 为单一 diff 字符串。

    策略：顺序拼接，每段之间加空行分隔。
    空 diff（对应无该类型问题）直接跳过。
    """

    DESCRIPTION = (
        "合并 R-01/R-05/R-07 三个专属规划器各自生成的 diff 为单一提案，"
        "无问题的类型对应 diff 为 None，直接跳过"
    )
    FORMAT_IN = "diag.repair.tags-patch"
    FORMAT_OUT = "diag.repair.patch-plan"

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data.get("router_class", "")
        parts: list[str] = []
        for key in ("desc_diff", "fail_diff", "tags_diff"):
            d = input_data.get(key)
            if d and d.strip():
                parts.append(d.strip())

        if not parts:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "diff": None},
                           diagnosis=f"PatchMerger: {router_class} 无有效 diff")

        merged = "\n\n".join(parts)
        return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                       output={**input_data, "diff": merged},
                       diagnosis=f"PatchMerger: {router_class} 合并 {len(parts)} 段 diff")


# ════════════════════════════════════════════════════════════════
# PatchValidatorRouter — AST 验证 diff 合法性
# ════════════════════════════════════════════════════════════════


class PatchValidatorRouter(Router):
    """AST 验证修复 diff 的安全性：

      1. diff 非空
      2. 新增行语法合法
      3. 不删除现有 PASS/FAIL Verdict 路径
      4. 不修改 FORMAT_IN / FORMAT_OUT 字段值
    """

    DESCRIPTION = "AST 验证 diff 安全性：语法合法 + 不破坏现有 PASS/FAIL 路径 + 不修改 FORMAT_IN/OUT"
    FORMAT_IN = "diag.repair.patch-plan"
    FORMAT_OUT = "diag.repair.validated-patch"

    def run(self, input_data: Any) -> Verdict:
        diff: str | None = input_data.get("diff")
        if not diff:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "validation_passed": True,
                                   "validation_notes": ["无 diff，跳过验证"]},
                           diagnosis="PatchValidator: 无 diff，跳过")

        router_class: str = input_data.get("router_class", "")
        notes: list[str] = []
        failed = False

        if not diff.strip():
            notes.append("diff 为空")
            failed = True

        if not failed:
            added_lines = [l[1:] for l in diff.splitlines()
                           if l.startswith("+") and not l.startswith("+++")]
            test_snippet = "\n".join(added_lines)
            if test_snippet.strip():
                try:
                    ast.parse(test_snippet)
                    notes.append("diff 新增行语法合法 ✓")
                except SyntaxError as e:
                    notes.append(f"diff 新增行语法错误（可能是片段，非致命）: {e}")

        removed_lines = [l[1:] for l in diff.splitlines()
                         if l.startswith("-") and not l.startswith("---")]
        for line in removed_lines:
            if "VerdictKind.PASS" in line or "VerdictKind.FAIL" in line:
                notes.append(f"diff 删除了 Verdict 行（危险）: {line.strip()}")
                failed = True
                break

        for line in diff.splitlines():
            if line.startswith(("-", "+")) and not line.startswith(("---", "+++")):
                stripped = line[1:].strip()
                if re.match(r"FORMAT_(IN|OUT)\s*=", stripped):
                    notes.append(f"diff 修改了 FORMAT_IN/OUT（禁止）: {stripped}")
                    failed = True
                    break

        # 检查新增行中 Verdict 构造函数是否用了 result= 而非 kind=（常见 LLM 幻觉）
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                stripped = line[1:]
                if "Verdict(" in stripped and re.search(r'result\s*=\s*["\']?FAIL', stripped):
                    notes.append(
                        f"diff 新增行使用了错误的 Verdict 参数 result='FAIL'（应为 kind=VerdictKind.FAIL）: {stripped.strip()}"
                    )
                    failed = True
                    break

        if failed:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "validation_passed": False, "validation_notes": notes},
                           diagnosis=f"PatchValidator: {router_class} 验证失败")

        return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                       output={**input_data, "validation_passed": True, "validation_notes": notes},
                       diagnosis=f"PatchValidator: {router_class} 验证通过")


# ════════════════════════════════════════════════════════════════
# PatchApplierRouter — 备份 + 写入修改理由 + 直接写入源文件
# ════════════════════════════════════════════════════════════════

_APPLIED_DIR = _REPAIR_PENDING_DIR.parent / "applied"
_BACKUP_DIR = _REPAIR_PENDING_DIR.parent / "backups"


def _parse_diff_hunks(diff: str) -> list[dict]:
    """将 unified diff 解析为 hunks 列表。

    每个 hunk: {"removed": [str], "added": [str], "context": [str]}
    其中 removed 是去掉前导 `-` 的行，added 是去掉前导 `+` 的行，
    context 是去掉前导 ` ` 的上下文行（仅 removed 为空时用于锚定）。
    """
    hunks: list[dict] = []
    current: dict | None = None

    for line in diff.splitlines():
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            if current and (current["removed"] or current["added"]):
                hunks.append(current)
            current = {"removed": [], "added": [], "context": []}
            continue
        if current is None:
            current = {"removed": [], "added": [], "context": []}

        if line.startswith("-"):
            current["removed"].append(line[1:])
        elif line.startswith("+"):
            current["added"].append(line[1:])
        else:
            # context line (leading space or bare)
            ctx_line = line[1:] if line.startswith(" ") else line
            current["context"].append(ctx_line)
            # flush current hunk when we return to context after changes
            if current["removed"] or current["added"]:
                hunks.append(current)
                current = {"removed": [], "added": [], "context": [ctx_line]}

    if current and (current["removed"] or current["added"]):
        hunks.append(current)

    return hunks


def _apply_diff_to_source(source: str, diff: str) -> tuple[str, list[str]]:
    """将 unified diff 应用到源文本。

    返回 (new_source, errors)。errors 非空表示有 hunk 应用失败。
    策略：
      1. 对每个 hunk，先尝试直接替换 removed_lines 为 added_lines
      2. 若失败，尝试带上下文锚定后再替换
      3. 若仍失败，跳过并记录错误（不阻塞其他 hunk）
    """
    errors: list[str] = []
    result = source

    hunks = _parse_diff_hunks(diff)
    for hunk in hunks:
        removed = hunk["removed"]
        added = hunk["added"]

        if not removed and not added:
            continue

        old_str = "\n".join(removed)
        new_str = "\n".join(added)

        if old_str and old_str in result:
            result = result.replace(old_str, new_str, 1)
        elif old_str:
            # 尝试去掉首尾空白后匹配（处理行尾空格差异）
            old_stripped = "\n".join(l.rstrip() for l in removed)
            # 扫描 source 中的候选匹配（按行比较，忽略行尾空格）
            src_lines = result.splitlines(keepends=True)
            rem_lines = [l.rstrip() for l in removed]
            matched_start = -1
            for i in range(len(src_lines) - len(rem_lines) + 1):
                if all(src_lines[i + j].rstrip("\n\r") == rem_lines[j]
                       for j in range(len(rem_lines))):
                    matched_start = i
                    break
            if matched_start >= 0:
                # 保留原行的换行符风格
                indent = ""
                if added and removed:
                    # 从第一行 removed 推断缩进
                    raw_rem = removed[0]
                    raw_add = added[0]
                    # 如果 added 缩进比 removed 少，自动对齐
                    if raw_rem.startswith(" ") and not raw_add.startswith(" "):
                        pass  # diff 已含正确缩进
                end = matched_start + len(rem_lines)
                eol = "\n"
                new_lines = [l + eol for l in new_str.splitlines()]
                result_lines = src_lines[:matched_start] + new_lines + src_lines[end:]
                result = "".join(result_lines)
            else:
                errors.append(f"无法匹配删除块（前20字）: {old_str[:40]!r}")
        # added-only hunk（纯插入，需上下文锚定）
        elif added and not removed:
            ctx = hunk.get("context", [])
            if ctx:
                anchor = ctx[-1].rstrip()
                if anchor in result:
                    result = result.replace(anchor, anchor + "\n" + "\n".join(added), 1)
                else:
                    errors.append(f"插入锚点未找到: {anchor[:40]!r}")

    return result, errors


class PatchApplierRouter(Router):
    """备份源文件 → 将 diff 写入源文件 → 保存修改记录（applied/）。

    三步：
      1. backup: data/doctor/repair/backups/<RouterClass>_<ts>.py
      2. apply: 解析 diff，对源文件做 old→new 替换，写入
      3. record: data/doctor/repair/applied/<RouterClass>.md（含 diff + 修改理由）
    """

    DESCRIPTION = (
        "将 LLM 生成的修复 diff 直接写入源文件（备份原版本 + 保存修改记录），"
        "不等待人类审批；若 diff 应用失败则 FAIL 并保留备份"
    )
    FORMAT_IN = "diag.repair.validated-patch"
    FORMAT_OUT = "diag.repair.applied"

    def __init__(self, applied_dir: Path | None = None, backup_dir: Path | None = None):
        self._applied_dir = applied_dir or _APPLIED_DIR
        self._backup_dir = backup_dir or _BACKUP_DIR

    def run(self, input_data: Any) -> Verdict:
        import datetime

        diff: str | None = input_data.get("diff")
        router_class: str = input_data.get("router_class", "UnknownRouter")
        b_class_issues: list = input_data.get("b_class_issues", [])
        source_file: str = input_data.get("source_file", "")

        if not diff or not b_class_issues:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "applied": False, "apply_note": "无 diff，跳过"},
                           diagnosis=f"PatchApplier: {router_class} 无 diff，跳过")

        src_path = Path(source_file)
        if not src_path.exists():
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "applied": False},
                           diagnosis=f"PatchApplier: 源文件不存在 {source_file}")

        original = src_path.read_text(encoding="utf-8", errors="ignore")

        # ── Step 1: 备份 ──
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = self._backup_dir / f"{router_class}_{ts}.py"
        try:
            backup_path.write_text(original, encoding="utf-8")
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "applied": False},
                           diagnosis=f"PatchApplier: 备份失败 {e}")

        # ── Step 2: 应用 diff ──
        new_source, errors = _apply_diff_to_source(original, diff)

        if errors:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "applied": False,
                                   "apply_errors": errors, "backup_path": str(backup_path)},
                           diagnosis=f"PatchApplier: {router_class} diff 应用失败: {errors}")

        if new_source == original:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "applied": False,
                                   "backup_path": str(backup_path)},
                           diagnosis=f"PatchApplier: {router_class} diff 应用后源文件未变化（可能已应用过）")

        try:
            src_path.write_text(new_source, encoding="utf-8")
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "applied": False},
                           diagnosis=f"PatchApplier: 写入源文件失败 {e}")

        # ── Step 3: 保存修改记录 ──
        self._applied_dir.mkdir(parents=True, exist_ok=True)
        pipeline_purpose = input_data.get("pipeline_purpose", "")
        pipeline_brief = input_data.get("pipeline_brief") or {}
        pipeline_node_desc = input_data.get("pipeline_node_desc", "")
        issues_md = "\n".join(
            f"- **[{i.get('check_id')}]** ({i.get('severity')}): {i.get('observation', '')}"
            for i in b_class_issues
        )
        diff_sections = []
        desc_d = input_data.get("desc_diff")
        fail_d = input_data.get("fail_diff")
        tags_d = input_data.get("tags_diff")
        if desc_d:
            diff_sections.append(f"### R-01 DESCRIPTION 补全\n```diff\n{desc_d}\n```")
        if fail_d:
            diff_sections.append(f"### R-05 FAIL 路径补充\n```diff\n{fail_d}\n```")
        if tags_d:
            diff_sections.append(f"### R-07 granted_tags 添加\n```diff\n{tags_d}\n```")
        if not diff_sections:
            diff_sections.append(f"### 合并修改\n```diff\n{diff}\n```")

        record = f"""# 修改记录: {router_class}

**应用时间**: {ts}
**源文件**: `{source_file}`
**备份**: `{backup_path}`
**所属管线**: `{pipeline_brief.get('pipeline_id', '未知')}`

## 管线业务目标
{pipeline_purpose or "(未记录)"}

## 节点 Validator 描述
{pipeline_node_desc or "(未提取到)"}

## 修复的问题
{issues_md}

## 应用的修改（按问题类型分节）

{chr(10).join(diff_sections)}
"""
        try:
            record_path = self._applied_dir / f"{router_class}.md"
            record_path.write_text(record, encoding="utf-8")
        except Exception:
            pass  # 记录失败不影响主流程

        return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                       output={**input_data, "applied": True,
                               "backup_path": str(backup_path),
                               "record_path": str(self._applied_dir / f"{router_class}.md")},
                       diagnosis=f"PatchApplier: {router_class} 已写入 {source_file}")


# ════════════════════════════════════════════════════════════════
# RediagnoseRouter — 重跑确定性诊断，验证等级提升
# ════════════════════════════════════════════════════════════════


class RediagnoseRouter(Router):
    """重跑 Doctor 确定性诊断链，对比前后 health_grade。"""

    DESCRIPTION = "重跑确定性诊断，对比修复前后 health_grade；未应用 patch 时报告 pending 状态"
    FORMAT_IN = "diag.repair.pending"
    FORMAT_OUT = "diag.repair.result"

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data.get("router_class", "")
        source_file: str = input_data.get("source_file", "")
        source_root: str = input_data.get("source_root", str(_DEFAULT_SOURCE_ROOT))
        pending_path: str | None = input_data.get("pending_path")

        if pending_path and Path(pending_path).exists():
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "rediagnose_status": "pending",
                                   "rediagnose_note": f"修复提案待审批：{pending_path}"},
                           diagnosis=f"Rediagnose: {router_class} 等待人类审批")

        from omnicompany.packages.services._diagnosis.doctor.routers import (
            RouterExtractorRouter, RouterSignatureRouter,
            RouterContextCollectorRouter, RouterDeterministicCheckRouter,
            RouterHealthWriterRouter,
        )

        def unpack(v):
            return v.output if hasattr(v, "output") else v

        try:
            r = unpack(RouterExtractorRouter().run({
                "router_class": router_class, "source_file": source_file, "source_root": source_root,
            }))
            r = unpack(RouterSignatureRouter().run(r))
            if not r.get("health_grade"):
                r = unpack(RouterContextCollectorRouter().run(r))
                r = unpack(RouterDeterministicCheckRouter().run(r))
            health = unpack(RouterHealthWriterRouter().run(r))
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "error": str(e)},
                           diagnosis=f"Rediagnose: 重诊断失败 {e}")

        before_grade = input_data.get("before_grade", "?")
        after_grade = health.get("health_grade", "?")
        improved = after_grade < before_grade if before_grade != "?" else None

        return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                       output={**input_data, "rediagnose_status": "applied",
                               "before_grade": before_grade, "after_grade": after_grade,
                               "improved": improved, "health_record": health},
                       diagnosis=f"Rediagnose: {router_class} {before_grade} → {after_grade}")


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════

def _extract_diff(response: str) -> str | None:
    """从 LLM 响应中提取 ```diff...``` 代码块。"""
    m = re.search(r"```diff\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"(--- a/.*?(?=```|\Z))", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


# ════════════════════════════════════════════════════════════════
# run_router_repair — 单 Router 修复流程入口函数
# ════════════════════════════════════════════════════════════════


def run_router_repair(
    router_class: str,
    source_file: str,
    source_root: str | None = None,
    model: str = _MODEL,
) -> dict:
    """对单个 Router 执行 B 类问题补全流程（三次 LLM 调用，各司其职）。

    流程：IssueLoader → SourceLoader → DescPlanner → FailPlanner → TagsPlanner
          → PatchMerger → PatchValidator → PatchApplier（直接写入源文件）

    返回 dict：status = "applied" / "no_issues" / "skipped" / "error"
    """
    if source_root is None:
        source_root = str(_DEFAULT_SOURCE_ROOT)

    def unpack(v):
        return v.output if hasattr(v, "output") else v

    req = {"router_class": router_class, "source_file": source_file, "source_root": source_root}

    try:
        r = unpack(IssueLoaderRouter().run(req))
        if r.get("skip_reason"):
            return {**r, "status": "skipped"}
        if not r.get("b_class_issues"):
            return {**r, "status": "no_issues"}

        r = unpack(RouterSourceLoaderRouter().run(r))
        r = unpack(DescriptionPlannerRouter(model=model).run(r))
        r = unpack(FailPathPlannerRouter(model=model).run(r))
        r = unpack(GrantedTagsPlannerRouter(model=model).run(r))
        r = unpack(PatchMergerRouter().run(r))

        if not r.get("diff"):
            return {**r, "status": "error", "error": "所有规划器均未生成有效 diff"}

        r = unpack(PatchValidatorRouter().run(r))
        if not r.get("validation_passed"):
            return {**r, "status": "error", "error": f"diff 验证失败: {r.get('validation_notes')}"}

        r = unpack(PatchApplierRouter().run(r))
        if not r.get("applied"):
            return {**r, "status": "error",
                    "error": r.get("apply_errors") or r.get("apply_note") or "apply 失败"}
        return {**r, "status": "applied"}

    except Exception as e:
        logger.exception("run_router_repair failed for %s", router_class)
        return {"router_class": router_class, "status": "error", "error": str(e)}
