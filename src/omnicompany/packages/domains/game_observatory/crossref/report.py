"""Cross-reference coverage reporting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


FIELD_EVIDENCE_KINDS: dict[tuple[str, str], str] = {
    ("BoardModelConfig", "cell"): "board_snapshot",
    ("ItemModelConfig", "CollectRewards"): "collect",
    ("ItemModelConfig", "MergedType"): "merge",
    ("ItemModelConfig", "SellingPrice"): "sell",
    ("ItemModelConfig", "Spread_CostEnergy"): "generate",
    ("ItemModelConfig", "Spread_ItemMaxNumber"): "generator_runtime",
    ("ItemModelConfig", "Spread_ItemRecoveryDuration"): "generator_runtime",
    ("ItemModelConfig", "Spread_OpenDuration"): "open_chest",
    ("ItemModelConfig", "Spread_StorageMaxNumber"): "generator_runtime",
    ("ItemModelConfig", "Spread_StorageRecoveryDuration"): "generator_cooldown",
    ("ItemModelConfig", "Spread_Weight"): "generator_output_sample",
    ("ItemModelConfig", "Type"): "merge",
    ("LevelConfig", "cost"): "main_task",
    ("MainTaskConfig", "cost"): "main_task",
    ("MainTaskConfig", "rewards"): "main_task",
    ("OrderItemConfig", "Price"): "deliver",
}

CORE_TABLES = (
    "BoardModelConfig",
    "ItemModelConfig",
    "LevelConfig",
    "MainTaskConfig",
    "OrderItemConfig",
)


def build_summary(
    ledger: dict[str, Any],
    row_coverage: dict[str, Any] | None = None,
    facility: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for table_name, table in sorted((ledger.get("tables") or {}).items()):
        fields = (table or {}).get("fields") or {}
        counts = {"observed": 0, "unobserved": 0, "contradicted": 0}
        for value in fields.values():
            status = value.get("status", "unobserved")
            counts[status if status in counts else "unobserved"] += 1
        total = len(fields)
        tables[table_name] = {
            **counts,
            "total": total,
            "coverage_rate": counts["observed"] / total if total else 0.0,
        }
    totals = {
        key: sum(table[key] for table in tables.values())
        for key in ("observed", "unobserved", "contradicted", "total")
    }
    totals["coverage_rate"] = totals["observed"] / totals["total"] if totals["total"] else 0.0
    top_unobserved = sorted(
        (
            {"table": name, "unobserved": value["unobserved"], "total": value["total"]}
            for name, value in tables.items()
            if value["unobserved"]
        ),
        key=lambda item: (-item["unobserved"], item["table"]),
    )[:20]
    summary = {
        "schema": "game-observatory.crossref-summary.v1",
        "generated_at": max(
            (
                field.get("last_evidence_at", "")
                for table in (ledger.get("tables") or {}).values()
                for field in (table.get("fields") or {}).values()
            ),
            default=ledger.get("generated_at", ""),
        )
        or ledger.get("generated_at", ""),
        "totals": totals,
        "tables": tables,
        "top_unobserved_tables": top_unobserved,
    }
    if row_coverage is not None:
        from .row_coverage import row_coverage_summary

        summary["row_coverage"] = row_coverage_summary(row_coverage)
        summary["top_verified_rows"] = row_coverage.get("top_verified_rows") or []
    if facility is not None:
        # F6-04 section 2.  Facility-side counts live beside the ledger, never
        # inside it: nothing here judges a configuration field.
        summary["facility_facts"] = facility.get("facility_facts") or {}
        summary["guard_events"] = facility.get("guard_events") or {}
    return summary


def write_summary(
    ledger: dict[str, Any],
    path: str | Path,
    row_coverage: dict[str, Any] | None = None,
    facility: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = build_summary(ledger, row_coverage, facility)
    rendered = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
        target.write_text(rendered, encoding="utf-8", newline="\n")
    return summary


def _escape(value: Any) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ")


def _contradiction_rows(results: Iterable[Any]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        if result.status != "contradicted":
            continue
        rows.append(
            {
                "table": result.table,
                "field": result.field,
                "row": ", ".join(
                    f"{key}={value}" for key, value in sorted(result.row_keys.items())
                ),
                "reason": result.reason,
                "fact_ids": list(result.all_fact_ids),
            }
        )
    return sorted(rows, key=lambda item: (item["table"], item["field"], item["row"]))


def _fill_suggestions(
    ledger: dict[str, Any], fact_kind_counts: dict[str, int], limit: int = 25
) -> list[dict[str, Any]]:
    """Rank unobserved fields by whether evidence of the enabling kind already exists."""

    suggestions = []
    for table_name, table in sorted((ledger.get("tables") or {}).items()):
        for field_name, field in sorted((table.get("fields") or {}).items()):
            if field.get("status") != "unobserved":
                continue
            kind = FIELD_EVIDENCE_KINDS.get((table_name, field_name))
            if kind is None and table_name not in CORE_TABLES:
                continue
            available = fact_kind_counts.get(kind or "", 0)
            if kind is None:
                hint = "尚无对账规则，需要新增规则或长时间观察后再判定"
            elif available:
                hint = f"已有 {available} 条 {kind} 事实，需要覆盖到该行"
            else:
                hint = f"缺少 {kind} 动作样本，需要先跑出该动作"
            suggestions.append(
                {
                    "table": table_name,
                    "field": field_name,
                    "evidence_kind": kind or "-",
                    "available_facts": available,
                    "rule_exists": kind is not None,
                    "hint": hint,
                }
            )
    return sorted(
        suggestions,
        key=lambda item: (
            not item["rule_exists"],
            -item["available_facts"],
            item["table"],
            item["field"],
        ),
    )[:limit]


_SAVE_VERDICT_LABELS = {
    "match": "一致",
    "column_set_differs": "列不一致",
    "type_differs": "类型不一致",
    "declared_only": "仅声明",
    "live_only": "仅实机",
}

# One reconciled fact per pseudo-table: these are code-declared constants, not
# configuration rows, so they never enter the field ledger.
CODE_DECLARED_TABLES = ("EnergyModel", "ShopModel")


def _save_schema_section(save_schema: dict[str, Any] | None) -> list[str]:
    """Render the declared-vs-live save schema reconciliation."""

    if not save_schema:
        return []
    totals = save_schema.get("totals") or {}
    reader = save_schema.get("save_reader") or {}
    save = save_schema.get("save") or {}
    lines = [
        "",
        "## 6. 存档结构对账（DBTableConfigs 声明 ↔ 实机存档）",
        "",
        f"存档副本：`{save.get('source_ref', '')}`（sha256 `{save.get('encrypted_sha256', '')}`）。",
        "",
        "| 口径 | 数量 |",
        "|---|---:|",
        f"| 声明表 | {totals.get('declared_tables', 0)} |",
        f"| 实机表 | {totals.get('live_tables', 0)} |",
        f"| 表名与列全一致 | {totals.get('match', 0)} |",
        f"| 列集合不一致 | {totals.get('column_set_differs', 0)} |",
        f"| 列类型不一致 | {totals.get('type_differs', 0)} |",
        f"| 仅声明（实机无此表） | {totals.get('declared_only', 0)} |",
        f"| 仅实机（声明无此表） | {totals.get('live_only', 0)} |",
        f"| 声明列总数 | {totals.get('declared_columns', 0)} |",
        f"| 声明与实机同名列 | {totals.get('matched_columns', 0)} |",
        f"| 仅声明列 | {totals.get('declared_only_columns', 0)} |",
        f"| 仅实机列 | {totals.get('live_only_columns', 0)} |",
        f"| 列类型不一致数 | {totals.get('type_mismatch_columns', 0)} |",
        f"| save_reader 已解码的声明表 | {reader.get('read_table_count', 0)} |",
        "",
        "save_reader 从未读过的声明表："
        + ("、".join(reader.get("never_read_tables") or []) or "无"),
        "",
        "仅列出与声明不完全一致的表；其余为逐列一致。",
        "",
        "| 逻辑名 | 存档表 | 判定 | 仅声明列 | 仅实机列 | 类型不一致 |",
        "|---|---|---|---|---|---|",
    ]
    differing = [
        (name, entry)
        for name, entry in sorted((save_schema.get("tables") or {}).items())
        if entry.get("verdict") != "match"
    ]
    if not differing:
        lines = lines[:-2] + ["45 张声明表与实机存档逐列一致，无差异行。"]
        return lines
    for name, entry in differing:
        lines.append(
            "| {logical} | {table} | {verdict} | {declared_only} | {live_only} | {types} |".format(
                logical=_escape(name),
                table=_escape(entry.get("table", "")),
                verdict=_SAVE_VERDICT_LABELS.get(
                    str(entry.get("verdict")), str(entry.get("verdict"))
                ),
                declared_only=_escape(", ".join(entry.get("declared_only_columns") or []) or "-"),
                live_only=_escape(", ".join(entry.get("live_only_columns") or []) or "-"),
                types=_escape(
                    ", ".join(
                        f"{column}: {value.get('declared')}→{value.get('live')}"
                        for column, value in sorted(
                            (entry.get("type_mismatches") or {}).items()
                        )
                    )
                    or "-"
                ),
            )
        )
    return lines


def _backfilled_facts_section(results: Iterable[Any]) -> list[str]:
    """Render the verdicts that check live evidence against code constants."""

    rows = [
        result
        for result in results
        if result.table in CODE_DECLARED_TABLES
    ]
    if not rows:
        return []
    lines = [
        "",
        "## 7. 回填事实（代码声明常量 ↔ 实机差分）",
        "",
        "这几条的声明方是反编译代码里的常量，不是配置表行，所以只在本节报告，不进字段台账。",
        "",
        "| 声明来源 | 字段 | 判定 | 依据 | 证据 |",
        "|---|---|---|---|---|",
    ]
    for result in sorted(
        rows, key=lambda item: (item.table, item.field, item.fact_id)
    ):
        lines.append(
            "| {table} | {field} | {status} | {reason} | {facts} |".format(
                table=_escape(result.table),
                field=_escape(result.field),
                status=_escape(result.status),
                reason=_escape(result.reason),
                facts=_escape(", ".join(result.all_fact_ids[:3])),
            )
        )
    return lines


def _free_claim_section(
    facility: dict[str, Any] | None, results: list[Any]
) -> list[str]:
    """Render what the save says every guard claim and close actually moved."""

    if not facility:
        return []
    view = (facility.get("facility_facts") or {}).get("free_claim") or {}
    rows = (facility.get("details") or {}).get("free_claim_rows") or []
    if not view.get("actions"):
        return []
    verdicts = [
        result
        for result in results
        if result.table in {"GuardRedline", "LevelConfig"}
        and result.field in {"claim_grants_nothing", "rewards"}
        and result.stats is not None
    ]
    lines = [
        "",
        "## 8. free-claim 与关闭动作的存档差分对账",
        "",
        "差分由两次存档回读夹出，属于整个窗口而不是单次点击；只有窗口内全部是"
        "领取/关闭类动作时才可归因，其余只记录。领取面若无配表声明则标 "
        "code/server-declared，不判定。",
        "",
        "| 口径 | 数量 |",
        "|---|---:|",
        f"| 领取/关闭类动作 | {view.get('actions', 0)} |",
        f"| 带存档测量的动作 | {view.get('actions_with_measurement', 0)} |",
        f"| 测量组 | {view.get('measurement_groups', 0)} |",
        f"| 可归因组 | {view.get('attributable_groups', 0)} |",
        f"| 窗口不可归因组 | {view.get('unattributable_groups', 0)} |",
        "",
        f"可归因组合计差分：{view.get('granted_totals_over_attributable_groups') or '无'}",
        "",
        "| 界面 | 动作数 | 已测量 | 动作类型 | 声明来源 |",
        "|---|---:|---:|---|---|",
    ]
    for screen, entry in (view.get("by_screen") or {}).items():
        lines.append(
            "| {screen} | {actions} | {measured} | {kinds} | {declaration} |".format(
                screen=_escape(screen),
                actions=entry.get("actions", 0),
                measured=entry.get("measured", 0),
                kinds=_escape(entry.get("kinds")),
                declaration=_escape(entry.get("declaration")),
            )
        )
    lines += [
        "",
        "| 测量组 | 界面 | 类型 | 可归因 | 窗口 | 差分 | 棋盘物件差分 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {group} | {screens} | {kinds} | {attributable} | {window} | {delta} | "
            "{items} |".format(
                group=_escape(row.get("group_id")),
                screens=_escape("、".join(row.get("screens") or [])),
                kinds=_escape("、".join(row.get("kinds") or [])),
                attributable="是" if row.get("attributable") else "否",
                window=_escape(row.get("window")),
                delta=_escape(row.get("delta")),
                items=_escape(row.get("board_items_delta") or "-"),
            )
        )
    if verdicts:
        lines += [
            "",
            "| 断言 | 行 | 判定 | 依据 | 证据 |",
            "|---|---|---|---|---|",
        ]
        for result in sorted(
            verdicts, key=lambda item: (item.table, item.field, item.fact_id)
        ):
            lines.append(
                "| {table}.{field} | {row} | {status} | {reason} | {facts} |".format(
                    table=_escape(result.table),
                    field=_escape(result.field),
                    row=_escape(
                        ", ".join(
                            f"{key}={value}"
                            for key, value in sorted(result.row_keys.items())
                        )
                    ),
                    status=_escape(result.status),
                    reason=_escape(result.reason),
                    facts=_escape(", ".join(result.all_fact_ids[:3])),
                )
            )
    return lines


def _paperbox_attribution_section(results: list[Any]) -> list[str]:
    """Render which action stripped which sand cell, and what it is priced at."""

    attributed = [
        result
        for result in results
        if result.table == "MainPaperboxExtraRewardConfig"
        and result.field == "cell_reward"
        and (result.stats or {}).get("attributed_action_id")
    ]
    triggers = [
        result
        for result in results
        if result.table == "BoardModelConfig" and result.field == "paperbox_clear_trigger"
    ]
    if not attributed and not triggers:
        return []
    observed = sum(1 for result in triggers if result.status == "observed")
    lines = [
        "",
        "## 9. 沙土剥层归因（合成落点四邻域）",
        "",
        "| 口径 | 数量 |",
        "|---|---:|",
        f"| 归因到单次合成的剥层 | {len(attributed)} |",
        f"| 剥层触发判定 observed | {observed} |",
        f"| 剥层触发判定 contradicted | {len(triggers) - observed} |",
        "",
        "| 格 | 归因动作 id | 合成落点 | 格奖励 | 判定 |",
        "|---|---|---|---|---|",
    ]
    for result in sorted(
        attributed, key=lambda item: (item.row_keys.get("row"), item.row_keys.get("col"))
    ):
        stats = result.stats or {}
        lines.append(
            "| ({row},{col}) | {action} | {target} | {reward} | {status} |".format(
                row=_escape(result.row_keys.get("row")),
                col=_escape(result.row_keys.get("col")),
                action=_escape(stats.get("attributed_action_id")),
                target=_escape(stats.get("merge_target")),
                reward=_escape(stats.get("configured_reward")),
                status=_escape(result.status),
            )
        )
    return lines


def _order_display_section(facility: dict[str, Any] | None) -> list[str]:
    """Render the deliverable-leftmost counts and the two candidate rules."""

    if not facility:
        return []
    view = (facility.get("facility_facts") or {}).get("order_display_rank") or {}
    if not view.get("samples"):
        return []
    lines = [
        "",
        "## 10. 订单显示序计数",
        "",
        "配表没有声明显示序，所以本节只记录命中与不命中，不判定任何字段。两条候选"
        "规则只在显示位 2/3 上分歧，而语料里从未在 2/3 位见过绿按钮，因此仍不可分辨。",
        "",
        "| 口径 | 数量 |",
        "|---|---:|",
        f"| 样本 | {view.get('samples', 0)} |",
        f"| 带按钮显示位的样本 | {view.get('samples_with_button_positions', 0)} |",
        f"| 可判定样本 | {view.get('judged_samples', 0)} |",
        f"| 可交付恒最左命中 | {view.get('deliverable_leftmost_hits', 0)} |",
        f"| 可交付恒最左不命中 | {view.get('deliverable_leftmost_misses', 0)} |",
        f"| 存档与屏幕计数不一致样本 | {view.get('save_screen_disagreement_samples', 0)} |",
        f"| 仅有交付配对（无按钮位）的样本 | {view.get('delivery_evidence_samples', 0)} |",
        f"| 两条候选预测一致的样本 | {view.get('candidates_indistinguishable_samples', 0)} |",
        f"| 第 2/3 位出现按钮的帧（alert） | {view.get('button_at_display_position_2_or_3', 0)} |",
        "",
        "| 候选规则 | 命中 | 不命中 | 不可判定 |",
        "|---|---:|---:|---:|",
    ]
    for name, entry in (view.get("candidates") or {}).items():
        lines.append(
            "| {name} | {hits} | {misses} | {unjudged} |".format(
                name=_escape(name),
                hits=entry.get("hits", 0),
                misses=entry.get("misses", 0),
                unjudged=entry.get("unjudged", 0),
            )
        )
    alerts = view.get("alerts") or []
    lines += ["", "第 2/3 位按钮 alert："]
    if not alerts:
        lines.append("无。")
    else:
        lines += ["", "| 运行 | 回合 | 帧 sha256 | 按钮显示位 | 可交付槽 |", "|---|---:|---|---|---|"]
        for alert in alerts:
            lines.append(
                "| {run} | {round} | {frame} | {positions} | {slots} |".format(
                    run=_escape(alert.get("run")),
                    round=_escape(alert.get("round")),
                    frame=_escape(alert.get("frame_sha256")),
                    positions=_escape(alert.get("button_display_positions")),
                    slots=_escape(alert.get("fulfillable_slots")),
                )
            )
    return lines


def _activity_and_verdict_section(facility: dict[str, Any] | None) -> list[str]:
    """The F7-04 families: activity score, story advances, reserve, deviation.

    Each block names its own sample count first.  A claim with no samples is
    printed as having none rather than as a rate over an empty set, because the
    energy-party score never left zero in the corpus this was written against
    and a reader has to be able to see that from the page.
    """

    if not facility:
        return []
    families = facility.get("facility_facts") or {}
    plane = families.get("trigger_plane") or {}
    story = families.get("story_advance") or {}
    reserve = families.get("order_reserve") or {}
    deviation = families.get("generate_deviation") or {}
    if not any((plane, story, reserve, deviation)):
        return []
    lines = ["", "## 12. 能量狂欢、剧情推进、订单预留与偏差裁定", "", "| 项 | 值 |", "|---|---:|"]
    lines += [
        f"| 活动回合样本 | {plane.get('round_samples', 0)} |",
        f"| 其中分数变动样本 | {plane.get('score_samples', 0)} |",
        f"| 到档发奖样本 | {plane.get('reward_samples', 0)} |",
        f"| energyConsumed 累计 | {plane.get('energy_consumed_total', 0)} |",
        f"| 同期 confirmed 生成成本 | {plane.get('expected_generate_cost_total', 0)} |",
    ]
    status = plane.get("daily_status") or {}
    if status:
        lines += ["", "活动每日状态（TriggerPlaneDailyStatus 1=未激活 2=激活 3=超时 4=满级）：", "", "| 状态 | 回合 |", "|---|---:|"]
        for name, count in status.items():
            lines.append(f"| {_escape(name)} | {count} |")
    if not plane.get("score_samples"):
        lines += [
            "",
            "分数规则本期无样本：分数全程为 0，不判定。",
        ]
    lines += [
        "",
        f"剧情推进 {story.get('samples', 0)} 次，带帧摘要 {story.get('with_frame_digest', 0)} 次；"
        f"同一时刻双计 {len(story.get('double_counted_moments') or [])} 处。",
        "",
        "| 来源 | 次数 |",
        "|---|---:|",
    ]
    per_stream = story.get("per_stream") or {}
    if not per_stream:
        lines.append("| 无记录 | 0 |")
    for stream, count in per_stream.items():
        lines.append(f"| {_escape(stream)} | {count} |")
    lines += [
        "",
        f"订单预留挡下的可合对 {reserve.get('samples', 0)} 例（只计数，不判定）。",
        "",
        f"放宽后的生成裁定（confirmed_with_deviation）{deviation.get('samples', 0)} 例，"
        f"其中落点与预测一致 {deviation.get('matched_prediction', 0)} 例；"
        "该类别单列，不计入 observed 也不计入 contradicted。",
    ]
    origins = deviation.get("by_origin") or {}
    if origins:
        lines += ["", "| 来源 | 例数 |", "|---|---:|"]
        for origin, count in origins.items():
            lines.append(f"| {_escape(origin)} | {count} |")
    return lines


def _guard_and_stop_section(facility: dict[str, Any] | None) -> list[str]:
    """Render guard actions, red-line refusals and stop reasons."""

    if not facility:
        return []
    guard = facility.get("guard_events") or {}
    timing = (facility.get("facility_facts") or {}).get("save_read_timing") or {}
    if not guard.get("total"):
        return []
    lines = [
        "",
        "## 11. 守卫与停机事实",
        "",
        "| 事件 | 数量 |",
        "|---|---:|",
    ]
    for event, count in (guard.get("by_event") or {}).items():
        lines.append(f"| {_escape(event)} | {count} |")
    lines += ["", "按界面的守卫动作：", "", "| 界面 | 次数 |", "|---|---:|"]
    for screen, count in (guard.get("guard_actions_by_screen") or {}).items():
        lines.append(f"| {_escape(screen)} | {count} |")
    lines += ["", "按原因的停机：", "", "| 口径 | 原因 | 次数 |", "|---|---|---:|"]
    outside = guard.get("segment_stop_reasons_outside_vocabulary") or {}
    for reason, count in (guard.get("segment_stop_reasons") or {}).items():
        mark = "（不在当前停机原因表内）" if reason in outside else ""
        lines.append(f"| 段 | {_escape(reason)}{mark} | {count} |")
    for reason, count in (guard.get("driver_stop_reasons") or {}).items():
        lines.append(f"| 驱动 | {_escape(reason)} | {count} |")
    for reason, count in (guard.get("driver_stop_taxonomy") or {}).items():
        lines.append(f"| 驱动 stop_taxonomy | {_escape(reason)} | {count} |")
    states = guard.get("guard_states") or {}
    lines += ["", "守卫状态进出：", "", "| 状态 | 进入 | 退出 |", "|---|---:|---:|"]
    if not states:
        lines.append("| 无记录 | 0 | 0 |")
    for state, entry in states.items():
        lines.append(
            f"| {_escape(state)} | {entry.get('entered', 0)} | {entry.get('left', 0)} |"
        )
    refusals = guard.get("pre_frame_refusals_by_screen") or {}
    lines += ["", "前帧拒发（按拒发时读到的界面）：", "", "| 界面 | 次数 |", "|---|---:|"]
    if not refusals:
        lines.append("| 无记录 | 0 |")
    for screen, count in refusals.items():
        lines.append(f"| {_escape(screen)} | {count} |")
    violations = guard.get("redline_violations") or []
    lines += ["", f"红线违规 {len(violations)} 条。"]
    if violations:
        lines += ["", "| 界面 | 意图 | 动作 id | 报告 |", "|---|---|---|---|"]
        for violation in violations:
            lines.append(
                "| {screen} | {intent} | {action} | {report} |".format(
                    screen=_escape(
                        violation.get("forbidden_surface")
                        or violation.get("screen")
                        or "未识别"
                    ),
                    intent=_escape(violation.get("intent")),
                    action=_escape(violation.get("action_id")),
                    report=_escape(violation.get("source_report")),
                )
            )
    if timing.get("samples"):
        lines += [
            "",
            "回读分阶段（设施口径，仅记录，不进配表台账）：",
            "",
            "| 阶段 | 样本 | p50 | min | max |",
            "|---|---:|---:|---:|---:|",
        ]
        for stage, entry in (timing.get("stages") or {}).items():
            lines.append(
                "| {stage} | {n} | {p50} | {min} | {max} |".format(
                    stage=_escape(stage),
                    n=entry.get("n", 0),
                    p50=entry.get("p50"),
                    min=entry.get("min"),
                    max=entry.get("max"),
                )
            )
        lines += ["", f"快路径与缓存标志：{(timing.get('flags') or {})}"]
    return lines


def render_report_md(
    summary: dict[str, Any],
    row_coverage: dict[str, Any],
    results: Iterable[Any],
    ledger: dict[str, Any],
    fact_kind_counts: dict[str, int],
    save_schema: dict[str, Any] | None = None,
    facility: dict[str, Any] | None = None,
) -> str:
    """Render the human-readable contradiction and hot-spot report."""

    results = list(results)
    lines: list[str] = [
        "# Romantic Restaurant 对账报告",
        "",
        "> 本文件由 `python -m omnicompany.packages.domains.game_observatory.crossref"
        " writeback` 生成，不要手改。",
        f"> 证据时间上界：{summary.get('generated_at', '')}",
        "",
        "## 1. 矛盾清单",
        "",
    ]
    contradictions = _contradiction_rows(results)
    if contradictions:
        lines += [
            "| 表 | 字段 | 行 | 实测与配置 | 证据 |",
            "|---|---|---|---|---|",
        ]
        lines += [
            "| {table} | {field} | {row} | {reason} | {facts} |".format(
                table=_escape(item["table"]),
                field=_escape(item["field"]),
                row=_escape(item["row"]),
                reason=_escape(item["reason"]),
                facts=_escape(", ".join(item["fact_ids"][:4])),
            )
            for item in contradictions
        ]
    else:
        lines.append("当前无矛盾记录。")
    lines += ["", "## 2. 逐表字段覆盖率与行覆盖率", "", "| 表 | 字段 observed/总数 | 字段对账率 | 行已验证/总行 | 行对账率 | 矛盾行 |", "|---|---:|---:|---:|---:|---:|"]
    rows = row_coverage.get("tables") or {}
    for table_name in sorted(set(summary.get("tables") or {}) | set(rows)):
        field_stats = (summary.get("tables") or {}).get(table_name) or {}
        row_stats = rows.get(table_name) or {}
        if not field_stats.get("observed") and not row_stats.get("verified_rows"):
            continue
        lines.append(
            "| {table} | {observed}/{total} | {rate:.4f} | {vrows}/{trows} | "
            "{rrate:.4f} | {crows} |".format(
                table=_escape(table_name),
                observed=field_stats.get("observed", 0),
                total=field_stats.get("total", 0),
                rate=field_stats.get("coverage_rate", 0.0),
                vrows=row_stats.get("verified_rows", 0),
                trows=row_stats.get("total_rows", 0),
                rrate=row_stats.get("row_coverage_rate", 0.0),
                crows=row_stats.get("contradicted_rows", 0),
            )
        )
    lines += ["", "## 3. 未观测热区（top_unobserved_tables）", "", "| 表 | 未观测字段数 | 字段总数 |", "|---|---:|---:|"]
    for entry in (summary.get("top_unobserved_tables") or [])[:20]:
        lines.append(
            "| {table} | {unobserved} | {total} |".format(
                table=_escape(entry.get("table")),
                unobserved=entry.get("unobserved"),
                total=entry.get("total"),
            )
        )
    lines += ["", "## 4. 最容易靠现有动作补齐的未观测字段", "", "| 表 | 字段 | 需要的事实类型 | 现有事实数 | 建议 |", "|---|---|---|---:|---|"]
    for entry in _fill_suggestions(ledger, fact_kind_counts):
        lines.append(
            "| {table} | {field} | {kind} | {count} | {hint} |".format(
                table=_escape(entry["table"]),
                field=_escape(entry["field"]),
                kind=_escape(entry["evidence_kind"]),
                count=entry["available_facts"],
                hint=_escape(entry["hint"]),
            )
        )
    weights = row_coverage.get("generator_weight_stats") or {}
    if weights:
        from .rules import MIN_WEIGHT_SAMPLE, WEIGHT_ALPHA

        lines += [
            "",
            "## 5. 生成器权重统计",
            "",
            f"判定门槛：样本量 ≥ {MIN_WEIGHT_SAMPLE} 且卡方 p ≥ {WEIGHT_ALPHA} 记 observed，"
            f"p < {WEIGHT_ALPHA} 或出现声明外产出记 contradicted，样本不足只记录不判定。",
            "",
            "| 生成器 | 样本量 | chi2 | df | p | 判定 | 声明权重 | 实测频次 |",
            "|---|---:|---:|---:|---:|---|---|---|",
        ]
        for generator, entry in sorted(weights.items()):
            fit = entry.get("fit") or {}
            sample = fit.get("sample_size", 0)
            p_value = fit.get("p_value")
            if fit.get("off_list"):
                verdict = "contradicted（出现声明外产出）"
            elif sample < MIN_WEIGHT_SAMPLE:
                verdict = "样本不足，不判定"
            elif isinstance(p_value, (int, float)):
                verdict = "observed" if p_value >= WEIGHT_ALPHA else "contradicted"
            else:
                verdict = "无声明权重"
            lines.append(
                "| {generator} | {n} | {chi2} | {df} | {p} | {verdict} | {declared} | "
                "{observed} |".format(
                    generator=_escape(generator),
                    n=sample,
                    chi2=fit.get("statistic", ""),
                    df=fit.get("degrees_of_freedom", ""),
                    p=p_value if p_value is not None else "",
                    verdict=_escape(verdict),
                    declared=_escape(entry.get("declared_weights")),
                    observed=_escape(fit.get("observed")),
                )
            )
    lines += _save_schema_section(save_schema)
    lines += _backfilled_facts_section(results)
    lines += _free_claim_section(facility, results)
    lines += _paperbox_attribution_section(results)
    lines += _order_display_section(facility)
    lines += _guard_and_stop_section(facility)
    lines += _activity_and_verdict_section(facility)
    return "\n".join(lines) + "\n"


def write_report_md(text: str, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8", newline="\n")
    return target
