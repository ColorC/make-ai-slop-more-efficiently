<!-- [OMNI] origin=ai-ide domain=docs/standards ts=2026-05-01T00:00:00Z type=standard status=active agent=ai-ide-bd9cde92 -->
<!-- [OMNI] summary="plan 规范, 锁定目标 / 工作区 / 工作规范, 防债务偏移遗忘, 基于 dashboard markdown wikilink 体系" -->
<!-- [OMNI] why="plan 自身规范一直在演进, 现在 dashboard markdown 跟 wikilink 已实装, plan 规范要落到这套实装上, 让每份 plan 都显式锚定到目标 / 工作区 / 适用规范, 不再悬浮" -->
<!-- [OMNI] tags=plan,standard,wikilink,binding,dashboard-integration,foundation -->
<!-- [OMNI] material_id="material:standards.concepts.plan_lifecycle_and_wikilink_binding.md" -->

# plan 规范

> **跟 [`protocol/plan_template.md`](../protocol/plan_template.md) 关系**: 本文是 concept 层 (plan 概念是什么 + 接 dashboard wikilink 体系); plan_template 是 protocol 层 (plan.md 文件章节硬下限). 两份规范层次不同, 互补不矛盾, 改任一份前必同步看另一份 (元规范 `standards_meta.md` 第 1 条).

## 一、 plan 是什么

plan 是跨日跨阶段的过程记录文档. 它**不是**永久概念约束 (那是 standard 跟 template 干的事), 也**不是**实施代码 (那是 worker / agent / tool 干的事). plan 装的是"现在为什么开始做这件事 / 拆成几段做 / 怎么算做完 / 风险在哪 / 做完归档到哪". 一份 plan 是一个**有边界、有终点、有归属**的过程记录, 不是无限拖的项目.

按概念分类, plan 归在 omnicompany 的"文档体系" (跟人类交互的, src 外部) 下面, 跟 report / DESIGN 平行 (DESIGN 归 team 一部分, 不独立). 所有面向人类阅读跟 dashboard 浏览的过程文档都按 plan 规范走.

## 二、 跟 dashboard 的接通

dashboard 已实装 obsidian 风格的 wikilink 跟 markdown 渲染, plan 规范完全建立在这套基础上.

### 2.1 wikilink 引用语法 (dashboard 已支持)

| 语法 | 用途 | 例子 |
|---|---|---|
| `[[plan:TOPIC-CODE]]` | 跨 plan 引用 | `[[plan:OMNICOMPANY-CLI-PHASE3]]` |
| `[[worker:worker_name]]` | 引用一个工人 | `[[worker:hygiene_scan]]` |
| `[[material:domain.name]]` | 引用一份材料 | `[[material:demogame.xlsm-formula-map]]` |
| `[[team:team_name]]` | 引用一个团队 | `[[team:csv_to_md]]` |
| `[[trace:trace_id]]` | 引用一次运行 trace | `[[trace:01KP5VHW]]` |
| `[[session:session_id]]` | 引用一个 session | `[[session:bd9cde92]]` |
| `[[node:node_id]]` | 引用一个节点 | `[[node:extract_step]]` |
| `[[note:any_note_id]]` | 引用一份散文笔记 | `[[note:debugging_log]]` |
| `[[task:task_id]]` | 引用一个任务 | `[[task:T-2026-05-01-01]]` |
| `[[name]]` | 默认 note 类型 (无前缀) | `[[README]]` |
| `[[type:id\|别名]]` | 带显示别名 | `[[worker:foo\|FooWorker 启动器]]` |
| `[[type:id#heading]]` | 带 heading 锚点 | `[[plan:CLI-PHASE3#§5]]` |
| `[[omni://type/id]]` | 完整协议 (跟短写等价) | `[[omni://plan/CLI-PHASE3]]` |

### 2.2 markdown 高级特性 (dashboard 已支持)

