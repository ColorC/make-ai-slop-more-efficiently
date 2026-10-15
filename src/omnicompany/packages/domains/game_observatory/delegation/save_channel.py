# [OMNI] origin=codex created_by=codex intent="provide the production Romantic Restaurant authoritative save channel"
"""Authoritative save-backed state channel for Romantic Restaurant."""

from __future__ import annotations

import hashlib
import gc
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..adapters import resolve_adb
from ..replay import RunHandle
from .save_history import SaveHistory
from .save_reader import (
    CoverageV1,
    DBSchemaV1,
    GameStateV1,
    Runner,
    FlushPlan,
    FlushReceiptV1,
    SaveFileV1,
    SavePullPlan,
    SaveSnapshotV1,
    decode_snapshot,
    load_db_schema_from_decompile,
    pull_save_snapshot,
    trigger_flush,
)
from .sqlcipher_offline import (
    DEFAULT_PARAMETERS,
    SQLCipherParametersV1,
    SaveDecryptError,
    SaveKeyMissingError,
    decrypt_sqlcipher_file,
    derived_key_cache_stats,
    load_save_password,
)


DEFAULT_LUA_DIR = Path(
    "E:/P4/spark-user/Doc/参考游戏资料/"
    "synthesis/lua_code/Lua-Project"
)
FlushMode = Literal["home_and_return", "none"]
FlushProfile = Literal["verified", "fast"]
# The "fast" profile keeps the same per-attempt recovery budget but polls the
# foreground every 200 ms and drops the pre-HOME focus probe.  Both cost one
# ``dumpsys window windows -a`` round trip each on this device.
FAST_FLUSH_RECOVERY_POLL_S = 0.2
# The pause has to outlast the game's own serialisation or the pull returns a
# save older than the pause that triggered it.  The F1-04 live probe measured
# the default 0.25 s failing 8 reads in 20 on the fast profile while the
# verified profile, whose flush runs about 1.6 s end to end, failed none.
DEFAULT_POST_HOME_DWELL_S = 0.25
FAST_FLUSH_POST_HOME_DWELL_S = 0.25
# What the fixed 1.0 s wait f0a23d19e introduced was really buying.  The F1-04
# live probe swept the post-HOME dwell to 1.25 s and the fast profile still
# returned data serialised before its own pause on 5 reads in 12, while the
# verified profile was fresh on 20 of 20.  The two differ in when am start
# lands relative to the pull, not in total flush time: the game finishes
# writing around the resume, so this is the floor between am start and the
# pull, and 1.0 s is the value the verified profile has always used.
FAST_FLUSH_MIN_RESUME_DWELL_S = 1.0


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SaveReadTimingV1(_FrozenModel):
    flush_s: float = Field(ge=0)
    pull_ms: float = Field(ge=0)
    decrypt_ms: float = Field(ge=0)
    decode_ms: float = Field(ge=0)
    cleanup_ms: float = Field(ge=0)
    total_s: float = Field(ge=0)
    pull_adb_calls: int = Field(default=0, ge=0)
    pull_fast_path: bool = False
    derived_key_cached: bool = False
    flush_adb_calls: int = Field(default=0, ge=0)
    flush_focus_command: str = ""


class SaveReadResultV1(_FrozenModel):
    state: GameStateV1
    coverage: dict[str, CoverageV1]
    timing: SaveReadTimingV1
    snapshot_sha256: str
    wal_applied: bool
    errors: list[str]


class SaveChannelError(RuntimeError):
    """The authoritative save channel could not return a decoded state."""


def _elapsed_ms(started_ns: int) -> float:
    return max((time.perf_counter_ns() - started_ns) / 1_000_000, 0.000001)


