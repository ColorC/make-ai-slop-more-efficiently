# [OMNI] origin=codex domain=services/_core/material_wiki ts=2026-08-02T00:00:00+08:00 type=router status=active
# [OMNI] summary="材料 Wiki 的 Markdown 本体适配器；一个稳定条目原子保存全部版本、关系、来源和操作回执，不建立第二份当前内容"
# [OMNI] why="Wiki 正文必须先成功落盘，随后投影才能更新；单文件本体允许跨 Windows/POSIX 原子替换，并用版本前置条件防止并发静默覆盖"
# [OMNI] tags=material-wiki,markdown,canonical,atomic,concurrency
# [OMNI] material_id="material:core.material_wiki.adapters.markdown.py"
"""Atomic Markdown storage for canonical Material Wiki entries."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable, Iterator, Mapping

import yaml

from ..schema import MaterialRelations, MaterialSourceRef, MaterialVersionRef


_VERSION_START = "<!-- material-wiki-version-start:{version} -->"
_VERSION_END = "<!-- material-wiki-version-end:{version} -->"
_VERSION_BLOCK = re.compile(
    r"<!-- material-wiki-version-start:(?P<version>[^ ]+) -->\n"
    r"(?P<body>.*?)\n<!-- material-wiki-version-end:(?P=version) -->",
    re.DOTALL,
)
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class MaterialWikiStorageError(RuntimeError):
    """Canonical body storage could not complete safely."""


class MaterialEntryNotFoundError(MaterialWikiStorageError):
    """No canonical entry exists for the requested material id."""


class MaterialEntryExistsError(MaterialWikiStorageError):
    """The target material id or canonical path already exists."""


class MaterialVersionConflictError(MaterialWikiStorageError):
    """The caller edited an older head and must reload before retrying."""


class MaterialIdempotencyConflictError(MaterialWikiStorageError):
    """An idempotency key was reused for a different mutation."""


@dataclass(frozen=True, slots=True)
class MaterialAliasResolution:
    requested_material_id: str
    target_material_id: str
    anchor: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class WikiVersion:
    version: str
    status: str
    body: str
    sources: tuple[MaterialSourceRef, ...]
    relations: MaterialRelations = field(default_factory=MaterialRelations)
    author_session: str | None = None
    created_at: str = ""
    prior_status: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", self.body.rstrip())
        object.__setattr__(self, "sources", tuple(self.sources))


@dataclass(frozen=True, slots=True)
class WikiOperation:
    key: str
    operation: str
    version: str
    status: str
    current_version: str | None
    created_at: str
    sources: tuple[MaterialSourceRef, ...] = ()
    author_session: str | None = None


@dataclass(frozen=True, slots=True)
class WikiDocument:
    material_id: str
    project_id: str
    profile_id: str
    title: str
    canonical_ref: str
    placement_path: tuple[str, ...]
    head_version: str
    current_version: str | None
    versions: tuple[WikiVersion, ...]
    operations: tuple[WikiOperation, ...] = ()
    schema_version: int = 1

    def version(self, version: str) -> WikiVersion:
        for item in self.versions:
            if item.version == version:
                return item
        raise MaterialEntryNotFoundError(
            f"{self.material_id} 不存在版本 {version}"
        )

    @property
    def head(self) -> WikiVersion:
        return self.version(self.head_version)

    @property
    def current(self) -> WikiVersion | None:
        return self.version(self.current_version) if self.current_version else None


@dataclass(frozen=True, slots=True)
class AdapterMutationResult:
    document: WikiDocument
    operation: WikiOperation
    idempotent_replay: bool
    dry_run: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_to_mapping(source: MaterialSourceRef) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": source.kind, "ref": source.ref}
    if source.note:
        result["note"] = source.note
    return result


def _version_ref_to_mapping(ref: MaterialVersionRef) -> dict[str, str]:
    return {"material_id": ref.material_id, "version": ref.version}


def _relations_to_mapping(relations: MaterialRelations) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if relations.parent_version:
        result["parent_version"] = _version_ref_to_mapping(relations.parent_version)
    for key in ("supersedes", "derived_from", "related"):
        values = getattr(relations, key)
        if values:
            result[key] = [_version_ref_to_mapping(item) for item in values]
    return result


def _version_to_mapping(version: WikiVersion) -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": version.version,
        "status": version.status,
        "created_at": version.created_at,
        "sources": [_source_to_mapping(item) for item in version.sources],
    }
    relations = _relations_to_mapping(version.relations)
    if relations:
        result["relations"] = relations
    if version.author_session:
        result["author_session"] = version.author_session
    if version.prior_status:
        result["prior_status"] = version.prior_status
    return result


def _operation_to_mapping(operation: WikiOperation) -> dict[str, Any]:
    result = {
        "key": operation.key,
        "operation": operation.operation,
        "version": operation.version,
        "status": operation.status,
        "current_version": operation.current_version,
        "created_at": operation.created_at,
        "sources": [_source_to_mapping(item) for item in operation.sources],
    }
    if operation.author_session:
        result["author_session"] = operation.author_session
    return result


def _frontmatter(document: WikiDocument) -> dict[str, Any]:
    return {
        "material_wiki": {
            "schema_version": document.schema_version,
            "material_id": document.material_id,
            "project_id": document.project_id,
            "profile_id": document.profile_id,
            "title": document.title,
            "canonical_ref": document.canonical_ref,
            "placement_path": list(document.placement_path),
            "head_version": document.head_version,
            "current_version": document.current_version,
            "versions": [_version_to_mapping(item) for item in document.versions],
            "operations": [_operation_to_mapping(item) for item in document.operations],
        }
    }


def render_document(document: WikiDocument) -> str:
    header = yaml.safe_dump(
        _frontmatter(document),
        allow_unicode=True,
        sort_keys=False,
        width=1000,
    ).strip()
    sections = ["---", header, "---", "", f"# {document.title}", ""]
    for version in document.versions:
        sections.extend(
            [
                _VERSION_START.format(version=version.version),
                f"## {version.version} · {version.status}",
                "",
                version.body.rstrip(),
                _VERSION_END.format(version=version.version),
                "",
            ]
        )
    return "\n".join(sections).rstrip() + "\n"


def _parse_frontmatter(text: str, path: Path) -> tuple[Mapping[str, Any], str]:
    if not text.startswith("---\n"):
        raise MaterialWikiStorageError(f"{path} 缺少 Material Wiki frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise MaterialWikiStorageError(f"{path} 的 frontmatter 未闭合")
    raw = yaml.safe_load(text[4:end]) or {}
    if not isinstance(raw, Mapping) or not isinstance(raw.get("material_wiki"), Mapping):
        raise MaterialWikiStorageError(f"{path} 不是 Material Wiki 本体条目")
    return raw["material_wiki"], text[end + 5 :]


def parse_document(text: str, path: Path) -> WikiDocument:
    raw, markdown = _parse_frontmatter(text, path)
    bodies = {match.group("version"): match.group("body").split("\n", 2)[-1].rstrip() for match in _VERSION_BLOCK.finditer(markdown)}
    versions: list[WikiVersion] = []
    for item in raw.get("versions") or ():
        version_id = str(item.get("version") or "")
        if version_id not in bodies:
            raise MaterialWikiStorageError(f"{path} 缺少版本正文 {version_id}")
        body = bodies[version_id]
        # 正文摘要不再是版本身份，也不再当闸门（业主 2026-09-18：「如果要控制版本，请用 git」）。
        # Material Core README 早已规定优先用设施原生 revision（Git commit / P4 CL / Yjs sv），
        # digest 只是无版本设施时的降级。这里曾把降级手段焊成硬校验，正文一被直接编辑就整篇
        # 静默落选（上层 _iter_documents 的 except-continue 连报错都吞掉），而 git 里明明有全部
        # 历史、摘要却什么都找不回来。存量文件里残留的 body_sha256 字段读到即忽略。
        versions.append(
            WikiVersion(
                version=version_id,
                status=str(item.get("status") or ""),
                body=body,
                sources=tuple(MaterialSourceRef.from_mapping(value) for value in item.get("sources") or ()),
                relations=MaterialRelations.from_mapping(item.get("relations")),
                author_session=item.get("author_session"),
                created_at=str(item.get("created_at") or ""),
                prior_status=item.get("prior_status"),
            )
        )
    operations = tuple(
        WikiOperation(
            key=str(item.get("key") or ""),
            operation=str(item.get("operation") or ""),
            version=str(item.get("version") or ""),
            status=str(item.get("status") or ""),
            current_version=item.get("current_version"),
            created_at=str(item.get("created_at") or ""),
            sources=tuple(
                MaterialSourceRef.from_mapping(value)
                for value in item.get("sources") or ()
            ),
            author_session=item.get("author_session"),
        )
        for item in raw.get("operations") or ()
    )
    document = WikiDocument(
        schema_version=int(raw.get("schema_version", 0)),
        material_id=str(raw.get("material_id") or ""),
        project_id=str(raw.get("project_id") or ""),
        profile_id=str(raw.get("profile_id") or ""),
        title=str(raw.get("title") or ""),
        canonical_ref=str(raw.get("canonical_ref") or ""),
        placement_path=tuple(raw.get("placement_path") or ()),
        head_version=str(raw.get("head_version") or ""),
        current_version=raw.get("current_version"),
        versions=tuple(versions),
        operations=operations,
    )
    document.head
    if document.current_version:
        document.current
    return document


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    key = str(path.resolve()).casefold()
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata_ignore = path.parent.parent / ".gitignore"
    try:
        with metadata_ignore.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write("locks/\n")
    except FileExistsError:
        lines = metadata_ignore.read_text(encoding="utf-8").splitlines()
        if "locks/" not in lines:
            with metadata_ignore.open("a", encoding="utf-8", newline="\n") as handle:
                if lines:
                    handle.write("\n")
                handle.write("locks/\n")
    with thread_lock, path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
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


class MarkdownWikiAdapter:
    """Read and atomically mutate Markdown entries below one canonical Wiki root."""

    def __init__(self, wiki_root: str | Path, *, canonical_prefix: str) -> None:
        self.wiki_root = Path(wiki_root).resolve()
        self.canonical_prefix = canonical_prefix.rstrip("/")

    def canonical_path(self, material_id: str, placement_path: tuple[str, ...]) -> Path:
        leaf = material_id.rsplit(".", 1)[-1]
        return self.wiki_root.joinpath(*placement_path, f"{leaf}.md")

    def canonical_ref(self, material_id: str, placement_path: tuple[str, ...]) -> str:
        path = self.canonical_path(material_id, placement_path)
        relative = path.relative_to(self.wiki_root).as_posix()
        return f"{self.canonical_prefix}/{relative}"

    def _lock_path(self, material_id: str) -> Path:
        digest = hashlib.sha256(material_id.encode("utf-8")).hexdigest()
        return self.wiki_root / ".material-wiki" / "locks" / f"{digest}.lock"

    def _iter_documents(self) -> Iterator[tuple[Path, WikiDocument]]:
        if not self.wiki_root.exists():
            return
        for path in self.wiki_root.rglob("*.md"):
            if ".material-wiki" in path.parts:
                continue
            try:
                yield path, parse_document(path.read_text(encoding="utf-8"), path)
            except MaterialWikiStorageError:
                continue

    def iter_documents(self) -> Iterator[tuple[Path, WikiDocument]]:
        """Yield canonical entries for browsing and rebuilds without exposing lock files."""
        yield from self._iter_documents()

    def _alias_resolution(self, material_id: str) -> MaterialAliasResolution | None:
        path = self.wiki_root / ".material-wiki" / "aliases.yaml"
        if not path.is_file():
            return None
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise MaterialWikiStorageError(f"材料别名表不可读：{path}") from exc
        aliases = raw.get("aliases") if isinstance(raw, Mapping) else None
        value = aliases.get(material_id) if isinstance(aliases, Mapping) else None
        if isinstance(value, str):
            target = value
            anchor = None
            reason = None
        elif isinstance(value, Mapping):
            target = str(value.get("target_material_id") or "").strip()
            anchor = str(value.get("anchor") or "").strip() or None
            reason = str(value.get("reason") or "").strip() or None
        else:
            return None
        if not target or target == material_id:
            raise MaterialWikiStorageError(f"材料别名 {material_id} 的目标无效")
        return MaterialAliasResolution(material_id, target, anchor, reason)

    def locate_with_resolution(
        self,
        material_id: str,
    ) -> tuple[Path, WikiDocument, MaterialAliasResolution | None]:
        for path, document in self._iter_documents():
            if document.material_id == material_id:
                return path, document, None
        resolution = self._alias_resolution(material_id)
        if resolution:
            second_resolution = self._alias_resolution(resolution.target_material_id)
            if second_resolution:
                raise MaterialWikiStorageError(
                    f"材料别名不允许形成链：{material_id} -> {resolution.target_material_id}"
                )
            for path, document in self._iter_documents():
                if document.material_id == resolution.target_material_id:
                    return path, document, resolution
            raise MaterialEntryNotFoundError(
                f"材料别名 {material_id} 指向不存在的本体 {resolution.target_material_id}"
            )
        raise MaterialEntryNotFoundError(f"找不到材料本体 {material_id}")

    def locate(self, material_id: str) -> tuple[Path, WikiDocument]:
        path, document, _resolution = self.locate_with_resolution(material_id)
        return path, document

    def load(self, material_id: str) -> WikiDocument:
        return self.locate(material_id)[1]

    @staticmethod
    def _replay(document: WikiDocument, key: str, operation: str) -> WikiOperation | None:
        for item in document.operations:
            if item.key != key:
                continue
            if item.operation != operation:
                raise MaterialIdempotencyConflictError(
                    f"幂等键 {key} 已用于 {item.operation}，不能复用于 {operation}"
                )
            return item
        return None

    def find_operation(self, key: str, operation: str) -> tuple[WikiDocument, WikiOperation] | None:
        for _, document in self._iter_documents():
            item = self._replay(document, key, operation)
            if item:
                return document, item
        return None

    def create(
        self,
        document: WikiDocument,
        *,
        idempotency_key: str,
        dry_run: bool = False,
    ) -> AdapterMutationResult:
        replay = self.find_operation(idempotency_key, "create")
        if replay:
            existing, operation = replay
            return AdapterMutationResult(existing, operation, True, dry_run)
        path = self.canonical_path(document.material_id, document.placement_path)
        operation = WikiOperation(
            key=idempotency_key,
            operation="create",
            version=document.head_version,
            status=document.head.status,
            current_version=document.current_version,
            created_at=_now(),
            sources=document.head.sources,
            author_session=document.head.author_session,
        )
        result_document = replace(document, operations=(operation,))
        if dry_run:
            if path.exists():
                raise MaterialEntryExistsError(f"目标本体路径已存在：{path}")
            return AdapterMutationResult(result_document, operation, False, True)
        with _file_lock(self._lock_path(document.material_id)):
            replay = self.find_operation(idempotency_key, "create")
            if replay:
                existing, replay_operation = replay
                return AdapterMutationResult(existing, replay_operation, True, False)
            if path.exists():
                raise MaterialEntryExistsError(f"目标本体路径已存在：{path}")
            _atomic_write(path, render_document(result_document))
        return AdapterMutationResult(result_document, operation, False, False)

    def mutate(
        self,
        material_id: str,
        *,
        operation_name: str,
        idempotency_key: str,
        expected_version: str,
        operation_sources: tuple[MaterialSourceRef, ...],
        author_session: str,
        mutation: Callable[[WikiDocument], tuple[WikiDocument, str, str]],
        dry_run: bool = False,
    ) -> AdapterMutationResult:
        path, initial, resolution = self.locate_with_resolution(material_id)
        if resolution:
            raise MaterialWikiStorageError(
                f"旧材料标识 {material_id} 仅供读取；请使用 {resolution.target_material_id} 修订"
            )
        replay = self._replay(initial, idempotency_key, operation_name)
        if replay:
            return AdapterMutationResult(initial, replay, True, dry_run)
        if initial.head_version != expected_version:
            raise MaterialVersionConflictError(
                f"{material_id} 当前 head={initial.head_version}，提交基于 {expected_version}"
            )
        updated, version, status = mutation(initial)
        operation = WikiOperation(
            key=idempotency_key,
            operation=operation_name,
            version=version,
            status=status,
            current_version=updated.current_version,
            created_at=_now(),
            sources=operation_sources,
            author_session=author_session,
        )
        updated = replace(updated, operations=(*updated.operations, operation))
        if dry_run:
            return AdapterMutationResult(updated, operation, False, True)
        with _file_lock(self._lock_path(material_id)):
            current = parse_document(path.read_text(encoding="utf-8"), path)
            replay = self._replay(current, idempotency_key, operation_name)
            if replay:
                return AdapterMutationResult(current, replay, True, False)
            if current.head_version != expected_version:
                raise MaterialVersionConflictError(
                    f"{material_id} 当前 head={current.head_version}，提交基于 {expected_version}"
                )
            updated, version, status = mutation(current)
            operation = WikiOperation(
                key=idempotency_key,
                operation=operation_name,
                version=version,
                status=status,
                current_version=updated.current_version,
                created_at=_now(),
                sources=operation_sources,
                author_session=author_session,
            )
            updated = replace(updated, operations=(*updated.operations, operation))
            _atomic_write(path, render_document(updated))
        return AdapterMutationResult(updated, operation, False, False)
