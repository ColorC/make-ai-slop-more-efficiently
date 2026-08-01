from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical_graph import design_object_rows, design_relation_rows
from .models import GameReport, utc_now
from .store import ObservatoryStore


class StorageBackendError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class MinioSettings:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str = "game-observatory-evidence"
    secure: bool = False

    @classmethod
    def from_env(cls) -> MinioSettings:
        required = {
            "endpoint": os.environ.get("GAME_OBSERVATORY_MINIO_ENDPOINT", ""),
            "access_key": os.environ.get("GAME_OBSERVATORY_MINIO_ACCESS_KEY", ""),
            "secret_key": os.environ.get("GAME_OBSERVATORY_MINIO_SECRET_KEY", ""),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise StorageBackendError(f"missing MinIO settings: {', '.join(missing)}")
        return cls(
            **required,
            bucket=os.environ.get(
                "GAME_OBSERVATORY_MINIO_BUCKET", "game-observatory-evidence"
            ),
            secure=os.environ.get("GAME_OBSERVATORY_MINIO_SECURE", "0") == "1",
        )


class MinioArtifactProjection:
    def __init__(self, settings: MinioSettings) -> None:
        try:
            from minio import Minio
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise StorageBackendError("install minio==7.2.20") from exc
        self.settings = settings
        self.client = Minio(
            settings.endpoint,
            access_key=settings.access_key,
            secret_key=settings.secret_key,
            secure=settings.secure,
        )

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.settings.bucket):
            self.client.make_bucket(self.settings.bucket)

    @staticmethod
    def object_name(sha256: str, suffix: str) -> str:
        clean_suffix = suffix.lower().lstrip(".") or "bin"
        return f"sha256/{sha256[:2]}/{sha256}.{clean_suffix}"

    def sync_artifacts(self, store: ObservatoryStore) -> dict[str, Any]:
        self.ensure_bucket()
        uploaded = 0
        reused = 0
        verified = 0
        objects: list[dict[str, Any]] = []
        for artifact in store.list_artifacts():
            path = Path(artifact.path)
            if not path.is_file():
                raise StorageBackendError(f"artifact is missing: {path}")
            actual = _sha256_file(path)
            if actual != artifact.sha256:
                raise StorageBackendError(f"artifact hash mismatch: {artifact.id}")
            object_name = self.object_name(actual, path.suffix)
            try:
                stat = self.client.stat_object(self.settings.bucket, object_name)
                reused += 1
            except Exception as exc:  # noqa: BLE001 - MinIO maps missing to S3Error
                if getattr(exc, "code", None) not in {"NoSuchKey", "NoSuchObject"}:
                    raise
                content_type = artifact.media_type or mimetypes.guess_type(path.name)[0]
                self.client.fput_object(
                    self.settings.bucket,
                    object_name,
                    str(path),
                    content_type=content_type or "application/octet-stream",
                    metadata={
                        "artifact-id": artifact.id,
                        "sha256": actual,
                        "kind": artifact.kind,
                    },
                )
                stat = self.client.stat_object(self.settings.bucket, object_name)
                uploaded += 1
            response = self.client.get_object(self.settings.bucket, object_name)
            digest = hashlib.sha256()
            try:
                for chunk in response.stream(amt=8 * 1024 * 1024):
                    digest.update(chunk)
            finally:
                response.close()
                response.release_conn()
            if digest.hexdigest() != actual or stat.size != path.stat().st_size:
                raise StorageBackendError(f"MinIO roundtrip mismatch: {artifact.id}")
            verified += 1
            objects.append(
                {
                    "artifact_id": artifact.id,
                    "object": object_name,
                    "sha256": actual,
                    "bytes": stat.size,
                }
            )
        return {
            "ok": verified == len(store.list_artifacts()),
            "bucket": self.settings.bucket,
            "endpoint": self.settings.endpoint,
            "uploaded": uploaded,
            "reused": reused,
            "verified": verified,
            "objects": objects,
        }


