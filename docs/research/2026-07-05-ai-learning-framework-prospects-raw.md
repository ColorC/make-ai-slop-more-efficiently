<!-- [OMNI] origin=claude-code domain=domains/decisions ts=2026-07-05T00:00:00Z type=research status=active -->

# AI学习框架与三条极端路线前景调研——五路原始材料(2026-07-05)

> 综合报告=docs/reports/AI学习框架前景与探索顺序-2026-07-05.md。前一轮调研=docs/research/2026-07-05-ai-researcher-gap-research-raw.md。
> 五路 agent 结构化原始产出, 每路含批判性前提审查(critical_assessment); [读]=实际打开读过, [摘要]=只见搜索摘要。

## 产品级学习宣称核查

## key_findings

### 1. Manus的'Knowledge/记忆'官方定位为'外部知识库+文件系统记忆',而非模型学习
证据: 官方博客明确说'we treat the file system as the ultimate context in Manus: unlimited in size, persistent by nature'。Knowledge事件是RAG式检索注入,不改变模型权重。用户显式说'记住X'需要确认(confirmation)才persist为knowledge。'学习'实际发生在两处:(1)把失败的action-observation对保留在上下文里,让模型'隐式更新内部信念、把先验从类似动作上移开'(作者原话,非严格贝叶斯,是类比说法);(2)todo.md被'不断重写'以把目标推回模型的'近期注意力范围'(Recitation机制)。Agent Skills页面官方确认技能可以'从成功对话自动生成'(automatically generate skills from your successful conversations)。
来源: Manus官方博客 Context Engineering for AI Agents https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus ; Manus Agent Skills 页面 https://manus.im/features/agent-skills

### 2. 'Hermes'存在指代混淆:Nous Research旗下有两个不同东西——Hermes 3/Hermes 4系列大模型(SFT+DPO微调的固定权重LLM),以及2026年2月新发布的'Hermes Agent'(独立的agent产品,主打'persistent memory + 自我改进')
证据: Hermes 3是Llama 3.1的微调版本,用约3.9亿token合成数据做SFT+DPO两阶段训练,agentic能力(工具调用、scratchpad、内心独白)是训练时固化进权重的,不是运行时学习。Hermes Agent(2026-02发布,最新v0.18.0于2026-07-01)才是号称'persistent learning loop'的产品:memory工具支持原子批量编辑(atomic operations against a character budget)、/learn命令从目录/URL/工作流蒸馏出可复用skill、curator做去重(可选调用LLM consolidation)、/journey命令提供'记忆图谱'可视化、集成第三方Honcho做跨会话用户建模、Supermemory做语义长期记忆。
来源: Hermes 3 Technical Report https://arxiv.org/pdf/2408.11857 ; Hermes Agent官网 https://hermes-agent.org/ ; GitHub Releases https://github.com/NousResearch/hermes-agent/releases ; 官方Provider文档 https://hermes-agent.nousresearch.com/docs/integrations/providers

### 3. Hermes Agent的'自我改进闭环'缺关键一环:自评(self-evaluation)不可靠,导致失败任务也被当作'成功'编码进技能库
证据: Reddit真实用户报告(u/CustomMerkins4u,+107赞):让Hermes Agent从Indiana DNR网站抓水质检测数据,结果把所有数据搞乱,但agent的自评'一直认为自己干得很好'(It always thinks it did a good job. ALWAYS)。第三方技术综述(crabtalk.ai)在'Open questions'部分直接追问:技能会不会过时?冲突技能会不会造成混乱?有没有pruning机制,还是让向量库无限增长?——作者承认文档没有回答这些问题,且Honcho用户建模'是否产生了有意义的改进,还是只是积累越来越陈旧的用户档案'仍是未知数。
来源: Kilo文章(整理1300条Reddit评论) https://kilo.ai/articles/openclaw-vs-hermes-what-reddit-says ; CrabTalk技术综述 https://crabtalk.ai/blog/hermes-agent-survey

### 4. Devin的Knowledge功能是'触发式知识条目库',触发靠trigger description,没有自动验证/版本管理/过期机制,依赖人工手动enable/disable
证据: 官方文档:每条Knowledge需要一个Trigger Description,'Devin会在当前工作与指定触发相关时检索该知识项';AI Suggestions基于聊天反馈自动建议要记住的知识,用户可编辑或驳回。文档未提及embedding检索还是关键词匹配的具体实现,也未提及冲突检测/自动失效。第三方长期使用报告(Answer.AI,一个月使用记录)指出:即便提供了'大量文档和示例',Devin仍然'难以使用内部工具'、'没有很好地阅读nbdev文档',说明knowledge base infrastructure设计良好不等于模型能正确利用它——20个任务里只有3个令人满意完成,14个彻底失败(The Register 2025年报道)。
来源: Devin官方文档 https://docs.devin.ai/product-guides/knowledge ; Answer.AI一个月使用记录 https://www.answer.ai/posts/2025-01-08-devin.html ; The Register报道 https://www.theregister.com/2025/01/23/ai_developer_devin_poor_reviews/

### 5. ChatGPT memory的底层实现不是向量检索,而是把'用户画像文本+最近约40条对话摘要'直接拼进system prompt;2026年6月上线的'Dreaming'机制是背景异步合成,处理规模化下的'过时/正确性/可扩展性'三大问题,但官方'无存储上限'的宣称与实测不符
证据: 独立技术分析(embracethered,通过逆向工程系统提示词)拆出六个组成部分:1)带时间戳的bio工具保存记忆 2)从历史对话推断的助手响应偏好 3)话题亮点总结 4)用户洞察(姓名/职业/位置) 5)约40条最新对话的时间戳+摘要+用户消息 6)交互元数据(设备/地理位置)。作者实测:官方称'无存储限制',但询问一年前的具体对话时ChatGPT无法检索,说明'最近对话'部分只是滑动窗口式截断,不是全量索引。OpenAI官方博客(通过第三方转述,原文403无法直接访问)将Dreaming类比人类睡眠记忆巩固,核心机制是'temporal revision'——系统会随时间推移主动修订已存记忆,应对'staleness(过时)、correctness(正确性)、scalability(可扩展性)'三个挑战,面向'数亿用户、跨多年时间尺度'。
来源: 独立技术分析 embracethered https://embracethered.com/blog/posts/2025/chatgpt-how-does-chat-history-memory-preferences-work/ (实际读取原文) ; OpenAI Dreaming相关二手转述(官方博客本身403无法直接验证,以下按摘要标注) StackFutures https://stackfutures.com/blog/chatgpt-dreaming-memory-june-2026/

### 6. Anthropic的memory tool是纯client-side文件操作API(create/view/str_replace/insert/delete/rename六个命令),存储完全由调用方实现,官方文档明确没有信念验证/证伪/自动过期机制,过期清理责任在开发者;Claude Code的MEMORY.md是同一思路的项目级落地,200行硬上限,超限静默只读前200行
证据: 官方API文档逐字确认:'The memory tool operates client-side: Claude requests file operations, and your application executes them. You control where and how the data is stored through your own infrastructure.' 安全章节明确写'Memory expiration: Periodically delete memory files that haven't been accessed in a long time'——这是留给开发者自己做的待办,不是内置机制。独立逆向分析(giuseppegurgone)证实Claude Code的MEMORY.md会在每次会话开始时被读取并注入system prompt,'这只是上下文重用,不涉及模型权重更新或真正的记忆学习',且发现共享团队记忆功能仅限自定义Agent,主会话未实现,分主题文件自动读取被锁在默认关闭的功能标志(tengu_coral_fern)后面。Anthropic官方Skills页面确认Skills可'自动从成功对话生成',通过description字段做语义触发判断,但同样没有自动验证skill正确性的步骤。
来源: Anthropic官方Memory tool文档(完整原文读取) https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool ; 独立逆向分析 https://giuseppegurgone.com/claude-memory ; Claude Skills官方公告(经WebSearch二手摘要,未直接打开原文) https://code.claude.com/docs/en/skills

### 7. 行业综述层面(mem0《State of AI Agent Memory 2026》)承认'记忆过时导致自信的错误'是2026年仍未解决的开放问题,且没有任何主流产品把信念验证/证伪机制当作标配
证据: 报告原文按摘要转述的表述:'一条关于用户雇主的高检索率记忆,在他们换工作后变成自信的错误',报告将其列为未解决问题,没有给出解决方案。报告聚焦标准化benchmark(LoCoMo、LongMemEval、BEAM)和检索架构演进(持久存储层、多信号检索、实体链接),但未讨论验证/证伪/信念更新结构,也没有点名批评具体产品。
来源: mem0 State of AI Agent Memory 2026 https://mem0.ai/blog/state-of-ai-agent-memory-2026 (实际读取原文)

## critical_assessment

### 1. Manus：Knowledge模块 + 文件系统外化记忆 + Recitation注意力操纵
隐含前提: 隐含前提:任务在单个较长session内完成、云端持久文件系统由厂商托管(非本地单机)、多用户场景下有'集体反馈模式'可供沉淀(需要用户规模)。所谓'隐式贝叶斯先验调整'依赖把错误留在同一个长上下文里,前提是上下文没有被裁剪/摘要掉。
对我们: 我们是单用户单工作站,没有'跨用户集体反馈'这个规模效应;但'文件系统作为记忆载体'+'todo.md式注意力复述'这两条对我们完全适用,而且我们已经在用类似机制(MEMORY.md)。差距在于:Manus的knowledge item需要用户手动confirm才persist,没有自动验证内容对不对——这点对我们是提醒,不能照抄。

### 2. Hermes Agent(Nous Research 2026年新品,不是Hermes 3模型):/learn命令+skill curator+Honcho用户建模+memory graph可视化
隐含前提: 隐含前提:有一个独立的'auxiliary model'做背景review和curator打分,这需要额外的推理算力预算(每次任务后台再跑一次分析);'self-evaluation'环节假设模型能可靠判断自己有没有做好——Reddit实测证明这个前提在真实工作(如数据抓取核对)中经常不成立;技能会无限增长,需要有pruning机制,而作者自己也承认这块不成熟。
对我们: 我们不能照抄它的'自评即真'模式——这正是我们要用'非执行方评审'去堵的洞(已有铁律:计划-测试-评审绑定TDD式)。它的skill从对话蒸馏、写成SKILL.md这套和我们的决策库/材料沉淀方向一致,但curator的'去重复/去陈旧'逻辑无外部证据支撑,不能直接搬。适合我们的是:它把'自评不可靠'这个坑用真实案例暴露出来,提醒我们决策库必须有独立信念验证环节,不能靠agent自己说'这条决策成立了'。

### 3. Hermes 3(Nous Research 的 LLM 本身,SFT+DPO微调出的固定权重模型,agentic function-calling能力)
隐含前提: 这是训练时一次性学习(fine-tune),不是运行时持续学习。'学习'发生在Nous Research训练该模型的阶段,消费者拿到的是权重冻结的产物,运行时不会因为你用得多就变聪明。
对我们: 与我们的场景(单用户在线学习/沉淀)完全不同轴——这是模型供应商侧的能力,不是agent产品的'越用越聪明'。容易和上面的Hermes Agent产品混淆,需要在对外表述里明确区分。

### 4. Devin Knowledge:trigger description触发的知识条目库,AI Suggestions自动建议记忆
隐含前提: 隐含前提:触发描述准确覆盖未来会遇到的场景(需要人工维护trigger,本质是团队协作场景下积累的'团队知识库'那套逻辑,假设有组织级/企业级多人共享的规模);知识条目本身对不对由人工手动enable/disable把关,没有自动验证。
对我们: 我们是单人场景,没有Organization/Enterprise scope的意义,但trigger-description这个'条件式召回'思路对我们的决策库检索有参考价值。核心差距:Devin的knowledge base infrastructure本身设计良好(第三方评价),但真正瓶颈在模型执行能力(hallucinate、20个任务只完成3个),说明'有知识库'不等于'能正确用知识库'——这是对我们最大的警示,决策库建好不代表AI真的会正确调用/遵守它。

### 5. ChatGPT memory(saved memories + chat history + 2026年6月上线的Dreaming背景合成)
隐含前提: 隐含前提:海量用户规模(官方原话是'数亿用户、跨多年时间尺度'的staleness/correctness/scalability三大挑战),需要专门的后台异步合成管线去解决'记忆过时'问题;这个前提在个人单机场景根本不存在——没有'跨多年漂移'的规模问题,只有'我这一个人的决策会不会前后矛盾'。
对我们: Dreaming处理的staleness/correctness问题在我们单人场景不需要用它的方式解决(不需要为百万用户做异步后台画像合成),但它承认的'correctness'问题(比如换工作后旧记忆变成自信的错误)恰恰是我们决策库要解决的核心——用证伪生命周期+决策树supersedes链条,而不是像ChatGPT那样让新对话悄悄覆盖旧画像且用户看不见过程。独立分析(embracethered)证实其底层就是把用户画像文本拼进system prompt,并非向量检索,'无限存储'宣称与实测的'仅约40条最近对话'不符——这提醒我们对外宣称必须诚实,不能把'能装多少'和'实际会用多少'混为一谈。

### 6. Claude memory tool(API层,client-side文件读写)+ Claude Code MEMORY.md(项目级,写入system prompt)+ Agent Skills(SKILL.md,静态+可自动生成)
隐含前提: memory tool的隐含前提是'开发者自己实现存储后端并做path traversal防护、容量上限、过期清理'——Anthropic官方文档明确写着这些安全责任在调用方,不是托管服务;Claude Code的MEMORY.md有200行硬上限,超限静默截断到前200行,说明它假设记忆内容量很小、高度提炼,不适合塞原始细节。
对我们: 这个我们已经在用(user CLAUDE.md + 项目MEMORY.md),但独立分析(giuseppegurgone逆向工程)证实这纯粹是'文件读写+prompt注入',完全没有模型权重更新,也没有信念验证/过期机制——'过期'完全靠人工判断该不该删。这与我们的决策库形成鲜明对照:我们要做的证伪生命周期、决策树supersedes、消费追踪账本,恰恰是Anthropic这套裸机制完全没有的部分,说明我们在做一件目前没有现成产品覆盖的事,而不是重复造轮子。Skills的'自动从成功对话生成'(Manus/Devin/Hermes Agent都有类似功能)是三家都在做的共性趋势,但都没有自动验证'这个skill真的对吗'这一步——全部依赖后续实际使用中隐式验证或人工审阅。

