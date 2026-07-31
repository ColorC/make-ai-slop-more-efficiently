# [OMNI] origin=codex domain=dashboard/boss_sight ts=2026-07-10T00:00:00Z type=service
# [OMNI] summary="按根对话反向聚合送审材料, 仅突出有具体理由且未在对话说明的内容"
# [OMNI] why="人的判断回到 agent 对话;会话绑定台账是材料归属唯一真源"
# [OMNI] tags=reviewstage,session-binding,readback,attention
# [OMNI] material_id="material:dashboard.boss_sight.reviewstage.readback.py"
"""Fast read-back for review materials linked to the current conversation."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.identity import (
    all_session_bindings,
    bindings_by_session_key,
    current_session_meta,
    link_record_to_session,
    update_session_binding,
)

from .store import Material, MaterialStore


_SAFE_SESSION_RE = re.compile(r"^[0-9A-Za-z._:-]+$")
_CODEX_UUID_SUFFIX_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_READBACK_CACHE_LOCK = threading.RLock()
_CODEX_ROLLOUT_PATHS: dict[tuple[str, str], Path] = {}
_CODEX_INDEXED_ROOTS: dict[str, float] = {}
_CODEX_INDEX_REFRESH_S = 10.0
_TRANSCRIPT_CACHE_MAX = 64
_TRANSCRIPT_CACHE: OrderedDict[
    str,
    tuple[int, int, int, str],
] = OrderedDict()


def _codex_sessions_root() -> Path:
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return home / "sessions"


def _codex_rollout_path(session_id: str) -> Path | None:
    if not session_id or not _SAFE_SESSION_RE.fullmatch(session_id):
        return None
    root = _codex_sessions_root()
    if not root.is_dir():
        return None
    cache_key = (str(root), session_id)
    with _READBACK_CACHE_LOCK:
        cached = _CODEX_ROLLOUT_PATHS.get(cache_key)
        if cached is not None and cached.is_file():
            return cached
        root_key = str(root)
        indexed_at = _CODEX_INDEXED_ROOTS.get(root_key, 0.0)
        session_is_uuid = _CODEX_UUID_SUFFIX_RE.fullmatch(session_id) is not None
        if session_is_uuid and time.monotonic() - indexed_at < _CODEX_INDEX_REFRESH_S:
            return None
        suffix = f"-{session_id}.jsonl"
        match: Path | None = None
        try:
            for path in root.rglob("*.jsonl"):
                stem_match = _CODEX_UUID_SUFFIX_RE.search(path.stem)
                if stem_match is not None:
                    indexed_id = stem_match.group(1)
                    indexed_key = (root_key, indexed_id)
                    previous = _CODEX_ROLLOUT_PATHS.get(indexed_key)
                    if previous is None or str(path) > str(previous):
                        _CODEX_ROLLOUT_PATHS[indexed_key] = path
                if path.name.endswith(suffix) and (
                    match is None or str(path) > str(match)
                ):
                    match = path
        except OSError:
            return None
        _CODEX_INDEXED_ROOTS[root_key] = time.monotonic()
        match = _CODEX_ROLLOUT_PATHS.get(cache_key) or match
        if match is not None:
            _CODEX_ROLLOUT_PATHS[cache_key] = match
        return match


def _codex_session_meta(session_id: str) -> dict[str, Any]:
    path = _codex_rollout_path(session_id)
    if path is None:
        return {}
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for _ in range(8):
                raw = stream.readline()
                if not raw:
                    break
                event = json.loads(raw)
                if event.get("type") == "session_meta":
                    payload = event.get("payload") or {}
                    return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _root_codex_conversation(session_id: str) -> str:
    """Only subagent threads ascend; user-created forks remain separate conversations."""
    current = session_id
    seen: set[str] = set()
    for _ in range(8):
        if not current or current in seen:
            break
        seen.add(current)
        meta = _codex_session_meta(current)
        if meta.get("thread_source") != "subagent":
            break
        parent = str(meta.get("parent_thread_id") or "").strip()
        if not parent:
            break
        current = parent
    return current or session_id


def resolve_review_context(
    *,
    session_id: str | None = None,
    conversation_id: str | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    meta = current_session_meta()
    sid = (session_id or meta.get("session_id") or "").strip()
    provider_v = (provider or meta.get("provider") or ("codex" if os.environ.get("CODEX_THREAD_ID") else "")).strip()
    if not sid:
        return {"resolved": False, "confidence": "none"}
    binding = bindings_by_session_key().get(f"{provider_v}:{sid}", {})
    prefix = "codex" if provider_v == "codex" else "cc"
    if not binding:
        canonical_trace_id = f"{prefix}_{sid}"
        binding = all_session_bindings().get(canonical_trace_id, {})
    trace_id = str(binding.get("trace_id") or (meta.get("trace_id") if sid == meta.get("session_id") else "") or f"{prefix}_{sid}")
    root_id = (
        (conversation_id or "").strip()
        or str(binding.get("conversation_id") or "").strip()
        or (_root_codex_conversation(sid) if provider_v == "codex" else sid)
    )
    return {
        "resolved": True,
        "provider": provider_v,
        "session_id": sid,
        "trace_id": trace_id,
        "conversation_id": root_id,
        "cwd": str(
            binding.get("cwd")
            or (meta.get("cwd") if sid == meta.get("session_id") else "")
            or ""
        ),
        "confidence": "high" if session_id or os.environ.get("CODEX_THREAD_ID") else "medium",
    }


def link_material_to_current_conversation(material_id: str, plan_id: str | None) -> dict[str, Any]:
    """Link after material persistence; failure is reported to the submitting agent."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("OMNI_TEST_ALLOW_SESSION_LINK"):
        return {"linked": False, "reason": "pytest-session-link-disabled"}
    context = resolve_review_context()
    if not context.get("resolved"):
        return {"linked": False, "reason": "session-unresolved"}
    update_session_binding(
        context["trace_id"],
        provider=context["provider"],
        session_id=context["session_id"],
        conversation_id=context["conversation_id"],
        cwd=os.getcwd(),
    )
    link_record_to_session(
        context["trace_id"], kind="review_material", record_id=material_id, ref_id=plan_id,
    )
    return {"linked": True, **context}


