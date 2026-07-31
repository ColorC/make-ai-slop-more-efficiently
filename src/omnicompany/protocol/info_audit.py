# [OMNI] origin=claude-code domain=protocol/info_audit.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:protocol.information_audit.schema.py"
"""Information Audit Protocol.

每次 LLM 节点执行后产出一份 InfoAuditReport, 让 LLM 用**中性语言**只回答
它本地观察到的事实:

  1. 现有信息对完成任务的充分程度?     → sufficiency: Sufficiency
  2. 它观察到缺了什么具体的信息?        → missing_info: list[MissingInfoItem]
  3. 每一项缺失的重要程度?              → missing_info[*].critical: bool
  4. 它对本次自评的置信度?              → confidence_self: float
  5. 非缺失的风险或观察?                → concerns: list[str] (自由文本)

**关键设计决策 (2026-04-09, 实地 Tier A+B 观察后修正)**:

**不问 "要不要 fallback" / "要不要继续"** —— 这类"承认失败"的问题 LLM 极不可靠,
要么保守地永远报 False, 要么一被质疑就马上认错。决策权**完全不给 LLM** —— runner
根据 `missing_info[*].critical` 是否非空 + 全局开关, 规则化决定是否触发兜底。

**节点是自治的**: 每个 LLM 只认自己的 format_in / format_out / DESCRIPTION, 只
报**本地观察**, 不要求它考虑上游下游全局。跨节点不一致的发现由 runner / guardian
横向对比, 不压在 LLM 身上。这是架构设计优势, 不是妥协 —— 自治的局部诚实报告比
勉强的全局判断更可信。

`concerns` 保持**完整自由文本** list, 不强制 subtype 分类 —— 元描述细粒度在下游
处理时再做, 不在采集端强行归类丢失细节。

三种产出路径 (对应 D1):
  STRICT     — 独立 isolated LLM 调用, 只看 prompt + 输出, 最客观
  PIGGYBACK  — 主 LLM 输出时搭便车, 成本最低, 真实反映局部状态
  OFF        — 不生成 info audit
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Sufficiency(str, Enum):
    """现有信息对完成任务的充分程度。"""

    SUFFICIENT = "sufficient"
    """信息充分,产出可靠。"""

    PARTIAL = "partial"
    """部分充分,产出可用但有限制/需要下游补齐。"""

    INSUFFICIENT = "insufficient"
    """信息不足,产出可能不可靠。应考虑 fallback。"""

    UNKNOWN = "unknown"
    """LLM 无法判断,或审计解析失败时的保守默认值。"""


class MissingInfoItem(BaseModel):
    """描述一条缺失的信息。

    LLM 填写时必须足够具体,以便下游:
      1. AgentNodeLoop 能根据 description + suggested_source 去捞
      2. guardian / 人类审计时能判断"这个缺失合理吗"

    不要写 "更多上下文" / "缺示例" 这种空洞描述,必须具体到
    "某文件的某字段" / "某 API 的某参数"。
    """

    description: str
    """缺什么。必须具体,不能空洞。"""

    critical: bool = False
    """缺这个是否会让当前节点产出不可靠 (True = 关键)。"""

    suggested_source: str | None = None
    """建议去哪里找。例: 'inspect.getsource(Router)' / 'data/domains/demogame/...'
    / 'ask human' 等。可为 None 表示 LLM 自己也不知道。"""

    confidence: float = 0.5
    """LLM 对"这条缺失描述得准不准"的自评 (0.0-1.0)。"""


class InfoAuditReport(BaseModel):
    """LLM 节点的信息审计报告。

    由 LLMClient 在开启 info_audit 模式时自动填充 (PIGGYBACK),
    或由 isolated probe 单独产出 (STRICT)。

    Verdict.info_audit 会携带本对象。**决策权不在本对象** —— runner 根据
    missing_info 里 critical=True 的项是否非空, 结合全局开关, 规则化决定是否
    触发兜底。LLM 只报本地观察的事实, 不做失败判断。
    """

    sufficiency: Sufficiency = Sufficiency.UNKNOWN
    """现有信息对完成任务的充分程度(LLM 本地观察)。中性描述,不暗示失败。"""

    missing_info: list[MissingInfoItem] = Field(default_factory=list)
    """观察到的缺失项结构化列表。每项带 critical 标志表重要程度。"""

    confidence_self: float = 0.5
    """LLM 对这份审计报告本身的自评置信度 (0.0-1.0)。
    STRICT 模式下通常更高(独立审计),PIGGYBACK 下通常反映 LLM 自信程度。"""

    attention_focus: str = ""
    """当前 LLM 的注意力集中在什么方面(自由文本)。"""

    concerns: list[str] = Field(default_factory=list)
    """LLM 识别的风险/观察/澄清/修改声明等,保持完整自由文本。
    不强制 subtype 分类 —— 元描述细粒度由下游处理时再做。"""

    @property
    def missing_critical(self) -> list[str]:
        """关键缺失项的快速索引 (description 列表)。
        派生自 missing_info 里 critical=True 的项,不需要 LLM 额外填写。"""
        return [m.description for m in self.missing_info if m.critical]

    @classmethod
    def parse_failed(cls, reason: str) -> InfoAuditReport:
        """审计 JSON 解析失败时的兜底报告。

        永远不能让 audit 解析失败反过来阻塞正常路径,这是 §9.2 风险对策。
        返回 UNKNOWN 状态, 明确表达"本次审计无法判断" —— runner 不应因此触发兜底。
        """
        return cls(
            sufficiency=Sufficiency.UNKNOWN,
            confidence_self=0.0,
            concerns=[f"info_audit JSON 解析失败: {reason}"],
        )


# -----------------------------------------------------------------------------
# LLM 提示词模板 (给 Phase 2 LLMClient 注入用)
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# info_audit Tool Schema (Anthropic 格式, OpenAI 侧由 llm.py 转换)
# -----------------------------------------------------------------------------
#
# M1 改造 (2026-04-15): piggyback 不再追加 JSON 代码块, 改为强制 LLM 调用
# info_audit 工具返回结构化结果. 优点:
#   1. 不污染主答案 (strict-JSON 下游节点不再因追加块崩溃)
#   2. LLM 遵守率 100% (而非长输出时 0%)
#   3. AgentNodeLoop 天然排除 (不给它注入此 tool)
#
# 问题措辞: 不问 "你能完成吗", 只问 "哪些信息若有会更好 / 你觉得应该存在但没拿
# 到的信息是什么" — 避开 LLM 自信偏置, 转向可操作的外部事实.

INFO_AUDIT_TOOL_NAME = "info_audit"

INFO_AUDIT_TOOL_SCHEMA: dict = {
    "name": INFO_AUDIT_TOOL_NAME,
    "description": (
        "在完成主任务的同时, 强制报告你对**支撑信息充分性**的观察. "
        "这不是评价你能不能完成任务 / 完成得好不好, 而是把你看到的 "
        "'哪些信息若有会让我做得更扎实' 记录下来, 供流水线沉淀. "
        "本工具调用**不代替主答案**, 你依然要完成原本的任务(通过文本回答或其他工具). "
        "诚实报告, 不要虚报缺失, 也不要隐瞒缺失."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sufficiency": {
                "type": "string",
                "enum": ["sufficient", "partial", "insufficient", "unknown"],
                "description": (
                    "对当前输入支撑信息的客观观察: "
                    "sufficient=足够做出有依据的产出; "
                    "partial=可产出但部分字段只能猜或留空; "
                    "insufficient=关键信息缺失导致产出基本是猜测; "
                    "unknown=罕见, 无法判断."
                ),
            },
            "missing_info": {
                "type": "array",
                "description": "观察到的具体缺失项.",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": (
                                "具体缺什么. 必须具体到下游工具能据此去捞. "
                                "好例: 'selftest/routers.py 里 LLMRouter 的完整源码'. "
                                "坏例: '更多上下文' / '更好的说明'."
                            ),
                        },
                        "critical": {
                            "type": "boolean",
                            "description": "缺这个是否让产出不可靠 (只标真正关键的).",
                        },
                        "suggested_source": {
                            "type": "string",
                            "description": "若知道可能在哪里找, 写明确路径/来源; 不知道填空串.",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "你对这条缺失描述准确性的自评 0.0-1.0.",
                        },
                    },
                    "required": ["description", "critical"],
                },
            },
            "should_exist_but_absent": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "你**觉得应该存在**但在本次输入中没拿到的信息(自由文本). "
                    "比 missing_info 更虚, 用于捕捉 LLM 直觉上的不满足感. "
                    "例: '应该有一份关于该 API 参数约束的官方文档'."
                ),
            },
            "confidence_self": {
                "type": "number",
                "description": "你对本审计准确性的自评 0.0-1.0.",
            },
            "attention_focus": {
                "type": "string",
                "description": "本次任务你的注意力集中在什么方面 (自由文本).",
            },
            "concerns": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "自由文本风险 / 澄清 / 主动修改说明 / 边界情况. "
                    "不强制分类, 保持细节."
                ),
            },
        },
        "required": ["sufficiency"],
    },
}


def info_audit_tool_payload_to_report(payload: dict) -> InfoAuditReport:
    """把 tool_use block 的 input dict 映射为 InfoAuditReport.

    兼容字段: 新增的 should_exist_but_absent 并入 concerns (前缀 '[wished] '),
    保持 InfoAuditReport 结构稳定不扩 schema.
    """
    if not isinstance(payload, dict):
        return InfoAuditReport.parse_failed("tool payload not a dict")
    try:
        wished = payload.get("should_exist_but_absent") or []
        concerns = list(payload.get("concerns") or [])
        concerns.extend(f"[wished] {w}" for w in wished if isinstance(w, str) and w)
        data = {
            "sufficiency": payload.get("sufficiency", "unknown"),
            "missing_info": payload.get("missing_info") or [],
            "confidence_self": float(payload.get("confidence_self") or 0.5),
            "attention_focus": payload.get("attention_focus") or "",
            "concerns": concerns,
        }
        return InfoAuditReport.model_validate(data)
    except Exception as e:
        return InfoAuditReport.parse_failed(f"tool payload validation failed: {e}")


# -----------------------------------------------------------------------------
# 遗留: 文本追加版 (兜底, 模型不支持工具或注入失败时用)
# -----------------------------------------------------------------------------

INFO_AUDIT_PROMPT_APPENDIX = """

