# Team 健康标准

> **必要不充分**: 不满足一定有问题, 满足不一定没问题.
> 强制度: `[MUST]` / `[SHOULD]` / `[MAY]`
>
> 代码参考: `src/omnicompany/protocol/team.py` (`TeamSpec` / `TeamEdge`, canonical 协议类)
> 设计参考: `.claude/skills/omnicompany-dev/SKILL.md` §4, §9

---

## 术语

本规范主体和代码契约都用 **Team** 表达一组 Worker 的协作单位。唯一结构是
`omnicompany.protocol.team.TeamSpec`。`Pipeline` / `PipelineSpec` 仅是旧 import 和旧数据的
兼容名，不表示第二种结构，也不得用于新设计。

下文历史条款（P-01~P-17）残留的 "Pipeline" / "管线" 字样请读作 Team；"节点"字样读作
Worker。完整对照见 [`terminology.md §6`](../_global/terminology.md)。

---

## 核心原则

### 原则 0 · 先需求产物，不按接线 (Demand-Supply, Not Wiring)

**管线不是按"顺序"设计的，是按"每个节点需要什么、产出什么"设计的。**

常见陷阱：打开编辑器先画一条 `A → B → C` 的拓扑图，再给每个节点塞内容。这种"接线思维"导致：
- 节点 B 声称接 A 的产物，实际消费的信息远超过声明（靠 `input_data` 透传上游所有字段）
- 拓扑里两个节点"连上了"，实际数据流完全绕过了 Format 声明 —— **管线骨架成了摆设，诊断工具看不到真实依赖**

**正确顺序**：
1. 列每个节点的 **需求表**（它要读什么 Format 才能工作）
2. 列每个节点的 **产出表**（它必然落下什么 Format）
3. 拓扑 = 需求-产出匹配的自然结果，**不是设计起点**

**架构允许的自由度**（鼓励使用）：
- **fan-in** — 一个节点可声明 `FORMAT_IN = list[str]`，从多个非直接上游拿输入
- **fan-out** — 一个节点的产出可被多个下游各自消费
- **跳连**（skip-connection） — 下游 N 可直接要上游 A 的产物，不必被迫经过 B、C
- **按需再取** — AgentNodeLoop 内节点可在执行期用工具动态拉额外信息

接线思维会下意识把这些自由度砍掉，只留"A→B→C 线性链"。设计时要反过来：**先问"这个节点到底依赖什么"，再让拓扑服从需求**。

### 原则 1 · 可观测 (Observability)

注册在 core/pipelines.py = dashboard / CLI / Guardian / event.db 都能看到.
不注册 = 隐身, 所有监控失效.

### 原则 2 · 错误路径完备 (Error Path Completeness)

只有 HAPPY PATH 的 pipeline 不是正式管线. 必须回答"如果某一步失败了怎么办".

### 原则 3 · SOFT-HARD 紧跟配对

每个 SOFT 节点 (LLM 判断、概率性输出) **紧下游**应有轻量 HARD 校验做"当场拦截".
校验粒度可以很轻 (schema 合法 / 枚举值 / 必填字段 / 引用存在性), 关键是"早发现早回跳".

SOFT 距末端 HARD 越远, 根因追溯越困难. 一个错误在第 1 个 SOFT 产生, 到第 4 跳末端才暴露, 诊断只能说"末端爆了" — 看不出根因在哪一跳.

```
✓ req_analyzer (SOFT) → verify_requirement (HARD)
  format_designer (SOFT) → verify_format_chain (HARD)
  code_generator (SOFT) → compile_checker (HARD)

✗ req_analyzer (SOFT) → format_designer (SOFT) → node_planner (SOFT) → code_generator (SOFT) → compile_checker (HARD)
  (4 跳裸 SOFT 链, 唯一 HARD 在末端)
```

### 原则 4 · 独立维度并行验证

如果一组验证检查的维度**互相独立** (一道失败不影响另一道的计算前提), 必须并行:

```
✓ compile → SCATTER → [lap | error_route | integration] → merge
✗ compile → lap → error_route → integration  (串行, 每轮只暴露一关问题)
```

