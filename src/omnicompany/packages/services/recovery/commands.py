# [OMNI] origin=claude-code created_by=omni-recover intent=expose-safe-recovery-commands
"""Portable command surface layered onto the compatibility CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from .evidence import plan_recovery, read_plan, write_plan
from .evidence_index import collect_evidence, load_index, write_index
from .providers import default_registry
from .runtime import (
    apply_plan,
    create_preapply_snapshot,
    rollback,
    run_probe_spec,
    verify_preapply_snapshot,
)


def _print(payload: object) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _source(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("source must be PROVIDER=PATH")
    provider, raw_path = value.split("=", 1)
    if not provider.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("source must be PROVIDER=PATH")
    return provider.strip().lower(), Path(raw_path).expanduser()


def _session(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("session must be PROVIDER=SESSION_ID")
    provider, session_id = value.split("=", 1)
    if not provider.strip() or not session_id.strip():
        raise argparse.ArgumentTypeError("session must be PROVIDER=SESSION_ID")
    return provider.strip().lower(), session_id.strip()


def index_build(args: argparse.Namespace) -> int:
    registry = default_registry(config_dirs=args.config_dir)
    session_filters: dict[str, set[str]] = {}
    for provider, session_id in args.session:
        session_filters.setdefault(provider, set()).add(session_id)
    records = collect_evidence(
        registry, args.source,
        session_filters=session_filters or None,
        path_prefixes=args.path_prefix,
    )
    manifest = write_index(records, args.out.resolve(strict=False))
    return _print({"mode": "index-build", "out": str(args.out.resolve()), **manifest})


def plan_command(args: argparse.Namespace) -> int:
    records = load_index(args.index.resolve(strict=False))
    plan = plan_recovery(records, args.workspace_root.resolve(strict=False))
    write_plan(plan, args.out.resolve(strict=False))
    counts: dict[str, int] = {}
    for action in plan.actions:
        counts[action.decision] = counts.get(action.decision, 0) + 1
    return _print({
        "mode": "plan", "out": str(args.out.resolve()), "plan_sha256": plan.plan_sha256,
        "actions": len(plan.actions), "decision_counts": counts,
    })


def snapshot_create_command(args: argparse.Namespace) -> int:
    plan = read_plan(args.plan.resolve(strict=False))
    payload = create_preapply_snapshot(plan, args.out.resolve(strict=False))
    return _print({
        "mode": "snapshot-create", "out": str(args.out.resolve()),
        "snapshot_sha256": payload["snapshot_sha256"], "records": len(payload["records"]),
    })


def snapshot_verify_command(args: argparse.Namespace) -> int:
    plan = read_plan(args.plan.resolve(strict=False))
    payload = verify_preapply_snapshot(
        plan, args.snapshot.resolve(strict=False), verify_workspace_guards=not args.skip_workspace_guards
    )
    return _print({
        "mode": "snapshot-verify", "ok": True,
        "snapshot_sha256": payload["snapshot_sha256"], "records": len(payload["records"]),
    })


def apply_command(args: argparse.Namespace) -> int:
    plan = read_plan(args.plan.resolve(strict=False))
    allowed = [action for action in plan.actions if action.decision in {
        "restore-missing-mutation", "restore-missing-read-fallback", "quarantine-exact-artifact"
    }]
    if not args.apply:
        return _print({
            "mode": "dry-run", "production_files_written": False,
            "plan_sha256": plan.plan_sha256, "eligible_actions": len(allowed),
        })
    result = apply_plan(
        plan,
        snapshot_dir=args.snapshot.resolve(strict=False),
        confirm_workspace=args.confirm_workspace,
        artifact_dir=args.artifact_dir.resolve(strict=False) if args.artifact_dir else None,
    )
    return _print({"mode": "apply", **result})


def rollback_command(args: argparse.Namespace) -> int:
    result = rollback(
        args.journal.resolve(strict=False),
        confirm_workspace=args.confirm_workspace,
        confirm_artifact_root=args.confirm_artifact_root,
        dry_run=not args.apply,
    )
    return _print(result)


def _load_spec(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("probe spec must be an object")
    return payload


def probe_command(args: argparse.Namespace) -> int:
    result = run_probe_spec(args.workspace_root, _load_spec(args.spec))
    _print(result)
    return 0 if result["passed"] else 2


def provider_doctor(args: argparse.Namespace) -> int:
    registry = default_registry(config_dirs=args.config_dir)
    definitions = registry.definitions
    adapters = registry.adapters
    return _print({
        "schema_version": 1,
        "providers": [
            {
                "name": name,
                "origin": definition.origin,
                "roots": list(definition.roots),
                "has_adapter": name in adapters,
            }
            for name, definition in sorted(definitions.items())
        ],
        "adapter_only": sorted(set(adapters) - set(definitions)),
    })


def add_portable_parsers(sub: argparse._SubParsersAction) -> None:
    providers = sub.add_parser("providers", help="Inspect provider manifests and parser plugins.")
    providers.add_argument("--config-dir", action="append", type=Path, default=[])
    providers.set_defaults(func=provider_doctor)

    index = sub.add_parser("index", help="Normalize provider evidence into a content-addressed index.")
    index_sub = index.add_subparsers(dest="index_command", required=True)
    build = index_sub.add_parser("build")
    build.add_argument("--source", action="append", type=_source, required=True)
    build.add_argument("--config-dir", action="append", type=Path, default=[])
    build.add_argument("--session", action="append", type=_session, default=[],
                       help="Provider-qualified session allow-list; repeatable.")
    build.add_argument("--path-prefix", action="append", type=Path, default=[],
                       help="Resolved workspace path allow-list; repeatable.")
    build.add_argument("--out", type=Path, required=True)
    build.set_defaults(func=index_build)

    plan = sub.add_parser("plan", help="Freeze byte candidates without allowing Reads to supersede mutations.")
    plan.add_argument("--index", type=Path, required=True)
    plan.add_argument("--workspace-root", type=Path, required=True)
    plan.add_argument("--out", type=Path, required=True)
    plan.set_defaults(func=plan_command)

    snapshot = sub.add_parser("snapshot", help="Create or verify a pre-apply workspace snapshot.")
    snapshot_sub = snapshot.add_subparsers(dest="snapshot_command", required=True)
    create = snapshot_sub.add_parser("create")
    create.add_argument("--plan", type=Path, required=True)
    create.add_argument("--out", type=Path, required=True)
    create.set_defaults(func=snapshot_create_command)
    verify = snapshot_sub.add_parser("verify")
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--snapshot", type=Path, required=True)
    verify.add_argument("--skip-workspace-guards", action="store_true")
    verify.set_defaults(func=snapshot_verify_command)

    apply = sub.add_parser("apply", help="Apply only frozen missing-file/artifact actions.")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--snapshot", type=Path, required=True)
    apply.add_argument("--confirm-workspace", default="")
    apply.add_argument("--artifact-dir", type=Path)
    apply.add_argument("--apply", action="store_true")
    apply.set_defaults(func=apply_command)

    rollback_parser = sub.add_parser("rollback", help="Reverse journaled missing-file writes with after-hash guards.")
    rollback_parser.add_argument("--journal", type=Path, required=True)
    rollback_parser.add_argument("--confirm-workspace", required=True)
    rollback_parser.add_argument("--confirm-artifact-root")
    rollback_parser.add_argument("--apply", action="store_true")
    rollback_parser.set_defaults(func=rollback_command)

    probe = sub.add_parser("probe", help="Run a versioned project-owned byte/build probe specification.")
    probe.add_argument("--workspace-root", type=Path, required=True)
    probe.add_argument("--spec", type=Path, required=True)
    probe.set_defaults(func=probe_command)
