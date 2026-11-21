# [OMNI] origin=claude-code domain=skill_importer/_archive/routers_legacy.py ts=2026-04-20T00:00:00Z
# [OMNI] material_id="material:utility.skill_importer.legacy_router_archive.py"
# OMNI-024 ALLOW: _archive/ 归档文件，Router 类不在标准位置属预期 (Phase D Diamond shortcut)
# [OMNI] DEPRECATED 2026-04-22 — Stage 3 Clean Migration 完成, 业务代码已迁到 workers/*.py:
#   SkillParserRouter          → workers/skill_parser.py          (SkillParserWorker)
#   StructureAnalysisRouter    → workers/structure_analysis.py    (StructureAnalysisWorker)
#   FormatInferenceRouter      → workers/format_inference.py      (FormatInferenceWorker)
#   RequirementDraftRouter     → workers/requirement_draft.py     (RequirementDraftWorker)
#   VerifyAgainstSkillRouter   → workers/verify_against_skill.py  (VerifyAgainstSkillWorker)
# 本文件仅保留作为历史参考, 不再被 workers/__init__.py 继承。
"""skill_importer routers — 2026-04-09 重构版 (DEPRECATED, 见文件头).

变更:
- 废弃原 CodeGeneratorRouter (和一堆 _gen_* 手工模板函数), 它生成的 Python 代码有语法
  bug 且平铺 TRANSFORMER, 不如 workflow-factory 智能
- 新增 RequirementDraftRouter: 把 parse/analyze/infer 的结构化结果产出为
  markdown 需求稿, 落到 data/absorption/skill_digest/<skill>.md, 供 workflow-factory
  直接消费
- 新增 VerifyAgainstSkillRouter: 跑在 workflow-factory 产物后面, 做"生成的管线是否
  覆盖 skill 的所有要求"的忠实度检验

整体分工 (和 workflow-factory 的重合决策):
- skill_importer 只做: 解析 skill md → 结构化 → 产需求稿 → 最后验证是否忠于 skill
- workflow-factory 才是 "详细需求 → 可用管线" 的唯一权威源
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.runtime.llm.llm import LLMClient
from omnifactory.runtime.routing.router import Router

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 1. SkillParserRouter — 解析 SKILL.md + references/ + scripts/
# ═══════════════════════════════════════════════════════════

class SkillParserRouter(Router):
    DESCRIPTION = (
        "解析一个 Claude Code Skill 目录 (含 SKILL.md / references/ / scripts/) "
        "为结构化 sections 列表。输出含标题层级 + 正文, 供下游做语义归纳。"
    )
    FORMAT_IN = "skill_importer.raw"
    FORMAT_OUT = "skill_importer.parsed_sections"

    def run(self, data: dict) -> Verdict:
        if not isinstance(data, dict) or "skill_dir" not in data:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis="input 必须含 skill_dir 字段",
            )

        skill_dir = Path(data["skill_dir"])
        skill_md = skill_dir / "SKILL.md"

        if not skill_md.exists():
            return Verdict(
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis=f"SKILL.md not found at {skill_md}",
            )

        content = skill_md.read_text(encoding="utf-8")

        # 按 markdown 标题切段
        sections: list[dict] = []
        for line in content.split("\n"):
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                sections.append(
                    {"title": line.strip("# ").strip(), "level": level, "body": ""}
                )
            elif sections:
                sections[-1]["body"] += line + "\n"

        # 抓 references/ 和 scripts/ 子目录
        # 扩展: 不只抓顶层 md, 递归抓 .md 文件 (repo-analyzer 这种 skill 的
        # references/ 下会有多份 guide 文件)
        reference_contents: dict[str, str] = {}
        ref_dir = skill_dir / "references"
        if ref_dir.exists():
            for ref in ref_dir.rglob("*.md"):
                rel = ref.relative_to(ref_dir)
                try:
                    reference_contents[str(rel).replace("\\", "/")] = ref.read_text(
                        encoding="utf-8"
                    )
                except OSError:
                    continue

        scripts_contents: dict[str, str] = {}
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.exists():
            for script in scripts_dir.iterdir():
                if script.is_file():
                    try:
                        # 2026-04-21 铁律 A 修复: 移除 [:2000] 预防性截断, 送完整脚本
                        scripts_contents[script.name] = script.read_text(encoding="utf-8")
                    except OSError:
                        continue

        skill_name = skill_dir.name

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "skill_name": skill_name,
                "skill_dir": str(skill_dir),
                "out_dir": data.get("out_dir", ""),
                "sections": sections,
                "reference_contents": reference_contents,
                "scripts_contents": scripts_contents,
                "total_skill_chars": len(content)
                + sum(len(v) for v in reference_contents.values())
                + sum(len(v) for v in scripts_contents.values()),
            },
            confidence=1.0,
            diagnosis=(
                f"parsed {len(sections)} sections, "
                f"{len(reference_contents)} reference files, "
                f"{len(scripts_contents)} scripts"
            ),
            granted_tags=["domain.skill_importer", "stage.parsed"],
        )


# ═══════════════════════════════════════════════════════════
# 2. StructureAnalysisRouter — LLM 归纳 skill 结构
# ═══════════════════════════════════════════════════════════

_ANALYSIS_PROMPT = """You are acting as an OmniCompany LAP Compiler.
Your job is to translate a Claude Code Skill into a structured pipeline blueprint.

