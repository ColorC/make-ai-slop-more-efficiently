# [OMNI] origin=claude-code domain=decisions ts=2026-07-10T00:00:00Z type=module status=active
# [OMNI] summary="30-知识投影:统一决策库事实性陈述(belief)→按域摘要页(docs/ontology/30-知识.md)。纯生成投影带Source注释,可随时重建,不是真源。"
# [OMNI] why="决策本体合并清单#1+前端清点#1:猜想主题摘要保留为生成投影;真源=records.jsonl;手册 INDEX 预留的 30-知识 部分由此渲染。"
# [OMNI] tags=decisions,knowledge,projection,belief,decision-ontology
"""30-知识 投影 —— 事实性陈述(belief)的按域摘要。

渲染规则(确定性,零 LLM):
  - 输入=library.active_records() 里全部 kind=belief;
  - 按域分组:tags 里的 domain:<x> 优先,否则 project,否则「未归域」;
  - 每域一节:状态分布 + 逐条(id/陈述/状态/风险/挑战数);
  - 头部带生成戳与 Source 注释——此文件是投影,手改会被下次重渲覆盖。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import library

_OMNI_ROOT = Path(__file__).resolve().parents[5]
KNOWLEDGE_PROJECTION_PATH = _OMNI_ROOT / "docs" / "ontology" / "30-知识.md"

_STATUS_MARK = {
    "untested": "·", "searching": "?", "challenged": "⚡",
    "supported": "✓", "partial": "±", "falsified": "✗",
}


def _domain_of(rec: dict) -> str:
    for t in rec.get("tags") or []:
        if isinstance(t, str) and t.startswith("domain:"):
            return t.split(":", 1)[1] or "未归域"
    return rec.get("project") or "未归域"


def render_knowledge_projection(out_path: Path | None = None) -> Path:
    """把库内全部 belief 渲染成 30-知识.md,返回落盘路径。"""
    out = out_path or KNOWLEDGE_PROJECTION_PATH
    beliefs = [r for r in library.active_records() if r.get("kind") == "belief"]

    groups: dict[str, list[dict]] = {}
    for r in beliefs:
        groups.setdefault(_domain_of(r), []).append(r)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "<!-- [OMNI] origin=ai-ide domain=decisions type=ontology-projection status=active -->",
        "<!-- Source: data/domains/decisions/library/records.jsonl (kind=belief) -->",
        f"<!-- 生成投影,勿手改;重渲: omni decisions knowledge (生成于 {ts}) -->",
        "",
        "# 30 · 知识(事实性陈述域摘要)",
        "",
        "事实性陈述(belief=可证伪猜想)的按域现状。真源=统一决策库;本页是可重建投影。",
        f"全库 belief {len(beliefs)} 条,{len(groups)} 个域。生命周期:untested→challenged→supported|partial|falsified;",
        "未挑战过的最高只到 untested/searching(反证优先,见 20-探索通则)。",
        "",
    ]

    for domain in sorted(groups, key=lambda d: (-len(groups[d]), d)):
        recs = sorted(groups[domain], key=lambda r: r.get("created_at") or "")
        by_status: dict[str, int] = {}
        for r in recs:
            s = r.get("status") or "untested"
            by_status[s] = by_status.get(s, 0) + 1
        dist = " · ".join(f"{k} {v}" for k, v in sorted(by_status.items(), key=lambda kv: -kv[1]))
        lines.append(f"## {domain}({len(recs)} 条:{dist})")
        lines.append("")
        for r in recs:
            mark = _STATUS_MARK.get(r.get("status") or "untested", "·")
            risk = r.get("risk_if_wrong") or "?"
            n_ch = len(r.get("challenge_log") or [])
            extra = f" ⚡×{n_ch}" if n_ch else ""
            lines.append(f"- [{mark}] `{r.get('id')}` {r.get('statement', '')}"
                         f"(风险={risk}{extra})")
        lines.append("")

    if not groups:
        lines.append("(库内暂无 belief)")
        lines.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
