

# 类别:AI agent 规则文件的统一管理与分发工具(单一源→多 agent 配置文件生成/同步生态)

## Ruler (intellectronica/ruler)
- **是什么**: 命令行工具:把规则、MCP 配置、skills 写在一份中心目录里,一条命令分发生成 30+ 种 AI 编码 agent 各自要求格式的配置文件(CLAUDE.md/AGENTS.md/.cursor/mcp.json/.clinerules 等)。
- **做法**: .ruler/ 目录树: AGENTS.md(主规则,新默认)/ruler.toml(配置)/instructions.md(遗留,仅AGENTS.md缺失时用)/skills/<name>/SKILL.md(必需)+helper文件/agents/*.md(子agent定义,YAML前置+Markdown)。ruler.toml 字段:default_agents=[...]; [mcp] enabled/merge_strategy(merge|overwrite); [mcp_servers.X] command/args; [gitignore] enabled/local; [backup] enabled; [agents.X] enabled/output_path。加载优先级(从高到低):项目根 AGENTS.md > .ruler/AGENTS.md > .ruler/instructions.md(仅缺失AGENTS.md时) > .ruler/ 下其余 .md 文件按排序。生成文件会被自动写入 .gitignore 的托管块(START/END Ruler Generated Files 注释包裹),并且每处插入 `<!-- Source: <relative_path_to_md_file> -->` 标注来源;覆盖前默认打 .bak 备份。CLI: --gitignore/--no-gitignore/--nested 等。
- **when表达**: 几乎没有语义级 when——生成物是把源文件按固定优先级顺序整份拼接进目标文件,始终全量注入,不做按文件类型/任务类型的条件裁剪。唯一的'范围'语义是 nested 规则:子目录放自己的 .ruler/ 可对该子树追加/覆盖规则(--nested 或 nested=true 开启,开启后子层不能再关闭,只能被上层强制)。
- **记录回路**: 无内置漂移检测。社区自行在 CI 里跑法:`ruler apply` 后 `git status --porcelain -- AGENTS.md CLAUDE.md`,若非空则判定'已提交的 agent 文件与 .ruler/ 源不同步'并 fail——纯文件级字符串 diff,不是语义级检测。
- **本体-接口同步**: 单向、无条件全量分发:一次 apply 把中心源整份复制进每个 agent 的目标文件,没有增量合并逻辑,也没有从目标文件读回修改并提示冲突的机制(有覆盖就直接覆盖,备份只是保险丝)。
- **开源情况**: MIT License,GitHub intellectronica/ruler,约2.8k star、152 fork、996次提交,最新版本 v0.3.44(2026-06-30),npm 包 @intellectronica/ruler,活跃维护中。
- **可抄**: 1) 生成物里插入 `<!-- Source: <path> -->` 注释注明来源——这正是我们'接口层只存指针,本体绝不进接口文件'的最低成本落地范式,值得直接照搬到 hook/skill/CLAUDE.md 里凡摘自手册的段落。2) nested 规则(子目录 .ruler/ 追加/覆盖)对应我们手册条目'规模与接入'字段的执行层实现——可以照抄这套目录级覆盖判定。3) CI 里'apply 后 git diff 判漂移'的土办法虽然简陋,但证明了'定期重放中心源生成流程,把 diff 当漂移信号'这条路径可行,可以直接用作我们'执行偏离手册要记偏离'回路的第一版实现。
- **差距**: when 语义几乎为零(全量硬拼接,没有条件加载);没有决策记录/判例引用/权威分层,规则只有'写了什么'没有'为什么、谁定的、能不能推翻';漂移检测停留在文件级字符串比对,检测不到'执行时实际发生的判断偏离了源头规则'这种语义级偏离。
- **来源**: https://github.com/intellectronica/ruler | https://ai.intellectronica.net/ruler | https://www.npmjs.com/package/@intellectronica/ruler

## rulesync (dyoshikawa/rulesync)
- **是什么**: 与 Ruler 同类的单一源多发工具,额外强调'convert'——不经过中心源、在已有的两种专有格式之间直接互转,以及从既有工具配置反向 import 成统一规则。
- **做法**: .rulesync/rules/*.md 为源文件目录。CLI: `rulesync init`(建目录+示例+配置)/`rulesync generate --targets "*" --features "*"`(从统一源生成)/`rulesync import --targets claudecode`(从已有工具配置反向导入成中心规则)/`rulesync convert --from cursor --to copilot,claudecode`(专有格式互转,不经中心源)/`rulesync fetch`(拉取外部 skills)。支持 30+ 目标工具,矩阵覆盖 rules/ignore/mcp/commands 等能力列,Claude Code 侧输出到 `.claude/memories/*.md`。
- **when表达**: 文档未见按文件路径/任务类型的条件加载字段,生成逻辑与 Ruler 类似是按 --features 选择'规则/忽略文件/MCP/命令'等类别整体生成,粒度是'类别开关'而非'内容级 when'。
- **记录回路**: 文档中未见漂移检测或运行记录机制。
- **本体-接口同步**: convert 命令允许两个专有格式之间不经过中心源直接互转,是比 Ruler 更灵活的多向同步,但仍是纯格式转换,不做语义合并或冲突提示。
- **开源情况**: MIT License,GitHub dyoshikawa/rulesync,约1.2k star、130 fork、4412次提交,最新版本 v9.6.0(2026-07),同时发布 npm 包与 PyPI 包(rulesync),提交频率很高、非常活跃。
- **可抄**: import 命令(从已存在的工具专有配置反向导入成中心规则)是'新判例经聚类成规则候选人裁后进手册'回路的雏形,只是它做的是机械格式转换、不做语义聚类去重——提醒我们做反向固化器时,'先能格式对齐、再谈语义聚类'是更稳妥的分阶段路径。convert 的多向互转能力也提示我们'接口投影'不该假设只有'本体→接口'一个方向,执行管线自己长出的判断也要有路径喂回本体草稿。
- **差距**: 同 Ruler,没有 when 的分级语义、没有决策空间/权威等级/谁拍板字段,是纯静态文件生成器,不追踪'规则今天被哪个 agent 真正引用了几次'。
- **来源**: https://github.com/dyoshikawa/rulesync | https://github.com/dyoshikawa/rulesync/blob/main/skills/rulesync/SKILL.md | https://dev.to/dyoshikawatech/rulesync-published-a-tool-to-unify-management-of-rules-for-claude-code-gemini-cli-and-cursor-390f

## AGENTS.md 开放标准
- **是什么**: 不是工具而是一份格式约定:给编码 agent 一个可预测的项目说明书位置(README 给人看、AGENTS.md 给 agent 看),意在替代各家专有的 CLAUDE.md/.cursorrules/GEMINI.md 各写一份的局面。
- **做法**: 纯自由 Markdown,无强制字段,官方原话'AGENTS.md is just standard Markdown, use any headings you like'。常见章节:项目概览、构建测试命令、代码风格约定。放置位置:项目根目录,或任意子目录(嵌套覆盖)。
- **when表达**: 唯一的 when 维度是'目录树最近优先'——支持嵌套多个 AGENTS.md,agent 读取离当前被编辑文件最近的那一份,离得更近的覆盖更远的(例如 OpenAI 主仓库有 88 个嵌套实例)。没有更细粒度的按文件类型/任务类型触发,是纯静态路径覆盖语义,比 Cursor、Copilot 都粗。
- **记录回路**: 无——它只是一个文件位置约定,不带任何工具链或记录机制。
- **本体-接口同步**: 没有中心生成器,各工具各自决定要不要原生读它、怎么读:Aider 靠 .aider.conf.yml 里指定读取,Gemini CLI 靠 .gemini/settings.json 指定,Cursor/Codex/Copilot 等 20+ 工具原生支持。Claude Code 不原生读 AGENTS.md,需要用户手动 symlink(`ln -s AGENTS.md CLAUDE.md`,Windows 下需管理员权限或开发者模式)或在 CLAUDE.md 里写 `@AGENTS.md` 的 import 语法接进去——同步完全靠约定而非分发机制,每接入一个新 agent 都要显式声明怎么接。
- **开源情况**: 开放规范,非代码库。规范仓库 agentsmd/agents.md(GitHub)。治理方从 OpenAI 主导、Google/Cursor/Factory/Sourcegraph 等联合参与,于2025年8月正式确立为开放规范,现移交 Linux Foundation 旗下 Agentic AI Foundation 托管。据统计截至2025年12月已有超6万开源项目采用、20+工具支持。
- **可抄**: 1) '目录树最近优先'的嵌套覆盖语义简单直接,可以直接复用到手册条目的'规模与接入'字段——条目声明它在哪层目录/子系统生效,子目录条目覆盖父目录。2) 它把'给 agent 的说明'和'给人的 README'显式分成两份文件,提醒我们语义本体(给决策系统读)和面向用户的项目文档也该分开存放,不要混写。
- **差距**: 只解决'内容放哪',完全不解决 when 的细粒度(不能按文件类型/任务类型选择性加载)、不解决多 agent 专有格式的自动同步(各家实现程度不一,Claude Code 干脆不读)、更谈不上判例记录或权威分层——它是我们'接口层'思路里最原始的版本,只有'约定存放位置',没有分发机制,也没有本体结构。
- **来源**: https://agents.md/ | https://github.com/agentsmd/agents.md | https://www.ssw.com.au/rules/symlink-agents-to-claude | https://agyn.io/blog/claude-md-agents-md-compatibility

## GitHub Copilot 自定义指令(copilot-instructions.md + applyTo)
- **是什么**: 官方产品内置的分层指令机制:全仓库指令面向整仓库始终注入,路径限定指令通过 glob 把内容限定到匹配的文件路径——是商业化产品里对'按文件路径条件触发'表达最直接的实现之一。
- **做法**: 全仓库:`.github/copilot-instructions.md`。路径限定:`.github/instructions/*.instructions.md`(或工作目录下的 `.github/instructions/`),文件头 frontmatter 只有 `applyTo` 一个核心字段(加可选 `excludeAgent`)。目前路径限定指令仅在 GitHub.com 的 Copilot cloud agent 与 Copilot Code Review 场景确认生效,IDE 内(VS Code)是否读取路径限定指令文档未详述。
- **when表达**: 两维:(a) 路径 glob 匹配——`applyTo: "app/models/**/*.rb"`,逗号分隔多模式如 `"**/*.ts,**/*.tsx"`,纯声明式无 AI 判断;(b) `excludeAgent` 按消费者身份过滤,例如把某份路径指令标记为只给 code-review 这类 cloud agent 读、不给交互式 IDE 读。没有 Cursor 那种'agent 自己读描述判断相关性'的智能筛选档位。全仓库指令与匹配到的路径限定指令是叠加生效(都注入),不是互斥择一;个人指令 > 仓库指令 > 组织指令。
- **记录回路**: 无。
- **本体-接口同步**: 无中心生成器,每个仓库自己手写这些文件;没有跨仓库/跨项目统一源工具(即没有 Ruler 那一层),同步靠人工在每个仓库分别维护。
- **开源情况**: 闭源(GitHub 官方产品功能),机制通过官方文档公开可查。
- **可抄**: `excludeAgent` 这个'按接入面身份排除'的字段值得抄——我们的接口投影(hook/skill/CLAUDE.md)可能要给手册条目的不同投影版本,用类似字段声明'这条不下发到哪个执法点'很直接。'全仓库+路径限定都生效、按来源叠加而非互斥择一'的合并规则也值得参考,避免我们做成'匹配到最细那条就丢掉全局约束'。
- **差距**: 纯静态 glob 匹配,没有语义/任务类型触发,没有'agent 自主判断相关性'这一档;没有中心本体和分发机制;完全没有判例记录或运行历史可视化。
- **来源**: https://docs.github.com/copilot/customizing-copilot/adding-custom-instructions-for-github-copilot | https://github.blog/changelog/2025-09-03-copilot-code-review-path-scoped-custom-instruction-file-support/ | https://docs.github.com/en/copilot/tutorials/use-custom-instructions

## Cursor Rules
- **是什么**: 调研到的所有产品里'when'表达力最强的一档:四种规则类型对应四种触发语义,并叠加 Team > Project > User 三层作用域与优先级。
- **做法**: `.cursor/rules/*.mdc`(必须 .mdc 扩展名,.md 会被忽略),支持子文件夹组织。三层作用域:User Rules(Cursor 客户端全局设置,不进版本控制,个人风格偏好)、Project Rules(`.cursor/rules/`,进版本控制,团队共享编码标准)、Team Rules(Team/Enterprise 订阅,组织级强制)。优先级 Team Rules → Project Rules → User Rules,从高到低。另外原生支持 Nested AGENTS.md(子目录级,就近优先,与上述 .mdc 规则并存)。创建方式:chat 里 `/create-rule`,或 UI 中 Customize → Rules → Add Rule。
- **when表达**: `.mdc` frontmatter 三字段 alwaysApply(bool)/description(string)/globs(string,逗号分隔多 pattern)组合出四态:①alwaysApply=true → Always Apply,每次对话都注入,忽略 description/globs;②alwaysApply=false 且 globs 非空 → Auto Attached,文件匹配上下文出现时才注入;③alwaysApply=false 且 description 非空、globs 为空 → Agent Requested,Agent 读 description 自行判断和当前任务相不相关,判定相关才把全文拉进来——四态里唯一一档把'要不要用这条规则'的判断权交给模型自己读文本决定;④三者皆空 → Manual,仅在 chat 里 `@rule-name` 手动引用才生效。
- **记录回路**: 未发现——没有材料显示 Cursor 会记录'这条规则今天在哪次对话里被 Agent Requested 命中'这样的调用历史,也没有规则互引图谱。
- **本体-接口同步**: 三层作用域本身就是同步策略:User Rules 本地私有不同步、Project Rules 随仓库版本控制自然同步给所有协作者、Team Rules 由组织管理端统一下发且不可被 Project/User 层绕过。
- **开源情况**: 闭源产品功能,机制经官方文档 cursor.com/docs/rules.md 确认。
- **可抄**: alwaysApply/globs/description 三态组合表,尤其'description 非空+globs 为空时转交模型自主判断'的设计,几乎就是我们手册条目'什么时候 when'字段的执行层实现范式——可以直接照抄这套三态判定表进执行管线的 when 触发元数据。Team>Project>User 三层优先级也是现成可抄的权威分层模型,直接映射我们'谁拍板与推翻条件'字段。
- **差距**: 规则本身仍是自由文本,没有结构化的'判断怎么下/判例引用'字段;Agent Requested 这一档把决策全交给模型运行时判断,没有回灌机制去校验'这次判断对不对、要不要把这次判定沉淀成新规则';没有全链路可视化(手册互引图/管线运行历史高亮),规则之间也没有引用图谱。
- **来源**: https://cursor.com/docs/rules.md | https://forum.cursor.com/t/cursor-rules-mdc-clarification/104879

## Claude Code Skills + Plugins + Marketplaces
- **是什么**: 我们自身所在的生态:SKILL.md 是最小的 when 载体,plugin.json 把 skills/agents/hooks/MCP/LSP 打包成可分发单元,marketplace.json 做发现与版本化分发——这条链路本身就是一个'中心源→多接入面分发'的现成样本,可以直接对照我们要建的三层设计。
- **做法**: SKILL.md:目录形式 `skills/<name>/SKILL.md`(+ 可选 reference.md/scripts/),frontmatter 关键字段是 `name`(控制调用名,缺省退化为安装目录名)与 `description`。plugin.json 顶层字段:name(必填)/displayName/version/description/author/homepage/repository/license/keywords/skills/commands/agents/hooks/mcpServers/outputStyles/lspServers/experimental.{themes,monitors}/dependencies/userConfig/channels/defaultEnabled。组件路径字段里 commands/agents/outputStyles/themes/monitors 是'替换默认目录'语义,skills 是'追加默认目录'语义。hooks 在 `hooks/hooks.json` 声明事件 matcher(PreToolUse/PostToolUse/SessionStart/InstructionsLoaded 等三十余种)+ action(command/http/mcp_tool/prompt/agent 五种类型)。marketplace.json 位于仓库根 `.claude-plugin/marketplace.json`,列每个 plugin 的 name/source/version 等;版本解析优先级为 plugin.json.version > marketplace 条目.version > git commit SHA(未设置 version 时每次 commit 都算新版本,设置了 version 则必须手动 bump 否则用户拉不到更新)。分发面:官方市场(claude-plugins-official,自动可用)/社区市场(anthropics/claude-plugins-community,每个插件 pin 到 commit SHA,过安全审核)/自建市场(GitHub repo、Git URL、本地路径、远程 URL 四种 source)。团队场景下 `.claude/settings.json` 的 `extraKnownMarketplaces` 能让团队自动装同一套市场。
- **when表达**: SKILL.md frontmatter 的 `description` 字段是唯一 when 载体,纯自然语言,固定句式'……的统一入口。Use when 用户要……'——本质是把 when 写成一句可检索的判断句,而不是结构化字段;每轮所有已装 skill 的 description 常驻可见,由模型自主判断要不要展开调用全文,和 Cursor 的 Agent Requested 同源,但没有'先选中再展开'的两段式(Cursor 只在判定相关后才拉全文,Skills 是描述常驻+全文按需拉取)。
- **记录回路**: `claude plugin details` 能看每个组件的 token 开销(always-on 常驻成本 / on-invoke 触发成本两档估算);'Not used recently' 面板统计'装了但两周内、十次会话都没用过的插件'。但这些都是使用量统计,不是'运行路径回灌本体'意义上的决策记录——没有记录'这次对话具体因为读到哪句 description 才触发了哪个 skill'。`InstructionsLoaded` 这个 hook 事件('CLAUDE.md 或 .claude/rules/*.md 被加载进上下文时触发')架构上是现成的可挂载点,但 Anthropic 自己没有拿它做决策记录用途。
- **本体-接口同步**: 插件更新走 `/plugin marketplace update` + `/plugin update`,由 plugin.json 版本号或 commit SHA 决定要不要拉新;官方市场默认开自动更新,第三方/本地开发市场默认关闭,管理员可用 managed settings 强开。
- **开源情况**: Claude Code CLI 本体闭源(Anthropic 官方产品),但插件/市场生态是开放格式,任何人可建 marketplace 仓库;官方 demo 市场(anthropics/claude-code 下的 plugins 目录)和社区市场均在 GitHub 公开可查。
- **可抄**: 1) SKILL.md 的'Use when...'固定句式描述,是手册'什么时候 when'字段最简洁的执行层落地范式,可直接照抄到本体条目给执行管线/接口层消费。2) plugin details 的 token 开销分层估算(always-on vs on-invoke),提醒手册互引图和管线可视化也该标注'这条本体常驻多贵、被引用时多贵',避免手册膨胀到人和模型都读不动。3) `InstructionsLoaded` 这类'本体被加载进上下文'的生命周期钩子,是做判例反向固化/偏离回灌天然的挂载点。
- **差距**: 全部 when 判断压在模型自己读 description 这一步,没有更高层裁决——描述写得不好导致误触发或漏触发都无记录可查、无法事后追责;没有'决策空间/权威等级'这类结构化元数据,skill 描述本质是训练模型行为的提示词而非给人审阅的决策记录;marketplace 的漂移检测等价于'版本号没 bump=没更新',纯字符串比较,不做内容级/语义级偏离检测。
- **来源**: https://code.claude.com/docs/en/discover-plugins | https://code.claude.com/docs/en/plugins-reference | https://code.claude.com/docs/en/plugin-marketplaces

## block/ai-rules
- **是什么**: Block(Square 母公司)开源的同类单一源多发工具,覆盖11种 agent,特点是提供了显式的 `status` 命令去'追踪已生成规则文件的一致性',以及'standard/symlink'双模式在'细粒度条件规则'与'极简统一文件'之间做取舍。
- **做法**: 源目录 `ai-rules/`(具体子结构未能完整核实,docs/rule-format.md 里区分 standard/symlink 两种源文件格式)。CLI:`ai-rules init`(建示例目录)/`generate`(产出 agent 专属文件)/`status`(据文档描述用于'验证同步状态',但未查实其判定机制是哈希对比、内容 diff 还是别的)/`clean`(清空生成物)/`list-agents`。常用 flag:--agents/--nested-depth/--gitignore。覆盖 AMP、Claude Code、Cline、Codex、Copilot、Cursor、Firebender、Gemini、Goose、Kilocode、Roo 共11个 agent,产物含 CLAUDE.md/AGENTS.md 等。安装方式为 curl 脚本装到 ~/.local/bin/ai-rules。
- **when表达**: Standard Mode 下规则文件 frontmatter 支持 description(帮助理解何时应用的上下文描述)/alwaysApply(bool)/fileMatching(glob,如 "src/**/*.ts"),语义上接近 Cursor 三字段但更简化,没有查到'agent 自主读 description 判断相关性'的确认细节。Symlink Mode 下则完全放弃条件语义:要求唯一一份 AGENTS.md(不能以 --- 开头,即禁止 frontmatter),所有 agent 通过符号链接指向同一文件,所有规则始终全量生效——用条件表达力换极简维护成本的一个明确设计取舍。
- **记录回路**: 文档提及 `status` 命令做一致性检查,但源文档没有说明具体实现(是否哈希比对、是否需要联网、检测粒度到文件还是到条目),这一点未能完整核实,如需采信应直接读源码确认。
- **本体-接口同步**: 同 Ruler 类工具,单向从源生成到各 agent 专有文件;差异在于额外内置了 status 这个'检查是否需要重新 generate'的显式命令,把'漂移检测'当作一等公民命令而不是靠用户自己拼 CI 脚本(对比 Ruler 需要用户自己写 git diff 检测)。
- **开源情况**: GitHub block/ai-rules,来自 Block(Square 母公司)团队,具体开源协议未在本次抓取的文档片段中确认,需查 LICENSE 文件核实。
- **可抄**: 把'漂移检测'做成显式一等公民 CLI 命令(`status`)而不是让用户自己在 CI 里拼 git diff,这个产品化姿态本身值得抄——即便我们目前不清楚它的具体判定算法,'提供一条命令随时能问系统我是否同步'这件事应该是我们执行管线的标配能力,而不是事后才想起来在 CI 里补一段脚本。Symlink Mode 的'放弃条件语义换极简'取舍也提醒我们:手册条目不是所有场景都要上复杂的 when 判断,小范围/低风险的接入面可以直接用'全量始终生效'的简化路径,这对应我们纪律里'小需求迅捷返回'的意图。
- **差距**: 同 Ruler/rulesync 一样没有决策记录/权威分层;status 命令的具体判定机制未公开验证到位,不能确认它是否真的做到了'语义级'一致性检测,很可能仍是文件级比对。
- **来源**: https://github.com/block/ai-rules | https://github.com/block/ai-rules/blob/main/README.md | https://github.com/block/ai-rules/blob/main/docs/agents.md

### 结论
- when 表达力从弱到强的谱系:Ruler/rulesync=无条件全量拼接(0档,不做任何裁剪)→ AGENTS.md=目录就近覆盖(1档,纯静态路径)→ GitHub Copilot=glob路径限定+按消费者身份excludeAgent(2档,静态多维叠加)→ Cursor=alwaysApply/globs/description三态组合,其中description档把判断权交给模型自主读文本决定(3档,唯一有语义判断)→ Claude Code Skills description=全量常驻+模型自主判断(同源于Cursor第3档但没有'先选中再展开'的两段式,token常驻成本更高)。我们手册'软判断全用文字写明'最贴近Cursor Agent Requested/Claude Skills description这一档,可以直接抄它们的frontmatter最小实现,但要在此基础上补上它们都没有的'谁拍板与推翻条件'。
- 全行业没有一家做到语义级漂移检测。查到的最高水位分别是:Ruler社区在CI里跑generate后git diff(纯文件级字符串比对);block/ai-rules有个status命令声称做一致性检查但实现机制未公开核实;Claude Code marketplace的更新判断纯粹是version字符串/commit SHA比较。没有一个工具检测'执行时实际发生的判断,和源头规则写的是否一致'这种语义级偏离——这正是我们'执行偏离手册要记偏离并回灌'回路完全空白、也是我们设计里唯一真正独有的部分,值得优先做原型。
- Ruler'生成物里插注释标来源'(`<!-- Source: <path> -->`)是接口层'只存指针'最低成本的落地范式,可以直接照抄:凡是从手册摘录进hook/skill/CLAUDE.md的内容,都该带一行指向手册条目的注释,不让接口文件变成事实的复述地。
- 权威分层这件事Cursor做得最像我们要的'谁拍板':Team Rules > Project Rules > User Rules三层,组织规则可覆盖个人偏好且不可绕过,这套三层可以直接映射我们'谁拍板与推翻条件'字段的执行侧实现。
- AGENTS.md标准最大的价值不在格式本身(纯自由markdown,零结构化字段),而在证明'中心内容位置的社区共识'能落地成规范并被20+工具原生适配——但即便如此Claude Code仍不原生读它,要靠symlink/@import手动接入,这提醒我们'接口层同步'永远不可能自动100%覆盖,每接一个新执法点都要显式声明怎么把本体接进去,不能假设约定了大家就都认。
- Claude Code plugin details的token开销分层估算(always-on常驻成本 vs on-invoke触发成本)和'Not used recently'巡检面板,提示我们手册互引图/管线可视化也该标注'这条本体常驻多贵、被引用时多贵',并定期巡检'接入了但没人用'的接口投影,而不是任由手册和接入面无限累积。


# 类别:规格驱动开发(Spec-Driven Development)产品的"宪法/steering/规范"治理机制调研 — 对照我们的决策本体三层设计(语义手册/执行管线/接口层)+判例库+三条回路

