# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.llm.multi_protocol_client.registry_and_dispatcher.implementation.py"
"""多协议 LLM 客户端 + 多模型注册表

V4: 自动检测 Anthropic / OpenAI 协议。DashScope 用 Anthropic SDK，the_company proxy 用 OpenAI SDK。
模型分配本身也是可进化参数 — 元进化可以调整哪个角色用哪个模型。
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # anthropic SDK 导入约 436ms, 仅类型检查需要; 运行时在各用到处懒导入(对齐下方 openai)
    import anthropic


logger = logging.getLogger(__name__)

# 限流重试配置
_RETRY_INITIAL_DELAY = 30
_RETRY_MAX_DELAY = 300


# ── 审计上下文 contextvar (Phase 2.5 关联机制, 2026-04-09) ──
# Runner 在执行节点前 set 一次, 该节点内所有 LLMClient.call() 调用自动继承
# audit_context (trace_id / pipeline_id / node_id), 无需每次调用显式传参。
# 通过 Python contextvars 在 async 任务间正确传递。
_AUDIT_CONTEXT_VAR: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "llm_audit_context",
    default={},
)

# 最近一次 LLM 调用的 info_audit 结果 — 供 runner 在 Router.run() 返回后兜底读取。
# 设计理由: 14 个 Router 子类不读 resp.info_audit, 写入 Verdict 时不填 info_audit,
# runner 只拿到 verdict.info_audit=None。为避免改 14 个文件, 这里让 LLMClient.call()
# 每次调用都更新此 contextvar, runner 在 router.run 返回后若 verdict.info_audit 空,
# 就从这里兜底取最后一次 LLM 调用的 audit。
#
# 限制: 一个节点内多次 LLM 调用时, 只保留最后一次的 audit。够用 — 一个节点的
# info_audit 本来就是对"节点本次完成任务"的整体评估, 关心的是最后一次。
_LAST_INFO_AUDIT_VAR: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "llm_last_info_audit",
    default=None,
)

_AI_PLAYER_PLANNER_MEASUREMENT_HOOK_VAR: contextvars.ContextVar[Any | None] = (
    contextvars.ContextVar("ai_player_planner_measurement_hook", default=None)
)


@contextlib.contextmanager
def use_ai_player_planner_measurement_hook(hook: Any):
    """Bind a fail-closed callback to the exact completed LLM invocation.

    Unlike best-effort audit sinks, callback failures propagate and prevent the
    planner result from being returned as a usable command.
    """

    token = _AI_PLAYER_PLANNER_MEASUREMENT_HOOK_VAR.set(hook)
    try:
        yield
    finally:
        _AI_PLAYER_PLANNER_MEASUREMENT_HOOK_VAR.reset(token)


@contextlib.contextmanager
def use_audit_context(ctx: dict[str, str] | None):
    """临时设置当前活动的 audit context。通常由 TeamRunner 在调度节点时使用。

    用法:
        with use_audit_context({"trace_id": t, "pipeline_id": p, "node_id": n}):
            verdict = router.run(input_data)
        # 退出 with 后自动还原

    进入时同时清空 `_LAST_INFO_AUDIT_VAR`, 让节点执行完后能读到"仅本节点内"
    的最后一次 audit, 不会看到上一节点的残留。
    """
    ctx_token = _AUDIT_CONTEXT_VAR.set(ctx or {})
    audit_token = _LAST_INFO_AUDIT_VAR.set(None)
    try:
        yield
    finally:
        _AUDIT_CONTEXT_VAR.reset(ctx_token)
        _LAST_INFO_AUDIT_VAR.reset(audit_token)


def get_audit_context() -> dict[str, str]:
    """读取当前活动的 audit context (供 LLMClient.call 内部使用)。"""
    return dict(_AUDIT_CONTEXT_VAR.get() or {})


def get_last_info_audit() -> Any:
    """读取当前 audit_context 作用域内最后一次 LLM 调用产生的 InfoAuditReport。

    设计供 TeamRunner 在 `router.run()` 返回后兜底读取, 弥补 14 个 Router
    子类不自主提取 info_audit 的 plumbing gap。返回 None 表示本节点内没有 LLM
    调用或没开 piggyback。
    """
    return _LAST_INFO_AUDIT_VAR.get()
_RETRY_MAX_ATTEMPTS = 5
_RETRY_BACKOFF_FACTOR = 2.0

# 流式调用默认不设总 wall-clock 上限。长思考、长文和多工具参数
# 只要仍有 stream event 就属于正常活动，不能因累计时长被截断。
# 传输层 request/read timeout 仍会捕获真正的“无返回”；特定管线也可显式
# 传入两个 deadline 做更严格的无进展保护。
_STREAM_WALL_CLOCK_DEADLINE: float | None = None
_STREAM_IDLE_CHUNK_DEADLINE: float | None = None


def _report_stream_progress(callback: Any | None, event: str, **data: Any) -> None:
    """Best-effort synchronous stream telemetry hook.

    LLMClient runs synchronously, usually inside a worker thread.  A small
    synchronous callback lets the Agent layer bridge progress into its async
    EventBus without coupling this protocol client to a bus implementation.
    """
    if callback is None:
        return
    try:
        callback({"event": event, **data})
    except Exception:
        logger.debug("LLM stream progress callback failed", exc_info=True)


# ---------------------------------------------------------------------------
# RateLimiter — 令牌桶，按 base_url 分组
# ---------------------------------------------------------------------------


class RateLimiter:
    """Per-endpoint rate limiter with **dual constraint**: token bucket + min interval.

    2026-04-10 重构: 原来的纯 token bucket 允许 burst (120/min 令牌可以瞬间打光),
    the_company 聚合 API 的 per-model quota 经常被并发 burst 打爆 (sentinel 后台巡逻 +
    workflow-factory 主线程 + LLMJudgeAgent 并发时表现尤其明显)。

    新方案 — 两个约束必须同时满足才放行:

    1. **令牌桶**: 平均速率 ≤ max_per_minute (保持原有语义, 长时间空闲可攒出容量)
    2. **最小间隔**: 相邻两次 acquire() 间隔 ≥ 60 / max_per_minute 秒
       (错峰核心: 即使令牌充足, 也不允许同一瞬间连发两次)

    两重保证 = "稳态速率有上限 + 短时不扎堆"。

    所有 LLMClient 实例在同一进程内共享同一个 (endpoint → RateLimiter) 单例,
    所以主线程的 workflow-factory LLM 调用和后台 sentinel 线程的 LLMJudgeAgent
    调用会被同一把锁串起来错峰执行。
    """

    _instances: dict[str, "RateLimiter"] = {}
    _lock = threading.Lock()

    def __init__(self, max_per_minute: int = 35):
        self._max = max_per_minute
        self._min_interval = 60.0 / max_per_minute if max_per_minute > 0 else 0.0
        self._tokens = float(max_per_minute)
        self._last_refill = time.monotonic()
        self._last_acquire = 0.0  # 最近一次 acquire() 放行的时间
        self._mu = threading.Lock()

    @classmethod
    def for_endpoint(cls, base_url: str, max_per_minute: int = 35) -> "RateLimiter":
        with cls._lock:
            if base_url not in cls._instances:
                cls._instances[base_url] = cls(max_per_minute)
            return cls._instances[base_url]

    def acquire(self) -> None:
        """Block until BOTH: token available AND min_interval elapsed since last acquire."""
        while True:
            with self._mu:
                now = time.monotonic()
                # 令牌桶补充
                elapsed = now - self._last_refill
                self._tokens = min(
                    self._max, self._tokens + elapsed * (self._max / 60.0)
                )
                self._last_refill = now
                # 距上次 acquire 的间隔是否达到 min_interval
                gap = now - self._last_acquire
                need_wait = self._min_interval - gap
                if self._tokens >= 1.0 and need_wait <= 0:
                    self._tokens -= 1.0
                    self._last_acquire = now
                    return
                # 外层 sleep 的等待时间: 优先等最小间隔, 否则等一个令牌生成周期
                if need_wait > 0:
                    sleep_for = max(0.05, need_wait)
                else:
                    sleep_for = 1.0
            time.sleep(sleep_for)


# ---------------------------------------------------------------------------
# ModelRegistry — 角色→模型配置映射
# ---------------------------------------------------------------------------

# OpenAI SDK 会在 base_url 后追加 ``chat/completions``；代理的当前标准接口在 /v1。
# 少 /v1 会落到旧兼容入口,同一把有效 key 会被误报为 models=['limited']。
_THE_COMPANY_URL = "https://internal-llm-proxy.example.com/v1"
_KIMI_URL = os.environ.get("KIMI_BASE_URL", "https://api.kimi.com/coding/v1")


# key_env → default value（环境变量未设置时使用）
_KEY_DEFAULTS: dict[str, str] = {
    "THE_COMPANY_API_KEY": os.environ.get("THE_COMPANY_API_KEY", ""),
    "KIMI_API_KEY": os.environ.get("KIMI_API_KEY", ""),
}


def _resolve_key(key_env: str) -> str:
    return os.environ.get(key_env, _KEY_DEFAULTS.get(key_env, ""))


class ModelRegistry:
    """多模型注册表 — Role → Tier → Policy → Model 三层映射。

    ## 三层架构

        Role (调用者声明的用途)
          ↓
        Tier (能力需求类别)
          ↓
        Policy (全局策略选择) ← 从 OMNI_MODEL_POLICY 环境变量读取
          ↓
        Model (具体模型 + 端点)

    ## 切换策略

        OMNI_MODEL_POLICY=production   # 默认: quality 用 Sonnet，standard 用 glm-5
        OMNI_MODEL_POLICY=balanced     # quality 降到 glm-5
        OMNI_MODEL_POLICY=cheap        # 所有 tier 都用 qwen-flash 系列
        OMNI_MODEL_POLICY=robust_test  # 用 qwen3.5-flash 测试鲁棒性

    运行时也可以 `set_active_policy(name)` 切换。

    ## Fallback 链

    任何 role 的 chain = [policy-selected primary, *universal_fallbacks]。
    兜底只使用 the_company proxy；不再使用外部失效 endpoint。
    """

    # ── 模型目录（raw model configs）──────────────────────────────────────
    # the_company 聚合 API 定价对照（$ per 1M tokens, input/output）—— 2026-06-23 用户同步最新价目表：
    #   gpt-5.5-expensive     5.00 / 30.00   quality 顶配(贵)
    #   gpt-image-2           5.00 / 10.00   图像
    #   gemini-3-pro-image    2.00 / 12.00   图像(pro)
    #   gemini-3.1-pro        2.00 / 12.00   quality
    #   gpt-5.3-codex         1.75 / 14.00   代码
    #   qwen3.7-max           1.71 / 5.14
    #   gemini-3.5-flash      1.50 / 9.00
    #   glm-5.2               1.14 / 4.00
    #   claude-haiku-4-5      1.00 / 5.00
    #   kimi-k2.6             0.93 / 3.86
    #   kimi-k2.7-code        0.93 / 3.86    代码
    #   glm-5.1               0.86 / 3.43    standard 首选
    #   gemini-3-flash        0.50 / 3.00
    #   gemini-3.1-flash-image 0.50 / 3.00   图像(flash)
    #   gpt-5.5               0.50 / 3.00  ★2026-06-23 自建 agent 主力(便宜+够强)
    #   deepseek-v4-pro       0.43 / 0.86    cheap 稳定
    #   qwen3-max             0.36 / 1.43
    #   qwen3.6-plus          0.29 / 1.71    vision 常规
    #   gpt-5.4               0.25 / 1.50  ← 大降价(原 2.50/15.00)
    #   gemini-embedding-2    0.20 / 0.00    embedding
    #   gemini-embedding-001  0.15 / 0.00    embedding
    #   deepseek-v4-flash     0.14 / 0.29    ultra-cheap
    #   text-embedding-3-large 0.13 / 0.00   embedding
    #   qwen-flash            0.02 / 0.21    ultra-cheap
    _MODELS: dict[str, dict[str, str]] = {
        # ── the_company proxy (THE_COMPANY_API_KEY) — 2026-06-23 同步最新价目表 ──
        # 旧型号(claude-opus/sonnet/glm-5/kimi-k2.5/qwen3.5-* 等)仍保留: 历史 policy/fallback 引用,
        # 不在新表内的按旧价兜底估算; 新表以下方"★最新"段为权威。
        # quality tier (claude / gpt / opus)
        "claude-opus-4-6":      {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "claude-opus-4-7":      {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "claude-sonnet-4-6":    {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "claude-haiku-4-5-20251001": {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "claude-haiku-4-5@20251001": {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gpt-5.3-codex":        {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gpt-5.4":              {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gpt-5.5":              {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gpt-5.5-expensive":    {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gpt-5.6-terra":        {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        # standard tier (glm / kimi / qwen-max)
        "glm-5":                {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "glm-5.1":              {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "glm-5.2":              {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "kimi-k2.5":            {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "kimi-k2.6":            {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "kimi-k2.7-code":       {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        # Kimi Code direct endpoint. Keep these IDs separate from the the_company
        # aliases above so a caller can explicitly choose its credential path.
        "k3":                    {"base_url": _KIMI_URL, "key_env": "KIMI_API_KEY"},
        "kimi-for-coding":       {"base_url": _KIMI_URL, "key_env": "KIMI_API_KEY"},
        "kimi-for-coding-highspeed": {"base_url": _KIMI_URL, "key_env": "KIMI_API_KEY"},
        "qwen3-max":            {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "qwen3.7-max":          {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "qwen3.5-plus":         {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "qwen3.6-max-preview":  {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "qwen3.6-plus":         {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        # cheap / fast tier (deepseek / qwen-flash)
        "deepseek-v3-2-251201": {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "deepseek-v4-flash":    {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "deepseek-v4-pro":      {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "qwen3.5-flash":        {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "qwen-flash":           {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        # vision tier
        "qwen3-vl-flash":       {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gemini-3.1":                   {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gemini-3-flash-preview":       {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gemini-3.5-flash":             {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gemini-3.1-flash-lite-preview": {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gemini-3.1-pro-preview":       {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        # image / embedding（非对话, 计价用; role 一般不选）
        "gpt-image-2":                   {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gemini-3-pro-image-preview":    {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gemini-3.1-flash-image-preview": {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gemini-embedding-001":          {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "gemini-embedding-2":            {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
        "text-embedding-3-large":        {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"},
    }

    # ── Policies: tier → model (每个 policy 定义四个 tier 的首选模型) ─────
    _POLICIES: dict[str, dict[str, str]] = {
        "production": {
            "quality":  "qwen3.6-plus",        # 2026-04-09: 从 claude-sonnet-4-6 降到 qwen3.6-plus 省钱
            "standard": "glm-5",
            "cheap":    "qwen3.5-flash",
            "vision":   "qwen3-vl-flash",
        },
        "balanced": {
            "quality":  "glm-5",
            "standard": "glm-5",
            "cheap":    "qwen3.5-flash",
            "vision":   "qwen3-vl-flash",
        },
        "cheap": {
            "quality":  "qwen3.5-plus",
            "standard": "qwen3.5-flash",
            "cheap":    "qwen3.5-flash",
            "vision":   "qwen3-vl-flash",
        },
        # 鲁棒性测试：全部用 qwen3.5-flash，能力最弱，用于验证系统对弱模型的容错
        "robust_test": {
            "quality":  "qwen3.5-flash",
            "standard": "qwen3.5-flash",
            "cheap":    "qwen3.5-flash",
            "vision":   "qwen3-vl-flash",
        },
        # 最高质量：每一层都上顶级模型（调试或关键任务）
        "max_quality": {
            "quality":  "claude-opus-4-6",
            "standard": "claude-sonnet-4-6",
            "cheap":    "glm-5",
            "vision":   "qwen3.6-plus",
        },
    }

    # ── Role → Tier: 每个 role 声明它需要什么能力 ────────────────────────
    _ROLE_TIERS: dict[str, str] = {
        "ide_agent":            "quality",   # 最高：交互、代码、推理
        "runtime_main":         "quality",   # 主运行时 (qwen3.6-plus; glm-5 需 sk- key 当前不可用)
        "evolution_strategy":   "quality",
        "evolution_reflect":    "quality",
        "meta_evolution":       "quality",
        "pioneer_explore":      "quality",
        "consolidation_worker": "quality",
        "pain_classify":        "cheap",     # 分类任务用便宜模型
        "vision":               "vision",
        "vision_quality":       "quality",   # 高质量视觉：用 qwen3.6-plus
        "field_discovery":      "quality",   # demogame 字段公式自主发现 AgentNodeLoop
    }

    # role-level override：忽略 policy 的 tier→model 映射，强制特定 role 用指定模型
    #   vision_quality: 高质量视觉永远用 qwen3.6-plus
    #   ide_agent: dashboard 交互式 IDE agent 用 max-preview（最高能力, 用户 round 28 拍）;
    #              其他 quality role (skill_importer/deep_read/repo_architect 等) 维持 policy
    _ROLE_OVERRIDES_DEFAULT: dict[str, str] = {
        "vision_quality": "qwen3.6-plus",
        "ide_agent":      "qwen3.6-max-preview",
    }

    # ── Universal fallback chain: 所有 tier 共享的末位兜底 ──────────────
    _UNIVERSAL_FALLBACK: list[str] = [
        "glm-5",                          # the_company 最稳定的中端
        "qwen3.5-flash",                  # the_company 最便宜
    ]

    # 2026-04-10: the_company 从 120 降到 40 RPM (1.5 秒/次最小间隔)。
    # 原值 120 允许 2 req/sec burst, 在主管线 + sentinel 后台巡逻并发时经常把
    # 聚合 API 的 per-model quota 打成 429。40 RPM + 强制最小间隔 (见 RateLimiter)
    # 错峰后稳定多了, 单跑 workflow-factory 的 7 次调用仍远低于上限。
    # 可通过 OMNICOMPANY_THE_COMPANY_RPM env 覆盖。
    _RATE_LIMITS: dict[str, int] = {
        _THE_COMPANY_URL: int(os.environ.get("OMNICOMPANY_THE_COMPANY_RPM", "40")),
    }

    _DEFAULT_POLICY = "production"
    _instance: "ModelRegistry | None" = None

    def __init__(self) -> None:
        # Active policy: env var > default
        self._active_policy: str = os.environ.get("OMNI_MODEL_POLICY", self._DEFAULT_POLICY)
        if self._active_policy not in self._POLICIES:
            logger.warning(
                "ModelRegistry: unknown policy '%s', falling back to '%s'",
                self._active_policy, self._DEFAULT_POLICY,
            )
            self._active_policy = self._DEFAULT_POLICY
        # Per-role overrides (runtime, set by meta-evolution or explicit pin)
        self._role_overrides: dict[str, str] = dict(self._ROLE_OVERRIDES_DEFAULT)
        logger.info("ModelRegistry initialized with policy=%s", self._active_policy)

    @classmethod
    def get_instance(cls) -> "ModelRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Policy management ────────────────────────────────────────────────

    @property
    def active_policy(self) -> str:
        return self._active_policy

    def set_active_policy(self, policy: str) -> None:
        """运行时切换策略。影响后续所有按 role 构造的 LLMClient。"""
        if policy not in self._POLICIES:
            raise ValueError(f"Unknown policy '{policy}'. Available: {list(self._POLICIES)}")
        old = self._active_policy
        self._active_policy = policy
        logger.info("ModelRegistry: policy %s -> %s", old, policy)

    def available_policies(self) -> list[str]:
        return list(self._POLICIES)

    # ── Core resolution: role → tier → policy → model ────────────────────

    def _resolve_model_name(self, role: str) -> str:
        """按 role → tier → active_policy 解析首选模型名。

        优先级：
          1. _role_overrides[role]（显式 pin）
          2. policy[tier_of(role)]
          3. 默认 policy 的对应 tier
          4. "glm-5"（最终兜底）
        """
        if role in self._role_overrides:
            return self._role_overrides[role]

        tier = self._ROLE_TIERS.get(role, "standard")
        policy = self._POLICIES.get(self._active_policy) or self._POLICIES[self._DEFAULT_POLICY]
        return policy.get(tier) or self._POLICIES[self._DEFAULT_POLICY].get(tier) or "glm-5"

    def _model_config(self, model: str) -> dict[str, str]:
        """根据模型名查 base_url + api_key。未知模型默认走 the_company。"""
        cfg = self._MODELS.get(model)
        if not cfg:
            logger.warning("ModelRegistry: unknown model '%s', defaulting to the_company endpoint", model)
            cfg = {"base_url": _THE_COMPANY_URL, "key_env": "THE_COMPANY_API_KEY"}
        return {
            "model": model,
            "base_url": cfg["base_url"],
            "api_key": _resolve_key(cfg["key_env"]),
        }

    # ── Public API (backward-compatible shape) ───────────────────────────

    def get(self, role: str) -> dict[str, str]:
        """返回首选配置 {model, base_url, api_key}（向后兼容）。"""
        return self._model_config(self._resolve_model_name(role))

    def get_fallback_chain(self, role: str) -> list[dict[str, str]]:
        """返回完整 fallback 链。

        chain = [primary_from_policy, *universal_fallbacks (去重)]
        """
        primary = self._resolve_model_name(role)
        seen = {primary}
        chain = [self._model_config(primary)]
        for m in self._UNIVERSAL_FALLBACK:
            if m in seen:
                continue
            seen.add(m)
            chain.append(self._model_config(m))
        return chain

    def set_role_model(self, role: str, model: str) -> None:
        """Pin a role to a specific model, overriding the current policy.

        Useful for meta-evolution or per-call specialization.
        Pass model="" or None to clear the pin.
        """
        if not model:
            self._role_overrides.pop(role, None)
            logger.info("ModelRegistry: cleared pin on role=%s", role)
            return
        self._role_overrides[role] = model
        logger.info("ModelRegistry: pinned role=%s -> model=%s", role, model)

    def rate_limiter_for(self, role: str) -> RateLimiter:
        cfg = self.get(role)
        limit = self._RATE_LIMITS.get(cfg["base_url"], 60)
        return RateLimiter.for_endpoint(cfg["base_url"], limit)

    def all_roles(self) -> dict[str, dict[str, str]]:
        """返回所有已知 role 的当前解析结果。"""
        return {r: self.get(r) for r in self._ROLE_TIERS}

    def describe(self) -> dict:
        """返回完整状态快照，用于 CLI / 调试。"""
        return {
            "active_policy": self._active_policy,
            "available_policies": list(self._POLICIES),
            "roles": {
                role: {
                    "tier": self._ROLE_TIERS[role],
                    "resolved_model": self._resolve_model_name(role),
                    "pinned": role in self._role_overrides,
                }
                for role in self._ROLE_TIERS
            },
            "policies": self._POLICIES,
        }


# ---------------------------------------------------------------------------
# Protocol detection
# ---------------------------------------------------------------------------

_OPENAI_ENDPOINTS = frozenset([_THE_COMPANY_URL, _KIMI_URL])


def _is_openai_endpoint(base_url: str) -> bool:
    return base_url in _OPENAI_ENDPOINTS


# ---------------------------------------------------------------------------
# Anthropic → unified response wrapper
# ---------------------------------------------------------------------------

@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


# ─── 跨厂 LLM 工具协议适配 (2026-05-05, P1.1) ──────────────────────────
#
# 背景: qwen3.6-plus 等模型走 OpenAI SDK 但**不发** OpenAI tool_calls 字段,
# 改返 `<tool_code>NAME(arg1=val1, arg2="str")</tool_code>` markup 文本块.
# 实测 (Wave 5 LLM smoke 2026-05-05): qwen 收到 Anthropic tools_spec 后该走
# tool_use 协议但实际返 markup, 一线工具调用全失败.
#
# 修法: 流式收完后, 若 OpenAI tool_calls 字段空但文本含 markup, fallback 解析:
#   - 用 ast.parse 安全解 NAME(kwargs) (不 eval, 防注入)
#   - 转 _ToolUseBlock 加进 content_blocks
#   - 剥文本 markup (LLM 不会看到自己的混淆痕迹)
#
# 通用: 任何返 markup 的跨厂 LLM 都受益. 不只 qwen.

import ast as _ast
import re as _re

_TOOL_CODE_RE = _re.compile(
    r'<tool_code>\s*(?P<body>.+?)\s*</tool_code>',
    _re.DOTALL,
)


def _parse_tool_code_blocks(text: str) -> tuple[list[_ToolUseBlock], str]:
    """从 LLM text 抽 <tool_code>NAME(args)</tool_code> 块, 返 (blocks, cleaned_text).

    解析格式: 单层函数调用 NAME(kw1=val1, kw2="str") — 跟 Python 关键字参数语法一致.
    用 ast.parse 安全解 (literal_eval 仅允字面量, 防注入).
    """
    blocks: list[_ToolUseBlock] = []
    matches = list(_TOOL_CODE_RE.finditer(text))
    if not matches:
        return [], text

    for idx, m in enumerate(matches):
        raw = m.group("body").strip()
        if not raw:
            continue
        # ast.parse 安全解 — 仅接受函数调用 + 字面量参数
        try:
            tree = _ast.parse(raw, mode="eval")
            call = tree.body
            if not isinstance(call, _ast.Call):
                continue
            # NAME 必须是普通标识符 (不允许 obj.method 或动态 lookup)
            if not isinstance(call.func, _ast.Name):
                continue
            name = call.func.id
            kwargs: dict = {}
            for kw in call.keywords:
                if kw.arg is None:
                    continue  # 跳 **kwargs
                # literal_eval 仅允字面量 (str / int / float / list / dict / tuple / bool / None)
                kwargs[kw.arg] = _ast.literal_eval(kw.value)
            # positional args 兼容 (qwen 偶尔用): 按 index 转 arg{N}
            for pos_idx, arg_node in enumerate(call.args):
                kwargs.setdefault(f"arg{pos_idx}", _ast.literal_eval(arg_node))
            blocks.append(_ToolUseBlock(
                id=f"toolcode_{idx}",
                name=name,
                input=kwargs,
            ))
        except (SyntaxError, ValueError, AttributeError, TypeError):
            # 解析失败 → 跳, 让下游看到原 markup 文本 (调试用)
            continue

    # 剥所有成功解析的 markup (即便没全成功也剥, 减少 LLM 自我混淆)
    cleaned = _TOOL_CODE_RE.sub("", text).strip()
    return blocks, cleaned


@dataclass
class _UnifiedUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    # 2026-05-04 prompt cache 接通后新增 (Anthropic streaming usage 字段对齐):
    # cache_read_input_tokens — 从缓存读的 input (远便宜 normal input ~10%)
    # cache_creation_input_tokens — 写新 cache 的 input (一次性, 比 normal 略贵)
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _UnifiedResponse:
    """Mimics anthropic.types.Message so downstream code works unchanged."""
    content: list[Any] = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: _UnifiedUsage = field(default_factory=_UnifiedUsage)
    model: str = ""
    reasoning_content: str = ""


# ── LLM 计量设施 ──

# 每百万 token 价格（美元），来源: the_company 聚合 API 定价 (2026-04-07)
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_M, output_per_M)
    # ── ★2026-06-23 用户同步最新价目表（权威）──
    "gpt-5.5-expensive":         (5.00,  30.00),
    "gpt-image-2":               (5.00,  10.00),
    "gemini-3-pro-image-preview": (2.00, 12.00),
    "gemini-3.1-pro-preview":    (2.00,  12.00),
    "gpt-5.3-codex":             (1.75,  14.00),
    "qwen3.7-max":               (1.71,  5.14),
    "gemini-3.5-flash":          (1.50,  9.00),
    "glm-5.2":                   (1.14,  4.00),
    "claude-haiku-4-5-20251001": (1.00,  5.00),
    "claude-haiku-4-5@20251001": (1.00,  5.00),
    "kimi-k2.6":                 (0.93,  3.86),
    "kimi-k2.7-code":            (0.93,  3.86),
    "glm-5.1":                   (0.86,  3.43),
    "gemini-3-flash-preview":    (0.50,  3.00),
    "gemini-3.1-flash-image-preview": (0.50, 3.00),
    "gpt-5.5":                   (0.50,  3.00),   # ★本轮自建 agent 主力
    "deepseek-v4-pro":           (0.43,  0.86),
    "qwen3-max":                 (0.36,  1.43),
    "qwen3.6-plus":              (0.29,  1.71),
    "gpt-5.4":                   (0.25,  1.50),   # 大降价(原 2.50/15.00)
    "gemini-embedding-2":        (0.20,  0.00),
    "gemini-embedding-001":      (0.15,  0.00),
    "deepseek-v4-flash":         (0.14,  0.29),
    "text-embedding-3-large":    (0.13,  0.00),
    "qwen-flash":                (0.02,  0.21),
    # ── 旧型号（不在最新表内, 留旧价供历史/fallback 调用估算）──
    "claude-opus-4-6":           (5.00,  25.00),
    "claude-sonnet-4-6":         (3.00,  15.00),
    "glm-5":                     (0.57,  2.57),
    "kimi-k2.5":                 (0.57,  3.00),
    "qwen3.5-plus":              (0.40,  1.20),
    "deepseek-v3-2-251201":      (0.28,  0.43),
    "qwen3.5-flash":             (0.03,  0.29),
    "qwen3-vl-flash":            (0.02,  0.21),
}


@dataclass
class LLMCallRecord:
    """单次 LLM 调用的计量记录。"""
    model: str
    role: str
    caller: str  # 调用者标识（如 "agent_loop.turn_3" 或 "auto_compact"）
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    stop_reason: str
    invocation_id: str = ""
    # 2026-05-04 prompt cache: 区分 normal input 跟 cache 读/写, 算成本 / 命中率
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "model": self.model,
            "role": self.role,
            "caller": self.caller,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "stop_reason": self.stop_reason,
            "invocation_id": self.invocation_id,
        }


class LLMMeter:
    """LLM 调用计量器 — 进程级单例，记录所有 LLMClient 调用。

    用途:
      1. 节点级成本核算（哪个 Router/AgentLoop 花了多少钱）
      2. 管线级效率诊断（哪些节点 token 效率低）
      3. 长期趋势监控（某管线的总开销随时间变化）
    """

    _instance: "LLMMeter | None" = None

    def __init__(self, *, persist: bool = False) -> None:
        self._records: list[LLMCallRecord] = []
        self._lock = threading.Lock()
        self._persist = persist

    @classmethod
    def get_instance(cls) -> "LLMMeter":
        if cls._instance is None:
            cls._instance = cls(persist=True)
        return cls._instance

    def record(self, rec: LLMCallRecord) -> None:
        with self._lock:
            self._records.append(rec)
        if self._persist:
            self._append_persistent(rec)

    def _meter_path(self) -> Any:
        override = os.environ.get("OMNI_LLM_METER_PATH")
        if override:
            from pathlib import Path as _Path
            return _Path(override)
        from omnicompany.core.config import omni_workspace_root
        return omni_workspace_root() / "data" / "llm" / "meter.jsonl"

    def _append_persistent(self, rec: LLMCallRecord) -> None:
        try:
            path = self._meter_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001 - metering must never break LLM calls.
            logger.debug("failed to persist llm meter record: %s", exc)

    def get_records(
        self,
        caller: str | None = None,
        caller_prefix: str | None = None,
        last_n: int | None = None,
    ) -> list[LLMCallRecord]:
        """查询记录。caller 精确匹配，caller_prefix 前缀匹配（用于 pipeline 级汇总）。"""
        with self._lock:
            recs = self._records
            if caller:
                recs = [r for r in recs if r.caller == caller]
            elif caller_prefix:
                recs = [r for r in recs if r.caller.startswith(caller_prefix)]
            if last_n:
                recs = recs[-last_n:]
            return list(recs)

    def summary(self, caller: str | None = None, caller_prefix: str | None = None) -> dict:
        """返回调用汇总：总 token、总成本、调用次数。

        2026-05-04 加 cache 维度: cache_hit_rate (0..1) + cache_savings_usd (vs 全 normal input).
        """
        recs = self.get_records(caller=caller, caller_prefix=caller_prefix)
        sum_in = sum(r.input_tokens for r in recs)
        sum_cache_read = sum(r.cache_read_tokens for r in recs)
        sum_cache_creation = sum(r.cache_creation_tokens for r in recs)
        # cache hit rate = cache_read / (input + cache_read + cache_creation)
        # 即 "本可付全价的 input 中, 有多少 ratio 是 cache 读" — 越高越省
        denom = sum_in + sum_cache_read + sum_cache_creation
        cache_hit_rate = (sum_cache_read / denom) if denom > 0 else 0.0
        return {
            "call_count": len(recs),
            "total_input_tokens": sum_in,
            "total_output_tokens": sum(r.output_tokens for r in recs),
            "total_cache_read_tokens": sum_cache_read,
            "total_cache_creation_tokens": sum_cache_creation,
            "cache_hit_rate": cache_hit_rate,
            "total_cost_usd": sum(r.cost_usd for r in recs),
            "avg_latency_ms": sum(r.latency_ms for r in recs) / len(recs) if recs else 0,
        }

    def breakdown(self, caller_prefix: str = "") -> dict[str, dict]:
        """按 caller 分组汇总（每个节点/router 各花了多少）。"""
        from collections import defaultdict
        groups: dict[str, list[LLMCallRecord]] = defaultdict(list)
        with self._lock:
            for r in self._records:
                if caller_prefix and not r.caller.startswith(caller_prefix):
                    continue
                # 提取节点级 caller（去掉 .turn_N 后缀）
                parts = r.caller.rsplit(".turn_", 1)
                node_caller = parts[0]
                groups[node_caller].append(r)
        return {
            k: {
                "call_count": len(recs),
                "total_input_tokens": sum(r.input_tokens for r in recs),
                "total_output_tokens": sum(r.output_tokens for r in recs),
                "total_cache_read_tokens": sum(r.cache_read_tokens for r in recs),
                "total_cache_creation_tokens": sum(r.cache_creation_tokens for r in recs),
                "total_cost_usd": sum(r.cost_usd for r in recs),
                "models_used": list({r.model for r in recs}),
            }
            for k, recs in groups.items()
        }

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


def _extract_response_text(result: Any) -> str:
    """从 Anthropic / OpenAI 响应对象里抽 text 合集, 永不抛。"""
    try:
        content = getattr(result, "content", None)
        if isinstance(content, list):
            texts = []
            for b in content:
                t = getattr(b, "text", None)
                if isinstance(t, str):
                    texts.append(t)
            if texts:
                return "\n".join(texts)
        choices = getattr(result, "choices", None)
        if choices:
            msg = getattr(choices[0], "message", None)
            if msg:
                c = getattr(msg, "content", "")
                if isinstance(c, str):
                    return c
    except Exception:
        pass
    return ""


def _extract_tool_calls(result: Any) -> list[dict[str, Any]]:
    """从响应对象里抽 tool_use/tool_calls 的摘要(含 input/arguments), 永不抛。

    M1 改造 (2026-04-15): 带上 input dict, 供 info_audit parser 直接消费.
    """
    import json as _json
    out: list[dict[str, Any]] = []
    try:
        # Anthropic
        content = getattr(result, "content", None)
        if isinstance(content, list):
            for b in content:
                if getattr(b, "type", None) == "tool_use":
                    out.append({
                        "type": "tool_use",
                        "name": getattr(b, "name", "?"),
                        "id": getattr(b, "id", ""),
                        "input": getattr(b, "input", {}) or {},
                    })
        # OpenAI
        choices = getattr(result, "choices", None)
        if choices:
            msg = getattr(choices[0], "message", None)
            tcs = getattr(msg, "tool_calls", None) if msg else None
            if tcs:
                for tc in tcs:
                    fn = getattr(tc, "function", None)
                    # OpenAI function.arguments 是 JSON 字符串, 解析后塞入 input
                    args_str = getattr(fn, "arguments", "") if fn else ""
                    parsed_args: dict[str, Any] = {}
                    parse_error: str | None = None
                    if isinstance(args_str, str) and args_str.strip():
                        try:
                            parsed_args = _json.loads(args_str)
                        except Exception as exc:
                            # 2026-04-18 BD.6c 修：原 `except Exception: parsed_args = {}`
                            # 静默吞掉 JSON 解析失败，导致 qwen 输出被 max_tokens 截成残
                            # JSON 时 input={} → 业务 tool 收到空参数 → 反复空 submit。
                            # 现在保留原始 args_str + 解析错误，让 LLMCallRouter 能 emit
                            # 警告并给 tool dispatch 明确信号。
                            parse_error = (
                                f"JSON parse failed: {exc}; "
                                f"args_str_len={len(args_str)}; "
                                f"head={args_str[:200]!r}; "
                                f"tail={args_str[-200:]!r}"
                            )
                            parsed_args = {"__raw_args": args_str, "__parse_error": parse_error}
                    elif isinstance(args_str, dict):
                        parsed_args = args_str
                    out.append({
                        "type": "tool_use",
                        "name": getattr(fn, "name", "?") if fn else "?",
                        "id": getattr(tc, "id", ""),
                        "input": parsed_args,
                    })
    except Exception:
        pass
    return out


def _estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """根据模型定价估算单次调用成本（美元）。

    2026-05-04 加 cache pricing (CC 对齐):
      - cache_read_input_tokens: ~10% normal input price (Anthropic 标准)
      - cache_creation_input_tokens: ~125% normal input price (一次性写入溢价)
    """
    pricing = _MODEL_PRICING.get(model)
    if not pricing:
        # 未知模型按中等价格估算
        return (
            input_tokens * 1.0
            + output_tokens * 5.0
            + cache_read_tokens * 0.1
            + cache_creation_tokens * 1.25
        ) / 1_000_000
    input_price, output_price = pricing
    # cache 价位按 input 价的 10% / 125% (Anthropic 标准比例)
    cache_read_price = input_price * 0.10
    cache_creation_price = input_price * 1.25
    return (
        input_tokens * input_price
        + output_tokens * output_price
        + cache_read_tokens * cache_read_price
        + cache_creation_tokens * cache_creation_price
    ) / 1_000_000


# ---------------------------------------------------------------------------
# Max tokens overflow parser (CC parseMaxTokensContextOverflowError 对齐, 2026-05-04)
# ---------------------------------------------------------------------------


def _parse_overflow_and_compute_new_max_tokens(
    err_msg_lower: str, current_max_tokens: int,
) -> int | None:
    """从 Anthropic 400 error message 抽 context_limit + current_input 算新 max_tokens.

    典型 error: "input length and max_tokens exceed context limit: 200000 < 195000 + 16384"
    抽: limit=200000, input=195000 → new_max = limit - input - 1000 (buffer) = 4000

    抽不到精确值 → 兜底砍一半. None = 当前 max_tokens 已最小 (1000) 不能再减.

    Args:
        err_msg_lower: lowercased error message
        current_max_tokens: 当前请求的 max_tokens

    Returns:
        new_max_tokens: 减后的值 (>=1000 才有意义), None 表示无法再减
    """
    import re as _re
    # 模式 1: "exceed context limit: <limit> < <input> + <max>"
    m = _re.search(
        r"context\s+limit[:\s]+(\d+)\s*[<≤]\s*(\d+)\s*\+\s*(\d+)",
        err_msg_lower,
    )
    if m:
        limit = int(m.group(1))
        input_tok = int(m.group(2))
        # max_in_err = int(m.group(3))  # 跟 current_max_tokens 应该一致
        new_max = limit - input_tok - 1000  # 1000 token 安全 buffer
        if new_max < 1000:
            return None  # 输入太大, 不可救
        return new_max
    # 模式 2: 没具体数字 → 兜底砍一半
    halved = current_max_tokens // 2
    if halved < 1000:
        return None
    return halved


# ---------------------------------------------------------------------------
# Prompt cache helpers (CC 对齐, 2026-05-04)
# ---------------------------------------------------------------------------

_CACHE_CONTROL_EPHEMERAL: dict = {"type": "ephemeral"}


def _add_cache_control_to_system(system: Any) -> Any:
    """把 plain string system prompt 转成 [{type:'text', text, cache_control:ephemeral}].

    Anthropic API: 静态 system prompt 标 cache_control 后下次调用 cache hit 仅算
    cache_read_input_tokens (远便宜 normal input). agent 长 system prompt 收益巨大.

    若已是 list of blocks, 不重复加; 若末尾 block 已有 cache_control, 不动.
    """
    if isinstance(system, str):
        if not system.strip():
            return system
        return [{"type": "text", "text": system, "cache_control": _CACHE_CONTROL_EPHEMERAL}]
    if isinstance(system, list) and system:
        # 已是 blocks 列表 — 在最后一个 text block 加 cache_control (若没标)
        out = list(system)
        # 找最后一个 dict 类型 text block
        for i in range(len(out) - 1, -1, -1):
            blk = out[i]
            if isinstance(blk, dict) and blk.get("type") == "text":
                if not blk.get("cache_control"):
                    out[i] = {**blk, "cache_control": _CACHE_CONTROL_EPHEMERAL}
                break
        return out
    return system


def _add_cache_control_to_tools(tools: list[dict]) -> list[dict]:
    """给最后一个 tool spec 加 cache_control: ephemeral. 静态工具列表缓存命中.

    Anthropic API: cache_control 标记末尾 tool 后, prefix (system + 之前所有工具)
    全部缓存. 单调用方 tool 列表稳定 → 几乎所有 round 都是 cache hit.
    """
    if not tools:
        return tools
    out = list(tools)
    # 找最后一个 dict 类型工具 (跳过任何非 dict 异常项)
    for i in range(len(out) - 1, -1, -1):
        t = out[i]
        if isinstance(t, dict):
            if not t.get("cache_control"):
                out[i] = {**t, "cache_control": _CACHE_CONTROL_EPHEMERAL}
            break
    return out


# ---------------------------------------------------------------------------
# LLMClient — 双协议 API 客户端
# ---------------------------------------------------------------------------


class LLMClient:
    """多协议 LLM 客户端 — Anthropic (DashScope) + OpenAI-compatible (the_company proxy)。

    支持两种构造方式：
      1. 直接传参: LLMClient(model=..., base_url=..., api_key=...)
      2. 按角色构造: LLMClient.for_role("pain_classify")

    自动检测 base_url 并选择 Anthropic 或 OpenAI SDK。
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        max_tokens: int = 16000,  # 现代 agent 默认 16k 起步 (2026-05-03 提, 跟 LLMCallRouter 16384 一致). 续写在 _continue_if_truncated_* 兜底, 撞 length 自动接着生
        tools: list[dict] | None = None,
        role: str | None = None,
        # 2026-05-04 CC 对齐: betas / extra_headers / extra_body / metadata 通道.
        # betas: anthropic_beta 列表 (例 ['prompt-caching-2024-07-31', 'computer-use-2024-10-22'])
        # extra_headers: 任意额外 HTTP header
        # extra_body: 任意额外 JSON body 字段 (Anthropic SDK 透传)
        # metadata: 调用元数据 (例 {'user_id': '...'} for tracking)
        # prompt_cache: 是否给 system + tools 自动加 cache_control: ephemeral 标记
        # thinking_budget_tokens: 启用 extended thinking (Anthropic Claude 3.7+ 特性).
        #   传 N>0 → kwargs['thinking'] = {'type': 'enabled', 'budget_tokens': N}
        #   模型先输出 <thinking>...</thinking> 块 (LLM 内部推理), 再 final answer.
        #   对复杂推理任务 (proofs / planning / debugging) 显著提升质量.
        betas: list[str] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        prompt_cache: bool = True,
        thinking_budget_tokens: int = 0,
        # 2026-05-04 CC unattended mode 对齐:
        # persistent_retry: 启用后 429/529/connection 错误**不限次**重试 (指数退避至 60s 上限),
        #   适合 batch / nohup / 后台 agent 跑长任务. 默认关 (cli/dashboard 用).
        # heartbeat_interval_sec: 每隔 N 秒在 retry 间隙调 heartbeat callback (告知"我还活着").
        #   默认 30s. 0 = 不发 heartbeat.
        # heartbeat_callback: callable(retry_count, last_error_msg). 默认无 (仅 log).
        persistent_retry: bool = False,
        heartbeat_interval_sec: float = 30.0,
        heartbeat_callback: Any | None = None,
        # Optional per-client deadlines.  The default has no aggregate stream
        # deadline: activity, not elapsed wall time, determines liveness.
        request_timeout_sec: float | None = None,
        stream_wall_clock_deadline_sec: float | None = None,
        stream_idle_chunk_deadline_sec: float | None = None,
        max_retry_attempts: int | None = None,
        max_continuation_retries: int | None = None,
        prefix_tool_errors: bool = True,
    ):
        # Resolve primary model from registry (no cross-model fallback — see call()).
        registry = ModelRegistry.get_instance()
        effective_role = role or (None if (model or base_url or api_key) else "runtime_main")
        if effective_role:
            primary = registry.get(effective_role)
            model = model or primary["model"]
            base_url = base_url or primary["base_url"]
            api_key = api_key or primary["api_key"]
        elif model and not (base_url or api_key):
            # 显式传入 model 但未传 base_url/api_key 时，从注册表查 model 的端点配置
            model_cfg = registry._model_config(model)
            base_url = base_url or model_cfg["base_url"]
            api_key = api_key or model_cfg["api_key"]

        # 默认模型按 endpoint 类型选取：the_company/OpenAI-compatible endpoint 用 the_company 模型
        # 注意：此处 base_url 已经被 role chain 或显式参数设置好了
        _effective_base = base_url or os.environ.get("ANTHROPIC_BASE_URL") or ""
        if model:
            self.model = model
        elif _is_openai_endpoint(_effective_base):
            self.model = os.environ.get("OMNICOMPANY_MODEL") or "qwen3.7-max"
        else:
            self.model = os.environ.get("ANTHROPIC_MODEL", "qwen3.5-plus")
        self.role = role
        self.max_tokens = max_tokens
        self.tools = tools if tools is not None else []
        # CC 对齐通道
        self._betas = list(betas) if betas else []
        self._extra_headers = dict(extra_headers) if extra_headers else {}
        self._extra_body = dict(extra_body) if extra_body else {}
        self._metadata = dict(metadata) if metadata else {}
        self._prompt_cache = prompt_cache
        self._thinking_budget = max(0, int(thinking_budget_tokens))
        self._persistent_retry = bool(persistent_retry)
        self._heartbeat_interval = max(0.0, float(heartbeat_interval_sec))
        self._heartbeat_cb = heartbeat_callback
        self._stream_wall_clock_deadline = (
            None
            if stream_wall_clock_deadline_sec is None
            else max(1.0, float(stream_wall_clock_deadline_sec))
        )
        self._stream_idle_chunk_deadline = (
            None
            if stream_idle_chunk_deadline_sec is None
            else max(1.0, float(stream_idle_chunk_deadline_sec))
        )
        self._max_retry_attempts = (
            _RETRY_MAX_ATTEMPTS
            if max_retry_attempts is None
            else max(0, int(max_retry_attempts))
        )
        self._max_continuation_retries = (
            self._MAX_CONTINUATION_RETRIES
            if max_continuation_retries is None
            else max(0, int(max_continuation_retries))
        )
        self._prefix_tool_errors = bool(prefix_tool_errors)

        resolved_base = base_url or os.environ.get("ANTHROPIC_BASE_URL")
        # 按 endpoint 类型选择 key，防止 DashScope token 被错误地发送到 OpenAI-compatible 端点
        if _is_openai_endpoint(resolved_base or ""):
            resolved_key = (
                api_key
                or os.environ.get("THE_COMPANY_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or "no-key"  # 防止测试环境 None 导致 OpenAI SDK 初始化崩溃
            )
        else:
            resolved_key = (
                api_key
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_API_KEY")
            )

        self._rate_limiter: RateLimiter | None = None
        if resolved_base:
            limit = ModelRegistry._RATE_LIMITS.get(resolved_base, 60)
            self._rate_limiter = RateLimiter.for_endpoint(resolved_base, limit)

        self._is_openai = _is_openai_endpoint(resolved_base or "")
        self._request_timeout = max(
            1.0,
            float(request_timeout_sec if request_timeout_sec is not None else (300.0 if self._is_openai else 120.0)),
        )
        self._openai_client: Any = None
        self.client: Any = None
        self._resolved_base = resolved_base
        self._resolved_key = resolved_key

        if self._is_openai:
            import openai
            self._openai_client = openai.OpenAI(
                base_url=resolved_base,
                api_key=resolved_key,
                timeout=self._request_timeout,
                max_retries=0,
            )
        else:
            import anthropic
            self.client = anthropic.Anthropic(
                base_url=resolved_base,
                api_key=resolved_key or os.environ.get("ANTHROPIC_AUTH_TOKEN"),
                timeout=self._request_timeout,
            )

    @classmethod
    def for_role(cls, role: str, **kwargs: Any) -> "LLMClient":
        """按角色从 ModelRegistry 构造。"""
        return cls(role=role, **kwargs)

    def call(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        caller: str = "",
        info_audit: bool | None = None,
        audit_context: dict[str, Any] | None = None,
        progress_callback: Any | None = None,
    ) -> Any:
        """调用 LLM。

        设计：**不做跨模型 silent fallback**。完成任务的正确性 > 永不失败。
        如果当前 role 的 primary 模型挂了，宁可让上层中止/重试，也不要悄悄
        降级到一个能力差很多的模型——那会让 agent 的行为发生不可预测的退化
        （上下文质量、工具使用能力、推理深度都会断崖式下降）。

        重试策略（同 model 内）：
        - 429 / 5xx / 连接错误：指数退避，最多 _RETRY_MAX_ATTEMPTS 次
        - 服务端给的 Retry-After header 始终被尊重（如果有）
        - 用尽后直接 raise，让上层决定怎么办

        Args:
            response_format: OpenAI structured output 格式。仅 OpenAI 协议生效。
            info_audit: Phase 2 (D1 PIGGYBACK): 在 system 里追加 info_audit
                提示, 要求 LLM 正常答完再吐一段 info_audit JSON 块。
                返回的 result 对象上会附加 `.info_audit: InfoAuditReport | None`
                属性, 并剥离 `.info_audit_cleaned_text` (正文不含 audit 块)。
            audit_context: Phase 2.5 统一审计所需的调用上下文 (trace_id /
                pipeline_id / node_id), runner 传入; 未传则落到 "adhoc"。
        """
        model = self.model
        invocation_id = uuid.uuid4().hex
        base_url = self._resolved_base or ""
        api_key = self._resolved_key or ""
        if not api_key:
            raise RuntimeError(
                f"LLM call failed: no api_key for role={self.role or 'default'} "
                f"model={model} base_url={base_url}. Set THE_COMPANY_API_KEY."
            )

        # ── Phase 3 预落: 全局信息审计开关 (env override) ──
        # 对应用户 2026-04-09 需求 #2 "全局可用的信息审计开关"
        # 设 OMNICOMPANY_INFO_AUDIT=1 后, 所有 LLMClient.call() 默认开 piggyback
        # 例外 1: info_audit.* 内部调用者(probe/post_hoc) 不能叠 piggyback
        # 例外 2: AgentNodeLoop (caller 形如 "<ClassName>.turn_N") 不开 piggyback
        #         理由: AgentNodeLoop 本身就是信息审计的最终兜底, 叫它再自审等于空转;
        #         而且 agent 的 tool_use 流程里再插 info_audit tool 会破坏多轮对话状态.
        # 例外 3: 显式传 info_audit=False (非 None) 的调用 → 严格 JSON 输出 Router
        #         用此 opt-out, 避免 tool 注入让 LLM 只调 tool 不吐主文本.
        #         对这些节点, info_audit 的职责由 post_hoc 层承担 (runner 兜底).
        _is_info_audit_internal = bool(caller) and str(caller).startswith("info_audit.")
        # 匹配 .turn_0 / .turn_5 / .turn_internal 等 agent 内部调用后缀
        _is_agent_loop_caller = bool(caller) and bool(
            re.search(r"\.turn_\w+$", str(caller))
        )
        _explicit_opt_out = info_audit is False
        if info_audit is None:
            # 未显式指定, 看 env var
            if (
                not _is_info_audit_internal
                and not _is_agent_loop_caller
                and os.environ.get("OMNICOMPANY_INFO_AUDIT", "").strip().lower() in ("1", "true", "piggyback")
            ):
                info_audit = True
            else:
                info_audit = False
        # 显式传 info_audit=True 但调用者是 agent loop → 尊重 agent loop 豁免
        if info_audit and _is_agent_loop_caller:
            info_audit = False
        # 显式 opt-out 始终尊重 (便于 strict-JSON Router 彻底跳过 piggyback)
        if _explicit_opt_out:
            info_audit = False

        # ── Phase 2.5 关联机制: 没显式传 audit_context 就从 contextvar 读 ──
        # Runner 在调 router.run() 前通过 use_audit_context({...}) 设置,
        # 这里自动继承, 节点内所有 LLM 调用都带上 trace_id/node_id/pipeline_id
        if audit_context is None:
            ctx = get_audit_context()
            if ctx:
                audit_context = ctx

        # ── Phase 2 PIGGYBACK (M1 2026-04-15 改造): tool 注入 + 文本提示兜底 ──
        # 改造核心:
        #   1. 首选方式: 把 info_audit 作为一个 tool 注入到 self.tools, 强制 LLM
        #      通过结构化 tool_use 返回审计结果 —— 不再污染主答案文本
        #   2. 同时保留文本追加提示 (INFO_AUDIT_PROMPT_APPENDIX), 作为 LLM 忘调
        #      工具时的兜底 (强调"优先用工具, 无工具才 fallback 文本")
        #   3. try/finally 保护 self.tools, 确保审计 tool 不泄漏到下次调用
        effective_system = system
        _original_tools = self.tools
        _injected_audit_tool = False
        if info_audit:
            try:
                from omnicompany.protocol.info_audit import (
                    INFO_AUDIT_PROMPT_APPENDIX,
                    INFO_AUDIT_TOOL_NAME,
                    INFO_AUDIT_TOOL_SCHEMA,
                )
                effective_system = (system or "") + INFO_AUDIT_PROMPT_APPENDIX
                # 注入 info_audit tool (若 self.tools 里已有同名 tool 不重复加)
                existing_names = {t.get("name") for t in (self.tools or [])}
                if INFO_AUDIT_TOOL_NAME not in existing_names:
                    self.tools = list(self.tools or []) + [INFO_AUDIT_TOOL_SCHEMA]
                    _injected_audit_tool = True
            except Exception:
                # 永不阻塞主路径
                effective_system = system

        try:
            start_ts = time.time()
            is_openai = _is_openai_endpoint(base_url)
            if is_openai:
                import openai
                oai_client = openai.OpenAI(
                    base_url=base_url,
                    api_key=api_key,
                    timeout=self._request_timeout,
                    max_retries=0,
                )
                result = self._call_openai_with(
                    oai_client, model, messages, effective_system, base_url,
                    response_format=response_format,
                    tool_choice=tool_choice,
                    progress_callback=progress_callback,
                )
                # 续写: finish_reason=length (OpenAI) 时, 把已生成内容回灌 + 注入 continue
                # 提示再调一次, 直到 finish_reason != length 或 重试上限. 跟 Claude Code
                # 的 max_output_tokens recovery 同模式 (build-src/src/query.ts L1188+).
                result = self._continue_if_truncated_openai(
                    oai_client, model, messages, effective_system, base_url, result,
                    response_format=response_format,
                    tool_choice=tool_choice,
                    progress_callback=progress_callback,
                )
            else:
                import anthropic
                anth_client = anthropic.Anthropic(
                    base_url=base_url,
                    api_key=api_key,
                    timeout=self._request_timeout,
                )
                result = self._call_anthropic_with(
                    anth_client, model, messages, effective_system,
                    tool_choice=tool_choice,
                    response_format=response_format,
                    progress_callback=progress_callback,
                )
                result = self._continue_if_truncated_anthropic(
                    anth_client, model, messages, effective_system, result,
                    tool_choice=tool_choice,
                    progress_callback=progress_callback,
                )
            latency_ms = (time.time() - start_ts) * 1000.0
        finally:
            # 恢复原 tools, 审计 tool 不泄漏到后续调用
            if _injected_audit_tool:
                self.tools = _original_tools

        # ── 计量记录 ──
        usage = getattr(result, "usage", None)
        actual_model = getattr(result, "model", model) or model
        in_tok = getattr(usage, "input_tokens", 0) if usage else 0
        out_tok = getattr(usage, "output_tokens", 0) if usage else 0
        # Cache tokens (Anthropic streaming usage 字段, prompt cache 接通后非零).
        # 老 OpenAI/non-cache 路径: 0/0, 不影响.
        cache_read = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
        stop = getattr(result, "stop_reason", "unknown")
        cost = _estimate_cost(actual_model, in_tok, out_tok, cache_read, cache_creation)
        meter_record = LLMCallRecord(
            model=actual_model,
            role=self.role or "default",
            caller=caller or f"llm.{self.role or 'direct'}",
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            cost_usd=cost,
            latency_ms=latency_ms,
            stop_reason=stop,
            invocation_id=invocation_id,
        )
        LLMMeter.get_instance().record(meter_record)
        planner_measurement_hook = _AI_PLAYER_PLANNER_MEASUREMENT_HOOK_VAR.get()
        if planner_measurement_hook is not None:
            planner_measurement_hook(result, meter_record)

        # ── Phase 2 PIGGYBACK: 解析 info_audit (M1 改造: 优先 tool_use, 兜底文本) ──
        #
        # M1 改造 (2026-04-15):
        #   1. 首选: 从 response 的 tool_use block 里抽 info_audit 工具调用
        #      → 不污染主文本, 无需 strip
        #   2. 兜底: 若 LLM 忘调工具, 从文本里扫 ```json``` 块 (旧逻辑保留)
        #   3. 只有兜底路径才 strip_info_audit_block (改写主文本)
        parsed_audit = None
        response_text = _extract_response_text(result)
        cleaned_text = response_text  # 默认不变
        used_tool_path = False
        if info_audit:
            try:
                from omnicompany.runtime.info_audit.parser import (
                    parse_info_audit_from_text,
                    parse_info_audit_from_tool_use,
                    strip_info_audit_block,
                )
                # ── Path A: 从 tool_use block 抽 (首选) ──
                tool_blocks: list[Any] = []
                # Anthropic: result.content 是 [TextBlock, ToolUseBlock, ...]
                content = getattr(result, "content", None)
                if isinstance(content, list):
                    tool_blocks.extend(content)
                # 兼容 _extract_tool_calls 已提取的 dict 列表
                extracted_tool_calls = _extract_tool_calls(result) or []
                tool_blocks.extend(extracted_tool_calls)
                parsed_audit = parse_info_audit_from_tool_use(tool_blocks)
                if parsed_audit is not None:
                    used_tool_path = True

                # ── Path B: 文本兜底 ──
                if parsed_audit is None:
                    parsed_audit = parse_info_audit_from_text(response_text)
                    if parsed_audit is not None:
                        # 文本路径才污染主答, 需要 strip
                        cleaned_text = strip_info_audit_block(response_text)
                        # 就地改写 Anthropic content[*].text (若可写)
                        try:
                            if isinstance(content, list):
                                for b in content:
                                    if hasattr(b, "type") and getattr(b, "type", "") == "text" and hasattr(b, "text"):
                                        b.text = cleaned_text
                                        break
                        except Exception:
                            pass
                        # OpenAI choices[0].message.content
                        try:
                            choices = getattr(result, "choices", None)
                            if choices:
                                msg = getattr(choices[0], "message", None)
                                if msg and hasattr(msg, "content") and isinstance(msg.content, str):
                                    msg.content = cleaned_text
                        except Exception:
                            pass

                # 附加到 result
                try:
                    result.info_audit = parsed_audit  # type: ignore[attr-defined]
                except Exception:
                    pass
                try:
                    result.info_audit_cleaned_text = cleaned_text  # type: ignore[attr-defined]
                except Exception:
                    pass
                try:
                    result.info_audit_from_tool = used_tool_path  # type: ignore[attr-defined]
                except Exception:
                    pass

                # 写入 contextvar 供 TeamRunner 兜底读取
                if parsed_audit is not None:
                    try:
                        _LAST_INFO_AUDIT_VAR.set(parsed_audit)
                    except Exception:
                        pass
                else:
                    # LLM 既没调工具也没追加 JSON 块 → 算 contract violation, 记录一下
                    logger.info(
                        "[info_audit] %s did not emit info_audit (neither tool_use nor text block)",
                        caller or "anon",
                    )
            except Exception:
                pass  # 永不阻塞

        # ── Phase 2.5 统一审计: 落盘 LLMAuditRecord + emit bus 事件 ──
        try:
            from omnicompany.runtime.info_audit.audit_store import (
                LLMAuditRecord,
                record_llm_call,
            )
            ctx = audit_context or {}
            rec = LLMAuditRecord(
                trace_id=ctx.get("trace_id", ""),
                pipeline_id=ctx.get("pipeline_id", ""),
                node_id=ctx.get("node_id", ""),
                role=self.role or "",
                model=actual_model,
                caller=caller or f"llm.{self.role or 'direct'}",
                system_prompt=effective_system or "",
                messages=messages,
                tools=list(self.tools or []),
                response_text=response_text,
                tool_calls=_extract_tool_calls(result),
                stop_reason=str(stop),
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=latency_ms,
                info_audit_mode="piggyback" if info_audit else "off",
                info_audit=(parsed_audit.model_dump() if parsed_audit else None),
            )
            record_llm_call(rec)
        except Exception:
            pass  # 永不阻塞主路径

        return result

    @staticmethod
    def _anthropic_msgs_to_openai(
        messages: list[dict[str, Any]],
        *,
        prefix_tool_errors: bool = True,
    ) -> list[dict[str, Any]]:
        """Convert Anthropic-format messages to OpenAI multi-turn format.

        Handles:
        - Plain text content (str)
        - Anthropic image blocks → OpenAI image_url blocks (base64 or url)
        - Anthropic tool_use blocks → OpenAI assistant tool_calls
        - Anthropic tool_result blocks → OpenAI role=tool messages
        """
        import json as _json
        oai: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, str):
                oai.append({"role": role, "content": content})
                continue
            # content is a list of blocks — may include images
            oai_content_parts: list[dict] = []  # for multimodal messages
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            tool_results: list[dict] = []
            has_image = False
            for block in content:
                btype = block.get("type", "text") if isinstance(block, dict) else "text"
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                    oai_content_parts.append({"type": "text", "text": block.get("text", "")})
                elif btype == "image":
                    # Anthropic image → OpenAI image_url
                    src = block.get("source", {})
                    src_type = src.get("type", "")
                    if src_type == "base64":
                        media = src.get("media_type", "image/png")
                        data = src.get("data", "")
                        url = f"data:{media};base64,{data}"
                    elif src_type == "url":
                        url = src.get("url", "")
                    else:
                        url = src.get("url", src.get("data", ""))
                    oai_content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": url},
                    })
                    has_image = True
                elif btype == "image_url":
                    # Already OpenAI format — pass through
                    oai_content_parts.append(block)
                    has_image = True
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": _json.dumps(block.get("input", {})),
                        },
                    })
                elif btype == "tool_result":
                    # 协议跨接：Anthropic 的 is_error 字段在 OpenAI 协议里没有对应位。
                    # 跨厂 LLM（qwen/DeepSeek 等）不识别 CC 的 <tool_use_error> XML 约定，
                    # 反复犯同错不从 error 学。给 content 加 [TOOL_ERROR] prefix 让通用
                    # LLM 明确感知失败（ERROR 是跨训练语料的强信号）。
                    # 来源：2026-04-18 BD.6a 实战发现 qwen 空 submit 死循环，单独
                    # is_error 字段不够。
                    raw_content = str(block.get("content", ""))
                    if prefix_tool_errors and block.get("is_error"):
                        prefix = "[TOOL_ERROR] " if not raw_content.lstrip().startswith("[TOOL_ERROR]") else ""
                        raw_content = f"{prefix}{raw_content}"
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": raw_content,
                    })
            if tool_results:
                oai.extend(tool_results)
            elif tool_calls:
                msg: dict[str, Any] = {"role": "assistant"}
                if text_parts:
                    msg["content"] = "\n".join(text_parts)
                msg["tool_calls"] = tool_calls
                reasoning_content = m.get("reasoning_content")
                if isinstance(reasoning_content, str) and reasoning_content:
                    msg["reasoning_content"] = reasoning_content
                oai.append(msg)
            elif has_image:
                # Multimodal message — use content parts list
                oai.append({"role": role, "content": oai_content_parts})
            else:
                oai.append({"role": role, "content": "\n".join(text_parts)})
        return oai

    def _tools_to_openai(self) -> list[dict[str, Any]]:
        """Convert self.tools (Anthropic format) to OpenAI function format."""
        result = []
        for t in self.tools:
            schema = dict(t.get("input_schema", {}))
            schema.pop("$defs", None)  # strip Intent $defs — not needed for execution
            props = {k: v for k, v in schema.get("properties", {}).items() if k != "intent"}
            if props:
                schema["properties"] = props
            result.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": schema,
                },
            })
        return result

    def _call_openai_with(
        self,
        oai_client: Any,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        base_url: str,
        response_format: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
        progress_callback: Any | None = None,
    ) -> _UnifiedResponse:
        """OpenAI 协议调用（可指定 client 和 model，供 fallback 链使用）。"""
        import json as _json
        import openai as _openai

        oai_messages: list[dict[str, Any]] = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(
            self._anthropic_msgs_to_openai(
                messages,
                prefix_tool_errors=self._prefix_tool_errors,
            )
        )

        oai_tools = self._tools_to_openai()

        rl = None
        if base_url:
            limit = ModelRegistry._RATE_LIMITS.get(base_url, 60)
            rl = RateLimiter.for_endpoint(base_url, limit)

        delay = float(_RETRY_INITIAL_DELAY)
        last_error: Exception | None = None

        for attempt in range(self._max_retry_attempts + 1):
            if rl:
                rl.acquire()
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": oai_messages,
                    "max_tokens": self.max_tokens,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                if oai_tools:
                    kwargs["tools"] = oai_tools
                    # 默认保持兼容的 auto；明确传入 tool_choice 的调用者可以选择
                    # 具体函数。上层必须按模型能力决定是否强制，且验证实际 tool_use。
                    kwargs["tool_choice"] = tool_choice if tool_choice is not None else "auto"
                if response_format:
                    kwargs["response_format"] = response_format
                if self._extra_body:
                    kwargs["extra_body"] = dict(self._extra_body)

                # 流式调用 — 逐 chunk 拼接，避免长输出超时
                _report_stream_progress(
                    progress_callback,
                    "request_started",
                    protocol="openai",
                    model=model,
                )
                stream = oai_client.chat.completions.create(**kwargs)
                _report_stream_progress(
                    progress_callback,
                    "stream_opened",
                    protocol="openai",
                    model=model,
                )

                text_parts: list[str] = []
                reasoning_parts: list[str] = []
                tool_call_bufs: dict[int, dict] = {}  # index → {id, name, args_str}
                finish_reason = "stop"
                resp_model = model
                stream_usage: _UnifiedUsage | None = None

                # Wall-clock deadline for entire stream iteration.
                # OpenAI SDK / httpx 的 timeout 只管 connection + initial byte,
                # 流式长输出时若代理 keep-alive 空 chunk 不断, 客户端会无限等。
                # 这里用 monotonic 时钟硬卡一个上限。
                # 2026-04-09: 新增, 因为 workflow-factory code_gen 实跑时卡死 15+ min 观察到。
                _stream_start = time.monotonic()
                _stream_deadline_sec = self._stream_wall_clock_deadline
                _last_chunk_time = _stream_start
                _chunk_idle_sec = self._stream_idle_chunk_deadline
                _last_progress_time = _stream_start
                _chunk_count = 0
                _text_chars = 0
                _reasoning_chars = 0
                _tool_arg_chars = 0

                for chunk in stream:
                    # 整体超时检查
                    _now = time.monotonic()
                    if (
                        _stream_deadline_sec is not None
                        and _now - _stream_start > _stream_deadline_sec
                    ):
                        try:
                            stream.close()
                        except Exception:
                            pass
                        raise TimeoutError(
                            f"LLM stream exceeded wall-clock {_stream_deadline_sec}s "
                            f"(model={model}, parts_so_far={len(text_parts)})"
                        )
                    # 空 chunk 死寂检查: 超过 idle 阈值 = upstream 卡死
                    if (
                        _chunk_idle_sec is not None
                        and _now - _last_chunk_time > _chunk_idle_sec
                    ):
                        try:
                            stream.close()
                        except Exception:
                            pass
                        raise TimeoutError(
                            f"LLM stream idle > {_chunk_idle_sec}s between chunks "
                            f"(model={model}, upstream likely stalled)"
                        )
                    _last_chunk_time = _now
                    _chunk_count += 1

                    if _chunk_count == 1:
                        _report_stream_progress(
                            progress_callback,
                            "first_chunk",
                            protocol="openai",
                            model=model,
                            elapsed_ms=int((_now - _stream_start) * 1000),
                        )

                    if not chunk.choices:
                        if _now - _last_progress_time >= 2.0:
                            _report_stream_progress(
                                progress_callback,
                                "stream_progress",
                                protocol="openai",
                                model=model,
                                elapsed_ms=int((_now - _stream_start) * 1000),
                                chunk_count=_chunk_count,
                                text_chars=_text_chars,
                                reasoning_chars=_reasoning_chars,
                                tool_arg_chars=_tool_arg_chars,
                            )
                            _last_progress_time = _now
                        continue
                    delta = chunk.choices[0].delta
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                    if chunk.model:
                        resp_model = chunk.model

                    # 提取 usage（流式在最后一个 chunk 返回，需 stream_options.include_usage）
                    if hasattr(chunk, "usage") and chunk.usage:
                        stream_usage = _UnifiedUsage(
                            input_tokens=getattr(chunk.usage, "prompt_tokens", 0) or 0,
                            output_tokens=getattr(chunk.usage, "completion_tokens", 0) or 0,
                        )

                    # 文本内容
                    if delta and delta.content:
                        text_parts.append(delta.content)
                        _text_chars += len(delta.content)
                    reasoning_delta = getattr(delta, "reasoning_content", None) if delta else None
                    if reasoning_delta:
                        reasoning_parts.append(reasoning_delta)
                        _reasoning_chars += len(reasoning_delta)

                    # tool_calls 增量拼接
                    if delta and delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_call_bufs:
                                tool_call_bufs[idx] = {
                                    "id": tc_delta.id or "",
                                    "name": (tc_delta.function.name if tc_delta.function else "") or "",
                                    "args": "",
                                }
                            buf = tool_call_bufs[idx]
                            if tc_delta.id:
                                buf["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    buf["name"] = tc_delta.function.name
                                if tc_delta.function.arguments:
                                    buf["args"] += tc_delta.function.arguments
                                    _tool_arg_chars += len(tc_delta.function.arguments)

                    if _now - _last_progress_time >= 2.0:
                        _report_stream_progress(
                            progress_callback,
                            "stream_progress",
                            protocol="openai",
                            model=model,
                            elapsed_ms=int((_now - _stream_start) * 1000),
                            chunk_count=_chunk_count,
                            text_chars=_text_chars,
                            reasoning_chars=_reasoning_chars,
                            tool_arg_chars=_tool_arg_chars,
                        )
                        _last_progress_time = _now

                _report_stream_progress(
                    progress_callback,
                    "stream_completed",
                    protocol="openai",
                    model=resp_model,
                    elapsed_ms=int((time.monotonic() - _stream_start) * 1000),
                    chunk_count=_chunk_count,
                    text_chars=_text_chars,
                    reasoning_chars=_reasoning_chars,
                    tool_arg_chars=_tool_arg_chars,
                )

                content_blocks: list[Any] = []
                full_text = "".join(text_parts)

                # 跨厂 LLM 工具协议适配 (2026-05-05, P1.1):
                # qwen3.6-plus 等不发 OpenAI tool_calls 字段, 改返 <tool_code> markup
                # 文本. 若标准 tool_calls 路径空 + 文本含 markup → fallback 解析.
                fallback_tool_blocks: list[_ToolUseBlock] = []
                if not tool_call_bufs and full_text and "<tool_code>" in full_text:
                    fallback_tool_blocks, full_text = _parse_tool_code_blocks(full_text)
                    if fallback_tool_blocks:
                        # 解析成功 → finish_reason 改 tool_use (跟 Anthropic 协议对齐),
                        # 让下游 agent loop 知道这是 tool_use 不是 end_turn
                        finish_reason = "tool_use"

                if full_text:
                    content_blocks.append(_TextBlock(text=full_text))

                # 标准 OpenAI tool_calls 字段路径
                for _idx, buf in sorted(tool_call_bufs.items()):
                    try:
                        args = _json.loads(buf["args"]) if buf["args"] else {}
                    except (ValueError, TypeError):
                        args = {}
                    content_blocks.append(_ToolUseBlock(
                        id=buf["id"],
                        name=buf["name"],
                        input=args,
                    ))

                # Fallback markup 路径
                content_blocks.extend(fallback_tool_blocks)

                usage = stream_usage or _UnifiedUsage()
                reasoning_content = "".join(reasoning_parts)
                logger.debug(
                    "LLM call [role=%s model=%s openai/stream] ok, %d chars, tools=%d",
                    self.role or "default", model, len(full_text),
                    len(tool_call_bufs),
                )
                return _UnifiedResponse(
                    content=content_blocks,
                    stop_reason=finish_reason,
                    usage=usage,
                    model=resp_model,
                    reasoning_content=reasoning_content,
                )
            except _openai.RateLimitError as e:
                last_error = e
                # Honor server-provided Retry-After header if present
                retry_after = None
                resp = getattr(e, "response", None)
                if resp is not None and hasattr(resp, "headers"):
                    ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
                    if ra:
                        try:
                            retry_after = float(ra)
                        except ValueError:
                            pass
                if attempt >= self._max_retry_attempts:
                    logger.warning(
                        "LLM rate limited [role=%s model=%s], giving up after %d attempts",
                        self.role or "default", model, attempt + 1,
                    )
                    break
                wait = retry_after if retry_after is not None else delay
                logger.warning(
                    "LLM rate limited [role=%s model=%s] (attempt %d/%d), sleeping %.0fs (retry-after=%s)...",
                    self.role or "default", model, attempt + 1, self._max_retry_attempts + 1, wait,
                    "yes" if retry_after is not None else "no",
                )
                time.sleep(wait)
                delay = min(delay * _RETRY_BACKOFF_FACTOR, _RETRY_MAX_DELAY)
            except (_openai.APIConnectionError, _openai.InternalServerError) as e:
                last_error = e
                if attempt >= self._max_retry_attempts:
                    break
                logger.warning(
                    "LLM connection error [role=%s model=%s] (attempt %d/%d): %s, sleeping %.0fs...",
                    self.role or "default", model, attempt + 1, self._max_retry_attempts + 1,
                    type(e).__name__, delay,
                )
                time.sleep(delay)
                delay = min(delay * _RETRY_BACKOFF_FACTOR, _RETRY_MAX_DELAY)
            except _openai.APIStatusError as e:
                # 400/401/403/404 等请求/鉴权/模型权限错误不会靠等待恢复。
                # 立即返回给调用方修配置,避免 30/60/120s 的无意义重试。
                last_error = e
                status = int(getattr(e, "status_code", 0) or 0)
                if status and status < 500:
                    logger.warning(
                        "LLM permanent API error [role=%s model=%s status=%s], fail fast: %s",
                        self.role or "default", model, status, e,
                    )
                    break
                if attempt >= self._max_retry_attempts:
                    break
                logger.warning(
                    "LLM server API error [role=%s model=%s status=%s] (attempt %d/%d), sleeping %.0fs...",
                    self.role or "default", model, status or "?", attempt + 1,
                    self._max_retry_attempts + 1, delay,
                )
                time.sleep(delay)
                delay = min(delay * _RETRY_BACKOFF_FACTOR, _RETRY_MAX_DELAY)
            except Exception as e:
                last_error = e
                if attempt >= self._max_retry_attempts:
                    break
                logger.warning(
                    "LLM error [role=%s model=%s] (attempt %d/%d): %s, sleeping %.0fs...",
                    self.role or "default", model, attempt + 1, self._max_retry_attempts + 1,
                    e, delay,
                )
                time.sleep(delay)
                delay = min(delay * _RETRY_BACKOFF_FACTOR, _RETRY_MAX_DELAY)

        raise last_error  # type: ignore[misc]

    # ── 续写 (max_tokens / length 截断恢复) ────────────────────────────
    #
    # 跟 Claude Code 同模式 (build-src/src/query.ts isWithheldMaxOutputTokens 路径):
    # LLM 输出被 max_tokens 截了 (finish_reason=length / stop_reason=max_tokens)
    # 时, 把已生成内容当作 assistant 消息回灌 + 注入"resume directly" 用户消息,
    # 再调一次 LLM 让它继续从切点接着写. 重复直到 finish 正常或重试上限.
    #
    # 写文件头注解的 agent (例 MaterialIdAgent 跑批 26 文件输出 1000+ 字 JSON)
    # 直接撞 max_tokens 截断 → JSON 解析 FAIL. 升 max_tokens 是绕开, 续写才是正修.

    _MAX_CONTINUATION_RETRIES = 3
    _CONTINUATION_USER_MSG = (
        "Output token limit hit. Resume directly — no apology, no recap of what you were doing. "
        "Pick up mid-thought if that is where the cut happened. Break remaining work into smaller pieces."
    )

    def _continue_if_truncated_openai(
        self,
        oai_client: Any,
        model: str,
        original_messages: list[dict[str, Any]],
        system: str,
        base_url: str,
        first_result: "_UnifiedResponse",
        response_format: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
        progress_callback: Any | None = None,
    ) -> "_UnifiedResponse":
        """OpenAI 路径续写. finish_reason='length' 时 inject + retry."""
        result = first_result
        accumulated_text = _extract_response_text(result)
        attempts = 0
        while result.stop_reason == "length" and attempts < self._max_continuation_retries:
            logger.info(
                "[continuation] LLM truncated (finish_reason=length, attempt %d/%d), continuing",
                attempts + 1, self._max_continuation_retries,
            )
            cont_messages = list(original_messages) + [
                {"role": "assistant", "content": [{"type": "text", "text": accumulated_text}]},
                {"role": "user", "content": [{"type": "text", "text": self._CONTINUATION_USER_MSG}]},
            ]
            try:
                next_result = self._call_openai_with(
                    oai_client, model, cont_messages, system, base_url,
                    response_format=response_format,
                    tool_choice=tool_choice,
                    progress_callback=progress_callback,
                )
            except Exception as e:
                logger.warning("[continuation] retry %d 失败 %s, 用已有内容封顶", attempts + 1, e)
                break
            more_text = _extract_response_text(next_result)
            accumulated_text += more_text
            result = next_result
            attempts += 1

        if attempts > 0:
            # 把累计文本写回 result.content (替换原 TextBlock)
            result = self._merge_text_into_result(result, accumulated_text)
        return result

    def _continue_if_truncated_anthropic(
        self,
        anth_client: Any,
        model: str,
        original_messages: list[dict[str, Any]],
        system: str,
        first_result: Any,
        tool_choice: dict[str, Any] | None = None,
        progress_callback: Any | None = None,
    ) -> Any:
        """Anthropic 路径续写. stop_reason='max_tokens' 时 inject + retry."""
        result = first_result
        accumulated_text = _extract_response_text(result)
        attempts = 0
        while getattr(result, "stop_reason", None) == "max_tokens" and attempts < self._max_continuation_retries:
            logger.info(
                "[continuation] LLM truncated (stop_reason=max_tokens, attempt %d/%d), continuing",
                attempts + 1, self._max_continuation_retries,
            )
            cont_messages = list(original_messages) + [
                {"role": "assistant", "content": [{"type": "text", "text": accumulated_text}]},
                {"role": "user", "content": self._CONTINUATION_USER_MSG},
            ]
            try:
                next_result = self._call_anthropic_with(
                    anth_client, model, cont_messages, system,
                    tool_choice=tool_choice,
                    progress_callback=progress_callback,
                )
            except Exception as e:
                logger.warning("[continuation] retry %d 失败 %s, 用已有内容封顶", attempts + 1, e)
                break
            more_text = _extract_response_text(next_result)
            accumulated_text += more_text
            result = next_result
            attempts += 1

        if attempts > 0:
            result = self._merge_text_into_result(result, accumulated_text)
        return result

    @staticmethod
    def _merge_text_into_result(result: Any, accumulated_text: str) -> Any:
        """把累计文本写回 result.content[0] (TextBlock) 让 caller 拿到完整内容."""
        try:
            content = getattr(result, "content", None)
            if isinstance(content, list):
                # 找第一个 text block 替换其文本
                replaced = False
                for b in content:
                    btype = getattr(b, "type", None) or (b.get("type") if isinstance(b, dict) else None)
                    if btype == "text":
                        if hasattr(b, "text"):
                            b.text = accumulated_text
                        elif isinstance(b, dict):
                            b["text"] = accumulated_text
                        replaced = True
                        break
                if not replaced:
                    # 没 text block 就插到最前面
                    content.insert(0, _TextBlock(text=accumulated_text))
        except Exception:
            pass
        return result

    def _call_anthropic_with(
        self,
        anth_client: Any,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        progress_callback: Any | None = None,
    ) -> "anthropic.types.Message":
        """Anthropic 协议调用（可指定 client 和 model，供 fallback 链使用）。

        2026-04-09 改为流式: 对齐 OpenAI 路径的 _STREAM_WALL_CLOCK_DEADLINE +
        _STREAM_IDLE_CHUNK_DEADLINE 双重本地超时。根因: 之前非流式
        `anth_client.messages.create(...)` 在大输入下可能被上游 proxy 无限 hold,
        Anthropic SDK 的 timeout 只管 connection 不管流式/长响应。

        2026-05-04 加 response_format (CC structured outputs 对齐):
          OpenAI 原生支持 response_format={'type':'json_schema',...}, Anthropic 不支持.
          我们通过 synthetic tool 模式实现 — 注入名为 `__structured_output__` 的工具,
          schema = response_format.json_schema.schema, 强制 tool_choice 指向它.
          模型必须 call 这个 tool, tool input 就是结构化输出.
          上层从 message.content 找该 tool_use block 的 input 字段拿结构化结果.
        """
        import anthropic  # 懒导入(SDK ~436ms); 下方 except 子句需 anthropic.BadRequestError 等
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        # Structured output via synthetic tool (Anthropic 没原生 response_format)
        effective_tools = list(self.tools) if self.tools else []
        effective_tool_choice = tool_choice
        if response_format and isinstance(response_format, dict):
            rf_type = response_format.get("type")
            if rf_type == "json_schema":
                schema_def = response_format.get("json_schema") or {}
                output_schema = schema_def.get("schema") or schema_def.get("parameters") or {}
                synth_tool = {
                    "name": "__structured_output__",
                    "description": (
                        schema_def.get("description")
                        or "Output the result by calling this tool with the structured JSON."
                    ),
                    "input_schema": output_schema,
                }
                effective_tools.append(synth_tool)
                effective_tool_choice = {"type": "tool", "name": "__structured_output__"}
        # 2026-05-04 prompt cache (CC 对齐): system + tools 加 cache_control: ephemeral
        # 静态长 prompt 缓存命中后, input_tokens 实际仅 cache_read_input_tokens (便宜 90%).
        if effective_tools:
            kwargs["tools"] = _add_cache_control_to_tools(effective_tools) if self._prompt_cache else effective_tools
        if system:
            kwargs["system"] = _add_cache_control_to_system(system) if self._prompt_cache else system
        if effective_tool_choice is not None:
            kwargs["tool_choice"] = effective_tool_choice
        # CC 对齐: betas / extra_headers / extra_body / metadata 透传
        if self._betas:
            kwargs["betas"] = list(self._betas)
        if self._extra_headers:
            kwargs["extra_headers"] = dict(self._extra_headers)
        if self._extra_body:
            kwargs["extra_body"] = dict(self._extra_body)
        if self._metadata:
            kwargs["metadata"] = dict(self._metadata)
        # Extended thinking (Claude 3.7+, 2026-05-04 加): budget_tokens > 0 → 启用
        if self._thinking_budget > 0:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }

        delay = _RETRY_INITIAL_DELAY
        last_error: Exception | None = None
        # persistent_retry: unattended 模式 (batch / nohup), 不限重试次数, 仅 cap delay.
        # 同时按 heartbeat_interval 周期回调 (告知 caller "agent 还活着, 在等 retry").
        _effective_max = 999_999 if self._persistent_retry else self._max_retry_attempts

        for attempt in range(_effective_max + 1):
            if self._rate_limiter:
                self._rate_limiter.acquire()
            try:
                # 流式调用 — 用 anth_client.messages.stream() context manager
                # 逐 event 读取, 用 wall-clock + idle chunk 双重 deadline 保护
                _stream_start = time.monotonic()
                _last_chunk_time = _stream_start
                text_parts: list[str] = []
                tool_use_blocks: list[dict] = []
                stop_reason = "stop"
                resp_model = model
                stream_usage = None
                _chunk_count = 0
                _text_chars = 0
                _last_progress_time = _stream_start

                _report_stream_progress(
                    progress_callback,
                    "request_started",
                    protocol="anthropic",
                    model=model,
                )
                with anth_client.messages.stream(**kwargs) as stream:
                    _report_stream_progress(
                        progress_callback,
                        "stream_opened",
                        protocol="anthropic",
                        model=model,
                    )
                    for event in stream:
                        _now = time.monotonic()
                        if (
                            self._stream_wall_clock_deadline is not None
                            and _now - _stream_start > self._stream_wall_clock_deadline
                        ):
                            raise TimeoutError(
                                f"Anthropic stream exceeded wall-clock "
                                f"{self._stream_wall_clock_deadline}s "
                                f"(model={model}, parts_so_far={len(text_parts)})"
                            )
                        if (
                            self._stream_idle_chunk_deadline is not None
                            and _now - _last_chunk_time > self._stream_idle_chunk_deadline
                        ):
                            raise TimeoutError(
                                f"Anthropic stream idle > "
                                f"{self._stream_idle_chunk_deadline}s between events "
                                f"(model={model}, upstream likely stalled)"
                            )
                        _last_chunk_time = _now
                        _chunk_count += 1
                        if _chunk_count == 1:
                            _report_stream_progress(
                                progress_callback,
                                "first_chunk",
                                protocol="anthropic",
                                model=model,
                                elapsed_ms=int((_now - _stream_start) * 1000),
                            )

                        ev_type = getattr(event, "type", "")
                        # text delta
                        if ev_type == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if delta and getattr(delta, "type", "") == "text_delta":
                                text_delta = getattr(delta, "text", "")
                                text_parts.append(text_delta)
                                _text_chars += len(text_delta)
                        elif ev_type == "message_start":
                            msg = getattr(event, "message", None)
                            if msg:
                                resp_model = getattr(msg, "model", model) or model
                        elif ev_type == "message_delta":
                            # stop_reason + usage 增量
                            delta = getattr(event, "delta", None)
                            if delta:
                                sr = getattr(delta, "stop_reason", None)
                                if sr:
                                    stop_reason = sr
                            usg = getattr(event, "usage", None)
                            if usg:
                                stream_usage = usg

                        if _now - _last_progress_time >= 2.0:
                            _report_stream_progress(
                                progress_callback,
                                "stream_progress",
                                protocol="anthropic",
                                model=resp_model,
                                elapsed_ms=int((_now - _stream_start) * 1000),
                                chunk_count=_chunk_count,
                                text_chars=_text_chars,
                            )
                            _last_progress_time = _now

                _report_stream_progress(
                    progress_callback,
                    "stream_completed",
                    protocol="anthropic",
                    model=resp_model,
                    elapsed_ms=int((time.monotonic() - _stream_start) * 1000),
                    chunk_count=_chunk_count,
                    text_chars=_text_chars,
                )

                # 流结束, 获取最终消息 (带完整的 content blocks 和 usage)
                final_msg = stream.get_final_message()
                logger.debug(
                    "LLM call [role=%s model=%s anthropic stream] ok, "
                    "chars=%d, stop=%s",
                    self.role or "default", resp_model,
                    sum(len(p) for p in text_parts), stop_reason,
                )
                return final_msg
            except anthropic.BadRequestError as e:
                # CC parseMaxTokensContextOverflowError 对齐 (2026-05-04):
                # input + max_tokens 超 context_window 时 Anthropic 返 400 含 "exceed".
                # 自动减 max_tokens 重试 1 次, 留 1000 token 安全 buffer.
                # 减完仍超 → 输入本身太大 → 不可救, raise 让上层走 L4 compact.
                err_msg = str(e).lower()
                is_overflow = (
                    "exceed" in err_msg
                    or "too long" in err_msg
                    or "context" in err_msg and "limit" in err_msg
                    or "prompt is too long" in err_msg
                )
                if is_overflow and attempt < _effective_max:
                    # 解析: 尝试从 err 抽 context_limit, current_input. 若抽不到, 砍一半.
                    new_max = _parse_overflow_and_compute_new_max_tokens(
                        err_msg, current_max_tokens=kwargs["max_tokens"],
                    )
                    if new_max and new_max >= 1000:
                        logger.warning(
                            "LLM context overflow [role=%s model=%s], reducing max_tokens %d → %d (attempt %d/%d) and retrying",
                            self.role or "default", model,
                            kwargs["max_tokens"], new_max,
                            attempt + 1, _effective_max + 1,
                        )
                        kwargs["max_tokens"] = new_max
                        delay = min(delay * 1.5, _RETRY_MAX_DELAY)  # 短退避
                        continue
                    # 减后仍 < 1000 → 输入本身超 → 不可救
                    logger.warning(
                        "LLM context overflow [role=%s model=%s] but input alone too large (max_tokens reduced to %s, can't go lower) — giving up, upper layer should L4 compact",
                        self.role or "default", model, new_max,
                    )
                # 非 overflow 或不可救 → 抛
                raise
            except anthropic.RateLimitError as e:
                last_error = e
                # Rate limit: 最多重试 1 次后快速 fallback
                if attempt >= min(1, _effective_max):
                    logger.warning(
                        "LLM rate limited [role=%s model=%s], giving up after %d attempts, will fallback",
                        self.role or "default", model, attempt + 1,
                    )
                    break
                retry_after = getattr(e, "response", None)
                if retry_after and hasattr(retry_after, "headers"):
                    ra = retry_after.headers.get("retry-after")
                    if ra:
                        try:
                            delay = max(delay, float(ra))
                        except ValueError:
                            pass
                logger.warning(
                    "LLM rate limited [role=%s model=%s] (attempt %d), sleeping %.0fs then retry once...",
                    self.role or "default", model, attempt + 1, delay,
                )
                time.sleep(delay)
                delay = min(delay * _RETRY_BACKOFF_FACTOR, _RETRY_MAX_DELAY)
            except (anthropic.APIConnectionError, anthropic.InternalServerError) as e:
                last_error = e
                if attempt >= _effective_max:
                    break
                logger.warning(
                    "LLM connection error [role=%s] (attempt %d/%s): %s, sleeping %.0fs...",
                    self.role or "default",
                    attempt + 1,
                    "∞" if self._persistent_retry else str(self._max_retry_attempts + 1),
                    type(e).__name__, delay,
                )
                # heartbeat (persistent_retry mode): 长 sleep 期间周期回调
                if self._persistent_retry and self._heartbeat_cb and self._heartbeat_interval > 0:
                    _slept = 0.0
                    while _slept < delay:
                        _step = min(self._heartbeat_interval, delay - _slept)
                        time.sleep(_step)
                        _slept += _step
                        try:
                            self._heartbeat_cb(attempt + 1, str(e)[:200])
                        except Exception:
                            pass
                else:
                    time.sleep(delay)
                delay = min(delay * _RETRY_BACKOFF_FACTOR, _RETRY_MAX_DELAY)
            except TimeoutError as e:
                # 2026-04-09: 本地 stream deadline 超时 - 直接 raise, 不 retry
                # (retry 会再次落入同一超时, 浪费时间; 上层 Router 接 FAIL 走 fallback 路由)
                logger.warning(
                    "LLM stream deadline [role=%s model=%s]: %s",
                    self.role or "default", model, e,
                )
                raise

        raise last_error  # type: ignore[misc]
