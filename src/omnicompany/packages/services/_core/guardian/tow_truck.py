# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-05T00:00:00Z
# [OMNI] material_id="material:core.guardian.tow_truck.disposition_engine.py"
"""OmniTow — 违规处置系统（拖车 + 告示牌 + 罚单）

Phase 1: warn + stamp（只写头，不动文件）
Phase 2: tombstone + quarantine（移动文件，原地写告示牌）
Phase 3 (2026-04-28 加): relocate (LLM 判目标位置 + 自动 mv, 失败降级 quarantine)

六种处置动作（04-OMNITOW.md + GUARDIAN-DOCS-CONFISCATION 阶段三）：
  warn          — 仅记录，不动文件
  stamp         — 注入 OmniMark(origin=unknown status=pending-review)
  tombstone     — 插入 UNIDENTIFIED 头 + 加入 watchlist（Phase 2）
  quarantine    — 备份到 .omni/quarantine/ + 原地写 TOMBSTONE（Phase 2）
  relocate      — 调 LLM 判目标位置, 信心 ≥ 0.8 自动 mv (Phase 3)
                  · 命中 hygiene_whitelist 跳过, 仅 warn (存量豁免铁律)
                  · LLM 失败 / 信心 < 0.8 降级到 quarantine
  evolve-signal — 发 EvolutionSignal（Phase 4，暂存日志）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = Path("e:/WindowsWorkspace/omnicompany")
_OMNI_DIR = ".omni"

Disposition = Literal["warn", "stamp", "tombstone", "quarantine", "relocate", "evolve-signal"]


# ─── 罚单数据结构 ────────────────────────────────────────────────

@dataclass
class Ticket:
    ticket_id: str
    detected_at: str
    rule_violated: str
    severity: str
    original_path: str
    disposition: list[str]
    file_fingerprint: str
    omnimark_detected: dict
    status: str = "open"           # open / whitelisted / resolved / deleted
    quarantine_path: str = ""
    llm_explanation: str = ""
    recommended_action: str = ""
    whitelist_expires: Optional[str] = None
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ─── TOMBSTONE 模板 ──────────────────────────────────────────────

_TOMBSTONE_TEMPLATE = """\
# +==============================================================+
# | [OMNI-TOMBSTONE] 该位置发生过未注册写入, 内容已没收归档      |
# +==============================================================+
#
# 这份罚单留在原位置是为了**提醒写入者** — 你刚才的写入没走 omnicompany
# 的注册流程, 内容被守护没收作为"非法写入材料"归档到了备份位置.
#
# 收缴时间: {detected_at}
# 罚单编号: {ticket_id}
# 违规规则: {rule_id} -- {rule_desc}
# 原始路径: {original_path}
# 归档位置 (没收内容仍可查): {quarantine_path}
#
# 双轨可查:
#   - 7 天内: 这份罚单留在原位提醒你
#   - 7 天后: 罚单清理, 归档位置仍永久保留没收的原内容
#
# 处理建议:
#   1. 走 omni sandbox 写一份合规版本 (先到 .omni/sandbox/drafts/, 自检后转正式区)
#   2. 或者: 走 omni register 直接把这份内容转正 (适用于内容本身合规, 只是没注册)
#   3. 跑 omni guardian patrol 确认违规已解决
#
# 申请临时豁免 (24h 白名单):
#   omni guardian whitelist {original_path} --reason "..."
#
# 人工恢复 (从归档区拉回来, 确认内容合规):
#   omni guardian restore {ticket_id}
#
# -------- 原始内容 (已没收, 仅供参考, 真本体在归档位置) -------
#
{content_commented}
"""

_UNIDENTIFIED_TEMPLATE = """\
# [OMNI-UNIDENTIFIED] 此文件由未知来源写入，等待身份认领
# 发现时间: {detected_at}
# 文件指纹: {fingerprint}
#
# 认领命令:
#   omni guardian acknowledge {path} --identity "origin=human domain=..."
#
# [以下为原始文件内容，正常生效]
# ----------------------------------------------------------------

