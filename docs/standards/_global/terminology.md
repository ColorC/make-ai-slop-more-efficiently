<!-- [OMNI] origin=claude-code domain=standards ts=2026-04-19T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:standards.global.terminology_migration_mapping.md" -->

# 术语迁移规范（Terminology Migration）

> **状态**: active · 2026-04-19 立档
> **上游决策**: [`docs/plans/[2026-04-19]BLACKBOARD-ARCHITECTURE/plan.md`](../../plans/format-material/%5B2026-04-19%5DBLACKBOARD-ARCHITECTURE/plan.md) §三 Q1 + §五
> **强制等级**: 新代码 MUST; 旧代码 grandfathered
> **Guardian 规则**: OMNI-036（新 module 用旧名 → WARN）

---

## §1 为什么改名

OmniCompany 是一个**自己编辑自己**的软件。命名不只是给协作者看，更是给自己（LLM）读自己代码时的**认知查询词典**。
"材料 / 工人 / 车间 / 部门 / 公司" 这套隐喻直接对应 self-portrait、订阅关系、职责边界这类高频查询——语义贴合降低推理层数。
改名不是美化，是**认知加速器**。

---

## §2 层级对照表

```
OmniCompany                           [项目]  ← 原 omnicompany
  └─ Department                       [域]    ← 原 packages/services/ + packages/domains/
        └─ Team                       [团队]  ← 原 pipeline
              └─ Worker               [工人]  ← 原 router
                    消费/产出 Material [物料]  ← 原 format
                    存 Stock           [库存]  ← 原 eventbus

贯穿层: Job                            [作业]  ← 原 run / trace (一次外部作业)
特殊 Department: 行政部 = 核心基础设施 (服务全公司, 非业务)
  - runtime/*                 (执行 / 观察 / agent / info_audit)
  - packages/services/guardian   (自检查员)
  - packages/services/doctor     (诊断员)
  - packages/services/registry   (户籍)
  - packages/services/repair     (修理员)
  - packages/services/selftest   (自测)
```

**为什么选这组词**：

| 新名 | 选用理由 |
|---|---|
| Material | 强调"可消费的实体"而非仅 schema |
| Worker | 强调"主动认领"而非机械转发 |
| Stock | 强调"存货仓"而非瞬时消息流 |
| Team | 一组工人协作完成一类任务 |
| Department | 一个部门统管一类职责 (一组 Team) |
| OmniCompany | 多部门协作的"自治组织" |
| Job | 一次外部订单作业 (继承原 run_id) |

---

## §3 自底向上替换顺序（硬规则）

**从数量最多的底层先换**，下层未 100% 替换完 **禁止** 向上推进：

| Phase | 替换对象 | 理由 |
|---|---|---|
| **A** | Material + Worker | 底层 primitive, 数量最多 (338 router / 161 format) |
| **B** | Team + Stock | 容器层 (48 pipeline + 1 eventbus) |
| **C** | Department | 域层 (services + domains) |
| **D** | OmniCompany | 顶层 (包名 / CLI) |

每 Phase 有 alias 过渡期。**下层未签章完成 → 不得开启上层 Phase**。

---

## §4 过渡期规则

- **新代码**: MUST 用新名（新建的 worker / material / group / etc.）
- **旧代码**: grandfathered, 按自然活跃度被动替换；不强制批量重构
- **Alias 过渡**: 协议层提供等价别名（如 `Material = Format` 一行 export）
- **Sunset 条件**: 某 Phase 的所有旧名使用点 = 0 **且** L1 签章 → 移除该层 alias
- **违反检测**: Guardian OMNI-036 扫新 module 的旧名 import → WARN（不阻塞）

### §4.1 Guardian OMNI-036 规则草案

**ID**: `OMNI-036` · **name**: `new-module-legacy-naming` · **severity**: `MEDIUM` · **disposition**: `[warn]`

**代码位置**: [`packages/services/guardian/rules/terminology.py`](../../../src/omnicompany/packages/services/_core/guardian/rules/terminology.py)

**触发条件**（AND）:
- 文件位于 `_NEW_MODULE_WHITELIST` 列表内
- 且源代码文本中出现以下任一：
  - 含 legacy identifier: `PipelineEdge` / `PipelineSpec` （Phase A 可扩展）
  - 含 legacy import pattern: `from omnicompany.protocol.format import Format` / `from omnicompany.protocol.* import PipelineEdge`

**豁免**:
- `_graveyard/` / `_archive/` / `packages/vendors/` 路径
- 白名单外的 legacy 目录（当前全部 legacy）
- alias 协议层代码（`Material = Format` 一行 export 自身）

**当前 Q0 状态**: `_NEW_MODULE_WHITELIST = ()` → 规则实装但暂不触发任何文件。

