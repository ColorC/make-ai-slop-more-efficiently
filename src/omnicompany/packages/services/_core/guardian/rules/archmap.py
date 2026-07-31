# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.guardian.archmap_compliance.enforcer.py"
"""Guardian 规则 — 架构图 / 抽屉结构 (OMNI-007/008/014/015/016/021)。

OMNI-007: src/ 下出现非预期的 .md/.json/.yaml 文件
OMNI-008: 根级 .py 文件出现在 src/omnicompany/ 下（违反抽屉纪律）
OMNI-014: 非法 drawer（不在 archmap.yaml 白名单里的顶层目录）
OMNI-015: 仓库根禁区文件
OMNI-016: packages/ 直接子目录禁区（必须是 domains/services/vendors 之一）
OMNI-021: data/ 新子目录漂移（forbid_new_subdirs=true 的 drawer）
"""
from __future__ import annotations

import logging
from pathlib import Path

from ._base import FileContext, GuardianRule, _is_python, _is_external

logger = logging.getLogger(__name__)


# ─── OMNI-007: src/omnicompany/ 下出现非预期的配置/文档文件 ────────

_STRAY_EXTS = frozenset({".md", ".json", ".yaml", ".yml"})
# 对齐 distributed-docs.md v2 §四：
#   - DESIGN.md 就近文档合法（v2 明示）
#   - PROGRESS.md 唯一权威在 docs/PROGRESS.md，src/ 下不允许（由 OMNI-035e 报违规）
_ALLOWED_IN_SRC = frozenset({"README.md", "py.typed", "CHANGELOG.md", "DESIGN.md"})
_STRAY_PATH_EXEMPTIONS = frozenset({
    # LLM-CALL-UNIFICATION T9 (2026-06-13): runtime prompt material loaded by
    # dashboard.native_agent.NativeIdeAgent._PROMPT_PATH.
    "src/omnicompany/dashboard/native_agent_prompt.md",
})

# vite/typescript 项目的标准根文件 — frontend/ 下允许这些 well-known 名字
_FRONTEND_PROJECT_FILES = frozenset({
    "package.json", "package-lock.json", "tsconfig.json", "tsconfig.node.json",
    "vite.config.ts", "vite.config.js", ".eslintrc.json", ".prettierrc",
    "yarn.lock", "pnpm-lock.yaml",
})


def _check_stray_config_in_src(ctx: FileContext) -> bool:
    p = ctx.path.replace("\\", "/")
    if not p.startswith("src/"):
        return False
    if _is_external(ctx):
        return False
    # NPM vendored deps — 整个 node_modules 树都豁免
    if "/node_modules/" in p:
        return False
    ext = Path(p).suffix.lower()
    if ext not in _STRAY_EXTS:
        return False
    if p in _STRAY_PATH_EXEMPTIONS:
        return False
    name = Path(p).name
    if name in _ALLOWED_IN_SRC:
        return False
    # dashboard/frontend 项目根的标准 JS/TS 配置文件
    if ("/dashboard/frontend/" in p or "/dashboard/extensions/" in p) and name in _FRONTEND_PROJECT_FILES:
        return False
    # knowledge/ 目录里的 .md 是语义节点的文档模板，故意和 routers/formats colocate
    if "/knowledge/" in p and ext == ".md":
        return False
    # .omni/ 目录是就近元数据（distributed-docs v2 §三）：manifest.yaml / health/*.jsonl
    if "/.omni/" in p:
        return False
    return True


# ─── OMNI-008: 根级 .py 文件出现在 src/omnicompany/ 下 ─────────────

_ALLOWED_ROOT_PYS = frozenset({"__init__.py", "__main__.py", "_core_version.py"})


def _check_root_py_in_omnicompany(ctx: FileContext) -> bool:
    if not _is_python(ctx):
        return False
    # 必须直接位于 src/omnicompany/ 下（不是 src/omnicompany/<subdir>/）
    parts = ctx.path.split("/")
    if len(parts) != 3 or parts[0] != "src" or parts[1] != "omnicompany":
        return False
    return parts[2] not in _ALLOWED_ROOT_PYS


# ─── OMNI-014: 非法 drawer ─────────────────────────────────────────

# 允许的非 drawer 辅助物（dunder / 版本文件）
_LEGAL_ROOT_AUX = frozenset({
    "__pycache__", "__init__.py", "py.typed", "_core_version.py",
})


def _check_illegal_drawer(ctx: FileContext) -> bool:
    """src/omnicompany/<X>/ 出现不在 archmap.yaml 白名单里的顶层目录。"""
    p = ctx.path.replace("\\", "/")
    if not p.startswith("src/omnicompany/"):
        return False
    rest = p[len("src/omnicompany/"):]
    if "/" not in rest:
        return False
    first_seg = rest.split("/", 1)[0]
    if first_seg in _LEGAL_ROOT_AUX:
        return False
    if first_seg.startswith("_archive"):
        return False

    try:
        from omnicompany.core.archmap import load_archmap
        m = load_archmap()
        if m.is_legal_src_omnicompany_drawer(first_seg):
            return False
    except Exception as e:
        logger.warning("OMNI-014: archmap 加载失败，回退保守白名单: %s", e)
        _FALLBACK = frozenset({
            "core", "bus", "protocol", "primitives", "runtime",
            "tracing", "cli", "dashboard", "packages", "_graveyard",
        })
        if first_seg in _FALLBACK:
            return False

    return True


# ─── OMNI-015: 仓库根禁区文件 ────────────────────────────────────

