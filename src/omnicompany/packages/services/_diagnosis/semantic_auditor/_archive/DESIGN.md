<!-- [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-05-04T12:45:00Z type=doc status=active belongs_to_service=semantic_auditor -->
<!-- [OMNI] material_id="material:diagnosis.semantic_auditor.design_specification.md" -->

# semantic_auditor · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限).
>
> 形态: 行政部 Team (见 terminology §2 · 核心基础设施服务全公司).
> Clean Migration 完成 2026-04-20 夜 (Stage 2 完全迁移 · workers/ 子目录 + Worker 基类 + Material alias).

## 状态
- **版本**：V2（2026-04-20 · Clean Migration · workers/ 子目录拆分 + Material kind 标注完整）
- **成熟度**：active（5 Worker 骨架 + LLM + 落盘全链通；首批人工复核阈值校准待做）
- **下一步**：首批 Finding 实跑 + 人工复核校准 confidence 阈值（0.7）；接 Sentinel / CLI 触发入口（Phase C）

## 核心接口

### 管线入口（见 [pipeline.py](pipeline.py)）
- **`build_pipeline() -> PipelineSpec`** — SemanticAuditor 审计管线（Phase B1 含 3 节点：artifact_selector → standard_matcher → excerpt_retriever；B2 追加 llm_auditor → finding_writer）

### Workers（见 [workers/](workers/)）
- **`ArtifactSelectorWorker`** (Worker #1 · HARD) — 从 path 列表 / git diff / 全扫输入产出待审 `artifacts[]`，每个带 `kind` — FORMAT_OUT: `semantic_auditor.artifact-set` — [workers/artifact_selector.py](workers/artifact_selector.py)
- **`StandardMatcherWorker`** (Worker #2 · HARD) — 读 `standards-index.yaml`，按 artifact kind + 路径匹配适用 `standard_id[]` — FORMAT_OUT: `semantic_auditor.audit-target-set` — [workers/standard_matcher.py](workers/standard_matcher.py)
- **`ExcerptRetrieverWorker`** (Worker #3 · HARD) — 按 `excerpt_strategy`（full / section）取标准摘录 — FORMAT_OUT: `semantic_auditor.audit-excerpt-set` — [workers/excerpt_retriever.py](workers/excerpt_retriever.py)
- **`LLMAuditWorker`** (Worker #4 · async HARD 外壳) — 循环 excerpts 调度 `AuditAgent` 单审，合并 Finding — FORMAT_OUT: `semantic_auditor.finding-set` — [workers/llm_audit.py](workers/llm_audit.py)
- **`FindingWriterWorker`** (Worker #5 · HARD) — 验证 Finding 字段（必填 + confidence 区间 + standard_id 合法），append 到 `docs/tech_debt/REGISTRY.md §语义合规待审` + `docs/ARCH-CHANGES.jsonl` `event_type=finding-generated` — FORMAT_OUT: `semantic_auditor.finding-written` — [workers/finding_writer.py](workers/finding_writer.py)

兼容 shim: [routers.py](routers.py) 旧名 `*Router` 作为 `*Worker` 别名保留（不要新增代码）
归档: _archive/routers_legacy.py 原单文件 5-Router 实现 · 见 _archive/README.md

### Agent（见 [audit_agent.py](audit_agent.py)）
- **`AuditAgent`** — 继承 `packages.services.agent.AgentNodeLoop`（新版 Router 化，**非** legacy `runtime/agent`）；单次 run 处理一个 (artifact, standard, excerpt) 三元组 — TOOL_ROUTERS: `ReadFileRouter` / `GrepRouter` / `GlobRouter`（+自动 `FinishRouter`）
- 输出约定：LLM 通过 `finish` 工具提交 `{"findings": [...]}`，LLMAuditRouter 外壳解析 JSON 并合并

### 标准索引加载器（见 [standards_loader.py](standards_loader.py)）
- **`load_standards_index(root) -> StandardsIndex`** — 读 `docs/standards/standards-index.yaml`，验证 schema
- **`infer_kind(path, index) -> str | None`** — 按 `kind_inference` 规则推断 artifact 类型
- **`match_standards(kind, path, index) -> list[str]`** — 给 (kind, path) 返回适用 standard id 列表
- **`retrieve_excerpt(standard_id, root, index) -> str`** — 按 excerpt_strategy 取标准内容

## 架构决策

### D1 — 为什么是独立服务包而不是 Guardian 的扩展

Guardian 规则是"每次 commit 快速确定性检查"（<30s），SemanticAuditor 是"按需/周期深度 LLM 检查"（2-5 min）。两者的触发频率、执行时长、输入形态、输出类型完全不同：

- 合一会污染 Guardian 的快速路径（LLM 慢 + 非确定性）
- 分离后共用 `docs/tech_debt/REGISTRY.md` 作为出口（Guardian 写 §活跃违规，SemanticAuditor 写 §语义合规待审），互补不重叠

### D2 — standards-index.yaml 作为"审计路由表"而非"标准内容"

问题：`SKILL.md` 1491 行 + `docs/standards/` 2485 行，全部喂 LLM 一定 context 溢出。

解法：`docs/standards/standards-index.yaml` 机器可读索引，维护三件事：
- 哪些文件 → 哪些 standard 适用（`path_match` glob）
- 取 standard 的哪些部分（`excerpt_strategy: full | section` + `key_sections`）
- 怎么从路径推 artifact `kind`（`kind_inference`）

LLM 每次只看**一个 artifact + 其适用 standards 的摘录**，不是全量标准。这是 `llm_first.md §3 信息充分性`的直接兑现。

### D3 — Router 纯粹：HARD Router 不调 LLM，LLMAuditRouter 走 AgentNodeLoop

Phase B1 的三个 HARD Router 完全确定性（读 yaml、glob、切 section），零 LLM 调用。Phase B2 的 `LLMAuditRouter` 必须是 `packages/services/agent/AgentNodeLoop` 子类（带 `read_file` / `grep` 工具），不是单轮 LLM 调用——因为大文件看不完 + 需要跨文件追溯。

遵循 CLAUDE.md "AgentNodeLoop 必须挂 EventBus" 铁律：所有 Format 都要进 bus，`LLMAuditRouter` 内部 LLM/tool/compact 调用全部事件落盘。

### D4 — Finding 格式固定：必须引 standard_id + confidence + line_hint

B2 产出的 Finding 不允许自由文本，强制字段：
- `standard_id`：引用 `standards-index.yaml` 的 id（审计必须指向已登记规范，不允许 LLM 自造规则）
- `confidence`：0.0~1.0，< 0.7 → status=`needs_human_review` 不直接写入 REGISTRY 主表
- `line_hint`：疑似违规所在行（读文件时必备）
- `recommended_action`：可执行修复描述

约束反映到 `FindingWriterRouter` 的 validator，不满足即丢弃。

### D5 — 命名纪律遵守 CLAUDE.md（2026-04-18）

类名 / 文件名不挂版本后缀。`AuditAgent` 继承 `packages/services/agent/AgentNodeLoop`（非 legacy `runtime/agent/AgentNodeLoop`，后者已 DEPRECATED）。新旧靠 import path 区分，不靠名字。

### D6 — LLMAuditRouter 作为薄外壳，单审逻辑全在 AgentNodeLoop（2026-04-18 Phase B2）

用户纪律："能 Router 体系就 Router 体系，Router 体系进 EventBus 审计优越"。问题：Pipeline 当前不原生支持 fan-out（每个 excerpt 一次 LLM）。

解法：`LLMAuditRouter` 是 async HARD **外壳**（仅做循环调度 + JSON 解析），单次审计逻辑全部在 `AuditAgent`（AgentNodeLoop 子类）。含义：
- 所有 LLM / tool / compact / prompt 事件**自动**通过 AgentNodeLoop 的 bus 接入层落盘（见 `_bus.py` `emit_agent_signal` / `emit_router_input` / `emit_router_output`）
- 外壳层仅产生 1 个节点级 verdict（`semantic_auditor.finding-set`），无自定义 bus 事件
- 将来若 Pipeline 支持 fan-out 原语，`LLMAuditRouter` 可退化为透传，Pipeline 直接以 `AuditAgent` 作为节点

### D7 — Finding 严格字段校验，拒绝 LLM 产出不合规数据（2026-04-18 Phase B2）

`FindingWriterRouter` 执行铁律：
- 必填字段：`standard_id` / `target_path` / `description` / `confidence` / `recommended_action`
- `standard_id` 必须在 `standards-index.yaml` 注册（拒绝 LLM 自造规则）
- `confidence` 必须在 [0.0, 1.0]；`< 0.7` → `status=needs_human_review` 不进 open 主流
- 去重键：`(standard_id, target_path)` 已存在 `open` / `needs_human_review` 条目不重复写
- 拒绝原因登记到返回 `output.rejected` 供上游追溯（不阻塞整批写入）

## 数据流 / 拓扑

### 审计管线（Phase B1 仅三节点串行；B2 追加两节点）

```
[触发] (输入: list[path] | git-diff | full-scan)
   ↓
ArtifactSelectorRouter (HARD)
   ├─ 把 path 列表 / git-diff / 全扫结果 → list[Artifact{path, kind}]
   └─ kind 由 standards-index.yaml.kind_inference 推断
   ↓
StandardMatcherRouter (HARD)
   ├─ 每个 Artifact 查 standards-index.yaml，按 path_match + applies_to 匹配
   └─ 产出 list[AuditTarget{artifact, applicable_standards: [standard_id...]}]
   ↓
ExcerptRetrieverRouter (HARD)
   ├─ 每个 standard_id 按 excerpt_strategy 取内容（full / 指定 key_sections）
   └─ 产出 list[AuditExcerpt{target, standard_id, excerpt_text}]
   ↓
[Phase B2] LLMAuditRouter (AgentNodeLoop)
   ├─ 读 artifact 全文 + excerpt_text → 调用 qwen-3.6-plus 审
   └─ 产出 list[Finding{standard_id, target, description, confidence, line_hint, action}]
   ↓
[Phase B2] FindingWriterRouter (HARD)
   ├─ Finding 验证（字段全 + confidence 阈值）
   ├─ append 到 docs/tech_debt/REGISTRY.md §语义合规待审
   └─ append 到 docs/ARCH-CHANGES.jsonl event_type=finding-generated
```

### 数据落盘
- `docs/tech_debt/REGISTRY.md §语义合规待审` — 人类可读 Finding 表（由 B2 FindingWriterRouter 写）
- `docs/ARCH-CHANGES.jsonl` — append-only 事件流（`event_type=finding-generated`）
- `data/semantic_auditor/excerpts/<hash>.txt` — excerpt 缓存（避免重复切片；由 Phase B2 实装）

## 已知局限

1. **LLMAuditRouter 是薄循环外壳** — Pipeline 当前不支持 fan-out 原语（见 D6）。外壳设计保证所有 LLM 事件走 AgentNodeLoop 落 bus，但 Pipeline 级只看到 1 个节点的 verdict。未来 Pipeline 支持 fan-out 后，外壳应退化为透传。

2. **kind_inference 首条命中即定型** — 一个 .py 文件既含 Router 又含 LLM 调用时，当前只打一个 kind。升级路径：改多标签 `kinds: list[str]`，但初版保持单 kind 简化匹配。

3. **path_match 是 glob 不是 AST** — `routers/**/*.py` 会把非 Router 类的 .py 也标成 router kind。初版可接受（标错最坏就是 LLM 多读几条标准），升级路径：AST 检测 `class X(Router)` 才标 router kind。

4. **key_sections 精确匹配 ## 标题文本** — standards 改名 `## X` → `## X（新）` 时 key_sections 命中失败。运行时 warn + fallback 到 full 模式；未来 section 级 ID 稳定化需改标准文件本身加锚。

5. **excerpt 无缓存** — 每次全读 YAML + 全切 section。规模扩大后需接 `data/semantic_auditor/excerpts/` 缓存。

6. **confidence 阈值（0.7）待首批 Finding 实跑校准** — 初版按经验设；首批 10-20 条真实 Finding 人工复核后再调。

7. **AuditAgent 的 NODE_PROMPT 是静态常量** — 不同标准家族（Router/Format/DESIGN.md）实际上需要不同的审视角。升级路径：按 `standard_id` 分类路由到不同 AuditAgent 子类，当前统一一个 prompt。

## 参考资料

- 关联标准索引：`docs/standards/standards-index.yaml`
- 关联规范：`docs/standards/llm_first.md` / `docs/standards/worker.md` / `docs/standards/material.md`
- 关联 Guardian：`../guardian/DESIGN.md`（两者互补，出口共用 REGISTRY）
- 关联 Agent 新包：`../agent/DESIGN.md`（Phase B2 LLMAuditRouter 继承 AgentNodeLoop）
- 关联计划：[`docs/plans/[2026-04-18]TECH-DEBT-AND-SEMANTIC-AUDIT/plan.md`](../../../../../docs/plans/[2026-04-18]TECH-DEBT-AND-SEMANTIC-AUDIT/plan.md)
- 关联 REGISTRY：`docs/tech_debt/REGISTRY.md`（出口表）
