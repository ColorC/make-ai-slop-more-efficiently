# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-07-03T00:00:00Z type=infra status=active
# [OMNI] summary="poof-notes 消费任务: 笔记(index.json + docs/*.md)只读读取 → 分类入册(标题分叉修正+双时间+受控标签)→ 语义索引可检索, 逐条 updatedDate 水位线增量。附孤儿 ydoc 回收(只移动进 _trash, collection 根绝不动)。"
# [OMNI] why="批3开工锚(overnight-run.md「批3 开工锚」): poof 零改动, 语义标签只存 omni 侧属性不回写 index.json; 孤儿 13 个只挪观察不删。"
# [OMNI] tags=semantic-os,notes-materialize,batch3,poof-notes
# [OMNI] material_id="material:services._core.lifecycle.notes_materialize.py"
"""poof-notes 消费任务(消费侧入册)。

run_notes_materialize(notes_root, *, state_path, model=None, root=None, submit_review=False):
    读 notes_root/index.json + docs/<id>.md(只读, 不改真源) → 对每条按 updatedDate 做
    逐条水位线(state_path 记 note_id → 上次处理的 updatedDate) → 有变更的送
    classify_material() 分类(受控词表)→ 进程内注册成 material → 写回 semantic 属性
    (双时间: content_time 取笔记 createDate/updatedDate, ingested_time 取入册时刻) →
    标题分叉修正(index.json 的 title 若是"未命名笔记"占位, 改取正文首个 # 标题)。

quarantine_orphan_ydocs(notes_root, dry_run):
    index.json 未收录的 docs/*.ydoc(孤儿)→ 只移动进 notes_root/_trash/, 逐条清单标注
    空骨架/有内容(有内容的粗提取一段可读文本做预览, 标注"粗提取")。collection 根
    (docs/poof-notes.ydoc)硬编码排除, 绝不触碰。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

_UNNAMED_TITLES = {"未命名笔记", "未命名", "Untitled", "untitled"}
_COLLECTION_ROOT_YDOC = "poof-notes.ydoc"
_EMPTY_SKELETON_MAX_BYTES = 2048  # 小于此判"空骨架"(经验值, 见 overnight-run.md 定性修正)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _load_index(notes_root: Path) -> dict[str, Any]:
    idx_path = notes_root / "index.json"
    if not idx_path.is_file():
        return {"notes": []}
    try:
        return json.loads(idx_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"notes": []}


def _load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.is_file():
        return {"notes": {}}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"notes": {}}


def _save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _first_markdown_heading(md_text: str) -> str | None:
    for line in md_text.splitlines():
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            return m.group(1).strip()
    return None


def _resolve_title(index_title: str, md_text: str | None) -> str:
    """标题分叉修正: docMeta.title 是占位("未命名笔记"等)时取正文首个 # 标题; 否则保留原标题。"""
    if index_title and index_title.strip() not in _UNNAMED_TITLES:
        return index_title
    if md_text:
        heading = _first_markdown_heading(md_text)
        if heading:
            return heading
    return index_title or "(无标题)"


def _note_entity_id(note_id: str) -> str:
    return f"data:notes.{note_id}"


def _register_note_inprocess(*, note_id: str, title: str, md_abs_path: str, register_kind: str) -> str:
    """进程内注册一条笔记为 material(仿 semantic_fs.classify._register_inprocess, 但用笔记
    自己的稳定 id 做 entity_id, 不依赖 base 相对路径, 因为 notes_root 通常不在 omni 仓内)。

    source_file 存**绝对路径**(而非相对 omni 仓根的相对路径) —— poof-notes 浅路径与 omni
    仓是两个不同的根, 用绝对路径能让 semantic_fs.index.build_index 无论传入哪个 root 都能
    正确 `root / source_file` 定位到真实文件(pathlib 对绝对路径的 join 会直接返回绝对路径)。
    """
    from omnicompany.packages.services._core.registry import InstanceEntry, get_registry

    entity_id = _note_entity_id(note_id)
    reg = get_registry()
    existing = reg.read(entity_id)
    attrs = dict(existing.attrs) if existing else {}
    attrs.update({
        "kind_omnicompany": register_kind,
        "registered_via": "notes_materialize",
        "is_directory": False,
        "title_resolved": title,
    })
    reg.write(InstanceEntry(
        entity_id=entity_id, type=register_kind, name=title or note_id,
        package="notes", source_file=md_abs_path or "", attrs=attrs,
        deps=(existing.deps if existing else []),
    ))
    return entity_id


