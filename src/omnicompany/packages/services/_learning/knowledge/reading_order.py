# [OMNI] origin=claude-code domain=services/knowledge/reading_order.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:learning.knowledge.semantic_reading_order.planner.py"
"""omnikb.reading_order — Stage 1.5 of context-aware deep_read.

由 Stage 1 的 RelationalFacts 出发, 通过 1 次 LLM call 让 LLM **看完所有 entry 的
事实清单后自己决定** 最优阅读顺序。LLM 的输出是一份 markdown:

  - 一段散文解释 "我为什么按这个顺序"
  - 一份 ordered list, 每一项含 entry id + 1 句理由

落盘:
  .omni/kb_reading_order.md   人和 LLM 都能读, 可手工 override

设计原则 (与 04 计划对齐):
  - 不打分, 不算 PageRank, 不写硬编码的 "in_degree 越大越靠前"
  - 完全把语义判断交给 LLM, 我们只负责把事实材料组织清楚
  - 输出必须人可读, 因为顺序错了的话人需要直接改 markdown

调用:
  python -m omnicompany.packages.services._learning.knowledge.reading_order --rebuild
  python -m omnicompany.packages.services._learning.knowledge.reading_order --filter karch
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# 入口 load .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[5] / ".env")
except ImportError:
    pass

from omnicompany.packages.services._learning.knowledge.index import load_or_rebuild
from omnicompany.packages.services._learning.knowledge.relational_facts import (
    RelationalFacts,
    build_all_facts,
    load_facts,
)

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[5]


# ═══════════════════════════════════════════════════════════
# 把 RelationalFacts 转成 LLM 友好的紧凑文本
# ═══════════════════════════════════════════════════════════

def _facts_to_text(facts: RelationalFacts, entry_description: str) -> str:
    """把一个 entry 的 facts 渲染成 prompt 可读的紧凑段落。

    格式刻意紧凑, 因为一次要把所有 entry 都喂给 LLM。
    """
    lines = [
        f"### `{facts.entry_id}` ({facts.entry_type}, {facts.entry_maturity})",
        f"  name: {facts.entry_name}",
        f"  desc: {entry_description[:180]}",
    ]

    if facts.explicit_references:
        lines.append(f"  explicit refs: {', '.join(facts.explicit_references)}")

    if facts.shared_code_anchors:
        anchor_strs = []
        for a in facts.shared_code_anchors:
            others = a.get("all") or a.get("sample") or []
            count = a.get("count", len(others))
            shown = ", ".join(others[:3])
            if count > len(others):
                anchor_strs.append(f"{a['file']}({count} others: {shown}...)")
            else:
                anchor_strs.append(f"{a['file']}({count} others: {shown})")
        lines.append(f"  shared code anchors: {'; '.join(anchor_strs)}")

    if facts.shared_tags:
        tag_strs = []
        for t in facts.shared_tags:
            others = t.get("all") or t.get("sample") or []
            count = t.get("count", len(others))
            shown = ", ".join(others[:3])
            tag_strs.append(f"{t['tag']}({count}: {shown})")
        lines.append(f"  shared tags: {'; '.join(tag_strs)}")

    if facts.same_domain and not facts.shared_tags:
        # 仅当 shared_tags 没覆盖 same_domain 时才单独列, 避免重复
        lines.append(f"  same_domain: {', '.join(facts.same_domain[:5])}")

    if facts.same_type:
        lines.append(f"  same_type (no other signal): {', '.join(facts.same_type[:5])}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 构造给 LLM 的总 prompt
# ═══════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """你是 OmniCompany OmniKB 知识库的 reading order planner。

你将看到 KB 中所有需要被深读 (deep_read) 补完的 entry 的清单, 每条含:
  - id / type / 当前 maturity
  - name + 一句话 description
  - explicit_references: 它已声明引用的其他 entry id
  - shared_code_anchors: 它和哪些 entry 共享了源代码文件
  - shared_tags: 它和哪些 entry 通过 tag 关联
  - same_layer / same_domain: 它在同 layer/domain 的其他 entry

**你的任务**: 基于这些**结构事实**, 使用语义判断给出一份合理的阅读顺序,
让先读的 entry 的 living 内容能被后读的 entry 引用。

排序原则 (按你的理解, 不要硬套):
1. 被多个 entry 共享的"基础概念" 应该先读 (例如某个 package 是多个 pipeline 的家)
2. 概念依赖明显的, 被依赖方先读 (例如 services_knowledge 应在 omnikb_audit 之前)
3. 完全独立的 entry 顺序无所谓, 放后面
4. 已经是 living 状态的 entry 也要列出来 (因为可能要重读), 但放在它们的依赖之后

## 输出格式

必须输出**纯 markdown** (不要 JSON), 三段:

```
# Reading Order

## Rationale

(2-5 段散文, 解释你按什么逻辑排序了, 为什么把哪些放前面, 有没有看到值得注意的依赖簇)

## Ordered list

1. `kb.id.foo` — 一句话理由
2. `kb.id.bar` — 一句话理由
...

## Notes / observations

(可选: 你发现的循环依赖、孤岛、或者某个 entry 看起来事实材料严重不足的情况)
```

