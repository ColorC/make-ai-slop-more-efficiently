"""Load the live-evidence products that the verified design layer cites.

Every loader here is read-only.  The cross-reference package owns the products
under ``crossref/``; this module only parses them so the generated documents can
quote fact identifiers, frame paths and coverage numbers instead of restating
configuration as if it had been observed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


REGISTRY_FILE = "unregistered-observations.md"
NAVIGATION_TRUTH = "config/game_observatory/romantic_restaurant/navigation.v1.json"
ORDER_GEOMETRY_TRUTH = "config/game_observatory/romantic_restaurant/order_geometry.v1.json"
BI_TRUTH = "synthesis/lua_code/Lua-Project/BIManager.lua"
SAVE_PROJECTION_TRUTH = (
    "data/domains/game_observatory/game_models/romantic-restaurant/save_projection/"
    "tutorial_state.v1.json"
)
FEASIBILITY_TRUTH = (
    "data/domains/game_observatory/game_models/romantic-restaurant/feasibility"
)

# Which run report stands for which piece of evidence.  Each pattern is written
# so that only the summary report of a work item matches it: the per-arm and
# per-drive files a long run writes while this generator is running carry a
# different infix ("-armA-", "-longrun3-") and are skipped, which keeps the
# selection stable while the gameplay line is still writing into the directory.
REPORT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("f4_06", "F4-06-2*.json"),
    ("f4_07", "F4-07-2*.json"),
    ("f4_08", "F4-08-2*.json"),
    ("f4_08_ab", "F4-08-ab-*.json"),
    ("f4_08_projection", "F4-08-projection-*.json"),
    ("f4_09_order_display", "F4-09-order-display-*.json"),
    ("f4_09_projection", "F4-09-projection-*.json"),
    ("f1_04", "F1-04-2*.json"),
)


@dataclass(frozen=True)
class MarkdownTable:
    """One pipe table lifted out of a record file."""

    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def _unescape(cell: str) -> str:
    return cell.replace("\\|", "|").strip()


def parse_markdown_tables(text: str) -> list[MarkdownTable]:
    """Split a markdown document into its pipe tables, preserving cell order."""

    tables: list[MarkdownTable] = []
    headers: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if headers is not None:
                tables.append(MarkdownTable(tuple(headers), tuple(rows)))
                headers, rows = None, []
            continue
        cells = [_unescape(cell) for cell in stripped.strip("|").split("|")]
        if headers is None:
            headers = cells
            rows = []
            continue
        if all(re.fullmatch(r":?-+:?", cell) for cell in cells if cell):
            continue
        cells = cells[: len(headers)] + [""] * max(0, len(headers) - len(cells))
        rows.append(dict(zip(headers, cells)))
    if headers is not None:
        tables.append(MarkdownTable(tuple(headers), tuple(rows)))
    return tables


def _table_with(tables: list[MarkdownTable], *required: str) -> MarkdownTable | None:
    for table in tables:
        if all(any(name == header for header in table.headers) for name in required):
            return table
    return None


def read_facts(path: Path) -> list[dict[str, Any]]:
    """Read the append-only fact stream, ignoring blank separator lines."""

    facts: list[dict[str, Any]] = []
    if not path.is_file():
        return facts
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            facts.append(json.loads(line))
    return facts


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def read_reports(feasibility_dir: str | Path | None) -> tuple[dict[str, Any], dict[str, str]]:
    """Pick one summary report per work item, newest first by file name.

    The names carry a UTC stamp, so the lexicographic maximum is the latest
    report of that item.  The chosen file name travels with the payload, so a
    document cites the report it actually read instead of a pattern.
    """

    payloads: dict[str, Any] = {}
    chosen: dict[str, str] = {}
    if feasibility_dir is None:
        return payloads, chosen
    root = Path(feasibility_dir)
    if not root.is_dir():
        return payloads, chosen
    for name, pattern in REPORT_PATTERNS:
        candidates = sorted(path for path in root.glob(pattern) if path.is_file())
        if not candidates:
            continue
        latest = candidates[-1]
        payload = _read_json(latest)
        if not payload:
            continue
        payloads[name] = payload
        chosen[name] = f"feasibility/{latest.name}"
    return payloads, chosen


def _read_source(path: str | Path | None) -> str:
    """Read one decompiled source file, or return empty text when absent."""

    if path is None:
        return ""
    candidate = Path(path)
    if not candidate.is_file():
        return ""
    return candidate.read_text(encoding="utf-8", errors="replace")


def parse_registry(text: str) -> list[dict[str, str]]:
    """Number the open-observation registry positionally, oldest row first."""

    tables = parse_markdown_tables(text)
    table = _table_with(tables, "观测描述", "猜测归属")
    if table is None:
        return []
    entries = []
    for index, row in enumerate(table.rows, start=1):
        entry = dict(row)
        entry["编号"] = f"UO-{index:02d}"
        entries.append(entry)
    return entries


def parse_long_run_timeline(text: str) -> list[dict[str, str]]:
    table = _table_with(parse_markdown_tables(text), "停机原因")
    return [dict(row) for row in table.rows] if table else []


def walk_frames(node: Any) -> Iterator[str]:
    """Yield every replay frame or screenshot reference under a survey subtree."""

    frame_keys = {
        "evidence_frame",
        "screenshot_path",
        "upper_screenshot_path",
        "lower_screenshot_path",
        "settled_screenshot_path",
    }
    list_keys = {"source_frames", "negative_evidence"}
    if isinstance(node, dict):
        for key, value in node.items():
            if key in frame_keys and isinstance(value, str):
                yield value
            elif key in list_keys and isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        yield item
            else:
                yield from walk_frames(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_frames(item)


def unique_frames(node: Any, limit: int | None = None) -> list[str]:
    seen: list[str] = []
    for frame in walk_frames(node):
        if frame not in seen:
            seen.append(frame)
    return seen if limit is None else seen[:limit]


@dataclass(frozen=True)
class EvidenceBundle:
    """Everything the verified layer is allowed to cite, already parsed."""

    facts: tuple[dict[str, Any], ...] = ()
    ledger: dict[str, Any] = field(default_factory=dict)
    row_coverage: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    save_schema: dict[str, Any] = field(default_factory=dict)
    navigation: dict[str, Any] = field(default_factory=dict)
    registry: tuple[dict[str, str], ...] = ()
    long_run: tuple[dict[str, str], ...] = ()
    record_tables: tuple[MarkdownTable, ...] = ()
    bi_source: str = ""
    energy_source: str = ""
    tutorial_state: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    order_geometry: dict[str, Any] = field(default_factory=dict)
    reports: dict[str, Any] = field(default_factory=dict)
    report_sources: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        *,
        crossref_dir: str | Path | None = None,
        navigation: str | Path | None = None,
        records_dir: str | Path | None = None,
        bi_source: str | Path | None = None,
        energy_source: str | Path | None = None,
        tutorial_state: str | Path | None = None,
        metrics: str | Path | None = None,
        order_geometry: str | Path | None = None,
        feasibility_dir: str | Path | None = None,
    ) -> EvidenceBundle:
        facts: tuple[dict[str, Any], ...] = ()
        ledger: dict[str, Any] = {}
        row_coverage: dict[str, Any] = {}
        summary: dict[str, Any] = {}
        save_schema: dict[str, Any] = {}
        if crossref_dir is not None:
            root = Path(crossref_dir)
            facts = tuple(read_facts(root / "facts.v1.ndjson"))
            ledger = _read_json(root / "config_crossref_ledger.v1.json")
            row_coverage = _read_json(root / "row_coverage.v1.json")
            summary = _read_json(root / "summary.v1.json")
            save_schema = _read_json(root / "save_schema_coverage.v1.json")
        navigation_payload = _read_json(Path(navigation)) if navigation is not None else {}
        registry: tuple[dict[str, str], ...] = ()
        long_run: tuple[dict[str, str], ...] = ()
        record_tables: list[MarkdownTable] = []
        if records_dir is not None:
            records = Path(records_dir)
            registry_path = records / REGISTRY_FILE
            if registry_path.is_file():
                registry = tuple(parse_registry(registry_path.read_text(encoding="utf-8")))
            for candidate in sorted(records.glob("long-run-*.md")):
                text = candidate.read_text(encoding="utf-8")
                long_run = tuple(parse_long_run_timeline(text))
                record_tables.extend(parse_markdown_tables(text))
        source_text = _read_source(bi_source)
        energy_text = _read_source(energy_source)
        projection = _read_json(Path(tutorial_state)) if tutorial_state is not None else {}
        dashboard = _read_json(Path(metrics)) if metrics is not None else {}
        geometry = _read_json(Path(order_geometry)) if order_geometry is not None else {}
        reports, report_sources = read_reports(feasibility_dir)
        return cls(
            facts=facts,
            ledger=ledger,
            row_coverage=row_coverage,
            summary=summary,
            save_schema=save_schema,
            navigation=navigation_payload,
            registry=registry,
            long_run=long_run,
            record_tables=tuple(record_tables),
            bi_source=source_text,
            energy_source=energy_text,
            tutorial_state=projection,
            metrics=dashboard,
            order_geometry=geometry,
            reports=reports,
            report_sources=report_sources,
        )

    def facts_of_kind(self, kind: str) -> list[dict[str, Any]]:
        return [fact for fact in self.facts if fact.get("kind") == kind]

    def record_table(self, *required: str) -> MarkdownTable | None:
        """The first table in the long-run records carrying all these columns."""

        return _table_with(list(self.record_tables), *required)

    def report(self, name: str) -> dict[str, Any]:
        """One loaded run report, or an empty mapping when it was not read."""

        payload = self.reports.get(name)
        return payload if isinstance(payload, dict) else {}

    def report_source(self, name: str) -> str:
        return self.report_sources.get(name, "")

    def registry_for(self, tokens: tuple[str, ...]) -> list[dict[str, str]]:
        """Route registry rows by their own 猜测归属 column, never by guesswork."""

        matched = []
        for entry in self.registry:
            owner = entry.get("猜测归属", "")
            if any(token in owner for token in tokens):
                matched.append(entry)
        return matched


ROUTING_TOKENS: dict[str, tuple[str, ...]] = {
    "ui-flow-network": ("界面", "仓库格", "价格面", "安全闸门"),
    "onboarding-and-rules": ("onboarding", "引导", "教学", "教程"),
    "monetization-touchpoints": ("商业化", "IAP", "留存触点", "价格面", "商店"),
    "activity-entry-map": ("运营活动", "activity-calendar", "运营触达"),
    "progression-live": ("进度曲线", "主线任务", "里程碑"),
    "telemetry-bi-events": ("埋点",),
    "story-script": ("剧情", "角色", "装修"),
    "save-schema": ("存档",),
    "board-prompts-and-rules": ("棋盘", "核心循环"),
    "story-speakers": ("剧情", "角色", "叙事"),
    "order-special-types": ("订单",),
    "energy-items": ("体力", "经济"),
    "skins-and-item-views": ("皮肤", "仓库"),
}
