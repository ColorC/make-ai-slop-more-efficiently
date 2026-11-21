# [OMNI] origin=ai-ide domain=omnicompany/canvas ts=2026-09-21T00:00:00Z
# [OMNI] material_id="material:packages.services.canvas.canvas_observation.py"
"""Read-only projections for the modern canvas store.

This module deliberately treats card observation metadata as opt-in.  A
legacy card has no trustworthy author/time merely because its document has an
``updatedAt`` or its source is a readable file, so projection returns
``unknown`` until a mutation writes the explicit observation envelope.
"""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

UNKNOWN = "unknown"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _present_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _explicit(value: Any) -> Any:
    if value is None or value == "":
        return UNKNOWN
    return deepcopy(value)


def _number(value: Any) -> int | float | str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return UNKNOWN


def _text(value: Any) -> str:
    return value if isinstance(value, str) else UNKNOWN


def _observation(card: Mapping[str, Any]) -> dict[str, Any]:
    material = card.get("material")
    metadata = material.get("metadata") if isinstance(material, Mapping) else None
    record = metadata.get("observation") if isinstance(metadata, Mapping) else None
    if not isinstance(record, Mapping):
        return {"source": UNKNOWN, "observed_at": UNKNOWN, "actor": UNKNOWN}
    observed_at = record.get("observed_at", record.get("observedAt"))
    return {
        "source": _explicit(record.get("source")),
        "observed_at": _explicit(observed_at),
        "actor": _explicit(record.get("actor", record.get("actor_ref"))),
    }


def card_projection(card: Mapping[str, Any]) -> dict[str, Any]:
    """Return a stable, explainable read projection for one card."""
    result = {
        "id": str(card.get("id") or UNKNOWN),
        "kind": _present_string(card.get("kind")) or UNKNOWN,
        "title": _present_string(card.get("title")) or UNKNOWN,
        "text": _text(card.get("text")),
        "source_ref": _explicit(card.get("source")),
        "x": _number(card.get("x")),
        "y": _number(card.get("y")),
        "width": _number(card.get("width")),
        "height": _number(card.get("height")),
        **_observation(card),
    }
    return result


def _todo_counts(document: Mapping[str, Any]) -> dict[str, int]:
    """Count only an explicit document ``todos`` collection.

    Canvas cards and review submissions are not TODO records.  In particular,
    do not reinterpret card status or lifecycle as a TODO count.
    """
    todos = document.get("todos")
    if not isinstance(todos, list):
        return {"active": 0, "archived": 0}
    active = 0
    archived = 0
    for item in todos:
        if not isinstance(item, Mapping):
            continue
        if bool(item.get("archived")):
            archived += 1
        else:
            active += 1
    return {"active": active, "archived": archived}


