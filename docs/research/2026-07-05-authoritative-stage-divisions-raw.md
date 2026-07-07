<!-- [OMNI] origin=claude-code domain=domains/decisions ts=2026-07-05T00:00:00Z type=research status=active -->

# 权威框架的阶段/维度划分——四路原始材料(2026-07-05)

> 综合报告=docs/reports/权威框架阶段划分对照与采纳建议-2026-07-05.md。
> 四路 agent 取各权威框架的**原文划分**(逐字, 英文原词保留); 每个框架带 划分对象/权威性证据/局限;
> [读]=实际打开读过原文, [摘要]=只见搜索摘要(其中 OpenAI 五级与 TechRxiv 三分类未取到一手原文, 已在各自条目里明示)。

## 自我进化agent综述

## frameworks

### F1. Self-Evolution 概念框架:四阶段迭代循环(Experience Acquisition / Experience Refinement / Updating / Evaluation)
来源: Tao, Z. et al. "A Survey on Self-Evolution of Large Language Models." arXiv:2404.14387 (提交2024-04-22,v2修订2024-06-03). https://arxiv.org/abs/2404.14387
划分对象: LLM 自我进化(self-evolution)——即模型不依赖人类/外部模型监督、自主从自身生成的经验中学习和提升的整个迭代过程——划分为一个周期内的连续阶段
划分:
  - Experience Acquisition(经验获取): 原文定义句 "the model identifies an evolution objective E^t, embarks on new tasks T^t, generates solutions Y^t"(模型确定一个进化目标 E^t,着手新任务 T^t,生成解答 Y^t)
  - Experience Refinement(经验提炼): 原文定义句 "the model examines and refines these experiences, discarding incorrect data and enhancing imperfect ones"(模型审查并提炼这些经验,舍弃错误数据、增强不完善的数据)
  - Updating(更新): 原文定义句 "the model undergoes an update process, integrating refined experiences into its framework"(模型经历一个更新过程,把提炼后的经验整合进自身框架)
  - Evaluation(评估): 原文定义句 "the model's performance is assessed through evaluation in external environment"(模型的表现通过在外部环境中的评估来衡量)
权威性: 配套 Abstract 原句逐字确认:"we first propose a conceptual framework for self-evolution and outline the evolving process as iterative cycles composed of four phases: experience acquisition, experience refinement, updating, and evaluation." 引言中再次原句重述:"This self-evolution is characterized by an iterative cycle involving experience acquisition, experience refinement, updating, and evaluation, as shown in Figure 2." 作者团队含阿里巴巴通义实验室(Tongyi Lab)、浙大等;配套 Awesome List 仓库 github.com/AlibabaResearch/DAMO-ConvAI/tree/main/Awesome-Self-Evolution-of-LLM 由阿里达摩院 Conversation AI 团队维护,约1.6k star;Semantic Scholar 显示约30次引用(截至检索时)。是本方向已知最早明确提出"四阶段迭代循环"框架的综述之一,被后续多篇综述(如 System1/System2融合综述 2407.08642、AGI综述 2405.10313 等)引用讨论。
局限: 仅为 arXiv 预印本,未见正式会议/期刊接收记录;四阶段本身较通用抽象(获取-提炼-更新-评估),更像一个可复用的概念容器而非严格互斥的操作定义,具体子维度(如 Task Evolution 分 Knowledge-Based/Knowledge-Free/Selective,Updating 分 In-Weight/In-Context)是论文自设的二级分类法,权威性弱于顶层四阶段本身;引用量(约30)相对本方向后来的综述不算特别高。

### F2. Self-Evolving Agents 三/四维划分法:What / When / How(/ Where)to Evolve
来源: Gao, H. et al. "A Survey of Self-Evolving Agents: What, When, How, and Where to Evolve on the Path to Artificial Super Intelligence." arXiv:2507.21046(提交2025-07-28,最新v4版本2026-01-16改标题加入"Where"). https://arxiv.org/abs/2507.21046 ,GitHub: github.com/CharlesQ9/Self-Evolving-Agents
划分对象: 自我进化型 agent(self-evolving agent)研究领域整体——即让 agent 能持续适应新任务/环境/交互的各类技术——按"进化什么/何时进化/如何进化/在哪进化"四个正交问题维度切分
划分:
  - What to evolve(进化什么,Section 3): 原文定义句 "A self-evolving agent differs from a static agent not by *what* components it contains, but by *which internal states* can be autonomously modified based on its own trajectories, reflections, and feedback signals." 子类:Models、Context(含 Memory Evolution / Prompt Optimization)、Tools、Architecture(含 Single-Agent System Optimization / Multi-Agent System Optimization)
  - When to evolve(何时进化,Section 4): 原文定义句 "We divide existing evolving methods according to different temporal stages with different learning paradigms such as supervised fine-tuning, reinforcement learning and inference-time evolving." 子类:Intra-Test-Time Self-Evolution、Inter-Test-Time Self-Evolution(各下含 In-Context Learning / Supervised Fine-Tuning / Reinforcement Learning)
  - How to evolve(如何进化,Section 5): 原文定义句 "We finally summarize different signals to guide the evolution of agents, such as textual feedback or scalar rewards, and also different architectures of agents to evolve." 子类:Reward-based Self-Evolution、Imitation and Demonstration Learning、Population-based and Evolutionary Methods、Cross-cutting Evolutionary Dimensions
  - Where to evolve(在哪进化,Section 6,v3/v4版新增于标题但摘要仍称"三个基础维度"): 原文定义句 "We also examine emerging applications in domains such as coding, education, and healthcare, where continual adaptation and evolution are essential." 子类:General Domain Evolution、Specialized Domain Evolution(Coding/GUI/Financial/Medical/Education/Others)
权威性: 摘要逐字原句(v4版):"This survey provides the first systematic and comprehensive review of self-evolving agents, organizing the field around three foundational dimensions: what, when, and how to evolve." 自称"首篇系统全面综述"。作者团队达27人,含 UIUC 的 Heng Ji、Princeton 的 Mengdi Wang、Penn State 的 Qingyun Wu 等知名 agent/LLM 研究者;配套 GitHub 仓库 github.com/CharlesQ9/Self-Evolving-Agents 约932 star(2025-08 上线20天内破600 star,见 LinkedIn/EvoAgentX 转发);OpenReview 页面显示已投稿评审(id=CTr3bovS5F)。是本方向2025年下半年被广泛引用/转载的代表性综述(HuggingFace Papers、多篇 Substack/博客解读)。
局限: 标题与摘要不一致:v3/v4标题已改为"What, When, How, and Where to Evolve"四词,但摘要正文仍写"three foundational dimensions",Where 维度像是后期版本追加、尚未完全整合进顶层框架表述,存在框架本身仍在演化中的迹象;四个维度之间有交叉(如 Architecture 下的 Multi-Agent 与 Where 维度的 Specialized Domain 有重叠),边界不是严格互斥。

### F3. Self-Evolving Agentic Systems 统一概念框架:四组件反馈环(System Inputs / Agent System / Environment / Optimisers)
来源: Fang, J. et al. "A Comprehensive Survey of Self-Evolving AI Agents: A New Paradigm Bridging Foundation Models and Lifelong Agentic Systems." arXiv:2508.07407(提交2025-08-10,修订2025-08-31). https://arxiv.org/abs/2508.07407 ,GitHub: github.com/EvoAgentX/Awesome-Self-Evolving-Agents
划分对象: 自我进化 agentic 系统(self-evolving agentic system)背后的反馈闭环(feedback loop)本身,划分为四个抽象组件而非阶段
划分:
  - System Inputs(系统输入): 经检索确认的原文措辞 "System inputs refer to the contextual information and data provided to the optimization process"(系统输入指提供给优化过程的上下文信息和数据),细分为 task-level(任务级,含任务描述与训练数据集)与 instance-level(实例级,含具体输入输出对)两种设定
  - Agent System(agent 系统): 综述称其为"受优化对象"(subject to optimization)的核心组件,涵盖单 agent 架构(prompt、memory、tools、LLM)与多 agent 配置(角色、通信方式、拓扑)——此条未能获取论文原文逐字定义句,以下引号内容来自间接检索非直接原文核实
  - Environment(环境): 检索确认的措辞 "the external context in which the agent system operates"(agent 系统运作所处的外部上下文),关键作用是产生 feedback signals(反馈信号)供优化使用——同样未能取得完整原文逐字定义句核实
  - Optimisers(优化器): 经检索确认的原文措辞 "Optimisers are the core component of the self-evolving feedback loop, responsible for refining the agent system based on performance feedback from the environment",并由 search space(搜索空间)与 optimisation algorithm(优化算法)两要素定义
权威性: 摘要逐字原句确认:"we first introduce a unified conceptual framework that abstracts the feedback loop underlying the design of self-evolving agentic systems. The framework highlights four key components: System Inputs, Agent System, Environment, and Optimisers, serving as a foundation for understanding and comparing different strategies." 配套 GitHub 仓库 EvoAgentX/Awesome-Self-Evolving-Agents 上线20天内突破600 star(据 LinkedIn 帖"Hot on GitHub, 20 days, 600 stars");EvoAgentX 是一个独立开源 agent 框架项目,该框架直接采用此四组件模型指导工程实现,构成"被下游系统采用"的证据。
局限: 由于无法稳定拉取该论文的 HTML/PDF 全文(arxiv.org/html 返回404、PDF因体积过大或编码问题两次解析失败),本条目中 Agent System 与 Environment 两项的定义句未能像 System Inputs、Optimisers 那样确认为逐字原文,存在转述风险,已在上面明确标注;该四组件框架描述的是静态结构(闭环里的四个"零件"),不是时间顺序的"阶段",与前两个框架的"阶段/维度"性质不同,归类对比时需注意。

### F4. 自我进化 Agent 三分类法:Model-Centric / Environment-Centric / Model-Environment Co-Evolution
来源: Xiang, Z. et al. "A Systematic Survey of Self-Evolving Agents: From Model-Centric to Environment-Driven Co-Evolution." TechRxiv preprint, DOI 10.36227/techrxiv.177203250.05832634(检索显示2026年2月前后)。GitHub: github.com/XMUDeepLIT/Awesome-Self-Evolving-Agents
划分对象: 自我进化 agent 研究方法按"进化的主体到底是模型本身还是模型所处的环境、还是两者共同"这一根本问题划分为三大类别
划分:
  - Model-Centric Self-Evolution(以模型为中心的自我进化): 检索到的转述性定义"agents enhance internal capabilities through inference scaling or parameter bootstrapping"(通过推理期算力扩展或参数自举来提升内部能力),细分 Inference-Based Evolution 与 Training-Based Evolution
  - Environment-Centric Self-Evolution(以环境为中心的自我进化): 检索到的转述性定义"agents achieve continual self-evolution by interacting with the environment to obtain external knowledge and experience-based feedback"(agent 通过与环境交互获取外部知识与经验反馈来实现持续自我进化),细分 Static Knowledge Evolution、Dynamic Experience Evolution、Modular Architecture Evolution、Agentic Topology Evolution
  - Model-Environment Co-Evolution(模型-环境协同进化): 检索到的转述性定义"agents and their environments jointly evolve through sustained interaction"(agent 与其环境通过持续交互共同进化)
权威性: 作者团队较大(14人以上),含厦门大学(Jinsong Su 等)与香港理工/理大团队成员;同时在 SSRN 与 TechRxiv 两个预印本平台挂出;GitHub 仓库 XMUDeepLIT/Awesome-Self-Evolving-Agents 明确以此三分类组织资料列表。但发表时间较新(2026年初),尚未见被其他综述引用的证据。
局限: 重要局限:多次尝试直接抓取 TechRxiv/SSRN/ResearchGate 原文均被403拒绝或链接失效,本条目三个划分的定义句均只是通过 WebSearch 摘要间接获得,未能逐字核对原文书面表述,不满足用户"逐字取自原文"的硬性要求——建议使用者视为"转述性线索、待后续直接读取原文核实"而非可直接引用的权威逐字引文;是本轮四个框架中唯一未达到验证标准的一条,列出仅供后续追查。

