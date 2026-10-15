"""Render extracted configuration facts as paired Markdown and JSON documents."""

from __future__ import annotations

import json
import os
from datetime import datetime
from omnicompany.core.pycompat import UTC
from pathlib import Path
from typing import Any, Callable

from .config_facts import ConfigFacts, extract_all
from .coverage_inventory import (
    INVENTORY_SLUG,
    build_coverage_inventory,
    read_foreign_markdown,
    render_coverage_inventory,
)
from .evidence import EvidenceBundle
from .live_facts import extract_live_all
from .permissive_facts import PERMISSIVE_EXTRACTORS
from .render_live import LIVE_RENDERERS
from .render_permissive import PERMISSIVE_RENDERERS


AUTHORED_DIRNAME = "_authored"
INDEX_FILENAME = "README.md"

# The frontmatter line that marks a document as generator output. The
# cross-reference write-back annotates only documents carrying it, so a
# hand-written page in the same directory keeps whatever its author wrote.
GENERATOR_MARKER_KEY = "生成器"
GENERATOR_MARKER_VALUE = "design_extraction"
GENERATOR_MARKER = f"{GENERATOR_MARKER_KEY}: {GENERATOR_MARKER_VALUE}"

DOC_METADATA = {
    "economy-item-chains": {
        "title": "经济系统：物品合成链与生成器参数",
        "system": "经济系统",
    },
    "economy-order-pricing": {
        "title": "经济系统：订单定价与出售价格对照",
        "system": "订单系统",
    },
    "progression-main-tasks": {
        "title": "进度系统：主线任务序列",
        "system": "主线任务",
    },
    "progression-player-levels": {
        "title": "进度系统：玩家等级曲线",
        "system": "玩家等级",
    },
    "monetization-shop-iap-cn": {
        "title": "商业化系统：商店与国内外 IAP 配置",
        "system": "商业化商店",
    },
    "board-initial-layout": {
        "title": "棋盘系统：初始 7×9 布局",
        "system": "棋盘布局",
    },
    "ui-flow-network": {
        "title": "界面系统：界面流转网络与红线界面",
        "system": "界面流转",
        "facet": "实机验证",
    },
    "onboarding-and-rules": {
        "title": "引导系统：教程链与规则文案",
        "system": "引导与规则",
        "facet": "实机验证",
    },
    "monetization-touchpoints": {
        "title": "商业化系统：实机触点",
        "system": "商业化触点",
        "facet": "实机验证",
    },
    "activity-entry-map": {
        "title": "运营系统：活动入口与登记",
        "system": "运营活动",
        "facet": "实机验证",
    },
    "progression-live": {
        "title": "进度系统：本账号实机曲线",
        "system": "进度曲线",
        "facet": "实机验证",
    },
    "telemetry-bi-events": {
        "title": "埋点系统：事件与字段声明",
        "system": "埋点",
        "facet": "代码派生",
    },
    "story-script": {
        "title": "叙事系统：剧情界面与角色",
        "system": "叙事",
        "facet": "实机验证",
    },
    "save-schema": {
        "title": "存档系统：声明结构与实机对账",
        "system": "存档结构",
        "facet": "实机验证",
    },
    "board-prompts-and-rules": {
        "title": "引导系统：棋盘闲置提示的优先级与退出条件",
        "system": "棋盘提示",
        "facet": "实机验证",
    },
    "story-speakers": {
        "title": "叙事系统：剧情说话人表",
        "system": "叙事角色",
        "facet": "实机验证",
    },
    "order-special-types": {
        "title": "订单系统：剧情特殊订单",
        "system": "特殊订单",
        "facet": "实机验证",
    },
    "energy-items": {
        "title": "经济系统：体力经济（上限、恢复、补给链、加速白名单、换金币与等待粒度）",
        "system": "体力经济",
        "facet": "实机验证",
    },
    "skins-and-item-views": {
        "title": "内容树：装饰皮肤与物品外观规模摘要",
        "system": "外观资源",
        "facet": "配置派生",
    },
}

# The verified layer is written by two extractor families; they share the
# payload shape, the three headings and the write-back's frontmatter marker.
VERIFIED_RENDERERS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    **LIVE_RENDERERS,
    **PERMISSIVE_RENDERERS,
}