- **callouts** (obsidian 风格): `> [!note] 标题` / `> [!warning]` / `> [!tip]` / 等. 渲染成彩色提示框
- **mermaid 代码块**: 用 ` ```mermaid ` 围栏直接画图, 阶段拆分 / 假设树推荐用 mermaid 而不是 ASCII 表格
- **GFM**: 表格 / 任务列表 / 删除线 / 自动链接
- **KaTeX 数学**: `$x^2$` 行内 / `$$x^2$$` 块级

### 2.3 plan 规范扩展的 wikilink entity (规范定义, dashboard 按规范实装)

plan 规范在 dashboard 已实装的 entity 之上扩展三类, dashboard 实装跟进本规范:

| 语法 | 用途 | 例子 |
|---|---|---|
| `[[standard:<分类>/<name>]]` | 引用一份规范 | `[[standard:concepts/material]]` |
| `[[workspace:<path>]]` | 引用一个工作区 | `[[workspace:omnicompany/data/_workspaces/csv_to_md/job1]]` |
| `[[package:<type>/<name>]]` | 引用一个 package | `[[package:service/guardian]]` / `[[package:domain/demogame]]` / `[[package:core/cli]]` |

dashboard 当前不识别这三类 wikilink, 渲染成 plain text 但仍可读. dashboard 后续按本规范加对应 entity handler (类型 / 跳转 / hover 预览), 现存按本规范写的 plan 自动激活功能, 不需要回头改文件.

**规范先行, 实装跟进** — plan 内文一律用 wikilink, 不用普通 markdown 链接 (除非链外部 URL).

## 三、 plan 头部必备字段 (唯一 frontmatter, 含绑定块)

> **2026-06-13 修订** (用户裁决"规范冲突以新的为准, 保持唯一体系"): 此前并存三套头部模式
> (本节旧版"OmniMark 后第二个 yaml 块, 代码没接" / 存量全用的顶部平铺字段 / plan_template
> 无 frontmatter 示例)。统一为: **文件顶部唯一一个 yaml frontmatter**, 平铺字段与绑定块
> 同居一块 — 这正是 dashboard 解析器 (controlplane/plans.py `parse_plan_frontmatter`)
> 唯一读取的位置, 嵌套的 binding 等新字段原样透传, 不需要第二个块。

plan.md 结构: **顶部 frontmatter → OmniMark v3 头注释 → §1 主题正文**。frontmatter 字段:

```yaml
---
# ── 平铺字段 (dashboard 列表/看板直接消费; 存量 plan 已全用) ──
title: <计划标题>
date: '<YYYY-MM-DD>'                              # 立计划日
project: <project_id>                             # 可选, 跨 plan 关联到项目 (归属以 plan_governance 覆盖表优先)
work_type: <refactor|infra-convergence|...>
status: <active|done|paused>
phase: <当前阶段名>                                # 可选
standards: []                                     # 可选, 旧字段, 新写法用下面 applicable_standards
exit_criteria:
  - <退出条件 1>
# ── 绑定块 (锚定目标/工作区/规范的核心机制) ──
binding:
  workspace: <项目相对路径或绝对路径>             # 必填, 单值
  packages:                                       # 0-N 个, 按 service / domain / 等分类
    - service:guardian
  targets:                                        # 0-N 个, plan 直接动到的具体实体
    - team:omnicompany_cli_phase3
    - material:omnicompany.identity_record
applicable_standards:                             # 0-N 个, 推导出来 + 手补
  - standards/concepts/material.md
