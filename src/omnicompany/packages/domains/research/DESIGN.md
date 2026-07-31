# research domain — 公开调研管线设计

> 用户 2026-06-14 新开「公开调研」线。**2026-06-30 重大校准**:弃外部搜索 API,改用带原生搜索的 frontier agent。核心 = **用 Claude Code / Codex 自己的原生 WebSearch/WebFetch 做搜+读+综合 + 一个累积去重的统一研究库(开跑前查重,绝不重复调研)**。omni 只负责原生搜索不白送的三件事:明确需求、全面性核查、可复用库。

## 为什么转原生搜索

旧设计是「便宜模型(qwen/deepseek)编排哑关键词 API(Serper/Tavily/DDG)」。两层里更弱的都被替掉:厂商级原生搜索本身会做查询改写/相关性排序,胜过哑 API;frontier 模型的提问/判断/综合胜过便宜模型。附带扔掉一整类基础设施:检索 key、后端选择、抓取 SSRF 守卫、DDG 正则失效。

## 现状:两条运行时,一个核心(2026-06-30,端到端验证过)

「搜+读+综合」交给带原生搜索的 agent;omni 只留确定性脚手架。两条运行时共用同一协议、同一落点 `library.save_research_record`:

- **交互式(前台 agent)** —— `research` SKILL 编码协议,Claude Code 用自己的 WebSearch/WebFetch 搜,`omni research check` 查重、`omni research save` 落库。验证:跑「uv vs pip/poetry」真题,零外部 API 落库(richness 18)。
- **无人值守(codex)** —— `omni run research.run -i topic="<题目>"`,三节点 Team:
  1. **intake**(RULE)—— 归一化题目 + 查重门(同题带出增量),建 run_dir。
  2. **native**(`routers/native.py`)—— codex exec(`-c tools.web_search=true`,readonly 沙箱,`--output-schema` 强约束)按调研协议搜+读+核源+综合,产带源记录。
  3. **library_write**(RULE)—— 去重累积 upsert 进统一库,渲 report.md。
  验证:跑「bun vs node/deno」真题,codex 原生搜索产 21 条带源发现 / 12 官方文档源 / richness 39。

  这一路仅用于无人值守/批量，不是交互式默认入口。默认总时限 600 秒、连续 60 秒没有可观测输出即终止；`run_dir/native_status.json` 记录 running/finished/interrupted、时限、worker 状态与事件计数，`run_dir/native_events.jsonl` 在 Codex 输出事件时实时追加，可直接 tail 查看阶段进展。外层调度器的等待时限必须长于内部总时限，不能用短 shell timeout 代替 worker 自己的终止协议。

### 三件脚手架(原生搜索不白送的)

1. **明确需求** —— 模糊一句话锁成清晰搜索意图 + 候选别名/角度(agent 做;codex 路写进 native 的 prompt 协议)。
2. **全面性核查** —— 搜完反问「每个同义说法/角度都真搜过吗」,别因搜错一种词漏掉其实存在的内容;缺口补搜或写进 `perspectives_open`。
3. **可复用库** —— 搜过就落库、日后查得到、同题不重搜。`omni research check`(JSON 查重门)+ `omni research save`(产物落库)。

### 落库契约(三路共用)

任何执行者只产这一坨,sink 照吃:
```
{summary, findings[{claim, source_url, support}], sources[{title,url,snippet,text?}],
 keywords[], aliases[], perspectives_covered[], perspectives_open[]}
```
`library.save_research_record()` = 组装 + upsert(topic_norm 为查重键,同题增量合并 findings/sources/keywords,richness 单增,墓碑软删)+ 投影 catalog + 渲 report + 源快照(给了 text 就落本地留底)。

**统一研究库**:`data/domains/research/library/records.jsonl`(append-only,最新行权威)+ `index.json`(倒排)+ `snapshots/`(源原文快照)+ `reports/`(待发布 md)。`omni research library [--topic X]` 看累积/查同题。

## 设计取舍(已定)

- 搜+读+综合一律交给带原生搜索的 frontier agent,**不再用便宜模型编排哑搜索**。codex 路 model_policy=none(调研要好脑子)。
- 研究库先 JSONL+倒排,不上向量;库大了(几百条+)再加语义近邻。
- 全面性核查有界(别无界发散);缺口诚实写进 perspectives_open,下次增量补。
- 独立 research domain,不塞进 absorption(那个学 AI 编码工具,定位不同)。
- 发布走 curated + 用户自己的a cloud provider服务器,不用 GitHub Pages。

## 复用的现成积木

外部 worker `services/_core/agent/external_workers`(codex exec 适配器,经 `metadata["codex_config"]` 传 `-c` 开 web_search)· 落库去重范式对标 `material_registry._dedup` · 本地资产 catalog `catalog.py`(别名召回,`omni refs find`)· 发布链 `packages/domains/personal_site`。

<details><summary>历史:已退役的外部搜索 + 便宜模型编排(2026-06-14 ~ 06-30)</summary>

早期六节点 Team(SOTA parity):intake → planner(中端模型先搜后拆多视角)→ orchestrate(并行子研究 + 反思有界迭代 + 覆盖账本)→ synthesize(便宜档接地综合)→ claim_verify(中端对抗核源)→ library_write。搜索走 `sources/web.py`(Tavily>Serper>DDG 按 key 自动选),编排走 qwen3.6-plus / deepseek。

硬阻断:免费检索源(DDG 抓取)失效,真实召回靠用户配 `SERPER_API_KEY`。2026-06-30 用户校准「直接抛却外部搜索源」,这套被原生搜索取代;`sources/web.py`、`routers/deep.py`(Planner/Orchestrator)、`routers/synth.py`(Synthesize/ClaimVerify)、`prompts.py` 已删,落库核心 `library.save_research_record` 三路共用保留。

</details>
