"""Roll the design documents up into a ten-category completeness inventory.

The other renderers each answer "what does this system look like".  This one
answers "how far did we get, and what is missing before the next notch".  It
reads only products that already exist: the rendered documents themselves, the
cross-reference ledgers, the open-observation registry and the efficiency
dashboard.  Nothing here is typed in by hand — every number is counted.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Iterator

from .evidence import REGISTRY_FILE, EvidenceBundle


INVENTORY_SLUG = "coverage-inventory"
INVENTORY_TITLE = "十类设计内容完整度盘点"

LAYERS = ("配置声明", "实机验证", "推断与未知")

# ``## 1 配置声明（反编译）`` / ``## 2 实机验证`` / ``## 3 推断与未知``
_LAYER_HEADING = re.compile(r"^##\s+\d+\s+(配置声明|实机验证|推断与未知)")
_HEADING = re.compile(r"^(#{2,6})\s")
_SEPARATOR_CELL = re.compile(r":?-+:?")
_REGISTRY_REFERENCE = re.compile(r"UO-(\d+)")
_FRONTMATTER_UPDATED = re.compile(r"^更新时间:\s*(.+)$", re.MULTILINE)
_FRONTMATTER_LEVEL = re.compile(r"^证据等级:\s*(.+)$", re.MULTILINE)

# A document that never declares the three layers still belongs to one of them.
# The evidence level decides which: configuration- and code-derived pages are
# declaration, everything else is observation.
_DECLARED_LEVELS = frozenset({"decompiled_config", "decompiled_code"})

# Which fact kinds ``crossref/rules.py`` consumes for which configuration table.
# The dispatch there is an if/elif chain over ``fact.kind``; this is the same
# mapping in data form so the inventory can say "this table has a rule but the
# fact stream carries no sample of the kind it needs".  A test keeps the key set
# in step with the source.
RULE_FACT_KINDS: dict[str, tuple[str, ...]] = {
    "deliver": ("OrderItemConfig",),
    "merge": ("ItemModelConfig",),
    "generate": ("ItemModelConfig",),
    "open_chest": ("ItemModelConfig",),
    "main_task": ("MainTaskConfig", "LevelConfig"),
    "sell": ("ItemModelConfig",),
    "collect": ("ItemModelConfig",),
    "generator_runtime": ("ItemModelConfig",),
    # F6-05: refill timing measured against the two recovery durations.
    "generator_cooldown": ("ItemModelConfig",),
    "energy_recovery": ("EnergyModel",),
    "energy_refill_free": ("ShopModel",),
    "generator_output_sample": ("ItemModelConfig",),
    "board_snapshot": ("BoardModelConfig",),
    "paperbox_clear": ("MainPaperboxExtraRewardConfig", "BoardModelConfig"),
    "order_appearance": ("OrderItemConfig",),
    # F6-04.  A claim is checked against the guard's own red line first; only a
    # surface whose grant a table declares reaches LevelConfig.
    "free_claim_delta": ("GuardRedline", "LevelConfig"),
    # F7-04.  The energy-party round is the only one of these five that reaches
    # a decompiled table; the other four are checked against the model, the
    # guard or the planner, which are code, not configuration.
    "trigger_plane_round": ("ItemGenerateScore", "TriggerPlaneModel"),
    "trigger_plane_reward": ("TriggerPlaneModel",),
    "story_advance": ("GuardStoryAdvance",),
    "order_reserve_block": ("PlannerOrderReserve",),
    "generate_deviation": ("PlannerVerdictRelaxation",),
}

RULES_SOURCE = "src/omnicompany/packages/domains/game_observatory/crossref/rules.py"
LEDGER_SOURCE = "crossref/config_crossref_ledger.v1.json"
ROW_COVERAGE_SOURCE = "crossref/row_coverage.v1.json"
FACTS_SOURCE = "crossref/facts.v1.ndjson"
DASHBOARD_SOURCE = "metrics/dashboard.json"

# Keys that pair a progression gate with the flag saying whether it is reached.
_GATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("unlock_task", "unlocked"),
    ("trigger_task", "trigger_reached"),
)
# Keys that name the thing sitting behind a gate, most specific first.
_GATE_LABEL_KEYS = ("book", "dialogue_id", "name", "id", "table", "slug")
# Keys carrying the account's current main-task position.
_PROGRESS_KEYS = ("live_progress", "live_story_progress")


# --------------------------------------------------------------------------- #
# markdown accounting
# --------------------------------------------------------------------------- #
def _table_row_count(lines: Iterable[str]) -> int:
    """Count body rows of every pipe table in one stretch of markdown."""

    total = 0
    headers: list[str] | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            headers = None
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if headers is None:
            headers = cells
            continue
        if all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells if cell):
            continue
        total += 1
    return total


def _sections(markdown: str) -> Iterator[tuple[str | None, list[str]]]:
    """Split a document into ``(layer, lines)`` blocks at every ``##`` heading."""

    layer: str | None = None
    buffer: list[str] = []
    for line in markdown.splitlines():
        match = _HEADING.match(line)
        if match and len(match.group(1)) == 2:
            yield layer, buffer
            buffer = []
            layer_match = _LAYER_HEADING.match(line)
            layer = layer_match.group(1) if layer_match else None
        buffer.append(line)
    yield layer, buffer