**Phase A 启用**:
1. L1 将新 module 路径（例如 `src/omnicompany/packages/services/omnicompany/`）填入白名单
2. 该层 alias sunset 时 `severity: MEDIUM` → `HIGH` 并扩展 legacy identifier 列表

**与其他规则关系**:
- **OMNI-033**: 原扫 `forbidden_aliases`（含 worker）已移除 worker 条目，不再误判
- **F-15 / LAP D9**: 按 Material 禁搭便车继续守, 本规则 orthogonal

---

## §5 适用范围

MUST 用新名：

- 代码标识符（类名 / 函数名 / 变量名 / 参数名）
- 文档正文（DESIGN.md / plan.md / standards / README）
- commit message / PR 标题与描述
- 日志 / 事件字段 / trace 标签
- CLI 命令名与 help 文本

MAY 用旧名：

- 引用 legacy API / 导入 legacy 模块时保持原名
- 讨论历史（"当年的 format 体系"）
- alias 过渡期内, 旧代码内部保留

---

## §6 两层命名：protocol 保留 · omnicompany 用新名（2026-04-20 L1 修正）

(2026-07-02 更正: 见 §13 第5条)

### §6.1 基本原则

**协议层 (`src/omnicompany/protocol/`) 及核心标准规范 (`docs/standards/concepts/material.md` / `pipeline.md` / `router.md` / `llm_first.md` 等) 保留原抽象名字**: `Router` / `Format` / `Pipeline` / `EventBus`。

**omnicompany 业务/组织层** 用新名: `Worker` / `Material` / `Team` / `Stock` / `Department`。

两者是**同构对应**, 不是命名迁移后取代:

| protocol 层 (不变) | omnicompany 层 (新) | 关系 |
|---|---|---|
| `Router` | `Worker` | Worker 本质是 Router 的子类 + omnicompany 术语包装 |
| `Format` | `Material` | Material 是 Format 的一次实例化, 带 job_id / 生命周期语义 |
| `Pipeline` | `Team` | Team 是一组 Worker 的 omnicompany 组织单位; protocol 层仍叫 Pipeline |
| `EventBus` | `Stock` | Stock 是 bus 的 omnicompany 角色名, 强调"存货仓"语义 |

### §6.2 为什么分两层

- **协议层要抽象稳定**: `Router` / `Format` 是数据契约, 命名应通用, 不贴业务组织学
- **omnicompany 层要贴业务**: 工人认领物料, 部门协作完成订单 — 这些词帮 LLM 建立高质量心智模型
- **语义升级不是取代**: Material 比 Format 多了"可消费实体 + 生命周期 + 流通状态", 但底层仍是 Format schema

### §6.3 何处用哪个（2026-04-20 修正 · 规范也用新命名主体）

**旧规范被新命名顶掉**, 不保留严格双轨, 旧命名仅**一句话带过**作兼容说明。

**protocol 原名保留**（仅以下场景）:
- `src/omnicompany/protocol/` 代码与 DESIGN.md （Python 类名: `Router` / `Format` / `PipelineSpec` 等）
- 代码层类引用 (`class FooRouter(Router):` / `from omnicompany.protocol.format import Format`)
- standards 文档**开头"术语"说明段一句话**: "Format 是 protocol 层类名, 读作 Material"

**omnicompany 新名（主体）**:
- 所有 standards 文档**叙述层**: format.md / router.md / pipeline.md 主体用 Material / Worker / Team
  （条款编号 F-01~F-18 / R-01~R-22 / P-01~P-17 稳定, 内含 "Format" 字样按术语说明段读作 Material）
- `docs/PROGRESS.md`（状态叙述）
- `docs/plans/**` 活跃 plan 的正文叙述（非归档 `_archive/`）
- `docs/reports/**` 新写的报告
- `src/omnicompany/packages/services/<team>/DESIGN.md` 业务层叙述
- `.claude/skills/omnicompany-dev/SKILL.md` 业务教学语境
- CLAUDE.md workspace 指引
- Memory 文件

**规则验证**: 新建文档时用新命名为主; 碰到代码 class 名时保留 protocol 原名。不造双轨噪音。

### §6.4 混用场景（合法）

一段话同时指向两层时, 允许混用并附对应括注:

> "每个 Worker（即 protocol 层的 Router 子类）订阅 Material（即 Format 实例）后激活..."

这种混用是**清晰的**而不是混乱的, 它同时表达了组织语义和底层契约。

### §6.5 Worker 粒度原则（2026-04-20 Patch-1 · guardian Team 1 迁移认知）

**硬规则**: Worker 粒度 = **完整职责 + FORMAT 边界 + 独立测试价值**。**不是"每个函数一个 Worker"**。

