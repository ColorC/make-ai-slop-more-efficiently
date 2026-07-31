# [OMNI] origin=ai-ide domain=publish ts=2026-06-15T00:00:00Z type=lib status=active
# [OMNI] summary="AIWorkSpace 知识快照核心: 选明文(排图片/构建/二进制)+ 镜像进 gitee 暂存克隆 + 提交/推送。"
# [OMNI] why="把'收明文→镜像→git'的确定性逻辑收一处, routers 只做编排; 对外推送默认显式 dry_run 可预览。"
# [OMNI] tags=publish,backup,snapshot,git,aiworkspace
"""AIWorkSpace 明文知识快照 —— 确定性核心(无 LLM)。

口径(对齐 gitee aiworkspace-snapshot 既有快照): 收一切**明文文本**, 排图片/媒体/压缩/
二进制 + 构建/缓存/备份/临时目录; 逐文件二进制嗅探(含空字节即二进制); 单文件超上限当数据跳过。
镜像策略: 暂存克隆从远端分支拉下来 → 清空工作树(留 .git) → 把选中的明文文件铺进去 →
`git add -A` 让 git 自己算增删改 → 提交 → (非 dry_run)推送。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

# ── 默认配置(可被 CLI/env 覆盖)────────────────────────────────────────────
DEFAULT_SRC = os.environ.get("OMNI_AIWORKSPACE_ROOT", r"d:\P4\main\AIWorkSpace")
GITEE_URL = "https://git-host.example.com/user/omnifactory-private.git"
SNAPSHOT_BRANCH = "aiworkspace-snapshot"
DEFAULT_MAX_FILE_MB = 2  # 单文件超此当数据/产物跳过

# 目录黑名单: 构建/依赖/缓存/备份/临时/工具私有态 —— 不是"我写的明文知识"
EXCLUDE_DIRS = {
    ".git", ".hg", ".svn",
    ".ai", ".claude", ".omni", ".cursor", ".idea", ".vs", ".vscode",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".gradle",
    "node_modules", "bower_components", "vendor", "site-packages",
    "dist", "build", "out", "_build", ".next", ".nuxt", ".turbo", ".parcel-cache",
    "__pycache__", ".tox", ".venv", "venv", "env", ".eggs",
    "_backups", "temp", "tmp", ".tmp", "coverage", ".nyc_output",
    "Library", "Temp", "Logs", "obj",  # unity / dotnet 产物
    # 工具生成产物(用户 2026-06-15 定: 排掉 /data/ 段的 pipeline-runs/batch_research 等生成数据,
    # 只留我写的源码+设计文档+配置)。'data' 在 AIWorkSpace 下几乎全是 app/tool/*/data 工具输出。
    "data", "pipeline-runs", "pipeline_runs", "batch_research",
}
# 前缀黑名单(如 scratch.unity-cli)
EXCLUDE_DIR_PREFIXES = ("scratch",)
# 仅在根层排除(嵌套自指目录, 避免快照套快照)
EXCLUDE_DIRS_AT_ROOT = {"AIWorkSpace"}

# 扩展名黑名单: 图片/媒体/压缩/文档二进制/字体/可执行/数据库 —— 一律不收
DENY_EXT = {
    # 图片
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tga",
    ".tiff", ".tif", ".psd", ".ai", ".eps", ".heic", ".avif",
    # 媒体
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv",
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac",
    # 压缩 / 打包
    ".zip", ".7z", ".rar", ".gz", ".bz2", ".xz", ".tar", ".tgz", ".jar", ".war",
    # 文档二进制
    ".pdf", ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt",
    # 字体
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # 运行时日志 —— 非"我写的知识"
    ".log",
    # 可执行 / 编译产物 / 数据库 / 大数据
    ".exe", ".dll", ".so", ".dylib", ".a", ".o", ".lib", ".bin", ".pdb",
    ".pyc", ".pyo", ".class", ".obj",
    ".db", ".sqlite", ".sqlite3", ".mdb", ".pack", ".idx",
    # unity 资产二进制
    ".unity", ".asset", ".prefab", ".fbx", ".obj3d", ".anim", ".controller",
}


# ── git 小工具 ────────────────────────────────────────────────────────────
class GitError(RuntimeError):
    pass


def _git(args: list[str], cwd: Path, check: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess:
    """跑一条 git 命令。stderr/stdout 都捕获。check=True 时非 0 退出抛 GitError。"""
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} (rc={proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


# ── 明文选择 ──────────────────────────────────────────────────────────────
def _is_text_file(path: Path, sniff_bytes: int = 8192) -> bool:
    """二进制嗅探: 读头 8KB, 含空字节判二进制; 否则试 utf-8, 退而求其次看不可见字节占比。"""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sniff_bytes)
    except OSError:
        return False
    if not chunk:
        return True  # 空文件算明文
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        # 可能是末尾截断的多字节字符; 用不可见 ASCII 占比兜底
        text_ok = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D, 0x0C, 0x08}
        nontext = sum(1 for b in chunk if b < 0x80 and b not in text_ok)
        return (nontext / len(chunk)) < 0.30


def iter_text_files(src_root: Path, max_file_mb: int = DEFAULT_MAX_FILE_MB) -> tuple[list[str], dict[str, Any]]:
    """遍历 src_root, 返回 (选中相对路径[posix] 列表, 统计)。

    统计含: included / skipped_ext / skipped_binary / skipped_large / by_top(各顶层目录选中数)。
    """
    max_bytes = max_file_mb * 1024 * 1024
    included: list[str] = []
    stats = {"included": 0, "skipped_ext": 0, "skipped_binary": 0, "skipped_large": 0,
             "bytes": 0, "by_top": {}}

    for dirpath, dirnames, filenames in os.walk(src_root):
        here = Path(dirpath)
        rel_dir = here.relative_to(src_root)
        at_root = (rel_dir == Path("."))
        # 原地裁剪要进的子目录(os.walk 会据修改后的 dirnames 决定下钻)
        kept = []
        for d in dirnames:
            if d.startswith("."):  # 所有点目录(.git/.logs/.pids/.ai/.claude/.omni/.pytest_cache…)= 工具态/运行时, 非知识
                continue
            if d in EXCLUDE_DIRS:
                continue
            if any(d.startswith(p) for p in EXCLUDE_DIR_PREFIXES):
                continue
            if at_root and d in EXCLUDE_DIRS_AT_ROOT:
                continue
            kept.append(d)
        dirnames[:] = kept

        for fn in filenames:
            fp = here / fn
            ext = fp.suffix.lower()
            if ext in DENY_EXT:
                stats["skipped_ext"] += 1
                continue
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            if size > max_bytes:
                stats["skipped_large"] += 1
                continue
            if not _is_text_file(fp):
                stats["skipped_binary"] += 1
                continue
            rel = fp.relative_to(src_root).as_posix()
            included.append(rel)
            stats["included"] += 1
            stats["bytes"] += size
            top = rel.split("/")[0] if "/" in rel else "(root)"
            stats["by_top"][top] = stats["by_top"].get(top, 0) + 1

    included.sort()
    return included, stats


# ── 镜像 + git ────────────────────────────────────────────────────────────
def ensure_staging_clone(staging_dir: Path, remote_url: str, branch: str) -> str:
    """暂存克隆与远端分支对齐。返回状态描述。

    首次: init + 浅 fetch 远端分支 + checkout(分支不存在则建空树)。
    复用: fetch + hard reset 到远端分支 tip(避免本地分叉)。
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    git_dir = staging_dir / ".git"
    if not git_dir.exists():
        _git(["init", "-q"], cwd=staging_dir)
        _git(["remote", "add", "origin", remote_url], cwd=staging_dir)
    else:
        # 确保 remote url 正确(可能换过)
        _git(["remote", "set-url", "origin", remote_url], cwd=staging_dir, check=False)
    # Windows 长路径(深层生成路径 > 260 字符会让 git add 崩)+ CJK 路径不转义
    _git(["config", "core.longpaths", "true"], cwd=staging_dir, check=False)
    _git(["config", "core.quotepath", "false"], cwd=staging_dir, check=False)
    _git(["config", "core.autocrlf", "false"], cwd=staging_dir, check=False)

    fetched = _git(["fetch", "--depth", "1", "origin", branch], cwd=staging_dir, check=False)
    if fetched.returncode == 0:
        _git(["checkout", "-B", branch, "FETCH_HEAD"], cwd=staging_dir)
        _git(["reset", "--hard", "FETCH_HEAD"], cwd=staging_dir)
        return f"对齐远端 origin/{branch}"
    # 远端没这个分支 → 建一个干净的孤儿分支
    _git(["checkout", "--orphan", branch], cwd=staging_dir, check=False)
    return f"远端无 {branch}, 新建空分支"


