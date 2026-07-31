# 决策本体同类产品调研:LLM 时代产品与开源代码

> 2026-07-10。承接 `docs/plans/[2026-07-10]DECISION-ONTOLOGY/plan.md`(决策本体设计稿)。用户指令:调研有没有类似产品、做法如何,以 LLM 和开源代码为主。
>
> 方法:13 个调研 agent(8 个主类 + 完备性批评自动发现 4 个缺口补漏),共核查 58 个产品/项目,每条要求读到真实机制(文件格式、字段、目录结构)并给一手来源。原始材料见同目录 `2026-07-10-decision-ontology-prior-art-appendix.md`(含全部字段级细节与来源链接)。覆盖 12 类:agent 规则同步工具、规格驱动开发、策略即代码、大模型护栏、管线编排触发语义、大模型可观测与提示版本、agent 知识晋升、自进化手册研究线,补漏 4 类:判例推理(CBR)与法律 AI、弱监督/标注回路平台、GitOps 配置漂移调和、特性开关/渐进交付。

## 一、总判词

**没有任何一个产品或开源项目实现了我们设计的完整形态**(语义手册软判断文字化 + 执行管线硬判断 + 接口纯指针 + 判例库 + 偏离回灌/晋升/退役三回路 + 两套可视化)。但**每个部件都有成熟的可抄样本**,而且分布在互不相识的几个生态里——把它们对齐拼起来,恰好就是我们的设计。三个此前被判为"独有"的部分,调研后定位如下:

- **偏离检测与调和**:结构态的"声明期望 vs 观测现实"持续对账是成熟工业范式(GitOps 全家),骨架可整套借;真空只在语义层("这条偏离在业务含义上合不合理、该不该固化成规则")。
- **判例晋升的人裁门**:业界几乎全是模型自主直写(Claude Code 自动记忆、Mem0、Letta、ACE、DSPy——最后这个文档明确写"移除了用户确认步骤");只有 Devin 一家把"运行→自动生成候选→人编辑/驳回/采纳→入库"产品化。我们坚持人裁不是过度设计,是业界最缺的一环。
- **手册互引图+全链路下钻可视化**:没有任何产品做到,大多止步于文件列表;是真领先,但也要按性价比排期。

另有两条横向结论:

- **"when"其实是两个不同的轴**,所有产品都只做了其中一个:①这条内容什么时候该进上下文/该触发(注入条件——Cursor/Kiro/OpenHands 做的);②系统行为在什么情境下成立(行为契约——OpenSpec 的 GIVEN/WHEN/THEN)。我们手册条目的"什么时候"字段必须拆成这两个独立小节,否则会混写。
- **商业化风险模式反复出现**:决策留痕/审计这类治理能力一旦做深就被收编成闭源云(Oso 弃开源、Invariant 被收购停托管、Camunda 8 改收费许可、driftctl 停摆并入商业产品)。我们把它做成自管设施,方向被反面案例反复验证。

## 二、按设计部件的可抄清单

### 语义手册:格式与治理

- **GitHub Spec Kit(MIT,119k star)**:宪法文件带语义化版本(MAJOR/MINOR/PATCH 自动判定),每次修订在文件顶部插 Sync Impact Report(版本变更+增删条目+**待联动更新的下游模板清单**,强制走一遍传播检查);一致性检测器只读、绝不擅自改宪法,原则变更必须走独立命令。→ 手册修订流程直接抄"改动必附影响报告+下游联动检查单"。
- **OpenSpec(MIT)**:specs/(现状真源)与 changes/(提议增量)物理分离,增量用 ADDED/MODIFIED/REMOVED 三段式 delta 而非整份重写;归档时 delta 合并回真源、change 文件夹整份平移进带日期的 archive/ 留痕。→ 手册条目的修订与判例留痕直接抄这套格式。
- **Flipt v2(Apache-2.0,全开源)**:中心本体=Git 仓库里的声明式文件,变更=PR,审批=PR review,审计=git history,回滚免费。与我们"手册是仓内 markdown"同构度最高,证明不必另造审批系统。
- **BMAD-Method(50k star)**:三层覆盖(出厂基线/团队/个人)+按数据形状定义的显式合并算法(标量覆盖/表深合并/带主键数组按键合并/普通数组纯追加);外部内容用 `file:` 引用不复制。→ "手册本体 vs 项目本地覆盖"的合并语义直接抄。
- **Tessl(MIT tile)**:spec 里 `[@test]` 行内链接把每条要求和验证它的测试焊死;**evals 黄金场景给"规则本身"配回归测试**(测 agent 照规则走会不会翻车,不是测代码),覆盖 drift、越权跳过、例外滥用等场景。→ 手册条目定稿后配金标场景,是"先测试过再回灌"的具体做法,我们此前完全没想到这层。
- **Langfuse(MIT)**:version(不可变递增整数)与 label(可变指针,production/staging,可加权限保护)分离。→ 手册条目版本化直接照搬。

