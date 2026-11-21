"""OmniPatrol — 规则引擎 + LLM Judge (薄层入口)

规则定义见 rules/ 子目录. 新增规则只需在对应 rules/*.py 中添加
check 函数和 GuardianRule, 并在 rules/__init__.py 注册. 本文件无需改动.
"""
# [OMNI] origin=omnifactory domain=omnifactory/guardian ts=2026-04-05T00:00:00Z
# [OMNI] material_id="material:services.guardian.patrol_legacy.rule_engine.llm_judge.archive.py"

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ─── 公开 API（向后兼容）──────────────────────────────────────────
# patrol_runner.py 和其他消费方从此处 import，无需改动。

# NOTE: 已归档 (2026-04-20), 不再被活代码 import。相对 import 改为 `..rules`。
from ..rules import (  # noqa: F401
    FileContext,
    GuardianRule,
    Violation,
    parse_omnimark,
    RULES,
)


# ─── 规则引擎 ──────────────────────────────────────────────────


class RuleEngine:
    """对 FileContext 列表运行所有规则，产出 Violation 列表。

    Phase 1 设计原则：
    - 纯计算，不触及文件系统
    - 每条规则独立执行，单条异常不影响其他规则
    - 违规计数全局唯一，便于跨 scan 追踪
    """

    def __init__(self, rules: list[GuardianRule] = RULES):
        self._rules = rules
        self._counter = 0

    def evaluate(self, files: list[FileContext]) -> list[Violation]:
        """运行所有规则，返回确认违规列表（向后兼容）。

        等价于 evaluate_split(files)["confirmed"] + evaluate_split(files)["needs_judgment"]。
        需要 Agent 复核的违规也包含在内（confidence=1.0，后续由 patrol_runner 决定是否送 Agent）。
        """
        result = self.evaluate_split(files)
        return result["confirmed"] + result["needs_judgment"]

    def evaluate_split(self, files: list[FileContext]) -> dict[str, list[Violation]]:
        """运行所有规则，按 certainty 分流返回。

        Returns:
            {"confirmed": [...], "needs_judgment": [...]}
            confirmed:      certainty="absolute" 的规则命中，直接报告
            needs_judgment:  certainty="needs_judgment" 的规则命中，建议送 GuardianAgent 复核
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        date_str = now[:10]
        confirmed: list[Violation] = []
        needs_judgment: list[Violation] = []

        for ctx in files:
            for rule in self._rules:
                try:
                    if rule.check(ctx):
                        self._counter += 1
                        ticket_id = f"TICKET-{date_str}-{self._counter:03d}"
                        msg = rule.message_template.format(path=ctx.path)
                        v = Violation(
                            ticket_id=ticket_id,
                            rule_id=rule.id,
                            severity=rule.severity,
                            path=ctx.path,
                            message=msg,
                            disposition=rule.disposition,
                            confidence=1.0,
                            detected_at=now,
                        )
                        if rule.certainty == "needs_judgment":
                            needs_judgment.append(v)
                        else:
                            confirmed.append(v)
                except Exception as e:
                    logger.debug("Rule %s failed on %s: %s", rule.id, ctx.path, e)

        return {"confirmed": confirmed, "needs_judgment": needs_judgment}


# ─── LLM Judge ─────────────────────────────────────────────────


class LLMJudge:
    """LLM 智能裁判层 — 处理规则引擎的盲区。

    规则引擎用字符串匹配，无法覆盖：
    - 别名导入:  import anthropic as ai; ai.Anthropic()
    - 动态导入:  importlib.import_module("anthropic")
    - exec/eval: exec("import anthropic")
    - subprocess 写文件: subprocess.run(["python", ...]) 生成文件
    - 进化代码:  生成器产出包含逃逸逻辑的文件

    LLMJudge 作为第二层，接收 FileContext 列表，
    对每个文件独立调用 LLM，输出结构化 Violation 列表。

    使用原则（Phase 1）：
    - 默认 disabled（需要显式传 enable=True）
    - 仅在 pilot_paths 配置的路径下运行（默认: packages/ 新增文件）
    - 每个文件一次 LLM 调用，结果合并进主 Violation 列表
    - 来源标记为 "llm-judge"，confidence < 1.0
    """

    # 试点区：只在这些路径前缀下运行 LLM Judge
    DEFAULT_PILOT_PATHS: tuple[str, ...] = (
        "src/omnifactory/packages/",
        "src/omnifactory/runtime/",
    )

    _SYSTEM = """\
