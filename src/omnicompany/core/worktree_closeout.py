"""Close out a finished Git worktree without --force (AGENTS.md「worktree 完成即回收」).

Order of work, every step checked before the next:

1. The path must be a registered, non-main worktree with no merge/rebase in progress, no
   registered worktree nested inside it, no process using it, and no link leading out of it
   except dependency links (a ``node_modules`` junction); those are unlinked as link objects.
   No other registered worktree of the same repo may link into it (a release tree's
   ``web/dist`` or ``node_modules`` junctioned to this one would break when it is removed).
2. Everything git would lose is archived in one commit on top of HEAD, kept under
   ``refs/archive/worktree-closeout/<YYYYMMDD>/<name>``: modified tracked files, untracked
   files, and ignored files that are not a known regenerable class (dependencies, build
   output, caches).  Each archived path's blob is compared with the file on disk.
   ``skip_prefixes`` exclude paths verified elsewhere (e.g. an exact copy of a branch).
3. Only then: modified files are restored to HEAD and untracked files deleted, by explicit
   path; ``git worktree remove`` (never --force) removes the rest.  Closeout is complete only
   when native removal returns 0, the path is absent, and a successful registration query
   confirms it is unregistered.  Every other result is incomplete and preserves the observed
   state and errors for native Git recovery; closeout never recursively deletes the leftover.

A refusal names every unmet condition and changes nothing.  Receipts go to the same log as
``omni cleanup rmtree``.
"""
from __future__ import annotations

import json
import ntpath
import os
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable

REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
# Ignored content of these kinds is rebuilt by its tool and is not archived.
REGENERABLE_NAMES = frozenset({
    "node_modules", "dist", "build", "target", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", ".cache", ".next", ".turbo", ".vite", ".parcel-cache", "coverage",
    ".gradle", ".tox", ".eggs", ".nyc_output", "test-results", "playwright-report",
})
REGENERABLE_SUFFIXES = (".pyc", ".pyo", ".tsbuildinfo")
MAX_IGNORED_ARCHIVE_BYTES = 200 * 2**20
MAX_LISTED = 30
# Sibling worktrees are scanned this many levels deep for links into the tree being closed
# (2026-10-12: 501 debug-agent trees, 1.3 M entries, 21.7 s).  Package contents of node_modules
# are not entered; node_modules/* and node_modules/@scope/* are listed.
INBOUND_SCAN_DEPTH = 8


_GIT_BINARY: str | None = None


def _git_binary() -> str:
    """The real git executable, not Git for Windows' ``cmd\\git.exe`` launcher.

    subprocess timeouts kill only the direct child.  Through the launcher that left the real git
    running: 2026-10-13 a ``status --untracked-files=all`` on a release tree kept 3.2 GB for 24
    minutes after its 900 s timeout had fired.  Called directly, the timeout stops the process
    that is doing the work.
    """
    global _GIT_BINARY
    if _GIT_BINARY is None:
        _GIT_BINARY = "git"
        try:
            exec_path = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, timeout=30).stdout.strip()
            candidate = os.path.join(exec_path, "git.exe" if os.name == "nt" else "git")
            if exec_path and os.path.isfile(candidate):
                _GIT_BINARY = candidate
        except (OSError, subprocess.SubprocessError):
            pass
    return _GIT_BINARY


def _git_command(*args: str) -> list[str]:
    """Build a native Git command, enabling Windows long paths for this process only."""
    config = ["-c", "core.longpaths=true"] if os.name == "nt" else []
    return [_git_binary(), *config, *args]