"""


# ─── OmniTow ────────────────────────────────────────────────────

class OmniTow:
    """违规处置系统。

    Phase 1 行为：
    - warn: 只写日志，不动文件
    - stamp: 补打 OmniMark 头（origin=unknown status=pending-review）
    - tombstone / quarantine: 仅记录罚单（Phase 2 才实际移动文件）
    - evolve-signal: 写入 .omni/evolution/ 日志

    Phase 2 行为（phase2=True）：
    - tombstone: 插入 UNIDENTIFIED 头 + 写 watchlist
    - quarantine: 备份原始文件 + 原地写 TOMBSTONE
    """

    # 试点区默认值（2026-04-08 Session 2 扩大）：
    # 以下规则启用 Phase 2 quarantine。这些都是"文件放错地方"类，
    # 当前 0 命中，开启后无既存误报风险；未来一旦有人闯祸立刻生效。
    _DEFAULT_PILOT_RULES: frozenset[str] = frozenset({
        "OMNI-007",   # 散落的 config/doc 文件
        "OMNI-006",   # temp 脚本进 src/
        "OMNI-008",   # src/omnicompany/ 根目录下的业务 .py
        "OMNI-014",   # src/omnicompany/ 下非法 drawer 目录
    })

    def __init__(
        self,
        project_root: str | Path = _DEFAULT_ROOT,
        phase2: bool = False,
        pilot_rules: frozenset[str] | None = None,
    ):
        self._root = Path(project_root)
        self._omni_dir = self._root / _OMNI_DIR
        self._phase2 = phase2
        # pilot_rules=None → 使用默认试点集合；frozenset() → 全量 Phase 2（无限制）
        self._pilot_rules: frozenset[str] | None = (
            pilot_rules if pilot_rules is not None else self._DEFAULT_PILOT_RULES
        )

    # ── 目录初始化 ───────────────────────────────────────────────

    def _ensure_dirs(self) -> None:
        for sub in ["quarantine", "watchlist", "whitelist", "evolution", "tmp"]:
            (self._omni_dir / sub).mkdir(parents=True, exist_ok=True)

    # ── 主处置入口 ───────────────────────────────────────────────

    def process(self, violation: dict) -> Ticket:
        """处置单条违规，返回生成的 Ticket。

        Args:
            violation: run_patrol() 结果中的 violations 条目
        """
        self._ensure_dirs()
        now = datetime.now(timezone.utc).isoformat()

        ticket = Ticket(
            ticket_id=violation.get("ticket_id", "TICKET-UNKNOWN"),
            detected_at=now,
            rule_violated=violation.get("rule_id", "?"),
            severity=violation.get("severity", "?"),
            original_path=violation.get("path", ""),
            disposition=violation.get("disposition", ["warn"]),
            file_fingerprint=self._fingerprint(violation.get("path", "")),
            omnimark_detected={},
            llm_explanation=violation.get("message", ""),
            recommended_action=(
                violation.get("suggestion")
                or self._recommend(violation.get("rule_id", ""))
            ),
        )

        for action in ticket.disposition:
            self._apply(action, ticket, violation)

        self._save_ticket(ticket)
        return ticket

    def process_all(self, violations: list[dict]) -> list[Ticket]:
        """批量处置，返回所有 Ticket。"""
        return [self.process(v) for v in violations]

    # ── 单项动作 ─────────────────────────────────────────────────

    def _apply(self, action: str, ticket: Ticket, violation: dict) -> None:
        match action:
            case "warn":
                logger.warning(
                    "[OmniTow WARN] %s  %s  %s",
                    ticket.ticket_id, ticket.rule_violated, ticket.original_path,
                )
            case "stamp":
                self._do_stamp(ticket)
            case "tombstone":
                if self._phase2 and self._in_pilot(ticket):
                    self._do_tombstone(ticket)
                else:
                    reason = (
                        f"规则 {ticket.rule_violated} 不在试点范围"
                        if self._phase2 else "Phase 2 未启用"
                    )
                    logger.info("[OmniTow] tombstone 跳过（%s）: %s", reason, ticket.original_path)
            case "quarantine":
                if self._phase2 and self._in_pilot(ticket):
                    self._do_quarantine(ticket)
                else:
                    reason = (
                        f"规则 {ticket.rule_violated} 不在试点范围"
                        if self._phase2 else "Phase 2 未启用"
                    )
                    logger.info("[OmniTow] quarantine 跳过（%s）: %s", reason, ticket.original_path)
            case "relocate":
                self._do_relocate(ticket, violation)
            case "evolve-signal":
                self._do_evolve_signal(ticket, violation)
            case _:
                logger.debug("[OmniTow] 未知动作 %s，跳过", action)

    def _do_stamp(self, ticket: Ticket) -> None:
        """注入 OmniMark(origin=unknown status=pending-review)。"""
        try:
            from omnicompany.core.omnimark import stamp_file
            abs_path = self._root / ticket.original_path
            stamp_file(abs_path, origin="unknown", status="pending-review")
            logger.info("[OmniTow STAMP] %s", ticket.original_path)
        except Exception as e:
            logger.warning("[OmniTow] stamp 失败 %s: %s", ticket.original_path, e)

    def _do_tombstone(self, ticket: Ticket) -> None:
        """插入 UNIDENTIFIED 告示头 + 加入 watchlist。"""
        abs_path = self._root / ticket.original_path
        if not abs_path.exists():
            return
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
            header = _UNIDENTIFIED_TEMPLATE.format(
                detected_at=ticket.detected_at,
                fingerprint=ticket.file_fingerprint,
                path=ticket.original_path,
            )
            abs_path.write_text(header + content, encoding="utf-8")
            self._save_watchlist(ticket)
            logger.warning("[OmniTow TOMBSTONE] %s", ticket.original_path)
        except Exception as e:
            logger.warning("[OmniTow] tombstone 失败 %s: %s", ticket.original_path, e)

    def _do_quarantine(self, ticket: Ticket) -> None:
        """备份文件到隔离区，原地写 TOMBSTONE 告示牌。"""
        abs_path = self._root / ticket.original_path
        if not abs_path.exists():
            return
        try:
            date_str = ticket.detected_at[:10]
            q_dir = self._omni_dir / "quarantine" / date_str
            q_dir.mkdir(parents=True, exist_ok=True)
            q_path = q_dir / abs_path.name
            # 备份原始文件
            shutil.copy2(str(abs_path), str(q_path))
            ticket.quarantine_path = str(q_path.relative_to(self._root))

            # 原始内容注释化（放在 TOMBSTONE 里）
            original = abs_path.read_text(encoding="utf-8", errors="replace")
            commented = "\n".join(f"# {ln}" for ln in original.splitlines())

            tombstone = _TOMBSTONE_TEMPLATE.format(
                detected_at=ticket.detected_at,
                ticket_id=ticket.ticket_id,
                rule_id=ticket.rule_violated,
                rule_desc=ticket.llm_explanation[:80],
                original_path=ticket.original_path,
                quarantine_path=ticket.quarantine_path,
                content_commented=commented,
            )
            abs_path.write_text(tombstone, encoding="utf-8")
            logger.warning(
                "[OmniTow QUARANTINE] %s → %s",
                ticket.original_path, ticket.quarantine_path,
            )
        except Exception as e:
            logger.warning("[OmniTow] quarantine 失败 %s: %s", ticket.original_path, e)

    def _do_relocate(self, ticket: Ticket, violation: dict) -> None:
        """LLM 判目标位置 → 信心高自动 mv, 否则降级 quarantine.

        关键规则:
        - 第一步检查 hygiene_whitelist (存量豁免) → 命中即跳过, 仅 warn
        - 调 relocate_judge.judge_relocate_target() 单次 LLM 调用
        - 信心 ≥ 0.8: mv 文件, 罚单标 resolved + recommended_action 记 LLM 理由
        - 信心 < 0.8: 降级到 _do_quarantine (会原地写告示牌)
        - LLM 失败 (None): 同上降级 quarantine
        - 干跑 OMNI_GUARDIAN_DRY_RUN=1: 不真 mv, 罚单记 "would relocate to X"
        """
        # 第一步: 豁免检查 (D1 D3 铁律 — 存量永不挪)
        try:
            from .hygiene_whitelist import load_whitelist, is_whitelisted
            wl = load_whitelist(self._root)
            entry = is_whitelisted(ticket.rule_violated, ticket.original_path, wl)
            if entry is not None:
                logger.info(
                    "[OmniTow RELOCATE 跳过] %s 命中豁免 (%s expires=%s): %s",
                    ticket.ticket_id, entry.path_pattern, entry.expires or "无",
                    ticket.original_path,
                )
                ticket.recommended_action = (
                    f"存量豁免 {entry.path_pattern} (理由: {entry.reason}); "
                    f"到期 {entry.expires or '无'} 重审"
                )
                return
        except Exception as e:
            logger.warning("[OmniTow] 豁免检查异常 (按未豁免处理): %s", e)

        # 第二步: 读文件内容 (片段, 不预防性截断 — 由 judge 自己处理)
        abs_path = self._root / ticket.original_path
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace") if abs_path.exists() else None
        except Exception:
            content = None

        # 第三步: LLM 判定
        try:
            from .relocate_judge import judge_relocate_target
            decision = judge_relocate_target(
                path=ticket.original_path,
                content=content,
                rule_id=ticket.rule_violated,
                rule_message=ticket.llm_explanation or violation.get("message", ""),
            )
        except Exception as e:
            logger.warning("[OmniTow RELOCATE] judge 异常: %s, 降级 quarantine", e)
            decision = None

        if decision is None:
            logger.info(
                "[OmniTow RELOCATE 降级] %s LLM 判定失败, 走 quarantine: %s",
                ticket.ticket_id, ticket.original_path,
            )
            ticket.recommended_action = "LLM relocate 判定失败, 已降级 quarantine"
            self._do_quarantine(ticket)
            return

        # 信心 < 0.8 也降级
        if decision.confidence < 0.8:
            logger.info(
                "[OmniTow RELOCATE 降级] %s 信心 %.2f < 0.8 (建议 %s · %s), 走 quarantine",
                ticket.ticket_id, decision.confidence, decision.target_path, decision.reason,
            )
            ticket.recommended_action = (
                f"LLM 信心 {decision.confidence:.2f} < 0.8, 建议 → {decision.target_path} "
                f"(理由: {decision.reason}); 已降级 quarantine 待人工确认"
            )
            self._do_quarantine(ticket)
            return

        # 第四步: 干跑模式不真 mv
        dry_run = os.environ.get("OMNI_GUARDIAN_DRY_RUN") == "1"
        if dry_run:
            logger.warning(
                "[OmniTow RELOCATE 干跑] %s would mv → %s (信心 %.2f · %s)",
                ticket.ticket_id, decision.target_path, decision.confidence, decision.reason,
            )
            ticket.recommended_action = (
                f"DRY_RUN: would relocate → {decision.target_path} "
                f"(信心 {decision.confidence:.2f}, 理由: {decision.reason})"
            )
            return

        # 第五步: 真 mv
        target_abs = self._root / decision.target_path
        try:
            target_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(abs_path), str(target_abs))
            ticket.status = "resolved"
            ticket.resolved_at = ticket.detected_at
            ticket.resolved_by = f"OmniTow.relocate ({decision.model})"
            ticket.recommended_action = (
                f"已 relocate → {decision.target_path} (信心 {decision.confidence:.2f}, "
                f"理由: {decision.reason})"
            )
            logger.warning(
                "[OmniTow RELOCATE] %s mv → %s (信心 %.2f)",
                ticket.ticket_id, decision.target_path, decision.confidence,
            )
        except Exception as e:
            logger.warning("[OmniTow RELOCATE] mv 失败 %s, 降级 quarantine: %s", abs_path, e)
            ticket.recommended_action = f"mv 失败 ({e}), 降级 quarantine"
            self._do_quarantine(ticket)

    def _do_evolve_signal(self, ticket: Ticket, violation: dict) -> None:
        """交给 OmniEvolve 处理内部管线违规矫正（三级升级机制）。"""
        try:
            from .evolve_signal import OmniEvolve
            evo = OmniEvolve(project_root=self._root)
            signal = evo.process(violation)
            if signal is not None:
                logger.info(
                    "[OmniTow] evolve-signal L%d: %s  节点=%s",
                    signal.escalation_level, ticket.ticket_id, signal.source_node,
                )
            else:
                # 非内部管线来源：仍写 pending_signals.jsonl 备查
                evo_dir = self._omni_dir / "evolution"
                evo_dir.mkdir(parents=True, exist_ok=True)
                entry = {
                    "signal_id": f"EVS-{ticket.ticket_id}",
                    "source_ticket": ticket.ticket_id,
                    "rule_violated": ticket.rule_violated,
                    "violation_path": ticket.original_path,
                    "detected_at": ticket.detected_at,
                    "note": "non-internal origin, skipped escalation",
                }
                log_file = evo_dir / "pending_signals.jsonl"
                with log_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("[OmniTow] evolve-signal 处理失败: %s", e)

    # ── 辅助 ─────────────────────────────────────────────────────

    def _in_pilot(self, ticket: Ticket) -> bool:
        """判断是否在 Phase 2 试点范围内。
        pilot_rules=None（不应发生）→ 拒绝；frozenset() → 全量放行；否则按集合判断。
        """
        if self._pilot_rules is None:
            return False
        if len(self._pilot_rules) == 0:
            return True   # frozenset() 传入表示全量 Phase 2
        return ticket.rule_violated in self._pilot_rules

    def _fingerprint(self, rel_path: str) -> str:
        abs_path = self._root / rel_path
        try:
            data = Path(abs_path).read_bytes()
            return "sha256:" + hashlib.sha256(data).hexdigest()[:16]
        except OSError:
            return "sha256:unknown"

    def _recommend(self, rule_id: str) -> str:
        recs = {
            "OMNI-001": "使用 omni guardian stamp 补打 [OMNI] 身份头",
            "OMNI-002": "将文件移至 src/omnicompany/packages/<namespace>/<domain>/",
            "OMNI-003": "所有 LLM 调用改用 omnicompany.runtime.llm.llm.LLMClient",
            "OMNI-004": "将 async def run() 改为 def run()（Router 同步协议）",
            "OMNI-005": "将 .db 文件移至 data/ 目录",
            "OMNI-006": "将临时脚本移至 scripts/ 或 tests/ 目录",
            "OMNI-007": "将配置/文档文件移至 docs/ 或 config/ 目录",
        }
        return recs.get(rule_id, "参考 OmniGuardian 设计文档处理违规")

    def _save_ticket(self, ticket: Ticket) -> None:
        """将罚单写入 .omni/quarantine/<date>/<ticket_id>.json。"""
        try:
            date_str = ticket.detected_at[:10]
            q_dir = self._omni_dir / "quarantine" / date_str
            q_dir.mkdir(parents=True, exist_ok=True)
            ticket_file = q_dir / f"{ticket.ticket_id}.json"
            ticket_file.write_text(
                json.dumps(ticket.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 更新索引
            self._update_index(ticket)
        except Exception as e:
            logger.debug("[OmniTow] 罚单写入失败: %s", e)

    def _update_index(self, ticket: Ticket) -> None:
        """追加更新 .omni/quarantine/index.json。"""
        index_file = self._omni_dir / "quarantine" / "index.json"
        try:
            if index_file.exists():
                index = json.loads(index_file.read_text(encoding="utf-8"))
            else:
                index = []
            entry = {
                "ticket_id": ticket.ticket_id,
                "rule": ticket.rule_violated,
                "severity": ticket.severity,
                "path": ticket.original_path,
                "status": ticket.status,
                "detected_at": ticket.detected_at,
            }
            # 去重：同 ticket_id 覆盖
            index = [e for e in index if e.get("ticket_id") != ticket.ticket_id]
            index.append(entry)
            index_file.write_text(
                json.dumps(index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("[OmniTow] 索引更新失败: %s", e)

    def _save_watchlist(self, ticket: Ticket) -> None:
        """将 UNIDENTIFIED 文件加入监视名单。"""
        try:
            watch_dir = self._omni_dir / "watchlist"
            watch_dir.mkdir(parents=True, exist_ok=True)
            safe_name = ticket.original_path.replace("/", "-").replace("\\", "-")
            watch_file = watch_dir / f"{safe_name}.watch.json"
            entry = {
                "path": ticket.original_path,
                "added_at": ticket.detected_at,
                "fingerprint": ticket.file_fingerprint,
                "unidentified_since": ticket.detected_at,
                "source_ticket": ticket.ticket_id,
                "change_count": 0,
            }
            watch_file.write_text(
                json.dumps(entry, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("[OmniTow] watchlist 写入失败: %s", e)

    # ── 逾期升级 (Phase 4 第四C 步, 2026-04-28) ────────────────────

    def escalate_overdue_tickets(self, threshold_days: int = 7) -> dict:
        """扫 status=open + detected_at > threshold_days 天的罚单, 升级到 evolve-signal.

        升级动作:
          - 罚单 status 改为 "overdue-escalated" + 写回 .omni/quarantine/<date>/<id>.json
          - 在 .omni/evolution/overdue_signals.jsonl 追加一条事件
          - 索引同步更新

        触发位置: sentinel 唤醒时调用; 不消耗 LLM (纯文件操作).

        Args:
            threshold_days: 罚单 detected_at 距今超过此天数视为逾期 (默认 7).

        Returns:
            {
                "escalated_count": int,
                "escalated_ticket_ids": list[str],
                "skipped_count": int,        # 已 resolved / already-escalated
            }
        """
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=threshold_days)
        cutoff_iso = cutoff.isoformat()
        index_file = self._omni_dir / "quarantine" / "index.json"
        if not index_file.exists():
            return {"escalated_count": 0, "escalated_ticket_ids": [], "skipped_count": 0}

        try:
            index = json.loads(index_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[OmniTow] 读 index.json 失败: %s", e)
            return {"escalated_count": 0, "escalated_ticket_ids": [], "skipped_count": 0}

        evo_dir = self._omni_dir / "evolution"
        evo_dir.mkdir(parents=True, exist_ok=True)
        overdue_log = evo_dir / "overdue_signals.jsonl"

        escalated: list[str] = []
        skipped = 0
        for entry in index:
            tid = entry.get("ticket_id", "")
            status = entry.get("status", "open")
            detected = entry.get("detected_at", "")
            if status != "open" or not detected:
                skipped += 1
                continue
            if detected >= cutoff_iso:
                # 还没逾期
                continue

            # 升级
            entry["status"] = "overdue-escalated"
            escalated.append(tid)

            # 写事件流
            try:
                event = {
                    "event": "overdue-escalated",
                    "ticket_id": tid,
                    "rule_id": entry.get("rule"),
                    "path": entry.get("path"),
                    "severity": entry.get("severity"),
                    "detected_at": detected,
                    "escalated_at": datetime.now(timezone.utc).isoformat(),
                    "threshold_days": threshold_days,
                }
                with overdue_log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.debug("[OmniTow] overdue 事件写入失败: %s", e)

            # 写回单条罚单 (status 字段)
            try:
                date_str = detected[:10]
                ticket_file = self._omni_dir / "quarantine" / date_str / f"{tid}.json"
                if ticket_file.exists():
                    data = json.loads(ticket_file.read_text(encoding="utf-8"))
                    data["status"] = "overdue-escalated"
                    ticket_file.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            except Exception as e:
                logger.debug("[OmniTow] 罚单 %s status 写回失败: %s", tid, e)

        # 写回索引
        if escalated:
            try:
                index_file.write_text(
                    json.dumps(index, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                logger.warning("[OmniTow] index.json 写回失败: %s", e)

        if escalated:
            logger.warning(
                "[OmniTow] 逾期升级 %d 条罚单 (>%d 天 status=open): %s",
                len(escalated), threshold_days, escalated[:5],
            )
        return {
            "escalated_count": len(escalated),
            "escalated_ticket_ids": escalated,
            "skipped_count": skipped,
        }

    # ── 查询接口 ─────────────────────────────────────────────────

    def list_tickets(self, status: str | None = None) -> list[dict]:
        """返回所有罚单（可按 status 过滤）。"""
        index_file = self._omni_dir / "quarantine" / "index.json"
        if not index_file.exists():
            return []
        try:
            index = json.loads(index_file.read_text(encoding="utf-8"))
            if status:
                index = [e for e in index if e.get("status") == status]
            return index
        except Exception:
            return []

    def get_ticket(self, ticket_id: str) -> dict | None:
        """读取单张罚单详情。"""
        q_root = self._omni_dir / "quarantine"
        if not q_root.exists():
            return None
        for q_dir in q_root.iterdir():
            if not q_dir.is_dir():
                continue
            ticket_file = q_dir / f"{ticket_id}.json"
            if ticket_file.exists():
                try:
                    return json.loads(ticket_file.read_text(encoding="utf-8"))
                except Exception:
                    return None
        return None

    def resolve_ticket(self, ticket_id: str, resolved_by: str = "human") -> bool:
        """将罚单标记为已解决。"""
        ticket_data = self.get_ticket(ticket_id)
        if not ticket_data:
            return False
        ticket_data["status"] = "resolved"
        ticket_data["resolved_at"] = datetime.now(timezone.utc).isoformat()
        ticket_data["resolved_by"] = resolved_by
        # 找到文件并写回
        q_root = self._omni_dir / "quarantine"
        if not q_root.exists():
            return False
        for q_dir in q_root.iterdir():
            if not q_dir.is_dir():
                continue
            ticket_file = q_dir / f"{ticket_id}.json"
            if ticket_file.exists():
                ticket_file.write_text(
                    json.dumps(ticket_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self._update_index_status(ticket_id, "resolved")
                return True
        return False

    def whitelist_ticket(
        self, ticket_id: str, hours: int = 24, reason: str = ""
    ) -> bool:
        """将罚单加入临时白名单。"""
        from datetime import timedelta
        ticket_data = self.get_ticket(ticket_id)
        if not ticket_data:
            return False
        expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        ticket_data["status"] = "whitelisted"
        ticket_data["whitelist_expires"] = expires
        ticket_data["whitelist_reason"] = reason
        for q_dir in (self._omni_dir / "quarantine").iterdir():
            if not q_dir.is_dir():
                continue
            ticket_file = q_dir / f"{ticket_id}.json"
            if ticket_file.exists():
                ticket_file.write_text(
                    json.dumps(ticket_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                # 写入白名单文件
                wl_file = self._omni_dir / "whitelist" / "whitelist.json"
                try:
                    wl = json.loads(wl_file.read_text(encoding="utf-8")) if wl_file.exists() else []
                    wl = [e for e in wl if e.get("ticket_id") != ticket_id]
                    wl.append({"ticket_id": ticket_id, "expires": expires, "reason": reason})
                    wl_file.write_text(json.dumps(wl, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                self._update_index_status(ticket_id, "whitelisted")
                return True
        return False

    def _update_index_status(self, ticket_id: str, status: str) -> None:
        index_file = self._omni_dir / "quarantine" / "index.json"
        try:
            if not index_file.exists():
                return
            index = json.loads(index_file.read_text(encoding="utf-8"))
            for e in index:
                if e.get("ticket_id") == ticket_id:
                    e["status"] = status
                    break
            index_file.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def is_whitelisted(self, path: str) -> bool:
        """检查路径是否在有效白名单内（未过期）。"""
        wl_file = self._omni_dir / "whitelist" / "whitelist.json"
        if not wl_file.exists():
            return False
        try:
            now = datetime.now(timezone.utc).isoformat()
            wl = json.loads(wl_file.read_text(encoding="utf-8"))
            for e in wl:
                if e.get("path") == path and (e.get("expires") or "") > now:
                    return True
        except Exception:
            pass
        return False
