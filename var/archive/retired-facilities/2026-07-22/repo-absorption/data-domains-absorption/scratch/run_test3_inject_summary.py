# [OMNI] origin=claude-code purpose=test3 ts=2026-04-15
"""Test 3: 注入 Omnicompany 实际代码摘要 vs self_portrait 文字。"""
import json, sys, re, time, subprocess
from pathlib import Path

sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")
from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env")

from omnicompany.runtime.llm.llm import LLMClient

report = Path("e:/WindowsWorkspace/omnicompany/data/domains/absorption/hermes-agent/report.md").read_text(encoding="utf-8")

SELF_PORTRAIT_TEXT = (
    "G1 工具鲁棒性-无统一重试 G2 向外学习-无经验蒸馏 G3 自扩展-依赖人工 "
    "G4 成果统计-无语义查询 G5 知识库-稀疏 G6 自优化-诊断能发现但修复靠人 G7 对外接口-无"
)

probe = json.loads(Path("e:/WindowsWorkspace/omnicompany/data/domains/absorption/probe_baseline.json").read_text(encoding="utf-8"))
omni_nodes = list(probe["per_node"].keys())

OMNI_SUMMARY = f"""Omnicompany 实际代码结构:

关键模块:
- runtime/exec/runner.py: PipelineRunner 管线执行器
- runtime/agent/agent_node_loop.py: AgentNodeLoop 多轮 Agent 基类（4 层上下文压缩）
- runtime/llm/llm.py: LLMClient（已有 RateLimiter + 令牌桶 + per-endpoint 限流 + 指数退避重试）
- runtime/info_audit/: probe/post_hoc/piggyback/audit_store 信息审计体系
- runtime/agent_crystallize/: agent 经验沉淀（TraceSummarizer/FormatEdgeInferrer/DescriptionRefiner）
- protocol/format.py: Format 注册表（支持 parent/components 复合关系）
- packages/services/doctor/: 管线级健康诊断
- packages/services/guardian/: Guardian 规则引擎（OMNI-001~030）

已有能力（不需要重复学）:
- LLM 重试 + RateLimiter + 令牌桶（llm.py）
- AgentNodeLoop 4 层压缩（microcompact/truncation/sliding_window/auto_compact）
- 信息充分性机制（probe/piggyback tool/post_hoc/crystallize）
- Format 编译期验证（FormatRegistry parent/components 循环检测）
- Guardian 30 条规则自动扫描

缺失能力:
- 无可插拔记忆架构（audit_store 是 jsonl，无向量检索）
- 无 agent 委托/子 agent 机制
- 无多模型投票/ensemble
- 无 agent 自建 skill 能力
- 无自动文件系统检查点
- prompt 各 Router 各写各的，无统一组装层

absorption 管线 9 节点: {omni_nodes}
"""

SPEC_SYSTEM = (
    "你是 Omnicompany 改进提案分析师。\n"
    "你会收到一份外部 repo 的吸纳报告和 Omnicompany 的实际信息。\n"
    "生成改进提案列表。\n\n"
    "提案停在功能层级（不要指定具体文件路径）。\n"
    "检查 Omnicompany 已有能力，不要重复提案。\n"
    "如果 hermes 的能力 Omnicompany 已有，标注 '已有可改进' 而非 '需要新建'。\n\n"
    "输出纯 JSON 数组:\n"
    '[{"title": "20字以内", "rationale": "为什么值得学", '
    '"omnicompany_status": "缺失|部分存在|已有可改进", '
    '"hermes_reference": "hermes 中的关键文件", "priority": "P0|P1|P2"}]'
)


def run_spec(label, context):
    client = LLMClient(model="qwen3.6-plus", role="runtime_main", max_tokens=4096)
    t0 = time.time()
    resp = client.call(
        messages=[{"role": "user", "content":
                   f"## 吸纳报告\n\n{report[:8000]}\n\n## Omnicompany 信息\n\n{context}\n\n请生成提案。"}],
        system=SPEC_SYSTEM, info_audit=False, caller="test3.spec",
    )
    elapsed = time.time() - t0
    raw = ""
    for b in getattr(resp, "content", []) or []:
        if getattr(b, "type", "") == "text":
            raw = getattr(b, "text", "") or ""
            break
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw.strip())
    try:
        data = json.loads(raw)
    except Exception:
        try:
            start = raw.find("[")
            if start >= 0:
                data, _ = json.JSONDecoder().raw_decode(raw[start:])
            else:
                data = []
        except Exception:
            data = []
    if not isinstance(data, list):
        data = []

    print(f"\n[{label}] {len(data)} proposals, {elapsed:.0f}s")
    for p in data:
        status = p.get("omnicompany_status", "?")
        print(f"  [{p.get('priority','?')}] {status:16s} {p.get('title','?')[:45]}")
    return data


def main():
    print("=== Test 3: self_portrait vs Omnicompany 代码摘要 ===")
    baseline = run_spec("baseline (self_portrait)", SELF_PORTRAIT_TEXT)
    enhanced = run_spec("enhanced (code summary)", OMNI_SUMMARY)

    print("\n=== 对比 ===")
    print(f"baseline: {len(baseline)} proposals")
    print(f"enhanced: {len(enhanced)} proposals")
    b_new = sum(1 for p in baseline if "缺" in str(p.get("omnicompany_status", "")))
    e_new = sum(1 for p in enhanced if "缺" in str(p.get("omnicompany_status", "")))
    e_exists = sum(1 for p in enhanced
                   if "已有" in str(p.get("omnicompany_status", ""))
                   or "部分" in str(p.get("omnicompany_status", "")))
    print(f"baseline 标为'缺失': {b_new}")
    print(f"enhanced 标为'缺失': {e_new}")
    print(f"enhanced 标为'已有/部分': {e_exists} (baseline 无法判断)")

    out = {"baseline": baseline, "enhanced": enhanced}
    Path("e:/WindowsWorkspace/omnicompany/data/domains/absorption/test3_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n结果已保存: data/domains/absorption/test3_results.json")


if __name__ == "__main__":
    main()
