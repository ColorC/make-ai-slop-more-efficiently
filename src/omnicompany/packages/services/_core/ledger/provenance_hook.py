# [OMNI] origin=claude-code domain=services/_core/ledger ts=2026-07-04T00:00:00Z type=module status=active agent=claude-code
# [OMNI] summary="通用运行留痕钩子:任何设计域工具真跑一次,确定性检索适用历史裁决并把 consumed_decisions 记进统一账本 events.jsonl;留痕失败绝不阻断工具主流程" why="统一设计工作室计划(UNIFIED-DESIGN-STUDIO §3.5):从配表留痕钩子 run_ledger 剥出通用件,lint 门禁/前端管线各挂薄钩子,不另开账本"
# [OMNI] tags=ledger,provenance,decisions,design-studio
"""通用运行留痕钩子 —— 设计域工具运行的统一消费面。

从 config_service/run_ledger.py 剥出的通用件(配表专属部分 xlsm 指纹留在原处)。
权威锚: docs/plans/[2026-07-04]UNIFIED-DESIGN-STUDIO/plan.md §3.5。

三条铁律(与配表钩子同款):
1. **留痕失败 ≠ 工具失败**。record_tool_run 整体 try/except 兜底, 任意异常一律吞掉、
   返回 None、留 ERROR 日志; 绝不把异常抛回门禁/管线主流程。门禁的裁决性由工具本体
   保证, 留痕只是消费面。
2. **决策检索只走确定性路径**(allow_semantic=False)。零命中就是空列表, 禁伪造。
3. **只写 data/ledger/events.jsonl**(ledgers.yaml ledger-ops 唯一登记位置), 不另开账本。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)


# ── 统一引用写法(docs/standards/_global/unified-reference.md)──────────────────

def decision_ref(decision_id: str) -> str:
    return f"[[decision:{decision_id}]]"


def file_ref(path: str) -> str:
    return f"[[file:{path}]]"


# ── 默认注入实现(懒导入, import 本模块零硬依赖)─────────────────────────────────

def default_decision_finder(query: str, *, allow_semantic: bool = True) -> list[dict]:
    """默认决策检索器: 接 decisions.catalog.find(钩子路径强制 allow_semantic=False)。"""
    from omnicompany.packages.domains.decisions import catalog

    return catalog.find(query, allow_semantic=allow_semantic)


def default_ledger_append(event, *, idempotency_key=None):
    """默认账本写入器: 接 services._core.ledger.append。"""
    from omnicompany.packages.services._core.ledger import append as _append

    return _append(event, idempotency_key=idempotency_key)


# ── 通用件 ─────────────────────────────────────────────────────────────────────

def collect_decisions(queries: Sequence[str], finder: Callable[..., list[dict]]) -> list[dict]:
    """确定性决策检索: 各查询词各查一遍, 按 id 去重合并。零命中即空, 禁伪造。

    任一查询异常向上冒泡(由 record_tool_run 外层 try 兜住转 None)。
    """
    seen: set[str] = set()
    merged: list[dict] = []
    for q in queries:
        if not q:
            continue
        for rec in finder(q, allow_semantic=False):
            rid = rec.get("id") if isinstance(rec, dict) else None
            if not rid or rid in seen:
                continue
            seen.add(rid)
            merged.append(rec)
    return merged


def record_tool_run(
    tool_id: str,
    event_type: str,
    activity: str,
    queries: Sequence[str],
    *,
    inputs: Sequence[str] | None = None,
    outputs: Sequence[str] | None = None,
    meta: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    decision_finder: Callable[..., list[dict]] | None = None,
    ledger_append: Callable[..., str] | None = None,
) -> str | None:
    """把一次工具运行记进留痕账本, 返回事件 id; 内部任意异常时返回 None(不抛出)。

    Args:
        tool_id: 工具/执法器标识(落 event.agent), 如 "demogame.design_doc_lint.lint"。
        event_type: 事件类型, 如 "design.lint_run"。
        activity: 人读活动描述。
        queries: 确定性决策检索词(域词+业务词); 命中记录的 id 落 consumed_decisions。
        inputs: 额外 inputs(如被检文件的 [[file:...]] 引用); 决策引用自动前置。
        outputs: 产出引用。
        meta: 事件元数据(工具结果摘要等)。
        idempotency_key: 幂等键(可选; 运行类事件通常每跑一次记一条, 不传)。
        decision_finder / ledger_append: 注入点, 默认接真实实现。

    Returns:
        成功: 事件 id。内部任意异常: None(留痕失败不影响工具主流程)。
    """
    finder = decision_finder or default_decision_finder
    appender = ledger_append or default_ledger_append

    try:
        decisions = collect_decisions(list(queries), finder)
        decision_ids = [d["id"] for d in decisions if isinstance(d, dict) and d.get("id")]

        ev_inputs = [decision_ref(rid) for rid in decision_ids] + [
            str(x) for x in (inputs or []) if str(x).strip()
        ]
        ev_outputs = [str(x) for x in (outputs or []) if str(x).strip()]

        from omnicompany.packages.services._core.ledger import LedgerEvent

        event = LedgerEvent(
            type=event_type,
            agent=tool_id,
            activity=activity,
            inputs=ev_inputs,
            outputs=ev_outputs,
            consumed_decisions=decision_ids,
            meta=dict(meta or {}),
        )
        return appender(event, idempotency_key=idempotency_key)

    except Exception:
        logger.error(
            "[provenance_hook] 运行留痕失败(不阻断工具主流程) · tool=%s type=%s",
            tool_id, event_type, exc_info=True,
        )
        return None