def count_layers(markdown: str, evidence_level: str) -> dict[str, int]:
    """Count table rows per layer, plus the rows no layer heading claims."""

    counts = {layer: 0 for layer in LAYERS}
    unassigned = 0
    declares_layers = bool(_LAYER_HEADING.search(markdown))
    fallback = (
        LAYERS[0] if evidence_level in _DECLARED_LEVELS else LAYERS[1]
    )
    for layer, lines in _sections(markdown):
        rows = _table_row_count(lines)
        if not rows:
            continue
        if layer is not None:
            counts[layer] += rows
        elif declares_layers:
            unassigned += rows
        else:
            counts[fallback] += rows
    counts["未归层"] = unassigned
    counts["合计"] = sum(counts[layer] for layer in LAYERS) + unassigned
    return counts


def _frontmatter_value(markdown: str, pattern: re.Pattern[str], default: str) -> str:
    match = pattern.search(markdown)
    return match.group(1).strip() if match else default


# --------------------------------------------------------------------------- #
# ledger accounting
# --------------------------------------------------------------------------- #
def _ledger_for_tables(
    evidence: EvidenceBundle, tables: Iterable[str]
) -> dict[str, int]:
    ledger_tables = (evidence.ledger or {}).get("tables") or {}
    totals = {"observed": 0, "contradicted": 0, "unobserved": 0, "fields": 0}
    for name in tables:
        fields = (ledger_tables.get(name) or {}).get("fields") or {}
        totals["fields"] += len(fields)
        for field in fields.values():
            status = str(field.get("status", "unobserved"))
            if status in totals:
                totals[status] += 1
            else:
                totals["unobserved"] += 1
    return totals


# --------------------------------------------------------------------------- #
# improvement paths
# --------------------------------------------------------------------------- #
def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _task_order(value: Any) -> tuple[int, int]:
    if isinstance(value, str):
        parts = value.split("_", 1)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])
    return (10**6, 10**6)


def _current_tasks(payload_facts: dict[str, Any]) -> list[str]:
    found = []
    for key in _PROGRESS_KEYS:
        block = payload_facts.get(key)
        if isinstance(block, dict) and isinstance(block.get("current_task"), str):
            found.append(block["current_task"])
    return found


def _unreached_gates(payload_facts: dict[str, Any]) -> dict[str, int]:
    """Collect main-task gates the account has not reached, with their weight."""

    gates: dict[str, int] = {}
    for node in _walk(payload_facts):
        for task_key, flag_key in _GATE_FIELDS:
            task = node.get(task_key)
            if not isinstance(task, str) or not task:
                continue
            if node.get(flag_key) is not False:
                continue
            gates[task] = gates.get(task, 0) + 1
    return gates


def _gate_paths(
    slug: str, facts: dict[str, Any], current: str | None
) -> list[dict[str, str]]:
    paths = []
    for task, count in sorted(_unreached_gates(facts).items(), key=lambda kv: _task_order(kv[0])):
        need = f"主线推进到 `{task}`（本文档有 {count} 条内容挂在这道门后）"
        if current:
            need += f"；账号当前 `{current}`"
        paths.append({"kind": "主线进度门", "document": slug, "need": need})
    return paths