def _optional_round(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_wal_mode(plaintext_database: Path) -> bool:
    """Read the SQLite ``file_format_write_version`` byte of a decrypted save."""

    try:
        with plaintext_database.open("rb") as stream:
            header = stream.read(20)
    except OSError:
        return False
    return len(header) >= 19 and (header[18] == 2 or header[19] == 2)


def _snapshot_file(snapshot: SaveSnapshotV1, filename: str) -> SaveFileV1 | None:
    return next(
        (
            item
            for item in snapshot.files
            if Path(item.remote_path).name == filename
        ),
        None,
    )


def _plaintext_snapshot(
    encrypted_snapshot: SaveSnapshotV1,
    encrypted_database: SaveFileV1,
    plaintext_database: Path,
) -> SaveSnapshotV1:
    plaintext = plaintext_database.read_bytes()
    files = [
        SaveFileV1(
            remote_path=encrypted_database.remote_path,
            local_path=str(plaintext_database),
            size_bytes=len(plaintext),
            sha256=hashlib.sha256(plaintext).hexdigest(),
            pull_method="offline-sqlcipher",
        )
    ]
    files.extend(
        item
        for item in encrypted_snapshot.files
        if item.remote_path.endswith(".playerprefs.xml")
    )
    return SaveSnapshotV1(
        captured_at=encrypted_snapshot.captured_at,
        destination=encrypted_snapshot.destination,
        serial=encrypted_snapshot.serial,
        package=encrypted_snapshot.package,
        files=files,
        errors=encrypted_snapshot.errors,
        elapsed_ms=encrypted_snapshot.elapsed_ms,
    )


class SaveChannel:
    """Read a complete GameStateV1 through flush, pull, decrypt, and decode."""

    def __init__(
        self,
        adb_path: Path | None,
        serial: str,
        package: str,
        *,
        key_file: Path | None = None,
        work_dir: Path,
        schema: DBSchemaV1 | None = None,
        run: RunHandle | None = None,
        parameters: SQLCipherParametersV1 = DEFAULT_PARAMETERS,
        runner: Runner | None = None,
        sleep: Callable[[float], None] = time.sleep,
        cleanup_plaintext: bool = True,
        verify_hmac: bool = False,
        reuse_pull_plan: bool = True,
        flush_profile: FlushProfile = "verified",
        post_home_dwell_s: float | None = None,
        min_resume_dwell_s: float | None = None,
        save_history: SaveHistory | None = None,
    ) -> None:
        self.adb_path = Path(adb_path) if adb_path is not None else resolve_adb()
        self.serial = serial
        self.package = package
        self.key_file = key_file
        self.work_dir = Path(work_dir)
        self.schema = schema or load_db_schema_from_decompile(DEFAULT_LUA_DIR)
        self.run = run
        self.parameters = parameters
        self.runner = runner
        self.sleep = sleep
        self.cleanup_plaintext = cleanup_plaintext
        self.verify_hmac = verify_hmac
        self.flush_profile = flush_profile
        self.pull_plan = SavePullPlan() if reuse_pull_plan else None
        # Only the fast profile learns a cheaper foreground probe; the verified
        # profile keeps the exact command sequence it has always used.
        self.flush_plan = FlushPlan() if flush_profile == "fast" else None
        # Kept so a probe can line the HOME keyevent up against the pulled
        # database mtime and tell whether the pause really persisted in time.
        self.last_flush_receipt: FlushReceiptV1 | None = None
        self.post_home_dwell_s = (
            post_home_dwell_s
            if post_home_dwell_s is not None
            else (
                FAST_FLUSH_POST_HOME_DWELL_S
                if flush_profile == "fast"
                else DEFAULT_POST_HOME_DWELL_S
            )
        )
        self.min_resume_dwell_s = (
            min_resume_dwell_s
            if min_resume_dwell_s is not None
            else FAST_FLUSH_MIN_RESUME_DWELL_S
        )
        # E6 save versioning, default off: when a SaveHistory is attached,
        # every decrypted read is exported and committed on change.  The
        # recorder never raises into this read path.  ``save_history_context``
        # is a mutable slot the owning loop refreshes per round so the commit
        # message can carry the round number; the channel itself only knows
        # the run.
        self.save_history = save_history
        self.save_history_context: dict[str, Any] = {}

    def _trigger_flush(self, flush: FlushMode) -> None:
        kwargs: dict[str, Any] = {"sleep": self.sleep}
        kwargs["post_home_dwell_s"] = self.post_home_dwell_s
        if self.runner is not None:
            kwargs["runner"] = self.runner
        if self.flush_profile == "fast":
            kwargs["recovery_poll_s"] = FAST_FLUSH_RECOVERY_POLL_S
            kwargs["probe_before"] = False
            kwargs["plan"] = self.flush_plan
            kwargs["min_resume_dwell_s"] = self.min_resume_dwell_s
        self.last_flush_receipt = trigger_flush(
            self.adb_path,
            self.serial,
            self.package,
            flush,
            **kwargs,
        )

    def _pull(self, destination: Path) -> SaveSnapshotV1:
        kwargs: dict[str, Any] = {}
        if self.runner is not None:
            kwargs["runner"] = self.runner
        if self.pull_plan is not None:
            kwargs["plan"] = self.pull_plan
        return pull_save_snapshot(
            self.adb_path,
            self.serial,
            self.package,
            destination,
            **kwargs,
        )

    def _cleanup(self, read_dir: Path) -> float:
        started = time.perf_counter_ns()
        if self.cleanup_plaintext:
            last_error: OSError | None = None
            for attempt in range(5):
                try:
                    shutil.rmtree(read_dir)
                    last_error = None
                    break
                except OSError as exc:
                    last_error = exc
                    gc.collect()
                    if attempt < 4:
                        time.sleep(0.025 * (attempt + 1))
            if last_error is not None:
                raise SaveChannelError("failed to remove save-channel plaintext") from last_error
        return _elapsed_ms(started)

    def read_state(
        self,
        *,
        flush: FlushMode = "home_and_return",
        include_board: bool = True,
    ) -> SaveReadResultV1:
        total_started = time.perf_counter_ns()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        read_dir = self.work_dir / f"read-{uuid.uuid4().hex}"
        read_dir.mkdir(exist_ok=False)
        cleanup_ms = 0.0
        completed = False
        replay_payload: dict[str, Any] | None = None

        try:
            flush_started = time.perf_counter_ns()
            self._trigger_flush(flush)
            flush_s = max((time.perf_counter_ns() - flush_started) / 1_000_000_000, 1e-9)

            pull_started = time.perf_counter_ns()
            snapshot = self._pull(read_dir / "encrypted")
            pull_ms = _elapsed_ms(pull_started)
            encrypted_database = _snapshot_file(snapshot, "db.sqlite3")
            if encrypted_database is None:
                raise SaveChannelError("pulled snapshot did not contain db.sqlite3")
            wal_file = _snapshot_file(snapshot, "db.sqlite3-wal")

            decrypt_started = time.perf_counter_ns()
            password = load_save_password(self.key_file)
            derivations_before = derived_key_cache_stats()["misses"]
            plaintext_path = read_dir / "plaintext.sqlite3"
            decrypt_result = decrypt_sqlcipher_file(
                Path(encrypted_database.local_path),
                plaintext_path,
                password=password,
                wal_path=Path(wal_file.local_path) if wal_file is not None else None,
                parameters=self.parameters,
                verify_hmac=self.verify_hmac,
            )
            decrypt_ms = _elapsed_ms(decrypt_started)
            derived_key_cached = (
                derived_key_cache_stats()["misses"] == derivations_before
            )
            # A warm pull skips the -wal/-shm/-journal probes because this save
            # is a rollback-journal container.  If the game ever switches the
            # database to WAL the plaintext header says so in byte 18, and the
            # memo is dropped so the next pull looks for the siblings again.
            if self.pull_plan is not None and _is_wal_mode(plaintext_path):
                self.pull_plan.forget()

            if self.save_history is not None:
                run_id = getattr(self.run, "run_id", None)
                self.save_history.record(
                    plaintext_path,
                    run_id=str(run_id) if run_id else None,
                    round_number=_optional_round(self.save_history_context.get("round")),
                    trigger="save_channel.read_state",
                )

            decode_started = time.perf_counter_ns()
            decoded_snapshot = _plaintext_snapshot(
                snapshot,
                encrypted_database,
                plaintext_path,
            )
            state = decode_snapshot(decoded_snapshot, self.schema).model_copy(
                update={"snapshot_sha256": encrypted_database.sha256}
            )
            if not include_board:
                coverage = dict(state.coverage)
                coverage["board"] = CoverageV1(
                    decoded=False,
                    reason="board decoding was not requested",
                    source_refs=coverage.get("board", CoverageV1(decoded=False)).source_refs,
                )
                state = state.model_copy(update={"board": None, "coverage": coverage})
            decode_ms = max(state.decode_ms, _elapsed_ms(decode_started))

            # F4-09 1.7: the row is written once the timing exists, so the
            # replay carries how the read was served and not only what it read.
            # Until now pull_fast_path, pull_adb_calls, derived_key_cached and
            # the flush probe never left this function, and whether the F1-04
            # fast paths ran during a live segment could not be shown
            # afterwards.
            replay_payload = state.model_dump(mode="json")
            errors = list(snapshot.errors)
            snapshot_sha256 = encrypted_database.sha256
            wal_applied = decrypt_result.wal_applied
            completed = True
        except (SaveDecryptError, SaveKeyMissingError):
            raise
        except subprocess.SubprocessError as exc:
            raise SaveChannelError("save-channel subprocess failed") from exc
        finally:
            if self.cleanup_plaintext:
                cleanup_ms = self._cleanup(read_dir)

        if not completed:
            raise SaveChannelError("save-channel read did not complete")
        total_s = max((time.perf_counter_ns() - total_started) / 1_000_000_000, 1e-9)
        timing = SaveReadTimingV1(
            flush_s=flush_s,
            pull_ms=pull_ms,
            decrypt_ms=decrypt_ms,
            decode_ms=decode_ms,
            cleanup_ms=cleanup_ms,
            total_s=total_s,
            pull_adb_calls=self.pull_plan.last_adb_calls if self.pull_plan else 0,
            pull_fast_path=bool(self.pull_plan and self.pull_plan.last_used_fast_path),
            derived_key_cached=derived_key_cached,
            flush_adb_calls=self.flush_plan.last_adb_calls if self.flush_plan else 0,
            flush_focus_command=(
                " ".join(self.flush_plan.focus_command)
                if self.flush_plan and self.flush_plan.focus_command
                else ""
            ),
        )
        if self.run is not None and replay_payload is not None:
            self.run.add_state(
                "save_diff",
                {**replay_payload, "save_read_timing": timing.model_dump(mode="json")},
                latency_ms=total_s * 1000,
            )
        return SaveReadResultV1(
            state=state,
            coverage=state.coverage,
            timing=timing,
            snapshot_sha256=snapshot_sha256,
            wal_applied=wal_applied,
            errors=errors,
        )


__all__ = [
    "DEFAULT_LUA_DIR",
    "FAST_FLUSH_RECOVERY_POLL_S",
    "FlushProfile",
    "SaveChannel",
    "SaveChannelError",
    "SaveKeyMissingError",
    "SaveReadResultV1",
    "SaveReadTimingV1",
]
