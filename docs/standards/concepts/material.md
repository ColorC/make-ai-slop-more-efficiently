# Material 健康标准

> **必要不充分**: 不满足一定有问题, 满足不一定没问题.
> 强制度: `[MUST]` / `[SHOULD]` / `[MAY]`
>
> 代码参考: `src/omnicompany/protocol/format.py`（protocol 层 Python 类名 `Format`）
> 设计参考: `.claude/skills/omnicompany-dev/SKILL.md` §2
> 公司级材料统一参考: authority-confirmation.md + autonomous-execution-rules.md。涉及 plan/progress/project/capture/review material 时, 唯一注册入口是 `protocol.Format + FormatRegistry.register`, 分散文档不得另立枚举或平行 registry。

---

## 术语

本规范主体叙述用 **Material** 表达数据契约。`Format` 是 protocol 层的 Python 类名, 在本规范中等同于 Material — 仅代码引用 / class 继承场景保留 `Format` 名字。

下文条款（F-01~F-18）的 "Format" 字样请读作 Material。完整对照见 [`terminology.md §6`](../_global/terminology.md)。

---

## 核心原则

### 原则 1 · 口头复现 (Verbal Reproducibility)

一个拿到 Format 完整定义（不看任何实现代码）的人, 能较为轻松地**举出具体例子**,
且这些例子不会和实际运行中的内容差太多.

验收方法: 把 description 单独给一个无背景 agent, 让它生成一条符合要求的具体值.
如果字段清单一致、枚举取值合法、整体语义对齐, 就算通过. 通不过 = description 不合格.

### 原则 2 · 语义渐进 (Semantic Progression)

同一 pipeline 内相邻 Format 之间是**渐进深化** — 每个后续 Format 在前一个基础上
增加信息或提高确定性. 不出现语义断裂 (前一步"原始需求", 下一步直接跳到"最终产物").

断裂 = 中间步骤被某个 Router 内部消化, 失去 trace-view 可观测性和中间步骤独立验证能力.

### 原则 3 · 验证报告是独立 Format, 不是主体改名

验证报告应当是**独立的 Format** (有自己的 id / description / schema),
原内容 + 验证报告的引用/链接 = "验证过的内容".

```
✗ 反模式 (skeleton 克隆链):
  project_skeleton → compiled_skeleton → audited_skeleton → tested_skeleton
  (代码本体没变, 只是把整体改名加后缀, trace-view 看不出差别)

✓ 正确范式 (原内容不变 + 验证报告独立):
  project_skeleton → project_skeleton (不变)
  + compile_report (独立 Format, 有自己的字段定义和语义)
  + lap_report (独立 Format)
  原内容通过引用关联到这些报告, granted_tags 累加过关标记
```

关键区别: 验证报告**可以而且应该**是独立 Format (它有自己的含义和消费方式),
但原内容**不应该因为被验证过就改名变身**.

### 原则 4 · 按价值拆分

一个中间产物应独立为 Format, 当且仅当**任一**条件成立:
- **人类可读价值** — trace-view 里能被人一眼看懂代表什么
- **调试价值** — 能判定某个环节是否健康
- **复用价值** — 别的管线会把它当输入/输出
- **检查薄弱性** — 标记需要独立验证的点

如果一个中间产物只在节点内部存在、对外无观察价值, 不拆, 藏在节点内部或走 Signal.

### 原则 6 · 按需拆分，不做超级 Material (Demand-Driven Granularity, 2026-04-20)

**一个 Material 的字段集必须由「下游真实消费」决定，不能为了图省事把所有相关信息打包成一个超级 Material。**

Material 按「bus 上的一条独立可订阅类型」设计，不是按「这个 Worker run() 里顺便产出的 blob」设计。判断「该不该拆」的信号：

