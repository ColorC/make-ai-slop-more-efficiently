<!-- [OMNI] origin=codex domain=standards/protocol ts=2026-05-17T00:00:00Z type=doc status=draft -->
<!-- [OMNI] summary="分布式渐进上下文绑定规范 v0.1 - 按 plan/project/path/kind/topic 解析应注入的 standards/templates/registry/project context" -->
<!-- [OMNI] why="OmniChat 内 agent 不应依赖 Codex/Claude 私有规则文件反复灌输 omnicompany 分布式文档、material、worker、template 规则; 上下文应由 omnicompany 自己的索引和绑定规范按需注入" -->
<!-- [OMNI] tags=context,plan,standards,template,material,worker,dogfood -->
<!-- [OMNI] material_id="material:standards.protocol.progressive_context_binding.md" -->

# 分布式渐进上下文绑定规范 v0.1

> **状态**: draft, 已有最小 CLI 解析入口 `omni context resolve`
> **作用域**: OmniChat / CLI / dashboard / external worker 都可消费的上下文选择协议
> **核心目标**: 不把所有规则塞进 agent.md / CLAUDE.md / `.claude/` / `.agents/`, 而是在相关工作发生时按需解析并注入上下文路径

---

## 一 · 已有权威源

本规范不替代已有规范, 只定义如何按需把它们组合进上下文:

| 领域 | 权威源 |
|---|---|
| 文档放置 | `distributed-docs.md` |
| 新规范前提 | `standards_meta.md` |
| 术语 | [`terminology.md`](../_global/terminology.md) |
| Plan | [`plan.md`](../concepts/plan.md), [`plan_template.md`](plan_template.md), `templates/plan/` |
| Material | [`material.md`](../concepts/material.md), `templates/material/` |
| Worker | [`worker.md`](../concepts/worker.md), `templates/worker/` |
| Agent | [`agent_first.md`](../concepts/agent_first.md), [`agent_tools.md`](../concepts/agent_tools.md), `templates/agent/` |
| 注册 | [`registration.md`](../cli/registration.md), `omni register/lookup` |
| 既有路径到规范索引 | `standards-index.yaml` |

---

## 二 · 解析输入

上下文解析器的输入是复合的, 不是单一文件名:

| 输入 | 说明 |
|---|---|
| `plan_id` | 当前 active plan, 读取 `plan.md` frontmatter / `brief.md` / `binding` |
| `project` | plan frontmatter 的 `project`, 或路径反推的 domain/service |
| `paths[]` | 本轮要读写或讨论的文件/目录 |
| `kinds[]` | material / worker / agent / plan / template / standard / context_binding 等 artifact kind |
| `topic` | 人类自然语言主题词, 用于触发"主观判断涉及到什么"的规则 |
| `key_contexts` | 计划或索引显式声明的关键上下文路径 |

解析器输出不是一大段 prompt, 而是一组**可打开的上下文路径**, 调用方可决定全文注入、摘要注入或只展示给 agent.

---

## 三 · 解析顺序

按以下顺序合并, 去重后输出:

1. **active plan 层**: 当前 plan 的 `plan.md`, `brief.md`, `standards`, `applicable_standards`
2. **project 层**: `docs/plans/<project>/project.md`, `src/.../<project>/DESIGN.md`, `.omni/manifest.yaml`, `.omni/workspace.yaml`
3. **path/kind 层**: `standards-index.yaml` 的 `kind_inference` + `path_match`
4. **topic/profile 层**: `context-bindings.yaml` 中的 `project` / `path_match` / `kinds` / `trigger_keywords`
5. **key memory 层**: plan 或 profile 显式列出的关键 standards/templates/examples, 只记录路径, 不复制内容

冲突时遵守已有规范优先级: 用户硬规则 > 已有规范 > 已有代码实践 > 新绑定草稿.

---

## 四 · 机器可读绑定

机器可读入口固定为:

```text
docs/standards/_meta/context-bindings.yaml
```

每条 profile 至少包含:

```yaml
- id: voxelcraft.material-authoring
  applies:
    projects: [voxelcraft]
    kinds: [material, standard_md]
    path_match:
      - docs/standards/_domain_specific/voxelcraft/**
      - src/omnicompany/packages/domains/voxelcraft/**/materials.py
    trigger_keywords: [material, worker, standard, 规范, 材料, 工人]
  include:
    standards:
      - docs/standards/concepts/material.md
    templates:
      - templates/material/注册件.yaml
```

`trigger_keywords` 是"主观判断"的最小实现: 用户没有给具体路径但说"要新做 material/worker/规范"时, 解析器仍能把对应上下文拿出来.

---

## 五 · Plan 绑定要求

涉及长期工作的 plan SHOULD 在 frontmatter 写:

```yaml
binding:
  workspace: omnicompany/
  packages: [...]
  targets: [...]
standards:
  - standards/protocol/progressive_context.md
  - standards/_global/distributed-docs.md
applicable_standards:
  - standards/protocol/progressive_context.md
```

Plan 不应复制规范正文. Plan 只声明"本计划应绑定哪些上下文入口", 真内容仍回到 standards/templates/project docs.

---

## 六 · MC 合规样本

当前 dogfood 样本:

```bash
omni context resolve \
  --plan current \
  --path docs/standards/_domain_specific/voxelcraft/building.md \
  --topic "voxelcraft material standard and worker definition" \
  --json
```

期望至少解析出:

- 本计划 `plan.md` / `brief.md`
- `docs/standards/_global/distributed-docs.md`
- `docs/standards/concepts/material.md`
- `docs/standards/concepts/worker.md`
- `docs/standards/concepts/template.md`
- `templates/material/注册件.yaml`
- `templates/worker/注册件.yaml`
- `src/omnicompany/packages/domains/voxelcraft/DESIGN.md`
- `src/omnicompany/packages/domains/voxelcraft/.omni/manifest.yaml`
- `src/omnicompany/packages/domains/voxelcraft/.omni/workspace.yaml`

这份样本不要求把全部内容灌进模型; 它要求 agent 能快速知道应该先读哪些权威入口.

---

## 七 · 反模式

| 反模式 | 后果 |
|---|---|
| 把本规范复制进 CLAUDE.md / agent.md | 平台绑定, 多处漂移 |
| 每个新 agent 都人工解释 material/worker/template 规则 | 上下文不可扩展, 人类重复劳动 |
| 新建 voxelcraft 专属规则但不引用全局 material/worker/template 规范 | 两套规则冲突 |
| 只按路径匹配, 不支持 topic trigger | 用户说"涉及 worker/material"时拿不到规范 |
| 自动注入所有 standards 全文 | 上下文爆炸, 重点丢失 |
| 计划里复制规范正文 | 违反"索引而非复制" |

---

## 八 · 下一步

v0.1 只保证 CLI 可解析. 后续接入顺序:

1. OmniChat 在构造 plan context 时调用同一解析器, 展示 context paths
2. `omni worker run` 支持从 resolver 输出自动转 `--context`
3. registry / dashboard 把 profile 命中结果作为可观测事件记录
