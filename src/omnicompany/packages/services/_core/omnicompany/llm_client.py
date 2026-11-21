# [OMNI] origin=claude-code domain=services/omnicompany ts=2026-04-23T00:00:00Z type=helper
# [OMNI] material_id="material:omnicompany.llm_json_caller.shared_helper.py"
"""omnicompany 共享 LLM 调用 helper · 走 WebBus audit · 跨 service 复用.

原在 team_builder/workers/_llm_client.py, 2026-04-23 下午提到 omnicompany/
作共享层, team_builder / config_service 等 service 共用本 helper.

设计:
- 使用 `runtime.llm.LLMClient` (qwen3.6-plus 默认 · role=runtime_main)
- 通过 WebBus `audit_request` / `audit_response` 记录 (transport 仍是 LLMClient)
- 不走 WebBus.precheck_url (LLM endpoint 不是 http:// scheme)
- 铁律 A: 不截断; 铁律 B: max_tokens 宽松

输出格式: 期望 LLM 返回 JSON, 解析失败时 fallback 为 {"_raw": text, "_parse_error": str}.
"""
from __future__ import annotations

import json
import re

# 自动加载项目根 .env (含 THE_COMPANY_API_KEY). 让 pytest / 独立脚本 / 外部调用者
# 不必先 load_dotenv — omnicompany 的 CLI 走 cli/main.py 自己加载, 但本 helper 可能被
# 任何入口 import, 自己 load 最稳. dotenv 已是 omnicompany 的必要依赖.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:  # pragma: no cover — dotenv 是必选依赖, ImportError 表示装环境坏
    pass
from typing import Any

from omnicompany.runtime.buses import WebBus


_JSON_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def call_llm_json(
    system: str,
    user: str,
    *,
    web_bus: WebBus | None = None,
    caller: str,
    role: str = "runtime_main",
    model: str | None = None,
    max_tokens: int = 16000,
) -> dict:
    """调 LLM · 期望 JSON 输出 · 走 WebBus audit 回流.

    Args:
      system: system prompt
      user: user prompt
      web_bus: WebBus 实例. None 则不审计 (本地测试).
      caller: 调用方标识 (例 "team_builder.intent_analyzer")
      role: LLM 角色 (默认 runtime_main → qwen3.6-plus)
      max_tokens: LLM 输出上限 (宽松, 铁律 B)

    Returns:
      解析后的 JSON dict. 解析失败返 {"_raw": text, "_parse_error": str}.

    Raises:
      RuntimeError: LLM 调用失败 (api_key 缺失 / 网络错误等)
    """
    from omnicompany.runtime.llm.llm import LLMClient, _extract_response_text

    # model 非空时显式覆盖 role 查表 (bench 场景 · 或特定 Worker 要指定模型)
    if model:
        client = LLMClient(model=model, max_tokens=max_tokens)
    else:
        client = LLMClient(role=role, max_tokens=max_tokens)
    url_placeholder = f"llm://{client.model}/chat"
    payload_size = len(system) + len(user)

    corr_id = None
    if web_bus is not None:
        corr_id = web_bus.audit_request(
            url_placeholder,
            "POST",
            payload_size=payload_size,
            note=f"caller={caller} role={role}",
        )

    try:
        response = client.call(
            messages=[{"role": "user", "content": user}],
            system=system,
            caller=caller,
        )
        text = _extract_response_text(response) or ""
    except Exception as e:
        if web_bus is not None and corr_id:
            web_bus.audit_response(
                corr_id, status=-1, body_size=0, note=f"exception: {type(e).__name__}: {e}"
            )
        raise

    if web_bus is not None and corr_id:
        web_bus.audit_response(
            corr_id,
            status=200,
            body_size=len(text),
            note=f"caller={caller} tokens≈{len(text)//4}",
        )

    # 解析 JSON · 先试 ```json 围栏, 再试裸 JSON
    parsed = _parse_json_loose(text)
    if parsed is None:
        return {"_raw": text, "_parse_error": "no JSON found in response"}
    if not isinstance(parsed, dict):
        return {"_raw": text, "_parse_error": f"top-level not dict, got {type(parsed).__name__}"}
    return parsed


_INVALID_ESCAPE_RE = re.compile(r'\\([^"\\/bfnrtu])')


def _sanitize_json_escapes(s: str) -> str:
    """骨架接管 · 剥离 LLM 常见非法 escape.

    JSON 合法 escape: \\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX
    LLM 经常错写 \\| \\` \\e 等 (中文 LLM 尤其容易在描述里举例时踩)
    非法 escape → 保留目标字符, 去掉反斜杠 (例: \\| → |)

    铁律 feedback_100pct_required_goes_to_skeleton: 100% 必做 = 骨架, 不靠 LLM 自觉产合法 JSON.
    """
    return _INVALID_ESCAPE_RE.sub(r'\1', s)


def _try_loads(s: str) -> Any | None:
    """先裸试, 失败后 sanitize escape 再试."""
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_sanitize_json_escapes(s))
    except json.JSONDecodeError:
        return None


def _parse_json_loose(text: str) -> Any | None:
    """宽容 JSON 解析: 优先 ```json 块, 回退裸文本, 再回退首个 {...}.

    每层解析失败时自动 sanitize 非法 escape 重试 (骨架容错 LLM JSON 产出).
    """
    if not text:
        return None
    # 1. ``` json ``` 围栏
    m = _JSON_FENCE.search(text)
    if m:
        v = _try_loads(m.group(1))
        if v is not None:
            return v
    # 2. 裸文本直接解
    v = _try_loads(text)
    if v is not None:
        return v
    # 3. 找首个 {...} 到对应 }
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None
    return _try_loads(text[start : end + 1])
