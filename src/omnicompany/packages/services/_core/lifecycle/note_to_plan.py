# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-06-25T00:00:00Z type=infra status=active
# [OMNI] summary="note→plan 调研澄清: 读 poof note→统一 run_json_agent 调研+结构化→渲染符合模板的 plan.md 草稿+brief, 歧义标 NEEDS CLARIFICATION 并入 human inbox"
# [OMNI] why="WORK-LIFECYCLE-AND-DISPATCH N-research: note(poof真源) 经调研澄清成可执行 plan"
# [OMNI] tags=lifecycle,note,plan,promote,clarify,unified-agent
# [OMNI] material_id="material:services._core.lifecycle.note_to_plan.py"
"""note→plan 调研澄清回路 (走统一 AgentNodeLoop, 不 fork)。

读 poof note 正文 → run_json_agent 在仓里调研 + 结构化成 plan 骨架 →
渲染成符合 plan 模板的 plan.md + brief.md 草稿 → 歧义用 [NEEDS CLARIFICATION: ...]
标记并(best-effort)入 omni human inbox。产出的草稿要过 omni plan gate 才能投递。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from omnicompany.core.plans_catalogue import _plans_root
from omnicompany.packages.services._core.agent.launch import run_json_agent
from omnicompany.packages.services._core.lifecycle.note_source import read_note_source

_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "short": {"type": "string"},
        "work_type": {"type": "string"},
        "purpose": {"type": "string"},
        "scope": {"type": "string"},
        "out_of_scope": {"type": "string"},
        "exit_criteria": {"type": "array", "items": {"type": "string"}},
        "requirements": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "desc": {"type": "string"}, "acceptance": {"type": "string"}}}},
        "products": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "type": {"type": "string"},
            "path": {"type": "string"}, "done": {"type": "string"}}}},
        "roadmap": {"type": "array", "items": {"type": "object", "properties": {
            "stage": {"type": "string"}, "goal": {"type": "string"}, "products": {"type": "string"}}}},
        "risks": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "desc": {"type": "string"}, "mitigation": {"type": "string"}}}},
        "clarifications": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "exit_criteria", "requirements", "products"],
}

_NODE_PROMPT = """你是 note→plan 调研澄清器。给你一条概念笔记, 你要把它完善成一份可执行的 plan 骨架。

工作方式: **最多读 3-5 个相关文件**快速了解现成设施(复用优先, 别从零造), 然后**立刻输出 JSON**。
不要无限读文件 —— 轮次有限, 读够就停, 必须产出 JSON。

硬要求:
- 复用优先: products 里写明"复用 X / 扩 Y"。
- exit_criteria: 这计划做到什么算完, 可对账。
- requirements: 每条含 id + desc + **acceptance(怎么算满足)**。
- products: 每条含 path + done(完成判定, agent 能查)。
- roadmap: 阶段化, 每阶段 goal + 产物子集。
- 真有想不清的歧义 → 放进 clarifications 列表(一句一个问题, 别用省略号占位)。

