# [OMNI] origin=codex domain=services/_core/material_wiki ts=2026-08-02T00:00:00+08:00 type=router status=active
# [OMNI] summary="材料 Wiki 存量迁移器：逐文件 inventory、规则归位、状态证据、dry-run、幂等本体写入、冲突回执和可恢复回滚"
# [OMNI] why="散落内容不能靠人工记忆选择性搬运，也不能在迁移时静默升级认可状态；每个范围内文件都要获得可追溯处置"
# [OMNI] tags=material-wiki,migration,inventory,dry-run,rollback
# [OMNI] material_id="material:core.material_wiki.migration.py"
"""Inventory and safely migrate scattered project material into canonical Wikis.

noqa-OMNI-080: this module parses project-authored JSON sources and facility
inventories/receipts; it does not parse LLM finish-tool text.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping
from urllib.parse import quote

import yaml

from .adapters.markdown import MaterialEntryNotFoundError
from .profiles import resolve_canonical_root
from .runtime import MaterialWikiRuntime
from .schema import MaterialSourceRef


TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".json", ".yaml", ".yml"})
DEFAULT_EXCLUDES = (".git/**", "node_modules/**", "dist/**", "__pycache__/**", "*.pyc")


@dataclass(frozen=True, slots=True)
class MigrationRule:
    pattern: str
    disposition: str
    placement_path: tuple[str, ...] = ()
    status: str | None = None
    status_evidence: str | None = None
    note: str | None = None
    extract: str = "file"
    id_field: str = "id"
    title_field: str = "title"
    status_field: str | None = None
    status_map: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class MigrationScope:
    project_id: str
    root_ref: str
    rules: tuple[MigrationRule, ...]
    default_disposition: str
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES


@dataclass(frozen=True, slots=True)
class MigrationManifest:
    batch_id: str
    scopes: tuple[MigrationScope, ...]
    superseded_by: str | None = None


@dataclass(frozen=True, slots=True)
class InventoryRecord:
    project_id: str
    root_ref: str
    relative_path: str
    source_ref: str
    size: int
    sha256: str
    disposition: str
    placement_path: tuple[str, ...]
    proposed_status: str | None
    status_evidence: str | None
    note: str | None
    material_id: str | None
    container_relative_path: str | None = None
    fragment: str | None = None
    fragment_index: int | None = None
    proposed_title: str | None = None


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = handle.name
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = handle.name
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_migration_manifest(path: str | Path) -> MigrationManifest:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping) or int(raw.get("schema_version", 0)) != 1:
        raise ValueError("migration manifest schema_version 必须为 1")
    scopes: list[MigrationScope] = []
    for scope_raw in raw.get("scopes") or ():
        rules = tuple(
            MigrationRule(
                pattern=str(item.get("pattern") or ""),
                disposition=str(item.get("disposition") or ""),
                placement_path=tuple(item.get("placement_path") or ()),
                status=item.get("status"),
                status_evidence=item.get("status_evidence"),
                note=item.get("note"),
                extract=str(item.get("extract") or "file"),
                id_field=str(item.get("id_field") or "id"),
                title_field=str(item.get("title_field") or "title"),
                status_field=(str(item["status_field"]) if item.get("status_field") else None),
                status_map=tuple(
                    (str(key), str(value))
                    for key, value in (item.get("status_map") or {}).items()
                ),
            )
            for item in scope_raw.get("rules") or ()
        )
        if any(not rule.pattern or not rule.disposition for rule in rules):
            raise ValueError("migration rule 必须含 pattern 与 disposition")
        if any(rule.extract not in {"file", "json_records"} for rule in rules):
            raise ValueError("migration rule extract 只支持 file 或 json_records")
        scopes.append(
            MigrationScope(
                project_id=str(scope_raw.get("project_id") or ""),
                root_ref=str(scope_raw.get("root_ref") or ""),
                rules=rules,
                default_disposition=str(scope_raw.get("default_disposition") or "unclassified"),
                excludes=tuple(scope_raw.get("excludes") or DEFAULT_EXCLUDES),
            )
        )
    if not scopes:
        raise ValueError("migration manifest 至少需要一个 scope")
    return MigrationManifest(
        batch_id=str(raw.get("batch_id") or ""),
        scopes=tuple(scopes),
        superseded_by=(str(raw["superseded_by"]) if raw.get("superseded_by") else None),
    )


def _matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path.casefold(), pattern.casefold())


def _material_id(project_id: str, source_ref: str) -> str:
    digest = hashlib.sha256(source_ref.encode("utf-8")).hexdigest()[:16]
    project = re.sub(r"[^a-z0-9]+", "-", project_id.casefold()).strip("-")
    return f"{project}.material.migrated.{digest}"


def _source_ref(root_ref: str, relative: str) -> str:
    return f"{root_ref.rstrip('/')}/{relative}"


def build_inventory(
    manifest: MigrationManifest,
    *,
    omnicompany_root: str | Path,
) -> tuple[InventoryRecord, ...]:
    records: list[InventoryRecord] = []
    for scope in manifest.scopes:
        root = resolve_canonical_root(scope.root_ref, omnicompany_root=omnicompany_root)
        if not root.is_dir():
            raise FileNotFoundError(f"inventory root 不存在：{root}")
        for path in sorted((item for item in root.rglob("*") if item.is_file())):
            relative = path.relative_to(root).as_posix()
            if any(_matches(relative, pattern) for pattern in scope.excludes):
                continue
            rule = next((item for item in scope.rules if _matches(relative, item.pattern)), None)
            disposition = rule.disposition if rule else scope.default_disposition
            raw = path.read_bytes()
            if rule and rule.extract == "json_records" and disposition == "migrate":
                parsed = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(parsed, list):
                    raise ValueError(f"json_records 规则要求顶层数组：{path}")
                if not parsed:
                    source_ref = _source_ref(scope.root_ref, relative)
                    records.append(
                        InventoryRecord(
                            project_id=scope.project_id,
                            root_ref=scope.root_ref,
                            relative_path=relative,
                            source_ref=source_ref,
                            size=len(raw),
                            sha256=hashlib.sha256(raw).hexdigest(),
                            disposition="empty_collection",
                            placement_path=rule.placement_path,
                            proposed_status=None,
                            status_evidence=rule.status_evidence,
                            note="结构化源为空；已盘点，无内容条目需要迁移",
                            material_id=None,
                        )
                    )
                    continue
                status_map = dict(rule.status_map)
                identities = [
                    str(item.get(rule.id_field) or f"item-{index + 1}")
                    if isinstance(item, Mapping)
                    else f"item-{index + 1}"
                    for index, item in enumerate(parsed)
                ]
                identity_counts = Counter(identities)
                for index, item in enumerate(parsed):
                    if not isinstance(item, Mapping):
                        raise ValueError(f"json_records 仅支持对象元素：{path}#{index}")
                    identity = str(item.get(rule.id_field) or f"item-{index + 1}")
                    identity_token = (
                        identity
                        if identity_counts[identity] == 1
                        else f"{identity}~{index + 1}"
                    )
                    fragment = quote(identity_token, safe="-._~")
                    source_ref = f"{_source_ref(scope.root_ref, relative)}#record={fragment}"
                    serialized = json.dumps(
                        item,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    raw_status = (
                        str(item.get(rule.status_field) or "")
                        if rule.status_field
                        else ""
                    )
                    proposed_status = status_map.get(raw_status, rule.status)
                    records.append(
                        InventoryRecord(
                            project_id=scope.project_id,
                            root_ref=scope.root_ref,
                            relative_path=f"{relative}#record={fragment}",
                            source_ref=source_ref,
                            size=len(serialized),
                            sha256=hashlib.sha256(serialized).hexdigest(),
                            disposition=disposition,
                            placement_path=rule.placement_path,
                            proposed_status=proposed_status,
                            status_evidence=rule.status_evidence,
                            note=rule.note,
                            material_id=_material_id(scope.project_id, source_ref),
                            container_relative_path=relative,
                            fragment=identity,
                            fragment_index=index,
                            proposed_title=str(item.get(rule.title_field) or identity),
                        )
                    )
                continue
            source_ref = _source_ref(scope.root_ref, relative)
            records.append(
                InventoryRecord(
                    project_id=scope.project_id,
                    root_ref=scope.root_ref,
                    relative_path=relative,
                    source_ref=source_ref,
                    size=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest(),
                    disposition=disposition,
                    placement_path=rule.placement_path if rule else (),
                    proposed_status=rule.status if rule else None,
                    status_evidence=rule.status_evidence if rule else None,
                    note=rule.note if rule else None,
                    material_id=(
                        _material_id(scope.project_id, source_ref)
                        if disposition == "migrate"
                        else None
                    ),
                )
            )
    return tuple(records)


def inventory_payload(manifest: MigrationManifest, records: tuple[InventoryRecord, ...]) -> dict[str, Any]:
    dispositions: dict[str, int] = {}
    projects: dict[str, int] = {}
    for record in records:
        dispositions[record.disposition] = dispositions.get(record.disposition, 0) + 1
        projects[record.project_id] = projects.get(record.project_id, 0) + 1
    source_files = {
        (record.project_id, record.root_ref, record.container_relative_path or record.relative_path)
        for record in records
    }
    return {
        "schema_version": 1,
        "batch_id": manifest.batch_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "source_files": len(source_files),
            "inventory_records": len(records),
            "projects": projects,
            "dispositions": dispositions,
        },
        "records": [asdict(record) for record in records],
    }


def write_inventory(
    manifest_path: str | Path,
    output_path: str | Path,
    *,
    omnicompany_root: str | Path,
) -> dict[str, Any]:
    manifest = load_migration_manifest(manifest_path)
    if manifest.superseded_by:
        raise ValueError(
            f"迁移批次 {manifest.batch_id} 已弃用并由 {manifest.superseded_by} 取代，不允许再次执行"
        )
    records = build_inventory(manifest, omnicompany_root=omnicompany_root)
    payload = inventory_payload(manifest, records)
    _atomic_json(Path(output_path), payload)
    return payload


def write_deprecation_ledger(
    inventory_path: str | Path,
    output_path: str | Path,
    *,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Render a complete human-readable archive view from migration truth.

    The inventory and canonical terminal entries remain authoritative.  This
    ledger is deliberately a generated report view so an old file cannot regain
    authority merely because it appears in an archive list.
    """
    inventory_file = Path(inventory_path)
    inventory = json.loads(inventory_file.read_text(encoding="utf-8"))
    if int(inventory.get("schema_version", 0)) != 1:
        raise ValueError("inventory schema_version 必须为 1")
    records = tuple(inventory.get("records") or ())
    deprecated = sorted(
        (item for item in records if item.get("disposition") == "deprecated"),
        key=lambda item: (str(item.get("project_id")), str(item.get("source_ref"))),
    )
    terminal = sorted(
        (
            item
            for item in records
            if item.get("disposition") == "migrate"
            and item.get("proposed_status") in {"rejected", "superseded"}
        ),
        key=lambda item: (str(item.get("project_id")), str(item.get("source_ref"))),
    )
    canonical_by_id: dict[str, str] = {}
    if receipt_path is not None:
        receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
        canonical_by_id = {
            str(item.get("material_id")): str(item.get("canonical_ref") or "")
            for item in receipt.get("results") or ()
            if item.get("material_id")
        }

    def cell(value: Any) -> str:
        return str(value or "—").replace("|", "\\|").replace("\n", " ")

    generated_at = datetime.now(timezone.utc).isoformat()
    lines = [
        "<!-- [OMNI] origin=material-wiki-migration domain=docs/plans/format-material "
        f"ts={generated_at} type=report status=generated -->",
        '<!-- [OMNI] summary="材料 Wiki 迁移中所有明确弃用源与终态拒绝/覆写本体的完整归档索引" -->',
        "",
        "# 材料 Wiki 弃用与终态归档索引",
        "",
        "> 本页是自动生成的归档视图，不是材料本体。处置真相来自 inventory；被拒绝或覆写的内容本体以 `rejected` / `superseded` 终态留存。",
        "",
        f"- 批次：`{cell(inventory.get('batch_id'))}`",
        f"- Inventory SHA-256：`{hashlib.sha256(inventory_file.read_bytes()).hexdigest()}`",
        f"- 明确弃用源：**{len(deprecated)}**",
        f"- 终态归档本体：**{len(terminal)}**",
        "",
        "## 明确弃用源",
        "",
        "| 项目 | 原始来源 | 处置依据 |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| {cell(item.get('project_id'))} | `{cell(item.get('source_ref'))}` | {cell(item.get('note'))} |"
        for item in deprecated
    )
    lines.extend(
        [
            "",
            "## 已拒绝或被覆写的终态本体",
            "",
            "| 项目 | 原始来源 | 状态 | Material ID | 本体位置 | 状态依据 |",
            "|---|---|---|---|---|---|",
        ]
    )
    lines.extend(
        "| "
        + " | ".join(
            (
                cell(item.get("project_id")),
                f"`{cell(item.get('source_ref'))}`",
                f"`{cell(item.get('proposed_status'))}`",
                f"`{cell(item.get('material_id'))}`",
                f"`{cell(canonical_by_id.get(str(item.get('material_id'))))}`",
                cell(item.get("status_evidence")),
            )
        )
        + " |"
        for item in terminal
    )
    lines.extend(
        [
            "",
            "## 使用规则",
            "",
            "- 上述原始来源只用于追溯，不得继续作为当前材料输入。",
            "- 若需复用其中内容，必须通过统一 Material Wiki 写入建立新版本或派生关系。",
            "- 本页不得手工增删条目；更新 manifest 后重新生成 inventory 与本页。",
            "",
        ]
    )
    _atomic_text(Path(output_path), "\n".join(lines))
    return {
        "batch_id": inventory.get("batch_id"),
        "inventory_sha256": hashlib.sha256(inventory_file.read_bytes()).hexdigest(),
        "deprecated_sources": len(deprecated),
        "terminal_entries": len(terminal),
        "output": str(output_path),
    }


