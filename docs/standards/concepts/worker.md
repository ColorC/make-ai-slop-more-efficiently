# Worker 健康标准

> **必要不充分**: 不满足一定有问题, 满足不一定没问题.
> 强制度: `[MUST]` / `[SHOULD]` / `[MAY]`
>
> 代码参考: `src/omnicompany/runtime/routing/router.py`（protocol 层 Python 类名 `Router`）
> 设计参考: `.claude/skills/omnicompany-dev/SKILL.md` §3, §5

---

## 术语

本规范主体叙述用 **Worker** 表达数据转换执行单元。`Router` 是 protocol 层的 Python 类名, 在本规范中等同于 Worker — 仅代码引用 / class 继承场景保留 `Router` 名字。

下文条款（R-01~R-22）的 "Router" 字样请读作 Worker; "节点" 字样同理。完整对照见 [`terminology.md §6`](../_global/terminology.md)。

---

## 核心原则

### 原则 1 · 单一语义 (Single Semantic Responsibility)

一个 Router 只做**一件语义上的事**. 判断"一件还是多件"的三个信号:

1. **分步骤**: run() 内有 step A → step B, 各自产生有独立含义的中间结果.
   例: 先"分析信息来源"(产出: 来源分类表) → 再"生成解析代码"(产出: Python 脚本).
   来源分类表本身有独立消费价值 → 应拆.

2. **分情况**: `if data['type'] == 'A' ... elif 'B'`, 不同分支做语义不同的操作.
   同质批处理 (for item in items: 对每个 item 做同样的事) 不算.

3. **有意义中间产物**: run() 中途产生可被其他节点独立消费的数据结构
   (表格配置规律、API 调用请求体、结构化分析报告).
   不作为 Format 输出而被内部消化 = 可观测性损失.

**允许的内循环**: 对 N 个同类 item 做相同操作的 for 循环不需要拆.

### 原则 2 · 无状态 (Statelessness)

Router 是 `input → Verdict` 的纯函数. 不持有跨调用可见的状态.
Pipeline Runner 可能在重试、并行、回跳时多次调用同一实例, 有状态 = 行为不可预测.

### 原则 3 · 设计先于实现 (Design Before Code)

任何 Router 写代码前必须先填 §A 的 18 项设计单表. 填不出来 = 设计未完成 = 禁止开工.

### 原则 4 · 信息源正面枚举, 不靠 LLM 自省

LLM 几乎从不主动承认"我信息不够", 会直接硬做并幻觉.
每个 LLM Router 的信息源必须在设计时正面枚举 (§B), 运行时通过 Format 字段预加载 / Tool 拉取 / AgentNodeLoop 探索 三条通道注入 (§C).

---

## 标准项

### 元数据

**R-01** `[MUST]` **DESCRIPTION ≥ 50 字符**

已有执行: Guardian OMNI-020

**R-02** `[MUST]` **FORMAT_IN 和 FORMAT_OUT 都已声明**

已有执行: Guardian OMNI-020

### 语义纯度

**R-03** `[MUST]` **run() 内不混合不同含义的 LLM 调用** (原则 1)

多次 LLM 调用仅当是同一种语义操作的重复 (如对 N 个 item 分别调用) 时才合法.
先"分析"再"生成" = 两种不同含义 = 应拆成两个 Router.

**R-04** `[MUST]` **LLM 调用使用统一 LLMClient**

不直接 `import openai / anthropic / requests`. 必须通过 `runtime.llm.LLMClient`.
原因: InfoAudit 审计 / 模型统一 / 调用计数 / 错误重试策略.

**R-05** `[MUST]` **Verdict 覆盖 PASS 和 FAIL**

只返回 PASS 的 Router = 永远不失败 = 下游无法对失败做反应 = HAPPY PATH ONLY.
例外: 确定性数据组装器 (ContextRouter 类), 其失败 = 抛异常.

**R-06** `[MUST]` **不直接写文件**

run() 内不应有 `open('w')` / `Path.write_text` / `shutil.copy`. 走 `guarded_write.write_file()`.
已有执行: Guardian OMNI-013

### 实现质量

**R-07** `[SHOULD]` **无跨调用状态** (原则 2)

run() 方法内不应有 `self.xxx = value` 形式的赋值 (除 self._logger 等工具属性).
特别禁止: `self.cache`, `self.last_result`, `self.counter`.

**R-08** `[SHOULD]` **有意义中间产物作为 Format 输出** (原则 1 信号 3)

