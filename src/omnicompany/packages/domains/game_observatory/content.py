from __future__ import annotations

import json
from pathlib import Path

from .models import (
    ArtifactRef,
    BenchmarkTask,
    BuildScope,
    Claim,
    ContentKind,
    FlowNode,
    Game,
    GameReport,
    MechanismSpec,
    NormalizedRect,
    ObjectiveCheck,
    Observation,
    PlayerVoice,
    ResourceDefinition,
    ResourceModel,
    ResourceRelation,
    ReverseEngineeredGameDesignSpec,
    RunRef,
    SourceRef,
    Surface,
    SystemConcept,
    SystemInstance,
    TraceEvent,
    UIElementInstance,
)


def afk_hero_upgrade_task() -> BenchmarkTask:
    return BenchmarkTask(
        id="task.afk.hero-upgrade.v1",
        title="从获准的可复位账号快照完成一次英雄升级",
        start_state="authorized snapshot=unknown; current runtime account Hero=false",
        goal="目标英雄等级增加 1，资源与属性变化同时匹配外部观察和白盒 oracle",
        allowed_actions=["tap", "swipe", "wait", "back", "reset"],
        reset_method="restore an authorized isolated account snapshot; currently unavailable",
        checks=[
            ObjectiveCheck(id="hero_level_delta", description="目标英雄等级严格增加 1", expected=1),
            ObjectiveCheck(id="resource_delta_matches_oracle", description="资源扣减与白盒成本一致", expected=True),
            ObjectiveCheck(id="attributes_match_oracle", description="HP/ATK/DEF 与白盒计算一致", expected=True),
            ObjectiveCheck(id="ui_before_after_visible", description="等级、资源与属性均有前后画面", expected=True),
            ObjectiveCheck(id="source_formula_present", description="源码仍存在成本与预览计算入口", expected=True),
        ],
        note="任务合同已冻结；真实执行由账号 Hero 解锁和获准的可复位资源快照门禁。",
        metadata={"execution_status": "blocked", "blocker": "Hero=false; resettable snapshot unavailable"},
    )