### F5. 多模态 LLM 自我提升三视角划分:Data Collection / Data Organization / Model Optimization
来源: Deng, S. et al. "Self-Improvement in Multimodal Large Language Models: A Survey." arXiv:2510.02665. https://arxiv.org/abs/2510.02665
划分对象: 多模态大模型(MLLM)领域内的自我提升(self-improvement)方法文献,按"提升流程中在哪个环节起作用"划分为三个视角
划分:
  - Data Collection(数据收集): 摘要原句列举"1) data collection"
  - Data Organization(数据组织): 摘要原句列举"2) data organization"(注:WebSearch初次摘要曾误写作"data verification",经直接抓取 arXiv 摘要页确认原文为"data organization")
  - Model Optimization(模型优化): 摘要原句列举"3) model optimization"
权威性: 摘要逐字原句确认:"We provide a structured overview of the current literature and discuss methods from three perspectives: 1) data collection, 2) data organization, and 3) model optimization, to facilitate the further development of self-improvement in MLLMs." 自称"the first to provide a comprehensive overview of self-improvement in Multimodal LLMs (MLLMs)"(首篇 MLLM 自我提升全面综述)。
局限: 限定在多模态(视觉-语言)场景,不是通用 LLM/agent 自我进化的划分,与用户主问题("AI 迭代学习、持续独立完成任务"的通用划分)贴合度弱于前两个框架,仅作为同类三段式划分的旁证收录;发表较新(2025-10),引用积累有限。

## key_findings

1. **两篇最核心的自我进化综述给出的划分性质不同:2404.14387 是"时间顺序的四阶段循环"(获取→提炼→更新→评估),2507.21046 是"三个正交问题维度"(what/when/how,而非时间顺序阶段)**
   证据: 2404.14387 原文用词 "iterative cycles composed of four phases"(强调phases、cycle);2507.21046 原文用词 "organizing the field around three foundational dimensions"(强调dimensions,是分类轴不是流程步骤)
   来源: arXiv 2404.14387 abstract;arXiv 2507.21046 abstract(v4)

2. **2507.21046 的标题在版本迭代中已从三维扩展为标题层面的四维(加入 Where),但摘要正文尚未同步更新,说明该框架本身仍处于演化/未完全稳定状态**
   证据: v4版标题为"...What, When, How, and Where to Evolve...",但abstract仍写"three foundational dimensions: what, when, and how to evolve"
   来源: arXiv 2507.21046v4 abstract 与标题对比

3. **2508.07407 提出的是静态结构性的"四组件反馈环"而非时间阶段,和前两篇的"阶段/维度"框架属于不同性质的划分(结构 vs 过程/问题轴)**
   证据: 摘要原句"a unified conceptual framework that abstracts the feedback loop underlying the design of self-evolving agentic systems. The framework highlights four key components: System Inputs, Agent System, Environment, and Optimisers"——强调components(组件)而非phases或dimensions
   来源: arXiv 2508.07407 abstract

4. **SEAL(2506.10943)与 Darwin Gödel Machine(2505.22954)是具体的自我改进系统实现,不具备独立的、可与上述综述并列引用的多段式阶段划分框架,不满足'自带阶段框架'的收录门槛**
   证据: SEAL 摘要中只描述 self-edit 生成→SFT→RL 训练闭环,无命名阶段术语;DGM 检索结果显示其核心是'alternates between self-modification and evaluation phases'的二元循环,粒度和正式程度远不及综述提出的三/四阶段分类法
   来源: arXiv 2506.10943 abstract;WebSearch 对 arXiv 2505.22954 的检索摘要

## sources
- [读] A Survey on Self-Evolution of Large Language Models (arXiv 2404.14387) — https://arxiv.org/abs/2404.14387
- [读] A Survey on Self-Evolution of Large Language Models — ar5iv HTML 全文 — https://ar5iv.labs.arxiv.org/html/2404.14387
- [读] Awesome-Self-Evolution-of-LLM (阿里 DAMO-ConvAI 配套仓库) — https://github.com/AlibabaResearch/DAMO-ConvAI/tree/main/Awesome-Self-Evolution-of-LLM
- [读] A Survey of Self-Evolving Agents: What, When, How, and Where to Evolve (arXiv 2507.21046, v4) — https://arxiv.org/abs/2507.21046v4
- [读] A Survey of Self-Evolving Agents — arXiv HTML v4 全文 — https://arxiv.org/html/2507.21046v4
- [读] GitHub - CharlesQ9/Self-Evolving-Agents — https://github.com/CharlesQ9/Self-Evolving-Agents
- [读] A Comprehensive Survey of Self-Evolving AI Agents (arXiv 2508.07407) — https://arxiv.org/abs/2508.07407
- [摘要] A Comprehensive Survey of Self-Evolving AI Agents — arXiv HTML(尝试拉取,404失败) — https://arxiv.org/html/2508.07407
- [读] GitHub - EvoAgentX/Awesome-Self-Evolving-Agents — https://github.com/EvoAgentX/Awesome-Self-Evolving-Agents
- [摘要] A Systematic Survey of Self-Evolving Agents: From Model-Centric to Environment-Driven Co-Evolution (TechRxiv,访问被403拒绝,仅WebSearch摘要) — https://www.techrxiv.org/doi/10.36227/techrxiv.177203250.05832634
- [读] GitHub - XMUDeepLIT/Awesome-Self-Evolving-Agents — https://github.com/XMUDeepLIT/Awesome-Self-Evolving-Agents
- [读] Self-Improvement in Multimodal Large Language Models: A Survey (arXiv 2510.02665) — https://arxiv.org/abs/2510.02665
- [读] Self-Adapting Language Models / SEAL (arXiv 2506.10943) — https://arxiv.org/abs/2506.10943
- [摘要] Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents (arXiv 2505.22954,仅WebSearch摘要,未直接读取原文) — https://arxiv.org/abs/2505.22954


---

## 自主性等级框架

## frameworks

### F1. DeepMind《Levels of AGI》—— Performance × Generality 矩阵（性能×通用性）
来源: Morris, M. R., Sohl-Dickstein, J., Fiedel, N., Warkentin, T., Dafoe, A., Faust, A., Farabet, C., & Legg, S. (2023). Levels of AGI for Operationalizing Progress on the Path to AGI. arXiv:2311.02462. Table 1, Section 4 "Levels of AGI". 原文HTML: https://arxiv.org/html/2311.02462v2
划分对象: 划分的对象是AI系统相对于人类的『性能深度』(Performance/depth)与『任务通用性广度』(Generality/breadth)——即一个系统在多宽的任务范围内、达到多深的能力水平，用于统一描述从窄AI到AGI的进展阶梯，而非直接划分'自主完成任务'本身。
划分:
  - Level 0: No AI — 'Narrow Non-AI' 举例 calculator software; compiler；'General Non-AI' 举例 human-in-the-loop computing, e.g., Amazon Mechanical Turk
  - Level 1: Emerging — 'equal to or somewhat better than an unskilled human'（原文逐字判据）
  - Level 2: Competent — 'at least 50th percentile of skilled adults'（原文逐字判据）
  - Level 3: Expert — 'at least 90th percentile of skilled adults'（原文逐字判据）
  - Level 4: Virtuoso — 'at least 99th percentile of skilled adults'（原文逐字判据）
  - Level 5: Superhuman — 'outperforms 100% of humans'（原文逐字判据）；General列对应名称为 'Artificial Superintelligence (ASI)'
  - 两个通用性维度定义：Narrow = 'clearly scoped task or set of tasks'；General = 'wide range of non-physical tasks, including metacognitive abilities like learning new skills'（Table 1表头原文）
权威性: arXiv 2311.02462，作者含DeepMind研究团队（Google DeepMind），发表于2023年11月，是目前AGI分级讨论中被引用最广泛的技术性提案之一；论文原文自陈其6级本体论可以把此前多个著名AGI定义（Agüera y Arcas & Norvig的定义落在'Emerging AGI'一档、OpenAI 2018年'劳动替代'门槛对应'Virtuoso AGI'、Legg/Shanahan/Suleyman等定义多落在'Competent AGI'一档）统一收纳，这是它作为'分级坐标系'被广泛引用/对齐的直接证据（原文："Aguera y Arcas & Norvig's definition would fall into the 'Emerging AGI' category of our ontology, while OpenAI's threshold of labor replacement better matches 'Virtuoso AGI'... Our 'Competent AGI' level is probably the best catch-all for many existing definitions"）。
局限: 论文明确写出6级矩阵里 Competent/Expert/Virtuoso/Superhuman 四档的 AGI(General)列在成文时全部标注为『not yet achieved』，即该框架当时主要是前瞻性/概念性坐标系，尚缺可执行的标准化基准测试来严格归类具体系统（论文第6节讨论了'Testing for AGI'的缺口）。

### F2. DeepMind《Levels of AGI》—— Levels of Autonomy（自主性等级，人机交互范式）
来源: 同上论文 Table 2，Section 6.2 "Capabilities vs. Autonomy"。原文HTML同上。
划分对象: 划分的对象是『人类与AI系统协作时，人类在交互范式中扮演的角色/控制权分配方式』——即给定AGI能力后，实际部署时人机交互设计所对应的自主程度阶梯，与上面的能力矩阵是正交但相关的两条轴（论文原文：'We propose characterizing human-AI interaction paradigms with six Levels of Autonomy'）。
划分:
  - Autonomy Level 0: No AI — 'human does everything'（原文判据）
  - Autonomy Level 1: AI as a Tool — 'human fully controls task and uses AI to automate mundane sub-tasks'（原文判据）
  - Autonomy Level 2: AI as a Consultant — 'AI takes on a substantive role, but only when invoked by a human'（原文判据）
  - Autonomy Level 3: AI as a Collaborator — 'co-equal human-AI collaboration; interactive coordination of goals & tasks'（原文判据）
  - Autonomy Level 4: AI as an Expert — 'AI drives interaction; human provides guidance & feedback or performs subtasks'（原文判据）
  - Autonomy Level 5: AI as an Agent — 'fully autonomous AI'（原文判据）
权威性: 同一篇arXiv 2311.02462论文内Table 2，与Table 1性能矩阵配套发布；论文明确指出这一自主性维度与既有的Sheridan/Parasuraman计算机自动化分类法（Sheridan et al., 1978; Sheridan & Parasuraman, 2005; Parasuraman et al., 2000）不同——后者是'computer-centric perspective'（以设计者让渡多少控制权给计算机为框架），而DeepMind这里是从'人机交互风格的本质'出发，并进一步讨论了自主等级与AGI能力等级的'解锁'关系（原文：'Higher levels of autonomy are "unlocked" by AGI capability progression'）。后续 Hugging Face 论文《Fully Autonomous AI Agents Should Not be Developed》（见下一框架）明确引用本文（Morris et al., 2024）作为'AGI相关文献中提出的自主性轨迹设定agent占据单一层级、完全自主'的参照对象，是该框架被后续研究引用/对照的直接证据。
局限: 论文自己强调：'appropriate autonomy level need not be the maximum achievable given the capabilities of the underlying model'，即等级之间不是必须递进采用的规范性阶梯，而是可选的设计空间；且各等级的'Example Risks Introduced'栏目本身带有较强的前瞻/推测性质（如Level 5一栏的示例系统标注为'not yet unlocked'）。

### F3. OpenAI 内部五级框架（Chatbots/Reasoners/Agents/Innovators/Organizations）
来源: 无OpenAI官方发表的论文或博客原文。唯一信源为 Bloomberg 记者 Rachel Metz 于 2024年7月11日 发表的付费墙报道《OpenAI Sets Levels to Track Progress Toward Superintelligent AI》(bloomberg.com/news/articles/2024-07-11)，转述OpenAI在员工全员会议(all-hands)上展示的内部分级。本轮WebFetch访问该URL返回HTTP 403（付费墙拦截），未能读到原文全文，仅读到多篇二手转述/媒体摘要（如Forbes、TechCrunch系报道的转述）。
划分对象: 划分对象据转述是『AI系统朝AGI演进的产品/能力形态阶段』，从对话式AI到能替代整个组织运作的AI。
划分:
  - Level 1: Chatbots — 据转述为'conversational language'层级的AI（本轮未读到OpenAI/Bloomberg原文逐字定义，只有二手转述）
  - Level 2: Reasoners — 二手转述称其判据为'systems that can do basic problem-solving tasks as well as a human with a doctorate-level education who doesn't have access to any tools'（此句来自多个转述该Bloomberg报道的媒体文章的一致引述，非本轮直接读到Bloomberg原文）
  - Level 3: Agents — 二手转述称其为'AI systems that can spend several days taking actions on a user's behalf'
  - Level 4: Innovators — 二手转述称其为'AI that can come up with new innovations'
  - Level 5: Organizations — 二手转述称其为'AI that can do the work of an organization'
