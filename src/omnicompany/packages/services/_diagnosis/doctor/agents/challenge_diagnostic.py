# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/agents ts=2026-05-07T11:35:00Z type=router status=skeleton agent=ai-ide
# [OMNI] summary="ChallengeDiagnosticAgent V0 — 质疑型诊断 agent (反向)·拿一条焦点假设走 schema §三步骤 4 真证否流程"
# [OMNI] why="V3 大工作 — 修 hypothesis_v1_upgrade_report 7.9 V2 剩余. 跟 spec/hypothesis 型平级是第三种诊断方法. 由 ChallengeQueue 排序的 top 假设作焦点喂进来"
# [OMNI] tags=agent,configurable,doctor,challenge,falsification,V3
# [OMNI] material_id="material:diagnosis.doctor.agents.challenge_diagnostic.skeleton.py"
"""ChallengeDiagnosticAgent · 质疑型诊断 (V0 骨架)

跟 spec_diagnostic / hypothesis_diagnostic 同形态, 但工作方向反过来:
- spec / hypothesis: 拿规范/假设判待诊断对象
- challenge: 拿单条焦点假设, 试证否假设本身

输入: doctor.challenge_diagnosis.request (含 focus_hypothesis_yaml_path / applies_to)
工作: 走 schema §三步骤 3-4 — 提质疑 + 试证否 (反例 fixture / 历史实例 / HIGH 权威规范)
输出: doctor.challenge_diagnosis.verdict + (条件) status=falsified

工具集 = spec/hypothesis 同 (read_file/glob/grep/list_dir/write_finding/submit_verdict)
+ git_log (历史查询) + record_hypothesis_challenge + record_hypothesis_resolution.

V0 限制:
- 一次只处理单条焦点假设 (调用方决定哪条 — 通常是 ChallengeQueue 排序的 top)
- 不批量 (批量是 ChallengeQueue 的事)
- 不自己改假设 statement (改假设是 HypothesisDeriverAgent 的事)
"""
from __future__ import annotations

# 保险: import 触发 doctor.tools 注册业务工具到 TOOL_REGISTRY
from omnicompany.packages.services._diagnosis.doctor import tools  # noqa: F401

from omnicompany.packages.services._core.agent import (
    AgentSpec,
    ConfigurableAgent,
)
from omnicompany.packages.services._diagnosis.doctor.agents.spec_diagnostic import (
    _build_spec_verdict_extractor,
)