def ensure_report_surfaces(report: GameReport) -> GameReport:
    """Backfill the canonical page/UI layer without replacing editorial content.

    Early fixtures exercised Surface's schema but never instantiated it.  This
    migration is intentionally deterministic and idempotent so old revisions can
    acquire semantic layouts while voices, annotations and reviewer edits remain
    untouched.
    """
    if report.slug == "afk-journey-hero-upgrade":
        updates: dict[str, object] = {}
        if report.benchmark_task is None:
            updates["benchmark_task"] = afk_hero_upgrade_task()
        if report.scope.account_stage == "hero-upgrade-unlocked":
            updates["scope"] = report.scope.model_copy(
                update={"account_stage": "source-analysis; runtime benchmark locked (Hero=false)"}
            )
        if updates:
            report = report.model_copy(update=updates)
    if report.surfaces:
        return report

    def element(
        element_id: str,
        role: str,
        label: str,
        *,
        text: str | None = None,
        actions: list[str] | None = None,
        sources: list[str] | None = None,
        artifacts: list[str] | None = None,
        parent_id: str | None = None,
        bounds: tuple[float, float, float, float] | None = None,
    ) -> UIElementInstance:
        return UIElementInstance(
            id=element_id,
            role=role,
            label=label,
            text=text,
            actions=actions or [],
            source_ids=sources or [],
            artifact_ids=artifacts or [],
            parent_id=parent_id,
            bounds=(
                NormalizedRect(x=bounds[0], y=bounds[1], width=bounds[2], height=bounds[3])
                if bounds
                else None
            ),
        )

    source_ids = {item.id for item in report.sources}
    run_id = report.runs[0].id if report.runs else None
    surfaces: list[Surface]
    if report.slug == "afk-journey-hero-upgrade":
        view = "src.afk.hero-upgrade-view"
        tutorial = "src.afk.hero-upgrade-tutorial"
        surfaces = [
            Surface(
                id="surface.afk.hero-list",
                title="英雄列表",
                kind="page",
                description="列表卡片承担目标英雄选择；教程会滚动并暂时收窄可交互区域。",
                source_ids=[tutorial],
                elements=[
                    element("ui.afk.hero-list.back", "button", "返回", actions=["tap"], sources=[tutorial]),
                    element("ui.afk.hero-list.card", "listitem", "目标英雄卡", actions=["tap"], sources=[tutorial]),
                    element("ui.afk.hero-list.scroll", "list", "英雄滚动列表", actions=["swipe"], sources=[tutorial]),
                ],
            ),
            Surface(
                id="surface.afk.hero-detail",
                title="英雄详情",
                kind="page",
                description="详情页把英雄身份、当前等级、属性与升级入口组织在同一反馈面。",
                source_ids=[view, tutorial],
                elements=[
                    element("ui.afk.hero-detail.identity", "heading", "英雄身份与等级", sources=[view]),
                    element("ui.afk.hero-detail.attributes", "list", "HP / ATK / DEF 属性", sources=[view]),
                    element("ui.afk.hero-detail.upgrade", "button", "升级", actions=["tap"], sources=[view, tutorial]),
                ],
            ),
            Surface(
                id="surface.afk.hero-upgrade-preview",
                title="升级预览与确认",
                kind="overlay",
                description="确认前并置等级和属性前后值，资源成本按钮同时显示需求与持有量。",
                source_ids=[view, "src.afk.hero-model-cost"],
                elements=[
                    element("ui.afk.upgrade.level-delta", "status", "等级前后值", sources=[view]),
                    element("ui.afk.upgrade.attribute-delta", "table", "属性前后对照", sources=[view]),
                    element("ui.afk.upgrade.cost", "button", "资源成本与确认", actions=["tap"], sources=[view, "src.afk.hero-model-cost"]),
                    element("ui.afk.upgrade.skill-feedback", "region", "技能解锁或强化反馈", sources=[view]),
                ],
            ),
        ]
    elif report.slug == "minecraft-stone-pickaxe":
        guide = "src.mc.beginner-guide"
        pickaxe = "src.mc.pickaxe"
        oracle = "src.mc.voxelcraft-reference"
        surfaces = [
            Surface(
                id="surface.minecraft.world-gathering",
                title="世界采集视图",
                kind="world",
                description="第一阶段在世界空间中识别树木与石头，并以工具等级决定可获得的掉落。",
                source_ids=[guide, oracle],
                elements=[
                    element("ui.minecraft.world.crosshair", "target", "准星与目标方块", actions=["look", "attack"], sources=[guide]),
                    element("ui.minecraft.world.hotbar", "list", "快捷栏与当前工具", actions=["select"], sources=[guide, oracle]),
                ],
            ),
            Surface(
                id="surface.minecraft.inventory-crafting",
                title="物品栏与 2×2 随身合成",
                kind="page",
                description="原木先在随身合成区转为木板，再为工作台和木棍提供材料。",
                source_ids=[guide],
                elements=[
                    element("ui.minecraft.inventory.grid", "grid", "物品栏", actions=["drag", "select"], sources=[guide, oracle]),
                    element("ui.minecraft.crafting.2x2", "grid", "2×2 合成格", actions=["drag"], sources=[guide]),
                    element("ui.minecraft.crafting.output", "status", "合成输出", actions=["take"], sources=[guide]),
                ],
            ),
            Surface(
                id="surface.minecraft.crafting-table",
                title="工作台 3×3 合成",
                kind="page",
                description="顶排三份石质材料与中轴两根木棍形成定形石镐配方。",
                source_ids=[pickaxe],
                elements=[
                    element("ui.minecraft.crafting.3x3", "grid", "3×3 配方格", actions=["drag"], sources=[pickaxe]),
                    element("ui.minecraft.crafting.stone-pickaxe-output", "status", "石镐输出槽", actions=["take"], sources=[pickaxe]),
                ],
            ),
            Surface(
                id="surface.minecraft.inventory-check",
                title="完成态物品栏",
                kind="overlay",
                description="设施以 inventory item ID 与数量判定完成，不把相似图标当作成功。",
                source_ids=[oracle],
                elements=[
                    element("ui.minecraft.inventory.stone-pickaxe", "listitem", "minecraft:stone_pickaxe", sources=[oracle]),
                ],
            ),
        ]
    elif report.slug == "afk-journey-first-launch-consent":
        source = "src.afk.first-launch-live"
        artifact_ids = [item.id for item in report.artifacts]
        frame_ids = [item.id for item in report.artifacts if item.kind == "screenshot"]
        ui_ids = [item.id for item in report.artifacts if item.kind == "ui_tree"]
        element_artifacts = [*frame_ids, *ui_ids]
        surfaces = [
            Surface(
                id="surface.afk.first-launch-consent",
                title="用户协议与隐私政策弹窗",
                kind="modal",
                description="竖屏中心阻断卡片：标题、可滚动正文、高强调继续按钮和低强调拒绝按钮。",
                source_ids=[source] if source in source_ids else [],
                artifact_ids=artifact_ids,
                run_id=run_id,
                elements=[
                    element("ui.afk.consent.modal", "dialog", "协议弹窗", sources=[source], artifacts=element_artifacts, bounds=(243/1080, 681/1920, 595/1080, 559/1920)),
                    element("ui.afk.consent.title", "heading", "用户协议与隐私政策", text="用户协议与隐私政策", sources=[source], artifacts=ui_ids, parent_id="ui.afk.consent.modal", bounds=(312/1080, 701/1920, 459/1080, 37/1920)),
                    element("ui.afk.consent.message", "document", "协议与隐私正文", sources=[source], artifacts=element_artifacts, parent_id="ui.afk.consent.modal", bounds=(278/1080, 770/1920, 525/1080, 282/1920)),
                    element("ui.afk.consent.agree", "button", "同意并继续", text="同意并继续", actions=["tap"], sources=[source], artifacts=element_artifacts, parent_id="ui.afk.consent.modal", bounds=(278/1080, 1070/1920, 525/1080, 77/1920)),
                    element("ui.afk.consent.disagree", "button", "不同意", text="不同意", actions=["tap"], sources=[source], artifacts=element_artifacts, parent_id="ui.afk.consent.modal", bounds=(278/1080, 1173/1920, 525/1080, 32/1920)),
                ],
            )
        ]
    elif report.slug == "minecraft-voxelcraft-fire-and-cooked-food":
        runtime_source = "src.voxelcraft.fire-food.runtime"
        by_stage = {
            str(item.metadata.get("stage")): item.id
            for item in report.artifacts
            if item.metadata.get("stage")
        }
        surface_specs = [
            ("spawn", "surface.voxelcraft.spawn", "空手出生与环境读取", "世界态保留 HUD、快捷栏和散落资源，作为第一夜路径起点。"),
            ("campfire", "surface.voxelcraft.campfire", "点燃篝火反馈", "火焰、烟、聊天与手记提示在同一世界画面叠加反馈。"),
            ("roasted-food", "surface.voxelcraft.roasted-result", "烤熟产物确认", "物品栏和屏幕物品名共同显示 Roasted Berry。"),
        ]
        surfaces = []
        for stage, surface_id, title, description in surface_specs:
            artifact = by_stage.get(stage)
            surface_artifacts = [artifact] if artifact else []
            surfaces.append(
                Surface(
                    id=surface_id,
                    title=title,
                    kind="world",
                    description=description,
                    source_ids=[runtime_source] if runtime_source in source_ids else [],
                    artifact_ids=surface_artifacts,
                    run_id=run_id,
                    elements=[
                        element(f"ui.voxelcraft.{stage}.world", "world", title, actions=["look"], sources=[runtime_source], artifacts=surface_artifacts),
                        element(f"ui.voxelcraft.{stage}.hud", "status", "生存 HUD 与快捷栏", sources=[runtime_source], artifacts=surface_artifacts),
                    ],
                )
            )
    else:
        return report

    instance = report.system_instance
    if instance is not None:
        instance = instance.model_copy(update={"surface_ids": [item.id for item in surfaces]})
    return report.model_copy(update={"surfaces": surfaces, "system_instance": instance})


