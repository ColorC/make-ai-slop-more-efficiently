"""Extract the six documents that consume the permissively imported tables.

F0-02b imported 27 tables the strict Lua parser had skipped, and F6-03
reconciled the save schema they declare.  Nothing read those products yet.
Each extractor here keeps the same three layers as the rest of the verified
layer: what the decompiled table declares, what the device or the save copy
actually showed, and what is still open.

Two tables turned out to mean something other than their name suggests, and
the extractors report the measured meaning rather than the assumed one:

``BoardPromptConfig``
    Not prompt copy.  It is the priority register for the board's idle hints;
    ``Priority`` is filled by a closure that increments once per declared row,
    so the declaration order *is* the ranking, and ``CloseCondition`` retires a
    hint (single segment: while the player level is below it; two segments: until
    that main task is finished).  Truth: ``BoardPrompt.lua``,
    ``BaseSceneBoardView.lua:375-403``, ``BaseSceneBoardModel.lua:58-68``.

``EnergyBoostItemConfig``
    Not energy items.  It is the whitelist of generator types on which the
    energy-boost mode may spend 2^n energy in one tap for 2^n outputs.  Truth:
    ``EnergyBoostModel.lua:104-112,233-236``, ``ItemSpread.lua:749-756``.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable

from .config_facts import ConfigFacts, parse_rewards
from .evidence import EvidenceBundle, ROUTING_TOKENS
from .live_facts import _report_stamp, table_localization

SAVE_SCHEMA_TRUTH = (
    "data/domains/game_observatory/game_models/romantic-restaurant/crossref/"
    "save_schema_coverage.v1.json"
)
LUA_TRUTH = "synthesis/lua_code/Lua-Project"
SURVEY_TRUTH = (
    "docs/plans/game-observatory/[2026-09-05]ROMANTIC-RESTAURANT-FULL-DELEGATION-v1/"
    "records/config-source-survey.md"
)
SURVEY_ORDER_GEOMETRY_TRUTH = "config/game_observatory/romantic_restaurant/order_geometry.v1.json"

# Ten reading groups for the save tables the reader has never decoded.  The
# grouping is by declared table name only; it says where to look next, not what
# the rows mean.
SAVE_TABLE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("账号与身份", ("Account", "UserProfile")),
    (
        "商业化与内购",
        ("Ad", "Bundle", "BundleMeta", "EnergyGemOffer", "IAPOrder", "Packet", "Shop", "ShopItem"),
    ),
    ("经济增益", ("BuffProperty", "BuffPropertyMeta")),
    ("叙事与角色", ("CharacterIntimacy", "StoryRecap")),
    ("客户端配置与网络", ("CDN", "CachedRequests", "Config", "HttpProxyType", "Local", "Misc")),
    (
        "棋盘与物品状态",
        ("CacheItem", "FixedSpread", "FixedSpreadNew", "FixedSwallow", "Inventory", "Slot"),
    ),
    ("解锁与开放", ("ItemUnlock", "ItemUnlockStr", "OpenFunc")),
    ("外观与装饰", ("NewSkin", "Skin", "WeddingDay")),
    ("引导与提示", ("NoticeState", "Tutorial")),
    ("运营触达与评价", ("Rate", "ReturnUser", "Survey")),
)
UNGROUPED_LABEL = "未归类"

# Field names that would carry player-visible copy.  Used to state, from the
# table itself, that a table holds no text rather than asserting it.
_TEXT_FIELD = re.compile(r"(Key|Text|Desc|Description|Name|Tip|Title)$", re.IGNORECASE)
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[0-9]+|[a-z]+")
_TASK_ID = re.compile(r"^(\d+)_(\d+)$")
_TRIGGER = re.compile(r"^taskFinish-(\d+_\d+)$")
_LUA_CONSTANT = re.compile(r"^\s*EnergyModel\.(\w+)\s*=\s*(-?\d+(?:\.\d+)?)\s*$", re.MULTILINE)


def _routed_registry(evidence: EvidenceBundle, slug: str) -> list[dict[str, str]]:
    return [
        {
            "编号": entry.get("编号", ""),
            "登记日期": entry.get("登记日期", ""),
            "观测描述": entry.get("观测描述", ""),
            "猜测归属": entry.get("猜测归属", ""),
            "处置": entry.get("处置（升格新类型 / 归入已有 / 只留原始层）", ""),
        }
        for entry in evidence.registry_for(ROUTING_TOKENS.get(slug, ()))
    ]


def _ledger_counts(evidence: EvidenceBundle, tables: list[str]) -> list[dict[str, Any]]:
    """Per source table, how much of it the field ledger has actually verified."""

    ledger_tables = (evidence.ledger or {}).get("tables") or {}
    rows = []
    for name in tables:
        fields = (ledger_tables.get(name) or {}).get("fields") or {}
        statuses = Counter(str(field.get("status")) for field in fields.values())
        rows.append(
            {
                "table": name,
                "fields": len(fields),
                "observed": statuses.get("observed", 0),
                "contradicted": statuses.get("contradicted", 0),
                "unobserved": statuses.get("unobserved", 0),
            }
        )
    return rows


def _value(cell: Any) -> Any:
    """Unwrap the ``{"ref": ..., "value": ...}`` form the permissive import writes."""

    if isinstance(cell, dict) and "value" in cell and "ref" in cell:
        return cell["value"]
    return cell


def _ref(cell: Any) -> str:
    if isinstance(cell, dict) and "ref" in cell:
        return str(cell["ref"])
    return ""


_HEX = re.compile(r"^[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")


def _hex_colour(cell: Any) -> str:
    """Render a decompiled colour as an sRGB hex triple, alpha appended if set.

    Two encodings reach here: ``CSColor(r, g, b[, a])`` with 0-1 channels, and
    ``Convert("RRGGBB")`` which already carries the hex string.
    """

    if not isinstance(cell, dict):
        return ""
    if "call" in cell:
        args = cell.get("args") or []
        if len(args) == 1 and isinstance(args[0], str) and _HEX.fullmatch(args[0]):
            return "#" + args[0].upper()
        return ""
    channels = [cell.get(key) for key in ("r", "g", "b")]
    if any(not isinstance(value, (int, float)) for value in channels):
        return ""
    text = "#" + "".join(f"{round(float(value) * 255):02X}" for value in channels)
    alpha = cell.get("a")
    if isinstance(alpha, (int, float)):
        text += f"{round(float(alpha) * 255):02X}"
    return text


def _words(name: str) -> list[str]:
    return [part.lower() for part in _CAMEL.findall(str(name)) if len(part) > 2]


def _task_order(task_id: str) -> tuple[int, int] | None:
    match = _TASK_ID.fullmatch(str(task_id))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _main_task_facts(evidence: EvidenceBundle) -> list[dict[str, Any]]:
    return sorted(
        evidence.facts_of_kind("main_task"), key=lambda item: str(item.get("captured_at"))
    )


def live_progress(evidence: EvidenceBundle) -> dict[str, Any]:
    """The furthest main task and player level the fact stream can prove."""

    settled: list[str] = []
    reached = ""
    level: int | None = None
    for fact in _main_task_facts(evidence):
        before = fact.get("before") or {}
        after = fact.get("after") or {}
        task_id = str(before.get("main_task_id") or "")
        if task_id:
            settled.append(task_id)
        reached = str(after.get("main_task_id") or "") or reached
        for source in (before, after):
            value = source.get("level")
            if isinstance(value, int):
                level = value if level is None else max(level, value)
    return {
        "settled_tasks": settled,
        "current_task": reached,
        "current_task_order": _task_order(reached),
        "level": level,
        "fact_count": len(settled),
    }


# --------------------------------------------------------------------------- #
# 1. Save schema
# --------------------------------------------------------------------------- #


def _declared_save_tables(facts: ConfigFacts) -> tuple[dict[str, Any], dict[str, Any]]:
    if not facts.has_table("DBTableConfigs"):
        return {}, {}
    rows = facts.rows("DBTableConfigs")
    payload = rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else {}
    if not payload:
        payload = {}
        for row in rows:
            if isinstance(row, dict):
                payload.update(row)
    tables = payload.get("EDBTableConfigs")
    value_types = payload.get("EDBValueType")
    return (
        tables if isinstance(tables, dict) else {},
        value_types if isinstance(value_types, dict) else {},
    )


def group_save_tables(names: list[str]) -> list[dict[str, Any]]:
    """Sort table names into the ten reading groups, keeping the leftovers visible."""

    remaining = list(names)
    groups = []
    for label, members in SAVE_TABLE_GROUPS:
        matched = [name for name in remaining if name in members]
        remaining = [name for name in remaining if name not in members]
        groups.append({"group": label, "tables": matched, "table_count": len(matched)})
    if remaining:
        groups.append(
            {"group": UNGROUPED_LABEL, "tables": remaining, "table_count": len(remaining)}
        )
    return groups


def extract_save_schema(facts: ConfigFacts, evidence: EvidenceBundle) -> dict[str, Any]:
    declared, value_types = _declared_save_tables(facts)
    coverage = evidence.save_schema or {}
    reconciled = coverage.get("tables") or {}
    reader = coverage.get("save_reader") or {}
    never_read = [str(name) for name in reader.get("never_read_tables") or []]
    tables = []
    for name in sorted(declared):
        declaration = declared[name] if isinstance(declared[name], dict) else {}
        columns = declaration.get("columns")
        entry = reconciled.get(name) or {}
        declared_columns = entry.get("declared_columns") or {
            key: _value(value) for key, value in (columns or {}).items()
        }
        tables.append(
            {
                "logical": name,
                "save_table": str(entry.get("table") or declaration.get("name") or ""),
                "declaration": str(entry.get("declaration") or ""),
                "declared_column_count": len(declared_columns),
                "declared_columns": {
                    key: str(_value(value)) for key, value in sorted(declared_columns.items())
                },
                "primary_key": str(
                    entry.get("declared_primary_key") or declaration.get("primaryKey") or ""
                ),
                "verdict": str(entry.get("verdict") or "未对账"),
                "declared_only_columns": [
                    str(item) for item in entry.get("declared_only_columns") or []
                ],
                "live_only_columns": [str(item) for item in entry.get("live_only_columns") or []],
                "type_mismatches": entry.get("type_mismatches") or {},
                "live_row_count": entry.get("live_row_count"),
                "read_by_save_reader": bool(entry.get("read_by_save_reader")),
            }
        )
    reconciled_rows = [row for row in tables if row["verdict"] != "未对账"]
    return {
        "source_tables": ["DBTableConfigs"],
        "sources": [SAVE_SCHEMA_TRUTH, f"{LUA_TRUTH}/DBTableConfigs.lua"],
        "evidence_level": "live_verified",
        "live_verified_rows": len(reconciled_rows),
        "value_types": {str(key): str(value) for key, value in sorted(value_types.items())},
        "totals": coverage.get("totals") or {},
        "save": coverage.get("save") or {},
        "reader": {
            "read_tables": [str(name) for name in reader.get("read_tables") or []],
            "read_table_count": reader.get("read_table_count"),
            "never_read_tables": never_read,
            "never_read_count": len(never_read),
        },
        "tables": tables,
        "differences": [
            row
            for row in tables
            if row["verdict"] not in {"match", "未对账"}
            or row["declared_only_columns"]
            or row["live_only_columns"]
            or row["type_mismatches"]
        ],
        "next_batch_groups": group_save_tables(never_read),
        "ledger": _ledger_counts(evidence, ["DBTableConfigs"]),
        "registry": _routed_registry(evidence, "save-schema"),
    }


# --------------------------------------------------------------------------- #
# 2. Board prompts
# --------------------------------------------------------------------------- #


def decode_close_condition(value: Any, progress: dict[str, Any]) -> dict[str, Any]:
    """Read one CloseCondition the way ``BaseSceneBoardView:_SelectPrompt`` reads it."""

    text = "" if value is None else str(value)
    if not text:
        return {
            "raw": "",
            "kind": "always_open",
            "meaning": "无条件，一直参与竞争",
            "open_now": True,
        }
    parts = text.split("_")
    if len(parts) == 1 and parts[0].isdigit():
        threshold = int(parts[0])
        level = progress.get("level")
        return {
            "raw": text,
            "kind": "player_level_below",
            "meaning": f"玩家等级 < {threshold} 时才参与竞争",
            "open_now": None if not isinstance(level, int) else level < threshold,
        }
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        order = _task_order(text)
        current = progress.get("current_task_order")
        return {
            "raw": text,
            "kind": "main_task_unfinished",
            "meaning": f"主线任务 {text} 未完成时才参与竞争",
            "open_now": None if not (order and current) else current <= order,
        }
    return {"raw": text, "kind": "unknown", "meaning": "编码形态未登记", "open_now": None}


def _live_names(evidence: EvidenceBundle) -> list[str]:
    surfaces = evidence.navigation.get("surfaces")
    actions = evidence.navigation.get("board_actions")
    names = list(surfaces) if isinstance(surfaces, dict) else []
    names += list(actions) if isinstance(actions, dict) else []
    return sorted({str(name) for name in names})


def _paperbox_clear_attribution(
    facts: ConfigFacts, evidence: EvidenceBundle
) -> dict[str, Any]:
    """Which merge stripped which sand cell, from the stripping facts themselves.

    Each fact carries the cell it uncovered and the merge target it sat next to,
    so the two directions of the rule are counted here rather than asserted: how
    many strips were adjacent to the merge, and what the reward table declares
    for the same cell.
    """

    reward_rows: list[list[str]] = []
    if facts.has_table("MainPaperboxExtraRewardConfig"):
        reward_rows = [
            [str(cell) for cell in row]
            for row in facts.rows("MainPaperboxExtraRewardConfig")
            if isinstance(row, list)
        ]

    def declared_reward(row: Any, column: Any) -> str:
        if not isinstance(row, int) or not isinstance(column, int):
            return ""
        if 1 <= row <= len(reward_rows) and 1 <= column <= len(reward_rows[row - 1]):
            value = reward_rows[row - 1][column - 1]
            return "" if value == "0" else value
        return ""

    events = []
    for fact in sorted(
        evidence.facts_of_kind("paperbox_clear"), key=lambda item: str(item.get("captured_at"))
    ):
        before = fact.get("before") or {}
        after = fact.get("after") or {}
        events.append(
            {
                "row": before.get("row"),
                "column": before.get("col"),
                "cell_before": str(before.get("cell_before") or ""),
                "cell_after": str(after.get("cell_after") or ""),
                "adjacent_to_merge_target": after.get("adjacent_to_merge_target"),
                "merge_result_type": str(after.get("merge_result_type") or ""),
                "merge_target": f'({after.get("merge_target_row")},{after.get("merge_target_col")})',
                "declared_reward": declared_reward(before.get("row"), before.get("col")),
                "captured_at": str(fact.get("captured_at") or ""),
                "fact_id": str(fact.get("fact_id") or ""),
            }
        )
    registration = (evidence.report("f4_08").get("registrations") or {}).get(
        "paperbox_clear_attribution"
    ) or {}
    return {
        "available": bool(events),
        "event_count": len(events),
        "adjacent_count": sum(1 for row in events if row["adjacent_to_merge_target"]),
        "with_declared_reward": sum(1 for row in events if row["declared_reward"]),
        "events": events,
        "rule": str(registration.get("rule") or ""),
        "reverse_direction": str(registration.get("reverse_direction") or ""),
        "source": evidence.report_source("f4_08"),
    }


def extract_board_prompts_and_rules(
    facts: ConfigFacts, evidence: EvidenceBundle
) -> dict[str, Any]:
    rows = list(facts.rows("BoardPromptConfig")) if facts.has_table("BoardPromptConfig") else []
    fields = list(facts.fields("BoardPromptConfig")) if rows else []
    progress = live_progress(evidence)
    live_names = _live_names(evidence)
    declared = [row for row in rows if isinstance(row, dict)]
    # A token shared by several prompt types is part of the naming scheme
    # ("item", "tap", "level"), not a subject; matching on it would file half the
    # registered surfaces under half the prompts.  Only tokens unique to one
    # prompt type are used for the cross-check.
    token_owners: Counter[str] = Counter()
    for row in declared:
        for token in set(_words(row.get("Type"))):
            token_owners[token] += 1
    prompts = []
    for rank, row in enumerate(declared, start=1):
        prompt_type = str(row.get("Type") or "")
        tokens = [token for token in _words(prompt_type) if token_owners[token] == 1]
        matches = sorted(
            {name for name in live_names for token in tokens if token in name.lower()}
        )
        condition = decode_close_condition(row.get("CloseCondition"), progress)
        prompts.append(
            {
                "rank": rank,
                "type": prompt_type,
                "priority_source": _ref(row.get("Priority")) or "autoIncreasePriority()",
                "close_condition": condition,
                "distinctive_tokens": tokens,
                "live_name_matches": matches,
            }
        )
    text_fields = [name for name in fields if _TEXT_FIELD.search(name)]
    scoped = [prompt for prompt in prompts if prompt["close_condition"]["raw"]]
    paperbox = _paperbox_clear_attribution(facts, evidence)
    return {
        "source_tables": ["BoardPromptConfig", "MainPaperboxExtraRewardConfig"]
        if paperbox["available"]
        else ["BoardPromptConfig"],
        "sources": [
            f"{LUA_TRUTH}/BoardPromptConfig.lua",
            f"{LUA_TRUTH}/BoardPrompt.lua",
            f"{LUA_TRUTH}/BaseSceneBoardView.lua",
        ]
        + ([paperbox["source"]] if paperbox.get("source") else []),
        "evidence_level": "live_partial",
        "paperbox_clear": paperbox,
        # Only the scoped prompts get a verdict from the fact stream: the
        # account's level and main-task progress decide whether they still run.
        "live_verified_rows": sum(
            1
            for prompt in prompts
            if prompt["close_condition"]["raw"] and prompt["close_condition"]["open_now"] is not None
        )
        + paperbox["event_count"],
        "declared_fields": fields,
        "text_fields": text_fields,
        "prompt_count": len(prompts),
        "scoped_count": len(scoped),
        "prompts": prompts,
        "live_progress": progress,
        "live_names": live_names,
        "ledger": _ledger_counts(evidence, ["BoardPromptConfig"]),
        "localization": table_localization(facts, "BoardPromptConfig"),
        "registry": _routed_registry(evidence, "board-prompts-and-rules"),
    }


# --------------------------------------------------------------------------- #
# 3. Story speakers
# --------------------------------------------------------------------------- #


def _speaker_base(name: str) -> str:
    return str(name).split("_")[0].rstrip("0123456789").lower() or str(name).lower()


def extract_story_speakers(facts: ConfigFacts, evidence: EvidenceBundle) -> dict[str, Any]:
    rows = facts.rows("StorySpeakerConfig") if facts.has_table("StorySpeakerConfig") else ()
    declared = rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else {}
    if not declared:
        declared = {key: value for row in rows if isinstance(row, dict) for key, value in row.items()}
    task_characters: Counter[str] = Counter()
    task_rows: dict[str, list[str]] = {}
    if facts.has_table("MainTaskConfig"):
        for row in facts.rows("MainTaskConfig"):
            if not isinstance(row, dict) or not row.get("detail_icon"):
                continue
            icon = str(row["detail_icon"]).lower()
            task_characters[icon] += 1
            task_rows.setdefault(icon, []).append(f"{row.get('day')}_{row.get('id')}")
    progress = live_progress(evidence)
    settled = set(progress["settled_tasks"])
    settled_icons = {
        icon for icon, tasks in task_rows.items() if settled.intersection(tasks)
    }
    speakers = []
    known_names = {str(name).lower() for name in declared}
    for name in sorted(declared):
        row = declared[name] if isinstance(declared[name], dict) else {}
        base = _speaker_base(name)
        lowered = str(name).lower()
        speakers.append(
            {
                "speaker": name,
                "base": base,
                "direction": row.get("Direction"),
                "nameplate_colour": _hex_colour(row.get("NameplateColor"))
                or _ref(row.get("NameplateColor")),
                "name_colour": _hex_colour(row.get("NameColor")),
                "bubble_colour": _hex_colour(row.get("BubbleColor"))
                or _ref(row.get("BubbleColor")),
                "has_tutorial_avatar_pos": row.get("TutorialAvatarPos") is not None,
                "extra_fields": sorted(
                    key
                    for key in row
                    if key
                    not in {
                        "Direction",
                        "NameplateColor",
                        "NameColor",
                        "BubbleColor",
                        "TutorialAvatarPos",
                    }
                ),
                "exact_task_rows": task_characters.get(lowered, 0),
                "base_task_rows": task_characters.get(base, 0),
                "seen_in_settled_tasks": lowered in settled_icons or base in settled_icons,
            }
        )
    bases = sorted({speaker["base"] for speaker in speakers})
    return {
        "source_tables": ["StorySpeakerConfig", "MainTaskConfig"],
        "sources": [
            f"{LUA_TRUTH}/StorySpeakerConfig.lua",
            f"{LUA_TRUTH}/StoryConfigModel.lua",
            SURVEY_TRUTH,
        ],
        "evidence_level": "live_partial",
        "live_verified_rows": sum(1 for speaker in speakers if speaker["seen_in_settled_tasks"]),
        "speaker_count": len(speakers),
        "base_count": len(bases),
        "task_character_count": len(task_characters),
        "direction_distribution": dict(
            sorted(Counter(str(speaker["direction"]) for speaker in speakers).items())
        ),
        "variant_groups": [
            {
                "base": base,
                "variants": [
                    speaker["speaker"] for speaker in speakers if speaker["base"] == base
                ],
                "base_task_rows": task_characters.get(base, 0),
            }
            for base in bases
            if sum(1 for speaker in speakers if speaker["base"] == base) > 1
        ],
        "speakers": speakers,
        "task_only_characters": sorted(set(task_characters) - set(bases) - known_names),
        "speakers_without_tasks": [
            speaker["speaker"]
            for speaker in speakers
            if speaker["exact_task_rows"] == 0 and speaker["base_task_rows"] == 0
        ],
        "live_progress": progress,
        "settled_characters": sorted(settled_icons),
        "text_status": "剧情文本本体不在反编译材料内（载体勘定见源头勘定记录）",
        "ledger": _ledger_counts(evidence, ["StorySpeakerConfig", "MainTaskConfig"]),
        "registry": _routed_registry(evidence, "story-speakers"),
    }


# --------------------------------------------------------------------------- #
# 4. Special order types
# --------------------------------------------------------------------------- #


def _order_row_live_rules(evidence: EvidenceBundle) -> dict[str, Any]:
    """How the four save slots map onto the three trays the screen shows.

    The forensics report scored every candidate ordering against the frames the
    replay tree already stores; the geometry product carries the calibration and
    the two rules the corpus cannot tell apart.  Both are read, not restated.
    """

    forensics = evidence.report("f4_09_order_display")
    geometry = evidence.order_geometry or {}
    corpus = forensics.get("corpus") or {}
    survivors = [
        str(name)
        for name in forensics.get("rules_at_full_hit_rate_with_at_least_eight_samples") or []
    ]
    scores = []
    for row in forensics.get("rule_scores") or []:
        if not isinstance(row, dict):
            continue
        scores.append(
            {
                "rule": str(row.get("rule") or ""),
                "button_pattern_hits": row.get("button_pattern_hits"),
                "button_pattern_samples": row.get("button_pattern_samples"),
                "button_pattern_hit_rate": row.get("button_pattern_hit_rate"),
                "confirmed_pairing_hits": row.get("confirmed_pairing_hits"),
                "confirmed_pairing_samples": row.get("confirmed_pairing_samples"),
                "confirmed_pairing_hit_rate": row.get("confirmed_pairing_hit_rate"),
                "survivor": str(row.get("rule") or "") in survivors,
            }
        )
    scores.sort(key=lambda item: (-(item["button_pattern_hit_rate"] or 0), item["rule"]))
    candidates = []
    for row in geometry.get("display_order_candidates") or []:
        if not isinstance(row, dict):
            continue
        candidates.append(
            {
                "rule": str(row.get("rule") or ""),
                "status": str(row.get("status") or ""),
                "button_pattern": f'{row.get("button_pattern_hits")}/'
                f'{row.get("button_pattern_samples")}',
                "confirmed_pairing": f'{row.get("confirmed_pairing_hits")}/'
                f'{row.get("confirmed_pairing_samples")}',
                "hand_read": f'{row.get("hand_read_hits")}/{row.get("hand_read_samples")}',
                "discriminating_check": str(row.get("discriminating_check") or ""),
            }
        )
    fixes = (evidence.report("f4_08").get("fixes_forced_by_the_device") or {}).get(
        "order_display_order"
    ) or {}
    occlusion_table = evidence.record_table("等级", "回合")
    occlusion = {
        "available": occlusion_table is not None,
        "headers": list(occlusion_table.headers) if occlusion_table else [],
        "rows": [dict(row) for row in occlusion_table.rows] if occlusion_table else [],
    }
    return {
        "available": bool(scores or candidates),
        "source": evidence.report_source("f4_09_order_display"),
        "geometry_source": SURVEY_ORDER_GEOMETRY_TRUTH,
        "display_positions": geometry.get("display_positions"),
        "slot_addressing": str(geometry.get("slot_addressing") or ""),
        "tray_centers": geometry.get("slot_centers") or {},
        "delivery_interaction": str(geometry.get("delivery_interaction") or ""),
        "corpus": {
            "runs": corpus.get("runs"),
            "planner_rounds": corpus.get("planner_rounds"),
            "rounds_with_enough_orders": corpus.get("rounds_with_enough_orders"),
            "rounds_with_buttons": corpus.get("rounds_with_buttons"),
            "confirmed_deliveries": corpus.get("confirmed_deliveries"),
            "save_screen_agreement": dict(corpus.get("save_screen_agreement") or {}),
        },
        "rule_scores": scores,
        "candidates": candidates,
        "type_two_fix": {
            "finding": str(fixes.get("finding") or ""),
            "live_confirmation": str(fixes.get("live_confirmation") or ""),
        },
        "notebook_occlusion": occlusion,
    }


def extract_order_special_types(
    facts: ConfigFacts, evidence: EvidenceBundle
) -> dict[str, Any]:
    rows = list(facts.rows("OrderSpecialConfig")) if facts.has_table("OrderSpecialConfig") else []
    progress = live_progress(evidence)
    current = progress["current_task_order"]
    warnings: list[dict[str, Any]] = []
    orders = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        trigger = str(row.get("Trigger") or "")
        match = _TRIGGER.fullmatch(trigger)
        trigger_task = match.group(1) if match else ""
        order = _task_order(trigger_task)
        if current is None or order is None:
            reached = None
        else:
            reached = current >= order
        chains = row.get("GeneratorChain")
        orders.append(
            {
                "id": row.get("Id"),
                "avatar_id": row.get("AvatarId"),
                "avatar_image": str(row.get("AvatarImage") or ""),
                "big_avatar_image": str(row.get("BigAvatarImage") or ""),
                "complete_image": str(row.get("CompleteImage") or ""),
                "reward_image": str(row.get("RewardImage") or ""),
                "description_key": str(row.get("DescriptionKey") or ""),
                "trigger": trigger,
                "trigger_task": trigger_task,
                "trigger_reached": reached,
                "generator_chains": [str(item) for item in chains]
                if isinstance(chains, list)
                else [str(chains)]
                if chains is not None
                else [],
                "requirements": [
                    str(row[key]) for key in ("Requirement1", "Requirement2") if row.get(key)
                ],
                "start_reward": str(row.get("StartReward") or ""),
                "rewards": parse_rewards(
                    row.get("Rewards"),
                    warnings=warnings,
                    table="OrderSpecialConfig",
                    field="Rewards",
                ),
                "frame_colour": _hex_colour(row.get("FrameColor")),
                "timelines": [
                    str(row.get(key) or "") for key in ("StartTimeline", "EndTimeline")
                ],
            }
        )
    slot_registry = _routed_registry(evidence, "order-special-types")
    row_rules = _order_row_live_rules(evidence)
    return {
        "source_tables": ["OrderSpecialConfig"],
        "sources": [f"{LUA_TRUTH}/OrderSpecialConfig.lua", SURVEY_ORDER_GEOMETRY_TRUTH]
        + ([row_rules["source"]] if row_rules.get("source") else []),
        "evidence_level": "live_partial",
        "live_verified_rows": sum(1 for order in orders if order["trigger_reached"])
        + len(row_rules.get("rule_scores") or []),
        "row_rules": row_rules,
        "order_count": len(orders),
        "orders": orders,
        "reward_totals": dict(
            sorted(
                Counter(
                    reward["type"] for order in orders for reward in order["rewards"]
                ).items()
            )
        ),
        "unreached": [
            order["trigger_task"] for order in orders if order["trigger_reached"] is False
        ],
        "live_progress": progress,
        "warnings": warnings,
        "ledger": _ledger_counts(evidence, ["OrderSpecialConfig"]),
        "registry": slot_registry,
    }


# --------------------------------------------------------------------------- #
# 5. Energy economy
# --------------------------------------------------------------------------- #


def parse_energy_constants(source: str) -> dict[str, Any]:
    """Read the energy clock constants straight out of the decompiled model."""

    return {
        name: int(float(value)) if float(value).is_integer() else float(value)
        for name, value in _LUA_CONSTANT.findall(source)
    }


def _collect_chains(facts: ConfigFacts) -> list[dict[str, Any]]:
    if not facts.has_table("ItemModelConfig"):
        return []
    by_type: dict[str, dict[str, Any]] = {}
    for row in facts.rows("ItemModelConfig"):
        if isinstance(row, dict) and row.get("Type") is not None:
            by_type[str(row["Type"])] = row
    warnings: list[dict[str, Any]] = []
    members: dict[str, list[dict[str, Any]]] = {}
    for type_id, row in by_type.items():
        rewards = parse_rewards(
            row.get("CollectRewards"),
            warnings=warnings,
            table="ItemModelConfig",
            field="CollectRewards",
        )
        if not rewards:
            continue
        for reward in rewards:
            members.setdefault(reward["type"], []).append(
                {
                    "type": type_id,
                    "merged_type": str(row.get("MergedType") or ""),
                    "amount": reward["amount"],
                }
            )
    chains = []
    for currency in sorted(members):
        levels = sorted(members[currency], key=lambda item: item["amount"])
        chains.append(
            {
                "currency": currency,
                "levels": levels,
                "sequence": [level["type"] for level in levels],
                "amounts": [level["amount"] for level in levels],
                "level_count": len(levels),
            }
        )
    return chains


def _merge_beats_collect(chain: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Per step of the supply chain: merging two pieces against claiming both.

    Both sides are read off ``CollectRewards`` itself, so the comparison is a
    property of the configuration rather than a rule of thumb.
    """

    levels = (chain or {}).get("levels") or []
    rows = []
    for lower, upper in zip(levels, levels[1:]):
        collected_twice = int(lower["amount"]) * 2
        merged = int(upper["amount"])
        rows.append(
            {
                "from_type": lower["type"],
                "to_type": upper["type"],
                "collect_two": collected_twice,
                "merge_then_collect": merged,
                "difference": merged - collected_twice,
                "merge_wins": merged > collected_twice,
            }
        )
    return rows