### when 语义(两个轴分开抄)

注入/触发轴:
- **Cursor Rules 四态**(业界表达力最强):alwaysApply=true 常驻;globs 非空按文件匹配自动附加;仅 description 非空则由模型读描述自主判断相关性再拉全文;全空则仅手动引用。→ 手册条目"接口投影"的注入模式和管线 when 元数据直接抄这张三字段四态判定表。
- **Amazon Kiro**:steering 三种注入模式(always/fileMatch/manual)+ hooks 十种触发事件分类学(PreToolUse 按工具类别+正则、文件 glob、任务前后、手动);EARS 记法("WHEN 条件 THE SYSTEM SHALL 行为")把软判断压成一行可测试模板。
- **OpenHands Skills**:frontmatter 的 triggers 关键词列表——最便宜的确定性触发档,省 token、可预测。
- 结构化谓词层:**Dagster** AutomationCondition 可组合表达式树+子条件命名标签(UI 能显示"因为哪支条件触发");**Prefect 3** 把 when 拆成 match/expect/posture(反应式/预警式)/within/threshold 正交字段——预警式(期望事件窗口内未发生即告警)是我们缺的否定式触发;**flagd** 用 JsonLogic 做可序列化谓词。
- 结论:每条 when 同时给**文字语义描述(给模型判断)+可选硬匹配键(给管线判断)**,不二选一;这正是 Guardrails Hub 给每个校验器打 Rule/ML/LLM 三选一标签的做法——这个字段直接抄进管线注册表。

行为契约轴:OpenSpec 的 Requirement+Scenario(GIVEN/WHEN/THEN)+RFC2119 强度词(MUST/SHOULD/MAY 给软判断分级)。

### 执行管线与运行记录

- **OPA(CNCF 毕业)是"中心本体+分布执法+决策留痕"最完整的成熟实现**:策略打包成 bundle(带 revision、roots 命名空间声明"这个包只管哪些路径")、执法点 ETag 轮询拉取+增量 delta;**决策日志是一等公民**,字段清单(decision_id/trace_id/bundles 含 revision/path/query/input/result/requested_by/timestamp/metrics/脱敏标记)直接作为我们运行记录的字段基线;METADATA 注解(title/description/custom/scope 分层继承+运行时可读回)是"给可执行规则挂语义元数据不污染执行逻辑"的现成范式。
- **Temporal**:运行历史的最高标准——追加式事件历史+确定性重放,三态视图(时间轴/同类折叠/JSON 导出)+Principal Attribution(谁触发的)。
- **Airflow 3**:`triggering_asset_events` 把"这次为什么被触发"当一等数据透传进执行上下文——"运行高亮走了哪条路径"在执行层(非仅 UI)的实现方式。
- **NeMo Guardrails**:单次调用的四件套日志(activated_rails/llm_calls/internal_events/history)= "本次触发了哪些规则"的可抄字段设计;五类 rail 按管线阶段挂载(输入/对话/输出/工具调用/检索)。
- **Guardrails AI**:on_fail 动作枚举(重问/自动修/过滤/拒答/放行/抛错)= "判断失败后怎么处置"的成熟词表。

### 接口层(生成、同步、防漂移)

