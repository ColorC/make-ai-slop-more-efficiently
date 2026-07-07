# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-07-03T00:00:00Z type=router
# [OMNI] material_id="material:core.guardian.workers.plan_bindings_scanner.implementation.py"
"""PlanBindingsScanWorker — Guardian 绑定注册表巡检 Worker (2026-07-03 打回硬化㈠首发).

对齐 HygieneScanWorker 的既有样板: OMNI-099 规则的 check 用 _noop_check 占位,
真实扫描必须挂在一个"会被跑到"的执行路径上——本 Worker 就是这条路径。

Worker 协议:
  FORMAT_IN  = guardian.plan-bindings-request
  FORMAT_OUT = guardian.plan-bindings-report

职责:
  调 plan_bindings_guardian.scan_plan_binding_violations(), 把返回的
  [{plan_id, category, detail}, ...] 打包成 Violation 列表 + 落盘报告。
  纯确定性, 不调用任何 LLM。

使用:
  > omni guardian plan-bindings scan
  > 定时任务 guard-plan-bindings-daily (.omni/cron/guard-plan-bindings-daily.json)

产出落盘:
  data/services/guardian/plan-bindings/plan-bindings-<ts>.json
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ..rules._base import Violation
from ..rules.plan_bindings_guardian import scan_plan_binding_violations

logger = logging.getLogger(__name__)

_CATEGORY_SEVERITY = {
    "missing_anchor": "MEDIUM",
    "incomplete": "MEDIUM",
    "dangling_test_file": "HIGH",
    "dangling_whatnow": "MEDIUM",
    "empty_error_samples": "LOW",
}


class PlanBindingsScanWorker(Worker):
    """扫描绑定注册表(计划-进度-测试-评审四件登记)违规, 产出告警清单。"""

    DESCRIPTION = (
        "Guardian 绑定注册表巡检: 缺锚(missing_anchor)/登记不完整(incomplete)/"
        "悬空测试文件或 whatnow 回指(dangling_*)/错误样本为空(empty_error_samples)。"
        "只产告警不清理, 纯确定性扫描不调 LLM。"
    )
    FORMAT_IN = "guardian.plan-bindings-request"
    FORMAT_OUT = "guardian.plan-bindings-report"
    INPUT_KEYS = ["project_root"]

    def run(self, input_data: dict[str, Any]) -> Verdict:
        project_root_str = input_data.get("project_root")
        if project_root_str:
            project_root = Path(project_root_str)
        else:
            from omnicompany.core.config import _project_root
            project_root = _project_root()

        if not project_root.exists():
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"project_root 不存在: {project_root}",
            )

        now = datetime.now(timezone.utc).isoformat()
        raw_violations = scan_plan_binding_violations(project_root)

        violations: list[Violation] = []
        by_category: dict[str, int] = {}
        for i, item in enumerate(raw_violations, start=1):
            category = item.get("category", "unknown")
            by_category[category] = by_category.get(category, 0) + 1
            violations.append(Violation(
                ticket_id=f"TICKET-{now[:10]}-PBIND-{i:03d}",
                rule_id="OMNI-099",
                severity=_CATEGORY_SEVERITY.get(category, "MEDIUM"),
                path=item.get("plan_id", ""),
                message=(
                    f"{item.get('plan_id')}: {category} — {item.get('detail')}\n"
                    f"  登记走 omni governance bind set <plan_id> ...; "
                    f"件级走 omni governance bind item set <plan_id> <item_id> ...; "
                    f"豁免走 --review-mode exempt --review-reason '...'."
                ),
                disposition=["warn"],
                confidence=1.0,
                detected_at=now,
            ))

        # 落盘(与 HygieneScanWorker 同一落点范式: data/services/guardian/<子目录>/)
        try:
            from omnicompany.core.config import resolve_service_data_dir
            out_dir = resolve_service_data_dir("guardian") / "plan-bindings"
            out_dir.mkdir(parents=True, exist_ok=True)
            report_path = out_dir / f"plan-bindings-{now.replace(':', '-')}.json"
            report_path.write_text(
                json.dumps(
                    {
                        "scan_ts": now,
                        "project_root": str(project_root),
                        "violations": [asdict(v) for v in violations],
                        "violation_count": len(violations),
                        "by_category": by_category,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            try:
                from omnicompany.core.omnimark import write_data_sidecar
                write_data_sidecar(
                    report_path,
                    written_by=f"{self.__class__.__module__}.{self.__class__.__name__}",
                    source_path=__file__,
                    ttl_days=30,
                )
            except Exception as e:
                logger.debug("sidecar 写入失败 (非致命): %s", e)
            logger.info("plan-bindings scan report written: %s", report_path)
        except Exception as e:
            logger.warning("plan-bindings scan report 落盘失败: %s", e)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "project_root": str(project_root),
                "scan_ts": now,
                "violations": [asdict(v) for v in violations],
                "violation_count": len(violations),
                "by_category": by_category,
            },
            diagnosis=(
                f"plan-bindings scan: {len(violations)} 违规 (by category: {by_category})"
                if violations
                else "plan-bindings scan: 绑定注册表巡检干净 · 0 违规"
            ),
        )


__all__ = ["PlanBindingsScanWorker"]
