# [OMNI] origin=claude-code domain=services/skill_importer ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:utility.skill_importer.skill_verifier.llm.py"
"""VerifyAgainstSkillWorker — LLM 忠实度检验 (SOFT, Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.llm.llm import LLMClient

logger = logging.getLogger(__name__)


_VERIFY_PROMPT = """你在检验一个由 workflow-factory 生成的 OmniCompany package 是否
忠实地实现了原 Claude Code Skill 的要求。

**原 skill 的核心要求清单**:

- skill_purpose: {skill_purpose}
- nodes (原 skill 识别出的): {nodes_summary}
- special_constraints (不可违反的铁律): {constraints}
- coverage_expectations: {coverage_expectations}

**workflow-factory 生成的 package 结构**:

- package path: {package_path}
- files: {file_list}

**package 内容摘要** (主要 .py 文件):

{package_content}

---

请严格检查并输出 markdown 报告, 必须含以下段落:

## 整体结论

(PASS / PARTIAL / FAIL 三选一 + 1-2 句说明)

## 节点覆盖检查

(列表形式: 原 skill 的每个节点是否在生成的 pipeline.py 中有对应节点, 用 ✓/✗
标记, 说明缺失的)

## 约束合规检查

(原 skill 的每条 special_constraint 是否被遵守, 逐条检查)

## 质量问题

(发现的代码质量问题, 如语法错误、平铺 TRANSFORMER 代替 SCATTER、缺少 AgentNodeLoop、
Format id 命名不规范等)

## 修复建议

(具体的可执行建议, 例如 "packages/xxx/routers.py:L42 应改为 AgentNodeLoop 子类")

不使用 confidence 标签。中文输出。"""


class VerifyAgainstSkillWorker(Worker):
    DESCRIPTION = (
        "跑在 workflow-factory 产物后面, 校验生成的 package 是否忠实覆盖原 skill "
        "的所有节点 / 约束 / 覆盖预期。产出 markdown compliance report。这是 "
        "skill_importer 的最后一道质量门."
    )
    FORMAT_IN = "skill_importer.compliance_check_request"
    FORMAT_OUT = "skill_importer.compliance_report"

    def run(self, data: dict) -> Verdict:
        if not isinstance(data, dict):
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis="input must be dict",
            )

        package_path_str = data.get("package_path")
        skill_structure = data.get("skill_structure")

        if not package_path_str or not skill_structure:
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis=(
                    "VerifyAgainstSkillWorker 需要 package_path 和 skill_structure 字段. "
                    "典型调用: 先跑 skill-import (parse+analyze+infer+draft) 拿 skill_structure, "
                    "再跑 workflow-factory 拿 package_path, 最后调用本 Worker 做检验。"
                ),
            )

        package_path = Path(package_path_str)
        if not package_path.exists():
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis=f"package path not found: {package_path}",
            )

        py_files = sorted(package_path.glob("*.py"))
        package_content_parts: list[str] = []
        file_list: list[str] = []
        for pf in py_files:
            file_list.append(pf.name)
            try:
                content = pf.read_text(encoding="utf-8")
                package_content_parts.append(
                    f"### {pf.name}\n```python\n{content}\n```"
                )
            except OSError:
                package_content_parts.append(f"### {pf.name}\n(read error)")

        package_content = "\n\n".join(package_content_parts)

        nodes_summary = "\n".join(
            f"- {n.get('id', '?')}: {n.get('title', '')} ({n.get('kind', 'ANCHOR')})"
            for n in skill_structure.get("nodes", [])
        )

        prompt = _VERIFY_PROMPT.format(
            skill_purpose=skill_structure.get("skill_purpose", ""),
            nodes_summary=nodes_summary,
            constraints=json.dumps(
                skill_structure.get("special_constraints", []),
                ensure_ascii=False,
                indent=2,
            ),
            coverage_expectations=skill_structure.get("coverage_expectations", ""),
            package_path=str(package_path),
            file_list=file_list,
            package_content=package_content,
        )

        try:
            client = LLMClient(role="ide_agent", max_tokens=8192, tools=[])
            response = client.call(
                messages=[{"role": "user", "content": prompt}],
                system=(
                    "你是严格的 OmniCompany 质量审计员, 不放过任何偏差, 也不加任何"
                    "主观判断分数。只陈述事实 + 给可执行的修复建议。"
                ),
            )
            report_md = "".join(
                b.text for b in response.content if hasattr(b, "text")
            ).strip()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis=f"LLM compliance check failed: {e}",
            )

        try:
            from omnicompany.core.config import resolve_db_dir
            from omnicompany.core.guarded_write import write_file

            digest_dir = resolve_db_dir("absorption") / "skill_digest"
            digest_dir.mkdir(parents=True, exist_ok=True)
            skill_name = skill_structure.get("skill_name") or package_path.name
            report_path = digest_dir / f"{skill_name}.compliance.md"
            write_file(
                str(report_path),
                report_md,
                origin="internal-engine",
                domain="services/skill_importer",
                purpose=f"compliance report for {skill_name}",
            )
        except Exception as e:
            logger.warning("[skill_importer.verify] write fallback: %s", e)
            report_path = package_path / "_compliance_report.md"
            report_path.write_text(report_md, encoding="utf-8")

        verdict_kind = VerdictKind.PASS
        report_upper = report_md.upper()
        if "## 整体结论" in report_md:
            conclusion_section = report_md.split("## 整体结论", 1)[1].split("##", 1)[0]
            if "FAIL" in conclusion_section.upper():
                verdict_kind = VerdictKind.FAIL
            elif "PARTIAL" in conclusion_section.upper():
                verdict_kind = VerdictKind.PARTIAL
        elif "FAIL" in report_upper and "PASS" not in report_upper:
            verdict_kind = VerdictKind.FAIL

        out = dict(data)
        out["compliance_report_path"] = str(report_path)
        out["compliance_report_chars"] = len(report_md)
        out["compliance_verdict"] = verdict_kind.value

        return Verdict(
            kind=verdict_kind,
            output=out,
            confidence=0.9,
            diagnosis=(
                f"compliance verdict={verdict_kind.value}, report saved to "
                f"{report_path.name}"
            ),
            granted_tags=["domain.skill_importer", "stage.verified"],
        )
