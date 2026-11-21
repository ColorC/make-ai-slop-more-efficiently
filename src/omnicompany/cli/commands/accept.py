# [OMNI] origin=claude-code domain=omnicompany/cli ts=2026-10-17T12:00:00+08:00 type=cli status=active agent=claude
# [OMNI] summary="omni accept —— 验收门 CLI：用户原话冻结为验收清单、记证据、确定性检查 + 独立验收者；done 通过才 exit 0。逻辑在 core/acceptance_gate.py（hook 共用）。"
# [OMNI] why="2026-10-17 长跑诊断 NEXT-CHANGES-v1 §2：完成声明要先定检查、结束时回到原始需求，由另一引擎新上下文独立验收。"
# [OMNI] tags=cli,gate,acceptance,hooks
"""omni accept —— 验收门（init / add / freeze / evidence / amend / check / verify / done / status）。"""
from __future__ import annotations

import json
from pathlib import Path

import click

from omnicompany.core import acceptance_gate as gate


def _echo(obj: object) -> None:
    click.echo(json.dumps(obj, ensure_ascii=False, indent=2))


def _fail(exc: Exception) -> None:
    click.echo(f"[accept] {exc}", err=True)
    raise SystemExit(1) from exc


_FILE = click.option("--file", "path", required=True, type=click.Path(path_type=Path), help="验收清单 JSON 绝对路径")


@click.group("accept")
def cmd_accept() -> None:
    """验收门：先按用户原话定检查，完成前由独立验收者核验。

    \b
    omni accept init --file A.json --title "..."
    omni accept add --file A.json --covers U1 --quote "<原话逐字片段>" --check "..." --layer L4 --user-path
    omni accept freeze --file A.json
    omni accept evidence --file A.json --item A1 --path <证据绝对路径>
    omni accept done --file A.json
    """


@cmd_accept.command("init")
@_FILE
@click.option("--title", required=True)
@click.option("--session", "session_id", default=None, help="会话号（默认取环境变量里的当前会话）")
@click.option("--provider", type=click.Choice(["claude_code", "codex"]), default=None)
@click.option("--request", "requests", multiple=True, help="补充一条用户原话（转录里取不到时）")
def accept_init(path: Path, title: str, session_id: str | None, provider: str | None, requests: tuple[str, ...]) -> None:
    """从当前会话转录取用户原话，建未冻结的验收清单（不覆盖已有文件）。"""
    try:
        provider, session_id = gate.current_session(provider, session_id)
        doc = gate.init(path, title=title, provider=provider, session_id=session_id, extra_requests=list(requests))
    except gate.GateError as exc:
        _fail(exc)
    click.echo(f"已建验收清单：{path.resolve()}（{len(doc['requests'])} 条用户原话）")
    for request in doc["requests"]:
        click.echo(f"  {request['id']}: {gate._clip(request['text'], 160)}")
    click.echo("下一步：按每条原话 omni accept add（至少一项 --user-path），然后 omni accept freeze。")


@cmd_accept.command("add")
@_FILE
@click.option("--covers", required=True, help="覆盖的原话编号，逗号分隔，如 U1,U3")
@click.option("--quote", required=True, help="原话里逐字出现的片段")
@click.option("--check", "check_text", required=True, help="怎么验（可执行的检查 / 观察点）")
@click.option("--layer", type=click.Choice(gate.LAYERS), required=True, help="L0 静态 … L4 用户真实路径")
@click.option("--user-path", is_flag=True, help="按用户会怎么用来验")
def accept_add(path: Path, covers: str, quote: str, check_text: str, layer: str, user_path: bool) -> None:
    try:
        item = gate.add_item(path, covers=[c.strip() for c in covers.split(",") if c.strip()], quote=quote,
                             check=check_text, layer=layer, user_path=user_path)
    except gate.GateError as exc:
        _fail(exc)
    click.echo(f"已加 {item['id']}（{layer}{'·用户路径' if user_path else ''}）：{path.resolve()}")


@cmd_accept.command("freeze")
@_FILE
@click.option("--session", "session_id", default=None)
@click.option("--provider", type=click.Choice(["claude_code", "codex"]), default=None)
def accept_freeze(path: Path, session_id: str | None, provider: str | None) -> None:
    """冻结验收项并绑定到当前会话（可选；长回合收尾提醒会附上清单路径）。"""
    try:
        provider, session_id = gate.current_session(provider, session_id)
        gate.freeze(path, provider=provider, session_id=session_id)
    except gate.GateError as exc:
        _fail(exc)
    click.echo(f"已冻结并绑定会话 {provider}:{session_id}：{path.resolve()}")
    click.echo(f"绑定记录：{gate.binding_path(session_id)}")


@cmd_accept.command("evidence")
@_FILE
@click.option("--item", "item_id", required=True)
@click.option("--path", "proof", type=click.Path(path_type=Path), default=None, help="证据文件绝对路径")
@click.option("--cmd", "command", default=None, help="实际运行的检查命令")
@click.option("--exit-code", type=int, default=None)
@click.option("--note", default="")
@click.option("--retract", type=int, default=None,
              help="撤回该项第 N 条（从 1 数）记错的证据，--note 写理由；记录保留给验收者看")