**R-09** `[MAY]` **实现 summarize_input() 或 summarize_output()**

为 trace-view 和 dashboard 提供人类可读摘要.

**R-10** `[SHOULD]` **run() ≤ 80 行**

超过 80 行大概率包含多个语义步骤, 应审视是否拆分.

**R-11** `[MUST]` **不硬编 model 名和 max_tokens**

`model="gpt-4-turbo"` / `max_tokens=16384` 不应写死在 Router 源码.
策略配置走 LLMClient 的 role 配置或 `build_bindings(config)` 传入.
硬编后果: 换模型要改 Router 代码, Router 变成配置和逻辑纠缠的泥潭.

**R-12** `[MUST]` **不泄漏 LLM 协议细节**

不在 Router 里自己 iter `block.type == "tool_use"` 或 `resp.choices[0].message.tool_calls`.
这是 LLMClient 基础设施的职责, Router 只看统一的结构化返回.

**R-13** `[MUST]` **确定性 Router confidence = 1.0**

RULE 类 Router (编译检查 / schema 校验 / 确定性转换) 的 Verdict.confidence 必须为 1.0.

**R-14** `[MUST]` **diagnosis 写明判定依据**

"处理成功" / "处理完成" 是废话. diagnosis 必须说清楚**具体判定了什么、结果如何**.
例: "三层编译检查通过: py_compile OK, import OK, PipelineChecker 0 errors"

**R-15** `[SHOULD]` **granted_tags 贴合实际验证**

如果本 Router 验证了某个语义维度, 必须在 Verdict 中 grant 对应 tag.
不贴 tag = 下游 required_tags 检查永远不过, 或者下游误以为没人验证过.

**R-16** `[SHOULD]` **通用规则沉淀为 Tool**

Router 内的"Python 源码清理" / "从 LLM 响应提代码块" / "JSON schema 验证" 等
与当前业务无关的通用能力, 必须沉淀为 `runtime/tools/` 下的独立 Tool.
锁死在某个 Router 里 = 复用性灾难.

**R-17** `[MUST]` **语义异常也是路由**

业务失败优先用 `VerdictKind.FAIL` + diagnosis, 让 pipeline runner 触发 FAIL 路由.
抛异常是可以接受的 — pipeline runner 会捕获并路由到错误处理分支.
但如果异常是**可预见的业务情况** (如输入缺字段、LLM 返回不合法), 用 Verdict 比抛异常更好:
Verdict 携带 diagnosis 和 output, 下游修复节点能读到具体失败原因.

### 设计与预算

**R-18** `[MUST]` **写代码前填完 18 项设计单表** (原则 3)

见附录 A. 填不出来的项 = 设计未完成 = 禁止开工.
特别重要的项: context_sources / hallucination_risks / verification_binding / output_token_budget.

**R-19** `[MUST]` **输出规模已评估, 超预算有拆分策略**

拆分的本质是**保持 LLM 注意力专注和任务可执行性**, 不是硬截断.
绝对不能因拆分而牺牲信息完整度和交互便利性.

估算公式 (参考): `预估行数 × 15 token/行` 或 `字符数 / 3.5`.
经验安全线: 单次非流式 ~4000 token, 流式 ~8000 token.

超出时的拆分策略 (选最适合的, 不是硬截断):
- SCATTER 拆分 (天然可切 N 个独立子任务)
- 骨架→填肉两步 (层次结构产物)
- 分页 PARTIAL 回跳 (长度不定的列表)
- 输入削减 + 多轮 merge (输入大产物也大)

**严禁硬截断** — 截断 = 信息丢失, 是最差的"策略".

**R-20** `[MUST]` **信息源正面枚举** (原则 4)

每个 SOFT Router 的设计必须列出它需要的信息源, 并对照 §B 的节点类型清单逐项检查.
不预测万能清单, 但"显而易见必要的"不能缺.

---

## 反模式

