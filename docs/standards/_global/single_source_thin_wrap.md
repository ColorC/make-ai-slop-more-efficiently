<!-- [OMNI] origin=ai-ide domain=omnicompany/standards/_global ts=2026-05-02T10:00:00Z type=doc status=active agent=ai-ide-current -->
<!-- [OMNI] summary="唯一源 + 薄包装铁律 - 规则 / 工作顺序源在 docs/standards/, .claude/skills/ 跟 .omni/sandbox/guides/ 是薄包装不重复写" -->
<!-- [OMNI] why="用户 2026-05-02 立铁律 - .claude/skills 修改起来麻烦因为内容跟 docs/ 重复维护. 应该单源 + 自动同步 / 薄包装" -->
<!-- [OMNI] tags=standard,single-source,thin-wrapper,foundation,maintenance -->
<!-- [OMNI] material_id="material:standards.global.single_source_thin_wrapper_rule.md" -->

# 唯一源 + 薄包装铁律

> **状态**: 规范 v1 (2026-05-02), 用户立, **取代** 我之前写的 [dot_claude_vs_dot_omni.md](dot_claude_vs_dot_omni.md) 里"两套目录平行分工" 的描述
> **关联**: `directory_structure.md` / `concepts_three_layers.md` / `cc_wrapper_hooks.md`

## 一、 用户原话

2026-05-02:

> ".claude 都不是迁移到 .omni 了, .omni 结构混乱, 主要是 skill 修改起来麻烦, 我们之后自动和 docs 里面的文档自动统一, 或者采用非常薄的包装. 具体规则, 工作顺序用唯一源, 不再在里面直接写, 维护起来麻烦"

含义清晰:
- **规则跟工作顺序** 的源 = `docs/standards/` (唯一)
- `.claude/skills/` / `.omni/sandbox/guides/` 等都是**薄包装** 或**自动同步**
- **不重复写** 同一份内容到两处, 改起来不麻烦

## 二、 唯一源 — `docs/standards/`

下面这些是**唯一源**, 改 docs 是改的源头, 其他位置自动跟上 / 薄包装引用:

| 内容类型 | 源位置 |
|---|---|
| 规则 (R-/F-/P-/D- 等硬规则) | `docs/standards/concepts/<kind>.md` |
| 工作顺序 (走 omni new → check → promote 等) | `docs/standards/cli/<topic>.md` |
| 全局规范 (目录结构 / 命名 / 三层分类) | `docs/standards/_global/<topic>.md` |
| 协议层契约 | `docs/standards/protocol/<topic>.md` |

**所有人** (用户 / AI IDE / agent) 改规则跟工作顺序都改 docs. 不改别处.

## 三、 薄包装 — 怎么写

### 形态 A · 引用型 SKILL.md (轻)

`.claude/skills/<X>/SKILL.md` 只保留:

```markdown
---
name: <skill 名>
description: <一句话>
user-invocable: true
---

# <Skill 标题>

你正在 <场景>. 完整规则跟工作顺序请读源:

- 入口: `docs/standards/cli/<X>.md`
- 概念: `docs/standards/concepts/<kind>.md`
- 工作流: 跑 `omni <command> --help` 看 CLI 子命令

本 Skill 不复述规则 — 改规则改 docs.
```

简短 + 全 link 出去. 维护代价 = 0 (改 docs 不用动 SKILL).

### 形态 B · 自动同步型 (重, 留下一阶段)

后续可加自动机制:
- `.claude/skills/<X>/SKILL.md` 由 `docs/standards/` 渲染生成 (类似静态站点 build)
- 守护扫到 docs 改了 → 自动重新渲染 SKILL
- 保证 SKILL = docs 的视图, 不可手写

当前阶段 (2026-05-02) 走形态 A. 自动同步留给后续基础设施工作.

### 形态 C · 不允许 — 独立写

**不允许** 在 SKILL.md 本地展开规则 / 工作顺序细节. 这是当前 `.claude/skills/omnicompany-use/SKILL.md` 的违规形态 (241 行 0 个 docs 引用 全本地写).

