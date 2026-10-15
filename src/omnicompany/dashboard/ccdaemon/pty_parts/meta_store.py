# [OMNI] origin=arch-ratchet-surgery ts=2026-08-17 type=infra
# 本文件是 ccdaemon/pty.py 的 exec 装载分片：无 import、不可独立 import；
# 名字在宿主模块 globals 中解析（与拆分前单文件语义等价）。

_META_STORE_THREAD_LOCK = threading.RLock()


_META_STORE_CACHE_KEY: tuple[str, int, int, int] | None = None


_META_STORE_CACHE_VALUE: dict[str, dict[str, Any]] | None = None


_META_STORE_ARCHIVE_READ_CACHE_KEY: tuple[str, int, int, int] | None = None


_META_STORE_ARCHIVE_READ_CACHE_VALUE: dict[str, dict[str, Any]] | None = None


def _meta_store_path() -> Path:
    """Where we persist session metadata so they survive backend restart.

    Schema: { "<pty_id>": { id, cmd, cwd, started_at, ended_at,
                            claude_session_id, active_plan, exit_reason } }
    """
    # repo_root / data / cc_sessions.json — same dir as ide_events.db
    state_dir = os.environ.get("OMNI_CC_DAEMON_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "cc_sessions.json"
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root() / "data" / "cc_sessions.json"


def _meta_store_archive_path() -> Path:
    """Sibling file holding sessions compacted out of the hot store.

    Same schema as the hot store (`{sid: {...fields...}}`), just for entries
    old enough that the daemon's own hot paths no longer need to see them on
    every read/write — only cold lookups like `list_recoverable_sessions`
    still care, and those merge it back in via `_read_meta_store_with_archive`.
    """
    return _meta_store_path().with_name("cc_sessions.archive.json")


@contextmanager
def _open_text_shared_delete(path: Path) -> Iterator[Any]:
    """Open text without blocking an atomic replace on Windows.

    Python's normal Windows file open omits FILE_SHARE_DELETE. A concurrent
    ``os.replace`` from a live PTY host can therefore fail with WinError 5.
    State discovery is read-only and specifically designed to tolerate
    replacement, so opt into read/write/delete sharing for this narrow path.
    """
    if os.name != "nt":
        with path.open("r", encoding="utf-8") as stream:
            yield stream
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002 | 0x00000004,  # SHARE_READ | SHARE_WRITE | SHARE_DELETE
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ctypes.WinError()

    fd: int | None = None
    raw_handle_owned = True
    try:
        fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY)
        raw_handle_owned = False
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = None  # fdopen owns and closes the Windows handle.
            yield stream
    finally:
        if fd is not None:
            os.close(fd)
        elif raw_handle_owned:
            ctypes.windll.kernel32.CloseHandle(handle)


def _read_text_shared_delete(path: Path) -> str:
    with _open_text_shared_delete(path) as stream:
        return stream.read()


