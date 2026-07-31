# [OMNI] origin=claude-code purpose=experiment ts=2026-04-15
"""实验 B: 开启所有 crystallizer 跑一次 absorption-v3 (hermes-agent-real)."""
import asyncio
import os
import sys

sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")
os.environ["OMNICOMPANY_INFO_AUDIT"] = "piggyback"
os.environ["OMNICOMPANY_CRYSTALLIZE"] = "trace,format,description"

from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env", override=True)
os.environ["OMNICOMPANY_INFO_AUDIT"] = "piggyback"
os.environ["OMNICOMPANY_CRYSTALLIZE"] = "trace,format,description"

from omnicompany.core.registry import discover
discover()
from omnicompany.core.dispatch import dispatch

SELF_PORTRAIT = """
## Omnicompany 自画像 — 已知缺口（G1-G7）

**G1 工具层鲁棒性** - Tool 层无统一重试/超时/并发/降级机制
**G2 向外学习** - 无从"执行经验"中自动蒸馏规则的机制
**G3 自扩展加速** - 新产线生产仍重度依赖人工
**G4 运行成果统计** - 无语义聚合的执行历史查询能力
**G5 分布式知识库管理** - OmniKB 内容稀疏
**G6 全流程自主优化** - 诊断能发现问题，但触发修复仍需人工
**G7 对外接口** - 无统一外部调用入口
""".strip()

INPUT = {
    "repo_name": "hermes-agent",
    "repo_local_path": "e:/WindowsWorkspace/参考项目/hermes-agent-real",
    "self_portrait": SELF_PORTRAIT,
}


async def main():
    print("=== 实验 B: crystallize ON (trace,format,description) ===\n")
    result = await dispatch("absorption-v3", INPUT)
    out = result.output if hasattr(result, "output") else result
    print(f"\nfindings: {len(out.get('findings') or [])}")
    print(f"iteration: {out.get('iteration', '?')}")

    from omnicompany.runtime.agent_crystallize.pending_queue import list_pending_patches
    patches = list_pending_patches()
    print(f"\n=== crystallize pending patches: {len(patches)} ===")
    for p in patches:
        try:
            print(f"\n-- {p.name} --")
            content = p.read_text(encoding="utf-8")
            print(content[:1200])
        except Exception:
            print(f"  (cannot read {p})")


asyncio.run(main())
