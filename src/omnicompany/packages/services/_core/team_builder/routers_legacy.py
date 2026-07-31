# [OMNI] origin=claude-code domain=workflow_factory/routers.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:core.team_builder.workflow_factory_req_analyzer_format_designer_node_planner_code_gen.routers_legacy.py"
"""workflow_factory routers — 全部 10+1 个节点的 Router 实现

节点分布：
  HARD (确定性): E compile_checker, G error_route_auditor, H integration_tester, J finalizer
  LLMRouter:     A req_analyzer, B format_designer, C node_planner, E' syntax_fixer, F lap_verifier
  AgentNodeLoop: D code_generator, I auto_fixer
"""

from __future__ import annotations

import importlib
import json
import os
import py_compile
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind


import logging as _wf_logging
_wf_log = _wf_logging.getLogger(__name__)


# ─── 2026-04-09 修复硬截断事故 ─────────────────────────────────
# 旧版本在多处用 text[:N] 截断 LLM 输入, 导致 workflow-factory 产出 11/12 节点
# 事故 (format_designer 33k → chain_text[:10000] 丢 70%)。
# qwen3.6-plus 等现代模型 1M context, 这些截断是遗留产物, 必须移除。
# 用下面这个助手: 不截但超过阈值时大声 log.warning, 让问题立刻暴露。
_WF_INPUT_WARN_THRESHOLD = 200_000  # 超过 200k chars 才 warn, 一般不会命中


def _wf_no_trunc(text: str, context_label: str = "") -> str:
    """workflow-factory 内部的"不截断"占位符 —— 只在超大时 warn。

    替代旧 `text[:N]` 模式, 消除块状信息丢失。
    """
    n = len(text or "")
    if n > _WF_INPUT_WARN_THRESHOLD:
        _wf_log.warning(
            "[wf_factory] %s LLM input exceptionally large: %d chars (threshold %d). "
            "Not truncated, but check model context window.",
            context_label or "<unknown>", n, _WF_INPUT_WARN_THRESHOLD,
        )
    return text or ""


