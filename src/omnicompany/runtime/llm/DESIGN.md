<!-- [OMNI] origin=claude-code domain=runtime/llm ts=2026-04-17T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:runtime.llm.module_spec.design.md" -->

# runtime/llm · 设计文档

## 状态
- **版本**: V2（M1 piggyback tool 化之后）
- **成熟度**: active
- **下一步**: piggyback tool_choice 强约束（目前是 LLM 自由选择是否调 info_audit tool，考虑强制）；多模型 ensemble（对标 hermes mixture_of_agents）

## 核心目的

`runtime/llm/` 是**所有 LLM 调用的统一入口**。提供 `LLMClient` 类，封装：

- 多 endpoint 支持（Anthropic 原生 / OpenAI 兼容 / the_company 聚合）
- 速率限制（令牌桶 + per-endpoint min_interval）
- 重试（指数退避 + 429/5xx/connection 专项处理）
- 流式调用（wall-clock + idle-chunk 双重 deadline）
- 信息审计集成（piggyback tool 注入 / post_hoc 兜底 / audit_store 落盘）
- 审计上下文 contextvar（trace_id / pipeline_id / node_id 自动继承）

**所有 LLM 调用走这里**——业务代码不应直接用 `anthropic.Anthropic()` 或 `openai.OpenAI()`。

## 核心接口