**信号 A · 下游订阅异质**
不同消费者只用到该 Material 的**不重叠字段子集** → 拆。
- 反例: `M2.a 表格理解文档` 若打包「业务视角 / 字段清单 / 规则证据 / 已知局限」四节为一份 markdown，但 ScriptAuthor 只要字段清单+规则证据，DemandComposer 只要业务视角 → 应拆。
- 拆法: `table.business-view` / `table.field-list` / `table.rule-evidence` / `table.known-limits` 各一份 Material, TableDocAuthor 合并它们产最终 markdown 给人类阅读（M2.a 成**汇总级 sink material**，其他四份是**可独立消费的 internal material**）。

**信号 B · 更新节奏异质**
该 Material 的某些字段高频 replay（如 diff 的 added_rows 每版都变），另一些字段稳定（如 type_row / field_names 只在 schema 演化时变）→ 拆。
- 反例: `table-diff` 打包 type_row + field_names + added_rows + removed_rows + modified_rows 一整份。
- 拆法: `table-schema-snapshot` (type_row + field_names + pk_cols, schema 稳定) + `table-rows-diff` (added + removed + modified, 每版算)。

**信号 C · 产者 / 订阅粒度不同**
一个 Worker 一次产多种语义不同的 Material（如 SourceCollector 同时产collab platform文档 / 排期 / 历史脚本）→ 拆而不是打 bundle。让下游按需订阅其中某一类，不必为了一个 Worker 的一次激活把所有源都等齐。

