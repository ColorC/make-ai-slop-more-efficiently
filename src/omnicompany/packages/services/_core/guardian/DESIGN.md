<!-- [OMNI] origin=claude-code domain=services/guardian ts=2026-05-04T11:30:00Z type=doc status=active belongs_to_service=guardian -->
<!-- [OMNI] material_id="material:core.guardian.service_design.specification.md" -->

# guardian · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (规则家族 / 接口 / 决策 / 数据流 / 局限).
>
> **命名兼容注**（2026-04-20）：本文中 `Router` / `Format` / `PipelineSpec` 按 [`terminology.md`](../../../../../../docs/standards/_global/terminology.md) 对照读作 `Worker` / `Material` / `Team`. protocol 层类名保留原名 (契约不变), 新代码 import 用 `from omnicompany.packages.services.omnicompany import Worker, Material, Team`.

## 规则家族

每条规则对应一个 `OMNI-NNN` 编号. 当前规则家族:

| 家族 | 覆盖 | 文件 |
|---|---|---|
| OMNI-001 | OmniMark 头存在性 | `omnimark.py` |
| OMNI-002/003/004/006/013 | 包边界 / SDK 集成边界 | `boundaries.py` |
| OMNI-005/011 | 数据落盘规范 (data/ 结构 / 就近 .omni/) | `data_storage.py` |
| OMNI-009/010/012 | 迁移与归档 (graveyard 转移) | `migration.py` |
| OMNI-007/008/014/015/016/021 | 架构地图 / src 不放文档 / pipeline 注册 | `archmap.py` |
| OMNI-017/018/019/020 | 可观测性 / 日志 / audit 落盘 | `observability.py` |
| OMNI-023/024 | 文件位置规范 | `location.py` |
| OMNI-030/031/032/033 | 命名规范 (Router/Format/class) | `naming.py` |
| OMNI-034a-g | DESIGN.md 结构合规 | `design_md.py` |
| OMNI-035a-e | 分布式文档 v2 合规 (docs/ 白名单 / plans 命名 / reports 日期 / PROGRESS 唯一) | `distributed_docs.py` |
| **OMNI-035f-j** (2026-04-28 立) | 计划目录子项闭集 / docs 内 .py / 数据产物 / 运行时残留 / 大文件 LLM 复核 | `distributed_docs.py` |
| **OMNI-093a-d** (2026-06-13 立) | 核心设施统一的唯一权威收束防漂移: 确认表 active / 执行规范绑定 / 分散入口锚点 / 禁第二套权威 | `authority_convergence.py` |

## 状态
- **版本**: V2（规则拆分为家族模块 + certainty 双轨 + Guardian Agent 复核雏形 + OMNI-034/035 已加入）
- **成熟度**: active
- **唯一权威收束**: 方向权威见 authority-confirmation.md, 执行门禁见 autonomous-execution-rules.md。本服务只通过 OMNI-093 守护它们, 不另立第二套设施统一决断。
- **下一步**: OMNI-034 TBD/decisions 语义检测从固定正则迁移到 Guardian Agent LLM 巡逻（见 §已知局限 1 + M1 计划）；auto_check 时间/变更阈值完全接通；统一技术债登记处（OMNI-035/093 等规则产出统一索引）

## 核心接口

### 管线入口（见 [pipeline.py](pipeline.py)）
- **`build_pipeline() -> PipelineSpec`** — Guardian 巡逻管线

### Health-check 管线 Workers（见 [workers/](workers/)）
- **`FsScannerWorker`** — 文件系统扫描 → 污染清单 — [workers/fs_scanner_worker.py](workers/fs_scanner_worker.py) (Clean Migration 2026-04-20 从 `FsScannerRouter` 迁出)
- **`ArchAuditorWorker`** — 扫描 src/ 下的架构规范（DEPRECATED / Router 元数据 / 空 init）— [workers/arch_auditor_worker.py](workers/arch_auditor_worker.py) (Clean Migration 2026-04-20 从 `ArchAuditorRouter` 迁出)
- **`HealthReporterRouter`** (AgentNodeLoop) — LLM 把扫描事实汇总为人类可读健康报告 — [routers.py](routers.py) (**不迁**, AgentNodeLoop 子类 Phase 1 runtime 统一后处理)