- **`LLMClient(model, *, role, tools, max_tokens)`** — 主类 — [llm.py](llm.py)
- **`LLMClient.call(messages, system, *, tool_choice, response_format, caller, info_audit, audit_context)`** — 主方法 — [llm.py:801](llm.py#L801)
- **`use_audit_context({trace_id, pipeline_id, node_id})`** — contextmanager，管线调 Router.run 前设置 — [llm.py:53](llm.py#L53)
- **`get_last_info_audit()`** — 读 contextvar 最后一次 LLM 调用的 info_audit（runner 兜底用）
- **`ModelRegistry`** — 模型元数据（endpoint / api_key / rate_limit）— [llm.py](llm.py)
- **`LLMMeter`** — 全局调用度量（model / caller / tokens / cost / latency 累计）— [llm.py](llm.py)
- **`RateLimiter`** — 双约束限流（令牌桶 + 最小间隔），per-endpoint 单例 — [llm.py:102](llm.py#L102)

## 架构决策

### D1 — 不做跨模型 silent fallback

如果 role=`runtime_main` 的主模型（qwen3.6-plus）失败，不会悄悄降级到 haiku。理由：
- 模型能力差异大（上下文 / 工具使用 / 推理深度都会断崖）
- Silent fallback 让 agent 行为不可预测
- 宁可让上层 RETRY 或 HALT

同 model 内的重试保留（429/5xx/connection 按指数退避最多 N 次）。

_验证来源: [归纳] 来自 feedback_openai_tools_fix（mistral-medium-3 多轮失败经验）+ [code] `llm.py::LLMClient.call` 无 fallback 分支_

### D2 — RateLimiter 双约束：令牌桶 + min_interval

单纯令牌桶允许 burst（一瞬间用光 120 个令牌）。the_company 聚合 API 对 burst 极敏感（workflow_factory + sentinel 后台 + LLMJudge 同时调会打爆 quota）。

双约束：
- **令牌桶**（平均速率 ≤ max_per_minute）
- **min_interval**（相邻两次 `acquire()` 间隔 ≥ 60 / max_per_minute 秒）

两条件必须同时满足。即使令牌充足也不允许"同一瞬间连发两次"。

_验证来源: [code] `llm.py::RateLimiter`（令牌桶 + `min_interval` 双约束实现）_

### D3 — 流式双 deadline

Anthropic / OpenAI 的 SDK timeout 只管 connection + initial byte。流式长输出时，若代理 keep-alive 发空 chunk，客户端可能无限等。

本地加两层 deadline：
- `_STREAM_WALL_CLOCK_DEADLINE = 600`（单次流式最长 10 分钟）
- `_STREAM_IDLE_CHUNK_DEADLINE = 60`（两次非空 chunk 之间最长 60 秒）

触发任一 → TimeoutError（不 retry，上层决定怎么办）。

_验证来源: [code] `llm.py::_STREAM_WALL_CLOCK_DEADLINE` + `_STREAM_IDLE_CHUNK_DEADLINE` 常量 + 流式消费 loop_

### D4 — Piggyback：tool 注入 + 三路豁免（M1 改造，2026-04-15）

**背景**：旧版 piggyback 把"请产出 info_audit JSON 块"追加到 system prompt，让 LLM 在主答案后吐第二段 JSON。问题：
- 长输出时 LLM 忘记追加（成功率 0%）
- 追加 JSON 块污染 strict-JSON 输出 Router（如 SpecParser → `json.loads` 崩）

**新版**：info_audit 作为一个 tool 注入 `self.tools` 列表，LLM 通过 tool_use block 返回结构化审计。主答案走 text block 不被污染。

**三路豁免**：
- `info_audit.*` 内部 caller（probe/post_hoc 自己）—— 避免自污染
- `.turn_\w+$` agent loop caller —— agent 有自己工具系统，不注入
- 显式 `info_audit=False` —— strict-JSON Router 逃生口

_验证来源: [git-log] 2026-04-15 M1 改造 + [experiment] 旧版文本追加 JSON 块 0% 成功率实测 + [code] `llm.py::INFO_AUDIT_TOOL_SCHEMA`_

### D5 — audit_context 用 contextvars 自动继承

Runner 在调 `Router.run()` 前 `with use_audit_context({trace_id, pipeline_id, node_id})`。Router 内部任何 LLM 调用自动继承这个 context → 落盘的 LLMAuditRecord 自带元信息。

好处：14 个 Router 子类不用显式传 `audit_context`，零改代码。

_验证来源: [code] `src/omnicompany/runtime/info_audit/audit_context.py::use_audit_context` contextvar 实现_

### D6 — LLMMeter 全局单例记录度量

每次 `LLMClient.call()` 完成后往 `LLMMeter` 写一条 `LLMCallRecord`（model / caller / input_tokens / output_tokens / cost / latency / stop_reason）。

用途：
- dashboard 看总 cost / 高频 caller / 慢调用
- insights 引擎（G4）未来消费
- 排查问题（哪个节点最费 token？）

_验证来源: [code] `llm.py::LLMMeter`（全局单例 + `LLMCallRecord` 模型）_

## 数据流 / 拓扑

```
Router.run(input_data) 内部
    ↓ LLMClient(model, role, tools).call(messages, system, caller="...", info_audit=True)
        ├── 读 audit_context contextvar → audit_ctx
        ├── 检查 caller 模式，决定是否注入 piggyback tool
        ├── 若注入：self.tools = [..., INFO_AUDIT_TOOL_SCHEMA]
        ├── RateLimiter.acquire()
        ├── 构造 kwargs，调 Anthropic 或 OpenAI stream API
        ├── 流式消费（双 deadline 保护）
        ├── _extract_response_text(result) → response_text
        ├── _extract_tool_calls(result) → tool_calls
        ├── Piggyback 解析：
        │     ├─ path A: parse_info_audit_from_tool_use(tool_calls) （优先）
        │     └─ path B: parse_info_audit_from_text(response_text) （兜底）
        │
        ├── LLMMeter.record(...)（全局度量）
        ├── 写 LLMAuditRecord 到 audit_store jsonl
        │     （含 trace_id / node_id / 完整 messages / response_text / info_audit / tool_calls）
        └── 返回 result（已挂 .info_audit / .info_audit_cleaned_text）
        ↓
    Router 处理 result.content / result.info_audit
    ↓
    Runner 读 verdict.info_audit（或从 contextvar 兜底）
    ↓
    若 None → runtime/info_audit/post_hoc 兜底
```

## 已知局限

1. **硬编码 qwen3.6-plus** — 按 L2 铁律"全线默认且只使用 qwen-3.6-plus，严禁自作主张升级昂贵模型"。合理但限制了 mixture_of_agents 类能力。升级路径：按任务复杂度路由（对标 hermes smart_model_routing），但要 L2 明确许可。

2. **Piggyback tool 靠 LLM 自愿调用** — tool_choice 不强制，LLM 可能偶尔忘。实测 free-form 任务遵守率 ~100%，strict-JSON 任务会"只调 tool 不吐主文本"（所以 strict-JSON Router 显式 opt-out）。升级路径：tool_choice 强制 + 改主任务为另一个 tool（需要所有业务 Router 配合，成本高）。

3. **RateLimiter 没有全局视图** — 每个 endpoint 单例，跨 endpoint 不协调。the_company 聚合 API 的 per-model 配额若要共享，当前不支持。

4. **流式 deadline 无 retry** — 超时直接 raise，不自动重试。对瞬态网络抖动不友好。

5. **LLMAuditRecord 落盘可能影响性能** — 每次 LLM 调用完写 jsonl（append，轻），但长时间运行累积大。目前没有 rotation。

## 参考资料

- 关联 info_audit：[runtime/info_audit/DESIGN.md](../info_audit/DESIGN.md)（piggyback tool 定义 + audit_store）
- 关联 agent：[runtime/agent/DESIGN.md](../agent/DESIGN.md)（agent loop 如何用 LLMClient）
- 关联 memory：project_llm_model_choice.md（L2 铁律）
- 关联 plan：`docs/plans/[2026-04-14]INFO-SUFFICIENCY/EXPERIMENT_REPORT.md`（M1 piggyback 改造全过程）

## 接收意愿

- **welcome_themes**:
  - 多模型 ensemble / mixture of agents（任务聚合多模型答案再裁决）
  - 智能模型路由（按任务复杂度 / 成本 / 延迟动态选型）
  - prompt caching 与增量上下文复用策略
  - 流式输出优化（背压控制 / token-level stop / 空 chunk 检测）
  - 成本归因与分摊（按 caller / pipeline / trace 汇总）
  - 新 LLM provider API 接入范式（新 endpoint 或新调用形态）
  - piggyback tool_choice 强约束改造（tool_choice 强制而非 LLM 自愿）
- **hard_constraints**:
  - 单模型铁律 A（qwen3.6-plus 为默认主模型；升级需 L2 明确许可）
  - 必须走统一聚合 API / LLMClient（业务代码不绕过直调 anthropic/openai SDK）
  - 成本敏感（默认配置不拉高 baseline token 预算；新能力可选开关接入）
  - 不做跨模型 silent fallback（D1）
- **soft_preferences**:
  - 偏好 qwen3.6-plus 兼容的消息/工具协议
  - 偏好异步 API 而非同步阻塞
  - 偏好结构化输出（tool_use / response_format）而非 prompt 内 JSON 块
- **maturity_preference**: `any`（LLM 调用层变化快，新理论可先接 experimental flag 后再推广）

## T4 Structured JSON Authority

`runtime/llm/structured.py::call_json` is the only single-call structured JSON
surface. It reuses `LLMClient` for transport, disables `info_audit` for strict
JSON calls, extracts JSON from plain/fenced output, validates the requested
schema subset, and performs one correction retry. Its default model slot is
`OMNI_STRUCTURED_LLM_MODEL`, falling back to `deepseek-v4-pro`.

## T5 Batch Execution Authority

`runtime/llm/batch.py` is the standard home for long-running LLM batch mechanics:

- `run_parallel_items` owns worker-thread execution, per-item failure isolation,
  and progress logging.
- `JsonCheckpoint` plus `load_json_checkpoint` / `write_json_checkpoint` own JSON
  intermediate artifacts and resume inputs.
- Governance pipelines may keep domain-specific map/cluster/reduce logic, but
  they must consume these runtime helpers instead of opening local thread pools
  or hand-rolling `_last_*` checkpoint protocols.

## T7 Runtime Usage Visibility Authority

Runtime LLM observability is split by source of truth:

- `LLMMeter.get_instance()` persists every runtime LLM call to
  `data/llm/meter.jsonl` (or `OMNI_LLM_METER_PATH`). Fresh `LLMMeter()`
  instances remain in-memory for tests and local probes.
- `runtime/llm/batch.py` owns batch run state in `data/llm/batch_status.json`
  (or `OMNI_LLM_BATCH_STATUS_PATH`) through `write_batch_status` and
  `read_batch_status`.
- BOSS SIGHT reads both runtime-owned artifacts through
  `dashboard/boss_sight/llm_runtime_usage.py`; route handlers do not summarize
  meter files directly.
- `/api/boss-sight/llm-runtime` is the explicit runtime endpoint. The existing
  `/api/boss-sight/usage` response embeds the same payload as `internal` so the
  sidebar usage widget can show single/batch/agent calls, tokens, cost, and
  batch state without a second request.

## T6 OpenAI-Compatible Reasoning Authority

`LLMClient` owns OpenAI-compatible reasoning payload adaptation:

- Streaming OpenAI-compatible responses may emit `delta.reasoning_content`.
  `LLMClient._call_openai_with` collects those deltas and returns them as
  `_UnifiedResponse.reasoning_content`.
- Anthropic-style assistant tool turns may carry root-level `reasoning_content`.
  `_anthropic_msgs_to_openai` preserves that field when converting a prior
  assistant tool turn back to OpenAI-compatible messages.
- Active product code must not direct-call the the_company `chat/completions`
  endpoint to work around reasoning payloads. `article_author` consumes
  `LLMClient` for both the main deepseek-v4-pro loop and the web_fetch
  extraction model.