def _iter_staging_rel(staging_dir: Path):
    """暂存树里现有文件的相对 posix 路径(跳过 .git)。"""
    for dirpath, dirnames, filenames in os.walk(staging_dir):
        if ".git" in dirnames:
            dirnames.remove(".git")
        here = Path(dirpath)
        for fn in filenames:
            yield (here / fn).relative_to(staging_dir).as_posix()


def mirror_files(staging_dir: Path, src_root: Path, rel_files: list[str]) -> int:
    """增量镜像: 选中明文集成为暂存树唯一真相 —— 删多余、拷新增/变更(按 size+mtime 跳过未变)。

    首次(暂存树还是旧快照)几乎全拷; 之后只拷增量, 再跑就快。返回实际拷贝数。
    """
    want = set(rel_files)
    # 1) 删暂存树里不再要的文件
    for rel in list(_iter_staging_rel(staging_dir)):
        if rel not in want:
            try:
                (staging_dir / rel).unlink()
            except OSError:
                pass
    # 2) 拷新增/变更
    copied = 0
    for rel in rel_files:
        src = src_root / rel
        dst = staging_dir / rel
        try:
            s = src.stat()
        except OSError:
            continue
        if dst.exists():
            try:
                d = dst.stat()
                if d.st_size == s.st_size and int(d.st_mtime) >= int(s.st_mtime):
                    continue  # 未变, 跳过
            except OSError:
                pass
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            copied += 1
        except OSError:
            pass
    # 3) 清掉空目录(删文件后可能留空壳)
    for dirpath, dirnames, filenames in os.walk(staging_dir, topdown=False):
        if ".git" in Path(dirpath).parts:
            continue
        if dirpath == str(staging_dir):
            continue
        if not filenames and not dirnames:
            try:
                Path(dirpath).rmdir()
            except OSError:
                pass
    return copied


