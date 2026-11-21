<!-- [OMNI] origin=ai-ide domain=services/semantic_auditor ts=2026-05-04T12:45:00Z type=doc status=active agent=ai-ide belongs_to_service=semantic_auditor -->
<!-- [OMNI] summary="semantic_auditor service 自我叙事 README — LLM 驱动的语义合规检查, 跟 Guardian 互补 (静态规则 vs 语义判定). 5 Worker 管线产 Finding 写 REGISTRY.md §语义合规待审" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写. semantic_auditor 是 tech_debt 的 producer 之一, 写它三件套让 cross-reference 真实有效" -->
<!-- [OMNI] tags=readme,semantic_auditor,diagnosis,self-narrative -->
<!-- [OMNI] material_id="material:services._diagnosis.semantic_auditor.readme.self_narrative.md"-->

# semantic_auditor · LLM 语义合规检查

> 跟 Guardian 互补 — Guardian 看路径 / 命名 / 结构 (确定性规则), semantic_auditor 看语义 / 内容 / 意图 (LLM 驱动). 5 Worker 管线产 Finding 写 REGISTRY.md §语义合规待审, 是 tech_debt 的 producer 之一.

---

## 这是什么

semantic_auditor 是 omnicompany 的 **LLM 驱动语义合规检查 service**. 它读 `docs/standards/standards-index.yaml` (审计路由表), 给每个待审 artifact 匹配适用 standards, 调 LLM (走 AgentNodeLoop + 工具) 跑深度审计, 产 `Finding` (含 `standard_id` + `confidence` + `line_hint` + `recommended_action`) 写到 `docs/tech_debt/REGISTRY.md` §语义合规待审.

形态: **5 节点串行 Team** (Phase B1 三节点 HARD + Phase B2 追加两节点 LLMAudit + FindingWriter).

跟其他诊断 service 的边界:

| | Guardian | SemanticAuditor |
|---|---|---|
| 检查层次 | 路径 / 命名 / 结构 / 存在性 | 语义 / 内容 / 意图一致性 |
| 执行方式 | 确定性规则, 每次 commit 快速触发 (<30s) | LLM 驱动, 按需 / 周期触发 (2-5 min) |
| 规则表达 | 42 条预定义规则 (OMNI-001~035) | `standards-index.yaml` 驱动 |
| 例 | "DESIGN.md 是否有 OmniMark 头" | "这个 Worker 真的只做单一职责吗" |
| 输出 | Violation (HIGH/MEDIUM/LOW) | Finding (含 standard_id + confidence) |

跟 tech_debt 关系: semantic_auditor 写 §语义合规待审 (producer), tech_debt 读全 section + 管 list/stats/resolve (consumer + owner).

## 解决什么 / 不解决什么

**解决**:
- 本仓的每个 artifact 是否仍符合它该遵循的语义规范 (不是 Guardian 能正则判定的)
- 给 Finding 含明确 standard_id + line_hint, 让人 / agent 能回查
- 跟 Guardian 互补共用 REGISTRY 出口

**不解决**:
- Guardian 已覆盖的结构性检查 (路径 / 命名 / 存在性)
- 自动修复 (Finding 登记到 REGISTRY 后, 修复走 [services/repair/](../../) 或人工)
- 业务正确性 (semantic_auditor 看的是协议 / 规范合规, 不是业务对错)

## 设计目的与最终目标

**设计目的**: Guardian 能查的东西有限 (路径 / 命名 / 头存在性等可正则判定), 但项目里大量合规问题是**语义级** (例 "Worker 是否真单一职责"). 这些不能用规则判, 必须 LLM 看. semantic_auditor 是这层 LLM 审计的承载.

**理论锚点**: 体现 llm_first.md §3 信息充分性 — `standards-index.yaml` 让 LLM 每次只看 "一个 artifact + 适用 standards 的摘录", 不全量喂标准防 context 溢出.

**最终目标** (当下能认知的):
- Phase C: Sentinel / CLI 触发入口 (周期或 git hook 触发)
- 首批 Finding 实跑 + 人工复核校准 confidence 阈值 (当前 0.7)
- 按 standard 家族分类路由到不同 AuditAgent 子类 (当前统一一个 prompt)
- 跟 CORE-SELF-STABILITY 第二阶段 自我画像漂移检测协作

## 规划

- **当前 V2** (active, 2026-04-20 Clean Migration): 5 Worker + AuditAgent + 标准索引加载器 + Finding 写 REGISTRY 全链通
- **下一步**: 首批 Finding 实跑 + 人工复核校准 confidence 阈值; 接 Sentinel / CLI 触发入口 (Phase C)
- **远景**: 按 standard 家族细分 AuditAgent + 跟自我画像漂移检测协作

进度细节: docs/PROGRESS.md (项目级) + [DESIGN.md `## 状态`](DESIGN.md) (本 service 状态).

## 构成

- 入口与 Team → [team.py](team.py) + [pipeline.py](pipeline.py) (`build_pipeline()`)
- Materials → [formats.py](formats.py)
- Workers (5 个 Phase B1+B2) → [workers/](workers/)
  - `ArtifactSelectorWorker` (HARD) — path / git-diff / 全扫 → list[Artifact]
  - `StandardMatcherWorker` (HARD) — 按 kind + path 匹配 standard_id[]
  - `ExcerptRetrieverWorker` (HARD) — 按 excerpt_strategy 取标准摘录
  - `LLMAuditWorker` (async HARD 外壳) — 循环调度 AuditAgent 单审
  - `FindingWriterWorker` (HARD) — 验证 Finding + append REGISTRY + ARCH-CHANGES
- Agent → [audit_agent.py](audit_agent.py) — `AuditAgent` (继承 `packages/services/agent/AgentNodeLoop`)
- 标准索引加载器 → [standards_loader.py](standards_loader.py)
- 旧名 compat shim → [routers.py](routers.py)

技术架构详述见 [DESIGN.md](DESIGN.md), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- 标准索引 → docs/standards/standards-index.yaml
- 信息充分性原则 → docs/standards/llm_first.md
- 跟 Guardian 互补 → ../../_core/guardian/README.md
- consumer / owner tech_debt → ../tech_debt/README.md
- 项目根叙事 → ../../../../../README.md
