"""Promote F4-13 exploration drafts into the reverse-design dossier (E3).

Spec: ``docs/plans/game-observatory/[2026-09-05]ROMANTIC-RESTAURANT-FULL-DELEGATION-v1/specs/E3-exploration-to-dossier.md``.

The agent handler writes one drafts directory per explored surface
(``<drafts>/<slug>-<ts>/`` with ``interaction-points.json``, ``design-notes.md``,
``signature-draft.json`` and ``evidence-pack.json``).  This module is the
deterministic promoter:

- interaction points route to the 界面与交互 document (``exploration-ui-flow``);
- design-notes lines route by a transparent keyword lexicon to the economy /
  activity / monetization / onboarding / narrative documents, with anything
  unmatched landing in ``exploration-uncategorized`` (the open-registration
  discipline of plan section 8 applies before a new category doc appears);
- the signature draft never enters this pipeline -- it still goes through the
  mechanical registry flow into ``navigation.v1.json``.

Every promoted document is an md+json pair under ``design/`` whose frontmatter
follows the established convention, with evidence level ``live_observed``
(seen live, not cross-validated).  Each promoted entry cites its evidence
(frame ids from the draft's evidence pack) and its config-reference candidates
(``.lua`` references lifted from the notes text); an entry with no candidate is
marked 无配置引用候选 and joins the doc's 待对账清单 -- references are never
invented.

Promotion rebuilds its own documents whole from the drafts every run, keyed by
the drafts directory name, so a repeat run over the same drafts is a zero-diff
no-op.  Documents the generator writes are never touched: the promoted docs
carry no ``生成器: design_extraction`` marker, which keeps the cross-reference
write-back and the generator's own slug space away from them, and the index
regeneration reads them back as foreign documents through the existing
``read_foreign_markdown`` / ``foreign_documents`` seam.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from .coverage_inventory import (
    INVENTORY_SLUG,
    build_coverage_inventory,
    read_foreign_markdown,
    render_coverage_inventory,
)
from .evidence import EvidenceBundle
from .render_docs import (
    DESIGN_CATEGORIES,
    DOC_METADATA,
    INDEX_FILENAME,
    foreign_documents,
    render_index,
    write_atomic,
)

PROMOTION_SCHEMA = "game-observatory.exploration-promotion.v1"
PROMOTER_MARKER = "生成器: design_extraction.exploration_promotion"
EVIDENCE_LEVEL = "live_observed"
NO_CONFIG_CANDIDATE = "无配置引用候选"

DRAFTS_ROOT_HINT = ".omni/sandbox/drafts/rr-agent-handler"

# A config-reference candidate is a decompiled-Lua citation the model already
# wrote into its notes (``EnergyModel.lua:110-120``); the promoter lifts it,
# it never derives one of its own.
_CONFIG_REFERENCE = re.compile(r"[A-Za-z][A-Za-z0-9_]*\.lua(?::\d+(?:-\d+)?)?")

_FRONTMATTER_REQUIRED = (
    "生成器",
    "类型",
    "职能",
    "业务",
    "分面",
    "稳定ID",
    "配表",
    "证据等级",
    "真源",
    "更新时间",
    "tags",
)

# The routing lexicon.  Order decides ties: a line naming both 体力 and 礼包
# is an economy note before it is a monetization one.  The keyword list is
# data, not inference -- a line either names a category's vocabulary or lands
# in uncategorized.
CATEGORY_DOCS: dict[str, dict[str, Any]] = {
    "economy": {
        "slug": "exploration-economy",
        "title": "经济系统：界面探索反拆笔记提升",
        "system": "经济系统",
        "keywords": (
            "体力",
            "能量",
            "金币",
            "钻石",
            "价格",
            "消耗",
            "恢复",
            "经济",
            "收益",
            "产出",
            "冷却",
            "售价",
        ),
    },
    "activity": {
        "slug": "exploration-activity",
        "title": "运营活动：界面探索反拆笔记提升",
        "system": "运营活动",
        "keywords": ("活动", "签到", "日签", "日历", "轮换", "限时"),
    },
    "monetization": {
        "slug": "exploration-monetization",
        "title": "商业化：界面探索反拆笔记提升",
        "system": "商业化触点",
        "keywords": ("付费", "充值", "商店", "礼包", "IAP", "商品", "购买", "¥"),
    },
    "onboarding": {
        "slug": "exploration-onboarding",
        "title": "引导与规则：界面探索反拆笔记提升",
        "system": "引导与规则",
        "keywords": ("引导", "教程", "提示", "规则", "新手"),
    },
    "narrative": {
        "slug": "exploration-narrative",
        "title": "叙事：界面探索反拆笔记提升",
        "system": "叙事",
        "keywords": ("剧情", "对话", "角色", "故事", "叙事", "台词"),
    },
    "uncategorized": {
        "slug": "exploration-uncategorized",
        "title": "未分类界面探索反拆笔记提升",
        "system": "未分类探索",
        "keywords": (),
    },
}

UI_FLOW_DOC = {
    "slug": "exploration-ui-flow",
    "title": "界面系统：界面探索交互点提升",
    "system": "界面流转",
}


# --------------------------------------------------------------------------- #
# reading drafts
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def discover_drafts(drafts_dir: Path) -> list[Path]:
    """Every drafts directory holding an interaction-points artifact, sorted."""

    if not drafts_dir.is_dir():
        return []
    return sorted(
        (
            candidate
            for candidate in drafts_dir.iterdir()
            if candidate.is_dir() and (candidate / "interaction-points.json").is_file()
        ),
        key=lambda path: path.name,
    )


def load_draft(draft_dir: Path) -> dict[str, Any]:
    """One drafts directory as the promoter reads it.

    The stable id is the directory name (``<slug>-<yyyymmddThhmmssZ>``); the
    signature draft is deliberately not read -- it belongs to the registry
    flow, not to this pipeline.
    """

    points_payload = _read_json(draft_dir / "interaction-points.json") or {}
    notes_path = draft_dir / "design-notes.md"
    notes = (
        notes_path.read_text(encoding="utf-8")
        if notes_path.is_file()
        else ""
    )
    evidence_pack = _read_json(draft_dir / "evidence-pack.json") or {}
    evidence = evidence_pack.get("evidence")
    frames = []
    if isinstance(evidence, dict):
        for frame in evidence.get("frames") or []:
            if isinstance(frame, dict) and frame.get("frame_id"):
                frames.append(str(frame["frame_id"]))
    return {
        "id": draft_dir.name,
        "surface_guess": points_payload.get("surface_guess"),
        "result": points_payload.get("result") or evidence_pack.get("result"),
        "points": [
            point
            for point in points_payload.get("points") or []
            if isinstance(point, dict)
        ],
        "notes": notes,
        "evidence_frames": frames,
    }


def _note_lines(notes: str) -> list[str]:
    """The content lines of a design-notes artifact, heading stripped."""

    lines = []
    for line in notes.splitlines():
        stripped = line.strip().lstrip("-*").strip()
        if not stripped or stripped.startswith("#") or stripped == "(none)":
            continue
        lines.append(stripped)
    return lines


def _config_candidates(*texts: str) -> list[str]:
    found: set[str] = set()
    for text in texts:
        found.update(_CONFIG_REFERENCE.findall(text or ""))
    return sorted(found)


def _route_line(line: str) -> str:
    for category, spec in CATEGORY_DOCS.items():
        if category == "uncategorized":
            continue
        if any(keyword in line for keyword in spec["keywords"]):
            return category
    return "uncategorized"


def _draft_date(draft_id: str) -> str | None:
    match = re.search(r"(\d{8})T\d{6}Z", draft_id)
    if match is None:
        return None
    raw = match.group(1)
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _frontmatter(slug: str, title_system: str, updated: str) -> list[str]:
    """The established dossier frontmatter, with the promoter's own marker."""

    return [
        "---",
        PROMOTER_MARKER,
        "类型: 反向设计",
        "职能: 玩法业务",
        f"业务: [{title_system}]",
        "分面: 实机验证",
        f'稳定ID: "[[design:romantic-restaurant/{slug}]]"',
        "配表: []",
        f"证据等级: {EVIDENCE_LEVEL}",
        f'真源: ["{DRAFTS_ROOT_HINT}"]',
        f"更新时间: {updated}",
        'tags: [game-observatory, romantic-restaurant, "实机验证", "反向设计"]',
        "---",
    ]