def stage_and_diff(staging_dir: Path) -> dict[str, Any]:
    """git add -A 后, 算相对 HEAD 的 name-status 增删改统计。"""
    _git(["add", "-A"], cwd=staging_dir)
    # HEAD 可能不存在(孤儿首提交)
    has_head = _git(["rev-parse", "--verify", "HEAD"], cwd=staging_dir, check=False).returncode == 0
    added = modified = removed = 0
    if has_head:
        ns = _git(["diff", "--cached", "--name-status"], cwd=staging_dir, check=False).stdout
    else:
        ns = _git(["diff", "--cached", "--name-status", "--no-renames"], cwd=staging_dir, check=False).stdout
    for line in ns.splitlines():
        code = line[:1]
        if code == "A":
            added += 1
        elif code == "M":
            modified += 1
        elif code == "D":
            removed += 1
        elif code == "R":
            modified += 1
    return {"added": added, "modified": modified, "removed": removed,
            "total_changes": added + modified + removed, "has_head": has_head}


def commit_and_push(staging_dir: Path, branch: str, message: str, push: bool) -> dict[str, Any]:
    """提交暂存内容; push=True 则推到 origin/<branch>。返回结果。"""
    diff = stage_and_diff(staging_dir)
    if diff["total_changes"] == 0:
        return {"committed": False, "pushed": False, "reason": "无变更", **diff}
    # 提交身份兜底(暂存仓没配 user 时 commit 会失败)
    _git(["config", "user.name", "omni-backup"], cwd=staging_dir, check=False)
    _git(["config", "user.email", "omni-backup@local"], cwd=staging_dir, check=False)
    _git(["commit", "-q", "-m", message], cwd=staging_dir)
    sha = _git(["rev-parse", "--short", "HEAD"], cwd=staging_dir, check=False).stdout.strip()
    result = {"committed": True, "sha": sha, **diff}
    if not push:
        result["pushed"] = False
        result["reason"] = "本地已提交(未推送)"
        return result
    pushed = _git(["push", "origin", f"{branch}:{branch}"], cwd=staging_dir, check=False)
    result["pushed"] = pushed.returncode == 0
    if pushed.returncode != 0:
        result["push_error"] = (pushed.stderr.strip() or pushed.stdout.strip())[:500]
    return result


def snapshot_message(src_root: Path, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"AIWorkSpace 知识快照 ({now.strftime('%Y-%m-%d %H:%M')}) · {src_root.name}"
