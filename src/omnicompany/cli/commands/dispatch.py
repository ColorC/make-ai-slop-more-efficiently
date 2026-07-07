# [OMNI] origin=claude-code ts=2026-06-21 type=cli
# [OMNI] material_id="material:cli.commands.dispatch.py"
"""omni dispatch — 总控派发: 把一条消息路由到 5 类之一(本机 sonnet 中思考)。"""
from __future__ import annotations

import json

import click

from .._access import any_caller


@click.group("dispatch")
def cmd_dispatch() -> None:
    """总控派发(消息 → 跳转/发送/新起 的路由决策)。"""


@cmd_dispatch.command("route")
@click.option("--message", "-m", default=None, help="想发出的消息")
@click.option("--context", "-c", default=None, help="最近上下文(可选)")
@click.option("--input-b64", "input_b64", default=None,
              help="base64 的 JSON {message, context?, poof_panes?} —— 给 poof 等调用方避开 shell 转义")
@click.option("--all", "all_convos", is_flag=True, help="把所有(非仅在跑)对话纳入候选")
@click.option("--json", "as_json", is_flag=True, help="只输出决策 JSON(给 poof 等调用方)")
@click.option("--timeout", default=150, help="worker 超时秒")
@any_caller
def cmd_dispatch_route(message: str | None, context: str | None, input_b64: str | None,
                       all_convos: bool, as_json: bool, timeout: int) -> None:
    """把一条消息路由到 5 类之一, 输出决策。"""
    from omnicompany.dashboard.boss_sight.services.dispatch_router import route

    poof_panes = None
    if input_b64:
        import base64
        payload = json.loads(base64.b64decode(input_b64).decode("utf-8"))
        message = payload.get("message") or message
        context = payload.get("context") or context
        poof_panes = payload.get("poof_panes")
    if not message:
        raise click.ClickException("要么给 -m/--message, 要么给 --input-b64")

    try:
        decision = route(message, context=context, running_only=not all_convos,
                         poof_panes=poof_panes, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        if as_json:
            click.echo(json.dumps({"kind": "error", "error": f"{type(e).__name__}: {e}"},
                                  ensure_ascii=False))
            raise SystemExit(1) from e
        raise click.ClickException(f"路由失败: {e}") from e

    if as_json:
        click.echo(json.dumps(decision, ensure_ascii=False))
        return

    kind = decision.get("kind")
    labels = {
        "send_active_window": "→ 发给已活跃外部窗口",
        "send_poof_pane": "→ 发给 poof 在跑窗格",
        "new_with_project": "→ 新起(带项目上下文)",
        "new_strongest": "→ 新起(最强模型)",
        "ask_user": "？需要你选一个目标",
    }
    click.echo(click.style(labels.get(kind, kind), bold=True))
    if decision.get("target_identity"):
        click.echo(f"  目标: {decision['target_identity']}  ({decision.get('target_location')}, pane {decision.get('target_pane') or '-'})")
    if decision.get("project"):
        click.echo(f"  项目: {decision['project']}")
    if decision.get("provider"):
        click.echo(f"  CLI: {decision['provider']}")
    if decision.get("candidates"):
        click.echo(f"  候选: {', '.join(decision['candidates'])}")
    click.echo(f"  理由: {decision.get('reason')}")
    click.echo(f"  发送: {decision.get('text')}")


@cmd_dispatch.command("activate")
@click.option("--location", default=None, help="目标位置(vscode/codex桌面/chrome/...)")
@click.option("--key", default=None, help="provider:session_id, 从注册表取该对话的位置")
@click.option("--title-hint", default=None, help="多窗口时优选标题含该串的")
@click.option("--copy", "copy_text", default=None, help="顺手把这段文字放进剪贴板(粘贴即用)")
@click.option("--paste", is_flag=True, help="激活后发 Ctrl+V 粘进聚焦输入框(复制到对话框里)")
@click.option("--dry", is_flag=True, help="只找窗口不激活(验证用, 不抢焦点)")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_dispatch_activate(location: str | None, key: str | None, title_hint: str | None,
                          copy_text: str | None, paste: bool, dry: bool, as_json: bool) -> None:
    """把某个 app 的窗口激活到最前(send_active_window 的执行端)。"""
    from omnicompany.dashboard.boss_sight.services import winjump

    if not location and key:
        from omnicompany.dashboard.boss_sight.services.agent_registry import load_registry
        rec = load_registry().get(key)
        if rec:
            location = rec.get("location")
            title_hint = title_hint or rec.get("name")
    if not location:
        raise click.ClickException("要么给 --location, 要么给能在注册表里查到的 --key")

    if copy_text:
        from omnicompany.dashboard.boss_sight.services import winjump as _wj
        _wj.set_clipboard(copy_text)  # CF_UNICODETEXT, 中文不乱

    if dry:
        # 只列出该位置匹配到的窗口, 不激活
        from omnicompany.dashboard.boss_sight.services.winjump import _enum_windows, _proc_name, _procs_for
        want = {p.lower() for p in _procs_for(location)}
        hits = [{"pid": p, "title": t, "proc": _proc_name(p)} for (h, p, t) in _enum_windows()
                if _proc_name(p).lower().removesuffix(".exe") in want]
        out = {"dry": True, "location": location, "matches": hits}
    else:
        out = winjump.activate_location(location, title_hint=title_hint, paste=paste)

    click.echo(json.dumps(out, ensure_ascii=False) if as_json else json.dumps(out, ensure_ascii=False, indent=2))


__all__ = ["cmd_dispatch"]