| 编号 | 名称 | 描述 |
|---|---|---|
| RA-01 | 多语义 LLM | 一个 run() 里 2+ 次不同含义的 LLM 调用 |
| RA-02 | 类型分流器 | `if data['type'] == 'A' ... elif 'B'` 式分流 |
| RA-03 | 越权写入 | run() 里 `open('w')` 不经 guarded_write |
| RA-04 | 有状态 Router | self.cache / self.last_result 等跨调用状态 |
| RA-05 | 吞异常假通过 | `except Exception: return Verdict(PASS)` |
| RA-06 | 中间产物内消化 | 有价值的中间数据不输出到 Verdict |
| RA-07 | 硬编模型 | `model="gpt-4"` 写死在 Router 里 |
| RA-08 | 协议泄漏 | 自己 iter `block.type == "tool_use"` |
| RA-09 | 废话 diagnosis | `diagnosis="处理完成"` |
| RA-10 | system prompt 当 changelog | 堆积"LLM 常犯错误"禁令代替真源码注入 |
| RA-11 | 通用逻辑锁死 | JSON schema 校验 / Python 清理等通用能力写死在某个 Router 里 |

---

## 检查优先级

1. R-01 → R-02 → R-04 → R-06 → R-11 → R-12 → R-05 → R-13 → R-07 → R-17 → R-10  **— 确定性, 秒级**
2. R-03 → R-08 → R-14 → R-15 → R-16  **— AST + LLM, 分钟级**
3. R-18 → R-19 → R-20  **— 设计审计, 人工 + LLM**

---

## 附录 A · 节点设计单表 (18 项)

每个 Router 写代码前必须填完. 详细填写指南见 SKILL.md §3.1.

| # | 字段 | 填写要求 |
|---|---|---|
| 1 | node_id | snake_case, 与 PipelineSpec 一致 |
| 2 | purpose | 动宾结构, ≤30 字 |
| 3 | kind | TRANSFORMER / ANCHOR / SCATTER |
| 4 | validator_kind | HARD / SOFT / N/A (ANCHOR only) |
| 5 | format_in | 每个在 formats.py 已定义; 支持 list[str] 多入 |
| 6 | format_out | 单 str; 验证节点通常 format_in == format_out |
| 7 | required_upstream_tags | 进入前必须已有的 granted_tags |
| 8 | granted_tags_on_pass | PASS 时贴的新 tag |
| 9 | output_token_budget | 公式: 预估行数 × 15; >4k 看字段 10 |
| 10 | scale_strategy | 超预算怎么办 (SCATTER / 骨架填肉 / 分页 / 削减 merge) |
| 11 | context_sources | 对照 §B 逐项勾选 |
| 12 | context_delivery | 三条通道: Format预加载 / Tool拉取 / AgentNodeLoop |
| 13 | hallucination_risks | **具体字段**, 每条对应信息源缓解措施 |
| 14 | verification_binding | 下游谁 HARD 校验本节点产出 |
| 15 | pass_route | NEXT / EMIT |
| 16 | fail_route | RETRY max=N / JUMP / EMIT |
| 17 | partial_route | 反馈回路去哪 |
| 18 | ~~maturity~~ | (已废弃, 健康程度由运行/诊断记录直接判断, 不用枚举标注) |

## 附录 B · 信息源清单 (按节点类型, 起点参考)

> 不是"必须全部满足"的验收清单, 是"写新节点时首先检查"的启发列表.
> 观察到 fallback_triggers 反复指向某类缺失时, 再沉淀为静态注入.

**代码生成类**: 目标基类真源码签名 / 同类已有参考范本 / 依赖 API 构造参数 / import 路径规范 / 依赖 dataclass 字段 / 语言版本约束

**NL 解析类**: 输出 schema + 枚举值 / ≥3 个 few-shot + 反例 / 领域术语表 / 歧义消解规则 / 拒绝条件

**审计/检查类**: 被审对象完整源码 / 每条规则的可执行判定条件 / 已知豁免清单 / 严重性分级 / 误报历史

**修复类**: 原始代码完整 / 失败报告(定位到行) / 类似问题历史修复 patch / 修复边界 / 验证回路入口

**决策/路由类**: 每分支历史案例 / 无效分支排除理由 / iteration 计数 / 各分支下游节点

**探索发现类**: 搜索边界 / 成功终止条件 / 失败终止条件 / 输出结构约束

## 附录 C · 上下文注入通道 (按首选序)

1. **Format 字段预加载** (首选) — 上游 Transformer 用 inspect/Read 预加载到 format_in 字段. 确定性、可观测、可缓存. **绝对禁止用 system prompt 教导替代真源码注入** (RA-10).

2. **Tool / Read 拉取** (次选) — 量大时节点内部用 Tool 拉取. 多一次 I/O, 省上游 Transformer 复杂度.

3. **AgentNodeLoop 动态探索** (最后手段) — 无法预先枚举时才用. token 消耗大、结果不稳定. 成熟后必须冷凝回通道 1.

