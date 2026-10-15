"""Extract the verified layer: what the survey and the fact stream actually show.

Each extractor keeps three layers apart.  Configuration statements come from the
decompiled tables, live verification comes from the fact stream and the survey
registration, and anything still guessed stays in the open-observation registry
with its own number.  Nothing here promotes a configuration value to "observed".
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Iterator

from .config_facts import ConfigFacts
from .localization import MISSING, hit_rate, lookup, scan_table, substitute_table
from .evidence import (
    BI_TRUTH,
    NAVIGATION_TRUTH,
    SAVE_PROJECTION_TRUTH,
    EvidenceBundle,
    ROUTING_TOKENS,
    unique_frames,
)

RUN_ID = re.compile(r"run\.romantic-restaurant\.[0-9A-Za-z]+\.[0-9a-f]+")
CJK = re.compile(r"[一-鿿]")
RECORDS_TRUTH = "docs/plans/game-observatory/[2026-09-05]ROMANTIC-RESTAURANT-FULL-DELEGATION-v1/records"
DASHBOARD_TRUTH = (
    "data/domains/game_observatory/game_models/romantic-restaurant/metrics/dashboard.json"
)


def _run_ids(value: Any) -> set[str]:
    return set(RUN_ID.findall(str(value)))


def _registry_by_run(evidence: EvidenceBundle) -> dict[str, list[dict[str, str]]]:
    """Index registry rows by the replay run identifier they cite."""

    index: dict[str, list[dict[str, str]]] = {}
    for entry in evidence.registry:
        for run_id in _run_ids(entry.get("首次出现 run / 帧 id", "")):
            index.setdefault(run_id, []).append(entry)
    return index


def _registry_numbers(entries: list[dict[str, str]]) -> list[str]:
    return [entry["编号"] for entry in entries]


def _registry_rows(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "编号": entry.get("编号", ""),
            "登记日期": entry.get("登记日期", ""),
            "观测描述": entry.get("观测描述", ""),
            "猜测归属": entry.get("猜测归属", ""),
            "处置": entry.get("处置（升格新类型 / 归入已有 / 只留原始层）", ""),
        }
        for entry in entries
    ]


def _routed_registry(evidence: EvidenceBundle, slug: str) -> list[dict[str, str]]:
    return _registry_rows(evidence.registry_for(ROUTING_TOKENS.get(slug, ())))


def _features(surface: dict[str, Any]) -> list[str]:
    return [str(item) for item in (surface.get("features") or [])]


def _anchor_count(surface: dict[str, Any]) -> int:
    signature = surface.get("signature") or {}
    anchors = signature.get("anchors") if isinstance(signature, dict) else None
    return len(anchors) if isinstance(anchors, dict) else 0


def _surfaces(evidence: EvidenceBundle) -> dict[str, Any]:
    surfaces = evidence.navigation.get("surfaces")
    return surfaces if isinstance(surfaces, dict) else {}


def _safety(evidence: EvidenceBundle) -> dict[str, Any]:
    safety = evidence.navigation.get("safety")
    return safety if isinstance(safety, dict) else {}


def _board_actions(evidence: EvidenceBundle) -> dict[str, Any]:
    actions = evidence.navigation.get("board_actions")
    return actions if isinstance(actions, dict) else {}


def mermaid_id(name: str) -> str:
    return "s_" + re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_")


def _resolve_surface(value: Any, known: list[str]) -> str | None:
    """Map a survey endpoint name onto a registered surface, or give up.

    Endpoints are written as prose in the survey ("black transition then
    story-dialogue"), so only exact names, registered prefixes and unambiguous
    completions resolve; everything else stays out of the diagram and is read
    from the transition table instead.
    """

    text = str(value or "")
    if text in known:
        return text
    for name in sorted(known, key=len, reverse=True):
        if text.startswith(name):
            return name
    completions = [name for name in known if name.startswith(text)] if text else []
    return completions[0] if len(completions) == 1 else None


# --------------------------------------------------------------------------- #
# 1. Interface flow network
# --------------------------------------------------------------------------- #


_PHASE_REPORT = re.compile(r"F\d-\d{2}")
# Control names that take a free grant: the two claim verbs the survey uses, the
# reward-panel continue button, and the dismiss tap on a celebration layer that
# registers no other control.  These are the four shapes the free-claim ruling
# has to disposition; matching the word "free" instead would file half the
# priced panels here, because their labels say "free" while the refill lasts.
_FREE_CLAIM_ELEMENT = ("claim", "open-gift", "safe-continue", "dismiss")
_WITHHELD_MARKERS = ("do-not-click", "not-used")


def _allowed_elements(surface: dict[str, Any]) -> list[str]:
    """Controls the survey lets a run touch: registered, not marked forbidden."""

    elements = surface.get("elements") or {}
    allowed = []
    for name in sorted(elements):
        entry = elements[name] if isinstance(elements[name], dict) else {}
        if entry.get("forbidden") is True or any(
            marker in name for marker in _WITHHELD_MARKERS
        ):
            continue
        if entry.get("usable") is False:
            continue
        allowed.append(name)
    return allowed


def _withheld_elements(surface: dict[str, Any]) -> list[str]:
    elements = surface.get("elements") or {}
    return sorted(
        name
        for name in elements
        if (isinstance(elements[name], dict) and elements[name].get("forbidden") is True)
        or any(marker in name for marker in _WITHHELD_MARKERS)
    )


def _anchor_key_forms(surface: dict[str, Any]) -> dict[str, int]:
    """How many signature anchors declare their colour under which key name."""

    anchors = ((surface.get("signature") or {}).get("anchors") or {}).values()
    forms: Counter[str] = Counter()
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        for key in ("average_rgb", "average_rgb_40x40"):
            if key in anchor:
                forms[key] += 1
    return dict(sorted(forms.items()))


def _prose_strings(node: Any) -> Iterator[str]:
    """Every string in a survey subtree, with file references removed.

    Report ids appear both in prose ("observed in F4-08") and inside replay
    paths ("F4-03a-replay/..."), and only the first kind says that a work item
    registered or re-checked this surface.  Path-shaped words are dropped word
    by word, so a sentence that quotes a frame path keeps its own prose.
    """

    if isinstance(node, dict):
        for value in node.values():
            yield from _prose_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _prose_strings(item)
    elif isinstance(node, str):
        yield " ".join(
            word
            for word in node.split()
            if "/" not in word and "replay" not in word and "\\" not in word
        )


def _cited_reports(surface: dict[str, Any], known: frozenset[str]) -> list[str]:
    cited: set[str] = set()
    for text in _prose_strings(surface):
        cited.update(name for name in _PHASE_REPORT.findall(text) if name in known)
    return sorted(cited)


def _loaded_report_ids(evidence: EvidenceBundle) -> frozenset[str]:
    """The work-item ids of the reports this generation actually read."""

    ids = set()
    for reference in evidence.report_sources.values():
        match = _PHASE_REPORT.search(reference.rsplit("/", 1)[-1])
        if match:
            ids.add(match.group(0))
    return frozenset(ids)


def _recent_registrations(evidence: EvidenceBundle) -> list[dict[str, Any]]:
    """Surfaces this phase registered or re-checked, with their allowed actions.

    Membership is decided by the survey entry itself: either it cites one of the
    phase reports in its own status, reasoning or evidence, or it carries a
    free-claim control, which is what the free-claim ruling had to disposition.
    Nothing here is a hand-kept list of names.
    """

    surfaces = _surfaces(evidence)
    forbidden = _safety(evidence).get("forbidden_surfaces") or {}
    known = _loaded_report_ids(evidence)
    rows = []
    for name in sorted(surfaces):
        surface = surfaces[name] if isinstance(surfaces[name], dict) else {}
        cited = _cited_reports(surface, known)
        elements = surface.get("elements") or {}
        free_claim = sorted(
            element
            for element in elements
            if any(token in element for token in _FREE_CLAIM_ELEMENT)
        )
        if not cited and not free_claim:
            continue
        entry = forbidden.get(name) if isinstance(forbidden.get(name), dict) else {}
        rows.append(
            {
                "surface": name,
                "kind": str(surface.get("kind") or ""),
                "status": str(surface.get("status") or ""),
                "cited_reports": cited,
                "forbidden": name in forbidden,
                "forbidden_reasons": [str(item) for item in (entry or {}).get("reasons") or []],
                "allowed_actions": _allowed_elements(surface),
                "withheld_actions": _withheld_elements(surface),
                "free_claim_controls": free_claim,
                "declared_allowed_actions": str(surface.get("allowed_actions") or ""),
                "anchor_count": _anchor_count(surface),
                "anchor_key_forms": _anchor_key_forms(surface),
                "evidence_frames": len(unique_frames(surface)),
            }
        )
    return rows


def extract_ui_flow_network(facts: ConfigFacts, evidence: EvidenceBundle) -> dict[str, Any]:
    surfaces = _surfaces(evidence)
    safety = _safety(evidence)
    forbidden = safety.get("forbidden_surfaces") or {}
    nodes = []
    for name in sorted(surfaces):
        surface = surfaces[name] if isinstance(surfaces[name], dict) else {}
        frames = unique_frames(surface)
        nodes.append(
            {
                "surface": name,
                "kind": str(surface.get("kind") or ""),
                "status": str(surface.get("status") or ""),
                "features": _features(surface),
                "anchor_count": _anchor_count(surface),
                "element_count": len(surface.get("elements") or {}),
                "evidence_frames": frames,
                "forbidden": name in forbidden,
            }
        )
    known = sorted(surfaces)
    transitions = []
    for item in evidence.navigation.get("transitions") or []:
        if not isinstance(item, dict):
            continue
        transitions.append(
            {
                "from": str(item.get("from") or ""),
                "action": str(item.get("action") or ""),
                "center": item.get("center"),
                "expected": str(item.get("expected") or ""),
                "actual": str(item.get("actual") or ""),
                "status": str(item.get("status") or ""),
                "resolved_source": _resolve_surface(item.get("from"), known),
                "resolved_target": _resolve_surface(item.get("actual"), known)
                or _resolve_surface(item.get("expected"), known),
            }
        )
    forbidden_rows = []
    for name in sorted(forbidden):
        entry = forbidden[name] if isinstance(forbidden[name], dict) else {}
        surface = surfaces.get(name) or {}
        close_name = str(entry.get("close_element") or "")
        close_element = (surface.get("elements") or {}).get(close_name) or {}
        forbidden_rows.append(
            {
                "surface": name,
                "reasons": [str(reason) for reason in entry.get("reasons") or []],
                "close_element": close_name,
                "close_center": close_element.get("center"),
                "detection": str(entry.get("detection") or ""),
            }
        )
    verified = [row for row in transitions if row["status"].startswith("verified")]
    refuted = [row for row in transitions if row["status"].startswith("refuted")]
    drawable = [
        row for row in transitions if row["resolved_source"] and row["resolved_target"]
    ]
    registrations = _recent_registrations(evidence)
    key_forms: Counter[str] = Counter()
    alternate_key_surfaces = []
    for name in sorted(surfaces):
        surface = surfaces[name] if isinstance(surfaces[name], dict) else {}
        forms = _anchor_key_forms(surface)
        for key, count in forms.items():
            key_forms[key] += count
        if "average_rgb" in forms:
            alternate_key_surfaces.append(
                {
                    "surface": name,
                    "anchors": forms.get("average_rgb"),
                    "forbidden": name in forbidden,
                }
            )
    return {
        "source_tables": [],
        "sources": [NAVIGATION_TRUTH],
        "evidence_level": "live_verified",
        "live_verified_rows": len(verified) + sum(1 for node in nodes if node["evidence_frames"]),
        "coordinate_space": evidence.navigation.get("coordinate_space") or {},
        "locale": (evidence.navigation.get("environment") or {}).get("locale", ""),
        "summary": {
            "surfaces": len(nodes),
            "transitions": len(transitions),
            "verified_transitions": len(verified),
            "refuted_transitions": len(refuted),
            "drawable_transitions": len(drawable),
            "forbidden_surfaces": len(forbidden_rows),
        },
        "nodes": nodes,
        "recent_registrations": registrations,
        "signature_anchor_keys": {
            "key_forms": dict(sorted(key_forms.items())),
            "alternate_key_surfaces": alternate_key_surfaces,
            "canonical_key": "average_rgb_40x40",
        },
        "transitions": transitions,
        "forbidden": forbidden_rows,
        "stop_surfaces": [str(item) for item in safety.get("stop_surfaces") or []],
        "policies": {
            "forbidden_surface_policy": str(safety.get("forbidden_surface_policy") or ""),
            "stop_surface_close_policy": str(safety.get("stop_surface_close_policy") or ""),
            "unrecognized_surface": str(safety.get("unrecognized_surface") or ""),
        },
        "route": {
            "goal": [str(item) for item in (evidence.navigation.get("route") or {}).get("goal") or []],
            "verified_prefix": [
                str(item)
                for item in (evidence.navigation.get("route") or {}).get("verified_prefix") or []
            ],
        },
        "registry": _routed_registry(evidence, "ui-flow-network"),
    }


# --------------------------------------------------------------------------- #
# 2. Onboarding and rule text
# --------------------------------------------------------------------------- #


TUTORIAL_TABLE = "TutorialConfig"

# Trailing digits are the step index inside one tutorial family (Beginning1 /
# Beginning2); the part before the first underscore is the family for names
# that qualify a family with a scene (HitBricks_west).  A base that still holds
# digits is kept whole, so pk1v1_1 stays pk1v1 instead of becoming pk1v.
_STEP_INDEX = re.compile(r"\d+$")


def executer_family(executer: str) -> str:
    head = str(executer).split("_", 1)[0]
    base = _STEP_INDEX.sub("", head)
    return base if base.isalpha() else head


def decode_condition(condition: str) -> list[dict[str, Any]]:
    """Split one ``condition`` cell into its clauses.

    The grammar is the one ``TutorialStartCondition:_Init`` implements: the cell
    is split on commas into clauses, each clause on the first hyphen into a
    condition type and its argument list, and the argument list on ``#``.
    """

    clauses = []
    for part in str(condition or "").split(","):
        text = part.strip()
        if not text:
            continue
        head, separator, tail = text.partition("-")
        clauses.append(
            {
                "clause": text,
                "type": head,
                "arguments": tail.split("#") if separator else [],
            }
        )
    return clauses


# How an unfinished step is expected to fire, phrased from its condition type
# alone.  This is an inference the document labels as such; nothing here is
# evidence that the step ever ran.
_TRIGGER_FORECAST = {
    "": "无条件，进入游戏后由所属系统的事件直接触发",
    "TutorialFinished": "在列出的前置教程全部完成之后",
    "MainLevel": "玩家等级达到该数值时",
    "FunctionEnabled": "对应功能开关被打开时",
    "CachedItemPushed": "缓存队列被推入指定棋子时",
    "TaskFinished": "列出的主线任务完成时",
    "BakeOutStart": "烘焙通关活动开启时",
    "CashDashStart": "CashDash 活动开启时",
}

# Which registered live artefact stands for which configuration row, and why.
# The key is the survey entry (kind, name); the basis is the readable reason a
# reviewer can re-check against the frame the survey already stores.
TUTORIAL_MATCHES: tuple[dict[str, str], ...] = (
    {
        "kind": "board_action",
        "name": "cobweb_unlock_tutorial",
        "tutorial_id": "tutorial_item_detail",
        "basis": "两步分别是「新咖啡机」角色气泡与信息按钮打开的物品详情面板，"
        "与该行说明「咖啡机&信息栏」逐项对应",
    },
    {
        "kind": "board_action",
        "name": "rave_reviews_event_tutorial",
        "tutorial_id": "tutorial_reviewRace_start",
        "basis": "引导目标是『好评如潮』笑脸进度角标，与该行说明「笑脸竞赛开始引导」对应",
    },
    {
        "kind": "board_action",
        "name": "bubble_item_tutorial",
        "tutorial_id": "tutorial_bubble",
        "basis": "引导目标是被泡泡包裹的棋子，与该行说明「泡泡」及条件 "
        "FunctionEnabled-bubble 对应",
    },
    {
        "kind": "surface",
        "name": "forced-shop-tutorial",
        "tutorial_id": "tutorial_shop",
        "basis": "帧上文案「商店现在开门了」，与该行说明「商城」对应；"
        "出现时机在升到 3 级之后，与条件 MainLevel-3 一致",
    },
    {
        "kind": "surface",
        "name": "energy-depleted-guide",
        "tutorial_id": "tutorial_energy",
        "basis": "角色气泡「我们的体力用完了！点击体力图标！」，与该行说明「能量不足」对应",
    },
)

_TUTORIAL_NAME_HINTS = ("tutorial", "guide")


def _is_tutorial_candidate(name: str, payload: dict[str, Any]) -> bool:
    text = f"{name} {payload.get('kind') or ''}".lower()
    return any(hint in text for hint in _TUTORIAL_NAME_HINTS)


def _tutorial_rows(facts: ConfigFacts) -> list[dict[str, Any]]:
    if not facts.has_table(TUTORIAL_TABLE):
        return []
    rows = []
    for row in facts.rows(TUTORIAL_TABLE):
        if not isinstance(row, dict):
            continue
        condition = str(row.get("condition") or "")
        executer = str(row.get("executer") or "")
        rows.append(
            {
                "tutorial_id": str(row.get("id") or ""),
                "desc": str(row.get("desc") or ""),
                "condition": condition,
                "executer": executer,
                "family": executer_family(executer),
                "clauses": decode_condition(condition),
            }
        )
    return rows


_MAX_CLAUSE_IDS = 8


def _condition_value_sets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every distinct condition clause, with the rows that reference it."""

    grouped: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        if not row["clauses"]:
            grouped.setdefault(("", ""), []).append(row["tutorial_id"])
            continue
        for clause in row["clauses"]:
            key = (str(clause["type"]), "#".join(str(arg) for arg in clause["arguments"]))
            grouped.setdefault(key, []).append(row["tutorial_id"])
    entries = []
    for (condition_type, arguments), ids in sorted(grouped.items()):
        unique = sorted(set(ids))
        entries.append(
            {
                "type": condition_type,
                "arguments": arguments,
                "reference_count": len(unique),
                # The full membership of a large clause is readable in the
                # complete table below; the clause table only shows a head.
                "tutorial_ids": unique[:_MAX_CLAUSE_IDS],
                "truncated": len(unique) > _MAX_CLAUSE_IDS,
                "forecast": _TRIGGER_FORECAST.get(condition_type, "条件类型未在反编译枚举内"),
            }
        )
    return entries


def _tutorial_save_state(evidence: EvidenceBundle) -> dict[str, Any]:
    projection = evidence.tutorial_state or {}
    names = {
        str(key): str(value) for key, value in (projection.get("state_names") or {}).items()
    }
    states = {}
    for row in projection.get("rows") or []:
        if isinstance(row, dict) and row.get("id") is not None:
            states[str(row["id"])] = names.get(str(row.get("state")), str(row.get("state")))
    return {
        "available": bool(projection),
        "source": (projection.get("save") or {}).get("source_ref", ""),
        "encrypted_sha256": (projection.get("save") or {}).get("encrypted_sha256", ""),
        "state_truth": str(projection.get("state_truth") or ""),
        "state_counts": dict(projection.get("state_counts") or {}),
        "snapshot_progress": dict(projection.get("snapshot_progress") or {}),
        "row_count": int(projection.get("row_count") or 0),
        "states": states,
    }


def table_localization(facts: ConfigFacts, table: str) -> dict[str, Any]:
    """Scan one named table against the unpacked text, substituting key columns."""

    texts = facts.localization()
    if not facts.has_table(table):
        return {
            "available": bool(texts),
            "table": table,
            "row_count": 0,
            "columns": [],
            "substitutable_fields": [],
            "substitutable_field_count": 0,
            "substituted": [],
            "substituted_count": 0,
        }
    return substitute_table(table, facts.fields(table), facts.rows(table), texts)


def extract_onboarding_and_rules(facts: ConfigFacts, evidence: EvidenceBundle) -> dict[str, Any]:
    chains = []
    for name, action in sorted(_board_actions(evidence).items()):
        if not isinstance(action, dict) or "steps" not in action:
            continue
        steps = []
        for step in action.get("steps") or []:
            if not isinstance(step, dict):
                continue
            signature = step.get("signature") or {}
            steps.append(
                {
                    "name": str(step.get("name") or ""),
                    "guided_target": step.get("guided_target"),
                    "target_semantic": str(step.get("target_semantic") or ""),
                    "anchor_count": len(signature.get("anchors") or {}),
                    "evidence_frame": str(step.get("evidence_frame") or ""),
                }
            )
        chains.append(
            {
                "chain": name,
                "event": str(action.get("event") or ""),
                "identified_as": [str(item) for item in action.get("identified_as") or []],
                "status": str(action.get("status") or ""),
                "allowed_action": str(action.get("allowed_action") or ""),
                "steps": steps,
            }
        )
    tutorial_surfaces = []
    for name, surface in sorted(_surfaces(evidence).items()):
        if not isinstance(surface, dict):
            continue
        kind = str(surface.get("kind") or "")
        if not any(hint in f"{kind} {name}".lower() for hint in _TUTORIAL_NAME_HINTS):
            continue
        elements = surface.get("elements") or {}
        tutorial_surfaces.append(
            {
                "surface": name,
                "kind": kind,
                "status": str(surface.get("status") or ""),
                "features": _features(surface),
                "dismiss_attempts": [
                    str(item) for item in surface.get("safe_dismiss_attempts") or []
                ],
                "disposition": str(surface.get("disposition") or ""),
                "allowed_actions": _allowed_elements(surface),
                "withheld_actions": _withheld_elements(surface),
                "verified_outcomes": [
                    {
                        "element": element,
                        "outcome": str((elements[element] or {}).get("verified_outcome") or ""),
                    }
                    for element in sorted(elements)
                    if isinstance(elements[element], dict)
                    and elements[element].get("verified_outcome")
                ],
                "evidence_frames": unique_frames(surface),
            }
        )
    free_refill = evidence.report("f4_06").get("free_refill_claim") or {}
    tutorial_free_claim = (
        {
            "surface": str(free_refill.get("surface") or ""),
            "claimed_once": free_refill.get("claimed_once"),
            "reason": str(free_refill.get("reason") or ""),
            "energy": [free_refill.get("energy_before"), free_refill.get("energy_after")],
            "gems": [free_refill.get("gems_before"), free_refill.get("gems_after")],
            "coins": [free_refill.get("coins_before"), free_refill.get("coins_after")],
            "evidence_frame_sha256": str(free_refill.get("evidence_frame_sha256") or ""),
            "config_refs": [str(item) for item in free_refill.get("config_refs") or []],
            "source": evidence.report_source("f4_06"),
        }
        if free_refill
        else {}
    )
    rule_texts = []
    for name, surface in sorted(_surfaces(evidence).items()):
        if not isinstance(surface, dict):
            continue
        frames = unique_frames(surface, limit=1)
        for feature in _features(surface):
            if CJK.search(feature):
                rule_texts.append(
                    {
                        "surface": name,
                        "text": feature,
                        "evidence_frame": frames[0] if frames else "",
                    }
                )
    observations = [
        {
            "fact_id": str(fact.get("fact_id") or ""),
            "observation": str((fact.get("after") or {}).get("observation") or ""),
            "key": str((fact.get("after") or {}).get("observation_key") or ""),
            "evidence_refs": [str(ref) for ref in fact.get("evidence_refs") or []],
        }
        for fact in evidence.facts_of_kind("gameplay_observation")
    ]
    step_rows = sum(len(chain["steps"]) for chain in chains)

    declared = _tutorial_rows(facts)
    by_id: dict[str, dict[str, Any]] = {}
    for row in declared:
        by_id.setdefault(row["tutorial_id"], row)
    families: Counter[str] = Counter(row["family"] for row in declared)
    duplicate_ids = sorted(
        identifier
        for identifier, count in Counter(row["tutorial_id"] for row in declared).items()
        if count > 1
    )
    save_state = _tutorial_save_state(evidence)

    # Where each live registration lives, so the match rows can quote the frame
    # the survey already stores instead of repeating a path by hand.
    live_frames: dict[tuple[str, str], list[str]] = {}
    for name, action in _board_actions(evidence).items():
        if isinstance(action, dict):
            live_frames[("board_action", name)] = unique_frames(action, limit=2)
    for name, surface in _surfaces(evidence).items():
        if isinstance(surface, dict):
            live_frames[("surface", name)] = unique_frames(surface, limit=2)

    matched_rows = []
    matched_ids = set()
    matched_keys = set()
    for match in TUTORIAL_MATCHES:
        key = (match["kind"], match["name"])
        if key not in live_frames or match["tutorial_id"] not in by_id:
            continue
        row = by_id[match["tutorial_id"]]
        matched_ids.add(row["tutorial_id"])
        matched_keys.add(key)
        matched_rows.append(
            {
                "live_kind": match["kind"],
                "live_name": match["name"],
                "tutorial_id": row["tutorial_id"],
                "executer": row["executer"],
                "desc": row["desc"],
                "condition": row["condition"],
                "basis": match["basis"],
                "evidence_frames": live_frames[key],
                "save_state": save_state["states"].get(row["tutorial_id"], "存档无该行"),
            }
        )
    matched_rows.sort(key=lambda item: item["tutorial_id"])

    unmatched_live = []
    for kind, source in (("board_action", _board_actions(evidence)), ("surface", _surfaces(evidence))):
        for name, payload in sorted(source.items()):
            if not isinstance(payload, dict):
                continue
            has_steps = bool(payload.get("steps"))
            if not (has_steps or _is_tutorial_candidate(name, payload)):
                continue
            if (kind, name) in matched_keys:
                continue
            unmatched_live.append(
                {
                    "live_kind": kind,
                    "live_name": name,
                    "status": str(payload.get("status") or ""),
                    "evidence_frames": live_frames.get((kind, name), []),
                }
            )

    save_finished = [
        row
        for row in declared
        if save_state["states"].get(row["tutorial_id"]) == "Finished"
        and row["tutorial_id"] not in matched_ids
    ]
    seen_finished: set[str] = set()
    save_rows = []
    for row in save_finished:
        if row["tutorial_id"] in seen_finished:
            continue
        seen_finished.add(row["tutorial_id"])
        save_rows.append(
            {
                "tutorial_id": row["tutorial_id"],
                "executer": row["executer"],
                "desc": row["desc"],
                "condition": row["condition"],
            }
        )

    untriggered = []
    for identifier, row in sorted(by_id.items()):
        if identifier in matched_ids:
            continue
        state = save_state["states"].get(identifier, "存档无该行")
        if state == "Finished":
            continue
        clause_types = [clause["type"] for clause in row["clauses"]] or [""]
        untriggered.append(
            {
                "tutorial_id": identifier,
                "executer": row["executer"],
                "family": row["family"],
                "desc": row["desc"],
                "condition": row["condition"],
                "save_state": state,
                "forecast": "；".join(
                    dict.fromkeys(
                        _TRIGGER_FORECAST.get(name, "条件类型未在反编译枚举内")
                        for name in clause_types
                    )
                ),
            }
        )

    foreign_save_ids = sorted(set(save_state["states"]) - set(by_id))
    return {
        "source_tables": [TUTORIAL_TABLE] if declared else [],
        "sources": (
            [NAVIGATION_TRUTH, RECORDS_TRUTH]
            + ([SAVE_PROJECTION_TRUTH] if save_state["available"] else [])
            + ([tutorial_free_claim["source"]] if tutorial_free_claim.get("source") else [])
        ),
        "evidence_level": "live_verified",
        "live_verified_rows": (
            step_rows + len(rule_texts) + len(observations) + len(matched_rows)
        ),
        "declared": {
            "table": TUTORIAL_TABLE,
            "row_count": len(declared),
            "unique_id_count": len(by_id),
            "duplicate_ids": duplicate_ids,
            "families": [
                {"family": name, "step_count": count} for name, count in sorted(families.items())
            ],
            "family_count": len(families),
            "rows": declared,
        },
        "conditions": _condition_value_sets(declared),
        "save_state": save_state,
        "matched": matched_rows,
        "unmatched_live": unmatched_live,
        "save_only_finished": save_rows,
        "untriggered": untriggered,
        "foreign_save_ids": foreign_save_ids,
        "chains": chains,
        "tutorial_surfaces": tutorial_surfaces,
        "tutorial_free_claim": tutorial_free_claim,
        "rule_texts": rule_texts,
        "observations": observations,
        "localization": table_localization(facts, TUTORIAL_TABLE),
        "registry": _routed_registry(evidence, "onboarding-and-rules"),
    }


# --------------------------------------------------------------------------- #
# 3. Monetization touchpoints
# --------------------------------------------------------------------------- #

_STORE_ROUTE_REASONS = ("app-store-update", "external-link")


def _touchpoint_category(
    name: str, surface: dict[str, Any], forbidden: dict[str, Any]
) -> str | None:
    """Classify by registered controls and red-line reasons, never by prose.

    Feature strings say things like "no price, no purchase control", so matching
    a price word inside them would file half the free surfaces as priced ones.
    The registered element names and the red-line reason list do not have that
    ambiguity.
    """

    elements = surface.get("elements") or {}
    reasons = [str(item) for item in (forbidden.get(name) or {}).get("reasons") or []]
    if surface.get("commercial_touchpoint") is True:
        return "real-money"
    if any(reason in _STORE_ROUTE_REASONS for reason in reasons):
        return "store-route"
    if any("price" in element for element in elements) or any(
        "price" in reason for reason in reasons
    ):
        return "soft-currency-price"
    if any("claim" in element or "open-gift" in element for element in elements):
        return "free-claim"
    return None


def _free_claim_policy(evidence: EvidenceBundle) -> dict[str, Any]:
    """The free-claim ruling, its per-surface disposition and its audit result.

    The rule text and the audit come from the dashboard product that already
    applies it; the per-surface verdict is recomputed here from the survey so a
    reader can see which registered control the verdict is about.
    """

    audit = (evidence.metrics or {}).get("free_claims") or {}
    policy = (evidence.report("f4_08").get("fixes_forced_by_the_device") or {}).get(
        "free_claim_policy"
    ) or {}
    deviation = evidence.report("f4_08").get("red_line_deviation") or {}
    surfaces = _surfaces(evidence)
    forbidden = _safety(evidence).get("forbidden_surfaces") or {}
    dispositions = []
    for name in sorted(surfaces):
        surface = surfaces[name] if isinstance(surfaces[name], dict) else {}
        elements = surface.get("elements") or {}
        for element in sorted(elements):
            if not any(token in element for token in _FREE_CLAIM_ELEMENT):
                continue
            entry = elements[element] if isinstance(elements[element], dict) else {}
            priced_catalog = isinstance(surface.get("sections"), dict) or bool(
                surface.get("priced_product_clicks") is not None
            )
            verified = bool(entry.get("verified_outcome"))
            in_forbidden = name in forbidden
            allowed = verified and not in_forbidden and not priced_catalog
            dispositions.append(
                {
                    "surface": name,
                    "element": element,
                    "verified_outcome": str(entry.get("verified_outcome") or ""),
                    "in_forbidden_surface": in_forbidden,
                    "priced_catalog": priced_catalog,
                    "verdict": "允许领取" if allowed else "只允许关闭",
                }
            )
    flagged = []
    for row in audit.get("flagged_rows") or []:
        if not isinstance(row, dict):
            continue
        verdict = row.get("verdict") or {}
        flagged.append(
            {
                "screen": str(row.get("screen") or row.get("identified_screen") or ""),
                "element": str(row.get("element") or ""),
                "kind": str(row.get("kind") or ""),
                "report": str(row.get("report") or ""),
                "before_policy": row.get("before_policy"),
                "era_basis": str(row.get("era_basis") or ""),
                "reasons": [str(item) for item in verdict.get("reasons") or []],
                "save_diff_available": (row.get("save_diff") or {}).get("available"),
            }
        )
    return {
        "available": bool(audit),
        "criterion": str(audit.get("criterion") or ""),
        "policy_source": str(audit.get("policy_source") or ""),
        "policy_effective_at": str(audit.get("policy_effective_at") or ""),
        "unconditional_grants": str(policy.get("unconditional_grants") or ""),
        "shop_gift": str(policy.get("shop_gift") or ""),
        "deviation": {
            "what": str(deviation.get("what") or ""),
            "against": str(deviation.get("against") or ""),
            "ruling": str(deviation.get("ruling") or ""),
            "redline_taps": deviation.get("redline_taps_on_money_ads_login_or_external"),
        },
        "dispositions": dispositions,
        "audit": {
            "events": audit.get("events"),
            "claims": audit.get("claims"),
            "closes": audit.get("closes"),
            "compliant": audit.get("compliant"),
            "claims_with_save_diff": audit.get("claims_with_save_diff"),
            "claims_without_save_diff": audit.get("claims_without_save_diff"),
            "flagged": audit.get("flagged"),
            "flagged_before_policy": audit.get("flagged_before_policy"),
            "flagged_after_policy": audit.get("flagged_after_policy"),
            "flagged_by_reason": dict(audit.get("flagged_by_reason") or {}),
            "surfaces_registered": audit.get("surfaces_registered"),
        },
        "flagged_rows": flagged,
        "source": DASHBOARD_TRUTH,
    }


def extract_monetization_touchpoints(
    facts: ConfigFacts, evidence: EvidenceBundle
) -> dict[str, Any]:
    surfaces = _surfaces(evidence)
    forbidden = _safety(evidence).get("forbidden_surfaces") or {}
    registry_index = _registry_by_run(evidence)
    touchpoints = []
    for name in sorted(surfaces):
        surface = surfaces[name] if isinstance(surfaces[name], dict) else {}
        features = _features(surface)
        elements = surface.get("elements") or {}
        category = _touchpoint_category(name, surface, forbidden)
        if category is None:
            continue
        frames = unique_frames(surface)
        matched: list[dict[str, str]] = []
        for frame in frames:
            for run_id in _run_ids(frame):
                for entry in registry_index.get(run_id, []):
                    if entry not in matched:
                        matched.append(entry)
        touchpoints.append(
            {
                "surface": name,
                "category": category,
                "status": str(surface.get("status") or ""),
                "features": features,
                "allowed_elements": sorted(
                    element
                    for element in elements
                    if "do-not-click" not in element and "not-used" not in element
                ),
                "withheld_elements": sorted(
                    element
                    for element in elements
                    if "do-not-click" in element or "not-used" in element
                ),
                "evidence_frames": frames,
                "registry": _registry_numbers(matched),
            }
        )
    catalog = []
    for name in sorted(surfaces):
        surface = surfaces[name] if isinstance(surfaces[name], dict) else {}
        sections = surface.get("sections")
        if not isinstance(sections, dict):
            continue
        for section_name in sorted(sections):
            section = sections[section_name] or {}
            for item in section.get("items") or []:
                catalog.append(
                    {
                        "surface": name,
                        "section": section_name,
                        "item": str(item),
                        "refresh": str(
                            section.get("refresh") or section.get("refresh_timer") or ""
                        ),
                    }
                )
    warehouse = _board_actions(evidence).get("warehouse") or {}
    storage = {
        "add_slot_cost_gems": warehouse.get("add_slot_cost_gems"),
        "add_slot_allowed": warehouse.get("add_slot_allowed"),
        "add_slot_control": warehouse.get("add_slot_control"),
        "status": str(warehouse.get("status") or ""),
    }
    free_claim = _free_claim_policy(evidence)
    return {
        "source_tables": ["ShopIAPConfigCN", "InventorySlotConfig"],
        "sources": [NAVIGATION_TRUTH, RECORDS_TRUTH]
        + ([free_claim["source"]] if free_claim.get("available") else []),
        "evidence_level": "live_verified",
        "live_verified_rows": len(touchpoints)
        + len(catalog)
        + len(free_claim.get("dispositions") or []),
        "free_claim": free_claim,
        "touchpoints": touchpoints,
        "catalog": catalog,
        "storage_expansion": storage,
        "click_discipline": {
            "priced_product_clicks": (surfaces.get("shop-daily-specials-and-hot-selling") or {}).get(
                "priced_product_clicks"
            ),
            "refresh_clicks": (surfaces.get("shop-daily-specials-and-hot-selling") or {}).get(
                "refresh_clicks"
            ),
        },
        "registry": _routed_registry(evidence, "monetization-touchpoints"),
    }


# --------------------------------------------------------------------------- #
# 4. Activity calendar
# --------------------------------------------------------------------------- #


def extract_activity_entry_map(facts: ConfigFacts, evidence: EvidenceBundle) -> dict[str, Any]:
    surfaces = _surfaces(evidence)
    activities = []
    for name, action in sorted(_board_actions(evidence).items()):
        if not isinstance(action, dict) or not action.get("event"):
            continue
        steps = action.get("steps") or []
        entry_step = steps[0] if steps and isinstance(steps[0], dict) else {}
        surface_name = next(
            (
                item
                for item in action.get("identified_as") or []
                if str(item) in surfaces
            ),
            "",
        )
        panels = sorted(
            item
            for item in surfaces
            if item.startswith(str(name).split("_")[0]) or "event" in item
        )
        activities.append(
            {
                "activity": str(action.get("event") or name),
                "chain": name,
                "status": str(action.get("status") or ""),
                "entry_control": entry_step.get("guided_target"),
                "entry_semantic": str(entry_step.get("target_semantic") or ""),
                "prompt_surface": surface_name,
                "panels": panels,
                "window": "未知（未观测）",
                "progress": next(
                    (
                        feature
                        for panel in panels
                        for feature in _features(surfaces.get(panel) or {})
                        if "progress" in feature
                    ),
                    "未知（未观测）",
                ),
                "reward": "未知（未观测）",
                "trigger": "未知（未观测）",
                "evidence_frames": [
                    {"source": f"教程链 {name}", "frame": frame}
                    for frame in unique_frames(action)
                ]
                + [
                    {"source": panel, "frame": frame}
                    for panel in panels
                    for frame in unique_frames(surfaces.get(panel) or {})
                ],
                "registry": _registry_numbers(
                    evidence.registry_for(("运营活动", "activity-entry-map"))
                ),
            }
        )
    return {
        "source_tables": [],
        "sources": [NAVIGATION_TRUTH, RECORDS_TRUTH],
        "evidence_level": "live_verified",
        "live_verified_rows": len(activities),
        "structure": ["活动", "起止", "进度", "奖励", "触发", "入口"],
        "activities": activities,
        "registry": _routed_registry(evidence, "activity-entry-map"),
    }


# --------------------------------------------------------------------------- #
# 5. Live progression curve
# --------------------------------------------------------------------------- #


def _main_task_rows(facts: ConfigFacts) -> dict[str, dict[str, Any]]:
    if not facts.has_table("MainTaskConfig"):
        return {}
    rows = {}
    for row in facts.rows("MainTaskConfig"):
        if isinstance(row, dict):
            rows[f"{row.get('day')}_{row.get('id')}"] = row
    return rows


def _level_rows(facts: ConfigFacts) -> dict[str, dict[str, Any]]:
    if not facts.has_table("LevelConfig"):
        return {}
    return {
        str(row.get("level")): row
        for row in facts.rows("LevelConfig")
        if isinstance(row, dict)
    }


_REPORT_STAMP = re.compile(r"(\d{8}T\d{6}Z)")


def _report_stamp(source: str) -> tuple[str, str]:
    """Sort key for one report reference: its own UTC stamp, then its name."""

    match = _REPORT_STAMP.search(source)
    return (match.group(1) if match else "", source)


def _projection_window(report: dict[str, Any], source: str) -> dict[str, Any]:
    """One recomputation of the days-to-target projection, as its report has it.

    Every input is named in the report itself: the two configuration costs, the
    recovery constant, and the coin yield measured over the report corpus.  The
    document repeats those inputs so a reader can redo the division.
    """

    inputs = report.get("inputs") or {}
    observed = report.get("observed") or {}
    target = report.get("target") or {}
    next_task = report.get("next_task") or {}
    return {
        "available": True,
        "source": source,
        "formula": str(report.get("formula") or ""),
        "inputs": {
            "from_task": inputs.get("from_task"),
            "to_task": inputs.get("to_task"),
            "coins_on_hand": inputs.get("coins_on_hand"),
            "level": inputs.get("level"),
            "xp": inputs.get("xp"),
            "energy_recovery_seconds": inputs.get("energy_recovery_seconds"),
            "config_refs": [str(item) for item in inputs.get("config_refs") or []],
        },
        "observed": {
            "reports_used": observed.get("reports_used"),
            "energy_spent_confirmed": observed.get("energy_spent_confirmed"),
            "delivery_coins_confirmed": observed.get("delivery_coins_confirmed"),
            "confirmed_deliveries": observed.get("confirmed_deliveries"),
            "coins_per_energy": observed.get("coins_per_energy"),
        },
        "energy_per_day": report.get("energy_per_day"),
        "coins_per_day": report.get("coins_per_day"),
        "next_task": {
            "task": next_task.get("task"),
            "cost": next_task.get("cost"),
            "coins_short": next_task.get("coins_short"),
            "days": next_task.get("days"),
        },
        "target": {
            "task": target.get("task"),
            "tasks_inclusive": target.get("tasks_inclusive"),
            "coins_needed": target.get("coins_needed"),
            "coins_short": target.get("coins_short"),
            "days": target.get("days"),
            "task_exp_granted": target.get("task_exp_granted"),
            "level_on_arrival": target.get("level_on_arrival"),
            "xp_into_that_level": target.get("xp_into_that_level"),
        },
    }


def _progress_projection(evidence: EvidenceBundle) -> dict[str, Any]:
    """Every recomputation of the projection, newest first.

    The projection is recomputed whenever the corpus grows, and the later run
    is not a correction of the earlier one: they are the same formula over
    different sample sizes.  Both stay on the page, ordered by the timestamp in
    their own file name, so the reader can see how the estimate moved and on how
    many reports each number rests.
    """

    candidates = [
        (evidence.report_source(name), evidence.report(name))
        for name in ("f4_09_projection", "f4_08_projection")
    ]
    windows = [
        _projection_window(report, source) for source, report in candidates if report
    ]
    if not windows:
        embedded = evidence.report("f4_08").get("progress_projection") or {}
        if embedded:
            windows = [_projection_window(embedded, evidence.report_source("f4_08"))]
    if not windows:
        return {"available": False, "windows": []}
    windows.sort(key=lambda window: _report_stamp(window["source"]), reverse=True)
    latest = windows[0]
    return {
        "available": True,
        "source": latest["source"],
        "formula": latest["formula"],
        "inputs": latest["inputs"],
        "observed": latest["observed"],
        "energy_per_day": latest["energy_per_day"],
        "coins_per_day": latest["coins_per_day"],
        "next_task": latest["next_task"],
        "target": latest["target"],
        "windows": windows,
    }


def extract_progression_live(facts: ConfigFacts, evidence: EvidenceBundle) -> dict[str, Any]:
    declared = _main_task_rows(facts)
    levels = _level_rows(facts)
    main_task_facts = sorted(
        evidence.facts_of_kind("main_task"), key=lambda item: str(item.get("captured_at"))
    )
    steps = []
    sequence: list[Any] = []
    for fact in main_task_facts:
        before = fact.get("before") or {}
        after = fact.get("after") or {}
        delta = fact.get("delta") or {}
        task_id = str(before.get("main_task_id") or "")
        config_row = declared.get(task_id) or {}
        observed_cost = -int(delta.get("coins") or 0)
        declared_cost = config_row.get("cost")
        sequence.append(before.get("main_task_cost"))
        steps.append(
            {
                "task": task_id,
                "name": str(config_row.get("name") or ""),
                "declared_cost": declared_cost,
                "displayed_cost": before.get("main_task_cost"),
                "observed_coin_cost": observed_cost,
                "consistent": declared_cost == observed_cost,
                "captured_at": str(fact.get("captured_at") or ""),
                "level_before": before.get("level"),
                "level_after": after.get("level"),
                "energy_before": before.get("energy"),
                "energy_after": after.get("energy"),
                "rewards": [str(item) for item in config_row.get("rewards") or []],
                "fact_id": str(fact.get("fact_id") or ""),
                "evidence_source": "facts",
            }
        )
    if main_task_facts:
        tail = main_task_facts[-1].get("after") or {}
        tail_id = str(tail.get("main_task_id") or "")
        tail_row = declared.get(tail_id) or {}
        sequence.append(tail.get("main_task_cost"))
        steps.append(
            {
                "task": tail_id,
                "name": str(tail_row.get("name") or ""),
                "declared_cost": tail_row.get("cost"),
                "displayed_cost": tail.get("main_task_cost"),
                "observed_coin_cost": None,
                "consistent": None,
                "captured_at": "",
                "level_before": tail.get("level"),
                "level_after": None,
                "energy_before": tail.get("energy"),
                "energy_after": None,
                "rewards": [str(item) for item in tail_row.get("rewards") or []],
                "fact_id": "",
                "evidence_source": "记录层（长跑记录），facts 未收录结算",
            }
        )
    level_steps = []
    for fact in main_task_facts:
        before = fact.get("before") or {}
        after = fact.get("after") or {}
        if before.get("level") == after.get("level"):
            continue
        source_level = str(before.get("level"))
        config_row = levels.get(source_level) or {}
        task_row = declared.get(str(before.get("main_task_id") or "")) or {}
        gained = 0
        for reward in task_row.get("rewards") or []:
            match = re.fullmatch(r"exp-([0-9]+)", str(reward))
            if match:
                gained = int(match.group(1))
        level_steps.append(
            {
                "from_level": before.get("level"),
                "to_level": after.get("level"),
                "declared_cost": config_row.get("cost"),
                "xp_before": before.get("xp"),
                "task_exp_reward": gained,
                "xp_after": after.get("xp"),
                "matches_declared_cost": config_row.get("cost")
                == (before.get("xp") or 0) + gained,
                "fact_id": str(fact.get("fact_id") or ""),
            }
        )
    runs: dict[str, dict[str, Any]] = {}
    for fact in sorted(evidence.facts, key=lambda item: str(item.get("captured_at"))):
        report = str(fact.get("source_report") or "")
        before = fact.get("before") or {}
        after = fact.get("after") or {}
        bucket = runs.setdefault(
            report,
            {
                "report": report,
                "first_seen": str(fact.get("captured_at") or ""),
                "last_seen": "",
                "fact_count": 0,
                "coins_first": None,
                "coins_last": None,
                "energy_min": None,
                "energy_max": None,
            },
        )
        bucket["fact_count"] += 1
        bucket["last_seen"] = str(fact.get("captured_at") or "")
        for source in (before, after):
            coins = source.get("coins")
            if isinstance(coins, int):
                if bucket["coins_first"] is None:
                    bucket["coins_first"] = coins
                bucket["coins_last"] = coins
            energy = source.get("energy")
            if isinstance(energy, int):
                bucket["energy_min"] = (
                    energy if bucket["energy_min"] is None else min(bucket["energy_min"], energy)
                )
                bucket["energy_max"] = (
                    energy if bucket["energy_max"] is None else max(bucket["energy_max"], energy)
                )
    projection = _progress_projection(evidence)
    declared_head = [
        {
            "task": key,
            "name": str(row.get("name") or ""),
            "cost": row.get("cost"),
            "rewards": [str(item) for item in row.get("rewards") or []],
            "detail_icon": str(row.get("detail_icon") or ""),
        }
        for key, row in sorted(
            ((key, row) for key, row in declared.items() if str(row.get("day")) in {"1", "2"}),
            key=lambda item: (int(item[1].get("day") or 0), int(item[1].get("id") or 0)),
        )
    ]
    return {
        "source_tables": ["MainTaskConfig", "LevelConfig"],
        "sources": [RECORDS_TRUTH]
        + [window["source"] for window in projection.get("windows") or [] if window["source"]],
        "evidence_level": "live_verified",
        "live_verified_rows": len([step for step in steps if step["fact_id"]]) + len(level_steps),
        "projection": projection,
        "declared_head": declared_head,
        "steps": steps,
        "cost_sequence": sequence,
        "level_steps": level_steps,
        "runs": [runs[key] for key in sorted(runs)],
        "long_run": [dict(row) for row in evidence.long_run],
        "registry": _routed_registry(evidence, "progression-live"),
    }


# --------------------------------------------------------------------------- #
# 6. Telemetry / BI event declarations
# --------------------------------------------------------------------------- #

_FUNCTION = re.compile(r"^function\s+BIManager[.:](\w+)")
_KEY = re.compile(r"^(\w+)\s*=")
_STRING_VALUE = re.compile(r'^\w+\s*=\s*"([^"]*)"')


def _table_literal(lines: list[str], start: int) -> tuple[list[str], dict[str, str], int]:
    """Read one brace-balanced Lua table literal, returning its top-level keys."""

    keys: list[str] = []
    literals: dict[str, str] = {}
    depth = 0
    index = start
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if depth == 1:
            match = _KEY.match(stripped)
            if match:
                keys.append(match.group(1))
                value = _STRING_VALUE.match(stripped)
                if value:
                    literals[match.group(1)] = value.group(1)
        depth += line.count("{") - line.count("}")
        if depth <= 0 and index > start:
            break
        index += 1
    return keys, literals, index


def parse_bi_events(source: str) -> dict[str, Any]:
    """Read the event declarations out of the decompiled telemetry manager."""

    lines = source.splitlines()
    events: list[dict[str, Any]] = []
    bag_fields: list[str] = []
    envelope_fields: list[str] = []
    device_fields: list[str] = []
    current = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        function = _FUNCTION.match(line)
        if function:
            current = function.group(1)
        stripped = line.strip()
        if stripped.startswith("local dict = {") and current:
            keys, literals, end = _table_literal(lines, index)
            if "event" in literals:
                events.append(
                    {
                        "function": current,
                        "event": literals["event"],
                        "fields": keys,
                        "constants": {
                            key: value for key, value in literals.items() if key != "event"
                        },
                    }
                )
            index = end
        elif stripped.startswith("local bagDict = {"):
            bag_fields, _, index = _table_literal(lines, index)
        index += 1
    in_append = False
    device_depth: int | None = None
    for line in lines:
        function = _FUNCTION.match(line)
        if function:
            in_append = function.group(1) == "_AppendBIInfo"
        if not in_append:
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("if withDeviceInfo then"):
            device_depth = indent
            continue
        if device_depth is not None and stripped == "end" and indent == device_depth:
            device_depth = None
            continue
        match = re.match(r"^dict\.(\w+)\s*=", stripped)
        if not match:
            continue
        if device_depth is not None:
            device_fields.append(match.group(1))
        else:
            envelope_fields.append(match.group(1))
    return {
        "events": sorted(events, key=lambda item: (item["event"], item["function"])),
        "bag_fields": list(dict.fromkeys(bag_fields)),
        "envelope_fields": list(dict.fromkeys(envelope_fields)),
        "device_fields": list(dict.fromkeys(device_fields)),
    }


CORE_EVENTS = ("acquire", "useitem", "action", "pay")


def extract_telemetry_bi_events(facts: ConfigFacts, evidence: EvidenceBundle) -> dict[str, Any]:
    parsed = parse_bi_events(evidence.bi_source) if evidence.bi_source else {
        "events": [],
        "bag_fields": [],
        "envelope_fields": [],
        "device_fields": [],
    }
    return {
        "source_tables": [],
        "sources": [BI_TRUTH],
        "evidence_level": "decompiled_code",
        "live_verified_rows": 0,
        "capture_status": "未实机截获（B 路线搁置）",
        "core_events": list(CORE_EVENTS),
        **parsed,
        "registry": _routed_registry(evidence, "telemetry-bi-events"),
    }


# --------------------------------------------------------------------------- #
# 7. Story script
# --------------------------------------------------------------------------- #

_STORY_SURFACES = ("story-cutscene", "story-dialogue", "board-story-prompt")
_DECORATION_SURFACES = ("decoration-choice", "decoration-confirmation")

RECAP_TABLE = "StoryRecapConfig"
EXPLORE_TABLE = "ExploreStoryConfig_icyforestmapexplore"
SPEAKER_TABLE = "StorySpeakerConfig"
_TASK_KEY = re.compile(r"^(\d+)_(\d+)$")

# StoryRecapModel:GetUnlockBookList compares the current main task against the
# book's own unlock task, so a book is readable only once the player is past it.
RECAP_UNLOCK_TRUTH = "synthesis/lua_code/Lua-Project/StoryRecapModel.lua"
# MapExploreTLPlotExecuter renders one Content cell as GameTextModel:GetText,
# which is the localized-key lookup, not the text itself.
EXPLORE_TEXT_TRUTH = "synthesis/lua_code/Lua-Project/MapExploreTLPlotExecuter.lua"


def _task_key(value: Any) -> tuple[int, int] | None:
    match = _TASK_KEY.fullmatch(str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _recap_books(facts: ConfigFacts, texts: dict[str, str]) -> dict[str, Any]:
    if not facts.has_table(RECAP_TABLE):
        return {"table": RECAP_TABLE, "row_count": 0, "books": [], "pages": [], "lines": []}
    rows = [row for row in facts.rows(RECAP_TABLE) if isinstance(row, dict)]
    books: dict[Any, dict[str, Any]] = {}
    pages: dict[tuple[Any, Any], dict[str, Any]] = {}
    lines: list[dict[str, Any]] = []
    for row in rows:
        book = row.get("Book")
        page = row.get("Page")
        entry = books.setdefault(
            book,
            {
                "book": book,
                "unlock_task": str(row.get("Task") or ""),
                "row_count": 0,
                "types": Counter(),
                "pages": set(),
            },
        )
        entry["row_count"] += 1
        entry["types"][str(row.get("Type") or "")] += 1
        entry["pages"].add(page)
        page_entry = pages.setdefault(
            (book, page),
            {"book": book, "page": page, "title": "", "subtitle": "", "types": Counter()},
        )
        kind = str(row.get("Type") or "")
        page_entry["types"][kind] += 1
        if kind == "title":
            page_entry["title"] = str(row.get("Content") or "")
        elif kind == "subtitle":
            page_entry["subtitle"] = str(row.get("Content") or "")
        # ``img`` rows carry an image asset name, not a localization key, so
        # they are counted apart from the text-bearing rows below.
        key = str(row.get("Content") or "")
        lines.append(
            {
                "book": book,
                "page": page,
                "type": kind,
                "key": key,
                "text": lookup(texts, key),
                "text_bearing": kind != "img",
            }
        )
    ordered_books = [
        {
            "book": entry["book"],
            "unlock_task": entry["unlock_task"],
            "unlock_task_order": _task_key(entry["unlock_task"]),
            "row_count": entry["row_count"],
            "page_count": len(entry["pages"]),
            "type_counts": dict(sorted(entry["types"].items())),
        }
        for entry in sorted(books.values(), key=lambda item: _sort_value(item["book"]))
    ]
    ordered_pages = [
        {
            "book": entry["book"],
            "page": entry["page"],
            "title_key": entry["title"],
            "title_text": lookup(texts, entry["title"]),
            "subtitle_key": entry["subtitle"],
            "subtitle_text": lookup(texts, entry["subtitle"]),
            "row_count": sum(entry["types"].values()),
            "type_counts": dict(sorted(entry["types"].items())),
        }
        for entry in sorted(
            pages.values(), key=lambda item: (_sort_value(item["book"]), _sort_value(item["page"]))
        )
    ]
    ordered_lines = sorted(
        lines,
        key=lambda item: (
            _sort_value(item["book"]),
            _sort_value(item["page"]),
            item["type"],
            item["key"],
        ),
    )
    text_bearing = [line for line in ordered_lines if line["text_bearing"]]
    resolved = [line for line in text_bearing if line["text"] is not None]
    return {
        "table": RECAP_TABLE,
        "row_count": len(rows),
        "book_count": len(ordered_books),
        "group_count": len({(row.get("Book"), row.get("Task")) for row in rows}),
        "type_counts": dict(sorted(Counter(str(row.get("Type") or "") for row in rows).items())),
        "books": ordered_books,
        "pages": ordered_pages,
        "lines": ordered_lines,
        "text_bearing_row_count": len(text_bearing),
        "resolved_row_count": len(resolved),
        "resolved_rate_percent": hit_rate(len(resolved), len(text_bearing)),
        "all_row_resolved_rate_percent": hit_rate(len(resolved), len(ordered_lines)),
        "unresolved_keys": sorted(
            {line["key"] for line in text_bearing if line["text"] is None}
        ),
        "non_text_row_count": len(ordered_lines) - len(text_bearing),
    }


def _sort_value(value: Any) -> tuple[int, float, str]:
    try:
        return (0, float(value), "")
    except (TypeError, ValueError):
        return (1, 0.0, str(value))


def _explore_dialogues(facts: ConfigFacts, texts: dict[str, str]) -> dict[str, Any]:
    if not facts.has_table(EXPLORE_TABLE):
        return {"table": EXPLORE_TABLE, "row_count": 0, "dialogues": [], "lines": []}
    rows = [row for row in facts.rows(EXPLORE_TABLE) if isinstance(row, dict)]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("Id") or ""), []).append(row)
    dialogues = []
    for identifier, members in sorted(grouped.items(), key=lambda item: _sort_value(item[0])):
        ordered = sorted(members, key=lambda row: _sort_value(row.get("Step")))
        dialogues.append(
            {
                "dialogue_id": identifier,
                "step_count": len(ordered),
                "steps": [row.get("Step") for row in ordered],
                "speakers": sorted({str(row.get("Speaker") or "") for row in ordered}),
                "positions": sorted({row.get("Position") for row in ordered}, key=_sort_value),
                "animations": sorted({str(row.get("Animation") or "") for row in ordered}),
                "bubble_types": sorted({str(row.get("BubbleType") or "") for row in ordered}),
                "content_keys": [str(row.get("Content") or "") for row in ordered],
            }
        )
    lines = [
        {
            "dialogue_id": str(row.get("Id") or ""),
            "step": row.get("Step"),
            "speaker": str(row.get("Speaker") or ""),
            "speaker_name": lookup(texts, f"{row.get('Speaker')}_name"),
            "key": str(row.get("Content") or ""),
            "text": lookup(texts, row.get("Content")),
        }
        for row in sorted(
            rows, key=lambda item: (_sort_value(item.get("Id")), _sort_value(item.get("Step")))
        )
    ]
    resolved = [line for line in lines if line["text"] is not None]
    return {
        "table": EXPLORE_TABLE,
        "row_count": len(rows),
        "dialogue_count": len(dialogues),
        "lines": lines,
        "resolved_row_count": len(resolved),
        "resolved_rate_percent": hit_rate(len(resolved), len(lines)),
        "unresolved_keys": sorted({line["key"] for line in lines if line["text"] is None}),
        "speaker_counts": dict(
            sorted(Counter(str(row.get("Speaker") or "") for row in rows).items())
        ),
        "position_counts": dict(
            sorted(Counter(str(row.get("Position")) for row in rows).items())
        ),
        "animation_counts": dict(
            sorted(Counter(str(row.get("Animation") or "") for row in rows).items())
        ),
        "bubble_type_counts": dict(
            sorted(Counter(str(row.get("BubbleType") or "") for row in rows).items())
        ),
        "dialogues": dialogues,
    }


