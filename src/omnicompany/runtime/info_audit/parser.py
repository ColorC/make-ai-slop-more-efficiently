# [OMNI] origin=claude-code domain=runtime/info_audit/parser ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:runtime.info_audit.response_parser.extractor.py"
"""从 LLM 响应文本中提取 info_audit JSON 块并解析为 InfoAuditReport。

设计要点:

  1. 鲁棒: 找不到 / 解析失败 → 返回 None (不是抛异常),
     调用方决定是否用 InfoAuditReport.parse_failed() 兜底
  2. 侵入性最小: 只负责识别形如 ```json { "info_audit": { ... } } ``` 的块,
     不做任何重写 / 校验 / 规范化
  3. 提取后可选地返回"清理过的正文"(去掉 audit 块),方便调用方把正文喂给
     原本的解析逻辑 (tool_call / JSON / 自由文本)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from omnicompany.protocol.info_audit import InfoAuditReport

logger = logging.getLogger(__name__)

# 匹配 ```json ... ``` 或 ``` ... ``` 代码块
_CODE_BLOCK_RE = re.compile(
    r"```(?:json|JSON)?\s*\n?(.*?)```",
    re.DOTALL,
)

# 匹配裸 JSON 对象,从一个以 `{` 开头的位置尝试平衡括号
_INFO_AUDIT_KEY_RE = re.compile(r'"info_audit"\s*:\s*\{', re.IGNORECASE)


def extract_info_audit_block(text: str) -> dict[str, Any] | None:
    """从文本里尝试抽出 info_audit 对象 (dict)。

    搜索顺序:
      1. 所有 ```json``` 代码块,解析出的 dict 若含 "info_audit" 键 → 取出
      2. 裸文本里搜 `"info_audit": {` 向后做括号平衡抽取
      3. 都失败 → None
    """
    if not text:
        return None

    # 1) code block 扫描
    for m in _CODE_BLOCK_RE.finditer(text):
        block = m.group(1).strip()
        try:
            obj = json.loads(block)
        except Exception:
            continue
        if isinstance(obj, dict):
            if "info_audit" in obj:
                ia = obj["info_audit"]
                if isinstance(ia, dict):
                    return ia
            # 也支持顶层就是 InfoAuditReport 的情况
            if {"sufficiency", "missing_info"}.issubset(obj.keys()):
                return obj

    # 2) 裸文本搜 "info_audit": { 做括号平衡
    m = _INFO_AUDIT_KEY_RE.search(text)
    if m:
        start = text.find("{", m.end() - 1)
        if start >= 0:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(text)):
                ch = text[i]
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            obj = json.loads(candidate)
                        except Exception:
                            return None
                        if isinstance(obj, dict):
                            return obj
                        return None
    return None


def parse_info_audit_from_text(text: str) -> InfoAuditReport | None:
    """从 LLM 响应文本解析出 InfoAuditReport。

    返回 None 表示没找到 audit 块;调用方可自行决定是否用
    InfoAuditReport.parse_failed() 兜底。
    """
    raw = extract_info_audit_block(text)
    if raw is None:
        return None
    try:
        return InfoAuditReport.model_validate(raw)
    except Exception as e:
        return InfoAuditReport.parse_failed(f"schema validation failed: {e}")


def parse_info_audit_from_tool_use(tool_use_blocks: list[Any]) -> InfoAuditReport | None:
    """从 Anthropic content blocks / 提取出的 tool_use 列表中找 info_audit 工具.

    参数可以是 Anthropic 原始 content 列表 (含 TextBlock / ToolUseBlock 混合),
    也可以是已提取的 dict 列表 ({"name":..., "input":...}).

    找不到返回 None; 找到但 payload 不合法返回 parse_failed 报告.
    """
    from omnicompany.protocol.info_audit import (
        INFO_AUDIT_TOOL_NAME,
        info_audit_tool_payload_to_report,
    )

    if not tool_use_blocks:
        return None

    for block in tool_use_blocks:
        # dict 形式 (tool_executor 已提取的)
        if isinstance(block, dict):
            if block.get("name") == INFO_AUDIT_TOOL_NAME or block.get("type") == "tool_use" and block.get("name") == INFO_AUDIT_TOOL_NAME:
                payload = block.get("input") or block.get("arguments") or {}
                return info_audit_tool_payload_to_report(payload)
            continue
        # Anthropic ToolUseBlock 对象
        btype = getattr(block, "type", "")
        bname = getattr(block, "name", "")
        if btype == "tool_use" and bname == INFO_AUDIT_TOOL_NAME:
            payload = getattr(block, "input", {}) or {}
            return info_audit_tool_payload_to_report(payload)
    return None


def strip_info_audit_block(text: str) -> str:
    """把 info_audit 代码块从文本里剔除,返回"干净正文"。

    用于调用方只想要正常答案、不想让 audit JSON 污染下游解析的场景。
    只剔除含 info_audit 的代码块;不含的保留。
    """
    if not text:
        return text

    def _repl(m: re.Match[str]) -> str:
        block = m.group(1).strip()
        try:
            obj = json.loads(block)
        except Exception:
            return m.group(0)
        if isinstance(obj, dict) and (
            "info_audit" in obj
            or {"sufficiency", "missing_info"}.issubset(obj.keys())
        ):
            return ""
        return m.group(0)

    return _CODE_BLOCK_RE.sub(_repl, text)