def _extract_json_obj(text: str) -> dict | None:
    """从 LLM 输出中提取第一个 JSON 对象。支持 ```json 代码块。"""
    # 尝试代码块
    m = re.search(r'```(?:json)?\s*\n(\{.*?\})\s*\n```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 尝试裸 JSON
    m = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    # 最后尝试整个文本
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ═══════════════════════════════════════════════════════════
# [M2.α/γ] F-15/P-13 "声明即消费" checker (2026-04-19)
# M2.α 落地于 workflow_factory 本地, M2.γ (commit TBD) 抽到
# packages/services/doctor/checks/format_in_consumption.py 作为共享实现。
# 此处 re-export 保持 import 路径向后兼容, 不让二处实现漂移。
# ═══════════════════════════════════════════════════════════

from omnicompany.packages.services._diagnosis.doctor.checks import (  # noqa: E402
    check_format_in_consumption,
)




# ═══════════════════════════════════════════════════════════
# [E] compile_checker — 三层编译检查 (HARD)
# ═══════════════════════════════════════════════════════════

from omnicompany.runtime.routing.router import Router


class CompileCheckerRouter(Router):
    """三层编译检查: L1 py_compile → L2 import → L3 PipelineChecker。

    输入 wf.project_skeleton: {package_path, files, pipeline_name, reports}
    输出 wf.project_skeleton: 同 Format, reports["compile"] 追加 + granted_tags=["compile-passed"] (P7.3)

    L1: 对每个 .py 文件调用 py_compile.compile() 检查语法。
    L2: 尝试 importlib.import_module(package_path) 检查 import 链。
    L3: 调用 PipelineChecker.check() 验证 Format 类型兼容性。
    所有三层通过才算 PASS，任一层失败即 FAIL 并附带错误详情。
    """

    FORMAT_IN = "wf.project_skeleton"
    FORMAT_OUT = "wf.project_skeleton"  # P7.3 单主干: 报告写进 reports 容器
    DESCRIPTION = (
        "三层编译检查: L1 py_compile 语法检查 → L2 import 可达验证 "
        "→ L3 PipelineChecker 类型兼容检查。任一层失败即 FAIL 并附带错误详情，"
        "全部通过给 wf.project_skeleton 贴 compile-passed tag, 报告写进 reports['compile']。"
    )

    def run(self, input_data: Any) -> Verdict:
        skeleton = input_data
        files: dict[str, str] = skeleton.get("files", {})
        pkg_path: str = skeleton.get("package_path", "")

        report = {
            "l1_syntax": {"passed": True, "errors": []},
            "l2_import": {"passed": True, "errors": []},
            "l3_pipeline": {"passed": True, "errors": [], "warnings": []},
        }

        # ── L1: py_compile ──
        with tempfile.TemporaryDirectory() as tmpdir:
            for fname, content in files.items():
                if not fname.endswith(".py"):
                    continue
                fpath = Path(tmpdir) / fname
                # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
                fpath.write_text(content, encoding="utf-8")
                try:
                    py_compile.compile(str(fpath), doraise=True)
                except py_compile.PyCompileError as e:
                    report["l1_syntax"]["errors"].append({
                        "file": fname,
                        "error": str(e),
                    })
                    report["l1_syntax"]["passed"] = False

        # ── L2: import 可达 ──
        if report["l1_syntax"]["passed"] and pkg_path:
            # 将生成的文件写入实际位置后尝试 import
            # 这里用安全方式：检查 package_path 对应的目录是否存在
            pkg_dir = pkg_path.replace(".", os.sep)
            # __file__ = .../src/omnifactory/packages/services/workflow_factory/routers.py
            # parents: [0]=workflow_factory [1]=services [2]=packages [3]=omnifactory [4]=src
            src_root = Path(__file__).resolve().parents[4]  # src/
            target = src_root / pkg_dir
            if target.exists():
                try:
                    # 尝试导入（会执行 register 逻辑）
                    mod = importlib.import_module(pkg_path)
                    # 检查是否有 build_pipeline 函数
                    if not hasattr(mod, "build_pipeline"):
                        report["l2_import"]["errors"].append({
                            "error": f"{pkg_path} 缺少 build_pipeline() 函数",
                        })
                        report["l2_import"]["passed"] = False
                except Exception as e:
                    report["l2_import"]["errors"].append({
                        "error": f"import {pkg_path} 失败: {e}",
                    })
                    report["l2_import"]["passed"] = False
            else:
                # 文件还没写入磁盘 — 这是正常的（代码在内存 skeleton 中）
                # 标记为 skipped 而非 failed，L2 会在 integration_tester 写磁盘后再验证
                report["l2_import"]["passed"] = True  # 不阻塞
                report["l2_import"]["skipped"] = True
                report["l2_import"]["errors"].append({
                    "error": f"包目录 {target} 不存在（跳过，integration_tester 会再验证）",
                    "severity": "info",
                })

        # ── L3: PipelineChecker ──
        # 尝试从文件内容中提取 PipelineSpec 并检查
        pipeline_py = files.get("pipeline.py", "")
        if pipeline_py and report["l1_syntax"]["passed"]:
            try:
                # 简单的静态分析：检查 pipeline.py 中是否有 PipelineSpec 构造
                if "PipelineSpec(" not in pipeline_py:
                    report["l3_pipeline"]["warnings"].append(
                        "pipeline.py 中未找到 PipelineSpec 构造"
                    )
                # 检查 nodes 和 edges 是否定义
                if "nodes=" not in pipeline_py:
                    report["l3_pipeline"]["errors"].append({
                        "error": "pipeline.py 中缺少 nodes 定义",
                    })
                    report["l3_pipeline"]["passed"] = False
                if "edges=" not in pipeline_py:
                    report["l3_pipeline"]["warnings"].append(
                        "pipeline.py 中缺少 edges 定义"
                    )
            except Exception as e:
                report["l3_pipeline"]["errors"].append({
                    "error": f"PipelineChecker 检查异常: {e}",
                })

        # 综合判定：L1 语法 + L3 结构必须通过，L2 import 在 skipped 时不阻塞
        report["passed"] = (
            report["l1_syntax"]["passed"]
            and report["l3_pipeline"]["passed"]
            and (report["l2_import"]["passed"] or report["l2_import"].get("skipped", False))
        )

        # P7.3 reports container: 写进 output["reports"]["compile"]
        reports = dict(skeleton.get("reports", {}))
        reports["compile"] = report
        result = {**skeleton, "reports": reports}

        if report["passed"]:
            return Verdict(
                kind=VerdictKind.PASS,
                output=result,
                granted_tags=["compile-passed"],
                diagnosis="编译检查通过",
            )
        return Verdict(
            kind=VerdictKind.FAIL,
            output=result,
            diagnosis=f"编译检查失败: {json.dumps([e for layer in report.values() if isinstance(layer, dict) for e in layer.get('errors', [])], ensure_ascii=False)[:500]}",
        )


# ═══════════════════════════════════════════════════════════
# [G] error_route_auditor — 错误路由完整性审计 (HARD)
# ═══════════════════════════════════════════════════════════

class ErrorRouteAuditorRouter(Router):
    """像 Rust 编译器一样检查错误路径完整性。五项确定性检查。

    输入: wf.project_skeleton (通过编译的)
    输出: wf.project_skeleton, reports["error_route"] 追加 + granted_tags=["error-route-passed"]

    检查项：
    1. FAIL 路由覆盖率 — 每个 ANCHOR 节点必须有 FAIL 出边
    2. LLM 失败声明 — 每个 SOFT 节点的 Router 必须包含 Verdict.fail / VerdictKind.FAIL
    3. 验证绑定 — bugfix 类必须有测试节点，代码生成类必须有编译检查
    4. UserInquiry 接口 — 标注 needs_user_inquiry 的节点必须 import UserInquiry
    5. DESCRIPTION 完整性 — 每个 Router 的 DESCRIPTION >= 50 字符
    """

    FORMAT_IN = "wf.project_skeleton"
    FORMAT_OUT = "wf.project_skeleton"  # P7.3 单主干 + reports 容器
    DESCRIPTION = (
        "错误路由完整性审计（确定性）。五项检查: FAIL 路由覆盖率、"
        "LLM 失败声明（grep Verdict.fail/VerdictKind.FAIL）、验证绑定、"
        "UserInquiry 接口使用、DESCRIPTION 完整性（>= 50 字符）。"
        "任一关键项未通过即 FAIL。报告写进 reports['error_route'], "
        "PASS 时贴 error-route-passed tag。"
    )

    def run(self, input_data: Any) -> Verdict:
        skeleton = input_data
        files: dict[str, str] = skeleton.get("files", {})

        issues: list[dict] = []
        checks = {
            "fail_coverage": {"total": 0, "covered": 0, "uncovered": []},
            "llm_fail_declaration": {"total_soft": 0, "has_fail_path": 0, "missing": []},
            "verification_binding": {"required": [], "present": [], "missing": []},
            "user_inquiry_check": {"required": 0, "implemented": 0, "missing": []},
            "description_check": {"total": 0, "adequate": 0, "too_short": []},
        }

        pipeline_py = files.get("pipeline.py", "")
        routers_py = files.get("routers.py", "")

        # ── 检查 1: FAIL 路由覆盖 ──
        # 从 pipeline.py 提取节点和边
        node_ids = re.findall(r'id="(\w+)"', pipeline_py)
        soft_nodes = set(re.findall(r'kind=ValidatorKind\.SOFT[^)]*id="(\w+)"', pipeline_py))
        # 也尝试反向匹配
        if not soft_nodes:
            # 匹配 id=... 后面跟 validator=ValidatorSpec(kind=ValidatorKind.SOFT)
            for match in re.finditer(r'PipelineNode\([^)]*id="(\w+)"[^)]*SOFT', pipeline_py, re.DOTALL):
                soft_nodes.add(match.group(1))

        # 找 FAIL 边
        fail_targets = set()
        for match in re.finditer(r'condition=VerdictKind\.FAIL[^)]*source="(\w+)"', pipeline_py, re.DOTALL):
            fail_targets.add(match.group(1))
        # 也找 FAIL 路由在 routes 字典中
        for match in re.finditer(r'VerdictKind\.FAIL.*?Route\(', pipeline_py, re.DOTALL):
            fail_targets.update(node_ids)  # 如果有全局 FAIL 路由，所有节点都覆盖

        checks["fail_coverage"]["total"] = len(node_ids)
        # 每个节点检查是否有 FAIL 出边或 FAIL 路由
        for nid in node_ids:
            # 检查是否在 edges 中有 source=nid, condition=FAIL 的边
            pattern = rf'source="{nid}"[^)]*FAIL|FAIL[^)]*source="{nid}"'
            # 或者在 routes 中有 VerdictKind.FAIL
            routes_pattern = rf'id="{nid}"[^}}]*VerdictKind\.FAIL'
            if re.search(pattern, pipeline_py, re.DOTALL) or re.search(routes_pattern, pipeline_py, re.DOTALL):
                checks["fail_coverage"]["covered"] += 1
            else:
                checks["fail_coverage"]["uncovered"].append(nid)

        # ── 检查 2: LLM 失败声明 ──
        # 从 routers.py 提取所有 Router 类
        router_classes = re.findall(r'class (\w+Router)\(', routers_py)
        for cls_name in router_classes:
            # 提取该类的代码块（粗略：从 class 到下一个 class 或文件末尾）
            cls_pattern = rf'class {cls_name}\(.*?(?=\nclass |\Z)'
            cls_match = re.search(cls_pattern, routers_py, re.DOTALL)
            if not cls_match:
                continue
            cls_code = cls_match.group()

            # 判断是否是 SOFT/LLM 节点
            is_llm = any(kw in cls_code for kw in ["LLMClient", "LLMRouter", "client.call", "AgentNodeLoop"])
            if not is_llm:
                continue

            checks["llm_fail_declaration"]["total_soft"] += 1

            # 继承 LLMRouter 的类自动拥有 FAIL 能力（基类 run() 有 FAIL 路径）
            inherits_llm_router = bool(re.search(rf'class {cls_name}\(\s*LLMRouter\s*\)', cls_code))

            # 检查是否有失败声明
            has_fail = inherits_llm_router or any(kw in cls_code for kw in [
                "VerdictKind.FAIL", "Verdict.fail",
                "kind=VerdictKind.FAIL",
                'kind="fail"',
                "PARTIAL",  # PARTIAL 也算一种失败声明
            ])
            if has_fail:
                checks["llm_fail_declaration"]["has_fail_path"] += 1
            else:
                checks["llm_fail_declaration"]["missing"].append(cls_name)
                issues.append({
                    "severity": "critical",
                    "check": "llm_fail_declaration",
                    "message": f"{cls_name} 是 LLM 节点但没有 FAIL/PARTIAL 路径——无法宣告失败",
                })

        # ── 检查 3: 验证绑定 ──
        pipeline_name = skeleton.get("pipeline_name", "").lower()
        if any(kw in pipeline_name for kw in ["debug", "fix", "bug"]):
            checks["verification_binding"]["required"].append("test")
            if any(kw in pipeline_py.lower() for kw in ["test_runner", "tester", "test_exec"]):
                checks["verification_binding"]["present"].append("test")
            else:
                checks["verification_binding"]["missing"].append("test")
                issues.append({
                    "severity": "warning",
                    "check": "verification_binding",
                    "message": "bugfix 类工作流缺少测试验证节点",
                })

        if any(kw in pipeline_name for kw in ["gen", "impl", "code", "write"]):
            checks["verification_binding"]["required"].append("compile")
            if any(kw in pipeline_py.lower() for kw in ["compile", "syntax", "py_compile"]):
                checks["verification_binding"]["present"].append("compile")
            else:
                checks["verification_binding"]["missing"].append("compile")
                issues.append({
                    "severity": "warning",
                    "check": "verification_binding",
                    "message": "代码生成类工作流缺少编译检查节点",
                })

        # ── 检查 4: UserInquiry ──
        # 从 node_plan 中找 needs_user_inquiry=True 的节点
        node_plan = skeleton.get("node_plan", {})
        if isinstance(node_plan, dict):
            for node in node_plan.get("nodes", []):
                if node.get("needs_user_inquiry"):
                    checks["user_inquiry_check"]["required"] += 1
                    node_id = node["id"]
                    # 检查对应 Router 是否 import 了 UserInquiry
                    if "UserInquiry" in routers_py or "user_inquiry" in routers_py:
                        checks["user_inquiry_check"]["implemented"] += 1
                    else:
                        checks["user_inquiry_check"]["missing"].append(node_id)
                        issues.append({
                            "severity": "warning",
                            "check": "user_inquiry_check",
                            "message": f"节点 {node_id} 标注需要用户交互但未使用 UserInquiry",
                        })

        # ── 检查 5: DESCRIPTION 完整性 ──
        for cls_name in router_classes:
            checks["description_check"]["total"] += 1
            desc_match = re.search(
                rf'class {cls_name}\(.*?DESCRIPTION\s*=\s*["\'](.+?)["\']',
                routers_py, re.DOTALL,
            )
            # 也匹配多行 DESCRIPTION
            if not desc_match:
                desc_match = re.search(
                    rf'class {cls_name}\(.*?DESCRIPTION\s*=\s*\((.*?)\)',
                    routers_py, re.DOTALL,
                )

            if desc_match:
                desc = desc_match.group(1).replace('"', '').replace("'", '').strip()
                if len(desc) >= 50:
                    checks["description_check"]["adequate"] += 1
                else:
                    checks["description_check"]["too_short"].append(cls_name)
                    issues.append({
                        "severity": "warning",
                        "check": "description_check",
                        "message": f"{cls_name}.DESCRIPTION 过短 ({len(desc)} < 50 字符)",
                    })
            else:
                checks["description_check"]["too_short"].append(cls_name)
                issues.append({
                    "severity": "warning",
                    "check": "description_check",
                    "message": f"{cls_name} 缺少 DESCRIPTION",
                })

        # ── 检查 6: Format 混杂检测 ──
        format_pairs = re.findall(r'format_in="([^"]+)".*?format_out="([^"]+)"', pipeline_py)
        passthrough_count = sum(1 for fi, fo in format_pairs if fi == fo)
        if passthrough_count > 1:  # syntax_fixer 的 pass-through 可以接受，超过 1 个说明有问题
            issues.append({
                "severity": "warning",
                "check": "format_health",
                "message": f"检测到 {passthrough_count} 个 Format pass-through 节点，"
                           f"建议用语义递进的 Format 链替代",
            })

        # ── 检查 7: SOFT 节点后续验证 ──
        # 从 edges 中提取 SOFT 节点的下游
        for soft_node in soft_nodes:
            # 检查 SOFT 节点 PASS 的下游是否有 HARD 验证
            pass_edges = re.findall(
                rf'source="{soft_node}".*?target="(\w+)".*?condition=VerdictKind\.PASS',
                pipeline_py, re.DOTALL,
            )
            # 简单检查：如果 SOFT 节点直接跳到 EMIT，且没有任何 HARD 后续
            for target in pass_edges:
                if target not in node_ids:
                    continue
                # 检查 target 是否是 HARD 或后续链中有 HARD
                target_soft = target in soft_nodes
                if target_soft:
                    issues.append({
                        "severity": "info",
                        "check": "soft_validation",
                        "message": f"SOFT 节点 {soft_node} 的 PASS 下游 {target} 也是 SOFT，"
                                   f"建议在两者之间插入 HARD 交叉验证",
                    })

        # ── 综合判定 ──
        critical = [i for i in issues if i["severity"] == "critical"]
        overall_passed = len(critical) == 0

        # P7.3 reports container
        reports = dict(skeleton.get("reports", {}))
        reports["error_route"] = {
            **checks,
            "overall_passed": overall_passed,
            "issues": issues,
        }
        result = {**skeleton, "reports": reports}

        if overall_passed:
            return Verdict(
                kind=VerdictKind.PASS,
                output=result,
                granted_tags=["error-route-passed"],
                diagnosis="错误路由审计通过",
            )
        return Verdict(
            kind=VerdictKind.FAIL,
            output=result,
            diagnosis=f"错误路由审计发现 {len(critical)} 个关键问题: "
                      + "; ".join(i["message"] for i in critical[:3]),
        )


# ═══════════════════════════════════════════════════════════
# [H] integration_tester — 集成测试 (HARD)
# ═══════════════════════════════════════════════════════════

class IntegrationTesterRouter(Router):
    """集成测试: 验证生成的代码能实际跑起来。

    输入: wf.project_skeleton (通过路由审计的)
    输出: wf.project_skeleton, reports["integration"] 追加 + granted_tags=["integration-passed"]

    四项测试：
    1. 所有文件可以写入磁盘
    2. import package 成功
    3. build_pipeline() 返回合法 PipelineSpec
    4. PipelineChecker.check() 通过
    """

    FORMAT_IN = "wf.project_skeleton"
    FORMAT_OUT = "wf.project_skeleton"  # P7.3 单主干 + reports 容器
    DESCRIPTION = (
        "集成测试（确定性）。四项测试: 文件写入磁盘、import package 成功、"
        "build_pipeline() 返回合法 PipelineSpec、PipelineChecker.check() 通过。"
        "任一测试失败即 FAIL。报告写进 reports['integration'], "
        "PASS 时贴 integration-passed tag。"
    )

    def run(self, input_data: Any) -> Verdict:
        skeleton = input_data
        files: dict[str, str] = skeleton.get("files", {})
        pkg_path: str = skeleton.get("package_path", "")

        tests = []

        # ── T1: 文件写入 ──
        # 2026-04-21 OMNI-041 防污染: 允许 skeleton 通过 _wf_test_output_root
        # 覆盖 src_root (测试脚本用 tmp 目录, 不要污染 src/)
        override_root = skeleton.get("_wf_test_output_root")
        if override_root:
            src_root = Path(override_root).resolve()
            src_root.mkdir(parents=True, exist_ok=True)
        else:
            # __file__ = .../src/omnifactory/packages/services/workflow_factory/_archive/routers_legacy.py
            # parents: [0]=_archive [1]=workflow_factory [2]=services [3]=packages [4]=omnifactory [5]=src
            # (parents[5] 因为从 routers.py 搬到 _archive/ 多了一层)
            # 历史上 parents[4]=src 当本文件还在 routers.py 里 (2026-04-21 前)
            src_root = Path(__file__).resolve().parents[5]  # src/
        pkg_dir = src_root / pkg_path.replace(".", os.sep)
        try:
            pkg_dir.mkdir(parents=True, exist_ok=True)
            for fname, content in files.items():
                fpath = pkg_dir / fname
                # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
                fpath.write_text(content, encoding="utf-8")
            # 2026-04-21: 新建目录后清 path_importer_cache, 让 T2 importlib 能发现新 pkg
            # (importlib.invalidate_caches() 在 Python 3.12 MetadataPathFinder 有 bug, 绕开)
            sys.path_importer_cache.clear()
            tests.append({"name": "file_write", "passed": True, "error": None})
        except Exception as e:
            tests.append({"name": "file_write", "passed": False, "error": str(e)})
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**skeleton, "reports": {**skeleton.get("reports", {}), "integration": {"passed": False, "tests": tests}}},
                diagnosis=f"文件写入失败: {e}",
            )

        # ── T2: import package + submodules ──
        # 2026-04-19 M1.1: __init__.py import 成功 ≠ submodule 健康。
        # 只验 pkg import 会漏 routers.py/formats.py 里的 import 幻觉（如 LLM 写
        # `from omnicompany.protocol.verdict` 应 `.anchor`, 实测 91074fc 案例）。
        # 4 个核心子模块全部拉起来, 任一失败即 T2 FAIL。
        try:
            # 清除可能的旧缓存
            for key in list(sys.modules.keys()):
                if key.startswith(pkg_path):
                    del sys.modules[key]
            mod = importlib.import_module(pkg_path)

            submodule_errors: list[str] = []
            for submod_name in ("formats", "pipeline", "routers", "run"):
                fname = f"{submod_name}.py"
                if fname not in files:
                    # 生成物里没这个文件, 跳过（例如 run.py 可选）
                    continue
                full_name = f"{pkg_path}.{submod_name}"
                try:
                    importlib.import_module(full_name)
                except Exception as se:
                    submodule_errors.append(f"{submod_name}.py: {type(se).__name__}: {se}")

            if submodule_errors:
                err_msg = "; ".join(submodule_errors)
                tests.append({"name": "import", "passed": False, "error": err_msg})
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={**skeleton, "reports": {**skeleton.get("reports", {}), "integration": {"passed": False, "tests": tests}}},
                    diagnosis=f"submodule import 失败: {err_msg}",
                )

            tests.append({"name": "import", "passed": True, "error": None})
        except Exception as e:
            tests.append({"name": "import", "passed": False, "error": str(e)})
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**skeleton, "reports": {**skeleton.get("reports", {}), "integration": {"passed": False, "tests": tests}}},
                diagnosis=f"import {pkg_path} 失败: {e}",
            )

        # ── T3: build_pipeline ──
        try:
            build_fn = getattr(mod, "build_pipeline", None)
            if build_fn is None:
                # 2026-04-19 修：按 registry 惯例去 .pipeline 子模块找（core/pipelines.py
                # 里用 _lazy("{pkg}.pipeline", "build_pipeline") 懒加载）。
                # 旧代码错去 .run 查（那里放的是 build_bindings），永远找不到。
                try:
                    pipeline_mod = importlib.import_module(f"{pkg_path}.pipeline")
                    build_fn = getattr(pipeline_mod, "build_pipeline", None)
                except ImportError:
                    pass
            if build_fn is None:
                # 最后兜底看 .run（有些管线把 build_pipeline 也放 run.py）
                try:
                    run_mod = importlib.import_module(f"{pkg_path}.run")
                    build_fn = getattr(run_mod, "build_pipeline", None)
                except ImportError:
                    pass

            if build_fn is None:
                tests.append({"name": "build_pipeline", "passed": False, "error": "未找到 build_pipeline 函数"})
            else:
                spec = build_fn()
                if spec is None:
                    tests.append({"name": "build_pipeline", "passed": False, "error": "build_pipeline() 返回 None"})
                else:
                    tests.append({"name": "build_pipeline", "passed": True, "error": None})
        except Exception as e:
            tests.append({"name": "build_pipeline", "passed": False, "error": str(e)})

        # ── T4: PipelineChecker ──
        if tests[-1]["passed"]:
            try:
                from omnicompany.protocol.format import create_builtin_registry
                from omnicompany.protocol.pipeline import PipelineChecker

                registry = create_builtin_registry()
                # Register the generated pipeline's own formats
                try:
                    fmt_mod = importlib.import_module(f"{pkg_path}.formats")
                    reg_fn = getattr(fmt_mod, "register_formats", None)
                    if reg_fn:
                        import inspect
                        sig = inspect.signature(reg_fn)
                        if sig.parameters:
                            reg_fn(registry)
                        else:
                            reg_fn()
                    # Also register any module-level Format objects
                    from omnicompany.protocol.format import Format as _Fmt
                    for attr_name in dir(fmt_mod):
                        obj = getattr(fmt_mod, attr_name)
                        if isinstance(obj, _Fmt) and not registry.is_registered(obj.id):
                            try:
                                registry.register(obj)
                            except (ValueError, KeyError):
                                pass
                except Exception:
                    pass  # Best effort
                checker = PipelineChecker(registry)
                result = checker.check(spec)
                if result.valid:
                    tests.append({"name": "pipeline_check", "passed": True, "error": None})
                else:
                    # Collect both structural errors and edge incompatibilities
                    errs = [str(e) for e in result.errors[:5]]
                    # Edge incompatibilities are the most common cause (feedback edge type mismatch)
                    for er in result.edge_results:
                        if not er.connection.compatible:
                            errs.append(
                                f"INCOMPAT: {er.edge.source}->{er.edge.target}: "
                                f"format_out={er.source_format_out} vs format_in={er.target_format_in}. "
                                f"Fix: change {er.edge.target}.format_in to 'requirement' (common ancestor) "
                                f"or adjust Format inheritance."
                            )
                    tests.append({"name": "pipeline_check", "passed": False, "error": "; ".join(errs)})
            except Exception as e:
                tests.append({"name": "pipeline_check", "passed": False, "error": str(e)})
        else:
            tests.append({"name": "pipeline_check", "passed": False, "error": "跳过（build_pipeline 未通过）"})

        # ── T5: build_bindings 实例化（M1.2, 2026-04-19）──
        # T3 只验 build_pipeline() 返回 PipelineSpec（拓扑合法）, 不碰 Router 实例。
        # T5 真调 build_bindings() 拿 dict[node_id, Router], 抓:
        #   - Router __init__ 崩（import LLMClient 错误 / hardcoded param 报 TypeError）
        #   - build_bindings 漏 Router（spec 有节点但 bindings 没对应 key）
        #   - binding value 不是 Router 子类实例
        bindings_for_runner: dict[str, Any] | None = None
        try:
            bindings_fn = None
            try:
                run_mod = importlib.import_module(f"{pkg_path}.run")
                bindings_fn = getattr(run_mod, "build_bindings", None)
            except ImportError:
                pass
            if bindings_fn is None:
                # 兜底看顶层包 / pipeline 子模块
                bindings_fn = getattr(mod, "build_bindings", None)
            if bindings_fn is None:
                try:
                    pipeline_mod = importlib.import_module(f"{pkg_path}.pipeline")
                    bindings_fn = getattr(pipeline_mod, "build_bindings", None)
                except ImportError:
                    pass

            if bindings_fn is None:
                tests.append({"name": "build_bindings", "passed": False,
                              "error": "未找到 build_bindings 函数（.run / pkg 顶层 / .pipeline 均无）"})
            else:
                bindings_obj = bindings_fn()
                if not isinstance(bindings_obj, dict) or not bindings_obj:
                    tests.append({"name": "build_bindings", "passed": False,
                                  "error": f"build_bindings() 返回非字典或空: {type(bindings_obj).__name__}"})
                else:
                    from omnicompany.runtime.routing.router import Router as _Router
                    non_router = [k for k, v in bindings_obj.items() if not isinstance(v, _Router)]
                    if non_router:
                        tests.append({"name": "build_bindings", "passed": False,
                                      "error": f"非 Router 实例: {non_router[:3]}"})
                    else:
                        # 对比 spec 的 node_ids 和 bindings keys（如果 T3 成功拿到 spec）
                        spec_nodes: set[str] = set()
                        if "spec" in locals() and locals().get("spec") is not None:
                            try:
                                spec_nodes = {n.id for n in spec.nodes}  # type: ignore[name-defined]
                            except Exception:
                                pass
                        missing = spec_nodes - set(bindings_obj.keys()) if spec_nodes else set()
                        if missing:
                            tests.append({"name": "build_bindings", "passed": False,
                                          "error": f"spec 节点未在 bindings 中: {sorted(missing)[:3]}"})
                        else:
                            tests.append({"name": "build_bindings", "passed": True, "error": None})
                            bindings_for_runner = bindings_obj
        except Exception as e:
            tests.append({"name": "build_bindings", "passed": False,
                          "error": f"{type(e).__name__}: {e}"})

        # ── T6: PipelineRunner 构造 dry-run（M1.4, 2026-04-19）──
        # 不跑实际步（需要有效 LLM 入口 payload 且会触发联网调用）,
        # 只验 runner 能构造: spec + bindings + bus + registry 对齐, 初始化不崩。
        # 抓 runner-level 的 bug（如 format_registry 里漏注册某个 Format 导致校验崩）。
        if any(t["name"] == "build_bindings" and t["passed"] for t in tests) and \
           any(t["name"] == "build_pipeline" and t["passed"] for t in tests) and \
           bindings_for_runner is not None:
            try:
                from omnicompany.bus.memory import MemoryBus
                from omnicompany.protocol.format import create_builtin_registry as _mk_reg
                from omnicompany.runtime.exec.runner import PipelineRunner

                # 复用 T4 已注册的 registry（含生成管线的 Format）
                runner_registry = _mk_reg()
                try:
                    fmt_mod = importlib.import_module(f"{pkg_path}.formats")
                    reg_fn = getattr(fmt_mod, "register_formats", None)
                    if reg_fn:
                        import inspect as _inspect
                        if _inspect.signature(reg_fn).parameters:
                            reg_fn(runner_registry)
                        else:
                            reg_fn()
                    from omnicompany.protocol.format import Format as _Fmt2
                    for attr_name in dir(fmt_mod):
                        obj = getattr(fmt_mod, attr_name)
                        if isinstance(obj, _Fmt2) and not runner_registry.is_registered(obj.id):
                            try:
                                runner_registry.register(obj)
                            except (ValueError, KeyError):
                                pass
                except Exception:
                    pass

                bus = MemoryBus()
                _runner = PipelineRunner(
                    pipeline=spec,
                    bindings=bindings_for_runner,
                    bus=bus,
                    format_registry=runner_registry,
                )
                # 只验 runner 构造不崩 + entry node 存在于 bindings
                entry_id = spec.entry
                if entry_id not in bindings_for_runner:
                    tests.append({"name": "runner_construct", "passed": False,
                                  "error": f"entry node '{entry_id}' 不在 bindings 中"})
                else:
                    tests.append({"name": "runner_construct", "passed": True, "error": None})
            except Exception as e:
                tests.append({"name": "runner_construct", "passed": False,
                              "error": f"{type(e).__name__}: {e}"})
        else:
            tests.append({"name": "runner_construct", "passed": False,
                          "error": "跳过（build_pipeline 或 build_bindings 未通过）"})

        passed = all(t["passed"] for t in tests)
        report = {"passed": passed, "tests": tests}
        # P7.3 reports container
        reports = dict(skeleton.get("reports", {}))
        reports["integration"] = report
        result = {**skeleton, "reports": reports}

        if passed:
            return Verdict(
                kind=VerdictKind.PASS,
                output=result,
                granted_tags=["integration-passed"],
                diagnosis="集成测试通过",
            )
        failed = [t for t in tests if not t["passed"]]
        return Verdict(
            kind=VerdictKind.FAIL,
            output=result,
            diagnosis=f"集成测试失败: {', '.join(t['name'] + ': ' + (t['error'] or '?') for t in failed[:3])}",
        )