**严格要求**:
- 列出的 id 必须严格来自我提供的清单, 不要发明
- 每个 id 只出现一次
- 顺序覆盖**全部**输入 entry, 不要漏
- 不要使用 confidence 标签 / 数字评分
- 中文输出"""


def build_user_prompt(
    facts_map: dict[str, RelationalFacts],
    descriptions: dict[str, str],
    type_filter: str | None = None,
) -> str:
    """构造 user prompt: 列出所有 entry 的事实文本。"""

    facts_list = list(facts_map.values())
    if type_filter:
        facts_list = [f for f in facts_list if f.entry_type == type_filter]

    facts_list.sort(key=lambda f: f.entry_id)

    blocks = []
    for f in facts_list:
        desc = descriptions.get(f.entry_id, "")
        blocks.append(_facts_to_text(f, desc))

    type_clause = (
        f"我只需要 `{type_filter}` 类型的 entry 的阅读顺序"
        if type_filter
        else "我需要 KB 中**全部** entry 的阅读顺序"
    )

    return f"""{type_clause}, 共 **{len(facts_list)}** 条:

{chr(10).join(blocks)}

---

请按系统提示中的格式输出完整的 reading order markdown."""


# ═══════════════════════════════════════════════════════════
# LLM 调用
# ═══════════════════════════════════════════════════════════

def _make_client():
    from omnicompany.runtime.llm.llm import LLMClient
    return LLMClient(role="ide_agent", max_tokens=8192)


# ═══════════════════════════════════════════════════════════
# 主逻辑
# ═══════════════════════════════════════════════════════════

def compute_reading_order(
    project_root: Path,
    *,
    type_filter: str | None = None,
) -> tuple[str, dict]:
    """跑 1 次 LLM call 让 LLM 排出阅读顺序。

    Returns:
        (markdown_text, source_manifest)
    """
    facts_map = load_facts(project_root)

    # 同时拿 entry 的 description (来自 KBStore)
    index = load_or_rebuild(project_root)
    descriptions: dict[str, str] = {}
    for e in index.all_entries():
        descriptions[e.id] = e.description or ""

    prompt = build_user_prompt(facts_map, descriptions, type_filter=type_filter)

    logger.info(
        "[reading_order] LLM call: prompt %d chars, %d entries",
        len(prompt),
        sum(1 for f in facts_map.values()
            if not type_filter or f.entry_type == type_filter),
    )

    client = _make_client()
    resp = client.call(
        messages=[{"role": "user", "content": prompt}],
        system=_SYSTEM_PROMPT,
    )
    text = resp.content[0].text if resp.content else ""

    manifest = {
        "type_filter": type_filter,
        "entry_count": sum(
            1 for f in facts_map.values()
            if not type_filter or f.entry_type == type_filter
        ),
        "prompt_chars": len(prompt),
        "response_chars": len(text),
    }
    return text, manifest


def save_reading_order(
    project_root: Path,
    markdown: str,
    manifest: dict,
) -> Path:
    """落盘到 .omni/kb_reading_order.md"""
    out = project_root / ".omni" / "kb_reading_order.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    header = (
        f"<!-- Generated by reading_order.py at {_now()} -->\n"
        f"<!-- entries: {manifest['entry_count']} | "
        f"prompt: {manifest['prompt_chars']} chars | "
        f"response: {manifest['response_chars']} chars -->\n\n"
    )
    out.write_text(header + markdown, encoding="utf-8")
    return out


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════
# 解析回订单 (供 Stage 2 使用)
# ═══════════════════════════════════════════════════════════

def parse_ordered_ids(reading_order_md: str) -> list[str]:
    r"""从 reading_order.md 中提取有序 id 列表。

    匹配 ``1. `kb.foo.bar` — 理由`` 这种 markdown 列表项。
    """
    import re
    ids: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(r"^\s*\d+\.\s+`(kb\.[^`]+)`")
    for line in reading_order_md.splitlines():
        m = pattern.match(line)
        if m:
            kid = m.group(1)
            if kid not in seen:
                ids.append(kid)
                seen.add(kid)
    return ids


def load_ordered_ids(project_root: Path) -> list[str]:
    """从落盘的 reading_order.md 加载有序 id 列表。"""
    p = project_root / ".omni" / "kb_reading_order.md"
    if not p.exists():
        return []
    return parse_ordered_ids(p.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 1.5: 1 LLM call to decide entry reading order"
    )
    parser.add_argument("--rebuild", action="store_true",
                        help="Force re-call LLM to recompute order")
    parser.add_argument("--filter", help="Filter to entry type: karch | kdec | kexp")
    parser.add_argument("--show", action="store_true",
                        help="Just show the saved reading_order.md")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    root = _project_root()

    if args.show:
        p = root / ".omni" / "kb_reading_order.md"
        if p.exists():
            print(p.read_text(encoding="utf-8"))
            return 0
        print("no reading_order.md saved yet")
        return 1

    out_path = root / ".omni" / "kb_reading_order.md"
    if out_path.exists() and not args.rebuild:
        print(f"reading_order.md exists at {out_path}")
        print("  use --rebuild to regenerate, or --show to print")
        return 0

    # 确保 facts 是新的
    if not (root / ".omni" / "kb_relational_facts.json").exists():
        logger.info("[reading_order] facts file missing, building...")
        facts_map = build_all_facts(root)
        from omnicompany.packages.services._learning.knowledge.relational_facts import save_facts
        save_facts(root, facts_map)

    markdown, manifest = compute_reading_order(root, type_filter=args.filter)
    saved = save_reading_order(root, markdown, manifest)
    print(f"\nsaved → {saved}")
    print(f"manifest: {json.dumps(manifest, ensure_ascii=False)}")

    # 显示前 30 行让用户能立即看到 LLM 输出
    lines = markdown.splitlines()
    print("\n--- first 30 lines ---")
    print("\n".join(lines[:30]))
    if len(lines) > 30:
        print(f"\n... ({len(lines) - 30} more lines, see {saved})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
