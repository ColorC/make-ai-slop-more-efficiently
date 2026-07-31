# [OMNI] origin=ai-ide ts=2026-06-12 type=cli
# [OMNI] material_id="material:cli.project.registry_verbs.py"
"""omni project — 项目注册表(驾驶舱首页项目工作板的数据源)。用户 + 总控 AI 共用。

项目是用户和总控的共同入口(用户原话 2026-06-12): 总控需要知道有什么项目才能决定
激活/了解什么。每个项目绑定一个 index 文件(PROJECT_INDEX.md, 强结构化 README,
注册 roots/快速工作选项(skill)/links/最新进展指针), 卡片一键复制的就是它的路径。

约定(重要): index 文件是本机 omnicompany 内部档(含硬编码本机路径), 只能住在
omnicompany 内部, 绝不放进会被上传分享的外部目录(如共享同步盘)——
否则同步到队友电脑里那些路径全是坏的。外部根目录的项目(代码在工作区之外),
index 统一放 <project-root>/docs/projects/<id>/PROJECT_INDEX.md; root 仍指向真实代码位置。

例:
  omni project register my-project --name "我的项目" --group other \\
      --root "<workspace-root>" \\
      --index "<project-root>/docs/projects/my-project/PROJECT_INDEX.md" \\
      --desc "一句话说明"
  omni project list --json          # 总控读这个了解全部项目
  omni project show my-project      # 含 index 浮出的 quick_actions
  omni project index-check --all    # 校验所有 index 文件结构
  omni project index-init my-project --path "<project-root>/docs/projects/my-project/PROJECT_INDEX.md"
"""
from __future__ import annotations

import json
from pathlib import Path

import click

from .._access import any_caller, external_or_controller

_INDEX_TEMPLATE = """---
omni_project: {pid}
name: {name}
group: {group}
updated: {today}          # 最后核对此 index 与现实一致的日期; 改完内容务必同步(否则系统判 index_stale)
roots:
  - path: {root}
    note: 主目录
entry_points: []          # 主要子目录: [{{path, note}}]
threads: []               # 工作线(项目状态的真源, 别在正文手写"活跃"): [{{name, status, updated, note}}]
                          #   status ∈ active|done|blocked|parked; updated=该线状态最后核对日(YYYY-MM-DD)
latest: []                # 最新进展指针(保持实时!): ["YYYY-MM-DD 一句话 + 文档路径"]
quick_actions: []         # 常用工作选项: [{{label, skill, where, desc}}] skill 可在外部仓但必须在此注册
links: []                 # [{{label, url}}]
---
# {name}

## 概况

(本项目是什么 / 解决什么。)

## 当前进展

(各工作线现状, 与 frontmatter.threads 一致 + 指向权威进度文档。
 别写死"活跃/进行中"——是否活跃由系统按真实文件/git 改动算, 这里只写每条线在做什么、卡在哪。)

## 主要目录

(哪些目录干什么, 与 frontmatter.roots/entry_points 一致。)

## 能做什么

(本项目的能力清单。)

## 常见展开方式

(接到相关需求时, 通常从哪里开始: 读什么文档 / 用什么 skill / 跑什么命令。)
"""


@click.group("project")
def cmd_project() -> None:
    """项目注册表 (register/list/show/remove/index-check/index-init)。"""


@cmd_project.command("register")
@click.argument("project_id")
@click.option("--name", default=None, help="显示名")
@click.option("--group", default=None, help="主分组(omnicompany/indie-game/other 或自定义)")
@click.option("--tag", "tags", multiple=True, help="标签(可多个)")
@click.option("--desc", default=None, help="一句话说明")
@click.option("--root", "roots", multiple=True, help="项目根目录(可多个)")
@click.option("--index", "index_path", default=None, help="index 文件(PROJECT_INDEX.md)绝对路径")
@click.option("--bg", default=None, help="卡片背景图 url(如 /api/project-assets/xxx.png)或 CSS 渐变")
@click.option("--icon", default=None, help="卡片小图标(lucide 图标名, kebab-case, 如 shield-check / book-open)")
@click.option("--plan-cat", "plan_categories", multiple=True, help="关联 docs/plans 下的类目(可多个)")
@click.option("--team-id", "team_ids", multiple=True,
              help="关联的 TeamSpec.id（可多个；Project registry 单向持有）")
@click.option("--primary-team-id", default=None,
              help="项目默认 TeamSpec.id（会自动加入 --team-id 列表）")