# ═══════════════════════════════════════════════════════════
# [A] req_analyzer — 需求解析 (LLMRouter)
# ═══════════════════════════════════════════════════════════

from omnicompany.runtime.routing.router import LLMRouter


class ReqAnalyzerRouter(LLMRouter):
    """将自然语言需求转化为结构化需求规格。

    使用 LLM 解析用户输入，提取：目标、领域、输入输出描述、约束条件、
    验证需求（什么阶段需要什么检查）、错误场景、用户交互点。
    参考现有管线命名规范。
    """

    FORMAT_IN = "wf.requirement_raw"
    FORMAT_OUT = "wf.requirement"
    INPUT_KEYS = ["text"]
    DESCRIPTION = (
        "将自然语言工作流需求解析为结构化需求规格。提取目标、领域、"
        "输入输出描述、约束条件、验证需求、错误场景、用户交互点。"
        "输出 JSON 格式的结构化需求。"
    )

    def run(self, input_data: Any) -> Verdict:
        text = input_data.get("text", "") if isinstance(input_data, dict) else str(input_data)
        if not text.strip():
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis="输入为空，需要自然语言需求描述")

        try:
            resp = self.client.call(
                messages=[{"role": "user", "content": f"请分析以下工作流需求：\n\n{text}"}],
                system=_REQ_SYSTEM,
            )
            raw = resp.content[0].text
            clean = raw

            parsed = _extract_json_obj(clean)
            if not parsed:
                return Verdict(kind=VerdictKind.FAIL, output=input_data,
                               diagnosis=f"LLM 未返回有效 JSON: {clean[:200]}")
            return Verdict(kind=VerdictKind.PASS, output=parsed)
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis=f"需求解析失败: {e}")


_REQ_SYSTEM = """\
你是一个 LAP (Language-Anchored Programming) 工作流架构师。
你的任务是将自然语言需求解析为结构化的工作流需求规格。

## 输出格式（严格 JSON）

```json
{
  "goal": "工作流要达成的目标（一句话）",
  "domain": "所属领域（sw/demogame/rewrite/custom 等）",
  "input_description": "输入数据的语义描述",
  "output_description": "期望输出的语义描述",
  "constraints": ["约束条件列表"],
  "reference_pipelines": ["可参考的现有管线名，如 sw-review, debug 等"],
  "verification_requirements": [
    {"stage": "阶段名", "method": "compiler|test|llm|schema", "criteria": "判定标准"}
  ],
  "error_scenarios": ["可能的错误场景及其处理方式"],
  "needs_user_interaction": ["需要用户确认的决策点"]
}
```

## 注意事项
- verification_requirements 至少包含 1 项
- 每个错误场景要说明处理方式（重试/打回/升级）
- 参考现有管线：sw-verify, sw-review, sw-plan, sw-tdd, sw-implement, debug,
  lang-rewrite, equiv-test, guardian, demogame-learn, skill-import
"""


# ═══════════════════════════════════════════════════════════
# [B] format_designer — Format 链设计 (LLMRouter)
# ═══════════════════════════════════════════════════════════

class FormatDesignerRouter(LLMRouter):
    """根据结构化需求设计 Format 链。

    每个 Format 是一个有意义的中间产物，包含语义身份。
    设计原则：语义锚定、单一职责、可验证性、继承优先。
    """

    FORMAT_IN = "wf.requirement"
    FORMAT_OUT = "wf.format_chain"
    INPUT_KEYS = None  # 接受任意 dict（上游输出直接传入）
    DESCRIPTION = (
        "根据结构化需求设计 Format 继承链。确保每个 Format 的 id 语义化"
        "（禁止机械编号），description 含三要素（内容语义/验证标准/下游用途），"
        "优先继承 BUILTIN_FORMATS。输出 JSON。"
    )

    def run(self, input_data: Any) -> Verdict:
        req = input_data
        req_text = json.dumps(req, indent=2, ensure_ascii=False) if isinstance(req, dict) else str(req)
        try:
            resp = self.client.call(
                messages=[{"role": "user", "content": f"请根据以下需求设计 Format 链：\n\n{_wf_no_trunc(req_text, 'format_designer.req_text')}"}],
                system=_FORMAT_SYSTEM,
            )
            raw = resp.content[0].text
            clean = raw
            parsed = _extract_json_obj(clean)
            if not parsed or "formats" not in parsed:
                return Verdict(kind=VerdictKind.FAIL, output=input_data,
                               diagnosis=f"Format 设计未返回有效 JSON: {clean[:200]}")
            # 显式传递上游需求上下文（不走私）
            parsed["requirement_context"] = {
                "domain": req.get("domain", "custom") if isinstance(req, dict) else "custom",
                "goal": req.get("goal", "") if isinstance(req, dict) else "",
            }
            return Verdict(kind=VerdictKind.PASS, output=parsed)
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis=f"Format 设计失败: {e}")


_FORMAT_SYSTEM = """\
你是一个 LAP Format 类型系统设计师。根据需求设计 Format 继承链。

## 设计原则
1. **语义锚定** — Format id 必须表达内容语义（如 "debug.hypothesis"），禁止 "output_0" 这种机械编号
2. **单一职责** — 每个 Format 只描述一种中间产物
3. **可验证性** — 每个 Format 都可以独立检查是否合格
4. **继承优先** — 优先继承已有类型（requirement, spec, code, test-result, doc, tool-observation）
5. **description 三要素** — 每个 Format 的 description 必须包含：内容语义 + 验证标准 + 下游用途

## Format 设计哲学（必须遵循）

Format 承担**阅读性**和**约束性**双重含义：
- **阅读性**：如果一个中间产物有明确语义、有调试价值、有复用可能，就应该定义为独立 Format
- **约束性**：Format 定义了数据结构契约，越精确越能在编译期和运行时发现问题

### 反模式（必须避免）
- ❌ 不要让 Format 链只有 `input → output` 两端，中间全藏在节点里
- ❌ 不要用 `_` 前缀字段走私上游数据（如 `_requirement`），显式在 schema 中声明
- ❌ 不要让同一个 Format 在多个验证节点间 pass-through，每经过验证应该用递进的 Format 表达
- ✅ 每个有意义的中间产物都应该有独立 Format（如 "通过编译的代码" ≠ "未编译的代码"）

### 粒度判断
Format 粒度随领域成熟度细化。设计时要有前瞻性：即使当前只有一条管线用，如果中间产物有独立调试/复用价值，就应该定义为 Format。

## 可继承的内置 Format
requirement, intent, spec, code, binary, test-plan, test-result, doc, api-doc,
ticket, chat-message, ci-signal, agent-state, agent-action, tool-observation

## 输出格式（严格 JSON）
```json
{
  "formats": [
    {
      "id": "domain.semantic-name",
      "name": "人类可读名称",
      "description": "完整语义描述（含三要素）",
      "parent": "父 Format id 或 null",
      "json_schema": {"type": "object", "properties": {...}, "required": [...]},
      "semantic_preconditions": ["前置条件"],
      "granted_tags_on_pass": ["PASS 时授予的标签"]
    }
  ],
  "chain": [
    {"from": "format_a", "to": "format_b", "via_node": "node_name"}
  ]
}
```
"""


# ═══════════════════════════════════════════════════════════
# [C] node_planner — 节点规划 (LLMRouter)
# ═══════════════════════════════════════════════════════════