**反例**（错误粒度）:
- Guardian 14 条 rule 每条做一个 Worker → 样板代码爆炸 + 把 O(F) 批判断拆成 O(F×R) 激活 + 失去 RuleEngine 简洁性

**正例**（正确粒度 · guardian 4 Worker）:

| Worker | 职责 | 为什么不再细分 |
|---|---|---|
| GitDiffScan | 扫 git 变更 → FileContext 集合 | 扫描是单一动作 |
| RuleEngine | 对 N 文件跑 M 规则 → violation 三分（确认/疑似/重复）| 规则批判断本质是一个"引擎"职责; 内部可继续用纯函数 rule 库 |
| LLMJudge | needs_judgment 子集复核 | 复核是独立 LLM 调用 |
| AuditTow | violation → sink（落盘 + 处置）| 落盘是外部边界动作 |

**判定方法**（写新 Worker 前自问）:
1. 此 Worker 有**明确 FORMAT_IN/FORMAT_OUT 边界**吗？边界模糊 → 合并到上下游
2. 单独写一个**Worker 级集成测试**有价值吗？没价值 → 它只是内部函数, 不该独立 Worker
3. 把职责再拆会变清晰还是更碎？更碎 → 停止拆分

**内部保留纯函数库合法**: Guardian 的 14 条 rule 保留为 checks.py 纯函数, 被 RuleEngineWorker 调用 — 这是 Worker 内部实现选择, 不上升为 Worker 粒度。

**来源**: [`docs/plans/[2026-04-19]BLACKBOARD-ARCHITECTURE/migration_log.md`](../../plans/format-material/%5B2026-04-19%5DBLACKBOARD-ARCHITECTURE/migration_log.md) Team 1 guardian · Patch-1。

---

## §7 Agent Team（纯 bus 驱动的 Worker 组合 · 2026-04-20 Patch-7 修正）

**Agent Team** = 一组 Worker 通过**主 bus** 订阅激活, **不是单 Worker + 迷你 stock**（原 R-19 "Agent Worker" 设计作废）:

- `Context Script Worker` — 组装 LLM 上下文（无 LLM 调用）· FORMAT_IN_MODE=`"or"` 订阅 `agent.request` OR `agent.tool_result`
- `LLM Worker` — 调 LLM 产 response（单轮调用, kind ∈ {tool_call, finish}）
- `Tool Script Worker (N)` — 响应 tool_call, 产 `agent.tool_result` 带 `_emit_as_new_job: True` (触发新子 job)
- `Finalizer Worker` — 响应 finish, 产 `agent.final_output` sink material 终止

**无"迷你 stock"** — 所有 material 流经主 bus, 可被外部审计/replay/调试。

**每轮循环 = 一个子 job**: 发起者 = tool_result 产出（带 `_emit_as_new_job`）, parent_job_id 链 agent 内部因果。Q1 "worker 每 job 单次激活" 和 "agent 多轮循环" 天然兼容（不同 job_id 允许 worker 再激活）。

**升级规则**: LLM Worker 不确定需要什么 material 时, **默认升级为 Agent Team**, 开放 workspace 供 Tool Script Worker 自取。

**Patch-7 pilot 实现**: [`packages/services/omnicompany/agent_team_demo.py`](../../../src/omnicompany/packages/services/_core/omnicompany/agent_team_demo.py) 4 Worker mock · 6 测试全过。

**详见**: [`router.md` R-19 (修正) / R-20 / R-24 FORMAT_IN_MODE / R-25 子 job](../concepts/worker.md)。

---

## §8 Workspace（Team 工作空间 + material 本体存储）

**Workspace** = Team 的磁盘工作目录, 保存大明文 material 的本体（database stock 只留指针）。

**命名**: `workspace.<team>.<session_kind>[.<job_id>]`

**读写约束**:
- **写**: 仅 `WorkspaceWriterWorker` 子类可写（避免审计断链）
- **读**: 任意 worker, 建议用 Tool Script Worker 包装

**大明文判定**: ≥ 10 KB 建议走 workspace, ≥ 1 MB 或二进制强制走 workspace。

**详见**: [`pipeline.md` P-14 / P-15 / P-16](../concepts/team.md) + [`router.md` R-22](../concepts/worker.md) + [`format.md` F-17](../concepts/material.md)。

---

## §9 Diagnosis Agent Worker（质疑上游, 少归因幻觉）

**Diagnosis Agent Worker** = Agent Worker 子类, 内置对上游 material 质疑能力。

**核心原则**（硬规则）: Worker 拿不到 material 或输出异常时, **沿 trace 往上查 material**, **尽量少归因于 LLM 幻觉**。不确定时 → 替换原 LLM Worker 为 Diagnosis Agent Worker 重试。

