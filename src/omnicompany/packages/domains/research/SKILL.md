---
name: research
description: 通用的公开调研管线 + 一个累积去重的统一研究库。把"拆题→并行联网查证带来源→对抗式逐条核源→综合成不打分报告→落库"固化成可反复跑的 Team。何时用:需要对一个题目做联网调研、查证、文献/方案综述,且要避免重复调研同一题目时。触发关键词:调研、研究、research、查证、文献综述、联网搜索、找方案、search、综述、信息收集、查重、不重复调研。开跑前会查重(同题带出增量),只认带来源的发现、不编造、结论不打分。
---

# research

research 是一条通用性价比研究管线,加一个统一研究库(累积、开跑前查重,绝不重复调研同一题目)。本质是把手搓的"拆题→并行联网查证带来源→综合成不打分报告"固化成可反复跑的 Team。苦力步骤走便宜模型,拆题/反思/核源这类错代价大的步骤走中端模型。

## 何时用

- 要对一个题目做联网调研,产出带引用来源、不打分的结论报告。
- 想做多视角覆盖的方案/文献综述,避免因术语别名没对上漏掉冷门但有效的方案(STORM 式召回)。
- 需要对每条结论对抗式核源(判 supported/partial/unsupported),unsupported 显眼标 ⚠。
- 同一题目可能反复调研,要先查重、只补缺口而非从头再跑。

## 怎么跑

```bash
omni run research.run -i topic="<调研题目>"
# 离线 mock 检索(无网/自测):
omni run research.run -i topic="<题目>" -i dry_run=true
```

参数(CLI 实际注册):`topic`(必填,调研题目)、`max_results`(保留片段数量级,默认 6)、`dry_run`(离线 mock,等价 `OMNI_WEB_SEARCH_DRY_RUN=1`)。查累积库:`omni research library [--topic X]`。

## 管线节点

六节点 Team(`research.run`):

1. **intake**(RULE)—— 归一化题目 + 查重门(同题带出增量),建 run_dir。
2. **planner**(LLM·中端)—— 先搜后拆:拿原题搜背景,产互不重叠的多视角子主题(含"基础覆盖"+"冷门/替代"兜底);失败有确定性兜底拆题。
3. **orchestrate**—— 节点内循环:并行派子研究员(各自独立局部上下文:多 query 搜→抓→单页摘要→抽带来源发现)→反思看覆盖账本指缺口+打捞未用上的料→有界迭代(默认 2 轮,衰减广度)。覆盖账本落 `data/domains/research/coverage/`。
4. **synthesize**(LLM·便宜档)—— 据带来源发现综合成接地、带引用编号、不打分的结论;失败降级。
5. **claim_verify**(LLM·中端)—— 对抗式逐条核源:并行抓原始来源判 supported/partial/unsupported 写回 `support`。
6. **library_write**(RULE)—— 去重累积 upsert 进统一库,渲 `report.md`。

统一研究库:`data/domains/research/library/records.jsonl`(append-only,最新行权威)+ `index.json`(topic_norm/keyword→record_id);首跑 dup=False,同题再跑 dup=True 走增量合并。

## 配置

- 实跑真实召回需配检索 key:设 `SERPER_API_KEY`(serper.dev 免费额度)即自动生效,零改码切到 serper;不配则免费源(DuckDuckGo 抓取)已失效拿不到真实结果。
- `OMNI_WEB_SEARCH_DRY_RUN=1` 或 `-i dry_run=true`:离线 mock 检索,用于自测管线骨架(mock 无内容时产 0 findings,防幻觉)。
