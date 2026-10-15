# [OMNI] origin=kimi-code created_by=kimi-code intent="F4-13 explore-first agent handler for unregistered Romantic Restaurant surfaces"
"""F4-13: bounded agent exploration of an unregistered or unexplored surface.

When the loop stops on a surface nobody has registered (``unknown_screen`` /
``not_on_board``) or on a registered surface still marked ``unexplored`` in the
navigation registry, the guardian may arm the next session's first segment with
``--agent-handler-first`` instead of asking a person at once.  That segment
runs this handler once, before its ordinary loop: a vision model is handed a
bounded evidence pack (recent frames, the authoritative save summary, the
registered surface list, the red-line rule text and everything already tried
on this surface) and proposes zero-cost exploration actions under a hard
schema contract.

Hard rules, all enforced in code here and never delegated to the model:

- the action space is view/scroll/tab/enter/claim-free/close only; every
  action is re-checked against the authoritative save after it runs, and any
  drop in energy, coins, gems or board content is a refutation;
- every action is adjudicated by its before/after frames; one refuted action
  fuses the whole exploration;
- the actuator's own pre-frame red-line guard stays in force unchanged --
  handler intents carry the ``navigation:agent-handler:`` prefix, which is the
  same surface-traversal class the state machine's own navigation uses;
- budgets: at most MAX_EXPLORATION_ACTIONS actions and
  MAX_EXPLORATION_WALL_S wall seconds per surface;
- one surface pays the model cost once: a drafts directory whose signature
  draft already matches the current frame ends the run before any model call.

Whether it converges or fuses, the handler writes the three artifacts the
registration pipeline consumes: the interaction-point list with its frame
evidence, the reverse-design notes, and a signature draft.  A draft is never
registered automatically.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .contracts import ActionRequestV1
from .navigation import DEFAULT_RGB_DISTANCE_THRESHOLD, SurfaceRegistry, frame_to_rgb


# F4-13 section 3: the per-surface exploration budgets.
MAX_EXPLORATION_ACTIONS = 12
MAX_EXPLORATION_WALL_S = 300.0
# F4-13 section 4: the evidence pack carries at most this many recent frames.
EVIDENCE_FRAME_LIMIT = 12
SCREEN_WIDTH = 1080
SCREEN_HEIGHT = 1920
# The actuator's pre-frame guard lets only surface-traversal intents onto a
# non-board surface, so every handler action carries this prefix -- the exact
# same legality layer the state machine's own navigation actions use.
INTENT_PREFIX = "navigation:agent-handler:"
# The contract's action entries carry a single x, y pair; how far a wait
# holds and how fast a gesture moves when the model does not say are fixed
# here rather than invented per call.
DEFAULT_WAIT_MS = 1000
DEFAULT_GESTURE_MS = 500
MIN_DRAG_MS = 300

# The stop vocabulary the guardian mirrors into its own event log.  Anything
# else the handler might want to say is a bug, not a new result.
RESULT_EXPLORED_DONE = "explored_done"
RESULT_ABORT = "abort"
RESULT_FILTER_REJECTED = "filter_rejected"
RESULT_SCHEMA_INVALID = "schema_invalid"
RESULT_REFUTED_ABORT = "refuted_abort"
RESULT_BUDGET_EXHAUSTED = "budget_exhausted"
HANDLER_RESULTS = frozenset(
    {
        RESULT_EXPLORED_DONE,
        RESULT_ABORT,
        RESULT_FILTER_REJECTED,
        RESULT_SCHEMA_INVALID,
        RESULT_REFUTED_ABORT,
        RESULT_BUDGET_EXHAUSTED,
    }
)

_PRIMITIVES = frozenset({"tap", "swipe", "drag", "wait"})
_ACTION_KEYS = frozenset(
    {"primitive", "x", "y", "intent", "ms", "x2", "y2", "duration_ms"}
)
_EXPLORATION_KEYS = frozenset(
    {"interaction_points", "design_notes", "signature_draft"}
)
_POINT_KEYS = frozenset({"x", "y", "kind", "semantics", "result"})
_ANCHOR_KEYS = frozenset({"x", "y", "size", "mean_rgb"})

# The JSON schema the production caller hands to the structured-JSON runtime.
# The runtime checks types and required keys only; coordinate bounds, the
# primitive vocabulary and the per-kind parameter rules are enforced by
# validate_proposal below, which is the contract's hard gate.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "surface_guess",
        "semantics",
        "actions",
        "exploration",
        "done",
        "abort",
    ],
    "properties": {
        "surface_guess": {"type": "string"},
        "semantics": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["primitive", "x", "y", "intent"],
                "properties": {
                    "primitive": {
                        "type": "string",
                        "enum": ["tap", "swipe", "drag", "wait"],
                    },
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "intent": {"type": "string"},
                    "ms": {"type": "integer"},
                    "x2": {"type": "integer"},
                    "y2": {"type": "integer"},
                    "duration_ms": {"type": "integer"},
                },
            },
        },
        "exploration": {
            "type": ["object", "null"],
            "properties": {
                "interaction_points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["x", "y", "kind", "semantics", "result"],
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "kind": {"type": "string"},
                            "semantics": {"type": "string"},
                            "result": {"type": "string"},
                        },
                    },
                },
                "design_notes": {"type": "string"},
                "signature_draft": {
                    "type": ["object", "null"],
                    "properties": {
                        "anchors": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["x", "y", "size", "mean_rgb"],
                                "properties": {
                                    "x": {"type": "integer"},
                                    "y": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "mean_rgb": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                    },
                                },
                            },
                        }
                    },
                },
            },
        },
        "done": {"type": "boolean"},
        "abort": {
            "type": ["object", "null"],
            "properties": {"reason": {"type": "string"}},
        },
    },
}

SYSTEM_PROMPT = """You are the exploration agent for an unattended merge-game run.
You are looking at one phone screen (1080x1920) of a game surface that the
deterministic state machine does not know.  Reverse-engineer it.

