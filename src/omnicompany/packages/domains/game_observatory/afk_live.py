from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .afk_benchmark import AfkHeroUpgradeOracle
from .media_validation import artifact_file_issues, assert_public_artifacts
from .models import (
    ArtifactRef,
    BalanceParameter,
    BalanceSpec,
    BenchmarkTask,
    BuildScope,
    Claim,
    ContentKind,
    CoreLoopSpec,
    CoreLoopStep,
    DependencySpec,
    DesignArtifactSpec,
    DesignSectionCoverage,
    DesignStatement,
    FailureRecoverySpec,
    FeedbackSpec,
    FlowNode,
    Game,
    GameReport,
    InformationArchitectureSpec,
    InteractionSpec,
    InteractionStep,
    LayoutElementSpec,
    LayoutSpec,
    MechanismSpec,
    NavigationEdge,
    NormalizedRect,
    ObjectiveCheck,
    Observation,
    PlayerVoice,
    ProgressionAxis,
    ProgressionSpec,
    ResourceDefinition,
    ResourceModel,
    ResourceRelation,
    ReverseEngineeredGameDesignSpec,
    RunRef,
    SourceRef,
    StateCase,
    StateMatrix,
    Surface,
    SystemConcept,
    SystemInstance,
    TutorialSpec,
    TutorialStep,
    UIElementInstance,
    utc_now,
)
from .store import ObservatoryStore


SCREEN_ROLES = ("world", "hero_hall", "monetization_interrupt", "hero_detail")


class AfkLiveEvidenceManifest(BaseModel):
    schema_version: Literal["game-observatory.afk-live-evidence.v1"] = (
        "game-observatory.afk-live-evidence.v1"
    )
    target_id: str = "device://mumu/0"
    serial: str
    package_name: str
    package_version: str
    platform_version: str
    device_model: str
    captured_at: str = Field(default_factory=utc_now)
    screenshot_artifact_ids: dict[str, str]
    capture_session_ids: list[str] = Field(default_factory=list)
    action_run_ids: list[str] = Field(default_factory=list)
    source_root: str = "D:/P4/main/Client"
    observed_fields: dict[str, str | int | float]
    upgrade_executed: bool = False
    note: str | None = None

    def required_role_issues(self) -> list[str]:
        return [role for role in SCREEN_ROLES if not self.screenshot_artifact_ids.get(role)]


def _rect(x: float, y: float, width: float, height: float) -> NormalizedRect:
    return NormalizedRect(x=x, y=y, width=width, height=height)


def _element(
    element_id: str,
    role: str,
    label: str,
    bounds: NormalizedRect,
    *,
    source_id: str,
    artifact_id: str,
    actions: list[str] | None = None,
    parent_id: str | None = None,
) -> UIElementInstance:
    return UIElementInstance(
        id=element_id,
        role=role,
        label=label,
        bounds=bounds,
        parent_id=parent_id,
        actions=actions or [],
        source_ids=[source_id],
        artifact_ids=[artifact_id],
    )