def _inventory_signature(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("project_id"),
        record.get("source_ref"),
        record.get("sha256"),
        record.get("disposition"),
        tuple(record.get("placement_path") or ()),
        record.get("proposed_status"),
        record.get("status_evidence"),
        record.get("material_id"),
    )


def audit_inventory(
    manifest_path: str | Path,
    inventory_path: str | Path,
    *,
    omnicompany_root: str | Path,
) -> dict[str, Any]:
    """Prove a saved inventory still matches live source bytes and migration rules."""
    manifest = load_migration_manifest(manifest_path)
    saved = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    live_records = build_inventory(manifest, omnicompany_root=omnicompany_root)
    saved_records = tuple(saved.get("records") or ())
    errors: list[str] = []
    if saved.get("batch_id") != manifest.batch_id:
        errors.append("inventory batch_id 与 manifest 不一致")
    if any(item.get("disposition") == "unclassified" for item in saved_records):
        errors.append("inventory 仍含 unclassified 文件")
    migrate = [item for item in saved_records if item.get("disposition") == "migrate"]
    if any(
        not item.get("placement_path")
        or not item.get("proposed_status")
        or not item.get("status_evidence")
        or not item.get("material_id")
        for item in migrate
    ):
        errors.append("migrate 记录缺 placement/status/status_evidence/material_id")
    material_ids = [str(item.get("material_id")) for item in migrate]
    duplicates = sorted(
        material_id
        for material_id, count in Counter(material_ids).items()
        if material_id and count > 1
    )
    if duplicates:
        errors.append(f"material_id 重复：{', '.join(duplicates[:10])}")
    saved_signatures = Counter(_inventory_signature(item) for item in saved_records)
    live_signatures = Counter(_inventory_signature(asdict(item)) for item in live_records)
    if saved_signatures != live_signatures:
        removed = sum((saved_signatures - live_signatures).values())
        added = sum((live_signatures - saved_signatures).values())
        errors.append(f"源文件或规则自 inventory 后发生漂移：saved_only={removed}, live_only={added}")
    return {
        "batch_id": manifest.batch_id,
        "inventory_sha256": hashlib.sha256(Path(inventory_path).read_bytes()).hexdigest(),
        "source_files": int((saved.get("summary") or {}).get("source_files") or 0),
        "inventory_records": len(saved_records),
        "migration_candidates": len(migrate),
        "errors": errors,
        "ok": not errors,
    }


