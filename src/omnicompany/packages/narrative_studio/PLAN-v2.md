# narrative_studio v2 执行计划(到全部做完)

> 2026-06-27。承接:数据地基已纠正(v2 导入只装确认方向 + 游戏内容落地层 + 否决案归档,具体层留空)。
> 本计划=把 narrative_studio 改成 vilo **唯一创作台**(吸收 Obsidian vault 的创作者功能),并照**四层信息架构**重搭前端。
> 拍定取舍:**1A** 落地层(cards/events)以 `wiki/` md 为单一真源,narrative_studio 编辑**写回** md;**2** 并入 `drafts/草稿看板` + `comments/圈选评论`。
> 原则:执行到完,不再为问而问;每阶段自带验证(pytest + 真 UI e2e),最终零缺陷。

## 四层信息架构(最终形态)

- **① 叙事指导层**:立意 · 背景/思考 · 大纲(四幕)· 受众与预期管理
- **② 叙事事实层**:情节 · 情节推理 · 设定(人设 / 世界 / 关系)
- **③ 落地指导层**:文风矩阵 · 落地结构演算引擎(路线节点图 + 数值/状态 + 演练 三合一)
- **④ 落地层**:游戏内文本(卡片/事件/标签/wiki,按类型分组,编辑写回 wiki)+ 草稿看板
- **横切(不单列页签,折进上下文)**:健康检查(就地标记+顶栏抽屉)· 完成度(侧栏)· 贯穿追踪(实体右键)· 分布对照(大纲/情节内)· 出处钻取(检查器)· 版本对照(顶栏)· 圈选评论(收件箱抽屉)
- **只读入口**:否决案归档(随时查被取代/否决旧案)

## P1 后端补完(落地层写回 + 创作者功能)

1. `wiki_sync.py`:GameText ↔ `wiki/cards|events/*.md` 的**保留式往返**(读时解析,写时只更新 文案/正文 + 创作者批注 + 选择,**保留未建模的段**:元素/关联事件/卡图块/frontmatter)。新条目按模板新建。
2. 模型:`GameText.is_draft`;新增 `comments`(圈选评论:id/anchor/target/body/status/resolved)+ `drafts`(复用 game_texts + is_draft,看板视图聚合)。
3. importer 扩:读 `wiki/drafts/` → game_texts(is_draft=True);读 `wiki/comments/圈选评论收件箱` → comments。
4. API:`PUT /game-text/{id}`(写回 wiki md)· `POST /game-text`(新建卡/事件,写 wiki)· `POST /draft/promote`(草稿转正式)· comments CRUD · 落地层按 text_type 列表。
5. 测试:wiki 往返(改 body→写回→重读一致,且未建模段不丢)、drafts、comments。

## P2 前端四层重搭

6. `views/index.tsx`:改成 4 层 group + 上述视图;工具从页签下沉为上下文/抽屉。
7. 新视图:`BackgroundView`(背景/思考)· `AudienceView`(受众与预期管理)· `GameTextView`(落地层,按 card/event/tag/wiki 分组,行内编辑→写回 wiki,显示 art/status/批注)· `DraftsView`(草稿看板 + 转正式)· `CommentsView`(圈选评论收件箱,复用 anchor 评论)· `RejectedArchiveView`(否决案归档,只读,按 superseded/rejected 分)· `PlotView`(情节=场景客观事实,空时引导新建)· `PlotReasoningView`(情节推理=causality/value_shift)。
8. 复用/合并既有:设定=Characters+World+Relationships 同层;落地结构演算引擎=RouteGraph+Variables+Playthrough 合一壳;文风矩阵=registers/voices/style_matrix。
9. 框架:store `CARRIER_VIEW` 补新载体;`api.ts` 补新端点;`Inspector` 支持新载体(game_texts/audience/background/comments/drafts 单/列);健康/完成度做成顶栏抽屉+侧栏而非页签;否决案只读。
10. 顶栏精简:跳转/搜索/替换/演练/版本/健康抽屉/完成度。

## P3 集成联调 + 验证

11. `npm run build`(tsc+vite)修到零错误。
12. 重导 v2 + 重启服务;TestClient 核全端点(含 wiki 写回往返、drafts、comments)。
13. Playwright 真 UI e2e:遍历四层全部视图 + 关键交互(编辑一条落地层卡→断言 wiki md 真被改写;草稿转正;圈选评论;否决案只读;演练)+ 零控制台报错。
14. 对抗式审计(后端逻辑 + 前后端契约 + 规格符合 + wiki 写回安全)→ 逐条修到零缺陷。

## P4 收尾

15. 更新 `08-交互设计与功能点.md`:四层 IA + 单一创作台 + 落地层 wiki 写回。
16. 重新提交成品到审阅台(demo live_url),替换旧材料。
17. 更新记忆(narrative_studio=vilo 唯一创作台,落地层写回 wiki,四层 IA)。

完成判据:四层 UI 跑通、落地层编辑真写回 wiki、草稿/评论/否决案到位、69+ 测试绿、真 UI e2e 零报错、无 TODO/桩。
