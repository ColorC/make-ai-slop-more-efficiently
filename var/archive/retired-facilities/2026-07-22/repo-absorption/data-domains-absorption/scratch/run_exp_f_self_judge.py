# [OMNI] origin=claude-code purpose=exp-f ts=2026-04-15
"""Exp F: patch self-judge 校准.

给 DescriptionRefiner 的 LLM judge 喂 5 条质量各异的 patch 候选，
让 LLM 自评，与人类直觉对比，验证"self-judge 能不能当准入门槛"。

5 条 patch（已知真实质量）:
  P1 明显好:  真实地添加 local_list（实验 B 产出，人类直觉 approve）
  P2 明显坏:  把 DESCRIPTION 改成废话
  P3 边界好:  增加 F-14 原则的引用（信息增益小但不伤害）
  P4 边界坏:  改动措辞但不增加信息（near-synonym 替换）
  P5 幻觉:    声称 agent 使用了不存在的工具 web_search
"""
import json, sys, os, re
sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")
from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env", override=True)

CURRENT_DESC = (
    "V3 模块探索：AgentNodeLoop，读完再选，"
    "local_grep 主动发现 + local_read 确认内容 + submit_module 提交，"
    "符合 F-14 判断信息充分原则"
)
FORMAT_IN = "absorption.repomap"
FORMAT_OUT = "absorption.module.code"

PATCHES = [
    {
        "id": "P1",
        "human_label": "approve",    # 实验 B 真实产出
        "proposed": (
            "V3 模块探索（AgentNodeLoop）：基于 repomap 初始指引，"
            "迭代使用 local_list 浏览目录结构，结合 local_grep 模式搜索与 "
            "local_read 内容确认，按需分批 submit_module 提交。"
            "严格遵循 F-14 信息充分原则，覆盖高价值架构与核心逻辑模块。"
        ),
        "evidence": "local_list 在 trace 中出现 8 次（最高频工具），当前 DESCRIPTION 未提及"
    },
    {
        "id": "P2",
        "human_label": "reject",
        "proposed": "处理 repomap 并生成模块代码信息",
        "evidence": "简化描述以减少 token 用量"
    },
    {
        "id": "P3",
        "human_label": "borderline-approve",
        "proposed": (
            "V3 模块探索：AgentNodeLoop，读完再选，"
            "local_grep 主动发现 + local_read 确认内容 + submit_module 提交，"
            "符合 F-14 判断信息充分原则（F-14: 判断节点信息充分的必要条件）。"
        ),
        "evidence": "给 F-14 增加了括号注释，减少新 AI 需要查文档的频率"
    },
    {
        "id": "P4",
        "human_label": "reject",  # 近义词替换，无信息增益
        "proposed": (
            "V3 模块探索：使用 AgentNodeLoop 方法，先读后选，"
            "通过 local_grep 发现 + local_read 验证 + submit_module 提交，"
            "遵循 F-14 信息充分判断准则"
        ),
        "evidence": "措辞更现代化（'读完再选' → '先读后选'）"
    },
    {
        "id": "P5",
        "human_label": "reject",  # 幻觉
        "proposed": (
            "V3 模块探索：AgentNodeLoop，读完再选，"
            "local_grep 主动发现 + local_read 确认内容 + web_search 补充外部文档 + "
            "submit_module 提交，符合 F-14 判断信息充分原则"
        ),
        "evidence": "trace 显示 agent 多次调用 web_search 查外部 API 文档"
    },
]

_JUDGE_SYSTEM = """你是代码规范质量仲裁员。

你会看到一个 Router 节点的:
  - format_in / format_out
  - 当前 DESCRIPTION
  - 提议的新 DESCRIPTION
  - 改动依据 (evidence)

你的任务: 判断这个改动是否值得应用。

判断维度:
  A. 信息增益: 新描述对"不知道这个节点用来做什么的 AI"提供了更多可操作指引吗?
  B. 准确性: 新描述和 FORMAT 语义、已知工具列表、节点职责一致吗? 有没有幻觉?
  C. 最小修改: 改动是否精准必要，还是冗余/纯美化?

输出 JSON (不要任何其他文字):
{
  "score": 0.0-1.0,
  "verdict": "approve|borderline|reject",
  "reasoning": "2-3 句话",
  "concerns": ["若有则列"]
}

score 参考:
  0.0-0.3  明显有害或无效
  0.3-0.5  改动微小或有疑问
  0.5-0.7  有价值但有保留
  0.7-1.0  清晰的正向改动
"""


def judge_patch(p: dict) -> dict:
    from omnicompany.runtime.llm.llm import LLMClient
    client = LLMClient(model="qwen3.6-plus", role="runtime_main", max_tokens=512)
    user_msg = f"""## 节点信息
format_in: {FORMAT_IN}
format_out: {FORMAT_OUT}

## 当前 DESCRIPTION
{CURRENT_DESC}

## 提议新 DESCRIPTION
{p['proposed']}

## 改动依据
{p['evidence']}

请评判这个改动。"""
    resp = client.call(
        messages=[{"role": "user", "content": user_msg}],
        system=_JUDGE_SYSTEM,
        info_audit=False,
        caller="info_audit.description_refiner",
    )
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
        return json.loads(raw)
    except Exception:
        return {"score": -1, "verdict": "parse_fail", "reasoning": raw[:200]}


def main():
    print("=== Exp F: patch self-judge 校准 ===\n")
    results = []
    for p in PATCHES:
        print(f"[{p['id']}] human={p['human_label']}", flush=True)
        judgment = judge_patch(p)
        score = judgment.get("score", -1)
        verdict = judgment.get("verdict", "?")
        correct = "✓" if (
            (p["human_label"] == "approve" and verdict == "approve") or
            (p["human_label"] == "reject" and verdict == "reject") or
            (p["human_label"] == "borderline-approve" and verdict in ("approve", "borderline"))
        ) else "✗"
        print(f"   LLM: score={score:.2f} verdict={verdict} {correct}")
        print(f"   reasoning: {judgment.get('reasoning','')[:120]}")
        concerns = judgment.get("concerns", [])
        if concerns:
            print(f"   concerns: {concerns[:2]}")
        print()
        results.append({
            "id": p["id"], "human": p["human_label"],
            "llm_score": score, "llm_verdict": verdict,
            "correct": correct == "✓",
            "reasoning": judgment.get("reasoning",""),
        })

    # Summary
    n_correct = sum(1 for r in results if r["correct"])
    print(f"=== 准确率: {n_correct}/{len(results)} ({100*n_correct//len(results)}%) ===")

    out = "e:/WindowsWorkspace/omnicompany/data/domains/absorption/exp_f_self_judge.json"
    import json
    open(out, "w", encoding="utf-8").write(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"written: {out}")


if __name__ == "__main__":
    main()