串行堆叠的代价: 浪费时间 / 中段失败后面没跑过 / 修复节点只能对单一报告反应.

判断准则: `A.run(x)` 和 `B.run(x)` 能否输入同一份数据各自独立产出报告? 能 → 必须并行.

### 原则 5 · 单节点纯粹 (Node Purity)

每个节点只做一件事, 获得恰好足够的上下文.
反模式: 一个节点接收 4 种报告然后自己判断优先级 → 应让管线路由决定走哪条修复路径.
反模式: 一个 Format 在 5 个节点间传递不变且无 granted_tags 累加 → 中间产物被隐藏.

---

## 标准项

### 注册与可见性

**P-01** `[MUST]` **在 core/pipelines.py 注册** (原则 1)

使用 `_lazy()` / `_lazy_fn()` 懒注册, 避免 CLI 启动时拉入重依赖.
已有执行: Guardian OMNI-017.

**P-02** `[MUST]` **bindings 完整**

bindings 的 key 必须与 PipelineSpec 的 node.id **一一对应**. 缺少或多余都导致运行时错误.

```python
node_ids = {n.id for n in pipeline.nodes}
binding_ids = set(bindings.keys())
assert node_ids == binding_ids
```

### 拓扑结构

**P-03** `[MUST]` **entry node 有明确 FORMAT_IN**

入口节点的 FORMAT_IN 不能为空. 入口接受什么数据必须明确.

**P-04** `[MUST]` **每个 SOFT 紧跟轻量 HARD** (原则 3)

从每个 SOFT node 出发, 其直接下游 (PASS edge 的 target) 中应至少有一个 HARD 节点.
"间接到达" 不够, 必须"紧跟".

**P-05** `[MUST]` **FAIL 分支存在** (原则 2)

edges 中至少有一条 `condition=VerdictKind.FAIL` 的边.

**P-06** `[MUST]` **无孤立节点**

每个非 entry 节点至少有一条入边.

**P-07** `[MUST]` **每个 ANCHOR 节点的 routes 覆盖 PASS 和 FAIL**

不只是检查"pipeline 整体有 FAIL 边", 而是检查每个节点的 `routes` dict 是否包含 PASS 和 FAIL key.
RETRY 必须设 `max_retries` 防死循环.

**P-08** `[SHOULD]` **独立维度验证并行** (原则 4)

如果 pipeline 中有多个验证节点, 审查它们是否可以并行 (输入同源、互不依赖).
可并行但串行设计 = 浪费 + 每轮只暴露一关.

### 信息流

**P-09** `[SHOULD]` **Format 链语义连贯**

按拓扑序遍历, 检查每条边 source.FORMAT_OUT 与 target.FORMAT_IN:
- 完全相等 → OK
- 有 parent 关系 → OK
- 完全不相关 → 问题 (信息流断裂)

**P-10** `[SHOULD]` **循环有最大迭代防护**

存在环时, 环内应有计数防护 (如 `ctx["<loop>_iter"] >= MAX` 时强制退出).

**注意**: 节点级 `max_retries` 只管单节点重试. **跨节点反馈回路**
(如 `auto_fixer → compile_checker → auto_fixer`) 必须有独立的全局 iteration 计数器,
不靠 `default_max_steps=30` 全局兜底 (那是按节点步数计, 与业务回路语义不对应).

### 健康度

**P-11** `[SHOULD]` **节点健康度由运行/诊断记录判断**

不使用 HYPOTHETICAL / GROWING / MATURE / CRYSTALLIZED 枚举标注.
节点的健康程度直接从历史运行记录 (成功率/失败模式/诊断发现) 和 Registry 档案中读取,
由诊断工作流在每次增量检查时更新.

### 验证绑定

**P-12** `[SHOULD]` **验证绑定对应业务类型**

- bugfix / 代码修改 → 末端必须有测试节点 (HARD)
- 代码生成 → 必须有编译检查节点 (HARD)
- 涉及用户决策 → 必须使用 UserInquiry 接口
- 可观测性: 所有执行经过 EventBus, 禁止静默旁路

