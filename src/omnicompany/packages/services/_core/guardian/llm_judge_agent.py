# [OMNI] origin=claude-code domain=guardian/llm_judge_agent.py ts=2026-04-09T00:00:00Z
# [OMNI] migrated 2026-05-02: 旧 runtime.agent.agent_node_loop.AgentNodeLoop 已 deprecate
# [OMNI] 现在用 packages.services._core.agent.AgentNodeLoop (router 化新基础设施)
# [OMNI] material_id="material:core.guardian.llm_single_file_judge.implementation.py"
"""LLMJudgeAgent — AgentNodeLoop 版本的 LLM 裁判员 (新 router 化架构, 2026-05-02 迁).

替代 patrol.LLMJudge 的单轮调用 + 200 行硬截断问题。

核心改进:
  1. 有工具: read_file (分段读长文件) / grep (验证符号存在性)
  2. 每个文件一次 Agent loop, agent 可主动查证 "规则命中是否属实"
  3. 先 grep 再报告未定义符号 (避免 LLMJudge 曾出的假阳性: 符号定义在文件尾部但被判为"未导入")
  4. 输出结构同旧 LLMJudge: 便于 patrol_runner 合并

相关事故: 2026-04-09 LLMJudge 对 services/knowledge/store.py (401 行) 和 run.py (28 行)
报告 4 条 HIGH violations, 全部假阳性。根因: 200 行截断 + SYSTEM prompt 有幻觉规则。
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent.agent_loop_config import (
    CompactConfig,
    LoopConfig,
    PermissionConfig,
)
from omnicompany.packages.services._core.agent import (
    AgentNodeLoop,
    GrepRouter,
    ReadFileRouter,
)
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter

logger = logging.getLogger(__name__)


_NODE_PROMPT = """\
你是 OmniGuardian 的 LLM 裁判员 Agent。对一个候选文件做架构违规判断。

【你要检查的违规类型】
OMNI-002 CRITICAL: 业务代码出现在 src/omnicompany/runtime/ 框架层根目录(非框架核心文件)
OMNI-003 CRITICAL: 绕过 LLMClient 直接调用 LLM SDK,包括:
  - 直接导入: import anthropic / from anthropic import ...
  - 别名导入: import anthropic as ai
  - 动态导入: importlib.import_module("anthropic") / __import__("anthropic")
  - exec/eval: exec("import anthropic")
  - 调用非 LLMClient 的 LLM API 端点(openai.OpenAI(), anthropic.Anthropic())
  豁免: src/omnicompany/runtime/llm/llm.py 自身是 LLMClient 实现,可以 import 任何 SDK