def _speaker_names(facts: ConfigFacts) -> list[str]:
    if not facts.has_table(SPEAKER_TABLE):
        return []
    names: set[str] = set()
    for row in facts.rows(SPEAKER_TABLE):
        if isinstance(row, dict):
            names.update(str(key) for key in row)
    return sorted(names)


def _speaker_check(
    facts: ConfigFacts, explore: dict[str, Any], texts: dict[str, str]
) -> dict[str, Any]:
    declared = _speaker_names(facts)
    used = sorted(explore.get("speaker_counts") or {})
    missing = sorted(set(used) - set(declared))
    # MapExploreTLPlotExecuter reads the displayed name as GetText(Speaker .. "_name").
    named = [
        {"speaker": name, "name_key": f"{name}_name", "name_text": lookup(texts, f"{name}_name")}
        for name in declared
    ]
    resolved = [entry for entry in named if entry["name_text"] is not None]
    return {
        "declared_table": SPEAKER_TABLE,
        "declared_count": len(declared),
        "used_speakers": used,
        "missing_from_declaration": missing,
        "subset": not missing and bool(used),
        "names": named,
        "named_count": len(resolved),
        "named_rate_percent": hit_rate(len(resolved), len(named)),
        "unnamed_speakers": [
            entry["speaker"] for entry in named if entry["name_text"] is None
        ],
    }