你是 OmniGuardian 的 LLM 裁判员，专门检测静态规则引擎无法覆盖的架构违规。

【你要检查的违规类型】
OMNI-002 CRITICAL: 业务代码出现在 src/omnifactory/runtime/ 框架层根目录（非框架核心文件）
OMNI-003 CRITICAL: 绕过 LLMClient 直接调用 LLM SDK，包括：
  - 直接导入: import anthropic / from anthropic import ...
  - 别名导入: import anthropic as ai
  - 动态导入: importlib.import_module("anthropic") / __import__("anthropic")
  - exec/eval: exec("import anthropic")
  - 调用非 LLMClient 的 LLM API 端点（openai.OpenAI(), anthropic.Anthropic()）
OMNI-004 HIGH: Router 子类（class XxxRouter(Router)）的 run() 是 async def 但方法体内没有任何 await
OMNI-005 HIGH: 代码将 .db 文件写入 data/ 之外的路径
OMNI-006 MEDIUM: 临时/调试脚本（以 test_/scratch_/tmp_/debug_ 开头）渗入 src/ 目录
OMNI-013 HIGH: 绕过 omnifactory.core.guarded_write.write_file 直接用 Path.write_text /
  open(..,"w") 写文件。注意豁免：
    - src/omnifactory/core/guarded_write.py / omnimark.py 自身（唯一裸写入口）
    - src/omnifactory/bus/*.py（SQLite 底层必须裸写）
    - tool_executor.py 的 undo backup write 分支（可接受）
    - except 路径里做 error recovery 的 write（可接受）
OMNI-PIPELINE-IF (你判断): pipeline 文件（packages/<domain>/pipeline.py / run.py / routers.py）
  是否通过标准 OmniCompany 接口构建？标准接口包括:
    - Router 子类必须继承 omnifactory.runtime.routing.router.Router (这是当前唯一的 Router 定义位置)
    - Format 必须构造自 omnifactory.protocol.format.Format
    - pipeline.py 必须返回 omnifactory.protocol.pipeline.PipelineSpec
    - 不允许自造 run loop / 自造事件总线
  若发现 Router 类没有继承 Router 基类、或 pipeline 文件里自造了跑 DAG 的代码，
  报告为 OMNI-PIPELINE-IF, severity=HIGH。
  【重要】protocol 包下没有 router.py — 从 runtime.routing.router 导入 Router 是合规的,
  不要把这个当违规。
OMNI-NEW (你自己发现的违规): 可能存在其他架构问题，请自行判断并报告，severity 自定

【特别注意】规则引擎已经检查了直接 import，你要重点关注它看不见的：
- 别名/动态/exec 形式的 LLM SDK 调用
- 通过 subprocess 产生或修改文件的代码（尤其是生成代码到 src/ 下的行为）
- 任何试图绕过架构约束的创意写法
- 自造 pipeline 框架，不使用 omnifactory.protocol.* 标准接口

【输出格式】严格 JSON，无 markdown fence，无注释：
{
  "has_violation": true/false,
  "violations": [
    {
      "rule_id": "OMNI-003",
      "severity": "CRITICAL",
      "confidence": 0.95,
      "reasoning": "第 12 行 import anthropic as ai，第 18 行 ai.Anthropic() 直接创建客户端"
    }
  ],
  "clean_reasoning": "如无违规，一句话说明文件看起来合规的原因"
}
"""

    _USER_TEMPLATE = """\
文件路径: {path}
变更类型: {change_type}（A=新增 M=修改 D=删除）
{rule_hits_hint}
文件内容（最多 200 行）:
```python
{content}
```"""

    _RULE_HITS_HINT = "规则引擎已命中: {hits}（这些已记录，请重点检查规则引擎看不见的变体）\n"
    _NO_HITS_HINT = "规则引擎未命中任何规则（请全面审查）\n"

    def __init__(
        self,
        pilot_paths: tuple[str, ...] | None = None,
        max_file_lines: int = 200,
    ):
        self._pilot_paths = pilot_paths or self.DEFAULT_PILOT_PATHS
        self._max_lines = max_file_lines
        self._counter_offset = 0  # 由外部 RuleEngine 注入，确保 ticket_id 全局唯一

    def _in_pilot_zone(self, ctx: FileContext) -> bool:
        return any(ctx.path.startswith(p) for p in self._pilot_paths)

    def _should_judge(self, ctx: FileContext, new_files_only: bool = False) -> bool:
        """决定某个文件是否值得 LLM 审查。"""
        if not ctx.content:  # 删除文件 / 无法读取
            return False
        if not ctx.path.endswith(".py"):
            return False
        if not self._in_pilot_zone(ctx):
            return False
        if "_graveyard" in ctx.path or "_archive" in ctx.path:
            return False
        if new_files_only and ctx.change_type not in ("A", "?"):
            return False
        return True

    def _call_llm(self, ctx: FileContext, rule_hits: list[str] | None = None) -> dict | None:
        """对单个文件运行 LLMJudgeAgent，返回解析后的 dict，失败返回 None。

        2026-04-09 重构: 从单轮 LLM + 200 行截断改为 AgentNodeLoop + 工具。
        agent 有 read_file/grep/think 工具，会主动读全文件 + grep 查证符号定义。
        这修复了 knowledge/store.py (401 行) 类文件的 4 条假阳性事故。
        """
        try:
            import asyncio
            from .llm_judge_agent import LLMJudgeAgent

            file_lines = len((ctx.content or "").splitlines())

            agent = LLMJudgeAgent()
            verdict = asyncio.run(agent.run({
                "path": ctx.path,
                "abs_path": ctx.abs_path,
                "change_type": ctx.change_type,
                "rule_hits": rule_hits or [],
                "file_lines": file_lines,
            }))

            if verdict.output is None:
                return None
            return verdict.output

        except Exception as e:
            logger.warning("LLMJudge: %s 审查失败: %s", ctx.path, e)
            return None

    def judge(
        self,
        files: list[FileContext],
        new_files_only: bool = True,
        counter_start: int = 0,
        rule_violations: list[Violation] | None = None,
    ) -> list[Violation]:
        """对符合条件的文件逐个调用 LLM，返回 LLM 发现的违规列表。

        Layer 2 设计原则：LLM 知道规则引擎已经抓到了什么，专注于填补盲区
        （别名导入、动态 import、exec/eval、subprocess 写文件等）。

        Args:
            files:            待审查的文件列表
            new_files_only:   True 则只审查 change_type=A/? 的新增文件（更聚焦）
            counter_start:    全局 ticket 计数起点（接续 RuleEngine 的计数）
            rule_violations:  规则引擎已产出的违规列表（用于构建"已命中"提示）
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        date_str = now[:10]
        counter = counter_start
        violations: list[Violation] = []

        # 构建 path → [rule_ids] 映射，让 LLM 知道规则引擎已命中了哪些
        rule_hits_by_path: dict[str, list[str]] = {}
        if rule_violations:
            for v in rule_violations:
                rule_hits_by_path.setdefault(v.path, []).append(v.rule_id)

        candidates = [f for f in files if self._should_judge(f, new_files_only)]
        if not candidates:
            logger.debug("LLMJudge: 试点区内无候选文件，跳过")
            return []

        logger.info("LLMJudge: 对 %d 个文件进行 LLM 审查...", len(candidates))

        for ctx in candidates:
            hits = rule_hits_by_path.get(ctx.path)
            result = self._call_llm(ctx, rule_hits=hits)
            if result is None:
                continue

            if not result.get("has_violation"):
                logger.debug("LLMJudge: %s 合规 (%s)", ctx.path,
                             result.get("clean_reasoning", ""))
                continue

            for v_raw in result.get("violations", []):
                rule_id = v_raw.get("rule_id", "OMNI-NEW")
                severity = v_raw.get("severity", "HIGH")
                confidence = float(v_raw.get("confidence", 0.8))
                reasoning = v_raw.get("reasoning", "")

                counter += 1
                ticket_id = f"TICKET-{date_str}-LLM-{counter:03d}"
                message = f"[LLM-Judge] {reasoning}"

                violations.append(Violation(
                    ticket_id=ticket_id,
                    rule_id=rule_id,
                    severity=severity,
                    path=ctx.path,
                    message=message,
                    disposition=["warn"],
                    confidence=confidence,
                    detected_at=now,
                ))
                logger.warning(
                    "LLMJudge VIOLATION %s %s %s (conf=%.2f): %s",
                    ticket_id, severity, ctx.path, confidence, reasoning,
                )

        return violations
