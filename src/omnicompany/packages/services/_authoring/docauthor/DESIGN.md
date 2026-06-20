<!-- [OMNI] origin=claude-code domain=services/docauthor ts=2026-05-05T18:00:00Z type=doc status=active belongs_to_service=docauthor -->
<!-- [OMNI] material_id="material:authoring.docauthor.service_design.document.md" -->
<!-- [OMNI] summary="docauthor 设计文档 V2 — Phase C 全总线驱动 4 kind (manifest/design/readme/skill) 闭环 author/reviewer/relauncher/lander 落地" -->
<!-- [OMNI] why="Phase A 漂移版只讲 manifest 单 worker, 当前真实状态已 Phase C+ 4 kind. 用 docauthor dogfood 自身重产并手补 LLM 编路径死链" -->
<!-- [OMNI] tags=design,docauthor,bus-driven,phase-c,self-narrative -->

# docauthor · 设计文档

> 设计目的请看 [README.md](README.md) (语境 / 解决什么 / 最终目标 / 规划). 怎么用请看 [SKILL.md](SKILL.md) (CLI 命令 / 操作步骤). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限).

## 状态
- **版本**: V2 (Phase C 全总线化落地, 2026-04-25 结构定型)
- **成熟度**: active
- **下一步**: 接入 sentinel 周期扫赤字目标自动投递 request; 优化超大仓库上下文摘要策略

## 核心目的
本服务是 Omnicompany 的**自动化文档作者**，负责为仓库内的 service / domain 子包 / 核心基础设施模块批量生成与维护 `.omni/manifest.yaml`、`DESIGN.md`、`README.md` 及 `SKILL.md`。它通过 MaterialDispatcher + SQLiteBus 驱动 Worker 团队协同，支持单次生成、Reviewer 审核、Refine 循环重试与终态落盘。
**不解决**: 不替代人类 L2 对核心语义的最终审批；不处理业务代码逻辑或运行时行为诊断；不绕过 Guardian 静态合规扫描。

## 核心接口
### Workers (bus 驱动节点)
- [`workers/manifest_author.py`](workers/manifest_author.py) — `ManifestAuthorWorker`
  - `FORMAT_IN = docauthor.manifest-request` · `FORMAT_OUT = docauthor.manifest-draft`
- [`workers/design_author.py`](workers/design_author.py) — `DesignDocAuthorWorker`
  - `FORMAT_IN = docauthor.design-request` · `FORMAT_OUT = docauthor.design-draft`
- [`workers/readme_author.py`](workers/readme_author.py) — `ReadmeAuthorWorker`
  - `FORMAT_IN = docauthor.readme-request` · `FORMAT_OUT = docauthor.readme-draft`
- [`workers/skill_author.py`](workers/skill_author.py) — `SkillAuthorWorker`
  - `FORMAT_IN = docauthor.skill-request` · `FORMAT_OUT = docauthor.skill-draft`
- [`workers/reviewer.py`](workers/reviewer.py) — `DocReviewerWorker` (OR 订阅多 draft)
  - `FORMAT_IN = [docauthor.*-draft]` · `FORMAT_OUT = docauthor.review-verdict`
- [`workers/relauncher.py`](workers/relauncher.py) — `ManifestRefineRelauncher` / `DesignRefineRelauncher` / `ReadmeRefineRelauncher` / `SkillRefineRelauncher`
  - `FORMAT_IN = docauthor.review-verdict` · `FORMAT_OUT = docauthor.<kind>-request` (带 `_emit_as_new_job`)
- [`workers/final_lander.py`](workers/final_lander.py) — `FinalLanderWorker`
  - `FORMAT_IN = docauthor.review-verdict` · `FORMAT_OUT = docauthor.job-final` (终局信号)

### Materials (schema 定义)
- [`formats.py`](formats.py) — 定义 11 个 Material (`manifest-request` / `manifest-draft` / `design-request` / `design-draft` / `readme-request` / `readme-draft` / `skill-request` / `skill-draft` / `review-request` / `review-verdict` / `job-final`), 均含 `kind.source` / `kind.internal` / `kind.sink` 标签与 JSON Schema

### Entry & Routing
- [`run.py`](run.py) — `build_bindings()` 适配 `omni run docauthor` CLI 入口, lazy import 避免启动加载 LLMClient
- [`team.py`](team.py) — `build_team_workers()` / `run_job()` 真·总线驱动入口, 组装 MaterialDispatcher + SQLiteBus
- [`__init__.py`](__init__.py) — 快捷导出 `run_job` / `build_team` 及 8 个 Worker 类

## 架构决策
### D1 · 全总线驱动替代同步 Harness (Phase C)
**决策**: 废弃 Phase A/B 的同步 `run_phase_a.py` harness, 所有 Worker 激活、Material 流转、状态留档完全走 `SQLiteBus` + `MaterialDispatcher`.
**理由**: 解耦 Worker 执行顺序, 天然支持 refine 循环重试与完整事件审计; 严格对齐 "所有内容都要走事件总线进行存储和调度" 架构硬指示.

### D2 · 单次 LLM 调用 vs 内部 Agent Loop 分工
**决策**: Manifest/Design/Readme/Skill Author 均使用 `call_llm_json` 单次调用, 不启用 `AgentNodeLoop` 多轮 tool 交互.
**理由**: 文档生成是结构化产出任务, 单次调用配合预置扫描上下文已覆盖核心语义需求; 若质量不足, 走外部 `DocReviewerWorker` + `Relauncher` 循环, 避免内部 tool-loop 状态机复杂度与 token 预算爆炸.

