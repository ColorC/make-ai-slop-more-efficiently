from __future__ import annotations

from typing import Any

from .models import GameReport

DesignObjectRow = tuple[str, str, dict[str, Any]]
DesignRelationRow = tuple[str, str, str, str, str]


def design_object_rows(report: GameReport) -> list[DesignObjectRow]:
    """Flatten a v0.3 design spec into stable, independently queryable objects."""

    spec = report.design_spec
    if spec is None:
        return []
    rows: dict[tuple[str, str], DesignObjectRow] = {}

    def add(object_type: str, value: Any, *, object_id: str | None = None) -> None:
        body = value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
        stable_id = object_id or body.get("id")
        if not stable_id:
            raise ValueError(f"{object_type} is missing a stable object id")
        key = (object_type, stable_id)
        row = (object_type, stable_id, body)
        existing = rows.get(key)
        if existing is not None and existing[2] != body:
            raise ValueError(f"design object collision: {object_type}:{stable_id}")
        rows[key] = existing or row

    add("design_spec", spec)
    for statement in [
        *spec.overview,
        *spec.player_goals,
        *spec.entry_and_unlock,
        *spec.monetization_specs,
        *spec.version_notes,
    ]:
        add("design_statement", statement)
    add("core_loop", spec.core_loop)
    for step in spec.core_loop.steps:
        add("core_loop_step", step)
    add("information_architecture", spec.information_architecture)
    for edge in spec.information_architecture.edges:
        add("navigation_edge", edge)
    for artifact in spec.design_artifacts:
        add("design_artifact", artifact)
    for layout in spec.layout_specs:
        add("layout_spec", layout)
        for element in layout.elements:
            add("layout_element", element)
    for interaction in spec.interaction_specs:
        add("interaction_spec", interaction)
        for step in interaction.steps:
            add("interaction_step", step)
    for matrix in spec.state_matrices:
        add("state_matrix", matrix)
        for case in matrix.cases:
            add("state_case", case)
    for progression in spec.progression_specs:
        add("progression_spec", progression)
        for axis in progression.axes:
            add("progression_axis", axis)
    for balance in spec.balance_specs:
        add("balance_spec", balance)
        for parameter in balance.parameters:
            add("balance_parameter", parameter)
    for feedback in spec.feedback_specs:
        add("feedback_spec", feedback)
    for tutorial in spec.tutorial_specs:
        add("tutorial_spec", tutorial)
        for step in tutorial.steps:
            add("tutorial_step", step)
    for failure in spec.failure_recovery_specs:
        add("failure_recovery_spec", failure)
    for dependency in spec.dependency_specs:
        add("dependency_spec", dependency)
    for coverage in spec.section_coverage:
        coverage_id = f"{spec.id}:section:{coverage.section}"
        add(
            "design_section_coverage",
            {"id": coverage_id, **coverage.model_dump(mode="json")},
            object_id=coverage_id,
        )
    return [rows[key] for key in sorted(rows)]


