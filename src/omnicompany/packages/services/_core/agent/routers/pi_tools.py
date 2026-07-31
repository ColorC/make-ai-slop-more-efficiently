# [OMNI] origin=codex domain=services/agent ts=2026-07-29 type=infrastructure
# [OMNI] material_id="material:core.agent.pi_compatible_tools.py"
"""Pi-compatible model tool surface backed by Omni-owned audited execution."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import shlex
import tempfile
import threading
import unicodedata
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.pi_behavior import (
    PI_TOOL_CONTRACTS,
)
from omnicompany.packages.services._core.agent.routers.dev_bash import (
    DevBashRouter,
)
from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
    _resolve_path_from_ctx,
)


_MAX_LINES = 2000
_MAX_BYTES = 50 * 1024
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_MAX_TIMEOUT_SECONDS = 2_147_483_647 / 1000
_MUTATION_LOCKS_GUARD = threading.Lock()
_MUTATION_LOCKS: dict[str, threading.Lock] = {}


class _PiToolContract:
    ERROR_RESULT_STYLE: ClassVar[str] = "plain"

    @classmethod
    def _apply_contract(cls, name: str) -> None:
        cls.TOOL_NAME = name
        cls.DESCRIPTION = PI_TOOL_CONTRACTS[name]["description"]
        cls.INPUT_SCHEMA = PI_TOOL_CONTRACTS[name]["input_schema"]


class PiReadRouter(_PiToolContract, SingleToolRouter):
    CONSUMED_META_IO = ("meta_io.fs.read_file_text",)
    PRODUCED_META_IO = ()
    IS_CONCURRENCY_SAFE = True
    IS_READONLY = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            raise ToolExecutionError("path is required")
        path = _resolve_path_from_ctx(raw_path, ctx)
        if not path.is_file():
            raise ToolExecutionError(f"Path not found: {path}")

        if path.suffix.casefold() in _IMAGE_SUFFIXES:
            attachments = getattr(ctx, "pending_image_attachments", None)
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if isinstance(attachments, list):
                attachments.append(
                    {
                        "name": path.name,
                        "mime": mime,
                        "base64": base64.b64encode(path.read_bytes()).decode(
                            "ascii"
                        ),
                    }
                )
            return f"Read image file [{mime}]"

        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        lines = text.split("\n")
        offset = int(args.get("offset") or 1)
        limit_raw = args.get("limit")
        if offset < 1:
            offset = 1
        if offset > len(lines):
            raise ToolExecutionError(
                f"Offset {offset} is beyond end of file ({len(lines)} lines total)"
            )
        limit = int(limit_raw) if limit_raw is not None else None
        selected = (
            lines[offset - 1 :]
            if limit is None
            else lines[offset - 1 : max(offset - 1 + limit, 0)]
        )
        selected_text = "\n".join(selected)
        output, output_lines, truncated, truncated_by, first_line_exceeds = (
            _truncate_head_text(selected_text)
        )
        if first_line_exceeds:
            first_line_bytes = len(lines[offset - 1].encode("utf-8"))
            return (
                f"[Line {offset} is {_format_size(first_line_bytes)}, exceeds "
                f"{_format_size(_MAX_BYTES)} limit. Use bash: sed -n "
                f"'{offset}p' {raw_path} | head -c {_MAX_BYTES}]"
            )
        consumed = len(selected) if not truncated else output_lines
        end_line = offset + max(consumed - 1, 0)
        more_after_selection = offset - 1 + len(selected) < len(lines)
        if truncated:
            next_offset = end_line + 1
            suffix = (
                ""
                if truncated_by == "lines"
                else f" ({_format_size(_MAX_BYTES)} limit)"
            )
            output += (
                f"\n\n[Showing lines {offset}-{end_line} of {len(lines)}"
                f"{suffix}. Use offset={next_offset} to continue.]"
            )
        elif more_after_selection:
            remaining = len(lines) - (offset - 1 + len(selected))
            output += (
                f"\n\n[{remaining} more lines in file. "
                f"Use offset={end_line + 1} to continue.]"
            )
        return output


PiReadRouter._apply_contract("read")


class PiBashRouter(_PiToolContract, DevBashRouter):
    def _execute(self, args: dict, ctx: ToolContext) -> str:
        timeout = args.get("timeout")
        if timeout is not None:
            try:
                timeout = float(timeout)
            except (TypeError, ValueError) as exc:
                raise ToolExecutionError(
                    "Invalid timeout: must be a finite number of seconds"
                ) from exc
            if timeout <= 0:
                raise ToolExecutionError(
                    "Invalid timeout: must be a finite number of seconds"
                )
            if timeout > _MAX_TIMEOUT_SECONDS:
                raise ToolExecutionError(
                    f"Invalid timeout: maximum is {_MAX_TIMEOUT_SECONDS} seconds"
                )
        command = str(args.get("command") or "")
        exports = {
            "PI_SESSION_ID": getattr(ctx, "pi_session_id", ""),
            "PI_PROVIDER": getattr(ctx, "pi_provider", ""),
            "PI_MODEL": getattr(ctx, "pi_model", ""),
            "PI_REASONING_LEVEL": getattr(ctx, "pi_reasoning_level", ""),
        }
        prefix = " ".join(
            f"{name}={shlex.quote(str(value))}"
            for name, value in exports.items()
            if str(value)
        )
        if prefix:
            command = f"export {prefix}; {command}"
        translated = {
            "command": command,
            "cwd": str(getattr(ctx, "cwd", "") or ""),
        }
        if timeout is not None:
            translated["timeout_sec"] = timeout
        try:
            output = super()._execute(translated, ctx)
        except ToolExecutionError as exc:
            raise ToolExecutionError(_normalize_bash_error(str(exc), timeout)) from exc
        lines = output.splitlines()
        while lines and re.fullmatch(r"\[(?:exit=0|cwd_after=.*)\]", lines[-1]):
            lines.pop()
        normalized = "\n".join(lines).replace("\n[stderr]\n", "\n").strip()
        return _truncate_bash_tail(normalized)


PiBashRouter._apply_contract("bash")


class PiWriteRouter(_PiToolContract, SingleToolRouter):
    CONSUMED_META_IO = ()
    PRODUCED_META_IO = (
        "meta_io.fs.create_file",
        "meta_io.fs.overwrite_file",
    )
    IS_CONCURRENCY_SAFE = False
    IS_READONLY = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        raw_path = str(args.get("path") or "").strip()
        content = args.get("content")
        if not raw_path:
            raise ToolExecutionError("path is required")
        if not isinstance(content, str):
            raise ToolExecutionError("content must be a string")
        path = _resolve_path_from_ctx(raw_path, ctx)
        _require_write_authority(path, ctx)
        with _mutation_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as stream:
                stream.write(content)
        return (
            f"Successfully wrote {_javascript_string_length(content)} bytes "
            f"to {raw_path}"
        )


PiWriteRouter._apply_contract("write")


class PiEditRouter(_PiToolContract, SingleToolRouter):
    CONSUMED_META_IO = ("meta_io.fs.read_file_text",)
    PRODUCED_META_IO = ("meta_io.fs.overwrite_file",)
    IS_CONCURRENCY_SAFE = False
    IS_READONLY = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        raw_path = str(args.get("path") or "").strip()
        edits = args.get("edits")
        if isinstance(edits, str):
            try:
                parsed = json.loads(edits)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                edits = parsed
        if isinstance(args.get("oldText"), str) and isinstance(
            args.get("newText"), str
        ):
            edits = list(edits) if isinstance(edits, list) else []
            edits.append(
                {"oldText": args["oldText"], "newText": args["newText"]}
            )
        if not raw_path:
            raise ToolExecutionError("path is required")
        if not isinstance(edits, list) or not edits:
            raise ToolExecutionError("edits must be a non-empty array")
        path = _resolve_path_from_ctx(raw_path, ctx)
        _require_write_authority(path, ctx)
        if not path.is_file():
            raise ToolExecutionError(f"Could not edit file: {raw_path}. Path not found.")

        with _mutation_lock(path):
            raw = path.read_bytes().decode("utf-8")
            bom = "\ufeff" if raw.startswith("\ufeff") else ""
            text = raw[len(bom) :]
            first_lf = text.find("\n")
            first_crlf = text.find("\r\n")
            line_ending = (
                "\r\n"
                if first_lf >= 0 and first_crlf >= 0 and first_crlf <= first_lf
                else "\n"
            )
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            updated = _apply_pi_edits(normalized, edits, raw_path)
            restored = (
                updated
                if line_ending == "\n"
                else updated.replace("\n", line_ending)
            )
            with path.open("w", encoding="utf-8", newline="") as stream:
                stream.write(bom + restored)
        return f"Successfully replaced {len(edits)} block(s) in {raw_path}."


PiEditRouter._apply_contract("edit")


def _truncate_head_text(
    content: str,
) -> tuple[str, int, bool, str | None, bool]:
    lines = content.split("\n") if content else []
    if content.endswith("\n") and lines:
        lines.pop()
    total_bytes = len(content.encode("utf-8"))
    if len(lines) <= _MAX_LINES and total_bytes <= _MAX_BYTES:
        return content, len(lines), False, None, False
    if lines and len(lines[0].encode("utf-8")) > _MAX_BYTES:
        return "", 0, True, "bytes", True
    selected: list[str] = []
    byte_count = 0
    truncated_by = "lines"
    for index, line in enumerate(lines[:_MAX_LINES]):
        line_bytes = len(line.encode("utf-8")) + (1 if index > 0 else 0)
        if byte_count + line_bytes > _MAX_BYTES:
            truncated_by = "bytes"
            break
        selected.append(line)
        byte_count += line_bytes
    if len(selected) >= _MAX_LINES and byte_count <= _MAX_BYTES:
        truncated_by = "lines"
    return "\n".join(selected), len(selected), True, truncated_by, False


def _truncate_bash_tail(content: str, *, empty_value: str = "(no output)") -> str:
    if not content:
        return empty_value
    lines = content.split("\n")
    total_lines = len(lines)
    total_bytes = len(content.encode("utf-8"))
    if total_lines <= _MAX_LINES and total_bytes <= _MAX_BYTES:
        return content
    selected: list[str] = []
    byte_count = 0
    truncated_by = "lines"
    last_line_partial = False
    for line in reversed(lines):
        if len(selected) >= _MAX_LINES:
            break
        line_bytes = len(line.encode("utf-8")) + (1 if selected else 0)
        if byte_count + line_bytes > _MAX_BYTES:
            truncated_by = "bytes"
            if not selected:
                raw = line.encode("utf-8")
                chunk = raw[-_MAX_BYTES:]
                while chunk and (chunk[0] & 0xC0) == 0x80:
                    chunk = chunk[1:]
                selected.insert(0, chunk.decode("utf-8"))
                byte_count = len(chunk)
                last_line_partial = True
            break
        selected.insert(0, line)
        byte_count += line_bytes
    if len(selected) >= _MAX_LINES and byte_count <= _MAX_BYTES:
        truncated_by = "lines"
    truncated = "\n".join(selected)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="pi-bash-",
        suffix=".log",
        delete=False,
        newline="",
    ) as stream:
        stream.write(content)
        full_output_path = stream.name
    start_line = total_lines - len(selected) + 1
    end_line = total_lines
    if last_line_partial:
        return (
            f"{truncated}\n\n[Showing last {_format_size(byte_count)} of line "
            f"{end_line} (line is "
            f"{_format_size(len(lines[-1].encode('utf-8')))}). Full output: "
            f"{full_output_path}]"
        )
    if truncated_by == "lines":
        return (
            f"{truncated}\n\n[Showing lines {start_line}-{end_line} of "
            f"{total_lines}. Full output: {full_output_path}]"
        )
    return (
        f"{truncated}\n\n[Showing lines {start_line}-{end_line} of {total_lines} "
        f"({_format_size(_MAX_BYTES)} limit). Full output: {full_output_path}]"
    )


def _normalize_bash_error(message: str, timeout: float | None) -> str:
    failed = re.search(
        r"bash command failed with exit code (-?\d+).*?\n"
        r"stdout:\n(.*?)\n"
        r"stderr:\n(.*?)\n"
        r"cwd_after=",
        message,
        re.DOTALL,
    )
    if failed:
        output = "\n".join(
            part for part in (failed.group(2), failed.group(3)) if part
        )
        visible = _truncate_bash_tail(output, empty_value="")
        status = f"Command exited with code {failed.group(1)}"
        return f"{visible}\n\n{status}" if visible else status
    timed_out = re.search(
        r"bash TIMEOUT after ([0-9.]+)s.*?\n"
        r"Captured stdout: (.*?)\n"
        r"Captured stderr: (.*?)\n"
        r"Note ",
        message,
        re.DOTALL,
    )
    if timed_out:
        output = "\n".join(
            part for part in (timed_out.group(2), timed_out.group(3)) if part
        )
        visible = _truncate_bash_tail(output, empty_value="")
        raw_timeout = timed_out.group(1)
        status = f"Command timed out after {raw_timeout} seconds"
        return f"{visible}\n\n{status}" if visible else status
    if "bash ABORTED by external signal" in message:
        return "Command aborted"
    return message


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _javascript_string_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _mutation_lock(path: Path) -> threading.Lock:
    key = str(path.expanduser().resolve()).casefold()
    with _MUTATION_LOCKS_GUARD:
        return _MUTATION_LOCKS.setdefault(key, threading.Lock())


def _normalize_for_fuzzy_match(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    return normalized.translate(
        str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201a": "'",
                "\u201b": "'",
                "\u201c": '"',
                "\u201d": '"',
                "\u201e": '"',
                "\u201f": '"',
                "\u2010": "-",
                "\u2011": "-",
                "\u2012": "-",
                "\u2013": "-",
                "\u2014": "-",
                "\u2015": "-",
                "\u2212": "-",
                "\u00a0": " ",
                "\u2002": " ",
                "\u2003": " ",
                "\u2004": " ",
                "\u2005": " ",
                "\u2006": " ",
                "\u2007": " ",
                "\u2008": " ",
                "\u2009": " ",
                "\u200a": " ",
                "\u202f": " ",
                "\u205f": " ",
                "\u3000": " ",
            }
        )
    )


def _apply_pi_edits(
    normalized_content: str,
    edits: list[Any],
    path: str,
) -> str:
    normalized_edits: list[tuple[str, str]] = []
    total = len(edits)
    for index, item in enumerate(edits):
        if not isinstance(item, dict):
            raise ToolExecutionError(f"edits[{index}] must be an object")
        old = item.get("oldText")
        new = item.get("newText")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ToolExecutionError(
                f"edits[{index}].oldText and newText must be strings"
            )
        old = old.replace("\r\n", "\n").replace("\r", "\n")
        new = new.replace("\r\n", "\n").replace("\r", "\n")
        if not old:
            if total == 1:
                raise ToolExecutionError(f"oldText must not be empty in {path}.")
            raise ToolExecutionError(
                f"edits[{index}].oldText must not be empty in {path}."
            )
        normalized_edits.append((old, new))

    use_fuzzy = any(
        old not in normalized_content
        and _normalize_for_fuzzy_match(old)
        in _normalize_for_fuzzy_match(normalized_content)
        for old, _new in normalized_edits
    )
    base = (
        _normalize_for_fuzzy_match(normalized_content)
        if use_fuzzy
        else normalized_content
    )
    matches: list[tuple[int, int, str, int]] = []
    for index, (old, new) in enumerate(normalized_edits):
        target = _normalize_for_fuzzy_match(old) if use_fuzzy else old
        count = base.count(target)
        if count == 0:
            if total == 1:
                raise ToolExecutionError(
                    f"Could not find the exact text in {path}. The old text "
                    "must match exactly including all whitespace and newlines."
                )
            raise ToolExecutionError(
                f"Could not find edits[{index}] in {path}. The oldText must "
                "match exactly including all whitespace and newlines."
            )
        if count > 1:
            if total == 1:
                raise ToolExecutionError(
                    f"Found {count} occurrences of the text in {path}. The text "
                    "must be unique. Please provide more context to make it unique."
                )
            raise ToolExecutionError(
                f"Found {count} occurrences of edits[{index}] in {path}. Each "
                "oldText must be unique. Please provide more context to make it unique."
            )
        start = base.index(target)
        matches.append((start, start + len(target), new, index))
    ordered = sorted(matches)
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            raise ToolExecutionError(
                f"edits[{previous[3]}] and edits[{current[3]}] overlap in "
                f"{path}. Merge them into one edit or target disjoint regions."
            )
    updated = base
    for start, end, new, _index in sorted(matches, reverse=True):
        updated = updated[:start] + new + updated[end:]
    if use_fuzzy:
        updated = _preserve_unchanged_lines(
            normalized_content,
            base,
            matches,
            updated,
        )
    if updated == normalized_content:
        if total == 1:
            raise ToolExecutionError(
                f"No changes made to {path}. The replacement produced identical "
                "content. This might indicate an issue with special characters "
                "or the text not existing as expected."
            )
        raise ToolExecutionError(
            f"No changes made to {path}. The replacements produced identical content."
        )
    return updated


def _preserve_unchanged_lines(
    original: str,
    base: str,
    matches: list[tuple[int, int, str, int]],
    _updated: str,
) -> str:
    original_lines = re.findall(r"[^\n]*\n|[^\n]+", original)
    base_spans: list[tuple[int, int]] = []
    offset = 0
    for line in re.findall(r"[^\n]*\n|[^\n]+", base):
        base_spans.append((offset, offset + len(line)))
        offset += len(line)
    if len(original_lines) != len(base_spans):
        raise ToolExecutionError(
            "Cannot preserve unchanged lines because the base content has a "
            "different line count."
        )
    groups: list[tuple[int, int, list[tuple[int, int, str, int]]]] = []
    for match in sorted(matches):
        start, end, _new, _index = match
        start_line = next(
            (
                index
                for index, (line_start, line_end) in enumerate(base_spans)
                if line_start <= start < line_end
            ),
            -1,
        )
        if start_line < 0:
            raise ToolExecutionError(
                "Replacement range is outside the base content."
            )
        end_line = start_line
        while end_line < len(base_spans) and base_spans[end_line][1] < end:
            end_line += 1
        if end_line >= len(base_spans):
            raise ToolExecutionError(
                "Replacement range is outside the base content."
            )
        end_line += 1
        if groups and start_line < groups[-1][1]:
            prior_start, prior_end, prior_matches = groups[-1]
            groups[-1] = (
                prior_start,
                max(prior_end, end_line),
                prior_matches + [match],
            )
        else:
            groups.append((start_line, end_line, [match]))
    result = ""
    original_index = 0
    for start_line, end_line, group_matches in groups:
        result += "".join(original_lines[original_index:start_line])
        group_start = base_spans[start_line][0]
        group_end = base_spans[end_line - 1][1]
        segment = base[group_start:group_end]
        for start, end, new, _index in sorted(group_matches, reverse=True):
            local_start = start - group_start
            local_end = end - group_start
            segment = segment[:local_start] + new + segment[local_end:]
        result += segment
        original_index = end_line
    result += "".join(original_lines[original_index:])
    return result


def _require_write_authority(path: Path, ctx: ToolContext) -> None:
    target = path.expanduser().resolve()
    allowed_paths = tuple(getattr(ctx, "allowed_write_paths", ()) or ())
    allowed_roots = tuple(getattr(ctx, "allowed_write_roots", ()) or ())
    for candidate in allowed_paths:
        if target == Path(candidate).expanduser().resolve():
            return
    for candidate in allowed_roots:
        try:
            target.relative_to(Path(candidate).expanduser().resolve())
            return
        except ValueError:
            continue
    raise ToolExecutionError(f"write REFUSED: {target} is outside the capability grant")


__all__ = [
    "PiBashRouter",
    "PiEditRouter",
    "PiReadRouter",
    "PiWriteRouter",
]
