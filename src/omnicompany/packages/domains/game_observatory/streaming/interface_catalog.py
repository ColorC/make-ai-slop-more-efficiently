"""Human summary of the interface network, layered onto the recorded map.

The interface map records what was observed: thousands of visual states and
one transition row per distinct hop.  That is the right evidence store and the
wrong thing to read.  The catalog (``interface_catalog.v1.json``) names every
surface, puts it in a category, and says what it is and how automatic play
treats it; this module joins the two for the map page without touching the
recorded evidence.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

CATALOG_SCHEMA = "game-observatory.interface-catalog.v1"
SUMMARY_TRIGGER_LIMIT = 3


def load_catalog(path: Path) -> dict[str, Any] | None:
    """Read the catalog, or None when it is absent or not a catalog."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != CATALOG_SCHEMA:
        return None
    if not isinstance(payload.get("surfaces"), dict) or not isinstance(payload.get("categories"), list):
        return None
    return payload


def summarize_graph(snapshot: dict[str, Any], catalog: dict[str, Any] | None) -> dict[str, Any]:
    """Return the snapshot with catalog entries and one edge per surface pair.

    The recorded arrays are left as they are.  Added keys:

    ``catalog`` on each entity the catalog knows; ``categories``; and
    ``summary_edges`` -- every recorded transition between two different
    surfaces folded into a single edge with its hop count and most common
    triggers, so the page draws one line per pair instead of hundreds.
    """

    graph = dict(snapshot)
    surfaces = (catalog or {}).get("surfaces", {}) if catalog else {}
    entities = []
    for entity in snapshot.get("entities", []):
        entry = surfaces.get(entity.get("id")) if isinstance(surfaces, dict) else None
        if isinstance(entry, dict):
            entity = {**entity, "catalog": dict(entry)}
        entities.append(entity)
    graph["entities"] = entities
    graph["categories"] = list((catalog or {}).get("categories", [])) if catalog else []

    counts: Counter[tuple[str, str]] = Counter()
    interactive: Counter[tuple[str, str]] = Counter()
    frames: Counter[tuple[str, str]] = Counter()
    triggers: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for transition in snapshot.get("transitions", []):
        source = transition.get("source_entity_id")
        target = transition.get("target_entity_id")
        if not isinstance(source, str) or not isinstance(target, str) or source == target:
            continue
        pair = (source, target)
        # One row is one recorded way of making the hop.  The extra post frames
        # are the same landing seen again while the screen stayed put, so they
        # are evidence volume, not more hops.
        counts[pair] += 1
        frames[pair] += 1 + len((transition.get("evidence") or {}).get("additional_post_frame_ids", []) or [])
        if transition.get("mode") == "interactive":
            interactive[pair] += 1
        trigger = transition.get("trigger")
        if isinstance(trigger, str) and trigger:
            triggers[pair][trigger] += 1
    graph["summary_edges"] = [
        {
            "id": f"summary:{source}->{target}",
            "source_entity_id": source,
            "target_entity_id": target,
            "count": count,
            "interactive_count": interactive[(source, target)],
            "frames": frames[(source, target)],
            "top_triggers": [
                {"trigger": trigger, "count": hits}
                for trigger, hits in triggers[(source, target)].most_common(SUMMARY_TRIGGER_LIMIT)
            ],
        }
        for (source, target), count in counts.most_common()
    ]
    observed = {entity.get("id") for entity in entities}
    graph["catalog_unobserved"] = sorted(
        name for name in surfaces if name not in observed
    ) if isinstance(surfaces, dict) else []
    return graph


__all__ = ["CATALOG_SCHEMA", "load_catalog", "summarize_graph"]