def _assistant_text(
    conversation_id: str,
    provider: str,
    cwd: str | None = None,
) -> str:
    if provider == "codex":
        path = _codex_rollout_path(conversation_id)
    elif provider in {"claude", "claude_code", "claude-code"}:
        try:
            from omnicompany.dashboard.ccdaemon.pty import _claude_jsonl_for

            path = _claude_jsonl_for(cwd or "", conversation_id)
        except Exception:
            path = None
    else:
        path = None
    if path is None:
        return ""
    cache_key = f"{provider}:{path}"
    with _READBACK_CACHE_LOCK:
        try:
            stat = path.stat()
        except OSError:
            return ""
        cached = _TRANSCRIPT_CACHE.get(cache_key)
        if cached is not None:
            cached_size, cached_mtime, cached_offset, cached_text = cached
            if stat.st_size == cached_size and stat.st_mtime_ns == cached_mtime:
                _TRANSCRIPT_CACHE.move_to_end(cache_key)
                return cached_text
            can_append = stat.st_size >= cached_size
        else:
            cached_offset = 0
            cached_text = ""
            can_append = False

        chunks: list[str] = []
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                if can_append:
                    stream.seek(cached_offset)
                while True:
                    line_offset = stream.tell()
                    raw = stream.readline()
                    if not raw:
                        break
                    # Provider logs are append-only JSONL. Do not advance the
                    # checkpoint past a line that is still being written.
                    if not raw.endswith("\n"):
                        stream.seek(line_offset)
                        break
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if provider == "codex":
                        if event.get("type") != "response_item":
                            continue
                        payload = event.get("payload") or {}
                        if payload.get("type") != "message" or payload.get("role") != "assistant":
                            continue
                        content = payload.get("content") or []
                    else:
                        if event.get("type") != "assistant":
                            continue
                        payload = event.get("message") or {}
                        if payload.get("role") not in {None, "assistant"}:
                            continue
                        content = payload.get("content") or []
                    if isinstance(content, str):
                        chunks.append(content)
                        continue
                    for part in content:
                        if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                            text = part.get("text")
                            if isinstance(text, str):
                                chunks.append(text)
                offset = stream.tell()
        except OSError:
            return cached_text if cached is not None else ""

        added = "\n".join(chunks)
        text = cached_text if can_append else ""
        if added:
            text = f"{text}\n{added}" if text else added
        try:
            final_stat = path.stat()
        except OSError:
            final_stat = stat
        _TRANSCRIPT_CACHE[cache_key] = (
            final_stat.st_size,
            final_stat.st_mtime_ns,
            offset,
            text,
        )
        _TRANSCRIPT_CACHE.move_to_end(cache_key)
        while len(_TRANSCRIPT_CACHE) > _TRANSCRIPT_CACHE_MAX:
            _TRANSCRIPT_CACHE.popitem(last=False)
        return text


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _has_concrete_content(store: MaterialStore, material: Material) -> bool:
    if (material.inline_content or "").strip():
        return True
    if material.file_relpath:
        try:
            path = store.resolve_file_path(material)
            return bool(path and Path(path).is_file() and Path(path).stat().st_size > 0)
        except OSError:
            return False
    return bool(str((material.extra or {}).get("live_url") or "").strip())


