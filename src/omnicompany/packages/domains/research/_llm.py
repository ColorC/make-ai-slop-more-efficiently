# [OMNI] origin=ai-ide domain=research ts=2026-06-14T00:00:00Z type=helper status=active
# [OMNI] summary="research domain 的 LLM 薄封装:safe_json(失败返 default 不炸管线)。统一走 runtime/llm/structured.call_json。"
# [OMNI] why="多处要'调便宜/中端模型 + schema 约束 + 失败降级'。集中一处,model=None 走默认便宜档,传 MID_MODEL 走中端。"
# [OMNI] tags=research,llm,helper
"""research domain LLM 薄封装。"""

from __future__ import annotations

import json as _json
from typing import Any, Mapping


def safe_json(
    system: str,
    user: Any,
    schema: Mapping[str, Any] | None = None,
    *,
    model: str | None = None,
    caller: str = "research",
    max_tokens: int = 2000,
    default: Any = None,
) -> Any:
    """调 call_json,失败(模型不可用/限流/不合 schema)返 default,绝不炸管线。"""
    from omnicompany.runtime.llm.structured import call_json

    if not isinstance(user, str):
        user = _json.dumps(user, ensure_ascii=False)
    try:
        return call_json(system=system, user=user, schema=schema, model=model,
                         caller=caller, max_tokens=max_tokens)
    except Exception:
        return default