@click.option("--pin", is_flag=True, default=None, help="置顶")
@external_or_controller
def cmd_project_register(project_id: str, name: str | None, group: str | None, tags: tuple[str, ...],
                         desc: str | None, roots: tuple[str, ...], index_path: str | None,
                         bg: str | None, icon: str | None, plan_categories: tuple[str, ...],
                         team_ids: tuple[str, ...], primary_team_id: str | None,
                         pin: bool | None) -> None:
    """注册/更新一个项目(不传的字段保留原值)。"""
    from omnicompany.core.projects_registry import set_project
    from .._access import current_caller

    fields = {
        "name": name,
        "group": group,
        "tags": list(tags) or None,
        "desc": desc,
        "roots": list(roots) or None,
        "index_path": index_path,
        "bg": bg,
        "icon": icon,
        "plan_categories": list(plan_categories) or None,
        "team_ids": list(team_ids) or None,
        "primary_team_id": primary_team_id,
        "pinned": pin,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    by = "controller" if current_caller() == "controller" else "human"
    item = set_project(project_id, by=by, **fields)
    click.echo(json.dumps({"ok": True, "project": item}, ensure_ascii=False, indent=2))


@cmd_project.command("list")
@click.option("--group", default=None, help="只看某分组")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON(总控用)")
@any_caller
def cmd_project_list(group: str | None, as_json: bool) -> None:
    """项目工作板全量(含最后活跃/快速选项), 总控了解项目全貌的入口。"""
    from omnicompany.core.projects_registry import enrich_projects

    data = enrich_projects()
    projects = data["projects"]
    if group:
        projects = [p for p in projects if p.get("group") == group]
    if as_json:
        click.echo(json.dumps({**data, "projects": projects}, ensure_ascii=False, indent=2))
        return
    if not projects:
        click.echo("(无注册项目; 用 omni project register 注册)")
        return
    for g in data["groups_order"]:
        rows = [p for p in projects if p.get("group") == g]
        if not rows:
            continue
        click.echo(f"== {data['group_labels'].get(g, g)} ==")
        for p in rows:
            la = (p.get("last_active") or "")[:16].replace("T", " ")
            qa = len(p.get("quick_actions") or [])
            idx = "✓" if p.get("index_ok") else ("✗" if p.get("index_ok") is False else "-")
            thr = p.get("threads") or []
            tcol = f"  线:{len(thr)}" if thr else ""
            stale = "  ⚠状态可能过期(index 久未核对)" if p.get("index_stale") else ""
            click.echo(f"  {p['id']:<22} {p.get('name','')}  活跃:{la}  plans:{p.get('plan_count',0)}  动作:{qa}{tcol}  index:{idx}{stale}")


@cmd_project.command("show")
@click.argument("project_id")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_project_show(project_id: str, as_json: bool) -> None:
    """单项目详情(注册字段 + index 浮出的 quick_actions/latest)。"""
    from omnicompany.core.projects_registry import enrich_projects

    p = next((x for x in enrich_projects()["projects"] if x.get("id") == project_id), None)
    if p is None:
        click.echo(json.dumps({"ok": False, "error": f"未注册: {project_id}"}, ensure_ascii=False))
        raise SystemExit(1)
    click.echo(json.dumps(p, ensure_ascii=False, indent=2))


@cmd_project.command("remove")
@click.argument("project_id")
@external_or_controller
def cmd_project_remove(project_id: str) -> None:
    from omnicompany.core.projects_registry import remove_project

    click.echo(json.dumps({"ok": remove_project(project_id)}, ensure_ascii=False))


@cmd_project.command("index-check")
@click.argument("project_id", required=False)
@click.option("--all", "check_all", is_flag=True, help="校验全部已注册项目的 index 文件")
@any_caller
def cmd_project_index_check(project_id: str | None, check_all: bool) -> None:
    """校验 index 文件: 结构(frontmatter 必填键 + threads)+ 状态可靠性(是否久未核对)。"""
    from omnicompany.core.projects_registry import enrich_projects, list_projects, parse_index_file

    targets = list_projects()
    if not check_all:
        targets = [p for p in targets if p.get("id") == project_id]
        if not targets:
            click.echo(json.dumps({"ok": False, "error": f"未注册: {project_id}"}, ensure_ascii=False))
            raise SystemExit(1)
    enriched = {x.get("id"): x for x in enrich_projects()["projects"]}
    results = []
    for p in targets:
        ip = p.get("index_path")
        r = parse_index_file(ip) if ip else {"ok": False, "error": "未配置 index_path"}
        e = enriched.get(p["id"], {})
        results.append({
            "id": p["id"], "index_path": ip, "ok": r.get("ok"), "error": r.get("error"),
            "warnings": r.get("warnings") or [],
            "stale": bool(e.get("index_stale")),
            "stale_reason": e.get("stale_reason"),
        })
    bad = [r for r in results if not r["ok"]]
    stale = [r for r in results if r["stale"]]
    click.echo(json.dumps(
        {"ok": not bad, "checked": len(results), "stale": len(stale), "results": results},
        ensure_ascii=False, indent=2))
    if bad:
        raise SystemExit(1)


@cmd_project.command("index-init")
@click.argument("project_id")
@click.option("--path", "index_path", required=True, help="要创建的 PROJECT_INDEX.md 绝对路径")
@click.option("--force", is_flag=True, help="已存在也覆盖")
@external_or_controller
def cmd_project_index_init(project_id: str, index_path: str, force: bool) -> None:
    """按模板创建 index 文件并绑定到项目注册表。"""
    from datetime import date

    from omnicompany.core.projects_registry import list_projects, set_project
    from .._access import current_caller

    proj = next((p for p in list_projects() if p.get("id") == project_id), None)
    if proj is None:
        click.echo(json.dumps({"ok": False, "error": f"先 register 项目: {project_id}"}, ensure_ascii=False))
        raise SystemExit(1)
    p = Path(index_path)
    if p.exists() and not force:
        click.echo(json.dumps({"ok": False, "error": f"已存在(用 --force 覆盖): {p}"}, ensure_ascii=False))
        raise SystemExit(1)
    p.parent.mkdir(parents=True, exist_ok=True)
    root = (proj.get("roots") or [str(p.parent)])[0]
    p.write_text(_INDEX_TEMPLATE.format(
        pid=project_id, name=proj.get("name", project_id), group=proj.get("group", "other"),
        today=date.today().isoformat(), root=root,
    ), encoding="utf-8")
    by = "controller" if current_caller() == "controller" else "human"
    set_project(project_id, by=by, index_path=str(p))
    click.echo(json.dumps({"ok": True, "created": str(p)}, ensure_ascii=False))
