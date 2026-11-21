# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/agents ts=2026-05-07T22:00:00Z type=router status=active agent=ai-ide
# [OMNI] summary="V20 CLI 入口 — HypothesisDiagnosticAgent 命令行 (拿假设 + target 真诊断). 修 V19/V20 真用 dogfood 时每次写 _scratch script 的真问题"
# [OMNI] why="V19+V20 真大规模实测假设系统时, run_hypothesis_diagnosis 现只 helper, 用户要写 asyncio.run 代码. V20 立 CLI 让一句命令跑通"
# [OMNI] tags=cli,doctor,hypothesis-diagnostic,V20
# [OMNI] material_id="material:diagnosis.doctor.agents.cli_hypothesis_diagnose.py"
"""V20 CLI · HypothesisDiagnosticAgent 命令行包装.

用法:
    # 单条假设 × 单 target:
    python -m omnicompany.packages.services._diagnosis.doctor.agents.cli_hypothesis_diagnose \\
      --target src/omnicompany/packages/services/_utility/csv_to_md/team.py \\
      --target-kind team \\
      --hypothesis-yaml data/services/doctor/hypotheses/H-2026-05-06-034.yaml

    # 多假设:
    python -m omnicompany....cli_hypothesis_diagnose \\
      --target <path> --target-kind team \\
      --hypothesis-yaml H-034.yaml H-035.yaml H-036.yaml

    # 输出 json:
    python -m omnicompany....cli_hypothesis_diagnose ... --output-json _scratch/result.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="omnicompany.packages.services._diagnosis.doctor.agents.cli_hypothesis_diagnose",
        description="V20 CLI: HypothesisDiagnosticAgent 拿假设 + target 真诊断",
    )
    p.add_argument("--target", required=True,
                   help="待诊断对象路径 (相对项目根, 例 'src/.../team.py')")
    p.add_argument("--target-kind", required=True,
                   choices=["worker", "material", "team", "agent", "hook", "tool", "plan"],
                   help="对象类型")
    p.add_argument("--hypothesis-yaml", required=True, nargs="+",
                   help="一条或多条假设 yaml 路径 (相对项目根)")
    p.add_argument("--output-json", default=None,
                   help="把 verdict 写 json 文件 (路径相对当前目录)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    from omnicompany.packages.services._diagnosis.doctor.agents import run_hypothesis_diagnosis

    try:
        events = asyncio.run(run_hypothesis_diagnosis(
            target_entity_path=args.target,
            target_entity_kind=args.target_kind,
            applicable_hypothesis_paths=args.hypothesis_yaml,
        ))
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    # 抽 verdict + findings
    verdict = None
    findings: list = []
    narrative = ""
    for ev in events:
        if "verdict" in getattr(ev, "event_type", ""):
            payload = getattr(ev, "payload", None)
            if isinstance(payload, dict):
                verdict = payload
                findings = payload.get("findings", [])
                narrative = payload.get("narrative", "") or ""

    print(f"events: {len(events)}")
    print(f"findings: {len(findings)}")
    print(f"narrative ({len(narrative)} chars): {narrative[:300]}")

    if findings:
        print()
        print(f"{'idx':<4} {'finding_kind':<14} {'applied_hypotheses':<35} evidence")
        print("-" * 100)
        for i, f in enumerate(findings):
            if not isinstance(f, dict):
                continue
            kind = f.get("finding_kind", "?")
            applied = ",".join(f.get("applied_hypotheses") or [])[:33]
            ev_str = (f.get("evidence") or "").replace("\n", " ")[:60]
            print(f"{i:<4} {kind:<14} {applied:<35} {ev_str}")

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        slim = {
            "target": args.target,
            "target_kind": args.target_kind,
            "hypothesis_yaml": args.hypothesis_yaml,
            "events_count": len(events),
            "findings_count": len(findings),
            "narrative": narrative,
            "findings": findings,  # 完整含 evidence/commentary/concern/applied_*
        }
        with out.open("w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n落档 json: {out}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
