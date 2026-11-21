# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.guardian.rules.migration_debt_detector.py"
"""Guardian 规则 — 迁移债务 (OMNI-009/010/012)。

OMNI-009: live code 从 _graveyard 导入（禁止坟墓代码复活进 live path）
OMNI-010: 禁用的已退役路径出现在 import / path literal / module reference
OMNI-012: 引用已退役的 runtime 扁平路径
"""
from __future__ import annotations

import re

from ._base import FileContext, GuardianRule, _is_python


# ─── OMNI-009: live code 从 _graveyard 导入 ──────────────────────

_GRAVEYARD_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+omnicompany\._graveyard\b",
    re.MULTILINE,
)


def _check_graveyard_import(ctx: FileContext) -> bool:
    if not _is_python(ctx):
        return False
    if ctx.content is None:
        return False
    if "/_graveyard/" in ctx.path:
        return False  # 坟内部互引不触发
    if "_archive" in ctx.path or "_archived" in ctx.path:
        return False
    return bool(_GRAVEYARD_IMPORT_RE.search(ctx.content))


# ─── OMNI-010: 禁用的已退役路径 ───────────────────────────────────

_BANNED_PATHS_RE = re.compile(
    # primitives_impl used as module or path (followed by . or /)
    r"primitives_impl[./]"
    # packages.omnicompany.X as module path
    r"|packages\.omnicompany\."
    # packages/omnicompany/ as filesystem path
    r"|packages/omnicompany/"
    # packages.imported or packages/imported as module/path
    r"|packages\.imported[./]"
    r"|packages/imported/"
)


def _check_banned_paths(ctx: FileContext) -> bool:
    if not _is_python(ctx) and not ctx.path.endswith((".yaml", ".yml", ".toml")):
        return False
    if ctx.content is None:
        return False
    if "/_graveyard/" in ctx.path or "/docs/" in ctx.path:
        return False
    if "_archive" in ctx.path or "_archived" in ctx.path:
        return False
    # The rule itself contains these strings as literals — self-exemption
    if ctx.path.endswith("packages/services/guardian/patrol.py"):
        return False
    if ctx.path.endswith("packages/services/guardian/rules/migration.py"):
        return False
    # Line-by-line scan that skips pure-comment lines
    lines = ctx.content.splitlines()
    in_triple_quote = False
    for line in lines:
        tq_count = line.count('"""') + line.count("'''")
        if tq_count % 2 == 1:
            in_triple_quote = not in_triple_quote
            continue
        if in_triple_quote:
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if _BANNED_PATHS_RE.search(line):
            return True
    return False


# ─── OMNI-012: 引用已退役的 runtime 扁平路径 ─────────────────────

_RETIRED_RUNTIME_NAMES = (
    # agent/ candidates (moved from flat to agent/)
    "agent_constants", "agent_loop", "agent_loop_compact", "agent_loop_config",
    "agent_loop_permissions", "agent_loop_tools", "agent_node_loop",
    "agent_intent_router", "ide_agent_loop",
    # llm/ candidates
    "embedding_client", "vision", "compression_summary",
    # exec/ candidates
    "runner", "session", "sub_pipeline", "bootstrap", "tool_executor",
    "graph_builder",
    # routing/ candidates
    "router", "route_retriever", "boltzmann_router", "soft_node_executor",
    # signals/ candidates
    "pain_system", "reward", "stuck", "mirror_node", "self_types",
    # storage/ candidates
    "db_access", "domain_loader", "experience_search", "tool_pattern_registry",
    # NOTE: "llm" alone is both a new subdir AND a retired flat module name.
    # We cannot flag it without false positives, so it's omitted here.
    # NOTE: "knowledge", "registry", "tools" are too generic to grep on safely.
)
_RETIRED_RUNTIME_FLAT_RE = re.compile(
    r"omnicompany\.runtime\.(" + "|".join(re.escape(n) for n in _RETIRED_RUNTIME_NAMES) + r")\b"
)


def _check_retired_runtime_flat(ctx: FileContext) -> bool:
    if not _is_python(ctx) and not ctx.path.endswith((".yaml", ".yml", ".toml")):
        return False
    if ctx.content is None:
        return False
    if "/_graveyard/" in ctx.path or "/docs/" in ctx.path:
        return False
    if "/data/" in ctx.path or ctx.path.startswith("data/"):
        return False
    if "_archive" in ctx.path or "_archived" in ctx.path:
        return False
    return bool(_RETIRED_RUNTIME_FLAT_RE.search(ctx.content))


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-009",
        name="live-graveyard-import",
        severity="HIGH",
        description="live 代码从 _graveyard 导入（坟墓代码复活进 live path）",
        check=_check_graveyard_import,
        disposition=["warn", "evolve-signal"],
        message_template="{path} 从 _graveyard 导入。坟墓目录不是 Python 包，如果需要该符号请先把它搬回 live 模块。",
    ),
    GuardianRule(
        id="OMNI-010",
        name="banned-retired-paths",
        severity="HIGH",
        description="引用了 2026-04-07 迁移后已退役的路径 (primitives_impl / packages.omnicompany / packages.imported)",
        check=_check_banned_paths,
        disposition=["warn", "evolve-signal"],
        message_template="{path} 引用了已退役路径（primitives_impl / packages.omnicompany / packages.imported）。这些抽屉不再存在，见 docs/ARCHITECTURE.md。",
    ),
    GuardianRule(
        id="OMNI-012",
        name="retired-runtime-flat-path",
        severity="MEDIUM",
        description="引用 2026-04-07 前的 runtime 扁平路径（如 runtime.router 而不是 runtime.routing.router）",
        check=_check_retired_runtime_flat,
        disposition=["warn", "evolve-signal"],
        message_template="{path} 引用了 runtime 扁平路径。Phase D 后 runtime/ 已拆分为 agent/ llm/ exec/ routing/ signals/ storage/，请使用新路径。",
    ),
]
