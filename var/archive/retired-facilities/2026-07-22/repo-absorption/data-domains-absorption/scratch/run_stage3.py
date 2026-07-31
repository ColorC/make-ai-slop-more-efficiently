"""smoke test: 跑 absorption-v3-stage3 管线（SpecParser + ApprovalGateS3）on hermes-agent"""
import asyncio, sys, json
from pathlib import Path
sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")

from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env", override=True)

from omnicompany.core.registry import discover
discover()
from omnicompany.core.dispatch import dispatch

# 读已有报告（上一轮 V3 管线产出）
REPORT_PATH = Path("e:/WindowsWorkspace/omnicompany/data/domains/absorption/hermes-agent/report.md")
report_md = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else ""

# 构建 absorption.report.v3 格式输入
INPUT = {
    "repo_name": "hermes-agent",
    "report_path": str(REPORT_PATH),
    "report_md": report_md,
    "structured": {},   # SpecParser 会回退到 LLM 解析 report_md
    "iteration": 2,
    "feedback_incorporated": [],
}

async def main():
    print("=== absorption-v3-stage3 smoke test: hermes-agent ===\n")
    print(f"report_md 长度: {len(report_md)} 字节")
    result = await dispatch("absorption-v3-stage3", INPUT)
    out = result.output if hasattr(result, "output") else result

    proposals = out.get("proposals") or []
    approved = out.get("approved_proposals") or []
    rejected = out.get("rejected_proposals") or []
    pending = out.get("pending_proposals") or []
    pending_path = out.get("pending_review_path", "")

    print(f"\n=== 结果 ===")
    print(f"提案总数:   {len(proposals)}")
    print(f"P0 提案:    {out.get('p0_count', '?')}")
    print(f"已批准:     {len(approved)}")
    print(f"已拒绝:     {len(rejected)}")
    print(f"待审批:     {len(pending)}")
    print(f"审批文件:   {pending_path}")

    if proposals:
        print("\n--- 提案清单 ---")
        for p in proposals:
            risk = p.get("risk_level", "?")
            ptype = p.get("type", "?")
            src = p.get("source", {})
            gap = src.get("gap_id", "?")
            pri = src.get("priority", "?")
            print(f"  [{p['proposal_id']}][{pri}][{gap}][{risk}] {p['title']} ({ptype})")

    if approved:
        print(f"\n已自动批准: {approved}")
    if pending:
        print(f"待人工审批: {pending}")
        print(f"→ 写入 {Path(pending_path).parent / 'approved_proposals.txt'} 来批准")

asyncio.run(main())
