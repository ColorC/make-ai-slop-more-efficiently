from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .compiler import SemanticReportCompiler
from .models import (
    CommunityFeedbackItem,
    ContentKind,
    PlayerVoice,
    SourceRef,
    SourceSnapshot,
    VoiceRecord,
    utc_now,
)
from .store import ObservatoryStore


class SourceVoicePipeline:
    """Versioned source and player-voice ingestion with retraction support."""

    def __init__(self, store: ObservatoryStore) -> None:
        self.store = store

    @staticmethod
    def _digest(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().casefold()

    @staticmethod
    def _source_semantic(source: SourceRef) -> dict[str, Any]:
        payload = source.model_dump(mode="json")
        payload.pop("captured_at", None)
        return payload

    def _voice_fingerprint(self, source: SourceRef, voice: PlayerVoice) -> str:
        return self._digest(
            {
                "url": source.url.strip(),
                "locator": source.locator,
                "summary": self._normalize_text(voice.summary),
                "quote": self._normalize_text(voice.quote or ""),
                "theme": voice.theme.strip().casefold(),
            }
        )

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("source acquisition requires a public http(s) URL")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, port)}
        except OSError as exc:
            raise ValueError(f"source hostname cannot be resolved: {parsed.hostname}") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError(f"source acquisition refuses non-public address: {address}")

    @classmethod
    def acquire_public_source(
        cls,
        url: str,
        *,
        max_bytes: int = 2_000_000,
    ) -> dict[str, Any]:
        cls._validate_public_url(url)
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 game-observatory/0.2 public-source-research",
                "Accept": "text/html,application/json;q=0.9,text/plain;q=0.8",
            },
        )
        with urlopen(request, timeout=20) as response:  # noqa: S310 - URL is public-only validated
            final_url = response.geturl()
            cls._validate_public_url(final_url)
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError(f"source response exceeds {max_bytes} bytes")
            content_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"
            status = int(getattr(response, "status", 200))

        class TitleParser(HTMLParser):
            title = ""
            in_title = False

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if tag.lower() == "title":
                    self.in_title = True

            def handle_endtag(self, tag: str) -> None:
                if tag.lower() == "title":
                    self.in_title = False

            def handle_data(self, data: str) -> None:
                if self.in_title:
                    self.title += data

        title = ""
        if content_type == "text/html":
            parser = TitleParser()
            parser.feed(raw.decode(charset, "replace"))
            title = re.sub(r"\s+", " ", parser.title).strip()
        return {
            "requested_url": url,
            "final_url": final_url,
            "status": status,
            "content_type": content_type,
            "bytes": len(raw),
            "content_sha256": hashlib.sha256(raw).hexdigest(),
            "page_title": title,
            "captured_at": utc_now(),
        }

    def acquire_and_ingest_source(
        self,
        report_id: str,
        source: SourceRef,
        *,
        excerpt: str | None = None,
        acquisition_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        acquired = self.acquire_public_source(acquisition_url or source.url)
        result = self.ingest_source(
            report_id,
            source.model_copy(
                update={
                    "captured_at": acquired["captured_at"],
                    "resolved_url": acquired["final_url"],
                    "content_sha256": acquired["content_sha256"],
                    "content_type": acquired["content_type"],
                    "content_bytes": acquired["bytes"],
                }
            ),
            excerpt=excerpt,
            metadata={**(metadata or {}), "acquisition": acquired},
        )
        result["acquisition"] = acquired
        return result

    def backfill_existing(self) -> dict[str, int]:
        snapshots = 0
        voices = 0
        for report in self.store.list_reports(include_drafts=True):
            sources = {item.id: item for item in report.sources}
            for source in report.sources:
                identity = self._source_semantic(source)
                for transient in ("retracted_at", "retraction_reason"):
                    identity.pop(transient, None)
                digest = self._digest({"source": identity, "excerpt": None, "locator": source.locator})
                snapshot = SourceSnapshot(
                    id=f"snapshot.source.{digest[:24]}",
                    source_id=source.id,
                    content_sha256=digest,
                    locator=source.locator,
                    captured_at=source.captured_at,
                    status=source.status,
                    metadata={"backfilled_from_report": report.id},
                )
                snapshots += int(self.store.save_source_snapshot(snapshot))
            for voice in report.player_voices:
                source = sources.get(voice.source_id)
                if not source:
                    continue
                fingerprint = self._voice_fingerprint(source, voice)
                record = VoiceRecord(
                    id=f"voice.record.{fingerprint[:24]}",
                    report_id=report.id,
                    fingerprint=fingerprint,
                    voice=voice,
                    status=voice.status,
                )
                voices += int(self.store.save_voice_record(record))
        return {"source_snapshots_created": snapshots, "voice_records_created": voices}

    def _compile(self) -> None:
        reports = self.store.list_reports()
        self.store.export_reports(reports)
        SemanticReportCompiler(self.store.export_root / "public").compile(reports)

    def ingest_source(
        self,
        report_id: str,
        source: SourceRef,
        *,
        excerpt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        report = self.store.get_report(report_id)
        if not report:
            raise ValueError(f"report not found: {report_id}")
        source = source.model_copy(update={"status": "active", "retracted_at": None, "retraction_reason": None})
        source_identity = self._source_semantic(source)
        for transient in ("retracted_at", "retraction_reason"):
            source_identity.pop(transient, None)
        digest = self._digest(
            {
                "source": source_identity,
                "excerpt": excerpt,
                "locator": source.locator,
            }
        )
        snapshot = SourceSnapshot(
            id=f"snapshot.source.{digest[:24]}",
            source_id=source.id,
            content_sha256=digest,
            locator=source.locator,
            excerpt=excerpt,
            captured_at=source.captured_at,
            metadata=metadata or {},
        )
        snapshot_created = self.store.save_source_snapshot(snapshot)
        existing_index = next((i for i, item in enumerate(report.sources) if item.id == source.id), None)
        report_changed = (
            existing_index is None
            or self._source_semantic(report.sources[existing_index]) != self._source_semantic(source)
        )
        if existing_index is None:
            report.sources.append(source)
        elif report_changed:
            report.sources[existing_index] = source
        if report_changed:
            report.updated_at = utc_now()
            self.store.upsert_report(report)
            self._compile()
        return {
            "ok": True,
            "report_id": report.id,
            "source_id": source.id,
            "snapshot_id": snapshot.id,
            "snapshot_created": snapshot_created,
            "report_changed": report_changed,
            "deduplicated": not snapshot_created and not report_changed,
        }

    def ingest_player_voice(
        self,
        report_id: str,
        source: SourceRef,
        voice: PlayerVoice,
        *,
        excerpt: str | None = None,
    ) -> dict[str, Any]:
        if source.kind != ContentKind.player_voice:
            raise ValueError("player voice source kind must be player_voice")
        if voice.source_id != source.id:
            raise ValueError("voice.source_id must match source.id")
        if voice.quote:
            if len(voice.quote) > 240 or len(voice.quote.split()) > 40:
                raise ValueError("player voice quote must be a necessary short excerpt")
            if excerpt and self._normalize_text(voice.quote) not in self._normalize_text(excerpt):
                raise ValueError("voice quote must occur in the preserved excerpt")
        report = self.store.get_report(report_id)
        if not report:
            raise ValueError(f"report not found: {report_id}")
        if voice.system_node_id and voice.system_node_id not in {item.id for item in report.flow}:
            raise ValueError(f"unknown flow node: {voice.system_node_id}")
        fingerprint = self._voice_fingerprint(source, voice)
        existing = next(
            (
                item
                for item in self.store.list_voice_records()
                if item.fingerprint == fingerprint
                and item.status == "active"
                and item.voice.status == "active"
            ),
            None,
        )
        if existing:
            # A report can be rebuilt from an older golden fixture while the append-only
            # voice ledger correctly retains the reviewed, source-bound record.  A
            # duplicate ingest must therefore reconcile the canonical projection instead
            # of returning early and leaving the report stripped of its quote/review data.
            source_result = self.ingest_source(
                report.id,
                source,
                excerpt=excerpt,
                metadata={"use": "player_voice"},
            )
            report = self.store.get_report(report.id)
            assert report is not None
            canonical_voice = existing.voice
            voice_index = next(
                (i for i, item in enumerate(report.player_voices) if item.id == canonical_voice.id),
                None,
            )
            report_changed = voice_index is None or (
                report.player_voices[voice_index].model_dump(mode="json")
                != canonical_voice.model_dump(mode="json")
            )
            if voice_index is None:
                report.player_voices.append(canonical_voice)
            elif report_changed:
                report.player_voices[voice_index] = canonical_voice
            if report_changed:
                report.updated_at = utc_now()
                self.store.upsert_report(report)
                self._compile()
            return {
                "ok": True,
                "report_id": existing.report_id,
                "source_id": source.id,
                "voice_id": existing.voice.id,
                "record_id": existing.id,
                "snapshot_id": source_result["snapshot_id"],
                "deduplicated": True,
                "report_changed": report_changed,
            }
        source_result = self.ingest_source(report.id, source, excerpt=excerpt, metadata={"use": "player_voice"})
        report = self.store.get_report(report.id)
        assert report is not None
        voice = voice.model_copy(
            update={"status": "active", "retracted_at": None, "retraction_reason": None}
        )
        voice_index = next((i for i, item in enumerate(report.player_voices) if item.id == voice.id), None)
        if voice_index is None:
            report.player_voices.append(voice)
        else:
            report.player_voices[voice_index] = voice
        report.updated_at = utc_now()
        self.store.upsert_report(report)
        record = VoiceRecord(
            id=f"voice.record.{fingerprint[:24]}",
            report_id=report.id,
            fingerprint=fingerprint,
            voice=voice,
        )
        self.store.save_voice_record(record)
        self._compile()
        return {
            "ok": True,
            "report_id": report.id,
            "source_id": source.id,
            "voice_id": voice.id,
            "record_id": record.id,
            "snapshot_id": source_result["snapshot_id"],
            "deduplicated": False,
            "report_changed": True,
        }

    def ingest_community_feedback(
        self,
        report_id: str,
        feedback: CommunityFeedbackItem,
        *,
        excerpt: str | None = None,
    ) -> dict[str, Any]:
        report = self.store.get_report(report_id)
        if not report:
            raise ValueError(f"report not found: {report_id}")
        missing_targets = sorted(set(feedback.target_object_ids) - report.design_object_ids())
        if missing_targets:
            raise ValueError(f"community feedback target objects do not resolve: {missing_targets}")
        source_result = self.ingest_source(
            report_id,
            feedback.source,
            excerpt=excerpt,
            metadata={"use": "community_feedback", "feedback_id": feedback.id},
        )
        report = self.store.get_report(report_id)
        assert report is not None
        canonical_source = next(item for item in report.sources if item.id == feedback.source.id)
        feedback = feedback.model_copy(update={"source": canonical_source})
        index = next(
            (i for i, item in enumerate(report.community_feedback) if item.id == feedback.id),
            None,
        )
        changed = index is None or (
            report.community_feedback[index].model_dump(mode="json")
            != feedback.model_dump(mode="json")
        )
        if index is None:
            report.community_feedback.append(feedback)
        elif changed:
            report.community_feedback[index] = feedback
        if changed:
            report.updated_at = utc_now()
            self.store.upsert_report(report)
            self._compile()
        return {
            "ok": True,
            "report_id": report.id,
            "feedback_id": feedback.id,
            "source_id": feedback.source.id,
            "snapshot_id": source_result["snapshot_id"],
            "deduplicated": not changed and source_result["deduplicated"],
            "report_changed": changed or source_result["report_changed"],
        }

    def acquire_and_ingest_community_feedback(
        self,
        report_id: str,
        feedback: CommunityFeedbackItem,
        *,
        excerpt: str | None = None,
        acquisition_url: str | None = None,
    ) -> dict[str, Any]:
        acquired = self.acquire_public_source(acquisition_url or feedback.source.url)
        source = feedback.source.model_copy(
            update={
                "captured_at": acquired["captured_at"],
                "resolved_url": acquired["final_url"],
                "content_sha256": acquired["content_sha256"],
                "content_type": acquired["content_type"],
                "content_bytes": acquired["bytes"],
            }
        )
        result = self.ingest_community_feedback(
            report_id,
            feedback.model_copy(update={"source": source}),
            excerpt=excerpt,
        )
        result["acquisition"] = acquired
        return result

    def theme_view(self, report_id: str) -> dict[str, Any]:
        report = self.store.get_report(report_id)
        if not report:
            raise ValueError(f"report not found: {report_id}")
        themes: dict[str, dict[str, Any]] = {}
        for voice in report.player_voices:
            if voice.status != "active" or voice.review_status != "reviewed":
                continue
            theme = themes.setdefault(
                voice.theme,
                {"theme": voice.theme, "sentiments": {}, "voices": [], "has_disagreement": False},
            )
            theme["sentiments"][voice.sentiment] = theme["sentiments"].get(voice.sentiment, 0) + 1
            theme["voices"].append(
                {
                    "id": voice.id,
                    "summary": voice.summary,
                    "quote": voice.quote,
                    "source_id": voice.source_id,
                    "system_node_id": voice.system_node_id,
                    "version_context": voice.version_context,
                }
            )
        for theme in themes.values():
            theme["has_disagreement"] = len(theme["sentiments"]) > 1
        return {
            "schema": "game-observatory.player-voice-theme-view.v1",
            "report_id": report.id,
            "themes": sorted(themes.values(), key=lambda item: item["theme"]),
            "disclaimer": "Counts describe only the collected public expressions; they are not population proportions.",
        }

    def review_player_voice(
        self,
        report_id: str,
        voice_id: str,
        *,
        decision: str,
        reviewer: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        if decision not in {"reviewed", "rejected"}:
            raise ValueError("voice review decision must be reviewed or rejected")
        report = self.store.get_report(report_id)
        if not report:
            raise ValueError(f"report not found: {report_id}")
        index = next((i for i, item in enumerate(report.player_voices) if item.id == voice_id), None)
        if index is None:
            raise ValueError(f"player voice not found: {voice_id}")
        voice = report.player_voices[index].model_copy(
            update={
                "review_status": decision,
                "reviewed_at": utc_now(),
                "reviewed_by": reviewer.strip(),
                "review_note": note,
            }
        )
        report.player_voices[index] = voice
        report.updated_at = utc_now()
        self.store.upsert_report(report)
        for record in self.store.list_voice_records(report.id):
            if record.voice.id == voice_id:
                self.store.update_voice_record(record.model_copy(update={"voice": voice}))
        self._compile()
        return {
            "ok": True,
            "report_id": report.id,
            "voice": voice.model_dump(mode="json"),
        }

    def retract_source(self, source_id: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("retraction reason is required")
        now = utc_now()
        affected: list[str] = []
        for report in self.store.list_reports(include_drafts=True):
            source_index = next((i for i, item in enumerate(report.sources) if item.id == source_id), None)
            if source_index is None:
                continue
            source = report.sources[source_index]
            if source.status == "retracted":
                continue
            report.sources[source_index] = source.model_copy(
                update={
                    "status": "retracted",
                    "retracted_at": now,
                    "retraction_reason": reason,
                }
            )
            report.player_voices = [
                item.model_copy(
                    update={
                        "status": "retracted",
                        "retracted_at": now,
                        "retraction_reason": reason,
                    }
                )
                if item.source_id == source_id
                else item
                for item in report.player_voices
            ]
            report.community_feedback = [
                item for item in report.community_feedback if item.source.id != source_id
            ]
            report.updated_at = now
            self.store.upsert_report(report)
            affected.append(report.id)
        snapshots = self.store.retract_source_snapshots(source_id, reason, now)
        voices = self.store.retract_voice_records(source_id, reason, now)
        if affected:
            self._compile()
        return {
            "ok": True,
            "source_id": source_id,
            "affected_reports": affected,
            "retracted_snapshots": snapshots,
            "retracted_voice_records": voices,
            "already_retracted": not affected,
        }
