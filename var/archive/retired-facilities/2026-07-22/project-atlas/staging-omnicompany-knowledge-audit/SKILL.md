---
name: knowledge-audit
description: >-
  OmniKB / 文档赤字一致性审计的统一入口。Use when 用户要查知识库漂移、扫孤儿 Router、看
  Format 覆盖、对 code_anchor 体检、找缺 DESIGN/manifest 的包、跑 KB 全审、补 skeleton 文档
  ——一律走现成的 omni run omnikb-audit + omni docauthor，禁另搭审计脚本。
---
> ⚠ 存疑(用户):与 material-doctor 似有重叠,谁新谁旧/是否合并待厘清。

# 知识审计（操作对象：OmniKB 与文档赤字）统一入口

任何"扫 KB 引用完整性 / 看 code_anchor 漂移 / 找孤儿 Router/Format / 查 skeleton DESIGN 或缺
manifest 的包 / 补这些赤字"的需求，一律走下面两个现成设施，**不要新写审计脚本**。

## 何时用 / 不在范围

- ✅ 本 SKILL：
  - OmniKB 一致性 / drift 审计（KFormat 覆盖、KArch code_anchor、孤儿 Router、KRouter
    staleness、hypothesis 文档校验）。
  - 文档赤字扫描与补全（services/domains 包缺 `.omni/manifest.yaml`、DESIGN.md
    status=skeleton 需要 reviewer 闭环重写）。
  - 观察某 target 历史 docauthor 事件链 / 查最近 reviewer issues。
- ❌ 非赤字、单纯写新文档（plan、report、决策记录）→ 走对应业务 SKILL，不走 docauthor。
- ❌ guardian/lint/类型/单测体检 → 走 `omni guardian` / `omni debt` 等，不在此。
- ❌ 项目速览名录维护 → 走 `omni project`，不写进 object-SKILL。

## 用哪个现成设施

### 1. OmniKB 全审 = `omni run omnikb-audit`

- **CLI 入口**：`omni run omnikb-audit`（`venv/Scripts/omni.exe run omnikb-audit`）。
- **注册真源**：`src/omnicompany/core/pipelines.py:347-364`
  （PipelineEntry name="omnikb-audit"，domain="knowledge"，default_db_dir=
  `data/services/knowledge`，default_max_steps=5）。
- **Team 构造**：`omnicompany.packages.services._learning.knowledge.run.build_audit_pipeline`
  （`src/omnicompany/packages/services/_learning/knowledge/run.py:15`，转发到同包
  `pipeline.build_audit_pipeline`）。
- **底层引擎**：`src/omnicompany/packages/services/_learning/knowledge/audit.py`
  - `run_full_audit(project_root)`（`audit.py:331`）= 一把跑 6 类审计：
    KB 自身 validation、`check_code_anchors`（OMNI-017 drift）、
    `find_orphan_routers`（OMNI-018 孤儿 Router/LLMRouter/AgentNodeLoop）、
    `staleness_report`（KRouter.relates_to_routers 引用不存在的类 + 长期 draft）、
    `format_coverage_report`（KFormat ↔ 可执行 FormatRegistry 对照）、
    `audit_hypothesis_docs`（`data/knowledge/hypotheses/*.md` 走 hypothesis.validator）。
- **设计文档**：
  `docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/`。

### 2. 文档赤字扫描 + 补全 = `omni docauthor`

- **CLI 真源**：`src/omnicompany/cli/commands/docauthor.py`
  （L2 工作流；bus 驱动，SQLiteBus 落 `data/events.db` 全留档）。
- 子命令（实测调用入口）：
  - `omni docauthor scan [--kind manifest|design|readme|skill|all] [--json-output]`
    （`docauthor.py:107`）— 扫全仓赤字：缺 `.omni/manifest.yaml` 的 service/domain 包 + 
    DESIGN.md `status=skeleton` 的位置。
  - `omni docauthor run <kind> <target> [--max-refine N] [--dry-run]`
    （`docauthor.py:140`）— 跑单目标，落 `src/`；终局打 issue 全量含 evidence。
  - `omni docauthor run-all [--kind] [--max-refine] [--dry-run] [--limit N]
    [--continue-on-fail]`（`docauthor.py:200`）— 扫赤字 + 逐个跑 + 批汇总报告，
    报告落 `data/services/docauthor/batch_reports/run_all_<ts>.json`。
  - `omni docauthor observe <target> [--n N] [--json-output]`
    （`docauthor.py:290`）— 从 `data/events.db` 查该 target 最近 N 个 docauthor 事件链。
  - `omni docauthor issues <target>`（`docauthor.py:366`）— 查最近
    `docauthor.review-verdict` 的 issue 全量。
- **Team 构造**：
  `omnicompany.packages.services._authoring.docauthor.team.run_job / summarize_events`
  （`docauthor.py:154` 处 import）。

> 不确定时先 `venv/Scripts/omni.exe run omnikb-audit --help` 或
> `venv/Scripts/omni.exe docauthor <sub> --help` 看当时的真实选项；上述真源比 --help 更权威，
> 但 CLI flags 偶有迭代。

## 铁律

- **禁另搭**：不要再写 ad-hoc Python 去扫 KB / 扫 code_anchor / grep 孤儿 Router / 扫
  skeleton DESIGN。所有这些视图都在 `audit.py` 的六类 + `docauthor scan` 里，
  缺什么去那两处加，**不要在调用方各起一套**。
- **指路不复制**：本 SKILL 不抄 audit 规则与 docauthor 流程，所有事实以
  `src/omnicompany/core/pipelines.py:347-364`、
  `src/omnicompany/packages/services/_learning/knowledge/audit.py`、
  `src/omnicompany/cli/commands/docauthor.py` 为准。
- **bus 留档不可绕**：`omni docauthor run/run-all` 默认落 `data/events.db`；想看历史用
  `observe` / `issues`，别去直接 grep 日志文件。
- **dry-run 验证再写盘**：批量 `run-all` 前先 `--dry-run --limit 3` 试一把，确认 reviewer
  与 refine 行为符合预期再去掉 `--dry-run` 写 `src/`。