def run_notes_materialize(
    notes_root: Path,
    *,
    state_path: Path,
    model: str | None = None,
    root: Path | None = None,
    submit_review: bool = False,
    review_store=None,
) -> dict[str, Any]:
    """消费一轮: 读 index.json + docs/*.md(只读), 逐条水位线增量分类入册。真源零改动。"""
    from omnicompany.packages.services._core.semantic_fs import classify as classify_mod
    from omnicompany.packages.services._core.semantic_fs import schema as schema_mod

    notes_root = Path(notes_root)
    idx = _load_index(notes_root)
    state = _load_state(Path(state_path))
    note_states: dict[str, Any] = dict(state.get("notes") or {})

    materialized = 0
    llm_calls = 0
    entity_ids: list[str] = []
    review_items: list[dict[str, Any]] = []

    for n in idx.get("notes") or []:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        note_id = str(n["id"])
        updated_date = n.get("updatedDate")
        watermark_key = str(updated_date) if updated_date is not None else ""
        prev = note_states.get(note_id)
        if prev is not None and prev.get("updatedDate_seen") == watermark_key:
            continue  # 逐条水位线: 这条没变, 跳过(零 LLM 调用)

        md_rel = n.get("md")
        md_path = (notes_root / md_rel) if md_rel else None
        md_text = None
        if md_path and md_path.is_file():
            try:
                md_text = md_path.read_text(encoding="utf-8")
            except OSError:
                md_text = None

        classify_target = md_path if (md_path and md_path.is_file()) else notes_root
        c = classify_mod.classify_material(classify_target, model=model, root=root)
        llm_calls += 1

        title = _resolve_title(str(n.get("title") or ""), md_text)
        md_abs = str(md_path.resolve()) if (md_path and md_path.is_file()) else ""
        entity_id = _register_note_inprocess(
            note_id=note_id, title=title,
            md_abs_path=md_abs,
            register_kind=c.get("register_kind", "data"),
        )

        create_date = n.get("createDate")
        content_time = str(updated_date if updated_date is not None else (create_date or _today()))
        schema_mod.set_semantic(
            entity_id,
            semantic_tags=c.get("semantic_tags") or [],
            content_time=content_time,
            ingested_time=_today(),
            root=root,
        )

        if submit_review and (c.get("confidence") == "low" or c.get("invalid_tags")):
            review_items.append({"note_id": note_id, "title": title, "entity_id": entity_id, "classify": c})

        note_states[note_id] = {"updatedDate_seen": watermark_key, "entity_id": entity_id}
        entity_ids.append(entity_id)
        materialized += 1

    _save_state(Path(state_path), {"notes": note_states})
    review_material = None
    if review_items:
        if review_store is None:
            from omnicompany.dashboard.boss_sight.reviewstage.routes import get_store
            review_store = get_store()
        from omnicompany.dashboard.boss_sight.reviewstage.report_submission import submit_markdown_report
        lines = ["# 笔记语义分类待核", ""]
        for item in review_items:
            c = item["classify"]
            lines.append(
                f"- `{item['note_id']}` · {item['title']} · `{item['entity_id']}` · "
                f"置信 `{c.get('confidence')}` · 越界 `{c.get('invalid_tags')}`"
            )
        review_material = submit_markdown_report(
            review_store, title="笔记语义分类待核", content="\n".join(lines),
            source_plan_id="format-material/[2026-06-27]SEMANTIC-FILESYSTEM-ALL-MATERIAL",
            reason="本轮笔记自动分类存在低置信或越界标签; 请按合并清单核对。",
            dedupe_key="notes-materialize-classification",
            stable_payload=json.dumps(review_items, ensure_ascii=False, sort_keys=True),
            version_family="notes-materialize-classification",
        )

    return {"ok": True, "materialized": materialized, "llm_calls": llm_calls,
            "entity_ids": entity_ids, "review_material": review_material}


