# [OMNI] origin=claude-code domain=omnifactory/cleanup_bot ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:diagnosis.cleanup_bot.anomaly_detector_llm_worker.py"
"""AnomalyDetectorWorker — cleanup_bot LLM 异常判断 (Stage 3 独立文件).

Worker 协议:
  FORMAT_IN  = cleanup.evidence
  FORMAT_OUT = cleanup.plan

职责: 调用 LLM 判断哪些路径是 AI 误触产生的错位垃圾, 生成 PowerShell 清理脚本。
"""
from __future__ import annotations

from typing import Any

from omnifactory.packages.services._core.omnicompany import Worker
from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.runtime.routing.router import LLMRouter


_CLEANUP_SYSTEM_PROMPT = """\
你是一个「系统环境异常清理机器人」的大脑。
AI Agent 在操作宿主机时，经常因为 bash 相对路径写错、或者字符拼接遗漏，导致在操作系统里留下"错位、嵌套、重复"的垃圾文件夹。
例如：意图访问 C:/workspace/，但不小心执行了 mkdir -p E:\\e\\WindowsWorkspace。

现在，底层的扫描器已经搜集了一批包含特定关键词的文件系统的物理路径。
请仔细评估这些路径，分析哪些是**正常的系统文件/业务仓库**，哪些是**一眼鉴定为 AI 误触产生的错误嵌套垃圾**。

返回格式要求 (Markdown)：
1. 【异常判定结论】：指出哪些路径是错误的衍生垃圾。解释原因（如："e\\"这种单字母极不符合人类建站常理，是路径拼接事故）。
2. 【正常保留路径】：明确指出哪些是正常的源路径，绝不可删除。
3. 【Windows 清理脚本】：提供一段标准的 PowerShell 脚本，利用 Remove-Item 将确诊的垃圾目录删掉（带有 -Recurse -Force）。使用 ```powershell 代码块。
"""


class AnomalyDetectorWorker(Worker, LLMRouter):
    """LLM 侦探节点：分析路径合法性，生成清理计划。"""

    DESCRIPTION = (
        "调用 LLM，分析 EvidenceGathererWorker 收集的可疑路径列表，"
        "区分正常业务路径与 AI 误触产生的错位垃圾，输出 PowerShell 清理脚本。"
    )
    FORMAT_IN = "cleanup.evidence"
    FORMAT_OUT = "cleanup.plan"
    INPUT_KEYS = ["evidence_str"]

    def run(self, input_data: Any) -> Verdict:
        evidence_str = input_data.get("evidence_str", "")
        if not evidence_str:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="输入不满足要求: evidence_str 为空")

        keyword = input_data.get("keyword", "")
        messages = [
            {
                "role": "user",
                "content": (
                    f"请分析以下被扫出的路径名单，它们包含关键词 `{keyword}`:\n\n{evidence_str}"
                ),
            }
        ]

        try:
            response = self.client.call(messages=messages, system=_CLEANUP_SYSTEM_PROMPT)
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
            output={"anomaly_report": content},
        )