def _table_paths(
    slug: str, tables: Iterable[str], evidence: EvidenceBundle
) -> list[dict[str, str]]:
    ledger_tables = (evidence.ledger or {}).get("tables") or {}
    row_tables = (evidence.row_coverage or {}).get("tables") or {}
    fact_kinds = {str(fact.get("kind")) for fact in evidence.facts}
    paths: list[dict[str, str]] = []
    for name in sorted(set(tables)):
        fields = (ledger_tables.get(name) or {}).get("fields") or {}
        observed = sum(1 for field in fields.values() if field.get("status") == "observed")
        if fields and observed < len(fields):
            paths.append(
                {
                    "kind": "字段级台账缺口",
                    "document": slug,
                    "need": f"`{name}` 字段级还差 {len(fields) - observed}/{len(fields)} 个字段的实机证据",
                    "source": LEDGER_SOURCE,
                }
            )
        coverage = row_tables.get(name)
        if isinstance(coverage, dict):
            verified = int(coverage.get("verified_rows") or 0)
            total = int(coverage.get("total_rows") or 0)
            if total and verified < total:
                paths.append(
                    {
                        "kind": "行级覆盖缺口",
                        "document": slug,
                        "need": f"`{name}` 行级还差 {total - verified}/{total} 行",
                        "source": ROW_COVERAGE_SOURCE,
                    }
                )
        missing_kinds = sorted(
            kind
            for kind, kind_tables in RULE_FACT_KINDS.items()
            if name in kind_tables and kind not in fact_kinds
        )
        for kind in missing_kinds:
            paths.append(
                {
                    "kind": "缺动作类型样本",
                    "document": slug,
                    "need": f"`{name}` 有对账规则消费 `{kind}` 事实，事实流里该类 0 条",
                    "source": f"{RULES_SOURCE} + {FACTS_SOURCE}",
                }
            )
    return paths


def _surface_paths(slug: str, facts: dict[str, Any]) -> list[dict[str, str]]:
    summary = facts.get("summary")
    if not isinstance(summary, dict):
        return []
    transitions = summary.get("transitions")
    drawable = summary.get("drawable_transitions")
    if not isinstance(transitions, int) or not isinstance(drawable, int):
        return []
    if transitions <= drawable:
        return []
    return [
        {
            "kind": "需要的界面登记",
            "document": slug,
            "need": (
                f"还有 {transitions - drawable}/{transitions} 条转移的端点没落在已登记界面，"
                "补登记后才能进流转图"
            ),
            "source": f"design/{slug}.json",
        }
    ]