Input sections (SKILL.md):
{sections_json}

Reference files (excerpts):
{reference_contents}

Scripts files (excerpts):
{scripts_contents}

Extract the following **strictly** as JSON (no markdown fencing):

{{
  "skill_purpose": "一句话描述此 skill 的核心用途",
  "skill_domain": "suggested pipeline domain name, snake_case",
  "skill_pipeline_name": "suggested pipeline name, hyphen-case",
  "nodes": [
    {{
      "id": "snake_case_id",
      "title": "Human readable title",
      "kind": "ANCHOR" | "TRANSFORMER" | "SCATTER",
      "is_llm": true | false,
      "uses_user_interaction": true | false,
      "uses_subagent_parallelism": true | false,
      "input_description": "1 sentence on what data comes in",
      "output_description": "1 sentence on what data goes out",
      "knowledge_points": ["exact rules / URLs / schema snippets from source, no paraphrasing"],
      "tools_required": ["tool names or file paths"]
    }}
  ],
  "dag_edges": [
    {{"source": "id_1", "target": "id_2", "condition": "PASS"}},
    {{"source": "id_2", "target": "id_1", "condition": "FAIL"}}
  ],
  "special_constraints": [
    "strict rules the skill enforces, e.g. '主 agent 在 subagent 运行期间不读子 agent 负责的文件'"
  ],
  "coverage_expectations": "如何衡量 '这个 skill 跑完算不算合格' 的标准"
}}

