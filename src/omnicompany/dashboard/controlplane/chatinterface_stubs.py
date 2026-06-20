# [OMNI] origin=ai-ide ts=2026-05-12 type=infra
# [OMNI] material_id="material:dashboard.controlplane.chatinterface_stubs.upstream_compat_router.py"
"""ChatInterface 上游 (claudecodeui) 期望的 endpoints stub.

阶段 2.5 of [`[2026-05-12]CHATINTERFACE-UPSTREAM-INTEGRATION/plan.md`](../../../../docs/plans/dashboard/[2026-05-12]CHATINTERFACE-UPSTREAM-INTEGRATION/plan.md).

ChatInterface 内 SessionStore / useSlashCommands / TaskMasterContext / 等 hook 期望
backend 提供这些 endpoints, 不提供时 ChatInterface 仍渲染但 console 一堆 404 错.
本 stub 返空 200 干净.

后续阶段 7+ 真接入时, 各 endpoint 改成调真实 backend (chat sessions / claude slash
commands 解析 / 等).

endpoints 清单
==============

| 路径                                          | 用途                  | 当前返回    |
|----------------------------------------------|----------------------|------------|
| GET  /api/providers/sessions/{sid}/messages  | SessionStore replay  | 空 history |
| POST /api/commands/list                      | slash 命令清单        | 空 list    |
| POST /api/commands/execute                   | slash 命令执行        | 404 (告知未实现) |
| GET  /api/taskmaster/status                  | TaskMaster 状态       | 未配置     |
| GET  /api/providers/auth                     | provider 认证状态     | 全 ok      |
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


chatinterface_stubs_router = APIRouter(prefix="/api", tags=["chatinterface-stubs"])


# ── SessionStore: GET messages ───────────────────────────────────────────────


@chatinterface_stubs_router.get("/providers/sessions/{sid}/messages")
async def get_session_messages(sid: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """SessionStore.fetchFromServer 调这个回放历史.

    若 sid 是 chat session (id 以 'chat-' 起手), 走 ccdaemon `/cc/chat/sessions/{sid}/history`
    端点 (ccdaemon 那边读 session.history_summary 转 NormalizedMessage).
    其他 sid (legacy / PTY etc) 返空.
    """
    if not sid.startswith("chat-"):
        return {"messages": [], "total": 0, "hasMore": False, "tokenUsage": None}
    # proxy 到 ccdaemon
    try:
        from omnicompany.dashboard.ccdaemon import lifecycle
        import httpx
        s = lifecycle.read_status()
        if not (s.alive and s.port):
            return {"messages": [], "total": 0, "hasMore": False, "tokenUsage": None}
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            r = await client.get(f"http://127.0.0.1:{s.port}/cc/chat/sessions/{sid}/history")
            if r.status_code != 200:
                return {"messages": [], "total": 0, "hasMore": False, "tokenUsage": None}
            return r.json()
    except Exception:
        return {"messages": [], "total": 0, "hasMore": False, "tokenUsage": None}


# ── Slash 命令: list ─────────────────────────────────────────────────────────


class SlashCommandsListBody(BaseModel):
    projectId: str | None = None
    provider: str | None = None
    sessionId: str | None = None
    projectPath: str | None = None  # 上游 useSlashCommands 真发的字段


_CLAUDE_BUILTIN_COMMANDS = [
    # OmniChat 控制面
    {"name": "goal", "description": "持续目标: /goal <目标> 自动续作到达成 | status | pause | complete | clear"},
    # 上下文管理
    {"name": "compact", "description": "压缩当前上下文 (保留摘要, 释放 token 预算)"},
    {"name": "clear", "description": "清空当前 session 历史 (开新对话)"},
    {"name": "init", "description": "扫描 cwd 生成 CLAUDE.md 项目指引"},
    # session 控制
    {"name": "resume", "description": "恢复指定 session ID"},
    {"name": "save", "description": "保存当前 session 状态"},
    # 配置查看
    {"name": "config", "description": "查看 ~/.claude/settings.json 配置"},
    {"name": "model", "description": "查看 / 切换当前模型"},
    {"name": "memory", "description": "查看 / 编辑 memory (auto-memory 持久化)"},
    {"name": "mcp", "description": "查看 MCP server 状态"},
    {"name": "permissions", "description": "查看 / 编辑工具调用权限"},
    # 输出控制
    {"name": "review", "description": "对当前文件/代码做审查"},
    {"name": "explain", "description": "解释某段代码的功能"},
    # 信息
    {"name": "help", "description": "查看可用命令"},
    {"name": "version", "description": "查看 claude-code CLI 版本"},
    {"name": "status", "description": "查看 session 状态"},
    # 退出
    {"name": "exit", "description": "退出当前 session"},
]

_CODEX_BUILTIN_COMMANDS = [
    {"name": "goal", "description": "持续目标: /goal <目标> 自动续作到达成 | status | pause | complete | clear"},
    {"name": "compact", "description": "压缩当前上下文 (codex CLI 内置)"},
    {"name": "clear", "description": "清空当前 session"},
    {"name": "model", "description": "查看 / 切换 Codex 模型"},
    {"name": "approval", "description": "切换 approval 模式 (auto/ask/never)"},
    {"name": "sandbox", "description": "切换 sandbox 模式"},
    {"name": "help", "description": "查看可用命令"},
    {"name": "exit", "description": "退出"},
]

_OMNI_AGENT_COMMANDS = [
    {"name": "goal", "description": "持续目标: /goal <目标> 自动续作到达成 | status | pause | complete | clear"},
    {"name": "compact", "description": "压缩当前上下文 (omnicompany agent 自家, 抄 claude 算法)"},
    {"name": "clear", "description": "清空 session 历史"},
    {"name": "help", "description": "查看 omnicompany agent 可用命令"},
]


def _native_command(name: str, description: str) -> dict[str, Any]:
    return {"name": name, "description": description, "metadata": {"nativePassthrough": True}}


def _native_provider_commands(provider: str) -> list[dict[str, Any]] | None:
    if provider == "codex":
        return [
            _native_command("goal", "Codex native slash command"),
            _native_command("compact", "Codex native slash command"),
            _native_command("clear", "Codex native slash command"),
            _native_command("model", "Codex native slash command"),
            _native_command("approval", "Codex native slash command"),
            _native_command("sandbox", "Codex native slash command"),
            {"name": "help", "description": "OmniChat help"},
            {"name": "status", "description": "OmniChat session status"},
        ]
    if provider == "claude_code":
        return [
            _native_command("goal", "Claude Code native slash command"),
            _native_command("compact", "Claude Code native slash command"),
            _native_command("clear", "Claude Code native slash command"),
            _native_command("init", "Claude Code native slash command"),
            _native_command("resume", "Claude Code native slash command"),
            _native_command("save", "Claude Code native slash command"),
            _native_command("config", "Claude Code native slash command"),
            _native_command("model", "Claude Code native slash command"),
            _native_command("memory", "Claude Code native slash command"),
            _native_command("mcp", "Claude Code native slash command"),
            _native_command("permissions", "Claude Code native slash command"),
            _native_command("review", "Claude Code native slash command"),
            _native_command("explain", "Claude Code native slash command"),
            {"name": "help", "description": "OmniChat help"},
            {"name": "status", "description": "OmniChat session status"},
        ]
    return None


@chatinterface_stubs_router.post("/commands/list")
def list_slash_commands(body: SlashCommandsListBody | None = None) -> dict[str, Any]:
    """ChatComposer 打 `/` 时 fetch 这个拿可用 slash 命令列表.

    按 body.provider 字段分发. 各 provider 的命令是 hardcoded 已知集合 (跟 claude
    binary / codex CLI / omnicompany agent 真支持的对齐). 后续 backend 真实现各
    provider /compact 等命令时, 命令仍按本清单暴露给前端, 后端 send_prompt 拦截
    特殊命令做真处理.
    """
    provider = (body and body.provider) or "claude_code"
    native_commands = _native_provider_commands(provider)
    if native_commands is not None:
        commands = native_commands
    elif provider == "codex":
        commands = _CODEX_BUILTIN_COMMANDS
    elif provider == "omni_agent":
        commands = _OMNI_AGENT_COMMANDS
    else:  # claude_code 默认
        commands = _CLAUDE_BUILTIN_COMMANDS
    # 上游 useSlashCommands.ts 真消费的字段是 data.builtIn / data.custom 不是 data.commands.
    # 保 commands 字段做向后兼容, 加 builtIn 让 ChatComposer 真识别.
    return {"commands": commands, "builtIn": commands, "custom": []}


# ── Slash 命令: execute ──────────────────────────────────────────────────────


class SlashCommandExecuteBody(BaseModel):
    commandName: str
    commandPath: str | None = None
    args: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


def _builtin(action: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "builtin", "action": action, "data": data or {}}


async def _ccdaemon_json(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str | None]:
    try:
        from omnicompany.dashboard.ccdaemon import lifecycle
        import httpx

        status = lifecycle.read_status()
        if not (status.alive and status.port):
            return None, "ccdaemon is not running"
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0)) as client:
            response = await client.request(
                method,
                f"http://127.0.0.1:{status.port}{path}",
                json=body if body is not None else None,
            )
            if response.status_code >= 400:
                try:
                    detail = response.json().get("detail")
                except Exception:
                    detail = response.text
                return None, str(detail or f"ccdaemon returned {response.status_code}")
            return response.json(), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _goal_message(goal_state: dict[str, Any] | None) -> str:
    if not goal_state:
        return "未设置持续目标。用 `/goal <目标>` 设一个会自动续作到达成的持续目标。"
    objective = str(goal_state.get("objective") or "").strip()
    status = str(goal_state.get("status") or "active")
    budget = goal_state.get("token_budget")
    auto = bool(goal_state.get("auto"))
    iters = goal_state.get("iterations")
    maxi = goal_state.get("max_iterations")
    lines = [
        "**持续目标 (OmniChat Goal)**",
        "",
        f"- 状态: `{status}`",
        f"- 目标: {objective}",
        f"- 自动续发: `{'开' if auto else '关'}`" + (
            f" · 已续 {iters}/{maxi} 轮" if auto and iters is not None else ""
        ),
    ]
    if budget:
        lines.append(f"- Token 预算: `{budget}`")
    lines.append("")
    lines.append("目标对所有 provider 生效, 每轮注入 Claude/Codex。`/goal complete` 标记完成、`/goal pause` 暂停续发、`/goal clear` 清除。")
    return "\n".join(lines)


async def _execute_goal_command(body: SlashCommandExecuteBody) -> dict[str, Any]:
    context = body.context or {}
    session_id = str(context.get("sessionId") or "").strip()
    if not session_id:
        return _builtin("goal", {"message": "No active chat session id is available for `/goal`."})

    args = [str(arg) for arg in (body.args or []) if str(arg).strip()]
    subcommand = (args[0].lower() if args else "status").lstrip("/")

    if subcommand in {"status", "show", "get"}:
        payload, error = await _ccdaemon_json("GET", f"/cc/chat/sessions/{session_id}/goal")
        if error:
            return _builtin("goal", {"message": f"Goal status failed: {error}"})
        return _builtin("goal", {"message": _goal_message((payload or {}).get("goal_state"))})

    if subcommand == "clear":
        payload, error = await _ccdaemon_json("POST", f"/cc/chat/sessions/{session_id}/goal", {"action": "clear"})
        if error:
            return _builtin("goal", {"message": f"Goal clear failed: {error}"})
        return _builtin("goal", {"message": _goal_message((payload or {}).get("goal_state"))})

    if subcommand in {"complete", "done"}:
        payload, error = await _ccdaemon_json("POST", f"/cc/chat/sessions/{session_id}/goal", {"action": "complete"})
        if error:
            return _builtin("goal", {"message": f"Goal complete failed: {error}"})
        return _builtin("goal", {"message": _goal_message((payload or {}).get("goal_state"))})

    if subcommand in {"pause", "cancel"}:
        payload, error = await _ccdaemon_json("POST", f"/cc/chat/sessions/{session_id}/goal", {"action": "pause"})
        if error:
            return _builtin("goal", {"message": f"Goal pause failed: {error}"})
        return _builtin("goal", {"message": _goal_message((payload or {}).get("goal_state"))})

    # auto=True 默认开自动续发循环; `/goal note <obj>` 设被动目标(只注入不续发)。
    auto = True
    if subcommand in {"set", "create", "start"}:
        objective = " ".join(args[1:]).strip()
    elif subcommand in {"note", "passive"}:
        objective = " ".join(args[1:]).strip()
        auto = False
    else:
        # `/goal 把当前阶段做完` 这种直接当 set 的口语简写。
        objective = " ".join(args).strip()

    if not objective:
        return _builtin("goal", {
            "message": "用法: `/goal <目标>`(自动续作到达成)、`/goal note <目标>`(只设不续作)、`/goal status`、`/goal pause`、`/goal complete`、`/goal clear`。"
        })

    payload, error = await _ccdaemon_json(
        "POST",
        f"/cc/chat/sessions/{session_id}/goal",
        {"action": "set", "objective": objective, "status": "active", "auto": auto},
    )
    if error:
        return _builtin("goal", {"message": f"Goal set failed: {error}"})
    return _builtin("goal", {"message": _goal_message((payload or {}).get("goal_state"))})


@chatinterface_stubs_router.post("/commands/execute")
async def execute_slash_command(body: SlashCommandExecuteBody) -> dict[str, Any]:
    command = (body.commandName or "").strip().lstrip("/").lower()
    if command == "goal":
        return await _execute_goal_command(body)
    if command == "clear":
        return _builtin("clear")
    if command == "help":
        return _builtin("help", {
            "content": (
                "**OmniChat commands**\n\n"
                "- `/goal <目标>`: 设持续目标并自动续作直到达成 (provider 无关)\n"
                "- `/goal note <目标>`: 只设目标不自动续作 (被动注入)\n"
                "- `/goal status`: 看当前持续目标 + 已续轮数\n"
                "- `/goal pause`: 暂停自动续发\n"
                "- `/goal complete`: 标记目标完成 (停续发)\n"
                "- `/goal clear`: 清除目标\n"
                "- `/clear`: 清屏"
            )
        })
    if command == "status":
        context = body.context or {}
        return _builtin("status", {
            "version": "omnichat",
            "uptime": "current session",
            "model": context.get("model") or "(default)",
            "provider": context.get("provider") or "(unknown)",
            "nodeVersion": "n/a",
            "platform": "web",
        })
    if command == "model":
        context = body.context or {}
        return _builtin("model", {
            "current": {"model": context.get("model") or "(default)"},
            "available": {
                "claude": ["local default", "sonnet", "opus", "haiku"],
                "cursor": [],
            },
        })
    raise HTTPException(status_code=404, detail=f"slash command not implemented: {command}")


# ── TaskMaster: status (我们没装 TaskMaster, 返 not-configured) ──────────────


@chatinterface_stubs_router.get("/taskmaster/status")
def taskmaster_status(projectId: str | None = None) -> dict[str, Any]:
    return {
        "hasTaskmaster": False,
        "status": "not-configured",
        "metadata": {},
    }


# ── Provider auth status (claudecodeui 检查各 provider 是否登录用) ────────────


@chatinterface_stubs_router.get("/settings/server-env")
def settings_server_env() -> dict[str, Any]:
    """ChatInterface QuickSettingsPanel 等可能问 server env. stub 返最小."""
    return {"isPlatform": False, "features": {}}


@chatinterface_stubs_router.get("/projects/{project_id:path}/sessions/{sid}/token-usage")
def project_session_token_usage(project_id: str, sid: str) -> dict[str, Any]:
    """token usage 初始值. ChatComposer TokenUsagePie 读 .used / .total 两个字段.
    我们没记 session 真累计 token, 返 0/200000 作初始, 第一次 result 帧后 adapter
    会 setTokenBudget({used, total}) 覆盖.
    """
    return {"used": 0, "total": 200000}


@chatinterface_stubs_router.get("/projects/{project_id:path}/files")
def project_files(project_id: str) -> list[Any]:
    """sidebar file browser / @ mention 数据.
    useFileMentions 直接 forEach 数组形态: ProjectFileNode[] = {name, type, path?, children?}.
    stub 返空数组. 之前返 {files:[], directories:[]} 让前端 t.forEach is not a function."""
    return []


@chatinterface_stubs_router.get("/taskmaster/installation-status")
def taskmaster_installation_status() -> dict[str, Any]:
    return {"installed": False, "version": None}


@chatinterface_stubs_router.get("/providers/auth")
def provider_auth_status() -> dict[str, Any]:
    """ChatInterface 顶栏 / ProviderSelector 调这个判断各 provider 可不可用.

    本 stub 简单返"全部可用". 真实现应该:
    - claude: 检查 `claude login` 状态 (~/.claude/.credentials.json 存在?)
    - codex: 检查 codex 登录状态
    - omni_agent: 检查 THE_COMPANY_API_KEY env
    """
    return {
        "claude": {"authenticated": True},
        "codex": {"authenticated": True},
        "gemini": {"authenticated": False},
        "cursor": {"authenticated": False},
    }


_UPLOAD_MAX_BYTES = 8 * 1024 * 1024  # 单图 8 MiB 上限


@chatinterface_stubs_router.post("/projects/{project_id:path}/upload-images")
async def upload_images(project_id: str, images: list[UploadFile] = File(...)) -> dict[str, Any]:
    """ChatComposer 贴/拖图触发 — multipart formData field name='images', 可多文件.

    上游 claudecodeui 期望返回 `{images: [{name, data, size, ...}]}`,
    其中 data 是 data URL (image bubble `<img src={img.data}>` 直接吃).

    本 stub 走 inline base64: 不存 disk, 不引入项目维度文件管理. 单图 8 MiB 上限防 OOM.
    后端 chat.py 暂未把 images 透传给 claude SDK (composerSendToWsFrame 也吞了 images
    字段), 当前 round 只让 UI 上传不报错 + 用户气泡显示缩略图.
    """
    out: list[dict[str, Any]] = []
    for f in images:
        data = await f.read()
        if len(data) > _UPLOAD_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"{f.filename}: 单图超过 {_UPLOAD_MAX_BYTES} bytes")
        mime = f.content_type or "image/png"
        if not mime.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"{f.filename}: 不是图片 (content_type={mime})")
        b64 = base64.b64encode(data).decode("ascii")
        out.append({
            "name": f.filename or "image",
            "data": f"data:{mime};base64,{b64}",
            "size": len(data),
            "mimeType": mime,
        })
    logger.info("upload-images project=%s count=%d total_bytes=%d", project_id, len(out), sum(i["size"] for i in out))
    return {"images": out}


__all__ = ["chatinterface_stubs_router"]