- **Ruler(MIT)**:生成物里每段插 `<!-- Source: <path> -->` 来源注释——"接口只存指针"最低成本落地;CI 里重放生成流程+git diff 判接口漂移(土但可行,第一版就用它)。
- **block/ai-rules**:把漂移检查做成一等 CLI 命令(status),不是让用户自己拼脚本。
- **Claude Code 自身**:MEMORY.md 索引强制加载(前 200 行/25KB)+主题文件按需读取的两级加载;插件面板的 token 开销分层(常驻成本 vs 触发成本)与"装了没用"巡检——手册可视化该标注"这条常驻多贵"。
- **GitHub Copilot** `excludeAgent`:按接入面身份排除某条投影;全局与路径限定叠加生效而非互斥择一(避免"匹配到最细就丢全局约束")。
- **AGENTS.md 标准的教训**:即便 6 万仓库采纳、20+ 工具支持,Claude Code 仍不原生读——接口同步永远要为每个执法点显式声明接法,不能假设约定即覆盖。

### 判例库字段

- **Devin Knowledge/Playbooks(闭源但机制公开)**:Trigger Description 必填且"具体优于模糊"(官方文档直接教怎么写 when);Playbook 五分法(步骤/完成后应为真的后置条件/易错点/禁止事项/需人提供的输入)与我们条目结构高度同构;每次编辑生成新版本可回滚。
- **Zep/Graphiti(Apache-2.0)**:双时态(事件发生时间 vs 写入时间)、作废不删除(invalidate not delete)、每条提炼事实强制关联回源记录(provenance)。三条全部抄进判例与手册条目。
- **法律 AI 的 Case Frame(2024)**:胜出理由与**落败理由并列记录**;"二阶元规则"槽位(本案用哪条元规则裁决几种解释谁优先+为什么适用);8 条批判性问题清单可直接改写成"这条判例还能不能引用"的退役检查单。
- **CBR 学科(30 年)**:四步循环(检索→套用→修订→保留)正好命名我们的回路,不必自造术语;案例三段式里"预期方案"与"实际结果"分开存(便于日后核验当初判得对不对)。
- **Horty/Bench-Capon 判例约束模型**:"新情况是否被既有判例强制"可核验——与判例库一致则自动走,两种结果都不矛盾才是自由裁量空间,只有真冲突才升级人裁。→ 直接落成"什么时候需要惊动作者"的判据,避免事事人审。

### 晋升回路(反向固化器的可抄件)

- **Devin 的候选卡交互**:运行反馈→自动生成建议→人编辑/驳回/采纳三态→入库,组织级待审列表。整套照抄。
- **Snorkel Flow 四象限**(需改造前提):按"手册是否覆盖"ד执行是否与手册一致"分四格→精修条目/换实现/新增条目/无需处理;错误反查最相关规则作为候选起点。注意它假设有客观对错,套到品味判断上要留"合法例外"档。
- **Argilla(Apache-2.0)**:候选记录三元组(候选值/把握度/来源 agent)+人审台;**distilabel**:候选生成不必写聚类算法,LLM 直接读一批偏离记录+现有条目产候选修订草稿,更轻。
- **ANGELIC(法律 AI)**:晋升的三段式——先写默认/不确定接受条件,遇到"干净"判例(排除被并列理由污染的假阳性)就把判例结论编码成优先级覆盖默认。候选不是模糊文字总结而是精确条件,人只裁"这条判例干不干净、采不采纳"。
- **ACE(ICLR 2026)**:条目级增量更新+helpful/harmful 计数器+确定性代码合并(不让 LLM 重写全篇)+每次变更落 diff 日志;明确命名两种手册退化模式(整体塌缩、同质化变短)可作退役审计检测指标。**AWM**:变量抽象化(具体值→占位符)是判例泛化成规则的具体技术动作。
- **Snorkel 开源库**:每条规则的覆盖率/重叠率/冲突率三件套体检指标——冲突高或几乎不触发的条目就是该送人裁的候选。

### 退役

- **Unleash(AGPL)业界最结构化**:条目按类型(发布/实验/运维/熔断/权限)配推荐存活期,到期自动标"疑似过期"进复核队列(而非巡检面板事后发现);五阶段生命周期由运行指标自动流转;审批期间再编辑会撤销已有批准。
- **GrowthBook(MIT)**:"单边规则"语义信号——某条判断的所有判例都倒向同一结果,说明它已退化成恒真式,该吸收为硬判断或归档;**AI agent 与人改配置走同一道审批+审计门,只加发起者身份字段**(业界唯一明确表态,直接回答了我们"子 agent 自动回灌要不要单开门禁"——不开)。
- **Smyth & Keane 竞争力保留算法(IJCAI 1995)**:按覆盖集/可达集把案例分四类(枢纽/冗余/桥接/互备),删除按固定优先级、绝不动枢纽案例——退役判据不看"多久没用"而看"删了会不会造成能力不可逆损失",伪代码完整可直接实现。

