# [OMNI] origin=ai-ide domain=services/_core/registry ts=2026-05-03T01:00:00Z type=material status=active agent=ai-ide
# [OMNI] summary="MaterialIdIndex - 从所有文件 OmniMark 头里 material_id 字段同步出来的扁平索引 (单 JSON 落盘). 跟 InstanceRegistry 6 种 AST 实体并存, 不替代"
# [OMNI] why="J 管线给 846 文件批量写了 material_id 头; 消费方 (G4 锚 / guardian / lap_auditor) 要按 material_id 查 file_path / kind 时需统一索引. AST 注册不覆盖 .md/.yaml/prompt 等带 material_id 的非代码文件"
# [OMNI] tags=registry,material_id,index,omnimark,P0
# [OMNI] material_id="material:core.registry.material_id_index.flat_index.py"
"""Material ID Index — 从 OmniMark headers 同步的扁平索引.

数据源: 文件 OmniMark 头里的 `material_id="..."` 字段 (规范 omni-header.md).
存储: <registry_dir>/material_id_index.json (单文件, 扁平 dict).
键: material_id 值 (例 "material:diagnosis.foo.bar.implementation.py").
值: file_path + kind + origin/ts/agent/domain + summary/why/tags.

跟 InstanceRegistry 关系:
  - InstanceRegistry: 6 种实体 AST 扫描 (代码即注册: format/router/agent_loop/...)
  - MaterialIdIndex: 文件 OmniMark 头扫描 (扩展到 .md/.yaml 等非代码文件)
  - 两个并存, 索引同一文件可在两边都有记录.

职责:
  - rebuild_from_headers(scopes, project_root): 走 scopes 全文件树, 解析头, 落 index
  - refresh_incremental(scopes, project_root): 增量刷新(只重扫水位线之后变更的文件)
  - lookup(material_id): 单条查
  - reverse_lookup(file_path): 反查 material_id
  - list_all(): 全量
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# 扫哪些扩展名 (跟 OmniMark 注释支持的语法一致: # 行或 <!-- --> 行)
_OMNIMARK_FILE_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".toml"}

# 跳目录跟 scanner.py 同步
_SKIP_DIRS = {
    "__pycache__", ".venv", "venv", "node_modules",
    "_archive", "_graveyard", ".git", "dist", "build",
    ".omni",  # 运行态/沙箱附属目录, 里面的 material_id 字段是数据不是自述头(2026-07-02 验收缺陷2)
}


def _clean_material_id(raw: str) -> str:
    """归一 material_id 值: 去首尾空白与包裹引号(源文件把值连引号写进头时的防御,
    2026-07-02 验收发现 1/1924 条带字面引号的脏条目)。"""
    s = (raw or "").strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in ("\"", "'"):
        s = s[1:-1].strip()
    return s


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dir_latest_mtime(scopes: list[Path]) -> float:
    """扫 scopes 内所有受支持文件, 取最新 mtime(增量刷新的水位线基准)。"""
    latest = 0.0
    for scope in scopes:
        scope = Path(scope)
        if not scope.exists():
            continue
        for p in scope.rglob("*"):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if p.suffix.lower() not in _OMNIMARK_FILE_SUFFIXES:
                continue
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if mt > latest:
                latest = mt
    return latest


@dataclass
class MaterialIdEntry:
    """单条 material_id 记录, 对应一个文件."""

    material_id: str
    file_path: str            # 相对项目根 (用 / 分隔)
    kind: str = ""            # OmniMark `type=` 字段 (router/agent/material/team/data/plan/...)
    origin: str = ""
    ts: str = ""
    agent: str = ""
    domain: str = ""
    summary: str = ""
    why: str = ""
    tags: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tags"] = list(self.tags)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MaterialIdEntry":
        d = dict(d)
        d["tags"] = tuple(d.get("tags") or ())
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


class MaterialIdIndex:
    """OmniMark `material_id` 索引 (单文件 JSON 落盘)."""

    def __init__(self, index_path: Path) -> None:
        self.index_path = Path(index_path)
        self._cache: Optional[dict[str, MaterialIdEntry]] = None
        self._meta: dict = {}

    # ── 读 ──────────────────────────────────────────────
    def _load(self) -> None:
        if self._cache is not None:
            return
        if not self.index_path.exists():
            self._cache = {}
            self._meta = {}
            return
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            self._cache = {}
            self._meta = {}
            return
        if not isinstance(data, dict):
            self._cache = {}
            self._meta = {}
            return
        entries_raw = data.get("entries", {}) or {}
        self._cache = {
            mid: MaterialIdEntry.from_dict(d)
            for mid, d in entries_raw.items() if isinstance(d, dict)
        }
        self._meta = data.get("meta", {}) or {}

    def lookup(self, material_id: str) -> Optional[MaterialIdEntry]:
        self._load()
        assert self._cache is not None
        return self._cache.get(material_id)

    def reverse_lookup(self, file_path: str) -> Optional[str]:
        self._load()
        assert self._cache is not None
        norm = Path(file_path).as_posix()
        for mid, entry in self._cache.items():
            if Path(entry.file_path).as_posix() == norm:
                return mid
        return None

    def list_all(self) -> list[MaterialIdEntry]:
        self._load()
        assert self._cache is not None
        return list(self._cache.values())

    def count(self) -> int:
        self._load()
        assert self._cache is not None
        return len(self._cache)

    @property
    def meta(self) -> dict:
        self._load()
        return dict(self._meta)

    # ── 写 ──────────────────────────────────────────────
    def rebuild_from_headers(
        self,
        scopes: list[Path],
        project_root: Path,
    ) -> dict:
        """走 scopes 内全文件树, 解析 OmniMark, 重建 index.

        Args:
          scopes: 要扫的根目录列表 (例 src/omnicompany, templates, docs)
          project_root: 算 file_path 的相对参考根 (一般是 omnicompany 项目根)

        Returns:
          {total_scanned, total_with_header, total_with_material_id,
           entries_written, conflicts: [{material_id, files}]}
        """
        # 延迟 import 避免循环
        from omnicompany.core.omnimark import parse_omnimark

        new_cache: dict[str, MaterialIdEntry] = {}
        # material_id → [file_path1, file_path2, ...] (同 id 多文件)
        seen_files: dict[str, list[str]] = {}
        total_scanned = 0
        total_with_header = 0
        total_with_material_id = 0

        for scope in scopes:
            scope = Path(scope)
            if not scope.exists():
                continue
            for p in scope.rglob("*"):
                if not p.is_file():
                    continue
                if any(part in _SKIP_DIRS for part in p.parts):
                    continue
                if p.suffix.lower() not in _OMNIMARK_FILE_SUFFIXES:
                    continue
                total_scanned += 1
                try:
                    fields = parse_omnimark(p)
                except Exception:
                    continue
                if fields is None:
                    continue
                total_with_header += 1
                material_id = _clean_material_id((fields.extra or {}).get("material_id", ""))
                if not material_id:
                    continue
                total_with_material_id += 1

                try:
                    rel = p.relative_to(project_root).as_posix()
                except ValueError:
                    rel = p.as_posix()

                # 冲突检测: 同 material_id 多文件 (理论上不该有, 但做检测告警)
                if material_id in seen_files:
                    seen_files[material_id].append(rel)
                    continue  # 保留首次出现的, 后续同 id 跳过

                seen_files[material_id] = [rel]
                new_cache[material_id] = MaterialIdEntry(
                    material_id=material_id,
                    file_path=rel,
                    kind=fields.type or "",
                    origin=fields.origin or "",
                    ts=fields.ts or "",
                    agent=fields.agent or "",
                    domain=fields.domain or "",
                    summary=fields.summary or "",
                    why=fields.why or "",
                    tags=tuple(fields.tags or ()),
                )

        conflicts = [
            {"material_id": mid, "files": files}
            for mid, files in seen_files.items() if len(files) > 1
        ]

        self._cache = new_cache
        self._meta = {
            "rebuilt_at": _now_iso(),
            "rebuilt_mtime": _dir_latest_mtime(scopes),
            "scopes": [str(s) for s in scopes],
            "project_root": str(project_root),
            "total_scanned": total_scanned,
            "total_with_header": total_with_header,
            "total_with_material_id": total_with_material_id,
        }
        self._save()

        return {
            "total_scanned": total_scanned,
            "total_with_header": total_with_header,
            "total_with_material_id": total_with_material_id,
            "entries_written": len(new_cache),
            "conflicts": conflicts,
        }

    def refresh_incremental(
        self,
        scopes: list[Path],
        project_root: Path,
    ) -> dict:
        """增量刷新: 只重扫比索引 rebuilt_mtime 新的文件, 已治理未变更的不碰。

        语义 (对齐目标架构 3.4 增量巡检"只扫水位线之后的变更"):
          - 无既有索引 → 退化为全量 rebuild_from_headers。
          - 有索引 → 以上次 meta.rebuilt_mtime 为水位, 只解析 mtime > 水位 的文件,
            把新增/变更的 material_id 覆盖进已有 cache, 未变更的原样保留。
          - 不做删除检测(文件删了/头去了 material_id 的陈旧条目留待下次全量清), 保守。

        Returns: {mode, total_rescanned, entries_updated, entries_total, conflicts}
        """
        self._load()
        assert self._cache is not None
        watermark = float(self._meta.get("rebuilt_mtime") or 0.0)
        if not self.index_path.exists() or watermark <= 0.0 or not self._cache:
            full = self.rebuild_from_headers(scopes, project_root)
            return {
                "mode": "full",
                "total_rescanned": full["total_scanned"],
                "entries_updated": full["entries_written"],
                "entries_total": full["entries_written"],
                "conflicts": full["conflicts"],
            }

        from omnicompany.core.omnimark import parse_omnimark

        # file_path(相对) → material_id, 用于变更时先摘掉旧 id 再写新 id
        path_to_mid: dict[str, str] = {
            e.file_path: mid for mid, e in self._cache.items()
        }
        total_rescanned = 0
        updated = 0
        conflicts_seen: dict[str, list[str]] = {}

        for scope in scopes:
            scope = Path(scope)
            if not scope.exists():
                continue
            for p in scope.rglob("*"):
                if not p.is_file():
                    continue
                if any(part in _SKIP_DIRS for part in p.parts):
                    continue
                if p.suffix.lower() not in _OMNIMARK_FILE_SUFFIXES:
                    continue
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                if mtime <= watermark:
                    continue  # 水位线之前, 已治理未变更, 不碰
                total_rescanned += 1
                try:
                    rel = p.relative_to(project_root).as_posix()
                except ValueError:
                    rel = p.as_posix()
                try:
                    fields = parse_omnimark(p)
                except Exception:
                    continue
                if fields is None:
                    continue
                material_id = _clean_material_id((fields.extra or {}).get("material_id", ""))
                if not material_id:
                    continue
                # 该文件之前登记过别的 material_id(改了头) → 摘掉旧条目
                old_mid = path_to_mid.get(rel)
                if old_mid and old_mid != material_id:
                    self._cache.pop(old_mid, None)
                # material_id 冲突: 已被别的文件占用
                existing = self._cache.get(material_id)
                if existing is not None and existing.file_path != rel:
                    conflicts_seen.setdefault(material_id, [existing.file_path]).append(rel)
                    continue
                self._cache[material_id] = MaterialIdEntry(
                    material_id=material_id,
                    file_path=rel,
                    kind=fields.type or "",
                    origin=fields.origin or "",
                    ts=fields.ts or "",
                    agent=fields.agent or "",
                    domain=fields.domain or "",
                    summary=fields.summary or "",
                    why=fields.why or "",
                    tags=tuple(fields.tags or ()),
                )
                path_to_mid[rel] = material_id
                updated += 1

        self._meta = dict(self._meta)
        self._meta["refreshed_at"] = _now_iso()
        self._meta["rebuilt_mtime"] = max(watermark, _dir_latest_mtime(scopes))
        self._save()
        conflicts = [{"material_id": m, "files": fs} for m, fs in conflicts_seen.items()]
        return {
            "mode": "incremental",
            "total_rescanned": total_rescanned,
            "entries_updated": updated,
            "entries_total": len(self._cache),
            "conflicts": conflicts,
        }

    def _save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        assert self._cache is not None
        data = {
            "meta": self._meta,
            "entries": {
                mid: entry.to_dict()
                for mid, entry in sorted(self._cache.items())
            },
        }
        self.index_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def get_material_id_index(registry_dir: Optional[Path] = None) -> MaterialIdIndex:
    """获取 MaterialIdIndex 实例 (默认放 _DEFAULT_REGISTRY_DIR/material_id_index.json)."""
    from . import _DEFAULT_REGISTRY_DIR
    base = Path(registry_dir) if registry_dir else _DEFAULT_REGISTRY_DIR
    return MaterialIdIndex(base / "material_id_index.json")
