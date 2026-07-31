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
    recipe_probe_response: str
    reset_after_run: bool
    note: str | None = None

    def required_role_issues(self) -> list[str]:
        return [role for role in SCREEN_ROLES if not self.screenshot_paths.get(role)]


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
        files = {
            "handcraft": root / "src/main/java/com/voxelcraft/protoworld/craft/HandcraftSystem.java",
            "jade": root / "src/main/java/com/voxelcraft/protoworld/client/jade/ProtoWorldJadePlugin.java",
            "recipe_zeroing": root / "src/main/java/com/voxelcraft/protoworld/recipe/RecipeZeroingHook.java",
            "body": root / "src/main/java/com/voxelcraft/protoworld/body/BodySystem.java",
            "food_profiles": root / "specs/food_profiles.json",
            "handbook_entries": root / "specs/handbook_entries.json",
            "e2e": root / "tools/tests/first_night_e2e.py",
        }
        result: dict[str, Any] = {"root": str(root), "files": {}, "checks": []}
        symbol_checks = {
            "handcraft": ("twig_pile", "CampfireBlock", "0.40"),
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