CONFIG_SLUGS = (
    "economy-item-chains",
    "economy-order-pricing",
    "progression-main-tasks",
    "progression-player-levels",
    "monetization-shop-iap-cn",
    "board-initial-layout",
)

# plan.md 第四节的十类设计内容 ↔ 本目录文档
DESIGN_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("核心循环与经济模型", ("economy-item-chains", "economy-order-pricing", "energy-items")),
    ("内容树", ("economy-item-chains", "board-initial-layout", "skins-and-item-views")),
    (
        "进度曲线",
        (
            "progression-main-tasks",
            "progression-player-levels",
            "progression-live",
            "save-schema",
        ),
    ),
    ("订单系统", ("economy-order-pricing", "order-special-types")),
    ("运营活动", ("activity-calendar", "activity-entry-map")),
    ("商业化", ("monetization-shop-iap-cn", "monetization-touchpoints")),
    ("界面与交互", ("ui-flow-network",)),
    ("引导与规则", ("onboarding-and-rules", "board-prompts-and-rules")),
    ("叙事", ("story-script", "story-speakers")),
    ("埋点表", ("telemetry-bi-events",)),
)


def write_atomic(path: Path, text: str) -> None:
    """Write through a sibling temporary file and rename it into place.

    Opening one of these products directly for truncation fails on this host
    whenever another session still holds the copy it has just read, and the
    design pages and the ledger are read by the write-back, the inventory and
    the dashboard.  The rename also means a reader never sees half a page.
    The metrics package writes its own products the same way; this one cannot
    import that helper without importing the driver behind it.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def local_today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _frontmatter(
    slug: str,
    sources: list[str],
    updated_at: str,
    *,
    evidence_level: str = "decompiled_config",
    truths: list[str] | None = None,
    verified_rows: int | None = None,
) -> list[str]:
    metadata = DOC_METADATA[slug]
    facet = str(metadata.get("facet", "配置派生"))
    resolved_truths = (
        truths
        if truths is not None
        else [f"synthesis/lua_code/Lua-Project/{table}.lua" for table in sources]
    )
    lines = [
        "---",
        GENERATOR_MARKER,
        "类型: 反向设计",
        "职能: 玩法业务",
        f"业务: [{metadata['system']}]",
        f"分面: {facet}",
        f'稳定ID: "[[design:romantic-restaurant/{slug}]]"',
        f"配表: {_json(sources)}",
        f"证据等级: {evidence_level}",
    ]
    if verified_rows is not None:
        lines.append(f"实机验证行数: {verified_rows}")
    lines.extend(
        [
            f"真源: {_json(resolved_truths)}",
            f"更新时间: {updated_at}",
            f'tags: [game-observatory, romantic-restaurant, "{facet}", "反向设计"]',
            "---",
        ]
    )
    return lines


COLUMN_FOOTER = [
    "## 页脚：对账写回追加的三列",
    "",
    "本文件的表格由生成器写出，对账写回会在每张表右侧追加三列，语义如下：",
    "",
    "- `实机证据`：该行覆盖的配置行在事实流里的事实 id，只列可读的前几条，"
    "完整清单在行级覆盖台账 `crossref/row_coverage.v1.json`。",
    "- `实机验证行数`：该行覆盖的配置行中，已被实机事实确认且未被推翻的条数。"
    "一个 md 行可能代表一整组配置行（一整行棋盘、一天任务），所以它不是 0/1 标志。",
    "- `矛盾数`：同一组配置行中，实机结果与配置声明互相矛盾的条数。",
    "",
    "三列由对账写回追加，不由本生成器写入；没跑过写回时它们为空。"
    "`配表` 未列出的表不会被标注。",
]

LIVE_FOOTER = [
    "## 页脚：两个同名口径",
    "",
    "- frontmatter 里的 `实机验证行数` 是本文件中带实机证据（事实 id 或帧路径）的条目总数，"
    "由生成器写出。",
    "- 表格右侧若出现 `实机证据` / `实机验证行数` / `矛盾数` 三列，是对账写回按 `配表` 追加的"
    "行级标注，作用域是单行；`配表` 为空的文档里这三列没有值。",
    "- 手写内容只允许放在 `_authored/<本文件名>`，由生成器原样嵌入标题之后。",
]


def _render_item_chains(data: dict[str, Any]) -> list[str]:
    summary = data["summary"]
    lines = [
        "## 汇总",
        "",
        f"- 链数量：{summary['chain_count']}",
        f"- 最长链：{summary['longest_chain']} 级",
        f"- 含生成器的链：{_json(summary['generator_chains'])}",
        "",
        "## 物品链",
        "",
    ]
    for chain in data["chains"]:
        lines.extend(
            [
                f"### {chain['chain_id']}",
                "",
                f"Type→MergedType 序列：`{' → '.join(chain['sequence'])}`；终止原因："
                f"`{chain['terminated_by']}`。",
                "",
                "| Type | MergedType | SellingPrice | Rare | BubbleChance | 生成器 | Spread_* |",
                "|---|---|---:|---:|---:|---|---|",
            ]
        )
        for level in chain["levels"]:
            lines.append(
                "| {type} | {merged} | {selling} | {rare} | {bubble} | {generator} | {params} |".format(
                    type=level["type"],
                    merged=level["merged_type"] or "",
                    selling=level["selling_price"],
                    rare=level["rare"],
                    bubble=level["bubble_chance"],
                    generator="是" if level["is_generator"] else "否",
                    params=_json(level["generator_parameters"]) if level["is_generator"] else "",
                )
            )
        lines.append("")
    return lines


def _render_order_pricing(data: dict[str, Any]) -> list[str]:
    lines = [
        "> 本页只有配置层。HUD 上那一行常规订单的实机规则——三个托盘位、可交付恒在最左、"
        "按显示位寻址、五单时订单行左移——在 `order-special-types` 的实机验证层。",
        "",
        "## 按链价格曲线",
        "",
    ]
    for curve in data["price_curves"]:
        compact = [
            {
                "Code": point["code"],
                "Price": point["price"],
                "SellingPrice": point["selling_price"],
                "Price/SellingPrice": point["price_to_selling_price"],
            }
            for point in curve["points"]
        ]
        lines.append(f"- `{curve['segment']}`：`{_json(compact)}`")
    lines.extend(["", "## 订单条目", ""])
    for table_name, rows in data["variants"].items():
        lines.extend(
            [
                f"### {table_name}",
                "",
                "| Code | Price | DiffScore | Weight | UnlockLevel | ForbiddenSlot | "
                "RepeatWeightDecrease | ProgressUnlock | SellingPrice | 比值 |",
                "|---|---:|---:|---:|---:|---|---:|---|---:|---:|",
            ]
        )
        for row in rows:
            lines.append(
                "| {Code} | {Price} | {DiffScore} | {Weight} | {UnlockLevel} | "
                "{ForbiddenSlot} | {RepeatWeightDecrease} | {ProgressUnlock} | "
                "{SellingPrice} | {PriceToSellingPrice} |".format(
                    **{
                        key: _json(value) if isinstance(value, (dict, list)) else value
                        for key, value in row.items()
                    }
                )
            )
        lines.append("")
    return lines


def _render_main_tasks(data: dict[str, Any]) -> list[str]:
    lines = [
        "## 每日汇总",
        "",
        "| day | cost 总和 | exp 总和 |",
        "|---:|---:|---:|",
    ]
    for row in data["day_summaries"]:
        lines.append(f"| {row['day']} | {row['cost_total']} | {row['exp_total']} |")
    lines.extend(["", "## day/area 任务序列", ""])
    for sequence in data["sequences"]:
        ids = [task["id"] for task in sequence["tasks"]]
        lines.append(f"- day `{sequence['day']}` / area `{sequence['area']}`：`{_json(ids)}`")
    lines.extend(
        [
            "",
            "## 前 30 个任务",
            "",
            "| day | area | id | cost | rewards | detail_icon | list_icon |",
            "|---:|---:|---:|---:|---|---|---|",
        ]
    )
    for task in data["first_30_tasks"]:
        lines.append(
            f"| {task['day']} | {task['area']} | {task['id']} | {task['cost']} | "
            f"{_json(task['rewards'])} | {task['detail_icon'] or ''} | {task['list_icon'] or ''} |"
        )
    if data["warnings"]:
        lines.extend(["", "## 解析警告", "", f"`{_json(data['warnings'])}`"])
    return lines


def _render_player_levels(data: dict[str, Any]) -> list[str]:
    lines = [
        "## 奖励物品 Type 出现频次",
        "",
        f"`{_json(data['reward_type_frequency'])}`",
        "",
        "## 等级曲线",
        "",
        "| level | cost | 累计 cost | rewards |",
        "|---:|---:|---:|---|",
    ]
    for level in data["levels"]:
        lines.append(
            f"| {level['level']} | {level['cost']} | {level['cumulative_cost']} | "
            f"{_json(level['rewards'])} |"
        )
    if data["warnings"]:
        lines.extend(["", "## 解析警告", "", f"`{_json(data['warnings'])}`"])
    return lines


def _render_shop(data: dict[str, Any]) -> list[str]:
    lines = [
        "## 国内与海外 IAP 差异",
        "",
        "| 商品 id | 国内内容物 | 海外内容物 | 内容不同 | 差异字段 | 国内价格字段 | 海外价格字段 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in data["iap_comparison"]:
        lines.append(
            f"| {row['id']} | {_json(row['cn_contents'])} | {_json(row['overseas_contents'])} | "
            f"{'是' if row['contents_differ'] else '否'} | {_json(row['different_fields'])} | "
            f"{_json(row['cn_price_fields'])} | "
            f"{_json(row['overseas_price_fields'])} |"
        )
    lines.extend(["", "## IAP 原始商品", ""])
    for label, products in (("国内", data["iap_cn"]), ("海外", data["iap_overseas"])):
        lines.append(f"### {label}")
        lines.append("")
        for product in products:
            lines.append(
                f"- `{product['id']}`：价格字段 `{_json(product['price_fields'])}`；"
                f"内容物 `{_json(product['contents'])}`；限购字段 `{_json(product['limit_fields'])}`；"
                f"原始行 `{_json(product['raw'])}`。"
            )
        lines.append("")
    lines.extend(["## 每日商品", ""])
    for table_name, products in data["daily_deals"].items():
        lines.append(f"### {table_name}")
        lines.append("")
        for product in products:
            lines.append(
                f"- 商品 `{product['id']}`：价格字段 `{_json(product['price_fields'])}`；"
                f"内容物 `{_json(product['contents'])}`；限购字段 `{_json(product['limit_fields'])}`。"
            )
        lines.append("")
    lines.extend(["## 仓库格扩容价", ""])
    for table_name, rows in data["inventory_expansion"].items():
        lines.append(f"- `{table_name}`：`{_json(rows)}`")
    lines.extend(["", "## 商店槽位权重", "", f"`{_json(data['shop_slot_weights'])}`"])
    return lines


def _render_board(data: dict[str, Any]) -> list[str]:
    dimensions = data["dimensions"]
    lines = [
        "## 尺寸与前缀",
        "",
        f"- 配置尺寸：{dimensions['configured_size']}（{dimensions['rows']} 行 × "
        f"{dimensions['columns']} 列）",
        "- `pb`：含义状态 `unknown`；纸箱/遮罩仅为候选解释。",
        "- `c`：含义状态 `unknown`。",
        "",
        "## 初始棋盘",
        "",
        "| 行\\列 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for row_number, row in enumerate(data["grid"], start=1):
        values = " | ".join(str(cell["raw"]) for cell in row)
        lines.append(f"| {row_number} | {values} |")
    lines.extend(
        [
            "",
            "## Type 分布",
            "",
            f"`{_json(data['type_distribution'])}`",
            "",
            "## 前缀分布",
            "",
            f"`{_json(data['prefix_distribution'])}`",
        ]
    )
    rewards = data.get("paperbox_rewards") or {}
    if rewards.get("available"):
        lines.extend(
            [
                "",
                "## 沙土格清除奖励（与棋盘并列的第二张网格）",
                "",
                f"`{rewards['table']}` 是与 `BoardModelConfig` 同尺寸、逐格平行的一张表"
                f"（{rewards['rows']} 行 × {rewards['columns']} 列）：某一格写了奖励，"
                "就表示这一格上的沙土层被清除时发这份奖励。它是清除的**收益**，"
                f"不是解锁的价格。全表有奖励的格子 {rewards['rewarding_cell_count']} 个，"
                f"其中初始棋盘上盖着沙土（`pb` 前缀）的 {rewards['cells_under_a_paperbox']} 个。",
                "",
                f"奖励分布：`{_json(rewards['reward_distribution'])}`",
                "",
                "| 行\\列 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |",
                "|---:|---|---|---|---|---|---|---|",
            ]
        )
        for row_number, row in enumerate(rewards["grid"], start=1):
            values = " | ".join(str(cell) for cell in row)
            lines.append(f"| {row_number} | {values} |")
        lines.extend(
            [
                "",
                "逐格对照（只列有奖励的格）：",
                "",
                "| 行 | 列 | 棋盘原始值 | 初始盖沙土 | 清除奖励 |",
                "|---:|---:|---|---|---|",
            ]
        )
        for cell in rewards["rewarding_cells"]:
            lines.append(
                f"| {cell['row']} | {cell['column']} | {cell['board_cell']} | "
                f"{'是' if cell['covered'] else '否'} | {cell['reward']} |"
            )
        lines.extend(
            [
                "",
                "哪一次合成剥掉哪一格的沙土，以及实机剥层与本表的逐格对账，在 "
                "`board-prompts-and-rules` 的实机验证层。",
            ]
        )
    return lines


RENDERERS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "economy-item-chains": _render_item_chains,
    "economy-order-pricing": _render_order_pricing,
    "progression-main-tasks": _render_main_tasks,
    "progression-player-levels": _render_player_levels,
    "monetization-shop-iap-cn": _render_shop,
    "board-initial-layout": _render_board,
}


def build_document_payloads(
    facts: ConfigFacts,
    *,
    generated_at: str | None = None,
    evidence: EvidenceBundle | None = None,
) -> dict[str, dict[str, Any]]:
    timestamp = generated_at or utc_now()
    payloads = {
        slug: {
            "schema": "game-observatory.config-derived-design.v1",
            "slug": slug,
            "title": DOC_METADATA[slug]["title"],
            "evidence_level": "decompiled_config",
            "generated_at": timestamp,
            "source_tables": data["source_tables"],
            "facts": data,
        }
        for slug, data in extract_all(facts).items()
    }
    if evidence is None:
        return payloads
    verified = dict(extract_live_all(facts, evidence))
    verified.update(
        {slug: extractor(facts, evidence) for slug, extractor in PERMISSIVE_EXTRACTORS.items()}
    )
    for slug, data in verified.items():
        payloads[slug] = {
            "schema": "game-observatory.verified-layer-design.v1",
            "slug": slug,
            "title": DOC_METADATA[slug]["title"],
            "evidence_level": data["evidence_level"],
            "generated_at": timestamp,
            "source_tables": data["source_tables"],
            "truth_sources": data["sources"],
            "live_verified_rows": data["live_verified_rows"],
            "facts": data,
        }
    return payloads


def read_authored(out_docs: str | Path, slug: str) -> str:
    """Return the hand-written annotation block for one document, if any."""

    path = Path(out_docs) / AUTHORED_DIRNAME / f"{slug}.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip("\n")


def render_markdown(payload: dict[str, Any], authored: str = "") -> str:
    slug = str(payload["slug"])
    sources = [str(source) for source in payload["source_tables"]]
    is_config_doc = slug in CONFIG_SLUGS
    if is_config_doc:
        lines = _frontmatter(slug, sources, local_today())
        preamble = "> 证据声明：以下内容仅来自反编译配置；配置里这么写，实机未验证。"
    else:
        lines = _frontmatter(
            slug,
            sources,
            local_today(),
            evidence_level=str(payload["evidence_level"]),
            truths=[str(item) for item in payload.get("truth_sources") or []],
            verified_rows=int(payload.get("live_verified_rows") or 0),
        )
        preamble = (
            "> 证据声明：本文件分三层——配置声明来自反编译配置，实机验证带事实 id 或帧路径，"
            "推断与未知带登记表编号。三层不互相顶替。"
        )
    lines.extend(["", f"# {payload['title']}", "", preamble, ""])
    if authored:
        lines.extend([f"<!-- authored:begin {slug} -->", authored, "<!-- authored:end -->", ""])
    renderer = RENDERERS[slug] if is_config_doc else VERIFIED_RENDERERS[slug]
    lines.extend(renderer(payload["facts"]))
    lines.extend(["", *(COLUMN_FOOTER if is_config_doc else LIVE_FOOTER)])
    return "\n".join(lines).rstrip() + "\n"


def _coverage_rows(evidence: EvidenceBundle) -> list[list[Any]]:
    ledger = evidence.ledger or {}
    ledger_tables = ledger.get("tables") or {}
    row_tables = (evidence.row_coverage or {}).get("tables") or {}
    rows: list[list[Any]] = []
    for name in sorted(row_tables):
        fields = (ledger_tables.get(name) or {}).get("fields") or {}
        observed = sum(1 for field in fields.values() if field.get("status") == "observed")
        coverage = row_tables[name]
        rows.append(
            [
                name,
                f"{observed}/{len(fields)}",
                f"{coverage.get('verified_rows')}/{coverage.get('total_rows')}",
                coverage.get("contradicted_rows"),
            ]
        )
    summary = ledger.get("summary") or {}
    if summary:
        rows.append(
            [
                "全部表（字段级）",
                f"{summary.get('observed')}/{summary.get('fields')}",
                "—",
                summary.get("contradicted"),
            ]
        )
    return rows


def foreign_documents(out_docs: str | Path, payloads: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Find documents in the directory that this generator does not write.

    Other work lines publish documents into the same directory.  The index has
    to list them, and the generator must never claim their slugs.
    """

    found: dict[str, dict[str, str]] = {}
    for path in sorted(Path(out_docs).glob("*.md")):
        slug = path.stem
        if slug in payloads or slug == INVENTORY_SLUG or path.name == INDEX_FILENAME:
            continue
        title, level = slug, "未标注"
        for line in path.read_text(encoding="utf-8").splitlines()[:40]:
            if line.startswith("证据等级:"):
                level = line.split(":", 1)[1].strip()
            elif line.startswith("# "):
                title = line[2:].strip()
                break
        found[slug] = {"title": title, "evidence_level": level}
    return found


