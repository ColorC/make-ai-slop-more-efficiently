# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.fail_path_planner.llm.py"
"""FailPathPlannerWorker — Repair Team Worker (Router 修复分组 · #4).

Worker 协议:
  FORMAT_IN  = diag.repair.desc-patch
  FORMAT_OUT = diag.repair.fail-patch

职责: R-05 专属 — 只生成 FAIL 路径补充 diff, 不处理其他问题。
"""
from __future__ import annotations

import logging
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import _MODEL, extract_diff

logger = logging.getLogger(__name__)


_FAIL_PROMPT = """你是 omnicompany LAP 协议专家。任务：根据 R-05 问题类型修复 run() 方法的 Verdict 路径，不改其他内容。

# 目标 Router: {router_class}

## R-05 问题描述
{r05_observation}

## 根据问题类型选择修复策略

### 策略 A：FAIL 路径缺失（观察到 "FAIL 缺失"）
- 在 run() 开头添加输入校验 → 若必要字段缺失返回 Verdict(kind=VerdictKind.FAIL, ...)
- 仅对直接访问列表中的字段进行保护

### 策略 B：PASS 路径缺失（观察到 "PASS 缺失"，且 FAIL 已存在）
- run() 已有 FAIL 路径，但最终 return 返回的是原始 dict 而非 Verdict(PASS, ...)
- 将 run() 末尾的 `return output_dict` 改为：
  `return Verdict(kind=VerdictKind.PASS, confidence=1.0, output=output_dict, diagnosis="...")`
- 不要改变任何输入校验逻辑

### 策略 C：PASS 和 FAIL 都缺失
- 先添加输入校验（策略 A），再包装最终返回（策略 B）

## INPUT_KEYS 声明（Router 明确声明需要的字段）
{input_keys}

## run() 源码中的直接访问点（input_data["key"]，若缺失会 KeyError）
{direct_accesses}

## run() 完整源码
```python
{run_source}
```

## Verdict 构造规范（严格）
- FAIL: `Verdict(kind=VerdictKind.FAIL, output=input_data, diagnosis="...", error_detail={{...}})`
- PASS: `Verdict(kind=VerdictKind.PASS, confidence=1.0, output=output_dict, diagnosis="...")`
- 禁止：`Verdict(result="FAIL", ...)` ← result 不是合法参数
- VerdictKind 导入：代码顶部已有 `from omnicompany.protocol.anchor import Verdict, VerdictKind`

## FAIL 路径语义（仅当策略 A/C 时使用）
- iterate（for x in key）：只检查 None，空列表不会 crash
- assign_to_dict_call（dict(key)）：None 会 crash，空 dict 不会
- index（key[0]）：None 和空列表都会 crash
- plain：None 会 crash，空值通常合法

## 输出
只输出 unified diff，不输出任何解释：

```diff
--- a/{router_class}
+++ b/{router_class}
@@ ... @@
 ... （context lines）
-        return output_dict
+        return Verdict(kind=VerdictKind.PASS, confidence=1.0, output=output_dict, diagnosis="...")
```
"""


class FailPathPlannerWorker(Worker):
    """R-05 专属: 只生成 FAIL 路径补充 diff, 不处理其他问题。

    提供直接访问 AST 分析结果 (usage_type + crash_if_missing/empty),
    让 LLM 能精确判断哪些需要 FAIL 保护、哪些语义上空值合法。
    """

    DESCRIPTION = (
        "R-05 专属 LLM 规划器：基于 AST 直接访问分析（usage_type/crash 语义），"
        "生成 FAIL 路径补充 diff（只在 run() 开头添加保护，不改其他逻辑）"
    )
    FORMAT_IN = "diag.repair.desc-patch"
    FORMAT_OUT = "diag.repair.fail-patch"

    def __init__(self, model: str = _MODEL):
        self._model = model

    def run(self, input_data: Any) -> Verdict:
        issues = input_data.get("b_class_issues", [])
        r05_issues = [i for i in issues if i.get("check_id") == "R-05"]
        if not r05_issues:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "fail_diff": None},
                           diagnosis="FailPathPlanner: 无 R-05 问题，跳过")

        router_class: str = input_data["router_class"]
        run_source: str = input_data.get("run_source", "(未提取)")
        direct_access_map: list = input_data.get("direct_access_map", [])
        input_keys_declared: list = input_data.get("input_keys_declared", [])
        r05_obs = r05_issues[0].get("observation", "")

        if direct_access_map:
            da_lines = []
            for da in direct_access_map:
                crash_empty = " | 空值也会crash" if da.get("crash_if_empty") else ""
                da_lines.append(
                    f'  - input_data["{da["key"]}"] 行{da["line"]} '
                    f'[{da["usage_type"]}] → KeyError if missing{crash_empty}\n'
                    f'    代码: {da["context"]}'
                )
            da_text = "\n".join(da_lines)
        else:
            da_text = "(未发现直接下标访问，可能使用 f-string 或其他变量名)"

        prompt = _FAIL_PROMPT.format(
            router_class=router_class,
            r05_observation=r05_obs,
            input_keys=", ".join(input_keys_declared) if input_keys_declared else "(未声明)",
            direct_accesses=da_text,
            run_source=run_source[:3000],
        )

        diff = self._call_llm(router_class, prompt)
        if not diff:
            return Verdict(kind=VerdictKind.FAIL, confidence=0.5,
                           output={**input_data, "fail_diff": None},
                           diagnosis=f"FailPathPlanner: {router_class} LLM 未生成 diff")

        return Verdict(kind=VerdictKind.PASS, confidence=0.9,
                       output={**input_data, "fail_diff": diff},
                       diagnosis=f"FailPathPlanner: {router_class} FAIL 路径 diff 生成")

    def _call_llm(self, router_class: str, prompt: str) -> str | None:
        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system="你是 omnicompany LAP 协议专家。只输出 unified diff，不输出任何解释。",
            )
            raw = resp.content[0].text if resp and resp.content else ""
            return extract_diff(raw)
        except Exception as e:
            logger.warning("FailPathPlanner LLM failed for %s: %s", router_class, e)
            return None
