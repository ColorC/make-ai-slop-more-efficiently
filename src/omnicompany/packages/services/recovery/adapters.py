# [OMNI] origin=claude-code created_by=omni-recover intent=normalize-provider-session-evidence
"""Built-in read-only adapters for common Codex/Claude/Kimi/OpenCode shapes."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Iterator

from .evidence import EvidenceRecord


def _stable_id(*parts: object) -> str:
    raw = "\0".join(str(part) for part in parts).encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(raw).hexdigest()[:24]


def _sequence(record_number: int, sub_index: int = 0) -> int:
    """Keep every later source record later than all children of an earlier one."""

    return record_number * 1_000_000 + sub_index


def _timestamp(value: object) -> str:
    if isinstance(value, (int, float)):
        # Provider SQLite timestamps are normally milliseconds.
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc).isoformat()
    raw = str(value or "").strip()
    if not raw:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc).isoformat()
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc).isoformat()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat()


def _jsonl(paths: Iterable[Path]) -> Iterator[tuple[Path, int, dict[str, Any]]]:
    for path in paths:
        try:
            lines = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with lines:
            for sequence, line in enumerate(lines, 1):
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield path, sequence, payload


def _files(source: Path, name: str = "*.jsonl") -> Iterable[Path]:
    if source.is_file():
        return [source]
    return source.rglob(name)


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _path(raw: object, cwd: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    return str(path.resolve(strict=False))


def _data_uri(value: object) -> tuple[bytes, str] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"data:([^;,]+);base64,(.+)", value, flags=re.DOTALL)
    if not match:
        return None
    try:
        return base64.b64decode(match.group(2), validate=True), match.group(1)
    except Exception:
        return None


def _content_bytes(block: dict[str, Any]) -> tuple[bytes, str] | None:
    source = _mapping(block.get("source"))
    raw = source.get("data") or block.get("data")
    mime = str(source.get("media_type") or block.get("mimeType") or block.get("mime_type") or "application/octet-stream")
    if isinstance(raw, str):
        try:
            return base64.b64decode(raw, validate=True), mime
        except Exception:
            pass
    for value in (block.get("image_url"), block.get("url"), source.get("url")):
        parsed = _data_uri(value)
        if parsed:
            return parsed
    return None


def _attachment_records(
    *,
    blocks: Iterable[object],
    provider: str,
    session_id: str,
    timestamp: str,
    sequence: int,
    locator: str,
    path: str | None = None,
    exact_path: bool = False,
    derivative: bool = False,
) -> Iterator[EvidenceRecord]:
    for index, raw_block in enumerate(blocks):
        if not isinstance(raw_block, dict):
            continue
        if str(raw_block.get("type") or "").lower() not in {"image", "input_image", "file", "attachment"}:
            continue
        content = _content_bytes(raw_block)
        if content is None:
            continue
        body, mime = content
        name = str(raw_block.get("name") or raw_block.get("filename") or f"attachment-{sequence}-{index}")
        evidence_id = _stable_id(provider, locator, sequence, "attachment", index)
        yield EvidenceRecord.from_bytes(
            evidence_id=evidence_id,
            provider=provider,
            session_id=session_id,
            timestamp=timestamp,
            sequence=_sequence(sequence, index),
            kind="exact-file-read" if exact_path and path else "attachment",
            authority=(
                "attachment-exact-path" if exact_path and path
                else "derivative-artifact" if derivative
                else "artifact-exact"
            ),
            source_locator=locator,
            content=body,
            path=path if exact_path else None,
            artifact_name=name,
            mime_type=mime,
            metadata={"original_block_type": raw_block.get("type")},
        )


def _patch_adds(
    patch: str,
    *,
    provider: str,
    session_id: str,
    timestamp: str,
    sequence: int,
    locator: str,
    cwd: str | None,
) -> Iterator[EvidenceRecord]:
    marker = re.compile(r"(?m)^\*\*\* (Add|Delete|Update) File: (.+?)\s*$")
    matches = list(marker.finditer(patch))
    for index, match in enumerate(matches):
        action, raw_path = match.group(1).lower(), match.group(2).strip()
        target = _path(raw_path, cwd)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(patch)
        body = patch[match.end():end]
        evidence_id = _stable_id(provider, locator, sequence, action, raw_path)
        if action == "add":
            content = b"\n".join(
                line[1:].encode("utf-8") for line in body.splitlines() if line.startswith("+")
            )
            if body.endswith("\n"):
                content += b"\n"
            yield EvidenceRecord.from_bytes(
                evidence_id=evidence_id, provider=provider, session_id=session_id,
                timestamp=timestamp, sequence=_sequence(sequence, index), kind="patch-add",
                authority="mutation-postimage", source_locator=locator,
                content=content, path=target, mime_type="text/plain; charset=utf-8",
            )
        else:
            yield EvidenceRecord(
                evidence_id=evidence_id, provider=provider, session_id=session_id,
                timestamp=timestamp, sequence=_sequence(sequence, index), kind=f"patch-{action}",
                authority="delete-intent" if action == "delete" else "intent-only",
                source_locator=locator, path=target,
                metadata={"patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest()},
            )


class CodexAdapter:
    name = "codex"

    def iter_evidence(self, source: Path) -> Iterable[EvidenceRecord]:
        session_by_log: dict[Path, str] = {}
        cwd_by_log: dict[Path, str] = {}
        calls: dict[tuple[Path, str], tuple[str, dict[str, Any], int, str]] = {}
        for log, sequence, item in _jsonl(_files(source)):
            payload = _mapping(item.get("payload"))
            locator = f"{log}:{sequence}"
            if item.get("type") == "session_meta":
                session_by_log[log] = str(payload.get("id") or payload.get("session_id") or log.stem)
                cwd_by_log[log] = str(payload.get("cwd") or log.parent)
                continue
            session_id = session_by_log.get(log, log.stem)
            cwd = cwd_by_log.get(log, str(log.parent))
            when = _timestamp(item.get("timestamp"))
            if item.get("type") == "event_msg" and payload.get("type") == "patch_apply_end" and payload.get("success"):
                for index, (raw_path, raw_change) in enumerate(_mapping(payload.get("changes")).items()):
                    change = _mapping(raw_change)
                    target = _path(raw_path, cwd)
                    if "content" in change:
                        content = str(change.get("content") or "").encode("utf-8")
                        yield EvidenceRecord.from_bytes(
                            evidence_id=_stable_id("codex", locator, raw_path, index), provider="codex",
                            session_id=session_id, timestamp=when, sequence=_sequence(sequence, index),
                            kind="patch-postimage", authority="mutation-postimage",
                            source_locator=locator, content=content, path=target,
                            mime_type="text/plain; charset=utf-8",
                        )
                continue
            if item.get("type") != "response_item":
                continue
            payload_type = str(payload.get("type") or "")
            if payload_type in {"custom_tool_call", "function_call"}:
                name = str(payload.get("name") or "")
                call_id = str(payload.get("call_id") or payload.get("id") or _stable_id(locator, name))
                args = _mapping(payload.get("arguments") or payload.get("input"))
                calls[(log, call_id)] = (name, args, sequence, when)
                if name.lower() in {"write", "write_file"}:
                    target = _path(args.get("path") or args.get("file_path"), cwd)
                    if "content" in args:
                        yield EvidenceRecord.from_bytes(
                            evidence_id=_stable_id("codex", locator, call_id), provider="codex",
                            session_id=session_id, timestamp=when, sequence=_sequence(sequence), kind="write",
                            authority="mutation-postimage", source_locator=locator,
                            content=str(args.get("content") or "").encode("utf-8"), path=target,
                            mime_type="text/plain; charset=utf-8",
                        )
                elif name == "apply_patch":
                    patch = str(payload.get("input") or args.get("patch") or "")
                    yield from _patch_adds(
                        patch, provider="codex", session_id=session_id, timestamp=when,
                        sequence=sequence, locator=locator, cwd=cwd,
                    )
                elif name.lower() in {"shell_command", "exec", "exec_command"}:
                    yield EvidenceRecord(
                        evidence_id=_stable_id("codex", locator, call_id), provider="codex",
                        session_id=session_id, timestamp=when, sequence=_sequence(sequence), kind="shell-command",
                        authority="intent-only", source_locator=locator,
                        metadata={"command_sha256": hashlib.sha256(str(payload.get("input") or args).encode()).hexdigest()},
                    )
            elif payload_type in {"custom_tool_call_output", "function_call_output"}:
                call_id = str(payload.get("call_id") or "")
                call = calls.get((log, call_id))
                output = payload.get("output") or payload.get("content") or []
                blocks = output if isinstance(output, list) else [output] if isinstance(output, dict) else []
                if call:
                    name, args, _, _ = call
                    target = _path(args.get("path") or args.get("file_path"), cwd)
                    detail = str(args.get("detail") or "").lower()
                    exact = name.lower() in {"read_file_bytes", "read_binary"} or (
                        name.lower() == "view_image" and detail == "original"
                    )
                    yield from _attachment_records(
                        blocks=blocks, provider="codex", session_id=session_id, timestamp=when,
                        sequence=sequence, locator=locator, path=target, exact_path=exact,
                        derivative=not exact,
                    )
            else:
                blocks = payload.get("content") if isinstance(payload.get("content"), list) else []
                yield from _attachment_records(
                    blocks=blocks, provider="codex", session_id=session_id, timestamp=when,
                    sequence=sequence, locator=locator,
                )


class ClaudeAdapter:
    name = "claude"

    def iter_evidence(self, source: Path) -> Iterable[EvidenceRecord]:
        backup_index = {
            path.name: path for path in source.rglob("*") if path.is_file() and "@v" in path.name
        } if source.is_dir() else {}
        for log, sequence, item in _jsonl(_files(source)):
            locator = f"{log}:{sequence}"
            session_id = str(item.get("sessionId") or item.get("session_id") or log.stem)
            when = _timestamp(item.get("timestamp"))
            cwd = str(item.get("cwd") or log.parent)
            if item.get("type") == "file-history-snapshot":
                snapshot = _mapping(item.get("snapshot"))
                when = _timestamp(snapshot.get("timestamp") or item.get("timestamp"))
                for index, (raw_path, raw_backup) in enumerate(_mapping(snapshot.get("trackedFileBackups")).items()):
                    backup = _mapping(raw_backup)
                    backup_name = str(backup.get("backupFileName") or "")
                    backup_path = backup_index.get(backup_name)
                    if backup_path and backup_path.is_file():
                        yield EvidenceRecord.from_bytes(
                            evidence_id=_stable_id("claude", locator, raw_path, backup_name), provider="claude",
                            session_id=session_id, timestamp=when, sequence=_sequence(sequence, index),
                            kind="checkpoint-preimage", authority="read-exact", source_locator=locator,
                            content=backup_path.read_bytes(), path=_path(raw_path, cwd),
                            metadata={"backup_file": backup_name, "checkpoint_preimage": True},
                        )
                continue
            result = _mapping(item.get("toolUseResult"))
            file_result = _mapping(result.get("file"))
            if file_result and "content" in file_result:
                target = _path(file_result.get("filePath"), cwd)
                yield EvidenceRecord.from_bytes(
                    evidence_id=_stable_id("claude", locator, "read"), provider="claude",
                    session_id=session_id, timestamp=when, sequence=_sequence(sequence), kind="read-exact",
                    authority="read-exact", source_locator=locator,
                    content=str(file_result.get("content") or "").encode("utf-8"), path=target,
                    mime_type="text/plain; charset=utf-8",
                )
            message = _mapping(item.get("message"))
            blocks = message.get("content") if isinstance(message.get("content"), list) else []
            yield from _attachment_records(
                blocks=blocks, provider="claude", session_id=session_id, timestamp=when,
                sequence=sequence, locator=locator,
            )
            if message.get("role") != "assistant":
                continue
            for index, block in enumerate(blocks):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = str(block.get("name") or "")
                args = _mapping(block.get("input"))
                target = _path(args.get("file_path") or args.get("path"), cwd)
                evidence_id = _stable_id("claude", locator, block.get("id"), index)
                if name == "Write" and "content" in args:
                    yield EvidenceRecord.from_bytes(
                        evidence_id=evidence_id, provider="claude", session_id=session_id,
                        timestamp=when, sequence=_sequence(sequence, index), kind="write",
                        authority="mutation-postimage", source_locator=locator,
                        content=str(args.get("content") or "").encode("utf-8"), path=target,
                        mime_type="text/plain; charset=utf-8",
                    )
                elif name in {"Bash", "Shell"}:
                    yield EvidenceRecord(
                        evidence_id=evidence_id, provider="claude", session_id=session_id,
                        timestamp=when, sequence=_sequence(sequence, index), kind="shell-command",
                        authority="intent-only", source_locator=locator,
                        metadata={"command_sha256": hashlib.sha256(str(args.get("command") or "").encode()).hexdigest()},
                    )
                elif name == "apply_patch":
                    yield from _patch_adds(
                        str(args.get("patch") or args.get("input") or ""), provider="claude",
                        session_id=session_id, timestamp=when, sequence=_sequence(sequence, index),
                        locator=locator, cwd=cwd,
                    )


class KimiAdapter:
    name = "kimi"

    def iter_evidence(self, source: Path) -> Iterable[EvidenceRecord]:
        wires = [source] if source.is_file() else source.rglob("wire.jsonl")
        calls: dict[tuple[Path, str], tuple[str, dict[str, Any], str, int, str]] = {}
        for log, sequence, item in _jsonl(wires):
            event = _mapping(item.get("event"))
            event_type = str(event.get("type") or "")
            session_id = next((part for part in log.parts if part.startswith("session_")), log.parent.name)
            when = _timestamp(event.get("time") or item.get("time"))
            locator = f"{log}:{sequence}"
            if event_type == "tool.call":
                call_id = str(event.get("uuid") or event.get("toolCallId") or _stable_id(locator))
                name = str(event.get("name") or "")
                args = _mapping(event.get("args"))
                cwd = str(args.get("cwd") or args.get("workdir") or log.parent)
                calls[(log, call_id)] = (name, args, cwd, sequence, when)
                target = _path(args.get("path") or args.get("file_path") or args.get("filePath"), cwd)
                evidence_id = _stable_id("kimi", locator, call_id)
                if name.lower() == "write" and "content" in args:
                    yield EvidenceRecord.from_bytes(
                        evidence_id=evidence_id, provider="kimi", session_id=session_id,
                        timestamp=when, sequence=_sequence(sequence), kind="write",
                        authority="mutation-postimage", source_locator=locator,
                        content=str(args.get("content") or "").encode("utf-8"), path=target,
                        mime_type="text/plain; charset=utf-8",
                    )
                elif name == "apply_patch":
                    yield from _patch_adds(
                        str(args.get("patch") or args.get("input") or args.get("content") or ""),
                        provider="kimi", session_id=session_id, timestamp=when,
                        sequence=sequence, locator=locator, cwd=cwd,
                    )
                elif name.lower() in {"bash", "shell", "powershell"}:
                    yield EvidenceRecord(
                        evidence_id=evidence_id, provider="kimi", session_id=session_id,
                        timestamp=when, sequence=_sequence(sequence), kind="shell-command",
                        authority="intent-only", source_locator=locator,
                        metadata={"command_sha256": hashlib.sha256(str(args.get("command") or "").encode()).hexdigest()},
                    )
            elif event_type == "tool.result":
                call_id = str(event.get("parentUuid") or event.get("toolCallId") or "")
                call = calls.get((log, call_id))
                if not call:
                    continue
                name, args, cwd, _, _ = call
                result = _mapping(event.get("result"))
                target = _path(args.get("path") or args.get("file_path") or args.get("filePath"), cwd)
                blocks = result.get("content") if isinstance(result.get("content"), list) else []
                exact = name.lower() in {"read_binary", "read_file_bytes", "view_image_original"}
                yield from _attachment_records(
                    blocks=blocks, provider="kimi", session_id=session_id, timestamp=when,
                    sequence=sequence, locator=locator, path=target, exact_path=exact,
                )
                if name.lower() == "read" and result.get("complete") is True and "content" in result:
                    content = result.get("content")
                    if isinstance(content, str):
                        yield EvidenceRecord.from_bytes(
                            evidence_id=_stable_id("kimi", locator, call_id, "read"), provider="kimi",
                            session_id=session_id, timestamp=when, sequence=_sequence(sequence), kind="read-exact",
                            authority="read-exact", source_locator=locator,
                            content=content.encode("utf-8"), path=target,
                            mime_type="text/plain; charset=utf-8",
                        )


class OpenCodeAdapter:
    name = "opencode"

    def iter_evidence(self, source: Path) -> Iterable[EvidenceRecord]:
        database = source / "opencode.db" if source.is_dir() else source
        if database.is_file():
            # ``immutable=1`` silently ignores a live WAL and therefore loses
            # the newest sessions. ``mode=ro`` remains query-only while letting
            # SQLite merge the existing WAL consistently.
            uri = f"file:{database.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    "SELECT p.*, s.directory FROM part p LEFT JOIN session s ON s.id=p.session_id "
                    "ORDER BY p.time_created, p.id"
                )
                for sequence, row in enumerate(rows, 1):
                    try:
                        data = json.loads(row["data"])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(data, dict):
                        continue
                    locator = f"{database}:part:{row['id']}"
                    session_id = str(row["session_id"])
                    when = _timestamp(row["time_created"])
                    cwd = str(row["directory"] or database.parent)
                    if data.get("type") == "tool":
                        tool = str(data.get("tool") or "").lower()
                        state = _mapping(data.get("state"))
                        args = _mapping(state.get("input"))
                        target = _path(args.get("filePath") or args.get("file_path") or args.get("path"), cwd)
                        evidence_id = _stable_id("opencode", row["id"])
                        if tool == "write" and "content" in args:
                            yield EvidenceRecord.from_bytes(
                                evidence_id=evidence_id, provider="opencode", session_id=session_id,
                                timestamp=when, sequence=_sequence(sequence), kind="write",
                                authority="mutation-postimage", source_locator=locator,
                                content=str(args.get("content") or "").encode("utf-8"), path=target,
                                mime_type="text/plain; charset=utf-8",
                            )
                        elif tool == "apply_patch":
                            yield from _patch_adds(
                                str(args.get("patchText") or args.get("patch") or ""),
                                provider="opencode", session_id=session_id, timestamp=when,
                                sequence=sequence, locator=locator, cwd=cwd,
                            )
                        elif tool == "read" and _mapping(state.get("metadata")).get("exact_bytes") is True:
                            output = state.get("output")
                            if isinstance(output, str):
                                yield EvidenceRecord.from_bytes(
                                    evidence_id=evidence_id, provider="opencode", session_id=session_id,
                                    timestamp=when, sequence=_sequence(sequence), kind="read-exact",
                                    authority="read-exact", source_locator=locator,
                                    content=output.encode("utf-8"), path=target,
                                    mime_type="text/plain; charset=utf-8",
                                )
                        elif tool in {"bash", "shell", "powershell"}:
                            yield EvidenceRecord(
                                evidence_id=evidence_id, provider="opencode", session_id=session_id,
                                timestamp=when, sequence=_sequence(sequence), kind="shell-command",
                                authority="intent-only", source_locator=locator,
                                metadata={"command_sha256": hashlib.sha256(str(args.get("command") or "").encode()).hexdigest()},
                            )
                    blocks = data.get("content") if isinstance(data.get("content"), list) else [data]
                    yield from _attachment_records(
                        blocks=blocks, provider="opencode", session_id=session_id, timestamp=when,
                        sequence=sequence, locator=locator,
                    )
            finally:
                connection.close()
        tool_output = source / "tool-output" if source.is_dir() else None
        if tool_output and tool_output.is_dir():
            for sequence, path in enumerate(sorted(item for item in tool_output.rglob("*") if item.is_file()), 1):
                yield EvidenceRecord.from_bytes(
                    evidence_id=_stable_id("opencode", "tool-output", path.relative_to(tool_output)),
                    provider="opencode", session_id="tool-output", timestamp=_timestamp(path.stat().st_mtime),
                    sequence=_sequence(sequence), kind="tool-output-blob", authority="artifact-exact",
                    source_locator=str(path), content=path.read_bytes(), artifact_name=path.name,
                )


def builtin_adapters() -> tuple[object, ...]:
    return (CodexAdapter(), ClaudeAdapter(), KimiAdapter(), OpenCodeAdapter())
