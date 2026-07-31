"""Validate and idempotently ingest a research guide bundle into its current leaf."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import SourceSnapshot
from ..store import ObservatoryStore
from .contracts import GuideKnowledgeV1
from .guide_research import GuideDecisionContextV1, assess_guide
from .store import AIPlayerStore


EXPECTED_RECORD_ID = "res:截至2026-07-16-三国谋定天下-bilibili-131:921d9daa"
EXPECTED_GUIDE_COUNT = 11


class ResearchFileSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    local_path: str = Field(min_length=1)
    snapshot: SourceSnapshot


class SanguoCurrentLeafGuideSeedV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "game-observatory.ai-player.sanguo-current-leaf-guide-seed.v1"
    ] = Field(
        default="game-observatory.ai-player.sanguo-current-leaf-guide-seed.v1",
        alias="schema",
    )
    target_environment_id: str = Field(min_length=1)
    research_record_id: str = Field(min_length=1)
    expected_research_source_count: int = Field(ge=1)
    usage_mode: Literal["discovery_only"]
    truth_precedence: Literal["client_live_evidence_first"]
    native: ResearchFileSnapshotV1
    report: ResearchFileSnapshotV1
    guides: tuple[GuideKnowledgeV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_closed_guide_bundle(self) -> "SanguoCurrentLeafGuideSeedV1":
        if self.research_record_id != EXPECTED_RECORD_ID:
            raise ValueError("unexpected Sanguo guide research record id")
        if self.expected_research_source_count != EXPECTED_GUIDE_COUNT:
            raise ValueError("Sanguo guide seed must declare exactly 11 research sources")
        if self.native.snapshot.source_id != self.research_record_id:
            raise ValueError("native SourceSnapshot must bind the research record id")
        if self.report.snapshot.source_id != self.research_record_id:
            raise ValueError("report SourceSnapshot must bind the research record id")
        if self.native.snapshot.id == self.report.snapshot.id:
            raise ValueError("native and report SourceSnapshots must have distinct ids")
        for item in (self.native, self.report):
            if item.snapshot.locator != item.local_path:
                raise ValueError("research SourceSnapshot locator must equal local_path")
        if len(self.guides) != EXPECTED_GUIDE_COUNT:
            raise ValueError("Sanguo current-leaf guide seed must contain exactly 11 guides")
        guide_keys = [(guide.id, guide.version) for guide in self.guides]
        if len(guide_keys) != len(set(guide_keys)):
            raise ValueError("Sanguo current-leaf guide ids and versions must be unique")
        urls = [str(guide.url) for guide in self.guides]
        if len(urls) != len(set(urls)):
            raise ValueError("each research source must map to one unique guide URL")
        for guide in self.guides:
            if guide.environment_id != self.target_environment_id:
                raise ValueError("guide does not belong to the target current leaf")
            if guide.status != "unverified":
                raise ValueError("research guides must remain unverified before client validation")
            if not guide.missing_applicability_reason:
                raise ValueError("every guide must retain its applicability gaps")
            required_gap_markers = (
                "Bilibili 1.31.0",
                "略阳振威将",
                "1641服投鞭断水",
                "客户端",
            )
            if any(
                marker not in guide.missing_applicability_reason
                for marker in required_gap_markers
            ):
                raise ValueError(
                    "every applicability gap must name build, account, server/world, "
                    "and client truth"
                )
            if not (guide.published_at or guide.updated_at):
                raise ValueError("every guide must retain a publication or update date")
            if not guide.author.strip() or not guide.platform.strip():
                raise ValueError("every guide must retain author and platform provenance")
            if not guide.evidence_refs or any(
                reference.environment_id != self.target_environment_id
                or reference.source_ids != [self.research_record_id]
                for reference in guide.evidence_refs
            ):
                raise ValueError("every guide must cite only this research record")
        return self


class SanguoCurrentLeafGuideIngestResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "game-observatory.ai-player.sanguo-current-leaf-guide-ingest-result.v1"
    ] = Field(
        default="game-observatory.ai-player.sanguo-current-leaf-guide-ingest-result.v1",
        alias="schema",
    )
    store_root: str
    seed_path: str
    seed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    research_record_id: str
    research_native_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    research_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    research_source_count: int = Field(ge=1)
    target_environment_id: str
    lineage_path: list[str] = Field(min_length=1)
    source_snapshot_count: int = Field(ge=0)
    guide_count: int = Field(ge=0)
    inserted_source_snapshot_count: int = Field(ge=0)
    inserted_guide_count: int = Field(ge=0)
    all_guides_unverified: bool
    all_guides_discovery_only: bool
    client_live_evidence_has_precedence: bool
    persistence_reopen_verified: bool
    device_actions_performed: Literal[0] = 0


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _workspace_file(local_path: str, workspace_root: Path) -> Path:
    candidate = Path(local_path)
    if candidate.is_absolute():
        raise ValueError(f"research path must be workspace-relative: {local_path}")
    resolved = (workspace_root / candidate).resolve()
    if not resolved.is_relative_to(workspace_root.resolve()):
        raise ValueError(f"research path escapes workspace: {local_path}")
    if not resolved.is_file():
        raise FileNotFoundError(f"research file is missing: {local_path}")
    return resolved


def _load_and_validate_research(
    seed: SanguoCurrentLeafGuideSeedV1,
    *,
    workspace_root: Path,
    expected_native_sha256: str,
    expected_report_sha256: str,
    expected_source_count: int,
) -> tuple[Path, Path, dict[str, Any]]:
    if expected_source_count != seed.expected_research_source_count:
        raise ValueError(
            "detached research source count does not match seed: "
            f"{expected_source_count} != {seed.expected_research_source_count}"
        )
    native_path = _workspace_file(seed.native.local_path, workspace_root)
    report_path = _workspace_file(seed.report.local_path, workspace_root)
    native_hash = _sha256(native_path)
    report_hash = _sha256(report_path)
    expected_native = expected_native_sha256.lower()
    expected_report = expected_report_sha256.lower()
    if native_hash != expected_native or native_hash != seed.native.snapshot.content_sha256:
        raise ValueError("research native hash does not match detached hash and SourceSnapshot")
    if report_hash != expected_report or report_hash != seed.report.snapshot.content_sha256:
        raise ValueError("research report hash does not match detached hash and SourceSnapshot")

    native = json.loads(native_path.read_text(encoding="utf-8"))
    native_sources = native.get("sources")
    if not isinstance(native_sources, list) or len(native_sources) != expected_source_count:
        raise ValueError("research native source count does not match the detached count")
    source_urls = [str(item.get("url", "")) for item in native_sources]
    if any(not url for url in source_urls) or len(source_urls) != len(set(source_urls)):
        raise ValueError("research native must contain 11 unique source URLs")
    guide_urls = [str(guide.url) for guide in seed.guides]
    if guide_urls != source_urls:
        raise ValueError("guide order and URLs must exactly match research native sources")

    report_text = report_path.read_text(encoding="utf-8")
    if f"record_id `{seed.research_record_id}`" not in report_text:
        raise ValueError("research report does not contain the requested record id")
    if f"来源 {expected_source_count} 条" not in report_text:
        raise ValueError("research report does not contain the requested source count")
    return native_path, report_path, native


def ingest_sanguo_current_leaf_guides(
    store_root: Path,
    seed_path: Path,
    *,
    expected_seed_sha256: str,
    expected_native_sha256: str,
    expected_report_sha256: str,
    expected_source_count: int,
    workspace_root: Path | None = None,
) -> SanguoCurrentLeafGuideIngestResultV1:
    """Ingest one closed research bundle after proving the exact target is current."""

    workspace = (workspace_root or Path(__file__).resolve().parents[6]).resolve()
    seed_path = seed_path.resolve()
    actual_seed_hash = _sha256(seed_path)
    if actual_seed_hash != expected_seed_sha256.lower():
        raise ValueError(
            "Sanguo current-leaf guide seed hash mismatch: "
            f"expected {expected_seed_sha256.lower()}, got {actual_seed_hash}"
        )
    seed = SanguoCurrentLeafGuideSeedV1.model_validate_json(
        seed_path.read_text(encoding="utf-8")
    )
    native_path, report_path, _native = _load_and_validate_research(
        seed,
        workspace_root=workspace,
        expected_native_sha256=expected_native_sha256,
        expected_report_sha256=expected_report_sha256,
        expected_source_count=expected_source_count,
    )

    resolved_store_root = store_root.resolve()
    observatory = ObservatoryStore(resolved_store_root)
    player = AIPlayerStore(observatory)
    selection = player.select_environment_lineage(seed.target_environment_id)
    if selection.selected_environment_id != seed.target_environment_id:
        raise ValueError(
            "guide ingest target is not the unique current environment leaf: "
            f"{seed.target_environment_id} -> {selection.selected_environment_id}"
        )
    environment = selection.selected_environment
    if environment.game_id != "sanguo-mouding-tianxia" or environment.channel != "bilibili":
        raise ValueError("guide ingest target is not the expected Sanguo Bilibili environment")

    decision_context = GuideDecisionContextV1(
        environment_id=environment.id,
        build_scope_id=environment.build_scope_id,
        account_scope_id=environment.account_scope_id,
        channel=environment.channel,
        decision_at=datetime.fromisoformat(seed.guides[0].retrieved_at.replace("Z", "+00:00")),
        game_version=None,
        season=None,
        server_stage=None,
    )
    assessments = [assess_guide(guide, decision_context, environment) for guide in seed.guides]
    if any(assessment.mode != "discovery_only" for assessment in assessments):
        raise ValueError("all research guides must assess as discovery_only before ingest")

    snapshots = [seed.native.snapshot, seed.report.snapshot]
    inserted = player.apply_knowledge_memory_seed(
        seed.target_environment_id,
        snapshots,
        seed.guides,
        (),
    )

    reopened_observatory = ObservatoryStore(resolved_store_root)
    reopened = AIPlayerStore(reopened_observatory)
    reopened_selection = reopened.select_environment_lineage(seed.target_environment_id)
    persisted_snapshots = {
        snapshot.id: snapshot
        for snapshot in reopened_observatory.list_source_snapshots(seed.research_record_id)
    }
    persistence_verified = (
        reopened_selection.selected_environment_id == seed.target_environment_id
        and all(persisted_snapshots.get(snapshot.id) == snapshot for snapshot in snapshots)
        and all(
            reopened.get_guide_knowledge(
                seed.target_environment_id,
                guide.id,
                version=guide.version,
            )
            == guide
            for guide in seed.guides
        )
        and _sha256(native_path) == seed.native.snapshot.content_sha256
        and _sha256(report_path) == seed.report.snapshot.content_sha256
    )
    return SanguoCurrentLeafGuideIngestResultV1(
        store_root=str(resolved_store_root),
        seed_path=str(seed_path),
        seed_sha256=actual_seed_hash,
        research_record_id=seed.research_record_id,
        research_native_sha256=_sha256(native_path),
        research_report_sha256=_sha256(report_path),
        research_source_count=expected_source_count,
        target_environment_id=seed.target_environment_id,
        lineage_path=reopened_selection.lineage_path,
        source_snapshot_count=len(snapshots),
        guide_count=len(seed.guides),
        inserted_source_snapshot_count=inserted["inserted_source_snapshot_count"],
        inserted_guide_count=inserted["inserted_guide_count"],
        all_guides_unverified=all(guide.status == "unverified" for guide in seed.guides),
        all_guides_discovery_only=all(
            assessment.mode == "discovery_only" for assessment in assessments
        ),
        client_live_evidence_has_precedence=(
            seed.truth_precedence == "client_live_evidence_first"
        ),
        persistence_reopen_verified=persistence_verified,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--expected-seed-sha256", required=True)
    parser.add_argument("--expected-native-sha256", required=True)
    parser.add_argument("--expected-report-sha256", required=True)
    parser.add_argument("--expected-source-count", type=int, required=True)
    parser.add_argument("--result", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = ingest_sanguo_current_leaf_guides(
        args.store_root,
        args.seed,
        expected_seed_sha256=args.expected_seed_sha256,
        expected_native_sha256=args.expected_native_sha256,
        expected_report_sha256=args.expected_report_sha256,
        expected_source_count=args.expected_source_count,
    )
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(
        json.dumps(
            result.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return int(
        not result.persistence_reopen_verified
        or not result.all_guides_discovery_only
        or not result.client_live_evidence_has_precedence
    )


if __name__ == "__main__":
    raise SystemExit(main())