**特殊工具**:
- `trace_back_tool` — 查 material 上游 producer
- `material_assertion_tool` — 对 material 内容提出假设验证

**输出分支**:
- `diagnosis.material_dispute` → 路由 validator, 可能发新 job 修上游
- 正常 FORMAT_OUT → 说明原 LLM Worker 是被劣质 material 拖累, 非幻觉

**详见**: [`router.md` R-21](../concepts/worker.md)。

---

## §11 Job 发起者（2026-04-20 Patch-8 · Q1.C 扩展）

**Job 的四类发起者**:

| 类型 | 语义 | 实现 |
|---|---|---|
| **Source material** | 用户输入 / 外部事件 / 定时触发 | 外部 `publish` 初始 material event (kind=source) |
| **Tool result** | Agent Team 内 tool 执行返回 | Worker output 带 `_emit_as_new_job: True` → dispatcher 用新 trace_id (parent=触发 event.id) |
| **Validator 发起** | validator worker 判不合格 / 需补 material (Q1.C 已有) | validator Worker 产出带 `_emit_as_new_job` + 新 `job.request` material |
| **Child job (显式)** | worker 显式请求子 job | 同 Tool result, 区分只在语义 |

**parent_job_id 链**: 通过 `payload._parent_job_id` 记录, Q4 诊断追溯 agent 内部因果。

**详见**: [`router.md` R-25](../concepts/worker.md) + [`packages/services/omnicompany/material_dispatcher.py`](../../../src/omnicompany/packages/services/_core/omnicompany/material_dispatcher.py)。

---

## §12 迁移分型（2026-04-20 Patch-2/3/4 · Stage 1 沉淀）

**四分型**（迁移动作差异）:

| 类 | 特征 | 动作 | 时间基线 |
|---|---|---|---|
| **A · 单体旧架构** | 内置 class (RuleEngine 等) + 旧入口文件 | 建 `workers/` + `materials.py` + 归档旧入口到 `_archive/` + 改外部 import | ~1.5 h |
| **B · 原生 pipeline** | 已有 `pipeline.py` + `routers.py` + `formats.py` 三件套 | 标 Material kind + DESIGN.md 填充 | ~0.25 h |
| **B 单体 AgentLoop** | 1 node pipeline 封装 while 循环 | 同 B + DESIGN 写明 R-19 Agent Team 迁移路径 | ~0.2 h |
| **C · 元服务库** | 无 Format/Router/Pipeline 三件套 | DESIGN.md 角色说明 + 概念映射表 + 零代码 | ~0.15 h |

**彻底归档原则**（Patch-2, 类 A 专用）:
- 旧入口文件（如 `patrol.py` / `patrol_runner.py`）归档到 `_archive/`, **不留原地**
- `__init__.py` 保留兼容 shim（re-export 旧 API）
- 外部调用者改 import 路径经 shim
- 测试 import 同改

**Dispatcher pilot 模式**（Patch-5, 用于 B 类验证）:
- 让 Team 通过 `MaterialDispatcher` 跑 Worker 订阅驱动
- 跑不通 = 暴露之前不严谨（F-15 透传 / F-16 错标 / output 约定不一致 等）
- **验证方法而非新需求**（用户 2026-04-20 洞察）

---

## §10 两层命名落地清单（2026-04-20 归档补全）

**protocol 层文件**（保留原 Router/Format/Pipeline 名, 不改）:
- `src/omnicompany/protocol/*`（代码 + DESIGN.md）
- `docs/standards/concepts/material.md` / `router.md` / `pipeline.md` / `llm_first.md` / `information_sufficiency.md` 等（原抽象条款 F-01~F-18, R-01~R-22, P-01~P-17 编号稳定）
- LAP D1-D9 规则原文

**omnicompany 层扩展**（新 omnicompany 概念, 在 standards 末尾"omnicompany 层扩展"节内）:
- `format.md` · F-16 Material kind / F-17 Workspace 映射 / F-18 Job 绑定 / FA-09~FA-12
- `router.md` · R-18 Worker 粒度 / R-19 Agent Worker / R-20 升级 / R-21 Diagnosis / R-22 Workspace Writer / RA-11~RA-14
- `pipeline.md` · P-14 Workspace / P-15 Team-Workspace 关系 / P-16 读写契约 / P-17 生命周期 / PA-12~PA-15

**omnicompany 业务叙述层**（用新名）:
- `docs/PROGRESS.md` / `docs/plans/**` / `docs/reports/**` / `.claude/skills/omnicompany-dev/SKILL.md`
- `src/omnicompany/packages/services/<team>/DESIGN.md`（业务层 DESIGN 叙述）
- CLAUDE.md workspace 指引