expected_completion: <YYYY-MM-DD>                 # 推荐, 预期完成日 (跟 §2 起止日一致)
ttl_days: <int>                                   # 可选, 填写后守护按此扫超期 (推荐 30-90)
---
```

存量 plan 只有平铺字段、无 binding 块 — 不违规, 下次实质性更新时补 binding。新立 plan 必带 binding.workspace。

### 3.1 binding.workspace (必填, 单值)

plan 必须显式绑定一个 workspace. workspace 是 plan 实施过程中所有写盘动作的隔离边界 — 跑代码 / 落数据 / 改文件都在这个 workspace 内.

填法:

- 项目相对路径: `omnicompany/` (整个 omnicompany 项目根, 适合跨服务大改)
- 子目录: `omnicompany/src/omnicompany/packages/services/guardian/` (单服务包改动)
- 独立 workspace: `omnicompany/data/_workspaces/<team>/<job>/` (一次 team 跑独立工作区)

**漏填后果**: 跑代码时不知道工作区边界 — 跨 plan 写盘互相污染, 守护扫到大量"身份不明的 workspace 写入". 这违反 workspace_isolation_and_test_discrimination 第一条铁律.

### 3.2 binding.packages (0-N 个, 推荐有)

plan 触及的 package 列表. 每条带前缀区分 package 类型: `service:<name>` / `domain:<name>` / `core:<name>` (核心层) / `protocol:<name>` (协议层).

填法举例:

- 单服务包改动: `service:guardian`
- 跨服务: `service:guardian` + `service:omnicompany`
- 业务领域改动: `domain:demogame`
- 核心层改动: `core:cli` + `core:bus`
- 跨业务跨核心: 多条都列

**为什么有这字段**: 让 plan 自动应用对应 package 的 standards (例如 `service:guardian` 自动应用 service 类规范). 不显式绑 package 的 plan, 守护不知道这份 plan 该按哪些规范审.

### 3.3 binding.targets (0-N 个, 推荐有)

plan 直接动到的具体实体. 用 dashboard wikilink 已支持的 entity type 前缀:

- `team:<name>` / `worker:<name>` / `material:<id>` / `agent:<name>` / `hook:<name>` / `tool:<name>` / `plan:<topic>` (引用其他 plan)

填法举例 (一份 CLI 实装 plan):

```yaml
targets:
  - team:omnicompany_cli_phase3
  - worker:register_identity
  - worker:register_material
  - material:omnicompany.identity_record
  - material:omnicompany.write_credential