最后一轮**必须**用 finish 工具输出完整 JSON, 字段见 schema。"""

_NODE_PROMPT_SIMPLE = """你是 note→plan 结构化器。**不要读任何文件**, 直接把这条概念笔记结构化成 plan 骨架 JSON。
- exit_criteria / requirements(含 acceptance) / products(含 path+done) / roadmap / risks。
- 想不清的放 clarifications(一句一问)。
立刻输出 JSON, 字段见 schema。"""


def _slug(s: str) -> str:
    s = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", s).strip("-").upper()
    return (s or "PROMOTED-PLAN")[:48]


def _render_plan_md(spec: dict[str, Any], *, plan_id: str, date: str, short: str,
                    note_id: str) -> str:
    title = spec.get("title") or "(未命名计划)"
    work_type = spec.get("work_type") or "feature"
    ec = spec.get("exit_criteria") or []
    reqs = spec.get("requirements") or []
    prods = spec.get("products") or []
    roadmap = spec.get("roadmap") or []
    risks = spec.get("risks") or []
    clar = spec.get("clarifications") or []

    fm_ec = "\n".join(f"  - {e}" for e in ec) or "  - (待补)"
    lines: list[str] = []
    lines.append("---")
    lines.append(f"title: 【{short}】{title}")
    lines.append(f"date: '{date}'")
    lines.append(f"work_type: {work_type}")
    lines.append("status: pending-review")
    lines.append("exit_criteria:")
    lines.append(fm_ec)
    lines.append("binding:")
    lines.append("  workspace: src/omnicompany")
    lines.append("  packages: []")
    lines.append("  targets: []")
    lines.append("applicable_standards: []")
    lines.append(f"source_note: 'note:poof-note://{note_id}'")
    lines.append("---")
    lines.append("")
    lines.append(f"<!-- [OMNI] origin=note-promote domain=plans ts={date}T00:00:00Z type=plan status=pending-review -->")
    lines.append(f"<!-- [OMNI] summary=\"{spec.get('purpose','')[:120]}\" -->")
    lines.append(f"<!-- [OMNI] why=\"由 poof note {note_id} 经 omni notes promote 调研澄清生成\" -->")
    lines.append("<!-- [OMNI] tags=plan,promoted,note -->")
    lines.append(f"<!-- [OMNI] material_id=\"material:plans.{_slug(short).lower()}.plan.md\" -->")
    lines.append("")
    lines.append(f"# {title} 计划书 (草稿, 由 note 提升)")
    lines.append("")
    lines.append(f"> **来源 note**: poof-note://{note_id}")
    lines.append(f"> **立计划日**: {date}")
    lines.append(f"> **范围**: {spec.get('scope','(待补)')}")
    lines.append(f"> **不在范围**: {spec.get('out_of_scope','(待补)')}")
    lines.append("")
    if clar:
        lines.append("## 〇 · 待澄清 (必须清零才能过 gate)")
        for c in clar:
            lines.append(f"- [NEEDS CLARIFICATION: {c}]")
        lines.append("")
    lines.append("## 一 · 需求清单")
    lines.append("")
    for i, r in enumerate(reqs, 1):
        rid = r.get("id") or f"R-{i}"
        lines.append(f"{i}. **{rid}**: {r.get('desc','')}. 验收: {r.get('acceptance','(待补)')}")
    lines.append("")
    lines.append("## 二 · 产物清单")
    lines.append("")
    lines.append("| ID | 类型 | 路径(形态) | 完成判定 |")
    lines.append("|---|---|---|---|")
    for i, p in enumerate(prods, 1):
        pid = p.get("id") or f"P-{i}"
        lines.append(f"| {pid} | {p.get('type','代码')} | {p.get('path','(待补)')} | {p.get('done','(待补)')} |")
    lines.append("")
    lines.append("## 三 · 验收标准")
    lines.append("")
    lines.append("### 3.1 静态验收")
    lines.append("- [ ] 全部产物清单文件存在")
    lines.append("### 3.2 动态验收")
    lines.append("- [ ] 入口命令可跑通 (具体见各 task test_strategy)")
    lines.append("")
    lines.append("## 四 · 路线图")
    lines.append("")
    if roadmap:
        for st in roadmap:
            lines.append(f"### {st.get('stage','阶段')}")
            lines.append(f"- {st.get('goal','')}")
            if st.get("products"):
                lines.append(f"- 产物: {st.get('products')}")
    else:
        lines.append("### 阶段 1 · (待 split 细化)")
    lines.append("")
    lines.append("## 五 · 不达标处置")
    lines.append("- 优先级 A 缺失: 阻断; 优先级 B: 走技术债; 存在但有问题: finding 不阻断。")
    lines.append("")
    lines.append("## 六 · 风险与假设")
    for i, rk in enumerate(risks, 1):
        lines.append(f"- **R-{i}**: {rk.get('desc','')}. 缓解: {rk.get('mitigation','')}")
    if not risks:
        lines.append("- (待补)")
    lines.append("")
    lines.append("## 七 · 决策日志")
    lines.append(f"- {date} · 由 note {note_id} 经 omni notes promote 生成草稿 (待用户审 + 澄清)")
    lines.append("")
    return "\n".join(lines)


def _render_brief(spec: dict[str, Any]) -> str:
    ec = spec.get("exit_criteria") or []
    return (
        "# " + (spec.get("title") or "草稿计划") + " · 核心摘要\n\n"
        "## 退出条件\n" + ("\n".join(f"- {e}" for e in ec) or "- (待补)") + "\n\n"
        "## 当前阶段\n- 阶段: 草稿 (note promote 生成, 待澄清 + 过 gate)\n"
        "- 进度: 待用户审路线图 + 清零 NEEDS CLARIFICATION\n\n"
        "## 执行约束\n- 过 omni plan gate 才能 omni plan split / dispatch。\n"
    )


def _push_clarifications(clar: list[str], plan_id: str) -> int:
    """best-effort: 把澄清问题入 omni human inbox。失败不阻断。"""
    if not clar:
        return 0
    try:
        from omnicompany.runtime.buses.human_bus import HumanBus, HumanKind
        bus = HumanBus()
        n = 0
        for c in clar:
            try:
                bus.ask(c, kind=HumanKind.HUMAN_BLOCKING, default="",
                        context={"plan_id": plan_id, "anchor": "note_promote"},
                        source="note_promote")
                n += 1
            except Exception:
                pass
        return n
    except Exception:
        return 0


async def _run_research(note_title: str, note_body: str, model: str | None,
                        *, simple: bool = False) -> dict[str, Any]:
    task = (
        f"概念笔记标题: {note_title}\n\n笔记正文:\n{note_body}\n\n"
        f"把它完善成 plan 骨架 JSON。"
    )
    kwargs: dict[str, Any] = {
        "task": task,
        "node_prompt": _NODE_PROMPT_SIMPLE if simple else _NODE_PROMPT,
        "result_schema": _PLAN_SCHEMA,
        "project_root": str(_plans_root().parent.parent),
        "max_turns": 6 if simple else 12,
        "caller": "note_promote",
    }
    if model:
        kwargs["model"] = model
    return await run_json_agent(**kwargs)


def _skeleton_spec(note_title: str, note_body: str) -> dict[str, Any]:
    """确定性兜底: 调研 agent 失败时, 从 note 直接搭最小 plan 骨架(必带待澄清, 不会过 gate)。"""
    return {
        "title": note_title,
        "short": note_title[:16],
        "work_type": "feature",
        "purpose": (note_body or note_title)[:200],
        "scope": "(待人工补全 — 由 note 自动骨架)",
        "out_of_scope": "(待补)",
        "exit_criteria": [f"实现『{note_title}』并通过验收(待人工细化)"],
        "requirements": [
            {"id": "R-1", "desc": f"实现 {note_title} 的核心诉求(见 note 正文)",
             "acceptance": "(待人工补 — 怎么算满足)"}
        ],
        "products": [
            {"id": "P-1", "type": "代码", "path": "(待人工补路径)", "done": "(待人工补完成判定)"}
        ],
        "roadmap": [{"stage": "阶段 1 · 细化", "goal": "把骨架补成可过 gate 的 plan", "products": "P-1"}],
        "risks": [{"desc": "骨架由 note 自动生成, 调研 agent 未成功细化",
                   "mitigation": "人工审 note 正文逐条补需求/产物/验收"}],
        "clarifications": [
            "这条 note 的核心交付物是什么(具体到可验收)?",
            "涉及/复用哪些现成设施与文件路径?",
        ],
    }


def promote_note_to_plan(note_id: str, *, category: str = "inbox",
                         model: str | None = None, dry: bool = False) -> dict[str, Any]:
    """读 poof note → 调研澄清 → 写 plan.md 草稿。返回 {ok, plan_id, path, clarifications, error}。"""
    src = read_note_source()
    note = src.get_note(note_id)
    if not note:
        return {"ok": False, "error": f"overlay-note-store 里找不到 note: {note_id} "
                f"(omni notes ls 看有哪些; 或 OVERLAY_NOTE_STORE_DIR/旧 POOF_NOTES_DIR 未配)"}
    body = note.body()
    if not body:
        return {"ok": False, "error": f"note {note_id} 没有正文 .md 导出 "
                f"(在 poof 里打开该笔记一次, 或 omni notes refresh 触发导出)"}

    # 三层鲁棒: 调研(读文件,12轮) → 简化(不读文件,6轮) → 确定性骨架(永不失败)
    source = "research"
    result = asyncio.run(_run_research(note.title, body, model))
    if not result.get("ok"):
        result = asyncio.run(_run_research(note.title, body, model, simple=True))
        source = "simple"
    if result.get("ok") and (result.get("final") or {}).get("requirements"):
        spec = result["final"]
    else:
        spec = _skeleton_spec(note.title, body)
        source = "skeleton"
    short = (spec.get("short") or note.title or "PROMOTED")[:24]
    date = datetime.now().strftime("%Y-%m-%d")
    name = _slug(spec.get("title") or note.title)
    plan_id = f"{category}/[{date}]{name}"
    plan_dir = _plans_root() / category / f"[{date}]{name}"
    plan_md = plan_dir / "plan.md"
    clar = spec.get("clarifications") or []

    if dry:
        return {"ok": True, "dry": True, "plan_id": plan_id, "path": str(plan_md),
                "source": source, "clarifications": clar,
                "spec_preview": {k: spec.get(k) for k in
                ("title", "exit_criteria", "requirements") if k in spec}}

    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_md.write_text(_render_plan_md(spec, plan_id=plan_id, date=date, short=short,
                                       note_id=note_id), encoding="utf-8")
    (plan_dir / "brief.md").write_text(_render_brief(spec), encoding="utf-8")
    pushed = _push_clarifications(clar, plan_id)
    return {"ok": True, "plan_id": plan_id, "path": str(plan_md), "source": source,
            "clarifications": clar, "clarifications_pushed_to_inbox": pushed,
            "next": f"omni plan gate '{plan_id}' (清零 NEEDS CLARIFICATION 后才能过)"}


__all__ = ["promote_note_to_plan"]
