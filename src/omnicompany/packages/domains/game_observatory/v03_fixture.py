from __future__ import annotations

import hashlib
from pathlib import Path

from omnicompany.packages.domains.game_observatory.content import seed_reports
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    BalanceParameter,
    BalanceSpec,
    ContentKind,
    CoreLoopSpec,
    CoreLoopStep,
    DependencySpec,
    DesignArtifactSpec,
    DesignSectionCoverage,
    DesignStatement,
    FailureRecoverySpec,
    FeedbackSpec,
    InformationArchitectureSpec,
    InteractionSpec,
    InteractionStep,
    LayoutElementSpec,
    LayoutSpec,
    NavigationEdge,
    NormalizedRect,
    ProgressionAxis,
    ProgressionSpec,
    ReverseEngineeredGameDesignSpec,
    RunRef,
    StateCase,
    StateMatrix,
    TutorialSpec,
    TutorialStep,
)


def _artifact(root: Path, artifact_id: str, kind: str, run_id: str) -> ArtifactRef:
    suffix = ".png" if kind == "screenshot" else ".svg"
    path = root / f"{artifact_id.replace('.', '-')}{suffix}"
    if kind == "screenshot":
        body = b"\x89PNG\r\
\x1a\
contract-fixture"
        media_type = "image/png"
    else:
        body = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
        media_type = "image/svg+xml"
    path.write_bytes(body)
    return ArtifactRef(
        id=artifact_id,
        kind=kind,
        path=str(path),
        sha256=hashlib.sha256(body).hexdigest(),
        run_id=run_id,
        media_type=media_type,
        metadata={"public": True, "fixture": True},
    )


