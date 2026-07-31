"""阶段 0 基准测试：开 piggyback 跑 absorption-v3，收集各节点 InfoAuditReport"""
import asyncio, sys, os, json
sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")
os.environ["OMNICOMPANY_INFO_AUDIT"] = "piggyback"  # 开启 piggyback 自评

from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env", override=True)
# override=True 可能覆盖我们的设置——在 load_dotenv 之后重新强制设置
os.environ["OMNICOMPANY_INFO_AUDIT"] = "piggyback"
assert os.environ.get("OMNICOMPANY_INFO_AUDIT") == "piggyback", "env var not set!"

from omnicompany.core.registry import discover
discover()
from omnicompany.core.dispatch import dispatch

SELF_PORTRAIT = """
## Omnicompany 自画像 — 已知缺口（G1-G7）

**G1 工具层鲁棒性** - Tool 层无统一重试/超时/并发/降级机制
**G2 向外学习** - 无从"执行经验"中自动蒸馏规则的机制
**G3 自扩展加速** - 新产线生产仍重度依赖人工
**G4 运行成果统计** - 无语义聚合的执行历史查询能力
**G5 分布式知识库管理** - OmniKB 内容稀疏，无会话间记忆可插拔 provider
**G6 全流程自主优化** - 诊断能发现问题，但触发修复仍需人工
**G7 对外接口** - 无统一外部调用入口，无多平台 gateway
""".strip()

INPUT = {
    "repo_name": "hermes-agent",
    "repo_local_path": "e:/WindowsWorkspace/参考项目/hermes-agent-real",
    "self_portrait": SELF_PORTRAIT,
}

async def main():
    print("=== 阶段 0 基准测试 (piggyback ON) ===\n")
    result = await dispatch("absorption-v3", INPUT)
    out = result.output if hasattr(result, "output") else result

    print(f"\n=== 管线结果 ===")
    print(f"findings: {len(out.get('findings') or [])}")
    print(f"iteration: {out.get('iteration', '?')}")

    # 读 audit_store 里的 piggyback 记录
    from omnicompany.runtime.info_audit.audit_store import load_historical_llm_calls
    try:
        audits = load_historical_llm_calls(last_n=50)
        print(f"\n=== Piggyback 审计记录（最近 {len(audits)} 条）===")
        for a in audits:
            node = a.get("node_id", "?")
            audit_data = a.get("info_audit")
            if audit_data:
                suf = audit_data.get("sufficiency", "N/A")
                missing_list = audit_data.get("missing_info", [])
                critical = sum(1 for m in missing_list if m.get("critical"))
                print(f"  [{node}] {suf} | missing={len(missing_list)} (critical={critical})")
                for m in missing_list[:2]:
                    print(f"    {'[!!]' if m.get('critical') else '[  ]'} {str(m.get('description',''))[:70]}")
            else:
                print(f"  [{node}] no audit")
    except Exception as e:
        print(f"audit_store 读取失败: {e}")
        import traceback; traceback.print_exc()

asyncio.run(main())