4. **info_audit + UniversalFallbackLoop** (运行时补救) — LLM 调用时自动报 missing_info, runner 规则化判断是否触发兜底. 不是设计时通道, 是运行时安全网.

---

### R-18 · Worker 粒度原则（2026-04-20 Patch-1 · 硬规则）

**粒度 = 完整职责 + FORMAT 边界 + 独立测试价值**。**不是"每个函数一个 Worker"**。

判定自问（写新 Worker 前）:
1. 有**明确 FORMAT_IN / FORMAT_OUT 边界**吗？边界模糊 → 合并到上下游
2. 单独写 **Worker 级集成测试**有价值吗？没价值 → 它只是内部函数, 不该独立 Worker
3. 再拆会变清晰还是更碎？更碎 → 停拆

**合法模式**: Worker 内部保留**纯函数库**（如 guardian 的 `rules/*.py` 14 条规则, 被 `RuleEngineWorker` 单体调用）。这是内部实现选择, 不上升为 Worker 粒度。

### R-19 · Agent Worker = 迷你 team（对外单 Worker, 内部三件套）

**Agent Worker** 是一种特殊 Worker 子类, 内部由**三种子 Worker** 组成迷你 team：

```
Agent Worker (FORMAT_IN / FORMAT_OUT 一套)
  ├── Context Script Worker    — 准备 / 压缩 / 组装上下文（无 LLM）
  ├── LLM Worker               — 调 LLM 产生响应（单次或循环）
  └── Tool Script Worker (N)   — 执行工具调用（read / grep / run / workspace 访问）
共享: 迷你 stock（内部 material 流转, 不外泄）
```

**对外表现**: 单个 Worker（声明一套 FORMAT_IN / FORMAT_OUT）。外部订阅图看它就是一个节点。

**内部机制**:
- 收到 FORMAT_IN 后, 先激活 Context Script Worker 准备初始上下文
- LLM Worker 消费上下文产生 response（含 tool call 列表）
- 若有 tool call → Tool Script Worker 激活, 产出 tool_result material（写回迷你 stock）
- LLM Worker 再次激活消费 tool_result, 直到产出最终 answer
- Context 再组装 → 产出 FORMAT_OUT 对外

**与旧 `runtime/agent/agent_node_loop.py` 的映射**: 旧单体 AgentNodeLoop 内部的 while 循环 = Agent Worker 的迷你 team + 内部订阅激活。行为等价, 结构更纯。

### R-20 · LLM Worker → Agent Worker 升级规则

**默认**: 当 LLM Worker 不确定需要哪些 material（初始 material 难穷举的情况）, **升级为 Agent Worker**, 开放相关 workspace 供其 Tool Script Worker 自由读取。

**判定场景**（符合即升级）:
- material 需求随 LLM 推理动态变化
- 预先枚举 material 将导致 FORMAT_IN schema 膨胀（>10 字段）
- 上游 material 内容量超过单次 LLM context 一半

**不升级的场景**:
- 明确需求: 单条或固定组 material 即足够（典型: 模板填充 / 分类）
- 确定性包装: 把 LLM 输出套 schema 转发（可能不需要 LLM, 用 rule 即可）

### R-21 · Diagnosis Agent Worker 变体（质疑上游 material）

**Diagnosis Agent Worker** 是 Agent Worker 的特殊子类, **内置对上游 material 的质疑能力**。

**用途**: 当 Worker 拿不到需要的 material, 或发现上游 material 不对时, **沿 trace 往上查**。**尽量少归因于模型幻觉**。

**核心原则**（硬规则）:
- 不确定 LLM 是否真错时, **优先替换为 Diagnosis Agent Worker** 重试
- Diagnosis Agent Worker 有额外工具: `trace_back_tool`（查 material 上游 producer + 其输入） / `material_assertion_tool`（对 material 内容提出假设验证）
- 其输出 FORMAT_OUT 可能是 `diagnosis.material_dispute`（质疑上游）或正常 FORMAT_OUT（成功产出）

**诊断结果处理**:
- `material_dispute` → 路由到 validator worker → 可能发新 job 修复上游
- 正常产出 → 说明原 LLM Worker 是被**劣质 material 拖累**, 非模型幻觉

### R-22 · Workspace Writer Worker（写 workspace 唯一合法入口）

**所有对 workspace 的写入必须经过 WorkspaceWriterWorker 子类**。直接 `Path.write_text` / `open("w")` 写入 workspace 路径 = 违反。

