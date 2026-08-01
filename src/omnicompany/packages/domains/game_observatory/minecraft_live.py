from __future__ import annotations

import hashlib
import html
import json
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

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
    EvidenceRun,
    EvidenceRunManifest,
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
    RunResult,
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


SCREEN_ROLES = (
    "world_start",
    "campfire",
    "roasted_food",
    "night_threat",
    "night_burning",
    "night_light",
    "recipe_zeroed",
)


class MinecraftFirstNightEvidenceManifest(BaseModel):
    schema_version: Literal["game-observatory.minecraft-first-night-evidence.v1"] = (
        "game-observatory.minecraft-first-night-evidence.v1"
    )
    source_root: str = "E:/MinecraftWorkspace/proto-world"
    game_version: str = "Minecraft Java 1.21.1 / ProtoWorld first-night v4"
    server_address: str = "127.0.0.1:25599"
    world_snapshot: str
    benchmark_player: str
    captured_at: str = Field(default_factory=utc_now)
    e2e_gates_path: str
    screenshot_paths: dict[str, str]
    run_screenshot_roles: list[str] = Field(
        default_factory=lambda: [
            "campfire",
            "roasted_food",
            "night_threat",
            "night_burning",
            "night_light",
        ]
    )
    recipe_probe_response: str
    reset_after_run: bool
    note: str | None = None

    def required_role_issues(self) -> list[str]:
        return [role for role in SCREEN_ROLES if not self.screenshot_paths.get(role)]

    def invalid_run_roles(self) -> list[str]:
        return sorted(set(self.run_screenshot_roles) - set(SCREEN_ROLES))


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