def accept_evidence(path: Path, item_id: str, proof: Path | None, command: str | None, exit_code: int | None,
                    note: str, retract: int | None) -> None:
    """记证据。同一条检查命令修好重跑后，以最后一次结果为准。"""
    try:
        if retract is not None:
            entry = gate.retract_evidence(path, item_id=item_id, index=retract, reason=note)
            click.echo(f"已撤回 {item_id} 第 {retract} 条证据：{entry.get('path') or entry.get('cmd')}")
            return
        entry = gate.add_evidence(path, item_id=item_id, proof=proof, note=note, command=command, exit_code=exit_code)
    except gate.GateError as exc:
        _fail(exc)
    click.echo(f"已记 {item_id} 证据：{entry.get('path') or entry.get('cmd')}")


@cmd_accept.command("move")
@_FILE
@click.option("--to", "new_path", required=True, type=click.Path(path_type=Path), help="新位置（清单 JSON 绝对路径）")
@click.option("--with", "carry", multiple=True, type=click.Path(path_type=Path),
              help="一起搬到新目录的证据文件或目录，可重复；旁边的验收原文自动跟着走")
def accept_move(path: Path, new_path: Path, carry: tuple[Path, ...]) -> None:
    """搬清单位置：证据路径、冻结快照、会话绑定一起改指向；内容不动，已有的通过仍有效。"""
    try:
        result = gate.move(path, new_path, carry=list(carry))
    except gate.GateError as exc:
        _fail(exc)
    click.echo(f"已搬到：{result['file']}")
    for item in result["carried"]:
        click.echo(f"  一起搬：{item}")
    click.echo(f"  改指向的会话绑定：{len(result['rebound'])}")


@cmd_accept.command("amend")
@_FILE
@click.option("--item", "item_id", default=None)
@click.option("--field", required=True,
              type=click.Choice(["add", "check", "layer", "quote", "remove", "waive-request"]))
@click.option("--value", required=True, help="新值；add 时是原话逐字片段；waive-request 时是原话编号")
@click.option("--basis-quote", required=True, help="用户改要求的原话（逐字）")
@click.option("--covers", default=None)
@click.option("--layer", type=click.Choice(gate.LAYERS), default="L4")
@click.option("--user-path", is_flag=True)
def accept_amend(path: Path, item_id: str | None, field: str, value: str, basis_quote: str, covers: str | None,
                 layer: str, user_path: bool) -> None:
    """冻结后改验收项：只能凭用户原话。"""
    try:
        record = gate.amend(path, item_id=item_id, field=field, value=value, basis_quote=basis_quote,
                            covers=[c.strip() for c in covers.split(",")] if covers else None,
                            layer=layer, user_path=user_path)
    except gate.GateError as exc:
        _fail(exc)
    click.echo(f"已修订 {record.get('item') or ''} {field}：{path.resolve()}")


@cmd_accept.command("refresh")
@_FILE
def accept_refresh(path: Path) -> None:
    """补入冻结后用户新说的话（它们也必须被覆盖）。"""
    added = gate.refresh(path)
    click.echo(f"新增用户原话 {len(added)} 条" + "".join(f"\n  {r['id']}: {gate._clip(r['text'], 160)}" for r in added))


@cmd_accept.command("check")
@_FILE
@click.option("--json", "as_json", is_flag=True)
def accept_check(path: Path, as_json: bool) -> None:
    """确定性检查（覆盖、逐字引用、用户路径项、冻结未被改、证据新鲜且存在）。不过 exit 2。"""
    try:
        result = gate.check(path)
    except gate.GateError as exc:
        _fail(exc)
    if as_json:
        _echo(result)
    else:
        click.echo(("[通过] " if result["ok"] else "[未过] ") + result["file"])
        for problem in result["problems"]:
            click.echo(f"  - {problem}")
    if not result["ok"]:
        raise SystemExit(2)


@cmd_accept.command("verify")
@_FILE
@click.option("--engine", type=click.Choice(["codex", "claude"]), default=None, help="默认与干活的引擎相反")
@click.option("--model", default=None)
@click.option("--timeout", type=int, default=1800)
def accept_verify(path: Path, engine: str | None, model: str | None, timeout: int) -> None:
    """只跑独立验收者（新上下文、只读）。"""
    verdict = gate.verify(path, engine=engine, model=model, timeout=timeout)
    _echo(verdict)
    if not verdict["pass"]:
        raise SystemExit(2)


@cmd_accept.command("done")
@_FILE
@click.option("--engine", type=click.Choice(["codex", "claude"]), default=None)
@click.option("--model", default=None)
@click.option("--timeout", type=int, default=1800)
def accept_done(path: Path, engine: str | None, model: str | None, timeout: int) -> None:
    """宣布完成前跑：refresh → check → 独立验收。全过 exit 0，否则 exit 2 并列出未过项。"""
    try:
        result = gate.done(path, engine=engine, model=model, timeout=timeout)
    except gate.GateError as exc:
        _fail(exc)
    _echo(result)
    if not result["ok"]:
        raise SystemExit(2)


