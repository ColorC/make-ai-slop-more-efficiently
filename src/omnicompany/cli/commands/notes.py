# [OMNI] origin=claude-code ts=2026-06-22 type=cli
# [OMNI] material_id="material:cli.commands.notes.py"
"""omni notes — 操控 poof 里的 BlockSuite 笔记(经文件命令队列桥)。需 poof 在跑。

给 codex 总控用: 任意 增/删/改 笔记元素属性、搜索笔记/元素、列模板、居中定位、刷新。
桥: CLI 写 `%LOCALAPPDATA%/poof/notes-bridge/req-<id>.json`, poof 前端轮询执行(在活的
BlockSuite collection 上做定向 op, 不整 doc 替换 → 不损坏笔记), 写回 res-<id>.json。
"""
from __future__ import annotations

import json
import os
import time
import uuid

import click

from .._access import any_caller, external_or_controller


def _bridge_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    d = os.path.join(base, "poof", "notes-bridge")
    os.makedirs(d, exist_ok=True)
    return d


def _call(op: str, *, timeout: float = 25.0, **args):
    d = _bridge_dir()
    rid = uuid.uuid4().hex[:12]
    req = os.path.join(d, f"req-{rid}.json")
    res = os.path.join(d, f"res-{rid}.json")
    tmp = req + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"op": op, **args}, f, ensure_ascii=False)
    os.replace(tmp, req)  # 原子, poof 不会读到半截
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(res):
            try:
                with open(res, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                time.sleep(0.2)
                continue
            try:
                os.remove(res)
            except OSError:
                pass
            return data
        time.sleep(0.4)
    try:
        os.remove(req)
    except OSError:
        pass
    raise click.ClickException("poof 没响应(要 poof 在跑;命令在活笔记 collection 上执行)")


@click.group("notes")
def cmd_notes() -> None:
    """操控 poof 笔记(增删改元素 / 搜索 / 模板 / 居中 / 刷新, 经桥, 需 poof 在跑)。"""


def _emit(data) -> None:
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


@cmd_notes.command("new")
@click.option("--title", default="新笔记")
@click.option("--text", default="")
@any_caller
def cmd_notes_new(title: str, text: str) -> None:
    """新建一条笔记。"""
    _emit(_call("new", title=title, text=text))


@cmd_notes.command("list")
@any_caller
def cmd_notes_list() -> None:
    """列所有笔记(id / 标题 / 元素数)。"""
    _emit(_call("list"))


@cmd_notes.command("search")
@click.argument("query")
@any_caller
def cmd_notes_search(query: str) -> None:
    """搜笔记 + 元素(标题 / 正文匹配),返回命中的元素链接 poof-note://note/block。"""
    _emit(_call("search", query=query))


@cmd_notes.command("show")
@click.option("--note", required=True)
@any_caller
def cmd_notes_show(note: str) -> None:
    """看某条笔记的所有元素(id / flavour / 文本 / 属性)。"""
    _emit(_call("show", note=note))


@cmd_notes.command("add")
@click.option("--note", required=True)
@click.option("--flavour", default="affine:paragraph", help="元素类型(模板)")
@click.option("--text", default="")
@click.option("--parent", default=None, help="父元素 id, 省略=笔记内容区")
@any_caller
def cmd_notes_add(note: str, flavour: str, text: str, parent: str | None) -> None:
    """新增一个元素。"""
    _emit(_call("add", note=note, flavour=flavour, text=text, parent=parent))


@cmd_notes.command("add-block")
@click.option("--note", required=True)
@click.option(
    "--kind",
    required=True,
    type=click.Choice(["md", "file", "plan", "progress", "review", "ai"]),
    help="md/file=文件路径(md→原生同步块, file→代码块原文); plan/progress/review=omni 实体; ai=AI块",
)
@click.option("--ref", default="", help="源标识: 文件路径 / plan_id / 进度 id / mat_id")
@click.option("--name", default=None, help="显示名(可选)")
@click.option("--text", default="", help="ai 块的初始文本(可选)")
@any_caller
def cmd_notes_add_block(
    note: str, kind: str, ref: str, name: str | None, text: str
) -> None:
    """把内容作为同步源块加进笔记(双向写回源 + 历史)。统一入口, 走 poof 的块注册表/插入总线。"""
    _emit(_call("add-block", note=note, kind=kind, ref=ref, name=name, text=text))


@cmd_notes.command("update")
@click.option("--note", required=True)
@click.option("--block", required=True)
@click.option("--text", default=None)
@click.option("--prop", "props", multiple=True, help="key=value, 可多个(改任意属性)")
@any_caller
def cmd_notes_update(note: str, block: str, text: str | None, props: tuple[str, ...]) -> None:
    """改某元素属性(--text 改文本,--prop key=value 改任意属性)。"""
    p: dict = {}
    if text is not None:
        p["text"] = text
    for kv in props:
        if "=" in kv:
            k, v = kv.split("=", 1)
            p[k] = v
    _emit(_call("update", note=note, block=block, props=p))


@cmd_notes.command("delete")
@click.option("--note", required=True)
@click.option("--block", required=True)
@any_caller
def cmd_notes_delete(note: str, block: str) -> None:
    """删某元素。"""
    _emit(_call("delete", note=note, block=block))


@cmd_notes.command("trash")
@click.option("--note", required=True)
@any_caller
def cmd_notes_trash(note: str) -> None:
    """整条笔记移入回收站(可在笔记库恢复)。"""
    _emit(_call("trash", note=note))


@cmd_notes.command("drop")
@click.option("--note", required=True)
@any_caller
def cmd_notes_drop(note: str) -> None:
    """彻底删除整条笔记(不可恢复)。"""
    _emit(_call("drop", note=note))


@cmd_notes.command("center")
@click.option("--note", required=True)
@click.option("--block", default=None)
@any_caller
def cmd_notes_center(note: str, block: str | None) -> None:
    """居中 / 定位到某笔记(或某元素)。需笔记面板开着。"""
    _emit(_call("center", note=note, block=block))


@cmd_notes.command("templates")
@any_caller
def cmd_notes_templates() -> None:
    """列可用的元素模板(flavour 们)。"""
    _emit(_call("templates"))


@cmd_notes.command("refresh")
@any_caller
def cmd_notes_refresh() -> None:
    """刷新(让 poof 重渲染当前笔记)。"""
    _emit(_call("refresh"))


# ── note 真源只读 + 提升成 plan (WORK-LIFECYCLE-AND-DISPATCH) ──
# 这两条**不走 notebridge**(不需 poof 在跑), 直接读 poof-notes 浅路径文件,
# note 唯一真源仍是 poof-notes, omni 只读不另存。

@cmd_notes.command("ls")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_notes_ls(as_json: bool) -> None:
    """列 poof-notes 浅路径里的笔记(只读, 不需 poof 在跑)。"""
    from omnicompany.packages.services._core.lifecycle.note_source import read_note_source
    src = read_note_source()
    if not src.available():
        raise click.ClickException(
            f"读不到 poof-notes 浅路径: {src.index_path} (poof 未落浅路径 / POOF_NOTES_DIR 未配)")
    notes = [n.to_dict() for n in src.list_notes()]
    if as_json:
        _emit(notes)
        return
    for n in notes:
        body = "有正文" if n["has_body"] else "无正文(.md未导出)"
        click.echo(f"  {n['id']}  {n['title']}  [{body}]  {n['anchor']}")


@cmd_notes.command("read")
@click.argument("note_id")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_notes_read(note_id: str, as_json: bool) -> None:
    """读一条 poof 笔记的标题+正文(只读浅路径, 不需 poof 在跑)。"""
    from omnicompany.packages.services._core.lifecycle.note_source import read_note_source
    n = read_note_source().get_note(note_id)
    if not n:
        raise click.ClickException(f"poof-notes 里没有 note: {note_id} (omni notes ls 看有哪些)")
    if as_json:
        _emit(n.to_dict(with_body=True))
        return
    click.echo(f"# {n.title}  ({n.anchor if hasattr(n,'anchor') else note_id})")
    click.echo(n.body() or "(无正文 .md 导出; 在 poof 打开该笔记或 omni notes refresh)")


@cmd_notes.command("promote")
@click.argument("note_id")
@click.option("--category", default="inbox", help="plan 落在 docs/plans/<category>/ 下")
@click.option("--model", default=None, help="调研用模型 (默认统一 agent 默认)")
@click.option("--dry", is_flag=True, help="只调研出骨架, 不落 plan.md")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_notes_promote(note_id: str, category: str, model: str | None,
                      dry: bool, as_json: bool) -> None:
    """把一条 poof note 调研澄清成可执行 plan 草稿 (走统一 agent)。

    产出 docs/plans/<category>/[date]NAME/plan.md(+brief.md); 歧义标 NEEDS CLARIFICATION
    并入 omni human inbox; 草稿要过 omni plan gate 才能 split/dispatch。
    """
    from omnicompany.packages.services._core.lifecycle.note_to_plan import promote_note_to_plan
    res = promote_note_to_plan(note_id, category=category, model=model, dry=dry)
    if as_json:
        _emit(res)
        if not res.get("ok"):
            raise SystemExit(1)
        return
    if not res.get("ok"):
        raise click.ClickException(res.get("error", "promote 失败"))
    if dry:
        click.echo(f"[dry] 将产出 plan: {res['plan_id']}")
        click.echo(f"  待澄清: {len(res.get('clarifications', []))} 条")
        return
    click.echo(f"✓ 生成 plan 草稿: {res['plan_id']}")
    click.echo(f"  路径: {res['path']}")
    if res.get("clarifications"):
        click.echo(f"  ⚠ {len(res['clarifications'])} 条待澄清 (已入 inbox {res.get('clarifications_pushed_to_inbox',0)} 条):")
        for c in res["clarifications"]:
            click.echo(f"    - {c}")
    click.echo(f"  下一步: {res.get('next')}")


# ── 消费任务(批3): 笔记 → material 入册, 逐条水位线增量(不改真源) ──

def _notes_materialize_state_path():
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root() / "data" / "lifecycle" / "notes_materialize_state.json"


@cmd_notes.command("materialize")
@external_or_controller
@click.option("--model", default=None, help="覆盖默认性价比模型")
@click.option("--inbox", is_flag=True, help="置信不足/越界标签推 human-inbox")
def cmd_notes_materialize(model: str | None, inbox: bool) -> None:
    """消费任务: poof-notes 逐条水位线增量入册(标题分叉修正+双时间+受控标签), 真源零改动。

    手动 = 直接跑此 verb; 定期 = cron 任务 gov-notes-materialize 每小时调同函数
    (index.json 无变更时零 LLM 调用)。
    """
    from omnicompany.packages.services._core.lifecycle.note_source import overlay_note_store_dir
    from omnicompany.packages.services._core.lifecycle.notes_materialize import run_notes_materialize
    report = run_notes_materialize(
        overlay_note_store_dir(), state_path=_notes_materialize_state_path(),
        model=model, push_inbox=inbox,
    )
    _emit(report)


@cmd_notes.command("quarantine-orphans")
@external_or_controller
@click.option("--apply", "do_apply", is_flag=True, help="真移动(默认 dry-run 只出清单)")
def cmd_notes_quarantine_orphans(do_apply: bool) -> None:
    """孤儿 ydoc(index.json 未收录)回收: 只移动进 poof-notes/_trash/, collection 根绝不动。

    默认 dry-run 只出清单; --apply 才真移动(可回搬, poof-notes 是独立 git 仓)。
    """
    from omnicompany.packages.services._core.lifecycle.note_source import overlay_note_store_dir
    from omnicompany.packages.services._core.lifecycle.notes_materialize import quarantine_orphan_ydocs
    report = quarantine_orphan_ydocs(overlay_note_store_dir(), dry_run=not do_apply)
    _emit(report)


__all__ = ["cmd_notes"]
