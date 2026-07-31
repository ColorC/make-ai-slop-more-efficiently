"""smoke test: 跑 absorption-v3 完整管线（Stage 1 + Stage 2 + wiki 三路 fan-in）on hermes-agent.

2026-04-18 升级：本文件不再携带硬编码 Omnicompany 自画像 —— wiki 三路
（capability_inventory / gap_registry / reception_intents）由管线 fan-in 从
DESIGN.md / docs/gaps/ 动态加载。
"""
import asyncio, sys
sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")

from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env", override=True)

from omnicompany.core.registry import discover
discover()
from omnicompany.core.dispatch import dispatch

INPUT = {
    "repo_name": "hermes-agent",
    "repo_local_path": "e:/WindowsWorkspace/参考项目/hermes-agent-real",
}

async def main():
    print("=== absorption-v3 smoke test: hermes-agent (Stage 1 + Stage 2) ===\n")
    result = await dispatch("absorption-module-driven", INPUT)
    print("\n=== 结果 ===")
    out = result.output if hasattr(result, "output") else result

    # Stage 1 字段
    findings = out.get("findings") or []
    module_readings = out.get("module_readings") or []
    files_read = out.get("files_read") or []
    overall = out.get("overall_assessment") or {}

    # Stage 2 字段
    report_path = out.get("report_path", "")
    iteration = out.get("iteration", "?")
    has_feedback = out.get("has_feedback", False)

    print(f"total_files:     {out.get('total_files', '?')}")
    print(f"files_read:      {len(files_read)}")
    print(f"modules_read:    {len(module_readings)}")
    print(f"findings:        {len(findings)}")
    print(f"absorption_value:{overall.get('absorption_value', '?')}")
    print(f"summary:         {overall.get('summary', '?')[:100] if overall.get('summary') else '?'}")
    print(f"iteration:       {iteration}")
    print(f"report_path:     {report_path}")
    print(f"has_feedback:    {has_feedback}")
    print()

    if module_readings:
        print("--- Modules read ---")
        for m in module_readings:
            judgement = m.get("judgement", "?")
            wiki_ref = m.get("wiki_ref") or m.get("gap_id") or "-"
            print(
                f"  [{m.get('priority')}][{judgement}/{wiki_ref}] "
                f"{m.get('path')} ({m.get('read_method')})"
            )
    print()

    if findings:
        print("--- Top findings ---")
        for f in findings[:8]:
            print(f"  [{f.get('priority')}][{f.get('gap_id')}] {f.get('title')} — {f.get('portability')}")

    if report_path:
        print(f"\n报告已写入: {report_path}")

asyncio.run(main())
