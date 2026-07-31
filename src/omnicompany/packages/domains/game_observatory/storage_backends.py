from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        rows: list[tuple[str, str, str | None, dict[str, Any]]] = []
        reports = store.list_reports(include_drafts=True)
        for report in reports:
            rows.append(("report", report.id, report.id, report.model_dump(mode="json")))
            for source in report.sources:
                rows.append(("source", source.id, report.id, source.model_dump(mode="json")))
            for flow in report.flow:
                rows.append(("flow", flow.id, report.id, flow.model_dump(mode="json")))
            for claim in [*report.claims, *report.interpretations]:
                rows.append(("claim", claim.id, report.id, claim.model_dump(mode="json")))
        for artifact in store.list_artifacts():
            rows.append(("artifact", artifact.id, None, artifact.model_dump(mode="json")))
        for run in store.list_runs(100_000):
            rows.append(("run", run.id, None, run.model_dump(mode="json")))
        for snapshot in store.list_source_snapshots():
            rows.append(("source_snapshot", snapshot.id, None, snapshot.model_dump(mode="json")))
        for voice in store.list_voice_records():
            rows.append(("voice_record", voice.id, voice.report_id, voice.model_dump(mode="json")))
        for target in store.list_targets():
            rows.append(("target", target.id, None, target.model_dump(mode="json")))
        for session in store.list_capture_sessions(limit=100_000):
            rows.append(("capture_session", session.id, None, session.model_dump(mode="json")))
        for patch in store.list_report_patches():
            rows.append(("report_patch", patch.id, patch.report_id, patch.model_dump(mode="json")))
        for annotation in store.list_report_annotations():
            rows.append(
                ("report_annotation", annotation.id, annotation.report_id, annotation.model_dump(mode="json"))
            )
        for event in store.list_trace_events():
            rows.append(("trace_event", str(event["id"]), None, event))
        return rows

    @staticmethod
    def _relations(store: ObservatoryStore) -> list[tuple[str, str, str, str, str, dict[str, Any]]]:
        values: set[tuple[str, str, str, str, str, str]] = set()
        for report in store.list_reports(include_drafts=True):
            for source in report.sources:
                values.add(("report", report.id, "has_source", "source", source.id, "{}"))
            for artifact in report.artifacts:
                values.add(("report", report.id, "has_artifact", "artifact", artifact.id, "{}"))
            for run in report.runs:
                values.add(("report", report.id, "has_run", "run", run.id, "{}"))
            for tag in report.tags:
                values.add(("report", report.id, "tagged", "tag", tag, "{}"))
            for flow in report.flow:
                values.add(("report", report.id, "has_flow", "flow", flow.id, "{}"))
                for source_id in flow.source_ids:
                    values.add(("flow", flow.id, "provenance", "source", source_id, "{}"))
            for claim in [*report.claims, *report.interpretations]:
                values.add(("report", report.id, "has_claim", "claim", claim.id, "{}"))
                for source_id in claim.source_ids:
                    values.add(("claim", claim.id, "provenance", "source", source_id, "{}"))
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
            "counts": counts,
            "relation_count": relation_count,
            "ok": True,
        }
