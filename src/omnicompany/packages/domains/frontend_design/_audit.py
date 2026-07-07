# [OMNI] origin=claude-code domain=frontend_design ts=2026-07-04T00:00:00Z type=lib status=active
# [OMNI] summary="DeterministicGate 接真的确定性件:把 surface 解析成可静态审计的前端 src 目录、真跑 ux_audit 三维枚举、把错位标记翻成带 L1/L2/L3 分级的 failures;审不了的输入如实降级不假 PASS。"
# [OMNI] why="统一设计工作室计划(UNIFIED-DESIGN-STUDIO §5 M6/§10 第四期):gate 接确定性前端审计器 ux_audit,分诊词汇=docs/standards/review/发现分诊三级规范.md。"
# [OMNI] tags=frontend_design,gate,ux_audit,triage
"""DeterministicGate 的确定性审计件(纯函数,可单测,ux_audit 注入点。)

- resolve_auditable_src(surface, branch): surface 若指向本仓 dashboard 前端可静态审计的
  目录/文件, 归一成一个 src 目录; 否则返回 None(外部 URL / 截图 / 不存在等 → 降级)。
- run_ux_audit(src_root, audit_fn): 真跑 ux_audit 确定性三维枚举 + 汇总。audit_fn 默认接
  真实 enumerators, 单测可注入桩/抛异常桩。
- audit_to_failures(report): 把 ux_audit 的 offenders(错位界面)翻成 gate failures,
  每条按「自动处置错了的代价」判 L1/L2/L3(发现分诊三级规范)。

分诊映射(判级看自动处置错了的代价, 不看发现置信度):
  🔴* (信息无层级 / 重排)      → L3  结构错位, 禁自动改版, 挂人裁决重排。
  🟠平铺 / ⚪删除无保护 / 🟡说明冗余 → L2  有依据但需人裁量, 报告呈现不拦流程。
  ux_audit 不产 L1(它标的都是需重排的结构问题, 没有"补一行零风险加法"那类)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


# ── surface → 可审计 src 目录 ────────────────────────────────────────────────

_SRC_MARKERS = ("entities", "shell", "styles", "components")


def _looks_like_frontend_src(d: Path) -> bool:
    """像前端源码根的 src 目录(含 entities/shell/styles/components 任一)。"""
    return d.is_dir() and any((d / m).is_dir() for m in _SRC_MARKERS)


def _walk_up_for_src(start: Path) -> Path | None:
    """从一个前端文件/目录向上找到最近的、像前端源码根的 `.../src` 目录(ux_audit 扫的粒度)。"""
    cur = start if start.is_dir() else start.parent
    # 自身就是可审计 src
    if cur.name == "src" and _looks_like_frontend_src(cur):
        return cur
    if _looks_like_frontend_src(cur):
        return cur
    for anc in [cur, *cur.parents]:
        if anc.name == "src" and _looks_like_frontend_src(anc):
            return anc
        cand = anc / "src"
        if _looks_like_frontend_src(cand):
            return cand
    return None


def resolve_auditable_src(surface: str, branch: str) -> Path | None:
    """把 surface 归一成可静态审计的前端 src 目录; 审不了返回 None。

    可审计判据(全部满足才算): dashboard 分支 + surface 是本机存在的路径 +
    能定位到一个像前端源码根的 src 目录(含 entities/shell/styles/components 任一)。
    外部 URL(http/https)、截图路径、不存在的路径、webgame 分支 一律 None → 降级。
    """
    if branch != "dashboard":
        return None
    s = (surface or "").strip()
    if not s or s.lower().startswith(("http://", "https://", "data:")):
        return None
    p = Path(s)
    try:
        if not p.exists():
            return None
    except OSError:
        return None
    return _walk_up_for_src(p)


# ── 真跑 ux_audit(注入点)──────────────────────────────────────────────────

def _real_ux_audit(src_root: str) -> dict:
    """接真实 ux_audit 确定性枚举 + 汇总, 返回其 report(不落 team 编排, 直调纯函数)。"""
    from omnicompany.packages.services._diagnosis.ux_audit.enumerators import (
        consolidate,
        enum_info,
        enum_interactions,
        enum_nav,
    )

    accum = {
        "src_root": src_root,
        "interactions": enum_interactions(src_root),
        "info": enum_info(src_root),
        "nav": enum_nav(src_root),
    }
    return consolidate(accum)


def run_ux_audit(src_root: str, audit_fn: Callable[[str], dict] | None = None) -> dict:
    """真跑确定性 UX 审计。audit_fn 默认真实枚举; 单测注入桩/抛异常桩。

    异常向上冒泡(gate 层决定如何处置: FAIL, 绝不吞成假 PASS)。
    """
    fn = audit_fn or _real_ux_audit
    return fn(src_root)


# ── ux_audit offenders → gate failures(带 L1/L2/L3)──────────────────────────

def _grade(flags: list[str]) -> str:
    """据错位标记判分诊级(见模块 docstring 的映射表)。"""
    joined = " ".join(flags or [])
    if "🔴" in joined:
        return "L3"
    return "L2"


def audit_to_failures(report: dict) -> tuple[list[dict], list[str]]:
    """把 ux_audit report 翻成 (failures, checked)。

    failures: 每条 {rule, severity(=L1/L2/L3), evidence, locator, triage}。
    checked: 已跑过的确定性规则名(供 gate_result.checked 透传)。
    """
    checked = ["ux_audit.interactions", "ux_audit.info", "ux_audit.nav"]
    failures: list[dict] = []
    for row in report.get("offenders") or []:
        flags = row.get("flags") or []
        level = _grade(flags)
        panel = row.get("panel", "?")
        evidence = (
            f"错位标记 {' '.join(flags)} · 露钮 {row.get('露钮', 0)}"
            f"/收纳 {row.get('kebab_items', 0)} · 说明段 {row.get('long_text', 0)}"
        )
        failures.append({
            "rule": "ux_audit.offender",
            "severity": level,
            "triage": level,
            "evidence": evidence,
            "locator": panel,
        })
    return failures, checked