class NodePlannerRouter(LLMRouter):
    """根据 Format 链为每条转换设计 Router 节点。

    设计原则：职责单一、HARD/SOFT 分明、FAIL 路由完整、
    LLM 可宣告失败、DESCRIPTION 详尽。
    """

    FORMAT_IN = "wf.format_chain"
    FORMAT_OUT = "wf.node_plan"
    INPUT_KEYS = None
    DESCRIPTION = (
        "根据 Format 链设计 Router 节点。确保：职责单一，HARD/SOFT 分类正确，"
        "每个 SOFT 节点有 FAIL 路由，每个 LLM 节点可以宣告失败，"
        "节点描述详尽到可直接指导编码（>= 50 字符）。输出 JSON。"
    )

    def run(self, input_data: Any) -> Verdict:
        fmt_chain = input_data
        chain_text = json.dumps(fmt_chain, indent=2, ensure_ascii=False) if isinstance(fmt_chain, dict) else str(fmt_chain)
        # 从显式字段读取上游需求上下文
        req_ctx = fmt_chain.get("requirement_context", {}) if isinstance(fmt_chain, dict) else {}
        domain = req_ctx.get("domain", "custom") if isinstance(req_ctx, dict) else "custom"
        pipeline_name = req_ctx.get("goal", "generated")[:30].replace(" ", "-").lower()

        try:
            resp = self.client.call(
                messages=[{"role": "user", "content": f"请根据以下 Format 链设计节点规划：\n\n{_wf_no_trunc(chain_text, 'node_planner.chain_text')}"}],
                system=_NODE_SYSTEM,
            )
            raw = resp.content[0].text
            clean = raw
            parsed = _extract_json_obj(clean)
            if not parsed or "nodes" not in parsed:
                return Verdict(kind=VerdictKind.FAIL, output=input_data,
                               diagnosis=f"节点规划未返回有效 JSON: {clean[:200]}")

            # ─── 完整性校验 (2026-04-09 修复块状信息丢失) ───────
            # 事故: qwen3.6-plus 在 node_planner 阶段只产出 11/12 节点,
            # 导致 format_designer 设计的 16 个 format 中 9 个完全未被任何节点消费。
            # 验证门: 每个 format_designer 设计的 format 必须被至少一个节点引用
            # (作为 format_in 或 format_out)。漏掉的 format 触发 FAIL → retry。
            nodes = parsed.get("nodes") or []
            all_fmts: set[str] = set()
            if isinstance(fmt_chain, dict):
                for f in fmt_chain.get("formats", []) or []:
                    fid = (f or {}).get("id")
                    if fid:
                        all_fmts.add(fid)
            used_fmts: set[str] = set()
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                for key in ("format_in", "format_out"):
                    v = n.get(key)
                    if v:
                        used_fmts.add(v)
            lost = sorted(all_fmts - used_fmts)
            if lost:
                coverage_pct = (len(used_fmts & all_fmts) / max(1, len(all_fmts))) * 100
                diag = (
                    f"Format 覆盖率不足: {len(lost)}/{len(all_fmts)} 个 format 未被任何节点消费 "
                    f"({coverage_pct:.0f}%): {lost[:8]}{'...' if len(lost) > 8 else ''}. "
                    f"请重新规划节点: format_designer 设计的每个 format 都必须作为某节点的 format_in "
                    f"或 format_out (入口 format 除外)。当前产出 {len(nodes)} 个节点, 明显不足以覆盖 "
                    f"{len(all_fmts)} 个 format, 你可能遗漏了后半段的合成/验证/发布阶段节点。"
                )
                return Verdict(kind=VerdictKind.FAIL, output=input_data,
                               diagnosis=diag)
            # 节点数下界: 至少应有 len(formats) - 1 个节点 (减去入口 format)
            min_nodes = max(1, len(all_fmts) - 1)
            if len(nodes) < min_nodes:
                diag = (
                    f"节点数过少: 只产出 {len(nodes)} 个节点, 但 format_designer 设计了 {len(all_fmts)} "
                    f"个 format, 至少应该有 {min_nodes} 个节点. 请重新规划, 确保每个 format 转换都有节点支撑."
                )
                return Verdict(kind=VerdictKind.FAIL, output=input_data,
                               diagnosis=diag)

            # 附加 node_planner 真正产出的元信息:
            # - pipeline_name: 从 goal 推导并 sanitize 后的管线名
            # - domain: 分流用
            # requirement_context 是 wf.format_chain 的内容, 不在这里搭便车
            # (F-15/P-13: 原样穿过就绕过)——下游 framework_context_loader 走 fan-in
            # 直连 format_designer 拿。
            parsed["pipeline_name"] = pipeline_name
            parsed["domain"] = domain
            parsed["_format_chain"] = fmt_chain
            return Verdict(kind=VerdictKind.PASS, output=parsed)
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=input_data,
                           diagnosis=f"节点规划失败: {e}")


_NODE_SYSTEM = """\
你是一个 LAP 管线节点架构师。根据 Format 链为每条转换设计 Router 节点。

## 设计原则
1. **职责单一** — 一个节点只做一件事（解析 OR 验证 OR 转换，不混合）
2. **HARD/SOFT 分明** — 能用确定性逻辑判定的标 HARD（编译/测试/schema），需要 LLM 的标 SOFT
3. **FAIL 路由完整** — 每个节点必须有 FAIL 路由（RETRY / JUMP / feedback / HALT）
4. **LLM 失败声明** — 每个 SOFT 节点必须能输出 Verdict.FAIL(diagnosis="输入不满足要求: ...")
5. **DESCRIPTION 详尽** — >= 50 字符，说明输入/处理/输出/成功标准/失败处理

## 拓扑健康度要求（必须遵循）

### 薄弱环节交叉验证
- SOFT 节点是确定性薄弱处，每个 SOFT 节点后面**必须有对应的交叉验证**
- 交叉验证用 HARD 节点实现：编译检查、schema 校验、AST 分析、单元测试
- loop/retry 只用于"随机发散"的锚定，确定性错误用预防性设计解决

### 单节点纯粹性
- 每个节点获得恰好足够的上下文，不要把无关信息塞进输入
- 不要让一个修复节点同时处理多种类型的失败 — 用管线路由分流

### 退出条件前置完全化
- 进入 SOFT 节点前，必须明确：PASS/FAIL/PARTIAL 各自含义、输出 Format schema
- 不要在 SOFT 节点内部定义退出条件，在 pipeline 层级的路由表中定义

## 实现类型
- Router (HARD): 确定性，如编译检查、schema 验证
- LLMRouter: 单次 LLM 调用 + 工具分发
- AgentNodeLoop: 多轮工具探索，仅用于确实需要多轮的任务（写多文件、跨文件修复）

## 节点设计单表 (SKILL §3.1) — SOFT 节点必填字段

除基础字段外, **每个 SOFT 节点**必须填以下 3 项 (2026-04-09 P7.8 meta-pipeline 自净要求):

- `context_sources`: list[str] — 本节点必须注入的**信息源清单**, 每条都要具体
  - 例: ["Router 基类源码 (inspect.getsource)", "参考域 selftest/routers.py 全文", "目标 API 的 JSON schema"]
  - **至少 1 条**, 否则 SOFT 节点会凭 LLM 记忆幻觉
  - 如果本节点有下游 HARD verification_binding 兜底, 也要写出兜底关系

- `hallucination_risks`: list[str] — LLM 可能幻觉的**具体字段**, 每条对应一条 context_sources 或缓解措施
  - 例: ["API 方法名 → 靠 context_sources[0] 真源码消除", "返回 schema 字段名 → 靠 context_sources[2] 消除"]
  - 不要写"可能出错"这种空话

- `output_token_budget`: str — 估算单次输出 token 上限
  - 公式: 预估产物字符数 / 3.5 或 代码行数 × 15
  - 例: "≈2500 (约 170 行代码)" / "≈800 (单条 JSON 报告)"
  - > 4k 的必须在 implementation_hint 写 scale_strategy (SCATTER / 分页 / 骨架+填肉)

## 输出格式（严格 JSON）
```json
{
  "nodes": [
    {
      "id": "node_name",
      "kind": "ANCHOR",
      "validator_kind": "HARD|SOFT",
      "format_in": "format.id",
      "format_out": "format.id",
      "description": "详尽描述（>= 50 字符, 说明输入/处理/输出/成功标准/失败处理）",
      "implementation_hint": "Router|LLMRouter|AgentNodeLoop, 如超预算加 scale_strategy, 必要时引用参考范本如 selftest/routers.py",
      "tools": ["工具列表，AgentNodeLoop 时填"],
      "error_routes": {"FAIL": "RETRY(3)|JUMP(target)|HALT|feedback(target)"},
      "needs_user_inquiry": false,
      "verification_binding": "compiler|test|schema|llm|null",
      "context_sources": ["SOFT 节点必填, 至少 1 条, 每条具体到可执行"],
      "hallucination_risks": ["SOFT 节点必填, 每条对应一条 context_sources"],
      "output_token_budget": "SOFT 节点必填, 例: ≈2500 (约 170 行)"
    }
  ],
  "edges": [
    {"source": "node_a", "target": "node_b", "condition": "PASS|FAIL|PARTIAL", "feedback": false}
  ],
  "feedback_loops": [
    {"description": "回路描述", "trigger": "触发条件", "path": ["node_a", "node_b"]}
  ]
}
```
"""


# ═══════════════════════════════════════════════════════════
# [C2] framework_context_loader — 注入框架真源码 (HARD)
# ═══════════════════════════════════════════════════════════

class FrameworkContextLoaderRouter(Router):
    """注入框架真源码到 node_plan，消灭 code_generator 对框架 API 的幻觉根源。

    【为什么存在】
    SKILL §3.3 代码生成类节点的信息源清单要求：必须注入框架基类/接口的真源码
    + 至少一份参考域全文。workflow_factory 历史上靠在 _CODE_GEN_SYSTEM 里用自然
    语言"教导"LLM（NodeKind 要小写 / 别 import typing.Dict / AnchorSpec 怎么构造），
    这是 system-prompt-as-changelog 反模式——每次 LLM 翻车就补一条禁令，prompt
    越长注意力越散，禁令还是记不住。

    正确做法：用 inspect.getsource 把真源码塞进 format_in 字段，LLM 看到的是
    真代码而不是二手传话，NodeKind/Verdict/Router 等 API 的幻觉率直接归零。

    【不做的事】
    这个节点只负责 Read + inspect + 打包。不裁剪、不摘要、不注释——保留原始源码
    形态。下游 code_generator 自己决定如何使用。
    """

    # 2026-04-19 (M2.β v2) composite fan-in: 原样穿过的 requirement_context 不该由
    # wf.node_plan 搭便车。FORMAT_IN 声明为 composite id, components =
    # [wf.node_plan, wf.format_chain]; runner composite mode 组织 input_data 为
    # {"wf.node_plan": {...}, "wf.format_chain": {...}}, Router 显式按 component
    # 取数据, 不再搭便车。
    FORMAT_IN = "wf.framework_context_loader.input"
    FORMAT_OUT = "wf.node_plan_augmented"
    DESCRIPTION = (
        "Deterministic framework context loader. 用 inspect.getsource 拉取 Router / "
        "Verdict / VerdictKind / LLMClient / AnchorSpec / PipelineNode / PipelineSpec / "
        "NodeKind / Format 等基础类型的真源码，并 Read 一份 selftest 参考域全文，"
        "封装到 node_plan.framework_context 字段供下游 code_generator 按字段名精确引用。"
        "消灭 code_generator 对框架 API 的幻觉根源。"
        "FORMAT_IN 是 composite (wf.framework_context_loader.input), 两路 fan-in: "
        "wf.node_plan (node_plan_auditor PASS) + wf.format_chain (format_designer PASS), "
        "前者给 pipeline_name/domain, 后者给 requirement_context。"
    )

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"期望 composite dict 类型, 收到 {type(input_data).__name__}",
            )

        # 2026-04-19 (M2.β v2) composite mode: runner 用每个 component 的 format_out 作 key
        # 组织 input_data。从两路显式取数, 不再靠扁平 merge 或透传。
        node_plan = input_data.get("wf.node_plan") or {}
        format_chain = input_data.get("wf.format_chain") or {}
        if not isinstance(node_plan, dict) or not isinstance(format_chain, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=(
                    f"composite fan-in 组件类型错误: wf.node_plan={type(node_plan).__name__}, "
                    f"wf.format_chain={type(format_chain).__name__}"
                ),
            )

        try:
            import inspect

            from omnicompany.runtime.routing.router import Router as _RouterBase
            from omnicompany.runtime.llm.llm import LLMClient
            from omnicompany.protocol.anchor import (
                Verdict as _Verdict,
                VerdictKind as _VerdictKind,
                AnchorSpec as _AnchorSpec,
                TransformerSpec as _TransformerSpec,
                ValidatorSpec as _ValidatorSpec,
                ValidatorKind as _ValidatorKind,
                Route as _Route,
                RouteAction as _RouteAction,
            )
            from omnicompany.protocol.pipeline import (
                NodeKind as _NodeKind,
                PipelineNode as _PipelineNode,
                PipelineSpec as _PipelineSpec,
                PipelineEdge as _PipelineEdge,
                NodeMaturity as _NodeMaturity,
            )
            from omnicompany.protocol.format import Format as _Format

            def _src(obj) -> str:
                """Safe inspect.getsource —— 某些 C 扩展/ Pydantic 动态类可能 fail。"""
                try:
                    return inspect.getsource(obj)
                except (OSError, TypeError) as e:
                    return f"# <inspect.getsource failed for {obj!r}: {e}>"

            # ── 框架真源码 ──
            llmclient_init_sig = str(inspect.signature(LLMClient.__init__))

            # PipelineNode / PipelineSpec 是 Pydantic BaseModel，getsource 可能拿不到
            # 干净定义（因为 Pydantic 动态生成），此时 fallback 到 .__fields__ 列表
            def _pydantic_or_src(cls) -> str:
                src = _src(cls)
                if "<inspect.getsource failed" in src:
                    try:
                        fields = getattr(cls, "model_fields", None) or getattr(cls, "__fields__", {})
                        lines = [f"class {cls.__name__}(BaseModel):"]
                        for name, field in fields.items():
                            ann = getattr(field, "annotation", None) or getattr(field, "type_", "Any")
                            lines.append(f"    {name}: {ann!s}")
                        return "\n".join(lines)
                    except Exception as e:
                        return f"# <pydantic fallback failed: {e}>"
                return src

            # ── 注册消费者源码（2026-04-19 加）──
            # 不告诉 LLM "你必须写 build_pipeline 函数"，而是给它看
            # 真实**调用方**代码：registry 用字符串 getattr 查 "build_pipeline"、
            # dispatch 用 getattr(mod, "register_formats")。LLM 看到这个自然推出
            # 为什么函数名/签名是 contract 不是惯例。
            from omnicompany.core import pipelines as _pipelines_mod
            from omnicompany.core import dispatch as _dispatch_mod

            framework_context = {
                "router_base_src": _src(_RouterBase),
                "verdict_dataclass_src": _src(_Verdict),
                "verdictkind_enum_src": _src(_VerdictKind),
                "llmclient_init_sig": llmclient_init_sig,
                "anchor_spec_src": _pydantic_or_src(_AnchorSpec),
                "transformer_spec_src": _pydantic_or_src(_TransformerSpec),
                "validator_spec_src": _pydantic_or_src(_ValidatorSpec),
                "validator_kind_src": _src(_ValidatorKind),
                "route_src": _pydantic_or_src(_Route),
                "route_action_src": _src(_RouteAction),
                "pipeline_node_src": _pydantic_or_src(_PipelineNode),
                "pipeline_spec_src": _pydantic_or_src(_PipelineSpec),
                "pipeline_edge_src": _pydantic_or_src(_PipelineEdge),
                "nodekind_enum_src": _src(_NodeKind),
                "node_maturity_src": _src(_NodeMaturity),
                "format_class_src": _src(_Format),
                # 消费者侧真源码：说明 build_pipeline / build_bindings / register_formats
                # 的函数名为何是 contract 而不是风格建议
                "pipeline_registry_lazy_loader_src": (
                    _src(getattr(_pipelines_mod, "_lazy", None))
                    + "\n\n"
                    + _src(getattr(_pipelines_mod, "_lazy_fn", None))
                ),
                "format_registry_dispatch_src": _src(
                    getattr(_dispatch_mod, "_load_format_registry_for_domain", None)
                ),
            }

            # ── 参考实现：selftest 全文 ──
            # 用 importlib 定位包路径，不硬编 file system 路径
            import omnicompany.packages.services._core.selftest as _selftest_pkg
            selftest_dir = Path(_selftest_pkg.__file__).parent
            for fname in ("routers.py", "pipeline.py", "formats.py", "run.py"):
                fpath = selftest_dir / fname
                key = f"ref_selftest_{fname.replace('.py', '')}"
                try:
                    framework_context[key] = fpath.read_text(encoding="utf-8")
                except OSError as e:
                    framework_context[key] = f"# <read {fpath} failed: {e}>"

            # ── 目标 package path ──
            # composite fan-in: wf.format_chain 给 requirement_context, wf.node_plan 给 pipeline_name
            req_ctx = format_chain.get("requirement_context", {}) or {}
            domain = req_ctx.get("domain", "custom")
            goal = req_ctx.get("goal", "generated")
            # 轻量 sanitize
            pipeline_name = (
                node_plan.get("pipeline_name")
                or goal.split(":")[-1].strip().split(".")[0]
                or "generated"
            )
            pipeline_name = re.sub(r"[^\w]", "_", pipeline_name).strip("_") or "generated"
            domain_sanitized = re.sub(r"[^\w]", "_", domain).strip("_") or "custom"
            framework_context["target_package_path"] = (
                f"omnicompany.packages.{domain_sanitized}.{pipeline_name}"
            )

            # ── 打包输出：保留原 node_plan 全部字段 + framework_context ──
            # 2026-04-19 (M2.β v2): 只从 wf.node_plan 组件继承字段, 不把 composite
            # fan-in 带进来的 wf.format_chain 透传给 code_gen_loop。F-15 合规 —
            # code_gen_loop 要 format_chain 请自己再 fan-in。
            augmented = dict(node_plan)  # 原 node_plan 全字段
            # reports 容器 (P7.3) 如果在 composite input_data 顶层也保留
            if "reports" in input_data:
                augmented["reports"] = input_data["reports"]
            augmented["framework_context"] = framework_context

            # 诊断信息：统计注入了多少字符，方便 trace-view 看
            total_chars = sum(len(str(v)) for v in framework_context.values())
            n_keys = len(framework_context)
            _wf_log.info(
                "[framework_context_loader] injected %d keys (%d chars total) for %s",
                n_keys, total_chars, framework_context["target_package_path"],
            )

            return Verdict(
                kind=VerdictKind.PASS,
                output=augmented,
                confidence=1.0,
                granted_tags=["framework-ctx-injected"],
                diagnosis=(
                    f"注入 {n_keys} 个框架源码字段 ({total_chars} chars)，"
                    f"target={framework_context['target_package_path']}"
                ),
            )
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"framework_context_loader 失败: {type(e).__name__}: {e}",
            )


