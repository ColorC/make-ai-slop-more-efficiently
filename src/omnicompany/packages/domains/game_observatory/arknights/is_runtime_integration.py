# [OMNI] origin=codex intent="resident reviewed Maa IS semantic integration"
"""Resident semantic IS integration (W4-F).

This module is deliberately small: it is the only place that joins the reviewed
recognizer, the single-channel screen adapter, and policy decisions.  Public
callers receive shell public views; private geometry remains in this object.
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .is_policy import RouteNodeCandidate, RoutePlan, validate_run_metrics
from .is_shell import ISActionKind, ISInteractionDriver, ISObservationV1, SemanticISActionV1
from .maa_is_interaction_assets import (
    InteractionAssetBundleV1, compile_maa_is_interaction_assets,
)
from .maa_is_runtime import (
    DirectScreenIO,
    MAAISRecognizer,
    MaaCoreRecognitionEngine,
    compile_recognition_overlay,
    maa_custom_swipe_invoker,
)

PINNED_SOURCE_COMMIT = "ddca2c8579504992e2c06551b96ce048101f6d8b"
PINNED_CLIENT_VERSION = "2.7.61"
MAX_JSON_BYTES = 64 * 1024


class SemanticIntegrationError(ValueError):
    pass


class FsyncLedger:
    """Append-only JSONL sink whose acknowledgement follows fsync."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def __call__(self, fact: Mapping[str, object]) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(dict(fact), ensure_ascii=False, sort_keys=True) + "\n").encode()
        with self.path.open("ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        return True


def build_resident_runtime(
    *,
    resource_root: Path | str,
    overlay_root: Path | str,
    ledger_path: Path | str,
    operator_factory: Callable[[Path], Any],
    internal_only_bypass: bool = False,
    bundle_factory: Callable[..., InteractionAssetBundleV1] | None = None,
    recognizer_factory: Callable[..., MAAISRecognizer] = MAAISRecognizer,
    engine_factory: Callable[..., Any] = MaaCoreRecognitionEngine,
    io_factory: Callable[..., DirectScreenIO] = DirectScreenIO,
    driver_factory: Callable[[DirectScreenIO], ISInteractionDriver] = ISInteractionDriver,
) -> "ResidentSemanticRuntime":
    """Compile resources before constructing the one resident operator.

    Factories are intentionally injectable, allowing offline tests to prove
    identity and failure behavior without importing/loading native MaaCore.
    """
    compiler = bundle_factory or compile_maa_is_interaction_assets
    bundle = compiler(
        resource_root,
        source_commit=PINNED_SOURCE_COMMIT,
        target_theme="Sami",
        target_client_version=PINNED_CLIENT_VERSION,
    )
    ledger = FsyncLedger(ledger_path)
    overlay = compile_recognition_overlay(bundle, Path(overlay_root) / bundle.content_sha256)
    operator = operator_factory(overlay.root)
    recognizer = recognizer_factory(
        bundle,
        engine_factory(operator, overlay),
        internal_only_bypass=internal_only_bypass,
        ledger_sink=ledger,
    )
    io = io_factory(operator, recognizer, maa_custom_swipe_invoker(operator))
    driver = driver_factory(io)
    return ResidentSemanticRuntime(operator, recognizer, io, driver)


class ResidentSemanticRuntime:
    def __init__(
        self,
        operator: Any,
        recognizer: MAAISRecognizer,
        io: DirectScreenIO,
        driver: ISInteractionDriver,
    ):
        self.operator = operator
        self.recognizer = recognizer
        self.io = io
        self.driver = driver
        self._private: dict[tuple[str, str], ISObservationV1] = {}
        self._lock = threading.RLock()

    def observe(self, root: Path | str, frame: bytes) -> dict[str, object]:
        observation = self.recognizer.observe(frame)
        key = (_root_key(root), observation.frame_sha256)
        with self._lock:
            self._private[key] = observation
        return _public(observation.public_view())

    def act(self, *, root: str, target_id: str, action: str,
            expected_frame: str) -> dict[str, object]:
        try:
            semantic = ISActionKind(action)
            request = SemanticISActionV1(semantic, target_id, expected_frame)
        except ValueError as exc:
            raise SemanticIntegrationError("unknown action") from exc
        key = (_root_key(root), expected_frame)
        with self._lock:
            observation = self._private.get(key)
            if observation is None:
                raise SemanticIntegrationError("unknown, stale, or consumed frame")
            matches = [
                target
                for target in observation.targets
                if target.target_id == target_id and target.action is semantic and target.enabled
            ]
            if len(matches) != 1:
                raise SemanticIntegrationError("target is absent or does not support requested action")
            # At-most-once: consume before the first live-frame check or input call.
            del self._private[key]
        receipt = self.driver.execute(observation, request)
        return {"ok": True, "receipt": _receipt(receipt)}


def _root_key(root: Path | str) -> str:
    try:
        return str(Path(root).resolve(strict=False))
    except (OSError, TypeError, ValueError) as exc:
        raise SemanticIntegrationError("invalid facility root") from exc


def _public(value: Any) -> dict[str, object]:
    result = {"schema": value.schema, "screen": value.screen.value,
              "frame_sha256": value.frame_sha256,
              "visible_text": list(value.visible_text),
              "legal_targets": []}
    result["legal_targets"] = [{"target_id": t.target_id, "action": t.action.value,
                                "label": t.label, "enabled": t.enabled}
                               for t in value.legal_targets]
    if value.observation_id is not None:
        result["observation_id"] = value.observation_id
    return result


def _receipt(value: Any) -> dict[str, object]:
    return {"schema": value.schema, "sequence": value.sequence,
            "action": value.action.value, "target_id": value.target_id,
            "gesture": value.gesture.value, "before_frame_sha256": value.before_frame_sha256,
            "after_frame_sha256": value.after_frame_sha256,
            "previous_receipt_sha256": value.previous_receipt_sha256,
            "receipt_sha256": value.receipt_sha256}


def load_capped_json(path: Path | str) -> object:
    candidate = Path(path)
    try:
        if candidate.stat().st_size > MAX_JSON_BYTES:
            raise SemanticIntegrationError("JSON file exceeds 65536 bytes")
        return json.loads(candidate.read_text(encoding="utf-8"))
    except SemanticIntegrationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticIntegrationError("malformed JSON file") from exc


def choose_route(
    plan_path: Path | str,
    metrics_path: Path | str,
    options: Sequence[Mapping[str, object]],
    choose: Callable[[str], object],
) -> dict[str, object]:
    plan_payload = load_capped_json(plan_path)
    metrics = load_capped_json(metrics_path)
    if not isinstance(plan_payload, Mapping):
        raise SemanticIntegrationError("route plan must be a JSON object")
    if not isinstance(metrics, Mapping):
        raise SemanticIntegrationError("route metrics must be a JSON object")
    if isinstance(options, (str, bytes)) or not isinstance(options, Sequence):
        raise SemanticIntegrationError("visible route options must be a sequence")
    plan = RoutePlan.from_dict(plan_payload)
    validate_run_metrics(metrics)
    visible: list[RouteNodeCandidate] = []
    visible_ids: set[str] = set()
    for item in options:
        if not isinstance(item, Mapping):
            raise SemanticIntegrationError("visible route option must be an object")
        kind = item.get("option_kind")
        if kind not in {"select-route-node", "select-visible-stage"}:
            continue
        choice_id, label = item.get("choice_id"), item.get("label")
        if not isinstance(choice_id, str) or not choice_id.strip():
            raise SemanticIntegrationError("visible route choice_id must be non-empty text")
        if not isinstance(label, str) or not label.strip():
            raise SemanticIntegrationError("visible route label must be non-empty text")
        if choice_id in visible_ids:
            raise SemanticIntegrationError("visible route choice ids must be unique")
        visible_ids.add(choice_id)
        visible.append(RouteNodeCandidate(choice_id, label))
    decision = plan.choose(visible, metrics)
    result = decision.to_dict()
    if decision.outcome == "selected":
        if decision.choice is None or not hasattr(decision.choice, "node_id"):
            raise SemanticIntegrationError("selected route decision has no exact node")
        receipt = choose(str(decision.choice.node_id))
        result["action_receipt"] = receipt
    return result
