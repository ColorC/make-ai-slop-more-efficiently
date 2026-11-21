"""Reap ended task temp directories (``<dir>/.omni/tmp/<task>``) — user rule 2026-10-14.

A task temp directory counts as ended, and is deleted through ``verified_cleanup.remove``, when all hold:

1. nothing under it was written for ``idle_days`` (7) days — measured without following links; a
   measurement cut off by its bound cannot prove that and the directory is kept;
2. it carries no batch manifest (``record.json`` / ``copy.json``) at its root — batches follow their own expiry;
3. no plan whose status is active / paused / blocked mentions it (its name, at least six characters, or its path)
   in ``plan.md`` / ``progress.md`` under the plans root; plans whose ``work_type`` is a cleanup campaign do not
   count — their ledgers name directories as cleanup targets;
4. rmtree's own checks pass (processes, links, hard links, Git data) at deletion time.

Usage:
    python -m omnicompany.core.task_temp_reaper --root C:/workspace/ [--root …] [--days 7] [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

REPARSE = 0x400
SKIP_DIRS = {".git", "node_modules", "$recycle.bin", "system volume information"}
LIVE_PLAN_STATUSES = {"active", "paused", "blocked", "in_progress", "in-progress"}
CLEANUP_WORK_TYPES = {"cleanup-campaign", "cleanup"}
DEFAULT_PLANS_ROOT = r"C:/workspace/omnicompany\docs\plans"
MIN_NAME_FOR_REFERENCE = 6
MEASURE_CAP_ENTRIES = 400_000
MEASURE_CAP_SECONDS = 30.0


def _is_link(entry: os.DirEntry, st: os.stat_result) -> bool:
    return entry.is_symlink() or bool(getattr(st, "st_file_attributes", 0) & REPARSE)


def find_temp_areas(roots: Iterable[str], max_depth: int = 8) -> list[str]:
    """Every ``.omni/tmp`` directory under ``roots`` (no link following, skipping .git / node_modules)."""
    found: list[str] = []
    for root in roots:
        stack = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for e in entries:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _is_link(e, st) or not stat.S_ISDIR(st.st_mode) or e.name.lower() in SKIP_DIRS:
                    continue
                if e.name == ".omni":
                    tmp = os.path.join(e.path, "tmp")
                    if os.path.isdir(tmp) and not os.path.islink(tmp):
                        found.append(tmp)
                if depth + 1 < max_depth and e.name != ".omni":
                    stack.append((e.path, depth + 1))
    return sorted(set(found))


def measure(root: str) -> dict[str, Any]:
    files = 0
    total = 0
    newest = 0.0
    started, stack, partial = time.monotonic(), [root], False
    while stack:
        if files > MEASURE_CAP_ENTRIES or time.monotonic() - started > MEASURE_CAP_SECONDS:
            partial = True
            break
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            newest = max(newest, st.st_mtime)
            if _is_link(e, st):
                continue
            if stat.S_ISDIR(st.st_mode):
                stack.append(e.path)
            else:
                files += 1
                total += st.st_size
    return {"files": files, "bytes": total, "newest": newest or None, "partial": partial}


def live_plan_texts(plans_root: str = DEFAULT_PLANS_ROOT) -> list[tuple[str, str]]:
    """``(path, text)`` of plan.md / progress.md of every plan whose plan.md status is live."""
    texts: list[tuple[str, str]] = []
    for dirpath, _dirnames, filenames in os.walk(plans_root):
        names = {f.lower(): f for f in filenames}
        if "plan.md" not in names:
            continue
        plan = os.path.join(dirpath, names["plan.md"])
        try:
            head = open(plan, encoding="utf-8", errors="replace").read(4000)
        except OSError:
            continue
        m = re.search(r"^status:\s*(\S+)", head, re.M)
        if not m or m.group(1).strip("'\"").lower() not in LIVE_PLAN_STATUSES:
            continue
        kind = re.search(r"^work_type:\s*(\S+)", head, re.M)
        if kind and kind.group(1).strip("'\"").lower() in CLEANUP_WORK_TYPES:
            continue  # a cleanup ledger names directories as targets, not as things it uses
        for key in ("plan.md", "progress.md"):
            if key in names:
                p = os.path.join(dirpath, names[key])
                try:
                    texts.append((p, open(p, encoding="utf-8", errors="replace").read()))
                except OSError:
                    pass
    return texts


def judge(task_dir: str, *, idle_days: float, plans: list[tuple[str, str]], now: float) -> dict[str, Any]:
    name = os.path.basename(task_dir.rstrip("\\/"))
    m = measure(task_dir)
    row: dict[str, Any] = {"path": task_dir, "task": name, **m, "reasons_kept": []}
    if m["partial"]:
        row["reasons_kept"].append("量不全，无法证明没有新写入")
    elif m["newest"] and now - m["newest"] < idle_days * 86400:
        row["reasons_kept"].append(f"最近 {idle_days:g} 天内有写入")
    for manifest in ("record.json", "copy.json"):
        if os.path.exists(os.path.join(task_dir, manifest)):
            row["reasons_kept"].append(f"带批次清单 {manifest}，按清单到期处理")
    if len(name) < MIN_NAME_FOR_REFERENCE:
        row["reasons_kept"].append("任务名太短，无法核对计划引用")
    else:
        refs = [p for p, text in plans if name in text or task_dir in text]
        if refs:
            row["reasons_kept"].append(f"被进行中/暂停的计划引用：{refs[0]}")
            row["plan_refs"] = refs[:5]
    row["idle_days"] = round((now - m["newest"]) / 86400, 1) if m["newest"] else None
    row["ended"] = not row["reasons_kept"]
    return row


def reap(roots: Iterable[str], *, idle_days: float = 7, dry_run: bool = False, plans_root: str = DEFAULT_PLANS_ROOT,
         process_rows: Callable[[], Iterable[dict[str, Any]]] | None = None, log_path: Path | None = None,
         on_row: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
    from omnicompany.core import verified_cleanup as vc

    plans = live_plan_texts(plans_root)
    snapshot: list[dict[str, Any]] = []

    def rows() -> list[dict[str, Any]]:
        if not snapshot:
            snapshot.extend((process_rows or vc._process_rows)())
        return snapshot

    results = []
    for area in find_temp_areas(roots):
        try:
            tasks = sorted(e.path for e in os.scandir(area) if e.is_dir(follow_symlinks=False))
        except OSError:
            continue
        for task_dir in tasks:
            row = judge(task_dir, idle_days=idle_days, plans=plans, now=time.time())
            if row["ended"]:
                done = vc.remove(task_dir, dry_run=dry_run, process_rows=rows, log_path=log_path,
                                 reason=f"任务临时目录已结束：{idle_days:g} 天无写入、无批次清单、未被进行中或暂停的计划引用（用户 2026-10-14 规则）")
                row["outcome"] = done["outcome"]
                row["problems"] = done["problems"][:3]
            else:
                row["outcome"] = "kept"
            results.append(row)
            if on_row:
                on_row(row)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m omnicompany.core.task_temp_reaper", description=__doc__.splitlines()[0])
    parser.add_argument("--root", action="append", required=True, help="在这些根下找 .omni\\tmp（可多次）")
    parser.add_argument("--days", type=float, default=7)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--plans-root", default=DEFAULT_PLANS_ROOT)
    parser.add_argument("--out", help="结果 JSON 写到这里")
    args = parser.parse_args(argv)
    results = reap(args.root, idle_days=args.days, dry_run=args.dry_run, plans_root=args.plans_root)
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    counts: dict[str, list[float]] = {}
    for r in results:
        c = counts.setdefault(r["outcome"], [0, 0])
        c[0] += 1
        c[1] += r["bytes"]
    for k, (n, b) in sorted(counts.items()):
        print(f"{k}: {int(n)} 个任务目录，{b / 2**30:.1f} GiB")
    from omnicompany.core.verified_cleanup import receipt_log_path

    print(f"回执：{receipt_log_path()}")
    return 2 if any(r["outcome"] in ("refused", "incomplete") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