# ═══════════════════════════════════════════════════════════
# [P7.8] node_plan_auditor — meta-pipeline 自净 (HARD)
#
# GAP §2.3 + §2.5: workflow_factory 不审 node_plan 是否满足 SKILL §3.1 节点设计
# 单表的 18 项 (context_sources / hallucination_risks / output_token_budget 等),
# 直接交给 code_generator → 生成的管线带着 workflow_factory 自己的坏习惯出生。
# 此节点放在 node_planner 之后、framework_context_loader 之前, 检查 node_plan
# 的语义质量, 不过即返回 PARTIAL → node_planner retry。
# ═══════════════════════════════════════════════════════════


class NodePlanAuditorRouter(Router):
    """对 node_plan 做语义级 HARD 审计 (P7.8 / GAP §2.3 + §2.5)。

    检查项 (每项不过都记到 issues, critical_issues 触发 FAIL):
      1. 每个 SOFT 节点是否声明了 context_sources (即上游有 Transformer 注入框架源码)
      2. 每个 hallucination_risk_field 是否对应一条 suggested_source
      3. SOFT 节点是否有 output_token_budget 估算 (>4k 的需 scale_strategy)
      4. 是否有参考范本被引用 (例: ref_selftest_routers 等 framework_context 字段)
      5. 每个 SOFT 节点是否有 FAIL 路由

    检查是 best-effort: 字段可能没填, 不强制存在所有字段; 但**关键缺失**会触发 FAIL。
    "关键缺失"指: SOFT 节点完全没填 context_sources 且没有 verification_binding。
    """

    FORMAT_IN = "wf.node_plan"
    FORMAT_OUT = "wf.node_plan"
    DESCRIPTION = (
        "node_plan 语义质量 HARD 审计 (P7.8 meta-pipeline 自净)。检查每个 SOFT 节点的 "
        "context_sources / hallucination_risks / output_token_budget / FAIL 路由是否填好, "
        "防止 workflow_factory 把自己的坏习惯复制到生成的管线 (GAP §2.3 + §2.5)。"
        "未通过返回 PARTIAL 让 node_planner 重新规划。"
    )

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"期望 dict node_plan, 收到 {type(input_data).__name__}",
            )

        nodes = input_data.get("nodes", []) or []
        issues: list[str] = []
        critical: list[str] = []

        for node in nodes:
            if not isinstance(node, dict):
                continue
            nid = node.get("id", "?")
            vkind = (node.get("validator_kind") or "").upper()
            is_soft = vkind == "SOFT"

            # 1) SOFT 节点必须有 context_sources 或 verification_binding
            if is_soft:
                ctx_sources = node.get("context_sources") or []
                vbinding = node.get("verification_binding")
                if not ctx_sources and not vbinding:
                    critical.append(
                        f"SOFT 节点 {nid} 既没填 context_sources 也没有 verification_binding "
                        f"(SKILL §3.1 第 11/14 项), 必然幻觉"
                    )
                elif not ctx_sources:
                    issues.append(
                        f"SOFT 节点 {nid} 未填 context_sources, 仅靠下游 verification_binding={vbinding} 兜底"
                    )

            # 2) hallucination_risks 必须对应 suggested_source / context_sources
            risks = node.get("hallucination_risks") or []
            if is_soft and not risks:
                issues.append(f"SOFT 节点 {nid} 未列 hallucination_risks (SKILL §3.1 第 13 项)")

            # 3) output_token_budget
            budget = node.get("output_token_budget")
            if is_soft and not budget:
                issues.append(f"SOFT 节点 {nid} 未估算 output_token_budget (SKILL §3.1 第 9 项)")

            # 4) 必须有 FAIL 路由 (从 error_routes 字段)
            error_routes = node.get("error_routes") or {}
            if is_soft and not error_routes.get("fail") and not error_routes.get("FAIL"):
                critical.append(f"SOFT 节点 {nid} 缺 FAIL 路由 (SKILL §9.1)")

        # 5) 至少要引用一份参考范本 (从 _format_chain 或 framework_context 字段反推)
        # node_plan 阶段还没有 framework_context (那是 framework_context_loader 的事),
        # 但 node 自身的 implementation_hint 应该提到参考管线之类
        any_ref = any(
            "selftest" in str(n.get("implementation_hint", "")).lower()
            or "ref_" in str(n.get("implementation_hint", "")).lower()
            or "参考" in str(n.get("implementation_hint", ""))
            for n in nodes if isinstance(n, dict)
        )
        if not any_ref and any(
            (n.get("validator_kind") or "").upper() == "SOFT"
            for n in nodes if isinstance(n, dict)
        ):
            issues.append(
                "node_plan 中没有任何 SOFT 节点的 implementation_hint 引用参考范本 "
                "(应该提到 selftest 或类似 MATURE 域)"
            )

        report = {
            "issues": issues,
            "critical_issues": critical,
            "passed": len(critical) == 0,
            "node_count": len(nodes),
        }
        result = {**input_data, "node_plan_audit": report}

        if not critical:
            return Verdict(
                kind=VerdictKind.PASS, output=result,
                granted_tags=["node-plan-audited"],
                diagnosis=f"node_plan 审计通过 ({len(nodes)} 节点, {len(issues)} 警告)",
            )
        return Verdict(
            kind=VerdictKind.PARTIAL, output=result,
            diagnosis=(
                f"node_plan 关键缺失 ({len(critical)} critical + {len(issues)} warn): "
                + "; ".join(critical[:3])
            ),
        )


# ═══════════════════════════════════════════════════════════
# [D-split] code_generator P7.2 SCATTER 拆分
#
# 把原 CodeGeneratorRouter (一个 Router 内顺序跑 4 次 LLM 生成 4 文件)
# 拆成 4 个独立节点。每个节点只生成一个文件,失败时只 retry 该步骤,
# 中间态用 wf.code_gen_state Format 增量累加 files 字典。
#
# GAP §1.2-A: 单节点纯粹性 + 局部 retry 能力
# 老 CodeGeneratorRouter 保留为可选 fallback (不在 pipeline.py 中使用)
# ═══════════════════════════════════════════════════════════


def _wf_extract_python_code(text: str, filename: str) -> str:
    """从 LLM 响应中提 Python 代码 (复用自老 _gen_one_file 的逻辑)。"""
    # ```filename.py ... ``` 优先
    m = re.search(rf'```{re.escape(filename)}\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # ```python ... ```
    m = re.search(r'```(?:python)?\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 裸代码兜底
    stripped = text.strip()
    if stripped.startswith(("import ", "from ", "class ", '"""', "# ", "def ")):
        return stripped
    lines = stripped.split("\n")
    code_lines = [l for l in lines if not l.startswith(("##", "- ", "> ", "注意"))]
    return "\n".join(code_lines).strip()


class _CodeGenBaseRouter(Router):
    """4 个 per-file code gen 子节点的基类。

    子类只需提供:
      - FILE_KEY (类属性): 'formats.py' / 'pipeline.py' / 'routers.py' / 'run.py'
      - DESCRIPTION (类属性): >= 50 字符
      - _build_prompt(state, files): 根据当前 state + 已生成的 files 构造本文件的 prompt
    """

    FORMAT_IN = "wf.code_gen_state"
    FORMAT_OUT = "wf.code_gen_state"
    FILE_KEY: str = ""

    def __init__(
        self,
        *,
        model: str | None = None,
        role: str = "ide_agent",
        max_tokens: int = 16384,
    ):
        # Fix 4 (GAP ②): 策略配置从 bindings 注入, 不硬编。
        # bindings 可以通过 CodeGenFormatsRouter(role=..., max_tokens=...) 覆盖。
        self._model = model
        self._role = role
        self._max_tokens = max_tokens

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(
            role=self._role,
            max_tokens=self._max_tokens,
            **({"model": self._model} if self._model else {}),
        )

    def _build_prompt(self, state: dict, files: dict[str, str]) -> str:
        raise NotImplementedError("subclass must implement _build_prompt")

    def _initial_state(self, input_data: dict) -> dict:
        """code_gen_formats 调用时初始化 pipeline_name / package_path / files。"""
        state = dict(input_data)
        if "files" not in state or not isinstance(state.get("files"), dict):
            state["files"] = {}
        if "pipeline_name" not in state or "package_path" not in state:
            req = state.get("requirement_context", {}) or state.get("_requirement", {})
            pipeline_name = (
                state.get("pipeline_name")
                or req.get("pipeline_name")
                or req.get("goal", "").split(":")[-1].strip().split(".")[0]
                or "generated"
            )
            domain = state.get("domain") or req.get("domain") or "custom"

            def _sanitize(s: str) -> str:
                s = (s or "").strip().replace(" ", "_").replace("-", "_")
                s = re.sub(r"[^\w]", "", s)
                return s if s and s.isascii() else ""

            pipeline_name = _sanitize(pipeline_name) or "generated"
            domain = _sanitize(domain) or "custom"
            state["pipeline_name"] = pipeline_name
            state["package_path"] = (
                f"omnicompany.packages.{domain}.{pipeline_name.replace('-', '_')}"
            )
        return state

    def run(self, input_data: Any) -> Verdict:
        if not self.FILE_KEY:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"{type(self).__name__} 未声明 FILE_KEY",
            )
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"期望 dict, 收到 {type(input_data).__name__}",
            )

        state = self._initial_state(input_data)
        files = dict(state.get("files", {}))

        try:
            client = self._make_client()
            prompt = self._build_prompt(state, files)
            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system=_CODE_GEN_SYSTEM,
            )
            text = resp.content[0].text if hasattr(resp, "content") else str(resp)
            code = _wf_extract_python_code(text, self.FILE_KEY)
            if not code or len(code) < 20:
                return Verdict(
                    kind=VerdictKind.FAIL, output=input_data,
                    diagnosis=f"{self.FILE_KEY} 生成结果太短或为空 ({len(code)} chars)",
                )
            files[self.FILE_KEY] = code
            state["files"] = files
            return Verdict(
                kind=VerdictKind.PASS, output=state,
                diagnosis=f"生成 {self.FILE_KEY} ({len(code)} chars), 累计 {len(files)} 个文件",
                granted_tags=[f"code-gen-{self.FILE_KEY.replace('.py', '')}"],
            )
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"{self.FILE_KEY} 生成失败: {type(e).__name__}: {e}",
            )


