---
name: project_atlas
description: 项目及业务收集管线 —— 语义策展跨工作空间(omnicompany/quant-lab/webworks/AIWorkSpace/poof)的可复用能力,用带工具的 claude-code worker 实地核实真入口,按"操作对象/生产对象"切成 object-SKILL(lark-cli 粒度),并维护项目速览名录(扩 omni project)。产出先落 staging 待人审。触发词:资源收集/对象SKILL/项目名录/收集业务能力/project_atlas/收集一个空间。
---

# project_atlas — 项目及业务收集

把各工作空间下"可复用的工具/管线/能力"语义策展进资源中心,产出两样:

1. **object-SKILL** —— 按"操作对象/生产对象"切(粒度像 lark-cli 把collab platform切成 doc/sheets/im),一对象一份,指向现成设施、写"禁另搭"。**由带工具的 claude-code worker 实地核实真入口后写**(标准见 `docs/plans/[2026-06-22]OMNI-RESOURCE-CENTER/object-skill-standard.md`)。
2. **项目速览名录** —— 扩 `omni project` 注册表,工具和非工具(内容仓)都登,一提就本地秒定位。

## 何时用

- 要把某个工作空间的可复用能力收集/登记进资源中心时。
- 要为一种新的"操作/生产对象"建一份 object-SKILL 时。

## 怎么跑

```bash
omni run project_atlas.run -i space=omnicompany            # 收集一个空间(worker 写 grounded staging 草稿 + 名录条目)
omni run project_atlas.run -i space=omnicompany -i dry_run=1  # 跳过 worker, 只验管线 plumbing
```

可选 space:omnicompany / quant-lab / webworks / poof / aiworkspace(见 `spaces.py`)。

## 管线(4 节点)

`intake`(解析 space→根) → `survey`(确定性收线索地图) → **`collect`(`omni worker run claude-code`,带 Read/Grep/Glob/Bash 实地核实真入口 + 写 grounded object-SKILL)** → `finalize`(读 staging 写名录 + 报告)。

> ⚠ 为什么 collect 用 worker 不用裸 LLM:没有读工具的 LLM 必然编造调用入口(实证:第一版把 `omni decisions record` 编成 `index.ts`)。详见标准"编写机制"节 + memory `feedback_agents_need_readonly_tools`。

## 产物

- `data/domains/project_atlas/staging/<space>/<object>/SKILL.md` —— worker 起草, **待人审**。
- `data/domains/project_atlas/atlas.jsonl` —— 项目速览名录草稿(待并入 `omni project`)。
- 批准后 export 到 `~/.claude/skills` + `~/.codex/skills`(两 AI 都按 description 认领)。

> 权威设计:`docs/plans/[2026-06-22]OMNI-RESOURCE-CENTER/03-collection-team-design.md`。AI 产出不直进 canonical,护"人工 grep 唯一可信"。