def _packaged_story(facts: ConfigFacts) -> dict[str, Any]:
    """Summarize what the Addressables unpack actually obtained, prefix by prefix."""

    index = facts.packaged_story()
    prefixes = index.get("prefixes") if isinstance(index.get("prefixes"), list) else []
    entries = []
    for entry in prefixes:
        if not isinstance(entry, dict):
            continue
        assets = entry.get("assets") if isinstance(entry.get("assets"), dict) else {}
        statuses = sorted(
            {
                str(asset.get("status") or "")
                for asset in assets.values()
                if isinstance(asset, dict)
            }
        )
        entries.append(
            {
                "prefix": str(entry.get("prefix") or ""),
                "origin": str(entry.get("origin") or ""),
                "asset_count": len(assets),
                "bytes": sum(
                    int(asset.get("bytes") or 0)
                    for asset in assets.values()
                    if isinstance(asset, dict)
                ),
                "status": statuses[0] if len(statuses) == 1 else "/".join(statuses),
                "parsed_sentences": int(entry.get("parsed_sentences") or 0),
            }
        )
    return {
        "available": bool(index),
        "catalog_prefix_count": index.get("catalog_prefix_count"),
        "packaged_prefix_count": index.get("packaged_prefix_count"),
        "cdn_cached_prefix_count": index.get("cdn_cached_prefix_count"),
        "obtained_prefix_count": index.get("obtained_prefix_count"),
        "not_obtained_prefix_count": index.get("not_obtained_prefix_count"),
        "not_obtained_prefixes": index.get("not_obtained_prefixes") or [],
        "encrypted_header_hex": index.get("encrypted_header_hex"),
        "encrypted_header_bytes": index.get("encrypted_header_bytes"),
        "plaintext_asset_count": index.get("plaintext_asset_count"),
        "encrypted_asset_count": index.get("encrypted_asset_count"),
        "parsed_sentence_count": index.get("parsed_sentence_count"),
        "prefixes": entries,
    }


