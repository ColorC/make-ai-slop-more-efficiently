# [OMNI] origin=claude-code domain=services/_governance ts=2026-06-12T12:00:00Z type=config
# [OMNI] material_id="material:governance.package_init.py"
"""Governance department.

Members:
- plan_steward: plan ownership, Chinese short titles, and format checks.
- work_history: repeated user needs and corrections mined from work history.
- doc_steward: 文档时效性 / 引用完整性治理。
- progress_steward: 进度型自述治理(轨一)—— 进度收束回 whatnow, plan.md 只许自我陈述。
- prose_steward: 语言治理(轨二)—— 非中文泄漏 / 术语一致 / 惜字如金。
- health_suppress / health_benchmark: 两轨共享的抑制名单 + 金标 benchmark。

CLI entry: `omni governance`.
Structured JSON LLM calls consume `runtime.llm.structured.call_json`; the default
model is resolved by the `OMNI_STRUCTURED_LLM_MODEL` slot.
"""
# 显式导出共享设施, 建立可见 import 链(OMNI-074: 非孤儿模块)。
from . import health_benchmark, health_suppress  # noqa: F401