def _coins_per_energy(evidence: EvidenceBundle) -> dict[str, Any]:
    """What one point of energy turned into, over the two measured windows."""

    long_run = (evidence.report("f4_08").get("long_run") or {}).get(
        "earlier_run_stopped_for_the_ab"
    ) or {}
    windows = []
    projections = sorted(
        (
            (evidence.report_source(name), evidence.report(name))
            for name in ("f4_09_projection", "f4_08_projection")
            if evidence.report(name)
        ),
        key=lambda item: _report_stamp(item[0]),
        reverse=True,
    )
    for source, projection in projections:
        observed = projection.get("observed") or {}
        if not observed:
            continue
        windows.append(
            {
                "window": f"全部报告语料（{observed.get('reports_used')} 份，截至该次重算）",
                "reports": observed.get("reports_used"),
                "energy_spent": observed.get("energy_spent_confirmed"),
                "delivery_coins": observed.get("delivery_coins_confirmed"),
                "deliveries": observed.get("confirmed_deliveries"),
                "coins_per_energy": observed.get("coins_per_energy"),
                "source": source,
            }
        )
    if long_run:
        energy = long_run.get("energy") or []
        coins = long_run.get("coins") or []
        windows.append(
            {
                "window": "升到等级 5 之后的长跑首段",
                "reports": long_run.get("segments"),
                "energy_spent": (energy[0] - energy[-1]) if len(energy) == 2 else None,
                "delivery_coins": (coins[-1] - coins[0]) if len(coins) == 2 else None,
                "deliveries": long_run.get("confirmed_deliveries"),
                "coins_per_energy": long_run.get("coins_per_energy"),
                "source": evidence.report_source("f4_08"),
            }
        )
    return {"windows": windows}


