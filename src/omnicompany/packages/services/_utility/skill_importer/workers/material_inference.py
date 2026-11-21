# [OMNI] origin=claude-code domain=services/skill_importer ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:utility.skill_importer.material_inference_implementation.py"
"""MaterialInferenceWorker — 确定性 Format 命名推断 (HARD, Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

import re

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


_STOPWORDS = {"the", "a", "an", "of", "to", "for", "with", "and", "or", "is", "are"}


def _desc_to_slug(desc: str) -> str:
    """从一段描述中提取 1-2 个核心英文词作为 concept slug."""
    if not desc:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", desc.lower())
    words = [w for w in cleaned.split() if w and w not in _STOPWORDS and len(w) > 2]
    if not words:
        return ""
    return "_".join(words[:2])


class MaterialInferenceWorker(Worker):
    DESCRIPTION = (
        "为每个节点推断 format_in / format_out 的命名, 采用 <domain>.<concept> 约定。"
        "相邻节点的 format_out 直接被下游 format_in 复用, 保持链式语义一致性。"
    )
    FORMAT_IN = "skill_importer.skill_structure"
    FORMAT_OUT = "skill_importer.material_chain"

    def run(self, data: dict) -> Verdict:
        domain = data.get("skill_domain") or "imported"
        domain_safe = re.sub(r"[^\w]", "_", domain).lower()

        nodes = data.get("nodes", [])
        if not nodes:
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis="no nodes to infer formats for",
            )

        for i, node in enumerate(nodes):
            if i == 0:
                node["format_in"] = f"{domain_safe}.user_request"
            else:
                node["format_in"] = nodes[i - 1]["format_out"]

            out_desc = node.get("output_description") or node.get("title") or f"step_{i}"
            concept = _desc_to_slug(out_desc) or f"step_{i}"
            node["format_out"] = f"{domain_safe}.{concept}"

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
