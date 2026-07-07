<!-- [OMNI] origin=claude-code domain=domains/decisions ts=2026-07-04T00:00:00Z type=research status=active -->

# 决策阶段/产物之间的标准化动词——开源+学术调研(2026-07-04)

> 背景:统一设计工作室方向(决策库 DEC-2026-07-04-007)之下的猜想 BLF-2026-07-04-001——
> "项目上下文视图+强制标签只助人机熟悉决策流程;要支撑蒙特卡洛式探索与明文训练,
> 需再加一层决策阶段/产物之间的标准化动词(问题拆分/反证/联想/推导/生成/问题延伸等)"。
> 本报告是该猜想 evidence_query 的文献半程(另一半=首棵真实决策树上实战标注)。
> 用户要求:结果不缥缈——每个体系必须带逐字词表+存活证据+一手来源。
> 产出方式:7 个领域搜索 agent + 1 个本地历史调研盘点 agent + 1 个对抗核查 agent(共 51 个体系)。

## 一、结论速览(先看这里)

1. **收敛规律(最重要)**:活下来的词表无一例外是"核心动词≤10 + 可插拔外围";
   扁平大表全部在使用中坍缩——TRIZ 40 条原理实际 4 条覆盖约 60% 案例、
   Bloom 19 个认知子过程实际只用 6 大类、CiTO 41 个引文动词真实标注不足 10 个、
   Walton 96 个论证图式常用的只有 10-15 个。我们的动词表首版就该按"≤10 核心 + 扩展槽"设计,
   并预期它还会继续坍缩(埋点统计使用频率,按数据砍)。
2. **Design Rationale 学派的死因诊断直接适用于我们**:记录发生在决策之后而非决策之中、
   记录者不获益(Grudin 错位)、节点摘要负担。唯一活了 20 年的 DRed(罗尔斯-罗伊斯)靠的是
   "当场即获益 + 免摘要 + 免专职引导者",不是类型学完备(类型最全的 DRL 死得最早)。
   **对我们是机会而非警告:AI 替人付记录成本,恰好瓦解三条死因中的两条**——
   这是 LLM 时代重做这件事的根本理由,也解释了为什么旧系统死了而我们可能活。
3. **树探索的先证**:rStar 用五个人类式推理动作(A1 提一步思维/A2 提剩余全部步骤/
   A3 拆子问题并答/A4 重答子问题/A5 改写问题)驱动 MCTS,消融实验证明动作越全效果越好
   (GSM8K 70.5%→75.0%)。但注意后续 rStar-Math 又把动作收窄为单一类型——
   动词集对探索的价值在"提出多样性",不在分类学完备。
4. **明文训练的先证与警示**:ExpeL 证明规则库要有质量门槛(把反思无脑纳入 insight 生成
   反而掉 10 个百分点);GEPA 证明反思式明文优化能以约 35 倍更少 rollout 平均高 GRPO 6%
   (最高 20%——注意是最高不是平均,核查纠正过的数字);Constitutional AI 2026 版转向
   "必须保留'为什么'的叙事,纯规则罗列撞泛化天花板"。
5. **结构性建议(比选哪几个词更重要)**:PDDL/STRIPS 的启示是动词表可以不做穷尽枚举,
   而做"动作 schema(动词名 + 前置状态 + 效果 + 理由 + 反证挂载)",词只是 schema 实例的标签;
   PROV-O 与 OpenLineage 的启示是**能被工具自动派生的边才活得下去**(PROV 活、CiTO 死,
   差别就在要不要人手动选精确动词);两层法 = 小而稳的边类型表 + 每条边可挂扩展 facet。
6. **三套候选动词集草案**见第三节 opus 核查报告的提炼段:
   草案 A「认知动作」6 词(拆分/推导/联想/生成/反证/延伸,最贴用户直觉);
   草案 B「溯源关系」7 词(派生自/修订自/受影响于/拆分为/关联/冲突于/失效由,机器可自动初判);
   草案 C「schema 化最小内核」(推荐作地基,A/B 词填进 verb 槽)。
7. **文献答不了、只能实战答的四个问题**:动词间边界的标注一致性(联想 vs 推导);
   明文规则库 vs 微调在裁决场景的头对头对照(文献没有);反向固化器产出的规则质量形态;
   词表坍缩点在哪。全部指向同一个动作:在首棵真实决策树上标注 100 条再回来修表。

## 二、本地历史调研盘点(为什么过去的搜索"缥缈")

过去不缥缈在搜索质量,缥缈在**落地断链**:
- 2026-05-15 reasoning-network 批(三份约 2500 行调研)对 Toulmin/IBIS/Dung AF/ASPIC+/
  Carneades/AIF/PROV/GSN/CAE/SACM/ADR/Lean/Coq/TLA+ 已做过系统选型且结论明确
  (Markdown+YAML+JSON Schema 主编辑层/Argdown 论证图/ADR 决策层/形式证明只做可选后端),
  **但承诺的 schema/tests/reasoning_ledger 全部没建**——地图画完了地没建。
- [2026-06-18]DECISION-MEMORY 是唯一真正落地在跑的(records.jsonl 3613 行),
  缺的是反向固化器和网页浏览面。
- **[2026-06-27]EXPLORATION-PATH-VIZ 实际已实现**(exploration 六模块 + material_graph.py +
  前端 entities/material-graph 三文件已注册进导航,git log 至少 3 次提交)——
  此前"计划已写实现未开工"的记忆是过时的;缺的是"最终效果"验收演示和真实数据。
- 结论:**这次不要再做第四轮文献综述,要做的是把词表钉进首棵可见决策树**。
  下面的逐字词表附录就是给这一步当零件库的。


## 三、对抗核查与提炼(opus 评审全文:核真/查漏/三套草案)

I have enough to synthesize. The Goknil et al. requirements-relation taxonomy (requires/refines/partially refines/contains/conflicts, Softw Syst Model 2011) is a formal, cited, small verb set directly naming "refines" and "conflicts". Accountable talk moves (revoice / press for reasoning / agree-disagree / provide evidence) are a live, real-classroom dialogue-move vocabulary — relevant to human-AI collaborative annotation but the moves are conversational, not artifact-relation verbs.

I now have all verifications. Writing the final report.

---

# 对抗核查报告：决策动词表调研

## 一、核真：抽查一手来源对回结果

我用 WebFetch/WebSearch 对回了 12 项被点名的核心声明。结论按"核实 / 存疑 / 错误"标注，附一手证据。

### 核实（逐字对回一手来源，无出入）

