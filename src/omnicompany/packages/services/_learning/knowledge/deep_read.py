# [OMNI] origin=claude-code domain=services/knowledge/deep_read.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:learning.knowledge.entry_completion_engine.deep_read.py"
"""omnikb.deep_read — 深读模式: 基于已有源码和上下文补完 KB 条目。

核心哲学 (区别于"enrich"的"创作"):
  - **不凭空写**: 所有 section 的内容必须能追溯到 (a) code_anchor 指向的实际源码 /
    (b) KB 现有其他 entry / (c) entry 自身的 seed description / (d) seed 时提到的
    plan 文档路径
  - **事实优先**: 禁止 LLM 用想象填补空白, 凡是上述 4 个来源都没提到的, 必须明说
    "基于当前可见信息无法回答"
  - **增量**: 每成功写完一个 entry 立即落盘, 不等 batch 结束, 允许随时中断恢复
  - **可追溯**: 落盘文件在 change log 段含本次的"信息来源清单" (读了哪几个文件)

实现模块构成:
  - context_builder — 给 LLM 组装高质量上下文 (code + KB refs + plan files)
  - deep_read_one — 单 entry 的深读 + 落盘
  - deep_read_batch — 批量 + 进度持久化 (.omni/kb_deep_read.progress.json)

调用示例:
  # 单条
  python -m omnicompany.packages.services._learning.knowledge.deep_read --id kb.arch.pipeline.omnikb_audit

  # 批量
  python -m omnicompany.packages.services._learning.knowledge.deep_read --batch karch --limit 5
  python -m omnicompany.packages.services._learning.knowledge.deep_read --resume     # 从 progress 继续
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# 入口脚本 load .env — llm.py 的 ModelRegistry 初始化时会读 THE_COMPANY_API_KEY
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[5] / ".env")
except ImportError:
    pass

from omnicompany.packages.services._learning.knowledge.index import load_or_rebuild
from omnicompany.packages.services._learning.knowledge.schema import KnowledgeEntry
from omnicompany.packages.services._learning.knowledge.store import KBStore

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Section 定义: 每个 entry 类型需要填的段 + 严格来源约束
# ═══════════════════════════════════════════════════════════

# (section_title, allowed_sources_hint, what_llm_should_do)
# allowed_sources 告诉 LLM 这段**只能**基于哪些来源, 不在列表里的信息禁止使用
KARCH_SECTIONS = [
    (
        "Why this exists",
        ["seed_description", "plan_docs", "related_experiments"],
        "解释这个东西为什么存在。只能基于 seed 阶段抓到的 description、相关 plan 文档的 README 引文, "
        "以及关联的 KExperiment 条目。不要发明设计动机。",
    ),
    (
        "How it works",
        ["code_snippets"],
        "描述它如何工作。**必须逐条引用代码片段段里出现的 struct/class/function 名**。"
        "只能谈代码里真实存在的东西。如果代码片段不足以解释全貌, 明说 '基于当前代码片段只能看到 X, "
        "完整机制需读 Y 文件'.",
    ),
    (
        "Public surface",
        ["code_snippets"],
        "列出该模块对外暴露的接口: public 类 / 函数 / 管线名 / Router 类 / Format id。"
        "只列代码中真实存在的, 不要杜撰 API。",
    ),
    (
        "Internal structure",
        ["code_snippets", "file_list"],
        "描述内部子模块的划分。基于 Files 段的实际文件清单和你在 code_snippets 里看到的 import 关系。",
    ),
    (
        "Files",
        ["code_anchors"],
        "直接列出 code_anchors 中的文件路径, 每条加 1 句作用描述 (作用描述必须基于 code_snippets 或 seed description)。",
    ),
    (
        "Related",
        ["kb_context"],
        "从 'KB 已有条目' 段中挑选真正相关的 entry id 列出。**禁止引用列表中不存在的 id**。"
        "如果没有任何合适的, 明说 '当前 KB 中无关联条目, 可能需要补写的类型: X/Y'.",
    ),
    (
        "Known limitations",
        ["code_snippets", "seed_description"],
        "基于代码中的 TODO/FIXME/XXX 注释、或 seed description 中明确提到的局限, 以及你读代码时发现的"
        "明显未实现区域。不要假设局限, 只陈述你能看到的。",
    ),
]

KDEC_SECTIONS = [
    ("Drivers", ["seed_description", "plan_docs", "related_experiments"],
     "决策动机。基于 seed description 和相关 plan/experiment 记载。"),
    ("Options considered", ["seed_description", "plan_docs"],
     "考虑过的方案。只能写 seed/plan 里真实提到的, 不要发明 'alternative'."),
    ("Decision", ["seed_description"],
     "最终决定。就是 seed description 里的内容, 必要时补充来源引用。"),
    ("Consequences", ["code_snippets"],
     "正负后果。基于代码或 plan 文档中能观察到的实际影响。"),
    ("Related", ["kb_context"],
     "关联 KArch/KExp, 从 KB 已有条目中选。"),
]

KEXP_SECTIONS = [
    ("Hypothesis", ["seed_description", "plan_docs"],
     "实验假设。基于 plan README 或 seed description。"),
    ("Method", ["plan_docs"],
     "方法。基于 plan 目录中的 md 文件引用, 不要编造。"),
    ("Samples", ["plan_docs"],
     "跑过的样本 (如有)。只能写 plan 文档里记载的。"),
    ("Findings", ["plan_docs", "kb_context"],
     "发现。基于 plan 文档或关联的其他 KB 条目。"),
    ("Followups", ["plan_docs"],
     "后续 TODO, 基于 plan 目录中的后续文件或 seed description 提到的 followup。"),
    ("Related", ["kb_context"],
     "关联条目。"),
]

_SECTIONS_BY_TYPE: dict[str, list[tuple[str, list[str], str]]] = {
    "karch": KARCH_SECTIONS,
    "kdec": KDEC_SECTIONS,
    "kexp": KEXP_SECTIONS,
}


# ═══════════════════════════════════════════════════════════
# Context Builder: 构造给 LLM 的严格事实材料
# ═══════════════════════════════════════════════════════════

@dataclass
class DeepReadContext:
    """一个 entry 的深读上下文, 只含可追溯事实, 不含想象材料。"""

    entry: KnowledgeEntry
    code_snippets: dict[str, str]   # rel_path → line-numbered content
    kb_context: list[dict]          # [{id, type, name, description}]
    plan_docs: dict[str, str]       # plan_file_rel_path → first 2000 chars
    source_manifest: dict[str, Any]

    def total_source_chars(self) -> int:
        return (
            sum(len(v) for v in self.code_snippets.values())
            + sum(len(v) for v in self.plan_docs.values())
            + sum(len(str(x)) for x in self.kb_context)
        )


_ANCHOR_RE = re.compile(r"^(?P<path>[^:]+?)(?::L(?P<start>\d+)(?:-L(?P<end>\d+))?)?$")


def _read_code_anchor(project_root: Path, anchor: str, max_chars: int = 6000) -> str:
    m = _ANCHOR_RE.match(anchor.strip())
    if not m:
        return f"[anchor parse error: {anchor}]"
    rel = m.group("path")
    start_s = m.group("start")
    end_s = m.group("end")

    fp = project_root / rel
    if not fp.exists():
        return f"[file not found: {rel}]"

    try:
        text = fp.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return f"[read error: {e}]"

    lines = text.splitlines()
    if start_s:
        start = max(int(start_s) - 1, 0)
        end = int(end_s) if end_s else (start + 50)
        end = min(end, len(lines))
        segment = lines[start:end]
        numbered = "\n".join(
            f"{i+start+1:5d}\t{line}" for i, line in enumerate(segment)
        )
    else:
        # 无行号: 读前 400 行
        segment = lines[:400]
        numbered = "\n".join(
            f"{i+1:5d}\t{line}" for i, line in enumerate(segment)
        )
    return numbered[:max_chars]


def _load_plan_docs(project_root: Path, plan_paths: list[str], char_budget: int = 6000) -> dict[str, str]:
    """读 plan 目录下的 md 文件, 每份截到 char_budget // N。"""
    if not plan_paths:
        return {}
    per_doc = max(char_budget // len(plan_paths), 800)
    out: dict[str, str] = {}
    for rel in plan_paths:
        fp = project_root / rel
        if not fp.exists():
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out[rel] = text[:per_doc]
    return out


def _kb_context_for(entry: KnowledgeEntry, project_root: Path, limit: int = 25) -> list[dict]:
    """找与 entry 相关的已有 KB entry, 返回 id/type/name/description 简表。

    关联判定:
      - 同 type
      - 共享 tags
      - 已在 entry 的 related_* 字段中声明的
    """
    index = load_or_rebuild(project_root)

    related_ids: set[str] = set()
    for attr in ("related_karchs", "related_decisions", "related_experiments"):
        related_ids.update(getattr(entry, attr, None) or [])

    pool: list[KnowledgeEntry] = []
    seen: set[str] = {entry.id}

    # 1) 显式 related
    for rid in related_ids:
        e = index.get(rid)
        if e and rid not in seen:
            pool.append(e)
            seen.add(rid)

    # 2) 共享 tag
    if entry.tags:
        for tag in entry.tags:
            if tag in ("draft", "stage.draft"):
                continue
            for e in index.all_entries():
                if e.id in seen:
                    continue
                if tag in e.tags:
                    pool.append(e)
                    seen.add(e.id)
                    if len(pool) >= limit:
                        break
            if len(pool) >= limit:
                break

    # 3) 同 type 填补
    if len(pool) < limit:
        for e in index.all_of_type(entry.omnikb_type):
            if e.id in seen:
                continue
            pool.append(e)
            seen.add(e.id)
            if len(pool) >= limit:
                break

    return [
        {
            "id": e.id,
            "type": e.omnikb_type,
            "name": e.name,
            "description": (e.description or "")[:200],
        }
        for e in pool[:limit]
    ]


def build_context(project_root: Path, entry: KnowledgeEntry) -> DeepReadContext:
    """组装一个 entry 的完整深读上下文。"""
    # code anchors
    anchors = getattr(entry, "code_anchors", None) or []
    code_snippets: dict[str, str] = {}
    for a in anchors:
        content = _read_code_anchor(project_root, a)
        code_snippets[a] = content

    # plan docs (从 KExp 的 followups 里拿, 或者 method_summary 中解析 docs/plans/ 路径)
    plan_paths: list[str] = []
    followups = getattr(entry, "followups", None) or []
    for f in followups:
        if isinstance(f, str) and f.startswith("docs/plans/"):
            plan_paths.append(f)
    method = getattr(entry, "method_summary", "") or ""
    m = re.search(r"docs/plans/([^\s]+)/?", method)
    if m:
        plan_dir = project_root / "docs" / "plans" / m.group(1).rstrip("/")
        if plan_dir.is_dir():
            for f in sorted(plan_dir.glob("*.md")):
                rel = str(f.relative_to(project_root)).replace("\\", "/")
                if rel not in plan_paths:
                    plan_paths.append(rel)

    plan_docs = _load_plan_docs(project_root, plan_paths[:6])
    kb_context = _kb_context_for(entry, project_root)

    manifest = {
        "entry_id": entry.id,
        "entry_type": entry.omnikb_type,
        "code_anchors_count": len(anchors),
        "code_anchors_chars": sum(len(v) for v in code_snippets.values()),
        "plan_docs_count": len(plan_docs),
        "plan_docs_chars": sum(len(v) for v in plan_docs.values()),
        "kb_context_count": len(kb_context),
    }

    return DeepReadContext(
        entry=entry,
        code_snippets=code_snippets,
        kb_context=kb_context,
        plan_docs=plan_docs,
        source_manifest=manifest,
    )


# ═══════════════════════════════════════════════════════════
# Prompt
# ═══════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """你在给 OmniCompany 项目的 OmniKB 知识库做深读 (deep read) 补完。

**深读不是创作**。你的任务是: 把已经存在的事实 (代码 / plan 文档 / 其他 KB 条目) 组织成结构化的 KB 条目段落。
凡是找不到事实依据的内容, 必须明说"当前可见材料不足以回答这一段"。不要编造设计意图、不要假设架构决策、
不要虚构 API 名。

## 输出格式 (严格)

输出一份 **纯 Markdown**, 每段用二级标题 `## Section Title` 开头, 按照下面 "待填段规格" 列出的顺序和标题输出。
**不要输出 JSON, 不要用代码块包裹, 不要前置解释**。直接以 `## <第一段标题>` 开始。

示例:

```
## Why this exists

OmniKB 系统承担 ... 的职责。在 `src/foo.py` 的 `FooClass` 实现了 ... 机制。

> _来源: src/foo.py, kb.experiment.retired_knowledge_

## How it works

...
```

## 严格规则

1. 每段 150~400 字散文为主, 关键点用 bullet list 或表格。中文输出。
2. **每段必须标注来源**, 段末加 `> _来源: <sources>_` 一行, 写清楚你引用了哪些文件 / 哪些 KB 条目 / 哪些 plan。
3. **引用代码时必须用反引号**, 必须是代码片段中真实出现的字符串 (class 名 / 函数名 / 路径)。禁止发明 identifier。
4. **引用 KB 条目时必须用 id 形式** (形如 `kb.arch.xxx`), 必须是"KB 已有条目"列表中真实存在的 id。
5. 如果某段完全没有来源可用, 直接写 "当前可见材料 (code/plan/kb context) 不足以回答这一段。补完此段需要阅读: <具体文件或 entry id>。"
6. 禁止 confidence 标签、分数、"非常确信"等主观表达。不确定性用散文表达 ("只看到接口未看到实现" 之类)。
7. 禁止重复 entry 的 description 或 name, 段内直接进内容。
8. **段标题必须与"待填段规格"中的标题完全一致**, 不要改写成 "为什么存在" 之类的翻译。

再次强调: **不要输出 JSON**, 输出纯 Markdown 从 `##` 标题开始。"""


def build_user_prompt(ctx: DeepReadContext) -> str:
    entry = ctx.entry
    type_label = entry.omnikb_type
    section_specs = _SECTIONS_BY_TYPE.get(type_label, [])

    section_lines = []
    for title, sources, hint in section_specs:
        src_str = ", ".join(f"`{s}`" for s in sources)
        section_lines.append(f"### {title}\n允许来源: {src_str}\n任务: {hint}")
    sections_block = "\n\n".join(section_lines)

    code_block = "\n\n".join(
        f"#### `{anchor}`\n```\n{snippet[:5000]}\n```"
        for anchor, snippet in ctx.code_snippets.items()
    ) if ctx.code_snippets else "(无 code_anchors 或读取失败)"

    plan_block = "\n\n".join(
        f"#### `{rel}`\n```\n{text}\n```"
        for rel, text in ctx.plan_docs.items()
    ) if ctx.plan_docs else "(无 plan 文档)"

    kb_block = "\n".join(
        f"- `{c['id']}` ({c['type']}) — {c['name']}: {c['description']}"
        for c in ctx.kb_context
    ) if ctx.kb_context else "(KB 中暂无相关条目)"

    return f"""## 待深读条目

- **id**: `{entry.id}`
- **type**: `{type_label}`
- **name**: {entry.name}
- **maturity**: {entry.maturity}
- **scope**: {getattr(entry, "scope", "(n/a)")}
- **tags**: {entry.tags}
- **seed description**: {entry.description}

## 待填段规格

以下段**必须全部填满** (如果无事实可写, 按系统提示第 6 条处理), 且只能用 "允许来源" 列表中的来源类别:

{sections_block}

## 事实材料

### A. 代码片段 (code_snippets)

{code_block}

### B. Plan 文档 (plan_docs)

{plan_block}

### C. KB 已有相关条目 (kb_context)

{kb_block}

---

**基于以上事实材料, 填写 sections. 不要发明事实, 禁止引用列表以外的 KB id 或代码符号. 返回严格 JSON.**
"""


# ═══════════════════════════════════════════════════════════
# LLM 调用 + 解析
# ═══════════════════════════════════════════════════════════

def _make_client(role: str = "ide_agent"):
    """构造 LLM client. 默认 ide_agent 因为深读需要理解代码 + 散文输出。

    .env 已在模块 import 时 load, THE_COMPANY_API_KEY 已在环境。
    """
    from omnicompany.runtime.llm.llm import LLMClient
    return LLMClient(role=role, max_tokens=8192)


def _parse_markdown_sections(text: str) -> dict[str, str] | None:
    """从 LLM 的纯 markdown 输出中按 `## <title>` 切段, 返回 {title: body}。

    设计选择:
      - LLM 直接输出 markdown 比输出 JSON 可靠得多 (LLM 经常在 JSON string
        里放裸 `"` 或真实换行导致 json.loads 失败)
      - 段标题必须严格以 `## ` 开头, 不依赖任何 JSON schema
      - body 保留原样 (含 `> _来源: ..._` 引用和代码块)
      - 容忍 LLM 偶尔在开头加散文, 第一个 `## ` 之前的内容被丢弃
    """
    # 剥可能的 markdown code fence 包裹 (虽然 prompt 禁止, LLM 偶尔还会加)
    stripped = text.strip()
    if stripped.startswith("```"):
        fence_start = re.match(r"```[a-zA-Z]*\s*\n", stripped)
        if fence_start:
            stripped = stripped[fence_start.end():]
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()

    # 按 ## 开头切段
    # re.split 保留分隔符便于后续组装
    pattern = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(stripped))
    if not matches:
        return None

    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(stripped)
        body = stripped[body_start:body_end].strip()
        if title and body:
            sections[title] = body

    return sections if sections else None


# ═══════════════════════════════════════════════════════════
# Body 渲染
# ═══════════════════════════════════════════════════════════

def render_body(
    entry: KnowledgeEntry,
    sections: dict[str, str],
    source_manifest: dict[str, Any],
    *,
    source_label: str = "LLM deep_read.py",
    promote_to: str = "living",
) -> str:
    """渲染 body. 会 inplace 更新 entry.maturity。

    与之前 enrich.py 的区别:
      - Change log 段记录本次使用的事实来源数量 (来自 source_manifest)
      - 没填的段不输出 fallback 占位, 保留 LLM 的 "无事实可用" 诚实陈述
    """
    if promote_to and promote_to != entry.maturity:
        entry.maturity = promote_to

    type_label = entry.omnikb_type
    section_specs = _SECTIONS_BY_TYPE.get(type_label, [])

    parts = [
        f"# {entry.name}",
        "",
        f"> **id**: `{entry.id}` · **type**: {type_label} · **maturity**: {entry.maturity}",
        "",
    ]

    for title, _sources, _hint in section_specs:
        body = sections.get(title) or sections.get(title.lower())
        parts.append(f"## {title}")
        parts.append("")
        if body:
            parts.append(body.strip())
        else:
            parts.append("_(LLM 未返回此段)_")
        parts.append("")

    parts.append("## Change log")
    parts.append("")
    parts.append(f"- {_today()} — deep-read by {source_label}")
    parts.append(
        f"- source manifest: "
        f"{source_manifest.get('code_anchors_count', 0)} code anchors "
        f"({source_manifest.get('code_anchors_chars', 0)} chars), "
        f"{source_manifest.get('plan_docs_count', 0)} plan docs "
        f"({source_manifest.get('plan_docs_chars', 0)} chars), "
        f"{source_manifest.get('kb_context_count', 0)} kb refs"
    )
    parts.append("")

    return "\n".join(parts)


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


# ═══════════════════════════════════════════════════════════
# 单条 deep read
# ═══════════════════════════════════════════════════════════

@dataclass
class DeepReadResult:
    kb_id: str
    success: bool
    sections_count: int
    body_chars: int
    path: str
    source_manifest: dict[str, Any]
    error: str = ""


def deep_read_one(project_root: Path, kb_id: str, *, dry_run: bool = False) -> DeepReadResult:
    store = KBStore(project_root)
    entry = store.read_entry(kb_id)
    if entry is None:
        return DeepReadResult(kb_id, False, 0, 0, "", {}, "entry not found")

    if entry.omnikb_type not in _SECTIONS_BY_TYPE:
        return DeepReadResult(kb_id, False, 0, 0, "", {},
                              f"type {entry.omnikb_type} not deep-readable")

    # 跳过已 living/stable 的 (除非 dry run)
    if entry.maturity in ("living", "stable") and not dry_run:
        return DeepReadResult(kb_id, False, 0, 0, "", {},
                              f"already {entry.maturity}, skipping (use --force to redo)")

    ctx = build_context(project_root, entry)
    logger.info(
        "[deep_read] %s: context assembled (%d code / %d plan / %d kb refs, total %d chars)",
        kb_id,
        ctx.source_manifest["code_anchors_count"],
        ctx.source_manifest["plan_docs_count"],
        ctx.source_manifest["kb_context_count"],
        ctx.total_source_chars(),
    )

    try:
        client = _make_client()
    except Exception as e:
        return DeepReadResult(kb_id, False, 0, 0, "", ctx.source_manifest,
                              f"LLMClient init failed: {e}")

    user_prompt = build_user_prompt(ctx)
    logger.info("[deep_read] %s: calling LLM (prompt %d chars)", kb_id, len(user_prompt))

    try:
        resp = client.call(
            messages=[{"role": "user", "content": user_prompt}],
            system=_SYSTEM_PROMPT,
        )
        text = resp.content[0].text if resp.content else ""
    except Exception as e:
        return DeepReadResult(kb_id, False, 0, 0, "", ctx.source_manifest,
                              f"LLM call failed: {e}")

    sections = _parse_markdown_sections(text)
    if not sections:
        return DeepReadResult(kb_id, False, 0, 0, "", ctx.source_manifest,
                              f"markdown parse failed; no ## headings found; first 300: {text[:300]}")
    body = render_body(entry, sections, ctx.source_manifest,
                       source_label="LLM deep_read.py", promote_to="living")

    if dry_run:
        return DeepReadResult(kb_id, True, len(sections), len(body),
                              "(dry run)", ctx.source_manifest)

    try:
        path = store.write_entry(entry, body=body, overwrite=True)
    except Exception as e:
        return DeepReadResult(kb_id, False, 0, 0, "", ctx.source_manifest,
                              f"write failed: {e}")

    logger.info("[deep_read] %s: written → %s (%d chars body)", kb_id, path.name, len(body))
    return DeepReadResult(kb_id, True, len(sections), len(body),
                          str(path), ctx.source_manifest)


# ═══════════════════════════════════════════════════════════
# 批量 deep read — 进度持久化 + 随时中断恢复
# ═══════════════════════════════════════════════════════════

def _progress_path(project_root: Path) -> Path:
    # 2026-04-09: 从 .omni/ 迁到 data/knowledge/ (正规 domain drawer)
    # 理由: .omni/ 不在任何 drawer, guarded_write 拒绝写入, 违反 OMNI-013
    # 进度文件属于 services/knowledge 的数据 artifact, 归 data/knowledge/ 符合 OMNI-005
    from omnicompany.core.config import resolve_domain_data_dir
    d = resolve_domain_data_dir("knowledge")
    d.mkdir(parents=True, exist_ok=True)
    return d / "kb_deep_read.progress.json"


def _load_progress(project_root: Path) -> dict:
    p = _progress_path(project_root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_progress(project_root: Path, progress: dict) -> None:
    # 2026-04-09: 从 p.write_text() 改走 guarded_write 修 OMNI-013
    # LLM-039 由 Guardian agent-loop 发现 (patrol.py 升级版)
    from omnicompany.core.guarded_write import write_file

    p = _progress_path(project_root)
    write_file(
        str(p),
        json.dumps(progress, ensure_ascii=False, indent=2),
        origin="internal-engine",
        domain="services/knowledge",
        purpose="save deep_read progress",
    )


def deep_read_batch(
    project_root: Path,
    *,
    type_filter: str | None = None,
    limit: int = 5,
    id_prefix: str | None = None,
    resume: bool = False,
) -> list[DeepReadResult]:
    """批量深读。每完成一条立即落盘, 同时更新 .omni/kb_deep_read.progress.json。

    resume=True 时跳过 progress 里已成功的条目, 从未完成的开始。
    """
    index = load_or_rebuild(project_root)

    candidates: list[KnowledgeEntry]
    if type_filter:
        candidates = index.all_of_type(type_filter)
    else:
        candidates = index.all_entries()
    candidates = [e for e in candidates if e.maturity == "draft"]
    if id_prefix:
        candidates = [e for e in candidates if e.id.startswith(id_prefix)]

    progress = _load_progress(project_root)
    done_ids = {k for k, v in progress.items() if v.get("success")}

    if resume:
        candidates = [e for e in candidates if e.id not in done_ids]
    else:
        # 非 resume 模式也不重复做已 done
        candidates = [e for e in candidates if e.id not in done_ids]

    candidates = candidates[:limit]
    if not candidates:
        logger.info("[deep_read] no candidates (all done or no drafts match filter)")
        return []

    logger.info(
        "[deep_read] batch starting: %d candidates (%d already done)",
        len(candidates), len(done_ids),
    )

    results: list[DeepReadResult] = []
    for i, entry in enumerate(candidates, 1):
        logger.info("[deep_read] [%d/%d] %s", i, len(candidates), entry.id)
        result = deep_read_one(project_root, entry.id)
        results.append(result)

        # 立即持久化进度
        progress[entry.id] = {
            "success": result.success,
            "sections_count": result.sections_count,
            "body_chars": result.body_chars,
            "path": result.path,
            "error": result.error,
            "source_manifest": result.source_manifest,
            "timestamp": _today(),
        }
        _save_progress(project_root, progress)

        status = "OK" if result.success else "FAIL"
        logger.info("[deep_read] [%d/%d] %s: %s", i, len(candidates), status, result.error or "done")

    return results


# ═══════════════════════════════════════════════════════════
# 手动 sections 输入 (golden sample 支持)
# ═══════════════════════════════════════════════════════════

def deep_read_manual(
    project_root: Path,
    kb_id: str,
    sections: dict[str, str],
    *,
    source_label: str = "manual (golden sample)",
) -> Path:
    """手动提供 sections, 走 render_body + 落盘路径。

    用于:
      - 无 LLM 时做 golden samples
      - LLM 输出被人修改后回写
    """
    store = KBStore(project_root)
    entry = store.read_entry(kb_id)
    if entry is None:
        raise SystemExit(f"entry {kb_id} not found")

    ctx = build_context(project_root, entry)
    body = render_body(entry, sections, ctx.source_manifest,
                       source_label=source_label, promote_to="living")
    return store.write_entry(entry, body=body, overwrite=True)


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def _project_root() -> Path:
    return Path(__file__).resolve().parents[5]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deep-read KB draft entries by grounding LLM on existing code + KB + plans"
    )
    parser.add_argument("--id", help="Single entry id to deep-read")
    parser.add_argument("--batch", help="Batch mode entry type: karch | kdec | kexp")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--id-prefix", help="Batch: filter by id prefix")
    parser.add_argument("--resume", action="store_true", help="Skip already-done entries (batch)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sections-file", help="Manual mode: JSON file of {section: body}")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    root = _project_root()

    if args.sections_file:
        if not args.id:
            print("--sections-file 需要配合 --id")
            return 1
        sections_path = Path(args.sections_file)
        if not sections_path.exists():
            print(f"sections file not found: {sections_path}")
            return 1
        sections = json.loads(sections_path.read_text(encoding="utf-8"))
        if not isinstance(sections, dict):
            print("sections file must contain a JSON object")
            return 1
        path = deep_read_manual(root, args.id, sections)
        print(f"OK: manual deep-read {args.id} → {path}")
        return 0

    if args.id:
        result = deep_read_one(root, args.id, dry_run=args.dry_run)
        print(f"\n{json.dumps(asdict(result), ensure_ascii=False, indent=2)}")
        return 0 if result.success else 1

    if args.batch:
        results = deep_read_batch(
            root,
            type_filter=args.batch,
            limit=args.limit,
            id_prefix=args.id_prefix,
            resume=args.resume,
        )
        ok = sum(1 for r in results if r.success)
        print("\n=== Deep Read Batch Summary ===")
        for r in results:
            status = "OK" if r.success else "FAIL"
            print(f"  [{status}] {r.kb_id}: sections={r.sections_count} chars={r.body_chars} {r.error}")
        print(f"\nTotal: {ok}/{len(results)} success")
        return 0 if ok == len(results) else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