class MinecraftFirstNightDesignBuilder:
    """Compile a real ProtoWorld first-night run into a v0.3 design specification."""

    REPORT_ID = "report.minecraft.voxelcraft-fire-food.v2"
    SLUG = "minecraft-first-night-fire-and-food"
    SCOPE_ID = "scope.minecraft.protoworld.first-night.v4"
    SYSTEM_ID = "minecraft-world-fire-food"
    INSTANCE_ID = "instance.minecraft.protoworld.first-night.v4"
    DESIGN_RUN_ID = "run.minecraft.design-reconstruction.20260713"

    def __init__(self, store: ObservatoryStore) -> None:
        self.store = store

    @staticmethod
    def load_manifest(path: Path) -> MinecraftFirstNightEvidenceManifest:
        return MinecraftFirstNightEvidenceManifest.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    @staticmethod
    def _inside_root(root: Path, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.expanduser().resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Minecraft evidence escapes source_root: {candidate}") from exc
        return candidate

    def _input_paths(
        self, manifest: MinecraftFirstNightEvidenceManifest
    ) -> tuple[Path, dict[str, Path]]:
        missing_roles = manifest.required_role_issues()
        if missing_roles:
            raise ValueError(f"Minecraft evidence manifest is missing roles: {missing_roles}")
        root = Path(manifest.source_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Minecraft source root does not exist: {root}")
        gates_path = self._inside_root(root, manifest.e2e_gates_path)
        if not gates_path.is_file():
            raise ValueError(f"Minecraft E2E gates file does not exist: {gates_path}")
        screenshots = {
            role: self._inside_root(root, manifest.screenshot_paths[role])
            for role in SCREEN_ROLES
        }
        missing = [str(path) for path in screenshots.values() if not path.is_file()]
        if missing:
            raise ValueError(f"Minecraft screenshots do not exist: {missing}")
        return gates_path, screenshots

    @staticmethod
    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def source_evidence(self, manifest: MinecraftFirstNightEvidenceManifest) -> dict[str, Any]:
        root = Path(manifest.source_root).expanduser().resolve()
        handcraft_rules = (
            root / "src/main/java/com/voxelcraft/protoworld/craft/HandcraftRules.java"
        )
        legacy_handcraft_system = (
            root / "src/main/java/com/voxelcraft/protoworld/craft/HandcraftSystem.java"
        )
        files = {
            "handcraft": (
                handcraft_rules if handcraft_rules.is_file() else legacy_handcraft_system
            ),
            "jade": root / "src/main/java/com/voxelcraft/protoworld/client/jade/ProtoWorldJadePlugin.java",
            "recipe_zeroing": root / "src/main/java/com/voxelcraft/protoworld/recipe/RecipeZeroingHook.java",
            "body": root / "src/main/java/com/voxelcraft/protoworld/body/BodySystem.java",
            "food_profiles": root / "specs/food_profiles.json",
            "handbook_entries": root / "specs/handbook_entries.json",
            "e2e": root / "tools/tests/first_night_e2e.py",
        }
        result: dict[str, Any] = {"root": str(root), "files": {}, "checks": []}
        symbol_checks = {
            "handcraft": (
                "ProtoWorldBlocks.TWIG_PILE",
                "CampfireBlock",
                "FIRE_CHANCE = 0.4f",
            ),
            "jade": ("CAMPFIRE", "UNFIRED_BOWL"),
            "recipe_zeroing": ("setRecipes", "protoworld"),
            "body": ("HungerManager", "night_fire"),
            "food_profiles": ("roasted_berry", "fruit"),
            "handbook_entries": ("fire_and_food", "roasted_berry"),
            "e2e": ("G3", "G4", "G24"),
        }
        for key, path in files.items():
            if not path.is_file():
                result["checks"].append({"id": f"source-{key}", "passed": False})
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            symbols = symbol_checks[key]
            passed = all(symbol in text for symbol in symbols)
            result["files"][key] = {
                "path": str(path),
                "sha256": self._sha(path),
                "symbols": list(symbols),
            }
            result["checks"].append({"id": f"source-{key}", "passed": passed})
        result["ok"] = len(result["files"]) == len(files) and all(
            item["passed"] for item in result["checks"]
        )
        return result

    def register_canonical_evidence_run(
        self,
        manifest: MinecraftFirstNightEvidenceManifest,
    ) -> dict[str, Any]:
        """Register an already completed deterministic real-input run without publishing a report.

        The imported run keeps the objective gate file, its source-oracle snapshot, and only the
        screenshots explicitly declared as belonging to that run. It deliberately does not invent
        per-action EvidenceStep objects when the source run did not preserve before/action/after
        video clips.
        """

        invalid_roles = manifest.invalid_run_roles()
        if invalid_roles:
            raise ValueError(f"Minecraft evidence manifest has invalid run roles: {invalid_roles}")
        gates_path, screenshot_paths = self._input_paths(manifest)
        gates_payload = json.loads(gates_path.read_text(encoding="utf-8"))
        passed = int(gates_payload.get("passed", 0))
        total = int(gates_payload.get("total", 0))
        gate_items = gates_payload.get("gates", [])
        if total <= 0 or passed != total or len(gate_items) != total:
            raise ValueError(
                "Minecraft canonical EvidenceRun requires a complete all-passed objective gate set"
            )
        if any(not isinstance(item, dict) or item.get("ok") is not True for item in gate_items):
            raise ValueError("Minecraft canonical EvidenceRun contains a failed objective gate")

        source_evidence = self.source_evidence(manifest)
        if source_evidence.get("ok") is not True:
            raise ValueError("Minecraft canonical EvidenceRun source-oracle checks failed")

        gates_sha = self._sha(gates_path)
        digest = gates_sha[:16]
        run_id = f"evidence.run.minecraft.first-night.{digest}"
        artifact_prefix = f"art.minecraft.canonical.{digest}"
        artifact_ids: list[str] = []
        artifacts: list[ArtifactRef] = []

        def import_artifact(
            artifact_id: str,
            source: Path,
            *,
            kind: Literal["screenshot", "runtime_state", "source"],
            title: str,
            media_type: str,
        ) -> ArtifactRef:
            suffix = source.suffix.lower() or ".bin"
            target = self.store.artifact_root / f"{artifact_id}{suffix}"
            if source.resolve() != target.resolve():
                shutil.copyfile(source, target)
            artifact = ArtifactRef(
                id=artifact_id,
                kind=kind,
                path=str(target),
                sha256=self._sha(target),
                run_id=run_id,
                locator=str(source),
                media_type=media_type,
                metadata={
                    "title": title,
                    "source_path": str(source),
                    "source_snapshot_commit": manifest.world_snapshot,
                    "benchmark_player": manifest.benchmark_player,
                },
            )
            self.store.save_artifact(artifact)
            artifacts.append(artifact)
            artifact_ids.append(artifact.id)
            return artifact

        imported_screens: dict[str, ArtifactRef] = {}
        for role in manifest.run_screenshot_roles:
            imported_screens[role] = import_artifact(
                f"{artifact_prefix}.{role.replace('_', '-')}",
                screenshot_paths[role],
                kind="screenshot",
                title=f"ProtoWorld first-night E2E · {role}",
                media_type="image/png",
            )
        gates_artifact = import_artifact(
            f"{artifact_prefix}.gates",
            gates_path,
            kind="runtime_state",
            title=f"ProtoWorld first-night objective gates · {passed}/{total}",
            media_type="application/json",
        )
        report_path = gates_path.with_name("report.md")
        report_artifact: ArtifactRef | None = None
        if report_path.is_file():
            report_artifact = import_artifact(
                f"{artifact_prefix}.report",
                report_path,
                kind="source",
                title="ProtoWorld 第一夜真输入 E2E 验证报告",
                media_type="text/markdown",
            )

        source_snapshot_path = self.store.artifact_root / f"{artifact_prefix}.source-oracle.json"
        source_snapshot_path.write_text(
            json.dumps(source_evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        source_artifact = ArtifactRef(
            id=f"{artifact_prefix}.source-oracle",
            kind="source",
            path=str(source_snapshot_path),
            sha256=self._sha(source_snapshot_path),
            run_id=run_id,
            locator=str(Path(manifest.source_root).resolve()),
            media_type="application/json",
            metadata={
                "title": "ProtoWorld first-night source-oracle snapshot",
                "source_snapshot_commit": manifest.world_snapshot,
            },
        )
        self.store.save_artifact(source_artifact)
        artifacts.append(source_artifact)
        artifact_ids.append(source_artifact.id)

        run_timestamp = manifest.captured_at
        run = EvidenceRun(
            id=run_id,
            target_id=f"minecraft://{manifest.server_address}/protoworld-first-night",
            adapter="proto-world-first-night-real-input-e2e",
            status="passed",
            game_id="minecraft-java",
            build_scope_id=f"minecraft-java-1.21.1-protoworld-{manifest.world_snapshot[:12]}",
            scope_id="scope.minecraft.protoworld.first-night.fire-food",
            viewport_width=854,
            viewport_height=480,
            orientation="landscape",
            environment={
                "source_root": str(Path(manifest.source_root).resolve()),
                "source_snapshot_commit": manifest.world_snapshot,
                "game_version": manifest.game_version,
                "server_address": manifest.server_address,
                "benchmark_player": manifest.benchmark_player,
                "objective_gates": {"passed": passed, "total": total},
                "gates_sha256": gates_sha,
                "driver": "real client KeyBinding/mouseClicked input; administrator commands only arrange deterministic fixtures",
                "assertion_oracles": "server RCON, handbook dump, and world/body player state",
                "capture_time_semantics": "completion timestamp from the preserved E2E output",
                "reset_after_run": manifest.reset_after_run,
                "visual_replay_boundary": "selected milestone screenshots; no per-action video was preserved",
            },
            started_at=run_timestamp,
            ended_at=run_timestamp,
            step_ids=[],
            artifact_ids=artifact_ids,
            manifest_id=f"manifest.{run_id}",
        )
        self.store.save_evidence_run(run)
        publication_issue = (
            "The source E2E preserved objective gates and milestone screenshots, but no "
            "per-action before/action/after video; no EvidenceStep was synthesized."
        )
        evidence_manifest = EvidenceRunManifest(
            id=f"manifest.{run_id}",
            evidence_run_id=run_id,
            generated_at=utc_now(),
            run=run,
            steps=[],
            artifact_ids=artifact_ids,
            action_run_ids=[],
            observation_run_ids=[],
            publication_issues=[publication_issue],
            publishable=False,
        )
        self.store.save_evidence_manifest(evidence_manifest)
        return {
            "run": run,
            "manifest": evidence_manifest,
            "artifacts": artifacts,
            "screens": imported_screens,
            "gates_artifact": gates_artifact,
            "report_artifact": report_artifact,
            "source_artifact": source_artifact,
            "gates": gates_payload,
            "source_evidence": source_evidence,
        }

    def _public_screenshot(
        self,
        role: str,
        source: Path,
        manifest: MinecraftFirstNightEvidenceManifest,
        run_id: str,
    ) -> ArtifactRef:
        body = source.read_bytes()
        suffix = source.suffix.lower() or ".png"
        artifact_id = f"art.minecraft.live.{role}"
        path = self.store.artifact_root / f"{artifact_id}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != path.resolve():
            shutil.copyfile(source, path)
        artifact = ArtifactRef(
            id=artifact_id,
            kind="screenshot",
            path=str(path),
            sha256=hashlib.sha256(body).hexdigest(),
            run_id=run_id,
            locator=source.name,
            media_type="image/png",
            metadata={
                "public": True,
                "evidence_role": role,
                "origin_path": str(source),
                "world_snapshot": manifest.world_snapshot,
                "game_version": manifest.game_version,
                "resolution": "854x480",
            },
        )
        issues = artifact_file_issues(artifact)
        if issues:
            raise ValueError("; ".join(issues))
        return artifact

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
        width, height = 854, 480
        palette = ["#d8e4ee", "#eadfc8", "#d9e8dc", "#ead8d8", "#ded9ea"]
        blocks: list[str] = []
        for index, element in enumerate(surface.elements):
            bounds = element.bounds or _rect(0.1, 0.1 + index * 0.1, 0.8, 0.08)
            x = round(bounds.x * width)
            y = round(bounds.y * height)
            rect_width = max(10, round(bounds.width * width))
            rect_height = max(10, round(bounds.height * height))
            label = html.escape(element.label or element.id)
            blocks.append(
                f'<g><rect x="{x}" y="{y}" width="{rect_width}" height="{rect_height}" '
                f'rx="6" fill="{palette[index % len(palette)]}" stroke="#26364a" stroke-width="2"/>'
                f'<text x="{x + 7}" y="{y + 18}" font-size="12" fill="#182230">{label}</text></g>'
            )
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="854" height="480" '
            'viewBox="0 0 854 480" role="img" aria-labelledby="title desc">'
            f'<title id="title">{html.escape(surface.title)} reverse-engineered wireframe</title>'
            f'<desc id="desc">{html.escape(surface.description or "")}</desc>'
            '<rect width="854" height="480" fill="#f4f0e7"/>'
            '<rect x="12" y="12" width="830" height="456" rx="18" fill="#fff" '
            'stroke="#172536" stroke-width="3"/>'
            f'<text x="28" y="42" font-size="19" font-weight="700" fill="#172536">{html.escape(surface.title)}</text>'
            '<text x="28" y="61" font-size="10" fill="#64748b">16:9 normalized reconstruction</text>'
            + "".join(blocks)
            + "</svg>"
        )

    @staticmethod
    def _sources(source_evidence: dict[str, Any], captured_at: str) -> list[SourceRef]:
        files = source_evidence["files"]
        return [
            SourceRef(
                id="src.minecraft.first-night-e2e",
                kind=ContentKind.direct_observation,
                title="ProtoWorld first-night real-input E2E",
                url="source://proto-world/tools/tests/first_night_e2e.py",
                locator="G1-G24; real KeyBinding and Screen input path",
                captured_at=captured_at,
                version_context=f"sha256:{files['e2e']['sha256']}",
                public=False,
                usage_policy="internal_evidence",
                note="固定世界中由客户端真实输入路径执行的 24 门验证；画面、RCON 世界状态和物品 ID 联合判定。",
            ),
            SourceRef(
                id="src.minecraft.handcraft",
                kind=ContentKind.direct_observation,
                title="ProtoWorld HandcraftSystem.java",
                url="source://proto-world/src/main/java/com/voxelcraft/protoworld/craft/HandcraftSystem.java",
                locator="world handcraft rules: twig pile, ignition, fire use",
                captured_at=captured_at,
                version_context=f"sha256:{files['handcraft']['sha256']}",
                public=False,
                usage_policy="internal_evidence",
                note="世界内堆枝、石子点火、点燃篝火上的食物和陶器交互规则。",
            ),
            SourceRef(
                id="src.minecraft.jade-guidance",
                kind=ContentKind.direct_observation,
                title="ProtoWorld contextual Jade guidance",
                url="source://proto-world/src/main/java/com/voxelcraft/protoworld/client/jade/ProtoWorldJadePlugin.java",
                locator="campfire and handcraft contextual hints",
                captured_at=captured_at,
                version_context=f"sha256:{files['jade']['sha256']}",
                public=False,
                usage_policy="internal_evidence",
                note="准星目标、堆枝进度、手持物和篝火状态共同决定下一步提示。",
            ),
            SourceRef(
                id="src.minecraft.recipe-zeroing",
                kind=ContentKind.direct_observation,
                title="ProtoWorld RecipeZeroingHook.java",
                url="source://proto-world/src/main/java/com/voxelcraft/protoworld/recipe/RecipeZeroingHook.java",
                locator="server recipe reload hook",
                captured_at=captured_at,
                version_context=f"sha256:{files['recipe_zeroing']['sha256']}",
                public=False,
                usage_policy="internal_evidence",
                note="服务端重载后清除非 protoworld 命名空间配方，原版石镐路径因此不是本实例的有效系统。",
            ),
            SourceRef(
                id="src.minecraft.body-system",
                kind=ContentKind.direct_observation,
                title="ProtoWorld BodySystem.java",
                url="source://proto-world/src/main/java/com/voxelcraft/protoworld/body/BodySystem.java",
                locator="hunger, saturation, meals, regeneration and night-fire triggers",
                captured_at=captured_at,
                version_context=f"sha256:{files['body']['sha256']}",
                public=False,
                usage_policy="internal_evidence",
                note="食物谱、顶饿储备、回血消耗与夜间火光反馈的运行规则。",
            ),
            SourceRef(
                id="src.minecraft.food-profiles",
                kind=ContentKind.direct_observation,
                title="ProtoWorld food_profiles.json",
                url="source://proto-world/specs/food_profiles.json",
                locator="raw and roasted food class mapping",
                captured_at=captured_at,
                version_context=f"sha256:{files['food_profiles']['sha256']}",
                public=False,
                usage_policy="internal_evidence",
                note="果、菌、肉、谷等食物元素谱及生熟物品身份。",
            ),
            SourceRef(
                id="src.minecraft.handbook-fire-food",
                kind=ContentKind.direct_observation,
                title="ProtoWorld 火与熟食手记定义",
                url="source://proto-world/specs/handbook_entries.json",
                locator="fire_and_food entry and roasted-food triggers",
                captured_at=captured_at,
                version_context=f"sha256:{files['handbook_entries']['sha256']}",
                public=False,
                usage_policy="internal_evidence",
                note="点燃篝火、取得熟食和夜间火光会解锁分段式知识反馈。",
            ),
            SourceRef(
                id="voice.minecraft.hunger-depth",
                kind=ContentKind.player_voice,
                title="How would you improve the hunger system?",
                url="https://www.reddit.com/r/minecraftsuggestions/comments/12ikobu/",
                author="Reddit / r/minecraftsuggestions",
                published_at="2023-04-11",
                version_context="community discussion across survival versions",
                note="玩家认为饥饿在开局应形成生存管理，但中后期容易退化成周期性吃东西的例行负担，并希望复杂餐食和饮食多样性更有价值。",
            ),
            SourceRef(
                id="voice.minecraft.campfire-legibility",
                kind=ContentKind.player_voice,
                title="Campfire Revamp",
                url="https://feedback.minecraft.net/hc/en-us/community/posts/360071395512-Campfire-Revamp",
                author="Minecraft Feedback community",
                version_context="vanilla campfire feedback",
                note="玩家希望营火上的食物熟成状态更可见，并可由明确的右键交互取回。",
            ),
            SourceRef(
                id="voice.minecraft.food-diversity",
                kind=ContentKind.player_voice,
                title="Food diversity mechanics and additional foods",
                url="https://www.reddit.com/r/minecraftsuggestions/comments/1gj1723/",
                author="Reddit / r/minecraftsuggestions",
                published_at="2024-11-04",
                version_context="Java survival food discussion",
                note="玩家指出饥饿与饱和是主要数值，但多种食物之间缺少持续的策略差异；食物多样性主要在资源紧缺的开局出现。",
            ),
        ]

    @staticmethod
    def _surfaces(
        screens: dict[str, ArtifactRef],
        runtime_source: str,
        recipe_source: str,
        baseline_run_id: str,
        e2e_run_id: str,
    ) -> list[Surface]:
        world = screens["world_start"]
        fire = screens["campfire"]
        food = screens["roasted_food"]
        recipe = screens["recipe_zeroed"]
        return [
            Surface(
                id="surface.minecraft.first-night-world",
                title="第一夜世界态与生存 HUD",
                kind="world",
                description="开局信息由世界画面、准星、生命/饥饿 HUD 和快捷栏共同表达；材料入口位于世界空间而不是菜单任务列表。",
                source_ids=[runtime_source],
                artifact_ids=[world.id],
                run_id=baseline_run_id,
                elements=[
                    _element("ui.minecraft.world.viewport", "viewport", "世界空间", _rect(0.0, 0.0, 1.0, 0.80), source_id=runtime_source, artifact_id=world.id),
                    _element("ui.minecraft.world.crosshair", "target", "准星与目标方块", _rect(0.485, 0.46, 0.03, 0.08), source_id=runtime_source, artifact_id=world.id, actions=["look", "attack", "use"]),
                    _element("ui.minecraft.world.health", "status", "生命值", _rect(0.28, 0.79, 0.20, 0.06), source_id=runtime_source, artifact_id=world.id),
                    _element("ui.minecraft.world.hunger", "status", "饥饿值", _rect(0.56, 0.79, 0.20, 0.06), source_id=runtime_source, artifact_id=world.id),
                    _element("ui.minecraft.world.hotbar", "list", "九格快捷栏", _rect(0.28, 0.85, 0.44, 0.14), source_id=runtime_source, artifact_id=world.id, actions=["select", "use", "drop"]),
                ],
            ),
            Surface(
                id="surface.minecraft.campfire-ignition",
                title="世界内堆枝与概率生火",
                kind="world",
                description="三根枯枝先形成世界内满堆；玩家手持石子反复敲击，成功后原地替换为点燃篝火并同步触发火焰、烟和知识提示。",
                source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.jade-guidance"],
                artifact_ids=[fire.id],
                run_id=e2e_run_id,
                elements=[
                    _element("ui.minecraft.fire.campfire", "world-object", "点燃的篝火", _rect(0.38, 0.31, 0.25, 0.38), source_id=runtime_source, artifact_id=fire.id, actions=["attack", "use"]),
                    _element("ui.minecraft.fire.jade", "context-help", "目标与手持物提示", _rect(0.34, 0.02, 0.34, 0.13), source_id="src.minecraft.jade-guidance", artifact_id=fire.id),
                    _element("ui.minecraft.fire.handbook-toast", "notification", "火与熟食知识提示", _rect(0.68, 0.02, 0.31, 0.18), source_id="src.minecraft.handbook-fire-food", artifact_id=fire.id),
                    _element("ui.minecraft.fire.hotbar", "list", "石子与材料快捷栏", _rect(0.28, 0.85, 0.44, 0.14), source_id=runtime_source, artifact_id=fire.id, actions=["select"]),
                ],
            ),
            Surface(
                id="surface.minecraft.campfire-cooking",
                title="营火烤位与熟食产物",
                kind="world",
                description="生食经右键放入营火烤位，600 tick 后转换为新的熟食物品；画面状态与物品 ID 同时承担完成反馈。",
                source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.food-profiles"],
                artifact_ids=[food.id],
                run_id=e2e_run_id,
                elements=[
                    _element("ui.minecraft.cooking.campfire", "facility", "四槽营火烤位", _rect(0.37, 0.29, 0.27, 0.41), source_id="src.minecraft.handcraft", artifact_id=food.id, actions=["use", "wait"]),
                    _element("ui.minecraft.cooking.food-on-fire", "progress", "烤位中的生食/熟食", _rect(0.43, 0.35, 0.15, 0.20), source_id=runtime_source, artifact_id=food.id),
                    _element("ui.minecraft.cooking.item-name", "status", "Roasted Berry 产物名", _rect(0.39, 0.73, 0.25, 0.08), source_id=runtime_source, artifact_id=food.id),
                    _element("ui.minecraft.cooking.hotbar", "list", "熟食进入快捷栏", _rect(0.28, 0.85, 0.44, 0.14), source_id=runtime_source, artifact_id=food.id, actions=["select", "eat"]),
                ],
            ),
            Surface(
                id="surface.minecraft.recipe-zeroed",
                title="原版配方入口的禁用态",
                kind="page",
                description="工作台界面真实可打开，但服务端只保留 ProtoWorld 配方；石镐配方不存在，迫使第一夜加工转向世界内手作。",
                source_ids=[runtime_source, recipe_source],
                artifact_ids=[recipe.id],
                run_id=baseline_run_id,
                elements=[
                    _element("ui.minecraft.recipe.book", "panel", "仅含 ProtoWorld 配方的配方书", _rect(0.05, 0.16, 0.42, 0.68), source_id=recipe_source, artifact_id=recipe.id, actions=["filter", "select"]),
                    _element("ui.minecraft.recipe.grid", "grid", "3×3 原版工作台格", _rect(0.54, 0.19, 0.15, 0.29), source_id=runtime_source, artifact_id=recipe.id, actions=["place"]),
                    _element("ui.minecraft.recipe.output", "status", "空输出槽", _rect(0.75, 0.23, 0.08, 0.16), source_id=recipe_source, artifact_id=recipe.id),
                    _element("ui.minecraft.recipe.inventory", "grid", "物品栏与材料", _rect(0.49, 0.49, 0.38, 0.31), source_id=runtime_source, artifact_id=recipe.id, actions=["move", "select"]),
                ],
            ),
        ]

    @staticmethod
    def _layout(surface: Surface, wireframe_id: str) -> LayoutSpec:
        return LayoutSpec(
            id=f"layout.{surface.id}",
            surface_id=surface.id,
            canvas_aspect_ratio="16:9",
            safe_area=_rect(0.015, 0.025, 0.97, 0.95),
            elements=[
                LayoutElementSpec(
                    id=f"layout-element.{element.id}",
                    ui_element_id=element.id,
                    bounds=element.bounds or _rect(0.1, 0.1, 0.8, 0.1),
                    anchors=["viewport"],
                    z_index=index + 1,
                    responsive_behavior="Minecraft GUI scale changes pixels but preserves semantic region.",
                )
                for index, element in enumerate(surface.elements)
            ],
            constraints=[
                "世界态元素以视口为坐标系；HUD 始终贴底。",
                "工作台格与输出槽保持固定网格关系，不随世界相机变化。",
            ],
            source_ids=surface.source_ids,
            artifact_ids=[wireframe_id, *surface.artifact_ids],
            run_id=surface.run_id,
        )

    def build(self, manifest: MinecraftFirstNightEvidenceManifest) -> GameReport:
        gates_path, screenshot_paths = self._input_paths(manifest)
        gates_payload = json.loads(gates_path.read_text(encoding="utf-8"))
        gates_by_id = {item["id"]: item for item in gates_payload.get("gates", [])}
        source_evidence = self.source_evidence(manifest)
        if not source_evidence["ok"]:
            raise ValueError("Minecraft source oracle is incomplete")

        e2e_digest = hashlib.sha256(gates_path.read_bytes()).hexdigest()[:12]
        baseline_digest = hashlib.sha256(
            (manifest.world_snapshot + manifest.recipe_probe_response).encode("utf-8")
        ).hexdigest()[:12]
        e2e_run_id = f"run.minecraft.first-night-e2e.{e2e_digest}"
        baseline_run_id = f"run.minecraft.recipe-probe.{baseline_digest}"
        screens = {
            role: self._public_screenshot(
                role,
                path,
                manifest,
                baseline_run_id if role in {"world_start", "recipe_zeroed"} else e2e_run_id,
            )
            for role, path in screenshot_paths.items()
        }

        sources = self._sources(source_evidence, manifest.captured_at)
        source_ids = [item.id for item in sources]
        runtime_source = "src.minecraft.first-night-e2e"
        surfaces = self._surfaces(
            screens,
            runtime_source,
            "src.minecraft.recipe-zeroing",
            baseline_run_id,
            e2e_run_id,
        )

        wireframes: list[ArtifactRef] = []
        design_artifacts: list[DesignArtifactSpec] = []
        for surface in surfaces:
            role = next(
                role for role, artifact in screens.items() if artifact.id in surface.artifact_ids
            )
            artifact = self._svg_artifact(
                f"art.minecraft.design.wireframe.{role}",
                "wireframe",
                self._wireframe_svg(surface),
                derived_from=surface.artifact_ids,
            )
            wireframes.append(artifact)
            design_artifacts.append(
                DesignArtifactSpec(
                    id=f"design-artifact.minecraft.wireframe.{role}",
                    title=f"{surface.title}反推线框图",
                    kind="wireframe",
                    artifact_id=artifact.id,
                    surface_ids=[surface.id],
                    derived_from_artifact_ids=surface.artifact_ids,
                    generation_method="hybrid",
                    source_ids=surface.source_ids,
                    run_id=self.DESIGN_RUN_ID,
                    review_status="reviewed",
                )
            )

        flow = [
            FlowNode(
                id="flow.minecraft.forage",
                title="在世界中取得枯枝、石子与生食",
                description="固定世界从空物品栏开始，散落物和可采集食材是第一批资源入口。",
                action="look, move, use or attack world objects",
                state_before="白天、空物品栏、未生火",
                state_after="物品栏持有枯枝、石子和生食",
                source_ids=[runtime_source, "src.minecraft.handcraft"],
                artifact_ids=[screens["world_start"].id],
                surface_ids=["surface.minecraft.first-night-world"],
                run_id=e2e_run_id,
                next=["flow.minecraft.stack-twigs"],
            ),
            FlowNode(
                id="flow.minecraft.stack-twigs",
                title="把三根枯枝堆成世界对象",
                description="枯枝逐次右键地面，堆叠状态从 1 增长到 3；未满堆不能进入点火判定。",
                action="use dry twig on the same ground position three times",
                state_before="枯枝在物品栏",
                state_after="world.twig_pile.size = 3",
                source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.jade-guidance"],
                artifact_ids=[screens["campfire"].id],
                surface_ids=["surface.minecraft.campfire-ignition"],
                run_id=e2e_run_id,
                next=["flow.minecraft.ignite"],
            ),
            FlowNode(
                id="flow.minecraft.ignite",
                title="用石子反复敲击直到生火",
                description="每次命中先给火星反馈，再按晴天 40%/雨天 20% 判断是否替换为点燃篝火。",
                action="attack full twig pile while holding stone pebble",
                state_before="满枯枝堆 + 石子",
                state_after="lit campfire",
                source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.handbook-fire-food"],
                artifact_ids=[screens["campfire"].id],
                surface_ids=["surface.minecraft.campfire-ignition"],
                run_id=e2e_run_id,
                next=["flow.minecraft.roast"],
            ),
            FlowNode(
                id="flow.minecraft.roast",
                title="把生食放上营火等待 600 tick",
                description="右键把生食送入营火烤位；计时完成后从生食物品 ID 转换为对应熟食。",
                action="use raw food on lit campfire, then wait",
                state_before="lit campfire + raw food",
                state_after="roasted food item",
                source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.food-profiles"],
                artifact_ids=[screens["roasted_food"].id],
                surface_ids=["surface.minecraft.campfire-cooking"],
                run_id=e2e_run_id,
                next=["flow.minecraft.eat"],
            ),
            FlowNode(
                id="flow.minecraft.eat",
                title="进食，把熟食写入饥饿和三餐状态",
                description="熟食增加饥饿/顶饿，并按果、菌、肉、谷类别记录进当前三餐；三类齐全会提高回血效率。",
                action="hold use to eat roasted food",
                state_before="饥饿 + 熟食",
                state_after="food level, saturation and meal classes updated",
                source_ids=[runtime_source, "src.minecraft.body-system", "src.minecraft.food-profiles"],
                artifact_ids=[screens["roasted_food"].id],
                surface_ids=["surface.minecraft.campfire-cooking"],
                run_id=e2e_run_id,
                next=["flow.minecraft.night-fire"],
            ),
            FlowNode(
                id="flow.minecraft.night-fire",
                title="在夜间把篝火同时作为加工站和安全节点",
                description="篝火光照使遗民进入灼烧状态，并推动夜与火光手记；同一设施兼顾熟食、照明和威胁控制。",
                action="remain within the illuminated campfire area",
                state_before="night threat near player",
                state_after="threat burned in block light >= 8",
                source_ids=[runtime_source, "src.minecraft.body-system", "src.minecraft.handbook-fire-food"],
                artifact_ids=[screens["night_burning"].id, screens["night_light"].id],
                surface_ids=["surface.minecraft.campfire-ignition"],
                run_id=e2e_run_id,
                next=[],
            ),
        ]

        wireflow = self._svg_artifact(
            "art.minecraft.design.first-night-wireflow",
            "wireflow",
            self._wireflow_svg(surfaces),
            derived_from=[item.id for item in screens.values()],
        )
        design_artifacts.append(
            DesignArtifactSpec(
                id="design-artifact.minecraft.first-night-wireflow",
                title="世界手作、生火、烤制与禁用配方分支 Wireflow",
                kind="wireflow",
                artifact_id=wireflow.id,
                surface_ids=[item.id for item in surfaces],
                flow_node_ids=[item.id for item in flow],
                derived_from_artifact_ids=[item.id for item in screens.values()],
                generation_method="hybrid",
                source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.recipe-zeroing"],
                run_id=self.DESIGN_RUN_ID,
                review_status="reviewed",
            )
        )

        layout_specs = [
            self._layout(surface, wireframes[index].id)
            for index, surface in enumerate(surfaces)
        ]
        mechanisms = [
            MechanismSpec(
                id="mechanism.minecraft.world-handcraft-gate",
                title="世界手作替代原版配方捷径",
                description="本实例在服务端重载时只保留 ProtoWorld 配方；营火必须通过世界内堆枝与敲击生成。",
                representation="rule",
                code="recipes = keep(namespace == 'protoworld'); vanilla stone_pickaxe recipe = absent",
                source_ids=["src.minecraft.recipe-zeroing", "src.minecraft.handcraft"],
                artifact_ids=[screens["recipe_zeroed"].id, screens["campfire"].id],
                run_id=baseline_run_id,
            ),
            MechanismSpec(
                id="mechanism.minecraft.ignition",
                title="生火是有反馈的概率努力",
                description="三根枯枝满堆是硬门；每次石子敲击先显示火星，晴天 40% 成功，雨天 20% 成功。",
                representation="pseudocode",
                code=(
                    "if pile.size == 3 and held == STONE_PEBBLE:\n"
                    "    show_sparks()\n"
                    "    chance = 0.20 if raining else 0.40\n"
                    "    if random() < chance: replace(pile, LIT_CAMPFIRE)"
                ),
                source_ids=["src.minecraft.handcraft"],
                artifact_ids=[screens["campfire"].id],
                run_id=e2e_run_id,
            ),
            MechanismSpec(
                id="mechanism.minecraft.food-conversion",
                title="营火按物品身份执行生熟转换",
                description="食物占用营火烤位 600 tick 后变成对应熟食；完成态由物品 ID 和数量判定。",
                representation="state_machine",
                code="raw_food + lit_campfire --600 ticks--> roasted_food",
                source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.food-profiles"],
                artifact_ids=[screens["roasted_food"].id],
                run_id=e2e_run_id,
            ),
            MechanismSpec(
                id="mechanism.minecraft.meal-regeneration",
                title="熟食与饮食多样性共同驱动恢复",
                description="熟食提高饥饿与顶饿；当前三餐覆盖更多元素谱时，受伤后的恢复耗时显著缩短。",
                representation="formula",
                code="regen_rate = base_rate * meal_variety_multiplier; healing consumes saturation",
                source_ids=[runtime_source, "src.minecraft.body-system", "src.minecraft.food-profiles"],
                artifact_ids=[screens["roasted_food"].id],
                run_id=e2e_run_id,
            ),
            MechanismSpec(
                id="mechanism.minecraft.fire-safety",
                title="火光把夜间威胁变成可控空间",
                description="遗民处在方块光照不低于 8 的区域会被灼烧；篝火因此兼具资源加工与空间防御价值。",
                representation="rule",
                code="if remnant.block_light >= 8: remnant.set_on_fire()",
                source_ids=[runtime_source, "src.minecraft.body-system"],
                artifact_ids=[screens["night_burning"].id, screens["night_light"].id],
                run_id=e2e_run_id,
            ),
        ]

        resources = [
            ResourceRelation(
                id="relation.minecraft.twigs-to-pile",
                resource="枯枝 ×3 → 满枯枝堆",
                role="conversion",
                description="背包材料转成带 size 状态的世界对象。",
                source_ids=["src.minecraft.handcraft"],
                artifact_ids=[screens["campfire"].id],
                run_id=e2e_run_id,
                from_resource_id="resource.minecraft.dry-twig",
                to_resource_id="resource.minecraft.twig-pile",
            ),
            ResourceRelation(
                id="relation.minecraft.pile-to-fire",
                resource="满枯枝堆 + 石子敲击 → 点燃篝火",
                role="conversion",
                description="石子不被消耗，但充当点火工具；概率失败只消耗时间。",
                source_ids=["src.minecraft.handcraft"],
                artifact_ids=[screens["campfire"].id],
                run_id=e2e_run_id,
                from_resource_id="resource.minecraft.twig-pile",
                to_resource_id="resource.minecraft.campfire",
            ),
            ResourceRelation(
                id="relation.minecraft.raw-to-roasted",
                resource="生食 + 600 tick 营火烤位 → 熟食",
                role="conversion",
                description="同一食物类别保留元素谱，但饥饿与顶饿价值提高。",
                source_ids=["src.minecraft.handcraft", "src.minecraft.food-profiles"],
                artifact_ids=[screens["roasted_food"].id],
                run_id=e2e_run_id,
                from_resource_id="resource.minecraft.raw-food",
                to_resource_id="resource.minecraft.roasted-food",
            ),
            ResourceRelation(
                id="relation.minecraft.fire-shared-facility",
                resource="点燃篝火",
                role="gate",
                description="同一设施开放烤制、烧陶、煮羹、照明、火把引燃和夜间威胁控制。",
                source_ids=["src.minecraft.handcraft", "src.minecraft.body-system"],
                artifact_ids=[screens["campfire"].id, screens["night_light"].id],
                run_id=e2e_run_id,
                from_resource_id="resource.minecraft.campfire",
                to_resource_id="resource.minecraft.first-night-safety",
            ),
        ]
        resource_model = ResourceModel(
            id="resource-model.minecraft.first-night-food",
            title="第一夜生火、熟食与安全资源模型",
            resources=[
                ResourceDefinition(id="resource.minecraft.dry-twig", title="枯枝", kind="material", unit="根", source_ids=["src.minecraft.handcraft"]),
                ResourceDefinition(id="resource.minecraft.stone-pebble", title="石子", kind="item", unit="枚", source_ids=["src.minecraft.handcraft"]),
                ResourceDefinition(id="resource.minecraft.twig-pile", title="满枯枝堆", kind="facility", source_ids=["src.minecraft.handcraft"], artifact_ids=[screens["campfire"].id], run_id=e2e_run_id),
                ResourceDefinition(id="resource.minecraft.campfire", title="点燃篝火", kind="facility", source_ids=[runtime_source, "src.minecraft.handcraft"], artifact_ids=[screens["campfire"].id], run_id=e2e_run_id),
                ResourceDefinition(id="resource.minecraft.raw-food", title="生食", kind="item", source_ids=["src.minecraft.food-profiles"]),
                ResourceDefinition(id="resource.minecraft.roasted-food", title="熟食", kind="item", source_ids=[runtime_source, "src.minecraft.food-profiles"], artifact_ids=[screens["roasted_food"].id], run_id=e2e_run_id),
                ResourceDefinition(id="resource.minecraft.first-night-safety", title="火光安全区", kind="other", source_ids=[runtime_source, "src.minecraft.body-system"], artifact_ids=[screens["night_light"].id], run_id=e2e_run_id),
            ],
            relation_ids=[item.id for item in resources],
            source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.body-system", "src.minecraft.food-profiles"],
        )

        overview = DesignStatement(
            id="statement.minecraft.overview",
            title="系统概述",
            statement="第一夜把散落物采集、世界内手作、概率生火、营火烤制、饥饿恢复和夜间安全压在同一条可见因果链上；原版网格配方被主动清零。",
            kind=ContentKind.analyst_interpretation,
            source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.recipe-zeroing", "src.minecraft.body-system"],
            artifact_ids=[screens["campfire"].id, screens["recipe_zeroed"].id, screens["night_light"].id],
            run_id=e2e_run_id,
        )
        player_goal = DesignStatement(
            id="statement.minecraft.player-goal",
            title="玩家目标",
            statement="在第一夜到来前从空手状态建立一处点燃的篝火、把易得生食转为更顶饿的熟食，并利用火光降低夜间遗民威胁。",
            kind=ContentKind.analyst_interpretation,
            source_ids=[runtime_source, "src.minecraft.handbook-fire-food"],
            artifact_ids=[screens["campfire"].id, screens["roasted_food"].id, screens["night_light"].id],
            run_id=e2e_run_id,
        )
        entry = DesignStatement(
            id="statement.minecraft.entry",
            title="入口与解锁",
            statement="玩家以空物品栏、白天和生存 HUD 进入固定岛世界；散石、枯枝、食材与情境提示开放手作链，工作台的原版石镐捷径在本实例不可用。",
            kind=ContentKind.direct_observation,
            source_ids=[runtime_source, "src.minecraft.recipe-zeroing"],
            artifact_ids=[screens["world_start"].id, screens["recipe_zeroed"].id],
            run_id=baseline_run_id,
        )
        version_note = DesignStatement(
            id="statement.minecraft.version",
            title="版本与实例边界",
            statement=f"仅适用于 {manifest.game_version}、本地服务器 {manifest.server_address} 与固定世界快照 {manifest.world_snapshot}；不是对原版 Minecraft 石镐流程的描述。",
            kind=ContentKind.direct_observation,
            source_ids=[runtime_source, "src.minecraft.recipe-zeroing"],
            artifact_ids=[screens["recipe_zeroed"].id],
            run_id=baseline_run_id,
        )
        core_loop = CoreLoopSpec(
            id="core-loop.minecraft.first-night-fire-food",
            title="觅食—生火—烤制—进食—过夜",
            player_goal=player_goal.statement,
            entry_conditions=["生存模式", "白天", "固定世界", "初始空物品栏"],
            exit_conditions=["取得至少一份熟食", "点燃篝火", "夜间火光安全反馈可验证"],
            cadence="首夜约 20 分钟；单次烤制 600 tick；生火尝试次数由概率决定。",
            steps=[
                CoreLoopStep(id="core-step.minecraft.forage", title="搜集", player_action="在世界中观察并拾取散落物与生食", system_response="物品进入背包，世界对象消失", state_before="空物品栏", state_after="枯枝、石子与生食", flow_node_ids=["flow.minecraft.forage"], source_ids=[runtime_source], artifact_ids=[screens["world_start"].id], run_id=e2e_run_id),
                CoreLoopStep(id="core-step.minecraft.fire", title="建立火源", player_action="堆三根枯枝并用石子反复敲击", system_response="火星反馈；成功时生成点燃篝火", state_before="材料态", state_after="点燃设施态", flow_node_ids=["flow.minecraft.stack-twigs", "flow.minecraft.ignite"], source_ids=[runtime_source, "src.minecraft.handcraft"], artifact_ids=[screens["campfire"].id], run_id=e2e_run_id),
                CoreLoopStep(id="core-step.minecraft.cook", title="加工食物", player_action="把生食放上营火并等待", system_response="600 tick 后输出熟食 ID", state_before="生食", state_after="熟食", flow_node_ids=["flow.minecraft.roast"], source_ids=[runtime_source, "src.minecraft.food-profiles"], artifact_ids=[screens["roasted_food"].id], run_id=e2e_run_id),
                CoreLoopStep(id="core-step.minecraft.eat", title="进食恢复", player_action="吃下熟食并组合不同元素谱", system_response="更新饥饿、顶饿、三餐和回血速率", state_before="饥饿/受伤", state_after="恢复与三餐记录", flow_node_ids=["flow.minecraft.eat"], source_ids=[runtime_source, "src.minecraft.body-system"], artifact_ids=[screens["roasted_food"].id], run_id=e2e_run_id),
                CoreLoopStep(id="core-step.minecraft.survive", title="利用火光过夜", player_action="围绕篝火组织夜间活动", system_response="光照区内遗民灼烧，手记分段确证", state_before="夜间威胁", state_after="可控安全区", flow_node_ids=["flow.minecraft.night-fire"], source_ids=[runtime_source, "src.minecraft.body-system"], artifact_ids=[screens["night_burning"].id, screens["night_light"].id], run_id=e2e_run_id),
            ],
        )
        architecture = InformationArchitectureSpec(
            id="ia.minecraft.first-night-fire-food",
            root_surface_ids=["surface.minecraft.first-night-world"],
            surface_ids=[item.id for item in surfaces],
            edges=[
                NavigationEdge(id="nav.minecraft.world-to-fire", from_surface_id="surface.minecraft.first-night-world", to_surface_id="surface.minecraft.campfire-ignition", trigger="把三根枯枝堆满并用石子点火", flow_node_ids=["flow.minecraft.stack-twigs", "flow.minecraft.ignite"]),
                NavigationEdge(id="nav.minecraft.fire-to-cooking", from_surface_id="surface.minecraft.campfire-ignition", to_surface_id="surface.minecraft.campfire-cooking", trigger="手持生食右键点燃篝火", flow_node_ids=["flow.minecraft.roast", "flow.minecraft.eat"]),
                NavigationEdge(id="nav.minecraft.world-to-disabled-recipe", from_surface_id="surface.minecraft.first-night-world", to_surface_id="surface.minecraft.recipe-zeroed", trigger="打开工作台与配方书", condition="界面可达，但原版配方被清零"),
            ],
            notes=["世界视口是主信息架构；工作台是可达但被约束的旁支。", "核心状态通过 HUD、世界对象、物品栏与手记跨层表达。"],
        )
        interaction = InteractionSpec(
            id="interaction.minecraft.first-night-fire-food",
            title="从空手到熟食与火光安全区",
            trigger="新玩家进入固定世界",
            preconditions=["世界快照可复位", "玩家物品栏清空", "服务器处于白天"],
            steps=[
                InteractionStep(id="interaction-step.minecraft.1", order=1, actor="player", action="观察世界、HUD 和快捷栏", response="确认空手与生存状态", surface_id="surface.minecraft.first-night-world", ui_element_id="ui.minecraft.world.viewport", flow_node_id="flow.minecraft.forage", source_ids=[runtime_source], artifact_ids=[screens["world_start"].id]),
                InteractionStep(id="interaction-step.minecraft.2", order=2, actor="player", action="右键拾取散石并取得三根枯枝", response="材料进入物品栏", surface_id="surface.minecraft.first-night-world", ui_element_id="ui.minecraft.world.crosshair", flow_node_id="flow.minecraft.forage", source_ids=[runtime_source], artifact_ids=[screens["world_start"].id]),
                InteractionStep(id="interaction-step.minecraft.3", order=3, actor="player", action="对同一地面逐次使用枯枝", response="枯枝堆 size 依次达到 1、2、3", surface_id="surface.minecraft.campfire-ignition", ui_element_id="ui.minecraft.fire.campfire", flow_node_id="flow.minecraft.stack-twigs", source_ids=[runtime_source, "src.minecraft.handcraft"], artifact_ids=[screens["campfire"].id]),
                InteractionStep(id="interaction-step.minecraft.4", order=4, actor="player", action="手持石子敲击满枯枝堆", response="失败给火星；成功生成点燃篝火", surface_id="surface.minecraft.campfire-ignition", ui_element_id="ui.minecraft.fire.campfire", flow_node_id="flow.minecraft.ignite", source_ids=[runtime_source, "src.minecraft.handcraft"], artifact_ids=[screens["campfire"].id]),
                InteractionStep(id="interaction-step.minecraft.5", order=5, actor="player", action="把生食放入营火烤位并等待", response="600 tick 后输出熟食", surface_id="surface.minecraft.campfire-cooking", ui_element_id="ui.minecraft.cooking.food-on-fire", flow_node_id="flow.minecraft.roast", source_ids=[runtime_source, "src.minecraft.food-profiles"], artifact_ids=[screens["roasted_food"].id]),
                InteractionStep(id="interaction-step.minecraft.6", order=6, actor="system", action="根据熟食和三餐类别更新身体状态", response="饥饿恢复，饮食多样性提高回血效率", surface_id="surface.minecraft.campfire-cooking", ui_element_id="ui.minecraft.cooking.item-name", flow_node_id="flow.minecraft.eat", source_ids=[runtime_source, "src.minecraft.body-system"], artifact_ids=[screens["roasted_food"].id]),
                InteractionStep(id="interaction-step.minecraft.7", order=7, actor="system", action="夜间检测篝火光照范围内的遗民", response="光照不低于 8 时使其灼烧并记录火光知识", surface_id="surface.minecraft.campfire-ignition", ui_element_id="ui.minecraft.fire.campfire", flow_node_id="flow.minecraft.night-fire", source_ids=[runtime_source, "src.minecraft.body-system"], artifact_ids=[screens["night_burning"].id, screens["night_light"].id]),
            ],
            postconditions=["至少一份熟食进入物品栏", "点燃篝火存在", "G1-G24 客观门均通过"],
            branches=["雨天点火概率减半", "连续失败时保留材料并继续尝试", "工作台原版配方分支确定性不可用"],
            failure_recovery_ids=["failure.minecraft.ignition-rng", "failure.minecraft.recipe-zeroed", "failure.minecraft.night-death"],
            diagram_artifact_id=wireflow.id,
            source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.body-system"],
            artifact_ids=[wireflow.id, *[item.id for item in screens.values()]],
            run_id=e2e_run_id,
        )

        state_matrix = StateMatrix(
            id="state-matrix.minecraft.ignition",
            title="枯枝堆与点火状态矩阵",
            subject_id="mechanism.minecraft.ignition",
            dimensions=["twig_count", "held_item", "weather", "rng_result", "campfire_lit"],
            cases=[
                StateCase(id="state.minecraft.no-pile", state="无枯枝堆", condition="twig_count = 0", visible=False, enabled=False, content="不能点火", feedback=["无点火提示"], next_state="枯枝堆 1-2 根", source_ids=["src.minecraft.handcraft", "src.minecraft.jade-guidance"]),
                StateCase(id="state.minecraft.partial-pile", state="枯枝堆未满", condition="twig_count in {1, 2}", visible=True, enabled=False, content="Jade 显示还差几根", feedback=["堆叠外观", "情境文字"], next_state="满三根", source_ids=["src.minecraft.handcraft", "src.minecraft.jade-guidance"]),
                StateCase(id="state.minecraft.full-wrong-tool", state="满堆但未持石子", condition="twig_count = 3 and held != stone_pebble", visible=True, enabled=False, content="提示需要石子", feedback=["目标提示"], next_state="持石子", source_ids=["src.minecraft.handcraft", "src.minecraft.jade-guidance"]),
                StateCase(id="state.minecraft.ignite-fail", state="点火失败", condition="full pile and random >= chance", visible=True, enabled=True, content="枯枝堆保留", feedback=["火星", "仍可继续敲击"], next_state="再次尝试", source_ids=["src.minecraft.handcraft"], artifact_ids=[screens["campfire"].id]),
                StateCase(id="state.minecraft.ignite-success", state="点火成功", condition="full pile and random < chance", visible=True, enabled=True, content="替换为点燃篝火", feedback=["火焰", "烟", "光照", "手记提示"], next_state="烤制/照明", source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.handbook-fire-food"], artifact_ids=[screens["campfire"].id]),
            ],
        )
        progression = ProgressionSpec(
            id="progression.minecraft.food-ladder",
            title="从野食到餐食的第一夜文明台阶",
            axes=[
                ProgressionAxis(id="axis.minecraft.food-processing", name="食物加工层级", unit="tier", stages=["野食/生食", "熟食", "三类羹/餐食"], gates=["点燃篝火", "烧成陶碗", "投入三类生食"], resets=["世界重建会复位玩家与设施状态"]),
                ProgressionAxis(id="axis.minecraft.fire-utility", name="火的复用价值", unit="capability", stages=["火星反馈", "烤制与照明", "烧陶", "煮羹", "夜间安全区"], gates=["满枯枝堆", "点火成功", "对应材料"], resets=[]),
            ],
            pacing=["生火成功次数受 40% 概率影响。", "烤食和烧陶均为 600 tick，煮羹为 800 tick。", "多类饮食的收益在受伤恢复时被感知。"],
            cross_system_effects=["饥饿与顶饿", "生命恢复", "手记知识", "夜间威胁", "照明与路径"],
            source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.body-system", "src.minecraft.food-profiles", "src.minecraft.handbook-fire-food"],
            artifact_ids=[screens["campfire"].id, screens["roasted_food"].id, screens["night_light"].id],
            run_id=e2e_run_id,
        )
        balance = BalanceSpec(
            id="balance.minecraft.hunger-food",
            title="点火、加工与恢复的关键参数",
            target_experience="让火成为第一夜的多用途枢纽：有少量失败摩擦，但每次行动可读；熟食与饮食多样性必须比单一生食更值得。",
            parameters=[
                BalanceParameter(id="balance-param.minecraft.ignite-clear", name="晴天单次点火成功率", value_or_range="40%", unit="per hit", tuning_role="控制从材料齐备到火源建立的尝试次数", constraints=["失败仍显示火星", "最多可重复尝试"], source_ids=["src.minecraft.handcraft"]),
                BalanceParameter(id="balance-param.minecraft.ignite-rain", name="雨天单次点火成功率", value_or_range="20%", unit="per hit", tuning_role="让天气降低生火效率但不封死路径", constraints=["为晴天的一半"], source_ids=["src.minecraft.handcraft"]),
                BalanceParameter(id="balance-param.minecraft.roast-time", name="烤制时间", value_or_range="600", unit="tick", tuning_role="形成等待窗口并允许并行做其他手作", source_ids=[runtime_source, "src.minecraft.handcraft"]),
                BalanceParameter(id="balance-param.minecraft.meal-variety", name="三类饮食恢复倍率实测", value_or_range=gates_by_id.get("G7", {}).get("evidence", "single vs three-class E2E"), unit="E2E evidence", tuning_role="奖励食物多样性并使三类羹有明确价值", constraints=["三类恢复耗时应小于单类/1.3"], source_ids=[runtime_source, "src.minecraft.body-system"]),
                BalanceParameter(id="balance-param.minecraft.fire-light", name="遗民灼烧光照门槛", value_or_range=">= 8", unit="block light", tuning_role="把篝火周边塑造成可控夜间空间", source_ids=[runtime_source, "src.minecraft.body-system"]),
            ],
            mechanism_ids=[item.id for item in mechanisms],
            notes=["本报告只记录外部可见/E2E 与源码 oracle 均支持的参数；未使用运营数据。"],
        )
        feedback = FeedbackSpec(
            id="feedback.minecraft.campfire",
            title="生火、烤制和夜间安全的多通道反馈",
            trigger="堆枝、点火尝试、食物熟成或夜间敌人进入火光",
            channels=["visual", "animation", "audio", "text", "numeric"],
            timing="点火火星即时；点燃后火焰/烟/光照持续；烤制 600 tick 后产物 ID 与名称改变；夜间状态按周期检测。",
            success_behavior="篝火替换世界对象并发光，熟食进入物品栏，手记/情境提示更新，光照区内遗民燃烧。",
            failure_behavior="点火失败仍显示火星且保留材料；材料或状态不满足时情境提示说明下一步；原版配方输出保持空。",
            surface_ids=[item.id for item in surfaces],
            ui_element_ids=["ui.minecraft.fire.campfire", "ui.minecraft.fire.jade", "ui.minecraft.fire.handbook-toast", "ui.minecraft.cooking.item-name", "ui.minecraft.recipe.output"],
            source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.jade-guidance", "src.minecraft.handbook-fire-food"],
            artifact_ids=[screens["campfire"].id, screens["roasted_food"].id, screens["night_light"].id, screens["recipe_zeroed"].id],
            run_id=e2e_run_id,
        )
        tutorial = TutorialSpec(
            id="tutorial.minecraft.contextual-first-night",
            title="由准星目标与手持物驱动的情境教学",
            steps=[
                TutorialStep(id="tutorial-step.minecraft.pile", trigger="准星指向可放置地面且手持枯枝", instruction="逐根把枯枝堆在同一处，提示显示还缺几根", allowed_actions=["look", "use", "select hotbar"], completion_condition="twig_pile.size == 3", recovery="重新拾取枯枝，已有堆叠保留", flow_node_ids=["flow.minecraft.stack-twigs"]),
                TutorialStep(id="tutorial-step.minecraft.ignite", trigger="准星指向满枯枝堆且手持石子", instruction="继续敲击；火星表示输入有效，直到点燃", allowed_actions=["look", "attack", "select hotbar"], completion_condition="block == lit_campfire", recovery="概率失败后原地继续敲击", flow_node_ids=["flow.minecraft.ignite"]),
                TutorialStep(id="tutorial-step.minecraft.cook", trigger="准星指向点燃篝火且手持可食物", instruction="把食材放上烤位，等待熟成后取回", allowed_actions=["use", "wait", "move"], completion_condition="inventory contains roasted food", recovery="烤位保留进度；回到篝火附近取回", flow_node_ids=["flow.minecraft.roast", "flow.minecraft.eat"]),
            ],
            skippable=True,
            repeat_behavior="Jade 提示随准星和手持物重复出现；手记条目保留已确证阶段。",
            source_ids=[runtime_source, "src.minecraft.jade-guidance", "src.minecraft.handbook-fire-food"],
            artifact_ids=[screens["campfire"].id, screens["roasted_food"].id],
            run_id=e2e_run_id,
        )
        failures = [
            FailureRecoverySpec(id="failure.minecraft.ignition-rng", title="点火概率失败", failure_condition="满足满堆与石子条件但本次随机数未命中", visible_behavior="出现火星但枯枝堆未变成篝火", retained_state="满枯枝堆与石子均保留", recovery_action="继续敲击；雨天可等待天气改善", flow_node_ids=["flow.minecraft.ignite"], source_ids=[runtime_source, "src.minecraft.handcraft"], artifact_ids=[screens["campfire"].id], run_id=e2e_run_id),
            FailureRecoverySpec(id="failure.minecraft.recipe-zeroed", title="原版工作台配方不可用", failure_condition="玩家尝试按原版石镐路径使用工作台", visible_behavior="配方书没有石镐，3×3 输出槽为空；命令探针返回 Unknown recipe", retained_state="物品栏材料不变", recovery_action="回到世界手作路径，寻找散落物并建立篝火", flow_node_ids=["flow.minecraft.forage", "flow.minecraft.stack-twigs"], source_ids=["src.minecraft.recipe-zeroing", runtime_source], artifact_ids=[screens["recipe_zeroed"].id], run_id=baseline_run_id),
            FailureRecoverySpec(id="failure.minecraft.night-death", title="夜间死亡与复活", failure_condition="遗民在玩家建立安全区前击杀玩家", visible_behavior="死亡屏显示死因；E2E 记录死亡并复活", retained_state="世界设施保留；玩家背包按世界规则处理", recovery_action="点击 Respawn，恢复测试场并重走火光路径", irreversible_effects=["死亡前未保存的行动时间"], flow_node_ids=["flow.minecraft.night-fire"], source_ids=[runtime_source, "src.minecraft.body-system"], artifact_ids=[screens["night_threat"].id, screens["night_burning"].id], run_id=e2e_run_id),
        ]
        dependencies = [
            DependencySpec(id="dependency.minecraft.recipe-zeroing", title="配方清零策略", direction="upstream", target_system_id="protoworld-recipe-zeroing", dependency="只有 protoworld 命名空间配方被保留，决定世界手作是主路径而非原版石镐合成。", source_ids=["src.minecraft.recipe-zeroing"], artifact_ids=[screens["recipe_zeroed"].id], run_id=baseline_run_id),
            DependencySpec(id="dependency.minecraft.body", title="饥饿、顶饿与恢复", direction="downstream", target_system_id="protoworld-body-system", dependency="熟食和三餐类别写入身体档，并影响回血耗时与顶饿消耗。", source_ids=["src.minecraft.body-system", "src.minecraft.food-profiles"], artifact_ids=[screens["roasted_food"].id], run_id=e2e_run_id),
            DependencySpec(id="dependency.minecraft.day-night", title="昼夜、天气与方块光", direction="shared", target_system_id="minecraft-world-time-weather-light", dependency="白天时限、雨天点火修正与夜间遗民灼烧都依赖世界状态。", source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.body-system"], artifact_ids=[screens["night_light"].id], run_id=e2e_run_id),
        ]
        player_voices = [
            PlayerVoice(id="voice-record.minecraft.hunger-depth", summary="玩家希望饥饿在开局承担真实生存管理，但不希望它在资源稳定后只剩周期性吃东西；复杂餐食和多样饮食应有持续价值。", theme="hunger-depth", sentiment="mixed", source_id="voice.minecraft.hunger-depth", system_node_id="flow.minecraft.eat", target_object_ids=["progression.minecraft.food-ladder", "balance.minecraft.hunger-food", "mechanism.minecraft.meal-regeneration"], version_context="community discussion; not ProtoWorld-specific", language="en", tags=["hunger", "food-variety", "early-game"]),
            PlayerVoice(id="voice-record.minecraft.campfire-legibility", summary="玩家要求营火上的熟成状态更可见、取回动作更明确；这直接约束烤位进度与产物反馈的可读性。", theme="campfire-legibility", sentiment="question", source_id="voice.minecraft.campfire-legibility", system_node_id="flow.minecraft.roast", target_object_ids=["feedback.minecraft.campfire", "surface.minecraft.campfire-cooking"], version_context="vanilla campfire feedback; interaction principle applies", language="en", tags=["campfire", "cooking", "feedback"]),
            PlayerVoice(id="voice-record.minecraft.food-diversity", summary="玩家认为多种食物在资源紧缺的开局最有意义，但基础饥饿/饱和模型不足以长期支撑选择；三餐元素谱是本实例对此问题的设计回应。", theme="food-diversity", sentiment="mixed", source_id="voice.minecraft.food-diversity", system_node_id="flow.minecraft.eat", target_object_ids=["resource-model.minecraft.first-night-food", "progression.minecraft.food-ladder"], version_context="Java survival food discussion", language="en", tags=["diet", "variety", "saturation"]),
        ]

        gates_copy = self.store.artifact_root / f"art.minecraft.e2e.gates.{e2e_digest}.json"
        shutil.copyfile(gates_path, gates_copy)
        gates_artifact = ArtifactRef(
            id=f"art.minecraft.e2e.gates.{e2e_digest}",
            kind="runtime_state",
            path=str(gates_copy),
            sha256=hashlib.sha256(gates_copy.read_bytes()).hexdigest(),
            run_id=e2e_run_id,
            locator=gates_path.name,
            media_type="application/json",
            metadata={
                "public": True,
                "gate_count": gates_payload.get("total"),
                "contains": "objective benchmark results only; no account secret or token",
            },
        )
        all_artifacts = [*screens.values(), gates_artifact, *wireframes, wireflow]
        runs = [
            RunRef(
                id=baseline_run_id,
                target_id=f"minecraft://{manifest.server_address}",
                adapter="bw-camera-real-input-and-rcon-oracle",
                started_at=manifest.captured_at,
                ended_at=manifest.captured_at,
                status="passed",
                build_scope_id=self.SCOPE_ID,
                artifact_ids=[screens["world_start"].id, screens["recipe_zeroed"].id],
                note="Real client world/HUD capture plus workbench recipe-absence probe.",
            ),
            RunRef(
                id=e2e_run_id,
                target_id=f"minecraft://{manifest.server_address}",
                adapter="proto-world-first-night-real-input-e2e",
                started_at=manifest.captured_at,
                ended_at=manifest.captured_at,
                status="passed",
                build_scope_id=self.SCOPE_ID,
                artifact_ids=[
                    *[
                        screens[role].id
                        for role in SCREEN_ROLES
                        if role not in {"world_start", "recipe_zeroed"}
                    ],
                    gates_artifact.id,
                ],
                note=f"{gates_payload.get('passed')}/{gates_payload.get('total')} objective gates passed; reset_after_run={manifest.reset_after_run}.",
            ),
            RunRef(
                id=self.DESIGN_RUN_ID,
                target_id="analysis://minecraft/first-night-fire-food",
                adapter="game-observatory-design-reconstruction",
                started_at=manifest.captured_at,
                ended_at=utc_now(),
                status="passed",
                build_scope_id=self.SCOPE_ID,
                artifact_ids=[*[item.id for item in wireframes], wireflow.id],
                note="Reviewed hybrid reconstruction from real frames, E2E gates, source rules, and preserved player feedback links.",
            ),
        ]
        benchmark = BenchmarkTask(
            id="task.minecraft.first-night-fire-food.v2",
            title="固定世界第一夜生火、熟食、三餐与火光安全闭环",
            start_state="fresh fixed-world snapshot; survival player; empty inventory; daytime",
            goal="完成 24 个真实输入客观门，并在测试后重建世界",
            allowed_actions=["look", "move", "jump", "attack", "use", "select_hotbar", "wait", "eat", "respawn"],
            reset_method="archive current world, rebuild deterministic island with tools/reset_world.ps1, then snapshot",
            checks=[
                ObjectiveCheck(id="all-24-gates", description="G1-G24 全部通过", expected=24, actual=gates_payload.get("passed"), passed=gates_payload.get("passed") == 24),
                ObjectiveCheck(id="world-handcraft", description="散石、堆枝与概率生火通过", expected=True, actual=all(gates_by_id.get(gate, {}).get("ok") for gate in ("G1", "G2", "G3")), passed=all(gates_by_id.get(gate, {}).get("ok") for gate in ("G1", "G2", "G3"))),
                ObjectiveCheck(id="food-conversion", description="生食烤制、进食与三餐通过", expected=True, actual=all(gates_by_id.get(gate, {}).get("ok") for gate in ("G4", "G5", "G7")), passed=all(gates_by_id.get(gate, {}).get("ok") for gate in ("G4", "G5", "G7"))),
                ObjectiveCheck(id="night-fire", description="夜间敌人、火光灼烧和知识反馈通过", expected=True, actual=all(gates_by_id.get(gate, {}).get("ok") for gate in ("G14", "G15", "G16")), passed=all(gates_by_id.get(gate, {}).get("ok") for gate in ("G14", "G15", "G16"))),
                ObjectiveCheck(id="advanced-food-loop", description="根茎、兽肉、烧陶、煮羹与吃羹通过", expected=True, actual=all(gates_by_id.get(gate, {}).get("ok") for gate in ("G19", "G20", "G21", "G22", "G23", "G24")), passed=all(gates_by_id.get(gate, {}).get("ok") for gate in ("G19", "G20", "G21", "G22", "G23", "G24"))),
                ObjectiveCheck(id="recipe-boundary", description="原版石镐配方确定性不可用", expected=True, actual="unknown recipe" in manifest.recipe_probe_response.lower(), passed="unknown recipe" in manifest.recipe_probe_response.lower()),
                ObjectiveCheck(id="world-reset", description="测试后执行归档、重建与快照", expected=True, actual=manifest.reset_after_run, passed=manifest.reset_after_run),
            ],
            note="这是可变更但可复位的本地 Minecraft benchmark；真实输入走 KeyBinding/Screen，RCON 只布置和读取客观状态。",
            metadata={"manifest": manifest.model_dump(mode="json"), "gates": gates_payload, "source_oracle": source_evidence},
        )

        object_map = {
            "scope": [self.SCOPE_ID],
            "system_overview": [overview.id, self.SYSTEM_ID],
            "player_goals": [player_goal.id],
            "entry_unlock": [entry.id],
            "core_loop": [core_loop.id],
            "information_architecture": [architecture.id],
            "surface_design": [*[item.id for item in layout_specs], *[item.id for item in design_artifacts if item.kind == "wireframe"]],
            "interaction_flow": [interaction.id, "design-artifact.minecraft.first-night-wireflow"],
            "state_matrix": [state_matrix.id],
            "rules_mechanics": [item.id for item in mechanisms],
            "resources_economy": [resource_model.id, *[item.id for item in resources]],
            "progression_balance": [progression.id, balance.id],
            "feedback": [feedback.id],
            "tutorial": [tutorial.id],
            "failure_recovery": [item.id for item in failures],
            "dependencies": [item.id for item in dependencies],
            "player_voice": [item.id for item in player_voices],
            "version_provenance": [version_note.id, self.SCOPE_ID, *source_ids],
        }
        coverage = [
            DesignSectionCoverage(
                section=section,
                status="complete",
                object_ids=object_ids,
                rationale="由 2026-07-13 固定世界实跑、E2E 客观门、源码/配表 oracle 或保留链接的玩家反馈支持。",
            )
            for section, object_ids in object_map.items()
        ]
        design_spec = ReverseEngineeredGameDesignSpec(
            id="design-spec.minecraft.first-night-fire-food.v2",
            title="世界内生火、营火烹饪与熟食反馈",
            scope_id=self.SCOPE_ID,
            system_instance_id=self.INSTANCE_ID,
            overview=[overview],
            player_goals=[player_goal],
            entry_and_unlock=[entry],
            core_loop=core_loop,
            information_architecture=architecture,
            design_artifacts=design_artifacts,
            layout_specs=layout_specs,
            interaction_specs=[interaction],
            state_matrices=[state_matrix],
            progression_specs=[progression],
            balance_specs=[balance],
            feedback_specs=[feedback],
            tutorial_specs=[tutorial],
            failure_recovery_specs=failures,
            dependency_specs=dependencies,
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
        scope = BuildScope(
            id=self.SCOPE_ID,
            game_id="minecraft-java",
            platform="windows-java",
            version=manifest.game_version,
            region="local-fixed-world",
            locale="zh-CN",
            account_stage="newbie-first-night",
            device="Fabric client + dedicated server + bw-camera",
            server=manifest.server_address,
            resolution="854x480",
            captured_at=manifest.captured_at,
            source_ids=[runtime_source, "src.minecraft.recipe-zeroing"],
            artifact_ids=[item.id for item in screens.values()],
            run_id=e2e_run_id,
        )
        concept = SystemConcept(
            id=self.SYSTEM_ID,
            title=design_spec.title,
            description="用世界对象堆叠、概率点火、营火计时加工、身体状态和夜间光照组织第一夜生存闭环。",
            tags=["world-handcraft", "fire", "cooking", "hunger", "night-safety"],
            source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.body-system"],
            artifact_ids=[screens["campfire"].id, screens["roasted_food"].id, screens["night_light"].id],
            run_id=e2e_run_id,
        )
        instance = SystemInstance(
            id=self.INSTANCE_ID,
            concept_id=concept.id,
            build_scope_id=scope.id,
            title="ProtoWorld 固定岛第一夜 v4 实例",
            surface_ids=[item.id for item in surfaces],
            source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.recipe-zeroing"],
            artifact_ids=[item.id for item in screens.values()],
            run_ids=[baseline_run_id, e2e_run_id],
        )
        summary = (
            "本设计案反推一个以世界交互替代原版菜单配方的第一夜系统：玩家从散落物取得材料，"
            "堆三根枯枝并用石子进行有火星反馈的概率点火，再把生食放入营火完成计时转换。"
            "熟食进入饥饿/顶饿与三餐状态，篝火同时形成夜间光照安全区。24/24 真实输入门已通过，"
            "原版石镐配方在本实例被源码策略确定性清零。"
        )
        report = GameReport(
            id=self.REPORT_ID,
            slug=self.SLUG,
            game_id="minecraft-java",
            game_title="Minecraft Java Edition / ProtoWorld",
            system_id=concept.id,
            system_title=concept.title,
            summary=summary,
            contract_version="reverse-engineered-game-design-spec.v0.3",
            migration_status="publishable",
            design_spec=design_spec,
            summary_claim=Claim(id="claim.minecraft.summary", kind=ContentKind.analyst_interpretation, statement=summary, source_ids=[runtime_source, "src.minecraft.handcraft", "src.minecraft.recipe-zeroing", "src.minecraft.body-system", *[item.source_id for item in player_voices]], artifact_ids=[screens["campfire"].id, screens["roasted_food"].id, screens["recipe_zeroed"].id, screens["night_light"].id], run_id=e2e_run_id, review_status="reviewed"),
            scope=scope,
            game=Game(id="minecraft-java", title="Minecraft Java Edition / ProtoWorld", aliases=["Minecraft", "ProtoWorld"], platforms=["windows-java", "macos-java", "linux-java"], source_ids=[runtime_source]),
            system_concept=concept,
            system_instance=instance,
            resource_model=resource_model,
            tags=["minecraft", "pc", "first-night", "survival", "world-handcraft", "fire", "campfire", "cooking", "hunger", "food-diversity", "night-safety", "real-input-e2e", "source-oracle", "player-voice"],
            status="published",
            cover_artifact_id=screens["campfire"].id,
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
                Observation(id="observation.minecraft.gates", statement=f"固定世界 E2E 由玩家 {manifest.benchmark_player} 通过 {gates_payload.get('passed')}/{gates_payload.get('total')} 门，并在结束后触发世界重建。", source_ids=[runtime_source], artifact_ids=[gates_artifact.id, screens["campfire"].id, screens["roasted_food"].id], run_id=e2e_run_id),
                Observation(id="observation.minecraft.recipe-boundary", statement=f"工作台可打开但石镐配方不存在；探针响应为：{manifest.recipe_probe_response}", source_ids=[runtime_source, "src.minecraft.recipe-zeroing"], artifact_ids=[screens["recipe_zeroed"].id], run_id=baseline_run_id),
                Observation(id="observation.minecraft.fire-night", statement="夜间 E2E 同时观察到遗民、光照灼烧和两阶段火光知识确证。", source_ids=[runtime_source, "src.minecraft.body-system", "src.minecraft.handbook-fire-food"], artifact_ids=[screens["night_threat"].id, screens["night_burning"].id, screens["night_light"].id], run_id=e2e_run_id),
            ],
            interpretations=[
                Claim(id="claim.minecraft.world-action-cost", kind=ContentKind.analyst_interpretation, statement="把配方从菜单搬到世界空间增加了摆放、瞄准与概率摩擦，因此情境提示和失败火星不是装饰，而是维持可学性的必要反馈。", source_ids=["src.minecraft.handcraft", "src.minecraft.jade-guidance", "voice.minecraft.campfire-legibility"], artifact_ids=[screens["campfire"].id], run_id=e2e_run_id, review_status="reviewed"),
                Claim(id="claim.minecraft.shared-fire-node", kind=ContentKind.analyst_interpretation, statement="篝火把食物加工、知识反馈、照明和夜间安全合并为一个高复用设施，减少第一夜多个系统各自设入口的认知成本。", source_ids=[runtime_source, "src.minecraft.body-system", "src.minecraft.handbook-fire-food"], artifact_ids=[screens["campfire"].id, screens["roasted_food"].id, screens["night_light"].id], run_id=e2e_run_id, review_status="reviewed"),
                Claim(id="claim.minecraft.variety-response", kind=ContentKind.analyst_interpretation, statement="三餐元素谱把玩家对 Minecraft 食物同质化的长期抱怨转成可测的恢复差异，但其长期节奏仍需脱离第一夜继续观察。", source_ids=[runtime_source, "voice.minecraft.hunger-depth", "voice.minecraft.food-diversity"], artifact_ids=[screens["roasted_food"].id], run_id=e2e_run_id, review_status="reviewed"),
            ],
            open_questions=["未经源码和测试说明的新玩家，能否仅靠火星与 Jade 提示理解概率失败？", "三餐多样性在资源稳定后是否仍有选择价值，而不是新的例行负担？", "营火烤位的 600 tick 进度是否需要更明确的视觉阶段？"],
        )
        report.assert_publishable()
        assert_public_artifacts(report)
        return report

    @staticmethod
    def _wireflow_svg(surfaces: list[Surface]) -> str:
        width, height = 1480, 520
        cards: list[str] = []
        arrows: list[str] = []
        for index, surface in enumerate(surfaces):
            x = 35 + index * 355
            cards.append(
                f'<g><rect x="{x}" y="105" width="285" height="285" rx="20" fill="#fff" '
                'stroke="#23334a" stroke-width="3"/>'
                f'<text x="{x + 18}" y="152" font-size="19" font-weight="700" fill="#192638">{html.escape(surface.title)}</text>'
                f'<text x="{x + 18}" y="187" font-size="13" fill="#607089">{html.escape(surface.kind)}</text>'
                f'<text x="{x + 18}" y="232" font-size="12" fill="#334155">{len(surface.elements)} semantic elements</text></g>'
            )
            if index < len(surfaces) - 1:
                arrows.append(
                    f'<path d="M {x + 285} 245 L {x + 345} 245" stroke="#d2603a" '
                    'stroke-width="5" marker-end="url(#arrow)"/>'
                )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">'
            '<title id="title">Minecraft first-night fire and food wireflow</title>'
            '<desc id="desc">World foraging, campfire ignition, food conversion, and the intentionally disabled grid-recipe branch.</desc>'
            '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">'
            '<path d="M0,0 L0,6 L9,3 z" fill="#d2603a"/></marker></defs>'
            '<rect width="1480" height="520" fill="#f5f1e8"/>'
            '<text x="35" y="52" font-size="27" font-weight="700" fill="#172536">World interaction replaces the vanilla recipe shortcut</text>'
            + "".join(arrows)
            + "".join(cards)
            + "</svg>"
        )

    def verify(
        self,
        manifest: MinecraftFirstNightEvidenceManifest,
        report: GameReport,
    ) -> dict[str, Any]:
        gates_path, _ = self._input_paths(manifest)
        gates_payload = json.loads(gates_path.read_text(encoding="utf-8"))
        gates = {item["id"]: item for item in gates_payload.get("gates", [])}
        required_groups = {
            "world-handcraft": ("G1", "G2", "G3"),
            "food-and-body": ("G4", "G5", "G6", "G7"),
            "night-fire": ("G14", "G15", "G16"),
            "advanced-food": ("G19", "G20", "G21", "G22", "G23", "G24"),
        }
        checks: list[dict[str, Any]] = [
            {
                "id": "publishable-design-spec",
                "passed": not report.publication_issues(),
                "actual": report.publication_issues(),
            },
            {
                "id": "four-real-surface-frames",
                "passed": len(report.surfaces) == 4
                and all(surface.artifact_ids for surface in report.surfaces),
                "actual": len(report.surfaces),
            },
            {
                "id": "all-e2e-gates",
                "passed": gates_payload.get("passed") == gates_payload.get("total") == 24,
                "actual": f"{gates_payload.get('passed')}/{gates_payload.get('total')}",
            },
            {
                "id": "recipe-boundary-probed",
                "passed": "unknown recipe" in manifest.recipe_probe_response.lower(),
                "actual": manifest.recipe_probe_response,
            },
            {
                "id": "world-reset-after-run",
                "passed": manifest.reset_after_run,
                "actual": manifest.reset_after_run,
            },
            {
                "id": "source-oracle",
                "passed": self.source_evidence(manifest)["ok"],
                "actual": self.source_evidence(manifest)["checks"],
            },
            {
                "id": "design-artifact-pairing",
                "passed": report.design_spec is not None
                and len(report.design_spec.design_artifacts) >= len(report.surfaces) + 1,
                "actual": len(report.design_spec.design_artifacts) if report.design_spec else 0,
            },
        ]
        for group, gate_ids in required_groups.items():
            checks.append(
                {
                    "id": group,
                    "passed": all(gates.get(gate_id, {}).get("ok") is True for gate_id in gate_ids),
                    "actual": {
                        gate_id: gates.get(gate_id, {}).get("evidence") for gate_id in gate_ids
                    },
                }
            )
        payload = {
            "schema": "game-observatory.minecraft-first-night-fire-food.v2",
            "generated_at": utc_now(),
            "ok": all(item["passed"] for item in checks),
            "boundary": (
                "Local mutable benchmark with deterministic cleanup: real client input mutates a dedicated "
                "fixed world, then reset_world.ps1 archives and rebuilds it. Vanilla stone-pickaxe crafting "
                "is explicitly out of scope because this instance removes that recipe."
            ),
            "manifest": manifest.model_dump(mode="json"),
            "report_id": report.id,
            "checks": checks,
            "gates": gates_payload,
            "source_oracle": self.source_evidence(manifest),
        }
        path = self.store.export_root / "minecraft-first-night-fire-food.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["path"] = str(path)
        return payload

    def promote(self, manifest: MinecraftFirstNightEvidenceManifest) -> dict[str, Any]:
        report = self.build(manifest)
        verification = self.verify(manifest, report)
        if not verification["ok"]:
            raise ValueError("Minecraft first-night design verification failed")
        for run in report.runs:
            self.store.save_run(
                RunResult(
                    id=run.id,
                    adapter=run.adapter,
                    target_id=run.target_id,
                    task_id=report.benchmark_task.id if report.benchmark_task else None,
                    status=run.status,
                    started_at=run.started_at,
                    ended_at=run.ended_at,
                    artifact_ids=run.artifact_ids,
                )
            )
        self.store.upsert_report(report)
        return {"report": report, "verification": verification}
