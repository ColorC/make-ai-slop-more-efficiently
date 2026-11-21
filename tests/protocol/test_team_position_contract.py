"""TeamSpec 岗位与生成溯源契约。"""

import pytest
from pydantic import ValidationError

from omnicompany.protocol.team import (
    NodeKind,
    TeamGenerationMethod,
    TeamGenerationSpec,
    TeamNode,
    TeamPositionActivation,
    TeamPositionSpec,
    TeamSpec,
)
from omnicompany.packages.services._core.team_loader import (
    dump_team_to_yaml,
    load_team_from_yaml,
)


def _position(*, activation: TeamPositionActivation) -> TeamPositionSpec:
    return TeamPositionSpec(
        id="work-owner",
        name="Project Work Owner",
        responsibilities=["维护目标、路由和完成证据"],
        non_responsibilities=["不自批自己的生产变更"],
        activation=activation,
        activation_evidence_refs=["task:demo"] if activation == TeamPositionActivation.ACTIVE else [],
    )


def test_active_position_maps_to_a_node_and_generated_team_remains_team_spec() -> None:
    team = TeamSpec(
        id="demo",
        name="Demo",
        description="one canonical team",
        nodes=[
            TeamNode(
                id="owner-worker",
                kind=NodeKind.SUB_PIPELINE,
                position_id="work-owner",
            )
        ],
        edges=[],
        entry="owner-worker",
        positions=[_position(activation=TeamPositionActivation.ACTIVE)],
        generation=TeamGenerationSpec(
            method=TeamGenerationMethod.TEAM_BUILDER,
            builder_id="team-builder",
            request_ref="material:request.demo",
        ),
    )

    assert isinstance(team, TeamSpec)
    assert team.nodes[0].position_id == team.positions[0].id
    assert team.generation is not None
    assert team.generation.builder_id == "team-builder"


def test_on_demand_position_does_not_require_a_node() -> None:
    team = TeamSpec(
        id="demo",
        name="Demo",
        description="on-demand position catalog",
        nodes=[],
        edges=[],
        entry="",
        positions=[_position(activation=TeamPositionActivation.ON_DEMAND)],
    )

    assert team.positions[0].activation == TeamPositionActivation.ON_DEMAND


@pytest.mark.parametrize(
    "generation",
    [
        TeamGenerationSpec(method=TeamGenerationMethod.DECLARED),
        TeamGenerationSpec(
            method=TeamGenerationMethod.TEAM_BUILDER,
            builder_id="team-builder",
        ),
    ],
)
def test_supported_generation_methods_do_not_create_another_team_type(
    generation: TeamGenerationSpec,
) -> None:
    team = TeamSpec(
        id="demo",
        name="Demo",
        description="same TeamSpec",
        nodes=[],
        edges=[],
        entry="",
        generation=generation,
    )

    assert type(team) is TeamSpec


def test_unknown_position_reference_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown position_id"):
        TeamSpec(
            id="demo",
            name="Demo",
            description="invalid mapping",
            nodes=[
                TeamNode(
                    id="worker",
                    kind=NodeKind.SUB_PIPELINE,
                    position_id="missing",
                )
            ],
            edges=[],
            entry="worker",
        )


def test_parallel_team_builder_identifier_is_rejected() -> None:
    with pytest.raises(ValidationError, match="builder_id='team-builder'"):
        TeamGenerationSpec(
            method=TeamGenerationMethod.TEAM_BUILDER,
            builder_id="project-team-builder",
        )


def test_position_and_generation_round_trip_through_existing_yaml_loader(
    tmp_path,
) -> None:
    path = tmp_path / "team.yaml"
    original = TeamSpec(
        id="demo",
        name="Demo",
        description="same loader",
        nodes=[
            TeamNode(
                id="owner-worker",
                kind=NodeKind.SUB_PIPELINE,
                position_id="work-owner",
            )
        ],
        edges=[],
        entry="owner-worker",
        positions=[_position(activation=TeamPositionActivation.ACTIVE)],
        generation=TeamGenerationSpec(
            method=TeamGenerationMethod.TEAM_BUILDER,
            builder_id="team-builder",
        ),
    )

    dump_team_to_yaml(original, path)
    loaded = load_team_from_yaml(path)

    assert loaded == original
