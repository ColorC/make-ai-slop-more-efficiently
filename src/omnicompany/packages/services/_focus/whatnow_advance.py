# [OMNI] origin=claude-code domain=services/_focus ts=2026-06-23 type=worker
# [OMNI] material_id="material:focus.whatnow_advance.py"
"""whatnow_advance — omni 自建 worker：自动推进本地任务 + 双渠道反馈。

非 multica issue 的本地任务（whatnow 里 channel=local 的 plan 类任务）走这里推进，
不经 multica 包装。meego / multica 仅作接单与反馈渠道。

入口：
  run(n_advance=2)  —— 推进 n 个本地任务 + 演示 multica/meego 双渠道反馈打通。
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root
from omnicompany.runtime.llm.structured import call_json, default_structured_model

WHATNOW = "http://127.0.0.1:8230"
MULTICA = os.path.join(os.environ.get("USERPROFILE", ""), ".multica", "bin", "multica.exe")

ADVANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["progress_note", "next_action", "new_completion"],
    "properties": {
        "progress_note": {"type": "string"},   # 一句话最新进展(≤40字)
        "next_action": {"type": "string"},      # 下一步具体动作(≤40字)
        "new_completion": {"type": "integer"},  # 推进后完成度估计 0-100
    },
}

SYSTEM = (
    "你是 omni 本地任务推进工人。给定一个计划任务及其计划摘录，给出：一句话最新进展(progress_note,中文≤40字)、"
    "下一步具体动作(next_action,中文≤40字)、推进后完成度估计(new_completion,0-100,不低于当前)。只输出 JSON。"
)


def _get(path: str) -> dict:
    return json.loads(urllib.request.urlopen(WHATNOW + path, timeout=30).read().decode("utf-8", "replace"))


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(WHATNOW + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())


def _plan_excerpt(plan_id: str) -> str:
    root = Path(omni_workspace_root())
    base = root / "docs" / "plans" / plan_id
    for rel in ("plan.md", "brief.md"):
        p = base / rel
        if p.is_file():
            try:
                t = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            import re
            t = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", t, flags=re.DOTALL)
            t = re.sub(r"\s+", " ", t).strip()
            if t:
                return t[:1200]
    return ""


def _multica(parts: list[str]) -> tuple[bool, str]:
    try:
        o = subprocess.run([MULTICA, *parts], capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace",  # Windows 默认 GBK 解不了 multica 的 UTF-8 输出
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return (o.returncode == 0, ((o.stdout or "") or (o.stderr or "")).strip())
    except Exception as e:  # noqa: BLE001
        return (False, str(e))


def _meego(parts: list[str]) -> tuple[bool, str]:
    try:
        cmd = (["cmd", "/C", "meegle", *parts] if os.name == "nt" else ["meegle", *parts])
        o = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace",
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return (o.returncode == 0, ((o.stdout or "") or (o.stderr or "")).strip())
    except Exception as e:  # noqa: BLE001
        return (False, str(e))


def advance_local(n: int = 2, echo=print) -> list[dict]:
    """挑 n 个本地(channel=local)未完成、带 plan 的任务，用 omni LLM 推进，写回 whatnow。"""
    board = _get("/api/board")
    tasks = [t for c in board.get("clusters", []) for g in c.get("goals", []) for t in g.get("tasks", [])
             if t.get("channel") == "local" and t.get("status") != "done" and t.get("plan_id")]
    tasks.sort(key=lambda t: t.get("completion", 0))
    picked = tasks[:n]
    model = default_structured_model()
    out = []
    for t in picked:
        ex = _plan_excerpt(t["plan_id"])
        try:
            r = call_json(system=SYSTEM,
                          user=f"任务:{t['title']}\n当前完成度:{t.get('completion',0)}\n计划摘录:{ex}",
                          schema=ADVANCE_SCHEMA, model=model, caller="focus.whatnow_advance", max_tokens=600)
        except Exception as e:  # noqa: BLE001
            echo(f"  [skip] {t['title'][:24]}: LLM 失败 {e}")
            continue
        note = f"[omni-worker 推进] {r.get('progress_note','')} | 下一步: {r.get('next_action','')}"
        new_c = max(int(t.get("completion", 0)), int(r.get("new_completion", t.get("completion", 0))))
        _post("/api/progress", {"subject_kind": "task", "subject_id": t["id"], "text": note, "source": "omni-worker"})
        _post("/api/task/patch", {"id": t["id"], "completion": new_c,
                                  "status": "in_progress" if new_c < 100 else "done"})
        echo(f"  [done] 推进 {t['title'][:30]} {t.get('completion',0)}=>{new_c}% | {r.get('progress_note','')[:30]}")
        out.append({"id": t["id"], "title": t["title"], "from": t.get("completion", 0), "to": new_c,
                    "note": r.get("progress_note", ""), "next": r.get("next_action", "")})
    return out


def feedback_channels(note: str, meego_work_item_id: str | None = None, echo=print) -> dict:
    """双渠道反馈打通：multica 创建测试 issue 并评论(真,自有测试单)；meego 评论 --dry-run(证明打通,不污染线上)。

    Windows 下中文 argv 会被 subprocess 损坏，故 title 用 ASCII、中文内容一律走 --*-file(UTF-8)。
    """
    res: dict[str, Any] = {}
    tmp = Path(omni_workspace_root()) / "data" / "_workspaces" / "whatnow_sweep"
    tmp.mkdir(parents=True, exist_ok=True)
    desc_f = tmp / "fb_desc.txt"; desc_f.write_text(f"omni-worker 本地任务推进回执（whatnow 联调测试单，非真实业务）\n反馈: {note}", encoding="utf-8")
    cmt_f = tmp / "fb_comment.txt"; cmt_f.write_text(f"omni-worker 反馈: {note}", encoding="utf-8")
    # multica：建一个联调测试 issue + 评论（真实但隔离，自有测试单；ASCII 标题避开中文 argv 损坏）
    ok, raw = _multica(["issue", "create", "--title", "[whatnow-sync] local-task advance receipt test",
                        "--description-file", str(desc_f), "--allow-duplicate", "--output", "json"])
    issue_id = ""
    if ok:
        try:
            j = json.loads(raw); issue_id = j.get("id") or j.get("key") or ""
        except Exception:  # noqa: BLE001
            pass
    if issue_id:
        ok2, _ = _multica(["issue", "comment", "add", issue_id, "--content-file", str(cmt_f), "--output", "json"])
        res["multica"] = {"issue": issue_id, "comment_ok": ok2}
        echo(f"  multica 反馈打通: issue {issue_id} 评论 {'OK' if ok2 else '失败'}")
    else:
        res["multica"] = {"error": raw[:160]}
        echo(f"  multica 反馈: 建测试单失败 {raw[:120]}")
    # meego：评论 --dry-run（证明渠道打通，不真写线上单）
    if meego_work_item_id:
        ok3, raw3 = _meego(["comment", "add", "--work-item-id", meego_work_item_id, "--set", f"content={note}", "--dry-run"])
        res["meego"] = {"work_item_id": meego_work_item_id, "dry_run_ok": ok3, "rendered": raw3[:160]}
        echo(f"  meego 反馈打通(dry-run): work_item {meego_work_item_id} → {'渲染OK' if ok3 else raw3[:60]}")
    return res


def run(n_advance: int = 2, echo=print) -> dict:
    echo("[omni-worker] 自动推进本地任务（非 multica，走 omni 自建 worker）…")
    advanced = advance_local(n_advance, echo=echo)
    # 取一个已同步的 meego work_item_id 做反馈演示
    board = _get("/api/board")
    meego_id = None
    for t in board.get("loose_tasks", []):
        for ref in t.get("external_refs", []):
            if ref.startswith("meego:"):
                cand = ref.split(":", 1)[1]
                if cand.isdigit():  # 跳过合成测试 ref，取真实 work_item_id
                    meego_id = cand
                    break
        if meego_id:
            break
    note = advanced[0]["note"] if advanced else "本地任务已由 omni worker 推进一步"
    echo("[omni-worker] 双渠道反馈打通演示（meego + multica）…")
    fb = feedback_channels(note, meego_work_item_id=meego_id, echo=echo)
    return {"advanced": advanced, "feedback": fb}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK，强制 UTF-8 免崩
    except Exception:  # noqa: BLE001
        pass
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    summary = run(n)
    print("\n=== summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