def _evidence_cell(point: dict[str, Any]) -> str:
    pre = point.get("pre_frame_id")
    post = point.get("post_frame_id")
    if pre and post:
        return f"{pre} → {post}"
    if pre or post:
        return str(pre or post)
    return "—"


def _candidate_clause(candidates: list[str]) -> str:
    if candidates:
        return "、".join(f"`{candidate}`" for candidate in candidates)
    return NO_CONFIG_CANDIDATE


def _render_ui_flow(drafts: list[dict[str, Any]], updated: str) -> tuple[str, dict[str, Any]]:
    slug = UI_FLOW_DOC["slug"]
    entries = []
    lines = [
        *_frontmatter(slug, UI_FLOW_DOC["system"], updated),
        "",
        f"# {UI_FLOW_DOC['title']}",
        "",
        "> 证据声明：本文件由 E3 提升管线从 F4-13 探索 drafts 确定性提升，"
        "条目按批次（drafts 目录名）组织；每条为 live_observed（实机看到，未交叉验证）。",
        "",
        "## 1 说明",
        "",
        f"- 来源：`{DRAFTS_ROOT_HINT}/<批次>/`（interaction-points.json + evidence-pack.json）",
        "- 签名草稿不经本管线：语料扫掠后走机械化登记入 navigation.v1.json。",
        "",
        "## 2 实机验证（交互点清单，按批次）",
        "",
    ]
    pending: list[str] = []
    for draft in drafts:
        if not draft["points"]:
            continue
        candidates = _config_candidates(
            draft["notes"],
            *(str(point.get("semantics") or "") for point in draft["points"]),
        )
        frames = draft["evidence_frames"]
        lines.extend(
            [
                f"### 批次 {draft['id']}",
                "",
                f"- 界面猜测：{draft['surface_guess'] or 'unknown'}；探索结果：{draft['result'] or '—'}",
                f"- 证据帧：{', '.join(frames) if frames else '—'}（drafts 内 evidence-pack.json）",
                f"- 配置引用候选：{_candidate_clause(candidates)}",
                "",
                "| 控件坐标 | 类型 | 语义推断 | 结果 | 来源 | 前后帧 |",
                "|---|---|---|---|---|---|",
            ]
        )
        rows = []
        for point in draft["points"]:
            x, y = point.get("x"), point.get("y")
            coordinate = f"({x}, {y})" if x is not None and y is not None else "—"
            semantics = str(point.get("semantics") or "").replace("|", "\\|")
            row = (
                f"| {coordinate} | {point.get('kind') or '—'} | {semantics} | "
                f"{point.get('result') or '—'} | {point.get('source') or '—'} | "
                f"{_evidence_cell(point)} |"
            )
            lines.append(row)
            rows.append(
                {
                    "x": x,
                    "y": y,
                    "kind": point.get("kind"),
                    "semantics": point.get("semantics"),
                    "result": point.get("result"),
                    "source": point.get("source"),
                    "pre_frame_id": point.get("pre_frame_id"),
                    "post_frame_id": point.get("post_frame_id"),
                }
            )
        lines.append("")
        if not candidates:
            pending.append(draft["id"])
        entries.append(
            {
                "draft": draft["id"],
                "surface_guess": draft["surface_guess"],
                "result": draft["result"],
                "points": rows,
                "evidence_frames": frames,
                "config_candidates": candidates,
            }
        )
    lines.extend(_reconciliation_section(pending))
    payload = {
        "schema": PROMOTION_SCHEMA,
        "slug": slug,
        "title": UI_FLOW_DOC["title"],
        "generated_at": updated,
        "evidence_level": EVIDENCE_LEVEL,
        "source": DRAFTS_ROOT_HINT,
        "category": "界面与交互",
        "entries": entries,
        "reconciliation_pending": pending,
    }
    return "\n".join(lines).rstrip() + "\n", payload


