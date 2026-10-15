<!-- [OMNI] origin=claude-code domain=services/hypothesis ts=2026-07-10T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:services.learning.hypothesis.service.design_doc.md" -->

# Hypothesis Service · 设计文档

## 状态

- **版本**: V6(外置 AgentRuntime 版,延续 V5 决策库收编)
- **成熟度**: active
- **权威**: `docs/plans/[2026-07-10]DECISION-ONTOLOGY/plan.md` 合并清单#1(停机一次性迁移)

## 核心目的

把"agent 通过探索学习假设"沉成**统一决策库的 belief 记录**——猜想=事实性陈述的未验证态,
与日常决策同库同设施(nature=factual,生命周期 untested→challenged→supported|partial|falsified)。
本服务不再有任何自有存储;主题摘要是生成投影(docs/ontology/30-知识.md),可随时重建。

## 核心接口

**两个核心 Router**(管线节点):
- **`ExperimenterWorker`** — DSH/Cordis 只读探索并返回带相对路径、精确行区间和逐字引文的 evidence proposals；Omni 校验后才组成 factlog — [workers/experimenter.py](workers/experimenter.py)
- **`BeliefReflectorRouter`** — 零工具外置 AgentRuntime 只返回 operation proposals；Omni 整批验证后调用 canonical decision library 并回读终态 — [workers/belief_reflector.py](workers/belief_reflector.py)

**决策库五件套**(Omni 控制面的兼容工具/API, [belief_tools.py](belief_tools.py)):
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

### D2 — Reflector 采用 proposal/application 分层

软判断(什么时候升级/什么算证据)由外置 DSH 提案，对齐 docs/ontology/20-探索通则.md。
DSH 没有决策库写工具；Omni 在任何写入前整批检查域内真 ID、去重、先挑战后 supported、
证伪后的下游 challenge、前提边与字段完整性，再由 canonical library 应用。执行中途若库失败，
Verdict 明确返回已应用动作，禁止把部分成功伪装成完整成功。

### D3 — Experimenter 外置，证据权威留在 Omni

旧 Python 内循环及任意 Bash/HTTP/写文件能力已删除。DSH 只获得工作区 read/list/search/
read-lines，输出 source-backed observations；Omni 在交给 Reflector 前逐条验证相对路径未越界、
文件存在、行区间有界、引文逐字命中且 belief id 属于当前快照。需要命令、网络、图片或外部状态
时必须返回 information_insufficient Andon，不会回退旧 loop。store 快照仍由
belief_tools.beliefs_snapshot() 现算；Reflector 两路输入(factlog+快照)缺一不可。

game-ui shadow 不会被伪装成普通文件探索：现行生产入口 fail closed，并在 AgentRuntime 迁移计划
中保留独立、限时的视觉提案窗口。窗口关闭条件是 Cordis 具备 grant-scoped 图像读取/裁片能力，
DSH 只返回 probe proposals，Omni 继续持有几何校验、风险门、账本写入和执行授权。

### D4 — EventBus 只记领域里程碑

会话仍可发布 TASK_INTENT / STATE_CHANGE / TASK_FINISH，但事件只含 session/domain/status/hash/计数；
goal、scene、Agent trace 与 belief 库正文不进入 EventBus。AgentRuntime 私有 session 也不投影成业务 stock。

## 测试

- tests/services/_learning/hypothesis/test_belief_tools.py — 五件套直写库/域归属/前提必真/证伪点名下游/投影渲染(确定性,零 LLM)
- tests/services/_learning/hypothesis/test_belief_reflector_external.py — 外置零工具、整批拒绝、挑战/裁定纪律、证伪回传与部分应用证据
- tests/services/_learning/hypothesis/test_experimenter_external.py — 外置只读 grant、逐字证据校验、越界拒绝、Andon 与 shadow fail-closed
- 真跑冒烟:`omni run hypothesis -i domain=<小目标> -i goal="..." -i max_iterations=1 --yes`
