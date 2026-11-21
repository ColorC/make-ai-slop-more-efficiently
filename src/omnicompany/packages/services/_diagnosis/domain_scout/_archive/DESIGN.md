<!-- [OMNI] origin=claude-code domain=services/domain_scout ts=2026-05-04T15:50:00Z type=doc status=design belongs_to_service=domain_scout -->
<!-- [OMNI] material_id="material:diagnosis.domain_scout.design_specification.md" -->

# domain_scout · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部**.

## 状态
- **版本**: V0 (design)
- **成熟度**: design
- **下一步**: Phase B.2 — Worker 真实现 + 3 个默认 topic yaml（`llm_agent_engineering` / `llm_first_spec_and_verification` / `knowledge_eng_and_distributed_docs`）；Phase A skeleton 2026-04-24 完成 · 6 Worker 拓扑合规 (0 orphan / 0 unconsumed 除 sink)

## 核心接口

### 入口函数
- **`run_scout(topic_id: str, since: Optional[str] = None)`** — 单次调研入口 — `run.py`
  - `topic_id`: 领域配置文件名（如 `llm_agent_engineering`）
  - `since`: 可选"只抓此时间后"；缺省读取该 topic 上次成功跑的 timestamp

### Material（`formats.py`，F-19 kind 必填）
- **`domain_scout.scout_request`** (kind.source) — `{topic_id, since, source_whitelist}` 外部请求入口
- **`domain_scout.fetch_batch`** (kind.internal) — 一批抓取原文 `[{url, title, published_at, raw_html, fetch_ts, source_hash}]`
- **`domain_scout.dedup_candidates`** (kind.internal) — 去重后候选 `[{url, title, content_excerpt, source_hash, novelty_score}]`
- **`domain_scout.evidence_bundle`** (kind.internal) — 每条候选的引用片段 `[{url, quoted_evidence, quoted_spans, relevance}]`
- **`domain_scout.raw_findings`** (kind.internal) — LLM 提炼的 finding 草稿 `[{title, insight, source_url, quoted_evidence, confidence}]`
- **`domain_scout.verified_findings`** (kind.internal) — 过可验证性检查的 findings（D4 硬约束全过）
- **`domain_scout.digest`** (kind.sink) — 最终 digest markdown 文件 + jsonl 索引

### Worker（继承 omnicompany.Worker，见 `workers/`）
- **`SourceFetcher`** — 按 topic.sources 配置抓取 (RSS / GitHub API / 网页) → `fetch_batch`
- **`DedupFilter`** — 对比历史 digest 索引去已报告项 → `dedup_candidates`（非 LLM，用 url + source_hash 指纹；这是规则的唯一合理应用：去重不涉及语义判断）
- **`EvidenceExtractor`** — 逐条抽原文段落做引用（LLM，**不截断**原文，Let LLM 主动搜索）→ `evidence_bundle`
- **`LLMSummarizer`** — 对每条候选写 finding 草稿（title / insight / confidence）→ `raw_findings`
- **`VerifiabilityCheck`** — **D4 硬约束门卫**：检查 source_url 可达 + quoted_evidence 在原文中 + source_hash 匹配 → `verified_findings`
  - 缺任一 → Worker Verdict = FAIL（不降级不 fallback）
- **`DigestWriter`** — 合成 digest.md + 更新 topic 索引 jsonl → `domain_scout.digest`

### 配置
- **Topic 配置** `config/domain_scout/<topic_id>.yaml` —
  ```yaml
  topic_id: llm_agent_engineering
  name: "LLM Agent 工程与工具链"
  sources:
    - kind: github_trending
      language: python
      filter: "agent | llm"
    - kind: rss
      url: "https://xxx.xxx/feed"
    - kind: arxiv
      query: "cat:cs.AI AND (agent OR llm)"
  source_whitelist:   # domain 级白名单，domain 外的 url 即使被抓到也过滤掉
    - github.com
    - arxiv.org
    - <blog domains>
  ```

## 架构决策

### D1 — 可验证性作为 Worker 粒度的硬约束
`VerifiabilityCheck` 是**独立 Worker**（而非内嵌在 `LLMSummarizer` 的自检）。理由：
- LLM 自评不可靠（MEMORY `feedback_forced_self_review_split_to_external`）
- 独立 Worker 的 FAIL 能在 MaterialDispatcher 可观测
- 失败的 finding 不进入 `verified_findings`，即"无可验证依据 = 无输出"，不让 LLM 幻觉污染下游

