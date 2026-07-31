"""Execute one evidence-linked game action without hand-writing task/session/request JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ..models import NormalizedAction, SourcePixelRect
from ..runtime import GameObservatory
from .account_policy import AccountActionIntentV1
from .contracts import EvidenceReferenceV1
from .live_step import LiveStepExpectationV1, LiveStepRequestV1, run_live_turn
from .store import AIPlayerStore


def _stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _safe_turn_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,95}", value):
        raise ValueError("turn-id must use 3-96 lowercase letters, digits, dots, or hyphens")
    return value


def _build_action(args: argparse.Namespace) -> tuple[NormalizedAction, SourcePixelRect | None]:
    if args.tap is not None:
        if args.bounds is None:
            raise ValueError("tap quick step requires --bounds x y width height")
        return (
            NormalizedAction(type="tap", x=args.tap[0], y=args.tap[1]),
            SourcePixelRect(
                x=args.bounds[0],
                y=args.bounds[1],
                width=args.bounds[2],
                height=args.bounds[3],
            ),
        )
    if args.wait is not None:
        return NormalizedAction(type="wait", seconds=args.wait), None
    if args.launch is not None:
        return NormalizedAction(type="launch", package=args.launch), None
    raise ValueError("quick step requires one action")


def build_quick_request(args: argparse.Namespace) -> LiveStepRequestV1:
    root = Path(args.root)
    facility = GameObservatory(root)
    player = AIPlayerStore(facility.store)
    environment = player.get_environment(args.environment_id)
    if environment is None:
        raise ValueError(f"unknown AI-player environment: {args.environment_id}")
    source_step = facility.store.get_evidence_step(args.source_step_id)
    if source_step is None or source_step.status != "passed":
        raise ValueError("source evidence step must exist and be passed")
    source_run = facility.store.get_evidence_run(source_step.evidence_run_id)
    if source_run is None or source_run.status != "passed":
        raise ValueError("source evidence run must exist and be passed")
    if source_run.scope_id != environment.id:
        raise ValueError("source evidence run belongs to another environment")
    source_artifact = facility.store.get_artifact(str(source_step.after_frame_id))
    if source_artifact is None:
        raise ValueError("source evidence step has no terminal screenshot")
    if source_artifact.metadata.get("semantic_state_eligible") is not True:
        raise ValueError("source terminal screenshot is not semantic-state eligible")
    action, bounds = _build_action(args)
    turn_id = _safe_turn_id(args.turn_id)
    reference = EvidenceReferenceV1(
        environment_id=environment.id,
        artifact_ids=[source_artifact.id],
        evidence_run_ids=[source_run.id],
        evidence_step_ids=[source_step.id],
        trace_run_ids=(
            [source_step.action_run_id] if source_step.action_run_id is not None else []
        ),
        note="上一动作的终态，作为本次短回合输入。",
    )
    player.resolve_evidence_references([reference])
    return LiveStepRequestV1(
        target_id=source_run.target_id,
        environment_id=environment.id,
        session_id=f"ai-player-session.quick.{turn_id}",
        task_id=f"task.quick.{turn_id}",
        bootstrap_session=True,
        initial_evidence=reference,
        viewport_width=source_run.viewport_width,
        viewport_height=source_run.viewport_height,
        game_id=source_run.game_id,
        build_scope_id=source_run.build_scope_id,
        action=action,
        target_name=args.target_name,
        target_bounds=bounds,
        account_action_intent=AccountActionIntentV1(
            id=f"intent.quick.{turn_id}",
            category="native_game_automation",
            summary=args.expectation,
            game_internal=True,
        ),
        expectation=LiveStepExpectationV1(
            summary=args.expectation,
            kind="visual_no_change" if args.expect_no_change else "visual_change",
            min_visual_distance=args.min_visual_distance,
            stop_conditions=[
                "预期画面变化未成立。",
                "出现真实支付、外部身份、登录冲突或语义不明选择。",
                "动作运行达到60秒。",
            ],
        ),
        actor="ai-player-quick-step",
        holder="ai-player-quick-step",
        lease_ttl_seconds=120,
        max_runtime_seconds=60,
        settle_threshold=args.settle_threshold,
        required_consecutive=args.required_consecutive,
        settle_timeout_seconds=args.settle_timeout,
        sample_interval_seconds=args.sample_interval,
        capture_profile="compact_static",
    )


def main(argv: list[str] | None = None) -> int:
    _stdio_utf8()
    parser = argparse.ArgumentParser(prog="game-observatory-ai-player-quick-step")
    parser.add_argument("--root", default="data/domains/game_observatory")
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--source-step-id", required=True)
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--expectation", required=True)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--tap", type=int, nargs=2, metavar=("X", "Y"))
    actions.add_argument("--wait", type=float)
    actions.add_argument("--launch")
    parser.add_argument("--bounds", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--expect-no-change", action="store_true")
    parser.add_argument("--min-visual-distance", type=float, default=0.03)
    parser.add_argument("--settle-threshold", type=float, default=0.012)
    parser.add_argument("--required-consecutive", type=int, default=1)
    parser.add_argument("--settle-timeout", type=float, default=5.0)
    parser.add_argument("--sample-interval", type=float, default=0.4)
    args = parser.parse_args(argv)
    request = build_quick_request(args)
    result = run_live_turn(request, root=Path(args.root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())