def _wait_granularity(evidence: EvidenceBundle) -> dict[str, Any]:
    """The A/B on how long to sleep before resuming, straight from its report."""

    comparison = evidence.report("f4_08_ab")
    report = evidence.report("f4_08")
    arms = comparison.get("arms") or {}
    threshold = report.get("resume_energy_threshold") or {}
    verdict = report.get("ab_verdict") or {}
    energy_bound = report.get("energy_bound_recomputed") or {}
    arm_rows = []
    for name in sorted(arms):
        arm = arms[name] or {}
        bound = (energy_bound.get("arms") or {}).get(name) or {}
        arm_rows.append(
            {
                "arm": name,
                "threshold": arm.get("resume_energy_threshold"),
                "wall_seconds": arm.get("wall_seconds"),
                "segments": arm.get("segments"),
                "rounds": arm.get("rounds"),
                "energy_spent": arm.get("energy_spent"),
                "waited_s": arm.get("waited_s"),
                "sleep_share": arm.get("sleep_share"),
                "effective_play_share": arm.get("effective_play_share"),
                "replay_mib": arm.get("replay_mib"),
                "confirmed_deliveries": arm.get("confirmed_deliveries"),
                "energy_bound_entered_at_segment": bound.get("entered_at_segment"),
            }
        )
    return {
        "available": bool(arm_rows),
        "source": evidence.report_source("f4_08_ab"),
        "report_source": evidence.report_source("f4_08"),
        "table": [dict(row) for row in comparison.get("table") or []],
        "arms": arm_rows,
        "default_threshold": threshold.get("default_today"),
        "default_formula": str(threshold.get("default_formula") or ""),
        "derivation_inputs": dict(threshold.get("derivation_inputs") or {}),
        "wake_rule": str(threshold.get("wake_rule") or ""),
        "threshold_one_reproduces_f4_07": threshold.get("threshold_one_reproduces_f4_07"),
        "winner": str(verdict.get("winner") or ""),
        "energy_income_each_arm": verdict.get("energy_income_each_arm"),
        "why_confirmed_is_the_wrong_yardstick": str(
            verdict.get("why_confirmed_is_the_wrong_yardstick") or ""
        ),
        "derived": dict(verdict.get("derived") or {}),
        "caveat": str(verdict.get("caveat") or ""),
        "energy_bound_criterion": str(energy_bound.get("criterion") or ""),
    }