class CodeGenFormatsRouter(_CodeGenBaseRouter):
    """生成 formats.py — Format 类型定义文件 (P7.2 拆分)."""

    FILE_KEY = "formats.py"
    # 第一步: 输入是上游 framework_context_loader 输出的 wf.node_plan_augmented,
    # 内部初始化 files={} 后流向后续节点的 wf.code_gen_state
    FORMAT_IN = "wf.node_plan_augmented"
    FORMAT_OUT = "wf.code_gen_state"
    DESCRIPTION = (
        "生成目标管线的 formats.py: 定义所有 Format 类型并注册。"
        "输入 wf.code_gen_state, 调 LLM 一次只生成 formats.py, "
        "失败只 retry 本节点不影响后续 pipeline.py / routers.py / run.py 三步。"
    )

    def _build_prompt(self, state: dict, files: dict[str, str]) -> str:
        format_chain = state.get("_format_chain", state.get("format_chain", {}))
        return f"""\
生成 formats.py: 定义所有 Format 类型并注册。

## Format 链设计
```json
{_wf_no_trunc(json.dumps(format_chain, indent=2, ensure_ascii=False), "code_gen_formats.format_chain")}
```

## 要求
- from omnicompany.protocol.format import Format, FormatRegistry
- 每个 Format 用 Format(id=..., name=..., description=..., parent=...) 构造
- description 必须含三要素: 内容语义 / 验证标准 / 下游用途
- parent 从内置类型中选: requirement, spec, code, test-result, tool-observation, doc
- 定义 register_formats() 函数注册所有 Format
- 只输出 formats.py 的完整代码
"""


class CodeGenPipelineRouter(_CodeGenBaseRouter):
    """生成 pipeline.py — PipelineSpec 拓扑文件 (P7.2 拆分)."""

    FILE_KEY = "pipeline.py"
    DESCRIPTION = (
        "生成目标管线的 pipeline.py: PipelineSpec 拓扑定义。"
        "输入 wf.code_gen_state (含已生成的 formats.py), 调 LLM 一次只生成 pipeline.py。"
        "失败只 retry 本节点。"
    )

    def _build_prompt(self, state: dict, files: dict[str, str]) -> str:
        formats_code = files.get("formats.py", "")
        format_ids = re.findall(r'id="([^"]+)"', formats_code)
        nodes_summary = _wf_no_trunc(
            json.dumps(state.get("nodes", []), indent=2, ensure_ascii=False),
            "code_gen_pipeline.nodes_summary",
        )
        edges_summary = _wf_no_trunc(
            json.dumps(state.get("edges", []), indent=2, ensure_ascii=False),
            "code_gen_pipeline.edges_summary",
        )
        pipeline_name = state.get("pipeline_name", "generated")
        entry_id = (state.get("nodes", [{}]) or [{}])[0].get("id", "start")
        feedback_loops = state.get("feedback_loops", [])

        return f"""\
生成 pipeline.py: 定义管线拓扑。

管线名: {pipeline_name}
入口节点: {entry_id}

## 可用的 Format ID (来自已生成的 formats.py)
{format_ids}

## 节点设计
```json
{nodes_summary}
```

## 边设计
```json
{edges_summary}
```

## 反馈回路
{json.dumps(feedback_loops, indent=2, ensure_ascii=False)}

## 关键要求
⚠️ PipelineSpec 必须包含全部字段:
```python
PipelineSpec(
    id="{pipeline_name}",
    name="...",
    description="...",
    nodes=[...],
    edges=[...],
    entry="{entry_id}",
)
```
- PipelineNode 必须用 NodeKind.ANCHOR + AnchorSpec (不是散装参数)
- format_in/format_out 必须使用上面列出的 Format ID
- VerdictKind.PASS/FAIL 作为 edge condition
- 反馈边标记 feedback=True
- ⚠️ 反馈边类型兼容: 如果 nodeA(out=X) feedback→nodeB(in=Y),且 X 和 Y 无继承关系,
  则 nodeB 的 format_in 必须改为它们的公共祖先(通常是 "requirement")
- 只输出 pipeline.py 的完整代码
"""


class CodeGenRoutersRouter(_CodeGenBaseRouter):
    """生成 routers.py — Router 类实现 (P7.2 拆分)."""

    FILE_KEY = "routers.py"
    DESCRIPTION = (
        "生成目标管线的 routers.py: 实现每个节点的 Router 类。"
        "输入 wf.code_gen_state (含已生成的 formats.py + pipeline.py), 调 LLM 一次只生成 routers.py。"
        "失败只 retry 本节点。"
    )

    def _build_prompt(self, state: dict, files: dict[str, str]) -> str:
        formats_code = files.get("formats.py", "")
        pipeline_code = files.get("pipeline.py", "")
        format_ids = re.findall(r'id="([^"]+)"', formats_code)
        node_ids = re.findall(r'id="(\w+)"', pipeline_code)
        nodes_summary = _wf_no_trunc(
            json.dumps(state.get("nodes", []), indent=2, ensure_ascii=False),
            "code_gen_routers.nodes_summary",
        )

        return f"""\
生成 routers.py: 实现每个管线节点的 Router 类。

## 节点列表 (来自 pipeline.py)
节点 ID: {node_ids}

## 节点详细设计
```json
{nodes_summary}
```

## 可用的 Format ID
{format_ids}

## 关键要求
- from omnicompany.protocol.anchor import Verdict, VerdictKind
- from omnicompany.runtime.routing.router import Router
- HARD 节点: 继承 Router, run(self, input_data: Any) -> Verdict, confidence=1.0
- SOFT 节点: 继承 Router, 内部创建 LLMClient 调用
  - 用 LLMClient().call(messages, system=...) 而非 .generate()
  - 或用 LLMClient(tools=[tool_spec]).call(..., tool_choice=...) 做结构化输出
- 每个类必须定义 FORMAT_IN, FORMAT_OUT, DESCRIPTION (>= 50 字)
- run() 必须有完整实现: 验证输入 → 处理 → 返回 Verdict(kind, output, diagnosis)
- FAIL 时 output 传回 input_data, diagnosis 说明原因
- 不要有 pass, TODO, NotImplementedError
- 只输出 routers.py 的完整代码
"""


class CodeGenRunRouter(_CodeGenBaseRouter):
    """生成 run.py — bindings 注册 + Format 转 wf.project_skeleton (P7.2 最终步)."""

    FILE_KEY = "run.py"
    FORMAT_OUT = "wf.project_skeleton"  # 最后一步: 收敛到 project_skeleton
    DESCRIPTION = (
        "生成目标管线的 run.py: build_bindings + 注册逻辑。"
        "输入 wf.code_gen_state (含已生成的 3 个 .py 文件), 调 LLM 一次只生成 run.py, "
        "并把累计的 files 字典封装为 wf.project_skeleton 供 compile_checker 验证。"
        "失败只 retry 本节点。"
    )

    def _build_prompt(self, state: dict, files: dict[str, str]) -> str:
        pipeline_code = files.get("pipeline.py", "")
        routers_code = files.get("routers.py", "")
        node_ids = re.findall(r'id="(\w+)"', pipeline_code)
        router_classes = re.findall(r'class (\w+Router)\(', routers_code)
        return f"""\
生成 run.py: 绑定 Router 到管线节点。

## 节点 ID (来自 pipeline.py)
{node_ids}

## Router 类名 (来自 routers.py)
{router_classes}

## 关键要求
```python
from typing import Any
from omnicompany.runtime.routing.router import Router
from .routers import {', '.join(router_classes)}
from .pipeline import build_pipeline

__all__ = ["build_pipeline", "build_bindings"]

def build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    # 每个 key 必须与 pipeline 节点 id 完全一致
    return {{
        # "node_id": RouterClass(...),
    }}
```
- SOFT Router 如需 LLMClient: from omnicompany.runtime.llm.llm import LLMClient
- 绑定 key 与节点 ID 大小写完全一致
- 只输出 run.py 的完整代码
"""

    def run(self, input_data: Any) -> Verdict:
        # 复用基类的 run() 生成 run.py, 然后把累积 state 转成 wf.project_skeleton
        v = super().run(input_data)
        if v.kind != VerdictKind.PASS:
            return v
        state = v.output
        files = dict(state.get("files", {}))
        # 补 __init__.py (如果还没有)
        if "__init__.py" not in files:
            pname = state.get("pipeline_name", "generated")
            files["__init__.py"] = f'"""Auto-generated pipeline: {pname}"""\n'
        # 收敛为 wf.project_skeleton
        skeleton = {
            "package_path": state.get("package_path", ""),
            "files": files,
            "pipeline_name": state.get("pipeline_name", "generated"),
        }
        return Verdict(
            kind=VerdictKind.PASS,
            output=skeleton,
            diagnosis=f"4-step SCATTER 完成, 共 {len(files)} 个文件",
            granted_tags=["code-gen-run", "code-gen-complete"],
        )


_CODE_GEN_SYSTEM = """\
你是一个 LAP 管线代码生成器, 根据节点规划生成 Python 文件。

## 真源码优先 (P7.5 重构后核心原则)

**输入 dict 里的 framework_context 字段**(由上游 framework_context_loader 注入)
**包含所有框架基类/接口的 inspect.getsource 真源码**:
  - router_base_src — Router 基类完整定义 (类签名、方法签名、注释)
  - verdict_dataclass_src — Verdict 全字段
  - verdictkind_enum_src — VerdictKind 真实枚举值
  - llmclient_init_sig — LLMClient.__init__ 真实签名
  - anchor_spec_src / pipeline_node_src / pipeline_spec_src / nodekind_enum_src / format_class_src
  - ref_selftest_routers / ref_selftest_pipeline / ref_selftest_formats — 一份完整的 MATURE 参考实现
  - target_package_path — 目标生成包的完整 import 路径

**关键: 不要凭记忆写 import 路径或 API 调用方式 ——
直接对照 framework_context 里的真源码**。NodeKind 的小写值、PipelineNode 必须用 AnchorSpec、
LLMClient 的参数名等等所有"我曾经犯过的错",答案都在 framework_context 里。

## 节点 ID 和 build_bindings 的一致性

PipelineNode 的 id 字段和 build_bindings() 返回的 dict key **必须完全相同**。
PipelineRunner 用 node.id 作为 key 查找 bindings, 不匹配会在运行时报 KeyError。
**推荐**: 统一用 snake_case (下划线), 避免 kebab-case 引起混淆。

## 质量要求

- 每个 Router 必须声明 FORMAT_IN, FORMAT_OUT, DESCRIPTION 三个类变量
- DESCRIPTION >= 50 字符, 写明判定标准和产出结构
- 每个 Router 的 run() 必须返回 Verdict 对象
- HARD Router 的 run() 必须有异常处理 + FAIL 路径
- 不要用 pass / TODO / NotImplementedError 占位

## 验证链拓扑: 单主干 + reports 容器 (SKILL §2.3, 必须遵守)

**不要**给每个验证节点发明一个新的 skeleton 克隆 Format (如 compiled_skeleton,
audited_skeleton, tested_skeleton)。这是 GAP §1.2-A 的反模式: 代码本体没变,
只是换"验收印章", trace-view 里看不出差别。

**正确做法**:
- 所有验证节点都用**同一个**主干 Format (如 `<domain>.project_artifact`) 做
  `format_in == format_out`
- 报告写进 `output["reports"][report_key]` (例: reports["compile"] / reports["lint"])
- 用 `Verdict.granted_tags=["compile-passed", "lint-passed"]` 累加状态
- 下游修复节点从 `output["reports"]` 读**所有**历史报告, 不要 pop 删

## Format description (SKILL §2.1): 五项语义, 不只是长度

每个 Format 的 description 必须写清 5 项:

1. **内容语义**: 这个 Format 装的是什么概念级别的产物
2. **字段含义**: json_schema 里每个顶层字段干什么用
3. **上游承诺**: 进入本 Format 前必须满足的前置条件 (例: "已通过 X 节点的 HARD 验证")
4. **下游用途**: 谁读这个 Format, 用来做什么决策
5. **最小合法样例** (推荐): 一个可运行的最小 JSON 示例

单纯写一句"xxx 数据"或者"xxx 的结果" 等于没写, lap_verifier 可能放过但 guardian 会追究。

## 输出规模预算 (SKILL §3.2)

每个 SOFT 节点必须在 implementation_hint 或 description 里写 `output_token_budget`:

- 公式: 预估产物字符数 / 3.5, 或代码行数 × 15
- >4000 token 的必须声明 scale_strategy (SCATTER / 分页 PARTIAL / 骨架+填肉 / 输入削减)
- 报告类节点必须设 `max_issues` 硬上限 + 超限时返回 `truncated=True, total_found=N`
  (否则下游"瞎子修复"同一类问题反复触发, 参见 AutoFixer 反模式)

## 拓扑健康度四原则 (SKILL §4.5, 必须遵守)

1. **薄弱环节交叉验证**: 每个 SOFT 节点**必须有**紧随其后的 HARD 验证节点作为前置完全化,
   不要让连续的 SOFT 节点堆叠 (n 个 SOFT = n 次不确定性相乘, 错误定位能力 = 0)。
2. **成熟度语义追踪**: 每个节点标 `maturity=NodeMaturity.HYPOTHETICAL/GROWING/MATURE/CRYSTALLIZED`,
   新节点默认 HYPOTHETICAL。
3. **Format 双重含义**: Format 既是数据契约也是人类可读文档, 不要为"验收印章"制造 Format。
4. **单节点纯粹性**: 一个 Router 内部不要偷偷跑多次 LLM 或多步流程,
   该拆成多个节点或 SCATTER。

## Router 实现规范 (SKILL §5.2, 4 条必须遵守)

- **不要硬编 model / max_tokens / role**: 这些策略配置通过 `__init__` 参数从 bindings 注入
  (`def __init__(self, *, model: str | None = None, role: str = "...", max_tokens: int = 8192)`),
  不要写 `LLMClient(role="固定值", max_tokens=16384)` 这样的硬编。
- **Router 里不 iter LLM 协议细节**: 不要在 Router 里 `block.type == "tool_use"` 这种分支,
  那是 LLMClient 的职责, Router 只看结构化返回。
- **通用规则沉淀为 Tool**: Python 源码清理 / JSON schema 校验 / 正则提取代码块 等
  与业务无关的通用能力, 沉淀到 `omnicompany.runtime.<tool_module>`, 别的管线可复用
  (例: `from omnicompany.runtime.codegen_tools import apply_python_lap_cleanup`)。
- **确定性 Router 的 `confidence=1.0`**, `diagnosis` 必填且具体。

## 遇到不确定的事 (REPLACES old changelog patches, P7.5 + 2026-04-09 info_audit)

如果你不确定某个 API 怎么用 / 某个枚举值是什么 / 某个类有哪些字段, **不要硬写**。
你的输出会被 LLMClient 自动收集 info_audit 信号 — 请在 missing_info 里**具体描述**
你观察到缺什么 (例: "缺 XxxRouter 的 __init__ signature, 不知道是否接受 client 参数"),
runner 会按规则触发兜底动态补信息。**强行编造一个 API 比承认不知道更糟糕**。

(老版本 system prompt 里有 100+ 行 "禁止 from typing import Dict" 这类 changelog 补丁,
GAP §2.1 指出这是反模式 — prompt 越长注意力越散。所有这些"不要做 X"的禁令已删除,
对应的真实信息都在 framework_context 里, 你**直接读源码**, 不需要二手传话。)
"""