| 声明 | 核查结果 | 一手证据 |
|---|---|---|
| **rStar 动作集 A1-A5** | **核实**。A1 提出一步思维 / A2 提出剩余全部步骤(=标准CoT) / A3 提出子问题并回答 / A4 重答子问题 / A5 改写问题。消融表(Table 1, GSM8K 200题)逐字对上:A3-only **70.5%** → A3+A5 **72.5%** → A3+A4+A5 **73.5%** → A2+A3+A4+A5 **74.0%** → 全A1-A5 **75.0%**。 | ar5iv 全文 2408.06195 |
| **Graph of Thoughts 算子表** | **核实**。§3.2 只正式定义三个变换,V+/E+ 记法逐字对上:Generation `V+={v1+…vk+}, E+={(v,v1+)…}`;Aggregation `V+={v+}, E+={(v1,v+)…(vk,v+)}`;Refining `V+={}, E+={(v,v)}`。Score/Validate/KeepBest **未获同级形式化**,只作为评分函数 ℰ 和排序函数 ℛ 出现——报告"辅助算子非同级"的判断正确。 | ar5iv 全文 2308.09687 |
| **Self-Discover 模块数** | **核实**。Table 2/附录A 共 **39** 个原子推理模块;三段 SELECT/ADAPT/IMPLEMENT 逐字对上。 | ar5iv 2402.03620 |
| **FBS 八过程** | **核实**。formulation(R→F→Be)/synthesis(Be→S)/analysis(S→Bs)/evaluation(Be↔Bs)/documentation(S→D)/reformulation I(S→S')/II(S→Be')/III(S→F' via Be),共 **8** 个,节点类型 R/F/Be/Bs/S/D 六类,全部逐字对上。 | Wikipedia FBS ontology(与 Gero 原文一致) |
| **Walton 专家意见六问** | **核实**。Expertise / Field / Opinion / Trustworthiness / Consistency / Backup Evidence 六条 critical questions 逐字确认。报告标"存疑待核"的这条现已坐实。 | Springer《The Assessment of Argumentation from Expert Opinion》等交叉印证 |
| **PROV-O 核心关系** | **核实**(略修正,见下)。三类 Entity/Activity/Agent 确认;starting-point **对象属性7个**(wasGeneratedBy/used/wasInformedBy/wasDerivedFrom/wasAttributedTo/wasAssociatedWith/actedOnBehalfOf),另有 startedAtTime/endedAtTime 是**数据属性**。报告写"约12个核心关系"偏高——把扩展属性也算进去了;严格的 starting-point 对象属性是7个。 | w3.org/TR/prov-o |
| **ExpeL 四算子 + 反证据** | **核实**。ADD/EDIT/UPVOTE/DOWNVOTE 四算子逐字确认(ADD 初始重要度=2,归零删除)。"把反思纳入 insight 生成反而伤性能"的消融也确认:HotpotQA 39.0%→29.0%。 | ar5iv 2308.10144 |
| **CiTO 动词数** | **核实(计数需澄清)**。当前版本 **2.8.2**,页面元数据更新 **2026-06-22**(报告写"2017年最近实质版本+2026元数据更新",一致)。cites 子属性数 **41**——注意:抓取时机械列出43行,但其中"citation"是父属性别名、且有正逆成对项,官方表头明确写"41 sub-properties"。报告的41准确。 | sparontologies.github.io/cito |
| **Jira 链接类型** | **核实**。默认4类:relates to / duplicates / blocks / clones(各带正逆),逐字对上。 | Atlassian 官方文档 |
| **JSON Canvas 边无 type** | **核实**(最强OSS负面证据)。边字段仅 id/fromNode/fromSide/fromEnd/toNode/toSide/toEnd/color/label,**无 type 字段**,语义只靠自由文本 label。四节点类型 text/file/link/group 确认。 | jsoncanvas spec 1.0.md |

### 错误 / 需修正

**GEPA 的量化数字被夸大——这是最重要的一处纠正。** 报告多处写"用35倍更少 rollout 跑赢 GRPO **13-20%**"、"效果比 GRPO 高 20%"。一手摘要原文是:**"outperforms GRPO by 6% on average and by up to 20%, while using up to 35x fewer rollouts"**。即:
- 平均只高 **6%**,最高才到 20%;把"最高20%"当"平均"用是错的。
- vs MIPROv2"over 10%"(如 AIME-2025 +12%)这部分准确。

这条是全批调研里"明文沉淀 vs 参数更新(RL)"最硬的正面证据,所以它的数字必须校准:**正确表述是"平均高6%、最高高20%、rollout省至多35倍"**,不能宣传成"稳定高13-20%"。

### 存疑（一手全文未能逐字对回，但substantive claim间接印证）

- **DRed 的 Figure 4 十节点+状态表**:designsociety 下载链接返回落地页、PDF 不可提取,无法逐字复核"10个节点类型/每节点1-5状态"这个精确计数。但检索确认了实质结构:IBIS 启发、Issue/Options/Pro-Con arguments、有向依赖图、节点状态可视化、面向评审会议、部署于航空航天多国。**结论:体系性质与存活证据可信,精确的节点/状态计数标存疑**(与原报告自己在缺口里的标注一致)。
- **DRL 完整关系表、SEURAT 完整词表**:沿用原报告的存疑标注,本次未新增核实(付费墙/扫描件),不改判。

---

## 二、查漏：明显相关但未覆盖的体系

按"是否值得补"分档。

### 值得补（有真实存活证据 + 直接补上被覆盖域缺的动词）

**1. SKOS 语义关系(W3C 标准)——强烈建议补。** 这是覆盖域完全漏掉的一支,恰好是我们最想要的形状:活的 W3C 标准、词表极小、被知识组织领域二十年广泛采用。逐字:
- 语义关系 **6个**:semanticRelation(父)、broader(更一般)、narrower(更具体)、broaderTransitive、narrowerTransitive、related(联想式关联)。
- 映射关系 **6个**:mappingRelation(父)、broadMatch、narrowMatch、relatedMatch、closeMatch、exactMatch。
- **对我们的价值**:`broader/narrower` 是"问题拆分/一般化↔特殊化"的成熟标准命名;`related` 是"联想"的最小可用命名。SKOS 证明了"3个核心关系(broader/narrower/related)撑起整个领域"——是"词表要小"最干净的活体正例,比 CiTO 更该抄。
- 来源:https://www.w3.org/TR/skos-reference/

**2. 需求关系形式化分类(Goknil et al., Softw Syst Model 2011)——建议补。** 软件工程 traceability 这条线里最有价值的不是 traceability link 恢复(那些工具"大多不区分类型"),而是**需求间关系的形式化五分类**,有逐字定义:
- **requires**(R1 满足当且仅当 R2 满足)、**refines**(R1 由 R2 加细节派生)、**partially refines**、**contains**、**conflicts**(R1 满足排斥 R2 满足)。
- **对我们的价值**:直接给出 `refines`(=推导/延伸的精确命名)和 `conflicts`(=反证在"产物层"而非"论据层"的命名)。覆盖域里 IBIS/DRL 的"反证"都挂在论据上,这里的 conflicts 挂在两个产物之间,更贴合我们"版本间连线"。
- 来源:https://link.springer.com/content/pdf/10.1007/s10270-009-0142-3.pdf

### 值得记录、但不作动词表来源

**3. Accountable Talk moves(教育学课堂对话)——记录为"人机协作标注"的旁证,不进词表。** 逐字的高频 move:**revoice(复述转述)、press for reasoning(追问理由)、agree/disagree(表态)、provide evidence(要证据)、build on(接续他人)**。这是极少数**真在真实现场被人主动使用**的对话动作词表(不是事后编码),对回了 design_cognition 那条最大空白("没查到任何体系被真人边工作边主动标注")。但它是**会话动作**(谁对谁说话),不是**产物间关系**,所以只能佐证"human-AI 协作实时标注可行",不能当版本轨迹图的边动词。
- 来源:https://www.fredhutch.org/.../AT-Sourcebook.pdf(Accountable Talk Sourcebook)

### 不值得补(判断 + 理由)

- **诊断推理 abduction 文献**:abduction 是"最佳解释推断"这一**单个推理模式**,不是一套边/节点词表;它会以"由征兆到状态"(Walton's Argument from Sign)的形式已被 argumentation 域覆盖。补它得到的是一个动词(推导的一种),不是一套表。**不补**。
- **AI 安全 debate 协议(Irving et al. / OpenReview 式辩论)**:那是一个**博弈协议**(两个 agent 交替反驳、人类裁判),不产出标准化关系动词表;它的"反驳"结构已被 Walton critical questions + ASPIC+ 三攻击覆盖。**不补**。
- **GTD 类工作流动词**:GTD 的动词是**任务状态流转**(next-action/waiting-for/someday),属于"待办状态机",与"决策/产物间关系"正交,已被 Jira/Linear/OpenLineage 的状态机侧覆盖。**不补**。

---

## 三、提炼：三套候选标准化动词集草案

先明确一条贯穿全部证据的设计约束:**活下来的体系无一例外是"核心动词≤10 + 可插拔外围",死掉的都是扁平大表**(TRIZ 40条前4条覆盖60%案例、Bloom 19子过程实际只用6大类、CiTO 41词真用不到10个、Walton 96图式只用前10-15个)。所以三套草案都锚定核心≤10。

另有一条**结构性选择**(来自 PDDL/STRIPS 的启示):我们要的可能不是"固定动词枚举",而是"动作schema(名字+前置状态+效果)+ 核心动词表"。下游"类蒙特卡洛探索"需要机器可执行的前置/效果谓词,而不只是人类可读标签——这一点文献层已经给出明确方向,但**具体谓词表只能靠实战填**。

### 草案 A —「认知动作」6动词(偏人类直觉,最贴用户举例)

面向:人和AI标注"这一步在做什么认知动作"。

| 动词 | 一句定义 | 继承自 | 为什么取它 |
|---|---|---|---|
| **拆分** decompose | 把一个问题/产物拆成更小的子问题或子部件 | Polya "Decomposing";Self-Discover #9;rStar A3;SKOS narrower | 五个体系共同命名,最稳 |
| **推导** derive | 从已确立的前提/知识推出新结论 | C-K K→K;PROV wasDerivedFrom;Goknil refines;Bloom "inferring" | "推导"在实体层(refines)和活动层(derive)都有先例 |
| **联想** associate | 借与当前问题结构相似的已解案例/概念做迁移 | Polya "Analogy";C-K C→C;Walton Argument from Analogy;SKOS related | "联想"最难命名,取"结构相似迁移"这个交集义 |
| **生成** generate | 产出一个新的候选方案/新版本 | GoT Generation;C-K K→C(disjunction);Bloom "generating" | 图算子层唯一有集合论定义的"造新节点" |
| **反证** refute | 提出反对、指出前提/推理/结论的失效 | IBIS objects-to;ASPIC+ rebut/undermine/undercut;CiTO refutes/disputes;Goknil conflicts | 建议保留 ASPIC+ 三子型作为"反证"的可展开细分 |
| **延伸** extend | 由当前议题/产物衍生出新的相邻议题 | DRL issue "generates";CiTO extends;QOC Question 派生 | DRL 是唯一显式区分"议题衍生"的体系 |

- **规模**:6个,正好落在工作记忆容量内。
- **取舍说明**:砍掉了"聚合/精炼/评估"——聚合(GoT Aggregation)在早期低频,精炼(Refining 自环)可并入"生成的一个特例",评估归到下面的"审阅意见"挂载点而非边动词。
- **只能靠实战回答的**:"联想"和"推导"的边界(结构相似 vs 逻辑推出)在真实设计标注里会不会稳定区分——文献里 C-K C→C 和 K→K 的区分需要专门训练才能一致,这直接预示我们标注一致性风险,**必须实战测**。

### 草案 B —「溯源关系」7动词(偏机器可派生,对齐 PROV-O/LLM抽取)

面向:让边尽量能被工具/LLM自动打上,而非纯靠人力(对回 PROV-O 存活的核心原因)。

| 动词 | 定义 | 继承自 |
|---|---|---|
| **派生自** derivedFrom | B 版本经转换基于 A 版本构建 | PROV wasDerivedFrom |
| **修订自** revisionOf | B 是 A 的修订版,含 A 的实质内容 | PROV wasRevisionOf |
| **受影响于** informedBy | A 决策活动向 B 传递了信息但非直接派生 | PROV wasInformedBy |
| **拆分为** narrower | A 拆出更具体的 B | SKOS narrower |
| **关联** related | A 与 B 有关但无层级 | SKOS related / Jira relates-to |
| **冲突于** conflicts | A 与 B 的满足互斥 | Goknil conflicts / Linear blocking |
| **失效由** invalidatedBy | A 因某活动/证据被判定失效 | PROV wasInvalidatedBy / DRed "now fails" |

- **规模**:7个。
- **取舍**:全部选可由 mtime/血缘/引用自动初判的关系,把"为什么"的语义留给 facet(见下)。这套的代价是丢了"认知味"(不像人话),但下游可复用性最高。
- **关键设计**:借 OpenLineage/PROV 的两层法——**边类型词表小而稳(这7个)+ 每条边挂可扩展 facet**(存审阅意见/引用的 critical question/明文规则)。这样"词表要小"和"要支撑明文规则积累"不打架。

### 草案 C —「schema化」最小内核(推荐作为地基,与A/B并存)

不做固定动词枚举,做一个 **动作schema** 让人和AI往里填:

```
Move := { verb(自由命名/从A表选), 
          from_state(前置谓词集), 
          to_state(增/删谓词集), 
          rationale(为什么,叙事非结论), 
          challenges }
```

- **继承**:PDDL action schema(precondition+effect)+ Walton critical questions(反证=结构化追问清单而非单标签)+ Constitutional AI 2026转向(必须存"为什么"叙事,不能只存结论条目)。
- **为什么推荐它做地基**:它同时满足(a)人机同词表——verb 槽可填A表;(b)类蒙特卡洛探索——from/to_state 是机器可执行谓词;(c)明文训练——rationale + challenges 就是"把裁决沉淀成可读规则"的载体,且用 MCTS+DPO 的"同层取最优最差成偏好对"结构比绝对打分更适合喂回AI(rStar-Math 已证绝对打分不如排序偏好对)。

---

## 四、明确指出:只能靠实战标注回答、文献答不了的问题

1. **6-8个动词在真实主观设计标注中的一致性(inter-annotator agreement)有多高**——文献只证明了"数学/代码任务上小动作集有效"(rStar 消融),**没有任何一手证据显示这些词表在游戏策划/前端/叙事设计场景被真人实时标注过**(design_cognition 域最大空白已复核坐实)。"联想 vs 推导""拆分 vs 延伸"的边界只有自己标100条才知道会不会塌。

2. **"明文规则库 vs 微调"在我们这种"裁决规则"场景的规模边界**——GEPA(校准后:平均+6%/最高+20%/省35x rollout)是 prompt/程序优化场景,不是"知识规则库"场景;文献里**没有一篇同基座、同任务、规则注入 vs LoRA 的头对头对照**。我们的边际收益边界只能自己跑。

3. **反向固化器(裁决→明文规则候选)到底产出什么质量**——ExpeL 已证"无质量门槛的无脑累加会污染规则库(加反思反降10个点)"、Constitutional AI 2026 已证"纯规则罗列会撞泛化天花板、必须留'为什么'"。这两条是**警示不是配方**;规则该长什么样、门槛设在哪,文献只给了"要设门槛/要留理由"两个约束,具体形态得实战迭代。

4. **动词表会不会像所有前辈一样"用着用着坍缩回更小的子集"**——Bloom(19→6)、TRIZ(40→4)、CiTO(41→<10)三个独立领域都发生了这个坍缩。这几乎是规律,意味着**别指望首版词表定终身**;应该先上草案A的6个 + schema地基,埋点统计各动词真实使用频率,3个月后按频率砍到实际核心。这个坍缩点在哪,只有数据知道。

---

**相关文件路径**(本次核查产物,仅一份缓存PDF落在):
`C:\Users\user\.claude\projects\e--WindowsWorkspace\2e57ebf0-63e7-4587-b78a-917dba27be16\tool-results\webfetch-1783157203919-nrv4qu.pdf`(Walton 2410.14335 原PDF,不可文本提取,已改用二手交叉印证)。无其他新建文件。


---

# 附录:七领域 51 体系逐字词表(原始调查数据)


## 附录·设计依据(Design Rationale)学派

六个体系共享一个三段式骨架——"问题/议题节点→候选方案节点→支持或反对的论据节点",分歧只在于要不要加"目标(goal)"层、"标准(criterion)"层、"版本状态"层。真正"活着"（有一手证据证明近年仍在生产环境使用）的只有一家：DRed，靠"当场可用、不摘要、无引导者门槛"在罗尔斯-罗伊斯撑了20多年（2002至今，2013年后仍在多起飞机发动机重大故障调查中被使用）。IBIS/gIBIS/QOC/DRL/SEURAT 均已进入学术引用层（仍被2025年论文当作"设计合理性"的经典定义来源），但作为可运行工具或活跃研究方向均已停止或边缘化，DRed自己的论文（2003）就直接把这一片领域称为"backwater"。LLM时代的自动抽取论文没有复活这套类型词表，而是用更扁平的表示（论据集合、ADR四段式）绕开了它——这对我们的启示是：类型化节点/边的分类学本身可能不是记录成本的解药，DRed 的存活证据指向"节点数量精简+免摘要+免专职引导者"才是关键，而不是类型学有多完备（DRL 的类型学最丰富但工具最先死）。给我们的决策动词表设计的直接教训：动词集合宁可小、宁可让每次记录不需要现场摘要成短语，也不要在"完备性"上加码。


### IBIS / gIBIS (Issue-Based Information System) 〔methodology〕

- 共3类节点+9类关系(据Wikipedia及多个二手源一致复述,一手论文因PDF无法逐字提取全部9种但可交叉确认其中至少8种)
- 【节点,3个】Issue — 需要被解答的问题/议题
- Position — 对某 Issue 的一个可能立场/答案
- Argument — 支持或反对某 Position 的论据(细分为 Pro/Con)
- 【关系,9个,来自 Wikipedia 复述 gIBIS 原文】responds-to — Position 回应某 Issue
- questions — 一个 Issue 对某信念/立场提出质疑
- supports — Argument 支持某 Position
- objects-to — Argument 反对某 Position
- specializes — 某概念是另一概念的更具体形式
- generalizes — 某概念是另一概念的更一般形式
- refers-to — 泛化的参照关系
- replaces — 某节点替换另一同类型节点
- (第9种未能在一手源中逐字确认,二手源本身也有'8个还是9个'的分歧)
- 【现代复活版:IBIS Vocabulary (vocab.methodandstructure.com/ibis), Dorian Taylor 维护, 版本0.7, 最后更新2026-06-09】7个类(Entity/State/Issue/Position/Argument/Invariant/Network)+28个属性(含 generalizes/specializes/replaces/replaced-by/implies/implied-by/suggests/suggested-by/questions/questioned-by/response/responds-to/supports/supported-by/opposes/opposed-by 等成对正反关系),是对gIBIS语义的RDF/OWL形式化扩展

**存活状态**: gIBIS 原型 1988年由 Conklin & Begeman 在 MCC 做出,1991年在 NCR 做过工业重构实验(Conklin & Burgess-Yakemovic);作为独立工具已死,唯一存活的商业衍生 Questmap 据 DRed 论文(2003)称'development of that seems to have ceased';开源实现 Compendium(1993年后续)/CompendiumNG 官方状态为'can still be downloaded but no longer actively maintained'(Wikipedia),最后发布 2.1.3 于2014年;IBIS 作为概念仍在2025年学术论文(ITcon Wyke & Lindhart)中被当作 DR 定义源头引用,但无存活的一手工业工具;2026年有一个人维护的形式化本体重写(IBIS Vocabulary)仍在更新,是目前唯一能确认'2026年仍活跃'的直接衍生物,但它是语义网词汇规范而非可用工具

**适配判断**: 三节点结构(issue/position/argument)与我们要的'问题拆分/反证/联想/推导'语义有天然对应(questions≈质疑/反证雏形,generalizes/specializes≈联想的一种),但9种关系里有5种是纯粹的元数据管理关系(replaces/refers-to/generalizes/specializes)而非'推理动作',真正对应认知动作的只有 questions/supports/objects-to/responds-to 四种,证据显示这个精简度仍然不够——DRed 靠在这个基础上砍掉更多才活下来,值得警惕'类型越全越好'的直觉。

**来源**: https://en.wikipedia.org/wiki/Issue-based_information_system · https://vocab.methodandstructure.com/ibis · https://en.wikipedia.org/wiki/Compendium_(software) · http://csis.pace.edu/~marchese/CS835/Readings/p303-conklin_gibis.pdf (Conklin & Begeman 1988 原文, ACM ToIS Vol.4) · designsociety.org/download-publication/24055 (Bracewell & Wallace 2003, 引用 gIBIS/Questmap 死亡证据)


### QOC (Questions, Options, Criteria) 〔methodology〕

- 共4类核心元素(3类节点+1类可选论证元素)+2类边关系
- 【节点,3个核心+1可选】Question — 界定设计空间里的关键议题('key issues for structuring the space of alternatives')
- Option — 对 Question 的一个候选答案/方案
- Criterion — 用于评估、比较 Options 的判断依据('the bases for choosing among the options')
- Argument(可选补充元素) — 用来论证/佐证某个 Option 对某个 Criterion 的评估结果
- 【边,2个,画在 Option—Criterion 之间】正向评估(support) — 实线表示该 Option 满足该 Criterion
- 负向评估(deny/challenge) — 虚线表示该 Option 不满足/损害该 Criterion

**存活状态**: 1991年由 MacLean, Young, Bellotti, Moran 在 Rank Xerox EuroPARC 发表(Human-Computer Interaction Vol.6);未形成独立存活工具,是'表示法(notation)'而非软件产品,至今仍偶被HCI教学引用为'易学的设计合理性记法'(2020年代博客/教材仍复述其概念),但没有查到任何2010年后仍在维护的专用QOC软件;近期(2026年出现)Springer论文《Relating Design Rationale Representations: Concepts and Tool Support》把QOC与其他体系放在一起讨论'工具支持'现状,但该文付费墙未能取得全文,无法确认其结论倾向;DRed论文(2003)将QOC列为IBIS'众多衍生物之一',与gIBIS/PHI/ÉGIDE/PROSUS/Compendium并列同归为'工业上很少被成功应用'的一类

**适配判断**: QOC的Criterion节点是IBIS/DRL都没有单独强调的一层,直接对应我们'评审意见挂在版本旁'的需求(criterion=评审标准维度);但QOC本身从未产出过存活工具,只是一种手工记法,对'系统探索(蒙特卡洛)'完全没有工程化先例可循。

**来源**: https://acawiki.org/Questions,_Options,_and_Criteria:_Elements_of_design_space_analysis · https://www.tandfonline.com/doi/abs/10.1080/07370024.1991.9667168 (MacLean et al. 1991 原文出处, HCI Vol.6) · designsociety.org/download-publication/24055 (DRed论文把QOC列为IBIS衍生物之一,证实工业采用率低) · https://notesforgrowth.github.io/Capturing-Design-Rationale/ (2020年代教学性复述,称仍'well worth learning' 但未举出在产工具)


### DRL (Decision Representation Language) 〔paper〕

- 原始论文(Lee & Lai 1991, 'What's in Design Rationale?', HCI Vol.6)全文PDF多次尝试均未能提取出逐字关系表(MIT dspace bitstream 405错误,ResearchGate/CORE返回二进制乱码);以下节点清单来自 Jintae Lee 本人 1997年 IEEE Expert 综述论文的逐字复述(该文是DRL作者自己写的二手权威转述,可信度高于第三方转述)
- 【节点,DRL在gIBIS三层基础上多加一层,故至少4类】Decision Problem — 对应gIBIS的Issue层(在DRL中如此称呼)
- Alternative — 对应gIBIS的Position层
- Claim — 对应gIBIS的Argument层
- Criteria — DRL独有在此基础上新增的一层,用于结构化评估维度(gIBIS/DRL对比中,原文明确写:'DRL provides similar constructs for the sublayers, calling them decision problem, alternative, and claim, respectively, and adds constructs for the criteria layer (criteria)')
- 【关系,总数未能逐字确认——检索到的二手复述反复提到 DRL 是'IBIS/QOC/DRL中关系类型表最全的一支'但没有任何来源给出可核实的完整清单;唯一能确认的具体关系词来自 Lee 1997年针对五个子层各自给出的关系集合(非DRL独有,是Lee自己提出的'通用决策层'分析框架里映射到DRL的部分)】argument层关系: supports / refutes / qualifies(该论据支持/反驳/限定某主张)
- alternative层关系: component-of / incompatible / specializes(某方案是另一方案的组成部分/互斥/特化)
- evaluation层关系: nominal / ordinal / real values / maximum expected utility(不同的评估量纲)
- criteria层关系: mutually exclusive / tradeoffs / specializes(准则间互斥/存在权衡/特化)
- issue层关系: generates / depends-on / replaces(某issue衍生出/依赖于/替换另一issue)

**存活状态**: 论文发表于1991年(Human-Computer Interaction Vol.6),作者 Jintae Lee 之后转向 MIT Process Handbook 项目(流程可复用知识库),未见其本人或他人长期维护一个叫'DRL'的独立软件产品;二手文献反复确认 DRL 影响了后续 SEURAT 的设计但'DRL did not provide a sufficiently explicit representation of some types of argumentation (such as indicating if an argument was for or against an alternative), which is why RATSpeak was developed as an extension' —— 即 DRL 本身被自己的继承者宣布'不够用'、需要扩展替代,是被自己的继承者宣布不够用而非被外部竞品淘汰;至今(2026年)仍作为学术引用源(ITcon 2025论文仍引用定义'设计合理性'),但无可运行工具存活证据

**适配判断**: DRL的Criteria独立层和'generates/depends-on/replaces'议题关系,是六个体系里唯一显式区分'议题衍生(generates)'与'议题依赖(depends-on)'的,这两个动词分别对应我们要的'问题延伸'和某种因果/前提关系,值得注意——但由于未能拿到一手全文核实完整关系表(共几个未定),这条证据标记为存疑,不应直接照搬计数,只能作为'该体系被公认关系表最丰富'的方向性参考。

**来源**: https://users.cs.northwestern.edu/~paritosh/papers/sketch-to-models/LeeDesignRationaleSystems.pdf (Lee 1997, IEEE Expert, 作者本人对DRL的二手权威转述) · https://dspace.mit.edu/handle/1721.1/41499 (Lee & Lai 1991原文元数据页,全文PDF多次尝试提取失败) · https://www.itcon.org/papers/2025_26-ITcon-Wyke.pdf (2025年仍引用 Lee and Lai 1991 作为DR定义源头之一)


### SEURAT / RATSpeak (Software Engineering Using RATionale) 〔tool〕

- 多次尝试获取 Burge 2005年 WPI 博士论文全文(BurgeDissertation.pdf / Burge-Diss-Defense.pdf)均因PDF为扫描图像/加密流失败,以下节点清单来自二手转述,未能逐字核实完整表
- 【节点,二手转述确认至少4类】Decision Problem — 需要决策的问题
- Alternative — 候选方案
- Argument / Claim — 支持或反对某方案的论据(含 Requirement 作为一种特殊论据来源)
- Requirement(functional / non-functional) — RATSpeak相对DRL的独有扩展,把需求直接纳入论证结构里作为一种可被引用的论据依据
- 【关系,二手转述确认的核心两类,原文可能更多但未能核实】supports — 论据支持某方案
- denies — 论据反对某方案
- 【补充结构】Argument Ontology — 一个论据类型的层级分类体系(argument types 的层级),用于系统化列举'哪些理由可以用来论证'

**存活状态**: Janet Burge 与 David C. Brown (WPI) 2000年代中期开发,主要文献集中在2005-2008年(2008年 Journal of Systems and Software 发表'Software Engineering Using RATionale');集成于 Eclipse IDE;未查到2010年后的持续开发或工业部署证据,二手源普遍只引用到2008-2015年区间;是六个体系里工业化程度最低、几乎完全停留在学术原型阶段的一支

**适配判断**: SEURAT把Requirement直接接入论证结构(而非只有Alternative/Argument),这一点对我们'设计规范挂在版本连线上'的需求有直接映射价值——但由于该体系本身几乎没有存活证据、也没能核实到完整逐字关系表,只能作为'把外部规范文档接入论证图'这一设计思路的佐证,不能作为词表来源直接采用。

**来源**: http://web.cs.wpi.edu/~dcb/Papers/SEURAT/Burge-Diss-Defense.pdf (Burge博士答辩PPT/文档,内容为图像未能提取文字) · https://www.sciencedirect.com/science/article/abs/pii/S0164121207001203 (Burge & Brown 2008, Journal of Systems and Software,付费墙未获全文) · https://www.researchgate.net/publication/221556079_SEURAT_integrated_rationale_management (摘要级二手源) · https://www.itcon.org/papers/2025_26-ITcon-Wyke.pdf (2025年论文仍引用 Burge & Brown 2008 作为DR管理方式定义源)


### DRed (Design Rationale editor, Rolls-Royce) 〔tool〕

- 据 Bracewell & Wallace 2003年ICED论文原文Figure 4(逐字读取自一手PDF截图),完整节点集合如下,共10个节点类型(含子状态)
- 【节点,10个,每个节点可带1-5种互斥状态】Issue — 状态可为: Open issue / Resolved issue / Insoluble issue / Rejected issue
- Answer — 状态可为: Open answer / Accepted answer / Rejected answer
- Pro argument — 状态可为: still holds / is dominant / now fails
- Con argument — 状态可为: still holds / is dominant / now fails
- Text statement — 状态可为: believed true / now known to be false
- User-defined field — 自由字段(键值对,如'a_user_defined_field: value of field')
- File reference(网页链接形式) — 例如指向外部网页(如 http://www.touchstone.com/tr/wp/IBIS.html)
- File reference(文档段落引用形式) — 例如指向本地文档特定章节(如 iced99.doc#fig3)
- Bitmap graphic(位图截图形式的文件引用) — 可直接嵌入CAD/电子表格等外部软件的截图
- (后续版本新增)Tunnelling link — 用于跨文件/跨图连接,不是节点而是一种特殊边,允许DR图跨多个文件分布导航
- 【状态迁移的边语义】箭头方向表示'状态审阅依赖':当某节点状态被设计者改变时,箭头从该节点指向所有'应因此被重新审阅状态'的其他节点(而非表示逻辑蕴含关系本身)——这是与IBIS/DRL等纯逻辑关系体系的本质区别:DRed的边语义是'审阅传播',不是论证结构

**存活状态**: 持续开发中,自2002年至今(2003年论文发表时已有v0.1-v0.4.2共8个连续发布版本,8个月内);由 Rolls-Royce plc 与 EPSRC 共同出资和控制分发;据 Hall et al. (2017, ICED17) 与其他二手源: 部署给 Rolls-Royce 英国/德国/美国/加拿大 1000+名工程师使用,2005年11月起成为Rolls-Royce技术PC标准PLM工具集的一部分,2004年获Rolls-Royce研发创新奖,2008-2013年在两起重大航空事故调查及Trent XWB发动机研发中发挥关键作用;是本次调研六个体系里唯一有'2013年后仍在真实工业场景使用'实锤证据的一支;衍生开源工具 DesignVUE(帝国理工,基于DRed理念,四节点issue/answer/pro/con)也在同一生态圈内

**适配判断**: DRed的10节点+状态迁移边模型证明:活下来的体系反而砍掉了'纯逻辑关系类型学',换成了更朴素的'节点状态+审阅传播提醒'。这对我们'版本间连线挂设计规范/版本旁挂审阅意见'的落地设计极具参考价值——它证明了真正被工业验证过的模式是状态化节点+提醒式边,而不是IBIS/DRL式的语义关系分类。

**来源**: designsociety.org/download-publication/24055 (Bracewell & Wallace 2003, ICED,一手PDF已逐字读取,含Figure 4完整节点截图) · https://impact.ref.ac.uk/casestudies2/refservice.svc/getcasestudypdf/14057 (剑桥大学REF影响力案例研究,二手确认1000+工程师规模及Trent XWB应用,原文本身为图像PDF未能逐字核实但摘要经WebFetch二次确认) · https://www.designsociety.org/download-publication/39788 (Hall et al. 2017 ICED17,四位工具作者访谈之一即DRed开发者视角,一手PDF已逐字读取)


### LLM 时代设计合理性自动抽取/生成(2024-2026 新兴方向,作为对照组非独立体系) 〔paper〕

- 未采用IBIS/QOC/DRL类型化节点,而是使用扁平/半结构化表示,列2套代表性方案
- 【ACM TOSEM 2025, 'Using LLMs in Generating Design Rationale for Software Architecture Decisions'】Architecture Problem (P) — 待解决的架构问题
- Architecture Decision (D) — 已选定的解决方案
- Design Rationale (DR) = {A1, A2, ..., An} — 一组论据(Argument)的集合,每条论据对应'优点/缺点/权衡'某一个视角,不再区分节点类型只是'论据条目'的扁平列表
- 【EASE 2026, 'Context Matters: Evaluating Context Strategies for Automated ADR Generation'】ADR标准四段式: Status(如Accepted) / Context(背景与动因) / Decision(选定方案) / Consequences(后续影响与权衡)
- 【DRMiner / Argus 等挖掘类工具(引用自二手综述)】从issue/commit/chat中抽取: decision / issue / alternative / pro-argument / con-argument 五类扁平标签,用于分类而非构建关系图

**存活状态**: 活跃增长中,2024-2026年多篇论文(ACM TOSEM 2025、EASE 2026、DRMiner、Argus、CoMRAT等);但两篇一手全文核实结果均显示论文明确声明'未讨论/未采用IBIS、QOC、DRL'(ACM TOSEM 2025原文:'No formal vocabulary framework (such as IBIS, QOC, or DRL) is mentioned';EASE 2026原文:'does not explicitly discuss IBIS, QOC, or DRL approaches...focus on contemporary LLM and RAG literature');即这是一条独立生长的新脉络,而非旧体系的'复活'

**适配判断**: 这是给我们最重要的警示证据:学术界即使在2025-2026年做'AI自动记录设计合理性'时,也主动放弃了经典类型化节点/边体系,改用更扁平的论据列表或ADR四段式——说明'类型化节点图'这个表示形式本身在工程实践里的边际收益可能不足以覆盖其记录/维护成本,我们要的动词表若要给'系统探索(蒙特卡洛)'和'明文训练'两个下游用,恐怕需要类型极简且与LLM抽取的自然产出(扁平论据/决策记录)对齐,而不是复刻IBIS/DRL式的完备分类学。

**来源**: https://arxiv.org/abs/2504.20781 (ACM TOSEM 2025, 一手html已核实) · https://arxiv.org/html/2604.03826v2 (EASE 2026, 一手html已核实) · https://arxiv.org/abs/2405.19623 (DRMiner, A Novel Approach for Automated Design Information Mining from Issue Logs)


**领域死因教训**: 六个体系死法高度收敛,核心是同一个"记录成本瓶颈"(rationale capture problem),具体机制:

1. **记录发生在决策之后而非决策之中**——Shipman & McCall (1996) 指出设计合理性记录"总是决策之后而非决策之中发生";Jintae Lee (1997, IEEE Expert) 直接引用其观点,说明论证(argumentation)视角要求把逻辑结构在推理发生时显式化,但这打断设计流程本身。DRed 论文(Bracewell & Wallace 2003)引用同样发现:一旦进入"实际构建(详细设计)阶段,设计者就停止记录"。

2. **谁受益与谁付出不对等(cost-benefit mismatch)**——Lee (1997) 直接引用 Jonathan Grudin"很多群件系统失败正是因为这个错位":记录者当下不获益,受益者是未来的、未知的人。Hall et al. (2017, ICED17 专家访谈论文)四位工具作者(DRed/DesignVUE/Compendium/Glyma的开发者)访谈结论高度一致:"key breakthrough was understanding how to tie this to adding value right there and then"——凡是没做到"当下即获益"的工具都难存活。这是本次调研两篇独立文献(1997年理论 + 2017年从业者口述)交叉验证的同一条死因,可信度高。

3. **节点摘要负担(summarization tax)**——DRed 论文明确记录了 Questmap/DRAMA 失败的技术原因:图标+单行文本标签强迫用户把每条 issue/answer/argument 压缩成 5-6 个词,"被认为是设计者不可承受的负担(intolerable burden)";DRed 靠允许节点直接容纳多行文本、消除图标解决了这一点,是已知最详细的"为什么同一套 IBIS 概念一个死一个活"的技术级归因。

4. **有正式方法要求专家引导者(facilitator)门槛高**——IBIS/gIBIS 的一支应用方式(dialogue mapping)依赖训练有素的引导者,"这是门手艺,只能靠硬练"(Hall et al. 引访谈原话),推广受限于人才而非工具本身。

5. **元研究佐证死亡范围**——DRed 论文本身引用一手证据:"过去四届 ASME DETC 和 ICED01 论文集里,Kunz & Rittel 的 IBIS 工作只被引用 3 次","这个领域在设计研究界已成为一潭死水(backwater)","除了 Questmap,这类商业软件工具没一个还活着,而且 Questmap 的开发似乎也已停止"(2003年原话)。这是体系内部研究者自己承认领域整体式微的罕见直接证据。

6. **LLM 时代并未复活旧词表,而是绕过了它**——2025-2026 年的 LLM 自动生成/抽取设计合理性论文(ACM TOSEM 2025、EASE 2026)完全没有采用 IBIS/QOC/DRL 的类型化节点体系,而是用扁平的"论据集合(a set of arguments)"或 ADR 的 Context/Decision/Consequences 四段式;搜索"design rationale" + IBIS + LLM 得到的是"issue log 挖掘"(DRMiner、Argus)等新造管线,不是对经典体系的封装或复活。换句话说:经典类型学在学术圈仍被引用为"历史定义来源"(2025年AEC领域NLP论文仍引 MacLean/Conklin/Lee 作为DR定义源头),但工程实践和LLM抽取管线都没有继承其类型表,是被绕过而非被复活。


**未覆盖/存疑**: 1. DRL(Lee & Lai 1991)原始论文的完整逐字关系表未能核实——多次尝试(MIT dspace bitstream 返回405、ResearchGate/CORE返回无法解析的二进制流)均未获得原文全文,只能依赖作者本人1997年综述里的转述(argument层supports/refutes/qualifies;alternative层component-of/incompatible/specializes;evaluation层nominal/ordinal/real values/maximum expected utility;criteria层mutually exclusive/tradeoffs/specializes;issue层generates/depends-on/replaces),这是二手作者自转述而非原始逐字表,且不确定是否穷尽了DRL论文里全部关系类型——多个二手来源反复宣称"DRL关系类型表最全"但没有一个来源给出可验证的完整清单及总数。建议:若需要精确到"DRL共有N个关系类型"这一硬指标,需要通过图书馆/付费数据库(Taylor & Francis, HCI Journal Vol.6)获取原文PDF重新核实,当前证据不足以支撑这个数字。

2. SEURAT/RATSpeak的完整节点与关系表未能核实——Burge博士论文(WPI)与2008年期刊论文均遇到PDF提取失败(图像扫描/付费墙),只能依赖零散二手摘要转述,置信度低于本报告其他条目。若这条体系对我们重要,需要单独找WPI图书馆开放获取版本或联系作者索取全文。

3. gIBIS原始1988年论文的第9种关系未能确认——8种关系(responds-to/questions/supports/objects-to/specializes/generalizes/refers-to/replaces)在多个二手源中反复出现且相互印证,但"共9种"这个总数被多个来源提及却没有任何一个来源列出第9种具体是什么,可能是各二手源转述时把某个正反成对关系计为独立一种、也可能是笔误传播导致的以讹传讹。原始ACM论文(dl.acm.org/doi/10.1145/58566.59297 或 62266.62278)因403权限一直未能直接读取。

4. QOC的现存工具生态未能确认——付费墙(Springer《Relating Design Rationale Representations》2026年新论文)可能包含QOC现状的重要一手信息,未能获取全文,只能标注"存疑待验证"。

5. DRed 2.0(designVUE之后的商业续作)及DRed当前(2026年)最新状态未直接核实——搜索到"DRED 2.0"论文标题但未深入取得全文,只核实到2003-2013年区间的证据链,不能确认DRed在2013-2026年间是否有重大版本迭代或是否仍在用(虽然2017年Hall et al. 论文访谈证实其开发者仍活跃于该领域,但这是间接证据非直接确认"2026年仍在Rolls-Royce生产环境运行")。

6. 未找到IBIS/QOC/DRL直接被LLM系统"封装复活"的论文——搜索到的都是"绕过经典体系另起炉灶"的证据,没有找到反例(即没有一篇2024-2026年论文说"我们把LLM抽取结果映射回IBIS/DRL的标准节点类型"),这本身是一个有意义的空白,值得记录但不能过度解读为"绝对没有"——可能存在但未被本次搜索覆盖到(搜索深度受限于时间与工具调用次数)。


## 附录·设计认知/设计科学本体

设计认知/设计科学本体这个领域给出的最大启示是:所有主流体系都是**描述性**(descriptive)的——为事后分析设计师的出声思维记录(protocol)而生,不是为"边工作边标注"设计的规定性工具;没有一个体系是为人机协作实时打标签而造的,这恰是我们要做的新东西。第二个启示是**领域高度碎片化**:Hay et al. (2017) 系统综述发现 30 年间 47 项 protocol study 各用各的编码词表,互不兼容,逼得他们另造一套"通用认知过程分类"去调和,而这套调和方案至今没见被复用的证据——即"想统一术语"本身也是一个反复失败的子领域。第三,词表规模普遍很小(FBS 8 个过程、C-K 4 个算子),这佐证"词表要小而稳"是可行方向,但这些体系的小词表换来的是粗粒度(FBS 的 "reformulation I/II/III" 本质是同一操作在三个状态空间的复制,C-K 的四算子对应用者来说需要额外训练才能对应到具体设计动作)。第四,linkography 的 40 年生存证据里有个关键警示:它至今**没有标准化的 move 类型表**,只标注"链接存在与否"这一个二元关系,2025年的 Fuzzy Linkography 论文明确说这是它"人工标注成本高、难以规模化"的根源——对我们要建的"边/节点类型词表"是个反面教材:关系类型(边)比节点内容分类更容易做小而稳,但如果只做"有没有关系"这种最简形式,后续系统探索(蒙特卡洛)会因信息量不足而无法用。第五,三个体系(FBS/C-K/Suwa-Tversky)都在 2024-2025 年的生成式AI设计论文里被引用为"背景理论"而不是"实际操作词表"——作者们哪怕研究AI辅助设计,依然选择自造一套四阶段临时标签(problem definition/idea generation/idea selection/idea evolution)而不直接复用这些学术词表,说明现有词表在实际系统构建者眼中"不够用/太学术"。


### FBS (Function-Behaviour-Structure) Ontology — Gero 〔methodology〕

- 共8个过程(processes),另有5个设计问题类别(design issues:R/F/Be/Bs/S/D)作为节点类型
- 节点类型 — Requirement (R):外部对设计对象的需求描述
- 节点类型 — Function (F):设计对象被期望达成的目的
- 节点类型 — Expected Behaviour (Be):从功能推导出的、结构应产生的预期行为
- 节点类型 — Structure (S):设计对象的组成部件及其关系
- 节点类型 — Behaviour derived from Structure (Bs):从已生成结构实际推导出的行为
- 节点类型 — Description/Documentation (D):关于结构的设计描述文档
- 过程1 formulation — 将需求转化为功能状态空间、再将功能转化为(预期)行为状态空间,记为 R→F→Be
- 过程2 synthesis — 将预期行为转化为结构,记为 Be→S
- 过程3 analysis — 从已生成的结构推导出实际行为,记为 S→Bs
- 过程4 evaluation — 比较预期行为(Be)与推导行为(Bs)以判断结构是否满足预期,记为 Be↔Bs
- 过程5 documentation — 基于结构生成设计描述,记为 S→D
- 过程6 reformulation I — 基于评估结果重新诠释并修改结构变量空间,记为 S→S'
- 过程7 reformulation II — 基于评估结果重新诠释并修改行为变量空间(进而影响结构生成方式),记为 S→Be'
- 过程8 reformulation III — 基于评估结果重新诠释并修改功能空间,记为 S→F' (经由 Be)

**存活状态**: 存活,持续被用。原始框架 Gero 1990 年提出;Gero & Kannengiesser 2004 年发展出 Situated FBS(引入 situated cognition,扩展出三世界隐喻+12种表征+20个过程)。截至可查证据,FBS 两篇奠基论文合计引用接近1000次,基于该框架的系列研究合计被引用超900次。它被用作 protocol analysis(设计师出声思维记录)的标准编码方案,论文明确写道该编码使'不同研究者在不同条件下研究不同设计师所得结果可比较'。2011年出现配套自动化分析工具 LINKOgrapher(将FBS编码与linkography结合),说明工具链仍在演进。已知一次编码信度检验:同一编码者间隔10天两次编码同一份 protocol,经 Delphi 法自仲裁后一致率超86%,52分钟设计会话被切分编码出475个片段——体现该词表是逐句/逐动作精细切分的重编码工作,非轻量标注。2024-2025年生成式AI设计论文(如 Chen et al. 2024 概念设计AI研究)仍将FBS列为背景理论引用,但未直接采用其八过程作为实际标注词表,而是另造四阶段临时分类,说明学术存活但未被新一代AI辅助设计工具直接复用为操作词表。

**适配判断**: 证据支持:词表足够小(8过程+6节点类型)且经40年验证足够稳定,天然是'节点类型+边类型'的候选骨架——R/F/Be/Bs/S/D 可对应我们材料的'版本阶段'类型,8个过程可对应版本间的'转换动词'。但证据也显示该词表是**描述性**的:设计出'formulation/synthesis/analysis/evaluation/documentation/reformulation'完全是为了让研究者能给已发生的设计会话录音逐句编码分类,从未见证据显示它被设计者本人在工作时主动标注自己的设计动作(即无'边工作边标注'的先例)。reformulation I/II/III 三分是因为要精确对应状态空间(S/Be/F)三个回退目标,粒度对人类使用者偏学术化,需要培训才能一致编码(信度检验86%已算高)。若采用,需要把三种 reformulation 合并简化为对我们更直白的'退回结构/退回预期效果/退回目标'三个说法,并且必须补一层'规定性'包装(即从事后编码转为决策时刻主动选择)才可用。

**来源**: https://en.wikipedia.org/wiki/Function-Behaviour-Structure_ontology · https://www.researchgate.net/publication/220306734_A_function-behavior-structure_ontology_of_processes · https://www.researchgate.net/publication/288111489_Using_the_FBS_ontology_to_capture_semantic_design_information_in_design_protocol_studies · https://www.designsociety.org/publication/30480/LINKOGRAPHER%3A+AN+ANALYSIS+TOOL+TO+STUDY+DESIGN+PROTOCOLS+BASED+ON+FBS+CODING+SCHEME · https://johngero.com/publications/2006/06GeroKannengiesserDCC06.pdf


### C-K Theory (Concept-Knowledge Theory) — Hatchuel & Weil 〔methodology〕

- 共4个算子(operators),定义在两个空间(C=概念空间, K=知识空间)之间
- 空间C (Concept space):无真值可判定的命题集合(不可判断真假的'待验证'概念陈述)
- 空间K (Knowledge space):带有可判定真值的命题集合(已确立、可判断真假的知识陈述)
- 算子 C→K (conjunction/连接) — 将概念空间中的一个提议与知识空间中已有命题相连接,把概念'实体化'为可验证的知识,即概念被知识确认或产出新知识
- 算子 K→C (disjunction/分离) — 把知识空间中的命题转化/引入到概念空间,对初始概念做属性增删的划分(partition),对应通常所说的'生成备选方案',但这些概念本身还不是备选方案,只是备选方案的潜在种子
- 算子 C→C (概念内部扩展) — 在概念空间内部通过分裂、混合、重塑一个概念,为其添加新属性并派生出新概念,不经过知识空间
- 算子 K→K (知识内部扩展) — 在知识空间内部运作,不改变概念空间,通过已确立知识推导/扩展出新的已确立知识

**存活状态**: 存活,持续被工业界与学术界使用。1996年 Hatchuel 提出初稿,2003年与 Weil 正式发表 C-K theory,2009年发布'C-K design theory: an advanced formulation'(引入与集合论力迫法、直觉主义数学的类比,理论仍在演进)。官网 ck-theory.org 显示 Mines ParisTech 于2009年设立'Design Theory and Methods for Innovation'讲席(Chair),由 Dassault Systèmes、RATP、Renault、Thales、Vallourec、SNCF、STMicroelectronics 等工业企业联合资助支持,截至可查证据讲席仍在运作,证明该理论有持续工业界资金背书而非纯学术遗留。文献称其'自1998年起已在法国、瑞典、德国等多个工业场景中被应用'。2024-2025年生成式AI设计研究(如 Hatchuel et al. 关于生成式设计算法 generativity 的形式化工作)仍在扩展 C-K 框架去分析 AI 生成算法的'生成性',说明该理论仍在被用作分析新技术(而非仅历史遗留)。

**适配判断**: 四算子是目前查到的所有体系里粒度最小、最稳定的候选(只有4项),且天然契合'边类型':C→K/K→C 对应我们说的'验证/生成候选'方向,C→C 对应'联想/概念内部演化'(与用户举例的'联想'高度对应),K→K 对应'推导'(与用户举例的'推导'高度对应)。但证据显示这套算子是**规定性但抽象度极高**的理论语言,面向的是'设计理论学者分析创新设计案例'场景,不是操作词表——业界应用案例(讲席资助企业)存在,但查不到'工程师在工作时主动敲下 C→K 标签'的一手证据,均是研究者事后用这套语言复盘案例。若采用,C-K 的'空间'区分(可证伪陈述vs不可证伪陈述)本身是一个有价值的隐藏轴,可以单独提炼为我们判断'这是猜想还是定论'的分类依据,但四算子本身需要重新命名为更贴近用户举例的动词(如反证/推导/联想/生成)才可落地,不能直接照搬 C→K 这类符号。

**来源**: https://en.wikipedia.org/wiki/C-K_theory · https://www.ck-theory.org/c-k-theory/?lang=en · https://www.designsociety.org/download-publication/19760/c-k_theory_in_practice_lessons_from_industrial_applica · https://johngero.com/conferences/sdc08/papers/Hatchuel.pdf


### Linkography — Goldschmidt 〔methodology〕

- 节点类型 — design move(设计移动):设计过程中对设计情境做出的一次具体改动/决定,是最小分析单位,通常按 protocol 转录的每一行/每一个动作切一个 move
- 边类型 — link(链接):当后一个 design move 可被认为建立在前一个 design move 之上时,在两者间画一条连接线;**只有'存在/不存在'一种二元状态,没有区分链接的类型或性质**
- 衍生模式(非独立词表项,是链接网络的宏观形态识别) — chunk(簇):以一个明确'引发'move开始、大部分链接都指回该引发move的一组相互关联的moves
- 衍生模式 — web(网):moves间联系异常紧密的簇,几乎每个move都与其他几乎所有move相连
- 衍生模式 — sawtooth(锯齿):每个move只与其紧邻的前一个和后一个move相连的序列
- 衍生模式 — orphan move(孤儿move):没有任何链接、被忽略的move,标志被放弃的岔路
- 衍生指标 — critical move(关键move):链接数量异常密集的move,被认为是设计过程中的重要节点
- 共约2个核心词条(move、link)+4个衍生形态识别名词,不构成标准化的'move类型表'

**存活状态**: 存活,持续被应用,但存在明确的规模化死结。Goldschmidt 发展linkography方法数十年,2014年出版专著《Linkography: Unfolding the Design Process》(MIT Press)。应用领域覆盖建筑、产品设计、动画、声音设计、游戏设计、群体创意方法比较(2018年 ScienceDirect 论文)等。2025年最新证据:一篇发表于 ACM Creativity & Cognition 2025 会议的 'Fuzzy Linkography' 论文明确指出 linkography '因高昂的人工标注成本,很少被用于分析大规模的创意活动痕迹',并引用2024年 Lee 等人将其应用于3D生成式AI研究的案例作为最新实例,同时该2025论文本身提出用'模糊链接(强度值)'取代传统二元链接、试图借助自动化/AI降低标注成本以实现规模化——即linkography至今仍活跃但其'纯人工标注'形态被认为已过时,正在被下一代自动化变体取代。

**适配判断**: 对我们最直接的负面警示:linkography 40年里从未发展出标准化的'边类型词表'(只有有/无链接的二元关系),这被2025年的后续研究明确点名为其规模化瓶颈根源。这说明如果我们的边词表也退化成'有没有关系'这种最简形式,虽然易于人工标注,但会让下游'系统探索(类蒙特卡洛)'和'规则沉淀'因信息量不足而失去区分力——边必须带类型(用户举例的问题拆分/反证/联想/推导等)才有意义,这是linkography留下的最强反面教训。它的'design move'节点定义(离散的、可枚举的设计改动)本身是好的通用起点,可直接借用做我们节点切分粒度的参照;'critical move'(高链接密度=关键决策点)的思路也可用于识别我们轨迹图上哪些版本是真正的决策枢纽。

**来源**: https://mitpress.mit.edu/9780262027199/linkography/ · https://arxiv.org/html/2502.04599 · https://dl.acm.org/doi/full/10.1145/3698061.3726915 · https://www.sciencedirect.com/science/article/pii/S0142694X18300395


**领域死因教训**: 这个领域体系"死"的方式不是被弃用,而是长期停留在**学术分析工具**层面、从未跨过"变成实际生产中人类主动使用的操作词表"这道坎——三条独立证据链都指向同一个根因:(1) FBS/C-K/Suwa-Tversky 三大体系在2024-2025年被生成式AI设计论文引用时,清一色只当"背景理论"提及,论文作者们哪怕在做AI辅助设计工具,依然选择自己临时拍一套四阶段标签(problem definition/idea generation/idea selection/idea evolution),而不直接采用这些学术词表——说明学术词表"太抽象/太难落地成界面上的按钮或标签",这是给我们最直接的镜子:我们要造的词表必须比这些学术词表更贴近实际操作动词,不能停留在'concept/knowledge/behaviour'这种需要专门培训才能用对的抽象层。(2) Hay et al. (2017) 系统综述发现30年47项protocol study各自发明各自的编码词表,互不兼容,逼出'造一套统一通用分类'的尝试,而这个统一尝试本身此后也查不到被复用的证据——说明"试图一次性统一全领域术语"这条路本身就是一种反复失败模式,词表统一工作本身会不断被后来者重新发明而不是收敛。(3) linkography 用了40年"只标记有无链接"的最简方案来换取人工标注的可行性,但2025年最新论文明确说这正是它无法规模化的死因——过度简化词表(为了标注方便而砍掉类型信息)会在下游失去价值,这是"词表要小"和"词表要有效"之间的真实张力,不能为了小而砍成只剩二元关系。


**未覆盖/存疑**: 1) 没有查到任何一手证据显示 FBS/C-K/linkography 曾被真实设计师(而非研究者)在工作现场实时主动标注自己的设计动作("边工作边标注"用法),所有证据都指向"研究者事后给录音/记录编码",这是本次调研最大的空白,可能本身就说明"实时人机协作标注"这件事在学术界从未被验证过,需要在最终报告里明确标注为"未见先例"而非"查漏"。2) 没能打开 johngero.com 的PDF原文(证书过期多次失败),FBS八过程定义引用的是二手转述(Wikipedia+ResearchGate聚合),虽经两个独立源交叉印证一致,但仍建议后续如有机会用能访问的镜像(如 ResearchGate 网页版或 Springer 链接 https://link.springer.com/chapter/10.1007/978-1-4020-5131-9_21 )核对原文逐字表述。3) Suwa & Tversky (1997) 编码方案本次只做了辅助性核实(四类别:physical/perceptual/functional/conceptual),未深入其子类目和被复用规模的精确统计,若后续需要可以补查。4) C-K theory 的"讲席仍在运作"这一状态判断来自官网页面缓存内容,未核实官网当前(2026年)是否仍列出这些企业为现役资助方,存在信息滞后可能。5) 未查到该领域是否有专门讨论"决策裁决沉淀为明文规则反哺训练"这类用法的先例文献,这个问题本次未被任何搜索结果直接回应,值得单独立题再查(可能该问法更接近人机协作/主动学习 active learning 文献而非设计认知文献)。


## 附录·论证理论与学术话语本体

论证理论/学术话语本体这个领域给我们的核心启示:凡是要求人在产出瞬间手动选择"精确语义类型动词"的体系(CiTO、Walton全量96图式),真实采纳率都很低,因为标注认知负担大于收益,只有小圈子试点在用;凡是词表极简(3-12个核心关系,可由工具自动派生或半自动推断)且能挂在既有工作流上的体系(PROV-O、RST的核心nucleus-satellite骨架、AIF三节点类型),才有广泛且长期(10-20年)的存活证据。Walton论证图式提供了一个对我们"反证"动词极有价值的机制——critical questions(每个图式自带3-8条可挑战的追问)——这个"生成式质询清单"模式比"简单反对边"信息量大得多,值得借鉴到我们的评审边设计。AIF的三分法(I-node信息/RA-node推理规则应用/CA-node冲突关系/PA-node偏好关系)是把"内容节点"和"关系节点"分离建模的成熟先例,与我们"版本节点+设计规范边+审阅意见挂点"的三层结构高度同构,可直接参考其"S-node承载图式实例"的设计。CiTO是我们"产物间标准化动词"最直接的先例，其失败教训(41个太细但真实只用了个位数的几个高频词如cites/extends/critiques/disputes/usesMethodIn)恰好印证了词表要小的必要性——真实活跃使用的CiTO动词从统计上看不超过10个。总体上,这个领域教会我们:节点/边类型表要小(个位数到十几)、关系语义要能被工具半自动派生而非纯靠人力手动打标、且要给"反证/质疑"类边配上结构化追问清单而非单一标签。


### Toulmin 论证模型(Toulmin Model of Argumentation) 〔methodology〕

- 共6项(3核心+3支撑)
- Claim(主张)— 作者希望说服受众接受的断言,应可辩驳而非既定事实
- Data/Grounds(依据/根据)— 用来支撑主张的证据或事实
- Warrant(担保/推论许可)— 连接依据与主张之间的隐含或明示的推理假设,解释为什么依据能支持主张
- Backing(支持/后援)— 为担保本身提供进一步正当性的具体依据,当担保受质疑时调用
- Qualifier(限定语)— 限制主张适用范围/强度的词语(如"通常""很可能"),表明主张的确信程度
- Rebuttal(反驳/例外)— 承认主张可能不成立的例外情况或反例条件

**存活状态**: 1958年提出,至今仍是英语写作教学(尤其修辞/论证写作课、STEM写作、法学院、辩论教学)最广泛教授的论证结构模型之一;大学写作中心(Purdue OWL、SJSU Writing Center等)持续在用,2020年代仍在被大量教学网站复用讲解,活跃但主要活在教学场景而非计算系统

**适配判断**: 6要素粒度适合作为我们"单个决策节点内部结构"的检查清单(每条决策记录是否有明确claim/data/warrant),但它不是"节点间关系"的词表,更像是节点内部的完备性校验模板,不能直接充当我们要的动词表;可以考虑用它反向校验每个决策节点的记录完整度。

**来源**: https://owl.purdue.edu/owl/general_writing/academic_writing/historical_perspectives_on_argumentation/toulmin_argument.html · https://writingcommons.org/section/genre/argument-argumentation/toulmin-argument/


### Walton 论证图式(Argumentation Schemes)+ Critical Questions 〔methodology〕

- reasoninglab索引页列出29个常用图式条目(未含所有变体细分);Walton, Reed & Macagno (2008)《Argumentation Schemes》一书据检索摘要收录约96个图式(此数字来自单一搜索摘要,未能直接核原书目录核实,已列入gaps)
- Argument from Expert Opinion(诉诸专家意见)— 因信源E是S领域专家且断言A为真,推出A为真;自带6条critical questions逐字对应:Expertise Question(E作为专家来源可信度多高)、Field Question(E是否确系A所在领域的专家)、Opinion Question(E断言了什么、蕴含A)、Trustworthiness Question(E作为信源本身是否可靠、有无偏见)、Consistency Question(A是否与其他专家的断言一致)、Backup Evidence Question(E的断言是否有证据支撑)
- Argument from Analogy(类比论证)— 因案例C1与C2在相关方面相似,且C1为真/应当,推出C2亦然
- Argument from Cause to Effect(因果论证·由因及果)— 因一般规律"若A则(可能)B"成立且A发生,推出B将/可能发生
- Argument from Correlation to Cause(由相关到因果)— 因A与B统计相关,推出A与B之间可能存在因果关系(常与"忽略共因/巧合"的质疑相配)
- Argument from Sign(征兆论证)— 因观察到征兆/迹象通常与某状态相伴,推出该状态存在
- Argument from Consequences(后果论证/结果论证)— 因某行动会导致好/坏后果,推出应/不应采取该行动(常用于实践推理)
- Argument from Precedent(先例论证)— 因先前案例被如此判定/处理,推出当前相似案例应同样处理
- Argument from Popular Practice / Popularity(诉诸大众做法/大众意见)— 因多数人这样做/相信,推出这样做是对的/可信
- Slippery Slope Argument(滑坡论证,含Full/Causal/Precedent三种子型)— 因采取第一步行动会引发一连串难以阻止的后果链,最终导致不可接受的结局,推出不应迈出第一步
- Argument from Ignorance(诉诸无知论证,含Plausible/Deductive两种子型)— 因某命题未被证伪/未被证明为假,推出该命题为真(反之亦然)
- Practical Reasoning Argument(实践推理论证)— 因行动者有目标G,且行动A是达成G的手段,推出行动者应执行A
- Ethotic Argument(人品论证)— 因论者品格可信/不可信,推出其论断可信/不可信(诉诸人品的正反两用)

**存活状态**: 理论持续活跃:Walton (1996)首版后经Walton, Reed & Macagno (2008)扩充为标准参考書,2020年代仍被论证挖掘(argument mining)、法律论证AI、计算论证系统大量引用(如2024年的Critical Questions Generation论文用LLM自动生成critical questions,2024-2025年仍有基于Walton图式的AI critiquing系统论文);属于论证理论中"最广泛计算化落地"的分支,是AIF能实例化的图式来源

**适配判断**: critical questions机制对我们的"反证"动词极具参考价值——不是单一"反对"边,而是每种推导都自带一组结构化追问清单(如"这个类比在哪个维度上可能不成立"),可以把这套思路移植成:每条"推导/生成"边被建立时,系统自动挂载一组该推导类型对应的标准追问,供人工或AI逐条核验、核验结果即成为"反证"节点的来源。图式全表(近百个)本身太大不能照搬,但前10-15个高频常识图式(专家意见/类比/因果/征兆/后果/先例/滑坡)可作为我们"推导"这个大类动词下的子类型参考,不建议直接搬全表当作我们要用的动词表。

**来源**: https://en.wikipedia.org/wiki/Argumentation_scheme · https://www.reasoninglab.com/patterns-of-argument/argumentation-schemes/waltons-argumentation-schemes/ · https://arxiv.org/pdf/2410.14335


### AIF(Argument Interchange Format)+ AIFdb/OVA 工具链 〔standard〕

- 共4类核心节点(2大类:I-node信息节点 + S-node图式应用节点,S-node再分3小类)
- I-node(Information Node,信息节点)— 承载论证中的命题性内容,如主张/前提/数据
- RA-node(Rule Application Node,推理规则应用节点)— 表示某条推理图式被具体实例化,连接前提I-node到结论I-node
- CA-node(Conflict Application Node,冲突应用节点)— 表示两个信息或推理之间存在冲突关系(如反驳、矛盾)的具体实例
- PA-node(Preference Application Node,偏好应用节点)— 表示在两个冲突项之间进行优先级判定的具体实例
- Upper Ontology(上层本体)— 定义节点与边的抽象骨架
- Forms Ontology(形式本体)— 用论证理论概念对上层本体的元素进行类型化

**存活状态**: 理论规范(AIF Core Specification, arg-tech发布)长期稳定被引用,是计算论证学界事实标准之一;工具链方面OVA(在线论证可视化工具)历经OVA→OVA2(2015)→OVA3(2022年12月最后一次重大更新,官网标注为speed/stability/scalability重构),据ARG-tech官网称OVA系列历史上有超过10万用户、80多个国家使用过;AIFdb(数据库基础设施,2012年上线)与corpora.aifdb.org仍在其官网列为active;但配套工具如Argublogging(2012年后无更新)、Arvina(2013年后无更新)、OVA-gen(2011 alpha后无进展)已明显停滞,2022年后到2026年之间是否仍有更新未能进一步核实(已列入gaps)

**适配判断**: 三节点分层(I-node内容/RA-node推理关系实例/CA-node冲突关系实例/PA-node偏好关系实例)与我们"版本节点(材料内容)+设计规范边(推理/依据关系)+审阅意见(冲突/偏好判断)"的三层结构高度同构,可直接借鉴其"把关系本身也建模成节点(S-node),而不是简单的有向边"这一做法——如果我们的"反证"关系需要挂丰富元数据(谁反证的、依据哪条critical question、结论是什么),用类似AIF的S-node模式(关系实例化为独立节点)比单纯打标签的边更合适。工具链本身(OVA/AIFdb)对我们不构成直接可复用的软件资产,但其节点类型词表本身值得抄。

**来源**: https://www.arg.tech/index.php/category/software/ · http://www.arg-tech.org/wp-content/uploads/2011/09/aif-spec.pdf · https://www.researchgate.net/publication/227201642_The_Argument_Interchange_Format


### ASPIC+ 结构化论证框架 〔paper〕

- 核心攻击类型共3种(此为框架惯例词表,非独立枚举文档,已在gaps中注明未深挖至逐字定义级别)
- Rebutting attack(反驳攻击)— 攻击论证的结论本身,提出相反结论
- Undermining attack(削弱攻击)— 攻击论证所依赖的前提(非默认前提)本身
- Undercutting attack(削弱推理攻击)— 攻击论证的推理步骤本身(即该推理规则在此情境下不适用),不直接否定前提或结论
- Strict rules(严格规则)— 若前提为真结论必然为真的推理规则,不可反驳只可削弱前提
- Defeasible rules(可废止规则)— 前提为真结论通常但非必然为真的推理规则,可被三种攻击之一击败

**存活状态**: 源自欧盟ASPIC项目(2004-2007),由Prakken与Modgil于2013年系统化发表"a tutorial"论文,是计算论证学界最常引用的结构化论证形式化框架之一;2024年仍有下游实现更新(如StabilityLabelAlgorithm包据检索显示2024年10月有更新)、以及LLM-ASPIC+(将大模型与该框架结合做defeasible reasoning)等2024-2025年新论文,理论框架保持活跃学术引用但主要存在于论文和小型代码包层面,未见大规模工业采用

**适配判断**: 三种攻击类型(反驳/削弱前提/削弱推理)比Walton的critical questions更贴近我们要的"反证"边的精确子类型划分——如果要把"反证"细分,可以直接借用这三种(反驳结论/否定依据/否定推理步骤本身),比自造词更有理论依据;但本次未查到逐字的一手定义原文,只从二手信息交叉验证,使用前建议查一遍Modgil & Prakken (2013) tutorial原文核对措辞。

**来源**: https://arg-tech.org/index.php/toast-an-aspic-implementation/ · https://www.tandfonline.com/doi/abs/10.1080/19462166.2013.869766


### RST(修辞结构理论,Rhetorical Structure Theory) 〔methodology〕

- 共32个关系(SFU现行维护版本:11个Presentational关系+14个Subject Matter关系+7个Multinuclear关系),注:1988年Mann&Thompson原始论文版本关系数与此有出入,未逐一核对(见gaps)
- Nucleus-Satellite(核心-卫星)结构类型— 主从不对等的文本片段关系(hypotactic从属关系)
- Multinuclear(多核)结构类型— 地位对等的文本片段并列关系(paratactic并列关系)
- Elaboration(细化)— 卫星片段对核心片段提供更详细的信息
- Evidence(证据)— 卫星片段提供证据以增强读者对核心片段的信任
- Justify(辩护)— 卫星片段为核心片段的可接受性提供理由
- Concession(让步)— 卫星片段承认与核心片段表面冲突的信息,以增强核心可信度
- Condition(条件)— 卫星片段陈述核心片段成立的前提条件
- Volitional/Non-volitional Cause & Result(意志性/非意志性因果)— 区分行动主体主观意图导致的因果与自然/非意图性因果
- Purpose(目的)— 卫星片段陈述核心行动所要达成的目的
- Contrast(对照,多核)— 两个对等片段呈现相异/对比的情形
- Sequence(序列,多核)— 多个对等片段按时间/逻辑顺序排列
- Joint/List(并列/列举,多核)— 多个对等片段无特定修辞关系地并列陈列

**存活状态**: 1988年Mann&Thompson提出,长期是计算语言学篇章结构分析(discourse parsing)的主流理论基础之一,RST Discourse Treebank(RST-DT)语料库至今仍是篇章解析研究的标准评测数据集,2020年代仍有大量neural discourse parsing论文(如neural RST-based discourse coherence评测)以RST关系集为标注体系,理论与语料库均处于持续活跃使用状态

**适配判断**: RST关系集是给"同一文本内部片段之间"的修辞功能定性,不是给"不同版本产物之间"定关系,与我们的应用场景(版本轨迹图/决策记录)错位较大;但其"核心-卫星非对称 vs 多核对等"这一二分结构设计思路,可以作为我们判断"两个决策节点连线是主从关系还是并列关系"的元层参考(例如"问题拆分"产生的子问题之间是Joint/List式并列,而"推导"产生的结论对前提是Nucleus-Satellite式从属),关系词本身不建议直接搬用。

**来源**: https://www.sfu.ca/rst/01intro/definitions.html · https://apps.dtic.mil/sti/tr/pdf/ADA173859.pdf


### CiTO(Citation Typing Ontology,引文类型本体) 〔standard〕

- 核心为2个属性(cites及其逆属性isCitedBy)+41个cites的子属性(逐字):agreesWith(赞同)、citesAsAuthority(引作权威)、citesAsDataSource(引作数据来源)、citesAsEvidence(引作证据)、citesAsMetadataDocument(引作元数据文档)、citesAsPotentialSolution(引作潜在解决方案)、citesAsRecommendedReading(引作推荐阅读)、citesAsRelated(引作相关文献)、citesAsSourceDocument(引作派生来源文档)、citesForInformation(为获取信息而引用)、compiles(编纂自)、confirms(确证)、containsAssertionFrom(包含来自…的论断)、corrects(纠正)、credits(致谢/归功于)、critiques(批评)、derides(嘲讽)、describes(描述)、disagreesWith(不同意)、discusses(讨论)、disputes(质疑/反驳)、documents(记录说明)、extends(扩展)、includesExcerptFrom(包含摘录自)、includesQuotationFrom(包含引语来自)、linksTo(链接到)、obtainsBackgroundFrom(从…获取背景)、obtainsSupportFrom(从…获取支持)、parodies(戏仿)、plagiarizes(剽窃自)、qualifies(限定/附加条件于)、refutes(驳倒)、repliesTo(回应)、retracts(撤回)、reviews(评论)、ridicules(讥讽)、speculatesOn(对…作推测)、supports(支持)、updates(更新)、usesConclusionsFrom(使用…的结论)、usesDataFrom(使用…的数据)、usesMethodIn(使用…中的方法)
- 另有generic object properties共4个:sharesAuthorWith(共享作者)、sharesAuthorInstitutionWith(共享作者机构)、sharesFundingAgencyWith(共享资助机构)、likes(点赞式引用)
- 另有3个data properties:hasCitationCreationDate、hasCitationTimeSpan、hasCoAuthorshipCitationLevel

**存活状态**: 本体本身持续维护(SPAR Ontologies项目,GitHub活跃仓库,版本2.8.2,2017年10月最近一次实质版本发布,文档页标注2026年6月仍有元数据更新);但真实标注采用率很低——Journal of Cheminformatics 2020年发起CiTO Pilot试点,两年内仅新增1篇编辑评论+6篇论文采用,标注意图种类比试点预期更丰富但总量仅约300次引用、分布在100多个期刊中,Wikidata经Scholia可视化的CiTO标注仅覆盖56篇文章、24种期刊;OpenCitations等开放引文项目虽支持CiTO但整体出版界大规模momentum转变"很难"(检索结果原话)

**适配判断**: 这是我们"产物之间标准化动词"最直接的一手先例,其失败教训极具警示性:41个逐字动词语义丰富、覆盖论证/引用几乎所有意图(赞同/反驳/扩展/使用方法/使用数据等),但真实标注量极小、且集中使用的其实只是extends/critiques/disputes/usesMethodIn/supports等个位数的高频词,长尾30多个动词几乎无人在真实标注中用到。这直接支持我们"词表要小"的设计原则——如果借鉴CiTO,应该只挑其中当前存活证据最强的5-8个高频动词(如cites/extends/critiques/disputes/usesMethodIn/supports/updates/corrects)作为我们初始版本间关系词表的候选,而非照搬全41个。

**来源**: https://sparontologies.github.io/cito/current/cito.html · https://link.springer.com/article/10.1186/s13321-020-00448-1 · https://link.springer.com/article/10.1186/s13321-023-00683-2


### W3C PROV-O(PROV Ontology,溯源本体) 〔standard〕

- 核心3个类:Entity(实体,一个具有固定面向的物理/数字/概念性事物)、Activity(活动,在一段时间内发生并作用于实体的事情)、Agent(施事者,对某活动的发生承担某种责任的事物)
- 核心对象属性共12个(逐字定义):wasGeneratedBy(某活动完成了对某新实体的产出)、used(某活动开始利用某实体)、wasInformedBy(两个活动之间交换了一个实体,一方使用了另一方生成的实体)、wasAssociatedWith(将某活动的责任赋予某施事者)、actedOnBehalfOf(将权限与责任作为代理人身份赋予某施事者)、wasDerivedFrom(某实体经由转换或基于既存实体构建而成为另一实体)、wasAttributedTo(将某实体归属于某施事者)、wasRevisionOf(表明派生实体包含了原始实体的实质性内容,是对原实体的修订版)、wasQuotedFrom(引用了一个可能更大的实体,新实体由此创建)、hadPrimarySource(引用了由具有直接知识的施事者产出的在先实体,作为一手来源)、wasInvalidatedBy(导致某实体失效的活动)、invalidated(wasInvalidatedBy的逆属性)

**存活状态**: 2013年成为W3C正式推荐标准(Recommendation),是当前科学数据溯源/工作流可复现性领域事实标准,持续被广泛采纳:RO-Crate(Research Object Crate)及其Workflow Run RO-Crate扩展(2024年PLOS One论文发表,已在至少6个工作流管理系统中实现)显式对齐PROV-O;science research object社区持续维护(researchobject.org),2024-2025年仍有大量机器学习溯源追踪、区块链溯源架构等论文将PROV-O作为底层本体基准引用

**适配判断**: 这是本次调研中存活证据最强、采用最广的体系,核心原因是词表极简(3类节点+约12个核心关系)且大多数关系可由工具自动记录(工作流引擎自动生成wasGeneratedBy/used/wasDerivedFrom等,不需要人手动逐条打标语义类型),这与CiTO需要人工语义判断形成鲜明对比。对我们的版本轨迹图,wasDerivedFrom(派生自)、wasRevisionOf(修订自)、wasInformedBy(受…活动影响,暗示两个决策活动之间有信息传递但非直接派生)这几个词的语义分层(实体级 vs 活动级)提供了一个重要设计启示:我们可能需要区分"版本产物之间的派生关系"(实体级,类似wasDerivedFrom/wasRevisionOf)与"决策活动之间的影响关系"(活动级,类似wasInformedBy),这是一个当前词表设计中容易被混为一谈但PROV-O给出了清晰先例区分的维度。

**来源**: https://www.w3.org/TR/prov-o/ · https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0309210 · https://arxiv.org/abs/2312.07852


**领域死因教训**: 共同死因分三类:(1)工具/软件层比理论层脆弱得多——AIF/AIFdb/OVA 系列理论稳定但工具链多次重写(OVA→OVA2→OVA3,2022年最后一次大改),小型学术项目缺工程维护资源,一旦维护者毕业/转岗即停更(如 Arvina 2013年后无更新、Argublogging 2012年后停滞);(2)采纳率与"理论优雅度"无关、与"标注成本"强相关——CiTO 语义丰富(41个逐字动词)但真实标注量极小(仅约300次引用被打标、集中在两三本期刊的试点),因为要求作者/审稿人手动选择精确的引用意图动词,认知负担超过收益,大多数出版商用不打类型的裸引用；PROV-O 反而因为"只需极简三元组(Entity/Activity/Agent + 少量关系)+工具自动生成(RO-Crate等工作流系统自动记录)"而广泛存活,不需要人手动打标；(3)词表颗粒度失衡是长期性死因——Walton体系从最初几十个涨到2008年这本书的96个,颗粒度不断细分导致谁都记不住全表,实际使用中只有前10-15个高频常识性图式(专家意见/类比/因果/征兆/后果/先例)被反复引用,长尾图式几乎从未被独立使用过,这对我们"词表要小且稳定"的目标是直接的反面教材:一旦词表膨胀超过人脑工作记忆(约7±2),标注一致性会塌陷。


**未覆盖/存疑**: 1) Walton/Reed/Macagno (2008)《Argumentation Schemes》一书是否精确收录96个图式,来源仅为一条搜索引擎摘要式断言,未能从原书或可靠二次文献直接核实该数字(注意reasoninglab网站索引到的是29个,这是常被引用的"精简常用集",96可能是包含所有变体亚型后的全集,两个数字都需读者自行核对原书目录);建议如需精确数字,后续应直接查原书目录页或找一篇明确列出总数的综述论文(如Macagno & Walton后续文章)。
2) 未能拿到PDF全文,因此expert opinion六条critical questions的逐字英文原文是从多条搜索片段拼合确认(Expertise/Field/Opinion/Trustworthiness/Consistency/Backup Evidence六问对应关系已交叉验证一致),但完整逐字句子(非我转述)建议使用时再查一遍Walton原书或"On a razor's edge: evaluating arguments from expert opinion"(Walton 2014, Journal of Applied Logic)原文核对逐字标点。
3) ASPIC+ 框架本身是纯理论框架(无独立"词表",而是推理规则+反驳/优先关系的形式化),我按用户任务只做了状态核实未深挖其"逐字词表",因为ASPIC+本质上没有像CiTO/PROV-O那样的固定动词清单,它的"词表"其实是AIF的CA/RA/PA复用;如果用户后续需要ASPIC+专门的"undermine/rebut/undercut"三种攻击类型逐字定义,建议补一轮查证(这三个词在我们的"反证"动词设计上可能比Walton的critical questions更直接可用,值得后续单独深挖)。
4) RST的32个关系总数是从SFU RST官网单页抓取,历史上Mann&Thompson 1988原始论文的关系数(常引用为23或25)与后续扩展版本(SFU现行页面列出的32个)存在版本差异,我给出的是SFU官网当前维护版本,未逐一核对1988年原始论文的更小版本清单。
5) 未能核实AIFdb当前(2026年)是否仍能访问/是否已下线——2022年OVA3是最后确认的更新记录,之后到2026年的4年空窗期未查证,存活状态可能已经进一步弱化,建议如果要采用AIF工具链本身(而非仅采用其节点类型词表)需先实地访问ova.arg.tech确认可用性。


