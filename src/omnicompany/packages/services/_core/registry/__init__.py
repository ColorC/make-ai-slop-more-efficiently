# [OMNI] origin=claude-code domain=services/registry ts=2026-04-11T00:00:00Z
# [OMNI] material_id="material:core.registry.package_aggregator.exports.py"
"""
omnicompany Registry — 注册体系

六元（Format / Router / Pipeline / AgentLoop / Tool / Hook）+ 开放可扩展的
MetaTypeRegistry，支持未来添加 Knowledge / Prompt / DataSource 等类型。

快速使用：
    from omnicompany.packages.services._core.registry import get_registry, scan, query

    reg = get_registry()          # 获取默认 InstanceRegistry（data/registry/）
    scan(reg)                     # 扫描 source_root，填充 registry
    result = query(reg).type("router").package("demogame.team_table").execute()
    for entry in result:
        print(entry.entity_id, entry.attrs.get("format_in"))
"""
from pathlib import Path

from .meta import EntityTypeDef, MetaTypeRegistry, meta_registry
from .instance import InstanceEntry, InstanceRegistry
from .scanner import scan_all, scan_file
from .query import RegistryQuery, QueryResult, query
from .archive import HealthSnapshot, HealthArchive, make_router_snapshot, make_format_snapshot
from .finding_archive import FindingArchive, get_finding_archive, git_head_short_hash
from .incremental import run_incremental_diagnosis, get_changed_files
from .material_index import MaterialIdIndex, MaterialIdEntry, get_material_id_index
from .resolver import (
    ResolveResult,
    ResolveError,
    UnifiedResolver,
    get_resolver,
    resolve_reference,
)

__all__ = [
    "EntityTypeDef",
    "MetaTypeRegistry",
    "meta_registry",
    "InstanceEntry",
    "InstanceRegistry",
    "scan_all",
    "scan_file",
    "RegistryQuery",
    "QueryResult",
    "query",
    "get_registry",
    "scan",
    "HealthSnapshot",
    "HealthArchive",
    "make_router_snapshot",
    "make_format_snapshot",
    "FindingArchive",
    "get_finding_archive",
    "git_head_short_hash",
    "run_incremental_diagnosis",
    "get_changed_files",
    "MaterialIdIndex",
    "MaterialIdEntry",
    "get_material_id_index",
    "ResolveResult",
    "ResolveError",
    "UnifiedResolver",
    "get_resolver",
    "resolve_reference",
]

# ── 默认路径 ─────────────────────────────────────────────────────────────────
_THIS_FILE = Path(__file__)
# 实际文件位置: src/omnicompany/packages/services/_core/registry/__init__.py
# (2026-07-04 修: 旧注释假设 services/registry/, 实为 services/_core/registry/, 多一层,
#  导致 parents[5] 算到 src/ 而非仓根, src/data/services/registry/ 由此长出一份错位存量。
#  见 docs/plans/omnicompany-governance/[2026-07-04]REGISTRY-STORAGE-SPLIT-REPAIR/plan.md)
# → omnicompany 包根 = parents[3]; 仓根("向上找 src/omnicompany + docs 并存"更抗目录挪动,
#   与 cli/commands/testmap.py::_project_root 同款写法, 兜底 parents[6])
_DEFAULT_SOURCE_ROOT = _THIS_FILE.parents[3]   # src/omnicompany  (registry/__init__.py → registry → _core → services → packages → omnicompany)


def _find_repo_root(start: Path) -> Path:
    """向上找仓根标志(src/omnicompany + docs 并存), 找不到则兜底固定层数。"""
    for p in (start, *start.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return start.parents[6]


# 2026-04-21 B4: data/registry/ → data/services/registry/ (对齐 archmap allowed_subdirs)
_DEFAULT_REGISTRY_DIR = _find_repo_root(_THIS_FILE) / "data" / "services" / "registry"


def get_registry(registry_dir: Path | str | None = None) -> InstanceRegistry:
    """获取 InstanceRegistry 实例（默认指向 data/services/registry/）。"""
    d = Path(registry_dir) if registry_dir else _DEFAULT_REGISTRY_DIR
    d.mkdir(parents=True, exist_ok=True)
    return InstanceRegistry(d)


def scan(
    registry: InstanceRegistry | None = None,
    source_root: Path | str | None = None,
) -> dict[str, int]:
    """扫描 source_root，更新 registry，返回各类型数量。

    等价于 scan_all(source_root, registry)，使用默认路径时更简洁。
    """
    reg = registry or get_registry()
    src = Path(source_root) if source_root else _DEFAULT_SOURCE_ROOT
    return scan_all(src, reg)
