# [OMNI] origin=codex domain=runtime/llm ts=2026-06-13T00:00:00Z type=infra
# [OMNI] material_id="material:runtime.llm.structured_json_call.py"
"""Single authority for one-shot structured JSON LLM calls.

This module deliberately stays above provider specifics: LLMClient owns network
transport, retries, metering, and audit logging; call_json owns the structured
JSON contract shared by governance and future departments.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

DEFAULT_STRUCTURED_MODEL_ENV = "OMNI_STRUCTURED_LLM_MODEL"
DEFAULT_STRUCTURED_MODEL = "deepseek-v4-pro"
DEFAULT_MODEL = os.environ.get(DEFAULT_STRUCTURED_MODEL_ENV, DEFAULT_STRUCTURED_MODEL).strip() or DEFAULT_STRUCTURED_MODEL

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_SCHEMA_PROMPT_LIMIT = 8000


class StructuredJSONError(ValueError):
    """Raised when the model cannot produce JSON satisfying the requested schema."""


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str


ClientFactory = Callable[..., Any]


def default_structured_model(
    *,
    env_var: str = DEFAULT_STRUCTURED_MODEL_ENV,
    fallback: str = DEFAULT_STRUCTURED_MODEL,
) -> str:
    """Resolve the model slot at call time so long-running processes can reconfigure it."""
    return os.environ.get(env_var, fallback).strip() or fallback


def parse_json_block(text: str) -> Any:
    """Extract the first valid JSON object/array from plain text or a fenced block."""
    raw = (text or "").strip()
    decoder = json.JSONDecoder()

    for match in _JSON_FENCE_RE.finditer(raw):
        candidate = match.group(1).strip()
        try:
            value, _ = decoder.raw_decode(candidate)
            return value
        except json.JSONDecodeError:
            continue

    for index, char in enumerate(raw):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
            return value
        except json.JSONDecodeError:
            continue

    preview = raw[:160].replace("\n", "\\n")
    raise StructuredJSONError(f"model output does not contain valid JSON: {preview!r}")


def validate_json_schema(value: Any, schema: Mapping[str, Any] | None) -> list[ValidationIssue]:
    """Validate a pragmatic JSON Schema subset used by Format and governance contracts."""
    if not schema:
        return []
    issues: list[ValidationIssue] = []
    _validate_schema_node(value, schema, "$", issues)
    return issues


def call_json(
    *,
    system: str,
    user: Any,
    schema: Mapping[str, Any] | None = None,
    model: str | None = None,
    role: str | None = None,
    caller: str = "structured.call_json",
    max_tokens: int = 8000,
    max_corrections: int = 1,
    client_factory: ClientFactory | None = None,
) -> Any:
    """Call an LLM once for strict JSON, with local schema validation and correction retry."""
    effective_model = model or (None if role else default_structured_model())
    model_label = effective_model or f"role:{role}"
    factory = client_factory or _default_client_factory
    client_kwargs: dict[str, Any] = {"max_tokens": max_tokens}
    if effective_model:
        client_kwargs["model"] = effective_model
    if role:
        client_kwargs["role"] = role
    client = factory(**client_kwargs)
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    effective_system = _with_structured_contract(system, schema)
    last_error: Exception | None = None
    last_text = ""

    for _attempt in range(max(0, max_corrections) + 1):
        result = client.call(
            messages=messages,
            system=effective_system,
            caller=caller,
            info_audit=False,
        )
        last_text = _extract_text(result)
        try:
            parsed = parse_json_block(last_text)
            issues = validate_json_schema(parsed, schema)
            if issues:
                raise StructuredJSONError(_format_issues(issues))
            return parsed
        except Exception as exc:  # noqa: BLE001 - surface one uniform structured-call error.
            last_error = exc
            messages = messages + [
                {"role": "assistant", "content": last_text[:4000]},
                {"role": "user", "content": _correction_prompt(exc, schema)},
            ]

    raise StructuredJSONError(
        f"model {model_label} did not return schema-valid JSON after "
        f"{max(0, max_corrections) + 1} attempt(s): {last_error}"
    )


def _default_client_factory(**kwargs: Any) -> Any:
    from omnicompany.runtime.llm.llm import LLMClient

    return LLMClient(**kwargs)


def _extract_text(result: Any) -> str:
    from omnicompany.runtime.llm.llm import _extract_response_text

    text = _extract_response_text(result) or ""
    if text:
        return text
    direct = getattr(result, "text", "")
    if isinstance(direct, str):
        return direct
    if isinstance(result, str):
        return result
    return ""


def _with_structured_contract(system: str, schema: Mapping[str, Any] | None) -> str:
    parts = [system.strip() if system else ""]
    parts.append(
        "Return only one strict JSON value. Do not include markdown, prose, comments, "
        "or trailing text."
    )
    if schema:
        schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True)
        parts.append(f"The JSON value must satisfy this JSON Schema subset:\n{schema_text[:_SCHEMA_PROMPT_LIMIT]}")
    return "\n\n".join(part for part in parts if part)


def _correction_prompt(error: Exception, schema: Mapping[str, Any] | None) -> str:
    parts = [
        "The previous response was not valid JSON for this contract.",
        f"Error: {error}",
        "Return only the corrected JSON value. No markdown or explanation.",
    ]
    if schema:
        schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True)
        parts.append(f"Required JSON Schema subset:\n{schema_text[:_SCHEMA_PROMPT_LIMIT]}")
    return "\n\n".join(parts)


def _format_issues(issues: list[ValidationIssue]) -> str:
    return "; ".join(f"{issue.path}: {issue.message}" for issue in issues[:12])


def _validate_schema_node(
    value: Any,
    schema: Mapping[str, Any],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    if not isinstance(schema, Mapping):
        return

    if "anyOf" in schema:
        branches = [b for b in schema.get("anyOf") or [] if isinstance(b, Mapping)]
        if branches and not any(not validate_json_schema(value, b) for b in branches):
            issues.append(ValidationIssue(path, "does not match anyOf"))
        return

    if "oneOf" in schema:
        branches = [b for b in schema.get("oneOf") or [] if isinstance(b, Mapping)]
        matches = sum(1 for b in branches if not validate_json_schema(value, b))
        if branches and matches != 1:
            issues.append(ValidationIssue(path, f"matches {matches} oneOf branches"))
        return

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        issues.append(ValidationIssue(path, f"expected one of {enum!r}, got {value!r}"))

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_json_type(value, expected_type):
        issues.append(ValidationIssue(path, f"expected type {expected_type!r}, got {_json_type(value)}"))
        return

    if isinstance(value, dict):
        required = schema.get("required") or []
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    issues.append(ValidationIssue(f"{path}.{key}", "required property missing"))

        properties = schema.get("properties") or {}
        if isinstance(properties, Mapping):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, Mapping):
                    _validate_schema_node(value[key], child_schema, f"{path}.{key}", issues)

            if schema.get("additionalProperties") is False:
                extra = sorted(set(value) - set(properties))
                for key in extra:
                    issues.append(ValidationIssue(f"{path}.{key}", "additional property is not allowed"))

    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            issues.append(ValidationIssue(path, f"expected at least {min_items} item(s)"))
        if isinstance(max_items, int) and len(value) > max_items:
            issues.append(ValidationIssue(path, f"expected at most {max_items} item(s)"))
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_schema_node(item, item_schema, f"{path}[{index}]", issues)

    if isinstance(value, str):
        min_len = schema.get("minLength")
        max_len = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(min_len, int) and len(value) < min_len:
            issues.append(ValidationIssue(path, f"expected length >= {min_len}"))
        if isinstance(max_len, int) and len(value) > max_len:
            issues.append(ValidationIssue(path, f"expected length <= {max_len}"))
        if isinstance(pattern, str) and not re.search(pattern, value):
            issues.append(ValidationIssue(path, f"does not match pattern {pattern!r}"))


def _matches_json_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_json_type(value, item) for item in expected)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _json_type(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_STRUCTURED_MODEL",
    "DEFAULT_STRUCTURED_MODEL_ENV",
    "StructuredJSONError",
    "call_json",
    "default_structured_model",
    "parse_json_block",
    "validate_json_schema",
]
