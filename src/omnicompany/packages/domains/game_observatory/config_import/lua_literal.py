"""Parser for the literal subset used by decompiled Lua config tables.

The strict mode accepts no names, calls, operators, or statements other than one
top-level ``return``.  The permissive mode additionally accepts dotted name
references and constructor-style calls, rendering them as data nodes.  Neither
mode ever executes source text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_NUMBER_RE = re.compile(
    r"[+-]?(?:0[xX][0-9A-Fa-f]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)"
)
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class LuaLiteralError(ValueError):
    """A malformed Lua literal with a one-based source location."""

    def __init__(self, line: int, col: int, message: str) -> None:
        self.line = line
        self.col = col
        self.message = message
        super().__init__(f"line {line}, col {col}: {message}")


class LuaNotLiteralError(LuaLiteralError):
    """Source that is valid or plausible Lua, but not a literal-only return."""


@dataclass(frozen=True)
class LuaParseStats:
    """Counters produced while parsing one source file."""

    nil_dropped: int = 0
    resolved_refs: int = 0
    unresolved_refs: int = 0
    calls: int = 0


def make_ref(name: str, resolved: bool, value: Any = None) -> dict[str, Any]:
    """Build the canonical node for a Lua name reference.

    Resolved references keep both the source spelling and the looked-up literal
    so a reader can audit the substitution; unresolved ones keep only the
    spelling.  The shape is uniform across every permissive table.
    """

    if resolved:
        return {"ref": name, "value": value}
    return {"ref": name}


def make_call(name: str, args: list[Any]) -> Any:
    """Build the node for a constructor-style call in a data table."""

    if name == "CSColor" and 3 <= len(args) <= 4:
        node = {"r": args[0], "g": args[1], "b": args[2]}
        if len(args) == 4:
            node["a"] = args[3]
        return node
    if name in {"Vector2", "Vector3"} and len(args) in {2, 3}:
        return list(args)
    return {"call": name, "args": list(args)}


class LuaLiteralParser:
    """Recursive-descent parser for a single Lua return-table literal."""

    def __init__(
        self,
        text: str,
        *,
        permissive: bool = False,
        resolver: Any = None,
    ) -> None:
        self.text = text
        self.pos = 0
        self.nil_dropped = 0
        self.permissive = permissive
        self.resolver = resolver
        self.resolved_refs = 0
        self.unresolved_refs = 0
        self.calls = 0

    @property
    def stats(self) -> LuaParseStats:
        return LuaParseStats(
            nil_dropped=self.nil_dropped,
            resolved_refs=self.resolved_refs,
            unresolved_refs=self.unresolved_refs,
            calls=self.calls,
        )

    def parse(self) -> Any:
        self._skip_ignored()
        if not self._consume_keyword("return"):
            self._raise_not_literal("expected one top-level 'return' statement")
        self._skip_ignored()
        if self._peek() != "{":
            self._raise_not_literal("top-level return value is not a table literal")
        value = self._parse_table()
        self._skip_ignored()
        if self._peek() == ";":
            self.pos += 1
            self._skip_ignored()
        if self.pos != len(self.text):
            self._raise_not_literal("additional statement or expression after table literal")
        return value

    def _parse_value(self) -> Any:
        self._skip_ignored()
        char = self._peek()
        if not char:
            self._error("expected a literal value")
        if char == "{":
            return self._parse_table()
        if char in {'"', "'"}:
            return self._parse_quoted_string()
        if char == "[" and self._long_bracket_level(self.pos) is not None:
            return self._parse_long_string()
        for word, value in (("true", True), ("false", False), ("nil", None)):
            if self._consume_keyword(word):
                return value
        number = _NUMBER_RE.match(self.text, self.pos)
        if number:
            token = number.group(0)
            self.pos = number.end()
            if "x" in token.lower():
                sign = -1 if token.startswith("-") else 1
                digits = token.lstrip("+-")[2:]
                return sign * int(digits, 16)
            if "." in token or "e" in token.lower():
                return float(token)
            return int(token, 10)
        identifier = _IDENTIFIER_RE.match(self.text, self.pos)
        if identifier:
            if self.permissive:
                return self._parse_name_expression()
            self._raise_not_literal("identifier or expression is not a literal value")
        if self._looks_non_literal_token():
            self._raise_not_literal("operator, call, or expression is not a literal value")
        self._error("expected a literal value")

    def _parse_name_expression(self) -> Any:
        """Parse a dotted name, optionally followed by a call argument list."""

        name = self._parse_dotted_name()
        self._skip_ignored()
        if self._peek() == "(":
            self.pos += 1
            args: list[Any] = []
            self._skip_ignored()
            if self._peek() == ")":
                self.pos += 1
            else:
                while True:
                    args.append(self._parse_value())
                    self._skip_ignored()
                    if self._peek() == ",":
                        self.pos += 1
                        continue
                    self._expect(")")
                    break
            self.calls += 1
            return make_call(name, args)
        found, value = self._resolve(name)
        if found:
            self.resolved_refs += 1
        else:
            self.unresolved_refs += 1
        return make_ref(name, found, value)

    def _parse_dotted_name(self) -> str:
        parts: list[str] = []
        while True:
            match = _IDENTIFIER_RE.match(self.text, self.pos)
            if not match:
                self._error("expected a name")
            parts.append(match.group(0))
            self.pos = match.end()
            if self._peek() == "." and not self.text.startswith("..", self.pos):
                self.pos += 1
                continue
            return ".".join(parts)

    def _resolve(self, name: str) -> tuple[bool, Any]:
        if self.resolver is None:
            return False, None
        return self.resolver(name)

    def _parse_table(self) -> Any:
        self._expect("{")
        entries: list[tuple[Any, Any]] = []
        array_index = 1
        has_explicit_key = False

        while True:
            self._skip_ignored()
            if self._peek() == "}":
                self.pos += 1
                break
            if not self._peek():
                self._error("expected '}' to close table")

            key: Any
            if self._peek() == "[" and self._long_bracket_level(self.pos) is None:
                has_explicit_key = True
                self.pos += 1
                key = self._parse_value()
                self._skip_ignored()
                self._expect("]")
                self._skip_ignored()
                self._expect("=")
                value = self._parse_value()
            else:
                identifier = _IDENTIFIER_RE.match(self.text, self.pos)
                if identifier:
                    saved = self.pos
                    candidate = identifier.group(0)
                    self.pos = identifier.end()
                    self._skip_ignored()
                    if self._peek() == "=":
                        has_explicit_key = True
                        key = candidate
                        self.pos += 1
                        value = self._parse_value()
                    else:
                        self.pos = saved
                        key = array_index
                        array_index += 1
                        value = self._parse_value()
                else:
                    key = array_index
                    array_index += 1
                    value = self._parse_value()
            entries.append((key, value))

            self._skip_ignored()
            if self._peek() in {",", ";"}:
                self.pos += 1
                continue
            if self._peek() == "}":
                continue
            if not self._peek():
                self._error("expected '}' to close table")
            if self._looks_non_literal_token():
                self._raise_not_literal("operator, call, or expression is not a literal value")
            self._error("expected ',', ';', or '}' after table field")

        if not has_explicit_key:
            return [value for _, value in entries]

        result: dict[str, Any] = {}
        for key, value in entries:
            normalized = self._normalize_key(key)
            if value is None:
                result.pop(normalized, None)
                self.nil_dropped += 1
            else:
                result[normalized] = value
        return result

    def _parse_quoted_string(self) -> str:
        quote = self._peek()
        self.pos += 1
        output = bytearray()
        escapes = {
            "a": "\a",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "v": "\v",
            "\\": "\\",
            '"': '"',
            "'": "'",
        }

        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == quote:
                self.pos += 1
                try:
                    return output.decode("utf-8")
                except UnicodeDecodeError as exc:
                    self._error(f"string bytes are not valid UTF-8: {exc}")
            if char in "\r\n":
                self._error("unterminated quoted string")
            if char != "\\":
                output.extend(char.encode("utf-8"))
                self.pos += 1
                continue

            self.pos += 1
            escaped = self._peek()
            if not escaped:
                self._error("unterminated string escape")
            if escaped.isdigit():
                end = self.pos
                while end < len(self.text) and end < self.pos + 3 and self.text[end].isdigit():
                    end += 1
                value = int(self.text[self.pos:end], 10)
                if value > 255:
                    self._error("decimal string escape exceeds one byte")
                output.append(value)
                self.pos = end
                continue
            if escaped in escapes:
                output.extend(escapes[escaped].encode("utf-8"))
                self.pos += 1
                continue
            if escaped == "x":
                digits = self.text[self.pos + 1 : self.pos + 3]
                if len(digits) != 2 or any(ch not in "0123456789abcdefABCDEF" for ch in digits):
                    self._error("invalid hexadecimal string escape")
                output.append(int(digits, 16))
                self.pos += 3
                continue
            if escaped == "z":
                self.pos += 1
                while self._peek() and self._peek().isspace():
                    self.pos += 1
                continue
            if escaped == "\r":
                self.pos += 1
                if self._peek() == "\n":
                    self.pos += 1
                output.append(ord("\n"))
                continue
            if escaped == "\n":
                self.pos += 1
                output.append(ord("\n"))
                continue
            self._error(f"unsupported string escape '\\{escaped}'")
        self._error("unterminated quoted string")

    def _parse_long_string(self) -> str:
        level = self._long_bracket_level(self.pos)
        if level is None:
            self._error("expected long string")
        opening_length = level + 2
        self.pos += opening_length
        closing = "]" + "=" * level + "]"
        end = self.text.find(closing, self.pos)
        if end < 0:
            self._error("unterminated long string")
        value = self.text[self.pos:end]
        if value.startswith("\r\n"):
            value = value[2:]
        elif value.startswith("\n"):
            value = value[1:]
        self.pos = end + len(closing)
        return value

    def _skip_ignored(self) -> None:
        while True:
            while self._peek() and (self._peek().isspace() or self._peek() == "\ufeff"):
                self.pos += 1
            if not self.text.startswith("--", self.pos):
                return
            comment_start = self.pos
            self.pos += 2
            level = self._long_bracket_level(self.pos)
            if level is None:
                newline = self.text.find("\n", self.pos)
                self.pos = len(self.text) if newline < 0 else newline + 1
                continue
            self.pos += level + 2
            closing = "]" + "=" * level + "]"
            end = self.text.find(closing, self.pos)
            if end < 0:
                self.pos = comment_start
                self._error("unterminated block comment")
            self.pos = end + len(closing)

    def _consume_keyword(self, keyword: str) -> bool:
        end = self.pos + len(keyword)
        if self.text[self.pos:end] != keyword:
            return False
        if end < len(self.text) and (
            self.text[end].isalnum() or self.text[end] == "_"
        ):
            return False
        self.pos = end
        return True

    def _expect(self, token: str) -> None:
        if not self.text.startswith(token, self.pos):
            self._error(f"expected '{token}'")
        self.pos += len(token)

    def _peek(self) -> str:
        if self.pos >= len(self.text):
            return ""
        return self.text[self.pos]

    def _long_bracket_level(self, pos: int) -> int | None:
        if pos >= len(self.text) or self.text[pos] != "[":
            return None
        cursor = pos + 1
        while cursor < len(self.text) and self.text[cursor] == "=":
            cursor += 1
        if cursor < len(self.text) and self.text[cursor] == "[":
            return cursor - pos - 1
        return None

    def _normalize_key(self, key: Any) -> str:
        if isinstance(key, dict) and "ref" in key:
            resolved = key.get("value")
            key = key["ref"] if resolved is None else resolved
        if key is None or isinstance(key, (dict, list)):
            self._error("table key must be a string, number, or boolean")
        if key is True:
            return "true"
        if key is False:
            return "false"
        return str(key)

    def _looks_non_literal_token(self) -> bool:
        return self._peek() in "+-*/%^#().:<>~" or bool(
            _IDENTIFIER_RE.match(self.text, self.pos)
        )

    def _location(self) -> tuple[int, int]:
        line = self.text.count("\n", 0, self.pos) + 1
        last_newline = self.text.rfind("\n", 0, self.pos)
        col = self.pos + 1 if last_newline < 0 else self.pos - last_newline
        return line, col

    def _error(self, message: str) -> None:
        line, col = self._location()
        raise LuaLiteralError(line, col, message)

    def _raise_not_literal(self, message: str) -> None:
        line, col = self._location()
        raise LuaNotLiteralError(line, col, message)


def parse_lua_permissive_return(text: str, resolver: Any = None) -> tuple[Any, LuaParseStats]:
    """Parse ``return <table>`` allowing name references and constructor calls."""

    parser = LuaLiteralParser(text, permissive=True, resolver=resolver)
    value = parser.parse()
    return value, parser.stats


def parse_lua_return(text: str) -> Any:
    """Parse one literal-only ``return { ... }`` Lua source file."""

    return LuaLiteralParser(text).parse()


def parse_lua_return_with_stats(text: str) -> tuple[Any, LuaParseStats]:
    """Parse a source file and return its value plus per-file counters."""

    parser = LuaLiteralParser(text)
    value = parser.parse()
    return value, parser.stats
