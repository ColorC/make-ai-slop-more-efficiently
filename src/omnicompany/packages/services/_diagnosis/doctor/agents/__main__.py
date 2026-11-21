# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/agents ts=2026-05-07T16:00:00Z type=router status=active agent=ai-ide
# [OMNI] summary="V11 CLI 入口 — 让用户跑 'python -m omnicompany.packages.services._diagnosis.doctor.agents' 调 run_challenge_pipeline. 不写 Python 脚本一句命令跑通"
# [OMNI] why="V9 立 helper 后真用户调用门槛仍高 (要写 asyncio.run 代码). V11 加 CLI 入口让命令行调用 — schema §三步骤 1-4 自动化最后一公里"
# [OMNI] tags=cli,doctor,challenge-pipeline,V11
# [OMNI] material_id="material:diagnosis.doctor.agents.__main__.cli_run_challenge_pipeline.py"
"""V11 CLI 入口 · run_challenge_pipeline 命令行包装.

用法:
    # 默认 dry-run 看排序 (无 LLM, 安全):
    python -m omnicompany.packages.services._diagnosis.doctor.agents \\
      --hypotheses-dir data/services/doctor/hypotheses \\
      --applies-to worker \\
      --focus-count 5 \\
      --dry-run

    # 真跑 ChallengeAgent (涉 LLM token):
    python -m omnicompany.packages.services._diagnosis.doctor.agents \\
      --hypotheses-dir data/services/doctor/hypotheses \\
      --applies-to worker \\
      --focus-count 1 \\
      --no-dry-run

输出:
    summary 一句话 + ranked 列表 (top N 假设 + score + reasons) + (非 dry-run) agent_runs
    list (每条 hypothesis_id + events_count + 可选 error)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="omnicompany.packages.services._diagnosis.doctor.agents",
        description="V11 CLI: 跑 ChallengeQueue 排序 → top N → ChallengeDiagnosticAgent 一条龙",
    )
    p.add_argument("--hypotheses-dir", default="data/services/doctor/hypotheses",
                   help="假设 yaml 目录 (相对项目根, 默认 data/services/doctor/hypotheses)")
    p.add_argument("--applies-to", default="",
                   help="问题对象 (worker/material/team/agent/...) 触发 b 类优先. 空时不触发")
    p.add_argument("--focus-count", type=int, default=1,
                   help="跑前 N 条 (默认 1, token 友好)")
    p.add_argument("--depended-by-threshold", type=int, default=3,
                   help="c 类阈值 (默认 3, 按 schema §三步骤 2.c)")
    p.add_argument("--include-frozen", action="store_true",
                   help="含 falsified/real_world_validated 假设 (默认跳, V7 一致)")
    dry_grp = p.add_mutually_exclusive_group()
    dry_grp.add_argument("--dry-run", action="store_true", default=True,
                         help="只 ranked 不调 agent (默认 — 安全, 无 LLM 调用)")
    dry_grp.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                         help="真调 ChallengeAgent (涉 LLM token)")
    p.add_argument("--output-json", default=None,
                   help="把完整 result 写进 json 文件 (路径相对当前目录)")
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI 入口. 返 0 = 成功 / 1 = hypotheses-dir 错 / 2 = pipeline 失败."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # local import 避免 import time 加载 dispatcher
    from omnicompany.packages.services._diagnosis.doctor.agents import (
        run_challenge_pipeline,
    )

    skip_frozen = not args.include_frozen

    try:
        result = asyncio.run(run_challenge_pipeline(
            hypotheses_dir=args.hypotheses_dir,
            applies_to=args.applies_to,
            focus_count=args.focus_count,
            skip_frozen=skip_frozen,
            depended_by_threshold=args.depended_by_threshold,
            dry_run=args.dry_run,
        ))
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"ERROR pipeline 跑挂: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    # 简短 stdout 摘要
    print(result["summary"])
    print()
    print(f"{'hypothesis_id':<30} {'score':>6} {'reasons':<60}")
    print("-" * 100)
    for entry in result["ranked"]:
        reasons_short = " / ".join(r.split(":")[0] for r in entry["reasons"]) or "(no boost)"
        print(f"{entry['hypothesis_id']:<30} {entry['priority_score']:>6} {reasons_short[:60]}")

    # 非 dry-run 时打 agent_runs
    if not args.dry_run and result["agent_runs"]:
        print()
        print(f"{'agent_run hypothesis_id':<30} {'events':>6} {'status':<30}")
        print("-" * 80)
        for run in result["agent_runs"]:
            status = run.get("error", "OK")[:30]
            print(f"{run['hypothesis_id']:<30} {run['events_count']:>6} {status}")

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # hypothesis_dict 可能含复杂结构, 强行 json 化时去掉 (太大且 yaml 已存)
        slim = {
            "summary": result["summary"],
            "ranked": [
                {k: v for k, v in entry.items() if k != "hypothesis_dict"}
                for entry in result["ranked"]
            ],
            "agent_runs": result["agent_runs"],
        }
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, indent=2)
        print(f"\n落档 json: {out_path}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