Hard rules:
- You may ONLY propose zero-cost actions: look, scroll, switch tabs, enter a
  sub-panel, claim something the game offers for free, or close/back out.
  Never propose a purchase, an ad watch, a login, a share, or anything that
  could spend energy, coins, gems or items.
- Every action you propose is checked against the game's authoritative save
  afterwards; if any resource or board item decreased, the exploration is
  fused immediately and the surface is handed to a human.
- Every action is also checked against before/after frames; an action that
  changes nothing counts as refuted and fuses the exploration.
- Stay inside the action budget stated in the evidence pack.  When you have
  learned what this surface is, or when nothing is left that is safe to try,
  set done=true.
- If the surface looks like a payment, advertisement, login or external-link
  surface, or you cannot tell what it is, set abort={reason} instead.

Answer with one JSON object matching the contract exactly:
{surface_guess, semantics, actions, exploration, done, abort}.
Each action is {primitive, x, y, intent} with optional ms (wait), or x2, y2
and duration_ms (swipe/drag).  exploration is null or {interaction_points,
design_notes, signature_draft}; a signature draft anchor is {x, y, size,
mean_rgb} with x, y the centre of a size-by-size patch whose average colour
is mean_rgb.  abort is null or {reason}."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _in_bounds(x: Any, y: Any) -> bool:
    return (
        _is_int(x)
        and _is_int(y)
        and 0 <= x < SCREEN_WIDTH
        and 0 <= y < SCREEN_HEIGHT
    )


def _validate_action(raw: Any, index: int) -> tuple[dict[str, Any] | None, str | None]:
    """One contract action entry, normalized to primitive + actuator params."""

    if not isinstance(raw, dict):
        return None, f"actions[{index}] is not an object"
    extra = set(raw) - _ACTION_KEYS
    if extra:
        return None, f"actions[{index}] has unknown keys: {sorted(extra)}"
    primitive = raw.get("primitive")
    if primitive not in _PRIMITIVES:
        return None, f"actions[{index}].primitive must be one of {sorted(_PRIMITIVES)}"
    intent = raw.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        return None, f"actions[{index}].intent must be a non-empty string"
    x, y = raw.get("x"), raw.get("y")
    if not _in_bounds(x, y):
        return None, f"actions[{index}] x/y must be inside {SCREEN_WIDTH}x{SCREEN_HEIGHT}"
    if primitive == "tap":
        params = {"x": x, "y": y}
    elif primitive == "wait":
        ms = raw.get("ms", DEFAULT_WAIT_MS)
        if not _is_int(ms) or ms < 0:
            return None, f"actions[{index}].ms must be a non-negative integer"
        params = {"ms": ms}
    else:
        # The contract carries one coordinate pair; where a gesture ends is an
        # optional extension.  A gesture without its destination is rejected,
        # not guessed.
        x2, y2 = raw.get("x2"), raw.get("y2")
        if not _in_bounds(x2, y2):
            return None, (
                f"actions[{index}] {primitive} requires in-bounds x2/y2"
            )
        duration = raw.get("duration_ms", DEFAULT_GESTURE_MS)
        if not _is_int(duration) or duration <= 0:
            return None, f"actions[{index}].duration_ms must be a positive integer"
        if primitive == "drag" and duration < MIN_DRAG_MS:
            return None, f"actions[{index}] drag duration_ms must be at least {MIN_DRAG_MS}"
        params = {
            "x1": x,
            "y1": y,
            "x2": x2,
            "y2": y2,
            "duration_ms": duration,
        }
    return (
        {"primitive": primitive, "intent": intent, "x": x, "y": y, "params": params},
        None,
    )


def _validate_anchors(raw: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not isinstance(raw, list):
        return None, "signature_draft.anchors must be a list"
    anchors: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) - _ANCHOR_KEYS:
            return None, f"signature_draft.anchors[{index}] is malformed"
        if not _in_bounds(item.get("x"), item.get("y")):
            return None, f"signature_draft.anchors[{index}] x/y out of bounds"
        size = item.get("size")
        if not _is_int(size) or size < 4:
            return None, f"signature_draft.anchors[{index}].size must be an integer >= 4"
        mean_rgb = item.get("mean_rgb")
        if (
            not isinstance(mean_rgb, list)
            or len(mean_rgb) != 3
            or any(not _is_int(channel) or not 0 <= channel <= 255 for channel in mean_rgb)
        ):
            return None, (
                f"signature_draft.anchors[{index}].mean_rgb must be three integers 0..255"
            )
        anchors.append(
            {
                "x": item["x"],
                "y": item["y"],
                "size": size,
                "mean_rgb": list(mean_rgb),
            }
        )
    return anchors, None


