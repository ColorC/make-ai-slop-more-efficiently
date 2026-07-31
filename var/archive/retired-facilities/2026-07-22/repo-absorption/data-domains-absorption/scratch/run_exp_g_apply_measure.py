# [OMNI] origin=claude-code purpose=exp-g ts=2026-04-15
"""Exp G: apply-then-measure — 应用 local_list patch 前后对比.

测量: module_explorer 加了 local_list 说明后, 同 repo 上 agent 行为是否改变?
  - total_turns: 轮数 (更少 = 更高效)
  - local_list 调用次数: 不变 (工具能力不变, 只是 DESCRIPTION 更准确)
  - local_read 调用次数: 可能减少 (更早定位目标文件)
  - 最终 findings 数: 不应下降

注意: DESCRIPTION 改变理论上影响的是:
  (1) 未来 probe / post_hoc 对这个节点的判断准确性
  (2) Doctor 对这个节点的健康度评分
  (3) 是否减少 agent 因"不知道自己有 local_list"而少用它

对 (3) 做实验: 若 agent 从 system_prompt 里读 DESCRIPTION (不少节点这样设计)
  → 加了 local_list 说明后它可能更快想到用 local_list
  → 若它本来就知道工具列表则 DESCRIPTION 改动影响不大
"""
import asyncio, json, sys, os, re, time
from collections import Counter

sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")
os.environ["OMNICOMPANY_INFO_AUDIT"] = "piggyback"
os.environ["OMNICOMPANY_CRYSTALLIZE"] = "off"  # 本实验不产 patch

from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env", override=True)

from omnicompany.core.registry import discover
discover()
from omnicompany.core.dispatch import dispatch
from omnicompany.runtime.agent_crystallize.trace import build_agent_loop_trace

SELF_PORTRAIT = """
G1 工具层鲁棒性 - Tool 层无统一重试/超时/并发/降级机制
G2 向外学习 - 无从"执行经验"中自动蒸馏规则的机制
G3 自扩展加速 - 新产线生产仍重度依赖人工
G4 运行成果统计 - 无语义聚合的执行历史查询能力
G5 分布式知识库管理 - OmniKB 内容稀疏
G6 全流程自主优化 - 诊断能发现问题，但触发修复仍需人工
G7 对外接口 - 无统一外部调用入口
""".strip()

INPUT_BASE = {
    "repo_name": "hermes-agent",
    "repo_local_path": "e:/WindowsWorkspace/参考项目/hermes-agent-real",
    "self_portrait": SELF_PORTRAIT,
}

DESC_BEFORE = (
    "V3 模块探索：AgentNodeLoop，读完再选，"
    "local_grep 主动发现 + local_read 确认内容 + submit_module 提交，"
    "符合 F-14 判断信息充分原则"
)
DESC_AFTER = (
    "V3 模块探索（AgentNodeLoop）：基于 repomap 初始指引，"
    "迭代使用 local_list 浏览目录结构，结合 local_grep 模式搜索与 "
    "local_read 内容确认，按需分批 submit_module 提交。"
    "严格遵循 F-14 信息充分原则，覆盖高价值架构与核心逻辑模块。"
)


def _extract_metrics(result, label: str) -> dict:
    """从 absorption 结果 + 最新 trace 里抽 metrics."""
    out = result.output if hasattr(result, "output") else result
    findings = len(out.get("findings") or [])
    iteration = out.get("iteration", 1)

    # 从 audit_store 读最新 trace 里的工具调用计数
    from pathlib import Path
    import json as _json
    day = Path("e:/WindowsWorkspace/omnicompany/data/llm_audit/2026-04-15")
    files = sorted(day.glob("01*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    tool_counts: Counter = Counter()
    total_turns = 0
    if files:
        for line in files[0].read_text(encoding="utf-8").splitlines():
            try:
                r = _json.loads(line)
                c = r.get("caller") or ""
                if "module_explorer" in c:
                    # 每条 LLM 调用 = 1 turn (粗略)
                    if ".turn_" in c and "internal" not in c:
                        total_turns += 1
                    for tc in (r.get("tool_calls") or []):
                        n = tc.get("name", "?")
                        if n and n != "info_audit":
                            tool_counts[n] += 1
            except: pass
    return {
        "label": label,
        "findings": findings,
        "iteration": iteration,
        "total_turns": total_turns,
        "local_list_calls": tool_counts.get("local_list", 0),
        "local_read_calls": tool_counts.get("local_read", 0),
        "local_grep_calls": tool_counts.get("local_grep", 0),
        "submit_module_calls": tool_counts.get("submit_module", 0),
        "total_tool_calls": sum(tool_counts.values()),
        "tool_distribution": dict(tool_counts.most_common(6)),
    }


async def run_one(label: str, desc: str) -> dict:
    # 临时修改 module_explorer 的 DESCRIPTION
    from omnicompany.packages.services.absorption.routers.module_explorer import ModuleExplorerRouter
    old = ModuleExplorerRouter.DESCRIPTION
    ModuleExplorerRouter.DESCRIPTION = desc
    try:
        print(f"\n[Exp G] 运行: {label}", flush=True)
        t0 = time.time()
        result = await dispatch("absorption-v3", dict(INPUT_BASE))
        elapsed = time.time() - t0
        metrics = _extract_metrics(result, label)
        metrics["elapsed_s"] = round(elapsed, 1)
        print(f"  findings={metrics['findings']} turns={metrics['total_turns']} "
              f"local_list={metrics['local_list_calls']} local_read={metrics['local_read_calls']} "
              f"grep={metrics['local_grep_calls']} submit={metrics['submit_module_calls']}")
        return metrics
    except Exception as e:
        print(f"  !! FAIL: {e}")
        return {"label": label, "error": str(e)[:200]}
    finally:
        ModuleExplorerRouter.DESCRIPTION = old


async def main():
    print("=== Exp G: apply-then-measure ===")
    print("BEFORE: 旧 DESCRIPTION (未提 local_list)")
    before = await run_one("BEFORE", DESC_BEFORE)
    print("AFTER:  新 DESCRIPTION (含 local_list + 迭代分批)")
    after = await run_one("AFTER", DESC_AFTER)

    print("\n=== 对比 ===")
    metrics = ["findings", "total_turns", "local_list_calls", "local_read_calls",
               "local_grep_calls", "submit_module_calls"]
    for m in metrics:
        b = before.get(m, "?")
        a = after.get(m, "?")
        if isinstance(b, int) and isinstance(a, int):
            diff = a - b
            flag = "↓" if diff < 0 else ("↑" if diff > 0 else "=")
            print(f"  {m:25s}: {b:4} → {a:4}  {flag}{abs(diff)}")
        else:
            print(f"  {m:25s}: {b} → {a}")

    out = "e:/WindowsWorkspace/omnicompany/data/domains/absorption/exp_g_apply_measure.json"
    import json
    json.dump({"before": before, "after": after}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\nwritten: {out}")


asyncio.run(main())
