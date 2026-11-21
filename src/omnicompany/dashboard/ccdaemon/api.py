# [OMNI] origin=arch-ratchet-surgery ts=2026-08-17 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.public_facade_api.py"
"""ccdaemon 公共契约面（facade）。

packages/** 与 cli/**（除 ccdaemon 自己的管理面 cc.py / guardian）访问 ccdaemon
的唯一合法入口；arch_gate 只对本模块开白名单。全部懒加载（PEP 562），
不 import 本模块以外的任何重量级依赖，CLI 首启零额外开销。

出口一律公共名。内部实现改名/搬家时只需要改这里的映射，消费方零改动——
这就是"私有符号跨层引用"问题的契约化解法（ARCH-RATCHET-SURGERY P2.2）。
"""
from __future__ import annotations

import importlib
from typing import Any

_PKG = __name__.rsplit(".", 1)[0]  # omnicompany.dashboard.ccdaemon

# public_name -> (submodule, real_attr)
_FUNCS: dict[str, tuple[str, str]] = {
    # pty — 会话/PTY 管理与共享元数据登记表 (data/cc_sessions.json)
    "get_manager": ("pty", "get_manager"),
    "update_meta_field": ("pty", "update_meta_field"),
    "read_meta_store": ("pty", "_read_meta_store"),
    "read_meta_store_with_archive": ("pty", "_read_meta_store_with_archive"),
    "write_meta_store": ("pty", "_write_meta_store"),
    "meta_store_path": ("pty", "_meta_store_path"),
    "pty_host_dir": ("pty", "_pty_host_dir"),
    "conversation_jsonl_for": ("pty", "_conversation_jsonl_for"),
    "claude_jsonl_for": ("pty", "_claude_jsonl_for"),
    # chat — OmniChat 会话
    "get_chat_manager": ("chat", "get_chat_manager"),
    "CreateChatSessionBody": ("chat", "CreateChatSessionBody"),
    "create_session": ("chat", "create_session"),
    # import_routes — 各 provider 会话转录扫描
    "scan_claude": ("import_routes", "_scan_claude"),
    "scan_codex": ("import_routes", "_scan_codex"),
    "scan_kiro": ("import_routes", "_scan_kiro"),
    "scan_qoder": ("import_routes", "_scan_qoder"),
    "scan_all_now": ("import_routes", "_scan_all_now"),
    "tail_lines": ("import_routes", "_tail_lines"),
    "extract_transcript": ("import_routes", "_extract_transcript"),
    # provider_runtime / provider_identity / lifecycle
    "ProviderRuntimeState": ("provider_runtime", "ProviderRuntimeState"),
    "CLI_AGENT_PROVIDER_IDS": ("provider_identity", "CLI_AGENT_PROVIDER_IDS"),
    "canonical_cli_provider_id": ("provider_identity", "canonical_cli_provider_id"),
    # 账号根目录解析: codex_2 / claude_2 是"另一个 CODEX_HOME / CLAUDE_CONFIG_DIR",
    # 下层读转录、读凭据、算额度都要按账号分流, 只能走这个契约面。
    "claude_home_for": ("provider_identity", "claude_home_for"),
    "codex_home_for": ("provider_identity", "codex_home_for"),
    "read_status": ("lifecycle", "read_status"),
}

# public_name -> submodule（整模块出口）
_MODULES: dict[str, str] = {
    "lifecycle": "lifecycle",
    "provider_identity": "provider_identity",
    "hooks_shared": "hooks._shared",
}

__all__ = sorted([*_FUNCS, *_MODULES])


def __getattr__(name: str) -> Any:
    if name in _FUNCS:
        submodule, attr = _FUNCS[name]
        return getattr(importlib.import_module(f"{_PKG}.{submodule}"), attr)
    if name in _MODULES:
        return importlib.import_module(f"{_PKG}.{_MODULES[name]}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return __all__
