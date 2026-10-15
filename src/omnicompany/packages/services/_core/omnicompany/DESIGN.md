<!-- [OMNI] origin=claude-code domain=services/omnicompany ts=2026-05-04T14:15:00Z type=doc status=active belongs_to_service=omnicompany -->
<!-- [OMNI] material_id="material:omnicompany.design_document.specification.md" -->

# omnicompany · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限) + **Team 新建统一形式硬规则**.
>
> 形态: 核心基础设施 (黑板架构 active · Team 新建统一形式规范权威).
> 范围: Worker / Material / Team 统一 shape 样板, 供其他 agent 并行开发参考.

## 状态

- **版本**: V1 active (2026-04-20 立档; 2026-06-13 材料统一裁决转正)
- **成熟度**: active
- **下一步**: 按材料统一计划继续把公司级材料写入口、material registry 与审阅材料并入 Format/EventBus 主线

## 核心接口

- **`MaterialDispatcher`** ([material_dispatcher.py](material_dispatcher.py)) — Worker × EventBus 激活驱动
  - `run_job(initial_material_id, initial_payload, *, job_id?, max_iterations=100)` — 启动一个 job
  - `orphan_workers(events)` — Q4 诊断: 列出订阅无 producer 的 worker (非 source)
  - `unconsumed_materials(events)` — Q4 诊断: 列出无 consumer 的非 sink material
- **Agent Team demo** ([agent_team_demo.py](agent_team_demo.py)) — 4 Worker mock 示例
  - `AgentContextScriptWorker` / `AgentLLMWorker` / `AgentToolWorker` / `AgentFinalizerWorker`

## Team 新建统一形式（硬规则 · 其他 agent 必须遵守）

### 目录结构

```
src/omnicompany/packages/services/<team_name>/     # 或 domains/<area>/<team_name>/
├── __init__.py                # re-export 主 API + 兼容 shim
├── DESIGN.md                  # 必须 active (七节 + 可选接收意愿)
├── manifest.yaml              # 可选: workers 清单 (Phase A 启用时)
├── formats.py                 # Material 定义 (含 kind.* tag)
├── workers/                   # Worker 子类集合
│   ├── __init__.py            # ALL_WORKERS 清单
│   ├── <worker_1>.py
│   ├── <worker_2>.py
│   └── ...
├── run.py                     # 可选: 提供 run_<team>(job_spec) 入口函数
└── _archive/                  # 类 A 迁移后旧文件归档位置 (若有)
    └── README.md              # 归档清单 + 原因 + 新位置
```

### Material 定义约定（[format.md](../../../../../../docs/standards/concepts/material.md) F-01..F-19）

```python
# formats.py
from omnicompany.protocol.format import Format

MY_SOURCE = Format(
    id="<team>.<material>.request",
    name="...",
    description="...",          # F-01 五要素完备 + F-02 ≥ 100 字
    parent="requirement",
    tags=["<team>", "kind.source"],     # ← F-19 必含 kind.*
    json_schema={...},          # F-06 与 description 一致
    examples=[...],             # F-07 ≥ 1 合法样例
)

MY_INTERNAL = Format(
    id="<team>.<intermediate>",
    ...,
    tags=["<team>", "kind.internal"],
)

MY_SINK = Format(
    id="<team>.<output>",       # 或标准 sink (stdout / audit_log.entry 等)
    ...,
    tags=["<team>", "kind.sink"],
)

ALL_FORMATS = [MY_SOURCE, MY_INTERNAL, MY_SINK]
```

### Worker 基类约定（[router.md](../../../../../../docs/standards/concepts/worker.md) R-01..R-25）

