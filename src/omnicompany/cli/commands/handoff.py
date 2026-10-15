# [OMNI] origin=claude-code domain=cli/commands ts=2026-08-10T00:00:00Z type=router status=active
# [OMNI] summary="omni handoff CLI — 交接方写文档并批量创建继承会话; 接手方登记、深读并写接手文档"
# [OMNI] why="预算/上下文见底时要在 claude 与 codex 之间来回交接, 手写散落 handoff.md 没索引没校验, 接手方找不到入口"
# [OMNI] tags=cli,handoff,session,intake,inheritance
# [OMNI] material_id="material:cli.commands.session_handoff.py"
"""omni handoff — 跨会话/跨 CLI 交接套件.

交接方(要交出去):  draft → 补正文 → seal → spawn(可批量)
接手方(被交接):    current → adopt → 深读真源 → draft --kind intake → seal → /compact

文档是唯一权威叙述, CLI 只管骨架、机器事实、落位、校验、索引和继任会话谱系。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import click

from .._access import any_caller, external_or_controller


PROVIDERS = ("claude_code", "claude_2", "codex", "codex_2", "kimi", "opencode", "codebuddy", "kiro", "qoder")

# 开场种子的两种真实失败(2026-08-10 实测, 对 omni agent start 同样成立):
#   1. PTY 刚创建、provider TUI 还没就绪, daemon 打进去的字被整段吞掉(终端只有 banner);
#   2. 字进了输入框但没提交 —— daemon 把「正文 + \r」一次写入, codex 这类 TUI 的
#      粘贴突发检测把换行当字面换行、把结尾回车也吃掉, 整段停在 `›` 提示符里。
# 两种都表现为"会话活着但一直没人干活"。判据不能用终端字节数(它有 256KB 上限、
# 还会因重绘乱长), 只能用"真的开了一轮"的语义信号: provider_session_id / working。
_SEED_POLL_S = 3.0
#: 新版 ccdaemon 自己就会分两次写 + 逐档补救(最坏约 40s), 所以这里先安静地等它,
#: 别两边同时往同一个输入框打字。这一段只在**旧 daemon 没有那套补救**时才真正出手,
#: 而且 _wait_turn 一看到开跑就立刻返回, 顺利时不会真等这么久。
_SEED_BOOT_WAIT_S = 45.0
#: 每一档补救(补回车 / 重投正文+回车)之后各等这么久看有没有真开一轮。
_SEED_STAGE_WAIT_S = 24.0
#: 正文与回车之间留一点间隔, 别又被算成同一次粘贴突发。
_SEED_ENTER_GAP_S = 1.2


def _core():
    from omnicompany.core import handoffs

    return handoffs


def _need(handoff_id: str) -> dict:
    row = _core().get_record(handoff_id)
    if row is None:
        raise click.ClickException(f"找不到交接记录: {handoff_id}(`omni handoff list` 看有哪些)")
    return row


def _doc_abs(record: dict) -> Path:
    doc = record.get("doc")
    if not doc:
        raise click.ClickException(f"记录 {record['id']} 还没有文档(先 `omni handoff draft`)")
    return _core().to_abs(doc)


def _brief(record: dict) -> str:
    binding = record.get("binding") or {}
    return (
        f"{record['id']}  [{record.get('kind')}/{record.get('status')}]  {record.get('title')}\n"
        f"    doc: {record.get('doc') or '-'}\n"
        f"    绑定: plan={binding.get('plan') or '-'} project={binding.get('project') or '-'}"
        f" workstream={binding.get('workstream') or '-'}"
    )


def _session_row(pty_id: str) -> dict | None:
    from omnicompany.packages.services._core.agent.control_plane import list_agent_workers

    try:
        rows = list_agent_workers()
    except RuntimeError:
        return None
    for row in rows:
        if str(row.get("id")) == pty_id:
            return row
    return None


def _turn_started(row: dict) -> bool:
    """真开了一轮的语义信号(provider 原生会话 id 出现 / 正在跑), 不看终端字节数。"""
    return bool(
        row.get("provider_session_id")
        or row.get("working")
        or str(row.get("provider_runtime_state") or "") == "working"
    )


def _wait_turn(pty_id: str, seconds: float) -> tuple[str, dict]:
    """等这条会话真开一轮: ("started"|"idle"|"gone", 最后看到的 row)。"""
    deadline = time.monotonic() + max(0.0, seconds)
    row: dict = {}
    while True:
        row = _session_row(pty_id) or {}
        if not row or not row.get("alive"):
            return "gone", row
        if _turn_started(row):
            return "started", row
        if time.monotonic() >= deadline:
            return "idle", row
        time.sleep(_SEED_POLL_S)


def _send_raw(pty_id: str, data: str) -> bool:
    """原样写进 PTY(不自动追加回车) —— 正文与回车必须分两次发, 见上方注释。"""
    from omnicompany.packages.services._core.agent.control_plane import send_agent_input

    try:
        try:
            send_agent_input(pty_id, data, submit=False)
        except TypeError:
            # 控制面还是老签名(没有 submit) → 退回追加回车的老行为: 回车本身照样发得出去,
            # 正文那一次会退化成"正文+回车一次写", 也就是这段代码要绕的那个失败形。
            send_agent_input(pty_id, data)
    except RuntimeError:
        return False
    return True


def _ensure_seed_submitted(pty_id: str, seed: str) -> tuple[str, str | None]:
    """确认继任会话真的开始干活, 没开就逐档补救。

    返回 (状态, provider 原生会话 id):
      landed     — daemon 自动投递的那份自己就生效了
      submitted  — 字在输入框里没提交, 补一个单独回车后开跑
      reseeded   — 整段被吞了, 重投正文 + 单独回车后开跑
      unconfirmed— 补救完还是没动静(去那条会话看一眼)
      gone/unknown
    """
    if _session_row(pty_id) is None:
        return "unknown", None

    status, row = _wait_turn(pty_id, _SEED_BOOT_WAIT_S)
    if status == "started":
        return "landed", row.get("provider_session_id")
    if status == "gone":
        return "gone", None

    if not _send_raw(pty_id, "\r"):
        return "unconfirmed", None
    status, row = _wait_turn(pty_id, _SEED_STAGE_WAIT_S)
    if status == "started":
        return "submitted", row.get("provider_session_id")
    if status == "gone":
        return "gone", None

    if not _send_raw(pty_id, seed):
        return "unconfirmed", None
    time.sleep(_SEED_ENTER_GAP_S)
    if not _send_raw(pty_id, "\r"):
        return "unconfirmed", None
    status, row = _wait_turn(pty_id, _SEED_STAGE_WAIT_S)
    if status == "started":
        return "reseeded", row.get("provider_session_id")
    return ("gone" if status == "gone" else "unconfirmed"), None


@click.group("handoff")
def cmd_handoff() -> None:
    """会话交接: 写交接文档 / 创建继承会话 / 接手登记与接手文档。"""


# ── 交接方 ───────────────────────────────────────────────────────────────────


@cmd_handoff.command("draft")
@click.option("--title", required=True, help="一句话说清这次交接的是什么工作")
@click.option("--kind", type=click.Choice(["handoff", "intake"]), default="handoff", show_default=True,
              help="handoff=交接方写; intake=接手方深读后写的初始文档")
@click.option("--reason", default="", help="为什么交接: budget / context / shift / cross-cli ...")
@click.option("--of", "of_id", default=None, help="intake 专用: 这份接手文档对应哪条交接记录")
@click.option("--plan", default=None, help="默认取本会话绑定")
@click.option("--project", default=None)
@click.option("--workstream", default=None)
@click.option("--task", default=None)
@click.option("--doc", "doc_path", default=None, help="指定文档落位(默认: 有计划进计划目录, 否则 docs/handoffs/<月>/)")
@click.option("--force", is_flag=True, help="目标文档已存在时覆盖")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_draft(title: str, kind: str, reason: str, of_id: str | None, plan: str | None,
              project: str | None, workstream: str | None, task: str | None,
              doc_path: str | None, force: bool, as_json: bool) -> None:
    """起草交接/接手文档骨架(预填机器事实), 正文由你自己补齐。"""
    core = _core()
    if kind == "intake" and of_id:
        of_record = _need(of_id)
        of_id = of_record["id"]
        plan = plan or (of_record.get("binding") or {}).get("plan")
        project = project or (of_record.get("binding") or {}).get("project")
        workstream = workstream or (of_record.get("binding") or {}).get("workstream")
    try:
        record, facts = core.create_record(
            kind=kind, title=title, reason=reason, plan=plan, project=project,
            workstream=workstream, task=task, of=of_id,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None

    target = (Path(doc_path) if doc_path else
              core.doc_target(kind, plan=(record["binding"] or {}).get("plan"),
                              slug=core.slugify(title, 32)))
    target = core.to_abs(target)
    if target.exists() and not force:
        raise click.ClickException(f"文档已存在: {target}(要覆盖加 --force)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(core.render_draft(record, facts), encoding="utf-8")
    record["doc"] = core.to_rel(target)
    core.save_record(record)
    if kind == "intake" and of_id:
        parent = core.get_record(of_id)
        if parent is not None:
            parent.setdefault("intake_docs", []).append(record["doc"])
            core.save_record(parent)

    if as_json:
        click.echo(json.dumps({"ok": True, "record": record, "doc": str(target)}, ensure_ascii=False, indent=2))
        return
    click.echo(f"OK 骨架已落地: {target}")
    click.echo(f"   记录: {record['id']}")
    click.echo("   下一步: 用编辑器补齐每个段落(把 TODO(填写) 换成真内容, 结论后面挂证据入口),")
    click.echo(f"           然后 `omni handoff seal {record['id']}`。")


@cmd_handoff.command("verify")
@click.argument("target")
@click.option("--kind", type=click.Choice(["handoff", "intake"]), default=None, help="直接给路径时用来指定类型")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_verify(target: str, kind: str | None, as_json: bool) -> None:
    """校验文档: 段落齐不齐、有没有留 TODO、必读入口的路径存不存在。"""
    core = _core()
    record = core.get_record(target)
    if record is not None:
        doc, kind = _doc_abs(record), record.get("kind") or "handoff"
    else:
        doc, record = core.to_abs(target), None
        kind = kind or ("intake" if "intake" in doc.name else "handoff")
    problems = core.verify_doc(doc, kind, record=record)
    if as_json:
        click.echo(json.dumps({"ok": not problems, "doc": str(doc), "problems": problems},
                              ensure_ascii=False, indent=2))
    else:
        click.echo(f"文档: {doc}")
        if not problems:
            click.echo(click.style("  合格", fg="green"))
        for item in problems:
            click.echo(click.style(f"  ✗ {item}", fg="red"))
    if problems:
        raise SystemExit(1)


@cmd_handoff.command("seal")
@click.argument("handoff_id")
@click.option("--no-mirror", is_flag=True, help="不写 workstream checkpoint / plan 进度")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_seal(handoff_id: str, no_mirror: bool, as_json: bool) -> None:
    """校验通过才封版: 记录转 ready, 并镜像一条 workstream checkpoint / plan 进度。"""
    core = _core()
    record = _need(handoff_id)
    doc = _doc_abs(record)
    problems = core.verify_doc(doc, record.get("kind") or "handoff", record=record)
    if problems:
        for item in problems:
            click.echo(click.style(f"  ✗ {item}", fg="red"), err=True)
        raise click.ClickException("文档没过校验, 不给封版 —— 先补齐再来")

    mirrored: dict[str, str] = {}
    binding = record.get("binding") or {}
    if not no_mirror:
        workstream = binding.get("workstream")
        if workstream:
            try:
                from omnicompany.core.workstreams import add_checkpoint

                add_checkpoint(
                    workstream,
                    f"[{record.get('kind')}] {record['title']}",
                    kind="handoff",
                    refs=[record["doc"], f"handoff:{record['id']}"],
                    source=record.get("from") or {},
                )
                mirrored["workstream"] = workstream
            except Exception as exc:  # noqa: BLE001 - 镜像失败不该挡住交接
                mirrored["workstream_error"] = str(exc)
        plan = binding.get("plan")
        if plan:
            try:
                from omnicompany.packages.services.boss_sight.progress import add_entry

                add_entry("plan", plan, f"交接文档 {record['id']}: {record['title']} → {record['doc']}",
                          by="handoff")
                mirrored["plan"] = plan
            except Exception as exc:  # noqa: BLE001
                mirrored["plan_error"] = str(exc)

    record["status"] = "ready"
    core.save_record(record)
    if as_json:
        click.echo(json.dumps({"ok": True, "record": record, "mirrored": mirrored},
                              ensure_ascii=False, indent=2))
        return
    click.echo(click.style(f"OK {record['id']} 已封版(ready)", fg="green"))
    for key, value in mirrored.items():
        click.echo(f"   镜像 {key}: {value}")
    click.echo(f"   下一步: `omni handoff spawn {record['id']} --to codex`(可多个 --to / --slice 批量)")


@cmd_handoff.command("spawn")
@click.argument("handoff_id")
@click.option("--to", "targets", multiple=True, type=click.Choice(PROVIDERS), required=True,
              help="继任会话的 CLI; 可重复(批量)")
@click.option("--slice", "slices", multiple=True,
              help="把工作切片, 一个切片一个继任会话; 不给=整份范围")
@click.option("--model", default=None, help="继任会话模型(provider 原生 id)")
@click.option("--cwd", default=None, help="继任会话工作目录(默认沿用交接方 cwd)")
@click.option("--visibility", type=click.Choice(["visible", "hidden"]), default="visible", show_default=True)
@click.option("--fork", is_flag=True, help="claude_code 专用: 从交接方原会话 fork(继承完整历史)")
@click.option("--print-only", is_flag=True, help="只打印 seed prompt 不真开会话(没 ccdaemon 时人工贴)")
@click.option("--no-confirm", is_flag=True,
              help="不确认开场种子是否落地(默认会等一会儿, 被 TUI 吞掉就补投一次)")
@click.option("--json", "as_json", is_flag=True)
@external_or_controller
def cmd_spawn(handoff_id: str, targets: tuple[str, ...], slices: tuple[str, ...],
              model: str | None, cwd: str | None, visibility: str, fork: bool,
              print_only: bool, no_confirm: bool, as_json: bool) -> None:
    """按交接文档创建一个或一批继承会话(开场即指向文档 + 复验要求)。"""
    core = _core()
    record = _need(handoff_id)
    if record.get("status") == "draft":
        raise click.ClickException(f"{record['id']} 还是 draft —— 先 `omni handoff seal {record['id']}`")
    doc_abs = _doc_abs(record)
    if not doc_abs.is_file():
        raise click.ClickException(f"文档文件不存在: {doc_abs}")

    # 切片 × 目标的配对: 1×N / N×1 / N×N 三种明确形态, 其它形态拒绝猜。
    slice_list = [s for s in slices if str(s).strip()] or [None]
    if len(targets) == 1:
        pairs = [(targets[0], item) for item in slice_list]
    elif len(slice_list) == 1:
        pairs = [(provider, slice_list[0]) for provider in targets]
    elif len(slice_list) == len(targets):
        pairs = list(zip(targets, slice_list))
    else:
        raise click.ClickException(
            f"--to({len(targets)}) 与 --slice({len(slice_list)}) 数量不匹配; "
            "要么一个 provider 配多个切片, 要么多个 provider 配一个范围, 要么一一对应"
        )

    origin = record.get("from") or {}
    binding = record.get("binding") or {}
    work_cwd = cwd or origin.get("cwd") or None
    results: list[dict] = []
    for provider, slice_text in pairs:
        seed = core.seed_prompt(record, doc_abs=str(doc_abs), slice_text=slice_text)
        row: dict = {"provider": provider, "slice": slice_text,
                     "mode": "fork" if fork else "new", "seed": seed}
        if print_only:
            results.append(row)
            continue
        if fork:
            if provider != "claude_code":
                raise click.ClickException("--fork 只有 claude_code 支持(其它 CLI 用 --to 新起)")
            if not origin.get("session_id"):
                raise click.ClickException("交接方没有可 fork 的 provider session id")
        from omnicompany.packages.services._core.agent.control_plane import (
            AgentLaunchRequest,
            launch_agent_worker,
        )

        try:
            handle = launch_agent_worker(
                AgentLaunchRequest(
                    task=seed,
                    provider=provider,
                    model=model,
                    cwd=work_cwd,
                    project=binding.get("project"),
                    workstream=binding.get("workstream"),
                    active_plan=binding.get("plan"),
                    topic=f"接手 {record['id']}: {record['title']}"[:200],
                    parent_agent_id=origin.get("pty_id") or origin.get("trace_id"),
                    fork_from_provider_session_id=origin.get("session_id") if fork else None,
                    visibility=visibility,
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            hint = ""
            if "404" in str(exc):
                # 在跑的 ccdaemon 比磁盘上的代码旧, /cc/agent-workers 还没上线。
                hint = ("\n  在跑的 ccdaemon 没有 /cc/agent-workers 路由(旧进程)。"
                        "\n  要么 `omni cc daemon restart`(会影响在跑的 PTY 页签, 先问用户), "
                        f"要么 `omni handoff spawn {record['id']} --to {provider} --print-only` "
                        "把 seed 贴进你自己开的 CLI。")
            raise click.ClickException(f"起继任会话失败({provider}): {exc}{hint}") from None
        row.update(worker_id=handle.worker_id, pty_id=handle.pty_id,
                   provider_session_id=handle.provider_session_id)
        if not no_confirm:
            row["seed_status"], native_id = _ensure_seed_submitted(handle.pty_id, seed)
            if native_id:
                # 拿到 provider 原生会话 id, 这一棒以后才能被 resume / fork。
                row["provider_session_id"] = native_id
        core.attach_successor(record, {k: v for k, v in row.items() if k != "seed"})
        results.append(row)

    if as_json:
        click.echo(json.dumps({"ok": True, "handoff": record["id"], "doc": str(doc_abs),
                               "successors": results}, ensure_ascii=False, indent=2))
        return
    if print_only:
        for row in results:
            click.echo(click.style(f"── seed for {row['provider']} "
                                   f"({row['slice'] or '整份范围'}) ──", fg="cyan"))
            click.echo(row["seed"])
            click.echo("")
        return
    labels = {"landed": "已开跑", "submitted": "字卡在输入框→补回车后开跑",
              "reseeded": "开场被吞→重投正文+回车后开跑",
              "unconfirmed": "没确认它开跑(去那条会话看一眼, 可能停在提示符)",
              "gone": "会话已不在了", "unknown": "查不到会话状态"}
    for row in results:
        status = row.get("seed_status")
        click.echo(f"OK {row['provider']} → pty {row['pty_id']}  ({row['mode']}; "
                   f"{row['slice'] or '整份范围'})"
                   + (f"  · {labels.get(status, status)}" if status else ""))
    click.echo(f"   文档: {doc_abs}")


# ── 接手方 ───────────────────────────────────────────────────────────────────


@cmd_handoff.command("current")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_current(as_json: bool) -> None:
    """本会话该读哪一份交接(按继任谱系 → 计划 → 工作目录解析)。"""
    core = _core()
    record = core.resolve_current()
    if record is None:
        if as_json:
            click.echo(json.dumps({"ok": False, "record": None}, ensure_ascii=False))
            return
        click.echo("(没解析到与本会话相关的交接; `omni handoff list` 自己挑一条)")
        raise SystemExit(1)
    if as_json:
        click.echo(json.dumps({"ok": True, "record": record, "doc": str(_doc_abs(record))},
                              ensure_ascii=False, indent=2))
        return
    click.echo(_brief(record))
    click.echo(f"    完整文档: {_doc_abs(record)}")
    click.echo(f"    接手: omni handoff adopt {record['id']}")


@cmd_handoff.command("adopt")
@click.argument("handoff_id", required=False)
@click.option("--note", default="", help="接手时想记一句(例: 与文档不符的地方)")
@click.option("--no-bind", is_flag=True, help="不把本会话绑到该交接的 plan/project/workstream")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_adopt(handoff_id: str | None, note: str, no_bind: bool, as_json: bool) -> None:
    """登记接手(并把本会话绑到同一 plan/project/workstream)。"""
    core = _core()
    record = _need(handoff_id) if handoff_id else core.resolve_current()
    if record is None:
        raise click.ClickException("没给 id 又解析不出当前交接; 先 `omni handoff list`")

    meta: dict = {}
    try:
        from omnicompany.packages.services._core.identity import current_session_meta

        meta = dict(current_session_meta())
    except Exception:  # noqa: BLE001
        meta = {}
    core.mark_adopted(record, adopter={
        "trace_id": meta.get("trace_id"), "provider": meta.get("provider"),
        "pty_id": meta.get("pty_id"), "session_id": meta.get("session_id"),
    }, note=note)

    bound: dict[str, str] = {}
    binding = record.get("binding") or {}
    if not no_bind and meta.get("trace_id"):
        fields = {key: binding.get(key) for key in ("project", "workstream") if binding.get(key)}
        if binding.get("plan"):
            fields["active_plan"] = binding["plan"]
        if fields:
            try:
                from omnicompany.packages.services._core.identity import update_session_binding

                update_session_binding(meta["trace_id"], source="handoff_adopt",
                                       topic=f"接手 {record['id']}: {record['title']}"[:200], **fields)
                bound = {k: str(v) for k, v in fields.items()}
            except Exception as exc:  # noqa: BLE001
                bound = {"error": str(exc)}

    if as_json:
        click.echo(json.dumps({"ok": True, "record": record, "bound": bound,
                               "doc": str(_doc_abs(record))}, ensure_ascii=False, indent=2))
        return
    click.echo(click.style(f"OK 已登记接手 {record['id']}", fg="green"))
    if bound:
        click.echo("   绑定: " + " ".join(f"{k}={v}" for k, v in bound.items()))
    click.echo(f"   文档: {_doc_abs(record)}")
    click.echo("   接着做: 读完文档 → 跑一条复验命令自证 → 有必要就 "
               f"`omni handoff draft --kind intake --of {record['id']} --title \"...\"`")


@cmd_handoff.command("note")
@click.argument("handoff_id")
@click.argument("text")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_note(handoff_id: str, text: str, as_json: bool) -> None:
    """给交接记录追一条备注(文档与实际不符、接手进展、被推翻的结论)。"""
    core = _core()
    record = _need(handoff_id)
    by = ""
    try:
        from omnicompany.packages.services._core.identity import current_session_meta

        by = str(current_session_meta().get("trace_id") or "")
    except Exception:  # noqa: BLE001
        by = ""
    try:
        core.add_note(record, text, by=by)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    if as_json:
        click.echo(json.dumps({"ok": True, "record": record}, ensure_ascii=False, indent=2))
        return
    click.echo(f"OK 已记 {len(record.get('notes') or [])} 条备注 → {record['id']}")


# ── 检索 ─────────────────────────────────────────────────────────────────────


@cmd_handoff.command("list")
@click.option("--kind", type=click.Choice(["handoff", "intake"]), default=None)
@click.option("--status", type=click.Choice(["draft", "ready", "handed", "adopted", "closed"]), default=None)
@click.option("--plan", default=None)
@click.option("--project", default=None)
@click.option("--limit", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_list(kind: str | None, status: str | None, plan: str | None,
             project: str | None, limit: int, as_json: bool) -> None:
    """列交接记录(默认按时间倒序)。"""
    rows = _core().list_records(kind=kind, status=status, plan=plan, project=project, limit=limit)
    if as_json:
        click.echo(json.dumps({"items": rows}, ensure_ascii=False, indent=2))
        return
    if not rows:
        click.echo("(无)")
        return
    for row in rows:
        click.echo(_brief(row))


@cmd_handoff.command("show")
@click.argument("handoff_id")
@click.option("--seed", "show_seed", is_flag=True, help="打印继任会话的 seed prompt(人工贴用)")
@click.option("--doc", "show_doc", is_flag=True, help="把文档正文打出来")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_show(handoff_id: str, show_seed: bool, show_doc: bool, as_json: bool) -> None:
    """看一条交接记录: 谱系、备注、文档路径。"""
    core = _core()
    record = _need(handoff_id)
    doc = core.to_abs(record["doc"]) if record.get("doc") else None
    if as_json:
        payload = {"record": record, "doc": str(doc) if doc else None}
        if show_seed and doc:
            payload["seed"] = core.seed_prompt(record, doc_abs=str(doc))
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    click.echo(_brief(record))
    click.echo(f"    完整文档: {doc or '-'}")
    if record.get("reason"):
        click.echo(f"    交接原因: {record['reason']}")
    origin = record.get("from") or {}
    click.echo(f"    交接方: {origin.get('provider') or '?'}:{origin.get('session_id') or '-'}"
               f"  cwd={origin.get('cwd') or '-'}")
    if origin.get("transcript"):
        click.echo(f"    原始 transcript: {origin['transcript']}")
    for successor in record.get("successors") or []:
        click.echo(f"    继任: {successor.get('provider')} pty={successor.get('pty_id')}"
                   f" [{successor.get('mode')}] {successor.get('slice') or '整份范围'}")
    for adopter in record.get("adopted_by") or []:
        click.echo(f"    接手: {adopter.get('provider') or '?'} {adopter.get('trace_id') or '-'}"
                   f" @{(adopter.get('at') or '')[:19]} {adopter.get('note') or ''}")
    for item in record.get("intake_docs") or []:
        click.echo(f"    接手文档: {item}")
    for note in record.get("notes") or []:
        click.echo(f"    备注 {(note.get('at') or '')[:19]}: {note.get('text')}")
    if show_seed and doc:
        click.echo(click.style("── seed prompt ──", fg="cyan"))
        click.echo(core.seed_prompt(record, doc_abs=str(doc)))
    if show_doc and doc and doc.is_file():
        click.echo(click.style("── 文档正文 ──", fg="cyan"))
        click.echo(doc.read_text(encoding="utf-8"))


__all__ = ["cmd_handoff"]