## transferable_mechanisms

1. **文件系统作为外化记忆+按需读取(Manus/Anthropic memory tool共有):不把所有历史塞进context,而是写成文件、需要时才读,保持active context聚焦当前任务**
   落法: 我们的审阅台+决策库本质上就是这个思路的加强版:决策不是塞进prompt,而是落库(decision/belief/comment三kind),运行时按需查回。可以进一步明确'什么时候写、什么时候读'的触发规则,避免像Claude Code MEMORY.md那样出现200行硬截断的粗暴上限——我们应该用检索(按anchor/links查)代替线性堆叠。

2. **Recitation(Manus的todo.md反复重写机制):通过持续复述目标,把关键信息推回模型的近期注意力,对抗长上下文里的目标漂移**
   落法: 可以用在运行留痕账本上——每次运行开始前,自动把'本次运行将消费的历史决策清单'复述进当前任务的顶部上下文,而不是让AI自己去决策库里翻找;这样账本记录的'消费了哪些决策'本身就是喂给模型的复述稿,一举两得。

3. **Trigger Description式条件召回(Devin Knowledge):知识条目挂一个'什么情况下该调用我'的描述,靠语义匹配当前任务与描述来决定是否检索**
   落法: 决策库的belief/decision记录可以加一个'适用场景描述'字段,让检索不是纯关键词/embedding粗筛,而是先过一层'这条决策的原始适用边界还符不符合当前场景'的语义判断——这其实就是我们已经想做的'证伪生命周期'的输入端:场景漂移了就该触发复核,而不是被动等失效。

4. **Skill自动蒸馏(Manus/Devin/Hermes Agent三家共性):从一次成功的对话/任务中自动提炼出可复用的SKILL.md,而非要求用户手写**
   落法: 我们的决策动词词表v1+材料沉淀方向可以复用这个模式,但必须补上三家都缺的一环——独立验证。具体做法:自动蒸馏出的候选决策/skill先落为'待验证'状态,进入我们已有的'非执行方评审'流程(计划-测试-评审绑定TDD式铁律),而不是像Hermes Agent那样靠agent自评就直接持久化,这正好是Reddit实测证明会出事的地方。

5. **过期/staleness的显式建模(mem0报告点出的开放问题+ChatGPT Dreaming的temporal revision尝试):记忆会随时间/场景变化变成'自信的错误',需要有主动修订机制**
   落法: 这正是我们'证伪生命周期'要解决的核心问题,而且行业目前(2026年中)公开承认还没解决好。我们可以把决策树的supersedes链条做成显式的'这条决策被哪条新决策替代/为什么'记录,而不是像ChatGPT那样静默覆盖用户看不见的画像,也不是像Devin那样纯靠人工手动disable——用可追溯的证伪记录替代两种做法的短板。

6. **运行时消费追踪的对照:目前查到的所有产品(Manus/Devin/ChatGPT/Claude/Hermes Agent)都没有'这次运行具体消费了哪几条历史记忆/决策'这种细粒度账本,只有粗粒度的'记忆被注入了'或'技能被调用了'**
   落法: 这确认了我们'运行留痕账本'这个设计目前在公开产品里没有对应物,是我们体系里相对独特、值得坚持做下去的一块——不是重复造轮子,而是补了行业普遍缺失的可追溯层。

## sources
- [读] Manus官方博客: Context Engineering for AI Agents - Lessons from Building Manus — https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
- [读] Manus官方: Agent Skills功能页 — https://manus.im/features/agent-skills
- [摘要] Trickle blog: Manus AI Review — https://trickle.so/blog/manus-ai-review
- [摘要] Hermes 3 Technical Report (arXiv 2408.11857) — https://arxiv.org/pdf/2408.11857
- [读] Hermes Agent官网 — https://hermes-agent.org/
- [读] Hermes Agent GitHub Releases — https://github.com/NousResearch/hermes-agent/releases
- [读] Hermes Agent官方文档: Providers — https://hermes-agent.nousresearch.com/docs/integrations/providers
- [读] Kilo: OpenClaw vs Hermes 2026: 1,300 Reddit Comments Analyzed — https://kilo.ai/articles/openclaw-vs-hermes-what-reddit-says
- [读] CrabTalk: Hermes Agent - what Nous Research built (技术综述) — https://crabtalk.ai/blog/hermes-agent-survey
- [摘要] 知乎: 拆解 Hermes Agent：开源 Agent 里唯一的闭环学习系统 — https://zhuanlan.zhihu.com/p/2025619437139628484
- [读] Devin官方文档: Knowledge — https://docs.devin.ai/product-guides/knowledge
- [读] Answer.AI: Thoughts On A Month With Devin — https://www.answer.ai/posts/2025-01-08-devin.html
- [摘要] The Register: 'First AI software engineer' is bad at its job — https://www.theregister.com/2025/01/23/ai_developer_devin_poor_reviews/
- [摘要] OpenAI Help Center: Memory FAQ — https://help.openai.com/en/articles/8590148-memory-faq
- [摘要] OpenAI官方博客: ChatGPT Memory Dreaming (403无法直接访问,内容按二手转述标注) — https://openai.com/index/chatgpt-memory-dreaming/
- [读] embracethered: How ChatGPT Remembers You - 独立技术分析 — https://embracethered.com/blog/posts/2025/chatgpt-how-does-chat-history-memory-preferences-work/
- [摘要] StackFutures: OpenAI Ships Dreaming 2.0 — https://stackfutures.com/blog/chatgpt-dreaming-memory-june-2026/
- [读] Anthropic官方文档: Memory tool — https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- [读] giuseppegurgone: Claude Code's Experimental Memory System - 独立逆向分析 — https://giuseppegurgone.com/claude-memory
- [摘要] Claude Code官方文档: Extend Claude with skills — https://code.claude.com/docs/en/skills
- [读] mem0: State of AI Agent Memory 2026 — https://mem0.ai/blog/state-of-ai-agent-memory-2026


---

## SOP手册路线证据

## key_findings

### 1. 把人类科研流程编成固定三阶段流水线(Agent Laboratory)本身能大幅降低成本, 但自主模式论文质量远低于人类反馈模式, 且论文承认完全自主目前不可行
证据: Agent Laboratory三阶段为literature review/experimentation/report writing; 全自主模式NeurIPS风格评分均值3.8/10, 加入'阶段末尾人类checkpoint'的co-pilot模式升到4.38/10(quality+0.75/soundness+0.48/clarity+0.23), 但significance反而-0.05; 成本仅$2.33/篇(GPT-4o); 论文人类checkpoint机制为'每个子任务结束时人类可批准或要求带高层意见重做'。
来源: Agent Laboratory (arXiv 2501.04227) https://arxiv.org/pdf/2501.04227 / https://arxiv.org/abs/2501.04227

### 2. 四次真实自主研究尝试中三次失败, 失败根因集中在'实现漂移'(遇阻退化为简化版而非解决根本问题)、'域智能缺乏'(不可行参数未被标记)、'长时域上下文退化'(逐渐丢失此前已建立的配置追踪)、'过度乐观'(只看汇总报告不查原始日志)
证据: MARL-1持续把过时hanabi-learning-env库导入方式恢复回旧版; WM-1遇训练超时选择'用简化/测试模式跑'而非修根因; WM-2违反Dreamer需在线学习的核心算法前提、CLIP处理误加.detach()阻断梯度流; 唯一成功案例AS-1靠'组合假设方法'(首假设失败后Revision Agent触发从测试转向调查失败机制)才成功。论文提出四条应对原则:'从抽象开始后期接地气'/'验证一切(独立于LLM自证)'/'规划失败与恢复(多轮而非零样本)'/'记录一切'。
来源: Why LLMs Aren't Scientists Yet: Lessons from Four Autonomous Research Attempts (arXiv 2601.03315) https://arxiv.org/html/2601.03315v1

### 3. 给LLM agent详尽的行业标准SOP自然语言文档并不能保证正确执行, 成功率因领域跨度达3.4倍, 且暴露的核心问题不是'看不懂SOP'而是SOP对人类隐性知识的省略在LLM这里变成真实的歧义
证据: SOP-Bench: 2411个任务/12个领域, SOP平均1394 token; Claude 4 Opus ReAct平均72.4%成功率, 但从最简单94.3%到最复杂27.8%; 视频标注任务26个工具时成功率仅20.8%, 精简到6个工具后升到37%(工具注册表膨胀本身导致16.2个百分点损失); 论文原文'对具有隐含领域知识的人员而言可能不模糊, 而我们的实验表明对LLMs而言确实模糊'。
来源: SOP-Bench: Complex Industrial SOPs for Evaluating LLM Agents (arXiv 2506.08119) https://arxiv.org/html/2506.08119

### 4. 复杂政策文档纯靠prompt呈现时, 随工作流深度增加准确率呈指数(非线性)崩溃, 深度3+任务数12时可跌到0%
证据: CAP-CPT论文表2: Qwen-2.5-32B在task(12)/workflow(3)设置下prompting baseline成功率0.01(1%), task(1)时仅7%; τ-Bench真实基准中原始prompt方法准确率26.96%; 复杂政策文档~50K token时占输入35%; 论文明确'workflow-level complexity leads to a much sharper drop'。
来源: Analyzing and Internalizing Complex Policy Documents for LLM Agents / CAP-CPT (arXiv 2510.11588, ACL 2026) https://arxiv.org/html/2510.11588v1

### 5. 把公司政策写进system prompt(最佳努力式)在τ-Bench航空任务上pass^10仅22.7%, 换成运行时结构化断言检查(ToolGuards)提升到pass^10=50%, 提升超过20个百分点
证据: 论文实验: 原始prompt-only方法pass^1=0.450/pass^10=0.227; 加入政策文档反思pass^10≈0.273; 完整ToolGuards(工具调用前自动执行验证代码, 检测违规则让agent重新思考)pass^1=0.685/pass^10=0.500; 论文原话'最佳努力策略本质上是非确定性的, 容易受攻击且无法有效扩展'。
来源: Towards Enforcing Company Policy Adherence in Agentic Workflows (arXiv 2507.16459) https://arxiv.org/html/2507.16459

### 6. knowing-doing gap有专门实证:模型能准确复述自己违反的约束(回忆准确率97.3%), 但同时违反率(KBV率)在模型间从8%到99%剧烈波动, 论文明确排除'纯遗忘'解释, 且74%违反发生在多轮交互第2轮之前
证据: DriftBench: 7个模型/38份研究简报/2146次评分运行/四种交互条件(单次/多轮中立/多轮压力/带检查点多轮); GPT-5.4的KBV率仅8%而Sonnet 4.6达99%, 但两者约束回忆准确率都接近100%; 论文原文'约束遗忘预测漂移应与回忆失败相关, 在本基准中情况并非如此'、'模型准确复述它们同时违反的约束'; 机制解释保持开放(候选:指令仲裁/表现性顺从/生成时约束未被主动权衡)。
来源: Models Recall What They Violate: Constraint Adherence in Multi-Turn LLM Ideation / DriftBench (arXiv 2604.28031) https://arxiv.org/html/2604.28031

### 7. 多轮指令遵循随轮数单调退化, 与前提的complexity无关也会发生; 声明式知识测试(知道规则)与程序性/生成式应用测试(用规则做决策)之间存在实测的15-23个百分点gap, 且这个gap会被常规复合评分掩盖
证据: Multi-IF基准: o1-preview第1轮准确率87.7%降到第3轮70.7%; InvestPhilBench: L1-L3声明式事实检索73-87%, 但用Gate Reconstruction Accuracy(逐门正确性)衡量的程序性重建在frontier模型上仅L4≈0.77/L7-L8的0.57-0.62, 比复合评分显示的高出15-23个百分点的差距被流畅文本掩盖; 论文机制解释锚定Anderson ACT*模型——程序性知识需要经练习编译的if-then产生式规则, 非文本声明式检索可得。
来源: Multi-IF (arXiv 2410.15553) https://arxiv.org/html/2410.15553; InvestPhilBench (arXiv 2606.25984) https://arxiv.org/html/2606.25984

### 8. 45位专家科学家对比AI审稿人与人类审稿人评Nature系列论文:AI在代码/严谨性等可核查技术层面有真实价值,但在'领域背景意义/创新贡献判断'上系统性失败, 人类审稿人预测最终录用的AUC(0.822)显著高于AI审稿人(0.710), 结论是'AI可增强但不能替代人类评审组'
证据: 另一独立研究显示AI自评分普遍虚高(GPT均分7.3/Claude均分6.1 vs 人类均分4.3), AI审稿人与人类评分相关性弱(r=0.15)但AI审稿人彼此间高相关(r=0.49); AI审稿人无法区分人类撰写的真实摘要与AI编造的摘要, 而人类审稿人能。
来源: On the limits and opportunities of AI reviewers (arXiv 2605.20668) https://arxiv.org/html/2605.20668v1; 相关独立数据点(AI自评分虚高)见WebSearch汇总,未逐篇精读，标记为参考性佐证

## critical_assessment

