# [OMNI] origin=claude-code domain=services/plan_audit ts=2026-06-19T00:00:00Z type=service
# [OMNI] material_id="material:core.plan_audit.audit_agents.agent_node_loop.py"
"""plan_audit.auditor — 落地审计 AgentNodeLoop 子类.

ConversationAuditor (输入(1)):
  读完整对话 → 列出用户每一条真实指示(只数 role:user, 排除工具回灌) → 对每条用
  read_file/grep/glob/list_dir/bash(git log/find/ls) 在「对话后续」+「实际硬盘」双查落地 →
  输出每条: 指示 / 状态(DONE/PARTIAL/PENDING) / 证据 → 末尾汇总未落地清单.

PlanAuditor (输入(2)):
  在 ConversationAuditor 基础上多吃 plan.md 原文 + exit_criteria + 多个候选对话,
  先判断每个对话是否真"在执行/起草"该 plan, 再对相关对话核对指示落地, 叠加 exit_criteria.

铁律(写进 NODE_PROMPT): 未落地 ≠ 落地后又删. 判定看对话历史里是否出现过落地动作
(改文件/建产物), 一旦出现过就算 DONE/PARTIAL, 哪怕现在硬盘上没了.
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
    RetryConfig,
)
from omnicompany.packages.services._core.agent import (
    AgentNodeLoop,
    GlobRouter,
    GrepRouter,
    ReadFileRouter,
    ListDirRouter,
    DevBashRouter,
)
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter

logger = logging.getLogger(__name__)


# 全读 + 受限 bash. DevBashRouter 已禁 git commit/push/reset、rm -rf、p4 写 —— 只读审计正好.
# bash 用于 git log/git show/find/ls 这类 grep 工具覆盖不到的"历史落地动作"取证.
AUDIT_TOOL_ROUTERS: list = [ReadFileRouter, GrepRouter, GlobRouter, ListDirRouter, DevBashRouter]


# ════════════════════════════════════════════════════════════════════════
# 共用 NODE_PROMPT 片段
# ════════════════════════════════════════════════════════════════════════

_LANDING_RULE = """\
【核心铁律 — 落地判定语义(必须严格执行)】
"未落地" **不包括** "落地了然后又被删/重构挪走" 的内容.
判定一条指示是否落地, 要看【对话历史里是否出现过落地动作】(agent 改过某文件 / 建过某产物 /
跑通过某命令), 一旦在对话中出现过该落地动作, 就算 **DONE**(或 PARTIAL), 哪怕现在硬盘上
已经没有那个文件了(可能后来被删/重命名/重构挪走).
反例(错误判法): 只 grep 当前硬盘状态, 发现某文件不在了 → 误判 PENDING. 这是错的.
正确判法: 双证据源 ——
  (A) 对话证据: 在对话后续的 assistant 消息 / 工具调用里, 是否出现过"创建/编辑/写入了 X""跑通了 Y".
  (B) 硬盘证据: 用 read_file/grep/glob/list_dir/bash(git log -- <path> / git show / find / ls)
      查当前硬盘 + git 历史. git log/git show 能查到"做过又删"的提交, 这正是区分
      "从没做" 和 "做过又删" 的关键工具.
只有当 (A) 和 (B) **都没有任何落地痕迹**(从没在对话里做过, git 历史里也查不到做过的提交)时,
才判 **PENDING**. 任何一边有"做过"的痕迹, 就不是未落地.
"""

_INSTRUCTION_EXTRACTION = """\
【第一步: 提取用户指示】
逐步通读整段对话, 列出 **用户(role:user)发出的每一条真实指示/需求**. 注意:
- 只数用户真实意图表达(要做什么 / 要改什么 / 要满足什么标准 / 纠正了什么方向).
- **排除** 工具回灌、系统提醒、`[from: ... not_user: true]` 之类的注入消息、纯确认("好""继续"
  这种无新信息的不单列, 但"继续重置"这类触发词若带新约束要并入相关指示).
- 一条用户消息可能含多条指示, 拆开逐条列.
- 保留指示原意, 不要替用户脑补需求.
"""

_VERIFY_AND_OUTPUT = """\
【第二步: 逐条核对落地】
对提取出的每一条指示, 主动调工具双查(对话证据 + 硬盘/git 证据), 判定状态:
- DONE    : 完全落地(对话里做过 且/或 硬盘+git 能证实做过, 满足指示).
- PARTIAL : 部分落地(做了一部分, 或做了但不满足指示全部要求).
- PENDING : 完全未落地(对话里从没做过, git 历史也查不到做过). 严格按上面铁律, 别把"做过又删"误判进来.
取证要具体: 引用对话里的落地动作描述, 或贴 read_file/grep 命中的文件:行 / git log 的 commit.