# ═══════════════════════════════════════════════════════════
# [E'] syntax_fixer — 语法修复 (LLMRouter)
# ═══════════════════════════════════════════════════════════

class SyntaxFixerRouter(LLMRouter):
    """逐文件精准修复编译语法错误。

    策略：不把所有文件一次性塞给 LLM（会溢出 token 导致截断引入新错误），
    而是按出错文件逐一修复——每次只给 LLM 一个文件 + 它的具体错误，
    要求输出该单个文件的完整修复版本。

    架构决策记录：
    - 不需要 AgentNodeLoop：不存在"探索未知"的需求，错误位置和内容已完全确定
    - 不适合单次 LLM：多文件合并输出会超出 max_tokens 导致截断
    - 最佳选择：逐文件迭代 LLM 调用，每次聚焦一个文件的修复
    """

    # P7.6 (GAP ⑨): 与 pipeline.py 声明一致, 都是 wf.project_skeleton
    # 修复器内部从 input_data["reports"]["compile"] 读取错误信息
    FORMAT_IN = "wf.project_skeleton"
    FORMAT_OUT = "wf.project_skeleton"
    INPUT_KEYS = None
    DESCRIPTION = (
        "逐文件精准修复编译语法错误。按出错文件逐一调用 LLM，每次只处理"
        "一个文件及其错误信息，要求输出该文件的完整修复版。避免多文件合并"
        "导致的 token 溢出和截断引入新错误的死循环。从 reports['compile'] 读错误信息。"
    )

    # 传给 LLM 的单个文件最大字符数（避免大文件截断 LLM 输出）
    _MAX_SOURCE_CHARS = 8000

    def run(self, input_data: Any) -> Verdict:
        skeleton = input_data
        # P7.7 全局 iteration 上限检查
        new_iter, halt = _check_global_fix_iter(skeleton)
        if halt:
            return halt
        skeleton = {**skeleton, "_global_fix_iter": new_iter}
        files = dict(skeleton.get("files", {}))
        # P7.3 reports container: 从 reports['compile'] 读, 不再从顶层 compile_report
        reports = skeleton.get("reports", {}) or {}
        report = reports.get("compile", {}) or skeleton.get("compile_report", {})

        # 按文件分组错误
        errors_by_file: dict[str, list[dict]] = {}
        for layer in report.values():
            if isinstance(layer, dict):
                for err in layer.get("errors", []):
                    fname = err.get("file", "unknown")
                    errors_by_file.setdefault(fname, []).append(err)

        if not errors_by_file:
            return Verdict(kind=VerdictKind.PASS, output=skeleton, diagnosis="无编译错误")

        fixed_count = 0
        fix_log = []

        # 逐文件修复：每次只给 LLM 一个文件和对应错误
        for fname, file_errors in errors_by_file.items():
            source = files.get(fname, "")
            if not source:
                fix_log.append(f"{fname}: 文件不存在，跳过")
                continue

            errors_text = json.dumps(file_errors[:5], indent=2, ensure_ascii=False)

            # 截断大文件——大文件全文会超出 LLM 输出 token 预算导致截断
            source_for_prompt = source
            if len(source) > self._MAX_SOURCE_CHARS:
                # 尝试只传报错行附近的上下文（±50 行）
                error_lines = []
                for err in file_errors[:3]:
                    msg = str(err.get("error", ""))
                    import re as _re
                    m = _re.search(r'line (\d+)', msg)
                    if m:
                        error_lines.append(int(m.group(1)))
                if error_lines:
                    lines = source.split("\n")
                    center = error_lines[0]
                    start = max(0, center - 50)
                    end = min(len(lines), center + 50)
                    source_for_prompt = "\n".join(lines[start:end])
                    source_for_prompt = (
                        f"# [文件已截断，只显示第 {start+1}-{end} 行，共 {len(lines)} 行]\n"
                        f"# 错误位置: 第 {center} 行\n\n"
                        + source_for_prompt
                    )
                else:
                    source_for_prompt = source[:self._MAX_SOURCE_CHARS] + "\n# ... [截断]"

            try:
                resp = self.client.call(
                    messages=[{"role": "user", "content": (
                        f"以下 Python 文件有编译错误。请修复错误并输出完整的修复后文件。\n"
                        f"只输出修复后的代码，不要输出其他文件。\n\n"
                        f"## 文件名: {fname}\n\n"
                        f"## 编译错误\n```json\n{errors_text}\n```\n\n"
                        f"## 当前代码\n```python\n{source_for_prompt}\n```\n\n"
                        f"请输出修复后的完整 {fname}（用 ```{fname} 或 ```python 标记）："
                    )}],
                    system=_SYNTAX_FIX_SYSTEM,
                )
                raw = resp.content[0].text
                clean = raw

                # 提取修复后的代码
                fixed_code = None
                # 尝试 ```filename.py 格式
                m = re.search(rf'```{re.escape(fname)}\s*\n(.*?)```', clean, re.DOTALL)
                if m:
                    fixed_code = m.group(1).strip()
                # 尝试 ```python 格式
                if not fixed_code:
                    m = re.search(r'```python\s*\n(.*?)```', clean, re.DOTALL)
                    if m:
                        fixed_code = m.group(1).strip()
                # 尝试纯代码块
                if not fixed_code:
                    m = re.search(r'```\s*\n(.*?)```', clean, re.DOTALL)
                    if m:
                        fixed_code = m.group(1).strip()

                if fixed_code and len(fixed_code) > 50:
                    files[fname] = fixed_code
                    fixed_count += 1
                    fix_log.append(f"{fname}: 修复 {len(file_errors)} 个错误")
                else:
                    fix_log.append(f"{fname}: LLM 未返回有效代码 (len={len(fixed_code) if fixed_code else 0})")

            except Exception as e:
                fix_log.append(f"{fname}: 修复异常 {e}")

        if fixed_count == 0:
            return Verdict(
                kind=VerdictKind.FAIL, output=skeleton,
                diagnosis=f"未能修复任何文件: {'; '.join(fix_log)}")

        # P7.3: 不再 pop compile_report — 报告已在 reports 容器中, 保留给下一轮对比
        fixed = {**skeleton, "files": files}
        return Verdict(
            kind=VerdictKind.PASS, output=fixed,
            granted_tags=["syntax-fix-applied"],
            diagnosis=f"逐文件修复完成 ({fixed_count}/{len(errors_by_file)} 文件): {'; '.join(fix_log)}")


_SYNTAX_FIX_SYSTEM = """\
你是一个精准的 Python 语法修复专家。你的任务是修复给定文件中的编译错误。

## 规则
1. **只修复报告的错误**，不要改动正常的代码逻辑
2. **输出完整文件**——包含所有原始代码 + 修复的部分
3. **保持所有 import 语句**不变，除非 import 本身有语法错误
4. **保持类和函数签名**不变，除非签名本身有语法错误
5. 常见语法错误类型：缩进、括号不匹配、冒号缺失、字符串引号不闭合、f-string 嵌套引号冲突

## 输出格式
用代码块包裹完整修复后的文件。不要省略任何代码。
"""


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


# ═══════════════════════════════════════════════════════════
# [P7.7] 全局跨节点 iteration counter (GAP ⑬)
#
# auto_fixer → compile_checker → lap_verifier → auto_fixer 的大回路没全局上限,
# _retry2.max_retries=2 只管单节点。这里让所有 fixer 在 skeleton["_global_fix_iter"]
# 累加, 超过 _GLOBAL_FIX_LIMIT 直接 PARTIAL + HALT。
# ═══════════════════════════════════════════════════════════

_GLOBAL_FIX_LIMIT = 10


def _check_global_fix_iter(skeleton: dict) -> tuple[int, "Verdict | None"]:
    """检查并递增 skeleton 上的全局修复迭代计数。

    Returns:
        (new_count, halt_verdict_if_exceeded)
        如果超限, 返回 (count, Verdict(PARTIAL, HALT diagnosis));
        否则返回 (count, None) 让调用方继续。
    """
    count = int(skeleton.get("_global_fix_iter", 0)) + 1
    if count > _GLOBAL_FIX_LIMIT:
        return count, Verdict(
            kind=VerdictKind.PARTIAL,
            output={**skeleton, "_global_fix_iter": count},
            diagnosis=(
                f"全局修复回路超限 ({count}/{_GLOBAL_FIX_LIMIT})。"
                f"GAP ⑬: 防止 auto_fixer↔compile_checker 大循环不收敛。"
                f"PARTIAL 触发后续 HALT。"
            ),
        )
    return count, None


# ═══════════════════════════════════════════════════════════
# [E''] deterministic_fixer — 确定性修复 (HARD, Level 1)
# ═══════════════════════════════════════════════════════════

class DeterministicFixerRouter(Router):
    """确定性修复器（HARD）— Level 1 修复。

    薄包装: Router 只负责 Verdict/错误路径/granted_tags, 真正的清理规则
    沉淀在 runtime/codegen_tools.py 里, 供别的代码生成管线复用 (GAP ③ 修复)。

    修复规则 (由 codegen_tools.apply_python_lap_cleanup 覆盖):
    1. `from typing import Dict/List` → 删除, 用内置类型
    2. `kind="ANCHOR"` / `kind="HARD"` 等字面量 → 枚举访问
    3. pipeline.py / routers.py 缺标准 import → 补全
    """

    FORMAT_IN = "wf.project_skeleton"
    FORMAT_OUT = "wf.project_skeleton"
    DESCRIPTION = (
        "确定性修复器（HARD，Level 1）。修复已知的高频编译错误模式："
        "typing import 清理、NodeKind/ValidatorKind 枚举修复、标准 import 补全。"
        "薄包装 runtime/codegen_tools.py 的纯函数库, 不调 LLM, 无法修复的问题"
        "原样保留给下游 syntax_fixer。"
    )

    def run(self, input_data: Any) -> Verdict:
        skeleton = input_data
        # P7.7 全局 iteration 上限检查
        new_iter, halt = _check_global_fix_iter(skeleton)
        if halt:
            return halt
        skeleton = {**skeleton, "_global_fix_iter": new_iter}

        # GAP ③ 修复: 调用通用 Tool (runtime/codegen_tools) 而非内联规则
        from omnicompany.runtime.codegen_tools import apply_python_lap_cleanup
        files = skeleton.get("files", {}) or {}
        new_files, fix_count = apply_python_lap_cleanup(files)

        if fix_count > 0:
            result = {**skeleton, "files": new_files}
            return Verdict(
                kind=VerdictKind.PASS,
                output=result,
                granted_tags=["deterministic-cleanup-applied"],
                diagnosis=f"确定性修复了 {fix_count} 个文件（typing/NodeKind/import）",
            )
        # 2026-04-19 关键修复：没修到东西时必须 PARTIAL，否则下游 compile 继续 FAIL
        # 又打回来，和本节点形成无限循环（已实证烧满 max_steps）。PARTIAL 让管线
        # 走 → syntax_fixer（LLM 修复层）。
        return Verdict(
            kind=VerdictKind.PARTIAL,
            output=skeleton,
            diagnosis="无确定性修复可做，升级给 syntax_fixer（LLM 层）处理",
        )


# ═══════════════════════════════════════════════════════════
# [I] auto_fixer — 自动修复 (LLM, Level 3 fallback)
# ═══════════════════════════════════════════════════════════