def _mention(material: Material, assistant_text: str) -> tuple[bool, str | None]:
    markers = [material.id, material.title, material.file_relpath]
    if material.file_relpath:
        markers.append(Path(material.file_relpath).name)
    for key, value in (material.extra or {}).items():
        key = str(key).strip().lower()
        if isinstance(value, str) and (key == "url" or key.endswith("_url")):
            markers.append(value)
    for marker in markers:
        marker = (marker or "").strip()
        if len(marker) >= 3 and marker in assistant_text:
            return True, marker
    return False, None


def build_review_readback(
    store: MaterialStore,
    *,
    session_id: str | None = None,
    conversation_id: str | None = None,
    provider: str | None = None,
    include_archived: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    context = resolve_review_context(
        session_id=session_id, conversation_id=conversation_id, provider=provider,
    )
    if not context.get("resolved"):
        return {"kind": "review_readback", "context": context, "counts": {"all": 0}, "items": []}
    bindings = all_session_bindings()
    trace_ids = {
        trace_id
        for trace_id, binding in bindings.items()
        if binding.get("conversation_id") == context["conversation_id"]
    }
    trace_ids.add(context["trace_id"])
    record_ids: list[str] = []
    for trace_id in trace_ids:
        for record in (bindings.get(trace_id, {}).get("records") or []):
            if record.get("kind") == "review_material" and record.get("id"):
                record_ids.append(str(record["id"]))
    record_ids = list(dict.fromkeys(record_ids))
    store.reload()
    transcript = _assistant_text(
        context["conversation_id"],
        context["provider"],
        context.get("cwd"),
    )
    linked_record_ids = set(record_ids)
    # A successfully linked submission is authoritative.  Some provider/tool
    # paths can still return the material id/link/title in the assistant reply
    # without writing the session ledger, though.  Exact transcript evidence is
    # strong enough to recover those materials without guessing by time or plan.
    if transcript:
        for candidate in store.list(
            include_archived=include_archived,
            include_internal=False,
        ):
            mentioned, _ = _mention(candidate, transcript)
            if mentioned and candidate.id not in linked_record_ids:
                record_ids.append(candidate.id)
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for material_id in record_ids:
        material = store.get(material_id)
        if material is None:
            missing.append(material_id)
            continue
        if material.archived and not include_archived:
            continue
        reason = (material.pushed_reason or "").strip()
        concrete = _has_concrete_content(store, material)
        mentioned, evidence = _mention(material, transcript)
        if reason and concrete and not mentioned:
            presentation = "highlight"
        elif reason and concrete and mentioned:
            presentation = "explained"
        else:
            presentation = "background"
        items.append({
            "id": material.id,
            "title": material.title,
            "status": _enum_value(material.status),
            "tier": _enum_value(material.tier),
            "kind": _enum_value(material.kind),
            "plan_id": material.source_plan_id,
            "reason": reason or None,
            "has_concrete_content": concrete,
            "presentation": presentation,
            "mentioned_in_conversation": mentioned,
            "mention_evidence": evidence,
            "association": (
                "session_binding"
                if material.id in linked_record_ids
                else "conversation_mention"
            ),
            "created_at": material.created_at,
        })
    rank = {"highlight": 0, "explained": 1, "background": 2}
    items.sort(key=lambda item: (rank[item["presentation"]], item["status"] != "pending", item["created_at"]), reverse=False)
    items = items[: max(1, limit)]
    counts = {"all": len(items), "highlight": 0, "explained": 0, "background": 0}
    for item in items:
        counts[item["presentation"]] += 1
    return {
        "kind": "review_readback",
        "context": context,
        "counts": counts,
        "items": items,
        "missing_material_ids": missing,
        "legacy_unattributed_note": "历史材料没有会话反向链接, 不做时间/plan 猜测回填。",
    }
