#!/usr/bin/env python3
"""progress CLI — OmniCompany progress service (:8230) 的命令行入口。

只依赖标准库(urllib)，零安装、零依赖——AI / 脚本可直接 shell 接入。
和 omnicompany 里的进度 feeder 走同一套 HTTP API。

用法:
  progress board                      看板概览(域→任务线→计划数)
  progress pins                       列出置顶
  progress pin   <id> [--goal]        置顶 具体任务(默认) / 任务线(--goal)
  progress unpin <id> [--goal]        取消置顶
  progress tasks [--channel local] [--search 关键字] [--limit 30]
  progress add   "标题" [--goal-id G] [--line main|side]
  progress progress <task_id> "进度文本"
  progress patch <task_id> [--completion 80] [--status in_progress]
  progress done  <task_id>            等价 patch --completion 100 --status done
  progress archive <task_id> [--unarchive]
  progress sync [meego|multica|all]   拉外部渠道
  任意子命令加 --json 输出原始 JSON。

服务地址默认 http://127.0.0.1:8230，可用环境变量 PROGRESS_SERVICE_URL 覆盖；WHATNOW_URL 兼容旧脚本。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("PROGRESS_SERVICE_URL") or os.environ.get("WHATNOW_URL", "http://127.0.0.1:8230")
BASE = BASE.rstrip("/")


def _req(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if raw else {}
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        sys.stderr.write(
            f"[progress] 连不上服务 {BASE}（{reason}）。\n"
            f"          先启动：E:\\WindowsWorkspace\\omnicompany\\services\\_progress\\progress_service\\start-progress-service.cmd\n"
        )
        sys.exit(2)


def get(path: str) -> dict:
    return _req("GET", path)


def post(path: str, body: dict) -> dict:
    return _req("POST", path, body)


def _out_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def _line_badge(line: str) -> str:
    return "主线" if line != "side" else "支线"


# ── 子命令 ───────────────────────────────────────────────────────────────────

def cmd_board(a) -> None:
    b = get("/api/board")
    if a.json:
        return _out_json(b)
    c = b.get("counts", {})
    print(f"progress-service @ {BASE}  ·  域 {c.get('clusters',0)} / 任务线 {c.get('goals',0)} / 计划 {c.get('tasks',0)}")
    pins = b.get("pins", [])
    if pins:
        print(f"\n📌 置顶 {len(pins)}")
        for p in pins:
            if p.get("missing"):
                print(f"   ! [{p['subject_kind']}] {p['subject_id']} (已失效)")
            elif p["subject_kind"] == "goal":
                print(f"   📌 [{_line_badge(p.get('line',''))}] {p.get('title','')}  {p.get('done_count',0)}/{p.get('task_count',0)}  {p.get('completion',0)}%")
            else:
                print(f"   📌 {p.get('title','')}  {p.get('completion',0)}%  [{p.get('channel','')}]  ({p['subject_id']})")
    for cl in b.get("clusters", []):
        goals = cl.get("goals", [])
        if not goals:
            continue
        print(f"\n# {cl.get('title','')}  {cl.get('note','')}".rstrip())
        for g in goals:
            tasks = g.get("tasks", [])
            done = sum(1 for t in tasks if _is_done(t.get("status", "")))
            pct = round(sum(t.get("completion", 0) for t in tasks) / len(tasks)) if tasks else 0
            print(f"  [{_line_badge(g.get('line',''))}] {g.get('title','')}  {done}/{len(tasks)}  {pct}%  ({g['id']})")
    inbox = b.get("loose_tasks", [])
    if inbox:
        print(f"\n📥 外部收件箱 {len(inbox)} 条（前 10）")
        for t in inbox[:10]:
            due = f"  排期 {t['due_date']}" if t.get("due_date") else ""
            print(f"   - {t.get('title','')[:50]}  [{t.get('channel','')}]{due}  ({t['id']})")


def _is_done(s: str) -> bool:
    s = (s or "").lower()
    return any(k in s for k in ("done", "完成", "关闭", "取消", "解决", "closed", "resolved", "cancel"))


def cmd_pins(a) -> None:
    b = get("/api/board")
    pins = b.get("pins", [])
    if a.json:
        return _out_json(pins)
    if not pins:
        print("（无置顶）")
        return
    for p in pins:
        if p.get("missing"):
            print(f"! [{p['subject_kind']}] {p['subject_id']} (已失效，建议 unpin)")
        elif p["subject_kind"] == "goal":
            print(f"[任务线·{_line_badge(p.get('line',''))}] {p.get('title','')}  {p.get('done_count',0)}/{p.get('task_count',0)}  {p.get('completion',0)}%  id={p['subject_id']}")
        else:
            print(f"[任务] {p.get('title','')}  {p.get('completion',0)}%  [{p.get('channel','')}]  id={p['subject_id']}")


def _all_tasks(b: dict) -> list[dict]:
    out = []
    for cl in b.get("clusters", []):
        for g in cl.get("goals", []):
            out.extend(g.get("tasks", []))
    out.extend(b.get("loose_tasks", []))
    return out


def _all_goals(b: dict) -> list[dict]:
    out = []
    for cl in b.get("clusters", []):
        out.extend(cl.get("goals", []))
    return out


def _subject_exists(kind: str, subject_id: str, b: dict | None = None) -> bool:
    b = b if b is not None else get("/api/board")
    ids = {g["id"] for g in _all_goals(b)} if kind == "goal" else {t["id"] for t in _all_tasks(b)}
    return subject_id in ids


def _existing_pin_note(kind: str, subject_id: str, b: dict) -> str | None:
    """在 /api/board 顶层 pins 里找该主体现有置顶记录的 note；未置顶则 None。"""
    for p in b.get("pins", []):
        if p.get("subject_kind") == kind and p.get("subject_id") == subject_id:
            return p.get("note", "")
    return None


def cmd_pin(a) -> None:
    kind = "goal" if a.goal else "task"
    b = get("/api/board")  # 一次查询，既做存在性校验又顺手查现有置顶备注
    if not _subject_exists(kind, a.id, b):
        label = "任务线" if kind == "goal" else "任务"
        msg = f"[progress] 找不到{label} id={a.id}（不存在，拒绝置顶；用 `progress board` 或 `progress tasks` 核对 id）"
        if a.json:
            _out_json({"ok": False, "error": msg})
            sys.exit(1)
        sys.stderr.write(msg + "\n")
        sys.exit(1)
    # 未显式给 --note 时，若该主体已置顶，沿用旧备注，不用空串覆盖抹掉。
    # 用户显式给了 --note（哪怕是显式空串 --note ""）才覆盖。
    if a.note is None:
        note = _existing_pin_note(kind, a.id, b) or ""
    else:
        note = a.note
    r = post("/api/pin", {"subject_kind": kind, "subject_id": a.id, "note": note, "pinned": True})
    if a.json:
        return _out_json(r)
    print(f"已置顶 [{kind}] {a.id}" if r.get("ok") else f"失败: {r}")


def cmd_unpin(a) -> None:
    kind = "goal" if a.goal else "task"
    r = post("/api/pin", {"subject_kind": kind, "subject_id": a.id, "pinned": False})
    if a.json:
        return _out_json(r)
    print(f"已取消置顶 [{kind}] {a.id}" if r.get("ok") else f"失败: {r}")


def cmd_tasks(a) -> None:
    b = get("/api/board")
    tasks = _all_tasks(b)
    if a.channel:
        tasks = [t for t in tasks if t.get("channel") == a.channel]
    if a.search:
        q = a.search.lower()
        tasks = [t for t in tasks if q in (t.get("title", "").lower())]
    tasks = tasks[: a.limit]
    if a.json:
        return _out_json(tasks)
    if not tasks:
        print("（无匹配任务）")
        return
    for t in tasks:
        print(f"{t.get('completion',0):>3}%  {t.get('title','')[:60]}  [{t.get('channel','')}]  ({t['id']})")


def cmd_add(a) -> None:
    # id 留空 → 服务端 upsert_task 自动分配 t<seq>（Task.id 无 serde default，必须带上空串否则 422）。
    body = {"id": "", "title": a.title, "line": a.line, "channel": a.channel, "status": "todo", "completion": 0}
    if a.goal_id:
        body["goal_id"] = a.goal_id
    if a.ref:
        body["external_refs"] = list(a.ref)  # 例: feishu:<wiki-token> / meego:<id>，把需求文档原链接挂上
    if a.due:
        body["due_date"] = a.due
    if a.assignee:
        body["assignee"] = a.assignee
    r = post("/api/tasks", body)
    if a.json:
        return _out_json(r)
    print(f"已新建任务 {r.get('id','?')}: {a.title}" if r.get("ok") else f"失败: {r}")


def cmd_progress(a) -> None:
    text = " ".join(a.text)
    r = post("/api/progress", {"subject_kind": "task", "subject_id": a.task_id, "text": text, "source": "cli"})
    if a.json:
        return _out_json(r)
    print(f"已记进度 → {a.task_id}: {text}" if r.get("ok") else f"失败: {r}")


def cmd_patch(a) -> None:
    body: dict = {"id": a.task_id}
    if a.completion is not None:
        body["completion"] = a.completion
    if a.status:
        body["status"] = a.status
    r = post("/api/task/patch", body)
    if a.json:
        return _out_json(r)
    print(f"已更新 {a.task_id}" if r.get("ok") else f"失败(任务不存在?): {r}")


def cmd_done(a) -> None:
    r = post("/api/task/patch", {"id": a.task_id, "completion": 100, "status": "done"})
    if a.json:
        return _out_json(r)
    print(f"已标记完成 {a.task_id}" if r.get("ok") else f"失败: {r}")


def cmd_archive(a) -> None:
    r = post("/api/task/archive", {"id": a.task_id, "archived": not a.unarchive})
    if a.json:
        return _out_json(r)
    verb = "取消归档" if a.unarchive else "归档"
    print(f"已{verb} {a.task_id}" if r.get("ok") else f"失败: {r}")


def cmd_auto_archive(a) -> None:
    r = post("/api/maintenance/auto-archive", {})
    if a.json:
        return _out_json(r)
    print(f"已归档 {r.get('archived', 0)} 条（已完成 / 进度≥90% 的计划；置顶的不动）")


def cmd_sync(a) -> None:
    targets = ["meego", "multica"] if a.channel == "all" else [a.channel]
    results = {}
    for ch in targets:
        results[ch] = post(f"/api/sync/{ch}", {})
    if a.json:
        return _out_json(results)
    for ch, r in results.items():
        if r.get("ok"):
            print(f"{ch}: 同步 {r.get('synced',0)} 条")
        else:
            print(f"{ch}: 失败 {r.get('error','')[:120]}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="progress", description="OmniCompany progress service CLI（接 :8230）")
    # 公共参数 --json，挂到每个子命令上（progress board --json）
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="输出原始 JSON")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, help: str):
        return sub.add_parser(name, help=help, parents=[common])

    add("board", "看板概览").set_defaults(func=cmd_board)
    add("pins", "列出置顶").set_defaults(func=cmd_pins)

    sp = add("pin", "置顶 任务/任务线（对已置顶主体重复置顶会将其提到最前）")
    sp.add_argument("id"); sp.add_argument("--goal", action="store_true", help="置顶任务线(goal)而非具体任务")
    sp.add_argument("--note", default=None, help="备注；不给则对已置顶主体沿用旧备注，显式传空串才清空")
    sp.set_defaults(func=cmd_pin)

    sp = add("unpin", "取消置顶")
    sp.add_argument("id"); sp.add_argument("--goal", action="store_true"); sp.set_defaults(func=cmd_unpin)

    sp = add("tasks", "列出/搜索任务")
    sp.add_argument("--channel"); sp.add_argument("--search"); sp.add_argument("--limit", type=int, default=30)
    sp.set_defaults(func=cmd_tasks)

    sp = add("add", "新建任务（本地 / 自定义 demogame 域需求）")
    sp.add_argument("title"); sp.add_argument("--goal-id", dest="goal_id")
    sp.add_argument("--line", choices=["main", "side"], default="side")
    sp.add_argument("--channel", default="local", help="local（默认）/ demogame（自定义 demogame 域需求，meego 同步抓不到的，手动入口）")
    sp.add_argument("--ref", action="append", help="external_ref，可多次：feishu:<wiki-token> / meego:<id>（挂需求文档原链接）")
    sp.add_argument("--due", help="排期/DDL，形如 2026-06-30")
    sp.add_argument("--assignee", help="经办人，如 maintainer")
    sp.set_defaults(func=cmd_add)

    sp = add("progress", "给任务加一条进度")
    sp.add_argument("task_id"); sp.add_argument("text", nargs="+"); sp.set_defaults(func=cmd_progress)

    sp = add("patch", "改任务完成度/状态")
    sp.add_argument("task_id"); sp.add_argument("--completion", type=int); sp.add_argument("--status")
    sp.set_defaults(func=cmd_patch)

    sp = add("done", "标记任务完成(100%% / done)")
    sp.add_argument("task_id"); sp.set_defaults(func=cmd_done)

    sp = add("archive", "归档/取消归档任务")
    sp.add_argument("task_id"); sp.add_argument("--unarchive", action="store_true"); sp.set_defaults(func=cmd_archive)

    add("auto-archive", "归档所有已完成/进度≥90%% 的计划（置顶的豁免）").set_defaults(func=cmd_auto_archive)

    sp = add("sync", "拉外部渠道(meego/multica)")
    sp.add_argument("channel", nargs="?", choices=["meego", "multica", "all"], default="all")
    sp.set_defaults(func=cmd_sync)
    return p


def main(argv: list[str] | None = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK，强制 UTF-8 免崩
    except Exception:  # noqa: BLE001
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    # 让 --json 既能放全局也能放子命令后
    args.func(args)


if __name__ == "__main__":
    main()