def _free_energy_grants(evidence: EvidenceBundle) -> list[dict[str, Any]]:
    """Every observed free grant of energy, each with its own save evidence."""

    rows = []
    for fact in evidence.facts_of_kind("energy_refill_free"):
        before = fact.get("before") or {}
        after = fact.get("after") or {}
        rows.append(
            {
                "grant": "棋盘体力棋子领取",
                "energy_before": before.get("energy"),
                "energy_after": after.get("energy"),
                "gems_before": before.get("gems"),
                "gems_after": after.get("gems"),
                "evidence": str(fact.get("fact_id") or ""),
                "source": str(fact.get("source_report") or ""),
            }
        )
    claim = evidence.report("f4_06").get("free_refill_claim") or {}
    if claim:
        rows.append(
            {
                "grant": f"教程内 {claim.get('surface')} 免费补给",
                "energy_before": claim.get("energy_before"),
                "energy_after": claim.get("energy_after"),
                "gems_before": claim.get("gems_before"),
                "gems_after": claim.get("gems_after"),
                "evidence": str(claim.get("evidence_frame_sha256") or ""),
                "source": evidence.report_source("f4_06"),
            }
        )
    deviation = evidence.report("f4_08").get("red_line_deviation") or {}
    cost = deviation.get("cost") or {}
    if cost:
        energy = cost.get("energy") or []
        gems = cost.get("gems") or []
        rows.append(
            {
                "grant": "回归礼物段的五次无条件领取",
                "energy_before": energy[0] if energy else None,
                "energy_after": energy[-1] if energy else None,
                "gems_before": gems[0] if gems else None,
                "gems_after": gems[-1] if gems else None,
                "evidence": str(deviation.get("evidence") or ""),
                "source": evidence.report_source("f4_08"),
            }
        )
    dismiss = (evidence.report("f4_08").get("registrations") or {}).get(
        "returning_player_gift"
    ) or {}
    diff = dismiss.get("save_diff") or {}
    if diff:
        before = diff.get("before") or {}
        after = diff.get("after") or {}
        rows.append(
            {
                "grant": "回归礼物庆祝层的散场点击",
                "energy_before": before.get("energy"),
                "energy_after": after.get("energy"),
                "gems_before": before.get("gems"),
                "gems_after": after.get("gems"),
                "evidence": "存档前后无差分" if before == after else "见报告",
                "source": evidence.report_source("f4_08"),
            }
        )
    return rows