def _localization_status(facts: ConfigFacts, texts: dict[str, str]) -> dict[str, Any]:
    """State where the text came from and which columns it can fill."""

    index = facts.localization_index()
    scans = [
        scan_table(name, facts.fields(name), facts.rows(name), texts)
        for name in (RECAP_TABLE, EXPLORE_TABLE)
        if facts.has_table(name)
    ]
    return {
        "available": bool(texts),
        "language": "cn",
        "source_asset": "Assets/Text/GameText/game_text_cn.json",
        "bundle_entry": index.get("bundle_entry"),
        "key_count": index.get("key_count") or len(texts),
        "language_count": index.get("language_count"),
        "catalog_language_count": index.get("catalog_language_count"),
        "keys_identical_across_languages": index.get("keys_identical_across_languages"),
        "complete_languages": index.get("complete_languages") or [],
        "incomplete_languages": index.get("incomplete_languages") or [],
        "language_shortfall": [
            {
                "language": record.get("language"),
                "key_count": record.get("key_count"),
                "missing_vs_reference": record.get("missing_vs_reference"),
            }
            for record in index.get("languages") or []
            if record.get("missing_vs_reference")
        ],
        "scans": scans,
    }


def _text_status(
    localization: dict[str, Any],
    recap: dict[str, Any],
    explore: dict[str, Any],
    packaged: dict[str, Any],
) -> str:
    """Say in one sentence how much of the text this run actually holds."""

    if not localization.get("available"):
        return (
            "两张剧情表的 Content 是本地化键，包内文本尚未解包，"
            "本次生成没有可换出的正文"
        )
    return (
        f"回顾表 {recap.get('resolved_row_count')}/{recap.get('text_bearing_row_count')} 条"
        f"正文已换出（另有 {recap.get('non_text_row_count')} 条 img 行不是文本键），"
        f"探险对话 {explore.get('resolved_row_count')}/{explore.get('row_count')} 句已换出；"
        f"随包剧情脚本 {packaged.get('obtained_prefix_count')}/"
        f"{packaged.get('catalog_prefix_count')} 个前缀已取得但全部加密，"
        f"逐句脚本 {packaged.get('parsed_sentence_count')} 句"
    )


