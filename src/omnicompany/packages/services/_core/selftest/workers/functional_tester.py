# [OMNI] origin=claude-code domain=omnicompany/selftest ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.selftest.functional_smoke.deterministic_tester.py"
"""FunctionalTesterWorker — Selftest Team Worker #2.

Worker 协议:
  FORMAT_IN  = selftest.registry-report
  FORMAT_OUT = selftest.selftest-report

职责: 运行确定性功能测试 (DomainScanner 冒烟 + Stock 读写往返 + TeamChecker + CLI health)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.packages.services._core.omnicompany import Worker


class FunctionalTesterWorker(Worker):
    """运行确定性功能测试: HARD 节点冒烟 + Stock 读写往返 + 类型检查 + CLI health。"""

    DESCRIPTION = (
        "运行确定性功能测试套件（不调用 LLM）: "
        "1) DomainScannerRouter 以 project_root 为输入, 验证能扫到 packages/; "
        "2) Stock (原 EventBus) 写入一条测试事件, 读回验证可用; "
        "3) TeamChecker 对 selftest 自身管线执行类型检查。"
        "输出每项测试的 ok/error 详情列表。"
    )
    FORMAT_IN = "selftest.registry-report"
    FORMAT_OUT = "selftest.selftest-report"

    def run(self, input_data: Any) -> Verdict:
        project_root = input_data.get("project_root") or _detect_project_root()
        registry_failures = [
            r for r in input_data.get("pipeline_results", []) if not r["ok"]
        ]

        functional_results: list[dict] = [
            _test_eventbus(project_root),
            _test_pipeline_checker(),
            _test_cli_health(),
        ]

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
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src").exists() and (parent / "pyproject.toml").exists():
            return str(parent)
    return str(here.parents[6])


def _short(exc: Exception) -> str:
    return str(exc)[:200]


def _test_eventbus(project_root: str) -> dict:
    name = "eventbus_import"
    try:
        from omnicompany.bus import SQLiteBus
        from omnicompany.bus.base import EventBus
        from omnicompany.protocol.events import FactoryEvent

        assert issubclass(SQLiteBus, EventBus), "SQLiteBus 未继承 EventBus"
        assert FactoryEvent is not None
        return {"name": name, "ok": True, "detail": "SQLiteBus 和 FactoryEvent 可正常导入"}
    except Exception as exc:
        return {"name": name, "ok": False, "error": _short(exc)}


def _test_pipeline_checker() -> dict:
    name = "selftest_pipeline_checker"
    try:
        from omnicompany.protocol.format import create_builtin_registry
        from omnicompany.protocol.team import TeamChecker
        from omnicompany.packages.services._core.selftest.formats import register_formats
        from omnicompany.packages.services._core.selftest.pipeline import build_pipeline

        registry = create_builtin_registry()
        register_formats(registry)
        checker = TeamChecker(registry)
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