### 声明诚实

**P-13** `[MUST]` **FORMAT_IN 必须完整声明节点消费的一切** (原则 0)

节点 `run()` 从 `input_data` 里读取的**每一个字段**都必须出现在声明的 FORMAT_IN 对应 Format schema 里（包含 parent 继承字段）。

**禁止做法**：
- 声明 `FORMAT_IN = "A"`，A 只声明了 `{x, y}`，`run()` 却读 `input_data["z"]` —— z 靠上游"好心透传"存在；
- `return { **input_data, ...new }` 把上游所有字段全拷贝给下游，让下一个节点也搭便车；
- 用 `input_data.get("anything_i_want")` 当默认的"上下文暗管"。

**为什么是 MUST**：一旦靠透传拿字段，Pipeline 的 Format 声明就形同虚设。Doctor 诊断 Format 链连贯性（P-09）、Guardian 审 FORMAT_IN 契约、LLM 基于 schema 自测输入 —— 全都失效。管线"接上了但完全绕过了系统"。

**验收方法**（Guardian 未来规则）：
1. AST 扫 `Router.run()` 方法，抽所有 `input_data.get(...)` / `input_data[...]` 读的 key
2. 对比该 Router 声明的 FORMAT_IN schema 的 required + optional 字段集合
3. 有差集 → 报 HIGH 告警

若节点确实需要多个语义上独立的输入（如"模块代码" + "OmniCompany 自知识"），用 `FORMAT_IN = list[str]` 显式声明 fan-in，不要偷偷塞。

---

## 反模式

| 编号 | 名称 | 描述 |
|---|---|---|
| PA-01 | 隐身管线 | pipeline.py 存在但未注册 |
| PA-02 | HAPPY PATH ONLY | edges 中无 FAIL 条件边 |
| PA-03 | 裸 SOFT 链 | 多个 SOFT 串联, 无中间 HARD 拦截 |
| PA-04 | Format 断裂 | 前后 FORMAT_OUT / FORMAT_IN 完全无关 |
| PA-05 | 死循环 | 有环但无 iteration 计数防护 |
| PA-06 | 孤岛节点 | 节点定义了但无入边 |
| PA-07 | 串行独立验证 | compile → lap → error_route → test 串行, 应并行 |
| PA-08 | bindings 不完整 | node_ids ≠ binding_keys |
| PA-09 | 万能节点 | 一个节点接 4 种报告自己判优先级 |
| PA-10 | 隐藏传递 | 一个 Format 5 个节点不变且无 granted_tags 累加 |
| PA-11 | 透传黑盒 | `return {**input_data, ...}` + `run()` 消费 FORMAT_IN schema 外的字段 → 接上了但绕过系统 |
| PA-12 | 接线思维 | 先画拓扑后定节点职责，节点消费/产出被动适应连线而非显式声明 |

---

## 检查优先级

1. P-01 → P-02 → P-05 → P-06 → P-03 → P-07 → P-13  **— 确定性, 秒级**
2. P-04 → P-08 → P-09 → P-10 → P-11  **— 拓扑分析, 秒~分钟级**
3. P-12  **— 业务语义审查, LLM 或人工**

---

### P-14 · Workspace = Team 的工作空间（material 本体存储层）

**Workspace** 是一个 Team（或一组 Team 协作时）的**物理工作空间**, 以目录形式落在磁盘, 保存大明文 material 的本体（详见 [`material.md` F-17](material.md)）。

**命名规范**（硬规则）:

```
workspace_id 格式: workspace.<team_name>.<session_kind>[.<job_id>]
  示例:
    workspace.guardian.scan_session_20260420
    workspace.absorption.stage3.job_K7F2X
    workspace.doctor.health_check_daily
```

**Workspace 目录结构约定**:

```
<workspace_id>/
  manifest.yaml      # workspace 元数据 (team_id / created_at / associated_jobs[])
  materials/         # material 指针指向的文件本体 (如 F-17 schema 中的 relpath)
    <relpath>
  logs/              # 可选: team 内部运行日志 (非 sink material, 辅助 debug)
  .omni/             # 可选: omni-trace / omni-audit 专用文件
```

