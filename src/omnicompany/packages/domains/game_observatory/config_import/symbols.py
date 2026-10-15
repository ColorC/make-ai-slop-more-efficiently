"""Static lookup of ``Prefix.Name`` references against Lua definition files.

Decompiled config tables refer to shared enumerations such as
``ItemType.GroceryBag04`` or ``UIPrefabConfigName.ItemDigView_mineral``.  Those
names are defined elsewhere in the same source tree as top-level
``Prefix = { Name = <literal>, ... }`` tables.  This module indexes those
definitions by reading the files, never by executing them.

The index is built lazily: one cheap regular-expression pass records which file
declares which top-level table, and a table's members are extracted only when a
reference to it is actually seen.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .lua_literal import LuaLiteralError, LuaLiteralParser, LuaNotLiteralError


# A top-level table declaration in column one, e.g. ``ItemType = {``.
_DECLARATION_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{", re.MULTILINE)


class SymbolTable:
    """Resolve dotted Lua names to the literals they were declared with."""

    def __init__(self, source: Path | str) -> None:
        self.source = Path(source)
        self._declarations: dict[str, Path] | None = None
        self._members: dict[str, dict[str, Any]] = {}

    def _index(self) -> dict[str, Path]:
        if self._declarations is None:
            declarations: dict[str, Path] = {}
            for path in sorted(
                self.source.rglob("*.lua"), key=lambda item: item.as_posix()
            ):
                try:
                    text = path.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError):
                    continue
                for match in _DECLARATION_RE.finditer(text):
                    declarations.setdefault(match.group(1), path)
            self._declarations = declarations
        return self._declarations

    def _table(self, prefix: str) -> dict[str, Any]:
        if prefix in self._members:
            return self._members[prefix]
        members: dict[str, Any] = {}
        path = self._index().get(prefix)
        if path is not None:
            members = _read_table_members(path, prefix)
        self._members[prefix] = members
        return members

    def resolve(self, name: str) -> tuple[bool, Any]:
        """Look up a dotted name; return ``(found, value)``."""

        prefix, _, member = name.rpartition(".")
        if not prefix or "." in prefix:
            return False, None
        value = self._table(prefix).get(member, _MISSING)
        if value is _MISSING:
            return False, None
        return True, value


_MISSING = object()


def _read_table_members(path: Path, prefix: str) -> dict[str, Any]:
    """Extract the scalar members of the top-level ``prefix`` table in *path*."""

    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return {}
    for match in _DECLARATION_RE.finditer(text):
        if match.group(1) != prefix:
            continue
        parser = LuaLiteralParser(text, permissive=True, resolver=None)
        parser.pos = match.end() - 1
        try:
            value = parser._parse_table()
        except (LuaLiteralError, LuaNotLiteralError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            key: item
            for key, item in value.items()
            if isinstance(item, (int, float, str, bool))
        }
    return {}
