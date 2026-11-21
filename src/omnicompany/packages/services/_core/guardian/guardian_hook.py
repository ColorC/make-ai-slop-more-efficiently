# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-05T17:04:51Z
# [OMNI] material_id="material:core.guardian.periodic_health_probe.implementation.py"
"""Guardian Hook — Phase 5 迁移

GuardianPeriodicHook — 每 N 轮读取系统健康摘要，产生 system_state_signal。

它综合：
  - meta_guardian_log.jsonl 最近条目（若存在）
  - semantic_network.db routing_events 最近失败率
  - evolution_signals 最近进化结论

这个 Hook 是 Guardian Consciousness 环的感官入口。
它只观测，不决策，不修改状态。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from omnicompany.runtime.routing.router import PeriodicHook, Signal


class GuardianPeriodicHook(PeriodicHook):
    """每 N 轮生成系统健康 Signal，供 GuardianMonitorNode 处理。

    读取来源（均为只读）：
    - routing_events：最近50条路由成功率
    - evolution_signals：最近5条进化结论
    - meta_guardian_log.jsonl：最近3条 MetaGuardian 评估（如存在）
    """

    COOLDOWN_ROUNDS = 5  # 每 5 轮检查一次

    def __init__(self, db_dir: str = ""):
        self._last_trigger_round = -999
        self._db_dir = db_dir  # meta_guardian_log 所在目录

    def should_poll(self, round_num: int) -> bool:
        return (round_num - self._last_trigger_round) >= self.COOLDOWN_ROUNDS

    async def poll(self, db_path: str, round_num: int) -> list[Signal]:
        signals = []
        try:
            parts: list[str] = []

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            # 1. 路由成功率（最近50条）
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as total, "
                    "SUM(CASE WHEN agent_success=1 THEN 1 ELSE 0 END) as ok "
                    "FROM (SELECT agent_success FROM routing_events "
                    "WHERE excluded_from_metrics=0 OR excluded_from_metrics IS NULL "
                    "ORDER BY id DESC LIMIT 50)"
                ).fetchone()
                if row and row["total"] > 0:
                    rate = (row["ok"] or 0) / row["total"] * 100
                    parts.append(
                        f"路由成功率（最近50条）：{row['ok']}/{row['total']} ({rate:.0f}%)"
                    )
            except Exception:
                pass

            # 2. 最近进化结论
            try:
                evo_rows = conn.execute(
                    "SELECT outcome_text, effective FROM evolution_signals "
                    "ORDER BY created_at DESC LIMIT 5"
                ).fetchall()
                if evo_rows:
                    eff_count = sum(1 for r in evo_rows if r["effective"])
                    parts.append(
                        f"最近5次进化：{eff_count}/5 有效\n"
                        + "\n".join(f"  - {r['outcome_text'][:80]}" for r in evo_rows[:2])
                    )
            except Exception:
                pass

            # 3. repair_queue 状态
            try:
                rq = conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM repair_queue GROUP BY status"
                ).fetchall()
                if rq:
                    rq_dict = {r["status"]: r["cnt"] for r in rq}
                    parts.append(
                        f"repair_queue: pending={rq_dict.get('pending', 0)} "
                        f"done={rq_dict.get('done', 0)} failed={rq_dict.get('failed', 0)}"
                    )
            except Exception:
                pass

            conn.close()

            # 4. meta_guardian_log（如存在）
            if self._db_dir:
                log_path = Path(self._db_dir) / "meta_guardian_log.jsonl"
                if log_path.exists():
                    try:
                        lines = log_path.read_text(encoding="utf-8", errors="replace").strip().split("\n")
                        recent = [json.loads(l) for l in lines[-3:] if l.strip()]
                        if recent:
                            last = recent[-1]
                            findings_txt = "; ".join(
                                f['desc'][:50] for f in last.get("findings_summary", [])[:2]
                            ) or "无异常发现"
                            parts.append(
                                f"MetaGuardian评估（round {last.get('round', '?')}）："
                                f"health={last.get('overall_health', '?'):.2f} | {findings_txt}"
                            )
                    except Exception:
                        pass

            if parts:
                self._last_trigger_round = round_num
                text = (
                    f"系统健康检查（round {round_num}）：\n"
                    + "\n".join(parts)
                )
                signals.append(Signal(
                    format="system_state_signal",
                    text=text,
                    meta={"round": round_num, "part_count": len(parts)},
                ))

        except Exception:
            pass

        return signals
