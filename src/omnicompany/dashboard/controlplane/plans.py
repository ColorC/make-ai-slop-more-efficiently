# [OMNI] origin=claude-code ts=2026-05-01 type=infra
# [OMNI] material_id="material:dashboard.plans_catalogue.scanner_api.py"
"""Plans catalogue — scans `docs/plans/[date]TOPIC/`.

Replaces the old SQLite assistant_db Goal/Plan tables (deprecated 2026-05-01).
Each plan = a folder under docs/plans/. Contains plan.md (canonical) + accessories.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from omnicompany.core.plans_catalogue import (
    DATE_RE,
    _plans_root,
    _project_root,
    _scan,
    _scan_cached,
    parse_plan_frontmatter,
)

plans_router = APIRouter()


@plans_router.get("/plans")
def list_plans() -> dict[str, Any]:
    items = _scan()
    # 治理部门(plan_steward)的中文标题浮出 — 用户 2026-06-12: 计划要汉化
    try:
        from omnicompany.core.projects_registry import plan_governance
        gov = plan_governance()
        if gov:
            items = [{**it, "title_zh": (gov.get(it["id"]) or {}).get("title_zh") or None}
                     for it in items]
    except Exception:  # noqa: BLE001 — 治理表损坏不拖垮计划列表
        pass
    return {"items": items, "total": len(items)}


class CreatePlanBody(BaseModel):
    topic: str = Field(..., min_length=1, max_length=120)       # 计划主题(进目录名)
    project_id: str | None = None                               # 归属项目(推断类目目录)
    category: str | None = None                                 # 显式类目, 覆盖项目推断
    title: str | None = None
    content: str = ""                                           # 草稿正文(纯文本)
    work_type: str = "planning"
    formalize: bool = False                                     # True=先用性价比模型整理成规范计划书


def _project_category(project_id: str | None) -> str | None:
    """项目的首个 plan_categories 类目 —— 新计划放它下面即可被项目"计划"区前缀匹配到。"""
    if not project_id:
        return None
    try:
        from omnicompany.core.projects_registry import list_projects
        for p in list_projects():
            if p.get("id") == project_id:
                cats = p.get("plan_categories") or []
                return (cats[0] if cats else None)
    except Exception:  # noqa: BLE001
        return None
    return None


def _formalize_plan(title: str, raw: str) -> str | None:
    """性价比模型把纯文本草稿整理成规范《计划书》markdown(用户: 我写纯文本, 格式化交给 AI)。失败返回 None。"""
    try:
        from omnicompany.runtime.llm.structured import call_json
        schema = {"type": "object",
                  "properties": {"plan_markdown": {"type": "string"}},
                  "required": ["plan_markdown"]}
        sys = ("你是把用户随手写的纯文本计划草稿整理成规范《计划书》Markdown 的助手。"
               "补全结构(背景/目标/方案/步骤/验收/风险), 保留用户原意与原始信息, 不臆造事实。只输出 markdown 正文。")
        d = call_json(system=sys, user=f"标题: {title}\n\n草稿正文:\n{raw}", schema=schema,
                      caller="plans.create.formalize", max_tokens=4000)
        md = (d or {}).get("plan_markdown")
        return md.strip() if isinstance(md, str) and md.strip() else None
    except Exception:  # noqa: BLE001
        return None


_TOPIC_BAD = re.compile(r'[\\/:*?"<>|\[\]]+')


@plans_router.post("/plans")
def create_plan(body: CreatePlanBody) -> dict[str, Any]:
    """在项目的计划类目目录下一键新建一篇计划书: docs/plans/<category>/[日期]主题/plan.md。

    撰写态草稿(纯文本)由前端送来; formalize=True 时先经性价比模型整理成规范计划书再落盘。
    归属由服务端既有 resolve_project_plans 判定(放在项目 plan_categories 下即前缀匹配到)。"""
    topic = (_TOPIC_BAD.sub("-", body.topic.strip()).strip("-")[:80]) or "NEW-PLAN"
    category = (body.category or _project_category(body.project_id) or "_infra").strip("/")
    pr = _plans_root()
    target_parent = (pr / category).resolve()
    try:
        target_parent.relative_to(pr.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid category")
    date = datetime.now().strftime("%Y-%m-%d")
    base = f"[{date}]{topic}"
    folder = target_parent / base
    if folder.exists():
        i = 2
        while (target_parent / f"{base}-{i}").exists():
            i += 1
        folder = target_parent / f"{base}-{i}"
    title = (body.title or body.topic).strip()
    body_md = body.content or ""
    if body.formalize and body_md.strip():
        body_md = _formalize_plan(title, body_md) or body_md
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fm = ["---", f"title: {title}", f"date: '{date}'"]
        if body.project_id:
            fm.append(f"project: {body.project_id}")
        fm += [f"work_type: {body.work_type}", "status: active", "exit_criteria: []",
               "binding:", "  workspace: .", "---", "", body_md.rstrip(), ""]
        (folder / "plan.md").write_text("\n".join(fm), encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"写计划失败: {e}") from e
    try:
        _scan_cached.cache_clear()  # 让 list/项目计划区立刻看到新计划
    except Exception:  # noqa: BLE001
        pass
    rel = str((folder / "plan.md").parent.relative_to(pr)).replace(os.sep, "/")
    return {"ok": True, "plan_id": rel, "abs_path": str((folder / "plan.md").resolve()),
            "category": category, "formalized": bool(body.formalize and body_md)}


def _doc_summary(path: Path, limit: int = 160) -> str:
    """抽取文档开头正文(去 frontmatter/标题/表格/代码/引用/列表符), 攒够约两行的量。
    用户 2026-06-06: 计划页每个文档一句简述; 不要在回车处截断, 显示 2 整行即可(前端 clamp 2 行)。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if text.startswith("---"):  # 跳过 YAML frontmatter
        end = text.find("\n---", 3)
        if end >= 0:
            nl = text.find("\n", end + 1)
            text = text[nl + 1:] if nl >= 0 else ""
    buf: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue  # 空行不停(跨段继续攒), 凑够两行
        # 标题/引用/表格/代码栅栏/分隔线/列表项/HTML = 段落/区块结束: 已有正文则停, 否则跳过继续找
        if line[0] in "#>|" or line.startswith("```") or line.startswith("---") \
                or line.startswith("- ") or line.startswith("* ") or line.startswith("<") \
                or re.match(r"^\d+\.\s", line):
            if buf:
                break
            continue
        buf.append(line)
        if len(" ".join(buf)) >= limit:
            break
    s = re.sub(r"[*`_\[\]]", "", " ".join(buf)).strip()
    if len(s) > limit:
        s = s[:limit].rstrip("，,、;；。 ") + "…"
    return s


@plans_router.get("/plans/{plan_id:path}")
def get_plan(plan_id: str) -> dict[str, Any]:
    pr = _plans_root()
    folder = pr / plan_id
    try:
        folder.resolve().relative_to(pr.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid plan id")
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"plan not found: {plan_id}")
    files = []
    for f in sorted(folder.rglob("*")):
        if f.is_file() and not f.name.startswith("."):
            rel = str(f.relative_to(folder)).replace(os.sep, "/")
            try:
                stat = f.stat()
                files.append({
                    "path": rel,
                    "is_md": f.suffix == ".md",
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "note_id_if_md": f"plans/{plan_id}/{rel[:-3]}" if f.suffix == ".md" else None,
                    "summary": _doc_summary(f) if f.suffix == ".md" else "",
                })
            except OSError:
                continue
    m = DATE_RE.match(plan_id.replace("_archive/", ""))
    meta = parse_plan_frontmatter(folder / "plan.md")
    return {
        "id": plan_id,
        "topic": m.group(2) if m else plan_id,
        "date": m.group(1) if m else None,
        "folder_path": str(folder.relative_to(_project_root())).replace(os.sep, "/"),
        "files": files,
        "archived": plan_id.startswith("_archive/"),
        "meta": meta,
    }