## 附录·LLM 推理结构/树搜索

这条线(Tree of Thoughts→Graph of Thoughts→RAP/LATS→rStar/rStar-Math→MCTS+DPO/PRM800K)是"用蒙特卡洛式树搜索驱动LLM推理"这一学术子领域的骨架,时间跨度2023-08到2025-08共约两年,证据链完整且高质量(全部有官方仓+论文,多数仓库2025年内仍有提交)。核心启示:(1)"用一小撮命名动词/算子描述推理步骤"这件事本身有先例且被验证有效——最直接的证据是 rStar 的 A1-A5(五个人类式推理动作)消融实验,逐步加动作从70.5%涨到75.0%,证明"命名动作集比单一动作/无结构CoT更强",这是用户想要的"动词驱动树探索"最硬的先证;(2)但这一层"命名动词表"在学术演进里是过渡态而非终态——继承者(rStar-Math、rStar2-Agent)逐步把命名动作替换成更细粒度的隐式步骤(代码增强CoT步骤、agentic工具调用),说明命名动词表更适合"人机协作标注/可解释性"场景而非"纯粹追求算法性能"场景,这恰好吻合我们的用途(人和AI要用同一词表标注,不是要极致刷分);(3)图论层面 Graph of Thoughts 提供的 Generate/Aggregate/Refine 三个正式算子定义是目前唯一"用图的语言定义推理变换"的规范化先例,可直接借用其 V+/E+ 集合论记法来描述我们的版本轨迹图的边/节点变换;(4)把树上轨迹转成训练数据("明文训练"的近亲)已经是活跃研究簇(MCTS+DPO/ReST-MCTS*/AlphaLLM-CPL),核心机制是"同一父节点下,取Q值最高与最低的候选步骤组成偏好对,一棵深度T的树产出T对偏好数据"——这给我们"把人类裁决沉淀为明文规则喂回AI"提供了具体可操作的数据结构范式(偏好对而非离散标签);(5)PRM800K式"每步打+1/0/-1三档标签"这条路径本身没死(数据集仍公开、仍被引用),但最新一代工作(rStar-Math)明确认为直接离散打分不如排序偏好对,这是我们设计"审阅意见"标注体系时的重要取舍参考。


