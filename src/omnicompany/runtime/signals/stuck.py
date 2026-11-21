# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.signals.loop_detector.algorithm.py"
"""StuckDetector — 检测 Agent 陷入循环

检测两类模式:
1. 概念/思维原地踏步: LLM 持续生成相同的推理意图，但没有新信息（主要检测目标）
2. 硬循环保底: 连续 N 次完全相同的工具调用 + 相同结果（宽松阈值，作为保底）
3. 独白循环: 连续 N 次相同纯文本响应（无 tool_call）

不检测:
- 工具调用失败后的重试（这是正常的错误恢复，应产生 pain 信号而非被硬截断）
- 同一工具在多步中高频出现（合法的单工具任务，如连续文件操作）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class StuckAnalysis:
    """stuck 分析结果"""
    loop_type: str
    repeat_times: int


# 意图指纹提取的停用词（中英混合）
_STOPWORDS = frozenset({
    # English
    "the", "is", "are", "was", "will", "to", "of", "in", "for", "and", "or",
    "a", "an", "i", "me", "my", "we", "our", "you", "it", "its", "be", "do",
    "have", "has", "this", "that", "with", "from", "not", "but", "so", "if",
    "as", "by", "on", "at", "need", "try", "use", "can", "let", "now", "then",
    "just", "also", "here", "there", "up", "out", "get", "make", "go",
    # Chinese common
    "的", "了", "是", "在", "我", "他", "她", "它", "们", "和", "与", "或",
    "不", "也", "都", "而", "但", "这", "那", "就", "把", "被", "已", "有",
    "将", "要", "可", "会", "来", "去", "到", "从", "对", "为", "以", "及",
})


class StuckDetector:
    """检测 Agent 循环

    维护最近 N 步的 action-observation 历史,
    检测是否陷入重复模式。

    两个阈值:
    - conceptual_threshold: 概念循环触发门槛（默认 4）——更宽松但检测更重要的模式
    - tool_repeat_threshold: 工具硬循环保底门槛（默认 6）——允许工具错误重试
    """

    def __init__(
        self,
        max_history: int = 16,
        repeat_threshold: int = 6,
        conceptual_threshold: int = 4,
    ):
        self.max_history = max_history
        self.repeat_threshold = repeat_threshold          # 工具硬循环
        self.conceptual_threshold = conceptual_threshold  # 思维原地踏步
        self._history: list[dict[str, Any]] = []
        self.stuck_analysis: StuckAnalysis | None = None

    def record(self, step_data: dict[str, Any]) -> None:
        """记录一步的 action-observation 数据

        step_data 应包含:
            tool_calls: list[dict] — 本轮工具调用 (或 None 表示纯文本)
            tool_results: list[dict] — 本轮工具结果 (或 None)
            text_output: str | None — 纯文本输出（LLM 推理文本）
        """
        self._history.append(step_data)
        if len(self._history) > self.max_history:
            self._history.pop(0)

    def is_stuck(self) -> bool:
        """检测是否陷入循环"""
        self.stuck_analysis = None

        if len(self._history) < min(self.conceptual_threshold, self.repeat_threshold):
            return False

        # 首先检测思维原地踏步（更重要、更宽松的阈值）
        if self._check_conceptual_loop():
            return True

        # 然后检测独白循环（纯文本）
        if self._check_monologue():
            return True

        # 最后作为保底：工具硬循环（宽松阈值，允许错误重试）
        if self._check_repeating_action_observation():
            return True

        return False

    # ── 归一化工具 ───────────────────────────────────────────────

    def _normalize_step(self, step: dict[str, Any]) -> str:
        """将一步归一化为可比较的字符串"""
        tool_calls = step.get("tool_calls")
        if tool_calls:
            parts = []
            for tc in tool_calls:
                name = tc.get("tool_name", "")
                args = tc.get("tool_args", {})
                parts.append(f"{name}:{_hash_args(args)}")
            return "|".join(parts)
        else:
            text = step.get("text_output") or ""
            return f"text:{text[:200]}"

    def _normalize_result(self, step: dict[str, Any]) -> str:
        """将工具结果归一化，兼容 content 为 str 或 list（多模态图片块）"""
        results = step.get("tool_results")
        if results:
            parts = []
            for r in results:
                content = r.get("content", "")
                if isinstance(content, list):
                    text_parts = [
                        c.get("text", "")[:200]
                        for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    ]
                    parts.append("|".join(text_parts))
                else:
                    s = str(content)
                    mid = len(s) // 2
                    parts.append(s[:150] + s[mid:mid+100] + s[-100:])
            return "|".join(parts)
        return ""

    def _extract_intent_fingerprint(self, text: str) -> frozenset[str]:
        """提取 LLM 推理文本中的关键意图词集合（去停用词）

        用于判断两段推理是否在表达相同的意图。
        """
        words = set(re.findall(r"[a-z\u4e00-\u9fff]{2,}", text.lower()))
        return frozenset(words - _STOPWORDS)

    # ── 检测逻辑 ─────────────────────────────────────────────────

    def _check_conceptual_loop(self) -> bool:
        """检测思维/概念原地踏步

        当 LLM 连续生成高度相似的推理文本（Jaccard ≥ 0.65），
        但没有注入新信息时，认为进入概念死循环。

        与工具重试的区别：
        - 工具重试：工具调用失败 → 换参数或换方法 → text_output 会有变化
        - 概念死循环：LLM 一直在思考「需要做 X」但每次都得出相同结论，毫无进展
        """
        n = self.conceptual_threshold
        # 只看有实质文本输出的步骤（LLM 推理步骤，排除空文本和极短的过渡）
        text_steps = [
            s for s in self._history
            if s.get("text_output") and len(s["text_output"]) > 80
        ]
        if len(text_steps) < n:
            return False

        last_n = text_steps[-n:]
        fingerprints = [self._extract_intent_fingerprint(s["text_output"]) for s in last_n]

        # 计算所有相邻步骤的 Jaccard 相似度
        similarities = []
        for i in range(1, len(fingerprints)):
            a, b = fingerprints[i - 1], fingerprints[i]
            if not a or not b:
                similarities.append(0.0)
                continue
            jaccard = len(a & b) / len(a | b)
            similarities.append(jaccard)

        if not similarities:
            return False

        # 所有相邻步骤 Jaccard ≥ 0.65 → 概念原地踏步
        if all(s >= 0.65 for s in similarities):
            self.stuck_analysis = StuckAnalysis(
                loop_type="conceptual_loop",
                repeat_times=len(last_n),
            )
            return True
        return False

    def _check_monologue(self) -> bool:
        """检测连续 N 次相同纯文本响应（无 tool_call，完全一致）"""
        n = self.repeat_threshold  # 独白用和工具硬循环相同的高阈值
        recent_text_steps = [
            s for s in self._history[-n * 2:]
            if not s.get("tool_calls")
        ]

        if len(recent_text_steps) < n:
            return False

        last_n = recent_text_steps[-n:]
        fingerprints = [self._normalize_step(s) for s in last_n]
        if len(set(fingerprints)) == 1:
            self.stuck_analysis = StuckAnalysis(
                loop_type="monologue",
                repeat_times=n,
            )
            return True
        return False

    def _check_repeating_action_observation(self) -> bool:
        """保底检测：连续 N 次完全相同的工具调用 + 完全相同的结果

        注意：此检测使用宽松阈值（默认 6），允许工具错误后多次重试。
        不同的错误消息会导致 results 不同，因此正常错误恢复不会触发此检测。
        """
        n = self.repeat_threshold
        if len(self._history) < n:
            return False

        recent = self._history[-n:]
        actions = [self._normalize_step(s) for s in recent]
        results = [self._normalize_result(s) for s in recent]

        if len(set(actions)) == 1 and len(set(results)) == 1:
            self.stuck_analysis = StuckAnalysis(
                loop_type="repeating_action_observation",
                repeat_times=n,
            )
            return True
        return False


def _hash_args(args: dict[str, Any]) -> str:
    """简单的参数哈希，用于比较"""
    parts = []
    for k, v in sorted(args.items()):
        v_str = str(v)[:100]
        parts.append(f"{k}={v_str}")
    return ",".join(parts)