def _validate_exploration(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, dict) or set(raw) - _EXPLORATION_KEYS:
        return None, "exploration must be null or an object with known keys"
    points_raw = raw.get("interaction_points", [])
    if not isinstance(points_raw, list):
        return None, "exploration.interaction_points must be a list"
    points: list[dict[str, Any]] = []
    for index, item in enumerate(points_raw):
        if (
            not isinstance(item, dict)
            or set(item) - _POINT_KEYS
            or any(key not in item for key in ("x", "y", "kind", "semantics", "result"))
        ):
            return None, f"exploration.interaction_points[{index}] is malformed"
        if not _in_bounds(item.get("x"), item.get("y")):
            return None, f"exploration.interaction_points[{index}] x/y out of bounds"
        if any(
            not isinstance(item.get(key), str) for key in ("kind", "semantics", "result")
        ):
            return None, (
                f"exploration.interaction_points[{index}] kind/semantics/result must be strings"
            )
        points.append({key: item[key] for key in ("x", "y", "kind", "semantics", "result")})
    design_notes = raw.get("design_notes", "")
    if not isinstance(design_notes, str):
        return None, "exploration.design_notes must be a string"
    signature_raw = raw.get("signature_draft")
    signature_draft: dict[str, Any] | None = None
    if signature_raw is not None:
        if not isinstance(signature_raw, dict) or set(signature_raw) - {"anchors"}:
            return None, "exploration.signature_draft must be null or {anchors: [...]}"
        anchors, error = _validate_anchors(signature_raw.get("anchors"))
        if error:
            return None, error
        signature_draft = {"anchors": anchors}
    return (
        {
            "interaction_points": points,
            "design_notes": design_notes,
            "signature_draft": signature_draft,
        },
        None,
    )


def validate_proposal(payload: Any) -> tuple[dict[str, Any] | None, str | None]:
    """The F4-13 output contract's hard gate.

    Returns the normalized proposal, or (None, reason) -- and a reason here
    means the handler answers schema_invalid and fuses, never a retry against
    the device.
    """

    if not isinstance(payload, dict):
        return None, "proposal is not an object"
    allowed = {
        "surface_guess",
        "semantics",
        "actions",
        "exploration",
        "done",
        "abort",
    }
    extra = set(payload) - allowed
    if extra:
        return None, f"unknown keys: {sorted(extra)}"
    guess = payload.get("surface_guess")
    if not isinstance(guess, str) or not guess.strip():
        return None, "surface_guess must be a non-empty string"
    semantics = payload.get("semantics")
    if not isinstance(semantics, str):
        return None, "semantics must be a string"
    done = payload.get("done")
    if not isinstance(done, bool):
        return None, "done must be a boolean"
    abort = payload.get("abort")
    if abort is not None:
        if (
            not isinstance(abort, dict)
            or not isinstance(abort.get("reason"), str)
            or not abort["reason"].strip()
        ):
            return None, "abort must be null or {reason: non-empty string}"
    actions_raw = payload.get("actions")
    if not isinstance(actions_raw, list):
        return None, "actions must be a list"
    actions: list[dict[str, Any]] = []
    for index, raw in enumerate(actions_raw):
        action, error = _validate_action(raw, index)
        if error:
            return None, error
        actions.append(action)
    exploration, error = _validate_exploration(payload.get("exploration"))
    if error:
        return None, error
    return (
        {
            "surface_guess": guess.strip(),
            "semantics": semantics,
            "actions": actions,
            "exploration": exploration,
            "done": done,
            "abort": abort,
        },
        None,
    )


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unknown-surface"


def _zero_cost_snapshot(semantic: Any) -> dict[str, Any]:
    """The save fields an exploration action must never decrease."""

    resources = semantic.resources
    items: dict[str, int] = {}
    occupied = 0
    for row in semantic.board.cells:
        for cell in row:
            code = getattr(cell, "raw_code", None)
            if code:
                occupied += 1
                items[str(code)] = items.get(str(code), 0) + 1
    return {
        "energy": resources.energy,
        "coins": resources.coins,
        "gems": resources.gems,
        "board_occupied": occupied,
        "board_items": items,
    }


def _zero_cost_violation(before: dict[str, Any], after: dict[str, Any]) -> str | None:
    for field in ("energy", "coins", "gems"):
        earlier, later = before.get(field), after.get(field)
        if _is_int(earlier) and _is_int(later) and later < earlier:
            return f"zero_cost_violation:{field}"
    if after["board_occupied"] < before["board_occupied"]:
        return "zero_cost_violation:board_occupied"
    for code, count in before["board_items"].items():
        if after["board_items"].get(code, 0) < count:
            return "zero_cost_violation:board_items"
    return None


def _anchor_matches(frame_rgb: Any, anchor: dict[str, Any], *, threshold: float) -> bool:
    """Whether one signature-draft anchor's patch matches this frame."""

    half = int(anchor["size"]) // 2
    height, width = frame_rgb.shape[:2]
    x0 = max(int(anchor["x"]) - half, 0)
    y0 = max(int(anchor["y"]) - half, 0)
    crop = frame_rgb[y0 : min(y0 + int(anchor["size"]), height), x0 : min(x0 + int(anchor["size"]), width)]
    if crop.size == 0:
        return False
    mean = crop.reshape(-1, crop.shape[-1]).mean(axis=0)
    distance = float(np.linalg.norm(mean - np.asarray(anchor["mean_rgb"], dtype=float)))
    return distance <= threshold


