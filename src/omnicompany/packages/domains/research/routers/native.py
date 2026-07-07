# [OMNI] origin=ai-ide domain=research/routers ts=2026-06-30T00:00:00Z type=router status=active
# [OMNI] summary="NativeResearch: 无人值守执行器。codex exec(原生 web_search)按调研协议搜+读+综合,输出受 schema 约束的研究记录。"
# [OMNI] why="2026-06-30 校准:弃外部搜索 API + 便宜模型编排,改用带原生搜索的 frontier agent。交互式由前台 agent 跑(SKILL),无人值守由本节点驱动 codex,二者同协议同 library.save_research_record 落点。"
# [OMNI] tags=research,router,native,codex,websearch
"""NativeResearch —— 无人值守调研执行器(codex 原生搜索)。

intake(research.intake)→ native(research.verified)→ library_write(research.record)。
本节点把「明确需求 → 原生 WebSearch/WebFetch 搜读 → 全面性核查 → 综合带源」整个交给 codex
(开 tools.web_search,readonly 沙箱,--output-schema 强约束产出),不再用便宜模型编排哑搜索。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

# codex --output-schema 走 OpenAI strict 模式: 每个 object 必须 additionalProperties:false
# 且 required 列全所有 property(没有"可选字段"概念,空就给空串/空数组)。
RESEARCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "description": "2-4 句客观概述,不打分"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string", "description": "一句具体结论"},
                    "source_url": {"type": "string", "description": "该结论依据的页 url"},
                    "support": {
                        "type": "string",
                        "enum": ["supported", "partial", "unsupported", "unverified"],
                        "description": "回读来源页后的支撑判定,默认从严",
                    },
                },
                "required": ["claim", "source_url", "support"],
            },
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "snippet": {"type": "string"},
                },
                "required": ["title", "url", "snippet"],
            },
        },
        "keywords": {"type": "array", "items": {"type": "string"}},
        "aliases": {"type": "array", "items": {"type": "string"}, "description": "别名/同义说法,供日后查重召回"},
        "perspectives_covered": {"type": "array", "items": {"type": "string"}},
        "perspectives_open": {"type": "array", "items": {"type": "string"}, "description": "还没覆盖的角度,诚实留白"},
    },
    "required": ["summary", "findings", "sources", "keywords", "aliases",
                 "perspectives_covered", "perspectives_open"],
}


def _build_prompt(topic: str, existing: dict | None) -> str:
    base = f"""你是公开调研员。对题目做联网调研,只用你自己的原生 web 搜索能力(web_search 工具),禁自写爬虫。全程中文。

题目:{topic}

步骤:
1. 明确需求:先想清楚这题到底要回答什么、范围/时间;列出这个概念的别名/同义说法(中英、行话、产品名 vs 通称)和要覆盖的多视角(机制/对比选型/反对与替代/落地与坑/基础覆盖),至少含一个冷门或替代视角。
2. 搜+读:逐角度搜,同一角度换几种说法各搜一次(覆盖第 1 步的别名清单);读有料的页,抽出带 source_url 的发现(claim 必须能在某个来源页找到支撑)。
3. 全面性核查:对着第 1 步的别名/角度清单逐项核——每个同义词、每个角度都真发起过搜索了吗?缺口能补就再搜,暂不深挖的写进 perspectives_open(诚实留白)。
4. 核源:每条 finding 标 support——回读它声称的来源页,看得到明确支撑才给 supported,沾边不充分给 partial,找不到/矛盾给 unsupported,没核到给 unverified;默认从严。
5. 只认页面里有的,绝不编造;客观、给证据、不打分。