- FORMAT_IN: `workspace.write_request`（含 workspace_id / relpath / content / hash）
- FORMAT_OUT: `workspace_file_stock.persisted`（sink material, 含落盘确认）
- 内部用 `core.guarded_write.write_file`（对齐 OMNI-013）

详见 [`team.md` omnicompany 扩展 · P-14 Workspace 定义](team.md)。

### R-23 · Worker.run() verdict.output 平铺约定（2026-04-20 Patch-6 · 硬规则）

**硬规则**: `verdict.output` 是 `FORMAT_OUT` 对应 Format 的 **payload 本体**（平铺字段 dict）, 不是 `{format_id: payload}` 嵌套。

**为什么**: Protocol 层 `FORMAT_OUT: str`（单 Format）, PipelineRunner 和 MaterialDispatcher 都按此约定消费 output。嵌套形式混淆上下游约定。

**反模式**（RA-16）:
```python
# ✗ 错误: 嵌套包装
FORMAT_OUT = "guardian.file_context_set"
def run(self, input_data):
    return Verdict(kind=PASS, output={"guardian.file_context_set": {"files": [...]}})
```

**正确**:
```python
# ✓ 正确: 平铺 payload
FORMAT_OUT = "guardian.file_context_set"
def run(self, input_data):
    return Verdict(kind=PASS, output={"files": [...], "scan_ts": ...})
```

**溯源**: 2026-04-20 Team 2 selftest 过 `MaterialDispatcher` 时 Guardian Workers 嵌套 output 被暴露, 修正后 79 → 85 passed。

---

### R-24 · FORMAT_IN_MODE 显式声明（2026-04-20 Patch-9 · 硬规则）

当 `FORMAT_IN = list[str]` 时, **必须显式声明 `FORMAT_IN_MODE`**:

- `"and"` (默认) · **composite fan-in**: 所有 FORMAT_IN 都到齐才激活 (典型: workflow_factory 多入节点 / guardian module_explorer)
- `"or"` · **alternative**: 任一 FORMAT_IN 到达即激活 (典型: Agent Team 的 ContextScript 订阅 `agent.request` OR `agent.tool_result`)

**为什么是 MUST**: 历史上 `list[str]` 隐含 AND 语义（workflow_factory composite 用法）, 但 Agent Team 需 OR 语义, **无明文声明则歧义**。MaterialDispatcher 按 `FORMAT_IN_MODE` 决定累计策略。

---

### R-25 · 子 job 发起（2026-04-20 Patch-8）

Worker 产出 `verdict.output` 带特殊字段 `_emit_as_new_job: True` 时, dispatcher **用新 trace_id (新 job_id)** 发布对应 event, 并记录 `payload._parent_job_id = 当前 job_id`。

**用法**:
- **Agent Team 新一轮循环** (tool_result 触发, parent = 上轮 job) — R-19 主要场景
- **Validator 发起新 job** (上轮 material 不合格触发补救, Q1.C)
- **外部代理委托** (Worker 显式要求子 job 处理)

**约定**:
- 触发事件 `_parent_job_id` 字段记录链 (Q4 诊断追溯用)
- 子 job 内 worker 的 Q1 "每 job 单次激活" 重新计数 (不同 trace_id → 不同激活 key)
- 终止由子 job 内产 sink material 决定, 不阻塞父 job

---

### R-26 · 禁止重复造 agent — 用统一 AgentNodeLoop（2026-06-23 用户立 · ⚠硬规则 · Guardian OMNI-095）

**agent 是最核心设施之一, 全项目唯一实现 = `packages/services/_core/agent`（`AgentNodeLoop` 薄调度器 + `SingleToolRouter` 工具族 + `TOOL_REGISTRY` + `ConfigurableAgent` 配置驱动）。任何"让 worker 调 LLM + 工具多轮干活"的需求, 一律走它, 禁止手搓第二套。**

正确做法（三选一, 都用统一实现）:
- **子类化 `AgentNodeLoop`**: 声明 `NODE_PROMPT` + `TOOL_ROUTERS`, agent 即 worker（`AgentNodeLoop.run` 是 async, MaterialDispatcher 的 `_invoke_worker_async` 原生 await, **不存在 asyncio 嵌套问题**——这早在 2026-04-20 解决, 别拿它当借口 fork）。
- **`ConfigurableAgent`**: 配置驱动（`SPEC = AgentSpec(...)`, 工具按字符串名查 `TOOL_REGISTRY`, prompt 走外部 .md, `workspace.write_prefixes` 限定写位置）。
- **普通 worker 借 `services._core.agent.launch.run_json_agent(...)`** 启动统一 AgentNodeLoop（只读 + 出结构化 JSON 的标准入口）。