```python
# workers/my_worker.py
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

class MyWorker(Router):
    """职责单一, 完整 FORMAT 边界, 独立测试价值 (R-18 粒度)."""

    DESCRIPTION = "..."         # R-01 ≥ 20 字符
    FORMAT_IN = "<team>.<material>.request"        # 或 list[str] + FORMAT_IN_MODE
    # FORMAT_IN_MODE = "and" | "or"                # R-24 · list[str] 时必填
    FORMAT_OUT = "<team>.<output>"                 # 单 Format (Protocol 约定)

    def run(self, input_data: dict) -> Verdict:
        # input_data[FORMAT_IN_id] = 上游产出的 payload (平铺字段)
        req = input_data.get("<team>.<material>.request", {})

        result = do_work(req)

        # Verdict.output = FORMAT_OUT payload 本体 (平铺, R-23)
        return Verdict(
            kind=VerdictKind.PASS,
            output={"field1": ..., "field2": ...},
            # 可选: output["_emit_as_new_job"] = True → 子 job (R-25)
        )
```

### workers/__init__.py 清单

```python
from .my_worker_1 import MyWorker1
from .my_worker_2 import MyWorker2

ALL_WORKERS = [MyWorker1, MyWorker2]

__all__ = ["MyWorker1", "MyWorker2", "ALL_WORKERS"]
```

### DESIGN.md 必填项（design_md_template.md）

- 状态（active / design / skeleton）
- 核心目的（解决 / 不解决）
- 核心接口（Worker 清单 + 主函数）
- 架构决策（至少 3 条, 含 Worker 粒度 / Material kind / 预算等）
- 数据流 / 拓扑（订阅图 · ASCII art）
- 已知局限
- 参考资料
- 接收意愿（基础设施模块必填）

## 架构决策

### D1 — Material ledger 掌权，EventBus 只投影

MaterialDispatcher 的 run-scoped ledger 保存正文、因果和激活游标；跨 run 查询由 canonical Material authority/BlackboardCoordinator 提供。`SQLiteBus` / `MemoryBus` 只接收 bodyless `material.published` 审计投影，投影失败不阻断 Material 执行。

### D2 — 引入 `Worker` 基类 + `Material` / `Team` 别名（2026-04-20 用户洞察修正）

**原决策**（错）: "不引入 Worker 基类, 仍用 Router". 这让其他 agent `import` 时看到的是 `Router` / `Format`, docstring 也旧 — 规范文档里"读作 Worker"他们不会去翻 terminology.md.

**修正**（对）: 在 [`worker.py`](worker.py) 提供:
- `class Worker(Router)` — 真基类, 带 `FORMAT_IN_MODE` 默认值 + 完整 docstring 说明 R-23/R-24/R-25 约定
- `Material = Format` — protocol Format 的语义别名
- `Team = PipelineSpec` — protocol PipelineSpec 的语义别名

**新代码第一眼看到的词汇**:
```python
from omnicompany.packages.services.omnicompany import Worker, Material, Team

class MyWorker(Worker):
    FORMAT_IN = "myteam.input"      # 字段保 FORMAT_IN (Protocol 契约, 不改名)
    FORMAT_OUT = "myteam.output"
    def run(self, input_data) -> Verdict: ...
```

**对旧代码**: `class FooRouter(Router)` 原地继续工作（Worker 只是 Router 的子类别名）。迁移 Worker 继承不是必须。

### D3 — TeamRunner 与 MaterialDispatcher 分工, 不是二重权威

TeamRunner 是显式 DAG 编排: 调用方已经知道流程节点和边, 适合 50+ 注册管线的有向执行。
MaterialDispatcher 是材料黑板执行器：Worker 通过 FORMAT_IN 订阅直接 ledger 中的 material，由 material 到达触发激活。二者共享 Format 体系但不共享状态权威；FactoryEvent 只作为现有 `run_job()` 返回值的兼容信封。

### D3b — 兼容原 PipelineRunner

Dispatcher 与 PipelineRunner 并存。Worker 按 R-23 平铺 output 两者都能消费。迁移期自然共存。

### D4 — 子 job 语义作为 Agent Team / Validator 循环的统一机制

所有"循环"（agent multi-turn / validator retry / tool subcall）**统一**通过 `_emit_as_new_job` + parent_job_id 链表达（R-25）。废弃 while 循环内状态保持（违反 R-07 Statelessness）。

