# [OMNI] origin=codex domain=services/_core/material_wiki ts=2026-08-02T00:00:00+08:00 type=format status=active
# [OMNI] summary="报告聚合视图的 material_ref 合同与 Markdown 渲染器；报告只保存引用和非材料说明，正文始终回读 Wiki 本体"
# [OMNI] why="报告可以组织阅读但不能复制一份可独立演化的当前结论；latest 与 fixed 两种引用明确区分随本体更新和冻结版本"
# [OMNI] tags=material-wiki,report,reference,view
# [OMNI] material_id="material:core.material_wiki.report_refs.py"
"""Typed report references that render canonical Material Wiki bodies on demand."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .service import MaterialWikiService


@dataclass(frozen=True, slots=True)
class MaterialReportRef:
    material_id: str
    version_policy: str = "latest"
    version: str | None = None

    def __post_init__(self) -> None:
        if self.version_policy not in {"latest", "fixed"}:
            raise ValueError("version_policy 只允许 latest 或 fixed")
        if self.version_policy == "fixed" and not self.version:
            raise ValueError("fixed material_ref 必须指定 version")
        if self.version_policy == "latest" and self.version is not None:
            raise ValueError("latest material_ref 不保存 version；渲染时读取当前有效版")


@dataclass(frozen=True, slots=True)
class MaterialReportView:
    title: str
    narration: str
    material_refs: tuple[MaterialReportRef, ...]

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("report title 不能为空")
        object.__setattr__(self, "material_refs", tuple(self.material_refs))


def render_material_ref(reference: MaterialReportRef, service: MaterialWikiService) -> str:
    document = service.get(reference.material_id)
    if reference.version_policy == "fixed":
        version = document.version(reference.version or "")
    else:
        version = document.current
        if version is None:
            raise ValueError(f"{reference.material_id} 没有当前有效版本")
    stable_ref = f"{document.canonical_ref}#version-{version.version}"
    return (
        f'<section data-material-ref="{document.material_id}" '
        f'data-material-version="{version.version}">\n\n'
        f"## [{document.title}]({stable_ref})\n\n"
        f"> 状态：`{version.status}` · 本体：`{document.material_id}@{version.version}`\n\n"
        f"{version.body}\n\n"
        "</section>"
    )


def render_report(view: MaterialReportView, service: MaterialWikiService) -> str:
    sections = [
        f"# {view.title}",
        "",
        "<!-- report-narration:non-material -->",
        view.narration.rstrip(),
        "<!-- /report-narration:non-material -->",
    ]
    for reference in view.material_refs:
        sections.extend(["", render_material_ref(reference, service)])
    return "\n".join(sections).rstrip() + "\n"


def report_view(
    *,
    title: str,
    narration: str,
    material_refs: Iterable[MaterialReportRef],
) -> MaterialReportView:
    return MaterialReportView(title, narration, tuple(material_refs))
