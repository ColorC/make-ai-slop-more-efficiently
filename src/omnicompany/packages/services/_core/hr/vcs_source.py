# [OMNI] origin=claude-code ts=2026-08-17T00:00:00Z type=module
# [OMNI] summary="LLM 人事系统 vcs collector: git 批量查询 + 缓存, 供 E棒 blame.py 做四档归因消费"
# [OMNI] why="P1 D棒契约 —— P0 实测本仓是攒批 checkpoint 提交(单次commit横扫7604文件,滞后写入~1.5天),
#            禁止逐条写事件调一次git; 必须按天/按路径批量拉一次并落缓存。2026-08-17 追加: 主会话通知
#            E棒 blame.py 消费侧接口形状已定死(commit_index 可调用/字典二态 + normcase(normpath) 归一化),
#            本模块的公开产出必须照那个形状对齐, 否则集成对不上"
# [OMNI] tags=hr,vcs,git,attribution,cache
"""LLM 人事系统 —— git 批量查询与缓存 collector。

**本模块不产出 HrEvent。** `kind="commit"` 事件由 E 棒 `hr/blame.py` 在做四档归因时
产出；E 棒的 vcs 档恒把 `session_id`/`actor_id` 置空字符串(红线, 有测试锁定)，
所以本模块从设计上就不产出、也不该产出任何会话/actor 线索 —— 塞了也会被丢弃，
反而会诱导后来的人误用。本模块只负责回答"这个文件在哪些提交里出现过、各是哪一天"。

硬事实(来自 `P0-coverage-report.md` V2，本模块的设计前提):
    * 本仓 git 是**攒批 checkpoint 提交** —— 一次 commit 可以横扫 7604 个文件，
      比对应写入事件滞后约 1.5 天。
    * 因此时间窗**必须按天设计**，按小时/分钟会大量失配。
    * **不能用一次提交反推是哪个会话干的** —— 同一时间窗内可能有几十个无关会话
      的改动混在同一次提交里。命中一个文件 = "文件级 + 天级时间窗" 精度，
      不能当作接近 `direct` 档的强证据；本模块把这个上限做成 `precision` 字段，
      不让调用方误以为能定位到会话。
    * `git log`/`git show` 单次调用实测 1~2 秒 —— **必须批量 + 缓存**，
      禁止"每条写事件调一次 git"。设计成 `build_commit_index()` 对整个时间窗
      跑**一次** `git log --name-only`，建 path -> 提交列表 的索引；后续
      查询全部是纯内存查询，不再起 git 子进程。
    * `.gitignore` 排除的路径(P0 实测 `data/domains/*` 被排除)属于"正常未提交"，
      不是漏报 —— 用 `git check-ignore -v` **批量**判定 + 缓存
      (`check_ignored_batch`)，同样不逐路径起进程。

消费侧接口形状(2026-08-17 主会话通知，E 棒 `hr/blame.py` 已按此接好，不得再改):
    `build_commit_index()` 的返回值 `CommitIndex` 本身**同时**是:
      * 一个字典: `index[normalized_path]` / `index.get(normalized_path, [])`
      * 一个可调用对象: `index(normalized_path) -> list[dict]`
    key 必须是 `_normalize(path)` 的输出 —— 即 `os.path.normcase(os.path.normpath(path))`，
    和 E 棒消费侧的归一化逐字节一致，否则 Windows 上大小写/斜杠方向差异会让 vcs 档
    静默失配(这种失配没有异常、没有报错，测试不覆盖到就发现不了)。
    每个 value 是 `list[dict]`，每条 dict 至少含 `"commit"`(提交哈希)、
    `"day"`(ISO 日期 `"YYYY-MM-DD"`，不是时间戳)，可选 `"files_changed"`
    (这次提交横扫了多少文件 —— 只透传原始事实，不替调用方下"是不是攒批"的判断)。
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

# ── 精度声明(闭集，供 lookup() 结果字段使用；不是 schema.py 的值域，纯本模块内部) ──
PRECISION_FILE_DAY = "file+day"      # 命中：文件级 + 天级时间窗，不代表定位到具体会话
PRECISION_GITIGNORED = "gitignored"  # 路径命中 .gitignore，未提交是正常现象
PRECISION_NOT_FOUND = "not_found"    # 索引窗口内该文件没有任何提交记录
PRECISION_UNKNOWN = "unknown"        # ts 无法解析，或索引本身构建失败

_COMMIT_MARK = "@@OMNI_HR_COMMIT@@"


def _now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _normalize(path: str | Path) -> str:
    """路径归一化 —— **必须和 E 棒 `hr/blame.py` 消费侧完全一致**。

    实现就是 `os.path.normcase(os.path.normpath(path))`，故意不做仓库相对化、
    不做大小写以外的任何"聪明"处理 —— 消费侧拿到的是 `HrEvent.target` 这类
    已经是绝对路径的字符串，直接照同一个函数处理才能保证 Windows 上大小写/
    正反斜杠不同的同一路径能命中同一条索引。**幂等**：对已经归一化过的字符串
    再调一次结果不变，所以 `CommitIndex.__call__` 可以放心地防御性再归一化一次。
    """
    return os.path.normcase(os.path.normpath(str(path)))


def _to_absolute(path: str | Path, repo_root: Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path(repo_root) / p


def _repo_relative_posix(path: str | Path, repo_root: Path) -> str:
    """仓库相对、正斜杠路径 —— `git check-ignore` 天然吃这种形式。

    这是单独一套归一化，只服务 `check_ignored_batch` 的缓存键，跟上面
    `_normalize()`(E 棒消费侧的绝对路径 + normcase 契约)是两件不同的事，
    不要混用。
    """
    p = Path(path)
    root = Path(repo_root)
    if p.is_absolute():
        try:
            root_r = root.resolve()
        except OSError:
            root_r = root
        try:
            p_r = p.resolve()
        except OSError:
            p_r = p
        try:
            p = p_r.relative_to(root_r)
        except ValueError:
            try:
                p = p.relative_to(root)
            except ValueError:
                pass
    return str(p).replace("\\", "/")


class CommitIndex(dict):
    """`build_commit_index()` 的产出：path -> 提交列表，**同时**是字典和可调用对象。

    * 字典用法: `index[_normalize(abs_path)]` / `index.get(key, [])`。
    * 可调用用法: `index(normalized_path) -> list[dict]`(内部会防御性再 `_normalize()`
      一次，传已经归一化过的字符串是幂等安全的)。

    额外携带的诊断属性(不影响上面两种"官方形状"，纯供本模块自己的 `lookup()`/
    测试/交付说明使用): `repo_root`/`since`/`until`/`built_at`/`commit_count`/
    `build_seconds`/`build_error`。

    每个 value 里的 dict 元素至少含 `"commit"`、`"day"`，可选 `"files_changed"`。
    **不含任何会话/actor 字段** —— 见模块 docstring。
    """

    def __init__(self, *, repo_root: Path, since: str | None, until: str | None):
        super().__init__()
        self.repo_root = Path(repo_root)
        self.since = since
        self.until = until
        self.built_at = _now_iso()
        self.commit_count = 0
        self.build_seconds = 0.0
        self.build_error = ""  # 非空表示 git log 本身失败；此时字典部分会是空的

    def __call__(self, normalized_path: str) -> list[dict]:
        return self.get(_normalize(normalized_path), [])

    def precision_notice(self) -> str:
        return (
            "本索引精度上限 = 文件级 + 天级时间窗；本仓提交习惯是攒批 checkpoint"
            "(单次提交可横扫数千文件，滞后写入约 1.5 天)。命中一次提交不代表能"
            "反推是哪个会话/哪次改动干的 —— 同一天时间窗内可能有几十个无关会话"
            "的改动混在同一提交里。"
        )


@dataclass
class LookupResult:
    """`lookup()` 的返回值：匹配结果 + 精度声明，供调用方判档用。

    这是本模块自带的高层便捷封装(接受相对/绝对路径，做天级邻近过滤 +
    可选 .gitignore 归类)；E 棒 blame.py 直接消费的是上面 `CommitIndex`
    本身(字典/可调用二态)，不经过这层。两者共用同一份底层索引数据。
    """

    path: str
    matched: bool
    precision: str
    commits: list[dict] = field(default_factory=list)
    note: str = ""


def _run_git(args: list[str], cwd: Path, *, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def build_commit_index(
    repo_root: Path,
    *,
    since: str | None = None,
    until: str | None = None,
    timeout: float = 120.0,
) -> CommitIndex:
    """对 repo_root 跑**一次**批量 `git log --name-only`，建立 path -> 提交列表 索引。

    Args:
        repo_root: git 仓库根(或其任意子目录 —— git 会自己找到 `.git`)。
        since/until: 传给 `git log --since=/--until=` 的原始字符串(接受 git 能
                     理解的任何形式，如 "2026-08-01"、"3 days ago")。按天设计，
                     不建议传小时级参数(见模块 docstring 的硬事实)。
        timeout: 单次 git log 调用的超时秒数。P0 实测本仓单次 log/show 是
                 1~2 秒量级；15GB 全语料外推下 git 交叉核对本身可能比 JSON
                 解析更耗时，超时给足余量。

    Returns:
        CommitIndex(dict 子类，见类文档)。若 git log 本身失败(非 git 仓/
        git 不可用/超时)，`build_error` 非空、索引为空字典 —— 调用方必须
        检查 `build_error`，不能把"索引为空"直接当成"这段时间没人提交过"。
    """
    started = time.monotonic()
    repo_root = Path(repo_root)
    args = ["log", "--name-only", f"--pretty=format:{_COMMIT_MARK}%H|%at|%s"]
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")

    index = CommitIndex(repo_root=repo_root, since=since, until=until)

    try:
        proc = _run_git(args, repo_root, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        index.build_error = f"git log failed to run: {exc}"
        index.build_seconds = time.monotonic() - started
        return index

    if proc.returncode != 0:
        index.build_error = f"git log rc={proc.returncode}: {proc.stderr.strip()[:500]}"
        index.build_seconds = time.monotonic() - started
        return index

    current: dict | None = None
    current_files: list[str] = []

    def _flush() -> None:
        nonlocal current, current_files
        if current is None:
            return
        record = {
            "commit": current["commit"],
            "day": current["day"],
            "files_changed": len(current_files),
            # 下面两个不是契约要求, 纯诊断用, 消费方可以忽略。
            "day_iso": current["day_iso"],
            "subject": current["subject"],
        }
        for rel_path in current_files:
            key = _normalize(repo_root / rel_path)
            index.setdefault(key, []).append(record)
        index.commit_count += 1

    for line in proc.stdout.splitlines():
        if line.startswith(_COMMIT_MARK):
            _flush()
            current_files = []
            body = line[len(_COMMIT_MARK):]
            parts = body.split("|", 2)
            if len(parts) != 3:
                current = None
                continue
            commit_hash, epoch_s, subject = parts
            try:
                epoch = int(epoch_s)
            except ValueError:
                current = None
                continue
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
            current = {
                "commit": commit_hash,
                "day": dt.strftime("%Y-%m-%d"),
                "day_iso": dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "subject": subject,
            }
        elif line.strip():
            if current is not None:
                current_files.append(line.strip().replace("\\", "/"))
    _flush()

    index.build_seconds = time.monotonic() - started
    return index


def check_ignored_batch(
    repo_root: Path,
    paths: Iterable[str | Path],
    *,
    cache: dict[str, bool] | None = None,
    timeout: float = 60.0,
) -> dict[str, bool]:
    """批量判定 paths 是否被 `.gitignore` 排除，一次 `git check-ignore -v --stdin` + 缓存。

    P0 实测 `data/domains/*` 被 `.gitignore` 排除，这类"未提交"是正常现象、不是
    漏报；`lookup()` 用这里产出的 cache 把 not_found 细分成 gitignored /
    genuinely-not-found。

    Args:
        paths: 绝对或仓库相对路径均可，内部统一转成仓库相对正斜杠形式
               (`_repo_relative_posix`)作为缓存键 —— 与 `CommitIndex` 的
               `_normalize()` 键是两套不同的归一化，互不影响。
        cache: 复用调用方已有的缓存 dict；省略则新建一个。已在 cache 里的
               (归一化后的)路径不会被重新查询 —— **这就是"缓存命中不再起
               子进程"的落点**。

    Returns:
        更新后的 cache(可能就是传入的同一个 dict 对象)，键 = 仓库相对正斜杠路径。
    """
    cache = cache if cache is not None else {}
    repo_root = Path(repo_root)
    rel_by_input = {p: _repo_relative_posix(p, repo_root) for p in paths}
    to_query = sorted({rel for rel in rel_by_input.values() if rel not in cache})
    if not to_query:
        return cache

    # 特意不走 `_run_git`(text=True): Windows 下 text 模式管道会把喂给 stdin 的
    # "\n" 翻译成 "\r\n"，git 会把那个多出来的 "\r" 当成路径的一部分，导致输出
    # 被加上一层 C 风格转义引号(`"path\r"`)而不再是能简单按 tab 切开的裸路径。
    # 用二进制管道自己控制换行, 绕开这个陷阱。
    proc = subprocess.run(
        ["git", "check-ignore", "-v", "--stdin"],
        cwd=str(repo_root),
        input=("\n".join(to_query) + "\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    stdout_text = proc.stdout.decode("utf-8", errors="replace")
    # rc=1 是"一个都没命中"的正常返回，不是错误；只有 >1 才是真失败(git 版本/参数问题)。
    ignored: set[str] = set()
    if proc.returncode in (0, 1):
        for line in stdout_text.splitlines():
            if "\t" in line:
                _pattern_part, matched_path = line.split("\t", 1)
                # git 对含特殊字符的路径会加 C 风格转义引号；正常路径不会触发，
                # 这里只做最基础的裸引号剥除，不做完整反转义(超出本模块场景)。
                matched_path = matched_path.strip().strip('"')
                ignored.add(matched_path.replace("\\", "/"))

    for rel in to_query:
        cache[rel] = rel in ignored
    return cache


def _parse_ts_to_date(ts: str) -> date:
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date()


def _parse_day(day_str: str) -> date:
    return datetime.strptime(day_str, "%Y-%m-%d").date()


def lookup(
    index: CommitIndex,
    path: str | Path,
    ts: str,
    *,
    window_days: int = 3,
    gitignore_cache: dict[str, bool] | None = None,
) -> LookupResult:
    """查 path 在 ts 附近(±window_days 天)有没有命中索引里的提交。

    这是本模块自带的便捷封装，接受相对或绝对路径(内部用 `index.repo_root`
    补全再 `_normalize()`)。E 棒 `hr/blame.py` 不经过这层，直接用
    `index[key]` / `index(key)` —— 两条路径读的是同一份底层数据，不会打架。

    Args:
        window_days: 天级邻近窗口(默认 ±3 天，覆盖 P0 实测的 ~1.5 天滞后，
                     留出余量)。**不是精确匹配** —— precision 字段永远是
                     `file+day` 或更粗，调用方不能把命中当成分钟/秒级证据。
        gitignore_cache: 传入 `check_ignored_batch()` 建好的缓存，用于把
                         "索引里找不到"细分成"正常被 .gitignore 排除"还是
                         "真的没查到"。不传就不做这层区分(仍返回 not_found)。
                         **本函数本身绝不起 git 子进程** —— 批量查询的职责
                         完全在 `build_commit_index` / `check_ignored_batch`，
                         这里只读内存。
    """
    if index.build_error:
        return LookupResult(
            path=str(path),
            matched=False,
            precision=PRECISION_UNKNOWN,
            note=f"index build failed, cannot answer: {index.build_error}",
        )

    abs_path = _to_absolute(path, index.repo_root)
    key = _normalize(abs_path)
    records = index.get(key, [])
    gitignore_key = _repo_relative_posix(abs_path, index.repo_root)

    if not records:
        if gitignore_cache is not None and gitignore_cache.get(gitignore_key):
            return LookupResult(
                path=key,
                matched=False,
                precision=PRECISION_GITIGNORED,
                note="path matches .gitignore; absence from git history is expected, not a miss. "
                + index.precision_notice(),
            )
        return LookupResult(
            path=key,
            matched=False,
            precision=PRECISION_NOT_FOUND,
            note="no commit touched this path within the indexed window. " + index.precision_notice(),
        )

    try:
        target_day = _parse_ts_to_date(ts)
    except (ValueError, TypeError):
        return LookupResult(
            path=key,
            matched=True,
            precision=PRECISION_UNKNOWN,
            commits=records,
            note="ts could not be parsed; returning all indexed commits for this path "
            "without day-proximity filtering. " + index.precision_notice(),
        )

    hits = [r for r in records if abs((_parse_day(r["day"]) - target_day).days) <= window_days]
    if not hits:
        return LookupResult(
            path=key,
            matched=False,
            precision=PRECISION_NOT_FOUND,
            commits=records,
            note=f"path has commits in the index but none within ±{window_days} days of ts. "
            + index.precision_notice(),
        )
    return LookupResult(
        path=key,
        matched=True,
        precision=PRECISION_FILE_DAY,
        commits=hits,
        note=index.precision_notice(),
    )


__all__ = [
    "PRECISION_FILE_DAY",
    "PRECISION_GITIGNORED",
    "PRECISION_NOT_FOUND",
    "PRECISION_UNKNOWN",
    "CommitIndex",
    "LookupResult",
    "build_commit_index",
    "check_ignored_batch",
    "lookup",
    "_normalize",
]