权威性: 这是OpenAI内部对员工展示的分级材料经Bloomberg独家报道'泄露'到公众领域，并非OpenAI官方发表的论文、博客或公开声明；此后被Forbes、TechCrunch、多家科技媒体及DeepMind《Levels of AGI》论文（脚注引用'OpenAI, 2018'的劳动替代定义，未直接引用此五级框架）广泛转述讨论，传播量级很大，但权威性仅停留在'企业内部工作分级经媒体转述'层次，不构成经同行评审或企业官方发表的文献。
局限: 重要局限：本轮未能实际读到该框架的第一手原文（Bloomberg文章付费墙拦截，OpenAI未发布对应官方原文），因此逐字定义部分只能标注为二手转述，不满足用户'逐字取自原文'的硬要求，请用户知悉此框架目前没有可核实的一手逐字原文来源；如需要满足硬底线，建议排除此框架或明确标注其为'经媒体转述、非一手可核实'的特殊类别。

### F4. 《From AI for Science to Agentic Science》综述——AI在科学发现中的四级自主/能力演进（用户提及的"Tool/Analyst/Scientist三级"经核实实际为四级命名体系）
来源: Wei, J. et al. (2025). From AI for Science to Agentic Science: A Survey on Autonomous Scientific Discovery. arXiv:2508.14111. Section 2.1 "The Evolution of AI for Science"，子节 2.1.1–2.1.4。原文HTML: https://arxiv.org/html/2508.14111v2
划分对象: 划分的对象是『AI在科学研究全流程（提出假设→设计实验→执行→分析→迭代）中扮演的角色与自主程度』，从辅助计算工具演进到可独立完成整个科学发现循环的自主伙伴，直至能发明新科学范式的生成式建构者。
划分:
  - Level 1: AI as a Computational Oracle (Expert Tools) — 原文：'AI operates as a Computational Oracle, a collection of highly specialized, non-agentic models designed to solve discrete, well-defined problems within a human-led workflow... they function as sophisticated function approximators that require constant human guidance for task definition, execution, and interpretation of results. The core of the scientific process-from forming hypotheses to designing experiments-remains entirely in the hands of the human researcher.'
  - Level 2: AI as an Automated Research Assistant (Partial Agentic Discovery) — 原文：'AI systems exhibit partial autonomy, functioning as agents that can execute specific, pre-defined stages of the research workflow... However, the high-level scientific direction, including the initial hypothesis, is still provided by human researchers... The agent's autonomy is limited to the execution of this pre-defined sub-goal, after which control returns to the human scientist.'
  - Level 3: AI as an Autonomous Scientific Partner (Full Agentic Discovery) — 原文：'the AI agent possesses the ability to conduct the entire scientific discovery cycle independently. It can observe a domain, formulate novel and non-obvious hypotheses, design and execute experiments to test them, analyze the results, and iteratively refine its knowledge and strategy with minimal human intervention... The human role shifts to that of a high-level strategist and validator.'
  - Level 4: AI as a Generative Architect (Future Prospect) — 原文：'a system capable of not just working within existing scientific paradigms, but actively inventing new ones. This goes beyond discovering new facts to engaging in autonomous invention. Such agents would possess the capacity to design novel scientific instruments, create new experimental methodologies, or formulate new conceptual and mathematical frameworks... moving from a "tool-user to tool-creator"'
权威性: arXiv 2508.14111，作者Jiaqi Wei等27人合作的大型综述（2025年发表），配套GitHub仓库 AgenticScience/Awesome-Agent-Scientists 做论文列表维护，是当前'Agentic Science'方向少数系统性综述之一；论文用形式化数学定义（如Level 3的策略优化目标函数）来锚定每级判据，比一般综述更严谨。
局限: 重要澄清：经本轮实读原文核实，该综述实际采用的是四级命名体系（Computational Oracle / Automated Research Assistant / Autonomous Scientific Partner / Generative Architect），并非用户任务描述中所称的'Tool/Analyst/Scientist三级'命名——原文中没有出现'Analyst'一词作为分级名称。请用户注意核对是否记忆了另一篇文献的命名，或该'三级'说法来自对本综述的某种转述简化（例如把Level 1归为Tool、Level 2+3合并为某种Analyst/Scientist二分）。本报告只如实呈现arXiv 2508.14111原文实际使用的四级体系，不代入用户预设的三级名称。

### F5. Hugging Face《Fully Autonomous AI Agents Should Not be Developed》—— AI Agent 五星等级表
来源: Mitchell, M., Ghosh, A., Luccioni, A. S., & Pistilli, G. (2025). Fully Autonomous AI Agents Should Not be Developed. arXiv:2502.02649. Table 1 (Section 2.2 "Current Landscape of Agentic Systems")。原文HTML: https://arxiv.org/html/2502.02649
划分对象: 划分的对象是『AI agent系统中，模型（LLM）输出对程序流程/控制权的影响程度』——即技术实现层面上人类与代码在多大程度上让渡控制权给模型输出，论文原文称其刻画的是'a sliding scale'的'agentic'程度。
划分:
  - ✩✩✩✩（无星）— 'Model has no impact on program flow'，对应术语 'Simple processor'，示例代码 print_llm_output(llm_response)，Who's in Control: 'Human'
  - ★✩✩✩ — 'Model determines basic program flow'，对应术语 'Router'，示例代码 if llm_decision(): path_a() else: path_b()，控制权描述: 'How functions are done; When'
  - ★★✩✩ — 'Model determines how functions are executed'，对应术语 'Tool caller'，示例代码 run_function(llm_chosen_tool, llm_chosen_args)，控制权描述: 'What functions are done; How'
  - ★★★✩ — 'Model controls iteration and program continuation'，对应术语 'Multi-step agent'，示例代码 while should_continue(): execute_next_step()，控制权描述: 'What functions exist; Which to do, when, how'
  - ★★★★（满星）— 'Model creates & executes new code'，对应术语 'Fully autonomous agent'，示例代码 create_code(user_request); execute()，Who's in Control: 'System'
权威性: arXiv 2502.02649，作者含 Margaret Mitchell（前Google/HuggingFace知名AI伦理研究者）、Avijit Ghosh、Alexandra Sasha Luccioni、Giada Pistilli（均为Hugging Face团队），发表于2025年2月并在Hugging Face Papers页面高亮；论文原文明确写出该五星量表'Levels adapted from (Roucher et al., 2024)'——即改编自Hugging Face smolagents框架作者Aymeric Roucher 2024年的分级提案（Hugging Face官方博客/文档中"Levels of agency"），说明这套量表已被Hugging Face官方开源框架smolagents实际采纳用于工程实践，具有'研究论文+同一机构产品文档'双重使用的证据。
局限: 论文原文脚注9自陈：'This approach to levelling is one way of categorizing; for a classic categorization with consensus, see Russell and Norvig (1995)'，即作者自己承认这只是众多可能分级方式之一，未形成学界共识；且该量表聚焦于单agent的代码控制流层面，论文明确将multi-agent系统的复杂性排除在外（'While we focus our ethical analysis on the behaviors of single agents, multi-agent systems introduce further complexities we leave for future work'）。

### F6. 《Levels of Autonomy for AI Agents》—— 五级『用户角色』自主性框架（Operator/Collaborator/Consultant/Approver/Observer）
来源: Feng, K. J. K. et al. Levels of Autonomy for AI Agents (Working Paper), Knight First Amendment Institute at Columbia University. arXiv:2506.12469。原文HTML: https://arxiv.org/html/2506.12469v2，Section 3 "Five Levels of Autonomy for AI Agents"
划分对象: 划分的对象是『用户在与AI agent交互时所扮演的角色』——论文将自主性定义为'the extent to which an AI agent is designed to operate without user involvement'，并明确主张自主性是可与能力(capability)/agency独立设计的决策变量，而非能力提升的必然结果。
划分:
  - Level 1: User as an operator — 原文：'the user is in charge at all times while the agent is available to provide support on-demand... L1 agents do not take action... unless explicitly invoked. Alternatively, if the agent proactively suggests actions, it does not execute them until they are approved by the user.'
  - Level 2: User as a collaborator — 原文：'Both the agent and the user can plan, delegate, and execute tasks to leverage each other's capabilities and knowledge... the agent does not always "follow" the user around in the environment and can independently work on its own tasks while the user works on theirs.'
  - Level 3: User as a Consultant — 原文：'The agent takes initiative in task planning and execution over extended time horizons. Users still have an active and important role... their involvement is more focused on providing feedback, preferences, and higher-level directional guidance rather than hands-on collaboration... there may be no mechanism for the user to directly take control from the agent.'
  - Level 4: User as an approver — 原文：'the user is only required to interact with the agent when the agent encounters a blocker it cannot resolve on its own. This includes reaching a failure state that prevents workflow continuation, providing credentials... that the user did not share, or signing off on consequential actions.'
  - Level 5: User as an observer — 原文：'a fully autonomous agent that does not require, and comes with no means for, user involvement. L5 agents plan and execute tasks over long time horizons and make all decisions on their own... The only control mechanism available to the user is an emergency off-switch that shuts off all agent activity.'
权威性: arXiv 2506.12469，由哥伦比亚大学 Knight First Amendment Institute 以working paper形式配发网页版发布（论文标题页自注'PDF accompaniment to the web publication by the Knight 1st Amendment Institute'），是一家专注于科技/言论自由与治理的知名学术机构智库产出；论文进一步提出配套的'AI autonomy certificates'治理机制设想，主张把自主等级作为可审计、可认证的独立设计维度，是目前把'自主性'与'能力'显式解耦、并给出可操作分级判据（以用户角色而非纯技术控制流定义）的代表性正式提案之一。
局限: 论文中未出现SAE J3016自动驾驶分级的直接类比或引用（经本轮检索确认，其五级命名borrows from'existing human-centered views on autonomy'并引用了Morris et al.关于AGI能力的立场论文，而非汽车自动驾驶标准）；这是一篇working paper/essay形式的立场文章，尚未见其经过传统同行评审期刊/会议正式发表的版本。

### F7. 《Data Agents: Levels, State of the Art, and Open Problems》—— 显式类比SAE J3016的数据智能体 L0–L5 分级
来源: SIGMOD 2026 Tutorial论文，arXiv:2602.04261。原文HTML: https://arxiv.org/html/2602.04261v1，Section 2.2 "The L0–L5 Hierarchy of Data Agents"
划分对象: 划分的对象是『数据管理/数据准备/数据分析全生命周期中，数据智能体（data agent）相对人类的自主程度』；论文原文明确写出其分级方法论直接借鉴自动驾驶SAE J3016标准：'Inspired by the SAE J3016 standard for driving automation (Shi et al., 2020), we adopt a six-level taxonomy of data agents from L0 to L5.'
划分:
  - L0: No Autonomy — 原文：'At L0, there is no data agent involvement. All tasks in data management, preparation, and analysis are performed manually by humans.'
  - L1: Assistance — 原文：'L1 data agents operate within a stateless, prompt-response framework. They can answer questions, generate code snippets, or suggest queries, but they do not perceive or interact with the environment. Humans remain fully responsible for executing and verifying any suggestions.'
  - L2: Partial Autonomy — 原文：'L2 data agents gain the ability to perceive and interact with their environment, including data lakes, DBMSs, code interpreters, and external APIs. They may possess memory and can invoke tools to autonomously execute task-specific procedures within human-orchestrated pipelines.'
  - L3: Conditional Autonomy — 原文：'L3 data agents are expected to autonomously orchestrate and execute tailored data pipelines for a wide range of tasks under human supervision. They interpret high-level user intentions and dominate the end-to-end workflow, while humans act as supervisors.'
  - L4: High Autonomy — 原文：'L4 data agents achieve high autonomy and reliability, eliminating the need for human supervision and explicit instructions. They are fully delegated to proactively monitor Data+AI ecosystems, autonomously discover issues and opportunities in data lakes, and orchestrate pipelines to address them.'
  - L5: Full Autonomy — 原文：'data agents are envisioned to innovate new solutions and paradigms beyond existing methods, acting as fully autonomous and generative data scientists. Human involvement becomes unnecessary.'
