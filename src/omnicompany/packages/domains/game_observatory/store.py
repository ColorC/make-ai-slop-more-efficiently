from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, Iterable


from .canonical_graph import design_object_rows, design_relation_rows
from .media_validation import assert_public_artifacts
from .models import (
    ArtifactRef,
    CaptureSession,
    DeviceLease,
    EvidenceRun,
    EvidenceRunManifest,
    EvidenceStep,
    GameReport,
    GatewayControl,
    ReportAnnotation,
    ReportPatch,
    RunResult,
    SourceSnapshot,
    TargetRecord,
    VoiceRecord,
    utc_now,
)


SCHEMA_VERSION = 8


def repository_observatory_root() -> Path:
    repository_root = Path(__file__).resolve().parents[5]
    return repository_root / "data" / "domains" / "game_observatory"


def default_observatory_root() -> Path:
    """Resolve one cwd-independent canonical root, with an explicit deployment override."""

    configured = os.environ.get("GAME_OBSERVATORY_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return repository_observatory_root()


def workspace_shadow_observatory_root() -> Path:
    """Return the known cwd-relative shadow that must never become a live store."""

    repository_root = Path(__file__).resolve().parents[5]
    return repository_root.parent / "data" / "domains" / "game_observatory"


class ObservatoryStore:
    """Small, deterministic canonical store.

    SQLite is used for the verified local facility. Reports remain revisioned
    canonical JSON, while the v0.3 design-spec graph is projected into first-class
    objects and relations for machine queries. Artifacts remain immutable files.
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        allow_workspace_shadow: bool = False,
    ) -> None:
        self.root = (root or default_observatory_root()).resolve()
        if (
            root is not None
            and self.root == workspace_shadow_observatory_root().resolve()
            and not allow_workspace_shadow
        ):
            raise ValueError(
                "refusing the cwd-relative workspace shadow Observatory root; "
                f"use the canonical root {default_observatory_root()}"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifact_root = self.root / "artifacts"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        from .content_addressed_store import ContentAddressedObjectStore

        self.cas = ContentAddressedObjectStore(self.root / "cas")
        self.export_root = self.root / "exports"
        self.export_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "observatory.sqlite3"
        self._lock = threading.RLock()
        self._connection_local = threading.local()
        self.initialize()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        current = getattr(self._connection_local, "connection", None)
        if current is not None:
            yield current
            return
        connection = self._new_connection()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def read_session(self) -> Iterator[None]:
        """Reuse one read connection across a bounded query-heavy operation."""

        current = getattr(self._connection_local, "connection", None)
        if current is not None:
            yield
            return
        connection = self._new_connection()
        self._connection_local.connection = connection
        try:
            yield
        finally:
            self._connection_local.connection = None
            connection.close()

    @contextmanager
    def canonical_read_snapshot(self) -> Iterator[None]:
        """Pin raw canonical rows to one SQLite snapshot for provenance hashing."""

        current = getattr(self._connection_local, "connection", None)
        if current is not None:
            owns_transaction = not current.in_transaction
            if owns_transaction:
                current.execute("BEGIN")
            try:
                yield
            finally:
                if owns_transaction:
                    current.rollback()
            return
        connection = self._new_connection()
        connection.execute("BEGIN")
        self._connection_local.connection = connection
        try:
            yield
        finally:
            self._connection_local.connection = None
            connection.rollback()
            connection.close()

    def get_canonical_provenance_body(
        self,
        record_kind: str,
        record_id: str,
    ) -> dict[str, Any] | None:
        """Return persisted JSON without projecting it through the current model schema."""

        records = {
            "artifact": ("artifacts", "id"),
            "evidence_run": ("evidence_runs", "id"),
            "evidence_step": ("evidence_steps", "id"),
            "trace_run": ("runs", "id"),
            "source_snapshot": ("source_snapshots", "id"),
            "evidence_manifest": ("evidence_manifests", "evidence_run_id"),
        }
        record = records.get(record_kind)
        if record is None:
            raise ValueError(f"unsupported canonical provenance record kind: {record_kind}")
        table, key_column = record
        current = getattr(self._connection_local, "connection", None)
        if current is not None:
            row = current.execute(
                f"SELECT body_json FROM {table} WHERE {key_column}=?",  # noqa: S608
                (record_id,),
            ).fetchone()
        else:
            with self._connect() as connection:
                row = connection.execute(
                    f"SELECT body_json FROM {table} WHERE {key_column}=?",  # noqa: S608
                    (record_id,),
                ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["body_json"])
        if not isinstance(payload, dict):
            raise ValueError(
                f"canonical provenance record is not a JSON object: {record_kind}:{record_id}"
            )
        return payload

    def initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reports(
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    game_id TEXT NOT NULL,
                    system_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS report_tags(
                    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    tag TEXT NOT NULL,
                    PRIMARY KEY(report_id, tag)
                );
                CREATE TABLE IF NOT EXISTS report_revisions(
                    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL,
                    body_sha256 TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(report_id, revision),
                    UNIQUE(report_id, body_sha256)
                );
                CREATE TABLE IF NOT EXISTS design_objects(
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    body_sha256 TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(object_type, object_id)
                );
                CREATE INDEX IF NOT EXISTS idx_design_objects_report
                    ON design_objects(report_id, object_type, object_id);
                CREATE INDEX IF NOT EXISTS idx_design_objects_type
                    ON design_objects(object_type, object_id);
                CREATE TABLE IF NOT EXISTS design_relations(
                    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    src_type TEXT NOT NULL,
                    src_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    dst_type TEXT NOT NULL,
                    dst_id TEXT NOT NULL,
                    PRIMARY KEY(report_id, src_type, src_id, relation, dst_type, dst_id)
                );
                CREATE INDEX IF NOT EXISTS idx_design_relations_report
                    ON design_relations(report_id, relation);
                CREATE INDEX IF NOT EXISTS idx_design_relations_src
                    ON design_relations(src_type, src_id, relation);
                CREATE INDEX IF NOT EXISTS idx_design_relations_dst
                    ON design_relations(dst_type, dst_id, relation);
                CREATE TABLE IF NOT EXISTS sources(
                    id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    locator TEXT,
                    public INTEGER NOT NULL,
                    body_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY,
                    adapter TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    task_id TEXT,
                    status TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts(
                    id TEXT PRIMARY KEY,
                    run_id TEXT,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cas_objects(
                    sha256 TEXT PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    bytes INTEGER NOT NULL CHECK(bytes >= 0),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_cas_refs(
                    artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id),
                    sha256 TEXT NOT NULL REFERENCES cas_objects(sha256),
                    source_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_artifact_cas_refs_sha256
                    ON artifact_cas_refs(sha256, artifact_id);
                CREATE TABLE IF NOT EXISTS trace_events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_snapshots(
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    locator TEXT,
                    status TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_id, content_sha256, locator)
                );
                CREATE INDEX IF NOT EXISTS idx_source_snapshots_source
                    ON source_snapshots(source_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS voice_entries(
                    id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_voice_entries_report
                    ON voice_entries(report_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS target_registry(
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_leases(
                    id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    holder TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_device_leases_target
                    ON device_leases(target_id, status, expires_at);
                CREATE INDEX IF NOT EXISTS idx_device_leases_status_expires
                    ON device_leases(status, julianday(expires_at));
                CREATE TABLE IF NOT EXISTS gateway_events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    target_id TEXT,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gateway_controls(
                    target_id TEXT PRIMARY KEY,
                    emergency_stopped INTEGER NOT NULL,
                    body_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capture_sessions(
                    id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_capture_sessions_target
                    ON capture_sessions(target_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS evidence_runs(
                    id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_runs_target
                    ON evidence_runs(target_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS evidence_steps(
                    id TEXT PRIMARY KEY,
                    evidence_run_id TEXT NOT NULL REFERENCES evidence_runs(id) ON DELETE CASCADE,
                    step_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    UNIQUE(evidence_run_id, step_index)
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_steps_run
                    ON evidence_steps(evidence_run_id, step_index);
                CREATE TABLE IF NOT EXISTS evidence_manifests(
                    id TEXT PRIMARY KEY,
                    evidence_run_id TEXT NOT NULL UNIQUE REFERENCES evidence_runs(id) ON DELETE CASCADE,
                    publishable INTEGER NOT NULL,
                    body_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS report_patches(
                    id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_report_patches_report
                    ON report_patches(report_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS report_annotations(
                    id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_report_annotations_report
                    ON report_annotations(report_id, created_at DESC);
                """
            )
            previous = conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            previous_version = int(previous["value"]) if previous else 0
            if previous_version < 6:
                for row in conn.execute("SELECT body_json FROM reports").fetchall():
                    self._replace_design_graph(
                        conn,
                        GameReport.model_validate_json(row["body_json"]),
                    )
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _replace_design_graph(conn: sqlite3.Connection, report: GameReport) -> None:
        conn.execute("DELETE FROM design_relations WHERE report_id=?", (report.id,))
        conn.execute("DELETE FROM design_objects WHERE report_id=?", (report.id,))
        object_rows = design_object_rows(report)
        conn.executemany(
            """INSERT INTO design_objects(
                   object_type,object_id,report_id,body_sha256,body_json,updated_at
               ) VALUES(?,?,?,?,?,?)""",
            [
                (
                    object_type,
                    object_id,
                    report.id,
                    hashlib.sha256(object_json.encode("utf-8")).hexdigest(),
                    object_json,
                    report.updated_at,
                )
                for object_type, object_id, body in object_rows
                for object_json in [json.dumps(body, ensure_ascii=False, sort_keys=True)]
            ],
        )
        conn.executemany(
            """INSERT INTO design_relations(
                   report_id,src_type,src_id,relation,dst_type,dst_id
               ) VALUES(?,?,?,?,?,?)""",
            [
                (report.id, src_type, src_id, relation, dst_type, dst_id)
                for src_type, src_id, relation, dst_type, dst_id in design_relation_rows(
                    report
                )
            ],
        )

    def upsert_report(self, report: GameReport) -> None:
        if report.status == "published":
            report.assert_publishable()
            assert_public_artifacts(report)
        else:
            report.assert_storable()
        body = report.model_dump(mode="json")
        body_json = json.dumps(body, ensure_ascii=False, sort_keys=True)
        body_sha = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO reports(id, slug, game_id, system_id, title, summary, status,
                                         body_json, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     slug=excluded.slug, game_id=excluded.game_id, system_id=excluded.system_id,
                     title=excluded.title, summary=excluded.summary, status=excluded.status,
                     body_json=excluded.body_json, updated_at=excluded.updated_at""",
                (
                    report.id,
                    report.slug,
                    report.game_id,
                    report.system_id,
                    report.system_title,
                    report.summary,
                    report.status,
                    body_json,
                    report.created_at,
                    report.updated_at,
                ),
            )
            exists = conn.execute(
                "SELECT 1 FROM report_revisions WHERE report_id=? AND body_sha256=?",
                (report.id, body_sha),
            ).fetchone()
            if not exists:
                next_revision = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(revision), 0) + 1 FROM report_revisions WHERE report_id=?",
                        (report.id,),
                    ).fetchone()[0]
                )
                conn.execute(
                    """INSERT INTO report_revisions(report_id,revision,body_sha256,body_json,created_at)
                       VALUES(?,?,?,?,?)""",
                    (report.id, next_revision, body_sha, body_json, utc_now()),
                )
            self._replace_design_graph(conn, report)
            conn.execute("DELETE FROM report_tags WHERE report_id=?", (report.id,))
            conn.executemany(
                "INSERT INTO report_tags(report_id, tag) VALUES(?,?)",
                [(report.id, tag) for tag in report.tags],
            )
            conn.executemany(
                """INSERT OR REPLACE INTO artifacts(
                       id, run_id, kind, path, sha256, body_json, created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                [
                    (
                        artifact.id,
                        artifact.run_id,
                        artifact.kind,
                        artifact.path,
                        artifact.sha256,
                        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False),
                        utc_now(),
                    )
                    for artifact in report.artifacts
                ],
            )
            conn.execute("DELETE FROM sources WHERE report_id=?", (report.id,))
            conn.executemany(
                """INSERT INTO sources(id, report_id, kind, title, url, locator, public, body_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                [
                    (
                        source.id,
                        report.id,
                        source.kind.value,
                        source.title,
                        source.url,
                        source.locator,
                        1 if source.public else 0,
                        json.dumps(source.model_dump(mode="json"), ensure_ascii=False),
                    )
                    for source in report.sources
                ],
            )

    def get_report(self, report_id_or_slug: str, *, include_drafts: bool = True) -> GameReport | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body_json, status FROM reports WHERE id=? OR slug=?",
                (report_id_or_slug, report_id_or_slug),
            ).fetchone()
        if not row or (not include_drafts and row["status"] != "published"):
            return None
        return GameReport.model_validate_json(row["body_json"])

    def list_reports(
        self,
        *,
        query: str = "",
        tag: str = "",
        game_id: str = "",
        include_drafts: bool = False,
    ) -> list[GameReport]:
        clauses: list[str] = []
        args: list[Any] = []
        if not include_drafts:
            clauses.append("r.status='published'")
        if query:
            clauses.append("(lower(r.title) LIKE ? OR lower(r.summary) LIKE ? OR lower(r.body_json) LIKE ?)")
            needle = f"%{query.lower()}%"
            args.extend([needle, needle, needle])
        if game_id:
            clauses.append("r.game_id=?")
            args.append(game_id)
        if tag:
            clauses.append("EXISTS(SELECT 1 FROM report_tags t WHERE t.report_id=r.id AND t.tag=?)")
            args.append(tag.lower())
        sql = "SELECT r.body_json FROM reports r"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY r.updated_at DESC, r.id"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [GameReport.model_validate_json(row["body_json"]) for row in rows]

    def list_tags(self, *, include_drafts: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT t.tag, COUNT(*) AS count FROM report_tags t"
        if not include_drafts:
            sql += " JOIN reports r ON r.id=t.report_id WHERE r.status='published'"
        sql += " GROUP BY t.tag ORDER BY count DESC, t.tag"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [{"tag": row["tag"], "count": row["count"]} for row in rows]

    def list_design_objects(
        self,
        *,
        report_id: str = "",
        object_type: str = "",
        query: str = "",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if report_id:
            clauses.append("report_id=?")
            args.append(report_id)
        if object_type:
            clauses.append("object_type=?")
            args.append(object_type)
        if query:
            clauses.append("lower(body_json) LIKE ?")
            args.append(f"%{query.lower()}%")
        sql = (
            "SELECT object_type,object_id,report_id,body_sha256,body_json,updated_at "
            "FROM design_objects"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY object_type, object_id"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._design_object_payload(row) for row in rows]

    def get_design_object(
        self,
        object_id: str,
        *,
        object_type: str = "",
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT object_type,object_id,report_id,body_sha256,body_json,updated_at "
            "FROM design_objects WHERE object_id=?"
        )
        args: list[Any] = [object_id]
        if object_type:
            sql += " AND object_type=?"
            args.append(object_type)
        sql += " ORDER BY object_type"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(
                f"design object id is ambiguous; specify object_type: {object_id}"
            )
        return self._design_object_payload(rows[0])

    @staticmethod
    def _design_object_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "object_type": row["object_type"],
            "object_id": row["object_id"],
            "report_id": row["report_id"],
            "sha256": row["body_sha256"],
            "updated_at": row["updated_at"],
            "body": json.loads(row["body_json"]),
        }

    def list_design_relations(
        self,
        *,
        report_id: str = "",
        src_id: str = "",
        dst_id: str = "",
        relation: str = "",
    ) -> list[dict[str, str]]:
        clauses: list[str] = []
        args: list[Any] = []
        for column, value in (
            ("report_id", report_id),
            ("src_id", src_id),
            ("dst_id", dst_id),
            ("relation", relation),
        ):
            if value:
                clauses.append(f"{column}=?")
                args.append(value)
        sql = (
            "SELECT report_id,src_type,src_id,relation,dst_type,dst_id "
            "FROM design_relations"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY src_type, src_id, relation, dst_type, dst_id"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def list_revisions(self, report_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT revision,body_sha256,created_at FROM report_revisions
                   WHERE report_id=? ORDER BY revision DESC""",
                (report_id,),
            ).fetchall()
        return [
            {
                "revision": int(row["revision"]),
                "sha256": row["body_sha256"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_revision(self, report_id: str, revision: int) -> GameReport | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body_json FROM report_revisions WHERE report_id=? AND revision=?",
                (report_id, revision),
            ).fetchone()
        return GameReport.model_validate_json(row["body_json"]) if row else None

    def current_revision(self, report_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM report_revisions WHERE report_id=?",
                (report_id,),
            ).fetchone()
        return int(row[0])

    def save_run(self, run: RunResult) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO runs(id, adapter, target_id, task_id, status, body_json, started_at, ended_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                     body_json=excluded.body_json, ended_at=excluded.ended_at""",
                (
                    run.id,
                    run.adapter,
                    run.target_id,
                    run.task_id,
                    run.status,
                    run.model_dump_json(),
                    run.started_at,
                    run.ended_at,
                ),
            )

    def get_run(self, run_id: str) -> RunResult | None:
        with self._connect() as conn:
            row = conn.execute("SELECT body_json FROM runs WHERE id=?", (run_id,)).fetchone()
        return RunResult.model_validate_json(row["body_json"]) if row else None

    def save_artifact(self, artifact: ArtifactRef) -> None:
        source = Path(artifact.path)
        if not source.is_absolute():
            source = self.root / source
        cas_object = None
        if source.is_file():
            cas_object = self.cas.put_file(source, expected_sha256=artifact.sha256)
            try:
                source.resolve().relative_to(self.root)
                source.resolve().relative_to(self.cas.root)
            except ValueError:
                try:
                    source.resolve().relative_to(self.root)
                except ValueError:
                    pass
                else:
                    self.cas.replace_with_compatibility_link(
                        source,
                        expected_sha256=artifact.sha256,
                    )
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO artifacts(id, run_id, kind, path, sha256, body_json, created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    artifact.id,
                    artifact.run_id,
                    artifact.kind,
                    artifact.path,
                    artifact.sha256,
                    artifact.model_dump_json(),
                    utc_now(),
                ),
            )
            if cas_object is not None:
                existing = conn.execute(
                    "SELECT sha256 FROM artifact_cas_refs WHERE artifact_id=?",
                    (artifact.id,),
                ).fetchone()
                if existing is not None and str(existing["sha256"]) != cas_object.sha256:
                    raise ValueError("artifact id cannot be rebound to another CAS object")
                now = utc_now()
                conn.execute(
                    """
                    INSERT OR IGNORE INTO cas_objects(sha256, path, bytes, created_at)
                    VALUES(?,?,?,?)
                    """,
                    (cas_object.sha256, cas_object.path, cas_object.bytes, now),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO artifact_cas_refs(
                        artifact_id, sha256, source_path, created_at
                    ) VALUES(?,?,?,?)
                    """,
                    (artifact.id, cas_object.sha256, artifact.path, now),
                )

    def register_artifact_cas_ref(self, artifact: ArtifactRef, cas_object: Any) -> None:
        """Idempotently project one immutable artifact onto its CAS object."""

        if cas_object.sha256 != artifact.sha256.lower():
            raise ValueError("artifact and CAS object hashes differ")
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT sha256 FROM artifact_cas_refs WHERE artifact_id=?",
                (artifact.id,),
            ).fetchone()
            if existing is not None and str(existing["sha256"]) != cas_object.sha256:
                raise ValueError("artifact id cannot be rebound to another CAS object")
            now = utc_now()
            conn.execute(
                """
                INSERT OR IGNORE INTO cas_objects(sha256, path, bytes, created_at)
                VALUES(?,?,?,?)
                """,
                (cas_object.sha256, cas_object.path, cas_object.bytes, now),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO artifact_cas_refs(
                    artifact_id, sha256, source_path, created_at
                ) VALUES(?,?,?,?)
                """,
                (artifact.id, cas_object.sha256, artifact.path, now),
            )

    def get_artifact_cas_ref(self, artifact_id: str) -> dict[str, Any] | None:
        """Return the CAS projection without changing the immutable ArtifactRef body."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT ref.artifact_id,ref.sha256,ref.source_path,object.path,object.bytes
                FROM artifact_cas_refs AS ref
                JOIN cas_objects AS object ON object.sha256=ref.sha256
                WHERE ref.artifact_id=?
                """,
                (artifact_id,),
            ).fetchone()
        return dict(row) if row else None

    def cas_migration_preview(self):
        """Describe a non-destructive historical artifact migration without copying bytes."""

        from .content_addressed_store import ContentAddressedObjectStore

        return ContentAddressedObjectStore.preview(
            self.list_artifacts(),
            canonical_root=self.root,
        )

    def get_artifact(self, artifact_id: str) -> ArtifactRef | None:
        with self._connect() as conn:
            row = conn.execute("SELECT body_json FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return ArtifactRef.model_validate_json(row["body_json"]) if row else None

    def get_artifacts(self, artifact_ids: Iterable[str]) -> dict[str, ArtifactRef]:
        """Load selected artifacts with bounded queries instead of one connection per id."""

        ids = list(dict.fromkeys(str(value) for value in artifact_ids if str(value).strip()))
        if not ids:
            return {}
        rows: list[sqlite3.Row] = []
        with self._connect() as conn:
            for offset in range(0, len(ids), 900):
                chunk = ids[offset : offset + 900]
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"SELECT body_json FROM artifacts WHERE id IN ({placeholders})",
                        chunk,
                    ).fetchall()
                )
        artifacts = [ArtifactRef.model_validate_json(row["body_json"]) for row in rows]
        return {artifact.id: artifact for artifact in artifacts}

    def list_artifacts(self) -> list[ArtifactRef]:
        with self._connect() as conn:
            rows = conn.execute("SELECT body_json FROM artifacts ORDER BY created_at,id").fetchall()
        return [ArtifactRef.model_validate_json(row["body_json"]) for row in rows]

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO trace_events(run_id,event_type,timestamp,payload_json) VALUES(?,?,?,?)",
                (run_id, event_type, utc_now(), json.dumps(payload, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def list_trace_events(
        self,
        run_id: str | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT seq,run_id,event_type,timestamp,payload_json FROM trace_events"
        params: list[Any] = []
        if run_id:
            query += " WHERE run_id=?"
            params.append(run_id)
        query += " ORDER BY seq"
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(1, limit))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": int(row["seq"]),
                "run_id": row["run_id"],
                "event_type": row["event_type"],
                "timestamp": row["timestamp"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def save_source_snapshot(self, snapshot: SourceSnapshot) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO source_snapshots(
                       id,source_id,content_sha256,locator,status,body_json,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
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
            return cursor.rowcount > 0

    def list_source_snapshots(self, source_id: str | None = None) -> list[SourceSnapshot]:
        query = "SELECT body_json FROM source_snapshots"
        params: tuple[Any, ...] = ()
        if source_id:
            query += " WHERE source_id=?"
            params = (source_id,)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [SourceSnapshot.model_validate_json(row["body_json"]) for row in rows]

    def retract_source_snapshots(self, source_id: str, reason: str, retracted_at: str) -> int:
        snapshots = self.list_source_snapshots(source_id)
        updated = 0
        with self._lock, self._connect() as conn:
            for snapshot in snapshots:
                if snapshot.status == "retracted":
                    continue
                value = snapshot.model_copy(
                    update={
                        "status": "retracted",
                        "metadata": {
                            **snapshot.metadata,
                            "retraction_reason": reason,
                            "retracted_at": retracted_at,
                        },
                    }
                )
                conn.execute(
                    "UPDATE source_snapshots SET status=?,body_json=? WHERE id=?",
                    ("retracted", value.model_dump_json(), value.id),
                )
                updated += 1
        return updated

    def save_voice_record(self, record: VoiceRecord) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO voice_entries(
                       id,report_id,source_id,fingerprint,status,body_json,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    record.id,
                    record.report_id,
                    record.voice.source_id,
                    record.fingerprint,
                    record.status,
                    record.model_dump_json(),
                    record.created_at,
                ),
            )
            return cursor.rowcount > 0

    def update_voice_record(self, record: VoiceRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE voice_entries SET status=?,body_json=? WHERE id=?",
                (record.status, record.model_dump_json(), record.id),
            )

    def list_voice_records(self, report_id: str | None = None) -> list[VoiceRecord]:
        query = "SELECT body_json FROM voice_entries"
        params: tuple[Any, ...] = ()
        if report_id:
            query += " WHERE report_id=?"
            params = (report_id,)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [VoiceRecord.model_validate_json(row["body_json"]) for row in rows]

    def retract_voice_records(self, source_id: str, reason: str, retracted_at: str) -> int:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id,body_json FROM voice_entries WHERE source_id=? AND status='active'",
                (source_id,),
            ).fetchall()
            for row in rows:
                record = VoiceRecord.model_validate_json(row["body_json"])
                voice = record.voice.model_copy(
                    update={
                        "status": "retracted",
                        "retracted_at": retracted_at,
                        "retraction_reason": reason,
                    }
                )
                value = record.model_copy(update={"status": "retracted", "voice": voice})
                conn.execute(
                    "UPDATE voice_entries SET status='retracted',body_json=? WHERE id=?",
                    (value.model_dump_json(), row["id"]),
                )
            return len(rows)

    def upsert_target(self, target: TargetRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO target_registry(id,provider,endpoint,kind,status,body_json,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET provider=excluded.provider,
                     endpoint=excluded.endpoint,kind=excluded.kind,status=excluded.status,
                     body_json=excluded.body_json,updated_at=excluded.updated_at""",
                (
                    target.id,
                    target.provider,
                    target.endpoint,
                    target.kind,
                    target.status,
                    target.model_dump_json(),
                    utc_now(),
                ),
            )

    def list_targets(self) -> list[TargetRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT body_json FROM target_registry ORDER BY provider,id"
            ).fetchall()
        return [TargetRecord.model_validate_json(row["body_json"]) for row in rows]

    def get_target(self, target_id: str) -> TargetRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body_json FROM target_registry WHERE id=?", (target_id,)
            ).fetchone()
        return TargetRecord.model_validate_json(row["body_json"]) if row else None

    def save_lease(self, lease: DeviceLease) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO device_leases(
                       id,target_id,holder,token,status,expires_at,body_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                     expires_at=excluded.expires_at,body_json=excluded.body_json""",
                (
                    lease.id,
                    lease.target_id,
                    lease.holder,
                    lease.token,
                    lease.status,
                    lease.expires_at,
                    lease.model_dump_json(),
                    lease.acquired_at,
                ),
            )

    def list_leases(
        self,
        target_id: str | None = None,
        *,
        status: str | None = None,
        expires_at_or_before: str | None = None,
    ) -> list[DeviceLease]:
        query = "SELECT body_json FROM device_leases"
        conditions: list[str] = []
        params: list[Any] = []
        if target_id:
            conditions.append("target_id=?")
            params.append(target_id)
        if status:
            conditions.append("status=?")
            params.append(status)
        if expires_at_or_before:
            conditions.append("julianday(expires_at) <= julianday(?)")
            params.append(expires_at_or_before)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [DeviceLease.model_validate_json(row["body_json"]) for row in rows]

    def get_lease_by_token(self, token: str) -> DeviceLease | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body_json FROM device_leases WHERE token=?", (token,)
            ).fetchone()
        return DeviceLease.model_validate_json(row["body_json"]) if row else None

    def append_gateway_event(
        self, event_type: str, target_id: str | None, payload: dict[str, Any]
    ) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO gateway_events(event_type,target_id,timestamp,payload_json)
                   VALUES(?,?,?,?)""",
                (event_type, target_id, utc_now(), json.dumps(payload, ensure_ascii=False)),
            )
            return int(cursor.lastrowid)

    def list_gateway_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT seq,event_type,target_id,timestamp,payload_json FROM gateway_events
                   ORDER BY seq DESC LIMIT ?""",
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return [
            {
                "seq": int(row["seq"]),
                "event_type": row["event_type"],
                "target_id": row["target_id"],
                "timestamp": row["timestamp"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def recent_gateway_events(
        self,
        target_id: str,
        event_type: str,
        since: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT seq,event_type,target_id,timestamp,payload_json FROM gateway_events
                   WHERE target_id=? AND event_type=? AND timestamp>=?
                   ORDER BY seq DESC LIMIT ?""",
                (target_id, event_type, since, max(1, min(limit, 5000))),
            ).fetchall()
        return [
            {
                "seq": int(row["seq"]),
                "event_type": row["event_type"],
                "target_id": row["target_id"],
                "timestamp": row["timestamp"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def save_gateway_control(self, control: GatewayControl) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO gateway_controls(target_id,emergency_stopped,body_json,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(target_id) DO UPDATE SET
                     emergency_stopped=excluded.emergency_stopped,
                     body_json=excluded.body_json, updated_at=excluded.updated_at""",
                (
                    control.target_id,
                    1 if control.emergency_stopped else 0,
                    control.model_dump_json(),
                    control.updated_at,
                ),
            )

    def get_gateway_control(self, target_id: str) -> GatewayControl | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body_json FROM gateway_controls WHERE target_id=?", (target_id,)
            ).fetchone()
        return GatewayControl.model_validate_json(row["body_json"]) if row else None

    def save_capture_session(self, session: CaptureSession) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO capture_sessions(id,target_id,status,body_json,started_at,ended_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                     body_json=excluded.body_json, ended_at=excluded.ended_at""",
                (
                    session.id,
                    session.target_id,
                    session.status,
                    session.model_dump_json(),
                    session.started_at,
                    session.ended_at,
                ),
            )

    def list_capture_sessions(
        self, target_id: str | None = None, *, limit: int = 100
    ) -> list[CaptureSession]:
        query = "SELECT body_json FROM capture_sessions"
        params: list[Any] = []
        if target_id:
            query += " WHERE target_id=?"
            params.append(target_id)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [CaptureSession.model_validate_json(row["body_json"]) for row in rows]

    def save_evidence_run(self, run: EvidenceRun) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO evidence_runs(id,target_id,status,body_json,started_at,ended_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                     body_json=excluded.body_json, ended_at=excluded.ended_at""",
                (
                    run.id,
                    run.target_id,
                    run.status,
                    run.model_dump_json(),
                    run.started_at,
                    run.ended_at,
                ),
            )

    def get_evidence_run(self, evidence_run_id: str) -> EvidenceRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body_json FROM evidence_runs WHERE id=?", (evidence_run_id,)
            ).fetchone()
        return EvidenceRun.model_validate_json(row["body_json"]) if row else None

    def get_evidence_runs(self, evidence_run_ids: Iterable[str]) -> dict[str, EvidenceRun]:
        """Load selected evidence runs in bounded batches."""

        ids = list(
            dict.fromkeys(str(value) for value in evidence_run_ids if str(value).strip())
        )
        if not ids:
            return {}
        rows: list[sqlite3.Row] = []
        with self._connect() as conn:
            for offset in range(0, len(ids), 900):
                chunk = ids[offset : offset + 900]
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"SELECT body_json FROM evidence_runs WHERE id IN ({placeholders})",
                        chunk,
                    ).fetchall()
                )
        runs = [EvidenceRun.model_validate_json(row["body_json"]) for row in rows]
        return {run.id: run for run in runs}

    def list_evidence_runs(
        self,
        target_id: str | None = None,
        *,
        limit: int = 100,
        scope_id: str | None = None,
        environment_id: str | None = None,
        ai_player_session_id: str | None = None,
    ) -> list[EvidenceRun]:
        query = "SELECT body_json FROM evidence_runs"
        params: list[Any] = []
        filters: list[str] = []
        uses_json_filter = False
        if target_id:
            filters.append("target_id=?")
            params.append(target_id)
        if scope_id:
            uses_json_filter = True
            filters.append("json_extract(body_json, '$.scope_id')=?")
            params.append(scope_id)
        if environment_id:
            uses_json_filter = True
            filters.append("json_extract(body_json, '$.environment.environment_id')=?")
            params.append(environment_id)
        if ai_player_session_id:
            uses_json_filter = True
            filters.append(
                "json_extract(body_json, '$.environment.ai_player_session_id')=?"
            )
            params.append(ai_player_session_id)
        if filters:
            if uses_json_filter:
                filters.insert(0, "json_valid(body_json)")
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [EvidenceRun.model_validate_json(row["body_json"]) for row in rows]

    def create_evidence_step(
        self,
        evidence_run_id: str,
        factory: Callable[[int], EvidenceStep],
    ) -> EvidenceStep:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(step_index), 0) FROM evidence_steps WHERE evidence_run_id=?",
                (evidence_run_id,),
            ).fetchone()
            step = factory(int(row[0]) + 1)
            if step.evidence_run_id != evidence_run_id:
                raise ValueError("evidence step factory returned the wrong evidence_run_id")
            conn.execute(
                """INSERT INTO evidence_steps(
                       id,evidence_run_id,step_index,status,body_json,started_at,ended_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    step.id,
                    step.evidence_run_id,
                    step.step_index,
                    step.status,
                    step.model_dump_json(),
                    step.started_at,
                    step.ended_at,
                ),
            )
        return step

    def save_evidence_step(self, step: EvidenceStep) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO evidence_steps(
                       id,evidence_run_id,step_index,status,body_json,started_at,ended_at
                   ) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                     body_json=excluded.body_json, ended_at=excluded.ended_at""",
                (
                    step.id,
                    step.evidence_run_id,
                    step.step_index,
                    step.status,
                    step.model_dump_json(),
                    step.started_at,
                    step.ended_at,
                ),
            )

    def get_evidence_step(self, step_id: str) -> EvidenceStep | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body_json FROM evidence_steps WHERE id=?", (step_id,)
            ).fetchone()
        return EvidenceStep.model_validate_json(row["body_json"]) if row else None

    def get_evidence_steps(self, step_ids: Iterable[str]) -> dict[str, EvidenceStep]:
        """Load selected evidence steps in bounded batches."""

        ids = list(dict.fromkeys(str(value) for value in step_ids if str(value).strip()))
        if not ids:
            return {}
        rows: list[sqlite3.Row] = []
        with self._connect() as conn:
            for offset in range(0, len(ids), 900):
                chunk = ids[offset : offset + 900]
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        f"SELECT body_json FROM evidence_steps WHERE id IN ({placeholders})",
                        chunk,
                    ).fetchall()
                )
        steps = [EvidenceStep.model_validate_json(row["body_json"]) for row in rows]
        return {step.id: step for step in steps}

    def count_evidence_steps_by_run_ids(
        self, evidence_run_ids: Iterable[str]
    ) -> dict[str, int]:
        """Count steps for selected runs without materializing every step payload."""

        ids = list(
            dict.fromkeys(str(value) for value in evidence_run_ids if str(value).strip())
        )
        if not ids:
            return {}
        counts: dict[str, int] = {}
        with self._connect() as conn:
            for offset in range(0, len(ids), 900):
                chunk = ids[offset : offset + 900]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    "SELECT evidence_run_id,COUNT(*) AS step_count "
                    f"FROM evidence_steps WHERE evidence_run_id IN ({placeholders}) "
                    "GROUP BY evidence_run_id",
                    chunk,
                ).fetchall()
                counts.update(
                    {str(row["evidence_run_id"]): int(row["step_count"]) for row in rows}
                )
        return counts

    def list_evidence_steps(self, evidence_run_id: str) -> list[EvidenceStep]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT body_json FROM evidence_steps
                   WHERE evidence_run_id=? ORDER BY step_index""",
                (evidence_run_id,),
            ).fetchall()
        return [EvidenceStep.model_validate_json(row["body_json"]) for row in rows]

    def save_evidence_manifest(self, manifest: EvidenceRunManifest) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO evidence_manifests(
                       id,evidence_run_id,publishable,body_json,generated_at
                   ) VALUES(?,?,?,?,?)
                   ON CONFLICT(evidence_run_id) DO UPDATE SET id=excluded.id,
                     publishable=excluded.publishable, body_json=excluded.body_json,
                     generated_at=excluded.generated_at""",
                (
                    manifest.id,
                    manifest.evidence_run_id,
                    1 if manifest.publishable else 0,
                    manifest.model_dump_json(),
                    manifest.generated_at,
                ),
            )

    def get_evidence_manifest(self, evidence_run_id: str) -> EvidenceRunManifest | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body_json FROM evidence_manifests WHERE evidence_run_id=?",
                (evidence_run_id,),
            ).fetchone()
        return EvidenceRunManifest.model_validate_json(row["body_json"]) if row else None

    def save_report_patch(self, patch: ReportPatch) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO report_patches(id,report_id,status,body_json,created_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status, body_json=excluded.body_json""",
                (
                    patch.id,
                    patch.report_id,
                    patch.status,
                    patch.model_dump_json(),
                    patch.created_at,
                ),
            )

    def get_report_patch(self, patch_id: str) -> ReportPatch | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body_json FROM report_patches WHERE id=?", (patch_id,)
            ).fetchone()
        return ReportPatch.model_validate_json(row["body_json"]) if row else None

    def list_report_patches(self, report_id: str | None = None) -> list[ReportPatch]:
        query = "SELECT body_json FROM report_patches"
        params: list[Any] = []
        if report_id:
            query += " WHERE report_id=?"
            params.append(report_id)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [ReportPatch.model_validate_json(row["body_json"]) for row in rows]

    def save_report_annotation(self, annotation: ReportAnnotation) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO report_annotations(id,report_id,object_id,status,body_json,created_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status, body_json=excluded.body_json""",
                (
                    annotation.id,
                    annotation.report_id,
                    annotation.object_id,
                    annotation.status,
                    annotation.model_dump_json(),
                    annotation.created_at,
                ),
            )

    def get_report_annotation(self, annotation_id: str) -> ReportAnnotation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body_json FROM report_annotations WHERE id=?", (annotation_id,)
            ).fetchone()
        return ReportAnnotation.model_validate_json(row["body_json"]) if row else None

    def list_report_annotations(self, report_id: str | None = None) -> list[ReportAnnotation]:
        query = "SELECT body_json FROM report_annotations"
        params: list[Any] = []
        if report_id:
            query += " WHERE report_id=?"
            params.append(report_id)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [ReportAnnotation.model_validate_json(row["body_json"]) for row in rows]

    def list_runs(self, limit: int = 50) -> list[RunResult]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT body_json FROM runs ORDER BY started_at DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [RunResult.model_validate_json(row["body_json"]) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            return {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in (
                    "reports", "report_revisions", "design_objects", "design_relations",
                    "sources", "runs", "artifacts", "trace_events",
                    "source_snapshots", "voice_entries",
                    "target_registry", "device_leases", "gateway_events", "gateway_controls",
                    "capture_sessions",
                    "evidence_runs", "evidence_steps", "evidence_manifests",
                    "report_patches", "report_annotations",
                )
            }

    def export_reports(self, reports: Iterable[GameReport] | None = None) -> Path:
        payload = [item.model_dump(mode="json") for item in (reports or self.list_reports())]
        path = self.export_root / "catalog.json"
        path.write_text(json.dumps({"reports": payload}, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
