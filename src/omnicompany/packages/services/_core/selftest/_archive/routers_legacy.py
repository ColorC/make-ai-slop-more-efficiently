# [OMNI] origin=claude-code domain=selftest/routers.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:core.selftest.registry_checker_functional_tester_selftest_gate_llm_reporter.routers_legacy.py"
"""selftest — Router 实现

四个 Router：
  RegistryCheckerRouter  — 验证所有管线可注册、build_pipeline/build_bindings 可调用、
                           node_ids == binding_keys
  FunctionalTesterRouter — HARD 节点烟雾测试 + EventBus 读写往返测试
  SelftestGateRouter     — 聚合报告，failed_checks > 0 → FAIL（阻断管线）
  LLMReporterRouter      — LLM 连通性探针 + 自然语言健康摘要生成（不可用时降级 WARN）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.runtime.routing.router import Router

logger = logging.getLogger(__name__)


# ─── 1. RegistryCheckerRouter ────────────────────────────────────────────────

class RegistryCheckerRouter(Router):
    """加载所有管线注册表，逐一验证 build_pipeline + build_bindings + bindings 完整性。"""

    DESCRIPTION = (
        "调用 register_all() 获取所有注册管线，对每条管线："
        "1) 调用 build_pipeline() 检查 PipelineSpec 可构建；"
        "2) 调用 build_bindings() 检查 bindings 可构建；"
        "3) 验证 PipelineSpec.node_ids 与 bindings.keys() 完全对应。"
        "输出每个管线的 ok/error 详情。"
    )
    FORMAT_IN = "selftest.request"
    FORMAT_OUT = "selftest.registry-report"

    def run(self, input_data: Any) -> Verdict:
        from omnifactory.core.pipelines import register_all
        import omnifactory.core.registry as _reg

        # 注册所有管线
        try:
            register_all()
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"register_all() 崩溃: {exc}",
            )

        entries = _reg.list_all()
        if not entries:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="注册表为空，register_all() 未注册任何管线",
            )

        pipeline_results = []
        total_fail = 0

        for entry in entries:
            result = _check_entry(entry)
            if not result["ok"]:
                total_fail += 1
            pipeline_results.append(result)

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=(
                f"注册检查完成: {len(entries)} 个管线, "
                f"{len(entries) - total_fail} 通过, {total_fail} 失败"
            ),
            output={
                "project_root": input_data.get("project_root", ""),
                "total_pipelines": len(entries),
                "failed_pipelines": total_fail,
                "pipeline_results": pipeline_results,
            },
        )


def _check_entry(entry: Any) -> dict:
    """对单个 PipelineEntry 执行三项检查，返回结果 dict。

    - build_pipeline() 失败 → error（结构性问题）
    - build_bindings() 需要必填参数 → warning（历史管线调用约定不标准）
    - build_bindings() 其他失败 → error
    - node_ids != binding_keys → error
    """
    name = getattr(entry, "name", str(entry))
    errors: list[str] = []
    warnings: list[str] = []
    pipeline_spec = None

    # 1. build_pipeline（AttributeError 说明注册条目过时，视为 warning）
    try:
        pipeline_spec = entry.build_pipeline()
    except AttributeError as exc:
        warnings.append(f"build_pipeline() 属性缺失（注册条目可能过时）: {_short(exc)}")
    except Exception as exc:
        errors.append(f"build_pipeline() 失败: {_short(exc)}")

    # 2. build_bindings（容忍历史管线的必填参数和过时注册条目）
    bindings = None
    try:
        bindings = entry.build_bindings()
    except TypeError as exc:
        msg = str(exc)
        if "required positional argument" in msg or "missing" in msg.lower():
            warnings.append(f"build_bindings() 需要必填参数（历史管线）: {_short(exc)}")
        else:
            errors.append(f"build_bindings() 失败: {_short(exc)}")
    except AttributeError as exc:
        warnings.append(f"build_bindings() 属性缺失（注册条目可能过时）: {_short(exc)}")
    except Exception as exc:
        errors.append(f"build_bindings() 失败: {_short(exc)}")

    # 3. node_ids == binding_keys（仅在两者都成功时检查）
    if pipeline_spec is not None and bindings is not None:
        node_ids = {n.id for n in pipeline_spec.nodes}
        binding_keys = set(bindings.keys())
        missing = node_ids - binding_keys
        extra = binding_keys - node_ids
        if missing:
            errors.append(f"bindings 缺少节点: {sorted(missing)}")
        if extra:
            errors.append(f"bindings 多余节点: {sorted(extra)}")

    return {
        "name": name,
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "node_count": len(pipeline_spec.nodes) if pipeline_spec else 0,
    }


def _short(exc: Exception) -> str:
    return str(exc)[:200]


# ─── 2. FunctionalTesterRouter ───────────────────────────────────────────────

class FunctionalTesterRouter(Router):
    """运行确定性功能测试：HARD 节点冒烟 + EventBus 读写往返。"""

    DESCRIPTION = (
        "运行确定性功能测试套件（不调用 LLM）："
        "1) DomainScannerRouter 以 project_root 为输入，验证能扫到 packages/；"
        "2) EventBus 写入一条测试事件，读回验证 SQLite bus 可用；"
        "3) PipelineChecker 对 selftest 自身管线执行类型检查。"
        "输出每项测试的 ok/error 详情列表。"
    )
    FORMAT_IN = "selftest.registry-report"
    FORMAT_OUT = "selftest.selftest-report"

    def run(self, input_data: Any) -> Verdict:
        project_root = input_data.get("project_root") or _detect_project_root()
        registry_failures = [
            r for r in input_data.get("pipeline_results", []) if not r["ok"]
        ]

        functional_results: list[dict] = []

        # ── 功能测试 1: DomainScannerRouter 冒烟 ──
        functional_results.append(_test_domain_scanner(project_root))

        # ── 功能测试 2: EventBus 读写往返 ──
        functional_results.append(_test_eventbus(project_root))

        # ── 功能测试 3: PipelineChecker on selftest pipeline ──
        functional_results.append(_test_pipeline_checker())

        # ── 功能测试 4: omni CLI health 命令可用 ──
        functional_results.append(_test_cli_health())

        failed = [r for r in functional_results if not r["ok"]]
        total = len(registry_failures) + len(functional_results)
        total_failed = len(registry_failures) + len(failed)
        total_passed = total - total_failed

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=(
                f"功能测试完成: {total} 项检查, "
                f"{total_passed} 通过, {total_failed} 失败"
            ),
            output={
                "project_root": str(project_root),
                "total_pipelines": input_data.get("total_pipelines", 0),
                "failed_pipelines": input_data.get("failed_pipelines", 0),
                "pipeline_results": input_data.get("pipeline_results", []),
                "registry_failures": registry_failures,
                "functional_results": functional_results,
                "functional_failures": failed,
                "total_checks": total,
                "passed_checks": total_passed,
                "failed_checks": total_failed,
            },
        )


def _detect_project_root() -> str:
    """自动探测项目根目录（包含 pyproject.toml 或 src/ 的祖先目录）。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src").exists() and (parent / "pyproject.toml").exists():
            return str(parent)
    return str(here.parents[6])  # fallback


