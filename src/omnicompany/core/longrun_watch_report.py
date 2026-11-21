# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-10-17T13:00:00+08:00 type=infra status=active
# [OMNI] summary="agent-longrun-remote 观察报告：一次汇总验收门/子线程策略/omni remote 台账与窗口内转录命中，只读、单线程、限时。"
# [OMNI] why="2026-10-17 用户：设施先运行一段时间再复盘；复盘数据走确定性脚本，不经模型逐条搬。见 docs/projects/agent-longrun-remote/WATCHLIST.md。"
"""Observation report for agent-longrun-remote: one deterministic pass, no model in the loop.

    python -I longrun_watch_report.py --since 2026-10-17 [--until 2026-10-31] [--out <report.md>]

Collects:
  * ``omni accept stats``  - acceptance gate + sub-thread policy decision ledgers
  * ``omni remote stats``  - omni remote operation ledger (counts, failures, refusals, chars sent)
  * binding / frozen checklist counts under ~/.omni/acceptance
  * session transcripts modified in the window (Claude ~/.claude*/projects, Codex ~/.codex*/sessions):
    how many sessions hit each gate message (bare-ssh denial, unbound nudge, sub-thread denial)

Transcript scanning is single-threaded, limited to those two roots and to files modified inside the
window, and stops at --max-seconds (default 600).  Sessions that built these facilities quote the
messages in their own code edits; pass their ids with --exclude (the 2026-10-17 build session is
excluded by default).
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import sys
import time
from pathlib import Path

BUILD_SESSIONS = ("00147e0f-5b78-4b10-b39a-a895d968a98a",)
MARKERS = {
    "裸 ssh 被拦（safety hook）": "裸 ssh 向工作节点下发命令",
    "长回合收尾提醒（Stop，10-17 晚起）": "收尾提醒（本会话只出现这一次）",
    "未绑定长会话被提醒（Stop，已退役）": "没有绑定验收清单就声称完成了",
    "未验收的完成声明被拦（Stop）": "检测到完成声明，但绑定的验收清单还没通过",
    "goal 完成被拒（Codex PreToolUse）": "goal 标记完成前必须有冻结的验收清单",
    "子线程续派活被拒（Codex PreToolUse）": "超过一线程一任务的上限",
    "调用 omni remote": "omni remote ",
}


def _day(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


def _omni(args: list[str]) -> str:
    exe = shutil.which("omni")
    if not exe:
        return "(找不到 omni 命令)"
    proc = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    return (proc.stdout + proc.stderr).strip() or f"(无输出, exit {proc.returncode})"


def _transcripts(since: dt.datetime, until: dt.datetime) -> list[Path]:
    home = Path.home()
    lo, hi = since.timestamp(), until.timestamp()
    files: list[Path] = []
    for root in sorted(home.glob(".claude*")):
        projects = root / "projects"
        if projects.is_dir():
            files += [p for p in projects.glob("*/*.jsonl") if lo <= p.stat().st_mtime <= hi]
    for root in sorted(home.glob(".codex*")):
        day = since.date()
        while day <= until.date():
            folder = root / "sessions" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
            if folder.is_dir():
                files += list(folder.glob("rollout-*.jsonl"))
            day += dt.timedelta(days=1)
    return files


def _scan(files: list[Path], exclude: set[str], max_seconds: float) -> tuple[dict[str, int], int, bool]:
    needles = {label: text.encode("utf-8") for label, text in MARKERS.items()}
    hits = {label: 0 for label in needles}
    started = time.monotonic()
    scanned = 0
    for path in files:
        if any(sid in path.name for sid in exclude):
            continue
        if time.monotonic() - started > max_seconds:
            return hits, scanned, True
        found: set[str] = set()
        tail = b""
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(1 << 22):
                    block = tail + chunk
                    found.update(label for label, needle in needles.items() if needle in block)
                    tail = block[-256:]
        except OSError:
            continue
        scanned += 1
        for label in found:
            hits[label] += 1
    return hits, scanned, False


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="起点日期或 ISO 时间，如 2026-10-17")
    ap.add_argument("--until", default=None, help="终点（默认现在）")
    ap.add_argument("--out", type=Path, default=None, help="同时写入的报告文件（Markdown）")
    ap.add_argument("--exclude", nargs="*", default=list(BUILD_SESSIONS), help="不计入的会话号")
    ap.add_argument("--max-seconds", type=float, default=600)
    args = ap.parse_args()
    since = _day(args.since)
    until = _day(args.until) if args.until else dt.datetime.now()

    accept_dir = Path.home() / ".omni" / "acceptance"
    bindings = len(list((accept_dir / "bindings").glob("*.json")))
    frozen = len(list((accept_dir / "frozen").glob("*.json")))
    files = _transcripts(since, until)
    hits, scanned, cut = _scan(files, set(args.exclude), args.max_seconds)

    window = f"{since:%Y-%m-%d %H:%M} → {until:%Y-%m-%d %H:%M}"
    lines = [f"# 长跑与远程设施观察报告（{window}）", "", f"生成于 {dt.datetime.now():%Y-%m-%d %H:%M}。", "",
             "## 验收门与子线程策略台账", "", "```", _omni(["accept", "stats", "--since", args.since]), "```", "",
             f"当前绑定中的会话 {bindings} 个，冻结过的清单 {frozen} 份（{accept_dir}）。", "",
             "## omni remote 台账", "", "```", _omni(["remote", "stats", "--since", args.since]), "```", "",
             "## 会话转录命中（按会话数）", "",
             f"扫描 {scanned} / {len(files)} 份窗口内转录{'（到时限截断）' if cut else ''}；排除 {', '.join(args.exclude) or '无'}。", "",
             "| 信号 | 会话数 |", "|---|---|"]
    lines += [f"| {label} | {count} |" for label, count in hits.items()]
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        sys.stdout.write(f"\n报告已写入：{args.out.resolve()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
