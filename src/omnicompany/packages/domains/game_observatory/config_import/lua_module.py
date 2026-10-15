"""Static scanner for decompiled Lua files that are data but not ``return`` tables.

Three shapes are recognised as data, all without executing anything:

``Name = { ... }``
    A top-level global assignment of a table literal.
``local Name = { ... }`` followed by ``return Name``
    A module-local table that is returned at the end of the file.
``Name = {}`` followed by ``Name.Key = <literal>`` / ``Name[<literal>] = <literal>``
    A table filled in field by field.

Every other top-level statement (function definitions, control flow, calls, and
assignments whose right-hand side is not a literal table) is skipped and
recorded, so the caller can report exactly what was left behind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .lua_literal import (
    LuaLiteralError,
    LuaLiteralParser,
    LuaNotLiteralError,
    LuaParseStats,
)


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Lua block structure.  ``for``/``while`` do not open a block themselves; their
# ``do`` does, so every opened block is closed by exactly one ``end``/``until``.
_BLOCK_OPENERS = frozenset({"function", "if", "do", "repeat"})
_BLOCK_CLOSERS = frozenset({"end", "until"})

RETURN_EXPORT = "__return__"


@dataclass(frozen=True)
class IgnoredStatement:
    """One top-level statement that carried no importable data."""

    line: int
    kind: str
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"line": self.line, "kind": self.kind, "text": self.text}


@dataclass
class ModuleScan:
    """Result of scanning one Lua source file for top-level data tables."""

    exports: dict[str, Any] = field(default_factory=dict)
    ignored: list[IgnoredStatement] = field(default_factory=list)
    stats: LuaParseStats = field(default_factory=LuaParseStats)
    returned_name: str | None = None


def _iter_tokens(text: str, pos: int) -> Iterator[tuple[str, str, int]]:
    """Yield ``(kind, value, end)`` triples, skipping comments and strings."""

    scanner = LuaLiteralParser(text)
    scanner.pos = pos
    while True:
        scanner._skip_ignored()
        if scanner.pos >= len(text):
            return
        char = text[scanner.pos]
        if char in {'"', "'"}:
            scanner._parse_quoted_string()
            yield "string", "", scanner.pos
            continue
        if char == "[" and scanner._long_bracket_level(scanner.pos) is not None:
            scanner._parse_long_string()
            yield "string", "", scanner.pos
            continue
        match = _IDENTIFIER_RE.match(text, scanner.pos)
        if match:
            scanner.pos = match.end()
            yield "name", match.group(0), scanner.pos
            continue
        scanner.pos += 1
        yield "op", char, scanner.pos


def _at_line_start(text: str, pos: int) -> bool:
    return pos == 0 or text[pos - 1] == "\n"


def _skip_statement(text: str, pos: int) -> int:
    """Return the offset of the next top-level statement after the one at *pos*.

    Block depth is tracked over Lua's block keywords; a boundary is taken only
    where depth is zero and the next statement starts in column one.  The
    decompiled sources place every top-level statement in column one, so this is
    an exact boundary for them rather than a guess.
    """

    depth = 0
    cursor = pos
    for kind, value, end in _iter_tokens(text, pos):
        cursor = end
        if kind == "name":
            if value in _BLOCK_OPENERS:
                depth += 1
            elif value in _BLOCK_CLOSERS:
                depth -= 1
        if depth > 0:
            continue
        probe = LuaLiteralParser(text)
        probe.pos = cursor
        probe._skip_ignored()
        if probe.pos >= len(text):
            return len(text)
        if _at_line_start(text, probe.pos):
            return probe.pos
    return len(text)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _statement_text(text: str, start: int, limit: int = 90) -> str:
    end = text.find("\n", start)
    if end < 0:
        end = len(text)
    snippet = text[start:end].strip()
    if len(snippet) > limit:
        snippet = snippet[: limit - 3] + "..."
    return snippet


def scan_module(text: str, resolver: Callable[[str], tuple[bool, Any]] | None = None) -> ModuleScan:
    """Scan *text* for top-level data tables without executing it."""

    scan = ModuleScan()
    locals_seen: dict[str, Any] = {}
    nil_dropped = resolved = unresolved = calls = 0
    pos = 0

    while True:
        parser = LuaLiteralParser(text, permissive=True, resolver=resolver)
        parser.pos = pos
        parser._skip_ignored()
        if parser.pos >= len(text):
            break
        start = parser.pos
        handled, next_pos, kind = _scan_statement(
            text, start, parser, scan, locals_seen, resolver
        )
        nil_dropped += parser.nil_dropped
        resolved += parser.resolved_refs
        unresolved += parser.unresolved_refs
        calls += parser.calls
        if not handled:
            scan.ignored.append(
                IgnoredStatement(
                    line=_line_of(text, start),
                    kind=kind,
                    text=_statement_text(text, start),
                )
            )
            next_pos = _skip_statement(text, start)
        if next_pos <= pos:
            next_pos = _skip_statement(text, start)
            if next_pos <= pos:
                break
        pos = next_pos

    if scan.returned_name and scan.returned_name in locals_seen:
        scan.exports[scan.returned_name] = locals_seen[scan.returned_name]
    scan.stats = LuaParseStats(
        nil_dropped=nil_dropped,
        resolved_refs=resolved,
        unresolved_refs=unresolved,
        calls=calls,
    )
    return scan


def _scan_statement(
    text: str,
    start: int,
    parser: LuaLiteralParser,
    scan: ModuleScan,
    locals_seen: dict[str, Any],
    resolver: Callable[[str], tuple[bool, Any]] | None,
) -> tuple[bool, int, str]:
    """Try to read one data statement at *start*; report whether it was data."""

    is_local = parser._consume_keyword("local")
    if is_local:
        parser._skip_ignored()

    if not is_local and parser._consume_keyword("return"):
        parser._skip_ignored()
        if parser._peek() == "{":
            try:
                value = parser._parse_table()
            except (LuaLiteralError, LuaNotLiteralError):
                return False, start, "non_literal_return"
            scan.exports[RETURN_EXPORT] = value
            return True, parser.pos, "return_table"
        name_match = _IDENTIFIER_RE.match(text, parser.pos)
        if name_match:
            scan.returned_name = name_match.group(0)
            return True, name_match.end(), "return_name"
        return False, start, "non_literal_return"

    name_match = _IDENTIFIER_RE.match(text, parser.pos)
    if not name_match:
        return False, start, "statement"
    name = name_match.group(0)
    if name in _BLOCK_OPENERS or name in _BLOCK_CLOSERS or name in {"for", "while", "elseif", "else"}:
        return False, start, "control_flow" if name != "function" else "function"
    parser.pos = name_match.end()
    parser._skip_ignored()

    # Field assignment: Name.Key = <literal> / Name[<literal>] = <literal>.
    if not is_local and parser._peek() in {".", "["}:
        return _scan_field_assignment(text, start, name, parser, scan)

    if parser._peek() != "=" or text.startswith("==", parser.pos):
        return False, start, "call" if parser._peek() == "(" else "statement"
    parser.pos += 1
    parser._skip_ignored()
    if parser._peek() != "{":
        return False, start, "non_literal_assignment"
    try:
        value = parser._parse_table()
    except (LuaLiteralError, LuaNotLiteralError):
        return False, start, "non_literal_assignment"
    if is_local:
        locals_seen[name] = value
    else:
        scan.exports[name] = value
    return True, parser.pos, "data_table"


def _scan_field_assignment(
    text: str,
    start: int,
    name: str,
    parser: LuaLiteralParser,
    scan: ModuleScan,
) -> tuple[bool, int, str]:
    if name not in scan.exports or not isinstance(scan.exports[name], (dict, list)):
        return False, start, "statement"
    if parser._peek() == ".":
        parser.pos += 1
        key_match = _IDENTIFIER_RE.match(text, parser.pos)
        if not key_match:
            return False, start, "statement"
        key: Any = key_match.group(0)
        parser.pos = key_match.end()
    else:
        parser.pos += 1
        try:
            key = parser._parse_value()
        except (LuaLiteralError, LuaNotLiteralError):
            return False, start, "statement"
        parser._skip_ignored()
        if parser._peek() != "]":
            return False, start, "statement"
        parser.pos += 1
    parser._skip_ignored()
    if parser._peek() != "=" or text.startswith("==", parser.pos):
        return False, start, "statement"
    parser.pos += 1
    # Field assignments accept literals only: a bare name on the right-hand side
    # is metatable or alias plumbing (``X.__index = X``), not data.
    strict = LuaLiteralParser(text)
    strict.pos = parser.pos
    try:
        value = strict._parse_value()
    except (LuaLiteralError, LuaNotLiteralError):
        return False, start, "non_literal_field"
    target = scan.exports[name]
    if isinstance(target, list):
        if target:
            return False, start, "non_literal_field"
        target = {}
        scan.exports[name] = target
    target[str(key)] = value
    return True, strict.pos, "data_field"


def has_literal_scalar(value: Any) -> bool:
    """Whether *value* contains at least one concrete literal below its refs."""

    if isinstance(value, dict):
        if "ref" in value and set(value) <= {"ref", "value"}:
            return "value" in value
        if "call" in value and set(value) <= {"call", "args"}:
            return any(has_literal_scalar(arg) for arg in value["args"])
        return any(has_literal_scalar(item) for item in value.values())
    if isinstance(value, list):
        return any(has_literal_scalar(item) for item in value)
    return value is not None


def data_exports(scan: ModuleScan) -> dict[str, Any]:
    """Exports that actually carry data.

    An export is kept when it is a non-empty container holding at least one
    literal scalar.  Empty tables (``X = {}`` class shells) and tables made only
    of unresolved references are dropped, which is what separates a data file
    from a code module.
    """

    kept: dict[str, Any] = {}
    for name, value in scan.exports.items():
        if not isinstance(value, (dict, list)) or not value:
            continue
        if not has_literal_scalar(value):
            continue
        kept[name] = value
    return kept
