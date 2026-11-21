# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.agent.message_compressor.four_layer.py"
"""agent_loop_compact — AgentNodeLoop 的四层上下文压缩

对齐 Claude Code 四层压缩链:
  L1 microcompact  — 工具结果老化（按轮数）
  L2 truncate      — 单条工具输出截断
  L3 sliding_window — 滑动窗口裁剪
  L4 auto_compact  — LLM 自动压缩（核心新增）
"""

from __future__ import annotations

import json
import logging

from omnicompany.runtime.agent.agent_loop_config import CompactConfig

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Token 估算
# ═══════════════════════════════════════════════════════════

def estimate_tokens(messages: list[dict]) -> int:
    """粗略估算消息列表的 token 数。

    混合语言场景: 1 token ≈ 1.5 chars（CC 用 1:1.3 纯英文）。
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total_chars += len(str(block.get("text", "")))
                    total_chars += len(str(block.get("content", "")))
                    # tool_use input 也算
                    inp = block.get("input")
                    if inp:
                        total_chars += len(json.dumps(inp, ensure_ascii=False)) if isinstance(inp, dict) else len(str(inp))
    return max(1, total_chars * 2 // 3)


# ═══════════════════════════════════════════════════════════
# L1: microcompact — 工具结果老化
# ═══════════════════════════════════════════════════════════

def apply_microcompact(messages: list[dict], cfg: CompactConfig) -> list[dict]:
    """L1: 老化超过 aging_threshold 轮之前的 tool_result。

    对齐 CC microcompact:
    - 从后往前数 N 轮 assistant 消息
    - N 轮之前的 tool_result 内容 → aged_message
    - 保持 tool_use_id 结构合法
    """
    if not messages or cfg.aging_threshold <= 0:
        return messages

    # 找到老化分界线
    assistant_count = 0
    boundary = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            assistant_count += 1
            if assistant_count >= cfg.aging_threshold:
                boundary = i
                break

    if boundary == 0:
        return messages

    result = []
    for i, msg in enumerate(messages):
        if i >= boundary:
            result.append(msg)
            continue

        content = msg.get("content")
        if msg.get("role") == "user" and isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    old_content = block.get("content", "")
                    if isinstance(old_content, str) and len(old_content) > 100:
                        first_line = old_content.split("\n")[0][:80]
                        new_blocks.append({
                            **block,
                            "content": f"[{first_line}... {cfg.aged_message}]",
                        })
                    else:
                        new_blocks.append(block)
                else:
                    new_blocks.append(block)
            result.append({**msg, "content": new_blocks})
        else:
            result.append(msg)

    return result


# ═══════════════════════════════════════════════════════════
# L2: truncate — 单条截断
# ═══════════════════════════════════════════════════════════

def truncate_content(content: str, max_chars: int, strategy: str = "head_tail") -> str:
    """截断过长的工具输出。

    对齐 CC 的截断策略: 保头+保尾，中间标记截断。
    """
    if len(content) <= max_chars:
        return content

    if strategy == "head":
        return content[:max_chars] + f"\n... [{len(content) - max_chars} chars truncated]"
    elif strategy == "tail":
        return f"[{len(content) - max_chars} chars truncated] ...\n" + content[-max_chars:]
    else:  # head_tail
        half = max_chars // 2
        return (
            content[:half]
            + f"\n\n... [{len(content) - max_chars} chars truncated] ...\n\n"
            + content[-half:]
        )


def apply_truncation(messages: list[dict], cfg: CompactConfig) -> list[dict]:
    """L2: 截断所有消息中过长的工具结果和文本。"""
    result = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict):
                    # tool_result 内容截断
                    if block.get("type") == "tool_result":
                        old = block.get("content", "")
                        if isinstance(old, str) and len(old) > cfg.max_tool_output:
                            new_blocks.append({
                                **block,
                                "content": truncate_content(old, cfg.max_tool_output, cfg.truncation_strategy),
                            })
                        else:
                            new_blocks.append(block)
                    else:
                        new_blocks.append(block)
                else:
                    new_blocks.append(block)
            result.append({**msg, "content": new_blocks})
        elif isinstance(content, str) and len(content) > cfg.max_tool_output * 2:
            result.append({**msg, "content": truncate_content(content, cfg.max_tool_output * 2, cfg.truncation_strategy)})
        else:
            result.append(msg)
    return result


# ═══════════════════════════════════════════════════════════
# L3: sliding_window — 滑动窗口
# ═══════════════════════════════════════════════════════════

def apply_sliding_window(messages: list[dict], cfg: CompactConfig) -> list[dict]:
    """L3: 超过 max_messages 时裁剪。

    保留第 1 条（任务描述）+ 最近 N-1 条。
    """
    if len(messages) <= cfg.max_messages:
        return messages
    return [messages[0]] + messages[-(cfg.max_messages - 1):]


# ═══════════════════════════════════════════════════════════
# L4: auto_compact — LLM 自动压缩
# ═══════════════════════════════════════════════════════════

_COMPACT_SYSTEM = """\
你的任务是总结一段 AI 助手与工具系统的对话历史。