class AgentHandler:
    """One bounded exploration of one surface; everything injectable."""

    def __init__(
        self,
        *,
        actuator: Any,
        run: Any,
        registry: SurfaceRegistry,
        read_semantic: Callable[[], Any],
        drafts_dir: Path,
        model_caller: Callable[[dict[str, Any]], Any] | None,
        model_name: str = "",
        trigger: str = "unknown_screen",
        expected_surface: str | None = None,
        max_actions: int = MAX_EXPLORATION_ACTIONS,
        max_wall_s: float = MAX_EXPLORATION_WALL_S,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_actions < 1:
            raise ValueError("max_actions must be positive")
        if max_wall_s <= 0:
            raise ValueError("max_wall_s must be positive")
        self.actuator = actuator
        # Named `replay` rather than `run` so the attribute never shadows the
        # run() method on the instance.
        self.replay = run
        self.registry = registry
        self.read_semantic = read_semantic
        self.drafts_dir = Path(drafts_dir)
        self.model_caller = model_caller
        self.model_name = model_name
        self.trigger = trigger
        self.expected_surface = expected_surface
        self.max_actions = max_actions
        self.max_wall_s = max_wall_s
        self.now = now
        self.sleep = sleep

    # -- device frames ------------------------------------------------------

    def _capture(self) -> tuple[Any, str, str]:
        """One frame: its RGB array, its exact hash and its replay frame id."""

        pixels, width, height, channels, _ms = self.actuator.screenshot_raw()
        channel_count = 4 if channels in {"RGBA", "BGRA"} else 3
        rgb = None
        if len(pixels) == int(width) * int(height) * channel_count:
            array = np.frombuffer(pixels, dtype=np.uint8).reshape(
                int(height), int(width), channel_count
            )
            if channels == "BGRA":
                array = array[:, :, [2, 1, 0, 3]]
            rgb = frame_to_rgb(array)
        sha = hashlib.sha256(pixels).hexdigest()
        record = self.replay.add_frame_raw(
            pixels,
            int(width),
            int(height),
            channels=channels if channels in {"RGB", "BGR", "RGBA"} else "RGBA",
            trigger="manual",
        )
        return rgb, sha, record.frame_id

    def _frame_entry(self, frame_id: str) -> dict[str, Any]:
        entry: dict[str, Any] = {"frame_id": frame_id}
        run_dir = getattr(self.replay, "run_dir", None)
        if run_dir is not None:
            entry["path"] = str(Path(run_dir) / "frames" / f"{frame_id}.webp")
        return entry

    # -- the one-surface-one-model-cost rule ---------------------------------

    def _already_explored(self, frame_rgb: Any) -> str | None:
        """The drafts directory whose signature draft matches this frame, if any."""

        if not self.drafts_dir.is_dir():
            return None
        for candidate in sorted(self.drafts_dir.glob("*/signature-draft.json")):
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            anchors = payload.get("anchors") if isinstance(payload, dict) else None
            if not isinstance(anchors, list) or not anchors:
                continue
            if all(
                isinstance(anchor, dict)
                and _in_bounds(anchor.get("x"), anchor.get("y"))
                and _is_int(anchor.get("size"))
                and isinstance(anchor.get("mean_rgb"), list)
                and _anchor_matches(
                    frame_rgb,
                    anchor,
                    threshold=self.registry.rgb_distance_threshold,
                )
                for anchor in anchors
            ):
                return str(candidate.parent)
        return None

    # -- the evidence pack ----------------------------------------------------

    def _evidence(
        self,
        *,
        frames: list[dict[str, Any]],
        baseline: dict[str, Any],
        actions_taken: int,
        started: float,
    ) -> dict[str, Any]:
        return {
            "task": (
                "Identify this unregistered game surface, map its safe "
                "interaction points, and draft a registration signature."
            ),
            "trigger": {"kind": self.trigger, "surface": self.expected_surface},
            "screen": {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT},
            "frames": frames[-EVIDENCE_FRAME_LIMIT:],
            "save_state": {
                "energy": baseline.get("energy"),
                "coins": baseline.get("coins"),
                "gems": baseline.get("gems"),
                "board_occupied": baseline.get("board_occupied"),
            },
            "registered_surfaces": sorted(self.registry.surfaces),
            "redline_rules": self.registry.payload.get("safety"),
            "interaction_points_tried": list(self._points),
            "budget": {
                "actions_left": self.max_actions - actions_taken,
                "wall_s_left": round(self.max_wall_s - (self.now() - started), 1),
            },
            "output_contract": OUTPUT_SCHEMA,
        }

    # -- the three artifacts ---------------------------------------------------

    def _write_drafts(
        self,
        *,
        surface_guess: str | None,
        evidence: dict[str, Any] | None,
        actions: list[dict[str, Any]],
        result: str,
        reason: str | None,
    ) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = _slug(surface_guess or self.expected_surface or "unknown-surface")
        directory = self.drafts_dir / f"{slug}-{stamp}"
        suffix = 1
        while directory.exists():
            suffix += 1
            directory = self.drafts_dir / f"{slug}-{stamp}-{suffix}"
        directory.mkdir(parents=True)
        (directory / "interaction-points.json").write_text(
            json.dumps(
                {
                    "surface_guess": surface_guess,
                    "result": result,
                    "points": self._points,
                    "actions": actions,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (directory / "design-notes.md").write_text(
            f"# {surface_guess or 'unknown surface'} -- reverse-design notes\n\n"
            f"{self._design_notes or '(none)'}\n",
            encoding="utf-8",
        )
        signature = self._signature_draft or {"anchors": []}
        (directory / "signature-draft.json").write_text(
            json.dumps(
                {
                    "surface_guess": surface_guess,
                    "status": "draft-not-registered",
                    "anchors": signature.get("anchors", []),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (directory / "evidence-pack.json").write_text(
            json.dumps(
                {"result": result, "reason": reason, "evidence": evidence},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return directory

    # -- the run --------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        started = self.now()
        self._points: list[dict[str, Any]] = []
        self._design_notes = ""
        self._signature_draft: dict[str, Any] | None = None
        frames: list[dict[str, Any]] = []
        actions_log: list[dict[str, Any]] = []
        surface_guess: str | None = None
        model_calls = 0
        last_evidence: dict[str, Any] | None = None

        def finish(
            result: str,
            reason: str | None = None,
            *,
            write_drafts: bool = False,
        ) -> dict[str, Any]:
            drafts = None
            if write_drafts:
                drafts = self._write_drafts(
                    surface_guess=surface_guess,
                    evidence=last_evidence,
                    actions=actions_log,
                    result=result,
                    reason=reason,
                )
            return {
                "result": result,
                "reason": reason,
                "trigger": {"kind": self.trigger, "surface": self.expected_surface},
                "surface_guess": surface_guess,
                "model": self.model_name or None,
                "model_calls": model_calls,
                "actions": list(actions_log),
                "interaction_points": list(self._points),
                "drafts_dir": None if drafts is None else str(drafts),
                "wall_s": round(self.now() - started, 3),
            }

        # 1. What is actually on screen.  A red-line surface is never explored,
        # and a surface the registry already knows (other than the unexplored
        # one the loop stopped on) is a guardian mistake, not an exploration.
        try:
            rgb, sha, frame_id = self._capture()
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            return finish(RESULT_ABORT, f"frame_capture_failed:{type(exc).__name__}")
        frames.append(self._frame_entry(frame_id))
        if rgb is None:
            return finish(RESULT_ABORT, "frame_unavailable")
        forbidden = self.registry.detect_forbidden_surface(rgb)
        if forbidden is not None:
            return finish(RESULT_ABORT, f"forbidden_surface_present:{forbidden.name}")
        screen = self.registry.identify_screen(rgb)
        if screen.name not in {"unknown", "overlay-unknown"} and (
            screen.name != self.expected_surface
        ):
            return finish(RESULT_ABORT, f"surface_already_registered:{screen.name}")

        # 2. One surface pays the model once: an existing signature draft that
        # matches this frame ends the run before any model call.
        explored = self._already_explored(rgb)
        if explored is not None:
            return finish(RESULT_ABORT, f"surface_already_explored:{explored}")

        # 3. No model configured is a configuration fact, not an exploration.
        if self.model_caller is None:
            return finish(RESULT_ABORT, "model_not_configured")

        # 4. The authoritative baseline every action is measured against.  A
        # save that will not read fails closed: without it the zero-cost rule
        # cannot be checked at all.
        try:
            baseline = _zero_cost_snapshot(self.read_semantic())
        except Exception as exc:  # noqa: BLE001 - fail closed
            return finish(RESULT_ABORT, f"save_baseline_unavailable:{type(exc).__name__}")

        # 5. The exploration loop.
        while True:
            if len(actions_log) >= self.max_actions:
                return finish(
                    RESULT_BUDGET_EXHAUSTED, "actions", write_drafts=True
                )
            if self.now() - started >= self.max_wall_s:
                return finish(
                    RESULT_BUDGET_EXHAUSTED, "wall_clock", write_drafts=True
                )
            last_evidence = self._evidence(
                frames=frames,
                baseline=baseline,
                actions_taken=len(actions_log),
                started=started,
            )
            try:
                payload = self.model_caller(last_evidence)
            except Exception as exc:  # noqa: BLE001 - a model failure is a contract failure
                detail = str(exc).replace("\n", " ")[:160]
                return finish(
                    RESULT_SCHEMA_INVALID,
                    f"model_call_failed:{type(exc).__name__}:{detail}",
                    write_drafts=True,
                )
            model_calls += 1
            proposal, error = validate_proposal(payload)
            if error is not None or proposal is None:
                return finish(RESULT_SCHEMA_INVALID, error, write_drafts=True)
            surface_guess = proposal["surface_guess"]
            exploration = proposal["exploration"]
            if exploration is not None:
                for point in exploration["interaction_points"]:
                    self._points.append({**point, "source": "model"})
                if exploration["design_notes"]:
                    self._design_notes = exploration["design_notes"]
                if exploration["signature_draft"] is not None:
                    self._signature_draft = exploration["signature_draft"]
            if proposal["abort"] is not None:
                return finish(
                    RESULT_ABORT, proposal["abort"]["reason"], write_drafts=True
                )

            for action in proposal["actions"]:
                if len(actions_log) >= self.max_actions:
                    return finish(
                        RESULT_BUDGET_EXHAUSTED, "actions", write_drafts=True
                    )
                if self.now() - started >= self.max_wall_s:
                    return finish(
                        RESULT_BUDGET_EXHAUSTED, "wall_clock", write_drafts=True
                    )
                # Every action is judged on the frame it was sent onto: the
                # handler's own pre-frame red-line check first, then the
                # actuator's unchanged pre-frame guard underneath it.
                try:
                    pre_rgb, pre_sha, pre_frame_id = self._capture()
                except Exception as exc:  # noqa: BLE001
                    return finish(
                        RESULT_ABORT,
                        f"frame_capture_failed:{type(exc).__name__}",
                        write_drafts=True,
                    )
                frames.append(self._frame_entry(pre_frame_id))
                if pre_rgb is not None:
                    pre_forbidden = self.registry.detect_forbidden_surface(pre_rgb)
                    if pre_forbidden is not None:
                        actions_log.append(
                            {
                                "intent": action["intent"],
                                "primitive": action["primitive"],
                                "verdict": "refused",
                                "reason": f"pre_frame_forbidden:{pre_forbidden.name}",
                                "pre_frame_id": pre_frame_id,
                            }
                        )
                        return finish(
                            RESULT_FILTER_REJECTED,
                            f"pre_frame_forbidden:{pre_forbidden.name}",
                            write_drafts=True,
                        )
                request = ActionRequestV1(
                    kind=action["primitive"],
                    intent=f"{INTENT_PREFIX}{action['intent']}",
                    params=action["params"],
                )
                try:
                    action_result = self.actuator.act(request)
                except Exception as exc:  # noqa: BLE001 - recorded, not raised
                    return finish(
                        RESULT_ABORT,
                        f"actuator_error:{type(exc).__name__}",
                        write_drafts=True,
                    )
                if action_result.error:
                    verdict_reason = action_result.error
                    verdict = (
                        "refused"
                        if verdict_reason.startswith("refused_by_pre_frame")
                        else "error"
                    )
                    actions_log.append(
                        {
                            "intent": action["intent"],
                            "primitive": action["primitive"],
                            "verdict": verdict,
                            "reason": verdict_reason,
                            "pre_frame_id": pre_frame_id,
                            "action_id": action_result.action_id,
                        }
                    )
                    if verdict == "refused":
                        return finish(
                            RESULT_FILTER_REJECTED, verdict_reason, write_drafts=True
                        )
                    return finish(
                        RESULT_ABORT,
                        f"action_error:{verdict_reason}",
                        write_drafts=True,
                    )

                post_frame_id = None
                if action["primitive"] == "wait":
                    # A wait has no visual obligation; the actuator already
                    # held the settle window.
                    verdict = "confirmed"
                else:
                    try:
                        _post_rgb, post_sha, post_frame_id = self._capture()
                    except Exception as exc:  # noqa: BLE001
                        return finish(
                            RESULT_ABORT,
                            f"frame_capture_failed:{type(exc).__name__}",
                            write_drafts=True,
                        )
                    frames.append(self._frame_entry(post_frame_id))
                    if post_sha == pre_sha:
                        # One refuted action fuses the whole exploration.
                        actions_log.append(
                            {
                                "intent": action["intent"],
                                "primitive": action["primitive"],
                                "verdict": "refuted",
                                "reason": "frame_unchanged",
                                "pre_frame_id": pre_frame_id,
                                "post_frame_id": post_frame_id,
                                "action_id": action_result.action_id,
                            }
                        )
                        return finish(
                            RESULT_REFUTED_ABORT,
                            f"action_no_visual_effect:{action['intent']}",
                            write_drafts=True,
                        )
                    verdict = "confirmed"

                # The zero-cost rule, against the authoritative save.  A read
                # that fails here fails closed as a refutation, because the
                # action's cost can no longer be proved.
                try:
                    after = _zero_cost_snapshot(self.read_semantic())
                except Exception:  # noqa: BLE001 - fail closed
                    actions_log.append(
                        {
                            "intent": action["intent"],
                            "primitive": action["primitive"],
                            "verdict": "refuted",
                            "reason": "save_verification_failed",
                            "pre_frame_id": pre_frame_id,
                            "post_frame_id": post_frame_id,
                            "action_id": action_result.action_id,
                        }
                    )
                    return finish(
                        RESULT_REFUTED_ABORT,
                        "save_verification_failed",
                        write_drafts=True,
                    )
                violation = _zero_cost_violation(baseline, after)
                if violation is not None:
                    actions_log.append(
                        {
                            "intent": action["intent"],
                            "primitive": action["primitive"],
                            "verdict": "refuted",
                            "reason": violation,
                            "pre_frame_id": pre_frame_id,
                            "post_frame_id": post_frame_id,
                            "action_id": action_result.action_id,
                        }
                    )
                    return finish(RESULT_REFUTED_ABORT, violation, write_drafts=True)

                actions_log.append(
                    {
                        "intent": action["intent"],
                        "primitive": action["primitive"],
                        "params": dict(action["params"]),
                        "verdict": verdict,
                        "pre_frame_id": pre_frame_id,
                        "post_frame_id": post_frame_id,
                        "action_id": action_result.action_id,
                    }
                )
                self._points.append(
                    {
                        "x": action["x"],
                        "y": action["y"],
                        "kind": action["primitive"],
                        "semantics": action["intent"],
                        "result": verdict,
                        "source": "handler",
                        "pre_frame_id": pre_frame_id,
                        "post_frame_id": post_frame_id,
                    }
                )

            if proposal["done"]:
                return finish(RESULT_EXPLORED_DONE, write_drafts=True)

            # Not done and nothing left refused: the next model round sees the
            # surface the actions left behind.
            try:
                rgb, sha, frame_id = self._capture()
            except Exception as exc:  # noqa: BLE001
                return finish(
                    RESULT_ABORT,
                    f"frame_capture_failed:{type(exc).__name__}",
                    write_drafts=True,
                )
            frames.append(self._frame_entry(frame_id))
            if rgb is not None:
                forbidden = self.registry.detect_forbidden_surface(rgb)
                if forbidden is not None:
                    return finish(
                        RESULT_FILTER_REJECTED,
                        f"forbidden_surface_present:{forbidden.name}",
                        write_drafts=True,
                    )


def make_call_json_caller(
    *,
    model: str,
    max_tokens: int = 8000,
) -> Callable[[dict[str, Any]], Any]:
    """The production model caller, on the standard structured-JSON entry.

    The evidence pack's frames ride as image blocks (webp, base64) ahead of
    the JSON text block; the token meter sees the call under the
    ``game_observatory.rr_agent_handler`` caller label.
    """

    def caller(evidence: dict[str, Any]) -> Any:
        from omnicompany.runtime.llm.structured import call_json

        blocks: list[dict[str, Any]] = []
        for frame in evidence.get("frames", []):
            path = frame.get("path")
            if not isinstance(path, str):
                continue
            candidate = Path(path)
            if not candidate.is_file():
                continue
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/webp",
                        "data": base64.b64encode(candidate.read_bytes()).decode("ascii"),
                    },
                }
            )
        blocks.append(
            {"type": "text", "text": json.dumps(evidence, ensure_ascii=False)}
        )
        return call_json(
            system=SYSTEM_PROMPT,
            user=blocks,
            schema=OUTPUT_SCHEMA,
            model=model,
            caller="game_observatory.rr_agent_handler",
            max_tokens=max_tokens,
        )

    return caller


# --------------------------------------------------------------------------- #
# Kimi Code CLI backend (2026-09-09 ruling)
#
# The k3 direct API key ran into its monthly quota (403), while the local Kimi
# Code CLI authenticates through the managed:kimi-code OAuth and works.  The
# ``kimi-cli`` sentinel routes the handler to this backend instead of the
# structured-JSON runtime; validate/budget/refutation logic in AgentHandler is
# unchanged.
# --------------------------------------------------------------------------- #

# ``--agent-handler-model kimi-cli`` selects this backend; anything else is a
# structured-JSON model id exactly as before.
KIMI_CLI_SENTINEL = "kimi-cli"
# One smoke run of the same prompt shape took about 70 s; the budget leaves
# comfortable headroom without letting a wedged CLI hold a play session.
KIMI_CLI_TIMEOUT_S = 180
# The CLI runs with the omnicompany repo root as its working directory so its
# ReadMediaFile/Read calls resolve inside the workspace; frames are handed
# over as absolute paths, never as base64 in the prompt.
KIMI_CLI_REPO_ROOT = Path(__file__).resolve().parents[6]
_KIMI_RESUME_PREFIX = "To resume this session"


class KimiCliError(RuntimeError):
    """The kimi CLI call failed; the handler maps this to model_call_failed."""


def render_kimi_cli_prompt(evidence: dict[str, Any]) -> str:
    """The deterministic prompt text for one evidence pack.

    Mirrors the 2026-09-09 smoke prompt composition: system prompt, the output
    schema JSON, the absolute frame paths (the CLI reads them itself with
    ReadMediaFile -- frames are never inlined as base64), the save-state
    summary, the registered surface list, the interaction points already
    tried, the remaining budget and the closing contract-only instruction.
    """

    parts = [
        SYSTEM_PROMPT,
        "",
        "输出 JSON schema：",
        json.dumps(OUTPUT_SCHEMA, ensure_ascii=False),
        "",
    ]
    frame_paths = [
        frame["path"]
        for frame in evidence.get("frames", [])
        if isinstance(frame, dict) and isinstance(frame.get("path"), str)
    ]
    if frame_paths:
        parts.append("证据帧（用 ReadMediaFile 读取，按时间顺序，均为绝对路径）：")
        parts.extend(frame_paths)
        parts.append("")
    save = evidence.get("save_state") or {}
    summary_bits = [
        f"{label} {save[key]}"
        for key, label in (
            ("energy", "能量"),
            ("coins", "金币"),
            ("gems", "宝石"),
            ("board_occupied", "棋盘占用"),
        )
        if save.get(key) is not None
    ]
    trigger = evidence.get("trigger") or {}
    registered = [str(name) for name in evidence.get("registered_surfaces") or []]
    tried = evidence.get("interaction_points_tried") or []
    budget = evidence.get("budget") or {}
    parts.append(f"任务：{evidence.get('task', '')}")
    parts.append(
        "触发："
        f"{trigger.get('kind', 'unknown')}"
        + (f"（界面 {trigger['surface']}）" if trigger.get("surface") else "")
    )
    parts.append(f"当前状态摘要：{'、'.join(summary_bits) or '（存档字段缺失）'}。")
    parts.append(f"已登记界面清单：{', '.join(registered) or '（空）'}。")
    parts.append(
        "已尝试交互点（勿重复无效点）："
        + (json.dumps(tried, ensure_ascii=False) if tried else "（无）")
    )
    parts.append(
        "剩余预算：动作 "
        f"{budget.get('actions_left', '?')} 次、"
        f"墙钟 {budget.get('wall_s_left', '?')} 秒。"
    )
    redline = evidence.get("redline_rules")
    if redline:
        parts.append(
            "红线规则（命中即 abort）："
            + json.dumps(redline, ensure_ascii=False)
        )
    parts.extend(
        [
            "",
            "请分析这个未登记界面，只输出契约 JSON（不要输出任何其他文字）。",
        ]
    )
    return "\n".join(str(part) for part in parts)


def extract_last_json_object(text: str) -> dict[str, Any]:
    """The final JSON object in the CLI's stdout.

    The CLI appends a ``To resume this session: ...`` trailer after the
    answer; those lines are stripped first.  Fenced ```json blocks are
    preferred, then a backward scan for the outermost object that reaches
    furthest into the text tolerates leading noise.  Anything else is a
    KimiCliError, which the handler treats with its existing
    schema_invalid/model_call_failed semantics.
    """

    cleaned = "\n".join(
        line
        for line in text.splitlines()
        if not line.strip().startswith(_KIMI_RESUME_PREFIX)
    ).strip()
    if not cleaned:
        raise KimiCliError("kimi CLI output was empty")
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    for block in reversed(re.findall(r"```(?:json)?\s*\n(.*?)```", cleaned, re.DOTALL)):
        try:
            value = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    decoder = json.JSONDecoder()
    best: tuple[int, int, dict[str, Any]] | None = None
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        absolute_end = index + end
        if (
            best is None
            or absolute_end > best[1]
            or (absolute_end == best[1] and index < best[0])
        ):
            best = (index, absolute_end, value)
    if best is None:
        raise KimiCliError("no JSON object found in kimi CLI output")
    return best[2]


def make_kimi_cli_caller(
    *,
    kimi_bin: str = "kimi",
    cwd: Path | str | None = None,
    timeout_s: float = KIMI_CLI_TIMEOUT_S,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    prompt_dir: Path | None = None,
) -> Callable[[dict[str, Any]], Any]:
    """The kimi-CLI model caller, same signature as ``make_call_json_caller``.

    The rendered prompt is staged in a temporary file and the CLI is told to
    Read that file: the ``kimi`` entry point on this host is an npm ``.cmd``
    shim, so a full evidence prompt on the command line would hit cmd.exe's
    8191-character limit.  ``--yolo`` is deliberately absent (it conflicts
    with ``-p`` headless mode on this build).  A non-zero exit, a timeout or
    an unparseable answer raises KimiCliError, which AgentHandler's existing
    ``model_call_failed`` path fuses into ``schema_invalid`` with the drafts
    written -- the play session itself never sees the CLI fail.
    """

    run = runner or subprocess.run
    work_dir = Path(cwd) if cwd is not None else KIMI_CLI_REPO_ROOT

    def caller(evidence: dict[str, Any]) -> Any:
        prompt = render_kimi_cli_prompt(evidence)
        stage = (
            Path(tempfile.mkdtemp(prefix="rr-agent-handler-kimi-", dir=prompt_dir))
            if prompt_dir is not None
            else Path(tempfile.mkdtemp(prefix="rr-agent-handler-kimi-"))
        )
        prompt_path = stage / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        instruction = (
            f"任务说明在文件 {prompt_path} 中。请先用 Read 工具完整读取该文件，"
            "然后严格按文件内容执行任务。最终回答只输出文件要求的契约 JSON，"
            "不要输出任何其他文字。"
        )
        resolved = shutil.which(kimi_bin) or kimi_bin
        try:
            # encoding must be pinned: on a zh-CN Windows host text=True
            # defaults to GBK and the stdout reader thread dies on the CLI's
            # UTF-8 bytes, which then surfaces as a bogus "output was empty".
            completed = run(
                [resolved, "-p", instruction],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise KimiCliError(
                f"kimi CLI timed out after {timeout_s}s"
            ) from exc
        except OSError as exc:
            raise KimiCliError(f"kimi CLI failed to start: {exc}") from exc
        if completed.returncode != 0:
            stderr_tail = (completed.stderr or "").strip()[-500:]
            raise KimiCliError(
                f"kimi CLI exited {completed.returncode}: {stderr_tail}"
            )
        return extract_last_json_object(completed.stdout or "")

    return caller


def make_model_caller(
    model: str,
    **kimi_kwargs: Any,
) -> tuple[Callable[[dict[str, Any]], Any] | None, str]:
    """Route ``--agent-handler-model``: the kimi-cli sentinel runs the local
    Kimi Code CLI backend, any other value is a structured-JSON model id, and
    an empty value is no model (the handler's fail-closed configuration fact).
    """

    if model == KIMI_CLI_SENTINEL:
        return make_kimi_cli_caller(**kimi_kwargs), model
    if model:
        return make_call_json_caller(model=model), model
    return None, ""


__all__ = [
    "AgentHandler",
    "DEFAULT_GESTURE_MS",
    "DEFAULT_WAIT_MS",
    "EVIDENCE_FRAME_LIMIT",
    "HANDLER_RESULTS",
    "INTENT_PREFIX",
    "KIMI_CLI_REPO_ROOT",
    "KIMI_CLI_SENTINEL",
    "KIMI_CLI_TIMEOUT_S",
    "KimiCliError",
    "MAX_EXPLORATION_ACTIONS",
    "MAX_EXPLORATION_WALL_S",
    "OUTPUT_SCHEMA",
    "RESULT_ABORT",
    "RESULT_BUDGET_EXHAUSTED",
    "RESULT_EXPLORED_DONE",
    "RESULT_FILTER_REJECTED",
    "RESULT_REFUTED_ABORT",
    "RESULT_SCHEMA_INVALID",
    "SYSTEM_PROMPT",
    "extract_last_json_object",
    "make_call_json_caller",
    "make_kimi_cli_caller",
    "make_model_caller",
    "render_kimi_cli_prompt",
    "validate_proposal",
]
