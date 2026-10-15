# [OMNI] origin=unknown domain=services/_core/agent agent=ai-ide-2b20d28d ts=2026-09-04T02:25:27Z
"""CLI-first external Agent Worker control plane.

This module deliberately does not implement another agent runtime.  It owns the
launch contract and delegates execution to the existing ccdaemon PTY runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any
import urllib.error
import urllib.request

from omnicompany.core.agent_environment import agent_environment, agent_environments


# Every table below is derived from the shared agent-environment registry
# (``omnicompany.core.agent_environment``, artifact
# ``config/agents/agent-environments.v1.json``), which LiWork mirrors. Registering a runtime is one edit to that JSON; nothing here has to change.
# ``tests/test_agent_environment_registry.py`` pins the derived values.
_ENVIRONMENTS = agent_environments()

#: Executable candidates per runtime. Entries with no executable (``dsh_web``, and the
#: retired ``zcode``) are not launchable and stay out, exactly as before.
PROVIDER_EXECUTABLES: dict[str, tuple[str, ...]] = {
    env.id: env.executables for env in _ENVIRONMENTS if env.executables
}

# Some provider executables do not enter their interactive Agent UI when
# invoked without a subcommand.  Keep those process arguments separate from
# executable discovery so every launch path receives the same argv contract.
PROVIDER_DEFAULT_ARGS: dict[str, tuple[str, ...]] = {
    env.id: env.process_args for env in _ENVIRONMENTS if env.process_args
}

MODEL_FLAGS: dict[str, str] = {
    env.id: env.model_flag for env in _ENVIRONMENTS if env.model_flag
}

# The model an explicit provider gets when the caller names none. User decision
# 2026-09-01: Claude runs on Opus by default — "每次没必要用 sonnet 启动"; the
# earlier sonnet default was a cost guard the user has now waived. Callers can
# still pick any model explicitly with ``--model``, and the difficulty-hint axis
# (boss_sight/controller/model_resolver._MODEL_BY_HINT) still routes low-effort
# work to sonnet. A route override pins its model on argv instead (see
# ``pins_model_on_argv``), so it is deliberately not part of this table.
SAFE_PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    env.id: env.default_model
    for env in _ENVIRONMENTS
    if env.default_model and not env.pins_model_on_argv
}

# Reasoning level a runtime gets when the caller supplies none. Claude Code's
# own default is ``medium``; the workspace standard is ``high`` (user decision
# 2026-09-01), so every launch path that does not name an effort still gets it.
SAFE_PROVIDER_DEFAULT_EFFORTS: dict[str, str] = {
    env.id: env.default_effort for env in _ENVIRONMENTS if env.default_effort
}


def safe_default_model(provider: str, model: str | None) -> str | None:
    if model is not None and model.strip():
        return model
    return SAFE_PROVIDER_DEFAULT_MODELS.get(provider)


def safe_default_effort(provider: str, effort: str | None) -> str | None:
    if effort is not None and effort.strip():
        return effort
    return SAFE_PROVIDER_DEFAULT_EFFORTS.get(provider)

# ── Codex → the_company API 接入 (不改全局 codex 配置) ─────────────────────────────
# 官方 Codex 订阅走 ~/.codex/config.toml 原样保留; 本接入只用 codex CLI 的
# ``-c key=value`` 进程级覆盖在内存里合成一个 ``the_company`` model provider,
# 指向公司 llm-proxy。密钥读环境变量 THE_COMPANY_API_KEY, 不写任何全局配置。
# 值按 TOML 字面量拼 (codex -c 会把 value 当 TOML 解析, 失败才当字符串)。
CODEX_THE_COMPANY_PROVIDER = "codex_the_company"
_CODEX_THE_COMPANY = agent_environment(CODEX_THE_COMPANY_PROVIDER)
assert _CODEX_THE_COMPANY is not None and _CODEX_THE_COMPANY.default_model
CODEX_THE_COMPANY_DEFAULT_MODEL = _CODEX_THE_COMPANY.default_model
THE_COMPANY_CODEX_CLI_OVERRIDES: tuple[tuple[str, str], ...] = _CODEX_THE_COMPANY.cli_overrides


@dataclass(frozen=True)
class AgentLaunchRequest:
    task: str | None = None
    provider: str = "codex"
    model: str | None = None
    effort: str | None = None
    cwd: str | None = None
    project: str | None = None
    workstream: str | None = None
    active_plan: str | None = None
    topic: str | None = None
    parent_agent_id: str | None = None
    resume_provider_session_id: str | None = None
    fork_from_provider_session_id: str | None = None
    origin: str = "agent"
    visibility: str = "visible"
    from_controller: bool = False
    safe_mode: bool = False
    pure_mode: bool = False

    @property
    def binding_kind(self) -> str:
        return "project" if any((self.project, self.workstream, self.active_plan)) else "unbound"


@dataclass(frozen=True)
class AgentHandle:
    worker_id: str
    pty_id: str
    provider: str
    model: str | None
    cwd: str
    runtime_kind: str = "cli"
    worker_kind: str = "external_agent_worker"
    product_kind: str = "agent_worker"
    binding_kind: str = "unbound"
    project: str | None = None
    workstream: str | None = None
    active_plan: str | None = None
    topic: str | None = None
    parent_agent_id: str | None = None
    visibility: str = "visible"
    provider_session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _daemon_base_url(explicit: str | None = None) -> str:
    if explicit:
        return explicit.rstrip("/")
    from omnicompany.dashboard.ccdaemon.api import lifecycle

    status = lifecycle.read_status()
    if not (getattr(status, "alive", False) and getattr(status, "port", None)):
        raise RuntimeError("ccdaemon is not running; start the dashboard/ccdaemon first")
    return f"http://127.0.0.1:{status.port}"


def _request_json(
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    method: str = "GET",
    daemon_base_url: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{_daemon_base_url(daemon_base_url)}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - loopback daemon
            raw = response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")[:500]
        except Exception:  # noqa: BLE001
            detail = exc.reason
        raise RuntimeError(f"ccdaemon HTTP {exc.code}: {detail}") from exc
    except OSError as exc:
        raise RuntimeError(f"ccdaemon request failed: {exc}") from exc
    return json.loads(raw.decode("utf-8")) if raw else {"ok": True}


def resolve_executable(provider: str) -> str:
    candidates = PROVIDER_EXECUTABLES.get(provider)
    if not candidates:
        raise ValueError(f"unsupported CLI Agent Worker provider: {provider}")
    for name in candidates:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    # Keep ccdaemon's provider-specific resolution available (notably Kiro on Windows).
    return candidates[0]


def _the_company_model_catalog_path() -> str | None:
    """the_company 模型目录文件路径(codex `model_catalog_json` 配置键), 不存在返回 None。

    codex 0.147.0 起自定义 provider 不再进 TUI 模型选择器默认列表, 必须用
    `model_catalog_json` 钉一份目录, 否则选模型时看到的是官方默认目录。
    目录由 config/agents/the_company-codex-model-catalog.json 静态维护(从代理
    GET /v1/models 生成)。
    """
    from omnicompany.core.config import omni_workspace_root

    path = (
        omni_workspace_root() / "config" / "agents" / "the_company-codex-model-catalog.json"
    )
    return path.as_posix() if path.is_file() else None


def _the_company_codex_command(model: str | None) -> list[str]:
    """Build a process-local Codex command wired to the the_company proxy.

    The official Codex subscription config is never touched: overrides travel as
    ``-c`` CLI flags and the key comes from the environment.
    """
    if not os.environ.get("THE_COMPANY_API_KEY", "").strip():
        raise ValueError(
            "Codex the_company 接入需要本地 secret THE_COMPANY_API_KEY "
            "(internal-llm-proxy.example.com); 环境中未找到, 设置后再试。"
        )
    upstream = str(model or "").strip() or CODEX_THE_COMPANY_DEFAULT_MODEL
    cmd = [resolve_executable(CODEX_THE_COMPANY_PROVIDER)]
    for flag, value in THE_COMPANY_CODEX_CLI_OVERRIDES:
        cmd.extend([flag, value])
    catalog = _the_company_model_catalog_path()
    if catalog is not None:
        # 与 agent_runtime.the_company_codex_config_overrides 同一口径: 值是 JSON 编码的路径字符串。
        cmd.extend(["-c", f"model_catalog_json={json.dumps(catalog, ensure_ascii=False)}"])
    cmd.extend(["--model", upstream])
    return cmd


# ── Codex → GLM Coding Plan (Z.ai) 接入 ──────────────────────────────────────
# 为什么不用 zcode: zcode 是第三方客户端, 而 GLM Coding Plan 官方明确限定只能在
# 其支持的工具内使用(Claude Code / Codex / Cline / OpenCode / Goose ...), zcode
# 不在名单内, 交互质量也低。本接入形状与 codex_the_company 完全一致: 官方 Codex CLI +
# 进程级 ``-c`` 覆盖, ~/.codex/config.toml 一字不改。
#
# 端点取自官方文档(docs.z.ai/devpack/latest-model 的 "Before You Start"):
# Codex 走 https://api.z.ai/api/v1 (OpenAI Responses 协议)。
# 本机实测 2026-08-17: POST /api/v1/responses model=glm-5.3 → HTTP 200。
#
# 密钥不写进任何被 git 跟踪的文件: 运行时按 env ZAI_API_KEY → omnicompany/.env
# (gitignored) 顺序解析, 由 provider_terminal_environment 注入子进程。
CODEX_GLM_PROVIDER = "codex_glm"
_CODEX_GLM = agent_environment(CODEX_GLM_PROVIDER)
assert _CODEX_GLM is not None and _CODEX_GLM.default_model
CODEX_GLM_DEFAULT_MODEL = _CODEX_GLM.default_model
GLM_CODEX_CLI_OVERRIDES: tuple[tuple[str, str], ...] = _CODEX_GLM.cli_overrides


def _glm_codex_command(model: str | None) -> list[str]:
    """Build a process-local Codex command wired to the GLM Coding Plan.

    The key is only *checked* here; injection into the child environment happens
    in ``provider_terminal_environment`` so that both the CLI Hub and a resume
    launch get it through the same path.
    """
    from omnicompany.dashboard.ccdaemon.provider_runtime import zai_api_key

    if not zai_api_key():
        raise ValueError(
            "GLM Coding Plan 接入需要 Z.ai coding plan key: 设置环境变量 ZAI_API_KEY, "
            "或在 omnicompany/.env 里写 ZAI_API_KEY=<key>。"
        )
    upstream = str(model or "").strip() or CODEX_GLM_DEFAULT_MODEL
    cmd = [resolve_executable(CODEX_GLM_PROVIDER)]
    for flag, value in GLM_CODEX_CLI_OVERRIDES:
        cmd.extend([flag, value])
    cmd.extend(["--model", upstream])
    return cmd


# ── DeepSeek Harness (dsh) PTY 接入 ──────────────────────────────────────────
# dsh 没有交互式终端模式(上游 2026-08-04 删掉 TUI 包), CLI 面只有 web 服务与
# headless 一次性任务。CLI Hub 用 config/agents/dsh/dsh-pty.py 把 headless 包成
# 交互窗格: 一行一个任务, 逐行 spawn `dsh --profile headless --patch <模型>`。
# 模型组合以 ``<backend>/<model>`` 形如 ``opencode-go/deepseek-v4-flash`` 走
# model 字段; patch 文件与 provider 注册表都在 config/agents/dsh/。
DSH_PROVIDER = "dsh"
DSH_WEB_PROVIDER = "dsh_web"
DSH_DEFAULT_MODEL = "opencode-go/deepseek-v4-flash"
DSH_SUPPORTED_MODELS = frozenset(
    {
        "opencode-go/deepseek-v4-flash",
        "opencode-go/deepseek-v4-pro",
        "the_company/deepseek-v4-flash",
        "the_company/deepseek-v4-pro",
    }
)
DSH_PTY_MARKER = "--omni-dsh-pty"

# 与 ccdaemon.provider_identity.CLAUDE_FAMILY_PROVIDER_IDS 同源: 两边都从共享
# agent-environment 注册表按 family 推导, 不再各存一份字面量, 也不跨层 import
# (arch_gate R1)。
CLAUDE_FAMILY_PROVIDER_IDS = frozenset(
    env.id for env in _ENVIRONMENTS if env.family == "claude"
)

CODEX_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
CLAUDE_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def effective_default_model(provider: str) -> str | None:
    """Return the model a fresh runtime will actually use when none is supplied."""
    if provider == CODEX_THE_COMPANY_PROVIDER:
        return CODEX_THE_COMPANY_DEFAULT_MODEL
    if provider == CODEX_GLM_PROVIDER:
        return CODEX_GLM_DEFAULT_MODEL
    if provider == DSH_PROVIDER:
        return DSH_DEFAULT_MODEL
    return safe_default_model(provider, None)


def _official_codex_model_metadata(provider: str) -> dict[str, dict[str, Any]]:
    """某个官方 Codex 账号家目录里的模型目录(``models_cache.json``), 按 slug 索引。

    家目录按环境注册表解析: 覆盖变量优先, 否则 ``~/<dir_name>``; 文件不存在返回空。
    """
    environment = agent_environment(provider)
    account_home = environment.account_home if environment else None
    if account_home is None:
        home = Path.home() / ".codex"
    else:
        override = os.environ.get(account_home.override_env, "").strip()
        home = Path(override) if override else Path.home() / account_home.dir_name
    try:
        payload = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    rows = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("slug") or row.get("id") or ""): row
        for row in rows
        if isinstance(row, dict) and str(row.get("slug") or row.get("id") or "").strip()
    }


def _the_company_model_metadata() -> dict[str, dict[str, Any]]:
    path = _the_company_model_catalog_path()
    if path is None:
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return {
        str(row.get("slug") or row.get("id") or ""): row
        for row in payload.get("models", [])
        if str(row.get("slug") or row.get("id") or "").strip()
    }


def agent_worker_model_catalog(provider: str) -> list[dict[str, Any]]:
    """Project server-owned model/effort truth into one runtime provider.

    The browser and LOFA never carry model or effort enums.  Provider-specific
    catalogs remain authoritative here and can evolve with the installed CLIs.
    """
    if provider == DSH_WEB_PROVIDER:
        return []
    if provider == DSH_PROVIDER:
        return [
            {"id": model, "label": model, "efforts": [], "default_effort": None}
            for model in sorted(DSH_SUPPORTED_MODELS)
        ]

    the_company_models = _the_company_model_metadata()
    if provider == CODEX_THE_COMPANY_PROVIDER and the_company_models:
        source_rows = [
            {
                "id": model_id,
                "display_name": row.get("display_name") or model_id,
            }
            for model_id, row in the_company_models.items()
        ]
    else:
        try:
            from omnicompany.packages.services._core.agent.resource_control import load_catalog

            catalog = load_catalog()
        except (OSError, ValueError, TypeError):
            return []
        provider_ids = {
            str(row.get("id") or "")
            for row in catalog.get("providers", [])
            if str(row.get("runtime_provider") or "") == provider
        }
        source_rows = [
            row for row in catalog.get("models", [])
            if provider_ids.intersection(str(item) for item in row.get("providers", []))
        ]

    official_models = _official_codex_model_metadata(provider) if provider in {"codex", "codex_2"} else {}
    result: list[dict[str, Any]] = []
    for row in source_rows:
        model_id = str(row.get("id") or "").strip()
        if not model_id:
            continue
        # 官方账号的模型(如 gpt-6-astra)不在 the_company 目录里, 推理档位从该账号
        # 登录后拉回的 models_cache.json 取; the_company 目录仍优先。
        runtime_row = the_company_models.get(model_id) or official_models.get(model_id, {})
        if provider in CLAUDE_FAMILY_PROVIDER_IDS:
            efforts = list(CLAUDE_REASONING_EFFORTS)
            default_effort = SAFE_PROVIDER_DEFAULT_EFFORTS.get(provider, "high")
        elif provider in {"codex", "codex_2", CODEX_THE_COMPANY_PROVIDER}:
            efforts = [
                str(item.get("effort"))
                for item in runtime_row.get("supported_reasoning_levels", [])
                if str(item.get("effort") or "").strip()
            ]
            default_effort = str(runtime_row.get("default_reasoning_level") or "") or None
        else:
            efforts = []
            default_effort = None
        result.append({
            "id": model_id,
            "label": str(row.get("display_name") or runtime_row.get("display_name") or model_id),
            "efforts": efforts,
            "default_effort": default_effort if default_effort in efforts else None,
        })
    return result


def _dsh_pty_script() -> str:
    from omnicompany.core.config import omni_workspace_root

    script = omni_workspace_root() / "config" / "agents" / "dsh" / "dsh-pty.py"
    if not script.is_file():
        raise RuntimeError(f"DeepSeek Harness PTY adapter missing: {script}")
    return str(script)


def _dsh_web_script() -> str:
    from omnicompany.core.config import omni_workspace_root

    script = omni_workspace_root() / "config" / "agents" / "dsh" / "dsh-web.py"
    if not script.is_file():
        raise RuntimeError(f"DeepSeek Harness web supervisor missing: {script}")
    return str(script)


def _dsh_command(model: str | None) -> list[str]:
    """Build the PTY command for the DeepSeek Harness adapter pane.

    The pane runs under the daemon's own interpreter (``sys.executable``) so it
    never depends on PATH python resolution; the adapter spawns ``dsh.cmd``
    itself and reports a clear error when the harness is not installed.
    """
    combo = str(model or "").strip() or DSH_DEFAULT_MODEL
    if combo not in DSH_SUPPORTED_MODELS:
        raise ValueError(
            f"dsh model combo {combo!r} is not supported; expected one of "
            + ", ".join(sorted(DSH_SUPPORTED_MODELS))
        )
    return [
        sys.executable,
        "-X",
        "utf8",
        _dsh_pty_script(),
        "--model",
        combo,
        DSH_PTY_MARKER,
    ]


def _dsh_web_command(model: str | None) -> list[str]:
    """Build the supervisor argv for a DeepSeek Harness Web UI session.

    The web UI picks its own model inside the browser from the machine
    settings (~/.dsh/settings.yaml), so no model combo travels in argv. The
    session id is appended by the web host at spawn time.
    """
    if (model or "").strip():
        raise ValueError("dsh_web does not take a model; select the model inside the Web UI")
    return [sys.executable, "-X", "utf8", _dsh_web_script()]


def _append_reasoning_effort(cmd: list[str], provider: str, effort: str | None) -> list[str]:
    normalized = str(effort or "").strip().lower()
    if not normalized:
        return cmd
    if provider in {"codex", "codex_2", CODEX_THE_COMPANY_PROVIDER, CODEX_GLM_PROVIDER}:
        if normalized not in CODEX_REASONING_EFFORTS:
            raise ValueError(f"unsupported Codex reasoning effort: {normalized}")
        return [*cmd, "-c", f'model_reasoning_effort="{normalized}"']
    if provider in CLAUDE_FAMILY_PROVIDER_IDS:
        if normalized not in CLAUDE_REASONING_EFFORTS:
            raise ValueError(f"unsupported Claude effort: {normalized}")
        return [*cmd, "--effort", normalized]
    raise ValueError(f"provider {provider!r} does not advertise reasoning effort selection")


def build_provider_command(
    provider: str,
    model: str | None = None,
    *,
    effort: str | None = None,
    pure_mode: bool = False,
) -> list[str]:
    if provider == CODEX_THE_COMPANY_PROVIDER:
        cmd = _the_company_codex_command(model)
    # codex provider 上的 ``the_company/<model>`` 前缀是等价入口, 方便已有
    # codex 调用方按模型切换来源而不改 provider 字段。
    elif provider == "codex" and (model or "").strip().startswith("the_company/"):
        cmd = _the_company_codex_command(str(model).strip().split("/", 1)[1].strip() or None)
    elif provider == CODEX_GLM_PROVIDER:
        cmd = _glm_codex_command(model)
    # 与 the_company 同一口径: codex provider 上的 ``zai/<model>`` 前缀等价于
    # codex_glm, 方便按模型切来源而不改 provider 字段。
    elif provider == "codex" and (model or "").strip().startswith("zai/"):
        cmd = _glm_codex_command(str(model).strip().split("/", 1)[1].strip() or None)
    elif provider == DSH_PROVIDER:
        cmd = _dsh_command(model)
    elif provider == DSH_WEB_PROVIDER:
        cmd = _dsh_web_command(model)
    else:
        cmd = [resolve_executable(provider), *PROVIDER_DEFAULT_ARGS.get(provider, ())]
        flag = MODEL_FLAGS.get(provider)
        if model and flag:
            cmd.extend([flag, model])
    cmd = _append_reasoning_effort(cmd, provider, safe_default_effort(provider, effort))
    return apply_pure_mode_flags(cmd, provider) if pure_mode else cmd


def apply_pure_mode_flags(cmd: list[str], provider: str) -> list[str]:
    """Disable provider-side customization discovery for a task-only worker.

    Authentication and the provider's native session store are deliberately
    untouched.  This controls context sources only; permission prompting is a
    separate ``safe_mode`` policy.
    """
    normalized = list(cmd)
    if provider in CLAUDE_FAMILY_PROVIDER_IDS:
        for flag in ("--safe-mode", "--disable-slash-commands"):
            if flag not in normalized:
                normalized.append(flag)
        return normalized
    if provider in {"codex", "codex_2", CODEX_THE_COMPANY_PROVIDER}:
        for feature in ("hooks", "memories", "skill_search", "plugins"):
            normalized.extend(["--disable", feature])
        if "--no-daemon" not in normalized:
            normalized.append("--no-daemon")
    return normalized


# ── T4 监督闸门 (SWARM-DISPATCH-PROACTIVITY P4) ──────────────────────────────
# T4(高性价比执行档)模型必须在监督下工作: 由某个 agent 派生(parent_agent_id)、
# 由用户面板发起(from_controller)、或用户直接唤起时显式豁免(env)。机械强制,
# 不依赖 prompt 遵守率。层级真源 = config/agent_control/catalog.json (用户钉的 T1-T4)。

T4_UNSUPERVISED_ENV = "OMNI_T4_UNSUPERVISED_OK"


def model_tier(model: str | None) -> str | None:
    """查 model 在用户钉层级目录里的 tier; 未知/未配置返回 None(不拦截)。"""
    if not model:
        return None
    try:
        from omnicompany.core.config import omni_workspace_root

        catalog_path = omni_workspace_root() / "config" / "agent_control" / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 目录读不到时不拦截, 闸门宁缺勿误杀
        return None
    needle = str(model).strip().lower()
    # ``the_company/<model>`` 前缀与裸模型同层级: T4 监督闸门不能因来源前缀而失效。
    if needle.startswith("the_company/"):
        needle = needle.split("/", 1)[1].strip()
    needle = str((catalog.get("aliases") or {}).get(needle, needle)).strip().lower()
    for entry in catalog.get("models", []):
        if needle in {str(entry.get("id", "")).lower(), str(entry.get("display_name", "")).lower()}:
            tier = entry.get("tier")
            return str(tier) if tier else None
    return None


def ensure_t4_supervision(request: AgentLaunchRequest) -> None:
    """T4 模型无监督启动 → PermissionError; 其余层级不管。"""
    if model_tier(request.model) != "T4":
        return
    if request.parent_agent_id or request.from_controller:
        return
    if os.environ.get(T4_UNSUPERVISED_ENV, "").strip().lower() in ("1", "true", "on"):
        return
    raise PermissionError(
        f"T4 模型 {request.model!r} 必须在监督下启动: 由监督 agent 派生(带 parent_agent_id)"
        f"或由用户面板发起; 用户直接唤起请设 {T4_UNSUPERVISED_ENV}=1 显式豁免。"
        " (SWARM-DISPATCH-PROACTIVITY P4 闸门)"
    )


def launch_agent_worker(
    request: AgentLaunchRequest,
    *,
    daemon_base_url: str | None = None,
) -> AgentHandle:
    """Launch one real PTY-backed Agent Worker and optionally submit its first task."""
    ensure_t4_supervision(request)
    cwd = str(Path(request.cwd or os.getcwd()).resolve())
    meta = _request_json(
        "/cc/agent-workers",
        payload={
            "provider": request.provider,
            "model": request.model,
            "effort": request.effort,
            "task": request.task,
            "cwd": cwd,
            "project": request.project,
            "workstream": request.workstream,
            "active_plan": request.active_plan,
            "topic": request.topic,
            "parent_agent_id": request.parent_agent_id,
            "resume_provider_session_id": request.resume_provider_session_id,
            "fork_from_provider_session_id": request.fork_from_provider_session_id,
            "origin": request.origin,
            "visibility": request.visibility,
            "from_controller": request.from_controller,
            "safe_mode": request.safe_mode,
            "pure_mode": request.pure_mode,
        },
        method="POST",
        daemon_base_url=daemon_base_url,
    )
    pty_id = str(meta.get("pty_id") or meta.get("id") or "")
    if not pty_id:
        raise RuntimeError("ccdaemon did not return a PTY id")

    handle = AgentHandle(
        worker_id=pty_id,
        pty_id=pty_id,
        provider=request.provider,
        model=request.model,
        cwd=cwd,
        binding_kind=request.binding_kind,
        project=request.project,
        workstream=request.workstream,
        active_plan=request.active_plan,
        topic=request.topic or request.task,
        parent_agent_id=request.parent_agent_id,
        visibility=request.visibility,
        provider_session_id=meta.get("provider_session_id"),
    )

    return handle


def list_agent_workers(*, daemon_base_url: str | None = None) -> list[dict[str, Any]]:
    page = _request_json("/cc/sessions", daemon_base_url=daemon_base_url)
    rows = page.get("items") if isinstance(page, dict) else None
    if rows is None and isinstance(page, dict):
        rows = page.get("sessions")
    return [row for row in (rows or []) if str(row.get("kind") or "pty") != "chat"]


def send_agent_input(
    worker_id: str,
    message: str,
    *,
    submit: bool = True,
    daemon_base_url: str | None = None,
) -> dict[str, Any]:
    """Submit one message to a worker.

    ``submit=False`` writes the bytes verbatim without appending a carriage
    return.  Callers need that to send the prompt body and the Enter keystroke
    as two separate writes: provider TUIs such as Codex treat one text+CR write
    as a paste burst, leave the text sitting in the composer and never submit it.
    """
    return _request_json(
        f"/cc/sessions/{worker_id}/input",
        payload={"data": message, "submit": submit},
        method="POST",
        daemon_base_url=daemon_base_url,
    )


def resume_agent_worker(
    provider: str,
    provider_session_id: str,
    *,
    cwd: str | None = None,
    daemon_base_url: str | None = None,
) -> dict[str, Any]:
    return _request_json(
        "/cc/sessions/resume-provider",
        payload={
            "provider": provider,
            "provider_session_id": provider_session_id,
            "cwd": str(Path(cwd or os.getcwd()).resolve()),
        },
        method="POST",
        daemon_base_url=daemon_base_url,
    )


__all__ = [
    "AgentHandle",
    "AgentLaunchRequest",
    "CLAUDE_REASONING_EFFORTS",
    "CODEX_REASONING_EFFORTS",
    "CODEX_THE_COMPANY_PROVIDER",
    "DSH_DEFAULT_MODEL",
    "DSH_PROVIDER",
    "DSH_SUPPORTED_MODELS",
    "DSH_WEB_PROVIDER",
    "PROVIDER_EXECUTABLES",
    "SAFE_PROVIDER_DEFAULT_MODELS",
    "T4_UNSUPERVISED_ENV",
    "agent_worker_model_catalog",
    "build_provider_command",
    "apply_pure_mode_flags",
    "effective_default_model",
    "ensure_t4_supervision",
    "launch_agent_worker",
    "list_agent_workers",
    "model_tier",
    "resume_agent_worker",
    "send_agent_input",
    "SAFE_PROVIDER_DEFAULT_EFFORTS",
    "safe_default_effort",
    "safe_default_model",
]
