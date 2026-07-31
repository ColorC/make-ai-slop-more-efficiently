# [OMNI] origin=claude-code domain=services/knowledge/audit.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:learning.knowledge.consistency_auditor.engine.py"
"""omnikb.audit — 知识库与代码的一致性审计。

三类审计:
  1. coverage_report — 对比 KFormat 与可执行 FormatRegistry
  2. code_anchor_drift — KArch.code_anchors 指向的文件/行是否还存在
  3. orphan_code — 代码里的 public Router/Format 但 KB 无对应条目
  4. staleness — KRouter.relates_to_routers 引用的类是否还存在

与 graveyard 的 analysis.py 的差异:
  - 简化了 path_suggest (BFS 路径建议), 因为实际用不到
  - 保留并加强了 code_anchor 校验, 这是本次升级的关键需求 (OMNI-017 drift)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omnicompany.packages.services._learning.knowledge.index import load_or_rebuild
from omnicompany.packages.services._learning.knowledge.schema import (
    KFormatEntry,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Format 覆盖报告
# ═══════════════════════════════════════════════════════════

@dataclass
class CoverageReport:
    knowledge_only: list[str] = field(default_factory=list)
    """有 KFormat 描述但无对应可执行 Format"""
    code_only: list[str] = field(default_factory=list)
    """有可执行 Format 但无 KFormat 描述"""
    both: list[str] = field(default_factory=list)
    """两侧都有"""

    def summary(self) -> str:
        return (
            f"coverage: both={len(self.both)} "
            f"knowledge_only={len(self.knowledge_only)} "
            f"code_only={len(self.code_only)}"
        )


def format_coverage_report(project_root: Path, registry: Any | None = None) -> CoverageReport:
    """对比 KB 中的 KFormat 与可执行 FormatRegistry。"""
    index = load_or_rebuild(project_root)

    if registry is None:
        try:
            from omnicompany.protocol.format import create_builtin_registry
            registry = create_builtin_registry()
        except ImportError:
            registry = None

    code_ids: set[str] = set()
    if registry is not None:
        try:
            code_ids = {f.id for f in registry.all_formats()}
        except Exception:
            pass

    kformat_ids = {e.id for e in index.all_kformats()}

    kb_related: set[str] = set()
    for kf in index.all_kformats():
        kb_related.update(kf.relates_to_formats)

    report = CoverageReport()
    for fmt_id in code_ids:
        if fmt_id in kformat_ids or fmt_id in kb_related:
            report.both.append(fmt_id)
        else:
            report.code_only.append(fmt_id)

    for kf_id in kformat_ids:
        kf = index.get(kf_id)
        if not isinstance(kf, KFormatEntry):
            continue
        has_code = kf_id in code_ids or any(r in code_ids for r in kf.relates_to_formats)
        if not has_code:
            report.knowledge_only.append(kf_id)

    return report


# ═══════════════════════════════════════════════════════════
# Code anchor drift 检测 (KArch 专用, OMNI-017)
# ═══════════════════════════════════════════════════════════

@dataclass
class AnchorDrift:
    karch_id: str
    anchor: str
    reason: str  # file_not_found | line_out_of_range | parse_error

    def __repr__(self) -> str:
        return f"[drift] {self.karch_id} @ {self.anchor}: {self.reason}"


_ANCHOR_RE = re.compile(
    r"^(?P<path>[^:]+?)(?::L(?P<start>\d+)(?:-L(?P<end>\d+))?)?$"
)


def check_code_anchors(project_root: Path) -> list[AnchorDrift]:
    """扫描所有 KArch 的 code_anchors, 验证:
      1. path 指向的文件是否存在
      2. 若带 L<start>-L<end>, end 是否超出文件总行数
    返回 drift 列表。
    """
    index = load_or_rebuild(project_root)
    drifts: list[AnchorDrift] = []
    project_root = project_root.resolve()

    for karch in index.all_karchs():
        for anchor in karch.code_anchors:
            m = _ANCHOR_RE.match(anchor.strip())
            if m is None:
                drifts.append(AnchorDrift(
                    karch_id=karch.id, anchor=anchor, reason="parse_error"
                ))
                continue

            rel_path = m.group("path")
            start_s = m.group("start")
            end_s = m.group("end")

            file_path = project_root / rel_path
            if not file_path.exists():
                drifts.append(AnchorDrift(
                    karch_id=karch.id, anchor=anchor, reason="file_not_found"
                ))
                continue

            if start_s:
                try:
                    start = int(start_s)
                    end = int(end_s) if end_s else start
                    total_lines = sum(1 for _ in file_path.read_text(
                        encoding="utf-8", errors="ignore"
                    ).splitlines())
                    if end > total_lines:
                        drifts.append(AnchorDrift(
                            karch_id=karch.id, anchor=anchor,
                            reason=f"line_out_of_range (end={end} > total={total_lines})"
                        ))
                except (OSError, ValueError) as e:
                    drifts.append(AnchorDrift(
                        karch_id=karch.id, anchor=anchor,
                        reason=f"parse_error: {e}"
                    ))

    return drifts


# ═══════════════════════════════════════════════════════════
# 孤儿代码检测 (OMNI-018)
# ═══════════════════════════════════════════════════════════

@dataclass
class OrphanCode:
    kind: str  # router | format | pipeline
    name: str
    source: str  # 源文件路径或 pipelines.py 中的注册名

    def __repr__(self) -> str:
        return f"[orphan-{self.kind}] {self.name} ({self.source})"


def find_orphan_routers(project_root: Path) -> list[OrphanCode]:
    """扫 src/omnicompany/**/routers.py 找所有 Router/LLMRouter/AgentNodeLoop
    类定义, 与 KB 的 KRouter 条目对比, 返回 KB 里没有描述的 Router 类名列表。

    这是 OMNI-018 规则的底层实现。
    """
    index = load_or_rebuild(project_root)
    described: set[str] = set()
    for kr in index.all_krouters():
        described.update(kr.relates_to_routers)

    # 扫代码
    orphans: list[OrphanCode] = []
    src = project_root / "src" / "omnicompany"
    if not src.exists():
        return []
    router_pat = re.compile(
        r"^class\s+(\w+)\s*\(\s*(Router|LLMRouter|AgentNodeLoop)\s*\)\s*:",
        re.MULTILINE,
    )
    for p in src.rglob("*.py"):
        if "__pycache__" in p.parts or "_graveyard" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in router_pat.finditer(text):
            class_name = m.group(1)
            if class_name not in described:
                orphans.append(OrphanCode(
                    kind="router",
                    name=class_name,
                    source=str(p.relative_to(project_root)).replace("\\", "/"),
                ))

    return orphans


# ═══════════════════════════════════════════════════════════
# Staleness 检测 (KRouter 引用不存在的 Router 类)
# ═══════════════════════════════════════════════════════════

@dataclass
class StalenessReport:
    stale_krouters: list[dict] = field(default_factory=list)
    """relates_to_routers 引用了不存在的类"""
    old_draft: list[str] = field(default_factory=list)
    """长期停留在 draft 且内容极少的条目 id"""

    def summary(self) -> str:
        return f"stale: krouters={len(self.stale_krouters)} old_draft={len(self.old_draft)}"


def staleness_report(project_root: Path) -> StalenessReport:
    index = load_or_rebuild(project_root)
    report = StalenessReport()

    # 扫代码收集实际存在的 Router 类名
    class_names: set[str] = set()
    src = project_root / "src" / "omnicompany"
    if src.exists():
        router_pat = re.compile(r"^class (\w+Router)\(", re.MULTILINE)
        for p in src.rglob("*.py"):
            if "__pycache__" in p.parts or "_graveyard" in p.parts:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in router_pat.finditer(text):
                class_names.add(m.group(1))

    if class_names:
        for kr in index.all_krouters():
            stale = [r for r in kr.relates_to_routers if r not in class_names]
            if stale:
                report.stale_krouters.append({
                    "krouter_id": kr.id,
                    "stale_refs": stale,
                    "source_path": kr.source_path,
                })

    # draft + 内容极少
    for entry in index.all_entries():
        if entry.maturity != "draft" or not entry.source_path:
            continue
        try:
            content = Path(entry.source_path).read_text(encoding="utf-8", errors="ignore")
            if len(content) < 200:
                report.old_draft.append(entry.id)
        except OSError:
            pass

    return report


# ═══════════════════════════════════════════════════════════
# 聚合审计
# ═══════════════════════════════════════════════════════════

@dataclass
class AuditReport:
    validation_issues: list = field(default_factory=list)
    anchor_drifts: list[AnchorDrift] = field(default_factory=list)
    orphan_routers: list[OrphanCode] = field(default_factory=list)
    staleness: StalenessReport = field(default_factory=StalenessReport)
    format_coverage: CoverageReport = field(default_factory=CoverageReport)
    def has_issues(self) -> bool:
        return (
            bool(self.validation_issues)
            or bool(self.anchor_drifts)
            or bool(self.orphan_routers)
            or bool(self.staleness.stale_krouters)
        )

    def summary(self) -> str:
        return (
            f"validation={len(self.validation_issues)} "
            f"drifts={len(self.anchor_drifts)} "
            f"orphans={len(self.orphan_routers)} "
            f"{self.staleness.summary()} "
            f"{self.format_coverage.summary()}"
        )


def run_full_audit(project_root: Path) -> AuditReport:
    """跑全部 6 类审计, 返回聚合报告。KBAuditRouter 会调用此函数。"""
    from omnicompany.packages.services._learning.knowledge.index import validate

    return AuditReport(
        validation_issues=validate(project_root),
        anchor_drifts=check_code_anchors(project_root),
        orphan_routers=find_orphan_routers(project_root),
        staleness=staleness_report(project_root),
        format_coverage=format_coverage_report(project_root),
    )