**磁盘位置**（约定优于配置）:
- 开发环境: `data/workspaces/<workspace_id>/`
- 生产环境: 由 `config.resolve_workspace_dir(workspace_id)` 解析（避免硬编码路径）

### P-15 · Team ↔ Workspace 关系（1:N）

- 一个 Team **可关联 0~N 个 Workspace**（短任务无状态 Team 可无 workspace; 复杂 Team 多 workspace 分 material 种类）
- 一个 Workspace **只归一个 Team**（避免跨 Team 写入冲突 / 审计链模糊）
- **跨 Team 共享** material 必须走 Stock（database）或显式的 sink material（如 `client_output`）, 不允许直接共享 workspace 路径

### P-16 · Workspace 读写契约（对齐 R-22）

- **写**: 仅 `WorkspaceWriterWorker` 子类可写（见 [`worker.md` R-22](worker.md)）。任何业务 Worker 发起写入 → 产 `workspace.write_request` material → Workspace Writer Worker 订阅落盘
- **读**: 任意 Worker 可通过 material 指针读, 建议用 Tool Script Worker 封装
- **删**: 走 GC / 归档策略, 不允许运行时删除（sink material 不可变, 参考 Q3）

### P-17 · Workspace 生命周期

| 阶段 | 语义 | 允许动作 |
|---|---|---|
| `active` | 当前 job 活跃使用 | 写 + 读 |
| `sealed` | job 完成, 保留查询但不写 | 只读 |
| `archived` | 移至长期存储（`data/_archive/workspaces/`）| 只读, 审计用 |
| `pending_gc` | 计划删除（TTL 过期）| 无操作, 等 GC |

生命周期转换由监督 Worker 触发（Phase 1 pilot 时定具体实现）。

### 反模式（P-14~P-17 相关）

| 编号 | 名称 | 描述 | 后果 |
|---|---|---|---|
| PA-13 | Workspace 跨 Team 写 | 违反 P-15, 另一 Team 直接写别人的 workspace | 审计链断 / 写入冲突 / 职责边界模糊 |
| PA-14 | 硬编码 workspace 路径 | 违反 P-14, 业务代码 `Path("data/workspaces/...")` 而非走 `config.resolve_workspace_dir` | 开发/生产路径错 / 无法迁 |
| PA-15 | sealed workspace 写入 | 违反 P-17, 完成 job 后试图继续写 | 破坏 replay 确定性 |
| PA-16 | material 本体硬塞 DB | material 的大明文本体塞 DB 而不走 workspace 文件（本质是 FA-09, pipeline 层也列出提醒）| DB 膨胀 / 查询慢 |

### P-18 · 用统一设施, 不平行造（2026-06-23 用户立 · ⚠硬规则）

**Team 里需要的"通用能力"一律用全项目唯一实现, 禁止在某个 team/worker 里平行造第二套。** 尤其:

- **agent（调 LLM+工具多轮干活）= 唯一实现 `packages/services/_core/agent`（AgentNodeLoop）**。team 里的"脑子" worker 走它（子类化 / `ConfigurableAgent` / `launch.run_json_agent`），禁手搓 ReAct 循环 → 详见 [`worker.md` R-26](worker.md) + Guardian **OMNI-095**。
- LLM 调用 → `runtime.llm.call_json`（OMNI-094）；写文件 → `core.guarded_write`（OMNI-013）；注册 → `PipelineEntry`/team-builder。

**外部自建 team（沉淀桥等亲手写包的场景）**: 必须严格照本规范模板 + 用上述统一设施。统一设施不够用 → **改进它**, 不复制平行第二套（重构可以, fork 不行）。

| 编号 | 名称 | 描述 | 后果 |
|---|---|---|---|
| PA-17 | team 内平行造核心设施 | 某 team/worker 自建 agent 循环 / 自管 LLM 调用 / 自己写文件门禁, 不用统一实现 | 核心设施 N 重化 / 审计·治理·压缩·重试各行其是 / 维护灾难 / Guardian OMNI-095·094·013 |
