# [OMNI] origin=claude-code domain=frontend_design ts=2026-07-04T00:00:00Z type=lib status=active
# [OMNI] summary="DeterministicGate 接真的确定性件:把 surface 解析成可静态审计的前端 src 目录、真跑 ux_audit 三维枚举 + style_gates 六条扫描 + token_drift 三方对账、把发现翻成带 L1/L2/L3 分级的 failures;审不了的输入如实降级不假 PASS。"
# [OMNI] why="统一设计工作室计划(UNIFIED-DESIGN-STUDIO §5 M6/§10 第四期):gate 接确定性前端审计器 ux_audit,分诊词汇=docs/standards/review/发现分诊三级规范.md。2026-07-18 M4(UNIFIED-FRONTEND-UPGRADE):style_gates/token_drift 进管线门禁 CI 化。"
# [OMNI] tags=frontend_design,gate,ux_audit,style_gates,token_drift,triage
"""DeterministicGate 的确定性审计件(纯函数,可单测,ux_audit/style_gates/token_drift 注入点。)

- resolve_auditable_src(surface, branch): surface 若指向本仓 dashboard 前端可静态审计的
  目录/文件, 归一成一个 src 目录; 否则返回 None(外部 URL / 截图 / 不存在等 → 降级)。
- run_ux_audit(src_root, audit_fn): 真跑 ux_audit 确定性三维枚举 + 汇总。audit_fn 默认接
  真实 enumerators, 单测可注入桩/抛异常桩。
- run_style_gates(dash_src, style_fn): 真跑 style_gates 六条扫描(ambient-recipe /
  motion-budget / emoji-scan / glass-scope / touch-target / hover-only)。LOFA 树独立
  于 surface **始终扫**; dashboard 树仅当 dash_src 非空(surface 已解析到本仓前端 src)。
- run_token_drift(drift_fn): 真跑 token_drift.reconcile 三方对账(theme.css ↔
  frostpane.css ↔ lofa tokens.css)。仅当 surface 解析到本仓前端 src 才由 gate 调用。
- audit_to_failures / style_gates_to_failures / token_drift_to_failures: 把三套审计器的
  发现统一翻成 gate failures(结构一致: rule/severity/triage/evidence/locator)。

分诊映射(判级看自动处置错了的代价, 不看发现置信度):
  🔴* (信息无层级 / 重排)      → L3  结构错位, 禁自动改版, 挂人裁决重排。
  🟠平铺 / ⚪删除无保护 / 🟡说明冗余 → L2  有依据但需人裁量, 报告呈现不拦流程。
  ux_audit 不产 L1(它标的都是需重排的结构问题, 没有"补一行零风险加法"那类)。
  style_gates 自带分级: L2(ambient-recipe/touch-target 硬线)→L2, L3(待评清单)→L3。
  token_drift 任一项漂移 → L2(token 真源失信属有依据但需人裁量的修正)。
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


# ── M4: style_gates 六条扫描(LOFA 始终扫; dashboard 侧随 surface 解析)────────

def _repo_root() -> Path | None:
    """omnicompany 仓根(以 theme.css 真源为锚); 找不到返回 None → 降级。"""
    anchor = "docs/projects/frontend-design/dashboard/theme.css"
    for q in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        if (q / anchor).is_file():
            return q
    return None


def _under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _real_style_gates(dash_src: str | None) -> dict:
    """真跑 style_gates 六条枚举器, 返回 {"results": {gate: [finding]}, "degraded": [说明]}。

    LOFA 树(仓根旁 lofa/app/www)独立于 surface 始终扫; dashboard 树仅当 dash_src 非空。
    树不存在时不冒充扫过——记 degraded 说明, 如实降级。
    """
    from omnicompany.packages.services._diagnosis.ux_audit import style_gates as sg

    root = _repo_root()
    trees: list[tuple[str, Path, Path]] = []
    degraded: list[str] = []
    if root is None:
        return {"results": {}, "degraded": ["style_gates: 定位不到仓根(theme.css 锚缺失) → 如实降级"]}
    if dash_src:
        src = Path(dash_src).resolve()  # os.walk 沿此产出绝对路径, 与 base 口径一致
        base = root if _under(src, root) else src
        trees.append(("dashboard", src, base))
    lofa = root.parent / "lofa" / "app" / "www"
    if lofa.is_dir():
        trees.append(("lofa", lofa, root.parent))
    else:
        degraded.append("style_gates.lofa: 缺审计树 lofa/app/www(仓旁 LOFA 检出不存在) → 如实降级")
    if not trees:
        return {"results": {}, "degraded": degraded or ["style_gates: 无可扫树 → 如实降级"]}
    results: dict[str, list[dict]] = {}
    for gate, _level, _rule, fn in sg._GATES:
        results[gate] = fn(trees)
    return {"results": results, "degraded": degraded}


def run_style_gates(dash_src: str | None, style_fn: Callable[[str | None], dict] | None = None) -> dict:
    """真跑 style_gates 扫描。style_fn 默认真实件; 单测注入桩/抛异常桩。

    入参 = 已解析的 dashboard src(未解析传 None, 此时只扫 LOFA 树)。
    异常向上冒泡(gate 层决定如何处置: FAIL, 绝不吞成假 PASS)。
    """
    fn = style_fn or _real_style_gates
    return fn(dash_src)


def style_gates_to_failures(report: dict) -> tuple[list[dict], list[str]]:
    """把 style_gates report 翻成 (failures, checked), finding 结构照 ux_audit offenders 翻译。

    style_gates 自带分级: L2(硬违规)→L2, L3(待评清单)→L3。
    """
    results = (report or {}).get("results") or {}
    checked = [f"style_gates.{gate}" for gate in results]
    failures: list[dict] = []
    for gate, findings in results.items():
        for f in findings or []:
            level = f.get("level") or "L3"
            failures.append({
                "rule": f"style_gates.{gate}",
                "severity": level,
                "triage": level,
                "evidence": f"{f.get('rule', '')} · {f.get('snippet', '')}",
                "locator": f"{f.get('file', '?')}:L{f.get('line', '?')}",
            })
    return failures, checked


# ── M4: token_drift 三方对账(仅当 surface 解析到本仓前端 src)─────────────────

def _real_token_drift() -> dict:
    """真跑 token_drift.reconcile, 返回 {"result": reconcile结果|None, "degraded": [说明]}。

    三份 token 文件缺任一份 → result=None + degraded 说明(如实降级, 不冒充零漂移)。
    """
    from omnicompany.packages.services._diagnosis.ux_audit import token_drift as td

    root = _repo_root()
    if root is None:
        return {"result": None, "degraded": ["token_drift: 定位不到仓根(theme.css 锚缺失) → 如实降级"]}
    theme = root / "docs/projects/frontend-design/dashboard/theme.css"
    frostpane = root / "src/omnicompany/dashboard/frontend/src/styles/frostpane.css"
    lofa = root.parent / "lofa" / "app" / "www" / "css" / "tokens.css"
    missing = [p for p in (theme, frostpane, lofa) if not p.is_file()]
    if missing:
        return {"result": None,
                "degraded": ["token_drift: 缺文件 " + "; ".join(str(p) for p in missing) + " → 如实降级"]}
    return {"result": td.reconcile(theme, frostpane, lofa), "degraded": []}


def run_token_drift(drift_fn: Callable[[], dict] | None = None) -> dict:
    """真跑 token 三方对账。drift_fn 默认真实件; 单测注入桩/抛异常桩。

    异常向上冒泡(gate 层决定如何处置: FAIL, 绝不吞成假 PASS)。
    """
    fn = drift_fn or _real_token_drift
    return fn()


def token_drift_to_failures(report: dict) -> tuple[list[dict], list[str]]:
    """把 token_drift report 翻成 (failures, checked)。任一项漂移(暗色/基础或亮色) = L2。"""
    result = (report or {}).get("result") or {}
    if not result:
        return [], []
    failures: list[dict] = []
    for r in result.get("drift") or []:
        loc = f"theme.css:L{r['theme_line']}" if r.get("theme_line") else str(r.get("key", "?"))
        failures.append({
            "rule": "token_drift.drift",
            "severity": "L2",
            "triage": "L2",
            "evidence": (f"token 漂移 {r.get('key')}: theme={r.get('theme')} / "
                         f"frostpane={r.get('frostpane')} / lofa={r.get('lofa')}"),
            "locator": loc,
        })
    for r in (result.get("light") or {}).get("drift") or []:
        failures.append({
            "rule": "token_drift.light-drift",
            "severity": "L2",
            "triage": "L2",
            "evidence": (f"亮色 token 漂移 {r.get('key')}: theme={r.get('theme')} / "
                         f"frostpane={r.get('frostpane')}"),
            "locator": str(r.get("key", "?")),
        })
    return failures, ["token_drift.reconcile"]
