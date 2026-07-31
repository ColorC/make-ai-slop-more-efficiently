"""Manage and run the local independent AI-player soft-signal reviewer."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ..runtime import GameObservatory
from .contracts import PlayerSoftSignalReviewerRole
from .soft_signal_reviewer_runtime import (
    build_review_bundle,
    import_local_reviewer_key,
    initialize_local_reviewer_key,
    load_submission,
    prepare_external_review,
    register_formal_reviewer_identity,
    reviewer_status,
    submit_external_signed_review,
    submit_signed_review,
)
from .soft_signal_attestation import PlayerSoftSignalReviewerPublicKeyV1
from .contracts import PlayerSoftSignalReviewV1
from .store import AIPlayerStore


_ROLES: tuple[PlayerSoftSignalReviewerRole, ...] = (
    "independent_agent",
    "human",
    "runtime_critic",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        help="Observatory root; omit to use the canonical local GAME_OBSERVATORY_ROOT",
    )
    operations = parser.add_subparsers(dest="operation", required=True)

    init = operations.add_parser(
        "init-key",
        help="create one restricted development-only Ed25519 key",
    )
    init.add_argument("--reviewer-id", required=True)
    init.add_argument("--reviewer-role", choices=_ROLES, default="independent_agent")
    init.add_argument("--key-id", required=True)

    imported = operations.add_parser(
        "import-key", help="copy a raw/Base64/PEM/local-JSON key into restricted storage"
    )
    imported.add_argument("--source", type=Path, required=True)
    imported.add_argument("--reviewer-id", required=True)
    imported.add_argument("--reviewer-role", choices=_ROLES, default="independent_agent")
    imported.add_argument("--key-id", required=True)

    registered = operations.add_parser(
        "register-public-key",
        help="explicitly register an external public key as a formal trust root",
    )
    registered.add_argument("--source", type=Path, required=True)

    status = operations.add_parser("status", help="verify local key ACL and public trust root")
    status.add_argument("--key-id", required=True)

    bundle = operations.add_parser(
        "bundle", help="export canonical samples and evidence for an external reviewer agent"
    )
    bundle.add_argument("--environment-id", required=True)
    bundle.add_argument("--sample-id", action="append", required=True)
    bundle.add_argument("--output", type=Path, required=True)

    submit = operations.add_parser(
        "submit", help="locally sign and append a development-only review"
    )
    submit.add_argument("--key-id", required=True)
    submit.add_argument("--submission", type=Path, required=True)

    prepared = operations.add_parser(
        "prepare-external",
        help="prepare the exact formal review payload for an external signer",
    )
    prepared.add_argument("--key-id", required=True)
    prepared.add_argument("--submission", type=Path, required=True)
    prepared.add_argument("--output", type=Path, required=True)

    external = operations.add_parser(
        "submit-external",
        help="append an externally signed review using the pre-registered formal trust root",
    )
    external.add_argument("--submission", type=Path, required=True)
    external.add_argument("--review", type=Path, required=True)
    return parser


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    return value


def _dump(value: Any) -> None:
    # Keep machine-readable stdout ASCII-safe across Windows console code pages.
    print(json.dumps(_json_value(value), ensure_ascii=True, sort_keys=True))


def _write_model(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    path.write_text(payload + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    facility = GameObservatory(args.root)
    root = facility.store.root
    try:
        if args.operation == "init-key":
            private_path, development_path, identity = initialize_local_reviewer_key(
                root,
                reviewer_id=args.reviewer_id,
                reviewer_role=args.reviewer_role,
                key_id=args.key_id,
            )
            _dump(
                {
                    "ok": True,
                    "operation": "init-key",
                    "private_key_path": str(private_path),
                    "private_key_restricted": True,
                    "trust_scope": "development_only",
                    "counts_toward_formal_gate": False,
                    "development_registry_path": str(development_path),
                    "public_identity": identity.model_dump(mode="json"),
                }
            )
            return 0
        if args.operation == "import-key":
            private_path, development_path, identity = import_local_reviewer_key(
                root,
                source=args.source,
                reviewer_id=args.reviewer_id,
                reviewer_role=args.reviewer_role,
                key_id=args.key_id,
            )
            _dump(
                {
                    "ok": True,
                    "operation": "import-key",
                    "private_key_path": str(private_path),
                    "private_key_restricted": True,
                    "trust_scope": "development_only",
                    "counts_toward_formal_gate": False,
                    "development_registry_path": str(development_path),
                    "public_identity": identity.model_dump(mode="json"),
                }
            )
            return 0
        if args.operation == "register-public-key":
            identity = PlayerSoftSignalReviewerPublicKeyV1.model_validate_json(
                args.source.read_text(encoding="utf-8")
            )
            path = register_formal_reviewer_identity(root, identity)
            _dump(
                {
                    "ok": True,
                    "operation": "register-public-key",
                    "formal_trust_root_path": str(path),
                    "public_identity": identity.model_dump(mode="json"),
                }
            )
            return 0
        if args.operation == "status":
            _dump({"ok": True, **reviewer_status(root, key_id=args.key_id)})
            return 0

        player = AIPlayerStore(facility.store)
        if args.operation == "bundle":
            bundle = build_review_bundle(
                player,
                environment_id=args.environment_id,
                sample_ids=args.sample_id,
            )
            _write_model(args.output, bundle)
            _dump(
                {
                    "ok": True,
                    "operation": "bundle",
                    "output": str(args.output.resolve()),
                    "sample_count": len(bundle.samples),
                    "evidence_run_count": len(bundle.evidence_runs),
                    "evidence_step_count": len(bundle.evidence_steps),
                }
            )
            return 0
        submission = load_submission(args.submission)
        if args.operation == "prepare-external":
            review = prepare_external_review(player, submission, key_id=args.key_id)
            _write_model(args.output, review)
            _dump(
                {
                    "ok": True,
                    "operation": "prepare-external",
                    "output": str(args.output.resolve()),
                    "review": _json_value(review),
                }
            )
            return 0
        if args.operation == "submit-external":
            signed = PlayerSoftSignalReviewV1.model_validate_json(
                args.review.read_text(encoding="utf-8")
            )
            review = submit_external_signed_review(player, submission, signed)
            _dump({"ok": True, "operation": "submit-external", "review": _json_value(review)})
            return 0
        review = submit_signed_review(player, submission, key_id=args.key_id)
        _dump(
            {
                "ok": True,
                "operation": "submit",
                "trust_scope": "development_only",
                "counts_toward_formal_gate": False,
                "review": _json_value(review),
            }
        )
        return 0
    except (OSError, RuntimeError, ValidationError, ValueError) as error:
        _dump(
            {
                "ok": False,
                "code": type(error).__name__,
                "message": str(error),
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