Rules:
- Order nodes by logical execution sequence
- Do NOT drop technical details; knowledge_points 必须包含原文的精确规则 / 阈值 / URL
- If skill has "subagent parallel analysis", set uses_subagent_parallelism=true and mark that node kind=SCATTER
- dag_edges use PASS for nominal flow, FAIL for retry loops
- Output ONLY the JSON object, no markdown code fence, no explanatory text"""


class StructureAnalysisRouter(Router):
    DESCRIPTION = (
        "基于 parsed sections 让 LLM 归纳 skill 的核心结构: 目的 / 节点列表 / "
        "依赖边 / 特殊约束 / 覆盖预期。输出是结构化 JSON, 供下游 FormatInference 和 "
        "RequirementDraft 消费。"
    )
    FORMAT_IN = "skill_importer.parsed_sections"
    FORMAT_OUT = "skill_importer.skill_structure"

    def run(self, data: dict) -> Verdict:
        sections_for_llm = [
            # 2026-04-21 铁律 A 修复: 移除 body[:1200] + sections[:30] 截断
            # qwen3.6-plus 1M context 足够消化完整 sections 清单
            {"title": s["title"], "level": s["level"], "body_preview": s["body"]}
            for s in data.get("sections", [])
        ]
        prompt = _ANALYSIS_PROMPT.format(
            sections_json=json.dumps(sections_for_llm, ensure_ascii=False),
            # 2026-04-21 铁律 A 修复: 移除 v[:2000] 预防性截断
            reference_contents=json.dumps(
                data.get("reference_contents", {}),
                ensure_ascii=False,
            ),
            scripts_contents=json.dumps(data.get("scripts_contents", {}), ensure_ascii=False),
        )

        try:
            client = LLMClient(role="ide_agent", max_tokens=8192, tools=[])
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis=f"LLMClient init failed: {e}",
            )

        try:
            response = client.call(
                messages=[{"role": "user", "content": prompt}],
                system="Output ONLY JSON, no markdown fence, no prose.",
            )
            text = "".join(
                b.text for b in response.content if hasattr(b, "text")
            ).strip()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis=f"LLM call failed: {e}",
            )

        # 宽松 JSON 解析 (容忍 ``` fence + 裸换行)
        parsed = _parse_json_loose(text)
        if parsed is None or not isinstance(parsed, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis=f"LLM JSON parse failed; first 300 chars: {text[:300]}",
            )

        if not parsed.get("nodes"):
            return Verdict(
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis="LLM 未返回任何节点, 分析失败",
            )

        out = dict(data)
        out.update({
            "skill_purpose": parsed.get("skill_purpose", ""),
            "skill_domain": parsed.get("skill_domain", "imported"),
            "skill_pipeline_name": parsed.get(
                "skill_pipeline_name", data["skill_name"].replace("_", "-")
            ),
            "nodes": parsed["nodes"],
            "dag_edges": parsed.get("dag_edges", []),
            "special_constraints": parsed.get("special_constraints", []),
            "coverage_expectations": parsed.get("coverage_expectations", ""),
        })
        return Verdict(
            kind=VerdictKind.PASS,
            output=out,
            confidence=0.85,
            diagnosis=(
                f"analyzed: {len(out['nodes'])} nodes, "
                f"{len(out['dag_edges'])} edges, "
                f"{len(out['special_constraints'])} constraints"
            ),
            granted_tags=["domain.skill_importer", "stage.analyzed"],
        )


# ═══════════════════════════════════════════════════════════
# 3. FormatInferenceRouter — 推断 Format 命名
# ═══════════════════════════════════════════════════════════

class FormatInferenceRouter(Router):
    DESCRIPTION = (
        "为每个节点推断 format_in / format_out 的命名, 采用 <domain>.<concept> 约定。"
        "相邻节点的 format_out 直接被下游 format_in 复用, 保持链式语义一致性。"
    )
    FORMAT_IN = "skill_importer.skill_structure"
    FORMAT_OUT = "skill_importer.format_chain"

    def run(self, data: dict) -> Verdict:
        domain = data.get("skill_domain") or "imported"
        domain_safe = re.sub(r"[^\w]", "_", domain).lower()

        nodes = data.get("nodes", [])
        if not nodes:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis="no nodes to infer formats for",
            )

        # 用语义名而不是 output_0 / output_1
        # 策略: 从 output_description 提取 1~2 个核心词作为 concept slug
        for i, node in enumerate(nodes):
            if i == 0:
                node["format_in"] = f"{domain_safe}.user_request"
            else:
                node["format_in"] = nodes[i - 1]["format_out"]

            out_desc = node.get("output_description") or node.get("title") or f"step_{i}"
            concept = _desc_to_slug(out_desc) or f"step_{i}"
            node["format_out"] = f"{domain_safe}.{concept}"

            # 避免重名 (同 concept 加数字后缀)
            used = [n["format_out"] for n in nodes[:i]]
            if node["format_out"] in used:
                cnt = 2
                while f"{node['format_out']}_{cnt}" in used:
                    cnt += 1
                node["format_out"] = f"{node['format_out']}_{cnt}"

        out = dict(data)
        out["nodes"] = nodes
        return Verdict(
            kind=VerdictKind.PASS,
            output=out,
            confidence=1.0,
            diagnosis=f"format chain inferred for {len(nodes)} nodes, domain={domain_safe}",
            granted_tags=["domain.skill_importer", "stage.format_inferred"],
        )


# ═══════════════════════════════════════════════════════════
# 4. RequirementDraftRouter — 产出 workflow-factory 需求稿
# ═══════════════════════════════════════════════════════════

_REQUIREMENT_TEMPLATE_PROMPT = """你在把一个 Claude Code Skill 翻译成 OmniCompany
workflow-factory 可消费的 **需求稿**。workflow-factory 会读这份需求稿, 生成完整的
OmniCompany LAP 管线 (formats.py / routers.py / pipeline.py / run.py)。

