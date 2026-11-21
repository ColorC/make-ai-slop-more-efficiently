# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-23T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.runtime_hygiene.scanner.rules.py"
"""Guardian 规则 — 运行空间卫生 (OMNI-047 / 048 / 049 / 050).

范围: **运行空间健康** (2026-04-23 plan GUARDIAN-COMPLIANCE-HARDENING §零 定义).
    核心引擎始终在线 · 无垃圾 (空文件夹 / 临时文件 / 过期产物) 残留 · 体积基线告警.
    具体性能诊断归 Doctor, 本层只做基线.

与 directory_hygiene 分工:
    directory_hygiene (OMNI-041/042): 目录命名/白名单 (语义空间规范)
    runtime_hygiene  (OMNI-047~050): 目录内容卫生 (运行空间无污染)

特殊: 本家族的规则**不走标准 RuleEngine (per-file FileContext)**, 扫描粒度是目录/文件系统,
    由 HygieneScanWorker 消费本模块的元数据 + scan_* 函数, 产出 Violation.
    这是 plan §六 "告警 ≠ 清理" 原则的落地 - Guardian 只产告警, 不动数据.

当前家族实装状态 (2026-04-23, plan §四 第一波):
    - OMNI-047 空文件夹扫描            ✅ (本文件 · I-09)
    - OMNI-048 临时文件残留            ✅ 048a 硬扫 · 048b 候选 (I-10, LLM 复核延到 I-25)
    - OMNI-049 过期运行产物老化        ✅ (I-11)
    - OMNI-050 数据体积异常告警        ✅ (I-12)
    - OMNI-051 data/ 分布式白名单      ✅ 051a 硬扫未授权 subdir · 051b 候选 (I-13)

I-12 体积告警 (2026-04-23 混合策略):
    - Guardian 内建**全局默认** (本模块 _DEFAULT_SIZE_LIMITS) 抓 events.db / *.db / *.jsonl
      等跨服务核心数据. PROGRESS.md 2026-04-21 记录 events.db 膨胀 9.5GB 事故, 该默认
      正是为防其再发生.
    - 各模块 .omni/manifest.yaml 可添加 `kind: size_limits` document 追加本地 pattern.
    - 顶层 archmap.yaml 迁 default 是 S-02 工作; 本波默认写死, 迁之前保持一致性.
    - 单轨硬判, 不走 LLM.

I-11 过期产物老化 (2026-04-23 约定):
    各模块 .omni/manifest.yaml 添加一个 `kind: aging_policy` document 声明:
        ---
        kind: aging_policy
        policies:
          - path_pattern: "data/services/foo/scratch/**/*"
            max_age_days: 7
            severity: warn
          - path_pattern: "logs/patrol/*.json"
            max_age_days: 30
            severity: info
    Guardian 按声明 stat mtime, 超期即加入老化清单 (不删, §九 告警≠清理).
    单轨规则: 时间硬判, 无语义分歧.
    policy 可来自 service manifest (就近声明) 或后续 S-02 把顶层通用 policy 放 archmap.

I-13 data/ 分布式白名单 (2026-04-23 约定):
    各 service 在 src/.../services/<svc>/.omni/manifest.yaml 内添加一个
    `kind: data_layout` document, 声明本 service 在 data/services/<svc>/ 下
    允许出现的 subdir/file. Guardian 按声明守护.

    manifest 示例 (额外 document, 用 --- 分隔, 与管线声明并列):
        ---
        kind: data_layout
        allowed_subdirs:
          scratch: 临时工作区, 可清理
          patrol: PatrolWorker 巡查报告
        required_files: []

    顶层 archmap.yaml 对"必须声明 data_layout"的 required_for 节属 S-02 (第二波).
    当前 service 若未声明 data_layout, OMNI-051 不报 — 等 OMNI-060 manifest-required
    接入时统一报.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Iterable

from ._base import FileContext, GuardianRule


# ══════════════════════════════════════════════════════════════
# OMNI-047 · 空文件夹扫描
# ══════════════════════════════════════════════════════════════

# 扫描范围: 项目根下应当"活跃"的目录, 空目录即噪音
_SCAN_ROOTS = ("src", "data", "tests", "scripts", "docs", "logs")

# 扫描时跳过的目录名 (不下钻, 也不判空)
# - 系统/构建: __pycache__ / .git / venv / .venv / .pytest_cache / node_modules
# - 归档: _graveyard / _archive (用户铁律: 归档不重写历史, 内部空目录也不扰动)
_SKIP_DIR_NAMES = frozenset({
    "__pycache__", ".git", "venv", ".venv", ".pytest_cache",
    "node_modules", ".mypy_cache", ".ruff_cache",
    "_graveyard", "_archive",
})

# 完整路径豁免 (2026-04-24 GuardianAuditStore 防递归):
# Guardian 自己产出的审计/报告/hygiene 记录不参与扫描, 避免"扫自己写的东西又产生新记录"循环
_SKIP_PATH_PREFIXES = (
    "data/services/guardian/",
)


def _is_path_skip(rel: str) -> bool:
    return any(rel.startswith(pref) for pref in _SKIP_PATH_PREFIXES)


def _is_python_package_placeholder(dir_path: Path) -> bool:
    """Python 包目录仅含 __init__.py 视为合法占位, 不算空."""
    try:
        entries = list(dir_path.iterdir())
    except (PermissionError, OSError):
        return False
    if len(entries) != 1:
        return False
    return entries[0].is_file() and entries[0].name == "__init__.py"


def _is_recursively_empty(dir_path: Path) -> bool:
    """递归判空: 目录下所有子项 (递归) 都在 _SKIP_DIR_NAMES 内或都是空目录."""
    try:
        entries = list(dir_path.iterdir())
    except (PermissionError, OSError):
        return False
    if not entries:
        return True
    # Python 包占位不算空
    if _is_python_package_placeholder(dir_path):
        return False
    for entry in entries:
        if entry.is_file():
            return False
        if entry.is_dir():
            if entry.name in _SKIP_DIR_NAMES:
                # 跳过目录视为"不存在", 不算实质内容
                continue
            if not _is_recursively_empty(entry):
                return False
    return True


def scan_empty_dirs(project_root: Path) -> list[str]:
    """扫描项目根下的空目录, 返回相对路径列表 (POSIX 分隔)."""
    empty: list[str] = []
    for top in _SCAN_ROOTS:
        base = project_root / top
        if not base.exists() or not base.is_dir():
            continue
        # BFS, 下钻时跳过 _SKIP_DIR_NAMES
        stack: list[Path] = [base]
        while stack:
            d = stack.pop()
            rel = d.relative_to(project_root).as_posix()
            # 防递归: guardian 自己产出路径整层跳过
            if _is_path_skip(rel):
                continue
            try:
                entries = list(d.iterdir())
            except (PermissionError, OSError):
                continue
            # 先收本层子目录 (非 skip) 入栈下钻
            for e in entries:
                if e.is_dir() and e.name not in _SKIP_DIR_NAMES:
                    stack.append(e)
            # 判 d 自身是否 "递归为空"
            # base 自己即便空也不报 (顶层 scan root 本来就可以临时空)
            if d == base:
                continue
            if _is_recursively_empty(d):
                empty.append(rel)
    return sorted(set(empty))


# ══════════════════════════════════════════════════════════════
# OMNI-048 · 临时文件残留 (双轨)
# ══════════════════════════════════════════════════════════════

# 048a · 硬模式 (absolute): 命中即违规, 无歧义. 源自跨平台 agent/IDE/OS 常见临时品.
_TEMP_HARD_PATTERNS: tuple[str, ...] = (
    # 编辑器/IDE 交换/备份
    "*.tmp", "*.bak", "*.orig", "*.swp", "*.swo", "*.rej",
    "*~",             # vim/emacs backup
    # OS 残留
    ".DS_Store", "Thumbs.db", "desktop.ini",
    # Office/WPS 打开锁文件 (用户 lark-cli 下载 xlsm 时可能产生)
    "~$*",
    # 明显 backup 命名模式
    "*_backup_*", "*_bak_*", "*_old_*", "*_copy_*",
    "*.backup", "*.old", "*.copy",
    # 合并冲突残留
    "*.BACKUP.*", "*.BASE.*", "*.LOCAL.*", "*.REMOTE.*",
    # 单次跑生成的 patch/diff 残留
    "*.patch.bak",
    # log rotation 残留 (logrotate 默认 .1/.2/... 后缀)
    "*.log.[0-9]", "*.log.[0-9][0-9]",
    # 进程 crash / nohup 残留
    "core.[0-9]*",
    "nohup.out",
    # Python bytecode cache files must never live in tracked source/doc areas.
    "*.pyc", "*.pyo",
    # 临时 pid 文件 (sentinel.pid 等合法 .omni/ 内 pid 由路径豁免保护)
    "*.pid",
)

# 048a 路径豁免 (2026-04-24 收紧, plan §十二 极端情况审计):
# - .omni/ 只豁免已知规定文件名 (manifest.yaml / sentinel.pid / 标准产物),
#   **不豁免整个 .omni/ 目录** — 防止 `.omni/xxx.bak` 类污染
# - .git/ 是 git 内部目录, 完全豁免
_HARD_PATH_EXEMPTIONS: tuple[str, ...] = (
    ".git/",                       # git 内部
)

# .omni/ 内的合法文件名 whitelist (精确匹配或 glob)
_OMNI_DIR_LEGAL_NAMES: tuple[str, ...] = (
    "manifest.yaml",               # 分布式文档规范
    "sentinel.pid",                # Guardian sentinel daemon pid
    "core_activity_ts.json",       # sentinel 活动时间
    "sentinel_state.json",         # sentinel 状态
    "shield_audit.jsonl",          # shield 写入审计
    "hygiene-whitelist.json",      # Guardian hygiene whitelist
    "health/*",                    # .omni/health/ 合规档案目录
    "guardian/*",                  # .omni/guardian/ 子目录
    "fix-queue/*",                 # Guardian auto_comment 软修复队列
    "quarantine/*",                # Guardian quarantine 隔离区
)


def _is_legal_omni_dir_file(rel_path: str) -> bool:
    """判 `.omni/` 下文件是否在合法名册内."""
    import fnmatch
    if "/.omni/" not in rel_path and not rel_path.startswith(".omni/"):
        return False
    # 拿 .omni/ 之后的相对部分
    idx = rel_path.find(".omni/")
    after = rel_path[idx + len(".omni/"):]
    for pat in _OMNI_DIR_LEGAL_NAMES:
        if fnmatch.fnmatch(after, pat):
            return True
    return False

# 048b · 可疑气味 (needs_judgment): 不是硬违规, 送 GuardianAgent 复核. regex 用于**生成候选**.
# 设计原则 (2026-04-23 精修):
#   - 用"气味词 + 连接符"减少普通英语单词误中 (避免 "testify.py" 命中 test_)
#   - 排除规范命名的目录 (docs/ 下 _fix_plan.md / _final_report.md 是规范归档命名)
#   - 排除 tests/ (pytest 约定 test_ 前缀)
#   - 排除 registry 自动产出 (router_fix.*.json 是 service 注册文件)
_TEMP_SUSPICIOUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # agent 常见"试一次"命名 (单词开头)
    re.compile(r"^(scratch|tmp|temp|try|debug|wip|fixme|delete_me|sandbox|playground|oneshot|one_off|quick)_", re.IGNORECASE),
    # `foo_scratch_bar.py` `foo_debug.py` 这种中段嵌入
    re.compile(r"_(scratch|tmp|temp|debug|wip|fixme)(_|\.)", re.IGNORECASE),
    # 渐进试错版本 (要求 _new/_old/_v2 + 后面跟实义词, 避开 *_new.py 类正常命名里的 _new)
    re.compile(r"_v\d+_(bak|old|copy|temp|backup|draft)(\.|_|$)", re.IGNORECASE),
    re.compile(r"_(old|deprecated)_", re.IGNORECASE),
)

# 路径级豁免 (2026-04-24 收紧, plan §十二 极端情况审计):
# - **规范命名位置**豁免 (不是整个目录豁免), 仍严扣"气味"检测
# - docs/plans/*_PLAN.md / _FIX_PLAN.md / _final_report.md 是规范归档命名
# - docs/reports/ 下正式报告不视作气味
# - tests/ 下 test_* / conftest* / fixtures 是 pytest 规范; 其他命名照扫
# - data/services/registry/ 下自动产出 (router_fix.\*.json 等) 命名规律
_SUSPICIOUS_PATH_EXEMPTIONS_FULL: tuple[str, ...] = (
    "data/services/registry/",     # registry 服务自动产出命名规律 (确认合法)
)

# 针对特定文件模式的豁免 (glob, 相对 project_root)
_SUSPICIOUS_FILE_EXEMPTION_GLOBS: tuple[str, ...] = (
    # docs/plans/ 下的正式 plan 文档命名
    "docs/plans/*/*_PLAN.md",
    "docs/plans/*/*_FIX_PLAN.md",
    "docs/plans/*/*_plan.md",
    "docs/plans/*/*_final_report.md",
    "docs/plans/*/*_report.md",
    "docs/plans/*/HANDOFF.md",
    "docs/plans/*/PROGRESS.md",
    # docs/reports/ 下正式报告
    "docs/reports/*.md",
    "docs/reports/*/*.md",
    # tests/ 下 pytest 规范命名
    "tests/test_*.py",
    "tests/*/test_*.py",
    "tests/*/*/test_*.py",
    "tests/conftest.py",
    "tests/*/conftest.py",
    "tests/fixtures/**",
    "tests/**/fixtures/**",
)


def _is_suspicious_exempt(rel: str) -> bool:
    """判路径是否在 OMNI-048b 豁免清单内 (整前缀 + 文件 glob 两种)."""
    import fnmatch
    for pref in _SUSPICIOUS_PATH_EXEMPTIONS_FULL:
        if rel.startswith(pref):
            return True
    for pat in _SUSPICIOUS_FILE_EXEMPTION_GLOBS:
        if fnmatch.fnmatch(rel, pat):
            return True
    return False

# 跳过扫描的目录 (复用空目录的 _SKIP_DIR_NAMES + 归档 + vendors)
_TEMP_SKIP_DIR_NAMES = (_SKIP_DIR_NAMES - {"__pycache__"}) | {"vendors"}


def _path_matches_any_glob(name: str, patterns: Iterable[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(name, pat):
            return True
    return False


def _path_matches_any_regex(name: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    for pat in patterns:
        if pat.search(name):
            return True
    return False


def scan_temp_files(project_root: Path) -> list[str]:
    """OMNI-048a 硬扫: 返回命中硬模式的文件相对路径.

    路径豁免 (2026-04-24 收紧):
      - `.git/` 整体豁免 (git 内部)
      - `.omni/` 只豁免已知合法文件名 (manifest.yaml / sentinel.pid 等), 其余照扫
    """
    hits: list[str] = []
    for top in _SCAN_ROOTS:
        base = project_root / top
        if not base.exists():
            continue
        for p in _walk_excluding(base, _TEMP_SKIP_DIR_NAMES):
            if not p.is_file():
                continue
            rel = p.relative_to(project_root).as_posix()
            if _is_path_skip(rel):           # 防递归 (guardian 自己产出)
                continue
            if any(ex in rel for ex in _HARD_PATH_EXEMPTIONS):
                continue
            # .omni/ 内: 只豁免合法文件名
            if "/.omni/" in rel or rel.startswith(".omni/"):
                if _is_legal_omni_dir_file(rel):
                    continue
                # 非合法文件名 → 照抓硬模式
            if _path_matches_any_glob(p.name, _TEMP_HARD_PATTERNS):
                hits.append(rel)
    # 根目录下也扫一层
    try:
        for p in project_root.iterdir():
            if not p.is_file():
                continue
            rel = p.relative_to(project_root).as_posix()
            if _is_path_skip(rel):
                continue
            if any(ex in rel for ex in _HARD_PATH_EXEMPTIONS):
                continue
            if "/.omni/" in rel or rel.startswith(".omni/"):
                if _is_legal_omni_dir_file(rel):
                    continue
            if _path_matches_any_glob(p.name, _TEMP_HARD_PATTERNS):
                hits.append(rel)
    except (PermissionError, OSError):
        pass
    return sorted(set(hits))


def scan_suspicious_temp_candidates(project_root: Path) -> list[str]:
    """OMNI-048b 可疑候选: 返回"气味像临时品" 但不在硬模式内的文件相对路径.

    这些**不直接判违规**, 而是送给 GuardianAgent 复核 (延至 I-25 接入).
    返回结构稳定, 下游可以任何时机开始消费.

    路径级豁免 (2026-04-23 精修): docs/ / tests/ / data/services/registry/
    下的规范命名不进候选, 避免海量误伤.
    """
    candidates: list[str] = []
    for top in _SCAN_ROOTS:
        base = project_root / top
        if not base.exists():
            continue
        for p in _walk_excluding(base, _TEMP_SKIP_DIR_NAMES):
            if not p.is_file():
                continue
            rel = p.relative_to(project_root).as_posix()
            if _is_path_skip(rel):           # 防递归
                continue
            # 路径级豁免 (规范命名 + 整前缀, 2026-04-24 收紧)
            if _is_suspicious_exempt(rel):
                continue
            stem = p.name
            # 硬模式已经抓的不重复进候选
            if _path_matches_any_glob(stem, _TEMP_HARD_PATTERNS):
                continue
            if _path_matches_any_regex(stem, _TEMP_SUSPICIOUS_PATTERNS):
                candidates.append(rel)
    return sorted(set(candidates))


def _walk_excluding(base: Path, skip_names: Iterable[str]):
    """手写 walk 替代 rglob, 能在下钻时跳过指定目录名."""
    skip = set(skip_names)
    stack: list[Path] = [base]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except (PermissionError, OSError):
            continue
        for e in entries:
            if e.is_dir():
                if e.name in skip:
                    continue
                stack.append(e)
            else:
                yield e


# ══════════════════════════════════════════════════════════════
# OMNI-051 · data/ 分布式白名单 (双轨)
# ══════════════════════════════════════════════════════════════

# 占位词字典 (OMNI-051b 候选生成 · LLM 最终判): allowed_subdirs 描述含这些词视为可疑
_PLACEHOLDER_WORDS: tuple[str, ...] = (
    "tbd", "todo", "fixme", "misc", "其他", "杂项", "待补", "占位", "placeholder",
    "tmp purpose", "no purpose",
)

_DATA_SERVICES_ROOT = "data/services"
_SERVICES_ROOT_PARTS = ("src", "omnicompany", "packages", "services")
_DEFAULT_DATA_ROOT_ALLOWED_DIRS = frozenset({
    "_archive",
    "_runtime",
    "absorption",
    "domains",
    "services",
})
_DEFAULT_DATA_ROOT_ALLOWED_FILES = frozenset({
    "events.db",
    "ide_events.db",
    "type_embeddings_cache.json",
    "private_domain_nodes.db",
    "intent_traces.db",
})
_DEFAULT_DATA_ROOT_ALLOWED_FILE_PATTERNS = ("*.db-shm", "*.db-wal")


def _services_root(project_root: Path) -> Path:
    return project_root.joinpath(*_SERVICES_ROOT_PARTS)


def _load_data_root_policy(project_root: Path) -> tuple[set[str], set[str], tuple[str, ...]]:
    archmap = project_root / "docs" / "archmap.yaml"
    if not archmap.exists():
        return (
            set(_DEFAULT_DATA_ROOT_ALLOWED_DIRS),
            set(_DEFAULT_DATA_ROOT_ALLOWED_FILES),
            tuple(_DEFAULT_DATA_ROOT_ALLOWED_FILE_PATTERNS),
        )
    try:
        import yaml
    except ImportError:
        return (
            set(_DEFAULT_DATA_ROOT_ALLOWED_DIRS),
            set(_DEFAULT_DATA_ROOT_ALLOWED_FILES),
            tuple(_DEFAULT_DATA_ROOT_ALLOWED_FILE_PATTERNS),
        )
    try:
        data = yaml.safe_load(archmap.read_text(encoding="utf-8")) or {}
        data_rule = ((data.get("repo_root") or {}).get("data") or {})
    except Exception:
        return (
            set(_DEFAULT_DATA_ROOT_ALLOWED_DIRS),
            set(_DEFAULT_DATA_ROOT_ALLOWED_FILES),
            tuple(_DEFAULT_DATA_ROOT_ALLOWED_FILE_PATTERNS),
        )

    allowed_subdirs = data_rule.get("allowed_subdirs") or {}
    if isinstance(allowed_subdirs, dict):
        dirs = {str(name).rstrip("/") for name in allowed_subdirs.keys()}
    else:
        dirs = {str(name).rstrip("/") for name in allowed_subdirs}

    files = {
        str(name)
        for name in (data_rule.get("required_files") or [])
        if isinstance(name, str)
    }
    files.update(
        str(name)
        for name in (data_rule.get("allowed_files") or [])
        if isinstance(name, str)
    )
    patterns = tuple(
        str(pattern)
        for pattern in (data_rule.get("allowed_file_patterns") or [])
        if isinstance(pattern, str)
    )
    return (
        dirs or set(_DEFAULT_DATA_ROOT_ALLOWED_DIRS),
        files or set(_DEFAULT_DATA_ROOT_ALLOWED_FILES),
        patterns or tuple(_DEFAULT_DATA_ROOT_ALLOWED_FILE_PATTERNS),
    )


def scan_data_root_layout_violations(project_root: Path) -> list[dict[str, str]]:
    """OMNI-056: data/ first-level entries must match docs/archmap.yaml."""
    data_dir = project_root / "data"
    if not data_dir.exists() or not data_dir.is_dir():
        return []
    allowed_dirs, allowed_files, allowed_file_patterns = _load_data_root_policy(project_root)
    violations: list[dict[str, str]] = []
    try:
        entries = sorted(data_dir.iterdir(), key=lambda p: p.name)
    except (PermissionError, OSError):
        return violations
    for entry in entries:
        if entry.is_dir():
            if entry.name not in allowed_dirs:
                violations.append({
                    "path": f"data/{entry.name}",
                    "kind": "dir",
                    "name": entry.name,
                    "reason": "not in docs/archmap.yaml repo_root.data.allowed_subdirs",
                })
            continue
        if entry.is_file():
            if entry.name in allowed_files:
                continue
            if any(fnmatch.fnmatch(entry.name, pattern) for pattern in allowed_file_patterns):
                continue
            violations.append({
                "path": f"data/{entry.name}",
                "kind": "file",
                "name": entry.name,
                "reason": "not in docs/archmap.yaml repo_root.data allowed files",
            })
    return violations


def _read_data_layout(svc_dir: Path) -> dict[str, Any] | None:
    """读 services/<svc>/.omni/manifest.yaml 中 kind: data_layout 的 document.

    无 manifest / 无该 document → 返回 None.
    多个 data_layout document → 返回第一个 (并不应当出现, 后续可加 OMNI-051c 检测重复).
    """
    manifest = svc_dir / ".omni" / "manifest.yaml"
    if not manifest.exists():
        return None
    try:
        import yaml  # 项目已依赖
    except ImportError:
        return None
    try:
        text = manifest.read_text(encoding="utf-8")
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind") == "data_layout":
                return doc
    except Exception:
        return None
    return None


def scan_data_subdir_violations(project_root: Path) -> list[dict[str, str]]:
    """OMNI-051a 硬扫: data/services/<svc>/ 下未在声明里的 subdir/file → 违规.

    若 service 未声明 data_layout → **不报本规则** (等 OMNI-060 manifest-required).
    返回 list, 每条含 svc / path / kind (subdir|file).
    """
    violations: list[dict[str, str]] = []
    services_dir = project_root / _DATA_SERVICES_ROOT
    if not services_dir.exists():
        return violations
    src_services = _services_root(project_root)
    for svc_data_dir in sorted(services_dir.iterdir()):
        if not svc_data_dir.is_dir():
            continue
        svc_name = svc_data_dir.name
        if svc_name.startswith((".", "_")):
            continue
        svc_src_dir = src_services / svc_name
        if not svc_src_dir.exists():
            # 业务可能在其他位置 (如 services 改名), 暂不报
            continue
        layout = _read_data_layout(svc_src_dir)
        if layout is None:
            continue  # 未声明, OMNI-060 接入时报
        allowed_subdirs = set((layout.get("allowed_subdirs") or {}).keys())
        allowed_files = set(layout.get("required_files") or [])
        try:
            entries = list(svc_data_dir.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            name = entry.name
            if name in (".omni", "__pycache__"):
                continue
            if entry.is_dir() and name not in allowed_subdirs:
                violations.append({
                    "svc": svc_name,
                    "path": entry.relative_to(project_root).as_posix(),
                    "kind": "subdir",
                })
            elif entry.is_file() and name not in allowed_files:
                violations.append({
                    "svc": svc_name,
                    "path": entry.relative_to(project_root).as_posix(),
                    "kind": "file",
                })
    return violations


def scan_placeholder_data_layout_candidates(project_root: Path) -> list[dict[str, str]]:
    """OMNI-051b 可疑候选: data_layout 声明里 allowed_subdirs 描述像占位词 → 送 LLM 复核.

    返回 list, 每条含 svc / subdir_name / description.
    """
    candidates: list[dict[str, str]] = []
    src_services = _services_root(project_root)
    if not src_services.exists():
        return candidates
    for svc_src_dir in sorted(src_services.iterdir()):
        if not svc_src_dir.is_dir() or svc_src_dir.name.startswith(("_", ".")):
            continue
        layout = _read_data_layout(svc_src_dir)
        if layout is None:
            continue
        allowed = layout.get("allowed_subdirs") or {}
        if not isinstance(allowed, dict):
            continue
        for subdir_name, desc in allowed.items():
            desc_str = str(desc or "").strip().lower()
            if not desc_str:
                # 完全空描述 → 候选
                candidates.append({
                    "svc": svc_src_dir.name,
                    "subdir_name": str(subdir_name),
                    "description": "",
                })
                continue
            for ph in _PLACEHOLDER_WORDS:
                if ph in desc_str:
                    candidates.append({
                        "svc": svc_src_dir.name,
                        "subdir_name": str(subdir_name),
                        "description": str(desc),
                    })
                    break
    return candidates


# ══════════════════════════════════════════════════════════════
# OMNI-049 · 过期运行产物老化 (单轨, 时间硬判)
# ══════════════════════════════════════════════════════════════

import time


def _read_aging_policies(svc_dir: Path) -> list[dict[str, Any]]:
    """读 services/<svc>/.omni/manifest.yaml 中 kind: aging_policy 的 document.

    返回 list[{path_pattern, max_age_days, severity}]. 无 manifest / 无该 document → [].
    """
    manifest = svc_dir / ".omni" / "manifest.yaml"
    if not manifest.exists():
        return []
    try:
        import yaml
    except ImportError:
        return []
    try:
        text = manifest.read_text(encoding="utf-8")
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind") == "aging_policy":
                policies = doc.get("policies") or []
                # 规范化
                norm: list[dict[str, Any]] = []
                for p in policies:
                    if not isinstance(p, dict):
                        continue
                    pat = p.get("path_pattern")
                    age = p.get("max_age_days")
                    if not pat or not isinstance(age, (int, float)) or age <= 0:
                        continue
                    norm.append({
                        "path_pattern": str(pat),
                        "max_age_days": float(age),
                        "severity": str(p.get("severity") or "warn"),
                        "source_svc": svc_dir.name,
                    })
                return norm
    except Exception:
        return []
    return []


# 2026-05-08 立: scratch 目录默认 aging policy (用户原话 "scratch 可以相对宽容,
# 但要按一定规律自动整理"). 给 _scratch/ + data/_scratch/ + data/services/<svc>/scratch/
# 默认 30 天老化警告. 不删, 只 warn (§九 告警≠清理).
_DEFAULT_SCRATCH_AGING: list[dict[str, Any]] = [
    {
        "path_pattern": "_scratch/**/*",
        "max_age_days": 30,
        "severity": "warn",
        "source_svc": "_default_scratch_aging",
    },
    {
        "path_pattern": "data/_scratch/**/*",
        "max_age_days": 30,
        "severity": "warn",
        "source_svc": "_default_scratch_aging",
    },
    {
        "path_pattern": "data/services/*/scratch/**/*",
        "max_age_days": 30,
        "severity": "warn",
        "source_svc": "_default_scratch_aging",
    },
    {
        "path_pattern": "docs/_sandbox/**/*",
        "max_age_days": 30,
        "severity": "warn",
        "source_svc": "_default_scratch_aging",
    },
]


def _collect_all_aging_policies(project_root: Path) -> list[dict[str, Any]]:
    """汇总所有 service 的 aging_policy document + 默认 scratch 老化."""
    src_services = _services_root(project_root)
    all_policies: list[dict[str, Any]] = list(_DEFAULT_SCRATCH_AGING)
    if not src_services.exists():
        return all_policies
    for svc_src_dir in sorted(src_services.iterdir()):
        if not svc_src_dir.is_dir() or svc_src_dir.name.startswith(("_", ".")):
            continue
        all_policies.extend(_read_aging_policies(svc_src_dir))
    return all_policies


def scan_aging_items(
    project_root: Path,
    now_ts: float | None = None,
) -> list[dict[str, Any]]:
    """OMNI-049 硬扫: 按各 service 声明的 aging_policy 扫过期文件.

    返回 list, 每条含 path / age_days / max_age_days / severity / source_svc / policy_pattern.
    `now_ts` 注入供测试 (默认 time.time()).
    """
    items: list[dict[str, Any]] = []
    policies = _collect_all_aging_policies(project_root)
    if not policies:
        return items
    now = now_ts if now_ts is not None else time.time()
    seen: set[str] = set()

    for policy in policies:
        pattern = policy["path_pattern"]
        max_age_days = policy["max_age_days"]
        max_age_sec = max_age_days * 86400.0
        # glob pattern 相对 project_root. 支持 ** 递归.
        try:
            matched = list(project_root.glob(pattern))
        except (ValueError, OSError):
            continue
        for p in matched:
            if not p.is_file():
                continue
            rel = p.relative_to(project_root).as_posix()
            if _is_path_skip(rel):           # 防递归 (guardian 自己产出)
                continue
            # sidecar 与主文件共命运, 不单独报 (I-20 data-provenance 2026-04-23)
            if rel.endswith(".omni.json"):
                continue
            if rel in seen:
                continue
            try:
                mtime = p.stat().st_mtime
            except (PermissionError, OSError):
                continue
            age_sec = now - mtime
            if age_sec <= max_age_sec:
                continue
            seen.add(rel)
            items.append({
                "path": rel,
                "age_days": round(age_sec / 86400.0, 1),
                "max_age_days": max_age_days,
                "severity": policy["severity"],
                "source_svc": policy["source_svc"],
                "policy_pattern": pattern,
            })
    items.sort(key=lambda x: (-x["age_days"], x["path"]))
    return items


# ══════════════════════════════════════════════════════════════
# OMNI-050 · 数据体积异常告警 (单轨, 体积硬判)
# ══════════════════════════════════════════════════════════════

# 全局默认体积阈值 (Guardian 内建 · S-02 后迁 archmap.yaml 顶层 default_size_limits).
# 单位 MB. 越靠前的 pattern 优先匹配 (但 scan 会以累计最严为准, 避免 glob 重叠漏网).
_DEFAULT_SIZE_LIMITS: tuple[dict[str, Any], ...] = (
    # 主事件库: PROGRESS.md 2026-04-21 记录 9.5GB 事故防线
    {"path_pattern": "data/events.db",        "max_size_mb": 1024, "severity": "HIGH",   "source_svc": "__default__"},
    {"path_pattern": "data/ide_events.db",    "max_size_mb": 1024, "severity": "HIGH",   "source_svc": "__default__"},
    {"path_pattern": "data/*.db",             "max_size_mb": 500,  "severity": "HIGH",   "source_svc": "__default__"},
    {"path_pattern": "data/**/*.db",          "max_size_mb": 500,  "severity": "MEDIUM", "source_svc": "__default__"},
    {"path_pattern": "data/**/*.jsonl",       "max_size_mb": 200,  "severity": "MEDIUM", "source_svc": "__default__"},
    {"path_pattern": "logs/**/*",             "max_size_mb": 100,  "severity": "LOW",    "source_svc": "__default__"},
)


def _read_size_limits(svc_dir: Path) -> list[dict[str, Any]]:
    """读 services/<svc>/.omni/manifest.yaml 中 kind: size_limits 的 document.

    返回 list[{path_pattern, max_size_mb, severity, source_svc}].
    """
    manifest = svc_dir / ".omni" / "manifest.yaml"
    if not manifest.exists():
        return []
    try:
        import yaml
    except ImportError:
        return []
    try:
        text = manifest.read_text(encoding="utf-8")
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind") == "size_limits":
                raw = doc.get("limits") or []
                norm: list[dict[str, Any]] = []
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    pat = item.get("path_pattern")
                    sz = item.get("max_size_mb")
                    if not pat or not isinstance(sz, (int, float)) or sz <= 0:
                        continue
                    sev = str(item.get("severity") or "MEDIUM").upper()
                    if sev not in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}:
                        sev = "MEDIUM"
                    norm.append({
                        "path_pattern": str(pat),
                        "max_size_mb": float(sz),
                        "severity": sev,
                        "source_svc": svc_dir.name,
                    })
                return norm
    except Exception:
        return []
    return []


def _collect_all_size_limits(project_root: Path) -> list[dict[str, Any]]:
    """合并 Guardian 默认 + 所有 service manifest 声明的 size_limits."""
    limits: list[dict[str, Any]] = list(_DEFAULT_SIZE_LIMITS)
    src_services = _services_root(project_root)
    if src_services.exists():
        for svc_src_dir in sorted(src_services.iterdir()):
            if not svc_src_dir.is_dir() or svc_src_dir.name.startswith(("_", ".")):
                continue
            limits.extend(_read_size_limits(svc_src_dir))
    return limits


def scan_volume_alerts(project_root: Path) -> list[dict[str, Any]]:
    """OMNI-050 硬扫: 按 size_limits 扫超阈值文件.

    返回 list, 每条含 path / size_mb / max_size_mb / severity / source_svc / policy_pattern.
    同一文件被多个 policy 命中, 只报一次 (取最严 severity + 最严阈值比, 即 size_mb/max 最大).
    """
    limits = _collect_all_size_limits(project_root)
    if not limits:
        return []
    seen: dict[str, dict[str, Any]] = {}

    for policy in limits:
        pattern = policy["path_pattern"]
        max_size_bytes = policy["max_size_mb"] * 1024 * 1024
        try:
            matched = list(project_root.glob(pattern))
        except (ValueError, OSError):
            continue
        for p in matched:
            if not p.is_file():
                continue
            rel = p.relative_to(project_root).as_posix()
            if _is_path_skip(rel):           # 防递归 (guardian 自己产出)
                continue
            try:
                size_bytes = p.stat().st_size
            except (PermissionError, OSError):
                continue
            if size_bytes <= max_size_bytes:
                continue
            candidate = {
                "path": rel,
                "size_mb": round(size_bytes / (1024 * 1024), 2),
                "max_size_mb": policy["max_size_mb"],
                "severity": policy["severity"],
                "source_svc": policy["source_svc"],
                "policy_pattern": pattern,
                # 用超限比做严重度排序权重
                "_ratio": size_bytes / max_size_bytes,
            }
            existing = seen.get(rel)
            if existing is None or candidate["_ratio"] > existing["_ratio"]:
                seen[rel] = candidate
    items = list(seen.values())
    items.sort(key=lambda x: (-x["_ratio"], x["path"]))
    for it in items:
        it.pop("_ratio", None)
    return items


# ══════════════════════════════════════════════════════════════
# RULES 元数据 (供 HygieneScanWorker 读取)
# ══════════════════════════════════════════════════════════════

def _noop_check(ctx: FileContext) -> bool:
    """占位 check: 本家族规则走目录级扫描, 不走 per-file FileContext.

    RuleEngine 若误调此 check, 始终返回 False (不触发). 真实扫描由
    HygieneScanWorker.scan_empty_dirs() 等直接调用对应 scan_* 函数.
    """
    return False


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-047",
        name="empty-directory",
        severity="LOW",
        description=(
            "项目目录下存在递归为空的子目录 (排除 __pycache__/.git/venv 等系统目录 + "
            "_archive/_graveyard 归档). 空目录多为 agent 创建后中途失败遗留, "
            "属运行空间污染, 也是根因追溯的信号."
        ),
        check=_noop_check,  # 不走 RuleEngine, 见本模块注释
        disposition=["warn"],
        message_template=(
            "{path}: 空目录. 若为 agent 创建中途失败遗留, 应由清理设施 (I-27) 清除. "
            "若合理保留, 请在该目录置 README.md 或 .gitkeep 说明用途."
        ),
        certainty="absolute",
    ),
    GuardianRule(
        id="OMNI-048a",
        name="temp-file-hard",
        severity="MEDIUM",
        description=(
            "命中硬模式的临时文件残留: *.tmp/*.bak/*.orig/*.swp/~$*/.DS_Store/Thumbs.db "
            "/*_backup_*/*_old_*/*.BACKUP.*/Git 冲突残留等. 无歧义, 命中即违规."
        ),
        check=_noop_check,
        disposition=["warn"],
        message_template=(
            "{path}: 临时文件残留 (硬模式命中). 由清理设施 (I-27) 删除, "
            "或加 .gitignore 若是合法本地缓存."
        ),
        certainty="absolute",
    ),
    GuardianRule(
        id="OMNI-048b",
        name="temp-file-suspicious",
        severity="LOW",
        description=(
            "文件名气味像临时品但不在硬模式内 (scratch_/tmp_/_try*/_wip_/_debug/"
            "_new_approach/_v2_old 等). 送 GuardianAgent LLM 复核是否真的是一次性脚本, "
            "复核机制 I-25 接入 (当前只产候选清单)."
        ),
        check=_noop_check,
        disposition=["warn"],
        message_template=(
            "{path}: 文件名气味像临时品, 待 GuardianAgent 语义复核 (I-25 未接入前仅列候选). "
            "若是合法模块, 请改名去除 scratch/tmp/try/wip 等字样."
        ),
        certainty="needs_judgment",
    ),
    GuardianRule(
        id="OMNI-049",
        name="aging-items",
        severity="MEDIUM",
        description=(
            "按各 service .omni/manifest.yaml `kind: aging_policy` 声明, "
            "扫出超 max_age_days 的运行产物. 单轨时间硬判, 不走 LLM 复核. "
            "不删除, 只产老化清单 (§九 告警≠清理)."
        ),
        check=_noop_check,
        disposition=["warn"],
        message_template=(
            "{path}: 文件年龄 {age_days} 天, 超过 service {source_svc} 声明的 "
            "max_age_days={max_age_days}. 建议清理设施 (I-27) 按 severity 处置."
        ),
        certainty="absolute",
    ),
    GuardianRule(
        id="OMNI-050",
        name="volume-alert",
        severity="HIGH",
        description=(
            "按 Guardian 内建默认 + 各 service .omni/manifest.yaml `kind: size_limits` 声明, "
            "扫超体积阈值的文件. events.db 膨胀事故防线 (PROGRESS.md 2026-04-21 记录 9.5GB). "
            "Guardian 只告警, 主数据库清理需独立设施 (plan §九)."
        ),
        check=_noop_check,
        disposition=["warn"],
        message_template=(
            "{path}: 大小 {size_mb}MB, 超阈值 {max_size_mb}MB (policy from {source_svc}). "
            "主数据库清理需独立设施, 不能简单 rm. 参见 plan §九 / I-27."
        ),
        certainty="absolute",
    ),
    GuardianRule(
        id="OMNI-051a",
        name="data-subdir-undeclared",
        severity="MEDIUM",
        description=(
            "data/services/<svc>/ 下出现未在该 service .omni/manifest.yaml `kind: data_layout` "
            "document 声明的 subdir/file. 若 service 未声明 data_layout, 本规则不触发 "
            "(等 OMNI-060 manifest-required 统一报缺声明)."
        ),
        check=_noop_check,
        disposition=["warn"],
        message_template=(
            "{path}: data/services/{svc}/ 下存在未声明的 {kind}. "
            "在 src/.../services/{svc}/.omni/manifest.yaml 的 kind: data_layout "
            "document 里添加对应 allowed_subdirs/required_files 声明, 或清理此污染."
        ),
        certainty="absolute",
    ),
    GuardianRule(
        id="OMNI-056",
        name="data-root-closed-set",
        severity="HIGH",
        description=(
            "data/ first-level directories and files must match docs/archmap.yaml "
            "repo_root.data allowed_subdirs/required_files/allowed_files. This catches "
            "historical scratch/runtime drift before deeper hygiene scans recurse."
        ),
        check=_noop_check,
        disposition=["warn"],
        message_template=(
            "{path}: data/ top-level {kind} is outside docs/archmap.yaml closed set. "
            "Move it under data/_runtime, data/services/<svc>, data/domains/<domain>, "
            "or update archmap after human review."
        ),
        certainty="absolute",
    ),
    GuardianRule(
        id="OMNI-051b",
        name="data-layout-placeholder",
        severity="LOW",
        description=(
            "data_layout 声明的 allowed_subdirs 描述像占位词 (tbd/todo/misc/其他/空) → "
            "送 GuardianAgent 判是否糊弄 (I-25 接入, 当前只产候选)."
        ),
        check=_noop_check,
        disposition=["warn"],
        message_template=(
            "service {svc} 的 data_layout 中 subdir '{subdir_name}' 描述为 '{description}' · "
            "疑似占位, 待 GuardianAgent 复核. 请给 subdir 写具体用途."
        ),
        certainty="needs_judgment",
    ),
]


__all__ = [
    "RULES",
    "scan_empty_dirs",
    "scan_temp_files",
    "scan_suspicious_temp_candidates",
    "scan_data_root_layout_violations",
    "scan_data_subdir_violations",
    "scan_placeholder_data_layout_candidates",
    "scan_aging_items",
    "scan_volume_alerts",
    "_DEFAULT_SIZE_LIMITS",
    "_SCAN_ROOTS",
    "_SKIP_DIR_NAMES",
    "_TEMP_HARD_PATTERNS",
    "_TEMP_SUSPICIOUS_PATTERNS",
    "_PLACEHOLDER_WORDS",
]