def extract_energy_items(facts: ConfigFacts, evidence: EvidenceBundle) -> dict[str, Any]:
    boost_rows = (
        list(facts.rows("EnergyBoostItemConfig"))
        if facts.has_table("EnergyBoostItemConfig")
        else []
    )
    item_rows: dict[str, dict[str, Any]] = {}
    if facts.has_table("ItemModelConfig"):
        for row in facts.rows("ItemModelConfig"):
            if isinstance(row, dict) and row.get("Type") is not None:
                item_rows[str(row["Type"])] = row
    whitelist = []
    for entry in boost_rows:
        value = str(_value(entry))
        ref = _ref(entry)
        item = item_rows.get(value) or {}
        spread = sorted(key for key in item if str(key).startswith("Spread_"))
        whitelist.append(
            {
                "ref": ref,
                "item_type": value,
                "chain": ref.split(".")[-1].rstrip("0123456789") if ref else "",
                "resolved": bool(ref) and value != ref,
                "in_item_model": value in item_rows,
                "is_generator": bool(spread),
                "spread_cost_energy": item.get("Spread_CostEnergy"),
            }
        )
    chains = _collect_chains(facts)
    energy_chain = next((chain for chain in chains if chain["currency"] == "energy"), None)
    constants = parse_energy_constants(evidence.energy_source)
    recovery = [
        {
            "fact_id": str(fact.get("fact_id") or ""),
            "elapsed_seconds": (fact.get("delta") or {}).get("elapsed_seconds"),
            "energy_before": (fact.get("before") or {}).get("energy"),
            "energy_after": (fact.get("after") or {}).get("energy"),
            "energy_gained": (fact.get("delta") or {}).get("energy"),
            "captured_at": str(fact.get("captured_at") or ""),
        }
        for fact in sorted(
            evidence.facts_of_kind("energy_recovery"),
            key=lambda item: str(item.get("captured_at")),
        )
    ]
    for row in recovery:
        elapsed = row["elapsed_seconds"]
        period = constants.get("DefaultRecoveryDuration")
        if isinstance(elapsed, (int, float)) and isinstance(period, (int, float)) and period:
            row["predicted_gain"] = int(elapsed // period)
            gained = row["energy_gained"]
            row["deviation"] = (
                abs(gained - row["predicted_gain"]) if isinstance(gained, (int, float)) else None
            )
        else:
            row["predicted_gain"] = None
            row["deviation"] = None
    observed_max = [
        value
        for fact in evidence.facts
        for source in (fact.get("before") or {}, fact.get("after") or {})
        if isinstance((value := source.get("energy")), int)
    ]
    free_grants = _free_energy_grants(evidence)
    coins_per_energy = _coins_per_energy(evidence)
    return {
        "source_tables": ["EnergyBoostItemConfig", "ItemModelConfig"],
        "sources": [
            f"{LUA_TRUTH}/EnergyBoostItemConfig.lua",
            f"{LUA_TRUTH}/EnergyBoostModel.lua",
            f"{LUA_TRUTH}/EnergyModel.lua",
        ]
        + [
            evidence.report_source(name)
            for name in ("f4_06", "f4_08", "f4_08_ab", "f4_08_projection", "f4_09_projection")
            if evidence.report_source(name)
        ],
        "evidence_level": "live_verified",
        "live_verified_rows": len(recovery)
        + len(free_grants)
        + len(coins_per_energy["windows"]),
        "constants": constants,
        "constants_source": f"{LUA_TRUTH}/EnergyModel.lua",
        "whitelist": whitelist,
        "whitelist_count": len(whitelist),
        "whitelist_chains": dict(
            sorted(Counter(entry["chain"] for entry in whitelist).items())
        ),
        "whitelist_unresolved": [entry["ref"] for entry in whitelist if not entry["resolved"]],
        "whitelist_missing_from_item_model": [
            entry["item_type"] for entry in whitelist if not entry["in_item_model"]
        ],
        "collect_chains": chains,
        "energy_chain": energy_chain,
        "merge_beats_collect": _merge_beats_collect(energy_chain),
        "coins_per_energy": coins_per_energy,
        "wait_granularity": _wait_granularity(evidence),
        "free_grants": free_grants,
        "recovery_live_check": dict(
            (evidence.report("f4_06").get("energy_model") or {}).get("live_verification") or {}
        ),
        "recovery_live_source": evidence.report_source("f4_06"),
        "recovery_facts": recovery,
        "recovery_summary": {
            "fact_count": len(recovery),
            "exact": sum(1 for row in recovery if row["deviation"] == 0),
            "within_one": sum(
                1 for row in recovery if isinstance(row["deviation"], int) and row["deviation"] <= 1
            ),
            "observed_energy_max": max(observed_max) if observed_max else None,
            "observed_energy_min": min(observed_max) if observed_max else None,
        },
        "ledger": _ledger_counts(evidence, ["EnergyBoostItemConfig", "ItemModelConfig"]),
        "registry": _routed_registry(evidence, "energy-items"),
    }


# --------------------------------------------------------------------------- #
# 6. Skins and item views
# --------------------------------------------------------------------------- #


def _field_profile(rows: list[Any], fields: list[str]) -> list[dict[str, Any]]:
    """Per declared field: how many rows carry it and in which encoded shape."""

    present: Counter[str] = Counter()
    shapes: dict[str, Counter[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            present[key] += 1
            if isinstance(value, dict) and "ref" in value:
                shape = "引用"
            elif isinstance(value, dict):
                shape = "构造"
            elif isinstance(value, list):
                shape = "列表"
            else:
                shape = "字面量"
            shapes.setdefault(key, Counter())[shape] += 1
    names = list(fields) + [key for key in sorted(present) if key not in fields]
    return [
        {
            "field": name,
            "rows": present.get(name, 0),
            "shapes": dict(sorted(shapes.get(name, Counter()).items())),
        }
        for name in names
    ]


def extract_skins_and_item_views(
    facts: ConfigFacts, evidence: EvidenceBundle
) -> dict[str, Any]:
    skins = list(facts.rows("SkinConfig")) if facts.has_table("SkinConfig") else []
    views = list(facts.rows("ItemViewConfig")) if facts.has_table("ItemViewConfig") else []
    skin_fields = list(facts.fields("SkinConfig")) if skins else []
    view_fields = list(facts.fields("ItemViewConfig")) if views else []
    areas: Counter[str] = Counter()
    slot_prefixes: Counter[str] = Counter()
    for row in skins:
        if not isinstance(row, dict):
            continue
        areas[str(_value(row.get("area")) or "")] += 1
        slot_prefixes[str(row.get("slot") or "").split("_")[0]] += 1
    view_kinds: Counter[str] = Counter()
    image_counts: Counter[int] = Counter()
    dig_views: Counter[str] = Counter()
    tutorial_boards = []
    for row in views:
        if not isinstance(row, dict):
            continue
        type_id = str(row.get("Type") or "")
        view_kinds["数字 Type" if type_id.isdigit() else "命名 Type"] += 1
        images = row.get("Image")
        image_counts[len(images) if isinstance(images, list) else 0] += 1
        if row.get("DigView") is not None:
            dig_views[str(_value(row.get("DigView")))] += 1
        if row.get("TutorialBoard") is not None:
            tutorial_boards.append(
                {"type": type_id, "tutorial_board": str(row.get("TutorialBoard"))}
            )
    return {
        "source_tables": ["SkinConfig", "ItemViewConfig"],
        "sources": [f"{LUA_TRUTH}/SkinConfig.lua", f"{LUA_TRUTH}/ItemViewConfig.lua"],
        "evidence_level": "decompiled_config",
        "live_verified_rows": 0,
        "scale": {
            "SkinConfig": len(skins),
            "ItemViewConfig": len(views),
            "SkinConfig 装饰区": len(areas),
            "SkinConfig 槽位前缀": len(slot_prefixes),
            "ItemViewConfig 挖掘外观": len(dig_views),
        },
        "skin_fields": _field_profile(skins, skin_fields),
        "view_fields": _field_profile(views, view_fields),
        "skin_areas": [
            {"area": name, "rows": count}
            for name, count in sorted(areas.items(), key=lambda item: (-item[1], item[0]))
        ],
        "skin_slot_prefixes": [
            {"prefix": name, "rows": count}
            for name, count in sorted(
                slot_prefixes.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "view_type_kinds": dict(sorted(view_kinds.items())),
        "view_image_counts": {str(key): value for key, value in sorted(image_counts.items())},
        "view_dig_views": [
            {"prefab": name, "rows": count}
            for name, count in sorted(dig_views.items(), key=lambda item: (-item[1], item[0]))
        ],
        "view_tutorial_boards": sorted(tutorial_boards, key=lambda item: item["type"]),
        "ledger": _ledger_counts(evidence, ["SkinConfig", "ItemViewConfig"]),
        "registry": _routed_registry(evidence, "skins-and-item-views"),
    }


PERMISSIVE_EXTRACTORS: dict[str, Callable[[ConfigFacts, EvidenceBundle], dict[str, Any]]] = {
    "save-schema": extract_save_schema,
    "board-prompts-and-rules": extract_board_prompts_and_rules,
    "story-speakers": extract_story_speakers,
    "order-special-types": extract_order_special_types,
    "energy-items": extract_energy_items,
    "skins-and-item-views": extract_skins_and_item_views,
}
