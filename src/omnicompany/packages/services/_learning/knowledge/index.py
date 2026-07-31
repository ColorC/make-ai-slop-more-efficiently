# [OMNI] origin=claude-code domain=services/knowledge/index.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:learning.knowledge.memory_index.query_engine.py"
"""omnikb.index — 内存索引 + 查询 + 索引持久化。

合并自 graveyard 的 search.py + manager.py 的查询部分。
提供:
  - KBIndex.from_store(store)  全量扫描 + 建索引
  - query by id / by tag / by domain / by scope / by type
  - rebuild_index(project_root)  重建 + 落盘 .omni/knowledge_index.json
  - validate(project_root)       引用完整性校验
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from omnicompany.packages.services._learning.knowledge.schema import (
    KArchitectureEntry,
    KDecisionEntry,
    KExperimentEntry,
    KFormatEntry,
    KRepoArchitectEntry,
    KRouterEntry,
    KnowledgeEntry,
    OMNIKB_TYPES,
)
from omnicompany.packages.services._learning.knowledge.store import KBStore

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# ValidationIssue
# ═══════════════════════════════════════════════════════════

@dataclass
class ValidationIssue:
    entry_id: str
    field: str
    message: str
    severity: str = "warning"  # error | warning | info

    def __repr__(self) -> str:
        return f"[{self.severity.upper()}] {self.entry_id}.{self.field}: {self.message}"


# ═══════════════════════════════════════════════════════════
# KBIndex
# ═══════════════════════════════════════════════════════════

class KBIndex:
    """内存索引, 支持多维度查询。

    6 种 entry 的索引全在一个对象里, 按 type 分桶。
    """

    def __init__(self) -> None:
        # 按 type 分桶, key = kb_id
        self._by_type: dict[str, dict[str, KnowledgeEntry]] = {
            t: {} for t in OMNIKB_TYPES
        }

        # 快速查询索引
        self._tag_to_ids: dict[str, list[str]] = {}
        self._domain_to_ids: dict[str, list[str]] = {}
        self._scope_to_ids: dict[str, list[str]] = {}  # 只对有 scope 字段的类型生效

        # KFormat bridge: 可执行 Format id → [kformat ids]
        self._format_bridge: dict[str, list[str]] = {}

    # ── 构建 ──────────────────────────────────────────────

    @classmethod
    def from_store(cls, store: KBStore) -> "KBIndex":
        idx = cls()
        for entry in store.iter_all_entries():
            idx._add(entry)
        return idx

    @classmethod
    def from_index_file(cls, index_path: Path) -> "KBIndex":
        """从 .omni/knowledge_index.json 快速加载。"""
        idx = cls()
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("[omnikb.index] cannot load %s: %s", index_path, e)
            return idx

        for t in OMNIKB_TYPES:
            from omnicompany.packages.services._learning.knowledge.schema import entry_class_for
            cls_for = entry_class_for(t)
            if cls_for is None:
                continue
            for item in data.get(t, []):
                try:
                    entry = cls_for(**item)
                    idx._add(entry)
                except Exception as e:
                    logger.debug("skip bad index entry %s: %s", t, e)
        return idx

    def _add(self, entry: KnowledgeEntry) -> None:
        bucket = self._by_type.get(entry.omnikb_type)
        if bucket is None:
            return
        bucket[entry.id] = entry

        for tag in entry.tags:
            self._tag_to_ids.setdefault(tag, []).append(entry.id)
            if tag.startswith("domain."):
                self._domain_to_ids.setdefault(tag[len("domain."):], []).append(entry.id)

        scope = getattr(entry, "scope", None)
        if scope:
            self._scope_to_ids.setdefault(scope, []).append(entry.id)

        # KFormat bridge
        if isinstance(entry, KFormatEntry):
            for fmt_id in entry.relates_to_formats:
                self._format_bridge.setdefault(fmt_id, []).append(entry.id)

    # ── 查询 ──────────────────────────────────────────────

    def get(self, kb_id: str) -> KnowledgeEntry | None:
        """按 id 查一个条目, 不关心类型。"""
        for bucket in self._by_type.values():
            if kb_id in bucket:
                return bucket[kb_id]
        return None

    def all_of_type(self, omnikb_type: str) -> list[KnowledgeEntry]:
        bucket = self._by_type.get(omnikb_type, {})
        return list(bucket.values())

    def all_kformats(self) -> list[KFormatEntry]:
        return self.all_of_type("kformat")  # type: ignore[return-value]

    def all_krouters(self) -> list[KRouterEntry]:
        return self.all_of_type("krouter")  # type: ignore[return-value]

    def all_karchs(self) -> list[KArchitectureEntry]:
        return self.all_of_type("karch")  # type: ignore[return-value]

    def all_kdecs(self) -> list[KDecisionEntry]:
        return self.all_of_type("kdec")  # type: ignore[return-value]

    def all_kexps(self) -> list[KExperimentEntry]:
        return self.all_of_type("kexp")  # type: ignore[return-value]

    def all_krepos(self) -> list[KRepoArchitectEntry]:
        return self.all_of_type("krepo")  # type: ignore[return-value]

    def all_entries(self) -> list[KnowledgeEntry]:
        result: list[KnowledgeEntry] = []
        for bucket in self._by_type.values():
            result.extend(bucket.values())
        return result

    def find(
        self,
        *,
        types: Iterable[str] | None = None,
        tags: Iterable[str] | None = None,
        domain: str | None = None,
        scope: str | None = None,
        maturity: str | None = None,
        id_prefix: str | None = None,
    ) -> list[KnowledgeEntry]:
        """组合条件查询。所有非 None 的条件用 AND 逻辑。"""
        candidates: list[KnowledgeEntry] = []
        if types:
            for t in types:
                candidates.extend(self.all_of_type(t))
        else:
            candidates = self.all_entries()

        if tags:
            tag_set = set(tags)
            candidates = [e for e in candidates if tag_set.issubset(set(e.tags))]

        if domain:
            candidates = [e for e in candidates if f"domain.{domain}" in e.tags]

        if scope:
            candidates = [
                e for e in candidates
                if getattr(e, "scope", "") == scope
            ]

        if maturity:
            candidates = [e for e in candidates if e.maturity == maturity]

        if id_prefix:
            candidates = [e for e in candidates if e.id.startswith(id_prefix)]

        return candidates

    def find_bridge(self, format_id: str) -> list[KFormatEntry]:
        """通过可执行 Format id 找到关联的 KFormat 条目。"""
        ids = self._format_bridge.get(format_id, [])
        kfs = self._by_type["kformat"]
        return [kfs[i] for i in ids if i in kfs]

    # ── 文本搜索 (轻量) ───────────────────────────────────

    def text_search(self, query: str, *, limit: int = 20) -> list[KnowledgeEntry]:
        """对 name / description / tags 做简单子串匹配, 忽略大小写。

        第一版不做 BM25/embed, 够用即可。后续 KBLocateRouter 可以在此基础上做
        query 扩展 (tokenize + 同义词)。
        """
        q = query.lower().strip()
        if not q:
            return []
        tokens = [t for t in q.split() if t]

        scored: list[tuple[int, KnowledgeEntry]] = []
        for entry in self.all_entries():
            haystack = " ".join([
                entry.id, entry.name, entry.description,
                " ".join(entry.tags),
            ]).lower()
            score = sum(haystack.count(tok) for tok in tokens)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:limit]]

    # ── 统计 & 持久化 ──────────────────────────────────────

    def stats(self) -> dict[str, int]:
        stats: dict[str, int] = {t: len(self._by_type[t]) for t in OMNIKB_TYPES}
        stats["total"] = sum(stats.values())
        stats["format_bridges"] = sum(len(v) for v in self._format_bridge.values())
        return stats

    def to_dict(self) -> dict:
        """序列化为可落 json 的 dict。"""
        out: dict = {}
        for t, bucket in self._by_type.items():
            out[t] = [e.model_dump() for e in bucket.values()]
        return out


# ═══════════════════════════════════════════════════════════
# 管理函数
# ═══════════════════════════════════════════════════════════

def rebuild_index(project_root: Path) -> KBIndex:
    """全量扫描 + 重建内存索引 + 落盘 .omni/knowledge_index.json"""
    store = KBStore(project_root)
    index = KBIndex.from_store(store)

    # 2026-04-09: 从 .omni/ 迁到 data/knowledge/ (正规 drawer) + 走 guarded_write 修 OMNI-013
    # LLM-040 由 Guardian agent-loop 发现
    from omnicompany.core.config import resolve_domain_data_dir
    from omnicompany.core.guarded_write import write_file

    cache_dir = resolve_domain_data_dir("knowledge")
    cache_dir.mkdir(parents=True, exist_ok=True)
    index_path = cache_dir / "knowledge_index.json"

    write_file(
        str(index_path),
        json.dumps(index.to_dict(), ensure_ascii=False, indent=2),
        origin="internal-engine",
        domain="services/knowledge",
        purpose="rebuild kb index cache",
    )

    stats = index.stats()
    logger.info("[omnikb.index] rebuilt: total=%d (%s) bridges=%d",
                stats["total"],
                " ".join(f"{t}={stats[t]}" for t in OMNIKB_TYPES),
                stats["format_bridges"])
    return index


def load_or_rebuild(project_root: Path) -> KBIndex:
    """优先从缓存加载, 缓存缺失时重建。"""
    # 2026-04-09: 从 .omni/ 迁到 data/knowledge/ (配合 rebuild_index 落盘路径)
    from omnicompany.core.config import resolve_domain_data_dir
    index_path = resolve_domain_data_dir("knowledge") / "knowledge_index.json"
    if index_path.exists():
        return KBIndex.from_index_file(index_path)
    return rebuild_index(project_root)


def validate(project_root: Path) -> list[ValidationIssue]:
    """校验所有 entry 的引用完整性。

    检查项:
      1. id 非空 + 唯一 (全类型内)
      2. KRouter.kformat_in/out 如果以 'kb.' 开头必须指向已存在的 KFormat
      3. KFormat.relates_to_krouters 必须指向已存在的 KRouter
      4. KArch.related_decisions / related_experiments / related_karchs 必须指向存在
      5. KDec.supersedes / superseded_by 必须指向 kdec
      6. deprecated 条目不应再被引用
    """
    store = KBStore(project_root)
    index = KBIndex.from_store(store)
    issues: list[ValidationIssue] = []

    all_ids: dict[str, list[KnowledgeEntry]] = {}
    for entry in index.all_entries():
        all_ids.setdefault(entry.id, []).append(entry)
        if not entry.id:
            issues.append(ValidationIssue(
                entry_id="<empty>",
                field="id",
                message=f"empty id in {entry.source_path}",
                severity="error",
            ))

    # 1b: 重复 id
    for kid, entries in all_ids.items():
        if kid and len(entries) > 1:
            paths = ", ".join(e.source_path for e in entries)
            issues.append(ValidationIssue(
                entry_id=kid,
                field="id",
                message=f"duplicate id across: {paths}",
                severity="error",
            ))

    kformat_ids = {e.id for e in index.all_kformats()}
    krouter_ids = {e.id for e in index.all_krouters()}
    karch_ids = {e.id for e in index.all_karchs()}
    kdec_ids = {e.id for e in index.all_kdecs()}
    kexp_ids = {e.id for e in index.all_kexps()}

    # 2: KRouter 引用
    for kr in index.all_krouters():
        for field, ref in [("kformat_in", kr.kformat_in), ("kformat_out", kr.kformat_out)]:
            if not ref:
                continue
            if ref.startswith("kb.") and ref not in kformat_ids:
                issues.append(ValidationIssue(
                    entry_id=kr.id, field=field,
                    message=f"references missing KFormat '{ref}'",
                    severity="warning",
                ))

    # 3: KFormat 引用
    for kf in index.all_kformats():
        for ref in kf.relates_to_krouters:
            if ref and ref not in krouter_ids:
                issues.append(ValidationIssue(
                    entry_id=kf.id, field="relates_to_krouters",
                    message=f"references missing KRouter '{ref}'",
                    severity="warning",
                ))

    # 4: KArch 交叉引用
    for karch in index.all_karchs():
        for ref in karch.related_decisions:
            if ref and ref not in kdec_ids:
                issues.append(ValidationIssue(
                    entry_id=karch.id, field="related_decisions",
                    message=f"references missing KDecision '{ref}'",
                    severity="warning",
                ))
        for ref in karch.related_experiments:
            if ref and ref not in kexp_ids:
                issues.append(ValidationIssue(
                    entry_id=karch.id, field="related_experiments",
                    message=f"references missing KExperiment '{ref}'",
                    severity="warning",
                ))
        for ref in karch.related_karchs:
            if ref and ref not in karch_ids:
                issues.append(ValidationIssue(
                    entry_id=karch.id, field="related_karchs",
                    message=f"references missing KArchitecture '{ref}'",
                    severity="warning",
                ))

    # 5: KDec supersede 链
    for kdec in index.all_kdecs():
        for ref in kdec.supersedes + kdec.superseded_by:
            if ref and ref not in kdec_ids:
                issues.append(ValidationIssue(
                    entry_id=kdec.id, field="supersedes/superseded_by",
                    message=f"references missing KDecision '{ref}'",
                    severity="warning",
                ))

    # 6: deprecated 被引用
    deprecated = {e.id for e in index.all_entries() if e.maturity == "deprecated"}
    if deprecated:
        for entry in index.all_entries():
            if entry.maturity == "deprecated":
                continue  # deprecated 引用 deprecated 不报
            for field_name in ("relates_to_krouters", "related_decisions",
                               "related_experiments", "related_karchs",
                               "supersedes", "superseded_by",
                               "depends_on", "contradicts"):
                refs = getattr(entry, field_name, None)
                if not refs:
                    continue
                for ref in refs:
                    if ref in deprecated:
                        issues.append(ValidationIssue(
                            entry_id=entry.id, field=field_name,
                            message=f"references deprecated '{ref}'",
                            severity="info",
                        ))


    return issues
