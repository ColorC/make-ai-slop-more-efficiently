# [OMNI] origin=claude-code domain=evolution/workflow ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.command_line_interface.py"
"""进化工作流 CLI 入口

用法示例：

  # 对一个已有的 failing trace 做浅层追踪
  python -m omnicompany.packages.services._core.evolution.workflow.cli shallow-trace \
      --trace-id <trace_id> \
      --pipeline-id <pipeline_id> \
      --bus data/events.db \
      --verdict "输出缺少具体工具调用步骤"

  # 完整进化流程（B.1→B.5）
  python -m omnicompany.packages.services._core.evolution.workflow.cli evolve \
      --trace-id <trace_id> \
      --pipeline-id <pipeline_id> \
      --bus data/events.db \
      --verdict "翻译结果需要审查"

  # 从已有黑板继续进化
  python -m omnicompany.packages.services._core.evolution.workflow.cli evolve \
      --board-id <board_id> \
      --store evolution_boards.db

  # 查看所有活跃黑板
  python -m omnicompany.packages.services._core.evolution.workflow.cli list-boards \
      --store evolution_boards.db

  # 查看单个黑板
  python -m omnicompany.packages.services._core.evolution.workflow.cli show-board \
      --board-id <board_id> \
      --store evolution_boards.db
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from omnicompany.packages.services._core.evolution.workflow.hypothesis_store import HypothesisBoardStore
from omnicompany.packages.services._core.evolution.workflow.orchestrator import EvolutionOrchestrator
from omnicompany.packages.services._core.evolution.workflow.pain_signal import QualityPainSignal
from omnicompany.packages.services._core.evolution.workflow.shallow_tracer import ShallowTracer


# ── 命令实现 ──


async def cmd_shallow_trace(args: argparse.Namespace) -> None:
    store_path = args.store or "evolution_boards.db"
    bus_path = args.bus  # Move 8: None → unified data/events.db (engine resolves)

    store = HypothesisBoardStore(store_path)
    tracer = ShallowTracer(store=store, bus_path=bus_path)

    pain = QualityPainSignal(
        trace_id=args.trace_id,
        pipeline_id=args.pipeline_id,
        failing_node_id=args.failing_node or "unknown",
        quality_verdict=args.verdict or "质量不达标（未指定具体原因）",
        expected_format=args.expected_format or "",
        actual_output_summary="",
        severity=args.severity or "soft",
        bus_path=bus_path,
    )

    print(f"\n[浅层追踪] trace={args.trace_id} pipeline={args.pipeline_id}")
    print(f"          疼痛描述: {pain.quality_verdict}")
    print(f"          总线: {bus_path}")
    print()

    board = await tracer.run(pain)

    print("─" * 60)
    print(f"黑板 ID: {board.board_id}")
    print(f"状态: {board.status}")
    print(f"假设数量: {len(board.hypotheses)}")
    print()

    if not board.hypotheses:
        print("⚠️  未找到候选假设节点。可能原因：")
        print("   - trace_id 不存在或总线文件路径错误")
        print("   - 所有节点输出均符合 Format 语义（问题在 Format 定义本身）")
        print(f"   - 升级原因: {board.escalation_reason}")
        return

    for i, h in enumerate(board.hypotheses, 1):
        print(f"假设 {i}: [{h.status.value}] confidence={h.confidence:.2f}")
        print(f"  节点: {h.suspect_node}")
        print(f"  陈述: {h.statement}")
        print(f"  可证伪性测试: {h.falsification_test}")
        print()

    print(f"黑板已保存到: {store_path}")
    print(f"  board_id = {board.board_id}")
    print()
    print("下一步：运行 show-board 查看详情，或手动进行深度诊断")


def cmd_list_boards(args: argparse.Namespace) -> None:
    store_path = args.store or "evolution_boards.db"
    store = HypothesisBoardStore(store_path)

    pipeline_id = getattr(args, "pipeline_id", None)
    boards = store.list_active(pipeline_id)

    if not boards:
        print("没有活跃的黑板。")
        return

    print(f"\n活跃黑板（共 {len(boards)} 个）:")
    print("─" * 60)
    for b in boards:
        active_count = len(b.active_hypotheses())
        print(f"  {b.board_id[:8]}...  pipeline={b.pipeline_id}  "
              f"status={b.status}  假设={len(b.hypotheses)}({active_count}活跃)  "
              f"trace={b.trace_id[:12]}...")


async def cmd_evolve(args: argparse.Namespace) -> None:
    store_path = args.store or "evolution_boards.db"
    bus_path = args.bus  # Move 8: None → unified data/events.db (engine resolves)
    store = HypothesisBoardStore(store_path)

    orch = EvolutionOrchestrator(
        store=store,
        bus_path=bus_path,
        max_cycles=args.max_cycles or 5,
    )

    if args.board_id:
        print(f"\n[进化] 继续已有黑板: {args.board_id}")
        result = await orch.continue_from_board(args.board_id)
    else:
        pain = QualityPainSignal(
            trace_id=args.trace_id,
            pipeline_id=args.pipeline_id,
            failing_node_id=args.failing_node or "",
            quality_verdict=args.verdict or "质量不达标",
            expected_format=args.expected_format or "",
            actual_output_summary="",
            severity=args.severity or "soft",
            bus_path=bus_path,
        )
        print(f"\n[进化] trace={args.trace_id} pipeline={args.pipeline_id}")
        print(f"       描述: {pain.quality_verdict}")
        result = await orch.run(pain)

    print()
    print("─" * 60)
    print(result.summary())
    print("─" * 60)


def cmd_show_board(args: argparse.Namespace) -> None:
    store_path = args.store or "evolution_boards.db"
    store = HypothesisBoardStore(store_path)

    board = store.load(args.board_id)
    if not board:
        # 尝试按 trace_id 查找
        board = store.load_by_trace(args.board_id)

    if not board:
        print(f"找不到黑板: {args.board_id}")
        sys.exit(1)

    print(f"\n黑板详情: {board.board_id}")
    print(f"  管线: {board.pipeline_id}")
    print(f"  trace: {board.trace_id}")
    print(f"  状态: {board.status}")
    print(f"  疼痛: {board.quality_verdict}")
    print(f"  修改锁定: {board.modification_lock or '(未锁定)'}")
    print(f"  实验次数: {len(board.experiment_log)}")
    print()

    print(f"假设池（共 {len(board.hypotheses)} 条）:")
    print("─" * 60)
    for h in sorted(board.hypotheses, key=lambda x: -x.confidence):
        status_icon = {
            "active": "[A]", "dormant": "[D]",
            "eliminated": "[X]", "confirmed": "[OK]",
        }.get(h.status.value, "[?]")
        print(f"  {status_icon} [{h.status.value}] conf={h.confidence:.2f}  node={h.suspect_node}")
        print(f"     {h.statement}")
        if h.supporting_traces:
            print(f"     支持 trace: {', '.join(h.supporting_traces[:3])}")
        if h.last_experiment_outcome:
            print(f"     最近实验: {h.last_experiment_outcome}")
        print()

    if board.experiment_log:
        print(f"实验记录（共 {len(board.experiment_log)} 条）:")
        print("─" * 60)
        for exp in board.experiment_log:
            print(f"  [{exp.outcome or 'IN_PROGRESS'}] {exp.change_type}: {exp.change_description[:60]}")
            if exp.causal_explanation:
                print(f"    因果解释: {exp.causal_explanation[:80]}")
            if exp.anti_pattern:
                print(f"    反模式: {exp.anti_pattern[:80]}")
        print()


# ── CLI 入口 ──


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="omnicompany-evolve",
        description="OmniCompany 进化工作流命令行",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # shallow-trace
    p_trace = sub.add_parser("shallow-trace", help="对失败 trace 做 B.1 浅层追踪")
    p_trace.add_argument("--trace-id", required=True, help="失败的 trace_id")
    p_trace.add_argument("--pipeline-id", required=True, help="管线 ID")
    p_trace.add_argument("--verdict", help="质量问题描述（自然语言）")
    p_trace.add_argument("--failing-node", help="质检节点 ID（可选）")
    p_trace.add_argument("--expected-format", help="期望 Format ID（可选）")
    p_trace.add_argument("--severity", default="soft", choices=["soft", "hard"])
    p_trace.add_argument("--bus", help="SQLiteBus 文件路径（默认: 引擎层 unified data/events.db）")
    p_trace.add_argument("--store", help="黑板存储路径（默认: evolution_boards.db）")

    # evolve
    p_evolve = sub.add_parser("evolve", help="完整进化流程（B.1→B.5）")
    p_evolve.add_argument("--trace-id", help="失败的 trace_id（新会话必填）")
    p_evolve.add_argument("--board-id", help="已有黑板 ID（继续进化时使用）")
    p_evolve.add_argument("--pipeline-id", help="管线 ID（新会话必填）")
    p_evolve.add_argument("--verdict", help="质量问题描述")
    p_evolve.add_argument("--failing-node", help="质检节点 ID")
    p_evolve.add_argument("--expected-format", help="期望 Format ID")
    p_evolve.add_argument("--severity", default="soft", choices=["soft", "hard"])
    p_evolve.add_argument("--bus", help="SQLiteBus 文件路径")
    p_evolve.add_argument("--store", help="黑板存储路径")
    p_evolve.add_argument("--max-cycles", type=int, default=5, help="最大进化轮次")

    # list-boards
    p_list = sub.add_parser("list-boards", help="列出所有活跃黑板")
    p_list.add_argument("--pipeline-id", help="按管线 ID 过滤")
    p_list.add_argument("--store", help="黑板存储路径")

    # show-board
    p_show = sub.add_parser("show-board", help="查看单个黑板详情")
    p_show.add_argument("board_id", help="board_id 或 trace_id")
    p_show.add_argument("--store", help="黑板存储路径")

    args = parser.parse_args()

    if args.command == "shallow-trace":
        asyncio.run(cmd_shallow_trace(args))
    elif args.command == "evolve":
        asyncio.run(cmd_evolve(args))
    elif args.command == "list-boards":
        cmd_list_boards(args)
    elif args.command == "show-board":
        cmd_show_board(args)


if __name__ == "__main__":
    main()
