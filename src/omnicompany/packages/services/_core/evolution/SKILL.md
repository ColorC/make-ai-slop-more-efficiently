---
name: evolution
description: omnicompany 假设驱动演化工作流 - 捕获 QualityPainSignal 跑 5 阶段循环 (浅追踪/诊断/实验/分析/状态更新), 跨会话 HypothesisBoard 状态载体.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/evolution ts=2026-05-04T14:55:00Z type=doc status=design agent=ai-ide belongs_to_service=evolution -->
<!-- [OMNI] summary="evolution 操作手册 — 跑演化循环的 4 个 CLI 命令 (shallow-trace/evolve/list-boards/show-board) 操作步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §六 模板严格写. status=design 说明工作流可能仍迭代, 但 SKILL 反映当前可用的操作" -->
<!-- [OMNI] tags=skill,evolution,how-to,core,design-stage -->
<!-- [OMNI] material_id="material:services._core.evolution.skill.operations_manual.md"-->

# evolution · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).
>
> ⚠ status=design, 工作流仍可能迭代, SKILL 反映当前可用操作.

---

## 适用范围

**用我**:
- 怀疑某管线 / Worker 有**慢性质量退化** (LLM 输出渐渐变差 / 某节点偶发 fail 但不立即 critical)
- 想自动跑假设 + 实验闭环找原因
- 想看历史 HypothesisBoard 状态 (跨会话持久)

**不用我**:
- 急性 critical 问题 → 找 [guardian](../guardian/SKILL.md) (源码合规) / [doctor](../../_diagnosis/doctor/SKILL.md) (单对象语义)
- 已知问题立即修 → 找 [repair](../repair/SKILL.md)
- 业务正确性测试 → 各 domain Team 自测
- 实时在线热更新 → evolution 是离线沙盒, 不支持热更

## 前置条件

- omnicompany 已装 (`omni --help` 确认)
- 有 `THE_COMPANY_API_KEY` (DiagnosisAgent 调 LLM)
- 有 trace 历史可供 ShallowTracer 浅追踪 (跑过相关管线留下 event.db)
- HypothesisBoardStore 用独立 SQLite (`evolution_boards.db`), 自动创建无需手工

## 操作步骤

### 场景 A · 浅层追踪某 trace 提取 Pain Signal

```bash
omni evolve shallow-trace --trace-id=<trace_id>
```

**用途**: 给某具体 trace 跑浅追踪, 提取关键 trace 片段 + 节点边界, 输出 QualityPainSignal.

### 场景 B · 跑完整演化循环 (B.1~B.5)

```bash
omni evolve --pain-signal=<path/to/pain_signal.json> --max-cycles=5
```

**用途**: 给一个已有 PainSignal 跑完整循环 (诊断 → 实验 → 分析 → 状态更新), max 5 轮.

**验证**: 跑完看 `evolution_boards.db` 里的 board 状态 (status / max_confidence_hypothesis), 或:

```bash
omni evolve list-boards
omni evolve show-board <board_id>
```

### 场景 C · 看现有 HypothesisBoard

```bash
omni evolve list-boards                       # 列所有 board (按时间)
omni evolve show-board <board_id>             # 看具体 board 详情 (假设清单 / confidence / status)
```

### 场景 D · 库调用

```python
from omnicompany.packages.services._core.evolution.workflow.orchestrator import EvolutionOrchestrator
from omnicompany.packages.services._core.evolution.workflow.hypothesis_store import HypothesisBoardStore

store = HypothesisBoardStore("evolution_boards.db")
orchestrator = EvolutionOrchestrator(store=store)
final_board = orchestrator.run(pain_signal=..., max_cycles=5)
```

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `omni evolve shallow-trace` | B.1 浅追踪 | `--trace-id` |
| `omni evolve` (主命令) | 跑完整 B.1~B.5 循环 | `--pain-signal` `--max-cycles` |
| `omni evolve list-boards` | 列 board | (无) |
| `omni evolve show-board <id>` | 看 board 详情 | `<board_id>` |
| `EvolutionOrchestrator.run()` (Python) | 库调用 | 见 [workflow/orchestrator.py](workflow/orchestrator.py) |
| `HypothesisBoardStore.load(board_id)` (Python) | 直接读 board | 见 [workflow/hypothesis_store.py](workflow/hypothesis_store.py) |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| `shallow-trace` 报 trace 找不到 | event.db 没那个 trace_id | 跑 `omni traces` 看实际 trace_id |
| `evolve` 跑满 max-cycles 仍未 done | 假设都 ELIMINATED 但没 CONFIRMED | 看 board 输出, 可能 PainSignal 描述太宽, 缩范围重跑 |
| ExperimentRunner 报 ImportError | `ProposedChange` 转的补丁有 import 问题 | 看 board 里的 patch 历史, 调整 `prompt`/`logic` 类生成 |
| HypothesisBoard 并发读写挂 | 当前无分布式锁 (局限 2) | 单进程跑, 多进程并发待 ServiceBus 迁移 |
| DiagnosisReport 内容空泛 | LLM 没给具体 evidence | 当前局限 (局限 3), 升级路径加 Pydantic 强类型校验 |
| `insert_node` / `split_node` 类结构变更没自动跑 | 当前仅 prompt/logic 类自动 (局限 1) | 结构变更需手工实施, 等 B.3 集成 libcst |
| 想 reopen 已 ELIMINATED 假设 | 当前不支持 | 重建新 PainSignal 并 evolve |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 (D1-D4 决策 / 5 阶段数据流) → [DESIGN.md](DESIGN.md)
- 跟 self_repair 合并计划 → [docs/plans/_archive/[2026-04-23]SELF-STABLE-CORE/](../../../../../docs/plans/_archive/)
- HypothesisBoard 数据结构 → [docs/plans/_archive/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/HYPOTHESIS_BLACKBOARD.md](../../../../../docs/plans/_archive/)