---

## 关联

- **Plan**: [`docs/plans/[2026-04-19]BLACKBOARD-ARCHITECTURE/plan.md`](../../plans/format-material/%5B2026-04-19%5DBLACKBOARD-ARCHITECTURE/plan.md) §五（迁移与反倒退协议）
- **Guardian 规则**: OMNI-036（草案在本文 §4 · 代码实现待 Phase A 开启时落地）
- **F-15 承接**: `Material 禁搭便车` = 原 `Format 禁搭便车`（[`format.md`](../concepts/material.md) F-15）

---

## §13 语义 OS 顶层词汇（2026-07-02）

> 上游：[`docs/plans/[2026-06-25]SEMANTIC-OS/plan.md`](../../plans/%5B2026-06-25%5DSEMANTIC-OS/plan.md) v2 修订 + [`docs/plans/[2026-07-02]SEMANTIC-OS-MAP/`](../../plans/%5B2026-07-02%5DSEMANTIC-OS-MAP/)（地图 + 目标架构，功能定义与操作权威在此）。

### §13.1 五类作用（一览）

判一块东西该不该建、算哪一类件，看它落在下面哪一类作用里：

| 作用 | 一句话定义 |
|---|---|
| **视图** | 对已有内容做整理 / 重组后呈现，只读，不制造新权威（不产生新的真源） |
| **输入** | 捕获人的决策与反馈；东西一旦被记下来，就当场获得了语义身份 |
| **索引与链接** | 让内容可寻址、可检索（给内容一个能被找到、能被指向的坐标） |
| **转换** | `运行时 × 程序 × 输入 → 输出`；这里的"程序"不只是代码，规范、模板、prompt 都算"程序" |
| **治理** | 把不确定的确定化（验证状态标记、漂移纠偏、术语收敛、垃圾回收这类活） |

### §13.2 通用件 / 专用件 与「入内三问」

一块代码或资产要放进 omnicompany（而不是放进某个业务域），必须**三问全部答"是"**：

1. 它处理的是**任意内容的语义**，而不是某个具体业务的内容？
2. 删掉任何一个业务，它**依然成立**（不会跟着散架）？
3. 它涉及的资产是**语义设施自身的**，而不是业务产物？

三问全过 → 通用件，进 omnicompany。任一问答"否" → 专用件，归属对应业务域。

### §13.3 无痕挂载

对外部只读内容（例如 demogame 工程这类不归我方管的代码/资产库）的**唯一合法接法**：

- 语义身份**全部记在我方自己的索引里**：外部位置（如 P4 路径 / collab platform链接）+ 内容指纹（版本号 / changelist / 修订时间）+ 我方的语义标注（这是什么、归哪个业务、和我方哪些材料相关）。
- **绝不往对方写任何文件**——不写文件头、不放旁车文件、不建目录，对方工程里看不出我们存在过。
- 我方有产出需要进对方世界时，**走对方自己的正规通道**（例如配表改动走 P4 提交、文档改动走collab platform接口），不绕过、不夹带。

### §13.4 LAP 一词三义定性（消歧）

`LAP` 在仓内同时指三样不同的东西，容易读串，需按以下定性区分（权威：`SEMANTIC-OS-MAP/target-architecture.md` 第一节 1.5 及 `SEMANTIC-OS-MAP/plan.md` 第二节）：

1. **LAP 独立仓**（`language-anchoring-protocol`）：理论规范的源头，2026-03-28 之后未再变动，是**冻结的理论规范源头**。
2. **omnicompany protocol 层**（`src/omnicompany/protocol/`）：LAP 概念的**活的实现血统**，今后 LAP 概念如何演进，以这一层的实际实现为准，不再回头改独立仓。
3. **LAP D1-D9**：team_builder 里对生成物做九维静态检查的检查器，已于 **2026-07-03（批4）显式废止**（见 §14）。废止理由：九维打分与本工作区"不打分、列证据"的既定民约冲突；实现体留在归档 `_archive/routers_legacy.py::LAPVerifierRouter` **不删**，team_builder 活代码里对它的全部引用已摘除，验证链改走 `compile_checker → error_route_auditor → integration_tester → finalizer`。

**另注**：`protocol/DESIGN.md` 以及各 CLI 文档里出现的 `D1` / `D2` / `D3`，是它们各自文档内部的决策编号，跟 LAP 的 `D1-D9` 纯属字母撞车，两者没有关系，读到时别混为一谈。

### §13.5 时效更正：§6 的 protocol 层"保留 Router / Pipeline 旧名"说法已不符现实

