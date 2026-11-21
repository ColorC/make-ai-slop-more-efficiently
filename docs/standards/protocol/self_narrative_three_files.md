<!-- [OMNI] origin=ai-ide domain=standards/protocol ts=2026-05-04T09:00:00Z type=doc status=active agent=ai-ide -->
<!-- [OMNI] summary="自我叙事三件套规范 — README + DESIGN + SKILL 各管一段, 递归适用所有层级 (项目根/domain/service/package). 父级指针式引用子级不复制" -->
<!-- [OMNI] why="DESIGN 七节模板把目的+构成+操作手册混在一起, 触发用户三反例 (重复/模糊/错乱). 拆成三件后边界清晰, 业界范式 (Diataxis/ADR/Good Docs) 已成熟可借鉴" -->
<!-- [OMNI] tags=standards,self-narrative,template,readme,design,skill,diataxis -->
<!-- [OMNI] material_id="material:standards.protocol.self_narrative_three_files.md" -->

# 自我叙事三件套规范 · README + DESIGN + SKILL

> 立 2026-05-04. 取代旧 design_md_template.md 的总叙事职能.
> **2026-06-13 起为 DESIGN/README/SKILL 的现行唯一权威**（用户裁决"规范冲突以新的为准"）:
> DESIGN 必需节为六节（核心目的归 README）, Guardian OMNI-034c / docauthor / distributed-docs
> / design_md_template.md（降为本规范的模板细则）已全部对齐。

---

## 一 · 这套规范是什么

omnicompany 的任何层级 (项目根 / domain / service / package) 都通过**三个文件**讲清自己的自我叙事:

| 文件 | 段 | 回答 | 业界对应 |
|---|---|---|---|
| **README** | 设计目的 | 我**为什么**存在? 语境 / 目的 / 规划 | [Diataxis · Explanation](https://diataxis.fr/) |
| **DESIGN** | 构成 | 我**是什么**组成的? 内容 / 构成 / 架构 | [Diataxis · Reference](https://diataxis.fr/) + [ADR / MADR](https://adr.github.io/) |
| **SKILL** | 能做+入口 | 我**怎么用**? 可适用范围 / 操作手册 | [Diataxis · How-to guides](https://diataxis.fr/) |

三件递归适用 — 每个层级都有自己一套, 形态一致, 内容深度递减, 父级到子级**指针式引用不复制**.

### 跟用户三反例的对应

- **不重复**: 三文件各管一段, 同一事实只一个权威源, 别处只引用
- **不模糊**: 找什么知道去哪 (目的→README / 构成→DESIGN / 用法→SKILL)
- **不错乱**: 每层自管自身, 父级到下一层就停 (私域不暴露到父级)

---

## 二 · 三件边界细则

### 2.1 README 管什么

**管**:
- 这一层是什么 (一句话定位)
- 解决什么问题 / 不解决什么问题
- 设计目的 + 当下能认知的最终目标
- 规划 / 路线图 / 里程碑
- 构成: **指针指向**子模块的 README (不复制子模块认知)

**不管**:
- 详细 API 接口 (那是 DESIGN)
- 详细架构决策 (那是 DESIGN)
- 详细操作步骤 (那是 SKILL)

**风格**: 叙事 / 友好 / 强动词. 类比 [The Good Docs Project README guide](https://www.thegooddocsproject.dev/template/readme): "项目第一印象, 决定读者是否继续往下读".

### 2.2 DESIGN 管什么

**管**:
- 对外接口清单 (API / Material / Worker / Team)
- 架构决策 (D1 / D2 / ... ADR 风格, 含决策上下文跟取舍)
- 数据流 / 拓扑
- 内部模块清单 (指针指向子模块的 DESIGN)
- 已知局限 (架构层面)

**不管**:
- 设计目的 (在 README)
- 用户怎么用 (在 SKILL)
- 业务私有逻辑细节 (在子模块的 DESIGN, 父级只指针)

**风格**: 严谨 / 结构化 / 可定位. 类比 [Google Design Docs](https://www.industrialempathy.com/posts/design-docs-at-google/): "高层实施策略 + 关键设计决策 + 取舍".

### 2.3 SKILL 管什么

**管**:
- 可适用范围 (什么场景用 / 什么场景不用)
- 前置条件 (用之前需要什么)
- 操作步骤 (1-2-3-4-5 顺序, 任务导向)
- 验证 (怎么知道做对了)
- 故障排查 (常见错 + 修)
- 入口指针 (CLI 命令 / API 调用 / 子模块 SKILL)

**不管**:
- 设计目的 (在 README)
- 内部架构 (在 DESIGN)

**风格**: 操作 / 步骤化 / 写给最 inexperienced 用户. 类比 [Diataxis 任务导向 how-to guides](https://diataxis.fr/how-to-guides/): "用户带着具体目标进, 帮 ta 达成".

### 2.4 三件交叉的判断

不知道某段内容写哪里时, 问三个问题:

1. 这段是在解释**为什么**这层存在? → README
2. 这段是在描述**架构是什么样**? → DESIGN
3. 这段是在告诉读者**怎么用**? → SKILL

如果一段同时回答多个问题 — 拆开. 如果觉得拆不开 — 八成是回答错层 (实际只回答一件, 但混进了别件的细节, 把别件细节剥离即可).

---

## 三 · 递归 + 指针式构成

### 3.1 三件适用所有层级

```
项目根/
├── README.md        语境/目的/规划. 构成段指向 → 各 domain 的 README
├── DESIGN.md        架构. 内部清单指向 → 各 domain 的 DESIGN
└── SKILL.md         操作总入口. 指针指向 → 各 domain 的 SKILL

packages/domains/<X>/
├── README.md        domain 自身的语境/目的. 构成段指向 → 各 service 的 README
├── DESIGN.md        domain 架构. 内部清单指向 → 各 service 的 DESIGN
└── SKILL.md         domain 操作手册. 指针指向 → 各 service 的 SKILL

packages/services/<Y>/
├── README.md        service 的语境/目的
├── DESIGN.md        service 架构 (七节, 见 §五)
└── SKILL.md         service 操作手册 (CLI / Material 入口 / 验证)

packages/services/<Y>/<package>/
├── README.md        package 的语境/目的 (可选, 简单 package 可省, 用 OmniMark 头 summary 顶替)
├── DESIGN.md        package 架构 (可选, 同上)
└── SKILL.md         package 操作手册 (可选, 同上)
```

### 3.2 指针式构成 (核心)

父级的"构成"段 / "内部清单"段 / "入口指针"段是 **超链接式声明**, 不复制子级内容. 例子:

✓ 正确 (项目根 README 的构成段):
```markdown
## 构成

omnicompany 由以下 domain 组成 (各自有独立的 README/DESIGN/SKILL):

- packages/domains/demogame/ — 游戏数据自动配置 SDK
- packages/domains/voxelcraft/ — Minecraft Java mod 自动开发
- packages/services/_core/ — 核心层服务 (registry / guardian / runtime)
- ...
```

✗ 错误 (项目根 README 替子级写认知):
```markdown
## 构成

demogame domain 提供赛季手册 / 装饰抽奖 / 商店等 13 张表的自动配置生成,
通过 ResolverAgent 推断字段语义, 通过 BenchmarkValidator 校验输出...
```

为什么错? 父级在替 demogame 复制 demogame 内部认知. 这是用户三反例之一 (重复 → 拼图碎片化, 哪份才是权威搞不清).

### 3.3 私域到 domain 即止

父级 (项目根) 的叙事在引用到 domain 这一级时停止. domain 内部具体业务规则 (例: demogame 的赛季 N 经济配置 / voxelcraft 的方块定义) **不暴露到项目根**叙事里. 想看 → 点 domain 的 README 进去, domain 自管.

如果 domain 自己没建三件 → 那是 domain 的责任, 不是父级的责任. 父级不替补.

### 3.4 自给自足

每层三件必须自己有内容. 点进 domain 如果 README 是空的 → domain 没履行自我叙事职责, 而不是"父级有信息但漏写". 检测机制: Guardian 扫"指针指向的目标是否有内容" (留待第二阶段做).

---

## 四 · README 模板

### 4.1 节结构

```markdown
<!-- [OMNI] origin=<origin> domain=<domain> ts=<ISO8601> type=doc status=active -->
<!-- [OMNI] summary="..." -->
<!-- [OMNI] why="..." -->
<!-- [OMNI] tags=readme,<scope>,... -->

# <模块名>

> 一句话定位 (≤ 30 字, 强动词开头)

## 这是什么

<2-3 段叙事, 回答: 我是什么 / 在系统里扮演什么角色>
<不写实现细节, 不写架构, 不写具体怎么用>

## 解决什么 / 不解决什么

**解决**:
- <问题 1>
- <问题 2>

**不解决**:
- <边界外的事 1, 这是给读者明确"我不是干这个的">
- <边界外的事 2>

## 设计目的与最终目标

<段落形式. 回答: 为什么要做这个 / 当下能认知的最终目标 (会随认知更新, 不假装一锤定音)>

## 规划

<里程碑或当前阶段. 当前 / 下一步 / 远景三层. 跟 docs/PROGRESS.md 的对应段保持一致, 不是替代它>

## 构成

<指针式列表, 指向子模块的 README. 每条 1 行, 不展开>

- [<子模块 1 名>](<子模块路径>/README.md) — <一句话说子模块管什么>
- [<子模块 2 名>](<子模块路径>/README.md) — <一句话>
- ...

## 想了解更多

- 架构 → 看 DESIGN.md
- 怎么用 → 看 SKILL.md
- <可选: 跨层级的关键参考>
```

### 4.2 反例

✗ README 写架构决策 (那是 DESIGN)
✗ README 写 CLI 命令清单 (那是 SKILL)
✗ README 在"构成"段把子模块的设计目的复制过来 (违反指针式)

### 4.3 项目根级 README 的特殊位置

仓库根 README.md 是**整个项目第一接触点** (GitHub 默认渲染). 写法可适度调整:
- 加项目徽章 / 安装快速开始 (常见 GitHub 习惯)
- "构成"段指向 docs/ 跟 packages/ 入口
- 在结尾贴常用文档链接表 (PROGRESS / 控制结构 / standards 入口)

---

## 五 · DESIGN 模板 (从七节缩窄)

### 5.1 跟现行七节模板的关系

现行 design_md_template.md 七节: 状态 / 核心目的 / 核心接口 / 架构决策 / 数据流-拓扑 / 已知局限 / 参考资料 + 可选第 8 节 接收意愿.

新规范下 DESIGN 范围收窄:
- "核心目的" 段 → **抽到 README** (这是设计目的, 不是构成)
- "状态" 段 → 留 DESIGN (status=skeleton/design/active/deprecated 仍归 DESIGN, 是构成的元信息)
- 其余 (核心接口 / 架构决策 / 数据流-拓扑 / 已知局限 / 参考资料) → DESIGN 自管
- "接收意愿" → 是构成段的扩展 (我能吸收什么主题), 留 DESIGN

迁移期: 旧 DESIGN.md 不立即改, 跟存量回填一起做 (后续 plan).

### 5.2 节结构

```markdown
<!-- [OMNI] origin=<origin> domain=<domain> ts=<ISO8601> type=doc status=<skeleton|design|active|deprecated> -->
<!-- [OMNI] summary="..." -->
<!-- [OMNI] why="..." -->
<!-- [OMNI] tags=design,<scope>,... -->

# <模块名> · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 SKILL.md.

## 状态
- **版本**: V<n>
- **成熟度**: skeleton | design | active | deprecated
- **下一步**: <1 行最紧迫的下一步>

## 核心接口

<对外暴露的关键类/函数/Material/Worker/Team. 列表形式, 含源码链接>

例:
- `PipelineRunner.run(initial_input)` — 跑一份 team
- `MANIFEST_REQUEST` Material — `{target_service_path: str}`
- ...

## 架构决策

### D1 — <决策标题 ≤ 20 字>

**决策**: <做了什么>

**理由**: <为什么这么做, 考虑过什么替代>

**取舍**: <得到什么, 放弃什么>

### D2 — <决策标题>
...

## 数据流 / 拓扑

<输入→处理→输出. 或关键组件协作图. ASCII art 可接受>

## 内部构成

<指针式列表, 指向子模块的 DESIGN. 跟 README 构成段对称, 但这里是技术性指针>

- [<子模块 1>](<路径>/DESIGN.md) — <一句话说技术职责>
- ...

## 已知局限

1. **<局限标题>** — <现状 + 未来升级路径>
2. ...

## 接收意愿 (可选, 基础设施模块建议填)

<格式参考 现行规范 §九. welcome_themes / hard_constraints / soft_preferences / maturity_preference>

## 参考资料

- <源码路径 / 关联 plans / 外部链接>
```

### 5.3 反例

✗ DESIGN 写"为什么要做这个" 长篇大论 (那是 README)
✗ DESIGN 写"怎么调用 CLI" 步骤 (那是 SKILL)
✗ DESIGN 在"内部构成" 段把子模块架构复制过来 (违反指针式)

---

## 六 · SKILL 模板

### 6.1 跟现行 .claude/skills/ 形态的关系

现有 `.claude/skills/omnicompany-dev/SKILL.md` 是 **Claude Code skill 框架**的产物, 用于 AI IDE 调起. 它有 YAML frontmatter (name / description / user-invocable / disable-model-invocation / argument-hint) + 内容.

**新规范下 SKILL.md 是更广义的"操作手册"** — 不只是给 Claude Code 调起, 也给:
- 人类用户读
- 其他 agent (非 Claude Code) 读
- 自动化工具 (CLI 帮助 / 文档生成器) 消费

形态: 保留现有 frontmatter (向后兼容 Claude Code skill), 但内容结构按新规范.

### 6.2 节结构

```markdown
---
name: <module-name>
description: <一句话, 让 AI 决定何时调起这个 skill 的依据>
user-invocable: true | false
disable-model-invocation: false
argument-hint: "<可选, CLI 风格参数提示>"
---

<!-- [OMNI] origin=<origin> domain=<domain> ts=<ISO8601> type=doc status=active -->
<!-- [OMNI] summary="..." -->
<!-- [OMNI] why="..." -->
<!-- [OMNI] tags=skill,<scope>,... -->

# <模块名> · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 DESIGN.md.

## 适用范围

**用我**: <什么场景适合用这个模块>
**不用我**: <什么场景应该用别的, 指向别的 SKILL>

## 前置条件

<用之前需要什么. 例: omni session 已绑 / G2 索引已 build / xxx service 已注册>

## 操作步骤

### 场景 A: <最常见操作>

1. <步骤 1, 包含具体命令 / 代码片段>
2. <步骤 2>
3. <验证: 怎么知道做对了>

### 场景 B: <次常见操作>
...

## 入口清单

<指针式 — CLI 命令 / API / 子模块 SKILL>

| 入口 | 用途 | 详细 |
|---|---|---|
| `omni xxx <args>` | <用途一句话> | <链接到详细帮助> |
| `<API 函数>` | <用途> | <链接到 DESIGN 对应接口段> |
| [子模块 SKILL](<子路径>/SKILL.md) | <子模块负责什么操作> | (递归指针) |

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| <报错信息 / 异常表现> | <根因> | <步骤> |
| ... | ... | ... |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 → DESIGN.md
- 相关规范 → <跨层级 standards 链接>
```

### 6.3 反例

✗ SKILL 写架构决策 (那是 DESIGN)
✗ SKILL 解释"为什么这么设计" 长篇 (那是 README)
✗ SKILL 写"未来可能扩展" 远景 (那是 README 的规划段)

---

## 七 · 工作流约束 — 改前改后校对画像 + 模板必用

> 2026-05-04 用户立. 这是三件套规范的执行配套, 没三件套也没"画像可校对".

### 7.1 改前必读

任何对 service / domain / 模块内文件 (`.py` / `.yaml` / `.md` / 配置 / 入口) 的改动, **改前必读相关 README + DESIGN + SKILL** (即"画像"). 没读过画像就动手 = 盲改, 大概率漂移.

| 改什么 | 必读什么 |
|---|---|
| service 内 `.py` 文件 | 该 service 的 DESIGN (架构) + SKILL (操作影响) |
| service 配置 (`manifest.yaml` / `formats.py` / Worker 列表) | DESIGN + 可能 README |
| service 入口 (`run.py` / `pipeline.py` / `team.py`) | 三件全读 |
| 跨 service 改动 | 涉及的所有 service 的 README + DESIGN + 顶层 archmap.yaml |

### 7.2 改后必校对

改完后, **校对画像跟现实是否还一致, 不一致就更新画像**.

| 改了什么 | 必更新什么 |
|---|---|
| 加 Worker / Material / Team | DESIGN 的"核心接口"段 + SKILL "入口清单"段 |
| 加 CLI 命令 | SKILL 入口表 + 故障排查 (如有新坑) |
| 改架构决策 | DESIGN 加新决策 `### D-N`, 不复用旧 D 编号 |
| 改设计目的或远景 | README "设计目的与最终目标" 或"规划" 段 |
| 改命名 / 路径 | 跨文件 cross-reference 链接全扫一遍 |

**反例**: 改完代码不改画像 = 画像跟现实漂移, 下次有人按画像理解会撞错. 这条规范是为防漂移, 跟 CORE-SELF-STABILITY 第二阶段 漂移检测之间有过渡价值 (人工自查 → 后续 Guardian 自动检测).

### 7.3 模板必用

新建 README / DESIGN / SKILL **必从本文档 §四 / §五 / §六 模板 cp 起手**, 不从零写, 不从原 DESIGN.md 改写.

- cp 模板节结构 → 填具体内容
- 节结构不动 (后续 Guardian 类规则会校验)
- 不熟悉某节 → 留 `<!-- TBD: 此节尚未填充 -->` 标记 + `status=skeleton`, 不省略
- **反例**: 2026-05-04 做 4 份 service 样例 (registry/docauthor/doctor/guardian) 时**没严格用模板**, 各份格式略有差异 (SKILL 故障排查表行数不一 / 命令清单格式有差) — 这正好命中"不重复" 反例之一. 此规范为防再发生.

### 7.4 漂移信号 (改后未校对画像的征兆)

撞上以下任一 → 立即更新画像, 不带病往前走:

- 画像里的"30 Worker" 跟实际 Worker 数对不上
- SKILL 入口清单里的命令实际跑不通或参数变了
- DESIGN 数据流 ASCII art 跟现行 Team 拓扑不符
- 跨服务 cross-reference 链接 404
- 远景段提的"下一步" 早已完成或已跳过
- README 一句话定位跟代码实际职责对不上

---

## 八 · 现有零件迁移说明

### 7.1 docs/README.md vs 项目根 README.md

- docs/README.md — 当前是 docs/ 目录索引, 保留. **它不是项目根 README**.
- 项目根 (仓库根) README.md — **当前缺**, 待建. 按本规范 §四写.

不混淆: docs/README 给 docs 内导航, 项目根 README 给项目自身叙事.

### 7.2 DESIGN.md 七节的拆分迁移

存量 DESIGN.md (例: [packages/services/_authoring/docauthor/DESIGN.md](../../../src/omnicompany/packages/services/_authoring/docauthor/DESIGN.md)) 按以下顺序迁移:

1. **抽"核心目的" 段** → 新建同目录 README.md
2. **DESIGN 留下**: 状态 / 核心接口 / 架构决策 / 数据流-拓扑 / 内部构成 (新加) / 已知局限 / 接收意愿 / 参考资料
3. **新建 SKILL.md** — 写怎么用这个 service

迁移不要求一次性完成, 跟存量补齐一起分批跑.

### 7.3 .claude/skills/ vs 各模块 SKILL.md

- `.claude/skills/omnicompany-dev/SKILL.md` / `omnicompany-use/SKILL.md` — Claude Code 顶层 skill 入口, 保留, 它们是用户调起 omnicompany 的"门面 skill"
- 各 module 的 SKILL.md — 是该 module 的"操作手册", 由门面 skill 在恰当时引导用户跳转

不替代关系, 是层级关系: 门面 skill 在顶层提示用户"做 X 看 module Y 的 SKILL", module Y 的 SKILL 有具体操作手册.

---

## 九 · Guardian 检测规则 (建议, 留待后续)

第一阶段不立即加, 留作第二阶段 (诊断+分析能力上线时一起):

| 编号 | 检查项 | Severity |
|---|---|---|
| (待定) | 项目根 / domain / service 缺三件之一 (active 状态) | MEDIUM |
| (待定) | README "构成" 段 / DESIGN "内部构成" 段含子模块认知复制 (启发式 LLM 检测) | LOW |
| (待定) | 三件之间的指针失效 (链接 404) | HIGH |
| (待定) | DESIGN 含"为什么" 长段落 (应在 README) | LOW (LLM 启发式) |

具体规则跟 OMNI 编号待第二阶段定. 当前 OMNI-034 (DESIGN 七节齐全) 在迁移期保留, 迁移完成后升级.

---

## 十 · 一句话总结

**自我叙事三件套 = README 写为什么 + DESIGN 写组成 + SKILL 写怎么用. 每层 (项目根/domain/service/package) 都自管自身三件, 父级到子级指针式引用不复制. 三反例 (重复/模糊/错乱) 0 命中**.

---

## 附录 A · 业界范式速记

| 范式 | 来源 | 跟我们对应 |
|---|---|---|
| **Diataxis** | [Daniele Procida](https://diataxis.fr/) | 四象限 (Tutorials/How-to/Reference/Explanation), 我们 README=Explanation / DESIGN=Reference / SKILL=How-to (Tutorials 不要, 项目内部用不上) |
| **ADR / MADR** | [adr.github.io](https://adr.github.io/) | 架构决策记录, DESIGN 的"架构决策" 段直接套 ADR 风格 (D1/D2/...) |
| **Google Design Docs** | [industrialempathy.com](https://www.industrialempathy.com/posts/design-docs-at-google/) | 高层实施策略 + 关键设计决策 + 取舍, 跟 DESIGN 对齐 |
| **The Good Docs Project** | [thegooddocsproject.dev](https://www.thegooddocsproject.dev/template/readme) | README 模板范式, "项目第一印象" / 友好语气 / 强动词 |
| **Operations Manual** | 业界共识 | SKILL 风格参考 — 步骤化 / 写给最 inexperienced 用户 / 含故障排查 |

## 附录 B · 三件套层级展开样例 (omnicompany 项目根)

```
omnicompany/                    ← 仓库根
├── README.md                   ← 项目根叙事 (待建)
├── DESIGN.md                   ← 项目根架构 (待建, 现有 docs/ARCHITECTURE.md 是雏形)
├── SKILL.md                    ← 项目根操作总入口 (待建)
├── docs/
│   ├── README.md               ← docs 目录索引 (现有, 保留, 不是项目根 README)
│   └── ...
├── packages/
│   ├── domains/demogame/
│   │   ├── README.md           ← demogame domain 叙事 (待建)
│   │   ├── DESIGN.md           ← demogame domain 架构 (待建)
│   │   └── SKILL.md            ← demogame 操作手册 (待建)
│   └── services/_core/registry/
│       ├── README.md           ← registry service 叙事 (待建)
│       ├── DESIGN.md           ← registry 架构 (现有, 七节, 待按 §5.2 收窄)
│       └── SKILL.md            ← registry 操作手册 (待建)
```

## Sources

- [Diataxis Documentation Framework — Daniele Procida](https://diataxis.fr/)
- [Architectural Decision Records (ADR)](https://adr.github.io/)
- [MADR — Markdown Architectural Decision Records](https://adr.github.io/madr/)
- [Design Docs at Google — Malte Ubl](https://www.industrialempathy.com/posts/design-docs-at-google/)
- [The Good Docs Project · README template guide](https://www.thegooddocsproject.dev/template/readme)
- [Diataxis · How-to guides](https://diataxis.fr/how-to-guides/)
- [Architecture Decision Records overview · Google Cloud](https://docs.cloud.google.com/architecture/architecture-decision-records)
- [GitBook · How to structure technical documentation](https://gitbook.com/docs/guides/docs-best-practices/documentation-structure-tips)
