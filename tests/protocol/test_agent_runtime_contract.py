from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPO_ROOT / "contracts" / "agent-runtime" / "v0"
SCHEMA_PATH = CONTRACT_ROOT / "agent-runtime.schema.json"
FIXTURES_ROOT = CONTRACT_ROOT / "fixtures"


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(_load(SCHEMA_PATH))


def test_positive_fixtures_are_accepted() -> None:
    validator = Draft202012Validator(_load(SCHEMA_PATH))
    for path in sorted((FIXTURES_ROOT / "valid").glob("*.json")):
        errors = sorted(validator.iter_errors(_load(path)), key=lambda item: list(item.path))
        assert errors == [], f"{path.name}: {[error.message for error in errors]}"


def test_counterexamples_are_rejected() -> None:
    validator = Draft202012Validator(_load(SCHEMA_PATH))
    for path in sorted((FIXTURES_ROOT / "invalid").glob("*.json")):
        errors = list(validator.iter_errors(_load(path)))
        assert errors, f"counterexample unexpectedly accepted: {path.name}"


def test_vendor_private_session_event_cannot_cross_boundary() -> None:
    validator = Draft202012Validator(_load(SCHEMA_PATH))
    value = _load(FIXTURES_ROOT / "invalid" / "dsh-session-event-leak.json")
    errors = list(validator.iter_errors(value))

    assert errors
    assert "sessionEvent" in json.dumps(value)

