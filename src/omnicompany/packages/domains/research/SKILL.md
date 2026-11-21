---
name: research
description: 公开调研 / 查资料 / 联网搜一下 / 调研某主题 / 找参考 / 文献方案综述的统一入口。优先由当前前台 agent(Claude Code / Codex)使用自己的原生 WebSearch/WebFetch 调研；可把相互独立的研究角度交给自己的 subagent 并行，主 agent 负责核源和综合。禁止把 omni agent 当默认调研执行者。omni 只提供可选的确定性脚手架：开跑前查重(check)、落进累积去重的统一研究库(save)。触发关键词:调研、研究、research、查证、文献综述、联网搜索、找方案、search、综述、信息收集、查重、不重复调研。开跑前先查重(同题只补缺口不重搜),只认带来源的发现、不编造、结论不打分。
---

# research —— 原生搜索做调研 + 累积去重的统一研究库

调研的「搜 + 读 + 综合」直接用**你自己(Claude Code / Codex)的原生 WebSearch / WebFetch + 判断力**来做——厂商级原生搜索 + frontier 模型的提问/判断/综合,已经胜过「哑关键词 API + 便宜模型编排」那一套。omni 不再做外部检索,只负责三件原生搜索不白送你的事:

1. **明确需求** —— 把模糊一句话锁成清晰搜索意图 + 候选别名/角度(由你做)。
2. **全面性核查** —— 搜完反问「每个同义说法/角度都真搜过吗」,别因为搜错一种词就漏掉其实存在的内容(由你做,STORM 召回思路)。
3. **可复用库** —— 搜过一次就落库,日后查得到、同题不重搜(确定性 CLI:`check` / `save`)。

## 铁律

- **前台模型原生搜索优先**：当前 agent 自己用 WebSearch / WebFetch 搜、读、判断和综合；需要并行时可分派给自己的 subagent，但主 agent 必须复核关键来源并形成最终结论。禁止默认转交 `omni run research.run` 或其它 omni agent。
- **只用原生 WebSearch / WebFetch**。禁外部搜索 API(Serper / Tavily / DuckDuckGo)、禁 requests/BeautifulSoup/Playwright 自写爬虫、禁临时写「检索→总结」小脚本。
- **先查重再搜，但脚手架不阻塞调研**：动手前优先跑 `omni research check`；同题已有就只补 `perspectives_open` 的缺口，不从头重搜。若 CLI 不可用、明显变慢或无观测，记录降级并直接进入原生搜索，不得把查重设施变成主任务阻塞点。
- **全面性核查是强制步**:同一概念的多种说法/别名/角度都要真搜过,别让其中一种没搜出来就当「没有」。
- **只认页面里有的**:每条结论带它依据的 url,页面里没支撑的不写;客观、给证据、**不打分**。
- **产物只落统一库**:`omni research save`,别让研究记录散到别处。
- **发现设施问题立即止损**：任何可选 helper / worker 若 60 秒内无阶段性输出，先检查进程与产物；首次超时只允许在缩小题目或确认根因后重试一次；第二次同类超时、出现孤儿进程、无结果目录或不可观测时，立即终止本次 helper、清理本次启动的残留进程，改由前台 agent 原生搜索，并把稳定性/时间效率问题记入修复范围。禁止盲目续等或重复启动。
- 可选无人值守 `research.run` 会在本次 run 目录分别写阶段事件流与 native 状态文件；不要用比内部总时限更短的 shell timeout 代替 worker 终止协议。

## 协议(交互式,前台 agent 跑)

### 0. 先查本地 + 查重

```bash
omni research check "<题目>"          # JSON: exists / 已覆盖角度 / 还缺的角度
omni refs find "<关键词>"             # 本地有没有已拉 repo / 资料 / 旧研究记录
```

`exists:true` → 读出已有记录,只针对 `perspectives_open` 补缺口(增量),**不重搜已覆盖的**。

### 1. 明确需求(clarify)

