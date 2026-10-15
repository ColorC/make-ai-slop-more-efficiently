"""Lightweight single-account tournament coordination.

This module intentionally avoids virtual machines, cryptographic authorities,
dual audit services, and production deployment machinery.  It coordinates four
logical work slots that use one game account serially, keeps a small local state
file, controls recording/pause/restart through host callbacks, and counts the
published simulation and pause budgets.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "DEFAULT_SLOTS",
    "M3Gap",
    "LightweightRefereeHooks",
    "LightweightTournament",
    "LightweightTournamentConfig",
    "LightweightTournamentError",
    "RunOutcome",
    "SkillMasteryStatus",
    "SlotMode",
    "SlotSpec",
    "build_m3_gap",
]


class LightweightTournamentError(RuntimeError):
    """A lightweight tournament state transition is invalid."""


class SlotMode(StrEnum):
    FIRST_SEEN = "first_seen"
    DEEP = "deep"


class RunOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TECHNICAL_VOID = "technical_void"


class SkillMasteryStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    mastery: int = Field(ge=0, le=3)


class M3Gap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_id: str
    skill_id: str
    current_mastery: int | None
    target_mastery: int = 3


def build_m3_gap(
    current: tuple[SkillMasteryStatus, ...],
    candidates: tuple[tuple[str, str], ...],
) -> tuple[M3Gap, ...]:
    """Return missing/non-M3 candidate skills without authorizing any upgrade."""

    observed = {(item.operator_id, item.skill_id): item.mastery for item in current}
    gaps = []
    for operator_id, skill_id in sorted(set(candidates)):
        mastery = observed.get((operator_id, skill_id))
        if mastery != 3:
            gaps.append(
                M3Gap(
                    operator_id=operator_id,
                    skill_id=skill_id,
                    current_mastery=mastery,
                )
            )
    return tuple(gaps)


class SlotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_id: str = Field(pattern=r"^[a-z0-9-]+$")
    model_label: str = Field(min_length=1)
    mode: SlotMode
    training_runs: int = Field(default=0, ge=0)
    official_runs: int = Field(ge=1)
    allow_public_research_during_training: bool = False

    @model_validator(mode="after")
    def validate_mode(self) -> SlotSpec:
        if self.mode is SlotMode.FIRST_SEEN and self.training_runs:
            raise ValueError("first-seen slots cannot have training runs")
        if self.mode is SlotMode.DEEP and self.training_runs != 10:
            raise ValueError("deep slots use exactly ten training runs")
        return self


DEFAULT_SLOTS = (
    SlotSpec(
        slot_id="luna-firstseen",
        model_label="Luna",
        mode=SlotMode.FIRST_SEEN,
        official_runs=3,
    ),
    SlotSpec(
        slot_id="deepseek-firstseen",
        model_label="DeepSeek",
        mode=SlotMode.FIRST_SEEN,
        official_runs=3,
    ),
    SlotSpec(
        slot_id="luna-deep",
        model_label="Luna",
        mode=SlotMode.DEEP,
        training_runs=10,
        official_runs=3,
        allow_public_research_during_training=True,
    ),
    SlotSpec(
        slot_id="deepseek-deep",
        model_label="DeepSeek",
        mode=SlotMode.DEEP,
        training_runs=10,
        official_runs=3,
        allow_public_research_during_training=True,
    ),
)


class LightweightTournamentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = "game-observatory.arknights-lightweight-tournament.v1"
    account_label: str = "shared-bilibili-account"
    theme_policy: str = "latest_period_per_available_theme"
    slots: tuple[SlotSpec, ...] = DEFAULT_SLOTS
    free_raw_operations: int = Field(default=200, ge=0)
    free_forecasts: int = Field(default=100, ge=0)
    free_pause_seconds: int = Field(default=7_200, ge=0)
    max_automatic_technical_restarts: int = Field(default=1, ge=0)

    @model_validator(mode="after")
    def validate_slots(self) -> LightweightTournamentConfig:
        slot_ids = [slot.slot_id for slot in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("slot ids must be unique")
        return self


class LightweightRefereeHooks(Protocol):
    """Visible host operations; implementations may wrap ADB/MAA/recording."""

    def start_recording(self, output_path: Path) -> bool: ...

    def stop_recording(self) -> bool: ...

    def pause_game(self) -> bool: ...

    def resume_game(self) -> bool: ...

    def restart_game(self) -> bool: ...


class LightweightTournament:
    """Small local coordinator for one-account, serial tournament runs."""

    _SCHEMA = "game-observatory.arknights-lightweight-state.v1"

    def __init__(
        self,
        root: Path,
        *,
        hooks: LightweightRefereeHooks,
        config: LightweightTournamentConfig | None = None,
        monotonic_ns: Callable[[], int],
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.hooks = hooks
        self.config = config or LightweightTournamentConfig()
        self.monotonic_ns = monotonic_ns
        self.utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self.state_path = self.root / "state.json"
        self._state = self._load_or_initialize()

    @property
    def state(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._state, ensure_ascii=False))

    def prepare(self) -> None:
        (self.root / "shared" / "simulator").mkdir(parents=True, exist_ok=True)
        (self.root / "shared" / "io").mkdir(parents=True, exist_ok=True)
        (self.root / "shared" / "score-tables").mkdir(parents=True, exist_ok=True)
        (self.root / "recordings").mkdir(parents=True, exist_ok=True)
        for slot in self.config.slots:
            slot_root = self.root / "slots" / slot.slot_id
            for child in ("notes", "scripts", "logs"):
                (slot_root / child).mkdir(parents=True, exist_ok=True)
        self._save()

    def start_run(
        self,
        *,
        slot_id: str,
        phase: str,
        run_number: int,
        theme: str,
    ) -> Path:
        if self._state["active_run"] is not None:
            raise LightweightTournamentError("the shared account is already in use")
        self._require_slot(slot_id)
        if phase not in {"training", "official"}:
            raise LightweightTournamentError("phase must be training or official")
        if run_number < 1 or not theme.strip():
            raise LightweightTournamentError("run number and theme are required")
        prior_runs = self._state["slots"][slot_id]["runs"]
        if any(
            run.get("phase") == phase and run.get("run_number") == run_number
            for run in prior_runs
        ):
            raise LightweightTournamentError(
                "a completed slot/phase/run number cannot be reused"
            )

        attempt = 1
        recording = self._recording_path(slot_id, theme, run_number, attempt)
        recording.parent.mkdir(parents=True, exist_ok=True)
        if not self.hooks.start_recording(recording):
            raise LightweightTournamentError("recording did not start")

        self._state["active_run"] = {
            "slot_id": slot_id,
            "phase": phase,
            "run_number": run_number,
            "theme": theme.strip(),
            "attempt": attempt,
            "recording": str(recording),
            "recording_roots": [str(recording)],
            "recording_resumes": 0,
            "raw_operations": 0,
            "forecasts": 0,
            "pause_seconds": 0.0,
            "pause_started_ns": None,
            "pause_counts_to_agent": False,
            "technical_restarts": 0,
            "warnings": [],
        }
        self._event("run_started", {"recording": str(recording)})
        self._save()
        return recording

    def resume_recording(self, *, reason: str) -> Path:
        """Start a new evidence segment after the harness process was replaced.

        A restored coordinator has no in-process recorder thread even though
        ``active_run`` is still present.  Merely reusing the old pathname makes
        the resumed play look recorded while no capture is running.  Preserve
        every recording root and rotate to a collision-free continuation
        without restarting the game or changing the attempt budget.
        """

        run = self._active_run()
        if not reason.strip():
            raise LightweightTournamentError("recording resume reason is required")
        resume_number = int(run.get("recording_resumes", 0)) + 1
        base_recording = self._recording_path(
            run["slot_id"], run["theme"], run["run_number"], run["attempt"]
        )
        recording = base_recording.with_name(
            f"{base_recording.stem}__resume-{resume_number}.mp4"
        )
        recording.parent.mkdir(parents=True, exist_ok=True)
        if not self.hooks.start_recording(recording):
            raise LightweightTournamentError("recording did not resume")
        roots = list(run.get("recording_roots") or [run["recording"]])
        roots.append(str(recording))
        run["recording"] = str(recording)
        run["recording_roots"] = roots
        run["recording_resumes"] = resume_number
        self._event(
            "recording_resumed",
            {"reason": reason.strip(), "recording": str(recording)},
        )
        self._save()
        return recording

    def record_forecast(self, *, raw_operations: int = 2) -> None:
        run = self._active_run()
        if raw_operations < 2:
            raise LightweightTournamentError("a forecast uses at least two raw operations")
        run["forecasts"] += 1
        run["raw_operations"] += raw_operations
        self._event("forecast_recorded", {"raw_operations": raw_operations})
        self._save()

    def record_raw_simulation(self, operations: int = 1) -> None:
        run = self._active_run()
        if operations < 1:
            raise LightweightTournamentError("raw operations must be positive")
        run["raw_operations"] += operations
        self._event("raw_simulation_recorded", {"operations": operations})
        self._save()

    def pause(self, *, reason: str, count_to_agent: bool = True) -> None:
        run = self._active_run()
        if run["pause_started_ns"] is not None:
            raise LightweightTournamentError("the run is already paused")
        if not reason.strip() or not self.hooks.pause_game():
            raise LightweightTournamentError("the game did not pause")
        run["pause_started_ns"] = self.monotonic_ns()
        run["pause_counts_to_agent"] = bool(count_to_agent)
        self._event("paused", {"reason": reason, "counts_to_agent": count_to_agent})
        self._save()

    def resume(self, *, reason: str = "continue") -> None:
        run = self._active_run()
        started = run["pause_started_ns"]
        if started is None:
            raise LightweightTournamentError("the run is not paused")
        now = self.monotonic_ns()
        if now < started:
            raise LightweightTournamentError("monotonic clock moved backwards")
        if not self.hooks.resume_game():
            raise LightweightTournamentError("the game did not resume")
        elapsed = (now - started) / 1_000_000_000
        if run["pause_counts_to_agent"]:
            run["pause_seconds"] += elapsed
        run["pause_started_ns"] = None
        run["pause_counts_to_agent"] = False
        self._event("resumed", {"reason": reason, "elapsed_seconds": elapsed})
        self._save()

    def technical_restart(self, *, reason: str, referee_override: bool = False) -> Path:
        run = self._active_run()
        if run["pause_started_ns"] is not None:
            self.resume(reason="technical_restart")
            run = self._active_run()
        if (
            run["technical_restarts"] >= self.config.max_automatic_technical_restarts
            and not referee_override
        ):
            raise LightweightTournamentError("automatic technical restart limit reached")
        self.hooks.stop_recording()
        if not reason.strip() or not self.hooks.restart_game():
            raise LightweightTournamentError("the game did not restart")
        run["technical_restarts"] += 1
        run["attempt"] += 1
        recording = self._recording_path(
            run["slot_id"], run["theme"], run["run_number"], run["attempt"]
        )
        if not self.hooks.start_recording(recording):
            raise LightweightTournamentError("recording did not restart")
        roots = list(run.get("recording_roots") or [run["recording"]])
        roots.append(str(recording))
        run["recording"] = str(recording)
        run["recording_roots"] = roots
        run["recording_resumes"] = 0
        self._event(
            "technical_restart",
            {"reason": reason, "recording": str(recording), "override": referee_override},
        )
        self._save()
        return recording

    def warn(self, message: str) -> None:
        run = self._active_run()
        if not message.strip():
            raise LightweightTournamentError("warning text is required")
        run["warnings"].append(message.strip())
        self._event("warning", {"message": message.strip()})
        self._save()

    def finish(self, *, outcome: RunOutcome, base_score: int) -> dict[str, Any]:
        run = self._active_run()
        if run["pause_started_ns"] is not None:
            self.resume(reason="run_finished")
            run = self._active_run()
        self.hooks.stop_recording()
        penalties = self._penalties(run)
        summary = {
            **run,
            "outcome": outcome.value,
            "base_score": base_score,
            "forecast_penalty": penalties["forecast_penalty"],
            "pause_penalty": penalties["pause_penalty"],
            "final_score": base_score - penalties["forecast_penalty"] - penalties["pause_penalty"],
        }
        self._state["slots"][run["slot_id"]]["runs"].append(summary)
        self._event("run_finished", {"outcome": outcome.value})
        self._state["active_run"] = None
        self._save()
        return json.loads(json.dumps(summary, ensure_ascii=False))

    def administrative_void(self, *, reason: str) -> dict[str, Any]:
        """Close an orphaned training run without claiming a device-side stop."""

        run = self._active_run()
        reason = reason.strip()
        if not reason:
            raise LightweightTournamentError("technical void reason is required")
        if run["phase"] != "training":
            raise LightweightTournamentError("official runs cannot be administratively voided")
        run["warnings"].append(reason)
        run["pause_started_ns"] = None
        run["pause_counts_to_agent"] = False
        penalties = self._penalties(run)
        summary = {
            **run,
            "outcome": RunOutcome.TECHNICAL_VOID.value,
            "base_score": 0,
            "forecast_penalty": penalties["forecast_penalty"],
            "pause_penalty": penalties["pause_penalty"],
            "final_score": 0,
            "technical_void_reason": reason,
            "recording_stop_status": "unverified_orphan_cleanup_required",
        }
        self._state["slots"][run["slot_id"]]["runs"].append(summary)
        self._event("run_administratively_voided", {"reason": reason})
        self._state["active_run"] = None
        self._save()
        return json.loads(json.dumps(summary, ensure_ascii=False))

    def current_budget(self) -> dict[str, int | float]:
        run = self._active_run()
        projected = dict(run)
        started = projected["pause_started_ns"]
        if started is not None and projected["pause_counts_to_agent"]:
            now = self.monotonic_ns()
            if now < started:
                raise LightweightTournamentError("monotonic clock moved backwards")
            projected["pause_seconds"] += (now - started) / 1_000_000_000
        return {
            "raw_operations": projected["raw_operations"],
            "forecasts": projected["forecasts"],
            "pause_seconds": projected["pause_seconds"],
            **self._penalties(projected),
        }

    def _penalties(self, run: dict[str, Any]) -> dict[str, int]:
        forecast_overage = max(
            run["forecasts"] - self.config.free_forecasts,
            math.ceil(max(0, run["raw_operations"] - self.config.free_raw_operations) / 2),
            0,
        )
        pause_over_seconds = max(0.0, run["pause_seconds"] - self.config.free_pause_seconds)
        pause_minutes = math.ceil(pause_over_seconds / 60) if pause_over_seconds else 0
        pause_penalty = min(pause_minutes, 30) * 5 + max(0, pause_minutes - 30) * 10
        return {
            "forecast_penalty": forecast_overage * 10,
            "pause_penalty": pause_penalty,
        }

    def _load_or_initialize(self) -> dict[str, Any]:
        if self.state_path.exists():
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if payload.get("schema_id") != self._SCHEMA:
                raise LightweightTournamentError("unsupported lightweight state schema")
            if payload.get("account_label") != self.config.account_label:
                raise LightweightTournamentError("state belongs to another account")
            return payload
        return {
            "schema_id": self._SCHEMA,
            "account_label": self.config.account_label,
            "event_sequence": 0,
            "active_run": None,
            "events": [],
            "slots": {slot.slot_id: {"runs": []} for slot in self.config.slots},
        }

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.state_path)

    def _event(self, event_type: str, details: dict[str, Any]) -> None:
        self._state["event_sequence"] += 1
        self._state["events"].append(
            {
                "sequence": self._state["event_sequence"],
                "event_type": event_type,
                "monotonic_ns": self.monotonic_ns(),
                "details": details,
            }
        )

    def _active_run(self) -> dict[str, Any]:
        run = self._state["active_run"]
        if run is None:
            raise LightweightTournamentError("there is no active run")
        return run

    def _require_slot(self, slot_id: str) -> SlotSpec:
        for slot in self.config.slots:
            if slot.slot_id == slot_id:
                return slot
        raise LightweightTournamentError(f"unknown slot: {slot_id}")

    def _recording_path(self, slot_id: str, theme: str, run_number: int, attempt: int) -> Path:
        safe_theme = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in theme)
        timestamp = self.utc_now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return (
            self.root
            / "recordings"
            / f"{slot_id}__{safe_theme}__run-{run_number}__attempt-{attempt}__{timestamp}.mp4"
        )