def _sanitize_nonfinite(obj: Any) -> Any:
    """递归把 NaN/Infinity 洗成 None。

    cc_sessions.json 曾被 NaN 毒化过: FastAPI/json 默认 allow_nan=True 会把 NaN
    原样写盘, 读回再写形成循环。读回侧统一过这道清洗, 脏数据进过一次也不会再循环。
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize_nonfinite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nonfinite(v) for v in obj]
    return obj


def _meta_store_disk_cache_path() -> Path:
    """Cross-process parse cache sitting next to cc_sessions.json.

    Hook subprocesses each pay a fresh json.loads() of the (9MB+) store on
    every invocation because the in-memory cache below is per-process. This
    sidecar lets a *different* process skip that parse when the store hasn't
    changed since the last process wrote the cache — keyed on the exact same
    (path, mtime_ns, size, ino) tuple already used for the in-memory cache, so
    it can never serve stale data, only save a redundant parse of identical
    bytes. Stored with marshal (not json): re-parsing JSON into the cache
    would spend exactly the CPU we're trying to avoid.
    """
    p = _meta_store_path()
    return p.with_name(p.stem + ".parsed-cache.bin")


def _read_meta_store_disk_cache(
    cache_key: tuple[str, int, int, int],
) -> dict[str, dict[str, Any]] | None:
    try:
        blob = _meta_store_disk_cache_path().read_bytes()
        cached_key, sanitized = marshal.loads(blob)
    except Exception:  # noqa: BLE001 - fail-open: any miss just re-parses JSON
        return None
    if tuple(cached_key) != cache_key or not isinstance(sanitized, dict):
        return None
    return sanitized


def _write_meta_store_disk_cache(
    cache_key: tuple[str, int, int, int], sanitized: dict[str, dict[str, Any]]
) -> None:
    path = _meta_store_disk_cache_path()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(marshal.dumps((list(cache_key), sanitized)))
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - best-effort; a stale/missing cache just means the next reader re-parses
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_meta_store(
    *,
    use_cache: bool = True,
    assume_locked: bool = False,
) -> dict[str, dict[str, Any]]:
    """Read the shared registry once per atomic file generation."""
    global _META_STORE_CACHE_KEY, _META_STORE_CACHE_VALUE

    if not assume_locked:
        with _meta_store_guard():
            return _read_meta_store(use_cache=use_cache, assume_locked=True)

    p = _meta_store_path()
    try:
        stat = os.stat(p)
    except OSError:
        return {}
    cache_key = (str(p), int(stat.st_mtime_ns), int(stat.st_size), int(stat.st_ino))

    if use_cache and _META_STORE_CACHE_KEY == cache_key and _META_STORE_CACHE_VALUE is not None:
        return _META_STORE_CACHE_VALUE

    if use_cache:
        disk_cached = _read_meta_store_disk_cache(cache_key)
        if disk_cached is not None:
            _META_STORE_CACHE_KEY = cache_key
            _META_STORE_CACHE_VALUE = disk_cached
            return disk_cached

    try:
        with _open_text_shared_delete(p) as stream:
            # os.stat() above and this fstat() are two separate syscalls; the
            # guard held by the assume_locked=False caller serializes every
            # reader/writer through the same cross-process lock, so nothing
            # can mutate the file between them in practice, but re-deriving
            # the key from the handle we actually read keeps that a property
            # of the lock rather than an assumption baked into this function.
            stat = os.fstat(stream.fileno())
            cache_key = (str(p), int(stat.st_mtime_ns), int(stat.st_size), int(stat.st_ino))
            if (
                use_cache
                and _META_STORE_CACHE_KEY == cache_key
                and _META_STORE_CACHE_VALUE is not None
            ):
                return _META_STORE_CACHE_VALUE
            store = json.loads(stream.read() or "{}") or {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
    # 历史脏文件兜底: NaN/Infinity → None (见 _sanitize_nonfinite)
    sanitized = _sanitize_nonfinite(store)
    if use_cache:
        _META_STORE_CACHE_KEY = cache_key
        _META_STORE_CACHE_VALUE = sanitized
        _write_meta_store_disk_cache(cache_key, sanitized)
    return sanitized


def _read_meta_store_archive_cached() -> dict[str, dict[str, Any]]:
    """Read `_meta_store_archive_path()`, cached on the file's own stat key."""
    global _META_STORE_ARCHIVE_READ_CACHE_KEY, _META_STORE_ARCHIVE_READ_CACHE_VALUE

    archive_path = _meta_store_archive_path()
    try:
        astat = os.stat(archive_path)
    except OSError:
        return {}
    key = (
        str(archive_path),
        int(astat.st_mtime_ns),
        int(astat.st_size),
        int(astat.st_ino),
    )
    if (
        key == _META_STORE_ARCHIVE_READ_CACHE_KEY
        and _META_STORE_ARCHIVE_READ_CACHE_VALUE is not None
    ):
        return _META_STORE_ARCHIVE_READ_CACHE_VALUE
    try:
        archived = json.loads(archive_path.read_text(encoding="utf-8") or "{}") or {}
    except (json.JSONDecodeError, OSError, ValueError):
        archived = {}
    _META_STORE_ARCHIVE_READ_CACHE_KEY = key
    _META_STORE_ARCHIVE_READ_CACHE_VALUE = archived
    return archived


def _read_meta_store_with_archive() -> dict[str, dict[str, Any]]:
    """Hot store merged with the P4.2b compaction archive.

    Only cold read paths that must still find sessions old enough to have
    been archived (recovery listing) call this — the hot paths (PTY
    create/close, chat persist) stay on `_read_meta_store()` and never pay
    for the archive at all. `hot` is always exactly whatever `_read_meta_
    store()` returns (never cached again here), so this stays correct even
    when a caller monkeypatches that function; only the archive half is
    cached, on its own file's stat key.
    """
    hot = _read_meta_store()
    archived = _read_meta_store_archive_cached()
    if not archived:
        return hot
    # Hot wins on key clash: a session id can only be in both if a previous
    # archive round raced a live reopen, and the hot store is always the
    # fresher truth for anything still tracked there.
    return {**archived, **hot}