```

**为什么有这字段**: dashboard 渲染 plan 时, 自动给 targets 里每个实体生成跳转链接, 让人读 plan 时直接点进对应实体的 view.

### 3.4 applicable_standards (0-N 个, 自动 + 手补)

plan 自动应用的 standards 列表. 推导规则:

- binding.targets 含 `material:*` → 自动加 `standards/concepts/material.md`
- binding.targets 含 `worker:*` → 自动加 `standards/concepts/worker.md`
- binding.targets 含 `team:*` → 自动加 `standards/concepts/team.md`
- 同上对 agent / hook / tool / data / template
- binding.packages 含 `core:cli` → 自动加 `standards/cli/omnicompany_cli.md`
- 任何 plan 都自动加 `standards/_global/code.md` 跟 `standards/_global/llm_first.md`

调用方可以**手动加**额外 standards (例如 `standards/protocol/design_md_template.md` 当 plan 涉及多份 DESIGN.md 时).

**为什么有这字段**: plan 实施过程中, 守护跟 lint 引擎按 applicable_standards 列表选校验规则. 不在列表里的规范不查, 在列表里的全查. 这让 plan 跟规范的关系**显式**而不是隐式.

### 3.5 expected_completion + ttl_days

- **expected_completion**: 预期完成日 ISO 8601, 跟 §2 起止日字段值一致 (冗余但显式让 dashboard 能直接读).
- **ttl_days** (可选): 整数, 填写后守护按此扫超期. 推荐值 30-90. 个人开发者排期不固定时可不填, 守护仅在字段存在时才扫.

## 四、 防债务 / 防偏移 / 防遗忘 三件事

### 4.1 防债务 — 收尾时强制审实施过程产生的债务

plan 收尾 (status 改 completed 之前) 必走"债务审议":

1. 实施过程中累积的债务列入 §7 风险段 (或独立 §15 债务清单段)
2. 每项债务标注: 债务类型 (代码 / 文档 / 测试 / 配置) + 严重度 (blocker / major / minor) + 跟进 plan id (如有)
3. 没有"待补"的开放项 — 每项债务要么标"已解决", 要么"已新立 plan 接手 (链接 [[plan:NEW-TOPIC]])", 要么"接受作为永久债务并归档"

dashboard 在 plan view 里显示债务清单, 让 plan 之间的债务流转可见.

### 4.2 防偏移 — 执行过程中检测实际行为偏离计划

偏移是 agent 长程自主运行中最常见的问题. 偏移不是指 plan 文档本身被改了, 而是**执行中的实际行为**跟计划定义的退出条件、执行方法论、执行步骤产生了严重出入.

**偏移检测的锚点** (agent 执行中应周期性自查):

1. **退出条件对齐**: 当前正在做的事, 是否仍在朝 plan 定义的退出条件/验收标准推进? 还是已经在解决计划未定义的问题?
2. **方法论对齐**: 实际采用的方式, 是否符合 plan 路线图约定的阶段拆分和执行方式? 还是已经绕开了计划路径在走别的路?
3. **步骤对齐**: 当前执行的步骤, 是否在 plan 路线图的某个阶段内? 还是已经做了计划没提到的事?

**偏移发生时的处置**:

- **轻度偏移** (在做计划内的事, 但顺序或细节有调整): 记录到决策日志, 继续执行
- **中度偏移** (在做计划边缘的事, 退出条件仍可达): 暂停, 向用户报告偏移情况, 等用户决定是继续还是回到原路径
- **重度偏移** (实际行为跟退出条件已经无关): 立即停止当前执行, 向用户报告. 如果新方向有价值, 新立 plan 承接, 旧 plan 按实际完成度收尾

**文档层面的大改** (主题/起止/输出整体变更) 也仍然要求新立 plan, 不许偷改方向:

1. 旧 plan 标 status=deprecated, OmniMark 头加替代指针 (例如 `replaced_by: plan:NEW-TOPIC`)
2. 新立 plan, 头部含 `replaces: [plan:OLD-TOPIC]` 指回旧的
3. 不许修旧 plan 假装从一开始就是新方向 (历史不可追溯, 失败教训丢失)

### 4.3 防遗忘 — 计划关键部分在 agent 上下文中持续可用

遗忘不是指自然时间上"忘了还有这个 plan", 而是 agent 在 compact / 新 session 后, 计划的关键部分不再在上下文中, 导致执行脱锚.

**plan 必须定义"核心摘要"** — 一段足够短但信息完整的文本, 包含:

1. **退出条件**: 这个计划做到什么程度算完 (从验收标准提炼)
2. **当前阶段**: 路线图中正在执行哪个阶段, 该阶段的关键产物是什么
3. **执行约束**: 不能做什么 / 必须遵守什么方法论 (从风险和假设提炼)

**载入机制**:

- plan 绑定 session 时, 核心摘要自动注入 system-reminder (当前已实现 plan 头部摘要注入)
- compact 发生后, 核心摘要作为恢复上下文的第一优先载入项
- agent 可通过命令 (如 `omni plan show <id>`) 随时重新载入完整 plan 内容

**核心摘要的维护**: 每个阶段切换时更新"当前阶段"部分. 核心摘要放在 plan 目录下独立文件 `brief.md`, 而非嵌在 plan.md 正文中 (见分文件规则).

> **注**: 旧版本的"防遗忘"定义为 ttl_days 自然时间过期守护机制. ttl_days 字段保留为**可选** (个人开发者排期不固定, 强制过期提醒无意义), 有需要时填写, 守护仅在字段存在时才扫.

## 五、 plan 八节固定结构 (沿用)

§1 主题 / §2 起止日期 / §3 参与方 / §4 关联材料 (上游 + 输出) / §5 阶段拆分 / §6 收尾条件 / §7 风险 / §8 收尾归档位置.

每节内文允许用 wikilink 引用其他实体. 推荐用 mermaid 块画阶段拆分图跟假设树 (在 compact summary 时机才必填的 §11).

具体填法见 `omnicompany/templates/plan/向导.md` (plan 模板的填空指引).

## 六、 compact summary 时机的扩展节

§9 工作性假设 / §10 验证情况 + 验证背后的假设 / §11 假设树 / §12 假设验证情况 + 一句话总览 / §13 实施情况 (实验日志 + 数据库证明).

这五节**只在 trigger 时机** (compact 前 / 用户要求总结 / 开新 team / 验证收尾) 必填. 普通 plan 编辑可省.

完整规程见 `standards/protocol/l2_session_summary_protocol.md`.

## 七、 plan 目录树 (规范权威定义)

plan 按 binding.packages 分类落到下面目录结构. **本节定义的结构是规范权威, 现存平铺是历史债务跟规范不一致 = 现存内容要按规范迁移, 不是规范让步**.

```
omnicompany/docs/plans/
  _infra/                                   # 没绑 package 的基础设施 plan
    [2026-05-01]OMNICOMPANY-CLI-PHASE3/
      plan.md
      compact_summary_*.md
  _cross/                                   # 跨多 package 的 plan
    [2026-04-25]NAMING-MIGRATION/
      plan.md
  service/<service_name>/                   # 单 service 绑定的 plan
    guardian/
      [2026-04-28]GUARDIAN-RULE-EXPANSION/
        plan.md
    omnicompany/
      [2026-05-08]REGISTRY-IMPLEMENTATION/
        plan.md
  domain/<domain_name>/                     # 单 domain 绑定的 plan
    demogame/
      [2026-04-26]SEASON-BOOK-RESOLVER/
        plan.md
    voxelcraft/
      [2026-04-25]NORTH-STAR-ROADMAP/
        plan.md
  _archive/                                 # 完成 + 冷静期 (2-4 周) 后归档
    <按上面同结构镜像>
