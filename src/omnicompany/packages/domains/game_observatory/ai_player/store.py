"""Persistent, environment-isolated storage for AI-player contracts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..models import (
    LIFECYCLE_ACTION_TYPES,
    ArtifactRef,
    EvidenceRun,
    EvidenceStep,
    NormalizedAction,
    SourcePixelRect,
    SourceSnapshot,
    utc_now,
)
from ..store import ObservatoryStore
from .account_metric_observation import (
    AccountMetricDefinitionV1,
    AccountMetricDeltaDerivationV1,
    metric_delta_fingerprint,
    validate_account_metric_derivation,
)
from .contracts import (
    AccountActionPolicyV1,
    ActionQualitySampleV1,
    EnvironmentPromotionV1,
    EnvironmentSelectionV1,
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    GameplayCandidateV1,
    GuideKnowledgeV1,
    MemoryRecordV1,
    NavigationFrameV1,
    NavigationStackV1,
    PlayerIterationAssessmentV1,
    PlayerSoftSignalReviewRequestV1,
    PlayerSoftSignalReviewV1,
    SemanticStateV1,
    SessionCapsuleV1,
    SkillRunV1,
    SkillValidationV1,
    SkillVersionV1,
    SpeechEventV1,
    SpeechIntentV1,
    StateAssignmentV1,
    StateObservationV1,
    StateRecognitionDecisionV1,
    TransitionEdgeV1,
)
from .guide_refresh import (
    GuideRefreshReceiptV1,
    GuideRefreshRequestV1,
    GuideResearchResultBundleV1,
    GuideVersionReferenceV1,
)
from .skill_validation import derive_skill_validation
from .skill_attestation import SkillValidatorTrustStore
from .planner_measurement import PlannerMeasurementTrustStore
from .soft_signal_attestation import PlayerSoftSignalReviewerTrustStore
from .text_integrity import (
    DEGRADED_ENCODING_HEALTH,
    RAW_SOURCE_FIELD_NAMES,
    SEMANTIC_FIELD_NAMES,
    CanonicalTextCorrectionV1,
    TextProjectionResult,
    canonical_text_issue,
    canonical_text_sha256,
    project_canonical_record_text,
    project_current_text,
    recover_latin1_utf8,
    value_at_json_path,
)
from .remediation import (
    TIER1_REMEDIATION_GATE_ID,
    Tier1RemediationRegressionFixtureV1,
    Tier1RemediationRegressionResultV1,
    Tier1RemediationVerificationV1,
    Tier1RemediationVerifierTrustStore,
    iteration_assessment_fingerprint,
    run_tier1_remediation_fixture,
    stable_tier1_remediation_verification_id,
    tier1_remediation_policy_fingerprint,
)


AI_PLAYER_SCHEMA_VERSION = 23

_KNOWN_ROUTE_SAFETY_LEVELS = (
    "read_only",
    "reversible",
    "progression",
    "social",
    "economic",
    "restricted",
)


@dataclass(frozen=True)
class KnownRouteSkillRunSummary:
    """The small immutable SkillRun projection needed by deterministic routing."""

    run_id: str
    skill_version_id: str
    outcome: str
    objective_success: bool
    validation_passed: bool
    false_success: bool
    safety_violation_count: int
    recovery_succeeded: bool
    decision_latency_ms: float
    baseline_decision_latency_ms: float
    baseline_model_input_tokens: int
    semantic_sedimentation_settled: bool


@dataclass(frozen=True)
class KnownRouteAliasMemory:
    """A route-alias projection that avoids loading unrelated memory payloads."""

    status: str
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True)
class TransitionEdgeProjectionRow:
    """Small latest-edge projection for task routing and reuse health reads."""

    id: str
    from_state_id: str
    to_state_id: str | None
    outcome: str
    action_type: str | None
    created_at: str
    version: int


@dataclass(frozen=True)
class StateRegionFingerprintProjectionRow:
    """Compact active-assignment projection used by A2 candidate matching."""

    state_id: str
    region_fingerprints: dict[str, str]


LEGACY_SKILL_TERMINAL_INVALIDATION_REASON = (
    "旧技能缺少结构化终态，已在契约升级中停用；保留来源，需重新验证后生成新版本。"
)


def _register_canonical_state_locked(
    connection: sqlite3.Connection,
    state: SemanticStateV1,
) -> None:
    """Project one active state into the unique semantic-fingerprint registry."""

    if state.status not in {"accepted", "candidate"}:
        return
    existing = connection.execute(
        """
        SELECT canonical_state_id FROM ai_player_canonical_state_registry
        WHERE environment_id=? AND semantic_fingerprint=?
        """,
        (state.environment_id, state.semantic_fingerprint),
    ).fetchone()
    now = state.created_at
    if existing is None:
        body = json.dumps(
            {
                "schema": "game-observatory.ai-player.canonical-state-registry.v1",
                "environment_id": state.environment_id,
                "semantic_fingerprint": state.semantic_fingerprint,
                "canonical_state_id": state.id,
                "selection": "first_active_then_adjudicated",
                "created_at": now,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO ai_player_canonical_state_registry(
                environment_id,semantic_fingerprint,canonical_state_id,
                body_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (state.environment_id, state.semantic_fingerprint, state.id, body, now, now),
        )
        return
    canonical_state_id = str(existing["canonical_state_id"])
    if canonical_state_id == state.id:
        return
    body = json.dumps(
        {
            "schema": "game-observatory.ai-player.canonical-state-alias.v1",
            "environment_id": state.environment_id,
            "state_id": state.id,
            "canonical_state_id": canonical_state_id,
            "semantic_fingerprint": state.semantic_fingerprint,
            "reason": "same_semantic_fingerprint_on_insert",
            "created_at": now,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO ai_player_canonical_state_aliases(
            environment_id,state_id,canonical_state_id,semantic_fingerprint,
            reason,body_json,created_at
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (
            state.environment_id,
            state.id,
            canonical_state_id,
            state.semantic_fingerprint,
            "same_semantic_fingerprint_on_insert",
            body,
            now,
        ),
    )


def _active_semantic_states_for_feature_hash(
    connection: sqlite3.Connection,
    environment_id: str,
    feature_hash: str,
) -> list[SemanticStateV1]:
    """Resolve one previously assigned exact screen without decoding state history."""

    assignment_rows = connection.execute(
        """
        SELECT assignment.observation_id, assignment.state_id,
               assignment.status, assignment.version
        FROM ai_player_state_observations AS observation
        JOIN ai_player_state_assignments AS assignment
          ON assignment.environment_id=observation.environment_id
         AND assignment.observation_id=observation.id
        WHERE observation.environment_id=? AND observation.feature_hash=?
        ORDER BY assignment.observation_id, assignment.version DESC
        """,
        (environment_id, feature_hash),
    ).fetchall()
    seen_observation_ids: set[str] = set()
    state_ids: set[str] = set()
    for row in assignment_rows:
        observation_id = str(row["observation_id"])
        if observation_id in seen_observation_ids:
            continue
        seen_observation_ids.add(observation_id)
        if row["status"] == "active":
            state_ids.add(str(row["state_id"]))
    if not state_ids:
        return []
    placeholders = ",".join("?" for _ in state_ids)
    alias_rows = connection.execute(
        f"""
        SELECT state_id,canonical_state_id
        FROM ai_player_canonical_state_aliases
        WHERE environment_id=? AND state_id IN ({placeholders})
        """,  # noqa: S608 - placeholders are generated, values remain parameterized
        (environment_id, *sorted(state_ids)),
    ).fetchall()
    aliases = {str(row["state_id"]): str(row["canonical_state_id"]) for row in alias_rows}
    state_ids = {aliases.get(state_id, state_id) for state_id in state_ids}
    placeholders = ",".join("?" for _ in state_ids)
    state_rows = connection.execute(
        f"""
        SELECT id, status, body_json, version
        FROM ai_player_semantic_states
        WHERE environment_id=? AND id IN ({placeholders})
        ORDER BY id, version DESC
        """,  # noqa: S608 - placeholders are generated, values remain parameterized
        (environment_id, *sorted(state_ids)),
    ).fetchall()
    latest_states: dict[str, SemanticStateV1] = {}
    seen_state_ids: set[str] = set()
    for row in state_rows:
        state_id = str(row["id"])
        if state_id in seen_state_ids:
            continue
        seen_state_ids.add(state_id)
        if row["status"] not in {"candidate", "accepted"}:
            continue
        latest_states[state_id] = SemanticStateV1.model_validate_json(row["body_json"])
    return [latest_states[state_id] for state_id in sorted(latest_states)]


@dataclass(frozen=True)
class StateTransitionIntent:
    """One deterministic edge to materialize after both endpoint observations classify."""

    id: str
    before_observation_id: str
    after_observation_id: str
    action: NormalizedAction
    target_bounds: SourcePixelRect | None
    expected_change: str
    evidence_refs: tuple[EvidenceReferenceV1, ...]
    created_at: str
    authoritative_before_state_id: str | None = None
    authoritative_after_state_id: str | None = None


@dataclass(frozen=True)
class SkillContractMigrationRecord:
    """Immutable provenance for one legacy skill contract invalidated by migration."""

    migration_id: str
    environment_id: str
    skill_version_id: str
    skill_id: str
    version: int
    original_body_json: str
    original_body_sha256: str
    original_content_sha256: str
    original_status: str
    migrated_body_sha256: str
    migrated_content_sha256: str
    migrated_status: str
    reason_code: str
    migrated_at: str


class AIPlayerStore:
    """AI-player tables sharing an Observatory database, but not its private internals."""

    def __init__(
        self,
        observatory_store: ObservatoryStore,
        *,
        skill_validator_trust_store: SkillValidatorTrustStore | None = None,
        soft_signal_reviewer_trust_store: PlayerSoftSignalReviewerTrustStore | None = None,
        remediation_verifier_trust_store: Tier1RemediationVerifierTrustStore | None = None,
        planner_measurement_trust_store: PlannerMeasurementTrustStore | None = None,
    ) -> None:
        self.observatory_store = observatory_store
        self.db_path = Path(observatory_store.db_path)
        self.skill_validator_trust_store = (
            skill_validator_trust_store or SkillValidatorTrustStore.from_environment()
        )
        self.soft_signal_reviewer_trust_store = (
            soft_signal_reviewer_trust_store
            or PlayerSoftSignalReviewerTrustStore.from_environment()
        )
        self.remediation_verifier_trust_store = (
            remediation_verifier_trust_store
            or Tier1RemediationVerifierTrustStore.from_environment()
        )
        self.planner_measurement_trust_store = (
            planner_measurement_trust_store or PlannerMeasurementTrustStore.from_environment()
        )
        self._write_lock = threading.RLock()
        self._connection_local = threading.local()
        self._text_corrections_cache: tuple[CanonicalTextCorrectionV1, ...] | None = None
        self._text_projection_applied_ids: set[str] = set()
        self._text_projection_unrecoverable_ids: set[str] = set()
        self._text_projection_unregistered_keys: set[str] = set()
        self._text_projection_hidden_keys: set[str] = set()
        self._skill_run_provenance_cache: tuple[str, tuple[tuple[int, int], ...], str] | None = None
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        current = getattr(self._connection_local, "connection", None)
        if current is not None:
            yield current
            return
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        self._connection_local.connection = connection
        try:
            with connection:
                yield connection
        finally:
            self._connection_local.connection = None
            connection.close()

    @contextmanager
    def read_session(self) -> Iterator[None]:
        """Reuse both canonical-store connections for a bounded read phase."""

        with self.observatory_store.read_session(), self._connection():
            yield

    def initialize(self) -> None:
        """Apply the independent AI-player schema exactly once."""

        with self._write_lock, self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_player_schema_version(
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    version INTEGER NOT NULL CHECK(version >= 0),
                    applied_at TEXT NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT version FROM ai_player_schema_version WHERE id=1"
            ).fetchone()
            database_version = int(row["version"]) if row else 0
            if database_version > AI_PLAYER_SCHEMA_VERSION:
                raise RuntimeError(
                    "AI-player schema version "
                    f"{database_version} is newer than supported version "
                    f"{AI_PLAYER_SCHEMA_VERSION}"
                )
            self._drop_text_integrity_triggers(
                connection,
                all_guards=database_version < 19,
            )
            migration_dir = Path(__file__).with_name("migrations")
            migrations = {
                1: migration_dir / "0001_stage0.sql",
                2: migration_dir / "0002_guide_knowledge.sql",
                3: migration_dir / "0003_state_recognition.sql",
                4: migration_dir / "0004_environment_lineage.sql",
                5: migration_dir / "0005_ai_player_sessions.sql",
                6: migration_dir / "0006_sanguo_daily_continuity.sql",
                7: migration_dir / "0007_sanguo_daily_integrity.sql",
                8: migration_dir / "0008_skill_lifecycle.sql",
                9: migration_dir / "0009_gameplay_account_speech.sql",
                10: migration_dir / "0010_player_iteration.sql",
                11: migration_dir / "0011_player_soft_signal_reviews.sql",
                12: migration_dir / "0012_account_metric_observations.sql",
                13: migration_dir / "0013_iteration_remediation.sql",
                14: migration_dir / "0014_planner_measurements.sql",
                15: migration_dir / "0015_text_integrity.sql",
                16: migration_dir / "0016_session_leases.sql",
                17: migration_dir / "0017_text_reconstruction.sql",
                18: migration_dir / "0018_state_adjudications.sql",
                19: migration_dir / "0019_guide_refresh_queue.sql",
                20: migration_dir / "0020_skill_expected_state_contract.sql",
                21: migration_dir / "0021_navigation_stack.sql",
                22: migration_dir / "0022_skill_locator_mobility_contract.sql",
                23: migration_dir / "0023_operation_memory_runtime_authority.sql",
            }
            for version in range(database_version + 1, AI_PLAYER_SCHEMA_VERSION + 1):
                migration_path = migrations[version]
                applied_at_raw = utc_now()
                if version == 20:
                    self._apply_skill_expected_state_contract_migration(
                        connection,
                        migration_path=migration_path,
                        applied_at=applied_at_raw,
                    )
                    continue
                if version == 22:
                    self._apply_skill_locator_mobility_contract_migration(
                        connection,
                        migration_path=migration_path,
                        applied_at=applied_at_raw,
                    )
                    continue
                applied_at = applied_at_raw.replace("'", "''")
                migration_sql = self._migration_sql(
                    connection,
                    version=version,
                    migration_path=migration_path,
                    migration_dir=migration_dir,
                )
                transaction_sql = f"""
                    BEGIN IMMEDIATE;
                    {migration_sql}
                    INSERT INTO ai_player_schema_version(id, version, applied_at)
                    VALUES(1, {version}, '{applied_at}')
                    ON CONFLICT(id) DO UPDATE SET
                        version=excluded.version,
                        applied_at=excluded.applied_at
                    ;
                    COMMIT;
                """
                try:
                    connection.executescript(transaction_sql)
                except Exception:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
            from .operation_memory import backfill_session_lifecycle_locked

            backfill_session_lifecycle_locked(connection)
            self._ensure_text_integrity_triggers(connection)

    @staticmethod
    def _drop_text_integrity_triggers(
        connection: sqlite3.Connection,
        *,
        all_guards: bool,
    ) -> None:
        """Remove old guards before schema repair or connection-independent rebuild."""

        rows = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='trigger' AND name LIKE 'text_guard_%'"
        ).fetchall()
        for row in rows:
            trigger_sql = str(row["sql"] or "")
            if not all_guards and "ai_player_canonical_text_is_valid" not in trigger_sql:
                continue
            trigger = str(row["name"])
            connection.execute(f'DROP TRIGGER IF EXISTS "{trigger}"')

    @staticmethod
    def _damaged_text_sql(value_expression: str) -> str:
        checks = [
            f"instr({value_expression}, '???') > 0",
            f"instr({value_expression}, char(65533)) > 0",
        ]
        checks.extend(
            f"instr({value_expression}, char({codepoint})) > 0"
            for codepoint in range(0x80, 0xA0)
        )
        return "(" + " OR ".join(checks) + ")"

    @staticmethod
    def _sql_string_list(values: Sequence[str]) -> str:
        return ", ".join("'" + value.replace("'", "''") + "'" for value in values)

    @classmethod
    def _damaged_json_sql(cls, column_expression: str) -> str:
        safe_json = (
            f"CASE WHEN json_valid({column_expression}) "
            f"THEN {column_expression} ELSE '{{}}' END"
        )
        semantic_names = cls._sql_string_list(sorted(SEMANTIC_FIELD_NAMES))
        raw_names = cls._sql_string_list(sorted(RAW_SOURCE_FIELD_NAMES))
        degraded_health = cls._sql_string_list(sorted(DEGRADED_ENCODING_HEALTH))
        damaged_value = cls._damaged_text_sql("CAST(damaged.value AS TEXT)")
        semantic_path_checks: list[str] = [
            f"CAST(damaged.key AS TEXT) IN ({semantic_names})"
        ]
        for field_name in sorted(SEMANTIC_FIELD_NAMES):
            token = "." + field_name
            semantic_path_checks.extend(
                (
                    f"damaged.fullkey = '${token}'",
                    f"instr(damaged.fullkey, '{token}.') > 0",
                    f"instr(damaged.fullkey, '{token}[') > 0",
                )
            )
        semantic_path_sql = "(" + " OR ".join(semantic_path_checks) + ")"
        raw_key_sql = f"CAST(damaged.key AS TEXT) IN ({raw_names})"
        health_sql = f"""
            EXISTS(
                SELECT 1
                FROM json_tree({safe_json}) AS health
                WHERE health.path = damaged.path
                  AND CAST(health.key AS TEXT) = 'encoding_health'
                  AND (
                    (
                        health.type = 'text'
                        AND lower(CAST(health.value AS TEXT)) IN ({degraded_health})
                    )
                    OR (
                        health.type = 'object'
                        AND EXISTS(
                            SELECT 1
                            FROM json_tree({safe_json}) AS health_status
                            WHERE health_status.path = health.fullkey
                              AND CAST(health_status.key AS TEXT) = 'status'
                              AND health_status.type = 'text'
                              AND lower(CAST(health_status.value AS TEXT))
                                  IN ({degraded_health})
                        )
                    )
                  )
            )
        """
        return f"""
            (
                json_valid({column_expression}) = 0
                OR EXISTS(
                    SELECT 1
                    FROM json_tree({safe_json}) AS damaged
                    WHERE damaged.type = 'text'
                      AND {damaged_value}
                      AND (
                        ({raw_key_sql} AND NOT {health_sql})
                        OR (NOT ({raw_key_sql}) AND {semantic_path_sql})
                      )
                )
            )
        """

    @classmethod
    def _ensure_text_integrity_triggers(cls, connection: sqlite3.Connection) -> None:
        """Install pure-SQL guards that work from every SQLite connection."""

        excluded_tables = {
            "ai_player_schema_version",
            "ai_player_text_corrections",
        }
        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'ai_player_%' ORDER BY name"
        ).fetchall()
        for table_row in tables:
            table = str(table_row["name"])
            if table in excluded_tables:
                continue
            immutable_update_guard = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=? "
                "AND name NOT LIKE 'text_guard_%' "
                "AND lower(sql) LIKE '%before update%' LIMIT 1",
                (table,),
            ).fetchone()
            columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            for column_row in columns:
                column = str(column_row["name"])
                if str(column_row["type"] or "").upper() != "TEXT":
                    continue
                if column.endswith("_json"):
                    invalid_sql = cls._damaged_json_sql(f'NEW."{column}"')
                elif column in SEMANTIC_FIELD_NAMES:
                    invalid_sql = cls._damaged_text_sql(f'NEW."{column}"')
                else:
                    continue
                trigger_stem = f"text_guard_{table}_{column}"
                for operation, suffix in (("INSERT", "insert"), ("UPDATE", "update")):
                    if operation == "UPDATE" and immutable_update_guard is not None:
                        connection.execute(
                            f'DROP TRIGGER IF EXISTS "{trigger_stem}_update"'
                        )
                        continue
                    operation_sql = (
                        operation
                        if operation == "INSERT"
                        else f'UPDATE OF "{column}"'
                    )
                    connection.execute(
                        f"""
                        CREATE TRIGGER IF NOT EXISTS "{trigger_stem}_{suffix}"
                        BEFORE {operation_sql} ON "{table}"
                        WHEN {invalid_sql}
                        BEGIN
                            SELECT RAISE(
                                ABORT,
                                'AI-player canonical semantic text failed integrity validation'
                            );
                        END
                        """
                    )

    @staticmethod
    def _migration_sql(
        connection: sqlite3.Connection,
        *,
        version: int,
        migration_path: Path,
        migration_dir: Path,
    ) -> str:
        """Render one migration, repairing a partially applied v7 safely."""

        if version != 7:
            return migration_path.read_text(encoding="utf-8")
        table_name = "ai_player_sanguo_daily_continuity_events"
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        prefix = ""
        if table_exists is None:
            prefix = (migration_dir / "0006_sanguo_daily_continuity.sql").read_text(
                encoding="utf-8"
            )
            existing_columns: set[str] = set()
        else:
            existing_columns = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
        required_columns = {
            "operation": "TEXT",
            "command_json": "TEXT",
            "previous_event_sha256": "TEXT",
            "event_sha256": "TEXT",
        }
        statements = [prefix] if prefix else []
        statements.extend(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type};"
            for column_name, column_type in required_columns.items()
            if column_name not in existing_columns
        )
        statements.append(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_player_sanguo_daily_event_hash
                ON ai_player_sanguo_daily_continuity_events(event_sha256)
                WHERE event_sha256 IS NOT NULL;
            """
        )
        return "\n".join(statements)

    @classmethod
    def _apply_skill_expected_state_contract_migration(
        cls,
        connection: sqlite3.Connection,
        *,
        migration_path: Path,
        applied_at: str,
    ) -> None:
        """Atomically invalidate verified legacy hashes and retain their raw provenance."""

        from .skills import build_skill_version

        try:
            connection.execute("BEGIN IMMEDIATE")
            cls._execute_sql_script_in_transaction(
                connection,
                migration_path.read_text(encoding="utf-8"),
            )
            rows = connection.execute(
                """
                SELECT environment_id, id, skill_id, version, status, body_json
                FROM ai_player_skill_versions
                ORDER BY environment_id, skill_id, version, id
                """
            ).fetchall()
            for row in rows:
                original_body = str(row["body_json"])
                try:
                    body = json.loads(original_body)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "cannot migrate malformed legacy skill JSON: "
                        f"{row['environment_id']}/{row['id']}"
                    ) from exc
                if not isinstance(body, dict):
                    raise RuntimeError(
                        "cannot migrate non-object legacy skill JSON: "
                        f"{row['environment_id']}/{row['id']}"
                    )
                if (
                    body.get("environment_id") != row["environment_id"]
                    or body.get("id") != row["id"]
                    or body.get("skill_id") != row["skill_id"]
                    or body.get("version") != row["version"]
                    or body.get("status") != row["status"]
                ):
                    raise RuntimeError(
                        "legacy skill row columns do not match canonical JSON: "
                        f"{row['environment_id']}/{row['id']}"
                    )

                original_hash = str(body.get("content_sha256") or "")
                steps = body.get("steps")
                if not isinstance(steps, list):
                    raise RuntimeError(
                        "legacy skill steps are missing: "
                        f"{row['environment_id']}/{row['id']}"
                    )
                lacks_structured_terminal = not any(
                    isinstance(step, dict)
                    and step.get("kind") == "assert"
                    and isinstance(step.get("expected_state_id"), str)
                    and bool(step["expected_state_id"].strip())
                    for step in steps
                )

                projected_values = {
                    **body,
                    "status": "invalidated",
                    "validation_run_ids": [],
                    "validation_id": None,
                    "independent_reset_count": 0,
                    "visual_variant_count": 0,
                    "failure_recovery_verified": False,
                    "invalidation_reason": LEGACY_SKILL_TERMINAL_INVALIDATION_REASON,
                }
                try:
                    projected = build_skill_version(**projected_values)
                except Exception as exc:
                    raise RuntimeError(
                        "legacy skill cannot be normalized safely: "
                        f"{row['environment_id']}/{row['id']}"
                    ) from exc
                current_hash = projected.content_sha256
                legacy_payload = projected.content_payload()
                for step in legacy_payload["steps"]:
                    step.pop("expected_state_id", None)
                legacy_hash = hashlib.sha256(
                    json.dumps(
                        legacy_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()

                is_legacy_hash = lacks_structured_terminal and original_hash == legacy_hash
                is_unsafe_current_trusted = (
                    lacks_structured_terminal
                    and original_hash == current_hash
                    and body.get("status") in {"preferred", "validated"}
                )
                if original_hash == current_hash and not is_unsafe_current_trusted:
                    continue
                if not (is_legacy_hash or is_unsafe_current_trusted):
                    raise RuntimeError(
                        "skill hash is neither current nor trusted legacy content: "
                        f"{row['environment_id']}/{row['id']}"
                    )

                migrated_body = projected.model_dump_json(by_alias=True)
                original_body_sha256 = hashlib.sha256(
                    original_body.encode("utf-8")
                ).hexdigest()
                migrated_body_sha256 = hashlib.sha256(
                    migrated_body.encode("utf-8")
                ).hexdigest()
                migration_id = hashlib.sha256(
                    (
                        f"{row['environment_id']}:{row['id']}:"
                        f"{original_body_sha256}:{migrated_body_sha256}"
                    ).encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """
                    INSERT INTO ai_player_skill_contract_migrations(
                        migration_id, environment_id, skill_version_id, skill_id, version,
                        original_body_json, original_body_sha256, original_content_sha256,
                        original_status, migrated_body_sha256, migrated_content_sha256,
                        migrated_status, reason_code, migrated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        migration_id,
                        row["environment_id"],
                        row["id"],
                        row["skill_id"],
                        row["version"],
                        original_body,
                        original_body_sha256,
                        original_hash,
                        row["status"],
                        migrated_body_sha256,
                        current_hash,
                        "invalidated",
                        "legacy_missing_structured_terminal_state",
                        applied_at,
                    ),
                )
                connection.execute(
                    """
                    UPDATE ai_player_skill_versions
                    SET status='invalidated', body_json=?
                    WHERE environment_id=? AND id=?
                    """,
                    (migrated_body, row["environment_id"], row["id"]),
                )
            connection.execute(
                """
                INSERT INTO ai_player_schema_version(id, version, applied_at)
                VALUES(1, 20, ?)
                ON CONFLICT(id) DO UPDATE SET
                    version=excluded.version,
                    applied_at=excluded.applied_at
                """,
                (applied_at,),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

    @classmethod
    def _apply_skill_locator_mobility_contract_migration(
        cls,
        connection: sqlite3.Connection,
        *,
        migration_path: Path,
        applied_at: str,
    ) -> None:
        """Add explicit locator mobility fields without distrusting unchanged skills."""

        from .skills import build_skill_version

        new_locator_fields = {
            "mobility",
            "reference_artifact_id",
            "search_region",
            "match_threshold",
        }
        try:
            connection.execute("BEGIN IMMEDIATE")
            cls._execute_sql_script_in_transaction(
                connection,
                migration_path.read_text(encoding="utf-8"),
            )
            rows = connection.execute(
                """
                SELECT environment_id, id, skill_id, version, status, body_json
                FROM ai_player_skill_versions
                ORDER BY environment_id, skill_id, version, id
                """
            ).fetchall()
            for row in rows:
                original_body = str(row["body_json"])
                try:
                    body = json.loads(original_body)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "cannot migrate malformed skill locator JSON: "
                        f"{row['environment_id']}/{row['id']}"
                    ) from exc
                if not isinstance(body, dict):
                    raise RuntimeError(
                        "cannot migrate non-object skill locator JSON: "
                        f"{row['environment_id']}/{row['id']}"
                    )
                if (
                    body.get("environment_id") != row["environment_id"]
                    or body.get("id") != row["id"]
                    or body.get("skill_id") != row["skill_id"]
                    or body.get("version") != row["version"]
                    or body.get("status") != row["status"]
                ):
                    raise RuntimeError(
                        "skill locator row columns do not match canonical JSON: "
                        f"{row['environment_id']}/{row['id']}"
                    )

                original_hash = str(body.get("content_sha256") or "")
                try:
                    projected = build_skill_version(**body)
                except Exception as exc:
                    raise RuntimeError(
                        "skill locator contract cannot be normalized safely: "
                        f"{row['environment_id']}/{row['id']}"
                    ) from exc
                current_hash = projected.content_sha256
                if original_hash == current_hash:
                    continue

                legacy_payload = projected.content_payload()
                for locator in legacy_payload["locators"]:
                    for field_name in new_locator_fields:
                        locator.pop(field_name, None)
                legacy_hash = hashlib.sha256(
                    json.dumps(
                        legacy_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if original_hash != legacy_hash:
                    raise RuntimeError(
                        "skill locator hash is neither current nor trusted legacy content: "
                        f"{row['environment_id']}/{row['id']}"
                    )

                migrated_body = projected.model_dump_json(by_alias=True)
                original_body_sha256 = hashlib.sha256(
                    original_body.encode("utf-8")
                ).hexdigest()
                migrated_body_sha256 = hashlib.sha256(
                    migrated_body.encode("utf-8")
                ).hexdigest()
                migration_id = hashlib.sha256(
                    (
                        f"{row['environment_id']}:{row['id']}:locator-mobility:"
                        f"{original_body_sha256}:{migrated_body_sha256}"
                    ).encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """
                    INSERT INTO ai_player_skill_locator_contract_migrations(
                        migration_id, environment_id, skill_version_id, skill_id, version,
                        original_body_json, original_body_sha256, original_content_sha256,
                        migrated_body_sha256, migrated_content_sha256, status, migrated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        migration_id,
                        row["environment_id"],
                        row["id"],
                        row["skill_id"],
                        row["version"],
                        original_body,
                        original_body_sha256,
                        original_hash,
                        migrated_body_sha256,
                        current_hash,
                        row["status"],
                        applied_at,
                    ),
                )
                connection.execute(
                    """
                    UPDATE ai_player_skill_versions
                    SET body_json=?
                    WHERE environment_id=? AND id=?
                    """,
                    (migrated_body, row["environment_id"], row["id"]),
                )
            connection.execute(
                """
                INSERT INTO ai_player_schema_version(id, version, applied_at)
                VALUES(1, 22, ?)
                ON CONFLICT(id) DO UPDATE SET
                    version=excluded.version,
                    applied_at=excluded.applied_at
                """,
                (applied_at,),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

    @staticmethod
    def _execute_sql_script_in_transaction(
        connection: sqlite3.Connection,
        script: str,
    ) -> None:
        """Execute complete SQLite statements without the implicit commit of executescript."""

        buffer = ""
        for line in script.splitlines(keepends=True):
            buffer += line
            if sqlite3.complete_statement(buffer):
                statement = buffer.strip()
                if statement:
                    connection.execute(statement)
                buffer = ""
        if buffer.strip():
            raise RuntimeError("incomplete AI-player migration SQL")

    @property
    def schema_version(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT version FROM ai_player_schema_version WHERE id=1"
            ).fetchone()
        return int(row["version"]) if row else 0

    def append_text_correction(
        self,
        correction: CanonicalTextCorrectionV1,
    ) -> CanonicalTextCorrectionV1:
        """Append a hash-bound projection overlay without changing source records."""

        with self._write_lock, self._connection() as connection:
            source_value = self._text_correction_source_value(connection, correction)
            if not isinstance(source_value, str):
                raise ValueError("text correction source path does not resolve to a string")
            if canonical_text_sha256(source_value) != correction.original_sha256:
                raise ValueError("text correction source hash no longer matches the raw record")
            issue = canonical_text_issue(source_value)
            if issue is None:
                raise ValueError("text correction source is already clean")
            if correction.status == "recovered":
                recovered = recover_latin1_utf8(source_value)
                if recovered is None or recovered != correction.projected_text:
                    raise ValueError(
                        "recovered text did not pass byte round-trip and readability checks"
                    )
            elif correction.status == "reconstructed":
                if not correction.basis_reference_ids:
                    raise ValueError(
                        "reconstructed text requires explicit evidence basis references"
                    )
            body = correction.model_dump_json(by_alias=True)
            record_key_json = json.dumps(
                correction.record_key,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            existing = connection.execute(
                "SELECT body_json FROM ai_player_text_corrections WHERE id=?",
                (correction.id,),
            ).fetchone()
            if existing is not None:
                if str(existing["body_json"]) != body:
                    raise ValueError("text correction id already contains different content")
                return CanonicalTextCorrectionV1.model_validate_json(existing["body_json"])
            connection.execute(
                """
                INSERT INTO ai_player_text_corrections(
                    id, source_table, record_key_json, source_column, field_path,
                    original_sha256, status, projected_text, diagnosis, created_by,
                    body_json, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    correction.id,
                    correction.source_table,
                    record_key_json,
                    correction.source_column,
                    correction.field_path,
                    correction.original_sha256,
                    correction.status,
                    correction.projected_text,
                    correction.diagnosis,
                    correction.created_by,
                    body,
                    correction.created_at,
                ),
            )
            self._text_corrections_cache = None
        return correction

    @staticmethod
    def _text_correction_source_value(
        connection: sqlite3.Connection,
        correction: CanonicalTextCorrectionV1,
    ) -> Any:
        table = correction.source_table
        table_row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if table_row is None or table == "ai_player_text_corrections":
            raise ValueError("text correction source table is not canonical")
        columns = {
            str(row["name"]): int(row["pk"])
            for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
        if correction.source_column not in columns:
            raise ValueError("text correction source column does not exist")
        primary_key_columns = {name for name, position in columns.items() if position > 0}
        if set(correction.record_key) != primary_key_columns:
            raise ValueError("text correction record key must match the full primary key")
        ordered_keys = sorted(correction.record_key)
        where = " AND ".join(f'"{key}"=?' for key in ordered_keys)
        row = connection.execute(
            f'SELECT "{correction.source_column}" FROM "{table}" WHERE {where}',
            tuple(correction.record_key[key] for key in ordered_keys),
        ).fetchone()
        if row is None:
            raise ValueError("text correction source record does not exist")
        source_value: Any = row[correction.source_column]
        if correction.field_path == "$":
            return source_value
        try:
            payload = json.loads(source_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("text correction JSON source is invalid") from exc
        try:
            return value_at_json_path(payload, correction.field_path)
        except (IndexError, KeyError) as exc:
            raise ValueError("text correction field path does not exist") from exc

    def list_text_corrections(self) -> list[CanonicalTextCorrectionV1]:
        cached = self._text_corrections_cache
        if cached is not None:
            return list(cached)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT body_json FROM ai_player_text_corrections "
                "ORDER BY created_at, id"
            ).fetchall()
        loaded = tuple(
            CanonicalTextCorrectionV1.model_validate_json(row["body_json"])
            for row in rows
        )
        self._text_corrections_cache = loaded
        return list(loaded)

    def _record_text_projection(self, result: TextProjectionResult) -> None:
        self._text_projection_applied_ids.update(result.applied_correction_ids)
        self._text_projection_unrecoverable_ids.update(
            result.unrecoverable_correction_ids
        )
        self._text_projection_unregistered_keys.update(
            result.unregistered_damage_keys
        )
        self._text_projection_hidden_keys.update(result.hidden_source_keys)

    def _consume_text_projection(
        self,
        residual: TextProjectionResult,
    ) -> TextProjectionResult:
        self._record_text_projection(residual)
        merged = TextProjectionResult(
            payload=residual.payload,
            applied_correction_ids=tuple(sorted(self._text_projection_applied_ids)),
            unrecoverable_count=len(self._text_projection_unrecoverable_ids),
            unregistered_damage_count=len(self._text_projection_unregistered_keys),
            hidden_source_count=len(self._text_projection_hidden_keys),
            unrecoverable_correction_ids=tuple(
                sorted(self._text_projection_unrecoverable_ids)
            ),
            unregistered_damage_keys=tuple(
                sorted(self._text_projection_unregistered_keys)
            ),
            hidden_source_keys=tuple(sorted(self._text_projection_hidden_keys)),
        )
        self._text_projection_applied_ids.clear()
        self._text_projection_unrecoverable_ids.clear()
        self._text_projection_unregistered_keys.clear()
        self._text_projection_hidden_keys.clear()
        return merged

    def project_current_text_payload(
        self,
        payload: Any,
        *,
        include_health: bool = True,
    ) -> Any:
        """Return the safe current projection while retaining immutable raw rows."""

        residual = project_current_text(payload, self.list_text_corrections())
        result = self._consume_text_projection(residual)
        projected = result.payload
        if include_health and isinstance(projected, dict):
            projected = {**projected, "text_integrity": result.health()}
        return projected

    def project_canonical_record_payload(
        self,
        payload: Any,
        *,
        source_table: str,
        record_key: dict[str, str | int],
        source_column: str = "body_json",
        include_health: bool = False,
    ) -> Any:
        """Project one explicitly identified source record through exact overlays."""

        result = project_canonical_record_text(
            payload,
            self.list_text_corrections(),
            source_table=source_table,
            record_key=record_key,
            source_column=source_column,
        )
        self._record_text_projection(result)
        projected = result.payload
        if include_health and isinstance(projected, dict):
            projected = {**projected, "text_integrity": result.health()}
        return projected

    def resolve_evidence_references(
        self,
        references: Sequence[EvidenceReferenceV1],
        *,
        environment_scope: EnvironmentScopeV1 | None = None,
    ) -> dict[str, list[Any]]:
        """Resolve canonical evidence through public ObservatoryStore methods."""

        # A reference may bind several artifacts, runs and steps.  Reusing one
        # bounded connection per store keeps the exact same validation and file
        # hashing semantics while avoiding a fresh SQLite connection for every
        # individual object.  The session is deliberately local to one resolve;
        # no canonical object or artifact hash survives into a later operation.
        with self.read_session(), self.observatory_store.read_session():
            return self._resolve_evidence_references_in_session(
                references,
                environment_scope=environment_scope,
            )

    def _resolve_evidence_references_in_session(
        self,
        references: Sequence[EvidenceReferenceV1],
        *,
        environment_scope: EnvironmentScopeV1 | None = None,
    ) -> dict[str, list[Any]]:
        """Resolve references while both canonical read sessions are active."""

        resolved: dict[str, list[Any]] = {
            "artifact": [],
            "evidence_run": [],
            "evidence_step": [],
            "trace_run": [],
            "source_snapshot": [],
        }
        for reference in references:
            for artifact_id in reference.artifact_ids:
                artifact = self.observatory_store.get_artifact(artifact_id)
                self._append_resolved(resolved, "artifact", artifact_id, artifact)
                self._assert_reference_environment(
                    reference.environment_id,
                    "artifact",
                    artifact_id,
                    metadata=artifact.metadata,
                    evidence_reference=reference,
                )
                artifact_path = Path(artifact.path)
                if not artifact_path.is_file():
                    raise ValueError(f"dead evidence artifact file: {artifact_id}")
                if hashlib.sha256(artifact_path.read_bytes()).hexdigest() != artifact.sha256:
                    raise ValueError(f"evidence artifact hash mismatch: {artifact_id}")
            for evidence_run_id in reference.evidence_run_ids:
                evidence_run = self.observatory_store.get_evidence_run(evidence_run_id)
                self._append_resolved(
                    resolved,
                    "evidence_run",
                    evidence_run_id,
                    evidence_run,
                )
                self._assert_reference_environment(
                    reference.environment_id,
                    "evidence_run",
                    evidence_run_id,
                )
                self._assert_run_scope(
                    reference.environment_id,
                    "evidence_run",
                    evidence_run_id,
                    evidence_run,
                    environment_scope=environment_scope,
                )
            for step_id in reference.evidence_step_ids:
                step = self.observatory_store.get_evidence_step(step_id)
                self._append_resolved(resolved, "evidence_step", step_id, step)
                self._assert_reference_environment(
                    reference.environment_id,
                    "evidence_step",
                    step_id,
                )
                evidence_run = self.observatory_store.get_evidence_run(step.evidence_run_id)
                if evidence_run is None:
                    raise ValueError(
                        f"dead evidence reference: evidence_run:{step.evidence_run_id}"
                    )
                self._assert_run_scope(
                    reference.environment_id,
                    "evidence_run",
                    step.evidence_run_id,
                    evidence_run,
                    environment_scope=environment_scope,
                )
            for run_id in reference.trace_run_ids:
                run = self.observatory_store.get_run(run_id)
                self._append_resolved(resolved, "trace_run", run_id, run)
                self._assert_reference_environment(
                    reference.environment_id,
                    "trace_run",
                    run_id,
                )
                self._assert_run_scope(
                    reference.environment_id,
                    "trace_run",
                    run_id,
                    run,
                    environment_scope=environment_scope,
                )
            for source_id in reference.source_ids:
                snapshots = self.observatory_store.list_source_snapshots(source_id)
                if not snapshots:
                    raise ValueError(f"dead evidence reference: source:{source_id}")
                self._assert_reference_environment(
                    reference.environment_id,
                    "source",
                    source_id,
                )
                resolved["source_snapshot"].extend(snapshots)
        return resolved

    def _assert_reference_environment(
        self,
        environment_id: str,
        reference_kind: str,
        reference_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        evidence_reference: EvidenceReferenceV1 | None = None,
    ) -> None:
        metadata_environment = (metadata or {}).get("environment_id")
        if metadata_environment and not self._environment_can_inherit_evidence(
            metadata_environment,
            environment_id,
        ):
            raise ValueError(
                "cross-environment evidence reference: "
                f"{reference_kind}:{reference_id} belongs to {metadata_environment}"
            )
        with self._connection() as connection:
            origin = connection.execute(
                """
                SELECT origin_environment_id FROM ai_player_evidence_origins
                WHERE reference_kind=? AND reference_id=?
                """,
                (reference_kind, reference_id),
            ).fetchone()
        if origin and not self._environment_can_inherit_evidence(
            origin["origin_environment_id"],
            environment_id,
        ):
            raise ValueError(
                "cross-environment evidence reference: "
                f"{reference_kind}:{reference_id} originates in "
                f"{origin['origin_environment_id']}"
            )
        if (
            reference_kind == "artifact"
            and not metadata_environment
            and not origin
        ):
            metadata_run_id = (metadata or {}).get("evidence_run_id")
            metadata_step_id = (metadata or {}).get("evidence_step_id")
            fallback_bound = (
                evidence_reference is not None
                and metadata_run_id in evidence_reference.evidence_run_ids
                and metadata_step_id in evidence_reference.evidence_step_ids
                and self.has_exact_persisted_evidence_binding(
                    environment_id,
                    evidence_run_id=metadata_run_id,
                    evidence_step_id=metadata_step_id,
                    artifact_ids=evidence_reference.artifact_ids,
                )
            )
            if not fallback_bound:
                raise ValueError(f"artifact evidence lacks environment identity: {reference_id}")


    def _environment_can_inherit_evidence(
        self,
        evidence_environment_id: str,
        target_environment_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """Allow evidence to flow only from an environment to itself or a descendant."""

        if evidence_environment_id == target_environment_id:
            return True
        query = """
            WITH RECURSIVE descendants(id) AS (
                SELECT child_environment_id
                FROM ai_player_environment_lineage
                WHERE parent_environment_id=?
                UNION ALL
                SELECT lineage.child_environment_id
                FROM ai_player_environment_lineage AS lineage
                JOIN descendants ON lineage.parent_environment_id=descendants.id
            )
            SELECT 1 FROM descendants WHERE id=? LIMIT 1
        """
        if connection is not None:
            return connection.execute(
                query,
                (evidence_environment_id, target_environment_id),
            ).fetchone() is not None
        with self._connection() as owned_connection:
            return owned_connection.execute(
                query,
                (evidence_environment_id, target_environment_id),
            ).fetchone() is not None

    def _reference_claimed_by_environment(
        self,
        reference_kind: str,
        reference_id: str,
        environment_id: str,
    ) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM ai_player_entity_evidence
                WHERE reference_kind=? AND reference_id=? AND environment_id=?
                LIMIT 1
                """,
                (reference_kind, reference_id, environment_id),
            ).fetchone()
        return row is not None

    def has_exact_persisted_evidence_binding(
        self,
        environment_id: str,
        *,
        evidence_run_id: str,
        evidence_step_id: str,
        artifact_ids: Sequence[str] = (),
    ) -> bool:
        """Return whether one already-persisted reference binds the exact run and step."""

        with self._connection() as connection:
            return self._has_exact_persisted_evidence_binding(
                connection,
                environment_id,
                evidence_run_id=evidence_run_id,
                evidence_step_id=evidence_step_id,
                artifact_ids=artifact_ids,
            )

    @staticmethod
    def _has_exact_persisted_evidence_binding(
        connection: sqlite3.Connection,
        environment_id: str,
        *,
        evidence_run_id: str,
        evidence_step_id: str,
        artifact_ids: Sequence[str] = (),
    ) -> bool:
        rows = connection.execute(
            """
            SELECT DISTINCT reference_json FROM ai_player_entity_evidence
            WHERE environment_id=? AND reference_kind='evidence_run' AND reference_id=?
            """,
            (environment_id, evidence_run_id),
        ).fetchall()
        available_artifact_ids = set(artifact_ids)
        for row in rows:
            reference = EvidenceReferenceV1.model_validate_json(row["reference_json"])
            if reference.environment_id != environment_id:
                continue
            if evidence_run_id not in reference.evidence_run_ids:
                continue
            if evidence_step_id not in reference.evidence_step_ids:
                continue
            if reference.artifact_ids and not set(reference.artifact_ids).issubset(
                available_artifact_ids
            ):
                continue
            return True
        return False

    def _reference_origin_environment_id(
        self,
        reference_kind: str,
        reference_id: str,
    ) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT origin_environment_id FROM ai_player_evidence_origins
                WHERE reference_kind=? AND reference_id=?
                """,
                (reference_kind, reference_id),
            ).fetchone()
        return row["origin_environment_id"] if row else None

    def _run_artifacts_prove_environment(self, environment_id: str, run: Any) -> bool:
        artifact_ids = list(getattr(run, "artifact_ids", None) or [])
        if not artifact_ids:
            return False
        for artifact_id in artifact_ids:
            artifact = self.observatory_store.get_artifact(artifact_id)
            if artifact is None:
                raise ValueError(f"dead run artifact reference: {artifact_id}")
            artifact_environment = artifact.metadata.get("environment_id")
            if not artifact_environment or not self._environment_can_inherit_evidence(
                artifact_environment,
                environment_id,
            ):
                raise ValueError(
                    "cross-environment run artifact: "
                    f"{artifact_id} belongs to {artifact_environment or 'unknown'}"
                )
            artifact_path = Path(artifact.path)
            if not artifact_path.is_file():
                raise ValueError(f"dead run artifact file: {artifact_id}")
            if hashlib.sha256(artifact_path.read_bytes()).hexdigest() != artifact.sha256:
                raise ValueError(f"run artifact hash mismatch: {artifact_id}")
        return True

    def _assert_run_scope(
        self,
        environment_id: str,
        reference_kind: str,
        reference_id: str,
        run: Any,
        *,
        environment_scope: EnvironmentScopeV1 | None = None,
    ) -> None:
        environment = environment_scope or self.get_environment(environment_id)
        if environment is None:
            raise KeyError(f"unknown AI-player environment: {environment_id}")
        if environment.id != environment_id:
            raise ValueError(f"environment scope id mismatch: {environment.id} != {environment_id}")
        embedded_environment = getattr(run, "environment", None) or {}
        embedded_environment_id = embedded_environment.get("environment_id")
        origin_environment_id = self._reference_origin_environment_id(
            reference_kind,
            reference_id,
        )
        evidence_environment_id = embedded_environment_id or origin_environment_id
        if evidence_environment_id and evidence_environment_id != environment_id:
            if not self._environment_can_inherit_evidence(
                evidence_environment_id,
                environment_id,
            ):
                raise ValueError(
                    "cross-environment run metadata: environment_id="
                    f"{evidence_environment_id} is outside the lineage of {environment_id}"
                )
            evidence_environment = self.get_environment(evidence_environment_id)
            if evidence_environment is None:
                raise ValueError(
                    "run evidence names an unknown ancestor environment: "
                    f"{evidence_environment_id}"
                )
            environment = evidence_environment
        target_id = getattr(run, "target_id", None)
        accepted_device_ids = {environment.device_scope_id, *environment.device_scope_id_aliases}
        if target_id and target_id not in accepted_device_ids:
            raise ValueError(
                f"cross-environment run target: {target_id} not in {sorted(accepted_device_ids)}"
            )
        game_id = getattr(run, "game_id", None)
        accepted_game_ids = {environment.game_id, *environment.game_id_aliases}
        if game_id and game_id not in accepted_game_ids:
            raise ValueError(
                f"cross-environment run game: {game_id} not in {sorted(accepted_game_ids)}"
            )
        build_scope_id = getattr(run, "build_scope_id", None)
        accepted_build_ids = {
            environment.build_scope_id,
            *environment.build_scope_id_aliases,
        }
        if build_scope_id and build_scope_id not in accepted_build_ids:
            raise ValueError(
                "cross-environment run build: "
                f"{build_scope_id} not in {sorted(accepted_build_ids)}"
            )
        embedded_checks = {
            "environment_id": {environment.id},
            "game_id": accepted_game_ids,
            "build_scope_id": accepted_build_ids,
            "account_scope_id": {environment.account_scope_id},
            "device_scope_id": accepted_device_ids,
            "channel": {environment.channel},
        }
        for key, accepted in embedded_checks.items():
            observed = embedded_environment.get(key)
            if observed and observed not in accepted:
                raise ValueError(
                    f"cross-environment run metadata: {key}={observed} "
                    f"not in {sorted(accepted)}"
                )
        identity_proven = (
            embedded_environment.get("environment_id") == environment.id
            or (game_id in accepted_game_ids and build_scope_id in accepted_build_ids)
            or origin_environment_id == environment.id
            or self._reference_claimed_by_environment(
                reference_kind,
                reference_id,
                environment_id,
            )
        )
        if not identity_proven:
            identity_proven = self._run_artifacts_prove_environment(environment_id, run)
        if not identity_proven:
            raise ValueError(f"run evidence lacks environment identity: {reference_id}")

    def resolve_evidence_refs(
        self,
        references: Sequence[EvidenceReferenceV1],
    ) -> dict[str, list[Any]]:
        return self.resolve_evidence_references(references)

    @staticmethod
    def _append_resolved(
        resolved: dict[str, list[Any]],
        kind: str,
        reference_id: str,
        value: Any | None,
    ) -> None:
        if value is None:
            raise ValueError(f"dead evidence reference: {kind}:{reference_id}")
        resolved[kind].append(value)

    def put_environment(self, environment: EnvironmentScopeV1) -> EnvironmentScopeV1:
        self.resolve_evidence_references(
            environment.evidence_refs,
            environment_scope=environment,
        )
        body = environment.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            existing = connection.execute(
                "SELECT body_json FROM ai_player_environments WHERE id=?",
                (environment.id,),
            ).fetchone()
            if existing:
                if existing["body_json"] == body:
                    return environment
                raise ValueError(f"environment is immutable and already exists: {environment.id}")
            identity_owner = connection.execute(
                "SELECT id FROM ai_player_environments WHERE identity_hash=?",
                (environment.identity_hash,),
            ).fetchone()
            if identity_owner:
                raise ValueError(
                    "environment identity hash already belongs to "
                    f"{identity_owner['id']}"
                )
            connection.execute(
                """
                INSERT INTO ai_player_environments(
                    id, game_id, identity_hash, body_json, created_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    environment.id,
                    environment.game_id,
                    environment.identity_hash,
                    body,
                    environment.created_at,
                ),
            )
            self._record_evidence(
                connection,
                environment.id,
                "environment",
                environment.id,
                environment.identity_hash,
                environment.evidence_refs,
            )
        return environment

    def get_environment(self, environment_id: str) -> EnvironmentScopeV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT body_json FROM ai_player_environments WHERE id=?",
                (environment_id,),
            ).fetchone()
        return EnvironmentScopeV1.model_validate_json(row["body_json"]) if row else None

    def list_environments(self, *, game_id: str | None = None) -> list[EnvironmentScopeV1]:
        """Return immutable environment scopes without exposing the backing tables."""

        query = "SELECT body_json FROM ai_player_environments"
        parameters: list[Any] = []
        if game_id is not None:
            query += " WHERE game_id=?"
            parameters.append(game_id)
        query += " ORDER BY created_at, id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [EnvironmentScopeV1.model_validate_json(row["body_json"]) for row in rows]

    def append_planner_measurement_receipt(self, receipt: Any) -> Any:
        """Pin one planner receipt and invocation identity in an append-only ledger."""

        from .planner_measurement import PlannerMeasurementReceiptV1

        receipt = PlannerMeasurementReceiptV1.model_validate(receipt)
        self.planner_measurement_trust_store.verify(receipt)
        if self.get_environment(receipt.environment_id) is None:
            raise ValueError("planner measurement environment is missing")
        artifact = self.observatory_store.get_artifact(receipt.artifact_id)
        if artifact is None or artifact.kind != "trace":
            raise ValueError("planner measurement artifact is missing or has the wrong kind")
        expected_metadata = {
            "schema": receipt.schema_id,
            "environment_id": receipt.environment_id,
            "command_id": receipt.command_id,
            "producer_identity": receipt.producer_identity,
            "invocation_id": receipt.invocation_id,
        }
        if any(artifact.metadata.get(key) != value for key, value in expected_metadata.items()):
            raise ValueError("planner measurement artifact metadata is not producer-bound")
        path = Path(artifact.path)
        if not path.is_absolute():
            path = self.observatory_store.root / path
        path = path.resolve()
        try:
            path.relative_to(self.observatory_store.root)
        except ValueError as error:
            raise ValueError("planner measurement artifact escapes canonical root") from error
        if not path.is_file():
            raise ValueError("planner measurement artifact file is missing")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact.sha256:
            raise ValueError("planner measurement artifact hash mismatch")
        try:
            artifact_receipt = PlannerMeasurementReceiptV1.model_validate_json(raw)
        except ValueError as error:
            raise ValueError("planner measurement artifact is not a valid receipt") from error
        if artifact_receipt != receipt:
            raise ValueError("planner measurement artifact contradicts the receipt")
        body_json = receipt.model_dump_json(by_alias=True)
        body_sha256 = hashlib.sha256(
            json.dumps(
                receipt.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        with self._write_lock, self._connection() as connection:
            conflicts = connection.execute(
                """
                SELECT body_json FROM ai_player_planner_measurement_receipts
                WHERE id=? OR artifact_id=? OR command_id=?
                   OR (producer_identity=? AND invocation_id=?)
                """,
                (
                    receipt.id,
                    receipt.artifact_id,
                    receipt.command_id,
                    receipt.producer_identity,
                    receipt.invocation_id,
                ),
            ).fetchall()
            if conflicts:
                if all(
                    PlannerMeasurementReceiptV1.model_validate_json(row["body_json"])
                    == receipt
                    for row in conflicts
                ):
                    return receipt
                raise ValueError(
                    "planner receipt id, command, artifact, or producer invocation is reused"
                )
            connection.execute(
                """
                INSERT INTO ai_player_planner_measurement_receipts(
                    id, environment_id, command_id, producer_identity,
                    invocation_id, artifact_id, artifact_sha256,
                    body_sha256, body_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    receipt.id,
                    receipt.environment_id,
                    receipt.command_id,
                    receipt.producer_identity,
                    receipt.invocation_id,
                    receipt.artifact_id,
                    artifact.sha256,
                    body_sha256,
                    body_json,
                    receipt.completed_at,
                ),
            )
        return receipt

    def get_planner_measurement_receipt_by_artifact(self, artifact_id: str) -> Any | None:
        """Read a pinned receipt and re-check both its immutable row and artifact bytes."""

        from .planner_measurement import PlannerMeasurementReceiptV1

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT artifact_sha256, body_sha256, body_json
                FROM ai_player_planner_measurement_receipts WHERE artifact_id=?
                """,
                (artifact_id,),
            ).fetchone()
        if row is None:
            return None
        receipt = PlannerMeasurementReceiptV1.model_validate_json(row["body_json"])
        self.planner_measurement_trust_store.verify(receipt)
        canonical = json.dumps(
            receipt.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != row["body_sha256"]:
            raise ValueError("planner measurement ledger body hash mismatch")
        artifact = self.observatory_store.get_artifact(artifact_id)
        if artifact is None or artifact.sha256 != row["artifact_sha256"]:
            raise ValueError("planner measurement artifact was replaced")
        path = Path(artifact.path)
        if not path.is_absolute():
            path = self.observatory_store.root / path
        path = path.resolve()
        try:
            path.relative_to(self.observatory_store.root)
        except ValueError as error:
            raise ValueError("planner measurement artifact escapes canonical root") from error
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
            raise ValueError("planner measurement artifact is missing or hash-invalid")
        if PlannerMeasurementReceiptV1.model_validate_json(path.read_bytes()) != receipt:
            raise ValueError("planner measurement artifact contradicts its pinned row")
        return receipt

    def validate_terminal_evidence_references(
        self,
        references: Sequence[EvidenceReferenceV1],
    ) -> None:
        """Require every run-backed milestone reference to be durably terminal."""

        with self._connection() as connection:
            self._require_terminal_milestone_evidence(connection, references)

    def apply_evidence_milestone(
        self,
        environment: EnvironmentScopeV1,
        memories: Sequence[MemoryRecordV1],
        frontier_tasks: Sequence[FrontierTaskV1],
        session_capsules: Sequence[SessionCapsuleV1],
    ) -> dict[str, int]:
        """Atomically apply one explicit, evidence-backed environment checkpoint."""

        environment_id = environment.id
        entities: tuple[tuple[str, Sequence[Any]], ...] = (
            ("memory", memories),
            ("frontier task", frontier_tasks),
            ("session capsule", session_capsules),
        )
        for label, items in entities:
            if any(item.environment_id != environment_id for item in items):
                raise ValueError(f"{label} environment does not match milestone environment")
        if any(task.status != "queued" for task in frontier_tasks):
            raise ValueError("milestone frontier tasks must be newly queued")

        references = list(environment.evidence_refs)
        for entity in (*memories, *frontier_tasks, *session_capsules):
            references.extend(entity.evidence_refs)
        for capsule in session_capsules:
            if capsule.pending_action is None:
                continue
            if capsule.pending_action.action_run_id is not None:
                raise ValueError("milestone capsules cannot retain a trace action run")
            references.extend(capsule.pending_action.evidence_refs)
            references.extend(capsule.pending_action.after_evidence_refs)
        if any(reference.trace_run_ids for reference in references):
            raise ValueError("milestones cannot retain trace run references")

        self.resolve_evidence_references(
            references,
            environment_scope=environment,
        )
        with self._write_lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_terminal_milestone_evidence(connection, references)

            existing_environment = connection.execute(
                "SELECT body_json FROM ai_player_environments WHERE id=?",
                (environment_id,),
            ).fetchone()
            environment_body = environment.model_dump_json(by_alias=True)
            if existing_environment and existing_environment["body_json"] != environment_body:
                raise ValueError(f"environment conflicts: {environment_id}")
            identity_owner = connection.execute(
                "SELECT id FROM ai_player_environments WHERE identity_hash=?",
                (environment.identity_hash,),
            ).fetchone()
            if identity_owner and identity_owner["id"] != environment_id:
                raise ValueError(
                    f"environment identity hash conflicts with: {identity_owner['id']}"
                )

            existing_memory_ids: set[str] = set()
            for memory in memories:
                row = connection.execute(
                    """
                    SELECT body_json FROM ai_player_memory_records
                    WHERE environment_id=? AND id=?
                    """,
                    (environment_id, memory.id),
                ).fetchone()
                if row is None:
                    continue
                if row["body_json"] != memory.model_dump_json(by_alias=True):
                    raise ValueError(f"memory record conflicts: {memory.id}")
                if not self._stored_evidence_matches(
                    connection,
                    environment_id,
                    "memory_record",
                    memory.id,
                    str(memory.version),
                    memory.evidence_refs,
                ):
                    raise ValueError(f"memory record evidence is incomplete: {memory.id}")
                existing_memory_ids.add(memory.id)

            existing_task_ids: set[str] = set()
            for task in frontier_tasks:
                row = connection.execute(
                    """
                    SELECT body_json FROM ai_player_frontier_tasks
                    WHERE environment_id=? AND id=?
                    """,
                    (environment_id, task.id),
                ).fetchone()
                if row is None:
                    continue
                if row["body_json"] != task.model_dump_json(by_alias=True):
                    raise ValueError(f"frontier task conflicts: {task.id}")
                if not self._stored_evidence_matches(
                    connection,
                    environment_id,
                    "frontier_task",
                    task.id,
                    str(task.version),
                    task.evidence_refs,
                ):
                    raise ValueError(f"frontier task evidence is incomplete: {task.id}")
                existing_task_ids.add(task.id)

            declared_task_ids = {task.id for task in frontier_tasks}
            for task in frontier_tasks:
                missing = set(task.dependency_task_ids) - declared_task_ids
                missing = {
                    task_id
                    for task_id in missing
                    if connection.execute(
                        """
                        SELECT 1 FROM ai_player_frontier_tasks
                        WHERE environment_id=? AND id=?
                        """,
                        (environment_id, task_id),
                    ).fetchone()
                    is None
                }
                if missing:
                    raise ValueError(f"frontier task has dead dependencies: {sorted(missing)}")

            existing_capsule_ids: set[str] = set()
            for capsule in session_capsules:
                if capsule.last_confirmed_state_id is not None:
                    state = connection.execute(
                        """
                        SELECT 1 FROM ai_player_semantic_states
                        WHERE environment_id=? AND id=?
                        """,
                        (environment_id, capsule.last_confirmed_state_id),
                    ).fetchone()
                    if state is None:
                        raise ValueError(
                            "capsule state is missing in environment: "
                            f"{capsule.last_confirmed_state_id}"
                        )
                referenced_tasks = {
                    *capsule.active_task_ids,
                    *capsule.pending_frontier_task_ids,
                }
                missing_tasks = referenced_tasks - declared_task_ids
                missing_tasks = {
                    task_id
                    for task_id in missing_tasks
                    if connection.execute(
                        """
                        SELECT 1 FROM ai_player_frontier_tasks
                        WHERE environment_id=? AND id=?
                        """,
                        (environment_id, task_id),
                    ).fetchone()
                    is None
                }
                if missing_tasks:
                    raise ValueError(
                        f"session capsule has dead task references: {sorted(missing_tasks)}"
                    )

                row = connection.execute(
                    """
                    SELECT body_json FROM ai_player_session_capsules
                    WHERE environment_id=? AND id=?
                    """,
                    (environment_id, capsule.id),
                ).fetchone()
                capsule_references = list(capsule.evidence_refs)
                if capsule.pending_action is not None:
                    capsule_references.extend(capsule.pending_action.evidence_refs)
                    capsule_references.extend(capsule.pending_action.after_evidence_refs)
                if row is not None:
                    if row["body_json"] != capsule.model_dump_json(by_alias=True):
                        raise ValueError(f"session capsule conflicts: {capsule.id}")
                    if not self._stored_evidence_matches(
                        connection,
                        environment_id,
                        "session_capsule",
                        capsule.id,
                        str(capsule.sequence),
                        capsule_references,
                    ):
                        raise ValueError(
                            f"session capsule evidence is incomplete: {capsule.id}"
                        )
                    existing_capsule_ids.add(capsule.id)
                    continue
                sequence_owner = connection.execute(
                    """
                    SELECT id FROM ai_player_session_capsules
                    WHERE environment_id=? AND session_id=? AND sequence=?
                    """,
                    (environment_id, capsule.session_id, capsule.sequence),
                ).fetchone()
                if sequence_owner:
                    raise ValueError(
                        "session capsule sequence conflicts with: "
                        f"{sequence_owner['id']}"
                    )

            if existing_environment:
                if not self._stored_evidence_matches(
                    connection,
                    environment_id,
                    "environment",
                    environment_id,
                    environment.identity_hash,
                    environment.evidence_refs,
                ):
                    raise ValueError(f"environment evidence is incomplete: {environment_id}")
            else:
                connection.execute(
                    """
                    INSERT INTO ai_player_environments(
                        id, game_id, identity_hash, body_json, created_at
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        environment_id,
                        environment.game_id,
                        environment.identity_hash,
                        environment_body,
                        environment.created_at,
                    ),
                )
                self._record_evidence(
                    connection,
                    environment_id,
                    "environment",
                    environment_id,
                    environment.identity_hash,
                    environment.evidence_refs,
                )

            for memory in memories:
                if memory.id in existing_memory_ids:
                    continue
                connection.execute(
                    """
                    INSERT INTO ai_player_memory_records(
                        environment_id, id, version, kind, subject_id, status,
                        supersedes_id, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        environment_id,
                        memory.id,
                        memory.version,
                        memory.kind,
                        memory.subject_id,
                        memory.status,
                        memory.supersedes_id,
                        memory.model_dump_json(by_alias=True),
                        memory.created_at,
                    ),
                )
                self._record_evidence(
                    connection,
                    environment_id,
                    "memory_record",
                    memory.id,
                    str(memory.version),
                    memory.evidence_refs,
                )

            for task in frontier_tasks:
                if task.id in existing_task_ids:
                    continue
                connection.execute(
                    """
                    INSERT INTO ai_player_frontier_tasks(
                        environment_id, id, version, status, source, body_json,
                        created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        environment_id,
                        task.id,
                        task.version,
                        task.status,
                        task.source,
                        task.model_dump_json(by_alias=True),
                        task.created_at,
                        task.created_at,
                    ),
                )
                self._record_evidence(
                    connection,
                    environment_id,
                    "frontier_task",
                    task.id,
                    str(task.version),
                    task.evidence_refs,
                )

            for capsule in session_capsules:
                if capsule.id in existing_capsule_ids:
                    continue
                connection.execute(
                    """
                    INSERT INTO ai_player_session_capsules(
                        environment_id, id, session_id, sequence, body_json, created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        environment_id,
                        capsule.id,
                        capsule.session_id,
                        capsule.sequence,
                        capsule.model_dump_json(by_alias=True),
                        capsule.created_at,
                    ),
                )
                capsule_references = list(capsule.evidence_refs)
                if capsule.pending_action is not None:
                    capsule_references.extend(capsule.pending_action.evidence_refs)
                    capsule_references.extend(capsule.pending_action.after_evidence_refs)
                self._record_evidence(
                    connection,
                    environment_id,
                    "session_capsule",
                    capsule.id,
                    str(capsule.sequence),
                    capsule_references,
                )

        return {
            "inserted_environment_count": int(existing_environment is None),
            "inserted_memory_count": len(memories) - len(existing_memory_ids),
            "inserted_frontier_task_count": len(frontier_tasks) - len(existing_task_ids),
            "inserted_session_capsule_count": len(session_capsules)
            - len(existing_capsule_ids),
        }

    @staticmethod
    def _require_terminal_milestone_evidence(
        connection: sqlite3.Connection,
        references: Sequence[EvidenceReferenceV1],
    ) -> None:
        checked_runs: set[str] = set()
        checked_steps: set[str] = set()

        def check_run(run_id: str) -> EvidenceRun:
            if run_id in checked_runs:
                row = connection.execute(
                    "SELECT body_json FROM evidence_runs WHERE id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"dead evidence reference: evidence_run:{run_id}")
                return EvidenceRun.model_validate_json(row["body_json"])
            row = connection.execute(
                "SELECT body_json FROM evidence_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"dead evidence reference: evidence_run:{run_id}")
            run = EvidenceRun.model_validate_json(row["body_json"])
            if run.status in {"running", "paused"} or not run.ended_at:
                raise ValueError(f"ongoing evidence run cannot produce a milestone: {run_id}")
            checked_runs.add(run_id)
            return run

        def check_step(step_id: str) -> EvidenceStep:
            row = connection.execute(
                "SELECT body_json FROM evidence_steps WHERE id=?",
                (step_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"dead evidence reference: evidence_step:{step_id}")
            step = EvidenceStep.model_validate_json(row["body_json"])
            if step_id not in checked_steps:
                if step.status == "running" or not step.ended_at:
                    raise ValueError(
                        f"ongoing evidence step cannot produce a milestone: {step_id}"
                    )
                checked_steps.add(step_id)
            check_run(step.evidence_run_id)
            return step

        for reference in references:
            for run_id in reference.evidence_run_ids:
                check_run(run_id)
            for step_id in reference.evidence_step_ids:
                check_step(step_id)
            for artifact_id in reference.artifact_ids:
                row = connection.execute(
                    "SELECT body_json FROM artifacts WHERE id=?",
                    (artifact_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"dead evidence reference: artifact:{artifact_id}")
                artifact = ArtifactRef.model_validate_json(row["body_json"])
                metadata_run_id = artifact.metadata.get("evidence_run_id")
                if metadata_run_id is not None:
                    if not isinstance(metadata_run_id, str) or not metadata_run_id.strip():
                        raise ValueError(
                            f"artifact has invalid evidence_run_id metadata: {artifact_id}"
                        )
                    check_run(metadata_run_id)
                if artifact.run_id:
                    evidence_run = connection.execute(
                        "SELECT 1 FROM evidence_runs WHERE id=?",
                        (artifact.run_id,),
                    ).fetchone()
                    if evidence_run is not None:
                        check_run(artifact.run_id)
                metadata_step_id = artifact.metadata.get("evidence_step_id")
                if metadata_step_id is not None:
                    if not isinstance(metadata_step_id, str) or not metadata_step_id.strip():
                        raise ValueError(
                            f"artifact has invalid evidence_step_id metadata: {artifact_id}"
                        )
                    step = check_step(metadata_step_id)
                    if metadata_run_id and step.evidence_run_id != metadata_run_id:
                        raise ValueError(
                            "artifact evidence run/step metadata mismatch: "
                            f"{artifact_id}"
                        )

    def get_session_capsule(
        self,
        environment_id: str,
        capsule_id: str,
    ) -> SessionCapsuleV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_session_capsules
                WHERE environment_id=? AND id=?
                """,
                (environment_id, capsule_id),
            ).fetchone()
        return SessionCapsuleV1.model_validate_json(row["body_json"]) if row else None

    def list_session_capsules(
        self,
        environment_id: str,
        *,
        limit: int = 20,
    ) -> list[SessionCapsuleV1]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_session_capsules
                WHERE environment_id=?
                ORDER BY created_at DESC, sequence DESC, id DESC LIMIT ?
                """,
                (environment_id, max(1, min(limit, 200))),
            ).fetchall()
        return [SessionCapsuleV1.model_validate_json(row["body_json"]) for row in rows]

    def get_navigation_stack(
        self,
        environment_id: str,
        session_id: str,
    ) -> NavigationStackV1 | None:
        """Return the caller stack retained across CLI processes for one live session."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_navigation_stacks
                WHERE environment_id=? AND session_id=?
                """,
                (environment_id, session_id),
            ).fetchone()
        return NavigationStackV1.model_validate_json(row["body_json"]) if row else None

    def push_navigation_frame(
        self,
        environment_id: str,
        session_id: str,
        frame: NavigationFrameV1,
    ) -> NavigationStackV1:
        """Remember a proven caller -> child entry after deterministic replay."""

        if frame.caller_state_id == frame.entered_state_id:
            raise ValueError("navigation entry must change the semantic surface")
        with self._write_lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_navigation_stacks
                WHERE environment_id=? AND session_id=?
                """,
                (environment_id, session_id),
            ).fetchone()
            current = (
                NavigationStackV1.model_validate_json(row["body_json"])
                if row is not None
                else None
            )
            frames = list(current.frames) if current is not None else []
            if (
                current is not None
                and current.current_state_id == frame.entered_state_id
                and frames
                and frames[-1] == frame
            ):
                return current
            if current is not None and current.current_state_id != frame.caller_state_id:
                caller_indexes = [
                    index
                    for index, retained in enumerate(frames)
                    if retained.entered_state_id == frame.caller_state_id
                ]
                frames = frames[: caller_indexes[-1] + 1] if caller_indexes else []
            frames.append(frame)
            stack = NavigationStackV1(
                environment_id=environment_id,
                session_id=session_id,
                version=(current.version + 1 if current is not None else 1),
                current_state_id=frame.entered_state_id,
                frames=frames,
            )
            self._write_navigation_stack_event(
                connection,
                stack=stack,
                operation="push",
                frame=frame,
            )
        return stack

    def pop_navigation_frame(
        self,
        environment_id: str,
        session_id: str,
        *,
        entered_state_id: str,
        caller_state_id: str,
    ) -> NavigationStackV1:
        """Consume the exact caller frame after a guarded Back/close succeeds."""

        with self._write_lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_navigation_stacks
                WHERE environment_id=? AND session_id=?
                """,
                (environment_id, session_id),
            ).fetchone()
            if row is None:
                raise ValueError("navigation caller stack is missing")
            current = NavigationStackV1.model_validate_json(row["body_json"])
            if not current.frames:
                raise ValueError("navigation caller stack is empty")
            frame = current.frames[-1]
            if (
                current.current_state_id != entered_state_id
                or frame.entered_state_id != entered_state_id
                or frame.caller_state_id != caller_state_id
            ):
                raise ValueError("navigation caller stack does not match the proven return")
            stack = NavigationStackV1(
                environment_id=environment_id,
                session_id=session_id,
                version=current.version + 1,
                current_state_id=caller_state_id,
                frames=current.frames[:-1],
            )
            self._write_navigation_stack_event(
                connection,
                stack=stack,
                operation="pop",
                frame=frame,
            )
        return stack

    @staticmethod
    def _write_navigation_stack_event(
        connection: sqlite3.Connection,
        *,
        stack: NavigationStackV1,
        operation: str,
        frame: NavigationFrameV1,
    ) -> None:
        body = stack.model_dump_json(by_alias=True)
        connection.execute(
            """
            INSERT INTO ai_player_navigation_stacks(
                environment_id, session_id, version, current_state_id,
                body_json, updated_at
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(environment_id, session_id) DO UPDATE SET
                version=excluded.version,
                current_state_id=excluded.current_state_id,
                body_json=excluded.body_json,
                updated_at=excluded.updated_at
            """,
            (
                stack.environment_id,
                stack.session_id,
                stack.version,
                stack.current_state_id,
                body,
                stack.updated_at,
            ),
        )
        event_body = json.dumps(
            {
                "schema": "game-observatory.ai-player.navigation-stack-event.v1",
                "operation": operation,
                "frame": frame.model_dump(mode="json", by_alias=True),
                "result": stack.model_dump(mode="json", by_alias=True),
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        connection.execute(
            """
            INSERT INTO ai_player_navigation_stack_events(
                environment_id, session_id, version, operation, body_json, created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                stack.environment_id,
                stack.session_id,
                stack.version,
                operation,
                event_body,
                stack.updated_at,
            ),
        )

    def get_environment_promotion(
        self,
        promotion_id: str,
    ) -> EnvironmentPromotionV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT body_json FROM ai_player_environment_promotions WHERE id=?",
                (promotion_id,),
            ).fetchone()
        return EnvironmentPromotionV1.model_validate_json(row["body_json"]) if row else None

    def promote_environment(
        self,
        promotion: EnvironmentPromotionV1,
    ) -> EnvironmentPromotionV1:
        """Atomically append an immutable child environment and its audited lineage edge."""

        parent = self.get_environment(promotion.parent_environment_id)
        if parent is None:
            raise KeyError(
                f"unknown parent AI-player environment: {promotion.parent_environment_id}"
            )
        self._validate_promotion_identity(parent, promotion)
        promotion_body = promotion.model_dump_json(by_alias=True)
        child = promotion.child_environment
        child_body = child.model_dump_json(by_alias=True)
        recorded_promotion = self.get_environment_promotion(promotion.id)
        if recorded_promotion is not None and recorded_promotion != promotion:
            raise ValueError(f"environment promotion conflicts: {promotion.id}")
        if recorded_promotion is None:
            self.resolve_evidence_references(
                [promotion.terminal_identity_evidence],
                environment_scope=parent,
            )
            self._require_terminal_identity_evidence(promotion)

        with self._write_lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_promotion = connection.execute(
                "SELECT body_json FROM ai_player_environment_promotions WHERE id=?",
                (promotion.id,),
            ).fetchone()
            if existing_promotion:
                if existing_promotion["body_json"] != promotion_body:
                    raise ValueError(f"environment promotion conflicts: {promotion.id}")
                self._require_terminal_identity_evidence(
                    promotion,
                    connection=connection,
                )
                existing_child = connection.execute(
                    "SELECT body_json FROM ai_player_environments WHERE id=?",
                    (child.id,),
                ).fetchone()
                lineage = connection.execute(
                    """
                    SELECT promotion_id FROM ai_player_environment_lineage
                    WHERE parent_environment_id=? AND child_environment_id=?
                    """,
                    (promotion.parent_environment_id, child.id),
                ).fetchone()
                if (
                    existing_child is None
                    or existing_child["body_json"] != child_body
                    or lineage is None
                    or lineage["promotion_id"] != promotion.id
                    or not self._stored_evidence_matches(
                        connection,
                        promotion.parent_environment_id,
                        "environment_promotion",
                        promotion.id,
                        "1",
                        [promotion.terminal_identity_evidence],
                    )
                    or not self._stored_evidence_matches(
                        connection,
                        child.id,
                        "environment",
                        child.id,
                        child.identity_hash,
                        child.evidence_refs,
                    )
                ):
                    raise ValueError(f"incomplete environment promotion state: {promotion.id}")
                return promotion

            self._require_terminal_identity_evidence(
                promotion,
                connection=connection,
            )
            existing_child = connection.execute(
                "SELECT body_json FROM ai_player_environments WHERE id=?",
                (child.id,),
            ).fetchone()
            if existing_child:
                raise ValueError(f"promotion child environment already exists: {child.id}")
            identity_owner = connection.execute(
                "SELECT id FROM ai_player_environments WHERE identity_hash=?",
                (child.identity_hash,),
            ).fetchone()
            if identity_owner:
                raise ValueError(
                    f"environment identity hash already belongs to {identity_owner['id']}"
                )
            child_owner = connection.execute(
                """
                SELECT promotion_id FROM ai_player_environment_lineage
                WHERE child_environment_id=?
                """,
                (child.id,),
            ).fetchone()
            if child_owner:
                raise ValueError(f"promotion child already has a parent: {child.id}")

            connection.execute(
                """
                INSERT INTO ai_player_environments(
                    id, game_id, identity_hash, body_json, created_at
                ) VALUES(?,?,?,?,?)
                """,
                (child.id, child.game_id, child.identity_hash, child_body, child.created_at),
            )
            connection.execute(
                """
                INSERT INTO ai_player_environment_promotions(
                    id, parent_environment_id, child_environment_id, body_json, promoted_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    promotion.id,
                    promotion.parent_environment_id,
                    child.id,
                    promotion_body,
                    promotion.promoted_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO ai_player_environment_lineage(
                    parent_environment_id, child_environment_id, promotion_id, created_at
                ) VALUES(?,?,?,?)
                """,
                (
                    promotion.parent_environment_id,
                    child.id,
                    promotion.id,
                    promotion.promoted_at,
                ),
            )
            self._record_evidence(
                connection,
                promotion.parent_environment_id,
                "environment_promotion",
                promotion.id,
                "1",
                [promotion.terminal_identity_evidence],
            )
            self._record_evidence(
                connection,
                child.id,
                "environment",
                child.id,
                child.identity_hash,
                child.evidence_refs,
            )
        return promotion

    def select_current_environment(self, environment_id: str) -> EnvironmentSelectionV1:
        """Resolve an environment to the unique leaf in its descendant lineage."""

        self._require_environment(environment_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                WITH RECURSIVE descendants(id) AS (
                    SELECT ?
                    UNION ALL
                    SELECT lineage.child_environment_id
                    FROM ai_player_environment_lineage AS lineage
                    JOIN descendants ON lineage.parent_environment_id=descendants.id
                )
                SELECT descendants.id
                FROM descendants
                WHERE NOT EXISTS (
                    SELECT 1 FROM ai_player_environment_lineage AS child_edge
                    WHERE child_edge.parent_environment_id=descendants.id
                )
                ORDER BY descendants.id
                """,
                (environment_id,),
            ).fetchall()
            leaf_ids = [row["id"] for row in rows]
            if len(leaf_ids) != 1:
                raise ValueError(
                    "environment lineage has no unique current leaf: "
                    f"{environment_id} -> {leaf_ids}"
                )
            selected_id = leaf_ids[0]
            reverse_path = [selected_id]
            cursor = selected_id
            while cursor != environment_id:
                parent_row = connection.execute(
                    """
                    SELECT parent_environment_id FROM ai_player_environment_lineage
                    WHERE child_environment_id=?
                    """,
                    (cursor,),
                ).fetchone()
                if parent_row is None:
                    raise ValueError(
                        f"broken environment lineage between {environment_id} and {selected_id}"
                    )
                cursor = parent_row["parent_environment_id"]
                reverse_path.append(cursor)
        lineage_path = list(reversed(reverse_path))
        selected = self.get_environment(selected_id)
        if selected is None:
            raise ValueError(f"dead selected environment: {selected_id}")
        return EnvironmentSelectionV1(
            requested_environment_id=environment_id,
            selected_environment_id=selected_id,
            selected_environment=selected,
            lineage_path=lineage_path,
            lineage_statuses={
                item: "current" if item == selected_id else "superseded"
                for item in lineage_path
            },
        )

    def select_environment_lineage(self, environment_id: str) -> EnvironmentSelectionV1:
        """Select the current leaf while retaining the complete ancestry path."""

        self._require_environment(environment_id)
        root_id = environment_id
        seen = {root_id}
        with self._connection() as connection:
            while True:
                row = connection.execute(
                    """
                    SELECT parent_environment_id FROM ai_player_environment_lineage
                    WHERE child_environment_id=?
                    """,
                    (root_id,),
                ).fetchone()
                if row is None:
                    break
                root_id = row["parent_environment_id"]
                if root_id in seen:
                    raise ValueError(f"environment lineage contains a cycle: {environment_id}")
                seen.add(root_id)
        return self.select_current_environment(root_id)

    def is_unique_current_environment_leaf(self, environment_id: str) -> bool:
        """Return whether the requested environment is its lineage's sole leaf.

        This preserves the branch and cycle rejection of
        :meth:`select_environment_lineage` for safety guards that only need a
        boolean answer, without constructing the full environment projection.
        """

        with self._connection() as connection:
            row = connection.execute(
                """
                WITH RECURSIVE ancestors(id) AS (
                    SELECT id FROM ai_player_environments WHERE id=?
                    UNION
                    SELECT lineage.parent_environment_id
                    FROM ai_player_environment_lineage AS lineage
                    JOIN ancestors
                      ON lineage.child_environment_id=ancestors.id
                ),
                roots(id) AS (
                    SELECT ancestor.id
                    FROM ancestors AS ancestor
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM ai_player_environment_lineage AS parent_edge
                        WHERE parent_edge.child_environment_id=ancestor.id
                    )
                ),
                descendants(id) AS (
                    SELECT id FROM roots
                    UNION
                    SELECT lineage.child_environment_id
                    FROM ai_player_environment_lineage AS lineage
                    JOIN descendants
                      ON lineage.parent_environment_id=descendants.id
                ),
                leaves(id) AS (
                    SELECT descendant.id
                    FROM descendants AS descendant
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM ai_player_environment_lineage AS child_edge
                        WHERE child_edge.parent_environment_id=descendant.id
                    )
                )
                SELECT COUNT(*) AS leaf_count, MAX(id) AS leaf_id
                FROM leaves
                """,
                (environment_id,),
            ).fetchone()
        return bool(
            row is not None
            and int(row["leaf_count"]) == 1
            and row["leaf_id"] == environment_id
        )

    def list_current_environment_selections(self) -> list[EnvironmentSelectionV1]:
        """Return one canonical current leaf for every independent lineage."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT environment.id
                FROM ai_player_environments AS environment
                WHERE NOT EXISTS (
                    SELECT 1 FROM ai_player_environment_lineage AS lineage
                    WHERE lineage.child_environment_id=environment.id
                )
                ORDER BY environment.created_at, environment.id
                """
            ).fetchall()
        return [self.select_current_environment(row["id"]) for row in rows]

    @staticmethod
    def _validate_promotion_identity(
        parent: EnvironmentScopeV1,
        promotion: EnvironmentPromotionV1,
    ) -> None:
        child = promotion.child_environment
        stable_fields = (
            "game_id",
            "game_id_aliases",
            "build_scope_id",
            "build_scope_id_aliases",
            "channel",
            "device_scope_id",
            "device_scope_id_aliases",
            "locale",
            "viewport_width",
            "viewport_height",
        )
        changed = [
            field_name
            for field_name in stable_fields
            if getattr(parent, field_name) != getattr(child, field_name)
        ]
        if changed:
            raise ValueError(f"promotion changed stable environment fields: {changed}")
        for field_name in ("server_scope_id", "world_scope_id"):
            parent_value = getattr(parent, field_name)
            child_value = getattr(child, field_name)
            if parent_value is not None and child_value != parent_value:
                raise ValueError(f"promotion cannot replace known {field_name}")
        identity_became_more_specific = (
            child.account_scope_id != parent.account_scope_id
            or (parent.server_scope_id is None and child.server_scope_id is not None)
            or (parent.world_scope_id is None and child.world_scope_id is not None)
        )
        if not identity_became_more_specific:
            raise ValueError("promotion must confirm at least one more-specific identity field")
        if child.identity_hash == parent.identity_hash:
            raise ValueError("promotion child requires a distinct identity hash")

        proof = promotion.terminal_identity_evidence
        proof_run_ids = set(proof.evidence_run_ids)
        proof_step_ids = set(proof.evidence_step_ids)
        for reference in child.evidence_refs:
            if set(reference.evidence_run_ids) - proof_run_ids:
                raise ValueError("child identity contains EvidenceRuns outside promotion proof")
            if set(reference.evidence_step_ids) - proof_step_ids:
                raise ValueError("child identity contains EvidenceSteps outside promotion proof")
            if reference.artifact_ids or reference.trace_run_ids or reference.source_ids:
                raise ValueError(
                    "promotion child identity must be derived only from terminal run/step proof"
                )

    def _require_terminal_identity_evidence(
        self,
        promotion: EnvironmentPromotionV1,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        proof = promotion.terminal_identity_evidence
        if connection is None:
            runs = {
                run_id: self.observatory_store.get_evidence_run(run_id)
                for run_id in proof.evidence_run_ids
            }
        else:
            runs = {}
            for run_id in proof.evidence_run_ids:
                row = connection.execute(
                    "SELECT body_json FROM evidence_runs WHERE id=?",
                    (run_id,),
                ).fetchone()
                runs[run_id] = (
                    EvidenceRun.model_validate_json(row["body_json"]) if row else None
                )
        for run_id, run in runs.items():
            if run is None:
                raise ValueError(f"dead promotion EvidenceRun: {run_id}")
            if run.status != "passed" or not run.ended_at:
                raise ValueError(f"promotion EvidenceRun is not terminal-passed: {run_id}")
        for step_id in proof.evidence_step_ids:
            if connection is None:
                step = self.observatory_store.get_evidence_step(step_id)
            else:
                row = connection.execute(
                    "SELECT body_json FROM evidence_steps WHERE id=?",
                    (step_id,),
                ).fetchone()
                step = EvidenceStep.model_validate_json(row["body_json"]) if row else None
            if step is None:
                raise ValueError(f"dead promotion EvidenceStep: {step_id}")
            if step.status != "passed" or not step.ended_at:
                raise ValueError(f"promotion EvidenceStep is not terminal-passed: {step_id}")
            if step.evidence_run_id not in runs:
                raise ValueError(
                    f"promotion EvidenceStep belongs to an unlisted run: {step_id}"
                )
            run = runs[step.evidence_run_id]
            if run is None or step_id not in run.step_ids:
                raise ValueError(f"promotion EvidenceRun does not retain step: {step_id}")

    def append_memory(self, record: MemoryRecordV1) -> MemoryRecordV1:
        self._prepare_entity(record.environment_id, record.evidence_refs)
        body = record.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_memory_records(
                        environment_id, id, version, kind, subject_id, status,
                        supersedes_id, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        record.environment_id,
                        record.id,
                        record.version,
                        record.kind,
                        record.subject_id,
                        record.status,
                        record.supersedes_id,
                        body,
                        record.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                if "canonical semantic text failed integrity validation" in str(error):
                    raise ValueError(
                        "AI-player canonical semantic text failed integrity validation"
                    ) from error
                raise ValueError(
                    f"memory record is append-only and already exists: {record.id}"
                ) from error
            self._record_evidence(
                connection,
                record.environment_id,
                "memory_record",
                record.id,
                str(record.version),
                record.evidence_refs,
            )
        return record

    def apply_knowledge_memory_seed(
        self,
        environment_id: str,
        source_snapshots: Sequence[SourceSnapshot],
        guides: Sequence[GuideKnowledgeV1],
        memories: Sequence[MemoryRecordV1],
    ) -> dict[str, int]:
        """Atomically apply sourced guide knowledge and its durable memories."""

        environment = self.get_environment(environment_id)
        if environment is None:
            raise KeyError(f"unknown AI-player environment: {environment_id}")
        if any(guide.environment_id != environment_id for guide in guides):
            raise ValueError("guide environment does not match seed environment")
        if any(memory.environment_id != environment_id for memory in memories):
            raise ValueError("memory environment does not match seed environment")

        for guide in guides:
            if guide.status != "current":
                continue
            canonical_values = {
                "applicable_build_scope_id": environment.build_scope_id,
                "applicable_account_scope_id": environment.account_scope_id,
                "applicable_channel": environment.channel,
            }
            mismatches = [
                field_name
                for field_name, expected in canonical_values.items()
                if getattr(guide, field_name) != expected
            ]
            if mismatches:
                raise ValueError(
                    "current guide applicability does not match canonical environment: "
                    + ", ".join(mismatches)
                )

        incoming_source_ids = {snapshot.source_id for snapshot in source_snapshots}
        references = [
            reference
            for entity in (*guides, *memories)
            for reference in entity.evidence_refs
        ]
        resolvable_references: list[EvidenceReferenceV1] = []
        for reference in references:
            missing_source_ids = [
                source_id
                for source_id in reference.source_ids
                if source_id not in incoming_source_ids
                and not self.observatory_store.list_source_snapshots(source_id)
            ]
            if missing_source_ids:
                raise ValueError(
                    "dead evidence reference: source:" + ",".join(sorted(missing_source_ids))
                )
            existing_source_ids = [
                source_id
                for source_id in reference.source_ids
                if self.observatory_store.list_source_snapshots(source_id)
            ]
            resolvable_references.append(
                reference.model_copy(update={"source_ids": existing_source_ids})
            )
        self.resolve_evidence_references(
            resolvable_references,
            environment_scope=environment,
        )

        with self._write_lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")

            for snapshot in source_snapshots:
                origin = connection.execute(
                    """
                    SELECT origin_environment_id FROM ai_player_evidence_origins
                    WHERE reference_kind='source' AND reference_id=?
                    """,
                    (snapshot.source_id,),
                ).fetchone()
                if origin and not self._environment_can_inherit_evidence(
                    origin["origin_environment_id"],
                    environment_id,
                    connection=connection,
                ):
                    raise ValueError(
                        "cross-environment source evidence: "
                        f"{snapshot.source_id} originates in "
                        f"{origin['origin_environment_id']}"
                    )

            existing_source_ids: set[str] = set()
            for snapshot in source_snapshots:
                row = connection.execute(
                    "SELECT body_json FROM source_snapshots WHERE id=?",
                    (snapshot.id,),
                ).fetchone()
                if row is None:
                    continue
                existing_snapshot = SourceSnapshot.model_validate_json(row["body_json"])
                if existing_snapshot.model_dump(mode="json") != snapshot.model_dump(
                    mode="json"
                ):
                    raise ValueError(f"source snapshot id conflicts: {snapshot.id}")
                existing_source_ids.add(snapshot.id)

            existing_guide_keys: set[tuple[str, int]] = set()
            latest_guide_versions = {
                row["id"]: int(row["latest_version"])
                for row in connection.execute(
                    """
                    SELECT id, MAX(version) AS latest_version
                    FROM ai_player_guide_knowledge
                    WHERE environment_id=?
                    GROUP BY id
                    """,
                    (environment_id,),
                ).fetchall()
            }
            incoming_guide_keys = [(guide.id, guide.version) for guide in guides]
            if len(incoming_guide_keys) != len(set(incoming_guide_keys)):
                raise ValueError("guide knowledge seed contains duplicate id and version")
            for guide in sorted(guides, key=lambda item: (item.id, item.version)):
                latest_version = latest_guide_versions.get(guide.id, 0)
                if guide.version <= latest_version:
                    continue
                if guide.version != latest_version + 1:
                    raise ValueError(
                        "guide knowledge seed must start at version 1 and increment consecutively"
                    )
                latest_guide_versions[guide.id] = guide.version
            for guide in guides:
                row = connection.execute(
                    """
                    SELECT body_json FROM ai_player_guide_knowledge
                    WHERE environment_id=? AND id=? AND version=?
                    """,
                    (environment_id, guide.id, guide.version),
                ).fetchone()
                if row is None:
                    continue
                if row["body_json"] != guide.model_dump_json(by_alias=True):
                    raise ValueError(f"guide knowledge conflicts: {guide.id}@{guide.version}")
                if not self._stored_evidence_matches(
                    connection,
                    environment_id,
                    "guide_knowledge",
                    guide.id,
                    str(guide.version),
                    guide.evidence_refs,
                ):
                    raise ValueError(
                        f"guide knowledge evidence is incomplete: {guide.id}@{guide.version}"
                    )
                existing_guide_keys.add((guide.id, guide.version))

            existing_memory_ids: set[str] = set()
            for memory in memories:
                row = connection.execute(
                    """
                    SELECT body_json FROM ai_player_memory_records
                    WHERE environment_id=? AND id=?
                    """,
                    (environment_id, memory.id),
                ).fetchone()
                if row is None:
                    continue
                if row["body_json"] != memory.model_dump_json(by_alias=True):
                    raise ValueError(f"memory record conflicts: {memory.id}")
                if not self._stored_evidence_matches(
                    connection,
                    environment_id,
                    "memory_record",
                    memory.id,
                    str(memory.version),
                    memory.evidence_refs,
                ):
                    raise ValueError(f"memory record evidence is incomplete: {memory.id}")
                existing_memory_ids.add(memory.id)

            for snapshot in source_snapshots:
                if snapshot.id in existing_source_ids:
                    continue
                connection.execute(
                    """
                    INSERT INTO source_snapshots(
                        id, source_id, content_sha256, locator, status, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        snapshot.id,
                        snapshot.source_id,
                        snapshot.content_sha256,
                        snapshot.locator,
                        snapshot.status,
                        snapshot.model_dump_json(),
                        snapshot.captured_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO ai_player_evidence_origins(
                        reference_kind, reference_id, origin_environment_id, created_at
                    ) VALUES('source',?,?,?)
                    """,
                    (snapshot.source_id, environment_id, utc_now()),
                )

            for guide in guides:
                if (guide.id, guide.version) in existing_guide_keys:
                    continue
                connection.execute(
                    """
                    INSERT INTO ai_player_guide_knowledge(
                        environment_id, id, version, status, url, season,
                        server_stage, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        guide.environment_id,
                        guide.id,
                        guide.version,
                        guide.status,
                        str(guide.url),
                        guide.season,
                        guide.server_stage,
                        guide.model_dump_json(by_alias=True),
                        guide.created_at,
                    ),
                )
                self._record_evidence(
                    connection,
                    environment_id,
                    "guide_knowledge",
                    guide.id,
                    str(guide.version),
                    guide.evidence_refs,
                )

            for memory in memories:
                if memory.id in existing_memory_ids:
                    continue
                connection.execute(
                    """
                    INSERT INTO ai_player_memory_records(
                        environment_id, id, version, kind, subject_id, status,
                        supersedes_id, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        memory.environment_id,
                        memory.id,
                        memory.version,
                        memory.kind,
                        memory.subject_id,
                        memory.status,
                        memory.supersedes_id,
                        memory.model_dump_json(by_alias=True),
                        memory.created_at,
                    ),
                )
                self._record_evidence(
                    connection,
                    environment_id,
                    "memory_record",
                    memory.id,
                    str(memory.version),
                    memory.evidence_refs,
                )

        return {
            "inserted_source_snapshot_count": len(source_snapshots) - len(existing_source_ids),
            "inserted_guide_count": len(guides) - len(existing_guide_keys),
            "inserted_memory_count": len(memories) - len(existing_memory_ids),
        }

    def get_memory(self, environment_id: str, memory_id: str) -> MemoryRecordV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_memory_records
                WHERE environment_id=? AND id=?
                """,
                (environment_id, memory_id),
            ).fetchone()
        return MemoryRecordV1.model_validate_json(row["body_json"]) if row else None

    def list_memories(
        self,
        environment_id: str,
        *,
        subject_id: str | None = None,
    ) -> list[MemoryRecordV1]:
        query = "SELECT body_json FROM ai_player_memory_records WHERE environment_id=?"
        parameters: list[Any] = [environment_id]
        if subject_id is not None:
            query += " AND subject_id=?"
            parameters.append(subject_id)
        query += " ORDER BY created_at, id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [MemoryRecordV1.model_validate_json(row["body_json"]) for row in rows]

    def invalidate_memory(
        self,
        environment_id: str,
        memory_id: str,
        *,
        reason: str,
        invalidation_id: str | None = None,
        evidence_refs: Sequence[EvidenceReferenceV1] | None = None,
    ) -> MemoryRecordV1:
        existing = self.get_memory(environment_id, memory_id)
        if existing is None:
            raise KeyError(f"unknown memory record: {environment_id}/{memory_id}")
        payload = existing.model_dump()
        payload.update(
            {
                "id": invalidation_id or f"{memory_id}.invalidation.v{existing.version + 1}",
                "version": existing.version + 1,
                "status": "invalidated",
                "supersedes_id": existing.id,
                "invalidation_reason": reason,
                "evidence_refs": list(evidence_refs or existing.evidence_refs),
                "created_at": utc_now(),
            }
        )
        invalidation = MemoryRecordV1.model_validate(payload)
        return self.append_memory(invalidation)

    def apply_state_transition_ingest(
        self,
        environment_id: str,
        observations: Sequence[StateObservationV1],
        transition_intents: Sequence[StateTransitionIntent],
    ) -> dict[str, Any]:
        """Recognize observations and persist their edges in one SQLite transaction."""

        environment = self.get_environment(environment_id)
        if environment is None:
            raise KeyError(f"unknown AI-player environment: {environment_id}")
        selection = self.select_environment_lineage(environment_id)
        if selection.selected_environment_id != environment_id:
            raise ValueError(
                "state transition ingest requires the current environment leaf: "
                f"{environment_id} -> {selection.selected_environment_id}"
            )
        if not observations:
            raise ValueError("state transition ingest requires observations")
        if any(item.environment_id != environment_id for item in observations):
            raise ValueError("state observation environment does not match ingest environment")
        observation_ids = [item.id for item in observations]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("state observation ids must be unique inside one ingest")
        intent_ids = [item.id for item in transition_intents]
        if len(intent_ids) != len(set(intent_ids)):
            raise ValueError("transition intent ids must be unique inside one ingest")
        for intent in transition_intents:
            if intent.action.type in LIFECYCLE_ACTION_TYPES:
                raise ValueError(
                    f"lifecycle action cannot enter semantic state ingest: {intent.id}"
                )
            missing = {
                intent.before_observation_id,
                intent.after_observation_id,
            } - set(observation_ids)
            if missing:
                raise ValueError(
                    f"transition intent references undeclared observations: {sorted(missing)}"
                )
            if any(
                reference.environment_id != environment_id
                for reference in intent.evidence_refs
            ):
                raise ValueError("transition intent evidence environment mismatch")

        authoritative_states: dict[str, tuple[str, str]] = {}
        for intent in transition_intents:
            guarded_endpoints = (
                (
                    intent.before_observation_id,
                    intent.authoritative_before_state_id,
                    "source_state_guard",
                ),
                (
                    intent.after_observation_id,
                    intent.authoritative_after_state_id,
                    "expected_state_guard",
                ),
            )
            for observation_id, state_id, method in guarded_endpoints:
                if state_id is None:
                    continue
                previous = authoritative_states.setdefault(
                    observation_id,
                    (state_id, method),
                )
                if previous[0] != state_id:
                    raise ValueError(
                        "one observation cannot be guarded by multiple semantic states: "
                        f"{observation_id}"
                    )

        references = [
            reference
            for observation in observations
            for reference in observation.evidence_refs
        ]
        references.extend(
            reference
            for intent in transition_intents
            for reference in intent.evidence_refs
        )
        self.resolve_evidence_references(
            references,
            environment_scope=environment,
        )

        with self._write_lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_terminal_passed_state_evidence(connection, references)
            transaction = _StateRecognitionTransactionView(self, connection)
            decisions: dict[str, StateRecognitionDecisionV1] = {}
            from .state_recognition import SemanticStateRecognizer

            for observation in observations:
                recognizer = SemanticStateRecognizer(
                    transaction,  # type: ignore[arg-type]
                    created_at=observation.created_at,
                )
                authoritative_state = authoritative_states.get(observation.id)
                decision = (
                    recognizer.recognize_from_verified_state_guard(
                        observation,
                        authoritative_state[0],
                        method=authoritative_state[1],
                    )
                    if authoritative_state is not None
                    else recognizer.recognize(observation)
                )
                decisions[observation.id] = decision

            edges: list[TransitionEdgeV1] = []
            for intent in transition_intents:
                before = decisions[intent.before_observation_id]
                after = decisions[intent.after_observation_id]
                before_state = transaction.get_semantic_state(
                    environment_id,
                    before.state_id,
                )
                after_state = transaction.get_semantic_state(
                    environment_id,
                    after.state_id,
                )
                if before_state is None or after_state is None:
                    raise ValueError("recognized transition endpoint state is missing")
                state_changed = before.state_id != after.state_id
                verified = (
                    before.disposition == "recognized_existing"
                    and after.disposition == "recognized_existing"
                    and before_state.status == "accepted"
                    and after_state.status == "accepted"
                )
                same_state_review = self._reviewed_same_state_semantic(
                    connection,
                    environment_id,
                    intent.id,
                )
                desired_outcome = (
                    "verified_transition"
                    if verified and state_changed
                    else "verified_progress"
                    if verified and same_state_review == "same_state_progress"
                    else "verified_no_change"
                    if verified and same_state_review == "same_state_no_progress"
                    else "deferred"
                )
                desired_observed_change = (
                    "动作后归类到新的已审定语义状态"
                    if desired_outcome == "verified_transition"
                    else "动作前后属于同一已审定操作态，独立复核确认内容或进度发生变化"
                    if desired_outcome == "verified_progress"
                    else "动作前后仍归类为同一已审定语义状态"
                    if desired_outcome == "verified_no_change"
                    else (
                        "动作证据完整，但至少一端语义状态仍为候选或待裁决，暂缓验证转移"
                        if not verified
                        else "动作前后属于同一操作态，尚无独立内容进度复核，暂缓判定"
                    )
                )
                existing_edge = transaction.get_transition_edge(
                    environment_id,
                    intent.id,
                )
                edge = TransitionEdgeV1(
                    id=intent.id,
                    version=existing_edge.version if existing_edge else 1,
                    environment_id=environment_id,
                    from_state_id=before.state_id,
                    to_state_id=after.state_id,
                    action=intent.action,
                    target_bounds=intent.target_bounds,
                    expected_change=intent.expected_change,
                    observed_change=desired_observed_change,
                    outcome=desired_outcome,
                    evidence_refs=list(intent.evidence_refs),
                    created_at=intent.created_at,
                )
                if existing_edge is not None and edge != existing_edge:
                    existing_covers_replay = (
                        existing_edge.id == edge.id
                        and existing_edge.environment_id == edge.environment_id
                        and existing_edge.from_state_id == edge.from_state_id
                        and existing_edge.to_state_id == edge.to_state_id
                        and existing_edge.action == edge.action
                        and existing_edge.target_bounds == edge.target_bounds
                        and existing_edge.expected_change == edge.expected_change
                        and existing_edge.outcome == edge.outcome
                        and existing_edge.recovery_skill_version_id
                        == edge.recovery_skill_version_id
                        and all(
                            reference in existing_edge.evidence_refs
                            for reference in edge.evidence_refs
                        )
                    )
                    if existing_covers_replay:
                        edge = existing_edge
                    elif existing_edge.outcome != "deferred" or not verified:
                        raise ValueError(
                            "transition edge revision is only allowed from deferred "
                            f"to verified after adjudication: {intent.id}"
                        )
                    else:
                        edge = edge.model_copy(update={"version": existing_edge.version + 1})
                transaction.put_transition_edge(edge)
                edges.append(edge)

        return {
            **transaction.inserted_counts,
            "decisions": decisions,
            "edges": edges,
        }

    @staticmethod
    def _reviewed_same_state_semantic(
        connection: sqlite3.Connection,
        environment_id: str,
        edge_id: str,
    ) -> str | None:
        rows = connection.execute(
            """
            SELECT body_json FROM ai_player_state_adjudications
            WHERE environment_id=? ORDER BY created_at DESC, id DESC
            """,
            (environment_id,),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["body_json"])
            for decision in payload.get("transition_decisions", []):
                if decision.get("edge_id") != edge_id:
                    continue
                semantic = decision.get("semantic_observation")
                if semantic in {"same_state_progress", "same_state_no_progress"}:
                    return str(semantic)
                return None
        return None

    def _require_terminal_passed_state_evidence(
        self,
        connection: sqlite3.Connection,
        references: Sequence[EvidenceReferenceV1],
    ) -> None:
        """Recheck terminal-passed run/step truth under the ingest write lock."""

        run_cache: dict[str, EvidenceRun] = {}
        step_cache: dict[str, EvidenceStep] = {}

        def require_run(run_id: str) -> EvidenceRun:
            if run_id not in run_cache:
                row = connection.execute(
                    "SELECT body_json FROM evidence_runs WHERE id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"dead evidence reference: evidence_run:{run_id}")
                run_cache[run_id] = EvidenceRun.model_validate_json(row["body_json"])
            run = run_cache[run_id]
            if run.status != "passed" or not run.ended_at:
                raise ValueError(f"state evidence run is not terminal-passed: {run_id}")
            if run.environment.get("semantic_state_eligible") is False:
                raise ValueError(
                    f"state EvidenceRun is marked semantic_state_eligible=false: {run_id}"
                )
            return run

        def require_step(step_id: str) -> EvidenceStep:
            if step_id not in step_cache:
                row = connection.execute(
                    "SELECT body_json FROM evidence_steps WHERE id=?",
                    (step_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"dead evidence reference: evidence_step:{step_id}")
                step_cache[step_id] = EvidenceStep.model_validate_json(row["body_json"])
            step = step_cache[step_id]
            if step.status != "passed" or not step.ended_at:
                raise ValueError(f"state evidence step is not terminal-passed: {step_id}")
            if step.action.type in LIFECYCLE_ACTION_TYPES:
                raise ValueError(
                    f"lifecycle EvidenceStep cannot enter semantic state ingest: {step_id}"
                )
            if step.metadata.get("semantic_state_eligible") is False:
                raise ValueError(
                    f"state EvidenceStep is marked semantic_state_eligible=false: {step_id}"
                )
            publication_issues = step.publication_issues()
            if publication_issues:
                raise ValueError(
                    f"state EvidenceStep is not publication-complete: {step_id}: "
                    + "; ".join(publication_issues)
                )
            return step

        for reference in references:
            if not reference.evidence_run_ids or not reference.evidence_step_ids:
                raise ValueError(
                    "state evidence must retain both EvidenceRun and EvidenceStep ids"
                )
            for run_id in reference.evidence_run_ids:
                require_run(run_id)
            for step_id in reference.evidence_step_ids:
                step = require_step(step_id)
                run = require_run(step.evidence_run_id)
                if step.evidence_run_id not in reference.evidence_run_ids:
                    raise ValueError(
                        f"state evidence step belongs to an unlisted run: {step_id}"
                    )
                if step.id not in run.step_ids:
                    raise ValueError(
                        f"state evidence run does not retain step: {step_id}"
                    )
            for artifact_id in reference.artifact_ids:
                row = connection.execute(
                    "SELECT body_json FROM artifacts WHERE id=?",
                    (artifact_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"dead evidence reference: artifact:{artifact_id}")
                artifact = ArtifactRef.model_validate_json(row["body_json"])
                if artifact.metadata.get("semantic_state_eligible") is False:
                    raise ValueError(
                        "state artifact is marked semantic_state_eligible=false: "
                        f"{artifact_id}"
                    )
                artifact_path = Path(artifact.path).resolve()
                artifact_root = self.observatory_store.artifact_root.resolve()
                if artifact_path != artifact_root and artifact_root not in artifact_path.parents:
                    raise ValueError(
                        f"artifact path escapes canonical Observatory root: {artifact_id}"
                    )
                if not artifact_path.is_file():
                    raise ValueError(f"dead evidence artifact file: {artifact_id}")
                if hashlib.sha256(artifact_path.read_bytes()).hexdigest() != artifact.sha256:
                    raise ValueError(f"evidence artifact hash mismatch: {artifact_id}")
                metadata_run_id = artifact.metadata.get("evidence_run_id")
                metadata_step_id = artifact.metadata.get("evidence_step_id")
                if metadata_run_id not in reference.evidence_run_ids:
                    raise ValueError(
                        f"artifact EvidenceRun binding is absent from reference: {artifact_id}"
                    )
                if metadata_step_id not in reference.evidence_step_ids:
                    raise ValueError(
                        f"artifact EvidenceStep binding is absent from reference: {artifact_id}"
                    )
                step = require_step(metadata_step_id)
                if step.evidence_run_id != metadata_run_id:
                    raise ValueError(
                        f"artifact EvidenceRun/EvidenceStep binding mismatch: {artifact_id}"
                    )
                if artifact_id not in step.artifact_ids:
                    raise ValueError(f"artifact is not retained by its EvidenceStep: {artifact_id}")
                expected_roles = {
                    step.before_frame_id: "before",
                    step.before_ui_tree_id: "before_ui_tree",
                    step.after_frame_id: "after",
                    step.after_ui_tree_id: "after_ui_tree",
                    step.video_artifact_id: "action_window_video",
                }
                if step.metadata.get("observation_only"):
                    expected_role = (
                        "observation_ui_tree"
                        if artifact.kind == "ui_tree"
                        else "observation"
                    )
                else:
                    expected_role = expected_roles.get(artifact_id)
                if expected_role is not None and artifact.metadata.get(
                    "evidence_role"
                ) != expected_role:
                    raise ValueError(
                        f"artifact EvidenceStep role mismatch: {artifact_id}"
                    )


    def put_semantic_state(self, state: SemanticStateV1) -> SemanticStateV1:
        self._prepare_entity(state.environment_id, state.evidence_refs)
        body = state.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            current_row = connection.execute(
                """
                SELECT body_json FROM ai_player_semantic_states
                WHERE environment_id=? AND id=? ORDER BY version DESC LIMIT 1
                """,
                (state.environment_id, state.id),
            ).fetchone()
            current = (
                SemanticStateV1.model_validate_json(current_row["body_json"])
                if current_row is not None
                else None
            )
            if (
                current is not None
                and current.status == "candidate"
                and state.version > current.version
                and state.status != "candidate"
            ):
                raise ValueError(
                    "candidate state lifecycle changes require signed state adjudication"
                )
            existing = connection.execute(
                """
                SELECT body_json FROM ai_player_semantic_states
                WHERE environment_id=? AND id=? AND version=?
                """,
                (state.environment_id, state.id, state.version),
            ).fetchone()
            if existing:
                if existing["body_json"] == body:
                    return state
                raise ValueError(
                    f"semantic state version is immutable: {state.id}@{state.version}"
                )
            connection.execute(
                """
                INSERT INTO ai_player_semantic_states(
                    environment_id, id, version, status, semantic_fingerprint,
                    body_json, created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    state.environment_id,
                    state.id,
                    state.version,
                    state.status,
                    state.semantic_fingerprint,
                    body,
                    state.created_at,
                ),
            )
            _register_canonical_state_locked(connection, state)
            self._record_evidence(
                connection,
                state.environment_id,
                "semantic_state",
                state.id,
                str(state.version),
                state.evidence_refs,
            )
        return state

    def get_semantic_state(
        self,
        environment_id: str,
        state_id: str,
        *,
        version: int | None = None,
    ) -> SemanticStateV1 | None:
        query = (
            "SELECT body_json FROM ai_player_semantic_states "
            "WHERE environment_id=? AND id=?"
        )
        parameters: list[Any] = [environment_id, state_id]
        if version is not None:
            query += " AND version=?"
            parameters.append(version)
        query += " ORDER BY version DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return SemanticStateV1.model_validate_json(row["body_json"]) if row else None

    def list_semantic_states(
        self,
        environment_id: str,
        *,
        statuses: Sequence[str] | None = None,
        latest_only: bool = True,
    ) -> list[SemanticStateV1]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_semantic_states
                WHERE environment_id=? ORDER BY id, version DESC
                """,
                (environment_id,),
            ).fetchall()
        states = [SemanticStateV1.model_validate_json(row["body_json"]) for row in rows]
        if latest_only:
            latest: dict[str, SemanticStateV1] = {}
            for state in states:
                latest.setdefault(state.id, state)
            states = list(latest.values())
        if statuses is not None:
            allowed = set(statuses)
            states = [state for state in states if state.status in allowed]
        return sorted(states, key=lambda state: (state.created_at, state.id, state.version))

    def append_state_observation(
        self,
        observation: StateObservationV1,
    ) -> StateObservationV1:
        self._prepare_entity(observation.environment_id, observation.evidence_refs)
        body = observation.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            existing = connection.execute(
                """
                SELECT body_json FROM ai_player_state_observations
                WHERE environment_id=? AND id=?
                """,
                (observation.environment_id, observation.id),
            ).fetchone()
            if existing:
                if existing["body_json"] == body:
                    return observation
                raise ValueError(f"state observation is immutable: {observation.id}")
            connection.execute(
                """
                INSERT INTO ai_player_state_observations(
                    environment_id, id, feature_hash, body_json, captured_at, created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    observation.environment_id,
                    observation.id,
                    observation.feature_hash,
                    body,
                    observation.captured_at,
                    observation.created_at,
                ),
            )
            self._record_evidence(
                connection,
                observation.environment_id,
                "state_observation",
                observation.id,
                "1",
                observation.evidence_refs,
            )
        return observation

    def get_state_observation(
        self,
        environment_id: str,
        observation_id: str,
    ) -> StateObservationV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_state_observations
                WHERE environment_id=? AND id=?
                """,
                (environment_id, observation_id),
            ).fetchone()
        return StateObservationV1.model_validate_json(row["body_json"]) if row else None

    def list_state_observations(
        self,
        environment_id: str,
        *,
        feature_hash: str | None = None,
    ) -> list[StateObservationV1]:
        query = "SELECT body_json FROM ai_player_state_observations WHERE environment_id=?"
        parameters: list[Any] = [environment_id]
        if feature_hash is not None:
            query += " AND feature_hash=?"
            parameters.append(feature_hash)
        query += " ORDER BY captured_at, id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [StateObservationV1.model_validate_json(row["body_json"]) for row in rows]

    def list_active_state_region_fingerprint_rows(
        self,
        environment_id: str,
        *,
        state_ids: Sequence[str] | None = None,
    ) -> list[StateRegionFingerprintProjectionRow]:
        """Load only fingerprints behind the latest active state assignments.

        A2 candidate matching needs ``state_id -> region_fingerprints`` and
        nothing else from the full observation and assignment contracts. Keep
        assignment ordering compatible with ``list_state_assignments`` so the
        caller's latest-per-state window remains unchanged.
        """

        state_filter = tuple(dict.fromkeys(state_ids or ()))
        if state_ids is not None and not state_filter:
            return []
        state_clause = ""
        parameters: list[Any] = [environment_id, environment_id]
        if state_filter:
            state_clause = (
                " AND assignment.state_id IN ("
                + ",".join("?" for _ in state_filter)
                + ")"
            )
            parameters.extend(state_filter)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                WITH latest AS (
                    SELECT observation_id, MAX(version) AS version
                    FROM ai_player_state_assignments
                    WHERE environment_id=?
                    GROUP BY observation_id
                )
                SELECT assignment.state_id,
                       json_extract(
                           CASE
                               WHEN json_valid(observation.body_json)
                               THEN observation.body_json
                               ELSE '{{}}'
                           END,
                           '$.features.region_fingerprints'
                       ) AS region_fingerprints_json
                FROM ai_player_state_assignments AS assignment
                JOIN latest
                  ON latest.observation_id=assignment.observation_id
                 AND latest.version=assignment.version
                JOIN ai_player_state_observations AS observation
                  ON observation.environment_id=assignment.environment_id
                 AND observation.id=assignment.observation_id
                WHERE assignment.environment_id=?
                  AND assignment.status='active'
                  {state_clause}
                  AND json_valid(observation.body_json)
                  AND json_type(
                      observation.body_json,
                      '$.features.region_fingerprints'
                  )='object'
                ORDER BY assignment.created_at, assignment.id, assignment.version
                """,
                parameters,
            ).fetchall()
        projected: list[StateRegionFingerprintProjectionRow] = []
        for row in rows:
            raw = row["region_fingerprints_json"]
            if not isinstance(raw, str):
                continue
            decoded = json.loads(raw)
            if not isinstance(decoded, dict) or not decoded:
                continue
            fingerprints = {
                str(key): str(value)
                for key, value in decoded.items()
                if isinstance(key, str) and isinstance(value, str) and key and value
            }
            if fingerprints:
                projected.append(
                    StateRegionFingerprintProjectionRow(
                        state_id=str(row["state_id"]),
                        region_fingerprints=fingerprints,
                    )
                )
        return projected

    def list_active_semantic_states_by_feature_hash(
        self,
        environment_id: str,
        feature_hash: str,
    ) -> list[SemanticStateV1]:
        with self._connection() as connection:
            return _active_semantic_states_for_feature_hash(
                connection,
                environment_id,
                feature_hash,
            )

    def append_state_assignment(self, assignment: StateAssignmentV1) -> StateAssignmentV1:
        self._prepare_entity(assignment.environment_id, assignment.evidence_refs)
        if self.get_state_observation(assignment.environment_id, assignment.observation_id) is None:
            raise ValueError(
                "state assignment observation is missing in environment: "
                f"{assignment.observation_id}"
            )
        self._require_semantic_state(assignment.environment_id, assignment.state_id)
        current = self.get_current_state_assignment(
            assignment.environment_id,
            assignment.observation_id,
        )
        if current is None and assignment.version != 1:
            raise ValueError("first state assignment must use version 1")
        if current is not None:
            if assignment.version != current.version + 1:
                raise ValueError("state assignment version must advance exactly once")
            if assignment.supersedes_id != current.id:
                raise ValueError("state assignment must name the assignment it supersedes")
        body = assignment.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_state_assignments(
                        environment_id, id, observation_id, state_id, version,
                        status, method, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        assignment.environment_id,
                        assignment.id,
                        assignment.observation_id,
                        assignment.state_id,
                        assignment.version,
                        assignment.status,
                        assignment.method,
                        body,
                        assignment.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"state assignment already exists: {assignment.id}") from error
            self._record_evidence(
                connection,
                assignment.environment_id,
                "state_assignment",
                assignment.id,
                str(assignment.version),
                assignment.evidence_refs,
            )
        return assignment

    def get_current_state_assignment(
        self,
        environment_id: str,
        observation_id: str,
    ) -> StateAssignmentV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_state_assignments
                WHERE environment_id=? AND observation_id=?
                ORDER BY version DESC LIMIT 1
                """,
                (environment_id, observation_id),
            ).fetchone()
        return StateAssignmentV1.model_validate_json(row["body_json"]) if row else None

    def get_latest_active_state_assignment(
        self,
        environment_id: str,
    ) -> StateAssignmentV1 | None:
        """Return the newest authoritative screen assignment with one indexed query."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT assignment.body_json
                FROM ai_player_state_assignments AS assignment
                LEFT JOIN ai_player_state_observations AS observation
                  ON observation.environment_id=assignment.environment_id
                 AND observation.id=assignment.observation_id
                WHERE assignment.environment_id=?
                  AND assignment.status='active'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM ai_player_state_assignments AS newer
                      WHERE newer.environment_id=assignment.environment_id
                        AND newer.observation_id=assignment.observation_id
                        AND newer.version>assignment.version
                  )
                ORDER BY COALESCE(observation.captured_at, assignment.created_at) DESC,
                         assignment.created_at DESC, assignment.id DESC
                LIMIT 1
                """,
                (environment_id,),
            ).fetchone()
        return StateAssignmentV1.model_validate_json(row["body_json"]) if row else None

    def list_state_assignments(
        self,
        environment_id: str,
        *,
        state_id: str | None = None,
        latest_only: bool = True,
    ) -> list[StateAssignmentV1]:
        query = "SELECT body_json FROM ai_player_state_assignments WHERE environment_id=?"
        parameters: list[Any] = [environment_id]
        if state_id is not None and not latest_only:
            query += " AND state_id=?"
            parameters.append(state_id)
        query += " ORDER BY observation_id, version DESC"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        assignments = [StateAssignmentV1.model_validate_json(row["body_json"]) for row in rows]
        if latest_only:
            latest: dict[str, StateAssignmentV1] = {}
            for assignment in assignments:
                latest.setdefault(assignment.observation_id, assignment)
            assignments = list(latest.values())
        if state_id is not None:
            assignments = [item for item in assignments if item.state_id == state_id]
        return sorted(assignments, key=lambda item: (item.created_at, item.id, item.version))

    def find_current_state_assignment_for_evidence(
        self,
        environment_id: str,
        *,
        evidence_step_ids: Sequence[str] = (),
        artifact_ids: Sequence[str] = (),
    ) -> StateAssignmentV1 | None:
        """Resolve a persisted terminal assignment without scanning every state."""

        reference_kind = "evidence_step" if evidence_step_ids else "artifact"
        reference_ids = list(dict.fromkeys(evidence_step_ids or artifact_ids))
        if not reference_ids:
            return None
        placeholders = ",".join("?" for _item in reference_ids)
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT assignment.body_json
                FROM ai_player_entity_evidence AS evidence
                JOIN ai_player_state_assignments AS assignment
                  ON assignment.environment_id=evidence.environment_id
                 AND assignment.id=evidence.entity_id
                 AND CAST(assignment.version AS TEXT)=evidence.entity_version
                WHERE evidence.environment_id=?
                  AND evidence.entity_type='state_assignment'
                  AND evidence.reference_kind=?
                  AND evidence.reference_id IN ({placeholders})
                  AND assignment.status='active'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM ai_player_state_assignments AS newer
                      WHERE newer.environment_id=assignment.environment_id
                        AND newer.observation_id=assignment.observation_id
                        AND newer.version>assignment.version
                  )
                ORDER BY assignment.created_at DESC, assignment.id DESC
                LIMIT 1
                """,
                [environment_id, reference_kind, *reference_ids],
            ).fetchone()
        return StateAssignmentV1.model_validate_json(row["body_json"]) if row else None

    def find_unique_current_state_observation_for_evidence(
        self,
        environment_id: str,
        *,
        state_id: str,
        evidence_step_id: str,
        artifact_id: str,
    ) -> StateObservationV1 | None:
        """Resolve one current state observation from its exact terminal evidence.

        The evidence reference index is the entry point.  Returning ``None``
        for both absent and ambiguous matches keeps safety-sensitive consumers
        fail closed without enumerating every assignment in the environment.
        """

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT observation.body_json
                FROM ai_player_entity_evidence AS step_evidence
                     INDEXED BY idx_ai_player_evidence_reference
                JOIN ai_player_state_observations AS observation
                  ON observation.environment_id=step_evidence.environment_id
                 AND observation.id=step_evidence.entity_id
                JOIN ai_player_state_assignments AS assignment
                  ON assignment.environment_id=observation.environment_id
                 AND assignment.observation_id=observation.id
                JOIN ai_player_entity_evidence AS artifact_evidence
                  ON artifact_evidence.environment_id=observation.environment_id
                 AND artifact_evidence.entity_type='state_observation'
                 AND artifact_evidence.entity_id=observation.id
                 AND artifact_evidence.entity_version=step_evidence.entity_version
                 AND artifact_evidence.reference_kind='artifact'
                 AND artifact_evidence.reference_id=?
                WHERE step_evidence.environment_id=?
                  AND step_evidence.entity_type='state_observation'
                  AND step_evidence.reference_kind='evidence_step'
                  AND step_evidence.reference_id=?
                  AND assignment.state_id=?
                  AND assignment.status='active'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM ai_player_state_assignments AS newer
                      WHERE newer.environment_id=assignment.environment_id
                        AND newer.observation_id=assignment.observation_id
                        AND newer.version>assignment.version
                  )
                GROUP BY observation.environment_id, observation.id
                ORDER BY assignment.created_at DESC, observation.id DESC
                LIMIT 2
                """,
                (artifact_id, environment_id, evidence_step_id, state_id),
            ).fetchall()
        if len(rows) != 1:
            return None
        return StateObservationV1.model_validate_json(rows[0]["body_json"])

    def list_recent_state_screenshot_prototypes(
        self,
        environment_id: str,
        state_id: str,
        *,
        limit: int = 8,
    ) -> list[ArtifactRef]:
        """Read recent active state prototypes in one indexed SQLite query."""

        bounded_limit = max(1, min(limit, 64))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT artifact.body_json
                FROM ai_player_state_assignments AS assignment
                JOIN ai_player_entity_evidence AS evidence
                  ON evidence.environment_id=assignment.environment_id
                 AND evidence.entity_type='state_observation'
                 AND evidence.entity_id=assignment.observation_id
                 AND evidence.reference_kind='artifact'
                JOIN artifacts AS artifact
                  ON artifact.id=evidence.reference_id
                 AND artifact.kind='screenshot'
                WHERE assignment.environment_id=?
                  AND assignment.state_id=?
                  AND assignment.status='active'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM ai_player_state_assignments AS newer
                      WHERE newer.environment_id=assignment.environment_id
                        AND newer.observation_id=assignment.observation_id
                        AND newer.version>assignment.version
                  )
                ORDER BY assignment.created_at DESC, artifact.created_at DESC,
                         artifact.id DESC
                LIMIT ?
                """,
                (environment_id, state_id, bounded_limit),
            ).fetchall()
        return [ArtifactRef.model_validate_json(row["body_json"]) for row in rows]

    def put_transition_edge(self, edge: TransitionEdgeV1) -> TransitionEdgeV1:
        self._prepare_entity(edge.environment_id, edge.evidence_refs)
        self._require_semantic_state(edge.environment_id, edge.from_state_id)
        if edge.to_state_id is not None:
            self._require_semantic_state(edge.environment_id, edge.to_state_id)
        if (
            edge.recovery_skill_version_id is not None
            and self.get_skill_version_by_id(
                edge.environment_id,
                edge.recovery_skill_version_id,
            )
            is None
        ):
            raise ValueError(
                "edge recovery skill version is missing in environment: "
                f"{edge.recovery_skill_version_id}"
            )
        if edge.outcome.startswith("verified_"):
            source = self.get_semantic_state(edge.environment_id, edge.from_state_id)
            destination = (
                self.get_semantic_state(edge.environment_id, edge.to_state_id)
                if edge.to_state_id is not None
                else None
            )
            if (
                source is None
                or destination is None
                or source.status != "accepted"
                or destination.status != "accepted"
            ):
                raise ValueError("verified transition edge requires accepted endpoints")
        body = edge.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            existing = connection.execute(
                """
                SELECT body_json FROM ai_player_transition_edges
                WHERE environment_id=? AND id=? AND version=?
                """,
                (edge.environment_id, edge.id, edge.version),
            ).fetchone()
            if existing:
                if existing["body_json"] == body:
                    return edge
                raise ValueError(
                    f"transition edge version is immutable: {edge.id}@{edge.version}"
                )
            connection.execute(
                """
                INSERT INTO ai_player_transition_edges(
                    environment_id, id, version, from_state_id, to_state_id,
                    outcome, body_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    edge.environment_id,
                    edge.id,
                    edge.version,
                    edge.from_state_id,
                    edge.to_state_id,
                    edge.outcome,
                    body,
                    edge.created_at,
                ),
            )
            self._record_evidence(
                connection,
                edge.environment_id,
                "transition_edge",
                edge.id,
                str(edge.version),
                edge.evidence_refs,
            )
        return edge

    def get_transition_edge(
        self,
        environment_id: str,
        edge_id: str,
        *,
        version: int | None = None,
    ) -> TransitionEdgeV1 | None:
        query = (
            "SELECT body_json FROM ai_player_transition_edges "
            "WHERE environment_id=? AND id=?"
        )
        parameters: list[Any] = [environment_id, edge_id]
        if version is not None:
            query += " AND version=?"
            parameters.append(version)
        query += " ORDER BY version DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return TransitionEdgeV1.model_validate_json(row["body_json"]) if row else None

    def list_transition_edges(
        self,
        environment_id: str,
        *,
        from_state_id: str | None = None,
        outcomes: Sequence[str] | None = None,
        latest_only: bool = True,
    ) -> list[TransitionEdgeV1]:
        query = "SELECT body_json FROM ai_player_transition_edges WHERE environment_id=?"
        parameters: list[Any] = [environment_id]
        if from_state_id is not None:
            query += " AND from_state_id=?"
            parameters.append(from_state_id)
        query += " ORDER BY id, version DESC"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        edges = [TransitionEdgeV1.model_validate_json(row["body_json"]) for row in rows]
        if latest_only:
            latest: dict[str, TransitionEdgeV1] = {}
            for edge in edges:
                latest.setdefault(edge.id, edge)
            edges = list(latest.values())
        if outcomes is not None:
            allowed = set(outcomes)
            edges = [edge for edge in edges if edge.outcome in allowed]
        return sorted(edges, key=lambda edge: (edge.created_at, edge.id, edge.version))

    def list_transition_edge_projection_rows(
        self,
        environment_id: str,
    ) -> list[TransitionEdgeProjectionRow]:
        """Return latest routing fields without validating full evidence payloads.

        Compact task/reuse projections only need edge identity, endpoints,
        outcome, and action kind.  Decoding every immutable evidence reference
        into ``TransitionEdgeV1`` made that hot read scale with historical
        payload size even though none of those fields were consumed.
        """

        query = """
            WITH ranked AS (
                SELECT
                    id,
                    from_state_id,
                    to_state_id,
                    outcome,
                    json_extract(body_json, '$.action.type') AS action_type,
                    created_at,
                    version,
                    ROW_NUMBER() OVER (
                        PARTITION BY id ORDER BY version DESC
                    ) AS latest_rank
                FROM ai_player_transition_edges
                WHERE environment_id=?
            )
            SELECT
                id,
                from_state_id,
                to_state_id,
                outcome,
                action_type,
                created_at,
                version
            FROM ranked
            WHERE latest_rank=1
            ORDER BY created_at,id,version
        """
        with self._connection() as connection:
            rows = connection.execute(query, (environment_id,)).fetchall()
        return [
            TransitionEdgeProjectionRow(
                id=str(row["id"]),
                from_state_id=str(row["from_state_id"]),
                to_state_id=(str(row["to_state_id"]) if row["to_state_id"] else None),
                outcome=str(row["outcome"]),
                action_type=(str(row["action_type"]) if row["action_type"] else None),
                created_at=str(row["created_at"]),
                version=int(row["version"]),
            )
            for row in rows
        ]

    def list_recent_transition_edges(
        self,
        environment_id: str,
        *,
        limit: int = 16,
    ) -> list[TransitionEdgeV1]:
        """Return a bounded latest-version window without scanning route history."""

        if limit < 1:
            raise ValueError("recent transition limit must be positive")
        query = """
            SELECT body_json FROM (
                SELECT
                    body_json,
                    created_at,
                    id,
                    version,
                    ROW_NUMBER() OVER (
                        PARTITION BY id ORDER BY version DESC
                    ) AS latest_rank
                FROM ai_player_transition_edges
                WHERE environment_id=?
            )
            WHERE latest_rank=1
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """
        with self._connection() as connection:
            rows = connection.execute(query, (environment_id, limit)).fetchall()
        edges = [TransitionEdgeV1.model_validate_json(row["body_json"]) for row in rows]
        return sorted(edges, key=lambda edge: (edge.created_at, edge.id, edge.version))

    def apply_state_adjudication(
        self,
        *,
        environment_id: str,
        adjudication_id: str,
        packet_sha256: str,
        seed_sha256: str,
        reviewer_id: str,
        reviewer_session_id: str,
        subject_session_ids: Sequence[str],
        state_revisions: Sequence[SemanticStateV1],
        assignment_revisions: Sequence[StateAssignmentV1],
        transition_evidence_refs: Sequence[EvidenceReferenceV1],
        adjudication_body_json: str,
        result_sha256: str,
        created_at: str,
    ) -> tuple[SemanticStateV1, ...]:
        """Atomically append signed review state revisions and its immutable ledger row."""

        environment = self.get_environment(environment_id)
        if environment is None:
            raise KeyError(f"unknown AI-player environment: {environment_id}")
        selection = self.select_environment_lineage(environment_id)
        if selection.selected_environment_id != environment_id:
            raise ValueError("state adjudication requires the current environment leaf")
        if any(item.environment_id != environment_id for item in state_revisions):
            raise ValueError("state adjudication revision environment mismatch")
        if len({item.id for item in state_revisions}) != len(state_revisions):
            raise ValueError("state adjudication state revisions must be unique")
        if any(item.environment_id != environment_id for item in assignment_revisions):
            raise ValueError("state adjudication assignment environment mismatch")
        if len({item.id for item in assignment_revisions}) != len(assignment_revisions):
            raise ValueError("state adjudication assignment revisions must be unique")
        if reviewer_session_id in set(subject_session_ids):
            raise ValueError("state adjudicator session cannot be an executing subject session")
        body = json.loads(adjudication_body_json)
        if not isinstance(body, dict) or body.get("seed_id") != adjudication_id:
            raise ValueError("state adjudication ledger body does not match its id")
        if not state_revisions and not body.get("transition_decisions"):
            raise ValueError("state adjudication requires a state or transition decision")
        references = [
            reference for item in state_revisions for reference in item.evidence_refs
        ]
        references.extend(
            reference for item in assignment_revisions for reference in item.evidence_refs
        )
        references.extend(transition_evidence_refs)
        self.resolve_evidence_references(references, environment_scope=environment)
        state_version_ids = [f"{item.id}@{item.version}" for item in state_revisions]
        assignment_version_ids = [
            f"{item.observation_id}@{item.version}" for item in assignment_revisions
        ]

        with self._write_lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT body_json, result_sha256 FROM ai_player_state_adjudications
                WHERE environment_id=? AND id=?
                """,
                (environment_id, adjudication_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["body_json"] != adjudication_body_json
                    or existing["result_sha256"] != result_sha256
                ):
                    raise ValueError(
                        f"state adjudication is immutable: {environment_id}/{adjudication_id}"
                    )
                transaction = _StateRecognitionTransactionView(self, connection)
                persisted = tuple(
                    transaction.get_semantic_state(
                        environment_id,
                        item.id,
                        version=item.version,
                    )
                    for item in state_revisions
                )
                if any(item is None for item in persisted) or tuple(
                    item for item in persisted if item is not None
                ) != tuple(state_revisions):
                    raise ValueError("state adjudication ledger output is not current")
                for assignment in assignment_revisions:
                    row = connection.execute(
                        """
                        SELECT body_json FROM ai_player_state_assignments
                        WHERE environment_id=? AND id=?
                        """,
                        (environment_id, assignment.id),
                    ).fetchone()
                    if row is None or StateAssignmentV1.model_validate_json(
                        row["body_json"]
                    ) != assignment:
                        raise ValueError("state adjudication assignment output is missing")
                return tuple(item for item in persisted if item is not None)

            self._require_terminal_passed_state_evidence(connection, references)
            transaction = _StateRecognitionTransactionView(self, connection)
            for revision in state_revisions:
                current = transaction.get_semantic_state(environment_id, revision.id)
                if current is None:
                    raise ValueError(f"state adjudication source is missing: {revision.id}")
                if current == revision:
                    continue
                if current.status == "accepted":
                    if revision.status != "accepted":
                        raise ValueError(
                            "accepted state enrichment must preserve accepted status: "
                            f"{revision.id}"
                        )
                    if (
                        revision.semantic_fingerprint != current.semantic_fingerprint
                        or revision.observation_feature_hashes
                        != current.observation_feature_hashes
                        or revision.evidence_refs != current.evidence_refs
                    ):
                        raise ValueError(
                            "accepted state enrichment cannot change identity or evidence: "
                            f"{revision.id}"
                        )
                elif current.status != "candidate":
                    raise ValueError(
                        "state adjudication source is neither candidate nor accepted: "
                        f"{revision.id}"
                    )
                if revision.version != current.version + 1:
                    raise ValueError(
                        f"state adjudication version is stale: {revision.id}"
                    )
                if revision.supersedes_id != current.id:
                    raise ValueError(
                        f"state adjudication revision lacks its source id: {revision.id}"
                    )
                if not any(
                    tag == f"adjudication-seed:{adjudication_id}"
                    for tag in revision.tags
                ):
                    raise ValueError(
                        f"state adjudication revision lacks ledger binding: {revision.id}"
                    )
                transaction.put_semantic_state(revision)

            for assignment in assignment_revisions:
                current = transaction.get_current_state_assignment(
                    environment_id,
                    assignment.observation_id,
                )
                if current == assignment:
                    continue
                if current is None or assignment.version != current.version + 1:
                    raise ValueError(
                        "state adjudication assignment version is stale: "
                        f"{assignment.observation_id}"
                    )
                if assignment.supersedes_id != current.id:
                    raise ValueError(
                        "state adjudication assignment lacks its source: "
                        f"{assignment.observation_id}"
                    )
                transaction.append_state_assignment(assignment)

            connection.execute(
                """
                INSERT INTO ai_player_state_adjudications(
                    environment_id, id, packet_sha256, seed_sha256,
                    reviewer_id, reviewer_session_id, subject_session_ids_json,
                    state_version_ids_json, assignment_version_ids_json,
                    body_json, result_sha256, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    environment_id,
                    adjudication_id,
                    packet_sha256,
                    seed_sha256,
                    reviewer_id,
                    reviewer_session_id,
                    json.dumps(list(subject_session_ids), ensure_ascii=False),
                    json.dumps(state_version_ids, ensure_ascii=False),
                    json.dumps(assignment_version_ids, ensure_ascii=False),
                    adjudication_body_json,
                    result_sha256,
                    created_at,
                ),
            )
        return tuple(state_revisions)

    def get_state_adjudication(
        self,
        environment_id: str,
        adjudication_id: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM ai_player_state_adjudications
                WHERE environment_id=? AND id=?
                """,
                (environment_id, adjudication_id),
            ).fetchone()
        if row is None:
            return None
        return {
            **dict(row),
            "subject_session_ids": json.loads(row["subject_session_ids_json"]),
            "state_version_ids": json.loads(row["state_version_ids_json"]),
            "assignment_version_ids": json.loads(
                row["assignment_version_ids_json"]
            ),
            "body": json.loads(row["body_json"]),
        }

    def list_state_adjudications(self, environment_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id FROM ai_player_state_adjudications
                WHERE environment_id=? ORDER BY created_at, id
                """,
                (environment_id,),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self.get_state_adjudication(environment_id, row["id"])) is not None
        ]

    def enqueue_task(self, task: FrontierTaskV1) -> FrontierTaskV1:
        if task.status != "queued":
            raise ValueError("a newly enqueued task must have queued status")
        self._prepare_entity(task.environment_id, task.evidence_refs)
        body = task.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_frontier_tasks(
                        environment_id, id, version, status, source, body_json,
                        created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        task.environment_id,
                        task.id,
                        task.version,
                        task.status,
                        task.source,
                        body,
                        task.created_at,
                        task.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"task already exists: {task.id}") from error
            self._record_evidence(
                connection,
                task.environment_id,
                "frontier_task",
                task.id,
                str(task.version),
                task.evidence_refs,
            )
        return task

    def get_task(self, environment_id: str, task_id: str) -> FrontierTaskV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_frontier_tasks
                WHERE environment_id=? AND id=?
                """,
                (environment_id, task_id),
            ).fetchone()
        return FrontierTaskV1.model_validate_json(row["body_json"]) if row else None

    def list_tasks(
        self,
        environment_id: str,
        *,
        statuses: Sequence[str] | None = None,
    ) -> list[FrontierTaskV1]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_frontier_tasks
                WHERE environment_id=? ORDER BY updated_at DESC, created_at DESC, id
                """,
                (environment_id,),
            ).fetchall()
        tasks = [FrontierTaskV1.model_validate_json(row["body_json"]) for row in rows]
        if statuses is not None:
            allowed = set(statuses)
            tasks = [task for task in tasks if task.status in allowed]
        return tasks

    def compare_and_swap_task_status(
        self,
        environment_id: str,
        task_id: str,
        expected_status: str,
        new_status: str,
        *,
        expected_version: int,
        updates: Mapping[str, Any] | None = None,
    ) -> FrontierTaskV1 | None:
        current = self.get_task(environment_id, task_id)
        if (
            current is None
            or current.status != expected_status
            or current.version != expected_version
        ):
            return None
        forbidden_updates = {"id", "environment_id", "status", "version", "created_at"}
        supplied_updates = dict(updates or {})
        if forbidden_updates.intersection(supplied_updates):
            raise ValueError("task CAS updates cannot replace identity or version fields")
        payload = current.model_dump()
        payload.update(supplied_updates)
        payload.update({"status": new_status, "version": expected_version + 1})
        replacement = FrontierTaskV1.model_validate(payload)
        self.resolve_evidence_references(replacement.evidence_refs)
        body = replacement.model_dump_json(by_alias=True)
        updated_at = utc_now()
        with self._write_lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE ai_player_frontier_tasks
                SET version=?, status=?, body_json=?, updated_at=?
                WHERE environment_id=? AND id=? AND status=? AND version=?
                """,
                (
                    replacement.version,
                    replacement.status,
                    body,
                    updated_at,
                    environment_id,
                    task_id,
                    expected_status,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                return None
            self._record_evidence(
                connection,
                replacement.environment_id,
                "frontier_task",
                replacement.id,
                str(replacement.version),
                replacement.evidence_refs,
            )
        return replacement

    def cas_task_status(
        self,
        environment_id: str,
        task_id: str,
        expected_status: str,
        new_status: str,
        *,
        expected_version: int,
        updates: Mapping[str, Any] | None = None,
    ) -> FrontierTaskV1 | None:
        return self.compare_and_swap_task_status(
            environment_id,
            task_id,
            expected_status,
            new_status,
            expected_version=expected_version,
            updates=updates,
        )

    def append_skill_version(self, skill: SkillVersionV1) -> SkillVersionV1:
        environment = self.get_environment(skill.environment_id)
        if environment is None:
            raise KeyError(f"unknown AI-player environment: {skill.environment_id}")
        self._assert_skill_applicability(environment, skill)
        latest = self.get_skill_version(skill.environment_id, skill.skill_id)
        if latest is None:
            if skill.version != 1 or skill.source_skill_version_id is not None:
                raise ValueError("a new skill must start at version 1 without a parent")
        elif (
            skill.version != latest.version + 1
            or skill.source_skill_version_id != latest.id
        ):
            raise ValueError("a skill successor must be the next version of the latest parent")
        self._prepare_entity(skill.environment_id, skill.evidence_refs)
        for transition_id in skill.source_transition_ids:
            if self.get_transition_edge(skill.environment_id, transition_id) is None:
                raise ValueError(
                    f"skill source transition is missing in environment: {transition_id}"
                )
        for recovery_id in skill.recovery_skill_version_ids:
            if self.get_skill_version_by_id(skill.environment_id, recovery_id) is None:
                raise ValueError(f"skill recovery version is missing in environment: {recovery_id}")
        if skill.source_skill_version_id is not None:
            source_skill = self.get_skill_version_by_id(
                skill.environment_id,
                skill.source_skill_version_id,
            )
            if source_skill is None:
                raise ValueError(
                    "skill source version is missing in environment: "
                    f"{skill.source_skill_version_id}"
                )
            if source_skill.skill_id != skill.skill_id:
                raise ValueError("a skill lifecycle successor must keep the same skill_id")
        dependency_ids = {
            step.subskill_version_id
            for step in skill.steps
            if step.subskill_version_id is not None
        }
        dependency_ids.update(skill.recovery_skill_version_ids)
        if skill.id in dependency_ids:
            raise ValueError("a skill cannot depend on itself")
        for dependency_id in dependency_ids:
            dependency = self.get_skill_version_by_id(skill.environment_id, dependency_id)
            if dependency is None:
                raise ValueError(f"skill dependency is missing in environment: {dependency_id}")
            safety_order = {
                "read_only": 0,
                "reversible": 1,
                "progression": 2,
                "social": 3,
                "economic": 4,
                "restricted": 5,
            }
            if safety_order[dependency.safety_level] > safety_order[skill.safety_level]:
                raise ValueError("a skill dependency exceeds the parent safety level")
            for step in skill.steps:
                if step.subskill_version_id != dependency_id:
                    continue
                step_effect_level = "read_only" if step.side_effect == "none" else step.side_effect
                if safety_order[dependency.safety_level] > safety_order[step_effect_level]:
                    raise ValueError("a subskill exceeds its step-declared side effect")
        self._assert_acyclic_skill_dependencies(skill, dependency_ids)
        declared_validation = (
            self.get_skill_validation(skill.environment_id, skill.validation_id)
            if skill.validation_id is not None
            else None
        )
        if latest is None and skill.status != "candidate":
            raise ValueError("a new skill lifecycle must begin as a candidate")
        if skill.status == "preferred":
            if latest is None or latest.status != "candidate":
                raise ValueError("a preferred skill must directly promote the latest candidate")
            if declared_validation is None or declared_validation.status != "passed":
                raise ValueError("a preferred skill requires a passed canonical validation")
            if declared_validation.skill_version_id != latest.id:
                raise ValueError("a preferred skill validation must bind its direct candidate")
            self.verify_skill_validation(declared_validation)
            if skill.content_sha256 != latest.content_sha256:
                raise ValueError("a preferred successor cannot change validated executable content")
        if skill.status in {"degraded", "invalidated"}:
            if latest is None or skill.content_sha256 != latest.content_sha256:
                raise ValueError("a non-executable successor must preserve its parent content")
        validation_references: list[EvidenceReferenceV1] = []
        for validation_run_id in skill.validation_run_ids:
            skill_run = self.get_skill_run(skill.environment_id, validation_run_id)
            if skill_run is not None:
                allowed_skill_version_ids = {
                    skill.id,
                    skill.source_skill_version_id,
                }
                if declared_validation is not None:
                    allowed_skill_version_ids.add(declared_validation.skill_version_id)
                if skill_run.skill_version_id not in allowed_skill_version_ids:
                    raise ValueError(
                        "skill validation run belongs to a different skill version: "
                        f"{validation_run_id}"
                    )
                validation_references.extend(skill_run.evidence_refs)
                continue
            reference_kind, run = self._resolve_validation_run(validation_run_id)
            self._assert_reference_environment(
                skill.environment_id,
                reference_kind,
                validation_run_id,
            )
            self._assert_run_scope(
                skill.environment_id,
                reference_kind,
                validation_run_id,
                run,
            )
            reference_field = (
                {"evidence_run_ids": [validation_run_id]}
                if reference_kind == "evidence_run"
                else {"trace_run_ids": [validation_run_id]}
            )
            validation_references.append(
                EvidenceReferenceV1(
                    environment_id=skill.environment_id,
                    **reference_field,
                )
            )
        if skill.validation_id is not None:
            validation = declared_validation
            if validation is None:
                raise ValueError(f"skill validation is missing in environment: {skill.validation_id}")
            if validation.skill_version_id not in {
                skill.id,
                skill.source_skill_version_id,
            } and not self._skill_version_is_ancestor(
                skill.environment_id,
                descendant_id=skill.source_skill_version_id,
                ancestor_id=validation.skill_version_id,
            ):
                raise ValueError("skill validation belongs to a different skill version")
            if skill.validation_run_ids != validation.skill_run_ids:
                raise ValueError("skill validation run ids must match the validation record")
            validation_references.extend(validation.evidence_refs)
        if skill.status == "candidate" and (
            skill.validation_id is not None
            or skill.validation_run_ids
            or skill.independent_reset_count
            or skill.visual_variant_count
            or skill.failure_recovery_verified
        ):
            raise ValueError("a candidate cannot inherit or claim validation gates")
        body = skill.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_skill_versions(
                        environment_id, id, skill_id, version, level, status,
                        body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        skill.environment_id,
                        skill.id,
                        skill.skill_id,
                        skill.version,
                        skill.level,
                        skill.status,
                        body,
                        skill.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"skill version is append-only and already exists: "
                    f"{skill.skill_id}@{skill.version}"
                ) from error
            self._record_evidence(
                connection,
                skill.environment_id,
                "skill_version",
                skill.id,
                str(skill.version),
                [*skill.evidence_refs, *validation_references],
            )
        return skill

    def _skill_version_is_ancestor(
        self,
        environment_id: str,
        *,
        descendant_id: str | None,
        ancestor_id: str,
    ) -> bool:
        cursor_id = descendant_id
        visited: set[str] = set()
        while cursor_id is not None and cursor_id not in visited:
            if cursor_id == ancestor_id:
                return True
            visited.add(cursor_id)
            cursor = self.get_skill_version_by_id(environment_id, cursor_id)
            if cursor is None:
                return False
            cursor_id = cursor.source_skill_version_id
        return False

    def _assert_acyclic_skill_dependencies(
        self,
        proposed: SkillVersionV1,
        dependency_ids: set[str],
    ) -> None:
        def dependencies(skill: SkillVersionV1) -> set[str]:
            result = set(skill.recovery_skill_version_ids)
            result.update(
                step.subskill_version_id
                for step in skill.steps
                if step.subskill_version_id is not None
            )
            return result

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(skill_version_id: str) -> None:
            if skill_version_id == proposed.id:
                raise ValueError("skill dependencies must form an acyclic graph")
            if skill_version_id in visiting:
                raise ValueError("skill dependencies must form an acyclic graph")
            if skill_version_id in visited:
                return
            visiting.add(skill_version_id)
            skill = self.get_skill_version_by_id(proposed.environment_id, skill_version_id)
            if skill is None:
                raise ValueError(f"skill dependency is missing in environment: {skill_version_id}")
            for dependency_id in dependencies(skill):
                visit(dependency_id)
            visiting.remove(skill_version_id)
            visited.add(skill_version_id)

        for dependency_id in dependency_ids:
            visit(dependency_id)

    @staticmethod
    def _assert_skill_applicability(
        environment: EnvironmentScopeV1,
        skill: SkillVersionV1,
    ) -> None:
        scope = skill.applicability_scope
        accepted_game_ids = {environment.game_id, *environment.game_id_aliases}
        accepted_build_ids = {
            environment.build_scope_id,
            *environment.build_scope_id_aliases,
        }
        accepted_device_ids = {
            environment.device_scope_id,
            *environment.device_scope_id_aliases,
        }
        if scope.game_id not in accepted_game_ids:
            raise ValueError("skill applicability game is outside the environment")
        if not accepted_build_ids.intersection(scope.build_scope_ids):
            raise ValueError("skill applicability build is outside the environment")
        if scope.channel != environment.channel or scope.locale != environment.locale:
            raise ValueError("skill applicability channel or locale is outside the environment")
        if not accepted_device_ids.intersection(scope.device_scope_ids):
            raise ValueError("skill applicability device is outside the environment")
        if environment.viewport_width not in scope.viewport_widths or (
            environment.viewport_height not in scope.viewport_heights
        ):
            raise ValueError("skill applicability viewport is outside the environment")
        optional_checks = (
            (scope.account_scope_ids, environment.account_scope_id, "account"),
            (scope.server_scope_ids, environment.server_scope_id, "server"),
            (scope.world_scope_ids, environment.world_scope_id, "world"),
        )
        for accepted, observed, label in optional_checks:
            if accepted and observed not in accepted:
                raise ValueError(f"skill applicability {label} is outside the environment")

    def append_skill_run(self, run: SkillRunV1) -> SkillRunV1:
        self._prepare_entity(run.environment_id, run.evidence_refs)
        skill = self.get_skill_version_by_id(run.environment_id, run.skill_version_id)
        if skill is None:
            raise ValueError(f"skill run version is missing in environment: {run.skill_version_id}")
        self._assert_skill_run_provenance(run, skill=skill)
        body = run.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_skill_runs(
                        environment_id, id, skill_version_id, outcome,
                        independent_reset_id, visual_variant_id, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        run.environment_id,
                        run.id,
                        run.skill_version_id,
                        run.outcome,
                        run.independent_reset_id,
                        run.visual_variant_id,
                        body,
                        run.finished_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"skill run is append-only and already exists: {run.id}") from error
            self._record_evidence(
                connection,
                run.environment_id,
                "skill_run",
                run.id,
                "1",
                run.evidence_refs,
            )
        return run

    def skill_run_provenance_sha256(
        self,
        evidence_refs: Sequence[EvidenceReferenceV1],
    ) -> str:
        """Hash persisted canonical JSON, independent of current model defaults."""

        reference_key = json.dumps(
            {
                "hash_schema": "raw-canonical-body-json.v1",
                "evidence_refs": [
                    reference.model_dump(mode="json", by_alias=True)
                    for reference in evidence_refs
                ],
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        storage_stamp = self._provenance_storage_stamp()
        cached = self._skill_run_provenance_cache
        if cached is not None and cached[0] == reference_key and cached[1] == storage_stamp:
            return cached[2]

        resolved = self.resolve_evidence_references(evidence_refs)


        def canonical_body(kind: str, record_id: str) -> dict[str, Any]:
            body = self.observatory_store.get_canonical_provenance_body(kind, record_id)
            if body is None:
                raise ValueError(f"dead canonical provenance record: {kind}:{record_id}")
            return body

        with self.observatory_store.canonical_read_snapshot():
            resolved = self.resolve_evidence_references(evidence_refs)
            payload: dict[str, Any] = {}
            for kind, items in resolved.items():
                payload[kind] = sorted(
                    (canonical_body(kind, item.id) for item in items),
                    key=lambda item: json.dumps(
                        item,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            run_ids = {
                item.id for item in resolved["evidence_run"] if isinstance(item, EvidenceRun)
            }
            run_ids.update(
                item.evidence_run_id
                for item in resolved["evidence_step"]
                if isinstance(item, EvidenceStep)
            )
            manifests = []
            for run_id in sorted(run_ids):
                manifest = self.observatory_store.get_canonical_provenance_body(
                    "evidence_manifest",
                    run_id,
                )
                if manifest is not None:
                    manifests.append(manifest)
            payload["evidence_manifest"] = manifests
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        final_stamp = self._provenance_storage_stamp()
        if final_stamp == storage_stamp:
            self._skill_run_provenance_cache = (reference_key, final_stamp, digest)
        return digest

    def _provenance_storage_stamp(self) -> tuple[tuple[int, int], ...]:
        """Detect canonical SQLite/WAL writes between provenance checks."""

        stamps: list[tuple[int, int]] = []
        for path in (self.db_path, Path(f"{self.db_path}-wal")):
            try:
                stat = path.stat()
            except FileNotFoundError:
                stamps.append((-1, -1))
            else:
                stamps.append((stat.st_mtime_ns, stat.st_size))
        return tuple(stamps)

    def verify_skill_run_provenance(self, run: SkillRunV1) -> None:
        skill = self.get_skill_version_by_id(run.environment_id, run.skill_version_id)
        if skill is None:
            raise ValueError(f"skill run version is missing in environment: {run.skill_version_id}")
        self._assert_skill_run_provenance(run, skill=skill)

    def _assert_skill_run_provenance(
        self,
        run: SkillRunV1,
        *,
        skill: SkillVersionV1,
    ) -> None:
        if skill.creator_id == run.validator_id:
            raise ValueError("a skill creator cannot attest its own skill run")
        referenced_run_ids = {
            item for reference in run.evidence_refs for item in reference.evidence_run_ids
        }
        referenced_step_ids = {
            item for reference in run.evidence_refs for item in reference.evidence_step_ids
        }
        if run.provenance_evidence_run_id not in referenced_run_ids:
            raise ValueError("skill run provenance EvidenceRun is not in its evidence references")
        if not set(run.provenance_evidence_step_ids).issubset(referenced_step_ids):
            raise ValueError("skill run provenance steps are not in its evidence references")
        evidence_run = self.observatory_store.get_evidence_run(run.provenance_evidence_run_id)
        manifest = self.observatory_store.get_evidence_manifest(run.provenance_evidence_run_id)
        if evidence_run is None or evidence_run.status not in {"passed", "failed", "stopped"}:
            raise ValueError("skill run requires a terminal canonical EvidenceRun")
        if manifest is None or not manifest.publishable or manifest.run != evidence_run:
            raise ValueError("skill run requires a matching publishable evidence manifest")
        manifest_steps = {step.id: step for step in manifest.steps}
        for step_id in run.provenance_evidence_step_ids:
            step = self.observatory_store.get_evidence_step(step_id)
            if (
                step is None
                or step.evidence_run_id != evidence_run.id
                or step.status not in {"passed", "failed", "stopped"}
                or manifest_steps.get(step_id) != step
            ):
                raise ValueError("skill run provenance contains a nonterminal or unrelated step")
        telemetry = evidence_run.environment.get("skill_validation")
        if not isinstance(telemetry, dict):
            raise ValueError("skill run EvidenceRun lacks canonical validation telemetry")
        expected = {
            "skill_version_id": run.skill_version_id,
            "skill_run_id": run.id,
            "validator_id": run.validator_id,
            "independent_reset_id": run.independent_reset_id,
            "visual_variant_id": run.visual_variant_id,
            "outcome": run.outcome,
            "precondition_satisfied": run.precondition_satisfied,
            "objective_success": run.objective_success,
            "validation_passed": run.validation_passed,
            "false_success": run.false_success,
            "safety_violation_count": run.safety_violation_count,
            "recovery_attempted": run.recovery_attempted,
            "recovery_succeeded": run.recovery_succeeded,
            "action_count": run.action_count,
            "model_input_tokens": run.model_input_tokens,
            "baseline_model_input_tokens": run.baseline_model_input_tokens,
            "decision_latency_ms": run.decision_latency_ms,
            "baseline_decision_latency_ms": run.baseline_decision_latency_ms,
        }
        mismatches = [key for key, value in expected.items() if telemetry.get(key) != value]
        if mismatches:
            raise ValueError(
                "skill run differs from canonical validation telemetry: "
                + ", ".join(mismatches)
            )
        actual_provenance_sha256 = self.skill_run_provenance_sha256(run.evidence_refs)
        if run.provenance_sha256 != actual_provenance_sha256:
            raise ValueError("skill run provenance hash does not match current canonical evidence")
        self.skill_validator_trust_store.verify(run)

    def get_skill_run(self, environment_id: str, run_id: str) -> SkillRunV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_skill_runs
                WHERE environment_id=? AND id=?
                """,
                (environment_id, run_id),
            ).fetchone()
        return SkillRunV1.model_validate_json(row["body_json"]) if row else None

    def list_skill_runs(
        self,
        environment_id: str,
        *,
        skill_version_id: str | None = None,
    ) -> list[SkillRunV1]:
        query = "SELECT body_json FROM ai_player_skill_runs WHERE environment_id=?"
        parameters: list[Any] = [environment_id]
        if skill_version_id is not None:
            query += " AND skill_version_id=?"
            parameters.append(skill_version_id)
        query += " ORDER BY created_at, id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [SkillRunV1.model_validate_json(row["body_json"]) for row in rows]

    def list_pending_deferred_skill_runs(
        self,
        environment_id: str,
    ) -> list[SkillRunV1]:
        """Read only SkillRuns whose own deferred action still needs sedimentation.

        SkillRun evidence also includes the preceding route boundary.  That
        source EvidenceRun may belong to another skill and must never enqueue
        this run.  Missing or malformed referenced evidence is selected so the
        strict consumer fails visibly instead of treating damaged provenance as
        settled.
        """

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT skill_run.body_json
                FROM ai_player_skill_runs AS skill_run
                WHERE skill_run.environment_id=?
                  AND EXISTS (
                      SELECT 1
                      FROM ai_player_entity_evidence AS run_evidence
                      LEFT JOIN evidence_runs AS deferred
                        ON deferred.id=run_evidence.reference_id
                      WHERE run_evidence.environment_id=skill_run.environment_id
                        AND run_evidence.entity_type='skill_run'
                        AND run_evidence.entity_id=skill_run.id
                        AND run_evidence.reference_kind='evidence_run'
                        AND (
                            deferred.id IS NULL
                            OR NOT json_valid(deferred.body_json)
                            OR (
                                json_extract(
                                    deferred.body_json,
                                    '$.environment.defer_semantic_sedimentation'
                                )=1
                                AND COALESCE(
                                    json_extract(
                                        deferred.body_json,
                                        '$.environment.skill_replay_version_id'
                                    ), ''
                                )=skill_run.skill_version_id
                                AND (
                                    COALESCE(
                                        json_extract(
                                            deferred.body_json,
                                            '$.environment.environment_id'
                                        ), ''
                                    )!=skill_run.environment_id
                                    OR NOT EXISTS (
                                        SELECT 1
                                        FROM evidence_steps AS any_step
                                        WHERE any_step.evidence_run_id=deferred.id
                                    )
                                    OR (
                                        COALESCE(
                                            json_extract(deferred.body_json, '$.status'),
                                            ''
                                        ) NOT IN ('failed', 'stopped')
                                        AND EXISTS (
                                            SELECT 1
                                            FROM evidence_steps AS deferred_step
                                            WHERE deferred_step.evidence_run_id=deferred.id
                                              AND NOT EXISTS (
                                                  SELECT 1
                                                  FROM ai_player_entity_evidence
                                                       AS assignment_evidence
                                                  JOIN ai_player_state_assignments AS assignment
                                                    ON assignment.environment_id=
                                                       assignment_evidence.environment_id
                                                   AND assignment.id=
                                                       assignment_evidence.entity_id
                                                   AND CAST(assignment.version AS TEXT)=
                                                       assignment_evidence.entity_version
                                                  WHERE assignment_evidence.environment_id=
                                                        skill_run.environment_id
                                                    AND assignment_evidence.entity_type=
                                                        'state_assignment'
                                                    AND assignment_evidence.reference_kind=
                                                        'evidence_step'
                                                    AND assignment_evidence.reference_id=
                                                        deferred_step.id
                                                    AND assignment.status='active'
                                                    AND NOT EXISTS (
                                                        SELECT 1
                                                        FROM ai_player_state_assignments AS newer
                                                        WHERE newer.environment_id=
                                                              assignment.environment_id
                                                          AND newer.observation_id=
                                                              assignment.observation_id
                                                          AND newer.version>assignment.version
                                                    )
                                              )
                                        )
                                    )
                                )
                            )
                        )
                  )
                ORDER BY skill_run.created_at, skill_run.id
                """,
                (environment_id,),
            ).fetchall()
        return [SkillRunV1.model_validate_json(row["body_json"]) for row in rows]

    def known_route_revision(self, environment_id: str) -> tuple[int, int, int, int]:
        """Return an append-only revision stamp for every fixed-graph input.

        Skill lifecycle changes and memory invalidations are represented by
        successor rows, while SkillRuns are immutable.  The three rowid high
        watermarks therefore invalidate a long-lived planner without hashing
        or decoding any canonical JSON.
        """

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COALESCE(MAX(rowid), 0)
                     FROM ai_player_skill_versions WHERE environment_id=?) AS skills,
                    (SELECT COALESCE(MAX(rowid), 0)
                     FROM ai_player_skill_runs WHERE environment_id=?) AS runs,
                    (SELECT COALESCE(MAX(rowid), 0)
                     FROM ai_player_memory_records WHERE environment_id=?) AS memories,
                    (SELECT COALESCE(MAX(rowid), 0)
                     FROM ai_player_state_assignments WHERE environment_id=?) AS assignments
                """,
                (environment_id, environment_id, environment_id, environment_id),
            ).fetchone()
        return (
            int(row["skills"]),
            int(row["runs"]),
            int(row["memories"]),
            int(row["assignments"]),
        )

    def list_known_route_skill_versions(
        self,
        environment_id: str,
        *,
        max_safety: str,
        require_successful_run: bool,
    ) -> list[SkillVersionV1]:
        """Load only latest versions that can possibly enter the fixed graph.

        SQLite performs lifecycle, executor, safety and proven-run filtering
        before Pydantic sees a body.  ``json_extract`` is guarded by
        ``json_valid`` so an unrelated damaged row cannot poison navigation.
        The Python planner still applies locator and candidate evidence rules;
        this query is a fail-closed candidate reduction, not a second source of
        route semantics.
        """

        try:
            maximum = _KNOWN_ROUTE_SAFETY_LEVELS.index(max_safety)
        except ValueError as error:
            raise KeyError(max_safety) from error
        accepted_safety = _KNOWN_ROUTE_SAFETY_LEVELS[: maximum + 1]
        safety_placeholders = ",".join("?" for _ in accepted_safety)
        safe_body = "CASE WHEN json_valid(skill.body_json) THEN skill.body_json ELSE '{}' END"
        successful_run_clause = ""
        parameters: list[Any] = [environment_id, *accepted_safety]
        if require_successful_run:
            successful_run_clause = """
                AND EXISTS (
                    SELECT 1
                    FROM ai_player_skill_runs AS success
                    WHERE success.environment_id=skill.environment_id
                      AND success.skill_version_id=skill.id
                      AND json_valid(success.body_json)
                      AND success.outcome='success'
                      AND json_extract(success.body_json, '$.objective_success')=1
                      AND json_extract(success.body_json, '$.validation_passed')=1
                      AND COALESCE(json_extract(success.body_json, '$.false_success'), 0)=0
                      AND COALESCE(
                            json_extract(success.body_json, '$.safety_violation_count'), 0
                          )=0
                      AND NOT EXISTS (
                          SELECT 1
                          FROM ai_player_skill_runs AS failure
                          WHERE failure.environment_id=success.environment_id
                            AND failure.skill_version_id=success.skill_version_id
                            AND (
                                failure.created_at > success.created_at
                                OR (
                                    failure.created_at=success.created_at
                                    AND failure.id > success.id
                                )
                            )
                            AND (
                                NOT json_valid(failure.body_json)
                                OR COALESCE(
                                    json_extract(failure.body_json, '$.false_success'), 0
                                )=1
                                OR COALESCE(
                                    json_extract(
                                        failure.body_json,
                                        '$.safety_violation_count'
                                    ), 0
                                )>0
                                OR failure.outcome='false_success'
                                OR (
                                    failure.outcome='failed'
                                    AND COALESCE(
                                        json_extract(
                                            failure.body_json,
                                            '$.recovery_succeeded'
                                        ), 0
                                    )=0
                                )
                            )
                      )
                )
            """
        query = f"""
            WITH latest AS (
                SELECT skill_id, MAX(version) AS version
                FROM ai_player_skill_versions
                WHERE environment_id=?
                GROUP BY skill_id
            )
            SELECT skill.body_json
            FROM ai_player_skill_versions AS skill
            JOIN latest
              ON latest.skill_id=skill.skill_id AND latest.version=skill.version
            WHERE skill.environment_id=?
              AND json_valid(skill.body_json)
              AND skill.status NOT IN ('degraded', 'invalidated')
              AND json_extract({safe_body}, '$.executor_kind')='normalized_actions'
              AND json_extract({safe_body}, '$.safety_level') IN ({safety_placeholders})
              AND (skill.level!='L3' OR skill.status IN ('validated', 'preferred'))
              {successful_run_clause}
            ORDER BY skill.created_at, skill.skill_id, skill.version
        """
        parameters.insert(1, environment_id)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [SkillVersionV1.model_validate_json(row["body_json"]) for row in rows]

    def list_known_route_run_summaries(
        self,
        environment_id: str,
        skill_version_ids: Sequence[str],
    ) -> list[KnownRouteSkillRunSummary]:
        """Read scalar run telemetry for selected graph skills, never run evidence JSON."""

        if not skill_version_ids:
            return []
        placeholders = ",".join("?" for _ in skill_version_ids)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id, skill_version_id, outcome, created_at,
                       json_valid(body_json) AS valid_json,
                       json_extract(
                           CASE WHEN json_valid(body_json) THEN body_json ELSE '{{}}' END,
                           '$.objective_success'
                       ) AS objective_success,
                       json_extract(
                           CASE WHEN json_valid(body_json) THEN body_json ELSE '{{}}' END,
                           '$.validation_passed'
                       ) AS validation_passed,
                       json_extract(
                           CASE WHEN json_valid(body_json) THEN body_json ELSE '{{}}' END,
                           '$.false_success'
                       ) AS false_success,
                       json_extract(
                           CASE WHEN json_valid(body_json) THEN body_json ELSE '{{}}' END,
                           '$.safety_violation_count'
                       ) AS safety_violation_count,
                       json_extract(
                           CASE WHEN json_valid(body_json) THEN body_json ELSE '{{}}' END,
                           '$.recovery_succeeded'
                       ) AS recovery_succeeded,
                       json_extract(
                           CASE WHEN json_valid(body_json) THEN body_json ELSE '{{}}' END,
                           '$.decision_latency_ms'
                       ) AS decision_latency_ms,
                       json_extract(
                           CASE WHEN json_valid(body_json) THEN body_json ELSE '{{}}' END,
                           '$.baseline_decision_latency_ms'
                       ) AS baseline_decision_latency_ms,
                       json_extract(
                           CASE WHEN json_valid(body_json) THEN body_json ELSE '{{}}' END,
                           '$.baseline_model_input_tokens'
                       ) AS baseline_model_input_tokens,
                       CASE WHEN EXISTS (
                           SELECT 1
                           FROM ai_player_entity_evidence AS run_evidence
                           LEFT JOIN evidence_runs AS deferred
                             ON deferred.id=run_evidence.reference_id
                           WHERE run_evidence.environment_id=skill_run.environment_id
                             AND run_evidence.entity_type='skill_run'
                             AND run_evidence.entity_id=skill_run.id
                             AND run_evidence.reference_kind='evidence_run'
                             AND (
                                 deferred.id IS NULL
                                 OR NOT json_valid(deferred.body_json)
                                 OR (
                                     json_extract(
                                         deferred.body_json,
                                         '$.environment.defer_semantic_sedimentation'
                                     )=1
                                     AND COALESCE(
                                            json_extract(
                                                deferred.body_json,
                                                '$.environment.skill_replay_version_id'
                                            ), ''
                                         )=skill_run.skill_version_id
                                     AND (
                                         COALESCE(
                                             json_extract(
                                                 deferred.body_json,
                                                 '$.environment.environment_id'
                                             ), ''
                                         )!=skill_run.environment_id
                                         OR NOT EXISTS (
                                             SELECT 1
                                             FROM evidence_steps AS deferred_step
                                             CROSS JOIN ai_player_entity_evidence
                                                  AS assignment_evidence
                                                  INDEXED BY idx_ai_player_evidence_reference
                                               ON assignment_evidence.environment_id=
                                                  skill_run.environment_id
                                              AND assignment_evidence.entity_type=
                                                  'state_assignment'
                                              AND assignment_evidence.reference_kind=
                                                  'evidence_step'
                                              AND assignment_evidence.reference_id=
                                                  deferred_step.id
                                             JOIN ai_player_state_assignments AS assignment
                                               ON assignment.environment_id=
                                                  assignment_evidence.environment_id
                                              AND assignment.id=
                                                  assignment_evidence.entity_id
                                              AND CAST(assignment.version AS TEXT)=
                                                  assignment_evidence.entity_version
                                             WHERE deferred_step.evidence_run_id=deferred.id
                                               AND assignment.status='active'
                                               AND NOT EXISTS (
                                                   SELECT 1
                                                   FROM ai_player_state_assignments AS newer
                                                   WHERE newer.environment_id=
                                                         assignment.environment_id
                                                     AND newer.observation_id=
                                                         assignment.observation_id
                                                     AND newer.version>assignment.version
                                               )
                                         )
                                     )
                                 )
                             )
                       ) THEN 0 ELSE 1 END AS semantic_sedimentation_settled
                FROM ai_player_skill_runs AS skill_run
                WHERE skill_run.environment_id=?
                  AND skill_run.skill_version_id IN ({placeholders})
                ORDER BY skill_run.created_at, skill_run.id
                """,
                (environment_id, *skill_version_ids),
            ).fetchall()

        summaries: list[KnownRouteSkillRunSummary] = []
        for row in rows:
            required = (
                "objective_success",
                "validation_passed",
                "false_success",
                "safety_violation_count",
                "decision_latency_ms",
                "baseline_decision_latency_ms",
                "baseline_model_input_tokens",
            )
            malformed = not row["valid_json"] or any(row[field] is None for field in required)
            if malformed:
                # A corrupt selected run is decisive negative evidence.  It can
                # never make a stale skill executable, while a later canonical
                # successful run may explicitly prove the operation again.
                summaries.append(
                    KnownRouteSkillRunSummary(
                        run_id=str(row["id"]),
                        skill_version_id=str(row["skill_version_id"]),
                        outcome="false_success",
                        objective_success=False,
                        validation_passed=False,
                        false_success=True,
                        safety_violation_count=1,
                        recovery_succeeded=False,
                        decision_latency_ms=0.0,
                        baseline_decision_latency_ms=0.0,
                        baseline_model_input_tokens=0,
                        semantic_sedimentation_settled=False,
                    )
                )
                continue
            summaries.append(
                KnownRouteSkillRunSummary(
                    run_id=str(row["id"]),
                    skill_version_id=str(row["skill_version_id"]),
                    outcome=str(row["outcome"]),
                    objective_success=bool(row["objective_success"]),
                    validation_passed=bool(row["validation_passed"]),
                    false_success=bool(row["false_success"]),
                    safety_violation_count=int(row["safety_violation_count"]),
                    recovery_succeeded=bool(row["recovery_succeeded"] or False),
                    decision_latency_ms=float(row["decision_latency_ms"]),
                    baseline_decision_latency_ms=float(
                        row["baseline_decision_latency_ms"]
                    ),
                    baseline_model_input_tokens=int(row["baseline_model_input_tokens"]),
                    semantic_sedimentation_settled=bool(
                        row["semantic_sedimentation_settled"]
                    ),
                )
            )
        return summaries

    def list_known_route_alias_memories(
        self,
        environment_id: str,
    ) -> list[KnownRouteAliasMemory]:
        """Project only active entry, terminal, and goal aliases used by routing."""

        safe_body = "CASE WHEN json_valid(body_json) THEN body_json ELSE '{}' END"
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    json_extract({safe_body}, '$.payload.schema') AS schema_id,
                    json_extract({safe_body}, '$.payload.skill_version_id') AS skill_version_id,
                    json_extract({safe_body}, '$.payload.observed_state_id') AS observed_state_id,
                    json_extract({safe_body}, '$.payload.required_state_id') AS required_state_id,
                    json_extract({safe_body}, '$.payload.successful_run_id') AS successful_run_id,
                    json_extract({safe_body}, '$.payload.goal_alias') AS goal_alias,
                    json_extract(
                        {safe_body}, '$.payload.requires_settled_run'
                    ) AS requires_settled_run,
                    json_extract({safe_body}, '$.payload.visual_distance') AS visual_distance,
                    CASE
                        WHEN json_type({safe_body}, '$.evidence_refs')='array'
                        THEN json_array_length({safe_body}, '$.evidence_refs')
                        ELSE 0
                    END AS evidence_ref_count,
                    json_extract({safe_body}, '$.payload.source_state_id') AS source_state_id,
                    json_extract(
                        {safe_body}, '$.payload.observed_terminal_state_id'
                    ) AS observed_terminal_state_id
                FROM ai_player_memory_records AS memory
                WHERE environment_id=? AND status='active' AND kind='procedural'
                  AND NOT EXISTS (
                      SELECT 1 FROM ai_player_memory_records AS successor
                      WHERE successor.environment_id=memory.environment_id
                        AND successor.supersedes_id=memory.id
                  )
                  AND json_extract({safe_body}, '$.payload.schema') IN (
                      'game-observatory.ai-player.known-skill-entry-alias.v1',
                      'game-observatory.ai-player.known-skill-terminal-alias.v1',
                      'game-observatory.ai-player.known-skill-goal-alias.v1'
                  )
                ORDER BY created_at, id
                """,
                (environment_id,),
            ).fetchall()
        result: list[KnownRouteAliasMemory] = []
        for row in rows:
            payload: dict[str, object] = {
                key: str(row[key])
                for key in (
                    "schema_id",
                    "skill_version_id",
                    "observed_state_id",
                    "required_state_id",
                    "successful_run_id",
                    "goal_alias",
                    "source_state_id",
                    "observed_terminal_state_id",
                )
                if row[key] is not None
            }
            if row["visual_distance"] is not None:
                payload["visual_distance"] = float(row["visual_distance"])
            if row["requires_settled_run"] is not None:
                payload["requires_settled_run"] = bool(row["requires_settled_run"])
            payload["evidence_ref_count"] = int(row["evidence_ref_count"] or 0)
            payload["schema"] = payload.pop("schema_id")
            result.append(
                KnownRouteAliasMemory(status="active", kind="procedural", payload=payload)
            )
        return result

    def verify_skill_validation(self, validation: SkillValidationV1) -> None:
        """Re-derive a validation from its currently resolved, attested evidence."""
        if self.get_skill_version_by_id(
            validation.environment_id,
            validation.skill_version_id,
        ) is None:
            raise ValueError(
                "skill validation version is missing in environment: "
                f"{validation.skill_version_id}"
            )
        runs = [
            self.get_skill_run(validation.environment_id, run_id)
            for run_id in validation.skill_run_ids
        ]
        if any(run is None for run in runs):
            raise ValueError("skill validation contains a missing run")
        typed_runs = [run for run in runs if run is not None]
        for run in typed_runs:
            self.verify_skill_run_provenance(run)
        if any(run.skill_version_id != validation.skill_version_id for run in typed_runs):
            raise ValueError("skill validation mixes different skill versions")
        derived = derive_skill_validation(
            environment_id=validation.environment_id,
            skill_version_id=validation.skill_version_id,
            evaluator=validation.evaluator,
            runs=typed_runs,
            created_at=validation.created_at,
        )
        if derived != validation:
            raise ValueError("skill validation must be derived exactly from immutable skill runs")

    def append_skill_validation(self, validation: SkillValidationV1) -> SkillValidationV1:
        self._prepare_entity(validation.environment_id, validation.evidence_refs)
        self.verify_skill_validation(validation)
        body = validation.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_skill_validations(
                        environment_id, id, skill_version_id, status,
                        total_run_count, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        validation.environment_id,
                        validation.id,
                        validation.skill_version_id,
                        validation.status,
                        validation.total_run_count,
                        body,
                        validation.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"skill validation is append-only and already exists: {validation.id}"
                ) from error
            self._record_evidence(
                connection,
                validation.environment_id,
                "skill_validation",
                validation.id,
                "1",
                validation.evidence_refs,
            )
        return validation

    def get_skill_validation(
        self,
        environment_id: str,
        validation_id: str,
    ) -> SkillValidationV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_skill_validations
                WHERE environment_id=? AND id=?
                """,
                (environment_id, validation_id),
            ).fetchone()
        return SkillValidationV1.model_validate_json(row["body_json"]) if row else None

    def list_skill_validations(
        self,
        environment_id: str,
        *,
        skill_version_id: str | None = None,
    ) -> list[SkillValidationV1]:
        query = "SELECT body_json FROM ai_player_skill_validations WHERE environment_id=?"
        parameters: list[Any] = [environment_id]
        if skill_version_id is not None:
            query += " AND skill_version_id=?"
            parameters.append(skill_version_id)
        query += " ORDER BY created_at, id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [SkillValidationV1.model_validate_json(row["body_json"]) for row in rows]

    def _resolve_validation_run(self, run_id: str) -> tuple[str, Any]:
        evidence_run = self.observatory_store.get_evidence_run(run_id)
        trace_run = self.observatory_store.get_run(run_id)
        if evidence_run is not None and trace_run is not None:
            raise ValueError(f"ambiguous validation run id: {run_id}")
        if evidence_run is not None:
            return "evidence_run", evidence_run
        if trace_run is not None:
            return "trace_run", trace_run
        raise ValueError(f"dead validation run reference: {run_id}")

    def get_skill_version(
        self,
        environment_id: str,
        skill_id: str,
        *,
        version: int | None = None,
    ) -> SkillVersionV1 | None:
        query = (
            "SELECT body_json FROM ai_player_skill_versions "
            "WHERE environment_id=? AND skill_id=?"
        )
        parameters: list[Any] = [environment_id, skill_id]
        if version is not None:
            query += " AND version=?"
            parameters.append(version)
        query += " ORDER BY version DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return SkillVersionV1.model_validate_json(row["body_json"]) if row else None

    def get_skill_version_by_id(
        self,
        environment_id: str,
        skill_version_id: str,
    ) -> SkillVersionV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_skill_versions
                WHERE environment_id=? AND id=?
                LIMIT 1
                """,
                (environment_id, skill_version_id),
            ).fetchone()
        return SkillVersionV1.model_validate_json(row["body_json"]) if row else None

    def list_path_reuse_skill_versions(
        self,
        environment_id: str,
    ) -> list[SkillVersionV1]:
        """Load only latest skills that can affect the path-reuse projection.

        Active atomic normalized-action skills contribute operation groups. A
        latest non-operation skill contributes only when an existing SkillRun
        can surface its title in repeated-path telemetry. The full Python
        health projection remains the semantic authority; this query only
        avoids validating unrelated canonical bodies on every compact read.
        """

        safe_body = "CASE WHEN json_valid(skill.body_json) THEN skill.body_json ELSE '{}' END"
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                WITH latest AS (
                    SELECT skill_id, MAX(version) AS version
                    FROM ai_player_skill_versions
                    WHERE environment_id=?
                    GROUP BY skill_id
                )
                SELECT skill.body_json
                FROM ai_player_skill_versions AS skill
                JOIN latest
                  ON latest.skill_id=skill.skill_id AND latest.version=skill.version
                WHERE skill.environment_id=?
                  AND (
                    (
                      json_valid(skill.body_json)
                      AND skill.status IN ('candidate', 'validated', 'preferred')
                      AND json_extract({safe_body}, '$.skill_layer')='atomic'
                      AND json_extract({safe_body}, '$.executor_kind')='normalized_actions'
                    )
                    OR EXISTS (
                      SELECT 1
                      FROM ai_player_skill_runs AS run
                      WHERE run.environment_id=skill.environment_id
                        AND run.skill_version_id=skill.id
                    )
                  )
                ORDER BY skill.created_at, skill.skill_id, skill.version
                """,
                (environment_id, environment_id),
            ).fetchall()
        skills = [SkillVersionV1.model_validate_json(row["body_json"]) for row in rows]
        return sorted(skills, key=lambda skill: (skill.created_at, skill.skill_id, skill.version))

    def list_executable_preferred_skill_versions(
        self,
        environment_id: str,
    ) -> list[SkillVersionV1]:
        """Project the preferred version for each non-invalidated skill lineage.

        A newer candidate intentionally keeps the latest preferred version
        executable; a newer degraded or invalidated version makes the lineage
        sticky-inactive. This is the same lifecycle rule as
        ``SkillLifecycle.select_preferred`` without decoding every historical
        version merely to prove that most lineages have no preferred member.
        """

        with self._connection() as connection:
            rows = connection.execute(
                """
                WITH latest AS (
                    SELECT skill_id, MAX(version) AS version
                    FROM ai_player_skill_versions
                    WHERE environment_id=?
                    GROUP BY skill_id
                ),
                eligible AS (
                    SELECT skill.skill_id
                    FROM ai_player_skill_versions AS skill
                    JOIN latest
                      ON latest.skill_id=skill.skill_id
                     AND latest.version=skill.version
                    WHERE skill.environment_id=?
                      AND skill.status NOT IN ('degraded', 'invalidated')
                ),
                preferred AS (
                    SELECT skill_id, MAX(version) AS version
                    FROM ai_player_skill_versions
                    WHERE environment_id=? AND status='preferred'
                    GROUP BY skill_id
                )
                SELECT skill.body_json
                FROM ai_player_skill_versions AS skill
                JOIN eligible ON eligible.skill_id=skill.skill_id
                JOIN preferred
                  ON preferred.skill_id=skill.skill_id
                 AND preferred.version=skill.version
                WHERE skill.environment_id=?
                ORDER BY skill.created_at, skill.skill_id, skill.version
                """,
                (environment_id, environment_id, environment_id, environment_id),
            ).fetchall()
        return [SkillVersionV1.model_validate_json(row["body_json"]) for row in rows]

    def skill_versions_are_current_latest(
        self,
        environment_id: str,
        skills: Sequence[SkillVersionV1],
    ) -> bool:
        """Verify a preloaded immutable skill set without decoding its bodies again."""

        if not skills:
            return True
        if any(skill.environment_id != environment_id for skill in skills):
            return False
        expected = {
            skill.skill_id: (skill.id, skill.version)
            for skill in skills
        }
        if len(expected) != len(skills):
            return False
        with self._connection() as connection:
            rows = connection.execute(
                """
                WITH latest AS (
                    SELECT skill_id, MAX(version) AS version
                    FROM ai_player_skill_versions
                    WHERE environment_id=?
                    GROUP BY skill_id
                )
                SELECT skill.skill_id, skill.id, skill.version
                FROM ai_player_skill_versions AS skill
                JOIN latest
                  ON latest.skill_id=skill.skill_id AND latest.version=skill.version
                WHERE skill.environment_id=?
                """,
                (environment_id, environment_id),
            ).fetchall()
        current = {
            str(row["skill_id"]): (str(row["id"]), int(row["version"]))
            for row in rows
        }
        return all(current.get(skill_id) == identity for skill_id, identity in expected.items())

    def list_skill_versions(
        self,
        environment_id: str,
        *,
        latest_only: bool = True,
    ) -> list[SkillVersionV1]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_skill_versions
                WHERE environment_id=? ORDER BY skill_id, version DESC, id
                """,
                (environment_id,),
            ).fetchall()
        skills = [SkillVersionV1.model_validate_json(row["body_json"]) for row in rows]
        if latest_only:
            latest: dict[str, SkillVersionV1] = {}
            for skill in skills:
                latest.setdefault(skill.skill_id, skill)
            skills = list(latest.values())
        return sorted(skills, key=lambda skill: (skill.created_at, skill.skill_id, skill.version))

    def get_skill_contract_migration(
        self,
        environment_id: str,
        skill_version_id: str,
    ) -> SkillContractMigrationRecord | None:
        """Return retained raw provenance for one safety-invalidated legacy skill."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT migration_id, environment_id, skill_version_id, skill_id, version,
                       original_body_json, original_body_sha256, original_content_sha256,
                       original_status, migrated_body_sha256, migrated_content_sha256,
                       migrated_status, reason_code, migrated_at
                FROM ai_player_skill_contract_migrations
                WHERE environment_id=? AND skill_version_id=?
                LIMIT 1
                """,
                (environment_id, skill_version_id),
            ).fetchone()
        return SkillContractMigrationRecord(**dict(row)) if row else None

    def append_session_capsule(self, capsule: SessionCapsuleV1) -> SessionCapsuleV1:
        self._prepare_entity(capsule.environment_id, capsule.evidence_refs)
        if capsule.last_confirmed_state_id is not None:
            self._require_semantic_state(
                capsule.environment_id,
                capsule.last_confirmed_state_id,
            )
        for task_id in [*capsule.active_task_ids, *capsule.pending_frontier_task_ids]:
            if self.get_task(capsule.environment_id, task_id) is None:
                raise ValueError(f"capsule task is missing in environment: {task_id}")
        pending_references: list[EvidenceReferenceV1] = []
        if capsule.pending_action is not None:
            pending = capsule.pending_action
            pending_references = [*pending.evidence_refs, *pending.after_evidence_refs]
            self.resolve_evidence_references(pending_references)
            if pending.action_run_id is not None:
                action_run = self.observatory_store.get_run(pending.action_run_id)
                if action_run is None:
                    raise ValueError(
                        f"dead pending action run reference: {pending.action_run_id}"
                    )
                self._assert_reference_environment(
                    capsule.environment_id,
                    "trace_run",
                    pending.action_run_id,
                )
                self._assert_run_scope(
                    capsule.environment_id,
                    "trace_run",
                    pending.action_run_id,
                    action_run,
                )
                step_ids = {
                    step_id
                    for reference in pending_references
                    for step_id in reference.evidence_step_ids
                }
                linked_by_step = any(
                    (step := self.observatory_store.get_evidence_step(step_id)) is not None
                    and step.action_run_id == pending.action_run_id
                    for step_id in step_ids
                )
                if not linked_by_step and not self._reference_claimed_by_environment(
                    "trace_run",
                    pending.action_run_id,
                    capsule.environment_id,
                ):
                    raise ValueError(
                        "pending action run is not linked to pending-action evidence: "
                        f"{pending.action_run_id}"
                    )
        body = capsule.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_session_capsules(
                        environment_id, id, session_id, sequence, body_json, created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        capsule.environment_id,
                        capsule.id,
                        capsule.session_id,
                        capsule.sequence,
                        body,
                        capsule.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"session capsule is append-only and already exists: {capsule.id}"
                ) from error
            self._record_evidence(
                connection,
                capsule.environment_id,
                "session_capsule",
                capsule.id,
                str(capsule.sequence),
                [*capsule.evidence_refs, *pending_references],
            )
        return capsule

    def get_latest_session_capsule(
        self,
        environment_id: str,
        *,
        session_id: str | None = None,
    ) -> SessionCapsuleV1 | None:
        query = (
            "SELECT body_json FROM ai_player_session_capsules "
            "WHERE environment_id=?"
        )
        parameters: list[Any] = [environment_id]
        if session_id is not None:
            query += " AND session_id=?"
            parameters.append(session_id)
        query += " ORDER BY sequence DESC, created_at DESC, id DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return SessionCapsuleV1.model_validate_json(row["body_json"]) if row else None

    def append_guide_refresh_request(
        self,
        request: GuideRefreshRequestV1,
    ) -> GuideRefreshRequestV1:
        """Append one immutable refresh request, idempotent by trigger evidence."""

        self._require_environment(request.environment_id)
        task = self.get_task(request.environment_id, request.task_id)
        if task is None:
            raise ValueError(f"guide refresh task is missing: {request.task_id}")
        if request.retry_of_request_id is not None:
            parent = self.get_guide_refresh_request(request.retry_of_request_id)
            if parent is None:
                raise ValueError(
                    f"guide refresh retry parent is missing: {request.retry_of_request_id}"
                )
            parent_receipt = self.get_guide_refresh_receipt(parent.id)
            if parent_receipt is None or parent_receipt.status == "completed":
                raise ValueError("guide refresh retry requires a retryable terminal parent")
            if (
                parent.environment_id != request.environment_id
                or parent.task_id != request.task_id
                or request.attempt != parent.attempt + 1
            ):
                raise ValueError("guide refresh retry lineage is inconsistent")
        existing = self.get_guide_refresh_request(request.id)
        if existing is not None:
            existing_body = existing.model_dump(mode="json", by_alias=True)
            incoming_body = request.model_dump(mode="json", by_alias=True)
            existing_body.pop("created_at", None)
            incoming_body.pop("created_at", None)
            if existing_body != incoming_body:
                raise ValueError(f"guide refresh request id conflicts: {request.id}")
            return existing

        self._prepare_entity(request.environment_id, request.evidence_refs)
        body = request.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_guide_refresh_requests(
                        environment_id, id, task_id, trigger, status, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        request.environment_id,
                        request.id,
                        request.task_id,
                        request.trigger,
                        request.status,
                        body,
                        request.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"guide refresh request is append-only: {request.id}"
                ) from error
            self._record_evidence(
                connection,
                request.environment_id,
                "guide_refresh_request",
                request.id,
                "1",
                request.evidence_refs,
            )
        return request

    def get_guide_refresh_request(
        self,
        request_id: str,
    ) -> GuideRefreshRequestV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT body_json FROM ai_player_guide_refresh_requests WHERE id=?",
                (request_id,),
            ).fetchone()
        return GuideRefreshRequestV1.model_validate_json(row["body_json"]) if row else None

    def list_guide_refresh_requests(
        self,
        environment_id: str,
        *,
        pending_only: bool = False,
    ) -> list[GuideRefreshRequestV1]:
        self._require_environment(environment_id)
        query = """
            SELECT request.body_json
            FROM ai_player_guide_refresh_requests AS request
        """
        parameters: list[Any] = [environment_id]
        if pending_only:
            query += """
                LEFT JOIN ai_player_guide_refresh_receipts AS receipt
                  ON receipt.request_id=request.id
                WHERE request.environment_id=? AND receipt.id IS NULL
            """
        else:
            query += " WHERE request.environment_id=?"
        query += " ORDER BY request.created_at, request.id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [GuideRefreshRequestV1.model_validate_json(row["body_json"]) for row in rows]

    def get_guide_refresh_receipt(
        self,
        request_id: str,
    ) -> GuideRefreshReceiptV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT body_json FROM ai_player_guide_refresh_receipts WHERE request_id=?",
                (request_id,),
            ).fetchone()
        return GuideRefreshReceiptV1.model_validate_json(row["body_json"]) if row else None

    def append_guide_refresh_receipt(
        self,
        receipt: GuideRefreshReceiptV1,
    ) -> GuideRefreshReceiptV1:
        """Persist one terminal receipt after verifying every completion claim."""

        request = self.get_guide_refresh_request(receipt.request_id)
        if request is None:
            raise ValueError(f"guide refresh request is missing: {receipt.request_id}")
        if request.environment_id != receipt.environment_id:
            raise ValueError("guide refresh receipt belongs to another environment")
        existing = self.get_guide_refresh_receipt(receipt.request_id)
        if existing is not None:
            if existing != receipt:
                raise ValueError(
                    f"guide refresh request already has a terminal receipt: {receipt.request_id}"
                )
            return existing

        if receipt.status == "completed":
            snapshots = {
                item.id: item for item in self.observatory_store.list_source_snapshots()
            }
            missing_snapshots = [
                snapshot_id
                for snapshot_id in receipt.source_snapshot_ids
                if snapshot_id not in snapshots
            ]
            if missing_snapshots:
                raise ValueError(
                    "guide refresh receipt cites missing source snapshots: "
                    + ", ".join(missing_snapshots)
                )
            for guide_ref in receipt.guides:
                guide = self.get_guide_knowledge(
                    receipt.environment_id,
                    guide_ref.id,
                    version=guide_ref.version,
                )
                if guide is None:
                    raise ValueError(
                        "guide refresh receipt cites missing guide: "
                        f"{guide_ref.id}@{guide_ref.version}"
                    )

        body = receipt.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_guide_refresh_receipts(
                        environment_id, id, request_id, status, body_json, created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        receipt.environment_id,
                        receipt.id,
                        receipt.request_id,
                        receipt.status,
                        body,
                        receipt.finished_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"guide refresh request already terminated: {receipt.request_id}"
                ) from error
        return receipt

    def complete_guide_refresh_request(
        self,
        bundle: GuideResearchResultBundleV1,
    ) -> tuple[GuideRefreshReceiptV1, dict[str, int]]:
        """Consume a strict worker bundle through the existing atomic seed path.

        The seed write is idempotent, so a worker can safely retry after a crash
        between the seed transaction and terminal-receipt append.
        """

        request = self.get_guide_refresh_request(bundle.request_id)
        if request is None:
            raise ValueError(f"guide refresh request is missing: {bundle.request_id}")
        existing = self.get_guide_refresh_receipt(bundle.request_id)
        if existing is not None:
            if existing.status != "completed":
                raise ValueError(
                    f"guide refresh request already terminated as {existing.status}"
                )
            expected_guides = [
                GuideVersionReferenceV1(id=item.id, version=item.version)
                for item in bundle.guides
            ]
            if (
                existing.research_record_id != bundle.research_record_id
                or existing.source_snapshot_ids != [item.id for item in bundle.source_snapshots]
                or existing.guides != expected_guides
            ):
                raise ValueError("completed guide refresh bundle conflicts with its receipt")
            return existing, {
                "inserted_source_snapshot_count": 0,
                "inserted_guide_count": 0,
                "inserted_memory_count": 0,
            }

        if any(item.environment_id != request.environment_id for item in bundle.guides):
            raise ValueError("guide research result belongs to another environment")
        applicability_pairs = (
            ("applicable_game_version", request.environment.game_version),
            ("season", request.environment.season),
            ("server_stage", request.environment.server_stage),
        )
        for guide in bundle.guides:
            for field_name, current_value in applicability_pairs:
                source_value = getattr(guide, field_name)
                if current_value is not None and source_value not in {None, current_value}:
                    raise ValueError(
                        f"guide research result {field_name} conflicts with trigger environment"
                    )

        counts = self.apply_knowledge_memory_seed(
            request.environment_id,
            bundle.source_snapshots,
            bundle.guides,
            [],
        )
        receipt = GuideRefreshReceiptV1(
            id=f"guide-refresh-receipt.{request.id}",
            environment_id=request.environment_id,
            request_id=request.id,
            status="completed",
            research_record_id=bundle.research_record_id,
            source_snapshot_ids=[item.id for item in bundle.source_snapshots],
            guides=[
                GuideVersionReferenceV1(id=item.id, version=item.version)
                for item in bundle.guides
            ],
            detail="独立 research.run 结果已通过严格来源与适用性合同并入库。",
            finished_at=bundle.completed_at,
        )
        return self.append_guide_refresh_receipt(receipt), counts

    def terminate_guide_refresh_request(
        self,
        request_id: str,
        *,
        status: str,
        detail: str,
        finished_at: str | None = None,
    ) -> GuideRefreshReceiptV1:
        """Record offline, source-unavailable, or failed as an explicit terminal state."""

        if status not in {"offline", "source_unavailable", "failed"}:
            raise ValueError("guide refresh terminal status is invalid")
        request = self.get_guide_refresh_request(request_id)
        if request is None:
            raise ValueError(f"guide refresh request is missing: {request_id}")
        receipt = GuideRefreshReceiptV1(
            id=f"guide-refresh-receipt.{request.id}",
            environment_id=request.environment_id,
            request_id=request.id,
            status=status,
            detail=detail,
            finished_at=finished_at or utc_now(),
        )
        return self.append_guide_refresh_receipt(receipt)

    def append_guide_knowledge(self, guide: GuideKnowledgeV1) -> GuideKnowledgeV1:
        environment = self.get_environment(guide.environment_id)
        if environment is None:
            raise KeyError(f"unknown AI-player environment: {guide.environment_id}")
        latest = self.get_guide_knowledge(guide.environment_id, guide.id)
        if latest is None and guide.version != 1:
            raise ValueError("guide knowledge must start at version 1")
        if latest is not None and guide.version != latest.version + 1:
            raise ValueError("guide knowledge successor must increment the latest version")
        for task_id in guide.triggering_task_ids:
            if self.get_task(guide.environment_id, task_id) is None:
                raise ValueError(f"guide triggering task is missing: {task_id}")
        if guide.status == "current":
            canonical_values = {
                "applicable_build_scope_id": environment.build_scope_id,
                "applicable_account_scope_id": environment.account_scope_id,
                "applicable_channel": environment.channel,
            }
            mismatches = [
                field_name
                for field_name, expected in canonical_values.items()
                if getattr(guide, field_name) != expected
            ]
            if mismatches:
                raise ValueError(
                    "current guide applicability does not match canonical environment: "
                    + ", ".join(mismatches)
                )
        self._prepare_entity(guide.environment_id, guide.evidence_refs)
        body = guide.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_guide_knowledge(
                        environment_id, id, version, status, url, season,
                        server_stage, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        guide.environment_id,
                        guide.id,
                        guide.version,
                        guide.status,
                        str(guide.url),
                        guide.season,
                        guide.server_stage,
                        body,
                        guide.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"guide knowledge version is append-only and already exists: "
                    f"{guide.id}@{guide.version}"
                ) from error
            self._record_evidence(
                connection,
                guide.environment_id,
                "guide_knowledge",
                guide.id,
                str(guide.version),
                guide.evidence_refs,
            )
        return guide

    def get_guide_knowledge(
        self,
        environment_id: str,
        guide_id: str,
        *,
        version: int | None = None,
    ) -> GuideKnowledgeV1 | None:
        query = (
            "SELECT body_json FROM ai_player_guide_knowledge "
            "WHERE environment_id=? AND id=?"
        )
        parameters: list[Any] = [environment_id, guide_id]
        if version is not None:
            query += " AND version=?"
            parameters.append(version)
        query += " ORDER BY version DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return GuideKnowledgeV1.model_validate_json(row["body_json"]) if row else None

    def list_guide_knowledge(self, environment_id: str) -> list[GuideKnowledgeV1]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_guide_knowledge
                WHERE environment_id=?
                ORDER BY created_at, id, version
                """,
                (environment_id,),
            ).fetchall()
        return [GuideKnowledgeV1.model_validate_json(row["body_json"]) for row in rows]

    def put_baseline_result(
        self,
        environment_id: str,
        baseline_id: str,
        *,
        fixture_hash: str,
        code_hash: str,
        config_hash: str,
        result: Mapping[str, Any] | BaseModel,
    ) -> dict[str, Any]:
        self._require_environment(environment_id)
        payload = self._json_mapping(result)
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        created_at_value = payload.get("generated_at") or payload.get("created_at")
        created_at = created_at_value if isinstance(created_at_value, str) else utc_now()
        with self._write_lock, self._connection() as connection:
            existing = connection.execute(
                """
                SELECT body_json FROM ai_player_baseline_runs
                WHERE environment_id=? AND baseline_id=? AND fixture_hash=?
                  AND code_hash=? AND config_hash=?
                """,
                (environment_id, baseline_id, fixture_hash, code_hash, config_hash),
            ).fetchone()
            if existing:
                if existing["body_json"] == body:
                    return payload
                raise ValueError(
                    "baseline result is immutable for the same fixture/code/config hashes"
                )
            connection.execute(
                """
                INSERT INTO ai_player_baseline_runs(
                    environment_id, baseline_id, fixture_hash, code_hash, config_hash,
                    body_json, created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    environment_id,
                    baseline_id,
                    fixture_hash,
                    code_hash,
                    config_hash,
                    body,
                    created_at,
                ),
            )
        return payload

    def get_baseline_result(
        self,
        environment_id: str,
        baseline_id: str,
        *,
        fixture_hash: str | None = None,
        code_hash: str | None = None,
        config_hash: str | None = None,
    ) -> dict[str, Any] | None:
        hashes = (fixture_hash, code_hash, config_hash)
        if any(value is not None for value in hashes) and not all(
            value is not None for value in hashes
        ):
            raise ValueError("baseline lookup requires all three hashes or none")
        query = (
            "SELECT body_json FROM ai_player_baseline_runs "
            "WHERE environment_id=? AND baseline_id=?"
        )
        parameters: list[Any] = [environment_id, baseline_id]
        if fixture_hash is not None:
            query += " AND fixture_hash=? AND code_hash=? AND config_hash=?"
            parameters.extend([fixture_hash, code_hash, config_hash])
        query += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return json.loads(row["body_json"]) if row else None

    def _prepare_entity(
        self,
        environment_id: str,
        evidence_refs: Sequence[EvidenceReferenceV1],
    ) -> None:
        self._require_environment(environment_id)
        self.resolve_evidence_references(evidence_refs)

    def _require_environment(self, environment_id: str) -> None:
        if self.get_environment(environment_id) is None:
            raise KeyError(f"unknown AI-player environment: {environment_id}")

    def _require_semantic_state(self, environment_id: str, state_id: str) -> None:
        if self.get_semantic_state(environment_id, state_id) is None:
            raise ValueError(f"semantic state is missing in environment: {state_id}")

    @staticmethod
    def _json_mapping(result: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
        if isinstance(result, BaseModel):
            return json.loads(result.model_dump_json(by_alias=True))
        payload = dict(result)
        return json.loads(json.dumps(payload, ensure_ascii=False))

    def append_gameplay_candidate(
        self,
        candidate: GameplayCandidateV1,
    ) -> GameplayCandidateV1:
        environment = self.get_environment(candidate.environment_id)
        if environment is None:
            raise KeyError(f"unknown AI-player environment: {candidate.environment_id}")
        if candidate.game_id not in {environment.game_id, *environment.game_id_aliases}:
            raise ValueError("gameplay candidate game_id is outside the environment")
        for task_id in candidate.triggering_task_ids:
            if self.get_task(candidate.environment_id, task_id) is None:
                raise ValueError(f"gameplay candidate task is missing: {task_id}")
        for state_id in {
            *candidate.entry_state_ids,
            *candidate.main_state_ids,
            *candidate.exit_state_ids,
        }:
            if self.get_semantic_state(candidate.environment_id, state_id) is None:
                raise ValueError(f"gameplay candidate state is missing: {state_id}")
        candidate_state_ids = {
            *candidate.entry_state_ids,
            *candidate.main_state_ids,
            *candidate.exit_state_ids,
        }
        for edge_id in candidate.transition_edge_ids:
            edge = self.get_transition_edge(candidate.environment_id, edge_id)
            if edge is None:
                raise ValueError(f"gameplay candidate transition is missing: {edge_id}")
            if edge.from_state_id not in candidate_state_ids or edge.to_state_id not in candidate_state_ids:
                raise ValueError(
                    "gameplay candidate transition endpoints must belong to its state boundary"
                )
        for adjacent_id in candidate.adjacent_gameplay_candidate_ids:
            if adjacent_id == candidate.id:
                raise ValueError("a gameplay candidate cannot be adjacent to itself")
            if self.get_gameplay_candidate(candidate.environment_id, adjacent_id) is None:
                raise ValueError(
                    f"adjacent gameplay candidate is missing: {adjacent_id}"
                )
        latest = self.get_gameplay_candidate(candidate.environment_id, candidate.id)
        if latest is None and candidate.version != 1:
            raise ValueError("a gameplay candidate must start at version 1")
        if latest is not None and candidate.version != latest.version + 1:
            raise ValueError("a gameplay candidate successor must increment the latest version")
        self._prepare_entity(candidate.environment_id, candidate.evidence_refs)
        body = candidate.model_dump_json(by_alias=True)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_gameplay_candidates(
                        environment_id, id, version, game_id, status, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        candidate.environment_id,
                        candidate.id,
                        candidate.version,
                        candidate.game_id,
                        candidate.status,
                        body,
                        candidate.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"gameplay candidate version already exists: {candidate.id}@{candidate.version}"
                ) from error
            self._record_evidence(
                connection,
                candidate.environment_id,
                "gameplay_candidate",
                candidate.id,
                str(candidate.version),
                candidate.evidence_refs,
            )
        return candidate

    def get_gameplay_candidate(
        self,
        environment_id: str,
        candidate_id: str,
        *,
        version: int | None = None,
    ) -> GameplayCandidateV1 | None:
        query = (
            "SELECT body_json FROM ai_player_gameplay_candidates "
            "WHERE environment_id=? AND id=?"
        )
        parameters: list[Any] = [environment_id, candidate_id]
        if version is not None:
            query += " AND version=?"
            parameters.append(version)
        query += " ORDER BY version DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return GameplayCandidateV1.model_validate_json(row["body_json"]) if row else None

    def list_gameplay_candidates(
        self,
        environment_id: str,
        *,
        latest_only: bool = True,
    ) -> list[GameplayCandidateV1]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_gameplay_candidates
                WHERE environment_id=? ORDER BY id, version DESC
                """,
                (environment_id,),
            ).fetchall()
        candidates = [GameplayCandidateV1.model_validate_json(row["body_json"]) for row in rows]
        if not latest_only:
            return candidates
        latest: dict[str, GameplayCandidateV1] = {}
        for candidate in candidates:
            latest.setdefault(candidate.id, candidate)
        return list(latest.values())

    def append_account_policy(self, policy: AccountActionPolicyV1) -> AccountActionPolicyV1:
        if self.get_environment(policy.environment_id) is None:
            raise KeyError(f"unknown AI-player environment: {policy.environment_id}")
        latest = self.get_account_policy(policy.environment_id)
        if latest is None:
            if policy.version != 1:
                raise ValueError("an account policy must start at version 1")
        elif policy.id != latest.id or policy.version != latest.version + 1:
            raise ValueError("an account policy successor must increment the canonical policy")
        self._prepare_entity(policy.environment_id, policy.evidence_refs)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_account_policies(
                        environment_id, id, version, body_json, created_at
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        policy.environment_id,
                        policy.id,
                        policy.version,
                        policy.model_dump_json(by_alias=True),
                        policy.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"account policy version already exists: {policy.id}@{policy.version}"
                ) from error
            self._record_evidence(
                connection,
                policy.environment_id,
                "account_policy",
                policy.id,
                str(policy.version),
                policy.evidence_refs,
            )
        return policy

    def get_account_policy(
        self,
        environment_id: str,
        *,
        policy_id: str | None = None,
        version: int | None = None,
    ) -> AccountActionPolicyV1 | None:
        query = "SELECT body_json FROM ai_player_account_policies WHERE environment_id=?"
        parameters: list[Any] = [environment_id]
        if policy_id is not None:
            query += " AND id=?"
            parameters.append(policy_id)
        if version is not None:
            query += " AND version=?"
            parameters.append(version)
        query += " ORDER BY version DESC, created_at DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return AccountActionPolicyV1.model_validate_json(row["body_json"]) if row else None

    def append_speech_intent(self, intent: SpeechIntentV1) -> SpeechIntentV1:
        policy = self.get_account_policy(intent.environment_id, policy_id=intent.policy_id)
        if policy is None:
            raise ValueError("speech intent account policy is missing")
        if policy.ai_identity_label != intent.ai_identity_label:
            raise ValueError("speech intent identity differs from the canonical account policy")
        if self.get_task(intent.environment_id, intent.triggering_task_id) is None:
            raise ValueError("speech intent triggering task is missing")
        latest = self.get_speech_intent(intent.environment_id, intent.id)
        if latest is None and intent.version != 1:
            raise ValueError("a speech intent must start at version 1")
        if latest is not None and intent.version != latest.version + 1:
            raise ValueError("a speech intent successor must increment the latest version")
        self._prepare_entity(intent.environment_id, intent.evidence_refs)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_speech_intents(
                        environment_id, id, version, policy_id, triggering_task_id,
                        status, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        intent.environment_id,
                        intent.id,
                        intent.version,
                        intent.policy_id,
                        intent.triggering_task_id,
                        intent.status,
                        intent.model_dump_json(by_alias=True),
                        intent.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"speech intent version already exists: {intent.id}@{intent.version}"
                ) from error
            self._record_evidence(
                connection,
                intent.environment_id,
                "speech_intent",
                intent.id,
                str(intent.version),
                intent.evidence_refs,
            )
        return intent

    def get_speech_intent(
        self,
        environment_id: str,
        intent_id: str,
        *,
        version: int | None = None,
    ) -> SpeechIntentV1 | None:
        query = (
            "SELECT body_json FROM ai_player_speech_intents WHERE environment_id=? AND id=?"
        )
        parameters: list[Any] = [environment_id, intent_id]
        if version is not None:
            query += " AND version=?"
            parameters.append(version)
        query += " ORDER BY version DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return SpeechIntentV1.model_validate_json(row["body_json"]) if row else None

    def list_speech_intents(
        self,
        environment_id: str,
        *,
        latest_only: bool = True,
    ) -> list[SpeechIntentV1]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_speech_intents
                WHERE environment_id=? ORDER BY id, version DESC
                """,
                (environment_id,),
            ).fetchall()
        intents = [SpeechIntentV1.model_validate_json(row["body_json"]) for row in rows]
        if not latest_only:
            return intents
        latest: dict[str, SpeechIntentV1] = {}
        for intent in intents:
            latest.setdefault(intent.id, intent)
        return list(latest.values())

    def append_speech_event(self, event: SpeechEventV1) -> SpeechEventV1:
        intent = self.get_speech_intent(
            event.environment_id,
            event.speech_intent_id,
            version=event.speech_intent_version,
        )
        if intent is None:
            raise ValueError("speech event intent version is missing")
        if event.status == "sent" and intent.status != "authorized":
            raise ValueError("a sent speech event requires an authorized speech intent")
        if event.evidence_step_id is not None and not any(
            event.evidence_step_id in reference.evidence_step_ids
            for reference in event.evidence_refs
        ):
            raise ValueError("speech event evidence does not contain its evidence step")
        if event.action_run_id is not None and not any(
            event.action_run_id in reference.trace_run_ids for reference in event.evidence_refs
        ):
            raise ValueError("speech event evidence does not contain its action run")
        self._prepare_entity(event.environment_id, event.evidence_refs)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_speech_events(
                        environment_id, id, speech_intent_id, speech_intent_version,
                        status, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        event.environment_id,
                        event.id,
                        event.speech_intent_id,
                        event.speech_intent_version,
                        event.status,
                        event.model_dump_json(by_alias=True),
                        event.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"speech event already exists: {event.id}") from error
            self._record_evidence(
                connection,
                event.environment_id,
                "speech_event",
                event.id,
                "1",
                event.evidence_refs,
            )
        return event

    def list_speech_events(
        self,
        environment_id: str,
        *,
        speech_intent_id: str | None = None,
    ) -> list[SpeechEventV1]:
        query = "SELECT body_json FROM ai_player_speech_events WHERE environment_id=?"
        parameters: list[Any] = [environment_id]
        if speech_intent_id is not None:
            query += " AND speech_intent_id=?"
            parameters.append(speech_intent_id)
        query += " ORDER BY created_at, id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [SpeechEventV1.model_validate_json(row["body_json"]) for row in rows]

    def append_account_metric_derivation(
        self,
        derivation: AccountMetricDeltaDerivationV1,
    ) -> AccountMetricDeltaDerivationV1:
        """Persist one recomputed before/after metric proof and freeze its definition."""

        self._require_environment(derivation.environment_id)
        validate_account_metric_derivation(self.observatory_store, derivation)
        self._prepare_entity(derivation.environment_id, derivation.evidence_refs)
        definition_json = derivation.definition.model_dump_json(by_alias=True)
        fingerprint = metric_delta_fingerprint(derivation.delta)
        with self._write_lock, self._connection() as connection:
            existing_definition = connection.execute(
                """
                SELECT body_json FROM ai_player_account_metric_definitions
                WHERE environment_id=? AND id=?
                """,
                (derivation.environment_id, derivation.definition.id),
            ).fetchone()
            if existing_definition is not None:
                canonical_definition = AccountMetricDefinitionV1.model_validate_json(
                    existing_definition["body_json"]
                )
                if canonical_definition != derivation.definition:
                    raise ValueError(
                        "account metric definition id already has different semantics"
                    )
            else:
                try:
                    connection.execute(
                        """
                        INSERT INTO ai_player_account_metric_definitions(
                            environment_id, id, metric_key, body_json, created_at
                        ) VALUES(?,?,?,?,?)
                        """,
                        (
                            derivation.environment_id,
                            derivation.definition.id,
                            derivation.definition.metric_key,
                            definition_json,
                            derivation.created_at,
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise ValueError(
                        "account metric key already has another frozen definition: "
                        f"{derivation.definition.metric_key}"
                    ) from error
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_account_metric_derivations(
                        environment_id, id, definition_id, metric_key,
                        before_observation_id, after_observation_id,
                        before_evidence_step_id, after_evidence_step_id,
                        delta_fingerprint, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        derivation.environment_id,
                        derivation.id,
                        derivation.definition.id,
                        derivation.definition.metric_key,
                        derivation.before_observation.id,
                        derivation.after_observation.id,
                        derivation.before_observation.evidence_step_id,
                        derivation.after_observation.evidence_step_id,
                        fingerprint,
                        derivation.model_dump_json(by_alias=True),
                        derivation.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                detail = str(error)
                if "delta_fingerprint" in detail:
                    message = "account metric delta is already registered"
                else:
                    message = f"account metric derivation already exists: {derivation.id}"
                raise ValueError(message) from error
            self._record_evidence(
                connection,
                derivation.environment_id,
                "account_metric_delta_derivation",
                derivation.id,
                "1",
                derivation.evidence_refs,
            )
        return derivation

    def get_account_metric_derivation(
        self,
        environment_id: str,
        derivation_id: str,
    ) -> AccountMetricDeltaDerivationV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_account_metric_derivations
                WHERE environment_id=? AND id=?
                """,
                (environment_id, derivation_id),
            ).fetchone()
        return (
            AccountMetricDeltaDerivationV1.model_validate_json(row["body_json"])
            if row
            else None
        )

    def list_account_metric_derivations(
        self,
        environment_id: str,
        *,
        metric_key: str | None = None,
        limit: int = 100,
    ) -> list[AccountMetricDeltaDerivationV1]:
        if limit < 1:
            raise ValueError("account metric derivation limit must be positive")
        query = (
            "SELECT body_json FROM ai_player_account_metric_derivations "
            "WHERE environment_id=?"
        )
        parameters: list[Any] = [environment_id]
        if metric_key is not None:
            query += " AND metric_key=?"
            parameters.append(metric_key)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            AccountMetricDeltaDerivationV1.model_validate_json(row["body_json"])
            for row in rows
        ]

    def _validate_registered_account_metric_deltas(
        self,
        sample: ActionQualitySampleV1,
    ) -> None:
        if not sample.account_metric_deltas:
            return
        if sample.evidence_step_id is None:
            raise ValueError("account metric deltas require an executed action evidence step")
        sample_step_ids = {
            step_id
            for reference in sample.evidence_refs
            for step_id in reference.evidence_step_ids
        }
        sample_artifact_ids = {
            artifact_id
            for reference in sample.evidence_refs
            for artifact_id in reference.artifact_ids
        }
        sample_evidence_run_ids = {
            run_id
            for reference in sample.evidence_refs
            for run_id in reference.evidence_run_ids
        }
        with self._connection() as connection:
            for delta in sample.account_metric_deltas:
                fingerprint = metric_delta_fingerprint(delta)
                row = connection.execute(
                    """
                    SELECT body_json FROM ai_player_account_metric_derivations
                    WHERE environment_id=? AND delta_fingerprint=?
                    """,
                    (sample.environment_id, fingerprint),
                ).fetchone()
                if row is None:
                    raise ValueError(
                        "action-quality account metric delta lacks a canonical derivation: "
                        f"{delta.metric_key}"
                    )
                derivation = AccountMetricDeltaDerivationV1.model_validate_json(
                    row["body_json"]
                )
                validate_account_metric_derivation(self.observatory_store, derivation)
                if derivation.delta != delta:
                    raise ValueError("stored account metric derivation delta differs from sample")
                if derivation.after_observation.evidence_step_id != sample.evidence_step_id:
                    raise ValueError(
                        "account metric after observation is not the executed action terminal"
                    )
                derivation_step_ids = {
                    step_id
                    for reference in derivation.evidence_refs
                    for step_id in reference.evidence_step_ids
                }
                derivation_artifact_ids = {
                    artifact_id
                    for reference in derivation.evidence_refs
                    for artifact_id in reference.artifact_ids
                }
                derivation_evidence_run_ids = {
                    run_id
                    for reference in derivation.evidence_refs
                    for run_id in reference.evidence_run_ids
                }
                if not (
                    derivation_step_ids.issubset(sample_step_ids)
                    and derivation_artifact_ids.issubset(sample_artifact_ids)
                    and derivation_evidence_run_ids.issubset(sample_evidence_run_ids)
                ):
                    raise ValueError(
                        "action-quality sample does not retain its account metric evidence"
                    )

    def append_action_quality_sample(
        self,
        sample: ActionQualitySampleV1,
    ) -> ActionQualitySampleV1:
        self._require_environment(sample.environment_id)
        if sample.task_id is not None and self.get_task(sample.environment_id, sample.task_id) is None:
            raise ValueError(f"action-quality task is missing: {sample.task_id}")
        if sample.semantic_state_id is not None:
            self._require_semantic_state(sample.environment_id, sample.semantic_state_id)
        self._prepare_entity(sample.environment_id, sample.evidence_refs)
        self._validate_action_quality_canonical_binding(sample)
        self._validate_registered_account_metric_deltas(sample)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_action_quality_samples(
                        environment_id, id, session_id, task_id, command_id,
                        action_run_id, evidence_step_id, outcome,
                        execution_disposition, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        sample.environment_id,
                        sample.id,
                        sample.session_id,
                        sample.task_id,
                        sample.command_id,
                        sample.action_run_id,
                        sample.evidence_step_id,
                        sample.outcome,
                        sample.execution_disposition,
                        sample.model_dump_json(by_alias=True),
                        sample.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                detail = str(error)
                if "action_run_id" in detail:
                    message = f"action-quality action run is already bound: {sample.action_run_id}"
                elif "evidence_step_id" in detail:
                    message = (
                        "action-quality evidence step is already bound: "
                        f"{sample.evidence_step_id}"
                    )
                elif "command_id" in detail:
                    message = f"action-quality command is already bound: {sample.command_id}"
                else:
                    message = f"action-quality sample already exists: {sample.id}"
                raise ValueError(message) from error
            self._record_evidence(
                connection,
                sample.environment_id,
                "action_quality_sample",
                sample.id,
                "1",
                sample.evidence_refs,
            )
        return sample

    def _validate_action_quality_canonical_binding(
        self,
        sample: ActionQualitySampleV1,
    ) -> None:
        """Bind one quality sample to one durable session command and terminal bundle."""

        with self._connection() as connection:
            session_row = connection.execute(
                "SELECT environment_id FROM ai_player_sessions WHERE id=?",
                (sample.session_id,),
            ).fetchone()
        if session_row is None:
            raise ValueError(f"action-quality session is missing: {sample.session_id}")
        if session_row["environment_id"] != sample.environment_id:
            raise ValueError("action-quality session belongs to another environment")

        if sample.execution_disposition == "rejected":
            raise ValueError(
                "rejected action-quality samples require canonical rejection evidence; "
                "caller-authored rejection payloads are not persistable"
            )
        if sample.task_id is None or sample.action_run_id is None or sample.evidence_step_id is None:
            raise ValueError("executed action quality requires task, action-run, and evidence-step ids")

        referenced_run_ids = {
            run_id for reference in sample.evidence_refs for run_id in reference.trace_run_ids
        }
        referenced_step_ids = {
            step_id for reference in sample.evidence_refs for step_id in reference.evidence_step_ids
        }
        referenced_evidence_run_ids = {
            run_id for reference in sample.evidence_refs for run_id in reference.evidence_run_ids
        }
        if sample.action_run_id not in referenced_run_ids:
            raise ValueError("action-quality action run must be included by the sample evidence")
        if sample.evidence_step_id not in referenced_step_ids:
            raise ValueError("action-quality evidence step must be included by the sample evidence")

        action_run = self.observatory_store.get_run(sample.action_run_id)
        step = self.observatory_store.get_evidence_step(sample.evidence_step_id)
        if action_run is None:
            raise ValueError(f"action-quality action run is missing: {sample.action_run_id}")
        if step is None:
            raise ValueError(f"action-quality evidence step is missing: {sample.evidence_step_id}")
        evidence_run = self.observatory_store.get_evidence_run(step.evidence_run_id)
        if evidence_run is None:
            raise ValueError(f"action-quality evidence run is missing: {step.evidence_run_id}")
        if step.evidence_run_id not in referenced_evidence_run_ids:
            raise ValueError("action-quality evidence run must be included by the sample evidence")
        if action_run.task_id != sample.task_id:
            raise ValueError("action-quality task does not match the canonical action run")
        if step.action_run_id != action_run.id:
            raise ValueError("action-quality evidence step does not bind the canonical action run")
        if step.id not in evidence_run.step_ids or action_run.id not in evidence_run.action_run_ids:
            raise ValueError("action-quality terminal bundle is incomplete in its evidence run")

        external_invocation_id = evidence_run.environment.get(
            "external_agent_invocation_id"
        )
        if isinstance(external_invocation_id, str) and external_invocation_id:
            from .external_action_quality import (
                validate_external_action_quality_sample,
            )

            validate_external_action_quality_sample(
                self,
                sample,
                evidence_run=evidence_run,
            )
            return

        metadata = step.metadata.get("autonomous_execution")
        expected_metadata = {
            "schema": "game-observatory.ai-player.autonomous-evidence.v1",
            "environment_id": sample.environment_id,
            "command_id": sample.command_id,
            "task_id": sample.task_id,
        }
        if not isinstance(metadata, dict) or any(
            metadata.get(key) != value for key, value in expected_metadata.items()
        ):
            raise ValueError(
                "action-quality sample does not match canonical autonomous-execution metadata"
            )
        telemetry = step.metadata.get("action_decision_telemetry")
        expected_telemetry = {
            "schema": "game-observatory.ai-player.action-decision-telemetry.v1",
            "model_input_tokens": sample.model_input_tokens,
            "model_output_tokens": sample.model_output_tokens,
            "baseline_model_input_tokens": sample.baseline_model_input_tokens,
            "decision_latency_ms": sample.decision_latency_ms,
            "baseline_decision_latency_ms": sample.baseline_decision_latency_ms,
        }
        if telemetry != expected_telemetry:
            raise ValueError(
                "action-quality token and latency telemetry do not match canonical evidence"
            )
        from .planner_measurement import resolve_planner_telemetry

        environment = self.get_environment(sample.environment_id)
        if environment is None:
            raise ValueError("action-quality environment is missing")
        command_payload = step.metadata.get("action_quality_command")
        if not isinstance(command_payload, dict):
            raise ValueError("action-quality canonical command payload is missing")
        from .orchestrator import AutonomousExecutionCommandV1

        canonical_command = AutonomousExecutionCommandV1.model_validate(command_payload)
        resolved_telemetry, receipt = resolve_planner_telemetry(
            self,
            environment=environment,
            command=canonical_command,
            task_id=sample.task_id,
        )
        if telemetry != resolved_telemetry.model_dump(mode="json", by_alias=True):
            raise ValueError("action-quality telemetry is not derived from planner evidence")
        if receipt is not None:
            receipt_artifact = self.observatory_store.get_artifact(receipt.artifact_id)
            if receipt_artifact is None:
                raise ValueError("planner measurement artifact is missing")
            expected_receipt_metadata = {
                "receipt": receipt.model_dump(mode="json", by_alias=True),
                "artifact_id": receipt_artifact.id,
                "artifact_sha256": receipt_artifact.sha256,
            }
            if step.metadata.get("planner_measurement_receipt") != expected_receipt_metadata:
                raise ValueError("terminal evidence does not bind its planner receipt")
        if sample.token_measurement_status != "measured":
            raise ValueError("executed action-quality samples require canonical token telemetry")
        canonical_status = metadata.get("status")
        if canonical_status not in {"succeeded", "failed"}:
            raise ValueError("canonical autonomous-execution metadata is not terminal")
        if canonical_status == "succeeded":
            if (
                action_run.status != "passed"
                or evidence_run.status != "passed"
                or step.status != "passed"
                or sample.outcome != "confirmed"
                or not sample.evidence_complete
            ):
                raise ValueError("successful action-quality facts contradict canonical evidence")
        elif (
            action_run.status == "running"
            or evidence_run.status == "running"
            or step.status == "running"
            or sample.outcome == "confirmed"
        ):
            raise ValueError("failed action-quality facts contradict canonical evidence")

        with self._connection() as connection:
            capsule_rows = connection.execute(
                """
                SELECT body_json FROM ai_player_session_capsules
                WHERE environment_id=? AND session_id=?
                ORDER BY sequence DESC
                """,
                (sample.environment_id, sample.session_id),
            ).fetchall()
        expected_effect = "confirmed" if canonical_status == "succeeded" else "failed"
        command_is_bound = False
        for row in capsule_rows:
            capsule = SessionCapsuleV1.model_validate_json(row["body_json"])
            pending = capsule.pending_action
            if (
                pending is None
                or pending.id != sample.command_id
                or (
                    pending.action != step.action
                    and not (
                        metadata.get("recovered_from_interruption") is True
                        and step.action.type == "wait"
                    )
                )
                or pending.action_run_id != action_run.id
                or pending.effect_status != expected_effect
                or sample.task_id not in capsule.active_task_ids
                or sample.task_id not in capsule.pending_frontier_task_ids
            ):
                continue
            pending_step_ids = {
                step_id
                for reference in pending.after_evidence_refs
                for step_id in reference.evidence_step_ids
            }
            pending_run_ids = {
                run_id
                for reference in pending.after_evidence_refs
                for run_id in reference.trace_run_ids
            }
            if step.id in pending_step_ids and action_run.id in pending_run_ids:
                command_is_bound = True
                break
        if not command_is_bound:
            raise ValueError(
                "action-quality command is not bound to a resolved capsule in the claimed session"
            )
        self._recompute_executed_action_quality_sample(
            sample,
            step=step,
            evidence_run=evidence_run,
            action_run=action_run,
        )

    def _recompute_executed_action_quality_sample(
        self,
        sample: ActionQualitySampleV1,
        *,
        step: EvidenceStep,
        evidence_run: EvidenceRun,
        action_run: Any,
    ) -> None:
        """Rebuild every assessment field from immutable canonical execution facts."""

        from .account_metric_observation import attach_account_metric_derivations
        from .action_history import ActionHistoryGuard, _same_action_target
        from .action_quality_producer import (
            ActionQualityHistoryContextV1,
            ActionQualityHistorySnapshotV1,
            produce_action_quality_sample,
        )
        from .consolidation import (
            CanonicalExecutionOutcomeV1,
            ConsolidationResultV1,
            ExecutionConsolidator,
        )
        from .orchestrator import (
            AutonomousExecutionCommandV1,
            autonomous_request_sha256,
        )
        from .expected_change_measurement import attach_expected_change_measurement
        from .planner_measurement import resolve_planner_telemetry
        from .session_control import AIPlayerSessionControl
        from .task_board import TaskBoard

        command_payload = step.metadata.get("action_quality_command")
        if not isinstance(command_payload, dict):
            raise ValueError(
                "executed action-quality evidence lacks its canonical command payload"
            )
        command = AutonomousExecutionCommandV1.model_validate(command_payload)
        if (
            command.environment_id != sample.environment_id
            or command.session_id != sample.session_id
            or command.command_id != sample.command_id
        ):
            raise ValueError("canonical action-quality command contradicts sample identity")

        history_payload = step.metadata.get("action_quality_history")
        if not isinstance(history_payload, dict):
            raise ValueError("executed action-quality evidence lacks pre-action history")
        snapshot = ActionQualityHistorySnapshotV1.model_validate(history_payload)
        request_sha256 = autonomous_request_sha256(command)
        if (
            snapshot.environment_id,
            snapshot.command_id,
            snapshot.request_sha256,
            snapshot.session_id,
            snapshot.task_id,
        ) != (
            sample.environment_id,
            sample.command_id,
            request_sha256,
            sample.session_id,
            sample.task_id,
        ):
            raise ValueError("canonical pre-action history contradicts sample identity")

        memory = self.get_memory(
            sample.environment_id,
            ExecutionConsolidator.command_memory_id(sample.command_id),
        )
        if memory is None or memory.payload.get("request_sha256") != request_sha256:
            raise ValueError("action-quality sample lacks its canonical consolidation memory")
        consolidation = ConsolidationResultV1.model_validate(memory.payload.get("result"))
        if consolidation.idempotent_replay:
            consolidation = consolidation.model_copy(update={"idempotent_replay": False})
        sample_artifact_ids = {
            item
            for reference in sample.evidence_refs
            for item in reference.artifact_ids
        }
        sample_run_ids = {
            item
            for reference in sample.evidence_refs
            for item in reference.evidence_run_ids
        }
        sample_step_ids = {
            item
            for reference in sample.evidence_refs
            for item in reference.evidence_step_ids
        }
        sample_trace_ids = {
            item
            for reference in sample.evidence_refs
            for item in reference.trace_run_ids
        }
        consolidation_reference_retained = all(
            (
                set(consolidation.evidence_ref.artifact_ids).issubset(sample_artifact_ids),
                set(consolidation.evidence_ref.evidence_run_ids).issubset(sample_run_ids),
                set(consolidation.evidence_ref.evidence_step_ids).issubset(sample_step_ids),
                set(consolidation.evidence_ref.trace_run_ids).issubset(sample_trace_ids),
            )
        )
        if (
            consolidation.environment_id != sample.environment_id
            or consolidation.command_id != sample.command_id
            or consolidation.task_id != sample.task_id
            or not consolidation_reference_retained
        ):
            raise ValueError("canonical consolidation contradicts action-quality sample")

        task = self.get_task(sample.environment_id, consolidation.task_id)
        session = AIPlayerSessionControl(self).get_session(
            sample.environment_id,
            sample.session_id,
        )
        transition = self.get_transition_edge(
            sample.environment_id,
            consolidation.transition_edge_id,
        )
        if task is None or session is None or transition is None:
            raise ValueError("action-quality canonical task/session/transition is missing")

        known_states = []
        for state_id in snapshot.known_state_ids_before_command:
            state = self.get_semantic_state(sample.environment_id, state_id)
            if state is None:
                raise ValueError(f"pre-action history state is missing: {state_id}")
            known_states.append(state.id)
        known_transitions: list[TransitionEdgeV1] = []
        if consolidation.transition_edge_id in snapshot.known_transition_ids_before_command:
            raise ValueError("result transition cannot pre-exist in pre-action history")
        for transition_id in snapshot.known_transition_ids_before_command:
            prior = self.get_transition_edge(sample.environment_id, transition_id)
            if prior is None:
                raise ValueError(
                    f"pre-action history transition is missing: {transition_id}"
                )
            known_transitions.append(prior)
        blocking = ActionHistoryGuard.BLOCKING_OUTCOMES
        recomputed_matches = [
            prior
            for prior in known_transitions
            if prior.from_state_id == consolidation.before_state_id
            and prior.outcome in blocking
            and _same_action_target(prior, command.action, command.target_bounds)
        ]
        if [item.id for item in recomputed_matches] != snapshot.matched_transition_ids:
            raise ValueError("pre-action matching failure history was not deterministically derived")

        autonomous_metadata = step.metadata.get("autonomous_execution")
        if not isinstance(autonomous_metadata, dict):
            raise ValueError("executed action-quality evidence lacks autonomous metadata")
        before_state = SemanticStateV1.model_validate(
            autonomous_metadata.get("before_state")
        )
        after_state = SemanticStateV1.model_validate(
            autonomous_metadata.get("after_state")
        )
        artifacts = []
        for artifact_id in consolidation.evidence_ref.artifact_ids:
            artifact = self.observatory_store.get_artifact(artifact_id)
            if artifact is None:
                raise ValueError(f"action-quality terminal artifact is missing: {artifact_id}")
            artifacts.append(artifact)
        outcome = CanonicalExecutionOutcomeV1(
            environment_id=sample.environment_id,
            command_id=sample.command_id,
            task_id=consolidation.task_id,
            status=autonomous_metadata.get("status"),
            evidence_run=evidence_run,
            evidence_step=step,
            artifacts=artifacts,
            action_run=action_run,
            before_state=before_state,
            after_state=after_state,
            observed_change=autonomous_metadata.get("observed_change"),
            failure_reason=autonomous_metadata.get("failure_reason"),
            recovered_from_interruption=bool(
                autonomous_metadata.get("recovered_from_interruption")
            ),
        )
        task_decision = TaskBoard().select(self.list_tasks(sample.environment_id))
        selectable_task_ids = [
            item.task_id
            for item in task_decision.dispositions
            if item.disposition == "eligible"
        ]
        history = ActionQualityHistoryContextV1(
            environment_id=sample.environment_id,
            command_id=sample.command_id,
            result_transition=transition,
            known_state_ids_before_command=known_states,
            known_transition_ids_before_command=[item.id for item in known_transitions],
            matching_prior_transitions=recomputed_matches,
            selectable_task_ids_after_consolidation=selectable_task_ids,
        )
        environment = self.get_environment(sample.environment_id)
        if environment is None:
            raise ValueError("action-quality environment is missing")
        resolved_telemetry, _receipt = resolve_planner_telemetry(
            self,
            environment=environment,
            command=command,
            task_id=task.id,
        )
        expected = produce_action_quality_sample(
            command=command,
            outcome=outcome,
            consolidation=consolidation,
            session=session,
            task=task,
            history=history,
            telemetry=resolved_telemetry,
        )
        derivations = sorted(
            (
                item
                for item in self.list_account_metric_derivations(
                    sample.environment_id,
                    limit=1_000_000,
                )
                if item.after_observation.evidence_step_id == step.id
            ),
            key=lambda item: (item.definition.metric_key, item.id),
        )
        expected = attach_account_metric_derivations(
            self.observatory_store,
            expected,
            derivations,
        )
        expected = attach_expected_change_measurement(
            self.observatory_store,
            sample=expected,
            command=command,
            outcome=outcome,
        )
        if expected != sample:
            differing = sorted(
                key
                for key, value in expected.model_dump(mode="json", by_alias=True).items()
                if sample.model_dump(mode="json", by_alias=True).get(key) != value
            )
            raise ValueError(
                "action-quality sample differs from deterministic canonical recomputation: "
                + ", ".join(differing)
            )

    def get_action_quality_sample(
        self,
        environment_id: str,
        sample_id: str,
    ) -> ActionQualitySampleV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_action_quality_samples
                WHERE environment_id=? AND id=?
                """,
                (environment_id, sample_id),
            ).fetchone()
        return ActionQualitySampleV1.model_validate_json(row["body_json"]) if row else None

    def list_action_quality_samples(
        self,
        environment_id: str,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[ActionQualitySampleV1]:
        if limit < 1:
            raise ValueError("action-quality sample limit must be positive")
        query = "SELECT body_json FROM ai_player_action_quality_samples WHERE environment_id=?"
        parameters: list[Any] = [environment_id]
        if session_id is not None:
            query += " AND session_id=?"
            parameters.append(session_id)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [ActionQualitySampleV1.model_validate_json(row["body_json"]) for row in rows]

    @staticmethod
    def _soft_signal_review_request_id(review: PlayerSoftSignalReviewV1) -> str:
        low_signals = sorted(item.signal for item in review.signals if item.score < 3)
        payload = json.dumps(
            [review.environment_id, review.id, low_signals],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"soft-review-request.{hashlib.sha256(payload).hexdigest()[:24]}"

    def _validate_soft_signal_review_binding(
        self,
        review: PlayerSoftSignalReviewV1,
        samples: list[ActionQualitySampleV1],
        *,
        verification_trust_store: PlayerSoftSignalReviewerTrustStore | None = None,
    ) -> None:
        trust_store = verification_trust_store or self.soft_signal_reviewer_trust_store
        if review.trust_scope == "formal_external" and verification_trust_store is not None:
            if verification_trust_store is not self.soft_signal_reviewer_trust_store:
                raise ValueError("formal soft-signal reviews require the configured formal trust root")
        trust_store.verify(review)
        subject_sessions = {sample.session_id for sample in samples}
        if set(review.subject_session_ids) != subject_sessions:
            raise ValueError("soft-signal review subject sessions do not match its samples")
        if review.reviewer_session_id in subject_sessions:
            raise ValueError("soft-signal reviewer session executed a reviewed action")

        subject_step_ids = {
            step_id
            for sample in samples
            for reference in sample.evidence_refs
            for step_id in reference.evidence_step_ids
        }
        signal_step_ids = {
            step_id for signal in review.signals for step_id in signal.evidence_step_ids
        }
        if not signal_step_ids.issubset(subject_step_ids):
            raise ValueError("soft-signal scores must cite reviewed action evidence steps")
        subject_action_run_ids = {
            sample.action_run_id for sample in samples if sample.action_run_id is not None
        }
        if (
            review.attestation is not None
            and review.attestation.reviewer_run_id in subject_action_run_ids
        ):
            raise ValueError("soft-signal reviewer run cannot be a reviewed action run")

        run = self.observatory_store.get_evidence_run(review.review_evidence_run_id)
        step = self.observatory_store.get_evidence_step(review.review_evidence_step_id)
        if run is None or step is None:
            raise ValueError("soft-signal review evidence run and step must exist")
        if run.status != "passed" or step.status != "passed":
            raise ValueError("soft-signal review evidence must be terminal and passed")
        if run.scope_id != review.environment_id:
            raise ValueError("soft-signal review evidence belongs to another environment")
        if step.evidence_run_id != run.id or step.id not in run.step_ids:
            raise ValueError("soft-signal review step is not bound to its evidence run")
        if step.action.type != "wait" or step.action_run_id is not None:
            raise ValueError("soft-signal review evidence must be a non-device review step")
        referenced_run_ids = {
            run_id for reference in review.evidence_refs for run_id in reference.evidence_run_ids
        }
        referenced_step_ids = {
            step_id for reference in review.evidence_refs for step_id in reference.evidence_step_ids
        }
        if run.id not in referenced_run_ids or step.id not in referenced_step_ids:
            raise ValueError("soft-signal review must retain its canonical review evidence")

        metadata = step.metadata.get("soft_signal_review")
        expected_metadata = {
            "schema": "game-observatory.ai-player.soft-signal-review-evidence.v1",
            "environment_id": review.environment_id,
            "review_id": review.id,
            "reviewer_id": review.reviewer_id,
            "reviewer_role": review.reviewer_role,
            "trust_scope": review.trust_scope,
            "sample_ids": review.sample_ids,
            "payload_sha256": review.compute_attestation_payload_sha256(),
        }
        if not isinstance(metadata, dict) or any(
            metadata.get(key) != value for key, value in expected_metadata.items()
        ):
            raise ValueError("soft-signal review evidence metadata does not match the review")

        executing_actors: set[str] = set()
        with self._connection() as connection:
            placeholders = ",".join("?" for _ in subject_sessions)
            if placeholders:
                rows = connection.execute(
                    "SELECT actor FROM ai_player_session_lifecycle_events "
                    f"WHERE session_id IN ({placeholders})",
                    tuple(sorted(subject_sessions)),
                ).fetchall()
                executing_actors.update(str(row["actor"]) for row in rows)
        for sample in samples:
            if sample.evidence_step_id is None:
                continue
            subject_step = self.observatory_store.get_evidence_step(sample.evidence_step_id)
            if subject_step is None:
                continue
            autonomous = subject_step.metadata.get("autonomous_execution")
            if not isinstance(autonomous, dict):
                continue
            for key in ("actor", "agent_id", "executor_actor", "operator"):
                value = autonomous.get(key)
                if isinstance(value, str) and value.strip():
                    executing_actors.add(value)
        if review.reviewer_id in executing_actors:
            raise ValueError("soft-signal reviewer also executed the reviewed session")

    def append_soft_signal_review(
        self,
        review: PlayerSoftSignalReviewV1,
        *,
        verification_trust_store: PlayerSoftSignalReviewerTrustStore | None = None,
    ) -> PlayerSoftSignalReviewV1:
        self._require_environment(review.environment_id)
        samples = [
            self.get_action_quality_sample(review.environment_id, sample_id)
            for sample_id in review.sample_ids
        ]
        if any(sample is None for sample in samples):
            raise ValueError("soft-signal review samples must exist in the environment")
        canonical_samples = [sample for sample in samples if sample is not None]
        if review.responds_to_request_id is not None:
            if review.trust_scope != "formal_external":
                raise ValueError("development-only reviews cannot answer formal review requests")
            request = self.get_soft_signal_review_request(
                review.environment_id,
                review.responds_to_request_id,
            )
            if request is None:
                raise ValueError("soft-signal review response request does not exist")
            if not set(review.sample_ids).issubset(request.sample_ids):
                raise ValueError("soft-signal review response is outside the request samples")
            trigger_review = self.get_soft_signal_review(
                review.environment_id,
                request.trigger_review_id,
            )
            if trigger_review is None:
                raise ValueError("soft-signal review request trigger is missing")
            if trigger_review.reviewer_id == review.reviewer_id:
                raise ValueError("soft-signal review response requires another reviewer")
        self._prepare_entity(review.environment_id, review.evidence_refs)
        self._validate_soft_signal_review_binding(
            review,
            canonical_samples,
            verification_trust_store=verification_trust_store,
        )

        low_signals = sorted(item.signal for item in review.signals if item.score < 3)
        request = None
        if low_signals and review.trust_scope == "formal_external":
            request = PlayerSoftSignalReviewRequestV1(
                id=self._soft_signal_review_request_id(review),
                environment_id=review.environment_id,
                evidence_refs=review.evidence_refs,
                trigger_review_id=review.id,
                sample_ids=review.sample_ids,
                signal_names=low_signals,
                reason=(
                    "独立复核发现软指标低于 3 分，需要另一审阅者回看原始动作、画面与任务上下文。"
                ),
                created_at=review.reviewed_at,
            )

        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_soft_signal_reviews(
                        environment_id, id, reviewer_id, reviewer_role,
                        reviewer_session_id, review_evidence_run_id,
                        review_evidence_step_id, responds_to_request_id,
                        minimum_score, body_json, reviewed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        review.environment_id,
                        review.id,
                        review.reviewer_id,
                        review.reviewer_role,
                        review.reviewer_session_id,
                        review.review_evidence_run_id,
                        review.review_evidence_step_id,
                        review.responds_to_request_id,
                        min(item.score for item in review.signals),
                        review.model_dump_json(by_alias=True),
                        review.reviewed_at,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO ai_player_soft_signal_review_subjects(
                        environment_id, review_id, reviewer_id, sample_id
                    ) VALUES(?,?,?,?)
                    """,
                    [
                        (
                            review.environment_id,
                            review.id,
                            review.reviewer_id,
                            sample_id,
                        )
                        for sample_id in review.sample_ids
                    ],
                )
                self._record_evidence(
                    connection,
                    review.environment_id,
                    "player_soft_signal_review",
                    review.id,
                    "1",
                    review.evidence_refs,
                )
                if request is not None:
                    connection.execute(
                        """
                        INSERT INTO ai_player_soft_signal_review_requests(
                            environment_id, id, trigger_review_id, execution_mode,
                            device_action_budget, body_json, created_at
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            request.environment_id,
                            request.id,
                            request.trigger_review_id,
                            request.execution_mode,
                            request.device_action_budget,
                            request.model_dump_json(by_alias=True),
                            request.created_at,
                        ),
                    )
                    self._record_evidence(
                        connection,
                        request.environment_id,
                        "player_soft_signal_review_request",
                        request.id,
                        "1",
                        request.evidence_refs,
                    )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"soft-signal review already exists or reuses proof: {review.id}") from error
        return review

    def get_soft_signal_review(
        self,
        environment_id: str,
        review_id: str,
    ) -> PlayerSoftSignalReviewV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT body_json FROM ai_player_soft_signal_reviews "
                "WHERE environment_id=? AND id=?",
                (environment_id, review_id),
            ).fetchone()
        return PlayerSoftSignalReviewV1.model_validate_json(row["body_json"]) if row else None

    def list_soft_signal_reviews(
        self,
        environment_id: str,
        *,
        sample_ids: Sequence[str] | None = None,
        trust_scope: str | None = None,
        limit: int = 100,
    ) -> list[PlayerSoftSignalReviewV1]:
        if limit < 1:
            raise ValueError("soft-signal review limit must be positive")
        with self._connection() as connection:
            query = (
                "SELECT body_json FROM ai_player_soft_signal_reviews "
                "WHERE environment_id=? ORDER BY reviewed_at DESC, rowid DESC"
            )
            parameters: list[Any] = [environment_id]
            if sample_ids is None and trust_scope is None:
                query += " LIMIT ?"
                parameters.append(limit)
            rows = connection.execute(query, parameters).fetchall()
        reviews = [PlayerSoftSignalReviewV1.model_validate_json(row["body_json"]) for row in rows]
        if trust_scope is not None:
            if trust_scope not in {"formal_external", "development_only"}:
                raise ValueError("unknown soft-signal review trust scope")
            reviews = [review for review in reviews if review.trust_scope == trust_scope]
        if sample_ids is None:
            return reviews[:limit]
        window = set(sample_ids)
        return [
            review for review in reviews if set(review.sample_ids).issubset(window)
        ][:limit]

    def get_soft_signal_review_request(
        self,
        environment_id: str,
        request_id: str,
    ) -> PlayerSoftSignalReviewRequestV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT body_json FROM ai_player_soft_signal_review_requests "
                "WHERE environment_id=? AND id=?",
                (environment_id, request_id),
            ).fetchone()
        return (
            PlayerSoftSignalReviewRequestV1.model_validate_json(row["body_json"])
            if row
            else None
        )

    def list_open_soft_signal_review_requests(
        self,
        environment_id: str,
    ) -> list[PlayerSoftSignalReviewRequestV1]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT request.body_json
                FROM ai_player_soft_signal_review_requests AS request
                LEFT JOIN ai_player_soft_signal_reviews AS response
                  ON response.environment_id=request.environment_id
                 AND response.responds_to_request_id=request.id
                WHERE request.environment_id=? AND response.id IS NULL
                ORDER BY request.created_at, request.id
                """,
                (environment_id,),
            ).fetchall()
        return [
            PlayerSoftSignalReviewRequestV1.model_validate_json(row["body_json"])
            for row in rows
        ]

    def append_iteration_assessment(
        self,
        assessment: PlayerIterationAssessmentV1,
    ) -> PlayerIterationAssessmentV1:
        self._require_environment(assessment.environment_id)
        stored_samples = [
            self.get_action_quality_sample(assessment.environment_id, sample_id)
            for sample_id in assessment.sample_ids
        ]
        missing_sample_ids = [
            sample_id
            for sample_id, sample in zip(assessment.sample_ids, stored_samples, strict=True)
            if sample is None
        ]
        if missing_sample_ids:
            raise ValueError(
                "iteration assessment samples are missing: " + ", ".join(missing_sample_ids)
            )
        stored_reviews = [
            self.get_soft_signal_review(assessment.environment_id, review_id)
            for review_id in assessment.soft_signal_review_ids
        ]
        if any(review is None for review in stored_reviews):
            raise ValueError("iteration assessment soft-signal reviews are missing")
        try:
            created_at = datetime.fromisoformat(assessment.created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("iteration assessment created_at must use ISO-8601") from error
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("iteration assessment created_at must include a timezone")
        if created_at > datetime.now(timezone.utc):
            raise ValueError("iteration assessment created_at cannot be in the future")

        from .iteration_monitor import (
            assess_player_iteration,
            stable_iteration_assessment_id,
        )

        canonical_samples = [sample for sample in stored_samples if sample is not None]
        expected_id = stable_iteration_assessment_id(
            assessment.environment_id,
            assessment.window_kind,
            assessment.sample_ids,
            assessment.soft_signal_review_ids,
        )
        if assessment.id != expected_id:
            raise ValueError("iteration assessment id is not derived from its canonical window")
        recomputed = assess_player_iteration(
            assessment_id=expected_id,
            window_kind=assessment.window_kind,
            samples=canonical_samples,
            soft_signal_reviews=[review for review in stored_reviews if review is not None],
        )
        if assessment != recomputed:
            raise ValueError("iteration assessment does not match deterministic recomputation")

        self._prepare_entity(assessment.environment_id, assessment.evidence_refs)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_iteration_assessments(
                        environment_id, id, window_kind, overall_status,
                        directive, body_json, created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        assessment.environment_id,
                        assessment.id,
                        assessment.window_kind,
                        assessment.overall_status,
                        assessment.directive,
                        assessment.model_dump_json(by_alias=True),
                        assessment.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"player-iteration assessment already exists: {assessment.id}"
                ) from error
            self._record_evidence(
                connection,
                assessment.environment_id,
                "player_iteration_assessment",
                assessment.id,
                "1",
                assessment.evidence_refs,
            )
        return assessment

    def get_iteration_assessment(
        self,
        environment_id: str,
        assessment_id: str,
    ) -> PlayerIterationAssessmentV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_iteration_assessments
                WHERE environment_id=? AND id=?
                """,
                (environment_id, assessment_id),
            ).fetchone()
        return PlayerIterationAssessmentV1.model_validate_json(row["body_json"]) if row else None

    def list_iteration_assessments(
        self,
        environment_id: str,
        *,
        limit: int = 20,
    ) -> list[PlayerIterationAssessmentV1]:
        if limit < 1:
            raise ValueError("iteration assessment limit must be positive")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_iteration_assessments
                WHERE environment_id=?
                ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                (environment_id, limit),
            ).fetchall()
        return [
            PlayerIterationAssessmentV1.model_validate_json(row["body_json"])
            for row in rows
        ]

    @staticmethod
    def _remediation_reference_ids(
        verification: Tier1RemediationVerificationV1,
    ) -> tuple[set[str], set[str]]:
        return (
            {
                run_id
                for reference in verification.evidence_refs
                for run_id in reference.evidence_run_ids
            },
            {
                step_id
                for reference in verification.evidence_refs
                for step_id in reference.evidence_step_ids
            },
        )

    def _validate_remediation_evidence_step(
        self,
        *,
        verification: Tier1RemediationVerificationV1,
        evidence_run_id: str,
        evidence_step_id: str,
        expected_run_status: str,
        expected_step_status: str,
        expected_adapter: str,
        expected_metadata_key: str,
        expected_metadata: Mapping[str, Any],
    ) -> None:
        run = self.observatory_store.get_evidence_run(evidence_run_id)
        step = self.observatory_store.get_evidence_step(evidence_step_id)
        if run is None or step is None:
            raise ValueError("remediation evidence run and step must exist")
        if run.scope_id != verification.environment_id:
            raise ValueError("remediation evidence belongs to another environment")
        if run.status != expected_run_status or step.status != expected_step_status:
            raise ValueError("remediation evidence terminal status contradicts its result")
        if not run.ended_at or not step.ended_at:
            raise ValueError("remediation evidence must be terminal")
        if run.adapter != expected_adapter:
            raise ValueError("remediation evidence did not originate from the fixed gate adapter")
        if step.evidence_run_id != run.id or step.id not in run.step_ids:
            raise ValueError("remediation evidence step is not bound to its run")
        if step.action.type != "wait" or step.action_run_id is not None:
            raise ValueError("remediation regression evidence must be non-device")
        run_ids, step_ids = self._remediation_reference_ids(verification)
        if run.id not in run_ids or step.id not in step_ids:
            raise ValueError("remediation verification did not retain canonical evidence")
        metadata = step.metadata.get(expected_metadata_key)
        if not isinstance(metadata, dict) or any(
            metadata.get(key) != value for key, value in expected_metadata.items()
        ):
            raise ValueError("remediation evidence metadata does not match signed content")

    def validate_tier1_remediation_verification(
        self,
        verification: Tier1RemediationVerificationV1,
    ) -> None:
        """Revalidate a signed remediation against immutable and live canonical evidence."""

        self._require_environment(verification.environment_id)
        assessment = self.get_iteration_assessment(
            verification.environment_id,
            verification.failed_assessment_id,
        )
        if assessment is None:
            raise ValueError("remediation failed assessment does not exist")
        if (
            assessment.overall_status != "failed"
            or assessment.directive != "pause_physical_and_repair_perception_executor"
            or assessment.tiers[0].status != "failed"
        ):
            raise ValueError("tier-1 remediation must target a tier-1 failed assessment")
        if verification.failed_assessment_sha256 != iteration_assessment_fingerprint(assessment):
            raise ValueError("remediation failed-assessment fingerprint does not match")
        if verification.gate_id != TIER1_REMEDIATION_GATE_ID:
            raise ValueError("remediation gate id is unsupported")
        if verification.policy_version != assessment.policy_version:
            raise ValueError("remediation policy version differs from the failed assessment")
        if verification.policy_sha256 != tier1_remediation_policy_fingerprint():
            raise ValueError("remediation policy fingerprint does not match the fixed hard gate")
        expected_id = stable_tier1_remediation_verification_id(
            environment_id=verification.environment_id,
            failed_assessment_id=verification.failed_assessment_id,
            regression_cases=verification.regression_cases,
            verifier_id=verification.verifier_id,
            verification_evidence_step_id=verification.verification_evidence_step_id,
        )
        if verification.id != expected_id:
            raise ValueError("remediation id is not derived from its signed canonical inputs")
        self.remediation_verifier_trust_store.verify(verification)
        if (
            verification.attestation is None
            or verification.attestation.verifier_run_id
            != verification.verification_evidence_run_id
        ):
            raise ValueError("remediation attestation is not bound to the verifier evidence run")

        try:
            verified_at = datetime.fromisoformat(verification.verified_at.replace("Z", "+00:00"))
            assessment_at = datetime.fromisoformat(assessment.created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("remediation timestamps must use ISO-8601") from error
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            raise ValueError("remediation verified_at must include a timezone")
        if assessment_at.tzinfo is None or assessment_at.utcoffset() is None:
            raise ValueError("failed assessment created_at must include a timezone")
        if verified_at < assessment_at:
            raise ValueError("remediation verification predates its failed assessment")
        if verified_at > datetime.now(timezone.utc):
            raise ValueError("remediation verification cannot be in the future")

        source_sample_ids = {
            sample_id
            for case in verification.regression_cases
            if case.partition == "failed_fixture"
            for sample_id in case.source_sample_ids
        }
        if source_sample_ids != set(assessment.sample_ids):
            raise ValueError("failed-fixture regressions must cover the complete failed window")
        for sample_id in source_sample_ids:
            if self.get_action_quality_sample(verification.environment_id, sample_id) is None:
                raise ValueError("remediation references a missing failed-window sample")

        subject_samples = [
            self.get_action_quality_sample(verification.environment_id, sample_id)
            for sample_id in assessment.sample_ids
        ]
        subject_session_ids = {
            sample.session_id for sample in subject_samples if sample is not None
        }
        if verification.verifier_session_id in subject_session_ids:
            raise ValueError("remediation verifier session executed a failed-window action")
        with self._connection() as connection:
            verifier_session = connection.execute(
                "SELECT environment_id FROM ai_player_sessions WHERE id=?",
                (verification.verifier_session_id,),
            ).fetchone()
            if (
                verifier_session is None
                or verifier_session["environment_id"] != verification.environment_id
            ):
                raise ValueError("remediation verifier session is not canonical in this environment")
            executed_by_verifier_session = connection.execute(
                "SELECT 1 FROM ai_player_action_quality_samples WHERE environment_id=? "
                "AND session_id=? LIMIT 1",
                (verification.environment_id, verification.verifier_session_id),
            ).fetchone()
            if executed_by_verifier_session is not None:
                raise ValueError("remediation verifier session contains device action samples")
            actors = {
                str(row["actor"])
                for row in connection.execute(
                    "SELECT actor FROM ai_player_session_lifecycle_events WHERE session_id=?",
                    (verification.verifier_session_id,),
                ).fetchall()
            }
            if verification.verifier_id not in actors:
                raise ValueError("remediation verifier identity is not bound to its session ledger")

        suite_manifest_sha256: str | None = None
        for case in verification.regression_cases:
            fixture_artifact = self.observatory_store.get_artifact(case.fixture_artifact_id)
            result_artifact = self.observatory_store.get_artifact(case.result_artifact_id)
            if fixture_artifact is None or result_artifact is None:
                raise ValueError("remediation fixture and result artifacts must exist")
            if fixture_artifact.sha256 != case.fixture_sha256:
                raise ValueError("remediation fixture artifact hash does not match signed content")
            if result_artifact.sha256 != case.result_sha256:
                raise ValueError("remediation result artifact hash does not match signed content")
            try:
                fixture_bytes = Path(fixture_artifact.path).read_bytes()
                result_bytes = Path(result_artifact.path).read_bytes()
                if hashlib.sha256(fixture_bytes).hexdigest() != case.fixture_sha256:
                    raise ValueError("remediation fixture file hash does not match signed content")
                if hashlib.sha256(result_bytes).hexdigest() != case.result_sha256:
                    raise ValueError("remediation result file hash does not match signed content")
                fixture = Tier1RemediationRegressionFixtureV1.model_validate_json(
                    fixture_bytes
                )
                result = Tier1RemediationRegressionResultV1.model_validate_json(
                    result_bytes
                )
            except (OSError, ValueError) as error:
                raise ValueError("remediation machine artifacts are unreadable or invalid") from error
            if fixture.content_sha256() != case.fixture_sha256:
                raise ValueError("remediation fixture bytes do not match their canonical model")
            if (
                fixture.environment_id != verification.environment_id
                or fixture.failed_assessment_id != verification.failed_assessment_id
                or fixture.gate_id != verification.gate_id
                or fixture.fixture_id != case.fixture_id
                or fixture.partition != case.partition
                or fixture.source_sample_ids != case.source_sample_ids
            ):
                raise ValueError("remediation fixture contradicts its signed case binding")
            if suite_manifest_sha256 is None:
                suite_manifest_sha256 = fixture.suite_manifest_sha256
            elif suite_manifest_sha256 != fixture.suite_manifest_sha256:
                raise ValueError("remediation fixtures do not share one sealed suite manifest")
            recomputed_result = run_tier1_remediation_fixture(
                fixture,
                result_id=result.id,
                generated_at=result.generated_at,
            )
            if result != recomputed_result:
                raise ValueError("remediation result does not match fixed-runner recomputation")
            if (
                result.metrics != case.metrics
                or result.passed != case.passed
                or result.fixture_id != case.fixture_id
                or result.fixture_sha256 != case.fixture_sha256
            ):
                raise ValueError("remediation result contradicts its signed case")
            terminal_status = "passed" if case.passed else "failed"
            self._validate_remediation_evidence_step(
                verification=verification,
                evidence_run_id=case.evidence_run_id,
                evidence_step_id=case.evidence_step_id,
                expected_run_status=terminal_status,
                expected_step_status=terminal_status,
                expected_adapter="ai-player-tier1-remediation-regression-v1",
                expected_metadata_key="tier1_remediation_case",
                expected_metadata={
                    "schema": "game-observatory.ai-player.tier1-remediation-case-evidence.v1",
                    "environment_id": verification.environment_id,
                    "verification_id": verification.id,
                    "failed_assessment_id": verification.failed_assessment_id,
                    "gate_id": verification.gate_id,
                    "case_id": case.id,
                    "partition": case.partition,
                    "fixture_id": case.fixture_id,
                    "metrics_sha256": case.metrics_sha256(),
                    "policy_sha256": verification.policy_sha256,
                },
            )

        self._validate_remediation_evidence_step(
            verification=verification,
            evidence_run_id=verification.verification_evidence_run_id,
            evidence_step_id=verification.verification_evidence_step_id,
            expected_run_status="passed",
            expected_step_status="passed",
            expected_adapter="ai-player-tier1-remediation-verifier-v1",
            expected_metadata_key="tier1_remediation_verification",
            expected_metadata={
                "schema": "game-observatory.ai-player.tier1-remediation-verifier-evidence.v1",
                "environment_id": verification.environment_id,
                "verification_id": verification.id,
                "failed_assessment_id": verification.failed_assessment_id,
                "gate_id": verification.gate_id,
                "verifier_id": verification.verifier_id,
                "payload_sha256": verification.payload_sha256(),
                "decision": verification.decision,
            },
        )

    def append_tier1_remediation_verification(
        self,
        verification: Tier1RemediationVerificationV1,
    ) -> Tier1RemediationVerificationV1:
        self._prepare_entity(verification.environment_id, verification.evidence_refs)
        self.validate_tier1_remediation_verification(verification)
        with self._write_lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO ai_player_tier1_remediation_verifications(
                        environment_id, id, failed_assessment_id, gate_id, decision,
                        verifier_id, verifier_session_id, verification_evidence_run_id,
                        verification_evidence_step_id, body_json, verified_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        verification.environment_id,
                        verification.id,
                        verification.failed_assessment_id,
                        verification.gate_id,
                        verification.decision,
                        verification.verifier_id,
                        verification.verifier_session_id,
                        verification.verification_evidence_run_id,
                        verification.verification_evidence_step_id,
                        verification.model_dump_json(by_alias=True),
                        verification.verified_at,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO ai_player_tier1_remediation_cases(
                        environment_id, verification_id, case_id, partition,
                        fixture_id, evidence_run_id, evidence_step_id
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            verification.environment_id,
                            verification.id,
                            case.id,
                            case.partition,
                            case.fixture_id,
                            case.evidence_run_id,
                            case.evidence_step_id,
                        )
                        for case in verification.regression_cases
                    ],
                )
                self._record_evidence(
                    connection,
                    verification.environment_id,
                    "tier1_remediation_verification",
                    verification.id,
                    "1",
                    verification.evidence_refs,
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    "tier-1 remediation verification already exists or reuses evidence: "
                    f"{verification.id}"
                ) from error
        return verification

    def get_tier1_remediation_verification(
        self,
        environment_id: str,
        verification_id: str,
    ) -> Tier1RemediationVerificationV1 | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT body_json FROM ai_player_tier1_remediation_verifications "
                "WHERE environment_id=? AND id=?",
                (environment_id, verification_id),
            ).fetchone()
        return (
            Tier1RemediationVerificationV1.model_validate_json(row["body_json"])
            if row
            else None
        )

    def list_tier1_remediation_verifications(
        self,
        environment_id: str,
        *,
        failed_assessment_id: str | None = None,
        limit: int = 20,
    ) -> list[Tier1RemediationVerificationV1]:
        if limit < 1:
            raise ValueError("remediation verification limit must be positive")
        query = (
            "SELECT body_json FROM ai_player_tier1_remediation_verifications "
            "WHERE environment_id=?"
        )
        parameters: list[Any] = [environment_id]
        if failed_assessment_id is not None:
            query += " AND failed_assessment_id=?"
            parameters.append(failed_assessment_id)
        query += " ORDER BY verified_at DESC, rowid DESC LIMIT ?"
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            Tier1RemediationVerificationV1.model_validate_json(row["body_json"])
            for row in rows
        ]

    @staticmethod
    def _stored_evidence_matches(
        connection: sqlite3.Connection,
        environment_id: str,
        entity_type: str,
        entity_id: str,
        entity_version: str,
        references: Sequence[EvidenceReferenceV1],
    ) -> bool:
        fields = (
            ("artifact", "artifact_ids"),
            ("evidence_run", "evidence_run_ids"),
            ("evidence_step", "evidence_step_ids"),
            ("trace_run", "trace_run_ids"),
            ("source", "source_ids"),
        )
        expected = {
            (reference_kind, reference_id)
            for reference in references
            for reference_kind, field_name in fields
            for reference_id in getattr(reference, field_name)
        }
        rows = connection.execute(
            """
            SELECT reference_kind, reference_id FROM ai_player_entity_evidence
            WHERE environment_id=? AND entity_type=? AND entity_id=? AND entity_version=?
            """,
            (environment_id, entity_type, entity_id, entity_version),
        ).fetchall()
        return {(row["reference_kind"], row["reference_id"]) for row in rows} == expected

    def _record_evidence(
        self,
        connection: sqlite3.Connection,
        environment_id: str,
        entity_type: str,
        entity_id: str,
        entity_version: str,
        references: Sequence[EvidenceReferenceV1],
    ) -> None:
        connection.execute(
            """
            DELETE FROM ai_player_entity_evidence
            WHERE environment_id=? AND entity_type=? AND entity_id=? AND entity_version=?
            """,
            (environment_id, entity_type, entity_id, entity_version),
        )
        fields = (
            ("artifact", "artifact_ids"),
            ("evidence_run", "evidence_run_ids"),
            ("evidence_step", "evidence_step_ids"),
            ("trace_run", "trace_run_ids"),
            ("source", "source_ids"),
        )
        for reference in references:
            reference_json = reference.model_dump_json(by_alias=True)
            for reference_kind, field_name in fields:
                for reference_id in getattr(reference, field_name):
                    origin = connection.execute(
                        """
                        SELECT origin_environment_id FROM ai_player_evidence_origins
                        WHERE reference_kind=? AND reference_id=?
                        """,
                        (reference_kind, reference_id),
                    ).fetchone()
                    if origin and not self._environment_can_inherit_evidence(
                        origin["origin_environment_id"],
                        environment_id,
                        connection=connection,
                    ):
                        raise ValueError(
                            "cross-environment evidence write: "
                            f"{reference_kind}:{reference_id} originates in "
                            f"{origin['origin_environment_id']}"
                        )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO ai_player_evidence_origins(
                            reference_kind, reference_id, origin_environment_id, created_at
                        ) VALUES(?,?,?,?)
                        """,
                        (reference_kind, reference_id, environment_id, utc_now()),
                    )


                    connection.execute(
                        """
                        INSERT OR IGNORE INTO ai_player_entity_evidence(
                            environment_id, entity_type, entity_id, entity_version,
                            reference_kind, reference_id, reference_json
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            environment_id,
                            entity_type,
                            entity_id,
                            entity_version,
                            reference_kind,
                            reference_id,
                            reference_json,
                        ),
                    )


class _StateRecognitionTransactionView:
    """The subset of AIPlayerStore used by SemanticStateRecognizer on one connection."""

    def __init__(
        self,
        owner: AIPlayerStore,
        connection: sqlite3.Connection,
    ) -> None:
        self.owner = owner
        self.connection = connection
        self.inserted_counts = {
            "inserted_state_observation_count": 0,
            "inserted_state_assignment_count": 0,
            "inserted_semantic_state_version_count": 0,
            "inserted_transition_edge_version_count": 0,
        }

    def append_state_observation(
        self,
        observation: StateObservationV1,
    ) -> StateObservationV1:
        body = observation.model_dump_json(by_alias=True)
        existing = self.connection.execute(
            """
            SELECT body_json FROM ai_player_state_observations
            WHERE environment_id=? AND id=?
            """,
            (observation.environment_id, observation.id),
        ).fetchone()
        if existing:
            if existing["body_json"] == body:
                return observation
            raise ValueError(f"state observation is immutable: {observation.id}")
        self.connection.execute(
            """
            INSERT INTO ai_player_state_observations(
                environment_id, id, feature_hash, body_json, captured_at, created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                observation.environment_id,
                observation.id,
                observation.feature_hash,
                body,
                observation.captured_at,
                observation.created_at,
            ),
        )
        self.owner._record_evidence(
            self.connection,
            observation.environment_id,
            "state_observation",
            observation.id,
            "1",
            observation.evidence_refs,
        )
        self.inserted_counts["inserted_state_observation_count"] += 1
        return observation

    def get_state_observation(
        self,
        environment_id: str,
        observation_id: str,
    ) -> StateObservationV1 | None:
        row = self.connection.execute(
            """
            SELECT body_json FROM ai_player_state_observations
            WHERE environment_id=? AND id=?
            """,
            (environment_id, observation_id),
        ).fetchone()
        return StateObservationV1.model_validate_json(row["body_json"]) if row else None

    def list_state_observations(
        self,
        environment_id: str,
        *,
        feature_hash: str | None = None,
    ) -> list[StateObservationV1]:
        query = "SELECT body_json FROM ai_player_state_observations WHERE environment_id=?"
        parameters: list[Any] = [environment_id]
        if feature_hash is not None:
            query += " AND feature_hash=?"
            parameters.append(feature_hash)
        query += " ORDER BY captured_at, id"
        rows = self.connection.execute(query, parameters).fetchall()
        return [StateObservationV1.model_validate_json(row["body_json"]) for row in rows]

    def list_active_semantic_states_by_feature_hash(
        self,
        environment_id: str,
        feature_hash: str,
    ) -> list[SemanticStateV1]:
        return _active_semantic_states_for_feature_hash(
            self.connection,
            environment_id,
            feature_hash,
        )

    def put_semantic_state(self, state: SemanticStateV1) -> SemanticStateV1:
        body = state.model_dump_json(by_alias=True)
        existing = self.connection.execute(
            """
            SELECT body_json FROM ai_player_semantic_states
            WHERE environment_id=? AND id=? AND version=?
            """,
            (state.environment_id, state.id, state.version),
        ).fetchone()
        if existing:
            if existing["body_json"] == body:
                return state
            raise ValueError(f"semantic state version is immutable: {state.id}@{state.version}")
        self.connection.execute(
            """
            INSERT INTO ai_player_semantic_states(
                environment_id, id, version, status, semantic_fingerprint,
                body_json, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                state.environment_id,
                state.id,
                state.version,
                state.status,
                state.semantic_fingerprint,
                body,
                state.created_at,
            ),
        )
        _register_canonical_state_locked(self.connection, state)
        self.owner._record_evidence(
            self.connection,
            state.environment_id,
            "semantic_state",
            state.id,
            str(state.version),
            state.evidence_refs,
        )
        self.inserted_counts["inserted_semantic_state_version_count"] += 1
        return state

    def get_semantic_state(
        self,
        environment_id: str,
        state_id: str,
        *,
        version: int | None = None,
    ) -> SemanticStateV1 | None:
        query = "SELECT body_json FROM ai_player_semantic_states WHERE environment_id=? AND id=?"
        parameters: list[Any] = [environment_id, state_id]
        if version is not None:
            query += " AND version=?"
            parameters.append(version)
        query += " ORDER BY version DESC LIMIT 1"
        row = self.connection.execute(query, parameters).fetchone()
        return SemanticStateV1.model_validate_json(row["body_json"]) if row else None

    def list_semantic_states(
        self,
        environment_id: str,
        *,
        statuses: Sequence[str] | None = None,
        latest_only: bool = True,
    ) -> list[SemanticStateV1]:
        rows = self.connection.execute(
            """
            SELECT body_json FROM ai_player_semantic_states
            WHERE environment_id=? ORDER BY id, version DESC
            """,
            (environment_id,),
        ).fetchall()
        states = [SemanticStateV1.model_validate_json(row["body_json"]) for row in rows]
        if latest_only:
            latest: dict[str, SemanticStateV1] = {}
            for state in states:
                latest.setdefault(state.id, state)
            states = list(latest.values())
        if statuses is not None:
            allowed = set(statuses)
            states = [state for state in states if state.status in allowed]
        return sorted(states, key=lambda state: (state.created_at, state.id, state.version))

    def append_state_assignment(self, assignment: StateAssignmentV1) -> StateAssignmentV1:
        if self.get_state_observation(
            assignment.environment_id,
            assignment.observation_id,
        ) is None:
            raise ValueError(
                "state assignment observation is missing in environment: "
                f"{assignment.observation_id}"
            )
        if self.get_semantic_state(assignment.environment_id, assignment.state_id) is None:
            raise ValueError(
                f"semantic state is missing in environment: {assignment.state_id}"
            )
        current = self.get_current_state_assignment(
            assignment.environment_id,
            assignment.observation_id,
        )
        if current is None and assignment.version != 1:
            raise ValueError("first state assignment must use version 1")
        if current is not None:
            if assignment.version != current.version + 1:
                raise ValueError("state assignment version must advance exactly once")
            if assignment.supersedes_id != current.id:
                raise ValueError("state assignment must name the assignment it supersedes")
        body = assignment.model_dump_json(by_alias=True)
        existing = self.connection.execute(
            """
            SELECT body_json FROM ai_player_state_assignments
            WHERE environment_id=? AND id=?
            """,
            (assignment.environment_id, assignment.id),
        ).fetchone()
        if existing:
            if existing["body_json"] == body:
                return assignment
            raise ValueError(f"state assignment already exists: {assignment.id}")
        try:
            self.connection.execute(
                """
                INSERT INTO ai_player_state_assignments(
                    environment_id, id, observation_id, state_id, version,
                    status, method, body_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    assignment.environment_id,
                    assignment.id,
                    assignment.observation_id,
                    assignment.state_id,
                    assignment.version,
                    assignment.status,
                    assignment.method,
                    body,
                    assignment.created_at,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"state assignment already exists: {assignment.id}") from error
        self.owner._record_evidence(
            self.connection,
            assignment.environment_id,
            "state_assignment",
            assignment.id,
            str(assignment.version),
            assignment.evidence_refs,
        )
        self.inserted_counts["inserted_state_assignment_count"] += 1
        return assignment

    def get_current_state_assignment(
        self,
        environment_id: str,
        observation_id: str,
    ) -> StateAssignmentV1 | None:
        row = self.connection.execute(
            """
            SELECT body_json FROM ai_player_state_assignments
            WHERE environment_id=? AND observation_id=?
            ORDER BY version DESC LIMIT 1
            """,
            (environment_id, observation_id),
        ).fetchone()
        return StateAssignmentV1.model_validate_json(row["body_json"]) if row else None

    def list_state_assignments(
        self,
        environment_id: str,
        *,
        state_id: str | None = None,
        latest_only: bool = True,
    ) -> list[StateAssignmentV1]:
        rows = self.connection.execute(
            """
            SELECT body_json FROM ai_player_state_assignments
            WHERE environment_id=? ORDER BY observation_id, version DESC
            """,
            (environment_id,),
        ).fetchall()
        assignments = [
            StateAssignmentV1.model_validate_json(row["body_json"]) for row in rows
        ]
        if latest_only:
            latest: dict[str, StateAssignmentV1] = {}
            for assignment in assignments:
                latest.setdefault(assignment.observation_id, assignment)
            assignments = list(latest.values())
        if state_id is not None:
            assignments = [item for item in assignments if item.state_id == state_id]
        return sorted(
            assignments,
            key=lambda item: (item.created_at, item.id, item.version),
        )

    def get_transition_edge(
        self,
        environment_id: str,
        edge_id: str,
    ) -> TransitionEdgeV1 | None:
        row = self.connection.execute(
            """
            SELECT body_json FROM ai_player_transition_edges
            WHERE environment_id=? AND id=?
            ORDER BY version DESC LIMIT 1
            """,
            (environment_id, edge_id),
        ).fetchone()
        return TransitionEdgeV1.model_validate_json(row["body_json"]) if row else None

    def put_transition_edge(self, edge: TransitionEdgeV1) -> TransitionEdgeV1:
        source = self.get_semantic_state(edge.environment_id, edge.from_state_id)
        if source is None:
            raise ValueError(f"semantic state is missing in environment: {edge.from_state_id}")
        destination = None
        if (
            edge.to_state_id is not None
            and (
                destination := self.get_semantic_state(
                    edge.environment_id,
                    edge.to_state_id,
                )
            )
            is None
        ):
            raise ValueError(f"semantic state is missing in environment: {edge.to_state_id}")
        if edge.outcome.startswith("verified_") and (
            source.status != "accepted"
            or destination is None
            or destination.status != "accepted"
        ):
            raise ValueError("verified transition edge requires accepted endpoints")
        body = edge.model_dump_json(by_alias=True)
        existing = self.connection.execute(
            """
            SELECT body_json FROM ai_player_transition_edges
            WHERE environment_id=? AND id=? AND version=?
            """,
            (edge.environment_id, edge.id, edge.version),
        ).fetchone()
        if existing:
            if existing["body_json"] == body:
                return edge
            raise ValueError(f"transition edge version is immutable: {edge.id}@{edge.version}")
        self.connection.execute(
            """
            INSERT INTO ai_player_transition_edges(
                environment_id, id, version, from_state_id, to_state_id,
                outcome, body_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                edge.environment_id,
                edge.id,
                edge.version,
                edge.from_state_id,
                edge.to_state_id,
                edge.outcome,
                body,
                edge.created_at,
            ),
        )
        self.owner._record_evidence(
            self.connection,
            edge.environment_id,
            "transition_edge",
            edge.id,
            str(edge.version),
            edge.evidence_refs,
        )
        self.inserted_counts["inserted_transition_edge_version_count"] += 1
        return edge
