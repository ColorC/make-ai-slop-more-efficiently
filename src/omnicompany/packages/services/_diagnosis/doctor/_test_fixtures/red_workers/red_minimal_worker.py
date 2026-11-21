# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/_test_fixtures ts=2026-05-06T19:00:00Z type=fixture status=active agent=ai-ide
# [OMNI] summary="红样本 fixture: 故意违反 worker.md 规范 R-01/R-02/R-04/R-14 4 条. SpecDiagnosticAgent 跑应至少产 4 finding"
# [OMNI] why="self_audit §B-2 红绿基线修复: 用户铁律 feedback_connected_is_not_discriminating 要求红绿对比. 此 fixture 是红样本"
# [OMNI] tags=test-fixture,red-sample,worker,non-compliant
# [OMNI] material_id="material:diagnosis.doctor.test_fixtures.red_workers.red_minimal_worker.py"
"""
红样本 fixture — 故意违反 worker.md 规范 4 条.

⚠️ 不是真 worker, 不挂 dispatcher, 仅供 SpecDiagnosticAgent 红绿基线用.

故意违反 (各条 SpecDiagnosticAgent 应识别 + 引):
- R-01: DESCRIPTION ≥ 50 字 → 这里只 8 字
- R-02: 必有 FORMAT_OUT 显式声明 → 这里没 FORMAT_OUT
- R-04: 调 LLM 必走 LLMClient 不直接 import openai/anthropic → 这里直接 import openai
- R-14: Verdict.diagnosis 必写明判定依据 → 这里 'OK' 空话
"""
from __future__ import annotations

# R-04 违反: 直接 import openai (跟 H-2026-05-06-003 假设对应)
# 实际生产 worker 应走 omnicompany.runtime.llm.LLMClient 统一入口
import openai  # noqa: F401

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class RedMinimalWorker(Worker):
    # R-01 违反: DESCRIPTION 太短 (跟 H-002 对应)
    DESCRIPTION = "干个事"

    # R-02 违反: 没 FORMAT_OUT 类属性 (跟 H-001 对应)
    FORMAT_IN = "fixture.input"
    # FORMAT_OUT 缺

    def run(self, input_data):
        # R-04 违反: 直接调 openai 不走 LLMClient
        # (这里只是签名级体现, 不实际跑)
        _ = openai  # 引用让 linter 不删 import

        # R-14 违反: Verdict.diagnosis 空话, 不写"判定了什么 / 怎么判定"
        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis="OK",  # 空话
        )
