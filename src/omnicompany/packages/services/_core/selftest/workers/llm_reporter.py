# [OMNI] origin=claude-code domain=omnicompany/selftest ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.selftest.llm_health.probe_reporter.py"
"""LLMReporterWorker — Selftest Team Worker #4 (sink 产出).

Worker 协议:
  FORMAT_IN  = selftest.selftest-report
  FORMAT_OUT = selftest.health-report  (kind.sink)

职责: LLM 连通性探针 + 自然语言健康摘要生成。LLM 不可用时降级 (始终 PASS)。
"""
from __future__ import annotations

from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.packages.services._core.omnicompany import Worker


class LLMReporterWorker(Worker):
    """LLM 连通性探针 + 自然语言健康摘要生成。不管 LLM 是否可用都 PASS (降级)。"""

    DESCRIPTION = (
        "调用 LLMClient 发送一条简短的 ping 请求, 验证 LLM 端点连通性; "
        "成功后用 LLM 生成一段自然语言的 OmniCompany 健康摘要。"
        "LLM 不可用时降级: llm_ok=False, 输出错误原因, 不阻断管线（始终 PASS）。"
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
            from omnicompany.runtime.llm.llm import LLMClient

            client = self._client or LLMClient(role="runtime_main", max_tokens=512)

            functional_summary = ", ".join(
                f"{r['name']}={'OK' if r['ok'] else 'FAIL'}"
                for r in functional_results
            )
            prompt = (
                f"你是 OmniCompany 系统监控助手。请用 2-3 句话（中文）总结以下自检结果:\n"
                f"- 已注册管线: {total_pipelines} 个\n"
                f"- 功能检查: {passed}/{total} 通过, {failed} 失败\n"
                f"- 过时注册警告: {warnings_count} 个\n"
                f"- 各项检查: {functional_summary}\n"
                f"请给出系统当前健康状态的简洁评估。"
            )

            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system="你是 OmniCompany 的系统自检报告生成器, 输出简洁的中文健康摘要。",
            )

            if hasattr(resp, "content") and resp.content:
                block = resp.content[0]
                text = getattr(block, "text", str(block))
            else:
                text = str(resp)

            return True, text.strip()
        except Exception as exc:
            return False, f"LLM 调用失败: {exc}"