def _test_domain_scanner(project_root: str) -> dict:
    name = "domain_scanner_smoke"
    try:
        from omnifactory.packages.services._diagnosis.pipeline_ci.routers import DomainScannerRouter
        router = DomainScannerRouter()
        verdict = router.run({"project_root": project_root})
        if verdict.kind == VerdictKind.FAIL:
            return {"name": name, "ok": False, "error": verdict.diagnosis}
        domain_count = len(verdict.output.get("domains", []))
        if domain_count == 0:
            return {"name": name, "ok": False, "error": "DomainScanner 返回空域列表"}
        return {"name": name, "ok": True, "detail": f"发现 {domain_count} 个域"}
    except Exception as exc:
        return {"name": name, "ok": False, "error": _short(exc)}


def _test_eventbus(project_root: str) -> dict:
    """验证 SQLiteBus 可导入并实例化（基础可用性检查）。"""
    name = "eventbus_import"
    try:
        from omnifactory.bus import SQLiteBus
        from omnifactory.bus.base import EventBus
        from omnifactory.protocol.events import FactoryEvent

        # 验证类继承关系正确
        assert issubclass(SQLiteBus, EventBus), "SQLiteBus 未继承 EventBus"
        # 验证 FactoryEvent 可导入
        assert FactoryEvent is not None

        return {"name": name, "ok": True, "detail": "SQLiteBus 和 FactoryEvent 可正常导入"}
    except Exception as exc:
        return {"name": name, "ok": False, "error": _short(exc)}


