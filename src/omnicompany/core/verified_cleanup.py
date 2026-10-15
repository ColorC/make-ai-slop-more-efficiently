"""Verified recursive deletion of task temp directories.

This is the 清理工具 that ``C:/workspace/AGENTS.md`` §文件落点与目录层级 refers to.  The
PreToolUse safety hook refuses every recursive ``rm``/``Remove-Item``/``rd`` by syntax because it
cannot look at the disk; this tool looks at the disk and deletes a whole tree only when the rule's
checks pass:

- the target is a literal absolute path strictly inside ``<project>/.omni/tmp/`` (whose own
  ``record.json`` must not mark unexpired human evidence), or the directory semantic archive
  (``omnicompany.core.dir_archive``) gives a delete basis for it: an expired batch manifest under
  a records/copies root, or a tmp/cache role with a declared lifecycle.  Role roots, formal
  roles, worktrees and undeclared paths are refused;
- no ancestor of the target is a symlink, junction or other reparse point;
- links inside the tree are allowed only when they point back into the tree (they are removed as
  link objects, never followed); a link that leads outside, or a file with a second hard link,
  refuses the target;
- nothing inside is tracked by Git, and the tree holds no repository or registered worktree;
- no other running process has its executable, command line or working directory inside it.

Any failed check refuses that target and names why; nothing is deleted on a refusal.  Every run
(deleted, dry-run or refused) appends one JSON line to the receipt log.
"""
from __future__ import annotations

import json
import ntpath
import os
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
MAX_LISTED = 50
UNEXPANDED = set("*?%$`")
LONG_PREFIX = "\\\\?\\"
SHELLS = frozenset({"bash.exe", "bash", "sh.exe", "sh", "dash", "zsh", "cmd.exe", "pwsh.exe", "pwsh",
                    "powershell.exe"})


def receipt_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) / "OmniCleanup" if base else Path.home() / ".omni-cleanup"
    return root / "receipts.jsonl"


@dataclass
class Inspection:
    target: str
    project_root: str | None = None
    files: int = 0
    dirs: int = 0
    bytes: int = 0
    internal_links: list[str] = field(default_factory=list)
    hardlinked: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE)


def _plain(path: str) -> str:
    return path[len(LONG_PREFIX):] if path.startswith(LONG_PREFIX) else path


def _long(path: str) -> str:
    if os.name == "nt" and not path.startswith(LONG_PREFIX):
        return LONG_PREFIX + os.path.abspath(path)
    return path


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(_plain(path))).rstrip("\\/")


def _within(path: str, root: str) -> bool:
    p, r = _key(path), _key(root)
    return p == r or p.startswith(r + os.sep)


def tmp_zone(path: Path) -> tuple[Path, Path] | None:
    """Return (project_root, tmp_root) when ``path`` lies strictly below ``<project>/.omni/tmp``."""
    parts = path.parts
    lowered = [p.lower() for p in parts]
    for i in range(1, len(parts) - 2):
        if lowered[i] == ".omni" and lowered[i + 1] == "tmp":
            return Path(*parts[:i]), Path(*parts[: i + 2])
    return None


def _check_literal(raw: str, ins: Inspection) -> Path | None:
    # Wildcards are never literal.  %/$/` may be part of a real name -- a mis-expanded
    # "C:/workspace/%SystemDrive%" is exactly the kind of debris to clean -- so they pass
    # only when that literal path exists; an unexpanded variable that names nothing is refused.
    if not raw or any(ch in "*?" for ch in raw) or (any(ch in UNEXPANDED for ch in raw) and not os.path.lexists(raw)):
        ins.problems.append("目标必须是不含通配符和未展开变量的字面路径")
        return None
    if not os.path.isabs(raw) or (os.name == "nt" and not ntpath.splitdrive(raw)[0]):
        ins.problems.append("目标必须是带盘符的绝对路径")
        return None
    if ".." in Path(raw).parts:
        ins.problems.append("目标路径不能含 ..")
        return None
    return Path(os.path.normpath(raw))


def _check_ancestors(target: Path, ins: Inspection) -> None:
    links = []
    for part in [*reversed(target.parents), target]:
        try:
            info = os.lstat(part)
        except FileNotFoundError:
            continue
        if _is_link(info):
            links.append(str(part))
    ins.checks["ancestor_links"] = links
    if links:
        ins.problems.append("目标或其祖先是链接/重解析点：" + "；".join(links))


def _link_destination(path: str) -> str | None:
    try:
        dest = _plain(os.readlink(path))
    except OSError:
        return None
    return os.path.normpath(dest if os.path.isabs(dest) else os.path.join(os.path.dirname(_plain(path)), dest))


def _walk(target: Path, ins: Inspection) -> None:
    root = str(target)
    external: list[str] = []
    hardlinks: list[str] = []
    repos: list[str] = []
    stack = [_long(root)]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as error:
            ins.problems.append(f"无法列出目录 {_plain(current)}：{error}")
            continue
        for entry in entries:
            shown = _plain(entry.path)
            info = entry.stat(follow_symlinks=False)
            if _is_link(info) or entry.is_symlink():
                dest = _link_destination(entry.path)
                if dest is not None and _within(dest, root):
                    ins.internal_links.append(entry.path)
                else:
                    external.append(f"{shown} -> {dest or '无法读取目标'}")
                continue
            if entry.name == ".git":
                repos.append(shown)
            if entry.is_dir(follow_symlinks=False):
                ins.dirs += 1
                stack.append(entry.path)
                continue
            ins.files += 1
            # DirEntry.stat() leaves st_nlink at 0 on Windows; lstat the file itself.
            full = os.lstat(entry.path)
            ins.bytes += full.st_size
            if full.st_nlink > 1:
                hardlinks.append(shown)
    ins.hardlinked = hardlinks
    ins.checks.update(internal_links=len(ins.internal_links), external_links=external[:MAX_LISTED],
                      hardlinks=hardlinks[:MAX_LISTED], git_dirs=repos[:MAX_LISTED])
    if external:
        ins.problems.append(f"树内有 {len(external)} 个指向树外或读不出目标的链接，例如 {external[0]}；先单独核验处理链接对象")
    if repos:
        ins.problems.append(f"树内有 Git 仓库或 worktree（{repos[0]}），worktree 用 git worktree remove 收尾")


_WIN32_NAMES: dict[str, Any] = {}


