# decisions domain — 决策记录设计

> 用户 2026-06-17 新开「决策记录」线。**主线是决策记录,不是决策提取**——提取只是往统一库灌数的其中一种方式。决策有多个源、要在多个领域落地:对话(claude/codex)、collab platform消息、demogame 策划文档(在这里决策被记进多个标准化中间文档)、手写札记。本 domain = **一套源无关、面无关的统一决策契约 + 一个统一决策库**,把这些汇成一棵可搜索的决策树。

## 为什么(锚在用户手写需求)

权威源:`E:\WindowsWorkspace\用户原始需求存档\用户手写的需求\决策树可执行化的工程本质与数据密集型开发.md`。要点落到本 domain:

- **决策树可操作化是公司自动工作流的本体**;LLM 之后"沉淀能力"成本大降,**个人决策树沉淀第一次 ROI 为正**——所以"把一切决策记录下来"现在值得做。
- **人类交互甜蜜点**:让决策贴着已有的富信息**中间契约**(设计文档、代码、bug 单、AI 产物、collab platform消息)沉淀——好读、好改、能在那插人类强控制节点。→ schema 里的 `anchor` 字段就是这个甜蜜点。
- **符号统一体系**:决策树的规模化硬成本在搜索/分类/寻址;符号统一能把硬工作大幅降低。→ 一套公共 envelope 契约,所有源、所有 kind 共用。
- **同构/分形**:个人决策树以相同接口接进团队树并保持自身结构。→ 决策树靠 `links`(rests_on/supersedes/parent)链出,而非目录层级。
- **可搜索**:决策空间明晰 + 学习/失败成本可量化时,最佳决策可脱离人搜索出来。→ `belief` 段带 `evidence_query` 和证伪生命周期,为后续自动搜索留口。

## 核心 schema(formats.py)

三件套:

| Format | 用途 |
|---|---|
| `decision.record` | 统一决策库每行契约,也是决策树的节点。`kind=decision\|belief\|comment`。 |
| `decision.observation` | 抽取态:从某个源抽出的原始决策信号,未去重/未接树。源→库 的桥。 |
| `decision.catalog_item` | 索引一条,按 id/anchor/project/tag/alias 召回。 |

**一条记录三种 kind**(公共 envelope + 各自专属段):
- `decision` —— 决策点的选择。专属:`decision_space`(候选项,**必列被否决项**)、`rationale`、`evidence`、`boundary`(失效边界)、`human_override`(人工可否决点)。
- `belief` —— 猜想/信念(可证伪),决策立足于它。专属:`verification_status`、`risk_if_wrong`、`evidence_query`、`challenge_log`、`resolution`。
- `comment` —— 对产物的评论(场景一:对 AI/他人产物发表意见)。可经审议 `promoted` 晋升为 decision。

**决策 vs 猜想为何分 kind 而不合一**:手写需求里"如何记录决策"与"如何产生猜想"是两件事——决策是树上的选择,猜想是关于世界的信念(决策立足其上、可被证伪)。合一会丢掉猜想的证伪生命周期。两者用 `links.rests_on` 连接:一个决策 rests_on 若干 belief。

## 字段继承(不重复发明,全部来自存量)

| 段 | 继承自 | 关键字段 |
|---|---|---|
| 猜想/信念 | hypothesis V1(`_diagnosis/doctor` + `_core/evolution/workflow/hypothesis.py`) | confidence / authority / verification_status / risk_if_wrong / challenge_log / resolution |
| 决策 | decision_model(`data/domains/demogame_ux/.../decision_model.md`) | decision_space(必列被否决项)/ evidence(证据边界)/ boundary / human_override |
| 召回 | research domain(`research.catalog_item`) | aliases / tags / catalog_item / 统一库 jsonl+索引范式 |
| 评论闭环 | Spec-083 本体决策评论闭环 | comment 挂 anchor、open→resolved→promoted、edit_log 审计 |
| 工作假设 | plan 模板 §9–§13 假设树 | rests_on/依赖链、verification 四档 |

## 决策树怎么长出来