### Tree of Thoughts (ToT) 〔paper〕

- 共3类核心操作(节点=thought,搜索算法=BFS/DFS,评估=value/vote)
- thought — 表示中间推理步骤的一段连贯语言序列(a coherent language sequence that serves as an intermediate step toward problem solving)
- thought generator — 从当前状态生成k个候选thought的生成器(propose/sample两种实现)
- state evaluator — 对当前思维状态做启发式评估,输出value或vote两种形式
- search algorithm — BFS或DFS,在thought树上做系统性搜索以选择要扩展/回溯的节点

**存活状态**: 论文NeurIPS 2023 poster,官方仓 princeton-nlp/tree-of-thought-llm 于GitHub API核实为 archived:true、最后push 2023-06-01,stars 2146(仓库已被作者归档,不再维护,但仍是最高引用的规范先例);社区fork仓kyegomez/tree-of-thoughts等仍偶有更新但非官方

**适配判断**: 提供了最基础的'节点=一步思维、树上做系统搜索'骨架,但它的動詞層(propose/sample、value/vote)是搜索策略而非我们要的领域动词(问题拆分/反证/联想等);可作我们树结构的地基但动词表需要另建

**来源**: https://arxiv.org/abs/2305.10601 · https://github.com/princeton-nlp/tree-of-thought-llm · https://neurips.cc/virtual/2023/poster/72797


### Graph of Thoughts (GoT) 〔paper〕

- 共3类正式定义的思维变换算子(论文Section 3.2逐字给出集合论定义)
- Generation(生成) — 基于已有单个thought v生成一个或多个新thought:V+={v1+,…,vk+}, E+={(v,v1+),…,(v,vk+)}(one can generate one or more new thoughts based on an existing single thought v)
- Aggregation(聚合) — 把任意多个thought聚合成一个新thought,以合并优点消除缺点:V+={v+}, E+={(v1,v+),…,(vk,v+)}(one can aggregate arbitrary thoughts into new ones, to combine and reinforce the advantages of these thoughts, while eliminating their disadvantages)
- Refining(精炼) — 通过修改内容对当前thought v做自环式迭代:V+={}, E+={(v,v)}(the refining of a current thought v by modifying its content)
- (辅助/非正式算子,论文用例中出现但未给出同级数学定义)Score/Improve/KeepBest/Validate — 在具体用例中作为Prompter/Parser组件出现,不是Section 3.2定义的图变换原语

**存活状态**: 论文ETH Zurich出品(Besta et al.),官方仓 spcl/graph-of-thoughts 经GitHub API核实 archived:false、最后push 2025-01-16、stars 6022,仍活跃(非归档,但commit频率低,近期主要是维护性提交);学术引用持续被后续graph推理综述引用为标准参照

**适配判断**: 直接命中用户要的'把推理定义成图上算子'这一诉求;Generate/Aggregate/Refine 三个算子的集合论记法(V+/E+)可以直接借用来形式化我们版本轨迹图的边变换类型(生成新版本/合并多版本/原地修订);但GoT本身只有3个算子,过于粗粒度,不能直接当我们的'问题拆分/反证/联想/推导'词表用,只能当骨架记法

**来源**: https://arxiv.org/abs/2308.09687 · https://github.com/spcl/graph-of-thoughts


### RAP (Reasoning via Planning) / LLM Reasoners 〔paper+tool〕