## 四、 现状违规清单 (待治理)

### 违规 1: `.claude/skills/omnicompany-dev/SKILL.md` (1999 行)

- 26 处 docs 引用 (有改进)
- 但 1999 行内容主体仍本地展开
- 改 docs 后内容容易跟不上

**修法**:
1. 缩到 200-400 行
2. 只保留 Skill 入口 + 调用约定 + docs 链接清单
3. 详细规则全引用到 docs

### 违规 2: `.claude/skills/omnicompany-use/SKILL.md` (241 行)

- 0 处 docs 引用
- 全本地写

**修法**: 同上, 改成纯链接型 + 调用约定

### 违规 3: `.omni/sandbox/guides/<kind>.md` (规范第二节列了)

- [docs/standards/cli/sandbox.md](../cli/sandbox.md) 第二节定义了 `.omni/sandbox/guides/material.md` 等是规范副本
- 实际上不存在 (我之前说"已预置" 但没真放副本, 而是 `omni sandbox guide --kind=<>` 命令直接读 `templates/<kind>/向导.md`)
- 当前行为: **已经是薄包装** (CLI 即时读 docs / templates), 不实际副本. 这点对了, 是规范文档本身写错

**修法**: 改 [sandbox.md](../cli/sandbox.md) 第二节, 撤"guides/ 副本预置" 描述, 写"guides 通过 `omni sandbox guide --kind=<>` 命令实时拉 templates/<kind>/向导.md, 不预置副本" (这是当前实施的事实).

## 五、 跨边界写作纪律

无论是 AI IDE / 用户 / 业务 agent, 写规则跟工作顺序前问自己:

1. 这条内容**是否已经在 docs/standards/ 有了**? — 在的话引用, 不复述
2. 我现在写的位置**是不是源**? — 不是源就只能链接 / 薄包装
3. 改了之后**其他位置会不会自动跟上**? — 不会就违规

**唯一源原则**适用范围:
- 规则 / 工作顺序 — 严格唯一源
- 概念定义 — 严格唯一源
- 命名约定 — 严格唯一源 (terminology.md)
- API 契约 — 严格唯一源 (各 service DESIGN.md 跟 protocol/)
- 但**业务事实数据** (例 demogame 业务字段语义) 不在本规范范围 (那是 data 类, 走 data 注册中心)

## 六、 反模式

**SKILL.md 里直接写规则 + 复述 docs** — 用户原话"修改起来麻烦" 的根源.

**两份独立维护的"沙盒规范"** — 一份在 docs/standards/cli/sandbox.md, 一份在 .omni/sandbox/guides/sandbox.md, 改完一边忘记另一边.

**".omni/sandbox/guides/material.md 副本"** — 规范文档说要预置, 实际应该走 CLI 实时读不预置 (避免副本漂移).

**复制 docs 内容到 dashboard 的 description 字段** — dashboard 显示规则信息走 API 拉 docs, 不复制.

**用户写新规则不进 docs 写到 plan / 散落 markdown** — 规则的源是 standards, plan 引用规则不发明规则.

## 七、 演进 (留下一阶段)

- **静态站点 build** — docs/standards/ 改 → 自动渲染到 .claude/skills/ + .omni/sandbox/guides/ + dashboard frontend 显示
- **守护扫一致性** — 扫 SKILL.md 跟 docs 的内容差异, 报警漂移
- **薄包装 lint** — `omni guardian check-thin-wrap` 扫 .claude/skills/ 内容跟 docs 重复度, 高重复度报警

## 八、 实施引用

- `omnicompany/docs/standards/` - 唯一源
- `omnicompany/.claude/skills/<X>/SKILL.md` - 应当走形态 A (薄包装)
- `omnicompany/src/omnicompany/cli/commands/creation.py` `cmd_sandbox_guide` - 已经是薄包装实施 (CLI 实时读 docs/templates, 不预置副本)
