# [OMNI] origin=claude-code domain=workflow_factory/routers.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:core.team_builder.lap_verifier_retired_impl.routers_legacy.py"
# OMNI-024 ALLOW: 归档冻结留档(废止实现体, 仅历史参考不再演进), 迁移无意义(同仓内其它 _archive ALLOW 先例)
"""_archive/routers_legacy.py — LAPVerifierRouter 废止实现体 (历史参考, 2026-07-26 缩编).

原文件是 workflow_factory 全部 Router 的单文件实现 (3075 行). 2026-07-26 OMNI-040
Stage 3 清洁: 其余 10 个 Router 的实现迁回正式位置 `../routers_legacy.py`,
本文件只保留 2026-07-03 批4 显式废止的 LAPVerifierRouter 实现体 (锚㋒契约:
"实现体留归档不删, 活代码引用已全摘除").

不要从本文件 import; 活代码不应引用 LAPVerifierRouter.
"""
from __future__ import annotations

import re
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router


# ═══════════════════════════════════════════════════════════
# [F] lap_verifier — LAP 合规验证 (LLMRouter)
# ═══════════════════════════════════════════════════════════

_LAP_VERIFY_SYSTEM = """\
你是一个 LAP 合规审计师。检查生成的工作流代码是否符合 LAP 规范。

## 审计维度

1. **Format 规范性**
   - description 是否含三要素（内容语义/验证标准/下游用途）
   - id 是否语义化（非机械编号）
   - 继承链是否合理

2. **Router 规范性**
   - 是否有 FORMAT_IN / FORMAT_OUT / DESCRIPTION
   - DESCRIPTION 是否 >= 50 字符
   - HARD 节点是否有确定性判定逻辑
   - SOFT 节点是否有 VerdictKind.FAIL 路径

3. **拓扑完整性**
   - 无孤立节点
   - feedback 边标记正确

4. **六元原语合规**
   - 继承正确的基类
   - Format 与真实内容对应

## 输出格式（严格 JSON）
```json
{
  "score": 0到100的整数,
  "passed": true或false（score >= 80 且无 critical issue）,
  "issues": [
    {"severity": "critical|warning|info", "category": "format|router|topology|primitive", "message": "描述"}
  ],
  "critical_issues": ["仅列出 critical 级别的问题"]
}
```
"""