def publishable_v03_report(root: Path):
    report = seed_reports()[0].model_copy(deep=True)
    assert report.system_concept is not None
    assert report.system_instance is not None
    assert report.resource_model is not None
    root.mkdir(parents=True, exist_ok=True)
    source_id = report.sources[0].id
    run_id = "run.v03.contract-fixture"

    screenshots: list[ArtifactRef] = []
    wireframes: list[ArtifactRef] = []
    design_artifacts: list[DesignArtifactSpec] = []
    layout_specs: list[LayoutSpec] = []
    for index, surface in enumerate(report.surfaces, start=1):
        screenshot = _artifact(root, f"art.v03.surface.{index}", "screenshot", run_id)
        wireframe = _artifact(root, f"art.v03.wireframe.{index}", "wireframe", run_id)
        screenshots.append(screenshot)
        wireframes.append(wireframe)
        surface.artifact_ids = [screenshot.id]
        surface.run_id = run_id
        element = surface.elements[0]
        if element.bounds is None:
            element.bounds = NormalizedRect(x=0.1, y=0.1, width=0.8, height=0.2)
        design_artifacts.append(
            DesignArtifactSpec(
                id=f"design-artifact.v03.wireframe.{index}",
                title=f"{surface.title}反推线框图",
                kind="wireframe",
                artifact_id=wireframe.id,
                surface_ids=[surface.id],
                derived_from_artifact_ids=[screenshot.id],
                generation_method="manual_reconstruction",
                source_ids=[source_id],
                run_id=run_id,
                review_status="reviewed",
            )
        )
        layout_specs.append(
            LayoutSpec(
                id=f"layout.v03.{index}",
                surface_id=surface.id,
                canvas_aspect_ratio="16:9",
                elements=[
                    LayoutElementSpec(
                        id=f"layout-element.v03.{index}",
                        ui_element_id=element.id,
                        bounds=element.bounds,
                        anchors=["safe-area"],
                    )
                ],
                constraints=["关键控件保持在安全区内"],
                source_ids=[source_id],
                artifact_ids=[screenshot.id, wireframe.id],
                run_id=run_id,
            )
        )

    wireflow = _artifact(root, "art.v03.wireflow", "wireflow", run_id)
    design_artifacts.append(
        DesignArtifactSpec(
            id="design-artifact.v03.wireflow",
            title="英雄升级主流程 Wireflow",
            kind="wireflow",
            artifact_id=wireflow.id,
            flow_node_ids=[item.id for item in report.flow],
            derived_from_artifact_ids=[item.id for item in screenshots],
            generation_method="hybrid",
            source_ids=[source_id],
            run_id=run_id,
            review_status="reviewed",
        )
    )
    all_artifacts = [*screenshots, *wireframes, wireflow]
    report.artifacts = all_artifacts
    report.runs = [
        RunRef(
            id=run_id,
            target_id="fixture://v03",
            adapter="contract-fixture",
            started_at="2026-07-13T00:00:00+08:00",
            ended_at="2026-07-13T00:01:00+08:00",
            status="passed",
            build_scope_id=report.scope.id,
            artifact_ids=[item.id for item in all_artifacts],
        )
    ]
    report.cover_artifact_id = screenshots[0].id
    report.scope.run_id = run_id
    report.scope.artifact_ids = [screenshots[0].id]
    report.system_title = report.system_concept.title
    report.system_instance.run_ids = [run_id]
    report.system_instance.artifact_ids = [screenshots[0].id]
    for index, node in enumerate(report.flow):
        screenshot = screenshots[index % len(screenshots)]
        surface = report.surfaces[index % len(report.surfaces)]
        node.artifact_ids = [screenshot.id]
        node.surface_ids = [surface.id]
        node.run_id = run_id
    for voice in report.player_voices:
        target = voice.system_node_id or report.flow[0].id
        voice.system_node_id = target
        voice.target_object_ids = [target]

    overview = DesignStatement(
        id="statement.v03.overview",
        title="系统定位",
        statement="玩家消耗成长资源提升单个英雄等级并立即观察属性变化。",
        kind=ContentKind.direct_observation,
        source_ids=[source_id],
        artifact_ids=[screenshots[0].id],
        run_id=run_id,
    )
    player_goal = DesignStatement(
        id="statement.v03.player-goal",
        title="玩家目标",
        statement="把目标英雄提升一级并确认战斗属性增长。",
        kind=ContentKind.analyst_interpretation,
        source_ids=[source_id],
        artifact_ids=[screenshots[1].id],
        run_id=run_id,
    )
    entry = DesignStatement(
        id="statement.v03.entry",
        title="入口与解锁",
        statement="英雄系统解锁后从主界面入口进入英雄列表。",
        kind=ContentKind.direct_observation,
        source_ids=[source_id],
        artifact_ids=[screenshots[0].id],
        run_id=run_id,
    )
    version_note = DesignStatement(
        id="statement.v03.version",
        title="版本范围",
        statement="本对象只覆盖合同测试夹具指定的版本范围。",
        kind=ContentKind.direct_observation,
        source_ids=[source_id],
        artifact_ids=[screenshots[0].id],
        run_id=run_id,
    )
    core_loop = CoreLoopSpec(
        id="core-loop.v03.hero-upgrade",
        title="选择—预览—确认—反馈",
        player_goal="提升目标英雄等级",
        entry_conditions=["英雄系统已解锁"],
        exit_conditions=["升级成功或资源不足"],
        steps=[
            CoreLoopStep(
                id="core-loop-step.v03.select",
                title="选择目标英雄",
                player_action="点击英雄卡",
                system_response="打开英雄详情",
                state_before="英雄列表",
                state_after="英雄详情",
                flow_node_ids=[report.flow[1].id],
                source_ids=[source_id],
                artifact_ids=[screenshots[0].id],
                run_id=run_id,
            ),
            CoreLoopStep(
                id="core-loop-step.v03.confirm",
                title="确认升级",
                player_action="点击升级按钮",
                system_response="扣除资源并刷新等级与属性",
                state_before="升级预览",
                state_after="升级结果",
                flow_node_ids=[report.flow[3].id],
                source_ids=[source_id],
                artifact_ids=[screenshots[2].id],
                run_id=run_id,
            ),
        ],
    )
    architecture = InformationArchitectureSpec(
        id="ia.v03.hero-upgrade",
        root_surface_ids=[report.surfaces[0].id],
        surface_ids=[item.id for item in report.surfaces],
        edges=[
            NavigationEdge(
                id="nav.v03.list-detail",
                from_surface_id=report.surfaces[0].id,
                to_surface_id=report.surfaces[1].id,
                trigger="点击英雄卡",
                flow_node_ids=[report.flow[1].id],
            ),
            NavigationEdge(
                id="nav.v03.detail-preview",
                from_surface_id=report.surfaces[1].id,
                to_surface_id=report.surfaces[2].id,
                trigger="点击升级",
                flow_node_ids=[report.flow[2].id],
            ),
        ],
    )
    interaction = InteractionSpec(
        id="interaction.v03.upgrade",
        title="完成一次英雄升级",
        trigger="玩家从英雄列表选择目标英雄",
        preconditions=["英雄系统已解锁", "账号拥有目标英雄"],
        steps=[
            InteractionStep(
                id="interaction-step.v03.open",
                order=1,
                actor="player",
                action="点击升级按钮",
                response="系统显示消耗和属性预览",
                state_before="英雄详情",
                state_after="升级预览",
                surface_id=report.surfaces[2].id,
                ui_element_id=report.surfaces[2].elements[0].id,
                flow_node_id=report.flow[2].id,
                source_ids=[source_id],
                artifact_ids=[screenshots[2].id],
            )
        ],
        postconditions=["升级成功或显示资源不足"],
        branches=["资源充足", "资源不足"],
        failure_recovery_ids=["failure.v03.resource-shortage"],
        diagram_artifact_id=wireflow.id,
        source_ids=[source_id],
        artifact_ids=[screenshots[2].id, wireflow.id],
        run_id=run_id,
    )
    matrix = StateMatrix(
        id="state-matrix.v03.upgrade-button",
        title="升级按钮状态矩阵",
        subject_id=report.surfaces[2].elements[0].id,
        dimensions=["资源是否充足"],
        cases=[
            StateCase(
                id="state.v03.available",
                state="可升级",
                condition="资源充足",
                visible=True,
                enabled=True,
                feedback=["显示消耗", "点击后属性增长"],
                next_state="升级成功",
                source_ids=[source_id],
                artifact_ids=[screenshots[2].id],
            ),
            StateCase(
                id="state.v03.insufficient",
                state="资源不足",
                condition="任一资源不足",
                visible=True,
                enabled=True,
                feedback=["显示不足反馈"],
                next_state="升级预览",
                source_ids=[source_id],
                artifact_ids=[screenshots[2].id],
            ),
        ],
    )
    progression = ProgressionSpec(
        id="progression.v03.hero-level",
        title="英雄等级成长",
        axes=[
            ProgressionAxis(
                id="progression-axis.v03.level",
                name="英雄等级",
                unit="级",
                stages=["当前等级", "目标等级"],
                gates=["升级资源"],
            )
        ],
        pacing=["逐级升级"],
        source_ids=[source_id],
        artifact_ids=[screenshots[2].id],
        run_id=run_id,
    )
    balance = BalanceSpec(
        id="balance.v03.hero-upgrade",
        title="升级成本与属性增量",
        target_experience="每次确认都能理解成本和收益",
        parameters=[
            BalanceParameter(
                id="balance-parameter.v03.cost",
                name="目标等级成本",
                value_or_range="由目标等级和赛季态决定",
                tuning_role="控制成长节奏",
                source_ids=[source_id],
            )
        ],
        mechanism_ids=[report.mechanisms[1].id],
    )
    feedback = FeedbackSpec(
        id="feedback.v03.upgrade",
        title="升级结果反馈",
        trigger="升级确认",
        channels=["visual", "animation", "text", "numeric"],
        timing="确认后立即",
        success_behavior="等级和属性数值刷新并展示变化",
        failure_behavior="保持原状态并说明资源不足",
        surface_ids=[report.surfaces[2].id],
        ui_element_ids=[report.surfaces[2].elements[0].id],
        source_ids=[source_id],
        artifact_ids=[screenshots[2].id],
        run_id=run_id,
    )
    tutorial = TutorialSpec(
        id="tutorial.v03.hero-upgrade",
        title="首次英雄升级引导",
        steps=[
            TutorialStep(
                id="tutorial-step.v03.open",
                trigger="首次进入英雄升级",
                instruction="点击升级按钮",
                allowed_actions=["tap upgrade"],
                blocked_actions=["drag unrelated list"],
                completion_condition="打开升级预览",
                recovery="返回英雄详情重新定位按钮",
                flow_node_ids=[report.flow[2].id],
            )
        ],
        repeat_behavior="完成后不重复强制",
        source_ids=[source_id],
        artifact_ids=[screenshots[2].id],
        run_id=run_id,
    )
    failure = FailureRecoverySpec(
        id="failure.v03.resource-shortage",
        title="升级资源不足",
        failure_condition="任一升级资源不足",
        visible_behavior="显示资源不足并保持升级预览",
        retained_state="英雄等级和资源不变",
        recovery_action="补充资源后再次确认",
        flow_node_ids=[report.flow[3].id],
        source_ids=[source_id],
        artifact_ids=[screenshots[2].id],
        run_id=run_id,
    )
    dependency = DependencySpec(
        id="dependency.v03.hero-roster",
        title="英雄系统解锁依赖",
        direction="upstream",
        target_system_id="hero-roster",
        dependency="英雄系统和目标英雄必须已解锁",
        source_ids=[source_id],
        artifact_ids=[screenshots[0].id],
        run_id=run_id,
    )

    object_map = {
        "scope": [report.scope.id],
        "system_overview": [overview.id],
        "player_goals": [player_goal.id],
        "entry_unlock": [entry.id],
        "core_loop": [core_loop.id],
        "information_architecture": [architecture.id],
        "surface_design": [
            *[item.id for item in layout_specs],
            *[item.id for item in design_artifacts if item.kind == "wireframe"],
        ],
        "interaction_flow": [interaction.id, "design-artifact.v03.wireflow"],
        "state_matrix": [matrix.id],
        "rules_mechanics": [item.id for item in report.mechanisms],
        "resources_economy": [report.resource_model.id, *[item.id for item in report.resources]],
        "progression_balance": [progression.id, balance.id],
        "feedback": [feedback.id],
        "tutorial": [tutorial.id],
        "failure_recovery": [failure.id],
        "dependencies": [dependency.id],
        "player_voice": [item.id for item in report.player_voices],
        "version_provenance": [report.scope.id, version_note.id, source_id],
    }
    coverage = [
        DesignSectionCoverage(
            section=section,
            status="complete",
            object_ids=object_ids,
            rationale="合同测试夹具提供了对应设计对象和证据。",
        )
        for section, object_ids in object_map.items()
    ]
    report.design_spec = ReverseEngineeredGameDesignSpec(
        id="design-spec.afk.hero-upgrade.v03-fixture",
        title=report.system_concept.title,
        scope_id=report.scope.id,
        system_instance_id=report.system_instance.id,
        overview=[overview],
        player_goals=[player_goal],
        entry_and_unlock=[entry],
        core_loop=core_loop,
        information_architecture=architecture,
        design_artifacts=design_artifacts,
        layout_specs=layout_specs,
        interaction_specs=[interaction],
        state_matrices=[matrix],
        progression_specs=[progression],
        balance_specs=[balance],
        feedback_specs=[feedback],
        tutorial_specs=[tutorial],
        failure_recovery_specs=[failure],
        dependency_specs=[dependency],
        version_notes=[version_note],
        mechanism_ids=[item.id for item in report.mechanisms],
        resource_model_id=report.resource_model.id,
        resource_relation_ids=[item.id for item in report.resources],
        player_voice_ids=[item.id for item in report.player_voices],
        section_coverage=coverage,
        source_ids=[item.id for item in report.sources],
        artifact_ids=[item.id for item in all_artifacts],
        run_ids=[run_id],
    )
    report.contract_version = "reverse-engineered-game-design-spec.v0.3"
    report.migration_status = "review_ready"
    report.status = "review"
    return report