【工作纪律】
- **对话存在文件里(首轮消息给了路径), 用 read_file 分片读(offset/limit), 别假设整段已在上下文; 读到文件末尾再下"指示提全了"的结论. 边读边记指示, 让 compact 管理上下文.**
- 先 list_dir / glob 摸清相关目录结构, 再精确 read_file / grep.
- bash 的 cwd: **直接用首轮消息里给你的『审计目标仓库根』那个绝对路径原样传入 cwd 参数**
  (例如 cwd='E:/WindowsWorkspace/omnicompany'). 不要自己拼接子目录, 不要在路径前后加多余的盘符或斜杠,
  否则会因路径不存在被拒. 要进子目录就在命令里 `cd sub && ...`, cwd 参数始终是那个仓库根.
  用途: `git log --oneline -- <path>` `git log --all --oneline -S '<symbol>'` `git show <sha> -- <path>`
  `find . -name ...` `ls`. bash 禁写操作(git commit/push、rm -rf 等会被拒), 这是只读审计, 别试图改任何东西.
- 不确定就多查一步, 别猜. 证据不足时状态标 PARTIAL 并说明缺什么证据, 不要硬判 DONE/PENDING.
- 预算有限(约 40 轮). 别在同一个失败命令上反复重试: 一个 bash 命令失败 2 次就换工具(read_file/grep)
  或换查法. 取够证据就尽快 finish 输出 JSON, 不要无限取证.