- 共4个核心概念(无固定命名动作集,动作按任务实例化)
- state s — 描述当前推理进度的自然语言配置(Blocksworld中=积木配置;数学推理中=中间变量取值;逻辑推理中=当前关注的事实)
- action a — 从p(a|s_t,c)采样得到的、使状态发生转移的算子,由任务定义(Blocksworld中固定为 Stack/Unstack/Put/Pickup 四个动词;数学推理中=生成一个子问题subquestion;逻辑推理中=从事实集中选一条规则)
- world model — 复用同一LLM得到状态转移分布p(s_{t+1}|s_t,a_t,c'),预测动作执行后的下一状态
- reward r(s,a) — 综合动作对数概率、状态置信度(采样一致性)、LLM自我评估("这步推理是否正确")与任务启发式的组合奖励

**存活状态**: 论文EMNLP 2023(Hao et al.),原始官方仓 Ber666/RAP 经GitHub API核实 archived:false、最后push 2023-08-25、stars 196(此后未再更新,事实上进入维护期);其思想被同作者团队重构进后续统一库 maitrix-org/llm-reasoners(论文2404.05221),该库经API核实 archived:false、最后push 2025-06-10、stars 2339,仍在活跃维护——RAP作为'search+reward+world model'三组件之一被吸收进这个更通用的库,原始独立仓本身已停更但方法论以库的形式存活

**适配判断**: RAP不提供固定命名动词表(动作按任务定义),对我们'词表要跨项目稳定'的目标参考价值有限;但它的state/action/world-model/reward四元结构,以及'RAP被泛化收编进llm-reasoners统一库'这一演化路径,提示我们的决策动词层也应该设计成'骨架协议+可插槽的领域动词'而非试图一次性穷举

**来源**: https://arxiv.org/abs/2305.14992 · https://github.com/Ber666/RAP · https://arxiv.org/abs/2404.05221 · https://github.com/maitrix-org/llm-reasoners


### LATS (Language Agent Tree Search) 〔paper〕

- 共6个MCTS循环步骤(逐字引自论文Section 4.2)
- Selection(选择) — 从根节点(初始状态s0)出发,每层选一个子节点直到到达叶节点(a child node is selected at each tree level until a leaf node is reached)
- Expansion(扩展) — 选定节点后,从策略pθ采样n个动作来扩展树(sampling n actions from pθ)
- Evaluation(评估) — 给每个新子节点赋一个标量值,用于后续选择和反向传播(assigns a scalar value to each new child node)
- Simulation(模拟) — 将当前选中节点继续扩展直到到达终止状态(expands the currently selected node until a terminal state is reached)
- Backpropagation(反向传播) — 根据一条轨迹的结果更新树上各节点的值(updates the values of the tree based on the outcome of a trajectory)
- Reflection(反思) — 遇到失败的终止节点时,让pθ根据轨迹和最终奖励给出一段verbal self-reflection(自然语言自我反思)

**存活状态**: 论文ICML 2024(Zhou et al.,2023-10提交),官方仓 lapisrocks/LanguageAgentTreeSearch 经GitHub API核实 archived:false、最后push 2024-07-30、stars 844,近一年无新提交(事实上停滞但未归档)

**适配判断**: LATS论文明确声明不预定义固定的thought/action类型词表('the exact instantiation of the action space depends on the particular environment'),它贡献的是MCTS六步循环的流程动词(而非领域动词),可以借用来描述我们'探索一条设计分支'的过程阶段,但不能替代我们要建的'问题拆分/反证/联想'这层领域动词

**来源**: https://arxiv.org/abs/2310.04406 · https://github.com/lapisrocks/LanguageAgentTreeSearch


### rStar (Mutual Reasoning, 五个人类式推理动作 A1-A5) 〔paper〕

- 共5个人类式推理动作(A1-A5,论文逐字定义,是用户要的'标准化动词驱动树探索'最直接先证)
- A1: 提出一步思维(Propose an one-step thought) — 提示LLM为给定问题生成下一个单步思维(prompts the LLM to generate the next one-step thought for a given question)
- A2: 提出剩余的思维步骤(Propose the remaining thought steps) — 不同于每次只生成一步,该动作对齐标准CoT一次性生成剩余全部步骤(aligns with standard CoT)
- A3: 提出下一个子问题并回答(Propose next sub-question along with its answer) — 受least-to-most提示法启发,把复杂问题拆解为子问题(breaks down a complex problem)
- A4: 重新回答子问题(Answer the sub-question again) — 因A3生成的子问题回答可能不正确,该动作对其重新作答(re-answer it)
- A5: 改写问题/子问题(Rephrase the question/sub-question) — 分析错误案例发现很多错误源于LLM误解题意,故引入该动作(the LLM misunderstanding the question)
- discriminator(判别器) — 另一个SLM,通过尝试补全被掩盖的部分推理轨迹,对MCTS生成的候选轨迹给出无监督反馈

**存活状态**: 论文2024-08(Qi et al.),官方仓 zhentingqi/rStar 经GitHub API核实 archived:false、最后push 2025-01-23、stars 973,已被ICLR 2025接收;后续被同团队升级为 rStar-Math(2025-01)与 rStar2-Agent(2025-08),这两代后续工作均放弃了A1-A5这套命名动作表(见rStar-Math条目),说明'5个命名动词'这一具体设计本身是过渡方案而非终态,但其'动作数量增加→效果提升'的消融结论被保留验证

**适配判断**: **这是本次调研中与用户诉求最贴合的直接先证**:论文Table 1明确做了动作数量消融——A3-only(70.5%,即RAP基线)→A3+A5(72.5%)→A3+A4+A5(73.5%)→A2+A3+A4+A5(74.0%)→全部A1-A5(75.0%),证明逐步扩充命名动作集合能稳定提升效果,且5个动作已经'plays a crucial role'(每个动作都有独立贡献);这直接回答了用户关心的'动作集大小对探索效果的影响是否被消融验证过'——答案是肯定的,且5个左右的小动作集就能取得大部分收益,支持我们'词表要小'的设计目标有实证基础

**来源**: https://arxiv.org/abs/2408.06195 · https://github.com/zhentingqi/rStar


### rStar-Math 〔paper〕

- 不再使用A1-A5命名动作表,改为单一动作类型 + 排序式步骤标注(共2个核心机制)
- action(动作) — 策略模型生成的一步'代码增强CoT'步骤:一步自然语言思维连同对应Python代码,NL CoT以Python注释形式嵌入代码中(the policy model generates a one-step NL CoT alongside its corresponding Python code);只有成功执行的Python代码才被保留为有效候选
- PPM(Process Preference Model,过程偏好模型) — 不用Q值直接做步骤级奖励标签,而是用Q值排序来构造偏好对:同一MCTS树中选Q值最高的两个候选作为正例、最低的两个作为负例,用pairwise ranking loss(Bradley-Terry模型)训练(we select two candidates with the highest Q-values as positive steps and two with the lowest as negative steps)

**存活状态**: 论文2025-01-09(Microsoft Research Asia),官方仓已并入 microsoft/rStar(GitHub API核实该仓 archived:false、最后push 2025-09-12、stars 1420),rStar-Math代码保留在该仓的 rStar-math 分支;仓库README明确称rStar-Math为'Our prior work'(前代工作),当前main分支已是继任者rStar2-Agent

**适配判断**: 关键教训案例:同一团队的下一代工作明确放弃了'命名人类式动作集'(A1-A5),改用更细粒度的隐式代码步骤;同时也明确放弃了'直接给步骤打离散分数标签'这种PRM式做法,改用Q值排序生成的偏好对——这为我们设计'审阅意见'的沉淀格式提供了正面参考(偏好对/相对排序 优于 绝对打分),但也提醒我们:命名动词表在追求算法性能的语境下会被自然淘汰,我们要用的场合(人机协作标注、可解释性、复用)与rStar-Math的场合(纯粹提升数学正确率)目标不同,所以'弃用命名动作'不构成对我们方案的反对证据,只是范围提醒

**来源**: https://arxiv.org/abs/2501.04519 · https://github.com/microsoft/rStar


### rStar2-Agent 〔paper〕

- 不再使用树搜索/MCTS或命名动作表,核心机制是纯agentic强化学习(共1个新算法名)
- GRPO-RoC(Group Relative Policy Optimization with Resample-on-Correct) — 在含噪代码执行环境中做有效训练的强化学习算法,用于让模型'先仔细思考再调用Python工具、并对代码执行反馈进行反思'(thinking carefully before using Python coding tools and reflecting on code execution feedback)

**存活状态**: 论文2025-08-28(arXiv 2508.20722),代码在 microsoft/rStar 仓main分支,该仓GitHub API核实最后push 2025-09-12、stars 1420、未归档,是rStar系列当前最新/最活跃形态

**适配判断**: 标志着'蒙特卡洛树搜索+命名推理动作'这条路线在该团队内部被完全替换为'无树无命名动作的纯RL+工具调用'——这是本调研中最强的'死因'证据:命名动词/树搜索脚手架被证明只是通往更强RL信号的过渡工具,一旦有足够训练算力/数据,团队会移除显式结构;对我们而言这不构成负面结论(我们的目标是人机协作可解释标注,不是刷分),但要清楚认识到学术界不会再继续投入维护这层

**来源**: https://arxiv.org/abs/2508.20722 · https://github.com/microsoft/rStar


### Everything of Thoughts (XoT) 〔paper〕

- 共3个MCTS阶段 + 2个思维修订步骤(逐字引自论文)
- thought(思维) — 定义为状态-动作对 τ={s,a}
- Selection(选择) — 从根节点出发,依据已有信息选择动作a*(the algorithm initiates at the root node and proceeds to choose an action a* from the available set A(s))
- Expansion & Evaluation(扩展与评估) — 到达未被选择过的叶节点后,扩展到下一步的新状态s以探索新思维(we expand to the state s for the next step for new thought exploration)
- Backpropagation(反向传播) — 沿路径更新所有Q(s,a)值
- Error Detection(错误检测) — 指示LLM检测MCTS生成的思维中的错误(instruct the LLM to detect any errors in the thought generated by MCTS)
- Revision(修订) — 从错误状态的父状态出发,MCTS再做一组L次模拟,最终产出修订后的思维(ultimately yielding a revised thought)

**存活状态**: 论文2023-11(Microsoft,Ding et al.),官方仓 microsoft/Everything-of-Thoughts-XoT 经GitHub API核实 archived:false、最后push 2024-02-21、stars 161,已一年多无实质提交,事实上处于停滞状态但未被归档

**适配判断**: 贡献了'预训练RL策略网络+MCTS共同产出思维,再让LLM做错误检测/修订'这一'MCTS-LLM协作修订'框架,和用户提到的'反证'动词有些呼应(Error Detection可类比反证/证伪一步),但该体系同样没有定义我们需要的细粒度领域动词(问题拆分/联想/推导等),只提供了流程阶段词,且项目热度与维护度在同类中最低

**来源**: https://arxiv.org/abs/2311.04254 · https://github.com/microsoft/Everything-of-Thoughts-XoT


### PRM800K / Let's Verify Step by Step 〔paper+dataset〕

- 共3档步骤级标签(逐字确认于GitHub README/HF数据集页,原论文为'Let's Verify Step by Step')
- +1(正确/Correct) — 该步骤推动解答正确前进的正向评级
- 0(中性/Neutral,无进展) — 该步骤本身不算错误,但没有取得任何进展(it isn't incorrect, but it does not make any progress)
- -1(错误/Incorrect) — 该步骤存在错误的负向评级

**存活状态**: 论文2023-05(OpenAI,Lightman et al.),官方数据集仓 openai/prm800k 经GitHub API核实 archived:true、最后push 2023-06-01、stars 2146——**仓库已被官方归档,不再维护**,但数据集本身持续被后续PRM/过程监督类工作引用为标准基线(如Math-Shepherd等论文对比对象),数据资产存活、代码仓库已死

**适配判断**: 给出了目前最广泛引用的'离散步骤标签集'范例(3档:正确/无进展/错误),可作我们'审阅意见'标注体系的备选之一;但要注意后续工作(rStar-Math等)明确认为直接离散打分不如Q值排序生成的偏好对有效,这是我们设计标注格式时的重要权衡依据——如果目标是训练可用信号,偏好对可能优于绝对标签;如果目标是给人看的、可解释的审阅意见,3档式离散标签仍有可读性优势

**来源**: https://arxiv.org/abs/2305.20050 · https://github.com/openai/prm800k


### MCTS+DPO / 步骤级偏好学习(以arXiv 2405.00451为代表) 〔paper〕

- 共2个核心机制(逐字引自论文,这是'树轨迹→训练数据'即用户所说'明文训练'的最直接近亲范式)
- state s_t — 推理链的前缀(the prefix of the reasoning chain)
- action a — 新增的一个推理步骤,拼接到前缀上使状态转移(a new reasoning step... s_{t+1} is the concatenation of s_t and a)
- Q值排序生成偏好对 — 用每个候选步骤对应的Q值来标注其偏好等级,Q值更高表示更优的下一步(we use the result Q value corresponding to each candidate step to label its preference, where higher Q values indicate preferred next steps)
- 正负样本选择 — 在树的每一深度,选Q值最高与最低的候选步骤分别作为正例和负例(we select the candidate steps of highest and lowest Q values as positive and negative samples at each tree depth)——一棵深度T的搜索树产出T对步骤级偏好数据,直接喂给DPO训练

**存活状态**: 论文2024-05(与OpenR等开源推理项目同期出现的研究簇之一),同类工作还包括 ReST-MCTS*(2024-06)、AlphaLLM-CPL(2024-10)、TreeRL(2025-06)等,构成一个持续活跃、2024-2025年仍在增补新论文的研究簇;本次调研未能核实单篇代表作的官方GitHub仓库存活状态(仅确认论文本身引用链条持续到2025年中),需要后续单独核实各仓库维护状态

**适配判断**: 这是用户要的'把树上轨迹变成训练数据'链路里机制最清晰、最可直接借用的范式:'同一父节点/同一深度下,取评分最高与最低的候选组成偏好对'——这个数据结构可以直接对应我们'人类裁决沉淀为明文规则'的一种具体实现形式(不是给单个决策打绝对分,而是在决策树同一层的多个候选方案间做相对优劣标注),比PRM800K的绝对标签更贴近我们'审阅意见挂在版本旁'的相对比较场景

**来源**: https://arxiv.org/abs/2405.00451 · https://arxiv.org/abs/2406.03816 · https://arxiv.org/abs/2410.06508 · https://arxiv.org/abs/2506.11902


**领域死因教训**: 共同死因/教训: (1) 独立算法仓一旦发论文即"完成态"停更(ToT/RAP/GoT/XoT/LATS 的原始仓最后一次真正的功能性提交都停在发表后数月内,后续只有依赖bot/小修),体系本身没有"死"但也没有持续演进——它们被固化成教科书式基线,新工作在论文里引用、在代码上重新实现,而不是长期维护原仓;(2) 真正被长期打磨、仍在2025-2026活跃提交的,无一例外是"泛化成库"或"从固定动作集升级为端到端RL"这两种转型:RAP→llm-reasoners(把ToT/RAP/CoT统一成 search+reward+world-model 三组件库,2025-06仍推送)、rStar(A1-A5固定人工动作集)→rStar-Math(动作从"人工命名的5种推理动作"收窄成单一的"code-augmented CoT步骤",丢弃了显式命名动作表)→rStar2-Agent(2025-08,进一步丢弃MCTS+固定动作,改成纯agentic RL+Python工具调用,树结构和命名动作词表完全消失);(3) PRM800K式"人工标注的离散步骤标签"路线也被绕过——rStar-Math 明确弃用"直接给步骤打离散分/类别标签"的方案,改用 Q值排序生成偏好对(pairwise preference,非分类标签),论文原话是不用"naive step-level score annotation";(4) 结论:动词/动作表这一层在学术界exists但生命周期短,一旦证明"小的命名动作集比无结构CoT/单动作RAP基线好"这个消融结论后,后续工作就抛弃命名动作转向(a)更细粒度的隐式token级/代码级动作,或(b)完全去结构化的强化学习信号;换句话说,命名动词表是"引导阶段的脚手架",不是终态,长期看会被更细的自动发现机制替代——这对我们"词表要小且稳定"的目标是正面消息(rStar的消融证明5个动作确实比1个/3个好且够用),但也是警示(别指望这套词表能长期免维护,它在学术界的宿命是被替换而非被继续维护)。


**未覆盖/存疑**: 1) 未能读取 arxiv PDF 原文的完整数学记号(工具链只能提取网页/ar5iv渲染文本,GoT的 V+/E+ 集合定义靠 ar5iv 提取,基本可信但未逐字核对PDF公式编号);2) LATS/XoT 论文里是否有关于"动作空间大小"的正式消融实验——未查到明确证据(LATS原文明确说它不定义固定动作集,所以谈不上"动作数量消融";XoT论文摘要/正文里没有类似rStar Table 1那样的动作数量消融表,只在rStar/rStar-Math里查到了);3) MCTS+DPO这条链路只深入查了一篇代表作(arXiv 2405.00451,MCTS-DPO/OpenR系),未系统核实它与"ReST-MCTS*""AlphaLLM-CPL""TreeRL"等同族工作之间谁是最主流/被引用最多的代表,只能说这是活跃研究簇(2024-2025年多篇),暂无法给出单一"生死判决";4) PRM800K 的三分类标签(+1/0/-1)是从GitHub README/HF数据集页提取,未逐字核对论文原文对"neutral"含义的完整定义句;5) 未核实这些体系在"游戏策划/前端组件设计"等主观设计决策场景下有没有被直接迁移使用的先例(全部证据来自数学/代码/QA类任务,业务侧关于game design decision trace的直接对应案例本次没查到,需要另开一条调研线);6) rStar2-Agent是否/多大程度上仍算"同一脉络"存在解读空间——仓库把它放在同一个 microsoft/rStar 仓的 main 分支、明确称 rStar-Math 为"prior work",但论文本身不再自称"rStar"体系的延续用词,这点status证据略为间接。


## 附录·LLM 经验沉淀/明文自我改进

明文沉淀(把经验/裁决炼成自然语言规则喂回LLM,不改权重)这条线证据链完整且活跃度高:从2023年Reflexion/Voyager/ExpeL建立"轨迹→反思/insight→存进上下文"范式,到2024年AWM/Self-Discover/Buffer of Thoughts把"可复用子程序"和"推理模块组合"结构化,到2024-2025年TextGrad/DSPy/GEPA把优化过程本身变成可编程的"文本梯度"或"反思式进化",再到Constitutional AI一路做到2026年1月发布的Claude's Constitution(80页、23000词)。这些系统共享同一核心信念:自然语言规则/反思文本可以替代梯度更新完成"学习"。最强的规模边界证据来自两处:一是ExpeL自己承认"把反思也纳入insight生成过程反而损害性能",insight质量高于数量、存在饱和/污染风险,且论文未与微调做正面对比;二是Anthropic在2026年把Constitutional AI从"一组独立原则(standalone principles)"整体转向"解释为什么(explain why)"的叙事式治理,承认孤立规则罗列这条路走不远、模型需要理解原则背后理由才能泛化到新情境。GEPA(2025)是唯一给出量化优势的:用反思式文本进化以35倍更少rollout跑赢强化学习(GRPO)13-20%,是"明文沉淀 vs 参数更新"最有力的正面证据。DSPy/TextGrad已产品化(DSPy 3.x约35.8k star、2026年5月仍发新版;TextGrad发表于Nature)证明这套范式已从论文走向工程实践。


### Reflexion 〔paper〕

- 共3个核心模型角色+2类记忆
- Actor (M_a) — 依据状态观测被专门提示生成文本与动作的LLM
- Evaluator (M_e) — 对Actor产出打分,计算反映任务表现的奖励分数
- Self-Reflection (M_sr) — 生成verbal self-reflection(语言化自我反思文本)为后续试验提供反馈
- short-term memory — 轨迹历史(trajectory history),即本轮上下文
- long-term memory — Self-Reflection模型输出的存档,跨多轮试验保留教训

**存活状态**: 存活;论文NeurIPS 2023录用,GitHub noahshinn/reflexion约2701 star、258 fork,截至2026年6月仍有issue活动;概念(轨迹→语言反思→存入上下文再retry)已被后续ExpeL、AWM等直接构建其上,是2023年后agent自我改进论文的标准引用起点

**适配判断**: Actor/Evaluator/Self-Reflection三角色分工可直接映射我们的「生成-审阅-反思沉淀」;但Reflexion的反思只服务于同一agent的下一次retry(单任务内循环),不是跨项目/跨版本的裁决库,记忆是重跑覆盖式而非结构化规则库,与我们要的「决策树+明文规则库」形态有距离,更像我们体系里的一个局部算子(反思生成)而非整体架构

**来源**: https://arxiv.org/abs/2303.11366 · https://github.com/noahshinn/reflexion · https://ar5iv.labs.arxiv.org/html/2303.11366


### ExpeL (Experiential Learning) 〔paper〕

- 共4个insight操作算子+2种比较模式+3阶段流程
- experience gathering — 用Reflexion反复重试训练任务(最多Z次),把轨迹τ收集进经验池ℬ
- insight extraction — 对比经验池中的轨迹,产出/维护自然语言insight集合
- ADD — 新增一条insight,初始重要度计数=2
- EDIT — 修改已有insight内容
- UPVOTE — 认同某insight,重要度计数+1
- DOWNVOTE — 不认同,重要度计数-1,归零则删除
- success/failure pairs comparison — 把失败轨迹与同任务的成功轨迹对比提炼insight
- success patterns comparison — 在不同任务的多条成功轨迹间找共性模式
- task inference — 评测时用提炼的insight增强任务说明,并检索top-k相似成功轨迹作为in-context示例

**存活状态**: 存活;论文AAAI 2024录用(arXiv 2308.10144,2023年8月),后续大量论文(如2026年的Experiential Reflective Learning)直接沿用其insight-extraction机制做基线或改进对象,是「从轨迹提炼明文规则」这一子领域的奠基工作之一

**适配判断**: ADD/EDIT/UPVOTE/DOWNVOTE四算子是目前查到的、最贴近「人类裁决→明文规则库→带权重增删」这条需求的现成词表,可直接借鉴作为我们「审阅意见如何变更规则库」的边操作原型;论文明确报告insight数量/质量存在负面ablation(加反思到insight生成过程反而伤性能),提示我们的规则库要做质量门槛而非无脑累加

**来源**: https://arxiv.org/abs/2308.10144 · https://arxiv.org/html/2308.10144v2 · https://ojs.aaai.org/index.php/AAAI/article/view/29936


### Voyager 〔paper〕

- 共3个系统组件+1种技能存储形式
- automatic curriculum — 自动课程,持续提出新探索目标以最大化探索
- skill library — 不断增长的可执行代码技能库,存储与检索复杂行为
- iterative prompting mechanism — 迭代提示机制,融合环境反馈/执行报错/自我验证来改进程序
- skill (as executable code) — 技能以带描述的JavaScript源文件形式存储,按语义相似度经向量库检索复用

**存活状态**: 存活;论文2023年5月(arXiv 2305.16291),GitHub MineDojo/Voyager约7000 star,仍有PR/issue活动;被广泛引用为「技能库不靠微调实现终身学习」的代表案例,但2026年行业评述指出其工程成熟度落后于LangChain/CrewAI等生产级框架,更多作为学术参照而非直接部署方案

**适配判断**: 技能=可执行代码+自然语言描述+向量检索,这套「描述性文本索引具体产物」的模式,和我们「版本节点旁挂设计规范/审阅意见」的挂载思路接近,可参考其检索复用机制,但Voyager的技能是过程性程序而非决策裁决,不直接覆盖『主观设计裁决沉淀』场景

**来源**: https://arxiv.org/abs/2305.16291 · https://github.com/MineDojo/Voyager · https://voyager.minedojo.org/


### Agent Workflow Memory (AWM) 〔paper〕

- 共1个核心抽象+2种运作模式
- workflow — 解题过程中的常见子程序(common sub-routine),已抽象掉具体任务上下文
- workflow induction — 从训练示例或过往经验中归纳提炼workflow的过程
- offline AWM — 有标注训练示例时,提前从中提炼workflow
- online AWM — 无额外数据集时,agent在测试查询过程中动态构建workflow

**存活状态**: 存活;论文2024年9月(arXiv 2409.07429),ICML 2025 poster录用,GitHub zorazrw/agent-workflow-memory约445 star/51 fork;在Mind2Web/WebArena两大web导航基准上相对提升24.6%/51.1%,是「把可复用子程序抽象出来喂回agent」这条线的代表工作,持续被2025-2026年agent memory类论文引用对比

**适配判断**: offline/online两种induction模式直接对应我们「批量沉淀历史决策」vs「实时随裁决增量沉淀」两条路径;workflow本身是「去掉具体上下文的可复用子程序」,与我们想要的「设计规范(可跨版本复用的判断准则)」在抽象层次上高度同构,是目前最贴近我们目标形态的候选

**来源**: https://arxiv.org/abs/2409.07429 · https://github.com/zorazrw/agent-workflow-memory


### TextGrad 〔tool〕

