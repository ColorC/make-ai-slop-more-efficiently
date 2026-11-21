# [OMNI] origin=claude-code domain=services/tech_debt ts=2026-04-18T00:00:00Z
# [OMNI] material_id="material:diagnosis.tech_debt.document_drift_checker.py"
"""tech_debt.drift_checker — 周期性文档漂移巡查。

检查对象：
  - DESIGN.md（src/omnicompany/**）：代码 mtime 比 DESIGN.md mtime 新 ≥ N 天 → design_md_drift
  - docs/plans/[YYYY-MM-DD]TOPIC/plan.md（非 _archive）：
      * status=active 但 plan.md N 天无更新 → plan_stale
      * 目录日期距今 ≥ M 天且 status ≠ archived → plan_old

核心设计铁律（2026-04-18 用户明示）：
  **避重 —— 不要在同一 DESIGN.md/plan 上反反复复登记持续膨胀。**
  实现：(kind, target) 作为 dedup key，已有 open 条目则跳过（不累计 scan_count）。
  条目只有在被 resolve 后重新漂移才会再次入库。

不调 LLM；纯文件系统 mtime + 字符串解析。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .registry_io import append_row, AppendResult

logger = logging.getLogger(__name__)


# 默认阈值（天）
_DEFAULT_DESIGN_DRIFT_DAYS = 14
_DEFAULT_PLAN_STALE_DAYS = 14
_DEFAULT_PLAN_OLD_DAYS = 30

_CODE_EXTENSIONS = (".py", ".ts", ".tsx", ".rs", ".go")
_PLAN_DATE_PATTERN = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\]")
_OMNIMARK_STATUS_PATTERN = re.compile(r"status=([a-zA-Z_-]+)")


@dataclass
class DriftFinding:
    kind: str           # design_md_drift / plan_stale / plan_old
    target: str         # 相对路径
    last_change: str    # YYYY-MM-DD（代码最近变更 或 plan 目录日期）
    last_update: str    # YYYY-MM-DD（文档自身最近更新）
    drift_days: int

    def to_fields(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "target": self.target,
            "last_change": self.last_change,
            "last_update": self.last_update,
            "drift_days": str(self.drift_days),
        }


# ─── 工具函数 ────────────────────────────────────────────────────

def _fmt_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _latest_code_mtime(directory: Path) -> float | None:
    """返回 directory 下直接 .py/.ts 等代码文件的最大 mtime。"""
    latest: float | None = None
    try:
        for p in directory.iterdir():
            if not p.is_file():
                continue
            if p.suffix not in _CODE_EXTENSIONS:
                continue
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if latest is None or m > latest:
                latest = m
    except OSError:
        return None
    return latest


def _parse_omnimark_status(md_file: Path) -> str | None:
    try:
        head = md_file.read_text(encoding="utf-8", errors="replace").split("\n", 3)[0]
    except OSError:
        return None
    m = _OMNIMARK_STATUS_PATTERN.search(head)
    return m.group(1) if m else None


# ─── DESIGN.md 漂移 ──────────────────────────────────────────────

def check_design_md_drift(
    root: str | Path,
    *,
    days_threshold: int = _DEFAULT_DESIGN_DRIFT_DAYS,
) -> list[DriftFinding]:
    """扫 src/omnicompany/** 下的 DESIGN.md，返回漂移发现。"""
    root_path = Path(root)
    src_root = root_path / "src"
    if not src_root.exists():
        return []

    findings: list[DriftFinding] = []
    for design in src_root.rglob("DESIGN.md"):
        rel = design.relative_to(root_path).as_posix()
        # 排除 _graveyard / _archive / vendors
        if any(seg in rel for seg in ("_graveyard", "_archive", "/vendors/", "\\vendors\\")):
            continue

        try:
            design_mtime = design.stat().st_mtime
        except OSError:
            continue

        code_mtime = _latest_code_mtime(design.parent)
        if code_mtime is None:
            # 同目录无代码（例如纯文档包根） — 跳过
            continue

        drift_seconds = code_mtime - design_mtime
        drift_days = int(drift_seconds / 86400)
        if drift_days >= days_threshold:
            findings.append(DriftFinding(
                kind="design_md_drift",
                target=rel,
                last_change=_fmt_date(code_mtime),
                last_update=_fmt_date(design_mtime),
                drift_days=drift_days,
            ))
    return findings


# ─── Plan 漂移 ───────────────────────────────────────────────────

def check_plan_drift(
    root: str | Path,
    *,
    stale_threshold_days: int = _DEFAULT_PLAN_STALE_DAYS,
    old_threshold_days: int = _DEFAULT_PLAN_OLD_DAYS,
) -> list[DriftFinding]:
    """扫 docs/plans/[date]TOPIC/plan.md 非 archived 计划。

    - plan_stale: status=active 且 plan.md mtime 距今 >= stale_threshold
    - plan_old:   status∈{draft,design,active} 且 目录日期距今 >= old_threshold（优先 stale）
    """
    root_path = Path(root)
    plans_dir = root_path / "docs" / "plans"
    if not plans_dir.exists():
        return []

    findings: list[DriftFinding] = []
    now_ts = datetime.now(timezone.utc).timestamp()
    today = datetime.now(timezone.utc).date()

    try:
        entries = list(plans_dir.iterdir())
    except OSError:
        return []

    for plan_dir in entries:
        if not plan_dir.is_dir():
            continue
        if plan_dir.name == "_archive":
            continue
        m = _PLAN_DATE_PATTERN.match(plan_dir.name)
        if not m:
            continue
        try:
            plan_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue

        plan_md = plan_dir / "plan.md"
        if not plan_md.exists():
            continue

        status = _parse_omnimark_status(plan_md) or "unknown"
        if status in ("archived", "deprecated"):
            continue

        try:
            plan_mtime = plan_md.stat().st_mtime
        except OSError:
            continue

        days_since_edit = int((now_ts - plan_mtime) / 86400)
        days_since_start = (today - plan_date).days
        rel = plan_md.relative_to(root_path).as_posix()

        # plan_stale 优先：active 但长时间不改
        if status == "active" and days_since_edit >= stale_threshold_days:
            findings.append(DriftFinding(
                kind="plan_stale",
                target=rel,
                last_change=m.group(1),
                last_update=_fmt_date(plan_mtime),
                drift_days=days_since_edit,
            ))
            continue

        # plan_old：任何未归档的老计划
        if days_since_start >= old_threshold_days:
            findings.append(DriftFinding(
                kind="plan_old",
                target=rel,
                last_change=m.group(1),
                last_update=_fmt_date(plan_mtime),
                drift_days=days_since_start,
            ))
    return findings


# ─── 统一入口 ────────────────────────────────────────────────────

def run_drift_audit(
    root: str | Path,
    *,
    dry_run: bool = False,
    design_days: int = _DEFAULT_DESIGN_DRIFT_DAYS,
    stale_days: int = _DEFAULT_PLAN_STALE_DAYS,
    old_days: int = _DEFAULT_PLAN_OLD_DAYS,
) -> dict[str, Any]:
    """跑全量 drift audit，写入 REGISTRY §文档漂移。

    避重：(kind, target) 已有 open DR-NNN → 跳过，不累计，不新增。
    """
    root_path = Path(root)
    design_findings = check_design_md_drift(root_path, days_threshold=design_days)
    plan_findings = check_plan_drift(
        root_path,
        stale_threshold_days=stale_days,
        old_threshold_days=old_days,
    )
    all_findings = design_findings + plan_findings

    summary = {
        "dry_run": dry_run,
        "design_count": len(design_findings),
        "plan_count": len(plan_findings),
        "total_findings": len(all_findings),
        "added": 0,
        "deduped": 0,
        "errors": 0,
        "added_ids": [],
        "examples": [f.target for f in all_findings[:5]],
    }

    if dry_run:
        return summary

    for f in all_findings:
        result: AppendResult = append_row(
            root_path,
            section_name="doc_drift",
            fields=f.to_fields(),
            dedup_keys=("kind", "target"),
        )
        if result.action == "added":
            summary["added"] += 1
            summary["added_ids"].append(result.row_id)
        elif result.action == "deduped":
            summary["deduped"] += 1
        else:
            summary["errors"] += 1
            logger.warning("drift_checker: %s → %s", f.target, result.error)

    return summary