def extract_story_script(facts: ConfigFacts, evidence: EvidenceBundle) -> dict[str, Any]:
    surfaces = _surfaces(evidence)
    safety = _safety(evidence)
    scenes = []
    for name in _STORY_SURFACES + _DECORATION_SURFACES:
        surface = surfaces.get(name)
        if not isinstance(surface, dict):
            continue
        variants = [
            {
                "name": str(variant.get("name") or ""),
                "frame": str(variant.get("screenshot_path") or ""),
            }
            for variant in surface.get("observed_variants") or []
            if isinstance(variant, dict)
        ]
        scenes.append(
            {
                "surface": name,
                "status": str(surface.get("status") or ""),
                "features": _features(surface),
                "advance_elements": sorted(surface.get("elements") or {}),
                "variants": variants,
                "evidence_frames": unique_frames(surface),
            }
        )
    characters: Counter[str] = Counter()
    if facts.has_table("MainTaskConfig"):
        for row in facts.rows("MainTaskConfig"):
            if isinstance(row, dict) and row.get("detail_icon"):
                characters[str(row["detail_icon"])] += 1
    decoration = surfaces.get("decoration-choice") or {}
    texts = facts.localization()
    recap = _recap_books(facts, texts)
    explore = _explore_dialogues(facts, texts)
    speakers = _speaker_check(facts, explore, texts)
    localization = _localization_status(facts, texts)
    packaged = _packaged_story(facts)

    settled = [
        str((fact.get("before") or {}).get("main_task_id") or "")
        for fact in evidence.facts_of_kind("main_task")
    ]
    reached = ""
    for fact in evidence.facts_of_kind("main_task"):
        reached = str((fact.get("after") or {}).get("main_task_id") or "") or reached
    reached_order = _task_key(reached)
    for book in recap["books"]:
        unlock = book["unlock_task_order"]
        book["unlocked"] = bool(reached_order and unlock and reached_order > unlock)
        book["live_gap_days"] = (
            unlock[0] - reached_order[0] if reached_order and unlock else None
        )
    unlocked_books = [book for book in recap["books"] if book.get("unlocked")]

    source_tables = ["MainTaskConfig"]
    for name in (RECAP_TABLE, EXPLORE_TABLE, SPEAKER_TABLE):
        if facts.has_table(name):
            source_tables.append(name)
    return {
        "source_tables": source_tables,
        "sources": [NAVIGATION_TRUTH, RECORDS_TRUTH, RECAP_UNLOCK_TRUTH, EXPLORE_TEXT_TRUTH],
        "evidence_level": "live_partial",
        "live_verified_rows": (
            sum(1 for scene in scenes if scene["evidence_frames"]) + len(unlocked_books)
        ),
        "recap": recap,
        "explore": explore,
        "speaker_check": speakers,
        "localization": localization,
        "packaged_story": packaged,
        "live_story_progress": {
            "settled_tasks": [task for task in settled if task],
            "current_task": reached,
            "current_task_order": reached_order,
            "unlocked_book_count": len(unlocked_books),
            "unlocked_books": [book["book"] for book in unlocked_books],
            "earliest_unlock_task": (
                recap["books"][0]["unlock_task"] if recap["books"] else ""
            ),
        },
        "scenes": scenes,
        "characters": [
            {"character": name, "task_count": count}
            for name, count in sorted(characters.items(), key=lambda item: (-item[1], item[0]))
        ],
        "decoration_choice": {
            "selection_policy": str(decoration.get("selection_policy") or ""),
            "declared_policy": str(safety.get("decoration_choice") or ""),
            "options": sorted(decoration.get("elements") or {}),
        },
        "advance_policy": {
            "story_continue_limit": safety.get("story_continue_limit"),
            "main_task_start_limit": safety.get("main_task_start_limit"),
        },
        "text_status": _text_status(localization, recap, explore, packaged),
        "registry": _routed_registry(evidence, "story-script"),
    }


LIVE_EXTRACTORS: dict[str, Callable[[ConfigFacts, EvidenceBundle], dict[str, Any]]] = {
    "ui-flow-network": extract_ui_flow_network,
    "onboarding-and-rules": extract_onboarding_and_rules,
    "monetization-touchpoints": extract_monetization_touchpoints,
    "activity-entry-map": extract_activity_entry_map,
    "progression-live": extract_progression_live,
    "telemetry-bi-events": extract_telemetry_bi_events,
    "story-script": extract_story_script,
}


def extract_live_all(
    facts: ConfigFacts, evidence: EvidenceBundle
) -> dict[str, dict[str, Any]]:
    """Run the seven verified-layer passes in stable slug order."""

    return {slug: extractor(facts, evidence) for slug, extractor in LIVE_EXTRACTORS.items()}