§6 中"protocol 层保留 Router / Format / Pipeline / EventBus 旧名"这句话，其中 **Router 与 Pipeline 两部分已不符现实**（2026-07-03 批4 复核坐实）：

- `Pipeline` 已于 **2026-04-21** 正名为 `Team`；`protocol/pipeline.py` 现在只是一个**兼容壳**（内部直接从 `protocol/team.py` 转出旧类名，供旧 import 路径继续用），并非活的实现。所以"protocol 层保留 Pipeline 旧名"已经不成立——旧名只剩废弃壳。
- `Router` 的真身实际在 [`runtime/routing/router.py`](../../../src/omnicompany/runtime/routing/router.py)，**不在 `protocol/` 目录下**。所以"protocol 层保留 Router 旧名"也不准确——protocol 目录里并没有活的 Router 实现体。
- `Format` / `EventBus` 部分仍如 §6 所述（Format 在 protocol 层是活契约类，Stock 是 EventBus 的 omnicompany 角色名）。

新写文档时，涉及 protocol 层现状描述，以本条为准；§6 原文作为历史记录保留，不再回改。

---

## §14 命名收尾对账与半退役设施决断（2026-07-03 批4 · 每句对代码现实）

> 上游：[`docs/plans/[2026-07-02]SEMANTIC-OS-MAP/plan.md`](../../plans/%5B2026-07-02%5DSEMANTIC-OS-MAP/plan.md) 第六节第四批 + `overnight-run.md` 批4 开工锚。
> 本节每一句都与代码现实核对过（下附核对点），不写愿望态。

### §14.1 命名迁移 Phase A：定性收尾完成，不复工存量改名

- **定性（用户 2026-07-03）**：命名迁移**主体已完成**。此前的"停摆"是边际递减 / 执行方自认到头，不是弃坑；剩余基本无需迁移。本批只做收尾对账宣告，不"复工"存量改名。
- **OMNI-036 白名单为空的正确读法**：`_NEW_MODULE_WHITELIST = ()`（代码现实：[`guardian/rules/terminology.py`](../../../src/omnicompany/packages/services/_core/guardian/rules/terminology.py)）**不再是"待办/待 L1 填入"**，而是"没有活跃迁移前沿需要专门圈定强制轮训"的忠实反映。规则本身**保持实装**，作为对未来新名回退的常驻护栏；将来某新建 module 需强制护栏时再把路径加进白名单即可（机制在，随时可用）。
- **其他软件的第二波命名**：随第七批各件顺带做，不在此单列强制。
- 核对点：`_NEW_MODULE_WHITELIST` 确为空元组；OMNI-036 仍在 `rules/__init__.py` 规则清单里（`*_R036`）注册生效。

### §14.2 protocol 层旧名说法更正（承接 §13.5）

- "protocol 层保留 Router / Pipeline 旧名"已不符现实：Pipeline 已于 2026-04-21 正名 Team，`protocol/pipeline.py` 只剩兼容壳；Router 真身在 `runtime/routing/router.py`，不在 protocol 目录。详见 §13.5。

### §14.3 LAP 九维检查器显式废止

- **决定**：LAP D1-D9 九维检查器**显式废止**（半退役 + 九维打分与"不打分列证据"民约冲突）。
- **代码现实**：`team_builder/workers/lap_verifier.py` 已删除；`team_builder/{__init__,workers/__init__,routers,run}.py` 里 `LAPVerifier*` 的 import / 导出 / DAG 绑定全部摘除；`team.py` 的 `lap_verifier` 节点与其边已移除，验证链重路由为 `compile_checker → error_route_auditor → integration_tester → finalizer`（团队节点由 14 减为 13，绑定同步 13）。
- **归档保留**：实现体 `_archive/routers_legacy.py::LAPVerifierRouter` **原地保留不删**（归档先行民约）；其余 ~20 个共享该归档文件的 Router 薄壳不受影响。
- **决策登记**：见决策库（omni decisions，本批新增一条废止决策）。

### §14.4 值域对齐（status 4 对 5 → 4 对 4）

