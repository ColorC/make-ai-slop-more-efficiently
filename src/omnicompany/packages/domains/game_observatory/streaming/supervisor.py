"""Long lived automatic-play supervisor for the shared play session."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[6]
RR_PLAY = REPO_ROOT / "scripts/game_observatory/rr_play.py"
DEFAULT_RR_SAVE_KEY_FILE = (
    REPO_ROOT
    / "data/domains/game_observatory/game_models/romantic-restaurant/feasibility"
    / "F1-03-20260905T182808Z/key.json"
)
# These are the continuing outcomes emitted by GameLoop.  A main-task outage
# or refuted streak is deliberately absent: retrying either at high speed
# would repeatedly revisit the same reported stop point.
CONTINUING_STOP_REASONS = frozenset({
    "max_rounds", "main_task_completed", "main_task_stopped", "max_wait_s",
    "resource_wait", "exhausted_v0",
})
AGENT_TAKEOVER_STOP_REASONS = frozenset({
    "unknown_screen",
    "not_on_board",
    "unexplored_surface",
    "refuted_streak",
    "tutorial_follow_stalled",
    "max_guard_steps",
    "shop_entry_stalled",
    "watchdog_timeout",
    "no_progress",
})
SEMANTIC_NO_PROGRESS_REASONS = frozenset({"max_rounds", "main_task_stopped", "max_wait_s"})
# Predictions that are about time passing, not about the account changing.
NON_PROGRESS_PREDICTION_KINDS = frozenset({"wait", "generator_inventory_wait"})
# Segments that end without the account moving and without waiting for a
# resource.  Three in a row is idling, whatever the stop reason says, and
# idling is handed to the same ladder as a dead screen: one agent look, then
# the bounded restarts, then a person.  The 2026-09-20 stall ran 52 of these
# and nothing counted them.
IDLE_SEGMENT_LIMIT = 3
# Stops that are a legitimate pause rather than idling: the run is waiting for
# energy to regenerate, or a person took the controls.
NON_IDLE_STOP_REASONS = frozenset({
    "waiting_for_energy", "resource_wait", "exhausted_v0", "control_handed_over",
    "target_level_reached",
})
# The supervisor keeps its own state beside the children's segment reports.
# These are not reports: a fresh one must not be read as a segment result, which
# would clear the reportless-retry budget that exists to catch a dead feed.
SUPERVISOR_STATE_FILES = frozenset({"attention-state.json", "daily-relaunch.json"})
# A daily-restart child force-stops the game and then waits for the boot to
# reach the board -- rr_play allows that wait 180 s, and nothing it does in the
# meantime touches the input ledger the watchdog reads.  The ordinary 90 s idle
# watchdog killed the first one 99 s in (2026-09-19 11:57Z) and scheduled an
# agent takeover over a game that was merely still loading.
RELAUNCH_WATCHDOG_TIMEOUT_S = 300.0
SEMANTIC_NO_PROGRESS_LIMIT = 3
# A child that writes no report at all did not judge the game: it died before
# it could, which in practice means the recording feed blinked (a stale frame,
# a dropped MuMu IPC handle).  Retry that a few times before asking a person,
# and reset the count as soon as any child reports again.
REPORTLESS_CHILD_RETRY_LIMIT = 3
# An attention episode used to end automatic play until a person came back.
# 2026-09-20: a US$0.99 offer panel whose registered signature did not fire held
# the run for seven hours with a full energy bar.  The agent handler identified
# it, refused to touch a purchase surface (which is the rule) and stopped, and
# nothing else was left to try.  Tapping an unknown surface blind stays
# forbidden -- but restarting the game is not a tap: a force stop drops every
# popup and boots back to the board, and the daily restart already proves that
# path.  So an episode the agent could not clear earns a bounded number of
# restarts before automatic play really does wait for a person.
ATTENTION_RELAUNCH_AFTER_S = 600.0
ATTENTION_RELAUNCH_LIMIT = 3
_RR_DRIVE_MODULE: Any | None = None


def _rr_drive_driver() -> Any:
    """Load rr_drive by repository path, independent of the process cwd."""

    global _RR_DRIVE_MODULE
    if _RR_DRIVE_MODULE is None:
        source = REPO_ROOT / "scripts/game_observatory/rr_drive.py"
        spec = importlib.util.spec_from_file_location("omni_rr_drive_runtime", source)
        if spec is None or spec.loader is None:
            raise RuntimeError("rr_drive is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _RR_DRIVE_MODULE = module
    return _RR_DRIVE_MODULE.Driver


class AutoSupervisor:
    """Keep one rr_play child alive while the server control gate is ``auto``."""

    def __init__(
        self,
        base: Path,
        stream_url: str,
        control_path: Path,
        target_level: int = 40,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        now: Callable[[], float] = time.time,
        wait: Callable[[float], None] | None = None,
        watchdog_timeout_s: float = 90.0,
        agent_watchdog_timeout_s: float = 600.0,
        progress_probe: Callable[[], Any] | None = None,
        utc_day: Callable[[], str] | None = None,
    ) -> None:
        if target_level < 1:
            raise ValueError("target_level must be positive")
        if watchdog_timeout_s <= 0:
            raise ValueError("watchdog_timeout_s must be positive")
        if agent_watchdog_timeout_s <= 0:
            raise ValueError("agent_watchdog_timeout_s must be positive")
        self.base = Path(base).resolve()
        self.auto_root = self.base / "auto"
        self.stream_url = stream_url
        self.control_path = Path(control_path).resolve()
        self.target_level = target_level
        self._popen = popen
        self._now = now
        self._wait = wait or time.sleep
        self.watchdog_timeout_s = float(watchdog_timeout_s)
        self.agent_watchdog_timeout_s = float(agent_watchdog_timeout_s)
        self._progress_probe = progress_probe
        self._utc_day = utc_day or (
            lambda: datetime.now(timezone.utc).date().isoformat()
        )
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._child: Any = None
        self._child_files: tuple[Any, Any] | None = None
        self._last_report: dict[str, Any] | None = None
        self._seen_reports: set[Path] = set()
        self._last_level: int | None = None
        self._last_energy: int | None = None
        self._wait_until: float | None = None
        self._stop_reason: str | None = None
        self._finished = False
        self._needs_attention = False
        self._generation = 0
        self._child_started_at: float | None = None
        self._child_is_agent = False
        self._child_is_relaunch = False
        self._child_last_activity_at: float | None = None
        self._child_activity_token: str | None = None
        self._pending_agent_context: Path | None = None
        self._pending_agent_key: str | None = None
        self._daily_relaunch_path = self.auto_root / "daily-relaunch.json"
        self._attention_state_path = self.auto_root / "attention-state.json"
        self._attention_state: dict[str, Any] = self._load_attention_state()
        pending_no_progress = self._attention_state.get("no_progress")
        self._semantic_no_progress_key: str | None = (
            str(pending_no_progress.get("fingerprint"))
            if isinstance(pending_no_progress, dict) and pending_no_progress.get("fingerprint")
            else None
        )
        self._semantic_no_progress_count = (
            int(pending_no_progress.get("count", 0))
            if isinstance(pending_no_progress, dict)
            and isinstance(pending_no_progress.get("count", 0), int)
            else 0
        )
        # An unresolved episode survives a supervisor process restart.  It is
        # intentionally held until a later child report proves real business
        # progress; merely seeing ``auto`` again must not re-arm the model.
        self._needs_attention = bool(self._attention_state.get("active_key"))
        # How long this episode has been waiting, and how many restarts it has
        # already been given.  The count rides in the persisted episode so a
        # service restart cannot hand the same dead screen a fresh budget.
        self._attention_since: float | None = None
        self._attention_escapes = (
            int(self._attention_state.get("relaunch_escapes", 0))
            if isinstance(self._attention_state.get("relaunch_escapes", 0), int)
            else 0
        )
        self._forced_relaunch = False
        # How many segments in a row finished without moving the account, and
        # when one last did.  Both are reported so idling is legible from
        # outside instead of hiding behind "running".
        self._idle_segments = 0
        self._last_progress_at: float | None = None
        self._reportless_children = 0
        self._last_attention_report: str | None = (
            str(self._attention_state.get("report_path"))
            if self._attention_state.get("report_path")
            else None
        )

    def _log(self, event: str, **payload: Any) -> None:
        self.auto_root.mkdir(parents=True, exist_ok=True)
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **payload}
        with (self.auto_root / "supervisor.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _mode(self) -> str:
        try:
            value = json.loads(self.control_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return "paused"
        mode = value.get("mode") if isinstance(value, dict) else None
        return mode if mode in {"auto", "manual", "paused"} else "paused"

    def _load_attention_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self._attention_state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save_attention_state(self) -> None:
        self.auto_root.mkdir(parents=True, exist_ok=True)
        temporary = self._attention_state_path.with_name(
            self._attention_state_path.name + f".{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(self._attention_state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self._attention_state_path)

    @staticmethod
    def _report_has_business_progress(report: dict[str, Any]) -> bool:
        """Did this segment move the account, as opposed to merely finishing?

        2026-09-20/21: 52 segments -- 9.4 hours -- ended in max_wait_s having
        done nothing but wait on a full board, because every deliverable order
        was blocked.  Each one still answered yes here: the old test
        short-circuited on ``loop.confirmed_predictions > 0`` and that counter
        includes confirmed waits.  So each idle segment reset the attention
        budget, every dashboard read "running", and nothing anywhere noticed.
        The per-round verdicts below already excluded waits; now they decide.
        """

        loop = report.get("loop") if isinstance(report.get("loop"), dict) else {}
        rounds = loop.get("rounds") if isinstance(loop.get("rounds"), list) else []
        adjudicated = False
        for item in rounds:
            if not isinstance(item, dict):
                continue
            for verdict in item.get("adjudication") or []:
                if not isinstance(verdict, dict):
                    continue
                adjudicated = True
                if verdict.get("status") in {"confirmed", "confirmed_with_deviation"}:
                    if verdict.get("kind") not in NON_PROGRESS_PREDICTION_KINDS:
                        return True
        if adjudicated:
            return False
        # A report that carries no per-round verdicts at all is older or
        # truncated; its summary counter is the only evidence there is.
        confirmed = loop.get("confirmed_predictions")
        return isinstance(confirmed, int) and confirmed > 0

    @staticmethod
    def _attention_key(report: dict[str, Any], reason: str) -> str:
        loop = report.get("loop") if isinstance(report.get("loop"), dict) else {}
        surface = loop.get("unexplored_surface") or report.get("surface") or ""
        return f"{reason}:{surface}"

    def _record_business_progress_locked(self, report: dict[str, Any]) -> None:
        if not self._report_has_business_progress(report):
            return
        self._semantic_no_progress_key = None
        self._semantic_no_progress_count = 0
        # Real progress ends the episode, so the restart budget starts over.
        self._attention_since = None
        self._attention_escapes = 0
        if self._attention_state:
            self._attention_state = {}
            self._save_attention_state()
            self._last_attention_report = None
            self._log("attention_recovered", report_path=report.get("_supervisor_report_path"))

    @staticmethod
    def _semantic_fingerprint(report: dict[str, Any]) -> str | None:
        """Return the stable business state, excluding animation/timing fields."""

        state = AutoSupervisor._state(report)
        if not state:
            return None
        board = state.get("board_matrix_7x9", state.get("board"))
        currencies = state.get("currencies")
        if not isinstance(currencies, dict):
            currencies = {
                key: state[key]
                for key in ("coins", "gems", "energy", "xp", "level")
                if key in state
            }
        orders = state.get("orders", state.get("order"))
        task = state.get("main_task", state.get("task"))
        semantic = {
            "board": board,
            "currencies": currencies,
            "orders": orders,
            "task": task,
        }
        if all(value is None or value == {} for value in semantic.values()):
            return None
        return json.dumps(semantic, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)

    def _clear_semantic_pending_locked(self) -> None:
        self._semantic_no_progress_key = None
        self._semantic_no_progress_count = 0
        if "no_progress" in self._attention_state:
            self._attention_state.pop("no_progress", None)
            self._save_attention_state()

    def _observe_semantic_no_progress_locked(self, report: dict[str, Any], reason: str) -> bool:
        """Count identical bounded stops and schedule one takeover at the limit."""

        if reason not in SEMANTIC_NO_PROGRESS_REASONS:
            self._clear_semantic_pending_locked()
            return False
        if self._report_has_business_progress(report):
            self._clear_semantic_pending_locked()
            return False
        fingerprint = self._semantic_fingerprint(report)
        if fingerprint is None:
            self._clear_semantic_pending_locked()
            return False
        if fingerprint == self._semantic_no_progress_key:
            self._semantic_no_progress_count += 1
        else:
            self._semantic_no_progress_key = fingerprint
            self._semantic_no_progress_count = 1
        self._attention_state["no_progress"] = {
            "fingerprint": self._semantic_no_progress_key,
            "count": self._semantic_no_progress_count,
            "last_report_path": report.get("_supervisor_report_path"),
        }
        self._save_attention_state()
        if self._semantic_no_progress_count < SEMANTIC_NO_PROGRESS_LIMIT:
            return False
        self._schedule_agent_locked(report, "no_progress")
        return True

    def _schedule_agent_locked(self, report: dict[str, Any], reason: str) -> None:
        key = self._attention_key(report, reason)
        previous = self._attention_state.get("active_key")
        if previous == key or self._attention_state.get("active_key"):
            self._needs_attention = True
            self._stop_reason = "needs_attention"
            self._last_attention_report = str(report.get("_supervisor_report_path") or "") or None
            self._log(
                "attention_held",
                reason=reason,
                attention_key=key,
                previous_key=previous,
                report_path=report.get("_supervisor_report_path"),
            )
            return
        report_path = report.get("_supervisor_report_path")
        if not isinstance(report_path, str) or not report_path:
            self._needs_attention = True
            self._stop_reason = "needs_attention"
            self._log("attention_missing_context", reason=reason)
            return
        self._attention_state = {
            "active_key": key,
            "reason": reason,
            "report_path": report_path,
            "agent_attempted": True,
            # The next generation is the one that will carry this episode.
            # Persist it before spawning so a supervisor restart cannot lose
            # the handover boundary.
            "agent_generation": self._generation + 1,
            # Carried across episodes: a restart that only produced the same
            # dead screen again must not refill its own budget.
            "relaunch_escapes": self._attention_escapes,
        }
        self._save_attention_state()
        self._pending_agent_context = Path(report_path)
        self._pending_agent_key = key
        self._stop_reason = "agent_takeover_pending"
        self._last_attention_report = report_path
        self._log(
            "agent_takeover_scheduled",
            reason=reason,
            attention_key=key,
            report_path=report_path,
        )

    def _recording_inputs_path(self) -> Path | None:
        current = self.base / "current.json"
        try:
            value = json.loads(current.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        root = value.get("root") if isinstance(value, dict) else None
        return Path(root) / "inputs.ndjson" if isinstance(root, str) and root else None

    @staticmethod
    def _latest_ndjson_record(path: Path | None) -> dict[str, Any] | None:
        if path is None:
            return None
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 32768), os.SEEK_SET)
                lines = handle.read().splitlines()
        except OSError:
            return None
        for raw in reversed(lines):
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                return value
        return None

    @staticmethod
    def _frame_path(value: Any, key_hint: str = "") -> str | None:
        if isinstance(value, dict):
            for key, item in value.items():
                if "frame" in key.lower() or "screenshot" in key.lower():
                    found = AutoSupervisor._frame_path(item, key)
                    if found:
                        return found
            if "frame" in key_hint.lower() or "screenshot" in key_hint.lower():
                for key in ("path", "relative_path", "file", "uri"):
                    item = value.get(key)
                    if isinstance(item, str) and item:
                        return item
            return None
        if isinstance(value, str) and value and ("frame" in key_hint.lower() or "screenshot" in key_hint.lower()):
            return value
        return None

    def _watchdog_evidence(self) -> tuple[dict[str, Any] | None, str | None]:
        inputs_path = self._recording_inputs_path()
        latest_input = self._latest_ndjson_record(inputs_path)
        screenshot = self._frame_path(latest_input)
        if screenshot is None and inputs_path is not None:
            screenshot = self._frame_path(
                self._latest_ndjson_record(inputs_path.with_name("video-frames.ndjson"))
            )
        return latest_input, screenshot

    def _activity_token(self) -> str:
        parts: list[str] = []
        inputs = self._recording_inputs_path()
        if inputs is not None:
            try:
                stat = inputs.stat()
                parts.append(f"inputs:{stat.st_size}:{stat.st_mtime_ns}")
            except OSError:
                parts.append("inputs:missing")
        if self._progress_probe is not None:
            try:
                value = self._progress_probe()
                parts.append("probe:" + json.dumps(value, sort_keys=True, default=str))
            except Exception as exc:  # diagnostic probe must never stop the child
                parts.append(f"probe-error:{type(exc).__name__}")
        for handle in self._child_files or ():
            try:
                parts.append(f"child:{os.fstat(handle.fileno()).st_size}")
            except (OSError, ValueError):
                parts.append("child:closed")
        return "|".join(parts)

    def _watchdog_locked(self) -> None:
        if self._child is None or self._wait_until is not None:
            return
        started = self._child_started_at
        if started is None:
            return
        token = self._activity_token()
        now = self._now()
        if token != self._child_activity_token:
            self._child_activity_token = token
            self._child_last_activity_at = now
            return
        last = self._child_last_activity_at if self._child_last_activity_at is not None else started
        timeout_s = (
            self.agent_watchdog_timeout_s
            if self._child_is_agent
            else max(self.watchdog_timeout_s, RELAUNCH_WATCHDOG_TIMEOUT_S)
            if self._child_is_relaunch
            else self.watchdog_timeout_s
        )
        if now - last <= timeout_s:
            return
        self._terminate_watchdog_child_locked(now - started, token, timeout_s=timeout_s)

    def _terminate_watchdog_child_locked(
        self,
        elapsed_s: float,
        token: str,
        *,
        timeout_s: float | None = None,
    ) -> None:
        child = self._child
        pid = getattr(child, "pid", None)
        timeout_s = self.watchdog_timeout_s if timeout_s is None else timeout_s
        latest_input, latest_screenshot = self._watchdog_evidence()
        last_loop = self._last_report.get("loop") if isinstance(self._last_report, dict) else {}
        if not isinstance(last_loop, dict):
            last_loop = {}
        stop_context = {
            "last_report_path": (
                self._last_report.get("_supervisor_report_path")
                if isinstance(self._last_report, dict)
                else None
            ),
            "last_stop_reason": last_loop.get("stop_reason") if last_loop else None,
            "last_level": self._last_level,
            "last_energy": self._last_energy,
            "supervisor_stop_reason": self._stop_reason,
        }
        if child is not None:
            try:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=2)
            except Exception:
                try:
                    child.kill()
                except Exception:
                    pass
        self._child = None
        self._close_child_files_locked()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = self.auto_root / f"watchdog-attention-{self._generation:04d}-{stamp}.json"
        report = {
            "schema": "game-observatory.supervisor-attention.v1",
            "stop_reason": "watchdog_timeout",
            "loop": {"stop_reason": "watchdog_timeout", "rounds": []},
            "evidence": {
                "kind": "child_no_progress",
                "pid": pid,
                "generation": self._generation,
                "child_kind": (
                    "agent"
                    if self._child_is_agent
                    else "daily-relaunch"
                    if self._child_is_relaunch
                    else "normal"
                ),
                "elapsed_s": round(float(elapsed_s), 3),
                "watchdog_timeout_s": timeout_s,
                "activity_token": token,
                "inputs_path": str(self._recording_inputs_path() or ""),
                "latest_input": latest_input,
                "latest_screenshot_path": latest_screenshot,
                "stop_context": stop_context,
            },
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["_supervisor_report_path"] = str(report_path)
        self._seen_reports.add(report_path)
        self._log("watchdog_timeout", report_path=str(report_path), pid=pid, elapsed_s=elapsed_s)
        self._schedule_agent_locked(report, "watchdog_timeout")

    def _daily_relaunch_ledger(self) -> dict[str, Any]:
        try:
            value = json.loads(self._daily_relaunch_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"version": 1, "relaunches": []}
        return value if isinstance(value, dict) else {"version": 1, "relaunches": []}

    def _daily_relaunch_due(self) -> bool:
        """Has today's restart already happened?

        The game hands out its daily content -- rewards, shop rows, event
        rotations -- at the UTC reset, and a client that has been up across the
        boundary keeps showing yesterday's.  The first segment of each UTC day
        therefore force-stops the package and relaunches it before connecting.
        An agent handover is left alone: that child is there to look at what is
        on screen, and restarting under it would throw the evidence away.
        """

        if self._pending_agent_context is not None:
            return False
        ledger = self._daily_relaunch_ledger()
        entries = ledger.get("relaunches")
        last = entries[-1] if isinstance(entries, list) and entries else None
        return not (isinstance(last, dict) and last.get("utc_day") == self._utc_day())

    def _record_daily_relaunch(self) -> None:
        """Written before the spawn, so a crash loop cannot restart all day."""

        ledger = self._daily_relaunch_ledger()
        entries = ledger.setdefault("relaunches", [])
        if not isinstance(entries, list):
            entries = []
            ledger["relaunches"] = entries
        entries.append({
            "utc_day": self._utc_day(),
            "at_epoch": int(time.time()),
            "generation": self._generation,
        })
        del entries[:-14]
        self.auto_root.mkdir(parents=True, exist_ok=True)
        temporary = self._daily_relaunch_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self._daily_relaunch_path)

    def _command(self, *, relaunch: bool = False) -> list[str]:
        serial = os.environ.get("RR_STREAM_SERIAL", "127.0.0.1:16448")
        command = [
            sys.executable, str(RR_PLAY), "--serial", serial, "--policy", "v2",
            "--backend", "mumu_ipc", "--enable-main-task", "--target-level",
            str(self.target_level), "--flush-profile", "foreground",
            *(["--force-stop-relaunch-first"] if relaunch else []),
            "--stream-url", self.stream_url,
            "--control-state-file", str(self.control_path), "--rounds", "20",
            "--output-root", str(self.auto_root), "--json",
        ]
        if self._pending_agent_context is not None:
            command.extend([
                "--agent-handler-first",
                "--agent-handler",
                "--agent-handler-model", "gpt-5.6-luna",
                "--agent-handler-drafts", str(self.auto_root / "agent-drafts"),
                "--agent-handler-context", str(self._pending_agent_context),
            ])
        return command

    def _spawn_locked(self) -> None:
        self.auto_root.mkdir(parents=True, exist_ok=True)
        self._generation += 1
        # Never interpret a report from a prior child as this generation's
        # result. rr_play writes a fresh report in this directory per segment.
        self._seen_reports.update(self.auto_root.glob("*.json"))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stdout = (self.auto_root / f"child-{self._generation:04d}-{stamp}.stdout.log").open("a", encoding="utf-8")
        stderr = (self.auto_root / f"child-{self._generation:04d}-{stamp}.stderr.log").open("a", encoding="utf-8")
        env = os.environ.copy()
        # Preserve the configured key path without reading or logging its contents.
        env["RR_SAVE_KEY_FILE"] = os.environ.get(
            "RR_SAVE_KEY_FILE", str(DEFAULT_RR_SAVE_KEY_FILE)
        )
        if not os.environ.get("RR_SAVE_KEY_FILE") and not DEFAULT_RR_SAVE_KEY_FILE.is_file():
            stdout.close()
            stderr.close()
            raise RuntimeError("default RR_SAVE_KEY_FILE is unavailable")
        env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        daily = self._daily_relaunch_due()
        if daily:
            self._record_daily_relaunch()
            self._log("daily_relaunch", generation=self._generation, utc_day=self._utc_day())
        # An escape restart is not the daily one and must not consume its slot:
        # today's fresh content is still owed even if a popup forced a restart.
        relaunch = daily or self._forced_relaunch
        self._forced_relaunch = False
        try:
            self._child = self._popen(
                self._command(relaunch=relaunch), cwd=str(REPO_ROOT), env=env, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, creationflags=creationflags,
            )
        except Exception:
            stdout.close()
            stderr.close()
            raise
        self._child_files = (stdout, stderr)
        self._child_started_at = self._now()
        self._child_is_agent = self._pending_agent_context is not None
        self._child_is_relaunch = relaunch
        self._child_activity_token = self._activity_token()
        self._child_last_activity_at = self._child_started_at
        if self._child_is_agent:
            self._stop_reason = "agent_takeover"
            self._attention_state["agent_generation"] = self._generation
            self._save_attention_state()
        self._pending_agent_context = None
        self._pending_agent_key = None
        self._log("child_started", generation=self._generation, pid=getattr(self._child, "pid", None))

    def _read_report_locked(self) -> dict[str, Any] | None:
        candidates = sorted(self.auto_root.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
        for path in reversed(candidates):
            if path in self._seen_reports or path.name in SUPERVISOR_STATE_FILES:
                continue
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(report, dict):
                continue
            self._seen_reports.add(path)
            report["_supervisor_report_path"] = str(path)
            return report
        return None

    @staticmethod
    def _state(report: dict[str, Any]) -> dict[str, Any]:
        loop = report.get("loop") if isinstance(report.get("loop"), dict) else {}
        rounds = loop.get("rounds") if isinstance(loop.get("rounds"), list) else []
        if rounds and isinstance(rounds[-1], dict):
            state = rounds[-1].get("state_after")
            if isinstance(state, dict):
                return state
        state = report.get("state_after")
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _agent_handler_reason(report: dict[str, Any]) -> str | None:
        handler = report.get("agent_handler")
        if not isinstance(handler, dict):
            return None
        reason = handler.get("reason")
        return str(reason) if reason is not None else None

    @classmethod
    def _agent_episode_did_nothing(cls, report: dict[str, Any]) -> bool:
        """True when the armed handler aborted without exploring anything."""

        handler = report.get("agent_handler")
        if not isinstance(handler, dict):
            return False
        if handler.get("result") != "abort":
            return False
        if handler.get("model_calls"):
            return False
        return not (handler.get("actions") or [])

    def _idle_escalation_due_locked(self, report: dict[str, Any], reason: str) -> bool:
        """Count segments that finish without moving the account."""

        if self._report_has_business_progress(report):
            self._idle_segments = 0
            self._last_progress_at = self._now()
            return False
        if reason in NON_IDLE_STOP_REASONS:
            return False
        self._idle_segments += 1
        self._log(
            "segment_without_progress",
            stop_reason=reason,
            consecutive=self._idle_segments,
            limit=IDLE_SEGMENT_LIMIT,
            report_path=report.get("_supervisor_report_path"),
        )
        if self._idle_segments < IDLE_SEGMENT_LIMIT:
            return False
        # Hand it to the ladder once, not once per segment afterwards.
        self._idle_segments = 0
        return True

    def _apply_report_locked(self, report: dict[str, Any]) -> None:
        agent_child = self._child_is_agent
        self._last_report = report
        self._record_business_progress_locked(report)
        state = self._state(report)
        if isinstance(state.get("level"), int):
            self._last_level = state["level"]
        if isinstance(state.get("energy"), int):
            self._last_energy = state["energy"]
        loop = report.get("loop") if isinstance(report.get("loop"), dict) else {}
        reason = str(loop.get("stop_reason") or report.get("stop_reason") or "")
        report_path = report.get("_supervisor_report_path")
        self._log("child_report", report_path=report_path, stop_reason=reason,
                  level=self._last_level, energy=self._last_energy)
        if self._last_level is not None and self._last_level >= self.target_level:
            self._finished = True
            self._wait_until = None
            self._stop_reason = "target_level_reached"
            return
        if reason == "waiting_for_energy":
            # Reuse rr_drive's established report interpretation and only
            # remove its wall-clock cap; this supervisor has no run deadline.
            driver = _rr_drive_driver()
            parser = driver(run_segment=lambda _: {}, segment_arguments=[], max_wall_clock_s=10**12,
                            now=self._now, sleep=self._wait)
            _seconds, target, _detail = parser.energy_wait_seconds(report)
            if target is None:
                self._needs_attention = True
                self._stop_reason = "needs_attention"
                self._log("needs_attention", reason="energy_wait_missing_recovery", report_path=report_path)
                return
            self._wait_until = target
            self._stop_reason = "waiting_for_energy"
            return
        if self._idle_escalation_due_locked(report, reason):
            # The screen is fine and the segments end cleanly; what is missing
            # is the account moving.  Raise it the same way a dead screen is
            # raised, so it becomes visible and enters the restart ladder
            # instead of burning hours behind a green "running".
            self._schedule_agent_locked(report, "no_progress")
            return
        if self._observe_semantic_no_progress_locked(report, reason):
            return
        if reason in AGENT_TAKEOVER_STOP_REASONS:
            self._schedule_agent_locked(report, reason)
            return
        if reason == "control_handed_over":
            # Manual takeover is an intentional cooperative end of this
            # child.  Preserve the report's reason for the UI; an explicit
            # resume() is available if the user wants automatic play again.
            self._stop_reason = reason
            self._log("control_handed_over", report_path=report_path)
            return
        if agent_child and self._attention_state and reason not in {"waiting_for_energy", "resource_wait"}:
            if self._agent_episode_did_nothing(report):
                # The handler never explored: it found the surface already
                # registered (or never reached the model), so this segment is
                # ordinary play wearing an agent label.  Holding the episode
                # here wedges automatic play for good -- resume() refuses while
                # an episode is open, and only a child report can close one, so
                # no child would ever run again.  Release it and judge the
                # segment by its own stop reason.
                self._attention_state = {}
                self._save_attention_state()
                self._log(
                    "agent_takeover_noop",
                    report_path=report_path,
                    stop_reason=reason,
                    handler_reason=self._agent_handler_reason(report),
                )
            else:
                # An agent episode that did explore must prove business
                # progress before ordinary retries resume.  This keeps a failed
                # takeover bounded.
                self._needs_attention = True
                self._stop_reason = "needs_attention"
                self._log("agent_takeover_failed", report_path=report_path, stop_reason=reason)
                return
        if reason and reason not in CONTINUING_STOP_REASONS:
            self._needs_attention = True
            self._stop_reason = "needs_attention"
            self._log("needs_attention", reason=reason, report_path=report_path)
            return
        self._stop_reason = reason or "segment_complete"

    def _reap_locked(self) -> None:
        if self._child is None or self._child.poll() is None:
            return
        exit_code = self._child.poll()
        self._child = None
        self._child_started_at = None
        self._child_activity_token = None
        self._child_last_activity_at = None
        self._close_child_files_locked()
        report = self._read_report_locked()
        if report is None:
            self._reportless_children += 1
            if self._reportless_children < REPORTLESS_CHILD_RETRY_LIMIT:
                self._stop_reason = "child_without_report"
                self._log(
                    "child_without_report_retry",
                    exit_code=exit_code,
                    attempt=self._reportless_children,
                    limit=REPORTLESS_CHILD_RETRY_LIMIT,
                )
                return
            self._needs_attention = True
            self._stop_reason = "needs_attention"
            self._log("child_without_report", exit_code=exit_code,
                      attempt=self._reportless_children)
        else:
            self._reportless_children = 0
            self._apply_report_locked(report)

    def _attention_escape_locked(self) -> bool:
        """Give a stuck episode a restart, a bounded number of times.

        Returns True when this cycle may spawn again.  The first call on a new
        episode only starts its clock: the agent handler, a hand-off from the
        user, or a late child report all get their chance first.
        """

        if self._attention_since is None:
            self._attention_since = self._now()
            return False
        waited = self._now() - self._attention_since
        if waited < ATTENTION_RELAUNCH_AFTER_S:
            return False
        if self._attention_escapes >= ATTENTION_RELAUNCH_LIMIT:
            return False
        self._attention_escapes += 1
        self._log(
            "attention_relaunch_escape",
            attention_key=self._attention_state.get("active_key"),
            reason=self._attention_state.get("reason"),
            waited_s=round(waited, 1),
            attempt=self._attention_escapes,
            limit=ATTENTION_RELAUNCH_LIMIT,
        )
        # Release the episode so the next child is an ordinary segment: it is
        # the restart, not the agent, that is being tried now.  The escape count
        # is what bounds this, and business progress is what clears it.
        self._attention_state = {}
        self._save_attention_state()
        self._last_attention_report = None
        self._needs_attention = False
        self._attention_since = None
        self._stop_reason = None
        self._forced_relaunch = True
        return True

    def _cycle(self) -> None:
        with self._lock:
            self._reap_locked()
            mode = self._mode()
            if mode != "auto":
                # rr_play observes the same control-state gate and must exit
                # cooperatively so it can flush its report and recording.
                # Only stop() (service shutdown) force-terminates a child.
                if self._child is not None or self._stop_reason is None:
                    self._stop_reason = "manual" if mode == "manual" else "paused"
                return
            self._watchdog_locked()
            if self._child is not None or self._finished:
                return
            if self._needs_attention and not self._attention_escape_locked():
                return
            if self._wait_until is not None:
                if self._now() < self._wait_until:
                    return
                self._wait_until = None
            self._spawn_locked()

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._cycle()
            except Exception as exc:  # keep supervisor alive and fail closed
                with self._lock:
                    self._needs_attention = True
                    self._stop_reason = "needs_attention"
                    self._log("supervisor_error", error_type=type(exc).__name__)
            self._stop_event.wait(0.25)

    def ensure_running(self) -> dict[str, Any]:
        with self._lock:
            self._cycle()
            # An episode in attention keeps the worker too: the restart escape
            # is measured inside _cycle, and a supervisor that came up holding a
            # persisted episode would otherwise never tick again.  A cycle in
            # attention does nothing until that wait is over.
            if self._mode() == "auto" and not self._finished:
                if self._thread is None or not self._thread.is_alive():
                    self._stop_event.clear()
                    self._thread = threading.Thread(target=self._worker, name="rr-auto-supervisor", daemon=True)
                    self._thread.start()
            return self.status()

    def resume(self) -> dict[str, Any]:
        """Resume only when no unresolved attention episode is persisted."""

        with self._lock:
            if self._attention_state.get("active_key"):
                self._needs_attention = True
                self._stop_reason = "needs_attention"
                self._log(
                    "attention_held",
                    reason="resume_without_business_progress",
                    attention_key=self._attention_state.get("active_key"),
                )
                return self.status()
            self._needs_attention = False
            self._attention_since = None
            if not self._finished:
                self._stop_reason = None
            return self.status()

    def clear_attention(self, actor: str = "person", note: str = "") -> dict[str, Any]:
        """A person says they have dealt with it; let ordinary play resume.

        2026-09-21.  An episode is held until a child report proves business
        progress, and no child runs while one is open -- so once the restart
        escapes are spent, nothing inside the supervisor can ever close it.
        The only way out was to write ``{}`` into attention-state.json and
        restart the service, because the in-memory copy would otherwise save
        over the file; that cost a stream drop twice on 2026-09-21.  This is
        the door for the person who looked at the screen and fixed it.

        The escape budget is reset with the episode: the reason the last three
        restarts did not help was the thing that has just been dealt with, and
        the next dead screen deserves its own three tries.
        """

        with self._lock:
            previous = dict(self._attention_state)
            self._attention_state = {}
            self._save_attention_state()
            self._last_attention_report = None
            self._needs_attention = False
            self._attention_since = None
            self._attention_escapes = 0
            self._idle_segments = 0
            self._last_progress_at = self._now()
            if not self._finished:
                self._stop_reason = None
            self._log(
                "attention_cleared_by_person",
                actor=actor,
                note=note,
                cleared_key=previous.get("active_key"),
                cleared_reason=previous.get("reason"),
                cleared_report=previous.get("report_path"),
                escapes_before=previous.get("relaunch_escapes"),
            )
            self._cycle()
        return self.ensure_running()

    def _stop_child_locked(self) -> None:
        child = self._child
        self._child = None
        if child is None:
            return
        try:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=2)
        except Exception:
            try:
                child.kill()
            except Exception:
                pass
        finally:
            self._close_child_files_locked()
            self._child_started_at = None
            self._child_activity_token = None
            self._child_last_activity_at = None
        self._log("child_stopped", pid=getattr(child, "pid", None))

    def _close_child_files_locked(self) -> None:
        handles = self._child_files
        self._child_files = None
        if handles is None:
            return
        for handle in handles:
            try:
                handle.close()
            except Exception:
                pass

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._stop_event.set()
            self._stop_child_locked()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
        with self._lock:
            self._stop_reason = "stopped"
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._reap_locked()
            if self._pending_agent_context is not None:
                phase = "agent_takeover_pending"
            elif self._child_is_agent and self._child is not None:
                phase = "agent_takeover"
            elif self._needs_attention:
                phase = "attention"
            elif self._wait_until is not None:
                phase = "waiting_for_energy"
            elif self._child is not None:
                phase = "running"
            else:
                phase = "idle"
            return {
                "progress": {
                    "level": self._last_level,
                    "energy": self._last_energy,
                    "target": self.target_level,
                    # "running" is not the same as "getting anywhere": these
                    # two are what tell idling apart from playing.
                    "idle_segments": self._idle_segments,
                    "seconds_since_progress": (
                        None
                        if self._last_progress_at is None
                        else round(self._now() - self._last_progress_at, 1)
                    ),
                },
                "wait_until": self._wait_until,
                "stop_reason": self._stop_reason,
                "needs_attention": self._needs_attention,
                "child_pid": None if self._child is None else getattr(self._child, "pid", None),
                "running": self._child is not None and self._child.poll() is None,
                "finished": self._finished,
                "phase": phase,
                "recovery": {
                    "attention_key": self._attention_state.get("active_key"),
                    "attention_report": self._last_attention_report,
                    "agent_attempted": bool(self._attention_state.get("agent_attempted")),
                    "relaunch_escapes": self._attention_escapes,
                    "relaunch_escape_limit": ATTENTION_RELAUNCH_LIMIT,
                    "watchdog_timeout_s": self.watchdog_timeout_s,
                    "agent_watchdog_timeout_s": self.agent_watchdog_timeout_s,
                },
            }


__all__ = ["AutoSupervisor"]
