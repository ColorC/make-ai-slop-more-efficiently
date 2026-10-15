# [OMNI] origin=claude-code ts=2026-06-22 type=cli
# [OMNI] material_id="material:cli.commands.notes.py"
"""omni notes — 操控 Canvas 的旧 BlockSuite 札记(经文件命令队列桥)。需札记画布打开。

给 codex 总控用: 任意 增/删/改 笔记元素属性、搜索笔记/元素、列模板、居中定位、刷新。
桥: CLI 写 `%LOCALAPPDATA%/overlay-shell/notes-bridge/req-<id>.json`, overlay-shell 前端轮询执行(在活的
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
    # Must match overlay-shell/src-tauri/src/notebridge.rs. The desktop app's
    # Legacy compatibility note: the retired product used a different runtime directory;
    # ``overlay-shell``; leaving the CLI on the legacy directory makes every
    # request time out because the running app never sees it.
    d = os.path.join(base, "overlay-shell", "notes-bridge")
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
    os.replace(tmp, req)  # 原子写，札记画布不会读到半截
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
    raise click.ClickException("札记画布没响应(需打开 Canvas；命令在活动札记 collection 上执行)")


@click.group("notes")
def cmd_notes() -> None:
    """操控 Canvas 札记(增删改元素 / 搜索 / 模板 / 居中 / 刷新，经桥，需札记画布打开)。"""


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
    """搜札记与元素(标题 / 正文匹配)，返回 omni://note/<id> 引用。"""
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
    type=click.Choice(["md", "file", "image", "plan", "progress", "review", "ai"]),
    help="md/file=文件路径(md→原生同步块, file→代码块原文); image=图片路径(可带 --xywh 摆上画布); plan/progress/review=omni 实体; ai=AI块",
)
@click.option("--ref", default="", help="源标识: 文件路径 / plan_id / 进度 id / mat_id")
@click.option("--name", default=None, help="显示名(可选)")
@click.option("--text", default="", help="ai 块的初始文本(可选)")
@click.option("--xywh", default=None, help='image 专用: 画布坐标 "[x,y,w,h]", 给了就摆上 surface 画布')
@any_caller
def cmd_notes_add_block(
    note: str, kind: str, ref: str, name: str | None, text: str, xywh: str | None
) -> None:
    """把内容作为同步源块加进札记(双向写回源 + 历史)，走 Canvas 块注册表/插入总线。"""
    _emit(_call("add-block", note=note, kind=kind, ref=ref, name=name, text=text, xywh=xywh))


@cmd_notes.command("add-element")
@click.option("--note", required=True)
@click.option(
    "--etype",
    required=True,
    type=click.Choice(["connector", "shape", "frame", "text"]),
    help="connector=连线(需 --from/--to); shape=形状 / frame=分区 / text=文本(需 --xywh)",
)
@click.option("--from", "from_", default=None, help="connector 起点块 id")
@click.option("--to", default=None, help="connector 终点块 id")
@click.option("--xywh", default=None, help='画布坐标 "[x,y,w,h]"(shape/frame/text 必填)')
@click.option("--text", default=None, help="文字内容(connector 标签 / shape / text)")
@click.option("--name", default=None, help="frame 标题")
@any_caller
def cmd_notes_add_element(
    note: str, etype: str, from_: str | None, to: str | None,
    xywh: str | None, text: str | None, name: str | None,
) -> None:
    """在笔记画布(surface)上创建连线/形状/分区/文本元素。"""
    _emit(_call("add-element", note=note, etype=etype, to=to, xywh=xywh,
                text=text, name=name, **{"from": from_}))


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
    """刷新当前札记。"""
    _emit(_call("refresh"))


# ── note 真源只读 + 提升成 plan (WORK-LIFECYCLE-AND-DISPATCH) ──
# 这两条**不走 notebridge**(不需 Canvas 打开)，直接读 overlay-note-store 浅路径文件；
# 札记唯一真源仍是 overlay-note-store，omni 只读不另存。

@cmd_notes.command("ls")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_notes_ls(as_json: bool) -> None:
    """列 overlay-note-store 里的 Material 札记(只读，不需 Canvas 打开)。"""
    from omnicompany.packages.services._core.lifecycle.note_source import read_note_source
    src = read_note_source()
    if not src.available():
        raise click.ClickException(
            f"读不到 overlay-note-store: {src.index_path} "
            "(札记画布尚未落盘 / OVERLAY_NOTE_STORE_DIR 未配；旧环境变量仍兼容)")
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
    """读一条 Material 札记的标题与正文(只读浅路径，不需 Canvas 打开)。"""
    from omnicompany.packages.services._core.lifecycle.note_source import read_note_source
    n = read_note_source().get_note(note_id)
    if not n:
        raise click.ClickException(f"overlay-note-store 里没有札记: {note_id} (用 omni notes ls 查看)")
    if as_json:
        _emit(n.to_dict(with_body=True))
        return
    click.echo(f"# {n.title}  ({n.anchor if hasattr(n,'anchor') else note_id})")
    click.echo(n.body() or "(无正文 .md 导出；在 Canvas 打开该札记或执行 omni notes refresh)")


@cmd_notes.command("promote")
@click.argument("note_id")
@click.option("--category", default="inbox", help="plan 落在 docs/plans/<category>/ 下")
@click.option("--model", default=None, help="调研用模型 (默认统一 agent 默认)")
@click.option("--dry", is_flag=True, help="只调研出骨架, 不落 plan.md")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_notes_promote(note_id: str, category: str, model: str | None,
                      dry: bool, as_json: bool) -> None:
    """把一条 Material 札记调研澄清成可执行 plan 草稿(走统一 agent)。

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
@click.option("--inbox", is_flag=True, help="置信不足/越界标签推 HumanBus 运行时人工复核队列")
def cmd_notes_materialize(model: str | None, inbox: bool) -> None:
    """消费任务: Material 札记逐条水位线增量入册(标题修正+双时间+受控标签)，真源零改动。

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
    """孤儿 ydoc(index.json 未收录)回收: 只移入 overlay-note-store/_trash，collection 根绝不动。

    默认 dry-run 只出清单；--apply 才真移动(可回搬，札记存储与 omni 仓分离)。
    """
    from omnicompany.packages.services._core.lifecycle.note_source import overlay_note_store_dir
    from omnicompany.packages.services._core.lifecycle.notes_materialize import quarantine_orphan_ydocs
    report = quarantine_orphan_ydocs(overlay_note_store_dir(), dry_run=not do_apply)
    _emit(report)


__all__ = ["cmd_notes"]