CHALLENGE_DIAGNOSTIC_SPEC = AgentSpec(

    # ── 1. 注册信息 ─────────────────────────────────────
    id="doctor.challenge_diagnostic",
    name="ChallengeDiagnosticAgent",
    domain="doctor",
    parent_worker_kind="agent",
    registry_namespace="services.agent.instances",

    # ── 2. LLM 配置 ────────────────────────────────────
    llm_model="qwen-3.6-plus",
    llm_temperature=0.2,
    llm_max_tokens=16000,
    llm_max_turns=1000,
    llm_timeout_seconds=600,

    # ── 3. 产出 material ─────────────────────────────────
    output_materials=(
        "doctor.challenge_diagnosis.verdict",
        "doctor.health_finding",
    ),
    primary_output="doctor.challenge_diagnosis.verdict",

    # ── 4. 触发 material ─────────────────────────────────
    trigger_materials=(
        "doctor.challenge_diagnosis.request",
    ),
    trigger_mode="any",

    # ── 5. 响应范围 ─────────────────────────────────────
    accepted_input_materials=(
        "doctor.challenge_diagnosis.request",
    ),
    forbidden_input_materials=(
        "doctor.challenge_diagnosis.verdict",
        "doctor.health_finding",
    ),

    # ── 6. 用户输入 ─────────────────────────────────────
    user_input_template=(
        "请质疑焦点假设 {focus_hypothesis_yaml_path} (applies_to={applies_to}).\n\n"
        "做法 (按 schema §三步骤 3-4):\n"
        "1. 用 read_file 读焦点假设 yaml (含 statement / motivation / applies_to / risk_if_wrong)\n"
        "2. 调 record_hypothesis_challenge 落质疑 (改 status='challenged')\n"
        "3. 试证否 3 路径:\n"
        "   A. 反例 fixture: list_dir + read_file 看 _test_fixtures/red_* 是否有反例\n"
        "   B. 历史实例: git_log 查近期 fix commit\n"
        "   C. 权威规范: read_file 读 standards_authority_map.yaml HIGH 档跟假设对照\n"
        "4. 找到反例 → 调 record_hypothesis_resolution 落 falsified\n"
        "5. 找不到反例 → 假设留 'challenged'\n"
        "6. write_finding (finding_kind='challenge', applied_hypotheses=[焦点假设 id])\n"
        "7. submit_verdict 提交 verdict (consulted_references 填实际查的反例/commit/规范 path)"
    ),
    user_input_required_fields=(
        "focus_hypothesis_yaml_path",
        "applies_to",
    ),

    # ── 7. 系统 prompt ─────────────────────────────────
    prompt_path="src/omnicompany/packages/services/_diagnosis/doctor/agents/challenge_diagnostic_prompt.md",
    prompt_substitutions={
        "agent_role": "质疑型诊断 agent",
        "primary_output": "doctor.challenge_diagnosis.verdict",
    },

    # ── 8. 工具列表 ────────────────────────────────────
    tools=(
        "read_file",
        "glob",
        "grep",
        "list_dir",
        "git_log",
        "record_hypothesis_challenge",
        "record_hypothesis_resolution",
        "write_finding",
        "submit_verdict",
    ),

    # ── 9. 工作区 ─────────────────────────────────────
    workspace={
        "name": "doctor.challenge_diagnostic",
        "write_prefixes": (
            "data/services/doctor/findings/{task_id}/",
            "data/services/doctor/_notes/{task_id}/",
            "data/services/doctor/hypotheses/",  # record_hypothesis_challenge/resolution 真改这里
        ),
        "read_prefixes": "READ_ANY",
        "bash_cwd_prefixes": ("",),
    },

    # ── 10-11. gates / context_triggers ─ V0 空 ─────────
    gates=(),
    context_triggers=(),

    # ── 12. 含自定义代码 ──
    allow_custom_code=True,

    # ── 13. 测试基线 ─ Stage C 立 + V3.1 真 LLM dogfood 升 PASS (2026-05-07) ─────────
    test_baseline={
        "green_samples": (
            "docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/samples/sample_hypothesis_green_solid.yaml",
        ),
        "red_samples": (
            "docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/samples/sample_hypothesis_red_easy_falsify.yaml",
        ),
        "gradient_samples": (),
        "_baseline_validated_at": "2026-05-07",
        "_baseline_overall": "PASS",
        "_v3_note": (
            "V3 Stage C 形态接通 smoke 测 PASS (18 pytest)."
        ),
        "_v3_1_note": (
            "V3.1 (2026-05-07) 真 LLM dogfood (qwen-3.6-plus) — 红绿对比真有判别力. "
            "红 fixture: agent 真走 schema §三步骤 3-4 三路径完整证否, 引 worker.md R-24 真行号 + "
            "block_engineer.py:125 真 file:line + 67+ grep 计数 + Guardian 规则 OMNI-038 真 ID + "
            "附录 A 第 5 项 L209. status='active' → 'falsified', verification_status='untested' → "
            "'falsified', resolution 真落档. "
            "绿 fixture: agent 没调工具就退 — 虽过 submit_verdict 协议但红绿对比 PASS (绿原状). "
            "暴露 V3.1.1 prompt 问题 (submit_verdict 跳过), 留 V3.1.1 修. "
            "完整报告: docs/plans/.../v3_1_real_llm_dogfood_2026-05-07.md"
        ),
    },
)


class ChallengeDiagnosticAgent(ConfigurableAgent):
    """质疑型诊断 agent — 拿一条焦点假设走真证否流程.

    复用 spec/hypothesis 的 verdict 提取逻辑 (跟它们共用 submit_verdict 通用工具 + 同
    verdict 形态), V0 三种诊断方法共用 finding 三字段 (evidence/commentary/concern).
    """

    SPEC = CHALLENGE_DIAGNOSTIC_SPEC

    def build_tool_context(self, *, input_data, turn, trace_id):
        ctx = super().build_tool_context(input_data=input_data, turn=turn, trace_id=trace_id)
        ctx["current_task_id"] = trace_id
        ctx["agent_id"] = self.SPEC.id
        ctx["scratch"] = ctx.get("scratch", {})
        return ctx

    def build_extract_result(self, *, bus):
        return _build_spec_verdict_extractor(bus=bus)