- 共约8个核心API/概念
- tg.Variable — 包裹文本/数据并追踪梯度,requires_grad开启优化
- tg.BlackboxLLM — LLM前向调用封装(可带system prompt)
- tg.TextLoss — 用LLM生成的自然语言反馈作为损失函数
- Textual Gradient(文本梯度) — backward engine对每个节点生成的、回答“这个输入该怎么改才能改善输出”的自然语言批评文本
- .backward() — 触发基于文本梯度的反向传播
- tg.TGD (Textual Gradient Descent) — 文本梯度下降优化器,类比PyTorch的SGD,执行 Prompt_new = TGD.step(Prompt, ∂Evaluation/∂Prompt)
- .step() — 用计算出的文本梯度更新变量/参数
- tg.set_backward_engine() — 配置用于计算梯度的LLM引擎

**存活状态**: 存活且有影响力;论文2024年6月(arXiv 2406.07496),2025年3月正式发表于Nature;GitHub zou-group/textgrad约3.6k star,最新release v0.1.6(2024-12),仍被引用为「AutoDiff via Text」范式代表

**适配判断**: 把「审阅意见」形式化为对某个节点的『文本梯度』(即“这版该怎么改”)、把版本演进形式化为按文本梯度做的.step()更新,这套计算图+反向传播的隐喻可以直接借来描述我们『版本间连线挂设计规范』——规范本质上就是一条边上沉淀的文本梯度;但TextGrad面向的是单一目标函数优化,不天然支持我们需要的多分支/多版本并存探索

**来源**: https://arxiv.org/abs/2406.07496 · https://github.com/zou-group/textgrad · https://arxiv.org/pdf/2412.03624


### DSPy 优化器(含 GEPA) 〔tool〕

- 共6个主要优化器+2个核心抽象
- Signature — 声明式定义LLM模块的输入输出契约
- Module — 可组合的LLM调用单元
- teleprompter (dspy.teleprompt) — 优化器所在的命名空间/基类概念
- BootstrapFewShot — 自动生成并挑选few-shot示例注入prompt
- COPRO — 用坐标上升法生成并迭代打磨每步的instruction(指令文本)
- MIPROv2 — 用贝叶斯优化同时优化instruction与few-shot示例
- BootstrapFewShotWithRandomSearch — 多次运行BootstrapFewShot并随机搜索,挑选过程中最佳程序
- GEPA (Genetic-Pareto) — 反思式提示进化:让LLM反思结构化执行轨迹(输入/输出/失败/反馈),针对目标模块提出新指令文本,并在自身尝试的帕累托前沿上组合互补经验

**存活状态**: 高度存活;DSPy GitHub stanfordnlp/dspy约35.8k star,2026年5月仍发3.2.1版本;GEPA论文2025年7月(arXiv 2507.19457)ICLR 2026 oral,已集成为dspy.GEPA且独立发布pip包gepa;GEPA用35倍更少rollout跑赢MIPROv2 13%、跑赢GRPO(强化学习)20%,是明文/反思优化优于参数化RL优化的最强量化证据

**适配判断**: GEPA的『反思结构化执行轨迹→提出新指令文本→帕累托前沿组合互补经验』直接对应我们『评审意见→修订设计规范→多版本并存挑优』的诉求,且它是目前唯一给出『明文反思式优化 vs 强化学习/微调』量化对比(35倍效率、13-20%效果差)的系统,是回答用户『规模边界』问题最直接的一手证据

**来源**: https://github.com/stanfordnlp/dspy · https://arxiv.org/abs/2507.19457 · https://dspy.ai/api/optimizers/GEPA/overview/


### Constitutional AI / Claude's Constitution 〔methodology〕

- 共2阶段训练流程+1个转向
- principles(constitution) — 一组用自然语言写成的规则/原则集合,供模型自我评判用
- critique — 模型依据宪法原则对自己回答做自我批评
- revision — 依据critique对回答做自我修订
- SL阶段(supervised phase) — 采样初始模型→生成self-critique+revision→用修订后回答微调原模型
- RLAIF(RL phase) — 采样微调后模型的多个候选→模型依宪法评判哪个更优→训练偏好/奖励模型→用AI偏好标签替代人类偏好标签做强化学习
- Collective Constitutional AI(CCAI) — 用Polis平台汇聚约1000名公众意见,产出'Public constitution'与内部版本对比训练
- 2026新转向:从『standalone principles(独立原则罗列)』转向『解释为什么(explain why)』——不满足于规定做什么,要让模型理解原则背后理由以便泛化到新情境

**存活状态**: 高度存活且持续演进;原始论文2022年12月(Anthropic),CCAI论文2024年发表于ACM FAccT;2026年1月22日发布全新『Claude's Constitution』(从2023版约2700词扩展到23000词,约80页),以CC0协议公开,并声明当前Claude模型训练在用它构造多种合成训练数据

**适配判断**: 这是『把人类(或公众)裁决沉淀为明文规则再喂回训练』这条路径里跑得最久、规模最大的真实案例,其2026年的转向本身就是给我们的强信号:纯粹罗列独立规则(类似我们最初设想的『裁决→规则条目』)会遇到泛化天花板,需要保留『为什么』的叙事/理由而非只留结论性规则条目——这直接约束我们明文规则库的记录粒度,不能只记『可/否』要记『理由』

**来源**: https://www.anthropic.com/research/constitutional-ai-harmlessness-from-ai-feedback · https://www.anthropic.com/constitution · https://www.anthropic.com/news/collective-constitutional-ai-aligning-a-language-model-with-public-input


### Self-Discover 〔paper〕

- 共39个atomic reasoning module(原子推理模块,列于论文Table 2/附录A)+3阶段流程,以下为完整39条逐字
- 1. How could I devise an experiment to help solve that problem?
- 2. Make a list of ideas for solving this problem, and apply them one by one to the problem to see if any progress can be made.
- 3. How could I measure progress on this problem?
- 4. How can I simplify the problem so that it is easier to solve?
- 5. What are the key assumptions underlying this problem?
- 6. What are the potential risks and drawbacks of each solution?
- 7. What are the alternative perspectives or viewpoints on this problem?
- 8. What are the long-term implications of this problem and its solutions?
- 9. How can I break down this problem into smaller, more manageable parts?
- 10. Critical Thinking: analyzing problems from different perspectives, questioning assumptions, and evaluating available evidence
- 11. Creative thinking: generating innovative and unconventional ideas to solve problems
- 12. Seek input and collaboration from others to solve the problem
- 13. Use systems thinking: considering problems as part of larger interconnected systems
- 14. Use Risk Analysis: evaluating potential risks, uncertainties, and tradeoffs
- 15. Use Reflective Thinking: stepping back for introspection and self-reflection
- 16. What is the core issue or problem that needs to be addressed?
- 17. What are the underlying causes or factors contributing to the problem?
- 18. Are there any potential solutions or strategies that have been tried before?
- 19. What are the potential obstacles or challenges that might arise?
- 20. Are there any relevant data or information that can provide insights into the problem?
- 21. Are there any stakeholders or individuals directly affected by the problem?
- 22. What resources are needed to tackle the problem effectively?
- 23. How can progress or success in solving the problem be measured or evaluated?
- 24. What indicators or metrics can be used?
- 25. Is the problem a technical or practical one requiring specific expertise?
- 26. Does the problem involve a physical constraint?
- 27. Is the problem related to human behavior, such as a social, cultural, or psychological issue?
- 28. Does the problem involve decision-making or planning under uncertainty?
- 29. Is the problem an analytical one requiring data analysis, modeling, or optimization?
- 30. Is the problem a design challenge requiring creative solutions and innovation?
- 31. Does the problem require addressing systemic or structural issues?
- 32. Is the problem time-sensitive or urgent?
- 33. What kinds of solution typically are produced for this kind of problem specification?
- 34. Given the current best solution, guess about other possible solutions
- 35. Imagine the current best solution is totally wrong; what other ways exist to think about it?
- 36. What is the best way to modify the current best solution?
- 37. Create an entirely new solution to the problem, ignoring current approaches
- 38. Let's think step by step.
- 39. Let's make a step by step plan and implement it with good notation and explanation.
- SELECT — 第一阶段,依据任务示例从39模块库中选出有用的推理模块
- ADAPT — 第二阶段,把选中的每个模块针对当前任务做定制化改写
- IMPLEMENT — 第三阶段,把改写后的模块具体化为可执行的推理结构,并给出每步该生成什么的具体指令

**存活状态**: 存活;论文2024年2月(arXiv 2402.03620),NeurIPS 2024录用,由Google DeepMind团队(Pei Zhou等,含Denny Zhou/Quoc Le)发表;在BigBench-Hard等推理基准上相对CoT提升最高32%,是『组合式选用推理模块』这一思路的代表作,持续被2025年后续论文(如探讨SELECT/ADAPT/IMPLEMENT结构效果的follow-up)引用对比

**适配判断**: 39个原子模块本质是一份『可复用的推理动作词表』,SELECT/ADAPT/IMPLEMENT三段式与我们要求的『决策阶段动词表』结构目标几乎一致(选择合适动词→按上下文适配→落地执行),可作为『动词表如何被系统化组合成结构』的直接参照范式,但其模块偏认知/推理策略而非我们需要的『设计裁决动作』(问题拆分/反证/联想/推导等),需要按我们的领域重新定制条目而非照搬

**来源**: https://arxiv.org/abs/2402.03620 · https://ar5iv.labs.arxiv.org/html/2402.03620 · https://deepmind.google/research/publications/64816/


### Buffer of Thoughts (BoT) 〔paper〕

- 共3个核心组件
- meta-buffer — 存储从各类任务解题过程中蒸馏出的一系列高层级思维,即thought-template
- thought-template — 针对某类问题的思维模板,检索后可自适应实例化为具体推理结构
- buffer-manager — 动态更新meta-buffer,随着解决任务增多持续增强meta-buffer的能力

**存活状态**: 存活;论文2024年6月(arXiv 2406.04271),NeurIPS 2024录用;在10个高难度推理任务上相对SOTA有提升(如Game of 24提升11%、Checkmate-in-One提升51%),是『模板化思维复用』这条子线的代表工作,但公开引用量/后续跟进不如Reflexion/Voyager/DSPy密集

**适配判断**: meta-buffer+thought-template+buffer-manager三件套,直接对应『规则库+规则条目+规则库维护逻辑』这一结构骨架,可作为我们『明文规则库该有哪几个必要角色』的最简参照(存储层/条目粒度/更新机制三分离),但论文聚焦数学推理题,没有涉及主观裁决或多方审阅场景

**来源**: https://arxiv.org/abs/2406.04271 · https://huggingface.co/papers/2406.04271


### AutoRule / Rule-Based Rewards (RBR) 〔paper〕

- 共3阶段抽取流程(AutoRule)+RBR核心机制
- AutoRule第一步 — 用推理模型对一对模型输出+偏好标签生成逐步理由(step-by-step justification)
- AutoRule第二步 — 从该推理链中识别候选规则(candidate rules)
- AutoRule第三步 — 将候选规则综合为统一规则集(unified rule set)
- rule-based reward — 用LM验证器计算某输出满足规则集的比例,作为辅助奖励与学习到的奖励模型一起用于策略优化(PPO)
- RBR(OpenAI) — 把安全策略拆解为细粒度、可由LLM打分的命题(propositions),用固定LLM按显式规则打分生成奖励信号,只需极少人工标定

**存活状态**: 存活但较新,采用证据有限;AutoRule论文2025年6月(arXiv 2506.15651);RBR为OpenAI 2024年发布方法(用于o1系模型安全对齐);两者都还在被后续2025-2026年『rule extraction from feedback』论文(如RIMRULE、IDEA)引用比较,尚未看到大规模独立第三方生产部署证据,更多停留在论文/内部对齐管线阶段

**适配判断**: AutoRule的三段抽取流程(理由生成→候选规则识别→综合规则集)是目前查到的、最直接对应『把审阅意见的理由自动提炼成规则条目再综合』这一诉求的现成算子链,可作为我们『评审意见→规则库』自动化环节的候选算法框架;但它服务于RLHF奖励塑形,落地场景是训练信号而非我们要的『决策树上可读的裁决规则』,迁移时需要把奖励计算换成检索式引用

**来源**: https://arxiv.org/abs/2506.15651 · https://openai.com/index/improving-model-safety-behavior-with-rule-based-rewards/


**领域死因教训**: 共同死因/教训可归纳为三类:(1) 独立规则罗列在规模变大后失去泛化力——Constitutional AI从2023版『一组独立原则』2026年被Anthropic官方否定,转向必须保留『为什么』的叙事式治理,直接说明纯规则条目列表这条路会走到瓶颈;(2) 规则/insight数量与质量不同步会互相伤害——ExpeL明确报告『把反思也纳入insight生成过程反而损害性能』,提示不设质量门槛的无脑累加会污染规则库;(3) 尚未有系统给出『明文沉淀vs微调』的正面收益边界对照实验——多数论文(ExpeL、Voyager)只论证『不用微调也能改进』,不比较『微调是否更好』;唯一给出量化优势的是GEPA(反思式文本进化35倍更省rollout、效果比GRPO强化学习高20%),但这是prompt/程序优化场景而非纯知识规则场景,不能直接外推到『裁决规则库』这种场景的规模边界。整体上没有一个体系被『废弃』——Reflexion/Voyager/ExpeL/AWM/DSPy/TextGrad/Constitutional AI全部仍在被引用或迭代,唯一算『被淘汰』的是DSPy内部旧优化器(如COPRO)正被GEPA这类更高效的反思式方法边缘化,但仍留在库里未被移除。


**未覆盖/存疑**: (1) 未找到"过去两年内明确被废弃"的明文沉淀体系案例作反例对照,不确定这是真的没有失败案例还是搜索覆盖不足;(2) "明文沉淀 vs 微调"的直接头对头量化对比研究(相同任务、相同基座,一组用规则库注入、一组用LoRA/全量微调)未查到专门论文,只能用GEPA(vs GRPO强化学习)和一般性ICL-vs-finetuning综述做间接推论,这条待专门再查一轮;(3) AutoRule/RBR在生产环境(而非论文benchmark)的采用证据薄弱,只有OpenAI一处官方博客,需要进一步查是否已用于GPT正式版对齐管线;(4) Self-Discover 39条模块首次抓取PDF时因二进制解析失败改用ar5iv HTML二次抓取,已补全全部39条逐字,但未做第三方交叉核对,建议如需精确引用时对照NeurIPS官方PDF复核。


## 附录·开源工具与工程实践

开源工程实践里,"决策记录"和"关系类型化"两件事从未在同一个系统里被做重过——工程界的真实答案是:结构化决策文档(ADR/MADR)用最多6-8个字段的模板,配合极简的自由文本双向链接短语(不是枚举);产物间关系类型化(Jira/Linear/GitHub)全部收敛到3-4个动词(blocks/relates to/duplicates,加GitHub 2025-2026新增的纯"blocked by/blocking"依赖关系),且都允许自定义但实践中很少真正扩展;论证图/画布类工具(JSON Canvas、React Flow生态)把"边"做得很轻——要么无type只有label(JSON Canvas)、要么完全交给应用层自定义(React Flow),没有一个通用画布规范内置"反证/推导/延伸"这类语义动词;真正做过复杂论证分类学的独立产品(Compendium的IBIS本体、DebateGraph)要么已停止实质开发要么长期停留在教育/辩论场景未破圈到工程决策记录;OpenLineage 证明了"事件+facet可扩展元数据"模式在数据世界的成功但它解决的是血缘(who-produced-what)不是决策语义(why),它的核心动词其实只有 START/RUNNING/COMPLETE/ABORT/FAIL/OTHER 六个运行状态,不是决策动词表。给我们的启示:词表要小(工程界公认活得好的都在3-8项之间)、边类型宁可先做成自由文本label+受控词表校验,而不是重枚举系统绑定单一工具死。


### ADR (Nygard格式) + adr-tools 〔tool〕

- 共2类字段(Nygard原始格式,非枚举,纯Markdown章节标题):
- Status — 决策当前状态(自由文本,常见取值 proposed/accepted/deprecated/superseded,但规范不强制枚举)
- Context — 促成这个决策的背景和作用力描述
- Decision — 采取的应对方案陈述
- Consequences — 该决策产生的结果(正面和负面)
- 链接短语机制(非固定动词,由用户在命令行自定义正反两个短语字符串,如: 'Supersedes'/'Superseded by'、'Amends'/'Amended by'、'Partially obsoletes'/'Partially obsoleted by')

**存活状态**: adr-tools(npryce/adr-tools)最后一次正式发布是 3.0.0(2018-07-25),之后无新发布,处于事实性休眠但未标记 archived;README/命令行工具本身几年未变但因规范极简(bash脚本生成纯Markdown文件、无服务端依赖),生态里持续有三方重写延续使用,如 Rust 版 adrs(joshrotenberg/adrs)、npm 包 @meza/adr-tools;GitHub 显示 5.5k stars / 633 forks,star数至今仍在增长说明仍被新项目采纳(常见做法是团队直接拿shell脚本抄进自己仓库而非依赖上游更新)

**适配判断**: link机制是「自由文本双向短语对」而非受控枚举,证明工程界能接受的最小可用版本是不预设语义动词表、把动词留给用户当次命名;可以作为我们词表'非必须做成硬枚举也能用'的对照组,但同时是我们要避免的反例——正因为不统一,不同ADR仓库间的链接短语完全不可比较、无法做统计或算子处理,这正是用户要我们做'足够小且稳定统一词表'要解决的问题。

**来源**: https://github.com/npryce/adr-tools · https://github.com/npryce/adr-tools/blob/master/src/adr-new · https://github.com/npryce/adr-tools/releases


### MADR (Markdown Architectural Decision Records) 〔standard〕

- 共14个模板字段(v4.0.0 完整版 adr-template.md,逐字顺序如下):
- status — 决策状态元数据(YAML frontmatter字段)
- date — 决策日期元数据
- decision-makers — 参与决策的人员列表
- consulted — 被咨询但非决策者的人员
- informed — 决策后需被告知的人员
- {short title, representative of solved problem and found solution} — 标题占位符
- Context and Problem Statement — 上下文与问题陈述
- Decision Drivers — 决策驱动因素/考量标准
- Considered Options — 被考虑过的候选方案列表
- Decision Outcome — 最终决策结果及理由
- Consequences — 决策后果(正负面)
- Confirmation — 如何确认该决策被正确实施
- Pros and Cons of the Options — 各候选方案的优缺点逐项分析
- More Information — 补充信息/参考链接

**存活状态**: 活跃且是当前ADR事实标准模板,最新版本 4.0.0 发布于 2024-09-17,GitHub star 2.3k;是 log4brains 的默认模板;仍在被引用于学术论文(Kopp/Armbruster/Zimmermann 2018 ZEUS会议论文提出格式与工具支持);社区维护(adr组织下,非单一开发者),截至查证时点仍持续接受更新

**适配判断**: MADR证明成熟决策文档模板会自然收敛到10-14个字段规模,且区分'决策者/被咨询者/被告知者'三种人员参与角色(decision-makers/consulted/informed)——这对我们"审阅意见挂在版本旁"的设计有直接参考价值:谁做决策、谁被咨询、谁只是被通知,是我们标注审阅意见时可直接借用的三分类,而不必自造。

**来源**: https://github.com/adr/madr · https://raw.githubusercontent.com/adr/madr/main/template/adr-template.md · https://adr.github.io/madr/


### log4brains 〔tool〕

- 共1类(不定义自己的词表,只是MADR的发布/浏览工具层):
- 无新增动词或类型,默认套用 MADR 的 status/字段体系,额外提供的是'发布为静态网站'和'IDE内记录'两个工程能力,不涉及语义分类

**存活状态**: 仍存活但增长缓慢:GitHub 1.4k星左右(不同信源分别显示1448/1292),最新版本 1.1.0 发布于约2024-12-17,npm显示0个项目将其声明为依赖,说明使用方式主要是CLI直接安装非库引用;作者 thomvaill 维护,非大型基金会背景

**适配判断**: 验证了'ADR生态里工具层可以很薄、语义层留给MADR模板'的分工模式;对我们的启示是决策记录的'词表标准'和'记录/发布工具'应该分离,词表要独立稳定,工具可以换。

**来源**: https://github.com/thomvaill/log4brains · https://www.npmjs.com/package/log4brains · https://jamstack.org/generators/log4brains/


### JSON Canvas (Obsidian开源画布格式) 〔standard〕