权威性: 本论文确认为SIGMOD（数据库领域顶级会议之一）2026年 Tutorial 材料，原文写明'we build on that survey and turn it into a teaching-oriented framework for SIGMOD attendees'；这是本轮检索中唯一一篇明确、逐字引用SAE J3016驾驶自动化标准作为分级方法论来源、且发表于正式学术会议渠道（而非博客/播客）的agent自主性分级提案，满足用户'找有正式出处的自动驾驶L0-L5类比'的要求，但其适用范围限定在数据智能体(data agent)这一垂直领域而非通用AI agent。
局限: 该框架的划分对象局限于数据管理/准备/分析这一垂直领域的智能体，不是面向通用AI agent或'持续独立完成任意任务'的普适分级；论文原文亦承认L4、L5目前仍是'vision and research roadmap'性质（原文标题2.5节即为'L4–L5: Vision and Research Roadmap'），尚无实际系统达到这两级。

## key_findings

1. **DeepMind《Levels of AGI》论文实际包含两套独立但相关的分级体系：一套是'性能×通用性'矩阵(Table 1，Level 0-5: No AI/Emerging/Competent/Expert/Virtuoso/Superhuman)，衡量AI能力本身；另一套是'Levels of Autonomy'(Table 2，Autonomy Level 0-5: No AI/AI as Tool/AI as Consultant/AI as Collaborator/AI as Expert/AI as Agent)，衡量人机交互中人类让渡控制权的程度。两者正交但相关（高自主性由高AGI能力'解锁'）。**
   证据: 直接读取arXiv 2311.02462 HTML全文Table 1和Table 2逐字确认，两表结构、术语、判据完全不同，且论文原文第6.2节明确写出'These Levels of Autonomy are correlated with the Levels of AGI'来说明二者的关系。
   来源: arXiv:2311.02462 (Morris et al., 2023)

2. **OpenAI的'五级'(Chatbots/Reasoners/Agents/Innovators/Organizations)框架并非OpenAI官方发表的论文或博客，唯一信源是Bloomberg记者2024年7月的付费墙独家报道，本轮尝试WebFetch被403拦截，未能读到一手原文，只读到多篇媒体的二手转述。**
   证据: WebFetch直接访问bloomberg.com对应URL返回HTTP 403 Forbidden；后续搜索到的所有'逐字引用'（如'doctorate-level education who doesn't have access to any tools'）均来自转述该Bloomberg文章的第三方媒体（Forbes、AI Insider等），而非本轮直接验证的Bloomberg原文。
   来源: Bloomberg, 'OpenAI Sets Levels to Track Progress Toward Superintelligent AI' (2024-07-11, Rachel Metz)——未能实读

3. **用户提及的Agentic Science综述'Tool/Analyst/Scientist三级'与本轮实读的arXiv 2508.14111原文不符——该综述实际采用的是四级命名(Computational Oracle/Automated Research Assistant/Autonomous Scientific Partner/Generative Architect)，全文未出现'Analyst'一词作为分级名称。**
   证据: 对该论文HTML全文做了Grep关键词搜索(Tool|Analyst|Scientist)，结合Read原文Section 2.1的四个子节标题和正文定义，确认命名体系与用户描述不一致，需请用户核实是否记混了另一篇文献。
   来源: arXiv:2508.14111 (Wei et al., 2025)

4. **Hugging Face论文《Fully Autonomous AI Agents Should Not be Developed》里的五星agent等级表，明确注明'Levels adapted from (Roucher et al., 2024)'，即源自Hugging Face smolagents框架作者Aymeric Roucher的分级提案，形成'论文+同机构开源框架文档'的双重落地证据。**
   证据: 读取arXiv 2502.02649原文Table 1的figcaption逐字确认引用来源；并通过WebSearch确认Roucher的原始'levels of agency'星级体系已用于smolagents官方文档/博客。
   来源: arXiv:2502.02649 Table 1 figcaption

5. **在本轮检索到的'把自动驾驶L0-L5类比到agent'的提案中，只有SIGMOD 2026 Tutorial论文《Data Agents: Levels, State of the Art, and Open Problems》明确逐字引用SAE J3016标准作为方法论来源；另一篇候选(Feng et al. arXiv:2506.12469, Knight First Amendment Institute)的五级框架(Operator/Collaborator/Consultant/Approver/Observer)经原文核实并未采用SAE类比，而是独立提出的'用户角色'视角。**
   证据: 对两篇论文全文分别做Grep(SAE|J3016)搜索：Data Agents论文2.2节第一句原文明确写'Inspired by the SAE J3016 standard for driving automation (Shi et al., 2020)'；Feng et al.论文全文搜索SAE/J3016均无结果，其Introduction与Section 3只提及Russell & Norvig的agent定义和Morris et al.的AGI能力立场论文作为借鉴来源。
   来源: arXiv:2602.04261 Section 2.2 vs arXiv:2506.12469 Section 1&3

## sources
- [读] Levels of AGI for Operationalizing Progress on the Path to AGI (arXiv:2311.02462, HTML v2) — https://arxiv.org/html/2311.02462v2
- [读] Levels of AGI for Operationalizing Progress on the Path to AGI (arXiv abstract page) — https://arxiv.org/abs/2311.02462
- [读] Fully Autonomous AI Agents Should Not be Developed (arXiv:2502.02649, HTML) — https://arxiv.org/html/2502.02649
- [读] From AI for Science to Agentic Science: A Survey on Autonomous Scientific Discovery (arXiv:2508.14111, HTML v2) — https://arxiv.org/html/2508.14111v2
- [读] Levels of Autonomy for AI Agents (Knight First Amendment Institute working paper, arXiv:2506.12469, HTML v2) — https://arxiv.org/html/2506.12469v2
- [读] Data Agents: Levels, State of the Art, and Open Problems (SIGMOD 2026 Tutorial, arXiv:2602.04261, HTML v1) — https://arxiv.org/html/2602.04261v1
- [摘要] OpenAI Sets Levels to Track Progress Toward Superintelligent AI (Bloomberg, Rachel Metz, 2024-07-11) — https://www.bloomberg.com/news/articles/2024-07-11/openai-sets-levels-to-track-progress-toward-superintelligent-ai
- [摘要] OpenAI's Five Levels of AI - And Where Are We Now? (转述Bloomberg报道的二手媒体文章) — https://theaiinsider.tech/2024/07/12/what-are-openais-five-levels-of-ai-and-where-are-we-now/
- [摘要] OpenAI's 5 Levels Of 'Super AI' (Forbes, 转述Bloomberg报道) — https://www.forbes.com/sites/jodiecook/2024/07/16/openais-5-levels-of-super-ai-agi-to-outperform-human-capability/


---

## 记忆与经验生命周期

## frameworks

### F1. A Survey on the Memory Mechanism of Large Language Model based Agents (memory sources / forms / operations 三分法)
来源: Zeyu Zhang, Xiaohe Bo, Chen Ma, Rui Li, Xu Chen, Quanyu Dai, Jieming Zhu, Zhenhua Dong, Ji-Rong Wen. arXiv:2404.13501 (2024-04-21初版, v1)；正式发表于 ACM Transactions on Information Systems (TOIS), DOI 10.1145/3748302。URL: https://arxiv.org/abs/2404.13501 、正式版 https://dl.acm.org/doi/10.1145/3748302
划分对象: LLM-based agent 记忆模块的"实现方式"这一问题，从三个维度(perspectives)拆解：记忆内容从哪来(sources)、记忆内容怎么表示(forms)、记忆内容怎么被处理(operations)。原文第5节标题即 'How to Implement the Memory of LLM-based Agent'。
划分:
  - 5.1 Memory Sources 三类来源——原文定义句：'In this section, we discuss the implementation of the memory module from three perspectives: memory sources, memory forms, and memory operations. Memory sources refer to where the memory contents come from.'
  - 5.1.1 Inside-trial Information (trial 内信息)——原文：'In the agent-environment interaction process, the historical steps within a trial are usually the most relevant and informative signals to support the agent's future actions.'
  - 5.1.2 Cross-trial Information (跨 trial 信息)——原文：'For LLM-based agents, the information accumulated across multiple trials in the environment is also a crucial part of the memory, typically including successful and failed actions and their insights, such as failure reasons, common action patterns to succeed, and so on.'
  - 5.1.3 External Knowledge (外部知识)——原文：'An important characteristic of LLM-based agents is that they can be directly communicated and controlled in natural languages. As such, LLM-based agents can easily incorporate external knowledge in textual forms (e.g., Wikipedia) to facilitate their decisions.'
  - 5.2 Memory Forms 两种形式——原文定义句：'Memory forms focus on how to represent the memory contents.' 及 'In general, there are two forms to represent the memory contents: textual form and parametric form. In textual form, the information is explicitly retained and recalled by natural languages. In parametric form, the memory information is encoded into parameters and implicitly influences the agent's actions.'
  - 5.2.1 Memory in Textual Form 下的四个子类——原文：'previous studies use the textual form memory to store four types of information including (1) complete agent-environment interactions, (2) recent agent-environment interactions, (3) retrieved agent-environment interactions, and (4) external knowledge.'
  - 5.2.2 Memory in Parametric Form 下的两个子类——原文：'we categorize previous works into two types: fine-tuning methods and memory editing methods.'
  - 5.3 Memory Operations 三个操作——原文定义句：'Memory operations aim to process the memory contents.' 及 'We separate the entire procedure of memory into three operations: memory writing, memory management, and memory reading. These three typically collaborate to achieve memory function, providing information for LLM inference.'
  - 5.3.1 Memory Writing——原文：'After the information is perceived by the agent, a part of it will be stored by the agent for further usage through the memory writing operation, and it is crucial to recognize which information is essential to store.'
  - 5.3.2 Memory Management——原文：'For human beings, memory information is constantly processed and abstracted in the brains. The memory in the agent can also be managed by reflecting to generate higher-level memories, merging redundant memory entries, and forgetting unimportant, early memories.' (子操作=reflecting/merging/forgetting，对应 Table 3 列名 Merging, Reflection, Forgetting)
  - 5.3.3 Memory Reading——原文：'This operation aims to obtain important information from the memory to support the next agent action. It corresponds to the third phase of the agent-environment interaction process.'
权威性: 已正式发表于 ACM Transactions on Information Systems (TOIS)，属信息检索/推荐系统领域顶级期刊之一；截至本次调研经 Semantic Scholar API 查得引用数 610 次（2024年发布，一年多引用超600，在同类综述里属高被引）；配套 GitHub 仓库 nuster1128/LLM_Agent_Memory_Survey 被后续多篇 2025-2026 综述(如 Rethinking Memory in LLM based Agents 2505.00675)直接点名引用并批评/对比其分类("Zhang et al. [367] covers only high-level operations such as writing, management, and reading, and misses some operations like indexing")，说明其 sources/forms/operations 三分法已成为该子领域后续综述对照的基准框架。
局限: 作者自己在 Table 1-3 的调研范围止于2024年4月前的工作(如 Reflexion/Voyager/MemGPT/Generative Agents等)，之后的 agentic RL、graph memory、KV-cache 层面进展未覆盖；'Memory Management' 一级操作粒度较粗，只到 reflecting/merging/forgetting 三个子机制，没有像后续综述(如2505.00675)那样进一步拆出 Consolidation vs Updating vs Forgetting 的独立地位——这点被后续综述明确指出是局限(遗漏了 Indexing 这一操作)。

