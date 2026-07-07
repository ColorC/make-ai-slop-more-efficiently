# [OMNI] origin=claude-code domain=narrative_studio ts=2026-07-05T00:00:00Z type=module status=active agent=narrative_studio
# [OMNI] summary="narrative_studio 跨边界写回的留痕薄钩子:wiki 写回/删除 与 import_vilo 导入完成时各记一条进统一账本 events.jsonl;只记跨边界写回不记每次编辑保存;留痕失败绝不影响写回主流程" why="统一设计工作室接入 §编辑活动留痕:复用 services._core.ledger.provenance_hook 通用件,不另开账本;范式同 demogame.design_doc_lint._record_provenance"
# [OMNI] tags=narrative,provenance,ledger,writeback
"""narrative_studio 编辑活动留痕 —— 只记跨边界写回。

统一账本落点唯一: data/ledger/events.jsonl(经 provenance_hook.record_tool_run)。
两个跨边界事件:
- narrative.studio_writeback: game_text 写回/删除 vilo wiki(cards/events/tags,游戏内容真源);
- narrative.studio_import:   import_vilo 从讨论稿重生成整个 vilo 项目。
每次编辑保存(_persist 落 JSON)不留痕——那是项目内部态,不跨边界。

三条铁律(同 provenance_hook / demogame.design_doc_lint):
1. 留痕失败 ≠ 写回失败:本模块两个入口整段 try/except 吞掉一切异常, 决不抛回 api.py 主流程;
2. 决策检索只走确定性路径(record_tool_run 内部 allow_semantic=False), 零命中即空, 禁伪造;
3. kill switch: 环境变量 OMNI_NARRATIVE_NO_PROVENANCE=1 时整体跳过(测试/离线用)。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, Sequence

logger = logging.getLogger("narrative_studio.provenance")

_KILL_SWITCH = "OMNI_NARRATIVE_NO_PROVENANCE"


def _enabled() -> bool:
    return os.environ.get(_KILL_SWITCH) != "1"


def _record(
    event_type: str,
    activity: str,
    queries: Sequence[str],
    *,
    inputs: Optional[Sequence[str]] = None,
    meta: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """内部统一入口: 转调通用 record_tool_run。任意异常一律吞掉返回 None。

    agent(=tool_id) 固定 "narrative_studio"; 决策检索走确定性查询词。
    """
    if not _enabled():
        return None
    try:
        from omnicompany.packages.services._core.ledger.provenance_hook import record_tool_run

        return record_tool_run(
            "narrative_studio",   # tool_id → LedgerEvent.agent
            event_type,
            activity,
            queries,
            inputs=inputs,
            meta=meta,
        )
    except Exception:  # noqa: BLE001
        logger.error("[narrative_studio.provenance] 留痕失败(不阻断写回) · type=%s", event_type, exc_info=True)
        return None


def record_wiki_writeback(
    *,
    text_id: str,
    text_type: str,
    wiki_relpath: Optional[str],
    deleted: bool,
) -> Optional[str]:
    """game_text 写回/删除 vilo wiki 后留痕一条。

    Args:
        text_id/text_type: 被写回的 GameText 身份(落 meta);
        wiki_relpath: 写回的目标相对路径(None=删除或写回失败);
        deleted: True=删除, False=写入/更新。
    inputs 引用写回目标文件([[file:...]]); 决策检索用确定性域词+业务词。
    """
    action = "删除" if deleted else "写回"
    # 引用写回目标文件(有 relpath 时引用之, 删除态引用 vilo 仓相对定位便于回查)。
    inputs = [f"[[file:{wiki_relpath}]]"] if wiki_relpath else [f"[[file:vilo-wiki:game_text:{text_id}]]"]
    return _record(
        "narrative.studio_writeback",
        f"叙事落地文本{action} vilo wiki · {text_type}/{text_id}",
        ("vilo", "叙事", "认可台账"),
        inputs=inputs,
        meta={
            "stage": "文风矩阵渲染",   # 落地文本 = 管线最末层(见 formats.py STAGE_PROSE_RENDER)
            "text_id": text_id,
            "text_type": text_type,
            "deleted": deleted,
            "wiki_relpath": wiki_relpath,
        },
    )


def record_import(
    *,
    vilo_repo: str,
    game_texts: int,
    characters: int,
    rejected: int,
) -> Optional[str]:
    """import_vilo 从讨论稿重生成 vilo 项目完成后留痕一条。

    ⚠ 立意为誊抄需人工同步(见 importer._build_premise / wiki/10):导入只搬已认可方向+游戏内容,
    立意等真源改动须先改 wiki/10 再同步, 本留痕只记一次导入事实, 不代表立意已对齐。
    """
    return _record(
        "narrative.studio_import",
        f"从 vilo 讨论稿重生成项目(game_texts={game_texts}/chars={characters}/rejected={rejected})",
        ("vilo", "叙事", "认可台账"),
        inputs=[f"[[file:{vilo_repo}]]"],
        meta={
            "stage": "import",
            "vilo_repo": vilo_repo,
            "game_texts": game_texts,
            "characters": characters,
            "rejected": rejected,
        },
    )
