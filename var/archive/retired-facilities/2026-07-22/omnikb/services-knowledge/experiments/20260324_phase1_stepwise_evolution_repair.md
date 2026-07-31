# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:20Z
---
omnikb_type: kexp
id: kb.experiment.20260324_phase1_stepwise_evolution_repair
name: Phase 1：步进式进化系统修复计划
tags:
- topic.plan
- date.2026-03-24
maturity: draft
summary: '**日期**: 2026-03-24 **状态**: 待执行 **背景**: Marathon 盲跑已暂停，所有 Python 进程已停止。Guardian
  监视循环已取消。'
date_started: '2026-03-24'
method_summary: see docs/plans/[2026-03-24]PHASE1-STEPWISE-EVOLUTION-REPAIR/
status: documented
followups:
- docs/plans/[2026-03-24]PHASE1-STEPWISE-EVOLUTION-REPAIR/insight-evaluation-and-meta-drive.md
- docs/plans/[2026-03-24]PHASE1-STEPWISE-EVOLUTION-REPAIR/README.md
- docs/plans/[2026-03-24]PHASE1-STEPWISE-EVOLUTION-REPAIR/step2-verifiable-tasks-and-pioneer-redesign.md
---

# Phase 1：步进式进化系统修复计划

> Plan directory: `docs/plans/[2026-03-24]PHASE1-STEPWISE-EVOLUTION-REPAIR/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# Phase 1：步进式进化系统修复计划

**日期**: 2026-03-24
**状态**: 待执行
**背景**: Marathon 盲跑已暂停，所有 Python 进程已停止。Guardian 监视循环已取消。

---

## 一、诊断结论

Marathon 连续运行积累了大量数据，但反馈回路从根本上是失效的：

| 问题 | 数据 |
|------|------|
| 路由事件总数 | 63 条 |
| 真正有效的反馈（agent_ok + align≥0.7） | 4 条（6.3%） |
| `agent_success` 的实际含义 | agent 有输出且不超时（不代表做对了） |
| alignment 评估 | LLM 印象分，无地面真相，成功/失败均值差异<0.1 |
| inference_edges（路径层知识） | 4 条（几乎为零） |
| 路由多样性 | 55+次路由走同一个节点 |
| 重复节点 | 22 组 62 个（同描述前缀的冗余节点） |
| 零 hit 的 hypothetical 节点 | 445 个（从未被路由到） |
| hard 节点卡死（hit≥15, succ=0） | 41 个（无法进化但仍在路由池） |

**核心问题**：进化算法在一个无法区分好坏的信号上运行，等于在噪声上优化。任务全部是开放式分析任务，没有可验证的期望输出，LLM judge 给任何"看上去像分析"的输出打 0.3-0.5 分，系统永远不知道自己做对了还是做错了。

---

## 二、目标

在重启持续运行之前，达到以下验收标准：

1. **反馈可信**：至少 50% 的路由任务有硬边界验证结果（而非 LLM 印象分）
2. **痛觉可传导**：节点失败时，pain 能正确积累并触发进化（而非 pain=0 永远留在路由池）
3. **路由可观察**：能从日志/DB 看出哪条路径被选中、执行结果、是否触发进化
4. **知识有沉淀**：完成一个完整循环后，inference_edges 数量增加，mature 节点增加
5. **数据库干净**：冗余节点清理完毕，autonomous/ 目录文件有序

---

## 三、执行步骤

### Step 0：数据备份

```bash
cd e:/WindowsWorkspace/omnicompany
cp data/autonomous/semantic_network.db data/autonomous/semantic_network.db.phase1.bak
cp data/autonomous/route_graph.db data/autonomous/route_graph.db.phase1.bak
```

---

### Step 1：数据库清理（减少噪声节点）

**目标**：让路由从 2166 个噪声节点变为更小的高质量候选池。

#### 1a. 触发 FMerge（合并重复节点）

```bash
python -c "
import asyncio, sys
sys.path.insert(0,'src')
from omnicompany.evolution.graph_builder import FMergeRouter
r = FMergeRouter('data/autonomous/semantic_network.db')
result = asyncio.run(r.run({}))
print(resu
```

## Plan files

- `docs/plans/[2026-03-24]PHASE1-STEPWISE-EVOLUTION-REPAIR/insight-evaluation-and-meta-drive.md`
- `docs/plans/[2026-03-24]PHASE1-STEPWISE-EVOLUTION-REPAIR/README.md`
- `docs/plans/[2026-03-24]PHASE1-STEPWISE-EVOLUTION-REPAIR/step2-verifiable-tasks-and-pioneer-redesign.md`

## Hypothesis

_(待补充: 这个 plan 的核心假设, 为什么需要做)_

## Method

_(待补充: 实施方法, 关键步骤)_

## Samples

_(待补充: 跑过的样本, 各自结果)_

## Findings

_(待补充: 关键发现, 哪些假设被验证, 哪些被推翻)_

## Followups

_(待补充: 后续 TODO, 关联其他 plan / 计划目录中的文件已自动列在 frontmatter)_

## Change log

- 2026-04-08 — auto-seeded from plan README
