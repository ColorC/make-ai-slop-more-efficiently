# [OMNI] origin=claude-code domain=omnifactory/lap_auditor ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:diagnosis.lap_auditor.llm_compliance_auditor.worker.python"
"""SpecAuditorWorker — lap_auditor LLM 合规审计 (Stage 3 独立文件).

Worker 协议:
  FORMAT_IN  = lap_auditor.context
  FORMAT_OUT = lap_auditor.report

职责: 调用 LLM 按四大 LAP 红线审计代码。
铁律 A 合规: 不做预防性截断, LLM 接收完整 code_context。
"""
from __future__ import annotations

from typing import Any

from omnifactory.packages.services._core.omnicompany import Worker
from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.runtime.routing.router import LLMRouter


_AUDITOR_SYSTEM_PROMPT = """\
你是一个高级架构审计员，熟悉「LAP（Logic-Anchor-Pipeline）六元语义接口规范」。
你的任务是阅读给定的代码，并按以下 4 个分类象限对其进行严格的架构审计：

【基础分类象限】
1. 规范的 LAP 管线实现（优质业务代码）：
   - 使用 TeamSpec 声明纯拓扑。
   - 包含明确的 Worker 绑定。节点职责单一。
   - 分支判断由 Verdict 和 Route 完成，而不是在 Worker.run 内部写死 if/else 控制管线走向。
2. 不完全的 LAP 管线实现（缺陷业务代码）：
   - 披着 LAP 的壳，但在 Worker.run() 中存在状态越位共享（绕过 Signal，直接修改外部状态/对象）。
3. 绕过了 LAP 管线的业务代码（非 LAP 业务代码）：
   - 原生的 Python 脚本或类，没拆分为 Node/Edge，没有注册 EventBus，是脱离监测的网络孤岛。
4. 基础设施代码（非业务）：
   - 无差别适用于所有业务领域、所有节点的底座代码。决不能掺杂特定业务逻辑。

🚨 【四大不可妥协的 LAP 核心规范（必须遵循的红线）】🚨
你必须利用以下 4 点要求对代码进行极限苛刻的审视。若违反任何一条，即便是使用 TeamSpec 编写的代码，也要直接判为"不完全的 LAP 管线实现（缺陷代码）"：

1. **事件总线驱动（Event-Bus Driven / Stock 驱动）**
   - 以产出者(Publisher)和消费者(Consumer)形式流转。确保一切模块间的交流信息都在事件（Event / Signal / Material）中流转，不可私下跨对象传参或调用。
2. **Material 永远匹配真实内容（Truth in Materials / Formats）**
   - 传递的 Material 描述了什么，内容 Payload 就应该是对应的精确定义。
   - 例如：如果加入了历史记录或上下文，"用户需求+工具调用历史+思考"绝不等于"工具最后一次调用"。如果 Payload 的范围扩大了，就必须将 Material 重新定义为更准确的名称（如"完整对话"），绝不能继续使用原来那个狭窄的 Material 名称。
3. **遵循标准接口规范实现**
   - Node、Anchor、Route、Validator、Worker 等必须严格继承并使用六元接口的基类。所有状态返回必须使用 Verdict 和 VerdictKind。
4. **类型的确切表达与识别度（Domain & Type Isolation）**
   - Domain（领域）一定要分清楚。理论上和已有内容有区别的，必须得到确切的类型表达。

请输出规范的 Markdown 分析报告，包含：
1. 【整体分类结论】（给出这批代码主要属于哪一个类别，并明确指出它是否违反了四大红线）
2. 【红线与违规点剖析】（详细指出违背了哪一条不可妥协的规范，或指出现存的逻辑越界）
3. 【演进与修复建议】（说明如何重构，比如如何修正 Material、如何抽离总线传输、如何分离 Domain）

⚠️ 注意：绝不允许在回答中重复被审计的源代码！使用纯文本返回总结分析即可，严格控制在 1000 字以内，避免触发最大 token 截断。
"""


class SpecAuditorWorker(Worker, LLMRouter):
    """LLM 审计节点：根据四大红线评估代码的 LAP 规范依从度。

    铁律 A 合规: 不对 code_context 做预防性截断, LLM 接收完整代码。
    """

    DESCRIPTION = (
        "调用 LLM，依据四大 LAP 红线（事件总线、Material真实性、接口规范、Domain隔离）"
        "对 code_context 中的代码进行架构审计，输出分类结论和修复建议。"
    )
    REFLECTION_ENABLED = True
    FORMAT_IN = "lap_auditor.context"
    FORMAT_OUT = "lap_auditor.report"
    INPUT_KEYS = ["code_context"]

    def run(self, input_data: Any) -> Verdict:
        code_context = input_data.get("code_context", "")
        if not code_context:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="输入不满足要求: 缺少 code_context")

        messages = [
            {"role": "user", "content": f"请审计以下代码：\n\n{code_context}"}
        ]

        try:
            response = self.client.call(
                messages=messages,
                system=_AUDITOR_SYSTEM_PROMPT,
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"LLM 调用失败: {e}")

        content = "".join(
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", "text") == "text"
        )

        if not content:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="LLM 返回空响应")

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "report": content,
                "target_path": input_data.get("target_path"),
            },
        )