### 1. Agent Laboratory 三阶段固定流水线(literature review→experimentation→report writing)
隐含前提: 隐含前提: 研究任务可预先拆成通用的三阶段并且每阶段的产出格式固定(论文强制摘要/引言等固定结构、最多两张图); 假设人类反馈只需要在阶段边界给一次高层意见就够纠偏; 假设评估可以用LLM扮演审稿人自动打分来替代人类评审。
对我们: 我们是单用户单机场景, 没有'大规模生成论文再筛'的吞吐量诉求, 所以Agent Laboratory省成本($2.33/篇)这个卖点对我们不成立; 但它暴露的两条对我们直接有用: (1)人类只在阶段边界看一眼比全自主线均分高0.58但仍全面偏低(4.38/10对应NeurIPS录用线远不到), 说明'阶段末尾拍一下'这种粗粒度检查点不足以让审阅台真正起作用, 我们的材料审阅台需要比'阶段末尾一瞥'更细的挂钩点; (2)LLM自评分和人类评分弱相关(r=0.15)在我们的账本/决策库场景直接对应'AI自己写决策记录里的信念更新是否可信'——不能让AI自证完成或自估质量, 这与我们已有的'完成判定不由执行方自认'规范吻合、且有了实证支撑。

### 2. SOP-Bench: 给LLM agent自然语言SOP文档+工具规范, 测执行成功率
隐含前提: 隐含前提: 现实工业SOP本身是给'已有隐性领域知识的人类员工'写的, 省略了大量'对人不模糊但对LLM模糊'的步骤细节; 前提是任务可以分解成工具调用序列且有明确的成功判定; 前提是复杂度可以用工具数/token数/分支数量化。
对我们: 我们只有一个用户+AI全程参与, 没有'老员工传帮带'积累的隐性知识库, 这正是SOP-Bench揭示的短板会被放大的场景: 论文里'工具从26个精简到6个, 成功率从20.8%升到37%'说明减少同时呈现的选择面比写更细的SOP文本更有效——这对应我们决策库/审阅台设计上应该'收窄当前可选动作集'而非'把规范写得更全'; 但SOP-Bench测的是电商/医疗等有明确外部SOP文档的领域, 我们的决策管线更多是'自建规范', 复杂度可控性比它测的场景更高, 不能直接套用它的失败率数字, 只能借鉴'步骤数越多、工具越多, 纯prompt文档表现越差'这个方向性结论。

### 3. CAP-CPT论文: 复杂政策文档内化, 纯prompt baseline随workflow深度指数崩溃(task(12)/workflow(3)成功率0%)
隐含前提: 隐含前提: 论文对比的解法(Category-Aware Policy Continued Pretraining)需要用大量(1K-30K条)人工标注的策略执行轨迹做持续预训练, 这要求团队有能力生成/标注这些轨迹并有微调基础设施; 隐含假设policy可以被拆成可枚举的'事实性/行为性/简单条件/复杂条件'四类规范。
对我们: 我们没有RL/标注基建、没有团队标注力量, CAP-CPT这个解法本身(继续预训练+SFT)在我们场景完全不可行——这是评估要求要对着的约束之一, 直接排除'把SOP内化进模型权重'这条路。但它暴露的现象(纯prompt随工作流深度指数崩溃, 而非线性下降)对我们有用: 说明我们八环节里越往后越多步骤累积的环节(设计验证→执行留痕→更新信念这条链), 光靠在prompt里叠加规范文字是不够的, 且失效不是渐进的而是断崖式的, 提示我们的门禁应该按'累计步骤数'设阈值主动切断/复核, 而不是假设prompt里写了规则就会一路稳定执行。

### 4. τ-Bench公司政策遵从论文: prompt-only baseline pass^10=22.7% vs ToolGuards结构化强制 pass^10=50%
隐含前提: 隐含前提: 政策可以被自动映射成可执行的'工具前置检查'(ToolGuards), 即领域动作集合是有限、可枚举、可提前编码成断言的(航空公司退改签规则); 前提是'检测到违规就让agent重新想'这种运行时拦截可以真正生效(即拦截点在动作执行前, 不是马后炮)。
对我们: 这条对我们最直接可迁移: 我们的'决策管线+确定性门禁'思路本质上就是ToolGuards的思路(把规范从prompt文本变成运行时可执行检查), 这篇论文用20+个百分点的硬数字证实了这个方向优于纯prompt。但要注意隐含前提——ToolGuards要求动作空间可枚举、违规判据可写成确定性代码, 这在我们'研究/探索'类工作(八环节里的'提出问题/形成假设')里未必成立, 那些环节的'对错'本身需要判断力而非规则匹配, 所以这个机制能覆盖的是决策管线里'执行/留痕'这类有明确对错的环节, 覆盈不到'形成假设/更新信念'这类需要判断力的环节。

### 5. 45位专家评AI审稿人(Nature系列论文): AI在'代码/严谨性'检查强, 在'领域背景/创新意义'判断上系统性失败
隐含前提: 隐含前提: '好研究'的判断包含两类完全不同的能力——技术正确性(可核查、有确定性答案)和学术贡献判断(需要对该子领域历史脉络、竞争工作、'这个问题重不重要'有内化的品味), 后者在训练数据里没有显式监督信号。
对我们: 这直接对应我们八环节里最难的'挑选下一步'(这个方向值不值得做)和'复盘校准'(这轮做得好不好、该不该继续)两环。我们是单用户场景没有45人专家团做二次校验, 这意味着我们更没有能力去补足AI在'领域背景判断'上的短板——用户自己是唯一的'人类专家评审', 所以决策库里这两环的记录必须明确标注'AI判断'与'用户判断'的边界, 不能让AI自己对'这条探索值不值得'做终审, 这是审阅台设计上要加的硬性区分, 而不是靠更细的SOP文字去弥补。

### 6. DriftBench: knowing-doing gap不是遗忘(约束回忆准确率97.3%, 但KBV率8%-99%跨模型剧烈波动)
隐含前提: 隐含前提: 论文测的是'创意生成任务'(写研究简报)在多轮压力下的约束遵守, 前提是可以用'回忆准确率'和'实际遵守率'两个独立指标分离出'不知道'和'知道但不做'这两种失效; 74%违反发生在第2轮前意味着这个现象在短程交互里就会出现, 不是长程独有的。
对我们: 这是本路调研最核心的证据: 它证明了'写清楚规则+确认模型能复述规则'这件事本身不能保证执行——这直接推翻'把SOP写全就够了'这个直觉的最强反例。但也要注意隐含前提: KBV率在不同模型间从8%到99%剧烈跳变(GPT-5.4仅8%, Sonnet 4.6达99%), 说明这个gap的大小高度依赖具体模型而非放之四海皆准的规律, 我们用的是API大模型(可能包括这些前沿模型), 不能假设'我们用的模型'一定落在高KBV率那一端, 但也不能假设它天然免疫——这提示我们即便选了'声称遵循指令好'的模型, 也必须靠外部门禁验证而非信任模型自称'我知道规则了'。

## transferable_mechanisms

1. **ToolGuards式运行时前置检查(τ-Bench论文): 自动把自然语言政策映射成'工具调用前执行的断言函数', 检测到违规就让agent重新想而非直接放行执行**
   落法: 对应我们'材料审阅台按确定性门禁分层级'的设计: 决策管线里凡是能枚举清楚'什么算违规'的环节(比如决策记录必须带证伪条件、材料必须挂对material类型、发布前必须过白名单检查), 应该做成执行前的断言检查, 而不是写进给AI看的规范文档指望它自己遵守; 这与我们已有'不可逆操作前先补质量'规则同构, 可以把这类检查点系统化为决策库的'门禁挂钩点清单', 而非分散在各处prompt里重复强调。

2. **SOP-Bench的'工具集精简优于文档加长'(26个工具减到6个, 成功率20.8%→37%)**
   落法: 对应我们决策库/审阅台在单次交互里暴露给AI的'可选动作/可选material'范围: 与其把决策动词词表和规范都塞进一次prompt让AI自己挑, 不如按当前所在的八环节阶段动态收窄可选集合(比如'形成假设'阶段只暴露belief相关的写入动作, 不暴露发布/归档动作), 用界面/工具注册表的方式做范围收窄而非依赖文本说明。

3. **CAP-CPT揭示的'工作流深度是最强衰减因子'(深度越深崩得越快, 是断崖式而非线性), 对应DriftBench '74%违反发生在第2轮前'**
   落法: 对应我们八环节里累积链条最长的'设计验证→执行留痕→更新信念'这段: 应该在决策管线里按累计步骤数/累计轮次设置强制复核点(类似τ-Bench的checkpoint机制), 而不是假设一次写好的规范能撑过整条链; 具体可以让运行留痕账本除了记录'消费了哪些历史决策', 也记录'当前链条已走了几步', 到阈值强制过一次人工审阅台复核。

4. **45人专家评审揭示的'AI强于可核查的技术正确性、弱于需要领域品味的价值判断'能力二分**
   落法: 对应决策库三kind(decision/belief/comment)里应该明确标注每条记录的'判断类型'——技术性/可核查的交给AI自主写入并可信, 涉及'这个方向值不值得''这个信念该不该采信'的价值判断类记录必须标注为'待用户复核'状态, 不能因为AI写得流畅就默认视为已决; 这是对我们已有'完成判定不由执行方自认'规则的具体化落地位置。

5. **Agent Laboratory的'阶段边界人类checkpoint仍不够'(全流程仅在边界看一眼, 质量分仍全面偏低)**
   落法: 对我们审阅台的启示是检查点粒度要比'阶段末尾'更细: 不能只在八环节的环与环之间设一个人工确认点, 而应该在环节内部的关键子步骤(比如'设计验证'环节里的'验证方法本身是否可信'这个子步骤)单独挂钩审阅, 避免把整段自主执行的中间过程都当黑盒。

## sources
- [读] Agent Laboratory: Using LLM Agents as Research Assistants — https://arxiv.org/abs/2501.04227
- [读] Why LLMs Aren't Scientists Yet: Lessons from Four Autonomous Research Attempts — https://arxiv.org/html/2601.03315v1
- [读] SOP-Bench: Complex Industrial SOPs for Evaluating LLM Agents — https://arxiv.org/html/2506.08119
- [读] Analyzing and Internalizing Complex Policy Documents for LLM Agents (CAP-CPT) — https://arxiv.org/html/2510.11588v1
- [读] Towards Enforcing Company Policy Adherence in Agentic Workflows — https://arxiv.org/html/2507.16459
- [读] Models Recall What They Violate: Constraint Adherence in Multi-Turn LLM Ideation (DriftBench) — https://arxiv.org/html/2604.28031
- [摘要] Multi-IF: Benchmarking LLMs on Multi-Turn and Multilingual Instructions Following — https://arxiv.org/html/2410.15553
- [读] InvestPhilBench: A Multi-Layer Dynamic Benchmark for Evaluating LLM Procedural Reasoning in Expert Investment Philosophy — https://arxiv.org/html/2606.25984
- [读] On the limits and opportunities of AI reviewers: Reviewing the reviews of Nature-family papers with 45 expert scientists — https://arxiv.org/html/2605.20668v1
- [读] The Instruction Gap: LLMs get lost in Following Instruction — https://arxiv.org/html/2601.03269v1
- [摘要] GuardAgent: Safeguard LLM Agents by a Guard Agent via Knowledge-Enabled Reasoning — https://arxiv.org/pdf/2406.09187
- [摘要] Helping LLMs Improve Code Generation Using Feedback from Testing and Static Analysis (静态分析反馈提升code compliance, security issues从>40%降到13%) — https://arxiv.org/pdf/2412.14841


---

## 小模型微调辅助沉淀

## key_findings