### F2. Rethinking Memory in LLM based Agents: Representations, Operations, and Emerging Topics (六核心操作：Consolidation / Indexing / Updating / Forgetting / Retrieval / Condensation)
来源: Yiming Du, Wenyu Huang, Danna Zheng, Zhaowei Wang, Sebastien Montella, Mirella Lapata, Kam-Fai Wong, Jeff Z. Pan. arXiv:2505.00675 (v3, 2025-12-24更新)。URL: https://arxiv.org/abs/2505.00675 ；配套仓库 https://github.com/Elvin-Yiming-Du/Survey_Memory_in_AI
划分对象: LLM-based agent 记忆的"原子操作"(atomic operations governing memory dynamics)这一问题——作者明确说此前综述都偏 application-level，缺 operational formalization，本文要补的就是这个操作层面的分类。原文第2.4节标题 'Memory Operations'。
划分:
  - 总纲(Abstract逐字)：'This work categorizes memory into parametric (implicit in model weights) and contextual (explicit external data, structured/unstructured) forms, and defines six core operations: Consolidation, Updating, Indexing, Forgetting, Retrieval, and Condensation.'
  - 三大类归组——原文2.4节：'These operations can be grouped into three functional categories: Memory Encoding, Memory Evolving, and Memory Adapting.'
  - 2.4.1 Memory Encoding 总定义——原文：'Memory encoding governs how information is transformed into storable representations and linked for later retrieval. It primarily involves two complementary processes: Consolidation and Indexing.'
  - Consolidation——原文：'Consolidation [258] refers to transforming short-term experiences E[t,t+Δt] = (o1,o2,...,ot) between t and t+Δt into persistent memory M. It encodes interaction histories (e.g., dialogs, trajectories) into durable forms such as model parameters, graphs, or knowledge bases.'
  - Indexing——原文：'Indexing [195] constructs auxiliary codes I such as entities, attributes, or content-based representations that serve as access points to stored memory. Beyond access, indexing encodes temporal and relational structures across memories, enabling efficient and coherent retrieval through traversable index paths.'
  - 2.4.2 Memory Evolving 总定义——原文：'Memory evolving describes how stored information dynamically changes over time through two complementary processes: memory updating and memory forgetting.'
  - Updating——原文：'Updating [136] reactivates existing memory representations in M and temporarily modify them with new knowledge K. Updating parametric memory typically involves a locate-and-edit mechanism that targets specific model components. Meanwhile, contextual memory updating involves summarization, pruning, or refinement to reorganize or replace outdated content.'
  - Forgetting——原文：'Forgetting is the ability to selectively suppress memory content F from M that may be outdated, irrelevant, or harmful. In parametric memory, it is commonly implemented through unlearning techniques that modify model parameters to erase specific knowledge. In contextual memory, forgetting involves time-based deletion or semantic filtering to discard content that is no longer relevant.'
  - 2.4.3 Memory Adapting 总定义——原文：'Memory adapting refers to how stored memory is retrieved and used during inference, encompassing two operations: retrieval and compression.'
  - Retrieval——原文：'Retrieval is the process of identifying and accessing relevant information from memory in response to inputs, aiming to support downstream tasks such as response generation, visual grounding, or intent prediction... Memory fragments are typically scored with a function sim() with those above a threshold θ deemed relevant.'
  - Condensation——原文：'Condensation enables efficient context usage under limited context window by retaining salient information and discarding redundancies with a compression ratio ρ before feeding it into models. It can be broadly divided into pre-input compression and post-retrieval compression.'
权威性: 作者团队跨爱丁堡大学(Mirella Lapata为ACL Fellow级别资深学者)、港中文、港科大、华为诺亚方舟实验室；论文经方法论章节说明基于对37篇种子论文人工标注+3万余篇NeurIPS/ICLR/ICML/ACL/EMNLP/NAACL论文的规模化筛选(3923篇高相关论文)构建分类法，属数据驱动的系统性taxonomy构建而非临时拍脑袋分类；配套GitHub仓库(Elvin-Yiming-Du/Survey_Memory_in_AI)获353星；论文明确指出并批评上一篇综述(2404.13501/Zhang et al.)'covers only high-level operations such as writing, management, and reading, and misses some operations like indexing'，即本文的六操作分类是针对性地对前作做出的细化和补强，形成了该子领域两代分类法的演进关系。
局限: 六操作两两分组本身(Encoding/Evolving/Adapting)是作者本轮提出的组织框架，尚未经过大量后续综述的独立验证或采纳(不像 sources/forms/operations 三分那样已被下一代综述直接引用批评)；Condensation 与 Consolidation 两个操作在语义上容易混淆，原文特别加了一句区分：'Unlike memory consolidation, which summarizes information during memory construction, compression focuses on reducing memory at inference'，说明该分类边界需要靠额外说明才能撑住，边界不是不言自明的。

