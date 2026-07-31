"""Lightweight console bootstrap for the ``omni`` command.

Lazily loads the Click CLI.  UTF-8 stream reconfiguration is applied
before any command output so Windows consoles render CJK correctly.
"""

from __future__ import annotations

import sys


def _configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - console encoding is best-effort
            pass


def main(argv: list[str] | tuple[str, ...] | None = None):
    """Enter the full Click CLI."""

    _configure_utf8_streams()
    from .main import cli

    arguments = list(sys.argv[1:] if argv is None else argv)
    return cli.main(args=arguments, prog_name="omni", standalone_mode=True)


if __name__ == "__main__":
    raise SystemExit(main())
