# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/builders ts=2026-05-07T05:55:00Z type=router status=skeleton agent=ai-ide
# [OMNI] summary="HypothesisAgentPromptBuilder — 据假设 yaml 自动产新诊断 agent prompt skeleton. plan §一第 7 条用户原话 '据健康假设产新诊断 agent' 的雏形"
# [OMNI] why="meta_diagnosis_pipeline_plan §阶段 8 + 元管线 5.8 节大动作 V1 — 真'诊断器构建器'. 不直接产新 ConfigurableAgent (改 dispatcher 太大), 只产 prompt skeleton, 给调用方决定接通"
# [OMNI] tags=builder,hypothesis,prompt-skeleton,structured,no-llm
# [OMNI] material_id="material:diagnosis.doctor.builders.hypothesis_agent_prompt_builder.skeleton.py"
"""HypothesisAgentPromptBuilder · 据假设产新诊断 agent prompt 骨架 (V0).

跟 PytestSkeletonBuilder 同模式 — 不用 LLM, 不直接落盘, 产 skeleton 字符串给调用方决定.

用户 plan §一第 7 条原话:
    "通过已有的需求进一步尝试拓展出各种健康性假设并记录,
     根据健康性假设再去创建用于诊断的 agent 或者 worker"

V0 行为:
- 输入: 一份假设 yaml dict (按 hypothesis_system_schema.md V1 schema)
- 输出: prompt skeleton 字符串 (按 hypothesis_diagnostic_prompt.md 模板)
- 含 OMNI 头 / status=skeleton / TODO 注释 / 引假设具体内容
- 调用方决定写到哪 (例 src/.../agents/hypothesis_<id>_prompt.md) + 是否立新 ConfigurableAgent

V0 不接通:
- 不直接产 ConfigurableAgent SPEC (是另一份工作, 涉及触发 material 命名空间 + dispatcher 注册)
- 不自动写到 src/ (避免污染)
- 调用方手工 review 后决定
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class HypothesisAgentPromptSkeleton:
    """据假设产的 prompt skeleton."""
    hypothesis_id: str
    hypothesis_statement: str
    target_path_suggestion: str  # 建议落档 path (但不直接落盘)
    content: str                 # prompt md 内容
    rationale: str               # 为什么产这份 skeleton


@dataclass
class HypothesisAgentBuildResult:
    skeletons: list[HypothesisAgentPromptSkeleton] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _safe_id(hypothesis_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", hypothesis_id)


class HypothesisAgentPromptBuilder:
    """据假设产新诊断 agent prompt 骨架.

    用法:
        builder = HypothesisAgentPromptBuilder()
        result = builder.build(hypotheses=[hyp_dict_1, hyp_dict_2, ...])
        for sk in result.skeletons:
            print(sk.target_path_suggestion)  # 建议落档 path
            print(sk.content)                  # prompt md 内容
    """

    def build(self, hypotheses: list[dict]) -> HypothesisAgentBuildResult:
        """据假设 list 产 skeleton.

        Args:
            hypotheses: list of hypothesis yaml dict (按 V1 schema). 必含 id/statement/motivation/applies_to.

        Returns:
            HypothesisAgentBuildResult, 含 skeletons list + notes (输入异常说明).
        """
        result = HypothesisAgentBuildResult()
        if not hypotheses:
            result.notes.append("输入为空 hypotheses list, 无 skeleton 产")
            return result

        for hyp in hypotheses:
            if not isinstance(hyp, dict):
                result.notes.append(f"跳过非 dict 输入: {type(hyp).__name__}")
                continue
            hid = hyp.get("id")
            statement = hyp.get("statement", "").strip()
            motivation = hyp.get("motivation", "").strip()
            applies_to = hyp.get("applies_to", "")
            if not hid or not statement:
                result.notes.append(f"跳过缺 id 或 statement 的假设: {hid or '<no-id>'}")
                continue

            safe = _safe_id(hid)
            target = f"src/omnicompany/packages/services/_diagnosis/doctor/agents/hypothesis_{safe}_prompt.md"
            content = self._gen_prompt_skeleton(hyp)
            result.skeletons.append(HypothesisAgentPromptSkeleton(
                hypothesis_id=hid,
                hypothesis_statement=statement,
                target_path_suggestion=target,
                content=content,
                rationale=f"据假设 {hid} (applies_to={applies_to}) 自动产专用诊断 agent prompt skeleton",
            ))

        return result

    def _gen_prompt_skeleton(self, hyp: dict) -> str:
        hid = hyp["id"]
        statement = hyp["statement"].strip()
        motivation = hyp.get("motivation", "").strip()
        applies_to = hyp.get("applies_to", "")
        evidence_query = hyp.get("evidence_query", "").strip()
        source_path = hyp.get("source_path", "")
        related_aps = hyp.get("related_anti_pattern_ids") or []
        confidence = hyp.get("confidence_level", "low")
        risk = hyp.get("risk_if_wrong", "low")

        related_aps_str = ", ".join(related_aps) if related_aps else "(待填)"

        return f'''<!-- [OMNI] origin=doctor.hypothesis_agent_prompt_builder domain=services/_diagnosis/doctor/agents ts=2026-05-07 type=prompt status=skeleton agent=doctor.builder -->
<!-- [OMNI] summary="据假设 {hid} 自动产 prompt skeleton — 单条假设专用诊断 agent" -->
<!-- [OMNI] why="hypothesis_agent_prompt_builder 自动产, 据 plan §一第 7 条 '据假设产新诊断 agent'. 调用方手工 review 后决定接通" -->
<!-- [OMNI] tags=prompt,agent,doctor,hypothesis-specific,auto-generated,skeleton -->
<!-- [OMNI] material_id="material:diagnosis.doctor.agents.hypothesis_{_safe_id(hid)}.system_prompt.md" -->

# 单假设诊断 agent · 系统 prompt (skeleton)

⚠️ **本 prompt 是 doctor.hypothesis_agent_prompt_builder 自动产的 skeleton, 需要调用方手工 review 后决定**:
1. 是否真要立新 ConfigurableAgent (有时一条假设走通用 HypothesisDiagnosticAgent 就够, 不需要专用 agent)
2. 是否要补 few-shot 例 (现 skeleton 没真 dogfood 产物作锚)
3. 是否要补反模式参照 (现 skeleton 只指 archetypes 编号 {related_aps_str}, 没解释)
4. 是否符合元规范 v1 (不跟现有 prompt 矛盾, 不重复造)

## 假设档案 ({hid})

- statement: {statement}
- motivation: {motivation}
- applies_to: {applies_to}
- evidence_query: {evidence_query}
- source_path: {source_path}
- related_anti_pattern_ids: {related_aps_str}
- confidence_level: {confidence}
- risk_if_wrong: {risk}

## 你做什么 / 不做什么

**做**:
- 拿待诊断对象 ({applies_to} 类型) 跟假设 {hid} 对照
- 按 evidence_query 指引去查证据 ({evidence_query})
- 自然语言判: 满足 / 违反假设 / 无法判断
- 给具体证据 (file:line 引用)
- 通过 submit_verdict 出口提交

**不做**:
- 不引其他假设 (本 agent 专做本假设, 不越界)
- 不修复. 只诊断
- 不打分. 用 evidence + commentary + concern 三字段自然语言

## finding 三字段

- evidence: 引代码/文档具体位置 (一句话)
- commentary: 引假设 statement + 证据具体说明这件事是什么 (一两段)
- concern: 来龙去脉 — 为什么这是问题 (或值得记的合规 case), 不修会怎样, 修起来代价多大 (引 risk_if_wrong={risk} 作参考)

## 反模式参照

跟反模式 archetypes.yaml AP-XXX 关联: {related_aps_str}

(skeleton 待手工补反模式具体内容跟 detection_strategy)

## 工具

跟现 HypothesisDiagnosticAgent 同: read_file / glob / grep / list_dir / write_finding / submit_verdict

## 提交

- target_entity_path / target_entity_kind ({applies_to})
- consulted_references: [本假设 yaml path / 待诊断对象 path]
- findings: list (每条 finding_kind=hypothesis, applied_hypotheses=[{hid}])
- narrative: 整体评论, 必含"是否满足假设 {hid}" 明确表态

## TODO (调用方手工补)

- [ ] 评估是否真需要立专用 agent (vs 走通用 HypothesisDiagnosticAgent 跑这条假设)
- [ ] 加 few-shot 真合规 finding 例
- [ ] 补反模式参照具体内容
- [ ] 跑红绿对比验真有判别力 (按用户铁律)
- [ ] 元规范 v1 自检 (不跟现有 prompt 矛盾)
- [ ] 改 status=skeleton → status=active (review 通过后)

## 退出

submit_verdict 校验通过返成功后, 调 finish.
'''
