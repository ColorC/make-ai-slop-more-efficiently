# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker status=deprecated
# [OMNI] summary="TeamNarrativeChecker — V3 L4 LLM 叙事审计 worker. 软归档 2026-05-06: 判定点已反推为 H-2026-05-06-011..015 进新假设库, 用 HypothesisDiagnosticAgent 替代"
# [OMNI] why="诊断重制阶段 3 项 2: D 类老 LLM worker 软归档. 物理归档待 V3 管线整体替换 (plan §六 阶段 8)"
# [OMNI] tags=worker,doctor,deprecated,phase-3-soft-archive
# [OMNI] material_id="material:diagnosis.doctor.worker.team.pipeline_narrative_auditor.py"
"""TeamNarrativeChecker — L4 LLM 叙事审计 (SOFT, Stage 3 2026-04-22).

## DEPRECATED (2026-05-06 软归档, V3 整体替换时物理归档)

本 worker 的 4 维判定点已反推为新假设入 `data/services/doctor/hypotheses/`:
- H-2026-05-06-011: Team LLM worker 失败应 passed=None (SKIP), 不抛异常阻断管线
- H-2026-05-06-012: Team 叙事审计 4 维 (连贯性 / 语义跳跃 / 意图对齐 / 节点单一)
- H-2026-05-06-013: purpose 多级回退加载 (manifest → spec → description)
- H-2026-05-06-014: 整体判定等级映射规则 (C/D = passed=False, A/B = INFO)
- H-2026-05-06-015: TeamSpec 注入 prompt 含 Format 链 + 边级信息流 (BFS 节点顺序)

新替代路径: 用 `HypothesisDiagnosticAgent` 跑这 5 条假设替代本 worker.

物理归档时机: plan §六 阶段 8 (闭环跑通 + 用户验收) 后.

---

Worker 协议:
  FORMAT_IN  = diag.team.extracted
  FORMAT_OUT = diag.team.check.narrative

诊断目标: LLM 评估整管线的:
  - 叙事连贯性: 从入口到出口, 每一步是否有清晰的信息增量
  - 语义跳跃: 哪条边两侧信息差距过大, 难以用 DESCRIPTION 解释
  - 意图对齐: 整体结构是否服务了 purpose 声明的业务目标
  - 节点单一性: 哪个节点做了超出 DESCRIPTION 声明的事情

LLM 失败时降级为 SKIP (passed=None), 不阻断管线.
输出 check_narrative 字段, advisory 级别 Finding.
"""
from __future__ import annotations

import json as _json
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


_SYSTEM = """\
你是 OmniCompany 管线叙事审计员。你将获得一条管线的完整语义信息。
你的任务：评估这条管线的叙事连贯性，找出语义问题，产出结构化 JSON。

## 审计重点

1. **叙事连贯性**：从入口到出口，每一步节点是否有清晰的信息增量？
   - 每个节点消费什么 → 产出什么 → 下游用它做什么？
   - 信息流是否自然流动，还是存在不必要的数据来回传递？

2. **语义跳跃**：某条边两侧的信息差距是否过大，难以用节点 DESCRIPTION 解释？
   - 格式突变（如：原始 CSV → 完整 Schema，中间是否遗漏了关键步骤？）
   - 信息增量不可解释（节点声称做一件事，但实际上必须做更多）

3. **意图对齐**：整体结构是否服务了 purpose 声明的业务目标？
   - 是否有节点完全游离于 purpose 之外？
   - purpose 声明的关键输出，是否真的由终端节点产出？

4. **节点单一性**：某个节点的 DESCRIPTION 是否承担了过多职责？

## 输出格式

严格输出合法 JSON（不要 markdown 代码块）：

{
  "narrative_coherent": true/false,
  "has_semantic_jump": true/false,
  "semantic_jump_locations": ["edge:node_a→node_b（跳跃描述）"],
  "purpose_aligned": true/false,
  "purpose_alignment_notes": "意图对齐分析",
  "violation_nodes": ["node_id（问题描述）"],
  "overall_grade": "A/B/C/D",
  "key_findings": ["最关键的 1-3 条发现，具体有针对性"],
  "improvement_suggestions": ["具体改进建议"],
  "summary": "一句话总结（中文，≤50字）"
}

评级标准：
- A: 叙事连贯 + 无语义跳跃 + 意图对齐
- B: 基本连贯，有轻微跳跃或局部不对齐
- C: 存在明显语义跳跃 OR 部分游离于 purpose
- D: 叙事断裂 OR 结构完全不服务声明意图
"""


