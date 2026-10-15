# [OMNI] origin=codex domain=services/_core/material_wiki ts=2026-08-02T00:00:00+08:00 type=router status=active
# [OMNI] summary="材料 Wiki 运行时：从唯一 profile 名录解析项目合同、本体根与投影设施，供 CLI、HTTP、迁移器和 Agent 共用"
# [OMNI] why="调用方不应各自猜测 Vilo、Walker 或 Bilibili 的路径；同一名录必须同时决定 profile 与 canonical adapter"
# [OMNI] tags=material-wiki,runtime,profile,adapter
# [OMNI] material_id="material:core.material_wiki.runtime.py"
"""Build project-scoped Material Wiki services from the canonical manifest."""

from __future__ import annotations

from pathlib import Path

from .adapters.markdown import MarkdownWikiAdapter, WikiDocument
from .profiles import (
    load_profile_from_wiki,
    load_profile_manifest,
    resolve_canonical_root,
    resolve_loader_path,
)
from .projections import (
    ProjectionCoordinator,
    ProjectionRebuildSummary,
    build_default_projection_coordinator,
)
from .schema import MaterialWikiContractError, MaterialWikiProfile, ProfileRegistration
from .service import MaterialWikiService


class MaterialWikiRuntime:
    """Resolve one registered project into the same canonical-first service everywhere."""

    def __init__(
        self,
        *,
        omnicompany_root: str | Path,
        manifest_path: str | Path | None = None,
        facility_root: str | Path | None = None,
    ) -> None:
        self.omnicompany_root = Path(omnicompany_root).resolve()
        self.manifest_path = Path(
            manifest_path or self.omnicompany_root / "config" / "material_wiki" / "profiles.yaml"
        ).resolve()
        self.facility_root = Path(facility_root or self.omnicompany_root).resolve()

    def registrations(self) -> tuple[ProfileRegistration, ...]:
        return load_profile_manifest(self.manifest_path)

    def registration(self, project_id: str) -> ProfileRegistration:
        registration = next(
            (item for item in self.registrations() if item.project_id == project_id),
            None,
        )
        if registration is None:
            raise MaterialWikiContractError(f"项目 {project_id!r} 未登记 Material Wiki profile")
        if registration.canonical is None:
            raise MaterialWikiContractError(f"项目 {project_id!r} 未登记 canonical backend")
        if not registration.loader_ref:
            raise MaterialWikiContractError(f"项目 {project_id!r} 未登记 profile loader_ref")
        return registration

    def profile(self, project_id: str) -> MaterialWikiProfile:
        registration = self.registration(project_id)
        path = resolve_loader_path(
            registration.loader_ref or "",
            omnicompany_root=self.omnicompany_root,
        )
        profile = load_profile_from_wiki(path)
        if profile.profile_id != registration.profile_id or profile.project_id != project_id:
            raise MaterialWikiContractError(
                f"{path} 的 profile 身份与 manifest 不一致："
                f"{profile.project_id}/{profile.profile_id}"
            )
        return profile

    def adapter(self, project_id: str) -> MarkdownWikiAdapter:
        registration = self.registration(project_id)
        canonical = registration.canonical
        assert canonical is not None
        root = resolve_canonical_root(
            canonical.root_ref,
            omnicompany_root=self.omnicompany_root,
        )
        return MarkdownWikiAdapter(root, canonical_prefix=canonical.canonical_prefix)

    def service(self, project_id: str) -> MaterialWikiService:
        return MaterialWikiService(
            self.profile(project_id),
            self.adapter(project_id),
            projection_coordinator=self._projection_coordinator(),
        )

    def _projection_coordinator(self) -> ProjectionCoordinator:
        return build_default_projection_coordinator(
            state_root=self.facility_root / "data" / "services" / "material_wiki" / "projections",
            semantic_root=self.facility_root,
            registry_dir=self.facility_root / "data" / "services" / "registry",
            isolated_events_root=(
                self.facility_root / "data" / "events" / "material_wiki"
                if self.facility_root != self.omnicompany_root
                else None
            ),
        )

    def rebuild(self, project_id: str) -> ProjectionRebuildSummary:
        """Recreate all disposable projections from one project's canonical bodies."""
        adapter = self.adapter(project_id)
        documents = tuple(document for _path, document in adapter.iter_documents())
        return self._projection_coordinator().rebuild(adapter, documents)

    def documents(self, project_id: str) -> tuple[tuple[Path, WikiDocument], ...]:
        return tuple(self.adapter(project_id).iter_documents())


__all__ = ["MaterialWikiRuntime"]