```

主题代号沿用大写蛇形 + 不挂版本号 (跟现有规则一致).

## 八、 standards 目录树 (规范权威定义)

standards 按 applicability 分类落到下面目录结构, 让 plan 的 applicable_standards 字段引用路径稳定. **本节定义的结构是规范权威, 现存平铺要按规范迁移**:

```
omnicompany/docs/standards/
  _global/                                  # 跨概念跨业务的全局规范
    code.md
    llm_first.md
    counterexample_ledger.md
    distributed-docs.md
    terminology.md
    verification_invariants.md
    workspace_isolation_and_test_discrimination.md
    information_sufficiency.md
  concepts/                                 # 八种基础概念规范
    material.md
    worker.md
    team.md
    agent.md                                # 新立, 当前没单独
    hook.md                                 # 新立
    tool.md                                 # 新立
    data.md                                 # 新立
    doc.md                                  # 新立, 含 plan / report / DESIGN 三种
    template.md
  cli/                                      # CLI 跟设施层规范
    omnicompany_cli.md
    sandbox.md
    omni-header.md
  protocol/                                 # 协议层 / 跨 session 规范
    l2_session_summary_protocol.md
    design_md_template.md                   # 注意: 后续合并到 concepts/team.md 因为 DESIGN 归 team
  _meta/                                    # 规范本身的元规范
    standards-index.yaml