### D5 — Q4 诊断作为 material 级编译器

`orphan_workers()` + `unconsumed_materials()` 是订阅图的**静态完整性检查**。每个 Team 跑 dispatcher 一次等于做一次"编译检查"。

## 数据流 / 拓扑

```
[外部调用]
    ↓  初始 material (kind.source)
MaterialDispatcher.run_job()
    ↓ append 初始 material → run-scoped ledger (scope=job_id)
    ↓
循环:
    ├─ 读取 ledger 待处理 material
    ├─ 按 event_type 匹配订阅 worker (FORMAT_IN_MODE 决定累计策略)
    ├─ Q1 单次激活 key: (event.trace_id, worker_id)
    ├─ 激活 → worker.run(input_data) → Verdict
    ├─ kind=PASS → append output (format_id=FORMAT_OUT)
    │   └─ output._emit_as_new_job=True → 用新 trace_id (子 job, R-25)
    └─ kind=FAIL → 不 publish (Q2.C 控制流退化为 material 不产出)
    ↓
直到 ledger 无新 material 或达 max_iterations
    ↓
可选投影 bodyless material.published 审计事件
    ↓
返回全部兼容信封 (含 source / internal / sink + 诊断结果)
```

## 已知局限

1. **当前 dispatcher 是同步顺序激活** — Q3 并发 (多 worker 订阅同 material) 按 workers 列表顺序跑, 不并行。简单 team 无影响, 高负载 team 需 Phase 2 扩异步并行。

2. **预算机制未实装 Q2.A 三上限** — 仅有 `max_iterations` 全局兜底, 未区分 `max_workers_per_job` / `max_child_jobs` / `max_job_tree_depth`。Phase 1 pilot 够用, Phase 2 完善。

3. **Workspace 集成未实装** — F-17 material 大明文走 workspace 的 WorkspaceWriterWorker (R-22) 尚无 pilot。当前 Team 的 material 都是结构化 dict。

4. **只用 MemoryBus 测过** — SQLiteBus 真跑还没测。subscribe API 在 SQLiteBus 里是真实 pub/sub, 语义应该兼容但需验证。

## 参考资料

- **规范**:
  - [terminology.md §7 / §11 / §12](../../../../../../docs/standards/_global/terminology.md) — Agent Team / Job 发起者 / 迁移分型
  - [router.md R-18 / R-19 / R-20 / R-21 / R-22 / R-23 / R-24 / R-25](../../../../../../docs/standards/concepts/worker.md) — Worker 规范
  - [format.md F-16 / F-17 / F-18 / F-19](../../../../../../docs/standards/concepts/material.md) — Material 规范
- **金标样本**:
  - [guardian/workers/](../guardian/workers/) — 类 A 迁移 4 Worker 样本
  - [agent_team_demo.py](agent_team_demo.py) — Agent Team 4 Worker 样本
- **Plan**:
  - 材料 Wiki 全量设施化总计划
  - BLACKBOARD-ARCHITECTURE/plan.md
  - migration_log.md
- **SKILL**: `.claude/skills/omnicompany-dev/SKILL.md` §2.10 / §2.11

## 接收意愿

- **welcome_themes**:
  - dispatcher 优化（并发激活 / durable Material authority adapter）
  - 新 pub/sub 模式 / 更高效订阅匹配
  - Q4 诊断规则扩展（新的 orphan / redundant 检测）
  - Agent Team 变体（Diagnosis Agent Worker 实装）
  - Workspace 集成（WorkspaceWriterWorker pilot）
- **hard_constraints**:
  - 必须用现有 EventBus 抽象（不另起 bus）
  - Worker 必须继承 protocol.Router（不另起 Worker 基类）
  - 单 job 内 worker 单次激活（Q1 铁律）
- **soft_preferences**:
  - 偏好 Python 原生异步（asyncio）超过线程池
  - 偏好结构化 Verdict.output 超过自由 dict
- **maturity_preference**: `any`（Phase 1 pilot 期, 欢迎新思路）