把题目锁清楚再搜——题目太泛先问 2-3 个澄清问题(范围 / 时间 / 什么算答上了)。同时头脑风暴:
- **别名 / 同义说法**:同一个东西有哪几种叫法(中英、行话、产品名 vs 通称)。这串是后面全面性核查的清单。
- **多视角角度**:机制原理 / 对比选型 / 反对方案与替代 / 落地与坑 / 基础覆盖 / 历史与争议。至少含一个「冷门/替代」视角,防只顺主流钻。

### 2. 原生搜索 + 读(你自己做)

- 用 **WebSearch** 逐角度搜,**同一角度换几种说法各搜一次**(覆盖第 1 步的别名清单)。
- 用 **WebFetch** 抓有料的页读正文,抽出带 `source_url` 的发现(claim + 依据 url)。
- 可将彼此独立的角度拆给自己的 subagent 并行搜索；每个 subagent 必须返回查询角度、直接 URL、事实/推论边界，主 agent 不得把其摘要未经复核直接当结论。
- 顺藤摸瓜有界:撞见新线索可再追一两轮,收益递减就停。

### 3. 全面性核查(comprehensiveness)

落库前对着第 1 步的别名/角度清单逐项核:**每个同义词、每个角度都真发起过搜索了吗?** 任一缺口 →
- 能补就再定向搜一次;
- 暂不深挖的写进 `perspectives_open`(诚实留白,日后增量)。

### 4.(可选)核源

把握不准的 claim,回读它声称的来源页,标 `support` = supported / partial / unsupported(默认从严,看不到明确支撑就别给 supported)。

### 5. 落库(save)

把综合好的产物写成 JSON 落进统一库(同题自动增量合并 + 投影 catalog + 渲 report.md):

```bash
omni research save --file <path-to.json>     # 或 -j '{...}' 或 stdin 管道
```

入参 JSON:

```json
{
  "topic": "调研题目(必填)",
  "summary": "2-4 句概述",
  "findings": [
    {"claim": "具体结论", "source_url": "依据 url", "support": "supported|partial|unsupported|unverified"}
  ],
  "sources": [
    {"title": "页标题", "url": "...", "snippet": "...", "text": "正文(给了就落本地快照,原页失效仍可回源)"}
  ],
  "keywords": ["关键词…"],
  "aliases": ["别名/同义词…(为日后查重召回)"],
  "perspectives_covered": ["这次覆盖了的角度…"],
  "perspectives_open": ["还没覆盖的角度…(诚实留白)"]
}
```

## 无人值守 / 批量(codex worker)

同一套协议、同一个 `omni research save` 落点,把执行者换成带原生搜索的 codex worker 即可(走 `omni worker run codex`),适合批量/定时。交互式一律走本 skill，由前台 agent 自己搜；不得为了普通交互式调研启动 `omni run research.run`。

## 导航 / 查询

| 要做 | 命令 |
|---|---|
| 开跑前查重(JSON,给 agent 读) | `omni research check "<题目>"` |
| 落库(原生搜完综合好的产物) | `omni research save --file <json>` |
| 看研究库累积了什么 | `omni research library` |
| 给题目查同题(人读) | `omni research library --topic "<题目>"` |
| 看落点 + 库计数 | `omni research status` |
| 列带病记录(校验不过) | `omni research doctor` |
| 先查本地有没有(repo/资料/旧记录) | `omni research find-local "<关键词>"` / `omni refs find` |

## 落点

统一研究库由 `omni research save` 管理：append-only 记录流（最新行权威）+ 查重倒排索引 + 原文快照 + 待发布报告。同题再跑 = 增量合并，richness 单增，不重复；不要直接编辑库文件。

> 历史:早期是六节点 Team(便宜模型编排 Serper/Tavily 外部搜索)，已被本「原生搜索 + 三件脚手架」取代；外部搜索源与便宜模型编排节点退役。现在仍注册的三节点 `research.run` 只是有总时限、空闲时限和状态文件的可选无人值守 Codex 通道，不是交互式默认入口；落库核心 `library.save_research_record` 两路共用。