## GitHub Spec Kit (spec-kit)
- **是什么**: 开源SDD工具包+CLI(specify-cli),给30+编码agent统一注入 /speckit.constitution → specify → plan → tasks → implement 斜杠命令流程,核心是把"项目宪法"落成一份可版本化、可传播的单一markdown文件,并强制走跨文件一致性检查。
- **做法**: 单一权威文件 `.specify/memory/constitution.md`,从`templates/constitution-template.md`实例化(占位符如PRINCIPLE_N_NAME/DESCRIPTION、GOVERNANCE_RULES、CONSTITUTION_VERSION)。`/speckit.constitution`命令按语义化版本(MAJOR/MINOR/PATCH)自动判定版本增量,更新后在文件顶部插入HTML注释的"Sync Impact Report"(版本变更+增删条目+待更新模板清单✅/⚠),并强制走传播检查清单——依次重读plan-template/spec-template/tasks-template/commands/*.md/README,判断是否需联动改。每个feature开`specs/NNN-name/`目录:spec.md、plan.md(含专门的"Constitution Check"门禁段落,Phase0前必须过、Phase1后重过)、research.md、data-model.md、contracts/、quickstart.md、tasks.md(按用户故事分阶段+[P]并行标记)。`/speckit.analyze`是只读跨文件一致性检查器:构建需求清单/任务覆盖映射/宪法规则集三个内部模型,做重复/歧义/欠规格/宪法冲突/覆盖缺口/不一致六类检测,输出CRITICAL/HIGH/MEDIUM/LOW分级报告,宪法MUST冲突恒为CRITICAL,但明确声明"non-negotiable within analysis scope"——若原则本身要改必须走独立的/speckit.constitution更新。`/speckit.converge`对比代码库现状与spec/plan/tasks,把未完成的活追加成新tasks,同样不回写宪法。接口分发:四层优先级栈——project-local overrides(.specify/templates/overrides/)>presets>extensions>core——运行时解析,extension/preset命令文件在**安装时**物化进各agent目录(如.claude/commands/)。extensions.yml定义生命周期钩子(before/after_constitution、before/after_plan、before/after_analyze等),每个钩子有enabled/optional/condition三字段,但condition表达式**未被解析**,规范原文写明"leave condition evaluation to the HookExecutor implementation"——即声明了语法位置但没做求值引擎。
- **when表达**: 宪法原则默认"always in context"(narrative-only,靠agent每个环节自己回头对照);唯一结构化的条件字段(hooks的condition)被显式声明未解析,只有固定的生命周期挂钩名(before_plan/after_plan等),没有真正的求值引擎。
- **记录回路**: 无自动回灌。/speckit.analyze与/speckit.converge都是只读检测器,宪法冲突强制标CRITICAL并阻断,但改不改宪法完全靠人工另起一次/speckit.constitution;版本变更记录只以Sync Impact Report(HTML注释)写在宪法文件顶部+git提交历史,不存在独立于代码仓库的判例/决策日志。
- **本体-接口同步**: 多agent上下文文件(CLAUDE.md/GEMINI.md等)在/speckit.plan的Phase1阶段"运行agent脚本"批量生成/刷新;命令模板通过四层优先级栈在运行时解析,extension/preset包在安装时把命令文件物化进各agent专属目录,卸载后自动回退到下一优先级版本。
- **开源情况**: MIT许可,github/spec-kit,119k+ stars,GitHub官方仓库,活跃维护中
- **可抄**: (1)Sync Impact Report+强制传播检查清单——手册改动后必须过一遍"联动哪些下游模板"的显式checklist,而非靠自觉,可直接照抄;(2)RFC2119强度词(MUST/SHOULD/MAY)给软判断分级,比纯文字更利于agent一致执行;(3)四层优先级栈+运行时解析的模板覆盖机制,可用于"手册本体vs项目本地临时覆盖";(4)"检测器只读、绝不擅自改宪法,原则变更必须走独立命令"的分权原则,与我们"决策空间/谁拍板"思路一致。
- **差距**: 没有真正的"when"触发语义——宪法原则是全量常驻上下文的叙事文本,唯一的条件字段(hooks condition)明确未实现、只是占位。没有运行记录/决策日志:无原子决策jsonl,没有把"哪个环节偏离了宪法、后来怎么改"结构化记录并反哺——analyze/converge都只检测不回灌,处理方式全靠人在对话里现场决定,不沉淀成判例。宪法版本历史只活在git commit里,没有独立于代码库的判例库。也没有规模分档:无论小改动还是大重构都是同一套7个命令,没有"小需求迅捷返回"分支。
- **来源**: https://github.com/github/spec-kit | https://raw.githubusercontent.com/github/spec-kit/main/README.md | https://raw.githubusercontent.com/github/spec-kit/main/templates/constitution-template.md | https://raw.githubusercontent.com/github/spec-kit/main/templates/commands/constitution.md | https://raw.githubusercontent.com/github/spec-kit/main/templates/commands/plan.md | https://raw.githubusercontent.com/github/spec-kit/main/templates/commands/analyze.md | https://raw.githubusercontent.com/github/spec-kit/main/templates/commands/converge.md | https://raw.githubusercontent.com/github/spec-kit/main/templates/tasks-template.md

## Amazon Kiro
- **是什么**: AWS出品的闭源agentic IDE(VS Code fork),把SDD产品化为三个正交系统:specs(requirements/design/tasks三件套)、steering(全局行为约束,含inclusion模式)、hooks(事件驱动自动化),是五个产品里对"when"表达最结构化、最产品化的一个。
- **做法**: specs:每个feature/bugfix经三阶段线性生成requirements.md(EARS记法:"WHEN <condition/event> THE SYSTEM SHALL <behavior>",及IF...THEN变体)→design.md(架构/时序图/数据流)→tasks.md(可勾选清单,agent按依赖图分"波次"wave并发执行,波内并行、波间顺序);创建时可选Requirements-First/Design-First,或"Quick Plan"跳过审批门直接三件套齐出。steering:markdown文件放`.kiro/steering/`(workspace)或`~/.kiro/steering/`(global,workspace优先覆盖global),文件最前必须是YAML frontmatter。三种inclusion模式:`inclusion: always`(默认,每次交互注入)、`inclusion: fileMatch`+`fileMatchPattern`(仅在打开匹配文件时注入,已知bug:global作用域下fileMatch不生效)、`inclusion: manual`(对话里`#文件名`引用或作为slash命令出现)。hooks:JSON文件放`.kiro/hooks/`,字段含name/trigger/matcher(正则)/action(agent prompt或shell command)/timeout/enabled,trigger类型共10种:PromptSubmit、AgentStop、PreToolUse(按工具类别read/write/shell/web/spec/all或来源前缀@mcp/@powers/@builtin+正则过滤)、PostToolUse、FileCreate/Save/Delete(glob)、PreTaskExecution/PostTaskExecution(spec任务前后)、ManualTrigger。
- **when表达**: 两套正交的when:①steering的inclusion模式回答"这条规则什么时候该出现在上下文里"(always常驻/fileMatch按glob匹配/manual按用户手动引用或选slash命令);②hooks的trigger类型回答"什么事件发生时该跑一个自动化动作"(10种IDE/agent生命周期事件+文件glob+正则过滤)。两者都是产品原生结构化字段,不是靠agent读文字判断。
- **记录回路**: 官方文档未见——没有steering/specs的版本历史、变更影响报告或决策日志功能,仅建议团队自己用git管理。
- **本体-接口同步**: 闭在Kiro自家IDE里,不存在"中心本体分发到多个外部agent"的问题——steering文件本身就是agent读取的唯一上下文来源,workspace作用域直接覆盖global作用域即完成同步。对外仅提到兼容社区AGENTS.md标准,但AGENTS.md不支持inclusion模式(只能always-included),导出会丢失when精度。
- **开源情况**: 闭源(AWS商业产品);kirodotdev/Kiro GitHub仓库(约4000 stars,无license字段)只是社区issue追踪+文档镜像,不含IDE核心源码。
- **可抄**: (1)三种inclusion模式(always/fileMatch/manual)是"手册条目按需注入上下文"的现成产品化词汇表,可直接借来描述接口投影该有的三档触发方式;(2)hooks的10种trigger类型(尤其PreToolUse按工具类别+正则、PreTaskExecution/PostTaskExecution)给了"执行管线when触发元数据"一份具体分类学;(3)EARS记法(WHEN...SHALL...)把软判断压成一行可测试模板,可作手册条目"结论"字段的精简写法选项;(4)task按依赖图分"波次"并发调度,是"长管线规模声明"的具体调度算法参照。
- **差距**: 闭源,机制细节官方文档本身也不完整(连开发者社区都在反馈fileMatch不生效等bug)。没有版本/审计机制——官方只给一条最佳实践建议("像对待代码一样对待steering变更,要求review"),没有内建版本号、变更影响分析或回滚。specs和steering是两条平行系统,文档未讲清二者如何互引(比如design.md能否引用某条steering规则)——即"手册互引"在Kiro里缺失。完全锁定在Kiro自家IDE和Claude系模型,没有spec-kit/OpenSpec那种"25+agent通用"的接口分发层。
- **来源**: https://kiro.dev/docs/steering/ | https://kiro.dev/docs/specs/ | https://kiro.dev/docs/specs/feature-specs/ | https://kiro.dev/docs/hooks/ | https://kiro.dev/docs/hooks/types/ | https://github.com/kirodotdev/Kiro/issues/6171 | https://github.com/kirodotdev/Kiro/issues/9176

## OpenSpec
- **是什么**: 开源SDD CLI+slash命令套件,核心创新是把"手册"拆成specs/(当前系统行为真源)和changes/(对specs的提议性增量),用ADDED/MODIFIED/REMOVED三段式delta描述改动,归档时delta自动合并回specs、change文件夹整份平移进archive/留痕。仓库本身完全dogfood(自己管理自己的specs/changes)。
- **做法**: `openspec/specs/<capability>/spec.md`:按能力域拆分,含`## Purpose`+多条`### Requirement:`(RFC2119强度词)+每条需求下若干`#### Scenario:`(GIVEN/WHEN/THEN/AND,这里WHEN表达"系统行为在什么情境下成立"而非流程触发条件)。`openspec/changes/<change-id>/`:proposal.md(Intent/Scope/Approach)、design.md(技术方案,含"### Decision: xxx"小节记架构决策理由)、tasks.md(层级编号checkbox)、specs/<capability>/spec.md(delta,只写ADDED/MODIFIED/REMOVED三段,不重写整份spec)、可选.openspec.yaml元数据。Schema机制:`openspec/schemas/<name>/schema.yaml`用声明式依赖图定义工作流需要哪些artifact(如tasks requires:[specs,design]),依赖是"可能性"而非强制门,可跳过或调换顺序;内置spec-driven schema(proposal→specs→design→tasks→implement),支持schema fork自定义。Archive:`/opsx:archive`把delta按ADDED(追加)/MODIFIED(替换)/REMOVED(删除)合并进主specs/,change文件夹整体移到changes/archive/YYYY-MM-DD-<change-id>/留全量上下文。Stores(beta):把openspec/整个目录单独开一个git仓库,通过git push/pull在多个代码仓库间共享同一份specs/changes,解决跨仓分发。README明确自我对标spec-kit("重、严格phase gate")和Kiro("锁死在他们IDE和Claude模型里")。
- **when表达**: Scenario的GIVEN/WHEN/THEN描述"系统行为在什么情境下成立"(产品行为条件),不是"agent流程该何时触发某条判断"——这是与我们手册when不同的另一个轴,值得在设计里明确区分"行为契约的when"vs"流程触发的when"。
- **记录回路**: 有,但文件夹粒度而非原子记录:archive/YYYY-MM-DD-<change-id>/保留完整proposal+design+tasks+delta spec,是持久化的"为什么改+怎么改"审计轨迹;但没有把执行阶段的"偏离"结构化捕获并聚类回灌进specs的机制——回灌只发生在change被人工批准archive的那一刻,不是持续侦测。
- **本体-接口同步**: `openspec update`重新生成各agent的指令文件(CLAUDE.md、.cursor/rules等,支持25+工具),中心openspec/目录是唯一源;Stores(beta)把整个openspec/目录单独开仓,靠git push/pull把同一份specs/changes分发到多个代码仓库,是五个产品里唯一给出"团队级/跨仓中心源分发"方案的。
- **开源情况**: MIT许可,Fission-AI/OpenSpec,npm包@fission-ai/openspec,活跃(频繁提交/changeset)
- **可抄**: (1)specs(真源)与changes(提议增量)分离、delta用ADDED/MODIFIED/REMOVED三段式而非整份重写,是"决策改手册"最值得抄的具体格式;(2)archive文件夹整份平移+日期前缀,是最简单可靠的判例留痕做法,proposal(为什么)+design(怎么做的决策理由)+tasks(做了什么)三件套天然覆盖决策记录要求的"一句话+锚点+决策空间";(3)Schema依赖图("dependencies are enablers, not gates")而非强制phase gate,是更宽松的规模档位实现思路;(4)"Lite spec(默认)vs Full spec(高风险改动)"分级直接对应"长管线要规模声明、小需求迅捷返回"。
- **差距**: 决策记录粒度是整个change文件夹而非原子的一句话jsonl,快速检索类似判例要翻文件夹而非查一条记录。delta合并规则是纯文本层面的追加/替换/删除,没有版本号或语义化版本(不像spec-kit宪法的MAJOR/MINOR/PATCH),proposal里也没有强制的审批人/权威等级栏位,approve与否靠人在对话里口头确认。scenario里的WHEN是"产品行为的适用条件"而非"agent该在什么情境触发某判断",和我们手册"何时触发"语义不是一个维度,容易被误用混淆。没有偏离检测——不存在"实现跑偏了自动记一笔"的机制,drift发现全靠人工审查(或靠Tessl那种专门的drift-eval)。
- **来源**: https://github.com/Fission-AI/OpenSpec | https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/README.md | https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/concepts.md | https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/writing-specs.md | https://github.com/Fission-AI/OpenSpec/tree/main/openspec/specs | https://github.com/Fission-AI/OpenSpec/tree/main/openspec/changes

## BMAD-Method (BMM)
- **是什么**: 开源"Agile AI驱动开发"框架,不是单一宪法文件,而是按敏捷阶段(1-analysis/2-plan-workflows/3-solutioning/…)组织的12+专家agent(PM/Architect/Dev/UX等)+34+workflow,每个agent/workflow是独立skill包(SKILL.md+customize.toml),用三层TOML覆盖机制做团队/个人定制。是五个产品里唯一采用"多角色agent团队扮演"而非"单一规范文档"路线的。
- **做法**: 每个可定制单元出厂自带customize.toml(标注"DO NOT EDIT — overwritten on every update",是完整可定制字段的schema),典型字段:标量(icon/role/identity/communication_style,override直接覆盖)、persistent_facts(追加型数组,存放组织规则/领域常量/用户偏好,支持`file:{project-root}/...`glob引用把外部文件内容当事实注入)、activation_steps_prepend/append(agent激活时先做/后做的步骤,自然语言描述)、[[agent.menu]](带code主键的表数组,每项是一个skill或一段prompt)。workflow多一个on_complete标量,在固定step(如Step12完成)后触发自定义收尾。三层覆盖:个人.user.toml(gitignore)>团队_bmad/custom/{skill}.toml(提交进版本库)>出厂customize.toml(基线)。合并算法按数据形状而非字段名判定:标量=覆盖胜出;table=递归深合并;带code/id主键的表数组=按主键合并(命中替换、否则追加);其余数组=纯追加(不能删除基线项,想"关掉"某条默认值只能用同code的no-op覆盖)。中心配置_bmad/config.toml/config.user.toml(安装器所有,每次install重建,不可手改)vs _bmad/custom/config.toml/config.user.toml(人写,安装器绝不碰),前者装[core]基础设置/[modules.<code>]各模块安装答案/[agents.<code>]agent花名册。when完全是自然语言:激活步骤/菜单prompt里直接写条件句(如"When the user mentions a competitor or market segment, query corp:competitive_db"),系统不解析条件语法,纯靠agent读prompt自己判断。bmad-help是专门的元agent,随时可问"这一步完了该干嘛"。
- **when表达**: 无结构化when字段,全部写成自然语言条件句,塞进activation_steps或menu prompt里,agent运行时自行判断是否满足、要不要执行——判断权完全下放,系统不做任何条件解析或匹配。
- **记录回路**: 未见——没找到内建的决策/变更日志机制;customize.toml的三层覆盖文件本身可进git,但那是配置变更历史,不是运行决策记录。
- **本体-接口同步**: 三层TOML覆盖(个人.user.toml>团队_bmad/custom/*.toml>出厂customize.toml)+显式的按数据形状合并算法(Python stdlib tomllib解析器),是五个产品里合并规则定义最精确的一个;中心配置_bmad/config.toml由安装器每次install时从各模块module.yaml重新生成,人写覆盖单独放_bmad/custom/,安装器绝不touch。
- **开源情况**: License: Other(需查具体条款,官方称100%免费开源),bmad-code-org/BMAD-METHOD,50k+ stars,npm包bmad-method,V6快速迭代中,核心框架开源、配套模块(BMad Builder/Test Architect/Game Dev Studio等)拆成独立仓库。
- **可抄**: (1)三层覆盖(基线/团队/个人)+按数据形状而非字段名的显式合并算法(标量覆盖/table深合并/带主键数组按键合并/其余数组纯追加),是"手册本体vs项目本地覆盖vs个人临时调整"可直接照抄的合并语义规范,比spec-kit的"四层栈找第一个匹配"更细粒度(能表达只改一条、其余继承);(2)persistent_facts用file:前缀做外部文件引用而非焊进配置文件,是"手册条目引用外部文档而非复制"的干净实现;(3)出厂文件顶部"DO NOT EDIT"+覆盖文件"只写要改的字段、别整份抄"的纪律,直接解决"本体被接口层悄悄改出分叉"的担忧;(4)bmad-help这种"随时问下一步该干嘛"的元agent,弥补纯静态注入"用户不知道该看哪条"的问题。
- **差距**: when完全靠agent读自然语言自己判断,没有Kiro那种结构化trigger分类学,可控性最弱。没有独立的决策日志或变更影响分析机制(相比spec-kit的Sync Impact Report、OpenSpec的archive),customize.toml变更就是普通文件变更,靠team自己在git里管。它解决的问题和我们的决策本体其实不是同一层——BMAD核心是"agent团队分工怎么协作产出PRD/架构/代码",更接近我们"执行管线"层面的角色分工,而非"什么时候该怎么判断"的语义手册,类比时要小心别混层。纯prose-driven when的代价是同一条规则在不同模型/不同时间跑,触发一致性没有保证。
- **来源**: https://github.com/bmad-code-org/BMAD-METHOD | https://raw.githubusercontent.com/bmad-code-org/BMAD-METHOD/main/README.md | https://docs.bmad-method.org/how-to/customize-bmad/ | https://raw.githubusercontent.com/bmad-code-org/BMAD-METHOD/main/src/bmm-skills/2-plan-workflows/bmad-create-prd/customize.toml

## Tessl (Spec-Driven Development tile)
- **是什么**: Tessl公司出品,分Spec Registry(公测,类npm的技能包注册市场)和Tessl Framework(私测)两块。目前公开可读源码的是一个轻量"方法论tile"(tessl install tessl-labs/spec-driven-development),给MCP兼容agent装一套skills+rules+evals,教它先提问、写spec、等批准、再实现、再回写spec。公司更大的野心("spec是唯一要维护的产物,代码是可重新生成的编译产物")目前主要仍是论纲。
- **做法**: .spec.md文件:YAML frontmatter只有name/description/targets(相对路径或glob,必填至少一个,声明这份spec描述哪些源文件)三个字段,正文是需求描述+可选API契约代码块,关键是行内`[@test] ../tests/xxx/test_yyy.py`链接——把每条需求/每段错误处理规则和验证它的具体测试文件逐条绑定,而非笼统提一句"有测试覆盖"。rules/:markdown规则文件,frontmatter只有一个布尔字段alwaysApply:true/false(比Kiro的三态inclusion更粗),正文是编号步骤的自然语言流程(如spec-before-code.md:收到新任务→查specs/目录是否有相关spec→有则先核实是否仍准确→没有则先收集需求写spec→必须拿到"stakeholder explicit approval"才能动代码;并列出什么算approval/什么不算——"沉默不算""你自己觉得没问题不算"——以及"trivial change"和"emergency hotfix(但要事后补spec)"两条例外)。skills/(requirement-gathering/spec-writer/spec-verification/work-review四个)是标准Agent Skill,分别对应一次只问一个问题的访谈、从已澄清需求写/改.spec.md、验证实现和测试是否还跟spec同步、完工后对照approved spec做review并把执行中新发现的需求回写进spec。evals/是最独特部分:9个黄金场景测试用例(每个含task.md场景+criteria.json判定标准),专测这套方法论prompt本身是否work,覆盖spec-drift-after-refactor(代码重构改了文件名/函数位置但没同步spec,能否审计出所有drift并生成修正spec)、one-question-enforcement、trivial-change-exception、skip-spec-pushback等场景;CI里`tessl skill review`跑技能审查,`make eval`本地跑评测,是唯一把"手册/规则本身要不要被信任"做成可回归测试的产品。分发机制:tessl install把tile内容整份复制进项目.tessl/目录,任何MCP兼容agent统一从这里读,赌MCP会成为通用上下文协议。
- **when表达**: 规则frontmatter只有alwaysApply:true/false一个二值字段控制是否常驻,没有条件匹配语法;真正的条件判断(比如"什么算approval""什么算trivial change可以跳过流程")都写成规则正文里的自然语言枚举清单,靠agent自己对照判断。
- **记录回路**: 有,但即时/单点式:work-review skill在完工review阶段把执行中发现的新需求直接写回spec,没有经过候选规则池/聚类/人工裁决的中间工序;但有一套独立的evals/黄金场景回归测试(9个场景,含drift检测、越权跳过检测等),用来验证方法论规则本身在agent身上执行是否可靠——五个产品里唯一对"规则本身"做自动化回归测试的。
- **本体-接口同步**: 单一集中目录.tessl/,赌注是所有MCP兼容agent都从同一份原始文件直接读取,不需要像spec-kit/OpenSpec那样为每个agent工具生成/转换专属适配文件——牺牲了"不支持MCP的agent怎么办"的兼容性,换取同步机制的极简。
- **开源情况**: MIT许可,tesslio/spec-driven-development-tile(tile本体开源);但Tessl Framework/Registry核心产品(私测阶段)机制未完全公开,第三方评测(codemyspec.com)称其"目前主要还是一个论纲"。
- **可抄**: (1)`[@test]`行内测试链接把"判断怎么下"和"拿什么验证它成立"焊在一起,可直接用于手册条目挂可运行验证点;(2)targets字段(spec描述哪些文件/glob)是"接口投影"最小可行实现——一条手册条目该管哪些文件,写一行glob就够;(3)evals/黄金场景——给"手册规则本身"配一套回归测试(不是测代码,是测agent照规则走会不会翻车),是我们目前完全没有的能力,可用来验证手册条目改版后agent判断是否跑偏,比单纯"回灌修订"更进一步(先测试过再回灌);(4)rule里明确列"什么算approval/什么不算"是"谁拍板"字段一份很具体的写法模板。
- **差距**: alwaysApply只有二值(true/false),没有Kiro那种细粒度的fileMatch/manual,when表达力最弱、全靠规则正文自然语言步骤兜底。没有版本号/语义化版本机制,spec变更history全靠git。没有独立于代码库的决策日志——work-review skill会把执行中发现的新需求写回spec,这是一种回灌,但回灌目标直接是spec本身,没有经过"新判例聚类→候选规则→人裁→再进手册"这道中间工序,即回灌是即时/单点的,不会攒起来做聚类分析。公司更大的"代码即可重新生成的编译产物"路线目前基本还是论纲,不能高估成熟度。
- **来源**: https://github.com/tesslio/spec-driven-development-tile | https://raw.githubusercontent.com/tesslio/spec-driven-development-tile/main/README.md | https://raw.githubusercontent.com/tesslio/spec-driven-development-tile/main/docs/spec-format.md | https://raw.githubusercontent.com/tesslio/spec-driven-development-tile/main/rules/spec-before-code.md | https://raw.githubusercontent.com/tesslio/spec-driven-development-tile/main/evals/spec-drift-after-refactor/task.md | https://docs.tessl.io/use/spec-driven-development-with-tessl | https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html | https://codemyspec.com/blog/tessl-review

### 结论
- "when"在这些产品里其实是两个不同的轴,要拆开写:①规则什么时候该出现在agent上下文里(Kiro的always/fileMatch/manual、Tessl的alwaysApply)——这是接口投影层的问题;②系统行为在什么情境下成立(OpenSpec的GIVEN/WHEN/THEN)——这是产品行为契约,不是流程触发条件。我们手册的when字段容易把两者混写,应分成两个独立字段。
- 没有一个产品做到我们设想的完整闭环'执行偏离→记录→聚类→候选→人裁→回灌手册':spec-kit的analyze/converge只检测不回灌;OpenSpec的回灌只发生在人工批准archive那一刻且是change文件夹粒度;Tessl的work-review回灌是即时写回、没有聚类候选这道工序。这条完整回路是我们设计里比调研到的所有产品都更完整的地方,但也说明没有现成实现可抄,'聚类→候选→人裁'这一段得自己扎实做。
- 版本化和影响分析上,spec-kit宪法的MAJOR/MINOR/PATCH+Sync Impact Report(自动判定bump类型+列出待更新的下游文件清单并强制走一遍)最值得直接抄——把'改手册要联动哪些下游'做成强制检查清单而非靠自觉,这条能直接落地到我们的手册维护流程。
- 覆盖/合并算法上,BMAD的'按数据形状分四类合并规则(标量覆盖/table深合并/带主键数组按键合并/纯数组追加)'+三层作用域(个人/团队/基线)是最精确可执行的规范,比spec-kit的'优先级栈找第一个匹配'更细粒度(能表达'只改一个字段、其余继承基线'),适合直接借给'手册本体vs项目本地覆盖'的合并语义设计。
- Tessl的evals/黄金场景(给规则本身配回归测试,而非测代码)填了一个我们目前完全没想到的空白——可以在手册条目定稿后配几个黄金场景验证agent的判断力,尤其是容易被误判的边界条件(如'什么算approval''什么算trivial不用走全套流程'),这是'先测试过再回灌'的具体做法。
- OpenSpec的specs(真源)/changes(提议增量,ADDED/MODIFIED/REMOVED三段式delta)分离,以及Kiro的hooks trigger分类学(10种事件类型+工具类别/正则/glob过滤)+任务依赖图分波次并发调度,分别是'判例怎么落进手册的格式'和'执行管线when触发元数据该有哪些字段'两个具体问题最值得参照的产品化答案。


# 类别:策略即代码与决策引擎(中心策略库 + 分布执法点 + 决策日志) —— 对照我们的“语义本体+执行管线+接口层”三层设计与统一决策库

## Open Policy Agent (OPA / Rego)
- **是什么**: CNCF 毕业的通用策略引擎:用 Rego 语言把策略从服务里解耦出来,服务通过查询把授权/校验问题甩给 OPA,OPA 可以以 sidecar、宿主守护进程或库三种形态部署。是本次调研里“中心本体+分布执法点+决策留痕”做得最完整的成熟实现。
- **做法**: 策略打包成 bundle(gzip tarball):.rego 策略文件 + data.json/yaml 数据 + 可选 policy.wasm;.manifest 声明 revision(常用 git commit hash)、roots(声明这个 bundle 拥有哪些 data 命名空间路径,不写默认整份 data 归它管)、rego_version、wasm、metadata;目录层级即数据路径(如 roles/bindings/data.json 载入 data.roles.bindings);可选 .signatures.json 用 JWT 对每个文件哈希签名做完整性校验;delta bundle 只含 patch.json(JSON Patch 的 remove/replace/upsert)只传数据增量不传策略增量。
- **when表达**: 语言层没有独立‘when’关键字,规则体是一组表达式的隐式 AND,Rego v1 引入显式 if 关键字(如 `deny if input.token != "secret"`)让触发条件更好读;多次同名定义构成逻辑 OR(incremental rules);default 关键字给兜底值。触发条件与执行逻辑写在同一段代码里,但 METADATA 注解块(# METADATA YAML 注释)可挂 title/description/custom/scope/entrypoint 等与执行逻辑分离的语义元数据,custom 字段可自由塞用户自定义键值(可用来放‘规模档位/须确认标志’),scope 支持 rule/document/package/subpackages 分层继承,且 rego.metadata.rule() 能在运行时读回自己的注解,做到‘自描述策略’。
- **记录回路**: 决策日志是一等公民:每条 decision log 含 decision_id / trace_id / span_id / bundles(含 revision)/ path / query / input / result / requested_by / timestamp / metrics / erased / masked / nd_builtin_cache 等字段,可上报到远端 HTTP 服务、console 或自定义插件;data.system.log.mask 路径写 Rego 规则做敏感字段 remove/upsert 脱敏,drop_decision 规则可整条丢弃,max_decisions_per_second 限流。但官方生态没有‘决策日志自动聚类回灌改策略’的闭环——这一步完全留给使用者自建。
- **本体-接口同步**: OPA 实例(可成百上千个 sidecar)周期性轮询中心 bundle 服务:短轮询用 If-None-Match ETag 避免重传,长轮询让服务端 hold 请求直到有更新;revision 字段让服务端判定要不要发 delta bundle。即“中心本体”是打包好的 bundle 服务,“接入面”是遍布各处的 OPA 实例,同步靠标准 pull + ETag,而非 push。
- **开源情况**: Apache-2.0,CNCF 毕业项目,github.com/open-policy-agent/opa,约 12k star,Go 编写,2026-07 仍在发版(v1.18.2),活跃。
- **可抄**: (1) bundle .manifest 的 roots 字段——命名空间声明‘这个包只能管这些路径’,可对应我们给每条执行管线声明它能触达的执行域,避免多来源写冲突;(2) decision log 字段清单本身就是一份现成的‘运行记录该记什么’范本(decision_id/input/result/bundle revision/耗时指标全都有),可直接作为我们判例记录字段基线;(3) METADATA 的 scope 分层挂载,对应手册条目要不要按目录继承语义元数据;(4) 用同一门执行语言(Rego)写日志脱敏规则而不是叠加新 DSL,值得学——记录管线复用执行语言而非另起一套。
- **差距**: 决策日志只解决‘记录’,不解决‘回灌’——没有官方机制把决策日志聚类反哺回策略草案,永远是人工或自建管线;METADATA 的 custom 字段纯自由格式,没有‘when 触发条件’这类结构化标准 schema,团队得自定约定;bundle 只有 revision 和 roots,没有‘谁拍板/推翻条件’这类治理字段,版本控制寄希望于外部 CI/Git。
- **来源**: https://www.openpolicyagent.org/docs/latest/management-decision-logs/ | https://www.openpolicyagent.org/docs/latest/management-bundles/ | https://www.openpolicyagent.org/docs/latest/policy-testing/ | https://www.openpolicyagent.org/docs/latest/philosophy/ | https://www.openpolicyagent.org/docs/latest/policy-language/ | https://www.openpolicyagent.org/docs/v0.51.0/annotations/ | https://github.com/open-policy-agent/opa

## AWS Cedar(语言/引擎)+ Amazon Verified Permissions(托管服务)
- **是什么**: Cedar 是可形式化验证的授权策略语言与求值引擎(Rust 实现,以 crate/SDK 形式嵌入,强调求值无副作用、结果与策略顺序无关,便于 SMT 自动证明);Verified Permissions 是 AWS 围绕 Cedar 提供的托管策略仓库(policy store)+ 决策 API 服务。
- **做法**: 策略由 permit/forbid 效果 + principal/action/resource 三元作用域 + 可选 when{...}/unless{...} 条件子句组成;forbid 对 permit 一票否决(任一 forbid 命中即整体 Deny);Verified Permissions 里 policy store 通常一一映射一个应用,可定义 schema 校验实体类型/属性形状。Cedar 本身不规定策略如何分发到执法点,这层留给托管服务或使用者自建。
- **when表达**: 语法层有真正的 when{...}/unless{...} 子句,直接对应“触发条件”,布尔逻辑靠 && 组合,可读性优于 Rego 的隐式 AND。配套的 cedar-policy-symcc 用 SMT 做策略等价性/影响范围的形式化验证并能给反例——是几家里唯一把“新策略会不会意外扩大/缩小权限”当成可自动证明问题来做的。
- **记录回路**: Cedar 引擎本身是纯求值库,不自带审计日志。Verified Permissions 把管理事件(CreatePolicy/DeletePolicy 等)默认记入 CloudTrail,但真正有价值的授权决策事件(IsAuthorized/BatchIsAuthorized)默认不记,需额外配置自定义 trail 并付费,决策字段还被塞进 additionalEventData 而非标准 responseElements,结构化程度明显弱于 OPA 的一等公民决策日志。
- **本体-接口同步**: Cedar 本身没有 bundle/轮询式中心-边缘同步协议;Verified Permissions 把“策略仓库”和“决策 API”合一,应用直接在线调 IsAuthorized,走的是“中心化在线裁决”而非 OPA 式“边缘缓存本体、本地裁决”——与我们希望执行管线本地可跑的诉求路数不同。
- **开源情况**: Cedar 语言/引擎 Apache-2.0 开源,github.com/cedar-policy/cedar,约 1.6k star,Rust 99.8%;Amazon Verified Permissions(托管决策服务+审计)纯闭源 AWS 服务。
- **可抄**: when/unless 子句把“条件”做成独立可读的一段,和我们手册“判断怎么下”字段近乎同构;forbid 优先于 permit 的一票否决语义,对应我们“谁拍板与推翻条件”里“更高权威可一票拒绝”的诉求;cedar-analysis 的形式化验证思路(“新策略只会更严不会更松”可证明)值得作为长期方向。
- **差距**: 决策留痕默认关闭、要额外配置、格式原始——策略引擎把决策日志当一等公民 vs 当选配审计功能,Cedar/Verified Permissions 属于后者;且它天然要求执法点联网调中心 API,没有 bundle 式“先分发到边缘再本地裁决”的路径,和我们“接口层只存指针、执行管线本地能跑”的诉求不完全兼容。
- **来源**: https://docs.cedarpolicy.com/policies/syntax-policy.html | https://github.com/cedar-policy/cedar | https://docs.aws.amazon.com/verifiedpermissions/latest/userguide/monitoring-cloudtrail.html

## Oso(Polar 语言,已弃维护并转向闭源云)
- **是什么**: 曾经的开源嵌入式授权库(Polar 语言,声明式,支持 RBAC/ReBAC/ABAC 与多语言绑定),定位与 OPA/Cedar 类似——策略嵌入应用进程内直接求值。列入本次调研主要作为商业化风险的反例。
- **做法**: Polar 语言写规则,库直接嵌入应用进程内求值,没有独立的 bundle 分发或决策日志基础设施,策略文件假设与应用代码同仓库同发布节奏。
- **when表达**: 无(未深入语言细节调研,因项目已停止作为可参照的活样本)。
- **记录回路**: 无——开源库阶段不提供决策留痕机制,一切靠使用者自己在应用层加日志。
- **本体-接口同步**: 无独立同步机制,谈不上“中心本体+多接入面同步”。
- **开源情况**: GitHub osohq/oso 已在 README 明确标注 Deprecated(原话:“We have deprecated the legacy Oso open source library”),官方战略转向闭源托管的 Oso Cloud,不再提供可自部署的开源引擎主线。
- **可抄**: 架构上没有新东西可抄;真正的借鉴价值是商业教训——一个原本开源的策略引擎项目在打磨‘决策留痕/审计’这类企业级需求时,选择收编成中心化闭源云服务而非把能力开源沉淀。
- **差距**: 没有解决我们关心的任何问题(when 表达、决策留痕、回灌均缺失),且已淡出开源赛道,列出主要是提醒:这类能力如果一直没人做深,产品最终会被架构成‘必须联网问总部’的形态。
- **来源**: https://github.com/osohq/oso | https://www.osohq.com/docs/reference/polar/introduction

## GoRules / zen-engine(JSON Decision Model, JDM)
- **是什么**: 开源可嵌入业务规则引擎(Rust 核心 + Node/Python/Go/Java/C#/Kotlin/Swift 多语言绑定),用 JDM(JSON Decision Model)描述“决策图”,节点类型含决策表/开关/函数(内嵌 QuickJS 跑 JS)/表达式/嵌套决策节点;配套闭源云平台(GoRules BRMS)提供可视化编辑、模拟器、审批发布流程。
- **做法**: JDM 是一份 JSON:顶层 nodes(每个含 id/type/name/position/content)+ edges(sourceId/targetId/sourceHandle/targetHandle)+ metadata,组成从 Input 节点流向 Output 节点的有向图;决策表节点的 content 内是 schema(inputs/outputs 列)+ rules(行),逐行从左到右求值,每列隐式 AND,单元格支持 Unary(如 `>1000`)或 Expression(ZEN 表达式语言)两种写法,空单元格恒真;hitPolicy 有 first(取首个命中行,输出单对象或 undefined)和 collect(取全部命中行,输出数组,可选聚合)。
- **when表达**: ‘when’被具体到表格的‘输入列’这一结构化位置——每一行就是一条业务人员能直接读的规则,而非嵌在代码里的条件表达式,是几家里对非工程师最友好的 when 表达方式。
- **记录回路**: 开源 zen-engine 核心是纯求值库(evaluate() 直接返回结果),README 未见内建决策留痕字段;在线编辑器带 Simulator 用于调试/试跑;闭源云平台文档提到“发布新版本走审批工作流,引擎自动拉取新版本无需重新部署应用”,决策留痕/审计能力更多落在闭源云平台一侧,开源引擎本身弱。
- **本体-接口同步**: 云平台侧强调更新后引擎自动拉取(类似 OPA 的 bundle 热更新思路),但开源 zen-engine 把“从哪里取 JDM”完全交给调用方(文件系统/数据库/REST API 均可),没有像 OPA 那样内建标准轮询协议。
- **开源情况**: zen-engine 核心 MIT 协议,github.com/gorules/zen,约 1.8k star,195 forks,120+ releases(最新 v0.54.0),Rust 为主,活跃;配套自托管/云端 BRMS 平台为商业产品,许可条款本次未确认为开源。
- **可抄**: 决策表的“电子表格式 when”可读性范式——若我们手册里有些判断天然是“多条件组合查表”型(如按规模档位+是否涉密+是否长管线三维决定要不要强制确认),这种结构化表格比自然语言段落更适合做执行投影的落地格式;“decision 节点嵌套调用别的决策图”对应我们“手册条目互相引用”的需求,只是它发生在执行层而非语义层。
- **差距**: 决策留痕和回灌基本没有开源实现,模拟器只是调试工具不是生产决策日志;数据模型纯粹是“图+表”,完全没有语义手册那层——没有地方写“这条规则为什么存在/谁能推翻它”,决策表一多就会塌缩成一堆没有背景说明的电子表格。
- **来源**: https://github.com/gorules/zen | https://docs.gorules.io/reference/json-decision-model-jdm | https://docs.gorules.io/docs/intro

## Camunda DMN(Decision Model and Notation)
- **是什么**: OMG 标准 DMN 的开源/源码可见实现,决策表是最常用子集,可独立使用或作为 Camunda 8(Zeebe)流程编排引擎里的一个任务节点。
- **做法**: 决策表用 DMN 标准 XML 描述,列出 input/output 表达式(用 FEEL 表达式语言写),行是规则;hitPolicy 属性五分类:UNIQUE(默认,至多一行命中,多行命中视为建模错误)、ANY(允许多行命中但要求输出一致)、FIRST(取第一条命中)、RULE ORDER(取全部命中按行序输出)、COLLECT(取全部命中,可选 SUM/MIN/MAX/COUNT 聚合)。
- **when表达**: when 落在表格“输入列表达式”里,用 FEEL(Friendly Enough Expression Language,专为业务人员设计)写;hitPolicy 本身是对“多条 when 同时命中时怎么裁决”的显式声明,比多数系统更完整,可直接借用其词汇表处理判例冲突消解。
- **记录回路**: DMN 标准本身不规定决策留痕;Camunda 8 平台侧(Operate/Tasklist)提供流程实例执行历史查询,决策评估作为流程一环被记入流程实例历史,是“流程审计”的副产品而非引擎原生的决策日志设计。
- **本体-接口同步**: DMN 表通常和 BPMN 流程模型一起部署到 Zeebe,走标准“部署资源”API,版本以部署 ID/版本号管理,与流程引擎耦合,不是 OPA 式独立 bundle 轮询协议。
- **开源情况**: DMN 标准本身开放(OMG 规范);Camunda 7 系代码(如已归档的 camunda-engine-dmn)Apache-2.0;但 Camunda 8 自 v8.6(2024-10)起,Zeebe/Operate/Tasklist/Identity/Optimize 等核心组件源码公开但改用 Camunda License v1(生产使用需付费授权,仅开发测试免费),已不是纯 Apache-2.0 意义上的开源。
- **可抄**: hitPolicy 五分类词汇(唯一/任意/优先级/首个/规则序/收集+聚合)是“多条判断同时命中怎么办”这一问题少见的成熟标准化答案,可直接抄进我们“判例冲突消解”规则的措辞;FEEL 是“给业务人员看的条件语言”的好范本。
- **差距**: 和 GoRules 一样只解决执行层的结构化判断,不覆盖语义手册那层“为什么/谁拍板”;且许可证转向后,治理能力(尤其审计)越来越绑定付费平台侧而非开放标准侧,拿它当长期参照要留意它已非纯开源项目。
- **来源**: https://docs.camunda.io/docs/8.7/components/modeler/dmn/decision-table-hit-policy/ | https://camunda.com/blog/2024/04/licensing-update-camunda-8-self-managed/ | https://github.com/camunda/camunda-engine-dmn

## Drools / Apache KIE(incubating)
- **是什么**: Java 生态最老牌的产生式规则引擎(Rete 算法族),同时内置一个宣称 Conformance Level 3(100% 覆盖标准)的 DMN 引擎。2023 年进入 Apache Incubator 改名 Apache KIE,是原 Red Hat Decision Manager 的上游开源项目。
- **做法**: 规则用 DRL(Drools Rule Language)写,语法直接是 `rule "name" [attributes] when <LHS 条件模式> then <RHS 动作> end`——LHS 对工作内存(working memory)里的事实(facts)做模式匹配,RHS 是命中后执行的 Java/MVEL 动作;规则文件(.drl)按业务域组织,可打成 KJAR(Knowledge JAR)部署到 KIE Server。
- **when表达**: 本次调研里唯一字面上就叫“when”关键字的系统——DRL 规则天生分 when(条件)/then(动作)两段;还支持 salience(优先级数值)、agenda-group(分组控制触发批次)、no-loop(防止规则触发自己)、enabled(布尔开关)、timer(interval/cron 定时触发)等规则级属性,本质就是‘规模档位/须确认标志/触发时机’这类元数据,只是它们是执行引擎的调度旋钮而非语义手册的记录字段。
- **记录回路**: 核心是 Rete 算法的工作内存事件模型,可挂 WorkingMemoryEventListener/AgendaEventListener 监听规则触发/事实变更,理论上能拼出决策审计,但需要开发者自己接监听器,不是引擎原生吐结构化决策日志;KIE Server/Business Central 侧有执行历史查询,偏运维监控而非“策略回灌”闭环。
- **本体-接口同步**: 规则以 KJAR 打包,经 KIE Server 部署/版本管理,支持热更新(换 KieContainer 版本无需重启应用),思路上与 OPA 的 bundle 热加载类似,但更偏 Java 企业内部部署,没有 OPA 那样标准化的轮询协议描述。
- **开源情况**: Apache-2.0,github.com/apache/incubator-kie-drools,约 6.3k star,2.6k forks,Java 为主,17,721 commits,近期仍有 release/PR,活跃;2023 年起进入 Apache 孵化器,治理上比 Camunda 8 更纯粹地留在开源基金会模式下。
- **可抄**: DRL 的 when/then 命名结构,几乎是我们手册“结论/判断怎么下”两段式的执行层直译,可作为管线注册表“条件->动作”最小声明单元的命名参照;salience/agenda-group 这类“规则调度元数据与规则体分离”的做法,呼应我们“接口层只放指针和最小提醒、本体不进接口文件”的分层原则——把“这条规则什么时候该被考虑”和“具体判断逻辑”分成两种不同性质的声明。
- **差距**: 决策留痕依然要靠使用者自己接事件监听器,官方没有把“决策日志”打磨成独立、结构化、可直接消费的产品特性;规则库越大越依赖 Rete 网络的隐式匹配顺序,“为什么触发了这条规则”对非工程师可解释性不友好,和我们希望手册对人类可读、可追溯的诉求有距离。
- **来源**: https://docs.drools.org/latest/drools-docs/drools/language-reference/index.html | https://github.com/apache/incubator-kie-drools | https://kie.apache.org/

### 结论
- 决策日志字段设计的黄金标准是 OPA:decision_id/input/result/bundle revision/耗时/脱敏标记这套字段清单可直接抄作为我们判例记录的字段基线;其余系统(Cedar/GoRules/DMN/Drools)在这一环普遍偏弱、默认关闭或要使用者自建,说明“决策留痕即一等公民而非事后拼凑”是我们设计里最值得咬住的差异化优势。
- when 的表达谱系从隐式(Rego 规则体的 AND)、到显式关键字(Cedar 的 when/unless、Drools 的 when/then)、到结构化表格(GoRules/DMN 决策表 + hitPolicy),没有一家系统同时把 when 既写给机器执行、又写给人看懂治理理由——我们“手册软判断用文字写明 + 管线硬判断用声明式配置”的两层拆分,恰好补上业界目前缺的中间层。
- 中心本体与接入面同步的成熟形态是 OPA 的 bundle + ETag 轮询 + roots 命名空间声明(拉模式、增量 delta、revision 对账),可直接作为我们管线注册表分发协议的设计蓝本;Cedar/Verified Permissions 选择相反的“中心化在线裁决”模式,提醒我们若要执行管线本地可跑就该学 OPA 的 pull 式而非强依赖联网 API。
- ‘决策日志回灌改规则’这条回路,在全部六个查到的产品里都是空白——没有一个把这一步做成原生特性,清一色留给使用者自建,说明我们“反向固化器聚类判例->人裁决->进手册”的回路是业界尚未标准化、值得投入做深的差异化环节,不是重新发明轮子。
- OPA 的 METADATA 注解(title/description/custom/scope + 运行时可读回)提供了“给可执行规则挂语义元数据但不污染执行逻辑”的现成范式,以及 Drools 的 salience/agenda-group“调度元数据与规则体分离”的做法,两者都可直接借鉴到我们的执行管线声明式注册表里补 when 触发元数据/规模档位/须确认标志字段。
- Oso(弃开源转闭源云)和 Camunda 8(源码可见但生产收费)的商业化转向提示:决策留痕/审计这类治理增值能力一旦做深,极易变成商业化收编点;我们把它做成内部自管设施而非依赖外部 SaaS,这个方向本身是对的。


# 类别:大模型护栏与行为规则框架(LLM Guardrails / Agent Safety Policy Engines)

## NVIDIA NeMo Guardrails
- **是什么**: 开源可编程护栏工具包,给基于LLM的对话/agent系统加运行时安全层。核心抽象是"rail":input/dialog/output/execution/retrieval五类,分别挂在请求处理管线的不同阶段(调用LLM前/多轮对话控制/生成响应后/工具调用前后/RAG检索后)做拦截、改写或放行。
- **做法**: DSL叫Colang(1.0类似`define user/define bot/define flow`的伪Python;2.0改为`flow`+`when/or when`事件匹配语句做分支)。标准项目目录是 config/{config.yml, *.co, actions.py}:config.yml的`rails:`字段下分input/output/dialog/retrieval/execution五个键,各自列出要激活的flow名;.co文件写对话流和拦截逻辑;actions.py放自定义Python动作(如self_check_input调另一个LLM做语义判断,这是软判断入口)。运行时generate()支持generation_options.log四件套——activated_rails/llm_calls/internal_events/colang_history,把这次调用触发了哪些rail/flow、每次子LLM调用的prompt/completion都吐出来;另有独立的Tracer/GenerationLog→InteractionLog体系,把执行数据解析成标准span(LLM调用span/Rail执行span/Action分发span),可导出OpenTelemetry或写本地jsonl。
- **when表达**: when写在Colang流程代码里而非独立声明式字段,例如`when user expressed feeling unwell -> pass`。硬判断(正则/参数校验/execution rail拦工具调用参数)和软判断(self_check_*调用另一个LLM语义打分)统一用同一套action语法混写在flow里,不分文件分层。
- **记录回路**: 有——单次调用的执行轨迹很扎实(四件套log + span化tracing),但没有"从大量运行记录反向聚类出新rail候选"的自动回灌,规则演化仍是人工看日志改.co文件。
- **本体-接口同步**: 无——一份config目录(yml+co+py)就是全部真源,应用直接加载它跑,不存在中心本体与多接入面分发同步的问题(它本身就是单点嵌入库)。
- **开源情况**: Apache-2.0,GitHub NVIDIA-NeMo/Guardrails(原NVIDIA/NeMo-Guardrails迁移改名),活跃,仍在推进Colang 2.0;另有闭源的NeMo Microservices商业化版本。
- **可抄**: (1) 按pipeline阶段分五类rail(input/dialog/output/execution/retrieval)是很干净的"何时执行"骨架,可对照我们管线要不要按读取前/决策中/输出前/工具调用前细分触发点;(2) generation_options.log四件套是"一次运行触发了哪些规则"的具体可抄字段设计,直接对应我们要的"本次引用了哪条本体"可视化;(3) 硬判断与软判断(self_check_*)统一用同一个action抽象混写,而不是强拆两套系统,值得参考其工程取舍。
- **差距**: when写死在流程脚本里,没有"判断怎么下/谁拍板/推翻条件"这类独立元数据,更谈不上判例引用;没有反向固化器把新判例聚类成规则候选;Colang flow既是语义描述又是可执行代码,软判断的自然语言意图和硬执行细节耦合在同一文件里,无法单独审阅语义层。
- **来源**: https://github.com/NVIDIA-NeMo/Guardrails | https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/rail-types | https://deepwiki.com/NVIDIA/NeMo-Guardrails/12-observability-and-tracing

## Guardrails AI
- **是什么**: Python框架,给LLM的输入/输出加"Guard"——由多个validator组成的校验管道,校验失败可reask(让LLM重答)、fix(自动修正)、filter/refrain/exception等方式处置。定位是"结构化输出+风险校验"而非对话流控制。
- **做法**: 两种定义方式:早期RAIL(Reliable AI Markup Language,一种XML,如`<rail version="0.1"><output><string name=".." validators="guardrails/uppercase; guardrails/two_words"/></output></rail>`);新版更常用Pydantic model + `Guard().use(Validator1, Validator2, ...)`直接组合validator对象。每个validator是一个类,声明检查逻辑和on_fail策略(reask/fix/filter/refrain/noop/exception)。Guard.__call__每次调用生成一个Call对象压入Guard.history栈,Call再分解成Iteration栈(每次validator-LLM交互),可回放raw output、validated output、每个validator的pass/fail与耗时。Guardrails Hub是独立validator市场,约70个validator按risk category(PII/毒性/越狱/事实性/格式/品牌风险等)和use case(聊天/RAG/摘要/代码生成)分类,并标注infrastructure requirement(Rule/ML/LLM三选一),用`guardrails hub install hub://guardrails/toxic_language`装。
- **when表达**: 没有声明式when条件语言——触发时机就是Guard被代码调用的那一刻,由宿主程序调用点决定。Hub给每个validator打的infrastructure requirement标签(Rule/ML/LLM)是它对"这是硬判断还是软判断"最直接的字段化表达。
- **记录回路**: 有明确记录结构——Guard.history的Call→Iteration两级栈,可回放每次校验的输入输出与通过与否。但没有从history自动生成新validator或调阈值的机制,回灌靠人工看日志改代码。
- **本体-接口同步**: 无——Guard对象直接嵌入调用方代码,一个应用一份配置;Hub分发的是validator代码包版本(走pip/hub install升级),不是规则文本同步。
- **开源情况**: Apache-2.0,GitHub guardrails-ai/guardrails,活跃;Hub上部分validator是独立小repo(如guardrails-ai/guardrails_pii),某些依赖商业API(Presidio/OpenAI等)。
- **可抄**: (1) Call→Iteration两级记录结构是"一次决策→多次子检查"的具体可抄模型,可对照我们原子决策记录+锚点设计;(2) infrastructure requirement(Rule/ML/LLM)标签直接可抄进我们管线注册表,标注每条判断是硬还是软;(3) on_fail的动作枚举(reask/fix/filter/refrain/noop/exception)是"判断失败后怎么处置"的成熟词表,可参照定义我们"偏离手册后怎么处理"的动作集。
- **差距**: 完全没有语义本体层——一个validator就是可执行代码+一句英文description,没有互相引用的手册、没有判例、没有谁拍板的记录;when的触发条件靠宿主代码调用位置决定,框架不提供规模声明/须确认标志这类分级;Hub是"验证器插件市场"而非"决策规则库"。
- **来源**: https://guardrailsai.com/docs/concepts/validators | https://guardrailsai.com/hub | https://github.com/guardrails-ai/guardrails/blob/main/docs/how_to_guides/rail.md | https://www.guardrailsai.com/docs/concepts/logs

## Meta Llama Guard (Llama Guard 3/4)
- **是什么**: 一个专门做安全审查的LLM分类模型(不是规则引擎),输入一条user/agent消息,输出safe/unsafe加违反的类别号,用来做agent的input/output moderation。
- **做法**: taxonomy是写死在prompt模板里的类别列表:S1-S13基于MLCommons标准hazard taxonomy(暴力犯罪/非暴力犯罪/性犯罪/儿童性剥削/诽谤/专家建议滥用/隐私/知识产权/大规模杀伤性武器/仇恨言论/自杀自伤/性内容/选举虚假信息),Llama Guard 3额外加S14代码解释器滥用(针对工具调用场景)。prompt模板结构固定:task instruction + {{unsafe_categories}}占位符(塞入S1-S14描述文本) + {{conversation}}占位符 + "只判最后一条消息,第一行输出safe/unsafe,不安全则第二行列违反类别号"。自定义taxonomy的机制就是替换{{unsafe_categories}}这段文本本身,是prompt级别的in-context覆盖,不是配置字段。
- **when表达**: taxonomy定义的是"什么算违规"(what),不是"何时触发"——每次调用对当前这一条消息全量跑一遍全部类别,没有when分支;触发时机(调用点)由外部系统(如接进NeMo Guardrails的self_check_input action)决定,Llama Guard自己不声明触发条件。
- **记录回路**: 模型本身不提供运行记录设施,记录与否全靠调用方。真实案例(PurpleLlama issue #7)显示自定义taxonomy经常不被模型忠实遵守,且没有测试环路去验证taxonomy改动是否真的生效——软判断执行忠实度没有保证。
- **本体-接口同步**: 无——taxonomy是prompt文本,想让多个应用点用同一份taxonomy得自己复制这段文本到每个调用点,没有中心化管理/引用机制,天然会出现多处taxonomy各自漂移的风险。
- **开源情况**: Llama社区许可(权重与代码公开可下载微调,非纯OSI开源),GitHub meta-llama/PurpleLlama(Llama-Guard子目录含MODEL_CARD),模型托管HuggingFace(meta-llama/Llama-Guard-3-8B等多个尺寸)。
- **可抄**: (1) taxonomy即"分类树+每类一句话定义"的结构,是判断"什么算违规"的最小可抄单元,可类比我们手册条目"结论"字段的精简版;(2) 输出格式"safe/unsafe + 违反类别号列表"是极精简的裁决记录格式,可类比决策记录的"一句话+锚点";(3) S14专为"工具调用场景"单独加一类,说明taxonomy会随agent能力扩张而演化,是判例驱动taxonomy修订的一个实例。
- **差距**: 没有"谁拍板/怎么改/推翻条件"这层治理,taxonomy改了就改了,没有版本历史或修改理由记录;且实测(PurpleLlama issue)显示自定义类别经常不被模型可靠遵守——纯粹靠prompt文本表达语义规则,执行忠实度没有硬判断兜底,这正是我们要用管线硬判断兜底解决的问题。
- **来源**: https://github.com/meta-llama/PurpleLlama/blob/main/Llama-Guard3/8B/MODEL_CARD.md | https://huggingface.co/meta-llama/Llama-Guard-3-8B | https://github.com/meta-llama/PurpleLlama/issues/7

## Invariant (Invariant Labs → Snyk Labs)
- **是什么**: agent安全策略引擎——用一种类Python/Datalog的Invariant Policy Language对agent trace(消息+工具调用序列)做规则匹配,支持离线批量分析已录制的trace,或通过Gateway反向代理实时拦截LLM/MCP请求。
- **做法**: 策略文件是.py风格DSL,规则形如`raise "External email to unknown address" if: (call: ToolCall) -> (call2: ToolCall) call is tool:get_inbox call2 is tool:send_email({to: ".*@[^ourcompany.com$].*"})`。`->`表达跨事件的时序关系,`is tool:xxx`做工具类型匹配,可加正则/属性匹配。运行方式:`LocalPolicy.from_string()` + `policy.analyze(messages)`离线分析返回`AnalysisResult.errors`列表;或Gateway把策略接成实时反向代理自动评估每次请求。配套Explorer(trace查看/标注UI,TS+Python的app-api/app-ui)供人工检视被标记的trace。
- **when表达**: `raise "..." if: <模式>`是直接的when表达——用事件模式匹配+时序算子(->)+属性/正则匹配混合表达触发条件,粒度可细到"两次工具调用之间的关系",是本次调研里when表达最贴近声明式条件语句的一个;若条件里接入语义分类器action,硬判断与软判断可在同一条规则里混用。
- **记录回路**: 离线模式下AnalysisResult.errors是这次分析的裁决记录,但README未说明是否持久化存档;实时Gateway据称会拦截请求但未详述日志落盘机制;本该承担"记录+人工复核"角色的Explorer托管服务已停运,这条记录回路目前公开可验证的部分不完整。
- **本体-接口同步**: 无——policy文件可本地分发,Gateway是唯一接入面(反向代理拦所有LLM/MCP流量),中心策略源与执行点是一体的,策略文字即可执行代码,不存在手册与管线分离。
- **开源情况**: GitHub invariantlabs-ai/invariant(核心策略引擎)+ invariantlabs-ai/explorer(trace查看器),开源。但2026年公司被Snyk收购(整合进Snyk Labs),Explorer的托管版已于2026年1月关停并引导用户转投Snyk商业产品——独立OSS项目被收编进商业闭源平台的真实案例。
- **可抄**: (1) `raise "一句话" if: <模式>`把"结论"和"判断依据"绑在同一条规则里,是紧凑的软硬合一表达,可参考其"违规原因用自然语言一句话+机器可判匹配条件"的组合方式;(2) `->`时序算子专门表达跨多个工具调用的因果/序列关系,是"多步骤组合触发"的具体语法参照;(3) Gateway单点反向代理拦截说明"接入面收敛到一个执行点"是可行的工程简化。
- **差距**: 没有治理层(谁拍板改policy/为什么改/历史版本对比),规则写错会静默漏判或误报,没看到白名单式的须确认标志分级;项目本身现状(托管Explorer关停、公司被收购转商业化)提示用第三方SaaS承载记录回路的脆弱性,是我们"运行记录必须自持"设计的一个反面参照。
- **来源**: https://github.com/invariantlabs-ai/invariant/blob/main/README.md | https://github.com/invariantlabs-ai/explorer | https://snyk.io/news/snyk-acquires-invariant-labs-to-accelerate-agentic-ai-security-innovation/

### 结论
- 四个框架都把"软判断的语义描述"和"硬判断的可执行代码"揉在同一份文件里(Colang flow、RAIL/Pydantic validator、Llama Guard taxonomy prompt、Invariant policy),没有一个做手册与管线的物理分层——这印证了我们坚持分离是对的,但也说明分离在工程上要多付出一层编译/绑定成本,得为此专门设计。
- "运行记录"这环做得最扎实的是NeMo Guardrails(四件套log字段+span化tracing)和Guardrails AI(Call/Iteration两级栈),两者字段设计都值得直接照抄;但都止步于"记下这次跑了什么",没有一个做到"批量运行记录反向聚类生成规则候选人裁"——这条回灌回路业界基本空白,是我们设计里相对独特的部分。
- Invariant的`raise "..." if: (call)->(call2) ...`把when表达为事件模式+时序算子的DSL,是本次调研里对"多步骤触发条件"表达最具体的例子,值得参考其算子语法丰富我们手册条目"判断怎么下"字段在多步场景下的表达。
- Guardrails Hub给每个validator打Rule/ML/LLM三选一标签,是"这条判断是硬判断还是软判断"最简洁的字段化做法,建议在我们管线注册表里补一个同类字段。
- Llama Guard(custom taxonomy经常不被模型忠实遵守)和Invariant Explorer(托管记录界面2026年1月关停、公司被收购)两个真实案例分别印证:软判断必须有硬判断兜底校验、运行记录数据必须自持不能依赖第三方托管——都是对我们设计的正向支撑证据。
- 所有四个框架的"中心源与接入面同步"基本是零同步问题,因为它们全是嵌入单应用的库或单点反向代理,规则文件本身就是唯一执行点,没有我们"一份本体手册对多个agent规则文件/执行点"的一对多分发场景——这块业界经验对我们参考价值有限,得自己摸索。


# 类别:工作流/数据编排管线的触发语义(when)表达与运行历史可视化——对照 omnicompany"执行管线缺 when 元数据"与"运行历史可视化"两项设计缺口

## Dagster — Declarative Automation (AutomationCondition)
- **是什么**: 数据编排平台的资产级自动化调度框架。用一棵可组合的布尔条件表达式树(AutomationCondition)挂在每个 asset/asset check 上,取代手写 sensor 代码,声明式回答"什么时候该物化这个资产"。
- **做法**: AutomationCondition 是不可变表达式树:operand(叶子谓词,如 missing/newly_updated/execution_failed/cron_tick_passed 等 11 种)+ operator(&/|/~、.newly_true()边沿触发、.since()相对时序、any_deps_match()/all_deps_match()跨依赖量词)。默认 sensor(default_automation_condition_sensor)每 30 秒对每个 code location 评估一次 tick;每个 InstigatorTick 含 status/timestamp/run_ids/cursor,cursor 内的 AssetDaemonCursor 带单调递增 evaluation_id 和 per-asset 的 AutomationConditionCursor,经去重序列化+zlib+base64 存入 InstigatorState 元数据库。评估结果与 run_ids 关联,可在 Asset Details 页的 Automation tab 按时间线倒查"为什么触发/为什么没触发"。源码见 GitHub declarative_automation/automation_condition.py。
- **when表达**: when 直接编码成表达式树而非自然语言字段,叶子是领域原语(cron到点/有失败/上游更新了),支持 AND/OR/NOT、边沿触发(newly_true)、相对时序(since)、依赖量词(any/all)。可用 with_label() 给子树打标签,UI 折叠/展开显示子条件真假,方便人读"具体因为哪一支条件触发"。
- **记录回路**: 有:每个 tick 评估结果落盘,Asset Details→Automation tab 可按资产按时间倒查每次评估结果与对应的 run_ids,形成"运行↔触发原因"的直接关联。但没有"评估历史自动回灌成新 condition 定义"的机制,回路止步于可查询的决策日志,不到判例聚类改规则那一步。
- **本体-接口同步**: 无中心本体/多接入面分离问题——asset 定义与 automation_condition 是同一份 Python 源码,声明与执行合一,天然不存在不同步。但也因此没有人读的文字手册层,软判断(谁拍板/判例引用)完全不在系统内。
- **开源情况**: 开源,Apache-2.0,github.com/dagster-io/dagster,约 15.8k star,Dagster Labs 商业化支持,活跃。
- **可抄**: 1) when 条件做成可组合表达式树+统一原语库而非散落 if;2) with_label() 让评估历史 UI 能显示"因为哪个命名子条件",直接对应我们给 PipelineEntry 补 when 元数据时的可读性诉求;3) 单调递增 evaluation_id + cursor 做评估-run 关联,避免重复触发。
- **差距**: when 纯代码表达式,没有"结论/判断怎么下/谁拍板/判例引用"这层人类语义手册,软判断靠代码注释或外部 wiki;也没有"新判例反向聚类成规则候选"的机制,全靠工程师手改代码。
- **来源**: https://docs.dagster.io/guides/automate/declarative-automation | https://docs.dagster.io/guides/automate/declarative-automation/automation-condition-reference | https://docs.dagster.io/guides/automate/declarative-automation/customizing-automation-conditions/describing-conditions-with-labels | https://github.com/dagster-io/dagster/blob/master/python_modules/dagster/dagster/_core/definitions/declarative_automation/automation_condition.py | https://github.com/dagster-io/dagster

## Temporal — Event History / 确定性重放 / Visibility
- **是什么**: 持久化执行(durable execution)引擎。不是触发调度框架,而是"运行记录"这一侧的最强工程样本——把一次工作流执行的每一步记成不可变事件日志(Event History),靠重放(replay)恢复/重建执行状态,天然获得完整可审计的运行历史。
- **做法**: 每个 Workflow Execution 对应一条 append-only Event History,持久化在 Temporal Service(Cassandra/MySQL/PostgreSQL 等可插拔存储)。事件由服务端响应外部发生的事和 Workflow 发出的 Command 生成,每个 Event 有 EventType 枚举、EventId、Timestamp、Attributes。历史超过 10240 事件报警、51200 强制终止(需 Continue-As-New)。Side Effect 机制处理非确定性代码(如生成 UUID):首次执行才跑,重放时直接读历史记录的结果不重跑。Web UI/Visibility 提供三种查看态:Timeline(时间轴)、Compact(同类 Event Group 折叠计数)、JSON(完整历史下载,OSS 和 Cloud 均有)。Principal Attribution 可查"是谁/哪个服务触发了这个 Signal/Cancel"。
- **when表达**: 无(该产品的 when/触发调度在 Schedule/Signal 侧,不是它的重点),这里取用的是它"运行记录"范式本身。
- **记录回路**: 记录侧极强(事件溯源+确定性重放保证记录与实际执行行为一致),但不反向回灌——Workflow 代码本身被视为唯一权威,历史只用于恢复和审计,不修订触发逻辑。
- **本体-接口同步**: 无:Workflow 代码本身既是唯一定义也是重放依据,靠"确定性"约束(而非同步机制)保证多语言 SDK/多 worker 重放结果一致。
- **开源情况**: 开源,MIT License,github.com/temporalio/temporal(Server),各语言 SDK 均在 temporalio 组织下,活跃,有商业化 Temporal Cloud。
- **可抄**: 1) 运行记录是结构化、可重放、分级可视(Timeline/Compact/JSON 三态)的事件序列,不是一条流水账日志文本——我们的管线运行历史可视化至少要做到"同类事件折叠+时间轴+可导出原始记录"三档;2) Principal Attribution(谁触发了这一步)值得抄进判例记录;3) "确定性重放"提醒我们:若运行记录要支持"重放验证",执行侧先要满足确定性,这是我们目前设计未考虑的前提。
- **差距**: 完全没有软判断/人读手册/谁拍板这层,是纯工程可靠性设施,不解决"这条 when 该不该改""判例该不该固化成规则"的治理问题;其"确定性"要求也天然排斥"由 LLM 语义判断决定要不要跑"这类我们手册里常见的软触发。
- **来源**: https://docs.temporal.io/workflow-execution/event | https://docs.temporal.io/encyclopedia/event-history | https://docs.temporal.io/web-ui | https://temporal.io/blog/lets-visualize-a-workflow | https://github.com/temporalio/temporal

## Prefect 3 — Automations & Triggers
- **是什么**: 工作流编排里的事件驱动自动化层。把"什么时候该做什么"表达成声明式的 trigger+action 配对(Automation),可挂在部署(deployment)上,也可在 UI/API 独立存在,响应 flow run 状态变化、自定义事件或指标阈值。
- **做法**: Automation = trigger + actions。trigger 类型:Reactive(事件发生即响应)、Proactive(期望事件在窗口内未发生则响应,用于检测"卡住了")、Metric(阈值触发)、Compound/Sequence(组合子 trigger)。声明式字段:type(event/compound/…)、match/match_related(按资源属性过滤事件)、expect(关注的事件类型数组)、posture(Reactive/Proactive)、within(时间窗口,ISO8601)、schedule_after、parameters(Jinja2 模板把触发事件字段填进被触发 flow 的参数)。可写在 prefect.yaml 的 triggers 块随部署提交,也可 `prefect deploy --trigger triggers.yaml/JSON` 或在 UI 里配置。触发本身还会额外发出 `prefect.automation.triggered` 与 `prefect.automation.action.executed` 两类事件,形成自记录链。
- **when表达**: when 被拆成match+expect(匹配哪些资源/事件)+posture(反应式/预警式)+within+threshold(窗口与阈值)几个正交结构化字段,是"when 元数据"最直接可抄的字段设计范式——把 when 从散文变成可查询、可组合的字段。
- **记录回路**: 有基础记录(triggered/action-executed 本身也是可查事件),但没有"历史触发效果反过来调整阈值"的自动回灌机制,和我们"判例聚类成规则候选"一样得靠人工回看事件 feed 手动调。
- **本体-接口同步**: triggers 可在三处定义(prefect.yaml 静态声明、deploy 时 CLI/JSON、UI 独立创建),文档明确"deploy 时传入的 trigger 会覆盖 prefect.yaml 中的定义"——多接入面各自可写,靠"后写覆盖"而非合并处理冲突,是一个值得警惕的反例。
- **开源情况**: 开源,Apache-2.0(核心),github.com/PrefectHQ/prefect,活跃,Prefect Cloud 商业化(部分 action 如 send-email-notification 仅 Cloud)。
- **可抄**: 1) trigger 字段拆成 match/expect/posture/within/threshold 的结构化范式,可直接映射进我们 PipelineEntry 的 when 元数据+规模档位设计;2) Proactive姿态(期望事件 N 分钟内未发生就报警)是我们手册目前缺的一种否定式触发("超时未见判例即视为异常"),值得补;3) 触发本身产生可查询元事件,形成自记录链。
- **差距**: trigger 纯粹是事件匹配规则,没有权威等级/谁能推翻/判例引用这些治理字段,也没有人工确认门(那是 Windmill 的强项);多接入面靠覆盖而非声明式合并同步,与我们"接口层只存指针、本体唯一"的设计方向正好相反,是反面教材。
- **来源**: https://docs.prefect.io/3.0/automate/events/automations-triggers | https://docs.prefect.io/v3/how-to-guides/automations/creating-deployment-triggers | https://docs.prefect.io/v3/concepts/event-triggers | https://github.com/PrefectHQ/prefect

## Apache Airflow 3 — Asset-aware / Event-driven Scheduling
- **是什么**: 老牌批处理编排器的数据感知调度层。DAG 的调度条件从"到点跑"升级为"上游数据资产更新了才跑",并进一步支持"外部事件(消息队列等)驱动资产更新"。
- **做法**: Asset 是轻量对象:uri(必需,唯一标识)+name(可选可读名)+extra(自定义元数据字典,明文存元数据库)+access_control。DAG 通过 `schedule=` 直接引用一个或多个 Asset,支持逻辑运算符组合:`asset1 & asset2`(全部更新才跑)、`asset1 | asset2`(任一更新即跑)、可嵌套。事件驱动侧,AssetWatcher 监控外部源(如消息队列),继承 BaseEventTrigger,实现 shared_stream_key()(标识共享上游资源避免重复监听)、open_shared_stream()(产出原始事件)、filter_shared_stream()(每个 watcher 实例自己过滤)。资产更新记为 AssetEvent(含 uri、source_dag_run 等),下游任务可通过 `triggering_asset_events` 参数直接拿到"这次运行是被哪些资产事件触发的"这份清单。UI 有 Asset Views 展示资产-DAG 依赖图。
- **when表达**: when 表达为"资产依赖图 + 逻辑运算符(&/|)",本质是数据可用性驱动,不如 Dagster 细粒度可编程,但语义直白:调度条件就是"我依赖的数据是否都/任一更新了"。
- **记录回路**: 有:`triggering_asset_events` 把"这次运行为什么被触发"直接透传进任务执行上下文,是"运行历史高亮走了哪条本体路径"这一诉求在执行层最直接的落地——不只是 UI 看,代码里也能取到并继续处理。
- **本体-接口同步**: Asset 定义是 Python 代码单一源;AssetWatcher 作为独立 trigger 同时出现在 UI 和元数据库里,文档未提及多接入面同步问题——同样是代码即定义模式。
- **开源情况**: 开源,Apache-2.0,github.com/apache/airflow,Apache 基金会顶级项目,数据编排领域最大开源项目之一,极活跃。
- **可抄**: 1) 逻辑运算符直接作用于"依赖单元"本身,构成极简图级 when 表达,适合我们手册里规模小、判断简单的管线;2) `triggering_asset_events` 把触发原因当一等公民透传给下游代码,是"运行历史高亮走了哪条路径"在执行层(非仅 UI)的具体实现方式;3) shared_stream_key 去重设计是接入面同步的一个具体技巧。
- **差距**: when 局限于"数据资产是否更新"单一维度,不像 Dagster 能自由组合失败/在途/cron 等多种正交条件;没有人工确认门(需另接第三方或自建);同样没有语义手册层和判例治理。
- **来源**: https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/asset-scheduling.html | https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/assets.html | https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/event-scheduling.html | https://github.com/apache/airflow

## Windmill — Flow Suspend / Approval
- **是什么**: 轻量工作流引擎(对标 Airflow/Temporal 但内置 UI 生成)。这里聚焦其"挂起+审批"机制——把人工确认门做成 flow 定义里的一个声明式步骤类型,而非外挂审批系统。
- **做法**: 在某一步的 Advanced→Suspend 配置里声明:required_events(需收到几个批准事件才恢复,支持多人会签)、timeout(多久未批准则自动取消或转分支)、hide_cancel、resume_form(恢复时需人类填写的表单 schema,含字段名/类型/默认值/enum下拉,default_args 可动态生成默认值)、权限位 require_approvers_to_be_logged_in / require_approvers_to_be_members_of_a_group / disable_self_approval(禁止触发者自批准)。运行时用 `wmill.getResumeUrls()` 生成一次性 resume/cancel 密钥 URL(可发邮件/Slack/Teams),点击即调 webhook 恢复流程;resume_form 提交值作为该步骤输出,下游可用 `resume["字段名"]` 做条件分支。
- **when表达**: 这里的 when 不是"管线该不该开始跑",而是"跑到这一步该不该继续往下走"——正是"长管线需要显式确认"这一诉求的现成实现:确认门是流程定义里的一等步骤类型,不是外挂的人工审核。
- **记录回路**: 弱:suspend/resume 事件作为该步骤的输入输出记录在这次 flow run 详情里(可查是谁、何时、以什么参数恢复的),但没有"审批历史自动生成新审批规则(如某人总批准就免审)"的回灌机制。
- **本体-接口同步**: 不适用——suspend 配置内嵌在 flow 定义本身,resume URL 是运行时动态生成的一次性凭证,不存在需要同步的独立"审批规则文件"。
- **开源情况**: 开放核心模式:backend/frontend 在 AGPLv3 下开放(企业特性除外),client 库(python/deno/go/powershell)是 Apache-2.0;"Community Edition"二进制混有不可二次分发/转售的专有代码,商业托管需另购许可。GitHub: windmill-labs/windmill。
- **可抄**: 1) 把人工确认门做成流程定义里的一等步骤(suspend/resume_form/required_events/timeout),正是我们"长管线要显式确认"最值得抄的落地形态——确认门本身也声明式地写进管线注册表;2) required_events 支持会签计数、disable_self_approval 防止执行者自批准,是我们目前设计未考虑的治理粒度;3) resume_form 表单值直接进下游条件分支,让人的判断成为流程数据的一部分而非外部旁路。
- **差距**: 确认门是运行时"暂停等输入",不解决"这条管线该不该被触发"(那是 when 元数据的范畴),只解决"跑到一半要不要继续";没有语义手册/判例库层,审批理由不会沉淀成可复用的文字判断依据。
- **来源**: https://www.windmill.dev/docs/flows/flow_approval | https://github.com/windmill-labs/windmill | https://github.com/windmill-labs/windmill/blob/main/LICENSE-AGPL

### 结论
- when 元数据应结构化成可查询字段(Prefect 的 match/expect/posture/within/threshold,Airflow 的资产依赖图+AND/OR),而不是一段自然语言描述;同时保留 Dagster with_label() 式的命名/标签层,让机器判断字段与人读解释并存,而不是二选一。
- 运行历史要能回答"这次为什么被触发、走了哪条路径",最佳实践是把触发原因当一等数据透传进执行上下文本身(Airflow 的 triggering_asset_events),再叠加一个按资产/管线维度倒查评估历史的 UI(Dagster 的 Automation tab evaluation history),而不只是事后翻日志文本。
- 事件溯源式运行记录(Temporal 的 Event History)是运行记录可视化的高标准参照:分层视图(Timeline 时间轴/Compact 折叠同类/JSON 原始导出)+ Principal Attribution(记录是谁触发的),这套三态可视化范式可直接搬进我们的管线运行历史设计。
- 长管线显式确认这一诉求已有现成范式:Windmill 把审批做成流程定义里的一等步骤类型(suspend/resume_form/required_events会签/timeout/disable_self_approval防自批准),建议我们的执行管线"须确认标志"直接借这套字段集合,而不是外挂审批系统。
- 几乎所有查到的产品都回避"中心本体与多接入面同步"这个问题——它们统一用代码即定义(as-code)让声明与执行合一,天然不存在需要同步的独立接口层;唯一出现多处可写的是 Prefect(prefect.yaml/CLI/UI 三处都能定义 trigger),它用后写覆盖而非合并处理冲突,是我们要避免的反模式——业界对这个问题没给出好答案,得自己想清楚。
- 没有一个产品做到我们说的语义手册层(结论/判断怎么下/谁拍板/判例引用的文字治理),when 全部是代码/配置表达式,软判断和治理完全在系统外;也没有一个做到新判例自动聚类成规则候选的回灌闭环——这两点是我们设计里业界缺乏对照物、必须自己摸索的部分。


# 类别:大模型可观测与提示版本管理(LLM Observability & Prompt Versioning)

## Langfuse
- **是什么**: 开源LLM工程平台:tracing/observability + prompt管理 + 数据集实验 + 评测,一个产品把"运行记录"和"提示版本"放在同一数据模型里。
- **做法**: Prompt对象字段:name、type(text/chat,创建后不可变)、version(不可变整数1,2,3...递增)、label(可变指针,如production/staging/latest,可自定义,production等可设为protected由RBAC锁定改动权限)、config。运行时用SDK `get_prompt(name)` 按version号或label取回具体prompt对象,再把这个对象整体传给generation观测(Python: `update_current_generation(prompt=prompt)`;Langchain: metadata里塞`langfuse_prompt`;Vercel AI SDK同理),这样trace的generation span就存了当时具体用的prompt对象引用,而不是事后去猜"那时候production是哪版"。UI里打开某条生成记录会高亮当时用的那个prompt版本;prompt详情页有Metrics tab,按version自动聚合延迟/token/成本/分数,可跨版本比较。部署前评测(Experiments,UI或SDK):挑一个prompt+一个数据集,跑LLM-judge或代码评估器打分,和其他版本的历史分数并排比较,人工看分数后手动把production标签挪到新版本——这就是它的"deploy"动作。自托管:Docker Compose/Helm/Terraform,存储为Postgres+ClickHouse。
- **when表达**: 无条件表达式,只有label的人工指派(谁是当前production这一个布尔状态),没有"何时该用哪条规则"的规则化描述。
- **记录回路**: 有,但是"主动评测→人工看板→人工挪指针"型,不是"运行时自动侦测偏离并生成候选修订"型:Experiments在部署前对着数据集打分决定要不要推进;运行时按version自动聚合出的Metrics仪表盘,给人工回滚/前进的判断依据。
- **本体-接口同步**: 运行时按需从Langfuse服务端拉取指定version/label的prompt(有服务端+客户端缓存防延迟),是"接入面向中心拉取"的单向模式;它的设计前提是你把prompt从代码里搬空,全部收进它的中心库,所以不存在"UI改的东西要不要同步回代码仓库"这个问题。
- **开源情况**: 开源,MIT协议(核心);仓库内`ee/`目录企业版功能另有协议。GitHub langfuse/langfuse,YC W23,活跃(commit/discussion频繁)。
- **可抄**: ①trace存的是"prompt对象引用"而非拷贝或猜测,这个显式绑定设计直接对应我们"本次运行用了本体哪一条/哪个版本"的诉求,而且它把机制写到了字段级(SDK参数名都给出来了),是本轮里唯一把这条链路讲清楚的产品。②version(不可变递增)与label(可变指针)分离,可以直接照搬到我们手册条目的版本化设计。③Metrics tab按version自动聚合分数,是"评测结果回灌决策"最简可行的一种UI形态,门槛不高。
- **差距**: when完全没有条件语义——label就是人工挪指针,没有"什么场景/什么输入特征下该用哪个版本"的规则表达,本质是接口层的一个最小指针,不是我们要的语义本体when小节。Prompt本身只是文本/chat模板,没有"结论/判断怎么下/谁拍板/推翻条件"这类结构化字段,更没有互相引用的手册网络。"运行发现偏离手册要求→自动回灌修订本体"这条路径它没有,Experiments是人工主动发起的部署前测试,不是从生产轨迹里自动侦测偏离并生成待裁决条目。
- **来源**: https://langfuse.com/docs/prompt-management/features/prompt-version-control | https://langfuse.com/docs/prompt-management/features/link-to-traces | https://langfuse.com/docs/prompt-management/data-model | https://langfuse.com/docs/evaluation/experiments/experiments-via-ui | https://langfuse.com/changelog/2024-11-22-prompt-experimentation | https://github.com/langfuse/langfuse

## LangSmith
- **是什么**: LangChain公司的商用LLM可观测/评测/Prompt Hub SaaS,Prompt Hub用内容寻址式commit hash做不可变版本记录。
- **做法**: Prompt Hub每次push生成唯一commit hash(捕获prompt文本+变量+关联模型配置),用tag做可移动指针指向某个commit——staging/production是LangChain预留的"environment tag",走专门的promotion UI管理(不能随便在freeform tag选择器里改),也可以自建普通tag。引用方式是URI `owner/prompt-name:commit-hash` 或 `:tag`。Prompt详情页左栏是commit历史+environment列表,右栏commit详情,可以对两个commit做Diff;每个environment维护一份"何时哪个commit被指派"的有序历史,支持一键回滚。Trace侧:官方一手文档(docs.langchain.com/langsmith/manage-prompts)聚焦Hub端的版本管理和commit diff,没有展开trace端如何存储"这次运行用了哪个prompt commit"的字段级机制;这条关联关系只在二手教程/博客(Mirascope、Medium)里被断言存在,一手文档未证实到字段级,故此处不下确定结论。
- **when表达**: 无,tag同样是人工指派/promotion UI手动挪动,不是条件规则表达式。
- **记录回路**: 一手文档只证实了"commit历史+environment指派历史可回滚"这个人工操作留痕;"评测结果是否驱动升级某commit到某environment"只在二手教程文章里被提及,未见一手证据,存疑未采信。
- **本体-接口同步**: 代码侧通过URI(`owner/name:commit-hash`或`:tag`)向Hub拉取,同Langfuse一样是"中心库拉取制"而非推送同步;闭源导致中心与多接入面之间具体的同步/缓存实现未公开。
- **开源情况**: 闭源。LangSmith平台(UI/后端/托管基础设施)不开源,生产可用的自托管需要Enterprise订阅,且SSO/RBAC/审计日志/self-host都锁在企业层;即使自托管也是客户VPC数据面+可能仍依赖云端控制面的混合形态。
- **可抄**: ①commit hash(内容寻址、不可变)+ environment tag(保留名+受管理UI+有序指派历史可回滚)的组合,是判例层"一句话+锚点+可回滚"的一个可参考的不可变记录单元设计。②"每个environment维护有序的commit指派历史"这个审计轨迹思路,值得抄到我们管线注册表的版本化。
- **差距**: 闭源导致机制细节只能靠二手资料交叉验证,置信度低于Langfuse。when语义同样只是人工tag指派,不是条件触发规则。"评测结果驱动是否把某commit推到某environment"这条回灌环,我在一手文档里没有找到证据,不确定它有没有做,不能像评价Langfuse那样给出确定结论。
- **来源**: https://docs.langchain.com/langsmith/manage-prompts | https://changelog.langchain.com/announcements/prompt-tags-in-langsmith-for-version-control | https://changelog.langchain.com/announcements/diff-view-in-langsmith-s-prompt-hub

## Braintrust
- **是什么**: 商用LLM评测+可观测+prompt管理一体化SaaS,主打"生产trace能直接跳回当时用的prompt版本重新调试"。
- **做法**: Prompt有版本历史;Playground里选prompt任务,hover该行会弹出版本子菜单(Latest version + 各历史版本),可选定某个历史版本加入对比。文档原话:生产trace会直接携带"originating prompt version and parameters",点开任意一条生产trace就能在Playground里用当时那个精确prompt版本打开——这是产品行为层面的强绑定描述,但一手docs(braintrust.dev/docs/evaluate/playgrounds)没有展开背后的字段名/存储结构。回灌环:trace页可以对输出做人工标注(annotate),点Optimize进入"Loop"、用这些标注做上下文尝试改写prompt(具体算法未公开);也支持"拿改过的prompt去重跑50条历史生产trace,直接看分数怎么变"——这是拿真实生产流量回放做验证,比单纯跑静态数据集更贴近"用真实运行检验修订"。Playground支持把一次对比保存成不可变Experiment快照,支持diff视图(Improvement/Regression/Tradeoff/Tie四态标签)。
- **when表达**: 无条件表达式,人工在Playground里选version、看对比结果、手动决定用哪版。
- **记录回路**: 本轮5个产品里回灌环做得较实的一个:拿修改后的prompt回放历史生产trace直接看分数变化,再叠加人工annotate→Optimize/Loop生成修订建议;但Loop的具体生成逻辑未公开,最终是否采纳仍是人工点确认,不是自动生效。
- **本体-接口同步**: 闭源,同步机制未公开。已知的架构事实是:自托管时数据面在客户云、控制面在Braintrust云,即便"自托管"也无法完全脱离其云端。
- **开源情况**: 闭源。核心引擎(Brainstore存储引擎)和查询语言(BTQL)专有;仅AI proxy组件在GitHub上以MIT开源(braintrustdata/proxy一类),这是一个有限例外,不代表核心平台开源。自托管是混合架构——数据面部署进客户自己的云(靠Terraform),控制面仍由Braintrust托管,不是完全气密的自托管。
- **可抄**: ①"点开生产trace直接跳转到当时那个prompt版本的Playground"这个交互目标,正是我们要的"下钻:结构图上高亮实际路径"该学的强绑定体验。②"拿新prompt去回放历史生产trace再打分"这个验证方式,是本轮里离"用真实运行检验修订"最近的一个具体机制,值得抄这个动作本身(不必抄它的实现)。
- **差距**: 闭源、一手文档只描述产品行为不描述底层数据结构,置信度较低,很多细节(比如trace上到底存了prompt version的哪个字段)无法验证。when语义没有——纯人工挑版本比对。控制面强依赖SaaS,不是我们想要的"仓库里的手册,可离线完全掌控可审计"的形态。
- **来源**: https://www.braintrust.dev/docs/evaluate/playgrounds | https://www.braintrust.dev/articles/what-is-prompt-versioning | https://www.braintrust.dev/blog/open-sourcing-proxy | https://github.com/braintrustdata/braintrust-deployment/blob/main/LICENSE

## Arize Phoenix
- **是什么**: 开源、OpenTelemetry原生的LLM可观测+评测+prompt管理平台,可本地/自托管运行也有云端版本。
- **做法**: Prompt按name聚合多个version,每次编辑生成新version;默认3个tag(production/staging/development)可自建更多,tag是贴在某个具体version上的标签而非单一可移动指针,一个version可以被多个tag同时命中,任何环境按tag名拉取对应version。Prompt Playground支持UI里直接改+对比多模型/多变体;Client SDK可以`create/update prompts`动态写入、按name/version/tag拉取模板并做变量渲染。官方有一篇标题为"Prompts in Code"的文档专门讨论UI与代码库的双向同步,但该页面WebFetch返回403,我只拿到标题和目录级信息,没能读到正文验证具体同步机制,这是本轮唯一没吃透的关键页面。Trace/Observability侧:核心是OpenTelemetry(OTLP)采集,40+框架/provider/语言自动埋点(LangChain、LlamaIndex、DSPy、Vercel AI SDK、OpenAI、Bedrock、Anthropic等);数据模型里Traces/Datasets/Experiments/Evaluations是四个一等公民,Experiments被描述为"tracked prompt/model/retrieval iterations"。但"某条trace的span上是否直接记录了用的是哪个prompt version"这种字段级绑定,我没能在一手来源里核实到(不像Langfuse文档那样写出具体SDK参数名),这条留空不下确定结论。
- **when表达**: 无条件表达式,tag是人工贴标(允许多对多,可理解为轻量"用途标签"而非严格的单一生效指针)。
- **记录回路**: 一手资料只证实到"Experiments把prompt/model/retrieval的迭代过程当一等公民记录"这一层描述;没有查到"自动检测生产trace里某prompt版本效果变差、生成待人工确认的回灌条目"这类机制的一手证据——功能可能存在(同类产品常见)但今天未查实,如实标注未证实而非直接判定"无"。
- **本体-接口同步**: 官方文档标题明确提到UI与代码库的双向同步(Prompts in Code),但该页面这次没能读到正文(403),只有标题级确认,细节待补查。
- **开源情况**: 开源,Elastic License 2.0(ELv2)——允许自用/自托管/修改,但限制"把它作为托管服务转售给第三方"。GitHub Arize-ai/phoenix,9k+ star,活跃(2025年4月发布prompt管理模块,2026年Phoenix 8.0继续加强prompt engineering功能)。部署方式:`pip install arize-phoenix`本地跑、Docker镜像、Helm on K8s,也有云端app.phoenix.arize.com。
- **可抄**: ①"Prompts in Code"这个提法本身,是本轮里唯一一个明确在标题层面讨论"数据库里的版本"与"代码里的定义"该如何互相同步的产品,值得后续单独针对这篇文档做二次调研,把机制读透。②tag可以打在任意version上、一个version可被多个tag命中,比Langfuse"一个label同时只指一个version"更灵活,更接近我们希望的"多接入面各自钉住不同版本"的场景。
- **差距**: trace到prompt-version的强绑定字段没有查实,可信度不足以像评价Langfuse那样给出确定结论。when语义同样是人工贴tag,没有条件表达式。ELv2协议对"魔改后对外提供托管服务"有限制,若只是内部自用/参考架构则无影响。
- **来源**: https://github.com/Arize-ai/phoenix | https://arize.com/docs/phoenix | https://arize.com/docs/phoenix/get-started/get-started-prompt-playground | https://arize.com/docs/phoenix/prompt-engineering/overview-prompts/prompts-in-code | https://community.arize.com/x/arize-ax-releases/1h60hy0sqx6q/phoenix-80-release-game-changing-features-for-prom

## promptfoo
- **是什么**: 开源、声明式YAML配置驱动的LLM评测/红队CLI+本地Web UI,面向"CI式测试套件"而非持续在线可观测平台,不自带prompt版本注册表。
- **做法**: 单文件`promptfooconfig.yaml`纳入git版本控制,声明prompts(内联文本/文件路径/结构化对象,含id、label、raw字段)、providers或targets(模型清单)、tests(vars+assert,每条assert含type/value/threshold/weight)。执行`promptfoo eval`后结果落盘到本地SQLite(`~/.promptfoo/promptfoo.db`,Drizzle ORM管理,可用`PROMPTFOO_CONFIG_DIR`改路径),也可导出时间戳JSON/CSV/YAML/HTML快照进仓库手动留痕。`promptfoo view`起本地web UI,支持在多次eval间切换(eval selector)、逐条diff对比(Compare,绿色新增/红色移除)、看pass率图表、看每格完整output/prompt/变量/评分详情;命令行`eval --compare baseline.json`可直接对比两轮结果找回归。可选`promptfoo share`把结果推到官方Cloudflare KV公开分享(存两周),或接Cloud/Enterprise版做团队级持久化;自托管容器里SQLite在多副本部署下无法共享,需要外置数据库或Cloud版才能多副本持久化。它自己不发明prompt版本号系统——版本管理外包给git(config进仓库历史)或外部prompt管理平台:官方文档明确给出`langfuse://name@label`或`langfuse://name:version`语法,直接从Langfuse按版本/label拉取prompt,变量自动映射;但这条集成是单向只读(从Langfuse读,不把评测分数或trace回写进Langfuse)。它的PR层回灌环是:官方GitHub Action在改动prompt文件的PR上跑eval,把结果贴成PR评论,人工看评论决定要不要合并。
- **when表达**: 无条件表达式。
- **记录回路**: 有,但发生在CI/PR审阅层:改prompt的PR跑eval把结果贴成PR评论,人工看着评论决定合并与否;不是运行时生产trace驱动的自动回灌,也不写回中心prompt库。
- **本体-接口同步**: 对外(如Langfuse)是单向只读集成——从中心prompt库拉取指定version/label跑评测,不回写分数或结果;对自身而言,它没有"中心+多接入面"概念,本体就是本地SQLite/可选云端share,是单机CLI工具的形态。
- **开源情况**: 开源,GitHub promptfoo/promptfoo,主流开源许可(未逐字核对LICENSE文件原文,以repo公开信息为准);被OpenAI、Anthropic等使用(官方repo描述自述)。
- **可抄**: ①"配置即版本"——不额外发明版本号系统,完全靠git commit做版本历史,是最省心的接入面同步方式,值得我们执行管线的小需求场景直接照搬。②eval结果落SQLite+web UI diff+CLI `--compare`,是"评测回灌"链路里最具体可执行的参考实现。③GitHub Action在改prompt的PR上跑eval、把结果贴成PR评论,是"新判例经评审进主线"这条回路一个朴素但真实生效的落地形式(靠人工审阅门禁,而非自动写库)。
- **差距**: 完全没有"prompt version"这个一等公民概念,也没有生产环境trace(面向CI测试而非线上可观测),所以"这次线上运行用了本体哪个版本"这条链路它根本不做——它只回答"这次测试用了config文件的哪个git commit状态"。when语义不存在。它和Langfuse的只读集成说明"评测引擎"与"记录/可观测中枢"在业界常被拆成两个独立产品靠一个协议对接,而非一个产品包圆到底。
- **来源**: https://github.com/promptfoo/promptfoo | https://www.promptfoo.dev/docs/configuration/reference/ | https://www.promptfoo.dev/docs/usage/web-ui/ | https://www.promptfoo.dev/docs/integrations/langfuse/ | https://www.promptfoo.dev/docs/usage/self-hosting/ | https://github.com/promptfoo/promptfoo/issues/880

### 结论
- "trace显式携带prompt版本引用"这条链路,本轮5个产品里只有Langfuse把机制写到了字段级(运行时把get_prompt()取回的对象整体传给generation);LangSmith、Braintrust、Phoenix都只在营销页/二手材料里断言trace记录了prompt版本,一手文档没交代具体schema——说明这条链路即使是头部产品也普遍没文档化到位,我们的手册如果能把"本次运行用了本体哪个版本"这条链路讲清楚到字段级,本身就是差异化优势。
- 所有5个产品的"when"都退化成了人工挪的一个指针(label/tag/commit-hash+environment),没有一个做到"什么条件下该用哪个版本"的规则化表达——印证了我们设计里"软判断用文字写明when"这件事在业界确实是空白,不是重新发明轮子。
- "评测结果回灌到版本修订"这条环,做得最实的是promptfoo(PR评论门禁,朴素但真的挡人工审阅)和Braintrust(拿新prompt回放历史生产流量再打分),两者共同点是"结果先给人看,人来点确认"——没有一个产品做到"自动生成待裁决的规则候选人"这种半自动回灌,这条如果我们做出来是真空白。
- 版本(不可变递增)与标签/指针(可变,指向某版本)分离,是Langfuse和LangSmith共有的通用范式,值得直接照搬到手册条目的版本化设计;LangSmith额外的"environment维护有序commit指派历史可回滚"值得抄进管线注册表。
- promptfoo证明"评测引擎"和"记录/可观测中枢"可以是两个独立产品通过一个只读协议(langfuse://)对接,而不必一个产品包圆——提示我们的三层设计(本体/管线/接口)也该允许拆成可替换组件对接,而非强耦合成单体。
- 开源程度两极分化:Langfuse和Phoenix核心开源、可完全自托管审计,机制可读到源码/文档字段级;LangSmith和Braintrust核心闭源,即便给了"自托管"选项也是数据面自托管、控制面仍挂在其云上,这两家的机制描述本次都要标注"二手/未完全核实",可信度明显低于前两者——后续如需要读实现代码级细节,应优先深挖Langfuse和Phoenix。


# 类别:Agent 知识/记忆的登记与晋升产品(对照"判例经反向固化器聚类成规则候选、人裁后进手册"这条回路)

## Cognition Devin — Knowledge & Playbooks
- **是什么**: 闭源自主 AI 软件工程师产品内建的两类持久化知识对象:Knowledge=零散事实/约定,Playbook=可复用任务模板(步骤+成功标准+护栏)。解决'每次都要重新教 Devin 同样的东西'。
- **做法**: Knowledge 字段=Trigger Description(必填,自然语言触发描述)+ Content(几句话)+ 可选 Macro(唯一标识符如 !deploy-checklist,会话内快速引用)+ 可选 Repo Pinning(作用域:无仓库/指定仓库/所有仓库)。组织级分 Organization Knowledge / Suggestions / Enterprise Knowledge 三个 tab,支持嵌套文件夹+批量启停,可'Promote to Enterprise'。Playbook 字段更结构化:Procedure(逐行祈使句步骤,要求互斥穷尽)、Specifications(完成后应为真的后置条件)、Advice(易错点提示)、Forbidden Actions(禁止事项)、Required from User(需人提供的外部输入);同样有 !macro 触发,且有版本历史——每次编辑保存生成新版本,可查看/回滚旧版本。
- **when表达**: Trigger Description 不是关键词匹配,是给模型的语义线索,由 Devin 在工作中自行判断当前任务语境是否匹配后决定要不要检索该条目。官方文档明确指出模糊 trigger('coding stuff')效果差,具体 trigger('当在 payments-service 仓库写数据库查询时')效果好,即 when 表达质量直接决定命中率。
- **记录回路**: 本类别里最贴近我们'判例晋升'的产品化实现:Devin 会根据会话中用户的纠正/反馈自动生成 Knowledge Suggestion(草稿),用户可编辑后保存、要求重新生成,或直接 dismiss;组织后台维护一份 Pending Knowledge Suggestions 列表供人工逐条审核。形成了 运行(会话反馈)→候选(自动生成 suggestion)→人审(edit/dismiss/save)→入库 的完整闭环,但公开文档未见任何'退役/过期'机制。
- **本体-接口同步**: Knowledge/Playbook 存于 Cognition 云端账户/组织级库,通过 Repo Pinning 和 Enterprise 层级分发到不同仓库/团队;不以文件形态存在、不经 git 分发,产品 UI 与 session 运行时共读同一后端,不存在'本体文件与执行面不同步'的问题。
- **开源情况**: 闭源 SaaS(Cognition Labs),机制通过官方文档公开可查。
- **可抄**: 1) Trigger Description 作为独立必填字段且强调'具体优于模糊',直接对应我们手册条目'什么时候'字段该怎么写才有效;2) 会话结束自动生成知识候选 + 人工 edit/dismiss/save 三态审核,是'判例聚类成规则候选、人裁后进手册'回路的现成产品化范式,值得照抄这套'建议卡片而非自动写入'的交互;3) Playbook 的 Procedure/Specifications/Advice/Forbidden Actions/Required from User 五分法,和我们条目的'结论/判断怎么下/谁拍板/推翻条件'结构高度同构,可直接借鉴字段拆分;4) 版本历史(每次编辑生成新版本、可回滚)值得抄进我们的判例引用机制。
- **差距**: Knowledge 本身只是一段自由文本+一个 trigger,粒度比我们手册条目粗,不含'谁拍板/推翻条件'字段;条目间没有互引图;trigger 完全靠 LLM 语义检索,没有第二层'声明式硬判断'(即完全没有对应我们'执行管线')兜底确定性触发;没有'这次运行到底用了哪条 Knowledge'的结构化下钻可视化,只能看到是否被检索。
- **来源**: https://docs.devin.ai/product-guides/knowledge | https://docs.devin.ai/product-guides/creating-playbooks | https://cognition.com/blog/june-24-product-update

## OpenHands Skills(原 microagents)
- **是什么**: 开源自主编程 agent OpenHands 里挂载在仓库内的知识片段/工作流,用关键词或'always'触发注入上下文,解决'仓库特定规范/工具用法要反复讲'的问题。microagents 是 V0 术语,现已标记 deprecated,V1 统一改称 Skills,机制基本沿用。
- **做法**: 加载优先级:.agents/skills/(推荐,V1)> .openhands/skills/(deprecated)> .openhands/microagents/(deprecated,向后兼容),同名只取第一个匹配路径。每个技能是 <name>.md 文件;repo.md 是仓库总览,可无 frontmatter(默认作为'总是加载'的仓库 agent);其余文件用 YAML frontmatter 声明 triggers:[keyword1, keyword2],命中关键词才把该 md 内容注入上下文。触发类型三档:always(无 triggers,总是加载,用于仓库级约定)、keyword-triggered(命中才加载)、manual(仅用户在界面手动触发,用于任务方案类文档)。仓库私有技能之外还有公共/共享技能库:github.com/OpenHands/skills(原 microagents 公共集,含 github.md/docker.md/kubernetes.md/code-review.md/security.md/ssh.md 等)和更新的全局技能注册表 github.com/OpenHands/extensions,供跨仓库复用与社区 PR 贡献。
- **when表达**: when 直接编码成关键词字符串列表(纯字符串匹配,非语义匹配),命中即注入。是三层里最'硬'的触发方式,比 Devin 的语义 trigger 更接近我们说的管线注册表 when 元数据,但粒度只有 keyword/always/manual 三档,没有规模声明或确认标志字段。
- **记录回路**: 无。公开文档未见任何'会话结束自动建议新技能'或'从运行历史回灌'机制,技能完全靠人工手写 md 放进仓库或提 PR 到公共仓库,是纯手工登记,没有晋升回路。
- **本体-接口同步**: 仓库私有技能通过 git 仓库本身分发(随代码库克隆自动生效,天然与代码同一次提交版本化);公共技能通过独立 OpenHands/extensions 仓库,agent 运行时按加载优先级去重合并。技能本身即纯文件,git 即中心源,不存在额外的'中心库→多接入面'同步机制。
- **开源情况**: 开源,Apache-2.0(github.com/OpenHands/OpenHands、OpenHands/skills、OpenHands/extensions 均为公开仓库)。
- **可抄**: 1) triggers 关键词列表作为 YAML frontmatter 字段,是最小成本的确定性 when 表达,可作我们管线注册表里'快速命中'档(比语义判断更省 token、更可预测);2) always/keyword-triggered/manual 三态分类,对应我们'规模档位'里的最小档(小需求迅捷返回不必语义判断);3) 私有目录优先级覆盖公共同名条目的加载规则,是'中心源+多接入面'最朴素可行的实现,值得参考其去重优先级设计。
- **差距**: 没有判例记录/决策日志层,skills 是静态知识而非'运行产生的判例';没有产品内建人审门(谁能提交新 skill 完全靠仓库 PR 流程把关,不是产品特性);没有版本历史 UI(版本化纯靠 git log);frontmatter 字段极简,不含'谁拍板/推翻条件/判例引用'等结构化字段,复用只到'关键词命中就整段注入'的粗粒度。
- **来源**: https://docs.openhands.dev/openhands/usage/microagents/microagents-overview | https://github.com/OpenHands/OpenHands/blob/main/skills/README.md | https://github.com/OpenHands/OpenHands/issues/7505

## Mem0
- **是什么**: 开源'记忆层' library/service,给 LLM agent 提供跨会话记忆的抽取、存储、检索,解决'每次都要把全部历史塞进 context'的问题。
- **做法**: 核心 API 四件套:add()(从对话消息抽取记忆候选)/ search()(按 user_id/agent_id/session 等 filter 做向量+BM25 关键词+实体匹配三路检索融合打分)/ update()/ delete()。旧版 AUDN 模型:每条抽取候选先做向量检索找最相似的 k 条已有记忆,再交给 LLM 判定 Add/Update/Delete/No-op 四态,而非写死 if/else 规则;新版 v3 把两次 LLM 调用合并为一次、只做 Add,简化对已有记忆的关系判断。记忆按 user_id/agent_id/session 三级 scope 组织,支持 metadata/categories 打标签。自托管有本地 dashboard(localhost:3000),云端 Mem0 Platform(app.mem0.ai)提供管理界面,但主要是'查看',不是结构化审阅工作流。
- **when表达**: 无显式 when 字段——检索时机完全由应用层代码决定何时调用 search(),库本身不像 Devin/OpenHands 那样让记忆自带触发条件。
- **记录回路**: 有自动抽取→判定的回路(add 阶段 LLM 抽取 + 旧版 AUDN 四态决策),但全自动无人审,没有'人工批准新记忆入库'的产品化步骤。社区有提案(issue #5330)讨论按访问频率做类 Ebbinghaus 遗忘曲线的记忆衰减/自动清理,但属于待定特性而非稳定能力。
- **本体-接口同步**: 记忆存于 Mem0 自管的向量库(+可选图存储如 Neo4j),不同 agent/session 通过 user_id/agent_id 参数共享同一份中心存储,属于'中心存储+多路 API 读写',不存在类似文件分发的同步问题。
- **开源情况**: 开源(github.com/mem0ai/mem0),同时有闭源云平台 Mem0 Platform。
- **可抄**: 1) 语义+关键词(BM25)+实体匹配三路融合打分排序,值得参考进我们'手册互引图+全链路下钻'的检索侧设计;2) 从 AUDN(把 Add/Update/Delete/No-op 判断交给 LLM)演进到 ADD-only 的简化路径,提示'自动判定记忆去留'容易冗余膨胀,业界最终选择收窄——我们判例晋升回路里'要不要自动 update/delete 旧判例'也该优先选简单策略,别一开始上复杂状态机。
- **差距**: 完全没有权威等级/谁拍板概念,抽取和写入全自动、无人审门,容易造成记忆污染(这也是后来简化到 ADD-only、并讨论加衰减机制的诱因);没有互引图或版本历史;when 触发条件不是记忆自带属性而是外部代码逻辑,与我们'条目自带 when'的设计方向不同。
- **来源**: https://github.com/mem0ai/mem0 | https://docs.mem0.ai/migration/oss-v2-to-v3 | https://arxiv.org/html/2504.19413v1 | https://github.com/mem0ai/mem0/issues/5330

## Letta(原 MemGPT)
- **是什么**: 开源有状态 agent 框架,核心贡献是让 agent 自己编辑自己的记忆(self-editing memory),解决固定上下文窗口下的长期记忆管理问题,更多是研究成果产品化,而非知识库/判例库。
- **做法**: 三层记忆:Core Memory(常驻上下文,由多个具名 memory block 组成,每个 block 有 label 唯一标识 + description 描述用途 + value 字符串内容 + limit 字符上限,默认 2000 字符/block,超限报错供 agent 感知)、Recall Memory(对话历史,存在上下文外可搜索)、Archival Memory(长期外部向量库,agent 通过工具调用插入/查询)。Agent 用工具函数 core_memory_append(追加)与 core_memory_replace(整体替换,空字符串等于删除)在推理循环里主动改写自己的 core memory block;支持无用户消息时的'心跳'式后台反思轮次,整理归档记忆、重写变乱的 human block、把最近对话浓缩成稳定笔记。memory block 还可以被多个 agent 共同挂载,一处更新对所有挂载它的 agent 立即可见。
- **when表达**: 无外部声明式 when 字段,完全由 agent 自己在推理时判断'这轮该不该调用记忆工具改写',触发条件内化在模型推理里。
- **记录回路**: '运行→改写'回路就是 agent 推理循环本身(工具调用即改写),没有独立于对话之外的'从多次运行归纳规则候选'层;周期性反思轮次概念上接近'定期退役审计',但审计者是模型自己,无人审门。
- **本体-接口同步**: 单 agent 内部状态,不存在中心库分发多接入面的问题;跨 agent 共享靠显式挂载同一 block,不是组织级知识库广播机制。有 ADE(Agent Development Environment)图形界面查看/编辑 block,及 Letta Cloud 托管平台。
- **开源情况**: 开源(github.com/letta-ai/letta),Apache-2.0,另有商业化 Letta Cloud 与图形化 ADE。
- **可抄**: 1) memory block 的 label+description+limit 三件套,把'一条知识该占多大上下文预算'显式声明出来,值得在我们'接口投影'里给注入到 CLAUDE.md/skill 的内容标注预算上限;2) core_memory_append 与 core_memory_replace 区分'追加'和'整体重写',对应我们判例回灌手册时'新增判例'与'修订已有条目'两种不同写操作,值得在管线里显式区分;3) 同一 block 挂载给多个 agent、更新即时对所有挂载者可见,是'中心条目多接入面同步'的一个轻量实现思路。
- **差距**: 没有'谁拍板/推翻条件'的人审层,写记忆的决定权在模型自己(生产环境里这是被诟病的风险点);没有判例(单次运行的原子记录)与规则(可复用条目)的层级区分,core memory 混杂'事实性用户信息'与'行为规则',粒度不对齐我们'结论/when/判断怎么下'的结构化字段;没有互引图。
- **来源**: https://docs.letta.com/guides/agents/memory-blocks/ | https://docs.letta.com/concepts/memory-management/

## Zep / Graphiti
- **是什么**: Zep 是商业化 agent 记忆服务,核心引擎 Graphiti 是开源'时序知识图谱'库,把对话与结构化业务数据都建成带时间戳的图谱,解决'记忆里的事实会随时间变化、旧事实不该被直接覆盖丢失'的问题。
- **做法**: 数据模型四层:Episode(原始摄入的每条消息/事件,作为可溯源的证据源)→ 语义抽取出 Entity 节点(人/产品/概念,summary 随时间演化)与 Fact/Edge(实体间三元组关系,带有效期区间)→ 支持开发者用 Pydantic 自定义 ontology(自定义实体/边类型)。双时态(bi-temporal)模型:每条边同时记录'事件发生时间'与'摄入时间',都带 validity interval;新 episode 到来时用语义+关键词+图搜索判断是否与已有知识冲突,冲突时用时间元数据把旧事实标记'作废但不删除'(invalidate not delete),可查询'现在为真'或'某个历史时间点为真'。
- **when表达**: 无独立人写 when 字段——'何时该更新/作废哪条事实'由图谱摄入算法在每次新 episode 到达时自动判定(语义/关键词/图搜索比对冲突),不是声明式规则。
- **记录回路**: Episode 本身即'原始运行记录',每条派生 Fact 都能追溯回源 Episode(provenance),比 Mem0 更强调可溯源;但没有'人工审核候选事实再决定要不要写入图谱'的门,摄入全自动。
- **本体-接口同步**: Graphiti 库本身可被多种客户端复用;Zep 商业服务在其上加了 Dashboard(图可视化、debug 日志、API 日志)、MCP Server(标准协议,任何支持 MCP 的 agent 都可挂载读取同一知识图谱)、以及 FastAPI REST 服务,是'中心图谱存储+多协议接入面(REST/MCP)'的实现。
- **开源情况**: Graphiti 引擎开源 Apache-2.0(github.com/getzep/graphiti);Zep 托管服务本身商业化为主,具体条款未深入核实。
- **可抄**: 1) '事件发生时间 vs 摄入时间'双时态字段,是我们判例记录目前缺的维度——一条决策该同时记录'决策发生时间'和'被写入本体的时间',利于回溯审计;2) 'invalidate not delete' 语义,直接对应我们本体条目被推翻后不该物理删除、而应保留判例引用链;3) 每条派生 Fact 强制关联回源 Episode 的可溯源设计,值得抄进'反向固化器聚类成规则候选'回路,保证每条候选规则都能点回具体是哪几次运行触发的。
- **差距**: 全自动摄入无人审门,与我们'人裁决才进手册'的铁律相反;没有权威等级概念,所有 Fact 平权,只按时间新旧决定有效性,没有'谁拍板'字段;实体/事实的语义颗粒度是知识图谱三元组,不是'结论/when/判断怎么下'这种叙述性条目,难以直接承载复杂软判断的文字说明。
- **来源**: https://arxiv.org/abs/2501.13956 | https://github.com/getzep/graphiti | https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/

## Claude Code — CLAUDE.md + Auto Memory
- **是什么**: Claude Code 自带的双层记忆机制:人写的 CLAUDE.md(指令/规则)与 Claude 自己写的 Auto Memory(经验笔记),解决'每次新会话都要重新交代项目背景/重复纠正同一个错误'的问题。是本类别里离我们'接口层只存指针、本体不进接口文件'最近的一个反例——它恰恰是接口层直接堆内容,不做分层。
- **做法**: CLAUDE.md 分四级作用域(管理策略 managed policy / 用户 / 项目 / 本地 CLAUDE.local.md),按目录树从根到当前工作目录逐级拼接进 context;支持 @path 语法导入其它文件(最多递归 4 层);支持 .claude/rules/ 目录下按 paths glob 做路径限定的条件加载(仅当 Claude 读到匹配路径的文件时才加载该条规则)。Auto Memory 是独立目录 ~/.claude/projects/<project>/memory/,以 MEMORY.md 为索引文件(启动时只加载前 200 行或 25KB,取先到者;更详细内容拆到独立主题文件如 debugging.md,按需 on-demand 读取,不在启动时全量加载)。Auto Memory 按 git 仓库(而非目录)划分作用域,同一仓库所有 worktree 共享一份记忆,不跨机器同步,需 v2.1.59+。
- **when表达**: 唯一显式 when 表达是 .claude/rules/ 的 paths frontmatter(glob 路径匹配,类似 OpenHands 的 keyword trigger,但匹配对象是文件路径而非关键词),触发时机是'Claude 读到匹配路径的文件时'而非每轮对话开始;CLAUDE.md 主文件与 Auto Memory 的 MEMORY.md 无 when 字段,是无条件全量注入(仅受行数/大小阈值限制)。
- **记录回路**: Auto Memory 由模型在会话中自主判断'这轮学到的东西以后有没有用'并直接写盘,没有暂存候选、没有人工 approve/dismiss 步骤——与 Devin 的 Knowledge Suggestion 形成鲜明对比(Claude Code 是模型自主写入生产文件,Devin 是模型生成建议、人点头才入库)。用户唯一的审核手段是事后用 /memory 浏览、手动编辑或删除已写入的 md 文件,是'先写入、后置人工纠错'而非'先人审、再写入'。
- **本体-接口同步**: 项目级 CLAUDE.md 通过 git 与团队共享(等同 OpenHands 仓库内 skills 模式);组织级 managed policy CLAUDE.md 通过 MDM/组策略/Ansible 分发到所有机器,是唯一带'下发到多接入面'机制的一档,但是单向广播,没有从各机器运行结果回灌到中心策略的回路;Auto Memory 明确'机器本地、不跨机器同步',同一用户在两台机器上会攒出两份不同的经验笔记。
- **开源情况**: 闭源产品(Anthropic),仅文档公开。
- **可抄**: 1) MEMORY.md 索引文件+详细内容拆到主题文件、只有索引前 200 行/25KB 强制启动加载、主题文件按需读取,是'接口层只存指针、本体不进接口文件'的一个朴素现成范式,可直接参考这套'索引强制加载+详情按需展开'的分层加载策略;2) .claude/rules/ 的 paths glob 触发是便宜好用的'文件路径当 when 条件'模式,适合我们管线注册表对'改了哪个目录就该触发哪条规则'的场景;3) 管理员分发的 managed CLAUDE.md 不可被个人配置排除、优先级最高,这种'组织策略强制生效、个人无法静默关闭'的设计对我们'须确认标志'的强制性有参考价值。
- **差距**: 最大差距是完全没有晋升审核门——Auto Memory 是模型单方面决定写入生产文件,官方文档也承认'Claude 不保证严格遵循、CLAUDE.md 是 context 不是强制配置',这与我们'新判例经反向固化器聚类成规则候选、人裁后进手册'的设计正相反(我们要求候选态+人审,它是直接生产态);没有版本历史/回滚 UI(纯裸 git,不 commit 就无历史);没有互引图或全链路下钻可视化,/memory 只是文件列表;CLAUDE.md 与 Auto Memory 都是扁平 markdown,不含'结论/when/谁拍板/判例引用'结构化字段,质量全靠人工 PR review 把关,产品本身不强制任何字段。
- **来源**: https://code.claude.com/docs/en/memory

### 结论
- '判例晋升需要候选态+人审门'这件事目前只有 Devin 一家把它产品化(会话反馈→自动生成 Knowledge Suggestion→人工 edit/dismiss/save 三态),其余全部是模型自主直写生产文件(Claude Code Auto Memory、Letta core memory)或全自动无人审摄入(Mem0、Zep/Graphiti)——这印证我们坚持'人裁决才进手册'不是过度设计,而是业界目前做得最少、最难产品化的一环。
- when 触发条件的表达在业界呈三档:纯语义模糊 trigger(Devin,交给模型自行语义判断,精度取决于描述具体程度)、关键词/路径硬匹配(OpenHands triggers、Claude Code rules paths,确定性高但粒度粗)、完全内化不做外部声明(Letta、Zep,靠模型推理/摄入算法自行判断)——我们手册'什么时候'字段应同时支持'文字语义描述'(喂给 LLM 判断)和'可选硬匹配 key'(喂给管线判断),不要二选一。
- '索引文件强制加载+详情文件按需读取'(Claude Code MEMORY.md 200行/25KB 阈值、主题文件 on-demand)是接口层'只存指针'的现成范式,可直接照抄这套两级加载机制,不必自己从零发明。
- 可溯源(provenance)链路是我们目前较弱的一环——Zep/Graphiti 把每条抽炼出的 Fact 强制关联回源 Episode,值得抄进'反向固化器聚类成规则候选'回路,让每条候选规则都能点回是哪几次具体判例/运行触发的,而不只是聚类后的抽象文字。
- 版本历史是稀缺能力——只有 Devin Playbooks 明确做了'每次编辑生成新版本、可查看回滚'的产品化版本控制,其余产品(Mem0/Letta/OpenHands skills/Claude Code)版本化全靠底层 git 或压根没有,提示我们手册条目的版本管理不能想当然指望 git log 好用,需要专门的'判例引用/supersedes'字段显式指向前一版本。
- 没有一个产品做到'手册互引图+全链路下钻'级别的可视化,大多止步于'文件列表/dashboard 看记忆'——这块反而是我们设计里领先市面产品的部分,不必因'没有先例'而怀疑价值,但要留意大厂没做出来也可能是难度/性价比使然,需评估投入优先级。


# 类别:自进化上下文/手册(self-evolving context & playbook)研究与实现——覆盖 agent playbook 演化、技能库、工作流记忆、提示词编译与进化五类代表性工作

## ACE (Agentic Context Engineering)
- **是什么**: 2025论文+开源框架(Stanford/UC Berkeley/SambaNova),把'上下文'当成可持续演化的playbook,用Generator/Reflector/Curator三角色循环,让LLM agent从执行反馈里增量积累/精炼策略而不改权重。是六个项目里最贴近'自动维护的手册'的一个,专门论证并命名了手册退化的两种失败模式。
- **做法**: playbook是结构化bullet列表,每条格式为 `[section_slug-00000] helpful=X harmful=Y :: content`,按分区组织(如STRATEGIES & INSIGHTS/FORMULAS & CALCULATIONS/COMMON MISTAKES TO AVOID)。Generator跑任务产出推理轨迹(含成功策略与踩坑);Reflector对轨迹做诊断、抽取具体教训(可多轮迭代);Curator把教训编译成'delta bullet集合'(只改增量、不重写全篇),用非LLM的确定性代码合并进主playbook。grow-and-refine:新bullet追加唯一id,命中已有bullet则更新helpful/harmful计数;去重靠语义embedding比对,合并时机可'每次delta后'或'lazy——上下文窗口超限才做'。开源实现里每次delta操作落一份 curator_operations_diff.jsonl 日志。
- **when表达**: 无显式when字段。每条bullet没有独立的适用条件标注,触发靠Generator在具体任务里隐式判断哪条bullet相关,不是我们要求的文字写明判断准则。
- **记录回路**: 有明确回路:执行反馈(非人工标注)驱动Reflector/Curator更新,curator_operations_diff.jsonl记录每次delta,可视为变更日志。但没有'退役审计'或跨会话判例聚类升级机制,只有单一playbook的持续增量,也未见版本化/回滚设计。
- **本体-接口同步**: 无——playbook本身既是本体也是直接喂给下游LLM的system prompt/context,不存在中心本体分发到多接入面的问题。离线(优化system prompt)和在线(优化agent memory)是两种部署形态,不是本体/接口的分层。
- **开源情况**: 官方开源 github.com/ace-agent/ace,Apache-2.0,约1.2k star/151 fork,论文被ICLR 2026接收;另有第三方实现 github.com/kayba-ai/agentic-context-engine(称Skillbook,支持100+ LLM provider,含SQLite/MLflow集成)。
- **可抄**: (a) bullet级最小更新单元+显式helpful/harmful计数器,天然防止整段重写掉细节;(b) Curator用确定性代码合并而非二次LLM生成,减少幻觉;(c) 明确命名并可检测的两种退化模式(context collapse突降、brevity bias同质化),可直接搬进我们的退役审计检测项;(d) delta操作日志(diff.jsonl)可对应我们判例层jsonl的字段设计。
- **差距**: 没有人在回路——curator更新全自动,没有'谁拍板/推翻条件'的裁决层,也没有判例分级/权威等级;bullet之间没有互相引用关系(是扁平分区列表,不是互链手册),无法表达'结论/when/怎么下/谁拍板/判例引用/执行投影/接口投影'的多字段结构;单一消费场景,不解决多接入面同步分发。
- **来源**: https://arxiv.org/abs/2510.04618 | https://arxiv.org/html/2510.04618 | https://github.com/ace-agent/ace | https://github.com/kayba-ai/agentic-context-engine

## Voyager
- **是什么**: NVIDIA/Caltech 2023年Minecraft开放式agent,核心是'不断增长的技能库',把成功完成的行为存成可执行代码供检索复用。是把判例固化成可执行技能(而非软判断规则)的朴素先例。
- **做法**: 技能库落盘为目录结构:skill/code/<name>.js(Mineflayer API写的JS程序)+skill/description/<name>.txt(对应描述)+skills.json元数据+一个vectordb(GPT-3.5生成描述的embedding做检索索引)。完整checkpoint还含action/chest_memory.json、curriculum/completed_tasks.json、curriculum/failed_tasks.json、curriculum/qa_cache.json及events/日志。三组件:自动课程(出题维持探索)、技能库(存取)、迭代式prompting(带环境反馈/报错/自我验证的改进循环)。检索靠任务描述与技能描述的embedding相似度召回。
- **when表达**: 无显式when字段,靠embedding相似度检索决定技能何时适用,同样是隐式相关性判断。
- **记录回路**: 有运行记录雏形——completed_tasks.json/failed_tasks.json记录任务成败,课程模块据此出下一个任务(轻量回灌),但没有面向文字规则的'偏离手册需记录并回灌修订'机制,因为它没有独立于代码的文字规则手册。
- **本体-接口同步**: 无,单一agent进程消费自己的技能库。README明确只有skill/子目录是'可分享'的部分,提示了运行态私有层与可发布层的边界该在目录结构里显式画出。
- **开源情况**: 官方开源 github.com/MineDojo/Voyager,论文 arXiv:2305.16291,项目页 voyager.minedojo.org,已有EvoSkill/ASDA等衍生工作引用其技能库范式。
- **可抄**: (a) '技能=可执行代码+自然语言描述+embedding索引'三件套持久化格式,可对照我们本体条目的'执行投影'字段——软判断条目理论上也该能挂一个可检索/可执行对象;(b) skill/子目录被设计成唯一可分享部分,提示我们要在目录结构里显式画出'对外发布层 vs 运行态私有层'的边界。
- **差距**: 技能库是纯成功案例的确定性复用,没有反思/退回机制——技能失效不会被标记退役,冲突时也没有裁决层;只解决硬技能怎么做,不解决软判断(when该用、谁拍板),更接近我们'执行管线'层但连声明式when触发元数据都没有。
- **来源**: https://github.com/MineDojo/Voyager | https://arxiv.org/abs/2305.16291 | https://raw.githubusercontent.com/MineDojo/Voyager/main/skill_library/README.md

## Reflexion
- **是什么**: 2023 NeurIPS,用'文字做强化学习'——不更新权重,让agent对失败做自我反思、把反思文本存进episodic memory,下一次trial把反思塞进prompt重跑。是'执行失败回灌到下一次决策'最简化的原型,也是本组里机制最原始的一个,可作对照基线。
- **做法**: 极简:self.reflections: List[str] = [] 纯内存Python list,加上拼接成的self.reflections_str,按episode(单任务多次trial)存在,无跨任务/跨进程持久化格式。ReflexionStrategy枚举定义NONE/LAST_ATTEMPT/REFLEXION/LAST_ATTEMPT_AND_REFLEXION四种策略开关(要不要反思、要不要带上次轨迹)。reflect_prompt模板把上次失败的scratchpad喂给LLM生成一段反思文字,直接前插进下一次trial的prompt。仓库按环境(alfworld_runs/、hotpotqa_runs/等)各自重复实现,没有做成统一格式的通用库。
- **when表达**: 无条目级when,只有'要不要开反思'的策略开关。
- **记录回路**: 有回合内雏形回路(失败→反思→下次trial采纳),但非持久化、非结构化,反思文本只是自然语言段落拼接,没有id/计数器/去重,理论上可无限增长且无退役机制——正是ACE论文点名的context collapse问题的前身。
- **本体-接口同步**: 无,单进程单agent消费自己的memory。
- **开源情况**: 官方开源 github.com/noahshinn/reflexion,论文 arXiv:2303.11366(NeurIPS 2023)。仓库偏论文复现代码,各benchmark目录重复度高,非可复用库。
- **可抄**: 策略开关(LAST_ATTEMPT vs REFLEXION vs 两者都用)提醒我们'带原始案例'和'带提炼后教训'是两个可独立调节的维度,我们的判例引用与手册结论字段也可以显式区分'引原文'还是'引结论'。
- **差距**: 本组最原始的一个——无结构化存储、无多条目组织、无去重/合并、无人在回路、无版本化,基本只是给prompt加一段话。唯一价值是作为最简对照组,衬出ACE等后续工作真正解决了什么(退化、去重、增量更新)。
- **来源**: https://arxiv.org/abs/2303.11366 | https://github.com/noahshinn/reflexion | https://raw.githubusercontent.com/noahshinn/reflexion/main/hotpotqa_runs/agents.py

## Agent Workflow Memory (AWM)
- **是什么**: 2024 CMU论文,让web navigation agent从成功轨迹里'归纳'出可复用高层workflow,再选择性回填进prompt指导后续任务,分offline(训练集批量归纳)和online(测试时边跑边归纳)两模式。是'从判例聚类成规则候选'这条回路最直接的先例。
- **做法**: 一个workflow=一段描述(description)+一段步骤序列(actions+observations),用占位符做变量抽象(如把'dry cat food'换成'{product-name}'以泛化)。归纳靠LLM prompt(非确定性算法):把多条任务轨迹丢给LLM,要求'找跨任务重复动作子序列、提炼可复用工作流、变量名抽象化、每个workflow至少2步'。Offline模式一次性批量生成W_offline供测试固定使用;online模式每个测试任务成功后即触发归纳并追加进memory M,下一任务立刻可用(在线滚雪球)。存储上没有向量检索,按网站/域名分组,全部workflow直接塞进system prompt(约每站点7.3-7.4个,靠人工规模控制)。
- **when表达**: 弱——workflow描述是'做什么'的自然语言标题(如Calculate Travel Time and Distance),适用场景靠LLM推理时自行判断,无显式condition字段。
- **记录回路**: online模式是完整的运行→归纳→回灌回路:成功trajectory触发LLM抽象成workflow并追加进memory,下一任务立即可见。但去重全靠prompt里'不要生成重复workflow'的软约束,论文自陈未发现自动合并相似workflow的机制,只用子轨迹重叠率(WebArena仅0.08)做事后评估。
- **本体-接口同步**: 无,workflow库直接是喂给同一agent的prompt片段,无中心本体与多接入面分离设计。
- **开源情况**: 官方开源 github.com/zorazrw/agent-workflow-memory,论文 arXiv:2409.07429。
- **可抄**: (a) 按站点/领域分桶存储、规模人工卡在个位数到十位数,印证我们'规模与接入'字段要显式声明上限以防手册撑爆上下文;(b) 变量抽象化(具体值→占位符)是把判例提炼成可泛化规则的具体技术手段,可直接对照我们'聚类成规则候选人'这一步该怎么做。
- **差距**: 无确定性去重/合并算法(纯靠prompt要求),无人裁决层,workflow间无互相引用或章节层级,online模式也无旧workflow退役机制——是我们'反向固化器聚类候选'这一步的简化实现,完全缺'人裁'和'退役审计'两段。
- **来源**: https://arxiv.org/abs/2409.07429 | https://arxiv.org/html/2409.07429v1 | https://github.com/zorazrw/agent-workflow-memory

## DSPy (BootstrapFewShot / MIPROv2 优化器)
- **是什么**: 斯坦福'声明式LM编程'框架,把写提示词变成写程序+定义指标+编译,优化器(如MIPROv2)自动从训练样例里同时反推'指令文本'和'少样本示例',编译产出可保存/加载的静态程序。是六者中'从判例批量蒸馏成规则'最工程化、最自动化的一个。
- **做法**: MIPROv2三阶段:①Bootstrap少样本——用当前程序在训练集上跑,只保留metric判定正确的输出作为few-shot候选(筛好判例);②指令生成——把训练集特征摘要+程序代码摘要+few-shot示例喂给prompt_model生成候选指令(从案例归纳文字规则);③贝叶斯优化搜索——在指令×示例组合空间搜索,minibatch评估选maximize metric的组合。编译产出optimized_program.save('optimized.json'),json内含instructions与demos两类字段,可load()复用。auto参数仅light/medium/heavy三档全自动,官方文档明确写'移除了用户确认步骤'。GEPA现已作为dspy.GEPA集成,用反思式变异替代贝叶斯搜索。
- **when表达**: 无——指令是整段生成文本,不是条目化的when-then结构,每个module对应一整段prompt。
- **记录回路**: 编译过程是一次性'评估→反推规则→再评估'的批处理循环,不是持续在线的执行偏离回灌;compile产出json后不再自动更新,需重新compile才能吸收新数据,是批量蒸馏而非在线生长的手册。
- **本体-接口同步**: 有明确save/load JSON机制,可理解为把本体(compiled program)序列化成单文件分发给各调用点,调用点load()即拿到同一份指令+示例——是'中心本体同步分发给接入面'问题的一个简单可行解。
- **开源情况**: 官方开源 github.com/stanfordnlp/dspy,Apache-2.0,Stanford NLP出品,生态活跃,文档 dspy.ai。
- **可抄**: (a) '从案例反推指令'的自动化程度值得抄——我们手册'判断怎么下'类条目理论上也能半自动从判例库产出候选文本再人工裁决;(b) 单一json含instructions+demos两类字段+save/load,是接口层直接拿全量序列化产物的范式,可与我们'接口层只存指针'的取舍做对比讨论。
- **差距**: 完全无人在回路(官方明确移除确认步骤),无谁拍板/推翻条件的裁决层;指令是整段黑箱文本,不具备互相引用、判例锚点、执行/接口投影分离等结构;优化目标是产出一份让metric变高的静态程序,不解决运行时持续吸收新判例、防退化的问题。
- **来源**: https://dspy.ai/api/optimizers/MIPROv2/ | https://github.com/stanfordnlp/dspy/blob/main/docs/docs/api/optimizers/MIPROv2.md

## GEPA (Reflective Prompt Evolution)
- **是什么**: 2025论文+开源框架(ICLR 2026 Oral,现已集成进DSPy),用自然语言反思+多目标(Pareto)进化搜索取代强化学习梯度来优化提示词/代码/agent系统里的文本组件,号称样本效率比GRPO高一个数量级以上。是'用执行轨迹的自然语言反思驱动规则演化'做得最系统的一个。
- **做法**: 每轮从候选池选一个父代候选,在训练集minibatch(默认3条)上跑,记录轨迹(reasoning chain、tool call)和评估器给出的可操作旁信息(如编译器报错等诊断文本,统称feedback_text),LLM读反思后诊断'哪个提示元素导致成败'、隐式做信用分配,生成变异后新候选。核心数据结构是按训练样本维护的Pareto frontier:对每个样本记录当前候选池里谁得分最高,取'至少在一个样本上最优'的候选集合、剔除被严格支配者,按'在Pareto集中命中次数'分配被选中做下轮父代的概率——用样本级多目标前沿代替单一全局最优以避免贪心局部最优。还支持system-aware merge:直接合并两个在不同任务上各有所长的Pareto最优候选取长补短。
- **when表达**: 无条目级when字段,候选整体是一段完整提示词/代码文本,靠'在哪些训练样本上表现最优'间接刻画适用范围,非文字显式判断准则。
- **记录回路**: 六者中回路最完整:rollout产出反馈文本→LLM反思生成新候选→Pareto前沿判定去留→(可选)与另一候选merge。但候选池落盘/日志细节论文与仓库均未详述。无显式退役审计概念,前沿会自然淘汰被严格支配的候选,相当于自动化的退役。
- **本体-接口同步**: 无中心本体/多接入面同步设计,最终选出的最优或合并候选直接当prompt/代码给下游单点消费。
- **开源情况**: 官方开源 github.com/gepa-ai/gepa,MIT license,约5.6k star/458 fork/42 releases,Cerebras Research联合UC Berkeley/Stanford/Databricks/MIT团队维护,活跃;论文 arXiv:2507.19457。有技能学习博客案例:在两个仓库上把resolve率从24%/55%提到93%/82%,迁移到Claude Code减少47%耗时。
- **可抄**: (a) Pareto前沿'多支候选并存、按样本级最优决定去留'比单一贪心手册更能保留'在特殊场景下才最优'的少数条目,提示我们判例引用部分该允许'局部最优但不代表该被全局删除'的条目;(b) system-aware merge是人裁之外的自动整合手段,可作为聚类候选环节'多规则怎么合并'的技术参考;(c) feedback_text把评估器内部诊断(如编译器报错)也当反思素材,提醒运行记录不能只存成功/失败布尔值,要留过程性诊断信息供回灌。
- **差距**: 全自动、无人在回路,候选间无引用关系也无权威等级,是整段文本的进化群体而非互相引用的手册;不解决谁拍板/推翻条件的治理问题,也无条目级结构化字段(结论/when/怎么下分离),本质仍是优化一段黑箱文本而非维护可读文档。
- **来源**: https://arxiv.org/abs/2507.19457 | https://arxiv.org/html/2507.19457v1 | https://github.com/gepa-ai/gepa | https://gepa-ai.github.io/gepa/

### 结论
- 回灌机制复杂度从Reflexion(纯内存文本拼接,无结构)→AWM(LLM归纳但无确定性去重)→ACE(条目化+计数器+确定性合并+diff日志)→GEPA(多目标Pareto前沿+自动merge)依次进化,但没有一个做到我们要的'人裁+权威等级+条目互相引用'的手册结构;ACE的bullet列表最接近却仍是扁平分区列表,不支持条目间引用和多字段(when/谁拍板/判例引用分离)。
- 六个项目全部没有人在回路的裁决层——DSPy文档甚至明确写'移除了用户确认';退化防护普遍靠确定性/统计手段(ACE的embedding去重+计数器、GEPA的Pareto支配剔除)而非人工审核。这说明'人裁必经'是我们设计相对业界最大的差异化,需要重点自证其价值而非只是自然而然的选项。
- ACE的curator_operations_diff.jsonl(每次delta留痕)、AWM的online即时回灌、GEPA的feedback_text(留存过程性诊断而非只留成功/失败布尔值)三者共同证明:回灌回路该留的是'每次变更的结构化diff+触发它的原始执行证据',这正好对应我们判例层jsonl的设计方向,可直接借鉴其diff字段设计。
- 没有一个项目解决'中心本体→多接入面同步分发'的问题(六个全是单点消费);唯一沾边的是DSPy的save/load单文件JSON机制(全量序列化下发),可作为我们'接口层只存指针、本体单独维护'方案之外的对比参照,但都不是我们'hook/skill/CLAUDE.md多接入面同步一份本体'的场景。
- ACE明确命名并可检测的两种失败模式(context collapse突降、brevity bias同质化)可直接借用做我们'退役审计'的检测指标,例如定期统计手册条目是否变短/变同质、是否长期未被引用。
- AWM的'变量抽象化(具体值→占位符)'和GEPA的'system-aware merge(合并两个各有所长的候选)'是'从新判例聚类成规则候选人'这一步可抄的两个具体算法动作:先做值抽象泛化,再做候选合并,而不是只靠LLM一次性总结完事。


# 完备性批评发现的缺口
- **判例推理(CBR)学科本体 + AI&Law 判例法机制 —— 对照“判例库/晋升/退役/谁拍板与推翻条件”** — 整个设计自称“判例库”,但已覆盖的8类里没有一类碰到 Case-Based Reasoning 这个30年的形式化学科,以及 AI&Law 的判例法机制——而这恰好是设计里被判为“业界空白、得自己扎实做”的三块的现成理论骨架。CBR 的 retrieve/reuse/revise/RETAIN 四步循环给“聚类相似判例→晋升成规则”一个成熟形式模型;case-base maintenance(冗余/过时案例的删除与遗忘策略)直接对应“退役”回路——ACE 的 collapse/brevity 只是统计信号,CBR 有整套 competence-based 保留/删除判据;更关键的是 AI&Law 的判例法把“谁拍板与推翻条件”形式化了:binding vs persuasive precedent(约束性 vs 参考性判例)、overruling(推翻)、distinguishing(区分适用)、stare decisis、factor/dimension-based reasoning(HYPO/CATO/ANGELIC)——这是所有已覆盖产品都缺、被反复标为“我们独有”的字段的直接现成来源,会把‘谁拍板/推翻条件字段自己摸索’这条结论改成‘有成熟法学形式模型可直接映射’。2024-2025 已有 LLM+CBR 混合(DS-Agent、GLARE 法律判决、NS-LCR 可解释判例检索)证明它在 LLM 时代是活跃方向不是故纸堆。
- **弱监督/程序化标注(Snorkel 系)+ 人在回路标注反馈台(Argilla/Label Studio/Rubrix) —— 对照“反向固化器:偏离聚类→规则候选→人裁→回灌”** — 设计里被明确标为“没有现成实现可抄、聚类→候选→人裁得自己扎实做”的那一段,其实和弱监督/程序化标注的工作流结构同构,而8类里完全没覆盖它。Snorkel 的 labeling function 就是“被文字化/代码化的软判断规则”,label model 把多条噪声规则去噪合并,而 Snorkel Flow 的 error-analysis 面板(Clarity Matrix)会定位“模型判错是因为哪条 LF 判错”,并直接 suggest 精修既有 LF / 新增 LF——这就是“偏离→定位→生成规则候选”被产品化的现成样板,填了设计里被判为真空的一步,会把‘聚类→候选无先例可抄’这条结论改成‘Snorkel Flow 已有可抄的错误分析→规则候选闭环’。配套的 Argilla/Rubrix/Label Studio 提供“候选先给人看、人来点接受/拒绝”的人在回路面板范式,直接对应人裁门。距离设计比 category6(观测/评测,只测量不合成规则)近得多,是不同的传统。
- **GitOps/配置管理的漂移检测与调和回路(ArgoCD/Flux/Terraform/Puppet drift) —— 对照“偏离检测/偏离回灌”被判为独有的那块** — category1 的结论断言“全行业没一家做语义级漂移检测,这是我们唯一真正独有的部分”。这个结论需要被 GitOps/IaC 的漂移检测调和回路实质修正:ArgoCD/Flux 做的正是“声明的期望态(Git)持续对比实际观测态(集群),语义 diff、判定漂移、自愈或告警”——这是工业界最成熟、已标准化的“源头声明 vs 现实执行持续对账”机制,尽管是结构态而非语义态。关键可借的机制:continuous reconciliation(不是事后翻日志而是持续对账)、server-side dry-run diff、drift 作为一等状态透出并可监控 drift events、以及 ignoreDifferences/管理字段豁免(正是应对“哪些偏离是噪声不该记”的现成方案——设计目前没处理误报)、self-heal vs alert 的处置分档。Terraform plan/state drift、Puppet/Chef configuration drift 是同一传统的更早形态。结论应从‘漂移检测是空白、独有’改成‘结构态漂移检测调和是成熟范式、可整套借调和循环骨架,真正的空白只在语义态这一层’——这是对差异化主张边界的重要收窄。
- **特性开关/渐进交付平台(OpenFeature/Unleash/Flagsmith/GrowthBook/Flipt) —— 对照“接口层中心本体→多接入面同步 + 退役 + 显式确认 + 迅捷返回”** — 跨8类反复出现的结论是“中心源→多投影同步没人解决好,OPA bundle 最接近”。特性开关平台恰恰是这个同步问题的工业级答案且完全没被覆盖:一份中心定义、在成百上千个分布执法点用 SDK 评估、带 targeting rules(when)+ 审计,规模比 OPA bundle 更极端;OpenFeature 是 CNCF 的厂商中立评估标准,直接对应“接口层同步协议”这一设计诉求。它还同时命中另外三根支柱,且各自都比已覆盖产品做得更专:退役——Unleash 给 flag 打类型(release/experiment/operational/kill-switch/permission)配推荐生命周期,超期自动标 potentially stale 供清理,比 category1 的‘Not used recently’面板更结构化;显式确认——change request 4-eyes review 审批把改动当生产迁移一样过合规门;迅捷返回/退役——kill-switch 作为一等开关类型即时下线。会把‘接口层同步只有 OPA bundle 可抄、退役只能靠巡检面板’这条结论改成‘特性开关生态在同步协议(OpenFeature)、生命周期退役(flag 类型+推荐时长+stale 检测)、审批门、即时熔断上都有更成熟可抄的范式’。虽与 policy-as-code 相邻,但渐进放量/熔断/flag 生命周期是策略引擎没有的独立维度。


# 类别:判例推理(Case-Based Reasoning)学科本体 与 AI & Law 判例法机制

## Aamodt & Plaza (1994) —— CBR 四步循环(4R)与任务-方法分解框架
- **是什么**: 不是软件产品,是整个 CBR 学科的奠基性方法论论文。把'用旧案例解决新问题并从中学习'形式化为 Retrieve→Reuse→Revise→Retain 四步循环,并把每一步进一步拆成任务-方法层级树(如 Retrieve 拆成 Identify Features / Initially Match / Search / Select)。几乎所有后续 CBR 系统(含法律 CBR)都沿用这套骨架语言。
- **做法**: 案例结构没有统一强制格式,但给出两种经典案例库组织模型:①动态记忆模型(Schank/Kolodner,CYRUS/CASEY系统)——案例库是判别网络,节点是'泛化片段(Generalized Episode)',每个GE含norms(该组案例共有特征)+indices(index name/value对,用于区分子案例)+cases;新案例存入时若与已有案例共享特征就动态生成新的GE节点,索引结构随学习增长。②类别-样例模型(PROTOS,Bareiss/Porter)——样例挂在category下,三种索引:feature links(特征→案例的'提醒')、case links(类别→其典型样例)、difference links(仅一两个特征不同的邻居案例);样例按'典型度'排序。RETAIN阶段落地为 Extract(决定保留什么:描述符/解/解释/求解方法)→Index(决定新索引)→Integrate(调整已有索引权重、按成功/失败反馈强化或弱化索引关联)。
- **when表达**: 无显式when元数据字段;但四步循环的'进入下一步'条件是隐式过程契约(REVISE失败则REPAIR,不然进RETAIN),when直接编码进过程控制里而非独立声明。
- **记录回路**: 有,但只做记录不做人工晋升:RETAIN阶段不论问题成功或失败都必定更新案例库(失败会单独存为failure case供以后预测同类失败),索引权重按结果反馈自动调整,属于纯自动化增量学习闭环,没有'聚类候选人裁'关卡。
- **本体-接口同步**: 无——论文年代的CBR系统多为单体系统,不存在'中心本体+多接入面'概念。
- **开源情况**: 论文本身无代码;文中提到的历史系统(PROTOS曾经University of Texas匿名ftp可取,动态记忆参考实现曾由西北大学Institute of Learning Sciences提供)均为1990年代产物,现已不可考,不构成可用开源资产。
- **可抄**: 四步循环命名法可直接套用命名我们的三条回路(retrieve≈匹配本体条目、reuse≈执行投影套用、revise≈偏离修订、retain≈判例回灌)。更值得抄的是'任务-方法'双层框架——粗粒度过程图给统一心智模型,任务下挂可插拔方法族(如initially-match下挂'follow direct index/search index structure/search general knowledge'三种)描述具体怎么做,正对应我们手册条目'结论(任务)vs判断怎么下(方法族)'的分层。案例结构里'norms(共性沉淀)vs indices(区分性索引)'的区分,对我们'结论'与'接口投影'分层也有直接借鉴意义。
- **差距**: 完全没有'谁拍板/推翻条件'概念——假设案例库是静态可信来源,不处理'新案例的解与旧案例冲突时谁说了算'这种权威判定问题(这是AI&Law分支才补上的)。也没有规模分级/确认门槛,是纯自动化循环,不含人裁决环节。
- **来源**: https://www.iiia.csic.es/~enric/papers/AICom.pdf | https://journals.sagepub.com/doi/10.3233/AIC-1994-7104

## Smyth & Keane (1995) 'Remembering to Forget' —— 竞争力保留式案例库退役算法(Footprint Deletion)
- **是什么**: 案例库维护(退役)算法。解决'案例库越滚越大导致检索变慢(swamping problem),但传统按使用效用删除的策略会误删关键案例、造成能力不可逆下降'的问题,提出用coverage(该案例能解决哪些问题)和reachability(该问题能被哪些案例解决)两个集合,把每个案例分成四类来指导删除顺序。
- **做法**: Coverage(c)={c'∈C: Adaptable(c,c')}, Reachable(c)={c'∈C: Adaptable(c',c)}。四类案例:Pivotal(唯一能覆盖某片问题空间的案例,Reachable(c)-{c}=∅,删除必导致能力不可逆下降,类比'不可退役的关键判例')、Auxiliary(其覆盖被某可达案例真包含,删了无影响)、Spanning(把两个独立案例的覆盖范围连起来,单删通常无影响、但若一端先被删就变关键)、Support(一组提供相同覆盖的案例,单删无影响、整组删光才降能力)。Footprint Deletion按auxiliary>support>spanning>pivotal的固定优先级删除,并给出Learning Update/Deletion Update增量算法(无需每次重算全库)。Footprint-Utility Deletion(FUD)把此分类与传统效用打分(Minton效用公式)结合,同类候选内先删效用低者。实验(不动产估值CBR系统)显示传统随机/效用删除法在swamping limit=25后能力跌到80%~86%,footprint系列始终维持基准100%。
- **记录回路**: 有,专注退役侧的闭环:'运行(案例库增长)→定期重算竞争力分类→按分类删除',并给出增量维护算法避免全量重算成本;但不含晋升/规则抽取。
- **开源情况**: 无开源代码;1995 IJCAI论文,含完整伪代码(Algorithm 1-3),机制描述充分、可直接实现。
- **可抄**: 这套四分类+coverage/reachability定义,是我们'判例库退役回路'目前完全欠缺、可直接照搬的形式判据——不是凭感觉判断'这条老判例还有没有用',而是先算Coverage/Reachability两个集合,再按四类分层判断'删除会不会造成能力(而非仅检索性能)的不可逆损失',这正对应'退役审计'该有的判据,而不是像ACE那样只看collapse/brevity这类表层引用统计。Footprint-Utility的两段式策略(先看结构性不可替代、同类内再看使用频率)也可直接套用为我们退役规则。
- **差距**: 只处理'案例库瘦身/退役',假设案例库内部没有互相冲突的判例(都是同一正确性标准下的历史解),不像法律判例库那样存在'两个判例结论互相矛盾、需要裁决谁赢'的问题;也没有'聚类相似判例晋升成规则'这个方向,纯删除/保留,不产出新的抽象规则。
- **来源**: https://www.ijcai.org/Proceedings/95-1/Papers/050.pdf

## ANGELIC / Prioritised Abstract Dialectical Framework(Al-Abdulkarim, Atkinson, Bench-Capon, 2014;续作 ANGELIC II, ICAIL 2023)
- **是什么**: 把HYPO/CATO/IBP三代经验性法律CBR系统的'要素(factor)推理'用PADF重新形式化为可执行Prolog程序的一套方法论,回答'怎么把判例库里学到的优先级变成可执行规则'。
- **做法**: 三层结构——issue(最高层,来自IBP的'逻辑模型',用AND/OR命题函数连接issue与其子factor)→abstract factor(来自CATO的多层要素层级,如F101 Info-Trade-Secret由F104 Info-Valuable AND F102 Efforts-To-Maintain-Secrecy组成)→base-level factor→fact(叶子,由具体证据支撑,'+'/'-'两极链接)。每个节点的接受条件写成一组Prolog子句,子句排列顺序编码优先级——先给出默认/不确定的接受条件(如两个对立factor都出现时结果unknown),遇到一个'干净'的真实先例(排除被其它并列理由污染的假阳性,论文特别强调需要排查此点)就把该先例结论编码成新的子句优先级、覆盖默认子句、删除unknown分支。这套子句本身即可执行程序,无需额外编译层。
- **when表达**: 有,但二值化嵌入推理规则本体——每个节点的Prolog子句本身就是when(什么条件下issue/factor被判定present/absent/unknown),没有独立元数据字段描述它。
- **开源情况**: 未见开源代码仓库;学术论文(JURIX 2014,续作ANGELIC II ICAIL 2023),Prolog片段以论文正文形式给出、逻辑上可直接转录复现,但未提供可下载工具/仓库。
- **可抄**: 这是本轮调研里'判例晋升成规则'这一步最具体的可抄样板:晋升不是抽象聚类总结,而是明确三段式——①给判断点写默认/不确定接受条件;②遇到干净的真实判例(排除假阳性理由污染);③把判例结论直接编码成Prolog子句优先级、覆盖默认。这给'新判例经聚类成规则候选人裁后进手册'一个可执行的形式化落地方式——规则候选不是模糊文字总结,而是精确的可执行条件,人只需裁决'要不要采纳/这条判例够不够干净'。
- **差距**: 只处理'单一法域内、同位阶判例之间'的要素优先级学习,没有处理跨位阶的binding vs persuasive(法院层级造成的判例效力差异)、也没有显式建模overruling(高级法院明确推翻先前判例)——假设判例库整体一致,新判例只是补全优先级空白,而非互相打架需要选边站。
- **来源**: https://cgi.csc.liv.ac.uk/~katie/jurix14a.pdf | https://www.semanticscholar.org/paper/Angelic-Environment-:-Support-for-the-Construction-Al-Abdulkarim-Atkinson/5c700b55023011837a19ec14a7695c73a6fac755

## Horty & Bench-Capon 的 Factor-based Precedential Constraint('reason model of precedent')与 Prakken (2021) 统一分析
- **是什么**: 把'binding precedent(约束性先例)/distinguishing(区分适用)/自由裁量'这些传统法学概念严格形式化的理论模型。不是软件,是一套证明论式定义,回答'给定一个判例库,新案例的判决结果什么时候在逻辑上被强制(forced),什么时候法官可以自由裁决(free)'。
- **做法**: 每个先例编码为三条规则(此三规则结构见于Prakken&Sartor 1998,ADF论文对其有直接转述):一条'支持原告方全部factor→判原告'的规则,一条对称的被告方规则,一条由该案实际判决结果决定的两规则间优先级。Horty & Bench-Capon (2012)的改进把'规则前件须用胜方全部factor'放宽为'可以是胜方factor集合的真子集(只要仍压倒对方理由)',从而把a fortiori推理(新案例比先例更有利于同一方,结果必然相同)扩展到更广强制范围。判断新案例是否forced:是否存在结果相同的先例、且两者所有差异都不使新案例比该先例更弱;若两种判决结果都与既有判例库保持逻辑一致,则free(对应'区分适用'的合法空间)。Prakken(2021)比较了几种factor/dimension-based模型对forced/free边界的不同刻画,指出factor缺失与factor被显式否定的处理差异会改变约束强度。
- **开源情况**: 纯理论论文,无代码/工具发布。
- **可抄**: 这是'谁拍板与推翻条件'字段最直接的现成理论骨架——把'这条判例对新情况有没有约束力'从主观判断变成可核验的逻辑条件:①判决结果是否与已有判例库整体保持一致(consistency);②若两种结果都一致,则是当值裁决者的自由裁量权(distinguishing的合法空间),若只有一种结果一致,则被强制。可直接映射为我们手册条目'谁拍板与推翻条件'字段的判据模板:先检验新情况与已有判例集合的一致性,一致则按已有优先级自动走,只有真正冲突才升级给人裁决,而非每次都默认要人工介入。
- **差距**: 只处理'同位阶判例之间要不要遵循'这一层,不处理法院层级造成的binding vs persuasive纵向权威结构,也不显式建模overruling这个动作本身——推翻在此框架里只能表现为'案例库不再包含被推翻的先例',而非'谁在什么条件下可以宣布推翻'的显式过程。
- **来源**: https://cgi.csc.liv.ac.uk/~katie/jurix14a.pdf | https://link.springer.com/article/10.1007/s10506-012-9125-8 | https://link.springer.com/article/10.1007/s10506-021-09284-6 | https://philpapers.org/rec/HORAFD

## Case Frames for Statutory Interpretation(Araszkiewicz, JURIX 2024/2025, arxiv 2411.06873)
- **是什么**: 面向大陆法系(成文法解释,而非判例法)场景的'判例记录卡'数据结构+论证图式——把一份判决书里对某法条用语的解释理由结构化记下,供以后的案子引用/挑战。2024年很新的工作,附带一个人工标注的10案例验证数据集。
- **做法**: Case Frame=四元组。Part1 Case Data(五元组:辖区/法院/案号/日期/是否终审)。Part2 Winning Interpretation(七元组:法条文档/该法条特征如所属法律部门与立法目的/被解释用语interpretandum/本案认定事实StateOfAffairs/胜出的解释Interpretans/该解释是内涵式还是外延式/支持该解释所用解释准则Canon,如文义、体系、目的解释)。Part3 Defeated Interpretations(结构同Part2但只需记落败的Interpretans+Canon,可多个)。Part4 Second-order Directive and Context(记录法院用什么元规则裁决几种解释准则谁优先——如'原则上先文义解释,只有重大理由才能偏离去搞体系/目的解释'——以及该元规则在本案适用的具体理由)。基于此重构出'Appeal to a Prior Case'论证图式(前提1=先例内容,前提2=新案例与先例在四个槽位至少共享一个相关元素,结论=可将先例某槽位值搬到新案例),并给出8条批判性问题(CQ1相关性/CQ2区分适用,含CQ2a法律部门、CQ2b条文类型、CQ2c立法目的三个子问题/CQ3反例案/CQ4管辖权/CQ5a-b时效性/CQ6法院层级/CQ7程序瑕疵/CQ8元规则是否有其它平行判例用了不同版本)用来挑战'能不能援引这个先例'这个论证本身。
- **when表达**: 有,体现为Premise 2(Similarity)——新案例与先例须在document/characteristics/interpretandum/事实四个槽位至少共享一个相关元素才能触发援引论证,这就是显式的when条件。
- **记录回路**: 无自动回灌;是人工标注验证(10案例),论文明确把'用ANGELIC II形式化+NLP自动抽取Case Frame字段'列为未来工作,尚未落地成运行系统。
- **开源情况**: 无开源代码/工具发布,仅论文内嵌的10案例标注表格。
- **可抄**: 跟我们'判例=一句话+锚点+决策空间+权威等级'最结构同构的现成设计——尤其胜出理由(Winning)与落败理由(Defeated)并列记录、而非只记结论,直接对应我们判例引用字段应同时存正反两面论证。'Second-order Directive and Context'槽位是标准形态的'谁拍板/怎么下判断'字段模板——不记'谁'这个人,而记'用了哪条元规则+为什么这条元规则在本案适用'。8条批判性问题(尤其CQ2系列分支、CQ3反例案、CQ5时效性)是可直接借用改写成我们'判例是否还能引用/该不该退役'检查单的现成清单,比空想穷举更完备。
- **差距**: 面向'援引说服力先例、不具强制约束力'的大陆法系场景,天然没有binding precedent必须遵守的硬约束——论文Discussion明确说'没有阻止判例库内部不一致的通用禁令,任何律师都可以找理由主张不同结论',这跟我们希望'手册条目要收敛出唯一权威结论'的诉求方向相反:它示范的是高质量记录冲突、允许长期共存多个不一致判例,而非我们想要的收敛机制。
- **来源**: https://arxiv.org/abs/2411.06873

## Review of CBR for LLM Agents(Hatalis et al., arxiv 2504.06943, 2025)+ DS-Agent(ICML 2024,已开源)
- **是什么**: 前者是2025年综述,系统整理'把经典CBR的retrieve/reuse/retain循环套进LLM agent记忆库'这一新方向;后者(DS-Agent)是已开源、真实落地的双阶段CBR+LLM系统,把Kaggle竞赛人类专家笔记本当'判例库',指导LLM自动完成数据科学任务。
- **做法**: 综述把案例形式化为四元组c=(P,S,O,M)(问题/方案/结果/元数据),检索用语义相似度(LLM embedding余弦)+显式特征匹配+结构相似度三路加权融合;retain阶段提出效用函数U=α·新颖度+β·有效性+γ·可泛化性,低于阈值δ则不保留(此为把Smyth&Keane式竞争力保留思想搬进LLM时代的直接尝试;注:该部分细节经小模型转述获得,公式编号未逐句核验原文,可信度中等,但整体机制方向与摘要及综述惯例一致)。DS-Agent(已验证的真实实现)更具体:development阶段把Kaggle排行榜靠前方案收进case bank,完整走CBR循环(检索最相似历史任务方案→LLM改写适配→执行反馈修正);deployment阶段为省算力退化成'直接检索最相似历史方案+LLM小幅改写'的简化CBR,不再跑完整revise循环。
- **记录回路**: 综述层面提出效用阈值式选择性保留,但停留在理论模型;DS-Agent层面无案例库回灌/竞争力重算机制,case bank在development阶段构建完即固定使用。
- **开源情况**: DS-Agent为公开GitHub仓库(guosyjlu/DS-Agent,ICML 2024官方实现),可直接运行;综述论文本身无配套代码。
- **可抄**: DS-Agent'development完整CBR造经验库、deployment退化成轻量检索+改写'的两阶段拆分,直接对应我们'重投入建手册(允许贵)vs轻量快速接口调用(要快)'的分级设计诉求。综述提出的效用保留阈值,再次印证Smyth&Keane式竞争力判据在LLM时代仍是主流回答而非被'最近使用时间'简单替代,可交叉验证我们的退役判据设计。
- **差距**: 两者都停留在'案例=成功经验的检索复用',完全没有触及法律CBR那套'谁拍板/binding vs persuasive/overruling'的权威结构——LLM agent CBR目前是纯效率增强(帮LLM少走弯路),没有'判例互相冲突时谁赢'的问题意识,说明这条新兴方向还没重新发明法律CBR已解决的部分。
- **来源**: https://arxiv.org/abs/2504.06943 | https://github.com/guosyjlu/DS-Agent | https://arxiv.org/abs/2402.17453

## jCOLIBRI / myCBR —— 开源 CBR 工具框架的案例文件格式实证
- **是什么**: 学术界最常用的两个开源CBR系统构建框架(Java),用来看'一个真实、可运行的案例库到底长什么文件格式'。
- **做法**: jCOLIBRI的案例(CBRCase)固定拆成Description(问题的属性集)/Solution(方案属性集)/Result(该案例真实应用后的结果记录,独立于'预期方案'单列)三段;属性(Attribute)分Simple(Name/Type/Weight/局部相似度函数四要素)和Compound(嵌套其它属性形成属性树)两种;案例库通过Connector组件与外部存储解耦(如DataBaseConnector从XML配置文件initFromXmlFile拉取/落盘),内存组织结构(线性表/kd树/case retrieval net)与持久化格式分离,由开发者按检索性能需要自行选择。myCBR走类似但更偏'相似度度量可视化编辑'路线。
- **开源情况**: jCOLIBRI由Universidad Complutense de Madrid GAIA组维护,历史上经校方页面/SourceForge开源分发,如今主站已老旧但仍有第三方镜像/衍生代码可查(如GitHub上的arynchoong/MTech-KE-CBR);myCBR见mycbr-project.net,同样学术开源,两者近年活跃度都很低(工具本身停留在2010年代)。
- **可抄**: 'Description/Solution/Result三段式+Simple/Compound属性+Connector解耦存储格式'是'案例该长什么样'最接地气的工程答案——尤其'Result独立于Solution单列'值得抄:'预期怎么做'和'做完后实际发生了什么'要分开记,对应我们判例记录里'决策空间(怎么判)'要和'运行记录里的实际结果'分层存放,不合并成一条,以便后续核验'这条判例当初的判断对不对'。
- **差距**: 完全是通用领域无关的检索-复用工具,不含任何判例效力层级/谁拍板概念,也没有主动的退役/晋升机制(增删案例都靠开发者手写代码调用,无内置competence模型),活跃度也已很低,更多是历史范式的现成参照,不是能直接接进现代LLM agent管线的活跃项目。
- **来源**: https://www.sciencedirect.com/science/article/pii/S0167642312000664 | https://github.com/arynchoong/MTech-KE-CBR

### 结论
- CBR的四步循环(retrieve/reuse/revise/retain)本身是30年验证过的成熟命名与任务-方法分解框架,我们'匹配本体条目→执行投影套用→偏离修订→判例回灌'的三条回路可以直接对齐这套语言,不必自造术语。
- 退役判据不该只看'多久没被引用'这种表层信号:Smyth&Keane的coverage/reachability四分类(pivotal/auxiliary/spanning/support)+footprint deletion给出一套可直接实现的竞争力保留算法,是我们目前完全欠缺、可以直接照搬的判据和算法。
- '谁拍板与推翻条件'字段在AI&Law里已有30年现成理论骨架:Horty&Bench-Capon的reason model把'新情况是否被先例强制约束'归结为'判决结果是否与既有判例库保持逻辑一致'这一可核验判据——一致则按已有优先级自动走,只有真正冲突才升级人裁决,不必每次都默认要人工介入。
- '判例晋升成规则'最具体的可操作样板来自ANGELIC/PADF:给每个判断点先写默认/不确定接受条件,遇到'干净'的先例(排除被并列理由污染的假阳性)就把该先例结论编码成优先级子句、覆盖默认——这是'聚类相似判例→规则候选→人裁'这条回路一个可执行的具体实现方式,而不是模糊的文字聚类总结。
- 2024年的Case Frame(大陆法系成文法解释)证明'胜出理由/落败理由并列记录+显式元规则(Second-order Directive)槽位+8条批判性问题清单'这套判例记录结构现在仍在被重新发明,字段设计和批判性问题清单可直接借用;但它本身容忍判例库长期存在互相矛盾、不强求收敛,这跟我们想要唯一权威结论的诉求相反,借字段不借这个立场。
- LLM时代的CBR(综述+DS-Agent)目前只关心检索复用效率,完全没有重新发明法律CBR的权威分级/binding-persuasive/推翻机制,这证实了判例库权威结构确实是业界空白,得从法律AI这支理论借,而非等LLM agent memory这条新兴赛道自己长出来。


# 类别:弱监督/程序化标注(Snorkel 系)+ 人在回路标注反馈台(Argilla/Label Studio) —— 对照"反向固化器:偏离聚类→规则候选→人裁→回灌"

## Snorkel(开源库)
- **是什么**: 把专家写的启发式规则程序化转成训练标签的弱监督框架。用户写多条 labeling function(LF),每条对一条样本投出{正类,负类,ABSTAIN弃权}三态标注,LF之间可能互相冲突、准确率未知。Snorkel 用一个不看真实标签的生成模型(label model)依据 LF 之间的一致/不一致模式估计每条 LF 的可靠度,把多条噪声标注加权合并成概率标签,再用这些概率标签训练下游判别模型。出自 Ratner et al., VLDB 2017《Snorkel: Rapid Training Data Creation with Weak Supervision》,是 data programming 范式的首个端到端实现。
- **做法**: `@labeling_function()` 装饰器包一个 Python 函数,输入一条数据、输出 {ABSTAIN(-1)/负类(0)/正类(1)} 三态——LF 本身就是被代码化的软判断规则,when 隐含在函数体的 if 分支里,没有独立自然语言 when 字段。`LFAnalysis(L, lfs).lf_summary(Y=None)` 产出一张表,列 = polarity(打哪个类)/coverage(覆盖多少样本)/overlaps(和别的LF重叠比例)/conflicts(冲突比例)/(给了金标才有)correct/incorrect/emp_acc,是给每条规则做体检的指标集。`LabelModel(cardinality=k).fit(L_train)` 学一个生成模型:把 LF 输出矩阵 L(n_samples×n_lfs)当观测,用 LF 间'在哪些样本上一致/不一致'的相关性结构反推每条 LF 的准确率权重,再 `predict_proba(L)` 给出概率标签,比多数投票更能处理覆盖率不均和类别不平衡两个偏差(论文明确说不建模 LF 间相关性,认为代价大于收益)。
- **when表达**: 隐含在 LF 函数体的 if 分支里,不是独立的自然语言字段,不可读、不可单独追问。
- **记录回路**: 无,批处理无状态。
- **本体-接口同步**: 无,LF 代码本身既是定义也是唯一分发形式。
- **开源情况**: Apache 2.0, github.com/snorkel-team/snorkel。团队主力已转去做闭源商用的 Snorkel Flow,原始开源库自2019年后基本进入维护模式(snorkel-extraction 仓库README明确写'maintenance mode as of Aug 2019'),不再加新功能,但库本身能跑、API文档(snorkel.readthedocs.io)完整。
- **可抄**: LF 的三态输出(证/伪/弃权,不是二元对错)值得直接抄——一条判断没把握时应该弃权而不是硬表态,多条判断合并时才不会互相稀释。LFAnalysis 的 coverage/overlaps/conflicts 三件套可以直接套到手册条目的偏离统计上:一条 when 判断如果 conflicts 高(经常和别的判断打架)或 coverage 极低(几乎不触发),就是该被人裁的候选。
- **差距**: label model 解的是'多条独立弱规则准确率加权合并'这个纯统计问题,前提是有一批(哪怕很小)金标数据估计经验准确率;我们的'偏离事件'本身就是judgment call,没有客观金标可拿来估计哪条判例更可信——它解决'噪声共识怎么去噪',不解决'偏离该不该被采纳进手册'这种权威裁决问题。且 LF 是静态代码,库本身不提供'从运行记录里聚类发现新规则候选'这一步,新增 LF 完全靠人手写(这一步在 Snorkel Flow 产品里才做)。库层面也没有运行记录/决策日志概念,LF 跑一次是无状态批处理,不记'这次为什么触发'。是纯 library,没有'中心本体+多接入面同步'这个层次。
- **来源**: https://github.com/snorkel-team/snorkel | https://snorkel.readthedocs.io/en/master/packages/_autosummary/labeling/snorkel.labeling.LFAnalysis.html | https://dl.acm.org/doi/10.14778/3157794.3157797 | https://www.snorkel.org/use-cases/01-spam-tutorial

## Snorkel Flow(商用闭源产品)
- **是什么**: Snorkel 团队把开源库包装成的企业级平台,核心是把'模型哪里错了→是哪条LF的锅→该怎么改'这条推理链产品化成可点选的 Clarity Matrix + Error Correlations 面板,直接对应'偏离聚类→定位到具体判例→生成规则候选'这一步。
- **做法**: Clarity Matrix 是二维矩阵,'下游模型判断(对/错)'×'LF去噪合并后标签(对/错)'分成几个象限,每格自带可执行建议:模型错+LF也错→左上格'Refine existing LFs'(筛出这批数据,定位拖累的LF);模型错+LF对→中格'Refine model'(标注没问题,该换模型);全部LF弃权+模型没兜住→'Write new LFs'(真空区,得新写)。Error Correlations/LF Error Contributions 表把模型错误样本反查'跟哪些LF的错误最相关',重合度高的LF被点名精修或删除。精修界面还有'一键根据已标注数据建议新LF',系统在标注表上挖掘满足精度阈值(约>85%)的规则模式作为候选推给用户接受/编辑。官方称这套操作为'Analysis: Rinse and repeat'循环。
- **when表达**: 同 Snorkel 仍是代码隐式 when;产品层新增的是'这条 when 该不该存在'的量化判据(象限位置/错误数量/颜色饱和度),而非文字判断标准。
- **记录回路**: 弱化版——每次训练产出可比较的模型版本和指标,但公开文档未描述完整版本对比/决策日志系统;精修/新增LF这个动作本身不强制留痕(为什么精修没有结构化记录字段)。
- **本体-接口同步**: 单一Web平台内闭环,LF编辑器/Analysis面板/Label页面共享同一份LF库和数据集状态,点Clarity Matrix某格直接跳转预筛Label页面,不存在分发到外部系统的同步问题。
- **开源情况**: 闭源商用 SaaS/私有部署(Snorkel AI 公司),核心机制只能从公开文档(docs.snorkel.ai)/博客(snorkel.ai/blog)拼,拿不到源码,部分细节(如85%阈值具体实现)未公开。
- **可抄**: 四象限矩阵是'偏离聚类→规则候选'目前查到的最贴近现成产品样板,可直接套用:把每条偏离事件按'手册条目是否覆盖到'×'管线判断是否与手册一致'分四格,分别对应'该精修手册条目/该换执行实现/该新增手册条目/无需处理'。Error Correlations 的'错误样本反查最相关规则'这个反查方向也可抄:出现偏离时反查最近触发过的手册条目里哪条和这次偏离最相关,作为规则候选起点而不是从零聚类。
- **差距**: 它解决的是有明确对错标准(有下游模型输出和金标可对比)的分类/抽取任务;我们的'偏离'没有客观对错——管线不遵手册不一定是手册错或管线错,可能是手册没预见到的合法例外,四象限依赖'能分对错'这个前提,套到价值判断上会失真。它的'人裁'仍是'编辑/接受一条LF代码'这个无状态UI操作,不产生可追溯的、带权威等级和判例引用的结构化决策记录,和我们判例层要求的字段完全对不上。
- **来源**: https://snorkel.ai/blog/building-better-datasets-with-snorkel-flow-error-analysis/ | https://docs.snorkel.ai/docs/0.96/user-guide/analysis/creating-good-labeling-functions | https://docs.snorkel.ai/docs/0.93/user-guide/best-practices/analysis-rinse-and-repeat/ | https://snorkel.ai/blog/how-does-the-snorkel-flow-label-model-work/

## Argilla
- **是什么**: 给'模型/规则给出的候选标签'配一个人工审阅界面的开源工具。核心概念是给每条待标注记录挂 Suggestion(候选建议,可能来自模型/LF/LLM)和 Response(人工作答),标注员在界面上对每条建议接受/修改/拒绝,产出可直接导出为训练集的结构化数据。2024年被 Hugging Face 收购($10M),现是HF生态下的数据集curation工具。
- **做法**: Record = 若干 Field(展示给人看的原始内容)+ 若干 Question(要标注员回答的问题,如LabelQuestion/RatingQuestion/SpanQuestion)。Suggestion 挂在某个Question上,字段含 value(建议值)、score(置信度)、agent(哪个模型/流程给出的建议)——这是'规则候选'落地结构的现成范式:谁提的、多有把握,都显式记录。Response 是标注员实际作答,嵌套结构 {values: {question_name: {value}}, user_id, inserted_at/updated_at},带 status 字段区分已提交/已弃用等状态(具体取值枚举未在本次查阅到的页面完整列出)。`dataset.records.log()` 是写入入口,建议和人工作答共存在同一条record上,方便算'这条建议被采纳率'。常和同厂 distilabel 配对:distilabel 产候选、Argilla 给人审阅。
- **when表达**: 无——不表达'什么时候该触发这条规则',只呈现'这条数据长啥样+模型给了什么建议',when判断已在上游(生成Suggestion的模型/LF)做完。
- **记录回路**: 有标注级记录(Response的inserted_at/updated_at + Suggestion的agent/score,构成'谁在什么时候对哪条建议做了什么决定'),但不是规则级决策日志,不会自动聚合成'这条规则被拒绝了几次'的统计。
- **本体-接口同步**: 单体架构(argilla-server提供API,前端读写同一份数据),没有'中心本体分发到多接入面'层次,它自己就是唯一接入面。
- **开源情况**: Apache 2.0, github.com/argilla-io/argilla,活跃维护(HF生态下)。
- **可抄**: Suggestion 的 value/score/agent 三元组是'规则候选'该长什么字段的现成范式,可直接抄进判例库的候选记录结构,不用自己发明字段。它把'建议'和'人工作答'分开存但挂在同一条记录上,方便回溯'AI给了什么建议、人最终怎么定',是判例层'判例引用+推翻条件'的最小闭环雏形。
- **差距**: 只管'单条候选的人工审阅',不管'候选从哪来'(候选生成完全靠外部,如distilabel或别的模型),也不做'多条候选聚类合并成一条规则'——这一步在Snorkel Flow里由'一键建议LF'做,Argilla这边完全空缺。它审阅的对象是单条数据的标签,不是'一条可复用的判断规则';没有'这条被接受的建议应反向修订到规则库哪一条'的回灌链路,回灌要使用者自己接手做。
- **来源**: https://github.com/argilla-io/argilla | https://docs.v1.argilla.io/en/v2.2.0/practical_guides/create_update_dataset/suggestions_and_responses.html | https://docs.v1.argilla.io/en/latest/conceptual_guides/data_model.html | https://argilla.io/blog/argilla-joins-hugggingface/

## Label Studio + ML Backend(HumanSignal)
- **是什么**: 开源标注平台,核心机制是标注平台和外部ML后端通过webhook+REST API双向同步:ML后端既给未标注数据预测建议(供标注员参考/一键接受),又在人工标注产生后被触发重新训练,还反过来决定'接下来该优先标哪条'(active learning)。
- **做法**: ML后端是独立Web服务,实现 LabelStudioMLBase 的 `predict(tasks, context)` → 返回预测数组(作为pre-annotation显示在标注界面供接受/改),以及 `fit(event, data)` → 用最新标注(event如ANNOTATION_CREATED/ANNOTATION_UPDATED携带的data)重训/更新模型,内部用 self.set()/self.get() 存取模型版本状态。触发链路:标注员提交/更新标注→按项目Webhooks设置把事件推给ML后端→fit()重训→标注员翻下一条任务时Label Studio再问ML后端要预测(带最新模型版本)→界面刷新。Active Learning用预测置信度给任务排序,分数最低(模型最不确定)的任务排到标注员队列最前面。项目结构:model.py(实现两方法)+Dockerfile/docker-compose.yml(独立容器部署)+_wsgi.py(标准入口),配置走环境变量LABEL_STUDIO_URL/LABEL_STUDIO_API_KEY指回中心。
- **when表达**: 无独立字段,when(该给哪条任务什么预测)隐含在predict()的模型推理里;'该优先处理哪条'这个元层面的when由置信度排序算法显式表达,是可抄的部分。
- **记录回路**: 半有——self.set()/self.get()持久化模型版本状态,配合项目Webhooks配置可查,但停留在'模型版本+性能指标'层面,不产出面向人的决策日志。
- **本体-接口同步**: 中心(Label Studio服务)和接入面(ML后端容器)间用环境变量声明地址+webhook单向事件推送,ML后端可插拔、可多个,同步机制简单直接(HTTP回调),细粒度控制体现在'配置在项目Webhooks设置里,可关可开可选事件类型'。
- **开源情况**: Apache 2.0, github.com/HumanSignal/label-studio(原heartexlabs),活跃维护,商业公司HumanSignal做企业版加纲。
- **可抄**: fit()/predict()两方法+webhook触发这套最小接口适合抄到偏离回灌场景——手册修订(fit,吃新判例更新规则)和when判断(predict,给执行管线建议)拆成两个独立可测试动作;webhook触发重训而非定时轮询,保证回灌是事件驱动、低延迟的,可作为'高优先级偏离'快速通道补充进三条回路。不确定性排序(优先处理模型最没把握的任务)思路也能直接套:偏离候选按'多条判例互相冲突程度'排序,优先给人裁,而非先来后到。
- **差距**: 它的'训练'是纯统计模型参数更新,fit()里发生什么是黑盒(ML后端自己实现,平台不关心),不产出可读的规则变更说明;我们要的是可读手册条目修订(结论/when/判据文字变化),不是权重张量变化,回灌产物形态完全不同——它证明'事件驱动回灌'骨架可行,但内容层(把偏离变成人话规则)完全不管。active learning只优化标注效率(选哪条该标),不做规则聚类/去重/合并成候选这一步。
- **来源**: https://labelstud.io/guide/ml | https://docs.humansignal.com/guide/active_learning | https://labelstud.io/guide/ml_create | https://github.com/HumanSignal/label-studio

## distilabel
- **是什么**: Argilla同厂开源框架,用LLM批量把'生成候选标注/偏好数据'这一步自动化、声明式流水线化,常见用法是LLM读一条原始数据后按prompt模板产出打分/标签/改写,一行代码把结果推给Argilla供人审阅——是Argilla候选来源的一种,填补'候选生成不靠聚类而靠LLM直接生成'这条路径。
- **做法**: 声明式Pipeline(steps串联,每个step是一个LLM调用+prompt模板,可配置多个候选LLM做交叉验证),产出直接是Argilla可消费的Suggestion格式,一行集成推送。
- **when表达**: 无。
- **记录回路**: 无,纯批处理流水线,不追踪谁用过哪版prompt产出了哪条候选的决策历史,除非外部自己记。
- **本体-接口同步**: 无独立同步机制,通过Argilla API单向推送候选。
- **开源情况**: Apache 2.0, github.com/argilla-io/distilabel,活跃维护(HF生态下)。
- **可抄**: 候选生成不一定要靠聚类算法从历史记录里挖规则,也可以直接用LLM读一批偏离记录+现有手册条目,提示词产出'候选修订'草稿,这比自己写聚类/统计发现规则轻得多,可作为反向固化器聚类步骤的替代或前置补充方案:先LLM生成候选修订草稿,再走Argilla式人审UI。
- **差距**: 生成质量完全依赖prompt和LLM能力,没有Snorkel那种'多条独立信号统计去噪'的可靠性保证;本身不做'多条候选合并成一条'的去重/聚类,只是候选的另一种来源,聚类/去重这一步仍要另外做。
- **来源**: https://github.com/argilla-io/distilabel | https://github.com/argilla-io/distilabel/blob/main/README.md

### 结论
- Snorkel Flow 的 Clarity Matrix 四象限(规则错/模型错/规则真空/都对)是'偏离聚类→定位到具体判例→生成候选'目前查到的最贴近产品化样板,可直接借四象限分类法套到'偏离该精修手册/该换实现/该新增条目/无需处理',但它的适用前提是能分清客观对错的分类型任务,不能直接套到无客观对错的'合规vs合法例外'判断上——这条结论要从'完全无先例可抄'改成'有可抄样板,但要改造分类前提'。
- Argilla 的 Suggestion 三元组(value候选值/score置信度/agent来源)是'规则候选'该长什么字段的现成范式,可以直接抄进判例库的候选记录结构,不用自己发明。
- Snorkel(开源库)的 LFAnalysis coverage/overlaps/conflicts 三件套,是给手册条目做健康度体检的现成指标集,能用来找出'该被人裁'的条目(冲突高或几乎不触发的判断)。
- Label Studio 的 fit()/predict() + webhook 是事件驱动回灌的最小骨架,可作为三条回路里'高优先级偏离走快速通道'的实现参考,但它的训练是黑盒权重更新、不产出可读规则说明,回灌的内容层(把偏离写成人话手册条目)仍要我们自己做。
- distilabel 提示:候选生成不必靠统计聚类,也可以用LLM直接从偏离记录+现有手册条目生成候选修订草稿,是比'自己写聚类算法'更轻的替代/前置路径。
- 这整条产品线(Snorkel/Snorkel Flow/Argilla/Label Studio/distilabel)共同的空白是:没有一个产品把'人裁通过的候选'写回成结构化、带权威等级、可判例引用的可读手册条目——它们的终点都是训练数据或模型权重,不是文档化的规则;'聚类/生成候选'和'错误定位'和'人审UI'这三段各有成熟样板可抄,但'裁决后回灌进语义手册'这一步产品化空白依然存在,仍需自己扎实做。


# 类别:GitOps / IaC 配置漂移检测与调和回路(Configuration Drift Detection & Reconciliation Loop)——用于修正"语义级漂移检测是我们唯一独有部分"这一结论的边界

## ArgoCD(Kubernetes GitOps CD,CNCF毕业项目)
- **是什么**: 持续把 Git 里声明的期望 K8s 清单与集群实际 live state 做结构化 diff,判定 OutOfSync/Synced,可选自动纠正(self-heal)。是当前 GitOps 阵营里"持续对账"实现最成熟的一个。
- **做法**: Application CRD 声明 source(git repo/path/revision)+destination。Application Controller 默认每 120s(+最多60s抖动,约3分钟)从 repo-server 取渲染后清单,与 API server live state 做 normalized diff,同时靠 K8s watch/informer 做事件驱动的即时感知。忽略规则写在 spec.ignoreDifferences 数组,按 group/kind/name/namespace 定位,支持三种匹配方式:jsonPointers(JSON Pointer,'/'转义为'~1')、jqPathExpressions(jq表达式匹配列表内容)、managedFieldsManagers(按 server-side-apply 字段管理者豁免,如忽略 kube-controller-manager 写回的字段);也可在 argocd-cm ConfigMap 里按资源类型全局配置。sync policy 的 automated.selfHeal=true 打开后,检测到 OutOfSync 会在 self-heal-timeout(默认5秒)后自动重新 apply 纠正;不开则停在 OutOfSync 等人工点 Sync。RespectIgnoreDifferences=true 这个 syncOption 让忽略规则在 apply 阶段也生效,而不只是计算 diff 时生效。
- **when表达**: 无语义 when。触发条件全是结构性的:轮询间隔(timeout.reconciliation)+ API watch 事件 + 可选 webhook;ignoreDifferences 的'何时忽略'用字段路径/管理者硬编码表达,不是自然语言判断。
- **记录回路**: 无回灌回路。drift 被发现后要么被 self-heal 直接抹掉(不留决策记录),要么停在 OutOfSync 等人工 Sync,没有'这次偏离该不该固化成新规则'的沉淀环节。
- **本体-接口同步**: Git 是唯一 source of truth,Application Controller 是唯一执行面,不存在多接入面同步问题——这正是 GitOps 的核心主张(one true source),但和我们"手册中心+多接口面同步"的问题形态不同,不能直接对应。
- **开源情况**: 开源,Apache-2.0,argoproj/argo-cd,CNCF 毕业项目,长期活跃维护,官方文档 argo-cd.readthedocs.io。
- **可抄**: ignoreDifferences 三种匹配方式(字段路径/内容表达式/字段所有者)是现成的"哪些偏离算噪声不该记"分类法,可直接映射我们本体条目要补的漂移豁免声明;automated.selfHeal 开/关这个二档开关对应"谁拍板:自动执行 vs 停下等人确认"。
- **差距**: 比的是结构化字段级 diff,完全不理解偏离的业务含义——豁免规则要人工穷举字段路径,没有从历史偏离案例反向归纳'这个字段该不该转正为规则'的机制;偏离只落在 Application 的 status/conditions 和 K8s Events 里,没有决策空间、权威等级、判例引用这些结构。
- **来源**: https://argo-cd.readthedocs.io/en/stable/user-guide/diffing/ | https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/

## Flux CD(kustomize-controller,CNCF毕业项目)
- **是什么**: 另一路 GitOps CD 实现,核心差异是每次 reconcile 用 server-side apply dry-run 直接算出漂移并默认自动纠正,而不是先展示等 selfHeal 开关。
- **做法**: Kustomization CRD:spec.interval(定时,最小60s,常见10m)+spec.sourceRef(Git/OCI/Helm 源)+spec.path。每次 reconcile(定时触发+源变更事件触发)kustomize-controller 对渲染后资源做一次 server-side apply dry-run,把 dry-run 结果与集群 live 对象比较得到漂移,默认直接 apply 纠正(drift correction 默认开启,无需像 ArgoCD 那样显式开 selfHeal)。spec.ignore 数组按 target(kind/name/namespace/group/version/labelSelector/annotationSelector)+paths(JSON Pointer)排除字段,不参与漂移判定与纠正。spec.force(全局或按资源 annotation kustomize.toolkit.fluxcd.io/force)让不可变字段变更时强制重建。controller 在 .status.inventory 里记录自己成功 apply 过的全部资源引用清单,作为 GC(prune)和后续漂移比对的基准。deletionPolicy 控制被移出 Git 的资源怎么处理:MirrorPrune(默认)/Delete/WaitForTermination/Orphan。Status conditions(Ready True/False,reason=ReconciliationSucceeded/Failed)+ K8s Events 记录每次 reconcile 结果与耗时。
- **when表达**: 无语义 when,一律结构化:interval 定时 + source 变更事件 + spec.ignore 的字段路径匹配。
- **记录回路**: 无。drift 被发现即被纠正或按 deletionPolicy 处置,没有留痕后反向修订'期望态声明本身该不该改'的判例回路;status conditions/events 是操作日志而非决策记录。
- **本体-接口同步**: 同 ArgoCD,Git 单一源,controller 单一执行面。
- **开源情况**: 开源,Apache-2.0,fluxcd/flux2,CNCF 毕业项目,活跃,官方文档 fluxcd.io。
- **可抄**: 'drift correction 默认开启不需要额外开关'这个默认值哲学;inventory(已应用对象清单)作为漂移比对基准的做法,对应我们"管线运行历史"需要的'这次实际生效条目清单';deletionPolicy 四态分档(镜像/删除/等待/孤儿)是"偏离处置怎么分级"的具体范例,可参照设计我们的处置分档。
- **差距**: 同 ArgoCD,是结构态字段级 diff,没有语义判断层;ignore 规则同样靠人工穷举字段路径,没有'这个字段总被忽略,该不该转正为规则候选'的自动归纳机制。
- **来源**: https://fluxcd.io/flux/components/kustomize/kustomizations/

## Terraform(HashiCorp,plan/state drift 检测)
- **是什么**: IaC 工具里最主流的 drift 检测方式,但是被动、on-demand 的——不是常驻服务,要用户主动跑一次命令才检测得到,是与 ArgoCD/Flux 持续 watch 架构最大的对照组。
- **做法**: state 文件(JSON)缓存 Terraform 管理的每个资源上一次已知的实际属性,即'观测态缓存'。terraform plan(或显式 -refresh-only)时对每个受管资源调用 provider 的 Read API 在内存中刷新 state(不落盘,除非 apply -refresh-only),再把刷新后的 state 与 .tf 配置声明的期望值 diff,输出 +/-/~ 三态变更列表。旧的 terraform refresh 子命令已废弃,官方现推荐用 -refresh-only 把'只报漂移不提议变更'和'plan 打算真改资源'两条路径显式区分开。发现漂移后处置三选一全靠人工:terraform apply(把现实拉回配置声明)/ terraform apply -refresh-only + 手改 .tf(接受现实、把声明改成现实)/ terraform import(把野生资源纳管)——没有自动 remediation。企业版 Terraform Cloud/Enterprise 在此之上加了后台定时调度的 health checks,开源核心本身不含。
- **when表达**: 无,完全由用户手动触发命令决定何时检查。
- **记录回路**: 无持久记录(除非用户自己接 CI 落盘)。
- **本体-接口同步**: 无接入面概念,CLI 单点使用。
- **开源情况**: 核心 hashicorp/terraform 现为 BUSL(2023年从MPL改),OpenTofu(Linux Foundation 托管的社区 fork)在 MPL 下延续开放许可路线。
- **可抄**: 'plan(会提议变更) vs plan -refresh-only(只报告不提议)'这种命令级读写分离,是把"记录偏离"和"处置偏离"拆成两个不可混淆动作的现成范例,可以直接借来约束我们的回灌回路不能一步到位改本体;state 文件作为'上次已知观测态的显式缓存,按需刷新而非每次重新推导'这个数据结构本身也值得参考。
- **差距**: 完全被动、无持续监控,两次 plan 之间的漂移完全不可见;没有'这条漂移该记为噪声还是异常'的语义分类字段;没有决策记录或回灌机制,plan 输出是一次性文本,不落任何历史。
- **来源**: https://developer.hashicorp.com/terraform/tutorials/state/resource-drift | https://www.hashicorp.com/en/blog/detecting-and-managing-drift-with-terraform

## Puppet(及同类 Chef)配置管理收敛回路
- **是什么**: 比 GitOps 更老的传统:agent 定期从 master 拉取期望态 catalog 并本地幂等收敛,漂移靠'下一次 run 自动覆盖'而非'检测后决策',是离散轮询版的调和循环。
- **做法**: manifest(声明式 DSL)描述 resource 期望态,master 编译成该节点专属的 catalog。agent 是 pull 模式,默认 runinterval=1800秒(30分钟)主动向 master 请求新 catalog 并本地应用:应用时逐个检查 resource 当前状态,已达期望态则跳过(no-op),未达则执行纠正动作——这就是 idempotency 的字面实现,没有'diff 展示+人工确认'这一步,是全自动纠正。漂移存在的时间窗口就是两次 run 之间(默认最长30分钟)。Puppet 有 noop 模式(puppet agent --noop,只报告不执行)对应'只告警不纠正'档位;report 系统把每次 run 的资源级变更写入 report(可存 PuppetDB),其中 corrective_change 字段专门标记'这次改动是因为 catalog 声明变了,还是因为发现了漂移才纠正'。
- **when表达**: 无语义 when,固定轮询间隔(runinterval,可配置)。
- **记录回路**: 有运行记录(PuppetDB report,含 corrective_change 标记),但没有反向修订 manifest 的机制——是纯执行日志,不是决策库。
- **本体-接口同步**: master 单一源,agent 是唯一接入面,pull 模式。
- **开源情况**: Puppet 开源 Apache-2.0(puppetlabs/puppet,Perforce/Puppet 商业化维护,增长放缓但仍在维护);Chef 定位类似(cookbook/resource,chef-client pull 循环),核心 Apache-2.0,现属 Progress Chef。
- **可抄**: corrective_change 标记是现成设计:区分'这条变更是主动声明的'还是'是被动纠偏的',直接对应我们要不要给回灌回路里的偏离打显式标记、别和正常需求变更混在一起统计;noop 模式(报告不执行)是最小侵入试运行档位,值得作为新规则上管线前的默认姿态。
- **差距**: 发现偏离就自动抹掉,没有中间人工裁决环节,更谈不上把偏离喂回去修订 manifest 本身;30分钟离散轮询窗口连'持续对账'都算不上,是定时批量对账,比 ArgoCD/Flux 的近实时 watch 更粗;完全没有语义层,resource 类型和期望态都是运维预先写死的。
- **来源**: https://www.puppet.com/docs/puppet/7/services_agent_unix.html | https://help.puppet.com/pe/2023.8/topics/understanding_idempotency.htm

## AWS CloudFormation Drift Detection
- **是什么**: 云厂商原生的 on-demand 漂移检测,请求式扫描一次 stack,把当前实际属性和模板声明属性逐字段对比,不做任何自动纠正,是四款里最'轻'的一个实现。
- **做法**: 调用 DetectStackDrift API(或控制台/CLI)后,CloudFormation 对 stack 中'支持漂移检测'的每个资源类型分别查询云端实际属性,与模板+参数算出的期望属性逐字段比较;只检查模板里显式声明过的属性,未声明字段的变化完全不参与比较。操作异步:先 DetectStackDrift 发起,DescribeStackDriftDetectionStatus 轮询进度,完成后 DescribeStackResourceDrifts 取详细结果。每个资源被分类为 IN_SYNC / MODIFIED / DELETED / NOT_CHECKED(资源类型不支持检测)。无内建自动 remediation;AWS 官方给的'自动修复'方案是额外接 CloudWatch Events + Lambda 监听漂移事件再触发处理,厂商本身只给检测原语。
- **when表达**: 无,纯手动/API 触发。
- **记录回路**: 无持久决策记录,一次检测结果是只读快照,不反向修订模板。
- **本体-接口同步**: 无接入面概念。
- **开源情况**: 闭源(AWS 托管服务),机制通过官方文档(docs.aws.amazon.com)和 CLI 参考公开。
- **可抄**: '只检查模板里显式声明过的属性'这条默认收窄规则,是应对噪声最省心的方案——没写进声明的字段变化天然不算漂移,不需要额外维护忽略名单,比 ArgoCD/Flux 的手动 ignoreDifferences 列表更轻;NOT_CHECKED 这个'能力不足以判定'的显式第三态也值得借,避免把'没法检测'误报成'没有漂移'。
- **差距**: 纯 on-demand、无持续监控、无自动处置,只回答'现在有没有漂移',不解决'漂移了然后怎么处置';检测覆盖面受限于资源类型是否'支持漂移检测',很多类型直接 NOT_CHECKED,覆盖不完整。
- **来源**: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-stack-drift.html | https://docs.aws.amazon.com/cli/latest/reference/cloudformation/detect-stack-drift.html

## Kubernetes controller-runtime / Operator Pattern(通用调和循环原语)
- **是什么**: 不是产品,是 ArgoCD/Flux 等所有 K8s 控制器共用的底层编程模型——把'持续对比期望与观测、驱动收敛'抽象成一个可复用的库和一套约定,是理解上面两款为何能做到'持续'的机制根源。
- **做法**: Custom Resource 的 spec 字段代表期望态(用户声明的意图),status 字段代表观测态(controller 自己写回的'我现在观测到的现实')。controller-runtime 提供 informer(watch API server 变更、维护本地缓存)+ workqueue(把变更事件去重排队)+ Reconciler 接口(用户只需实现一个函数:给定某资源的 namespace/name,读取期望态和实际子资源状态,做必要动作让二者趋同,返回是否需要 requeue)。这个循环是无限循环、幂等、level-triggered(水平触发)而非 edge-triggered(边沿触发)——不管错过多少次中间事件,只要最后一次 reconcile 时该资源的当前完整状态被正确读取,就能收敛到正确结果;并配有定期 resync 作为兜底,防止 watch 漏事件导致永久不一致。
- **when表达**: 无语义 when;触发是'被 watch 对象变更 + 定期 resync 兜底'。
- **记录回路**: 无,机制层不提供记录/回灌,由上层应用(如 ArgoCD)自己决定要不要记。
- **本体-接口同步**: 无。
- **开源情况**: 开源,Apache-2.0,kubernetes-sigs/controller-runtime,Kubernetes 生态核心基础设施,极活跃。
- **可抄**: 'level-triggered 而非 edge-triggered'是最值得抄的一条设计原则——我们的回灌回路如果是'事件驱动、错过一次通知就永久遗漏',就该补成'下一次巡检时重新从当前完整状态推导该做什么',不依赖不丢事件的假设;spec/status 物理分离(期望态用户写、观测态系统写,同一对象不同字段读写权限分开)这个数据结构范式也可直接照搬。
- **差距**: 纯机制层,完全不涉及语义、不涉及'这个偏离该不该被允许'的判断,也不提供记录/回灌,更不含'结构化 diff+忽略规则'这类现成能力(那是 ArgoCD/Flux 在这层之上加的)——它只解决'循环怎么写才不丢状态',不解决'循环该做什么判断'。
- **来源**: https://kubernetes.io/docs/concepts/extend-kubernetes/operator/

## driftctl(Snyk,已进入维护模式)——专用漂移检测工具的兴衰案例
- **是什么**: 曾经是专门做'云资源 vs Terraform state 漂移检测'的独立开源只读扫描工具,2023年6月起进入维护模式、功能并入 Snyk IaC 商业产品,是'漂移检测能不能作为独立产品长期存在'的一个反例样本。
- **做法**: driftctl scan 先枚举云账号(AWS/Azure/GCP/GitHub)里实际存在的所有受支持资源,再解析本地/远端 Terraform state,逐条比较,把结果分三类:unmanaged(云上有、state 没有,可能是手动新建的野生资源)、missing(state 有、云上没有,可能被手动删除)、changed(两边都有但属性对不上的漂移)。这个三分类比 ArgoCD/Flux 二元的 in-sync/out-of-sync 更细,专门服务于'漂移到底是哪种类型'的问询。
- **when表达**: 无,scan 命令手动触发。
- **记录回路**: 无。
- **本体-接口同步**: 无。
- **开源情况**: 原开源(snyk/driftctl),现处于 maintenance mode(2023-06-29 起不再积极开发),功能收编进 Snyk IaC 闭源产品线。
- **可抄**: unmanaged / missing / changed 三分类比二元 in-sync/out-of-sync 更贴近我们需要的'偏离类型'区分(新增未纳管 / 被删除 / 被改动三种要对应完全不同的处置策略),可直接借用做我们判例库的偏离类型枚举字段。
- **差距**: 它的停摆本身是个警示:纯粹'只做漂移检测、不做纠正、不做管理'的独立工具商业上难以独立存活,最终要么被主平台吸收成一个功能模块要么停止维护;同样是结构态而非语义态,没解决'这条漂移的业务含义是什么'。
- **来源**: https://github.com/snyk/driftctl | https://docs.driftctl.com/0.23.0/usage/cmd/scan-usage/

### 结论
- '语义级漂移检测是空白'这条结论需要收窄:结构态漂移检测+持续调和回路早已是成熟工业范式——ArgoCD/Flux 靠常驻 controller 持续 watch+diff+纠正,K8s controller-runtime 提供底层的 level-triggered 调和循环原语,Terraform/CloudFormation 是被动 on-demand 版,Puppet/Chef 是离散轮询版。真正的空白只收窄到'语义层'这一环:没有一家能判断'这条偏离在业务/意图上是否合理、该不该固化成新规则',全部停在字段级/属性级的结构比对。
- 可以整套借的是调和循环的骨架结构,不用重新发明:期望态(spec)与观测态(status)物理分离存储、level-triggered 而非 edge-triggered(不依赖不丢事件,靠定期 resync 兜底)、diff 结果落三态而非二元(IN_SYNC/MODIFIED/NOT_CHECKED 或 unmanaged/missing/changed),这些直接映射我们管线执行历史该怎么记录'实际路径'。
- '哪些偏离是噪声不该记'这个我们目前没处理的误报豁免问题,行业已有多套现成解法可抄:ArgoCD ignoreDifferences(JSON Pointer/JQ表达式/字段管理者三种匹配)、Flux spec.ignore(按 target+paths)、CloudFormation'默认只检查模板显式声明过的属性'(最省心,不用维护忽略名单)——可以设计成本体条目自带的漂移豁免声明字段。
- 处置分档也是现成的,可直接映射我们的'须确认标志'与规模档位:self-heal(自动纠正,ArgoCD/Flux 默认开或可开)vs alert-only(只告警等人工 Sync)vs noop(报告不执行,Puppet --noop 试运行档)三档,加上 Puppet 的 corrective_change 字段(区分这次改动是主动声明变了还是被动纠偏),都可以直接搬进我们的处置元数据设计。
- 记录侧普遍薄弱是这几家共同的短板:ArgoCD/Flux 只留 K8s Events/status conditions,Puppet 留执行 report,Terraform/CloudFormation 甚至默认不落盘——没有一家把'漂移事件'当作可积累、可聚类、可反向产出规则候选的判例库对待。我们的'统一决策库+反向固化器把新判例聚类成规则候选人裁进手册'这套回路,确实是这几家结构态方案都没有的部分,是可以站得住的差异化主张,但边界要明确写成'语义级偏离裁决与本体回灌',而不是笼统的'漂移检测'。
- driftctl 的停摆(2023年起维护模式、并入 Snyk 商业产品)是一个警示:纯粹'只做检测不做管理'的独立漂移工具商业上难以独立存活。提示我们的偏离检测与回灌回路不该做成外挂扫描器,应该焊死在管线执行主干和统一决策库上才有持续维护和使用的理由。


# 类别:特性开关/渐进交付平台(Feature Flag & Progressive Delivery) —— 对照"接口层中心本体→多接入面同步 + 退役 + 显式确认 + 迅捷返回"

## OpenFeature (CNCF) + flagd 参考实现
- **是什么**: CNCF孵化级的厂商中立特性开关求值标准(evaluation API/Provider/Hooks/Events规范)+ 官方参考实现daemon flagd。解决的是'评估接口标准化'与'配置怎么同步到分布式接入点'两件事,不涉及后台管理/审批/退役。
- **做法**: spec分四层:Evaluation API(应用调用入口)→Provider(翻译层,各厂商实现,相当于我们的'执行管线')→Evaluation Context(动态/静态上下文数据)→Hooks(before/after/error/finally四个生命周期钩子,只做校验/日志/遥测不带判断逻辑)→Events(PROVIDER_READY/CONFIGURATION_CHANGED等状态通知)。规范用markdown+RFC2119(MUST/SHOULD/MAY)三级稳定性(Experimental/Hardening/Stable)分级维护在github.com/open-feature/spec的specification/目录。Appendix C定义OFREP协议:单flag求值端点(server端,动态context)+批量求值端点(client端,静态context一次拉全量本地缓存),响应带reason字段(TARGETING_MATCH/DEFAULT/DISABLED/ERROR)。flagd的flag definition JSON顶层结构为{$schema, flags:{flagKey:{state, variants, defaultVariant, targeting}}, $evaluators, metadata},targeting字段用JsonLogic表达when,例如 {"if":[{"ends_with":[{"var":"email"},"@example.com"]},"on","off"]} 或 {"fractional":[{"var":"email"},["red",33],["blue",33],["green",34]]} 做确定性伪随机分流。flagd可同时挂多个sync源(file/http/grpc/gcs/s3/k8s CRD),按'后定义源优先级更高'本地合并,删除事件触发resync重建完整状态——是去中心化拉取+本地合并而非中心推送。K8s场景下OpenFeature Operator用FeatureFlag/FeatureFlagSource CRD把flag定义和flagd sidecar部署耦合。
- **when表达**: targeting字段内联JsonLogic谓词(if/var/ends_with/in/fractional等操作符组合),规则返回variant名或null(fallback到defaultVariant),纯结构化硬判断,不表达'谁拍板/判例引用/推翻条件'这类叙述性内容。
- **记录回路**: 无。Events只做状态变化通知,Hooks可外挂日志/遥测但规范不规定存储或回灌机制;退役/审批/审计完全在spec范围之外,留给各厂商自建。
- **本体-接口同步**: flagd去中心化多源拉取(file/http/grpc/k8s CRD/云存储)+本地按源优先级合并;OFREP协议统一'接入点问中心要答案'的HTTP接口;各厂商(Unleash/GrowthBook等)在此规范之上各自加自己的推送/轮询/流式实现。
- **开源情况**: CNCF incubating项目,spec仓库github.com/open-feature/spec、flagd仓库github.com/open-feature/flagd,均Apache 2.0。官方provider registry收录LaunchDarkly/Flagsmith/Unleash/Split/CloudBees/GO Feature Flag等厂商实现。是标准而非产品本身,持续活跃(2026年5月Cloudflare刚发布基于它的边缘服务Flagship)。
- **可抄**: 1) JsonLogic作为when表达式:结构化、可序列化、能在浏览器/服务端/边缘复用同一份规则——可以给我们执行管线的'when触发元数据'抄一层类似结构化谓词,而不是纯自然语言。2) Hooks的before/after/error/finally范式是'接口层只留最小提醒'的具体落地:钩子不带判断逻辑,判断在Provider(对应我们的执行管线)里,和我们'本体绝不存在接口文件里'的设计同构。3) flagd多源合并+resync容错(源丢失时重建而非报错)值得抄进我们'接口投影同步'的容错设计。4) OFREP的reason字段是轻量'这次为什么这样走'的返回值,可对照我们'本次引用了哪条本体条目'的可视化诉求,但它只在单次响应里带、不落库,是运行时可观测性而非留痕审计。
- **差距**: 完全没有'退役'概念——flag什么时候该删、谁拍板、有没有推翻条件一句未提;没有'运行记录回灌修订本体'的回路;同步是去中心化拉取而非中心推送+确认,四眼审批在这层完全不存在;本体是JsonLogic结构化谓词而非我们要的'手册体软判断',只解决机器可执行硬判断的分发,不解决语义本体的分发。
- **来源**: https://openfeature.dev/docs/reference/intro/ | https://github.com/open-feature/spec | https://openfeature.dev/specification/appendix-c/ | https://flagd.dev/reference/flag-definitions/ | https://flagd.dev/concepts/syncs/ | https://github.com/open-feature/flagd

## Unleash
- **是什么**: 开源(AGPL-3.0,开放核心)特性开关平台。在这几家里'flag生命周期治理'和'变更审批门'做得最结构化:每个flag按类型配推荐存活期,自动打stale标签+五阶段生命周期看板,企业版有change request四眼审批。
- **做法**: Flag Type:release(推荐40天)/experiment(推荐40天)/operational(推荐7天)/kill-switch(无限期)/permission(无限期),admin可在Configure>Feature flag types改每种类型的期望寿命;超期自动打'potentially stale'(不下线只提示),也可手动'Toggle stale state'生成feature-stale-on事件驱动外部自动化(Slack通知/破坏CI/自动开PR删代码)。Lifecycle五阶段Define→Develop→Production→Cleanup→Archived,由metrics上报自动流转(建了没数据→收到非生产metrics→收到生产metrics→标记completed但仍有生产流量提示清理→禁用删代码归档),展示在Project status仪表板的技术债评分里。Change Request(企业版)状态机:draft(仅作者可编辑)→in review(再编辑会撤销已有批准)→approved(达到项目设置的批准数,最多10审批人)→scheduled(可选,批准后调度到未来批量应用,可重调度/立即应用/拒绝但不可编辑或回溯)→applied;'跳过变更请求'权限仅限环境级操作(开关flag),feature级操作(如archive)强制走审批。同步:SDK默认轮询(Node.js 15s),企业版Unleash Edge支持SSE流式(先全量同步再长连接推流),断线自动回退轮询+用最近已知状态继续服务并emit警告事件。
- **开源情况**: github.com/Unleash/unleash,AGPL-3.0(开源核心版),GitHub约1.3万star,是这几家里社区最大的;RBAC/SSO/多环境(超2个)/change request/高级分段等在Pro/Enterprise付费层。
- **可抄**: 1) flag类型→推荐寿命→到期自动'potentially stale',是退役最结构化的落地:创建时就声明'这条判断预期活多久',到期自动进复核队列而非人工巡检发现——可直接抄进我们管线注册表:每条规则候选人打类型(临时实验性/永久熔断/权限类),配到期天数,到期自动标'待复核'而非静默过期。2) 五阶段生命周期由运行时metrics信号自动驱动流转,不是人工填状态——是'运行记录驱动状态'的好例子,可对照我们'规则候选人有没有被执行管线引用过、引用后有没有被推翻'来自动流转状态。3) 'draft阶段再编辑会撤销已有批准'防止审批完偷偷改内容,值得抄进我们的裁决门。4) kill-switch作为独立flag类型且明确'无限期、用于紧急关断',是'迅捷返回/熔断'的产品化先例,可对照定义一种绕开normal审批通道的豁免类型。
- **差距**: Change Request是纯审批门,不记录'为什么批准/驳回'的推理(只有diff+评论),更不会把决策沉淀成新规则反哺其它flag——没有'新判例聚类成规则候选人'这一步。Targeting是UI表单式结构化条件(用户属性/百分比/自定义策略),假设所有when都能化成布尔条件,不处理'交给人判断'这类灰色地带。Stale detection只看超期和用量,不看判断内容有没有被证伪,是时间/用量驱动而非判例驱动的退役。
- **来源**: https://docs.getunleash.io/concepts/change-requests | https://docs.getunleash.io/unleash-edge | https://github.com/Unleash/unleash | https://www.getunleash.io/blog/feature-flag-change-requests-how-to

## Flagsmith
- **是什么**: 开源(BSD-3-Clause)特性开关+远程配置平台。Change request机制把'环境级flag变更'和'项目级segment变更'拆成两条不同粒度的四眼审批线,是'审批粒度按影响范围分层'的例子。
- **做法**: 两种change request:Feature CR(环境级,触发场景是改flag在某环境的默认值/加改segment override,身份级override immediate生效可绕过审批)、Segment CR(项目级,触发场景是编辑已有segment的规则和条件,影响该项目所有环境;新建segment不需要走CR)。流程:创建(标题+描述)→指定/等待审批人(环境或项目设置里配所需审批数)→审批人看当前值vs新值的diff→达到所需批准数→发布(立即生效,生成audit log entry)。权限分层到操作粒度:Feature CR的创建/审批/发布是环境级权限,Segment CR是项目级权限。文档未提及schedule能力(批准即发,不像Unleash有'调度到未来批量应用'的缓冲带)。
- **开源情况**: github.com/Flagsmith/flagsmith,BSD-3-Clause(比Unleash的AGPL更宽松),约6000+ star,change request功能仅限Scale-Up和Enterprise付费计划。
- **可抄**: 按'改动影响半径'分两条审批线(环境级 vs 跨环境的项目级)而非一刀切,直接对应我们'长管线要规模声明+显式确认,小需求迅捷返回'——影响半径可作为要不要走确认门的分级依据,而不只是长短。'新建segment不需要CR但编辑已有segment需要CR'背后的直觉是:新建是可回滚的独立试验,修改已有的会动到所有依赖它的既有flag,这条判断本身可以写进我们手册当一个判例(新增vs修改要不要走同一道确认门)。
- **差距**: 没有退役/生命周期机制,文档完全没提stale flag或推荐寿命(Unleash有,Flagsmith没有),把清理完全留给人工。审批记录只提到audit log entry和segment修订历史,公开文档没给出具体数据结构,不如Unleash公开得细。四眼审批不含schedule到未来批量生效的缓冲带。
- **来源**: https://docs.flagsmith.com/administration-and-security/governance-and-compliance/change-requests | https://www.flagsmith.com/blog/what-is-the-four-eyes-principle | https://github.com/Flagsmith/flagsmith

## GrowthBook
- **是什么**: 开源(MIT)特性开关+实验平台。核心特色是把flag revision当一等公民做完整审计轨迹,并且明确把'人工UI改动'和'AI agent通过MCP/REST改动'塞进同一道审批+审计门——是'人和AI共用同一治理路径'的少数明确表态案例。
- **做法**: Targeting Condition:UI可视化配置或Advanced Mode直接写JSON,语法仿MongoDB查询(如{"country":{"$in":["US","CA"]}})。Rule按上到下顺序求值,第一条匹配的rule生效,都不匹配则用default value。Stale detection命中两类条件之一即标'Stale'列(不自动下线):(a)两周内未更新且所有环境未激活,(b)存在one-sided rule(某条规则把100%流量导向单一variation,即判断已经退化成恒真式)。GrowthBook 4.4起支持Claude Code/Cursor这类AI coding agent通过MCP server/REST API'发现并清理'stale flag。审计:flag revision机制记录每次改动的who/what/when,RBAC+四眼审批工作流+audit log三件套是标配,无论改动来自人工UI还是AI agent调用都走同一审批门+同一份audit log。同步:SDK Connection端点返回全量flag/experiment JSON payload;SDK Webhook在payload变化时通知下游失效自建缓存;JS/React SDK支持SSE Streaming实时推送;可选自建GrowthBook Proxy,多proxy实例间用Redis Pub/Sub广播payload保持集群内一致。
- **开源情况**: github.com/growthbook/growthbook,MIT协议(比Unleash/Flagsmith都更宽松),audit log在所有付费层(含免费层)都有,是这几家里对审计最不设费用门槛的。
- **可抄**: 1) 'AI agent改配置和人改配置走同一道审批+审计门,唯一区别是发起者身份字段'——直接回答了我们未来子agent自动改本体/管线注册表时要不要单独开一套门禁的问题:答案是不开。2) Stale判定加了'one-sided rule'语义信号(分支已退化成恒真判断),不只看时间/流量,对应我们本体里'一条规则如果所有判例都倒向同一结果,该考虑它是否该被吸收成硬判断或直接归档'。3) MongoDB风格JSON+UI表单二级入口(简单场景不用学语法,复杂场景可下钻写代码),值得在我们'手册转结构化判断'那层参考。
- **差距**: Stale detection统一2周规则,不像Unleash那样按类型区分到期时间(不区分kill-switch还是实验),粒度更粗。Rule评估是'first-match-wins'顺序敏感模型,规则间无显式优先级/冲突检测,复杂targeting容易因顺序踩坑,和我们希望的'判断怎么下要可解释'有张力。审计只覆盖'改了什么',不覆盖'为什么这么改/参考了哪条判例',仍是执行层留痕而非语义层留痕。
- **来源**: https://docs.growthbook.io/features/targeting | https://docs.growthbook.io/features/stale-detection | https://docs.growthbook.io/app/webhooks/sdk-webhooks | https://www.growthbook.io/blog/growthbook-4-4 | https://github.com/growthbook/growthbook

## Flipt (v2 Git-native)
- **是什么**: 开源(Apache 2.0)特性开关平台,v2起把flag/segment/rule状态整个搬进Git仓库当YAML声明式配置,让'评审+发布+回滚+审计'直接复用Git已有的PR机制——是本次调研里'中心本体=版本化文档仓库'这个思路和我们设计同构度最高的产品。
- **做法**: storage backend可配置(filesystem/git/oci/s3等,通过sync provider抽象),Git模式配置示例为storage.type=git+repository/ref/poll_interval(如30s),server按轮询间隔watch仓库变化。多环境组织三选一:每个环境一个Git分支、每个环境一个目录、或每个环境一个独立仓库(官方未给出权威取舍指南)。变更走标准PR流程:开分支改YAML→开PR→(可配)要求审批→合并→server下次轮询/webhook感知变更生效,天然获得完整history和blame,不需要另造审计表。支持segment间AND/OR复合targeting,boolean和multivariate两种flag类型,支持OpenFeature标准和OFREP(可作为OpenFeature的一个provider后端)。除Git history外还有独立Audit Events+Webhook,覆盖非Git路径(如UI直接改)的变更留痕。
- **开源情况**: github.com/flipt-io/flipt,Apache 2.0,100%开源无付费墙分级(相比Unleash/Flagsmith的开放核心模式更彻底),Go编写,SQLite/PostgreSQL/MySQL可选后端存储。
- **可抄**: 1) '中心本体=Git仓库声明式YAML,变更=PR,审计=Git history'和我们'语义本体是互相引用的markdown文档'高度同构——证明这套模式在feature flag这种更偏硬判断的领域也能跑通,可以反向印证给本体接一层'变更走PR'流程是可行路径,不用另造审批系统。2) 三种环境组织方式(分支/目录/独立仓库)可直接映射我们思考'手册要不要按接入面/规模档位拆分文件'的备选方案——分支适合同一本体不同发布阶段,目录适合同一本体不同接入面互相可见方便互引,独立仓库适合完全隔离的判断域。3) Git原生支持'先在分支里测试flag改动再合并到生产',对应我们'长管线先沙盒验证再进正式手册'的诉求,而且是免费获得的能力,不用另造沙盒机制。
- **差距**: Git-native是'存储介质'层面的同构,但内容仍是结构化YAML(硬判断),不表达'软判断怎么下、谁拍板、判例引用'这类叙述性内容——本体的文字论证部分它完全没有对应物,只提供了一个足够好的容器。Poll-based sync(如30s)在'多接入面实时同步'上不如flagd的streaming或Unleash Edge的SSE快,若要求发布即生效需额外配webhook补足。三种环境组织方式缺权威取舍指南,是'能力'而非'最佳实践沉淀',判例沉淀部分仍需自己摸索。
- **来源**: https://github.com/flipt-io/flipt | https://docs.flipt.io/v1/reference/openfeature/overview

### 结论
- 'when'在这个领域已工业化成结构化谓词(flagd用JsonLogic、GrowthBook用MongoDB风格JSON),解决的是纯硬判断的分发,和我们手册要的'软判断可文字论证'是两层不同问题——不能直接照搬当本体格式,但可以给管线注册表的when触发元数据抄一个类似的结构化谓词层。
- 退役目前最成熟的做法是Unleash:flag按类型(release/experiment/operational/kill-switch/permission)配推荐存活期,到期自动打'potentially stale'进复核队列,而不是靠巡检面板事后发现;GrowthBook补了'one-sided rule'语义信号(分支已退化成恒真判断)。这两条都比定期巡检更值得抄进我们的判例/规则候选人生命周期。
- 中心源→多接入面同步目前有两种典型范式:flagd式(去中心化拉取+本地多源合并+resync容错,接入点自己决定信什么)与Flipt v2式(Git仓库为唯一真源,PR即审批即发布即留痕,接入面轮询/webhook拉取)。后者和我们'手册是互相引用的markdown文档'这个设定同构度最高,值得优先参考它的PR化流程而非另造审批系统。
- 显式确认门(四眼审批)在这些产品里普遍按'改动影响半径'分级而非笼统的'重要与否':Flagsmith把环境级flag变更和跨环境的segment规则变更拆成两条审批线;Unleash允许环境级开关跳过审批但feature级归档强制审批。提示我们'要不要走确认门'该锚定'改动会波及哪些下游接入面',而不是简单的长/短管线二分。
- GrowthBook明确让'AI agent通过MCP/REST改配置'和'人工UI改配置'走同一道审批+审计门,是调研到的唯一一家表态'人和AI共用同一治理路径'的产品,直接回答了我们未来给自动化回灌开小灶的问题——答案是不开,只加发起者身份字段。
- 没有一家解决'运行记录回灌修订本体'这个回路:Events/Webhook/审计日志全部止步于记录变更和通知下游,没有一家把线上运行数据自动聚类成新targeting规则候选人交人裁决——这仍是我们设计里独有、且这个领域完全空白的一环,值得继续自建而非等着抄。
