"""Apply rule results to the field-complete ledger and documentation."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ..design_extraction.render_docs import AUTHORED_DIRNAME, GENERATOR_MARKER
from .evidence_index import FactV1, read_facts
from .facility import build_facility_view
from .report import render_report_md, write_report_md, write_summary
from .row_coverage import build_row_coverage, write_row_coverage
from .rules import CLAIM_DECLARING_TABLES, RuleResult, apply_rules
from .save_schema import read_save_schema_coverage

# Aggregate evidence feeds the statistical rules; it is not a standalone
# gameplay observation, so it never lands in the unregistered-observation log.
# The F6-04 facility families join it: a save-read timing, a guard action and a
# tray reading are instrumentation, and their place is the facility section of
# the report, not a log of gameplay rules waiting for a configuration field.
_AGGREGATE_KINDS = frozenset(
    {
        "board_snapshot",
        "generator_cooldown",
        "generator_output_sample",
        "generator_runtime",
        "guard_event",
        "order_appearance",
        "order_display_rank",
        "save_read_timing",
        # F7-04.  A story advance, a reserve refusal, a relaxed verdict and an
        # activity row are all instrumentation of the same kind: they are
        # counted in the facility section and never wait for a config field.
        "generate_deviation",
        "order_reserve_block",
        "story_advance",
        "trigger_plane_round",
        "trigger_plane_reward",
    }
)


def _write_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8", newline="\n")


def _apply_results(ledger: dict[str, Any], results: Iterable[RuleResult]) -> None:
    grouped: dict[tuple[str, str], list[RuleResult]] = defaultdict(list)
    for result in results:
        grouped[(result.table, result.field)].append(result)
    for (table_name, field_name), matches in grouped.items():
        table = (ledger.get("tables") or {}).get(table_name) or {}
        field = (table.get("fields") or {}).get(field_name)
        if not isinstance(field, dict):
            continue
        old_status = field.get("status", "unobserved")
        statuses = {match.status for match in matches}
        if "contradicted" in statuses:
            new_status = "contradicted"
        elif "observed" in statuses:
            new_status = "observed"
        else:
            new_status = old_status
        field["status"] = new_status
        field["fact_ids"] = sorted(
            set(field.get("fact_ids") or [])
            | {fact_id for match in matches for fact_id in match.all_fact_ids}
        )


def _set_evidence_times(
    ledger: dict[str, Any], results: Iterable[RuleResult], facts_by_id: dict[str, FactV1]
) -> None:
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for result in results:
        for fact_id in result.all_fact_ids:
            fact = facts_by_id.get(fact_id)
            if fact and fact.captured_at:
                grouped[(result.table, result.field)].append(fact.captured_at)
    for (table_name, field_name), timestamps in grouped.items():
        table = (ledger.get("tables") or {}).get(table_name) or {}
        field = (table.get("fields") or {}).get(field_name)
        if not isinstance(field, dict):
            continue
        field["last_evidence_at"] = max(timestamps + [field.get("last_evidence_at", "")])


def _frontmatter_tables(text: str) -> list[str]:
    match = re.search(r'^配表:\s*(\[.*\])\s*$', text, flags=re.MULTILINE)
    if not match:
        return []
    try:
        return [str(value) for value in json.loads(match.group(1))]
    except json.JSONDecodeError:
        return []


def _add_frontmatter_count(text: str, count: int) -> str:
    line = f"实机已验证字段数: {count}"
    if re.search(r"^实机已验证字段数:.*$", text, flags=re.MULTILINE):
        return re.sub(r"^实机已验证字段数:.*$", line, text, flags=re.MULTILINE)
    return re.sub(r"^(证据等级:.*)$", rf"\1\n{line}", text, count=1, flags=re.MULTILINE)


_EXTRA_COLUMNS = ("实机证据", "实机验证行数", "矛盾数")
_MAX_DOC_FACT_IDS = 3
_HEADER_ALIASES = {"行\\列": "row", "行": "row", "行/列": "row"}


def _row_matches(
    headers: list[str], cells: list[str], table_names: list[str], results: list[RuleResult]
) -> list[RuleResult]:
    """Select rule results whose configuration row this markdown row stands for.

    A markdown row may name a whole group of configuration rows (a board row, a
    main-task day), so results match on the columns the two have in common.
    """

    values = {
        _HEADER_ALIASES.get(header, header): cell
        for header, cell in zip(headers, cells)
    }
    matched = []
    for result in results:
        if result.table not in table_names or not result.row_keys:
            continue
        shared = set(result.row_keys) & set(values)
        if not shared:
            continue
        if all(values[key] == result.row_keys[key] for key in shared):
            matched.append(result)
    return matched


def _row_annotations(
    headers: list[str], cells: list[str], table_names: list[str], results: list[RuleResult]
) -> dict[str, str]:
    matched = _row_matches(headers, cells, table_names, results)
    fact_ids = sorted(
        {
            fact_id
            for result in matched
            if result.status == "observed"
            for fact_id in result.all_fact_ids
        }
    )
    verified = {
        tuple(sorted(result.row_keys.items()))
        for result in matched
        if result.status == "observed"
    }
    contradicted = {
        tuple(sorted(result.row_keys.items()))
        for result in matched
        if result.status == "contradicted"
    }
    # Design documents cite a readable head of the evidence; the complete list
    # per row stays in crossref/row_coverage.v1.json and the ledger.
    shown = ", ".join(fact_ids[:_MAX_DOC_FACT_IDS])
    if len(fact_ids) > _MAX_DOC_FACT_IDS:
        shown = f"{shown} 等 {len(fact_ids)} 条"
    return {
        "实机证据": shown,
        "实机验证行数": str(len(verified - contradicted)),
        "矛盾数": str(len(contradicted)),
    }


def _strip_appended_columns(cells: list[str]) -> list[str]:
    """Drop the annotation columns a previous writeback appended.

    Writeback only ever appends, so its own columns are a suffix. Removing a
    trailing run rather than every cell that happens to carry one of these
    names keeps a generator-authored column of the same name (the design index
    has its own ``实机验证行数``) in place, along with the values under it.
    """

    end = len(cells)
    while end and cells[end - 1] in _EXTRA_COLUMNS:
        end -= 1
    return cells[:end]


def _augment_markdown_tables(
    text: str, table_names: list[str], results: list[RuleResult]
) -> str:
    lines = text.splitlines()
    output: list[str] = []
    headers: list[str] | None = None
    original_headers: list[str] = []
    appended: list[str] = []
    in_table = False
    for line in lines:
        if not line.startswith("|"):
            headers = None
            in_table = False
            output.append(line)
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if headers is None:
            original_headers = _strip_appended_columns(cells)
            # A column the source table already owns keeps its own meaning; the
            # annotation of the same name would otherwise duplicate the header.
            appended = [name for name in _EXTRA_COLUMNS if name not in original_headers]
            headers = original_headers + appended
            output.append("| " + " | ".join(headers) + " |")
            in_table = True
            continue
        if in_table and all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            cells = cells[: len(original_headers)]
            cells += ["---"] * (len(headers) - len(cells))
            output.append("|" + "|".join(cells) + "|")
            continue
        cells = cells[: len(original_headers)]
        cells += [""] * (len(original_headers) - len(cells))
        annotations = _row_annotations(original_headers, cells, table_names, results)
        cells.extend(annotations[name] for name in appended)
        output.append("| " + " | ".join(cells) + " |")
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def _frontmatter(text: str) -> str:
    """The block between the opening and closing ``---`` lines, if any."""

    if not text.startswith("---"):
        return ""
    lines = text.splitlines()[1:]
    block = []
    for line in lines:
        if line.strip() == "---":
            return "\n".join(block)
        block.append(line)
    return ""


def is_generated_document(text: str) -> bool:
    """Whether the write-back owns this document.

    Only the design generator writes the marker line, and it writes it into
    frontmatter, so the index page (no frontmatter) and any hand-written page
    in the same directory are excluded by the same rule. Documents the
    write-back does not own are never read for tables and never rewritten.
    """

    return any(
        line.strip() == GENERATOR_MARKER for line in _frontmatter(text).splitlines()
    )


def update_design_docs(
    docs_dir: str | Path, ledger: dict[str, Any], results: list[RuleResult]
) -> list[Path]:
    changed = []
    for path in sorted(Path(docs_dir).glob("*.md")):
        if AUTHORED_DIRNAME in path.parts:
            continue
        original = path.read_text(encoding="utf-8")
        if not is_generated_document(original):
            continue
        tables = _frontmatter_tables(original)
        count = sum(
            1
            for table_name in tables
            for field in (((ledger.get("tables") or {}).get(table_name) or {}).get("fields") or {}).values()
            if field.get("status") == "observed"
        )
        updated = _add_frontmatter_count(original, count)
        updated = _augment_markdown_tables(updated, tables, results)
        if updated != original:
            _write_if_changed(path, updated)
            changed.append(path)
    return changed


def _is_unregistered_claim(fact: FactV1) -> bool:
    """Whether a claim fact is a gameplay rule with no configuration field.

    Only a claim that can grant qualifies.  A close or a dismiss declares
    nothing to observe -- what it moved is adjudicated as the red-line
    assertion and reported there -- and a surface whose grant a table declares
    is not unregistered at all.
    """

    return str(fact.before.get("action_kind")) == "free-claim" and str(
        fact.before.get("screen")
    ) not in CLAIM_DECLARING_TABLES


def append_unregistered(
    path: str | Path, facts: Iterable[FactV1], results: Iterable[RuleResult]
) -> list[str]:
    matched_ids = {fact_id for result in results for fact_id in result.all_fact_ids}
    unregistered = [
        fact
        for fact in facts
        if fact.fact_id not in matched_ids
        and fact.kind not in _AGGREGATE_KINDS
        and (fact.kind != "free_claim_delta" or _is_unregistered_claim(fact))
    ]
    if not unregistered:
        return []
    target = Path(path)
    if target.is_file():
        text = target.read_text(encoding="utf-8")
    else:
        text = (
            "# 未登记实机观测\n\n"
            "| fact_id | 观测 | 证据 | 处置 |\n"
            "|---|---|---|---|\n"
        )
    added = []
    for fact in sorted(unregistered, key=lambda item: item.fact_id):
        if fact.fact_id in text:
            continue
        if fact.kind == "merge" and any(
            str(change.get("before", "")).startswith(("c#", "pb#c#"))
            for change in fact.delta.get("board") or []
        ):
            observation = "蛛网合成清除并解锁相邻格"
        elif fact.kind == "free_claim_delta":
            # No configuration table declares what this surface grants, so the
            # claim is a gameplay rule with no field to observe.
            observation = (
                f"{fact.before.get('screen')} 的免费领取"
                f"（{fact.before.get('element')}）无配表声明，发放由代码或服务端决定"
            )
        else:
            observation = str(fact.after.get("observation") or fact.kind)
        observation = observation.replace("|", "\\|")
        evidence = "<br>".join(fact.evidence_refs).replace("|", "\\|")
        text += (
            f"| {fact.fact_id} | {observation} | {evidence} | "
            "实机验证的玩法规则，无配置字段对应 |\n"
        )
        added.append(fact.fact_id)
    if added:
        _write_if_changed(target, text)
    return added


def writeback_ledger(
    *,
    facts_path: str | Path,
    ledger_path: str | Path,
    config_root: str | Path,
    summary_path: str | Path,
    docs_dir: str | Path | None = None,
    observations_path: str | Path | None = None,
    row_coverage_path: str | Path | None = None,
    report_md_path: str | Path | None = None,
    save_schema_path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply evidence while preserving the ledger schema and prior observations."""

    facts = read_facts(facts_path)
    facts_by_id = {fact.fact_id: fact for fact in facts}
    results = apply_rules(facts, config_root)
    target = Path(ledger_path)
    ledger = json.loads(target.read_text(encoding="utf-8"))
    _apply_results(ledger, results)
    _set_evidence_times(ledger, results, facts_by_id)
    rendered = json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_if_changed(target, rendered)
    provisional = write_summary(ledger, summary_path)
    row_coverage = build_row_coverage(
        results,
        facts_by_id,
        config_root,
        facts=facts,
        generated_at=provisional.get("generated_at", ""),
    )
    facility = build_facility_view(facts)
    summary = write_summary(ledger, summary_path, row_coverage, facility)
    if row_coverage_path:
        write_row_coverage(row_coverage, row_coverage_path)
    if report_md_path:
        fact_kind_counts: dict[str, int] = {}
        for fact in facts:
            fact_kind_counts[fact.kind] = fact_kind_counts.get(fact.kind, 0) + 1
        write_report_md(
            render_report_md(
                summary,
                row_coverage,
                results,
                ledger,
                fact_kind_counts,
                save_schema=(
                    read_save_schema_coverage(save_schema_path)
                    if save_schema_path
                    else None
                ),
                facility=facility,
            ),
            report_md_path,
        )
    changed_docs = update_design_docs(docs_dir, ledger, results) if docs_dir else []
    unregistered = (
        append_unregistered(observations_path, facts, results) if observations_path else []
    )
    return {
        "facts": len(facts),
        "facility_facts": facility["facility_facts"],
        "guard_events": facility["guard_events"],
        "rule_results": len(results),
        "observed_results": sum(result.status == "observed" for result in results),
        "contradicted_results": sum(result.status == "contradicted" for result in results),
        "updated_documents": [str(path) for path in changed_docs],
        "unregistered_added": unregistered,
        "summary": summary,
        "row_coverage": row_coverage.get("tables") and {
            table: {
                "verified_rows": value["verified_rows"],
                "total_rows": value["total_rows"],
                "contradicted_rows": value["contradicted_rows"],
            }
            for table, value in row_coverage["tables"].items()
        },
    }
