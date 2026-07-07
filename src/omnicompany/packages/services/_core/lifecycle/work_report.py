# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-06-25T00:00:00Z type=infra status=active
# [OMNI] summary="工作报告聚合器: 把 splits/tasks/bindings/plan_runs/materials 聚成五章中文 markdown, 落 plan_reports/"
# [OMNI] why="WORK-REPORT-AND-REVIEW-TYPES P-2: plan run 跑完要有可审阅工作报告, 关上 '缺过程审阅' 缺口"
# [OMNI] tags=lifecycle,work-report,aggregator,markdown
# [OMNI] material_id="material:services._core.lifecycle.work_report.py"
"""plan 工作报告聚合器。

聚合来源:
- ``data/lifecycle/splits/<plan_safe>.json`` — 拆分理由
- ``TaskStore().list_tasks(plan_id)`` — task 与状态
- ``dispatch_task.list_task_bindings(plan_id)`` — 每 task 的 agent 绑定
- ``data/lifecycle/plan_runs/`` 该 plan 最近一次 run 的 ``report``
- ``board._materials_for_plan(plan_id)`` — 审阅物料

渲染中文 markdown 五章 (骨架见 ``docs/standards/concepts/work_report.md``):
一·执行概览 / 二·拆分思路 / 三·逐 task 执行结果 / 四·审阅与回流 / 五·结论。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _safe(plan_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", plan_id)


def _lifecycle_dir() -> Path:
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root() / "data" / "lifecycle"


def _load_split(plan_id: str) -> dict[str, Any]:
    p = _lifecycle_dir() / "splits" / f"{_safe(plan_id)}.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _find_latest_run_report(plan_id: str) -> tuple[dict[str, Any], str | None]:
    """扫 plan_runs/ 找该 plan 最近一次 run 的 report。返回 (report, run_id)。"""
    d = _lifecycle_dir() / "plan_runs"
    if not d.is_dir():
        return {}, None
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for f in d.glob("*.json"):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if raw.get("plan_id") != plan_id:
            continue
        candidates.append((f.stat().st_mtime, raw.get("run_id") or f.stem, raw))
    if not candidates:
        return {}, None
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, run_id, raw = candidates[0]
    return raw.get("report") or {}, run_id


def _fmt_cell(v: Any) -> str:
    if v is None or v == "":
        return "-"
    if isinstance(v, list):
        return ",".join(str(x) for x in v) if v else "-"
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v).replace("|", "\\|").replace("\n", " ")


_STATUS_CN = {
    "pending": "待办",
    "in_progress": "执行中",
    "review": "待审阅",
    "done": "已完成",
    "blocked": "受阻",
    "deferred": "延后",
    "cancelled": "已取消",
}


def _render_markdown(
    plan_id: str,
    *,
    split: dict[str, Any],
    tasks: list[Any],
    bindings: dict[str, dict[str, Any]],
    run_report: dict[str, Any],
    materials: list[dict[str, Any]],
) -> str:
    status_counts: dict[str, int] = {}
    for t in tasks:
        status_counts[t.status] = status_counts.get(t.status, 0) + 1
    done_tasks = [t for t in tasks if t.status in {"done", "cancelled"}]
    unfinished = [t for t in tasks if t.status not in {"done", "cancelled"}]

    dispatched_by_id: dict[str, dict[str, Any]] = {}
    for d in run_report.get("dispatched") or []:
        tid = str(d.get("task_id") or "")
        if tid:
            dispatched_by_id[tid] = d

    mats_by_task: dict[str, list[dict[str, Any]]] = {}
    for m in materials:
        sid = m.get("source_subagent_id") if isinstance(m, dict) else None
        if isinstance(sid, str) and sid.startswith("task-") and sid[len("task-"):]:
            mats_by_task.setdefault(sid[len("task-"):], []).append(m)
        else:
            mats_by_task.setdefault("_plan", []).append(m)

    how_run = {"warm": "一个 agent 一口气接着做完（不每个任务重开）",
               "cold": "每个任务单独开一个 agent"}.get(run_report.get("session_mode"), None)
    why_stop = {"no_ready_task": "所有能做的任务都做完了", "max_steps": "做到设定的步数就停了",
                "task_failed": "有任务失败，停下来等人处理", "hold_at_review": "每个任务做完先停下等人验收",
                "reached_until_task": "做到指定任务就停了", "stuck_no_progress": "有任务卡住没进展，停下",
                "gate_blocked": "计划没写全，没让执行"}.get(run_report.get("stopped_reason"), run_report.get("stopped_reason") or "—")

    lines: list[str] = []
    lines.append(f"# 工作报告 · {plan_id}")
    lines.append("")
    lines.append("> 这份报告说明：这个计划具体做了哪些事、每件事做了什么、产出了什么、还剩什么没做。")
    lines.append("")

    # 总览
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- 这个计划一共拆成 {len(tasks)} 件事，已经做完 {len(done_tasks)} 件。")
    if run_report.get("steps") is not None:
        line = f"- 这次执行做了 {run_report.get('steps')} 件"
        if how_run:
            line += f"，{how_run}"
        line += f"；{why_stop}。"
        lines.append(line)
    tokens = run_report.get("tokens")
    if isinstance(tokens, dict) and tokens.get("total"):
        lines.append(f"- 这次执行大约用了 {tokens.get('total')} 个 token"
                     f"（输入 {tokens.get('input', 0)}、输出 {tokens.get('output', 0)}）。")
    pending_review = [m for m in materials if (m.get("status") in (None, "pending", "", "待审"))]
    lines.append(f"- 产出了 {len(materials)} 份给人看的东西，其中 {len(pending_review)} 份还没审。")
    lines.append("")

    # 怎么拆的、为什么
    lines.append("## 怎么拆的、为什么这么拆")
    lines.append("")
    rationale = (split.get("split_rationale") or "").strip()
    if rationale:
        lines.append(rationale)
    else:
        lines.append("（这个计划的任务是手工建的，或拆分早于现在的记录，没有留下整体拆分说明。）")
    lines.append("")
    split_by_id: dict[str, dict[str, Any]] = {}
    for st in split.get("tasks") or []:
        sid = str(st.get("id") or "")
        if sid:
            split_by_id[sid] = st
    for t in tasks:
        st = split_by_id.get(t.id, {})
        reasoning = (st.get("reasoning") or t.reasoning or "").strip()
        if reasoning:
            lines.append(f"- **{t.title}**：{reasoning}")
    lines.append("")

    # 每件事具体做了什么
    lines.append("## 每件事具体做了什么")
    lines.append("")
    if not tasks:
        lines.append("（还没有任务。）")
    for t in tasks:
        d = dispatched_by_id.get(t.id, {})
        mats = mats_by_task.get(t.id, [])
        done = t.status in {"done", "cancelled"}
        lines.append(f"### {t.id}. {t.title} — {'已完成' if done else _STATUS_CN.get(t.status, t.status)}")
        # 动手前先估的:文件范围 / 预计产出 / 工作量·难度(让人对账"说的"和"做的")
        scope = getattr(t, "file_scope", None) or []
        exp = getattr(t, "expected_outputs", None) or []
        wl, df = getattr(t, "workload", None), getattr(t, "difficulty", None)
        pre = []
        if scope:
            pre.append("范围 " + "、".join(f"`{s}`" for s in scope[:8]))
        if wl is not None or df is not None:
            pre.append(f"工作量 {wl if wl is not None else '?'}/难度 {df if df is not None else '?'}")
        if pre:
            lines.append("计划时估的：" + " · ".join(pre))
        if exp:
            lines.append("预计产出：" + "、".join(str(e) for e in exp[:8]))
        # 做了什么: 优先用 agent 边做边记的 notes(具体), 其次用执行摘要
        notes = list(getattr(t, "notes", []) or [])
        if notes:
            lines.append("做了什么（执行时记的）：")
            for n in notes:
                txt = (n.get("text") if isinstance(n, dict) else str(n)) or ""
                txt = txt.strip().replace("\n", " ")
                if txt:
                    lines.append(f"- {txt}")
        elif d.get("summary"):
            lines.append(f"做了什么：{(d.get('summary') or '').strip()}")
        else:
            lines.append("做了什么：（没有留下执行记录）")
        # 改了哪些文件
        changed = d.get("changed_files") or []
        if changed:
            lines.append(f"改动文件：{', '.join('`'+c+'`' for c in changed[:12])}"
                         + ("…" if len(changed) > 12 else ""))
        # 产出待审
        if mats:
            names = "；".join(f"{m.get('title') or m.get('id')}（{m.get('status') or '待审'}）" for m in mats)
            lines.append(f"产出待审：{names}")
        lines.append("")

    # 还剩什么 + 待审
    lines.append("## 还剩什么、要人看什么")
    lines.append("")
    if unfinished:
        lines.append("还没做完的：")
        for t in unfinished:
            lines.append(f"- {t.id}. {t.title}（{_STATUS_CN.get(t.status, t.status)}）")
    else:
        lines.append("- 所有任务都做完了。")
    if pending_review:
        lines.append("")
        lines.append("等你审的产出（在审阅台点开看）：")
        for m in pending_review:
            lines.append(f"- {m.get('title') or m.get('id')}")
    lines.append("")

    return "\n".join(lines)


def build_work_report(
    plan_id: str,
    *,
    run_report: dict[str, Any] | None = None,
    run_id: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """聚合 plan 工作报告 → 五章中文 markdown。

    Returns:
        ``{ok, path, markdown}`` — ``path`` 为持久化路径(persist=False 时为空)。
    """
    from omnicompany.packages.services._core.lifecycle.board import _materials_for_plan
    from omnicompany.packages.services._core.lifecycle.dispatch_task import (
        list_task_bindings,
    )
    from omnicompany.packages.services._core.lifecycle.task import TaskStore

    split = _load_split(plan_id)
    tasks = TaskStore().list_tasks(plan_id)
    bindings_list = list_task_bindings(plan_id)
    bindings = {str(b.get("task_id")): b for b in bindings_list if b.get("task_id") is not None}

    resolved_run_id = run_id
    rr: dict[str, Any] = {}
    if run_report is not None:
        rr = dict(run_report)
    else:
        rr, latest_id = _find_latest_run_report(plan_id)
        if resolved_run_id is None:
            resolved_run_id = latest_id

    if not isinstance(rr.get("tokens"), dict):
        try:
            from omnicompany.packages.services._core.lifecycle.plan_timeline import (
                build_plan_timeline,
            )
            tl_tokens = (build_plan_timeline(plan_id).get("totals") or {}).get("tokens")
            if isinstance(tl_tokens, dict):
                rr["tokens"] = tl_tokens
        except Exception:
            pass

    materials = _materials_for_plan(plan_id)

    md = _render_markdown(
        plan_id,
        split=split,
        tasks=tasks,
        bindings=bindings,
        run_report=rr,
        materials=materials,
    )

    out_path = ""
    if persist:
        reports_dir = _lifecycle_dir() / "plan_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        stem = resolved_run_id or _safe(plan_id)
        path = reports_dir / f"{stem}.md"
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(md, encoding="utf-8")
        tmp.replace(path)
        out_path = str(path)

    return {"ok": True, "path": out_path, "markdown": md}


def submit_versioned_report(
    plan_id: str, markdown: str, *, title: str,
    kind: str = "agent-workflow-report", source_subagent_id: str = "work-report",
) -> dict[str, Any]:
    """提交一份工作报告物料, 但**版本化**: 同 plan 的旧报告归档 + 把它们记成历史版本。

    解决两件事: ① 同一材料的不同版本(新报告是第 N 版, 旧版可回看);
    ② 报告不再每次堆一份在待审里(旧版自动归档, 待审只留最新)。
    """
    try:
        from omnicompany.dashboard.boss_sight.reviewstage.routes import get_store
        store = get_store()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}

    # 找该 plan 现有的同类报告(未归档的), 作为旧版本
    prev = []
    try:
        for m in store.list(plan_id=plan_id, include_archived=False):
            mk = str(getattr(getattr(m, "kind", ""), "value", getattr(m, "kind", "")))
            rk = (getattr(m, "extra", {}) or {}).get("report_kind")
            if mk == kind or rk == kind or mk == "agent-workflow-report":
                prev.append(m)
    except Exception:
        prev = []

    version_history = []
    for p in prev:
        pid = getattr(p, "id", None) or getattr(p, "material_id", None)
        # version 现是正式字段; 兼容改造前遗留材料(那时候版本号存在 extra.version 里)。
        legacy_version = (getattr(p, "extra", {}) or {}).get("version", "?")
        version_history.append({
            "version": getattr(p, "version", None) or legacy_version,
            "material_id": pid,
            "created_at": getattr(p, "created_at", None),
            "title": getattr(p, "title", None),
        })
        # 旧版归档(从待审列表移走, 仍可在"已归档"里回看)
        try:
            if pid:
                store.set_archived(pid, True, by="work-report-versioning")
        except Exception:
            pass
    # 历史里也带上更早版本的链
    for p in prev:
        for older in (getattr(p, "extra", {}) or {}).get("previous_versions", []) or []:
            if older not in version_history:
                version_history.append(older)

    report_version = len(version_history) + 1
    # Material 契约链(第二期 A1): version 现在是 store.create() 的正式字段(不能塞进 extra,
    # 那会撞 "extra 禁塞 project/track/version/version_family" 阻断)。project 沿用
    # authored/store.py 的 plan→project 前缀匹配(同一套项目名录真源=决策库); 匹配不上落
    # "unfiled"(义务在 agent 不在人, 自动化闭环不应被标签卡住)。
    try:
        from omnicompany.dashboard.boss_sight.authored.store import _project_for_plan
        project = _project_for_plan(plan_id) or "unfiled"
    except Exception:  # noqa: BLE001
        project = "unfiled"
    try:
        m = store.create(
            kind=kind, tier="important",
            title=(f"{title} · 第{report_version}版" if report_version > 1 else title)[:80],
            source_plan_id=plan_id, source_subagent_id=source_subagent_id,
            inline_content=markdown,
            extra={"report_kind": "agent-workflow-report",
                   "previous_versions": version_history},
            project=project,
            track="工作报告",
            version=report_version,
            version_family=title,
        )
        return {"ok": True, "material_id": getattr(m, "id", None), "version": report_version,
                "archived_old": len(prev)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


__all__ = ["build_work_report", "submit_versioned_report"]