### D3 · 终局落盘二分策略 (src/ vs quarantine)
**决策**: `FinalLanderWorker` 按 `review-verdict` 分支落盘: `passed=True` 直写 `src/<target>/`; `passed=False` 且 `iter >= max_refine_iters` 写入 `data/services/docauthor/drafts/_quarantine/<slug>/`.
**理由**: 保护 `src/` 免受低质或带 critical issue 草稿污染; 隔离区提供安全缓冲, 供 L2 后续人工干预、调整 prompt 或追加 context 后重跑.

### D4 · 反泄漏白名单机制 (Prompt 硬屏障)
**决策**: Author Worker 的 `_SPEC_SOURCES` / `_GOLDEN_EXAMPLES` 为 hardcoded 白名单, 仅允许注入 `distributed-docs.md` 与 guardian 公开范例. `gold_samples/` 目录绝对不进扫描路径与 prompt.
**理由**: 保证盲审质量评估的公允性; 白名单扩写必须走代码变更与 Review, 杜绝动态路径拼接导致的越权读取.

### D5 · `_emit_as_new_job` 绕过 Q1 单次激活铁律
**决策**: Relauncher 将未通过的 verdict 翻译回新 author request 时, 强制标记 `_emit_as_new_job=True` 生成独立 `trace_id` 子 job.
**理由**: 框架 Q1 规定同一 `(trace_id, worker_id)` 仅激活一次. 子 job 机制使同一 Worker 在 refine 多轮中可合法重复响应, 实现无状态重试闭环.

## 数据流 / 拓扑
```
外部触发 (CLI / 计划脚本 / 未来 sentinel)
       │
       ▼ (投递 source material)
docauthor.<kind>-request (kind.source)
       │
       ▼
┌─Author Worker (Manifest/Design/Readme/Skill)────────────┐
│ 1. scan_service_data → ls 目录结构                      │
│ 2. read_design_md / read_existing → 读已有草稿保留人工  │
│ 3. grep_plan_history → 扫 docs/plans/ 关联上下文        │
│ 4. LLM call_llm_json → 注入规范+范例+扫描证据产 draft    │
└─────────────────────────────────────────────────────────┘
       │
       ▼ (产出 draft)
docauthor.<kind>-draft (kind.internal)
       │
       ▼ (OR 订阅)
┌─DocReviewerWorker───────────────────────────────────────┐
│ 对照规范七节/四字段逐项检查 → 产出 Verdict(passed/issues)│
└─────────────────────────────────────────────────────────┘
       │
       ▼ (产 verdict)
docauthor.review-verdict (kind.internal)
       │
       ├─ passed=True ──► FinalLanderWorker ──► 写 src/ ──► docauthor.job-final
       │
       ├─ passed=False & iter < max ──► RefineRelauncher ──► 新 request (子 job trace)
       │                                    (循环回 Author)
       └─ passed=False & iter == max ──► FinalLanderWorker ──► 写 quarantine ──► job-final
```

## 已知局限
- **扫描上下文全量拼接易超 LLM 窗口**: Author Worker 将目录树、现有 DESIGN.md、plan history 直接拼入 prompt, 超大服务目录可能触发上下文截断或稀释关键信息. 升级路径: 引入动态摘要层 (summary agent) 或接入 OmniKB 语义检索, 按文件权重/近期修改时间动态截取关键片段, 替代全量 dump.
- **grep_plan_history 仅字面匹配**: 当前实现用 service/package 名做纯字符串 grep, 可能漏掉"隐喻引用"或误带无关历史 plan. 升级路径: 替换为轻量 LLM 过滤层 (`call_llm_json` 判相关性) 或向量化检索匹配, 提升计划上下文召回精度.
- **Domain 顶层聚合目录处理未自适应**: 若 target 指向 `domains/<dom>/` 顶层且含多个子包, Worker 当前按单包逻辑线性扫描, 可能遗漏子包 data 路径或产出过大 manifest. 升级路径: 增加前置 `is_domain_aggregate` 路由判断, 识别后自动拆分为多个子 author job 并行处理.
- **Sentinel 自动触发链路未闭合**: 当前 `run_job` 与 CLI 入口已就绪, 但缺乏常驻哨兵定时扫描仓库赤字目标并自动投递 request. 升级路径: 实现独立 `DocSentinelWorker`, 接入系统 crontab 或 inotify 监听 `src/` 变更事件, 定时产出 `<kind>-request` 投递 SQLiteBus 实现零人工干预.

## 参考资料
- 上游 plan: [`docs/plans/diagnosis/[2026-04-25]AUTO-DOCAUTHOR-WORKER/plan.md`](../../../../../../docs/plans/diagnosis/%5B2026-04-25%5DAUTO-DOCAUTHOR-WORKER/plan.md)
- 规范权威 (Author 可见): `docs/standards/_global/distributed-docs.md`
- 合法公开范例 (Author 可见): `_core/guardian/.omni/manifest.yaml` · [`_core/guardian/DESIGN.md`](../../_core/guardian/DESIGN.md)
- 运行时框架: [`_core/omnicompany/worker.py`](../../_core/omnicompany/worker.py) · [`_core/omnicompany/material_dispatcher.py`](../../_core/omnicompany/material_dispatcher.py)
- 总线实现: [`bus/sqlite.py`](../../../../bus/sqlite.py)
- 金标隔离 (Worker 不可见): `docs/plans/diagnosis/[2026-04-25]AUTO-DOCAUTHOR-WORKER/gold_samples/` — D4 反泄漏硬屏障