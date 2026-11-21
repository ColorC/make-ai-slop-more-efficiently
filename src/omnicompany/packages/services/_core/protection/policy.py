# [OMNI] origin=ai-ide domain=services/_core/protection ts=2026-05-02T04:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="protection 策略 - 监控范围 + 白名单 + 配置文件"
# [OMNI] why="范围性开启 (用户硬规则) - 锁不是全局, 配 watched_paths 列受锁目录, whitelist_patterns 列豁免"
# [OMNI] tags=protection,policy,whitelist,scope
# [OMNI] material_id="material:core.protection.policy_loader.config_engine.py"
"""锁策略 - watched_paths + whitelist + policy 配置文件.

watched_paths 是相对项目根的目录前缀, 落在这些目录下的写入才检查违规.
whitelist_patterns 是 fnmatch 风格 glob, 匹配的文件豁免 (沙盒 / 系统 / __pycache__ /
.git / venv 等).

policy 文件位置: `.omni/protection_policy.json` (项目根).
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any


_POLICY_REL = ".omni/protection_policy.json"
_BASELINE_REL = ".omni/protection_baseline.json"


# ── 默认监控目录 (落在这里面的写入检查违规) ─────────────────────────
# 这些是 omnicompany 治理范围. 不在这里面的目录 (例如 venv / scripts/ 个人脚本) 不查.

DEFAULT_WATCHED_PATHS = (
    "src/omnicompany/",
    "docs/",
    "templates/",
    "data/services/",
    # "." = 仓库根的直接子文件(不递归)。对齐 Guardian OMNI-015 根禁区:
    # 此前外部直写仓库根没人实时拦(防线空档, 2026-06-13 二重权威调研坐实), 存量根文件靠 baseline 豁免。
    ".",
)


# ── 默认白名单 (匹配则豁免, 哪怕在 watched 内) ─────────────────────
# 系统文件 / 沙盒 / cache / 大概率合规的位置.

DEFAULT_WHITELIST_PATTERNS = (
    # 系统文件
    "**/.git/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.DS_Store",
    "**/node_modules/**",
    "**/venv/**",
    "**/.venv/**",
    # 沙盒 + 注册中心 + active session 文件
    ".omni/sandbox/**",
    ".omni/quarantine/**",
    ".omni/protection_policy.json",
    ".omni/sessions/**",
    "data/cc_session_active.json",
    "data/ide_events.db",
    "data/ide_events.db-*",
    "data/services/registry/**",
    "data/cc_hooks_audit.jsonl",
    "data/_scratch/**",
    # 归档区
    "**/_archive/**",
    "**/_graveyard/**",
    # 守护自己留下的指导文件 (handle external_write 后留的)
    "**/*.OMNI-EVICTED.md",
)


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[5]


def _policy_path() -> Path:
    return _project_root() / _POLICY_REL


def _baseline_path() -> Path:
    return _project_root() / _BASELINE_REL


def load_baseline() -> set[str]:
    """加载 baseline (grandfathered 路径集合)."""
    p = _baseline_path()
    if not p.is_file():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data.get("paths", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_baseline(paths: set[str]) -> Path:
    """保存 baseline."""
    p = _baseline_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"paths": sorted(paths), "version": 1}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return p


def is_in_baseline(rel_path: str, baseline: set[str] | None = None) -> bool:
    """判定路径是不是在 baseline."""
    if baseline is None:
        baseline = load_baseline()
    return rel_path in baseline


def _default_policy() -> dict[str, Any]:
    return {
        "enabled": False,
        "watched_paths": list(DEFAULT_WATCHED_PATHS),
        "whitelist_patterns": list(DEFAULT_WHITELIST_PATTERNS),
        "runtime_mode": "warn",   # warn | enforce | off (PreToolUse hook 用)
        "meta_io_rules": {
            # G4 灵活规则 (用户原话"什么目录扫/清/追根除") 跟元 IO 联动:
            "enforce_unregistered_tools": False,  # 切 True: tool 没声明元 IO 时 PreToolUse 阻断
            "enforce_unregistered_meta_io": False,  # 切 True: 调用未注册元 IO 时阻断
            "watched_meta_io_per_path": [],
            # 每条 dict: {path_prefix, allowed_meta_io: [...], mode: warn|enforce}
            # 例: {"path_prefix": "data/_writable/", "allowed_meta_io":
            #      ["meta_io.fs.create_file", "meta_io.fs.overwrite_file"],
            #      "mode": "enforce"}
        },
        "version": 1,
    }


def load_policy() -> dict[str, Any]:
    """加载 policy. 不存在则返回默认值. 旧版 policy 缺 runtime_mode 时自动补 warn."""
    p = _policy_path()
    if not p.is_file():
        return _default_policy()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_policy()
    # 向后兼容: 旧 policy 没 runtime_mode / meta_io_rules 字段
    if "runtime_mode" not in data:
        data["runtime_mode"] = "warn"
    if "meta_io_rules" not in data:
        data["meta_io_rules"] = {
            "enforce_unregistered_tools": False,
            "enforce_unregistered_meta_io": False,
            "watched_meta_io_per_path": [],
        }
    return data


def save_policy(policy: dict[str, Any]) -> Path:
    p = _policy_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def is_watched(file_path: str | Path, policy: dict | None = None) -> bool:
    """文件是不是落在 watched_paths 内 (相对项目根的前缀匹配)."""
    if policy is None:
        policy = load_policy()
    proj = _project_root()
    p = Path(file_path).resolve()
    try:
        rel = p.relative_to(proj)
    except ValueError:
        return False
    rel_str = str(rel).replace("\\", "/")
    for prefix in policy.get("watched_paths", []):
        if prefix in (".", "./"):
            # 仓库根直接子项, 不递归 (子目录归各自的 watched 前缀管)
            if "/" not in rel_str:
                return True
            continue
        if rel_str.startswith(prefix.rstrip("/") + "/") or rel_str == prefix.rstrip("/"):
            return True
    return False


def is_whitelisted(file_path: str | Path, policy: dict | None = None) -> bool:
    """文件是否匹配白名单 (fnmatch 风格 glob)."""
    if policy is None:
        policy = load_policy()
    proj = _project_root()
    p = Path(file_path).resolve()
    try:
        rel = p.relative_to(proj)
    except ValueError:
        return True  # 项目外, 不归我们管
    rel_str = str(rel).replace("\\", "/")
    for pat in policy.get("whitelist_patterns", []):
        if fnmatch.fnmatch(rel_str, pat):
            return True
        # ** 支持: glob 没原生支持, 加一层尝试
        norm_pat = pat.replace("**/", "*/").replace("/**", "/*")
        if fnmatch.fnmatch(rel_str, norm_pat):
            return True
        # 前缀匹配 (例如 .omni/sandbox/** 匹配 .omni/sandbox/foo/bar.md)
        if pat.endswith("/**"):
            prefix = pat[:-3].rstrip("/")
            if rel_str == prefix or rel_str.startswith(prefix + "/"):
                return True
    return False
