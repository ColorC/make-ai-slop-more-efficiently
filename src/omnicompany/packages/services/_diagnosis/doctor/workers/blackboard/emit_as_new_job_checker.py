# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.blackboard.emit_new_job_compliance_checker.py"
"""EmitAsNewJobCheckerWorker — 黑板诊断子域 Worker #6.

Worker 协议:
  FORMAT_IN  = doctor.blackboard.audit_request
  FORMAT_OUT = doctor.blackboard.emit_check_report

职责: 扫 Worker.run 源码 · 检测 `_emit_as_new_job` 合规性 (R-25).

合规要求:
- Worker 在 Verdict.output 写 `_emit_as_new_job: True` 时, 必须在 DESCRIPTION 或 run() docstring
  中说明"发子 job"的用途 (防滥用)
- 理由: 子 job 打破 Q1 单次激活约束, 需显式设计意图 (agent 循环 / validator 修复 / tool 返回)

实现: 粗扫文本匹配. 详版 AST 在 Stage 3 清洁.
"""
from __future__ import annotations

import inspect
import re
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import build_subscription_graph


_EMIT_RE = re.compile(r"_emit_as_new_job")
_REASON_KEYWORDS = ("子 job", "子job", "new job", "emit as", "agent 多轮", "validator 修复", "tool 返回", "tool_result")


class EmitAsNewJobCheckerWorker(Worker):
    """诊断 _emit_as_new_job 合规使用 (R-25 · 防滥用)."""

    DESCRIPTION = (
        "订阅 doctor.blackboard.audit_request · 动态 import Team · "
        "对每个 Worker.run 源码查找 '_emit_as_new_job' 出现. 若有出现, "
        "要求 DESCRIPTION 或 run() docstring 含 '子 job' / 'agent 多轮' / 'validator 修复' 等 "
        "关键词说明发子 job 理由 (防滥用). 产出 doctor.blackboard.emit_check_report (sink)."
    )
    FORMAT_IN = "doctor.blackboard.audit_request"
    FORMAT_OUT = "doctor.blackboard.emit_check_report"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        req = input_data.get("doctor.blackboard.audit_request", input_data)
        team_path = req.get("team_module_path", "")
        if not team_path:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="team_module_path 缺失")

        try:
            g = build_subscription_graph(team_path)
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"加载 Team 失败: {type(exc).__name__}: {exc}",
            )

        findings: list[dict] = []
        for w in g.workers:
            if not w.run_source:
                continue
            if not _EMIT_RE.search(w.run_source):
                continue
            # 有 _emit_as_new_job 使用 · 查理由
            description = getattr(w.cls, "DESCRIPTION", "") or ""
            run_method = getattr(w.cls, "run", None)
            run_doc = inspect.getdoc(run_method) or ""
            class_doc = inspect.getdoc(w.cls) or ""
            combined = f"{description}\n{run_doc}\n{class_doc}"
            reason_documented = any(kw in combined for kw in _REASON_KEYWORDS)
            if not reason_documented:
                findings.append({
                    "worker_class": w.name,
                    "file": w.source_file,
                    "reason_documented": False,
                    "violation": (
                        "R-25 · Worker 用 _emit_as_new_job 发子 job, 但 DESCRIPTION / docstring "
                        "未说明用途 (子 job / agent 循环 / validator 修复 / tool 返回)"
                    ),
                    "severity": "MEDIUM",
                })

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"_emit_as_new_job 扫描完成: {len(g.workers)} Worker, {len(findings)} 违规",
            output={
                "team_module_path": team_path,
                "findings": findings,
                "worker_count": len(g.workers),
                "violation_count": len(findings),
            },
        )
