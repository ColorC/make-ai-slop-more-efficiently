"""Apply one reviewed immutable AI-player environment promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..store import ObservatoryStore
from .contracts import EnvironmentPromotionV1
from .store import AIPlayerStore


class EnvironmentPromotionSeedResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "game-observatory.ai-player.environment-promotion-seed-result.v1"
    ] = Field(
        default="game-observatory.ai-player.environment-promotion-seed-result.v1",
        alias="schema",
    )
    seed_sha256: str = Field(min_length=64, max_length=64)
    store_root: str = Field(min_length=1)
    database_path: str = Field(min_length=1)
    store_schema_version: int = Field(ge=1)
    promotion_id: str = Field(min_length=1)
    parent_environment_id: str = Field(min_length=1)
    child_environment_id: str = Field(min_length=1)
    inserted_promotion_count: int = Field(ge=0, le=1)
    lineage_path: list[str] = Field(min_length=2)
    persistence_reopen_verified: bool


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_sha256(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("expected seed SHA-256 must be 64 hexadecimal characters")
    return normalized


def ingest_environment_promotion_seed(
    store_root: Path,
    seed_path: Path,
    *,
    expected_store_root: Path,
    expected_seed_sha256: str,
) -> EnvironmentPromotionSeedResultV1:
    """Hash-check, atomically apply, reopen, and verify one promotion."""

    resolved_root = store_root.resolve()
    resolved_expected_root = expected_store_root.resolve()
    if resolved_root != resolved_expected_root:
        raise ValueError(
            "AI-player store root mismatch: "
            f"expected {resolved_expected_root}, received {resolved_root}"
        )
    if not resolved_root.is_dir():
        raise FileNotFoundError(f"AI-player store root is missing: {resolved_root}")

    seed_bytes = seed_path.read_bytes()
    seed_sha256 = _sha256_bytes(seed_bytes)
    if seed_sha256 != _validate_sha256(expected_seed_sha256):
        raise ValueError("environment promotion seed SHA-256 mismatch")
    promotion = EnvironmentPromotionV1.model_validate_json(seed_bytes)

    observatory = ObservatoryStore(resolved_root)
    player = AIPlayerStore(observatory)
    existing = player.get_environment_promotion(promotion.id)
    player.promote_environment(promotion)

    reopened_observatory = ObservatoryStore(resolved_root)
    reopened = AIPlayerStore(reopened_observatory)
    selection = reopened.select_current_environment(promotion.parent_environment_id)
    persistence_verified = (
        _sha256_bytes(seed_path.read_bytes()) == seed_sha256
        and reopened.get_environment_promotion(promotion.id) == promotion
        and reopened.get_environment(promotion.child_environment.id)
        == promotion.child_environment
        and selection.selected_environment_id == promotion.child_environment.id
        and selection.lineage_path[-1] == promotion.child_environment.id
    )
    return EnvironmentPromotionSeedResultV1(
        seed_sha256=seed_sha256,
        store_root=str(resolved_root),
        database_path=str(reopened_observatory.db_path.resolve()),
        store_schema_version=reopened.schema_version,
        promotion_id=promotion.id,
        parent_environment_id=promotion.parent_environment_id,
        child_environment_id=promotion.child_environment.id,
        inserted_promotion_count=int(existing is None),
        lineage_path=selection.lineage_path,
        persistence_reopen_verified=persistence_verified,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--expected-store-root", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--expected-seed-sha256", required=True)
    parser.add_argument("--result", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = ingest_environment_promotion_seed(
        args.store_root,
        args.seed,
        expected_store_root=args.expected_store_root,
        expected_seed_sha256=args.expected_seed_sha256,
    )
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(
        json.dumps(
            result.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\
",
        encoding="utf-8",
    )
    return int(not result.persistence_reopen_verified)


if __name__ == "__main__":
    raise SystemExit(main())