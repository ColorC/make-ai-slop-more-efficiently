# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-18T00:00:00Z
# [OMNI] material_id="material:core.guardian.distributed_docs.validator.py"
"""Guardian 规则 — 分布式文档规范 v2 合规 (OMNI-035 家族)。

配对规范：`docs/standards/distributed-docs.md` v2（2026-04-18 立档）。
检查 docs/ 目录白名单、plans/ 命名、reports/ 日期前缀、PROGRESS 唯一性、
计划目录子项闭集、docs/ 内代码/数据产物/运行时残留、大文件 LLM 复核。

家族：
  OMNI-035a  docs/ 根出现非白名单文件/目录                   HIGH      absolute
  OMNI-035b  docs/plans/ 根下散文 .md（必须目录化）           HIGH      absolute
  OMNI-035c  docs/plans/<name>/ 命名非规范                    HIGH      absolute
  OMNI-035d  docs/reports/*.md 缺日期前缀                     MEDIUM    absolute
  OMNI-035e  非 docs/PROGRESS.md 的 PROGRESS.md               HIGH      absolute
  OMNI-035f  docs/plans/[date]TOPIC/ 子项不在闭集             HIGH      absolute  (2026-04-28 立)
  OMNI-035g  docs/ 子目录出现 .py / .pyc / __pycache__        HIGH      absolute  (2026-04-28 立)
  OMNI-035h  docs/ 子目录出现 .json / .jsonl 数据产物         HIGH      absolute  (2026-04-28 立)
  OMNI-035i  docs/ 出现 .log/.prefab/.sh/.pkl/.db/.sqlite     HIGH      absolute  (2026-04-28 立)
  OMNI-035j  docs/ 单文件 > 1 MB 需 LLM 复核                  MEDIUM    needs_judgment (2026-04-28 立)

035a~i 全部纯路径/文件名检查, 无 LLM 依赖.
035j 是 Guardian 内部首条 needs_judgment 规则, 触发后送 judge_agent.py 复核 (qwen-3.6-plus).

注: 本规则家族**不**复用 rules._base._is_external 的 _archive 豁免, 因为 docs/plans/_archive/
也在守护范围内. 真正的归档安全区是 data/_archive/, 由其他规则各自处理或跳过.
"""
from __future__ import annotations

import re

from ._base import FileContext, GuardianRule, _is_external


# ── docs/ 根层闭集（见 distributed-docs.md §四） ──
_ALLOWED_DOCS_ROOT_FILES = frozenset({
    "README.md",
    "PROGRESS.md",
    "控制结构.md",
    "ARCHITECTURE.md",
    "SDK_CONTRACT.md",
    "overseer_backlog.md",
    "taxonomy.yaml",
    "archmap.yaml",
    "ARCH-CHANGES.jsonl",
})

# plans/ 目录命名模式：[YYYY-MM-DD]TOPIC
_PLAN_DIR_PATTERN = re.compile(r"^\[\d{4}-\d{2}-\d{2}\][^/]+$")

# reports/ 文件命名模式：[YYYY-MM-DD]TOPIC.md
_REPORT_FILE_PATTERN = re.compile(r"^\[\d{4}-\d{2}-\d{2}\][^/]+\.md$")


def _norm(path: str) -> str:
    return path.replace("\\", "/")


# ── OMNI-035a: docs/ 根层白名单 ────────────────────────────────────