你的输出必须是 **markdown**, 段落结构严格如下, 用中文。

## 目标
(3-5 句话描述这条管线做什么, 为什么要有它, 目标 skill 是什么)

## 新 Package 位置
(形如 `src/omnifactory/packages/services/<name>/`, 解释为什么是 services 而不是 domains)

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
(OmniCompany 硬规则: 遵守 omnifactory-dev skill, 禁用 confidence, 经 guarded_write, Guardian 合规)

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
- AgentNodeLoop 子类模板: `src/omnifactory/packages/services/absorption/landmark_picker.py`
- KBLocateRouter / KBWriteRouter: `src/omnifactory/packages/services/knowledge/routers.py`
- UserInquiry 例子: `src/omnifactory/packages/services/workflow_factory/routers.py`
- guarded_write: `src/omnifactory/core/guarded_write.py` 的 `write_file()`

开始生成 markdown 需求稿, 不要前置解释, 直接从 `## 目标` 开始。"""


class RequirementDraftRouter(Router):
    DESCRIPTION = (
        "把 skill_structure + format_chain 整合成一份 workflow-factory 可消费的 "
        "markdown 需求稿。落盘到 data/absorption/skill_digest/<skill>.md, 下游 "
        "workflow-factory 只需要读这个文件就能生成管线代码。不再自己生成 Python "
        "代码——那是 workflow-factory 的唯一权威职责。"
    )
    FORMAT_IN = "skill_importer.format_chain"
    FORMAT_OUT = "skill_importer.requirement_draft"

    def run(self, data: dict) -> Verdict:
        skill_name = data.get("skill_name", "unnamed")
        nodes = data.get("nodes", [])

        # 2026-04-21 铁律 A 修复: 原"节选而非全文以避免 prompt 爆炸"已违反铁律 A
        # 移除 sections[:20] 数量截断 + body[:600] 内容截断, qwen3.6-plus 1M 足够
        sections_digest_parts: list[str] = []
        for s in data.get("sections", []):
            title = s.get("title", "")
            body = s.get("body", "")
            sections_digest_parts.append(f"### {title}\n{body}")
        # 2026-04-21 铁律 A 修复: 移除 [:6000] 预防性截断
        sections_digest = "\n\n".join(sections_digest_parts)

        references_digest_parts: list[str] = []
        # 2026-04-21 铁律 A 修复: 移除 list(...)[:4] 文件数量截断 + content[:1500] 内容截断
        for fname, content in data.get("reference_contents", {}).items():
            references_digest_parts.append(f"#### {fname}\n{content}")
        # 2026-04-21 铁律 A 修复: 移除 [:4000] 预防性截断
        references_digest = "\n\n".join(references_digest_parts)

        prompt = _REQUIREMENT_TEMPLATE_PROMPT.format(
            skill_name=skill_name,
            skill_purpose=data.get("skill_purpose", ""),
            skill_domain=data.get("skill_domain", "imported"),
            pipeline_name=data.get("skill_pipeline_name", skill_name),
            coverage_expectations=data.get("coverage_expectations", ""),
            node_count=len(nodes),
            # 2026-04-21 铁律 A 修复: 移除 [:6000] 预防性截断
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
                kind=VerdictKind.FAIL,
                output=data,
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
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis=f"LLM call failed: {e}",
            )

        # 剥可能的 markdown fence
        if md_text.startswith("```"):
            lines = md_text.split("\n", 1)
            if len(lines) > 1:
                md_text = lines[1]
            if md_text.endswith("```"):
                md_text = md_text[:-3].rstrip()

        # 落盘到 data/absorption/skill_digest/<skill>.md (使用 guarded_write)
        try:
            from omnifactory.core.config import resolve_db_dir

            digest_dir = resolve_db_dir("absorption") / "skill_digest"
            digest_dir.mkdir(parents=True, exist_ok=True)
            digest_path = digest_dir / f"{skill_name}.md"
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis=f"resolve path failed: {e}",
            )

        try:
            from omnifactory.core.guarded_write import write_file

            write_file(
                str(digest_path),
                md_text,
                origin="internal-engine",
                domain="services/skill_importer",
                purpose=f"skill digest for {skill_name}",
            )
        except Exception as e:
            # fallback: 裸 write (仅在 guarded_write 不可用时)
            logger.warning(
                "[skill_importer.draft] guarded_write fallback: %s", e
            )
            try:
                digest_path.write_text(md_text, encoding="utf-8")
            except Exception as e2:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output=data,
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


# ═══════════════════════════════════════════════════════════
# 5. VerifyAgainstSkillRouter — 忠实度检验
# ═══════════════════════════════════════════════════════════

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


class VerifyAgainstSkillRouter(Router):
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
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis="input must be dict",
            )

        package_path_str = data.get("package_path")
        skill_structure = data.get("skill_structure")

        if not package_path_str or not skill_structure:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis=(
                    "VerifyAgainstSkillRouter 需要 package_path 和 skill_structure 字段. "
                    "典型调用: 先跑 skill-import (parse+analyze+infer+draft) 拿 skill_structure, "
                    "再跑 workflow-factory 拿 package_path, 最后调用本 Router 做检验。"
                ),
            )

        package_path = Path(package_path_str)
        if not package_path.exists():
            return Verdict(
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis=f"package path not found: {package_path}",
            )

        # 读 package 核心文件
        py_files = sorted(package_path.glob("*.py"))
        package_content_parts: list[str] = []
        file_list: list[str] = []
        for pf in py_files:
            file_list.append(pf.name)
            try:
                content = pf.read_text(encoding="utf-8")
                # 2026-04-21 铁律 A 修复: 移除 content[:3000] 预防性截断, 送完整文件
                package_content_parts.append(
                    f"### {pf.name}\n```python\n{content}\n```"
                )
            except OSError:
                package_content_parts.append(f"### {pf.name}\n(read error)")

        # 2026-04-21 铁律 A 修复: 移除 [:20000] aggregate 截断
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
                kind=VerdictKind.FAIL,
                output=data,
                diagnosis=f"LLM compliance check failed: {e}",
            )

        # 落盘到 data/absorption/skill_digest/<skill>.compliance.md
        try:
            from omnifactory.core.config import resolve_db_dir
            from omnifactory.core.guarded_write import write_file

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

        # 粗略解析 "整体结论" 段判断 verdict
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


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def _parse_json_loose(text: str) -> Any:
    """容忍 ``` fence 和裸换行的宽松 JSON 解析."""
    stripped = text.strip()

    # 直接 parse
    try:
        return json.loads(stripped, strict=False)
    except Exception:
        pass

    # 剥 fence
    fence_match = re.match(r"```(?:\w+)?\s*\n", stripped)
    if fence_match:
        body = stripped[fence_match.end() :]
        if body.endswith("```"):
            body = body[:-3].rstrip()
        try:
            return json.loads(body, strict=False)
        except Exception:
            pass

    # first { 到 last }
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first >= 0 and last > first:
        try:
            return json.loads(stripped[first : last + 1], strict=False)
        except Exception:
            pass

    return None


_STOPWORDS = {"the", "a", "an", "of", "to", "for", "with", "and", "or", "is", "are"}


def _desc_to_slug(desc: str) -> str:
    """从一段描述中提取 1-2 个核心英文词作为 concept slug.

    策略: 小写化 → 去符号 → 取前 3 个非 stopword → 用 _ 连接。
    """
    if not desc:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", desc.lower())
    words = [w for w in cleaned.split() if w and w not in _STOPWORDS and len(w) > 2]
    if not words:
        return ""
    # 取前 2 个, 符合命名习惯 (e.g., analysis_request / scope_validated)
    return "_".join(words[:2])
