---
name: domain_scout
description: omnicompany 领域外部调研 - 6 Worker 管线产可验证 digest material 喂 privacy_publish, 每条 finding 含原文链接+引文+源 hash.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/domain_scout ts=2026-05-04T15:52:00Z type=doc status=design agent=ai-ide belongs_to_service=domain_scout -->
<!-- [OMNI] summary="domain_scout 操作手册 — 跑领域调研的步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] tags=skill,domain_scout,how-to,scouting,design-stage -->
<!-- [OMNI] material_id="material:services._diagnosis.domain_scout.skill.operations_manual.md"-->

# domain_scout · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).
>
> ⚠ status=design (V0), Worker 真实现 Phase B.2 待做.

## 适用范围

**用我**: 给声明的领域跑外部调研 + 拿可验证 digest.
**不用我**: 私域抓取 / 主动发布 / 学习内化 (找 absorption) / 实时监控.

## 前置条件

- omnifactory 已装 + `THE_COMPANY_API_KEY` 配
- `config/domain_scout/<topic_id>.yaml` 配置已写 (sources / source_whitelist)
- 网络可达 (抓 GitHub / arxiv / RSS / 网页)

## 操作步骤

### 场景 A · 跑某 topic 调研

```bash
omni run domain_scout -i topic_id="llm_agent_engineering"
```

**可选参数**:
- `since`: ISO timestamp, 缺省读上次成功跑的 timestamp

**验证**: 输出 `<topic_id>/digests/YYYY-MM-DD.md` (digest markdown) + `index.jsonl` 累积.

### 场景 B · 库调用

```python
from omnifactory.packages.services._diagnosis.domain_scout.run import run_scout
result = run_scout(topic_id="llm_agent_engineering", since="2026-04-01")
```

### 场景 C · 配新 topic

写 `config/domain_scout/<topic_id>.yaml`:
```yaml
topic_id: my_topic
sources:
  - kind: github_trending
    language: python
  - kind: rss
    url: "https://..."
source_whitelist:
  - github.com
```

## 入口清单

| 入口 | 用途 |
|---|---|
| `omni run domain_scout` | 跑某 topic 调研 |
| `run_scout(topic_id, since)` (Python) | 库调用 |
| `config/domain_scout/<topic_id>.yaml` | topic 配置 |

## 故障排查

| 现象 | 修 |
|---|---|
| SourceFetcher 抓不下 | 网站 HTML 结构改, 改 source 的 adapter 字段 |
| VerifiabilityCheck 大量 FAIL | LLM 编引文 / source_url 不可达, 调 LLM prompt 强约束引文必须复制原文 |
| 中文社媒 digest 质量差 | 局限 2, V0 优先英文源 (github/arxiv) |
| confidence 当精确分数 | 局限 3, 是 LLM 自评不是统计概率, 只做粗排 |
| 同一事件多文章被各自报告 | 局限 5, 当前去重只 url+hash, 事件级聚合是未来方向 |

## 想了解更多

- [README.md](README.md) / [DESIGN.md](DESIGN.md)
- 下游 → ../../_authoring/privacy_publish/
- LLM 铁律 → docs/standards/llm_first.md
