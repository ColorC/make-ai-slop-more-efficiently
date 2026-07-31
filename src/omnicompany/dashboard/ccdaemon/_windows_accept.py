"""Keep a Windows Proactor listener alive after transient AcceptEx failures.

On Python 3.12, a client that resets during ``AcceptEx`` can surface WinError
64. ``BaseProactorEventLoop._start_serving`` treats every accept ``OSError`` as
fatal and closes the listening socket, leaving the uvicorn process alive but
unreachable. Dashboard reconnect bursts make that race observable.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable


_TRANSIENT_ACCEPT_WINERRORS = {64}  # ERROR_NETNAME_DELETED
_installed = False
_original_accept: Callable[..., Any] | None = None


def _is_transient_accept_error(exc: BaseException | None) -> bool:
    return (
        isinstance(exc, OSError)
        and getattr(exc, "winerror", None) in _TRANSIENT_ACCEPT_WINERRORS
    )


def install_resilient_accept() -> bool:
    """Patch IocpProactor.accept once; return whether the patch is active."""
    global _installed, _original_accept
    if os.name != "nt":
        return False
    if _installed:
        return True

    from asyncio import windows_events

    original = windows_events.IocpProactor.accept
    _original_accept = original

    def resilient_accept(self: Any, listener: Any) -> asyncio.Future[Any]:
        loop = self._loop
        result: asyncio.Future[Any] = loop.create_future()
        current: list[asyncio.Future[Any] | None] = [None]

        def attempt() -> None:
            if result.done() or listener.fileno() < 0 or loop.is_closed():
                if not result.done():
                    result.cancel()
                return
            try:
                pending = original(self, listener)
            except OSError as exc:
                if _is_transient_accept_error(exc):
                    loop.call_later(0.01, attempt)
                else:
                    result.set_exception(exc)
                return
            current[0] = pending

            def completed(future: asyncio.Future[Any]) -> None:
                if result.done():
                    return
                try:
                    accepted = future.result()
                except asyncio.CancelledError:
                    result.cancel()
                except OSError as exc:
                    if (
                        _is_transient_accept_error(exc)
                        and listener.fileno() >= 0
                        and not loop.is_closed()
                    ):
                        loop.call_later(0.01, attempt)
                    else:
                        result.set_exception(exc)
                except BaseException as exc:  # pragma: no cover - defensive
                    result.set_exception(exc)
                else:
                    result.set_result(accepted)

            pending.add_done_callback(completed)

        def cancel_pending(future: asyncio.Future[Any]) -> None:
            pending = current[0]
            if future.cancelled() and pending is not None and not pending.done():
                pending.cancel()

        result.add_done_callback(cancel_pending)
        attempt()
        return result

    windows_events.IocpProactor.accept = resilient_accept
    _installed = True
    return True


def install_exception_filter(loop: asyncio.AbstractEventLoop) -> None:
    """Hide only the internal accept helper's duplicate transient error."""
    if os.name != "nt" or getattr(loop, "_omni_accept_filter", False):
        return
    previous = loop.get_exception_handler()

    def handler(active_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        future = context.get("future")
        coro_name = ""
        if future is not None:
            try:
                coro_name = future.get_coro().__qualname__
            except (AttributeError, RuntimeError):
                pass
        if _is_transient_accept_error(exc) and "accept_coro" in coro_name:
            return
        if previous is not None:
            previous(active_loop, context)
        else:
            active_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)
    setattr(loop, "_omni_accept_filter", True)

