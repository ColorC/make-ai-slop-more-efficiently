# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-07-03T00:00:00Z type=config
# [OMNI] summary="Guardian 规则 OMNI-100 · testmap 同步提醒(MEDIUM): 改了 testmap 治下软件的源码但该软件的 testmap.yaml 与测试均未变化 → 每软件报一条; 没有 testmap 的目录不报。"
# [OMNI] why="上位计划 docs/plans/omnicompany-governance/[2026-07-03]FEATURE-TEST-LEDGER/plan.md「完成标准接线批」: testmap 契约层已建成, 但没有巡检提醒源码改了却没同步登记台账, 靠自觉会漂移。"
# [OMNI] tags=guardian,testmap,test-ledger,OMNI-100
# [OMNI] material_id="material:core.guardian.rules.testmap_sync.py"
"""Guardian 规则 · OMNI-100 · testmap 同步提醒(MEDIUM, warn)。

语义: 本次变更集(最近一次提交 + 未提交改动)里, 改了某个已有 testmap.yaml 治下软件的
源码, 但该软件的 testmap.yaml 与测试都没动 → 每个软件报一条 MEDIUM 提醒。没有
testmap.yaml 的目录不报(新软件的引导归理论覆盖再评管线, 不归本规则)。

全部确定性判定, 不调 LLM。变更集获取 (`_changed_paths`)、testmap 所属软件查找
(`_find_testmap_dir`)、testmap 测试锚加载 (`_load_testmap_test_anchors`) 均为模块级
可 monkeypatch 函数/缓存, 供测试注入。
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from ._base import FileContext, GuardianRule, _is_external, _is_scratch

logger = logging.getLogger(__name__)

# Windows 隐藏子进程窗口(本机硬规则: 起子进程一律不弹前台窗口)。
_CREATE_NO_WINDOW = 0x08000000

_TESTMAP_FILENAME = "testmap.yaml"


def _is_testmap_file(path: str) -> bool:
    return path.replace("\\", "/").rsplit("/", 1)[-1] == _TESTMAP_FILENAME


def _is_test_file(path: str) -> bool:
    p = path.replace("\\", "/")
    if "/tests/" in f"/{p}" or "/test/" in f"/{p}":
        return True
    name = p.rsplit("/", 1)[-1]
    if ".test." in name or ".spec." in name:
        return True
    return False


def _is_doc_file(path: str) -> bool:
    return path.replace("\\", "/").lower().endswith(".md")


# ── 变更集获取(可注入/可 monkeypatch 的测试缝) ──────────────────────────────

_changed_paths_cache: Optional[frozenset] = None


def _changed_paths() -> frozenset:
    """本次变更集: `git diff --name-only HEAD~1..HEAD` ∪ `git status --porcelain` 的路径,
    统一成 `/` 分隔的仓相对路径。进程生命周期缓存一次。

    git 历史不足一个提交、非 git 仓、或子进程失败均容错返回空集, 不抛异常。
    """
    global _changed_paths_cache
    if _changed_paths_cache is not None:
        return _changed_paths_cache

    paths: set[str] = set()

    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1..HEAD"],
            capture_output=True, text=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        if diff.returncode == 0:
            for line in diff.stdout.splitlines():
                line = line.strip()
                if line:
                    paths.add(line.replace("\\", "/"))
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        if status.returncode == 0:
            for line in status.stdout.splitlines():
                if not line.strip():
                    continue
                # `git status --porcelain` 格式: "XY <path>"(重命名为 "XY old -> new")
                rel = line[3:].strip()
                if " -> " in rel:
                    rel = rel.split(" -> ", 1)[1].strip()
                rel = rel.strip('"')
                if rel:
                    paths.add(rel.replace("\\", "/"))
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    _changed_paths_cache = frozenset(paths)
    return _changed_paths_cache


def _reset_cache_for_tests() -> None:
    """测试用: 重置全部模块级缓存(变更集/testmap 目录查找/testmap 加载/已报名单)。"""
    global _changed_paths_cache
    _changed_paths_cache = None
    _testmap_dir_cache.clear()
    _testmap_load_cache.clear()
    _reported_apps.clear()


# ── 所属软件查找(向上找最近一层含 testmap.yaml 的目录, 不出仓根) ────────────

_testmap_dir_cache: dict[str, Optional[str]] = {}


def _repo_root_from_ctx(ctx: FileContext) -> Optional[Path]:
    """从 ctx.abs_path 与 ctx.path 反推仓根(abs_path 去掉 path 后缀)。"""
    abs_path = Path(ctx.abs_path)
    rel_parts = Path(ctx.path.replace("\\", "/")).parts
    abs_parts = abs_path.parts
    if len(rel_parts) == 0 or len(rel_parts) > len(abs_parts):
        return None
    root_parts = abs_parts[: len(abs_parts) - len(rel_parts)]
    if not root_parts:
        return None
    return Path(*root_parts)


def _find_testmap_dir(ctx: FileContext) -> Optional[str]:
    """从 ctx.path 的目录逐级向上(不出仓根)找最近一层含 testmap.yaml 的目录,
    返回该目录的仓相对路径(`/` 分隔)。找不到返回 None。按目录做模块级缓存。
    """
    rel_path = ctx.path.replace("\\", "/")
    start_dir = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""

    if start_dir in _testmap_dir_cache:
        return _testmap_dir_cache[start_dir]

    root = _repo_root_from_ctx(ctx)
    if root is None:
        _testmap_dir_cache[start_dir] = None
        return None

    result: Optional[str] = None
    cur = start_dir
    while True:
        candidate = (root / cur / _TESTMAP_FILENAME) if cur else (root / _TESTMAP_FILENAME)
        if candidate.is_file():
            result = cur
            break
        if not cur:
            break
        cur = cur.rsplit("/", 1)[0] if "/" in cur else ""

    _testmap_dir_cache[start_dir] = result
    return result


# ── testmap 加载(解析 tests 锚, 坏表按"无锚"处理) ───────────────────────────

_testmap_load_cache: dict[str, list[str]] = {}


def _load_testmap_test_anchors(root: Path, testmap_dir: str) -> list[str]:
    """加载 testmap_dir/testmap.yaml, 返回其登记的所有 tests.file 锚(相对 testmap
    目录解析后的仓相对路径, `/` 分隔)。解析失败(坏表)按"无锚"处理, 不重复报
    (坏表是 verify_testmap 的事)。按 testmap 路径做模块级缓存。
    """
    if testmap_dir in _testmap_load_cache:
        return _testmap_load_cache[testmap_dir]

    testmap_path = (root / testmap_dir / _TESTMAP_FILENAME) if testmap_dir else (root / _TESTMAP_FILENAME)
    anchors: list[str] = []
    try:
        from omnicompany.packages.services._governance.testmap import load_testmap

        tm = load_testmap(testmap_path)
        base_dir_rel = Path(testmap_dir) if testmap_dir else Path(".")
        for feat in tm.features:
            for t in feat.tests:
                anchor_rel = (base_dir_rel / t.file).as_posix()
                anchors.append(anchor_rel)
    except Exception as e:  # noqa: BLE001 — 坏表按"无锚"处理, 继续
        logger.debug("OMNI-100 testmap 加载失败(按无锚处理): %s (%s)", testmap_path, e)

    _testmap_load_cache[testmap_dir] = anchors
    return anchors


# ── 每软件只报一次 ───────────────────────────────────────────────────────

_reported_apps: set[str] = set()


def _check_testmap_not_updated(ctx: FileContext) -> bool:
    if ctx.change_type not in ("A", "M"):
        return False
    if _is_external(ctx) or _is_scratch(ctx):
        return False
    if _is_testmap_file(ctx.path) or _is_test_file(ctx.path) or _is_doc_file(ctx.path):
        return False

    testmap_dir = _find_testmap_dir(ctx)
    if testmap_dir is None:
        return False

    if testmap_dir in _reported_apps:
        return False

    changed = _changed_paths()
    if not changed:
        return False

    testmap_file_rel = f"{testmap_dir}/{_TESTMAP_FILENAME}" if testmap_dir else _TESTMAP_FILENAME
    if testmap_file_rel in changed:
        return False

    root = _repo_root_from_ctx(ctx)
    anchors = _load_testmap_test_anchors(root, testmap_dir) if root is not None else []
    if any(a in changed for a in anchors):
        return False

    # testmap 目录子树内的测试文件是否在变更集里
    prefix = f"{testmap_dir}/" if testmap_dir else ""
    has_subtree_test_change = any(
        (not prefix or changed_path.startswith(prefix)) and _is_test_file(changed_path)
        for changed_path in changed
    )
    if has_subtree_test_change:
        return False

    _reported_apps.add(testmap_dir)
    return True


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-100",
        name="testmap-not-updated",
        severity="MEDIUM",
        description="改了 testmap 治下软件的源码但 testmap 与测试均无变化",
        check=_check_testmap_not_updated,
        disposition=["warn"],
        message_template=(
            "{path} 所属软件的源码有改动,但其 testmap.yaml 与测试均未更新。"
            "功能点或行为若有变化,请同步登记台账(omni testmap show <app> 查现状);"
            "纯重构不改行为可忽略本提醒。"
        ),
        certainty="absolute",
    ),
]

__all__ = ["RULES", "_reset_cache_for_tests"]