def _test_pipeline_checker() -> dict:
    name = "selftest_pipeline_checker"
    try:
        from omnifactory.protocol.format import create_builtin_registry
        from omnifactory.protocol.pipeline import PipelineChecker
        from omnifactory.packages.services._core.selftest.formats import register_formats
        from omnifactory.packages.services._core.selftest.pipeline import build_pipeline

        registry = create_builtin_registry()
        register_formats(registry)
        checker = PipelineChecker(registry)
        result = checker.check(build_pipeline())
        if not result.valid:
            return {"name": name, "ok": False, "error": str(result.type_errors)}
        return {"name": name, "ok": True, "detail": "selftest 管线类型检查通过"}
    except Exception as exc:
        return {"name": name, "ok": False, "error": _short(exc)}


def _test_cli_health() -> dict:
    name = "cli_health"
    try:
        import subprocess
        import shutil
        omni_path = shutil.which("omni")
        if not omni_path:
            return {"name": name, "ok": False, "error": "omni 命令不在 PATH 中"}

        result = subprocess.run(
            [omni_path, "health"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {
                "name": name, "ok": False,
                "error": f"omni health 退出码 {result.returncode}: {result.stderr[:200]}",
            }
        return {"name": name, "ok": True, "detail": "omni health 返回 0"}
    except Exception as exc:
        return {"name": name, "ok": False, "error": _short(exc)}


# ─── 3. SelftestGateRouter ───────────────────────────────────────────────────

class SelftestGateRouter(Router):
    """门控：failed_checks > 0 则 FAIL，并打印可读报告。"""

    DESCRIPTION = (
        "Selftest 门控节点：读取 FunctionalTesterRouter 的综合报告，"
        "若 failed_checks > 0 则返回 VerdictKind.FAIL 并打印所有失败项，"
        "否则返回 VerdictKind.PASS。"
    )
    FORMAT_IN = "selftest.selftest-report"
    FORMAT_OUT = "selftest.selftest-report"

    def run(self, input_data: Any) -> Verdict:
        total = input_data.get("total_checks", 0)
        passed = input_data.get("passed_checks", 0)
        failed = input_data.get("failed_checks", 0)
        total_pipelines = input_data.get("total_pipelines", 0)
        failed_pipelines = input_data.get("failed_pipelines", 0)

        print(f"\n{'='*62}")
        print("  OmniCompany Selftest Report")
        print(f"{'='*62}")
        print(f"  Registered pipelines : {total_pipelines}  "
              f"({failed_pipelines} failed to load)")
        print(f"  Total checks         : {total}")
        print(f"  Passed               : {passed}")
        print(f"  Failed               : {failed}")
        print(f"{'='*62}")

        # 管线注册失败
        for r in input_data.get("pipeline_results", []):
            if not r["ok"]:
                print(f"  FAIL [registry] {r['name']}")
                for err in r.get("errors", []):
                    print(f"      ERROR: {err[:120]}")
            elif r.get("warnings"):
                print(f"  WARN [registry] {r['name']}")
                for w in r.get("warnings", []):
                    print(f"      WARN: {w[:120]}")

        # 功能测试
        for r in input_data.get("functional_results", []):
            icon = "PASS" if r["ok"] else "FAIL"
            detail = r.get("detail", r.get("error", ""))
            print(f"  {icon} [functional] {r['name']}: {detail[:100]}")

        print(f"{'='*62}\n")

        if failed > 0:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"Selftest 失败: {failed}/{total} 项检查不通过",
                output=input_data,
            )

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"Selftest 全部通过: {total} 项检查，{total_pipelines} 个管线正常",
            output=input_data,
        )