def _reconciliation_section(pending: list[str]) -> list[str]:
    lines = ["## 待对账清单（缺配置引用候选）", ""]
    if not pending:
        lines.append("无：本文件所有批次都带配置引用候选。")
        return lines
    lines.append("以下批次缺配置引用候选，标 无配置引用候选，待与反编译 Lua 对账：")
    lines.append("")
    lines.extend(f"- {draft_id}（{NO_CONFIG_CANDIDATE}）" for draft_id in pending)
    return lines


def _render_notes_doc(
    category: str,
    drafts_notes: list[tuple[dict[str, Any], list[str]]],
    updated: str,
) -> tuple[str, dict[str, Any]]:
    spec = CATEGORY_DOCS[category]
    slug = spec["slug"]
    entries = []
    pending: list[str] = []
    lines = [
        *_frontmatter(slug, spec["system"], updated),
        "",
        f"# {spec['title']}",
        "",
        "> 证据声明：本文件由 E3 提升管线从 F4-13 探索 drafts 的 design-notes "
        "按关键词路由确定性提升；每条为 live_observed（实机看到，未交叉验证）。",
        "",
        "## 2 实机验证（反拆笔记，按批次）",
        "",
    ]
    for draft, note_lines in drafts_notes:
        candidates = _config_candidates("\n".join(note_lines))
        frames = draft["evidence_frames"]
        lines.extend(
            [
                f"### 批次 {draft['id']}",
                "",
                f"- 界面猜测：{draft['surface_guess'] or 'unknown'}；探索结果：{draft['result'] or '—'}",
                f"- 证据帧：{', '.join(frames) if frames else '—'}（drafts 内 evidence-pack.json）",
                f"- 配置引用候选：{_candidate_clause(candidates)}",
                "",
            ]
        )
        lines.extend(f"- {line}" for line in note_lines)
        lines.append("")
        if not candidates:
            pending.append(draft["id"])
        entries.append(
            {
                "draft": draft["id"],
                "surface_guess": draft["surface_guess"],
                "result": draft["result"],
                "notes": note_lines,
                "evidence_frames": frames,
                "config_candidates": candidates,
            }
        )
    lines.extend(_reconciliation_section(pending))
    payload = {
        "schema": PROMOTION_SCHEMA,
        "slug": slug,
        "title": spec["title"],
        "generated_at": updated,
        "evidence_level": EVIDENCE_LEVEL,
        "source": DRAFTS_ROOT_HINT,
        "category": category,
        "entries": entries,
        "reconciliation_pending": pending,
    }
    return "\n".join(lines).rstrip() + "\n", payload