OMNI-004 HIGH: Router 子类的 run() 是 async def 但方法体内没有任何 await
OMNI-005 HIGH: 代码将 .db 文件写入 data/ 之外的路径
OMNI-006 MEDIUM: 临时/调试脚本(以 test_/scratch_/tmp_/debug_ 开头)渗入 src/ 目录
OMNI-013 HIGH: 绕过 omnicompany.core.guarded_write.write_file 直接用 Path.write_text /
  open(..,"w") 写文件。豁免:
    - src/omnicompany/core/guarded_write.py / omnimark.py 自身
    - src/omnicompany/bus/*.py (SQLite 底层必须裸写)
    - tool_executor.py 的 undo backup write 分支
    - 数据文件 (.db / .json / .jsonl / events / traces) 走 data/ 下
OMNI-PIPELINE-IF HIGH: pipeline 文件 (pipeline.py/run.py/routers.py) 是否通过标准接口构建:
  - Router 子类必须继承 omnicompany.runtime.routing.router.Router (这是唯一位置)
  - Format 必须构造自 omnicompany.protocol.format.Format
  - pipeline.py 必须返回 omnicompany.protocol.pipeline.TeamSpec
  - 不允许自造 run loop / 自造事件总线
  【重要】从 runtime.routing.router 导入 Router 是合规的,不是违规。
  protocol 包下没有 router.py,也没有 protocol.router 模块。
OMNI-NEW: 你可以发现其他架构问题,severity 自定

【工作流程 — 必须遵守】

1. **先全量读文件**: 用 `read_file` 读完整文件 (支持 offset/limit, 文件 > 2000 行时分段读取)。
   **严禁只读开头就下结论**。

2. **查证 import / 符号定义**: 如果你怀疑某个函数/符号"未定义",必须先用 `grep` 在该文件内
   搜索这个符号的 `def <name>` 或 `class <name>` 或 `<name> =` 定义。很多符号定义在文件尾部,
   如果只读开头会误判。**在 grep 确认不存在之前,严禁报告"未定义/未导入"类违规**。

3. **runtime import 模式豁免**: 如果 import 写在函数体内部 (`def foo(): from x import y`)
   而不是文件顶部,这是 "runtime import" 模式,是合法的延迟加载。不要报告为"未 import"。

4. **except 分支的 path.write_text**: 如果裸写只出现在 `except` 块里做 error recovery,
   不要报告 OMNI-013。但如果 `except` 块是默默降级 (比如 guarded_write 不可用时静默写入),
   应该报告,建议移除 fallback 让错误浮出来。

5. **grep 用相对路径**: 默认从项目根跑 grep,path 留空或写 "src/".

【输出格式】完成判断后调用 `finish` 工具,result 参数必须是严格 JSON (无 markdown fence):

```json
{
  "has_violation": true,
  "violations": [
    {
      "rule_id": "OMNI-013",
      "severity": "HIGH",
      "confidence": 0.95,
      "reasoning": "第 380-381 行在 except 分支里调用 path.write_text() 绕过 guarded_write,不是 error recovery 而是静默降级 fallback",
      "suggestion": "删除 fallback 分支,让 guarded_write 导入失败直接抛出,便于问题浮出"
    }
  ],
  "clean_reasoning": ""
}
```

如果无违规,`has_violation=false`, `violations=[]`, `clean_reasoning` 写一句合规原因。

【最终自检】
发出 finish 之前,对每条 violation 自问:
- 我真的读完整文件了吗 (不只是前 200 行)？
- 我 grep 过被我说"未定义"的符号了吗？
- runtime import 模式的豁免我考虑了吗？
- except 分支的 error recovery 豁免我考虑了吗？
任何一项"否",回去补查证,不要匆忙 finish。
"""


class _LLMJudgePromptBuilder(PromptBuilderRouter):
    """裁判 agent 自定义首轮 user message — 把 input_data 的 file 信息拼成简洁 prompt."""

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        path = input_data.get("path", "?")
        abs_path = input_data.get("abs_path", "")
        change_type = input_data.get("change_type", "?")
        rule_hits = input_data.get("rule_hits") or []
        file_lines = input_data.get("file_lines", -1)

        if rule_hits:
            hits_hint = f"规则引擎已命中: {', '.join(rule_hits)}(重点检查规则引擎看不见的变体)"
        else:
            hits_hint = "规则引擎未命中任何规则(请全面审查)"

        content = [
            f"候选文件: {path}",
            f"绝对路径: {abs_path}",
            f"变更类型: {change_type} (A=新增 M=修改)",
            f"文件行数: {file_lines}",
            hits_hint,
            "",
            "请按 SYSTEM 里的工作流程:",
            "1. 用 read_file 读完整文件 (如果行数 > 2000 分多次读)",
            "2. 对每个怀疑点用 grep 查证",
            "3. 完成后用 finish 输出 JSON",
        ]
        return [{"role": "user", "content": "\n".join(content)}]


class _LLMJudgeExtractResult(ExtractResultRouter):
    """裁判 agent 自定义产物提取 — parse JSON, fallback 到 FAIL verdict."""

    def extract(
        self,
        *,
        final_text: str,
        messages: list[dict],
        turn_count: int,
        stop_reason: str,
    ) -> Verdict:
        text = (final_text or "").strip()
        # 去掉可能的 markdown fence
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()

        try:
            parsed = json.loads(text)
            if "has_violation" not in parsed:
                parsed["has_violation"] = bool(parsed.get("violations"))
            if "violations" not in parsed:
                parsed["violations"] = []
            return Verdict(kind=VerdictKind.PASS, output=parsed)
        except Exception as e:
            logger.warning(
                "[LLMJudgeAgent] 输出解析失败: %s\n原始: %s",
                e,
                text[:300],
            )
            return Verdict(
                kind=VerdictKind.FAIL,
                output={
                    "has_violation": False,
                    "violations": [],
                    "parse_error": str(e),
                },
                diagnosis=f"Agent 输出解析失败: {e}",
            )


class LLMJudgeAgent(AgentNodeLoop):
    """带工具的单文件违规裁判 agent (新 router 化架构, 2026-05-02 迁)。

    输入 (传给 run() 的 dict):
        {
            "path":         "src/omnicompany/packages/services/knowledge/store.py",
            "abs_path":     "E:/WindowsWorkspace/omnicompany/src/..." ,
            "change_type":  "A" | "M",
            "rule_hits":    ["OMNI-013", ...] (规则引擎已命中的规则),
            "file_lines":   401,   # 行数提示, agent 决定分段读取策略
        }

    输出 (Verdict.output):
        {
            "has_violation": bool,
            "violations": [
                {rule_id, severity, confidence, reasoning, suggestion},
                ...
            ],
            "clean_reasoning": "...",
        }
    """

    NODE_PROMPT: ClassVar[str] = _NODE_PROMPT
    TOOL_ROUTERS: ClassVar[list] = [GrepRouter, ReadFileRouter]
    LOOP_CONFIG: ClassVar[LoopConfig] = LoopConfig(
        max_turns=15,
        compact=CompactConfig(auto_compact_enabled=False),
        permission=PermissionConfig(mode="readonly"),
    )

    DESCRIPTION = "AgentNodeLoop: 单文件违规裁判 (带 read_file/grep 工具, 可分段读全文)"

    def __init__(
        self,
        *,
        model: str | None = None,
        bus: Any | None = None,
        config: LoopConfig | None = None,
    ):
        super().__init__(model=model, bus=bus, config=config or self.LOOP_CONFIG)

    # ── 子类钩子: 自定义 PromptBuilder + ExtractResult ──

    def build_prompt_builder(self, *, bus: Any) -> PromptBuilderRouter:
        return _LLMJudgePromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> ExtractResultRouter:
        return _LLMJudgeExtractResult(bus=bus)