- **决定**：`omnimark.STATUS_VALUES` 与 `docs/taxonomy.yaml` 的 `status_values` 对齐为同一集合（4 值：active / deprecated / quarantined / pending-review）。
- **收窄方向证据**：以 omnimark 四值为准、从 taxonomy 移除多出的 `draft`——因为四值在 `src` 内均有真实生产写入方（`pending-review` 由 `guardian/tow_truck.py` stamp_file 与 `cli/commands/guardian.py` 多处写入），而 `draft` 在全 `src` grep `status="draft"` **零生产写入方**（它与 DESIGN.md 文档成熟度词表里的 draft 撞名但非同一字段）。
- **更正（批4返修，2026-07-03）**：上面这句"零生产写入方"当时不实——笔记转计划管线（`packages/services/_core/lifecycle/note_to_plan.py`，经 `omni notes promote` 命令可达）会在生成的计划 frontmatter 里写 `status: draft`、在 OMNI 头注释里写 `status=draft`，是一个真实存在的生产者，只是当时没被 grep 到 / 没被核对到。本轮修复已把这个生产者本身改写为 `status: pending-review` / `status=pending-review`，四值收窄的方向判断（`draft` 不该留在值域里）依然成立，只是"零生产写入方"这个论据表述不准确，现予更正。另需说明：当前代码里没有任何地方对 status 值域做强制运行时校验——值域对齐这件事目前只在文档层面和测试断言层面起效，不是运行时 guardrail，往后若再长出新的 `draft` 生产点，不会有代码自动拦截，仍需靠 grep 巡查或测试覆盖发现。

### §14.5 问询库路径修死

- **决定**：进化问询 SQLite 从"随进程 cwd 漂移的裸相对路径"改为锚定 `data/runtime/buses/inquiries.db`（与 human_inbox.db 同级）。
- **代码现实**：`user_inquiry.py` 补 `_resolve_default_db_path()`（向上找仓根标志锚定，与 cwd 无关）+ 环境变量逃生舱 `OMNI_INQUIRY_DB_PATH` + `migrate_legacy_inquiry_db()`（迁移旧漂移文件带 .bak 回滚副本）；CLI 四个子命令与 orchestrator 默认走锚定路径。磁盘上尚无实体文件（无问询发生），无历史文件需迁移。
- **人工审批 SQLite（更正，批4返修，2026-07-03）**：上面"无同病，本批核实无需改"的结论不实。`runtime/buses/human_bus.py::_resolve_inbox_path()` 原实现虽然带 `OMNI_HUMAN_INBOX_PATH` 逃生舱，但上行查找起点仍是 `Path.cwd()`——和 `user_inquiry.py` 修复前是同一病根：若进程从仓库外目录启动，向上永远找不到 `src/omnicompany` 标志，会静默退回到最后走到的 cursor 拼路径，产生仓库外的错误数据库文件，不报错也不提示。这个缺陷在上一轮验收时没有被测出来（当时的对照测试同样只覆盖了"仓库内不同 cwd"场景，没覆盖"祖先链完全无仓库标志"场景）。本轮（批4返修）已把 `user_inquiry.py` 与 `human_bus.py` 两个文件同步改为从模块自身 `__file__` 向上锚定仓根（不再依赖 cwd），找不到仓根标志时改为抛出 `RuntimeError`，不再静默回退到 cwd 拼路径。

### §14.6 管线命名收敛到"域.动作"点号命名（旧名走别名过渡）

- **决定**：管线命名统一到"域.动作"点号命名空间；旧 kebab 名走 `PipelineEntry.aliases` 别名过渡，**不批量改调用方**（沿用自底向上纪律）。
- **代码现实（本批已迁）**：software_engineering 域六条管线 `sw-verify / sw-review / sw-plan / sw-design / sw-tdd / sw-implement` 迁为 `sw.verify / sw.review / sw.plan / sw.design / sw.tdd / sw.implement`，旧 kebab 名进 `aliases`。核对点：`registry.get("sw-verify")` 与 `registry.get("sw.verify")` 返回**同一对象**；注册表去重总数仍为 76（未增未减）。

### §14.7 注册表残留清理（431 → 269 router）

- **决定**：给 scanner 补上其 docstring 一直承诺却从未实现的 `prune_stale()`（重扫 → diff → 备份 → 清理三段），清掉历史扫描残留条目。
- **代码现实**：残留判据 = 条目 `source_file` 已从磁盘消失；apply 前强制整目录备份；只删 dry-run 标记的残留，绝不碰活条目。
- **实际数字（活注册表 `data/services/registry/`）**：router 431 → 269（删 162 条残留，如 absorption 包搬走后遗留的 `absorption.CoverageAuditorRouter` 等）；agent_loop 17 → 8、format 161 → 60、pipeline 48 → 27。
- **安全核对**：`omni routers` 活类数清理前后均为 **126**（它走 `RouterRegistry.discover_router_classes()` 运行时反射，与磁盘 JSON 解耦，故清理磁盘残留不可能减少活类数）。备份快照与对比清单落在 `data/_workspaces/semantic-os-map/batch4/`。
- **注**：仓根另有一份 `src/data/services/registry/`（内容是 materials/plans/templates，无 router 类型）是另一个注册表，**不在本锚清理范围**，未触碰。

---

## §15 发布门牌与内部登记名（2026-07-04 工作区命名更正）

