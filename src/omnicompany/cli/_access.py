# [OMNI] origin=ai-ide ts=2026-05-25 type=infra
# [OMNI] material_id="material:cli.access_control.caller_decorator.py"
"""omni cli caller-based access control.

用户原话 (2026-05-25):
> 需要一个注册机制, 不能被多方使用, 也不能被 subagent 使用. 可以被外部或者总控
> agent 使用.

落实: 每个 cli 命令通过装饰器声明 access tier; 实际 caller 由 OMNI_CLI_CALLER
环境变量传递, 默认 'external' (用户终端).

三档 caller:
- external: 用户直接终端调 (默认)
- controller: BOSS SIGHT 总控 agent 通过 Bash tool 调 (ccdaemon 给该 session 子进程
  环境注入 OMNI_CLI_CALLER=controller)
- subagent: subagent worker (claude_code/codex/omni_agent 跑的 session) 通过 Bash
  调; 某些命令 (例 worker spawn/fork — 防递归无限 spawn) 显式不开放给 subagent

环境变量 OMNI_CLI_CALLER 不写 / 设非法值 → 当 external 处理 (最严档进入受限命令时
显式 deny).
"""

from __future__ import annotations

import os
from functools import wraps
from typing import Iterable

import click

from omnicompany.core.caller_identity import (
    CALLER_ENV,
    DEFAULT_CALLER,
    KNOWN_CALLERS,
    normalize_caller,
)


def current_caller() -> str:
    """读 OMNI_CLI_CALLER, 不在已知集合时 fallback external."""
    return normalize_caller(os.environ.get(CALLER_ENV))


def access(*, allow: Iterable[str]):
    """装饰器: 限制 caller. allow 是 caller 名 set/list.

    用法:
        @access(allow={"external", "controller"})
        @click.command()
        def cmd_worker_spawn(): ...

    caller 不在 allow 集合 → click.ClickException + exit code 2 (CLI 规范化拒绝).
    """
    allowed = frozenset(allow)
    unknown = allowed - KNOWN_CALLERS
    if unknown:
        raise ValueError(f"unknown caller(s) in @access(allow=...): {unknown}")

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            caller = current_caller()
            if caller not in allowed:
                raise click.ClickException(
                    f"access denied: caller={caller!r}, this command requires "
                    f"caller in {sorted(allowed)}. "
                    f"(set OMNI_CLI_CALLER env var to declare caller identity)"
                )
            return fn(*args, **kwargs)
        return wrapper
    return deco


# 常用语法糖
external_or_controller = access(allow={"external", "controller"})
"""external 或 controller 可调 (subagent 不能). 默认给 spawn / fork / dispatch 类."""

any_caller = access(allow=KNOWN_CALLERS)
"""三档都能调. 用于纯查询 / 列表类命令 (无副作用)."""


__all__ = [
    "CALLER_ENV", "KNOWN_CALLERS", "DEFAULT_CALLER",
    "current_caller", "access",
    "external_or_controller", "any_caller",
]
