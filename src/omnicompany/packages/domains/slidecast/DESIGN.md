<!-- [OMNI] origin=ai-ide domain=slidecast ts=2026-06-20T00:00:00Z type=doc status=active -->
<!-- [OMNI] summary="slidecast 域设计:AI 自动生成会动的 HTML 演示式讲解/说书 deck(Slidev),可选导出视频。IR-first 架构。" -->

# slidecast — 域设计

> 类别 **aigc-video-content**(演示 PPT 式视频内容生成)。本域是该类别下首个项目,
> 往后这个类别可能还有别的视频生成项目。
> 立项:2026-06-20(用户:"在 omnicompany 开一个项目,aigc-video-content 类,即演示 PPT 类的视频生成")。

## 状态

**雏形** —— 架构与拓扑已定(本文件 + `team.py`),管线节点(routers/run.py)与注册待实现。
不要当成可跑的成品;下一步见文末。

## 核心目的

把一个**选题或脚本**自动变成**会动的 HTML 演示式讲解 deck**(知识科普 / 说书),
需要时再**导出成带旁白的视频**。

回答的问题:
> 怎么用 AI 把结构化内容,变成信息密度高、会动、可编程、贴合 HTML 强项、且能被 LLM 可靠生成的讲解演示(并可选出视频)?

## 方向定调

- **引擎 = Slidev**。理由:Markdown 最好让 LLM 稳定生成、动效体系最全(v-click 逐步揭示 /
  magic-move 代码变形 / v-motion 运动 / 转场)、有官方 AI 工具链、社区最活、MIT 可商用。
- **否掉 AI 生视频素材拼接**(MoneyPrinterTurbo 那套无信息量拼接)。
- 完整选型证据(六环节公平对比 + 对抗式核查、不打分):
  `docs/reports/题目1-科普说书讲解管线-选型对比-HTML动画演示路线-2026-06-20.md`。
  旧的 AI 生视频路线报告(2026-06-13)已加归档横幅,非现行权威。

## 架构决策

### D1 — IR-first(结构化 slide JSON 作中间层)

LLM 不直接产 Slidev Markdown,而是先产**结构化 slide IR(JSON)**,再由确定性渲染器翻成 Slidev Markdown。
- **为什么**:IR 可用 JSON Schema 约束 + 校验失败有界重试;内容与表现解耦;可审计。
- **附带红利**:日后若 Slidev 动效不够,**只换渲染后端(→ reveal.js),LLM 侧完全不动**。
  这才是低成本退路,而不是"三个框架都拉着备用"。

### D2 — 单引擎起步,不三个都拉

只用 Slidev。reveal.js 作 **documented fallback**(撞到 Slidev 表达不了的动效,如跨页同元素平滑补间才上),
Marp 不在本目标范围(动效弱、偏静态/PDF 场景)。养三套主题/模板/接线是三倍成本零收益。

### D3 — 视频是独立支线,与 HTML deck 分开

**没有任何 HTML 幻灯片框架原生导出 MP4**——"会动的 HTML deck"和"导出视频"是两件事。
- 主线交付:可交互 HTML/SPA。
- 视频支线(`export_video`,默认不跑):要帧完美 + 原生旁白对齐用 Remotion(把 deck 改写成 React 组件;
  注意 >3 人公司付费);不想付费/复用现成 deck 走截帧 + ffmpeg(纯 CSS 动画会失真,动画走 JS)+
  WhisperX/whisper.cpp 拿逐词时间戳对齐旁白。**中文逐词对齐质量未实测,是真实风险**。

### D4 — 域边界(对齐目录纪律)

- 管线代码(team/routers/prompt)在本 domain。
- 产物(decks / renders / videos / runs)进 `data/domains/slidecast/`(gitignore 运行态)。
- 内容真源(选题清单 / 脚本素材)留外部,不进管线代码。
- 通用能力复用 `packages/services/`(LLM 网关、web 检索等),领域只组装;图像若需走 liclick。

### D5 — 复用既有设施,不重复造

苦力 worker 走统一 LLM 网关性价比模型(同 research / vilo 的用法)。不自建模型客户端。

## 管线拓扑(declared,见 `team.py`)

```
选题/脚本
  ↓ intake(RULE)      归一化输入(选题/受众/时长/风格),建 run_dir
  ↓ outline(LLM)      产讲解大纲(钩子→分点→收尾;一页一观点)
  ↓ author_ir(LLM)    逐页产 slide IR(标题/要点/动画步/图表/讲稿)+ guardrails
  ↓ validate_ir(RULE) JSON schema 校验 + 占位/越界/动画序号检查;失败有界重试
  ↓ render_slidev(RULE) IR → Slidev Markdown(v-click / magic-move / Mermaid)
  ↓ build_deck(RULE)  slidev build → 可交互 HTML/SPA
  ↓ export_video(RULE,可选,默认不跑) 导出带旁白 MP4
```

## 已知风险 / 未决(诚实标注)

1. **LLM 一次成稿可编译率无第三方实测** —— 选型报告里最大空白。Slidev 是综合判断下的选择,
   不是"AI 生成成功率"实测出来的。**待 bake-off**(Slidev vs reveal.js,同选题多模型多次跑,统计 build 通过率 + 人工质量)。
2. **中文逐词时间戳质量未实测** —— WhisperX 默认对齐模型不含中文;视频支线旁白对齐的真实风险,采用前必须自跑验证。
3. **Slidev 高级特性(Vue 组件/scoped CSS/Monaco)会拉低可编译率** —— 需锁"安全特性子集"写进 prompt/guardrails。
4. **License** —— Slidev 本体 MIT 安全;视频支线若用 Remotion,>3 人公司需付费;若用 slidev-ai 类有额外商用条款,采用前读 LICENSE。

## 下一步

1. 实现 `routers/`(各节点 transform)+ `run.py`(节点 ID → Router 绑定),参考 `domains/research/`。
2. 在 `src/omnicompany/core/pipelines.py` 注册一个 `slidecast.run` 条目(`_lazy` 懒加载,照 research/vilo 写法)。
3. `python scripts/validate_domains.py` 校验,出**第一份真 deck**(主题→会动 HTML)跑通主线。
4. 补 bake-off(风险 1),再决定视频支线是否上(风险 2)。

## 参考

- 选型报告(权威):`docs/reports/题目1-科普说书讲解管线-选型对比-HTML动画演示路线-2026-06-20.md`
- 旧 AI 视频路线(已归档):`docs/reports/题目1-科普说书视频管线-选型对比-2026-06-13.md`
- 同类管线域写法:`packages/domains/research/`(team.py / run.py / routers/)
- 关联 memory:`open_research_project_line`(公开调研线 + 两题目)
