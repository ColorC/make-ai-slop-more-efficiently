---
name: omnicompany
description: omnicompany 黑板架构中心 - 用 Worker/Material/Team 基类建新 Team, 用 MaterialDispatcher 跑订阅激活, 用 Q4 诊断检订阅图.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/omnicompany ts=2026-05-04T14:20:00Z type=doc status=active agent=ai-ide belongs_to_service=omnicompany -->
<!-- [OMNI] summary="omnicompany 操作手册 — 建新 Team 的步骤 + MaterialDispatcher 用法 + Q4 诊断 + 入口清单 + 故障排查" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §六 模板严格写. omnicompany 是中心承载, SKILL 主要是'怎么从 import Worker 起手到跑通 Team' 的工程指引" -->
<!-- [OMNI] tags=skill,omnicompany,how-to,core,blackboard -->
<!-- [OMNI] material_id="material:services._core.omnicompany.skill.operations_manual.md"-->

# omnicompany · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构 + Team 新建硬规则 请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- 想建新 Team (Worker / Material / Team 基类 + 目录结构 + formats.py + workers/)
- 想用 MaterialDispatcher 跑订阅激活管线
- 想给已有 Team 跑 Q4 诊断 (orphan_workers / unconsumed_materials)
- 想看 Agent Team 4 Worker 金标范本

**不用我**:
- 想跑业务 Team → 直接 `omni run <team_id>` (业务 Team 用 omnicompany 的基类, 但跑由 PipelineRunner / dispatcher 触发)
- 想看文档层三件套规范 → 找 [self_narrative_three_files.md](../../../../../../docs/standards/protocol/self_narrative_three_files.md)
- 想看命名迁移进度 → 找 [terminology.md](../../../../../../docs/standards/_global/terminology.md)
- 想看 Worker / Material 设计单 → 找 [worker.md](../../../../../../docs/standards/concepts/worker.md) / [material.md](../../../../../../docs/standards/concepts/material.md)

## 前置条件

- omnicompany 已装 (`omni --help` 确认)
- 知道 Worker / Material / Team 基本概念 (看 [README.md](README.md))
- 建新 Team 时, 知道你的 Team 想做什么 (业务目标), 然后按硬规则套结构

## 操作步骤

### 场景 A · 建新 Team (核心场景, 其他 agent 必读)

按 [DESIGN.md `## Team 新建统一形式`](DESIGN.md) 硬规则:

1. **建目录** `src/omnicompany/packages/services/<team_name>/` (或 `domains/<area>/<team_name>/`)
2. **加 7 个文件**:
   - `__init__.py` (re-export 主 API + 兼容 shim)
   - `DESIGN.md` (七节, 见 design_md_template.md)
   - `README.md` (语境 / 目的 / 规划, 见 [self_narrative_three_files.md §四](../../../../../../docs/standards/protocol/self_narrative_three_files.md))
   - `SKILL.md` (操作手册, 见 [self_narrative_three_files.md §六](../../../../../../docs/standards/protocol/self_narrative_three_files.md))
   - `formats.py` (Material 定义, 含 kind.* tag)
   - `workers/__init__.py` + `workers/<worker_n>.py` (每个 Worker 一个文件)
   - `run.py` (可选, 提供 `run_<team>(job_spec)` 入口)
3. **Material 定义** (`formats.py`):
   ```python
   from omnicompany.packages.services._core.omnicompany import Material  # = Format alias

   MY_SOURCE = Material(
       id="<team>.<material>.request",
       description="...",         # F-01 五要素 + F-02 ≥ 100 字
       tags=["<team>", "kind.source"],     # F-19 必含 kind.*
       json_schema={...},
       examples=[...],            # F-07 ≥ 1 合法样例
   )
   ```
4. **Worker 定义** (`workers/<worker_n>.py`):
   ```python
   from omnicompany.packages.services._core.omnicompany import Worker

   class MyWorker(Worker):
       DESCRIPTION = "..."         # R-01 ≥ 20 字符
       FORMAT_IN = "<team>.<material>.request"
       FORMAT_OUT = "<team>.<output>"
       def run(self, input_data) -> Verdict: ...
   ```

详细见 [DESIGN.md](DESIGN.md) Team 新建硬规则段.

### 场景 B · 跑订阅激活管线 (MaterialDispatcher)