class AfkLiveDesignBuilder:
    """Turn a real MuMu observation trail into a publishable design specification."""

    REPORT_ID = "report.afk-journey.hero-upgrade.v1"
    SLUG = "afk-journey-hero-upgrade"
    SCOPE_ID = "scope.afk.android-cn.1.7.21.hero-hall"
    DESIGN_RUN_ID = "run.afk.design-reconstruction.20260713"

    def __init__(self, store: ObservatoryStore) -> None:
        self.store = store

    @staticmethod
    def load_manifest(path: Path) -> AfkLiveEvidenceManifest:
        return AfkLiveEvidenceManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def _source_artifacts(
        self, manifest: AfkLiveEvidenceManifest
    ) -> dict[str, ArtifactRef]:
        missing_roles = manifest.required_role_issues()
        if missing_roles:
            raise ValueError(f"AFK evidence manifest is missing roles: {missing_roles}")
        artifacts: dict[str, ArtifactRef] = {}
        for role in SCREEN_ROLES:
            artifact_id = manifest.screenshot_artifact_ids[role]
            artifact = self.store.get_artifact(artifact_id)
            if artifact is None:
                raise ValueError(f"AFK evidence artifact not found: {artifact_id}")
            if artifact.kind not in {"screenshot", "video_frame"}:
                raise ValueError(f"AFK evidence {artifact_id} is not a visual frame")
            issues = artifact_file_issues(artifact)
            if issues:
                raise ValueError("; ".join(issues))
            artifacts[role] = artifact
        return artifacts

    def _public_screenshot(
        self,
        role: str,
        source: ArtifactRef,
        manifest: AfkLiveEvidenceManifest,
    ) -> ArtifactRef:
        body = Path(source.path).read_bytes()
        suffix = Path(source.path).suffix.lower() or ".png"
        artifact_id = f"art.afk.live.{role}"
        path = self.store.artifact_root / f"{artifact_id}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if Path(source.path).resolve() != path.resolve():
            shutil.copyfile(source.path, path)
        return ArtifactRef(
            id=artifact_id,
            kind=source.kind,
            path=str(path),
            sha256=hashlib.sha256(body).hexdigest(),
            run_id=source.run_id,
            media_type=source.media_type or "image/png",
            metadata={
                "public": True,
                "evidence_role": role,
                "derived_from_artifact_id": source.id,
                "target_id": manifest.target_id,
                "package_version": manifest.package_version,
                "resolution": "1080x1920",
            },
        )

    def _svg_artifact(
        self,
        artifact_id: str,
        kind: Literal["wireframe", "wireflow"],
        body: str,
        *,
        derived_from: list[str],
    ) -> ArtifactRef:
        path = self.store.artifact_root / f"{artifact_id}.svg"
        encoded = body.encode("utf-8")
        path.write_bytes(encoded)
        return ArtifactRef(
            id=artifact_id,
            kind=kind,
            path=str(path),
            sha256=hashlib.sha256(encoded).hexdigest(),
            run_id=self.DESIGN_RUN_ID,
            media_type="image/svg+xml",
            metadata={
                "public": True,
                "derived_from_artifact_ids": derived_from,
                "generation_method": "hybrid",
                "reviewed_by": "game-observatory-builder",
            },
        )

    @staticmethod
    def _wireframe_svg(surface: Surface) -> str:
        width, height = 720, 1280
        blocks: list[str] = []
        palette = ["#d9e3f0", "#e8d8bc", "#d8e7dc", "#ead8d8", "#ddd8eb"]
        for index, element in enumerate(surface.elements):
            bounds = element.bounds or _rect(0.1, 0.1 + index * 0.1, 0.8, 0.08)
            x = round(bounds.x * width)
            y = round(bounds.y * height)
            rect_width = max(12, round(bounds.width * width))
            rect_height = max(12, round(bounds.height * height))
            label = html.escape(element.label or element.id)
            fill = palette[index % len(palette)]
            blocks.append(
                f'<g><rect x="{x}" y="{y}" width="{rect_width}" height="{rect_height}" '
                f'rx="10" fill="{fill}" stroke="#26364a" stroke-width="2"/>'
                f'<text x="{x + 10}" y="{y + 24}" font-size="16" fill="#182230">{label}</text>'
                f'<text x="{x + 10}" y="{y + 46}" font-size="11" fill="#53647a">'
                f'{bounds.x:.3f}, {bounds.y:.3f}, {bounds.width:.3f}, {bounds.height:.3f}</text></g>'
            )
        title = html.escape(surface.title)
        description = html.escape(surface.description or "")
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">'
            f'<title id="title">{title} reverse-engineered wireframe</title>'
            f'<desc id="desc">{description}</desc>'
            '<rect width="720" height="1280" fill="#f7f4ed"/>'
            '<rect x="22" y="22" width="676" height="1236" rx="34" fill="#fff" '
            'stroke="#172536" stroke-width="4"/>'
            f'<text x="42" y="64" font-size="25" font-weight="700" fill="#172536">{title}</text>'
            '<text x="42" y="92" font-size="12" fill="#64748b">1080×1920 normalized reconstruction</text>'
            + "".join(blocks)
            + '</svg>'
        )

    @staticmethod
    def _wireflow_svg(surfaces: list[Surface]) -> str:
        width, height = 1520, 520
        cards: list[str] = []
        arrows: list[str] = []
        for index, surface in enumerate(surfaces):
            x = 35 + index * 370
            title = html.escape(surface.title)
            cards.append(
                f'<g><rect x="{x}" y="90" width="300" height="310" rx="24" fill="#fff" '
                'stroke="#23334a" stroke-width="3"/>'
                f'<text x="{x + 22}" y="140" font-size="22" font-weight="700" fill="#192638">{title}</text>'
                f'<text x="{x + 22}" y="172" font-size="14" fill="#607089">{html.escape(surface.kind)}</text>'
                f'<text x="{x + 22}" y="220" font-size="13" fill="#334155">{len(surface.elements)} semantic elements</text>'
                f'<text x="{x + 22}" y="252" font-size="13" fill="#334155">state: {html.escape(surface.id)}</text></g>'
            )
            if index < len(surfaces) - 1:
                arrows.append(
                    f'<path d="M {x + 300} 245 L {x + 360} 245" stroke="#e56b3f" '
                    'stroke-width="5" marker-end="url(#arrow)"/>'
                )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">'
            '<title id="title">AFK Journey hero hall observed wireflow</title>'
            '<desc id="desc">World navigation to hero hall, conditional monetization interruption, and hero upgrade detail.</desc>'
            '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">'
            '<path d="M0,0 L0,6 L9,3 z" fill="#e56b3f"/></marker></defs>'
            '<rect width="1520" height="520" fill="#f5f1e8"/>'
            '<text x="35" y="48" font-size="28" font-weight="700" fill="#172536">Observed navigation and interruption branch</text>'
            + "".join(arrows)
            + "".join(cards)
            + '</svg>'
        )

    @staticmethod
    def _sources(source_evidence: dict[str, Any]) -> list[SourceRef]:
        files = source_evidence["files"]
        return [
            SourceRef(
                id="src.afk.live-mumu-1.7.21",
                kind=ContentKind.direct_observation,
                title="AFK Journey CN 1.7.21 MuMu live observation",
                url="source://game-observatory/mumu/0/afk-journey-1.7.21",
                locator="captured 2026-07-13; 1080×1920",
                version_context="Android CN package 1.7.21",
                public=False,
                usage_policy="internal_evidence",
                note="设施租约内的真实启动、英雄厅、商业化中断与英雄详情截图。",
            ),
            SourceRef(
                id="src.afk.hero-upgrade-view",
                kind=ContentKind.direct_observation,
                title="AFK Journey HeroUpgradeView.lua",
                url="source://afk-client/Binary/Src/UI/Hero/View/HeroUpgradeView.lua",
                locator="lines 37-113, 215-251",
                version_context=f"sha256:{files['upgrade_view']['sha256']}",
                public=False,
                usage_policy="internal_evidence",
                note="升级界面、下一等级预览、多资源按钮与确认入口。",
            ),
            SourceRef(
                id="src.afk.hero-model-cost",
                kind=ContentKind.direct_observation,
                title="AFK Journey HeroModel:GetLevelUpCost",
                url="source://afk-client/Binary/Src/UI/Hero/HeroModel.lua",
                locator="lines 624-681, 2411-2477",
                version_context=f"sha256:{files['hero_model']['sha256']}",
                public=False,
                usage_policy="internal_evidence",
                note="按当前等级、目标等级和赛季态计算升级消耗，并逐项检查资源是否足够。",
            ),
            SourceRef(
                id="src.afk.hero-upgrade-tutorial",
                kind=ContentKind.direct_observation,
                title="AFK Journey HeroUpgradeTutorialTask.lua",
                url="source://afk-client/Binary/Src/UI/Tutorial/Hero/HeroUpgrade/HeroUpgradeTutorialTask.lua",
                locator="lines 32-130, 147-190",
                version_context=f"sha256:{files['tutorial']['sha256']}",
                public=False,
                usage_policy="internal_evidence",
                note="主界面进入英雄系统、定位英雄、点击升级以及返回的官方客户端教程路径。",
            ),
            SourceRef(
                id="voice.afk.hero-essence-bottleneck",
                kind=ContentKind.player_voice,
                title="Hero Essence - best sources for F2P players",
                url="https://www.reddit.com/r/AFKJourney/comments/1bz40jd/",
                author="Reddit / r/AFKJourney",
                published_at="2024-04-08",
                version_context="2024 live progression",
                note="玩家把 Hero Essence 描述为需要等待推进的硬瓶颈，并询问可重复来源。",
            ),
            SourceRef(
                id="voice.afk.seasonal-resonance-legibility",
                kind=ContentKind.player_voice,
                title="Does anyone know how nonseason resonance affects seasonal CP",
                url="https://www.reddit.com/r/AFKJourney/comments/1e26yf4/",
                author="Reddit / r/AFKJourney",
                published_at="2024-07-13",
                version_context="2024 seasonal progression",
                note="玩家无法从界面直接理解永久共鸣等级如何影响赛季战力。",
            ),
            SourceRef(
                id="voice.afk.per-hero-leveling-tedium",
                kind=ContentKind.player_voice,
                title="I wish leveling up characters was less tedious",
                url="https://www.reddit.com/r/AFKJourney/comments/1gpohd6/",
                author="Reddit / r/AFKJourney",
                published_at="2024-11-12",
                version_context="2024 live progression",
                note="玩家希望资源充足时能对共鸣队列进行 +1/+5/+10 等批量升级。",
            ),
        ]

    def _run_refs(
        self,
        public_screens: dict[str, ArtifactRef],
        design_artifacts: list[ArtifactRef],
    ) -> list[RunRef]:
        stored = {item.id: item for item in self.store.list_runs(10000)}
        refs: list[RunRef] = []
        for run_id in sorted({item.run_id for item in public_screens.values() if item.run_id}):
            run = stored.get(run_id)
            if run is None:
                raise ValueError(f"AFK screenshot run is missing: {run_id}")
            refs.append(
                RunRef(
                    id=run.id,
                    target_id=run.target_id,
                    adapter=run.adapter,
                    started_at=run.started_at,
                    ended_at=run.ended_at,
                    status=run.status,
                    build_scope_id=self.SCOPE_ID,
                    artifact_ids=[
                        item.id for item in public_screens.values() if item.run_id == run_id
                    ],
                    note="MuMu ADB capture executed through the Game Observatory lease gateway.",
                )
            )
        refs.append(
            RunRef(
                id=self.DESIGN_RUN_ID,
                target_id="analysis://afk-journey/hero-hall",
                adapter="game-observatory-design-reconstruction",
                started_at="2026-07-13T10:00:00+00:00",
                ended_at="2026-07-13T10:01:00+00:00",
                status="passed",
                build_scope_id=self.SCOPE_ID,
                artifact_ids=[item.id for item in design_artifacts],
                note="Reviewed hybrid reconstruction from live frames and source-level rules.",
            )
        )
        return refs

    def _surfaces(
        self, screens: dict[str, ArtifactRef], live_source: str
    ) -> list[Surface]:
        world = screens["world"]
        hall = screens["hero_hall"]
        offer = screens["monetization_interrupt"]
        detail = screens["hero_detail"]
        return [
            Surface(
                id="surface.afk.world-navigation",
                title="主世界与底部系统导航",
                kind="world",
                description="开放世界画面承载任务追踪、地图与活动入口；底部固定导航将英雄厅作为一级系统入口。",
                source_ids=[live_source],
                artifact_ids=[world.id],
                run_id=world.run_id,
                elements=[
                    _element("ui.afk.world.quest", "status", "任务追踪", _rect(0.64, 0.17, 0.34, 0.12), source_id=live_source, artifact_id=world.id),
                    _element("ui.afk.world.bottom-nav", "navigation", "底部一级导航", _rect(0.20, 0.90, 0.80, 0.10), source_id=live_source, artifact_id=world.id),
                    _element("ui.afk.world.hero-hall-entry", "button", "英雄厅", _rect(0.51, 0.91, 0.16, 0.09), source_id=live_source, artifact_id=world.id, actions=["tap"], parent_id="ui.afk.world.bottom-nav"),
                ],
            ),
            Surface(
                id="surface.afk.hero-hall",
                title="英雄厅总览与共鸣队列",
                kind="page",
                description="一屏同时呈现总战力、赛季共鸣等级、共鸣之脉、五名核心队列、完整英雄卡池与三条赛季成长入口。",
                source_ids=[live_source],
                artifact_ids=[hall.id],
                run_id=hall.run_id,
                elements=[
                    _element("ui.afk.hall.power", "status", "总战力 12,343 万", _rect(0.03, 0.06, 0.32, 0.05), source_id=live_source, artifact_id=hall.id),
                    _element("ui.afk.hall.resonance", "status", "赛季共鸣等级 357", _rect(0.27, 0.27, 0.51, 0.09), source_id=live_source, artifact_id=hall.id),
                    _element("ui.afk.hall.core-slots", "list", "五名共鸣队列", _rect(0.24, 0.35, 0.72, 0.07), source_id=live_source, artifact_id=hall.id),
                    _element("ui.afk.hall.hero-card", "listitem", "英雄卡片", _rect(0.04, 0.46, 0.18, 0.16), source_id=live_source, artifact_id=hall.id, actions=["tap"]),
                    _element("ui.afk.hall.quick-upgrade", "button", "快速升级", _rect(0.39, 0.83, 0.24, 0.07), source_id=live_source, artifact_id=hall.id, actions=["tap"]),
                    _element("ui.afk.hall.season-tabs", "tablist", "赛季回响 / 快速升级 / 赛季装备", _rect(0.02, 0.82, 0.96, 0.10), source_id=live_source, artifact_id=hall.id),
                ],
            ),
            Surface(
                id="surface.afk.monetization-interrupt",
                title="英雄浏览链路中的限时礼包中断",
                kind="modal",
                description="首次尝试打开英雄详情时出现高占屏、强对比的 1200% 限时礼包；返回键可退出并恢复英雄厅。",
                source_ids=[live_source],
                artifact_ids=[offer.id],
                run_id=offer.run_id,
                elements=[
                    _element("ui.afk.offer.panel", "dialog", "1200% 限时礼包", _rect(0.08, 0.06, 0.84, 0.86), source_id=live_source, artifact_id=offer.id),
                    _element("ui.afk.offer.price", "button", "价格 ¥45", _rect(0.60, 0.45, 0.24, 0.06), source_id=live_source, artifact_id=offer.id, actions=["tap"], parent_id="ui.afk.offer.panel"),
                    _element("ui.afk.offer.exit", "button", "系统返回退出", _rect(0.00, 0.00, 0.12, 0.08), source_id=live_source, artifact_id=offer.id, actions=["back"]),
                ],
            ),
            Surface(
                id="surface.afk.hero-detail-upgrade",
                title="罗万英雄详情与赛季升级预览",
                kind="page",
                description="英雄身份、永久等级、赛季等级、战力与两项升级资源在同一页面可见；绿色主按钮承载确认升级。",
                source_ids=[live_source, "src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
                artifact_ids=[detail.id],
                run_id=detail.run_id,
                elements=[
                    _element("ui.afk.detail.identity", "heading", "罗万 / 旅行商人", _rect(0.02, 0.01, 0.37, 0.11), source_id=live_source, artifact_id=detail.id),
                    _element("ui.afk.detail.hero-stage", "region", "英雄 3D 展示区", _rect(0.05, 0.12, 0.90, 0.64), source_id=live_source, artifact_id=detail.id),
                    _element("ui.afk.detail.base-level", "status", "永久等级 305", _rect(0.21, 0.78, 0.12, 0.05), source_id=live_source, artifact_id=detail.id),
                    _element("ui.afk.detail.season-level", "status", "赛季等级 357", _rect(0.57, 0.72, 0.36, 0.14), source_id=live_source, artifact_id=detail.id),
                    _element("ui.afk.detail.power", "status", "战力 151 万", _rect(0.65, 0.84, 0.25, 0.05), source_id=live_source, artifact_id=detail.id),
                    _element("ui.afk.detail.upgrade", "button", "升级", _rect(0.18, 0.88, 0.80, 0.10), source_id=live_source, artifact_id=detail.id, actions=["tap"]),
                    _element("ui.afk.detail.cost-coins", "status", "金币 21567 万 / 13561", _rect(0.43, 0.94, 0.15, 0.03), source_id=live_source, artifact_id=detail.id, parent_id="ui.afk.detail.upgrade"),
                    _element("ui.afk.detail.cost-manual", "status", "赛季经验材料 29950 / 8518", _rect(0.61, 0.94, 0.18, 0.03), source_id=live_source, artifact_id=detail.id, parent_id="ui.afk.detail.upgrade"),
                ],
            ),
        ]

    def build(self, manifest: AfkLiveEvidenceManifest) -> GameReport:
        source_artifacts = self._source_artifacts(manifest)
        screens = {
            role: self._public_screenshot(role, artifact, manifest)
            for role, artifact in source_artifacts.items()
        }
        source_evidence = AfkHeroUpgradeOracle(Path(manifest.source_root)).source_evidence()
        if not source_evidence["ok"]:
            raise ValueError("AFK source oracle symbols are incomplete")
        sources = self._sources(source_evidence)
        source_ids = [item.id for item in sources]
        live_source = "src.afk.live-mumu-1.7.21"
        surfaces = self._surfaces(screens, live_source)

        visual_design_artifacts: list[ArtifactRef] = []
        design_specs: list[DesignArtifactSpec] = []
        layout_specs: list[LayoutSpec] = []
        role_for_surface = dict(zip((item.id for item in surfaces), SCREEN_ROLES, strict=True))
        for surface in surfaces:
            role = role_for_surface[surface.id]
            wireframe = self._svg_artifact(
                f"art.afk.design.wireframe.{role}",
                "wireframe",
                self._wireframe_svg(surface),
                derived_from=[screens[role].id],
            )
            visual_design_artifacts.append(wireframe)
            design_specs.append(
                DesignArtifactSpec(
                    id=f"design-artifact.afk.wireframe.{role}",
                    title=f"{surface.title}反推线框图",
                    kind="wireframe",
                    artifact_id=wireframe.id,
                    surface_ids=[surface.id],
                    derived_from_artifact_ids=[screens[role].id],
                    generation_method="hybrid",
                    source_ids=[live_source],
                    run_id=self.DESIGN_RUN_ID,
                    review_status="reviewed",
                )
            )
            layout_specs.append(
                LayoutSpec(
                    id=f"layout.afk.{role}",
                    surface_id=surface.id,
                    canvas_aspect_ratio="9:16",
                    safe_area=_rect(0.0, 0.0, 1.0, 1.0),
                    elements=[
                        LayoutElementSpec(
                            id=f"layout-element.afk.{role}.{index}",
                            ui_element_id=element.id,
                            bounds=element.bounds or _rect(0.0, 0.0, 1.0, 1.0),
                            anchors=["portrait-canvas"],
                            responsive_behavior="保持相对竖屏画布的归一化位置；横屏需重新编排。",
                        )
                        for index, element in enumerate(surface.elements, start=1)
                    ],
                    constraints=["主操作位于拇指可达的下半屏", "永久信息与赛季信息必须用标签区分"],
                    source_ids=[live_source],
                    artifact_ids=[screens[role].id, wireframe.id],
                    run_id=self.DESIGN_RUN_ID,
                )
            )

        wireflow = self._svg_artifact(
            "art.afk.design.hero-upgrade-wireflow",
            "wireflow",
            self._wireflow_svg(surfaces),
            derived_from=[item.id for item in screens.values()],
        )
        visual_design_artifacts.append(wireflow)

        flow = [
            FlowNode(
                id="afk.flow.open-hero-hall",
                title="从主世界进入英雄厅",
                description="底部一级导航中的英雄厅按钮在世界态持续可见。",
                action="tap 英雄厅",
                state_before="主世界",
                state_after="英雄厅总览",
                source_ids=[live_source, "src.afk.hero-upgrade-tutorial"],
                artifact_ids=[screens["world"].id, screens["hero_hall"].id],
                surface_ids=[surfaces[0].id, surfaces[1].id],
                run_id=screens["world"].run_id,
                next=["afk.flow.select-hero"],
            ),
            FlowNode(
                id="afk.flow.select-hero",
                title="在共鸣卡池选择英雄",
                description="英雄卡片是列表到详情的主入口；本次会话首次点击被限时礼包分支截获。",
                action="tap 罗万卡片",
                state_before="英雄厅总览",
                state_after="限时礼包或英雄详情",
                source_ids=[live_source, "src.afk.hero-upgrade-tutorial"],
                artifact_ids=[screens["hero_hall"].id, screens["monetization_interrupt"].id],
                surface_ids=[surfaces[1].id, surfaces[2].id],
                run_id=screens["hero_hall"].run_id,
                next=["afk.flow.dismiss-offer", "afk.flow.open-hero-detail"],
            ),
            FlowNode(
                id="afk.flow.dismiss-offer",
                title="退出商业化中断",
                description="不触碰价格或免费奖励格，使用系统返回恢复英雄厅。",
                action="back",
                state_before="1200% 限时礼包",
                state_after="英雄厅总览",
                source_ids=[live_source],
                artifact_ids=[screens["monetization_interrupt"].id, screens["hero_hall"].id],
                surface_ids=[surfaces[2].id, surfaces[1].id],
                run_id=screens["monetization_interrupt"].run_id,
                next=["afk.flow.open-hero-detail"],
            ),
            FlowNode(
                id="afk.flow.open-hero-detail",
                title="打开罗万详情",
                description="第二次选择同一英雄后进入详情，永久等级与赛季等级同时显示。",
                action="tap 罗万卡片",
                state_before="英雄厅总览",
                state_after="罗万英雄详情",
                source_ids=[live_source, "src.afk.hero-upgrade-tutorial"],
                artifact_ids=[screens["hero_hall"].id, screens["hero_detail"].id],
                surface_ids=[surfaces[1].id, surfaces[3].id],
                run_id=screens["hero_detail"].run_id,
                next=["afk.flow.inspect-upgrade"],
            ),
            FlowNode(
                id="afk.flow.inspect-upgrade",
                title="读取升级前置条件",
                description="绿色升级按钮内并列显示两种资源的拥有量与需求量；本次基准停在确认前。",
                action="observe; do not tap",
                state_before="罗万英雄详情",
                state_after="升级条件已读",
                source_ids=[live_source, "src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
                artifact_ids=[screens["hero_detail"].id],
                surface_ids=[surfaces[3].id],
                run_id=screens["hero_detail"].run_id,
                next=["afk.flow.confirm-upgrade"],
            ),
            FlowNode(
                id="afk.flow.confirm-upgrade",
                title="确认升级（规则已验证，动作未执行）",
                description="源码表明按钮会按下一等级与赛季态计算多项成本并提交升级；账号资源变更未获本次验证授权。",
                action="tap 升级（仅在可复位快照获准后）",
                state_before="升级条件已读",
                state_after="等级与属性刷新，或资源不足反馈",
                source_ids=["src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
                artifact_ids=[screens["hero_detail"].id],
                surface_ids=[surfaces[3].id],
                run_id=screens["hero_detail"].run_id,
            ),
        ]

        mechanisms = [
            MechanismSpec(
                id="afk.mechanism.level-up-cost",
                title="下一等级多资源成本计算",
                description="升级界面调用 GetLevelUpCost，按目标等级、当前等级与赛季态生成多项成本，并逐项判断是否足够。",
                representation="pseudocode",
                code="costs = GetLevelUpCost(current + 1, current, isSeason)\
canUpgrade = every(cost.owned >= cost.required)",
                source_ids=["src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
                artifact_ids=[screens["hero_detail"].id],
                run_id=screens["hero_detail"].run_id,
            ),
            MechanismSpec(
                id="afk.mechanism.preview-before-mutation",
                title="确认前的等级、战力与成本预览",
                description="页面在不修改账号资源的前提下展示当前等级、赛季等级、战力和资源充足状态。",
                representation="state_machine",
                code="detail -> preview(costs, level, power) -> {confirm | back}",
                source_ids=[live_source, "src.afk.hero-upgrade-view"],
                artifact_ids=[screens["hero_detail"].id],
                run_id=screens["hero_detail"].run_id,
            ),
            MechanismSpec(
                id="afk.mechanism.resonance-layering",
                title="永久等级与赛季共鸣并行",
                description="英雄详情显示永久等级 305 与赛季等级 357；英雄厅把赛季共鸣 357 作为全队组织中心，并另示共鸣之脉 +120。",
                representation="rule",
                code="effective presentation = permanent hero level + seasonal resonance layer + resonance-vein modifier",
                source_ids=[live_source],
                artifact_ids=[screens["hero_hall"].id, screens["hero_detail"].id],
                run_id=screens["hero_hall"].run_id,
            ),
            MechanismSpec(
                id="afk.mechanism.liveops-interruption",
                title="限时礼包可条件性插入浏览路径",
                description="英雄卡片点击并不总是直接到详情；本次会话被礼包全屏模态分支截获，返回后可恢复原路径。",
                representation="state_machine",
                code="hero_card -> if offer_due then offer_modal -> back -> hero_hall else hero_detail",
                source_ids=[live_source],
                artifact_ids=[screens["hero_hall"].id, screens["monetization_interrupt"].id],
                run_id=screens["monetization_interrupt"].run_id,
            ),
        ]

        resources = [
            ResourceRelation(
                id="relation.afk.coins-upgrade-cost",
                resource="金币",
                role="cost",
                description="当前页面显示拥有 21567 万、单次需求 13561；资源充足。",
                source_ids=[live_source, "src.afk.hero-model-cost"],
                artifact_ids=[screens["hero_detail"].id],
                run_id=screens["hero_detail"].run_id,
                from_resource_id="resource.afk.coins",
                to_resource_id="resource.afk.season-level",
            ),
            ResourceRelation(
                id="relation.afk.manual-upgrade-cost",
                resource="赛季经验材料",
                role="cost",
                description="当前页面显示拥有 29950、单次需求 8518；资源充足。",
                source_ids=[live_source, "src.afk.hero-model-cost"],
                artifact_ids=[screens["hero_detail"].id],
                run_id=screens["hero_detail"].run_id,
                from_resource_id="resource.afk.season-manual",
                to_resource_id="resource.afk.season-level",
            ),
            ResourceRelation(
                id="relation.afk.hero-essence-gate",
                resource="Hero Essence",
                role="gate",
                description="玩家反馈将关键等级节点的精华需求感知为等待型瓶颈；本次 357→下一等级画面未出现该资源。",
                source_ids=["voice.afk.hero-essence-bottleneck", "src.afk.hero-model-cost"],
                artifact_ids=[screens["hero_detail"].id],
            ),
        ]
        resource_model = ResourceModel(
            id="resource-model.afk.hero-season-upgrade",
            title="英雄赛季升级资源与进度模型",
            resources=[
                ResourceDefinition(id="resource.afk.coins", title="金币", kind="currency", unit="万", source_ids=[live_source], artifact_ids=[screens["hero_detail"].id], run_id=screens["hero_detail"].run_id),
                ResourceDefinition(id="resource.afk.season-manual", title="赛季经验材料", kind="material", unit="份", source_ids=[live_source], artifact_ids=[screens["hero_detail"].id], run_id=screens["hero_detail"].run_id),
                ResourceDefinition(id="resource.afk.season-level", title="赛季共鸣等级", kind="progress", unit="级", source_ids=[live_source], artifact_ids=[screens["hero_hall"].id, screens["hero_detail"].id], run_id=screens["hero_hall"].run_id),
            ],
            relation_ids=[item.id for item in resources],
            source_ids=[live_source, "src.afk.hero-model-cost"],
        )

        player_voices = [
            PlayerVoice(
                id="pv.afk.hero-essence-bottleneck",
                summary="玩家把 Hero Essence 描述为需要等待 AFK 收益才能跨越的硬瓶颈，并寻找稳定补充来源。",
                theme="resource-bottleneck",
                sentiment="negative",
                source_id="voice.afk.hero-essence-bottleneck",
                system_node_id="afk.flow.inspect-upgrade",
                target_object_ids=["relation.afk.hero-essence-gate", "afk.mechanism.level-up-cost"],
                version_context="2024 live progression",
                language="en",
                tags=["hero-essence", "waiting", "progression"],
            ),
            PlayerVoice(
                id="pv.afk.seasonal-resonance-legibility",
                summary="玩家难以从游戏内说明理解永久共鸣如何影响赛季战力，反映多层等级体系的可读性成本。",
                theme="progression-legibility",
                sentiment="question",
                source_id="voice.afk.seasonal-resonance-legibility",
                target_object_ids=["progression.afk.layered-levels", "afk.mechanism.resonance-layering"],
                version_context="2024 seasonal progression",
                language="en",
                tags=["resonance", "season", "clarity"],
            ),
            PlayerVoice(
                id="pv.afk.per-hero-leveling-tedium",
                summary="玩家认为资源积累后逐个英雄升级形成重复劳动，并提出对五名共鸣位进行 +1/+5/+10 批量升级。",
                theme="interaction-efficiency",
                sentiment="mixed",
                source_id="voice.afk.per-hero-leveling-tedium",
                system_node_id="afk.flow.confirm-upgrade",
                target_object_ids=["interaction.afk.hero-upgrade", "ui.afk.hall.quick-upgrade"],
                version_context="2024 live progression",
                language="en",
                tags=["batch-upgrade", "qol", "repetition"],
            ),
        ]

        summary = (
            "本设计案从真实 MuMu 会话反推 AFK Journey 的英雄厅与赛季英雄升级：主世界一级导航进入英雄厅，"
            "英雄厅以共鸣等级和五名核心队列组织全卡池，单英雄详情将永久等级、赛季等级、战力和两项成本合并到确认按钮。"
            "实测路径还记录到限时礼包对英雄浏览的条件性中断；源码用于校验成本和教程规则，玩家反馈则绑定到资源瓶颈、等级可读性与批量操作对象。"
        )
        scope = BuildScope(
            id=self.SCOPE_ID,
            game_id="afk-journey",
            platform="Android / MuMu emulator + local client source oracle",
            version=manifest.package_version,
            region="CN",
            locale="zh-CN",
            account_stage="progress 105; hero hall unlocked; seasonal resonance 357",
            device=f"{manifest.device_model}; {manifest.platform_version}; MuMu 0",
            package_name=manifest.package_name,
            server="not retained",
            resolution="1080x1920 portrait",
            captured_at=manifest.captured_at,
            source_ids=[live_source, "src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
            artifact_ids=[item.id for item in screens.values()],
            run_id=screens["world"].run_id,
        )
        concept = SystemConcept(
            id="hero-hall-season-upgrade",
            title="英雄厅与赛季英雄升级",
            description="以共鸣队列组织英雄成长，并在单英雄详情中预览和确认赛季等级资源消耗的成长系统。",
            tags=["progression", "resonance", "hero-roster", "seasonal-level", "liveops-interruption"],
            source_ids=[live_source, "src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
            artifact_ids=[screens["hero_hall"].id, screens["hero_detail"].id],
            run_id=screens["hero_hall"].run_id,
        )
        instance = SystemInstance(
            id="instance.afk.android-cn.1.7.21.hero-hall",
            concept_id=concept.id,
            build_scope_id=scope.id,
            title="AFK Journey CN 1.7.21 赛季 357 英雄厅实例",
            surface_ids=[item.id for item in surfaces],
            source_ids=[live_source, "src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
            artifact_ids=[item.id for item in screens.values()],
            run_ids=sorted({item.run_id for item in screens.values() if item.run_id}),
        )

        overview = DesignStatement(id="statement.afk.overview", title="系统定位", statement="英雄厅把赛季共鸣队列、英雄卡池和单英雄升级统一为一条成长入口。", kind=ContentKind.direct_observation, source_ids=[live_source], artifact_ids=[screens["hero_hall"].id, screens["hero_detail"].id], run_id=screens["hero_hall"].run_id)
        player_goal = DesignStatement(id="statement.afk.player-goal", title="玩家目标", statement="快速判断当前队伍成长层、选择英雄，并在确认前理解下一等级的成本与收益。", kind=ContentKind.analyst_interpretation, source_ids=[live_source, "src.afk.hero-upgrade-view"], artifact_ids=[screens["hero_hall"].id, screens["hero_detail"].id], run_id=screens["hero_detail"].run_id)
        entry = DesignStatement(id="statement.afk.entry", title="入口与解锁", statement="进度 105 的账户在主世界底部一级导航持续显示英雄厅入口；英雄厅、赛季回响和赛季装备均已解锁。", kind=ContentKind.direct_observation, source_ids=[live_source], artifact_ids=[screens["world"].id, screens["hero_hall"].id], run_id=screens["world"].run_id)
        monetization = DesignStatement(id="statement.afk.monetization", title="商业化中断边界", statement="本次会话首次从英雄卡片进入详情时出现 ¥45 的 1200% 限时礼包；这里只记录外部可见触发、布局与退出路径，不推断转化率或内部运营逻辑。", kind=ContentKind.direct_observation, source_ids=[live_source], artifact_ids=[screens["monetization_interrupt"].id], run_id=screens["monetization_interrupt"].run_id)
        version_note = DesignStatement(id="statement.afk.version", title="版本与验证边界", statement=f"结论限于 CN Android {manifest.package_version}、该账户进度和 2026-07-13 的 MuMu 会话；升级按钮未执行，资源扣除与升级后属性只由源码规则支持。", kind=ContentKind.direct_observation, source_ids=[live_source, "src.afk.hero-model-cost"], artifact_ids=[screens["hero_detail"].id], run_id=screens["hero_detail"].run_id)

        core_loop = CoreLoopSpec(
            id="core-loop.afk.hero-upgrade",
            title="进入英雄厅—选择英雄—读取成本—确认或返回",
            player_goal="在不误触商业化入口的情况下完成一次可解释的英雄成长决策",
            entry_conditions=["英雄厅已解锁", "账户处于可操作主世界", "目标英雄已拥有"],
            exit_conditions=["确认升级并刷新等级", "资源不足停留", "返回英雄厅"],
            cadence="每次获得足够赛季资源后重复；英雄厅提供快速升级入口降低批量操作成本。",
            steps=[
                CoreLoopStep(id="core-step.afk.enter", title="进入", player_action="点击英雄厅", system_response="展示共鸣等级、核心队列和卡池", state_before="主世界", state_after="英雄厅", flow_node_ids=[flow[0].id], source_ids=[live_source], artifact_ids=[screens["world"].id, screens["hero_hall"].id], run_id=screens["world"].run_id),
                CoreLoopStep(id="core-step.afk.select", title="选择", player_action="点击目标英雄卡片", system_response="打开详情，或先插入限时礼包", state_before="英雄厅", state_after="礼包模态或英雄详情", flow_node_ids=[flow[1].id, flow[2].id, flow[3].id], source_ids=[live_source], artifact_ids=[screens["hero_hall"].id, screens["monetization_interrupt"].id, screens["hero_detail"].id], run_id=screens["hero_hall"].run_id),
                CoreLoopStep(id="core-step.afk.evaluate", title="评估", player_action="读取永久等级、赛季等级、战力和两项成本", system_response="在升级主按钮内显示拥有量/需求量", state_before="英雄详情", state_after="升级条件已读", flow_node_ids=[flow[4].id], source_ids=[live_source, "src.afk.hero-model-cost"], artifact_ids=[screens["hero_detail"].id], run_id=screens["hero_detail"].run_id),
                CoreLoopStep(id="core-step.afk.confirm", title="确认或返回", player_action="资源充足时点击升级，否则返回", system_response="扣除成本并刷新成长反馈，或保持原状态", state_before="升级条件已读", state_after="升级结果或英雄厅", flow_node_ids=[flow[5].id], source_ids=["src.afk.hero-upgrade-view", "src.afk.hero-model-cost"], artifact_ids=[screens["hero_detail"].id], run_id=screens["hero_detail"].run_id),
            ],
        )

        architecture = InformationArchitectureSpec(
            id="ia.afk.hero-hall",
            root_surface_ids=[surfaces[0].id],
            surface_ids=[item.id for item in surfaces],
            edges=[
                NavigationEdge(id="nav.afk.world-hall", from_surface_id=surfaces[0].id, to_surface_id=surfaces[1].id, trigger="tap 英雄厅", flow_node_ids=[flow[0].id]),
                NavigationEdge(id="nav.afk.hall-offer", from_surface_id=surfaces[1].id, to_surface_id=surfaces[2].id, trigger="conditional offer on hero selection", condition="限时礼包达到展示条件", flow_node_ids=[flow[1].id]),
                NavigationEdge(id="nav.afk.offer-hall", from_surface_id=surfaces[2].id, to_surface_id=surfaces[1].id, trigger="system back", flow_node_ids=[flow[2].id]),
                NavigationEdge(id="nav.afk.hall-detail", from_surface_id=surfaces[1].id, to_surface_id=surfaces[3].id, trigger="tap hero card", condition="无待展示礼包或礼包已退出", flow_node_ids=[flow[3].id]),
            ],
            notes=["商业化模态是条件分支，不是英雄升级核心循环的必经设计。"],
        )

        design_specs.append(
            DesignArtifactSpec(
                id="design-artifact.afk.hero-upgrade-wireflow",
                title="英雄厅浏览与商业化中断 Wireflow",
                kind="wireflow",
                artifact_id=wireflow.id,
                surface_ids=[item.id for item in surfaces],
                flow_node_ids=[item.id for item in flow],
                derived_from_artifact_ids=[item.id for item in screens.values()],
                generation_method="hybrid",
                source_ids=[live_source],
                run_id=self.DESIGN_RUN_ID,
                review_status="reviewed",
            )
        )

        interaction = InteractionSpec(
            id="interaction.afk.hero-upgrade",
            title="从主世界到升级确认前",
            trigger="玩家需要检查或提升英雄成长",
            preconditions=["英雄厅已解锁", "目标英雄已拥有"],
            steps=[
                InteractionStep(id="interaction-step.afk.enter", order=1, actor="player", action="点击英雄厅", response="打开英雄厅", state_before="主世界", state_after="英雄厅", surface_id=surfaces[0].id, ui_element_id="ui.afk.world.hero-hall-entry", flow_node_id=flow[0].id, source_ids=[live_source], artifact_ids=[screens["world"].id, screens["hero_hall"].id]),
                InteractionStep(id="interaction-step.afk.select", order=2, actor="player", action="点击罗万卡片", response="条件性显示礼包或打开详情", state_before="英雄厅", state_after="礼包/英雄详情", surface_id=surfaces[1].id, ui_element_id="ui.afk.hall.hero-card", flow_node_id=flow[1].id, source_ids=[live_source], artifact_ids=[screens["hero_hall"].id, screens["monetization_interrupt"].id]),
                InteractionStep(id="interaction-step.afk.recover", order=3, actor="player", action="如出现礼包则按返回", response="恢复英雄厅", state_before="礼包", state_after="英雄厅", surface_id=surfaces[2].id, ui_element_id="ui.afk.offer.exit", flow_node_id=flow[2].id, source_ids=[live_source], artifact_ids=[screens["monetization_interrupt"].id, screens["hero_hall"].id]),
                InteractionStep(id="interaction-step.afk.read", order=4, actor="system", action="渲染英雄详情", response="显示 305 永久等级、357 赛季等级、151 万战力与两项成本", state_before="英雄厅", state_after="升级条件可读", surface_id=surfaces[3].id, ui_element_id="ui.afk.detail.upgrade", flow_node_id=flow[4].id, source_ids=[live_source, "src.afk.hero-model-cost"], artifact_ids=[screens["hero_detail"].id]),
            ],
            postconditions=["玩家能在确认前判断资源是否足够", "未获资源变更授权时停在确认前"],
            branches=["限时礼包出现 / 不出现", "资源充足 / 不足", "确认 / 返回"],
            failure_recovery_ids=["failure.afk.offer-interrupt", "failure.afk.resource-shortage"],
            diagram_artifact_id=wireflow.id,
            source_ids=[live_source, "src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
            artifact_ids=[*([item.id for item in screens.values()]), wireflow.id],
            run_id=self.DESIGN_RUN_ID,
        )

        state_matrix = StateMatrix(
            id="state-matrix.afk.upgrade-button",
            title="升级按钮与路径状态矩阵",
            subject_id="ui.afk.detail.upgrade",
            dimensions=["资源充足性", "商业化中断", "资源变更授权"],
            cases=[
                StateCase(id="state.afk.observed-sufficient", state="资源充足、停在确认前", condition="金币 21567万≥13561 且材料 29950≥8518；本次未授权升级", visible=True, enabled=True, content="绿色升级按钮", feedback=["两项拥有量/需求量并列"], next_state="保持英雄详情", source_ids=[live_source], artifact_ids=[screens["hero_detail"].id]),
                StateCase(id="state.afk.insufficient", state="任一资源不足", condition="cost.owned < cost.required", visible=True, enabled=False, content="成本仍可见", feedback=["不足项提示", "不扣除资源"], next_state="英雄详情", source_ids=["src.afk.hero-model-cost"], artifact_ids=[screens["hero_detail"].id]),
                StateCase(id="state.afk.offer-interrupted", state="限时礼包中断", condition="礼包展示条件满足", visible=False, enabled=False, content="升级控件被全屏模态遮挡", feedback=["返回后恢复英雄厅"], next_state="英雄厅", source_ids=[live_source], artifact_ids=[screens["monetization_interrupt"].id]),
            ],
        )

        progression = ProgressionSpec(
            id="progression.afk.layered-levels",
            title="永久英雄等级、赛季共鸣与共鸣之脉三层成长",
            axes=[
                ProgressionAxis(id="axis.afk.permanent-level", name="永久英雄等级", unit="级", stages=["罗万 305"], gates=["永久成长资源"], resets=[]),
                ProgressionAxis(id="axis.afk.season-level", name="赛季共鸣等级", unit="级", stages=["英雄厅 357", "罗万 357"], gates=["金币", "赛季经验材料", "赛季阶段上限"], resets=["赛季切换"]),
                ProgressionAxis(id="axis.afk.resonance-vein", name="共鸣之脉", unit="加成等级", stages=["+120"], gates=["独立系统进度"], resets=[]),
            ],
            pacing=["普通等级消耗可逐次推进", "关键资源节点可能形成等待门", "英雄厅提供快速升级以压缩重复操作"],
            cross_system_effects=["总战力", "队伍战斗属性", "赛季玩法推进"],
            source_ids=[live_source, "src.afk.hero-model-cost", "voice.afk.seasonal-resonance-legibility"],
            artifact_ids=[screens["hero_hall"].id, screens["hero_detail"].id],
            run_id=screens["hero_hall"].run_id,
        )
        balance = BalanceSpec(
            id="balance.afk.level-357-preview",
            title="赛季等级 357 的可见成本样本",
            target_experience="让玩家在确认前同时理解下一步成本、库存余量与成长层级。",
            parameters=[
                BalanceParameter(id="balance-param.afk.coin-cost", name="金币成本", value_or_range="13561（界面单位与金币库存同标度）", tuning_role="控制常规升级频率", constraints=["当前拥有 21567 万"], source_ids=[live_source, "src.afk.hero-model-cost"]),
                BalanceParameter(id="balance-param.afk.manual-cost", name="赛季经验材料成本", value_or_range="8518", tuning_role="控制赛季等级推进速度", constraints=["当前拥有 29950"], source_ids=[live_source, "src.afk.hero-model-cost"]),
                BalanceParameter(id="balance-param.afk.season-level", name="当前赛季等级", value_or_range="357", unit="级", tuning_role="选择目标等级对应成本与属性区间", source_ids=[live_source]),
            ],
            mechanism_ids=["afk.mechanism.level-up-cost", "afk.mechanism.resonance-layering"],
            notes=["这里只记录一个账号、一个等级点的外部可见样本，不外推全等级曲线。"],
        )
        feedback = FeedbackSpec(
            id="feedback.afk.upgrade-preview",
            title="升级确认前反馈",
            trigger="打开英雄详情或资源变化",
            channels=["visual", "text", "numeric"],
            timing="详情页稳定后立即",
            success_behavior="绿色主按钮内显示两项资源拥有量/需求量，赛季等级与战力置于按钮上方。",
            failure_behavior="源码规则阻止资源不足升级并保留当前等级；具体不足态画面仍待可复位夹具采集。",
            surface_ids=[surfaces[3].id],
            ui_element_ids=["ui.afk.detail.upgrade", "ui.afk.detail.cost-coins", "ui.afk.detail.cost-manual"],
            source_ids=[live_source, "src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
            artifact_ids=[screens["hero_detail"].id],
            run_id=screens["hero_detail"].run_id,
        )
        tutorial = TutorialSpec(
            id="tutorial.afk.hero-upgrade",
            title="客户端英雄升级引导状态机",
            steps=[
                TutorialStep(id="tutorial-step.afk.entry", trigger="教程要求打开英雄系统", instruction="定位并点击主界面英雄按钮", allowed_actions=["tap hero entry"], blocked_actions=["unrelated main-view input"], completion_condition="英雄列表打开", recovery="重新定位主界面按钮", flow_node_ids=[flow[0].id]),
                TutorialStep(id="tutorial-step.afk.hero", trigger="英雄列表稳定", instruction="定位目标英雄并打开详情", allowed_actions=["scroll hero list", "tap target hero"], blocked_actions=["unrelated hero cards"], completion_condition="英雄详情打开", recovery="返回列表重新定位", flow_node_ids=[flow[1].id, flow[3].id]),
                TutorialStep(id="tutorial-step.afk.upgrade", trigger="详情页升级按钮可见", instruction="点击 btn_upgrade", allowed_actions=["tap upgrade"], blocked_actions=["unrelated detail controls"], completion_condition="升级步骤完成", recovery="返回详情重新定位升级按钮", flow_node_ids=[flow[5].id]),
            ],
            skippable=None,
            repeat_behavior="任务完成后清理遮罩和事件绑定，不重复强制。",
            source_ids=["src.afk.hero-upgrade-tutorial"],
            artifact_ids=[screens["world"].id, screens["hero_hall"].id, screens["hero_detail"].id],
            run_id=screens["hero_detail"].run_id,
        )
        failures = [
            FailureRecoverySpec(id="failure.afk.offer-interrupt", title="限时礼包打断英雄选择", failure_condition="英雄卡片点击触发礼包展示条件", visible_behavior="全屏礼包覆盖英雄厅并突出价格/免费链", retained_state="英雄厅和已选目标未发生资源变化", recovery_action="系统返回后再次选择英雄", irreversible_effects=[], flow_node_ids=[flow[1].id, flow[2].id], source_ids=[live_source], artifact_ids=[screens["monetization_interrupt"].id, screens["hero_hall"].id], run_id=screens["monetization_interrupt"].run_id),
            FailureRecoverySpec(id="failure.afk.resource-shortage", title="升级资源不足", failure_condition="任一 GetLevelUpCost 项的拥有量小于需求量", visible_behavior="升级不可完成并显示不足项", retained_state="等级、属性与库存不变", recovery_action="获得足够资源后重新进入或刷新详情", irreversible_effects=[], flow_node_ids=[flow[5].id], source_ids=["src.afk.hero-model-cost"], artifact_ids=[screens["hero_detail"].id], run_id=screens["hero_detail"].run_id),
        ]
        dependencies = [
            DependencySpec(id="dependency.afk.hero-module", title="英雄系统解锁", direction="upstream", target_system_id="hero-roster", dependency="主世界必须显示英雄厅一级入口，且账户已拥有英雄。", source_ids=[live_source, "src.afk.hero-upgrade-tutorial"], artifact_ids=[screens["world"].id, screens["hero_hall"].id], run_id=screens["world"].run_id),
            DependencySpec(id="dependency.afk.season-context", title="赛季成长上下文", direction="shared", target_system_id="season-progression", dependency="赛季共鸣、赛季装备和赛季资源共同决定英雄厅当前呈现。", source_ids=[live_source, "src.afk.hero-model-cost"], artifact_ids=[screens["hero_hall"].id, screens["hero_detail"].id], run_id=screens["hero_hall"].run_id),
            DependencySpec(id="dependency.afk.liveops-offer", title="Live Ops 展示调度", direction="upstream", target_system_id="liveops-offer", dependency="礼包展示条件可在英雄卡片到详情的导航边上插入全屏模态。", source_ids=[live_source], artifact_ids=[screens["monetization_interrupt"].id], run_id=screens["monetization_interrupt"].run_id),
        ]

        all_artifacts = [*screens.values(), *visual_design_artifacts]
        runs = self._run_refs(screens, visual_design_artifacts)
        benchmark = BenchmarkTask(
            id="task.afk.hero-upgrade-observation.v2",
            title="MuMu 外部观测与源码 oracle 联合校验英雄升级前置状态",
            start_state="AFK Journey CN 1.7.21 main world; hero hall unlocked",
            goal="到达英雄详情并从画面提取等级、战力和两项资源数；不执行升级",
            allowed_actions=["tap", "wait", "back", "capture"],
            reset_method="system back to hero hall; package restart if needed; no account snapshot mutation",
            checks=[
                ObjectiveCheck(id="path-reaches-hero-detail", description="真实路径从主世界到英雄详情", expected=True),
                ObjectiveCheck(id="ocr-season-level", description="OCR 提取赛季等级", expected=str(manifest.observed_fields["season_level"])),
                ObjectiveCheck(id="ocr-base-level", description="OCR 提取永久等级", expected=str(manifest.observed_fields["base_level"])),
                ObjectiveCheck(id="ocr-combat-power", description="OCR 提取战力显示", expected=str(manifest.observed_fields["combat_power"])),
                ObjectiveCheck(id="ocr-two-costs", description="OCR 同时提取两项拥有量/需求量", expected=[str(manifest.observed_fields[key]) for key in ("coin_owned", "coin_cost", "manual_owned", "manual_cost")]),
                ObjectiveCheck(id="source-formula-present", description="源码仍含 GetLevelUpCost 与升级入口", expected=True),
                ObjectiveCheck(id="no-resource-mutation", description="本次验证没有执行升级或购买", expected=True),
            ],
            note="这是外部视角内容设施的客观提取 benchmark；资源扣除型 benchmark 需要独立可复位快照和明确授权。",
            metadata={"manifest": manifest.model_dump(mode="json"), "source_oracle": source_evidence},
        )

        object_map = {
            "scope": [scope.id],
            "system_overview": [overview.id, concept.id],
            "player_goals": [player_goal.id],
            "entry_unlock": [entry.id],
            "core_loop": [core_loop.id],
            "information_architecture": [architecture.id],
            "surface_design": [*[item.id for item in layout_specs], *[item.id for item in design_specs if item.kind == "wireframe"]],
            "interaction_flow": [interaction.id, "design-artifact.afk.hero-upgrade-wireflow"],
            "state_matrix": [state_matrix.id],
            "rules_mechanics": [item.id for item in mechanisms],
            "resources_economy": [resource_model.id, *[item.id for item in resources], monetization.id],
            "progression_balance": [progression.id, balance.id],
            "feedback": [feedback.id],
            "tutorial": [tutorial.id],
            "failure_recovery": [item.id for item in failures],
            "dependencies": [item.id for item in dependencies],
            "player_voice": [item.id for item in player_voices],
            "version_provenance": [scope.id, version_note.id, *source_ids],
        }
        coverage = [
            DesignSectionCoverage(section=section, status="complete", object_ids=object_ids, rationale="由 2026-07-13 MuMu 实景、客户端源码或已保留链接的玩家反馈支持。")
            for section, object_ids in object_map.items()
        ]
        design_spec = ReverseEngineeredGameDesignSpec(
            id="design-spec.afk.hero-hall-season-upgrade.v1",
            title=concept.title,
            scope_id=scope.id,
            system_instance_id=instance.id,
            overview=[overview],
            player_goals=[player_goal],
            entry_and_unlock=[entry],
            core_loop=core_loop,
            information_architecture=architecture,
            design_artifacts=design_specs,
            layout_specs=layout_specs,
            interaction_specs=[interaction],
            state_matrices=[state_matrix],
            progression_specs=[progression],
            balance_specs=[balance],
            feedback_specs=[feedback],
            tutorial_specs=[tutorial],
            failure_recovery_specs=failures,
            dependency_specs=dependencies,
            monetization_specs=[monetization],
            version_notes=[version_note],
            mechanism_ids=[item.id for item in mechanisms],
            resource_model_id=resource_model.id,
            resource_relation_ids=[item.id for item in resources],
            player_voice_ids=[item.id for item in player_voices],
            section_coverage=coverage,
            source_ids=source_ids,
            artifact_ids=[item.id for item in all_artifacts],
            run_ids=[item.id for item in runs],
        )
        report = GameReport(
            id=self.REPORT_ID,
            slug=self.SLUG,
            game_id="afk-journey",
            game_title="AFK Journey",
            system_id=concept.id,
            system_title=concept.title,
            summary=summary,
            contract_version="reverse-engineered-game-design-spec.v0.3",
            migration_status="publishable",
            design_spec=design_spec,
            summary_claim=Claim(id="claim.afk.summary", kind=ContentKind.analyst_interpretation, statement=summary, source_ids=[live_source, "src.afk.hero-upgrade-view", "src.afk.hero-model-cost", *[item.source_id for item in player_voices]], artifact_ids=[screens["hero_hall"].id, screens["hero_detail"].id], run_id=screens["hero_hall"].run_id, review_status="reviewed"),
            scope=scope,
            game=Game(id="afk-journey", title="AFK Journey", aliases=["剑与远征：启程"], platforms=["android", "ios", "windows"], source_ids=[live_source]),
            system_concept=concept,
            system_instance=instance,
            resource_model=resource_model,
            tags=["mobile", "android", "mumu", "hero-hall", "hero-upgrade", "seasonal-progression", "resonance", "resource-cost", "liveops-interruption", "source-oracle", "player-voice"],
            status="published",
            cover_artifact_id=screens["hero_hall"].id,
            sources=sources,
            artifacts=all_artifacts,
            runs=runs,
            surfaces=surfaces,
            claims=[],
            flow=flow,
            mechanisms=mechanisms,
            resources=resources,
            player_voices=player_voices,
            benchmark_task=benchmark,
            observations=[
                Observation(id="obs.afk.real-path", statement="MuMu 实测从主世界进入英雄厅，并在退出礼包后打开罗万详情。", source_ids=[live_source], artifact_ids=[item.id for item in screens.values()], run_id=screens["hero_detail"].run_id),
                Observation(id="obs.afk.visible-values", statement="详情页可见永久等级 305、赛季等级 357、战力 151 万、金币 21567万/13561 与材料 29950/8518。", source_ids=[live_source], artifact_ids=[screens["hero_detail"].id], run_id=screens["hero_detail"].run_id),
                Observation(id="obs.afk.source-cost", statement="当前源码仍通过 GetLevelUpCost 计算下一等级多项成本，并对每项 IsEnough。", source_ids=["src.afk.hero-upgrade-view", "src.afk.hero-model-cost"]),
            ],
            interpretations=[
                Claim(id="claim.afk.layered-legibility", kind=ContentKind.analyst_interpretation, statement="英雄厅用全队共鸣降低逐英雄成长的组织成本，但永久等级、赛季等级和共鸣之脉并行仍增加解释负担。", source_ids=[live_source, "voice.afk.seasonal-resonance-legibility"], artifact_ids=[screens["hero_hall"].id, screens["hero_detail"].id], run_id=screens["hero_hall"].run_id, review_status="reviewed"),
                Claim(id="claim.afk.offer-friction", kind=ContentKind.analyst_interpretation, statement="礼包插入英雄卡到详情的核心浏览边，会增加路径不确定性；返回恢复原状态降低了不可逆风险。", source_ids=[live_source], artifact_ids=[screens["monetization_interrupt"].id, screens["hero_hall"].id], run_id=screens["monetization_interrupt"].run_id, review_status="reviewed"),
            ],
            open_questions=["资源不足时升级按钮的颜色、文案与跳转入口是什么？需要可复位夹具采集。", "升级成功后的属性、音效、动画与资源扣除是否与源码 oracle 完全一致？本次没有执行资源变更。", "限时礼包的展示频率和触发条件是什么？外部观察无法推断内部运营配置。"],
        )
        report.assert_publishable()
        assert_public_artifacts(report)
        return report

    @staticmethod
    def _ocr_tokens(path: Path) -> list[dict[str, Any]]:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - environment boundary
            raise RuntimeError("rapidocr_onnxruntime is required for AFK visual benchmark") from exc
        result, _ = RapidOCR()(str(path))
        return [
            {"text": str(item[1]), "score": float(item[2]), "box": item[0]}
            for item in (result or [])
        ]

    def verify(self, manifest: AfkLiveEvidenceManifest, report: GameReport) -> dict[str, Any]:
        detail_source = self.store.get_artifact(manifest.screenshot_artifact_ids["hero_detail"])
        if detail_source is None:
            raise ValueError("AFK hero-detail source artifact is missing")
        tokens = self._ocr_tokens(Path(detail_source.path))
        digit_stream = " ".join(re.sub(r"[^0-9/]", "", item["text"]) for item in tokens)

        def contains(value: str | int | float) -> bool:
            return str(value) in digit_stream

        expected_costs = [
            manifest.observed_fields[key]
            for key in ("coin_owned", "coin_cost", "manual_owned", "manual_cost")
        ]
        checks = [
            {"id": "publishable-design-spec", "passed": not report.publication_issues(), "actual": report.publication_issues()},
            {"id": "four-real-surface-frames", "passed": len(report.surfaces) == 4 and all(surface.artifact_ids for surface in report.surfaces), "actual": len(report.surfaces)},
            {"id": "ocr-season-level", "passed": contains(manifest.observed_fields["season_level"]), "actual": digit_stream},
            {"id": "ocr-base-level", "passed": contains(manifest.observed_fields["base_level"]), "actual": digit_stream},
            {"id": "ocr-combat-power", "passed": contains(manifest.observed_fields["combat_power"]), "actual": digit_stream},
            {"id": "ocr-two-costs", "passed": all(contains(value) for value in expected_costs), "actual": digit_stream},
            {"id": "source-formula-present", "passed": AfkHeroUpgradeOracle(Path(manifest.source_root)).source_evidence()["ok"], "actual": "GetLevelUpCost / nextLevel / btn_upgrade"},
            {"id": "no-resource-mutation", "passed": manifest.upgrade_executed is False, "actual": manifest.upgrade_executed},
            {"id": "design-artifact-pairing", "passed": len(report.design_spec.design_artifacts) >= len(report.surfaces) + 1 if report.design_spec else False, "actual": len(report.design_spec.design_artifacts) if report.design_spec else 0},
        ]
        payload = {
            "schema": "game-observatory.afk-mumu-hero-upgrade-observation.v2",
            "generated_at": utc_now(),
            "ok": all(item["passed"] for item in checks),
            "boundary": "Read-only content extraction benchmark; no upgrade, purchase, reward claim, or account setting mutation.",
            "manifest": manifest.model_dump(mode="json"),
            "report_id": report.id,
            "checks": checks,
            "ocr": {"engine": "rapidocr_onnxruntime", "tokens": tokens},
            "source_oracle": AfkHeroUpgradeOracle(Path(manifest.source_root)).source_evidence(),
        }
        path = self.store.export_root / "afk-mumu-hero-upgrade-observation.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["path"] = str(path)
        return payload

    def promote(self, manifest: AfkLiveEvidenceManifest) -> dict[str, Any]:
        report = self.build(manifest)
        verification = self.verify(manifest, report)
        if not verification["ok"]:
            raise ValueError("AFK live design verification failed")
        self.store.upsert_report(report)
        return {"report": report, "verification": verification}