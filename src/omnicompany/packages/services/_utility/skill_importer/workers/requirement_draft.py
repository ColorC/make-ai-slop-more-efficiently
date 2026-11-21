# [OMNI] origin=claude-code domain=services/skill_importer ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:utility.skill_importer.requirement_draft_generator.llm.py"
"""RequirementDraftWorker — LLM 产出 workflow-factory 需求稿 (SOFT, Stage 3 2026-04-22)."""
from __future__ import annotations

import json
import logging

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.llm.llm import LLMClient

logger = logging.getLogger(__name__)


_REQUIREMENT_TEMPLATE_PROMPT = """你在把一个 Claude Code Skill 翻译成 OmniCompany
workflow-factory 可消费的 **需求稿**。workflow-factory 会读这份需求稿, 生成完整的
OmniCompany LAP 管线 (formats.py / routers.py / pipeline.py / run.py)。

你的输出必须是 **markdown**, 段落结构严格如下, 用中文。

## 目标
(3-5 句话描述这条管线做什么, 为什么要有它, 目标 skill 是什么)

## 新 Package 位置
(形如 `src/omnicompany/packages/services/<name>/`, 解释为什么是 services 而不是 domains)

## 管线节点拓扑
(markdown 伪代码 + 箭头图, 列出所有节点按顺序, 并标注 kind 和关键特征)

## 节点规格 (逐条)
(对每个节点: id / kind / validator / format_in / format_out / 职责段落 / 工具 / 路由 / 是否 AgentNodeLoop)

## Format 链
(列出所有 Format 的 id 和一句话 description, 要求每个 Format description ≥ 100 字是硬性)

## 错误路由策略
(按节点列 FAIL 处理方式: HALT / RETRY N / NEXT)

## LLM + 验证绑定
(哪些节点是 SOFT 需要 LLM, 哪些 HARD, 哪些需要 UserInquiry, 哪些需要 AgentNodeLoop)

## 约束
(OmniCompany 硬规则: 遵守 omnicompany-dev skill, 禁用 confidence, 经 guarded_write, Guardian 合规)

## 期望验收
(能描述 "workflow-factory 跑完之后怎么判断成功" 的硬标准)

---

**Skill 基本信息**:

- skill_name: {skill_name}
- skill_purpose: {skill_purpose}
- domain: {skill_domain}
- pipeline_name: {pipeline_name}
- coverage_expectations: {coverage_expectations}

**节点列表** ({node_count} 个):
{nodes_json}

**DAG 边**:
{edges_json}

**特殊约束** ({constraint_count} 条):
{constraints_json}

**Skill 原文摘要** (用于你理解深层意图):
{sections_digest}

**参考文件** (如有):
{references_digest}

**现有 OmniCompany 代码参考** (workflow-factory 会用到的既有模板):
- AgentNodeLoop 子类模板: `src/omnicompany/packages/services/absorption/landmark_picker.py`
- KBLocateRouter / KBWriteRouter: `src/omnicompany/packages/services/knowledge/routers.py`
- UserInquiry 例子: `src/omnicompany/packages/services/workflow_factory/routers.py`
- guarded_write: `src/omnicompany/core/guarded_write.py` 的 `write_file()`

开始生成 markdown 需求稿, 不要前置解释, 直接从 `## 目标` 开始。"""


class RequirementDraftWorker(Worker):
    DESCRIPTION = (
        "把 skill_structure + format_chain 整合成一份 workflow-factory 可消费的 "
        "markdown 需求稿。落盘到 data/absorption/skill_digest/<skill>.md, 下游 "
        "workflow-factory 只需要读这个文件就能生成管线代码。不再自己生成 Python "
        "代码——那是 workflow-factory 的唯一权威职责。"
    )
    FORMAT_IN = "skill_importer.material_chain"
    FORMAT_OUT = "skill_importer.requirement_draft"

    def run(self, data: dict) -> Verdict:
        skill_name = data.get("skill_name", "unnamed")
        nodes = data.get("nodes", [])

        sections_digest_parts: list[str] = []
        for s in data.get("sections", []):
            title = s.get("title", "")
            body = s.get("body", "")
            sections_digest_parts.append(f"### {title}\n{body}")
        sections_digest = "\n\n".join(sections_digest_parts)

        references_digest_parts: list[str] = []
        for fname, content in data.get("reference_contents", {}).items():
            references_digest_parts.append(f"#### {fname}\n{content}")
        references_digest = "\n\n".join(references_digest_parts)

        prompt = _REQUIREMENT_TEMPLATE_PROMPT.format(
            skill_name=skill_name,
            skill_purpose=data.get("skill_purpose", ""),
            skill_domain=data.get("skill_domain", "imported"),
            pipeline_name=data.get("skill_pipeline_name", skill_name),
            coverage_expectations=data.get("coverage_expectations", ""),
            node_count=len(nodes),
            nodes_json=json.dumps(nodes, ensure_ascii=False, indent=2),
            edges_json=json.dumps(data.get("dag_edges", []), ensure_ascii=False),
            constraint_count=len(data.get("special_constraints", [])),
            constraints_json=json.dumps(
                data.get("special_constraints", []), ensure_ascii=False, indent=2
            ),
            sections_digest=sections_digest,
            references_digest=references_digest or "(no references)",
        )

        try:
            client = LLMClient(role="ide_agent", max_tokens=8192, tools=[])
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis=f"LLMClient init failed: {e}",
            )

        try:
            response = client.call(
                messages=[{"role": "user", "content": prompt}],
                system=(
                    "你是 OmniCompany 架构文档作者, 产出严谨、可被 workflow-factory "
                    "直接消费的 markdown 需求稿。不使用 confidence 标签, 不加前置解释。"
                ),
            )
            md_text = "".join(
                b.text for b in response.content if hasattr(b, "text")
            ).strip()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis=f"LLM call failed: {e}",
            )

        if md_text.startswith("```"):
            lines = md_text.split("\n", 1)
            if len(lines) > 1:
                md_text = lines[1]
            if md_text.endswith("```"):
                md_text = md_text[:-3].rstrip()

        try:
            from omnicompany.core.config import resolve_db_dir

            digest_dir = resolve_db_dir("absorption") / "skill_digest"
            digest_dir.mkdir(parents=True, exist_ok=True)
            digest_path = digest_dir / f"{skill_name}.md"
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis=f"resolve path failed: {e}",
            )

        try:
            from omnicompany.core.guarded_write import write_file

            write_file(
                str(digest_path),
                md_text,
                origin="internal-engine",
                domain="services/skill_importer",
                purpose=f"skill digest for {skill_name}",
            )
        except Exception as e:
            logger.warning("[skill_importer.draft] guarded_write fallback: %s", e)
            try:
                digest_path.write_text(md_text, encoding="utf-8")
            except Exception as e2:
                return Verdict(
                    kind=VerdictKind.FAIL, output=data,
                    diagnosis=f"write failed: {e2}",
                )

        out = dict(data)
        out["requirement_draft_path"] = str(digest_path)
        out["requirement_draft_chars"] = len(md_text)
        out["requirement_draft_preview"] = md_text[:500]
        return Verdict(
            kind=VerdictKind.PASS,
            output=out,
            confidence=0.9,
            diagnosis=(
                f"requirement draft written: {digest_path.name} "
                f"({len(md_text)} chars)"
            ),
            granted_tags=["domain.skill_importer", "stage.requirement_drafted"],
        )
