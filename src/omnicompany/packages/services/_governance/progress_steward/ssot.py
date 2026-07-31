# [OMNI] origin=codex domain=services/_governance/progress_steward ts=2026-07-10T00:00:00Z type=router
# [OMNI] summary="计划进度唯一真源审计: 对照 WhatNow 圈出本地状态副本与权威内部矛盾"
# [OMNI] why="宽口径措辞扫描会跳过 OMNI 头, 无法发现 plan status 本身已经形成第二真源"
# [OMNI] tags=governance,progress-ssot,whatnow,reviewstage
# [OMNI] material_id="material:governance.progress_steward.ssot.py"
"""Deterministic plan-progress single-source-of-truth governance."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root
from omnicompany.core.progress_authority import (
    PLAN_GOAL_AUTHORITIES,
    goals_by_id,
    iter_progress_tasks,
    load_progress_board,
    task_groups_by_plan,
)

from .probe import report_dir


_PLAN_META_RE = re.compile(
    r"\b(status|completion|progress|current_step|next_step)=(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I,
)
_YAML_FIELD_RE = re.compile(r"^\s*(status|completion|progress|current_step|next_step)\s*:\s*(.+?)\s*$", re.I)
_BODY_FIELD_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?(当前状态|完成度|进度|当前步骤|下一步)(?:\*\*)?\s*[:：]\s*(.+?)\s*$",
)
_OVERALL_DONE_NOTE_RE = re.compile(
    r"(?:\d+\s*个?\s*(?:里程碑|阶段|任务)|(?:全部|所有)\s*(?:里程碑|阶段|任务))"
    r".{0,10}(?:全做出|全部完成|均已完成|已收官|all\s+done)",
    re.I,
)
_OPEN_WORK_NOTE_RE = re.compile(r"待(?:用户|验收|裁决|复现|批准)|仍|剩|缺少|收尾|未完成|未做", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plan_id(path: Path, root: Path) -> str:
    plans = root / "docs" / "plans"
    try:
        return path.parent.relative_to(plans).as_posix()
    except ValueError:
        return ""


def _active_plan_paths(root: Path) -> list[Path]:
    plans = root / "docs" / "plans"
    if not plans.is_dir():
        return []
    return sorted(
        path for path in plans.rglob("*.md")
        if path.name.lower() == "plan.md"
        and not any(part in {"_archive", "_graveyard", "_scratch"} for part in path.relative_to(plans).parts)
    )


def _claims(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    claims: list[dict[str, Any]] = []
    in_header_fence = False
    for line_no, line in enumerate(lines[:40], 1):
        if line.lstrip().startswith("```"):
            in_header_fence = not in_header_fence
            continue
        if in_header_fence or "[OMNI]" not in line:
            continue
        for match in _PLAN_META_RE.finditer(line):
            claims.append({
                "source": "omnimark", "line": line_no,
                "field": match.group(1).lower(), "value": match.group(2).strip("\"'"),
            })
    first_nonempty = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first_nonempty is not None and lines[first_nonempty].strip() == "---":
        for index in range(first_nonempty + 1, min(len(lines), first_nonempty + 80)):
            if lines[index].strip() == "---":
                break
            match = _YAML_FIELD_RE.match(lines[index])
            if match:
                claims.append({
                    "source": "yaml", "line": index + 1,
                    "field": match.group(1).lower(), "value": match.group(2).strip("\"'"),
                })
    in_fence = False
    for line_no, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or "[OMNI]" in line:
            continue
        match = _BODY_FIELD_RE.match(line)
        if not match:
            continue
        field = {
            "当前状态": "status", "完成度": "completion", "进度": "progress",
            "当前步骤": "current_step", "下一步": "next_step",
        }[match.group(1)]
        claims.append({"source": "body", "line": line_no, "field": field, "value": match.group(2).strip()})
    return claims


def _status(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"active", "in_progress", "ongoing", "wip", "doing"}:
        return "in_progress"
    if text in {"done", "complete", "completed", "finished", "closed"}:
        return "done"
    if text in {"pending", "planned", "todo", "not_started"}:
        return "pending"
    return text


def _percent(value: Any) -> int | None:
    match = re.search(r"(?<!\d)(100|[0-9]{1,2})\s*%?", str(value or ""))
    return int(match.group(1)) if match else None


def scan_plan_progress_authority(root: Path, board: dict[str, Any] | None) -> dict[str, Any]:
    """Read-only deterministic audit. No plan file is ever modified."""
    root = Path(root)
    if board is None:
        violation = {
            "category": "authority_unreachable", "certainty": "absolute",
            "reason": "WhatNow board 不可达; 本轮不能宣称进度唯一真源为 clean。",
        }
        return {
            "scan_state": "unavailable", "clean": False, "scanned_plans": 0,
            "authority_tasks": 0, "violation_count": 1,
            "counts": {"authority_unreachable": 1}, "violations": [violation],
        }
    tasks = list(iter_progress_tasks(board))
    by_plan = task_groups_by_plan(board)
    by_goal = goals_by_id(board)
    violations: list[dict[str, Any]] = []
    paths = _active_plan_paths(root)
    for path in paths:
        plan_id = _plan_id(path, root)
        plan_tasks = by_plan.get(plan_id, [])
        claims = _claims(path)
        goal_id = PLAN_GOAL_AUTHORITIES.get(plan_id)
        goal_authority = by_goal.get(goal_id) if goal_id else None
        authority = plan_tasks[0] if len(plan_tasks) == 1 else goal_authority
        if len(plan_tasks) > 1:
            violations.append({
                "plan_id": plan_id, "plan_path": path.relative_to(root).as_posix(),
                "category": "duplicate_authority_task", "certainty": "absolute",
                "task_ids": [task.get("id") for task in plan_tasks],
                "reason": "同一 plan_id 在 WhatNow 有多个 task, 权威写入点不唯一。",
            })
        if not plan_tasks and not goal_authority:
            violations.append({
                "plan_id": plan_id, "plan_path": path.relative_to(root).as_posix(),
                "category": "missing_authority_task", "certainty": "absolute",
                "reason": "活跃计划在 WhatNow 无对应 task/goal, 当前进度没有权威落点。",
            })
        for claim in claims:
            if claim["source"] == "body":
                violations.append({
                    "plan_id": plan_id,
                    "plan_path": path.relative_to(root).as_posix(),
                    "category": "body_progress_candidate",
                    "certainty": "needs_review",
                    "local": claim,
                    "authority": ({
                        key: authority.get(key)
                        for key in ("id", "status", "completion", "archived", "latest_progress")
                    } if authority else None),
                    "reason": "正文进度措辞可能是历史记录或局部评估, 禁止自动删除; 交语义复核判定。",
                })
                continue
            item = {
                "plan_id": plan_id, "plan_path": path.relative_to(root).as_posix(),
                "category": "local_authority_duplicate", "certainty": "absolute",
                "local": claim,
                "authority": ({key: authority.get(key) for key in ("id", "status", "completion", "archived", "latest_progress")} if authority else None),
                "reason": f"plan.md 本地声明可变字段 {claim['field']}, 已形成 WhatNow 之外的第二真源。",
            }
            violations.append(item)
            if not authority:
                continue
            if claim["field"] == "status" and _status(claim["value"]) != _status(authority.get("status")):
                violations.append({**item, "category": "status_conflict", "reason": "本地状态与 WhatNow 当前状态不一致。"})
            if claim["field"] in {"completion", "progress"}:
                local_percent = _percent(claim["value"])
                authority_percent = _percent(authority.get("completion"))
                if local_percent is not None and authority_percent is not None and local_percent != authority_percent:
                    violations.append({**item, "category": "completion_conflict", "reason": "本地完成度与 WhatNow 当前完成度不一致。"})
    for plan_id, plan_tasks in by_plan.items():
        if len(plan_tasks) != 1:
            continue
        task = plan_tasks[0]
        latest = str(task.get("latest_progress") or "")
        summary = latest.split("| 证据:", 1)[0][:240]
        if (
            _OVERALL_DONE_NOTE_RE.search(summary)
            and not _OPEN_WORK_NOTE_RE.search(summary)
            and (_status(task.get("status")) != "done" or int(task.get("completion") or 0) < 100)
        ):
            violations.append({
                "plan_id": plan_id,
                "category": "authority_note_conflict_candidate", "certainty": "needs_review",
                "authority": {key: task.get(key) for key in ("id", "status", "completion", "archived", "latest_progress")},
                "reason": "WhatNow 最新进展声称全部做出, 但结构化状态仍非 100%/done。",
            })
    counts = Counter(item["category"] for item in violations)
    return {
        "scan_state": "ok", "clean": not violations, "scanned_plans": len(paths),
        "authority_tasks": len(tasks), "violation_count": len(violations),
        "counts": dict(counts), "violations": violations,
    }


def _remove_omnimark_field(line: str, field: str) -> str | None:
    for match in _PLAN_META_RE.finditer(line):
        if match.group(1).lower() != field:
            continue
        start, end = match.span()
        return line[:start].rstrip() + line[end:]
    return None


def apply_progress_ssot_fixes(
    *,
    root: Path,
    audit: dict[str, Any],
    write_report: bool = True,
) -> dict[str, Any]:
    """Remove only structured plan-progress copies; preserve prose and mtimes."""
    root = Path(root)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in audit.get("violations") or []:
        local = item.get("local") or {}
        if item.get("category") != "local_authority_duplicate":
            continue
        if local.get("source") not in {"omnimark", "yaml"}:
            continue
        if item.get("plan_path"):
            grouped[str(item["plan_path"])].append(local)

    changed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for relpath, claims in sorted(grouped.items()):
        path = root / relpath
        try:
            stat = path.stat()
            original = path.read_bytes()
            text = original.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            skipped.append({"path": relpath, "reason": f"read_failed:{type(exc).__name__}"})
            continue
        lines = text.splitlines(keepends=True)
        removed: list[dict[str, Any]] = []
        for claim in sorted(claims, key=lambda item: int(item.get("line") or 0), reverse=True):
            index = int(claim.get("line") or 0) - 1
            if index < 0 or index >= len(lines):
                skipped.append({"path": relpath, "claim": claim, "reason": "line_moved"})
                continue
            line = lines[index]
            source = claim.get("source")
            field = str(claim.get("field") or "").lower()
            if source == "omnimark":
                replacement = _remove_omnimark_field(line, field)
                if replacement is None:
                    skipped.append({"path": relpath, "claim": claim, "reason": "claim_changed"})
                    continue
                lines[index] = replacement
            else:
                match = _YAML_FIELD_RE.match(line.rstrip("\r\n"))
                if not match or match.group(1).lower() != field:
                    skipped.append({"path": relpath, "claim": claim, "reason": "claim_changed"})
                    continue
                lines.pop(index)
            removed.append(claim)
        updated = "".join(lines).encode("utf-8")
        if updated == original:
            continue
        path.write_bytes(updated)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        changed.append({
            "path": relpath,
            "removed": removed,
            "before_sha256": hashlib.sha256(original).hexdigest(),
            "after_sha256": hashlib.sha256(updated).hexdigest(),
            "mtime_preserved_ns": stat.st_mtime_ns,
        })

    payload = {
        "kind": "progress_ssot_fix",
        "generated_at": _now(),
        "changed_files": len(changed),
        "removed_claims": sum(len(item["removed"]) for item in changed),
        "changed": changed,
        "skipped": skipped,
    }
    if write_report:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = report_dir() / f"progress_ssot-fix-{stamp}.json"
        latest = report_dir() / "progress_ssot-fix-latest.json"
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        out.write_text(serialized, encoding="utf-8")
        latest.write_text(serialized, encoding="utf-8")
        payload.update({"_written": str(out), "_latest": str(latest)})
    return payload


def _review_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 计划进度唯一真源巡检", "",
        f"- 扫描计划: {payload['scanned_plans']}",
        f"- 违规/待复核: {payload['violation_count']}",
        f"- WhatNow 来源: {payload['authority_source']}", "",
        "## 分类", "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in sorted(payload.get("counts", {}).items()))
    lines.extend(["", "## 证据", ""])
    for item in payload.get("violations", [])[:100]:
        local = item.get("local") or {}
        anchor = f" L{local.get('line')} {local.get('field')}={local.get('value')}" if local else ""
        lines.append(f"- `{item.get('category')}` · `{item.get('plan_id', '-')}`{anchor} · {item.get('reason')}")
    return "\n".join(lines) + "\n"


def _submit_review_material(payload: dict[str, Any], review_store=None) -> dict[str, Any]:
    if payload.get("violation_count", 0) <= 0:
        return {"submitted": False, "reason": "clean"}
    if review_store is None:
        from omnicompany.dashboard.boss_sight.reviewstage.routes import get_store
        review_store = get_store()
    from omnicompany.dashboard.boss_sight.reviewstage.report_submission import submit_markdown_report

    reason = "后台发现计划文档含可变进度副本或 WhatNow 内部状态语义矛盾; 请只读核对证据。"
    return submit_markdown_report(
        review_store,
        title="计划进度唯一真源巡检",
        content=_review_markdown(payload),
        source_plan_id="omnicompany-governance/[2026-06-27]SEMANTIC-SPACE-HEALTH",
        reason=reason,
        dedupe_key="progress-ssot-audit",
        stable_payload=json.dumps(payload.get("violations", []), ensure_ascii=False, sort_keys=True),
        version_family="progress-ssot-audit",
        extra={"report_path": payload.get("_written")},
    )


def run_progress_ssot_audit(
    *,
    root: Path | None = None,
    board: dict[str, Any] | None = None,
    write: bool = True,
    submit_review: bool = False,
    fix: bool = False,
    review_store=None,
) -> dict[str, Any]:
    base = Path(root or omni_workspace_root())
    if board is None:
        board, source = load_progress_board(base, timeout=10)
    else:
        source = "provided"
    payload = scan_plan_progress_authority(base, board)
    payload.update({"kind": "progress_ssot", "generated_at": _now(), "authority_source": source})
    if fix and payload.get("scan_state") == "ok":
        fix_result = apply_progress_ssot_fixes(root=base, audit=payload, write_report=write)
        payload = scan_plan_progress_authority(base, board)
        payload.update({
            "kind": "progress_ssot",
            "generated_at": _now(),
            "authority_source": source,
            "fix": fix_result,
        })
    if write:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = report_dir() / f"progress_ssot-{stamp}.json"
        latest = report_dir() / "progress_ssot-latest.json"
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        out.write_text(serialized, encoding="utf-8")
        latest.write_text(serialized, encoding="utf-8")
        payload["_written"] = str(out)
        payload["_latest"] = str(latest)
    if submit_review:
        payload["review_material"] = _submit_review_material(payload, review_store=review_store)
    return payload
