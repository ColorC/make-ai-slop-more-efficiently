<!-- [OMNI] origin=claude-code domain=services/hypothesis ts=2026-07-10T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:services.learning.hypothesis.service.design_doc.md" -->

# Hypothesis Service · 设计文档

## 状态

- **版本**: V5(决策库收编版,取代 V4 khyp 文档版)
- **成熟度**: active
- **权威**: `docs/plans/[2026-07-10]DECISION-ONTOLOGY/plan.md` 合并清单#1(停机一次性迁移)

## 核心目的

把"agent 通过探索学习假设"沉成**统一决策库的 belief 记录**——猜想=事实性陈述的未验证态,
与日常决策同库同设施(nature=factual,生命周期 untested→challenged→supported|partial|falsified)。
本服务不再有任何自有存储;主题摘要是生成投影(docs/ontology/30-知识.md),可随时重建。

## 核心接口

**两个核心 Router**(管线节点):
- **`ExperimenterRouter`** — AgentNodeLoop,主探索 agent;bash/read_file/glob/grep 自由探索,输出行为轨迹 — _archive/routers_legacy.py(注:_archive 命名历史沿用,内容是现役实现)
- **`BeliefReflectorRouter`** — AgentNodeLoop,总结 agent;读轨迹 + belief 快照,用决策库五件套直接维护统一库 — [workers/belief_reflector.py](workers/belief_reflector.py)

**决策库五件套**(Reflector 的工具, [belief_tools.py](belief_tools.py)):
- `list_beliefs` / `record_belief` / `challenge_belief` / `resolve_belief` / `link_belief`
- 硬门:risk_if_wrong 必填;rests_on 必须是库内真 id;falsified 自动点名 rests_on 下游(回传必做)

**数据层**:统一决策库 `data/domains/decisions/library/records.jsonl`(kind=belief,
tags=[hypothesis-explore, domain:<x>])。无自有存储。

**投影**:`omni decisions knowledge` 重渲 30-知识(belief 按域摘要);session 收工自动重渲。

**管线入口**:`omni run hypothesis -i domain=<x> -i goal="..."`(注册于 core/pipelines.py,
confirm=True 长管线确认门);真实多轮循环=team.run_session()。

## 架构决策

### D1 — 探索与日常决策同一套设施(合并清单#1)

V4 的 khyp 主题文档是"存储即视图"的双轨:文档既当真源又当阅读面,与决策库并行。
V5 拆开:库存(belief 进统一库)+ 摘要投影(30-知识,可重建)。
拆除件:HypothesisStore(V1 死代码)、KHypothesisEntry/validator/knowledge.graph(khyp 体系)、
reflector_daemon + lockstep 双脑模式(无生产调用方)。
V1 遗留 session 数据快照归档于 data/_archive/hypothesis_v1_sessions_20260710/。

### D2 — Reflector 判断纪律来自手册

软判断(什么时候升级/什么算证据)写在 NODE_PROMPT,对齐 docs/ontology/20-探索通则.md
(反证优先/回传必做/推导带出处);硬校验在库层(library.validate_record)。

### D3 — Experimenter 不变

自由探索产轨迹的部分与 V4 相同;store 快照现由 belief_tools.beliefs_snapshot() 现算,
形态兼容(id/state/trigger/predicted)。Reflector 两路输入(factlog+快照)缺一不可:
只有 factlog 会重复立已知猜想,只有快照没证据可对照。

## 测试

- tests/services/_learning/hypothesis/test_belief_tools.py — 五件套直写库/域归属/前提必真/证伪点名下游/投影渲染(确定性,零 LLM)
- 真跑冒烟:`omni run hypothesis -i domain=<小目标> -i goal="..." -i max_iterations=1 --yes`
