# [OMNI] origin=claude-code domain=software_engineering/generated ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.generated.validation_and_stats_routers.implementation.py"
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router


class ValidateInputRouter(Router):
    """验证输入文本的非空与合法性"""

    FORMAT_IN = "sw.text-input"
    FORMAT_OUT = "sw.input-check-result"
    DESCRIPTION = (
        "接收用户文本输入意图，执行确定性校验逻辑。检查 JSON 结构合法性及 'text' 字段是否存在。"
        "若 text 为空字符串或 null，生成 status=FAIL 的验证结果对象；若非空，生成 status=PASS 的对象。"
        "此节点作为守门员，确保下游仅处理有效数据。"
    )

    def run(self, input_data: dict[str, Any]) -> Verdict:
        """
        验证输入数据的有效性

        Args:
            input_data: 包含 text 字段的输入字典

        Returns:
            Verdict: 验证结果，PASS 表示输入有效，FAIL 表示无效
        """
        try:
            # 检查 text 字段是否存在
            if "text" not in input_data:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={
                        "status": "FAIL",
                        "reason": "输入缺少必需的 'text' 字段"
                    }
                )

            text = input_data.get("text")

            # 检查 text 是否为空或 null
            if text is None or text == "":
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={
                        "status": "FAIL",
                        "reason": "文本内容为空"
                    }
                )

            # 检查 text 是否为字符串类型
            if not isinstance(text, str):
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={
                        "status": "FAIL",
                        "reason": "'text' 字段必须是字符串类型"
                    }
                )

            # 验证通过，返回 PASS 状态
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    "status": "PASS",
                    "text": text
                }
            )

        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={
                    "status": "FAIL",
                    "reason": f"验证过程发生错误: {str(e)}"
                }
            )


class CalculateStatsRouter(Router):
    """计算文本统计指标"""

    FORMAT_IN = "sw.input-check-result"
    FORMAT_OUT = "sw.stats-metrics"
    DESCRIPTION = (
        "读取上游验证结果。若输入状态为 FAIL，节点终止并返回验证失败信息；若为 PASS，"
        "对文本执行纯确定性统计计算（字数、行数、字符数）。计算逻辑不依赖 LLM，"
        "直接基于字符串操作生成统计指标对象。"
    )

    def run(self, input_data: dict[str, Any]) -> Verdict:
        """
        计算文本统计指标

        Args:
            input_data: 包含 status 和 text 的验证结果

        Returns:
            Verdict: 包含统计指标的结果
        """
        try:
            # 获取验证通过的文本
            text = input_data.get("text", "")

            # 计算字数（按空白字符分割）
            words = text.split()
            word_count = len(words)

            # 计算行数（空字符串为 0 行，否则按换行符分割）
            line_count = len(text.splitlines()) if text else 0

            # 计算字符数
            char_count = len(text)

            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    "word_count": word_count,
                    "line_count": line_count,
                    "char_count": char_count
                }
            )

        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={
                    "word_count": 0,
                    "line_count": 0,
                    "char_count": 0
                }
            )