### F3. Memory in the Age of AI Agents: A Survey — Forms, Functions and Dynamics (记忆生命周期三阶段：Formation / Evolution / Retrieval)
来源: Yuyang Hu, Shichun Liu, Yanwei Yue, Guibin Zhang 等(核心贡献者按字母序列出，共31位作者，跨新加坡国立大学/中国人民大学/复旦大学/北京大学/南洋理工/牛津大学等12家机构，核心指导者含 Shuicheng Yan)。arXiv:2512.13564 v2 (2026-01-13更新，原始发布2025-12-16，Hugging Face Daily Papers 当日精选)。URL: https://arxiv.org/abs/2512.13564 ；配套仓库 https://github.com/Shichun-Liu/Agent-Memory-Paper-List
划分对象: 本综述用 forms(记忆载体是什么)/functions(记忆为什么被需要)/dynamics(记忆如何运作和演化) 三个正交透镜统一梳理2025-2026年碎片化的 agent memory 领域；本轮任务对应的是第三个透镜 Dynamics，即记忆的"生命周期操作"本身，原文第5节标题 'Dynamics: How Memory Operates and Evolves?'
划分:
  - 三大过程总纲——原文：'To systematically analyze "how" the memory system operates and evolves, we examine the complete memory lifecycle by decomposing it into three fundamental processes.' 图8caption逐字：'(1) Memory Formation transforms raw interactive experiences into information-dense knowledge units by selectively identifying patterns with long-term utility; (2) Memory Evolution dynamically integrates new memories into the existing repository through consolidation, updating, and forgetting mechanisms to ensure the knowledge base remains coherent and efficient; and (3) Memory Retrieval executes context-aware queries to access specific memory modules, thereby optimizing reasoning performance with precise information support.'
  - 1. Memory Formation——原文：'MemoryFormation(Section 5.1): This process focuses on transforming raw experience into information-dense knowledge... This part answers the question: "How to extract the memory?".' 下分五类操作(原文列表'Five Categories of Memory Formation Operations')：Semantic Summarization('transforms lengthy raw data into compact summaries, filtering out redundancy while preserving global, high-level semantic information')、Knowledge Distillation('extracts specific cognitive assets, ranging from factual details to experiential planning strategies')、Structured Construction('organizes amorphous source data into explicit topological representations, such as knowledge graphs or hierarchical trees')、Latent Representation('encodes raw experiences directly into machine-native formats (e.g., vector embeddings or KV states) within a continuous latent space')、Parametric Internalization(标题列出，正文续后未在本次摘录范围内)
  - 2. Memory Evolution——原文：'Memory Evolution(Section 5.2): This process represents the dynamic evolution of the memory system. It focuses on integrating newly formed memories with the existing memory base... This part answers the question: "How to refine the memory?".' 下分三个机制(原文列表'Three Mechanisms of Memory Evolution')：Memory Consolidation('merges new and existing memories and performs reflective integration, forming more generalized insights. This ensures that learning is cumulative rather than isolated.')、Memory Updating('resolves conflicts between new and existing memories, correcting and supplementing the repository to maintain accuracy and relevance.')、Memory Forgetting('removes outdated or redundant information, freeing capacity and improving efficiency.')
  - Consolidation 再细分三档——原文小标题：Local Consolidation（'This operation focuses on fine-grained updates involving highly similar memory fragments.'）、Cluster-level Fusion（'Adopting cluster-level fusion is essential for capturing cross-instance regularities as memory grows.'）、Global Integration（'This operation performs holistic consolidation to maintain global coherence and to distill system-level insights from accumulated experience.'）
  - Updating 再细分两档——原文：'Depending on where the memory resides, updates fall into two categories: (1) External Memory Update: updates to external memory stores and (2) Model Editing: model-internal editing within the parameter space.'
  - Forgetting 再细分三档——原文：'Forgetting mechanisms can be categorized into Time-based Forgetting, Frequency-based Forgetting, and Importance-driven Forgetting, corresponding respectively to creation time, retrieval activity, and integrated semantic valuation.'
  - 3. Memory Retrieval——原文：'Memory Retrieval(Section 5.3): This process determines the quality of the retrieved memory... This part answers the question: "How to utilize the memory?".' 下分四步(原文列表'Four Steps of Memory Retrieval')：Retrieval Timing and Intent('determines the specific moments and objectives for memory retrieval, shifting from passive, instruction-driven triggers to autonomous, self-regulated decisions')、Query Construction('bridges the semantic gap between the user's raw input and the stored memory index by decomposing or rewriting queries into effective retrieval signals')、Retrieval Strategies('executes the search over the memory repository, employing paradigms ranging from sparse lexical matching to dense semantic embedding and structure-aware graph traversal')、Post-Retrieval Processing('refines the retrieved raw fragments through re-ranking, filtering, and aggregation, ensuring that the final context provided to the model is concise and coherent')
权威性: 31位作者跨12家顶尖机构(新加坡国立大学、中国人民大学、复旦大学、北京大学、南洋理工、牛津大学等)，核心指导者含 Shuicheng Yan(计算机视觉/多模态领域高被引学者)；2025年12月16日发布当日即登 Hugging Face Daily Papers 精选；经 Semantic Scholar API 查得截至本次调研已获 199 次引用(发布仅约半年即达此量级，速度很快)；配套 GitHub 论文列表仓库(Shichun-Liu/Agent-Memory-Paper-List)获 2.2k 星，是本轮同类综述里社区关注度最高的一个；原文摘要明确定位为对'Traditional taxonomies such as long/short-term memory'不足以覆盖当前局面的回应，是目前(2025-2026)时间点上最新、覆盖面最广的一版。
局限: 论文本身是2025年12月刚发布、2026年1月才出v2的极新综述，尚未经过长期同行检验，六个月的引用量虽快但绝对基数(199)仍远小于已有两年积累的2404.13501(610引用)；Formation/Evolution/Retrieval 三分法与前述 2505.00675 的 Encoding/Evolving/Adapting 三分法高度同构(本质上是同一个'编码-演化-检索/适配'骨架的两种命名)，本文并未明确对比说明二者关系，存在同一时期内多个团队各自独立提出几乎同构分类法而互相未充分对话引用的情况，读者需自行辨识这是行业收敛还是巧合命名。

### F4. Continual Lifelong Learning with Neural Networks: A Review — Stability-Plasticity Dilemma（补充：经典持续学习视角，非本轮主线但按任务要求收录）
来源: German I. Parisi, Ronald Kemker, Jose L. Part, Christopher Kanan, Stefan Wermter. Neural Networks, Vol.113, pp.54-71, 2019；预印本 arXiv:1802.07569。URL: https://arxiv.org/abs/1802.07569
划分对象: 这篇不是划分'记忆操作阶段'的清单式taxonomy，而是围绕一个核心矛盾/维度——生物与人工学习系统在持续学习中必须同时满足的两种能力之间的张力，即 stability-plasticity dilemma，并以此矛盾轴组织全文对现有方法的分类(regularization-based / architectural / rehearsal-based 三类应对策略，详见原文第3节)。收录理由：用户要求若有'获取/巩固/迁移'权威版本也取一个，本文摘要逐字给出了这个三段式动词链，第2.1节则给出了该矛盾轴的正式定义。
划分:
  - 三段式能力链(摘要逐字，非小节标题而是连续动词短语)——原文：'Humans and animals have the ability to continually acquire, fine-tune, and transfer knowledge and skills throughout their lifespan.' 及正文第2.1节开篇重复：'As humans, we have an astonishing ability to adapt by effectively acquiring knowledge and skills, refining them on the basis of novel experiences, and transferring them across multiple domains.'（acquire/acquiring=获取，fine-tune/refining=巩固精炼，transfer=迁移）
  - Stability-Plasticity Dilemma 正式定义(第2.1节标题即此，原文逐字)——'The stability-plasticity dilemma regards the extent to which a system must be prone to integrate and adapt to new knowledge and, importantly, how this adaptation process should be compensated by internal mechanisms that stabilize and modulate neural activity to prevent catastrophic forgetting.'
  - 应对方法三分类(第3节，非本轮重点但一并列出以说明其分类落点)——原文列出的三种机制：'i) regulate intrinsic levels of synaptic plasticity to protect consolidated knowledge (Sec. 3.2); ii) allocate additional neural resources to learn new information (Sec. 3.3), and iii) use complementary learning systems for memory consolidation and experience replay (Sec. 3.4).'
权威性: 发表于 Neural Networks(Elsevier旗舰期刊之一)，经 Semantic Scholar API 查得引用数 3574 次，是持续学习(continual/lifelong learning)领域被引用最多的综述之一，几乎每篇后续持续学习论文的引言都会引用它来定义 stability-plasticity dilemma 和 catastrophic forgetting 问题。
局限: 这不是一个'把学习过程切成互斥阶段'的清单式framework(不像前三个memory综述那样给出Formation/Evolution/Retrieval式的编号步骤)——'acquire, fine-tune, transfer' 只是摘要里的连续动词链，作者并未在正文把它们提炼成三个有编号、有独立小节定义的正式阶段；真正被反复引用和形式化定义的是 stability-plasticity dilemma 这一个矛盾维度，而非三段式生命周期。若用户需要的是能直接套用的'阶段清单'，本文提供的是矛盾轴而非阶段清单，需谨慎区分使用场景；且本文聚焦神经网络层面的持续学习(灾难性遗忘/正则化/架构扩展/回放)，与LLM-based agent的记忆操作(检索增强、上下文管理等)不是同一层面的问题，跨领域套用时两者对'遗忘'和'巩固'的操作化定义并不一致。

## key_findings

1. **两篇LLM agent记忆综述(2404.13501 与 2505.00675)之间存在明确的代际演进关系：后者点名批评前者的操作分类'只到writing/management/reading，漏了indexing'。**
   证据: 2505.00675 原文第1节：'Zhang et al. [367] covers only high-level operations such as writing, management, and reading, and misses some operations like indexing.'
   来源: arXiv:2505.00675 Introduction

2. **2025年12月发布的'Memory in the Age of AI Agents'综述的Dynamics三段(Formation/Evolution/Retrieval)与2505.00675的Encoding/Evolving/Adapting三段在结构上高度同构，但两篇论文彼此没有互相点名对齐，属于同期内几个团队各自收敛到相似骨架的现象。**
   证据: 对比两篇论文第5节(2512.13564)与第2.4节(2505.00675)的三段式定义，动词链均为'编码/构建→演化/整合(consolidation+updating+forgetting)→检索/适配'，但2512.13564全文未引用2505.00675，反之亦然（经检索未发现互引）。
   来源: arXiv:2512.13564 第5节 vs arXiv:2505.00675 第2.4节 直接文本比对

3. **持续学习(continual learning)经典综述里最广为引用的'三段式'实为摘要中的动词链(acquire/fine-tune/transfer)，并未被作者自己形式化为带编号小节的三阶段模型；真正被形式化、反复引用的是stability-plasticity dilemma这一维度。**
   证据: 全文grep未发现'acquisition'/'consolidation'/'transfer'被列为三个带编号的小节标题；第2.1节标题是'The Stability-Plasticity Dilemma'并给出独立定义句。
   来源: arXiv:1802.07569 全文结构(pdftotext提取核对)

## sources
- [读] A Survey on the Memory Mechanism of Large Language Model based Agents (arXiv:2404.13501) — https://arxiv.org/abs/2404.13501
- [摘要] A Survey on the Memory Mechanism of Large Language Model-based Agents (ACM TOIS正式版) — https://dl.acm.org/doi/10.1145/3748302
- [读] Rethinking Memory in LLM based Agents: Representations, Operations, and Emerging Topics (arXiv:2505.00675) — https://arxiv.org/abs/2505.00675
- [读] Memory in the Age of AI Agents: A Survey — Forms, Functions and Dynamics (arXiv:2512.13564) — https://arxiv.org/abs/2512.13564
- [读] Continual Lifelong Learning with Neural Networks: A Review (arXiv:1802.07569 / Neural Networks 2019) — https://arxiv.org/abs/1802.07569
- [摘要] nuster1128/LLM_Agent_Memory_Survey (配套GitHub仓库) — https://github.com/nuster1128/llm_agent_memory_survey
- [读] Elvin-Yiming-Du/Survey_Memory_in_AI (配套GitHub仓库, 353 stars) — https://github.com/Elvin-Yiming-Du/Survey_Memory_in_AI
- [读] Shichun-Liu/Agent-Memory-Paper-List (配套GitHub仓库, 2.2k stars) — https://github.com/Shichun-Liu/Agent-Memory-Paper-List
- [读] Semantic Scholar API - citationCount 2404.13501 (610次引用) — https://api.semanticscholar.org/graph/v1/paper/arXiv:2404.13501
- [读] Semantic Scholar API - citationCount 2512.13564 (199次引用) — https://api.semanticscholar.org/graph/v1/paper/arXiv:2512.13564
- [读] Semantic Scholar API - citationCount 1802.07569 (3574次引用) — https://api.semanticscholar.org/graph/v1/paper/arXiv:1802.07569
- [摘要] van de Ven & Tolias, Three scenarios for continual learning (仅查摘要，未采用为主分类，说明性排除) — https://arxiv.org/abs/1904.07734


---

## 认知架构与模块划分

## frameworks

### F1. CoALA — Cognitive Architectures for Language Agents
来源: Theodore R. Sumers, Shunyu Yao, Karthik Narasimhan, Thomas L. Griffiths. arXiv:2309.02427 (Sep 2023, latest v3 Mar 2024). Published in Transactions on Machine Learning Research (TMLR), 2024.
划分对象: 整个 language agent 的构造方式:该 agent 如何存储信息(memory)、如何对内对外行动(action space)、如何在每个决策周期里选择行动(decision-making procedure)。三个维度不是互斥阶段,而是一个 agent 架构的三块正交拆分。
划分:
  - 三大组成部分(原文): 'CoALA organizes agents along three key dimensions: their information storage (divided into working and long-term memories); their action space (divided into internal and external actions); and their decision-making procedure.'
  - Working Memory(工作记忆): 'Working memory maintains active and readily available information as symbolic variables for the current decision cycle.'
  - Episodic Memory(情景记忆): 'Episodic memory stores experience from earlier decision cycles.'
  - Semantic Memory(语义记忆): 'Semantic memory stores an agent's knowledge about the world and itself.'
  - Procedural Memory(程序性记忆): 'Language agents contain two forms of procedural memory: implicit knowledge stored in the LLM weights, and explicit knowledge written in the agent's code.'
  - Reasoning Actions(推理动作): 'Reasoning allows language agents to process the contents of working memory to generate new information.'
  - Retrieval Actions(检索动作): 'In CoALA, a retrieval procedure reads information from long-term memories into working memory.'
  - Learning Actions(学习动作): 'Learning occurs by writing information to long-term memory, which includes a spectrum of diverse procedures.'
  - Grounding Actions(落地/接地动作): 'Grounding procedures execute external actions and process environmental feedback into working memory as text.'(细分为三类环境: physical environments / dialogue with humans or other agents / digital environments)
  - 决策周期(Planning 与 Execution): 'CoALA structures this top-level program into decision cycles...In each cycle, the agent can use reasoning and retrieval actions to plan. This planning subprocess selects a grounding or learning action, which is executed.'
  - Planning 内部三子阶段(propose/evaluate/select): 'During planning, reasoning and retrieval can be flexibly applied to propose, evaluate, and select actions...The proposal sub-stage generates one or more action candidates...the evaluation sub-stage assigns a value to each...the selection step either selects one to execute or rejects them.'
权威性: 发表于 TMLR(2024),Semantic Scholar 引用量约 280+(截至检索时,持续增长中),是 2023-2024 年被后续 LLM agent 综述(如 Xi et al. 2309.07864 直接沿用其 memory 分类)反复引用的奠基性框架;作者团队来自 Princeton(含 ReAct 作者 Shunyu Yao);GitHub 上有专门的 awesome-language-agents 仓库按 CoALA 分类收录 300+ 相关工作。
局限: 论文本身承认这是回顾性组织框架(用于系统化已有工作、指出未来方向),而非新提出的可执行系统;四类 memory 的划分借用认知心理学但作者也说明 procedural memory 在语言 agent 里边界模糊(隐式存于权重、显式存于代码,两者性质差异很大);grounding actions 三分类(physical/dialogue/digital)之间也有交叠案例。

### F2. BDI 架构(Belief-Desire-Intention)
来源: Anand S. Rao and Michael P. Georgeff, "BDI Agents: From Theory to Practice", Proceedings of the First International Conference on Multiagent Systems (ICMAS-95), AAAI, 1995, pp. 312-319. (原始出处 http://cdn.aaai.org/ICMAS/1995/ICMAS95-042.pdf)
划分对象: 理性agent的心智状态(mental attitudes)划分,用来解释一个在资源有限(计算/时间受限)条件下的agent应该维持哪几类内部状态才能表现出恰当行为,以及agent的运行时解释器(interpreter)循环怎么处理这些状态。
划分:
  - 三分总述(原文): 'One such architecture views the system as a rational agent having certain mental attitudes of Belief, Desire and Intention (BDI), representing, respectively, the information, motivational, and deliberative states of the agent.'
  - Belief(信念): 'We call such a component the system's beliefs... beliefs can be viewed as the informative component of system state.'
  - Desire(愿望): 'We call this component the system's desires, which can be thought of as represent[ing]...'(原文此处被截断于组件命名句;愿望被作者明确与目标(goals)区分,见下条脚注)
  - 愿望与目标的区分(脚注,原文): 'We distinguish desires from goals as they are defined, for example, in the AI literature in that they may be many at any instant of time and may be mutually incompatible.'
  - Intention(意图): 'We call this additional state component the system's intentions. In essence, the intentions of the system capture the deliberative component of the system.'
  - 解释器循环伪代码(原文,逐字): 'options := option-generator(event-queue); selected-options := deliberate(options); update-intentions(selected-options);' 并说明: 'At the beginning of every cycle, the option generator reads the event queue and returns a list of options. Next, the deliberator selects a subset of options to be adopted and adds these to the intention structure. If there is an intention to perform an atomic action at this point in time, the agent then executes it.'
  - 四大功能模块(原文列举): '...namely, option generation, deliberation, execution, and intention handling.'
权威性: 该论文本身引用量约3148+(scispace/Semantic Scholar数据),是BDI从理论logic(Rao & Georgeff 1991)走向可实现interpreter架构最常被引用的桥梁论文;BDI三分法后被Georgeff, Pell, Pollack, Tambe, Wooldridge在《The Belief-Desire-Intention Model of Agency》(ATAL'98/1999)进一步阐述为'Beliefs represent the informational state...Desires represent the motivational state...Intentions represent the deliberative state'(Wikipedia转述,未逐字核对原文);BDI 架构衍生出的 PRS、dMARS、JACK、Jason 等系统被广泛用于工业级multi-agent部署(如论文中的空中交通管理案例)。
局限: Rao & Georgeff 自己在文中承认这一具体解释器'不是一个实用的理性推理系统'(架构基于逻辑封闭的belief/desire/intention集合,可证明性过程不可计算),且未说明option generator和deliberation过程如何做到满足实时性要求;三分法本身源自哲学家 Michael Bratman 1987年的《Intentions, Plans, and Practical Reason》对人类实践推理的理论,移植到计算系统时desire与goal、intention与commitment之间的边界在不同后续实现里并不统一。

### F3. SOAR 认知架构模块划分(及其2023-2025年与LLM结合的复活工作)
来源: John E. Laird, "Introduction to the Soar Cognitive Architecture", arXiv:2205.03854 (May 2022,作者本人撰写的权威综述); 复活工作例:Siyu Wu, Alessandro Oltramari, Jonathan Francis, C. Lee Giles, Frank E. Ritter, "Cognitive LLMs: Towards Integrating Cognitive Architectures and Large Language Models for Manufacturing Decision-making" (LLM-ACTR), arXiv:2408.09176 (Aug 2024)。
划分对象: Soar把一个通用认知agent划分成哪些任务无关的模块(短期/长期记忆种类、处理模块、学习机制),以及它的decision cycle(决策周期)分成哪几个阶段来处理这些模块间的交互。
划分:
  - 整体模块列举(原文): 'Figure 1 shows the structure of Soar, which consists of interacting task-independent modules. There are short-term and long-term memories, processing modules, learning mechanisms, and interfaces between them.'
  - Working Memory(工作记忆,原文): 'Working memory maintains an agent's situational awareness, including perceptual input, intermediate reasoning results, active goals, hypothetical states, and buffers for interacting with semantic memory, episodic memory, the spatial-visual system (SVS), and the motor system.'
  - Procedural Memory 与 Working Memory 的驱动关系(原文): 'In Soar, agent behavior is driven by the interaction between the contents of working memory, which describe the agent's current goals, situation, and intermediate results of reasoning, and procedural memory, which encodes the agent's skills and processing knowledge.'
  - Semantic/Episodic Memory(原文): 'Other modules provide nonsymbolic reasoning (SVS) and long-term declarative knowledge (semantic and episodic memory).'
  - 决策周期四阶段(原文,依次列出): '2.1 Input Phase...the input phase processes data from perception, SVS, and retrievals from semantic and episodic memory, and adds that information to the associated buffers in working memory.' / '2.2 Elaboration Phase...where rules fire that elaborate the situation..., propose operators, and evaluate operators.' / '2.3 Operator Selection Phase...a fixed decision procedure processes the contents of preference memory to choose the current operator.' / '2.4 Operator Application Phase...operator application rules that match the selected operator fire to apply it.'
  - Impasse(僵局)三类型引入(原文): 'There are three types of impasses that correspond to the different types of failures of the decision procedure that are related to the different types of knowledge described earlier: operator proposal, operator evaluation, and operator application.'
  - 学习机制chunking(原文): 'chunking is a learning mechanism that converts deliberate, sequential reasoning into parallel rule firings.'
  - 2024年LLM复活工作摘要(LLM-ACTR,原文,针对ACT-R但同类复活范式): 'we introduce LLM-ACTR, a novel neuro-symbolic architecture that provides human-aligned and versatile decision-making by integrating the ACT-R Cognitive Architecture with LLMs. Our framework extracts and embeds knowledge of ACT-R's internal decision-making process as latent neural representations, injects this information into trainable LLM adapter layers, and fine-tunes the LLMs for downstream prediction.'
权威性: Laird本人是Soar自1983年创立以来的核心作者,该arXiv综述是Soar官方文档体系的一部分(soar.eecs.umich.edu发布)并被CoALA论文直接引用为认知架构先例;LLM-ACTR/Cognitive LLMs发表并在Design for Manufacturing任务上做实验,是2024年学界把经典认知架构(ACT-R/SOAR)与LLM结合的代表工作之一,另有CogRec(arXiv:2512.24113,融合Soar与LLM做推荐)、Joshi & Ustun (AAAI Symposium 2024)、Kirk, Wray, Lindes, Laird (AAAI'24)等同期工作印证这是一条活跃的复活路线。
局限: Soar/ACT-R的模块划分是为符号推理系统设计的,其'chunking'学习机制、production rule匹配等机制与当代LLM的连续向量表示天然不兼容,复活工作普遍需要额外做'提取ACT-R内部决策过程为潜在神经表示'这类桥接工程,而非直接嫁接;这类工作目前仍以特定领域任务(如制造决策)的小规模实验为主,尚未证明能扩展到通用agent场景。

### F4. Wang et al. 综述四模块框架
来源: Lei Wang et al., "A Survey on Large Language Model based Autonomous Agents", arXiv:2308.11432 (Aug 2023), 发表于 Frontiers of Computer Science (2024).
划分对象: 把LLM-based autonomous agent的整体构造(construction)划分成哪几个模块。
划分:
  - 总述(原文): '...the overall structure of our framework is illustrated in Figure 2, which is composed of a profiling module, a memory module, a planning module, and an action module.'
  - Profiling Module(画像模块,原文): 'The purpose of the profiling module is to identify the role of the agent.'
  - Memory Module(记忆模块,原文): 'It stores information perceived from the environment and leverages the recorded memories to facilitate future actions.'
  - Planning Module(规划模块,原文): 'The planning module aims to empower the agents with such human capability, which is expected to make the agent behave more reasonably, powerfully, and reliably.'
  - Action Module(行动模块,原文): 'The action module is responsible for translating the agent's decisions into specific outcomes.'
权威性: Semantic Scholar/SciSpace 引用量约461+;发表于 Frontiers of Computer Science(Springer);与Xi et al. (2309.07864)并列为2023年最常被引用的两篇LLM agent综述,常被后续agent工作(尤其是社会科学模拟类agent论文)作为标准分类法引用。
局限: 四模块划分聚焦于单agent的内部构造,对multi-agent协作、工具使用的深度技术细节展开有限;'profiling module'这一提法主要服务于social simulation场景(如生成式agent的角色扮演),在纯任务型agent(如代码agent)语境下不总是被采用同名模块。

### F5. Xi et al. 综述三部分框架(Brain-Perception-Action)
来源: Zhiheng Xi et al. (Fudan NLP Group), "The Rise and Potential of Large Language Model Based Agents: A Survey", arXiv:2309.07864 (Sep 2023, v3)。
划分对象: 把LLM-based agent的概念框架(conceptual framework)划分成哪三个关键部分,并说明各部分对应人类的哪个系统。
划分:
  - 三部分总述(原文): 'we present a general conceptual framework of an LLM-based agent composed of three key parts: brain, perception, and action'
  - Figure 2 图注(原文): 'Conceptual framework of LLM-based agent with three components: brain, perception, and action. Serving as the controller, the brain module undertakes basic tasks like memorizing, thinking, and decision-making. The perception module perceives and processes multimodal information from the external environment, and the action module carries out the execution using tools and influences the surroundings.'
  - Brain(原文): 'The brain is the core of an AI agent because it not only stores knowledge and memories but also undertakes indispensable functions like information processing and decision-making. It can present the process of reasoning and planning, and cope well with unseen tasks, exhibiting the intelligence of an agent.'
  - Perception(原文): 'Its core purpose is to broaden the agent's perception space from a text-only domain to a multimodal sphere that includes textual, auditory, and visual modalities.'
  - Action(原文): 'we present the action module designed to expand the action space of an agent...Specifically, we empower the agent with embodied action ability and tool-handling skills, enabling it to adeptly adapt to environmental changes, provide feedback, and even influence and mold the environment.'
  - Brain模块内部再拆五个子能力(原文,章节标题逐字): 'Natural Language Interaction §3.1.1 / Knowledge §3.1.2 / Memory §3.1.3 / Reasoning & Planning §3.1.4 / Transferability & Generalization §3.1.5'
  - 对应人体类比(原文): '...the perception module, corresponding to human sensory systems such as the eyes and ears, perceives changes in the external environment...the action module, corresponding to human limbs, carries out the execution with the assistance of tools and leaves an impact on the surroundings.'
权威性: 作者团队为复旦大学NLP组(Fudan NLP Group),论文长达86页,是2023年最常被引用的LLM agent综述之一(GitHub配套仓库 WooooDyy/LLM-Agent-Paper-List);与Wang et al.综述并列成为该领域引用最广的两份分类法参照,后续大量agent论文(尤其中文学界)沿用Brain-Perception-Action三分法组织自己的related work章节。
局限: Brain-Perception-Action的类比借助'人类感官-大脑-四肢'的直观映射,作者自陈'The framework can be tailored for different application scenarios, i.e. not every specific component will be used in all studies'——即三部分并非每个具体agent实现都必须全部具备;Brain内部的memory/reasoning/planning子模块之间边界与CoALA的memory四分法、Wang et al.的planning module有大量概念重叠,三份综述之间的术语并未统一。

### F6. Huang et al. LLM Agent Planning 综述五分类taxonomy
来源: Xu Huang, Weiwen Liu, Xiaolong Chen, Xingmei Wang, Hao Wang, Defu Lian, Yasheng Wang, Ruiming Tang, Enhong Chen, "Understanding the planning of LLM agents: A survey", arXiv:2402.02716 (Feb 2024)。
划分对象: 把LLM-agent的规划(planning)能力相关工作,按'如何改进规划'这一问题划分成哪五个方向(taxonomy)。
划分:
  - taxonomy总述(原文): 'we present a novel and systematic taxonomy for LLM-based agent plannning that divides existing works into five important categories, covering task decomposition, multi-plan selection, external module-aided planning, reflection and refinement and memory-augmented planning'
  - Task Decomposition(任务分解,原文): 'This kind of method adopts the idea of divide and conquer, decomposing the complicated into several sub-tasks and then sequentially planning for each sub-task.'
  - Multi-plan Selection(多方案选择,原文): 'This kind of method focuses on leading the LLM to "think" more, generating various alternative plans for a task. Then a task-related search algorithm is employed to select one plan to execute.'
  - External Planner-Aided Planning(外部规划器辅助,原文): 'This methodology is crafted to employ an external planner to elevate the planning procedure, aiming to address the issues of efficiency and infeasibility of generated plans, while the LLM mainly plays the role in formalizing the tasks.'
  - Reflection and Refinement(反思与精炼,原文): 'This methodology emphasizes improving planning ability through reflection and refinement. It encourages LLM to reflect on failures and then refine the plan.'
  - Memory-augmented Planning(记忆增强规划,原文): 'This kind of approach enhances planning with an extra memory module, in which valuable information is stored, such as commonsense knowledge, past experiences, domain-specific knowledge, et al. The information is retrieved when planning, serving as auxiliary signals.'
  - 五者关系说明(原文): 'The five directions are interconnected rather than mutually exclusive, often involving the concurrent adoption of multiple techniques.'
权威性: 作者自陈'To the best of our knowledge, this is the first work that comprehensively analyzes LLM-based agents from the planning abilities.'(首份专注LLM agent planning能力的系统综述);发表并在多个benchmark上做了实证评测(四个benchmark、多个代表性方法对比),被后续planning相关工作和agent综述引用为该细分领域的标准分类法。
局限: 五个方向本身'interconnected rather than mutually exclusive'(论文自陈),很多实际系统同时使用多种技术,分类边界依赖于'哪个是主要机制'的主观判断;该taxonomy发表于2024年2月,此后(尤其是test-time compute/长程reasoning模型出现后)规划范式发生了较大变化,分类法本身未随之更新。

### F7. Kolb 经验学习圈(Experiential Learning Cycle)
来源: David A. Kolb, "Experiential Learning: Experience as the Source of Learning and Development", Prentice Hall, 1984. AI论文引用实例: Fenia Christopoulou et al./相关团队, "Kolb-Based Experiential Learning for Generalist Agents with Human-Level Kaggle Data Science Performance" (Agent K), arXiv:2411.03562 (Nov 2024)。
划分对象: 把'学习'本身(不限于AI)划分为哪四个循环阶段(adaptive learning modes)。
划分:
  - 学习定义(原文,广泛转引自Kolb 1984原书,页码来源不完全统一,常见标注p.38或p.41): 'Learning is the process whereby knowledge is created through the transformation of experience. Knowledge results from the combination of grasping and transforming experience.'
  - Concrete Experience(具体经验): 学习者遭遇一个新经验或重新诠释已有经验的阶段
  - Reflective Observation(反思性观察): 学习者对该经验进行个人化反思的阶段
  - Abstract Conceptualization(抽象概念化): 学习者基于反思形成新想法或修改既有抽象概念的阶段
  - Active Experimentation(主动实验): 学习者把新想法付诸实践检验是否带来改变的阶段
  - AI论文引用实例(Agent K论文,arXiv:2411.03562,原文摘要逐字): 'Human expertise emerges through iterative cycles of interaction, reflection, and internal model updating, which are central to cognitive theories such as Kolb's experiential learning and Vygotsky's zone of proximal development...we propose a computational framework of Kolb's learning cycle with Vygotsky's ZPD for autonomous agents. Our architecture separates extrinsic (environment interaction) and intrinsic (internal reflection/abstraction) functions...'
权威性: Kolb 1984是教育学/组织行为学领域被引用最广的学习理论之一(被广泛用于医学教育、管理培训等领域的标准参照);Agent K论文(arXiv:2411.03562)是2024年一篇明确以Kolb学习圈为计算框架、并在81个真实Kaggle数据科学竞赛任务上做实证的AI agent论文,取得超过Kaggle Masters中位数的Elo-MMR成绩,是'AI论文正式引用Kolb作为理论依据'的直接实例;另有'SAMULE: Self-Learning Agents Enhanced by Multi-level Reflection'(arXiv:2509.20562)、'Adaptive Self-improvement LLM Agentic System'(arXiv:2502.02534)等多篇同期AI论文引用Kolb。
局限: 本轮未能直接打开Kolb 1984原书页面核实精确页码和四阶段的逐字定义原文(该书非公开PDF,只能通过多个二手教学网站交叉印证的转述定量确认,故'学习'定义那句标记为来自原书但页码有p.38/p.41两种说法并存,未能翻到原书核实,四阶段本身的具体逐字定义未能核实到原书原句,只能给出学界通行的转述性定义);Kolb循环最初是为人类学习设计的理论,移植到AI agent时'reflection'与'abstract conceptualization'两阶段在具体实现里边界并不总是清晰可分。

### F8. Dreyfus 技能习得五阶段模型
来源: Stuart E. Dreyfus and Hubert L. Dreyfus, "A Five-Stage Model of the Mental Activities Involved in Directed Skill Acquisition", ORC 80-2, University of California Berkeley Operations Research Center, Feb 1980(后收录为 Dreyfus & Dreyfus, "Mind Over Machine", Free Press, 1986)。AI论文引用实例: "Nurture-First Agent Development: Building Domain-Expert AI Agents Through Conversational Knowledge Crystallization", arXiv:2603.10808 (2026)。
划分对象: 把人类习得一项技能的过程划分为哪五个发展阶段。
划分:
  - 五阶段列举(学界通行转述,原始1980报告题名即为'Five-Stage Model'): novice(新手) / advanced beginner(高级新手) / competent(胜任) / proficient(精通) / expert(专家)
  - 阶段性质概述(学界通行转述): novice阶段单纯遵循规则和指令行事;随阶段推进逐渐减少对抽象规则的依赖、增加对具体情境的直接把握;expert阶段不再依赖规则做决策,能够不假思索地采取行动
  - AI论文引用实例(Nurture-First Agent Development,arXiv:2603.10808,原文逐字): 'The Dreyfus model of skill acquisition [14]—which identifies five stages from novice to expert—further informs our understanding of how expertise develops through progressive stages, a trajectory that NFD mirrors in the agent context.'
  - 该论文参考文献原文: 'Dreyfus and Dreyfus [1986] Hubert L. Dreyfus and Stuart E. Dreyfus. Mind Over Machine: The Power of Human Intuition and Expertise in the Era of the Computer. Free Press, New York, 1986.'
权威性: 该模型最初由美国空军科学研究办公室(US Air Force Office of Scientific Research)资助研究产出,后成为技能习得理论的经典参照,被广泛用于护理教育、医学培训(如超声培训)、软件工程职业发展(Dreyfus model of skill acquisition在敏捷开发/Scrum圈内也是常见参照)等领域的标准分期法;2026年的'Nurture-First Agent Development'论文明确将其作为AI agent能力发展阶段的理论依据直接引用。
局限: 本轮未能直接打开1980年原始DTIC报告全文核实五阶段各自的逐字定义原文(DTIC PDF为扫描件且访问受限,曾遇到429错误未能重试成功),五阶段名称本身来自题名与广泛转引,未做到从原始报告逐字抄录每阶段定义,只能确认阶段名称序列及大方向描述、以及AI论文引用该模型的原文；该模型本身也长期受到质疑(是否真的存在离散阶段而非连续谱系),在专家决策研究中并非无争议的共识。

## key_findings

1. **CoALA用三个正交维度(memory/action space/decision-making)而非线性阶段来组织language agent,其中memory细分四类(working/episodic/semantic/procedural),action细分四类(reasoning/retrieval/learning/grounding),decision cycle内部再分propose/evaluate/select三个子阶段。**
   证据: arxiv.org/html/2309.02427v3 原文逐句核实,包括'CoALA organizes agents along three key dimensions...'与'During planning, reasoning and retrieval can be flexibly applied to propose, evaluate, and select actions...'
   来源: Sumers et al., CoALA, arXiv:2309.02427, TMLR 2024

2. **BDI架构的三分(Belief/Desire/Intention)对应agent的information/motivational/deliberative三种状态,其可执行interpreter循环是一个三步伪代码:option-generator读事件队列生成options,deliberate从options中选出subset加入intention structure,再update-intentions。**
   证据: 从Rao & Georgeff 1995原始ICMAS论文PDF用pdftotext提取全文后逐句核实,含原文伪代码'options := option-generator(event-queue); selected-options := deliberate(options); update-intentions(selected-options);'
   来源: Rao & Georgeff, BDI Agents: From Theory to Practice, ICMAS-95, 1995

3. **SOAR的decision cycle分四阶段(Input/Elaboration/Operator Selection/Operator Application),遇到知识不足或冲突时产生三类impasse(operator proposal/evaluation/application对应的失败类型),chunking机制把substate里的deliberate reasoning编译成并行rule firing从而学习。**
   证据: 从Laird本人撰写的arXiv:2205.03854原文提取核实,含'2.1 Input Phase...2.2 Elaboration Phase...2.3 Operator Selection Phase...2.4 Operator Application Phase'及三类impasse的原文列举
   来源: Laird, Introduction to the Soar Cognitive Architecture, arXiv:2205.03854, 2022

4. **2024年出现把ACT-R/SOAR这类经典符号认知架构与LLM结合的复活工作(如LLM-ACTR),做法是把认知架构内部决策过程提取为潜在神经表示、注入LLM adapter层做微调,在制造决策任务上验证優于纯chain-of-thought的LLM baseline。**
   证据: arXiv:2408.09176摘要原文逐字核实
   来源: Wu, Oltramari, Francis, Giles, Ritter, Cognitive LLMs (LLM-ACTR), arXiv:2408.09176, 2024

5. **Wang et al.综述把LLM-based autonomous agent划分为profiling/memory/planning/action四模块,Xi et al.综述则划分为brain/perception/action三部分(brain内部再拆五个子能力:natural language interaction/knowledge/memory/reasoning&planning/transferability&generalization),两份综述是2023年该领域被引用最广的两份分类法参照。**
   证据: 分别从arxiv.org/html/2308.11432v6与本地pdftotext提取的arXiv:2309.07864v3全文中逐句核实两份综述各自的模块定义原文
   来源: Wang et al., arXiv:2308.11432 (Frontiers of Computer Science 2024); Xi et al., arXiv:2309.07864, Fudan NLP Group

6. **Huang et al.的LLM agent planning综述给出五分类taxonomy(task decomposition/multi-plan selection/external planner-aided planning/reflection and refinement/memory-augmented planning),并自称是首份系统分析LLM agent规划能力的综述,同时承认五者'interconnected rather than mutually exclusive'。**
   证据: 从本地pdftotext提取的arXiv:2402.02716全文中逐句核实五个类别的原文定义句
   来源: Huang et al., Understanding the planning of LLM agents: A survey, arXiv:2402.02716, 2024

7. **Kolb经验学习圈(concrete experience/reflective observation/abstract conceptualization/active experimentation)被2024年'Agent K'论文正式作为计算框架采用,在真实Kaggle数据科学竞赛(81个任务)上取得超过Kaggle Masters中位数的成绩,是AI论文把Kolb理论直接工程化落地的实例。**
   证据: arXiv:2411.03562摘要原文逐字核实,含'we propose a computational framework of Kolb's learning cycle with Vygotsky's ZPD for autonomous agents'
   来源: Kolb-Based Experiential Learning for Generalist Agents (Agent K), arXiv:2411.03562, Nov 2024

8. **Ericsson等1993年对deliberate practice的原始定义强调其是'专为提升当前表现水平而设计的活动'、'需要努力且本身并不天然令人愉悦'，被2025年'Improving the Scaling Laws of Synthetic Data with Deliberate Practice'一文直接引用作为合成数据生成方法的理论依据。**
   证据: 从Fermat's Library转载的Ericsson 1993原文摘录核实定义句,并从arxiv.org/html/2502.15588v1核实引用原文'A key principle underlying learning in human is deliberate practice (DP)...(Ericsson et al., 1993)'
   来源: Ericsson, Krampe & Tesch-Römer, The Role of Deliberate Practice in the Acquisition of Expert Performance, Psychological Review, 1993; 引用实例见arXiv:2502.15588

9. **Dreyfus技能习得五阶段模型(novice/advanced beginner/competent/proficient/expert)被2026年'Nurture-First Agent Development'论文明确引用,作为AI agent能力渐进发展轨迹的理论参照。**
   证据: 从arxiv.org/html/2603.10808v1核实原文引用句'The Dreyfus model of skill acquisition [14]—which identifies five stages from novice to expert—further informs our understanding...'
   来源: Nurture-First Agent Development, arXiv:2603.10808

## sources
- [读] Cognitive Architectures for Language Agents (HTML v3) — https://arxiv.org/html/2309.02427v3
- [摘要] Cognitive Architectures for Language Agents (arXiv abstract) — https://arxiv.org/abs/2309.02427
- [摘要] Semantic Scholar - CoALA paper page — https://www.semanticscholar.org/paper/Cognitive-Architectures-for-Language-Agents-Sumers-Yao/e4bb1b1f97711a7634bf4bff72c56891be2222e6
- [读] BDI Agents: From Theory to Practice (原始PDF, Rao & Georgeff 1995) — https://cdn.aaai.org/ICMAS/1995/ICMAS95-042.pdf
- [读] AAAI paper page - BDI Agents: From Theory to Practice — https://aaai.org/papers/icmas95-042-bdi-agents-from-theory-to-practice/
- [摘要] SciSpace - BDI Agents citation count — https://scispace.com/papers/bdi-agents-from-theory-to-practice-4cue39s538
- [读] The Belief-Desire-Intention Model of Agency (Georgeff, Pell, Pollack, Tambe, Wooldridge) — https://www.cs.ox.ac.uk/people/michael.wooldridge/pubs/atal98b.pdf
- [读] Belief–desire–intention software model - Wikipedia — https://en.wikipedia.org/wiki/Belief%E2%80%93desire%E2%80%93intention_software_model
- [读] Introduction to the Soar Cognitive Architecture (Laird, arXiv:2205.03854) — https://arxiv.org/pdf/2205.03854
- [读] Cognitive LLMs / LLM-ACTR (arXiv:2408.09176 abstract) — https://arxiv.org/abs/2408.09176
- [读] A Survey on Large Language Model based Autonomous Agents (HTML v6, Wang et al.) — https://arxiv.org/html/2308.11432v6
- [摘要] SciSpace - Wang et al. survey citation count — https://scispace.com/papers/a-survey-on-large-language-model-based-autonomous-agents-1qsfp75nbk
- [读] The Rise and Potential of Large Language Model Based Agents: A Survey (Xi et al., arXiv:2309.07864v3, extracted PDF text) — https://arxiv.org/pdf/2309.07864v3
- [读] Understanding the planning of LLM agents: A survey (Huang et al., arXiv:2402.02716, extracted PDF text) — https://arxiv.org/pdf/2402.02716
- [读] Kolb's experiential learning - Wikipedia — https://en.wikipedia.org/wiki/Kolb's_experiential_learning
- [读] Kolb-Based Experiential Learning for Generalist Agents (Agent K), arXiv:2411.03562 — https://arxiv.org/abs/2411.03562
- [读] Fermat's Library - Ericsson 1993 annotated deliberate practice — https://fermatslibrary.com/p/787c0427
- [读] Improving the Scaling Laws of Synthetic Data with Deliberate Practice (arXiv:2502.15588v1 HTML) — https://arxiv.org/html/2502.15588v1
- [读] Nurture-First Agent Development (arXiv:2603.10808v1 HTML) — https://arxiv.org/html/2603.10808v1
- [摘要] A Five-Stage Model of the Mental Activities Involved in Directed Skill Acquisition (Dreyfus & Dreyfus, DTIC original report) — https://apps.dtic.mil/sti/tr/pdf/ADA084551.pdf


---

