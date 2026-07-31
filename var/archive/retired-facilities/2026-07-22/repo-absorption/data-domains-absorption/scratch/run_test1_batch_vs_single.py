# [OMNI] origin=claude-code purpose=test1 ts=2026-04-15
"""Test 1: LearningExtractor 分批(按 gap_id) vs 一次(当前)。

用缓存的 46 个模块（从 audit_store 提取），不需要重跑 ModuleExplorer。
对比：findings 数量、关键文件覆盖率、路径编造率。
"""
import json, sys, os, time, re
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")
from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env")

REPO = Path("e:/WindowsWorkspace/参考项目/hermes-agent-real")
MODULES = json.loads(Path("e:/WindowsWorkspace/omnicompany/data/domains/absorption/hermes-agent/cached_modules.json").read_text(encoding="utf-8"))

SELF_PORTRAIT = """
G1 工具层鲁棒性 - Tool 层无统一重试/超时/并发/降级机制
G2 向外学习 - 无从"执行经验"中自动蒸馏规则的机制
G3 自扩展加速 - 新产线生产仍重度依赖人工
G4 运行成果统计 - 无语义聚合的执行历史查询能力
G5 分布式知识库管理 - OmniKB 内容稀疏
G6 全流程自主优化 - 诊断能发现问题，但触发修复仍需人工
G7 对外接口 - 无统一外部调用入口
""".strip()

# 加载模块内容（从真实文件）
def load_module_content(m: dict) -> str:
    path = m.get("path", "")
    full = REPO / path
    if full.exists():
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[:300])
    return f"(file not found: {path})"


