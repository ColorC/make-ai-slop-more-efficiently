"""Persist the read-only Sanguo pre-login baseline and guide candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import ArtifactRef, SourceSnapshot
from ..store import ObservatoryStore
from .contracts import EnvironmentScopeV1, EvidenceReferenceV1, GuideKnowledgeV1
from .guide_research import load_guide_seed
from .store import AIPlayerStore


class SanguoPreloginMemoryResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_id: Literal["sanguo-prelogin-memory-result.v1"] = Field(
        default="sanguo-prelogin-memory-result.v1",
        alias="schema",
    )
    environment_id: str
    runtime_db: str
    environment_input_path: str
    environment_input_sha256: str = Field(min_length=64, max_length=64)
    guide_seed_path: str
    guide_seed_sha256: str = Field(min_length=64, max_length=64)
    research_native_path: str
    research_native_sha256: str = Field(min_length=64, max_length=64)
    research_record_id: str
    guide_count: int = Field(ge=0)
    guide_source_snapshot_count: int = Field(ge=0)
    all_guides_unverified_before_live_identity: bool
    ai_player_schema_version: int = Field(ge=1)
    persistence_reopen_verified: bool
    device_actions_performed: Literal[0] = 0


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def seed_sanguo_prelogin_memory(
    *,
    workspace_root: Path,
    environment_path: Path,
    guide_seed_path: Path,
    research_native_path: Path,
    store_root: Path,
    output_path: Path,
) -> SanguoPreloginMemoryResultV1:
    root = workspace_root.resolve()
    environment_path = environment_path.resolve()
    guide_seed_path = guide_seed_path.resolve()
    research_native_path = research_native_path.resolve()
    baseline = json.loads(environment_path.read_text(encoding="utf-8"))
    guide_seed = json.loads(guide_seed_path.read_text(encoding="utf-8"))
    if baseline.get("schema") != "ai-player-environment-baseline.v1":
        raise ValueError("unsupported Sanguo environment baseline schema")
    if baseline.get("status") != "pre_login_observed":
        raise ValueError("this seed command accepts only the read-only pre-login baseline")
    if baseline.get("device_actions_performed") != 0:
        raise ValueError("pre-login seed baseline must record zero device actions")
    research_record_id = str(guide_seed["research_record_id"])
    environment_id = "environment.sanguo.bilibili.mumu.prelogin.1_31_0"
    environment_sha = _sha256(environment_path)
    guide_seed_sha = _sha256(guide_seed_path)
    research_sha = _sha256(research_native_path)

    observatory = ObservatoryStore(store_root)
    environment_artifact = ArtifactRef(
        id="art.ai-player.sanguo.environment-baseline.v1",
        kind="runtime_state",
        path=str(environment_path),
        sha256=environment_sha,
        media_type="application/json",
        metadata={
            "environment_id": environment_id,
            "read_only_capture": True,
            "device_actions_performed": 0,
        },
    )
    observatory.save_artifact(environment_artifact)
    observatory.save_source_snapshot(
        SourceSnapshot(
            id=f"snapshot.ai-player.sanguo.guide-research.{research_sha[:16]}",
            source_id=research_record_id,
            content_sha256=research_sha,
            locator=_relative(research_native_path, root),
            excerpt="14-source current-guide discovery record for the pure AI account.",
            captured_at=str(guide_seed["retrieved_at"]),
            metadata={
                "source_count": len(guide_seed.get("guides", [])),
                "channel": baseline["game"]["channel"],
            },
        )
    )

    identity_payload = {
        "device": baseline["canonical_device"],
        "game": baseline["game"],
        "status": baseline["status"],
    }
    environment = EnvironmentScopeV1(
        id=environment_id,
        game_id=baseline["game"]["game_id"],
        build_scope_id=(
            f"{baseline['game']['channel']}-{baseline['game']['version_name']}-"
            f"{baseline['game']['version_code']}"
        ),
        account_scope_id="account.unconfirmed.prelogin",
        device_scope_id="device.mumu15.local.canonical-16384",
        locale="zh-CN",
        viewport_width=1080,
        viewport_height=1920,
        identity_hash=_canonical_hash(identity_payload),
        evidence_refs=[
            EvidenceReferenceV1(
                environment_id=environment_id,
                artifact_ids=[environment_artifact.id],
            )
        ],
        created_at=str(baseline["captured_at"]),
    )
    player_store = AIPlayerStore(observatory)
    player_store.put_environment(environment)
    guides = load_guide_seed(
        guide_seed_path,
        environment_id=environment_id,
        research_record_id=research_record_id,
    )
    for guide in guides:
        existing = player_store.get_guide_knowledge(environment_id, guide.id, version=guide.version)
        if existing is None:
            player_store.append_guide_knowledge(guide)
        elif existing != guide:
            raise ValueError(f"guide seed changed inside an existing runtime: {guide.id}")

    reopened = AIPlayerStore(ObservatoryStore(store_root))
    persisted_guides = reopened.list_guide_knowledge(environment_id)
    source_snapshots = reopened.observatory_store.list_source_snapshots(research_record_id)
    if reopened.get_environment(environment_id) != environment:
        raise RuntimeError("Sanguo pre-login environment changed after store reopen")
    if persisted_guides != guides:
        raise RuntimeError("Sanguo guide candidates changed after store reopen")
    if not source_snapshots or any(
        snapshot.source_id != research_record_id for snapshot in source_snapshots
    ):
        raise RuntimeError("canonical guide research record did not survive store reopen")

    result = SanguoPreloginMemoryResultV1(
        environment_id=environment_id,
        runtime_db=_relative(Path(observatory.db_path), root),
        environment_input_path=_relative(environment_path, root),
        environment_input_sha256=environment_sha,
        guide_seed_path=_relative(guide_seed_path, root),
        guide_seed_sha256=guide_seed_sha,
        research_native_path=_relative(research_native_path, root),
        research_native_sha256=research_sha,
        research_record_id=research_record_id,
        guide_count=len(persisted_guides),
        guide_source_snapshot_count=len(source_snapshots),
        all_guides_unverified_before_live_identity=all(
            guide.status == "unverified" for guide in persisted_guides
        ),
        ai_player_schema_version=reopened.schema_version,
        persistence_reopen_verified=True,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            result.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\
",
        encoding="utf-8",
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--guide-seed", type=Path, required=True)
    parser.add_argument("--research-native", type=Path, required=True)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = seed_sanguo_prelogin_memory(
        workspace_root=args.workspace_root,
        environment_path=args.environment,
        guide_seed_path=args.guide_seed,
        research_native_path=args.research_native,
        store_root=args.store_root,
        output_path=args.output,
    )
    return int(
        result.guide_count != 14
        or not result.all_guides_unverified_before_live_identity
        or not result.persistence_reopen_verified
    )


if __name__ == "__main__":
    raise SystemExit(main())