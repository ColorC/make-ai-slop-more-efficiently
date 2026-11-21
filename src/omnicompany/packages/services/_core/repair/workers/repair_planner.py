# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.llm_repair_planner.delta_generator.py"
"""RepairPlannerWorker — Repair Team Worker (Format 修复分组 · #1).

Worker 协议:
  FORMAT_IN  = repair.fmt.attempt
  FORMAT_OUT = repair.fmt.attempt

职责: 调用 LLM 分析 health_record 中的失败检查, 输出字段修复 delta JSON。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

logger = logging.getLogger(__name__)

_MODEL = "qwen3.6-plus"

_SYSTEM = """\
你是 OmniCompany 的 Format 修复专家，请严格对照 standards/material.md 规范修复 Format 定义。

=== 核心标准（必须满足） ===

F-01 五要素 — description 必须包含全部五项：
  ① 关键字段的业务含义（不只是字段名/类型，要说明用途）
  ② 枚举值的业务约束（如有 enum/状态机）
  ③ 上游来源：由哪个节点/管线生产
  ④ 下游用途：被哪个具体节点（名称）消费
  ⑤ 最小合法示例（或说明数据形态）

F-02 — description 长度 >= 100 字符 [MUST]

F-07 — examples 中的示例必须通过 json_schema 验证（如有 schema）[SHOULD]

=== 修复原则 ===
- 只输出需要修改的字段，不输出无需修改的字段
- 字段名必须是 Format() 构造器的合法 kwarg：description / tags / examples / json_schema / parent（禁止修改 id）
- tags 必须包含域标签（如 "bw"、"guardian"、"doctor" 等，从 format_id 前缀推断）
- description 修复重点：
    * 补全下游节点名（F-01-④）——格式："下游用途：[节点名] 使用此数据做 XXX"
    * 补全字段业务语义（F-01-①）——说明每个关键字段的用途，而非仅重复字段名
    * 长度 >= 100 字符（F-02）
- examples 修复：提供覆盖 json_schema required 字段的最小合法示例
- 如果是终端输出节点（"终端输出"/"无后续"），下游用途写"终端输出，供用户/CI 读取"
- 输出严格 JSON，无 markdown，无注释：{"field1": value1, "field2": value2}
- 如果所有标准已满足，输出空对象：{}
"""


class RepairPlannerWorker(Worker):
    """调用 LLM 分析 health_record 中的失败检查，输出字段修复 delta JSON。

    输入：repair.fmt.attempt（含 health_record + format source）
    输出：delta dict，key=字段名，value=修复后的值
    """

    DESCRIPTION = "LLM 分析 Format 健康失败项，输出字段修复 delta JSON"
    FORMAT_IN = "repair.fmt.attempt"
    FORMAT_OUT = "repair.fmt.attempt"

    def __init__(self, model: str | None = None):
        self._model = model or _MODEL

    def run(self, input_data: Any) -> Verdict:
        health_record: dict = input_data.get("health_record", {})
        source_excerpt: str = input_data.get("source_excerpt", "")
        format_id: str = input_data.get("format_id", "")

        delta = self._plan(format_id, health_record, source_excerpt)

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={**input_data, "delta": delta},
            diagnosis=f"RepairPlanner: delta_fields={list(delta.keys())}",
        )

    def _plan(self, format_id: str, health_record: dict, source_excerpt: str) -> dict:
        failing_checks = [
            c for c in health_record.get("checks", [])
            if not c.get("passed")
        ]
        if not failing_checks:
            return {}

        try:
            from omnicompany.runtime.llm.llm import LLMClient

            client = LLMClient(model=self._model)
            # 契约变更 #02 (2026-04-25): 显示 verdict + counts, 不显 grade
            counts = health_record.get("counts", {})
            counts_str = (f"counts: critical={counts.get('critical', 0)}, "
                          f"major={counts.get('major', 0)}, minor={counts.get('minor', 0)}")
            user_msg = (
                f"Format ID: {format_id}\n\n"
                f"当前源码（Format 定义段）：\n```python\n{source_excerpt}\n```\n\n"
                f"健康档案摘要 (verdict={health_record.get('verdict', 'uncertain')}, {counts_str})：\n"
                f"失败检查：\n"
                + "\n".join(
                    f"  - {c['check']}: {c.get('detail', '')} "
                    + (f"sub_checks={[s for s in c.get('sub_checks', []) if not s.get('passed')]}"
                       if c.get("sub_checks") else "")
                    for c in failing_checks
                )
                + "\n\n请输出修复 delta JSON。"
            )
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=_SYSTEM,
            )
            raw = resp.content[0].text.strip()
            # 去除可能的 markdown 代码块包装
            if raw.startswith("```"):
                raw = re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("`").strip()
            delta = json.loads(raw)
            # 安全检查：不允许修改 id
            delta.pop("id", None)
            return delta
        except Exception as e:
            logger.warning("RepairPlanner LLM call failed: %s", e)
            return {}