**禁止**（即 RA-19）: 自己 `for/while` 循环里直调 `call_json`/`LLMClient.call` + 手拼 `tool_use`/`tool_result` 回合 = 手搓 ReAct = 重复造 agent。

**统一 agent 不够用怎么办**: **改进它**——加工具（写个 `SingleToolRouter` 注册进 `TOOL_REGISTRY`）/ 加启动器 / 扩 `AgentSpec` / 提 PR 到 `_core/agent`。重构统一实现可以, 复制出平行第二套不行。

> 溯源: 2026-06-23 我（Claude Code）沉淀 team 时嫌统一 AgentNodeLoop（async+bus）麻烦, 自建了同步 ReAct 循环（`runtime/agent/tool_agent.py`）。用户裁决"你重建了一个 agent?…如果统一实现不够好, 你就改进统一实现, 不要二重 / 不要搞第二套, 用统一设施执行"。已删 fork、改用统一 `AgentNodeLoop`、立此规 + Guardian OMNI-095。

### R-27 · LLM 工作必须进入标准管线；文件校验在同一 Agent 会话闭环（2026-07-14 用户立 · ⚠硬规则 · Guardian OMNI-094）

业务代码只能在注册 Team/Worker/Agent 内调用 `call_json`、batch 或 Agent 设施；一次性文件工作走受审计的 `omni worker run`。禁止在 `docs/`、`scripts/`、`.omni/sandbox/` 或仓根写临时 LLM 包装脚本。调用前先按 Atlas `llm-workflow` Skill 选型。

当产物是文件且需要格式、长度、事实或结构校验时，必须选 AGENT：Agent 直接写文件并运行声明的 lint/test；失败输出作为当前会话的下一轮上下文，Agent 就地修改后重跑。lint 失败不得由外层重新调用模型、不得重建会话、不得从头生成整份文件。单次结构化工具适合固定 schema 数据，不承担长文或多文件编辑协议。

### 反模式（R-18~R-26 相关）

| 编号 | 名称 | 描述 | 后果 |
|---|---|---|---|
| RA-12 | 过度 Worker 化 | 违反 R-18, 每函数一个 Worker | 样板爆炸 + O(F×R) 激活次数 + 破坏批处理简洁 |
| RA-13 | Agent Worker 假单体 | 声称 Agent Worker 但内部无 Context/LLM/Tool 三件套分离, 仍是 while 循环 | 不符 R-19, 失去可观测/可替换/可 diagnose 能力 |
| RA-14 | 草率归因 LLM 幻觉 | 遇到 LLM Worker 输出异常, 不沿 trace 查上游 material, 直接加 retry / 换模型 / 加 prompt 约束 | 根因没查, 修表面 |
| RA-15 | 绕 Workspace Writer 写文件 | 违反 R-22, 业务 Worker 直接写 workspace 文件 | 审计断链 / 无 hash / 无 sink material 记录 |
| RA-16 | verdict.output 嵌套 | 违反 R-23, output = `{format_id: payload}` 嵌套而非平铺 | PipelineRunner / MaterialDispatcher 消费错乱, Team 2 selftest 暴露 |
| RA-17 | FORMAT_IN list 无 MODE | 违反 R-24, `FORMAT_IN = list[str]` 不显式声明 AND/OR | 未来 dispatcher 语义变化时 worker 行为错乱 |
| RA-18 | Worker 内部 while 循环持状态 | 违反 R-07 Statelessness + R-25, 应用 `_emit_as_new_job` 让 dispatcher 驱动循环 | 无法 replay / 不可观测 / 阶段 D AgentNodeLoop 替换后更痛苦 |
| RA-19 | 重复造 agent | 违反 R-26, 手搓 ReAct/工具循环(循环内直调 LLM + 自拼 tool 回合)而非用统一 AgentNodeLoop | 核心设施二重化 / 失去统一审计·压缩·重试·工具治理 / Guardian OMNI-095 |
| RA-20 | 临时 LLM 包装脚本 | 违反 R-27, 在非标准位置直接调用 LLM/Agent, 或 lint 失败后新开调用从头生成 | 审计断链 / 重复推理 / 丢失上一轮错误上下文 / Guardian OMNI-094 |
