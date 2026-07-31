# [OMNI] origin=omnicompany domain=omnicompany/repair ts=2026-04-20T00:00:00Z
# [OMNI] material_id="material:core.repair.cli_runner.entrypoint.py"
"""repair.run — Format 修复 AgentLoop CLI 入口 + build_bindings (Clean Migration 2026-04-20).

用法:
    python -m omnicompany.packages.services._core.repair.run <format_id> [source_root] [--max N]

示例:
    python -m omnicompany.packages.services._core.repair.run bw.code_spec
    python -m omnicompany.packages.services._core.repair.run bw.vision src/omnicompany --max 2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from omnicompany.packages.services._core.omnicompany import Worker

from .workers import FormatRepairAgentLoopWorker


def build_bindings(input_dict: dict | None = None) -> dict[str, Worker]:
    """Team bindings: node_id → Worker 实例。

    repair Team 目前只暴露 1 个对外 pipeline node (format_repair_loop);
    Router 修复子管线 9 Worker 通过 run_router_repair() 辅助函数链式驱动 (见 routers.py),
    当前不进 Team pipeline。
    """
    return {
        "format_repair_loop": FormatRepairAgentLoopWorker(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Format 自动修复 AgentLoop")
    parser.add_argument("format_id", help="待修复的 Format ID，如 bw.code_spec")
    parser.add_argument(
        "source_root",
        nargs="?",
        default="e:/WindowsWorkspace/omnicompany/src/omnicompany",
        help="omnicompany 源码根目录（默认：e:/WindowsWorkspace/omnicompany/src/omnicompany）",
    )
    parser.add_argument("--max", dest="max_iterations", type=int, default=3, help="最大修复迭代次数（默认 3）")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

    loop = FormatRepairAgentLoopWorker()
    result = loop.run({
        "format_id": args.format_id,
        "source_root": args.source_root,
        "max_iterations": args.max_iterations,
    })

    report = result.output if hasattr(result, "output") else result
    print(json.dumps(report, ensure_ascii=False, indent=2))

    grade = report.get("final_grade", "?")
    success = report.get("success", False)
    iters = len(report.get("iterations", []))
    init = report.get("initial_grade", "?")
    print(f"\n{'✓' if success else '✗'} {args.format_id}: {init} → {grade} ({iters} 轮迭代)")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