def design_relation_rows(report: GameReport) -> list[DesignRelationRow]:
    """Build the local canonical graph for v0.3 object navigation."""

    spec = report.design_spec
    if spec is None:
        return []
    rows: set[DesignRelationRow] = set()

    def relate(src_type: str, src_id: str, relation: str, dst_type: str, dst_id: str) -> None:
        if src_id and dst_id:
            rows.add((src_type, src_id, relation, dst_type, dst_id))

    relate("report", report.id, "has_design_spec", "design_spec", spec.id)
    relate("design_spec", spec.id, "scoped_by", "build_scope", spec.scope_id)
    relate(
        "design_spec",
        spec.id,
        "describes_instance",
        "system_instance",
        spec.system_instance_id,
    )
    statement_groups = {
        "has_overview": spec.overview,
        "has_player_goal": spec.player_goals,
        "has_entry_unlock": spec.entry_and_unlock,
        "has_monetization_note": spec.monetization_specs,
        "has_version_note": spec.version_notes,
    }
    for relation, statements in statement_groups.items():
        for statement in statements:
            relate("design_spec", spec.id, relation, "design_statement", statement.id)
    relate("design_spec", spec.id, "has_core_loop", "core_loop", spec.core_loop.id)
    for step in spec.core_loop.steps:
        relate("core_loop", spec.core_loop.id, "has_step", "core_loop_step", step.id)
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

    for artifact in spec.design_artifacts:
        relate("design_spec", spec.id, "has_design_artifact", "design_artifact", artifact.id)
        relate(
            "design_artifact",
            artifact.id,
            "materialized_as",
            "artifact",
            artifact.artifact_id,
        )
        for input_id in artifact.derived_from_artifact_ids:
            relate("design_artifact", artifact.id, "derived_from", "artifact", input_id)
        for surface_id in artifact.surface_ids:
            relate("design_artifact", artifact.id, "depicts", "surface", surface_id)
        for flow_id in artifact.flow_node_ids:
            relate("design_artifact", artifact.id, "depicts", "flow", flow_id)

    for layout in spec.layout_specs:
        relate("design_spec", spec.id, "has_layout", "layout_spec", layout.id)
        relate("layout_spec", layout.id, "describes", "surface", layout.surface_id)
        for element in layout.elements:
            relate("layout_spec", layout.id, "has_layout_element", "layout_element", element.id)
            relate(
                "layout_element",
                element.id,
                "describes",
                "ui_element",
                element.ui_element_id,
            )

    for interaction in spec.interaction_specs:
        relate("design_spec", spec.id, "has_interaction", "interaction_spec", interaction.id)
        relate(
            "interaction_spec",
            interaction.id,
            "diagrammed_by",
            "artifact",
            interaction.diagram_artifact_id,
        )
        for failure_id in interaction.failure_recovery_ids:
            relate(
                "interaction_spec",
                interaction.id,
                "recovers_with",
                "failure_recovery_spec",
                failure_id,
            )
        for step in interaction.steps:
            relate("interaction_spec", interaction.id, "has_step", "interaction_step", step.id)
            if step.surface_id:
                relate("interaction_step", step.id, "on", "surface", step.surface_id)
            if step.ui_element_id:
                relate("interaction_step", step.id, "uses", "ui_element", step.ui_element_id)
            if step.flow_node_id:
                relate("interaction_step", step.id, "realized_by", "flow", step.flow_node_id)

    for matrix in spec.state_matrices:
        relate("design_spec", spec.id, "has_state_matrix", "state_matrix", matrix.id)
        relate("state_matrix", matrix.id, "about", "design_object", matrix.subject_id)
        for case in matrix.cases:
            relate("state_matrix", matrix.id, "has_case", "state_case", case.id)
    for progression in spec.progression_specs:
        relate("design_spec", spec.id, "has_progression", "progression_spec", progression.id)
        for axis in progression.axes:
            relate("progression_spec", progression.id, "has_axis", "progression_axis", axis.id)
    for balance in spec.balance_specs:
        relate("design_spec", spec.id, "has_balance", "balance_spec", balance.id)
        for parameter in balance.parameters:
            relate("balance_spec", balance.id, "has_parameter", "balance_parameter", parameter.id)
        for mechanism_id in balance.mechanism_ids:
            relate("balance_spec", balance.id, "expressed_by", "mechanism", mechanism_id)
    for feedback in spec.feedback_specs:
        relate("design_spec", spec.id, "has_feedback", "feedback_spec", feedback.id)
        for surface_id in feedback.surface_ids:
            relate("feedback_spec", feedback.id, "on", "surface", surface_id)
        for element_id in feedback.ui_element_ids:
            relate("feedback_spec", feedback.id, "uses", "ui_element", element_id)
    for tutorial in spec.tutorial_specs:
        relate("design_spec", spec.id, "has_tutorial", "tutorial_spec", tutorial.id)
        for step in tutorial.steps:
            relate("tutorial_spec", tutorial.id, "has_step", "tutorial_step", step.id)
    for failure in spec.failure_recovery_specs:
        relate(
            "design_spec",
            spec.id,
            "has_failure_recovery",
            "failure_recovery_spec",
            failure.id,
        )
    for dependency in spec.dependency_specs:
        relate("design_spec", spec.id, "has_dependency", "dependency_spec", dependency.id)
        relate(
            "dependency_spec",
            dependency.id,
            "targets",
            "system_concept",
            dependency.target_system_id,
        )
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
    return sorted(rows)