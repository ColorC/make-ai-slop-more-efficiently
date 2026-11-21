# [OMNI] origin=claude-code ts=2026-08-17T00:00:00Z type=module
# [OMNI] summary="LLM 人事系统 fs collector: 巡检坏产物(顶层游离/空文件/null命名/乱码路径名/异常体积)"
# [OMNI] why="P1 D棒契约 —— 文件系统只读巡检, 复用 guardian 闭集判据而非另发明一套顶层游离规则"
# [OMNI] tags=hr,fs,stray,guardian,hygiene
"""LLM 人事系统 —— 文件系统巡检 collector。

产出 `kind="stray"` 的 `HrEvent`，覆盖四类坏产物:
    1. 顶层游离   —— 复用 `guardian.rules.project_profile_hygiene.check_top_level_stray`
                      (`.omni/hygiene-profile.yaml` 登记的 closed_set_only 根的闭集判据)，
                      不自造第二套"哪些顶层目录合法"的规则。
    2. null/空文件 —— 0 字节文件, 以及文件名字面就是 null/nul/NUL 这类保留名。
    3. 乱码路径名 —— 含 U+FFFD 替换字符, 或含孤立代理项(surrogateescape 产物、
                      无法以严格 UTF-8 往返编码)的目录/文件名。
    4. 异常体积   —— 明显超出同类的巨大文件(阈值可配)。

设计约束(遵 P1-implementation-contract.md §三 全棒共同硬约束):
    * 扫描根可注入, 不硬编码 Path.home()。
    * 只提字段, 不复制内容。
    * 编码统一 utf-8 / errors=replace(路径名场景下体现为: 遇到解不开的名字只记录信号,
      不尝试"修正"或"重新解码猜测原文", 免得把 GBK 落盘事故二次伪造成另一种编码)。
    * 不吞异常当成功 —— 巡检过程中 stat/listdir 失败一律计入 `files_failed` + `failures` 样例。

已知 schema 缺口(**不自行处理，已在交付说明里向主会话提出**):
    `schema.PROVIDERS` 闭集只有五家 CLI (claude/codex/codebuddy/kiro/qoder)，没有给
    fs/vcs 这类"巡检类"来源留位置，即便 `Kind` 的文档字符串明确写着 stray 就是
    "文件系统巡检发现的坏产物"。本模块产出的 `provider` 字段填 `FS_EVENT_PROVIDER`
    ("fs")，这个值**不在** `PROVIDERS` 闭集内，因此 `validate_event()` 对这些事件
    必然报一条 "provider not in (...)" 问题 —— 这是如实反映 schema 现状，不是本模块
    的 bug，也不允许我们私自把它填成 "claude" 之类的假 provider 来蒙混过关
    (`P1-implementation-contract.md` 明令"不要硬塞一个不对的 provider")。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .schema import CollectResult, HrEvent

from ..guardian.rules.project_profile_hygiene import (
    check_top_level_stray,
    dangerous_bash_roots,
)

# ── provider/session 哨兵值(见模块 docstring 的"已知 schema 缺口") ──────────
FS_EVENT_PROVIDER = "fs"           # 不在 schema.PROVIDERS 闭集内，见上方说明
FS_SESSION_ID = "fs-scan"          # 契约指定的哨兵 session_id(fs 事件天生没有会话归属)

# ── 坏产物分类标签(写进 note，便于下游一眼看出"哪一类") ──────────────────
CATEGORY_TOP_LEVEL_STRAY = "top_level_stray"
CATEGORY_EMPTY_FILE = "empty_file"
CATEGORY_NULL_NAME = "null_name"
CATEGORY_MOJIBAKE_NAME = "mojibake_name"
CATEGORY_OVERSIZED_FILE = "oversized_file"

# 递归扫描时跳过的基础设施目录(与 guardian._iter_files_and_dirs 的排除集合同源思路)：
# 纯粹是为了不在 node_modules/.git 这类噪音里空转，不代表这些目录名本身合法与否
# (top-level 闭集判定完全交给 check_top_level_stray，不受本集合影响)。
_DEFAULT_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset({
    ".git", "node_modules", "venv", ".venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build",
})

# "nul"(3字符, 不分扩展名)是 Windows 保留设备名, 正常路径下建不出来(会被当成设备
# 特殊文件), 出现就异常, 更可能来自非 Windows 原生写入路径(WSL/远端同步/压缩包解出)。
# "null"(4字符)不是保留名, 完全合法可创建 —— 但也正因为合法, 它同时是很多正经软件
# 的模块名(例: keyring.backends.null.py、各类 no-op/null-object 实现), 真实语料
# 冒烟已实测命中一个第三方包的 null.py 误报。所以"null"只在【没有扩展名、整个文件名
# 字面就是 null】时才算(呼应契约原句"文件名字面就是 null"), 不看 stem —— "null.py"
# 这类带扩展名的合法模块不再误伤。
_RESERVED_DEVICE_STEMS: frozenset[str] = frozenset({"nul"})
_BARE_NULL_NAMES: frozenset[str] = frozenset({"null"})

# 200MB: 仓库/数据目录里绝大多数正常产物(代码、文档、小型图片、jsonl 快照)远小于此，
# 但足以抓到误落地的大型二进制/数据库导出/视频草稿。可通过 FsScanPolicy.large_file_bytes
# 覆盖 —— 不等同于 P0 测得的五家 CLI 语料体量(codex 单文件 1.66GB 是正常语料，不是坏产物)。
DEFAULT_LARGE_FILE_BYTES = 200 * 1024 * 1024


@dataclass
class FsScanPolicy:
    """fs 巡检的可调参数。全部有默认值，测试可用 tmp_path 完全覆盖。"""

    large_file_bytes: int = DEFAULT_LARGE_FILE_BYTES
    excluded_dir_names: frozenset[str] = _DEFAULT_EXCLUDED_DIR_NAMES
    # 传给 check_top_level_stray 的 project_root；None 时该函数会退化用
    # omni_workspace_root() 解析真实仓根 —— 测试必须显式传 tmp_path 下的
    # 伪 project_root，不要依赖这个隐式默认值(否则测试就不隔离了)。
    project_root: Path | str | None = None
    # root 命中 dangerous_bash_roots(即 root 本身就是 workspace/drive 这类巨大
    # 外部根)时，默认只做一层顶层游离检查、不递归 null/乱码/体积扫描(防扫炸，
    # 呼应 .omni/hygiene-profile.yaml 里 closed_set_only 根的设计原则)。
    # 显式传 True 才允许对这类根做全量递归。
    recurse_dangerous_roots: bool = False
    # 安全阀：递归扫描检查的条目数上限。None = 不限。超过时如实截断并记入
    # failures + watermark["truncated"]，不静默吞掉超出部分。
    max_entries: int | None = None


def _now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _record_failure(result: CollectResult, message: str) -> None:
    """failures 样例最多留 20 条(schema 约定)，超出后只默默不再追加(不抛异常)。"""
    if len(result.failures) < 20:
        result.failures.append(message)


def _make_stray_event(*, path: Path, category: str, note: str) -> HrEvent:
    """构造一条 kind=stray 的 HrEvent。

    evidence_ref 的 "#L<行号>" 对文件系统发现没有真实含义(不是逐行读出来的)，
    统一填 "#L1" 作为"这是整份文件/目录级别的发现，不是某一行"的诚实占位，
    不编造一个看起来精确、实则无意义的行号。
    """
    return HrEvent(
        ts=_now_iso(),
        provider=FS_EVENT_PROVIDER,
        session_id=FS_SESSION_ID,
        kind="stray",
        evidence_ref=f"{path}#L1",
        target=str(path),
        target_confidence="direct",
        verdict="unknown",
        note=f"{category}: {note}"[:500],
    )


def _is_null_like_name(name: str) -> bool:
    p = Path(name)
    stem_lowered = p.stem.lower()
    if stem_lowered in _RESERVED_DEVICE_STEMS:
        return True  # nul/NUL(不分扩展名) 是保留设备名, 出现即异常
    return name.lower() in _BARE_NULL_NAMES and not p.suffix


def _is_mojibake_name(name: str) -> tuple[bool, str]:
    """判一个路径分量名是否"无法用 UTF-8 正常解释、或含替换字符 U+FFFD"。

    两条判据(均来自契约原文，不额外发明第三条启发式)：
      1. 名字里含 U+FFFD —— 通常来自某处用 errors="replace" 解码坏字节后，
         把结果直接当文件名写回磁盘(GBK 落盘事故的典型二次产物)。
      2. 名字无法以 utf-8 strict 往返编码 —— 名字里含孤立代理项(lone surrogate,
         U+D800-DFFF)，这是 Python 用 surrogateescape 把"读不出的原始字节"
         装进 str 时的痕迹，同样指向"这个名字的真实来源不是合法 UTF-8"。

    只做这两条硬判据；"视觉上像乱码但其实是合法 Unicode 文本"(例如 GBK 字节被
    误当 Latin-1 解码、又重新编码成合法但语义错误的字符)不在可判定范围内 ——
    这种情况没有结构性信号可判，硬猜会引入新的编造，宁可漏检也不瞎猜。
    """
    if "\ufffd" in name:
        return True, "name contains U+FFFD replacement character"
    try:
        name.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return True, (
            "name contains lone surrogate codepoint(s) (surrogateescape artifact); "
            "cannot round-trip as strict UTF-8"
        )
    return False, ""


def _is_dangerous_root(root: Path, policy: FsScanPolicy) -> bool:
    """root 是否等于某个 closed_set_only 的"巨大外部根"(workspace/drive 之类)。

    复用 `dangerous_bash_roots`(本是给写入前 hook 判"别在这种根下相对写"用的)，
    这里借它的闭集来源判"扫描到这一层要不要收着点，别对着 E:\\ 递归"。
    """
    try:
        resolved = root.resolve()
    except OSError:
        resolved = root
    try:
        danger_roots = dangerous_bash_roots(policy.project_root)
    except Exception:
        danger_roots = []
    for candidate in danger_roots:
        try:
            if resolved == Path(candidate).resolve():
                return True
        except OSError:
            continue
    return False


def _scan_top_level_stray(root: Path, policy: FsScanPolicy, result: CollectResult) -> None:
    """对 root 的直接子项做一层闭集判定(不递归 —— 呼应 hygiene-profile 的设计)。"""
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        result.files_failed += 1
        _record_failure(result, f"cannot list top level of {root}: {exc}")
        return

    hits = 0
    for entry in entries:
        if entry.name in policy.excluded_dir_names:
            continue
        try:
            hit = check_top_level_stray(entry, policy.project_root)
        except Exception as exc:  # check_top_level_stray 本身已内部兜底永不抛，这里再兜一层防御
            _record_failure(result, f"check_top_level_stray raised on {entry}: {exc}")
            continue
        if hit is None:
            continue
        hits += 1
        result.events.append(
            _make_stray_event(
                path=entry,
                category=CATEGORY_TOP_LEVEL_STRAY,
                note=f"root={hit.get('root_label')}: {hit.get('message')}",
            )
        )
    result.watermark["stray_top_level_hits"] = hits


def _check_name_categories(path: Path, result: CollectResult) -> None:
    name = path.name
    if _is_null_like_name(name):
        result.events.append(
            _make_stray_event(
                path=path,
                category=CATEGORY_NULL_NAME,
                note=f"literal null-like name: {name!r}",
            )
        )
    mojibake, reason = _is_mojibake_name(name)
    if mojibake:
        result.events.append(
            _make_stray_event(path=path, category=CATEGORY_MOJIBAKE_NAME, note=reason)
        )


def _scan_recursive(root: Path, policy: FsScanPolicy, result: CollectResult) -> None:
    """递归巡检 null/空文件、乱码路径名、异常体积。

    files_seen/files_parsed/files_failed 只统计【文件】(不含目录)，与 schema
    既有的 CollectResult.coverage 语义(分母排除空文件、分子是成功检查的文件数)
    对齐；目录数单独记在 watermark["dirs_examined"]，不混进文件计数。
    """
    entries_examined = 0
    truncated = False

    def _bump_and_check_budget() -> bool:
        """返回 True 表示预算已耗尽，调用方应立即停止扫描。"""
        nonlocal entries_examined, truncated
        entries_examined += 1
        if policy.max_entries is not None and entries_examined > policy.max_entries:
            if not truncated:
                truncated = True
                result.watermark["truncated"] = True
                _record_failure(
                    result,
                    f"scan truncated at max_entries={policy.max_entries} under {root}",
                )
            return True
        return False

    def _on_walk_error(exc: OSError) -> None:
        result.files_failed += 1
        _record_failure(result, f"walk error under {root}: {exc}")

    dirs_examined = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=_on_walk_error):
        dirnames[:] = [d for d in dirnames if d not in policy.excluded_dir_names]
        dirpath_p = Path(dirpath)

        for dname in list(dirnames):
            if _bump_and_check_budget():
                result.watermark["dirs_examined"] = dirs_examined
                return
            dirs_examined += 1
            _check_name_categories(dirpath_p / dname, result)

        for fname in filenames:
            if _bump_and_check_budget():
                result.watermark["dirs_examined"] = dirs_examined
                return
            fpath = dirpath_p / fname
            result.files_seen += 1
            try:
                size = fpath.stat().st_size
            except OSError as exc:
                result.files_failed += 1
                _record_failure(result, f"stat failed: {fpath}: {exc}")
                continue

            result.files_parsed += 1
            if size == 0:
                result.files_empty += 1
                result.events.append(
                    _make_stray_event(path=fpath, category=CATEGORY_EMPTY_FILE, note="0-byte file")
                )
            if size > policy.large_file_bytes:
                result.events.append(
                    _make_stray_event(
                        path=fpath,
                        category=CATEGORY_OVERSIZED_FILE,
                        note=f"size={size} bytes > threshold={policy.large_file_bytes} bytes",
                    )
                )
            _check_name_categories(fpath, result)

    result.watermark["dirs_examined"] = dirs_examined


def scan_filesystem(root: Path, *, policy: FsScanPolicy | None = None) -> CollectResult:
    """巡检 root 下的四类坏产物，产出 CollectResult(kind="stray" 的 HrEvent 列表)。

    Args:
        root: 巡检根(测试传 tmp_path 下的 fixture，生产可传仓根/工作区根/盘根/
              omnicompany/data 等任意目录)。
        policy: 见 FsScanPolicy；缺省用其默认值。

    行为：
      * 顶层游离检查永远只做【一层】(root 的直接子项)，不管 root 有多大 ——
        这是复用 check_top_level_stray 本身的语义，不是本函数额外加的限制。
      * null/空文件、乱码路径名、异常体积三类需要递归；若 root 本身就命中
        dangerous_bash_roots(即 root 是 workspace/drive 这类巨大外部根)且
        policy.recurse_dangerous_roots 为 False(默认)，则跳过递归、只做
        上面的顶层游离检查，避免对着盘根/工作区根扫炸。
    """
    policy = policy or FsScanPolicy()
    root = Path(root)
    result = CollectResult(provider=FS_EVENT_PROVIDER)

    if not root.exists():
        _record_failure(result, f"scan root does not exist: {root}")
        result.files_failed += 1
        return result

    _scan_top_level_stray(root, policy, result)

    if _is_dangerous_root(root, policy) and not policy.recurse_dangerous_roots:
        result.watermark["recursive_scan_skipped_dangerous_root"] = str(root)
        return result

    _scan_recursive(root, policy, result)
    return result


__all__ = [
    "FS_EVENT_PROVIDER",
    "FS_SESSION_ID",
    "CATEGORY_TOP_LEVEL_STRAY",
    "CATEGORY_EMPTY_FILE",
    "CATEGORY_NULL_NAME",
    "CATEGORY_MOJIBAKE_NAME",
    "CATEGORY_OVERSIZED_FILE",
    "DEFAULT_LARGE_FILE_BYTES",
    "FsScanPolicy",
    "scan_filesystem",
]
