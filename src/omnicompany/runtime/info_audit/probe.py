# [OMNI] origin=claude-code domain=runtime/info_audit/probe ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:runtime.info_audit.isolated_probe_engine.implementation.py"
"""独立 isolated LLM 信息审计探针 (Phase 2.4 + P2.5.5)。

D1 = (c) STRICT 模式的核心: 主 LLM 答完后, 另起一个**没有对话历史污染**的
新 LLMClient 实例, 只看原 prompt + 主 LLM 输出, 独立判断:
  "这个节点的信息够不够 / 缺什么 / 是否建议兜底"

这比 PIGGYBACK (主 LLM 自评) 可靠得多, 因为孤立 LLM 不会因为"我刚刚自信地答了"
就虚报 sufficiency=sufficient。

用户洞察 (计划文档 §2): 让孤立的 LLM 各自发现, 很准。
"""

from __future__ import annotations

from typing import Any

from omnicompany.protocol.info_audit import InfoAuditReport, Sufficiency


_SYSTEM_PROMPT = """\
你是一位严格的信息完整度审计员。

你会收到:
  1. 一个 LAP Router 节点的描述 (format_in / format_out / description)
  2. 可选: 该节点本次运行的真实 prompt 片段
  3. 可选: 该节点本次运行的真实输出片段

你的任务: 独立判断这个节点要把 format_in 转换到 format_out, **输入信息是否足够**。
注意: 你不是评判"答案对不对", 你是评判"有没有关键信息缺失"。

严格返回如下格式的 JSON, 不要任何其他文本:

```json
{
  "info_audit": {
    "sufficiency": "sufficient|partial|insufficient|unknown",
    "needs_more_info": true|false,
    "missing_info": [
      {"description": "具体缺什么", "critical": true, "suggested_source": "去哪找"}
    ],
    "missing_critical": ["关键缺失的摘要"],
    "fallback_recommended": true|false,
    "confidence_self": 0.0-1.0,
    "attention_focus": "...",
    "concerns": ["..."]
  }
}
```

审计原则:
- 你没看到过 prompt 的具体内容 → unknown + confidence 偏低
- 你看到 prompt 里确实缺关键字段 → insufficient + 具体描述缺什么
- 你看到 prompt 齐全 → sufficient
- fallback_recommended 只在"缺的信息关键到产出必然不可靠"时才 true
- 不要虚报谦虚; 不要为了显得严谨而编造缺失项
"""


def run_info_audit_probe_strict(
    *,
    format_in: str,
    format_out: str,
    description: str,
    original_system: str = "",
    original_user_preview: str = "",
    original_response_preview: str = "",
    model: str | None = None,
) -> InfoAuditReport:
    """用独立 isolated LLM 做一次严格信息审计。

    永不抛异常: 任何失败 → 返回 InfoAuditReport.parse_failed(...)。
    """
    try:
        # 延迟 import, 避免循环
        from omnicompany.runtime.llm.llm import LLMClient
        from omnicompany.runtime.info_audit.parser import parse_info_audit_from_text
    except Exception as e:
        return InfoAuditReport.parse_failed(f"probe import failed: {e}")

    user_content = _build_user_prompt(
        format_in=format_in,
        format_out=format_out,
        description=description,
        original_system=original_system,
        original_user_preview=original_user_preview,
        original_response_preview=original_response_preview,
    )

    try:
        # **新实例, 不共享对话历史 = "isolated"**
        kwargs: dict[str, Any] = {"role": "runtime_main", "max_tokens": 2048}
        if model:
            kwargs["model"] = model
        client = LLMClient(**kwargs)
        result = client.call(
            messages=[{"role": "user", "content": user_content}],
            system=_SYSTEM_PROMPT,
            caller="info_audit.probe",
        )
    except Exception as e:
        return InfoAuditReport.parse_failed(f"probe LLM call failed: {e}")

    text = _extract_text(result)
    report = parse_info_audit_from_text(text)
    if report is None:
        return InfoAuditReport.parse_failed("probe returned no info_audit block")
    return report


def _build_user_prompt(
    *,
    format_in: str,
    format_out: str,
    description: str,
    original_system: str,
    original_user_preview: str,
    original_response_preview: str,
) -> str:
    parts = [
        "请审计下面这个节点的信息完整度。",
        "",
        f"## 节点 format_in\n{format_in}",
        f"## 节点 format_out\n{format_out}",
        f"## 节点描述\n{description}",
    ]
    if original_system:
        parts.append(f"## 节点原 system prompt 预览 (可能截断)\n```\n{original_system[:3000]}\n```")
    if original_user_preview:
        parts.append(f"## 节点原 user prompt 预览 (可能截断)\n```\n{original_user_preview[:3000]}\n```")
    if original_response_preview:
        parts.append(f"## 节点原响应预览 (可能截断)\n```\n{original_response_preview[:3000]}\n```")
    parts.append("\n现在请按 system 指令返回 info_audit JSON。")
    return "\n\n".join(parts)


def _extract_text(result: Any) -> str:
    """从 Anthropic / OpenAI 响应对象里抽纯文本。"""
    try:
        # Anthropic 形态
        content = getattr(result, "content", None)
        if isinstance(content, list):
            texts: list[str] = []
            for b in content:
                t = getattr(b, "text", None)
                if isinstance(t, str):
                    texts.append(t)
            if texts:
                return "\n".join(texts)
        # OpenAI 形态
        choices = getattr(result, "choices", None)
        if choices:
            msg = getattr(choices[0], "message", None)
            if msg:
                c = getattr(msg, "content", "")
                if isinstance(c, str):
                    return c
        # Dict fallback
        if isinstance(result, dict):
            return str(result.get("content") or result.get("text") or result)
    except Exception:
        pass
    return str(result)


# 兼容快捷:  UNKNOWN sufficiency 判定 (runner 可能会用到)
def is_probe_usable(report: InfoAuditReport | None) -> bool:
    return report is not None and report.sufficiency != Sufficiency.UNKNOWN
