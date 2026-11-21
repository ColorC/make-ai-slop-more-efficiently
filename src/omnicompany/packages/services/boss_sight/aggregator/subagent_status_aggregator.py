# [OMNI] origin=ai-ide ts=2026-05-23 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.aggregator.subagent_status_aggregator.py"
"""subagent_status_aggregator — 维护当前所有 subagent 活跃情况.

落实 T1.3.3: subagent 活跃情况装载. ground_truth § 7.7 第 2 项明确这是
omnicompany 现状缺口 (EventBus.read_trace 只能查单 trace, 没有全局聚合).

策略:
- 订阅 EventBus 的 subagent.* 事件 (subagent.spawned / completed / blocked /
  fork / shutdown)
- 在内存维护 subagent_id → SubagentStatus 表
- 提供 list_active() / get_by_id() / to_material_payload() 给总控 ctx 用
- 不持久化到磁盘 — 重启重建 (从 cc_sessions.json 里恢复 alive=True 的)

设计权衡: 块 1 阶段 EventBus 上还没有 subagent.* 事件源, 这个 aggregator 当前是
**骨架** — 一旦块 3 把 subagent 接通, 这个 aggregator 自动激活.
块 1 阶段它从 cc_sessions.json 读 alive 状态作为唯一数据源.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from omnicompany.core.caller_identity import CALLER_SUBAGENT
from omnicompany.packages.services.boss_sight.controller.worker_contract import (
    WORKER_KIND_STANDALONE,
    WORKER_KIND_TEAM,
)

_log = logging.getLogger(__name__)


SubagentState = Literal["idle", "running", "blocked", "done", "shutdown"]
SubagentKind = Literal[WORKER_KIND_TEAM, WORKER_KIND_STANDALONE]

# "真在跑"窗口: 会话 alive 但其 transcript(.jsonl)近 N 秒无写入 → 视为 idle(僵尸), 不算 running。
# 默认 10 分钟。用户反馈: alive 标志结束不清, 一堆早停的被算成 running。
_ACTIVE_WINDOW_SEC = int(os.environ.get("OMNI_AGENT_ACTIVE_WINDOW_SEC", "600") or 600)


def _transcript_mtime(cwd: str | None, claude_session_id: str | None) -> float | None:
    """会话 transcript(.jsonl) 的最后写入时间 —— 真实活动信号(LLM 每轮都会写)。"""
    if not claude_session_id:
        return None
    try:
        from omnicompany.dashboard.ccdaemon.api import claude_jsonl_for
        p = claude_jsonl_for(cwd or "", claude_session_id)
        if p is not None and p.is_file():
            return p.stat().st_mtime
    except Exception:  # noqa: BLE001
        return None
    return None


@dataclass
class SubagentStatus:
    """单个 subagent 的活跃情况."""

    subagent_id: str                # session id (chat-xxx / claude_session_uuid / etc.)
    kind: SubagentKind | str = WORKER_KIND_STANDALONE
    state: SubagentState | str = "idle"
    plan_id: str | None = None      # 关联 plan
    cwd: str | None = None
    started_at_ts: str | None = None  # ISO 8601
    last_event_ts: str | None = None  # ISO 8601
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class SubagentStatusAggregator:
    """订阅 EventBus 维护活跃 subagent 状态.

    块 1 阶段使用 cc_sessions.json 做主数据源 (EventBus 的 subagent.* 事件还没源头).
    块 3 接通后, EventBus 订阅会接管, 这里只需在 __init__ 里注册一次 subscribe.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root)
        self.cc_sessions_path = self.workspace_root / "data" / "cc_sessions.json"
        self._subagents: dict[str, SubagentStatus] = {}
        # TODO 块 3 接通: self._bus_subscription = bus.subscribe(...) for subagent.*

    def refresh_from_cc_sessions(self) -> None:
        """从 cc_sessions.json 拉所有 alive session 当做 subagent. 块 1 主路径."""
        if not self.cc_sessions_path.is_file():
            return
        try:
            data = json.loads(self.cc_sessions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        new: dict[str, SubagentStatus] = {}
        for sid, fields in data.items():
            if not isinstance(fields, dict):
                continue
            # 总控自己 (kind=controller) 不算 subagent
            if fields.get("kind") == "controller" or fields.get("provider") == "controller":
                continue
            # 真 subagent = 总控派出去的(caller_identity==subagent)。用户自己另开的 codex/claude
            # 会话不是 subagent, 不该算进"运行线程/subagent"口径(用户反馈 2026-06-05)。
            if fields.get("caller_identity") != CALLER_SUBAGENT:
                continue
            state: SubagentState
            raw_status = str(fields.get("status") or fields.get("state") or "").lower()
            ended = raw_status == "ended" or fields.get("ended_at") is not None
            if raw_status == "blocked":
                state = "blocked"
            elif ended:
                state = "done"
            elif fields.get("alive"):
                # alive 不等于真在跑: 看 transcript 近况。近 N 秒有写入才算 running, 否则 idle(僵尸不计入运行)。
                mt = _transcript_mtime(fields.get("cwd"), fields.get("claude_session_id"))
                state = "running" if (mt is not None and (time.time() - mt) <= _ACTIVE_WINDOW_SEC) else "idle"
            else:
                state = "idle"
            started_at = fields.get("started_at")
            started_iso: str | None = None
            if isinstance(started_at, (int, float)):
                started_iso = datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat()
            new[sid] = SubagentStatus(
                subagent_id=sid,
                kind=WORKER_KIND_STANDALONE,  # cc_sessions 里都是 standalone
                state=state,
                plan_id=fields.get("active_plan"),
                cwd=fields.get("cwd"),
                started_at_ts=started_iso,
                last_event_ts=datetime.now(timezone.utc).isoformat(),
            )
        self._subagents = new

    def list_active(self) -> list[SubagentStatus]:
        """所有非 done/shutdown 的 subagent."""
        return [s for s in self._subagents.values() if s.state in {"idle", "running", "blocked"}]

    def list_all(self) -> list[SubagentStatus]:
        return list(self._subagents.values())

    def get_by_id(self, subagent_id: str) -> SubagentStatus | None:
        return self._subagents.get(subagent_id)

    # ------------------------------------------------------------------
    # EventBus 事件 handler (块 3 接通后真用)
    # ------------------------------------------------------------------

    def on_subagent_spawned(self, payload: dict) -> None:
        sid = payload.get("subagent_id")
        if not sid:
            return
        self._subagents[sid] = SubagentStatus(
            subagent_id=sid,
            kind=payload.get("kind", WORKER_KIND_STANDALONE),
            state="running",
            plan_id=payload.get("plan_id"),
            cwd=payload.get("cwd"),
            started_at_ts=datetime.now(timezone.utc).isoformat(),
            last_event_ts=datetime.now(timezone.utc).isoformat(),
        )

    def on_subagent_completed(self, payload: dict) -> None:
        sid = payload.get("subagent_id")
        if sid and sid in self._subagents:
            self._subagents[sid].state = "done"
            self._subagents[sid].last_event_ts = datetime.now(timezone.utc).isoformat()

    def on_subagent_blocked(self, payload: dict) -> None:
        sid = payload.get("subagent_id")
        if sid and sid in self._subagents:
            self._subagents[sid].state = "blocked"
            self._subagents[sid].last_event_ts = datetime.now(timezone.utc).isoformat()

    def on_subagent_shutdown(self, payload: dict) -> None:
        sid = payload.get("subagent_id")
        if sid and sid in self._subagents:
            self._subagents[sid].state = "shutdown"
            self._subagents[sid].last_event_ts = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # 给总控 ctx 注入用
    # ------------------------------------------------------------------

    def to_material_payload(self) -> dict:
        return {
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "active_count": len(self.list_active()),
            "total_count": len(self._subagents),
            "subagents": [s.to_dict() for s in self._subagents.values()],
        }


__all__ = [
    "SubagentState",
    "SubagentKind",
    "SubagentStatus",
    "SubagentStatusAggregator",
    "WORKER_KIND_STANDALONE",
    "WORKER_KIND_TEAM",
]