def canvas_projection(document_id: str, document: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize one modern canvas without changing the source document."""
    cards = document.get("cards") if isinstance(document.get("cards"), list) else []
    relations = document.get("relations") if isinstance(document.get("relations"), list) else []
    catalog = document.get("catalog")
    lifecycle = catalog.get("lifecycle") if isinstance(catalog, Mapping) else None
    lifecycle = _present_string(lifecycle) or UNKNOWN
    return {
        "document_id": document_id,
        "title": _present_string(document.get("title")) or UNKNOWN,
        "revision": document.get("revision") if isinstance(document.get("revision"), int) else 0,
        "updated_at": _present_string(document.get("updatedAt")) or UNKNOWN,
        "lifecycle": lifecycle,
        "state": "archived" if lifecycle == "archived" else "active",
        "card_count": len(cards),
        "relation_count": len(relations),
        "todo_counts": _todo_counts(document),
        "cards": [card_projection(card) for card in cards if isinstance(card, Mapping)],
    }


def recent_canvases(
    documents: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Project canvases by explicit document ``updatedAt`` in descending order."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    rows = list(documents)
    rows.sort(key=lambda item: str(item[1].get("updatedAt") or ""), reverse=True)
    return [canvas_projection(str(document_id), document) for document_id, document in rows[:limit]]


def recent_canvas_projection(document_id: str, document: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility-shaped single-row helper used by the HTTP bridge."""
    return canvas_projection(document_id, document)


def session_id_from_canvas_key(document_id: str) -> str:
    """Decode ``session-<base64url(external:<pty>)>`` back to the bare PTY id."""
    key = str(document_id or "")
    if not key.startswith("session-"):
        return key
    try:
        encoded = key[len("session-"):]
        encoded += "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode(encoded).decode("utf-8")
        return raw[len("external:"):] if raw.startswith("external:") else raw
    except Exception:
        return key


def canvas_has_content(
    *,
    card_count: Any = None,
    cards: Any = None,
) -> bool:
    """A canvas has content when it stores at least one non-group card."""
    if isinstance(cards, list):
        return any(
            isinstance(card, Mapping) and card.get("kind") != "group"
            for card in cards
        )
    if isinstance(card_count, int) and not isinstance(card_count, bool):
        return card_count > 0
    return False


def history_display_title(session_title: str | None, canvas_title: str | None) -> str:
    """Prefer the session's last title; fall back to the canvas name."""
    return _present_string(session_title) or _present_string(canvas_title) or "会话札记"


def query_snippet(query: str, *texts: str) -> str | None:
    """Return a short snippet around the first case-insensitive match.

    ``None`` means no haystack matched. An empty query matches everything and
    returns an empty snippet.
    """
    needle = str(query or "").strip().casefold()
    if not needle:
        return ""
    for raw in texts:
        if not isinstance(raw, str) or not raw:
            continue
        hay = raw.casefold()
        index = hay.find(needle)
        if index < 0:
            continue
        start = max(0, index - 20)
        end = min(len(raw), index + len(query.strip()) + 36)
        snippet = " ".join(raw[start:end].split())
        if start:
            snippet = "…" + snippet
        if end < len(raw):
            snippet += "…"
        return snippet
    return None


def _card_search_texts(
    document: Mapping[str, Any] | None,
    material_texts: Mapping[str, str] | None,
) -> list[str]:
    if not isinstance(document, Mapping):
        return []
    cards = document.get("cards")
    if not isinstance(cards, list):
        return []
    texts: list[str] = []
    materials = material_texts or {}
    for card in cards:
        if not isinstance(card, Mapping) or card.get("kind") == "group":
            continue
        title = _present_string(card.get("title"))
        if title:
            texts.append(title)
        body = card.get("text")
        if isinstance(body, str) and body.strip():
            texts.append(body)
        source = card.get("source")
        editable_id = ""
        if isinstance(source, Mapping):
            editable_id = str(source.get("editableId") or card.get("id") or "")
        else:
            editable_id = str(card.get("id") or "")
        material = materials.get(editable_id)
        if isinstance(material, str) and material.strip():
            texts.append(material)
    return texts


def history_canvases(
    rows: Iterable[Mapping[str, Any]],
    *,
    session_titles: Mapping[str, str] | None = None,
    documents: Mapping[str, Mapping[str, Any]] | None = None,
    material_texts: Mapping[str, str] | None = None,
    session_excerpts: Mapping[str, str] | None = None,
    favorites: Sequence[str] | None = None,
    current_id: str = "",
    query: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Project content-bearing session canvases for the history panel.

    Default label is the session's last title. Rows without content are
    dropped. A non-empty query must match session title, canvas title, card
    text, or a provided session excerpt.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    titles = session_titles or {}
    docs = documents or {}
    excerpts = session_excerpts or {}
    favorite_ids = [str(item) for item in (favorites or []) if item]
    favorite_set = set(favorite_ids)
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        document_id = str(row.get("document_id") or "")
        if not document_id:
            continue
        document = docs.get(document_id)
        cards = document.get("cards") if isinstance(document, Mapping) else None
        card_count = row.get("cards")
        if isinstance(card_count, list):
            cards = card_count
            card_count = len(card_count)
        if not canvas_has_content(card_count=card_count, cards=cards):
            if document_id not in favorite_set:
                continue
        if not document_id.startswith("session-") and document_id not in favorite_set:
            continue
        session_id = session_id_from_canvas_key(document_id)
        canvas_title = _present_string(row.get("title"))
        if not canvas_title and isinstance(document, Mapping):
            canvas_title = _present_string(document.get("title"))
        session_title = _present_string(titles.get(session_id))
        display_title = history_display_title(session_title, canvas_title)
        card_texts = _card_search_texts(document, material_texts)
        excerpt = excerpts.get(session_id) or ""
        snippet = query_snippet(
            query,
            display_title,
            session_title or "",
            canvas_title or "",
            excerpt,
            *card_texts,
        )
        if snippet is None:
            continue
        updated_at = _present_string(row.get("updated_at") or row.get("updatedAt")) or UNKNOWN
        count = len(cards) if isinstance(cards, list) else (
            card_count if isinstance(card_count, int) and not isinstance(card_count, bool) else 0
        )
        items.append({
            "document_id": document_id,
            "session_id": session_id,
            "title": display_title,
            "canvas_title": canvas_title or "",
            "session_title": session_title or "",
            "updated_at": updated_at,
            "card_count": count,
            "favorite": document_id in favorite_set,
            "current": document_id == current_id,
            "snippet": snippet,
        })
    items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    favorite_items = [item for item in items if item["favorite"]]
    favorite_items.sort(key=lambda item: favorite_ids.index(item["document_id"]) if item["document_id"] in favorite_ids else 10_000)
    rest = [item for item in items if not item["favorite"]]
    ordered = favorite_items + rest
    return ordered[:limit]


def decorate_mutation_patch(
    patch: Mapping[str, Any],
    *,
    actor_ref: Mapping[str, Any],
    mutation_id: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Add explicit observation metadata to cards touched by a new mutation.

    The returned patch is a deep copy.  Existing explicit metadata wins; this
    preserves evidence supplied by a caller while making newly written cards
    auditable from the mutation's real actor and timestamp.
    """
    result = deepcopy(dict(patch))
    raw_cards = result.get("upsert_cards")
    if not isinstance(raw_cards, list):
        return result
    timestamp = _present_string(observed_at) or _utc_now()
    prepared: list[Any] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, Mapping):
            prepared.append(raw_card)
            continue
        card = dict(raw_card)
        material = dict(card.get("material")) if isinstance(card.get("material"), Mapping) else {}
        metadata = dict(material.get("metadata")) if isinstance(material.get("metadata"), Mapping) else {}
        if not isinstance(metadata.get("observation"), Mapping):
            source = metadata.get("evidence_source")
            if not isinstance(source, Mapping):
                source = card.get("source")
            if not isinstance(source, Mapping):
                source = {"kind": "mutation", "ref": mutation_id}
            metadata["observation"] = {
                "source": deepcopy(dict(source)),
                "observed_at": timestamp,
                "actor": deepcopy(dict(actor_ref)),
            }
        material["metadata"] = metadata
        card["material"] = material
        prepared.append(card)
    result["upsert_cards"] = prepared
    return result
