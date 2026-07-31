# [OMNI] origin=claude-code purpose=dispute-test ts=2026-04-15
"""测试 ProposalDisputeLoop：构造一个真实异议，看 agent 是否能修订提案。"""
import asyncio, sys
from pathlib import Path

sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")
from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env")

from omnicompany.packages.services.absorption.routers.proposal_dispute_loop import (
    ProposalDisputeLoopRouter,
)

# 当前 pending_proposals 和 report
REPO_DIR = Path("e:/WindowsWorkspace/omnicompany/data/domains/absorption/hermes-agent")
pending = (REPO_DIR / "pending_proposals.md").read_text(encoding="utf-8")
report = (REPO_DIR / "report.md").read_text(encoding="utf-8")

# 模拟人类异议（基于我独立调研里发现管线漏掉的东西）
dispute = """# dispute_feedback — 2026-04-15

我审阅了 pending_proposals.md，发现几个严重问题：

## 1. 遗漏了 hermes-agent 最有价值的"自学习闭环"

当前 7 条提案里完全没提 hermes 的自学习机制：
- trajectory 执行轨迹记录（agent/trajectory.py）
- insights 会话分析引擎（agent/insights.py）
- skill_manager_tool agent 自建 skill（tools/skill_manager_tool.py）

这三个文件构成 hermes 完整的"执行→记录→分析→沉淀→复用"闭环，
跟 Omnicompany 的 crystallize 主轴直接对标。
必须加成 P0 提案。

## 2. 遗漏了"子 agent 委托"架构

tools/delegate_tool.py 实现了 parent→child agent 的委托 + 并发 + 工具继承限制。
Omnicompany 现在是单线 pipeline，没有委托机制。这是架构级新能力。
应加为 P0 提案（architectural_pattern）。

## 3. PRO-002 可插拔记忆提供者 太泛

hermes 有 8 种具体的 memory provider 实现（byterover / hindsight / holographic / honcho /
mem0 / openviking / retaindb / supermemory）。提案应说明：
- 不只是 ABC，还有 12 个生命周期钩子（prefetch / sync_turn / on_pre_compress / on_session_end 等）
- holographic memory 的向量符号架构（HRR）值得单独列一条

## 4. PRO-001 统一错误分类 相对 Omnicompany 已有能力评价不准

Omnicompany 的 LLMClient 已经有 RateLimiter + 指数退避。hermes 的真正独特之处是
agent/error_classifier.py 把错误分类到"恢复策略 flag"（rotate_key / backoff / compress），
不是基础重试。status 应为"已有可改进"而非"缺失"。

请主动去 hermes 的 agent/ 和 tools/ 目录查证上述几点，重新修订 proposals。
"""


async def main():
    input_data = {
        "repo_name": "hermes-agent",
        "repo_local_path": "e:/WindowsWorkspace/参考项目/hermes-agent-real",
        "pending_proposals": pending,
        "dispute_feedback": dispute,
        "report_md": report,
    }

    print("=== ProposalDisputeLoop 测试 ===")
    print(f"  原 pending: {len(pending)} chars")
    print(f"  dispute:    {len(dispute)} chars")
    print(f"  report:     {len(report)} chars")
    print()

    router = ProposalDisputeLoopRouter()
    result = await router.run(input_data)

    print()
    print(f"=== 结果 ===")
    print(f"verdict: {result.kind.value}")
    print(f"diagnosis: {result.diagnosis}")
    revised = result.output.get("revised_proposals", [])
    print(f"revised proposals: {len(revised)}")
    for p in revised:
        chg = p.get("change_from_previous", "?")
        pri = p.get("priority", "?")
        st = p.get("omnicompany_status", "?")
        print(f"  [{pri}] [{chg:10s}] [{st}] {p.get('title','?')}")
    print()
    print(f"revision_summary: {result.output.get('revision_summary','')[:300]}")
    print()
    print(f"written to: {result.output.get('revised_proposals_path','')}")


asyncio.run(main())
