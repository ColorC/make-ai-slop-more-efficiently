---
name: decisions
description: 一套源无关、落地面无关的统一决策契约 + 一个统一决策库,把分散的决策/猜想/评论汇成一棵可搜索的决策树。主线是决策记录(手记+查回+接树),提取只是往库灌数的一种方式。何时用:要记录一个决策点及其被否决项与理由、记录一个可证伪的猜想/信念、对某产物发表评论、或查回历史决策并沿依赖链追溯影响时。触发关键词:决策、决策记录、决策树、记一笔、为什么这么选、被否决项、猜想、信念、belief、证伪、评论、decision、查决策、接树、rests_on。一条记录三种 kind(decision/belief/comment),决策必列被否决项。
---

# decisions

decisions 是源无关、面无关的统一决策契约,加一个统一决策库。它把来自对话、消息、文档、手写札记的决策信号汇成一棵靠 `links` 链边(不靠目录)的可搜索决策树。主线是决策记录而非提取——先能手记一条、查得回、接进树才算可用。决策与猜想分开成不同 kind,因为决策是树上的选择、猜想是它立足的可证伪信念,合一会丢掉猜想的证伪生命周期。

## 何时用

- 记录一个决策点:选了什么、否决了什么(必列被否决项)、为什么、失效边界、人工可否决点。
- 记录一个可证伪的猜想/信念(belief):风险、怎么验证、证伪日志,决策 `rests_on` 它。
- 对 AI 或他人产物发表评论(comment),可经审议晋升为决策。
- 查回历史决策(id/别名/子串/语义召回),沿 `rests_on` 反向链追某 belief 被证伪后受影响的决策。

## 怎么跑

decisions 主线走 `omni decisions` 子命令(源无关的人读/手填入口),不是 `omni run`:

```bash
# 手记决策(强制显化被否决项)
omni decisions record --kind decision -s "选了X" --choose "X:稳" --reject "A:贵" -r "因为..."
# 手记猜想
omni decisions record --kind belief -s "猜Y成立" --risk high --query "怎么验证"
# 查回 / 看一条 / 接树 / 体检
omni decisions find "X"
omni decisions show <id>
omni decisions link <决策id> rests_on <猜想id>
omni decisions doctor
```

子命令全集:`record / list / find / show / link / mark / doctor / reindex / status`。存量对话炼化(往库灌 observation)走 extract runner,断点续跑+增量,用便宜模型抽、不烧主对话 token。

## 管线节点

- 核心 schema 三件套(`formats.py`):`decision.record`(库每行契约=决策树节点,kind=decision|belief|comment)、`decision.observation`(抽取态,源→库的桥,未去重未接树)、`decision.catalog_item`(召回索引一条)。
- 公共 envelope 薄,`required` 只锁 id/kind/statement;专属段按 kind 挂:decision 有 decision_space/rationale/evidence/boundary/human_override;belief 有 verification_status/risk_if_wrong/evidence_query/challenge_log/resolution;comment 可 promoted 晋升。
- 决策树靠 `links` 链边:`rests_on`(决策→猜想)、`supersedes`(决策→决策)、`parent`(子决策/分形)、`anchor`(挂在文档/代码/AI 产物/消息上)。
- 已落地地基:`library.py`(按显式 id 增量合并+墓碑软删+落库校验,决策须列被否决项)、`catalog.py`(纯投影 library 的召回:id 直击→别名精确→子串→词重叠→便宜模型语义兜底)、CLI、产物落 `data/domains/decisions/library/{records.jsonl,index.json}`。

## 配置

无需额外配置。语义召回兜底与提取炼化用 omni 内置 LLM 网关(便宜/中端档),无需单独配 key;库与索引落 `data/domains/decisions/library/`。