**信号 D · 观测 / 调试粒度不同**
某部分字段有独立**人类阅读价值**（能在 trace view 里被一眼看懂代表什么）或**调试价值**（能独立判定某环节是否健康）→ 拆。合用参考 [原则 4 · 按价值拆分](#原则-4--按价值拆分)。

**反面信号（此时不拆）**：
- 所有下游都需要全量字段，且字段间语义紧耦合（如 `business-description` 的 3 字段）
- 拆分后下游订阅配置复杂度 ≫ 单份 Material 的认知成本
- 字段数少 (< 5) 且总 payload < 1KB

**反模式 FA-14 · 超级 Material**：
```
✗ demogame.table-everything {
    table_name, type_row, field_names, pk_cols,
    added_rows, removed_rows, modified_rows, unchanged_count,
    field_rules, field_semantics, table_schema_full,
    understanding_doc_markdown, edit_script_python,
    benchmark_report_json, residual_classified,
    ... (35 个字段 / 50 KB)
  }
  所有下游被迫订阅同一个巨无霸, 各取所需字段 → Format 契约形同虚设,
  诊断工具看不到真实依赖, 与 FA-08 透传消费一样是"管线接上了但绕过系统".

✓ 拆分后:
  demogame.table-schema-snapshot  (稳定元数据)
  demogame.table-rows-diff        (版本级增量)
  demogame.field-rule             (规则挖矿结果)
  demogame.field-semantic         (LLM 语义富化)
  demogame.table-schema           (合并 schema)
  demogame.table-understanding-doc  (M2.a sink, 给人类)
  demogame.table-edit-script      (M3.a sink, 给 SDK)
  每个 Material 有明确 producer + 明确 consumer 集合, 订阅图可静态审计.
```

**与原则 4 的关系**：原则 4 说「成熟后按价值拆分」（后验拆）；原则 6 说「设计时先问下游消费就决定粒度」（先验拆）。两者方向一致，原则 6 更强：**初始粗粒度允许但上线前必须过一遍「下游消费异质性」体检**，不能把体检推到「成熟后」。

**检验**: 写 Material 定义前必须填:
```
Material 名: <id>
产者: <Worker class 名> (一个)
订阅者清单:
  - <下游 Worker 1>: 真实消费 <字段子集>
  - <下游 Worker 2>: 真实消费 <字段子集>
  - ...
拆分决策: <不拆: 所有下游消费字段重叠度 > 80%> | <拆为 N 个: 字段子集 / 更新节奏 / 观测粒度不同>
```
如果填不出「订阅者清单」, 说明 Material 被凭空设计 ≠ 下游真实需要, 必须先补订阅者分析再开写.

### 原则 7 · 一手信息源 vs 衍生产物禁区 (Source-vs-Derivative Boundary, 2026-04-20)

**Worker 的输入必须是「一手信息源」, 不得订阅本系统自己产出的衍生 material 作为「理解 baseline」。**

衍生产物 ≠ 信息源. 若一个 material 的产者本身就是 Worker (非人类 / 非外部输入), 它就是**衍生产物**:
不能用它来 bootstrap 另一个 Worker 的"理解 / 判定 / 富化", 否则形成**自证循环** — 旧错误永远被新产物继承, 系统无法进化。

**一手信息源**（允许订阅作为理解 baseline）:

| 类型 | 例子 | 特征 |
|---|---|---|
| 人类手写 | `business-description` / `whitelist` / collab platform排期文档 / 策划案 | kind.source, 无 Worker producer |
| 外部权威数据 | xlsm 公式 / CSV 历史版本 / 客户端源码 / P4 depot 状态 | 业务真实态 |
| 上游 Worker 的**事实提取** (不含判定) | `table-schema-snapshot` (CSV 头两行的结构抽取, 无语义判定) / `table-rows-diff` (PK 级确定性 diff) | 抽取层 Worker, 无 LLM, 无判定 |

**衍生产物**（不得作为理解 baseline）:

- `demogame.business-understanding-doc` (M2.b)
- `demogame.table-understanding-doc` (M2.a)
- `demogame.business-edit-script` (M3.b)
- `demogame.table-edit-script` (M3.a)
- `demogame.benchmark-report` (M1)
- 历史 `docs/tables/<table>.md` / `docs/business/<business>.md` / `scripts/process_<table>.py` / `business_workflows/<business>.yaml` / `data/benchmarks/*.md` (都是本系统过去产的)

**判定信号**: 某 material 的 `tags` 含 `source.llm-authored` / `source.qwen-*` / `status.sdk-deliverable` / `kind.sink` → 大概率是衍生产物, 新 Worker 不该订阅它做理解 baseline。

**允许的例外** — "基于既存修改" 不是"作 baseline":
- ScriptAuthor (M3.a) Q4 决议"必读既存 process_<table>.py" — 这是 **N3 按需修改不覆盖**的实现细节, 目的是**保留人类手改**, 不是"把旧脚本的理解当真值"
- Worker 读既存文档必须明确写"用于 diff + 增量修改", 不得 emit "依据既存推出 XXX 字段是 FK"之类的继承判定

**反模式 FA-15 · 衍生产物自证循环**:
```
✗ FieldSemanticEnricher v2 订阅 demogame.table-understanding-doc (M2.a 历史版)
  → "语义从旧 M2.a 的字段清单节继承" → 新 M2.a 只是复述旧版
  → 旧版的字段误判 (把 Excel 公式错判为 minimum_input) 永远无法自愈

✓ FieldSemanticEnricher v2 订阅 xlsm 公式 + 客户端 Lua 源
  → 独立从一手事实推语义, 不靠任何历史 M2.a
  → 旧版错判被新版覆盖, 系统能进化
```

**检验**: Worker 设计时列订阅清单, 每条订阅标注类别 (人类 / 外部 / 抽取层 / 衍生产物). 有衍生产物出现就必须 justify (是 N3 修改路径还是真 baseline 循环)。

### 原则 5 · 判断节点的输入信息完整性 (Decision Information Sufficiency)

**判断节点的 input Format 必须包含该节点做出判断所需的全部信息。**

LLM 节点做出的判断（选择、分类、评估）必须有充分信息基础。LLM 几乎不会主动承认"我信息不够"——它会基于不完整信息直接给出看起来合理的输出，但判断质量不可靠。

**违反示例**：
```
absorption.repomap（每文件只有 5 个 symbol 名）
    → ModulePicker（判断：这个文件值不值得深读？）
```
问题：判断"值不值得深读"需要看代码内容，input Format 里只有 symbol 名。
判断发生在读取之前，信息基础不足，选择结果不可信。

**验收方法**：
1. 列出节点要做的判断（选择/分类/评估的具体内容）
2. 逐项检查：input Format 里的哪个字段支撑了这个判断？
3. 每项判断都必须找到对应的字段支撑，否则 input Format 设计不合格

**常见修法**：
- 将选择和读取合并为同一个 AgentNodeLoop（选择发生在读之后）
- 在 input Format 里增加足够的内容字段（而不是只有元数据）
- 拆分为"候选清单"→"读取"→"确认选择"三阶段，确认阶段才做最终判断

**与铁律 5 的关系**：铁律 5 说"不靠 LLM 自省信息是否够用"，原则 5 是设计层面的前置保障——在设计时就验证信息充分，而不是期望运行时 LLM 自己发现信息不足。

---

## 标准项

### 信息完整性

**F-01** `[MUST]` **description 五要素完备**

| 要素 | 内容 | 不合格示例 | 合格示例 |
|---|---|---|---|
| ① 字段级语义 | 每个字段的**业务含义** | "包含目标、领域" | "`goal`: 一句话概括最终产物; `domain`: packages 命名空间" |
| ② 值域/枚举/不变量 | 有限取值穷举 + **来源**; 多字段间不变量 | "method: 字符串" | "`method` ∈ {compiler, test, llm, schema}, 来源: ValidatorKind" |
| ③ 上游承诺 | 进入本 Format 时已有的 granted_tags / 已通过的验证 | "由上游产出" | "上游承诺: `structured`(已语法合法), `fields-extracted`(主字段非空)" |
| ④ 下游用途 | **节点 id** + 它怎么消费 | "format_designer 据此设计" | "`format_designer` 读 goal+domain 决定命名空间; 读 constraints 决定验证" |
| ⑤ 最小合法样例 | 具体 JSON, 所有字段有合法取值 | 略 | 一份完整 JSON 实例 |

强烈推荐在 description 里同时写**合法样例和反例**, 让无背景 agent 能 one-shot 学会.

验收: 用原则 1 的复现测试. 通不过 = 不合格.

**F-02** `[MUST]` **description ≥ 100 字符**

已有执行: Guardian OMNI-019

**F-03** `[MUST]` **tags 非空且语义准确**

空 tags 导致 granted_tags 链断裂 + dashboard 搜索失效 + 诊断无法按 domain/stage 过滤.

**F-04** `[SHOULD]` **tags 使用点分层命名**: `domain.stage.aspect`

**F-05** `[MUST]` **parent 指向已存在的 Format**

非空 parent 必须在 BUILTIN_FORMATS 或同 package 内已定义. 悬空 = 继承链断裂.

**F-06** `[SHOULD]` **json_schema 与 description 不矛盾**

同时有时, 字段集合和约束应一致.

**F-07** `[SHOULD]` **examples 至少 1 个且合法**

有 json_schema 时, 每个 example 应通过 `jsonschema.validate`.

**F-08** `[SHOULD]` **semantic_preconditions 与 required_tags 对应**

只声明一边 = 另一边的消费者看不见约束. 两者同时空 OK, 同时非空 OK, 一空一非空 = 问题.

**F-15** `[MUST]` **声明即消费（完整性对齐）** (对应 pipeline.md 原则 0 / P-13)

节点 `run()` 从 `input_data` 里读取的每个字段都必须在声明的 FORMAT_IN 对应 Format schema 里（含 parent 继承字段）。

**违反示例**：
```python
FORMAT_IN = "absorption.module.code"   # schema 只有 {repo_name, module_readings, files_read}
def run(self, input_data):
    self_portrait = input_data.get("self_portrait")  # ✗ schema 没声明
    feedback = input_data.get("supplement_guidance") # ✗ schema 没声明
```

**为什么是 MUST**：schema 是 Format 对 LLM / Doctor / Guardian 的"真实契约"。一旦靠透传消费未声明字段，Format 层面的诊断全部失效，管线接上了但绕过系统。

**修法二选一**：
1. 补进 Format schema（若字段语义属于该 Format）
2. 若字段是独立语义（如"自知识" vs "模块代码"），拆独立 Format + 声明 `FORMAT_IN = list[str]` 做 fan-in

### 语义质量

**F-09** `[SHOULD]` **相邻 Format 语义渐进** (原则 2)

**F-10** `[SHOULD]` **id 遵循层次命名**: `<domain>.<stage>.<aspect>`

**F-14** `[MUST]` **判断节点的输入信息充分** (原则 5)

当某个节点要做选择/分类/评估判断时，其 input Format 必须包含做出该判断所需的全部信息。

验收方法：逐项列出节点要做的判断，检查 input Format 的哪个字段支撑了该判断。
找不到对应支撑字段 = 判断信息不足 = input Format 设计不合格，必须修改 Format 或改用 AgentNodeLoop。

违反 F-14 的典型表现：LLM 节点基于元数据（文件名、symbol 名）做出需要内容支撑的判断（该文件值不值得深读），
导致判断结果随机或与实际内容不符，且无法在运行时自动修复（LLM 不会主动说"我信息不够"）。

### 结构范式

**F-11** `[MUST]` **验证报告是独立 Format, 原内容不改名** (原则 3)

验证报告应当作为独立 Format 存在 (如 `compile_report` / `lap_report`), 有自己的字段定义和语义.
原内容 (如 `project_skeleton`) 通过引用/链接关联到这些报告, granted_tags 累加过关标记.

禁止: 把原内容改名为 `compiled_skeleton` / `audited_skeleton` 等 (克隆链反模式).

**F-12** `[SHOULD]` **成熟后按价值拆分** (原则 4)

初始粗粒度没问题. 一旦中间产物有独立调试/复用价值, 立刻拆分. 有前瞻性地拆 Format — 不光为复用, 更为在管线内建更多可检查点.

**F-13** `[MUST]` **PipelineChecker 通过**

在提交前:
```python
checker = PipelineChecker(registry)
result = checker.check(build_pipeline())
assert result.valid
```
检查: 边类型兼容 / Transformer 正确 / 继承链合法 / required_tags 可满足.

---

## 反模式

| 编号 | 名称 | 描述 | 后果 |
|---|---|---|---|
| FA-01 | 空壳 Format | description < 100 字且无 schema 无 examples | 消费者完全靠猜 |
| FA-02 | 空标签 | tags=[] | granted_tags 链断裂 |
| FA-03 | 隐藏字段 | `_internal_ctx` 下划线前缀走私 | trace-view 看不见, 类型安全崩塌 |
| FA-04 | 语义过载 | 一个 Format 承载两种语义不同的数据 | 下游被迫 if/else 分情况处理 |
| FA-05 | 克隆链改名 | 原内容因被验证就改名 (compiled_xxx / audited_xxx) | 原内容不变 + 验证报告为独立 Format (原则 3) |
| FA-06 | 语义断裂 | 前后 Format 完全无过渡 | 中间过程被内部消化, 不可观测 |
| FA-07 | 异质混合 | 将各自有明确含义和操作方式的数据混合为一个大 Format | 要么有统一处理方式, 要么有统一语义才能合一. 例: 代码语句集合为"代码" ✓; 代码+审计报告+配置混在一起 ✗ |
| FA-08 | 透传消费 | FORMAT_IN schema 缺字段, 节点靠 `input_data` 透传暗管消费 (对应 F-15 / P-13) | Format 契约形同虚设, 诊断工具看不见真实依赖 |

---

## 检查优先级

1. F-02 → F-05 → F-03 非空 → F-04 → F-08 → F-15 → F-10 → F-07 → F-11 → F-13  **— 确定性, 秒级**
2. F-03 语义 → F-06 → F-01 五要素 → F-09 渐进性 → F-12  **— LLM, 分钟级**

---

### F-16 · Material Kind 三分（source / internal / sink）

每个 Material 必须声明 `kind`（通过 tag 或 schema 字段）, 三选一：

| kind | 语义 | 诊断允许 |
|---|---|---|
| `internal`（默认） | worker 间流转 | 必须有 producer + consumer |
| `source` | 系统外部输入（用户请求 / 外部事件 / 定时触发）| 允许**无 producer**, 由外部注入 stock |
| `sink` | 系统最终输出（落盘 / 响应外部 / 审计）| 允许**无 consumer**, 由 sink worker 写外部 |

**标准 sink material 预定义**（行政部级）:

- `stdout` — 标准输出（返回调用方的文本 / JSON）
- `workspace_file_stock.persisted` — 落盘文件
- `client_output` — 返回给用户 / 上游 agent 的结构化结果
- `audit_log.entry` — 审计日志

**标准 source material 预定义**:

- `user_request` — 外部用户输入
- `external_event` — 外部系统事件

Q4 活体验证: "孤儿" 诊断允许订阅 source 的 worker 无上游 producer; "疑似冗余" 诊断对 sink material 无订阅者豁免（INFO 级）。

### F-17 · Material ↔ Workspace 文件映射（大明文走文件, DB 留指针）

material 承载**大明文内容**（长文本 / 二进制 / 多媒体）时, **本体写 workspace 文件**, 数据库（stock）只保留指向文件的**指针**（路径 + 元数据）:

```yaml
# material schema 示例（guardian.file_context_set 若走 workspace 模式）:
files_ref:
  - workspace: "workspace.guardian.scan_session_20260420"
    relpath: "files/src/omnicompany/foo.py.cached"
    ts: "2026-04-20T10:00:00Z"
    hash: "sha256:..."
    size_bytes: 4512
```

**判定"大明文"**（硬阈值, Phase 1 pilot 可调）:
- 单条 material ≥ 10 KB: 建议走 workspace
- 单条 ≥ 1 MB: 强制走 workspace
- 二进制 / 多媒体: 强制走 workspace
- 小结构化 dict（字段 < 10 个, 单值字符串 < 1 KB）: 走数据库正文

**读写约束**（见 router.md R-18 "Workspace Writer Worker"）:
- **写 workspace**: 唯一合法入口是 `WorkspaceWriterWorker` 子类
- **读 workspace**: 任意 worker 通过 workspace 指针 + 自带 `TOOL_SCRIPT_WORKER` 读取（典型: Agent Worker 内部 Tool Script Worker）

### F-18 · Job ↔ Material 绑定

- 所有 non-sink material 带 `job_id`（Q1 单次激活语义）
- `parent_job_id` 可选（validator 发新 job 时引用上轮）
- Material **不可变**（Q3 · 要改发新 material 带 `supersedes: old_id`）

### F-19 · Material kind 声明（2026-04-20 · F-16 升级为 MUST）

**硬规则**: 每个 Material **必须**通过 `tags` 包含以下其一:

- `kind.source` — 外部输入（用户请求 / 外部事件 / 定时触发）, 允许无 producer
- `kind.internal` (默认) — worker 间流转, 必须有 producer + consumer
- `kind.sink` — 系统最终输出（落盘 / 响应外部）, 允许无 consumer

**为什么 MUST**: MaterialDispatcher 的 Q4 诊断（孤儿 worker / 疑似冗余 material）依赖 kind 判定:
- 订阅 source 的 worker 无上游 producer **合法**
- 产 sink 的 worker 无下游 consumer **合法**
- 其他 material 应有完整 producer-consumer 链

**反模式**（FA-13）: Material 无 kind tag → Q4 诊断误报 / 订阅图完整性无法静态验证。

**Guardian 未来规则**（OMNI-037 候选）: Format 定义时 tags 必含 `kind.*` 之一。

---

### 反模式（F-16~F-19 相关）

| 编号 | 名称 | 描述 | 后果 |
|---|---|---|---|
| FA-09 | 大明文直塞数据库 | 违反 F-17, 长文本 / 二进制塞 material 正文而非 workspace 文件 | stock 膨胀, 查询慢, replay 成本高 |
| FA-10 | kind 缺失 | material 未声明 source/internal/sink | Q4 诊断误判孤儿/冗余 |
| FA-11 | sink material 有 consumer | sink 按定义应为终端, 出现下游 worker 订阅 | 架构语义错乱, sink 职责扩散 |
| FA-12 | 跨 job material 透传 | worker 消费了非自身 job_id 的 material | 违反 Q1 单次激活, 破坏 replay 确定性 |
| FA-13 | Material 无 kind tag | 违反 F-19, tags 缺 `kind.source/internal/sink` | Q4 诊断误报 / 订阅图完整性无法静态验证 |
