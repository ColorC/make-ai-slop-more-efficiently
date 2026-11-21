# [OMNI] origin=claude-code domain=runtime/agent_crystallize/patch_self_judge ts=2026-04-15T00:00:00Z
# [OMNI] material_id="material:runtime.agent_crystallize.patch_self_judge.quality_gate.py"
"""patch_self_judge — SpecPatch 产出后的自判断准入门槛.

Exp F 发现两类失效:
  1. 阈值软 (P4): LLM 识别出"近义词替换无增益"但给 borderline 而非 reject
  2. 结构漏洞 (P5): LLM 信任 evidence 文本，不核实工具是否真实存在

修复:
  1. 阈值收紧: verdict 必须是 approve 才通过 (borderline → reject)
  2. 工具白名单交叉验证: 若 patch 提到某工具, 检查它是否在 actual_tools 白名单里
     → 幻觉工具 (如 web_search) 直接 reject, 不问 LLM

对 Exp F 五条 patch 的预期效果:
  P1 approve   → approve  ✓ (local_list 在白名单里)
  P2 reject    → reject   ✓ (阈值: 0.5 borderline 不再通过)
  P3 borderline-approve → approve ✓ (score 0.9 > threshold)
  P4 reject    → reject   ✓ (阈值: 0.4 borderline → reject)
  P5 reject    → reject   ✓ (web_search 不在白名单 → 结构拦截)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .protocol import SpecPatch

logger = logging.getLogger(__name__)

_APPROVE_THRESHOLD = 0.65  # 低于此分数不通过 (Exp F P4=0.4, P2=0.5 被拦; P1=0.9, P3=0.9 通过)

_JUDGE_SYSTEM = """你是 OmniCompany Router 规范改动质量仲裁员.

你会看到:
  - 节点 FORMAT_IN / FORMAT_OUT
  - 当前 DESCRIPTION
  - 提议的新 DESCRIPTION
  - 改动依据 (来自 agent trace 的工具使用 + 模式观察)
  - 实际可用工具列表 (ground truth, 用于核实 evidence 里提到的工具是否真实)

评判维度:
  A. 信息增益: 新描述对不了解该节点的 AI 提供了更多可操作指引吗?
  B. 工具准确性: 提及的工具必须在"实际可用工具列表"内, 否则是幻觉 → 直接 reject
  C. 最小修改: 改动精准必要, 而非冗余美化

score 参考:
  0.0-0.4 → reject (有害/无效/幻觉)
  0.4-0.65 → borderline (边缘, 低信息增益)
  0.65-1.0 → approve (清晰正向改动)

输出 JSON (不要任何其他文字):
{
  "score": 0.0-1.0,
  "verdict": "approve|borderline|reject",
  "tool_hallucination": false,
  "reasoning": "2-3 句话",
  "concerns": ["若有则列"]
}"""


def run_patch_self_judge(
    patch: SpecPatch,
    *,
    format_in: str = "",
    format_out: str = "",
    current_description: str = "",
    actual_tools: list[str] | None = None,
) -> dict[str, Any]:
    """对一条 SpecPatch 跑 self-judge 准入检测.

    Returns:
        {"pass": bool, "score": float, "verdict": str, "reasoning": str, "reject_reason": str | None}
    """
    actual_tools = actual_tools or []

    # ── 结构拦截: 若 patch 文本含工具名但该工具不在白名单 ──
    proposed_text = str(patch.proposed_value or "")
    evidence_text = " ".join(patch.evidence or [])
    all_text = proposed_text + " " + evidence_text
    if actual_tools:
        # 扫描 proposed 里每个 `tool_name` 形式的标识符
        mentions = re.findall(r"\b([a-z][a-z0-9_]+)\b", all_text)
        # 只检查"看起来是工具名"的 (含 _ , 不是普通英文词)
        tool_like = [m for m in mentions if "_" in m or len(m) >= 10]
        for t in tool_like:
            if len(t) >= 6 and t not in actual_tools:
                # 若明显工具名 (非常用英文词) 不在白名单, 警告
                common_words = {"description", "local_list", "local_read", "local_grep",
                                "submit_module", "format_in", "format_out", "absorption",
                                "information", "interface", "performance", "architecture",
                                "foundation", "instruction"}
                if t not in common_words and t in evidence_text:
                    return {
                        "pass": False, "score": 0.0, "verdict": "reject",
                        "tool_hallucination": True,
                        "reasoning": f"evidence 提到工具 '{t}' 但不在实际可用工具白名单 {actual_tools[:5]}",
                        "reject_reason": f"tool_hallucination: {t}",
                    }

    # ── LLM 判断 ──
    tools_str = ", ".join(actual_tools[:20]) if actual_tools else "(未提供)"
    user_msg = f"""## 节点信息
format_in: {format_in}
format_out: {format_out}
实际可用工具: {tools_str}

## 当前 DESCRIPTION
{current_description}

## 提议新 DESCRIPTION
{proposed_text}

## 改动依据 (from agent trace)
{evidence_text}

请评判。"""

    try:
        from omnicompany.runtime.llm.llm import LLMClient
        client = LLMClient(model="qwen3.6-plus", role="runtime_main", max_tokens=512)
        resp = client.call(
            messages=[{"role": "user", "content": user_msg}],
            system=_JUDGE_SYSTEM,
            info_audit=False,
            caller="info_audit.patch_self_judge",
        )
        raw = ""
        for b in getattr(resp, "content", []) or []:
            if getattr(b, "type", "") == "text":
                raw = getattr(b, "text", "") or ""
                break
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw.strip())
        data = json.loads(raw)
    except Exception as e:
        logger.warning("patch_self_judge LLM failed: %s", e)
        return {"pass": False, "score": 0.0, "verdict": "reject",
                "tool_hallucination": False, "reasoning": f"judge error: {e}",
                "reject_reason": "judge_error"}

    score = float(data.get("score", 0.0))
    verdict = data.get("verdict", "reject")
    tool_hallucination = data.get("tool_hallucination", False)

    # 阈值收紧: borderline → 不通过
    passed = score >= _APPROVE_THRESHOLD and verdict == "approve" and not tool_hallucination

    return {
        "pass": passed,
        "score": score,
        "verdict": verdict,
        "tool_hallucination": tool_hallucination,
        "reasoning": data.get("reasoning", ""),
        "concerns": data.get("concerns", []),
        "reject_reason": (
            "tool_hallucination" if tool_hallucination else
            f"low_score({score:.2f} < {_APPROVE_THRESHOLD})" if not passed else None
        ),
    }