# --------------------------------------------------------------------------- #
# the promotion
# --------------------------------------------------------------------------- #
def promote_drafts(
    drafts_dir: Path,
    design_dir: Path,
) -> dict[str, Any]:
    """Rebuild the promoted documents from every drafts directory found.

    The promoted docs are this pipeline's own products, rebuilt whole each
    run and keyed by drafts directory name: a repeat run over unchanged
    drafts writes byte-identical files.  Every other document in the design
    directory is left untouched.
    """

    drafts = [load_draft(directory) for directory in discover_drafts(Path(drafts_dir))]
    dates = sorted(date for draft in drafts if (date := _draft_date(draft["id"])))
    updated = dates[-1] if dates else datetime.now(UTC).date().isoformat()

    notes_by_category: dict[str, list[tuple[dict[str, Any], list[str]]]] = {
        category: [] for category in CATEGORY_DOCS
    }
    for draft in drafts:
        routed: dict[str, list[str]] = {}
        for line in _note_lines(draft["notes"]):
            routed.setdefault(_route_line(line), []).append(line)
        for category, lines in routed.items():
            notes_by_category[category].append((draft, lines))

    design_dir = Path(design_dir)
    design_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, dict[str, Any]] = {}
    ui_drafts = [draft for draft in drafts if draft["points"]]
    if ui_drafts:
        markdown, payload = _render_ui_flow(ui_drafts, updated)
        written[UI_FLOW_DOC["slug"]] = {"markdown": markdown, "payload": payload}
    for category, drafts_notes in notes_by_category.items():
        if not drafts_notes:
            continue
        markdown, payload = _render_notes_doc(category, drafts_notes, updated)
        written[payload["slug"]] = {"markdown": markdown, "payload": payload}
    for slug, document in sorted(written.items()):
        write_atomic(
            design_dir / f"{slug}.json",
            json.dumps(document["payload"], ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        write_atomic(design_dir / f"{slug}.md", document["markdown"])
    return {
        "schema": PROMOTION_SCHEMA,
        "drafts_dir": str(drafts_dir),
        "design_dir": str(design_dir),
        "updated": updated,
        "drafts_promoted": [draft["id"] for draft in drafts],
        "documents": {
            slug: {
                "entries": len(document["payload"]["entries"]),
                "reconciliation_pending": document["payload"]["reconciliation_pending"],
            }
            for slug, document in sorted(written.items())
        },
    }


# --------------------------------------------------------------------------- #
# index regeneration through the existing generators
# --------------------------------------------------------------------------- #
def regenerate_index(
    design_dir: Path,
    evidence: EvidenceBundle,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Re-render README and the coverage inventory around the promoted docs.

    This reuses the generator's own assemblers with the payloads and markdown
    already on disk: no generated document is rewritten, and the promoted docs
    enter both products through the foreign-document seam.
    """

    design_dir = Path(design_dir)
    generated_at = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    payloads: dict[str, dict[str, Any]] = {}
    rendered: dict[str, str] = {}
    for slug in DOC_METADATA:
        payload_path = design_dir / f"{slug}.json"
        markdown_path = design_dir / f"{slug}.md"
        if not payload_path.is_file() or not markdown_path.is_file():
            continue
        payloads[slug] = json.loads(payload_path.read_text(encoding="utf-8"))
        rendered[slug] = markdown_path.read_text(encoding="utf-8")
    foreign = read_foreign_markdown(design_dir, payloads, INDEX_FILENAME)
    inventory = build_coverage_inventory(
        DESIGN_CATEGORIES,
        payloads,
        rendered,
        foreign,
        evidence,
        generated_at=generated_at,
    )
    write_atomic(
        design_dir / f"{INVENTORY_SLUG}.json",
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    write_atomic(
        design_dir / f"{INVENTORY_SLUG}.md", render_coverage_inventory(inventory)
    )
    write_atomic(
        design_dir / INDEX_FILENAME,
        render_index(payloads, evidence, foreign_documents(design_dir, payloads)),
    )
    return {
        "generated_documents": sorted(payloads),
        "foreign_documents": sorted(foreign),
        "index": str(design_dir / INDEX_FILENAME),
        "inventory": str(design_dir / f"{INVENTORY_SLUG}.md"),
    }


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--drafts", type=Path, required=True, help="F4-13 drafts root")
    parser.add_argument("--out-design", type=Path, required=True, help="dossier design dir")
    parser.add_argument(
        "--crossref-dir",
        type=Path,
        help="crossref products dir; enables README/coverage-inventory regeneration",
    )
    parser.add_argument("--records-dir", type=Path, help="plan records directory")
    parser.add_argument("--metrics", type=Path, help="efficiency dashboard json")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = promote_drafts(args.drafts, args.out_design)
    if any(value is not None for value in (args.crossref_dir, args.records_dir, args.metrics)):
        # Regenerating the index without the crossref/records products would
        # empty the ledger sections, so regeneration only runs when the caller
        # points at least one evidence source.
        evidence = EvidenceBundle.load(
            crossref_dir=args.crossref_dir,
            records_dir=args.records_dir,
            metrics=args.metrics,
        )
        result["index_regen"] = regenerate_index(args.out_design, evidence)
    else:
        result["index_regen"] = "skipped_no_evidence"
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        documents = ", ".join(sorted(result["documents"])) or "none"
        print(f"promoted {len(result['drafts_promoted'])} drafts into: {documents}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
