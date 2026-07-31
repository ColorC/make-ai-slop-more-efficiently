# [OMNI] origin=claude-code type=infra summary="会话归属统一设施(唯一入口): session → omni项目/计划 的五级归属链(绑定>临时预判>cwd匹配>digest词表>LLM标识)+常驻增量标注; 任何消费者(ccusage统计/看板/未来渠道)一律从这里取, 禁止各渠道自建分类" why="用户2026-07-17指令: 对话自动分类和识别做成统一任务, 不要各渠道各自为战反复分类" tags=session-attribution,unified,boss-sight
"""session_attribution —— 会话归属统一设施(唯一入口)。

任何"这个会话属于哪个项目/计划"的需求一律 import 本模块, 禁止另建分类:
    from omnicompany.dashboard.boss_sight.session_attribution import make_resolver, ensure_labeler
    resolve = make_resolver()          # resolve(sid, agent) -> (项目, 计划)
    ensure_labeler()                   # 常驻增量标注线程(幂等, 已在则不重复起)

归属链(优先级从高到低, 全部复用既有设施):
  1. 会话绑定(omni session bind; data/cc_session_bindings.json + cc_sessions.json) —— 用户显式声明, 最权威
  2. 临时/测试预判(cwd 在 temp/scratchpad) —— 确定性
  3. cwd → omni 项目 roots 匹配(core.projects_registry) —— 确定性
  4. agent_digests(现成会话摘要设施)的项目原文 → 词表映射对齐 omni 项目 id(词表级一次映射, 非逐会话)
  5. LLM 深读标注(qwen; 读前8条用户消息+digest线索; 结果落盘缓存) —— 兜底
  判不出 → "未归属"。

落盘(data/boss_sight/ccusage_cache/):
  session_project_labels.json  第5级标注缓存(增量, __v 版本迁移)
  project_vocab_map.json       第4级词表映射
  labeler_status.json          标注线程心跳(state/labeled/pending)

实现体暂在 ccusage_stats.py(历史原因), 本模块是唯一对外入口; 后续物理搬家不影响调用方。
"""

from __future__ import annotations

from typing import Any

from .ccusage_stats import (  # noqa: F401  —— 统一入口 re-export
    _ensure_labeler as ensure_labeler,
    _make_resolver as make_resolver,
)


def attribution_summary() -> dict[str, Any]:
    """归属设施状态总览(API/巡检用): 标注线程心跳 + 各级缓存规模。"""
    import json

    from .ccusage_stats import _cache_dir, _load_labels, _load_vocab

    d = _cache_dir()
    status: dict[str, Any] = {}
    try:
        p = (d / "labeler_status.json") if d else None
        if p and p.is_file():
            status = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    labels = _load_labels()
    labels.pop("__v", None)
    labeled = [v for v in labels.values() if v]
    vocab = _load_vocab()
    return {
        "chain": ["绑定", "临时预判", "cwd匹配", "digest词表", "LLM标注", "未归属"],
        "labeler": status,
        "llm_labels": {"total": len(labels), "resolved": len(labeled), "null": len(labels) - len(labeled)},
        "vocab": {"words": len(vocab), "mapped": sum(1 for v in vocab.values() if v)},
        "note": "唯一入口=boss_sight.session_attribution; 各渠道禁止自建会话分类。",
    }


__all__ = ["make_resolver", "ensure_labeler", "attribution_summary"]
