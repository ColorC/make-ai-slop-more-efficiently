# [OMNI] origin=claude-code purpose=ec5-readme-audit ts=2026-04-18
"""README 能力五分类独立 LLM 审计（EC-5）。

目的：README.md 里把 src/omnicompany/ 的模块归成"学习/诊断/执行/持久化&观测/规范"五类。
本脚本让独立 LLM 对照 README 分类 + 实际目录结构，判断：
  - 覆盖性：有无重要模块未归类？
  - 归属明确性：有无模块同时属于多类（边界模糊）？
  - 分类本身是否合逻辑？

产出：data/domains/absorption/scratch/readme_classification_audit.md

不做：不自动修改 README（留给人审）。
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")
try:
    from dotenv import load_dotenv
    load_dotenv("e:/WindowsWorkspace/omnicompany/.env")
except ImportError:
    pass

from omnicompany.runtime.llm.llm import LLMClient

ROOT = Path("e:/WindowsWorkspace/omnicompany/src/omnicompany")
README = ROOT / "README.md"
OUT = Path("e:/WindowsWorkspace/omnicompany/data/domains/absorption/scratch/readme_classification_audit.md")


def collect_dir_tree(root: Path, max_depth: int = 2) -> list[str]:
    """收集 root 下的目录树，限制深度，排除 __pycache__/scratch 等噪声。"""
    lines: list[str] = []
    SKIP = {"__pycache__", "scratch", ".omni", "tests", "generated"}

    def walk(p: Path, depth: int, prefix: str):
        if depth > max_depth:
            return
        try:
            entries = sorted([e for e in p.iterdir() if e.name not in SKIP and not e.name.startswith(".")])
        except Exception:
            return
        dirs = [e for e in entries if e.is_dir()]
        for d in dirs:
            rel = d.relative_to(root).as_posix()
            lines.append(f"{prefix}{rel}/")
            walk(d, depth + 1, prefix + "  ")

    walk(root, 0, "")
    return lines


def build_prompt(readme_text: str, tree_lines: list[str]) -> str:
    tree_text = "\n".join(tree_lines)
    return f"""# 任务

你是 Omnicompany 架构审计员。我给你：
1. Omnicompany 顶层 README.md 全文（含能力五分类）
2. `src/omnicompany/` 实际目录树（深度 2）

你要做**独立判断**：README 的五分类是否准确反映实际代码结构。

## 五分类是什么

README 把能力归为五类：
- **学习**（Learning）— 从外部 / 运行经验中提炼知识
- **诊断**（Diagnosis）— 对管线/代码/信息做健康检查
- **执行**（Execution）— 管线/Agent 运行引擎
- **持久化 & 观测**（Persistence）— 数据/事件/审计落盘与查询
- **规范**（Protocol）— 契约层、基础设施

## 三条审计标准

1. **覆盖性**：目录树里有哪些重要模块没被 README 归入任何一类？
2. **归属明确性**：README 把某模块归到 X 类，但从名字/邻近位置看它也合理归到 Y 类？列出这些边界模糊案例。
3. **分类本身合逻辑**：五类划分是否清晰（无重叠、无遗漏的领域）？还是某两类其实是同一件事的两面？

## 输出格式（严格 JSON）

```json
{{
  "consistency_score": "high | medium | low",
  "coverage_issues": [
    {{"module": "相对路径", "reason": "为何应被归入某类"}}
  ],
  "boundary_ambiguities": [
    {{"module": "相对路径", "current_class": "README 里归的类", "alternative_class": "你认为也合理的类", "reason": "..."}}
  ],
  "logical_issues": [
    "如果觉得五分类本身有问题，列出来"
  ],
  "suggested_revisions": [
    "如果需要改 README，给出具体建议（行级别）"
  ],
  "overall_judgment": "一段话总结你的结论"
}}
```

**约束**：
- 只输出上述 JSON，不加其他文本
- `coverage_issues` / `boundary_ambiguities` / `logical_issues` / `suggested_revisions` 可以为空数组
- `consistency_score` 必须三选一

---

## README 全文

{readme_text}

---

## 实际目录树

```
{tree_text}
```
"""


def main() -> None:
    readme_text = README.read_text(encoding="utf-8")
    tree_lines = collect_dir_tree(ROOT, max_depth=2)
    print(f"[INFO] README 字数: {len(readme_text)}; 目录树条数: {len(tree_lines)}")

    prompt = build_prompt(readme_text, tree_lines)

    client = LLMClient(model="qwen3.6-plus", role="runtime_main", max_tokens=4096)
    print("[INFO] 调用 LLM...")
    t0 = time.time()
    resp = client.call(
        messages=[{"role": "user", "content": prompt}],
        system="你是一个架构审计员，严格按 JSON 格式输出。",
        info_audit=False,
        caller="ec5.readme_audit",
    )
    elapsed = time.time() - t0
    print(f"[INFO] LLM 耗时 {elapsed:.1f}s")

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
        start = raw.find("{")
        if start >= 0:
            try:
                data, _ = json.JSONDecoder().raw_decode(raw[start:])
            except Exception as e:
                print(f"[ERROR] JSON 解析失败: {e}")
                print(f"[DEBUG] 原始输出:\n{raw}")
                sys.exit(1)
        else:
            print(f"[ERROR] 无 JSON:\n{raw}")
            sys.exit(1)

    # 写 markdown 报告
    md = ["# README 能力五分类独立审计报告",
          "",
          f"**时间**：{time.strftime('%Y-%m-%d %H:%M')}",
          f"**审计员**：qwen3.6-plus (caller=ec5.readme_audit)",
          f"**LLM 耗时**：{elapsed:.1f}s",
          "",
          "## 总体判断",
          "",
          f"- **一致性评分**：`{data.get('consistency_score', 'N/A')}`",
          f"- **结论**：{data.get('overall_judgment', '（缺）')}",
          "",
          "## 覆盖性问题",
          ""]

    covs = data.get("coverage_issues", []) or []
    if not covs:
        md.append("_无_")
    else:
        for c in covs:
            md.append(f"- **{c.get('module', '?')}** — {c.get('reason', '')}")

    md += ["", "## 归属明确性（边界模糊）", ""]
    ambs = data.get("boundary_ambiguities", []) or []
    if not ambs:
        md.append("_无_")
    else:
        for a in ambs:
            md.append(f"- **{a.get('module', '?')}** — 当前归类 `{a.get('current_class', '?')}`，也可归 `{a.get('alternative_class', '?')}`：{a.get('reason', '')}")

    md += ["", "## 分类本身的逻辑问题", ""]
    logs = data.get("logical_issues", []) or []
    if not logs:
        md.append("_无_")
    else:
        for l in logs:
            md.append(f"- {l}")

    md += ["", "## 建议修订", ""]
    revs = data.get("suggested_revisions", []) or []
    if not revs:
        md.append("_无需修订_")
    else:
        for r in revs:
            md.append(f"- {r}")

    md += ["", "---", "", "## 原始 LLM JSON 输出", "",
           "```json", json.dumps(data, ensure_ascii=False, indent=2), "```", ""]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"[OK] 报告已写入 {OUT}")


if __name__ == "__main__":
    main()