class PostgresCanonicalProjection:
    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise StorageBackendError("PostgreSQL DSN is required")
        self.dsn = dsn

    @staticmethod
    def _object_rows(store: ObservatoryStore) -> list[tuple[str, str, str | None, dict[str, Any]]]:
        rows: dict[tuple[str, str], tuple[str, str, str | None, dict[str, Any]]] = {}

        def add(
            object_type: str,
            object_id: str,
            report_id: str | None,
            value: Any,
        ) -> None:
            if not object_id:
                raise StorageBackendError(f"{object_type} is missing a stable object id")
            body = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            key = (object_type, object_id)
            row = (object_type, object_id, report_id, body)
            existing = rows.get(key)
            if existing is not None and existing[3] != body:
                raise StorageBackendError(
                    f"canonical object collision for {object_type}:{object_id}"
                )
            rows[key] = existing or row

        reports = store.list_reports(include_drafts=True)
        for report in reports:
            add("report", report.id, report.id, report)
            add("build_scope", report.scope.id, report.id, report.scope)
            if report.game:
                add("game", report.game.id, None, report.game)
            if report.play:
                add("play", report.play.id, None, report.play)
            if report.system_concept:
                add("system_concept", report.system_concept.id, report.id, report.system_concept)
            if report.system_instance:
                add("system_instance", report.system_instance.id, report.id, report.system_instance)
            if report.resource_model:
                add("resource_model", report.resource_model.id, report.id, report.resource_model)
                for resource in report.resource_model.resources:
                    add("resource_definition", resource.id, report.id, resource)
            if report.benchmark_task:
                add("benchmark_task", report.benchmark_task.id, report.id, report.benchmark_task)
                for check in report.benchmark_task.checks:
                    add("objective_check", check.id, report.id, check)
            for source in report.sources:
                add("source", source.id, report.id, source)
            for surface in report.surfaces:
                add("surface", surface.id, report.id, surface)
                for element in surface.elements:
                    add("ui_element", element.id, report.id, element)
            for flow in report.flow:
                add("flow", flow.id, report.id, flow)
            for mechanism in report.mechanisms:
                add("mechanism", mechanism.id, report.id, mechanism)
            for relation in report.resources:
                add("resource_relation", relation.id, report.id, relation)
            for observation in report.observations:
                if hasattr(observation, "id"):
                    add("observation", observation.id, report.id, observation)
            for voice in report.player_voices:
                add("player_voice", voice.id, report.id, voice)
            claims = [*report.claims, *report.interpretations]
            if report.summary_claim:
                claims.append(report.summary_claim)
            for claim in claims:
                if hasattr(claim, "id"):
                    add("claim", claim.id, report.id, claim)
            for object_type, object_id, body in design_object_rows(report):
                add(object_type, object_id, report.id, body)
            spec = report.design_spec
            if spec:
                add("design_spec", spec.id, report.id, spec)
                statements = [
                    *spec.overview,
                    *spec.player_goals,
                    *spec.entry_and_unlock,
                    *spec.monetization_specs,
                    *spec.version_notes,
                ]
                for statement in statements:
                    add("design_statement", statement.id, report.id, statement)
                add("core_loop", spec.core_loop.id, report.id, spec.core_loop)
                for step in spec.core_loop.steps:
                    add("core_loop_step", step.id, report.id, step)
                add(
                    "information_architecture",
                    spec.information_architecture.id,
                    report.id,
                    spec.information_architecture,
                )
                for edge in spec.information_architecture.edges:
                    add("navigation_edge", edge.id, report.id, edge)
                for design_artifact in spec.design_artifacts:
                    add("design_artifact", design_artifact.id, report.id, design_artifact)
                for layout in spec.layout_specs:
                    add("layout_spec", layout.id, report.id, layout)
                    for element in layout.elements:
                        add("layout_element", element.id, report.id, element)
                for interaction in spec.interaction_specs:
                    add("interaction_spec", interaction.id, report.id, interaction)
                    for step in interaction.steps:
                        add("interaction_step", step.id, report.id, step)
                for matrix in spec.state_matrices:
                    add("state_matrix", matrix.id, report.id, matrix)
                    for case in matrix.cases:
                        add("state_case", case.id, report.id, case)
                for progression in spec.progression_specs:
                    add("progression_spec", progression.id, report.id, progression)
                    for axis in progression.axes:
                        add("progression_axis", axis.id, report.id, axis)
                for balance in spec.balance_specs:
                    add("balance_spec", balance.id, report.id, balance)
                    for parameter in balance.parameters:
                        add("balance_parameter", parameter.id, report.id, parameter)
                for feedback in spec.feedback_specs:
                    add("feedback_spec", feedback.id, report.id, feedback)
                for tutorial in spec.tutorial_specs:
                    add("tutorial_spec", tutorial.id, report.id, tutorial)
                    for step in tutorial.steps:
                        add("tutorial_step", step.id, report.id, step)
                for failure in spec.failure_recovery_specs:
                    add("failure_recovery_spec", failure.id, report.id, failure)
                for dependency in spec.dependency_specs:
                    add("dependency_spec", dependency.id, report.id, dependency)
                for coverage in spec.section_coverage:
                    coverage_id = f"{spec.id}:section:{coverage.section}"
                    add(
                        "design_section_coverage",
                        coverage_id,
                        report.id,
                        {"id": coverage_id, **coverage.model_dump(mode="json")},
                    )
            for tag in report.tags:
                add("tag", tag, None, {"id": tag, "label": tag})
        for artifact in store.list_artifacts():
            add("artifact", artifact.id, None, artifact)
        for run in store.list_runs(100_000):
            add("run", run.id, None, run)
        for snapshot in store.list_source_snapshots():
            add("source_snapshot", snapshot.id, None, snapshot)
        for voice in store.list_voice_records():
            add("voice_record", voice.id, voice.report_id, voice)
        for target in store.list_targets():
            add("target", target.id, None, target)
        for session in store.list_capture_sessions(limit=100_000):
            add("capture_session", session.id, None, session)
        for patch in store.list_report_patches():
            add("report_patch", patch.id, patch.report_id, patch)
        for annotation in store.list_report_annotations():
            add("report_annotation", annotation.id, annotation.report_id, annotation)
        for event in store.list_trace_events():
            add("trace_event", str(event["id"]), None, event)
        return [rows[key] for key in sorted(rows)]

    @staticmethod
    def _relations(store: ObservatoryStore) -> list[tuple[str, str, str, str, str, dict[str, Any]]]:
        values: set[tuple[str, str, str, str, str, str]] = set()

        def relate(src_type: str, src_id: str, relation: str, dst_type: str, dst_id: str) -> None:
            if dst_id:
                values.add((src_type, src_id, relation, dst_type, dst_id, "{}"))

        def provenance(
            object_type: str,
            object_id: str,
            value: Any,
        ) -> None:
            for source_id in getattr(value, "source_ids", []):
                relate(object_type, object_id, "provenance", "source", source_id)
            for artifact_id in getattr(value, "artifact_ids", []):
                relate(object_type, object_id, "evidenced_by", "artifact", artifact_id)
            run_id = getattr(value, "run_id", None)
            if run_id:
                relate(object_type, object_id, "observed_in", "run", run_id)

        for report in store.list_reports(include_drafts=True):
            relate("report", report.id, "describes_game", "game", report.game.id if report.game else "")
            relate("report", report.id, "has_scope", "build_scope", report.scope.id)
            provenance("build_scope", report.scope.id, report.scope)
            if report.system_concept:
                relate("report", report.id, "has_system_concept", "system_concept", report.system_concept.id)
                provenance("system_concept", report.system_concept.id, report.system_concept)
            if report.system_instance:
                relate("report", report.id, "has_system_instance", "system_instance", report.system_instance.id)
                relate(
                    "system_instance",
                    report.system_instance.id,
                    "instance_of",
                    "system_concept",
                    report.system_instance.concept_id,
                )
                relate(
                    "system_instance",
                    report.system_instance.id,
                    "scoped_by",
                    "build_scope",
                    report.system_instance.build_scope_id,
                )
                for surface_id in report.system_instance.surface_ids:
                    relate("system_instance", report.system_instance.id, "has_surface", "surface", surface_id)
                provenance("system_instance", report.system_instance.id, report.system_instance)
                for run_id in report.system_instance.run_ids:
                    relate("system_instance", report.system_instance.id, "observed_in", "run", run_id)
            if report.resource_model:
                relate("report", report.id, "has_resource_model", "resource_model", report.resource_model.id)
                provenance("resource_model", report.resource_model.id, report.resource_model)
                for resource in report.resource_model.resources:
                    relate(
                        "resource_model",
                        report.resource_model.id,
                        "defines_resource",
                        "resource_definition",
                        resource.id,
                    )
                    provenance("resource_definition", resource.id, resource)
                for relation_id in report.resource_model.relation_ids:
                    relate(
                        "resource_model",
                        report.resource_model.id,
                        "has_relation",
                        "resource_relation",
                        relation_id,
                    )
            if report.benchmark_task:
                relate("report", report.id, "has_benchmark_task", "benchmark_task", report.benchmark_task.id)
                for check in report.benchmark_task.checks:
                    relate(
                        "benchmark_task",
                        report.benchmark_task.id,
                        "has_objective_check",
                        "objective_check",
                        check.id,
                    )
            for source in report.sources:
                relate("report", report.id, "has_source", "source", source.id)
            for artifact in report.artifacts:
                relate("report", report.id, "has_artifact", "artifact", artifact.id)
            for run in report.runs:
                relate("report", report.id, "has_run", "run", run.id)
            for tag in report.tags:
                relate("report", report.id, "tagged", "tag", tag)
            for surface in report.surfaces:
                relate("report", report.id, "has_surface", "surface", surface.id)
                provenance("surface", surface.id, surface)
                for element in surface.elements:
                    relate("surface", surface.id, "has_ui_element", "ui_element", element.id)
                    provenance("ui_element", element.id, element)
                    if element.parent_id:
                        relate("ui_element", element.id, "child_of", "ui_element", element.parent_id)
            for flow in report.flow:
                relate("report", report.id, "has_flow", "flow", flow.id)
                provenance("flow", flow.id, flow)
                for next_id in flow.next:
                    relate("flow", flow.id, "next", "flow", next_id)
            for mechanism in report.mechanisms:
                relate("report", report.id, "has_mechanism", "mechanism", mechanism.id)
                provenance("mechanism", mechanism.id, mechanism)
            for resource in report.resources:
                relate("report", report.id, "has_resource_relation", "resource_relation", resource.id)
                provenance("resource_relation", resource.id, resource)
                if resource.from_resource_id:
                    relate(
                        "resource_relation",
                        resource.id,
                        "from",
                        "resource_definition",
                        resource.from_resource_id,
                    )
                if resource.to_resource_id:
                    relate(
                        "resource_relation",
                        resource.id,
                        "to",
                        "resource_definition",
                        resource.to_resource_id,
                    )
            for observation in report.observations:
                if hasattr(observation, "id"):
                    relate("report", report.id, "has_observation", "observation", observation.id)
                    provenance("observation", observation.id, observation)
            for voice in report.player_voices:
                relate("report", report.id, "has_player_voice", "player_voice", voice.id)
                relate("player_voice", voice.id, "provenance", "source", voice.source_id)
                if voice.system_node_id:
                    relate("player_voice", voice.id, "about", "flow", voice.system_node_id)
            claims = [*report.claims, *report.interpretations]
            if report.summary_claim:
                claims.append(report.summary_claim)
            for claim in claims:
                if hasattr(claim, "id"):
                    relate("report", report.id, "has_claim", "claim", claim.id)
                    provenance("claim", claim.id, claim)
                    if claim.flow_node_id:
                        relate("claim", claim.id, "about", "flow", claim.flow_node_id)
            for src_type, src_id, relation, dst_type, dst_id in design_relation_rows(
                report
            ):
                relate(src_type, src_id, relation, dst_type, dst_id)
            spec = report.design_spec
            if spec:
                relate("report", report.id, "has_design_spec", "design_spec", spec.id)
                relate("design_spec", spec.id, "scoped_by", "build_scope", spec.scope_id)
                relate(
                    "design_spec",
                    spec.id,
                    "describes_instance",
                    "system_instance",
                    spec.system_instance_id,
                )
                for source_id in spec.source_ids:
                    relate("design_spec", spec.id, "provenance", "source", source_id)
                for artifact_id in spec.artifact_ids:
                    relate("design_spec", spec.id, "evidenced_by", "artifact", artifact_id)
                for run_id in spec.run_ids:
                    relate("design_spec", spec.id, "observed_in", "run", run_id)

                statement_groups = {
                    "has_overview": spec.overview,
                    "has_player_goal": spec.player_goals,
                    "has_entry_unlock": spec.entry_and_unlock,
                    "has_monetization_note": spec.monetization_specs,
                    "has_version_note": spec.version_notes,
                }
                for relation_name, statements in statement_groups.items():
                    for statement in statements:
                        relate(
                            "design_spec",
                            spec.id,
                            relation_name,
                            "design_statement",
                            statement.id,
                        )
                        provenance("design_statement", statement.id, statement)

                relate("design_spec", spec.id, "has_core_loop", "core_loop", spec.core_loop.id)
                for step in spec.core_loop.steps:
                    relate("core_loop", spec.core_loop.id, "has_step", "core_loop_step", step.id)
                    provenance("core_loop_step", step.id, step)
                    for flow_id in step.flow_node_ids:
                        relate("core_loop_step", step.id, "realized_by", "flow", flow_id)

                architecture = spec.information_architecture
                relate(
                    "design_spec",
                    spec.id,
                    "has_information_architecture",
                    "information_architecture",
                    architecture.id,
                )
                for surface_id in architecture.surface_ids:
                    relate(
                        "information_architecture",
                        architecture.id,
                        "contains_surface",
                        "surface",
                        surface_id,
                    )
                for root_id in architecture.root_surface_ids:
                    relate(
                        "information_architecture",
                        architecture.id,
                        "has_root",
                        "surface",
                        root_id,
                    )
                for edge in architecture.edges:
                    relate(
                        "information_architecture",
                        architecture.id,
                        "has_navigation_edge",
                        "navigation_edge",
                        edge.id,
                    )
                    relate("navigation_edge", edge.id, "from", "surface", edge.from_surface_id)
                    relate("navigation_edge", edge.id, "to", "surface", edge.to_surface_id)
                    for flow_id in edge.flow_node_ids:
                        relate("navigation_edge", edge.id, "realized_by", "flow", flow_id)

                for design_artifact in spec.design_artifacts:
                    relate(
                        "design_spec",
                        spec.id,
                        "has_design_artifact",
                        "design_artifact",
                        design_artifact.id,
                    )
                    relate(
                        "design_artifact",
                        design_artifact.id,
                        "materialized_as",
                        "artifact",
                        design_artifact.artifact_id,
                    )
                    for input_id in design_artifact.derived_from_artifact_ids:
                        relate(
                            "design_artifact",
                            design_artifact.id,
                            "derived_from",
                            "artifact",
                            input_id,
                        )
                    for surface_id in design_artifact.surface_ids:
                        relate(
                            "design_artifact",
                            design_artifact.id,
                            "depicts",
                            "surface",
                            surface_id,
                        )
                    for flow_id in design_artifact.flow_node_ids:
                        relate(
                            "design_artifact",
                            design_artifact.id,
                            "depicts",
                            "flow",
                            flow_id,
                        )
                    for source_id in design_artifact.source_ids:
                        relate(
                            "design_artifact",
                            design_artifact.id,
                            "provenance",
                            "source",
                            source_id,
                        )
                    if design_artifact.run_id:
                        relate(
                            "design_artifact",
                            design_artifact.id,
                            "observed_in",
                            "run",
                            design_artifact.run_id,
                        )

                for layout in spec.layout_specs:
                    relate("design_spec", spec.id, "has_layout", "layout_spec", layout.id)
                    relate("layout_spec", layout.id, "describes", "surface", layout.surface_id)
                    provenance("layout_spec", layout.id, layout)
                    for element in layout.elements:
                        relate(
                            "layout_spec",
                            layout.id,
                            "has_layout_element",
                            "layout_element",
                            element.id,
                        )
                        relate(
                            "layout_element",
                            element.id,
                            "describes",
                            "ui_element",
                            element.ui_element_id,
                        )

                for interaction in spec.interaction_specs:
                    relate(
                        "design_spec",
                        spec.id,
                        "has_interaction",
                        "interaction_spec",
                        interaction.id,
                    )
                    relate(
                        "interaction_spec",
                        interaction.id,
                        "diagrammed_by",
                        "artifact",
                        interaction.diagram_artifact_id,
                    )
                    provenance("interaction_spec", interaction.id, interaction)
                    for failure_id in interaction.failure_recovery_ids:
                        relate(
                            "interaction_spec",
                            interaction.id,
                            "recovers_with",
                            "failure_recovery_spec",
                            failure_id,
                        )
                    for step in interaction.steps:
                        relate(
                            "interaction_spec",
                            interaction.id,
                            "has_step",
                            "interaction_step",
                            step.id,
                        )
                        provenance("interaction_step", step.id, step)
                        if step.surface_id:
                            relate("interaction_step", step.id, "on", "surface", step.surface_id)
                        if step.ui_element_id:
                            relate(
                                "interaction_step",
                                step.id,
                                "uses",
                                "ui_element",
                                step.ui_element_id,
                            )
                        if step.flow_node_id:
                            relate("interaction_step", step.id, "realized_by", "flow", step.flow_node_id)

                for matrix in spec.state_matrices:
                    relate("design_spec", spec.id, "has_state_matrix", "state_matrix", matrix.id)
                    relate("state_matrix", matrix.id, "about", "design_object", matrix.subject_id)
                    for case in matrix.cases:
                        relate("state_matrix", matrix.id, "has_case", "state_case", case.id)
                        provenance("state_case", case.id, case)

                for progression in spec.progression_specs:
                    relate(
                        "design_spec",
                        spec.id,
                        "has_progression",
                        "progression_spec",
                        progression.id,
                    )
                    provenance("progression_spec", progression.id, progression)
                    for axis in progression.axes:
                        relate(
                            "progression_spec",
                            progression.id,
                            "has_axis",
                            "progression_axis",
                            axis.id,
                        )
                for balance in spec.balance_specs:
                    relate("design_spec", spec.id, "has_balance", "balance_spec", balance.id)
                    for parameter in balance.parameters:
                        relate(
                            "balance_spec",
                            balance.id,
                            "has_parameter",
                            "balance_parameter",
                            parameter.id,
                        )
                        for source_id in parameter.source_ids:
                            relate(
                                "balance_parameter",
                                parameter.id,
                                "provenance",
                                "source",
                                source_id,
                            )
                    for mechanism_id in balance.mechanism_ids:
                        relate(
                            "balance_spec",
                            balance.id,
                            "expressed_by",
                            "mechanism",
                            mechanism_id,
                        )
                    for artifact_id in balance.table_artifact_ids:
                        relate(
                            "balance_spec",
                            balance.id,
                            "evidenced_by",
                            "artifact",
                            artifact_id,
                        )

                for feedback in spec.feedback_specs:
                    relate("design_spec", spec.id, "has_feedback", "feedback_spec", feedback.id)
                    provenance("feedback_spec", feedback.id, feedback)
                    for surface_id in feedback.surface_ids:
                        relate("feedback_spec", feedback.id, "on", "surface", surface_id)
                    for element_id in feedback.ui_element_ids:
                        relate("feedback_spec", feedback.id, "uses", "ui_element", element_id)
                for tutorial in spec.tutorial_specs:
                    relate("design_spec", spec.id, "has_tutorial", "tutorial_spec", tutorial.id)
                    provenance("tutorial_spec", tutorial.id, tutorial)
                    for step in tutorial.steps:
                        relate("tutorial_spec", tutorial.id, "has_step", "tutorial_step", step.id)
                        for flow_id in step.flow_node_ids:
                            relate("tutorial_step", step.id, "realized_by", "flow", flow_id)
                for failure in spec.failure_recovery_specs:
                    relate(
                        "design_spec",
                        spec.id,
                        "has_failure_recovery",
                        "failure_recovery_spec",
                        failure.id,
                    )
                    provenance("failure_recovery_spec", failure.id, failure)
                    for flow_id in failure.flow_node_ids:
                        relate(
                            "failure_recovery_spec",
                            failure.id,
                            "occurs_on",
                            "flow",
                            flow_id,
                        )
                for dependency in spec.dependency_specs:
                    relate(
                        "design_spec",
                        spec.id,
                        "has_dependency",
                        "dependency_spec",
                        dependency.id,
                    )
                    relate(
                        "dependency_spec",
                        dependency.id,
                        "targets",
                        "system_concept",
                        dependency.target_system_id,
                    )
                    provenance("dependency_spec", dependency.id, dependency)

                for mechanism_id in spec.mechanism_ids:
                    relate("design_spec", spec.id, "uses_mechanism", "mechanism", mechanism_id)
                if spec.resource_model_id:
                    relate(
                        "design_spec",
                        spec.id,
                        "uses_resource_model",
                        "resource_model",
                        spec.resource_model_id,
                    )
                for relation_id in spec.resource_relation_ids:
                    relate(
                        "design_spec",
                        spec.id,
                        "uses_resource_relation",
                        "resource_relation",
                        relation_id,
                    )
                for voice_id in spec.player_voice_ids:
                    relate("design_spec", spec.id, "uses_player_voice", "player_voice", voice_id)
                for coverage in spec.section_coverage:
                    coverage_id = f"{spec.id}:section:{coverage.section}"
                    relate(
                        "design_spec",
                        spec.id,
                        "has_section_coverage",
                        "design_section_coverage",
                        coverage_id,
                    )
                    for object_id in coverage.object_ids:
                        relate(
                            "design_section_coverage",
                            coverage_id,
                            "covers",
                            "design_object",
                            object_id,
                        )
            for voice in report.player_voices:
                for target_id in voice.target_object_ids:
                    relate("player_voice", voice.id, "about", "design_object", target_id)
        return [(*item[:5], json.loads(item[5])) for item in sorted(values)]

    def rebuild(self, store: ObservatoryStore) -> dict[str, Any]:
        try:
            import psycopg
            from psycopg.types.json import Jsonb
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise StorageBackendError("install psycopg[binary]==3.3.4") from exc
        objects = self._object_rows(store)
        relations = self._relations(store)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS game_observatory_objects(
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    report_id TEXT NULL,
                    body JSONB NOT NULL,
                    body_sha256 TEXT NOT NULL,
                    projected_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY(object_type, object_id)
                );
                CREATE INDEX IF NOT EXISTS idx_game_observatory_objects_report
                    ON game_observatory_objects(report_id);
                CREATE INDEX IF NOT EXISTS idx_game_observatory_objects_body
                    ON game_observatory_objects USING GIN(body);
                CREATE TABLE IF NOT EXISTS game_observatory_relations(
                    src_type TEXT NOT NULL,
                    src_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    dst_type TEXT NOT NULL,
                    dst_id TEXT NOT NULL,
                    body JSONB NOT NULL,
                    PRIMARY KEY(src_type, src_id, relation, dst_type, dst_id)
                );
                """
            )
            cur.execute("DELETE FROM game_observatory_relations")
            cur.execute("DELETE FROM game_observatory_objects")
            projected_at = utc_now()
            cur.executemany(
                """INSERT INTO game_observatory_objects(
                       object_type,object_id,report_id,body,body_sha256,projected_at
                   ) VALUES(%s,%s,%s,%s,%s,%s)""",
                [
                    (
                        object_type,
                        object_id,
                        report_id,
                        Jsonb(body),
                        hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest(),
                        projected_at,
                    )
                    for object_type, object_id, report_id, body in objects
                ],
            )
            cur.executemany(
                """INSERT INTO game_observatory_relations(
                       src_type,src_id,relation,dst_type,dst_id,body
                   ) VALUES(%s,%s,%s,%s,%s,%s)""",
                [(*item[:5], Jsonb(item[5])) for item in relations],
            )
            cur.execute(
                "SELECT object_id,body,pg_typeof(body)::text FROM game_observatory_objects "
                "WHERE object_type='report' ORDER BY object_id"
            )
            reports = cur.fetchall()
            for _object_id, body, pg_type in reports:
                if pg_type != "jsonb":
                    raise StorageBackendError("PostgreSQL projection did not preserve JSONB")
                GameReport.model_validate(body)
            cur.execute(
                "SELECT object_type,count(*) FROM game_observatory_objects "
                "GROUP BY object_type ORDER BY object_type"
            )
            counts = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute("SELECT count(*) FROM game_observatory_relations")
            relation_count = int(cur.fetchone()[0])
        return {
            "ok": counts.get("report") == len(store.list_reports(include_drafts=True)),
            "objects": sum(counts.values()),
            "object_counts": counts,
            "relations": relation_count,
            "reports_roundtripped": len(reports),
            "jsonb": True,
        }
