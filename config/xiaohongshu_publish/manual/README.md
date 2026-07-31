# 小红书发布操作手册包

本目录只保存当前有效的操作规范。写作 Agent 每次运行都完整读取，不依赖此前会话。

中文作者总规则：`config/publication_common/production-copy-authorship.md`。小红书标题、正文和全部图卡可见文字只由 `models.json.writer_model` 指定的发布 Agent 独立成文；模型可以从登记的作者模型中选择。

## 固定读取顺序

1. `docs/plans/publish/[2026-07-13]XIAOHONGSHU-AI-PUBLISH/launch/simple-v1/DECISION-MANUAL.md`
2. `config/publication_common/production-copy-authorship.md`
3. `src/omnicompany/packages/domains/xiaohongshu_publish/SKILL.md`
4. `docs/ontology/checklists/50-对外发布-检查单.md`
5. `config/publication_common/visual-evidence-and-layout.md`
6. `publication-types.md`
7. `checklists/01-before-writing.md`
8. `title-patterns.md`
9. `templates/source-evidence-index.md`
10. 按发布类型读取 `templates/technical-introduction-longform.md` 或 `templates/journey-retrospective.md`
11. `checklists/02-draft-review.md`
12. `checklists/03-carrier-visual.md`
13. `rubrics/content-quality.md`
14. `checklists/04-prepublish.md`
15. `validation/concepts.json`

`templates/content-brief.md`、`templates/fact-ledger.md`、`templates/uncompressed-master.md` 以及旧 authoring packet 只用于追查历史产物和旧兼容代码，禁止装入新的成文作者上下文。

## 组件职责

- 语义手册：保存判断方法。
- SKILL：保存操作顺序、入口和故障处理。
- 静态目录模板：只列原始路径、切片、材料类型、状态和公开边界。
- 检查单：在阶段边界防漏。
- rubric：给写作自检和独立审查同一套质量语言。
- 代码门禁：校验 authoring packet、文件摘要、唯一首行 Agent tag、文本格式、平台 AI 标识要求和真实发布授权。
- 运行证据：始终记录发布 Agent 的精确模型、Bash 工作区和输出路径保护结果；启用独立审阅时再记录审阅 Agent。

## 模型调用预算

- 一篇内容默认由同一个选定发布 Agent 在一个连续会话内完成回源、成稿、自检和修订；正文、图卡可见文字与标题在同一会话产出。
- Codex、Claude 不得撰写或改写任何公开中文，也不得制作给成文作者改写的母稿、事实稿、brief、提纲、标题和建议句。
- 独立审阅默认关闭。只有用户明确要求、公开风险较高或事实归属仍有争议时，才增加一次不同模型审阅。
- 材料尚未整理时，可以用 Claude Code Sonnet 或 Codex Terra 做一次前置搜集。前置 Agent 只输出文件路径、精确切片、材料类型、状态、公开边界和对材料本身的中立说明，不写文章、不判断动机、不生成二手故事。
- 字数、禁句、唯一首行 Agent tag、概念首见、URL、图片尺寸、字体和溢出全部先走确定性检查，不消耗模型调用。
- 确定性检查未通过时，先汇总全部问题，再进行一次批量返修；禁止按段落或单卡串行调用高级模型。
- 启用独立审阅时，只判断事实、归属、可理解性和载体容量。纯偏好不触发返工；审阅员只引用一手证据位置，不写替换句。
- 纯机械格式由确定性整理完成。任何会改变公开中文语义或措辞的修改都回到原作者会话；Codex、Claude 与视觉实现者不得直接改字。
- Agent tag 的新增、移位、去重和长文块同步统一走 `xiaohongshu.signature-normalize`；禁止让写作模型逐文件 edit 后再靠 lint 猜错处。
- 草稿检查与预览渲染必须接收本轮帖子范围；延期稿或未进入本轮的帖子不能阻断已就绪内容。
- 每篇公开正文首次出现 `ColorC` 时，由机械装配器统一写成 `ColorC（账号所有者）`；写作 Agent 不手工维护这项格式。
- 已通过审阅的稿件收到定点反馈时，由原发布员连续续修现有文件；冻结未进入本轮发布的帖子，禁止从头重写整组内容。
- 搜集结果只充当定位索引。发布 Agent 必须沿路径直接读取原始材料；索引中的转述、摘要或评价不能直接进入公开稿。原始材料与二手说明冲突时，以原始材料为准。
- 避免频繁传话：前置 Agent 不向审阅 Agent复述，审阅 Agent不向新写作 Agent复述。默认由同一发布 Agent读取证据路径、写作、检查并在原文件续修。
- 对外发布稿不得保留“等待扩展、等待登录、等待真实账号验收”等发布前状态；这些只写内部运行记录。读者看到的稿件必须与已经完成发布前检查的事实一致。

## 执行效率门禁

- 写作 Agent 只运行本节点声明的装配与 lint。若任务明确启用独立审阅，由外层注册管线调用；渲染和审阅台提交也由外层调用。
- 禁止从 Agent Bash 启动另一个 Agent、`xiaohongshu.review` 或其他 `omni run` 下游工作流。
- 30/60 秒只是前台观测周期。流式 LLM 不设默认总时长上限；reasoning、text、tool args 或协议事件仍在增长就继续运行，禁止为赶固定秒数而拆文件、重启会话或重生全文。
- 连接断开、进程退出、心跳失效、连续无返回或流式计数长时间完全不变才是需要介入的异常。确认真实超时后停止当前路径并修复设施，禁止原样重试。
- 相同工具名与原生参数已经失败后，不再执行第二次。需要继续时必须改变输入、修复文件或改用合适设施。
- 每次结束读取 Agent performance 摘要。出现异常 wall time、工具错误、超时或重复拦截时，先修公共设施，再决定是否重跑内容。
- 启动写作员或审查员前必须拿到 PID、trace id、stdout/stderr 路径和状态文件；状态文件应在读取资料前出现，并在等待模型时持续刷新。
- 任何 Agent 状态文件缺失、停止更新或无法写入时，外层管线立即终止该进程树。确认没有旧写入者后，修复观测设施并从工作区最新文件续跑；禁止盲等或并发重启。
- 写作、可选独立审阅和前置 Agent 白名单统一从 `config/xiaohongshu_publish/models.json` 读取，禁止在提示词、lint、预览页或模板中各自硬编码。模型专属的思考参数由 `model_policy.py` 适配；定点续修关闭不必要的长推理并直接使用原生 edit。若仍超时，停止该路径并调整任务拆分，禁止原参数重跑。

Memory 不保存本目录内容的副本。
