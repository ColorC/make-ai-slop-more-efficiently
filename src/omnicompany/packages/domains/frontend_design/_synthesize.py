# [OMNI] origin=claude-code domain=frontend_design ts=2026-07-04T00:00:00Z type=lib status=active
# [OMNI] summary="Synthesize 接真的确定性件:汇总 gate failures + vlm comparisons 成 improvements+报告(写 run_dir);把本次运行的 L3 级发现落统一决策库(幂等);运行留痕调 provenance_hook。决策写入注入 library.upsert,留痕注入 record_tool_run,均可单测。"
# [OMNI] why="统一设计工作室计划(UNIFIED-DESIGN-STUDIO §5 M6/§6 D5/§10 第四期):decisions_recorded 填真实 id 不再空数组硬编码;留痕失败不阻断门禁。"
# [OMNI] tags=frontend_design,synthesize,decisions,provenance
"""Synthesize 的确定性件:汇总 → improvements+报告 → 决策沉淀 → 运行留痕。

三块可注入(默认接真, 单测各自替身):
  - build_report(...)                  纯函数, 无副作用外只写 run_dir 报告文件。
  - persist_l3_decisions(..., upsert)  把 L3 发现落统一决策库(kind=comment/status=open),
                                        幂等键 alias=fd-run-<run名>-<序号>; upsert 默认接
                                        decisions.library.upsert。返回真实落库 id 列表。
  - record_run(..., recorder)          调 provenance_hook.record_tool_run 留痕; recorder 默认
                                        接真实件。留痕失败(返 None / 抛异常)绝不阻断——由本层吞。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


# ── 汇总 → improvements + 报告 ───────────────────────────────────────────────

def build_improvements(failures: list[dict], comparisons: list[dict]) -> list[dict]:
    """把门禁 failures 与相对评审 comparisons 汇成结构化改进建议(证据列表, 不打分)。"""
    improvements: list[dict] = []
    for f in failures or []:
        improvements.append({
            "source": "gate",
            "triage": f.get("triage") or f.get("severity") or "",
            "locator": f.get("locator", ""),
            "evidence": f.get("evidence", ""),
            "suggestion": f"重排 {f.get('locator','该面板')}(错位: {f.get('evidence','')})",
        })
    for c in comparisons or []:
        verdict = c.get("verdict", "")
        if verdict in ("", "same", "n/a"):
            continue
        improvements.append({
            "source": "vlm",
            "aspect": c.get("aspect", ""),
            "verdict": verdict,
            "evidence": c.get("evidence", ""),
        })
    return improvements


def build_report(
    *,
    branch: str,
    surface: str,
    project: str,
    run_dir: str,
    gate_status: str,
    failures: list[dict],
    comparisons: list[dict],
    improvements: list[dict],
) -> str:
    """写一份人读 markdown 报告到 run_dir/report.md, 返回其路径(写盘失败返回空串, 不抛)。"""
    lines = [
        f"# 前端审查报告 · {branch} 分支",
        "",
        f"- surface: `{surface}`",
        f"- project: `{project}`",
        f"- gate_status: `{gate_status}`",
        f"- 门禁发现: {len(failures)} 条 · 相对评审: {len(comparisons)} 条 · 改进建议: {len(improvements)} 条",
        "",
    ]
    if failures:
        lines += ["## 确定性门禁发现(证据列表, 不打分)", "",
                  "| 分级 | 定位 | 证据 |", "|---|---|---|"]
        for f in failures:
            lines.append(
                f"| {f.get('triage') or f.get('severity','')} | {f.get('locator','')} | {f.get('evidence','')} |"
            )
        lines.append("")
    if comparisons:
        lines += ["## VLM 相对评审(对基准图, 不打分)", "",
                  "| 方面 | 判定 | 证据 |", "|---|---|---|"]
        for c in comparisons:
            lines.append(f"| {c.get('aspect','')} | {c.get('verdict','')} | {c.get('evidence','')} |")
        lines.append("")
    if improvements:
        lines += ["## 改进建议", ""]
        for imp in improvements:
            if imp.get("source") == "gate":
                lines.append(f"- [{imp.get('triage','')}] {imp.get('suggestion','')}")
            else:
                lines.append(f"- [vlm/{imp.get('verdict','')}] {imp.get('aspect','')}: {imp.get('evidence','')}")
        lines.append("")
    md = "\n".join(lines) + "\n"
    try:
        rd = Path(run_dir)
        rd.mkdir(parents=True, exist_ok=True)
        rp = rd / "report.md"
        from omnicompany.core.guarded_write import write_file
        write_file(rp, md, origin="frontend_design", domain="frontend_design",
                   purpose="前端审查汇总报告", writer="internal-engine")
        return str(rp)
    except Exception:
        return ""


# ── 决策沉淀: L3 发现 → 统一决策库(幂等)──────────────────────────────────────

def _default_upsert(record: dict) -> tuple[dict, bool]:
    from omnicompany.packages.domains.decisions import library

    return library.upsert(record)


def persist_l3_decisions(
    *,
    failures: list[dict],
    project: str,
    run_dir: str,
    report_path: str,
    upsert: Callable[[dict], tuple[dict, bool]] | None = None,
) -> list[str]:
    """把本次运行的 L3 级发现落统一决策库, 返回真实落库 id 列表(可能为空)。

    kind=comment / status=open / project=入题的 project / anchor.ref=报告文件(run_dir 的报告);
    tags 含 frontend_design; 幂等键 alias=fd-run-<run名>-<序号>(同一 run 重跑合并进原 id)。
    upsert 抛异常时向上冒泡(决策写入是 synthesize 的职责, 失败要如实, 不静默)。
    """
    up = upsert or _default_upsert
    run_name = Path(run_dir).name if run_dir else "unknown"
    l3 = [f for f in (failures or []) if (f.get("triage") or f.get("severity")) == "L3"]
    recorded: list[str] = []
    for i, f in enumerate(l3, start=1):
        alias = f"fd-run-{run_name}-{i:02d}"
        rec = {
            "kind": "comment",
            "status": "open",
            "scope": "project",
            "project": project,
            "statement": f"[前端审查·L3] {f.get('locator','?')}: {f.get('evidence','')}"[:200],
            "aliases": [alias],
            "tags": ["frontend_design", "ux_audit", "L3", "前端审查发现"],
            "anchor": {
                "kind": "doc",
                "ref": report_path or run_dir,
                "excerpt": f.get("evidence", ""),
            },
            "origin": {"channel": "manual", "author": "frontend_design.pipeline"},
            "created_by": "claude-code(frontend_design.synthesize)",
        }
        out, _ = up(rec)
        rid = out.get("id") if isinstance(out, dict) else None
        if rid:
            recorded.append(rid)
    return recorded


# ── 运行留痕: 调 provenance_hook(留痕失败不阻断)──────────────────────────────

def record_run(
    *,
    run_dir: str,
    surface: str,
    branch: str,
    n_failures: int,
    n_comparisons: int,
    decisions_recorded: list[str],
    recorder: Callable[..., Any] | None = None,
) -> str | None:
    """把本次前端审查运行记进统一账本 events.jsonl。返回事件 id 或 None。

    留痕失败绝不阻断 synthesize 主流程: record_tool_run 本身整体 try/except 兜底返 None;
    这里再包一层 try/except, 即便注入的 recorder 直接抛异常也不冒泡(与配表钩子同款语义)。
    """
    from omnicompany.packages.services._core.ledger import provenance_hook

    fn = recorder or provenance_hook.record_tool_run
    try:
        return fn(
            tool_id="frontend_design.pipeline",
            event_type="design.frontend_review_run",
            activity=f"前端审查({branch}) · {surface}",
            queries=("frontend_design", "frostpane", "设计语言"),
            outputs=[provenance_hook.file_ref(run_dir)] if run_dir else [],
            meta={
                "branch": branch,
                "n_failures": n_failures,
                "n_comparisons": n_comparisons,
                "decisions_recorded": decisions_recorded,
            },
        )
    except Exception:
        return None