- 共4种节点类型+边的6个字段(spec 1.0,逐字):
- 节点通用字段: id(必需,节点唯一ID)、type(必需,取值 text/file/link/group 四选一)、x/y(必需,像素坐标)、width/height(必需,像素尺寸)、color(可选)
- text型节点专属字段: text(必需,纯文本+Markdown语法内容)
- file型节点专属字段: file(必需,系统内文件路径)、subpath(可选,总以#开头)
- link型节点专属字段: url(必需)
- group型节点专属字段: label(可选,分组标签)、background(可选,背景图路径)、backgroundStyle(可选,取值 cover/ratio/repeat)
- 边(edge)字段: id(必需,边唯一ID)、fromNode(必需,起点节点id)、fromSide(可选,取值 top/right/bottom/left)、fromEnd(可选,取值 none/arrow,默认none)、toNode(必需,终点节点id)、toSide(可选,取值同fromSide)、toEnd(可选,取值同fromEnd,默认arrow)、color(可选)、label(可选,边的文本标签,自由字符串)
- ⚠边没有 type 字段,边的语义完全靠自由文本 label 表达,规范层不提供任何预置边类型枚举

**存活状态**: 活跃且持续扩张中,由 Obsidian 团队于2024年发布并开源(jsoncanvas.org独立站点+规范仓库),已有 Obsidian、Kinopio、Flowchart Fun、hi-canvas、OrgPad、Charkoal 等多个第三方应用实现导入导出互通;规范维护在 github.com/obsidianmd/jsoncanvas,仍在接受issue讨论(如#7 Diagram Types、#10 JSON Schema)

**适配判断**: 这是对我们最直接的负面参照:目前工程界唯一有多应用互通生态的开源画布格式,明确选择不给边设type字段,只给自由文本label——如果我们要在画布类产物(版本轨迹图)上做'挂设计规范/审阅意见'的边,JSON Canvas本身不提供任何'反证/推导/延伸'式的边类型机制,需要我们在其label字段基础上自建受控词表约定,或者放弃JSON Canvas作为存储格式改用React Flow自定义edge.data.type。

**来源**: https://github.com/obsidianmd/jsoncanvas · https://github.com/obsidianmd/jsoncanvas/blob/main/spec/1.0.md · https://obsidian.md/blog/json-canvas/ · https://kinopio.club/blog/posts/json-canvas/


### React Flow / xyflow 〔tool〕

- 共0个预定义领域词表(库本身不内置任何决策/论证语义):
- 库层只提供技术性节点/边抽象: Node(id/position/data/type等)、Edge(id/source/target/type/label/data等),edge.type 和 edge.label 完全是自由字符串,由使用方自己定义语义(是否表示'反证'/'推导'等完全靠应用层约定)

**存活状态**: 活跃头部项目,已改名/统一到 xyflow 组织(React Flow + Svelte Flow 双实现),v12大版本(@xyflow/react)持续维护;生态里存在第三方'数据血缘'方向组件如 flow-lineage(github.com/ollionorg/flow-lineage,npm包 @cldcvr/flow-lineage),但未找到一个开箱即用的'版本轨迹/血缘图应用'成品——都是拿库自建

**适配判断**: 确认了要用React Flow画版本轨迹图,词表和边类型系统必须我们自己定义并注入 edge.data,库不提供任何现成本体;flow-lineage 是最接近'血缘图'语义的三方组件但仍是通用DAG展示,没有语义化决策动词,不能直接套用只能借UI交互形态。

**来源**: https://github.com/xyflow/xyflow · https://reactflow.dev/showcase · https://github.com/ollionorg/flow-lineage · https://www.npmjs.com/package/@cldcvr/flow-lineage


### OpenLineage 〔standard〕

- 共3种事件类型(顶层Event): RunEvent(描述一次job运行的观测状态)、DatasetEvent(描述数据集本身的静态变更)、JobEvent(描述job配置/定义的变更)
- RunEvent.eventType 枚举共6个值(逐字): START、RUNNING、COMPLETE、ABORT、FAIL、OTHER(规范要求每次run至少1个START+1个COMPLETE/ABORT/FAIL,OTHER用于同一run内的补充事件)
- Facet(元数据挂载单元)机制: facet是挂在Run/Job/Dataset三种核心实体上的'原子化元数据片段',标准facet举例(逐字命名): nominalTime、parent、errorMessage(Run级)、sourceCodeLocation、sourceCode、sql、ownership(Job级)、schema、dataSource、lifecycleStateChange、version、columnLineage、ownership(Dataset级)、dataQualityMetrics、dataQualityAssertions、inputStatistics(输入数据集级)、outputStatistics(输出数据集级)
- 自定义facet规则: 必须用项目专属前缀避免命名冲突,且必须带 _schemaURL 字段指向对应版本的facet JSON Schema

**存活状态**: 活跃且建制化程度最高:2023年9月从Linux Foundation AI & Data 项目毕业(graduation,LF治理体系里的最高成熟度认证),核心集成方包括 Apache Airflow(官方Provider)、Apache Spark(含列级血缘)、Apache Flink、dbt、Dagster;Marquez是其官方参考实现;2025-2026持续有新采用方(如Datadog成为新的lineage消费方、Booking.com工程师成为新committer)

**适配判断**: OpenLineage的核心创新不是动词表(它的eventType只是6个运行状态,不是语义决策动词),而是facet可扩展元数据机制——'核心事件模型稳定不变+领域元数据通过带命名空间前缀和schemaURL的facet无限扩展'这个架构模式,直接对应我们'词表要小且稳定但要支撑未来明文规则积累'的需求:我们的决策边可以借鉴'边类型词表小而稳(类似eventType)+每条边可挂可扩展的facet(存具体审阅意见/设计规范引用/明文规则)'这个两层结构,而不是把所有语义都塞进一个动词里。

**来源**: https://github.com/OpenLineage/OpenLineage · https://github.com/OpenLineage/OpenLineage/blob/main/spec/OpenLineage.md · https://lfaidata.foundation/blog/2023/09/20/lf-ai-data-foundation-announces-graduation-of-openlineage-project/


### Jira Issue Links 〔tool〕

- 共4对(8个,每对正反双向,逐字): relates to / relates to(双向对称)、duplicates / is duplicated by、blocks / is blocked by、clones / is cloned by
- 管理员可通过'Add new link type'自定义新增类型,需配置: 类型名称(Name)、正向描述(Outward link description)、反向描述(Inward link description)三个字段

**存活状态**: 活跃,是Jira Cloud/Data Center当前默认开箱配置(2026年现行文档确认),Atlassian官方持续维护文档;这4类是绝大多数团队实际使用的全部集合,自定义功能存在但实践中低使用率(未找到大规模'企业自定义出十几种link type'的案例报道)

**适配判断**: 这是工程界'产物间关系动词'最大规模真实存活的样本:4对关系覆盖了绝大多数团队的全部需求,没人真正把它扩展到十几种;强烈支持用户'词表要小'的目标——如果我们的决策边类型词表超过个位数量级,就已经偏离了工程界经过十几年验证的'够用规模'。

**来源**: https://confluence.atlassian.com/adminjiraserver/configuring-issue-linking-938847862.html


### Linear Issue Relations 〔tool〕

- 共4个(逐字): Blocked(被阻塞)、Blocking(阻塞他人)、Related(一般关联)、Duplicate(重复)
- 附加机制: 在描述或评论中直接@引用其他issue会自动生成Related关联,无需手动操作

**存活状态**: 活跃,是Linear当前产品的标准功能(2020-03-11 changelog上线后持续至今为核心功能),Linear是当前工程界増长最快的问题跟踪工具之一,该功能未见被废弃或大改

**适配判断**: 和Jira高度一致地收敛到4个动词,且更进一步把'blocks/blocked by'处理成同一关系的两个视角而非用户手动选择方向——这提示我们的词表设计也应考虑'一个语义关系,由系统自动生成正反两个视角的呈现',而不是要求人工每次都选'正确方向'。

**来源**: https://linear.app/docs/issue-relations · https://linear.app/changelog/2020-03-11-issue-relations


### GitHub Issue Dependencies / Duplicate Detection 〔tool〕

- 共2类关系(2025-2026新功能,逐字): blocked by / blocking(一对双向依赖关系,2025-08-21上线);Duplicate of(重复标记,通过评论关键字'Duplicate of #N'触发,而非结构化字段)
- 2026-06-10更新: gh CLI v2.94.0 新增 --blocked-by / --blocking 参数及对应的 --add-* / --remove-* 变体用于命令行操作依赖关系
- 2026-06-18更新: Issue创建时的重复检测(Duplicate Detection)进入公开预览,自动在创建表单内联提示至多3条疑似重复项

**存活状态**: 活跃且是2025-2026 GitHub今年新加的功能(不是历史遗留),说明工程界仍在持续往'结构化issue关系'方向补课而非收缩;GitHub此前长期只有非结构化的'关键字触发关闭'(fixes/closes)+ 自由文本'Duplicate of'评论惯例,直到2025年才补上结构化的blocked-by/blocking

**适配判断**: GitHub作为体量最大的平台,2025年之前十几年都没有结构化关系类型(全靠自由文本'Duplicate of'和'Fixes #N'关键字),直到近两年才补上——这说明'结构化类型化关系'不是刚需门槛,轻量自由文本+关键字识别可以撑很久;但当规模真的到了GitHub级别,还是会走向结构化,佐证'先上轻量文本约定,视规模再收紧成正式词表'是合理路径。

**来源**: https://github.blog/changelog/2025-08-21-dependencies-on-issues/ · https://github.blog/changelog/2026-06-10-manage-sub-issues-types-and-dependencies-from-github-cli/ · https://github.blog/changelog/2026-06-18-duplicate-detection-and-issue-fields-mcp-support-for-github-issues/


### Kialo (论证图/结构化辩论平台) 〔tool〕

- 共2类核心关系(逐字,来自二手信源,官方页面404未能一手核实): Pro(支持论点)、Con(反对论点),挂在thesis(论题)下呈层级树结构
- 支撑机制: claim(论点)、vote(投票)、thesis(主论题)

**存活状态**: 活跃,非营利模式运营(由私人慈善基金会资助,免费无广告),官方教育版 Kialo Edu 持续更新(2026年多个第三方评测站点仍在收录);号称超百万用户,18000+公开辩论,但最近一次公开数字更新是2023年7月,近两年增长数据未见披露

**适配判断**: Kialo证明'只用Pro/Con二元动词+层级树'这种极简本体也能支撑百万级用户的真实论证协作,是我们词表'宁少勿多'的又一佐证;但它的场景是公开辩论非私有设计决策,层级树(非DAG)结构和我们要的多版本图连线场景不完全对应,可参考其'二元最小可用'思路但不能直接照搬其树形拓扑。

**来源**: https://en.wikipedia.org/wiki/Kialo · https://www.kialo-edu.com/ · https://www.kialo-edu.com/research


### DebateGraph 〔tool〕

- 未能一手核实其内部关系类型逐字定义(官方站点仍在线但当前功能文档未获取到,二手来源如Wikipedia、ResearchGate引用的多为2010-2016年旧描述,提及其支持多层次议题地图但未给出逐字动词表)

**存活状态**: 网站仍可访问(debategraph.org返回200,判定为技术存活),但最近一次可查证的实质性活跃证据(媒体报道/学术引用)止步于2016年前后;曾被白宫、英国外交部、CNN Amanpour栏目使用,但均为2010年代早期案例,近十年无新增采用案例证据

**适配判断**: 判定为'僵尸存活'(technically alive, functionally stagnant)——网站没关但生态和媒体关注度停滞在十年前,不建议作为设计参考的活样本,只能作为'论证图类独立产品长期缺乏迭代会怎样'的警示案例。

**来源**: https://en.wikipedia.org/wiki/Debategraph · https://debategraph.org/


### Compendium (IBIS本体论证图软件) 〔tool〕

- 核心为IBIS(Issue-Based Information System)本体,常见节点类型(逐字,源自软件历史文档,未能拿到当前版本一手界面截图核实完整枚举): Issue(议题)、Position(立场/方案)、Argument(论据,可为Pro或Con两种子类型)、Decision、Note、Reference
- 关系边逻辑上对应IBIS父子结构(Issue引出Position,Position被Argument支持或反对),但未查到该关系在软件内是否有独立的可配置'边类型'字段

**存活状态**: 已停止原始团队开发:源码于2009-01-13以LGPL开源,最后官方版本2.0发布于2013年3月,随后由社区通过 compendiumng.org 接手至今,但该社区分支近十余年未见重大版本更新或活跃报道;判定为死亡(核心开发死因=依赖单一学术机构Open University KMi的项目资助周期,资助结束后原团队解散,社区接盘缺乏持续动力)

**适配判断**: 死亡案例,但其IBIS本体(Issue/Position/Argument的Pro-Con二分)是学术界论证图长期公认的最小完备结构,可作为我们'问题拆分/反证/推导'词表的理论对照——尤其'Argument细分Pro/Con'与'反证'动词直接相关,值得在语义设计上参考,即使这个具体软件已死。

**来源**: https://en.wikipedia.org/wiki/Compendium_(software) · http://compendium.open.ac.uk/ · https://kmi.open.ac.uk/technologies/name/compendium


**领域死因教训**: 共同死因分三类:(1)重工具化的独立论证图应用(Compendium)——功能强但学习成本高、依赖单一原开发团队(NYNEX/Open University KMi),核心团队一停止投入,社区接管即名存实亡(compendiumng.org 社区分支近十余年无实质新版本);(2)命令行/规范类工具(adr-tools)——工具本身"做完了"就不再更新(2018年3.0.0后无发布),但因为规范极简(bash脚本+纯Markdown文件)反而"死而不僵",生态转为大量三方重写/兼容实现(Rust版adrs、log4brains、meza/adr-tools npm包等)接盘,规范存活但原实现休眠;(3)边/关系类型定义得越"重"(强类型enum、需要中心化服务器/账号)越难以被工程界日常采用——Jira/Linear/GitHub 都只保留4类基础关系(blocks/relates/duplicates/clones或同义)并允许自定义,没人在实践中用满一套复杂分类体系;反之,越"轻"(纯文本label、free-form链接短语)存活率越高,如 JSON Canvas 边只给 label 自由字符串不给 type enum、adr-tools 的链接短语是用户自定义"文本对"而非预置枚举。共性教训:决策/论证类"重语义边类型"体系如果依赖单一站点/单一维护者/强中心化存储,寿命明显短于"薄规范+纯文本文件+可被多实现重写"的体系。


**未覆盖/存疑**: 1. adr-tools 的 `adr link` 独立子命令是否存在存疑——实际实现是 `adr new -l "N:正向短语:反向短语"`,没有确认是否有单独 `adr link` 命令用于给已存在的两个ADR事后建链(未在README/命令列表里查到,可能需要看 src/ 全部脚本源码确认)。2. React Flow/xyflow 生态里没有找到一个"现成的版本轨迹/血缘图应用"产品(即拿来即用、非自己二次开发的),只确认了它是被用来自建这类应用的库,以及一个数据血缘方向的第三方组件 flow-lineage(ollionorg/cldcvr),该组件的真实使用规模和维护活跃度未核实(npm周下载量、star数未查)。3. Tana supertag 的边类型机制细节不足——只确认了"supertag=is-a、field=has-a"的定性区分,没有拿到 Tana 是否支持给两个节点间的引用关系打自定义类型标签(比如"支持/反对"这种语义关系)的逐字字段文档,Heptabase 的"whiteboard connection"是否有类型/标签机制完全没查到一手资料。4. Kialo 的官方"Pro/Con"逐字定义页面404,没有确认它在Pro/Con之外是否还有第三种关系(如"中立评论"或"impact"权重标注)的逐字机制,只能确认二元Pro/Con结构来自间接信源。5. DebateGraph 域名仍返回200(还活着),但没有查到最近(2024-2026)有实质使用案例或更新日志,只能算"僵尸存活"未核实是否真被使用。


## 附录·问题求解操作词表(跨领域经典)

六个体系里,存活到今天且仍在真实工作流里被动词驱动的只有证明助理的 tactic 语言(Lean/Rocq、Coq)——这是本次调研里"标准化动词驱动状态转换"的唯一活体范本,规模在实际高频使用层面收敛到十几个核心动词、扩展层可以到150+条(Rocq官方文档),核心与外围分层清晰。TRIZ 的40条发明原理仍在被引用和教学(尤其东亚,Samsung/三星、BAE Systems等有案例),但西方工业界公认在"学习成本高、条目冗余"上口碑下滑,有论文专门讨论"为什么TRIZ人气在下降"。Polya的启发式词表(analogy/decomposition/generalization-specialization/working backwards/auxiliary problem等)已经完全学术化/教材化,不再是"活体系"而是被后继工作(Schoenfeld、AI heuristic search史)引用的历史起点,严格意义上没有一个可数的官方"词表"边界(书里叫"heuristic dictionary"有67条词条,但没有一个规范化的精简版被广泛采用)。Bloom修订分类法(6大类19小类)是教育界事实标准,但一线教师普遍只用到"动词贴标签"层面(6大类),19个子过程很少被完整使用——即"大词表在教学实践中被自动裁剪回小词表"。PDDL/STRIPS 的算子模型不是一个动词词表而是一个只有2个基元(precondition、effect=add/delete)的语法框架,动词内容(pick-up、stack等)完全是领域自定义,这对我们最大的启示是:也许我们要的不是一张固定动词表,而是一个"动作schema"(名字+前置条件+效果)让人和AI往里填自定义动词。苏格拉底提问法官方原始文本(Paul & Elder)其实不是一个平的"6类"列表,而是一个"三层"分类法(元素/parts of thought 9项 + 推理质量标准/intellectual standards 若干项 + 六型问题是后人从中提炼的教学简化版),网上流传的"六种类型"是二次简化,不是原始最权威版本。


### Polya 问题求解启发式(How to Solve It) 〔methodology〕

- 共约67条(原书'Short Dictionary of Heuristic'词条数,二手引用,未逐条核实全部67条原文)——本次核实到的核心条目:
- Analogy(类比) — 找到与当前问题结构相似的已解决问题,借用其解法。
- Decomposing and Recombining(分解与重组) — 把问题拆成部分,再以新方式重新组合部分。
- Generalization(一般化) — 把问题的特殊情形推广到更一般的情形以获得更多解题信息。
- Specialization(特殊化) — 从一般情形聚焦到具体特例以获得可操作的切入点。
- Working Backwards(逆向工作法) — 从目标结论出发反推,直到连接到已知条件。
- Auxiliary Problem(辅助问题) — 找一个子问题,其解能帮助解出原问题。
- Auxiliary Elements(辅助元素) — 引入辅助的构造/图示/记号/中间目标帮助解题。
- Variation of the Problem(变换问题) — 改变问题的条件或提法以获得新联想。
- Induction(归纳) — 从若干具体例子归纳出一般规律。

**存活状态**: 作为独立、成建制的'词表体系'已经不是活体系——书本身是1945年的经典,内容被完全学术化、教材化,在数学教育/问题解决教学中长期作为背景常识引用,但没有一个组织在'维护/更新'这份词表,也没有权威的精简子集被广泛统一采用;它的思想被后继工作(如 Schoenfeld 的问题解决框架、AI heuristic search 早期史)当作起点消化吸收,而不是作为一套动词表被直接调用。

**适配判断**: 证据:核心动词(分解/类比/逆推/一般化/特殊化)与用户举例的'问题拆分/反证/联想/推导'高度同构,说明这是决策动词层的'祖先词汇',可作为语义校验参照,但不适合直接照搬——因为它没有配套的'边类型/节点类型/算子'形式化,只是一份自然语言启发式清单,且规模(67条)远超我们'足够小且稳定'的要求,需要人工精炼到个位数核心词才能用。

**来源**: https://press.princeton.edu/books/paperback/9780691164076/how-to-solve-it · https://gist.github.com/jph00/d60301884c56fe063101a7cc6193b3af · https://www.hlevkin.com/hlevkin/90MathPhysBioBooks/Math/Polya/George_Polya_How%20to%20Solve%20It.pdf(原书PDF,提取受阻,仅作来源标注)


### TRIZ 40条发明原理(40 Inventive Principles) 〔methodology〕

- 共40项(官方逐字清单,来自 triz40.com):
- 1. Segmentation(分割) — 把物体分成独立部分或使其可分。
- 2. Taking out(抽取) — 从物体中分离出有干扰作用或唯一必要的部分/属性。
- 3. Local quality(局部质量) — 让物体各部分承担不同、更适合各自局部工况的功能。
- 4. Asymmetry(不对称) — 用不对称形状替代对称形状。
- 5. Merging(合并) — 把相同或相关的物体/操作在空间或时间上合并。
- 6. Universality(多用性) — 让一个物体承担多种功能,从而省去其他物体。
- 7. Nested doll(嵌套) — 把一个物体放入另一个物体内部,层层嵌套。
- 8. Anti-weight(反重量) — 用与其他物体的相互作用来补偿物体的重量。
- 9. Preliminary anti-action(预先反作用) — 预先施加与最终有害效应相反的作用。
- 10. Preliminary action(预先作用) — 预先完成所需的变化(全部或部分)。
- 11. Beforehand cushioning(预先应急措施) — 预先准备应急手段以补偿物体可靠性不足。
- 12. Equipotentiality(等势性) — 改变工作条件使物体无需举起或放下。
- 13. The other way round(反向操作) — 用相反的动作/顺序/坐标系解决问题。
- 14. Spheroidality-Curvature(曲面化) — 用曲线/曲面部件替代直线/平面部件。
- 15. Dynamics(动态化) — 让物体或其环境的特性能自动调整以适应各阶段最佳工况。
- 16. Partial or excessive actions(部分或过量作用) — 如果难以百分百达到效果,就采用'略少'或'略多'来简化问题。
- 17. Another dimension(维数变化) — 把物体或系统移到另一维度(如从一维到二维/三维)。
- 18. Mechanical vibration(机械振动) — 使物体振动或利用共振。
- 19. Periodic action(周期性作用) — 用周期性/脉冲动作替代连续动作。
- 20. Continuity of useful action(有效作用的连续性) — 让物体的所有部分始终满负荷工作。
- 21. Skipping(跳过) — 高速通过有害或危险的工序阶段。
- 22. Blessing in disguise/Turn Lemons into Lemonade(变害为利) — 利用有害因素达成正面效果。
- 23. Feedback(反馈) — 引入反馈以改进过程或动作。
- 24. Intermediary(中介物) — 使用中间载体传递或转移动作。
- 25. Self-service(自服务) — 使物体具备自我服务、自我维护的功能。
- 26. Copying(复制) — 用简单廉价的复制品替代昂贵、易损或不便的物体。
- 27. Cheap short-living objects(廉价短寿命物件) — 用一组廉价物件替代昂贵物件,以牺牲某些特性(如寿命)换取成本。
- 28. Mechanics substitution(机械系统替代) — 用光学、声学、味觉、嗅觉等原理替代机械手段。
- 29. Pneumatics and hydraulics(气动与液压结构) — 用气体和液体部件替代固体部件。
- 30. Flexible shells and thin films(柔性壳体和薄膜) — 用柔性外壳和薄膜替代传统结构。
- 31. Porous materials(多孔材料) — 使物体变为多孔或添加多孔元件。
- 32. Colour changes(颜色改变) — 改变物体或环境的颜色/透明度。
- 33. Homogeneity(同质性) — 让与给定物体相互作用的物体由相同或性质相近的材料制成。
- 34. Discarding and recovering(抛弃与再生) — 使已完成功能的部件失效/溶解/抛弃,或在使用中直接再生恢复部件。
- 35. Parameter changes(参数变化) — 改变物体的物理状态、浓度、柔度、温度等参数。
- 36. Phase transitions(相变) — 利用相变过程中产生的效应(如体积变化、放热吸热等)。
- 37. Thermal expansion(热膨胀) — 利用材料的热膨胀或收缩。
- 38. Strong oxidants(强氧化剂) — 用富氧空气、纯氧甚至臭氧、离子化氧替代常规空气。
- 39. Inert atmosphere(惰性环境) — 用惰性环境替代正常环境,或添加中性部件/添加剂。
- 40. Composite materials(复合材料) — 用复合材料替代均质材料。

**存活状态**: 仍在被使用,但呈现明显的地域分化和口碑下滑。东亚(尤其韩国三星、日本企业)近十年加速采用,三星早期项目曾节省过亿美元;西方工业界(BAE Systems、GE、Mars等)有采用案例但整体'人气下降',有专门论文《Why TRIZ Popularity is Declining》讨论此现象,核心批评是学习/应用成本高(堪比考六西格玛黑带)、条目冗余(研究发现仅4条原理就能覆盖约60%的已发表解法案例,其余36条长尾利用率低)。2025年出现'TRIZ+ChatGPT/LLM'方向的复兴研究,结论是TRIZ仍有价值但AI是增强而非替代。

**适配判断**: 证据:40这个规模本身就是反面教材——论文明确指出仅4/40条原理覆盖了60%实际案例,说明条目数远超'常被用到'的核心子集,西方从业者的核心抱怨正是'条目太多记不住、不落地';这直接支撑'词表要小'的判断,但也提示可以借鉴TRIZ的形式——原理配合一张'矛盾矩阵'(用哪个原理解决哪类工程矛盾)做检索,而不是要求用户记住全表,这对我们做'节点类型 x 边类型'查找表可能有参考价值。

**来源**: https://www.triz40.com/aff_Principles_TRIZ.php · https://en.wikipedia.org/wiki/40_principles_of_invention · https://www.researchgate.net/publication/316940926_Why_TRIZ_Popularity_is_Declining · https://www.cambridge.org/core/journals/ai-edam/article/enhancing-triz-through-environmentbased-design-methodology-supported-by-a-large-language-model


### 证明助理 Tactic 语言(Lean 4 / Rocq(原Coq)) 〔tool〕

- Lean 4/Rocq 官方文档收录的完整tactic表规模为150+条(Rocq官方Tactic Reference页面实测163条含变体,Lean同源),核心高频动词约15-20个,逐字如下:
- intro / intros(引入) — 把目标里的全称量词或蕴含前提引入为上下文中的假设。
- apply(应用) — 把目标与某表达式的结论做合一,生成该表达式各前提对应的新目标。
- exact(准确给出) — 直接用一个类型完全匹配目标的表达式关闭目标。
- induction(归纳) — 对局部上下文中某归纳类型的变量做数学归纳,为每个构造子生成一个子目标并附带归纳假设。
- cases / destruct(分情况) — 对某归纳类型的项做案例分析,按构造子拆出对应子目标。
- rewrite / rw(重写) — 用一组等式证明,把目标里等式左边替换为右边。
- constructor(构造子) — 对归纳类型目标应用其构造子来完成构造。
- simp(化简) — 用注册过的化简引理自动化简目标。
- rfl(自反性) — 当目标是形如 a = a 的自反关系时直接关闭。
- assumption(用假设) — 用上下文里恰好匹配目标类型的假设直接关闭目标。
- contradiction(矛盾) — 当上下文中已存在互相矛盾的假设时直接关闭目标。
- exfalso(化归荒谬) — 把目标转换为 False,靠证明矛盾来完成原目标。
- specialize(特化) — 把某个全称量化的假设代入具体值,得到更具体的假设。
- generalize(一般化) — 把目标里出现的具体表达式替换为一个新的自由变量。
- obtain(析取获得) — 结合 have 和模式匹配,从存在性/合取假设里析出具体见证和子假设。
- refine(部分给出) — 类似exact但允许留下未解决的洞(_),把洞转成新目标。
- by_cases(分类讨论) — 对一个命题P做真假两种情况的分类讨论。
- omega(线性算术判定) — 对整数/自然数上的线性算术目标做自动判定。
- decide(可判定性判定) — 对可判定命题做可计算的自动求解。
- ext(外延性) — 应用标注为@[ext]的外延性引理(如函数相等归约为逐点相等)。

**存活状态**: 高度活跃,是本次调研里唯一真正'活体系'的标准化动词表,2025年3月Coq更名为Rocq(9.0版发布),原名带来的英文谐音尴尬是改名主因,tactic词表本身完整延续未受影响;Lean 4 的 Mathlib4 社区库(超百万行形式化数学)以及Lean官方每次发版都在扩展/整理tactic集,是当前最活跃的形式化数学生态之一,被数学界(如Fields奖得主参与的项目)和AI辅助证明研究广泛使用。

**适配判断**: 证据:这是'标准化动词驱动状态转换'里唯一被日常高频使用、且有强制反馈闭环(证明能否真的关闭目标是客观可判定的)的体系。规模呈两层结构——核心高频词表约15-20个(用户过一遍教程就能记住),外围专用tactic可扩展到150+(处理特定领域如线性算术、位向量、并发验证等)。这个'核心小、外围可插拔扩展'的结构,是我们词表设计最值得直接模仿的模式:不要一次性做扁平大表,而是做'核心决策动词≤10-15个 + 领域/场景可注册扩展动词'。

**来源**: https://rocq-prover.org/doc/V8.10.0/refman/proof-engine/tactics.html · https://lean-lang.org/doc/reference/latest/Tactic-Proofs/Tactic-Reference/ · https://rocq-prover.org/releases/9.0.0 · https://en.wikipedia.org/wiki/Rocq · https://github.com/leanprover-community/mathlib4


### PDDL / STRIPS 规划算子模型 〔standard〕

- 核心不是一张动词表,而是只有2个基元的算子语法框架(逐字,来自官方PDDL语法与STRIPS经典定义):
- action schema(动作模式) — 一个三元组:名字+参数列表、前置条件(precondition)、效果(effect),形式为 (:action <name> :parameters (<...>) :precondition (<...>) :effect (<...>))。
- precondition(前置条件) — 一个合取式(文字的合取),描述该动作可被应用时必须成立的世界状态。
- effect / add-list(添加效果) — 动作应用后应变为真的原子集合(STRIPS经典术语'add list')。
- delete-list(删除效果) — 动作应用后应变为假的原子集合(STRIPS经典术语'delete list';现代PDDL用 (not ...) 表达负效果,不再使用独立的delete-list概念)。
- 具体'动词'(如 pick-up、stack、unstack、move等)完全是领域自定义,PDDL/STRIPS官方本身不提供、不限定动词词表——这是与其他5个体系的关键区别。

**存活状态**: STRIPS(1971年提出)是历史起点,已被PDDL(1998年为首届国际规划竞赛IPC-1定义)取代为事实标准并持续扩展(PDDL2.1加时间、PDDL2.2加派生谓词、PDDL3加偏好等),至今仍是AI自动规划领域(含机器人任务规划、游戏AI行为规划)的通用交换格式,每年国际规划竞赛(ICAPS/IPC)仍在用,是6个体系里除Lean/Rocq外唯一同样活跃的。

**适配判断**: 证据:这是6个体系里给我们最大结构性启示的一个——它证明'决策动词表'不一定要是一份固定词汇表,可以是一个'算子schema'(名字+前置条件+效果),让每个具体决策动作(无论叫'问题拆分'还是'反证')都实例化同一个schema。这对应用户要的'节点类型/边类型/算子'三元结构非常贴合:边的语义可以统一用'前置状态谓词 + 后置状态谓词(增/删)'来记录,动词只是这个schema实例的名字标签,不需要动词词表本身做到穷尽,但每条边必须能填进这个骨架里,这样才能喂给'类蒙特卡洛'搜索(需要机器可执行的前置/效果谓词,而不只是人类可读的动词标签)。

**来源**: https://fai.cs.uni-saarland.de/teaching/winter18-19/planning-material/planning03-pddl-post-handout.pdf · https://www.cs.cmu.edu/afs/cs/project/jair/pub/volume28/coles07a-html/node14.html · http://www.inf.ed.ac.uk/teaching/courses/propm/papers/pddl.html


### 修订版 Bloom 认知过程分类(Anderson & Krathwohl, 2001) 〔standard〕

- 6大类(逐字,来自Anderson & Krathwohl 2001官方分类,一手/权威转述源已确认):
- 1. Remembering(记忆) — 从长时记忆中识别或提取相关知识;下辖 recognizing(识别)、recalling(提取)。
- 2. Understanding(理解) — 从口头、书面、图像信息中建构意义;下辖 interpreting(解释)、exemplifying(举例)、classifying(分类)、summarizing(概括)、inferring(推断)、comparing(比较)、explaining(说明)。
- 3. Applying(应用) — 在给定情境中执行或使用某个程序;下辖 executing(执行,用于熟悉任务)、implementing(实施,用于陌生任务)。
- 4. Analyzing(分析) — 把材料分解为组成部分,判断部分之间及部分与整体结构/目的的关系;下辖 differentiating(区分)、organizing(组织)、attributing(归因)。
- 5. Evaluating(评价) — 依据准则和标准做判断;下辖 checking(核查)、critiquing(评判)。
- 6. Creating(创造) — 把要素组合成一个连贯或功能性的整体,重新组织要素形成新模式/结构;下辖 generating(生成)、planning(计划)、producing(产出)。
- 以上6大类下共展开19个具体认知过程(本次核实到6+2+2+3+2+3=18项逐字子项,官方标准数字为19项——本次未能拿到完全无误差的第19项对照,推测是Understand类下还有1项子过程未被本次转述稿完整列出,原始19项权威表建议直接查 Krathwohl 2002 论文 Table 1 核对)。

**存活状态**: 教育界事实标准,自2001年发布后被全球K12及高等教育课程设计、教学目标撰写广泛采用超过20年,至今仍是教学大纲/学习目标(learning objectives)撰写的主流参照框架;但学术界(见2022年PMC论文'Probing Internal Assumptions of the Revised Bloom's Taxonomy')有实证研究质疑其6个类别之间是否真的存在论文预设的层级递进关系,一线教学实践中普遍只用到6大类的动词贴标签层面,19个具体认知过程很少被完整对照使用。

**适配判断**: 证据:这是'规模与实践坍缩'现象最典型的例子——官方词表19项,但实际使用普遍坍缩回6大类。这提示我们即便设计出一份较大的完整决策动词表,也要预留'6-10个高层类别'作为默认展示层,19项级别的细粒度只在需要精确统计/复用规则时才展开,不能强制用户每次都从最细粒度选择。

**来源**: https://cmapspublic2.ihmc.us/rid=1Q2PTM7HL-26LTFBX-9YN8/Krathwohl%202002.pdf(原始来源,提取受阻) · https://quincycollege.edu/wp-content/uploads/Anderson-and-Krathwohl_Revised-Blooms-Taxonomy.pdf(Leslie Wilson转述稿,已读取) · https://pmc.ncbi.nlm.nih.gov/articles/PMC9727608/


### 苏格拉底提问法标准化问题类型表(Paul & Elder) 〔methodology〕

- 流传最广的二次简化版'六种类型'(逐字,来自Paul原始分类的教学简化版,多个二手教学资源一致引用):
- 1. Questions of Clarification(澄清型问题) — 追问某个说法/概念到底是什么意思,要求进一步说明或举例。
- 2. Questions that Probe Assumptions(探查假设型问题) — 追问说话者不言自明地默认了什么前提。
- 3. Questions that Probe Reasons and Evidence(探查理由与证据型问题) — 追问支撑某个结论的依据和证据是什么。
- 4. Questions about Viewpoints or Perspectives(视角/立场型问题) — 追问是否存在其他看待同一问题的方式。
- 5. Questions that Probe Implications and Consequences(探查含义与后果型问题) — 追问如果这个结论成立,会推出什么、会导致什么后果。
- 6. Questions about the Question(元问题) — 追问这个问题本身的目的是什么、为什么要问这个问题。
- 原始一手来源(Paul & Elder《The Art of Socratic Questioning》官方指南)实际是更细的三层分类,本次已读取原文确认:第一层'Questions that Target the Parts of Thinking'(针对思维的组成要素,含purpose目的、information信息、inference/interpretation推论与解释、concepts概念、assumptions假设、implications/consequences含义与后果、point of view视角、question at issue待决问题、context情境,共约9项);第二层'Questions that Target the Quality of Reasoning'(针对推理质量,对应clarity清晰、accuracy准确、precision精确、relevance相关、depth深度、breadth广度、logic逻辑、significance重要性、fairness公正等'智识标准'若干项)。流传的'六种类型'是从这两层里教学简化提炼出的通俗版,并非原书唯一或最权威的呈现形式。

**存活状态**: 作为批判性思维教学法持续活跃,Foundation for Critical Thinking(criticalthinking.org)至今仍在维护和销售这套材料(2006年版指南仍在售),被广泛用于中小学到大学的批判性思维课程、企业培训(如敏捷教练用其做coaching追问)、心理咨询(苏格拉底式提问是CBT认知行为疗法的核心技术之一);未见'死亡'证据,但网络上流传的'六类型'版本相对原始三层分类是被大幅简化和降级传播的,一手最权威版本反而知名度较低。

**适配判断**: 证据:这个体系给我们的最大提醒是'防止版本失真'——网络广泛流传的简化'六类型'不是作者最权威的呈现,原始文本其实是'思维要素(9项)+推理质量标准(若干项)'两层结构,和我们要设计的'节点类型 vs 边类型'的两层区分(决策产物的组成部分 vs 决策转换的质量评判维度)有结构上的相似性,值得作为'评审意见挂靠维度'的参考模板,而不是直接作为'决策转换动词'来用——因为它本质是审问型问题分类,不是操作/转换动作分类,和Polya/TRIZ/tactic/PDDL四个体系的'动作导向'性质不同,更接近我们'审阅意见'那一挂载点而非'版本间连线'那一挂载点。

**来源**: https://www.criticalthinking.org/files/SocraticQuestioning2006.pdf(一手来源,已读取原文) · https://www.trigonweb.com/dowload/SOCRATIC%20QUESTIONS.pdf · https://www.jamesbowman.me/post/socratic-questions-infographic.pdf


**领域死因教训**: 跨六个体系比对,词表死亡/边缘化的共同原因收敛为三条:(1) 规模超出人类工作记忆容量且缺少使用频率分层——TRIZ 40条原理里前4条覆盖约60%已发表案例(Fey/Rivin统计),其余36条长尾使用率低,是西方企业弃用的直接技术原因(不是理念错,是条目数超过可现场记忆调用的阈值,与Miller 7±2 的经典发现方向一致,但本次调研未找到把"7±2"和"词表存活率"直接做相关性统计的论文——这是待补的空白,只能类比不能当结论引用)。(2) 无强制反馈闭环则会漂移成装饰性清单——TRIZ在西方的"学了拍照留念、不真正嵌入工作流"批评,与Bloom分类法"教师从未真正用满6类只用前2类"的批评同构:词表如果不绑定一个必须产出可验证结果的动作(证明义务/发明产出/教学评估),就会退化成墙报。(3) 活体系无一例外靠"小核心词表+可无限扩展的外围"两层结构续命——Lean/Coq tactic 词表表面上有150+条,但社区培训材料反复收敛到10-20个"核心tactic"(intro/apply/exact/induction/rewrite/simp/cases/constructor等),其余是特定问题域的自动化封装;PDDL的核心只有"precondition+effect(add/delete)"两个算子,复杂行为靠组合基础谓词而非扩充算子种类;Bloom 6大类之下才展开19个子过程,子过程本身不需要每次都被引用。这提示我们:决策动词层如果参照这些活体系,应该做成"核心动词≤10,子类型/参数可扩展"而不是一次性做一张扁平大表。另外Coq改名为Rocq(2025)提示:名字/品牌层的迁移不等于词表体系失败,tactic词表本身在改名后完整延续,这与"体系死亡"要严格区分。


**未覆盖/存疑**: 1) Bloom's 19个具体认知过程的逐字全表(每小类下的具体子项名称,如 Remember 下的 recognizing/recalling,Understand 下的7项等)没能拿到干净的官方逐字列表——两份PDF(Krathwohl 2002原文、quincycollege版)都因编码问题读取失败,只从Leslie Wilson整理稿里间接确认了6大类各自展开的子过程名(不完整,缺 Analyze/Evaluate/Create 各自完整子项数)。建议下一步直接读 Krathwohl 2002 "A Revision of Bloom's Taxonomy: An Overview" (Theory into Practice 41(4)) 的 Table 1,那里应该有完整19项对照表。2) Polya《How to Solve It》"Short Dictionary of Heuristic"官方逐字67条词条列表没能提取(原书PDF不可读),只确认了"67条"这个数字(来自二手引用)和5-6个最常被引用的核心词条,没有拿到67条的完整逐字清单,也没查到这67条里有没有一份被后人公认的"精简权威子集"。3) TRIZ "40 Principles" x "39 Engineering Parameters" 矛盾矩阵(Contradiction Matrix)本身没有细查——只查了40条原理清单,矩阵是TRIZ真正的"检索算法"部分,和我们"决策动词"关系可能比原理列表本身更大,值得追加一次调研。4) "规模与可用性关系"是否有人专门研究过,除了Miller 1956这篇经典心理学论文(讲短时记忆容量7±2,不是专门针对"方法论词表规模"的研究)之外,没有找到把"词表条目数"和"体系存活率/采纳率"直接做统计相关的论文——这是用户问题里明确要的东西,目前只能给出四个体系的规模数字加上从业者定性评价(TRIZ"太复杂""几十条记不住"、Bloom"教师只用6大类不用19小类"),没有量化研究。建议标记为未解决,如果需要可以另开一轮专门搜"vocabulary size" + "taxonomy adoption" + "cognitive load"的教育测量学/知识工程文献。5) Coq/Rocq tactic词表历史演化(从Coq V1到V8到Rocq 9.0,tactic数量是怎么从几十条涨到150+条的)只做了终点快照,没做纵向对比,如果要支持"演化"论证需要再查旧版本文档。


---

# 附录:本地历史调研盘点明细


### docs/plans/reasoning-network/[2026-05-15]explicit-hypothesis-reasoning-reboot/plan.md (2026-05-15)

- 覆盖: 明文化假设推理网络重启计划:把 agent 的假设/证明/证否/反例/依赖/联想/探索状态显式化,做成可计算可审计的推理网络。定义 Claim/Evidence/Argument 最小 schema(valid_under 适用条件、status 状态机、七种边)。提出 H0-H5 六条可证伪假设与五阶段渐进实验路线。
- 结论: 结论是研究纲领而非实现:给出完整理论框架和风险清单,但止步于 Phase 0(建档)。下一步写着先做 old_work_audit/schema_v0/离线trace重放,后两项未见完成证据。
- 缥缈在哪: 全篇是理论设计+文献综述,没有代码/schema真正落地(承诺的 tests/fixtures/reasoning_ledger/ 目录本次核实不存在)、五个实验Phase全部停在Phase0、六条假设无一被验证或证伪,是一份'该怎么想'的元讨论,没固化成可执行下一步。

### docs/plans/reasoning-network/[2026-05-15]explicit-hypothesis-reasoning-reboot/old_work_audit.md (2026-05-15)

- 覆盖: 审计四条更早线索:假设学习元理论、诊断重整、语义网络重设计、演化工作流假设黑板。列出'已被支持的局部方向'S1-S4和'不靠谱证据'F1-F5。
- 结论: 方向有局部价值苗头,但旧工作没形成成熟系统,核心问题是'局部有效证据被过早解释成系统成立'。列出可继承技术碎片表和需重做部分。
- 缥缈在哪: 是元审计而非实证结果,'审计'只是转述总结不是重新验证。文中列的'仍需单独做的归并'四项全是待办、未真正执行,结论停在方向性判断,没产出可复用假设清单或经验证发现。

### docs/plans/reasoning-network/[2026-05-15]explicit-hypothesis-reasoning-reboot/schema_traditions_survey.md 等三份格式调研 (2026-05-15)

- 覆盖: 极详尽成熟传统调研(约2500行):承载格式层、论证结构层(Toulmin/IBIS/Dung AF/ASPIC+/ABA/Carneades/AIF)、证据溯源层(PROV/RO-Crate/OpenLineage)、工程决策格式(ADR/MADR)、严格验证层(Lean/Coq/Isabelle/TLA+)、Assurance Case体系(GSN/CAE/SACM)。给出v0选型结论和完整目录结构。
- 结论: 选型结论明确、分层清楚,是这批里最系统的一份,但从未转化为实际代码或数据。
- 缥缈在哪: 调研扎实但零验证/零落地:承诺的 tests/fixtures/reasoning_ledger/ 目录、checker、v0 schema文件均未见任何后续踪迹。是一份'选好了要用什么'的报告,没有下一份文档接续说'真的做了、跑了、发现了什么'。

### docs/reports/reasoning-network/2026-05-15-explicit-fail-edge-asset-map.md (2026-05-15)

- 覆盖: 把旧假设H-034/H-038编号翻译成人类可读语义名,列出关键资产确切位置,提出'四层控制策略'和'编号是外键不是名字'的索引原则。
- 结论: 明确要求用语义名而非编号做索引,直接对应用户'搜索结果一直缥缈'的抱怨根源——历史材料大量用H-034/V18-V26这类无语义编号。
- 缥缈在哪: 本文档不缥缈,但它指向的底层资产本次核实确认从未被创建——地图画完了但地没建。

### docs/plans/[2026-06-18]DECISION-MEMORY/plan.md 与 decisions/DESIGN.md (2026-06-18)

- 覆盖: 把决策记录升级为个人决策记忆系统:后台LLM定期从对话总结决策→可读决策库→决策树→反向提炼规范。摘抄agent memory领域做法。定义三种kind(decision/belief/comment)+寻址(project+track)。
- 结论: 这条线是本批里少数真正落地并持续在跑的:schema三件套+统一库+召回+CLI已实装,records.jsonl实测3613行真实数据在用,extract_run.py抽取管线已跑批量。
- 缥缈在哪: 本身不算缥缈(有真实运行数据),但反向固化器(决策→规范候选)与网页浏览界面两块仍停在计划阶段未见代码——地基扎实但看不到最终产出实例,这正是体验为缥缈的部分。

### docs/plans/[2026-06-27]EXPLORATION-PATH-VIZ/plan.md 与 docs/research/2026-06-27-exploration-path-visualization.md (2026-06-27)

- 覆盖: 调研盘点LLM对话树/分支工具(Loom/tldraw/Sensecape/Tree of Thoughts)、设计理由框架(IBIS),结论是'视觉部分早被解决,真正空白是带理由的因果边,没有产品闭合因果边到决策树蒸馏这一环'。计划文档定义'图=真本体投影'约束、数据模型、构建管线、可视化前端。
- 结论: 这条线也已真正实现并上线:本次核实 exploration/{projection,version,backfill,causal_extract,manage,narrative}.py 全部存在且有pycache;material_graph.py存在;前端entities/material-graph三文件存在且已注册进导航;git log显示至少3次相关提交。
- 缥缈在哪: 代码在跑、UI可点开,相对不缥缈。缥缈感来源:没有'最终效果截图/演示'验收文档;反向固化器仍未做;产出分散在多份文档里,没有'这就是最终形态'的收敛陈述,需翻多份文档才能拼出全貌。

### docs/plans/[2026-07-02]SEMANTIC-OS-MAP/design-candidates/ 三份设计稿与交叉评审、digest.md (2026-07-02)

- 覆盖: 三份目标态架构设计均提出统一寻址方案,都把决策树/探索路径可视化列为既有计划引用点。critique-agent-nav交叉评审发现:tags_and_wikilinks.md已有类型化wikilink寻址+notes_api.py已实装解析器——三份设计稿都没注意到。digest.md重复搜索了MADR/AgDR/Karpathy Wiki模式,与05-15那批调研内容相当重叠。
- 结论: critique-agent-nav给出的元建议直接命中用户抱怨:三份稿子共同盲区是都从'我们该建什么'出发,没先核查'AI现在实际怎么导航、已经有什么'。
- 缥缈在哪: 决策树/探索路径可视化在此只是被反复提及为'已有计划',没有一份专门核实EXPLORATION-PATH-VIZ到底做完没有——即使大量引用它,也没人回去exploration/目录verify代码是否真存在真在跑。

**可直接复用**: 1) 05-15那批reasoning-network调研是关于假设/证明/证否/论证结构/知识图谱最全面的成熟传统选型报告,结论明确(Markdown+YAML+JSON Schema主编辑层/Argdown论证图/ADR决策层/形式证明只做可选后端),可直接作为决策树表达格式的选型依据,不必重新调研。2) [2026-06-18]DECISION-MEMORY的decisions域是当前唯一真正在生产环境跑、有3613行真实数据的决策记录系统,任何后续决策树工作都应在它之上增量,不要另起。3) [2026-06-27]EXPLORATION-PATH-VIZ的调研已把探索路径可视化的prior art查清,且计划已被证实真正实现(exploration模块+前端entities/material-graph已上线可点开)——不需要重新调研该怎么做,需要核实现在效果如何、反向固化器/网页浏览两块尚缺。4) critique-agent-nav.md指出既成事实:tags_and_wikilinks.md已有类型化wikilink寻址+notes_api.py已有解析器——任何统一寻址新设计都应先核对这个。


**这次不要重复的**: 不要再重复:①对Toulmin/IBIS/Dung AF/ASPIC+/ABA/Carneades/AIF/PROV/SACM/GSN/CAE/ADR/MADR/Lean/Coq/Isabelle/TLA+/Alloy/Dafny等成熟论证与形式化传统做新一轮文献综述——05-15那批三份文档已做得非常全面且给出明确选型结论,07-02批的digest.md也重复搜索了一次,两次结论基本一致。②不要再重新调研LLM对话树/分支工具/设计理由框架该长什么样——docs/research/2026-06-27-exploration-path-visualization.md已做过完整prior art扫描并给出数据模型和可视化方案取舍表。③不要再假设决策树/探索路径可视化只是纸面计划——本次盘点已用git log+find+grep实测核实exploration/六个模块文件、material_graph.py、前端entities/material-graph三文件均存在,且已注册进导航,git log显示至少3次相关提交。下一步应是去点开dashboard实际看一眼决策树视图长什么样、核实反向固化器和网页浏览两块的真实缺口,而不是再读一遍计划文档或再调研一遍prior art。