```python
from omnicompany.packages.services._core.omnicompany import MaterialDispatcher
from omnicompany.bus import MemoryBus

bus = MemoryBus()
dispatcher = MaterialDispatcher(bus=bus, workers=[MyWorker1, MyWorker2, MyWorker3])
events = dispatcher.run_job(
    initial_material_id="<team>.<material>.request",
    initial_payload={"field1": ...},
    max_iterations=100,
)
print(events)  # 含 source / internal / sink 全部 event
```

### 场景 C · Q4 诊断 (订阅图静态完整性检查)

```python
events = dispatcher.run_job(...)
orphans = dispatcher.orphan_workers(events)        # Worker 订阅了无 producer 且非 kind.source
unconsumed = dispatcher.unconsumed_materials(events)  # Material 有 producer 但无 consumer 且非 kind.sink
print(f"orphans: {orphans}")
print(f"unconsumed: {unconsumed}")
```

**用途**: 写完 Team 跑一次, 确认订阅图无缺口. 也是 [doctor blackboard 子域](../../_diagnosis/doctor/SKILL.md) 的检查项之一.

### 场景 D · 看 Agent Team 4 Worker 金标范本

```python
# 看金标
from omnicompany.packages.services._core.omnicompany import agent_team_demo

# 4 Worker: AgentContextScript / AgentLLM / AgentTool / AgentFinalizer
# 体现 R-19 Agent Team 三件套 + 子 job + FORMAT_IN_MODE
```

文件: [agent_team_demo.py](agent_team_demo.py).

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `from omnicompany.packages.services._core.omnicompany import Worker` | Worker 基类 | (继承 Router) |
| `from ... import Material` | Material 别名 | (= Format) |
| `from ... import Team` | Team 别名 | (= PipelineSpec) |
| `MaterialDispatcher(bus, workers)` | 订阅激活引擎 | `.run_job(initial_material_id, initial_payload, max_iterations=100)` |
| `dispatcher.orphan_workers(events)` | Q4 诊断 | 列订阅无 producer 且非 source 的 Worker |
| `dispatcher.unconsumed_materials(events)` | Q4 诊断 | 列有 producer 但无 consumer 且非 sink 的 Material |
| [agent_team_demo.py](agent_team_demo.py) | 金标范本 | Agent Team 4 Worker 参考实现 |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| `dispatcher.run_job` 死循环不停 | Worker 互相订阅产生新事件无终止 | 减小 `max_iterations`; 检查 Worker 是否产 sink material 了, 还是不停产 internal |
| Q4 诊断报 orphan_workers 不空 | Worker 订阅了非 source material 但没 producer | 加 producer Worker, 或改这个 Material 标 `kind.source` |
| Q4 诊断报 unconsumed_materials 不空 | Material 有 producer 但没 consumer 且非 sink | 加 consumer Worker, 或改这个 Material 标 `kind.sink` |
| Worker 单次激活了多次 (违反 Q1) | dispatcher 实现 bug 或 Worker 自己 `_emit_as_new_job` 误用 | 检查 dispatcher event Q1 key (event.trace_id, worker_id), 看是否子 job 用了同一 trace_id |
| Worker 用 `from omnicompany.runtime.routing.router import Router` 但报 import 错 | 应该用 omnicompany 的 Worker 别名而非直接 Router | `from omnicompany.packages.services._core.omnicompany import Worker` |
| dispatcher 同步顺序激活慢 | 当前 Q3 不并发 (D1 / 局限 1) | Phase 2 backlog 加异步并行, 简单 team 先忍受 |
| 想用 SQLiteBus 但跑挂 | 当前只 MemoryBus 测过 (局限 4) | 当前局限, SQLiteBus 真跑测待 Phase 2 |
| 大 material payload 撑爆 dict | 没用 Workspace (F-17 大明文) | 当前 Workspace 集成无 pilot (局限 3), 业务 Team 暂走结构化 dict |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 + Team 新建硬规则 → [DESIGN.md](DESIGN.md)
- 文档层三件套规范 → [self_narrative_three_files.md](../../../../../../docs/standards/protocol/self_narrative_three_files.md)
- 命名迁移 → [terminology.md](../../../../../../docs/standards/_global/terminology.md)
- Worker 设计单 R-01~R-25 → [worker.md](../../../../../../docs/standards/concepts/worker.md)
- Material 五要素 F-01~F-19 → [material.md](../../../../../../docs/standards/concepts/material.md)
- Team 新建参考样本 → [../guardian/workers/](../guardian/workers/)
- 黑板架构 plan → BLACKBOARD-ARCHITECTURE/plan.md
