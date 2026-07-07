# [OMNI] origin=claude-code domain=services/_core/semantic_fs ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="语义文件系统首个消费场景(里程碑四): 继承 MATERIAL-GAP-MAP 意图, 把 demogame 配表缺失的一手源 material(cross-table-lookup/formula-semantic)从真一手源(xlsm 公式字符串)自动产出→入册→可语义检索。守铁律: 只读公式等真一手源, 禁读同业务 SDK process_*.py。"
# [OMNI] why="证明'自动分类+入册+语义检索'能让某域缺失 material 被填上、被找到(MATERIAL-GAP-MAP 抬覆盖率的意图), 不解归档老 plan, 作消费场景纳入。"
# [OMNI] tags=semantic-os,material,demogame-config,material-gap,consumer
# [OMNI] material_id="material:core.semantic_fs.demogame_gap.py"
"""demogame 配表缺失一手源 material 产出(里程碑四 · MATERIAL-GAP-MAP 消费场景)。

输入只接**真一手源**(xlsm 公式字符串 [{field, formula}]);禁读同业务 SDK process_*.py(那是 benchmark 验证对象)。
产两类 material:
  - demogame.cross-table-lookup: 确定性 parse VLOOKUP 参数 → 字段依赖哪张表/哪列。
  - demogame.formula-semantic  : LLM(qwen)把公式翻成中文语义。
产出落 data/domains/demogame/materials/gap/, 经 M2 materialize 入册(domain.demogame/kind.source), M3 index 可检索。
MI(多源融合+用户必填)走 human-inbox, 不静默拍板。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

# =VLOOKUP(U34, Unit!A:X, 5, FALSE) → 依赖表 Unit, 取第 5 列
_VLOOKUP_RE = re.compile(r"VLOOKUP\s*\([^,]+,\s*([A-Za-z_][\w]*)\s*!?[^,]*,\s*(\d+)", re.I)


def extract_cross_table(formulas: list[dict]) -> list[dict]:
    """确定性: 从公式抽 VLOOKUP 跨表依赖(真一手源 = 公式字符串本身)。"""
    out = []
    for f in formulas:
        for m in _VLOOKUP_RE.finditer(f.get("formula", "")):
            out.append({"field": f.get("field", ""), "depends_table": m.group(1),
                        "column": int(m.group(2)), "formula": f.get("formula", "")})
    return out


def _md_cross_table(deps: list[dict]) -> str:
    lines = ["# demogame 配表 · 跨表依赖 (cross-table-lookup)",
             "", "> 一手源 = xlsm 公式 VLOOKUP 参数(确定性抽取)。供配表 agent 解析跨表依赖。", "",
             "| 字段 | 依赖表 | 取列 | 公式 |", "|---|---|---|---|"]
    for d in deps:
        lines.append(f"| {d['field']} | {d['depends_table']} | {d['column']} | `{d['formula']}` |")
    return "\n".join(lines) + "\n"


def translate_formulas(formulas: list[dict], *, model: str = "qwen3.6-plus") -> list[dict]:
    """LLM 把公式翻中文语义(formula-semantic)。"""
    from omnicompany.runtime.llm.structured import call_json
    items = "\n".join(f"- {f.get('field','')}: {f.get('formula','')}" for f in formulas)
    sys = ("你是 demogame 配表公式语义翻译器。把每条 Excel 公式翻成一句中文业务语义(它在算什么)。"
           "只输出 JSON。")
    schema = {"type": "object", "required": ["items"], "properties": {"items": {"type": "array",
              "items": {"type": "object", "required": ["field", "semantic"],
                        "properties": {"field": {"type": "string"}, "semantic": {"type": "string"}}}}}}
    res = call_json(system=sys, user=f"翻译这些公式:\n{items}", schema=schema, model=model,
                    caller="demogame_gap.formula_semantic", max_tokens=1500, max_corrections=2)
    return (res or {}).get("items", [])


def _md_formula_semantic(items: list[dict], formulas: list[dict]) -> str:
    fmap = {f.get("field", ""): f.get("formula", "") for f in formulas}
    lines = ["# demogame 配表 · 公式语义 (formula-semantic)",
             "", "> 一手源 = xlsm 公式字符串;LLM 翻成中文业务语义。供配表 agent 理解字段算法。", "",
             "| 字段 | 中文语义 | 公式 |", "|---|---|---|"]
    for it in items:
        lines.append(f"| {it.get('field','')} | {it.get('semantic','')} | `{fmap.get(it.get('field',''),'')}` |")
    return "\n".join(lines) + "\n"


def produce(formulas: list[dict], *, model: str = "qwen3.6-plus", root: Path | None = None,
            do_index: bool = True, echo: Any = None) -> dict[str, Any]:
    """从一手源公式产出两类 demogame material → materialize 入册 → index 可检索。"""
    from .classify import materialize
    from . import index as IDX
    from .schema import set_semantic
    base = root or omni_workspace_root()
    out_dir = base / "data" / "domains" / "demogame" / "materials" / "gap"
    out_dir.mkdir(parents=True, exist_ok=True)

    deps = extract_cross_table(formulas)
    sem = translate_formulas(formulas, model=model)
    files = {
        out_dir / "cross_table_lookup.md": _md_cross_table(deps),
        out_dir / "formula_semantic.md": _md_formula_semantic(sem, formulas),
    }
    produced = []
    for p, content in files.items():
        p.write_text(content, encoding="utf-8")
        r = materialize(p, model=model, root=base, push_inbox=False, force=True)
        # 强制盖上 demogame 一手源受控标签(产者已知精确归属, 比泛分类更准)
        if r.get("ok"):
            ttype = "type.cross-table-lookup" if "cross_table" in p.name else "type.formula-semantic"
            set_semantic(r["entity_id"], semantic_tags=["domain.demogame", "kind.source", "stage.produce", ttype],
                         root=base)
            produced.append({"file": p.name, "entity_id": r["entity_id"],
                             "tags": ["domain.demogame", "kind.source", ttype]})
            if echo:
                echo(f"  produced+registered {p.name} -> {r['entity_id']}")
    indexed = None
    if do_index and produced:
        indexed = IDX.build_index(entity_ids=[x["entity_id"] for x in produced], root=base, echo=echo)
    return {"ok": True, "cross_table_deps": len(deps), "formula_semantics": len(sem),
            "produced": produced, "indexed": indexed,
            "rule": "只读公式真一手源, 未读同业务 SDK process_*.py"}