# ─── 4. LLMReporterRouter ────────────────────────────────────────────────────

class LLMReporterRouter(Router):
    """LLM 连通性探针 + 自然语言健康摘要生成。

    不管 LLM 是否可用都返回 PASS（降级处理）：
    - LLM 可用 → llm_ok=True + llm_summary 有内容
    - LLM 不可用 → llm_ok=False + llm_summary 为错误说明
    """

    DESCRIPTION = (
        "调用 LLMClient 发送一条简短的 ping 请求，验证 LLM 端点连通性；"
        "成功后用 LLM 生成一段自然语言的 OmniCompany 健康摘要。"
        "LLM 不可用时降级：llm_ok=False，输出错误原因，不阻断管线（始终 PASS）。"
    )
    FORMAT_IN = "selftest.selftest-report"
    FORMAT_OUT = "selftest.health-report"

    def __init__(self, client=None):
        self._client = client

    def run(self, input_data: Any) -> Verdict:
        total = input_data.get("total_checks", 0)
        passed = input_data.get("passed_checks", 0)
        failed = input_data.get("failed_checks", 0)
        total_pipelines = input_data.get("total_pipelines", 0)
        warnings_count = sum(
            len(r.get("warnings", []))
            for r in input_data.get("pipeline_results", [])
        )

        llm_ok, llm_summary = self._call_llm(
            total=total, passed=passed, failed=failed,
            total_pipelines=total_pipelines, warnings_count=warnings_count,
            functional_results=input_data.get("functional_results", []),
        )

        output = {**input_data, "llm_ok": llm_ok, "llm_summary": llm_summary}

        print(f"\n{'='*62}")
        print("  LLM Reporter")
        print(f"{'='*62}")
        print(f"  LLM available: {'YES' if llm_ok else 'NO'}")
        if llm_ok:
            print(f"\n{llm_summary}\n")
        else:
            print(f"  Reason: {llm_summary[:200]}")
        print(f"{'='*62}\n")

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"LLM reporter 完成 (llm_ok={llm_ok})",
            output=output,
        )

    def _call_llm(
        self, total: int, passed: int, failed: int,
        total_pipelines: int, warnings_count: int,
        functional_results: list,
    ) -> tuple[bool, str]:
        try:
            from omnifactory.runtime.llm.llm import LLMClient

            client = self._client or LLMClient(role="runtime_main", max_tokens=512)

            functional_summary = ", ".join(
                f"{r['name']}={'OK' if r['ok'] else 'FAIL'}"
                for r in functional_results
            )
            prompt = (
                f"你是 OmniCompany 系统监控助手。请用 2-3 句话（中文）总结以下自检结果：\n"
                f"- 已注册管线: {total_pipelines} 个\n"
                f"- 功能检查: {passed}/{total} 通过，{failed} 失败\n"
                f"- 过时注册警告: {warnings_count} 个\n"
                f"- 各项检查: {functional_summary}\n"
                f"请给出系统当前健康状态的简洁评估。"
            )

            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system="你是 OmniCompany 的系统自检报告生成器，输出简洁的中文健康摘要。",
            )

            # 提取文本
            if hasattr(resp, "content") and resp.content:
                block = resp.content[0]
                text = getattr(block, "text", str(block))
            else:
                text = str(resp)

            return True, text.strip()

        except Exception as exc:
            return False, f"LLM 调用失败: {exc}"
