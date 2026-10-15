"""Evidence-bound interface exploration graph for a live game session.

The recorder deliberately keeps the observation model small.  A frame gives us a
screen identity and a visual observation; an action can connect the frame before
the action to the frame after it.  It never turns a tap coordinate into a button
box.  A coordinate is retained as an ``observed-point`` element until a later
source-backed element resolver supplies a real element identity.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


SCHEMA = "game-observatory.interface-map.v1"
PATH_SCHEMA = "game-observatory.interface-map.path.v1"
MAIN_ENTITY_ID = "main-board"
RESTAURANT_SCENE_ID = "restaurant-scene"
PROJECTION_OBSERVATION_LIMIT = 64
PERCEPTUAL_HASH_DISTANCE = 16
ATOMIC_REPLACE_PERMISSION_RETRIES = 3
ATOMIC_REPLACE_PERMISSION_DELAY_S = 0.01
_UNKNOWN_SCREEN = re.compile(r"^(?:unknown|unrecognised|unrecognized|provisional|unknown[:/])", re.I)
_DISPLAY_TITLES = {
    "main-board": "餐厅主界面",
    "restaurant-scene": "浪漫餐厅",
    "main-task-panel": "任务面板",
    "story-dialogue": "剧情对话",
    "decoration-choice": "装饰选择",
    "decoration-confirmation": "装饰确认",
    "energy-supply-panel": "体力补给",
    "energy-depleted-guide": "体力不足提示",
    "warehouse-price-panel": "仓库价格",
    "daily-rewards": "每日奖励",
    "reward-panel": "奖励面板",
    "level-up-reward": "升级奖励",
    "board-story-prompt": "主界面剧情提示",
    "shop-daily-specials-and-hot-selling": "商店",
    "story-cutscene": "剧情演出",
    "rave-reviews-event-intro": "好评活动介绍",
    "rave-reviews-complete-panel": "好评活动完成",
    "store-rating-prompt": "应用商店评价",
    "energy-party-announcement-fullscreen": "体力活动公告",
    "tutorial-overlay": "新手提示",
    "returning-player-gift": "回归礼物",
    "newbie-gift": "新手礼物",
    "newbie-gift-progress-modal": "新手礼物进度",
    "update-game-modal": "更新提示",
    "save-load-login": "登录提示",
    "loading-splash": "加载画面",
}


def _replace_with_permission_retry(source: str, destination: Path) -> None:
    """Retry transient Windows sharing locks, while preserving other failures."""

    for attempt in range(ATOMIC_REPLACE_PERMISSION_RETRIES):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt + 1 == ATOMIC_REPLACE_PERMISSION_RETRIES:
                raise
            time.sleep(ATOMIC_REPLACE_PERMISSION_DELAY_S)


def _open_append_with_permission_retry(path: Path):
    """Open the append-only ledger, retrying only transient sharing locks."""

    for attempt in range(ATOMIC_REPLACE_PERMISSION_RETRIES):
        try:
            return path.open("a", encoding="utf-8", buffering=1)
        except PermissionError:
            if attempt + 1 == ATOMIC_REPLACE_PERMISSION_RETRIES:
                raise
            time.sleep(ATOMIC_REPLACE_PERMISSION_DELAY_S)


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{field} must be a finite number")
    return number


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


class InterfaceMap:
    """Persist a session's evidence-backed interface graph.

    ``root`` is a session directory supplied by the runtime.  The class never
    copies screenshots; ``frame['path']`` remains a link in the JSON and NDJSON
    records.  The map JSON is replaced atomically after every accepted frame.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.map_path = self.root / "interface-map.json"
        self.observations_path = self.root / "observations.ndjson"
        self._lock = threading.RLock()
        self._repaired_transition_ids = False
        self._document = self._load()
        if self._repaired_transition_ids:
            self._atomic_write()
        self._observation_by_frame = {
            item["frame_id"]: item for item in self._document["observations"]
        }
        self._ledger_frame_by_id: dict[str, dict[str, Any]] = {}
        self._ledger_index_loaded = False
        self._state_by_key = {
            (item["entity_id"], item.get("semantic_signature"), item.get("perceptual_hash"), item["visual_hash"]): item
            for item in self._document["states"]
        }
        self._transition_keys = {
            self._transition_key(item): item for item in self._document["transitions"]
        }

    def _empty(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "version": 1,
            "main_entity_id": MAIN_ENTITY_ID,
            "entities": [],
            "states": [],
            "observations": [],
            "elements": [],
            "transitions": [],
            "view": {
                "default_state_ids": {},
                "recent_observation_limit": PROJECTION_OBSERVATION_LIMIT,
            },
        }

    def _load(self) -> dict[str, Any]:
        if not self.map_path.exists():
            return self._empty()
        try:
            value = json.loads(self.map_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid interface map: {self.map_path}") from exc
        if not isinstance(value, dict) or value.get("schema") != SCHEMA:
            raise ValueError(f"unsupported interface map schema: {value.get('schema')!r}")
        document = self._empty()
        for key in ("entities", "states", "observations", "elements", "transitions"):
            records = value.get(key, [])
            if not isinstance(records, list):
                raise ValueError(f"interface map {key} must be an array")
            document[key] = records
        document["main_entity_id"] = value.get("main_entity_id", MAIN_ENTITY_ID)
        document["view"] = value.get("view", document["view"])
        document["view"].setdefault("recent_observation_limit", PROJECTION_OBSERVATION_LIMIT)
        self._repaired_transition_ids = self._repair_duplicate_transition_ids(document["transitions"])
        self._validate_document(document)
        return document

    @staticmethod
    def _repair_duplicate_transition_ids(transitions: list[dict[str, Any]]) -> bool:
        """Give legacy duplicate rows stable IDs without dropping their evidence."""

        seen: set[str] = set()
        repaired = False
        for index, transition in enumerate(transitions):
            transition_id = transition.get("id")
            if transition_id not in seen:
                seen.add(transition_id)
                continue
            row = json.dumps(
                transition,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            digest = hashlib.sha256(row.encode("utf-8")).hexdigest()[:16]
            candidate = f"transition-repaired-{digest}"
            if candidate in seen:
                candidate = f"{candidate}-{index}"
                while candidate in seen:
                    candidate = f"{candidate}-{index + 1}"
            transition["id"] = candidate
            seen.add(candidate)
            repaired = True
        return repaired

    @staticmethod
    def _validate_document(document: dict[str, Any]) -> None:
        ids: dict[str, set[str]] = {}
        for kind in ("entities", "states", "observations", "elements", "transitions"):
            ids[kind] = set()
            for record in document[kind]:
                if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                    raise ValueError(f"interface map {kind} contains an invalid record")
                if record["id"] in ids[kind]:
                    raise ValueError(f"duplicate {kind} id: {record['id']}")
                ids[kind].add(record["id"])
        entity_ids = ids["entities"]
        state_ids = ids["states"]
        observation_ids = ids["observations"]
        element_ids = ids["elements"]
        for state in document["states"]:
            if state.get("entity_id") not in entity_ids:
                raise ValueError(f"state endpoint missing: {state['id']}")
        for observation in document["observations"]:
            if observation.get("state_id") not in state_ids:
                raise ValueError(f"observation state endpoint missing: {observation['id']}")
            for element_id in observation.get("element_ids", []):
                if element_id not in element_ids:
                    raise ValueError(f"observation element endpoint missing: {element_id}")
        for transition in document["transitions"]:
            if transition.get("source_state_id") not in state_ids or transition.get("target_state_id") not in state_ids:
                raise ValueError(f"transition state endpoint missing: {transition['id']}")
            if transition.get("element_id") and transition["element_id"] not in element_ids:
                raise ValueError(f"transition element endpoint missing: {transition['element_id']}")

    @staticmethod
    def _frame(frame: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(frame, dict):
            raise TypeError("frame must be a dict")
        required = ("seq", "mono_ns", "wall_ns", "path", "width", "height", "screen_id", "screen_confidence", "visual_hash")
        missing = [field for field in required if field not in frame]
        if missing:
            raise ValueError(f"frame missing required field(s): {', '.join(missing)}")
        seq = frame["seq"]
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise ValueError("seq must be a non-negative integer")
        mono_ns = frame["mono_ns"]
        wall_ns = frame["wall_ns"]
        if isinstance(mono_ns, bool) or not isinstance(mono_ns, int) or mono_ns < 0:
            raise ValueError("mono_ns must be a non-negative integer")
        if isinstance(wall_ns, bool) or not isinstance(wall_ns, int) or wall_ns < 0:
            raise ValueError("wall_ns must be a non-negative integer")
        pts_us = frame.get("pts_us")
        if pts_us is not None and (
            isinstance(pts_us, bool) or not isinstance(pts_us, int) or pts_us < 0
        ):
            raise ValueError("pts_us must be a non-negative integer")
        width, height = frame["width"], frame["height"]
        if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
            raise ValueError("width must be a positive integer")
        if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
            raise ValueError("height must be a positive integer")
        confidence = _finite_number(frame["screen_confidence"], "screen_confidence")
        if not 0 <= confidence <= 1:
            raise ValueError("screen_confidence must be between 0 and 1")
        screen_id = _non_empty_text(frame["screen_id"], "screen_id")
        visual_hash = _non_empty_text(frame["visual_hash"], "visual_hash")
        path = frame["path"]
        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise ValueError("path must be a non-empty screenshot link")
        supplied_frame_id = frame.get("id", frame.get("frame_id"))
        frame_id = f"frame-{seq}" if supplied_frame_id is None else _non_empty_text(supplied_frame_id, "frame.id")
        session_id = frame.get("session_id")
        if session_id is not None:
            session_id = _non_empty_text(session_id, "session_id")
        perceptual_hash = frame.get("perceptual_hash")
        if perceptual_hash is not None:
            perceptual_hash = _non_empty_text(perceptual_hash, "perceptual_hash")
        semantic_signature = frame.get("semantic_signature")
        if semantic_signature is not None:
            if isinstance(semantic_signature, str):
                semantic_signature = semantic_signature.strip()
                if not semantic_signature:
                    raise ValueError("semantic_signature must not be empty")
            else:
                try:
                    semantic_signature = json.dumps(semantic_signature, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("semantic_signature must be JSON serializable") from exc
        normalized = {
            "frame_id": frame_id,
            "session_id": session_id,
            "seq": seq,
            "mono_ns": mono_ns,
            "wall_ns": wall_ns,
            "path": str(path),
            "width": width,
            "height": height,
            "screen_id": screen_id,
            "screen_confidence": confidence,
            "visual_hash": visual_hash,
            "perceptual_hash": perceptual_hash,
            "semantic_signature": semantic_signature,
        }
        if pts_us is not None:
            normalized["pts_us"] = pts_us
        if isinstance(frame.get("frontier"), dict):
            frontier = frame["frontier"]
            if not isinstance(frontier.get("source_frame_id"), str) or not isinstance(frontier.get("candidates"), list):
                raise ValueError("frontier must retain its source frame and candidates")
            if len(frontier["candidates"]) > 128:
                raise ValueError("too many frontier candidates")
            normalized["frontier"] = _json_copy(frontier)
        return normalized

    @staticmethod
    def _action(action: dict[str, Any] | None) -> dict[str, Any] | None:
        if action is None:
            return None
        if not isinstance(action, dict):
            raise TypeError("action must be a dict or None")
        required = ("event_id", "kind", "source", "pre_frame_id", "pre_screen_id", "seq", "intent", "overlapping")
        missing = [field for field in required if field not in action]
        if missing:
            raise ValueError(f"action missing required field(s): {', '.join(missing)}")
        event_id = _non_empty_text(action["event_id"], "action.event_id")
        kind = _non_empty_text(action["kind"], "action.kind")
        source = _non_empty_text(action["source"], "action.source")
        pre_screen_id = _non_empty_text(action["pre_screen_id"], "action.pre_screen_id")
        pre_frame_id = action["pre_frame_id"]
        if pre_frame_id is not None:
            pre_frame_id = _non_empty_text(pre_frame_id, "action.pre_frame_id")
        seq = action["seq"]
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise ValueError("action.seq must be a non-negative integer")
        intent = action["intent"]
        if intent is not None and not isinstance(intent, str):
            raise ValueError("action.intent must be text or None")
        overlapping = action["overlapping"]
        if not isinstance(overlapping, bool):
            raise ValueError("action.overlapping must be boolean")
        normalized: dict[str, Any] = {
            "event_id": event_id,
            "kind": kind,
            "source": source,
            "pre_frame_id": pre_frame_id,
            "pre_screen_id": pre_screen_id,
            "seq": seq,
            "intent": intent.strip() if isinstance(intent, str) else None,
            "overlapping": overlapping,
        }
        for field in ("nx", "ny"):
            if field in action and action[field] is not None:
                number = _finite_number(action[field], f"action.{field}")
                if not 0 <= number <= 1:
                    raise ValueError(f"action.{field} must be between 0 and 1")
                normalized[field] = number
        return normalized

    @staticmethod
    def _provisional(screen_id: str, confidence: float) -> bool:
        if screen_id == MAIN_ENTITY_ID:
            return False
        return confidence < 0.5 or bool(_UNKNOWN_SCREEN.match(screen_id))

    @staticmethod
    def _state_id(entity_id: str, visual_hash: str) -> str:
        digest = hashlib.sha256(visual_hash.encode("utf-8")).hexdigest()[:16]
        return f"{entity_id}:state:{digest}"

    @staticmethod
    def _hash_distance(left: str | None, right: str | None) -> int | None:
        if not left or not right:
            return None
        if len(left) != len(right):
            return None
        try:
            return sum((int(a, 16) ^ int(b, 16)).bit_count() for a, b in zip(left, right))
        except ValueError:
            return sum(a != b for a, b in zip(left, right))

    @classmethod
    def _state_matches(cls, state: dict[str, Any], frame: dict[str, Any]) -> bool:
        semantic = frame.get("semantic_signature")
        if semantic is not None and state.get("semantic_signature") != semantic:
            return False
        if semantic is None and state.get("semantic_signature") is not None:
            return False
        left, right = state.get("perceptual_hash"), frame.get("perceptual_hash")
        distance = cls._hash_distance(left, right)
        if distance is not None:
            return distance <= PERCEPTUAL_HASH_DISTANCE
        return state.get("visual_hash") == frame.get("visual_hash")

    @staticmethod
    def _transition_key(transition: dict[str, Any]) -> tuple[Any, ...]:
        point = transition.get("observed_point") or {}
        return (
            transition.get("source_state_id"), transition.get("target_state_id"),
            transition.get("mode"), transition.get("trigger"),
            point.get("nx"), point.get("ny"),
        )

    @classmethod
    def _transition_id(cls, transition: dict[str, Any]) -> str:
        """Derive an ID from the complete edge key, including observed point."""

        source, target, mode, trigger, nx, ny = cls._transition_key(transition)
        transition_seed = json.dumps(
            [source, target, mode, trigger, nx, ny],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"transition-{hashlib.sha256(transition_seed.encode('utf-8')).hexdigest()[:16]}"

    def _atomic_write(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            self._document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        ) + "\n"
        fd, temporary = tempfile.mkstemp(prefix="interface-map.", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_with_permission_retry(temporary, self.map_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _append_ledger(self, frame: dict[str, Any], action: dict[str, Any] | None, *, duplicate: bool = False) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        record = {"frame": frame, "action": action}
        if duplicate:
            record["duplicate"] = True
        with _open_append_with_permission_retry(self.observations_path) as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._ledger_frame_by_id[frame["frame_id"]] = frame

    def _find_state(self, entity_id: str, frame: dict[str, Any]) -> dict[str, Any] | None:
        for state in reversed(self._document["states"]):
            if state["entity_id"] == entity_id and self._state_matches(state, frame):
                return state
        return None

    def _ensure_ledger_index(self) -> None:
        if self._ledger_index_loaded:
            return
        self._ledger_index_loaded = True
        if not self.observations_path.exists():
            return
        try:
            lines = self.observations_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw_frame = record.get("frame") if isinstance(record, dict) else None
            if isinstance(raw_frame, dict) and raw_frame.get("frame_id"):
                self._ledger_frame_by_id[raw_frame["frame_id"]] = raw_frame

    def _find_ledger_observation(self, frame_id: str) -> dict[str, Any] | None:
        projected = self._observation_by_frame.get(frame_id)
        if projected is not None:
            return projected
        self._ensure_ledger_index()
        raw_frame = self._ledger_frame_by_id.get(frame_id)
        if raw_frame is not None:
            try:
                state = self._find_state(raw_frame["screen_id"], raw_frame)
                if state is None:
                    return None
                return {
                    "id": frame_id,
                    "frame_id": frame_id,
                    "entity_id": raw_frame["screen_id"],
                    "state_id": state["id"],
                    "seq": raw_frame["seq"],
                    "screen_id": raw_frame["screen_id"],
                    "session_id": raw_frame.get("session_id"),
                    "element_ids": [],
                }
            except KeyError:
                return None
        return None

    def _trim_projection(self) -> None:
        limit = int(self._document["view"].get("recent_observation_limit", PROJECTION_OBSERVATION_LIMIT))
        observations_by_entity: dict[str, list[dict[str, Any]]] = {}
        for item in self._document["observations"]:
            observations_by_entity.setdefault(item["entity_id"], []).append(item)
        retained: set[str] = set()
        for entity in self._document["entities"]:
            recent = observations_by_entity.get(entity["id"], [])[-limit:]
            entity["observation_ids"] = [item["id"] for item in recent]
            retained.update(entity["observation_ids"])
        self._document["observations"] = [item for item in self._document["observations"] if item["id"] in retained]
        self._observation_by_frame = {item["frame_id"]: item for item in self._document["observations"]}
        observations_by_state: dict[str, list[str]] = {}
        for item in self._document["observations"]:
            observations_by_state.setdefault(item["state_id"], []).append(item["id"])
        for state in self._document["states"]:
            state["observation_ids"] = observations_by_state.get(state["id"], [])[-limit:]

    def _ensure_observed_point(self, entity_id: str, action: dict[str, Any] | None) -> list[str]:
        if not action or "nx" not in action or "ny" not in action:
            return []
        element_id = f"{entity_id}:observed-point:{action['event_id']}"
        element = {
            "id": element_id,
            "entity_id": entity_id,
            "kind": "observed-point",
            "source": action["source"],
            "event_id": action["event_id"],
            "point": {"coordinate_space": "normalized", "nx": action["nx"], "ny": action["ny"]},
        }
        if not any(item["id"] == element_id for item in self._document["elements"]):
            self._document["elements"].append(element)
        return [element_id]

    def _previous_for(self, current: dict[str, Any], action: dict[str, Any] | None) -> dict[str, Any] | None:
        if action and action.get("pre_frame_id"):
            return self._find_ledger_observation(action["pre_frame_id"])
        if action is None:
            session_id = current.get("session_id")
            prior = [
                item for item in self._document["observations"]
                if item["seq"] < current["seq"]
                and (session_id is None or item.get("session_id") == session_id)
            ]
            return max(prior, key=lambda item: item["seq"], default=None)
        return None

    def _add_transition(self, previous: dict[str, Any], current: dict[str, Any], action: dict[str, Any] | None, element_ids: list[str]) -> None:
        if previous["state_id"] == current["state_id"] or previous["entity_id"] == current["entity_id"]:
            return
        mode = "automatic" if action is None else ("observed-gap" if action["overlapping"] else "interactive")
        trigger = "系统状态变化" if action is None else (action["intent"] or action["kind"])
        transition: dict[str, Any] = {
            "id": "",
            "source_entity_id": previous["entity_id"],
            "target_entity_id": current["entity_id"],
            "source_state_id": previous["state_id"],
            "target_state_id": current["state_id"],
            "mode": mode,
            "trigger": trigger,
            "outcome": "unknown" if mode in {"automatic", "observed-gap"} else "success",
            "evidence": {"pre_frame_id": previous["frame_id"], "post_frame_id": current["frame_id"]},
        }
        if element_ids:
            transition["element_id"] = element_ids[0]
            transition["observed_point"] = {"nx": action["nx"], "ny": action["ny"]}
        transition["id"] = self._transition_id(transition)
        key = self._transition_key(transition)
        existing = self._transition_keys.get(key)
        if existing is None and action is not None:
            # A frame can arrive first from the interval sampler and later
            # again with the action that explains the same landing.  Upgrade
            # that evidence in place instead of making two parallel edges.
            for candidate in self._document["transitions"]:
                if (
                    candidate["source_state_id"] == transition["source_state_id"]
                    and candidate["target_state_id"] == transition["target_state_id"]
                    and candidate.get("mode") == "automatic"
                ):
                    self._transition_keys.pop(self._transition_key(candidate), None)
                    candidate.update({
                        "mode": transition["mode"],
                        "trigger": transition["trigger"],
                        "outcome": transition["outcome"],
                    })
                    candidate["evidence"] = transition["evidence"]
                    if element_ids:
                        candidate["element_id"] = element_ids[0]
                        candidate["observed_point"] = transition["observed_point"]
                    # 2026-09-17: the id is derived from the key, and the key
                    # just changed.  Keeping the automatic-key id let the next
                    # automatic edge between the same two states derive the
                    # same id; snapshot() then refused the duplicate and the map
                    # page stopped loading until a restart repaired the file.
                    candidate["id"] = self._transition_id(candidate)
                    self._transition_keys[self._transition_key(candidate)] = candidate
                    return
        if existing is None:
            self._document["transitions"].append(transition)
            self._transition_keys[key] = transition
        elif current["frame_id"] not in existing.setdefault("evidence", {}).setdefault("additional_post_frame_ids", []):
            existing["evidence"]["additional_post_frame_ids"].append(current["frame_id"])

    def _observe(self, frame: dict[str, Any], action: dict[str, Any] | None = None) -> None:
        current = self._frame(frame)
        normalized_action = self._action(action)
        entity_id = current["screen_id"]
        existing_observation = self._find_ledger_observation(current["frame_id"])
        if existing_observation is not None:
            point_entity_id = normalized_action.get("pre_screen_id", entity_id) if normalized_action else entity_id
            element_ids = self._ensure_observed_point(point_entity_id, normalized_action)
            if current["frame_id"] in self._observation_by_frame:
                for element_id in element_ids:
                    if element_id not in existing_observation.setdefault("element_ids", []):
                        existing_observation["element_ids"].append(element_id)
            previous = self._previous_for(current, normalized_action)
            if previous is not None and normalized_action is not None and previous["screen_id"] != normalized_action["pre_screen_id"]:
                previous = None
            if previous is not None:
                self._add_transition(previous, existing_observation, normalized_action, element_ids)
            self._atomic_write()
            self._append_ledger(current, normalized_action, duplicate=True)
            return
        entities = {item["id"]: item for item in self._document["entities"]}
        entity = entities.get(entity_id)
        provisional = self._provisional(entity_id, current["screen_confidence"])
        if entity is None:
            entity = {
                "id": entity_id,
                "source_screen_id": entity_id,
                "title": _DISPLAY_TITLES.get(entity_id, "未识别界面" if provisional else "已识别界面"),
                "provisional": provisional,
                "state_ids": [],
                "observation_ids": [],
                "is_main": entity_id == MAIN_ENTITY_ID,
            }
            self._document["entities"].append(entity)
        elif not provisional and entity.get("provisional"):
            entity["provisional"] = False
            if entity.get("title") == "未识别界面":
                entity["title"] = _DISPLAY_TITLES.get(entity_id, "已识别界面")

        state_key = (entity_id, current.get("semantic_signature"), current.get("perceptual_hash"), current["visual_hash"])
        state = self._find_state(entity_id, current)
        if state is None:
            state_id = self._state_id(entity_id, json.dumps(state_key, ensure_ascii=False, sort_keys=True))
            state = {
                "id": state_id,
                "entity_id": entity_id,
                "semantic_key": current.get("semantic_signature") or current["visual_hash"],
                "visual_hash": current["visual_hash"],
                "perceptual_hash": current.get("perceptual_hash"),
                "semantic_signature": current.get("semantic_signature"),
                "observation_ids": [],
            }
            self._state_by_key[state_key] = state
            self._document["states"].append(state)
            entity["state_ids"].append(state_id)
        observation_id = current["frame_id"]
        if observation_id in self._observation_by_frame:
            raise ValueError(f"duplicate frame seq: {current['seq']}")
        point_entity_id = normalized_action.get("pre_screen_id", entity_id) if normalized_action else entity_id
        element_ids = self._ensure_observed_point(point_entity_id, normalized_action)
        observation = {
            "id": observation_id,
            "frame_id": observation_id,
            "entity_id": entity_id,
            "state_id": state["id"],
            "seq": current["seq"],
            "mono_ns": current["mono_ns"],
            "wall_ns": current["wall_ns"],
            "path": current["path"],
            "width": current["width"],
            "height": current["height"],
            "screen_id": entity_id,
            "session_id": current.get("session_id"),
            "screen_confidence": current["screen_confidence"],
            "visual_hash": current["visual_hash"],
            "perceptual_hash": current.get("perceptual_hash"),
            "semantic_signature": current.get("semantic_signature"),
            "element_ids": element_ids,
        }
        if current.get("pts_us") is not None:
            observation["pts_us"] = current["pts_us"]
        if "frontier" in current:
            observation["frontier"] = current["frontier"]
        self._document["observations"].append(observation)
        self._observation_by_frame[observation_id] = observation
        state["observation_ids"].append(observation_id)
        entity["observation_ids"].append(observation_id)
        self._document["view"]["default_state_ids"][entity_id] = state["id"]

        previous = self._previous_for(current, normalized_action)
        if previous is not None and normalized_action is not None and previous["screen_id"] != normalized_action["pre_screen_id"]:
            previous = None
        if previous is not None:
            self._add_transition(previous, observation, normalized_action, element_ids)
        self._trim_projection()
        self._atomic_write()
        self._append_ledger(current, normalized_action)

    def observe(self, frame: dict[str, Any], action: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._observe(frame, action)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result = copy.deepcopy(self._document)
            self._validate_document(result)
            return result

    def observations(self, offset: int = 0, limit: int = 100, entity_id: str | None = None) -> dict[str, Any]:
        """Read the complete append-only observation ledger in bounded pages."""
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if entity_id is not None:
            entity_id = _non_empty_text(entity_id, "entity_id")
        with self._lock:
            records: list[dict[str, Any]] = []
            if self.observations_path.exists():
                for line in self.observations_path.read_text(encoding="utf-8").splitlines():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    frame = record.get("frame", {})
                    if entity_id is None or frame.get("screen_id") == entity_id:
                        records.append(record)
            return {
                "schema": "game-observatory.interface-map.observations.v1",
                "offset": offset,
                "limit": limit,
                "entity_id": entity_id,
                "total": len(records),
                "items": records[offset:offset + limit],
            }

    def observation_by_id(self, frame_id: str) -> dict[str, Any] | None:
        """Return a copy of one complete raw frame from the append-only ledger."""

        frame_id = _non_empty_text(frame_id, "frame_id")
        with self._lock:
            self._ensure_ledger_index()
            frame = self._ledger_frame_by_id.get(frame_id)
            return copy.deepcopy(frame) if frame is not None else None

    def path(self, target_entity_id: str, from_entity_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            return self._path(target_entity_id, from_entity_id)

    def _path(self, target_entity_id: str, from_entity_id: str | None = None) -> dict[str, Any]:
        target = _non_empty_text(target_entity_id, "target_entity_id")
        start = from_entity_id or self._document["main_entity_id"]
        entities = {item["id"] for item in self._document["entities"]}
        base = {"schema": PATH_SCHEMA, "from_entity_id": start, "target_entity_id": target}
        if start not in entities:
            return {**base, "reachable": False, "reason": "unknown_source", "entity_ids": [], "state_ids": [], "transition_ids": []}
        if target not in entities:
            return {**base, "reachable": False, "reason": "unknown_target", "entity_ids": [], "state_ids": [], "transition_ids": []}
        if start == target:
            return {**base, "reachable": True, "entity_ids": [start], "state_ids": [], "transition_ids": []}
        outgoing: dict[str, list[dict[str, Any]]] = {}
        for transition in self._document["transitions"]:
            if transition.get("mode") != "interactive":
                continue
            outgoing.setdefault(transition["source_entity_id"], []).append(transition)
        queue = [start]
        previous: dict[str, tuple[str, dict[str, Any]]] = {}
        seen = {start}
        while queue:
            current = queue.pop(0)
            for transition in outgoing.get(current, []):
                child = transition["target_entity_id"]
                if child in seen:
                    continue
                seen.add(child)
                previous[child] = (current, transition)
                if child == target:
                    queue.clear()
                    break
                queue.append(child)
        if target not in previous:
            return {**base, "reachable": False, "reason": "unreachable", "entity_ids": [], "state_ids": [], "transition_ids": []}
        entity_ids = [target]
        state_ids: list[str] = []
        transition_ids: list[str] = []
        cursor = target
        while cursor != start:
            parent, transition = previous[cursor]
            entity_ids.append(parent)
            state_ids.extend([transition["source_state_id"], transition["target_state_id"]])
            transition_ids.append(transition["id"])
            cursor = parent
        entity_ids.reverse()
        state_ids.reverse()
        transition_ids.reverse()
        return {**base, "reachable": True, "entity_ids": entity_ids, "state_ids": list(dict.fromkeys(state_ids)), "transition_ids": transition_ids}


__all__ = ["InterfaceMap", "MAIN_ENTITY_ID", "PATH_SCHEMA", "RESTAURANT_SCENE_ID", "SCHEMA"]