# ─────────────────────────── 孤儿 ydoc 回收 ───────────────────────────

def _extract_readable_text(data: bytes, max_chars: int = 200) -> str:
    """从 ydoc 二进制粗提取可读文本(供预览用, 不是正式 Yjs 解码)。"""
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""
    # 只留可打印字符聚成的片段(粗提取, 标注清楚不是精确解析)
    printable = re.sub(r"[^\x20-\x7e一-鿿]+", " ", text)
    printable = re.sub(r"\s+", " ", printable).strip()
    return printable[:max_chars]


def quarantine_orphan_ydocs(notes_root: Path, dry_run: bool = True) -> dict[str, Any]:
    """孤儿(index.json 未收录的 docs/*.ydoc)只允许移动进 _trash/, collection 根绝不动。

    dry_run=True: 只出清单, 不移动任何文件(白天决定救回前的安全预览)。
    """
    notes_root = Path(notes_root)
    docs_dir = notes_root / "docs"
    if not docs_dir.is_dir():
        return {"ok": True, "items": []}

    idx = _load_index(notes_root)
    registered_ydocs = {
        Path(n["ydoc"]).name for n in (idx.get("notes") or [])
        if isinstance(n, dict) and n.get("ydoc")
    }

    items: list[dict[str, Any]] = []
    for p in sorted(docs_dir.glob("*.ydoc")):
        if p.name == _COLLECTION_ROOT_YDOC:
            continue  # collection 根: 硬编码排除, 即便疏忽被 index.json 排除在外也绝不动
        if p.name in registered_ydocs:
            continue  # 在册笔记的 ydoc, 不是孤儿

        try:
            size = p.stat().st_size
            data = p.read_bytes()
        except OSError:
            continue
        kind = "empty_skeleton" if size <= _EMPTY_SKELETON_MAX_BYTES else "has_content"
        item: dict[str, Any] = {"file": p.name, "kind": kind, "size_bytes": size}
        if kind == "has_content":
            preview = _extract_readable_text(data)
            item["extraction_note"] = f"粗提取(非精确 Yjs 解码): {preview}" if preview else "粗提取: 未能提取到可读文本"
        items.append(item)

    if dry_run:
        return {"ok": True, "items": items, "dry_run": True}

    trash_dir = notes_root / "_trash"
    trash_dir.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for item in items:
        src = docs_dir / item["file"]
        dest = trash_dir / item["file"]
        try:
            src.rename(dest)
            moved.append(item["file"])
        except OSError as e:  # noqa: BLE001
            item["move_error"] = str(e)
            continue

    manifest_path = trash_dir / "manifest.json"
    existing_manifest: dict[str, Any] = {"items": []}
    if manifest_path.is_file():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing_manifest = {"items": []}
    existing_by_file = {it["file"]: it for it in existing_manifest.get("items") or [] if isinstance(it, dict)}
    for item in items:
        existing_by_file[item["file"]] = item
    manifest_path.write_text(
        json.dumps({"items": list(existing_by_file.values()), "generated_at": _today()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {"ok": True, "items": items, "moved": moved, "dry_run": False,
            "manifest_path": str(manifest_path)}


__all__ = ["run_notes_materialize", "quarantine_orphan_ydocs"]
