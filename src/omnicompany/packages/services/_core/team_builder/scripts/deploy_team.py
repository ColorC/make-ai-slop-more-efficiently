# [OMNI] origin=codex domain=services/_core/team_builder/scripts ts=2026-07-25T00:00:00Z type=tool
# [OMNI] summary="Deploy a reviewed Team Builder registration plan without model reruns"
# [OMNI] why="Replace the legacy text-to-LLM-to-deploy loop and unsafe whole-file rollback"
"""Deploy one reviewed Team Builder registration plan.

This command never invokes Team Builder, an LLM, an external Agent, or a Team
node.  It validates the captured plan, writes the package, registers the
TeamEntry, performs import/build smoke checks, and optionally binds a Project.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from omnicompany.packages.services._core.team_builder.registration_plan_deployer import (
    deploy_registration_plan,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registration-plan", required=True, type=Path)
    parser.add_argument("--project-id")
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--receipt", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan = json.loads(args.registration_plan.read_text(encoding="utf-8"))
    result = deploy_registration_plan(
        plan,
        approved=args.approved,
        project_id=args.project_id,
        check_only=args.check_only,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