### 偏离检测与调和(GitOps 骨架整套借)

- 期望态与观测态物理分离存储(spec/status);水平触发而非边沿触发(不依赖不丢事件,定期重扫兜底);diff 结果三态而非二元(未纳管/已丢失/被改动,或加"无法判定"第四态)。
- **偏离豁免声明**(我们此前没处理误报):ArgoCD ignoreDifferences 三种匹配(字段路径/内容表达式/字段所有者);CloudFormation 更省心——只对显式声明过的属性做对账,没写进声明的变化天然不算偏离。
- **处置三档**:自动纠正/只告警等人/试运行只报告不执行(Puppet noop);**corrective_change 标记**区分"主动声明变了"还是"被动纠偏",两类别混进同一统计。
- **Terraform 的读写分离**:"只报告偏离"和"提议变更"是两条显式不同的命令——回灌回路不许一步到位改本体。
- **driftctl 停摆的教训**:纯检测不管理的外挂工具活不下来;偏离检测必须焊在管线执行主干和决策库上。

### 确认门与规模分档

- **Windmill**:人工确认门是流程定义里的一等步骤类型——所需批准数(会签)/超时转分支/恢复表单(人的判断作为数据进下游分支)/**禁止发起者自批准**。字段集合直接抄进"须确认"标志的实现。
- **Flagsmith**:审批线按**改动影响半径**分层(环境级一条线、跨环境项目级另一条线),而非笼统的重要与否——"要不要走确认门"锚定波及面,不是简单的长短二分。
- **DS-Agent(ICML 2024)**:建库阶段跑完整循环(允许贵)、使用阶段退化成轻量检索+小幅改写(要快)——正是"重投入建手册 vs 迅捷返回"的两阶段拆分。

### 可视化与提示-运行关联

- **Langfuse 是唯一把"这次运行用了规则哪个版本"写到字段级的产品**(运行时把取回的 prompt 对象整体传给观测 span);头部闭源产品(LangSmith/Braintrust)都只在营销层断言。我们把 ontology_refs 挂载点做到字段级,本身就是差异化。
- **Braintrust 的验证动作**:改了规则后,拿新版本回放历史真实运行再打分——比静态测试集更贴近"用真实运行检验修订"。
- 所有产品的评测回灌共同点:**结果先给人看、人点确认**,没有一家自动生效——与我们人裁门一致。

## 三、对设计稿的修订

九条字段级修订已折进 `docs/plans/[2026-07-10]DECISION-ONTOLOGY/plan.md` 第九节(when 拆两轴、偏离豁免与三态分类、处置三档、版本与指针分离、双时态、按类型配寿命的退役队列、确认门一等步骤化、黄金场景回归测试、人机同门),此处不重复。

## 四、产品全景索引(细节与来源见附录)

规则同步:Ruler、rulesync、block/ai-rules、AGENTS.md、Copilot instructions、Cursor Rules、Claude Code Skills/Plugins。规格驱动:Spec Kit、Kiro、OpenSpec、BMAD、Tessl。策略即代码:OPA、Cedar/Verified Permissions、Oso(弃)、GoRules/zen、Camunda DMN、Drools/KIE。护栏:NeMo Guardrails、Guardrails AI、Llama Guard、Invariant(被收购)。管线编排:Dagster、Temporal、Prefect、Airflow、Windmill。可观测:Langfuse、LangSmith、Braintrust、Phoenix、promptfoo。知识晋升:Devin、OpenHands、Mem0、Letta、Zep/Graphiti、Claude Code 记忆。自进化手册:ACE、Voyager、Reflexion、AWM、DSPy、GEPA。判例推理与法律 AI:CBR 4R、Smyth&Keane、ANGELIC、Horty/Bench-Capon、Case Frame、DS-Agent、jCOLIBRI。标注回路:Snorkel、Snorkel Flow、Argilla、Label Studio、distilabel。漂移调和:ArgoCD、Flux、Terraform、Puppet、CloudFormation、controller-runtime、driftctl。特性开关:OpenFeature/flagd、Unleash、Flagsmith、GrowthBook、Flipt。
