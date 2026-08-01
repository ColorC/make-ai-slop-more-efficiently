from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .runtime import GameObservatory


def _configure_utf8_stdio() -> None:
    """Keep machine-readable CLI JSON UTF-8 even on a legacy Windows code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Test capture streams and embedded hosts may not permit reconfiguration.
            continue


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="game-observatory")
    parser.add_argument(
        "command",
        choices=[
            "bootstrap", "validate", "capture", "export", "proof", "ingest",
            "promote-afk-first-launch", "promote-afk-live-design",
            "promote-minecraft-live-design",
            "promote-voxelcraft-fire-food",
            "source-ingest", "voice-ingest", "source-retract",
            "target-refresh", "lease-acquire", "lease-release",
            "capture-stream", "install-apk", "start-package", "force-stop-package",
            "evidence-open", "evidence-step", "evidence-complete", "evidence-route",
            "emergency-stop", "emergency-stop-clear", "rate-limit", "mumu-control",
            "mumu-clone", "mumu-snapshot-export", "mumu-snapshot-import", "mumu-delete-clone",
            "mumu-multi-verify",
            "mumu-snapshot-verify",
            "storage-verify",
            "recover-target", "gateway-verify",
            "afk-preflight", "afk-benchmark",
            "minecraft-verify",
            "editorial-verify",
            "voice-verify",
            "agent-verify",
            "exploration-verify",
            "exploration-coordinate-grid",
            "exploration-visual-candidate-manifest",
            "exploration-prior-target-reference",
            "exploration-score-pair",
            "saturation-check",
            "visual-locator-setup",
            "visual-locator-benchmark",
            "visual-locator-score",
            "pc-agent-verify",
            "phase6-verify",
            "site-quality",
            "phase-proofs",
            "unity-preflight", "minecraft-observe",
            "monitor", "backup", "verify-backup", "recovery-drill",
        ],
    )
    parser.add_argument("--root", type=Path)
    parser.add_argument("--serial", default="127.0.0.1:7555")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--frame-id")
    parser.add_argument("--ui-id")
    parser.add_argument("--version", default="1.7.21")
    parser.add_argument("--spawn-shot", type=Path)
    parser.add_argument("--campfire-shot", type=Path)
    parser.add_argument("--roasted-shot", type=Path)
    parser.add_argument("--source-id")
    parser.add_argument("--reason")
    parser.add_argument("--target-id")
    parser.add_argument("--holder", default="game-observatory-cli")
    parser.add_argument("--ttl", type=int, default=300)
    parser.add_argument("--lease-token")
    parser.add_argument("--target")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--package")
    parser.add_argument("--apk", type=Path)
    parser.add_argument("--frames", type=int, default=10)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--ui-every", type=int, default=0)
    parser.add_argument("--max-recoveries", type=int, default=2)
    parser.add_argument("--actor", default="game-observatory-cli")
    parser.add_argument("--max-actions-per-minute", type=int, default=30)
    parser.add_argument("--min-action-interval-ms", type=int, default=150)
    parser.add_argument("--operation")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--snapshot-name", default="game-observatory-snapshot")
    parser.add_argument("--number", type=int, default=1)
    parser.add_argument("--uncompressed", action="store_true")
    parser.add_argument("--bridge-port", type=int, default=18820)
    parser.add_argument("--base-url", default="http://127.0.0.1:8222")
    parser.add_argument("--browser-evidence", type=Path)
    parser.add_argument("--evidence-run-id")
    parser.add_argument("--viewport-width", type=int)
    parser.add_argument("--viewport-height", type=int)
    parser.add_argument("--game-id")
    parser.add_argument("--build-scope-id")
    parser.add_argument("--scope-id")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--manual-ledger", type=Path)
    parser.add_argument("--hypothesis-ledger", type=Path)
    parser.add_argument("--adjudication", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--manual-session-id")
    parser.add_argument("--hypothesis-session-id")
    parser.add_argument("--manual-human-intervention-seconds", type=float, default=0)
    parser.add_argument("--hypothesis-human-intervention-seconds", type=float, default=0)
    parser.add_argument("--manual-elapsed-seconds", type=float, default=0)
    parser.add_argument("--hypothesis-elapsed-seconds", type=float, default=0)
    parser.add_argument(
        "--coordinate-space",
        choices=["normalized_1000", "source_pixels"],
        default="normalized_1000",
    )
    parser.add_argument("--grid-step", type=int, default=100)
    parser.add_argument("--omniparser-home", type=Path)
    parser.add_argument("--box-threshold", type=float, default=0.05)
    parser.add_argument("--locator-timeout", type=float, default=1200)
    parser.add_argument("--locator-result", type=Path, action="append")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument(
        "--grid-layout",
        choices=["full", "bands_2x2"],
        default="full",
    )
    args = parser.parse_args(argv)
    facility = GameObservatory(args.root)
    if args.command == "bootstrap":
        result = facility.bootstrap()
    elif args.command == "validate":
        result = facility.validate()
    elif args.command == "capture":
        result = facility.capture_device(args.serial)
    elif args.command == "export":
        result = {
            "ok": True,
            "canonical_path": str(facility.store.export_reports()),
            "public_build": facility.compile_public(),
        }
    elif args.command == "ingest":
        if not args.file or not args.file.is_file():
            parser.error("ingest requires --file <report.json>")
        from .models import GameReport
        report = GameReport.model_validate_json(args.file.read_text(encoding="utf-8"))
        facility.store.upsert_report(report)
        facility.store.export_reports(facility.store.list_reports())
        facility.compile_public()
        result = {
            "ok": True,
            "report_id": report.id,
            "revisions": facility.store.list_revisions(report.id),
        }
    elif args.command in {"source-ingest", "voice-ingest"}:
        if not args.file or not args.file.is_file():
            parser.error(f"{args.command} requires --file <ingest-envelope.json>")
        payload = json.loads(args.file.read_text(encoding="utf-8"))
        from .models import PlayerVoice, SourceRef
        from .source_voice import SourceVoicePipeline

        pipeline = SourceVoicePipeline(facility.store)
        source = SourceRef.model_validate(payload["source"])
        if args.command == "source-ingest":
            result = pipeline.ingest_source(
                payload["report_id"],
                source,
                excerpt=payload.get("excerpt"),
                metadata=payload.get("metadata"),
            )
        else:
            result = pipeline.ingest_player_voice(
                payload["report_id"],
                source,
                PlayerVoice.model_validate(payload["voice"]),
                excerpt=payload.get("excerpt"),
            )
    elif args.command == "source-retract":
        if not args.source_id or not args.reason:
            parser.error("source-retract requires --source-id and --reason")
        from .source_voice import SourceVoicePipeline

        result = SourceVoicePipeline(facility.store).retract_source(args.source_id, args.reason)
    elif args.command == "target-refresh":
        result = {"ok": True, "targets": facility.discover_targets(refresh=True)}
    elif args.command == "lease-acquire":
        if not args.target_id:
            parser.error("lease-acquire requires --target-id")
        lease = facility.device_gateway().acquire(args.target_id, args.holder, ttl_seconds=args.ttl)
        result = {"ok": True, "lease": lease.model_dump(mode="json")}
    elif args.command == "lease-release":
        if not args.lease_token:
            parser.error("lease-release requires --lease-token")
        lease = facility.device_gateway().release(args.lease_token)
        result = {"ok": True, "lease": lease.model_dump(mode="json")}
    elif args.command == "capture-stream":
        if not args.target_id or not args.lease_token:
            parser.error("capture-stream requires --target-id and --lease-token")
        session = facility.device_gateway().capture_stream(
            args.target_id,
            args.lease_token,
            frame_count=args.frames,
            interval_seconds=args.interval,
            include_ui_every=args.ui_every,
            max_recoveries=args.max_recoveries,
        )
        result = {"ok": session.status == "passed", "session": session.model_dump(mode="json")}
    elif args.command == "evidence-open":
        if (
            not args.target_id
            or not args.lease_token
            or not args.viewport_width
            or not args.viewport_height
        ):
            parser.error(
                "evidence-open requires --target-id, --lease-token, "
                "--viewport-width, and --viewport-height"
            )
        run = facility.device_gateway().start_evidence_run(
            args.target_id,
            args.lease_token,
            viewport_width=args.viewport_width,
            viewport_height=args.viewport_height,
            game_id=args.game_id,
            build_scope_id=args.build_scope_id,
            scope_id=args.scope_id,
            environment={"caller": "game-observatory-cli"},
        )
        result = {"ok": True, "run": run.model_dump(mode="json")}
    elif args.command == "evidence-step":
        if not args.evidence_run_id or not args.lease_token or not args.file:
            parser.error(
                "evidence-step requires --evidence-run-id, --lease-token, "
                "and --file <step.json>"
            )
        if not args.file.is_file():
            parser.error(f"evidence step file not found: {args.file}")
        from .models import (
            EvidenceDynamicSceneProfile,
            EvidenceTerminalCondition,
            NormalizedAction,
            SourcePixelRect,
        )

        payload = json.loads(args.file.read_text(encoding="utf-8"))
        step = facility.device_gateway().record_evidence_step(
            args.evidence_run_id,
            args.lease_token,
            NormalizedAction.model_validate(payload["action"]),
            target_name=payload.get("target_name"),
            target_bounds=(
                SourcePixelRect.model_validate(payload["target_bounds"])
                if payload.get("target_bounds")
                else None
            ),
            settle_threshold=float(payload.get("settle_threshold", 0.01)),
            required_consecutive=int(payload.get("required_consecutive", 2)),
            settle_timeout_seconds=float(payload.get("settle_timeout_seconds", 4.0)),
            sample_interval_seconds=float(payload.get("sample_interval_seconds", 0.25)),
            terminal_condition=(
                EvidenceTerminalCondition.model_validate(payload["terminal_condition"])
                if payload.get("terminal_condition")
                else None
            ),
            dynamic_scene_profile=(
                EvidenceDynamicSceneProfile.model_validate(
                    payload["dynamic_scene_profile"]
                )
                if payload.get("dynamic_scene_profile")
                else None
            ),
        )
        result = {"ok": step.status == "passed", "step": step.model_dump(mode="json")}
    elif args.command == "evidence-complete":
        if not args.evidence_run_id or not args.lease_token:
            parser.error("evidence-complete requires --evidence-run-id and --lease-token")
        manifest = facility.device_gateway().complete_evidence_run(
            args.evidence_run_id,
            args.lease_token,
        )
        result = {
            "ok": manifest.publishable,
            "manifest": manifest.model_dump(mode="json"),
        }
    elif args.command == "evidence-route":
        if not args.lease_token or not args.file or not args.file.is_file():
            parser.error(
                "evidence-route requires --lease-token and --file <route.json>"
            )
        from .evidence_route import EvidenceRouteRunner, load_evidence_route

        result = EvidenceRouteRunner(facility.store).run(
            load_evidence_route(args.file),
            args.lease_token,
            repetitions=args.repetitions,
        )
    elif args.command == "install-apk":
        if not args.target_id or not args.lease_token or not args.apk:
            parser.error("install-apk requires --target-id, --lease-token, and --apk")
        result = facility.device_gateway().install_apk(args.target_id, args.lease_token, args.apk)
    elif args.command in {"start-package", "force-stop-package"}:
        if not args.target_id or not args.lease_token or not args.package:
            parser.error(f"{args.command} requires --target-id, --lease-token, and --package")
        gateway = facility.device_gateway()
        result = (
            gateway.start_package(args.target_id, args.lease_token, args.package)
            if args.command == "start-package"
            else gateway.force_stop_package(args.target_id, args.lease_token, args.package)
        )
    elif args.command == "emergency-stop":
        if not args.target_id or not args.reason:
            parser.error("emergency-stop requires --target-id and --reason")
        control = facility.device_gateway().emergency_stop(
            args.target_id, reason=args.reason, actor=args.actor
        )
        result = {"ok": True, "control": control.model_dump(mode="json")}
    elif args.command == "emergency-stop-clear":
        if not args.target_id:
            parser.error("emergency-stop-clear requires --target-id")
        control = facility.device_gateway().clear_emergency_stop(
            args.target_id, actor=args.actor
        )
        result = {"ok": True, "control": control.model_dump(mode="json")}
    elif args.command == "rate-limit":
        if not args.target_id:
            parser.error("rate-limit requires --target-id")
        control = facility.device_gateway().configure_rate_limit(
            args.target_id,
            max_actions_per_minute=args.max_actions_per_minute,
            min_action_interval_ms=args.min_action_interval_ms,
            actor=args.actor,
        )
        result = {"ok": True, "control": control.model_dump(mode="json")}
    elif args.command == "mumu-control":
        if not args.target_id or not args.lease_token or not args.operation:
            parser.error("mumu-control requires --target-id, --lease-token, and --operation")
        result = facility.device_gateway().mumu_control(
            args.target_id, args.lease_token, args.operation
        )
    elif args.command == "mumu-clone":
        if not args.target_id or not args.lease_token:
            parser.error("mumu-clone requires --target-id and --lease-token")
        result = facility.device_gateway().mumu_clone(
            args.target_id, args.lease_token, number=args.number
        )
    elif args.command == "mumu-snapshot-export":
        if not args.target_id or not args.lease_token:
            parser.error("mumu-snapshot-export requires --target-id and --lease-token")
        result = facility.device_gateway().mumu_export_snapshot(
            args.target_id,
            args.lease_token,
            name=args.snapshot_name,
            compressed=not args.uncompressed,
        )
    elif args.command == "mumu-snapshot-import":
        if not args.target_id or not args.lease_token or not args.snapshot:
            parser.error(
                "mumu-snapshot-import requires --target-id, --lease-token, and --snapshot"
            )
        result = facility.device_gateway().mumu_import_snapshot(
            args.target_id,
            args.lease_token,
            args.snapshot,
            number=args.number,
        )
    elif args.command == "mumu-delete-clone":
        if not args.target_id or not args.lease_token:
            parser.error("mumu-delete-clone requires --target-id and --lease-token")
        result = facility.device_gateway().mumu_delete_clone(
            args.target_id, args.lease_token
        )
    elif args.command == "mumu-multi-verify":
        result = facility.verify_mumu_multi_instance(
            package=args.package or "com.the_companygame.demogame.android.cn"
        )
    elif args.command == "mumu-snapshot-verify":
        if not args.snapshot or not args.target_id:
            parser.error("mumu-snapshot-verify requires --snapshot and --target-id")
        result = facility.verify_mumu_snapshot_restore(
            args.snapshot,
            restored_target_id=args.target_id,
            package=args.package or "com.the_companygame.demogame.android.cn",
        )
    elif args.command == "storage-verify":
        result = facility.verify_production_storage()
    elif args.command == "recover-target":
        if not args.target_id or not args.lease_token:
            parser.error("recover-target requires --target-id and --lease-token")
        target = facility.device_gateway().recover_target(args.target_id, args.lease_token)
        result = {"ok": target.status == "online", "target": target.model_dump(mode="json")}
    elif args.command == "gateway-verify":
        result = facility.verify_device_gateway(
            args.target_id or "device://adb/127.0.0.1:7555",
            package=args.package or "com.the_companygame.demogame.android.cn",
        )
    elif args.command == "afk-preflight":
        result = facility.afk_hero_upgrade_preflight(
            args.snapshot,
            bridge_port=args.bridge_port,
        )
        result["ok"] = result["ready"]
    elif args.command == "afk-benchmark":
        if not args.snapshot:
            parser.error("afk-benchmark requires --snapshot")
        result = facility.run_afk_hero_upgrade_benchmark(
            args.snapshot,
            bridge_port=args.bridge_port,
        )
    elif args.command == "promote-afk-live-design":
        if not args.file:
            parser.error("promote-afk-live-design requires --file <manifest.json>")
        result = facility.promote_afk_live_design(args.file)
    elif args.command == "promote-minecraft-live-design":
        if not args.file:
            parser.error("promote-minecraft-live-design requires --file <manifest.json>")
        result = facility.promote_minecraft_live_design(args.file)
    elif args.command == "minecraft-verify":
        result = facility.verify_minecraft_adapters(
            console_target=args.target or "minecraft://127.0.0.1:8332"
        )
    elif args.command == "editorial-verify":
        result = facility.verify_editorial_pipeline()
    elif args.command == "voice-verify":
        result = facility.verify_player_voice_pipeline()
    elif args.command == "agent-verify":
        result = facility.verify_agent_plugins(
            args.target_id or "device://mumu/0"
        )
    elif args.command == "exploration-verify":
        result = facility.verify_exploratory_mobile_agent(
            args.target_id or "device://mumu/0"
        )
    elif args.command == "exploration-coordinate-grid":
        if not args.file or not args.file.is_file() or not args.destination:
            parser.error(
                "exploration-coordinate-grid requires --file <image> "
                "and --destination <output.png>"
            )
        from .exploration_benchmark import (
            build_banded_coordinate_reference,
            build_coordinate_reference,
        )

        if args.grid_layout == "bands_2x2":
            coordinate_reference = build_banded_coordinate_reference(
                args.file,
                args.destination,
                bands=4,
                pixel_step=args.grid_step,
            )
        else:
            coordinate_reference = build_coordinate_reference(
                args.file,
                args.destination,
                coordinate_space=args.coordinate_space,
                normalized_step=args.grid_step,
                pixel_step=args.grid_step,
            )
        result = {
            "ok": True,
            "coordinate_reference": coordinate_reference,
        }
    elif args.command == "exploration-prior-target-reference":
        if not args.fixture or not args.fixture.is_file() or not args.destination:
            parser.error(
                "exploration-prior-target-reference requires --fixture <fixture.json> "
                "and --destination <output.png>"
            )
        from .exploration_benchmark import (
            build_prior_target_reference,
            load_fixture,
        )

        result = {
            "ok": True,
            "prior_target_reference": build_prior_target_reference(
                load_fixture(args.fixture),
                args.destination,
            ),
        }
    elif args.command == "exploration-visual-candidate-manifest":
        if (
            not args.fixture
            or not args.fixture.is_file()
            or not args.locator_result
            or len(args.locator_result) != 1
            or not args.locator_result[0].is_file()
            or not args.destination
        ):
            parser.error(
                "exploration-visual-candidate-manifest requires --fixture <fixture.json>, "
                "exactly one --locator-result <result.json>, and "
                "--destination <manifest.json>"
            )
        from .exploration_benchmark import (
            load_fixture,
            write_visual_candidate_manifest,
        )

        result = {
            "ok": True,
            "visual_candidate_manifest": write_visual_candidate_manifest(
                load_fixture(args.fixture),
                args.locator_result[0],
                args.destination,
            ),
        }
    elif args.command == "exploration-score-pair":
        required = {
            "--fixture": args.fixture,
            "--manual-ledger": args.manual_ledger,
            "--hypothesis-ledger": args.hypothesis_ledger,
            "--output-dir": args.output_dir,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error("exploration-score-pair requires " + ", ".join(missing))
        from .exploration_benchmark import (
            compare_paired_scores,
            load_fixture,
            score_probe_ledger,
            write_score,
        )

        fixture = load_fixture(args.fixture)
        manual = score_probe_ledger(
            fixture,
            args.manual_ledger,
            path="manual",
            session_id=args.manual_session_id,
            human_intervention_seconds=args.manual_human_intervention_seconds,
            elapsed_seconds=args.manual_elapsed_seconds,
        )
        hypothesis = score_probe_ledger(
            fixture,
            args.hypothesis_ledger,
            path="hypothesis",
            session_id=args.hypothesis_session_id,
            adjudication_path=args.adjudication,
            human_intervention_seconds=args.hypothesis_human_intervention_seconds,
            elapsed_seconds=args.hypothesis_elapsed_seconds,
        )
        verdict = compare_paired_scores(manual, hypothesis)
        output_dir = args.output_dir.resolve()
        result = {
            "ok": True,
            "manual_score": str(write_score(output_dir / "manual-score.json", manual)),
            "hypothesis_score": str(
                write_score(output_dir / "hypothesis-score.json", hypothesis)
            ),
            "paired_verdict": str(
                write_score(output_dir / "paired-verdict.json", verdict)
            ),
            "strict_dominance": verdict.strict_dominance,
        }
    elif args.command == "saturation-check":
        if not args.file or not args.file.is_file():
            parser.error("saturation-check requires --file <ledger.json>")
        from .saturation import (
            load_saturation_ledger,
            validate_saturation_ledger,
            write_saturation_validation,
        )

        validation = validate_saturation_ledger(
            load_saturation_ledger(args.file),
            store=facility.store,
        )
        if args.destination:
            write_saturation_validation(validation, args.destination)
        result = validation.model_dump(mode="json", by_alias=True)
        if args.destination:
            result["validation_path"] = str(args.destination.resolve())
    elif args.command == "visual-locator-setup":
        from .visual_locator import OmniParserRuntime

        locator = OmniParserRuntime(
            facility.store.root,
            source_root=args.omniparser_home,
        )
        result = locator.setup(download=not args.skip_download)
    elif args.command == "visual-locator-benchmark":
        if not args.file or not args.file.is_file() or not args.destination:
            parser.error(
                "visual-locator-benchmark requires --file <image> "
                "and --destination <output-directory>"
            )
        from .visual_locator import OmniParserRuntime

        locator = OmniParserRuntime(
            facility.store.root,
            source_root=args.omniparser_home,
        )
        result = locator.locate(
            args.file,
            args.destination,
            box_threshold=args.box_threshold,
            timeout_seconds=args.locator_timeout,
        )
    elif args.command == "visual-locator-score":
        if not args.fixture or not args.locator_result or not args.destination:
            parser.error(
                "visual-locator-score requires --fixture <fixture.json>, "
                "one or more --locator-result <result.json>, and "
                "--destination <output-directory>"
            )
        from .exploration_benchmark import load_fixture
        from .visual_locator_benchmark import (
            score_locator_result,
            score_locator_series,
            write_locator_score,
        )

        fixture = load_fixture(args.fixture)
        scores = [
            score_locator_result(fixture, path) for path in args.locator_result
        ]
        output_dir = args.destination.resolve()
        run_score_paths = [
            str(write_locator_score(output_dir / f"run-{index:02d}-score.json", score))
            for index, score in enumerate(scores, 1)
        ]
        verdict = score_locator_series(scores)
        result = {
            "ok": verdict.passed,
            "run_scores": run_score_paths,
            "series_verdict": str(
                write_locator_score(output_dir / "series-verdict.json", verdict)
            ),
            "passed": verdict.passed,
        }
    elif args.command == "pc-agent-verify":
        result = facility.verify_pc_visual_agent(
            args.target or "minecraft://127.0.0.1:8332"
        )
    elif args.command == "phase6-verify":
        result = facility.verify_phase6_agent_race(
            args.target_id or "device://mumu/0"
        )
    elif args.command == "site-quality":
        result = facility.verify_public_site_quality(
            args.base_url,
            browser_evidence=args.browser_evidence,
        )
    elif args.command == "phase-proofs":
        result = facility.build_phase_proofs()
    elif args.command == "unity-preflight":
        from .game_adapters import AfkUnityExplorerAdapter

        adapter = AfkUnityExplorerAdapter(facility.store)
        target = adapter.connect(args.target or "source://unity/afk-journey?bridge_port=18820")
        result = {"ok": True, "target": target.model_dump(mode="json")}
    elif args.command == "minecraft-observe":
        from .game_adapters import MinecraftVisualAdapter

        adapter = MinecraftVisualAdapter(facility.store)
        target = adapter.connect(args.target or "minecraft://127.0.0.1:8332")
        observation = adapter.observe()
        result = {
            "ok": True,
            "target": target.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json"),
        }
    elif args.command in {"monitor", "backup", "verify-backup", "recovery-drill"}:
        from .maintenance import FacilityMaintenance

        maintenance = FacilityMaintenance(facility.store)
        if args.command == "monitor":
            result = maintenance.monitor()
        elif args.command == "backup":
            result = maintenance.backup(args.destination)
        elif args.command == "verify-backup":
            if not args.destination:
                parser.error("verify-backup requires --destination <backup-dir>")
            result = maintenance.verify_backup(args.destination)
        else:
            if not args.destination:
                parser.error("recovery-drill requires --destination <backup-dir>")
            result = maintenance.recovery_drill(args.destination)
    elif args.command == "promote-afk-first-launch":
        if not args.frame_id or not args.ui_id:
            parser.error("promote-afk-first-launch requires --frame-id and --ui-id")
        report = facility.promote_afk_first_launch(
            args.frame_id,
            args.ui_id,
            version=args.version,
        )
        result = {
            "ok": True,
            "report_id": report.id,
            "slug": report.slug,
            "artifact_ids": [item.id for item in report.artifacts],
        }
    elif args.command == "promote-voxelcraft-fire-food":
        screenshots = [args.spawn_shot, args.campfire_shot, args.roasted_shot]
        if any(path is None or not path.is_file() for path in screenshots):
            parser.error(
                "promote-voxelcraft-fire-food requires --spawn-shot, --campfire-shot, and --roasted-shot"
            )
        report = facility.promote_voxelcraft_fire_food(
            args.spawn_shot,
            args.campfire_shot,
            args.roasted_shot,
            version=args.version,
        )
        result = {
            "ok": True,
            "report_id": report.id,
            "slug": report.slug,
            "artifact_ids": [item.id for item in report.artifacts],
        }
    else:
        validation = facility.validate()
        result = {"ok": validation["ok"], "path": str(facility.proof_report(validation))}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