def _git(cwd: str, *args: str, env: dict | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(_git_command("--no-optional-locks", "-C", cwd, *args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)


def _out(cwd: str, *args: str, env: dict | None = None) -> str:
    proc = _git(cwd, *args, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败（{proc.returncode}）：{proc.stderr.strip()[:300]}")
    return proc.stdout


def _key(path: str) -> str:
    path = path[4:] if path.startswith("\\\\?\\") else path
    return ntpath.normcase(ntpath.normpath(path)).rstrip("\\")


def _within(path: str, root: str) -> bool:
    p, r = _key(path), _key(root)
    return p == r or p.startswith(r + "\\")


def _regenerable(rel: str) -> bool:
    parts = rel.rstrip("/").split("/")
    return any(p in REGENERABLE_NAMES for p in parts) or rel.endswith(REGENERABLE_SUFFIXES)


def _task_temp_prefix(rel: str) -> str | None:
    """``…/.omni/tmp/<task>`` containing ``rel`` (AGENTS.md default task temp area), else None.

    A worktree being closed means its tasks are over, so their temp batches are neither
    archived nor traversed by git; they go through rmtree's checks instead.
    """
    parts = rel.rstrip("/").split("/")
    for i in range(len(parts) - 1):
        if parts[i] == ".omni" and parts[i + 1] == "tmp":
            return "/".join(parts[: i + 3]) if len(parts) > i + 2 else "/".join(parts[: i + 2])
    return None


def _links(root: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & REPARSE:
                try:
                    target = os.readlink(entry.path)
                except OSError:
                    target = ""
                target = target[4:] if target.startswith("\\\\?\\") else target
                if target and not ntpath.isabs(target):
                    target = ntpath.normpath(ntpath.join(ntpath.dirname(entry.path), target))
                found.append((entry.path, target))
                continue
            if entry.is_dir(follow_symlinks=False) and entry.name != ".git":
                stack.append(entry.path)
    return found


def _bounded_links(root: str, max_depth: int = INBOUND_SCAN_DEPTH) -> list[tuple[str, str]]:
    """Links under ``root`` down to ``max_depth`` levels, without following them."""
    found: list[tuple[str, str]] = []
    stack = [(root, 0, False)]
    while stack:
        current, depth, in_packages = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & REPARSE:
                found.append((entry.path, _link_target(entry.path)))
                continue
            if not entry.is_dir(follow_symlinks=False) or entry.name == ".git" or depth + 1 >= max_depth:
                continue
            if in_packages:
                if entry.name.startswith("@"):
                    stack.append((entry.path, depth + 1, True))
                continue
            stack.append((entry.path, depth + 1, entry.name == "node_modules"))
    return found


def _link_target(link: str) -> str:
    try:
        target = os.readlink(link)
    except OSError:
        return ""
    target = target[4:] if target.startswith("\\\\?\\") else target
    if target and not ntpath.isabs(target):
        target = ntpath.normpath(ntpath.join(ntpath.dirname(link), target))
    return target


class InboundLinks:
    """Links from every registered worktree of a repo, scanned once per command and reused."""

    def __init__(self) -> None:
        self._by_repo: dict[str, list[tuple[str, str]]] = {}

    def __call__(self, common_dir: str, registered: list[str]) -> list[tuple[str, str]]:
        key = _key(common_dir)
        if key not in self._by_repo:
            seen: dict[str, str] = {}
            for tree in registered:
                if os.path.isdir(tree):
                    for link, target in _bounded_links(tree):
                        seen.setdefault(_key(link), (link, target))
            self._by_repo[key] = list(seen.values())
        return self._by_repo[key]


def _inbound(path: str, common: str, registered: list[str],
             inbound_links: Callable[[str, list[str]], list[tuple[str, str]]]) -> list[tuple[str, str]]:
    """Links outside ``path`` that still point into it (re-read now: a source closed earlier is gone)."""
    hits = []
    for link, target in inbound_links(common, registered):
        if not target or _within(link, path) or not _within(target, path):
            continue
        if not os.path.exists(target):  # already dangling: removing this tree changes nothing for it
            continue
        if os.path.lexists(link) and _key(_link_target(link)) == _key(target):
            hits.append((link, target))
    return hits


def _tracked_rels(path: str, rels: list[str]) -> list[str]:
    """The subset of ``rels`` at or under which the index tracks files."""
    files = []
    for i in range(0, len(rels), 200):  # ls-files has no --pathspec-from-file
        files += [f for f in _out(path, "ls-files", "-z", "--", *rels[i:i + 200]).split("\0") if f]
    return sorted({r for r in rels if any(f == r or f.startswith(r + "/") for f in files)})


def _restore_tracked(path: str, rels: list[str]) -> list[str]:
    """Write back from HEAD whatever HEAD tracks under ``rels``; return the rels that had tracked files."""
    tracked = _tracked_rels(path, rels) if rels else []
    if tracked:
        with tempfile.TemporaryDirectory(prefix="omni-wt-links-") as tmp:
            listing = os.path.join(tmp, "paths")
            with open(listing, "w", encoding="utf-8", newline="") as handle:
                handle.write("\0".join(tracked) + "\0")
            _out(path, "restore", "--source=HEAD", "--staged", "--worktree",
                 f"--pathspec-from-file={listing}", "--pathspec-file-nul")
    return tracked


def _empty_git_directories(marker: str) -> list[str] | None:
    """Only directory structure, with no file, link, ref or HEAD left anywhere."""
    directories, stack = [], [marker]
    while stack:
        current = stack.pop()
        for entry in os.scandir(current):
            metadata = entry.stat(follow_symlinks=False)
            if (entry.is_symlink() or getattr(metadata, "st_file_attributes", 0) & REPARSE
                    or not entry.is_dir(follow_symlinks=False)):
                return None
            stack.append(entry.path)
        directories.append(current)
    return directories


def _drop_empty_git_markers(root: str) -> int:
    """Remove empty ``.git`` directories under ``root`` (not following links); return how many.

    Deploy tests leave fixture checkouts whose ``.git`` is an empty directory (2026-10-13:
    ``.omni/tmp/deploy-concurrency-tests/*/worktrees/checkout/.git`` in 15 release trees).  An empty
    directory is no repository and holds nothing, yet rmtree refuses any ``.git`` it meets.
    """
    removed, stack = 0, [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & REPARSE or not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name == ".git":
                try:
                    empty = _empty_git_directories(entry.path)
                    if empty is not None:
                        for directory in sorted(empty, key=len, reverse=True):
                            os.rmdir(directory)  # Empty directories only; never recursive deletion.
                        removed += 1
                except OSError:
                    pass
                continue
            stack.append(entry.path)
    return removed


def _nested_repos(root: str) -> list[str]:
    """Directories under ``root`` holding a ``.git`` entry, other than an empty ``.git`` directory."""
    found, stack = [], [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & REPARSE:
                continue
            if entry.name == ".git":
                if entry.is_file(follow_symlinks=False) or any(os.scandir(entry.path)):
                    found.append(current)
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(entry.path)
    return sorted(found)


def _state_commit(repo: str) -> str | None:
    """Commit the uncommitted non-ignored state of ``repo`` (or a worktree of it) without touching its index."""
    if not _status(repo, "--untracked-files=all"):
        return None
    head = _git(repo, "rev-parse", "-q", "--verify", "HEAD").stdout.strip()
    with tempfile.TemporaryDirectory(prefix="omni-nested-") as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(tmp, "index")}
        _out(repo, "read-tree", head or "--empty", env=env)
        _out(repo, "add", "-A", env=env)
        tree = _out(repo, "write-tree", env=env).strip()
    parents = ["-p", head] if head else []
    return _out(repo, "-c", "user.name=omni worktree closeout", "-c", "user.email=omni-closeout@localhost",
                "commit-tree", tree, *parents, "-m", "archive: nested repository state before closeout").strip()


def _gitfile_admin(worktree: str) -> str | None:
    """The admin directory a ``.git`` file names, resolved; None when it is not a gitfile."""
    try:
        line = open(os.path.join(worktree, ".git"), encoding="utf-8").read().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    return ntpath.normpath(ntpath.join(worktree, line[len("gitdir:"):].strip()))


def _gitfile_main(worktree: str) -> str | None:
    """The main checkout a ``.git`` file points into (``<main>/.git/worktrees/<name>``), else None."""
    admin = _gitfile_admin(worktree)
    if admin is None:
        return None
    worktrees_dir = ntpath.dirname(admin)
    dot_git = ntpath.dirname(worktrees_dir)
    if ntpath.basename(worktrees_dir) != "worktrees" or ntpath.basename(dot_git) != ".git":
        return None
    return ntpath.dirname(dot_git)


def _preserve_nested_repos(task_temp: str, common: str, base: str, *, process_rows, log_path,
                           fixture_plan: dict | None = None) -> tuple[list[dict], list[str]]:
    """Fetch every ref of each nested repository under ``task_temp`` into ``base/<n>/…`` and delete its ``.git``.

    User decision 2026-10-13: 51 closed-out trees held 240 nested repositories in task temp areas
    (deploy tests, pytest fixtures, one gamewright source snapshot); none had a remote.  Uncommitted
    non-ignored state is first committed inside the nested repository; ignored files that are not a
    regenerable class stop the closeout.  A ``.git`` file is accepted only when it is a worktree of a
    nested repository in the same task temp (deploy tests' release-checkouts): its HEAD, or its state
    commit, is written as ``refs/omni-closeout/worktrees/<i>`` in that repository before the fetch.
    Every fetched ref is compared with the nested repository before its ``.git`` goes through rmtree.
    """
    from omnicompany.core import verified_cleanup as vc

    if fixture_plan is not None:
        return _preserve_fixture_plan(common, base, fixture_plan)

    records, problems = [], []
    repos = _nested_repos(task_temp)
    mains = [r for r in repos if os.path.isdir(os.path.join(r, ".git"))]
    main_keys = {_key(r): r for r in mains}
    worktrees_of: dict[str, list[str]] = {}
    blocked: set[str] = set()
    for i, wt in enumerate(r for r in repos if os.path.isfile(os.path.join(r, ".git"))):
        admin = _gitfile_admin(wt)
        if admin is not None and not os.path.exists(admin):
            # Points at an admin directory that no longer exists: no repository behind it, only a
            # pointer (2026-10-13: fixture checkouts whose main repo an earlier run had removed).
            os.remove(os.path.join(wt, ".git"))
            records.append({"repo": wt, "refs": 0, "state_commit": None, "dest": None, "dangling_gitfile_removed": admin})
            continue
        main = _gitfile_main(wt)
        if not main or _key(main) not in main_keys:
            problems.append(f"嵌套的 .git 是 gitfile，指向任务临时区以外的仓库，先单独处理：{wt}")
            continue
        keep = [e[3:] for e in _status(wt, "--ignored=matching", "--untracked-files=normal")
                if e.startswith("!!") and not _regenerable(e[3:])]
        if keep:
            problems.append(f"嵌套仓库的 worktree 里有不可重建的被忽略文件，先逐项判断：{wt}：{keep[:3]}")
            blocked.add(_key(main))
            continue
        tip = _state_commit(wt) or _git(wt, "rev-parse", "-q", "--verify", "HEAD").stdout.strip()
        if tip:
            _out(wt, "update-ref", f"refs/omni-closeout/worktrees/{i}", tip)
        worktrees_of.setdefault(_key(main), []).append(wt)
    upload_pack = f'"{_git_binary()}" -c core.longpaths=true upload-pack'  # deep fixture paths exceed MAX_PATH
    for n, repo in enumerate(mains):
        gitdir = os.path.join(repo, ".git")
        if _key(repo) in blocked:
            continue
        ignored = [e[3:] for e in _status(repo, "--ignored=matching", "--untracked-files=normal") if e.startswith("!!")]
        keep = [i for i in ignored if not _regenerable(i)]
        if keep:
            problems.append(f"嵌套仓库里有不可重建的被忽略文件，先逐项判断：{repo}：{keep[:3]}")
            continue
        state = _state_commit(repo)
        if state:
            _out(repo, "update-ref", "refs/omni-closeout/state", state)
        want = dict(line.split(" ", 1)[::-1] for line in
                    _out(repo, "for-each-ref", "--format=%(objectname) %(refname)").splitlines() if line)
        dest = f"{base}/{n}"
        if want:
            fetch = subprocess.run(_git_command("--git-dir", common, "fetch", "--no-tags", "--no-write-fetch-head",
                                                f"--upload-pack={upload_pack}", gitdir, f"+refs/*:{dest}/*"),
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
            if fetch.returncode != 0:
                problems.append(f"嵌套仓库的 ref 没拉进来：{repo}：{fetch.stderr.strip()[:200]}")
                continue
            got = {}
            for name in want:
                local = f"{dest}/{name[len('refs/'):]}"
                got[name] = subprocess.run(_git_command("--git-dir", common, "rev-parse", "-q", "--verify", local),
                                           capture_output=True, text=True).stdout.strip()
            bad = [name for name, sha in want.items() if got.get(name) != sha]
            if bad:
                problems.append(f"嵌套仓库的 ref 核对不一致，没有删除：{repo}：{bad[:3]}")
                continue
        done = vc.remove(gitdir, reason=f"worktree 收尾：任务临时区里的嵌套仓库（ref 在 {dest}）",
                         process_rows=process_rows, log_path=log_path,
                         basis_override=f"嵌套仓库全部 ref 已拉进 {dest} 并逐个核对提交号（用户 2026-10-13 裁定先保全再删）")
        if done["outcome"] != "deleted":
            problems.append(f"嵌套仓库的 .git 没删掉：{repo}：{'；'.join(done['problems'][:2])}")
            continue
        gitfiles = worktrees_of.get(_key(repo), [])
        for wt in gitfiles:  # their HEADs are refs of the repository just preserved
            os.remove(os.path.join(wt, ".git"))
        records.append({"repo": repo, "refs": len(want), "state_commit": state, "dest": dest, "worktrees": len(gitfiles)})
    return records, problems


def _status(path: str, *extra: str) -> list[str]:
    return [line for line in _out(path, "status", "--porcelain=v1", "-z", *extra).split("\0") if line]


def _size(path: str) -> int:
    total = 0
    if os.path.isfile(path):
        return os.lstat(path).st_size
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def _archive_ref(common_dir: str, name: str) -> str:
    base = f"refs/archive/worktree-closeout/{time.strftime('%Y%m%d')}/{name}"
    ref, n = base, 1
    while subprocess.run(_git_command("--git-dir", common_dir, "rev-parse", "-q", "--verify", ref),
                         capture_output=True).returncode == 0:
        n += 1
        ref = f"{base}-{n}"
    return ref


def _archive_tree(path: str, info: dict[str, Any], skip: list[str]) -> str:
    """Tree of HEAD plus everything git would lose, built in a scratch copy of the worktree's own index.

    Starting from the real index keeps sparse-checkout skip flags, so paths outside the sparse
    definition stay as HEAD has them instead of being recorded as deleted.
    """
    import shutil

    with tempfile.TemporaryDirectory(prefix="omni-wt-close-") as tmp:
        scratch = os.path.join(tmp, "index")
        env = dict(os.environ, GIT_INDEX_FILE=scratch)
        real = os.path.join(info["admin_dir"], "index")
        if os.path.isfile(real):
            # copy2 keeps the index mtime: with a fresh mtime git's racy-clean check would trust stale
            # stat data and miss a same-size edit made in the same second (caught by the blob check below).
            shutil.copy2(real, scratch)
        else:
            _out(path, "read-tree", "HEAD", env=env)
        # Exclude only prefixes that cover untracked/modified paths: git refuses an exclude pathspec
        # naming an ignored path, and add -A never touches ignored content anyway.
        changed = info["untracked"] + info["modified"]
        excluded = [p for p in skip if any(c == p or c.startswith(p + "/") for c in changed)]
        excluded += info.get("task_temp_untracked", [])
        # Content that lives elsewhere: other hard-link names outside every non-main worktree, and
        # paths git only sees through an in-tree link (they are archived under their real path).
        excluded += info.get("untracked_hardlinked_elsewhere", []) + info.get("through_link_prefixes", [])
        excluded += info.get("untracked_regenerable_dirs", [])
        # --sparse: a file outside the sparse definition that a build wrote to disk is still listed
        # by git status and must be archived (real shape: juggernaut-20261007 public/hifi/lua/*.json).
        _out(path, "add", "-A", "--sparse", "--", ".", *[f":(exclude){p}" for p in excluded], env=env)
        if info["ignored_to_archive"]:
            _out(path, "add", "-f", "--sparse", "--", *info["ignored_to_archive"], env=env)
        return _out(path, "write-tree", env=env).strip()


def _verify_archive(path: str, commit: str, archived: list[str]) -> list[str]:
    """Paths whose bytes on disk differ from the archive commit (two git processes in total).

    One ``ls-tree`` lists the archived blobs and one ``hash-object --stdin-paths`` hashes the
    files as ``git add`` would (same filters), so a worktree with thousands of files is checked
    in seconds instead of two processes per file.
    """
    present = [rel for rel in archived
               if os.path.lexists(os.path.join(path, *rel.split("/")))
               and not os.path.islink(os.path.join(path, *rel.split("/")))]
    if not present:
        return []
    listing = _out(path, "ls-tree", "-r", "-z", "--full-tree", commit)
    want: dict[str, str] = {}
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, _, name = entry.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            want[name] = parts[2]
    proc = subprocess.run(_git_command("--no-optional-locks", "-C", path, "hash-object", "--stdin-paths"),
                          input="\n".join(present) + "\n", capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1800)
    have = proc.stdout.split()
    if proc.returncode != 0 or len(have) != len(present):
        return present  # cannot prove anything; refuse rather than guess
    return [rel for rel, digest in zip(present, have) if want.get(rel) != digest]


def inspect(path: str, *, process_rows: Callable[[], Iterable[dict[str, Any]]] | None = None,
            skip_prefixes: Iterable[str] = (), unlink_roots: Iterable[str] = (),
            inbound_links: Callable[[str, list[str]], list[tuple[str, str]]] | None = None,
            closing: Iterable[str] = ()) -> dict[str, Any]:
    """Everything close() needs, plus the list of reasons it would refuse."""
    from omnicompany.core import verified_cleanup as vc

    path = os.path.normpath(path)
    info: dict[str, Any] = {"worktree": path, "problems": [], "skip_prefixes": [p.strip("/") for p in skip_prefixes]}
    problems = info["problems"]
    if not os.path.isfile(os.path.join(path, ".git")):
        problems.append("不是 worktree（根目录没有 .git 文件）；主仓或普通目录不由这里收尾")
        return info
    common = os.path.normpath(_out(path, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    admin = os.path.normpath(_out(path, "rev-parse", "--absolute-git-dir").strip())
    info.update(common_dir=common, admin_dir=admin)
    listing = subprocess.run(_git_command("--git-dir", common, "worktree", "list", "--porcelain"), capture_output=True,
                             text=True, encoding="utf-8", errors="replace").stdout
    registered = [line[9:] for line in listing.splitlines() if line.startswith("worktree ")]
    if not any(_key(r) == _key(path) for r in registered):
        problems.append("这个目录没有登记在所属仓的 worktree 列表里")
    nested = [r for r in registered if _within(r, path) and _key(r) != _key(path)]
    if nested and not all(_key(r) in {_key(c) for c in closing} for r in nested):
        problems.append(f"里面还嵌套着 {len(nested)} 个已登记的 worktree，先收里层：" + "；".join(nested[:5]))
    inbound = _inbound(path, common, [os.path.normpath(r) for r in registered], inbound_links or InboundLinks())
    # An outer worktree closed in the same command (it contains this one) often keeps a bridge junction
    # (<outer>/webworks, <outer>/.omni/webworks) onto this tree (2026-10-13: hero-unified-public-20261003,
    # E:\wmref, …).  Inner trees go first; such bridge links are removed as link objects only.
    outers = [os.path.normpath(c) for c in closing if _within(path, c) and _key(c) != _key(path)]
    info["outer_bridge_links_to_unlink"] = [(p, t) for p, t in inbound if any(_within(p, o) for o in outers)]
    inbound = [(p, t) for p, t in inbound if (p, t) not in info["outer_bridge_links_to_unlink"]]
    info["inbound_links"] = inbound[:MAX_LISTED]
    if inbound:
        problems.append(f"同仓别的 worktree 有 {len(inbound)} 个链接指向这里，移除会让它们悬空，先处理对方：" +
                        "；".join(f"{p} -> {t}" for p, t in inbound[:5]))
    for marker in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        if os.path.exists(os.path.join(admin, marker)):
            problems.append(f"正在进行 {marker}，先处理完或单独存档后中止")
    lock = os.path.join(admin, "index.lock")
    if os.path.exists(lock):
        lock_stat = os.stat(lock)
        problems.append(f"管理目录里有 index.lock（{lock_stat.st_size} 字节，"
                        f"{time.strftime('%m-%d %H:%M', time.localtime(lock_stat.st_mtime))}）：先确认没有 git 在用这棵树，"
                        f"再处理这把锁：{lock}")
    info["head"] = _out(path, "rev-parse", "HEAD").strip()
    info["branch"] = _git(path, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip() or None
    info["refs_containing_head"] = _out(path, "for-each-ref", "--contains", info["head"],
                                        "--format=%(refname)").split()[:MAX_LISTED]
    links = _links(path)
    link_rels = [os.path.relpath(p, path).replace(os.sep, "/") for p, _t in links]

    def _through_link(rel: str) -> bool:
        # git status -uall lists the contents of an in-tree junction as untracked files (2026-10-13:
        # release trees had a root ``webworks`` junction onto the tracked workshop/vendor/webworks).
        # Those are views of other paths: never archived twice, never deleted through the link.
        rel = rel.rstrip("/")
        return any(rel.startswith(l + "/") for l in link_rels)

    # Collapsed status first (2026-10-13 bp2-approval-20261007: an untracked workshop/vendor/webworks/
    # node_modules made status --untracked-files=all run past 900 s; collapsed it took 0.13 s).
    # Untracked directories of a regenerable class are neither listed file by file nor archived; they go
    # through rmtree.  Other untracked directories are listed one by one, never through a link.
    coarse = _status(path, "--untracked-files=normal")
    entries = [e for e in coarse if not e.startswith("??")]
    regen_dirs, link_entries = [], set()
    for e in coarse:
        if not e.startswith("??"):
            continue
        rel = e[3:]
        bare = rel.rstrip("/")
        if bare in link_rels:
            link_entries.add(bare)  # the link object itself: removed as a link, never expanded
        elif _through_link(rel):
            entries.append(e)  # a view through a link: counted and filtered below
        elif not rel.endswith("/"):
            entries.append(e)
        elif bare.split("/")[-1] in REGENERABLE_NAMES and not _task_temp_prefix(rel):
            regen_dirs.append(bare)
        else:
            # Expanded on its own; nested repositories still come back as undescended "dir/" entries.
            entries += [x for x in _status(path, "--untracked-files=all", "--", rel) if x.startswith("??")]
    info["untracked_regenerable_dirs"] = regen_dirs
    info["modified"] = [e[3:] for e in entries if not e.startswith("??")]
    untracked_all = [e[3:] for e in entries if e.startswith("??")]
    untracked = [u for u in untracked_all if not _through_link(u) and u.rstrip("/") not in link_rels]
    info["untracked_through_links"] = len(untracked_all) - len(untracked)
    info["through_link_prefixes"] = sorted(link_entries | {l for l in link_rels for u in untracked_all if u.startswith(l + "/")})
    info["task_temp_untracked"] = sorted({t for t in (_task_temp_prefix(u) for u in untracked) if t})
    # A file whose other hard-link name lies outside every non-main worktree (2026-10-13: release trees
    # hard-linked workshop/config/integrations.local.json to the main tree's live config) keeps its
    # content there: it is not archived (no secrets copied into archive commits), only this name goes.
    non_main = [os.path.normpath(r) for r in registered[1:]]

    def _lives_elsewhere(rel: str) -> bool:
        full = os.path.join(path, *rel.rstrip("/").split("/"))
        try:
            st = os.lstat(full)
        except OSError:
            return False
        if st.st_nlink < 2 or not stat.S_ISREG(st.st_mode):
            return False
        names = vc._hardlink_names(full) or []
        return any(not _within(n, path) and not any(_within(n, w) for w in non_main) for n in names)

    candidates = [u for u in untracked if not _task_temp_prefix(u)]
    elsewhere = [u for u in candidates if _lives_elsewhere(u)]
    info["untracked_hardlinked_elsewhere"] = elsewhere
    info["untracked"] = [u for u in candidates if u not in set(elsewhere)]
    if any(e[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD") for e in entries):
        problems.append("有未解决的冲突文件")
    planned_nested = {_key(r) for r in nested} & {_key(c) for c in closing}
    undescended = [u for u in info["untracked"] if u.endswith("/") and
                   _key(os.path.join(path, *u.rstrip("/").split("/"))) not in planned_nested]
    if undescended:
        problems.append(f"有 git 不进入的未跟踪目录（嵌套仓库或 worktree），先单独核验：{undescended[:5]}")
    # Excluding the untracked regenerable dirs and link objects keeps git out of them: bp2-approval-20261007's
    # node_modules held a junction cycle (@wiki-core/blocks/node_modules/@wiki-core/blocks/…) and this
    # listing ran past 900 s; with the excludes it took 0.07 s.
    pruned = [f":(exclude){d}" for d in regen_dirs + sorted(link_entries)]
    ignored = [e[3:] for e in _status(path, "--ignored=matching", "--untracked-files=normal", "--", ".", *pruned)
               if e.startswith("!!") and not _through_link(e[3:])]
    info["ignored_task_temp"] = [i for i in ignored if _task_temp_prefix(i)][:MAX_LISTED]
    skipped = info["skip_prefixes"]

    def _skipped(rel: str) -> bool:
        rel = rel.rstrip("/")
        return any(rel == p or rel.startswith(p + "/") for p in skipped)

    info["ignored_skipped"] = [i for i in ignored if _skipped(i)][:MAX_LISTED]
    keep_ignored = [i for i in ignored if not _regenerable(i) and not _task_temp_prefix(i) and not _skipped(i)
                    and not _lives_elsewhere(i)]
    info["ignored_regenerable"] = [i for i in ignored if _regenerable(i)][:MAX_LISTED]
    info["ignored_to_archive"] = keep_ignored
    ignored_bytes = sum(_size(os.path.join(path, *i.rstrip("/").split("/"))) for i in keep_ignored)
    info["ignored_to_archive_bytes"] = ignored_bytes
    if ignored_bytes > MAX_IGNORED_ARCHIVE_BYTES:
        problems.append(f"被忽略且不属于可重建类的内容有 {ignored_bytes / 2**20:.0f} MiB，超过归档上限；"
                        f"先逐项判断（例如 {keep_ignored[:5]}）")
    outside = [(p, t) for p, t in links if not t or not _within(t, path)]
    roots = [os.path.normpath(r) for r in unlink_roots]
    info["unlink_roots"] = roots
    # Dependency links, and links whose target lies under a root the caller named explicitly
    # (e.g. formal content a release tree borrowed), are removed as link objects only.
    dependency = [(p, t) for p, t in outside
                  if "\\node_modules" in _key(p) or (t and any(_within(t, r) for r in roots))]
    stray = [(p, t) for p, t in outside if (p, t) not in dependency]
    info["links"] = len(links)
    info["dependency_links_to_unlink"] = dependency
    # In-tree links outside node_modules are removed as link objects before anything is deleted, so no
    # deletion can pass through one; a link standing where HEAD tracks files gets HEAD written back.
    info["internal_links_to_unlink"] = [(p, t) for p, t in links  # a link named node_modules counts; links inside one do not
                                        if t and _within(t, path) and "\\node_modules\\" not in _key(os.path.dirname(p)) + "\\"]
    if stray:
        problems.append("有指向树外的非依赖链接，先单独核验处理：" + "；".join(f"{p} -> {t}" for p, t in stray[:5]))
    rows = process_rows if process_rows is not None else vc._process_rows
    probe = vc.Inspection(target=path)
    vc._check_processes(Path(path), probe, rows)
    problems.extend(probe.problems)
    return info


def close(path: str, *, dry_run: bool = False, reason: str = "", skip_prefixes: Iterable[str] = (),
          unlink_roots: Iterable[str] = (),
          inbound_links: Callable[[str, list[str]], list[tuple[str, str]]] | None = None,
          closing: Iterable[str] = (),
          process_rows: Callable[[], Iterable[dict[str, Any]]] | None = None,
          log_path: Path | None = None) -> dict[str, Any]:
    from omnicompany.core import verified_cleanup as vc

    started = time.time()
    try:
        info = inspect(path, process_rows=process_rows, skip_prefixes=skip_prefixes, unlink_roots=unlink_roots,
                       inbound_links=inbound_links, closing=closing)
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        info = {"worktree": os.path.normpath(path), "problems": [f"核验时出错：{error}"], "outcome": "refused"}
        return _finish(info, started, log_path)
    info.update(at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), reason=reason, kind="worktree-closeout")
    if info["problems"]:
        info["outcome"] = "refused"
        return _finish(info, started, log_path)
    path, common, skip = info["worktree"], info["common_dir"], info["skip_prefixes"]
    try:
        tree = _archive_tree(path, info, skip)
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        info["outcome"] = "refused"
        info["problems"].append(f"打归档提交时出错，没有删除任何东西：{error}")
        return _finish(info, started, log_path)
    name = os.path.basename(path.rstrip("\\/"))
    commit = subprocess.run(_git_command("-c", "user.name=omni worktree closeout", "-c", "user.email=omni-closeout@localhost",
                             "-C", path, "commit-tree", tree, "-p", info["head"], "-m",
                             f"archive: worktree {name} before closeout ({reason or 'no reason given'})"),
                            capture_output=True, text=True, check=True).stdout.strip()
    info["archive_commit"] = commit
    archived = [p for p in info["modified"] + info["untracked"]
                if not any(p == s or p.startswith(s + "/") for s in skip)]
    bad = _verify_archive(path, commit, archived)
    info["archive_mismatch"] = bad[:MAX_LISTED]
    if bad:
        info["outcome"] = "refused"
        info["problems"].append(f"{len(bad)} 个文件与归档提交不一致，没有删除任何东西，例如 {bad[0]}")
        return _finish(info, started, log_path)
    if not dry_run:  # the ref is written only after verification; a dry run leaves none behind
        ref = _archive_ref(common, name)
        _out(path, "update-ref", ref, commit)
        info["archive_ref"] = ref
    ref = info.get("archive_ref", "(dry run: no ref)")
    if dry_run:
        info["outcome"] = "dry-run"
        return _finish(info, started, log_path)
    try:
        return _clean_and_remove(info, ref, process_rows, log_path, started)
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        info["outcome"] = "incomplete"
        info["problems"].append(f"归档之后的步骤出错，没有移除 worktree（内容已在 {ref}）：{error}")
        return _finish(info, started, log_path)


def _clean_and_remove(info: dict[str, Any], ref: str, process_rows: Callable[[], Iterable[dict[str, Any]]] | None,
                      log_path: Path | None, started: float) -> dict[str, Any]:
    from omnicompany.core import verified_cleanup as vc

    path, common = info["worktree"], info["common_dir"]
    for link, _target in info["outer_bridge_links_to_unlink"]:  # objects in the outer tree, closed next
        vc._remove_link_object(link)
    unlinked = []
    for link, _target in info["dependency_links_to_unlink"] + info["internal_links_to_unlink"]:
        vc._remove_link_object(link)
        unlinked.append(os.path.relpath(link, path).replace(os.sep, "/"))
    # A link can stand where HEAD tracks a directory (2026-10-13: release trees had
    # workshop/vendor/webworks/apps/material-canvas junctioned to another checkout).  Status looked
    # through it, so those paths were not in the modified list; once the link object is gone
    # they show as deleted.  Their HEAD copies are written back so the tree is clean for remove.
    info["unlinked_tracked_restored"] = _restore_tracked(path, unlinked)
    if info["modified"]:
        with tempfile.TemporaryDirectory(prefix="omni-wt-paths-") as tmp:
            listing = os.path.join(tmp, "paths")
            with open(listing, "w", encoding="utf-8", newline="") as handle:
                handle.write("\0".join(info["modified"]) + "\0")
            # restore --staged --worktree from HEAD: paths HEAD has are rewritten, paths it lacks
            # (staged additions) leave index and worktree; their content is already archived.
            _out(path, "restore", "--source=HEAD", "--staged", "--worktree",
                 f"--pathspec-from-file={listing}", "--pathspec-file-nul")
    rows = process_rows or vc._process_rows
    temp_results = []
    for rel in info["task_temp_untracked"]:
        full = os.path.join(path, *rel.split("/"))
        if os.path.isfile(full):
            os.chmod(full, stat.S_IWRITE)
            os.remove(full)
            continue
        if not os.path.lexists(full):
            continue
        info["empty_git_markers_removed"] = info.get("empty_git_markers_removed", 0) + _drop_empty_git_markers(full)
        # Links in the task temp whose target is elsewhere in this tree (2026-10-13: .omni/tmp/<task>/…/
        # node_modules/@contentkit/* onto the tree's vendor/webworks packages) are outside the temp root
        # for rmtree; the tree is going away, so they are removed as link objects first.
        for link, target in _links(full):
            if target and _within(target, path) and not _within(target, full):
                vc._remove_link_object(link)
                info["task_temp_links_unlinked"] = info.get("task_temp_links_unlinked", 0) + 1
        base = (f"refs/archive/worktree-closeout-nested/{time.strftime('%Y%m%d')}/"
                f"{os.path.basename(path.rstrip(chr(92) + '/'))}/{len(temp_results)}")
        nested, nested_problems = _preserve_nested_repos(full, common, base, process_rows=rows, log_path=log_path)
        info.setdefault("nested_repos_preserved", []).extend(nested)
        if nested_problems:
            temp_results.append({"path": full, "outcome": "kept", "files": None, "problems": nested_problems[:3]})
            continue
        done = vc.remove(full, reason=f"worktree 收尾：任务临时区（{ref}）", process_rows=rows, log_path=log_path,
                         basis_override="worktree 收尾：任务随 worktree 结束，任务临时区不归档")
        temp_results.append({"path": full, "outcome": done["outcome"], "files": done.get("files"), "problems": done["problems"][:3]})
    info["task_temp_removed"] = temp_results
    failed_temp = [r for r in temp_results if r["outcome"] != "deleted"]
    if failed_temp:
        info["outcome"] = "incomplete"
        info["problems"].append("任务临时区没删干净，没有移除 worktree：" + "；".join(
            f"{r['path']}：{'；'.join(r['problems'])}" for r in failed_temp[:3]))
        return _finish(info, started, log_path)
    for rel in info["untracked_regenerable_dirs"]:
        full = os.path.join(path, *rel.split("/"))
        if not os.path.lexists(full):
            continue
        done = vc.remove(full, reason=f"worktree 收尾：未跟踪的可重建目录（{ref}）", process_rows=rows, log_path=log_path,
                         basis_override="worktree 收尾：未跟踪的依赖/构建目录属可重建类，不归档")
        if done["outcome"] != "deleted":
            info["outcome"] = "incomplete"
            info["problems"].append(f"未跟踪的可重建目录没删掉，没有移除 worktree：{full}：{'；'.join(done['problems'][:2])}")
            return _finish(info, started, log_path)
    restored = set(info["modified"])
    emptied = set()
    for rel in info["untracked"] + info["untracked_hardlinked_elsewhere"]:
        if rel in restored:
            # Staged as deleted yet still on disk shows up twice; HEAD's copy was just restored.
            continue
        full = os.path.join(path, *rel.split("/"))
        if not os.path.lexists(full):
            continue
        if os.path.islink(full) or (getattr(os.lstat(full), "st_file_attributes", 0) & REPARSE):
            vc._remove_link_object(full)
        else:
            os.chmod(full, stat.S_IWRITE)
            os.remove(full)
        parent = os.path.dirname(full)
        while _within(parent, path) and _key(parent) != _key(path):
            emptied.add(parent)
            parent = os.path.dirname(parent)
    for directory in sorted(emptied, key=len, reverse=True):
        try:
            os.rmdir(directory)
        except OSError:
            pass
    rest = _status(path, "--untracked-files=all")
    if rest:
        info["outcome"] = "incomplete"
        info["problems"].append(f"清理后仍不干净，没有移除 worktree：{rest[:5]}")
        return _finish(info, started, log_path)
    try:
        removal = subprocess.run(_git_command("--git-dir", common, "worktree", "remove", path), capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=1800)
        remove_rc: int | None = removal.returncode
        remove_stderr = removal.stderr.strip()[:300]
    except (OSError, subprocess.SubprocessError) as error:
        remove_rc = None
        remove_stderr = str(error).strip()[:300]
    try:
        registration = subprocess.run(_git_command("--git-dir", common, "worktree", "list", "--porcelain"),
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        registration_rc: int | None = registration.returncode
        registration_stderr = registration.stderr.strip()[:300]
        if registration_rc == 0:
            registered_paths = {_key(row[9:]) for row in registration.stdout.splitlines() if row.startswith("worktree ")}
            is_registered: bool | None = _key(path) in registered_paths
        else:
            is_registered = None
    except (OSError, subprocess.SubprocessError) as error:
        registration_rc = None
        registration_stderr = (
            f"TimeoutExpired: worktree registration query timed out after {error.timeout} seconds"
            if isinstance(error, subprocess.TimeoutExpired) else f"{type(error).__name__}: {str(error).strip()[:240]}"
        )
        is_registered = None
    path_exists = os.path.lexists(path)
    info.update(
        remove_rc=remove_rc,
        remove_stderr=remove_stderr,
        remove_path_exists=path_exists,
        remove_registration="registered" if is_registered is True else "unregistered" if is_registered is False else "unknown",
        registration_rc=registration_rc,
        registration_stderr=registration_stderr,
    )
    if remove_rc != 0 or path_exists or is_registered is not False or registration_rc != 0:
        info["outcome"] = "incomplete"
        info["problems"].append(
            "native git worktree remove 状态未完全确认成功；保留目录与登记现状供原生 Git 恢复 "
            f"(rc={remove_rc}, stderr={remove_stderr!r}, path_exists={path_exists}, registration="
            f"{'registered' if is_registered is True else 'unregistered' if is_registered is False else 'unknown'}, "
            f"registration_rc={registration_rc}, registration_stderr={registration_stderr!r})"
        )
        return _finish(info, started, log_path)
    info["outcome"] = "closed"
    return _finish(info, started, log_path)


def _finish(info: dict[str, Any], started: float, log_path: Path | None) -> dict[str, Any]:
    from omnicompany.core.verified_cleanup import receipt_log_path

    info["seconds"] = round(time.time() - started, 2)
    log = log_path or receipt_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(info, ensure_ascii=False, default=str) + "\n")
    info["receipt_log"] = str(log)
    return info


def _fixture_version(repo: str, bare: bool = False) -> dict[str, Any]:
    head = _git(repo, "rev-parse", "-q", "--verify", "HEAD", timeout=30)
    if head.returncode not in (0, 1):
        raise RuntimeError(f"fixture HEAD 无法核验：{repo}")
    refs = dict(s.split(" ", 1) for s in
                _out(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines() if s)
    return {"head": head.stdout.strip() or None, "refs": refs,
            "index": "" if bare else _out(repo, "ls-files", "--stage", "-z"),
            "status": [] if bare else _status(repo, "--untracked-files=all")}


def _fixture_topology(root: str, markers: list[str], *, collect_problems: list[str] | None = None) -> dict[str, Any]:
    """Inventory bare/normal/unborn/worktree/empty-marker fixtures without parent fallback."""
    from omnicompany.core import verified_cleanup as vc
    candidates, empty, stack = {}, [], [root]
    while stack:
        current = stack.pop()
        entries = list(os.scandir(current))
        if {"HEAD", "objects", "config"} <= {e.name for e in entries} and os.path.basename(current) != ".git":
            if _out(current, "rev-parse", "--is-bare-repository").strip() != "true":
                raise RuntimeError(f"未知 bare Git 形状：{current}")
            candidates[_key(current)] = (current, True)
            continue
        for entry in entries:
            metadata = entry.stat(follow_symlinks=False)
            if (entry.name != ".git" and entry.is_dir(follow_symlinks=False)
                    and not entry.is_symlink() and not getattr(metadata, "st_file_attributes", 0) & REPARSE):
                stack.append(entry.path)
    for marker in markers:
        if os.path.isdir(marker) and _empty_git_directories(marker) is not None:
            empty.append(marker)
        else:
            repo = os.path.dirname(marker)
            candidates[_key(repo)] = (repo, False)
    plan = {"retirement_root": root, "repositories": [], "worktrees": [], "empty_markers": sorted(empty)}
    problems = []
    for repo, bare in sorted(candidates.values()):
        try:
            common = os.path.normpath(_out(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
            gitdir = os.path.normpath(_out(repo, "rev-parse", "--absolute-git-dir").strip())
            if not _within(common, root) or not _within(gitdir, root):
                raise RuntimeError(f"fixture Git 管理目录在批次外：{repo}")
            if bare:
                if _key(repo) != _key(common) or _key(repo) != _key(gitdir):
                    raise RuntimeError(f"bare fixture 的真实根不匹配：{repo}")
            elif _key(_out(repo, "rev-parse", "--show-toplevel").strip()) != _key(repo):
                raise RuntimeError(f"fixture 被误认成父仓：{repo}")
            registered = [s[9:] for s in _out(repo, "worktree", "list", "--porcelain").splitlines()
                          if s.startswith("worktree ")]
            if any(not _within(p, root) or not os.path.isdir(p) for p in registered):
                raise RuntimeError(f"fixture 含批次外或缺失 worktree：{repo}")
            remotes = []
            for remote in _out(repo, "remote").splitlines():
                urls = (_out(repo, "remote", "get-url", "--all", remote).splitlines()
                        + _out(repo, "remote", "get-url", "--push", "--all", remote).splitlines())
                for url in urls:
                    if not bare or not ntpath.isabs(url) or not ntpath.splitdrive(url)[0] or not _within(url, root):
                        raise RuntimeError(f"fixture 含非本批 bare mirror 的 remote：{repo}")
                    destination = os.path.normpath(url)
                    checked = vc.Inspection(destination)
                    vc._check_ancestors(Path(destination), checked)
                    if (checked.problems or not os.path.isdir(destination) or _key(destination) not in candidates
                            or _key(_out(destination, "rev-parse", "--show-toplevel").strip()) != _key(destination)):
                        raise RuntimeError(f"bare mirror remote 不是真实同批 fixture 根：{repo}")
                    remotes.append(destination)
            for marker in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD", "index.lock"):
                if os.path.exists(os.path.join(gitdir, marker)):
                    raise RuntimeError(f"fixture 有未结束的 Git 操作或锁：{repo}：{marker}")
            row = {"repo": repo, "bare": bare, "gitdir": gitdir, "common": common,
                   "registered": registered, "local_remotes": sorted(set(remotes)),
                   "version": _fixture_version(repo, bare)}
            if not bare and _key(gitdir) != _key(common):
                if not os.path.isfile(os.path.join(repo, ".git")):
                    raise RuntimeError(f"fixture worktree 身份不匹配：{repo}")
                plan["worktrees"].append(row)
            else:
                if not bare and (not os.path.isdir(os.path.join(repo, ".git"))
                                 or _key(common) != _key(os.path.join(repo, ".git"))):
                    raise RuntimeError(f"独立 fixture Git 根不匹配：{repo}")
                plan["repositories"].append(row)
            if not bare:
                keep = [s[3:] for s in _status(repo, "--ignored=matching", "--untracked-files=normal")
                        if s.startswith("!!") and not _regenerable(s[3:])]
                ignored_bytes = sum(_size(os.path.join(repo, *rel.rstrip("/").split("/"))) for rel in keep)
                if ignored_bytes > MAX_IGNORED_ARCHIVE_BYTES:
                    raise RuntimeError(f"fixture 非可重建 ignored 内容超过既有归档上限：{repo}")
                row["ignored_to_archive"] = keep
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            problems.append(f"{repo}：{error}")
            for category in ("repositories", "worktrees"):
                plan[category] = [row for row in plan[category] if _key(row["repo"]) != _key(repo)]
    if problems and collect_problems is not None:
        collect_problems.extend(problems)
    elif problems:
        raise RuntimeError("fixture 全批拓扑预检失败：\n" + "\n".join(problems))
    return plan


def _fixture_object_links(ins, plan: dict[str, Any]) -> list[dict[str, Any]]:
    from omnicompany.core import verified_cleanup as vc
    roots = [os.path.join(r["common"], "objects") for r in plan["repositories"]]
    seen, records, problems = set(), [], []
    for file in ins.hardlinked:
        names = None
        try:
            identity, names = vc._snapshot_identity(Path(file)), vc._hardlink_names(file)
            if not identity or not names or identity["reparse"] or identity["deletePending"]:
                raise RuntimeError(f"无法核验 fixture 对象硬链接：{file}")
            key = (str(identity["volumeSerial"]), str(identity["fid128"]))
            if key in seen:
                continue
            seen.add(key)
            if len({_key(n) for n in names}) != identity["nlink"]:
                raise RuntimeError(f"fixture 对象硬链接入口不完整：{file}")
            for name in names:
                other = vc._snapshot_identity(Path(name))
                if (not any(_within(name, r) for r in roots) or not other or other["reparse"]
                        or other["deletePending"] or (str(other["volumeSerial"]), str(other["fid128"])) != key):
                    raise RuntimeError(f"fixture 对象硬链接有树外入口或 native 身份不符：{file}")
            records.append({"native_identity": {"volumeSerial": key[0], "fid128": key[1]}, "names": names})
        except (OSError, RuntimeError) as error:
            problems.append(f"{error}；全部硬链接入口：{names or [file]}")
    if problems:
        raise RuntimeError("fixture 对象硬链接全批核验失败：\n" + "\n".join(problems))
    return records


def _fixture_worktree_tree(repo: str, head: str | None, ignored: Iterable[str] = ()) -> str:
    """Native Git tree of actual working files, independent of the original staged index."""
    gitdir = _out(repo, "rev-parse", "--absolute-git-dir").strip()
    with tempfile.TemporaryDirectory(prefix="omni-fixture-index-", dir=gitdir) as directory:
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(directory, "index")}
        _out(repo, "read-tree", head or "--empty", env=env)
        _out(repo, "add", "-A", env=env)
        if ignored:
            _out(repo, "add", "-f", "--sparse", "--", *ignored, env=env)
        return _out(repo, "write-tree", env=env).strip()


def _fixture_commit(repo: str, tree: str, head: str | None, label: str) -> str:
    return _out(repo, "-c", "user.name=omni fixture closeout", "-c", "user.email=omni-closeout@localhost",
                "commit-tree", tree, *(["-p", head] if head else []), "-m", label).strip()


def _verify_fixture_content(row: dict[str, Any]) -> None:
    repo, bare = row["repo"], row["bare"]
    if _fixture_version(repo, bare) != row["version"]:
        raise RuntimeError(f"fixture 原生 refs/HEAD/index 版本变化：{repo}")
    if not bare:
        if _out(repo, "write-tree").strip() != row["index_tree"]:
            raise RuntimeError(f"fixture 原 index tree 已变化：{repo}")
        if _fixture_worktree_tree(repo, row["version"]["head"], row.get("ignored_to_archive", ())) != row["worktree_tree"]:
            raise RuntimeError(f"fixture 工作树内容或模式在保全后变化：{repo}")
    if _fixture_version(repo, bare) != row["version"]:
        raise RuntimeError(f"fixture 内容验证期间原生版本变化：{repo}")



def _fixture_tree_without(repo: str, tree: str, prefixes: list[str]) -> str:
    """Expected native tree after already-preserved descendant worktrees were removed."""
    gitdir = _out(repo, "rev-parse", "--absolute-git-dir").strip()
    with tempfile.TemporaryDirectory(prefix="omni-fixture-index-", dir=gitdir) as directory:
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(directory, "index")}
        _out(repo, "read-tree", tree, env=env)
        paths = [name for name in _out(repo, "ls-files", "-z", env=env).split("\0") if name
                 and any(name == rel or name.startswith(rel + "/") for rel in prefixes)]
        if paths:
            _out(repo, "update-index", "--force-remove", "--", *paths, env=env)
        return _out(repo, "write-tree", env=env).strip()


def _fixture_after_closed_children(row: dict[str, Any], closed: list[dict[str, Any]]) -> dict[str, Any]:
    """Allow only this operation's verified native removals, retaining every original archive."""
    repo, expected = row["repo"], {**row["version"]}
    refs = dict(expected["refs"])
    prefixes = []
    for done in closed:
        if _key(done["common_dir"]) == _key(row["common"]):
            refs[done["archive_ref"]] = done["archive_commit"]
        if _within(done["worktree"], repo) and _key(done["worktree"]) != _key(repo):
            prefixes.append(os.path.relpath(done["worktree"], repo).replace(os.sep, "/"))
    expected["refs"] = refs
    expected["status"] = [entry for entry in expected["status"] if not (
        entry.startswith("?? ") and any(entry[3:].rstrip("/") == rel
            or entry[3:].startswith(rel + "/") for rel in prefixes))]
    result = {**row, "version": expected}
    if prefixes:
        result["ignored_to_archive"] = [rel for rel in row.get("ignored_to_archive", ())
            if not any(rel.rstrip("/") == prefix or rel.startswith(prefix + "/") for prefix in prefixes)]
        result["worktree_tree"] = _fixture_tree_without(repo, row["worktree_tree"], prefixes)
    return result


def _fixture_archive_objects(common: str, root: str) -> tuple[set[str], dict[tuple[str, str, str], str]]:
    """Reuse native objects only; old numeric buckets never certify a source or completion."""
    proc = subprocess.run(_git_command("--git-dir", common, "for-each-ref",
        "--format=%(refname)%09%(objectname)%09%(tree)%09%(parent)%09%(subject)",
        "refs/archive/test-fixtures"), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if proc.returncode:
        raise RuntimeError(f"fixture existing archive inventory failed: {proc.stderr[:240]}")
    objects, commits = set(), {}
    for line in proc.stdout.splitlines():
        fields = line.split("\t", 4)
        if len(fields) != 5:
            continue
        name, oid, tree, parent, label = fields
        pieces = name.split("/", 6)
        if len(pieces) != 7 or pieces[4] != os.path.basename(root) or not pieces[5].isdigit():
            continue
        objects.add(oid)
        if tree and label in {"archive original fixture index", "archive fixture working files"}:
            commits[(tree, parent, label)] = oid
    return objects, commits


def _verify_fixture_archives(common: str, records: list[dict]) -> None:
    """Batch native reads, then compare every expected ref/OID before destructive steps."""
    expected = {name: oid for row in records for name, oid in row["preserved_refs"].items()}
    if not expected:
        return
    prefix = os.path.commonprefix(list(expected)).rsplit("/", 1)[0] + "/"
    proc = subprocess.run(_git_command("--git-dir", common, "for-each-ref",
        "--format=%(refname)%09%(objectname)", prefix),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if proc.returncode:
        raise RuntimeError(f"fixture archive native verification failed: {proc.stderr[:240]}")
    actual = dict(line.split("\t", 1) for line in proc.stdout.splitlines() if "\t" in line)
    for name, oid in expected.items():
        if actual.get(name) != oid:
            raise RuntimeError(f"fixture archived ref native version mismatch: {name}")


def _preserve_fixture_plan(common: str, base: str, plan: dict[str, Any]) -> tuple[list[dict], list[str]]:
    """Preserve current sources; resume interrupted archives by reusing verified native objects."""
    records = []
    try:
        root = plan["retirement_root"]
        if not os.path.isabs(root) or any(not _within(row["repo"], root) for row in plan["repositories"]):
            raise RuntimeError("fixture archive plan does not match its exact retirement root")
        objects, commits = _fixture_archive_objects(common, root)
        for n, row in enumerate(plan["repositories"]):
            repo, bare, version = row["repo"], row["bare"], row["version"]
            if ((len(row["registered"]) != 1 and not plan.get("allow_batch_worktrees"))
                    or _fixture_version(repo, bare) != version):
                raise RuntimeError(f"fixture 有其它 worktree 或原生版本已变化：{repo}")
            if any(name.startswith("refs/omni-closeout/") for name in version["refs"]):
                raise RuntimeError(f"fixture source refs conflict with reserved archive metadata: {repo}")
            state = index_commit = index_tree = worktree_tree = None
            reused = []
            if not bare:
                # Compare current native trees, including MM index v1 and ignored worktree v2.
                index_tree = _out(repo, "write-tree").strip()
                verified_index = _git(repo, "diff", "--cached", "--quiet", index_tree, "--", timeout=30)
                if verified_index.returncode:
                    raise RuntimeError(f"fixture 原 index 与保全 tree 不符：{repo}")
                worktree_tree = _fixture_worktree_tree(repo, version["head"], row.get("ignored_to_archive", ()))
                for tree, label, role in ((index_tree, "archive original fixture index", "index"),
                                          (worktree_tree, "archive fixture working files", "state")):
                    key = (tree, version["head"] or "", label)
                    commit = commits.get(key)
                    if commit:
                        reused.append(role)
                    else:
                        commit = _fixture_commit(repo, tree, version["head"], label)
                        commits[key] = commit
                    if role == "index":
                        index_commit = commit
                    else:
                        state = commit
            dest = f"{base}/{n}"
            mapping = {f"{dest}/{name[len('refs/'):]}": oid for name, oid in version["refs"].items()}
            if version["head"]:
                mapping[f"{dest}/omni-closeout/head"] = version["head"]
            if state:
                mapping[f"{dest}/omni-closeout/state"] = state
            if index_commit:
                mapping[f"{dest}/omni-closeout/index"] = index_commit
            local = {name: oid for name, oid in mapping.items() if oid in objects}
            if local:
                # Native transaction adds fresh references; it never rewrites old archive refs.
                update = subprocess.run(_git_command("--git-dir", common, "update-ref", "--stdin"),
                    input=("start\n" + "".join(f"create {name} {oid}\n" for name, oid in local.items()) + "prepare\ncommit\n").encode("utf-8"),
                    capture_output=True, timeout=60)
                if update.returncode:
                    raise RuntimeError(f"fixture archived native objects could not be referenced: {repo}: {update.stderr.decode('utf-8', 'replace')[:240]}")
            missing = {name: oid for name, oid in mapping.items() if oid not in objects}
            if missing:
                upload_pack = f'"{_git_binary()}" -c core.longpaths=true upload-pack'
                fetch = subprocess.run(_git_command("--git-dir", common, "fetch", "--no-tags", "--no-write-fetch-head",
                                                    f"--upload-pack={upload_pack}", repo,
                                                    *[f"{oid}:{name}" for name, oid in missing.items()]),
                                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
                if fetch.returncode:
                    raise RuntimeError(f"fixture 原生保全失败：{repo}：{fetch.stderr[:240]}")
            if _fixture_version(repo, bare) != version:
                raise RuntimeError(f"fixture 保全期间原生版本变化：{repo}")
            record = {**row, "dest": dest, "state_commit": state, "index_commit": index_commit,
                      "index_tree": index_tree, "worktree_tree": worktree_tree, "preserved_refs": mapping,
                      "reused_native_commits": reused, "fetched_refs": len(missing),
                      "empty_unborn": not version["head"] and not version["refs"] and
                          not version["index"] and (bare or not _out(repo, "ls-tree", worktree_tree))}
            _verify_fixture_archives(common, [record])
            _verify_fixture_content(record)
            objects.update(mapping.values())
            records.append(record)
        _verify_fixture_archives(common, records)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return records, [str(error)]
    return records, []


def retire_fixture_root(raw: str, *, archive, dry_run: bool = False, reason: str = "",
                        process_rows=None, log_path: Path | None = None) -> dict[str, Any]:
    """Retire registered pytest temporary roots via native preservation and ordinary cleanup."""
    from omnicompany.core import verified_cleanup as vc
    started, mutated = time.time(), False
    rows = process_rows or vc._process_rows
    info = {"target": raw, "kind": "pytest-fixture-retirement", "reason": reason,
            "files": 0, "dirs": 0, "bytes": 0, "checks": {}, "problems": []}
    try:
        ins = vc.inspect_retiring_fixture_root(raw, archive=archive, process_rows=rows)
        info.update(target=ins.target, files=ins.files, dirs=ins.dirs, bytes=ins.bytes,
                    checks=ins.checks, problems=list(ins.problems))
        if not ins.ok:
            raise RuntimeError("fixture 根的现有保护检查未通过")
        root, canonical = ins.target, ins.checks["canonical_root"]
        main = _out(canonical, "rev-parse", "--show-toplevel").strip()
        common = _out(main, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        main_registered = _key(main) == _key(canonical) or any(
            d.host == "local" and d.layer == "workspace" and d.role == "project"
            and d.status == "active" and _key(d.path) == _key(main) for d in archive.declarations)
        if (not main_registered or not _within(canonical, main) or _within(main, root) or _within(common, root)
                or not os.path.isdir(os.path.join(main, ".git"))
                or _key(common) != _key(os.path.join(main, ".git"))):
            raise RuntimeError("项目真实主仓或归档位置不安全")
        preflight_problems = []
        plan = _fixture_topology(root, ins.repository_markers, collect_problems=preflight_problems)
        info.update(archive_repository=main, repository_count=len(plan["repositories"]),
                    worktrees=[r["repo"] for r in plan["worktrees"]])
        try:
            info["object_hardlinks"] = _fixture_object_links(ins, plan)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            preflight_problems.append(str(error))
        closing = [r["repo"] for r in plan["worktrees"]]
        for row in plan["worktrees"]:
            checked = inspect(row["repo"], process_rows=rows, closing=closing, unlink_roots=[root])
            preflight_problems.extend(f"{row['repo']}：{problem}" for problem in checked["problems"])
        if preflight_problems:
            raise RuntimeError("fixture 全批预检失败：\n" + "\n".join(preflight_problems))
        if dry_run:
            info["outcome"] = "dry-run"
            return _finish(info, started, log_path)
        base = f"refs/archive/test-fixtures/{time.strftime('%Y%m%d')}/{os.path.basename(root)}/{time.time_ns()}"
        # Preserve original refs, index and working files of ALL fixtures before native worktree removal.
        before = {**plan, "repositories": plan["repositories"] + plan["worktrees"], "allow_batch_worktrees": True}
        preclose, problems = _preserve_nested_repos(root, common, base + "/before-close", process_rows=rows,
                                                   log_path=log_path, fixture_plan=before)
        info["before_close_preserved"] = preclose
        mutated = True
        if problems:
            raise RuntimeError("；".join(problems))
        info["worktrees_closed"] = []
        refresh_processes = getattr(rows, "refresh", None)
        if plan["worktrees"] and refresh_processes is not None:
            refresh_processes()  # Refresh before the first destructive native worktree close as well.
        for row in sorted(plan["worktrees"], key=lambda r: len(r["repo"]), reverse=True):
            saved = next(r for r in preclose if _key(r["repo"]) == _key(row["repo"]))
            saved = _fixture_after_closed_children(saved, info["worktrees_closed"])
            _verify_fixture_content(saved)
            done = close(row["repo"], reason=reason, process_rows=rows, log_path=log_path,
                         closing=closing, unlink_roots=[root])
            mutated = True
            info["worktrees_closed"].append(done)
            if done["outcome"] != "closed":
                raise RuntimeError(f"fixture worktree 未原生收口：{row['repo']}：{done['problems'][:3]}")
        ins = vc.inspect_retiring_fixture_root(root, archive=archive, process_rows=rows)
        if not ins.ok:
            raise RuntimeError(f"worktree 收尾后保护检查失败：{ins.problems[:3]}")
        plan = _fixture_topology(root, ins.repository_markers)
        if plan["worktrees"]:
            raise RuntimeError("fixture 仍有非主 worktree")
        links = _fixture_object_links(ins, plan)
        base = f"refs/archive/test-fixtures/{time.strftime('%Y%m%d')}/{os.path.basename(root)}/{time.time_ns()}"
        preserved, problems = _preserve_nested_repos(root, common, base, process_rows=rows,
                                                    log_path=log_path, fixture_plan=plan)
        info["repositories_preserved"] = preserved
        mutated = True
        if problems:
            raise RuntimeError("；".join(problems))
        if links:
            for row in plan["repositories"]:
                if any(_within(name, os.path.join(row["common"], "objects"))
                       for link in links for name in link["names"]):
                    _out(row["repo"], "repack", "-a", "-d")
        refresh_processes = getattr(rows, "refresh", None)
        if refresh_processes is not None:
            refresh_processes()  # Preservation can take minutes; check current consumers before any Git removal.
        fresh = vc.inspect_retiring_fixture_root(root, archive=archive, process_rows=rows)
        if not fresh.ok or fresh.hardlinked:
            raise RuntimeError(f"保全后文件系统/对象硬链接未通过：{fresh.problems[:3]}")
        if _fixture_topology(root, fresh.repository_markers) != plan:
            raise RuntimeError("保全后 fixture 拓扑或原生版本变化")
        for row in preserved:
            _verify_fixture_content(row)
        _verify_fixture_archives(common, preserved)
        for row in sorted(preserved, key=lambda r: len(r["gitdir"]), reverse=True):
            done = vc.remove(row["gitdir"], reason="fixture 全部 refs/HEAD/dirty state 已原生保全：" + row["dest"],
                             process_rows=rows, log_path=log_path, archive=archive,
                             basis_override="已登记 retiring pytest 根的 Git 原生保全收尾：" + row["dest"])
            if done["outcome"] != "deleted":
                raise RuntimeError(f"Git 管理资料未通过普通清理核验：{row['gitdir']}：{done['problems'][:3]}")
        info["empty_git_markers_removed"] = _drop_empty_git_markers(root)
        done = vc.remove(root, reason=reason, process_rows=rows, log_path=log_path, archive=archive)
        info.update(root_removal=done, outcome=done["outcome"], problems=done["problems"])
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        info["outcome"] = "incomplete" if mutated else "refused"
        info["problems"].append(str(error))
    return _finish(info, started, log_path)