def run_learning_extractor_call(module_readings: list, label: str, max_findings: int = 50) -> dict:
    """单次 LLM 调用做 learning extraction。"""
    from omnicompany.runtime.llm.llm import LLMClient

    system = f"""你是 Omnicompany 的学习分析师。你会收到若干外部 repo 模块的实际代码。

你的任务：对每个模块，判断 Omnicompany 能从中学到什么。

**what_it_does**：基于代码描述实际实现了什么。
**omnicompany_delta**：Omnicompany 当前缺少什么。
**action**：Omnicompany 应该怎么做——功能层级，不指定具体文件路径。
**portability**：directly_reusable / worth_learning / reference_only

输出纯 JSON：
{{"repo_name": "hermes-agent", "findings": [
  {{"gap_id": "G1", "priority": "P0", "title": "≤20字",
    "what_it_does": "...", "omnicompany_delta": "...", "action": "...",
    "portability": "...",
    "evidence": [{{"file": "真实路径（不要改写）", "lines": "...", "quote": "≤80字"}}]
  }}
], "overall_assessment": {{"absorption_value": "high|medium|low", "summary": "..."}} }}

findings 按 P0→P1→P2 排序，最多 {max_findings} 条（只写有证据的）。
保留原始文件路径，不要改写或推理路径。"""

    module_sections = []
    for r in module_readings:
        path = r.get("path", "?")
        gap = r.get("gap_id", "?")
        pri = r.get("priority", "P2")
        content = load_module_content(r)
        module_sections.append(f"### [{pri}] {gap} — `{path}`\n\n{content}")

    user_msg = f"""# 学习提炼任务

**Repo**: hermes-agent
**模块数量**: {len(module_readings)} 个

## Omnicompany 自画像（G1-G7 缺口）
{SELF_PORTRAIT}

---

## 模块代码

{"---".join(module_sections)}

---

请对每个模块判断 Omnicompany 能学到什么，输出 JSON。"""

    client = LLMClient(model="qwen3.6-plus", role="runtime_main", max_tokens=8192)
    t0 = time.time()
    resp = client.call(
        messages=[{"role": "user", "content": user_msg}],
        system=system,
        info_audit=False,
        caller="test1.learning_extractor",
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
        stripped = raw.lstrip()
        start = stripped.find("{")
        if start >= 0:
            data, _ = json.JSONDecoder().raw_decode(stripped[start:])
        else:
            data = json.loads(stripped)
    except Exception as e:
        return {"label": label, "error": str(e), "raw": raw[:500], "elapsed": elapsed}

    findings = data.get("findings") or []

    # 检查路径保真
    real_paths = 0; fake_paths = 0
    for f in findings:
        for ev in f.get("evidence") or []:
            p = ev.get("file", "")
            if (REPO / p).exists():
                real_paths += 1
            elif p:
                fake_paths += 1

    return {
        "label": label,
        "n_findings": len(findings),
        "n_modules_input": len(module_readings),
        "real_paths": real_paths,
        "fake_paths": fake_paths,
        "elapsed": round(elapsed, 1),
        "findings": findings,
        "titles": [f.get("title", "?") for f in findings],
        "covered_files": list(set(
            ev.get("file", "")
            for f in findings for ev in (f.get("evidence") or [])
        )),
    }


def main():
    print("=== Test 1: 分批 vs 一次 ===\n")

    # ── Baseline: 当前方式（全部模块一次 LLM 调用，hardcap 10）──
    print(f"[baseline] {len(MODULES)} 模块, hardcap=10...", flush=True)
    baseline = run_learning_extractor_call(MODULES, "baseline", max_findings=10)
    if "error" in baseline:
        print(f"  ERROR: {baseline['error']}")
    else:
        print(f"  findings: {baseline['n_findings']}, real_paths: {baseline['real_paths']}, fake: {baseline['fake_paths']}, time: {baseline['elapsed']}s")
        for t in baseline["titles"]:
            print(f"    - {t}")

    # ── 实验: 按 gap_id 分组，每组独立调用，无 hardcap ──
    by_gap = defaultdict(list)
    for m in MODULES:
        by_gap[m.get("gap_id", "?")].append(m)

    all_findings = []
    all_real = 0; all_fake = 0; total_time = 0
    for gap_id in sorted(by_gap):
        modules = by_gap[gap_id]
        print(f"\n[batch {gap_id}] {len(modules)} 模块...", flush=True)
        result = run_learning_extractor_call(modules, f"batch_{gap_id}", max_findings=50)
        if "error" in result:
            print(f"  ERROR: {result['error']}")
            continue
        print(f"  findings: {result['n_findings']}, real: {result['real_paths']}, fake: {result['fake_paths']}, time: {result['elapsed']}s")
        all_findings.extend(result["findings"])
        all_real += result["real_paths"]
        all_fake += result["fake_paths"]
        total_time += result["elapsed"]

    # ── 对比 ──
    print("\n" + "=" * 60)
    print("对比结果:")
    print(f"  baseline (一次):   {baseline.get('n_findings', '?')} findings, "
          f"real={baseline.get('real_paths',0)} fake={baseline.get('fake_paths',0)}, "
          f"time={baseline.get('elapsed','?')}s")
    print(f"  batched  (分 {len(by_gap)} 组): {len(all_findings)} findings, "
          f"real={all_real} fake={all_fake}, time={total_time:.1f}s")
    print()

    # 覆盖关键文件？
    key_files = [
        "agent/insights.py", "agent/trajectory.py", "tools/delegate_tool.py",
        "tools/skills_hub.py", "agent/prompt_builder.py",
        "plugins/memory/hindsight/__init__.py",
    ]
    baseline_covered = set(baseline.get("covered_files", []))
    batch_covered = set(ev.get("file", "") for f in all_findings for ev in (f.get("evidence") or []))

    print("关键文件覆盖:")
    for kf in key_files:
        b = "Y" if any(kf in c for c in baseline_covered) else "N"
        e = "Y" if any(kf in c for c in batch_covered) else "N"
        print(f"  {kf:45s}  baseline={b}  batched={e}")

    # 保存结果
    out = {
        "baseline": {k: v for k, v in baseline.items() if k != "findings"},
        "batched": {
            "n_findings": len(all_findings),
            "real_paths": all_real,
            "fake_paths": all_fake,
            "elapsed": round(total_time, 1),
            "titles": [f.get("title", "?") for f in all_findings],
        },
        "baseline_titles": baseline.get("titles", []),
        "batched_titles": [f.get("title", "?") for f in all_findings],
    }
    Path("e:/WindowsWorkspace/omnicompany/data/domains/absorption/test1_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果已保存: data/domains/absorption/test1_results.json")


if __name__ == "__main__":
    main()