只输出符合 output schema 的 JSON(summary/findings/sources/keywords/aliases/perspectives_covered/perspectives_open),不要别的话。"""
    if existing:
        covered = existing.get("perspectives_covered") or []
        open_p = existing.get("perspectives_open") or []
        base += (
            f"\n\n【增量】库内已有同题记录,已覆盖角度:{covered};还缺:{open_p}。"
            "重点补还缺的角度与新进展,别重复已覆盖的;findings/sources 给新增的即可(系统会自动与旧记录合并)。"
        )
    return base


class NativeResearch(Router):
    """无人值守执行器:codex(原生 web_search)按调研协议产出受 schema 约束的研究记录。"""

    DESCRIPTION = "原生调研: codex 原生 web 搜索按协议搜+读+核源+综合(无外部搜索 API)"
    FORMAT_IN = "research.intake"
    FORMAT_OUT = "research.verified"
    REQUIRED_CONTEXT = ["topic", "run_dir"]

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.packages.services._core.agent.external_workers import (
            ExternalAgentPermissionMode,
            ExternalAgentRunRequest,
            run_external_agent_request,
        )

        ctx = input_data if isinstance(input_data, dict) else {}
        topic = ctx["topic"]
        run_dir = Path(ctx["run_dir"])
        existing = ctx.get("existing")
        timeout_s = float(ctx.get("timeout_s", 900) or 900)

        schema_path = run_dir / "output_schema.json"
        schema_path.write_text(json.dumps(RESEARCH_OUTPUT_SCHEMA, ensure_ascii=False), encoding="utf-8")

        request = ExternalAgentRunRequest(
            provider="codex",
            prompt=_build_prompt(topic, existing),
            cwd=str(run_dir),
            permission_mode=ExternalAgentPermissionMode.READONLY,
            model_policy="none",  # 调研要好脑子,别降便宜档
            timeout_s=timeout_s,
            output_schema_path=schema_path,
            metadata={"codex_config": {"tools.web_search": "true"}},
        )
        result = asyncio.run(run_external_agent_request(request))

        data = getattr(result, "structured_output", None)
        if not isinstance(data, dict):
            # 兜底:从 final_text 里捞 JSON
            try:
                data = json.loads((getattr(result, "final_text", "") or "").strip())
            except (json.JSONDecodeError, ValueError):
                data = None

        # codex 的错误信封也是 dict(如 {"type":"error","status":400,...})——必须识别,
        # 否则会被当空结果落库成"干净"的 0 发现记录。要求出现协议关键键才算真产出。
        def _is_record(d: Any) -> bool:
            return isinstance(d, dict) and "summary" in d and "findings" in d and "type" not in d

        if not _is_record(data):
            err = ""
            if isinstance(data, dict):
                err = (data.get("error") or {}).get("message") or str(data)[:300]
            (run_dir / "native_raw.txt").write_text(
                (getattr(result, "final_text", "") or "") + ("\n\n" + err if err else ""),
                encoding="utf-8")
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**ctx, "synthesis": {"summary": "(codex 调研未产出合法记录)", "findings": []},
                        "sources": [], "coverage": {}},
                diagnosis=f"codex 调研失败 status={getattr(result, 'status', '?')}: {err[:160]}"
                          "(原文存 native_raw.txt)",
            )

        synthesis = {
            "summary": data.get("summary", ""),
            "findings": data.get("findings") or [],
            "keywords": data.get("keywords") or [],
            "aliases": data.get("aliases") or [],
            "perspectives_open": data.get("perspectives_open") or [],
        }
        sources = data.get("sources") or []
        coverage = {"covered": data.get("perspectives_covered") or []}

        (run_dir / "native.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        n = len(synthesis["findings"])
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "topic": topic, "topic_norm": ctx.get("topic_norm", ""), "run_dir": str(run_dir),
                "synthesis": synthesis, "sources": sources, "coverage": coverage,
                "existing": existing,
            },
            confidence=1.0 if n else 0.0,
            diagnosis=f"codex 原生调研: {n} 条发现 · {len(sources)} 源 · 覆盖 {len(coverage['covered'])} 视角",
            granted_tags=["domain.research", "stage.verified"],
        )