```

迁移由本规范配套的 todo 第 3 步"概念分类 + 目录迁移"做, 用全文 grep + sed / Python 脚本批量改引用. plan 跟 standards 两份目录树同期迁, 不分两次. 迁完之后整个项目里引用 plan / standards 的位置全部按本规范的新路径走.

## 九、 实例化协议跟模板的关系

plan 实例化按 `omnicompany/templates/plan/` 模板四件套走. 当前 plan 模板的骨架.md 已含 §1-§8 + §9-§13 节, 但 binding 块跟新目录结构还没体现, 阶段六 (todo 第 6 项 — 模板范本升级) 同步加上.

实例化命令 (阶段三 CLI 实装的事): `omni new plan --topic=TOPIC-CODE --bind-workspace=<path> --bind-package=<list> --bind-targets=<list>`. CLI 自动:

1. 推导 applicable_standards (按 targets / packages 类型)
2. 决定目标目录 (按 packages 是否单一 / 跨多 / 没绑分别走 service/domain/_cross/_infra)
3. 拷骨架 + 填头部绑定块 + 显示向导

## 十、 反模式

下面这些做法在新规范下确认会引发问题:

- **plan 没 binding.workspace 字段**: 跑实施时不知道工作区边界, 跨 plan 互相污染. 守护扫到必报.
- **plan 引用其他实体不用 wikilink**: 例如 §4 关联材料里写 "csv_to_md 服务包的 ParsedRows 材料", 不写 `[[material:csv_to_md.parsed_rows]]`. dashboard 没法跳转 + 自动检测引用失效困难. 守护扫到给 warn.
- **plan 跨度超过 3 个月**: 那是项目不是计划, 应当拆. 阶段拆分 ≤ 8 个的判定是辅助 (能拆 ≤ 8 个阶段就算 plan 范围, 拆不下就是项目).
- **plan 收尾不走债务审议**: 直接改 status=completed 而不审实施过程产生的债务. 后续接手的人不知道遗留, 重复踩坑.
- **plan 主题/起止/输出大改还是同一份 plan**: 历史不可追溯, 失败教训丢失. 必新立替代 plan, 旧的 deprecated.
- **plan 头部 binding.targets 跟 §4 关联材料对不上**: 头部声明的目标跟正文里实际写的关联材料不一致, 头部就成了装饰, 失去锚定意义.
- **plan 落到 docs/plans/ 平铺不进 _infra/_cross/service/<x>/ 子目录**: 跟新目录结构不一致. 大量 plan 平铺一层不可读.
- **standards 引用用绝对路径 + 没用 applicable_standards 字段**: 改 standards 目录路径时 plan 引用失效. 用 applicable_standards 列表 + 路径以 `standards/<分类>/` 开头能让批量替换更可控.
- **ttl_days 填超长 (例如 365)**: 如果选择填 ttl_days, 不要填无意义的大数. 超过 3 个月的 plan 应当拆成多个.

## 十一、 跟其他规范的关系

- **跟 distributed-docs.md**: distributed-docs 立六域结构, plan 在"过程记录" 域. 本规范是 plan 这一类内容的具体规则, 不矛盾.
- **跟 omni-header.md**: plan 头部 OmniMark v3 五字段必填, 位于顶部 frontmatter 之后、正文之前. 不冲突.
- **跟 l2_session_summary_protocol.md**: l2 协议立 8 项 checklist, 本规范的 §9-§13 五节是这 8 项的具体落到 plan 章节里. 一一对应.
- **跟 worker / material / team 规范**: applicable_standards 自动推导逻辑跟这三份规范挂钩 — targets 含哪种类型就拉哪份规范进来.
- **跟 template.md**: plan 是 template 系统的一种 kind, 走 template 标准的实例化协议. 现在 plan 模板独立 kind, 阶段六 (概念分类) 改成 doc 下的子 kind.

## 十二、 这份规范的版本演进

- 2026-05-01 立 v1: 含 binding 块 + 三防机制 + 新目录结构 + dashboard wikilink 接通
- 2026-06-13 修订 v1.1: 头部三套模式统一为顶部唯一 frontmatter (平铺字段 + binding 同居一块, 解析器已接);
  本文件确立为 plan 唯一权威, plan_template.md 降为 protocol 层模板细则

后续修订:

- 概念分类做完后, 本规范合并到 `standards/concepts/doc.md` 下作 plan 子 kind 节. 旧路径 `standards/concepts/plan.md` 留 stub 指向新位置.
- omnicompany CLI 注册中心实装后, `omni new plan` 命令按本规范第九节实例化协议接通.
- dashboard 按本规范第 2.3 节扩展的三类 wikilink (standard / workspace / package) 加 entity handler 后, 现存按本规范写的 plan 自动激活功能 (跳转 / hover / 类型识别) — 本规范不需要回头改, dashboard 实装跟规范同语义即可.
