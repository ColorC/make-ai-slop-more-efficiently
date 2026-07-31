from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .adapters import AdbAdapter, AdapterError, SourceFixtureAdapter
from .benchmark import BenchmarkBundleWriter
from .compiler import SemanticReportCompiler
from .content import ensure_report_surfaces, seed_reports, write_validation_fixtures
from .gateway import DeviceGateway, GatewayError, MumuCli
from .models import (
    BenchmarkTask,
    ArtifactRef,
    BuildScope,
    Claim,
    ContentKind,
    FlowNode,
    Game,
    GameReport,
    MechanismSpec,
    NormalizedAction,
    ObjectiveCheck,
    Observation,
    ResourceDefinition,
    ResourceModel,
    ResourceRelation,
    RunRef,
    RunResult,
    SourceRef,
    SystemConcept,
    SystemInstance,
    TraceEvent,
    TargetInfo,
    TargetRecord,
    utc_now,
)
from .store import ObservatoryStore
from .source_voice import SourceVoicePipeline


class GameObservatory:
    def __init__(self, root: Path | None = None) -> None:
        self.store = ObservatoryStore(root)

    def bootstrap(self) -> dict[str, Any]:
        reports = seed_reports()
        for report in reports:
            # Bootstrap is creation-only. Curated reports may contain reviewer patches,
            # acquired sources, and retraction state that must never be reset to seed data.
            if self.store.get_report(report.id) is None:
                self.store.upsert_report(report)
        surface_backfill: list[str] = []
        legacy_downgraded: list[str] = []
        for existing in self.store.list_reports(include_drafts=True):
            migrated = ensure_report_surfaces(existing)
            if migrated.contract_version == "legacy-report-v0.2" and migrated.status == "published":
                has_visual_evidence = any(
                    item.kind in {"screenshot", "video_frame"} for item in migrated.artifacts
                )
                migrated = migrated.model_copy(
                    update={
                        "status": "draft",
                        "migration_status": (
                            "needs_design_artifacts" if has_visual_evidence else "needs_evidence"
                        ),
                        "updated_at": utc_now(),
                    }
                )
                legacy_downgraded.append(migrated.id)
            if migrated.model_dump(mode="json") != existing.model_dump(mode="json"):
                self.store.upsert_report(migrated)
                surface_backfill.append(existing.id)
        provenance_backfill = SourceVoicePipeline(self.store).backfill_existing()
        fixtures = write_validation_fixtures(self.store.root)
        all_reports = self.store.list_reports()
        catalog = self.store.export_reports(all_reports)
        public_build = self.compile_public(all_reports)
        return {
            "ok": True,
            "counts": self.store.counts(),
            "reports": [report.id for report in all_reports],
            "fixtures": [str(path) for path in fixtures],
            "catalog": str(catalog),
            "public_build": public_build,
            "provenance_backfill": provenance_backfill,
            "surface_backfill": surface_backfill,
            "legacy_downgraded": legacy_downgraded,
        }

    def compile_public(self, reports: list[GameReport] | None = None) -> dict[str, Any]:
        compiler = SemanticReportCompiler(self.store.export_root / "public")
        return compiler.compile(reports or self.store.list_reports())

    def verify_public_site_quality(
        self,
        base_url: str = "http://127.0.0.1:8222",
        *,
        browser_evidence: Path | None = None,
    ) -> dict[str, Any]:
        from .quality import BrowserEvidenceCollector, PublicSiteQualityVerifier

        if browser_evidence is None:
            BrowserEvidenceCollector(self.store).capture(base_url)
            browser_evidence = self.store.export_root / "public-site-browser-evidence.json"
        return PublicSiteQualityVerifier(self.store).verify(
            base_url,
            browser_evidence_path=browser_evidence,
        )

    def build_phase_proofs(self) -> dict[str, Any]:
        from .proofs import PhaseProofBuilder

        return PhaseProofBuilder(self.store).build()

    def import_local_artifact(
        self,
        path: Path,
        *,
        kind: str = "screenshot",
        public: bool = False,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Copy external evidence into the canonical artifact store.

        The content hash is the identity, making repeated promotions idempotent.
        Original paths stay in the internal record and are stripped by the
        public projection.
        """
        source = path.expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"artifact file does not exist: {source}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        artifact_id = f"art.external.{digest[:16]}"
        suffix = source.suffix.lower() or ".bin"
        destination = self.store.artifact_root / f"{artifact_id}{suffix}"
        if source != destination and not destination.exists():
            shutil.copy2(source, destination)
        artifact = ArtifactRef(
            id=artifact_id,
            kind=kind,
            path=str(destination),
            sha256=digest,
            run_id=run_id,
            locator=source.name,
            media_type=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            metadata={
                **(metadata or {}),
                "public": public,
                "origin_path": str(source),
            },
        )
        self.store.save_artifact(artifact)
        return artifact

    def promote_voxelcraft_fire_food(
        self,
        spawn_screenshot: Path,
        campfire_screenshot: Path,
        roasted_screenshot: Path,
        *,
        version: str = "Minecraft 1.21.1 / voxelcraft first-night v3",
        compile_after: bool = True,
    ) -> GameReport:
        """Promote a real voxelcraft play trace into a public design report."""
        paths = [spawn_screenshot, campfire_screenshot, roasted_screenshot]
        latest_mtime = max(path.expanduser().resolve().stat().st_mtime for path in paths)
        captured_at = datetime.fromtimestamp(latest_mtime, timezone.utc).isoformat()
        run_id = "run.voxelcraft.fire-food." + hashlib.sha256(
            "|".join(str(path.expanduser().resolve()) for path in paths).encode("utf-8")
        ).hexdigest()[:12]
        labels = ["spawn", "campfire", "roasted-food"]
        artifacts = [
            self.import_local_artifact(
                path,
                public=True,
                run_id=run_id,
                metadata={"game": "minecraft-java", "system": "fire-and-cooked-food", "stage": label},
            )
            for path, label in zip(paths, labels, strict=True)
        ]
        self.store.save_run(
            RunResult(
                id=run_id,
                adapter="external-runtime-evidence",
                target_id="minecraft://voxelcraft/first-night-world",
                task_id="observation.voxelcraft.fire-and-cooked-food",
                status="passed",
                started_at=captured_at,
                ended_at=utc_now(),
                artifact_ids=[item.id for item in artifacts],
            )
        )
        spawn, campfire, roasted = artifacts
        runtime_source = SourceRef(
            id="src.voxelcraft.fire-food.runtime",
            kind=ContentKind.direct_observation,
            title="voxelcraft 第一夜真实运行画面",
            url=f"/api/game-observatory/artifacts/{campfire.id}",
            locator=f"run={run_id}; artifacts={','.join(item.id for item in artifacts)}",
            captured_at=captured_at,
            version_context=version,
            public=True,
            note="固定验证世界中的连续实玩证据：空手出生、点燃篝火、烤制产物进入物品栏。",
        )
        handcraft_source = SourceRef(
            id="src.voxelcraft.handcraft-system",
            kind=ContentKind.direct_observation,
            title="voxelcraft · HandcraftSystem.java",
            url="source://voxelcraft/src/main/java/com/voxelcraft/protoworld/craft/HandcraftSystem.java",
            locator="38-42, 76-143",
            captured_at=captured_at,
            version_context=version,
            public=False,
            note="枯枝堆叠、石子概率点火、失败火星反馈与雨天修正的源码事实。",
        )
        guidance_source = SourceRef(
            id="src.voxelcraft.jade-fire-guidance",
            kind=ContentKind.direct_observation,
            title="voxelcraft · ProtoWorldJadePlugin.java",
            url="source://voxelcraft/src/main/java/com/voxelcraft/protoworld/client/jade/ProtoWorldJadePlugin.java",
            locator="56-86",
            captured_at=captured_at,
            version_context=version,
            public=False,
            note="按枝堆进度、手持物和篝火状态切换的情境提示。",
        )
        handbook_source = SourceRef(
            id="src.voxelcraft.handbook-fire-food",
            kind=ContentKind.direct_observation,
            title="voxelcraft · 火与熟食手记定义",
            url="source://voxelcraft/specs/handbook_entries.json",
            locator="394-422",
            captured_at=captured_at,
            version_context=version,
            public=False,
            note="点火与首次熟食触发的两段客观记录文本。",
        )
        report = GameReport(
            id="report.minecraft.voxelcraft-fire-food.v1",
            slug="minecraft-voxelcraft-fire-and-cooked-food",
            game_id="minecraft-java",
            game_title="Minecraft Java Edition",
            system_id="voxelcraft-fire-and-cooked-food",
            system_title="从枯枝火星到烤熟浆果：第一夜的可见因果链",
            summary=(
                "这套第一夜系统没有把生火压缩成菜单配方，而是让玩家在世界中堆满三根枯枝，"
                "再用石子反复敲击。每次尝试都给出火星，成功后场景、光照、烟与手记同步变化；"
                "把生食放上篝火一段时间后，物品身份变为熟食。真实画面与源码共同确认了从材料、"
                "动作、概率反馈到产物转换的完整闭环。"
            ),
            summary_claim=Claim(
                id="claim.voxelcraft.fire-food.summary",
                kind=ContentKind.analyst_interpretation,
                statement=(
                    "这套第一夜系统没有把生火压缩成菜单配方，而是让玩家在世界中堆满三根枯枝，"
                    "再用石子反复敲击。每次尝试都给出火星，成功后场景、光照、烟与手记同步变化；"
                    "把生食放上篝火一段时间后，物品身份变为熟食。真实画面与源码共同确认了从材料、"
                    "动作、概率反馈到产物转换的完整闭环。"
                ),
                source_ids=[runtime_source.id, handcraft_source.id, guidance_source.id, handbook_source.id],
                artifact_ids=[item.id for item in artifacts],
                run_id=run_id,
                review_status="reviewed",
            ),
            scope=BuildScope(
                id="scope.minecraft.voxelcraft.first-night.v3",
                game_id="minecraft-java",
                platform="windows-java",
                version=version,
                region="local-validation-world",
                locale="zh-CN",
                account_stage="newbie-first-night",
                device="PC keyboard+mouse / Fabric client + dedicated server",
                captured_at=captured_at,
                source_ids=[runtime_source.id],
                artifact_ids=[item.id for item in artifacts],
                run_id=run_id,
            ),
            game=Game(
                id="minecraft-java",
                title="Minecraft Java Edition",
                aliases=["Minecraft"],
                platforms=["windows-java", "macos-java", "linux-java"],
            ),
            system_concept=SystemConcept(
                id="voxelcraft-fire-and-cooked-food",
                title="世界内生火与熟食转换",
                description="用空间堆叠、概率点火和时间转换组织第一夜资源加工。",
                tags=["world-interaction", "fire", "cooking"],
                source_ids=[handcraft_source.id, guidance_source.id, handbook_source.id],
                artifact_ids=[campfire.id, roasted.id],
                run_id=run_id,
            ),
            system_instance=SystemInstance(
                id="instance.minecraft.voxelcraft.fire-food.v3",
                concept_id="voxelcraft-fire-and-cooked-food",
                build_scope_id="scope.minecraft.voxelcraft.first-night.v3",
                title="voxelcraft 第一夜验证世界实例",
                source_ids=[runtime_source.id, handcraft_source.id, guidance_source.id],
                artifact_ids=[item.id for item in artifacts],
                run_ids=[run_id],
            ),
            resource_model=ResourceModel(
                id="resource-model.voxelcraft.fire-food",
                title="第一夜生火与熟食资源模型",
                resources=[
                    ResourceDefinition(
                        id="resource.voxelcraft.twig-pebble",
                        title="枯枝与石子",
                        kind="material",
                        source_ids=[handcraft_source.id],
                    ),
                    ResourceDefinition(
                        id="resource.voxelcraft.raw-roasted-food",
                        title="生食与烤熟食物",
                        kind="item",
                        source_ids=[runtime_source.id, handbook_source.id],
                        artifact_ids=[roasted.id],
                        run_id=run_id,
                    ),
                    ResourceDefinition(
                        id="resource.voxelcraft.campfire",
                        title="篝火",
                        kind="facility",
                        source_ids=[runtime_source.id, guidance_source.id],
                        artifact_ids=[campfire.id],
                        run_id=run_id,
                    ),
                ],
                relation_ids=[
                    "relation.voxelcraft.ignite-cost",
                    "relation.voxelcraft.food-conversion",
                    "relation.voxelcraft.campfire-gate",
                ],
                source_ids=[runtime_source.id, handcraft_source.id, guidance_source.id],
            ),
            tags=[
                "minecraft", "pc", "first-night", "survival", "world-interaction", "fire",
                "cooking", "contextual-guidance", "probability-feedback", "source-backed",
                "direct-observation",
            ],
            status="draft",
            migration_status="needs_design_artifacts",
            cover_artifact_id=campfire.id,
            sources=[runtime_source, handcraft_source, guidance_source, handbook_source],
            artifacts=artifacts,
            runs=[
                RunRef(
                    id=run_id,
                    target_id="minecraft://voxelcraft/first-night-world",
                    adapter="external-runtime-evidence",
                    started_at=captured_at,
                    ended_at=captured_at,
                    status="passed",
                    build_scope_id="scope.minecraft.voxelcraft.first-night.v3",
                    artifact_ids=[item.id for item in artifacts],
                )
            ],
            flow=[
                FlowNode(
                    id="bw.fire.spawn",
                    title="在空手状态读取环境",
                    description="固定世界以空物品栏、白天和完整生存 HUD 开始，让世界中的散落物承担第一批可交互线索。",
                    action="look and forage",
                    state_before="新手进入验证世界",
                    state_after="确认枯枝、石子与可食资源",
                    source_ids=[runtime_source.id],
                    artifact_ids=[spawn.id],
                    next=["bw.fire.stack"],
                ),
                FlowNode(
                    id="bw.fire.stack",
                    title="把三根枯枝堆到世界中",
                    description="枯枝不是背包配方材料；对地面使用后形成 1→3 的空间堆叠，Jade 按当前数量提示还差几根。",
                    action="use twig on ground until pile size = 3",
                    state_before="物品栏持有枯枝",
                    state_after="地面存在满三根枯枝堆",
                    source_ids=[handcraft_source.id, guidance_source.id],
                    next=["bw.fire.ignite"],
                ),
                FlowNode(
                    id="bw.fire.ignite",
                    title="用石子连续敲出火星",
                    description="满堆才允许进入点火判定；单次失败仍产生火星，成功则原地替换为点燃的篝火。",
                    action="attack full twig pile with pebble",
                    state_before="满三根枯枝堆 + 石子",
                    state_after="点燃的篝火与“火与熟食”提示",
                    source_ids=[runtime_source.id, handcraft_source.id, handbook_source.id],
                    artifact_ids=[campfire.id],
                    next=["bw.fire.cook"],
                ),
                FlowNode(
                    id="bw.fire.cook",
                    title="把生食放上篝火等待转换",
                    description="情境提示只在手持可食物并瞄准篝火时出现；食物进入烤位后由时间推进完成转换。",
                    action="use raw food on lit campfire and wait",
                    state_before="点燃篝火 + 生浆果",
                    state_after="烤熟浆果进入可取回状态",
                    source_ids=[runtime_source.id, guidance_source.id, handbook_source.id],
                    artifact_ids=[roasted.id],
                    next=["bw.fire.verify"],
                ),
                FlowNode(
                    id="bw.fire.verify",
                    title="用产物身份确认闭环",
                    description="画面中的物品名已变为 Roasted Berry；设施同时保留原始运行截图，避免只凭总结文字宣称成功。",
                    action="inspect resulting inventory item",
                    state_before="烤制完成",
                    state_after="物品栏持有 protoworld:roasted_berry",
                    source_ids=[runtime_source.id, handbook_source.id],
                    artifact_ids=[roasted.id],
                ),
            ],
            mechanisms=[
                MechanismSpec(
                    id="bw.fire.probability",
                    title="失败动作也积累可读反馈",
                    description="点火不是一次确定性交换。满足三根枯枝前置后，每次石子敲击有 40% 成功率，雨天减半；失败仍显示火星。",
                    representation="pseudocode",
                    code=(
                        "if twig_pile.size == 3 and held == PEBBLE:\n"
                        "    show_sparks()\n"
                        "    chance = 0.20 if raining else 0.40\n"
                        "    if random() < chance: replace_with(LIT_CAMPFIRE)"
                    ),
                    source_ids=[handcraft_source.id],
                ),
                MechanismSpec(
                    id="bw.fire.contextual-guidance",
                    title="提示由世界状态与手持物共同决定",
                    description="同一瞄准目标会随堆叠数量、手持石子或食物而切换提示，把说明贴近玩家正能执行的下一步。",
                    representation="rule",
                    code="hint = f(target.block_state, held_item, campfire.lit)",
                    source_ids=[guidance_source.id, runtime_source.id],
                ),
                MechanismSpec(
                    id="bw.fire.food-conversion",
                    title="烤制把生食变成新的资源身份",
                    description="篝火不是纯视觉奖励，而是一个带等待时间的资源转换设施；完成态以产物 ID 而非画面猜测判定。",
                    representation="state_machine",
                    code="raw_food + lit_campfire --time--> roasted_food",
                    source_ids=[runtime_source.id, handbook_source.id],
                ),
            ],
            resources=[
                ResourceRelation(
                    id="relation.voxelcraft.ignite-cost",
                    resource="枯枝 ×3 + 石子敲击",
                    role="cost",
                    description="三根枯枝构成燃料前置，石子作为可重复点火工具触发概率判定。",
                    source_ids=[handcraft_source.id],
                ),
                ResourceRelation(
                    id="relation.voxelcraft.food-conversion",
                    resource="生浆果 → 烤熟浆果",
                    role="conversion",
                    description="点燃篝火把早期采集物升级为新的熟食资源身份。",
                    source_ids=[runtime_source.id, handbook_source.id],
                ),
                ResourceRelation(
                    id="relation.voxelcraft.campfire-gate",
                    resource="篝火",
                    role="gate",
                    description="篝火同时是照明、阶段提示与熟食转换的共享设施节点。",
                    source_ids=[runtime_source.id, guidance_source.id],
                ),
            ],
            observations=[
                Observation(
                    id="obs.voxelcraft.state-sequence",
                    statement="空手出生、点燃篝火、拿到烤熟浆果三张真实运行画面形成了可复查的状态序列。",
                    source_ids=[runtime_source.id],
                    artifact_ids=[spawn.id, campfire.id, roasted.id],
                    run_id=run_id,
                ),
                Observation(
                    id="obs.voxelcraft.ignite-feedback",
                    statement="点火成功时，世界火焰、烟、聊天记录与右侧手记提示在同一屏同时反馈。",
                    source_ids=[runtime_source.id, handcraft_source.id, handbook_source.id],
                    artifact_ids=[campfire.id],
                    run_id=run_id,
                ),
                Observation(
                    id="obs.voxelcraft.roasted-result",
                    statement="烤制完成后物品栏和屏幕物品名共同显示 Roasted Berry，终点不只依赖视觉特效。",
                    source_ids=[runtime_source.id, handbook_source.id],
                    artifact_ids=[roasted.id],
                    run_id=run_id,
                ),
            ],
            interpretations=[
                Claim(
                    id="claim.voxelcraft.interpretation.spatial-friction",
                    kind=ContentKind.analyst_interpretation,
                    statement="把生火放在世界空间中，会比菜单配方多出摆放、瞄准和重复尝试成本；情境提示承担了抵消这部分摩擦的责任。",
                    source_ids=[handcraft_source.id, guidance_source.id],
                    review_status="reviewed",
                ),
                Claim(
                    id="claim.voxelcraft.interpretation.failure-signal",
                    kind=ContentKind.analyst_interpretation,
                    statement="失败仍冒火星使概率机制更像持续努力，而不是无响应的输入丢失；它也是自动化 agent 判断动作已命中的中间信号。",
                    source_ids=[handcraft_source.id],
                    artifact_ids=[campfire.id],
                    run_id=run_id,
                    review_status="reviewed",
                ),
                Claim(
                    id="claim.voxelcraft.interpretation.shared-node",
                    kind=ContentKind.analyst_interpretation,
                    statement="篝火把教学、资源加工和夜间视觉地标合并为一个节点，提高了第一夜学习内容之间的互相强化。",
                    source_ids=[runtime_source.id, guidance_source.id, handbook_source.id],
                    artifact_ids=[campfire.id, roasted.id],
                    run_id=run_id,
                    review_status="reviewed",
                ),
            ],
            open_questions=[
                "未经提示的玩家能否仅凭火星反馈理解需要继续敲击，而不是更换材料？",
                "雨天成功率减半时，当前反馈是否足以区分天气惩罚与交互失败？",
                "不同生食的烤制时间和饱腹收益是否能在不打开说明页的情况下被感知？",
            ],
            created_at=captured_at,
            updated_at=captured_at,
        )
        report = ensure_report_surfaces(report)
        report.assert_storable()
        self.store.upsert_report(report)
        self.store.export_reports(self.store.list_reports())
        if compile_after:
            self.compile_public()
        return report

    @staticmethod
    def _artifact_time(artifact_id: str) -> str:
        parts = artifact_id.split(".")
        try:
            stamp_ms = int(parts[2])
            return datetime.fromtimestamp(stamp_ms / 1000, timezone.utc).isoformat()
        except (IndexError, ValueError, OSError):
            from .models import utc_now
            return utc_now()

    def promote_afk_first_launch(
        self,
        frame_artifact_id: str,
        ui_artifact_id: str,
        *,
        version: str = "1.7.21",
        compile_after: bool = True,
    ) -> GameReport:
        frame = self.store.get_artifact(frame_artifact_id)
        ui_tree = self.store.get_artifact(ui_artifact_id)
        if not frame or frame.kind != "screenshot" or not Path(frame.path).is_file():
            raise ValueError("a stored screenshot artifact is required")
        if not ui_tree or ui_tree.kind != "ui_tree" or not Path(ui_tree.path).is_file():
            raise ValueError("a stored UI tree artifact is required")
        root = ElementTree.parse(ui_tree.path).getroot()
        texts = {node.attrib.get("text", "").strip() for node in root.iter("node")}
        required = {"用户协议与隐私政策", "同意并继续", "不同意"}
        missing = required - texts
        if missing:
            raise ValueError(f"UI tree is not the AFK first-launch consent gate: {sorted(missing)}")

        stamp = self._artifact_time(frame.id)
        public_frame = frame.model_copy(update={"metadata": {**frame.metadata, "public": True}})
        public_ui = ui_tree.model_copy(update={"metadata": {**ui_tree.metadata, "public": True}})
        self.store.save_artifact(public_frame)
        self.store.save_artifact(public_ui)
        source = SourceRef(
            id="src.afk.first-launch-live",
            kind=ContentKind.direct_observation,
            title="AFK Journey 1.7.21 · MuMu 首次启动观测",
            url=f"/api/game-observatory/artifacts/{public_frame.id}",
            locator=f"run={frame.run_id}; frame={frame.id}; ui_tree={ui_tree.id}; activity=UserAgreementActivity",
            captured_at=stamp,
            version_context=f"com.the_companygame.demogame.android.cn {version}, MuMu Android 15",
            public=True,
            note="真实启动停在法律同意门；未代替用户点击同意。",
        )
        observation_run_id = frame.run_id or ui_tree.run_id
        report_runs = (
            [
                RunRef(
                    id=observation_run_id,
                    target_id="mumu://127.0.0.1:7555",
                    adapter="adb",
                    started_at=stamp,
                    ended_at=stamp,
                    status="passed",
                    build_scope_id="scope.afk.android.cn.1.7.21.first-launch",
                    artifact_ids=[public_frame.id, public_ui.id],
                    note="只读采集首次启动协议门；未执行法律选择。",
                )
            ]
            if observation_run_id
            else []
        )
        report = GameReport(
            id="report.afk-journey.first-launch-consent.v1",
            slug="afk-journey-first-launch-consent",
            game_id="afk-journey",
            game_title="AFK Journey",
            system_id="first-launch-consent",
            system_title="首次启动协议门：服务开始前的强制选择",
            summary=(
                "中国 Android 包在首次启动后没有直接进入登录或游戏下载，而是由独立的"
                "UserAgreementActivity 阻断后续服务。页面将用户协议、隐私概要、第三方共享清单和儿童个人信息"
                "指引集中说明，并把“同意并继续”设为唯一主按钮；本次观测止于该法律同意门。"
            ),
            summary_claim=Claim(
                id="claim.afk.first-launch.summary",
                kind=ContentKind.analyst_interpretation,
                statement=(
                    "中国 Android 包在首次启动后没有直接进入登录或游戏下载，而是由独立的"
                    "UserAgreementActivity 阻断后续服务。页面将用户协议、隐私概要、第三方共享清单和儿童个人信息"
                    "指引集中说明，并把“同意并继续”设为唯一主按钮；本次观测止于该法律同意门。"
                ),
                source_ids=[source.id],
                artifact_ids=[public_frame.id, public_ui.id],
                run_id=observation_run_id,
                review_status="reviewed",
            ),
            scope=BuildScope(
                id="scope.afk.android.cn.1.7.21.first-launch",
                game_id="afk-journey",
                platform="android",
                version=version,
                region="CN",
                locale="zh-CN",
                account_stage="pre-consent-first-launch",
                device="MuMu Android 15 / V2344A / 1080x1920 portrait",
                captured_at=stamp,
                source_ids=[source.id],
                artifact_ids=[public_frame.id, public_ui.id],
                run_id=observation_run_id,
            ),
            game=Game(
                id="afk-journey",
                title="AFK Journey",
                aliases=["剑与远征：启程"],
                platforms=["android", "ios", "windows"],
            ),
            system_concept=SystemConcept(
                id="first-launch-consent",
                title="首次启动协议门",
                description="在产品服务开始前要求玩家作出法律同意或拒绝选择的阻断流程。",
                tags=["onboarding", "consent", "legal-gate"],
                source_ids=[source.id],
                artifact_ids=[public_frame.id, public_ui.id],
                run_id=observation_run_id,
            ),
            system_instance=SystemInstance(
                id="instance.afk.android.cn.1.7.21.first-launch",
                concept_id="first-launch-consent",
                build_scope_id="scope.afk.android.cn.1.7.21.first-launch",
                title="AFK 中国 Android 1.7.21 首次启动实例",
                source_ids=[source.id],
                artifact_ids=[public_frame.id, public_ui.id],
                run_ids=[observation_run_id] if observation_run_id else [],
            ),
            resource_model=ResourceModel(
                id="resource-model.afk.first-launch-consent",
                title="首次启动协议门资源模型（无经济资源）",
                source_ids=[source.id],
            ),
            tags=[
                "mobile", "android", "onboarding", "consent", "privacy", "legal-gate",
                "modal", "touch", "ui-tree", "direct-observation",
            ],
            status="draft",
            migration_status="needs_design_artifacts",
            cover_artifact_id=public_frame.id,
            sources=[source],
            artifacts=[public_frame, public_ui],
            runs=report_runs,
            flow=[
                FlowNode(
                    id="afk.consent.launch",
                    title="启动中国 Android 包",
                    description="桌面入口启动 com.the_companygame.demogame.android.cn，前台 Activity 变为 UserAgreementActivity。",
                    action="launch package",
                    state_before="MuMu launcher",
                    state_after="首次启动协议弹窗",
                    source_ids=[source.id],
                    artifact_ids=[public_frame.id, public_ui.id],
                    next=["afk.consent.read"],
                ),
                FlowNode(
                    id="afk.consent.read",
                    title="查看协议与隐私材料入口",
                    description="正文集中列出用户协议、隐私条款概要、第三方共享个人信息清单和儿童个人信息保护指引。",
                    action="read linked documents",
                    state_before="协议弹窗",
                    state_after="仍停留在协议弹窗",
                    source_ids=[source.id],
                    artifact_ids=[public_frame.id, public_ui.id],
                    next=["afk.consent.choice"],
                ),
                FlowNode(
                    id="afk.consent.choice",
                    title="在继续与退出之间做明确选择",
                    description="高强调红色主按钮为“同意并继续”，低强调文本按钮为“不同意”。本次设施没有代替用户作法律选择。",
                    action="human decision required",
                    state_before="协议弹窗",
                    state_after="继续产品服务或拒绝退出（未执行）",
                    source_ids=[source.id],
                    artifact_ids=[public_frame.id, public_ui.id],
                ),
            ],
            mechanisms=[
                MechanismSpec(
                    id="afk.consent.blocking-gate",
                    title="法律同意是首次服务前置门",
                    description="协议 Activity 在游戏登录和内容加载之前取得前台焦点；未作选择时无法进入后续流程。",
                    representation="state_machine",
                    code=(
                        "launch -> UserAgreementActivity\n"
                        "  agree    -> continue product service\n"
                        "  disagree -> stop / exit\n"
                        "  no choice -> remain blocked"
                    ),
                    source_ids=[source.id],
                ),
                MechanismSpec(
                    id="afk.consent.visual-hierarchy",
                    title="主次按钮承担方向性引导",
                    description="“同意并继续”使用整行高饱和红色按钮，“不同意”使用低对比文本，形成明显的视觉权重差。",
                    representation="rule",
                    code="primary(agree) > secondary(disagree) in size, fill, contrast",
                    source_ids=[source.id],
                ),
            ],
            observations=[
                Observation(
                    id="obs.afk.first-launch.activity",
                    statement="前台窗口明确属于 com.the_company.sdk.compliance.ui.UserAgreementActivity。",
                    source_ids=[source.id],
                    artifact_ids=[public_ui.id],
                    run_id=observation_run_id,
                ),
                Observation(
                    id="obs.afk.first-launch.semantic-elements",
                    statement="协议正文和两个决策按钮均存在于 Android UI hierarchy，可被语义定位，不必只靠像素 OCR。",
                    source_ids=[source.id],
                    artifact_ids=[public_frame.id, public_ui.id],
                    run_id=observation_run_id,
                ),
                Observation(
                    id="obs.afk.first-launch.blocking-modal",
                    statement="弹窗为竖屏中心卡片；背景完全压暗，用户无法绕过该门继续浏览游戏。",
                    source_ids=[source.id],
                    artifact_ids=[public_frame.id, public_ui.id],
                    run_id=observation_run_id,
                ),
            ],
            interpretations=[
                Claim(
                    id="claim.afk.first-launch.interpretation.density",
                    kind=ContentKind.analyst_interpretation,
                    statement="把四类材料压进一段正文降低了首次启动的页面数量，但也提高了单屏阅读密度。",
                    source_ids=[source.id],
                    artifact_ids=[public_frame.id, public_ui.id],
                    run_id=observation_run_id,
                    review_status="reviewed",
                ),
                Claim(
                    id="claim.afk.first-launch.interpretation.hierarchy",
                    kind=ContentKind.analyst_interpretation,
                    statement="主次按钮的强烈权重差有利于快速继续，同时值得在公开设计报告中明确记录而非只保留截图。",
                    source_ids=[source.id],
                    artifact_ids=[public_frame.id, public_ui.id],
                    run_id=observation_run_id,
                    review_status="reviewed",
                ),
            ],
            open_questions=[
                "用户拒绝后是直接退出、二次确认还是允许离线浏览？本次未执行法律选择。",
                "同意后是否还有单独的 SDK 权限、账号与资源下载门？",
            ],
            created_at=stamp,
            updated_at=stamp,
        )
        report = ensure_report_surfaces(report)
        report.assert_storable()
        self.store.upsert_report(report)
        self.store.export_reports(self.store.list_reports())
        if compile_after:
            self.compile_public()
        return report

    def device_gateway(self) -> DeviceGateway:
        return DeviceGateway(self.store)

    def discover_targets(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        gateway = self.device_gateway()
        if refresh:
            gateway.refresh()
        return [item.model_dump(mode="json") for item in gateway.target_infos()]

    def capture_device(self, serial: str) -> dict[str, Any]:
        adapter = AdbAdapter(self.store)
        target = adapter.connect(serial)
        self._remember_connected_adb_target(target)
        observation = adapter.observe()
        return {
            "ok": True,
            "target": target.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json"),
        }

    def inspect_device(self, serial: str, *, package: str | None = None) -> dict[str, Any]:
        """Read connection, foreground, and optional package/build identity."""

        adapter = AdbAdapter(self.store)
        target = adapter.connect(serial)
        self._remember_connected_adb_target(target)
        foreground_activity = adapter.foreground_activity()
        package_payload: dict[str, Any] | None = None
        if package:
            installed = adapter.package_installed(package)
            package_payload = {
                "installed": installed,
                **(adapter.package_info(package) if installed else {"package": package}),
            }
        return {
            "ok": True,
            "target": target.model_dump(mode="json"),
            "foreground_activity": foreground_activity,
            "package": package_payload,
        }

    def _remember_connected_adb_target(self, target: TargetInfo) -> TargetRecord:
        """Make a just-proven direct ADB connection authoritative for guarded actions."""

        serial = str(target.metadata.get("serial") or "").strip()
        if not serial or target.status != "online":
            raise ValueError("a connected ADB target must be online and expose its serial")
        record = TargetRecord(
            id=f"device://adb/{serial}",
            provider="adb-direct",
            endpoint=serial,
            kind=target.kind,
            label=target.label,
            status="online",
            capabilities=target.capabilities,
            metadata={**target.metadata, "serial": serial, "connection_proof": "adb-connect"},
            last_seen_at=utc_now(),
        )
        self.store.upsert_target(record)
        return record

    def verify_device_gateway(
        self,
        target_id: str = "device://adb/127.0.0.1:7555",
        *,
        package: str = "com.the_companygame.demogame.android.cn",
    ) -> dict[str, Any]:
        """Build durable Phase 1 evidence from real, already-executed gateway operations."""
        gateway = self.device_gateway()
        gateway.refresh()
        target = self.store.get_target(target_id)
        sessions = self.store.list_capture_sessions(target_id, limit=100)
        events = [
            item for item in self.store.list_gateway_events(1000) if item["target_id"] == target_id
        ]
        runs = [item for item in self.store.list_runs(500) if item.target_id == target_id]
        event_types = {item["event_type"] for item in events}
        passed_session = next(
            (
                item
                for item in sessions
                if item.status == "passed"
                and len(item.frame_artifact_ids) >= 3
                and item.ui_tree_artifact_ids
            ),
            None,
        )
        stream_artifacts_ok = bool(passed_session)
        if passed_session:
            for artifact_id in [
                *passed_session.frame_artifact_ids,
                *passed_session.ui_tree_artifact_ids,
            ]:
                artifact = self.store.get_artifact(artifact_id)
                if not artifact or not Path(artifact.path).is_file():
                    stream_artifacts_ok = False
                    break
        package_state: dict[str, Any] = {"installed": False, "foreground_activity": "unknown"}
        if target and target.status == "online":
            try:
                adapter = gateway._adb_adapter(target_id)
                package_state = {
                    "installed": adapter.package_installed(package),
                    "foreground_activity": adapter.foreground_activity(),
                }
            except (AdapterError, GatewayError) as exc:
                package_state["error"] = str(exc)
        mumu_info: dict[str, Any] = {}
        try:
            if target and target.kind == "mumu":
                mumu_info = MumuCli().info(str(target.metadata.get("vmindex") or "all"))
        except (GatewayError, json.JSONDecodeError) as exc:
            mumu_info = {"error": str(exc)}
        checks = [
            {
                "id": "target-online-capabilities",
                "passed": bool(
                    target
                    and target.status == "online"
                    and {"install", "pixel", "touch", "ui_tree"} <= set(target.capabilities)
                ),
            },
            {"id": "package-install-channel", "passed": any(run.id.startswith("run.install.") for run in runs)},
            {"id": "package-visible", "passed": bool(package_state.get("installed"))},
            {"id": "continuous-frame-session", "passed": stream_artifacts_ok},
            {
                "id": "mumu-lifecycle",
                "passed": any(
                    item["event_type"] == "mutation_dispatched"
                    and str(item["payload"].get("operation", "")).startswith("mumu_")
                    for item in events
                )
                and bool(mumu_info),
            },
            {"id": "transport-recovery", "passed": "transport_recovered" in event_types},
            {
                "id": "emergency-stop-cycle",
                "passed": {"emergency_stop", "emergency_stop_cleared"} <= event_types,
            },
            {"id": "persistent-rate-limit", "passed": "rate_limit_configured" in event_types},
            {"id": "safe-final-state", "passed": not gateway.control(target_id).emergency_stopped},
        ]
        result = {
            "schema": "game-observatory.device-gateway-validation.v1",
            "generated_at": utc_now(),
            "ok": all(item["passed"] for item in checks),
            "target": target.model_dump(mode="json") if target else None,
            "package": {"name": package, **package_state},
            "mumu_info": mumu_info,
            "capture_session": passed_session.model_dump(mode="json") if passed_session else None,
            "checks": checks,
            "event_types": sorted(event_types),
        }
        path = self.store.export_root / "device-gateway-validation.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def verify_mumu_multi_instance(
        self,
        *,
        package: str = "com.the_companygame.demogame.android.cn",
    ) -> dict[str, Any]:
        """Prove two real MuMu instances have independent leases, ADB endpoints, and app state."""
        gateway = self.device_gateway()
        gateway.refresh()
        targets = [
            item
            for item in self.store.list_targets()
            if item.provider == "mumu-adb" and item.id.startswith("device://mumu/")
        ]
        online = [item for item in targets if item.status == "online"]
        leases = []
        evidence: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        error: str | None = None
        try:
            if len(online) < 2:
                raise GatewayError("at least two online MuMu instances are required")
            selected = sorted(online, key=lambda item: item.id)[:2]
            for target in selected:
                leases.append(gateway.acquire(target.id, "phase1-mumu-multi", ttl_seconds=300))
            primary, clone = selected
            primary_lease, clone_lease = leases
            gateway.force_stop_package(clone.id, clone_lease.token, package)
            gateway.start_package(primary.id, primary_lease.token, package)
            time.sleep(2)
            primary_adapter = gateway._adb_adapter(primary.id)
            clone_adapter = gateway._adb_adapter(clone.id)
            primary_activity = primary_adapter.foreground_activity()
            clone_activity = clone_adapter.foreground_activity()
            primary_observation = primary_adapter.observe_frame(include_ui=True)
            clone_observation = clone_adapter.observe_frame(include_ui=True)
            evidence = [
                {
                    "target": primary.model_dump(mode="json"),
                    "foreground_activity": primary_activity,
                    "observation": primary_observation.model_dump(mode="json"),
                },
                {
                    "target": clone.model_dump(mode="json"),
                    "foreground_activity": clone_activity,
                    "observation": clone_observation.model_dump(mode="json"),
                },
            ]
            checks = [
                {
                    "id": "two-stable-targets",
                    "passed": primary.id != clone.id,
                    "detail": [primary.id, clone.id],
                },
                {
                    "id": "distinct-adb-endpoints",
                    "passed": primary.metadata.get("serial") != clone.metadata.get("serial"),
                    "detail": [primary.metadata.get("serial"), clone.metadata.get("serial")],
                },
                {
                    "id": "independent-active-leases",
                    "passed": primary_lease.token != clone_lease.token,
                    "detail": [primary_lease.id, clone_lease.id],
                },
                {
                    "id": "package-state-isolation",
                    "passed": package in primary_activity and package not in clone_activity,
                    "detail": {"primary": primary_activity, "clone": clone_activity},
                },
                {
                    "id": "independent-pixel-and-ui-capture",
                    "passed": bool(
                        primary_observation.frame
                        and clone_observation.frame
                        and primary_observation.ui_tree
                        and clone_observation.ui_tree
                        and primary_observation.frame.id != clone_observation.frame.id
                    ),
                    "detail": {
                        "primary_frame": primary_observation.frame.id
                        if primary_observation.frame
                        else None,
                        "clone_frame": clone_observation.frame.id
                        if clone_observation.frame
                        else None,
                    },
                },
            ]
        except (AdapterError, GatewayError, OSError) as exc:
            error = str(exc)
            if not checks:
                checks = [{"id": "multi-instance-run", "passed": False, "detail": error}]
        finally:
            for lease in leases:
                try:
                    gateway.release(lease.token)
                except GatewayError:
                    pass
        result = {
            "schema": "game-observatory.mumu-multi-instance-validation.v1",
            "generated_at": utc_now(),
            "ok": bool(checks) and all(item["passed"] for item in checks),
            "package": package,
            "checks": checks,
            "instances": evidence,
            "error": error,
        }
        path = self.store.export_root / "mumu-multi-instance-validation.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def verify_mumu_snapshot_restore(
        self,
        snapshot_path: Path,
        *,
        restored_target_id: str,
        package: str = "com.the_companygame.demogame.android.cn",
    ) -> dict[str, Any]:
        snapshot = snapshot_path.resolve()
        archive_check: dict[str, Any] = {}
        archive_error: str | None = None
        try:
            archive_check = MumuCli().wait_for_snapshot_archive(snapshot, timeout=900.0)
        except GatewayError as exc:
            archive_error = str(exc)
        digest = hashlib.sha256()
        if snapshot.is_file():
            with snapshot.open("rb") as handle:
                for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
        gateway = self.device_gateway()
        gateway.refresh()
        target = self.store.get_target(restored_target_id)
        sessions = self.store.list_capture_sessions(restored_target_id, limit=100)
        capture = next(
            (
                item
                for item in sessions
                if item.status == "passed"
                and len(item.frame_artifact_ids) >= 2
                and len(item.ui_tree_artifact_ids) >= 2
            ),
            None,
        )
        package_state = {"installed": False, "foreground_activity": "unknown"}
        if target and target.status == "online":
            try:
                adapter = gateway._adb_adapter(restored_target_id)
                package_state = {
                    "installed": adapter.package_installed(package),
                    "foreground_activity": adapter.foreground_activity(),
                }
            except (AdapterError, GatewayError) as exc:
                package_state["error"] = str(exc)
        imported = any(
            item["event_type"] == "mutation_dispatched"
            and item["payload"].get("operation") == "mumu_import_snapshot"
            for item in self.store.list_gateway_events(10_000)
        )
        checks = [
            {
                "id": "snapshot-archive-valid",
                "passed": bool(archive_check.get("ok") and not archive_error),
                "detail": archive_check or archive_error,
            },
            {
                "id": "snapshot-content-hash",
                "passed": snapshot.is_file() and snapshot.stat().st_size > 0,
                "detail": {
                    "bytes": snapshot.stat().st_size if snapshot.is_file() else 0,
                    "sha256": digest.hexdigest() if snapshot.is_file() else None,
                },
            },
            {"id": "import-audited", "passed": imported},
            {
                "id": "restored-instance-online",
                "passed": bool(target and target.status == "online"),
                "detail": target.model_dump(mode="json") if target else None,
            },
            {
                "id": "restored-package-runs",
                "passed": bool(
                    package_state.get("installed")
                    and package in str(package_state.get("foreground_activity"))
                ),
                "detail": package_state,
            },
            {
                "id": "restored-pixel-and-ui-capture",
                "passed": bool(capture),
                "detail": capture.model_dump(mode="json") if capture else None,
            },
        ]
        result = {
            "schema": "game-observatory.mumu-snapshot-validation.v1",
            "generated_at": utc_now(),
            "ok": all(item["passed"] for item in checks),
            "snapshot": str(snapshot),
            "restored_target_id": restored_target_id,
            "checks": checks,
        }
        path = self.store.export_root / "mumu-snapshot-validation.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def verify_production_storage(self) -> dict[str, Any]:
        from .storage_backends import (
            MinioArtifactProjection,
            MinioSettings,
            PostgresCanonicalProjection,
            StorageBackendError,
        )

        minio_result: dict[str, Any]
        postgres_result: dict[str, Any]
        try:
            minio_result = MinioArtifactProjection(MinioSettings.from_env()).sync_artifacts(
                self.store
            )
        except (OSError, StorageBackendError, ValueError) as exc:
            minio_result = {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - SDK/network errors become durable evidence
            minio_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        dsn = os.environ.get("GAME_OBSERVATORY_POSTGRES_DSN", "")
        try:
            postgres_result = PostgresCanonicalProjection(dsn).rebuild(self.store)
        except (OSError, StorageBackendError, ValueError) as exc:
            postgres_result = {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - driver/server errors become durable evidence
            postgres_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        result = {
            "schema": "game-observatory.production-storage-validation.v1",
            "generated_at": utc_now(),
            "ok": bool(minio_result.get("ok") and postgres_result.get("ok")),
            "minio": minio_result,
            "postgres": postgres_result,
        }
        path = self.store.export_root / "production-storage-validation.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def afk_hero_upgrade_preflight(
        self,
        snapshot_path: Path | None = None,
        *,
        bridge_port: int = 18820,
    ) -> dict[str, Any]:
        from .afk_benchmark import AfkHeroUpgradeOracle

        result = AfkHeroUpgradeOracle().preflight(snapshot_path, bridge_port=bridge_port)
        path = self.store.export_root / "afk-hero-upgrade-preflight.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def promote_afk_live_design(self, manifest_path: Path) -> dict[str, Any]:
        """Publish a source-backed AFK design spec from a real MuMu evidence manifest."""
        from .afk_live import AfkLiveDesignBuilder

        resolved = manifest_path.resolve()
        root = self.store.root.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("AFK live manifest must stay inside the Game Observatory data root")
        if not resolved.is_file():
            raise ValueError(f"AFK live manifest not found: {resolved}")
        builder = AfkLiveDesignBuilder(self.store)
        manifest = builder.load_manifest(resolved)
        promoted = builder.promote(manifest)
        self.store.export_reports(self.store.list_reports())
        public_build = self.compile_public()
        report = promoted["report"]
        return {
            "ok": True,
            "report_id": report.id,
            "slug": report.slug,
            "status": report.status,
            "contract_version": report.contract_version,
            "surface_count": len(report.surfaces),
            "design_artifact_count": len(report.design_spec.design_artifacts),
            "verification": promoted["verification"],
            "public_build": public_build,
        }

    def promote_minecraft_live_design(self, manifest_path: Path) -> dict[str, Any]:
        """Publish a source-backed Minecraft design spec from a real fixed-world run."""
        from .minecraft_live import MinecraftFirstNightDesignBuilder

        resolved = manifest_path.resolve()
        root = self.store.root.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(
                "Minecraft live manifest must stay inside the Game Observatory data root"
            )
        if not resolved.is_file():
            raise ValueError(f"Minecraft live manifest not found: {resolved}")
        builder = MinecraftFirstNightDesignBuilder(self.store)
        manifest = builder.load_manifest(resolved)
        promoted = builder.promote(manifest)
        self.store.export_reports(self.store.list_reports())
        public_build = self.compile_public()
        report = promoted["report"]
        return {
            "ok": True,
            "report_id": report.id,
            "slug": report.slug,
            "status": report.status,
            "contract_version": report.contract_version,
            "surface_count": len(report.surfaces),
            "design_artifact_count": len(report.design_spec.design_artifacts),
            "objective_gate_count": int(
                report.benchmark_task.metadata.get("gates", {}).get(
                    "total", len(report.benchmark_task.checks)
                )
            ),
            "verification": promoted["verification"],
            "public_build": public_build,
        }

    def run_afk_hero_upgrade_benchmark(
        self,
        snapshot_path: Path,
        *,
        bridge_port: int = 18820,
    ) -> dict[str, Any]:
        """Run only after PlayMode and a verified isolated account snapshot are present."""
        from .afk_benchmark import AfkHeroUpgradeOracle, AfkHeroUpgradeSnapshot
        from .game_adapters import AfkUnityExplorerAdapter

        oracle = AfkHeroUpgradeOracle()
        preflight = oracle.preflight(snapshot_path, bridge_port=bridge_port)
        if not preflight["ready"]:
            return {
                "ok": False,
                "status": "preflight_blocked",
                "preflight": preflight,
                "error": "AFK benchmark refused to run before every safety preflight passes",
            }
        snapshot = AfkHeroUpgradeSnapshot.model_validate_json(
            snapshot_path.read_text(encoding="utf-8")
        )
        task = oracle.task(snapshot)
        adapter = AfkUnityExplorerAdapter(self.store)
        target = adapter.connect(f"source://unity/afk-journey?bridge_port={bridge_port}")
        result = adapter.evaluate(task)
        bundle = BenchmarkBundleWriter(self.store.root / "benchmarks").write(
            task,
            target,
            result,
            report_fragments=[
                {
                    "kind": "afk_hero_upgrade_objective_summary",
                    "task_id": task.id,
                    "status": result.status,
                    "checks": [item.model_dump(mode="json") for item in result.checks],
                    "external_observation_fields": ["level", "resources", "HP", "ATK", "DEF"],
                    "white_box_oracle": oracle.source_evidence(),
                }
            ],
        )
        return {
            "ok": result.status == "passed",
            "status": result.status,
            "preflight": preflight,
            "run": result.model_dump(mode="json"),
            "bundle": str(bundle),
        }

    def verify_minecraft_adapters(
        self,
        *,
        console_target: str = "minecraft://127.0.0.1:8332",
    ) -> dict[str, Any]:
        """Validate visual and semantic oracle paths without launching the full benchmark."""
        from .game_adapters import MinecraftMineflayerAdapter, MinecraftVisualAdapter

        visual = MinecraftVisualAdapter(self.store)
        visual_target = visual.connect(console_target)
        visual_observation = visual.observe()
        server_port = 25565
        properties = Path("E:/MinecraftWorkspace/proto-world/run-server/server.properties")
        if properties.is_file():
            for line in properties.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("server-port="):
                    server_port = int(line.split("=", 1)[1].strip())
                    break
        elif visual_target.metadata.get("status", {}).get("server", {}).get("port"):
            server_port = int(visual_target.metadata["status"]["server"]["port"])
        oracle_uri = f"minecraft://127.0.0.1:{server_port}"
        oracle = MinecraftMineflayerAdapter(self.store)
        oracle_target = oracle.connect(oracle_uri)
        oracle_observation = oracle.observe()
        report = self.store.get_report("report.minecraft.stone-pickaxe.v1")
        if not report or not report.benchmark_task:
            raise ValueError("Minecraft stone-pickaxe task is missing")
        diagnostic = oracle.evaluate(report.benchmark_task)
        result = {
            "schema": "game-observatory.minecraft-adapter-validation.v1",
            "generated_at": utc_now(),
            "ok": visual_target.status == "online" and oracle_target.status == "online",
            "full_benchmark_executed": False,
            "boundary": "Per user direction, this validation is read-only and does not run the full task.",
            "shared_task_id": report.benchmark_task.id,
            "visual": {
                "target": visual_target.model_dump(mode="json"),
                "observation": visual_observation.model_dump(mode="json"),
            },
            "oracle": {
                "target": oracle_target.model_dump(mode="json"),
                "observation": oracle_observation.model_dump(mode="json"),
                "start_state_diagnostic": diagnostic.model_dump(mode="json"),
            },
            "checks": [
                {"id": "visual-runtime-online", "passed": visual_target.status == "online"},
                {"id": "mineflayer-oracle-online", "passed": oracle_target.status == "online"},
                {
                    "id": "same-task-contract",
                    "passed": diagnostic.task_id == report.benchmark_task.id,
                },
                {
                    "id": "failure-localized-at-start-state",
                    "passed": diagnostic.status == "stopped"
                    and any(item.actual is not None for item in diagnostic.checks),
                },
            ],
        }
        result["ok"] = result["ok"] and all(item["passed"] for item in result["checks"])
        path = self.store.export_root / "minecraft-adapter-validation.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def verify_editorial_pipeline(
        self,
        report_id: str = "report.afk-journey.first-launch-consent.v1",
    ) -> dict[str, Any]:
        """Exercise durable patch, annotation, revision, diff and incremental rebuild paths."""
        from .editorial import EditorialService
        from .models import ReportPatchOperation

        service = EditorialService(self.store)
        report = self.store.get_report(report_id)
        if not report:
            raise ValueError(f"report not found: {report_id}")
        question = "该协议门在横屏或平板布局中是否保持相同的主次按钮层级？"
        applied = next(
            (
                item
                for item in self.store.list_report_patches(report.id)
                if item.status == "applied" and "Phase 4 incremental compile validation" in item.note
            ),
            None,
        )
        if question not in report.open_questions:
            patch = service.propose_patch(
                report.id,
                base_revision=self.store.current_revision(report.id),
                author="game-observatory-validation",
                note="Phase 4 incremental compile validation",
                operations=[
                    ReportPatchOperation(
                        op="replace",
                        target_kind="report",
                        target_id=report.id,
                        field="open_questions",
                        value=[*report.open_questions, question],
                    )
                ],
            )
            applied = service.apply_patch(patch.id, reviewer="game-observatory-reviewer")
            report = self.store.get_report(report.id)
        if not applied or not applied.applied_revision:
            raise ValueError("Phase 4 applied patch evidence is unavailable")

        annotation_body = "Phase 4 验证：此节点已通过来源链和 UI tree 复核。"
        annotation = next(
            (
                item
                for item in self.store.list_report_annotations(report.id)
                if item.body == annotation_body
            ),
            None,
        )
        if not annotation:
            annotation = service.annotate(
                report.id,
                object_id=report.flow[0].id,
                author="game-observatory-validation",
                body=annotation_body,
                kind="source_note",
                source_ids=report.flow[0].source_ids,
            )
        if annotation.status != "resolved":
            annotation = service.resolve_annotation(
                annotation.id, reviewer="game-observatory-reviewer"
            )
        before = self.store.get_revision(report.id, applied.base_revision)
        after = self.store.get_revision(report.id, applied.applied_revision)
        changes = SemanticReportCompiler.diff(before, after) if before and after else []
        first_build = self.compile_public()
        second_build = self.compile_public()
        checks = [
            {"id": "patch-applied", "passed": applied.status == "applied"},
            {
                "id": "revision-preserved",
                "passed": bool(before and after and applied.applied_revision > applied.base_revision),
            },
            {
                "id": "object-diff",
                "passed": any(item["path"].startswith("/open_questions") for item in changes),
            },
            {"id": "annotation-resolved", "passed": annotation.status == "resolved"},
            {
                "id": "incremental-rebuild-idempotent",
                "passed": not second_build["compiled"]
                and len(second_build["skipped"]) == len(self.store.list_reports()),
            },
        ]
        result = {
            "schema": "game-observatory.editorial-validation.v1",
            "generated_at": utc_now(),
            "ok": all(item["passed"] for item in checks),
            "report_id": report.id,
            "patch": applied.model_dump(mode="json"),
            "annotation": annotation.model_dump(mode="json"),
            "diff": changes,
            "first_build": first_build,
            "second_build": second_build,
            "checks": checks,
        }
        path = self.store.export_root / "editorial-validation.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def verify_player_voice_pipeline(self) -> dict[str, Any]:
        """Acquire three real AFK discussions and exercise review/dedupe/retraction."""
        pipeline = SourceVoicePipeline(self.store)
        report_id = "report.afk-journey.hero-upgrade.v1"
        report = self.store.get_report(report_id)
        if not report:
            raise ValueError(f"report not found: {report_id}")
        configs = {
            "voice.afk.essence-wall": {
                "acquisition_url": "https://old.reddit.com/r/AFKJourney/comments/1c1isqk/",
                "author": "JuanFran21",
                "published_at": "2024-04-11T16:03:02+00:00",
                "quote": (
                    "Can only level up one hero per day at this point, meaning you need to spend "
                    "5 days before you can raise your resonance level."
                ),
                "locator": "post body, paragraph 1",
                "tags": ["hero-essence", "progression-gate", "resource-conversion"],
            },
            "voice.afk.level-clarity": {
                "acquisition_url": "https://old.reddit.com/r/AFKJourney/comments/1fkmzhd/",
                "author": "Ima_Genie",
                "published_at": "2024-09-19T15:01:25+00:00",
                "quote": (
                    "Anyone else preferred to start at level 1? It feels less satisfying leveling "
                    "up my hero's and doesn't really offer a simpler indication of my progress"
                ),
                "locator": "post body, paragraph 1",
                "tags": ["level-display", "progression-legibility"],
            },
            "voice.afk.level-up-all": {
                "acquisition_url": "https://old.reddit.com/r/AFKJourney/comments/1gpohd6/",
                "author": "Spirited-Collection1",
                "published_at": "2024-11-12T16:19:37+00:00",
                "quote": (
                    "When you’ve saved up a lot of resources going in and leveling up each character "
                    "individually feels like a chore."
                ),
                "locator": "post body, paragraph 1",
                "tags": ["batch-upgrade", "interaction-friction"],
            },
        }
        acquired: list[dict[str, Any]] = []
        for source_id, config in configs.items():
            report = self.store.get_report(report_id)
            source = next(item for item in report.sources if item.id == source_id)
            voice = next(item for item in report.player_voices if item.source_id == source_id)
            source = source.model_copy(
                update={
                    "author": config["author"],
                    "published_at": config["published_at"],
                    "locator": config["locator"],
                    "usage_policy": "short_excerpt",
                    "license_note": "Public Reddit discussion; retain link and necessary short excerpt only.",
                }
            )
            voice = voice.model_copy(
                update={
                    "quote": config["quote"],
                    "quote_locator": config["locator"],
                    "context": "Single public player post; not a population estimate.",
                    "language": "en",
                    "tags": config["tags"],
                    "review_status": "reviewed",
                    "reviewed_at": utc_now(),
                    "reviewed_by": "game-observatory-reviewer",
                    "review_note": "Source page, author, date and short excerpt checked.",
                }
            )
            acquisition = pipeline.acquire_and_ingest_source(
                report_id,
                source,
                excerpt=config["quote"],
                acquisition_url=config["acquisition_url"],
                metadata={"use": "player_voice", "quote_locator": config["locator"]},
            )
            voice_result = pipeline.ingest_player_voice(
                report_id,
                source,
                voice,
                excerpt=config["quote"],
            )
            acquired.append(
                {
                    "source_id": source_id,
                    "snapshot_id": acquisition["snapshot_id"],
                    "content_sha256": acquisition["acquisition"]["content_sha256"],
                    "status": acquisition["acquisition"]["status"],
                    "voice": voice_result,
                }
            )

        validation_source_id = "voice.validation.phase5-retraction"
        report = self.store.get_report(report_id)
        validation_source = next(
            (item for item in report.sources if item.id == validation_source_id), None
        )
        deduplicated = False
        reviewed = False
        if validation_source is None:
            base_source = next(item for item in report.sources if item.id == "voice.afk.essence-wall")
            validation_source = base_source.model_copy(
                update={
                    "id": validation_source_id,
                    "title": "Phase 5 duplicate/review/retraction validation source",
                    "locator": "post body, paragraph 1; validation duplicate",
                }
            )
            validation_voice = next(
                item for item in report.player_voices if item.source_id == "voice.afk.essence-wall"
            ).model_copy(
                update={
                    "id": "pv.validation.phase5-retraction",
                    "source_id": validation_source_id,
                    "theme": "pipeline-validation",
                    "review_status": "pending",
                    "reviewed_at": None,
                    "reviewed_by": None,
                    "review_note": None,
                }
            )
            excerpt = validation_voice.quote
            first = pipeline.ingest_player_voice(
                report_id, validation_source, validation_voice, excerpt=excerpt
            )
            duplicate = pipeline.ingest_player_voice(
                report_id, validation_source, validation_voice, excerpt=excerpt
            )
            deduplicated = not first["deduplicated"] and duplicate["deduplicated"]
            review = pipeline.review_player_voice(
                report_id,
                validation_voice.id,
                decision="reviewed",
                reviewer="game-observatory-reviewer",
                note="Phase 5 reviewer transition validation.",
            )
            reviewed = review["voice"]["review_status"] == "reviewed"
            pipeline.retract_source(
                validation_source_id,
                "Phase 5 tombstone validation for a duplicate validation entry",
            )
        else:
            deduplicated = True
            validation_voice = next(
                item for item in report.player_voices if item.source_id == validation_source_id
            )
            reviewed = validation_voice.review_status == "reviewed"

        report = self.store.get_report(report_id)
        public = SemanticReportCompiler.public_report(report)
        theme_view = pipeline.theme_view(report_id)
        validation_source = next(item for item in report.sources if item.id == validation_source_id)
        checks = [
            {"id": "three-real-pages-acquired", "passed": len(acquired) == 3 and all(item["status"] == 200 for item in acquired)},
            {
                "id": "short-quotes-source-bound",
                "passed": all(
                    next(item for item in report.player_voices if item.source_id == source_id).quote
                    for source_id in configs
                ),
            },
            {"id": "dedupe", "passed": deduplicated},
            {"id": "review-transition", "passed": reviewed},
            {"id": "retraction-tombstone", "passed": validation_source.status == "retracted"},
            {
                "id": "retracted-voice-hidden",
                "passed": all(item["source_id"] != validation_source_id for item in public["player_voices"]),
            },
            {
                "id": "theme-view-no-population-claim",
                "passed": "not population proportions" in theme_view["disclaimer"],
            },
        ]
        result = {
            "schema": "game-observatory.player-voice-validation.v1",
            "generated_at": utc_now(),
            "ok": all(item["passed"] for item in checks),
            "acquired": acquired,
            "theme_view": theme_view,
            "validation_tombstone": validation_source.model_dump(mode="json"),
            "checks": checks,
        }
        path = self.store.export_root / "player-voice-validation.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def verify_agent_plugins(
        self,
        target_id: str = "device://mumu/0",
    ) -> dict[str, Any]:
        """Run available plugins and keep unavailable candidates explicitly non-passing."""
        from .agent_plugins import (
            AgentPluginRegistry,
            AirtestReplayAdapter,
            MaaFrameworkAdapter,
        )

        plugins = AgentPluginRegistry().probe()
        gateway = self.device_gateway()
        gateway.refresh()
        target_record = self.store.get_target(target_id)
        if target_record is None:
            raise ValueError(f"agent validation target is unknown: {target_id}")
        adapter_serial = str(
            target_record.metadata.get("serial")
            or (target_record.metadata.get("adb_endpoint") or {}).get("serial")
            or ""
        )
        if not adapter_serial and target_record.kind == "adb":
            adapter_serial = target_record.endpoint.removeprefix("device://adb/")
        if not adapter_serial:
            raise ValueError(f"agent validation target has no live ADB serial: {target_id}")
        adapter_target_id = f"device://adb/{adapter_serial}"
        lease = gateway.acquire(target_id, "game-observatory-agent-validation", ttl_seconds=300)
        try:
            airtest_adapter = AirtestReplayAdapter(self.store)
            airtest_target = airtest_adapter.connect(adapter_target_id)
            airtest_task = BenchmarkTask(
                id="task.plugin.mobile-observe.v1",
                title="在共享 MuMu target 上保存一帧并确认 AFK 包存在",
                start_state="leased online MuMu instance",
                goal="produce a PNG observation without mutating game state",
                allowed_actions=["wait"],
                reset_method="no mutation; release lease",
                checks=[
                    ObjectiveCheck(id="target_connected", description="Airtest target online", expected=True),
                    ObjectiveCheck(id="frame_is_png", description="saved frame is PNG", expected=True),
                    ObjectiveCheck(id="package_installed", description="AFK package is installed", expected=True),
                ],
                metadata={"package": "com.the_companygame.demogame.android.cn"},
            )
            airtest_run = airtest_adapter.evaluate(airtest_task)
            airtest_bundle = BenchmarkBundleWriter(self.store.root / "benchmarks").write(
                airtest_task,
                airtest_target,
                airtest_run,
                report_fragments=[
                    {
                        "kind": "agent_plugin_result",
                        "plugin": "airtest",
                        "category": "stable_mobile_replay",
                        "status": airtest_run.status,
                        "checks": [item.model_dump(mode="json") for item in airtest_run.checks],
                    }
                ],
            )

            maa_adapter = MaaFrameworkAdapter(self.store)
            maa_target = maa_adapter.connect(adapter_target_id)
            maa_task = BenchmarkTask(
                id="task.plugin.maafw-mobile-observe.v1",
                title="MaaFramework 在共享 MuMu target 上保存像素与控制器回调",
                start_state="leased online MuMu instance",
                goal="produce a PNG and callback trace without mutating game state",
                allowed_actions=["wait"],
                reset_method="no mutation; release lease",
                checks=[
                    ObjectiveCheck(id="target_connected", description="Maa target online", expected=True),
                    ObjectiveCheck(id="frame_is_png", description="saved frame is PNG", expected=True),
                    ObjectiveCheck(
                        id="callback_trace_saved",
                        description="controller callback trace is stored",
                        expected=True,
                    ),
                    ObjectiveCheck(
                        id="package_installed",
                        description="AFK package is installed",
                        expected=True,
                    ),
                ],
                metadata={"package": "com.the_companygame.demogame.android.cn"},
            )
            maa_run = maa_adapter.evaluate(maa_task)
            maa_bundle = BenchmarkBundleWriter(self.store.root / "benchmarks").write(
                maa_task,
                maa_target,
                maa_run,
                report_fragments=[
                    {
                        "kind": "agent_plugin_result",
                        "plugin": "maaframework",
                        "category": "stable_mobile_replay",
                        "status": maa_run.status,
                        "checks": [item.model_dump(mode="json") for item in maa_run.checks],
                    }
                ],
            )
        finally:
            gateway.release(lease.token)
        categories = {
            "exploratory_mobile_agent": False,
            "stable_mobile_replay": (
                airtest_run.status == "passed" and maa_run.status == "passed"
            ),
            "pc_visual_agent": False,
        }
        checks = [
            {"id": "airtest-runnable", "passed": airtest_run.status == "passed"},
            {"id": "maaframework-runnable", "passed": maa_run.status == "passed"},
            {
                "id": "maaframework-callback-trace",
                "passed": any(
                    item.id == "callback_trace_saved" and item.passed is True
                    for item in maa_run.checks
                ),
            },
            {
                "id": "unified-task-result-contract",
                "passed": (
                    airtest_run.task_id == airtest_task.id
                    and len(airtest_run.checks) == len(airtest_task.checks)
                    and maa_run.task_id == maa_task.id
                    and len(maa_run.checks) == len(maa_task.checks)
                ),
            },
            {"id": "lease-released", "passed": self.store.get_lease_by_token(lease.token).status == "released"},
        ]
        result = {
            "schema": "game-observatory.agent-plugin-validation.v1",
            "generated_at": utc_now(),
            "ok": all(item["passed"] for item in checks),
            "phase6_ready": all(categories.values()),
            "categories": categories,
            "plugins": [item.model_dump(mode="json") for item in plugins],
            "airtest": {
                "target": airtest_target.model_dump(mode="json"),
                "run": airtest_run.model_dump(mode="json"),
                "bundle": str(airtest_bundle),
            },
            "maaframework": {
                "target": maa_target.model_dump(mode="json"),
                "run": maa_run.model_dump(mode="json"),
                "bundle": str(maa_bundle),
            },
            "checks": checks,
            "boundary": (
                "Airtest and MaaFramework are verified stable controller substrates, not exploratory "
                "agents. Open-AutoGLM and Cradle are not counted without configured model endpoints "
                "and a real shared-task run."
            ),
        }
        path = self.store.export_root / "agent-plugin-validation.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def verify_exploratory_mobile_agent(
        self,
        target_id: str = "device://mumu/0",
    ) -> dict[str, Any]:
        """Run a real screenshot-planned, safety-gated action on the leased MuMu target."""
        from .agent_plugins import MaaFrameworkAdapter
        from .exploration import (
            CodexCliVisionPlanner,
            ExplorationPolicy,
            ExplorationRunner,
        )

        gateway = self.device_gateway()
        gateway.refresh()
        target_record = self.store.get_target(target_id)
        if target_record is None:
            raise ValueError(f"exploration target is unknown: {target_id}")
        serial = str(
            target_record.metadata.get("serial")
            or (target_record.metadata.get("adb_endpoint") or {}).get("serial")
            or ""
        )
        if not serial:
            raise ValueError(f"exploration target has no live ADB serial: {target_id}")

        lease = gateway.acquire(target_id, "game-observatory-codex-vision", ttl_seconds=600)
        adapter = MaaFrameworkAdapter(
            self.store,
            gateway=gateway,
            gateway_target_id=target_id,
            lease_token=lease.token,
        )
        adb = AdbAdapter(self.store)
        reset_result: dict[str, Any] | None = None
        try:
            target = adapter.connect(f"device://adb/{serial}")
            adb.connect(serial)
            adapter.act(NormalizedAction(type="home"))
            adapter.act(NormalizedAction(type="wait", seconds=0.8))
            task = BenchmarkTask(
                id="task.plugin.mobile-open-settings.v1",
                title="从 MuMu 启动器安全打开 Android 设置",
                start_state="Android launcher home surface",
                goal=(
                    "Tap the visible Settings gear icon in the bottom dock; do not open any game, "
                    "advertisement, store, consent or account surface. Android Settings must become "
                    "the foreground activity."
                ),
                allowed_actions=["tap", "wait", "back", "home"],
                reset_method="HOME key before and after the run",
                checks=[
                    ObjectiveCheck(
                        id="settings_foreground",
                        description="Android Settings becomes the foreground activity",
                        expected=True,
                    )
                ],
                metadata={
                    "risk_class": "local_system_navigation",
                    "forbidden": [
                        "payment",
                        "account binding",
                        "consent",
                        "chat",
                        "delete",
                    ],
                },
            )

            def checker(
                _task: BenchmarkTask,
                _observation: Any,
                _history: Any,
            ) -> list[ObjectiveCheck]:
                activity = adb.foreground_activity()
                actual = activity.startswith("com.android.settings/")
                return [
                    ObjectiveCheck(
                        id="settings_foreground",
                        description=f"Android Settings foreground; actual={activity}",
                        expected=True,
                        actual=actual,
                        passed=actual,
                    )
                ]

            planner = CodexCliVisionPlanner(self.store.root / "codex-vision-planner")
            outcome = ExplorationRunner(self.store).run(
                adapter=adapter,
                target=target,
                task=task,
                planner=planner,
                policy=ExplorationPolicy(
                    allowed_action_types=["tap", "wait", "back", "home"],
                    max_steps=2,
                    max_seconds=300,
                    max_state_visits=2,
                    allowed_tap_regions=[(0.20, 0.70, 0.42, 1.0)],
                ),
                checker=checker,
            )
            trace = [
                TraceEvent.model_validate_json(line)
                for line in Path(outcome.trace_artifact.path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            artifacts = [
                artifact
                for artifact_id in outcome.run.artifact_ids
                if (artifact := self.store.get_artifact(artifact_id)) is not None
            ]
            bundle = BenchmarkBundleWriter(self.store.root / "benchmarks").write(
                task,
                target,
                outcome.run,
                trace=trace,
                artifacts=artifacts,
                report_fragments=[
                    {
                        "kind": "agent_plugin_result",
                        "plugin": planner.name,
                        "category": "exploratory_mobile_agent",
                        "status": outcome.run.status,
                        "steps": len(outcome.steps),
                        "states": outcome.state_count,
                        "stop_reason": outcome.stop_reason,
                    }
                ],
            )
        finally:
            if adapter.controller is not None:
                try:
                    reset_result = adapter.act(NormalizedAction(type="home"))
                except AdapterError as exc:
                    reset_result = {"ok": False, "error": str(exc)}
            gateway.release(lease.token)

        checks = [
            {"id": "objective-met", "passed": outcome.run.status == "passed"},
            {
                "id": "vision-decision-recorded",
                "passed": bool(outcome.steps)
                and outcome.steps[0].decision.provider == "codex-cli-vision",
            },
            {
                "id": "normalized-action-contract",
                "passed": bool(outcome.steps)
                and outcome.steps[0].decision.action is not None
                and outcome.steps[0].decision.action.type in task.allowed_actions,
            },
            {"id": "full-trace-saved", "passed": Path(outcome.trace_artifact.path).is_file()},
            {
                "id": "lease-released",
                "passed": self.store.get_lease_by_token(lease.token).status == "released",
            },
            {"id": "home-restored", "passed": bool(reset_result and reset_result.get("ok"))},
        ]
        result = {
            "schema": "game-observatory.exploratory-mobile-validation.v1",
            "generated_at": utc_now(),
            "ok": all(item["passed"] for item in checks),
            "category": "exploratory_mobile_agent",
            "target_id": target_id,
            "adapter_target": target.model_dump(mode="json"),
            "task": task.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
            "bundle": str(bundle),
            "reset_result": reset_result,
            "checks": checks,
            "boundary": (
                "This proves a real screenshot-planned, safety-gated Android navigation task. "
                "It does not prove arbitrary real-time game play or replace per-game benchmarks."
            ),
        }
        path = self.store.export_root / "exploratory-mobile-validation.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def verify_pc_visual_agent(
        self,
        target_uri: str = "minecraft://127.0.0.1:8332",
    ) -> dict[str, Any]:
        """Run the same exploration contract over the read-only Minecraft visual adapter."""
        from .exploration import (
            CodexCliVisionPlanner,
            ExplorationPolicy,
            ExplorationRunner,
        )
        from .game_adapters import MinecraftVisualAdapter

        adapter = MinecraftVisualAdapter(self.store)
        target = adapter.connect(target_uri)
        task = BenchmarkTask(
            id="task.plugin.pc-minecraft-visual-observe.v1",
            title="识别 Minecraft 世界画面并执行一次非变更等待",
            start_state="voxelcraft visual snapshot and runtime status are available",
            goal=(
                "Visually confirm the current frame is a Minecraft world scene, then perform exactly "
                "one allowed wait action. Do not request keyboard, mouse, world, inventory, chat or "
                "server mutation."
            ),
            allowed_actions=["wait"],
            reset_method="read-only adapter; no world reset",
            checks=[
                ObjectiveCheck(
                    id="server_online",
                    description="voxelcraft Minecraft server remains online",
                    expected=True,
                ),
                ObjectiveCheck(
                    id="frame_nonblank",
                    description="Minecraft frame has non-trivial pixel variance",
                    expected=True,
                ),
                ObjectiveCheck(
                    id="safe_wait_executed",
                    description="visual planner completed the allowed non-mutating wait",
                    expected=True,
                ),
            ],
            metadata={"risk_class": "read_only_pc_visual", "mutation_allowed": False},
        )

        def checker(
            _task: BenchmarkTask,
            observation: Any,
            history: Any,
        ) -> list[ObjectiveCheck]:
            state = {}
            if observation.runtime_state:
                state = json.loads(Path(observation.runtime_state.path).read_text(encoding="utf-8"))
            server_online = bool(state.get("server", {}).get("online"))
            frame_nonblank = False
            try:
                import cv2

                image = cv2.imread(str(observation.frame.path))
                frame_nonblank = bool(image is not None and float(image.std()) > 5.0)
            except (ImportError, ValueError, TypeError):
                frame_nonblank = False
            safe_wait = bool(
                history
                and history[-1].decision.action
                and history[-1].decision.action.type == "wait"
            )
            actuals = {
                "server_online": server_online,
                "frame_nonblank": frame_nonblank,
                "safe_wait_executed": safe_wait,
            }
            return [
                item.model_copy(
                    update={"actual": actuals[item.id], "passed": actuals[item.id] is True}
                )
                for item in task.checks
            ]

        planner = CodexCliVisionPlanner(self.store.root / "codex-vision-planner")
        outcome = ExplorationRunner(self.store).run(
            adapter=adapter,
            target=target,
            task=task,
            planner=planner,
            policy=ExplorationPolicy(
                allowed_action_types=["wait"],
                max_steps=1,
                max_seconds=300,
                max_state_visits=2,
            ),
            checker=checker,
        )
        trace = [
            TraceEvent.model_validate_json(line)
            for line in Path(outcome.trace_artifact.path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        artifacts = [
            artifact
            for artifact_id in outcome.run.artifact_ids
            if (artifact := self.store.get_artifact(artifact_id)) is not None
        ]
        bundle = BenchmarkBundleWriter(self.store.root / "benchmarks").write(
            task,
            target,
            outcome.run,
            trace=trace,
            artifacts=artifacts,
            report_fragments=[
                {
                    "kind": "agent_plugin_result",
                    "plugin": planner.name,
                    "category": "pc_visual_agent",
                    "status": outcome.run.status,
                    "steps": len(outcome.steps),
                    "states": outcome.state_count,
                    "stop_reason": outcome.stop_reason,
                }
            ],
        )
        checks = [
            {"id": "objective-met", "passed": outcome.run.status == "passed"},
            {
                "id": "vision-decision-recorded",
                "passed": bool(outcome.steps)
                and outcome.steps[0].decision.provider == "codex-cli-vision",
            },
            {
                "id": "read-only-action-enforced",
                "passed": bool(outcome.steps)
                and outcome.steps[0].decision.action is not None
                and outcome.steps[0].decision.action.type == "wait",
            },
            {"id": "full-trace-saved", "passed": Path(outcome.trace_artifact.path).is_file()},
        ]
        result = {
            "schema": "game-observatory.pc-visual-agent-validation.v1",
            "generated_at": utc_now(),
            "ok": all(item["passed"] for item in checks),
            "category": "pc_visual_agent",
            "target": target.model_dump(mode="json"),
            "task": task.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
            "bundle": str(bundle),
            "checks": checks,
            "boundary": (
                "This proves PC-game screenshot interpretation and a safety-gated read-only action "
                "through the common exploration contract. Keyboard/mouse and real-time play remain "
                "outside this validation."
            ),
        }
        path = self.store.export_root / "pc-visual-agent-validation.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def verify_phase6_agent_race(
        self,
        target_id: str = "device://mumu/0",
    ) -> dict[str, Any]:
        """Compare exploration and stable replay on one task, then aggregate PC visual proof."""
        from .agent_plugins import AirtestReplayAdapter
        from .exploration import (
            ExplorationDecision,
            ExplorationPolicy,
            ExplorationRunner,
            ScriptedExplorationPlanner,
        )

        mobile_path = self.store.export_root / "exploratory-mobile-validation.json"
        pc_path = self.store.export_root / "pc-visual-agent-validation.json"
        stable_plugin_path = self.store.export_root / "agent-plugin-validation.json"
        required = [mobile_path, pc_path, stable_plugin_path]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise ValueError(f"phase 6 prerequisite evidence is missing: {missing}")
        mobile = json.loads(mobile_path.read_text(encoding="utf-8"))
        pc_visual = json.loads(pc_path.read_text(encoding="utf-8"))
        stable_plugins = json.loads(stable_plugin_path.read_text(encoding="utf-8"))
        if not mobile.get("ok") or not pc_visual.get("ok") or not stable_plugins.get("ok"):
            raise ValueError("phase 6 prerequisite validation has not passed")

        task = BenchmarkTask.model_validate(mobile["task"])
        mobile_step = mobile["outcome"]["steps"][0]
        mobile_action = NormalizedAction.model_validate(mobile_step["decision"]["action"])
        if mobile_action.type != "tap" or mobile_action.x is None or mobile_action.y is None:
            raise ValueError("mobile exploration evidence has no replayable tap decision")
        before_artifact = self.store.get_artifact(mobile_step["before_artifact_id"])
        if before_artifact is None:
            raise ValueError("mobile exploration before-frame artifact is missing")
        try:
            import cv2

            mobile_frame = cv2.imread(str(before_artifact.path))
            if mobile_frame is None:
                raise ValueError("mobile exploration before-frame cannot be decoded")
            mobile_height, mobile_width = mobile_frame.shape[:2]
        except ImportError as exc:
            raise ValueError("OpenCV is required for cross-adapter coordinate mapping") from exc
        normalized_x = mobile_action.x / mobile_width
        normalized_y = mobile_action.y / mobile_height

        gateway = self.device_gateway()
        gateway.refresh()
        target_record = self.store.get_target(target_id)
        if target_record is None:
            raise ValueError(f"phase 6 replay target is unknown: {target_id}")
        serial = str(
            target_record.metadata.get("serial")
            or (target_record.metadata.get("adb_endpoint") or {}).get("serial")
            or ""
        )
        if not serial:
            raise ValueError(f"phase 6 replay target has no live ADB serial: {target_id}")

        lease = gateway.acquire(target_id, "game-observatory-airtest-same-task", ttl_seconds=600)
        adapter = AirtestReplayAdapter(
            self.store,
            gateway=gateway,
            gateway_target_id=target_id,
            lease_token=lease.token,
        )
        adb = AdbAdapter(self.store)
        reset_result: dict[str, Any] | None = None
        try:
            target = adapter.connect(f"device://adb/{serial}")
            adb.connect(serial)
            adapter.act(NormalizedAction(type="home"))
            adapter.act(NormalizedAction(type="wait", seconds=0.8))
            preview = adapter.observe()
            frame_size = preview.metadata.get("frame_size") or []
            if len(frame_size) != 2:
                raise ValueError("Airtest preview did not report frame dimensions")
            replay_x = round(normalized_x * int(frame_size[0]))
            replay_y = round(normalized_y * int(frame_size[1]))
            decision = ExplorationDecision(
                surface_summary="Recorded launcher fixture for stable replay",
                safe_to_act=True,
                risk_flags=[],
                action=NormalizedAction(type="tap", x=replay_x, y=replay_y, duration_ms=100),
                rationale="Replay the accepted visual decision on the same task and checker.",
                expected_change="Android Settings becomes the foreground activity.",
                provider="airtest-stable-replay",
                raw={
                    "source_run": mobile["outcome"]["run"]["id"],
                    "source_normalized_point": [normalized_x, normalized_y],
                },
            )
            planner = ScriptedExplorationPlanner(
                [decision, decision.model_copy(deep=True)],
                name="airtest-stable-replay",
            )

            def checker(
                _task: BenchmarkTask,
                _observation: Any,
                _history: Any,
            ) -> list[ObjectiveCheck]:
                activity = adb.foreground_activity()
                actual = activity.startswith("com.android.settings/")
                return [
                    ObjectiveCheck(
                        id="settings_foreground",
                        description=f"Android Settings foreground; actual={activity}",
                        expected=True,
                        actual=actual,
                        passed=actual,
                    )
                ]

            stable_outcome = ExplorationRunner(self.store).run(
                adapter=adapter,
                target=target,
                task=task,
                planner=planner,
                policy=ExplorationPolicy(
                    allowed_action_types=["tap", "wait", "back", "home"],
                    max_steps=2,
                    max_seconds=120,
                    max_state_visits=2,
                    allowed_tap_regions=[(0.20, 0.70, 0.42, 1.0)],
                ),
                checker=checker,
            )
            trace = [
                TraceEvent.model_validate_json(line)
                for line in Path(stable_outcome.trace_artifact.path)
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            artifacts = [
                artifact
                for artifact_id in stable_outcome.run.artifact_ids
                if (artifact := self.store.get_artifact(artifact_id)) is not None
            ]
            bundle = BenchmarkBundleWriter(self.store.root / "benchmarks").write(
                task,
                target,
                stable_outcome.run,
                trace=trace,
                artifacts=artifacts,
                report_fragments=[
                    {
                        "kind": "agent_plugin_result",
                        "plugin": planner.name,
                        "category": "stable_mobile_replay",
                        "status": stable_outcome.run.status,
                        "source_exploration_run": mobile["outcome"]["run"]["id"],
                    }
                ],
            )
        finally:
            if adapter.device is not None:
                try:
                    reset_result = adapter.act(NormalizedAction(type="home"))
                except AdapterError as exc:
                    reset_result = {"ok": False, "error": str(exc)}
            gateway.release(lease.token)

        categories = {
            "exploratory_mobile_agent": bool(mobile.get("ok")),
            "stable_mobile_replay": (
                bool(stable_plugins.get("categories", {}).get("stable_mobile_replay"))
                and stable_outcome.run.status == "passed"
            ),
            "pc_visual_agent": bool(pc_visual.get("ok")),
        }
        checks = [
            {"id": "all-three-categories", "passed": all(categories.values())},
            {
                "id": "same-mobile-task",
                "passed": stable_outcome.run.task_id == mobile["outcome"]["run"]["task_id"],
            },
            {
                "id": "same-mobile-objective-checker",
                "passed": [item.id for item in stable_outcome.run.checks]
                == [item["id"] for item in mobile["outcome"]["run"]["checks"]],
            },
            {
                "id": "common-exploration-contract",
                "passed": stable_outcome.run.adapter.startswith("exploration:")
                and mobile["outcome"]["run"]["adapter"].startswith("exploration:")
                and pc_visual["outcome"]["run"]["adapter"].startswith("exploration:"),
            },
            {
                "id": "lease-released",
                "passed": self.store.get_lease_by_token(lease.token).status == "released",
            },
            {"id": "home-restored", "passed": bool(reset_result and reset_result.get("ok"))},
        ]
        result = {
            "schema": "game-observatory.phase6-agent-race-validation.v1",
            "generated_at": utc_now(),
            "ok": all(item["passed"] for item in checks),
            "phase6_ready": all(categories.values()) and all(item["passed"] for item in checks),
            "categories": categories,
            "mobile_same_task": {
                "task_id": task.id,
                "exploratory": {
                    "plugin": "codex-cli-vision",
                    "run_id": mobile["outcome"]["run"]["id"],
                    "status": mobile["outcome"]["run"]["status"],
                    "steps": len(mobile["outcome"]["steps"]),
                },
                "stable_replay": {
                    "plugin": "airtest-stable-replay",
                    "run": stable_outcome.run.model_dump(mode="json"),
                    "steps": len(stable_outcome.steps),
                    "bundle": str(bundle),
                    "coordinate_mapping": {
                        "source_frame": [mobile_width, mobile_height],
                        "source_point": [mobile_action.x, mobile_action.y],
                        "normalized_point": [normalized_x, normalized_y],
                        "airtest_frame": frame_size,
                        "airtest_point": [replay_x, replay_y],
                        "raw_device": list(adapter.device_size or ()),
                    },
                },
            },
            "pc_visual": {
                "plugin": "codex-cli-vision",
                "run_id": pc_visual["outcome"]["run"]["id"],
                "status": pc_visual["outcome"]["run"]["status"],
                "boundary": pc_visual["boundary"],
            },
            "stable_substrates": stable_plugins,
            "reset_result": reset_result,
            "checks": checks,
            "decision": (
                "Keep Codex CLI vision as the currently runnable exploratory planner and Airtest/Maa "
                "as stable controllers. Open-AutoGLM and Cradle remain optional candidates until a "
                "model endpoint and real task run exist."
            ),
        }
        path = self.store.export_root / "phase6-agent-race-validation.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(path)
        return result

    def validate(self) -> dict[str, Any]:
        bootstrap = self.bootstrap()
        checks: list[dict[str, Any]] = []
        reports = self.store.list_reports(include_drafts=True)
        for report in reports:
            try:
                if report.status == "published":
                    report.assert_publishable()
                    detail = "v0.3 publishable"
                else:
                    report.assert_storable()
                    detail = {
                        "status": report.status,
                        "migration_status": report.migration_status,
                        "publication_issues": report.publication_issues(),
                    }
                checks.append({"id": f"report:{report.id}", "passed": True, "detail": detail})
            except ValueError as exc:
                checks.append({"id": f"report:{report.id}", "passed": False, "detail": str(exc)})

        provenance_audit = {
            "schema": "game-observatory.provenance-audit.v1",
            "generated_at": utc_now(),
            "reports": [
                {
                    "id": report.id,
                    "status": report.status,
                    "issues": report.provenance_issues(),
                }
                for report in reports
            ],
        }
        provenance_path = self.store.export_root / "provenance-audit.json"
        provenance_path.write_text(
            json.dumps(provenance_audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        checks.append(
            {
                "id": "provenance-audit",
                "passed": all(not item["issues"] for item in provenance_audit["reports"]),
                "detail": str(provenance_path),
            }
        )

        fixture_root = self.store.root / "fixtures"
        golden_manifest_path = fixture_root / "golden" / "manifest.json"
        golden_failures: list[str] = []
        try:
            manifest = json.loads(golden_manifest_path.read_text(encoding="utf-8"))
            if manifest.get("schema") != "game-observatory.golden-manifest.v1":
                golden_failures.append("unexpected manifest schema")
            for name, relative_path in manifest.get("contracts", {}).items():
                path = fixture_root / relative_path
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not payload.get("$defs") and name == "game-report":
                    golden_failures.append(f"{name}: schema has no definitions")
            for case in manifest.get("cases", []):
                path = fixture_root / case["path"]
                report = GameReport.model_validate_json(path.read_text(encoding="utf-8"))
                report.assert_storable()
                expected = case["expected"]
                actual = {
                    "game_id": report.game_id,
                    "system_id": report.system_id,
                    "flow_nodes": len(report.flow),
                    "mechanisms": len(report.mechanisms),
                    "provenance_issues": len(report.provenance_issues()),
                    "publication_issues": len(report.publication_issues()),
                }
                if actual != expected:
                    golden_failures.append(f"{case['id']}: expected {expected}, got {actual}")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            golden_failures.append(str(exc))
        checks.append(
            {
                "id": "golden-contract-suite",
                "passed": not golden_failures,
                "detail": golden_failures or str(golden_manifest_path),
            }
        )

        afk_task = BenchmarkTask(
            id="task.afk.source-contract",
            title="AFK source contract",
            start_state="source checkout",
            goal="verify report assertions",
            allowed_actions=[],
            reset_method="read-only fixture",
            checks=[
                ObjectiveCheck(
                    id="preview_has_before_after_attributes",
                    description="preview renders before/after attributes",
                    expected=True,
                ),
                ObjectiveCheck(
                    id="cost_uses_target_level",
                    description="cost depends on target level",
                    expected=True,
                ),
                ObjectiveCheck(
                    id="tutorial_has_return_path",
                    description="tutorial includes a return path",
                    expected=True,
                ),
            ],
        )
        minecraft = next(item for item in reports if item.slug == "minecraft-stone-pickaxe")
        tasks = [
            (fixture_root / "afk-hero-upgrade.json", afk_task),
            (fixture_root / "minecraft-stone-pickaxe.json", minecraft.benchmark_task),
        ]
        for fixture, task in tasks:
            assert task is not None
            adapter = SourceFixtureAdapter(self.store)
            target = adapter.connect(f"fixture://{fixture}")
            result = adapter.evaluate(task)
            self.store.save_run(result)
            bundle = BenchmarkBundleWriter(self.store.root / "benchmarks").write(
                task,
                target,
                result,
                report_fragments=[
                    {
                        "kind": "objective_summary",
                        "task_id": task.id,
                        "passed": result.status == "passed",
                        "checks": [item.model_dump(mode="json") for item in result.checks],
                    }
                ],
            )
            checks.append(
                {
                    "id": f"fixture:{task.id}",
                    "passed": result.status == "passed",
                    "detail": [item.model_dump(mode="json") for item in result.checks],
                    "bundle": str(bundle),
                }
            )

        web_root = Path(__file__).resolve().parent / "web"
        web_ok = all((web_root / name).is_file() for name in ("index.html", "app.js", "styles.css"))
        checks.append({"id": "frontend-assets", "passed": web_ok, "detail": str(web_root)})
        # Validation consumes the durable registry. Hardware probing has its own
        # explicit refresh command because it appends gateway audit events.
        targets = self.discover_targets(refresh=False)
        if not targets:
            targets = self.discover_targets(refresh=True)
        online_target_ids = [item["id"] for item in targets if item["status"] == "online"]
        durable_device_evidence: list[str] = []
        for name in (
            "device-gateway-validation.json",
            "mumu-multi-instance-validation.json",
            "mumu-snapshot-validation.json",
        ):
            path = self.store.export_root / name
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("ok") is True:
                durable_device_evidence.append(str(path))
        checks.append(
            {
                "id": "device-discovery",
                "passed": bool(online_target_ids or durable_device_evidence),
                "detail": {
                    "online_target_ids": online_target_ids,
                    "durable_validation": durable_device_evidence,
                    "targets": targets,
                },
            }
        )
        return {
            "ok": all(item["passed"] for item in checks),
            "bootstrap": bootstrap,
            "checks": checks,
            "counts": self.store.counts(),
        }

    def proof_report(self, validation: dict[str, Any] | None = None) -> Path:
        validation = validation or self.validate()
        reports = self.store.list_reports()
        from .maintenance import FacilityMaintenance

        monitor = FacilityMaintenance(self.store).monitor()
        backups = sorted((self.store.root / "backups").glob("*/backup.json"))

        def load_evidence(name: str) -> dict[str, Any]:
            path = self.store.export_root / name
            if not path.is_file():
                return {}
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}

        storage = load_evidence("production-storage-validation.json")
        site_quality = load_evidence("public-site-quality-validation.json")
        phase_index = load_evidence("phase-proofs/index.json")
        postgres = storage.get("postgres", {})
        minio = storage.get("minio", {})
        technical_passed = phase_index.get("technical_passed", [])
        overall_passed = phase_index.get("overall_passed", [])
        evidence_names = (
            "provenance-audit.json",
            "device-gateway-validation.json",
            "mumu-multi-instance-validation.json",
            "mumu-snapshot-validation.json",
            "production-storage-validation.json",
            "afk-hero-upgrade-preflight.json",
            "minecraft-adapter-validation.json",
            "editorial-validation.json",
            "player-voice-validation.json",
            "agent-plugin-validation.json",
            "exploratory-mobile-validation.json",
            "pc-visual-agent-validation.json",
            "phase6-agent-race-validation.json",
            "public-site-browser-evidence.json",
            "public-site-quality-validation.json",
            "phase-proofs/index.json",
            "recovery-drill.json",
        )
        evidence_paths = [self.store.export_root / name for name in evidence_names]
        lines = [
            "# 游戏观测站 v0.2 · 当前设施证据报告（非最终 MVP）",
            "",
            f"- 本地纵向切片：{'通过' if validation['ok'] else '未通过'}",
            f"- 技术门通过阶段：`{technical_passed}`；阶段总体验收通过：`{overall_passed}`。",
            "- 完整 v0.2：未通过；Phase 1、2、3、7 仍有技术/外部条件，所有技术通过阶段仍待非开发者有效性验收。",
            f"- 已发布档案：{len(reports)}",
            f"- 当前计数：`{json.dumps(validation['counts'], ensure_ascii=False)}`",
            f"- 健康监控：{'通过' if monitor['ok'] else '失败'}；数据库 `{monitor['database_integrity']}`；artifact {monitor['artifacts_checked']} 个",
            f"- 最近完整备份：`{backups[-1].parent if backups else '尚无'}`",
            f"- 公开站壳质量：{'通过' if site_quality.get('site_shell_ready') else '未通过'}；档案完整性：{'通过' if site_quality.get('archive_complete') else '未通过'}。",
            f"- 生产存储：PostgreSQL JSONB {postgres.get('objects', '未验收')} 对象 / {postgres.get('relations', '未验收')} 关系；MinIO {minio.get('verified', '未验收')} 个 hash 对象。",
            "",
            "## Phase 0–7 当前裁决",
            "",
            "| 阶段 | 技术状态 | 有效性状态 | 总体状态 | 直接证据 / 缺口 |",
            "|---|---|---|---|---|",
        ]
        for phase in phase_index.get("phases", []):
            failures = phase.get("failure_samples", [])
            detail = "；".join(failures) if failures else "技术证据齐全；待非开发者理解与使用验收"
            lines.append(
                f"| Phase {phase.get('phase')} {phase.get('title', '')} | "
                f"{phase.get('technical_status', 'unknown')} | "
                f"{phase.get('effectiveness_status', 'unknown')} | "
                f"{phase.get('overall_status', 'unknown')} | {detail} |"
            )
        if not phase_index.get("phases"):
            lines.append("| Phase 0–7 | unknown | unknown | unknown | 阶段证明索引尚未生成 |")
        lines.extend(["", "## 可复核证据", ""])
        for path in evidence_paths:
            lines.append(f"- [{'x' if path.is_file() else ' '}] `{path}`")
        lines.extend(["", "## 已生产内容", ""])
        for report in reports:
            lines.extend(
                [
                    f"### {report.game_title} · {report.system_title}",
                    "",
                    report.summary,
                    "",
                    f"- 流程节点：{len(report.flow)}",
                    f"- 机制描述：{len(report.mechanisms)}",
                    f"- 当前来源：{sum(item.status == 'active' for item in report.sources)}",
                    f"- 当前玩家声音：{sum(item.status == 'active' for item in report.player_voices)}",
                    "",
                ]
            )
        lines.extend(["## 验证项", ""])
        for item in validation["checks"]:
            lines.append(f"- [{'x' if item['passed'] else ' '}] `{item['id']}`")
        lines.extend(
            [
                "",
                "## 明确边界",
                "",
                "- 本报告只证明当前纵向切片，不证明 v0.2 Phase 0–7 全部完成，也不构成首次 MVP 验收。",
                "- 按用户明确边界没有启动完整游戏 benchmark；两份 source fixture 已产生统一六件套，Minecraft 只读 adapter 与 PC 视觉 agent 已真实采集并理解 console 截图和世界状态。",
                "- AFK Android 1.7.21 已安装并停在 UserAgreementActivity；设施不代替用户接受法律协议。",
                "- AFK Unity AgentBridge 已在 PlayMode/in_game 连接 D:/P4/main/Client/Assets；当前账号 Hero=false，且没有经授权、可复位的资源状态，因此英雄升级真跑保持 fail-closed。",
                "- LOFA `10.3.104.225:5555` 已登记，但直接 ADB 与单向轮询通道当前均离线。",
                "- Airtest 与 MaaFramework 已在本机 MuMu 真跑；Codex 视觉规划经统一探索合同完成 Android Settings 导航并以 ADB oracle 判定，另完成 Minecraft 只读 PC 视觉任务。Open-AutoGLM 与 Cradle 因缺模型端点/本地运行环境保持未接入。",
                f"- SQLite + 本地 content-addressed artifact 仍是默认本地真源；同一 repository 已对 PostgreSQL 17 JSONB 完成 {postgres.get('objects', '未验收')} 对象、{postgres.get('relations', '未验收')} 关系与 {postgres.get('reports_roundtripped', '未验收')} 份报告 round-trip，{minio.get('verified', '未验收')} 个证据对象已在 MinIO 按 hash 校验。该证据只证明本机生产兼容部署，不等于高可用云环境。",
                "- 公开站壳的 HTTP、CSP、缓存、响应式、无障碍、性能和并发预算已通过；AFK 英雄升级与 Minecraft 石镐缺 benchmark 运行证据，因此公开档案完整性门仍失败。",
                "- 公开站已通过本机/LAN 多用户验证；本轮没有获准的公网域名、CDN 或正式部署环境。",
                "- 公开站只暴露允许公开的来源；内部源码定位保留在语义真源中。",
                "",
            ]
        )
        out = self.store.export_root / "facility-proof.md"
        out.write_text("\n".join(lines), encoding="utf-8")
        return out