def _source_reference(slug: str, tables: Iterable[str]) -> str:
    names = ", ".join(sorted(set(tables)))
    return f"design/{slug}.json" + (f"（配表 {names}）" if names else "")


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def build_coverage_inventory(
    categories: tuple[tuple[str, tuple[str, ...]], ...],
    payloads: dict[str, dict[str, Any]],
    rendered: dict[str, str],
    foreign: dict[str, str],
    evidence: EvidenceBundle,
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Count the three layers per document, per category, and what is missing."""

    documents: dict[str, dict[str, Any]] = {}
    for slug, markdown in sorted(rendered.items()):
        payload = payloads.get(slug, {})
        tables = [str(name) for name in payload.get("source_tables") or []]
        level = str(payload.get("evidence_level") or _frontmatter_value(markdown, _FRONTMATTER_LEVEL, "未标注"))
        documents[slug] = {
            "slug": slug,
            "title": str(payload.get("title") or slug),
            "evidence_level": level,
            "generated": True,
            "layers": count_layers(markdown, level),
            "ledger": _ledger_for_tables(evidence, tables),
            "live_verified_rows": int(payload.get("live_verified_rows") or 0),
            "source_tables": tables,
            "updated_at": _frontmatter_value(markdown, _FRONTMATTER_UPDATED, "未标注"),
        }
    for slug, markdown in sorted(foreign.items()):
        level = _frontmatter_value(markdown, _FRONTMATTER_LEVEL, "未标注")
        documents[slug] = {
            "slug": slug,
            "title": _foreign_title(markdown, slug),
            "evidence_level": level,
            "generated": False,
            "layers": count_layers(markdown, level),
            "ledger": _ledger_for_tables(evidence, ()),
            "live_verified_rows": 0,
            "source_tables": [],
            "updated_at": _frontmatter_value(markdown, _FRONTMATTER_UPDATED, "未标注"),
        }

    current_tasks: list[str] = []
    for payload in payloads.values():
        current_tasks.extend(_current_tasks(payload.get("facts") or {}))
    current = sorted(set(current_tasks), key=_task_order)[-1] if current_tasks else None

    category_rows: list[dict[str, Any]] = []
    improvement: list[dict[str, Any]] = []
    for index, (name, slugs) in enumerate(categories, start=1):
        present = [slug for slug in slugs if slug in documents]
        missing = [slug for slug in slugs if slug not in documents]
        layers = {layer: 0 for layer in LAYERS}
        unassigned = 0
        for slug in present:
            counts = documents[slug]["layers"]
            for layer in LAYERS:
                layers[layer] += counts[layer]
            unassigned += counts["未归层"]
        total = sum(layers.values()) + unassigned
        category_rows.append(
            {
                "index": index,
                "category": name,
                "documents": list(slugs),
                "missing_documents": missing,
                "layers": layers,
                "unassigned_rows": unassigned,
                "total_rows": total,
                "ratios": {
                    layer: (layers[layer] / total if total else None) for layer in LAYERS
                },
                "evidence_levels": sorted(
                    {documents[slug]["evidence_level"] for slug in present}
                ),
                "live_verified_rows": sum(
                    documents[slug]["live_verified_rows"] for slug in present
                ),
                "updated_at": max(
                    (documents[slug]["updated_at"] for slug in present), default="未标注"
                ),
            }
        )
        seen: set[tuple[str, str]] = set()
        category_paths: list[dict[str, Any]] = []
        for slug in present:
            facts = (payloads.get(slug) or {}).get("facts") or {}
            tables = documents[slug]["source_tables"]
            entries = (
                _gate_paths(slug, facts, current)
                + _table_paths(slug, tables, evidence)
                + _surface_paths(slug, facts)
            )
            for entry in entries:
                key = (entry["kind"], entry["need"])
                if key in seen:
                    # Two documents in one category can quote the same table;
                    # the gap is one gap, not two.
                    continue
                seen.add(key)
                entry.setdefault("source", _source_reference(slug, tables))
                category_paths.append({"category": name, "index": index, **entry})
        if not category_paths:
            slugs_without_tables = ", ".join(
                slug for slug in present if not documents[slug]["source_tables"]
            )
            category_paths.append(
                {
                    "category": name,
                    "index": index,
                    "kind": "无配表可对账",
                    "document": present[0] if present else "",
                    "need": (
                        f"本类文档的 `配表` 为空（{slugs_without_tables or '—'}），"
                        "台账与行覆盖给不出缺口；下一档只能靠新增实机登记"
                    ),
                    "source": LEDGER_SOURCE,
                }
            )
        improvement.extend(category_paths)

    return {
        "schema": "game-observatory.design-coverage-inventory.v1",
        "slug": INVENTORY_SLUG,
        "title": INVENTORY_TITLE,
        "generated_at": generated_at,
        "current_main_task": current,
        "categories": category_rows,
        "documents": [documents[slug] for slug in sorted(documents)],
        "improvement_paths": improvement,
        "registry": _registry_digest(evidence, rendered, foreign),
        "throughput": _throughput(evidence, documents),
    }


def _foreign_title(markdown: str, slug: str) -> str:
    for line in markdown.splitlines()[:60]:
        if line.startswith("# "):
            return line[2:].strip()
    return slug


def _registry_digest(
    evidence: EvidenceBundle, rendered: dict[str, str], foreign: dict[str, str]
) -> dict[str, Any]:
    """How much of the open-observation registry the documents have absorbed."""

    total = len(evidence.registry)
    referenced: set[int] = set()
    for markdown in list(rendered.values()) + list(foreign.values()):
        for match in _REGISTRY_REFERENCE.finditer(markdown):
            referenced.add(int(match.group(1)))
    valid = {number for number in referenced if 1 <= number <= total}
    unconsumed = [
        {
            "编号": entry["编号"],
            "观测描述": str(entry.get("观测描述", "")),
            "猜测归属": str(entry.get("猜测归属", "")),
        }
        for index, entry in enumerate(evidence.registry, start=1)
        if index not in valid
    ]
    return {
        "total": total,
        # F6-05: the registry numbers as they stood when this product was
        # built.  The gameplay line appends to
        # ``records/unregistered-observations.md`` continuously, so a check
        # that compares this product against the live file goes red on every
        # new observation without anything being wrong.  Carrying the snapshot
        # lets the product be checked against itself; the live file is only
        # asked to be no shorter than the snapshot.
        "numbers": [str(entry.get("编号", "")) for entry in evidence.registry],
        "source": f"{REGISTRY_FILE} as read at generated_at",
        "referenced": len(valid),
        "dangling_references": sorted(referenced - valid),
        "rate": (len(valid) / total) if total else None,
        "unconsumed": unconsumed,
    }


def _throughput(evidence: EvidenceBundle, documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Divide the two accumulated counts by the dashboard's wall clock."""

    overview = (evidence.metrics or {}).get("overview") or {}
    wall_seconds = overview.get("wall_seconds")
    hours = (float(wall_seconds) / 3600.0) if isinstance(wall_seconds, (int, float)) else None
    facts = len(evidence.facts)
    live_rows = sum(entry["live_verified_rows"] for entry in documents.values())
    return {
        "wall_seconds": wall_seconds,
        "wall_hours": hours,
        "dashboard_generated_at": (evidence.metrics or {}).get("generated_at"),
        "crossref_generated_at": (evidence.summary or {}).get("generated_at"),
        "facts": facts,
        "live_verified_rows": live_rows,
        "facts_per_hour": (facts / hours) if hours else None,
        "live_verified_rows_per_hour": (live_rows / hours) if hours else None,
        "sources": {
            "wall_seconds": f"{DASHBOARD_SOURCE} overview.wall_seconds",
            "facts": FACTS_SOURCE,
            "live_verified_rows": "design/<slug>.json live_verified_rows",
        },
    }


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _category_note(data: dict[str, Any]) -> str:
    empty = [
        entry["slug"] for entry in data["documents"] if entry["layers"]["合计"] == 0
    ]
    note = (
        "比例的分母是该类三层条目数之和；分母为 0 时三格都是 `—`。同一份文档被两类挂靠时，"
        "它的条目在两类里各算一次，所以各类之和大于文档总条目数。"
    )
    if empty:
        names = ", ".join(f"`{slug}`" for slug in empty)
        note += f"以下文档全篇没有表格，三层都记 0，它们的证据在正文里：{names}。"
    return note


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render_coverage_inventory(data: dict[str, Any]) -> str:
    lines = [
        f"# {data['title']}",
        "",
        "> 本文件由生成器写出，不要手改。三层的分层依据是文档自己的 `## 1 配置声明` /",
        "> `## 2 实机验证` / `## 3 推断与未知` 小节：每个小节里的表格正文行数就是该层条目数。",
        "> 没有分层小节的文档按它的证据等级整体归入一层。对账写回追加的三列不增加行数。",
        "",
        "## 1 按十类设计内容",
        "",
        "| 序号 | 设计内容 | 挂靠文档 | 配置声明 | 实机验证 | 推断与未知 | 三层比例（声明/实机/未知） | 证据等级 | 最近更新 |",
        "|---:|---|---|---:|---:|---:|---|---|---|",
    ]
    for row in data["categories"]:
        documents = ", ".join(f"`{slug}`" for slug in row["documents"])
        if row["missing_documents"]:
            missing = ", ".join(f"`{slug}`" for slug in row["missing_documents"])
            documents += f"（缺 {missing}）"
        ratios = " / ".join(_percent(row["ratios"][layer]) for layer in LAYERS)
        levels = ", ".join(row["evidence_levels"]) or "—"
        lines.append(
            f"| {row['index']} | {row['category']} | {documents} | "
            f"{row['layers'][LAYERS[0]]} | {row['layers'][LAYERS[1]]} | {row['layers'][LAYERS[2]]} | "
            f"{ratios} | {levels} | {row['updated_at']} |"
        )
    lines.extend(
        [
            "",
            _category_note(data),
            "",
            "## 2 按文档",
            "",
            "| 文档 | 证据等级 | 配置声明 | 实机验证 | 推断与未知 | 未归层 | 台账 observed/contradicted/unobserved | 实机验证行数 | 更新时间 |",
            "|---|---|---:|---:|---:|---:|---|---:|---|",
        ]
    )
    for entry in data["documents"]:
        ledger = entry["ledger"]
        mark = "" if entry["generated"] else "（手写）"
        lines.append(
            f"| `{entry['slug']}`{mark} | {entry['evidence_level']} | "
            f"{entry['layers'][LAYERS[0]]} | {entry['layers'][LAYERS[1]]} | "
            f"{entry['layers'][LAYERS[2]]} | {entry['layers']['未归层']} | "
            f"{ledger['observed']}/{ledger['contradicted']}/{ledger['unobserved']}"
            f"（共 {ledger['fields']} 字段） | {entry['live_verified_rows']} | {entry['updated_at']} |"
        )
    lines.extend(
        [
            "",
            "`未归层` 是既不在三个分层小节里、也不属于整体归层文档的表格行；它恒为 0，"
            "不为 0 说明有一层的表格漏在小节之外。台账三元组按该文档 `配表` 里的表求和，"
            "`配表` 为空的文档恒为 `0/0/0（共 0 字段）`。",
            "",
            "## 3 提升路径",
            "",
            "每条都只来自程序可判的条件：配表的台账/行覆盖缺口、对账规则需要而事实流里没有的"
            "动作类型、配置里写死的主线进度门、以及端点尚未登记的界面转移。",
            "",
            "| 序号 | 设计内容 | 类型 | 下一档验证需要什么 | 来源 |",
            "|---:|---|---|---|---|",
        ]
    )
    for entry in data["improvement_paths"]:
        need = str(entry["need"]).replace("|", "\\|")
        lines.append(
            f"| {entry['index']} | {entry['category']} | {entry['kind']} | {need} | `{entry['source']}` |"
        )
    if not data["improvement_paths"]:
        lines.append("| — | — | — | 没有程序可判的缺口 | — |")
    registry = data["registry"]
    lines.extend(
        [
            "",
            "## 4 开放式登记消化率",
            "",
            f"- 登记表总条目：{registry['total']}",
            f"- 已被设计文档引用（按 UO 编号匹配）：{registry['referenced']}",
            f"- 未消化：{len(registry['unconsumed'])}",
            f"- 消化率：{_percent(registry['rate'])}",
        ]
    )
    if registry["dangling_references"]:
        dangling = ", ".join(f"UO-{number:02d}" for number in registry["dangling_references"])
        lines.append(f"- 引用了登记表里不存在的编号：{dangling}")
    lines.extend(
        [
            "",
            "| 编号 | 观测描述 | 猜测归属 |",
            "|---|---|---|",
        ]
    )
    for entry in registry["unconsumed"]:
        description = entry["观测描述"].replace("|", "\\|")
        owner = entry["猜测归属"].replace("|", "\\|")
        lines.append(f"| {entry['编号']} | {description} | {owner} |")
    if not registry["unconsumed"]:
        lines.append("| — | 登记表已全部被文档引用 | — |")
    throughput = data["throughput"]
    lines.extend(
        [
            "",
            "## 5 与效率仪表的交叉",
            "",
            "只取数，不评价。分母是仪表口径的总墙钟（只累计有动作的 run）。两个计数来自不同"
            "时刻刷新的产物，玩法线还在跑时它们不是同一秒的快照：仪表刷新于 "
            f"{throughput['dashboard_generated_at'] or '未标注'}，对账产物刷新于 "
            f"{throughput['crossref_generated_at'] or '未标注'}。",
            "",
            "| 口径 | 数值 | 来源 |",
            "|---|---:|---|",
            f"| 总墙钟（秒） | {_number(throughput['wall_seconds'])} | `{throughput['sources']['wall_seconds']}` |",
            f"| 事实条数 | {_number(throughput['facts'])} | `{throughput['sources']['facts']}` |",
            f"| 实机验证行数合计 | {_number(throughput['live_verified_rows'])} | `{throughput['sources']['live_verified_rows']}` |",
            f"| 每小时新增事实数 | {_number(throughput['facts_per_hour'])} | 事实条数 ÷（总墙钟/3600） |",
            f"| 每小时新增实机验证行数 | {_number(throughput['live_verified_rows_per_hour'])} | 实机验证行数合计 ÷（总墙钟/3600） |",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def read_foreign_markdown(
    out_docs: str | Path, known: Iterable[str], index_filename: str
) -> dict[str, str]:
    """Read the documents in the directory that this generator does not write."""

    claimed = set(known) | {INVENTORY_SLUG}
    found: dict[str, str] = {}
    for path in sorted(Path(out_docs).glob("*.md")):
        if path.stem in claimed or path.name == index_filename:
            continue
        found[path.stem] = path.read_text(encoding="utf-8")
    return found