### 1. Sleep-time compute(Letta+UC Berkeley)在相同准确率下把test-time compute削减约5倍，把sleep-time compute扩大后可在GSM-Symbolic上提升13%、AIME上提升18%；多查询共享同一context场景下平均成本削减2.5倍
证据: 双agent架构(primary+sleep-time agent)，sleep-time agent在空闲期反复调用rethink_memory()(最多10次)把'原始context'转成'学习后的context'，全程不更新任何模型权重。实验用GPT-4o/GPT-4o-mini(GSM-Symbolic)和OpenAI o1/o3-mini/Claude Sonnet 3.7/DeepSeek-R1(AIME)。核心前提是'query predictability'——用Llama2-70B的log概率量化查询的可预判程度，发现准确率提升幅度随可预判性增加而扩大；论文承认查询难预判或与context无关时该方法效果差。
来源: Sleep-time Compute: Beyond Inference Scaling at Test-time, arXiv:2504.13171 (https://arxiv.org/html/2504.13171v1)

### 2. STaR的核心循环是'自生成理由→筛选正确的→微调→迭代'，且理论上被形式化为'零一终止奖励的on-policy RL循环'，收敛保证的条件是初始预训练准确率要'显著高于随机'
证据: 对于模型答错的题目，STaR用rationalization机制——把正确答案喂给模型让它编出一个'看似合理'的推理路径，再拿这个理由去微调。这一机制本身要求任务有客观正确答案（数学/代码/SQL等），STaR-SQL、Lean-STaR是其在窄领域(Text-to-SQL/定理证明)的扩展。
来源: STaR: Bootstrapping Reasoning With Reasoning, arXiv:2203.14465 (https://ar5iv.labs.arxiv.org/html/2203.14465)；STaR相关扩展综述 (https://www.emergentmind.com/topics/self-taught-reasoning-star)

### 3. ReST(DeepMind)把在线RL拆成grow(生成候选)+improve(离线筛选微调)两个循环，目的是复用一次采样的数据做多轮离线RL改进，从而比在线RL(如PPO)更省算力，但实验聚焦机器翻译这类有自动评测指标(BLEU/reward model)的任务
证据: grow loop用当前policy生成大量候选输出，improve loop用打分函数(reward model)排序筛选后做离线RL训练，多次improve复用同一批grow数据。摘要层面强调'compute和sample-efficient'，但未能从可读来源拿到具体模型规模/BLEU提升数字（论文PDF解析失败，仅摘要可信）。
来源: Reinforced Self-Training (ReST) for Language Modeling, arXiv:2308.08998 (仅读到摘要，actually_read标false)

### 4. Aviary(FutureHouse)用behavior cloning+expert iteration训练Llama-3.1-8B agent，在窄科学任务(LitQA2文献问答/SeqQA)上匹配甚至超过前沿LLM和人类专家，推理成本最多降低100倍
证据: 任务是DNA构建分子克隆/科学文献问答/蛋白质工程稳定性设计，均为定义清晰、benchmark明确的窄科学任务，前提是有专家轨迹数据可供behavior cloning、且有客观评测标准衡量'对错'。
来源: Aviary: training language agents on challenging scientific tasks, arXiv:2412.21154 (https://arxiv.org/html/2412.21154v1)

### 5. ATLaS只用专家轨迹里约30%的'关键步骤'(规划/复杂推理/策略决策)微调Llama-3.1-8B-Instruct，效果超过用全部步骤微调；4张A100跑约8小时；held-in任务准确率从60.52%提升到65.91%，held-out从36.18%提升到38.36%
证据: 关键步骤的筛选依赖GPT-4o(付费API)做选择器，训练数据是AgentTraj-L(ALFWorld 2420条、BabyAI 810条等专家轨迹)。论文未讨论单卡/更小模型场景，未来工作明确提到'如何用更低时间和计算成本精确选出关键步骤'仍是开放问题。
来源: ATLaS: Agent Tuning via Learning Critical Steps, arXiv:2503.02197 (https://arxiv.org/html/2503.02197v1)

### 6. 全参数持续微调下，模型规模从1.1B到7.1B(BLOOMZ四档)，灾难性遗忘的严重程度递增——7.1B模型的领域知识遗忘18.37%、推理遗忘13.62%、阅读理解遗忘26.75%，均高于1.1B模型的9.54%/6.73%/18.04%；混入10000条通用指令数据可让LLAMA-7B的MMLU-human从26.8%恢复到30%
证据: 研究解释是大模型初始性能更强，在新任务上拟合造成的性能落差更明显。这项研究只测了全参数微调，未纳入LoRA/QLoRA对比，是这条证据链的明确局限。decoder-only架构(BLOOMZ)比encoder-decoder(mT0)在同等规模下遗忘更轻。
来源: An Empirical Study of Catastrophic Forgetting in Large Language Models During Continual Fine-tuning, arXiv:2308.08747 (https://arxiv.org/html/2308.08747v5)

### 7. LoRA对灾难性遗忘的缓解效果不是普遍规律——2025年多篇论文(LoRA-Null/OPLoRA/CLoRA)专门设计正交投影/子空间正则化去抑制LoRA本身可能引入的高幅度奇异方向偏离，说明默认超参下的LoRA仍可能显著遗忘，需要额外工程手段兜底
证据: OPLoRA用预训练权重在代表性激活的零空间上构造LoRA适配器，确保更新方向不干扰已有知识；CLoRA用子空间正则化。这些论文的存在本身说明'LoRA天然抗遗忘'不成立，需要专门设计。
来源: OPLoRA (arXiv:2510.13003)、CLoRA subspace regularization (arXiv:2410.16801)，均仅读到摘要/搜索结果摘录，actually_read标false

### 8. GEPA(反思式prompt优化)平均比GRPO(在线RL+LoRA)高6%、最高高20%，用少35倍的rollout；多数任务上GEPA只需300-400次rollout就追平GRPO最佳验证表现
证据: 任务集中在多跳问答(HotpotQA)、指令遵循(IFBench)、隐私保护委托(PUPA)、检索增强验证(HoVer)，都是相对窄的agentic任务。论文第8节明确写'权重更新在数据充足或可大规模rollout时可能超越prompt优化'，但没有给出量化的规模阈值，把这称为未来工作方向；论文没有和SFT/离线LoRA微调做直接对照，对比对象是GRPO(在线RL)和其他prompt优化方法(MIPROv2)。
来源: GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning, arXiv:2507.19457 (https://arxiv.org/html/2507.19457v1)

### 9. ACE(Agentic Context Engineering)把上下文当作可增量演化的'playbook'(Generator/Reflector/Curator三角色分工)，比GEPA在AppWorld离线任务上高11.9%、金融任务平均高8.6%，延迟比GEPA降82.3%、比Dynamic Cheatsheet降91.5%；但存在'context collapse'现象——第60步18282 tokens时准确率66.7%，第61步整体重写后骤降到122 tokens、准确率跌到57.1%
证据: ACE用增量式delta更新(只追加/合并小条目)而非整体重写来规避brevity bias(优化器倾向把prompt压成短小通用而丢失领域细节)和context collapse。论文附录B承认依赖'足够强的Reflector'提炼有意义洞察，'HotpotQA这类任务反而受益于简洁指令而非长context'，缺乏可靠反馈信号(ground truth或执行反馈)时性能下降，且没有和SFT/GRPO等权重微调方法做直接头对头对比，也没有量化'需要多少轨迹积累才见效'。
来源: Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models, arXiv:2510.04618 (https://arxiv.org/html/2510.04618v1)

### 10. Dynamic Cheatsheet(不改权重、纯测试时演化记忆)让Claude 3.5 Sonnet的AIME 2024准确率提升27个百分点，让GPT-4o在Game of 24任务上准确率从10%跃升到99%
证据: 机制是维护一份持久化、自适应的'速查表'(cheatsheet)，记录过往成功/失败的策略和启发式，检索时按余弦相似度取历史相关案例，三种变体(DR仅检索、DC-Cu仅累积、DC-RS检索+综合精炼)。这是目前查到的'明文沉淀'路线里效果最戏剧化的数字之一，但也是任务高度结构化(数学/逻辑谜题有客观对错)的场景。
来源: Dynamic Cheatsheet: Test-Time Learning with Adaptive Memory, arXiv:2504.07952 (通过搜索摘录获得，actually_read标false，未直接WebFetch原文)

### 11. cross-encoder专用重排模型比通用大模型做reranker更高效准确，THINKPRM-1.5B用极少训练数据就超过更大规模的现成PRM，测试时用process advantage verifier(PAV)搜索比outcome reward model准确率高8%以上、计算效率高1.5-5倍
证据: 理论条件是verifier只需要'能区分步骤好坏、且不与base policy偏离太远'，不要求verifier本身能力强于被验证的模型——这是'弱prover提升强policy'的理论依据。
来源: Rewarding Progress: Scaling Automated Process Verifiers for LLM Reasoning, arXiv:2410.08146(通过搜索摘录，actually_read标false)

### 12. 小模型做judge/reward存在能力地板——Qwen 3 0.6B在RewardBench 2的'Chat Hard'和'Safety'难任务上准确率不到50%，在某些情况下甚至不如随机；criteria injection+ensembling能让judge准确率提升13.5个百分点到85.8%，但需要1.3倍的推理成本
证据: 说明'用小模型辅助判断'不是无条件成立，存在任务难度和模型能力的匹配门槛，不能盲目下放判断职责给太小的模型。
来源: On Cost-Effective LLM-as-a-Judge Improvement Techniques, arXiv:2604.13717 (https://arxiv.org/html/2604.13717v1)，主要通过搜索摘录，actually_read标false

### 13. 单卡消费级显卡（RTX 3090/4090）跑QLoRA的真实成本：7B模型1000步(batch 4, 512上下文)约20-40分钟，13B模型在4090上约3-4小时；RTX 4090云端价格约$0.34-0.69/小时
证据: 这类实证针对的是'已有格式化训练数据、跑通一次训练'的场景，不包含数据准备/评测集构建的时间成本，这是评估我们真实门槛时必须补上的隐藏成本。
来源: 综合多篇实践指南(Compute Market、RunPod、JarvisLabs)的搜索摘录，actually_read标false，无法逐篇打开验证具体测试环境细节

## critical_assessment

### 1. Sleep-time compute(Letta/UC Berkeley, arXiv:2504.13171)
隐含前提: ①隐含前提是'query predictability'——同一段context会被多次、可预判地查询(如长文档反复问答、编程agent反复处理同一代码库),sleep阶段的'预判'才有回报;②论文实验全部用闭源大模型API(GPT-4o/o1/o3-mini/Claude 3.7/DeepSeek-R1)做sleep-time agent和primary agent,本身就依赖强模型的推理能力去做记忆重组,不是靠一个小模型;③论文自己承认'查询与context无关或难预判的场景'该方法效果差,且'高test-time compute预算时标准scaling可能更优'。
对我们: 我们是单用户长程项目(decision-record/voxelcraft),同一批决策上下文确实会被反复查询,'可预判性'前提部分成立;但我们没有团队级的高频交互量去justify专门起后台'睡眠agent'常驻空转,而且这条路线完全不涉及'微调进权重'——它属于我们已经在做的'账本/材料审阅台'范畴(留痕+整理),不是本路要评估的'微调小模型'路线。可用的是它的架构思路:双agent(前台交互/后台整理)分离,異步整理优于同步增量整理;可以直接对应我们决策库的'后台炼化札记'(governance decisions-run)已有的模式,而非引入新微调设施。

### 2. STaR/ReST自我训练系(自生成理由/轨迹→筛选→微调)
隐含前提: ①需要一个可自动判定对错的verifier/reward(数学答案、翻译BLEU、代码单测通过率),即'窄域+有明确correctness signal';②STaR的rationalization机制要求'给出正确答案后仍能生成看似合理的推理'，这在无标准答案的开放域决策/创作类任务上不成立;③ReST的'grow-improve'两阶段设计是为了摊薄在线RL的rollout成本,前提是已经承认RL/微调本身值得做，只是想省钱,而非论证'要不要微调'。
对我们: 我们的核心工作(决策记录、策划设计、写作审阅)绝大部分没有自动可判定的对错,不能直接套用STaR/ReST的'自动打分-筛选'环节；唯一可能沾边的是'voxelcraft战斗数值调参/配表校验'这类有确定性验证(能不能打完/是否越界)的子任务,理论上可以生成'轨迹→跑验证→挑对的→当训练数据'，但这仍然要求先有一个稳定跑得动的verifier管线(我们已有headless-auto-battle-sim这类),且要额外承担微调基础设施的成本。

### 3. 轨迹微调/behavior cloning(含Aviary专家迭代、ATLaS关键步骤微调、EEF)
隐含前提: ①Aviary的'8B agent超过前沿LLM且成本低100倍'是在窄科学任务(DNA克隆/文献问答/蛋白质工程)上、有大量专家轨迹和明确benchmark(LitQA2/SeqQA)的条件下成立,不是通用能力提升;②ATLaS用Llama-3.1-8B在4张A100上训8小时,还需要GPT-4o(付费API)做'关键步骤'筛选器,即训练管线本身依赖大模型辅助,并非'小模型自给自足';③EEF/多数轨迹微调论文都假设有'专家轨迹数据集'(数千条级别,如ALFWorld 2420条/BabyAI 810条)现成可用,我们目前没有这个量级的干净轨迹库。
对我们: 我们单人+单机的场景缺三样东西:(a)窄且可重复触发的任务(不像科学QA benchmark那样定义清楚);(b)数千条级别的专家轨迹积累(我们的决策记录目前是个位数/几十条级别的密度);(c)配套的4卡A100级算力(我们只有单工作站)。Aviary式'8B打赢前沿模型'的收益,需要先有窄任务+大轨迹量两个条件同时满足,目前都不满足。

### 4. LoRA/QLoRA持续学习缓解灾难性遗忘
隐含前提: ①'LoRA遗忘少于全参数微调'的结论有前提边界——查到的实证论文(2603.27707等)显示这不是普遍规律,超参选择不当(高幅度奇异方向偏离预训练权重)反而会加剧遗忘;②Luo et al.(arXiv:2308.08747)的核心实证是全参数微调(1.1B-7.1B四档BLOOMZ),不是LoRA,且明确发现'模型越大遗忘越严重'(7.1B比1.1B遗忘幅度几乎翻倍,领域知识18.37% vs 9.54%);③LoRA专门的持续学习论文用的多是缓解特定任务序列的遗忘,前提是'任务序列可枚举、每次微调有明确任务边界',而不是我们要的'持续从对话/决策中零散吸收经验'这种模糊、无边界的沉淀模式。
对我们: 对我们最扎心的一条是'模型越大遗忘越严重'——如果我们想用一个还算能干的7B-14B模型持续吸收经验,遗忘风险比更小的模型更高,而这正是我们唯一会考虑本地跑的规模区间。且我们查到的LoRA缓解方案(混入通用指令数据、预先通用指令微调)都要求维护一份'通用能力回放集'并持续对照评测防止跑偏,这本身就是一套不亚于微调本身的运维负担,单人很难长期维护。

### 5. 小模型辅助角色(PRM/reranker/judge)
隐含前提: ①cross-encoder重排/小型PRM(如THINKPRM-1.5B)优于通用大模型的前提是'任务范围窄且可离线预训练'(检索重排、数学步骤验证有明确ground truth或progress signal),不是'通用助理判断';②LLM-as-judge小模型研究明确给出'最低能力阈值'——Qwen 3 0.6B在难任务(Chat Hard/Safety)上准确率低于50%甚至不如随机,说明小模型辅助不是无脑降规模,存在一个能力地板;③process reward model的'弱prover提升强policy'理论结果要求prover'能区分步骤优劣但不能与base policy偏离太远'，这个对齐要求本身就是一个不低的门槛。
对我们: 我们的场景以API大模型为主力，'小模型辅助'这条最贴近现实的落点不是'训练一个PRM'，而是用现成的小模型/规则做检索重排或规则性预筛(比如决策库检索前的粗筛)，这不需要微调，只需要接入一个开源reranker(如cross-encoder)。真正的'训练PRM/judge'需要大量步骤级标注数据，我们没有标注人力，不满足前提。

### 6. GEPA/ACE式反思式明文优化 vs RL微调/权重更新
隐含前提: ①GEPA论文自己在第8节承认'边界条件理解不充分'，未系统给出'数据量/rollout预算/模型规模多大时该转向权重更新'的量化阈值，这是一个尚待研究的开放问题而非确定结论;②ACE论文的强项数字(AppWorld+11.9%、金融+8.6%、延迟降82.3%)都是和其他context方法(GEPA/MIPROv2/Dynamic Cheatsheet)比，不是和SFT/GRPO微调头对头比，'跑赢微调'目前主要是宣称与外推，不是这几篇论文的直接实验结论;③ACE明确依赖'足够强的Reflector'去提炼有效经验，且存在'context collapse'(实测从18282 tokens的66.7%准确率崩到122 tokens的57.1%)这种脆弱性，需要专门的防坍缩设计(增量式delta更新而非整体重写)。
对我们: 这条路线是最贴合我们现有决策库/审阅台体系的——我们本来就在做'决策记录+材料分层+反思式沉淀'，本质上就是明文playbook路线的一种实现。critical的警示是ACE的'context collapse'现象:如果我们的决策库汇总/压缩逻辑写得像'整体重写摘要'而不是'增量式追加+去重'，会重蹈同样的坍缩覆辙——这是一个可以现在就自查的具体风险点，而不是遥远的理论问题。

### 7. 单工作站7B-14B级QLoRA算力/时间成本
隐含前提: ①网上实证数据(7B在RTX4090上1000步20-40分钟、13B在RTX4090上3-4小时)针对的是'跑通一次训练'，前提是已经有现成的、格式化好的训练数据集，不包含'从零构建+清洗+标注决策库轨迹为训练样本'这个更耗时的环节;②'LoRA在小样本下也能泛化'的说法多来自特定风格/格式适配任务(如新闻分类、客服话术)，不是'吸收零散决策经验并保留通用推理能力'这种更难的目标;③几个指南提到'不需要大数据集也能有效'，但这类结论的任务通常是'风格模仿'或'格式约束'，跟我们想要的'内化决策经验、避免重复犯错'性质不同，后者更接近需要因果/条件泛化能力的任务，样本效率的参照系不一定适用。
对我们: 硬件门槛本身不是障碍——单卡消费级显卡确实能跑7B-13B的QLoRA，这条如果要做技术上可行。真正的瓶颈在于:(a)我们没有现成的、干净的、成规模的'决策轨迹训练集'（决策记录目前是叙事性文本，不是可直接当SFT样本的input-output对）；(b)没有独立于训练数据的评测集来验证微调后是否真的变好而非过拟合或遗忘（这需要额外维护一套回归benchmark，工作量不小于微调本身）；(c)单人验证'有没有变好'目前只能靠自己主观感受读结果，违反'便宜模型产出不免检'和'拒绝压缩数字'的既有工作纪律——没有金标benchmark时，微调效果是不可信的。

## transferable_mechanisms

1. **双agent异步整理架构(前台交互agent + 后台空闲期整理agent，各自可配不同模型)**
   落法: 对应我们已有的'omni governance decisions-run'后台炼化札记机制——可以明确把它定位成'sleep-time agent'角色:白天/交互中的决策记录只做最小化留痕(raw context)，由一个后台任务(不必是微调，可以是同一个API大模型在空闲时段跑)定期把决策库里的原始记录重组、去重、提炼成'学习后的上下文'（比如决策树的摘要节点、可复用的原则条目），写回决策库供下次检索。不需要新增微调设施，只需要明确排班这个'sleep pass'的触发条件(参照工作里程表而非自然时间)。

2. **增量式delta更新(playbook只追加/合并小条目，禁止整体重写摘要)以避免context collapse和brevity bias**
   落法: 直接适用于我们的决策库/审阅台内容治理——任何'压缩历史决策成摘要'的环节，都应改成增量合并(类似git的三路合并而非覆盖重写)，并且要有'长度不能突然从数千token崩到几十token'这种健康检查作为闸门，可以做成material-doctor或knowledge-audit里的一条具体规则,直接对应ACE论文实测的context collapse现象(18282→122 tokens、准确率66.7%→57.1%)。

3. **可预判性(query predictability)作为'要不要做额外沉淀投入'的判据**
   落法: 在决定某类经验值不值得'沉淀'（无论是明文还是微调）之前，先问'这类上下文/这类问题未来会被反复问到吗'。对voxelcraft试炼场，可以先梳理哪些决策类型是高频重复出现的(比如兵种平衡调参的取舍原则)，只对这些做主动沉淀,而不是对每次决策都同等投入整理成本——这是一个免费但常被忽略的筛选器，可以直接写进决策记录的分类规则里。

4. **窄任务+客观正确信号(verifier)是自我训练类方法(STaR/ReST/Aviary专家迭代)的入场券**
   落法: 在voxelcraft域里，唯一具备这个前提的子任务是数值/战斗验证类(能不能正常打完、是否越界、平衡性回归)——这类任务已经有headless-auto-battle-sim/content-audit-pipeline这类确定性验证管线。如果未来真要试'微调辅助'，应该从这类窄且可验证的子任务开始收集'轨迹→验证结果'对，而不是从策划案/叙事这类无客观对错的任务开始。

5. **先用现成小模型做检索重排/预筛(cross-encoder reranker)，不训练不微调**
   落法: 对我们的决策库检索(以及未来的探索路径可视化/material-graph)可以直接接一个开源cross-encoder做检索结果重排，作为'小模型辅助大模型'路线里唯一门槛低、不需要标注数据、立刻能落地的一步——这比训练PRM或微调agent现实得多，是最小可行的第一步候选。

6. **微调前必须有独立于训练数据的评测集（金标benchmark），否则效果不可信**
   落法: 这直接对应用户既有工作纪律'便宜模型产出不免检'和'拒绝压缩数字'——如果未来真走到'微调小模型辅助决策库'这一步，开工前必须先按plan_test_audit_binding_tdd的方式锚定：测试标准是什么、错误样本长什么样、由谁(非执行方)评审效果，而不是自己感觉'看起来更懂了'就算数。这也是当前最缺的先决条件——我们目前没有一份可复用的、独立的决策质量评测集。

## sources
- [摘要] Sleep-time agents | Letta Docs — https://docs.letta.com/guides/agents/architectures/sleeptime/
- [读] Sleep-time Compute | Letta (博客) — https://www.letta.com/blog/sleep-time-compute/
- [读] Sleep-time Compute: Beyond Inference Scaling at Test-time (arXiv:2504.13171) — https://arxiv.org/html/2504.13171v1
- [摘要] STaR: Self-Taught Reasoner Bootstrapping Reasoning With Reasoning (arXiv:2203.14465) — https://ar5iv.labs.arxiv.org/html/2203.14465
- [摘要] Self-Taught Reasoning (STaR) 综述 — https://www.emergentmind.com/topics/self-taught-reasoning-star
- [读] Reinforced Self-Training (ReST) for Language Modeling (arXiv:2308.08998) — https://arxiv.org/abs/2308.08998
- [摘要] DeepMind Researchers Introduce Reinforced Self-Training (ReST) - MarkTechPost — https://www.marktechpost.com/2023/08/24/deepmind-researchers-introduce-reinforced-self-training-rest-a-simple-algorithm-for-aligning-llms-with-human-preferences-inspired-by-growing-batch-reinforcement-learning-rl/
- [摘要] Mitigating Forgetting in Low Rank Adaptation (arXiv:2512.17720) — https://arxiv.org/pdf/2512.17720
- [摘要] OPLoRA: Orthogonal Projection LoRA Prevents Catastrophic Forgetting (arXiv:2510.13003) — https://arxiv.org/pdf/2510.13003
- [摘要] Controlled Low-Rank Adaptation with Subspace Regularization (CLoRA, arXiv:2410.16801) — https://arxiv.org/pdf/2410.16801
- [摘要] Low-Rank Adaptation Reduces Catastrophic Forgetting in Sequential Transformer Encoder Fine-Tuning (arXiv:2603.27707) — https://arxiv.org/pdf/2603.27707
- [读] An Empirical Study of Catastrophic Forgetting in Large Language Models During Continual Fine-tuning (arXiv:2308.08747) — https://arxiv.org/html/2308.08747v5
- [摘要] SciRerankBench: Benchmarking Rerankers Towards Scientific RAG — https://arxiv.org/html/2508.08742v1
- [摘要] Rewarding Progress: Scaling Automated Process Verifiers for LLM Reasoning (arXiv:2410.08146) — https://arxiv.org/abs/2410.08146
- [摘要] THINKPRM / Process Reward Models That Think — https://www.alphaxiv.org/overview/2504.16828v5
- [读] GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning (arXiv:2507.19457) — https://arxiv.org/html/2507.19457v1
- [读] Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models (arXiv:2510.04618) — https://arxiv.org/html/2510.04618v1
- [摘要] Dynamic Cheatsheet: Test-Time Learning with Adaptive Memory (arXiv:2504.07952) — https://arxiv.org/abs/2504.07952
- [摘要] Best GPU for Fine-Tuning LLMs 2026 — QLoRA & LoRA | Compute Market — https://www.compute-market.com/blog/best-gpu-for-fine-tuning-llm-2026
- [摘要] QLoRA on RTX 4090 in 2026: True Total Cost After 100 Training Runs — https://dev.to/jovan_chan_9500711396d4e6/qlora-on-rtx-4090-in-2026-true-total-cost-after-100-training-runs-vs-runpod-141m
- [读] Can Past Experience Accelerate LLM Reasoning? (arXiv:2505.20643) — https://arxiv.org/pdf/2505.20643
- [摘要] Is Fine-Tuning Dead? Discover Agentic Context Engineering for Model Evolution Without Fine-Tuning — https://eu.36kr.com/en/p/3504237709859976
- [读] Exploring Expert Failures Improves LLM Agent Tuning (arXiv:2504.13145) — https://arxiv.org/pdf/2504.13145
- [读] ATLaS: Agent Tuning via Learning Critical Steps (arXiv:2503.02197) — https://arxiv.org/html/2503.02197v1
- [读] Aviary: training language agents on challenging scientific tasks (arXiv:2412.21154) — https://arxiv.org/html/2412.21154v1
- [摘要] Reflexion: Language Agents with Verbal Reinforcement Learning (arXiv:2303.11366) — https://arxiv.org/abs/2303.11366
- [摘要] On Cost-Effective LLM-as-a-Judge Improvement Techniques (arXiv:2604.13717) — https://arxiv.org/html/2604.13717v1
- [读] Behavior Cloning 综述 (emergentmind) — https://www.emergentmind.com/topics/behavior-cloning


---

## 形式推理语言前景

## key_findings

### 1. 顶级Lean定理证明器2025年在miniF2F上已到70-90%区间, 但PutnamBench(更难的大学竞赛级)通过率仍很低
证据: DeepSeek-Prover-V2(671B)在miniF2F-test达到88.9%通过率, 但PutnamBench 658题中只解出49题(约7.4%); Kimina-Prover以pass@8192在miniF2F达到80.7%创当时纪录, 此前最优BFS-Prover为72.9%; Seed-Prover系列在IMO2025用16.5小时算力使前5题达到金牌线(35/42)。这些数字说明'看似很高的通过率'高度依赖题目难度分布和推理预算(pass@N和搜索时长), 并非通用意义上'AI已经很会证明'。
来源: MarkTechPost DeepSeek-Prover-V2 报道 https://www.marktechpost.com/2025/05/01/deepseek-ai-released-deepseek-prover-v2-an-open-source-large-language-model-designed-for-formal-theorem-proving-through-subgoal-decomposition-and-reinforcement-learning/ ; arXiv 2504.11354 (Kimina-Prover) ; ByteDance Seed博客 https://seed.bytedance.com/en/blog/seed-prover-1-5-advanced-mathematical-reasoning-through-a-novel-agentic-architecture

### 2. 自动形式化(NL→Lean)存在严重的'自评虚高'问题: LLM自称正确率97-98%, 人工核验后只有62.7%-90.6%; 端到端(形式化+证明)拿faithful翻译计分时准确率仅34.8%(而非分别取97%和70%相乘的假象)
证据: miniF2F-Lean Revisited论文实测: Herald Translator 'LLM自评97.5% vs 人工核验62.7%'; Kimina Autoformalizer 'LLM自评98.4% vs 人工核验90.6%'; 原因是'LLM把明显偏离原题意思的形式化也标记为正确'; 修正后的miniF2F-v2在'奥赛设定'下端到端准确率约70%, 显著低于分别报告的单项高分给人的印象。
来源: miniF2F-Lean Revisited https://arxiv.org/html/2511.03108v1 (WebFetch 实读全文摘要)

### 3. 税法(IRC Section 121)形式化实验中, 自然语言提示与Prolog增强提示检测法条内部矛盾的准确率打平(均为33%, 3个测试用例中1个), Prolog版本规则覆盖度反而更低(66% vs 100%); 唯一确定的增益是纯Prolog模型给出'确定且可复现'的判定, 而非准确率提升
证据: 论文对比GPT-4o/GPT-5在自然语言提示 vs Prolog增强提示下检测IRC Section 121竞争性解释矛盾的表现: 自然语言33%准确率/100%规则覆盖; Prolog增强33%准确率/66%规则覆盖; 纯Prolog模型'deterministic and reproducible'但依赖人工/LLM先把法条转成Prolog规则。
来源: LLM-Assisted Formalization Enables Deterministic Detection of Statutory Inconsistency in the Internal Revenue Code, arXiv 2511.11954 (WebFetch 实读摘要页 https://arxiv.org/abs/2511.11954)

### 4. 受控自然语言(Attempto ACE、Naproche)三十年来未在通用业务场景破圈, 文献归因为'重视解析翻译本身、轻视翻译结果下游怎么用'的工程问题, 以及'自然语言解释和形式语言解释在关键处悄悄分叉'的语义鸿沟问题——这不是算力/数据问题, 是设计问题, 不会因为换成LLM自动解决
证据: ACM Computing Surveys综述指出: 既有CNL工作'重视解析和翻译, 轻视对形式化结果的使用', 且'如果没有specific地借鉴具有实证验证的语言学意义理论, 很容易把CNL形式化成自然解释和形式解释在关键处悄悄分叉的样子'; CNL在需求规范上'被证明能产出更高质量、非技术人员能懂、且可自动分析的需求', 但'几乎没有实际应用证据和可用性证据'导致未被广泛采用。
来源: Trustworthy Formal Natural Language Specifications相关综述引用, WebSearch摘录 https://arxiv.org/pdf/2310.03885 及 ACM CSUR综述 https://dl.acm.org/doi/10.1145/3778169

### 5. 工业界对形式化方法的从业者共识: 形式化验证(如TLA+)擅长安全性/活性这类'非黑即白'的正确性属性, 完全无法回答时延/成本/过载行为这类性能与运维问题; 实践中只对'特别棘手、易出错的协议'做完整形式化, 而非全面覆盖
证据: Marc Brooker(AWS)博客原文: 'safety and liveness are only a small part of a larger overall picture. Many of the questions that designers face can't be adequately tackled with these methods'; 实践中'verification or model checking of these specifications is focused on safety and liveness'而非全面验证, 精力集中在'particularly tricky or error-prone'的协议上。
来源: Marc Brooker, "Formal Methods Only Solve Half My Problems" https://brooker.co.za/blog/2022/06/02/formal.html (WebFetch 实读全文)

### 6. '让形式化方法进入主流开发'的提案明确把'每周正的成本收益比'作为采用门槛, 目标是十年内采用率提升两个数量级——即形式化投入必须能在当前工作周期内就见效, 而非要求先搭建整套体系再谈回报
证据: 论文核心主张: 'meeting developers where they are'指'融入开发者现有技能和工作流, 用他们熟悉的技术和已经在产出的工件, 但用于新目的', 关键度量是'ensuring a positive weekly cost-benefit ratio for developer time invested'。
来源: Reid et al., "Towards making formal methods normal: meeting developers where they are", HATRA 2020, https://alastairreid.github.io/papers/HATRA_20/ (WebFetch 实读)

### 7. 把科学论证(物理学论证)自动形式化到Lean时, 即便题目本身已有精确记号(狄拉克记号、矢量微积分), LLM依然系统性出现'记号坍缩'和'抽象拔高'两种语义漂移模式, 导致翻译后的形式陈述'完美通过类型检查'但已经不是原论证——这是一个人机协同管线(FormalScience)在200题FormalPhysics数据集上明确记录并命名的失败模式
证据: 论文构建了200道大学物理题(量子力学、电磁学)及其人工核验的Lean4形式化, 记录方法学上'notational collapse'和'abstraction elevation'两类语义漂移, 说明'形式合法(编译通过)不等于语义忠实'在数理科学的自动形式化里是普遍现象, 需要人在回路(single domain expert)把关。
来源: FormalScience: Scalable Human-in-the-Loop Autoformalisation of Science with Agentic Code Generation in Lean, arXiv 2604.23002 (WebSearch摘录+WebFetch摘要)

### 8. 常识/日常推理的形式化(McCarthy一脉, 非单调逻辑框架)历经数十年知识表示研究, 至今没有产出'具备实质广度的成功常识推理系统', 根因是日常推理天然是非单调的(允许跳跃结论、随新证据收回结论), 与经典逻辑的单调证明范式存在根本张力, 不是靠更多算力/数据能解决的工程问题
证据: 综述指出'尚无一个具备实质广度的常识推理系统从知识表示研究中产生', 且'许多领域尚未被充分理解, 使得不清楚该如何以表示常识知识及定义有效推理机制的方式将其形式化'; 常识推理'需要跳跃结论并以非经典方式处理逻辑不一致', 因而被非单调逻辑建模, 其中部分是不可判定的(undecidable)。
来源: Current and Future Challenges in Knowledge Representation and Reasoning, arXiv 2308.04161(WebSearch摘录); 及常识推理形式化历史综述

### 9. 2025-2026年出现的低成本自动形式化案例: 单人两周用约100美元LLM订阅费产出13万行形式化拓扑学; miniF2F单题定理证明平均成本约0.516美元, CombiBench约0.83美元/题——说明'轻量数学自动形式化'已经廉价到个人可承担, 但这是在mathlib这种数十年积累的形式化库基础上做的, 而非从零建形式语言
证据: "130k Lines of Formal Topology in Two Weeks: Simple and Cheap Autoformalization for Everyone?"报告约100美元LLM订阅成本产出13万行(后续到16万行)形式化拓扑学内容; 另一项theorem proving成本核算给出miniF2F均价0.516美元/题、CombiBench约0.83美元/题, 均基于对已有大型formal math语料库(mathlib)的复用。
来源: arXiv 2601.03298 (WebSearch摘录, https://arxiv.org/pdf/2601.03298); 及相关成本核算WebSearch结果

## critical_assessment

### 1. AlphaProof / Seed-Prover / DeepSeek-Prover-V2 等顶级定理证明器在 miniF2F/PutnamBench/IMO 上的高分
隐含前提: 隐含前提: (1) 目标域是数学命题, 有 Lean/Isabelle 这类已存在几十年、被数万数学家共同打磨的形式语言和庞大库(mathlib); (2) 正确性判据是二元且客观的(编译器/证明检查器说了算), 不存在'这个证明对不对'的主观分歧; (3) 允许每题几小时到三天的重复搜索(AlphaProof/Seed-Prover 都是天级计算), 且允许失败后重来不产生现实后果; (4) 训练/推理有百万美元级算力支撑(DeepMind/字节量级); (5) 自动形式化语料(NL-FL 对照)以千万条计地被预先攒好(AlphaProof 8000万条自动形式化陈述)。
对我们: 我们场景: 单用户单工作站+API大模型, 无海量算力做天级搜索, 无预先积累的'决策语言→形式逻辑'平行语料, 更重要的是决策记录的正确性判据本身是主观的(这个决策'对不对'不像证明那样有编译器仲裁)。可用的只是其思路: 用一个可执行/可回放的检查器代替'AI自称做到了', 但不能照搬其重搜索、重语料、重算力的路线。

### 2. miniF2F-Lean Revisited 揭示的'LLM自评97%通过 vs 人工核验62.7%-90.6%'的自动形式化虚高问题
隐含前提: 隐含前提: 这个现象成立需要有'人工可核验'这个环节存在, 且核验者是懂形式语言的专家; 揭示的是LLM作为formalizer同时又是verifier时的自我认证偏差。
对我们: 对我们高度相关且是负面警示: 如果让LLM把决策记录'翻译'成某种形式化结构后又让LLM自己判断翻译对不对, 大概率重演这个虚高陷阱(自称做到但实际漂移)。我们没有形式语言专家做人工核验的资源, 意味着凡是我们采用的'形式化'环节, 检查必须是可执行的确定性检查(能跑起来复现的脚本/门禁), 而不能是另一个LLM说'翻译得对'。

### 3. Catala/Datalog/Prolog 形式化税法(IRC section 121)、GDPR 等法律条文, 用于捕捉法条内部矛盾
隐含前提: 隐含前提: (1) 目标文本本身就是为了被精确适用而写的规则集合(法条本来就追求排他性适用, 立法者本意接近逻辑规则), 天然比日常论证更'像程序'; (2) 形式化对象是静态、版本化、变更缓慢的文本(税法一年一修), 不是每天都在变的决策流; (3) 即便如此, 论文实测中 GPT-4o/GPT-5 用自然语言提示的不一致检测准确率只有 33%(3个测试用例中1个), 加了 Prolog 提示准确率同样是 33%但规则覆盖度反而降到 66%——形式化辅助并未带来准确率提升, 只带来了'确定性可复现'这一个好处。
对我们: 对我们的教训: 就算是最接近'天然可形式化'的法律文本, 目前LLM+形式化混合方案也没有在准确率上跑赢纯自然语言(税法案例准确率打平, 33%对33%), 唯一的增益是可复现性/确定性, 而非正确率。这意味着对决策记录这种更松散、更主观的对象, 不该期待形式化会'自动变得更准', 它能给的只是'同一段文本每次检查都得到同一个结论'这类工程性保证, 而不是让AI更聪明。

### 4. Attempto ACE / Naproche 受控自然语言(CNL)——用词法受限的'类英语'句子写规范, 背后自动映射到一阶逻辑/Prolog
隐含前提: 隐含前提: (1) 使用者愿意学习并遵守一套词汇和语法子集的写作纪律(这本身是训练成本); (2) 组织内有专人/流程做规范维护和查询(ACE定位是'领域专家和逻辑学家之间的桥', 隐含存在后者); (3) 需求文本是相对稳定、需要反复复核的合同/规范类文本, 而不是每天新增的探索性记录; (4) 文献指出其多年未被广泛采用的根因是'重视解析翻译本身、轻视翻译结果的下游使用'(工程问题)以及'自然解释和形式解释在关键处悄悄分叉'(语义鸿沟问题), 后者是纯设计问题不因换成LLM自动解决。
对我们: 对我们不成立的前提: 我们是单人工作、中文为主, 没有资源维护一套持续被检验的CNL词汇表, 也没有'逻辑学家'角色去把关语义分叉; 而且决策记录的价值在于低摩擦地记录探索过程, 强加CNL写作纪律会直接违背'降低记录摩擦'的初衷。可取的是其失败教训: 别做'重翻译、轻使用'的形式化, 形式化产物必须直接服务于后续被程序消费(检索/门禁/账本聚合), 否则会重蹈CNL三十年未破圈的覆辙。

### 5. Marc Brooker(AWS)对TLA+等形式化方法'只解决我一半问题'的从业者反思——形式化方法擅长安全性/活性验证, 不擅长时延/成本/过载行为等性能问题
隐含前提: 隐含前提: 该反思发生在大规模分布式系统工程语境, 已经有明确的协议/状态机可以建模, 且组织(AWS)已经具备把TLA+日常嵌入设计评审的工程文化和专职人力; 其'只对特别棘手、易错的协议才值得做完整形式化验证'的建议本身就是一种资源受限下的取舍指南。
对我们: 高度可迁移的一条: 即便在形式化方法最成熟的工业场景(分布式系统安全性), 从业者的共识也是'不要全面形式化, 只对易错/高风险的关键点做', 且形式化验证不能替代'依赖经验和判断'的那部分决策。这直接支持我们'挑关键决策点做门禁检查, 不追求全面形式化决策记录'的取舍。

### 6. 'Meeting developers where they are'(Reid et al., HATRA 2020) 提出的形式化方法要以'每周正的成本收益比'作为采用门槛, 目标十年内采用率提高两个数量级
隐含前提: 隐含前提: 目标群体是普通软件工程师(非形式化方法专家), 衡量单位是'每周投入的时间是否很快见效'; 隐含假设组织有意愿持续投入哪怕轻量的形式化实践, 且存在可以逐步加码的路径(从类型检查这类'已经在用的轻量形式化'起步)。
对我们: 完全适用: 我们是单人+AI全程参与, 没有专职形式化方法团队, 采用标准必须是'轻量起步、每次投入都要在当周就看到回报', 而不是先搭一整套形式语言再谈用途。这条支持我们从最轻量的结构化(字段化的决策记录+确定性门禁脚本)起步, 而非跳到重形式化。

### 7. FormalScience(物理学论证自动形式化到Lean)揭示的'语义漂移'(notational collapse记号坍缩、abstraction elevation抽象拔高)——LLM形式化物理论证时系统性丢失语义细节
隐含前提: 隐含前提: 该发现建立在物理学这种'已经半形式化'(有精确记号、方程、单位制)的学科上, 且有200道题目规模的人工核验数据集做对照; 换言之connect NL到FL的鸿沟即便在数理化这类'最接近形式逻辑'的学科内部依然存在系统性坍缩。
对我们: 警示意义大: 如果连有精确记号系统的物理学论证都会被LLM'翻译'时坍缩语义, 那么把更松散、更依赖上下文常识的'决策为什么这么做'翻译成形式结构, 语义漂移只会更严重。这支持我们对'LLM自动把决策记录形式化'保持谨慎, 只在决策记录中留存人类判断的'证据列表'而非强行结构化为可推导的逻辑命题。

### 8. commonsense/常识推理数十年未能被形式化(McCarthy一系脉络, 非单调逻辑, frame problem)
隐含前提: 隐含前提: 常识推理的形式化困境本身是历史悠久且未随算力/数据增长解决的'意义鸿沟'问题, 不是工程资源问题; 这类推理天然允许'跳跃结论并在证据更新后收回'(non-monotonic), 与经典逻辑的单调性根本冲突。
对我们: 非常关键的边界确认: 我们的决策记录本质上就是一种'跳跃结论+证据更新后收回'的非单调过程(belief 可证伪、可被新证据推翻), 这与形式逻辑的单调证明范式存在根本张力。这解释了为什么'决策记录'不该模仿'数学证明'的全形式化路线, 而应该模仿'可证伪科学假设'的路线(留痕+证伪机制), 这恰好是我们已经在做的(belief 证伪生命周期), 无需再引入额外的逻辑形式化层。

## transferable_mechanisms

1. **确定性检查器代替'另一个LLM说翻译对不对'——税法Prolog案例和miniF2F-Lean Revisited都表明, 只要验证环节还是LLM自我认证, 就会重演'自评虚高'; 唯一可信的验证是可执行、可复现的确定性程序(编译器/求解器/脚本), 不是自然语言复述式的'AI审阅'**
   落法: 我们的材料审阅台'确定性门禁'方向已经走对了路: 对每类产物(决策记录/账本条目)该配的不是'LLM读一遍说没问题', 而是能重复跑出同一结果的脚本(比如决策树的links是否指向存在的id、anchor是否真的可定位到原文、belief的证伪状态转换是否合法)。这条路线的证据支持我们继续往'脚本化门禁'投入, 而不是往'让LLM当形式化裁判'投入。

2. **'每周正成本收益比'作为采用形式化的门槛(Reid et al.), 对应到Brooker'只对棘手易错点做完整验证'的实践——两者共同指向一个可操作的分级策略: 只在决策记录里挑高风险/高复用价值的节点做重检查, 其余保持轻量自由文本**
   落法: 决策库不必对每条decision/belief都要求同等程度的结构化校验。可以分层: 大多数决策记录保持当前的自由字段+锚点(轻量), 只有被标记'高风险'或'被多次复用/被多条links引用'的决策节点才触发额外的确定性检查(比如要求该决策的anchor必须能重放到具体产物、其证伪条件必须写明可观测信号)。加码的触发信号=复用次数或下游影响面, 不是预先规定。

3. **非单调逻辑/常识推理的形式化失败史 + belief可证伪-可收回的设计, 说明'决策记录'应该模仿'可证伪科学假设'(留痕+新证据可推翻旧结论)的范式, 而非'数学证明'(单调、一旦证出不可撤销)的范式**
   落法: 我们已有的belief证伪生命周期机制本身就是针对非单调推理的正确设计选择, 调研反而验证了'不需要再往决策库上叠加一层逻辑证明式的形式化', 因为那会引入单调逻辑和非单调决策过程之间的根本冲突。下一步该做的是让'新证据推翻旧belief'这个动作本身留痕更结构化(谁在什么时间点用什么证据推翻了哪条belief), 而不是让belief本身变成可被逻辑推导的命题。

4. **FormalScience的'语义漂移分类学'(notational collapse记号坍缩/abstraction elevation抽象拔高)——即便編译通过/格式合法, 也可能已经偏离原意；这类漂移需要专门命名和检查点, 而不是靠'看起来结构对了就默认语义对'**
   落法: 给我们的决策动词词表和链接边一个警示: 当AI把一段自由文本决策'规整'成标准字段(选了哪个动词、连了哪条链)时, 同样可能发生'坍缩'(把复杂权衡压成一个动词标签)或'拔高'(把具体场景描述成过度抽象的通用原则)。可以在决策记录的评审环节里专门设一个检查点: 要求原始自由文本片段和结构化字段并存、可对照, 而不是结构化后丢弃原文, 方便日后发现漂移。

5. **低成本自动形式化案例(100美元两周13万行)证明的前提是'复用数十年积累的现成形式化库(mathlib)', 而不是从零建形式语言——对应到我们场景就是: 便宜不是来自形式化本身便宜, 而是来自'有没有现成骨架可以挂'**
   落法: 如果我们想给决策记录引入任何'结构化程度更高'的层, 性价比最高的做法是挂在我们已有的骨架上(决策动词词表v1、links边类型、anchor机制), 而不是设计一套全新的形式语法。任何新加的'形式化'字段都应该问: 这个字段是否复用已有词表/已有校验脚本, 还是又在造一个新词汇系统——后者性价比低, 前者才是这批调研支持的路线。

## sources
- [摘要] DeepSeek-Prover-V2 671B: Features, Benchmarks & Hosting Platforms Compared — https://www.marktechpost.com/2025/05/01/deepseek-ai-released-deepseek-prover-v2-an-open-source-large-language-model-designed-for-formal-theorem-proving-through-subgoal-decomposition-and-reinforcement-learning/
- [读] miniF2F-Lean Revisited: Reviewing Limitations and Charting a Path Forward — https://arxiv.org/html/2511.03108v1
- [摘要] Kimina-Prover Preview: Towards Large Formal Reasoning Models with RL (arXiv 2504.11354) — https://arxiv.org/pdf/2504.11354
- [读] AlphaProof / AI achieves silver-medal standard solving IMO problems — DeepMind blog — https://deepmind.google/blog/ai-solves-imo-problems-at-silver-medal-level/
- [摘要] Olympiad-level formal mathematical reasoning with reinforcement learning (Nature, AlphaProof paper) — https://www.nature.com/articles/s41586-025-09833-y
- [摘要] LeanDojo: Theorem Proving with Retrieval-Augmented Language Models — https://arxiv.org/abs/2306.15626
- [摘要] Attempto Controlled English (ACE) — ADS / arXiv — https://arxiv.org/pdf/cmp-lg/9603003
- [摘要] The Naproche Project: Controlled Natural Language Proof Checking of Mathematical Texts — https://naproche.github.io/publications.html
- [摘要] Controlled Natural Language for Requirements Specification: A Systematic Literature Review (ACM Computing Surveys) — https://dl.acm.org/doi/10.1145/3778169
- [摘要] Trustworthy Formal Natural Language Specifications — https://arxiv.org/pdf/2310.03885
- [读] LLM-Assisted Formalization Enables Deterministic Detection of Statutory Inconsistency in the Internal Revenue Code — https://arxiv.org/abs/2511.11954
- [摘要] Language Models and Logic Programs for Trustworthy Tax Reasoning — https://arxiv.org/pdf/2508.21051
- [读] Marc Brooker, "Formal Methods Only Solve Half My Problems" — https://brooker.co.za/blog/2022/06/02/formal.html
- [读] Towards making formal methods normal: meeting developers where they are (Reid et al., HATRA 2020) — https://alastairreid.github.io/papers/HATRA_20/
- [读] The Fusion of Large Language Models and Formal Methods for Trustworthy AI Agents: A Roadmap — https://arxiv.org/html/2412.06512v1
- [读] Autoformalization in the Era of Large Language Models: A Survey — https://arxiv.org/html/2505.23486
- [读] FormalScience: Scalable Human-in-the-Loop Autoformalisation of Science with Agentic Code Generation in Lean — https://arxiv.org/abs/2604.23002
- [摘要] Current and Future Challenges in Knowledge Representation and Reasoning (commonsense formalization history) — https://arxiv.org/pdf/2308.04161
- [摘要] 130k Lines of Formal Topology in Two Weeks: Simple and Cheap Autoformalization for Everyone? — https://arxiv.org/pdf/2601.03298
- [摘要] Seed-Prover 1.5: Mastering Undergraduate-Level Theorem Proving via Learning from Experience (ByteDance blog) — https://seed.bytedance.com/en/blog/seed-prover-1-5-advanced-mathematical-reasoning-through-a-novel-agentic-architecture


---

## 神经符号逻辑模型前景

## key_findings

### 1. AlphaGeometry是神经语言模型(生成辅助构造/候选)+符号演绎引擎(DD+AR代数推理算法)的循环协作,不是端到端神经网络,也不是纯符号系统
证据: 架构:符号引擎先做穷尽演绎,推不出结论时语言模型预测一个新的几何构造(点/线/圆)加入图中,再让符号引擎继续演绎,如此循环直到证出结论或到达迭代上限。训练数据是从10亿个随机生成的几何图形出发,用符号演绎穷举关系,筛出1亿个独特样本(其中900万含新构造)做语言模型的无监督训练,完全不用人类证明数据。运行1.5小时级别IMO难题需要4块V100+250个CPU核心;开源后社区评估认为要在普通家用硬件(4-8核CPU/16-32GB内存/无高端GPU)上一天内跑出同等效果,需要把效率再提升约100倍(尚未做到)。
来源: Google DeepMind官方博客 https://deepmind.google/blog/alphageometry-an-olympiad-level-ai-system-for-geometry/ ; GitHub google-deepmind/alphageometry README

### 2. AlphaProof是Gemini自动形式化+AlphaZero式强化学习在Lean定理证明器内搜索证明步骤的组合,AlphaGeometry2处理几何、AlphaProof处理代数数论,两者分工不重叠
证据: 流程分两阶段:先用微调过的Gemini把自然语言数学题自动翻译成Lean形式化语句(生成百万级形式化问题库),再用类AlphaZero的强化学习循环——求解网络在Lean里搜索证明/反驳步骤,每条被Lean验证通过的证明反过来强化语言模型能力。2024 IMO放榜结果:AlphaProof解出3道代数/数论题,AlphaGeometry2用19秒解出第4题几何题,共4/6题、28/42分,达银牌线;两道组合数学题仍未解出。局限明确写出:问题必须先人工/半人工转成Lean形式语言(耗时),训练耗费数周计算资源处理百万级问题。
来源: Google DeepMind官方博客 https://deepmind.google/blog/ai-solves-imo-problems-at-silver-medal-level/

### 3. 神经符号AI没有统一主流架构,学界公认的是Henry Kautz六分类法,且2025年才明显加速工业采纳(主要动机是治LLM幻觉)
证据: 六分类:Symbolic Neuro symbolic(如GPT类LM)/SymbolicNeuro/Neural|Symbolic(神经解释感知为符号,含Neural-Concept-Learner和AlphaProof)/Neuro:Symbolic→Neuro(符号系统生成训练数据供深度学习,含AlphaGeometry这类)/NeuralSymbolic(从符号规则生成神经网络,含Logic Tensor Networks)/NeuralSymbolic。原文明确写'不存在单一主流方法',开放问题包括最优架构组装方式、评估指标、神经模型表示能力、学习推理的原则性结合。亚马逊在Vulcan仓储机器人和Rufus购物助手中已落地(感知用神经、物理约束/操作规划用符号)。
来源: Wikipedia Neuro-symbolic AI https://en.wikipedia.org/wiki/Neuro-symbolic_AI

### 4. Scallop(基于Datalog的可微逻辑推理语言)是目前工程质量最扎实、速度最快的神经符号框架之一,但GitHub活跃度是几百星量级,且开发者自己在Hacker News上公开质疑实用性
证据: GitHub实测(2026-07-05抓取):scallop-lang/scallop仓库498星、32 fork、35个open issue,最后push在2026-06-26(仍活跃维护)。Hacker News 2025年3月讨论帖里,开发者评价是'工程质量确实扎实(有解释器+JIT编译到Rust动态加载为Python模块)',但也有人明确说'听起来很酷,但我还在琢磨它到底有什么实际用途'(Sounds cool but I'm still trying to figure out the practicality)。一篇2025年9月的框架实证对比论文(arxiv 2509.07122)测试MNIST求和/Shapes视觉问答/命名实体识别/数学性质推理四个玩具任务,结论是Scallop训练推理速度全场最快(如Shapes任务训练13.17ms vs DeepProbLog的755.13ms),但论文明确写'现有框架虽然可功能化,但缺乏用户友好库阻碍采纳','大多数框架把神经建模和符号-亚符号连接任务留给用户自己做',呼吁下一代框架该整合LLM来解决知识工程瓶颈。
来源: GitHub API实测 https://github.com/scallop-lang/scallop ; Hacker News https://news.ycombinator.com/item?id=43443640 ; arxiv 2509.07122 https://arxiv.org/html/2509.07122v1

### 5. DeepProbLog(神经网络+ProbLog概率逻辑编程)推理需要做精确加权模型计数,复杂任务上代价高到不实用;Logic Tensor Networks把一阶逻辑约束编译成GPU计算图但生态更小众
证据: DeepProbLog由Robin Manhaeve等人(KU Leuven)开发,GitHub ML-KULeuven/deepproblog实测366星、71 fork、11 open issue、最后push在2024-11-13(近一年未更新)。文献指出其'执行精确的加权模型计数计算,这使得它在复杂任务上代价高到不现实'。Logic Tensor Networks把逻辑公式编码进神经网络学习项编码和权重,常与LYRICS、Tensorlog并列,GitHub上有多个并行仓库(scallop-lang生态外),分裂度高、无单一权威仓库。
来源: GitHub API实测 https://github.com/ML-KULeuven/deepproblog ; arxiv 2304.04812(Scallop论文) https://arxiv.org/abs/2304.04812

### 6. Popper(现代归纳逻辑编程ILP系统)把整个ILP搜索问题编码成ASP/SAT/CSP求解器实例,2024-2025年有面向噪声标签的Propper扩展和在ARC(抽象推理语料库)上的应用尝试,GitHub活跃度是几百星量级
证据: Popper用'生成-测试-组合-约束'架构:用答案集编程(ASP)基于背景知识生成候选谓词,用正负例评估候选,靠移除失败候选的泛化/不完整预测的特化来剪枝搜索空间。GitHub logic-and-learning-lab/Popper实测306星。Propper是2024年的扩展,建立在Popper-MaxSynth基础上加入神经符号推理处理噪声标签和不确定背景知识。2025年有论文尝试用ILP(含Popper)解ARC(抽象推理语料库)问题,是把ILP用到'类人抽象推理'这个更宽任务上的探索性尝试,不是主流做法。
来源: GitHub API实测 https://github.com/logic-and-learning-lab/Popper ; arxiv搜索结果摘要(SAGE期刊2025 Program Synthesis Using ILP for ARC,arxiv 2408.11367 Propper)

### 7. 知识图谱规则学习(从数据里学出符号规则)是神经符号里相对成熟、有实际数据支持的子方向,AMIE/AnyBURL等纯符号方法在预测质量上已能和向量嵌入方法竞争,2025年有显著的规则集压缩进展
证据: AMIE用关联规则挖掘探索知识图谱里的频繁模式,AMIE+/AMIE3引入剪枝优化使其可扩展到大型知识图谱;AnyBURL是另一路对照系统。可微规则学习方面Neural-LP用RNN为每步生成不同关系的可能性,DRUM在此基础上用低秩近似取得更好结果。2025年的工作(具体见Rule Learning for Knowledge Graph Completion综述条目)显示可以把规则集压缩70%-96%、获得31倍加速,同时保留平均91%的基线性能,即使只用AnyBURL这类SOTA方法所需规则量的一小部分。
来源: WebSearch摘要综合(AMIE/AnyBURL/Neural-LP/DRUM相关综述条目,含arxiv 2408.05773 Neurosymbolic Methods for Rule Mining、ACM Web Conference 2025 Transfer Rule Learning论文标题及摘要),注:此条为WebSearch摘要级信息,未逐篇WebFetch精读全文,实际读取深度低于AlphaGeometry/AlphaProof/Scallop等条目

### 8. 神经符号方法在ARC-AGI(抽象与推理语料库,测试类人抽象推理泛化能力的基准)上和纯LLM、程序合成方法一样,从ARC-AGI-1到ARC-AGI-2普遍出现2-3倍性能下滑,说明组合泛化是所有路线的共同短板而非LLM独有问题;但神经符号方法目前的绝对分数明显落后纯大模型
证据: 2025年一篇综述分析82种方法后发现:程序合成、神经符号、纯神经三类方法在ARC-AGI-1到ARC-AGI-2上都出现2-3倍分数下滑。具体数字对比:纯大模型侧Opus 4.6在ARC-AGI-1上到93.0%,ARC-AGI-2掉到68.8%,ARC-AGI-3只有13%,Gemini 3 Deep Think在某版本上84.6%;神经符号/程序合成侧,一个融合向量符号代数(VSA)与对象中心程序合成的方案在ARC-AGI-1-Train上仅10.8%、ARC-AGI-1-Eval上3.0%;另一个用transformer提议DSL原语来收窄符号搜索空间的Neuro-Symbolic ARC(NSA)方案比之前SOTA提升27%,ARC Prize 2025最好的神经符号类方案NVARC也只有24%。也就是说在这个具体基准上,神经符号方法的绝对分数目前远低于顶尖纯LLM推理模型,'2-3倍衰减'这个共性结论不能等同于神经符号更抗衰减。
来源: WebSearch摘要综合(The ARC of Progress towards AGI综述 arxiv 2603.13372、Vector Symbolic Algebras for ARC arxiv 2511.08747),注:此条为WebSearch摘要级信息,未逐篇WebFetch精读,数字来自搜索引擎摘要,建议后续如需引用需二次核实原文

### 9. 神经符号方法目前在实践中几乎找不到'单人/业余开发者用它做成过实际东西'的公开案例,应用集中在学术论文和少数企业内部落地(亚马逊Vulcan/Rufus),缺乏面向个人开发者的友好工具链
证据: 多轮针对性搜索(关键词含hackathon、indie developer、solo project、game、tool,组合Scallop/DeepProbLog/ProbLog/answer set programming)均未找到真实的单人业余项目案例。唯一找到的相关活动是2016年UT Dallas办的HackAI(用s(ASP)系统做推理应用),距今近十年且非近期活跃社区。2025年的框架实证对比论文(arxiv 2509.07122)直接指出'神经符号领域的学习曲线陡峭,缺乏用户友好的工具、库和统一框架','复杂度仍是普通开发者采纳的障碍'。DeepProbLog、Scallop、Logic Tensor Networks三个仓库里,只有Scallop在2026年年中仍有活跃提交,其余GitHub活跃度(star数百、近一年少更新或无更新)也印证社区规模小。
来源: GitHub API实测(scallop-lang/scallop、ML-KULeuven/deepproblog、logic-and-learning-lab/Popper stargazers/pushed_at字段) ; arxiv 2509.07122 https://arxiv.org/html/2509.07122v1 ; WebSearch多轮针对性检索无正面结果

## critical_assessment

### 1. 神经符号(神经+符号混合)整体路线用于'训练逻辑模型/系统性推理'
隐含前提: 隐含前提:(1)存在一个可形式化的窄域符号系统(几何公理、Datalog规则、一阶逻辑约束)作为符号引擎的地基,这本身要靠专家人工设计或已有形式化知识库;(2)训练神经组件需要能自动'穷举生成'大量(百万到十亿级)合成训练样本,这个生成器本身要求该领域可被符号引擎完全穷举求解(如AlphaGeometry靠符号引擎从10亿随机图形反推100万可学样本);(3)需要有工程团队维护'神经-符号双向接口'(可微分逻辑/DD+AR/Lean交互协议)这类脏活;(4)评估目标本身是封闭、可判定正确性的(几何证明/Datalog查询结果/ILP假设的真假),而不是开放式自然语言任务。
对我们: 我们是单用户+API大模型为主+无RL基建+中文工作语言+个人软件工厂场景。上面四个前提在我们这里几乎都不成立:(1)我们的决策库/审阅台/账本体系不是一个'可形式化穷举'的窄域,是开放式、语言驱动、语义模糊的治理场景;(2)我们没有能力去构建'穷举生成器'来产生百万级合成训练样本——这本身需要专门的领域符号引擎作为起点,而我们没有;(3)我们没有工程资源去维护Rust级别的可微分推理运行时或Lean交互层;(4)我们的'正确性'判据(某决策是否复用得当、某材料是否达标)本质是主观、可争议的,不像几何证明那样非黑即白可判定。结论:整体路线的成功配方(窄域+海量合成数据+专用求解器+封闭判据)在我们场景里没有对应物,直接照搬不可行。

### 2. AlphaGeometry/AlphaProof的'神经产生候选+符号做严格验证'配方
隐含前提: 隐含前提:(1)存在一个'足够强的符号后端'能在给定候选后独立完成严格演绎(DD+AR演绎引擎、Lean证明检查器),神经网络只负责'指路'不负责'把关';(2)符号后端的验证是快速且确定性的,能在训练循环里被反复调用几百万次(强化学习自对弈);(3)AlphaGeometry用4×V100+250 CPU、AlphaProof用数周计算做训练,这是Google级别算力;(4)问题域可自动形式化(自然语言数学题→Lean语句),AlphaProof用微调Gemini做这一步,这本身也是一个专门训练过的大模型能力。
对我们: 我们的决策库场景没有'能独立把关'的符号验证器——判断一条决策记录是否'对'、一次探索是否'有价值',没有类似Lean/DD+AR那样确定性的检查程序。同时我们完全没有4×V100+250CPU或数周算力做强化学习自对弈,单台Windows工作站+API模型的量级差了几个数量级。可迁移的部分反而是这套'配方的抽象结构'(候选生成器+确定性检查器+循环验证)本身,而不是具体的神经符号技术栈——这一点在下面'可迁移机制'里展开。

### 3. '神经符号能以远低于纯LLM的算力/数据实现同等或更好效果'(Neurosymbolic AI as antithesis to scaling laws一文的核心宣称)
隐含前提: 该文的强证据(PhysORD节省99%数据、Ctrl-G 9B超越175B-1T模型)全部来自结构化/物理规律明确/可形式化的窄任务(物理约束系统、受控文本生成配合有限自动机),隐含前提是任务本身存在可写成显式符号约束的'结构'(物理定律、语法自动机、知识图谱模式),神经网络只需要在这个已知结构内做插值/选择,而不需要发现结构本身。该文自己也承认对非结构化推理任务论证薄弱,且未讨论符号知识从哪来(手工编码还是自动提取)这一可扩展性关键问题。
对我们: 我们的核心工作(决策语义、探索路径、材料审阅)是非结构化、需要发现结构而非套用已知结构的场景,不满足该文证据成立的前提。'低算力也能好'这个卖点对我们没有直接适用性——我们的瓶颈不是算力,而是没有可形式化的符号骨架可用。

### 4. 知识图谱规则学习(AMIE/AnyBURL/Neural-LP/DRUM从数据里学出符号规则)
隐含前提: 隐含前提:(1)存在一个结构良好的三元组知识图谱(实体-关系-实体)作为学习底座,规则挖掘是在这个已有图结构上做频繁模式统计;(2)评测目标是'链接预测'(缺失三元组补全),是一个有明确ground truth可算MRR/Hits@k的封闭任务;(3)规则形式局限于Horn子句(如果A且B则C)这类简单蕴含,不涉及更复杂的语用/语境判断。
对我们: 我们的决策库如果未来建成'决策-链接-决策'的图结构(links边),这条路径理论上有对应可能性——但目前我们的决策记录是自然语言驱动的语义内容,尚未形成密集的、适合频繁模式挖掘的三元组图谱规模。这条子方向是几条路线里前提条件相对最接近我们未来可能形态的,但目前时机未到,现在直接套用为时过早。

### 5. 单人可玩性:Scallop/DeepProbLog/Popper/LTN等具体框架的上手与落地
隐含前提: 这些框架的可用性前提是:(1)使用者具备Datalog/ProbLog/一阶逻辑/ASP的符号编程背景,愿意为每个新任务手写符号规则和背景知识;(2)接受当前生态'缺用户友好库'的现实,自己搭建神经-符号连接胶水代码;(3)任务恰好落在这些框架设计针对的模式(视觉关系推理、简单NER、数字运算)范围内。
对我们: 我们是单人+中文工作语言+个人软件工厂,不具备专职符号编程投入,而实测证据(GitHub几百星量级、近一年少更新、Hacker News开发者自己质疑实用性、2025年综述论文直言'缺乏用户友好库阻碍采纳')说明这些框架目前连英语母语的学术/工程社区都还没跑出好用的模式,更不用说我们这种业余中文场景。找不到任何单人业余项目案例这一点,是最直接的'现在不能用'信号。

## transferable_mechanisms

1. **候选生成器(神经/大模型)+确定性检查器(符号/规则)+循环验证,而非神经网络独自输出结论——AlphaGeometry/AlphaProof的核心配方抽象出来就是这个结构**
   落法: 在决策记录/审阅台体系里,可以把'AI生成一条决策/一次探索建议'当作神经组件的候选生成,再挂一层轻量'确定性检查器'(不是复杂符号推理,而是简单可判定的规则,比如:该决策是否引用了真实存在的历史决策id、链接边是否闭环、anchor是否指向真实存在的材料路径、证伪条件是否可被自动检测的脚本判定)。这类似把决策管线里已经在做的'材料层确定性门禁'思路,往前推一步应用到决策记录本身的结构完整性校验,而不需要引入完整的神经符号技术栈。

2. **训练数据自产自销:符号引擎穷举生成海量样本供神经组件学习,而非依赖稀缺人工标注(AlphaGeometry从10亿随机图形自动生成100万训练样本)**
   落法: 对我们场景的映射不是'生成合成训练数据来训练一个新模型'(我们没有训练基建),而是这个思路本身提醒我们:决策库的复用价值来自'能被自动重放/自动验证的历史',如果决策记录能挂上可执行的验证脚本(比如某决策的证伪条件写成一个可运行的检查),就相当于给每条决策配了一个'穷举验证器',让未来的AI agent在决策复用时能自动确认历史决策当前是否仍然成立,而不需要重新靠人工判断。这是'方法论借鉴'而非'技术栈借鉴'。

3. **知识图谱规则学习(AMIE/AnyBURL/Neural-LP从图数据里挖掘Horn子句规则)对应到决策树/探索路径可视化的links边结构**
   落法: 如果决策库的links边(决策树/探索路径)未来积累到足够密度,规则挖掘思路(频繁模式统计出'什么样的决策类型经常导向证伪'或'哪类anchor组合历史复用率高')可以作为决策库健康度分析的一个简单统计工具,用轻量频繁模式统计(不需要上完整ILP框架)先验证有没有信号,而非直接引入Popper/AMIE这类专用系统。这是当前唯一在'前提条件'上离我们较近的子方向,但建议先用轻量统计工具试探信号,而非直接采购重型框架。

4. **Henry Kautz六分类法里的'Symbolic[Neuro]'模式(符号系统调用神经组件做局部决策,如AlphaGo用符号级MCTS树搜索调用神经网络做局面评估)**
   落法: 这个模式和我们已经在做的'管线里用规则/脚本做流程骨架,关键节点调用LLM做语义判断'高度类似(比如omni run 管线的确定性步骤+agent节点混合)。这条'可迁移机制'的价值在于:确认我们现有的'规则骨架+LLM节点'管线设计模式,本身就已经是神经符号谱系里一种成熟、被工业验证过的模式(AlphaGo式),不需要额外引入新技术栈去'补齐'神经符号能力——我们已经在用它的精神,只是没有用这个名字。

## sources
- [读] AlphaGeometry: An Olympiad-level AI system for geometry — Google DeepMind — https://deepmind.google/blog/alphageometry-an-olympiad-level-ai-system-for-geometry/
- [读] AI achieves silver-medal standard solving International Mathematical Olympiad problems — Google DeepMind — https://deepmind.google/blog/ai-solves-imo-problems-at-silver-medal-level/
- [读] Neuro-symbolic AI - Wikipedia — https://en.wikipedia.org/wiki/Neuro-symbolic_AI
- [读] Neurosymbolic AI as an antithesis to scaling laws - PMC (NIH) — https://pmc.ncbi.nlm.nih.gov/articles/PMC12084822/
- [读] Neuro-Symbolic Frameworks: Conceptual Characterization and Empirical Comparative Analysis (arxiv 2509.07122) — https://arxiv.org/html/2509.07122v1
- [读] GitHub - scallop-lang/scallop (API实测:498星/32 fork/35 open issue/2026-06-26最后push) — https://github.com/scallop-lang/scallop
- [读] GitHub - ML-KULeuven/deepproblog (API实测:366星/71 fork/11 open issue/2024-11-13最后push) — https://github.com/ML-KULeuven/deepproblog
- [读] GitHub - logic-and-learning-lab/Popper (API实测:306星) — https://github.com/logic-and-learning-lab/Popper
- [摘要] Scallop – A Language for Neurosymbolic Programming | Hacker News讨论摘要 — https://news.ycombinator.com/item?id=43443640
- [摘要] Scallop: A Language for Neurosymbolic Programming (arxiv 2304.04812) — https://arxiv.org/abs/2304.04812
- [读] Popper GitHub仓库搜索结果(logic-and-learning-lab/Popper及相关小项目) — https://api.github.com/search/repositories?q=popper+inductive+logic+programming
- [摘要] Neurosymbolic Methods for Rule Mining(arxiv 2408.05773)及知识图谱规则学习相关综述摘要 — https://arxiv.org/pdf/2408.05773
- [摘要] The ARC of Progress towards AGI: A Living Survey of Abstraction and Reasoning (arxiv 2603.13372) — https://arxiv.org/abs/2603.13372
- [摘要] Vector Symbolic Algebras for the Abstraction and Reasoning Corpus (arxiv 2511.08747) — https://arxiv.org/html/2511.08747
- [摘要] GitHub - google-deepmind/alphageometry README及依赖 — https://github.com/google-deepmind/alphageometry
- [摘要] GitHub - tpgh24/ag4masses: Making AlphaGeometry accessible to the masses(社区轻量化尝试) — https://github.com/tpgh24/ag4masses


---