def _hardlink_names(path: str) -> list[str] | None:
    """Every name of one file (Windows FindFirstFileNameW / FindNextFileNameW); None when not listable."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    if not _WIN32_NAMES:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.FindFirstFileNameW.restype = wintypes.HANDLE
        k32.FindFirstFileNameW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR]
        k32.FindNextFileNameW.restype = wintypes.BOOL
        k32.FindNextFileNameW.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR]
        k32.FindClose.argtypes = [wintypes.HANDLE]
        _WIN32_NAMES.update(k32=k32, invalid=wintypes.HANDLE(-1).value, DWORD=wintypes.DWORD)
    k32, DWORD = _WIN32_NAMES["k32"], _WIN32_NAMES["DWORD"]
    drive = ntpath.splitdrive(_plain(path))[0]
    buf = ctypes.create_unicode_buffer(32768)
    size = DWORD(32768)
    handle = k32.FindFirstFileNameW(_long(path), 0, ctypes.byref(size), buf)
    if not handle or handle == _WIN32_NAMES["invalid"]:
        return None
    names = [drive + buf.value]
    try:
        while True:
            size = DWORD(32768)
            if not k32.FindNextFileNameW(handle, ctypes.byref(size), buf):
                break
            names.append(drive + buf.value)
    finally:
        k32.FindClose(handle)
    return names


def _declared_cache_roots(archive: Any) -> list[str]:
    from omnicompany.core import dir_archive

    try:
        loaded = archive if archive is not None else dir_archive.load_archive()
    except Exception:  # noqa: BLE001 - without the archive no cache root is trusted
        return []
    return [d.path for d in loaded.declarations if d.host == "local" and d.role == "cache"]


def _check_hardlinks(target: Path, ins: Inspection, archive: Any) -> None:
    """A file with another hard link is fine when every other name is inside the tree, inside a
    declared cache (e.g. the pnpm store), or inside some ``node_modules`` (another install of the
    same package).  Deleting one name never deletes the data the other names keep."""
    files = getattr(ins, "hardlinked", [])
    if not files:
        return
    root = str(target)
    marker = os.sep + "node_modules" + os.sep
    cache_roots: list[str] | None = None
    unresolved: list[str] = []
    foreign: list[str] = []
    counts = {"node_modules": 0, "cache": 0}
    for file in files:
        names = _hardlink_names(file)
        if names is None:
            unresolved.append(file)
            continue
        for name in names:
            if _within(name, root):
                continue
            if marker in os.path.normcase(name):
                counts["node_modules"] += 1
                continue
            if cache_roots is None:
                cache_roots = _declared_cache_roots(archive)
            if any(_within(name, root_) for root_ in cache_roots):
                counts["cache"] += 1
                continue
            foreign.append(f"{file} = {name}")
    ins.checks["hardlinks"] = {"files": len(files), "other_names": counts,
                               "unresolved": unresolved[:MAX_LISTED], "foreign": foreign[:MAX_LISTED]}
    if unresolved:
        ins.problems.append(f"树内有 {len(unresolved)} 个文件另有硬链接，但列不出它们的其它名字，例如 {unresolved[0]}")
    if foreign:
        ins.problems.append(f"树内有 {len(foreign)} 个硬链接的其它名字落在树外，既不在已登记的缓存区，也不在其它 node_modules 里，"
                            f"例如 {foreign[0]}")


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def _check_git(target: Path, ins: Inspection) -> None:
    probe = _git(["rev-parse", "--show-toplevel"], target)
    if probe.returncode != 0:
        ins.checks["git"] = "not in a repository"
        return
    top = Path(probe.stdout.strip())
    rel = os.path.relpath(target, top)
    tracked = _git(["ls-files", "-z", "--", rel], top)
    names = [n for n in tracked.stdout.split("\0") if n]
    ins.checks["git"] = {"toplevel": str(top), "tracked_files": len(names)}
    if tracked.returncode != 0:
        ins.problems.append(f"git ls-files 失败：{tracked.stderr.strip()[:200]}")
    elif names:
        ins.problems.append(f"目录里有 {len(names)} 个 Git 跟踪文件（例如 {names[0]}），不是临时内容")
    worktrees = _git(["worktree", "list", "--porcelain"], top)
    inside = [line[9:] for line in worktrees.stdout.splitlines()
              if line.startswith("worktree ") and _within(line[9:], str(target))]
    if inside:
        ins.problems.append("目录里登记着 worktree：" + "；".join(inside) + "，用 git worktree remove 收尾")


def _process_rows() -> Iterable[dict[str, Any]]:
    """Every process except this one and its ancestors (whose command lines name the target)."""
    import psutil

    me = psutil.Process()
    skip = {me.pid, *(p.pid for p in me.parents())}
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        if proc.pid in skip:
            continue
        row = dict(proc.info)
        try:
            row["cwd"] = proc.cwd()
        except (psutil.Error, OSError):
            row["cwd"] = None
        yield row


def _check_processes(target: Path, ins: Inspection, rows: Callable[[], Iterable[dict[str, Any]]]) -> None:
    root = _key(str(target))
    hits = []
    for row in rows():
        texts = [row.get("exe") or "", row.get("cwd") or ""]
        # _process_rows excludes this diagnostic's own PID and its observed parent chain.
        # Every other process, including shells, must pass the same path checks.
        texts.append(" ".join(row.get("cmdline") or []))
        if any(root in os.path.normcase(t.replace("/", os.sep)) for t in texts if t):
            hits.append(f"{row.get('pid')} {row.get('name')}")
    ins.checks["processes"] = hits[:MAX_LISTED]
    if hits:
        ins.problems.append("有进程在用这个目录（程序、命令行或工作目录在里面）：" + "；".join(hits[:10]))


def _archive_zone(target: Path, ins: Inspection, archive: Any) -> bool:
    """Outside ``.omni/tmp``: the directory semantic archive must give a delete basis."""
    from omnicompany.core import dir_archive

    try:
        answer = dir_archive.explain(str(target), archive=archive)
    except Exception as error:  # noqa: BLE001 - a broken declaration source refuses, it does not delete
        ins.problems.append(f"不在 <项目>/.omni/tmp/<任务>/ 下，且目录语义档案读取失败：{error}")
        return False
    verdict = answer["delete"]
    ins.checks["archive"] = {"role": answer.get("role"), "owner": answer.get("owner"),
                             "declared_in": answer.get("declared_in"), "basis": verdict["basis"]}
    if not verdict["allowed"]:
        ins.problems.append("不在 <项目>/.omni/tmp/<任务>/ 下，目录语义档案也没有给出删除依据："
                            + "；".join(verdict["problems"]))
        return False
    return True


def _evidence_hold(target: Path, ins: Inspection) -> None:
    """A task batch whose own record.json marks unexpired human evidence is kept."""
    from omnicompany.core import dir_archive

    manifest_file = target / "record.json"
    if not manifest_file.is_file():
        return
    manifest = dir_archive._read_manifest(manifest_file).get("manifest") or {}
    expires = dir_archive._parse_time(manifest.get("expires"))
    if manifest.get("human_evidence") and (expires is None or expires > dir_archive.datetime.now(dir_archive.timezone.utc)):
        ins.problems.append(f"批次清单标为给人看的证据且未到期（{manifest_file}，expires={manifest.get('expires')}），按清单保留")


def inspect(raw: str, *, process_rows: Callable[[], Iterable[dict[str, Any]]] = _process_rows,
            archive: Any = None, basis_override: str | None = None) -> Inspection:
    ins = Inspection(target=raw)
    target = _check_literal(raw, ins)
    if target is None:
        return ins
    ins.target = str(target)
    zone = tmp_zone(target)
    if basis_override:
        # Only for callers that established the basis themselves (worktree_closeout leftovers);
        # every disk check below still runs.
        ins.checks["archive"] = {"basis": basis_override}
    elif zone is None:
        if not _archive_zone(target, ins, archive):
            return ins
    else:
        ins.project_root = str(zone[0])
        _evidence_hold(target, ins)
        if ins.problems:
            return ins
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        ins.problems.append("目标不存在")
        return ins
    _check_ancestors(target, ins)
    if ins.problems:
        return ins
    if not stat.S_ISDIR(info.st_mode):
        ins.problems.append("目标不是目录；单个文件直接删除即可")
        return ins
    _walk(target, ins)
    _check_hardlinks(target, ins, archive)
    _check_git(target, ins)
    _check_processes(target, ins, process_rows)
    return ins


def _clear_readonly(func, path, _exc) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove_link_object(path: str) -> None:
    info = os.lstat(path)
    if stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_DIRECTORY:
        os.rmdir(path)  # junction or directory symlink: removes the link, never the target's content
    else:
        os.unlink(path)


def remove(raw: str, *, dry_run: bool = False, reason: str = "", process_rows=_process_rows,
           log_path: Path | None = None, archive: Any = None, basis_override: str | None = None) -> dict[str, Any]:
    started = time.time()
    ins = inspect(raw, process_rows=process_rows, archive=archive, basis_override=basis_override)
    result: dict[str, Any] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "target": ins.target, "project_root": ins.project_root,
        "reason": reason, "files": ins.files, "dirs": ins.dirs, "bytes": ins.bytes,
        "checks": ins.checks, "problems": list(ins.problems),
        "actor": {k: os.environ[k] for k in ("CLAUDE_SESSION_ID", "CODEX_SESSION_ID", "OMNI_SESSION_ID") if k in os.environ},
    }
    if not ins.ok:
        result["outcome"] = "refused"
    elif dry_run:
        result["outcome"] = "dry-run"
    else:
        for link in sorted(ins.internal_links, key=len, reverse=True):
            _remove_link_object(link)
        shutil.rmtree(_long(ins.target), onexc=_clear_readonly)
        if os.path.lexists(ins.target):
            result["outcome"] = "incomplete"
            result["problems"].append("删除后目录仍在")
        else:
            result["outcome"] = "deleted"
    result["seconds"] = round(time.time() - started, 2)
    log = log_path or receipt_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    result["receipt_log"] = str(log)
    return result


def _snapshot_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        return subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return subprocess.CompletedProcess(["git", *args], 124, stdout, stderr + "\nverification command timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(["git", *args], 127, "", f"{type(exc).__name__}: {exc}")


def _snapshot_inventory(target: Path) -> tuple[dict[str, os.stat_result], list[str], list[str]]:
    """Nofollow inventory for the narrowly typed standalone-snapshot cleanup path."""
    files: dict[str, os.stat_result] = {}
    directories: list[str] = []
    links: list[str] = []
    for ancestor in [*reversed(target.parents), target]:
        if os.path.lexists(ancestor) and _is_link(os.lstat(ancestor)):
            return files, directories, [str(ancestor)]
    pending = [target]
    while pending:
        directory = pending.pop()
        directories.append(os.path.relpath(directory, target).replace(os.sep, "/"))
        with os.scandir(_long(str(directory))) as entries:
            for entry in entries:
                path = Path(_plain(entry.path))
                relative = path.relative_to(target).as_posix()
                info = entry.stat(follow_symlinks=False)
                if _is_link(info) or entry.is_symlink():
                    links.append(relative)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    files[relative] = os.lstat(path)
                else:
                    links.append(relative)
    return files, directories, links


def _bundle_header(bundle: Path) -> dict[str, Any]:
    """Read only the bounded Git bundle header; never inspect/scan its pack body."""
    references: dict[str, str] = {}
    prerequisites: list[str] = []
    filters: list[str] = []
    total = 0
    with bundle.open("rb") as handle:
        version = handle.readline(65537)
        total += len(version)
        if len(version) > 65536 or version.rstrip(b"\r\n") not in (b"# v2 git bundle", b"# v3 git bundle"):
            raise ValueError("Git bundle header version is invalid")
        while True:
            line = handle.readline(65537)
            total += len(line)
            if not line or len(line) > 65536 or total > 1024 * 1024:
                raise ValueError("Git bundle header is missing a bounded terminator")
            if line in (b"\n", b"\r\n"):
                break
            text = line.decode("ascii", "strict").rstrip("\r\n")
            if text.startswith("@"):
                if text.casefold().startswith("@filter="):
                    filters.append(text)
                continue
            if text.startswith("-"):
                prerequisites.append(text[1:])
                continue
            fields = text.split(" ", 1)
            if (len(fields) != 2 or len(fields[0]) not in (40, 64) or not fields[1]
                    or any(character not in "0123456789abcdefABCDEF" for character in fields[0])
                    or fields[1] in references):
                raise ValueError("Git bundle reference header is malformed/duplicated")
            references[fields[1]] = fields[0].lower()
    if not references:
        raise ValueError("Git bundle has no named references")
    return {"version": version.decode("ascii").rstrip("\r\n"), "references": references,
            "prerequisites": prerequisites, "filters": filters}


def _verify_bundle_independent(bundle: Path) -> tuple[subprocess.CompletedProcess[str] | None,
                                                     subprocess.CompletedProcess[str] | None]:
    """Verify the bundle in a fresh empty bare repository, never using target objects as support."""
    with tempfile.TemporaryDirectory(prefix="omni-snapshot-bundle-check-") as temporary:
        empty = Path(temporary)
        initialized = _snapshot_git(["init", "--bare", "-q"], empty)
        if initialized.returncode:
            return initialized, None
        return (_snapshot_git(["bundle", "verify", str(bundle)], empty),
                _snapshot_git(["bundle", "list-heads", str(bundle)], empty))


def _set_pinned_readonly(pin: Any, path: Path, expected: dict[str, Any], readonly: bool) -> dict[str, Any]:
    """Change only FILE_ATTRIBUTE_READONLY through a handle bound to the pinned native ID."""
    if os.name != "nt":
        raise OSError("native Windows FILE_WRITE_ATTRIBUTES operation is unavailable")
    import ctypes
    from ctypes import wintypes

    class BasicInfo(ctypes.Structure):
        _fields_ = [("creation", ctypes.c_longlong), ("access", ctypes.c_longlong),
                    ("write", ctypes.c_longlong), ("change", ctypes.c_longlong),
                    ("attributes", wintypes.DWORD)]

    class Id128(ctypes.Structure):
        _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    class FileIdInfo(ctypes.Structure):
        _fields_ = [("volume", ctypes.c_ulonglong), ("file_id", Id128)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    query = kernel.GetFileInformationByHandleEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    query.restype = wintypes.BOOL
    set_info = kernel.SetFileInformationByHandle
    set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    set_info.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(_long(str(path)), 0x100, 0x1 | 0x2 | 0x4, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateFileW FILE_WRITE_ATTRIBUTES failed", str(path))
    try:
        identity, basic = FileIdInfo(), BasicInfo()
        if not query(handle, 18, ctypes.byref(identity), ctypes.sizeof(identity)):
            raise OSError(ctypes.get_last_error(), "FileIdInfo query failed", str(path))
        actual_identity = (str(int(identity.volume)), bytes(identity.file_id.bytes).hex())
        if actual_identity != (expected.get("volumeSerial"), expected.get("fid128")):
            raise OSError("FILE_WRITE_ATTRIBUTES handle identity differs from the pinned proof", str(path))
        if pin is not None:
            import msvcrt
            pin_file = getattr(pin, "file", None)
            if pin_file is None:
                raise OSError("held read pin has no native handle", str(path))
            pin_identity = FileIdInfo()
            pin_handle = wintypes.HANDLE(msvcrt.get_osfhandle(pin_file.fileno()))
            if not query(pin_handle, 18, ctypes.byref(pin_identity), ctypes.sizeof(pin_identity)):
                raise OSError(ctypes.get_last_error(), "held read pin FileIdInfo query failed", str(path))
            pinned = (str(int(pin_identity.volume)), bytes(pin_identity.file_id.bytes).hex())
            if pinned != actual_identity:
                raise OSError("FILE_WRITE_ATTRIBUTES handle differs from the held read pin", str(path))
        if not query(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise OSError(ctypes.get_last_error(), "FileBasicInfo query failed", str(path))
        basic.attributes = ((int(basic.attributes) | 0x1) if readonly else (int(basic.attributes) & ~0x1)) or 0x80
        if not set_info(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise OSError(ctypes.get_last_error(), "SetFileInformationByHandle FILE_BASIC_INFO failed", str(path))
    finally:
        close(handle)
    observed = _snapshot_identity(path)
    immutable_fields = set(expected) - {"attributes", "changeFileTime100ns"}
    expected_attributes = ((int(expected.get("attributes", 0)) | 0x1) if readonly
                           else (int(expected.get("attributes", 0)) & ~0x1))
    if (observed is None or any(observed.get(field) != expected.get(field) for field in immutable_fields)
            or int(observed.get("attributes", 0)) != expected_attributes
            or bool(observed.get("attributes", 0) & 0x1) != readonly):
        raise OSError("native readonly attribute update changed metadata beyond its own attribute/ChangeTime", str(path))
    return observed


def _delete_pinned_snapshot_tree(target: Path, files: dict[str, os.stat_result], directories: list[str],
                                 file_pins: dict[str, Any], file_native: dict[str, Any],
                                 directory_native: dict[str, Any],
                                 readonly_mutations: list[dict[str, Any]],
                                 deleted_original_files: list[dict[str, Any]]) -> None:
    """Identity-check the complete tree immediately before deleting only pinned file aliases."""
    from omnicompany.packages.domains.publish.session_local_retention import _delete_pinned_alias

    current_files, current_directories, links = _snapshot_inventory(target)
    if links or set(current_files) != set(files) or set(current_directories) != set(directories):
        raise OSError("target inventory changed before pinned cleanup")
    current_file_native = {relative: _snapshot_identity(target / Path(relative)) for relative in current_files}
    current_directory_native = {
        relative: _snapshot_identity(target if relative == "." else target / Path(relative))
        for relative in current_directories
    }
    if current_file_native != file_native or current_directory_native != directory_native:
        raise OSError("target native identity/metadata changed before pinned cleanup")
    for relative in sorted(current_files, key=lambda value: value.count("/"), reverse=True):
        pin = file_pins.get(relative)
        if pin is None:
            raise OSError(f"missing read pin for {relative}")
        path = target / Path(relative)
        info = os.lstat(path)
        read_only = bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_READONLY", 0x1))
        before = file_native[relative]
        if _snapshot_identity(path) != before:
            raise OSError(f"file full native metadata changed immediately before delete: {path}")
        mutation = None
        try:
            expected_before_delete = before
            if read_only:
                if pin.identity != {"device": int(info.st_dev), "file_id": int(info.st_ino)}:
                    raise OSError(f"read-only alias no longer names the pinned file: {path}")
                changed = _set_pinned_readonly(pin, path, before, readonly=False)
                mutation = {"path": str(path), "before": before, "afterClear": changed,
                            "operation": "native FILE_WRITE_ATTRIBUTES clear READONLY only"}
                readonly_mutations.append(mutation)
                expected_before_delete = changed
            if _snapshot_identity(path) != expected_before_delete:
                raise OSError(f"file full native metadata changed after readonly preparation: {path}")
            _delete_pinned_alias(pin, path)
            deleted_original_files.append({"relative": relative,
                                           "logicalBytes": int(before["logicalBytes"]),
                                           "native": before})
        except Exception:
            if read_only and mutation is not None and os.path.lexists(path):
                try:
                    mutation["afterRestore"] = _set_pinned_readonly(
                        pin, path, mutation["afterClear"], readonly=True)
                except Exception as restore_error:
                    mutation["restoreError"] = f"{type(restore_error).__name__}: {restore_error}"
            raise
    for relative in sorted((value for value in current_directories if value != "."),
                           key=lambda value: value.count("/"), reverse=True):
        os.rmdir(_long(str(target / Path(relative))))
    os.rmdir(_long(str(target)))


def _snapshot_identity(path: Path) -> dict[str, int | str | bool] | None:
    """Capture native full identity without following reparse points or using hashes."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class BasicInfo(ctypes.Structure):
            _fields_ = [("creation", ctypes.c_longlong), ("access", ctypes.c_longlong),
                        ("write", ctypes.c_longlong), ("change", ctypes.c_longlong),
                        ("attributes", wintypes.DWORD)]

        class StandardInfo(ctypes.Structure):
            _fields_ = [("allocation", ctypes.c_longlong), ("eof", ctypes.c_longlong),
                        ("links", wintypes.DWORD), ("delete_pending", wintypes.BOOLEAN),
                        ("directory", wintypes.BOOLEAN)]

        class Id128(ctypes.Structure):
            _fields_ = [("bytes", ctypes.c_ubyte * 16)]

        class FileIdInfo(ctypes.Structure):
            _fields_ = [("volume", ctypes.c_ulonglong), ("file_id", Id128)]

        class AttributeTagInfo(ctypes.Structure):
            _fields_ = [("attributes", wintypes.DWORD), ("reparse_tag", wintypes.DWORD)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        query = kernel.GetFileInformationByHandleEx
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        query.restype = wintypes.BOOL
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        handle = create(_long(str(path)), 0x80, 0x1 | 0x2 | 0x4, None, 3, 0x02000000 | 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            return None
        try:
            basic, standard, identity, tag = BasicInfo(), StandardInfo(), FileIdInfo(), AttributeTagInfo()
            for info, kind in ((basic, 0), (standard, 1), (tag, 9), (identity, 18)):
                if not query(handle, kind, ctypes.byref(info), ctypes.sizeof(info)):
                    return None
            attrs = int(tag.attributes)
            return {
                "volumeSerial": str(int(identity.volume)), "fid128": bytes(identity.file_id.bytes).hex(),
                "allocationBytes": int(standard.allocation), "logicalBytes": int(standard.eof),
                "nlink": int(standard.links), "deletePending": bool(standard.delete_pending),
                "directory": bool(standard.directory), "creationFileTime100ns": int(basic.creation),
                "writeFileTime100ns": int(basic.write), "changeFileTime100ns": int(basic.change),
                "attributes": attrs, "reparse": bool(attrs & REPARSE), "reparseTag": int(tag.reparse_tag),
            }
        finally:
            close(handle)
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return {
        "volumeSerial": str(int(info.st_dev)), "fid128": f"{int(info.st_ino):032x}",
        "allocationBytes": int(getattr(info, "st_blocks", 0) * 512),
        "logicalBytes": int(info.st_size), "nlink": int(info.st_nlink),
        "deletePending": False, "directory": bool(stat.S_ISDIR(info.st_mode)),
        "creationFileTime100ns": int(getattr(info, "st_birthtime_ns", info.st_ctime_ns) // 100),
        "writeFileTime100ns": int(info.st_mtime_ns // 100), "changeFileTime100ns": int(info.st_ctime_ns // 100),
        "attributes": int(getattr(info, "st_file_attributes", 0)),
        "reparse": bool(getattr(info, "st_file_attributes", 0) & REPARSE), "reparseTag": 0,
    }


def _snapshot_refusal(result: dict[str, Any], problem: str) -> None:
    result["problems"].append(problem)
    result["outcome"] = "refused"


def _strict_native_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an exact integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    raise ValueError(f"{field} must be an exact integer or decimal string")


def _legacy_map_members(embedded: dict[str, Any], *, source_root: Path, target: Path,
                        proof_members: list[dict[str, Any]], zip_path: Path,
                        zip_names: set[str]) -> None:
    """Validate the existing full-worktree recovery map and bind its target subset."""
    expected_top = {"schema", "createdUtc", "archivePath", "sourceRoot", "git", "fileCount",
                    "logicalBytes", "files"}
    if set(embedded) != expected_top or embedded.get("schema") != "hero-preflight-evidence-recovery-map-v1":
        raise ValueError("unsupported legacy recovery-map schema/fields")
    if (_key(str(embedded.get("sourceRoot", ""))) != _key(str(source_root))
            or _key(str(embedded.get("archivePath", ""))) != _key(str(zip_path))
            or not isinstance(embedded.get("createdUtc"), str)
            or not isinstance(embedded.get("git"), dict)):
        raise ValueError("legacy recovery-map root/archive metadata conflicts with proof")
    rows = embedded.get("files")
    if not isinstance(rows, list) or _strict_native_int(embedded.get("fileCount"), "fileCount") != len(rows):
        raise ValueError("legacy recovery-map fileCount does not match files")
    total = 0
    seen_sources: set[str] = set()
    seen_relatives: set[str] = set()
    seen_members: set[str] = set()
    archive_members: set[str] = set()
    selected: dict[str, dict[str, Any]] = {}
    required_native = {"volume", "fid128", "allocationBytes", "logicalBytes", "nlink", "attributes",
                       "reparse", "deletePending", "directory", "creationFileTime100ns",
                       "writeFileTime100ns", "changeFileTime100ns"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"source", "relativePath", "zipMember", "logicalBytes",
                                                      "native", "gitIndex"}:
            raise ValueError(f"legacy recovery-map row {index} has invalid shape")
        source_text, relative_text, member = row["source"], row["relativePath"], row["zipMember"]
        logical = _strict_native_int(row["logicalBytes"], f"row[{index}].logicalBytes")
        native = row["native"]
        if (not isinstance(source_text, str) or not os.path.isabs(source_text)
                or any(part in {".", ".."} for part in Path(source_text).parts)
                or not isinstance(relative_text, str) or not isinstance(member, str)
                or not isinstance(row["gitIndex"], dict) or not isinstance(native, dict)
                or set(native) != required_native):
            raise ValueError(f"legacy recovery-map row {index} has invalid path/native/index fields")
        relative = Path(relative_text)
        zip_member = Path(member)
        if (relative.is_absolute() or zip_member.is_absolute() or member.startswith(("/", "\\"))
                or ".." in relative.parts or ".." in zip_member.parts
                or any(part in {".", ""} for part in relative_text.split("/"))
                or "\\" in relative_text or "\\" in member):
            raise ValueError(f"legacy recovery-map row {index} has unsafe member path")
        archive_members.add(member)
        source = Path(source_text)
        try:
            expected_relative = source.relative_to(source_root).as_posix()
        except ValueError as exc:
            raise ValueError(f"legacy recovery-map row {index} source escapes sourceRoot") from exc
        if expected_relative != relative_text:
            raise ValueError(f"legacy recovery-map row {index} relativePath conflicts with source")
        source_key, relative_key, member_key = _key(str(source)), relative_text.casefold(), member.casefold()
        if source_key in seen_sources or relative_key in seen_relatives or member_key in seen_members:
            raise ValueError(f"legacy recovery-map row {index} duplicates a source/path/member")
        seen_sources.add(source_key)
        seen_relatives.add(relative_key)
        seen_members.add(member_key)
        integer_fields = ("allocationBytes", "logicalBytes", "nlink", "attributes")
        normalized_native: dict[str, Any] = {"volumeSerial": str(_strict_native_int(native["volume"], "volume")),
                                             "fid128": native["fid128"]}
        if (not isinstance(native["fid128"], str) or len(native["fid128"]) != 32
                or any(character not in "0123456789abcdefABCDEF" for character in native["fid128"])):
            raise ValueError(f"legacy recovery-map row {index} fid128 is invalid")
        for field in integer_fields:
            normalized_native[field] = _strict_native_int(native[field], field)
        for field in ("reparse", "deletePending", "directory"):
            if not isinstance(native[field], bool):
                raise ValueError(f"legacy recovery-map row {index} {field} must be boolean")
            normalized_native[field] = native[field]
        for field in ("creationFileTime100ns", "writeFileTime100ns", "changeFileTime100ns"):
            normalized_native[field] = _strict_native_int(native[field], field)
        if (logical < 0 or normalized_native["logicalBytes"] != logical
                or normalized_native["allocationBytes"] < 0 or normalized_native["nlink"] < 1):
            raise ValueError(f"legacy recovery-map row {index} size/link fields conflict")
        total += logical
        if _within(source_text, str(target)):
            try:
                target_relative = source.relative_to(target).as_posix()
            except ValueError as exc:
                raise ValueError(f"legacy recovery-map row {index} has inconsistent target containment") from exc
            selected[source_key] = {"relative": target_relative, "member": member,
                                    "logicalBytes": logical, "native": normalized_native}
    if total != _strict_native_int(embedded.get("logicalBytes"), "logicalBytes"):
        raise ValueError("legacy recovery-map aggregate logicalBytes conflicts")
    if archive_members != zip_names - {"recovery-map.json"}:
        raise ValueError("legacy recovery-map and ZIP member inventory differ")
    expected_by_source: dict[str, dict[str, Any]] = {}
    for member in proof_members:
        if not isinstance(member, dict) or not isinstance(member.get("source"), str):
            raise ValueError("typed proof member is malformed")
        expected_by_source[_key(member["source"])] = member
    if set(selected) != set(expected_by_source):
        raise ValueError("legacy recovery-map target subset is missing/conflicting members")
    for source_key, row in selected.items():
        member = expected_by_source[source_key]
        native = member.get("native")
        if not isinstance(native, dict):
            raise ValueError("typed proof member native metadata is malformed")
        projected = {key: value for key, value in native.items() if key != "reparseTag"}
        if "volumeSerial" in projected:
            projected["volumeSerial"] = str(projected["volumeSerial"])
        for field in ("creationFileTime100ns", "writeFileTime100ns", "changeFileTime100ns"):
            projected[field] = _strict_native_int(projected[field], field)
        if (row["relative"] != member.get("relative") or row["member"] != member.get("member")
                or row["logicalBytes"] != _strict_native_int(native.get("logicalBytes"), "logicalBytes")
                or row["native"] != projected):
            raise ValueError(f"legacy recovery-map target row conflicts with proof: {member['source']}")


def remove_git_snapshot(raw: str, proof_path: str, *, dry_run: bool = False, reason: str = "",
                        process_rows=_process_rows, log_path: Path | None = None,
                        archive: Any = None) -> dict[str, Any]:
    """Remove one standalone Git snapshot only when its typed recovery proof is complete.

    This is intentionally separate from ``rmtree``: the ordinary path continues to reject every
    nested repository.  The proof binds one target, its registered non-main parent worktree, an
    active formal archive ZIP, every raw member, and a verified native Git bundle.
    """
    started = time.time()
    result: dict[str, Any] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "target": raw,
                              "proof": proof_path, "reason": reason, "files": 0, "dirs": 0,
                              "bytes": 0, "deletedOriginalFiles": [], "deletedOriginalBytes": 0,
                              "remainingOriginalFiles": [], "checks": {}, "problems": []}
    target = _check_literal(raw, Inspection(target=raw))
    if target is None:
        _snapshot_refusal(result, "目标必须是带盘符且不含变量/通配符/..的绝对字面路径")
    try:
        proof = json.loads(Path(proof_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        proof = None
        _snapshot_refusal(result, f"proof JSON无法读取：{type(exc).__name__}: {exc}")
    if not result["problems"] and (not isinstance(proof, dict) or proof.get("schema") != "omni.git-snapshot-proof.v1"):
        _snapshot_refusal(result, "proof schema必须是 omni.git-snapshot-proof.v1")
    if result["problems"]:
        pass
    else:
        assert target is not None and isinstance(proof, dict)
        if _key(str(target)) != _key(str(proof.get("target", ""))):
            _snapshot_refusal(result, "proof target与命令目标不一致")
        if not os.path.isabs(proof_path) or _within(proof_path, str(target)):
            _snapshot_refusal(result, "proof必须是目标树外的绝对路径")
        zone = tmp_zone(target)
        if zone is None:
            _snapshot_refusal(result, "目标不在项目 .omni/tmp 下")
        else:
            project_root, tmp_root = zone
            parent = Path(str(proof.get("parent_worktree", "")))
            try:
                relative = target.relative_to(tmp_root)
            except ValueError:
                relative = Path("..")
            if (len(relative.parts) != 1 or not _within(str(project_root), str(parent))
                    or not _within(str(target), str(parent))):
                _snapshot_refusal(result, "目标必须是已登记父worktree内部项目 .omni/tmp 的直接任务子目录")
            result["checks"]["parent_worktree"] = str(parent)
            # Parent must be a registered, non-main worktree of a repository.  A clone's own
            # worktree list containing only itself does not satisfy this requirement.
            parent_git_file = parent / ".git"
            try:
                parent_git_info = os.lstat(parent_git_file)
            except OSError:
                parent_git_info = None
            parent_top = _snapshot_git(["rev-parse", "--show-toplevel"], parent)
            parent_git_dir = _snapshot_git(["rev-parse", "--path-format=absolute", "--git-dir"], parent)
            parent_common = _snapshot_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], parent)
            parent_list = _snapshot_git(["worktree", "list", "--porcelain"], parent)
            parent_registered = parent_list.returncode == 0 and any(
                line.startswith("worktree ") and _key(line[9:]) == _key(str(parent))
                for line in parent_list.stdout.splitlines())
            if (parent_git_info is None or _is_link(parent_git_info) or not stat.S_ISREG(parent_git_info.st_mode)
                    or parent_top.returncode or parent_git_dir.returncode
                    or parent_common.returncode or parent_list.returncode or not parent_registered
                    or _key(parent_top.stdout.strip()) != _key(str(parent))
                    or _key(parent_git_dir.stdout.strip()) == _key(parent_common.stdout.strip())):
                _snapshot_refusal(result, "父项目必须是已登记的非主Git worktree（独立main clone不满足）")
            result["checks"]["parent_git"] = {"top": parent_top.stdout.strip(),
                                               "registered": parent_registered,
                                               "is_non_main": bool(parent_git_dir.stdout.strip()
                                                                    and parent_common.stdout.strip()
                                                                    and _key(parent_git_dir.stdout.strip())
                                                                    != _key(parent_common.stdout.strip()))}
            # Target itself must be a clean standalone main clone, not a nested registered
            # worktree or a mistaken parent-repository path.
            target_git_dir = _snapshot_git(["rev-parse", "--path-format=absolute", "--git-dir"], target)
            target_common = _snapshot_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], target)
            target_top = _snapshot_git(["rev-parse", "--show-toplevel"], target)
            target_worktree_list = _snapshot_git(["worktree", "list", "--porcelain"], target)
            target_worktrees = [line[9:] for line in target_worktree_list.stdout.splitlines()
                                if line.startswith("worktree ")]
            result["checks"]["target_worktrees"] = target_worktrees
            if (target_worktree_list.returncode or len(target_worktrees) != 1
                    or _key(target_worktrees[0]) != _key(str(target))):
                _snapshot_refusal(result, "目标main clone登记了其它worktree，不能移除其Git共同目录")
            status = _snapshot_git(["status", "--porcelain=v1", "--untracked-files=all"], target)
            refs = _snapshot_git(["for-each-ref", "--format=%(refname) %(objectname)"], target)
            head = _snapshot_git(["rev-parse", "HEAD"], target)
            if (not (target / ".git").is_dir() or any(p.returncode for p in
                    (target_git_dir, target_common, target_top, status, refs, head))
                    or _key(target_top.stdout.strip()) != _key(str(target))
                    or _key(target_git_dir.stdout.strip()) != _key(target_common.stdout.strip())
                    or status.stdout.strip()):
                _snapshot_refusal(result, "目标不是clean的独立Git main clone，或Git身份/工作区检查失败")
            actual_refs = {}
            for line in refs.stdout.splitlines():
                fields = line.split(" ", 1)
                if len(fields) == 2:
                    actual_refs[fields[0]] = fields[1]
            expected_refs = proof.get("git", {}).get("refs") if isinstance(proof.get("git"), dict) else None
            if (not isinstance(expected_refs, dict) or expected_refs != actual_refs
                    or proof.get("git", {}).get("head") != head.stdout.strip()):
                _snapshot_refusal(result, "proof中的Git HEAD/全部refs与当前目标不一致")
            bundle = Path(str(proof.get("git", {}).get("bundle_path", "")))
            if not os.path.isabs(str(bundle)) or _within(str(bundle), str(target)):
                _snapshot_refusal(result, "Git bundle必须是目标树外的绝对路径")
            bundle_native = _snapshot_identity(bundle)
            header = None
            if (not result["problems"] and bundle_native is not None and not bundle_native.get("reparse")
                    and not bundle_native.get("deletePending") and not bundle_native.get("directory")):
                try:
                    header = _bundle_header(bundle)
                except (OSError, ValueError, UnicodeError) as exc:
                    _snapshot_refusal(result, f"Git bundle header读取失败：{type(exc).__name__}: {exc}")
            elif not result["problems"]:
                _snapshot_refusal(result, "Git bundle不是普通文件或native身份未知")
            if header is not None:
                result["checks"]["bundle_header"] = header
                if header["prerequisites"] or header["filters"]:
                    _snapshot_refusal(result, "Git bundle含prerequisite或filter capability")
                header_refs = header["references"]
                if ({name: oid for name, oid in header_refs.items() if name != "HEAD"} != actual_refs
                        or ("HEAD" in header_refs and header_refs["HEAD"] != head.stdout.strip())):
                    _snapshot_refusal(result, "Git bundle header refs name/OID与当前clone全部refs不一致")
            verify, heads = ((None, None) if result["problems"] or header is None
                             else _verify_bundle_independent(bundle))
            bundle_refs = {}
            if heads and heads.returncode == 0:
                for line in heads.stdout.splitlines():
                    fields = line.split(" ", 1)
                    if len(fields) == 2:
                        bundle_refs[fields[1]] = fields[0]
            if (verify is None or heads is None or verify.returncode or heads.returncode
                    or {name: oid for name, oid in bundle_refs.items() if name != "HEAD"} != actual_refs or head.stdout.strip() not in bundle_refs.values()):
                _snapshot_refusal(result, "Git bundle未能verify或未覆盖当前全部refs与HEAD")
            result["checks"]["git"] = {"head": head.stdout.strip(), "refs": actual_refs,
                                        "bundle_verified": bool(verify and verify.returncode == 0),
                                        "bundle_head_count": len(bundle_refs)}
            from omnicompany.core import dir_archive
            loaded_archive = archive if archive is not None else dir_archive.load_archive()
            archive_root = Path(str(proof.get("archive_root", "")))
            zip_path = Path(str(proof.get("zip_path", "")))
            declaration = next((d for d in loaded_archive.declarations
                                if d.host == "local" and d.role == "archive" and d.status == "active"
                                and _key(d.path) == _key(str(archive_root))
                                and _within(str(zip_path), d.path)
                                and _within(str(archive_root), d.path)), None)
            answer = dir_archive.explain(zip_path, archive=loaded_archive)
            if (declaration is None or answer.get("links") or answer.get("conflicts")
                    or answer.get("role") != "archive" or not answer.get("formal")):
                _snapshot_refusal(result, "ZIP必须位于无链接冲突的active正式archive声明根内")
            if (_within(str(zip_path), str(target)) or _within(str(bundle), str(target))
                    or not _within(str(zip_path), str(archive_root))
                    or not _within(str(bundle), str(archive_root))):
                _snapshot_refusal(result, "ZIP与Git bundle必须位于源树外的正式archive根内")
            archive_file_native = {str(path): _snapshot_identity(path) for path in (zip_path, bundle)}
            if any(meta is None or meta.get("nlink") != 1 or meta.get("reparse")
                   or meta.get("deletePending") or meta.get("directory")
                   for meta in archive_file_native.values()):
                _snapshot_refusal(result, "ZIP/Git bundle含硬链接、reparse或delete-pending")
            if proof.get("archiveFiles") != archive_file_native:
                _snapshot_refusal(result, "ZIP/Git bundle full native metadata与proof不一致")
            result["checks"]["archive"] = {"root": str(archive_root), "zip": str(zip_path),
                                            "declared": declaration is not None,
                                            "role": answer.get("role"), "formal": answer.get("formal"),
                                            "links": answer.get("links"), "conflicts": answer.get("conflicts")}
            hold = Inspection(target=str(target))
            _evidence_hold(target, hold)
            if hold.problems:
                _snapshot_refusal(result, hold.problems[0])
            pin_stack = ExitStack()
            deleted_original_files: list[dict[str, Any]] = []
            try:
                files: dict[str, os.stat_result] = {}
                directories: list[str] = []
                links: list[str] = []
                file_pins: dict[str, Any] = {}
                try:
                    files, directories, links = _snapshot_inventory(target)
                    from omnicompany.packages.domains.publish.session_local_retention import _ReadPin

                    for relative, info in files.items():
                        pin = pin_stack.enter_context(_ReadPin(target / Path(relative)))
                        if pin.identity != {"device": int(info.st_dev), "file_id": int(info.st_ino)}:
                            _snapshot_refusal(result, f"源pin身份与目录枚举不一致：{relative}")
                            break
                        file_pins[relative] = pin
                    zip_pin = pin_stack.enter_context(_ReadPin(zip_path))
                    if zip_pin.identity != {"device": int(os.stat(zip_path).st_dev),
                                            "file_id": int(os.stat(zip_path).st_ino)}:
                        _snapshot_refusal(result, "ZIP读pin身份与当前文件不一致")
                    bundle_pin = pin_stack.enter_context(_ReadPin(bundle))
                    if bundle_pin.identity != {"device": int(os.stat(bundle).st_dev),
                                               "file_id": int(os.stat(bundle).st_ino)}:
                        _snapshot_refusal(result, "Git bundle读pin身份与当前文件不一致")
                except OSError as exc:
                    _snapshot_refusal(result, f"无跟随树遍历失败：{type(exc).__name__}: {exc}")
                result["files"] = len(files)
                result["dirs"] = max(0, len(directories) - 1)
                result["bytes"] = sum(int(info.st_size) for info in files.values())
                if links:
                    _snapshot_refusal(result, f"目标树含链接/非普通文件，例如 {links[0]}")
                file_native = {relative: _snapshot_identity(target / Path(relative)) for relative in files}
                directory_native = {relative: _snapshot_identity(target if relative == "." else target / Path(relative))
                                    for relative in directories}
                if any(meta is None or meta["nlink"] != 1 or meta["reparse"] or meta["deletePending"]
                       for meta in file_native.values()):
                    _snapshot_refusal(result, "目标树含硬链接或链接计数未知")
                if any(meta is None or meta["reparse"] or meta["deletePending"]
                       for meta in directory_native.values()):
                    _snapshot_refusal(result, "目标树目录含reparse或delete-pending")
                if proof.get("directories") != directory_native:
                    _snapshot_refusal(result, "目录full native metadata与proof不一致")
                members = proof.get("members")
                if not isinstance(members, list):
                    _snapshot_refusal(result, "proof members必须逐项覆盖整棵源树")
                else:
                    declared = {m.get("relative"): m for m in members if isinstance(m, dict)}
                    if (len(declared) != len(members) or set(declared) != set(files)
                            or any(not isinstance(k, str) or Path(k).is_absolute() or ".." in Path(k).parts
                                   or "\\" in k for k in declared)
                            or any(_key(str(m.get("source", ""))) != _key(str(target / Path(m["relative"])))
                                   for m in members if isinstance(m, dict) and m.get("relative") in files)):
                        _snapshot_refusal(result, "proof成员未与当前树逐文件一一覆盖")
                    try:
                        with zipfile.ZipFile(zip_path, "r") as zf:
                            listed_names = zf.namelist()
                            zip_names = set(listed_names)
                            wanted = {m.get("member") for m in members if isinstance(m, dict)}
                            if len(listed_names) != len(zip_names) or not wanted.issubset(zip_names):
                                _snapshot_refusal(result, "ZIP重复成员或未包含proof recovery map成员")
                            if len(wanted) != len(members) or "recovery-map.json" in wanted:
                                _snapshot_refusal(result, "recovery map含重复或保留的ZIP成员名")
                            embedded = json.loads(zf.read("recovery-map.json"))
                            if not isinstance(embedded, dict):
                                _snapshot_refusal(result, "ZIP内recovery-map.json必须是对象")
                            elif embedded.get("schema") == "omni.git-snapshot-recovery-map.v1":
                                if embedded.get("members") != members:
                                    _snapshot_refusal(result, "ZIP内recovery-map.json与typed proof映射不一致")
                            elif embedded.get("schema") == "hero-preflight-evidence-recovery-map-v1":
                                try:
                                    _legacy_map_members(embedded, source_root=parent, target=target,
                                                        proof_members=members, zip_path=zip_path,
                                                        zip_names=zip_names)
                                except (TypeError, ValueError, KeyError) as exc:
                                    _snapshot_refusal(result, f"legacy recovery-map与typed proof不一致：{exc}")
                            else:
                                _snapshot_refusal(result, "ZIP内recovery-map.json schema不受支持")
                            for relative, info in files.items():
                                member = declared.get(relative)
                                if not member:
                                    continue
                                if member.get("native") != file_native[relative]:
                                    _snapshot_refusal(result, f"源full metadata与proof不一致：{relative}")
                                    break
                                archive_member = member.get("member")
                                if (not isinstance(archive_member, str) or archive_member not in zip_names
                                        or Path(archive_member).is_absolute() or ".." in Path(archive_member).parts
                                        or "\\" in archive_member):
                                    _snapshot_refusal(result, f"ZIP缺少源成员：{relative}")
                                    break
                                source_path = target / Path(relative)
                                with source_path.open("rb") as source, zf.open(archive_member, "r") as saved:
                                    while True:
                                        left = source.read(1024 * 1024)
                                        right = saved.read(1024 * 1024)
                                        if left != right:
                                            _snapshot_refusal(result, f"源与ZIP raw内容不同：{relative}")
                                            break
                                        if not left:
                                            break
                                    if source.read(1) or saved.read(1):
                                        _snapshot_refusal(result, f"源/ZIP EOF不一致：{relative}")
                                        break
                            if {relative: _snapshot_identity(target / Path(relative)) for relative in files} != file_native:
                                _snapshot_refusal(result, "ZIP逐成员读回期间源full native metadata发生变化")
                            if {relative: _snapshot_identity(target if relative == "." else target / Path(relative))
                                    for relative in directories} != directory_native:
                                _snapshot_refusal(result, "ZIP逐成员读回期间目录full native metadata发生变化")
                            if {str(path): _snapshot_identity(path) for path in (zip_path, bundle)} != archive_file_native:
                                _snapshot_refusal(result, "ZIP/Git bundle读取期间full native metadata发生变化")
                    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError) as exc:
                        _snapshot_refusal(result, f"ZIP recovery proof读取失败：{type(exc).__name__}: {exc}")
                if any(_is_link(os.lstat(p)) for p in [*reversed(target.parents), target]
                       if os.path.lexists(p)):
                    _snapshot_refusal(result, "目标或祖先存在reparse/symlink")
                process_inspection = Inspection(target=str(target))
                _check_processes(target, process_inspection, process_rows)
                if process_inspection.problems:
                    result["checks"]["processes"] = process_inspection.checks.get("processes", [])
                    _snapshot_refusal(result, process_inspection.problems[0])
                if not result["problems"]:
                    result["outcome"] = "dry-run"
                    if not dry_run:
                        try:
                            _delete_pinned_snapshot_tree(target, files, directories, file_pins,
                                                         file_native, directory_native,
                                                         result["checks"].setdefault("readonlyMutations", []),
                                                         deleted_original_files)
                        except OSError as exc:
                            result["outcome"] = "incomplete"
                            result["problems"].append(f"pinned native cleanup stopped: {type(exc).__name__}: {exc}")
                        else:
                            result["outcome"] = "deleted" if not os.path.lexists(target) else "incomplete"
                            if result["outcome"] == "incomplete":
                                result["problems"].append("删除后目标仍存在")
            except Exception as exc:
                if deleted_original_files:
                    result["outcome"] = "incomplete"
                    result["problems"].append(
                        f"snapshot cleanup failed after deleting {len(deleted_original_files)} original files: "
                        f"{type(exc).__name__}: {exc}")
                else:
                    _snapshot_refusal(result, f"snapshot proof processing failed: {type(exc).__name__}: {exc}")
            finally:
                deleted_set = {row["relative"] for row in deleted_original_files}
                result["deletedOriginalFiles"] = [str(target / Path(row["relative"]))
                                                   for row in deleted_original_files]
                result["deletedOriginalBytes"] = sum(int(row["logicalBytes"])
                                                     for row in deleted_original_files)
                original_files = locals().get("files", {})
                result["remainingOriginalFiles"] = [str(target / Path(relative))
                                                     for relative in original_files if relative not in deleted_set]
                result["remainingOriginalBytes"] = sum(int(original_files[relative].st_size)
                                                       for relative in original_files if relative not in deleted_set)
                pin_stack.close()
    result["seconds"] = round(time.time() - started, 2)
    log = log_path or receipt_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    result["receipt_log"] = str(log)
    return result