不靠目录,靠 `links` 在记录间链边:
- `rests_on`(decision→belief):决策立足哪些猜想。猜想一旦 `falsified`,顺着反向链能找出受影响的决策。
- `supersedes`(decision→decision):决策演化,旧决策被取代但留痕。
- `parent`(→decision):子决策/分形。
- `anchor`(→中间契约):决策挂在哪份文档/代码/AI 产物/collab platform消息上。

这样个人记录能以相同接口并进更大的树并保持自身结构(同构)。

## 多源多落地面(路线,按序)

**源(往统一库灌数,各产 `decision.observation`)**:
1. claude/codex 对话 —— 复用已有 `_governance/work_history`(挖重复需求/指正)+ `boss_sight` 札记决策抽取,改成产 observation 汇入本库(它们保持原位当上游,不拆)。
2. collab platform消息 —— **新增源**:目前只接了collab platform文档/表格,没接 IM 消息。需在取数层加collab platform messages。
3. 手写札记 —— 已有 boss_sight authored 抽取,接进来。

**落地面(决策记录的去处,非只统一库)**:
- 统一决策库 `data/domains/decisions/library/records.jsonl`(append-only + 索引,照 research 去重/增量范式)。
- **demogame 区域**:决策被记进**多个标准化中间文档**(策划阶段文档=决策的人读载体)。这条在执行端(AIWorkSpace)落地,与本 domain 共用同一套 record 契约(符号统一),但物理隔离、不反向依赖本仓;成熟后以 workflow js 导出过去。

## 设计取舍(已定)

- 决策 / 猜想分 kind 不合一(理由见上)。
- 统一库先 jsonl + 倒排索引,不上向量;库大了再加语义近邻(同 research)。
- envelope 公共字段尽量薄,专属段按 kind 挂;`required` 只锁 id/kind/statement,其余渐进填(抽取态信息常不全)。
- 苦力抽取/精炼一律便宜模型;判 kind / 接树这类错代价大的步骤上中端档。

## 已落地(2026-06-18 地基)

源无关地基已通,`omni decisions` 可手记/召回/接树/体检(端到端验证过):

1. ✅ `library.py` —— 统一库读写 + 按显式 id 增量合并(累积段并集、链合并、challenge_log 追加、标量最新胜)+ 墓碑软删 + 落库校验(schema + 决策须列被否决项 lint)。去重键 = 显式 id(`DEC-/BLF-/CMT-YYYY-MM-DD-NNN`),非题目归一。
2. ✅ `catalog.py` —— 纯投影 library 的召回层:`find` 现算不馊(id 直击→别名精确→子串→词重叠→便宜模型语义兜底可降级);`index.json` 供 grep。包根稳定入口 `record` / `lookup_or_none` / `find_local`(见 `__init__.py`)。
3. ✅ CLI `omni decisions`(record / list / find / show / link / mark / doctor / reindex / status)。`record` 用 `--choose`/`--reject` 强制显化被否决项。
4. ✅ 产物落 `data/domains/decisions/library/{records.jsonl,index.json}`;首条种子=本次会话真实决策 DEC-2026-06-18-001(rests_on BLF-2026-06-18-001)。

## 待加(下一刀,按序)

1. 抽取管线:`observation` 生产者(先对话源,复用 work_history 数据源),refine 节点判 kind + 补 anchor/links + 经 `library.upsert` 并库。
2. collab platform消息源:取数层加 `feishu_messages`,产 observation。
3. demogame 落地面:record→标准化中间文档的渲染器(在执行端,共用契约)。
4. 决策树视图:沿 `links.rests_on` 反向链,某 belief `falsified` 时列出受影响决策(证伪传播)。

## 复用的现成积木

统一库去重/增量 `packages/domains/research/library.py` · 索引召回 `research/catalog.py` · 假设结构与证伪机 `_diagnosis/doctor/builders/hypothesis_*` + `_core/evolution/workflow/hypothesis.py` · 对话源 `_governance/work_history/sources.py` · 札记决策抽取 `dashboard/boss_sight/authored/extract.py` · LLM 苦力 `runtime/llm/structured.call_json` · 批量并行 `runtime/llm/batch.run_parallel_items`。
