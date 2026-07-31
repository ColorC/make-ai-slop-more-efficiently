"""Verified transfer between accidentally split local Observatory stores."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import ArtifactRef
from .store import ObservatoryStore


class StoreTransferResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.store-transfer-result.v1"] = Field(
        default="game-observatory.store-transfer-result.v1",
        alias="schema",
    )
    source_root: str
    destination_root: str
    dry_run: bool
    source_targets: int
    source_artifacts: int
    source_evidence_runs: int
    source_evidence_steps: int
    source_evidence_manifests: int
    copied_artifacts: int
    reused_artifacts: int
    inserted_targets: int
    inserted_evidence_runs: int
    inserted_evidence_steps: int
    inserted_evidence_manifests: int
    verification_pass: bool


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _same_model(left, right) -> bool:
    return left.model_dump(mode="json", by_alias=True) == right.model_dump(
        mode="json", by_alias=True
    )


def transfer_evidence_store(
    source_root: Path,
    destination_root: Path,
    *,
    dry_run: bool = False,
) -> StoreTransferResultV1:
    source_root = source_root.resolve()
    destination_root = destination_root.resolve()
    if source_root == destination_root:
        raise ValueError("source and destination Observatory roots must differ")
    if not (source_root / "observatory.sqlite3").is_file():
        raise FileNotFoundError(f"source Observatory database is missing: {source_root}")
    source = ObservatoryStore(source_root)
    destination = ObservatoryStore(destination_root)

    source_targets = source.list_targets()
    source_artifacts = source.list_artifacts()
    source_runs = source.list_evidence_runs(limit=1000)
    source_steps = [
        step for run in source_runs for step in source.list_evidence_steps(run.id)
    ]
    source_manifests = [
        manifest
        for run in source_runs
        if (manifest := source.get_evidence_manifest(run.id)) is not None
    ]

    artifact_plan: list[tuple[ArtifactRef, Path, Path, bool]] = []
    for artifact in source_artifacts:
        source_path = Path(artifact.path).resolve()
        if not source_path.is_relative_to(source_root):
            raise ValueError(f"source artifact path escapes source root: {artifact.id}")
        if not source_path.is_file():
            raise FileNotFoundError(f"source artifact file is missing: {artifact.id}")
        if _sha256(source_path) != artifact.sha256:
            raise ValueError(f"source artifact hash mismatch: {artifact.id}")
        destination_path = destination.artifact_root / source_path.name
        existing = destination.get_artifact(artifact.id)
        if existing is not None and existing.sha256 != artifact.sha256:
            raise ValueError(f"destination artifact id has different content: {artifact.id}")
        if destination_path.exists() and _sha256(destination_path) != artifact.sha256:
            raise ValueError(f"destination artifact path has different content: {destination_path}")
        artifact_plan.append(
            (artifact, source_path, destination_path, existing is not None)
        )

    for run in source_runs:
        existing = destination.get_evidence_run(run.id)
        if existing is not None and not _same_model(existing, run):
            raise ValueError(f"destination evidence run conflicts with source: {run.id}")
    for step in source_steps:
        existing = destination.get_evidence_step(step.id)
        if existing is not None and not _same_model(existing, step):
            raise ValueError(f"destination evidence step conflicts with source: {step.id}")
    for manifest in source_manifests:
        existing = destination.get_evidence_manifest(manifest.evidence_run_id)
        if existing is not None and not _same_model(existing, manifest):
            raise ValueError(
                "destination evidence manifest conflicts with source: "
                f"{manifest.evidence_run_id}"
            )

    inserted_targets = sum(
        destination.get_target(target.id) is None for target in source_targets
    )
    inserted_runs = sum(destination.get_evidence_run(run.id) is None for run in source_runs)
    inserted_steps = sum(
        destination.get_evidence_step(step.id) is None for step in source_steps
    )
    inserted_manifests = sum(
        destination.get_evidence_manifest(manifest.evidence_run_id) is None
        for manifest in source_manifests
    )
    copied_artifacts = sum(not destination_path.exists() for _, _, destination_path, _ in artifact_plan)
    reused_artifacts = len(artifact_plan) - copied_artifacts

    if not dry_run:
        for target in source_targets:
            if destination.get_target(target.id) is None:
                destination.upsert_target(target)
        for artifact, source_path, destination_path, _existing in artifact_plan:
            if not destination_path.exists():
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination_path)
            destination.save_artifact(
                artifact.model_copy(update={"path": str(destination_path)})
            )
        for run in source_runs:
            if destination.get_evidence_run(run.id) is None:
                destination.save_evidence_run(run)
        for step in source_steps:
            if destination.get_evidence_step(step.id) is None:
                destination.save_evidence_step(step)
        for manifest in source_manifests:
            if destination.get_evidence_manifest(manifest.evidence_run_id) is None:
                destination.save_evidence_manifest(manifest)

    verification_pass = True
    if not dry_run:
        for artifact, _source_path, destination_path, _existing in artifact_plan:
            stored = destination.get_artifact(artifact.id)
            if (
                stored is None
                or Path(stored.path).resolve() != destination_path.resolve()
                or not destination_path.is_file()
                or _sha256(destination_path) != artifact.sha256
            ):
                verification_pass = False
        verification_pass = verification_pass and all(
            (stored := destination.get_evidence_run(run.id)) is not None
            and _same_model(stored, run)
            for run in source_runs
        )
        verification_pass = verification_pass and all(
            (stored := destination.get_evidence_step(step.id)) is not None
            and _same_model(stored, step)
            for step in source_steps
        )
        verification_pass = verification_pass and all(
            (stored := destination.get_evidence_manifest(manifest.evidence_run_id)) is not None
            and _same_model(stored, manifest)
            for manifest in source_manifests
        )

    return StoreTransferResultV1(
        source_root=str(source_root),
        destination_root=str(destination_root),
        dry_run=dry_run,
        source_targets=len(source_targets),
        source_artifacts=len(source_artifacts),
        source_evidence_runs=len(source_runs),
        source_evidence_steps=len(source_steps),
        source_evidence_manifests=len(source_manifests),
        copied_artifacts=copied_artifacts,
        reused_artifacts=reused_artifacts,
        inserted_targets=inserted_targets,
        inserted_evidence_runs=inserted_runs,
        inserted_evidence_steps=inserted_steps,
        inserted_evidence_manifests=inserted_manifests,
        verification_pass=verification_pass,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--result", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = transfer_evidence_store(
        args.source_root,
        args.destination_root,
        dry_run=args.dry_run,
    )
    payload = json.dumps(result.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2)
    if args.result:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(payload + "\
", encoding="utf-8")
    print(payload)
    return int(not result.verification_pass)


if __name__ == "__main__":
    raise SystemExit(main())