---
## 信息审计 (info_audit) — 强制要求

你必须在本次响应中**同时**:
  1. 完成主任务 (通过文本回答或调用任务相关工具)
  2. 调用 `info_audit` 工具, 报告你对"支撑信息是否充足"的观察

**不要跳过 info_audit 工具** — 你的工具列表中一定包含它.

若因某种原因无法调用工具 (如工具 runtime 错误), 才**另起一段** JSON 代码块作为兜底:

```json
{
  "info_audit": {
    "sufficiency": "sufficient|partial|insufficient|unknown",
    "missing_info": [
      {"description": "具体缺什么", "critical": true|false,
       "suggested_source": "哪里找 or null", "confidence": 0.0-1.0}
    ],
    "should_exist_but_absent": ["你觉得应该存在但没拿到的信息"],
    "confidence_self": 0.0-1.0,
    "attention_focus": "...",
    "concerns": ["..."]
  }
}
```

**关键原则**:

1. 不问你"能不能完成"/"完成度多少", 只报告"支撑信息够不够 / 什么信息若有会更好".
2. `missing_info[*].description` 必须具体到下游可操作 (好: 'XX 文件的 YY 字段'; 坏: '更多上下文').
3. `critical=true` 只标"缺了就让产出不可靠"的项.
4. 诚实报告本地观察, 不虚报也不隐瞒.
5. 本审计不影响主答案, 哪怕 insufficient 也要尽力给出最好的答案.
"""
