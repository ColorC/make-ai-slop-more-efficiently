# [OMNI] origin=codex domain=runtime/llm ts=2026-06-13T00:00:00Z type=infra
# [OMNI] material_id="material:runtime.llm.structured_json_call.py"
"""Single authority for native structured LLM calls.

LLMClient owns transport, streaming, metering, and audit logging. ``call_json``
binds one native output tool, accepts only that tool's arguments, and validates
them locally. Prompt-shaped JSON, fenced JSON extraction, and text repair are
deliberately excluded from this authority. When a native tool call is missing or
its arguments are invalid, the same model conversation receives a tool error and
may submit the native tool again.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

DEFAULT_STRUCTURED_MODEL_ENV = "OMNI_STRUCTURED_LLM_MODEL"
DEFAULT_STRUCTURED_MODEL = "qwen3.7-max"
DEFAULT_MODEL = os.environ.get(DEFAULT_STRUCTURED_MODEL_ENV, DEFAULT_STRUCTURED_MODEL).strip() or DEFAULT_STRUCTURED_MODEL

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_OUTPUT_TOOL_NAME = "submit_structured_result"


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
    max_corrections: int = 2,
    client_factory: ClientFactory | None = None,
) -> Any:
    """Call an LLM once and accept only native output-tool arguments.

    ``max_corrections`` is retained as a compatibility name for the number of
    same-conversation follow-ups allowed when the model reasons or answers in
    prose without calling the output tool, or when native tool arguments fail
    local validation. It never asks the model to repair or emit JSON text.
    """
    effective_model = model or (None if role else default_structured_model())
    model_label = effective_model or f"role:{role}"
    factory = client_factory or _default_client_factory
    output_schema: Mapping[str, Any] = schema or {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
    output_tool = {
        "name": _OUTPUT_TOOL_NAME,
        "description": "Submit the complete structured result for this task.",
        "input_schema": dict(output_schema),
    }
    client_kwargs: dict[str, Any] = {
        "max_tokens": max_tokens,
        "tools": [output_tool],
        # 结构化管线自己持有会话内纠错。传输层遇到 4xx/配置错误应立即暴露,
        # 不能按 30/60/120s 静默退避把永久错误伪装成长时间思考。
        "max_retry_attempts": 0,
    }
    if effective_model:
        client_kwargs["model"] = effective_model
    if role:
        client_kwargs["role"] = role
    client = factory(**client_kwargs)
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    for turn in range(max(0, max_corrections) + 1):
        result = client.call(
            messages=messages,
            system=system,
            tool_choice="auto",
            response_format=None,
            caller=caller,
            info_audit=False,
        )
        try:
            parsed = _extract_output_tool_input(result)
        except StructuredJSONError:
            if turn >= max(0, max_corrections):
                raise
            messages.extend([
                _assistant_continuation_message(result),
                {
                    "role": "user",
                    "content": (
                        "Continue the same task with the reasoning above. "
                        "Now call the single provided output tool with the complete result."
                    ),
                },
            ])
            continue
        issues = validate_json_schema(parsed, schema)
        if issues:
            issue_text = _format_issues(issues)
            if turn >= max(0, max_corrections):
                raise StructuredJSONError(
                    f"model {model_label} returned invalid native tool arguments: "
                    f"{issue_text}"
                )
            messages.extend(
                _native_tool_error_messages(
                    result,
                    tool_name=_OUTPUT_TOOL_NAME,
                    error_text=(
                        "Native tool arguments failed validation: " + issue_text + ". "
                        "Keep the reasoning and task context, then call the same output "
                        "tool again with complete valid arguments. Do not emit JSON text."
                    ),
                )
            )
            continue
        return parsed
    raise StructuredJSONError(f"model {model_label} did not call the native output tool")


def _default_client_factory(**kwargs: Any) -> Any:
    from omnicompany.runtime.llm.llm import LLMClient

    return LLMClient(**kwargs)


def _extract_output_tool_input(result: Any) -> dict[str, Any]:
    for block in getattr(result, "content", None) or []:
        if getattr(block, "name", "") != _OUTPUT_TOOL_NAME:
            continue
        value = getattr(block, "input", None)
        if isinstance(value, dict):
            return value
    raise StructuredJSONError(
        "model did not call the native structured-output tool; free text is rejected"
    )


def _assistant_continuation_message(result: Any) -> dict[str, Any]:
    text = "".join(
        str(getattr(block, "text", ""))
        for block in getattr(result, "content", None) or []
        if getattr(block, "text", "")
    )
    message: dict[str, Any] = {
        "role": "assistant",
        "content": ([{"type": "text", "text": text}] if text else []),
    }
    reasoning = getattr(result, "reasoning_content", "")
    if isinstance(reasoning, str) and reasoning:
        message["reasoning_content"] = reasoning
    return message


def _native_tool_error_messages(
    result: Any,
    *,
    tool_name: str,
    error_text: str,
) -> list[dict[str, Any]]:
    """Preserve a rejected native tool call and return its error in-protocol."""
    text_blocks: list[dict[str, Any]] = []
    tool_blocks: list[dict[str, Any]] = []
    tool_use_id = ""
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", "")
        if text:
            text_blocks.append({"type": "text", "text": str(text)})
        if getattr(block, "name", "") == tool_name:
            tool_use_id = str(getattr(block, "id", "") or "native_output_tool")
            tool_blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": tool_name,
                    "input": getattr(block, "input", {}) or {},
                }
            )
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": [*text_blocks, *tool_blocks],
    }
    reasoning = getattr(result, "reasoning_content", "")
    if isinstance(reasoning, str) and reasoning:
        assistant["reasoning_content"] = reasoning
    tool_result = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id or "native_output_tool",
                "content": error_text,
                "is_error": True,
            }
        ],
    }
    return [assistant, tool_result]


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