def _record_meta_store_lock_wait(duration_ns: int) -> None:
    # Fail-open: this is passive observation, never allowed to affect the
    # meta store's own read-modify-write correctness.
    try:
        get_latency_monitor().observe_meta_store_lock_wait(duration_ns)
    except Exception:  # noqa: BLE001
        pass


def _record_meta_store_write(duration_ns: int) -> None:
    try:
        get_latency_monitor().observe_meta_store_write(duration_ns)
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def _meta_store_guard() -> Iterator[None]:
    """Serialize cc_sessions.json read-modify-write across hooks and daemon."""
    lock_path = _meta_store_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    wait_started_ns = time.perf_counter_ns()
    with _META_STORE_THREAD_LOCK, open(lock_path, "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _record_meta_store_lock_wait(time.perf_counter_ns() - wait_started_ns)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_meta_store_unlocked(store: dict[str, dict[str, Any]]) -> None:
    global _META_STORE_CACHE_KEY, _META_STORE_CACHE_VALUE

    write_started_ns = time.perf_counter_ns()
    try:
        p = _meta_store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            # 紧凑写 (不 indent): store 已涨到 9MB+, 每条聊天消息全量写一次, 体积小一半;
            # allow_nan=False 拦截 NaN/Infinity, 防再次毒化 store 文件 (曾实发)。
            text = json.dumps(store, ensure_ascii=False, allow_nan=False)
        except ValueError:
            # 有脏浮点混进来: 洗掉后兜底写, 并留 log 让毒化源可追查
            logger.warning("cc_sessions.json 含 NaN/Infinity, 清洗后重写", exc_info=True)
            text = json.dumps(_sanitize_nonfinite(store), ensure_ascii=False, allow_nan=False)
        last_error: OSError | None = None
        for attempt in range(8):
            tmp = p.with_name(f".{p.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            try:
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, p)
                stat = p.stat()
                _META_STORE_CACHE_KEY = (
                    str(p),
                    int(stat.st_mtime_ns),
                    int(stat.st_size),
                    int(stat.st_ino),
                )
                _META_STORE_CACHE_VALUE = store
                # Refresh the cross-process disk cache immediately so the next
                # reader (often a hook subprocess) doesn't even take the
                # disk-cache-miss path — it goes straight to a marshal.loads()
                # instead of json.loads() of the full 9MB+ text.
                _write_meta_store_disk_cache(_META_STORE_CACHE_KEY, store)
                return
            except OSError as exc:
                last_error = exc
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                if attempt < 7:
                    time.sleep(min(0.01 * (2 ** attempt), 0.25))
        logger.error(
            "cc_sessions.json write failed after retries; refusing to silently drop metadata: %s",
            last_error,
        )
        raise OSError(f"cc_sessions.json write failed after retries: {last_error}")
    finally:
        # Includes any retry backoff sleeps above — that's real caller-perceived
        # write latency, not just the happy-path serialize+replace cost.
        _record_meta_store_write(time.perf_counter_ns() - write_started_ns)


def _write_meta_store(store: dict[str, dict[str, Any]]) -> None:
    """Atomically merge one or more session entries into the shared registry.

    PERF-OBSERVATORY P4.2b: this is OmniChat's persist choke point
    (`chat.py::_persist_write`), called on every streamed token/tool event for
    every open chat session. Two cheap wins here matter a lot in aggregate:
    ``use_cache=True`` lets the read skip a full JSON parse when this process
    already holds the freshest generation (still `os.stat`-verified under the
    lock, so a concurrent external writer is still detected correctly); and
    the dirty check below skips the write+fsync entirely when the merge
    produced no actual change (e.g. a redundant persist call after a no-op
    field update) instead of rewriting the whole multi-MB store for nothing.
    """
    with _meta_store_guard():
        current = _read_meta_store(use_cache=True, assume_locked=True)
        changed = False
        for sid, fields in store.items():
            merged = {**(current.get(sid) or {}), **fields}
            if merged != current.get(sid):
                current[sid] = merged
                changed = True
        if not changed:
            return
        _write_meta_store_unlocked(current)


def _mutate_meta_store(
    mutator: Callable[[dict[str, dict[str, Any]]], bool | None],
) -> None:
    """Run `mutator` against the locked store and persist iff it reports a change.

    `mutator` may return ``False`` to signal "nothing actually changed, skip
    the write" (see `_upsert_meta_entry`). Any other return value (including
    the historical ``None``) preserves the old always-write behavior, so
    mutators that don't opt into the dirty check keep working unchanged.
    """
    with _meta_store_guard():
        store = _read_meta_store(use_cache=True, assume_locked=True)
        changed = mutator(store)
        if changed is False:
            return
        _write_meta_store_unlocked(store)
