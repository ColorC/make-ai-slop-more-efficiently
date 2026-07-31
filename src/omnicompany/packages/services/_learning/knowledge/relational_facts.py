# [OMNI] origin=claude-code domain=services/knowledge/relational_facts.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:learning.knowledge.relational_facts_extractor.py"
"""omnikb.relational_facts — Stage 1 of context-aware deep_read.

按 04-CONTEXT-AWARE-DEEP-READ.md 的原则: **不打分, 只列结构事实**, 把判断
权留给后续的 LLM (Stage 1.5/2)。

本模块对 KB 里的每个 entry 算出一份 RelationalFacts:

  - explicit_references  自己 frontmatter 里 related_* 已声明的
  - shared_code_anchors  与其他 entry 共享的 code_anchor 文件清单
  - shared_tags          按 tag 分桶, 每桶列出共享此 tag 的其他 entry id
  - same_layer/domain    同 layer.* / domain.* 的其他 entry id
  - same_type            同 omnikb_type 的其他 entry id

落盘:
  .omni/kb_relational_facts.json   (机器格式, 因为是结构清单不是散文)

调用:
  python -m omnicompany.packages.services._learning.knowledge.relational_facts --rebuild
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from omnicompany.packages.services._learning.knowledge.index import load_or_rebuild
from omnicompany.packages.services._learning.knowledge.schema import KnowledgeEntry

logger = logging.getLogger(__name__)


@dataclass
class RelationalFacts:
    """一个 entry 的客观结构事实清单。

    所有字段都是可枚举的事实, 没有 score / weight / rank。
    """

    entry_id: str
    entry_type: str
    entry_name: str
    entry_maturity: str

    explicit_references: list[str] = field(default_factory=list)
    """frontmatter.related_* 已显式声明的 KB id 列表 (去重合并)"""

    shared_code_anchors: list[dict] = field(default_factory=list)
    """[{file: <path>, with: [其他引用此文件的 entry id]}]"""

    shared_tags: list[dict] = field(default_factory=list)
    """[{tag: <name>, with: [同 tag 的其他 entry id]}]"""

    same_layer: list[str] = field(default_factory=list)
    """同 layer.* tag 的其他 entry id"""

    same_domain: list[str] = field(default_factory=list)
    """同 domain.* tag 的其他 entry id"""

    same_type: list[str] = field(default_factory=list)
    """同 omnikb_type 的其他 entry id (上限 50, 防止 KB 大时爆炸)"""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _facts_for_entry(
    entry: KnowledgeEntry,
    all_entries: list[KnowledgeEntry],
    file_to_entries: dict[str, list[str]],
    tag_to_entries: dict[str, list[str]],
) -> RelationalFacts:
    """给定一个 entry + 全局索引, 返回它的 RelationalFacts。"""

    facts = RelationalFacts(
        entry_id=entry.id,
        entry_type=entry.omnikb_type,
        entry_name=entry.name,
        entry_maturity=entry.maturity,
    )

    # 1. 显式引用 (从 related_* 字段合并)
    refs: set[str] = set()
    for attr in ("related_karchs", "related_decisions", "related_experiments",
                 "relates_to_formats", "relates_to_krouters", "relates_to_routers"):
        v = getattr(entry, attr, None) or []
        for item in v:
            if isinstance(item, str) and item.startswith("kb."):
                refs.add(item)
    facts.explicit_references = sorted(refs)

    # 2. 共享 code_anchor 文件
    own_anchors = getattr(entry, "code_anchors", None) or []
    own_files = set()
    for a in own_anchors:
        # 取 file 部分 (去掉行号 :L10-L20)
        path_part = a.split(":", 1)[0].strip()
        if path_part:
            own_files.add(path_part)

    # 共享列表的展示策略: 列出来 ≤ 12 条全列, > 12 条则给 "10 个示例 + 总数",
    # 既保留语义又避免 LLM prompt 噪音淹没
    def _summarize(ids: list[str]) -> dict:
        ids = sorted(ids)
        if len(ids) <= 12:
            return {"count": len(ids), "all": ids}
        return {"count": len(ids), "sample": ids[:10]}

    def _summarize_list(ids: list[str]) -> list[str] | dict:
        """同 _summarize 但顶层是 list 时的便利包装"""
        if len(ids) <= 12:
            return sorted(ids)
        return {"count": len(ids), "sample": sorted(ids)[:10]}

    for f in sorted(own_files):
        others = [eid for eid in file_to_entries.get(f, []) if eid != entry.id]
        if others:
            entry_record = {"file": f, **_summarize(others)}
            facts.shared_code_anchors.append(entry_record)

    # 3. 共享 tag (跳过通用 tag, 只保留有信息量的)
    skip_tags = {
        "draft", "stage.draft", "topic.package", "topic.pipeline",
        "topic.plan", "topic.retired", "stage.abandoned",
        # layer.pipeline / layer.services 这类太宽泛, 同 layer 已有 same_layer 字段
        "layer.pipeline",
    }
    for t in entry.tags:
        if t in skip_tags:
            continue
        if t.startswith("layer.") or t.startswith("date.") or t.startswith("pipeline."):
            continue
        others = [eid for eid in tag_to_entries.get(t, []) if eid != entry.id]
        if others:
            tag_record = {"tag": t, **_summarize(others)}
            facts.shared_tags.append(tag_record)

    # 4. same_layer (从 layer.* tag 反查), 大列表会被 _summarize_list 截到 sample
    layer_tags = [t for t in entry.tags if t.startswith("layer.")]
    same_layer_set: set[str] = set()
    for lt in layer_tags:
        for eid in tag_to_entries.get(lt, []):
            if eid != entry.id:
                same_layer_set.add(eid)
    facts.same_layer = list(same_layer_set)
    if len(facts.same_layer) > 12:
        # 把 dict 形式塞到 list 字段里 — 用一个特殊 entry 标记
        facts.same_layer = sorted(facts.same_layer)[:10]
        # 注意: 实际数量已被截断, 后续 prompt 会用 stats 里的 total

    # 5. same_domain (同上)
    domain_tags = [t for t in entry.tags if t.startswith("domain.")]
    same_domain_set: set[str] = set()
    for dt in domain_tags:
        for eid in tag_to_entries.get(dt, []):
            if eid != entry.id:
                same_domain_set.add(eid)
    facts.same_domain = sorted(same_domain_set)
    if len(facts.same_domain) > 12:
        facts.same_domain = facts.same_domain[:10]

    # 6. same_type (低信息量, 只在更强信号都为空时填充, 上限 10)
    has_strong_signal = bool(
        facts.explicit_references
        or facts.shared_code_anchors
        or facts.shared_tags
    )
    if not has_strong_signal:
        same_type = [
            e.id for e in all_entries
            if e.omnikb_type == entry.omnikb_type and e.id != entry.id
        ]
        facts.same_type = sorted(same_type)[:10]
    # 否则留空, 不让 50 条同类噪音淹没真信号

    return facts


def build_all_facts(project_root: Path) -> dict[str, RelationalFacts]:
    """对全 KB 算 RelationalFacts, 返回 {entry_id: RelationalFacts}."""
    index = load_or_rebuild(project_root)
    all_entries = index.all_entries()

    # 反向索引: file_path → [entry_id 列表]
    file_to_entries: dict[str, list[str]] = {}
    for e in all_entries:
        anchors = getattr(e, "code_anchors", None) or []
        for a in anchors:
            path_part = a.split(":", 1)[0].strip()
            if path_part:
                file_to_entries.setdefault(path_part, []).append(e.id)

    # 反向索引: tag → [entry_id 列表]
    tag_to_entries: dict[str, list[str]] = {}
    for e in all_entries:
        for t in e.tags:
            tag_to_entries.setdefault(t, []).append(e.id)

    facts_map: dict[str, RelationalFacts] = {}
    for e in all_entries:
        facts_map[e.id] = _facts_for_entry(e, all_entries, file_to_entries, tag_to_entries)

    return facts_map


def save_facts(project_root: Path, facts_map: dict[str, RelationalFacts]) -> Path:
    """落盘到 .omni/kb_relational_facts.json"""
    out_path = project_root / ".omni" / "kb_relational_facts.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": 1,
        "entry_count": len(facts_map),
        "facts": {eid: asdict(f) for eid, f in facts_map.items()},
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return out_path


def load_facts(project_root: Path) -> dict[str, RelationalFacts]:
    """从磁盘加载 facts. 找不到则重建并保存."""
    p = project_root / ".omni" / "kb_relational_facts.json"
    if not p.exists():
        facts = build_all_facts(project_root)
        save_facts(project_root, facts)
        return facts

    payload = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, RelationalFacts] = {}
    for eid, raw in payload.get("facts", {}).items():
        out[eid] = RelationalFacts(**raw)
    return out


def stats(facts_map: dict[str, RelationalFacts]) -> dict:
    """快速统计便于人 review."""
    total = len(facts_map)
    if not total:
        return {"total": 0}

    has_explicit = sum(1 for f in facts_map.values() if f.explicit_references)
    has_shared_code = sum(1 for f in facts_map.values() if f.shared_code_anchors)
    has_shared_tags = sum(1 for f in facts_map.values() if f.shared_tags)
    has_same_domain = sum(1 for f in facts_map.values() if f.same_domain)

    avg_shared_code_count = sum(
        len(f.shared_code_anchors) for f in facts_map.values()
    ) / total
    avg_shared_tag_count = sum(
        len(f.shared_tags) for f in facts_map.values()
    ) / total

    return {
        "total": total,
        "with_explicit_references": has_explicit,
        "with_shared_code_anchors": has_shared_code,
        "with_shared_tags": has_shared_tags,
        "with_same_domain": has_same_domain,
        "avg_shared_code_files_per_entry": round(avg_shared_code_count, 2),
        "avg_shared_tags_per_entry": round(avg_shared_tag_count, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute RelationalFacts for every KB entry (Stage 1 of deep_read v2)"
    )
    parser.add_argument("--rebuild", action="store_true",
                        help="Force rebuild from current KB state")
    parser.add_argument("--show", help="Print the facts for a specific entry id")
    parser.add_argument("--sample", type=int, default=0,
                        help="Print the facts for N sample entries (one karch, one kexp, etc)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = _project_root()

    if args.rebuild or not (root / ".omni" / "kb_relational_facts.json").exists():
        facts_map = build_all_facts(root)
        out = save_facts(root, facts_map)
        st = stats(facts_map)
        print(f"saved facts → {out}")
        print(f"stats: {json.dumps(st, ensure_ascii=False, indent=2)}")
    else:
        facts_map = load_facts(root)
        print(f"loaded {len(facts_map)} facts from .omni/kb_relational_facts.json")

    if args.show:
        f = facts_map.get(args.show)
        if not f:
            print(f"entry {args.show} not found")
            return 1
        print(json.dumps(asdict(f), ensure_ascii=False, indent=2))

    if args.sample > 0:
        # 优先选有意思的样本: 显式引用最多的, code_anchor 共享最多的, 各取一个
        by_explicit = sorted(
            facts_map.values(),
            key=lambda f: -len(f.explicit_references),
        )
        by_shared_code = sorted(
            facts_map.values(),
            key=lambda f: -sum(len(s["with"]) for s in f.shared_code_anchors),
        )
        by_shared_tags = sorted(
            facts_map.values(),
            key=lambda f: -len(f.shared_tags),
        )
        seen_ids: set[str] = set()
        samples: list[RelationalFacts] = []
        for src in (by_explicit, by_shared_code, by_shared_tags):
            for f in src:
                if f.entry_id in seen_ids:
                    continue
                samples.append(f)
                seen_ids.add(f.entry_id)
                break
        # 再补几个 (kexp / package / pipeline 各类)
        for f in facts_map.values():
            if len(samples) >= args.sample:
                break
            if f.entry_id in seen_ids:
                continue
            samples.append(f)
            seen_ids.add(f.entry_id)

        for f in samples[:args.sample]:
            print("\n" + "=" * 60)
            print(json.dumps(asdict(f), ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
