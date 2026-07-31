# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-15T00:00:00Z type=router
# [OMNI] material_id="material:learning.absorption.v3_legacy.findings_extractor.batch_llm_analyzer.py"
"""learning_extractor — V3 LearningExtractorRouter（分批 LLM 调用）

输入：absorption.module.code（每个模块的实际代码内容）
输出：absorption.learning（可操作的学习发现，绑定 G1-G7）

2026-04-15 C1 改造：
  旧：55 模块一次 LLM 调用，hardcap 10 条 → 82% 信息丢失
  新：按 gap_id 分批，每批独立 LLM 调用，无硬上限 → 单点测试 10→33 findings
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)

_MODEL = "qwen3.6-plus"

# 注意：不再写"最多 10 条"——让 LLM 按实际情况输出
_SYSTEM_BATCH = """你是 OmniCompany 的学习分析师。你会收到同一缺口维度（{gap_id}）下的若干外部 repo 模块代码。

你的任务：对每个模块，判断 OmniCompany 能从中学到什么。

## 判断原则

**what_it_does**：描述代码实际实现了什么，基于你看到的代码。
**omnifactory_delta**：OmniCompany 当前缺少什么。
**action**：OmniCompany 应该怎么做——停在功能层级，不指定具体文件路径。
**portability**：directly_reusable / worth_learning / reference_only

**诚实原则**：没什么可学的就说 reference_only，不要硬凑。

## 路径保真原则

evidence 里的 file 路径**必须原样保留模块标注的路径**，不要改写、推理或美化。

## 输出格式

纯 JSON，无 markdown 代码块：
{{
  "gap_id": "{gap_id}",
  "findings": [
    {{
      "gap_id": "{gap_id}",
      "priority": "P0|P1|P2",
      "title": "简短标题（≤20字）",
      "what_it_does": "...",
      "omnifactory_delta": "...",
      "action": "功能层级描述，不指定文件路径",
      "portability": "directly_reusable | worth_learning | reference_only",
      "evidence": [
        {{"file": "原始路径（不改写）", "lines": "45-67", "quote": "≤80字"}}
      ]
    }}
  ]
}}

每个模块至少产出一条 finding（哪怕是 reference_only）。按 P0→P1→P2 排序。"""


class LearningExtractorRouter(Router):
    """V3 学习提炼节点（分批 LLM 调用）。

    按 gap_id 分组，每组独立 LLM 调用，合并 findings。
    解决旧版"55 模块一次调用 hardcap 10"的 82% 信息丢失问题。
    """

    DESCRIPTION = (
        "V3 学习提炼：按 gap_id 分批 LLM 调用，分析模块代码，"
        "判断 what_it_does / delta / action / portability，产出带证据的发现。"
        "路径保真（不让 LLM 改写文件路径）。无硬上限。"
    )
    FORMAT_IN = "absorption.module.code"
    FORMAT_OUT = "absorption.learning"

    _MODEL = _MODEL
    _MAX_MODULES_PER_BATCH = 10  # 单批超过此数再拆分（G1 有 12+ 模块时）

    def __init__(self, *, model: str | None = None, **kwargs: Any) -> None:
        self._model = model or self._MODEL

    def run(self, input_data: Any) -> Verdict:
        repo_name = input_data.get("repo_name", "unknown")
        module_readings: list[dict] = input_data.get("module_readings") or []
        self_portrait = input_data.get("self_portrait", "")

        if not module_readings:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis="LearningExtractor: module_readings 为空",
            )

        # ── C1: 按 gap_id 分组 ──
        by_gap: dict[str, list[dict]] = defaultdict(list)
        for r in module_readings:
            gap = r.get("gap_id", "unknown")
            by_gap[gap].append(r)

        # ── 分批调用 LLM ──
        all_findings: list[dict] = []
        batch_diagnostics: list[str] = []

        for gap_id in sorted(by_gap):
            modules = by_gap[gap_id]
            # 大组再拆（防 G1 有 15 个模块超窗口）
            sub_batches = [
                modules[i:i + self._MAX_MODULES_PER_BATCH]
                for i in range(0, len(modules), self._MAX_MODULES_PER_BATCH)
            ]
            for batch_idx, batch in enumerate(sub_batches):
                batch_label = f"{gap_id}" if len(sub_batches) == 1 else f"{gap_id}.{batch_idx}"
                findings = self._run_single_batch(
                    repo_name, batch, gap_id, self_portrait, batch_label,
                )
                all_findings.extend(findings)
                batch_diagnostics.append(f"{batch_label}:{len(findings)}")

        # ── 汇总 ──
        p0_count = sum(1 for f in all_findings if f.get("priority") == "P0")
        reusable = sum(1 for f in all_findings if f.get("portability") == "directly_reusable")

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "repo_name": repo_name,
                "findings": all_findings,
                "overall_assessment": {
                    "absorption_value": "high" if p0_count >= 3 else ("medium" if all_findings else "low"),
                    "total_findings": len(all_findings),
                    "batch_breakdown": batch_diagnostics,
                    "summary": f"{len(all_findings)} findings from {len(by_gap)} gap groups",
                },
            },
            confidence=0.85 if all_findings else 0.3,
            diagnosis=(
                f"LearningExtractor: {len(all_findings)} 发现 "
                f"({p0_count} P0, {reusable} directly_reusable), "
                f"batches={batch_diagnostics}"
            ),
            granted_tags=["domain.absorption", "stage.v3.learning"],
        )

    def _run_single_batch(
        self,
        repo_name: str,
        modules: list[dict],
        gap_id: str,
        self_portrait: str,
        batch_label: str,
    ) -> list[dict]:
        """单个 gap_id 批次的 LLM 调用。"""
        # 铁律 A：禁止预防性截断。模块代码原样喂 LLM。
        # token 预算守卫由外层 _MAX_MODULES_PER_BATCH=10 + 按 gap_id 分组提供；
        # 若单批仍超窗口，改进路径是进一步拆分或转 AgentNodeLoop 主动分段读，
        # 绝不在这里截断后半。
        module_sections: list[str] = []
        for r in modules:
            path = r.get("path", "?")
            priority = r.get("priority", "P2")
            content = r.get("content", "")
            module_sections.append(
                f"### [{priority}] {gap_id} — `{path}`\n\n{content}"
            )

        modules_text = "\n\n---\n\n".join(module_sections)
        system = _SYSTEM_BATCH.format(gap_id=gap_id)

        user_msg = f"""# 学习提炼任务 — {gap_id}

**Repo**: {repo_name}
**缺口**: {gap_id}
**模块数量**: {len(modules)} 个

## OmniCompany 自画像
{self_portrait}

---

## 模块代码

{modules_text}

---

请判断每个模块 OmniCompany 能学到什么，输出 JSON。"""

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=system,
                info_audit=False,
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw.strip())
            stripped = raw.lstrip()
            start = stripped.find("{")
            if start >= 0:
                data, _ = json.JSONDecoder().raw_decode(stripped[start:])
            else:
                data = json.loads(stripped)
        except Exception as e:
            logger.warning("LearningExtractor batch %s failed: %s", batch_label, e)
            return []

        findings = data.get("findings") or []
        logger.info(
            "[LearningExtractor] batch %s: %d modules → %d findings",
            batch_label, len(modules), len(findings),
        )
        return findings