def _check_forbidden_root_file(ctx: FileContext) -> bool:
    """仓库根下的文件 / 目录匹配 archmap.forbidden_at_repo_root。"""
    p = ctx.path.replace("\\", "/")
    # 只关注根层(没有 / 或只有一个尾部 /)
    if p.count("/") > 1:
        return False
    if "/" in p and not p.endswith("/"):
        return False
    try:
        from omnicompany.core.archmap import load_archmap
        m = load_archmap()
        if p.endswith("/"):
            # 目录检查
            name = p[:-1]
            return name in m.forbidden_root_dirs
        # 文件检查
        hit, _reason = m.is_forbidden_root_file(p)
        return hit
    except Exception as e:
        logger.warning("OMNI-015: archmap 加载失败，跳过本次: %s", e)
        return False


# ─── OMNI-016: packages/ 直接子目录禁区 ──────────────────────────

_LEGAL_PACKAGES_LAYERS = frozenset({"domains", "services", "vendors"})


def _check_packages_direct_child(ctx: FileContext) -> bool:
    """src/omnicompany/packages/<X>/ 中的 X 必须是 domains/services/vendors 之一。"""
    p = ctx.path.replace("\\", "/")
    if not p.startswith("src/omnicompany/packages/"):
        return False
    rest = p[len("src/omnicompany/packages/"):]
    if "/" not in rest:
        return False
    first_seg = rest.split("/", 1)[0]
    if first_seg in _LEGAL_PACKAGES_LAYERS:
        return False
    if first_seg in ("__pycache__", "__init__.py"):
        return False
    if first_seg.startswith("_archive"):
        return False
    return True


# ─── OMNI-021: data/ 新子目录漂移 ────────────────────────────────

def _check_drawer_drift(ctx: FileContext) -> bool:
    """OMNI-021: 扫 forbid_new_subdirs=true 的 drawer，发现非白名单子目录。"""
    p = ctx.path.replace("\\", "/")
    if not p.endswith("/"):
        return False
    # 形如 "data/absorption/" — 恰好两段（顶层 drawer + 一级子目录）
    parts = p.rstrip("/").split("/")
    if len(parts) != 2:
        return False
    drawer, sub = parts[0], parts[1]
    try:
        from omnicompany.core.archmap import load_archmap
        m = load_archmap()
    except Exception:
        return False
    spec = m.repo_root.get(drawer)
    if not spec:
        return False
    if not spec.get("forbid_new_subdirs"):
        return False
    allowed = spec.get("allowed_subdirs", {}) or {}
    import fnmatch as _fn
    for pat in allowed.keys():
        pat_name = pat.rstrip("/")
        if _fn.fnmatch(sub, pat_name):
            return False
    return True


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-007",
        name="stray-config-in-src",
        severity="LOW",
        description="src/ 下出现非预期的 .md/.json/.yaml 文件",
        check=_check_stray_config_in_src,
        disposition=["warn"],
        message_template="{path} 是配置/文档文件但在 src/ 下。请移至 docs/ 或 config/ 目录。",
    ),
    GuardianRule(
        id="OMNI-008",
        name="root-py-in-omnicompany",
        severity="HIGH",
        description="src/omnicompany/ 根目录下出现业务 .py 文件（只允许 dunder + _core_version.py）",
        check=_check_root_py_in_omnicompany,
        disposition=["warn", "quarantine"],
        message_template="{path} 出现在 src/omnicompany/ 根目录。业务代码必须放在 core/ / runtime/ / packages/<domain>/ 等抽屉内。",
    ),
    GuardianRule(
        id="OMNI-014",
        name="illegal-drawer",
        severity="CRITICAL",
        description="src/omnicompany/ 下出现非 archmap.yaml 白名单的顶层目录",
        check=_check_illegal_drawer,
        disposition=["warn", "quarantine"],
        message_template="{path} 属于一个非法 drawer。合法 drawer 见 docs/archmap.yaml 的 src_omnicompany 段。请移入对应 drawer，或修改 archmap.yaml 新增 drawer 定义（需 human 批准）后再写入。",
    ),
    GuardianRule(
        id="OMNI-015",
        name="forbidden-root-file",
        severity="HIGH",
        description="仓库根下出现 archmap.yaml 禁区 glob 匹配的文件（.log / *_report.* / scratch_* / tmp_* 等）",
        check=_check_forbidden_root_file,
        disposition=["warn", "quarantine"],
        message_template="{path} 是仓库根下的禁区文件（报告 / 日志 / 临时文件）。合法位置: docs/reports/<category>/ 或 logs/ 或 .omni/tmp/ 或 tests/_output/。",
    ),
    GuardianRule(
        id="OMNI-016",
        name="packages-direct-child",
        severity="CRITICAL",
        description="src/omnicompany/packages/ 下出现非 layer 子目录（必须是 domains/services/vendors 之一）",
        check=_check_packages_direct_child,
        disposition=["warn", "quarantine"],
        message_template="{path} 直接挂在 packages/ 下，违反 layer 结构。合法位置: packages/domains/<name>/ (业务域) 或 packages/services/<name>/ (系统服务) 或 packages/vendors/<name>/ (第三方)。",
    ),
    GuardianRule(
        id="OMNI-021",
        name="drawer-subdir-drift",
        severity="HIGH",
        description="forbid_new_subdirs=true 的 drawer(如 data/)出现非白名单子目录",
        check=_check_drawer_drift,
        disposition=["warn", "evolve-signal"],
        message_template="{path} 是一个 drawer 新子目录漂移。该 drawer 在 archmap.yaml 里声明了 forbid_new_subdirs: true。合法子目录见 archmap.yaml 对应 drawer 的 allowed_subdirs。请归档到 _archive_* 或把该子目录加进 allowed_subdirs（需 human 批准 archmap.yaml）。",
    ),
]