## 输出要求（必须包含以下章节）

1. **任务目标与意图** — 原始任务是什么，当前阶段目标
2. **关键发现** — 已经发现的重要事实（保留完整的文件路径、代码片段、数值数据）
3. **已完成的操作** — 做了什么工具调用，结果如何（保留关键结果）
4. **未完成的任务** — 还有什么没做完
5. **当前工作状态** — 下一步应该做什么

## 格式

直接输出纯文本总结。不要调用任何工具。不要输出 JSON。
必须保留所有关键的文件路径、代码片段、数值、变量名。
不要遗漏任何正在进行的操作或待验证的假设。
宁可多写也不要遗漏关键信息。"""


def _serialize_messages_for_compact(messages: list[dict], max_chars: int = 80_000) -> str:
    """将消息列表序列化为可读文本，用于喂给压缩 LLM。"""
    lines: list[str] = []
    total = 0

    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")

        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(f"[tool_use: {block.get('name', '?')}({json.dumps(block.get('input', {}), ensure_ascii=False)[:200]})]")
                    elif block.get("type") == "tool_result":
                        c = block.get("content", "")
                        if isinstance(c, str) and len(c) > 500:
                            c = c[:500] + "..."
                        parts.append(f"[tool_result: {c}]")
            text = "\n".join(parts)
        else:
            text = str(content)

        line = f"[{role}] {text}"
        total += len(line)
        if total > max_chars:
            lines.append(f"[... 对话被截断，共 {len(messages)} 条消息]")
            break
        lines.append(line)

    return "\n\n".join(lines)


async def auto_compact(
    messages: list[dict],
    cfg: CompactConfig,
    llm_call,
    compact_failures: int,
) -> tuple[list[dict] | None, int]:
    """L4: 用 LLM 压缩对话历史。

    对齐 CC autoCompact:
    - 检查熔断器
    - 调用 compact_model 生成摘要
    - 用摘要替换历史消息，保留最近 N 轮
    - 失败时返回 (None, updated_failures)

    Args:
        messages: 当前消息列表
        cfg: 压缩配置
        llm_call: async callable(messages, system) -> response text
        compact_failures: 当前连续失败次数

    Returns:
        (compacted_messages | None, updated_compact_failures)
    """
    if compact_failures >= cfg.auto_compact_max_failures:
        logger.warning(
            "[compact] 熔断：连续失败 %d 次，跳过压缩", compact_failures,
        )
        return None, compact_failures

    try:
        conversation_text = _serialize_messages_for_compact(messages)

        summary = await llm_call(
            [{"role": "user", "content": f"以下是需要总结的对话历史：\n\n{conversation_text}"}],
            _COMPACT_SYSTEM,
        )

        # 构建压缩后的消息列表
        preserve_count = cfg.compact_preserve_turns * 3  # 每轮约 3 条消息
        recent = messages[-preserve_count:] if preserve_count < len(messages) else messages

        compacted = [
            {"role": "user", "content": f"[对话历史摘要]\n\n{summary}"},
            {"role": "assistant", "content": "明白，我已了解之前的工作内容。请继续。"},
        ] + recent

        logger.info(
            "[compact] 成功: %d 条消息 → %d 条 (摘要 %d chars)",
            len(messages), len(compacted), len(summary),
        )
        try:
            from omnicompany.dashboard.ccdaemon.hooks.session_companion import (
                enqueue_native_compact,
            )

            enqueue_native_compact(messages, summary)
        except Exception:
            logger.debug("[compact] companion enqueue skipped", exc_info=True)
        return compacted, 0  # 重置失败计数

    except Exception as e:
        new_failures = compact_failures + 1
        logger.error(
            "[compact] 失败 (%d/%d): %s",
            new_failures, cfg.auto_compact_max_failures, e,
        )
        return None, new_failures