### D2 — LLM-first 的一个例外：去重
`DedupFilter` 用 url 精确匹配 + source_hash 匹配，**不**用 LLM。理由：去重是纯字面运算，LLM 反而容易把"同一事件不同文章"当不同条目。这是 L1 铁律"LLM 优先"的唯一例外（见 plan.md D2）。

### D3 — 源白名单作为软治理
`source_whitelist` 在 topic 配置中声明。即使 SourceFetcher 经转跳抓到白名单外的 url，也会在 EvidenceExtractor 前被过滤。白名单是策略声明，不是内容判定。

### D4 — workspace 隔离
每次 `run_scout` 在 `data/services/domain_scout/<topic_id>/runs/<run_id>/` 独立落盘中间 material（MEMORY `feedback_workspace_isolation_and_test_discrimination`）。历史 digest 存 `digests/YYYY-MM-DD.md`，索引 `index.jsonl` 跨 run 累积。

### D5 — 抓取不做过滤，过滤由 LLM 做
SourceFetcher 只做"抓下来"，不做关键词过滤。过滤交给 EvidenceExtractor + LLMSummarizer，因为"相关 vs 不相关"是语义判断（MEMORY `feedback_no_regex_for_language_work`）。

### D6 — digest 是 sink material
digest 落盘后由下游（`privacy_publish`）主动读，domain_scout 不 push。符合 Material kind.sink 语义。

## 数据流 / 拓扑

```
  [scout_request (source)]
          ↓
  SourceFetcher
          ↓
  [fetch_batch (internal)]
          ↓
  DedupFilter
          ↓
  [dedup_candidates (internal)]
          ↓
  EvidenceExtractor
          ↓
  [evidence_bundle (internal)]
          ↓
  LLMSummarizer
          ↓
  [raw_findings (internal)]
          ↓
  VerifiabilityCheck  ←── D4 硬约束门
          ↓    (FAIL → drop finding)
  [verified_findings (internal)]
          ↓
  DigestWriter
          ↓
  [digest (sink)] → data/services/domain_scout/<topic_id>/digests/YYYY-MM-DD.md
                  + index.jsonl (append-only 累积索引供去重)
```

## 已知局限

1. **抓取依赖公开 API 与 HTML 稳定性** — 新闻站 HTML 结构改版会让 SourceFetcher 抓失败。缓解：每个 source 配置带 `adapter` 字段，adapter 失败时输出诊断 material 让人工更新。
2. **LLM 对中文社媒摘要质量未验证** — V0 先跑英文源（github/arxiv），微信公众号 / 知乎 / 微博 类中文源是 Phase B 之后的事。
3. **置信度不是概率** — `confidence` 字段是 LLM 自评，不是统计意义上的概率。下游（privacy_publish）不应把它当精确分数用，只做粗排。
4. **无实时性** — 当前只按 L1 触发跑一轮。daemon 化在 Phase D 之后讨论，要看是否有周期需求。
5. **去重只按 url + hash** — 同一事件不同文章会被各自报告。"事件级聚合"是未来方向，需要 LLM 做跨文关联。

## 参考资料

- plan.md — 主 plan
- material.md §F-19 — Material kind 必填
- worker.md — Worker 粒度 R-18 / R-23
- llm_first.md — LLM 能力铁律
- workspace_isolation_and_test_discrimination.md — workspace 隔离

## 接收意愿

- **welcome_themes**:
  - 多源聚合框架（arxiv-rss / github-trending-api / atom-parser 的新封装）
  - 抓取健壮性模式（self-healing selector / 去结构化抓取）
  - 跨语言摘要（中文社媒语料处理）
  - 事件级聚合算法（LLM 跨文关联同一事件）
  - 可验证性增强（原文引文精确定位 / 引用图谱）
- **hard_constraints**:
  - 必须保留 source_url / quoted_evidence / source_hash 三字段（D4 不可破）
  - 禁用正则做语言分类 / 相关性判定（MEMORY `feedback_no_regex_for_language_work`）
  - 单模型铁律（qwen-3.6-plus）
- **soft_preferences**:
  - 偏好异步抓取
  - 偏好可离线复算的 pipeline（抓取与 LLM 解耦，便于回放）
- **maturity_preference**: any