【输出格式】完成后调用 `finish` 工具, result 必须是严格 JSON(无 markdown fence):
{
  "instructions": [
    {
      "text": "用户指示原意(简洁转述)",
      "status": "DONE | PARTIAL | PENDING",
      "evidence": "具体证据: 对话里做过什么 + 硬盘/git 查到什么(文件:行 / commit / 命令输出要点)",
      "landed_then_removed": false
    }
  ],
  "not_landed": [
    "（仅 PENDING 的指示, 复述于此, 作为未落地清单）"
  ],
  "summary": "一段话总览: 共几条指示, DONE/PARTIAL/PENDING 各几条, 关键未落地项是什么."
}
注意: 如果某条是"做过又删/重构挪走", 它 status 不是 PENDING(按铁律), 但请把
landed_then_removed=true 标出来, 并在 evidence 里说清"曾经做过(对话/commit 证据), 现已不在硬盘".
"""


_CONVERSATION_NODE_PROMPT = (
    "你是 OmniCompany 的【第三方 plan audit 审计 Agent】. 你有全部只读权限 + 受限 bash, "
    "你的任务是审计一段【对话】里用户发出的所有指示的落地情况.\n\n"
    + _INSTRUCTION_EXTRACTION + "\n"
    + _LANDING_RULE + "\n"
    + _VERIFY_AND_OUTPUT
)


_PLAN_NODE_PROMPT = (
    "你是 OmniCompany 的【第三方 plan audit 审计 Agent】. 你有全部只读权限 + 受限 bash. "
    "这次输入是一个 **plan** + 若干【候选对话】(它们读/写/提到过这个 plan). 你的任务:\n\n"
    "【第零步: 筛真正在执行/起草该 plan 的对话】\n"
    "候选对话只是『提到过 plan 关键词』的, 不一定真在执行. 逐个判断: 这个对话是不是真的在"
    "起草 / 推进 / 执行这个 plan(而非只是顺带提了一句 / 在做别的事)? 把不相关的剔掉, 说明理由.\n\n"
    "【然后对每个相关对话 + plan 本身做落地核对】\n"
    + _INSTRUCTION_EXTRACTION
    + "(plan 场景下, 用户指示来自这些相关对话; 同时把 plan.md 的 exit_criteria 当作额外的待核对项.)\n\n"
    + _LANDING_RULE + "\n"
    + _VERIFY_AND_OUTPUT
    + "\nplan 场景额外要求: 在 instructions 里把 plan 的 exit_criteria 逐条作为条目核对落地(text "
    "前缀 '[exit_criteria] '); summary 里点明 plan 整体推进到什么程度、哪些 exit_criteria 还没满足.\n"
    "并在 JSON 顶层加一个 \"relevant_conversations\" 字段: "
    "[{\"session_id\":..., \"is_executing_plan\": true/false, \"reason\": ...}].\n"
)


# ════════════════════════════════════════════════════════════════════════
# PromptBuilder: 把对话/plan 拼成首轮 user message
# ════════════════════════════════════════════════════════════════════════


class _ConversationAuditPromptBuilder(PromptBuilderRouter):
    """首轮 user message: 给对话文件路径 + 分片读指引(对话内容走 read_file 分片, 不塞 prompt)."""

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        cwd = input_data.get("cwd", "")
        provider = input_data.get("provider", "")
        session_id = input_data.get("session_id", "")
        tfile = input_data.get("transcript_file", "")
        n = input_data.get("message_count", "?")
        chars = input_data.get("char_count", "?")
        trunc = "(尾部已截断)" if input_data.get("truncated") else ""
        content = [
            f"待审计对话: provider={provider} session_id={session_id}",
            f"对话工作目录(cwd): {cwd or '(未知)'}",
            f"审计目标仓库根(你 bash 的 cwd 用这个): {input_data.get('repo_root', cwd)}",
            "",
            "对话已存成文件(仅用户直接输入 + 助理直接文本输出, 已剔除工具调用/工具返回/思考/compact):",
            f"  {tfile}",
            f"  共 {n} 条消息, 约 {chars} 字符 {trunc}",
            "",
            "**别期望整段对话已在你上下文里** —— 用 read_file 分片读这个文件(offset/limit, 一次读一段),",
            "读一段就提取该段里用户发出的指示, 继续往后读直到文件末尾(看 read_file 回报的总行数, 确保读全),",
            "边读边用 grep/glob/list_dir/bash(git log/show) 核查落地. 读完+核查完再 finish 输出 JSON.",
        ]
        return [{"role": "user", "content": "\n".join(content)}]


class _PlanAuditPromptBuilder(PromptBuilderRouter):
    """把 plan.md + exit_criteria + 多个候选对话拼成首轮 user message."""

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        plan_id = input_data.get("plan_id", "")
        plan_md = input_data.get("plan_md", "")
        exit_criteria = input_data.get("exit_criteria") or []
        repo_root = input_data.get("repo_root", "")
        convos = input_data.get("conversations") or []

        parts: list[str] = [
            f"待审计 plan: {plan_id}",
            f"审计目标仓库根(你 bash 的 cwd 用这个): {repo_root}",
            "",
            "===== plan.md 原文(开始) =====",
            plan_md or "(plan.md 读不到内容)",
            "===== plan.md 原文(结束) =====",
            "",
        ]
        if exit_criteria:
            parts.append("plan frontmatter 的 exit_criteria:")
            for c in exit_criteria:
                parts.append(f"  - {c}")
            parts.append("")
        parts.append(f"下面是 {len(convos)} 个候选对话(读/写/提到过这个 plan), 各已存成文件. 用 read_file 分片读, 逐个判断是否真在执行/起草该 plan:")
        for idx, c in enumerate(convos):
            parts.append(
                f"  #{idx} provider={c.get('provider')} session_id={c.get('session_id')} "
                f"match_reason={c.get('match_reason')} 文件={c.get('transcript_file')} "
                f"({c.get('message_count', '?')} 条, ~{c.get('char_count', '?')} 字符)"
            )
        parts.append("")
        parts.append("**别期望对话内容已在上下文里** —— 用 read_file 分片读每个文件(offset/limit), 长就多读几次, compact 会帮你管理上下文. 读全再按 SYSTEM 流程作业, 最后 finish 输出 JSON.")
        return [{"role": "user", "content": "\n".join(parts)}]


# ════════════════════════════════════════════════════════════════════════
# ExtractResult: parse 最终 JSON
# ════════════════════════════════════════════════════════════════════════


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
        if t.startswith("json"):
            t = t[4:].strip()
    return t


class _AuditExtractResult(ExtractResultRouter):
    """parse audit agent 的最终 JSON, fallback 到带 raw_text 的 PARTIAL verdict."""

    def extract(self, *, final_text: str, messages: list[dict], turn_count: int, stop_reason: str) -> Verdict:
        text = _strip_fence(final_text)
        try:
            parsed = json.loads(text)
            parsed.setdefault("instructions", [])
            parsed.setdefault("not_landed", [])
            parsed.setdefault("summary", "")
            kind = VerdictKind.PASS
            if stop_reason == "max_turns":
                kind = VerdictKind.PARTIAL
            return Verdict(kind=kind, output=parsed)
        except Exception as e:
            logger.warning("[plan_audit] 输出 JSON 解析失败: %s\n原始: %s", e, text[:400])
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output={
                    "instructions": [],
                    "not_landed": [],
                    "summary": "（agent 输出未能解析为 JSON, 见 raw_text）",
                    "parse_error": str(e),
                    "raw_text": final_text,
                },
                diagnosis=f"audit 输出解析失败: {e}",
            )


# ════════════════════════════════════════════════════════════════════════
# Agent 子类
# ════════════════════════════════════════════════════════════════════════

# 审计需要多轮(逐条指示取证), 给足 turns; 对话很长靠 L2 单条截断 + L4 LLM 压缩兜底.
_AUDIT_LOOP_CONFIG = LoopConfig(
    max_turns=60,
    compact=CompactConfig(
        aging_threshold=0,
        max_messages=240,
        max_tool_output=24_000,
        auto_compact_enabled=True,
        auto_compact_threshold=0.88,
    ),
    # permission readonly: 我们的工具集本就是全读 + DevBash(自带写操作黑名单), 是只读取向.
    permission=PermissionConfig(mode="readonly"),
    retry=RetryConfig(max_retries=6),
)


class _BaseAuditor(AgentNodeLoop):
    """共用: 注入 allowed_bash_roots(DevBashRouter 需要), 走只读审计."""

    TOOL_ROUTERS: ClassVar[list] = AUDIT_TOOL_ROUTERS
    LOOP_CONFIG: ClassVar[LoopConfig] = _AUDIT_LOOP_CONFIG

    def __init__(self, *, model: str | None = None, bus: Any | None = None, config: LoopConfig | None = None):
        super().__init__(model=model, bus=bus, config=config or self.LOOP_CONFIG)

    def build_extract_result(self, *, bus: Any) -> ExtractResultRouter:
        return _AuditExtractResult(bus=bus)

    def build_tool_context(self, *, input_data: dict, turn: int, trace_id: str) -> dict:
        ctx = super().build_tool_context(input_data=input_data, turn=turn, trace_id=trace_id)
        # DevBashRouter 要 allowed_bash_roots, 否则一切 bash 被拒. 审计放行仓库根(只读命令).
        from omnicompany.core.config import omni_workspace_root

        repo_root = input_data.get("repo_root") or str(omni_workspace_root())
        roots = [repo_root]
        cwd = input_data.get("cwd")
        if cwd and cwd not in roots:
            roots.append(cwd)
        ctx["allowed_bash_roots"] = tuple(roots)
        # bash 没传 cwd 时的默认 cwd
        ctx["cwd"] = repo_root
        return ctx


class ConversationAuditor(_BaseAuditor):
    """输入(1): 审计一段对话里用户每条指示的落地情况.

    run() input_data:
        {
          "transcript": [{role, text}, ...],   # load_full_transcript 的产物
          "truncated": bool,
          "cwd": "...",                         # 对话原工作目录
          "repo_root": "...",                   # 审计目标仓库根(bash cwd)
          "provider": "claude_code"|"codex",
          "session_id": "...",
          "trace_id": "...", "session_id": ...,
        }
    """

    NODE_PROMPT: ClassVar[str] = _CONVERSATION_NODE_PROMPT
    DESCRIPTION = "AgentNodeLoop: 对话指示落地审计(全读 + 受限 bash, git 历史取证区分'做过又删')"

    def build_prompt_builder(self, *, bus: Any) -> PromptBuilderRouter:
        return _ConversationAuditPromptBuilder(template=self.NODE_PROMPT, bus=bus)


class PlanAuditor(_BaseAuditor):
    """输入(2): 审计一个 plan + 相关对话的落地情况(叠加 exit_criteria).

    run() input_data:
        {
          "plan_id": "...",
          "plan_md": "...",                      # plan.md 原文
          "exit_criteria": [...],                # frontmatter 解析
          "repo_root": "...",
          "conversations": [{provider, session_id, transcript, truncated, match_reason}, ...],
          "trace_id": ...,
        }
    """

    NODE_PROMPT: ClassVar[str] = _PLAN_NODE_PROMPT
    DESCRIPTION = "AgentNodeLoop: plan 落地审计(筛真执行对话 + 指示/exit_criteria 落地核对)"

    def build_prompt_builder(self, *, bus: Any) -> PromptBuilderRouter:
        return _PlanAuditPromptBuilder(template=self.NODE_PROMPT, bus=bus)