def afk_hero_upgrade_report() -> GameReport:
    sources = [
        SourceRef(
            id="src.afk.hero-upgrade-view",
            kind=ContentKind.direct_observation,
            title="AFK Journey · HeroUpgradeView.lua",
            url="source://afk-client/Binary/Src/UI/Hero/View/HeroUpgradeView.lua",
            locator="38, 88-113, 215-226, 248-251",
            version_context="local Perforce workspace, 2026-07-13",
            public=False,
            note="升级面板 prefab、属性前后对比、资源按钮和点击委托的源码证据。",
        ),
        SourceRef(
            id="src.afk.hero-upgrade-tutorial",
            kind=ContentKind.direct_observation,
            title="AFK Journey · HeroUpgradeTutorialTask.lua",
            url="source://afk-client/Binary/Src/UI/Tutorial/Hero/HeroUpgrade/HeroUpgradeTutorialTask.lua",
            locator="38-48, 61-91, 102-109, 118-130",
            version_context="local Perforce workspace, 2026-07-13",
            public=False,
            note="从主界面进入英雄列表、选择英雄、点击升级和返回的官方客户端教程路径。",
        ),
        SourceRef(
            id="src.afk.hero-model-cost",
            kind=ContentKind.direct_observation,
            title="AFK Journey · HeroModel:GetLevelUpCost",
            url="source://afk-client/Binary/Src/UI/Hero/HeroModel.lua",
            locator="624-681",
            version_context="local Perforce workspace, 2026-07-13",
            public=False,
            note="升级消耗由目标等级、当前等级和赛季态计算。",
        ),
        SourceRef(
            id="src.afk.explorer",
            kind=ContentKind.direct_observation,
            title="AFK Journey Unity Explorer",
            url="source://aiworkspace/cli/unity-cli/unity_cli/explorer",
            locator="prompts.py:33; tools.py:362-364",
            version_context="local AIWorkSpace, 2026-07-13",
            public=False,
            note="已有截图、UI 点击、Lua、状态检测、路线图、恢复与 journal 能力。",
        ),
        SourceRef(
            id="voice.afk.essence-wall",
            kind=ContentKind.player_voice,
            title="They REALLY need to buff Hero Essence gains",
            url="https://www.reddit.com/r/AFKJourney/comments/1c1isqk/",
            author="Reddit / r/AFKJourney",
            published_at="2024-04-11",
            version_context="2024 live version",
            note="玩家把英雄精华描述为推进瓶颈，并讨论资源转换与可重复获取。",
        ),
        SourceRef(
            id="voice.afk.level-clarity",
            kind=ContentKind.player_voice,
            title="Levels",
            url="https://www.reddit.com/r/AFKJourney/comments/1fkmzhd/",
            author="Reddit / r/AFKJourney",
            published_at="2024-09-19",
            version_context="2024 seasonal progression",
            note="玩家讨论赛季等级、共鸣等级与协同等级并存时的进度可读性。",
        ),
        SourceRef(
            id="voice.afk.level-up-all",
            kind=ContentKind.player_voice,
            title="I wish leveling up characters was less tedious",
            url="https://www.reddit.com/r/AFKJourney/comments/1gpohd6/",
            author="Reddit / r/AFKJourney",
            published_at="2024-11-12",
            version_context="2024 live version",
            note="玩家希望在资源允许时批量升级，同时有人仍重视逐英雄分配。",
        ),
    ]
    return ensure_report_surfaces(GameReport(
        id="report.afk-journey.hero-upgrade.v1",
        slug="afk-journey-hero-upgrade",
        game_id="afk-journey",
        game_title="AFK Journey",
        system_id="hero-upgrade",
        system_title="英雄升级：逐英雄反馈与共鸣资源压力",
        summary=(
            "客户端把英雄升级组织为“英雄入口—英雄列表—详情—升级反馈”的短闭环：升级前先展示等级与"
            "HP/ATK/DEF 的前后值，再把目标等级对应的资源消耗挂在主按钮上。玩家声音显示，真正的摩擦"
            "不只在按钮，而在英雄精华瓶颈、多个等级体系的可读性，以及逐个升级的重复劳动。"
        ),
        summary_claim=Claim(
            id="claim.afk.hero-upgrade.summary",
            kind=ContentKind.analyst_interpretation,
            statement=(
                "客户端把英雄升级组织为“英雄入口—英雄列表—详情—升级反馈”的短闭环：升级前先展示等级与"
                "HP/ATK/DEF 的前后值，再把目标等级对应的资源消耗挂在主按钮上。玩家声音显示，真正的摩擦"
                "不只在按钮，而在英雄精华瓶颈、多个等级体系的可读性，以及逐个升级的重复劳动。"
            ),
            source_ids=[item.id for item in sources],
            review_status="reviewed",
        ),
        scope=BuildScope(
            id="scope.afk.local-source.2026-07-13",
            game_id="afk-journey",
            platform="android+unity-source",
            version="local-workspace-unknown",
            region="CN-development",
            account_stage="hero-upgrade-unlocked",
            device="MuMu Android 15 + Unity source",
            source_ids=["src.afk.hero-upgrade-view", "src.afk.explorer"],
        ),
        game=Game(
            id="afk-journey",
            title="AFK Journey",
            aliases=["剑与远征：启程"],
            platforms=["android", "ios", "windows"],
        ),
        system_concept=SystemConcept(
            id="hero-upgrade",
            title="英雄升级",
            description="以资源消耗换取单英雄等级和属性提升的成长系统。",
            tags=["progression", "resource-gate", "list-detail"],
            source_ids=["src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
        ),
        system_instance=SystemInstance(
            id="instance.afk.hero-upgrade.local-source",
            concept_id="hero-upgrade",
            build_scope_id="scope.afk.local-source.2026-07-13",
            title="AFK 本地源码工作区英雄升级实例",
            source_ids=[
                "src.afk.hero-upgrade-view",
                "src.afk.hero-upgrade-tutorial",
                "src.afk.hero-model-cost",
            ],
        ),
        resource_model=ResourceModel(
            id="resource-model.afk.hero-upgrade",
            title="英雄升级资源模型",
            resources=[
                ResourceDefinition(
                    id="resource.afk.training-manual",
                    title="训练手册 / 英雄经验类资源",
                    kind="material",
                    source_ids=["src.afk.hero-model-cost"],
                ),
                ResourceDefinition(
                    id="resource.afk.hero-essence",
                    title="英雄精华",
                    kind="material",
                    source_ids=["voice.afk.essence-wall"],
                ),
                ResourceDefinition(
                    id="resource.afk.season-level",
                    title="赛季等级资源",
                    kind="material",
                    source_ids=["src.afk.hero-upgrade-view", "voice.afk.level-clarity"],
                ),
            ],
            relation_ids=[
                "relation.afk.training-cost",
                "relation.afk.essence-gate",
                "relation.afk.season-cost",
            ],
            source_ids=["src.afk.hero-model-cost", "src.afk.hero-upgrade-view"],
        ),
        tags=[
            "mobile", "unity", "progression", "resource-loop", "hero-upgrade", "list-detail",
            "touch", "source-probe", "player-friction", "seasonal-progression",
        ],
        status="draft",
        sources=sources,
        flow=[
            FlowNode(
                id="afk.flow.hero-entry",
                title="从主界面打开英雄系统",
                description="教程直接定位 MainView 的 btn_hero_on，并开放系统按钮后引导点击。",
                action="tap btn_hero_on",
                state_before="主世界/主界面",
                state_after="英雄列表",
                source_ids=["src.afk.hero-upgrade-tutorial"],
                next=["afk.flow.hero-select"],
            ),
            FlowNode(
                id="afk.flow.hero-select",
                title="在英雄列表选择目标英雄",
                description="教程可滚动列表并临时禁用长按，把触点固定到目标英雄卡。",
                action="tap hero card",
                state_before="英雄列表",
                state_after="英雄详情",
                source_ids=["src.afk.hero-upgrade-tutorial"],
                next=["afk.flow.preview"],
            ),
            FlowNode(
                id="afk.flow.preview",
                title="预览下一级与属性变化",
                description="升级子视图同时呈现当前/下一等级与 HP、ATK、DEF 前后值。",
                action="tap btn_upgrade",
                state_before="英雄详情",
                state_after="升级预览",
                source_ids=["src.afk.hero-upgrade-view", "src.afk.hero-upgrade-tutorial"],
                next=["afk.flow.confirm"],
            ),
            FlowNode(
                id="afk.flow.confirm",
                title="检查资源并确认升级",
                description="主按钮从 HeroModel 读取目标等级消耗，并显示玩家当前持有量。",
                action="tap cost button",
                state_before="升级预览",
                state_after="等级/资源/属性更新或资源不足反馈",
                source_ids=["src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
                next=["afk.flow.return"],
            ),
            FlowNode(
                id="afk.flow.return",
                title="返回英雄列表或主界面",
                description="教程分别绑定详情返回和全局导航返回，恢复此前被禁用的拖拽。",
                action="tap btn_back_detail / btn_back",
                state_before="英雄详情",
                state_after="英雄列表/主界面",
                source_ids=["src.afk.hero-upgrade-tutorial"],
            ),
        ],
        mechanisms=[
            MechanismSpec(
                id="afk.mechanism.preview",
                title="升级预览用克隆数据计算",
                description="不先修改真实英雄；克隆 HeroData、写入 nextLevel，再重算属性用于前后对比。",
                representation="pseudocode",
                code=(
                    "next = clone(hero)\n"
                    "next.level = currentLevel + 1\n"
                    "before = calculateHeroAttributes(hero)\n"
                    "after  = calculateHeroAttributes(next)\n"
                    "render(level, HP, ATK, ARM: before -> after)"
                ),
                source_ids=["src.afk.hero-upgrade-view"],
            ),
            MechanismSpec(
                id="afk.mechanism.cost",
                title="消耗依赖目标等级与赛季态",
                description="升级面板请求 GetLevelUpCost(nextLevel, currentLevel, isEp)，再把多项资源交给统一成本按钮。",
                representation="formula",
                code="costs = GetLevelUpCost(currentLevel + 1, currentLevel?, isSeasonal)",
                source_ids=["src.afk.hero-model-cost", "src.afk.hero-upgrade-view"],
            ),
            MechanismSpec(
                id="afk.mechanism.feedback",
                title="数值升级与技能升级共享反馈面板",
                description="同一 HeroUpgradeView 在纯属性变化和技能解锁/强化之间切换，并针对主动、被动、必杀、专属和赛季技能呈现不同信息。",
                representation="state_machine",
                code="detail -> {stage_preview | skill_unlock | skill_level_up} -> confirm -> updated_detail",
                source_ids=["src.afk.hero-upgrade-view"],
            ),
        ],
        resources=[
            ResourceRelation(
                id="relation.afk.training-cost",
                resource="训练手册 / 英雄经验类资源",
                role="cost",
                description="支撑普通等级推进；玩家讨论中会在后期与英雄精华形成不对称库存。",
                source_ids=["src.afk.hero-model-cost", "voice.afk.essence-wall"],
            ),
            ResourceRelation(
                id="relation.afk.essence-gate",
                resource="英雄精华",
                role="gate",
                description="玩家公开表达中反复被描述为关键等级节点的推进瓶颈。",
                source_ids=["voice.afk.essence-wall"],
            ),
            ResourceRelation(
                id="relation.afk.season-cost",
                resource="赛季等级资源",
                role="cost",
                description="源码以 isEp 分支区分赛季升级；玩家也感知到多个等级体系并存。",
                source_ids=["src.afk.hero-upgrade-view", "voice.afk.level-clarity"],
            ),
        ],
        player_voices=[
            PlayerVoice(
                id="pv.afk.essence",
                summary="一些玩家认为英雄精华供给让升级停顿过长，并提出提高掉落或允许其他资源转换。",
                theme="resource-bottleneck",
                sentiment="negative",
                source_id="voice.afk.essence-wall",
                system_node_id="afk.flow.confirm",
                version_context="2024 live version",
            ),
            PlayerVoice(
                id="pv.afk.level-clarity",
                summary="赛季等级、共鸣等级和协同等级同时出现时，有玩家觉得进度不再直观。",
                theme="progression-legibility",
                sentiment="mixed",
                source_id="voice.afk.level-clarity",
                system_node_id="afk.flow.preview",
                version_context="2024 seasonal progression",
            ),
            PlayerVoice(
                id="pv.afk.batch",
                summary="玩家希望资源充足时批量升级五名英雄，以减少重复操作；也有人保留逐英雄分配的偏好。",
                theme="batch-upgrade",
                sentiment="mixed",
                source_id="voice.afk.level-up-all",
                system_node_id="afk.flow.confirm",
                version_context="2024 live version",
            ),
        ],
        observations=[
            Observation(
                id="obs.afk.preview-attributes",
                statement="升级预览不仅展示等级，也把 HP、ATK、DEF 的变化前置到确认之前。",
                source_ids=["src.afk.hero-upgrade-view"],
            ),
            Observation(
                id="obs.afk.cost-owned-amount",
                statement="资源成本组件显示拥有量，使资源不足在点击前已经可见。",
                source_ids=["src.afk.hero-upgrade-view", "src.afk.hero-model-cost"],
            ),
            Observation(
                id="obs.afk.tutorial-path",
                statement="官方客户端教程给出了可执行的完整入口和返回路径，可直接转成自动化状态图。",
                source_ids=["src.afk.hero-upgrade-tutorial"],
            ),
        ],
        interpretations=[
            Claim(
                id="claim.afk.interpretation.combined-pressure",
                kind=ContentKind.analyst_interpretation,
                statement="单次升级反馈本身很完整，但玩家体验压力来自升级频率、资源门和多个进度口径的组合。",
                source_ids=[
                    "src.afk.hero-upgrade-view",
                    "voice.afk.essence-wall",
                    "voice.afk.level-clarity",
                ],
                review_status="reviewed",
            ),
            Claim(
                id="claim.afk.interpretation.batch-value",
                kind=ContentKind.analyst_interpretation,
                statement="批量升级诉求说明高频重复动作可能削弱逐次属性反馈的价值。",
                source_ids=["src.afk.hero-upgrade-view", "voice.afk.level-up-all"],
                review_status="reviewed",
            ),
        ],
        open_questions=[
            "当前公开版本中批量升级按钮的出现条件与 2024 玩家讨论相比是否已变化？",
            "不同赛季的资源命名和回收关系如何在升级面板中解释？",
        ],
    ))


def minecraft_stone_pickaxe_report() -> GameReport:
    sources = [
        SourceRef(
            id="src.mc.beginner-guide",
            kind=ContentKind.official_statement,
            title="Minecraft Wiki · Beginner's guide",
            url="https://minecraft.wiki/w/Tutorial:Beginner%27s_guide",
            locator="early-game crafting and stone-tool progression",
            version_context="Java Edition general survival progression",
            note="社区维护的公开机制资料；说明木镐取得圆石并解锁石制工具的早期链路。",
        ),
        SourceRef(
            id="src.mc.pickaxe",
            kind=ContentKind.official_statement,
            title="Minecraft Wiki · Pickaxe",
            url="https://minecraft.wiki/w/Pickaxe",
            locator="crafting table / stone pickaxe recipe",
            version_context="Java Edition general recipe",
            note="社区维护的公开机制资料；列出镐的统一形状和石质材料。",
        ),
        SourceRef(
            id="src.mc.voxelcraft-reference",
            kind=ContentKind.direct_observation,
            title="voxelcraft · vanilla Minecraft 1.21.1 reference",
            url="source://omnicompany/data/domains/voxelcraft/references/vanilla_minecraft_1_21_1",
            locator="minecraft/ worldgen registry + README",
            version_context="Minecraft 1.21.1",
            public=False,
            note="本地版本化参考和 ID 校验入口。",
        ),
        SourceRef(
            id="voice.mc.first-tools",
            kind=ContentKind.player_voice,
            title="How much cobblestone do you mine at the start of a world?",
            url="https://www.reddit.com/r/Minecraft/comments/1p7genj/",
            author="Reddit / r/Minecraft",
            published_at="2025-11",
            version_context="survival play, version unknown",
            note="玩家把木镐、工作台和三块圆石描述为开局石镐路径的一部分。",
        ),
    ]
    task = BenchmarkTask(
        id="task.minecraft.craft-stone-pickaxe",
        title="固定世界中合成一把石镐",
        start_state="空物品栏、固定种子、白天出生点",
        goal="物品栏中出现 minecraft:stone_pickaxe",
        allowed_actions=["move", "look", "attack", "use", "inventory", "craft"],
        reset_method="restore fixed world snapshot and player inventory",
        checks=[
            ObjectiveCheck(
                id="inventory_contains_stone_pickaxe",
                description="最终物品栏包含 minecraft:stone_pickaxe",
                expected=True,
            ),
            ObjectiveCheck(
                id="recipe_uses_three_stone_materials",
                description="配方使用三份 stone-tier material",
                expected=3,
            ),
            ObjectiveCheck(
                id="recipe_uses_two_sticks",
                description="配方使用两根木棍",
                expected=2,
            ),
        ],
        note="当前只实现 task/checker 合同与源夹具验证，不执行完整视觉 benchmark。",
    )
    return ensure_report_surfaces(GameReport(
        id="report.minecraft.stone-pickaxe.v1",
        slug="minecraft-stone-pickaxe",
        game_id="minecraft-java",
        game_title="Minecraft Java Edition",
        system_id="craft-stone-pickaxe",
        system_title="从裸手到石镐：早期生存的第一条工具链",
        summary=(
            "石镐不是一个孤立配方，而是一条把世界采集、2×2 随身合成、3×3 工作台、工具等级与方块掉落"
            "串起来的早期教学链。任务的客观终点很简单——物品栏出现 stone_pickaxe——但路径会自然检验空间行动、"
            "工具前置、材料转换和合成布局。"
        ),
        summary_claim=Claim(
            id="claim.minecraft.stone-pickaxe.summary",
            kind=ContentKind.analyst_interpretation,
            statement=(
                "石镐不是一个孤立配方，而是一条把世界采集、2×2 随身合成、3×3 工作台、工具等级与方块掉落"
                "串起来的早期教学链。任务的客观终点很简单——物品栏出现 stone_pickaxe——但路径会自然检验空间行动、"
                "工具前置、材料转换和合成布局。"
            ),
            source_ids=[item.id for item in sources],
            review_status="reviewed",
        ),
        scope=BuildScope(
            id="scope.minecraft.java.1.21.1",
            game_id="minecraft-java",
            platform="windows-java",
            version="1.21.1",
            region="global",
            locale="zh-CN",
            account_stage="new-survival-world",
            device="PC keyboard+mouse / source oracle",
            source_ids=["src.mc.beginner-guide", "src.mc.voxelcraft-reference"],
        ),
        game=Game(
            id="minecraft-java",
            title="Minecraft Java Edition",
            aliases=["Minecraft"],
            platforms=["windows-java", "macos-java", "linux-java"],
        ),
        system_concept=SystemConcept(
            id="craft-stone-pickaxe",
            title="石镐工具链",
            description="从世界采集和木制前置工具进入石质工具层的早期生存系统。",
            tags=["crafting", "tool-progression", "early-game"],
            source_ids=["src.mc.beginner-guide", "src.mc.pickaxe"],
        ),
        system_instance=SystemInstance(
            id="instance.minecraft.stone-pickaxe.1.21.1",
            concept_id="craft-stone-pickaxe",
            build_scope_id="scope.minecraft.java.1.21.1",
            title="Minecraft Java 1.21.1 石镐路径",
            source_ids=["src.mc.beginner-guide", "src.mc.pickaxe", "src.mc.voxelcraft-reference"],
        ),
        resource_model=ResourceModel(
            id="resource-model.minecraft.stone-pickaxe",
            title="石镐早期资源模型",
            resources=[
                ResourceDefinition(
                    id="resource.minecraft.log-planks",
                    title="原木与木板",
                    kind="material",
                    source_ids=["src.mc.beginner-guide"],
                ),
                ResourceDefinition(
                    id="resource.minecraft.wooden-tools",
                    title="工作台、木棍与木镐",
                    kind="item",
                    source_ids=["src.mc.beginner-guide", "src.mc.pickaxe"],
                ),
                ResourceDefinition(
                    id="resource.minecraft.stone-pickaxe",
                    title="石镐",
                    kind="item",
                    source_ids=["src.mc.pickaxe"],
                ),
            ],
            relation_ids=[
                "relation.minecraft.log-planks",
                "relation.minecraft.wooden-tools",
                "relation.minecraft.stone-pickaxe-cost",
            ],
            source_ids=["src.mc.beginner-guide", "src.mc.pickaxe"],
        ),
        tags=[
            "pc", "minecraft", "crafting", "world-navigation", "resource-loop", "tool-progression",
            "mouse-keyboard", "source-probe", "objective-checker", "early-game",
        ],
        status="draft",
        sources=sources,
        flow=[
            FlowNode(
                id="mc.flow.wood",
                title="取得原木",
                description="裸手破坏树干，获得第一种可加工资源。",
                action="break log",
                state_before="空物品栏",
                state_after="拥有原木",
                source_ids=["src.mc.beginner-guide"],
                next=["mc.flow.planks"],
            ),
            FlowNode(
                id="mc.flow.planks",
                title="把原木转成木板",
                description="随身 2×2 合成完成第一层资源转换。",
                action="craft planks",
                state_before="拥有原木",
                state_after="拥有木板",
                source_ids=["src.mc.beginner-guide"],
                next=["mc.flow.table-tools"],
            ),
            FlowNode(
                id="mc.flow.table-tools",
                title="制作工作台、木棍与木镐",
                description="工作台把合成空间扩展为 3×3；木镐是取得圆石的工具前置。",
                action="craft table, sticks, wooden pickaxe",
                state_before="拥有木板",
                state_after="拥有工作台、木棍和木镐",
                source_ids=["src.mc.beginner-guide", "src.mc.pickaxe"],
                next=["mc.flow.stone"],
            ),
            FlowNode(
                id="mc.flow.stone",
                title="用木镐开采石头",
                description="石头被合适工具破坏后提供 stone-tier 合成材料，典型为圆石。",
                action="mine stone with wooden pickaxe",
                state_before="拥有木镐",
                state_after="至少三份石质工具材料",
                source_ids=["src.mc.beginner-guide", "src.mc.voxelcraft-reference"],
                next=["mc.flow.craft"],
            ),
            FlowNode(
                id="mc.flow.craft",
                title="在工作台摆出镐形配方",
                description="顶排三份石质材料，中轴两根木棍，输出一把石镐。",
                action="craft minecraft:stone_pickaxe",
                state_before="三份石质材料 + 两根木棍 + 工作台",
                state_after="物品栏出现石镐",
                source_ids=["src.mc.pickaxe"],
                next=["mc.flow.verify"],
            ),
            FlowNode(
                id="mc.flow.verify",
                title="用物品栏状态客观验收",
                description="不以画面“看起来像成功”为准，直接检查物品 ID 与数量。",
                action="inspect inventory",
                state_before="合成完成",
                state_after="objective checker pass/fail",
                source_ids=["src.mc.voxelcraft-reference"],
            ),
        ],
        mechanisms=[
            MechanismSpec(
                id="mc.mechanism.recipe",
                title="石镐是定形配方",
                description="材料数量正确仍不够；三份材料与两根木棍必须形成镐的空间布局。",
                representation="pseudocode",
                code=(
                    "grid = [\n"
                    "  [STONE, STONE, STONE],\n"
                    "  [EMPTY, STICK, EMPTY],\n"
                    "  [EMPTY, STICK, EMPTY],\n"
                    "]\n"
                    "output = minecraft:stone_pickaxe"
                ),
                source_ids=["src.mc.pickaxe"],
            ),
            MechanismSpec(
                id="mc.mechanism.tool-gate",
                title="材料取得受工具等级约束",
                description="玩家必须先有木镐，才能稳定从石头得到进入石制工具层的材料。",
                representation="state_machine",
                code="bare_hands -> wood -> wooden_pickaxe -> cobblestone -> stone_pickaxe",
                source_ids=["src.mc.beginner-guide"],
            ),
            MechanismSpec(
                id="mc.mechanism.checker",
                title="完成态可以由世界状态直接判定",
                description="固定任务不依赖主观截图判断；inventory item ID 是清晰终点，里程碑可逐段诊断。",
                representation="rule",
                code="pass iff inventory.count('minecraft:stone_pickaxe') >= 1",
                source_ids=["src.mc.voxelcraft-reference"],
            ),
        ],
        resources=[
            ResourceRelation(
                id="relation.minecraft.log-planks",
                resource="原木 → 木板",
                role="conversion",
                description="原始世界资源转成通用合成材料。",
                source_ids=["src.mc.beginner-guide"],
            ),
            ResourceRelation(
                id="relation.minecraft.wooden-tools",
                resource="木板 → 工作台 / 木棍 / 木镐",
                role="cost",
                description="同一初始资源被分配到生产设施、工具柄与前置工具。",
                source_ids=["src.mc.beginner-guide", "src.mc.pickaxe"],
            ),
            ResourceRelation(
                id="relation.minecraft.stone-pickaxe-cost",
                resource="三份石质材料 + 两根木棍",
                role="cost",
                description="构成石镐的最终定形配方。",
                source_ids=["src.mc.pickaxe"],
            ),
        ],
        player_voices=[
            PlayerVoice(
                id="pv.mc.opening-route",
                summary="玩家会把木镐、工作台和三块圆石压缩成一条熟练的开局惯例，并进一步讨论一次应采多少圆石。",
                theme="opening-routine",
                sentiment="mixed",
                source_id="voice.mc.first-tools",
                system_node_id="mc.flow.stone",
                version_context="version unknown",
            )
        ],
        benchmark_task=task,
        observations=[
            Observation(
                id="obs.minecraft.cross-surface-flow",
                statement="流程同时跨越世界操作与两个合成界面，适合检验 agent 的长程状态保持。",
                source_ids=["src.mc.beginner-guide", "src.mc.pickaxe"],
            ),
            Observation(
                id="obs.minecraft.inventory-oracle",
                statement="最终 inventory item ID 可形成比截图更强的客观判定。",
                source_ids=["src.mc.voxelcraft-reference"],
            ),
            Observation(
                id="obs.minecraft.shared-task-contract",
                statement="高层语义路径与像素键鼠路径可以共享同一任务定义。",
                source_ids=["src.mc.voxelcraft-reference"],
            ),
        ],
        interpretations=[
            Claim(
                id="claim.minecraft.interpretation.task-density",
                kind=ContentKind.analyst_interpretation,
                statement="石镐任务的价值不在难度，而在以很短路径串联采集、转换、设施、工具门和空间配方。",
                source_ids=["src.mc.beginner-guide", "src.mc.pickaxe"],
                review_status="reviewed",
            ),
            Claim(
                id="claim.minecraft.interpretation.shared-checker",
                kind=ContentKind.analyst_interpretation,
                statement="同一 checker 可同时验证高层语义 agent 与低层视觉 agent，便于区分规划和操作问题。",
                source_ids=["src.mc.voxelcraft-reference"],
                review_status="reviewed",
            ),
        ],
        open_questions=[
            "视觉 adapter 如何稳定处理背包格、配方书与手动摆放之间的切换？",
            "固定世界 snapshot 应使用原版存档、服务器命令还是容器化实例？",
        ],
    ))


def write_validation_fixtures(root: Path) -> list[Path]:
    from .afk_benchmark import AfkHeroUpgradeSnapshot, write_contract_snapshot

    fixtures = root / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    afk = fixtures / "afk-hero-upgrade.json"
    afk.write_text(
        '{\n  "title": "AFK hero upgrade source checks",\n  "facts": {\n'
        '    "preview_has_before_after_attributes": true,\n'
        '    "cost_uses_target_level": true,\n'
        '    "tutorial_has_return_path": true\n  }\n}\n',
        encoding="utf-8",
    )
    minecraft = fixtures / "minecraft-stone-pickaxe.json"
    minecraft.write_text(
        '{\n  "title": "Minecraft stone pickaxe source checks",\n  "facts": {\n'
        '    "inventory_contains_stone_pickaxe": true,\n'
        '    "recipe_uses_three_stone_materials": 3,\n'
        '    "recipe_uses_two_sticks": 2\n  }\n}\n',
        encoding="utf-8",
    )
    golden = fixtures / "golden"
    schema_root = golden / "schemas"
    report_root = golden / "reports"
    schema_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)
    contract_models = {
        "source-ref": SourceRef,
        "artifact-ref": ArtifactRef,
        "run-ref": RunRef,
        "trace-event": TraceEvent,
        "surface": Surface,
        "ui-element": UIElementInstance,
        "game": Game,
        "build-scope": BuildScope,
        "system-concept": SystemConcept,
        "system-instance": SystemInstance,
        "resource-model": ResourceModel,
        "observation": Observation,
        "claim": Claim,
        "benchmark-task": BenchmarkTask,
        "game-report": GameReport,
        "reverse-engineered-design-spec": ReverseEngineeredGameDesignSpec,
        "afk-hero-upgrade-snapshot": AfkHeroUpgradeSnapshot,
    }
    contract_paths: dict[str, str] = {}
    for name, model in contract_models.items():
        path = schema_root / f"{name}.schema.json"
        path.write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        contract_paths[name] = path.relative_to(fixtures).as_posix()

    cases: list[dict[str, object]] = []
    for report in seed_reports():
        report.assert_storable()
        path = report_root / f"{report.slug}.report.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        cases.append(
            {
                "id": report.id,
                "path": path.relative_to(fixtures).as_posix(),
                "expected": {
                    "game_id": report.game_id,
                    "system_id": report.system_id,
                    "flow_nodes": len(report.flow),
                    "mechanisms": len(report.mechanisms),
                    "provenance_issues": 0,
                    "publication_issues": len(report.publication_issues()),
                },
            }
        )
    manifest = golden / "manifest.json"
    afk_snapshot = write_contract_snapshot(fixtures / "afk-hero-upgrade-snapshot.contract.json")
    manifest.write_text(
        json.dumps(
            {
                "schema": "game-observatory.golden-manifest.v1",
                "contracts": contract_paths,
                "cases": cases,
                "fixture_tasks": [
                    {"path": afk.name, "task_id": "task.afk.source-contract"},
                    {"path": minecraft.name, "task_id": "task.minecraft.craft-stone-pickaxe"},
                ],
                "snapshot_contracts": [afk_snapshot.relative_to(fixtures).as_posix()],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return [afk, minecraft, afk_snapshot, manifest]


def seed_reports() -> list[GameReport]:
    stamp = "2026-07-13T00:00:00+08:00"
    reports = [afk_hero_upgrade_report(), minecraft_stone_pickaxe_report()]
    for report in reports:
        report.created_at = stamp
        report.updated_at = stamp
        report.scope.captured_at = stamp
        for source in report.sources:
            source.captured_at = stamp
    return reports
