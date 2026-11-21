# [OMNI] origin=claude-code domain=trace_induction/requirement_writer.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:services.learning.trace_induction.requirement_writer.sop_to_requirement.py"
"""requirement_writer — SOP → Workflow Factory 需求文档

将 SOP 提取结果转化为 Workflow Factory 能理解的结构化需求文档。
越结构化的需求文档，WF 的 req_analyzer 阶段输出越准确。

设计来源: DESIGN-trace-induction.md §需求文档格式
"""

from __future__ import annotations

import logging

from omnicompany.packages.services._learning.trace_induction.sop_extractor import SOP

logger = logging.getLogger(__name__)


# ── 需求文档模板 ──────────────────────────────────────────────────────────

_REQUIREMENT_TEMPLATE = """\
# Pipeline 需求：{purpose}

## 1. 目标
{goal}

## 2. 触发场景
{trigger_scene}
- 典型输入示例：{input_example}
- 典型输出示例：{output_example}

## 3. 操作流程（SOP）

### 步骤列表
{steps_text}

### 步骤间数据流
{data_flow}

### 判断/分支点
{branches}

## 4. 错误处理
{error_handling}

## 5. 验证标准
{validation_criteria}

## 6. 参考资料
- 来源 trace：{trace_ids}
- 领域知识：{domain_knowledge}

## 7. 约束
{constraints}
"""


_GENERATION_PROMPT = """\
你是一位技术文档工程师。
基于以下 SOP 和上下文信息，撰写一份 pipeline 需求文档。

SOP：
{sop_yaml}

上下文（从历史 trace 中提取）：
- 该任务执行了 {execution_count} 次
- 使用的领域知识：{domain_knowledge}

请按照以下模板格式输出需求文档（纯 Markdown，不要代码块包裹）：

{template}

注意：
- 目标必须一句话说清楚
- 验证标准必须具体可检查，不能是"结果正确"这种笼统描述
- 错误处理要覆盖 SOP 中标注的每个异常场景
- 数据流描述要精确到具体的数据字段
- 约束中必须包含 SOP 中提到的所有 notes 和 rules
"""


# ── 主接口 ────────────────────────────────────────────────────────────────

def generate_requirement_from_sop(
    sop: SOP,
    *,
    domain: str = "",
    domain_knowledge: str = "",
) -> str:
    """从 SOP 直接生成需求文档（确定性模板填充，不调用 LLM）。

    适用于 SOP 已经足够详细的场景。
    对于复杂场景建议使用 generate_requirement_with_llm()。
    """
    steps_lines = []
    for s in sop.steps:
        line = f"{s.id}. {s.action} — 工具：{s.tool}"
        if s.notes:
            line += f"（{s.notes}）"
        steps_lines.append(line)

    data_flow_lines = []
    for i, s in enumerate(sop.steps):
        if i < len(sop.steps) - 1:
            next_s = sop.steps[i + 1]
            if s.output and next_s.input:
                data_flow_lines.append(
                    f"- Step {s.id} → Step {next_s.id}：传递 {s.output}"
                )

    error_lines = []
    for e in sop.error_handling:
        error_lines.append(f"- {e.condition}：{e.action}")

    # 收集所有 rules
    constraint_lines = []
    for s in sop.steps:
        for r in s.rules:
            constraint_lines.append(f"- {r}")
    if sop.preconditions:
        for p in sop.preconditions:
            constraint_lines.append(f"- 前置条件：{p}")

    return _REQUIREMENT_TEMPLATE.format(
        purpose=sop.purpose,
        goal=sop.purpose,
        trigger_scene=f"当需要{sop.purpose}时",
        input_example=sop.steps[0].input if sop.steps and sop.steps[0].input else "（待补充）",
        output_example=sop.steps[-1].output if sop.steps and sop.steps[-1].output else "（待补充）",
        steps_text="\n".join(steps_lines) or "（无）",
        data_flow="\n".join(data_flow_lines) or "（无显式数据流）",
        branches="（无条件分支）",
        error_handling="\n".join(error_lines) or "（无错误处理）",
        validation_criteria="- Pipeline 执行完成且无异常\n- 输出符合预期格式",
        trace_ids=", ".join(sop.derived_from),
        domain_knowledge=domain_knowledge or "（无）",
        constraints="\n".join(constraint_lines) or "（无特殊约束）",
    )


async def generate_requirement_with_llm(
    sop: SOP,
    *,
    llm_call=None,
    execution_count: int = 1,
    domain_knowledge: str = "",
) -> str | None:
    """用 LLM 从 SOP 生成更完善的需求文档。

    Args:
        sop: 结构化 SOP
        llm_call: async callable(messages, system) -> str
        execution_count: 该任务执行了多少次
        domain_knowledge: 相关领域知识

    Returns:
        Markdown 格式的需求文档，或 None（失败时）
    """
    if llm_call is None:
        # 降级为确定性模板填充
        return generate_requirement_from_sop(
            sop, domain_knowledge=domain_knowledge,
        )

    sop_yaml = sop.to_yaml_str()

    prompt = _GENERATION_PROMPT.format(
        sop_yaml=sop_yaml,
        execution_count=execution_count,
        domain_knowledge=domain_knowledge or "无",
        template=_REQUIREMENT_TEMPLATE.format(
            purpose="{purpose}",
            goal="{一句话描述}",
            trigger_scene="{什么时候需要用}",
            input_example="{具体例子}",
            output_example="{具体例子}",
            steps_text="{步骤列表}",
            data_flow="{数据流描述}",
            branches="{分支描述}",
            error_handling="{错误处理}",
            validation_criteria="{验证标准}",
            trace_ids=", ".join(sop.derived_from),
            domain_knowledge="{相关知识}",
            constraints="{约束条件}",
        ),
    )

    try:
        raw = await llm_call(
            [{"role": "user", "content": prompt}],
            "你是一位技术文档工程师。请输出纯 Markdown 格式的需求文档。",
        )
        return raw.strip()
    except Exception:
        logger.warning("[requirement_writer] LLM generation failed", exc_info=True)
        # 降级为确定性填充
        return generate_requirement_from_sop(
            sop, domain_knowledge=domain_knowledge,
        )
