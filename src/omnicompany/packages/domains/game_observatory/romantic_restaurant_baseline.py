# [OMNI] origin=codex created_by=codex intent="export the canonical Romantic Restaurant modeling baseline"
"""Export the first canonical Romantic Restaurant game-model baseline.

The exporter is intentionally read-only with respect to the observatory SQLite
store.  It reconciles a small, named historical sample against canonical
EvidenceStep and runtime telemetry records, then writes deterministic JSON
models that remain honest about the missing historical route-result receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .ai_player.contracts import EvidenceReferenceV1
from .ai_player.execution_benchmark import (
    build_execution_batch,
    summarize_execution_benchmark,
)
from .game_modeling import (
    ApplicabilityScopeV1,
    ContentEntityV1,
    ContentRelationV1,
    EvidenceStepMomentV1,
    ExecutionAttemptLogV1,
    ExecutionAttemptV1,
    ExecutionSafetyReceiptV1,
    FactRecordV1,
    FailureRecoveryBoundaryV1,
    FacilityTimingV1,
    GameContentGraphV1,
    GameSystemDossierV1,
    GameSystemRegistryV1,
    GuardedBatchRoutePreparationV1,
    NewSessionPreparationV1,
    NextStatePredictionV1,
    PlayStrategyRecordV1,
    PredictionAdjudicationV1,
    ResourceFlowV1,
    StrategyCandidateV1,
    StrategyMetricDefinitionV1,
    SystemRegistryEntryV1,
    SystemRelationV1,
    SystemStateV1,
    SystemTransitionV1,
    UnknownQuestionV1,
    ValidationQuestionV1,
    iter_dossier_facts,
    validate_model_path,
    validate_prediction_adjudication,
)

GAME_ID = "romantic-restaurant"
ENVIRONMENT_ID = "environment.romantic-restaurant.mumu0.preidentity.3_95_0"
UPDATED_AT = datetime.fromisoformat("2026-08-05T02:29:56.916306+00:00")
DEFAULT_DATABASE = Path("data/domains/game_observatory/observatory.sqlite3")
DEFAULT_OUTPUT_DIR = Path("data/domains/game_observatory/game_models/romantic-restaurant")
DEFAULT_AGENT_RECEIPT_DIR = Path("data/domains/game_observatory/agent_receipts/actions")
SOURCE_DOCUMENTS = [
    "docs/projects/game-observatory/cases/romantic-restaurant/README.md",
    "docs/projects/game-observatory/cases/romantic-restaurant/01-merge-board-generator-and-unlock.md",
    "docs/projects/game-observatory/cases/romantic-restaurant/02-order-economy-and-restaurant-restoration.md",
    "docs/projects/game-observatory/cases/romantic-restaurant/03-monetization-activity-and-collection-map.md",
    "docs/projects/game-observatory/cases/romantic-restaurant/EXECUTION_EFFICIENCY.md",
]

Outcome = Literal["effective", "no_change"]


@dataclass(frozen=True)
class AttemptSpec:
    step_id: str
    outcome: Outcome
    business_change: str | None


@dataclass(frozen=True)
class SampleSpec:
    id: str
    session_id: str
    chain_kind: Literal["high_repetition", "semi_fixed"]
    route_id: str
    attempts: tuple[AttemptSpec, ...]
    legal_business_terminal_reached: bool
    branch_decisions: tuple[str, ...] = ()


SAMPLES: tuple[SampleSpec, ...] = (
    SampleSpec(
        id="bread-level-six-chain",
        session_id="ai-player-session.external.romantic.c9e2ba58bcab42da98d6ee732683e318",
        chain_kind="high_repetition",
        route_id="route.historical.per-action.bread-level-six",
        attempts=(
            AttemptSpec(
                "evidence.step.7e568075de4341cab7629d69a2f32f74",
                "effective",
                "两个一级奶油袋合成为二级面团。",
            ),
            AttemptSpec(
                "evidence.step.d5202ebf1da6420abb43faf05681f401",
                "effective",
                "两个二级面团合成为三级初学者面包。",
            ),
            AttemptSpec(
                "evidence.step.7d7fa38012c64f8bbb65d10316d7b5fd",
                "effective",
                "两个三级初学者面包合成为四级长条面包。",
            ),
            AttemptSpec(
                "evidence.step.3ccbeed29a4c460f90c3f01fe0896ef9",
                "effective",
                "两个四级长条面包合成为五级切片面包。",
            ),
            AttemptSpec(
                "evidence.step.d034b1203c664f00bee50c19c4c9e6e0",
                "effective",
                "两个五级切片面包合成为六级黄油果酱吐司。",
            ),
        ),
        legal_business_terminal_reached=True,
    ),
    SampleSpec(
        id="generator-three-taps-before-safe-stop",
        session_id="ai-player-session.external.romantic.c9e2ba58bcab42da98d6ee732683e318",
        chain_kind="high_repetition",
        route_id="route.historical.per-action.generator-three-taps",
        attempts=(
            AttemptSpec(
                "evidence.step.70d502e4bf184a0fafbe53345dcc2b33",
                "effective",
                "购物篮生成一个一级面包袋并消耗一点体力。",
            ),
            AttemptSpec(
                "evidence.step.10752626b667463483d6cb268204f7a2",
                "effective",
                "购物篮再次生成一个一级面包袋并消耗一点体力。",
            ),
            AttemptSpec(
                "evidence.step.e290ec1e3a3c4bc6b44cc51c26dc8c52",
                "effective",
                "购物篮第三次生成一个一级面包袋并消耗一点体力。",
            ),
        ),
        legal_business_terminal_reached=True,
    ),
    SampleSpec(
        id="witness-three-dialogue-clicks",
        session_id="ai-player-session.external.romantic.c9e2ba58bcab42da98d6ee732683e318",
        chain_kind="high_repetition",
        route_id="route.historical.per-action.witness-dialogue",
        attempts=(
            AttemptSpec(
                "evidence.step.ee6d768fcb3e4b1f80230da065b21971",
                "effective",
                "推进“目击者”剧情对话第一步。",
            ),
            AttemptSpec(
                "evidence.step.0a862fb961b145a59f45cccf2fec2cd5",
                "effective",
                "推进“目击者”剧情对话第二步。",
            ),
            AttemptSpec(
                "evidence.step.4cc07111e2304e27bf249630f723ef84",
                "effective",
                "推进“目击者”剧情对话第三步。",
            ),
        ),
        legal_business_terminal_reached=False,
    ),
    SampleSpec(
        id="order-plus-fourteen-tail",
        session_id="ai-player-session.external.romantic.fea2de2fca2647b7b0cbed584c8627ce",
        chain_kind="semi_fixed",
        route_id="route.historical.per-action.order-plus-fourteen",
        attempts=(
            AttemptSpec(
                "evidence.step.b59bd0cb7e29430e92d3cbecbbf3b396",
                "effective",
                "合成一份三级经典炸薯条。",
            ),
            AttemptSpec(
                "evidence.step.094788f244ec426d84a14529e552e974",
                "effective",
                "两个三级经典炸薯条合成为四级番茄酱薯条。",
            ),
            AttemptSpec(
                "evidence.step.d37d7c7f190c4a3d94b0529ff0002d11",
                "effective",
                "提交+14订单，四级薯条被消费，HUD金币18变为32。",
            ),
        ),
        legal_business_terminal_reached=True,
        branch_decisions=("先完成四级薯条，再提交已亮起的左侧+14订单。",),
    ),
    SampleSpec(
        id="order-plus-twenty-tail",
        session_id="ai-player-session.external.romantic.87a598ae773044a2a68529abcff63def",
        chain_kind="semi_fixed",
        route_id="route.historical.per-action.order-plus-twenty",
        attempts=(
            AttemptSpec(
                "evidence.step.ccbbb6f190a14f6d92ae0af9a0db951f",
                "effective",
                "轻篮生成一个一级土豆。",
            ),
            AttemptSpec(
                "evidence.step.14b063ea8e5842aaa7f999cda2d97b5e",
                "effective",
                "两个一级土豆合成为二级切片土豆。",
            ),
            AttemptSpec(
                "evidence.step.7a0bdfec8fb74faab5ba3fd3500ad4cc",
                "effective",
                "两个二级切片土豆合成为三级经典炸薯条。",
            ),
            AttemptSpec(
                "evidence.step.3c89fc64b7314652aeefe4e4511c422d",
                "effective",
                "提交+20订单，三级薯条与六级吐司被消费，HUD金币32变为52。",
            ),
            AttemptSpec("evidence.step.92482c8070f94f61a8189757e9290163", "no_change", None),
        ),
        legal_business_terminal_reached=True,
        branch_decisions=("复用棋盘既有六级吐司，只补齐三级薯条后提交右侧+20订单。",),
    ),
)


@dataclass(frozen=True)
class AdjudicatedAttemptSpec:
    id: str
    step_id: str
    terminal_telemetry_id: str
    planned_action_id: str
    outcome: Literal["effective", "no_change", "failed"]
    expectation_met: bool
    business_change: str | None = None
    observed_detail: str | None = None
    failure_reason: str | None = None
    receipt_id: str | None = None


ADJUDICATION_SESSION_ID = "ai-player-session.external.9860a3ae7522438b9311b6060635b8a1"
ADJUDICATION_TASK_ID = "task.romantic-restaurant.prediction-adjudication.low-risk.v1"
ADJUDICATION_ROUTE_ID = "route.manual.per-action.low-risk-prediction-round-1"
ADJUDICATED_ATTEMPTS: tuple[AdjudicatedAttemptSpec, ...] = (
    AdjudicatedAttemptSpec(
        id="enter-kitchen",
        step_id="evidence.step.6a552d69d77449689d4d18f3ec6db992",
        terminal_telemetry_id=(
            "runtime-telemetry.session.ai-player-session.external."
            "9860a3ae7522438b9311b6060635b8a1.act.4.58599ee413ed"
        ),
        planned_action_id="planned-action.low-risk-round-1.enter-kitchen",
        outcome="effective",
        expectation_met=True,
        business_change="从餐厅主界面进入厨房合成棋盘。",
        observed_detail="kitchen_board_entered",
    ),
    AdjudicatedAttemptSpec(
        id="shopping-basket-first-tap",
        step_id="evidence.step.9a957b61ced241a1bf9aece6141f5a15",
        terminal_telemetry_id=(
            "runtime-telemetry.session.ai-player-session.external."
            "9860a3ae7522438b9311b6060635b8a1.act.7.233f66f4358a"
        ),
        planned_action_id="planned-action.low-risk-round-1.shopping-basket-first-tap",
        outcome="no_change",
        expectation_met=False,
        observed_detail=(
            "selection_only: 体力仍为100，没有生成基础产物；购物篮出现蓝色选择框，底部显示“轻篮（等级5）”。"
        ),
    ),
    AdjudicatedAttemptSpec(
        id="mislocated-same-level-merge",
        step_id="evidence.step.80c9d1ffb4d246f7bef223b5b470f6ca",
        terminal_telemetry_id=(
            "runtime-telemetry.session.ai-player-session.external."
            "9860a3ae7522438b9311b6060635b8a1.act.10.2363864f462a"
        ),
        planned_action_id="planned-action.low-risk-round-1.merge-declared-equal-items",
        outcome="failed",
        expectation_met=False,
        observed_detail=(
            "declared merge objects were never touched; swipe (202,624)->(269,624) "
            "落在棋盘顶部锁定沙土区域，且67像素位移不足以表达相邻棋盘格中心。"
        ),
        failure_reason="invalid_target: source and target grid centers were mislocated",
    ),
)

ROUND_TWO_ADJUDICATION_SESSION_ID = "ai-player-session.external.7677c3b2f67a41f19df0eb239bc67889"
ROUND_TWO_ADJUDICATION_ROUTE_ID = "route.manual.per-action.low-risk-prediction-round-2"
ROUND_TWO_GENERATOR_RECEIPT_ID = (
    "agent-receipt.action.sha256.08a456bbd748c3dcbacb7bd911d291bf7a6573b8c90f57082ba9c05e368d28b6"
)
ROUND_TWO_MERGE_RECEIPT_ID = (
    "agent-receipt.action.sha256.fe4a4e64253d90fc375ab0868f715e7c6ddf5c9b9c58eed9012b279df3bb8462"
)
ROUND_TWO_ADJUDICATED_ATTEMPTS: tuple[AdjudicatedAttemptSpec, ...] = (
    AdjudicatedAttemptSpec(
        id="shopping-basket-second-tap",
        step_id="evidence.step.a224f8b376ef4cb890a91635655ab14c",
        terminal_telemetry_id=(
            "runtime-telemetry.session.ai-player-session.external."
            "7677c3b2f67a41f19df0eb239bc67889.act.4.ac9e6aa020fd"
        ),
        planned_action_id="planned-action.low-risk-round-2.shopping-basket-second-tap",
        outcome="effective",
        expectation_met=True,
        business_change=(
            "再次点击已选中的轻篮，体力由100降至99，棋盘空位新增一个面粉袋外观的基础产物。"
        ),
        observed_detail=(
            "cli_false_negative_preserved: guarded-action receipt 保留 "
            "expectation_missed/visual_state_change_only/stop=true，但业务终态显示体力100→99且棋盘新增产物。"
        ),
        receipt_id=ROUND_TWO_GENERATOR_RECEIPT_ID,
    ),
    AdjudicatedAttemptSpec(
        id="same-level-flour-bag-merge",
        step_id="evidence.step.4db74819ced9428dad5fb2fac906932a",
        terminal_telemetry_id=(
            "runtime-telemetry.session.ai-player-session.external."
            "7677c3b2f67a41f19df0eb239bc67889.act.7.acf2d8c9255b"
        ),
        planned_action_id="planned-action.low-risk-round-2.merge-equal-flour-bags",
        outcome="effective",
        expectation_met=True,
        business_change=(
            "左侧源格清空，右侧目标格由面粉袋升级为一个面团（等级2），棋盘占用净减少1格。"
        ),
        observed_detail=(
            "cli_expectation_met: guarded-action receipt 记录 expectation_met，终态同时满足源格清空、目标升级和净释放一格。"
        ),
        receipt_id=ROUND_TWO_MERGE_RECEIPT_ID,
    ),
)


@dataclass(frozen=True)
class EvidenceRow:
    id: str
    evidence_run_id: str
    started_at: datetime
    ended_at: datetime
    body: Mapping[str, Any]
    telemetry_event_id: str | None
    telemetry_occurred_at: datetime | None


class ObservatoryReader:
    """Small read-only projection over the canonical observatory database."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        uri = f"file:{self.database_path.as_posix()}?mode=ro"
        self.connection = sqlite3.connect(uri, uri=True)
        self.connection.row_factory = sqlite3.Row

    def __enter__(self) -> "ObservatoryReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.connection.close()

    def evidence(
        self,
        step_id: str,
        *,
        expected_environment_id: str = ENVIRONMENT_ID,
        expected_session_id: str | None = None,
        require_telemetry: bool = True,
    ) -> EvidenceRow:
        row = self.connection.execute(
            "SELECT s.id,s.evidence_run_id,s.body_json,s.started_at,s.ended_at,"
            "r.body_json AS evidence_run_body_json "
            "FROM evidence_steps AS s JOIN evidence_runs AS r ON r.id=s.evidence_run_id "
            "WHERE s.id=?",
            (step_id,),
        ).fetchone()
        if row is None or row["ended_at"] is None:
            raise ValueError(f"missing completed EvidenceStep: {step_id}")
        body = json.loads(row["body_json"])
        evidence_run_body = json.loads(row["evidence_run_body_json"])
        if evidence_run_body.get("scope_id") != expected_environment_id:
            raise ValueError(f"evidence environment drift: {step_id}")
        telemetry_id = f"runtime-telemetry.session.gateway-action-reserve.{step_id}"
        telemetry = self.connection.execute(
            "SELECT environment_id,session_id,event_type,occurred_at "
            "FROM ai_player_runtime_telemetry_events WHERE id=?",
            (telemetry_id,),
        ).fetchone()
        if telemetry is None and require_telemetry:
            raise ValueError(f"missing reserve telemetry: {telemetry_id}")
        if telemetry is not None:
            if telemetry["environment_id"] != expected_environment_id:
                raise ValueError(f"telemetry environment drift: {telemetry_id}")
            if expected_session_id and telemetry["session_id"] != expected_session_id:
                raise ValueError(f"telemetry session drift: {telemetry_id}")
            if telemetry["event_type"] != "session.checkpointed":
                raise ValueError(f"unexpected reserve telemetry type: {telemetry_id}")
        if require_telemetry:
            self.assert_no_route_result(step_id)
        return EvidenceRow(
            id=row["id"],
            evidence_run_id=row["evidence_run_id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]),
            body=body,
            telemetry_event_id=telemetry_id if telemetry is not None else None,
            telemetry_occurred_at=(
                datetime.fromisoformat(telemetry["occurred_at"]) if telemetry is not None else None
            ),
        )

    def assert_no_route_result(self, step_id: str) -> None:
        for table in (
            "ai_player_operation_executions",
            "ai_player_action_quality_samples",
        ):
            rows = self.connection.execute(
                f"SELECT body_json FROM {table} WHERE evidence_step_id=?",  # noqa: S608
                (step_id,),
            ).fetchall()
            for row in rows:
                if _route_result_values(json.loads(row["body_json"])):
                    raise ValueError(
                        f"historical route result unexpectedly appeared for {step_id} in {table}"
                    )

    def assert_telemetry_event(self, telemetry_id: str, *, expected_session_id: str) -> None:
        row = self.connection.execute(
            "SELECT environment_id,session_id,event_type FROM "
            "ai_player_runtime_telemetry_events WHERE id=?",
            (telemetry_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"missing runtime telemetry: {telemetry_id}")
        if row["environment_id"] != ENVIRONMENT_ID:
            raise ValueError(f"telemetry environment drift: {telemetry_id}")
        if row["session_id"] != expected_session_id:
            raise ValueError(f"telemetry session drift: {telemetry_id}")
        if row["event_type"] != "session.checkpointed":
            raise ValueError(f"unexpected terminal telemetry type: {telemetry_id}")

    def assert_action_quality_sample(self, sample_id: str, *, step_id: str) -> None:
        row = self.connection.execute(
            "SELECT evidence_step_id FROM ai_player_action_quality_samples WHERE id=?",
            (sample_id,),
        ).fetchone()
        if row is None or row["evidence_step_id"] != step_id:
            raise ValueError(f"missing or mismatched action-quality sample: {sample_id}")

    def session_budget(self, session_id: str, when: datetime, *, terminal: bool) -> int:
        operator = ">=" if terminal else "<="
        order = "ASC" if terminal else "DESC"
        row = self.connection.execute(
            "SELECT result_session_json FROM ai_player_session_lifecycle_events "
            f"WHERE session_id=? AND created_at {operator} ? "
            f"ORDER BY created_at {order} LIMIT 1",
            (session_id, when.isoformat()),
        ).fetchone()
        if row is None:
            fallback_order = "DESC" if terminal else "ASC"
            row = self.connection.execute(
                "SELECT result_session_json FROM ai_player_session_lifecycle_events "
                f"WHERE session_id=? ORDER BY created_at {fallback_order} LIMIT 1",
                (session_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"missing lifecycle budget snapshot: {session_id}")
        return int(json.loads(row["result_session_json"])["remaining_action_budget"])

    def lease_receipt(self, session_id: str) -> tuple[str, str, bool]:
        row = self.connection.execute(
            "SELECT id,status,last_heartbeat_at FROM ai_player_session_leases "
            "WHERE session_id=? ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"missing session lease: {session_id}")
        return row["id"], row["status"], bool(row["last_heartbeat_at"])


def _route_result_values(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"route_result_id", "route_result_ids"}:
                if isinstance(item, str) and item:
                    found.append(item)
                elif isinstance(item, Sequence) and not isinstance(item, str):
                    found.extend(str(entry) for entry in item if entry)
            else:
                found.extend(_route_result_values(item))
    elif isinstance(value, Sequence) and not isinstance(value, str):
        for item in value:
            found.extend(_route_result_values(item))
    return found


def _artifacts(body: Mapping[str, Any]) -> list[str]:
    values = body.get("artifact_ids", [])
    return [str(item) for item in values if item]


def _evidence_reference(
    row: EvidenceRow,
    *,
    note: str,
    additional_source_ids: Sequence[str] = (),
) -> EvidenceReferenceV1:
    action_run_id = row.body.get("action_run_id")
    source_ids = [
        item for item in dict.fromkeys([row.telemetry_event_id, *additional_source_ids]) if item
    ]
    return EvidenceReferenceV1(
        environment_id=ENVIRONMENT_ID,
        artifact_ids=_artifacts(row.body),
        evidence_run_ids=[row.evidence_run_id],
        evidence_step_ids=[row.id],
        trace_run_ids=[action_run_id] if action_run_id else [],
        source_ids=source_ids,
        note=note,
    )


def _fact_reference(
    reader: ObservatoryReader, step_ids: Sequence[str], note: str
) -> EvidenceReferenceV1:
    rows = [reader.evidence(step_id, require_telemetry=False) for step_id in step_ids]
    return EvidenceReferenceV1(
        environment_id=ENVIRONMENT_ID,
        artifact_ids=list(dict.fromkeys(item for row in rows for item in _artifacts(row.body))),
        evidence_run_ids=list(dict.fromkeys(row.evidence_run_id for row in rows)),
        evidence_step_ids=list(step_ids),
        trace_run_ids=list(
            dict.fromkeys(
                str(row.body["action_run_id"]) for row in rows if row.body.get("action_run_id")
            )
        ),
        source_ids=list(
            dict.fromkeys(row.telemetry_event_id for row in rows if row.telemetry_event_id)
        ),
        note=note,
    )


def _scope() -> ApplicabilityScopeV1:
    return ApplicabilityScopeV1(
        environment_id=ENVIRONMENT_ID,
        game_id=GAME_ID,
        build_scope_id="android.mumu0.preidentity",
        game_version="3.95.0",
        account_scope_id="preidentity.local-device-account",
        locale="zh-CN",
        valid_from=datetime.fromisoformat("2026-08-03T00:00:00+00:00"),
        notes=["首个 canonical 基线仅覆盖 2026-08-03 至 2026-08-05 的 MuMu 0 实机证据。"],
    )


def _fact(
    reader: ObservatoryReader,
    fact_id: str,
    statement: str,
    *,
    step_ids: Sequence[str] = (),
    status: Literal["verified", "inferred", "unknown"] = "verified",
    rationale: str | None = None,
) -> FactRecordV1:
    references = (
        [_fact_reference(reader, step_ids, f"{fact_id} 的 canonical EvidenceStep。")]
        if step_ids
        else []
    )
    return FactRecordV1(
        id=fact_id,
        statement=statement,
        status=status,
        applicability=_scope(),
        evidence_refs=references,
        rationale=rationale,
        recorded_at=UPDATED_AT,
    )


def _build_attempt(
    reader: ObservatoryReader,
    sample: SampleSpec,
    attempt_spec: AttemptSpec,
    sequence: int,
) -> tuple[ExecutionAttemptV1, EvidenceRow]:
    row = reader.evidence(attempt_spec.step_id, expected_session_id=sample.session_id)
    action = row.body.get("action") or {}
    live_evaluation = row.body.get("live_evaluation") or {}
    outcome = attempt_spec.outcome
    reference = _evidence_reference(
        row,
        note="历史单动作 EvidenceStep 与 gateway-action-reserve checkpoint 对账。",
    )
    return (
        ExecutionAttemptV1(
            id=f"execution-attempt.romantic.{sample.id}.{sequence:02d}",
            sequence=sequence,
            environment_id=ENVIRONMENT_ID,
            session_id=sample.session_id,
            route_id=sample.route_id,
            planned_action_id=f"historical-action.{sample.id}.{sequence:02d}",
            action_kind=str(action.get("type") or "unknown"),
            started_at=row.started_at,
            ended_at=row.ended_at,
            outcome=outcome,
            expectation_met=bool(live_evaluation.get("expectation_met", outcome == "effective")),
            business_state_changed=outcome == "effective",
            business_change=attempt_spec.business_change,
            telemetry_event_ids=[row.telemetry_event_id],
            route_result_ids=[],
            canonical_source_gaps=["route_result_ids"],
            evidence_refs=[reference],
        ),
        row,
    )


def _build_batch(reader: ObservatoryReader, sample: SampleSpec):
    attempts_and_rows = [
        _build_attempt(reader, sample, spec, index)
        for index, spec in enumerate(sample.attempts, start=1)
    ]
    attempts = [item[0] for item in attempts_and_rows]
    rows = [item[1] for item in attempts_and_rows]
    started_at = rows[0].started_at
    ended_at = rows[-1].ended_at
    wall_clock = (ended_at - started_at).total_seconds()
    evidence_processing = sum((row.ended_at - row.started_at).total_seconds() for row in rows)
    action_starts = [
        datetime.fromisoformat(str(row.body["action_started_at"]))
        for row in rows
        if row.body.get("action_started_at")
    ]
    action_ends = [
        datetime.fromisoformat(str(row.body["action_ended_at"]))
        for row in rows
        if row.body.get("action_ended_at")
    ]
    device_window = (
        (max(action_ends) - min(action_starts)).total_seconds()
        if action_starts and action_ends
        else None
    )
    timing = FacilityTimingV1(
        request_received_at=started_at,
        checkpoint_completed_at=ended_at,
        terminal_receipt_completed_at=ended_at,
        wall_clock_seconds=wall_clock,
        evidence_processing_seconds=evidence_processing,
        device_action_seconds=device_window,
    )
    lease_id, lease_state, heartbeat = reader.lease_receipt(sample.session_id)
    if lease_state not in {"held", "released", "expired", "lost"}:
        raise ValueError(f"unsupported lease terminal state: {lease_state}")
    safety_reference = EvidenceReferenceV1(
        environment_id=ENVIRONMENT_ID,
        evidence_run_ids=list(dict.fromkeys(row.evidence_run_id for row in rows)),
        evidence_step_ids=[row.id for row in rows],
        source_ids=[row.telemetry_event_id for row in rows],
        note=(
            "批次安全收据来自 lease、heartbeat、预算快照和 checkpoint telemetry；"
            "pending_action_count_at_terminal=0 仅投影本批所列 EvidenceStep 均已完成，"
            "不声称 SQLite 存在独立 pending-action 字段。"
        ),
    )
    safety = ExecutionSafetyReceiptV1(
        source_guard_checked=True,
        source_guard_rejection_count=0,
        lease_id=lease_id,
        lease_state_at_terminal=lease_state,
        heartbeat_observed=heartbeat,
        remaining_action_budget_at_start=reader.session_budget(
            sample.session_id, started_at, terminal=False
        ),
        remaining_action_budget_at_terminal=reader.session_budget(
            sample.session_id, ended_at, terminal=True
        ),
        checkpoint_completed=True,
        pending_action_count_at_terminal=0,
        safety_violations=[],
        evidence_refs=[safety_reference],
    )
    return build_execution_batch(
        batch_id=f"execution-batch.romantic.{sample.id}",
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        session_id=sample.session_id,
        chain_kind=sample.chain_kind,
        route_id=sample.route_id,
        planned_action_count=len(sample.attempts),
        legal_business_terminal_reached=sample.legal_business_terminal_reached,
        branch_decisions=sample.branch_decisions,
        knowledge_outcomes=(),
        timing=timing,
        attempts=attempts,
        recovery_events=(),
        safety=safety,
        evidence_refs=[safety_reference],
    )


def build_benchmarks(reader: ObservatoryReader):
    batches = sorted(
        (_build_batch(reader, sample) for sample in SAMPLES),
        key=lambda batch: batch.timing.request_received_at,
    )
    high = summarize_execution_benchmark(
        benchmark_id="execution-benchmark.romantic.high-repetition-baseline",
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        workload_kind="high_repetition",
        batches=[batch for batch in batches if batch.chain_kind == "high_repetition"],
        notes=[
            "三个历史样本均为单动作 CLI 慢速基线，不是效率成功样本。",
            "最高样本约 3.74 有效动作/分钟，低于预声明的 10+ 目标。",
            "历史动作有真实 checkpoint telemetry，但没有 canonical route result；source gap 强制批次失败。",
        ],
        updated_at=UPDATED_AT,
    )
    semi_fixed = summarize_execution_benchmark(
        benchmark_id="execution-benchmark.romantic.semi-fixed-baseline",
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        workload_kind="semi_fixed",
        batches=[batch for batch in batches if batch.chain_kind == "semi_fixed"],
        notes=[
            "+14 与 +20 订单都完成了真实业务终态，但吞吐仅约 0.83 与 0.63 动作/分钟。",
            "+20 批次完整保留 5 次 attempt，其中最后一次稳定等待为 no_change，不计有效动作。",
            "业务完成不能替代效率验收；历史 route result 缺失同样使 gate 失败。",
        ],
        updated_at=UPDATED_AT,
    )
    return high, semi_fixed


def build_merge_board_dossier(reader: ObservatoryReader) -> GameSystemDossierV1:
    generator_steps = [item.step_id for item in SAMPLES[1].attempts]
    bread_steps = [item.step_id for item in SAMPLES[0].attempts]
    fries_steps = [
        "evidence.step.094788f244ec426d84a14529e552e974",
        "evidence.step.7a0bdfec8fb74faab5ba3fd3500ad4cc",
    ]
    generator_fact = _fact(
        reader,
        "fact.rr.merge.generator-cost",
        "在已验证的购物篮连续生成样本中，每次有效生成一个一级面包袋并消耗1点体力。",
        step_ids=generator_steps,
    )
    merge_fact = _fact(
        reader,
        "fact.rr.merge.same-level-two-to-one",
        "已验证的面包与薯条链均通过两个同级物品合成为一个更高一级物品。",
        step_ids=[*bread_steps, *fries_steps],
    )
    bread_fact = _fact(
        reader,
        "fact.rr.merge.level-six-toast",
        "连续五次同级合成形成六级黄油果酱吐司。",
        step_ids=bread_steps,
    )
    fries_fact = _fact(
        reader,
        "fact.rr.merge.fries-levels",
        "薯条链已验证三级经典炸薯条，以及由两个三级薯条形成的四级番茄酱薯条。",
        step_ids=fries_steps,
    )
    drift_fact = _fact(
        reader,
        "fact.rr.merge.source-guard-required",
        "批量执行必须在每次设备动作前重新核对来源区域，连续无变化时停止并重新识别。",
        status="inferred",
        rationale="历史证据使用逐动作来源守卫；尚未有新 guarded-batch route 的实机结果。",
    )
    return GameSystemDossierV1(
        id="rr-01-merge-board",
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        title="合成棋盘、生成器与物品升级链",
        classification="core_loop",
        modeling_status="partial",
        objective=[merge_fact],
        entry_and_unlock=[generator_fact],
        states=[
            SystemStateV1(
                id="state.rr.merge.generator-ready",
                label="生成器可用",
                description="购物篮已被选中，可尝试生成一级物品。",
                facts=[generator_fact],
            ),
            SystemStateV1(
                id="state.rr.merge-items-available",
                label="同级物品可合成",
                description="棋盘上存在两个可安全拖合的同级物品。",
                facts=[merge_fact],
            ),
            SystemStateV1(
                id="state.rr.merge-known-high-tier",
                label="已知高阶物品形成",
                description="当前证据覆盖六级吐司、三级薯条与四级薯条。",
                facts=[bread_fact, fries_fact],
            ),
        ],
        transitions=[
            SystemTransitionV1(
                id="transition.rr.merge.generate",
                from_state_id="state.rr.merge.generator-ready",
                to_state_id="state.rr.merge-items-available",
                trigger="点击已选中的购物篮生成器",
                preconditions=["生成器可用", "剩余体力至少为1", "来源守卫通过"],
                effects=["生成一级物品", "体力减少1"],
                facts=[generator_fact],
            ),
            SystemTransitionV1(
                id="transition.rr.merge.upgrade",
                from_state_id="state.rr.merge-items-available",
                to_state_id="state.rr.merge-known-high-tier",
                trigger="将一个物品拖到同级物品上",
                preconditions=["两个物品同链同级", "目标格状态未漂移"],
                effects=["消耗两个同级物品", "生成一个高一级物品"],
                facts=[merge_fact, bread_fact, fries_fact],
            ),
        ],
        resource_flows=[
            ResourceFlowV1(
                id="flow.rr.merge.energy-to-item",
                direction="consume",
                resource_entity_id="entity.rr.resource.energy",
                quantity_expression="1 per verified shopping-basket generation",
                conditions=["仅对当前购物篮样本成立，不能推广到所有生成器"],
                facts=[generator_fact],
            )
        ],
        relations=[
            SystemRelationV1(
                id="relation.rr.merge-to-orders",
                relation_type="supplies",
                target_system_id="rr-02-order-economy-restoration",
                description="合成物品被订单消费并兑换金币。",
                facts=[bread_fact, fries_fact],
            )
        ],
        failure_recovery_boundaries=[
            FailureRecoveryBoundaryV1(
                id="boundary.rr.merge.source-drift",
                kind="safety",
                condition="来源格或目标格与记录的局部来源不一致，或连续动作未产生预期业务变化。",
                response="动作前重新识别；不一致则拒绝动作，连续无变化则停止批次并保留 checkpoint。",
                facts=[drift_fact],
            )
        ],
        unknown_questions=[
            UnknownQuestionV1(
                id="unknown.rr.merge.generator-table",
                question="购物篮完整产出表、概率、冷却与耗尽规则是什么？",
                priority="high",
                next_observation="在独立授权的新 Session 中记录长序列产出、体力变化和冷却状态。",
                stop_reason="当前仅有少量确定性样本，不能推导完整概率表。",
                related_fact_ids=[generator_fact.id],
            ),
            UnknownQuestionV1(
                id="unknown.rr.merge.full-item-tree",
                question="面包、薯条及新订单所需物品的完整合成树是什么？",
                priority="high",
                next_observation="建立实体级物品图并为每次新等级留存 Before/After 证据。",
                related_fact_ids=[merge_fact.id],
            ),
        ],
        source_document_ids=SOURCE_DOCUMENTS,
        updated_at=UPDATED_AT,
    )


def build_order_dossier(reader: ObservatoryReader) -> GameSystemDossierV1:
    plus14 = _fact(
        reader,
        "fact.rr.order.plus-fourteen",
        "提交四级番茄酱薯条的+14订单后，HUD金币由18变为32。",
        step_ids=["evidence.step.d37d7c7f190c4a3d94b0529ff0002d11"],
    )
    plus20 = _fact(
        reader,
        "fact.rr.order.plus-twenty",
        "提交三级经典炸薯条与六级黄油果酱吐司的+20订单后，HUD金币由32变为52。",
        step_ids=[
            "evidence.step.3c89fc64b7314652aeefe4e4511c422d",
            "evidence.step.92482c8070f94f61a8189757e9290163",
        ],
    )
    witness = _fact(
        reader,
        "fact.rr.restoration.witness-cost",
        "“目击者”任务面板显示成本24、表面奖励XP 15；启动并完成剧情后HUD金币42变为18。",
        step_ids=[
            "evidence.step.observe.b214429670b742f3a91fd47a8c5a3ec4",
            "evidence.step.fa8460bd92f740fab72a726d7ccc9ffe",
            "evidence.step.observe.9dbdf6825ab1421b9596b992245cdc04",
        ],
    )
    external_window = _fact(
        reader,
        "fact.rr.restoration.delivery-window-boundary",
        "“外卖窗口”面板显示成本46、表面奖励XP 15和小黄鸭1；本轮关闭面板且金币仍为52，未启动任务。",
        step_ids=[
            "evidence.step.fa82c19897bf4cdf8f547fc6a657ad49",
            "evidence.step.efd56ff7239c45b0b9fa58150f1a4faf",
        ],
    )
    xp_unknown = _fact(
        reader,
        "fact.rr.restoration.xp-receipt-unknown",
        "任务面板显示的XP 15是否实际到账仍未知。",
        status="unknown",
        rationale="没有独立XP条、等级数字变化或结算动画的可靠终态回读。",
    )
    return GameSystemDossierV1(
        id="rr-02-order-economy-restoration",
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        title="订单经济、金币门槛与餐厅修复任务",
        classification="core_loop",
        modeling_status="partial",
        objective=[plus14, plus20],
        entry_and_unlock=[witness, external_window],
        states=[
            SystemStateV1(
                id="state.rr.order.items-ready",
                label="订单物品已齐",
                description="订单区域显示可完成，所需物品仍在棋盘。",
                facts=[plus14, plus20],
            ),
            SystemStateV1(
                id="state.rr.order.coins-earned",
                label="订单金币已到账",
                description="订单物品被消费，HUD金币按订单奖励增加。",
                facts=[plus14, plus20],
            ),
            SystemStateV1(
                id="state.rr.restoration-task-visible",
                label="餐厅任务面板可见",
                description="可只读确认名称、成本和表面奖励；启动是独立消耗决策。",
                facts=[witness, external_window, xp_unknown],
            ),
        ],
        transitions=[
            SystemTransitionV1(
                id="transition.rr.order.submit",
                from_state_id="state.rr.order.items-ready",
                to_state_id="state.rr.order.coins-earned",
                trigger="点击绿色订单完成区域",
                preconditions=["订单物品齐全", "订单完成按钮可用"],
                effects=["消费订单物品", "HUD金币增加", "刷新新订单"],
                facts=[plus14, plus20],
            ),
            SystemTransitionV1(
                id="transition.rr.restoration.open-panel",
                from_state_id="state.rr.order.coins-earned",
                to_state_id="state.rr.restoration-task-visible",
                trigger="返回餐厅并打开任务面板",
                preconditions=["餐厅入口可用"],
                effects=["显示任务成本与表面奖励，不代表已经消费"],
                facts=[external_window],
            ),
        ],
        resource_flows=[
            ResourceFlowV1(
                id="flow.rr.order.items-to-coins",
                direction="convert",
                resource_entity_id="entity.rr.currency.coins",
                quantity_expression="+14 or +20 in the retained samples",
                conditions=["订单物品齐全并成功提交"],
                facts=[plus14, plus20],
            ),
            ResourceFlowV1(
                id="flow.rr.restoration.coins-to-story",
                direction="consume",
                resource_entity_id="entity.rr.currency.coins",
                quantity_expression="24 for Witness; 46 displayed for Delivery Window",
                conditions=["只有明确启动任务才应视为消费"],
                facts=[witness, external_window],
            ),
        ],
        relations=[
            SystemRelationV1(
                id="relation.rr.orders-from-merge",
                relation_type="consumes_output_of",
                target_system_id="rr-01-merge-board",
                description="订单消费合成棋盘产物。",
                facts=[plus14, plus20],
            )
        ],
        failure_recovery_boundaries=[
            FailureRecoveryBoundaryV1(
                id="boundary.rr.restoration-explicit-spend",
                kind="irreversible",
                condition="任务面板出现会消耗金币的“开始”按钮。",
                response="先记录成本和奖励；没有独立授权则关闭面板，不启动、不扣费。",
                facts=[external_window],
            )
        ],
        unknown_questions=[
            UnknownQuestionV1(
                id="unknown.rr.restoration.delivery-window",
                question="“外卖窗口”启动后的玩法、状态和奖励结算是什么？",
                priority="high",
                next_observation="获得独立消费授权后，从52金币安全终态建立新 Session。",
                stop_reason="本轮只读确认，未点击开始，不能宣称理解其玩法。",
                related_fact_ids=[external_window.id],
            ),
            UnknownQuestionV1(
                id="unknown.rr.restoration.xp",
                question="XP 15是否到账，以及小黄鸭奖励位于何处、用途是什么？",
                priority="medium",
                next_observation="寻找可读XP/等级入口与奖励库存终态。",
                related_fact_ids=[xp_unknown.id, external_window.id],
            ),
        ],
        source_document_ids=SOURCE_DOCUMENTS,
        updated_at=UPDATED_AT,
    )


def build_top_level_boundary_dossier(reader: ObservatoryReader) -> GameSystemDossierV1:
    visible = _fact(
        reader,
        "fact.rr.boundary.visible-does-not-mean-modeled",
        "“外卖窗口”任务面板可见且可关闭，但本轮未启动，因此只确认入口、成本和表面奖励。",
        step_ids=[
            "evidence.step.fa82c19897bf4cdf8f547fc6a657ad49",
            "evidence.step.efd56ff7239c45b0b9fa58150f1a4faf",
        ],
    )
    forbidden = _fact(
        reader,
        "fact.rr.boundary.prohibited-actions",
        "真实支付、广告、登录、账号绑定、外链、分享、发言与社交动作继续禁止自动执行。",
        status="inferred",
        rationale="这是本案例持续生效的安全政策边界，不是从游戏画面推导出的玩法事实。",
    )
    unknown_task = _fact(
        reader,
        "fact.rr.boundary.unknown-task-authorization",
        "带显式资源消耗或未知外部流程的新任务必须独立授权后再建模。",
        status="inferred",
        rationale="可见入口只证明存在，不证明执行合法、风险已知或玩法已理解。",
    )
    return GameSystemDossierV1(
        id="rr-03-visible-top-level-boundaries",
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        title="可见顶层入口、未知系统与安全操作边界",
        classification="top_level",
        modeling_status="read_only_boundary",
        objective=[visible],
        entry_and_unlock=[visible],
        states=[
            SystemStateV1(
                id="state.rr.boundary.entry-visible",
                label="入口可见",
                description="能够识别并只读打开面板。",
                facts=[visible],
            ),
            SystemStateV1(
                id="state.rr.boundary.authorization-required",
                label="需要独立授权",
                description="启动会消费资源或进入未知流程，不能继续自动执行。",
                facts=[forbidden, unknown_task],
            ),
        ],
        transitions=[
            SystemTransitionV1(
                id="transition.rr.boundary.inspect-and-close",
                from_state_id="state.rr.boundary.entry-visible",
                to_state_id="state.rr.boundary.authorization-required",
                trigger="只读打开任务面板并关闭",
                preconditions=["不点击开始", "不触发支付、广告、登录、外链或社交"],
                effects=["记录入口名称、成本和表面奖励", "保持资源不变"],
                facts=[visible, forbidden],
            )
        ],
        resource_flows=[],
        relations=[
            SystemRelationV1(
                id="relation.rr.boundary-restoration",
                relation_type="guards",
                target_system_id="rr-02-order-economy-restoration",
                description="对餐厅任务启动和资源消费提供授权边界。",
                facts=[unknown_task],
            )
        ],
        failure_recovery_boundaries=[
            FailureRecoveryBoundaryV1(
                id="boundary.rr.top-level-external-risk",
                kind="safety",
                condition="出现支付、广告、登录、绑定、外链、分享、发言或社交入口。",
                response="停止操作，checkpoint，返回安全已知页面；不得以探索名义越界。",
                facts=[forbidden],
            )
        ],
        unknown_questions=[
            UnknownQuestionV1(
                id="unknown.rr.boundary.delivery-window-play",
                question="“外卖窗口”属于何种玩法系统，启动后有哪些状态、资源和恢复边界？",
                priority="high",
                next_observation="获得46金币消费授权后新建独立 Session，并先做只读状态识别。",
                stop_reason="当前证据只覆盖面板，不能把入口存在写成玩法理解。",
                related_fact_ids=[visible.id, unknown_task.id],
            )
        ],
        source_document_ids=SOURCE_DOCUMENTS,
        updated_at=UPDATED_AT,
    )


def build_registry(dossiers: Sequence[GameSystemDossierV1]) -> GameSystemRegistryV1:
    return GameSystemRegistryV1(
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        systems=[
            SystemRegistryEntryV1(
                system_id=dossier.id,
                title=dossier.title,
                classification=dossier.classification,
                modeling_status=dossier.modeling_status,
                dossier_path=f"dossiers/{dossier.id}.json",
                evidence_fact_ids=[
                    fact.id
                    for fact in dossier.objective + dossier.entry_and_unlock
                    if fact.status == "verified"
                ],
                unknown_question_ids=[item.id for item in dossier.unknown_questions],
            )
            for dossier in dossiers
        ],
        updated_at=UPDATED_AT,
    )


def build_content_graph(reader: ObservatoryReader) -> GameContentGraphV1:
    scope = _scope()
    generator_fact = _fact(
        reader,
        "fact.rr.graph.generator-energy",
        "购物篮生成器在保留样本中每次生成一级面包袋并消耗1点体力。",
        step_ids=[item.step_id for item in SAMPLES[1].attempts],
    )
    merge_fact = _fact(
        reader,
        "fact.rr.graph.merge-upgrade",
        "同级二合一形成高一级物品；面包链已到六级吐司，薯条链已到四级薯条。",
        step_ids=[
            *[item.step_id for item in SAMPLES[0].attempts],
            "evidence.step.094788f244ec426d84a14529e552e974",
            "evidence.step.7a0bdfec8fb74faab5ba3fd3500ad4cc",
        ],
    )
    order14_fact = _fact(
        reader,
        "fact.rr.graph.order14",
        "+14订单消费四级番茄酱薯条并使金币18变为32。",
        step_ids=["evidence.step.d37d7c7f190c4a3d94b0529ff0002d11"],
    )
    order20_fact = _fact(
        reader,
        "fact.rr.graph.order20",
        "+20订单消费三级薯条和六级吐司并使金币32变为52。",
        step_ids=[
            "evidence.step.3c89fc64b7314652aeefe4e4511c422d",
            "evidence.step.92482c8070f94f61a8189757e9290163",
        ],
    )
    witness_fact = _fact(
        reader,
        "fact.rr.graph.witness",
        "“目击者”成本24，完成剧情后金币42变为18。",
        step_ids=[
            "evidence.step.observe.b214429670b742f3a91fd47a8c5a3ec4",
            "evidence.step.observe.9dbdf6825ab1421b9596b992245cdc04",
        ],
    )
    delivery_fact = _fact(
        reader,
        "fact.rr.graph.delivery-window",
        "“外卖窗口”显示成本46、XP 15和小黄鸭1，但未启动。",
        step_ids=[
            "evidence.step.fa82c19897bf4cdf8f547fc6a657ad49",
            "evidence.step.efd56ff7239c45b0b9fa58150f1a4faf",
        ],
    )
    xp_unknown = _fact(
        reader,
        "fact.rr.graph.xp-unknown",
        "面板显示的XP是否到账未知。",
        status="unknown",
        rationale="没有独立XP或等级终态证据。",
    )
    entities = [
        ContentEntityV1(
            id="entity.rr.system.merge",
            kind="system",
            label="合成棋盘",
            applicability=scope,
            facts=[merge_fact],
        ),
        ContentEntityV1(
            id="entity.rr.generator.shopping-basket",
            kind="generator",
            label="购物篮生成器",
            applicability=scope,
            facts=[generator_fact],
        ),
        ContentEntityV1(
            id="entity.rr.resource.energy",
            kind="resource",
            label="体力",
            applicability=scope,
            facts=[generator_fact],
        ),
        ContentEntityV1(
            id="entity.rr.item.toast-l6",
            kind="item",
            label="黄油果酱吐司（等级6）",
            applicability=scope,
            facts=[merge_fact],
        ),
        ContentEntityV1(
            id="entity.rr.item.fries-l3",
            kind="item",
            label="经典炸薯条（等级3）",
            applicability=scope,
            facts=[merge_fact],
        ),
        ContentEntityV1(
            id="entity.rr.item.fries-l4",
            kind="item",
            label="番茄酱薯条（等级4）",
            applicability=scope,
            facts=[merge_fact],
        ),
        ContentEntityV1(
            id="entity.rr.order.plus14",
            kind="order",
            label="+14薯条订单",
            applicability=scope,
            facts=[order14_fact],
        ),
        ContentEntityV1(
            id="entity.rr.order.plus20",
            kind="order",
            label="+20薯条与吐司订单",
            applicability=scope,
            facts=[order20_fact],
        ),
        ContentEntityV1(
            id="entity.rr.currency.coins",
            kind="currency",
            label="金币",
            applicability=scope,
            facts=[order14_fact, order20_fact, witness_fact],
        ),
        ContentEntityV1(
            id="entity.rr.task.witness",
            kind="story",
            label="目击者",
            applicability=scope,
            facts=[witness_fact],
        ),
        ContentEntityV1(
            id="entity.rr.task.delivery-window",
            kind="restoration",
            label="外卖窗口",
            applicability=scope,
            facts=[delivery_fact],
        ),
        ContentEntityV1(
            id="entity.rr.reward.xp",
            kind="reward",
            label="XP",
            applicability=scope,
            facts=[xp_unknown],
        ),
        ContentEntityV1(
            id="entity.rr.reward.duck",
            kind="reward",
            label="小黄鸭",
            applicability=scope,
            facts=[delivery_fact],
        ),
    ]
    relation_specs = [
        (
            "generator-consumes-energy",
            "entity.rr.generator.shopping-basket",
            "consumes",
            "entity.rr.resource.energy",
            generator_fact,
        ),
        (
            "generator-supplies-merge",
            "entity.rr.generator.shopping-basket",
            "supplies",
            "entity.rr.system.merge",
            generator_fact,
        ),
        (
            "merge-produces-toast",
            "entity.rr.system.merge",
            "produces",
            "entity.rr.item.toast-l6",
            merge_fact,
        ),
        (
            "merge-produces-fries3",
            "entity.rr.system.merge",
            "produces",
            "entity.rr.item.fries-l3",
            merge_fact,
        ),
        (
            "merge-produces-fries4",
            "entity.rr.system.merge",
            "produces",
            "entity.rr.item.fries-l4",
            merge_fact,
        ),
        (
            "order14-consumes-fries4",
            "entity.rr.order.plus14",
            "consumes",
            "entity.rr.item.fries-l4",
            order14_fact,
        ),
        (
            "order14-rewards-coins",
            "entity.rr.order.plus14",
            "rewards",
            "entity.rr.currency.coins",
            order14_fact,
        ),
        (
            "order20-consumes-fries3",
            "entity.rr.order.plus20",
            "consumes",
            "entity.rr.item.fries-l3",
            order20_fact,
        ),
        (
            "order20-consumes-toast",
            "entity.rr.order.plus20",
            "consumes",
            "entity.rr.item.toast-l6",
            order20_fact,
        ),
        (
            "order20-rewards-coins",
            "entity.rr.order.plus20",
            "rewards",
            "entity.rr.currency.coins",
            order20_fact,
        ),
        (
            "witness-consumes-coins",
            "entity.rr.task.witness",
            "consumes",
            "entity.rr.currency.coins",
            witness_fact,
        ),
        (
            "delivery-requires-coins",
            "entity.rr.task.delivery-window",
            "displays_cost_in",
            "entity.rr.currency.coins",
            delivery_fact,
        ),
        (
            "delivery-displays-xp",
            "entity.rr.task.delivery-window",
            "displays_reward",
            "entity.rr.reward.xp",
            delivery_fact,
        ),
        (
            "delivery-displays-duck",
            "entity.rr.task.delivery-window",
            "displays_reward",
            "entity.rr.reward.duck",
            delivery_fact,
        ),
    ]
    relations = [
        ContentRelationV1(
            id=f"relation.rr.{suffix}",
            source_entity_id=source,
            relation_type=kind,
            target_entity_id=target,
            applicability=scope,
            facts=[fact],
        )
        for suffix, source, kind, target, fact in relation_specs
    ]
    return GameContentGraphV1(
        id="content-graph.romantic-restaurant.baseline",
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        entities=entities,
        relations=relations,
        conflicts=[],
        source_dossier_ids=[
            "rr-01-merge-board",
            "rr-02-order-economy-restoration",
            "rr-03-visible-top-level-boundaries",
        ],
        updated_at=UPDATED_AT,
    )


def build_strategy(reader: ObservatoryReader, high, semi_fixed) -> PlayStrategyRecordV1:
    baseline_refs = [batch.evidence_refs[0] for batch in [*high.batches, *semi_fixed.batches]]
    precondition = _fact(
        reader,
        "fact.rr.strategy.historical-baseline",
        "历史逐动作CLI样本在高重复区域约3.65至3.74有效动作/分钟，在订单尾链约0.63至0.83有效动作/分钟。",
        step_ids=[attempt.step_id for sample in SAMPLES for attempt in sample.attempts],
    )
    return PlayStrategyRecordV1(
        id="play-strategy.romantic-restaurant.execution-baseline",
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        decision_problem="如何在保留来源守卫、lease、heartbeat、预算、checkpoint、EvidenceStep和route result的前提下，把已知路径执行得更快？",
        objective_priority=[
            "先保持安全与 canonical 证据完整",
            "高重复路径稳定达到10+有效动作/分钟",
            "半固定路径稳定达到5+有效动作/分钟",
            "业务完成率和失败恢复不能因提速下降",
        ],
        preconditions=[precondition],
        constraints=[
            "每次设备动作前必须通过来源守卫。",
            "必须保留 session lease、heartbeat、action budget 和 checkpoint。",
            "每个 attempt 必须有 EvidenceStep、telemetry event 和 canonical route result。",
            "连续无变化、来源漂移、lease丢失或预算不足必须中止并恢复。",
            "支付、广告、登录、外链、分享、发言和社交动作不进入自动批处理。",
        ],
        metrics=[
            StrategyMetricDefinitionV1(
                id="effective_actions_per_minute",
                label="有效动作吞吐",
                direction="maximize",
                unit="actions/minute",
                primary=True,
            ),
            StrategyMetricDefinitionV1(
                id="expected_effect_hit_rate",
                label="预期效果命中率",
                direction="maximize",
                unit="ratio",
            ),
            StrategyMetricDefinitionV1(
                id="canonical_source_gap_count",
                label="canonical来源缺口",
                direction="minimize",
                unit="attempts",
                safety_critical=True,
            ),
            StrategyMetricDefinitionV1(
                id="safety_violation_count",
                label="安全违规数",
                direction="minimize",
                unit="violations",
                safety_critical=True,
            ),
        ],
        candidates=[
            StrategyCandidateV1(
                id="candidate.per-action-cli",
                name="逐动作CLI基线",
                hypothesis="逐动作观察和checkpoint较安全，但进程启动、识别与证据处理开销导致吞吐很低。",
                legal_actions=["单次tap/swipe/wait", "每步独立识别", "每步checkpoint"],
                expected_benefits=["已有真实业务终态", "故障定位粒度细"],
                risks=["高固定开销", "历史缺少canonical route result"],
                opportunity_costs=["重复区域吞吐远低于用户要求"],
                short_term_effects=["作为慢速反例和对照组保留"],
                long_term_effects=["不适合作为高重复区域正式设施"],
                evidence_refs=baseline_refs,
            ),
            StrategyCandidateV1(
                id="candidate.guarded-batch-route",
                name="来源守卫的批处理路线",
                hypothesis="在同一受控route内复用识别上下文并批量执行，可减少每动作固定开销，同时在每步保留守卫与canonical收据。",
                legal_actions=[
                    "预声明route",
                    "每步局部来源守卫",
                    "批内heartbeat",
                    "逐步EvidenceStep与route result",
                ],
                expected_benefits=["高重复区域目标10+动作/分钟", "半固定路径目标5+动作/分钟"],
                risks=["状态漂移可能在批内放大", "需要可靠失败中止和恢复"],
                opportunity_costs=["设施实现和新Session验证成本"],
                short_term_effects=["先建立可回放fixture与dry-run"],
                long_term_effects=["形成可迁移到第二个游戏的已知路径执行设施"],
                evidence_refs=[],
            ),
        ],
        baseline_candidate_id="candidate.per-action-cli",
        selected_candidate_id=None,
        trial_results=[],
        counterexamples=[
            f"{batch.id}: {batch.effective_actions_per_minute:.2f}/min, gate={batch.gate_status}"
            for batch in [*high.batches, *semi_fixed.batches]
        ],
        conclusion="baseline_only",
        conclusion_notes=[
            "guarded-batch-route尚未在新Session实机运行，不能宣称更优。",
            "五个历史批次均失败：吞吐不达标，且每个attempt缺少canonical route result。",
            "订单和剧情发生真实业务变化，只证明跑过，不证明玩得快或策略已优化。",
        ],
        updated_at=UPDATED_AT,
    )


def _model_bytes(model: Any) -> bytes:
    payload = model.model_dump(mode="json", by_alias=True)
    text = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    return f"{text}\n".encode("utf-8")


def _fact_from_dossiers(dossiers: Sequence[GameSystemDossierV1], fact_id: str) -> FactRecordV1:
    matches = [
        fact for dossier in dossiers for fact in iter_dossier_facts(dossier) if fact.id == fact_id
    ]
    if not matches:
        raise ValueError(f"source fact not found: {fact_id}")
    canonical = matches[0].model_dump(mode="json", by_alias=True)
    if any(item.model_dump(mode="json", by_alias=True) != canonical for item in matches[1:]):
        raise ValueError(f"source fact id has divergent records: {fact_id}")
    return matches[0]


def build_new_session_preparation(
    *,
    reader: ObservatoryReader,
    dossiers: Sequence[GameSystemDossierV1],
    source_models: Mapping[str, Any],
) -> NewSessionPreparationV1:
    """Build a side-effect-free contract for the next independent Session."""

    generator_cost = _fact_from_dossiers(dossiers, "fact.rr.merge.generator-cost")
    same_level_merge = _fact_from_dossiers(dossiers, "fact.rr.merge.same-level-two-to-one")
    delivery_boundary = _fact_from_dossiers(
        dossiers, "fact.rr.restoration.delivery-window-boundary"
    )
    predictions = [
        NextStatePredictionV1(
            id="prediction.rr.generator-single-tap.v1",
            game_id=GAME_ID,
            environment_id=ENVIRONMENT_ID,
            system_id="rr-01-merge-board",
            unknown_question_ids=["unknown.rr.merge.generator-table"],
            source_fact_ids=[generator_cost.id],
            source_evidence_refs=generator_cost.evidence_refs,
            current_state_condition=(
                "新 Session 已只读确认位于合成棋盘，购物篮可见、无冷却遮罩且体力余额足够。"
            ),
            proposed_observation=(
                "在新 Session 中单独记录一次经来源守卫批准的购物篮点击及其 Before/After。"
            ),
            predicted_next_state=(
                "体力减少 1，棋盘新增一个购物篮基础产物；若没有冷却或耗尽信号，生成器仍可用。"
            ),
            expected_signals=[
                "体力计数恰好减少 1",
                "一个此前空位或既有位置出现新的基础产物",
                "EvidenceStep、telemetry event 与 route result 三者可对账",
            ],
            disconfirming_signals=[
                "体力未减少或减少量不是 1",
                "无新产物、一次出现多个产物，或生成器进入未建模冷却/耗尽状态",
                "业务变化存在但缺少 canonical route result",
            ],
            created_at=UPDATED_AT,
        ),
        NextStatePredictionV1(
            id="prediction.rr.same-level-merge.v1",
            game_id=GAME_ID,
            environment_id=ENVIRONMENT_ID,
            system_id="rr-01-merge-board",
            unknown_question_ids=["unknown.rr.merge.full-item-tree"],
            source_fact_ids=[same_level_merge.id],
            source_evidence_refs=same_level_merge.evidence_refs,
            current_state_condition=(
                "新 Session 已只读确认棋盘上存在两个同类同级、可安全合并且不属于订单提交区的物品。"
            ),
            proposed_observation=(
                "在独立 EvidenceStep 中执行一次同级二合一，并记录源格、目标格和终态物品。"
            ),
            predicted_next_state=("两个同类同级物品合并为一个高一级物品，棋盘占用格净减少 1。"),
            expected_signals=[
                "源格清空或物品移出",
                "目标格出现同链高一级物品",
                "棋盘占用格净减少 1",
            ],
            disconfirming_signals=[
                "物品未合并或被拒绝",
                "生成结果不属于同一物品链的高一级实体",
                "合并引发未建模弹窗、订单提交或资源消费",
            ],
            created_at=UPDATED_AT,
        ),
        NextStatePredictionV1(
            id="prediction.rr.delivery-window-reward.v1",
            game_id=GAME_ID,
            environment_id=ENVIRONMENT_ID,
            system_id="rr-02-order-economy-restoration",
            unknown_question_ids=[
                "unknown.rr.restoration.delivery-window",
                "unknown.rr.restoration.xp",
            ],
            source_fact_ids=[delivery_boundary.id],
            source_evidence_refs=delivery_boundary.evidence_refs,
            current_state_condition=(
                "仅在后续获得单独的 46 金币消费授权，并以新 Session 重新确认价格与奖励面板后成立。"
            ),
            proposed_observation=("先只读记录授权前状态；未获得明确消费授权时不得点击开始。"),
            predicted_next_state=(
                "若价格和奖励面板未变且授权后完成启动，系统将进入外卖窗口后续状态，并显示或结算 XP 15 与小黄鸭 1。"
            ),
            expected_signals=[
                "金币按面板成本减少 46",
                "出现可识别的外卖窗口后续状态",
                "XP 15 与小黄鸭 1 至少出现一种 canonical 到账或库存信号",
            ],
            disconfirming_signals=[
                "价格、奖励或入口状态与当前模型不同",
                "消费后没有可识别后续状态",
                "奖励未到账、数值不同或用途与模型假设冲突",
            ],
            created_at=UPDATED_AT,
        ),
    ]
    generator_row = reader.evidence(
        ROUND_TWO_ADJUDICATED_ATTEMPTS[0].step_id,
        expected_session_id=ROUND_TWO_ADJUDICATION_SESSION_ID,
    )
    merge_row = reader.evidence(
        ROUND_TWO_ADJUDICATED_ATTEMPTS[1].step_id,
        expected_session_id=ROUND_TWO_ADJUDICATION_SESSION_ID,
    )
    terminal_rows = [generator_row, merge_row]
    terminal_specs = list(ROUND_TWO_ADJUDICATED_ATTEMPTS)
    terminal_evidence = {
        row.id: EvidenceStepMomentV1(
            step_id=row.id,
            started_at=row.started_at,
            session_id=ROUND_TWO_ADJUDICATION_SESSION_ID,
        )
        for row in terminal_rows
    }
    adjudications = [
        PredictionAdjudicationV1(
            id="adjudication.confirmed.prediction.rr.generator-single-tap.v1",
            prediction_id="prediction.rr.generator-single-tap.v1",
            game_id=GAME_ID,
            environment_id=ENVIRONMENT_ID,
            disposition="confirmed",
            observed_outcome=(
                "再次点击已选中轻篮后，体力由100降至99，棋盘空位新增一个面粉袋外观基础产物。"
            ),
            explanation=(
                "自动 CLI 因视觉阈值给出 expectation_missed，但独立终态业务观察同时满足体力消耗和产物生成，故人工业务裁定为 confirmed；保留冲突回执。"
            ),
            evidence_refs=[
                _evidence_reference(
                    generator_row,
                    note=(
                        "第二轮新 Session 的生成动作终态；绑定 terminal telemetry 与原始 agent receipt。"
                    ),
                    additional_source_ids=[
                        terminal_specs[0].terminal_telemetry_id,
                        ROUND_TWO_GENERATOR_RECEIPT_ID,
                    ],
                )
            ],
            adjudicated_at=generator_row.ended_at,
        ),
        PredictionAdjudicationV1(
            id="adjudication.confirmed.prediction.rr.same-level-merge.v1",
            prediction_id="prediction.rr.same-level-merge.v1",
            game_id=GAME_ID,
            environment_id=ENVIRONMENT_ID,
            disposition="confirmed",
            observed_outcome=(
                "两个相邻同类同级面粉袋合并后，源格清空，目标格成为一个面团（等级2），棋盘占用净减少1格。"
            ),
            explanation=(
                "第二轮新 Session 的正确落点拖拽同时满足源格清空、目标升级和净释放1格，预测成立。"
            ),
            evidence_refs=[
                _evidence_reference(
                    merge_row,
                    note="第二轮新 Session 的同级合并终态。",
                    additional_source_ids=[
                        terminal_specs[1].terminal_telemetry_id,
                        ROUND_TWO_MERGE_RECEIPT_ID,
                    ],
                )
            ],
            adjudicated_at=merge_row.ended_at,
        ),
        PredictionAdjudicationV1(
            id="adjudication.pending.prediction.rr.delivery-window-reward.v1",
            prediction_id="prediction.rr.delivery-window-reward.v1",
            game_id=GAME_ID,
            environment_id=ENVIRONMENT_ID,
            disposition="pending",
            explanation="46金币外卖入口未获单独消费授权，本轮保持 pending 且禁止执行。",
            next_action=predictions[2].proposed_observation,
        ),
    ]
    prediction_by_id = {prediction.id: prediction for prediction in predictions}
    for adjudication in adjudications[:2]:
        validate_prediction_adjudication(
            prediction_by_id[adjudication.prediction_id],
            adjudication,
            terminal_evidence,
            expected_session_id=ROUND_TWO_ADJUDICATION_SESSION_ID,
        )
    validation_questions = [
        ValidationQuestionV1(
            id="validation.rr.generator-single-tap",
            unknown_question_id="unknown.rr.merge.generator-table",
            prediction_id="prediction.rr.generator-single-tap.v1",
            priority="high",
            preconditions=[
                "创建新的 canonical AIPlayerSession，不恢复历史 walkthrough Session。",
                "只读确认购物篮、体力和至少一个可接收产物的棋盘位置。",
                "route-result 持久化、EvidenceStep 与 telemetry 对账设施均通过 dry-run。",
            ],
            observation_plan=[
                "保存动作前状态与来源区域。",
                "只执行一次已授权购物篮动作。",
                "等待稳定终态并记录体力、产物、生成器可用性和三类 canonical 收据。",
            ],
            success_condition="独立 EvidenceStep 足以 confirmed、corrected 或 refuted 该预测。",
            failure_condition="来源漂移、收据缺失、无稳定终态或出现未建模资源/冷却状态。",
            recovery_action="立即停止批处理，checkpoint 后重新只读观察；不得盲目重放。",
            authorization_state="adjudicated",
        ),
        ValidationQuestionV1(
            id="validation.rr.same-level-merge",
            unknown_question_id="unknown.rr.merge.full-item-tree",
            prediction_id="prediction.rr.same-level-merge.v1",
            priority="high",
            preconditions=[
                "新 Session 中存在两个同类同级物品。",
                "目标不在订单提交区，操作不触发不可逆提交。",
                "来源格、目标格和预期物品链均已只读识别。",
            ],
            observation_plan=[
                "记录合并前两个实体和占用格数。",
                "执行一次来源守卫的同级合并。",
                "记录合并后实体、占用格数和异常界面。",
            ],
            success_condition="独立 EvidenceStep 可裁定二合一和下一等级预测。",
            failure_condition="合并被拒绝、实体分类不确定或引发非预期提交/弹窗。",
            recovery_action="停止路径并回到只读识别，不执行第二次尝试。",
            authorization_state="adjudicated",
        ),
        ValidationQuestionV1(
            id="validation.rr.delivery-window-reward",
            unknown_question_id="unknown.rr.restoration.delivery-window",
            prediction_id="prediction.rr.delivery-window-reward.v1",
            priority="high",
            preconditions=[
                "用户另行明确授权 46 金币消费。",
                "新 Session 重新确认金币、成本、奖励和不可逆边界。",
                "有消费后状态识别与失败恢复方案。",
            ],
            observation_plan=[
                "授权前只读确认面板。",
                "没有明确授权则以 blocked 结束，不执行设备动作。",
                "获得授权后另起受控批次记录消费、后续状态和奖励到账。",
            ],
            success_condition="独立证据裁定玩法入口与奖励预测。",
            failure_condition="未授权、面板变化、资金不足或消费后无法恢复到稳定状态。",
            recovery_action="安全停止并保留消费前后证据；不得重复扣费。",
            authorization_state="blocked_pending_authorization",
        ),
    ]
    generator_locator_id = "locator.rr.shopping-basket.selected.round-2"
    frozen_generator_actions = [
        {
            "id": f"planned-action.rr.shopping-basket-ten-tap.{sequence:02d}",
            "sequence": sequence,
            "locator_id": generator_locator_id,
            "action": {"type": "tap", "x": 404, "y": 1120},
            "account_action_intent": {
                "id": f"intent.rr.shopping-basket-generation.{sequence:02d}",
                "category": "gameplay_progression",
                "summary": "????????????????????????",
                "game_internal": True,
            },
            "expectation": {
                "summary": "???????????1????????????",
                "kind": "visual_change",
                "min_visual_distance": 0.015,
                "expected_business_change": "????1????????????",
                "effective_when": [
                    "????????1",
                    "????????????????",
                    "EvidenceStep?terminal telemetry ? SkillRunV1 ????",
                ],
                "stop_conditions": [
                    "????????",
                    "??????????",
                    "???????????????",
                    "???????????????????????",
                ],
            },
            "lightweight_guard_checks": [
                "??????????",
                "???? locator ????????????",
                "?????????????????0",
            ],
            "recovery_action": "?????????????????? canonical safe-stop/reconcile?",
        }
        for sequence in range(1, 11)
    ]
    source_hashes = {
        path: hashlib.sha256(_model_bytes(model)).hexdigest()
        for path, model in sorted(source_models.items())
    }
    return NewSessionPreparationV1(
        id="new-session-preparation.romantic-restaurant.round-2",
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        generated_at=max(row.ended_at for row in terminal_rows),
        source_model_sha256=source_hashes,
        current_phase_allowed_operations=[
            "read_model",
            "read_evidence",
            "generate_contract",
            "validate_contract",
        ],
        current_phase_forbidden_actions=[
            "恢复或继续历史浪漫餐厅 Session",
            "调用 DeviceGateway 执行 tap、swipe、back、launch 或 wait",
            "启动外卖窗口或消费 46 金币",
            "提交订单、领取奖励、观看广告、登录、外链、分享、发言或社交",
        ],
        predictions=predictions,
        adjudications=adjudications,
        validation_questions=validation_questions,
        guarded_batch_route=GuardedBatchRoutePreparationV1(
            candidate_id="candidate.guarded-batch-route",
            route_id="route.romantic.shopping-basket-ten-tap.guard-v1",
            route_version=1,
            game_id=GAME_ID,
            environment_id=ENVIRONMENT_ID,
            chain_kind="high_repetition",
            execution_status="ready_for_live_validation",
            execution_authorization="single_live_validation",
            executable=True,
            activation_requirements=[
                "? canonical Session ?? running?lease?heartbeat ? action budget ???",
                "DeviceGateway?RecognitionService?OperationMemory?canonical telemetry ? CAS ?????????",
                "????????????????????????10???????10?",
                "?????? EvidenceStep?terminal telemetry ? canonical SkillRunV1?",
                "?????????????????????/???checkpoint ? terminal receipt?",
            ],
            blocking_reasons=[
                "?????????? canonical Session ????????????",
                "??????????????? benchmark ???????",
                "?????? canonical route result?????? route ? live-validation ???",
            ],
            source_state_guard={
                "required_surface": "??????????????5?????????",
                "source_evidence_refs": generator_cost.evidence_refs,
                "reference_artifact_id": "art.device.1785921087171.059e9b6e",
                "generator_locator_id": generator_locator_id,
                "minimum_empty_cells": 10,
                "minimum_energy": 10,
                "minimum_session_action_budget": 11,
                "checkpoint_action_reserve": 1,
                "required_conditions": [
                    "?????????? round-2 canonical ??????",
                    "???????10",
                    "?????????10",
                    "? active pending action???? lease ?????",
                ],
                "forbidden_surface_signals": [
                    "?????????????",
                    "46???????????????",
                    "??????????????????????",
                    "?????????????",
                ],
            },
            locators=[
                {
                    "id": generator_locator_id,
                    "kind": "fixed_source_pixel",
                    "target_name": "?????????5????",
                    "source_evidence_step_ids": ["evidence.step.a224f8b376ef4cb890a91635655ab14c"],
                    "source_artifact_ids": [
                        "art.device.1785921087171.059e9b6e",
                        "art.probe-terminal.1785921090924.db35395e",
                    ],
                    "point": {"x": 404, "y": 1120},
                    "bounds": {"x": 340, "y": 1050, "width": 130, "height": 150},
                }
            ],
            planned_actions=frozen_generator_actions,
            batch_end_checkpoint=[
                "????????????????????????",
                "??10???? EvidenceStep?terminal telemetry ? SkillRunV1 ?????",
                "?????no-change?false-success ???????????",
            ],
            terminal_receipt_requirements=[
                "ExecutionBatchV1 ???? canonical attempts ? evidence refs?",
                "?? ExecutionAttemptV1.route_result_ids ???? SkillRunV1.id?",
                "ExecutionSafetyReceiptV1 ? clean?? benchmark ?????????",
            ],
            wall_clock_boundary=(
                "request_received_at ? terminal_receipt_completed_at????? guard???????"
                "??????checkpoint ??????"
            ),
            success_branch=[
                "10????????????? canonical ?????",
                "?? checkpoint ? terminal receipt ???????? live-validation ???",
                "???????? route ??? live_validated????????????????",
            ],
            failure_branch=[
                "???????????????????????????????????",
                "???? EvidenceStep?telemetry ? SkillRunV1??? no-op ??????",
            ],
            recovery_branch=[
                "?? canonical safe-stop/reconcile ????? pending action?",
                "???????????????????????????",
                "???????????????????",
            ],
            forbidden_actions=[
                "??????????????????????",
                "46??????????????????????????",
                "??????????????????????",
                "?? SkillRunV1 ??????????????",
            ],
            high_repetition_target_per_minute=10.0,
            semi_fixed_target_per_minute=5.0,
        ),
        notes=[
            "本文件只是一份新 Session 准备合同，不证明任何预测成立。",
            "guarded batch route 仍未运行；策略结论继续是 baseline_only。",
            "本次导出不访问设备、不恢复 Session、不消费任何游戏资源。",
        ],
    )


def build_low_risk_prediction_round_one_attempt_log(
    reader: ObservatoryReader,
) -> ExecutionAttemptLogV1:
    attempts: list[ExecutionAttemptV1] = []
    for sequence, spec in enumerate(ADJUDICATED_ATTEMPTS, start=1):
        row = reader.evidence(spec.step_id, expected_session_id=ADJUDICATION_SESSION_ID)
        reader.assert_telemetry_event(
            spec.terminal_telemetry_id,
            expected_session_id=ADJUDICATION_SESSION_ID,
        )
        action = row.body.get("action") or {}
        attempts.append(
            ExecutionAttemptV1(
                id=f"execution-attempt.romantic.low-risk-round-1.{spec.id}",
                sequence=sequence,
                environment_id=ENVIRONMENT_ID,
                session_id=ADJUDICATION_SESSION_ID,
                route_id=ADJUDICATION_ROUTE_ID,
                planned_action_id=spec.planned_action_id,
                action_kind=str(action.get("type") or "unknown"),
                started_at=row.started_at,
                ended_at=row.ended_at,
                outcome=spec.outcome,
                expectation_met=spec.expectation_met,
                business_state_changed=spec.outcome == "effective",
                business_change=spec.business_change,
                failure_reason=spec.failure_reason,
                observed_detail=spec.observed_detail,
                execution_receipt_kind="single_action_execution",
                telemetry_event_ids=[spec.terminal_telemetry_id],
                route_result_ids=[],
                canonical_source_gaps=[],
                evidence_refs=[
                    _evidence_reference(
                        row,
                        note="本轮单步 act 的 EvidenceStep、action run 与 checkpoint 回执。",
                        additional_source_ids=[spec.terminal_telemetry_id],
                    )
                ],
            )
        )

    false_positive_id = "action-quality.external-agent-action.55bc5149e868397389745e4b6cd62d6a"
    reader.assert_action_quality_sample(
        false_positive_id,
        step_id="evidence.step.9a957b61ced241a1bf9aece6141f5a15",
    )
    return ExecutionAttemptLogV1(
        id="execution-attempt-log.romantic.low-risk-prediction-round-1",
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        session_id=ADJUDICATION_SESSION_ID,
        task_id=ADJUDICATION_TASK_ID,
        purpose="记录第一次低风险预测裁定回合的真实设备动作与人工业务裁定。",
        benchmark_eligible=False,
        benchmark_exclusion_reason=(
            "本轮是坐标与业务语义校准；仅三个离散单步，含 selection_only 和 invalid_target，未运行预声明的高重复或半固定速度批次。"
        ),
        prediction_ids=[
            "prediction.rr.generator-single-tap.v1",
            "prediction.rr.same-level-merge.v1",
        ],
        attempts=attempts,
        conflicting_assessment_ids=[false_positive_id],
        notes=[
            "只有进入厨房构成有效业务状态变化。",
            "购物篮首次点击仅选中生成器，不能裁定再次点击是否生成。",
            "错误坐标合并没有触及声明对象，不能裁定同级二合一规则。",
            "旧 action-quality 样本把视觉变化提升成业务预期成立；人工业务复核与之冲突。",
            "两条低风险预测继续 pending；本记录不证明会玩、玩得好或达到10+有效动作/分钟。",
        ],
        updated_at=max(attempt.ended_at for attempt in attempts),
    )


def _assert_agent_receipt(receipt_id: str, *, expected_step_id: str) -> Path:
    digest = receipt_id.rsplit(".", 1)[-1]
    path = DEFAULT_AGENT_RECEIPT_DIR / digest[:2] / f"{digest}.json"
    if not path.is_file():
        raise ValueError(f"missing agent receipt: {path.as_posix()}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"agent receipt hash drift: {path.as_posix()}")
    document = json.loads(path.read_text(encoding="utf-8"))
    action = document.get("action") or {}
    if action.get("evidence_step_id") != expected_step_id:
        raise ValueError(f"agent receipt EvidenceStep drift: {receipt_id}")
    return path


def build_low_risk_prediction_round_two_attempt_log(
    reader: ObservatoryReader,
) -> ExecutionAttemptLogV1:
    attempts: list[ExecutionAttemptV1] = []
    for sequence, spec in enumerate(ROUND_TWO_ADJUDICATED_ATTEMPTS, start=1):
        row = reader.evidence(spec.step_id, expected_session_id=ROUND_TWO_ADJUDICATION_SESSION_ID)
        reader.assert_telemetry_event(
            spec.terminal_telemetry_id,
            expected_session_id=ROUND_TWO_ADJUDICATION_SESSION_ID,
        )
        if spec.receipt_id is None:
            raise ValueError(f"round-two attempt is missing its agent receipt: {spec.id}")
        receipt_path = _assert_agent_receipt(spec.receipt_id, expected_step_id=spec.step_id)
        action = row.body.get("action") or {}
        attempts.append(
            ExecutionAttemptV1(
                id=f"execution-attempt.romantic.low-risk-round-2.{spec.id}",
                sequence=sequence,
                environment_id=ENVIRONMENT_ID,
                session_id=ROUND_TWO_ADJUDICATION_SESSION_ID,
                route_id=ROUND_TWO_ADJUDICATION_ROUTE_ID,
                planned_action_id=spec.planned_action_id,
                action_kind=str(action.get("type") or "unknown"),
                started_at=row.started_at,
                ended_at=row.ended_at,
                outcome=spec.outcome,
                expectation_met=spec.expectation_met,
                business_state_changed=True,
                business_change=spec.business_change,
                observed_detail=spec.observed_detail,
                execution_receipt_kind="single_action_execution",
                telemetry_event_ids=[spec.terminal_telemetry_id],
                route_result_ids=[],
                canonical_source_gaps=[],
                evidence_refs=[
                    _evidence_reference(
                        row,
                        note=(
                            "该单步 act 由 EvidenceStep、terminal telemetry 与原始 agent receipt 共同闭合；"
                            f"receipt={receipt_path.as_posix()}。"
                        ),
                        additional_source_ids=[
                            spec.terminal_telemetry_id,
                            spec.receipt_id,
                        ],
                    )
                ],
            )
        )

    return ExecutionAttemptLogV1(
        id="execution-attempt-log.romantic.low-risk-prediction-round-2",
        game_id=GAME_ID,
        environment_id=ENVIRONMENT_ID,
        session_id=ROUND_TWO_ADJUDICATION_SESSION_ID,
        task_id=ADJUDICATION_TASK_ID,
        purpose="在第二轮新 canonical Session 中裁定两条低风险玩法预测。",
        benchmark_eligible=False,
        benchmark_exclusion_reason=(
            "本轮仅包含两个离散裁定动作，未预声明或运行高重复批次，不能用于10+有效动作/分钟验收。"
        ),
        prediction_ids=[
            "prediction.rr.generator-single-tap.v1",
            "prediction.rr.same-level-merge.v1",
        ],
        attempts=attempts,
        conflicting_assessment_ids=[ROUND_TWO_GENERATOR_RECEIPT_ID],
        notes=[
            "再次点击已选中的生成器会消耗1体力并生成一个基础产物。",
            "两个相邻同类同级面粉袋可二合一为面团（等级2），棋盘占用净减少1格。",
            "生成动作自动回执的 expectation_missed 为视觉阈值假阴性，历史回执原样保留并记录冲突。",
            "两条预测已 confirmed，但本轮不构成策略优越性或速度 benchmark。",
        ],
        updated_at=max(attempt.ended_at for attempt in attempts),
    )


def _write_model(path: Path, model: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_model_bytes(model))


def export_romantic_restaurant_baseline(
    database_path: str | Path = DEFAULT_DATABASE,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> tuple[Path, ...]:
    destination = Path(output_dir)
    with ObservatoryReader(database_path) as reader:
        high, semi_fixed = build_benchmarks(reader)
        dossiers = [
            build_merge_board_dossier(reader),
            build_order_dossier(reader),
            build_top_level_boundary_dossier(reader),
        ]
        registry = build_registry(dossiers)
        graph = build_content_graph(reader)
        strategy = build_strategy(reader, high, semi_fixed)
        round_one_attempt_log = build_low_risk_prediction_round_one_attempt_log(reader)
        round_two_attempt_log = build_low_risk_prediction_round_two_attempt_log(reader)

        source_models = {
            "system-registry.json": registry,
            "dossiers/rr-01-merge-board.json": dossiers[0],
            "dossiers/rr-02-order-economy-restoration.json": dossiers[1],
            "dossiers/rr-03-visible-top-level-boundaries.json": dossiers[2],
            "content-graph.json": graph,
            "strategies/baseline.json": strategy,
            "benchmarks/high-repetition-baseline.json": high,
            "benchmarks/semi-fixed-baseline.json": semi_fixed,
            "execution-attempts/low-risk-prediction-round-1.json": round_one_attempt_log,
            "execution-attempts/low-risk-prediction-round-2.json": round_two_attempt_log,
        }
        preparation = build_new_session_preparation(
            reader=reader, dossiers=dossiers, source_models=source_models
        )
    outputs = tuple((destination / path, model) for path, model in source_models.items()) + (
        (destination / "next-session-preparation.json", preparation),
    )
    written: list[Path] = []
    for path, model in outputs:
        _write_model(path, model)
        validate_model_path(path)
        written.append(path)
    return tuple(written)


def all_verified_evidence_step_ids(models: Iterable[Any]) -> set[str]:
    """Collect verified fact EvidenceStep ids for reconciliation tests."""

    return {step_id for step_id, _ in all_verified_evidence_step_scopes(models)}


def all_verified_evidence_step_scopes(models: Iterable[Any]) -> set[tuple[str, str]]:
    """Collect verified EvidenceStep ids with their declared environment scope."""

    found: dict[str, str] = {}

    def visit(value: Any) -> None:
        if isinstance(value, FactRecordV1):
            if value.status == "verified":
                for reference in value.evidence_refs:
                    for step_id in reference.evidence_step_ids:
                        previous = found.setdefault(step_id, reference.environment_id)
                        if previous != reference.environment_id:
                            raise ValueError(
                                f"EvidenceStep declared in multiple environments: {step_id}"
                            )
            return
        model_fields = getattr(type(value), "model_fields", None)
        if model_fields is not None:
            for field_name in model_fields:
                visit(getattr(value, field_name))
        elif isinstance(value, Mapping):
            for item in value.values():
                visit(item)
        elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            for item in value:
                visit(item)

    for model in models:
        visit(model)
    return {(step_id, environment_id) for step_id, environment_id in found.items()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    paths = export_romantic_restaurant_baseline(args.database, args.output)
    for path in paths:
        print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