def _title(path: Path) -> str:
    if path.suffix.casefold() in {".md", ".markdown"}:
        text = path.read_text(encoding="utf-8-sig")
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    return path.stem


def _body(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix not in TEXT_SUFFIXES:
        raise ValueError(f"不支持把 {suffix or '无扩展名'} 文件复制为正文：{path}")
    text = path.read_text(encoding="utf-8-sig").rstrip()
    if suffix == ".json":
        parsed = json.loads(text)
        text = json.dumps(parsed, ensure_ascii=False, indent=2)
        return f"```json\n{text}\n```"
    if suffix in {".yaml", ".yml"}:
        return f"```yaml\n{text}\n```"
    return text


def _migration_records(
    manifest: MigrationManifest,
    *,
    omnicompany_root: Path,
) -> tuple[tuple[InventoryRecord, Path], ...]:
    records = build_inventory(manifest, omnicompany_root=omnicompany_root)
    result: list[tuple[InventoryRecord, Path]] = []
    for record in records:
        if record.disposition != "migrate":
            continue
        root = resolve_canonical_root(record.root_ref, omnicompany_root=omnicompany_root)
        relative = record.container_relative_path or record.relative_path
        result.append((record, root / Path(relative)))
    return tuple(result)


def _record_body(record: InventoryRecord, source_path: Path) -> str:
    if record.fragment_index is None:
        return _body(source_path)
    parsed = json.loads(source_path.read_text(encoding="utf-8-sig"))
    if not isinstance(parsed, list) or record.fragment_index >= len(parsed):
        raise ValueError(f"提取记录已漂移：{record.source_ref}")
    item = parsed[record.fragment_index]
    if not isinstance(item, Mapping):
        raise ValueError(f"提取记录不再是对象：{record.source_ref}")
    identity = str(item.get("id") or f"item-{record.fragment_index + 1}")
    if record.fragment is not None and identity != record.fragment:
        raise ValueError(f"提取记录身份已漂移：{record.source_ref}")
    text = json.dumps(item, ensure_ascii=False, indent=2)
    return f"```json\n{text}\n```"


def run_migration(
    manifest_path: str | Path,
    *,
    runtime: MaterialWikiRuntime,
    dry_run: bool,
    receipt_path: str | Path | None = None,
    inventory_path: str | Path | None = None,
) -> dict[str, Any]:
    manifest = load_migration_manifest(manifest_path)
    if manifest.superseded_by:
        raise ValueError(
            f"迁移批次 {manifest.batch_id} 已弃用并由 {manifest.superseded_by} 取代，不允许再次执行"
        )
    journal_path = (
        Path(f"{receipt_path}.journal.jsonl")
        if receipt_path is not None and not dry_run
        else None
    )
    inventory_audit = None
    if inventory_path is not None:
        inventory_audit = audit_inventory(
            manifest_path,
            inventory_path,
            omnicompany_root=runtime.omnicompany_root,
        )
        if not inventory_audit["ok"]:
            raise ValueError("inventory 审计失败：" + "；".join(inventory_audit["errors"]))
    results: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    for record, source_path in _migration_records(
        manifest,
        omnicompany_root=runtime.omnicompany_root,
    ):
        try:
            if not record.placement_path or not record.proposed_status or not record.status_evidence:
                raise ValueError("migrate 规则必须提供 placement_path/status/status_evidence")
            content = _record_body(record, source_path)
            key = f"migration:{manifest.batch_id}:{record.sha256}"
            service = runtime.service(record.project_id)
            receipt = service.create(
                title=record.proposed_title or _title(source_path),
                placement_path=record.placement_path,
                body=content,
                status=record.proposed_status,
                sources=(
                    MaterialSourceRef(
                        "migration_source",
                        record.source_ref,
                        record.status_evidence,
                    ),
                ),
                idempotency_key=key,
                material_id=record.material_id,
                dry_run=dry_run,
            )
            item = asdict(receipt)
            created_by_batch = not receipt.idempotent_replay
            if receipt.idempotent_replay and not dry_run:
                document = service.get(record.material_id or receipt.material_id)
                created_by_batch = any(
                    operation.key == key and operation.operation == "create"
                    for operation in document.operations
                )
            item.update(
                source_ref=record.source_ref,
                source_sha256=record.sha256,
                status_evidence=record.status_evidence,
                batch_operation_key=key,
                created_by_batch=created_by_batch,
            )
            results.append(item)
            if journal_path is not None:
                _append_jsonl(
                    journal_path,
                    {"batch_id": manifest.batch_id, "event": "result", "item": item},
                )
        except Exception as exc:  # noqa: BLE001 - full batch conflict report
            conflict = {"source_ref": record.source_ref, "error": str(exc)}
            conflicts.append(conflict)
            if journal_path is not None:
                _append_jsonl(
                    journal_path,
                    {"batch_id": manifest.batch_id, "event": "conflict", "item": conflict},
                )
    payload = {
        "schema_version": 1,
        "batch_id": manifest.batch_id,
        "dry_run": dry_run,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inventory_audit": inventory_audit,
        "journal_path": str(journal_path) if journal_path is not None else None,
        "summary": {
            "candidates": len(results) + len(conflicts),
            "succeeded": len(results),
            "conflicts": len(conflicts),
            "new_entries": sum(
                1 for item in results if not item["idempotent_replay"] and not item["dry_run"]
            ),
            "batch_entries": sum(
                1 for item in results if item["created_by_batch"] and not item["dry_run"]
            ),
        },
        "results": results,
        "conflicts": conflicts,
    }
    if receipt_path is not None:
        _atomic_json(Path(receipt_path), payload)
    return payload


def recover_receipt_from_journal(journal_path: str | Path) -> dict[str, Any]:
    """Recover the latest result for each material from an interrupted journal."""
    batch_id: str | None = None
    results: dict[str, dict[str, Any]] = {}
    conflicts: dict[str, dict[str, str]] = {}
    for line in Path(journal_path).read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        batch_id = str(event.get("batch_id") or batch_id or "")
        item = event.get("item") or {}
        if event.get("event") == "result" and item.get("material_id"):
            results[str(item["material_id"])] = dict(item)
        elif event.get("event") == "conflict" and item.get("source_ref"):
            conflicts[str(item["source_ref"])] = dict(item)
    result_values = list(results.values())
    conflict_values = list(conflicts.values())
    return {
        "schema_version": 1,
        "batch_id": batch_id,
        "recovered_from_journal": str(journal_path),
        "summary": {
            "candidates": len(result_values) + len(conflict_values),
            "succeeded": len(result_values),
            "conflicts": len(conflict_values),
            "batch_entries": sum(
                1 for item in result_values if item.get("created_by_batch")
            ),
        },
        "results": result_values,
        "conflicts": conflict_values,
    }


def rollback_migration(
    receipt_path: str | Path,
    *,
    runtime: MaterialWikiRuntime,
    dry_run: bool = False,
) -> dict[str, Any]:
    receipt_file = Path(receipt_path)
    if receipt_file.name.endswith(".journal.jsonl"):
        receipt = recover_receipt_from_journal(receipt_file)
    else:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for item in receipt.get("results") or ():
        material_id = str(item.get("material_id") or "")
        if not item.get("created_by_batch") or item.get("dry_run"):
            skipped.append({"material_id": material_id, "reason": "不是本批新建的本体"})
            continue
        project_id = material_id.split(".material.", 1)[0]
        try:
            service = runtime.service(project_id)
            document = service.get(material_id)
            version = str(item.get("version") or document.head_version)
            result = service.reject(
                material_id,
                target_version=version,
                expected_version=document.head_version,
                sources=(MaterialSourceRef("migration_rollback", f"batch:{receipt['batch_id']}"),),
                idempotency_key=f"migration-rollback:{receipt['batch_id']}:{material_id}:{version}",
                dry_run=dry_run,
            )
            results.append(asdict(result))
        except MaterialEntryNotFoundError:
            skipped.append({"material_id": material_id, "reason": "本体已不存在"})
        except Exception as exc:  # noqa: BLE001 - continue producing complete rollback receipt
            errors.append({"material_id": material_id, "error": str(exc)})
    return {
        "batch_id": receipt.get("batch_id"),
        "dry_run": dry_run,
        "results": results,
        "skipped": skipped,
        "errors": errors,
    }


__all__ = [
    "audit_inventory",
    "MigrationManifest",
    "InventoryRecord",
    "build_inventory",
    "inventory_payload",
    "load_migration_manifest",
    "recover_receipt_from_journal",
    "rollback_migration",
    "run_migration",
    "write_deprecation_ledger",
    "write_inventory",
]
