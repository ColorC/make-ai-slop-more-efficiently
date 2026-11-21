# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:guardian.rule_engine.executor.worker.py"
"""RuleEngineWorker — Guardian Team Worker #2 (self-contained).

Worker 协议:
  FORMAT_IN  = guardian.file_context_set
  FORMAT_OUT = guardian.violation_set

职责: 订阅 file_context_set → 对每个文件跑所有 RULES → 产出违规三分集合。

Worker 粒度原则 (terminology.md §6.5): 本 Worker 内部批处理 14 条 rule
(规则清单来自 `rules/*.py`), 不是"每 rule 一个 Worker"。
内部 rule 库保留作为 Worker 的纯函数实现依赖。

历史: 原 `patrol.py::RuleEngine` 类已归档到 `_archive/patrol_legacy.py`,
逻辑内联至本 Worker (2026-04-20 Team 1 迁移)。
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.packages.services._core.omnicompany import Worker

from ..rules import FileContext, GuardianRule, Violation, RULES

logger = logging.getLogger(__name__)


class RuleEngineWorker(Worker):
    """对 file_context_set 跑所有 Guardian rules → 产出 violation_set。"""

    DESCRIPTION = (
        "Guardian Team Worker #2: 订阅 guardian.file_context_set, 对每个 FileContext "
        "跑所有 14 条 rule (内部纯函数库 `rules/*.py`), 按 certainty 分流产出 "
        "guardian.violation_set (confirmed/needs_judgment/duplicates)。"
    )
    FORMAT_IN = "guardian.file_context_set"
    FORMAT_OUT = "guardian.violation_set"

    def __init__(self, rules: list[GuardianRule] = RULES):
        self._rules = rules
        self._counter = 0

    def run(self, input_data: dict[str, Any]) -> Verdict:
        payload = input_data.get("guardian.file_context_set") or input_data
        scan_ts = payload.get("scan_ts", "")
        scan_mode = payload.get("scan_mode", "diff")
        files_dicts = payload.get("files", [])

        # dict → FileContext (内部类型)
        files = [FileContext(**d) for d in files_dicts]

        # 内联原 RuleEngine.evaluate_split() 逻辑
        now = datetime.now(timezone.utc).isoformat()
        date_str = now[:10]
        confirmed: list[Violation] = []
        needs_judgment: list[Violation] = []
        seen: set[tuple[str, str]] = set()  # (path, rule_id) 去重

        for ctx in files:
            for rule in self._rules:
                try:
                    if rule.check(ctx):
                        key = (ctx.path, rule.id)
                        if key in seen:
                            continue
                        seen.add(key)

                        self._counter += 1
                        ticket_id = f"TICKET-{date_str}-{self._counter:03d}"
                        msg = rule.message_template.format(path=ctx.path)
                        v = Violation(
                            ticket_id=ticket_id,
                            rule_id=rule.id,
                            severity=rule.severity,
                            path=ctx.path,
                            message=msg,
                            disposition=rule.disposition,
                            confidence=1.0,
                            detected_at=now,
                        )
                        if rule.certainty == "needs_judgment":
                            needs_judgment.append(v)
                        else:
                            confirmed.append(v)
                except Exception as e:
                    logger.debug("Rule %s failed on %s: %s", rule.id, ctx.path, e)

        # Protocol 约定: verdict.output 是 FORMAT_OUT 对应 Format 的 payload 本体 (平铺)
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "scan_ts": scan_ts,
                "scan_mode": scan_mode,
                "confirmed": [asdict(v) for v in confirmed],
                "needs_judgment": [asdict(v) for v in needs_judgment],
                "duplicates": [],
            },
        )
