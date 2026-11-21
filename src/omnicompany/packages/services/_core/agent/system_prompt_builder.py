# [OMNI] origin=claude-code domain=services/agent ts=2026-05-04 type=helper
# [OMNI] material_id="material:core.agent.system_prompt.cacheable_sections.py"
"""SystemPromptBuilder — 把 system prompt 拆成 cacheable / volatile sections.

CC 对齐 (build-src/src/constants/systemPromptSections.ts + claude.ts cache_control):
  Anthropic API 允许 system 是 list of blocks, 每块独立标 cache_control:
    {type: 'text', text: ..., cache_control: {type:'ephemeral', ttl:'1h'}}

  cache hit 规则: 在 cache_control 标记位置, 该块之前的所有内容若跟上次匹配 → cache_read.
  策略: 把稳定不变的 (基础指令 / tool 描述 / skill 列表) 标 1h ttl, 变化频繁的
  (turn counter / 当前时间 / user 偏好) 标 5m ttl 或不标.

用法:
  builder = SystemPromptBuilder()
  builder.add_section("base instructions", cacheable=True, ttl="1h")
  builder.add_section("skill listing", cacheable=True, ttl="1h")
  builder.add_section(f"current turn: {turn}", cacheable=False)  # volatile
  system = builder.build()  # → list of blocks ready for LLMClient
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class _Section:
    text: str
    cacheable: bool = True
    ttl: Literal["5m", "1h"] = "1h"


@dataclass
class SystemPromptBuilder:
    """组合 system prompt sections 标 cache_control.

    设计原则:
      - cacheable=True 块标 ephemeral cache_control (按 ttl)
      - cacheable=False 块不标 (但仍占位置, cache 在它之前的 cacheable 块还有效)
      - 多个连续 cacheable 块可省略中间 cache_control, 只在最后一个标 (Anthropic 规则:
        cache_control 标 LAST 块, 之前所有内容默认进缓存窗口)
      - 但为了细粒度控制 (1h vs 5m), 我们在每个 cacheable 块都标 (Anthropic 接受多 cache_control)
    """

    sections: list[_Section] = field(default_factory=list)

    def add_section(
        self,
        text: str,
        *,
        cacheable: bool = True,
        ttl: Literal["5m", "1h"] = "1h",
    ) -> "SystemPromptBuilder":
        """加一个 section. 返 self 链式调用."""
        if not text:
            return self
        self.sections.append(_Section(text=text, cacheable=cacheable, ttl=ttl))
        return self

    def build(self) -> list[dict]:
        """构造 Anthropic system blocks 列表. 仅 cacheable=True 块标 cache_control.

        小优化: 末尾连续 cacheable 块且 ttl 相同 → 只标最后一个 (Anthropic 规则: cache_control
        在 LAST 块即可, 之前所有内容默认进 cache 窗口).
        """
        if not self.sections:
            return []
        blocks: list[dict] = []
        for s in self.sections:
            blk: dict = {"type": "text", "text": s.text}
            if s.cacheable:
                blk["cache_control"] = {"type": "ephemeral", "ttl": s.ttl}
            blocks.append(blk)
        # 优化: 末尾连续相同 ttl 的 cacheable 块, 只保留最后一个 cache_control.
        # 例: [c1h, c1h, c1h, vol] → 第 1/2 个 cache_control 可省 (cache 命中规则一致).
        # 但保留也无害, Anthropic 允许多 cache_control 块. 简化, 先不做合并.
        return blocks

    def build_text(self) -> str:
        """合并成单一 string (cache 不生效, 但兼容旧 API)."""
        return "\n\n".join(s.text for s in self.sections)


__all__ = ["SystemPromptBuilder"]
