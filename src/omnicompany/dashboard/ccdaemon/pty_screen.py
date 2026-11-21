"""Server-side terminal screen model for scale-insensitive PTY reattach.

The replay ring in ``pty_host`` is a raw ANSI tail with a byte cap. Its
validity as a reattach snapshot is O(history): once a long-running TUI's
status-line repaints evict the escape sequences that built the screen, the
tail can no longer reconstruct anything and a fresh viewer sees a torn or
blank terminal. The geometry-nudge fallback then depends on the guest TUI
cooperating — codex, for example, ignores nudges while busy.

This module removes both dependencies: the host feeds every output chunk
through a VT emulator (pyte) that maintains the *current* screen grid plus a
bounded scrollback, and can serialize that state into one self-contained ANSI
frame. Serving that frame on reattach is O(cols x rows) regardless of session
age — the tmux model.

Regression containment (healthy sessions must behave exactly as before):

- The model only exists for agent TUI providers; shells keep the raw ring,
  whose plain scrollback replay is already faithful.
- Any pyte failure (import, feed, render) marks the model dead and every
  caller falls back to the raw-ring path, i.e. today's behavior.
- The synthesized frame is only ever *served* when the raw replay is already
  truncated — the case that is guaranteed garbage today. Non-truncated
  sessions keep byte-identical snapshots.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Private-marker CSI such as ``ESC [ > 4 ; 2 m`` (XTMODKEYS; Claude Code sends
# it to enable modifyOtherKeys). pyte drops the ``>`` and applies the params as
# SGR 4;2, latching underline + dim onto every later cell, blanks included —
# replayed frames then draw each empty row as a full-width underline. These
# sequences never change screen contents, so the model skips them. ``?``
# (DEC private modes) is left to pyte, which parses it correctly.
_PRIVATE_CSI_RE = re.compile(r"\x1b\[[<=>][0-9;:]*[ -/]*[@-~]")
# A chunk may end inside such a sequence; hold that tail for the next feed.
_PRIVATE_CSI_TAIL_RE = re.compile(r"\x1b(?:\[(?:[<=>][0-9;:]*[ -/]*)?)?\Z")
_SGR_RE = re.compile(r"\x1b\[([0-9;:]*)m")


def _without_underline(params: str) -> str:
    """SGR params minus underline on (4, 4:x, 21); extended colors kept whole."""
    parts = params.split(";") if params else [""]
    kept: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if part in ("38", "48", "58") and i + 1 < len(parts):
            width = 3 if parts[i + 1] == "5" else 5 if parts[i + 1] == "2" else 1
            kept.extend(parts[i:i + width])
            i += width
            continue
        if part not in ("4", "21") and not part.startswith("4:"):
            kept.append(part)
        i += 1
    return ";".join(kept)


def strip_underline_sgr(frame: str) -> str:
    """Drop underline from every SGR in a rendered frame.

    Browser terminals already strip underline from live output; frames from
    screen models that latched a bogus underline (see ``_PRIVATE_CSI_RE``)
    must not bring it back on reattach.
    """
    def repl(match: re.Match[str]) -> str:
        params = match.group(1)
        kept = _without_underline(params)
        if kept == params:
            return match.group(0)
        return f"\x1b[{kept}m" if kept else ""

    return _SGR_RE.sub(repl, frame)

SCREEN_MODEL_ENABLED = (
    (os.environ.get("OMNI_PTY_SCREEN_MODEL") or "1").strip().lower()
    not in {"0", "false", "no", "off"}
)
# Scrolled-off lines kept in the emulator (memory bound: lines x cols cells).
SCREEN_HISTORY_LINES = int(os.environ.get("OMNI_PTY_SCREEN_HISTORY_LINES") or 1000)
# Scrollback lines included in a rendered frame (payload bound). History is
# rendered as plain text; only the live grid carries colors/attributes.
RENDER_HISTORY_LINES = int(os.environ.get("OMNI_PTY_SCREEN_RENDER_HISTORY_LINES") or 300)

# These DEC private modes belong to input transport, not screen cells. Losing
# them on a synthesized repaint leaves a fullscreen guest with no wheel input.
_MOUSE_MODES = (9, 1000, 1002, 1003, 1005, 1006, 1015, 1016)
# These DEC private modes affect keyboard/focus/paste input delivery rather
# than screen cells. A synthesized repaint must restore them just like mouse
# tracking, otherwise a fullscreen guest can stop drawing after reattach.
_INPUT_MODES = (1, 1004, 2004)

# pyte names basic colors after their historical VT names ("brown" is SGR 33).
_FG_CODES = {
    "black": "30", "red": "31", "green": "32", "brown": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "white": "37",
    "brightblack": "90", "brightred": "91", "brightgreen": "92",
    "brightbrown": "93", "brightblue": "94", "brightmagenta": "95",
    "brightcyan": "96", "brightwhite": "97",
}
_BG_CODES = {
    "black": "40", "red": "41", "green": "42", "brown": "43",
    "blue": "44", "magenta": "45", "cyan": "46", "white": "47",
    "brightblack": "100", "brightred": "101", "brightgreen": "102",
    "brightbrown": "103", "brightblue": "104", "brightmagenta": "105",
    "brightcyan": "106", "brightwhite": "107",
}

_DEFAULT_ATTR = ("default", "default", False, False, False, False, False)


def _is_hex_color(value: str) -> bool:
    if len(value) != 6:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _sgr(attr: tuple[Any, ...]) -> str:
    fg, bg, bold, italics, underscore, reverse, blink = attr
    codes = ["0"]
    if bold:
        codes.append("1")
    if italics:
        codes.append("3")
    if underscore:
        codes.append("4")
    if blink:
        codes.append("5")
    if reverse:
        codes.append("7")
    if isinstance(fg, str) and fg != "default":
        named = _FG_CODES.get(fg)
        if named:
            codes.append(named)
        elif _is_hex_color(fg):
            codes += ("38", "2", str(int(fg[0:2], 16)), str(int(fg[2:4], 16)), str(int(fg[4:6], 16)))
    if isinstance(bg, str) and bg != "default":
        named = _BG_CODES.get(bg)
        if named:
            codes.append(named)
        elif _is_hex_color(bg):
            codes += ("48", "2", str(int(bg[0:2], 16)), str(int(bg[2:4], 16)), str(int(bg[4:6], 16)))
    return "\x1b[" + ";".join(codes) + "m"


def _cell_attr(cell: Any) -> tuple[Any, ...]:
    return (
        cell.fg, cell.bg, cell.bold, cell.italics,
        cell.underscore, cell.reverse, cell.blink,
    )


def _line_text(line: Any, cols: int) -> str:
    """Plain text of one row; wide-char continuation cells hold ``""``."""
    return "".join(line[x].data for x in range(cols)).rstrip()


def _row_ansi(row: Any, cols: int) -> str:
    """One grid row as ANSI with minimal SGR runs, trailing blanks trimmed."""
    last = -1
    for x in range(cols):
        cell = row[x]
        if cell.data not in ("", " ") or _cell_attr(cell) != _DEFAULT_ATTR:
            last = x
    parts: list[str] = []
    current: tuple[Any, ...] | None = None
    for x in range(last + 1):
        cell = row[x]
        data = cell.data
        if data == "":
            continue  # continuation cell of a wide character
        attr = _cell_attr(cell)
        if attr != current:
            parts.append(_sgr(attr))
            current = attr
        parts.append(data)
    if current is not None and current != _DEFAULT_ATTR:
        parts.append("\x1b[0m")
    return "".join(parts)


class ScreenModel:
    """Bounded VT emulator fed beside the replay ring; renders on demand."""

    def __init__(self, cols: int, rows: int) -> None:
        import pyte

        self._pyte = pyte
        self._screen = pyte.HistoryScreen(cols, rows, history=SCREEN_HISTORY_LINES)
        self._stream = pyte.Stream(self._screen)
        self._carry = ""
        self.dead = False

    def _kill(self, stage: str) -> None:
        if not self.dead:
            self.dead = True
            logger.warning("PTY screen model died during %s; falling back to raw replay", stage, exc_info=True)

    def feed(self, chunk: str) -> None:
        if self.dead:
            return
        try:
            data = self._carry + chunk
            self._carry = ""
            tail = _PRIVATE_CSI_TAIL_RE.search(data)
            if tail is not None:
                self._carry = data[tail.start():]
                data = data[:tail.start()]
            self._stream.feed(_PRIVATE_CSI_RE.sub("", data))
        except Exception:
            self._kill("feed")

    def resize(self, cols: int, rows: int) -> None:
        if self.dead:
            return
        try:
            self._screen.resize(rows, cols)
        except Exception:
            self._kill("resize")

    def render_frame(self) -> str | None:
        """Self-contained ANSI frame: scrollback tail + grid + cursor state.

        Feeding the frame into a freshly reset terminal of the same geometry
        reproduces the emulator's display and cursor (see the roundtrip test).
        Returns ``None`` when the model is unavailable.
        """
        if self.dead:
            return None
        try:
            return self._render()
        except Exception:
            self._kill("render")
            return None

    def _render(self) -> str:
        screen = self._screen
        cols = screen.columns
        rows = screen.lines
        parts: list[str] = ["\x1b[0m"]
        history = list(screen.history.top)
        for line in history[-RENDER_HISTORY_LINES:]:
            # A history row is emitted as plain text. Explicitly terminate
            # attributes before its line ending so a provider's unterminated
            # SGR (notably underline) can never bleed into the next row when
            # xterm replays the synthesized frame.
            parts.append("\x1b[0m")
            parts.append(_line_text(line, cols))
            parts.append("\r\n")
        parts.append("\r\n".join(_row_ansi(screen.buffer[y], cols) + "\x1b[0m" for y in range(rows)))
        parts.append("\x1b[0m")
        parts.append(f"\x1b[{screen.cursor.y + 1};{screen.cursor.x + 1}H")
        visible = self._pyte.modes.DECTCEM in screen.mode
        parts.append("\x1b[?25h" if visible else "\x1b[?25l")
        for mode in _MOUSE_MODES:
            # pyte stores DEC private modes shifted to distinguish ANSI modes.
            if mode << 5 in screen.mode:
                parts.append(f"\x1b[?{mode}h")
        for mode in _INPUT_MODES:
            if mode << 5 in screen.mode:
                parts.append(f"\x1b[?{mode}h")
        return "".join(parts)


def create_screen_model(cols: int, rows: int, provider: str) -> ScreenModel | None:
    """Factory. ``None`` (feature off) unless *provider* is an agent TUI and
    pyte is importable; shells and missing-dependency environments keep the
    raw-ring behavior exactly."""
    if not SCREEN_MODEL_ENABLED:
        return None
    try:
        from .provider_identity import PTY_AGENT_PROVIDER_IDS
    except Exception:
        return None
    if provider not in PTY_AGENT_PROVIDER_IDS:
        return None
    try:
        return ScreenModel(cols, rows)
    except Exception:
        logger.warning("PTY screen model unavailable (pyte missing?); raw replay only", exc_info=True)
        return None
