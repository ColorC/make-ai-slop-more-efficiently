# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-25T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.worker.health_record_aggregator.py"
"""WorkerHealthWriter — Router 健康档案聚合 (HARD).

**契约变更 #02 (2026-04-25)**: 去 health_score/health_grade · severity 归一 critical/major/minor.
- 不打分 (分数无统一尺度, 铁律)
- 用 health_record_v2.build_health_record 构造 v2 schema
- 孤立 Router 检测保留 · is_isolated 字段
- 摘要保留语义描述 (不含分数/等级词)

Worker 协议:
  FORMAT_IN  = diag.worker.audit
  FORMAT_OUT = diag.worker.health-record

落盘: data/registry/health/ (中央) + 就近 .omni/health/ (proximity).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._diagnosis.doctor.health_record_v2 import build_health_record
from omnicompany.protocol.anchor import Verdict, VerdictKind

# 可选 HealthArchive 集成
try:
    from omnicompany.packages.services._core.registry.archive import (
        HealthArchive,
        make_router_snapshot,
        write_proximity_snapshot,
    )
    from omnicompany.packages.services._core.registry.scanner import _infer_package
    _ARCHIVE_AVAILABLE = True
    _REGISTRY_ARCHIVE_DIR = Path(__file__).parents[6] / "data" / "registry" / "health"
except ImportError:
    _ARCHIVE_AVAILABLE = False
    HealthArchive = None  # type: ignore
    make_router_snapshot = None  # type: ignore
    write_proximity_snapshot = None  # type: ignore
    _infer_package = None  # type: ignore
    _REGISTRY_ARCHIVE_DIR = Path(".")

from ._shared import logger


class WorkerHealthWriter(Worker):
    """汇总 acc.checks, 生成 v2 Router 健康档案 (不打分)."""

    DESCRIPTION = "汇总 acc.checks 的 severity 分组 (critical/major/minor 归一), 生成 v2 Router 健康档案 (无分数/等级)"
    FORMAT_IN = "diag.worker.audit"
    FORMAT_OUT = "diag.worker.health-record"
    INPUT_KEYS = ["worker_class", "checks"]

    def run(self, input_data: Any) -> Verdict:
        worker_class: str = input_data["worker_class"]
        checks: list[dict] = input_data.get("checks", [])
        context: dict = input_data.get("context", {})
        audit_path: str | None = input_data.get("audit_path")

        # 孤立 Router 检测 · 保留为语义字段
        context_gaps = context.get("context_gaps", [])
        is_isolated = any("未在任何 pipeline.py 中使用" in g for g in context_gaps)

        # 特殊: sig_ok=False 时无法完整诊断 → 标一个 critical 占位
        sig_ok = input_data.get("sig_ok", True)
        enriched_checks = list(checks)
        if not sig_ok:
            enriched_checks.insert(0, {
                "check": "signature",
                "passed": False,
                "severity": "CRITICAL",
                "observation": f"Router '{worker_class}' 基础元数据缺失, 无法完整诊断",
            })

        summary_base = f"Router '{worker_class}' "
        health_record = build_health_record(
            enriched_checks,
            summary_base=summary_base,
            failure_repr="observation",
            # 额外域字段 · v2 原样保留
            worker_class=worker_class,
            source_file=input_data.get("source_file", ""),
            source_root=input_data.get("source_root", ""),
            sig_ok=sig_ok,
            is_isolated=is_isolated,
            audit_path=audit_path or "",
        )

        if is_isolated:
            health_record["summary"] += " [孤立 Router: 未被任何 pipeline 使用]"

        self._save_router_health(worker_class, health_record, input_data)

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=health_record,
            diagnosis=(
                f"RouterHealthWriter: {worker_class} verdict={health_record['verdict']} "
                f"counts={health_record['counts']}"
            ),
        )

    def _save_router_health(
        self, worker_class: str, health_record: dict, input_data: dict
    ) -> None:
        """中央 + 就近双写 Router 健康档案 (静默失败)."""
        if not _ARCHIVE_AVAILABLE:
            return
        try:
            source_file = input_data.get("source_file", "")
            source_root = input_data.get("source_root", "")
            pkg = _infer_package(Path(source_file), Path(source_root)) if (source_file and source_root) else "unknown"
            archive = HealthArchive(_REGISTRY_ARCHIVE_DIR)
            snapshot = make_router_snapshot(f"router:{pkg}.{worker_class}", health_record, source_file, archive)
            write_proximity_snapshot(source_file, "routers", worker_class, snapshot)
        except Exception as e:
            logger.debug("HealthArchive write skipped for %s: %s", worker_class, e)
