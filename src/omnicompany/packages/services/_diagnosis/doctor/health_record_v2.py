# [OMNI] origin=claude-code domain=services/doctor ts=2026-04-25T00:00:00Z type=helper
# [OMNI] material_id="material:diagnosis.doctor.health_record.schema_v2_engine.py"
"""doctor health-record schema v2 共享 helper · 契约变更 #02 (2026-04-25).

用户 2026-04-25 硬指示:
- 不打分 (分数无统一尺度)
- severity 归一 critical/major/minor (跟 docauthor Reviewer 一致)
- 不做 v1 兼容

本模块定义:
- `SCHEMA_VERSION = 2` 常量
- `normalize_severity(raw)` · 上游 4 档 (CRITICAL/HIGH/MEDIUM/LOW/INFO) → 3 档 (critical/major/minor) 或 None (INFO 丢弃)
- `build_health_record(checks, context=..., **kwargs)` · 从 raw checks 构造 v2 health_record dict
- `is_v2_record(record)` · 判断是否合规 v2 (读路径用)

**禁**: 任何 health_score / health_grade / 加权求和 / 阈值→等级映射逻辑.
"""
from __future__ import annotations

from typing import Any


SCHEMA_VERSION = 2


# ═══════════════════════════════════════════════════════════════════
# severity 归一 · 4+1 档 → 3 档
# ═══════════════════════════════════════════════════════════════════

_SEVERITY_MAP = {
    # 上游 raw (大写)
    "CRITICAL": "critical",
    "HIGH":     "major",
    "MEDIUM":   "minor",
    "LOW":      "minor",
    "INFO":     None,       # INFO 是提示非问题, 不进 failures / counts
    # 已经是小写形式的直通
    "critical": "critical",
    "major":    "major",
    "minor":    "minor",
    "info":     None,
}


def normalize_severity(raw: str | None) -> str | None:
    """raw → 3 档 · 返回 None 表丢弃 (INFO)."""
    if not raw:
        return None
    return _SEVERITY_MAP.get(raw, "minor")     # 未知 severity 保守归 minor


# ═══════════════════════════════════════════════════════════════════
# 从 checks 构造 v2 health_record
# ═══════════════════════════════════════════════════════════════════

def build_health_record(
    checks: list[dict],
    *,
    summary_base: str = "",
    failure_repr: str = "observation",       # failures_by_severity 条目取 check 的哪个字段
    **extra_fields: Any,
) -> dict[str, Any]:
    """把 raw checks list 聚合成 v2 health_record dict.

    Args:
        checks: raw check list · 每条含 `passed`, `severity`, `check`, `observation`/`detail`
        summary_base: summary 字段前缀 (例 "Router 'FooRouter' "), 后面自动补 check 统计
        failure_repr: failures_by_severity 条目里放 check 的哪个字段 · 'observation' 默认
        **extra_fields: 额外字段原样落入 record (例 router_class / source_file / sig_ok)

    Returns:
        v2 health_record dict · 含 schema_version=2, verdict, passed, failures_by_severity,
        counts, checks (原样), summary, 加上 extra_fields.

    不做:
        - 任何数字 score 计算
        - 任何 A/B/C/D/F 等级映射
        - v1 兼容 (老调用者若传 health_score/health_grade 会被忽略)
    """
    failures_by_severity: dict[str, list[str]] = {
        "critical": [],
        "major":    [],
        "minor":    [],
    }
    counts = {
        "total_checks": 0,
        "passed_checks": 0,
        "critical": 0,
        "major": 0,
        "minor": 0,
    }

    for check in checks:
        counts["total_checks"] += 1
        passed = check.get("passed")
        if passed is True:
            counts["passed_checks"] += 1
            continue
        if passed is None:
            continue       # 跳过 (上游未判)
        # passed == False · 进 failures
        sev_raw = check.get("severity")
        sev = normalize_severity(sev_raw)
        if sev is None:
            continue       # INFO · 丢
        name = check.get("check", "?")
        obs = check.get(failure_repr) or check.get("detail") or check.get("observation") or ""
        if isinstance(obs, dict):
            obs = str(obs)
        failures_by_severity[sev].append(f"{name}: {obs}")
        counts[sev] += 1

    passed_overall = counts["critical"] == 0
    verdict = "healthy" if passed_overall and counts["major"] == 0 else (
        "uncertain" if passed_overall else "unhealthy"
    )

    summary = summary_base + _format_summary_tail(counts, failures_by_severity)

    record = {
        "schema_version": SCHEMA_VERSION,
        "verdict":  verdict,
        "passed":   passed_overall,
        "checks":   checks,                              # 保留全量 raw (含原 CRITICAL/HIGH/MEDIUM/...)
        "failures_by_severity": failures_by_severity,    # 3 档归一, 给消费者
        "counts":   counts,
        "summary":  summary,
        **extra_fields,                                  # 域字段原样 (例 router_class)
    }
    return record


def _format_summary_tail(counts: dict, failures: dict[str, list[str]]) -> str:
    """给 summary 加"N critical / M major / K minor"尾缀."""
    parts = [f"{counts['total_checks']} checks, {counts['passed_checks']} passed"]
    if counts["critical"]:
        parts.append(f"{counts['critical']} critical ({'; '.join(failures['critical'][:2])}{'...' if len(failures['critical']) > 2 else ''})")
    if counts["major"]:
        parts.append(f"{counts['major']} major")
    if counts["minor"]:
        parts.append(f"{counts['minor']} minor")
    return " · ".join(parts)


# ═══════════════════════════════════════════════════════════════════
# v2 校验 (读路径用)
# ═══════════════════════════════════════════════════════════════════

_REQUIRED_V2_KEYS = ("schema_version", "verdict", "passed",
                     "failures_by_severity", "counts", "checks")


def is_v2_record(record: Any) -> bool:
    """严格判 · record 是否 v2 · 不容错 · 不兼容 v1."""
    if not isinstance(record, dict):
        return False
    if record.get("schema_version") != SCHEMA_VERSION:
        return False
    for k in _REQUIRED_V2_KEYS:
        if k not in record:
            return False
    return True


def assert_no_legacy_fields(record: dict, *, context: str = "") -> None:
    """硬断言: record 不含 health_score / health_grade 等旧字段.

    给测试用. Raises AssertionError.
    """
    legacy = [k for k in ("health_score", "health_grade") if k in record]
    assert not legacy, (
        f"v2 health_record 不得含旧字段 {legacy} (context={context})"
    )


__all__ = [
    "SCHEMA_VERSION",
    "normalize_severity",
    "build_health_record",
    "is_v2_record",
    "assert_no_legacy_fields",
]
