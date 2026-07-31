<!-- [OMNI] origin=claude-code domain=project_atlas ts=2026-06-22 type=doc status=active -->

# project_atlas — 设计

跨工作空间的"可复用能力"收集与登记:把各空间(omnicompany/quant-lab/webworks/poof/aiworkspace/walker…)的工具/管线/能力,语义策展成按"操作/生产对象"切的 object-SKILL,供 Claude Code 与 Codex 按 description 认领,防重复造轮。

## 状态

GROWING(2026-06-22)。收集管线已跑通,产出 83 个 grounded object-SKILL 跨 6 空间;`omni atlas` 审/导闭环已建并 export 到两个 AI。审后正式名录化(并入 omni project)+ enumerate 去 CLI-中心化 待办。全节点 maturity=GROWING(第一版裸 LLM 翻车后重做)。

## 核心接口

- `omni run project_atlas.run -i space=<空间> [-i dry_run=1]` —— 收集一个空间(断点续跑,中断重跑自动接上)。
- `omni atlas list|approve|reject|export` —— 审/导闭环（staging→canonical→`~/.claude/skills`+`~/.agents/skills`）；旧 `~/.codex/skills` 只读兼容、不再写入。
- 产物:`data/domains/project_atlas/{staging/<space>/<obj>/SKILL.md(待审), skills/(批准), plan/<space>.objects.json(对象清单=断点续跑真源)}`。

## 架构决策

- collect 用 `omni worker run claude-code`(带 Read/Grep/Glob 的 agent 实地核实真入口),**不用裸 LLM**——后者无工具核实必然编造命令(结构性幻觉)。
- enumerate 喂确定性自省清单(`_omni_inventory`:真实管线名+CLI 命令树),worker **只归并不探索**(目标 15~30,>40 护栏中止),避免粒度炸 + 触发 Task 中断。
- **断点续跑**:objects.json + staging 文件即 checkpoint,以落盘产物判完成,不信 worker 误报的 status(它常写完文件后在尾部被中断标 failed)。
- AI 产出落 staging,**人审(`omni atlas`)批准才入 canonical/export**——护"人工 grep 唯一可信"。

## 数据流 / 拓扑

`request →[intake RULE: space→根+run_dir] →[survey RULE: 收线索地图] →[collect WORKER: enumerate 对象清单→逐对象 grounded 起草, 已有跳过] →[finalize RULE: 读 staging 落名录+报告]`。

collect 内部:`_omni_inventory`(仅 omnicompany)→ enumerate worker 写 `objects.json` → 逐对象 author worker 写 `staging/<space>/<obj>/SKILL.md`。

## 已知局限

- enumerate 现只喂 omni 注册命令(**CLI-中心**),非 CLI 资产(app/项目类,如 aigc-lab/poof)靠"补漏轮"才登——待让 enumerate 兼顾非 CLI。
- 拓扑由第一版 5 节点(裸 LLM classify/author/write 翻车)重做为 4 节点(worker)。
- 审后正式名录化(并入 `omni project`)、跨仓二次差集自动化 未建。

## 参考资料

- 权威设计:`docs/plans/[2026-06-22]OMNI-RESOURCE-CENTER/03-collection-team-design.md`
- 编写标准:`docs/plans/[2026-06-22]OMNI-RESOURCE-CENTER/object-skill-standard.md`
- 状态与 TODO:`docs/plans/[2026-06-22]OMNI-RESOURCE-CENTER/04-status-and-todo.md`
