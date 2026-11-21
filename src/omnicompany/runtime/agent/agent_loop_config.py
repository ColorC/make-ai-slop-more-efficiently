# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.agent.loop_config.dataclasses.py"
"""agent_loop_config — AgentNodeLoop 配置体系

对齐 Claude Code v2.1.88 的参数量级，不保守。
所有配置通过 dataclass 暴露，子类可完全覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════
# API 重试配置
# ═══════════════════════════════════════════════════════════

@dataclass
class RetryConfig:
    """API 重试（对齐 CC withRetry）。

    CC 默认: max_retries=10, base_delay=500ms, max_delay=32s, jitter=0.25
    """

    max_retries: int = 10
    base_delay_ms: int = 500
    max_delay_ms: int = 32_000
    jitter_factor: float = 0.25
    retry_on_overload: bool = True
    fallback_model: str | None = None
    fallback_after_attempts: int = 3


# ═══════════════════════════════════════════════════════════
# 上下文压缩配置
# ═══════════════════════════════════════════════════════════

@dataclass
class CompactConfig:
    """四层上下文压缩配置（2026-04-18 按 CC 语义校准）。

    历史 bug（详见 `docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/bd1_cc_alignment_audit.md`）：
    旧默认 `aging_threshold=5` 每轮触发，而 CC 的 microCompact 默认 **disabled**
    且只在 `time gap > 60 min` 时触发。artcontest 跑 29 轮（全程 <5 分钟）场景下
    CC 本不该 aging，而旧实现每轮都老化，导致 qwen submit 时看不到主 Lua 原文 →
    95.9% 函数名幻觉率。

    CC 对齐策略：
    - L1 aging 默认 **off**（aging_threshold=0），与 CC `enabled: false` 对齐
    - 若业务真需要 aging（长会话），改用 COMPACT_PRESET + time-based 触发
    """

    # L1: 工具结果老化（CC microCompact 对齐）
    aging_threshold: int = 0
    """**默认 0 即禁用**（2026-04-18 改）。保留最近 N 轮 assistant 消息之前的工具结果会被
    老化为摘要。>0 时才启用，且仍按"轮数"语义（非 CC 的 time gap；time-based 见下方 L1b）。

    **警告**：除非业务场景明确不适合保留全量 tool_result（超长对话 / 频繁 read 大文件），
    否则不要改回。改回前读 BD.1 审计。
    """

    aged_message: str = "[已执行，结果已省略]"

    # L1b: time-based aging（对齐 CC time-based microCompact，默认 off）
    time_based_aging_enabled: bool = False
    """time gap > gap_threshold_minutes 时触发（对齐 CC 默认 False）"""
    time_based_gap_threshold_minutes: int = 60
    """CC 默认 60 分钟：服务端 1h cache TTL 已必过期"""
    time_based_keep_recent_tool_results: int = 5
    """time-based 触发时，保留最近 N 个 compactable tool_result（按个数，非轮数）"""

    # L2: 单条截断
    max_tool_output: int = 20_000
    """单个工具结果最大字符数。CC 动态，我们给 20K 够用"""

    truncation_strategy: str = "head_tail"
    """截断策略: "head_tail" 保头尾 | "head" 只保头 | "tail" 只保尾"""

    # L3: 滑动窗口
    max_messages: int = 200
    """最大消息条数（2026-04-18 从 120 提到 200，EVIDENCE_DRIVEN 默认）。
    100 轮 × 约 3 条/轮 × 余量。长对话 agent 场景再提。"""

    # L4: LLM 自动压缩
    auto_compact_enabled: bool = True
    auto_compact_threshold: float = 0.90
    """token 占用率阈值（2026-04-18 从 0.95 降到 0.90 对齐 CC 预留 output buffer）。
    CC 用 contextWindow - 13K - 20K output 预留，相当于约 83-90%。"""

    auto_compact_output_reserve_tokens: int = 20_000
    """autoCompact 触发前为 summary 生成预留的 output tokens（对齐 CC MAX_OUTPUT_TOKENS_FOR_SUMMARY=20K）"""

    auto_compact_max_failures: int = 3
    """连续压缩失败次数达到此值后熔断（CC: 3）"""

    compact_model: str | None = None
    """压缩用的模型（None=使用主模型，可设为更便宜的模型）"""

    compact_preserve_turns: int = 3
    """压缩后保留最近 N 轮原始消息不压缩"""

    # L4 扩展: 行为保全摘要
    enable_compression_summary: bool = True
    """L4 压缩前生成行为保全摘要，记录 agent 做了什么（用于轨迹归纳）"""

    compression_summary_db_path: str = "data/intent_traces.db"
    """行为保全摘要写入的数据库路径（复用 intent_traces.db）"""


# ═══════════════════════════════════════════════════════════
# Compact Preset（BD.3 / D4 修：业务层声明策略）
# ═══════════════════════════════════════════════════════════

EVIDENCE_DRIVEN_COMPACT = CompactConfig(
    aging_threshold=0,
    max_messages=200,
    auto_compact_threshold=0.90,
)
"""证据型任务（Prefab 语义识别 / 代码分析 / findings 生成）：
aging 禁用，靠 L2 单条截断 + L4 LLM 压缩兜底。submit 时能看到所有 evidence。"""

CONVERSATIONAL_COMPACT = CompactConfig(
    aging_threshold=0,
    time_based_aging_enabled=True,
    max_messages=80,
    auto_compact_threshold=0.85,
)
"""对话型任务（长 REPL、帮助型 assistant）：time-based aging 打开（60 min gap），
滑窗较短，auto_compact 早触发。"""

DISABLED_COMPACT = CompactConfig(
    aging_threshold=0,
    max_messages=10_000,
    auto_compact_enabled=False,
    time_based_aging_enabled=False,
)
"""全部关闭（toy / smoke 测试 / 希望 full 原始 messages 落盘）"""


# ═══════════════════════════════════════════════════════════
# 权限配置
# ═══════════════════════════════════════════════════════════

@dataclass
class PermissionConfig:
    """工具权限（对齐 CC 五层权限中的核心三层）。

    mode:
      "bypass"   — 全部放行（信任模式，撤下所有安全网）
      "default"  — DeathZone 硬规则拦截，其余放行
      "readonly" — 只允许只读工具
      "strict"   — 非只读工具需要 AI 分类器确认
    """

    mode: str = "default"
    always_allow: list[str] = field(default_factory=list)
    always_deny: list[str] = field(default_factory=list)
    ai_classifier_enabled: bool = False
    ai_classifier_model: str | None = None


# ═══════════════════════════════════════════════════════════
# 总配置
# ═══════════════════════════════════════════════════════════

@dataclass
class LoopConfig:
    """AgentNodeLoop 总配置。"""

    max_turns: int = 100
    """最大 LLM 调用轮数。不保守，实际任务经常需要几十轮"""

    context_window: int = 200_000
    """上下文窗口大小（token）。Opus/Sonnet 均 200K"""

    retry: RetryConfig = field(default_factory=RetryConfig)
    compact: CompactConfig = field(default_factory=CompactConfig)
    permission: PermissionConfig = field(default_factory=PermissionConfig)

    # 工具并发
    enable_tool_concurrency: bool = True
    max_concurrent_tools: int = 10
    """最大并发工具执行数（CC: 10）"""

    # 预算
    budget_warning_threshold: float = 0.9
    """到 90% max_turns 时注入预算警告"""


# ═══════════════════════════════════════════════════════════
# 预设
# ═══════════════════════════════════════════════════════════

PRESET_LIGHTWEIGHT = LoopConfig(
    max_turns=10,
    compact=CompactConfig(auto_compact_enabled=False),
    permission=PermissionConfig(mode="readonly"),
)

PRESET_STANDARD = LoopConfig(
    max_turns=100,
)

PRESET_HEAVY_DUTY = LoopConfig(
    max_turns=200,
    compact=CompactConfig(auto_compact_threshold=0.85),
    permission=PermissionConfig(mode="strict", ai_classifier_enabled=True),
    retry=RetryConfig(max_retries=10),
)

PRESET_UNRESTRICTED = LoopConfig(
    max_turns=100,
    permission=PermissionConfig(mode="bypass"),
)
