# [OMNI] origin=claude-code purpose=stage3-fanin-smoke ts=2026-04-18
"""Stage 3 fan-in 冒烟测试：验证 7 节点拓扑 + composite Format 合并。

用 2026-04-18 前一轮 hermes-agent absorption-v3 跑出的 report.md 作为入口。
观察：
- entry_bootstrap fan-out 3 路
- capability_loader + gap_loader 产出非空
- spec_parser 消费 composite 输入（3 个 format_id 作 key）
- pending_proposals.md 落盘，内容含 gap_id 正确映射
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")
try:
    from dotenv import load_dotenv
    load_dotenv("e:/WindowsWorkspace/omnicompany/.env", override=True)
except ImportError:
    pass

from omnicompany.core.registry import discover
discover()
from omnicompany.core.dispatch import dispatch


REPO = "hermes-agent"
REPORT_PATH = Path(f"e:/WindowsWorkspace/omnicompany/data/domains/absorption/{REPO}/report.md")


async def main() -> int:
    if not REPORT_PATH.exists():
        print(f"[FAIL] {REPORT_PATH} 不存在；先跑 absorption-v3 产出 report.md")
        return 1

    report_md = REPORT_PATH.read_text(encoding="utf-8")
    print(f"[INFO] 加载 report.md 字数: {len(report_md)}")

    # 构造 absorption.report.v3 入口 payload
    initial = {
        "repo_name": REPO,
        "report_path": str(REPORT_PATH),
        "report_md": report_md,
        "iteration": 1,
        "structured": {
            "proposals": [],  # 空，强制 SpecParser 走 LLM 路径（测试 wiki fan-in 的效果）
            "highlights": [],
        },
        "feedback_incorporated": [],
    }

    print("\n=== Stage 3 fan-in 冒烟测试 ===")
    try:
        result = await dispatch("absorption-workflow-modifier", initial)
    except Exception as e:
        print(f"[FAIL] dispatch 异常: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        return 2

    print("\n=== 结果 ===")
    if isinstance(result, dict):
        print(f"total_count: {result.get('total_count', '?')}")
        print(f"p0_count: {result.get('p0_count', '?')}")
        print(f"pending_review_path: {result.get('pending_review_path', '?')}")
        proposals = result.get("proposals") or []
        print(f"\n--- Top proposals ---")
        for p in proposals[:10]:
            pid = p.get("proposal_id", "?")
            title = p.get("title", "?")
            status = p.get("omnicompany_status", "?")
            gap = p.get("source", {}).get("gap_id", "?")
            print(f"  {pid} | {status} | gap={gap} | {title}")
    else:
        print(f"result type: {type(result).__name__}")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
