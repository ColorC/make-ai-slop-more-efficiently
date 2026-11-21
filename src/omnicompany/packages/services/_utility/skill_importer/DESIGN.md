<!-- [OMNI] origin=claude-code domain=services/skill_importer ts=2026-04-20T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:utility.skill_importer.design_specification.md" -->

# skill_importer · 设计文档

## 状态
- **版本**: V1 (Phase D Diamond shortcut 2026-04-20)
- **成熟度**: active
- **下一步**: 修复铁律 A 截断违规（StructureAnalysisRouter sections[:30] / body[:1200]）；接入 F-17 Workspace 写入

## 核心目的
将 Claude Code Skill 目录（SKILL.md + references/ + scripts/）转化为 workflow-factory 可消费的 markdown 需求稿，以及对生成的 OmniCompany package 做忠实度验证。

两条独立管线：
- **主管线**：raw → SkillParser → StructureAnalysis (LLM) → FormatInference → RequirementDraft (LLM) → requirement_draft (落盘到 data/absorption/skill_digest/)
- **验证管线**：compliance_check_request → VerifyAgainstSkill (LLM) → compliance_report

## 核心接口

- [workers/__init__.py](workers/__init__.py) — `ALL_WORKERS` (5 Worker, Diamond shortcut)
- [formats.py](formats.py) — 7 个 Material 定义（主管线 5 + 验证管线 2）
- [pipeline.py](pipeline.py) — `build_pipeline()`
- [_archive/routers_legacy.py](_archive/routers_legacy.py) — 原 Router 实现

## 架构决策

### D1 — Diamond Shortcut 迁移

5 个 Router 中含 LLM 调用（StructureAnalysisRouter / RequirementDraftRouter / VerifyAgainstSkillRouter），采用 Diamond shortcut: `class XxxWorker(Worker, _LegacyRouter)`. 业务逻辑保留在 `_archive/routers_legacy.py`。

### D2 — 两条独立管线

主管线（parse → analyze → infer → draft）和验证管线（verify）是独立触发的。验证管线需要主管线产物（skill_structure）和 workflow-factory 产物（package_path）一起作为输入，不能内联到主管线。

### D3 — 需求稿落盘 + guarded_write

RequirementDraftRouter 产出后直接落盘 `data/absorption/skill_digest/<skill>.md`，使用 `guarded_write.write_file()`。FORMAT_OUT（requirement_draft）只包含路径指针，不含明文。这符合 F-17 Workspace 大明文精神。

### D4 — workflow-factory 是生成权威

skill_importer 只做 "解析 + 结构化 + 产需求稿 + 事后验证"，不直接生成 Python 代码。代码生成交给 workflow-factory，这是 2026-04-09 重构的核心决策（废弃了原 CodeGeneratorRouter）。

### D5 — 已知铁律 A 截断违规（grandfathered）

StructureAnalysisRouter 中有预防性截断：`sections[:30]` + `body[:1200]`。RequirementDraftRouter 中有 `sections[:20]` + `body[:600]`。SkillParserRouter 的 scripts 读取有 `[:2000]`。这些违反铁律 A，但在 _archive/ 中作为 grandfathered 保留，**升级路径**：Stage 3 真迁时改为 agent loop + 主动分片。

## 数据流 / 拓扑

```
【主管线】
skill_importer.raw (source)
  → SkillParserWorker (确定性, 读 SKILL.md + references/ + scripts/)
  → skill_importer.parsed_sections (internal)
  → StructureAnalysisWorker (LLM 归纳)
  → skill_importer.skill_structure (internal)
  → FormatInferenceWorker (确定性, format_in/out 命名)
  → skill_importer.format_chain (internal)
  → RequirementDraftWorker (LLM 产出, 落盘)
  → skill_importer.requirement_draft (sink, 指针)

【验证管线】
skill_importer.compliance_check_request (source)
  → VerifyAgainstSkillWorker (LLM 忠实度检验)
  → skill_importer.compliance_report (sink)
```

## 已知局限

1. **铁律 A 截断违规** — StructureAnalysisRouter/RequirementDraftRouter/SkillParserRouter 含多处预防性截断，LLM 看不到完整 skill 内容。**升级路径**：Stage 3 真迁改为 agent loop + 全量读取。

2. **Diamond 体未真迁移** — 业务逻辑仍在 _archive/，三个 LLM Router 在 Stage 3 低优先级真迁。

3. **需求稿质量依赖 LLM** — RequirementDraftRouter 的 markdown 质量无确定性 validator，workflow-factory 消费质量参差不齐。**升级路径**：加后置结构验证 Worker（检查必需段落存在）。

## 新哲学对齐（Phase D · 2026-04-20）

### Material 层（F-16/17/18/19）

| 条款 | 状态 | 说明 |
|---|---|---|
| F-16 kind 三分 | ✅ | raw=source; compliance_check_request=source; parsed_sections/skill_structure/format_chain=internal; requirement_draft=sink; compliance_report=sink |
| F-17 Workspace 大明文 | ⚠️ 部分 | RequirementDraftRouter 落盘到 data/absorption/skill_digest/，但 FORMAT_OUT 只含路径指针，符合精神；SkillParser 脚本截断违反铁律 A |
| F-18 Job × Material 绑定 | N/A | 传统 pipeline，待新 Runtime |
| F-19 kind.* tag 必填 | ✅ | Phase D 修正：7 条 Material 全部补 kind.* |

### Worker 层（R-18~R-25）

| 条款 | 状态 | 说明 |
|---|---|---|
| R-18 粒度 | ✅ | 5 Worker 各有完整职责 + FORMAT 边界 |
| R-19 Agent Worker 升级 | ⚠️ 待评估 | 3 LLM 节点串行可升级 Agent Worker；当前 grandfathered |
| R-20 Agent Worker 三件套 | ⚠️ 待评估 | 同上 |
| R-21 Diagnosis Agent Worker | N/A | |
| R-22 WorkspaceWriterWorker | ⚠️ 待评估 | RequirementDraftRouter 用 guarded_write 落盘，未走 WorkspaceWriterWorker；升级路径 Stage 3 |
| R-23 Verdict.output 平铺 | ✅ | 所有 Worker 输出无嵌套 format_id |
| R-24 FORMAT_IN_MODE | N/A | 所有 Worker FORMAT_IN 为单 str |
| R-25 子 job | N/A | 无 _emit_as_new_job |

### Team 层（P-13~P-17）

| 条款 | 状态 | 说明 |
|---|---|---|
| P-13 声明即消费 | ✅ | 各 Worker 只消费 FORMAT_IN 声明的 Material |
| P-14~17 Workspace 目录 | N/A | |

**结论**: F-19 缺口已修正。Diamond shortcut 完成。铁律 A 截断违规在 _archive/ 中 grandfathered，记录于 D5。

## 参考资料

- [workers/](workers/) — 5 个 Worker (Diamond shortcut)
- [formats.py](formats.py) — 7 个 Material
- [_archive/routers_legacy.py](_archive/routers_legacy.py) — 原 Router 实现
- ../workflow_factory/ — RequirementDraft 的下游消费者
