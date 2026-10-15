# [OMNI] origin=claude domain=services/_focus ts=2026-09-05 type=service status=active
# [OMNI] summary="收件箱处置层: 只存「你对这条消息做了什么」(忽略/延后到某时/已处理)。列表实时取源, 不物化。"
# [OMNI] why="用户 2026-09-05:「这为啥要定时同步, 这应该直接取源, 定时也太离谱了」—— 旧设计把状态机骑在 task 信封上, 逼得列表必须先物化成任务再靠 cron 推。"
# [OMNI] material_id="material:packages.services._focus.inbox_dispositions"
"""收件箱处置 —— 唯一需要持久的那一层。

旧设计的成因(不是随便写的): 「延后要能回来、忽略要留痕」需要状态, 而当时不想另立
第二套状态机, 就把状态放进 task 信封 —— 代价是**列表也跟着物化**: 一条提审只有先被
同步成任务才看得见, 于是必须有人定时推, 而且推进去就删不掉(总线无硬删)。

方向反了。该实时的是列表, 该存的只有处置:

  · **列表**: 每次现问适配器(review / agent / session), 源里有什么就是什么;
  · **处置**: 只记 dedupeKey → {忽略 | 延后到某时 | 已处理}, 一条几十字节;
  · **提升**: 只有你真决定「这是要干的活」时才落成 task —— 那时它就不是消息了。

这样 37 条积压提审是**立刻可见**的, 清掉它们写的是 37 条处置记录, 不是 37 个删不掉的
总线任务。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.RLock()

STATES = ("dismissed", "deferred", "done")


def store_path() -> Path:
    override = os.environ.get("OMNI_INBOX_DISPOSITIONS_PATH")
    if override:
        return Path(override)
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root() / "data" / "focus" / "inbox_dispositions.json"


def _read() -> dict[str, dict[str, Any]]:
    try:
        doc = json.loads(store_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = doc.get("byKey")
    if not isinstance(rows, dict):
        return {}
    return {str(k): v for k, v in rows.items() if isinstance(v, dict)}


def _write(rows: dict[str, dict[str, Any]]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "$doc": "收件箱处置: dedupeKey -> {state, until, at}。列表实时取源, 这里只存你做过什么。",
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "byKey": rows,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def all_dispositions() -> dict[str, dict[str, Any]]:
    return _read()


def set_disposition(key: str, state: str, *, until: str | None = None) -> dict[str, Any]:
    if state not in STATES:
        return {"ok": False, "code": "invalid_argument", "message": f"不认识的处置: {state}"}
    if state == "deferred" and not until:
        return {"ok": False, "code": "invalid_argument", "message": "延后要给 until"}
    with _lock:
        rows = _read()
        rows[str(key)] = {
            "state": state,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **({"until": until} if until else {}),
        }
        _write(rows)
    return {"ok": True, "key": key, "state": state}


def clear(key: str) -> bool:
    """撤销处置 —— 忽略错了要能拿回来, 这是「归档不是删除」的等价物。"""
    with _lock:
        rows = _read()
        if str(key) not in rows:
            return False
        rows.pop(str(key))
        _write(rows)
        return True


def _deferral_elapsed(row: dict[str, Any], now: float) -> bool:
    until = str(row.get("until") or "")
    if not until:
        return True
    try:
        return time.mktime(time.strptime(until[:19], "%Y-%m-%dT%H:%M:%S")) <= now
    except ValueError:
        return True


def visibility(key: str, rows: dict[str, dict[str, Any]], now: float) -> str:
    """一条消息此刻该不该占视野: show / hidden / deferred。

    延后到点就自己回来 —— 不需要任何人来推, 因为列表本来就是现算的。
    """
    row = rows.get(str(key))
    if not row:
        return "show"
    state = str(row.get("state") or "")
    if state in ("dismissed", "done"):
        return "hidden"
    if state == "deferred":
        return "show" if _deferral_elapsed(row, now) else "deferred"
    return "show"


def prune(live_keys: set[str]) -> int:
    """源里已经没有的条目, 处置记录也就没用了(那条提审早就裁完了)。

    只清 dismissed/done: 延后中的要留着 —— 它可能只是还没到点又暂时没进候选。
    """
    with _lock:
        rows = _read()
        drop = [
            key for key, row in rows.items()
            if key not in live_keys and str(row.get("state")) in ("dismissed", "done")
        ]
        for key in drop:
            rows.pop(key)
        if drop:
            _write(rows)
        return len(drop)
