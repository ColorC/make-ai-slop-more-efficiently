# [OMNI] origin=claude-code purpose=experiment ts=2026-04-15
"""实验 C: probe 启动期 baseline — absorption-v3 每个 LLM/SOFT 节点独立 probe."""
import os
import sys

sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")

from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env", override=True)

from omnicompany.core.registry import discover
discover()

from omnicompany.packages.services.absorption.pipeline import build_v3_pipeline
from omnicompany.runtime.info_audit.startup_baseline import run_pipeline_probe_baseline


def main():
    pipeline = build_v3_pipeline()
    result = run_pipeline_probe_baseline(
        pipeline,
        output_dir="e:/WindowsWorkspace/omnicompany/data/domains/absorption",
        include_kinds=("ANCHOR", "LLM", "SOFT"),  # absorption 全用 ANCHOR
    )
    print("=== probe 启动期 baseline (absorption-v3) ===")
    print(f"nodes probed: {result['summary']['total']}")
    print(f"summary: {result['summary']}")
    print()
    for node, r in result["per_node"].items():
        suff = r["sufficiency"]
        print(f"  [{suff:12s} conf={r['confidence_self']:.2f} kind={r.get('kind','?')}] {node}")
        for m in (r.get("missing_info") or [])[:2]:
            crit = "[!!]" if m.get("critical") else "[  ]"
            print(f"     {crit} {(m.get('description') or '')[:90]}")


if __name__ == "__main__":
    main()
