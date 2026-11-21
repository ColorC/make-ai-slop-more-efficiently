# [OMNI] origin=claude-code domain=omnicompany/core/remote ts=2026-10-17T00:00:00+08:00 type=lib status=active
# [OMNI] summary="omni remote: PowerShell 模板渲染、单引号字面量、here-string 与禁止编码载荷检查。"
"""PowerShell text helpers for ``omni remote``.

All remote code is produced from the ``ps/*.ps1`` templates in this package. Values are
only ever inserted as single-quoted PowerShell literals (``'`` doubled), here-strings, or
integers/booleans rendered here, so callers never hand-escape anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PS_DIR = Path(__file__).resolve().parent / "ps"
_TOKEN = re.compile(r"__OMNI_[A-Z_]+__")

#: Windows command lines top out at 32767 chars; leave room for the launcher, quoting
#: expansion of ``"`` and the node's own wrapping (rnode used the same budget).
INLINE_LIMIT = 24_000


class RemoteRefused(Exception):
    """A request refused locally before anything was sent. ``rule`` names the guard."""

    def __init__(self, rule: str, message: str, **detail: object) -> None:
        super().__init__(message)
        self.rule = rule
        self.detail = detail


def ps_quote(value: str | None) -> str:
    if value is None:
        return "$null"
    return "'" + str(value).replace("'", "''") + "'"


def ps_array(values: list[str] | tuple[str, ...]) -> str:
    return ", ".join(ps_quote(v) for v in values) if values else ""


def ps_bool(value: bool) -> str:
    return "$true" if value else "$false"


def here_string_safe(text: str) -> bool:
    """A single-quoted here-string ends at any line that starts with ``'@``."""
    return not any(line.startswith("'@") for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))


def here_string(text: str) -> str:
    if not here_string_safe(text):
        raise ValueError("text contains a line starting with '@ and cannot be embedded as a here-string")
    return "@'\n" + text + "\n'@"


def load_template(name: str) -> str:
    return (PS_DIR / name).read_text(encoding="utf-8")


def render(name: str, tokens: dict[str, str]) -> str:
    """Fill ``__OMNI_X__`` tokens of one template; every token must be supplied, none left over."""
    text = load_template(name)
    wanted = set(_TOKEN.findall(text))
    given = {f"__OMNI_{key}__" for key in tokens}
    missing = sorted(wanted - given)
    if missing:
        raise KeyError(f"template {name} missing tokens: {', '.join(missing)}")
    values = {f"__OMNI_{key}__": value for key, value in tokens.items()}
    # one pass: a value that itself contains "__OMNI_X__" (a user script, say) is never re-substituted
    return _TOKEN.sub(lambda match: values[match.group(0)], text)


def prelude(owned_roots: list[str], write_roots: list[str]) -> str:
    return render("common.ps1", {"OWNED_ROOTS": ps_array(owned_roots), "WRITE_ROOTS": ps_array(write_roots)})


# ── encoded-payload guard (Kaspersky nodes) ────────────────────────────────────

@dataclass(frozen=True)
class PayloadHit:
    rule: str
    offset: int
    excerpt: str


_FORBIDDEN = (
    ("encoded-command", re.compile(r"(?i)(?<![\w-])-(?:encodedcommand|enc|ec)(?=\s)")),
    ("base64-decode", re.compile(r"(?i)frombase64string")),
    ("invoke-expression", re.compile(r"(?i)\binvoke-expression\b|(?<![\w$-])iex(?![\w-])")),
    ("gzip-stream", re.compile(r"(?i)gzipstream|deflatestream")),
    ("base64-blob", re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")),
)


def scan_encoded_payload(text: str) -> list[PayloadHit]:
    hits = []
    for rule, pattern in _FORBIDDEN:
        match = pattern.search(text)
        if match:
            start = max(0, match.start() - 30)
            hits.append(PayloadHit(rule, match.start(), text[start: match.end() + 30].replace("\n", " ")))
    return hits


def assert_plain_payload(text: str, *, node: str, what: str) -> None:
    hits = scan_encoded_payload(text)
    if hits:
        first = hits[0]
        raise RemoteRefused(
            "forbid_encoded_payload",
            f"节点 {node} 的画像设置了 forbid_encoded_payload（卡巴斯基机器），{what} 里出现了"
            f"「{first.rule}」形态（偏移 {first.offset}：…{first.excerpt}…），什么都没下发。"
            "改成明文脚本：不要用 -EncodedCommand / FromBase64String / iex / GZip。",
            hits=[hit.__dict__ for hit in hits],
        )


def command_chars(text: str) -> int:
    """Rough command-line cost: every ``"`` is escaped once by the launcher."""
    return len(text) + text.count('"')