> 上游：[`docs/plans/omnicompany-governance/[2026-07-03]WORKSPACE-REPO-RENAME/repo-name-decisions.md`](../../plans/omnicompany-governance/%5B2026-07-03%5DWORKSPACE-REPO-RENAME/repo-name-decisions.md)。

### §15.1 两套名字

**发布门牌**是给外部人看的项目 / 产品 / 公共规范名。它可以采用完整说明短句的首字母缩写，README 首行写成：

```text
# HANDLE · Full Sentence
```

**内部登记名**是给本机治理、挂载、账本、路径登记、运行宿主使用的名字。内部登记名必须严肃、功能直译，不为了整齐强行品牌化。

这两套名字不能混用：不是每个 git 文件夹、数据仓、镜像目录、运行宿主都需要发布门牌。

### §15.2 什么时候才取发布门牌

满足以下任一条件，才进入发布门牌候选：

1. 未来可能作为独立产品、应用、公共规范、公开研究仓对外发布。
2. 已经是公开仓或对外材料，需要有可解释的项目门牌。
3. 作为自有业务仓独立挂载，且预期会被外部协作者直接识别。

反例：

- 输入真源 / 数据仓（如笔记仓）不取发布门牌。
- 运行宿主 / 已收编旧壳不取发布门牌。
- 发布镜像从属源项目，不另取门牌。
- 外部只读参考仓保留上游名字，我方不另命名。
- 内部诊断小工具用功能直译，不起代号。

### §15.3 当前工作区裁决

发布门牌候选：

| 对象 | 发布门牌 | 完整长句 | 裁决 |
|---|---|---|---|
| omnicompany | MASME | Make AI Slop More Efficiently | 未来整体发布门牌；物理路径、包名、CLI 不改。 |
| poof | POOF | Pop Overlay Onto Foreground | 悬浮层外设可能单独发布；旧 `Poof, There It Is` 解释废止。 |
| lofa | LOFA | Look Over From Afar | 移动端外设可能单独发布；修正原 `Look From Afar` 缩写不严。 |
| language-anchoring-protocol | LAP | Language Anchoring Protocol | 公开理论仓，已是合格缩写。 |
| vilo-wants-to-know | VWTK | Vilo Wants To Know | 作品 / 内容真源未来可发布；当前内容仓不急搬。 |
| quant-lab | MAWO | Measure Assets Without Overfitting | 公开研究仓候选；现阶段保留 `quant-lab` 物理路径。 |

不作为对外发布项目命名：`poof-notes`、`whatnow`、`webworks`、`cmd-flash-watchdog`、`hypothesis-workspace`、`demoworkspace`、`omnifactory-public`、`参考项目/*`。

### §15.4 新业务仓口径

语义 OS 目标态下，新业务优先落到标准位，按外部域挂载接入，而不是继续把所有内容铺在工作区顶层。

- 对外发布产品：可用首字母缩写门牌。
- 自有业务仓但不对外发布：仓名用朴素功能直译。
- 内部设施 / 器官：严肃功能直译，不起代号。
- 外部只读内容：保留对方原名，我方只建无痕索引。

---

## §16 任务(task)的两粒度与唯一真源（2026-07-05）

> 上游：[`docs/plans/agent-orchestration/[2026-07-05]TASK-SSOT-UNIFICATION/plan.md`](../../plans/agent-orchestration/%5B2026-07-05%5DTASK-SSOT-UNIFICATION/plan.md)。用户拍板：omnichat 里看到的任务必须有唯一来源。

**唯一真源**：机器上一切任务只存在 progress-service（:8230，whatnow.json）一处。旧
`data/lifecycle/tasks/` 第二存储已废除（存量已迁移归档）。

**两粒度**（都在同一真源里，用 `parent_task_id` 区分）：

| 术语 | 是什么 | id 形态 | 例 |
|---|---|---|---|
| **计划任务**（计划级 task） | 一个计划或一张外部工单在进度体系里的身位 | `p_<sanitized plan_id>` / `t<seq>` | `p_2026_06_22_OMNI_RESOURCE_CENTER` |
| **执行子任务**（执行级 task） | `omni plan split` 从计划拆出的一步可投递工单，挂在计划任务下 | `<parent_id>.<局部序号>` | `p_xxx.3`（CLI/Python 侧用局部序号 `3`） |

**语义边界**：执行子任务做完一步不代表整计划完成——它不触发计划完成硬闸、不被自动归档
（做完的步骤留作履历）；计划任务的完成度 = max(不倒退当前值, LLM 评估, 子任务完成率)。

**写文档时**：别再把 `omni task` 的任务和任务窗口的任务当两套系统叙述，它们是同一真源的两粒度。