def render_index(
    payloads: dict[str, dict[str, Any]],
    evidence: EvidenceBundle,
    foreign: dict[str, dict[str, str]] | None = None,
) -> str:
    """Write the map from the ten design-content categories to the documents."""

    routed = {
        entry["编号"]
        for payload in payloads.values()
        for entry in (payload["facts"].get("registry") or [])
    }
    lines = [
        "# 浪漫餐厅反向设计文档索引",
        "",
        "> 本文件由生成器写出，不要手改。每份文档的三层结构、证据等级和更新时间见下表。",
        "",
        f"每一类做到了几成、下一档验证还差什么，见盘点页 [`{INVENTORY_SLUG}`]"
        f"({INVENTORY_SLUG}.md)：十类三层比例、按文档计数、提升路径清单、"
        "开放式登记消化率与效率仪表交叉。",
        "",
        "## 1 十类设计内容 ↔ 文档",
        "",
        "| 序号 | 设计内容 | 文档 | 证据等级 |",
        "|---:|---|---|---|",
    ]
    known = dict(foreign or {})
    for index, (category, slugs) in enumerate(DESIGN_CATEGORIES, start=1):
        documents = ", ".join(f"`{slug}`" for slug in slugs)
        levels = ", ".join(
            sorted(
                {
                    str(payloads[slug]["evidence_level"])
                    if slug in payloads
                    else str(known.get(slug, {}).get("evidence_level", "未收录"))
                    for slug in slugs
                }
            )
        )
        lines.append(f"| {index} | {category} | {documents} | {levels} |")
    lines.extend(
        [
            "",
            "## 2 文档清单",
            "",
            "| 文档 | 标题 | 证据等级 | 实机验证行数 | 配表 |",
            "|---|---|---|---:|---|",
        ]
    )
    for slug in sorted(payloads):
        payload = payloads[slug]
        tables = ", ".join(str(table) for table in payload["source_tables"]) or "—"
        lines.append(
            f"| `{slug}` | {payload['title']} | {payload['evidence_level']} | "
            f"{payload.get('live_verified_rows', 0)} | {tables} |"
        )
    if known:
        lines.extend(
            [
                "",
                "### 2.1 本目录内非本生成器产出的文档",
                "",
                "这些文档由其它工作线写出，本生成器只读它们的 frontmatter，不覆盖、不改名。",
                "",
                "写回补上的 `实机证据` / `实机验证行数` / `矛盾数` 三列按各自 `配表` 计算："
                "不声明 `配表` 的文档在这里恒为空与 0，那是「没有配置表可对账」，"
                "不是「没有证据」——它们的证据在正文里。",
                "",
                "| 文档 | 标题 | 证据等级 |",
                "|---|---|---|",
            ]
        )
        for slug in sorted(known):
            entry = known[slug]
            lines.append(f"| `{slug}` | {entry['title']} | {entry['evidence_level']} |")
    lines.extend(
        [
            "",
            "## 3 对账总览",
            "",
            "字段级来自配置对账台账，行级来自行覆盖台账；棋盘表的字段列名由 (row, col) 合成。",
            "",
            f"对账写回只处理 frontmatter 带 `{GENERATOR_MARKER}` 的文档：给它们的每张表格补上 "
            "`实机证据` / `实机验证行数` / `矛盾数` 里还缺的列，并按该文档的 `配表` 重算 "
            "`实机已验证字段数`。本索引没有 frontmatter，`_authored/` 与手写文档没有该标记，"
            "写回一律不碰，本节 2 的 `实机验证行数` 与 `配表` 都是生成器自己的口径。",
            "",
            "| 表 | 字段 observed/总数 | 行 已验证/总行 | 矛盾行 |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in _coverage_rows(evidence):
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    lines.extend(
        [
            "",
            "## 4 未被任何文档吸收的登记项",
            "",
            "路由依据是登记表自己的「猜测归属」列；下列条目还没有对应的提炼类型。",
            "",
            "| 编号 | 观测描述 | 猜测归属 |",
            "|---|---|---|",
        ]
    )
    for entry in evidence.registry:
        if entry["编号"] in routed:
            continue
        description = str(entry.get("观测描述", "")).replace("|", "\\|")
        owner = str(entry.get("猜测归属", "")).replace("|", "\\|")
        lines.append(f"| {entry['编号']} | {description} | {owner} |")
    return "\n".join(lines).rstrip() + "\n"


def write_design_documents(
    facts: ConfigFacts,
    out_docs: str | Path,
    *,
    generated_at: str | None = None,
    evidence: EvidenceBundle | None = None,
) -> dict[str, dict[str, Any]]:
    output_dir = Path(out_docs)
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = build_document_payloads(facts, generated_at=generated_at, evidence=evidence)
    rendered: dict[str, str] = {}
    for slug, payload in payloads.items():
        # Written through a sibling and renamed.  Truncating one of these in
        # place fails on this host whenever another session still holds the
        # copy it has just read, and the design pages are read by the
        # write-back, the inventory and the publication guard.
        write_atomic(
            output_dir / f"{slug}.json",
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        rendered[slug] = render_markdown(payload, read_authored(output_dir, slug))
        write_atomic(output_dir / f"{slug}.md", rendered[slug])
    if evidence is not None:
        foreign = read_foreign_markdown(output_dir, payloads, INDEX_FILENAME)
        inventory = build_coverage_inventory(
            DESIGN_CATEGORIES,
            payloads,
            rendered,
            foreign,
            evidence,
            generated_at=generated_at or utc_now(),
        )
        write_atomic(
            output_dir / f"{INVENTORY_SLUG}.json",
            json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        write_atomic(
            output_dir / f"{INVENTORY_SLUG}.md", render_coverage_inventory(inventory)
        )
        write_atomic(
            output_dir / INDEX_FILENAME,
            render_index(payloads, evidence, foreign_documents(output_dir, payloads)),
        )
    return payloads