兼容 shim: [routers.py](routers.py) 中 `FsScannerRouter = FsScannerWorker` / `ArchAuditorRouter = ArchAuditorWorker` 旧名别名保留, `HealthReporterRouter` 原样。
归档: [_archive/routers_legacy.py](_archive/routers_legacy.py) 原单文件 3-class 实现 · 见 [_archive/README.md](_archive/README.md)

### 规则引擎（见 [rules/_base.py](rules/_base.py)）
- **`FileContext`** — path / abs_path / change_type / content / omnimark — [rules/_base.py:22](rules/_base.py#L22)
- **`GuardianRule`** — id / name / severity / description / check / disposition / certainty — [rules/_base.py:32](rules/_base.py#L32)
- **`Violation`** — ticket_id / rule_id / severity / path / message / disposition / confidence — [rules/_base.py:48](rules/_base.py#L48)
- **`parse_omnimark(content) -> dict | None`** — OmniMark 头解析（委托给 core.omnimark）

### 辅助模块（见 [rules/](rules/)）
- **`rules/__init__.py`** — 规则聚合器（显式 import 所有家族 + 按 OMNI 编号组装 RULES 列表）
- **`patrol.py` + `patrol_runner.py` + `patrol_hook.py`** — patrol 执行入口（git hook / CLI / CI）
- **`auto_check.py` + `auto_comment.py`** — 时间/变更阈值自动触发 + 自动评论
- **`judge_agent.py` + `llm_judge_agent.py`** — Guardian Agent LLM 复核机制
- **`sentinel.py`** — 哨兵守护（常驻巡逻）
- **`tow_truck.py`** — 违规后的默认修复动作（轻量改名/挪位）
- **`guardian_hook.py` + `evolve_signal.py`** — 融合 `crystallize` 的演化信号入口
- **`registry_updater.py`** — patrol 结果同步到 `docs/tech_debt/REGISTRY.md` §活跃违规 + `docs/ARCH-CHANGES.jsonl` 事件（Phase A2，2026-04-18 接入）
- **`audit_store.py`** — `GuardianAuditStore` 审计留档 (2026-04-24 新增): append-only JSONL, 每次 GuardianAgent 判定 (confirmed/dismissed/uncertain) 写 record, 支持五元组 (path + rule_id + rule_version + prompt_sha8 + file_sha16) 缓存查询防重跑 · 落盘 `data/services/guardian/audit/records.jsonl` + sidecar
- **`hygiene_whitelist.py`** — hygiene scan 白名单 (2026-04-23 I-10 新增 · 2026-04-24 加 `expires`/`on_expire` 支持到期失效): 每条 entry 必带 reason + 强烈建议 expires · 过期自动不豁免, 强制重审
- **`hook_installer.py`** — git hook 幂等安装 (2026-04-23 I-19): 5 态管理 (absent/managed-current/managed-stale/foreign/replaced-foreign) + `# OMNI-GUARDIAN-MANAGED` marker 保护用户自定义

## 架构决策

### D1 — 规则按编号分家族而非单文件

Guardian 有 30+ 条规则，初版都在一个 `rules.py` 里。问题：
- 单文件膨胀到 2000+ 行
- 添规则要通读所有规则才知道是否冲突
- 规则按**语义家族**而非编号自然聚类

V2 重构：每个家族（`omnimark.py` / `boundaries.py` / `data_storage.py` / ...）独立文件，各自 export `RULES = [...]`。`rules/__init__.py` 显式 import 并按 OMNI 编号拼接。

好处：
- 新增规则只改一个家族文件 + 一行 `__init__.py` import
- 对比同家族规则方便（冲突自检）
- `patrol.py` 不用改

### D2 — certainty 双轨：`absolute` 规则命中即违规 / `needs_judgment` 命中只是疑似

问题：某些规则是字符串硬判（路径结尾 `/.omni/` — 100% 违规），某些是语义猜测（某字段是否"描述不清晰" — 容易误报）。

解法：`GuardianRule.certainty` 字段：
- `absolute` — 命中 = 违规，confidence=1.0，直接出 Violation
- `needs_judgment` — 命中 = 疑似，送 Guardian Agent（LLM）复核，复核后才出 Violation（confidence < 1.0）

当前 OMNI-001 ~ OMNI-033 多数 absolute，Guardian Agent 复核路径刚打通（`judge_agent.py` / `llm_judge_agent.py`），规模化应用待铺开。

### D3 — Guardian Agent 对 needs_judgment 的 LLM 复核是"二次裁定"，不是规则替代

设计原则：rule 是第一层筛（便宜、快），Agent 是第二层审（贵、准）。

好处：
- 避免每条规则都调 LLM（成本爆炸）
- 避免每条规则都是 AST 硬规则（语义判断做不到）
- 用户 2026-04-17 原话："Guardian 里加固定文本检测反而违反原则"—— 核心意思是**让 LLM 做语义判断**，不是"所有规则都要 LLM"

所以 certainty 双轨的分工：结构/位置类 → 规则；语义/意图类 → Agent。

### D4 — disposition 可配置：warn / fix / block

每条 `GuardianRule.disposition: list[str]` 指定命中后的动作：
- `warn` — 只告警（默认）
- `fix` — 自动修复（tow_truck.py 内置安全改动：重命名 / 移位）
- `block` — 阻止 commit（git hook 拒绝）

Phase 1 全部 `warn`（避免误杀）。Phase 2 逐条升级到 fix/block。

### D5 — OMNI-007 硬规则：src 下不放文档 / 配置

违反次数最多的一条规则。背景：`DESIGN.md` 等文档曾经放在 `src/omnicompany/packages/xxx/` 下。用户明确指定改放 `docs/plans/` 或 `docs/standards/`。

Guardian 扫 `src/` 下任何 `.md` / `.yaml` / `.toml` → 违规。豁免列表：`.omni/manifest.yaml`（分布式文档规范自己定义的就近配置）。

**本 DESIGN.md 本身也应该遵守** — 实际上 `services/*/DESIGN.md` 走 OMNI-034 豁免（DESIGN.md 就近是规范），不算 OMNI-007 违规。

### D6 — OMNI-034 当前实现 vs 目标实现（本 session 新建，部分违反 llm_first）

2026-04-17 新加 OMNI-034 检查 DESIGN.md 结构合规，六条子规则：
- `034a` 缺 OmniMark 头（absolute / 结构）
- `034b` status 字段不在 skeleton/design/active/deprecated 之一（absolute / 结构）
- `034c` 缺 7 个必需二级标题中任一个（absolute / 结构）
- `034d` status=active 但含 `<!-- TBD:` / `_待补充` / `TODO:`（**当前 absolute 字符串规则，违反 llm_first**）
- `034e` status=design/active 但 `## 架构决策` 无任何 `### D1` 条目（**当前 absolute 正则**）
- `034f` INFO 级，统计 skeleton 文档（非违规）

`034a/b/c/f` 是纯结构检查，保留 absolute 合理。`034d/e` 语义判断部分应迁移到 Guardian Agent LLM 巡逻（见 §已知局限 1）。

### D9 — 拖车 relocate 动作 + 存量豁免铁律（GUARDIAN-DOCS-CONFISCATION，2026-04-28）

**背景**：docs/ 累积 578 个非法 .py / .json / 数据产物 / 缓存。直接强制隔离会破坏 figma-to-prefab 等活跃 agent 的工作流。

**决策**：
1. 新增 `relocate` 处置动作（`tow_truck.py::_do_relocate`）—— 调 LLM 单次复核（`relocate_judge.py`，qwen-3.6-plus）判定目标位置，信心 ≥ 0.8 自动 mv，否则降级 quarantine。
2. **存量豁免铁律**（D1 D3）：`_do_relocate` 第一步 hygiene_whitelist 命中即跳过，仅 warn。578 个存量违规一次性登记 60 天豁免（2026-06-27 到期重审），由 figma-to-prefab agent 自清。
3. **OMNI-035 全系列升级**：035a~e disposition=["warn", "stamp"]；035f~j disposition=["warn", "stamp", "relocate"]。035j 是 Guardian 内部首条 `needs_judgment` 规则，触发 LLM 语义判断（图示合理 / 数据违规）。
4. 干跑模式 `OMNI_GUARDIAN_DRY_RUN=1`：不调 LLM 也不真 mv，便于 CI 离线测试。

**为什么不复用 GuardianAgent**：relocate 是单次判定无中间状态，不需要 multi-turn agent loop。直接走 LLMClient.call() 模式（同 evolve_signal 先例）。

### D10 — pre-commit 拦截集合扩展 + 罚单逾期升级（GUARDIAN-DOCS-CONFISCATION，2026-04-28）

**第四A**：pre-commit 模板（`hook_installer.py::PRE_COMMIT_TEMPLATE`）BLOCK_RULES 集合扩展加入 OMNI-035f / 035g / 035h / 035i（HIGH 严重度）。035j 是 MEDIUM + needs_judgment，不进硬阻断（避免 LLM 复核拖慢 commit）。受 hygiene_whitelist 豁免的违规自动跳过，figma agent 提交不被存量阻断。

**第四C**：`tow_truck.py::escalate_overdue_tickets(threshold_days=7)` 扫 `index.json` 找 status=open + detected_at > 7 天的罚单，升级到 evolve-signal（写 `.omni/evolution/overdue_signals.jsonl`），罚单 status 改 "overdue-escalated"。sentinel daemon_loop 唤醒时调用一次（`sentinel.py:run_one_pass()`），0 LLM 消耗纯文件操作。

### D8 — patrol → REGISTRY 自动同步（Phase A2，2026-04-18）

OMNI-NNN 违规除了落 `logs/patrol/patrol-*.json`，还同步到 `docs/tech_debt/REGISTRY.md §活跃违规` 表，供人/agent 统一阅读与处置。

关键机制（见 `registry_updater.py`）：
- **去重键**：`(rule_id, path)` 已存在 status=open → 持续扫描数 +1，不新增 ID
- **ID 体系**：`D-NNN` 自增；只处理 `rule_id` 以 `OMNI-` 开头的行（`OVERSEER` 等手工条目不碰）
- **事件流**：每条新违规在 `docs/ARCH-CHANGES.jsonl` 记一条 `event_type=violation-found`，含 `change_id` / `ts` / `drawer=services/guardian`
- **容错**：写入失败仅 `logger.debug`，不阻塞 patrol 主流程

这条与 D4 的 `disposition` 正交：REGISTRY 同步是**可见性基础设施**（所有 warn/fix/block 都会登记），不做任何修复。

### D7 — auto_check 时间/变更阈值触发（未完全接通）

`auto_check.py` 设计：
- 每 N 次 commit 触发一次（默认 10 次）
- 每 M 小时触发一次（默认 24 小时）
- 文件变更行数 ≥ K 触发（默认 100 行）

当前状态：入口在代码里，但 git hook 注册不全自动，需要手工 `omni guardian --install`。

## 数据流 / 拓扑

### 巡逻管线（3 节点串行）

```
[输入] (触发源: git hook / CLI / 定时 / auto_check 阈值)
   ↓
FsScannerRouter (RULE)
   ├─ 读 git status + 列 src/ + docs/ 文件变更
   └─ 产出 list[FileContext]（含 path / content / omnimark）
   ↓
ArchAuditorRouter (RULE)
   ├─ 对每个 FileContext 跑 guardian.rules.RULES 里所有规则的 check()
   ├─ certainty=absolute 命中 → 直接 Violation（confidence=1.0）
   ├─ certainty=needs_judgment 命中 → 交 Guardian Agent 复核 → 可能 Violation
   └─ 产出 list[Violation]
   ↓
HealthReporterRouter (AgentNodeLoop LLM)
   ├─ Violation 按 OMNI 编号分组 + 优先级排序
   ├─ LLM 写人类可读报告（为什么违规 / 怎么修）
   └─ 产出 health_report.md + 通知（slack / GitHub comment 等）
```

### 规则加载顺序（见 rules/__init__.py）

按 OMNI 编号组装：
```
_R001 (omnimark)
_R002_013 (boundaries)
_R005_011 (data_storage)
_R009_012 (migration)
_R007_021 (archmap, 含 OMNI-007 src 不放文档)
_R017_020 (observability)
_R023_024 (location)
_R030_033 (naming)
_R034 (design_md)  ← 本 session 新加
```

数据落盘：
- `data/guardian/violations.jsonl` — append-only 违规记录
- `data/guardian/reports/<date>.md` — HealthReporter 产出的巡逻报告
- `.omni/guardian/_summary.json` — dashboard 消费摘要

## 已知局限

1. **OMNI-034d/e 当前是字符串硬检测，违反 llm_first 原则** — 规则 034d 用 `<!-- TBD:` / `TODO:` 正则扫，034e 用 `### D\d+` 正则扫，这是"固定文本检测"。用户 2026-04-17 明确指出："guardian 里加固定文本检测反而违反原则"。**升级路径**（M1）：结构检查（034a/b/c/f）保留 regex；034d/e 的 TBD / "文档看起来未完成" / "决策是否有实质内容" 判断移到 Guardian Agent 的 LLM 巡逻 backlog。当前先标 TODO，后续独立实施。

2. **Guardian Agent 复核路径规模化应用不足** — `judge_agent.py` / `llm_judge_agent.py` 已接通，但 30+ 条规则中 `certainty=needs_judgment` 的规则很少（大多是 absolute）。真实的语义判断规则（如"Router description 是否足够具体"）大量在 Doctor 的 LLM 检查器里，Guardian 的 Agent 复核层还没接过来。**升级路径**：把 Doctor 的部分语义检查迁移到 Guardian（两者分工：Doctor 看单对象语义，Guardian 看跨文件合规）。

3. **tow_truck.py 自动修复能力保守** — 目前只做文件重命名 / 轻量挪位，不动代码内容。大量"缺 OmniMark 头"的文件应该可以自动补头（从 git log 推 origin + domain），但 Phase 1 选择 warn-only。**升级路径**：Phase 2 逐条规则升级到 `disposition=[fix]`。

4. **auto_check 未完全接通 git hook** — 需手动安装 hook。CI 接入靠 `omni guardian` CLI 显式调用，不会自动在 PR 时跑。

5. **Violation 的 `cross_refs`（关联其他违规）未用** — 想法是"A 文件少 OmniMark 头 + B 文件引用 A 错版本"这类级联违规标记关联，方便批量修。当前每条 Violation 独立，无关联。

## 新哲学对齐（Phase D · 2026-04-20）

> 对照 13 条新世界条款逐项评估（完整权威见 docs/standards/material.md + worker.md + team.md）。

### Material 层（F-16/17/18/19）

| 条款 | 状态 | 说明 |
|---|---|---|
| F-16 kind 三分 | ✅ | formats.py 5 条 Material: check-request=source / fs-report+arch-report+node-report=internal / health-report=sink；materials.py 当前只保留 4 条真实 patrol material，不再声明未落盘的 audit sink |
| F-17 Workspace 大明文 | N/A | guardian 无大 payload（违规条目为结构化小 dict，不需要 workspace 文件） |
| F-18 Job × Material 绑定 | N/A | guardian 当前走传统 pipeline 模式，MaterialDispatcher job_id 链路待 Phase 1 runtime 接通 |
| F-19 kind.* tag 必填 | ✅ | Phase D 修正：formats.py 5 条全部补 kind.*（本次 commit） |

### Worker 层（R-18~R-25）

| 条款 | 状态 | 说明 |
|---|---|---|
| R-18 粒度 | ✅ | 当前导出的 Worker 均有真实职责 + FORMAT 边界（FsScanner/ArchAuditor/HygieneScan/ReportWriter/GitDiffScan/RuleEngine）；LLMJudge 与 AuditTow 均已退出 active chain |
| R-19 Agent Worker 升级 | N/A | 无动态 Material 需求，静态规则管线不适用 |
| R-20 Agent Worker 三件套 | N/A | 同上 |
| R-21 Diagnosis Agent Worker | N/A | guardian 本身即诊断系统，不需要对自身上游质疑 |
| R-22 WorkspaceWriterWorker | N/A | 无 workspace 文件写入 |
| R-23 Verdict.output 平铺 | ✅ | 所有 Worker.run() 产出 Verdict(output={flat_fields}) 无嵌套 format_id 包裹 |
| R-24 FORMAT_IN_MODE | N/A | 所有 Worker FORMAT_IN 为单 str，无 list 多入 |
| R-25 子 job | N/A | 无 _emit_as_new_job 使用 |

### Team 层（P-13~P-17）

| 条款 | 状态 | 说明 |
|---|---|---|
| P-13 声明即消费 | ✅ | Workers 只消费 FORMAT_IN 声明的 Material，无搭便车 |
| P-14~17 Workspace 目录 | N/A | guardian 无 workspace 目录约定 |

**结论**: formats.py 的 health-check material 与 materials.py 的 patrol material 均只描述真实边界；materials.py 不再保留未由真实落盘链路支撑的 audit sink。

---

## 参考资料

- 关联管线：[pipeline.py](pipeline.py) + [routers.py](routers.py)
- 关联规则家族：[rules/](rules/) 全部 10 个家族文件
- 关联规则注册：[rules/__init__.py](rules/__init__.py)（RULES 聚合）
- 关联 Agent 复核：judge_agent.py / llm_judge_agent.py
- 关联 auto_check：[auto_check.py](auto_check.py) / [auto_comment.py](auto_comment.py) / [sentinel.py](sentinel.py)
- 关联 tow_truck：[tow_truck.py](tow_truck.py)（Phase 1 只重命名）
- 关联演化接入：[guardian_hook.py](guardian_hook.py) / [evolve_signal.py](evolve_signal.py)
- 关联 doctor：[../doctor/DESIGN.md](../../_diagnosis/doctor/DESIGN.md)（Guardian 静态 vs Doctor 运行时）
- 关联 standards：`docs/standards/distributed-docs.md`（§八 OMNI-034 引用）
- 关联 standards：`docs/standards/design_md_template.md`（OMNI-034 的标准来源）
- 关联 plan：`docs/plans/[2026-04-17]OMNICOMPANY-SELF-KNOWLEDGE/HANDOFF.md`（M1 修改计划）

## D11 - Audit/tow placeholder retirement (2026-06-13)

`AuditTowWorker` was removed from the active Guardian patrol chain. The worker
claimed to persist audit records and delegate to OmniTow, but its implementation
only returned a synthetic summary with a hard-coded persistence path.

Current patrol flow:

`guardian.scan_request -> GitDiffScanWorker -> guardian.file_context_set -> RuleEngineWorker -> guardian.violation_set -> patrol result`

Optional GuardianAgent review still writes judgment cache records through
`GuardianAuditStore`. Tow actions remain explicit `tow_truck.py` operations.
There is no active worker that presents audit persistence or tow handling unless
that real contract is implemented and tested in the same block.
