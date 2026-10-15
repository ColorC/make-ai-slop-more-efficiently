# Agent Worker 目录(浏览器建会话的唯一注册表)分片。与 turn_confirm.py 同惯例:
# 由 pty_routes.py exec 拼接, 命名空间与拆分前等价(R4 行数棘轮)。
# 依赖 exec 时已就绪的 cc_router / AGENT_WORKER_PROVIDER_IDS / Any。
_PROVIDER_LABELS = {
    "codex": "Codex CLI（official 1）",
    "codex_2": "Codex CLI（official 2）",
    "codex_the_company": "Codex CLI（the_company API）",
    "codex_glm": "Codex CLI（GLM Coding Plan）",
    "claude_code": "Claude CLI（official 1）",
    "claude_2": "Claude CLI（official 2）",
    "antigravity": "Antigravity CLI",
    "codebuddy": "CodeBuddy CLI",
    "kimi": "Kimi CLI",
    "opencode": "OpenCode Go",
    "kiro": "Kiro CLI",
    "qoder": "Qoder CLI",
    "dsh": "DeepSeek Harness CLI",
    "dsh_web": "DeepSeek Harness Web",
    "grok": "Grok Build CLI",
}


def agent_worker_catalog() -> dict[str, Any]:
    """Return the sole browser-facing registry for creatable session kinds."""
    from omnicompany.packages.services._core.agent.control_plane import (
        agent_worker_model_catalog,
        effective_default_model,
    )

    options: list[dict[str, Any]] = []
    for provider in VISIBLE_AGENT_WORKER_PROVIDER_IDS:
        model_rows = agent_worker_model_catalog(provider)
        default_model = effective_default_model(provider)
        default_row = next((row for row in model_rows if row["id"] == default_model), None)
        options.append({
            "id": f"provider:{provider}",
            "label": _PROVIDER_LABELS.get(provider, provider),
            "kind": "agent_worker",
            "provider": provider,
            "default_model": default_model,
            "models": [row["id"] for row in model_rows],
            "model_labels": {row["id"]: row["label"] for row in model_rows},
            "model_efforts": {row["id"]: row["efforts"] for row in model_rows},
            "model_default_efforts": {row["id"]: row["default_effort"] for row in model_rows},
            "default_effort": default_row.get("default_effort") if default_row else None,
            "efforts": sorted({effort for row in model_rows for effort in row["efforts"]}),
            "surface": "web" if provider == "dsh_web" else "pty",
        })
    options.extend([
        {"id": "shell:plain", "label": "纯 CLI", "kind": "shell", "cmd": ["pwsh", "-NoLogo"]},
        {"id": "shell:powershell", "label": "PowerShell", "kind": "shell", "cmd": ["pwsh"]},
    ])
    # assistant(机主的助理)按模型阶梯挑当前能用的一档: Astra > Fable > Opus > Sol,
    # 报过额度用尽的 provider 一段时间内跳过。见 ccdaemon/assistant.py。
    from omnicompany.dashboard.ccdaemon.assistant import resolve_assistant_launch

    rung = resolve_assistant_launch()
    assistant_model_row = next(
        (row for row in agent_worker_model_catalog(rung.provider) if row["id"] == rung.model),
        None,
    )
    # 阶梯写死的档位优先(机主定的 high); 该模型不支持时退到目录默认。
    supported = set(assistant_model_row.get("efforts") or []) if assistant_model_row else set()
    effort = rung.effort if rung.effort in supported or not supported else (
        assistant_model_row.get("default_effort") if assistant_model_row else None
    )
    return {
        "schema_version": 1,
        "options": options,
        "assistant": {
            "label": _PROVIDER_LABELS.get(rung.provider, rung.provider),
            "provider": rung.provider,
            "model": rung.model,
            "effort": effort,
        },
    }


@cc_router.get("/agent-workers/catalog")
def get_agent_worker_catalog() -> dict[str, Any]:
    return agent_worker_catalog()