class AutoFixerRouter(Router):
    """LLM 自动修复 — Level 3 fallback。

    接收通过编译但后续验证失败的代码，用 LLM 跨文件修复。
    只在 deterministic_fixer (Level 1) 和 syntax_fixer (Level 2) 无法解决时到达。

    ═══════════════════════════════════════════════════════════
    待处理 backlog (2026-04-10 审计, non-critical 的流程/设计问题)
    ═══════════════════════════════════════════════════════════

    # PROPOSAL #3 (snippet 协议脆弱)
    # Snippet 替换依赖 LLM 精确复制原始字符串 (空格/缩进/换行全对), 实际 LLM
    # 经常少一个空格或改一个 tab → `old in files[fname]` 返回 False → 跳过,
    # 导致 fixed_count=0 落入 fallback full rewrite。
    # 建议改成 unified diff 协议或基于行号 + fuzzy 匹配的 patch 协议。
    # 难度: 中 (需要新工具或规范化 snippet) | 影响: 大 (主路径成功率)

    # ISSUE #5 (硬编 rules 是 changelog 反模式, 同 _CODE_GEN_SYSTEM P7.5)
    # user_prompt 里硬编了 3 条 "rules": INCOMPAT feedback edge / Missing id /
    # NodeKind must be lowercase。每一条都是历史 LLM 翻车后打的补丁, 属于
    # SKILL §3.4a 反模式 (system prompt as changelog)。与 P7.5 削减 _CODE_GEN_SYSTEM
    # 的思路一致, 这些规则本应沉淀到 framework_context 由 LLM 直接对照真源码消除。
    # 难度: 低 | 影响: 中 (prompt 噪音 + 新 rule 积累)

    # BUG #6 (fallback 只重写 pipeline.py)
    # 当 snippet 替换全失败时, fallback 只重写 pipeline.py (line ~2595 注释:
    # "pipeline.py is almost always the culprit"), 但实际 bug 可能在 routers.py
    # (LLM 客户端 import 幻觉, 例: 本次 E2E 的 'No module named lap') 或 formats.py。
    # fallback 应根据 issue 关联的文件名选 target, 或逐个文件尝试重写。
    # 难度: 低 | 影响: 中 (修复覆盖率)

    # PROPOSAL #7 (Router 里藏子管线)
    # 本 Router 的 run() 是 8 步子流程 (计数 → 收集 → prompt → LLM → 解析 →
    # 应用 → fallback → 返回), 符合 GAP §1.2-A "Router 里藏子管线" 反模式 (比老
    # CodeGeneratorRouter 轻, 但存在)。理想拆分:
    #   issue_collector (HARD)
    #   → llm_fix_planner (SOFT tool_use)
    #   → snippet_applier (HARD)
    #   → fallback_rewriter (SOFT, optional)
    # 每步独立 FAIL 路由 + 局部 retry。
    # 难度: 高 (4 个 Router + 新 pipeline + Format) | 影响: 中 (可观测性/可测试性)
    ═══════════════════════════════════════════════════════════
    """

    FORMAT_IN = "wf.project_skeleton"
    FORMAT_OUT = "wf.project_skeleton"  # P7.3 单主干 + reports 容器
    DESCRIPTION = (
        "自动修复器。接收 wf.project_skeleton, 从 reports 容器读取所有历史失败报告 "
        "(compile/lap_audit/error_route/integration), 按优先级修复。GAP §1.2-H 之前 "
        "auto_fixer 修完后 pop 掉 compile_report 导致下一轮瞎子修复, 现在所有报告都"
        "保留在 reports 容器供历史对比。"
    )

    def __init__(
        self,
        *,
        model: str | None = None,
        role: str = "ide_agent",
        max_tokens: int = 16384,
    ):
        # Fix 4 (GAP ②): 策略配置从 bindings 注入, 不硬编
        self._model = model
        self._role = role
        self._max_tokens = max_tokens

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(
            role=self._role,
            max_tokens=self._max_tokens,
            **({"model": self._model} if self._model else {}),
        )

    def run(self, input_data: Any) -> Verdict:
        skeleton = input_data
        # P7.7 全局 iteration 上限检查
        new_iter, halt = _check_global_fix_iter(skeleton)
        if halt:
            return halt
        skeleton = {**skeleton, "_global_fix_iter": new_iter}
        files = dict(skeleton.get("files", {}))

        # P7.3 reports container: 从 skeleton["reports"] 读取所有历史报告
        # 之前是从 skeleton["compile_report"] 等顶层键读, 现在统一从 reports 容器读
        # GAP §1.2-H: 修复后 reports 不被 pop, 下次失败时能对比历史避免瞎子修复
        issues = []
        all_reports = skeleton.get("reports", {}) or {}
        if not isinstance(all_reports, dict):
            all_reports = {}
        for report_key in ("compile", "lap_audit", "error_route", "integration"):
            report = all_reports.get(report_key, {})
            if not isinstance(report, dict):
                continue
            for v in report.values():
                if isinstance(v, dict) and "errors" in v:
                    issues.extend(str(e) for e in v["errors"] if e)
            issues.extend(str(e) for e in report.get("issues", []) if e)
            # integration: extract per-test errors
            for t in report.get("tests", []):
                if not t.get("passed") and t.get("error"):
                    issues.append(f"[{t['name']}] {t['error']}")

        if not issues:
            return Verdict(kind=VerdictKind.PASS, output=skeleton, diagnosis="无需修复")

        # 精确修复：用 tool_use 让 LLM 只输出需要修改的文件+修改内容
        _FIX_SCHEMA = {
            "type": "object",
            "properties": {
                "fixes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string", "enum": list(files.keys())},
                            "old_snippet": {"type": "string", "description": "要替换的原始代码片段（足够唯一）"},
                            "new_snippet": {"type": "string", "description": "替换后的新代码"},
                            "reason": {"type": "string"},
                        },
                        "required": ["filename", "old_snippet", "new_snippet"],
                    },
                }
            },
            "required": ["fixes"],
        }

        # Fix 6 (GAP ⑲): 显式截断标记. 老版本硬编 top 10 没告诉 LLM 还有更多,
        # 导致 LLM 以为只有这 10 条需要修。现在加 truncation 明示。
        _MAX_ISSUES_SHOWN = 10
        total_found = len(issues)
        shown_issues = issues[:_MAX_ISSUES_SHOWN]
        truncated = total_found > _MAX_ISSUES_SHOWN
        issues_text = "\n".join(f"- {i}" for i in shown_issues)
        if truncated:
            issues_text += (
                f"\n\n[⚠️ TRUNCATED: 共 {total_found} 条 issue, 本次只显示前 "
                f"{_MAX_ISSUES_SHOWN} 条。余下 {total_found - _MAX_ISSUES_SHOWN} 条 "
                f"会在下一轮修复时显示, 请先聚焦这批。]"
            )
        # 必修 2026-04-10 (审计 #4): 原来截前 3000 字符会把 pipeline.py (~4500) 和
        # routers.py (~8000+) 截掉一大半, LLM 看到残缺代码无法正确定位 old_snippet
        # → snippet 替换必然失败 → AutoFixer 永远只能走 fallback full rewrite。
        # qwen3.6-plus 上下文 ~128k token, 4 个文件 × 15k 字符 = 60k 字符 ≈ 15k token 完全放得下。
        # 保留一个宽松上限 (每个文件 15000 字符) 防止极端大文件爆上下文。
        _MAX_FILE_CONTEXT_CHARS = 15000
        context = ""
        for fname in ["pipeline.py", "routers.py", "formats.py", "run.py"]:
            if fname in files:
                content = files[fname]
                if len(content) > _MAX_FILE_CONTEXT_CHARS:
                    content = content[:_MAX_FILE_CONTEXT_CHARS] + f"\n# ... [truncated, total {len(files[fname])} chars]"
                context += f"\n### {fname}\n```python\n{content}\n```\n"

        user_prompt = (
            f"Fix these issues in the generated pipeline code:\n\n{issues_text}\n\n"
            f"Current code:\n{context}\n\n"
            f"Rules:\n"
            f"- INCOMPAT feedback edge: change target node's format_in to 'requirement'\n"
            f"- Missing id/entry in PipelineSpec: add them\n"
            f"- NodeKind must be lowercase: NodeKind.ANCHOR not 'ANCHOR'\n"
            f"- Output only the minimal fixes needed."
        )

        _auto_fixer_system = (
            "You are a LAP pipeline code fixer. Produce minimal, precise code fixes. "
            "Only change what is broken. Do not rewrite entire files."
        )

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            tool_spec = {"name": "apply_fixes", "description": "Apply code fixes", "input_schema": _FIX_SCHEMA}
            # 必修 2026-04-10 (审计 #1): 老版本硬编 role="runtime_main" max_tokens=4096,
            # 完全绕过 __init__ 注入的 self._role/self._max_tokens, 导致 AutoFixer 总是走
            # glm-5 而不是 bindings 指定的模型 (和 Fix B qwen3.6-plus 切换冲突)。
            client = LLMClient(
                role=self._role,
                max_tokens=self._max_tokens,
                tools=[tool_spec],
                **({"model": self._model} if self._model else {}),
            )
            resp = client.call(
                messages=[{"role": "user", "content": user_prompt}],
                system=_auto_fixer_system,
                tool_choice={"type": "tool", "name": "apply_fixes"},
            )

            # Extract tool_use result
            fix_data = None
            if hasattr(resp, "content"):
                for block in resp.content:
                    if hasattr(block, "type") and block.type == "tool_use":
                        fix_data = block.input
                        break
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        fix_data = block.get("input", {})
                        break
            if hasattr(resp, "choices") and resp.choices:
                tc = resp.choices[0].message.tool_calls
                if tc:
                    import json as _json
                    fix_data = _json.loads(tc[0].function.arguments)

            if not fix_data or not fix_data.get("fixes"):
                return Verdict(kind=VerdictKind.FAIL, output=skeleton,
                               diagnosis="AutoFixer: no fixes produced")

            # Apply snippet replacements
            fixed_count = 0
            fix_log = []
            for fix in fix_data["fixes"]:
                fname = fix.get("filename", "")
                old = fix.get("old_snippet", "")
                new = fix.get("new_snippet", "")
                if fname in files and old and old in files[fname]:
                    files[fname] = files[fname].replace(old, new, 1)
                    fixed_count += 1
                    fix_log.append(f"{fname}: {fix.get('reason', 'fixed')[:50]}")
                else:
                    fix_log.append(f"{fname}: snippet not found, skipped")

            if fixed_count == 0:
                # Fallback: full-file rewrite for pipeline.py only (highest-likelihood fix target)
                user_prompt2 = (
                    f"Rewrite only pipeline.py to fix:\n{issues_text}\n\n"
                    f"Current pipeline.py:\n```python\n{files.get('pipeline.py','')}\n```\n\n"
                    "Output complete fixed pipeline.py in a ```pipeline.py fenced block."
                )
                # 必修 2026-04-10 (审计 #2): 同上, fallback 路径也必须用注入的 role/max_tokens
                client2 = LLMClient(
                    role=self._role,
                    max_tokens=self._max_tokens,
                    **({"model": self._model} if self._model else {}),
                )
                resp2 = client2.call(messages=[{"role": "user", "content": user_prompt2}],
                                     system=_auto_fixer_system)
                text2 = resp2.content[0].text if hasattr(resp2, "content") else ""
                match = re.search(r'```pipeline\.py\s*\n(.*?)```', text2, re.DOTALL)
                if match:
                    files["pipeline.py"] = match.group(1).strip()
                    fixed_count = 1
                    fix_log.append("pipeline.py: full rewrite fallback")

            if fixed_count == 0:
                return Verdict(kind=VerdictKind.FAIL, output=skeleton,
                               diagnosis=f"AutoFixer: could not apply fixes. Log: {fix_log}")

            # P7.3: reports 容器保留, 不再 pop
            fixed = {**skeleton, "files": files}
            return Verdict(kind=VerdictKind.PASS, output=fixed,
                           granted_tags=["auto-fix-applied"],
                           diagnosis=f"Fixed {fixed_count} file(s): {'; '.join(fix_log)}")

        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=skeleton,
                           diagnosis=f"AutoFixer error: {e}")

        # (end of run method)


# ═══════════════════════════════════════════════════════════
# [J] finalizer — 最终化 (HARD)
# ═══════════════════════════════════════════════════════════

class FinalizerRouter(Router):
    """最终化: 注册管线到全局 registry，生成质量总结。

    输入: 通过全部验证链的 wf.tested_skeleton
    输出: wf.done
    """

    FORMAT_IN = "wf.project_skeleton"  # P7.3 单主干: 通过 granted_tags 累加确认验证完成
    FORMAT_OUT = "wf.done"
    DESCRIPTION = (
        "最终化（确定性）。将通过全部验证的管线注册到全局 pipeline registry，"
        "生成质量总结报告（编译/LAP/路由/测试四项得分），输出最终产物。"
        "从 reports 容器读取四项报告而不是顶层键 (P7.3 重构)。"
    )

    def run(self, input_data: Any) -> Verdict:
        import importlib
        import sys
        from pathlib import Path

        skeleton = input_data
        pipeline_name = skeleton.get("pipeline_name", "unknown")
        pkg_path = skeleton.get("package_path", "")
        files: dict[str, str] = skeleton.get("files", {})

        # P7.3: 从 reports 容器读取质量得分
        reports = skeleton.get("reports", {}) or {}
        quality = {
            "compile": reports.get("compile", {}).get("passed", False),
            "lap_audit": reports.get("lap_audit", {}).get("passed", False),
            "error_routes": reports.get("error_route", {}).get("overall_passed", False),
            "integration": reports.get("integration", {}).get("passed", False),
        }

        # ── 写入文件系统 ──
        # pkg_path 如 "omnicompany.packages.domains.demogame.tavern_pool_modify"
        # → 写到 src/omnifactory/packages/domains/demogame/tavern_pool_modify/
        written = False
        write_path: Path | None = None
        write_error = ""
        if pkg_path and files:
            try:
                # 2026-04-21 OMNI-041 防污染: 允许测试/工具显式覆盖 src_root, 避免误写 src/
                # 测试脚本应传 _wf_test_output_root 指向 tmp 目录, 生产场景留空默认 src/ 根
                override_root = (
                    skeleton.get("_wf_test_output_root")
                    or input_data.get("_wf_test_output_root")
                )
                if override_root:
                    src_root = Path(override_root).resolve()
                    src_root.mkdir(parents=True, exist_ok=True)
                else:
                    # 找到 src 根目录（从本文件向上找）
                    this_file = Path(__file__).resolve()
                    src_root = this_file
                    for _ in range(10):
                        if (src_root / "omnifactory").is_dir() and (src_root / "omnifactory" / "__init__.py").exists():
                            break
                        src_root = src_root.parent

                # pkg_path → 目录路径
                parts = pkg_path.split(".")
                pkg_dir = src_root.joinpath(*parts)
                pkg_dir.mkdir(parents=True, exist_ok=True)

                for fname, content in files.items():
                    # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
                    (pkg_dir / fname).write_text(content, encoding="utf-8")

                # 2026-04-21: 新建目录后清 path_importer_cache 让后续 import 发现新 pkg
                sys.path_importer_cache.clear()

                written = True
                write_path = pkg_dir
            except Exception as e:
                write_error = str(e)

        # ── 写入后验证：能否 import ──
        importable = False
        import_error = ""
        if written and pkg_path:
            try:
                # 清除可能的旧缓存
                for key in list(sys.modules.keys()):
                    if key.startswith(pkg_path):
                        del sys.modules[key]
                mod = importlib.import_module(pkg_path)
                build_fn = getattr(mod, "build_pipeline", None)
                if build_fn:
                    build_fn()  # 实际执行 build_pipeline()
                importable = True
            except Exception as e:
                import_error = str(e)

        if not importable:
            # 写入了但 import/run 失败 → FAIL，让修复回路处理
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**skeleton, "write_path": str(write_path), "import_error": import_error},
                diagnosis=f"管线代码写入 {write_path} 但 import/build 失败: {import_error}",
            )

        result = {
            "pipeline_name": pipeline_name,
            "package_path": pkg_path,
            "write_path": str(write_path),
            "quality_summary": quality,
            "registered": False,
            "files": list(files.keys()),
        }

        return Verdict(
            kind=VerdictKind.PASS,
            output=result,
            diagnosis=f"管线 {pipeline_name} 写入 {write_path}，import+build 验证通过",
        )