@cmd_accept.command("status")
@_FILE
def accept_status(path: Path) -> None:
    try:
        click.echo(gate.status_text(path, budget=100_000))
    except gate.GateError as exc:
        _fail(exc)


@cmd_accept.command("bind")
@_FILE
@click.option("--session", "session_id", default=None)
@click.option("--provider", type=click.Choice(["claude_code", "codex"]), default=None)
def accept_bind(path: Path, session_id: str | None, provider: str | None) -> None:
    """把已冻结的清单绑定到另一个会话（交接 / 继任时用）。"""
    try:
        if not gate.load(path).get("frozen_at"):
            raise gate.GateError("清单还没冻结")
        provider, session_id = gate.current_session(provider, session_id)
    except gate.GateError as exc:
        _fail(exc)
    click.echo(f"已绑定：{gate.bind(path, provider, session_id)}")


@cmd_accept.command("unbind")
@click.option("--session", "session_id", default=None)
@click.option("--provider", type=click.Choice(["claude_code", "codex"]), default=None)
@click.option("--reason", required=True, help="为什么解除（写进清单的 gate_overrides）")
def accept_unbind(session_id: str | None, provider: str | None, reason: str) -> None:
    try:
        provider, session_id = gate.current_session(provider, session_id)
    except gate.GateError as exc:
        _fail(exc)
    bound = gate.bound_acceptance(session_id)
    if bound:
        doc = gate.load(bound)
        doc.setdefault("gate_overrides", []).append({"at": gate._now(), "reason": f"unbind: {reason}"})
        gate._atomic_write_json(bound, doc)
    gate.binding_path(session_id).unlink(missing_ok=True)
    click.echo(f"已解除 {provider}:{session_id} 的绑定" + (f"（清单 {bound}）" if bound else ""))


_STATS_LABELS = {
    "init": "建清单", "freeze": "冻结并绑定", "amend": "按原话改条目", "verdict_pass": "独立验收通过",
    "verdict_fail": "独立验收未过", "done_check_failed": "done 确定性检查未过", "stop_block": "Stop 拦下未验收的完成声明",
    "stop_override": "连拦后放行", "stop_nudge_unbound": "未绑定长会话首次声称完成被提醒",
    "stop_remind": "长回合首次声称完成被提醒（每会话一次）",
    "goal_deny": "Codex update_goal 完成被拒", "evidence_retract": "撤回记错的证据", "move": "搬动清单位置",
    "deny": "子线程续派活被拒", "allow_after_strikes": "子线程连拒后放行",
}


@cmd_accept.command("stats")
@click.option("--since", default=None, help="起始时间（ISO，如 2026-10-17 或 2026-10-17T12:00+08:00）")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def accept_stats(since: str | None, as_json: bool) -> None:
    """汇总验收门与子线程策略的决策台账（events.jsonl），复盘长跑设施有没有被用上。"""
    from omnicompany.core import subagent_policy

    report = {
        "acceptance": gate.summarize_events(gate.state_root() / "events.jsonl", since),
        "subagent_policy": gate.summarize_events(subagent_policy._state_root() / "events.jsonl", since),
    }
    if as_json:
        _echo(report)
        return
    for name, part in report.items():
        click.echo(f"[{name}] 台账 {part['file']}  会话 {part['sessions']}  时间 {part['first'] or '-'} → {part['last'] or '-'}")
        for kind, count in part["kinds"].items():
            click.echo(f"  {_STATS_LABELS.get(kind, kind)}（{kind}）: {count}")
        if not part["kinds"]:
            click.echo("  （无记录）")
    click.echo("远程操作另看 `omni remote stats`。")


@cmd_accept.command("install-hooks")
@click.option("--provider", type=click.Choice(["claude_code", "codex"]), required=True)
@click.option("--target", required=True, type=click.Path(path_type=Path), help="settings.json 或 hooks.json 绝对路径")
@click.option("--codex-config", type=click.Path(path_type=Path), default=None, help="Codex config.toml（写信任哈希）")
@click.option("--python", "python", type=click.Path(path_type=Path), default=gate._PY,
              help="跑 hook 的解释器（脚本纯标准库，任何 3.10+ 都行）")
@click.option("--script", type=click.Path(path_type=Path), default=Path(gate.__file__).resolve())
def accept_install_hooks(provider: str, target: Path, codex_config: Path | None, python: Path, script: Path) -> None:
    """幂等追加 Stop（Codex 另加子线程策略 PreToolUse），并移除已退役的 SessionStart / update_goal 验收组。"""
    _echo(gate.install_hooks(provider, target.absolute(), script=script, python=python, codex_config=codex_config))


__all__ = ["cmd_accept"]
