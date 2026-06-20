# [OMNI] origin=claude-code domain=services/registry ts=2026-04-11T00:00:00Z
# [OMNI] material_id="material:core.registry.instance_storage.json_persister.py"
"""
InstanceRegistry — 实例注册表

存储格式：明文 JSON，每个实体一个文件，位于 data/registry/{type}/{entity_safe_id}.json
文件路径可被 git 追踪，diff 清晰可读。

entity_id 格式：{type}:{package}.{name}
  示例：router:demogame.team_table.SchemaAssembler
        format:demogame.table_schema
        team:demogame.team_table

自由原则：
  - entity_id 不强制 package 深度
  - 不强制 name 格式（Router 用类名，Format 用 id，Pipeline 用 pipeline 名）
  - 边界情况（无 package 的顶层实体）允许省略 package 部分
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_filename(entity_id: str) -> str:
    """将 entity_id 转换为安全文件名（替换 : 和 / 为 _）。"""
    return re.sub(r"[:/\\]", "_", entity_id) + ".json"


@dataclass
class InstanceEntry:
    """一个已注册实体的实例记录。

    必填字段反映"稳定身份"判据；attrs 存放类型特定属性（自由扩展）。
    """

    entity_id: str
    """全局唯一标识，格式 {type}:{package}.{name}。"""

    type: str
    """实体类型（format / router / pipeline / agent_loop / tool / hook）。"""

    name: str
    """实体名称（Router 类名 / Format id / Pipeline 名称 等）。"""

    package: str
    """所属 package 点分路径（如 demogame.team_table），可为空字符串。"""

    source_file: str
    """定义文件的相对路径（相对于 source_root.parent，即 omnicompany 根目录）。"""

    scanned_at: str = field(default_factory=_now_iso)
    """最近一次扫描到该实体的时间（ISO 8601）。"""

    first_seen_at: str = field(default_factory=_now_iso)
    """首次注册时间（创建后不更新）。"""

    attrs: dict[str, Any] = field(default_factory=dict)
    """类型特定属性（FORMAT_IN/OUT、节点数、工具清单等），自由扩展。"""

    deps: list[str] = field(default_factory=list)
    """直接依赖的其他实体 entity_id 列表（FORMAT_IN → format:xxx 等）。"""

    def to_dict(self) -> dict:
        d = asdict(self)
        # 保证 entity_id 在顶层清晰可见（便于 git diff 一眼识别）
        return {"_entity_id": self.entity_id, "_type": self.type, **d}

    @classmethod
    def from_dict(cls, d: dict) -> "InstanceEntry":
        d = {k: v for k, v in d.items() if not k.startswith("_")}
        return cls(**d)


# 类型目录读取缓存(进程级, 跨 InstanceRegistry 实例共享 —— get_registry 每次新建实例)。
# 键 = (registry_dir, type_name); 失效信号 = (文件数, 最新 mtime)。看板高频读注册表时,
# 没变就只做 N 次 stat(便宜), 不再 read+json.loads 每个文件(原来每个请求全盘重读)。
_TYPE_CACHE: dict[tuple[str, str], tuple[tuple[int, float], list["InstanceEntry"]]] = {}


def _type_dir_token(type_dir: Path) -> tuple[int, float]:
    """目录指纹: (json 文件数, 最新 mtime)。增删改任一文件都会让它跳变。"""
    count = 0
    latest = 0.0
    for f in type_dir.glob("*.json"):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        count += 1
        if mtime > latest:
            latest = mtime
    return (count, latest)


class InstanceRegistry:
    """基于明文 JSON 文件的实例注册表。

    每个实体存储在 {registry_dir}/{type}/{safe_entity_id}.json。
    写操作是幂等的：重新扫描同一实体会更新文件（保留 first_seen_at）。
    """

    def __init__(self, registry_dir: Path) -> None:
        self.registry_dir = Path(registry_dir)

    def _entity_path(self, entity_id: str) -> Path:
        type_name = entity_id.split(":")[0]
        return self.registry_dir / type_name / _safe_filename(entity_id)

    # ── 写操作 ──────────────────────────────────────────────────────────────

    def write(self, entry: InstanceEntry) -> None:
        """写入或更新一个实体记录（幂等）。保留 first_seen_at。"""
        path = self._entity_path(entry.entity_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        # 保留 first_seen_at（如果已存在）
        existing = self.read(entry.entity_id)
        if existing is not None:
            entry.first_seen_at = existing.first_seen_at

        entry.scanned_at = _now_iso()
        path.write_text(
            json.dumps(entry.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def delete(self, entity_id: str) -> bool:
        """删除一个实体记录。返回是否实际删除了文件。"""
        path = self._entity_path(entity_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # ── 读操作 ──────────────────────────────────────────────────────────────

    def read(self, entity_id: str) -> Optional[InstanceEntry]:
        """读取一个实体记录，不存在则返回 None。"""
        path = self._entity_path(entity_id)
        if not path.exists():
            return None
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return InstanceEntry.from_dict(d)
        except Exception:
            return None

    def exists(self, entity_id: str) -> bool:
        return self._entity_path(entity_id).exists()

    # ── 列举 ────────────────────────────────────────────────────────────────

    def iter_type(self, type_name: str) -> Iterator[InstanceEntry]:
        """迭代某类型下的所有实体(进程级缓存, 目录指纹不变就不重读)。"""
        yield from self._cached_type_entries(type_name)

    def _cached_type_entries(self, type_name: str) -> list["InstanceEntry"]:
        type_dir = self.registry_dir / type_name
        if not type_dir.exists():
            return []
        token = _type_dir_token(type_dir)
        cache_key = (str(self.registry_dir), type_name)
        hit = _TYPE_CACHE.get(cache_key)
        if hit is not None and hit[0] == token:
            return hit[1]
        entries: list[InstanceEntry] = []
        for f in sorted(type_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                entries.append(InstanceEntry.from_dict(d))
            except Exception:
                continue
        _TYPE_CACHE[cache_key] = (token, entries)
        return entries

    def list_all(self) -> list[InstanceEntry]:
        """列出所有已注册实体。"""
        result = []
        for type_dir in sorted(self.registry_dir.iterdir()):
            if not type_dir.is_dir() or type_dir.name.startswith("."):
                continue
            if type_dir.name == "health":
                continue
            for entry in self.iter_type(type_dir.name):
                result.append(entry)
        return result

    def list_by_type(self, type_name: str) -> list[InstanceEntry]:
        return list(self.iter_type(type_name))

    def list_by_package(self, package: str, type_name: str | None = None) -> list[InstanceEntry]:
        """列出属于某 package 的实体（支持前缀匹配）。"""
        all_entries = (
            list(self.iter_type(type_name))
            if type_name
            else self.list_all()
        )
        return [e for e in all_entries if e.package == package or e.package.startswith(package + ".")]

    def count(self, type_name: str | None = None) -> int:
        if type_name:
            return sum(1 for _ in self.iter_type(type_name))
        return sum(
            sum(1 for _ in self.iter_type(td.name))
            for td in self.registry_dir.iterdir()
            if td.is_dir() and not td.name.startswith(".") and td.name != "health"
        )

    # ── 依赖图辅助 ──────────────────────────────────────────────────────────

    def dependents_of(self, entity_id: str) -> list[InstanceEntry]:
        """找出所有依赖 entity_id 的实体（反向依赖查询）。"""
        return [e for e in self.list_all() if entity_id in e.deps]