def _check_docs_root_stray(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if not p.startswith("docs/"):
        return False
    rest = p[len("docs/"):]
    # 只管 docs/ 的直接子文件（深层由 035b/c/d 处理）
    if "/" in rest:
        return False
    return rest not in _ALLOWED_DOCS_ROOT_FILES


# ── OMNI-035b: docs/plans/ 根下散文 .md ─────────────────────────────

def _check_plans_loose_md(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if not p.startswith("docs/plans/"):
        return False
    rest = p[len("docs/plans/"):]
    if "/" in rest:
        return False
    return rest.endswith(".md")


# ── OMNI-035c: docs/plans/<name>/ 命名非规范 ────────────────────────

def _check_plans_nonstandard_dir(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if not p.startswith("docs/plans/"):
        return False
    rest = p[len("docs/plans/"):]
    parts = rest.split("/")
    if len(parts) < 2:
        return False
    name = parts[0]
    if name == "_archive":
        return False
    if _PLAN_DIR_PATTERN.match(name):
        return False
    # 仅在 <name>/ 的直接子文件上触发以降噪
    if len(parts) > 2:
        return False
    return True


# ── OMNI-035d: docs/reports/ 无日期前缀 .md ─────────────────────────

def _check_reports_undated(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if not p.startswith("docs/reports/"):
        return False
    rest = p[len("docs/reports/"):]
    # 只管 reports/ 直接子文件；reports/progress/<YYYY-MM>-archive.md 由 progress 子目录自管
    if "/" in rest:
        return False
    if not rest.endswith(".md"):
        return False
    return not _REPORT_FILE_PATTERN.match(rest)


# ── OMNI-035e: 非 docs/PROGRESS.md 的 PROGRESS.md ───────────────────

def _check_stray_progress(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if _is_external(ctx):
        return False
    name = p.rsplit("/", 1)[-1] if "/" in p else p
    if name != "PROGRESS.md":
        return False
    return p != "docs/PROGRESS.md"


# ── OMNI-035f: docs/plans/[date]TOPIC/ 子项闭集 (2026-04-28 立, 2026-05-15 重整) ──

# 真现实路径模式 (2026-05-15 plans/ 主题区单轴 + omnicompany- 行政层前缀):
#   docs/plans/_archive/[date]TOPIC/<rest>                                — 顶层归档 (早期遗留)
#   docs/plans/<主题区>/[date]TOPIC/<rest>                                — 主题区直接
#   docs/plans/<主题区>/<子主题>/[date]TOPIC/<rest>                       — 主题区内按对象拆子层
#   docs/plans/<主题区>/_archive/[date]TOPIC/<rest>                       — 主题区内归档
# 主题区命名: 代码模块名 (agent-framework/dashboard/...) | 业务域名 (voxelcraft/demogame) | omnicompany-<中文能力名>
_PLAN_TOPIC_INNER = re.compile(
    r"^docs/plans/"
    r"(?:_archive/"
    r"|[^/_][^/]*/(?:[^/_][^/]*/)?(?:_archive/)?"
    r")"
    r"\[\d{4}-\d{2}-\d{2}\][^/]+/(.+)$"
)

# plan 目录顶级允许的非 .md 子项闭集 (扩自 spikes/_archive/):
#   spikes/        — 试跑实验
#   _archive/      — 归档
#   samples/       — 样例 yaml/json
#   data/          — 数据集 (anti_patterns/canonical_anchors 等)
#   reports/       — 阶段报告 (v14_/v22_ 类系列报告应归此)
_PLAN_ALLOWED_SUBDIRS = ("spikes", "_archive", "samples", "data", "reports")


def _check_plans_topic_subitem(ctx: FileContext) -> bool:
    """OMNI-035f: plan 根直接非 .md 散件 (硬违规, absolute HIGH)."""
    p = _norm(ctx.path)
    m = _PLAN_TOPIC_INNER.match(p)
    if not m:
        return False
    rest = m.group(1)
    # 只判 plan 根直接散件, 子目录归 OMNI-035f2
    if "/" in rest:
        return False
    return not rest.endswith(".md")


def _check_plans_topic_nonclosed_subdir(ctx: FileContext) -> bool:
    """OMNI-035f2: plan 子目录第一段非闭集 (灰色, needs_judgment MEDIUM, LLM 判).

    现实闭集 spikes/_archive/samples/data/reports 覆盖大部分用途. 但真 plan 可能
    用 requirements/ gold_samples/ reference_answers/ 等特化子目录. 这部分死规则
    爆候选 + LLM 真判 是否真"特化数据子目录" 还是该归 samples/data/.
    """
    p = _norm(ctx.path)
    m = _PLAN_TOPIC_INNER.match(p)
    if not m:
        return False
    rest = m.group(1)
    if "/" not in rest:
        return False
    first_seg = rest.split("/", 1)[0]
    return first_seg not in _PLAN_ALLOWED_SUBDIRS


# ── OMNI-035g: docs/ 子目录禁 .py / .pyc / __pycache__ (2026-04-28 立) ──

def _check_docs_python(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if not p.startswith("docs/"):
        return False
    # docs/ 根级由 035a 闭集管, 这里只看深层
    if "/" not in p[len("docs/"):]:
        return False
    if p.endswith(".py") or p.endswith(".pyc"):
        return True
    if "__pycache__/" in p:
        return True
    return False


# ── OMNI-035h: docs/ 子目录禁 .json / .jsonl 数据产物 (2026-04-28 立) ──

def _check_docs_data_artifact(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if not p.startswith("docs/"):
        return False
    # docs/ 根级 (含 archmap.yaml / ARCH-CHANGES.jsonl) 由 035a 闭集管
    if "/" not in p[len("docs/"):]:
        return False
    return p.endswith(".json") or p.endswith(".jsonl")


# ── OMNI-035i: docs/ 禁运行时残留 (2026-04-28 立) ────────────────────

_RUNTIME_RESIDUE_EXTS = (
    ".log", ".prefab", ".sh", ".pkl", ".db", ".sqlite",
    ".db-shm", ".db-wal",
)


def _check_docs_runtime_residue(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if not p.startswith("docs/"):
        return False
    return any(p.endswith(ext) for ext in _RUNTIME_RESIDUE_EXTS)


# ── OMNI-035j: docs/ 大文件 (>1 MB) → LLM 复核 (2026-04-28 立) ────────

_LARGE_FILE_THRESHOLD_BYTES = 1 * 1024 * 1024  # 1 MB


def _check_docs_large_file(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if not p.startswith("docs/"):
        return False
    try:
        from pathlib import Path as _Path
        size = _Path(ctx.abs_path).stat().st_size
    except Exception:
        return False
    return size > _LARGE_FILE_THRESHOLD_BYTES


# ── 规则清单 ────────────────────────────────────────────────────────

RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-035a",
        name="docs-root-stray-item",
        severity="HIGH",
        description="docs/ 根出现非白名单文件（闭集见 distributed-docs.md §四）",
        check=_check_docs_root_stray,
        disposition=["warn", "stamp"],
        message_template=(
            "{path} 不在 docs/ 根白名单。"
            "白名单: README.md / PROGRESS.md / 控制结构.md / ARCHITECTURE.md / "
            "SDK_CONTRACT.md / overseer_backlog.md / taxonomy.yaml / archmap.yaml / "
            "ARCH-CHANGES.jsonl。新增先改 distributed-docs.md §四。"
        ),
    ),
    GuardianRule(
        id="OMNI-035b",
        name="plans-loose-md",
        severity="HIGH",
        description="docs/plans/ 根下散文 .md（必须包为 [date]TOPIC/plan.md 目录）",
        check=_check_plans_loose_md,
        disposition=["warn", "stamp"],
        message_template=(
            "{path} 不应直接置于 docs/plans/ 下。"
            "规范：每个计划必须是目录 [YYYY-MM-DD]TOPIC/，主文档命名 plan.md。"
        ),
    ),
    GuardianRule(
        id="OMNI-035c",
        name="plans-nonstandard-name",
        severity="HIGH",
        description="docs/plans/<name>/ 命名不匹配 [YYYY-MM-DD]TOPIC/",
        check=_check_plans_nonstandard_dir,
        disposition=["warn", "stamp"],
        message_template=(
            "{path} 所在 plans 目录名不规范。必须形如 [2026-04-18]TOPIC/；例外仅 _archive/。"
        ),
    ),
    GuardianRule(
        id="OMNI-035d",
        name="reports-undated",
        severity="MEDIUM",
        description="docs/reports/*.md 应以 [YYYY-MM-DD] 前缀",
        check=_check_reports_undated,
        disposition=["warn", "stamp"],
        message_template=(
            "{path} 应重命名为 [YYYY-MM-DD]TOPIC.md（reports/ 下直接 .md 必须带日期前缀）。"
        ),
    ),
    GuardianRule(
        id="OMNI-035e",
        name="stray-progress-md",
        severity="HIGH",
        description="PROGRESS.md 唯一权威只在 docs/PROGRESS.md",
        check=_check_stray_progress,
        disposition=["warn", "stamp"],
        message_template=(
            "{path} 违反 PROGRESS 唯一性。"
            "删除此文件，内容并入 docs/PROGRESS.md 或对应包 DESIGN.md 状态节。"
        ),
    ),
    GuardianRule(
        id="OMNI-035f",
        name="plans-topic-stray-non-md",
        severity="HIGH",
        description="docs/plans/[YYYY-MM-DD]TOPIC/ 根级散件 (非 .md) 应归子目录",
        check=_check_plans_topic_subitem,
        disposition=["warn", "stamp", "relocate"],
        message_template=(
            "{path} 是计划目录根级散件 (非 .md). 散件 yaml/json 应进 samples/ 或 data/ 子目录, "
            "脚本进 data/_workspaces/<plan>/."
        ),
    ),
    GuardianRule(
        id="OMNI-035f2",
        name="plans-topic-nonclosed-subdir",
        severity="MEDIUM",
        description="docs/plans/[YYYY-MM-DD]TOPIC/ 子目录第一段不在闭集 (LLM 复核)",
        check=_check_plans_topic_nonclosed_subdir,
        disposition=["warn"],
        message_template=(
            "{path} 子目录第一段不在闭集 (spikes/_archive/samples/data/reports). "
            "可能是真特化数据子目录 (requirements/gold_samples 等), 也可能该归 samples/data/. "
            "LLM 复核或人工判."
        ),
        certainty="needs_judgment",
    ),
    GuardianRule(
        id="OMNI-035g",
        name="docs-python-leak",
        severity="HIGH",
        description="docs/ 任意子目录禁出现 .py / .pyc / __pycache__",
        check=_check_docs_python,
        disposition=["warn", "stamp", "relocate"],
        message_template=(
            "{path} 是 Python 代码或缓存, 不应放 docs/。"
            "脚本应在 src/ 或 data/_workspaces/, 缓存应被 .gitignore。"
        ),
    ),
    GuardianRule(
        id="OMNI-035h",
        name="docs-data-artifact-leak",
        severity="HIGH",
        description="docs/ 子目录禁出现 .json / .jsonl 数据产物",
        check=_check_docs_data_artifact,
        disposition=["warn", "stamp", "relocate"],
        message_template=(
            "{path} 是数据产物, 不应放 docs/。"
            "数据应在 data/_workspaces/<plan>/ 或 data/services/<svc>/。"
            "docs/ 根级例外 (archmap.yaml / ARCH-CHANGES.jsonl) 由 035a 闭集管。"
        ),
    ),
    GuardianRule(
        id="OMNI-035i",
        name="docs-runtime-residue",
        severity="HIGH",
        description="docs/ 禁运行时残留 (.log / .prefab / .sh / .pkl / .db / .sqlite)",
        check=_check_docs_runtime_residue,
        disposition=["warn", "stamp", "relocate"],
        message_template=(
            "{path} 是运行时残留 (日志/预制件/脚本/缓存/数据库)。docs/ 内一律禁出。"
        ),
    ),
    GuardianRule(
        id="OMNI-035j",
        name="docs-large-file-needs-judgment",
        severity="MEDIUM",
        description="docs/ 单文件 > 1 MB 需 LLM 复核 (图示合理 / 数据产物违规)",
        check=_check_docs_large_file,
        disposition=["warn", "stamp", "relocate"],
        certainty="needs_judgment",
        message_template=(
            "{path} 大于 1 MB。图示/截图/设计稿合法; 数据产物/缓存违规。"
            "送 Guardian Agent 语义判定 (qwen-3.6-plus)。"
        ),
    ),
]
