# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker status=deprecated
# [OMNI] summary="WorkerContextualAuditor — V3 LLM 4 层语义审计 worker. 软归档 2026-05-06: 判定点已反推为 H-2026-05-06-006..010 进新假设库, 用 HypothesisDiagnosticAgent 替代"
# [OMNI] why="诊断重制阶段 3 项 2: D 类老 LLM worker 软归档. 物理归档待 V3 管线整体替换 (plan §六 阶段 8)"
# [OMNI] tags=worker,doctor,deprecated,phase-3-soft-archive
# [OMNI] material_id="material:diagnosis.doctor.worker.worker.llm_contextual_auditor.py"
"""WorkerContextualAuditor — LLM 四层语义审计 (SOFT, Stage 3 Clean Migration 2026-04-22).

## DEPRECATED (2026-05-06 软归档, V3 整体替换时物理归档)

本 worker 的 4 层判定点已反推为新假设入 `data/services/doctor/hypotheses/`:
- H-2026-05-06-006: LLM 失败 passed=None 不抛异常 / 不伪造 passed=True
- H-2026-05-06-007: LLM Router vs RULE Router 差异化 schema
- H-2026-05-06-008: 评估结果存档关联 git hash
- H-2026-05-06-009: 评估 schema 保留 uncertain 选项 (允许"我不知道")
- H-2026-05-06-010: LLM 重试上限 + 优雅降级

新替代路径: 用 `HypothesisDiagnosticAgent` 跑这 5 条假设 (plus 现有 sample_hypothesis), 而不是
单次 fan-in 调本 worker. agent 形态优势: 拒打分铁律 + 跨任意 Router 对象 + agent loop 探索式上下文.

物理归档时机: plan §六 阶段 8 (闭环跑通 + 用户验收) 后, V3 管线整体切换到新架构时.

---

Worker 协议:
  FORMAT_IN  = diag.worker.det-checks
  FORMAT_OUT = diag.worker.audit

诊断目标: 对 Router 进行全语境语义审计, 注入:
  - Router 源码
  - FORMAT_IN / FORMAT_OUT 定义
  - 上下游邻居 DESCRIPTION
  - Pipeline 简述
  - 确定性检查失败摘要
  - AST 衍生信号
  - docs/standards/worker.md 节选

LLM 评估四层:
  层 A (信息前提): Router 是否有足够信息做好本职工作
  层 B (执行质量): Router 是否做对了、做完整了
  层 C (产出忠实度): 输出是否与 FORMAT_OUT 契约一致
  层 D (本职手艺): LLM Router 的 prompt 设计 / RULE Router 的边界完整性

RULE Router 使用 Schema B (精简), LLM Router 使用 Schema A (完整).
LLM 失败最多 RETRY 2 次; 最终失败追加 passed=null check.
报告存档到 data/doctor/audit/rtr_<ClassName>/<git_hash>.md.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import DEFAULT_SOURCE_ROOT, logger


_SYSTEM = (
    "你是一位资深软件工程师, 正在对一个 Router 类进行代码审计. "
    "Router 是一个处理节点, 接受结构化输入 (FORMAT_IN), 产出结构化输出 (FORMAT_OUT). "
    "请根据给定的信息, 对 Router 进行四层评估: "
    "层 A (信息前提: Router 是否有足够信息做好本职工作), "
    "层 B (执行质量: Router 是否做对了、做完整了), "
    "层 C (产出忠实度: 输出是否与 FORMAT_OUT 契约一致), "
    "层 D (本职手艺: LLM Router 的 prompt 设计; RULE Router 的边界完整性). "
    "请严格按照指定 JSON schema 输出, 不要输出其他内容. "
    "对于三值字段 ('true'|'false'|'uncertain'), 当信息不足以判断时输出 'uncertain', 不要强行给结论."
)

_SCHEMA_A_TEMPLATE = """\
输出格式 (LLM Router, 严格 JSON):
{
  "a_info_sufficient": "true | false | uncertain",
  "a_info_gaps": "具体描述信息缺口, 或 'none'",
  "a_implicit_assumptions": "隐式假设列表 (逗号分隔), 或 'none'",
  "a_budget_feasible": "true | false | uncertain",
  "a_budget_notes": "token 预算评估, 或 'none'",
  "b_r03_homogeneous": "true | false | uncertain",
  "b_r03_notes": "多 LLM 调用同质性分析",
  "b_r08_intermediates_ok": "true | false",
  "b_r08_candidates": "有独立价值的中间变量列表, 或 'none'",
  "b_r16_generic_extracted": "true | false",
  "b_r16_candidates": "可提取为 Tool 的通用逻辑, 或 'none'",
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
  "p_split_reason": "拆分建议, 或 'none'",
  "p_could_merge": "true | false",
  "p_merge_notes": "合并建议, 或 'none'",
  "overall_grade": "A | B | C | D",
  "key_findings": ["发现 1", "发现 2"],
  "improvement_suggestions": ["建议 1", "建议 2"],
  "detailed_report": "完整 Markdown 审计报告"
}"""

_SCHEMA_B_TEMPLATE = """\
输出格式 (RULE Router, 严格 JSON):
{
  "a_info_sufficient": "true | false | uncertain",
  "a_info_gaps": "具体描述信息缺口, 或 'none'",
  "a_implicit_assumptions": "隐式假设列表 (逗号分隔), 或 'none'",
  "a_budget_feasible": "true | false | uncertain",
  "a_budget_notes": "复杂度评估",
  "b_r08_intermediates_ok": "true | false",
  "b_r08_candidates": "有独立价值的中间变量列表, 或 'none'",
  "b_r16_generic_extracted": "true | false",
  "b_r16_candidates": "可提取为 Tool 的通用逻辑, 或 'none'",
  "b_error_paths_complete": "true | false | uncertain",
  "b_error_notes": "错误路径覆盖评估",
  "c_r14_diagnosis_quality": "true | false | uncertain",
  "c_r14_notes": "diagnosis 字符串质量评估",
  "c_r15_tags_accurate": "true | false | N/A",
  "c_r15_notes": "granted_tags 与验证行为一致性",
  "c_format_out_aligned": "true | false | uncertain",
  "c_format_out_notes": "Verdict.output 与 FORMAT_OUT 描述的对齐情况",
  "c_confidence_calibrated": "true | false | N/A",
  "c_confidence_notes": "confidence 校准 (RULE Router 应全 1.0)",
  "d_rule_boundary_complete": "true | false",
  "d_rule_boundary_notes": "边界条件 (空输入/类型错误/字段缺失) 是否全有 FAIL 路径",
  "d_rule_output_precise": "true | false",
  "d_rule_output_notes": "Verdict.output 字段与 FORMAT_OUT 严格对齐情况",
  "p_should_split": "true | false | uncertain",
  "p_split_reason": "拆分建议, 或 'none'",
  "p_could_merge": "true | false",
  "p_merge_notes": "合并建议, 或 'none'",
  "overall_grade": "A | B | C | D",
  "key_findings": ["发现 1", "发现 2"],
  "improvement_suggestions": ["建议 1", "建议 2"],
  "detailed_report": "完整 Markdown 审计报告"
}"""


class WorkerContextualAuditor(Worker):
    """LLM 四层语义审计 + git 存档; RULE/LLM Router 用不同 schema."""

    DESCRIPTION = "LLM 全语境审计: Router 源码 + FORMAT 定义 + 邻居 + 确定性检查结果 → 层 A/B/C/D 评级 + 改进建议 + git 存档"
    FORMAT_IN = "diag.worker.det-checks"
    FORMAT_OUT = "diag.worker.audit"
    INPUT_KEYS = ["worker_class", "extracted", "context", "checks"]

    def __init__(self, model: str | None = None):
        # 项目唯一 LLM (铁律): qwen3.6-plus. 保留 model 参数以便测试覆写.
        self._model = model or "qwen3.6-plus"

    def run(self, input_data: Any) -> Verdict:
        worker_class: str = input_data["worker_class"]
        source_root = Path(input_data.get("source_root", DEFAULT_SOURCE_ROOT))
        extracted: dict = input_data.get("extracted", {})
        context: dict = input_data.get("context", {})
        checks: list[dict] = input_data.get("checks", [])
        router_kind: str = extracted.get("ast_signals", {}).get("router_kind", "RULE")

        standards = self._load_standards(source_root)

        user_msg = self._build_user_msg(
            worker_class, extracted, context, checks, router_kind, standards
        )
        schema_template = _SCHEMA_A_TEMPLATE if router_kind == "LLM" else _SCHEMA_B_TEMPLATE

        audit_data: dict = {}
        raw_text: str = ""
        for attempt in range(3):
            audit_data, raw_text = self._audit(user_msg)
            if audit_data.get("overall_grade") in ("A", "B", "C", "D"):
                break
            if attempt < 2:
                logger.warning(
                    "RouterContextualAudit attempt %d failed for %s, retrying",
                    attempt + 1, worker_class,
                )

        audit_path: str = ""
        if audit_data:
            archived = self._archive_report(worker_class, router_kind, source_root, audit_data)
            if archived:
                audit_path = str(archived)

        if audit_data.get("overall_grade"):
            grade = audit_data["overall_grade"]
            obs_str_parts = []
            for k, v in list(audit_data.items())[:6]:
                if k in ("overall_grade", "key_findings", "improvement_suggestions", "detailed_report"):
                    continue
                if isinstance(v, str):
                    obs_str_parts.append(f"{k}={v}")
            observation = f"grade={grade}; " + "; ".join(obs_str_parts)[:200]
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
                "observation": "LLM 审计失败 (3 次重试后仍无效响应), 需人工复核",
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
                f"RouterContextualAudit: {worker_class} grade={audit_data.get('overall_grade', 'N/A')}"
            ),
        )

    def _build_user_msg(
        self,
        worker_class: str,
        extracted: dict,
        context: dict,
        checks: list[dict],
        router_kind: str,
        standards: str,
    ) -> str:
        parts: list[str] = [f"# Router 审计请求: {worker_class} ({router_kind} Router)\n"]

        run_source = extracted.get("run_source", "")
        parts.append(
            f"## Router 完整源码\n```python\nclass {worker_class}(Router):\n"
            f"    DESCRIPTION = {repr(extracted.get('description', ''))}\n"
            f"    FORMAT_IN = {repr(extracted.get('format_in', ''))}\n"
            f"    FORMAT_OUT = {repr(extracted.get('format_out', ''))}\n\n"
            f"{run_source}\n```"
        )

        fmt_in_def = context.get("format_in_def")
        if fmt_in_def:
            parts.append(
                f"## FORMAT_IN 定义 ({extracted.get('format_in')})\n"
                f"description: {fmt_in_def.get('description', '(未找到)')}\n"
                f"tags: {fmt_in_def.get('tags', [])}\n"
                f"examples: {json.dumps(fmt_in_def.get('examples', []), ensure_ascii=False)}"
            )
        else:
            parts.append(f"## FORMAT_IN 定义\n(未找到 {extracted.get('format_in')} 的 Format 定义)")

        fmt_out_def = context.get("format_out_def")
        if fmt_out_def:
            parts.append(
                f"## FORMAT_OUT 定义 ({extracted.get('format_out')})\n"
                f"description: {fmt_out_def.get('description', '(未找到)')}\n"
                f"tags: {fmt_out_def.get('tags', [])}\n"
                f"examples: {json.dumps(fmt_out_def.get('examples', []), ensure_ascii=False)}"
            )
        else:
            parts.append(f"## FORMAT_OUT 定义\n(未找到 {extracted.get('format_out')} 的 Format 定义)")

        upstreams = context.get("upstream_routers", [])
        if upstreams:
            parts.append("## 上游 Router (生产 FORMAT_IN 的节点)")
            for u in upstreams:
                parts.append(f"- **{u['class']}**: {u.get('description', '')}")
        else:
            parts.append("## 上游 Router\n(未找到, 可能是管线入口)")

        downstreams = context.get("downstream_routers", [])
        if downstreams:
            parts.append("## 下游 Router (消费 FORMAT_OUT 的节点)")
            for d in downstreams:
                parts.append(f"- **{d['class']}**: {d.get('description', '')}")
        else:
            parts.append("## 下游 Router\n(未找到, 可能是管线出口)")

        pipeline_briefs = context.get("pipeline_briefs", [])
        pb = context.get("pipeline_brief")
        if pipeline_briefs:
            pipeline_purpose = context.get("pipeline_purpose", "")
            pipeline_lines = [
                f"pipeline_id: {b.get('pipeline_id', '?')} | node_id: {b.get('node_id', '?')}"
                for b in pipeline_briefs
            ]
            pipeline_summary = "\n".join(pipeline_lines)
            if pipeline_purpose:
                pipeline_summary += f"\n业务目标: {pipeline_purpose}"
            parts.append(f"## Pipeline 简述\n{pipeline_summary}")
        elif pb:
            parts.append(
                f"## Pipeline 简述\n"
                f"pipeline_id: {pb.get('pipeline_id', '?')} | "
                f"node_id: {pb.get('node_id', '?')}"
            )
        else:
            parts.append("## Pipeline 简述\n(未在任何 pipeline.py 中找到引用)")

        failed_checks = [c for c in checks if c.get("passed") is False]
        if failed_checks:
            parts.append("## 确定性检查失败项 (已有结论, 无需重复判断)")
            for c in failed_checks:
                parts.append(f"- [{c.get('severity')}] {c.get('check')}: {c.get('observation')}")
        else:
            parts.append("## 确定性检查\n所有确定性检查已通过")

        ast_signals = extracted.get("ast_signals", {})
        llm_calls = ast_signals.get("llm_calls", [])
        self_asgs = ast_signals.get("self_assignments", [])
        input_keys = ast_signals.get("input_keys_accessed", [])
        output_keys = ast_signals.get("output_keys_produced", [])
        verdict_pats = ast_signals.get("verdict_patterns", [])

        parts.append(
            f"## AST 衍生信号\n"
            f"- router_kind: {router_kind}\n"
            f"- llm_calls ({len(llm_calls)} 处): {[c.get('line') for c in llm_calls]}\n"
            f"- input_keys_accessed: {input_keys}\n"
            f"- output_keys_produced: {output_keys}\n"
            f"- verdict_patterns: {[vp.get('kind') for vp in verdict_pats]}\n"
            f"- self_assignments (SUSPICIOUS/LIKELY_VIOLATION): "
            f"{[sa for sa in self_asgs if sa.get('classification') != 'INFO']}"
        )

        if standards:
            parts.append(f"## Router 标准 (节选)\n{standards}")

        schema_template = _SCHEMA_A_TEMPLATE if router_kind == "LLM" else _SCHEMA_B_TEMPLATE
        parts.append(f"\n请输出严格 JSON, 按以下 schema: \n{schema_template}")

        return "\n\n".join(parts)

    def _load_standards(self, source_root: Path) -> str:
        """从 docs/standards/worker.md 加载 LLM 专用标准节选."""
        for parent in [source_root, *source_root.parents[:4]]:
            candidate = parent / "docs" / "standards" / "worker.md"
            if candidate.exists():
                try:
                    content = candidate.read_text(encoding="utf-8")
                    return self._filter_standards(content)
                except Exception:
                    pass
        return "(worker.md 未找到)"

    def _filter_standards(self, content: str) -> str:
        """从 worker.md 提取 LLM 需要的部分, 跳过确定性检查已覆盖的条目."""
        lines = content.splitlines()
        result: list[str] = []
        skip_items = {
            "**R-01**", "**R-02**", "**R-04**", "**R-05**",
            "**R-06**", "**R-07**", "**R-10**", "**R-11**",
            "**R-12**", "**R-13**", "**R-17**",
        }
        in_skip = False
        for line in lines:
            if any(skip in line for skip in skip_items):
                in_skip = True
            elif line.startswith("**R-") or line.startswith("### ") or line.startswith("## "):
                in_skip = False

            if not in_skip:
                result.append(line)

        return "\n".join(result)

    def _audit(self, user_msg: str) -> tuple[dict, str]:
        """调用 LLM; 返回 (audit_data, raw_text). 失败返回 ({}, error_msg)."""
        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(role="runtime_main")
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=_SYSTEM,
            )
            raw = resp.content[0].text.strip()
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
        worker_class: str,
        router_kind: str,
        source_root: Path,
        audit_data: dict,
    ) -> Path | None:
        """将 Markdown 报告存档到 data/doctor/audit/rtr_<ClassName>/<git_hash>.md."""
        try:
            git_hash = self._get_git_hash(source_root)
            safe_name = f"rtr_{worker_class}"
            audit_dir = source_root.resolve().parents[1] / "data" / "doctor" / "audit" / safe_name
            audit_dir.mkdir(parents=True, exist_ok=True)

            grade = audit_data.get("overall_grade", "?")
            detailed = audit_data.get("detailed_report", "(无详细报告)")
            findings = audit_data.get("key_findings", [])
            suggestions = audit_data.get("improvement_suggestions", [])

            report_lines = [
                f"# Router 审计报告: {worker_class}",
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
            logger.warning("Failed to archive router audit report for %s: %s", worker_class, e)
            return None