class LAPVerifierRouter(Router):
    """确定性 LAP 合规审计（HARD）。

    四维度静态分析：
    1. Format 规范性 — AST 检查 formats.py 中 Format 定义
    2. Router 规范性 — AST 检查 routers.py 中 Router 类定义
    3. 拓扑完整性 — 解析 pipeline.py 检查孤立节点和 FAIL 路由
    4. Format 链健康度 — 检查是否有 Format 混杂或走私字段
    """

    FORMAT_IN = "wf.project_skeleton"
    FORMAT_OUT = "wf.project_skeleton"  # P7.3 单主干 + reports 容器
    DESCRIPTION = (
        "LAP 合规审计（确定性）。四维度静态分析：Format 规范性（description 三要素/语义命名）、"
        "Router 规范性（FORMAT_IN/OUT/DESCRIPTION 存在且 >=50 字符）、拓扑完整性（无孤立节点）、"
        "Format 链健康度（无 Format 混杂或走私字段）。报告写进 reports['lap_audit'], "
        "PASS 时贴 lap-audit-passed tag。"
    )

    def run(self, input_data: Any) -> Verdict:
        import ast as _ast

        skeleton = input_data
        files = skeleton.get("files", {})
        issues: list[dict] = []
        critical_issues: list[dict] = []
        score = 100

        formats_py = files.get("formats.py", "")
        routers_py = files.get("routers.py", "")
        pipeline_py = files.get("pipeline.py", "")

        # ── D1: Format 规范性 ──
        if formats_py:
            try:
                tree = _ast.parse(formats_py)
                format_count = 0
                for node in _ast.walk(tree):
                    if isinstance(node, _ast.Call):
                        func = node.func
                        name = getattr(func, "id", "") or getattr(func, "attr", "")
                        if name == "Format":
                            format_count += 1
                            has_desc = any(
                                kw.arg == "description" for kw in node.keywords
                            )
                            has_id = any(kw.arg == "id" for kw in node.keywords)
                            if not has_id:
                                critical_issues.append({
                                    "dimension": "format_spec",
                                    "message": "Format 定义缺少 id 字段",
                                })
                                score -= 15
                            if not has_desc:
                                critical_issues.append({
                                    "dimension": "format_spec",
                                    "message": "Format 定义缺少 description 字段 (F-02 MUST)",
                                })
                                score -= 10
                            else:
                                # M1.3 (2026-04-19): F-02 MUST — description ≥ 100 字符。
                                # 旧版只查存在性不查长度, 容忍 50 字符占位符。
                                desc_text = ""
                                for kw in node.keywords:
                                    if kw.arg != "description":
                                        continue
                                    val = kw.value
                                    if isinstance(val, _ast.Constant) and isinstance(val.value, str):
                                        desc_text = val.value
                                    elif isinstance(val, _ast.JoinedStr):
                                        desc_text = "x" * 150  # f-string 跳过
                                    break
                                if len(desc_text) < 100:
                                    fmt_id_val = None
                                    for kw in node.keywords:
                                        if kw.arg == "id" and isinstance(kw.value, _ast.Constant):
                                            fmt_id_val = kw.value.value
                                            break
                                    critical_issues.append({
                                        "dimension": "format_spec",
                                        "message": (
                                            f"Format {fmt_id_val or '<unknown>'} description 仅 "
                                            f"{len(desc_text)} 字符 < 100 (F-02 MUST)"
                                        ),
                                    })
                                    score -= 10
                if format_count == 0:
                    critical_issues.append({
                        "dimension": "format_spec",
                        "message": "formats.py 中未找到 Format 定义",
                    })
                    score -= 20
            except SyntaxError as e:
                issues.append({
                    "dimension": "format_spec",
                    "message": f"formats.py 语法错误: {e}",
                })
                score -= 10

        # ── D2: Router 规范性 ──
        if routers_py:
            try:
                tree = _ast.parse(routers_py)
                for node in _ast.walk(tree):
                    if isinstance(node, _ast.ClassDef):
                        # 检查是否是 Router 子类
                        base_names = [
                            getattr(b, "id", "") or getattr(b, "attr", "")
                            for b in node.bases
                        ]
                        if not any(b in ("Router", "LLMRouter") for b in base_names):
                            continue
                        # 检查必需的类属性
                        class_attrs = {}
                        for item in node.body:
                            if isinstance(item, _ast.Assign):
                                for target in item.targets:
                                    if isinstance(target, _ast.Name):
                                        class_attrs[target.id] = item.value
                        for required in ("FORMAT_IN", "FORMAT_OUT", "DESCRIPTION"):
                            if required not in class_attrs:
                                critical_issues.append({
                                    "dimension": "router_spec",
                                    "message": f"Router {node.name} 缺少 {required}",
                                })
                                score -= 10
                        # DESCRIPTION 长度检查
                        if "DESCRIPTION" in class_attrs:
                            desc_node = class_attrs["DESCRIPTION"]
                            desc_text = ""
                            if isinstance(desc_node, _ast.Constant) and isinstance(desc_node.value, str):
                                desc_text = desc_node.value
                            elif isinstance(desc_node, _ast.JoinedStr):
                                desc_text = "x" * 50  # f-string，假设足够长
                            if len(desc_text) < 50:
                                issues.append({
                                    "dimension": "router_spec",
                                    "message": f"Router {node.name} DESCRIPTION 不足 50 字符 ({len(desc_text)})",
                                })
                                score -= 3
            except SyntaxError as e:
                issues.append({
                    "dimension": "router_spec",
                    "message": f"routers.py 语法错误: {e}",
                })
                score -= 10

        # ── D3: 拓扑完整性 ──
        if pipeline_py:
            # 提取所有节点 ID
            node_ids = set(re.findall(r'id="(\w+)"', pipeline_py))
            # 提取边引用的节点
            edge_sources = set(re.findall(r'source="(\w+)"', pipeline_py))
            edge_targets = set(re.findall(r'target="(\w+)"', pipeline_py))
            referenced = edge_sources | edge_targets
            # 入口节点
            entry_match = re.search(r'entry="(\w+)"', pipeline_py)
            if entry_match:
                referenced.add(entry_match.group(1))
            # 孤立节点
            orphans = node_ids - referenced
            if orphans:
                for orphan in orphans:
                    issues.append({
                        "dimension": "topology",
                        "message": f"孤立节点: {orphan} 未被任何边引用",
                    })
                    score -= 5

        # ── D4: Format 链健康度 ──
        if pipeline_py:
            # 检查是否有 Format 混杂（同一 format 作为多个验证节点的 in/out）
            format_pairs = re.findall(r'format_in="([^"]+)".*?format_out="([^"]+)"', pipeline_py)
            pass_through = [(fi, fo) for fi, fo in format_pairs if fi == fo]
            if len(pass_through) > 1:
                issues.append({
                    "dimension": "format_health",
                    "message": f"检测到 {len(pass_through)} 个 pass-through 节点（Format 输入输出相同），"
                               f"可能存在 Format 混杂。建议用语义递进的 Format 链替代。",
                })
                score -= 3
        if routers_py:
            # 检查走私字段
            smuggle_patterns = re.findall(r'\[\"_\w+\"\]|\.get\(\"_\w+\"', routers_py)
            if smuggle_patterns:
                for pat in smuggle_patterns[:3]:
                    issues.append({
                        "dimension": "format_health",
                        "message": f"疑似走私字段: {pat}，请在 Format schema 中显式声明",
                    })
                    score -= 5

        # ── D5: info_audit 覆盖度 (Phase 5.1) ──
        # 默认: 所有 SOFT 节点应参与 info_audit 跟踪 (LLMClient 全局开关自动处理)。
        # 规则: 若 routers.py 中 Router 显式设置 INFO_AUDIT_OPT_OUT=True,
        #       必须在 DESCRIPTION 里说明原因, 否则 WARN。
        if routers_py and pipeline_py:
            has_soft = "ValidatorKind.SOFT" in pipeline_py
            if has_soft:
                opt_out_routers: list[tuple[str, str]] = []  # (cls_name, description)
                try:
                    tree = _ast.parse(routers_py)
                    for cls_node in _ast.walk(tree):
                        if not isinstance(cls_node, _ast.ClassDef):
                            continue
                        cls_description = ""
                        opts_out = False
                        for item in cls_node.body:
                            if not isinstance(item, _ast.Assign):
                                continue
                            for target in item.targets:
                                if not isinstance(target, _ast.Name):
                                    continue
                                if target.id == "INFO_AUDIT_OPT_OUT":
                                    val = item.value
                                    if isinstance(val, _ast.Constant) and val.value is True:
                                        opts_out = True
                                elif target.id == "DESCRIPTION":
                                    val = item.value
                                    if isinstance(val, _ast.Constant) and isinstance(val.value, str):
                                        cls_description = val.value
                        if opts_out:
                            opt_out_routers.append((cls_node.name, cls_description))
                except SyntaxError:
                    pass

                for cls_name, desc in opt_out_routers:
                    # 检查 DESCRIPTION 是否明确说明了退出原因
                    justified = any(
                        marker in desc
                        for marker in ("info_audit", "INFO_AUDIT", "信息审计", "不需要审计")
                    )
                    if not justified:
                        issues.append({
                            "dimension": "info_audit_coverage",
                            "message": (
                                f"Router {cls_name} 设置 INFO_AUDIT_OPT_OUT=True 但 "
                                f"DESCRIPTION 未说明原因, 确认这是刻意决策 "
                                f"(SOFT 节点通常应参与 info_audit 跟踪)"
                            ),
                        })
                        score -= 2

        # ── D6: Format description 五项语义 (SKILL §2.1, Fix 10) ──
        # 老版本只检查 description 是否存在 + 是否 >= 50 字符, 不检查五项语义:
        #   1. 内容语义  2. 字段含义  3. 上游承诺  4. 下游用途  5. 最小样例
        # 放宽: 至少出现 3/5 项才算过, 否则 WARN (不 FAIL 免打断生成)
        if formats_py:
            try:
                tree = _ast.parse(formats_py)
                for node in _ast.walk(tree):
                    if not isinstance(node, _ast.Call):
                        continue
                    name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
                    if name != "Format":
                        continue
                    fid = None
                    desc = ""
                    for kw in node.keywords:
                        if kw.arg == "id" and isinstance(kw.value, _ast.Constant):
                            fid = kw.value.value
                        elif kw.arg == "description":
                            val = kw.value
                            if isinstance(val, _ast.Constant) and isinstance(val.value, str):
                                desc = val.value
                            elif isinstance(val, _ast.JoinedStr):
                                desc = "x" * 300  # f-string 跳过, 假设充分
                    if not desc or not fid:
                        continue
                    if len(desc) < 200:
                        # 短 description 极可能漏了五项
                        issues.append({
                            "dimension": "format_semantics",
                            "message": (
                                f"Format {fid} description 只有 {len(desc)} 字符, "
                                f"SKILL §2.1 要求写全五项语义 (内容/字段/上游承诺/下游用途/样例), "
                                f"通常至少 200 字符"
                            ),
                        })
                        score -= 3
                    # 启发式检查: 五个关键词至少命中 3 个
                    markers = [
                        any(k in desc for k in ("语义", "表达", "概念", "代表")),  # 内容语义
                        any(k in desc for k in ("字段", "schema", "属性", "键")),  # 字段含义
                        any(k in desc for k in ("上游", "前置", "承诺", "已通过", "经过")),  # 上游承诺
                        any(k in desc for k in ("下游", "供", "用于", "消费", "使用")),  # 下游用途
                        any(k in desc for k in ("样例", "示例", "例如", "例:", "最小")),  # 样例
                    ]
                    hit = sum(markers)
                    if hit < 3:
                        issues.append({
                            "dimension": "format_semantics",
                            "message": (
                                f"Format {fid} description 语义要素不足 ({hit}/5 命中), "
                                f"SKILL §2.1 要求至少提到内容语义/字段含义/上游承诺/下游用途/样例 中的 3 项"
                            ),
                        })
                        score -= 2
            except SyntaxError:
                pass

        # ── D7: 拓扑反模式 — skeleton 克隆链检测 (SKILL §2.3, Fix 10) ──
        # 检测 formats.py 里是否定义了同一主干 + 多个继承的"验收印章"克隆 Format
        # 例: project_skeleton → compiled_skeleton → audited_skeleton → tested_skeleton
        if formats_py and pipeline_py:
            try:
                tree = _ast.parse(formats_py)
                parent_to_children: dict[str, list[str]] = {}
                id_to_parent: dict[str, str] = {}
                for node in _ast.walk(tree):
                    if not isinstance(node, _ast.Call):
                        continue
                    name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
                    if name != "Format":
                        continue
                    fid = None
                    parent = None
                    for kw in node.keywords:
                        if kw.arg == "id" and isinstance(kw.value, _ast.Constant):
                            fid = kw.value.value
                        elif kw.arg == "parent" and isinstance(kw.value, _ast.Constant):
                            parent = kw.value.value
                    if fid and parent:
                        id_to_parent[fid] = parent
                        parent_to_children.setdefault(parent, []).append(fid)
                # 寻找深度 >= 3 的继承链 (可能是克隆链)
                for root, chain in id_to_parent.items():
                    depth = 0
                    cur = root
                    seen = set()
                    while cur in id_to_parent and cur not in seen:
                        seen.add(cur)
                        cur = id_to_parent[cur]
                        depth += 1
                    if depth >= 3 and "skeleton" in root.lower():
                        issues.append({
                            "dimension": "topology_anti_pattern",
                            "message": (
                                f"Format {root} 继承深度 {depth}, 疑似 skeleton 克隆链反模式 "
                                f"(SKILL §2.3 / GAP §1.2-A). 验证节点应用单主干 Format + "
                                f"reports 容器 + granted_tags 累加, 不要为每个验收阶段造新 Format"
                            ),
                        })
                        score -= 5
            except SyntaxError:
                pass

        # ── D8: SOFT 节点的 output_token_budget (SKILL §3.2, Fix 10) ──
        # 这是补丁式检查: routers.py 里 SOFT Router 的 DESCRIPTION 应该提到 budget 或 scale_strategy,
        # 否则可能超预算截断
        if routers_py and pipeline_py:
            has_soft_in_pipeline = "ValidatorKind.SOFT" in pipeline_py
            if has_soft_in_pipeline:
                try:
                    tree = _ast.parse(routers_py)
                    for cls_node in _ast.walk(tree):
                        if not isinstance(cls_node, _ast.ClassDef):
                            continue
                        bases = [
                            getattr(b, "id", "") or getattr(b, "attr", "")
                            for b in cls_node.bases
                        ]
                        if not any(b in ("LLMRouter", "AgentNodeLoop") for b in bases):
                            continue  # 只检查 LLM 类 Router
                        cls_desc = ""
                        for item in cls_node.body:
                            if not isinstance(item, _ast.Assign):
                                continue
                            for target in item.targets:
                                if isinstance(target, _ast.Name) and target.id == "DESCRIPTION":
                                    val = item.value
                                    if isinstance(val, _ast.Constant) and isinstance(val.value, str):
                                        cls_desc = val.value
                        # 检查是否有具体的 token 数字或明确的 scale 策略关键词
                        # 避免匹配"没提到 token"这类负面语义
                        import re as _re_d8
                        has_budget = bool(
                            _re_d8.search(r'(\d{2,}\s*(?:token|字|行))', cls_desc)
                            or _re_d8.search(r'(?:budget|output_token_budget|max_tokens)', cls_desc)
                            or any(k in cls_desc for k in ("SCATTER", "分页 PARTIAL", "骨架+填肉", "scale_strategy"))
                        )
                        if not has_budget:
                            issues.append({
                                "dimension": "token_budget",
                                "message": (
                                    f"LLM Router {cls_node.name} DESCRIPTION 未提 token 预算/scale_strategy "
                                    f"(SKILL §3.2), 超预算时可能截断"
                                ),
                            })
                            score -= 1
                except SyntaxError:
                    pass

        # ── D9: F-15/P-13 声明即消费 (M2.α, 2026-04-19) ──
        # 口号: Format 禁搭便车 —— 真的用到的字段必须进入对应 Format schema。
        # 调 module 级 check_format_in_consumption() 纯 AST 对比; 全仓版本
        # 将在 M2.γ 提取到 packages/services/doctor/checks/。
        if routers_py and formats_py:
            try:
                f15_findings = check_format_in_consumption(routers_py, formats_py)
                for f in f15_findings:
                    if f["severity"] == "critical":
                        critical_issues.append({
                            "dimension": "format_in_consumption",
                            "message": f["message"],
                        })
                        score -= 10
                    else:  # warn
                        issues.append({
                            "dimension": "format_in_consumption",
                            "message": f["message"],
                        })
                        score -= 2
            except Exception as _e_d9:
                # checker 不应阻塞 LAP 本身; 内部错只记 warn
                issues.append({
                    "dimension": "format_in_consumption",
                    "message": f"D9 checker 执行异常: {type(_e_d9).__name__}: {_e_d9}",
                })

        # ── 综合判定 ──
        score = max(0, score)
        passed = score >= 70 and len(critical_issues) == 0

        report = {
            "score": score,
            "passed": passed,
            "issues": issues,
            "critical_issues": critical_issues,
        }
        # P7.3 reports container
        reports = dict(skeleton.get("reports", {}))
        reports["lap_audit"] = report
        result = {**skeleton, "reports": reports}

        if passed:
            return Verdict(
                kind=VerdictKind.PASS, output=result,
                granted_tags=["lap-audit-passed"],
                diagnosis=f"LAP 审计: {score}/100",
            )
        return Verdict(kind=VerdictKind.FAIL, output=result,
                       diagnosis=f"LAP 审计不通过: {score}/100, "
                                 f"{len(critical_issues)} critical issues")