class TeamNarrativeChecker(Worker):
    """L4 LLM 整管线语义连贯性审计."""

    DESCRIPTION = (
        "L4 整管线叙事审计 (LLM): 给定完整 Format 链 + 所有节点 DESCRIPTION + purpose/design_rationale, "
        "评估叙事连贯性 (信息增量是否可解释)、语义跳跃 (哪条边信息差距过大)、意图对齐 (结构是否服务业务目标). "
        "LLM 失败时降级为 SKIP (passed=None), 不阻断管线. "
        "输出 check_narrative 字段, advisory 级别 Finding."
    )
    FORMAT_IN = "diag.team.extracted"
    FORMAT_OUT = "diag.team.check.narrative"

    def __init__(self, model: str | None = None):
        # 项目唯一 LLM: qwen3.6-plus. 保留 model 参数以便测试覆写.
        self._model = model or "qwen3.6-plus"

    def run(self, input_data: Any) -> Verdict:
        specs_data: list[dict] = input_data.get("specs_data", [])
        pipeline_file: str = input_data.get("pipeline_file", "")

        if not specs_data:
            output = dict(input_data)
            output["check_narrative"] = {
                "check": "narrative",
                "passed": None,
                "severity": "INFO",
                "detail": "无 TeamSpec 数据, 跳过叙事审计",
                "findings": [],
            }
            return Verdict(kind=VerdictKind.PASS, confidence=1.0, output=output,
                           diagnosis="PipelineNarrativeChecker: 无数据, 跳过")

        all_audit_results: list[dict] = []
        for spec_data in specs_data:
            audit = self._audit_spec(spec_data, pipeline_file)
            all_audit_results.append(audit)

        any_fail = any(r.get("overall_grade") in ("C", "D") for r in all_audit_results)
        findings = []
        for r in all_audit_results:
            pid = r.get("pipeline_id", "unknown")
            if r.get("has_semantic_jump"):
                for loc in r.get("semantic_jump_locations", []):
                    findings.append({
                        "pipeline_id": pid,
                        "check_id": "narrative_semantic_jump",
                        "level": "advisory",
                        "location": loc,
                        "observation": f"语义跳跃: {loc}",
                    })
            if not r.get("purpose_aligned"):
                findings.append({
                    "pipeline_id": pid,
                    "check_id": "narrative_purpose_misalign",
                    "level": "advisory",
                    "location": f"pipeline:{pid}",
                    "observation": f"意图不对齐: {r.get('purpose_alignment_notes', '')}",
                })
            for vn in r.get("violation_nodes", []):
                findings.append({
                    "pipeline_id": pid,
                    "check_id": "narrative_node_overload",
                    "level": "advisory",
                    "location": f"node:{vn.split('(')[0] if '(' in vn else vn}",
                    "observation": f"节点职责过重: {vn}",
                })

        output = dict(input_data)
        output["check_narrative"] = {
            "check": "narrative",
            "passed": not any_fail,
            "severity": "MEDIUM" if any_fail else "INFO",
            "audit_results": all_audit_results,
            "findings": findings,
        }
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=f"PipelineNarrativeChecker: {len(all_audit_results)} pipelines, {len(findings)} findings",
        )

    def _audit_spec(self, spec_data: dict, pipeline_file: str) -> dict:
        """对单个 TeamSpec 执行叙事审计."""
        try:
            from omnicompany.protocol.team import TeamSpec
            spec = TeamSpec.model_validate(spec_data)
        except Exception:
            return {"pipeline_id": spec_data.get("id", "?"), "overall_grade": "?",
                    "error": "spec 反序列化失败"}

        # 尝试加载 manifest (获取 purpose / design_rationale)
        manifest = None
        try:
            from omnicompany.protocol.manifest import load_manifest
            manifest = load_manifest(pipeline_file)
        except Exception:
            pass

        purpose = (
            (manifest.purpose if manifest else None)
            or spec.purpose
            or spec.description
            or ""
        )
        design_rationale = (manifest.design_rationale if manifest else "") or ""

        # 构建 Format 链描述
        node_map = {n.id: n for n in spec.nodes}
        format_chain_lines: list[str] = []
        for edge in spec.edges:
            src = node_map.get(edge.source)
            tgt = node_map.get(edge.target)
            if not src or not tgt:
                continue
            try:
                src_fmt = src.format_out
                tgt_fmt = tgt.format_in
            except Exception:
                continue
            cond = f"[{edge.condition.value}]" if edge.condition else ""
            fb = "[feedback]" if edge.feedback else ""
            format_chain_lines.append(
                f"  {edge.source}({src_fmt}) →{cond}{fb} {edge.target}({tgt_fmt})"
            )

        # 节点 DESCRIPTION (BFS 顺序)
        node_descs: list[str] = []
        visited: set[str] = set()
        queue = [spec.entry]
        out_edges_map: dict[str, list[str]] = {}
        for e in spec.edges:
            out_edges_map.setdefault(e.source, []).append(e.target)
        while queue:
            nid = queue.pop(0)
            if nid in visited:
                continue
            visited.add(nid)
            node = node_map.get(nid)
            if node:
                desc = ""
                if node.anchor:
                    desc = node.anchor.validator.description if node.anchor.validator else ""
                elif node.transformer:
                    desc = node.transformer.description or ""
                node_descs.append(f"  [{nid}] {desc}")
            for nxt in out_edges_map.get(nid, []):
                if nxt not in visited:
                    queue.append(nxt)

        user_msg = (
            f"## 管线 ID: {spec.id}\n\n"
            f"## Purpose (业务目标)\n{purpose or '(未声明)'}\n\n"
            + (f"## Design Rationale (设计理由)\n{design_rationale}\n\n" if design_rationale else "")
            + "## Format 链 (边级别)\n" + "\n".join(format_chain_lines) + "\n\n"
            + "## 节点 DESCRIPTION (执行顺序)\n" + "\n".join(node_descs) + "\n"
        )

        try:
            # 走统一 LLM 设施 (role-based), 不硬传 model
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(role="runtime_main")
            response = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=_SYSTEM,
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                import re as _re
                raw = _re.sub(r"^```[a-z]*\n?", "", raw)
                raw = _re.sub(r"\n?```$", "", raw.strip())
            audit_data = _json.loads(raw)
            audit_data["pipeline_id"] = spec.id
            return audit_data
        except Exception as exc:
            return {
                "pipeline_id": spec.id,
                "overall_grade": "?",
                "narrative_coherent": None,
                "has_semantic_jump": False,
                "semantic_jump_locations": [],
                "purpose_aligned": None,
                "purpose_alignment_notes": "",
                "violation_nodes": [],
                "key_findings": [],
                "improvement_suggestions": [],
                "summary": f"LLM 审计失败 ({type(exc).__name__})",
                "error": str(exc),
            }
