<!-- [OMNI] origin=ai-ide domain=services/docauthor ts=2026-05-04T10:30:00Z type=doc status=active agent=ai-ide belongs_to_service=docauthor -->
<!-- [OMNI] summary="docauthor service 自我叙事 README — 把 L2 手工写 manifest/DESIGN 的劳动转 Worker 管线. 让新建 service 或填 skeleton 时自动产合规文档 draft" -->
<!-- [OMNI] why="docauthor 三件套样例 — 自动写文档的 service 反向给自己用三件套规范写, dogfooding 自我叙事是否真好用" -->
<!-- [OMNI] tags=readme,docauthor,authoring,self-narrative -->
<!-- [OMNI] material_id="material:services._authoring.docauthor.readme.self_narrative.md"-->

# docauthor · 自动文档作者

> 让 Worker 管线自动产合规的 `.omni/manifest.yaml` + `DESIGN.md` draft. 替代"L2 手工写 N 份相似文档"的重复劳动, 给文档生成留痕 / 可回放 / 可盲审.

---

## 这是什么

docauthor 是 omnicompany 的**文档作者 service**. 当用户/AI IDE 新建一个 service 或要填补 skeleton DESIGN 时, docauthor 调用 LLM 扫源码 + 读现有规范 + 引用合法范例, 产一份合规 draft.

形态: Worker 管线. Phase A (当前) 单 Worker (`ManifestAuthorWorker`); Phase B 加 `DesignDocAuthorWorker` + `DocReviewerWorker` 串 Team; Phase C 加 CLI 自动接入 + sentinel 周期扫描触发.

跟其他文档管理服务的边界:
- **它产 draft**, 不直接落盘 — 落盘归调用方决定 (Phase A 走盲审 → Phase B 走 Reviewer 闭环 → Phase C 走人工 confirm)
- **它写规范文档**, 不写业务代码 (那是 [workflow_factory](../../_diagnosis/) / team_builder)
- **它写 service 级文档**, 不升级项目级 PROGRESS.md (那是另开 plan 的责任)
- **它产文档基于规范**, 不**手写金标** (金标人类出品, 用作事后对比, 不进 Worker prompt — D3 反泄漏)

## 解决什么 / 不解决什么

**解决**:
- 31 份 skeleton DESIGN + 新 service 命名补文档的重复 L2 劳动 → 自动化产 draft
- 文档生成可留痕 / 可回放 / 可盲审 (走事件总线 + GuardianAuditStore 5 元组防重跑)
- 让"L2 没空手填" 不再是文档失修的借口 — 跑一下 omni docauthor 就有 draft

**不解决**:
- 自动产业务代码 (归 [workflow_factory](../../_diagnosis/) / team_builder)
- 自动升级 docs/PROGRESS.md (那是另开 plan)
- 手写金标样本 (人类出品, 不由 Worker 产, 见 D3 反泄漏)
- 直接落盘 draft (Phase A 不落, Phase B 走 Reviewer 闭环再落)

## 设计目的与最终目标

**设计目的**: 让"L2 手工写 manifest/DESIGN" 这件**重复机械工作** 自动化, 同时保证产出合规 + 留痕 + 可审. L2 退出日常文档撰写, 只留最终审阅角色 (跟 omnicompany 整体"L2 退出日常, 留兜底" 长期目标对齐).

**最终目标** (当下能认知的):
- Phase A → Phase B → Phase C 三阶段递进, 最终 omnicompany 任何新建 service 自动有合规 manifest + DESIGN draft, 人审通过即落盘
- 进一步: 接入 [自我叙事三件套](../../../../../../docs/standards/protocol/self_narrative_three_files.md), 让 docauthor 不止产 manifest/DESIGN, 也产 README + SKILL — 形成完整三件套自动化产线
- 远景: 跟 自我认知 service 协作, docauthor 的产物用作自我画像的实例补全

## 规划

- **当前 Phase A** (V1 active, 2026-04-25 落地): `ManifestAuthorWorker` 单 Worker, draft 不落盘, 走盲审退出
- **Phase B**: 加 `DesignDocAuthorWorker` (产 DESIGN 七节) + `DocReviewerWorker` (闭环审核) → Team 形态
- **Phase C**: CLI `omni docauthor manifest <svc>` 自动入口 + sentinel 周期扫 skeleton 触发
- **后续**: 扩到三件套全套 (README + DESIGN + SKILL 全自动产)

## 构成

- 入口与 Team → [run.py](run.py) (`build_bindings()`) + [team.py](team.py)
- Materials (Phase A 两个) → [formats.py](formats.py)
  - `MANIFEST_REQUEST` (kind.source) — `{target_service_path, notes_hint?}`
  - `MANIFEST_DRAFT` (kind.sink) — `{manifest_path, manifest_content, scan_evidence, notes}`
- Workers → [workers/](workers/)
  - `ManifestAuthorWorker` ([workers/manifest_author.py](workers/manifest_author.py)) — 当前唯一 Worker, 单次 LLM 调用产 draft
- 数据归宿 → data/services/docauthor/ (drafts / audit / batch_reports)

技术架构详述见 [DESIGN.md](DESIGN.md), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- 上游 plan → [docs/plans/[2026-04-25]AUTO-DOCAUTHOR-WORKER/plan.md](../../../../../docs/plans/%5B2026-04-25%5DAUTO-DOCAUTHOR-WORKER/plan.md)
- 文档规范权威 → docs/standards/distributed-docs.md
- 自我叙事三件套规范 → [docs/standards/protocol/self_narrative_three_files.md](../../../../../../docs/standards/protocol/self_narrative_three_files.md)
- 项目根叙事 → [../../../../../README.md](../../../../../../README.md)
