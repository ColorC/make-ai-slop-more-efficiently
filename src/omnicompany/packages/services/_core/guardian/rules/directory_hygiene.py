# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-21T00:00:00Z
# [OMNI] material_id="material:core.guardian.directory_hygiene.enforcer.py"
"""Guardian 规则 — 目录结构卫生 (OMNI-041/042).

OMNI-041: data/ 下新建的目录必须在 archmap.yaml `data.allowed_subdirs` 白名单内
OMNI-042: 归档目录命名必须统一为 `_archive/<topic>/`，禁止 `_archived/`、`_archive_<xxx>/` 等变体

背景:
    2026-04-21 B3/B4 治理发现 data/ 下长期存在 18+ 非法 subdir (voxelcraft/crystallize/
    doctor/llm_audit 等) 违反 archmap.yaml `forbid_new_subdirs: true`, 但 Guardian
    没扫到。根因: fs_scanner_worker.py 自己维护了一份 13 entries 的 hardcoded 白名单,
    与 archmap.yaml 的 3 entries 严重不一致 (B3b 发现).

    同期 data/_archive_* 出现 4 种前缀变体 (_archive, _archive_agent_loop,
    _archive_event_split, _archive_pre_move8), scripts/ 下还有 3 种 (_archived,
    _archived_analysis, _archive_agent_loop), 命名混乱难以预测。B3/B5 已统一.

两条规则把这些发现转成不可再回潮的强制校验, 以 archmap.yaml 为**唯一**白名单源.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ._base import FileContext, GuardianRule


_ARCHMAP_CACHE: dict[str, object] = {}
_FORBIDDEN_ARCHIVE_VARIANT_RE = re.compile(r"_archive_[a-z_]+|_archived(?:_[a-z_]+)?")


def _load_archmap_data_allowed() -> tuple[set[str], set[str], set[str]]:
    """从 docs/archmap.yaml 读取 data 下的合法 subdirs/files/patterns。

    返回 (allowed_subdirs, allowed_files, allowed_file_patterns)。
    结果缓存至进程级避免重复读 YAML。
    """
    if _ARCHMAP_CACHE.get("loaded"):
        return (
            _ARCHMAP_CACHE["subdirs"],      # type: ignore[return-value]
            _ARCHMAP_CACHE["files"],        # type: ignore[return-value]
            _ARCHMAP_CACHE["patterns"],     # type: ignore[return-value]
        )
    # archmap 在 <repo>/docs/archmap.yaml
    # 本文件 parents[5] = src/omnicompany/packages/services/guardian/rules/directory_hygiene.py
    # parents[5] = src, parents[6] = <repo>
    repo_root = Path(__file__).resolve().parents[6]
    archmap_path = repo_root / "docs" / "archmap.yaml"
    allowed_subdirs: set[str] = set()
    allowed_files: set[str] = set()
    allowed_patterns: set[str] = set()
    try:
        if archmap_path.exists():
            data = yaml.safe_load(archmap_path.read_text(encoding="utf-8")) or {}
            data_section = (data.get("src_omnicompany") or {}).get("data")
            # 顶层 "repo_root" + "data" 在 v12 schema 下可能在不同位置, 容错搜索
            if data_section is None:
                root_section = data.get("repo_root") or {}
                data_section = root_section.get("data") or data.get("data")
            if isinstance(data_section, dict):
                subdirs = data_section.get("allowed_subdirs", {}) or {}
                # subdirs 可能是 dict (key = "_archive/", value = 注释) 也可能是 list
                if isinstance(subdirs, dict):
                    allowed_subdirs = {k.rstrip("/") for k in subdirs.keys()}
                elif isinstance(subdirs, list):
                    allowed_subdirs = {str(k).rstrip("/") for k in subdirs}
                allowed_files = set(data_section.get("allowed_files", []) or [])
                allowed_files.update(data_section.get("required_files", []) or [])
                allowed_patterns = set(data_section.get("allowed_file_patterns", []) or [])
    except Exception:
        pass
    _ARCHMAP_CACHE["subdirs"] = allowed_subdirs
    _ARCHMAP_CACHE["files"] = allowed_files
    _ARCHMAP_CACHE["patterns"] = allowed_patterns
    _ARCHMAP_CACHE["loaded"] = True
    return allowed_subdirs, allowed_files, allowed_patterns


def _path_first_data_subdir(p: str) -> str | None:
    """从路径里抽 data/ 下的第一层目录名。

    e.g. "data/services/doctor/foo.py" → "services"
         "data/events.db" → None (文件, 不是 subdir)
    """
    parts = p.replace("\\", "/").split("/")
    if len(parts) < 2 or parts[0] != "data":
        return None
    # 第二段必须是目录才算 subdir; 文件 (包含 .) 跳过
    second = parts[1]
    if "." in second:
        return None
    return second


def _check_data_subdir_whitelist(ctx: FileContext) -> bool:
    """OMNI-041: data/ 下的新增文件/目录必须在 archmap allowed_subdirs 白名单内.

    只对 "A" (新增) change_type 生效, 已存在的文件不扰动.
    """
    if ctx.change_type not in ("A", "?"):
        return False
    p = ctx.path.replace("\\", "/")
    if not p.startswith("data/"):
        return False
    allowed_subdirs, allowed_files, allowed_patterns = _load_archmap_data_allowed()
    remainder = p[len("data/"):]
    if "/" not in remainder:
        # 单段路径 - 可能是目录 (data/services) 也可能是文件 (data/events.db)
        # 先判合法 subdir (目录), 再判合法 file
        if remainder in allowed_subdirs:
            return False
        # 判文件
        if remainder in allowed_files:
            return False
        for pat in allowed_patterns:
            from fnmatch import fnmatch
            if fnmatch(remainder, pat):
                return False
        # 物理上是目录就报 subdir 违规, 是文件报 file 违规 (消息模板同)
        return True
    # 多段路径 data/<first>/... — 第一段必须合法
    first = remainder.split("/", 1)[0]
    return first not in allowed_subdirs


def _check_archive_naming_consistency(ctx: FileContext) -> bool:
    """OMNI-042: 归档目录命名必须统一 _archive/, 禁 _archive_<xxx>/ 和 _archived/ 变体.

    对新增文件的路径做正则检查. 路径含 _archive_<any> 或 _archived 的都违规.
    允许的只有 `_archive` (单独) 和 `_archive/<子目录>/`.

    豁免: 路径已在 `_archive/` 之内 (如 `_archive/pre_move8/foo/_archive_old_backups/`)
    不再追究 - 归档内的历史命名保持原样, 不强迫重写历史.
    """
    if ctx.change_type not in ("A", "?"):
        return False
    p = ctx.path.replace("\\", "/")
    # 只扫 data/, scripts/, src/omnicompany/ 下的路径 (项目管控范围)
    if not any(p.startswith(prefix) for prefix in ("data/", "scripts/", "src/omnicompany/")):
        return False
    parts = p.split("/")
    # 豁免 1: 路径已有 _archive/ 段之后, 内部命名不管
    try:
        archive_idx = parts.index("_archive")
        # 只检查 _archive 之前的路径段
        parts_to_check = parts[:archive_idx]
    except ValueError:
        # 路径不含 _archive/, 检查全部
        parts_to_check = parts
    for seg in parts_to_check:
        if _FORBIDDEN_ARCHIVE_VARIANT_RE.fullmatch(seg):
            return True
    return False


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-041",
        name="data-subdir-whitelist",
        severity="HIGH",
        description="data/ 下新增 subdir/file 必须在 archmap.yaml allowed_subdirs/allowed_files 白名单内",
        check=_check_data_subdir_whitelist,
        disposition=["warn"],
        message_template=(
            "{path}: data/ 下新增路径不在 archmap.yaml 白名单. 合法 subdir: _archive/ "
            "_runtime/ domains/ services/ absorption/. 根层 .db 文件需在 required_files "
            "或 allowed_files 明文加入. 新增其他目录请先 PR 更新 docs/archmap.yaml."
        ),
        certainty="absolute",
    ),
    GuardianRule(
        id="OMNI-042",
        name="archive-naming-consistency",
        severity="HIGH",
        description="归档目录必须用 _archive/<topic>/ 结构, 禁止 _archived/ 或 _archive_<xxx>/ 变体",
        check=_check_archive_naming_consistency,
        disposition=["warn"],
        message_template=(
            "{path}: 路径段含过时的归档命名变体 (_archived/_archive_xxx). "
            "2026-04-21 B3/B5 统一规范: 只允许 `_archive/<topic>/`. "
            "将此路径重命名到 _archive/<描述性子名>/ 下."
        ),
        certainty="absolute",
    ),
]
