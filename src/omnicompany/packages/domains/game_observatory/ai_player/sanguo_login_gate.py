"""Build and seed the evidence-backed Sanguo pre-login state map."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..store import ObservatoryStore
from .contracts import (
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    MemoryRecordV1,
    SemanticStateV1,
    StateAssignmentV1,
    StateObservationFeaturesV1,
    TransitionEdgeV1,
)
from .guide_research import load_guide_seed
from .state_recognition import build_state_observation
from .store import AIPlayerStore


ENVIRONMENT_ID = "environment.sanguo.bilibili.mumu.prelogin.1_31_0"
GAME_ID = "sanguo-mouding-tianxia"
BUILD_SCOPE_ID = "nslg-bilibili-1.31.0-versionCode-13100-20260715"
ACCOUNT_SCOPE_ID = "account.sanguo.pure-ai.prelogin-unauthenticated"
DEVICE_SCOPE_ID = "device://mumu/0"
RESEARCH_RECORD_ID = "res:截至2026-07-15-三国谋定天下-bilibili-版-1:f768c565"


STATE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "state.sanguo.prelogin.qr-login",
        "title": "扫码登录覆盖层",
        "description": "本地唯一主入口打开后出现的外部手机版扫码登录覆盖层。",
        "run_id": "evidence.run.08cdd16cf2b744a7a6be39d201ca5baf",
        "step_id": "evidence.step.fd7273eeba1c4efc985e03ba5fa8b22c",
        "artifact_id": "art.device.1784130710011.17b87e24",
        "features": {
            "ui_structure_tokens": ["surface:unity-login", "overlay:qrcode", "button:close"],
            "ui_text_tokens": ["扫码登录", "请打开 三国：谋定天下手机版 扫一扫登录"],
            "runtime_tokens": ["package:com.bilibili.nslg", "orientation:landscape"],
            "overlay_tokens": ["qrcode-login"],
            "critical_features": {"surface": "login", "dialog": "qrcode-login"},
        },
    },
    {
        "id": "state.sanguo.prelogin.startup-announcement",
        "title": "开服公告",
        "description": "关闭扫码层后显示的开服公告覆盖层。",
        "run_id": "evidence.run.d674d51bfbb444b2be36f1b9a393800f",
        "step_id": "evidence.step.eb57c743c9fa4308abb07077c4038eb8",
        "artifact_id": "art.device.1784130564909.b2d178ae",
        "features": {
            "ui_structure_tokens": ["surface:unity-login", "overlay:announcement", "button:close"],
            "ui_text_tokens": ["开服公告"],
            "runtime_tokens": ["package:com.bilibili.nslg", "orientation:landscape"],
            "overlay_tokens": ["startup-announcement"],
            "critical_features": {"surface": "login", "dialog": "startup-announcement"},
        },
    },
    {
        "id": "state.sanguo.prelogin.local-login",
        "title": "本地登录页",
        "description": "包含“征战天下”和设置入口的本地登录页；没有游客或既有会话入口。",
        "run_id": "evidence.run.dc6dfb73746d465090e0a9288abb85f5",
        "step_id": "evidence.step.c9ba2ff95da444cfbcf49b17bc3ee945",
        "artifact_id": "art.device.1784130875387.7ea7c2b8",
        "features": {
            "ui_structure_tokens": ["surface:unity-login", "button:enter", "button:settings"],
            "ui_text_tokens": ["征战天下", "设置"],
            "runtime_tokens": ["package:com.bilibili.nslg", "orientation:landscape"],
            "critical_features": {"surface": "login", "dialog": "none"},
        },
    },
    {
        "id": "state.sanguo.prelogin.settings",
        "title": "登录页设置",
        "description": "只包含画质、帧率和报错反馈的设置覆盖层。",
        "run_id": "evidence.run.6c74163fd0534174a6619c5c80e9432a",
        "step_id": "evidence.step.efc4f5bef03a4f53951d9bcd18272e8e",
        "artifact_id": "art.device.1784130818741.cfb59ae9",
        "features": {
            "ui_structure_tokens": ["surface:unity-login", "overlay:settings", "button:close"],
            "ui_text_tokens": ["设置", "画质", "帧率", "报错反馈"],
            "runtime_tokens": ["package:com.bilibili.nslg", "orientation:landscape"],
            "overlay_tokens": ["login-settings"],
            "critical_features": {"surface": "login", "dialog": "settings"},
        },
    },
)


EDGE_SPECS: tuple[dict[str, str], ...] = (
    {
        "id": "edge.sanguo.prelogin.qr-to-announcement",
        "run_id": "evidence.run.d674d51bfbb444b2be36f1b9a393800f",
        "step_id": "evidence.step.eb57c743c9fa4308abb07077c4038eb8",
        "from": "state.sanguo.prelogin.qr-login",
        "to": "state.sanguo.prelogin.startup-announcement",
        "expected": "关闭扫码登录覆盖层后显示开服公告",
    },
    {
        "id": "edge.sanguo.prelogin.announcement-to-login",
        "run_id": "evidence.run.d79f766361994075a49551a439e0175a",
        "step_id": "evidence.step.68ba8ac36f6a4cb7b4169cda26af3205",
        "from": "state.sanguo.prelogin.startup-announcement",
        "to": "state.sanguo.prelogin.local-login",
        "expected": "关闭开服公告后显示本地登录页",
    },
    {
        "id": "edge.sanguo.prelogin.login-to-qr",
        "run_id": "evidence.run.08cdd16cf2b744a7a6be39d201ca5baf",
        "step_id": "evidence.step.fd7273eeba1c4efc985e03ba5fa8b22c",
        "from": "state.sanguo.prelogin.local-login",
        "to": "state.sanguo.prelogin.qr-login",
        "expected": "点击征战天下后打开扫码登录覆盖层",
    },
    {
        "id": "edge.sanguo.prelogin.qr-to-login",
        "run_id": "evidence.run.d44d67025b6a4788950d5fe8757f2336",
        "step_id": "evidence.step.6099fab0b8cd4211bd427856b8e1832e",
        "from": "state.sanguo.prelogin.qr-login",
        "to": "state.sanguo.prelogin.local-login",
        "expected": "关闭扫码登录覆盖层后返回本地登录页",
    },
    {
        "id": "edge.sanguo.prelogin.login-to-settings",
        "run_id": "evidence.run.6c74163fd0534174a6619c5c80e9432a",
        "step_id": "evidence.step.efc4f5bef03a4f53951d9bcd18272e8e",
        "from": "state.sanguo.prelogin.local-login",
        "to": "state.sanguo.prelogin.settings",
        "expected": "打开登录页设置覆盖层",
    },
    {
        "id": "edge.sanguo.prelogin.settings-to-login",
        "run_id": "evidence.run.dc6dfb73746d465090e0a9288abb85f5",
        "step_id": "evidence.step.c9ba2ff95da444cfbcf49b17bc3ee945",
        "from": "state.sanguo.prelogin.settings",
        "to": "state.sanguo.prelogin.local-login",
        "expected": "关闭设置覆盖层后返回本地登录页",
    },
)


class SanguoLoginGateSeedResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["sanguo-login-gate-seed-result.v1"] = Field(
        default="sanguo-login-gate-seed-result.v1",
        alias="schema",
    )
    environment_id: str
    state_count: int
    transition_count: int
    guide_count: int
    artifact_identity_claim_count: int
    external_identity_blocker: str
    login_gate_closed: Literal[False] = False
    persistence_reopen_verified: bool
    ai_player_schema_version: int


def _identity_hash(payload: Mapping[str, Any]) -> str:
    identity_payload = {key: value for key, value in payload.items() if key != "created_at"}
    raw = json.dumps(
        identity_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _same_except_timestamps(
    left: BaseModel,
    right: BaseModel,
    *timestamp_fields: str,
) -> bool:
    """Accept a legacy seed only when every non-clock field is identical."""

    left_payload = left.model_dump(mode="json", by_alias=True)
    right_payload = right.model_dump(mode="json", by_alias=True)
    for field in timestamp_fields:
        left_payload.pop(field, None)
        right_payload.pop(field, None)
    return left_payload == right_payload


def build_login_gate_fixture(store: ObservatoryStore) -> dict[str, Any]:
    states: list[dict[str, Any]] = []
    for spec in STATE_SPECS:
        run = store.get_evidence_run(spec["run_id"])
        step = store.get_evidence_step(spec["step_id"])
        artifact = store.get_artifact(spec["artifact_id"])
        if run is None or step is None or artifact is None:
            raise ValueError(f"missing Sanguo login-gate evidence for {spec['id']}")
        if step.evidence_run_id != run.id or spec["artifact_id"] not in step.artifact_ids:
            raise ValueError(f"Sanguo state evidence does not belong to its step: {spec['id']}")
        states.append(
            {
                **spec,
                "artifact_sha256": artifact.sha256,
                "screenshot_fingerprint": artifact.sha256,
                "captured_at": step.ended_at or step.started_at,
                "created_at": step.ended_at or step.started_at,
            }
        )
    edges: list[dict[str, Any]] = []
    for spec in EDGE_SPECS:
        run = store.get_evidence_run(spec["run_id"])
        step = store.get_evidence_step(spec["step_id"])
        if run is None or step is None or step.evidence_run_id != run.id:
            raise ValueError(f"missing Sanguo login-gate transition evidence for {spec['id']}")
        if not step.before_frame_id or not step.after_frame_id:
            raise ValueError(f"Sanguo transition lacks before/after frames: {spec['id']}")
        edges.append(
            {
                **spec,
                "before_artifact_id": step.before_frame_id,
                "after_artifact_id": step.after_frame_id,
                "recorded_step_status": step.status,
                "recorded_run_status": run.status,
                "created_at": step.ended_at or step.started_at,
            }
        )
    identity_run = store.get_evidence_run(
        "evidence.run.19651f10f47a4c04bc5b85100f9b21e3"
    )
    if identity_run is None:
        raise ValueError("missing Sanguo login-gate identity run")
    blocker_step = store.get_evidence_step(
        "evidence.step.fd7273eeba1c4efc985e03ba5fa8b22c"
    )
    if blocker_step is None:
        raise ValueError("missing Sanguo external-login blocker step")
    return {
        "schema": "sanguo-login-gate-fixture.v1",
        "environment": {
            "id": ENVIRONMENT_ID,
            "game_id": GAME_ID,
            "game_id_aliases": ["nslg"],
            "build_scope_id": BUILD_SCOPE_ID,
            "account_scope_id": ACCOUNT_SCOPE_ID,
            "channel": "bilibili",
            "device_scope_id": DEVICE_SCOPE_ID,
            "device_scope_id_aliases": [
                "device://adb/127.0.0.1:16384",
                "device.mumu15.local.canonical-16384",
            ],
            "version_name": "1.31.0",
            "version_code": 13100,
            "package": "com.bilibili.nslg",
            "identity_run_id": "evidence.run.19651f10f47a4c04bc5b85100f9b21e3",
            "created_at": identity_run.started_at,
        },
        "states": states,
        "edges": edges,
        "blocker": {
            "kind": "external_identity_qr_login",
            "summary": "点击本地唯一“征战天下”后必须使用手机版扫码登录；没有游客或既有会话入口。",
            "reactivation_condition": "取得外部身份登录的单独授权并完成可审计的扫码登录",
            "user_gameplay_actions": 0,
            "created_at": blocker_step.ended_at or blocker_step.started_at,
        },
    }


def write_login_gate_fixture(store_root: Path, output_path: Path) -> Path:
    payload = build_login_gate_fixture(ObservatoryStore(store_root))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def seed_login_gate_fixture(
    store_root: Path,
    fixture_path: Path,
    guide_seed_path: Path,
) -> SanguoLoginGateSeedResultV1:
    observatory = ObservatoryStore(store_root)
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if payload.get("schema") != "sanguo-login-gate-fixture.v1":
        raise ValueError("unsupported Sanguo login-gate fixture schema")
    environment_payload = payload["environment"]
    environment = EnvironmentScopeV1(
        id=environment_payload["id"],
        game_id=environment_payload["game_id"],
        game_id_aliases=environment_payload["game_id_aliases"],
        build_scope_id=environment_payload["build_scope_id"],
        account_scope_id=environment_payload["account_scope_id"],
        channel=environment_payload["channel"],
        device_scope_id=environment_payload["device_scope_id"],
        device_scope_id_aliases=environment_payload["device_scope_id_aliases"],
        locale="zh-CN",
        viewport_width=1920,
        viewport_height=1080,
        identity_hash=_identity_hash(environment_payload),
        evidence_refs=[
            EvidenceReferenceV1(
                environment_id=ENVIRONMENT_ID,
                evidence_run_ids=[environment_payload["identity_run_id"]],
            )
        ],
        created_at=environment_payload["created_at"],
    )
    player = AIPlayerStore(observatory)
    existing_environment = player.get_environment(ENVIRONMENT_ID)
    if existing_environment is None:
        player.put_environment(environment)
    elif not _same_except_timestamps(existing_environment, environment, "created_at"):
        raise ValueError("canonical Sanguo login-gate environment conflicts with fixture")
    else:
        environment = existing_environment

    artifact_ids = {
        state["artifact_id"] for state in payload["states"]
    } | {
        artifact_id
        for edge in payload["edges"]
        for artifact_id in (edge["before_artifact_id"], edge["after_artifact_id"])
    }
    for artifact_id in sorted(artifact_ids):
        artifact = observatory.get_artifact(artifact_id)
        if artifact is None:
            raise ValueError(f"missing Sanguo login-gate artifact: {artifact_id}")
        owner = artifact.metadata.get("environment_id")
        if owner not in {None, ENVIRONMENT_ID}:
            raise ValueError(f"Sanguo artifact is already owned by another environment: {artifact_id}")
        if owner is None:
            observatory.save_artifact(
                artifact.model_copy(
                    update={
                        "metadata": {
                            **artifact.metadata,
                            "environment_id": ENVIRONMENT_ID,
                            "identity_claim_source": "sanguo-login-gate-fixture.v1",
                        }
                    }
                )
            )

    for state_payload in payload["states"]:
        refs = [
            EvidenceReferenceV1(
                environment_id=ENVIRONMENT_ID,
                artifact_ids=[state_payload["artifact_id"]],
                evidence_run_ids=[state_payload["run_id"]],
                evidence_step_ids=[state_payload["step_id"]],
            )
        ]
        feature_payload = dict(state_payload["features"])
        feature_payload["screenshot_fingerprint"] = state_payload["screenshot_fingerprint"]
        features = StateObservationFeaturesV1.model_validate(feature_payload)
        observation = build_state_observation(
            environment_id=ENVIRONMENT_ID,
            viewport_width=1920,
            viewport_height=1080,
            features=features,
            evidence_refs=refs,
            observation_id=f"observation.{state_payload['id']}",
            captured_at=state_payload["captured_at"],
            created_at=state_payload["created_at"],
        )
        existing_observation = player.get_state_observation(ENVIRONMENT_ID, observation.id)
        if existing_observation is None:
            player.append_state_observation(observation)
        elif not _same_except_timestamps(
            existing_observation,
            observation,
            "captured_at",
            "created_at",
        ):
            raise ValueError(f"Sanguo state observation conflicts with fixture: {observation.id}")
        else:
            observation = existing_observation
        state = SemanticStateV1(
            id=state_payload["id"],
            environment_id=ENVIRONMENT_ID,
            title=state_payload["title"],
            description=state_payload["description"],
            semantic_fingerprint=observation.feature_hash,
            observation_feature_hashes=[observation.feature_hash],
            tags=["prelogin", "evidence-backed-candidate"],
            status="accepted",
            evidence_refs=refs,
            created_at=state_payload["created_at"],
        )
        existing_state = player.get_semantic_state(ENVIRONMENT_ID, state.id)
        if existing_state is None:
            player.put_semantic_state(state)
        elif not _same_except_timestamps(existing_state, state, "created_at"):
            raise ValueError(f"Sanguo semantic state conflicts with fixture: {state.id}")
        else:
            state = existing_state
        assignment = StateAssignmentV1(
            id=f"assignment.{observation.id}.v1",
            environment_id=ENVIRONMENT_ID,
            observation_id=observation.id,
            state_id=state.id,
            method="new_candidate",
            confidence=1.0,
            reasons=["实机截图、UI 结构和可见文字共同建立的登录前状态候选"],
            evidence_refs=refs,
            created_at=state_payload["created_at"],
        )
        current = player.get_current_state_assignment(ENVIRONMENT_ID, observation.id)
        if current is None:
            player.append_state_assignment(assignment)
        elif not _same_except_timestamps(current, assignment, "created_at"):
            raise ValueError(f"Sanguo state assignment conflicts with fixture: {assignment.id}")

    for edge_payload in payload["edges"]:
        step = observatory.get_evidence_step(edge_payload["step_id"])
        if step is None:
            raise ValueError(f"missing Sanguo edge step: {edge_payload['step_id']}")
        refs = [
            EvidenceReferenceV1(
                environment_id=ENVIRONMENT_ID,
                artifact_ids=[
                    edge_payload["before_artifact_id"],
                    edge_payload["after_artifact_id"],
                ],
                evidence_run_ids=[edge_payload["run_id"]],
                evidence_step_ids=[edge_payload["step_id"]],
            )
        ]
        edge = TransitionEdgeV1(
            id=edge_payload["id"],
            environment_id=ENVIRONMENT_ID,
            from_state_id=edge_payload["from"],
            to_state_id=edge_payload["to"],
            action=step.action,
            target_bounds=step.target_bounds,
            expected_change=edge_payload["expected"],
            observed_change=(
                f"Before/After 已显示目标状态变化；原始 step={edge_payload['recorded_step_status']}，"
                f"run={edge_payload['recorded_run_status']}，状态事实与录制稳定门分别保留。"
            ),
            outcome="verified_state_change",
            evidence_refs=refs,
            created_at=edge_payload["created_at"],
        )
        existing_edge = player.get_transition_edge(ENVIRONMENT_ID, edge.id)
        if existing_edge is None:
            player.put_transition_edge(edge)
        elif not _same_except_timestamps(existing_edge, edge, "created_at"):
            raise ValueError(f"Sanguo transition conflicts with fixture: {edge.id}")

    guides = load_guide_seed(
        guide_seed_path,
        environment_id=ENVIRONMENT_ID,
        research_record_id=RESEARCH_RECORD_ID,
    )
    for guide in guides:
        existing = player.get_guide_knowledge(ENVIRONMENT_ID, guide.id, version=guide.version)
        if existing is None:
            player.append_guide_knowledge(guide)
        elif existing != guide:
            raise ValueError(f"Sanguo guide knowledge conflicts with seed: {guide.id}")

    blocker_refs = [
        EvidenceReferenceV1(
            environment_id=ENVIRONMENT_ID,
            evidence_run_ids=["evidence.run.08cdd16cf2b744a7a6be39d201ca5baf"],
            evidence_step_ids=["evidence.step.fd7273eeba1c4efc985e03ba5fa8b22c"],
        )
    ]
    blocker = MemoryRecordV1(
        id="memory.sanguo.prelogin.external-qr-blocker.v1",
        environment_id=ENVIRONMENT_ID,
        kind="failure_forbidden",
        subject_id="login.external-identity-qr",
        payload={
            key: value
            for key, value in payload["blocker"].items()
            if key != "created_at"
        },
        evidence_refs=blocker_refs,
        created_at=payload["blocker"]["created_at"],
    )
    existing_blocker = player.get_memory(ENVIRONMENT_ID, blocker.id)
    if existing_blocker is None:
        player.append_memory(blocker)
    elif not _same_except_timestamps(existing_blocker, blocker, "created_at"):
        raise ValueError("Sanguo login blocker memory conflicts with fixture")
    else:
        blocker = existing_blocker

    reopened = AIPlayerStore(ObservatoryStore(store_root))
    persistence_verified = (
        reopened.get_environment(ENVIRONMENT_ID) == environment
        and len(reopened.list_semantic_states(ENVIRONMENT_ID)) == len(payload["states"])
        and len(reopened.list_transition_edges(ENVIRONMENT_ID)) == len(payload["edges"])
        and len(reopened.list_guide_knowledge(ENVIRONMENT_ID)) == len(guides)
        and reopened.get_memory(ENVIRONMENT_ID, blocker.id) == blocker
    )
    return SanguoLoginGateSeedResultV1(
        environment_id=ENVIRONMENT_ID,
        state_count=len(payload["states"]),
        transition_count=len(payload["edges"]),
        guide_count=len(guides),
        artifact_identity_claim_count=len(artifact_ids),
        external_identity_blocker=payload["blocker"]["summary"],
        persistence_reopen_verified=persistence_verified,
        ai_player_schema_version=reopened.schema_version,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-fixture")
    build.add_argument("--store-root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    seed = subparsers.add_parser("seed")
    seed.add_argument("--store-root", type=Path, required=True)
    seed.add_argument("--fixture", type=Path, required=True)
    seed.add_argument("--guide-seed", type=Path, required=True)
    seed.add_argument("--result", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build-fixture":
        write_login_gate_fixture(args.store_root, args.output)
        return 0
    result = seed_login_gate_fixture(args.store_root, args.fixture, args.guide_seed)
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(
        json.dumps(result.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return int(not result.persistence_reopen_verified)


if __name__ == "__main__":
    raise SystemExit(main())
