# [OMNI] origin=codex domain=services/session-companion ts=2026-08-11 type=service
# [OMNI] material_id="material:services.session-companion.executor.py"
"""Deterministic executor for validated companion proposals."""

from __future__ import annotations

import re
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from .models import CompanionEvent, stable_digest
from .policy import (
    explicit_decision_quote,
    feedback_context_requested,
    material_submission_allowed,
)


SCOPE_AUTO_BIND_CONFIDENCE = 0.7


def _enum(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value or "")


class ActionExecutor:
    """Apply only allowlisted actions through established store APIs."""

    def __init__(
        self,
        *,
        review_store: Any | None = None,
        authored_store: Any | None = None,
        digest_writer: Callable[..., dict[str, Any]] | None = None,
        coordination_store: Any | None = None,
    ) -> None:
        self._review_store = review_store
        self._authored_store = authored_store
        self._digest_writer = digest_writer
        if coordination_store is None:
            from .store import CompanionStore

            review_root = getattr(review_store, "root", None)
            coordination_root = (
                Path(review_root).parent / "_session_companion_coordination"
                if review_root is not None
                else None
            )
            coordination_store = CompanionStore(coordination_root)
        self._coordination_store = coordination_store

    @property
    def review_store(self) -> Any:
        if self._review_store is None:
            from omnicompany.packages.services.boss_sight.reviewstage.routes import get_store

            self._review_store = get_store()
        return self._review_store

    @property
    def authored_store(self) -> Any:
        if self._authored_store is None:
            from omnicompany.packages.services.boss_sight.authored.store import get_authored_store

            self._authored_store = get_authored_store()
        return self._authored_store

    def execute(self, event: CompanionEvent, actions: dict[str, Any]) -> list[dict[str, Any]]:
        receipts: list[dict[str, Any]] = []
        try:
            receipts.extend(self._apply_digest(event, actions.get("digest") or {}))
            digest_payload = actions.get("digest") or {}
            digest_project = self._canonical_project(
                str(digest_payload.get("project") or "").strip()
            )
            if not digest_project:
                digest_project = self._content_project(
                    [event.first_user_input, event.user_input, event.assistant_output,
                     event.compact_summary, *[str(value) for value in digest_payload.values()]]
                )
            scopes = [row for row in actions.get("scope_segments") or [] if isinstance(row, dict)]
            # The companion may correctly identify a project in its digest but
            # omit the redundant scope action. If the digest text and current
            # event both point to exactly one registered project, synthesize the
            # same proposal here so project assignment is not lost between the
            # two JSON channels. Ambiguous content stays unbound.
            if not scopes and digest_project:
                evidence = self._event_project_evidence(
                    event,
                    {},
                    model_project=digest_project,
                    candidate_project=digest_project,
                    confidence=0.9,
                )
                digest_evidence = self._content_project(
                    [str(value) for value in digest_payload.values()]
                )
                if (
                    evidence in (set(), {digest_project})
                    and digest_evidence == digest_project
                ):
                    scopes = [{
                        "project": digest_project,
                        "confidence": 0.9,
                        "make_default": True,
                        "rationale": "digest 与会话内容共同指向唯一注册项目",
                        "evidence_refs": list(event.evidence_refs),
                    }]
            high_confidence_scopes = [
                row
                for row in scopes
                if float(row.get("confidence") or 0.0) >= SCOPE_AUTO_BIND_CONFIDENCE
                and any(str(row.get(key) or "").strip() for key in ("project", "workstream", "plan"))
            ]
            auto_default_scope = high_confidence_scopes[0] if len(high_confidence_scopes) == 1 else None
            for scope in scopes:
                receipts.append(
                    self._apply_scope(
                        event,
                        scope,
                        digest_project=digest_project,
                        unique_high_confidence=scope is auto_default_scope,
                    )
                )
            for decision in actions.get("decisions") or []:
                key = self._decision_semantic_key(event, decision)
                with self._coordination_lock(key):
                    receipts.append(self._apply_decision(event, decision))
            for material in actions.get("materials") or []:
                key = self._material_semantic_key(event, material)
                with self._coordination_lock(key):
                    receipts.append(self._apply_material(event, material))
            for feedback in actions.get("feedback") or []:
                key = self._feedback_semantic_key(event, feedback)
                with self._coordination_lock(key):
                    receipts.append(self._apply_feedback(event, feedback))
            for message in actions.get("unresolved") or []:
                receipts.append({"action": "unresolved", "status": "recorded", "message": str(message)})
        except Exception as exc:
            raise ActionExecutionError(str(exc), receipts=receipts) from exc
        return receipts

    def _apply_digest(self, event: CompanionEvent, digest: dict[str, Any]) -> list[dict[str, Any]]:
        visible = {key: value for key, value in digest.items() if str(value or "").strip()}
        if not visible or not event.session_id:
            return []
        if not any(
            str(value or "").strip()
            for value in (
                event.first_user_input,
                event.user_input,
                event.assistant_output,
                event.compact_summary,
            )
        ):
            return [{
                "action": "digest",
                "status": "ignored",
                "reason": "empty lifecycle event preserves the last valid digest",
            }]
        if visible.get("project"):
            canonical_project = self._canonical_project(str(visible["project"]))
            if canonical_project:
                visible["project"] = canonical_project
            else:
                visible.pop("project", None)
        digest_project = str(
            visible.get("project") or self._canonical_project(event.project) or ""
        )
        if visible.get("plan"):
            resolved_plan = self._resolve_plan_id(str(visible["plan"]))
            if not resolved_plan or not self._valid_plan(resolved_plan, project=digest_project):
                visible.pop("plan", None)
            else:
                # Persist one canonical plan id even when the model returned a
                # globally-unique basename or date-stripped topic.
                visible["plan"] = resolved_plan
        writer = self._digest_writer
        if writer is None:
            from omnicompany.packages.services.boss_sight.services.agent_digest import upsert_digest

            writer = upsert_digest
        result = writer(
            event.provider,
            event.session_id,
            visible,
            source="session_companion",
            source_mtime=event.transcript_mtime,
        )
        return [{"action": "digest", "status": "applied", "fields": sorted(visible), "result": result}]

    def _apply_scope(
        self,
        event: CompanionEvent,
        action: dict[str, Any],
        *,
        digest_project: str = "",
        unique_high_confidence: bool = False,
    ) -> dict[str, Any]:
        proposed_project = str(action.get("project") or "").strip()
        project = self._canonical_project(proposed_project) if proposed_project else ""
        workstream = str(action.get("workstream") or "").strip()
        plan = str(action.get("plan") or "").strip()
        resolved_plan = self._resolve_plan_id(plan) if plan else ""
        if proposed_project and not project:
            return self._skip(
                "scope",
                "unknown project",
                project=proposed_project,
                canonical_project="",
            )
        scope_project = project or self._canonical_project(event.project)
        if not scope_project and resolved_plan:
            scope_project = self._plan_project(resolved_plan)
        if (plan or workstream) and not scope_project:
            return self._skip(
                "scope",
                "project required for plan or workstream",
                plan=plan,
                workstream=workstream,
            )
        if plan and not self._valid_plan(plan, project=scope_project):
            return self._skip(
                "scope",
                "unknown or cross-project plan",
                plan=plan,
                project=scope_project,
            )
        plan = resolved_plan or plan
        if workstream and not self._valid_workstream(workstream, project=scope_project):
            return self._skip("scope", "unknown or cross-project workstream", workstream=workstream)
        if not any((project, workstream, plan)):
            return self._skip("scope", "empty scope")
        from omnicompany.packages.services._core.identity import (
            binding_for_session,
            session_binding_is_confirmed,
            set_session_default_scope_from_history,
            upsert_session_scope_segment,
            update_session_binding,
        )
        current = binding_for_session(
            trace_id=event.trace_id,
            provider=event.provider,
            session_id=event.session_id,
        )
        binding_trace_id = str(current.get("trace_id") or event.trace_id)

        # Never let model text collide with a human-authored history segment.
        # The normalized model id remains part of the deterministic hash so
        # multiple evidenced scopes in one event still stay distinct.
        segment_id = "companion-" + stable_digest(
            event.event_id,
            action.get("segment_id"),
            project,
            workstream,
            plan,
        )
        made_default = False
        default_block_reason = ""
        confidence = float(action.get("confidence") or 0.0)
        wants_default = (
            confidence >= SCOPE_AUTO_BIND_CONFIDENCE
            and (bool(action.get("make_default")) or unique_high_confidence)
        )
        if wants_default:
            current_project = self._canonical_project(str(current.get("project") or ""))
            target_project = project or scope_project
            evidence_projects = self._event_project_evidence(
                event,
                current,
                model_project=digest_project,
                candidate_project=target_project,
                confidence=confidence,
            )
            conflicting_manual_scope = session_binding_is_confirmed(current) and any(
                (
                    project and current_project and project != current_project,
                    workstream
                    and str(current.get("workstream") or "").strip()
                    and workstream != str(current.get("workstream") or "").strip(),
                    plan
                    and str(current.get("active_plan") or "").strip()
                    and plan != str(current.get("active_plan") or "").strip(),
                )
            )
            if conflicting_manual_scope:
                default_block_reason = "manual authority preserved"
            elif len(evidence_projects) > 1:
                default_block_reason = "multiple project evidence"
            elif target_project and evidence_projects != {target_project}:
                default_block_reason = "insufficient project evidence"
            else:
                made_default = True

        upsert_session_scope_segment(
            binding_trace_id,
            segment_id,
            project=project or None,
            workstream=workstream or None,
            active_plan=plan or None,
            binding_state="observed" if made_default else "candidate",
            source="session_companion",
            rationale=str(action.get("rationale") or ""),
            evidence_refs=list(action.get("evidence_refs") or []),
        )
        if made_default:
            set_session_default_scope_from_history(
                binding_trace_id,
                project=project or None,
                workstream=workstream or None,
                active_plan=plan or None,
            )
            update_session_binding(binding_trace_id, source="session_companion")
        return {
            "action": "scope",
            "status": "applied",
            "segment_id": segment_id,
            "made_default": made_default,
            "default_block_reason": default_block_reason,
            "project": project,
            "proposed_project": proposed_project,
            "workstream": workstream,
            "plan": plan,
        }

    def _apply_decision(self, event: CompanionEvent, action: dict[str, Any]) -> dict[str, Any]:
        quote = str(action.get("evidence_quote") or "").strip()
        if not explicit_decision_quote(event.user_input, quote):
            return self._skip("decision", "decision lacks a verbatim durable user choice")
        semantic_key = self._decision_semantic_key(event, action)
        from omnicompany.packages.domains.decisions import library, record
        from omnicompany.packages.services._core.identity import link_record_to_session

        raw_project = str(action.get("project") or event.project or "").strip()
        project = self._canonical_project(raw_project) or raw_project
        plan = str(action.get("plan") or event.active_plan or "").strip()
        decision_scope = self._decision_scope(project)
        decision_statement = self._normalized_decision_text(action.get("statement"))
        for existing in library.active_records():
            if str(existing.get("kind") or "") != "decision":
                continue
            existing_scope = self._decision_scope(str(existing.get("project") or ""))
            same_stable_key = existing.get("session_companion_semantic_key") == semantic_key
            same_statement = (
                existing_scope == decision_scope
                and self._normalized_decision_text(existing.get("statement")) == decision_statement
            )
            if same_stable_key or same_statement:
                link_record_to_session(
                    event.trace_id,
                    kind="decision",
                    record_id=existing["id"],
                    ref_id=plan or project or None,
                )
                return {
                    "action": "decision",
                    "status": "duplicate",
                    "semantic_key": semantic_key,
                    "decision_id": existing["id"],
                }

        chosen = [row for row in action.get("chosen") or [] if isinstance(row, dict)]
        rejected = [row for row in action.get("rejected") or [] if isinstance(row, dict)]
        decision_space = [
            {"option": str(row.get("option") or ""), "chosen": selected, "why": str(row.get("why") or "")}
            for selected, rows in ((True, chosen), (False, rejected))
            for row in rows
            if str(row.get("option") or "").strip()
        ]
        provider = event.provider.casefold()
        channel = "claude" if "claude" in provider else "codex" if "codex" in provider else "note"
        fields: dict[str, Any] = {
            "status": "adopted" if chosen and rejected else "proposed",
            "scope": "project" if project else "personal",
            "authority": "user_explicit",
            "confidence": "high",
            "origin": {
                "channel": channel,
                "session_ref": event.trace_id,
                "observed_at": event.created_at,
                "author": "user",
            },
            "anchor": {
                "kind": "other",
                "ref": event.evidence_refs[0] if event.evidence_refs else event.event_id,
                "excerpt": quote,
            },
            "evidence": [
                {
                    "ref": event.evidence_refs[0] if event.evidence_refs else event.event_id,
                    "note": f"用户原话：{quote}",
                }
            ],
            "rationale": str(action.get("rationale") or "").strip(),
            "boundary": str(action.get("boundary") or "").strip(),
            "applies_to": str(action.get("applies_to") or "").strip(),
            "tags": ["session-companion", "user-explicit"],
            "created_by": "session_companion",
            "session_companion_semantic_key": semantic_key,
        }
        if project:
            fields["project"] = project
        if plan:
            fields["track"] = {"kind": "plan", "id": plan}
        if decision_space:
            fields["decision_space"] = decision_space
        decision = record("decision", str(action.get("statement") or "").strip(), **fields)
        link_record_to_session(
            event.trace_id,
            kind="decision",
            record_id=decision["id"],
            ref_id=plan or project or None,
        )
        return {
            "action": "decision",
            "status": "applied",
            "semantic_key": semantic_key,
            "decision_id": decision["id"],
            "decision_status": decision.get("status"),
            "statement": decision.get("statement"),
            "project": project,
            "plan": plan,
        }

    def _apply_material(self, event: CompanionEvent, action: dict[str, Any]) -> dict[str, Any]:
        if not material_submission_allowed(event.user_input):
            return self._skip(
                "material",
                "current user turn did not explicitly request a review submission",
            )
        requested_project = str(action.get("project") or "").strip()
        plan = str(action.get("plan") or "").strip()
        warnings: list[str] = []
        if requested_project == "unfiled":
            ownership_project = ""
            project = "unfiled"
        else:
            ownership_project = (
                requested_project
                if requested_project and self._valid_project(requested_project)
                else event.project if event.project and self._valid_project(event.project) else ""
            )
            if requested_project and requested_project != ownership_project:
                warnings.append(f"unverified requested project {requested_project!r} was not used")
            project = (
                ownership_project
                if ownership_project and self._valid_review_project(ownership_project)
                else "unfiled"
            )
            if project == "unfiled":
                warnings.append(
                    "material was submitted as unfiled because no verified ReviewStage project was available"
                )
        if plan and not ownership_project:
            warnings.append(f"unverified requested plan {plan!r} was not linked")
            plan = ""
        elif plan and not self._valid_plan(plan, project=ownership_project):
            warnings.append(f"unverified requested plan {plan!r} was not linked")
            plan = ""
        semantic_key = self._material_semantic_key(event, action)
        existing = self._material_by_semantic_key(semantic_key)
        if existing is not None:
            return {"action": "material", "status": "duplicate", "semantic_key": semantic_key, "material_id": existing.id}
        family = f"session-companion:{stable_digest(event.trace_id, ownership_project or project, action.get('title'))}"
        versions = [
            int(getattr(item, "version", 0) or 0)
            for item in self.review_store.list(include_archived=True, include_internal=True)
            if str(getattr(item, "version_family", "") or "") == family
        ]
        version = max(versions, default=0) + 1
        extra = {
            "session_companion": {
                "semantic_key": semantic_key,
                "source_trace_id": event.trace_id,
                "source_event_id": event.event_id,
                "lane_key": event.lane_key,
                "evidence_refs": list(action.get("evidence_refs") or []),
                "rationale": str(action.get("rationale") or ""),
                "ownership_version": 0,
                "requested_project": requested_project,
                "ownership_project": ownership_project,
                "ownership_warnings": warnings,
            }
        }
        material = self.review_store.create(
            kind=str(action.get("kind") or "markdown"),
            tier=str(action.get("tier") or "important"),
            title=str(action.get("title") or "").strip(),
            source_subagent_id=f"session-companion:{stable_digest(event.lane_key)}",
            source_plan_id=plan or None,
            inline_content=str(action.get("content") or "").strip(),
            extra=extra,
            project=project,
            track=str(action.get("track") or "工作报告"),
            version=version,
            version_family=family,
        )
        from omnicompany.packages.services._core.identity import link_record_to_session

        link_record_to_session(
            event.trace_id,
            kind="review_material",
            record_id=material.id,
            ref_id=plan or None,
        )
        return {
            "action": "material",
            "status": "applied",
            "semantic_key": semantic_key,
            "material_id": material.id,
            "title": material.title,
            "project": project,
            "requested_project": requested_project,
            "warnings": warnings,
        }

    def _apply_feedback(self, event: CompanionEvent, action: dict[str, Any]) -> dict[str, Any]:
        if not feedback_context_requested(event.user_input):
            return self._skip(
                "feedback",
                "current user turn does not contain material feedback",
                material_id=str(action.get("material_id") or "").strip(),
            )
        material_id = str(action.get("material_id") or "").strip()
        material = self.review_store.get(material_id)
        if material is None:
            return self._skip("feedback", "material not found", material_id=material_id)
        explicit_target = self._feedback_target_is_explicit(event, material)
        material_project = str(getattr(material, "project", "") or "")
        material_plan = str(getattr(material, "source_plan_id", "") or "")
        if not explicit_target and event.project and material_project != event.project:
            return self._skip(
                "feedback",
                "implicit feedback target belongs to another project",
                material_id=material_id,
            )
        if (
            not explicit_target
            and event.active_plan
            and material_plan
            and material_plan != event.active_plan
        ):
            return self._skip(
                "feedback",
                "implicit feedback target belongs to another plan",
                material_id=material_id,
            )
        if not self._feedback_target_is_unique(event, material):
            return self._skip(
                "feedback",
                "material target is not explicit or uniquely linked to this conversation",
                material_id=material_id,
            )
        kind = str(action.get("kind") or "")
        semantic_key = self._feedback_semantic_key(event, action)
        applied = list((material.extra or {}).get("session_companion_feedback_keys") or [])
        if semantic_key in applied:
            return {"action": "feedback", "status": "duplicate", "semantic_key": semantic_key, "material_id": material_id}
        if kind in {"accept", "reject"}:
            if not bool(action.get("explicit")) or not self._user_explicitly_set_verdict(
                event.user_input,
                kind,
            ):
                return self._skip("feedback", "verdict is not explicit", material_id=material_id)
            verdict = "accepted" if kind == "accept" else "rejected"
            self.review_store.set_verdict(
                material_id,
                verdict,
                by="session_companion",
                reason=str(action.get("reason") or action.get("content") or ""),
            )
            self._remember_feedback_key(material_id, semantic_key, applied)
            return {
                "action": "feedback",
                "status": "applied",
                "kind": kind,
                "verdict": verdict,
                "material_id": material_id,
                "semantic_key": semantic_key,
            }
        content = str(action.get("content") or action.get("reason") or "").strip()
        if not content:
            return self._skip("feedback", "comment/update content is empty", material_id=material_id)
        existing_note = next(
            (
                note
                for note in self.authored_store.list(target_kind="material", target_id=material_id, include_archived=True)
                if str((note.extra or {}).get("session_companion_semantic_key") or "") == semantic_key
            ),
            None,
        )
        if existing_note is not None:
            self._record_comment_edge_once(material_id, existing_note)
            self._remember_feedback_key(material_id, semantic_key, applied)
            return {"action": "feedback", "status": "duplicate", "semantic_key": semantic_key, "note_id": existing_note.id}
        note = self.authored_store.create(
            content=content,
            author="session_companion",
            target={"kind": "material", "id": material_id, "material_id": material_id, "project_id": material.project},
            uses=["comment"],
            feedback_status="saved",
            project_id=material.project,
            extra={
                "session_companion_semantic_key": semantic_key,
                "source_trace_id": event.trace_id,
                "source_event_id": event.event_id,
                "feedback_kind": kind,
            },
        )
        self._record_comment_edge_once(material_id, note)
        self._remember_feedback_key(material_id, semantic_key, applied)
        return {
            "action": "feedback",
            "status": "applied",
            "kind": kind,
            "material_id": material_id,
            "note_id": note.id,
            "semantic_key": semantic_key,
        }

    def _remember_feedback_key(self, material_id: str, key: str, previous: list[str]) -> None:
        keys = list(dict.fromkeys([*previous, key]))[-100:]
        self.review_store.patch_extra(
            material_id,
            {"session_companion_feedback_keys": keys},
            by="session_companion",
        )

    @staticmethod
    def _decision_semantic_key(event: CompanionEvent, action: dict[str, Any]) -> str:
        raw_project = str(action.get("project") or event.project or "").strip()
        project = ActionExecutor._canonical_project(raw_project) or raw_project
        statement = ActionExecutor._normalized_decision_text(
            action.get("statement") or action.get("evidence_quote")
        )
        return "decision:" + stable_digest(ActionExecutor._decision_scope(project), statement)

    @staticmethod
    def _normalized_decision_text(value: Any) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return "".join(character for character in normalized if character.isalnum())

    @staticmethod
    def _decision_scope(project: str) -> str:
        raw = str(project or "").strip()
        canonical = ActionExecutor._canonical_project(raw) if raw else ""
        return canonical or ActionExecutor._normalized_decision_text(raw) or "personal"

    @staticmethod
    def _material_semantic_key(event: CompanionEvent, action: dict[str, Any]) -> str:
        return "material:" + stable_digest(
            event.trace_id,
            str(action.get("project") or "").strip(),
            str(action.get("title") or "").casefold(),
            event.user_input,
        )

    @staticmethod
    def _feedback_semantic_key(event: CompanionEvent, action: dict[str, Any]) -> str:
        return "feedback:" + stable_digest(
            event.trace_id,
            str(action.get("material_id") or "").strip(),
            str(action.get("kind") or ""),
            event.user_input,
            action.get("content"),
            action.get("reason"),
        )

    @contextmanager
    def _coordination_lock(self, semantic_key: str) -> Any:
        from .store import LockBusy

        deadline = time.monotonic() + 2.0
        lock = None
        while lock is None:
            candidate = self._coordination_store.lock(
                f"action:{semantic_key}",
                stale_after_s=300.0,
            )
            try:
                candidate.__enter__()
            except LockBusy:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
            else:
                lock = candidate
        try:
            yield
        finally:
            lock.__exit__(None, None, None)

    def _material_by_semantic_key(self, semantic_key: str) -> Any | None:
        for material in self.review_store.list(include_archived=True, include_internal=True):
            meta = (material.extra or {}).get("session_companion") or {}
            if isinstance(meta, dict) and meta.get("semantic_key") == semantic_key:
                return material
        return None

    def _record_comment_edge_once(self, material_id: str, note: Any) -> None:
        material = self.review_store.get(material_id)
        recorded = any(
            isinstance(row, dict)
            and row.get("source") == "authored_note"
            and str(row.get("comment_id") or "") == str(note.id)
            for row in (getattr(material, "history", None) or [])
        )
        if recorded:
            return
        self.review_store.record_authored_comment(
            material_id,
            note_id=note.id,
            author=note.author,
            created_at=note.created_at,
            feedback_status=note.feedback_status,
        )

    def _feedback_target_is_explicit(self, event: CompanionEvent, material: Any) -> bool:
        user_text = event.user_input.casefold()
        if material.id.casefold() in user_text:
            return True
        title = str(material.title or "").strip()
        if len(title) < 3 or title.casefold() not in user_text:
            return False
        same_title = [
            item
            for item in self.review_store.list(include_archived=False, include_internal=False)
            if str(item.title or "").strip().casefold() == title.casefold()
        ]
        return len(same_title) == 1

    def _feedback_target_is_unique(self, event: CompanionEvent, material: Any) -> bool:
        if self._feedback_target_is_explicit(event, material):
            return True
        from omnicompany.packages.services._core.identity import binding_for_session

        binding = binding_for_session(
            trace_id=event.trace_id,
            provider=event.provider,
            session_id=event.session_id,
        )
        linked_ids = {
            str(row.get("id") or "")
            for row in binding.get("records") or []
            if isinstance(row, dict) and row.get("kind") == "review_material"
        }
        candidates = [
            item
            for item in self.review_store.list(include_archived=False, include_internal=False)
            if item.id in linked_ids
            or str(((item.extra or {}).get("session_companion") or {}).get("source_trace_id") or "")
            == event.trace_id
        ]
        candidates = [item for item in candidates if _enum(item.status) == "pending"] or candidates
        return len(candidates) == 1 and candidates[0].id == material.id

    @staticmethod
    def _user_explicitly_set_verdict(user_input: str, kind: str) -> bool:
        text = str(user_input or "").casefold()
        if kind == "accept":
            negative = (
                "不通过",
                "不能通过",
                "不可通过",
                "未通过",
                "别通过",
                "不要通过",
                "不同意",
                "不批准",
                "不接受",
                "拒绝",
                "驳回",
                "否决",
                "not approve",
                "not accept",
                "reject",
                "decline",
            )
            if any(token in text for token in negative):
                return False
            return bool(
                re.search(r"通过|同意|批准|接受|\b(?:approve|approved|accept|accepted)\b", text)
            )
        if kind == "reject":
            return bool(
                re.search(
                    r"不通过|不能通过|不可通过|未通过|拒绝|驳回|否决|"
                    r"\b(?:reject|rejected|decline|declined)\b|not\s+(?:approve|accept)",
                    text,
                )
            )
        return False

    @staticmethod
    def _valid_project(project: str) -> bool:
        from omnicompany.core.projects_registry import list_projects

        return project in {str(item.get("id") or "") for item in list_projects()}

    @staticmethod
    def _canonical_project(project: str) -> str:
        from omnicompany.packages.services.boss_sight.services.project_resolution import (
            canonical_project_id,
        )

        return canonical_project_id(label=project)

    @staticmethod
    def _content_project(values: list[str]) -> str:
        """Return one registered project explicitly named by content only."""
        from omnicompany.packages.services.boss_sight.services.project_resolution import (
            project_ids_referenced_in_text,
        )

        matches = project_ids_referenced_in_text("\n".join(str(value or "") for value in values))
        return next(iter(matches)) if len(matches) == 1 else ""

    @staticmethod
    def _event_project_evidence(
        event: CompanionEvent,
        current_binding: dict[str, Any],
        *,
        model_project: str = "",
        candidate_project: str = "",
        confidence: float = 0.0,
    ) -> set[str]:
        from omnicompany.packages.services.boss_sight.services.project_resolution import (
            canonical_project_id,
            project_ids_referenced_in_text,
        )
        from omnicompany.packages.services._core.identity import session_binding_is_confirmed

        evidence: set[str] = set()
        if session_binding_is_confirmed(current_binding):
            current_project = canonical_project_id(label=current_binding.get("project"))
            if current_project:
                evidence.add(current_project)

        # Project ownership is derived from conversation content, not cwd.
        # Include both user and assistant/compact text because a user may name
        # the product once while the useful project clue appears in the agent's
        # resulting explanation or compacted context.
        for text in (
            event.user_input,
            event.first_user_input,
            event.assistant_output,
            event.compact_summary,
        ):
            evidence.update(project_ids_referenced_in_text(text))

        # A plan is an explicit scope pointer, not a directory heuristic.  Its
        # registered frontmatter/category may identify the project even when
        # the natural-language turns use only the plan name.
        active_plan = str(event.active_plan or current_binding.get("active_plan") or "").strip()
        if active_plan:
            plan_project = canonical_project_id(label=active_plan)
            if plan_project:
                evidence.add(plan_project)

        # A semantic model proposal is usable when it agrees with the digest,
        # cites this event, has substantial content, and is high confidence.
        # This is still content evidence; cwd never participates in the gate.
        model_id = canonical_project_id(label=model_project)
        candidate_id = canonical_project_id(label=candidate_project)
        content_present = any(
            str(text or "").strip()
            for text in (
                event.user_input,
                event.first_user_input,
                event.assistant_output,
                event.compact_summary,
            )
        )
        if (
            content_present
            and confidence >= 0.85
            and model_id
            and model_id == candidate_id
            and not evidence
        ):
            evidence.add(model_id)
        return evidence

    @staticmethod
    def _valid_workstream(workstream: str, *, project: str) -> bool:
        from omnicompany.core.workstreams import get_workstream

        row = get_workstream(workstream)
        return bool(row and (not project or str(row.get("project") or "") == project))

    @staticmethod
    def _valid_review_project(project: str) -> bool:
        from omnicompany.packages.services.boss_sight.reviewstage.material_types import (
            known_review_projects,
        )

        return project in known_review_projects()

    @staticmethod
    def _resolve_plan_id(plan: str) -> str:
        """Resolve a full id, basename, or unique date-stripped plan name.

        Model digests often return only the visible plan topic. Normalize that
        query before persisting it so the binding ledger keeps one plan key and
        future project joins can use the plan's own frontmatter.
        """
        query = str(plan or "").strip().replace("\\", "/")
        if not query:
            return ""
        from omnicompany.core.plans_catalogue import DATE_RE, _plans_root, _walk_plan_dirs

        root = _plans_root().resolve()
        direct = (root / query).resolve()
        try:
            direct.relative_to(root)
        except ValueError:
            direct = root / "__missing__"
        if direct.is_dir() and (direct / "plan.md").is_file():
            return direct.relative_to(root).as_posix()

        matches: list[tuple[str, Path]] = []
        for path in _walk_plan_dirs(root):
            try:
                plan_id = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if plan_id == query or path.name == query:
                matches.append((plan_id, path))
                continue
            match = DATE_RE.match(path.name)
            if match and match.group(2) == query:
                matches.append((plan_id, path))
        return matches[0][0] if len(matches) == 1 else ""

    @classmethod
    def _plan_project(cls, plan: str) -> str:
        """Read the plan's declared project, never infer one from its folder."""
        from omnicompany.core.plans_catalogue import _plans_root, parse_plan_frontmatter

        plan_id = cls._resolve_plan_id(plan)
        if not plan_id:
            return ""
        metadata = parse_plan_frontmatter(_plans_root() / plan_id / "plan.md")
        return cls._canonical_project(str(metadata.get("project") or ""))

    @classmethod
    def _valid_plan(cls, plan: str, *, project: str = "") -> bool:
        plan_id = cls._resolve_plan_id(plan)
        if not plan_id:
            return False
        from omnicompany.core.plans_catalogue import _plans_root

        plans_root = _plans_root().resolve()
        path = (plans_root / plan_id / "plan.md").resolve()
        try:
            path.relative_to(plans_root)
        except ValueError:
            return False
        if not path.is_file():
            return False
        if project:
            from omnicompany.core.plans_catalogue import parse_plan_frontmatter

            plan_project = str(parse_plan_frontmatter(path).get("project") or "").strip()
            if plan_project:
                plan_project = ActionExecutor._canonical_project(plan_project)
            # Automatic writes need an explicit ownership fact. Legacy plans
            # without project frontmatter stay unresolved instead of being
            # guessed from a directory prefix.
            if plan_project != project:
                return False
        return True

    @staticmethod
    def _skip(action: str, reason: str, **extra: Any) -> dict[str, Any]:
        return {"action": action, "status": "skipped", "reason": reason, **extra}


class ActionExecutionError(RuntimeError):
    """Executor failure carrying every receipt completed before the failure."""

    def __init__(self, message: str, *, receipts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.receipts = list(receipts)


__all__ = ["ActionExecutionError", "ActionExecutor"]
