"""本轮复核: 一轮结束后, 由维护模型(不是会话本身)事后总结两件事。

1. 判断卡: 这一轮里 agent 自己替人做了哪些主观取舍(文案口吻、视觉、交互、节奏手感……),
   选了什么、还有哪些可选。会话照常按默认做完, 不停下来问; 人事后看一眼, 想换就点一下。
2. 准则提醒: 对照 config/quality_criteria/ 里放之四海皆准的准则, 本轮产出有哪些明显违反。

2026-11-20 业主:「判断卡是可以做的, 但是也是——应该后总结, 不要引起会话本身的提问欲望」;
「重点并非在于我的个人口味, 有很多准则就是优秀与否的差异」。

结果按 (会话, 轮次) 落盘缓存, 同一轮只调一次模型。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from .turn_results import last_turn, resolve_session_transcript

MAX_JUDGMENTS = 4
MAX_FLAGS = 4
_LOCK = threading.Lock()
_INFLIGHT: dict[str, threading.Event] = {}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "chose": {"type": "string"},
                    "alternatives": {"type": "array", "items": {"type": "string"}},
                    "where": {"type": "string"},
                },
                "required": ["topic", "chose", "alternatives"],
            },
        },
        "criteria_flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "evidence": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["criterion", "evidence"],
            },
        },
    },
    "required": ["judgments", "criteria_flags"],
}

_SYSTEM = """你是旁观复核员，不参与会话，只在一轮工作结束后读它的记录写复核。

输出两类条目，都用陈述句写「已经做了什么」，绝不向任何人提问：

judgments（判断卡）：这一轮里 agent 自己拍板的主观取舍——文案口吻和措辞、视觉与配色、版式与交互方式、
游戏手感与数值倾向、命名等「没有对错、看人感受」的选择。每条写：
- topic：取舍的是什么（10 字内）
- chose：实际选了哪个（具体到内容，30 字内）
- alternatives：1–3 个同样合理的替代方案（具体，每个 30 字内，能直接拿去改）
- where：在哪能看到（画布卡标题、文件名或界面位置；没有就留空）
客观事实、技术实现细节、按明确要求做的事都不算判断卡。没有就给空数组。最多 4 条，挑影响观感最大的。

criteria_flags（准则提醒）：对照给出的准则清单，本轮产出里证据确凿的违反。每条写：
- criterion：准则 id
- evidence：记录里的原文证据（引用，60 字内）
- fix：一句话怎么改
只报记录里有直接证据的；拿不准就不报。没有就给空数组。最多 4 条。

只输出 JSON。"""


def _criteria_root() -> Path:
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root() / "config" / "quality_criteria"


def load_criteria(root: Path | None = None) -> list[dict[str, str]]:
    """准则库: 每个 yaml 一个领域, criteria[] 每条至少有 id 与 rule。"""
    out: list[dict[str, str]] = []
    base = root or _criteria_root()
    for path in sorted(base.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        domain = str(data.get("domain") or path.stem)
        for item in data.get("criteria") or []:
            if isinstance(item, dict) and item.get("id") and item.get("rule"):
                out.append({"id": f"{domain}/{item['id']}", "rule": str(item["rule"]).strip()})
    return out


def _cache_dir() -> Path:
    from omnicompany.core.config import omni_workspace_root

    path = omni_workspace_root() / "data" / "cc_turn_reviews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clip(value: Any, size: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= size else text[: size - 1] + "…"


def clean_review(raw: Any, criteria_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    data = raw if isinstance(raw, dict) else {}
    judgments = []
    for item in data.get("judgments") or []:
        if not isinstance(item, dict) or not item.get("topic") or not item.get("chose"):
            continue
        alternatives = [_clip(a, 60) for a in item.get("alternatives") or [] if str(a or "").strip()][:3]
        if not alternatives:
            continue
        judgments.append({
            "topic": _clip(item["topic"], 24),
            "chose": _clip(item["chose"], 80),
            "alternatives": alternatives,
            "where": _clip(item.get("where"), 80),
        })
    flags = []
    for item in data.get("criteria_flags") or []:
        if not isinstance(item, dict) or not item.get("evidence"):
            continue
        criterion = str(item.get("criterion") or "").strip()
        if criteria_ids and criterion not in criteria_ids:
            # 模型常省掉领域前缀: 按尾段补全, 补不上就丢。
            matches = [cid for cid in criteria_ids if cid.endswith("/" + criterion)]
            if len(matches) != 1:
                continue
            criterion = matches[0]
        flags.append({
            "criterion": criterion,
            "evidence": _clip(item["evidence"], 120),
            "fix": _clip(item.get("fix"), 80),
        })
    return {"judgments": judgments[:MAX_JUDGMENTS], "criteria_flags": flags[:MAX_FLAGS]}


def _review_input(prompt: str, assistant: list[str], criteria: list[dict[str, str]]) -> str:
    body = "\n\n".join(text for text in assistant if text.strip())
    if len(body) > 9000:
        body = "…" + body[-9000:]
    return (
        f"人这一轮的要求：\n{_clip(prompt, 1500) or '(无)'}\n\n"
        f"agent 这一轮的全部回复（按时间顺序）：\n{body or '(无)'}\n\n"
        "准则清单：\n" + "\n".join(f"- {c['id']}：{c['rule']}" for c in criteria)
    )


def session_turn_review(sid: str, *, call_model: Any = None) -> dict[str, Any]:
    _provider, path, unavailable = resolve_session_transcript(sid)
    base: dict[str, Any] = {"session_id": sid, "turn_key": "", "judgments": [], "criteria_flags": []}
    if path is None:
        return {**base, "unavailable": unavailable}
    turn = last_turn(path)
    if not turn.turn_key:
        return {**base, "unavailable": "no-turn"}
    cache_file = _cache_dir() / f"{sid}.json"
    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    if cached.get("turn_key") == turn.turn_key:
        return cached

    # 同一轮同时来几个请求(多个页面开着)只调一次模型, 其余等它的结果。
    key = f"{sid}:{turn.turn_key}"
    with _LOCK:
        event = _INFLIGHT.get(key)
        owner = event is None
        if owner:
            event = threading.Event()
            _INFLIGHT[key] = event
    if not owner:
        event.wait(timeout=120)
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {**base, "turn_key": turn.turn_key, "unavailable": "pending"}
    try:
        criteria = load_criteria()
        if not any(text.strip() for text in turn.assistant):
            review = {"judgments": [], "criteria_flags": []}
        else:
            if call_model is None:
                from omnicompany.runtime.llm import call_json as call_model
            raw = call_model(
                system=_SYSTEM,
                user=_review_input(turn.prompt, turn.assistant, criteria),
                schema=REVIEW_SCHEMA,
                caller="ccdaemon.turn_review",
                max_tokens=2000,
            )
            review = clean_review(raw, {c["id"] for c in criteria})
        result = {**base, "turn_key": turn.turn_key, **review, "reviewed_at": time.time()}
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return result
    except Exception as error:  # 复核是附加信息: 模型挂了不影响结果页其余部分
        return {**base, "turn_key": turn.turn_key, "unavailable": f"review-failed: {str(error)[:120]}"}
    finally:
        with _LOCK:
            _INFLIGHT.pop(key, None)
        event.set()
