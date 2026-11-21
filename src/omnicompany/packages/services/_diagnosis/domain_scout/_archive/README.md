<!-- [OMNI] origin=ai-ide domain=services/domain_scout ts=2026-05-04T15:50:00Z type=doc status=design agent=ai-ide belongs_to_service=domain_scout -->
<!-- [OMNI] summary="domain_scout service 自我叙事 README — 给声明的领域做周期性外部调研, 6 Worker 管线产可验证 digest material 喂 privacy_publish. status=design Phase A skeleton" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写, 快速推进" -->
<!-- [OMNI] tags=readme,domain_scout,diagnosis,scouting,self-narrative,design-stage -->
<!-- [OMNI] material_id="material:services._diagnosis.domain_scout.readme.self_narrative.md"-->

# domain_scout · 领域外部调研

> 给声明的领域 (例 `llm_agent_engineering`) 做周期性外部调研 (公开互联网 / 开源社区 / 新闻 / 社媒), 产**可验证** digest material (原文链接 + 引用片段 + 源快照哈希) 喂下游 (主 privacy_publish).
>
> ⚠ status=design (V0 Phase A skeleton 2026-04-24, Phase B.2 Worker 真实现待做).

## 这是什么

domain_scout 是 omnicompany 的**领域外部调研 service**. 6 Worker 管线 (SourceFetcher → DedupFilter → EvidenceExtractor → LLMSummarizer → VerifiabilityCheck → DigestWriter) 给声明的领域做周期性调研, 产可验证 digest.

## 解决什么 / 不解决什么

**解决**: 领域周期性外部调研 / 每条 finding 可验证 (源链接 + 引文 + hash) / 不让 LLM 编出来的幻觉污染下游.

**不解决**: 私域抓取 (公司内 / 付费墙) / 主动发布 (privacy_publish 的事) / 学习内化 (absorption 的事) / 实时监控 (当前按 L1 触发, 非 daemon).

## 设计目的与最终目标

**设计目的**: 让 omnicompany 周期性了解外部领域动态, 但每条 finding 都**可验证**. D1 决策: VerifiabilityCheck 是独立 Worker (而非 LLMSummarizer 自检), LLM 自评不可靠.

**跟 absorption 边界** (重要):
- absorption 一次性 (每 repo 跑完归档), 输出 proposal 内化
- domain_scout 周期性 (每轮产增量 digest), 输出 digest 喂 privacy_publish

**最终目标**: Phase B.2 Worker 真实现 + 跑通 3 个默认 topic (LLM agent 工程 / LLM-first 规范 / 知识工程).

## 规划

- **当前 V0 design** (Phase A skeleton 2026-04-24, 6 Worker 拓扑合规)
- **下一步**: Phase B.2 Worker 真实现 + 3 个默认 topic yaml

## 构成

- 入口 → `run_scout(topic_id, since)` → run.py
- Materials (7 条) → [formats.py](formats.py)
  - `domain_scout.scout_request` (source)
  - 5 个 internal: fetch_batch / dedup_candidates / evidence_bundle / raw_findings / verified_findings
  - `domain_scout.digest` (sink)
- Workers (6 个) → [workers/](workers/)
  - SourceFetcher (RSS / GitHub API / 网页)
  - DedupFilter (D2 例外: url + hash 不用 LLM)
  - EvidenceExtractor (LLM 抽引文)
  - LLMSummarizer (LLM 写 finding 草稿)
  - **VerifiabilityCheck** (D1 硬约束门, FAIL 不降级)
  - DigestWriter (合成 digest.md + 索引 jsonl)
- Topic 配置 → `config/domain_scout/<topic_id>.yaml`

## 想了解更多

- [DESIGN.md](DESIGN.md) (含 D1-D6 决策 + 接收意愿) / [SKILL.md](SKILL.md)
- 下游 → ../../_authoring/privacy_publish/
- 兄弟 → ../../_authoring/absorption/ (一次性内化 vs 周期摘要)
- LLM 铁律 → docs/standards/llm_first.md
- 主 plan → [docs/plans/[2026-04-24]THREE-PACKAGES/plan.md](../../../../../docs/plans/%5B2026-04-24%5DTHREE-PACKAGES/plan.md)
