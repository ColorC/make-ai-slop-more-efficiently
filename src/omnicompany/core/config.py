# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:omnicompany.core.config.path_resolver.engine.py"
"""omnicompany.core.config — 统一配置解析（基础设施）

所有 DB 路径、环境变量、管线默认参数的唯一解析入口。

## Project root resolution contract

Data MUST live under `<omnicompany_repo_root>/data/<domain>/`.

The resolution order is:
1. `OMNICOMPANY_DB_DIR` env var (absolute path preferred)
2. Walk up from THIS FILE's location to find the omnicompany package root
   (the directory containing `pyproject.toml`). This is independent of cwd,
   so running `omni` from a parent directory will NOT scatter data.
3. Fallback: the directory 3 levels up from this file (src/omnicompany/config.py)

This prevents the "scattered data" bug where running from cwd with no
pyproject.toml caused data/<domain>/ to be created next to cwd.
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_db_dir(domain: str = "default") -> Path:
    """解析指定 domain 的 events.db 所在目录。

    优先读取 OMNICOMPANY_DB_DIR 环境变量作为根目录，
    默认为项目根 data/ 下（基于本文件位置，不依赖 cwd）。

    Args:
        domain: 领域标识（如 "unity-qa", "demogame", "evolution"）

    Returns:
        Path 对象，指向 data/<domain>/ 目录
    """
    raw = os.environ.get("OMNICOMPANY_DB_DIR", "")
    if raw:
        base = Path(raw)
        if not base.is_absolute():
            base = (Path.cwd() / base).resolve()
    else:
        # 默认: 项目根/data/ — 基于本文件位置，与 cwd 无关
        base = _project_root() / "data"
    d = base / domain
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_domain_data_dir(domain: str) -> Path:
    """业务 domain 的 artifact 目录：``data/domains/<domain>/``

    用于 ``packages/domains/<domain>/`` 产生的 learn artifact / user_feedback /
    cache 等非事件数据。与 ``packages/domains/<domain>/`` 一一对应。

    与 :func:`resolve_db_dir` 的区别：
    - ``resolve_db_dir`` 是历史遗留（事件库 + 任意 artifact 混用），Move 8 后
      事件已统一到 ``data/events.db``，残留的 artifact 用途会落到 ``data/<domain>/``
      路径，触发 archmap ``data.forbid_new_subdirs`` 的 OMNI-021 漂移告警。
    - ``resolve_domain_data_dir`` 是 S3e.2 后业务 domain artifact 的规范入口，
      落在 archmap ``data.allowed_subdirs.domains/`` 白名单下，不触告警。

    Args:
        domain: domain 名（如 ``"demogame"``），与 ``packages/domains/<domain>/`` 同名。

    Returns:
        ``<project_root>/data/domains/<domain>/`` 目录（保证存在）。
    """
    raw = os.environ.get("OMNICOMPANY_DB_DIR", "")
    if raw:
        base = Path(raw)
        if not base.is_absolute():
            base = (Path.cwd() / base).resolve()
    else:
        base = _project_root() / "data"
    d = base / "domains" / domain
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_service_data_dir(service: str) -> Path:
    """service artifact 目录: ``data/services/<service>/`` (2026-04-21 B4+ 引入).

    与 ``packages/services/<service>/`` 对称，对每个 service 归一 artifact 出口。
    取代原来散落的 ``data/doctor/``、``data/guardian/``、``data/workflow_factory/`` 等
    违反 archmap ``data.forbid_new_subdirs`` 的历史路径。

    Args:
        service: service 名 (与 ``packages/services/<name>/`` 同名)

    Returns:
        ``<project_root>/data/services/<service>/`` 目录（保证存在）。
    """
    raw = os.environ.get("OMNICOMPANY_DB_DIR", "")
    if raw:
        base = Path(raw)
        if not base.is_absolute():
            base = (Path.cwd() / base).resolve()
    else:
        base = _project_root() / "data"
    d = base / "services" / service
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_runtime_data_dir(name: str) -> Path:
    """runtime 临时/日志/审计数据目录: ``data/_runtime/<name>/`` (2026-04-21 B4+ 引入).

    用途: llm_audit 审计落盘、scratch 暂存、crystallize pending queue 等 agent 可写
    但不归属特定 service/domain 的 runtime 产物。

    Args:
        name: 运行时分类名 (如 "llm_audit", "scratch", "crystallize")

    Returns:
        ``<project_root>/data/_runtime/<name>/`` 目录（保证存在）。
    """
    raw = os.environ.get("OMNICOMPANY_DB_DIR", "")
    if raw:
        base = Path(raw)
        if not base.is_absolute():
            base = (Path.cwd() / base).resolve()
    else:
        base = _project_root() / "data"
    d = base / "_runtime" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_db_path(domain: str = "default") -> Path:
    """返回事件 DB 的完整路径（Move 8 后：统一到 data/events.db 或 data/ide_events.db）。

    Move 8 之前：每个 domain 一个独立 events.db（data/<domain>/events.db），
    导致 dashboard 必须 rglob 13 个文件做 join。

    Move 8 之后：所有事件统一写入两个文件：
      - data/ide_events.db   ← domain in {"ide", "ide.agent", "ide_agent"}
      - data/events.db       ← 其他所有（factory / pipeline / guardian / demogame ...）

    domain 仍然有意义：作为 FactoryEvent.source 字段写入 events 表，
    dashboard 仍可按 source LIKE 'pkg.X%' 过滤。但路径不再分叉。
    """
    return resolve_unified_db_path(_basename_for_domain(domain))


def resolve_unified_db_path(basename: str = "events.db") -> Path:
    """Move 8 唯一权威路径解析 — 所有事件 DB 必须落在这里之一。

    Args:
        basename: "events.db"（默认） 或 "ide_events.db"

    Returns:
        <project_root>/data/<basename>，与 cwd 无关。

    OMNICOMPANY_DB_DIR 仍然有效，但仅作为根目录覆盖（用于测试/隔离），
    它不会再触发 per-domain 子目录拆分。
    """
    if basename not in ("events.db", "ide_events.db"):
        raise ValueError(
            f"basename must be 'events.db' or 'ide_events.db', got: {basename!r}"
        )
    raw = os.environ.get("OMNICOMPANY_DB_DIR", "")
    if raw:
        base = Path(raw)
        if not base.is_absolute():
            base = (Path.cwd() / base).resolve()
        # OMNICOMPANY_DB_DIR 历史上常被设成 data/autonomous/ — 兼容：
        # 如果指向 data/<sub>/，回退到其 data/ 父目录作为 unified 根
        if base.parent.name == "data":
            base = base.parent
    else:
        base = _project_root() / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / basename


def _basename_for_domain(domain: str) -> str:
    """根据 domain 决定使用哪个 unified DB 文件。"""
    if domain in ("ide", "ide.agent", "ide_agent"):
        return "ide_events.db"
    return "events.db"


def project_data_root() -> Path:
    """返回 <project_root>/data/ 绝对路径 — 引擎层路径校验使用。"""
    return (_project_root() / "data").resolve()


def omni_workspace_root() -> Path:
    """omnicompany 仓库根的【唯一权威】解析入口 — depth-independent, 不依赖 cwd。

    所有模块 (尤其 BOSS SIGHT) 都应调本函数, 不要再各处写
    `os.environ.get("OMNI_WORKSPACE_ROOT") or Path(__file__).parents[N]` 这类
    硬编码深度的散点逻辑 (N 各处不一致, 移动文件即失效)。

    解析顺序:
    1. OMNI_WORKSPACE_ROOT 环境变量 (dashboard/ccdaemon 注入时优先)
    2. 从本文件向上找含 pyproject.toml 的最近祖先 (与文件深度/ cwd 无关)
    3. 兜底: 含 src/omnicompany 的最近祖先
    """
    env = os.environ.get("OMNI_WORKSPACE_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "pyproject.toml").exists():
            return p
    for p in here.parents:
        if (p / "src" / "omnicompany").is_dir():
            return p
    return here.parents[3]


def _project_root() -> Path:
    """[内部别名] 委托到 omni_workspace_root() — 仓库根唯一权威 (见上)。"""
    return omni_workspace_root()


# ── demogame SDK 路径 ──────────────────────────────────────────────────────────────

def demogame_sdk_dir() -> Path:
    """demogame-auto-config-sdk 的根目录。

    由 demogame_SDK_DIR 环境变量（绝对路径）注入；缺失即清晰报错，
    不返回猜测路径。开发机在 .env 配置 demogame_SDK_DIR 维持现用法
    （见 .env.example）。
    """
    raw = os.environ.get("demogame_SDK_DIR", "")
    if raw:
        return Path(raw)
    raise RuntimeError(
        "未设置 demogame_SDK_DIR, 无法定位 demogame SDK: 请在 .env 配置 demogame_SDK_DIR "
        "(见 .env.example)。"
    )


def p4_root() -> Path:
    """Perforce 客户端工作区根 (开发机 = D:\\P4\\main)。

    由 P4_ROOT 环境变量注入；缺失即清晰报错，不返回猜测路径。
    demogame 各域的 Client/Excel/Binary 等子路径都应从本根派生。
    开发机在 .env 配置 P4_ROOT 维持现用法（见 .env.example）。
    """
    raw = os.environ.get("P4_ROOT", "")
    if raw:
        return Path(raw)
    raise RuntimeError(
        "未设置 P4_ROOT, 无法定位 Perforce 工作区: 请在 .env 配置 P4_ROOT "
        "(见 .env.example)。"
    )


def p4_client() -> str:
    """Perforce 客户端名 (项目代号, 弱身份)。

    优先 P4_CLIENT, 兼容历史 EXPLORE_P4_CLIENT; 缺失即报错, 不写死代号。
    """
    raw = os.environ.get("P4_CLIENT") or os.environ.get("EXPLORE_P4_CLIENT")
    if raw:
        return raw
    raise RuntimeError(
        "未设置 P4_CLIENT, 无法定位 Perforce 客户端: 请在 .env 配置 P4_CLIENT "
        "(见 .env.example)。"
    )
