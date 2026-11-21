"""Small, explicit asyncio executor pools for long-lived dashboard processes.

Python sizes the implicit pool from the machine CPU count and never shrinks
workers once a burst has created them.  On a 24-core workstation that means
28 retained threads in every detached PTY host.  These services only offload
short filesystem, sqlite and ConPTY calls, so a small bounded pool is both
enough and materially cheaper when many sessions are open.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor


def executor_workers(env_name: str, default: int) -> int:
    """Resolve a bounded worker count, tolerating a malformed override."""

    raw = os.environ.get(env_name)
    try:
        workers = int(raw) if raw is not None else int(default)
    except (TypeError, ValueError):
        workers = int(default)
    return max(2, min(16, workers))


def configure_default_executor(
    loop: asyncio.AbstractEventLoop,
    *,
    env_name: str,
    default_workers: int,
    thread_name_prefix: str,
) -> ThreadPoolExecutor:
    """Install and return a bounded executor for one long-lived event loop."""

    executor = ThreadPoolExecutor(
        max_workers=executor_workers(env_name, default_workers),
        thread_name_prefix=thread_name_prefix,
    )
    loop.set_default_executor(executor)
    return executor
