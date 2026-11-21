<!-- [OMNI] origin=claude-code domain=services/tech_debt ts=2026-05-04T12:15:00Z type=doc status=active belongs_to_service=tech_debt -->
<!-- [OMNI] material_id="material:diagnosis.tech_debt.service_design_documentation.md" -->

# tech_debt · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限).
>
> 形态: REGISTRY.md 共享数据源的 I/O + 管理层 (不是 Team, 是数据层 owner 服务).

## 状态
- **版本**：V1 · Phase C4（REGISTRY + DriftChecker + 主动登记 + scan 协调 + **统一 ARCH-CHANGES writer**）
- **成熟度**：active（2026-04-18）
- **下一步**：Phase C5 — Sentinel 后台集成（git hook / 定时器）；按需把阈值做成可配置（现 14/14/30 天硬编码）

## 核心接口

### registry_io（见 [registry_io.py](registry_io.py)）
- **`RegistrySnapshot`** — 整份 REGISTRY.md 的内存视图
- **`load_registry(root) -> RegistrySnapshot`** — 读 `docs/tech_debt/REGISTRY.md` 解析所有 section
- **`list_rows(snapshot, section=, status=) -> list[RegistryRow]`** — 按条件过滤
- **`compute_stats(snapshot) -> dict`** — 按 section / rule_id / status / severity 统计
- **`resolve_row(root, row_id, reason, resolved_by) -> ResolveResult`** — 移到 §已解决 + 写 ARCH-CHANGES
- **`append_row(root, section_name, fields, dedup_keys=) -> AppendResult`** — 通用写入（供 DriftChecker / omni debt add / 外部 agent 使用）

### drift_checker（见 [drift_checker.py](drift_checker.py)）
- **`check_design_md_drift(root, days_threshold=14)`** — `src/omnicompany/**/DESIGN.md` 代码 mtime 比 DESIGN.md 新 N 天 → `design_md_drift`
- **`check_plan_drift(root, stale_threshold_days=14, old_threshold_days=30)`** — `docs/plans/[date]TOPIC/plan.md`：
  - `status=active` 但 plan.md mtime 距今 ≥ stale → `plan_stale`
  - 其他 non-archived 且目录日期距今 ≥ old → `plan_old`
- **`run_drift_audit(root, dry_run=)`** — 统一入口，写 §文档漂移 section（dedup_keys=(kind,target)）

### events（见 [events.py](events.py)）
- **`append_event(root, event_type, initiator, drawer, change, payload=) -> ARCHEvent`** — 统一 ARCH-CHANGES.jsonl 写入
- **`read_events(root, event_type=, since_date=)`** — 读取事件流

### CLI（见 [`cli/commands/debt.py`](../../../../cli/commands/debt.py)）
- **`omni debt list [--section X] [--status X] [--json] [--limit N]`** — 列出债务条目
- **`omni debt stats [--json]`** — 全局统计
- **`omni debt resolve <ID> --reason TEXT [--by NAME]`** — 标记解决
- **`omni debt scan [--fast | --full | --drift-only] [--limit N] [--dry-run]`** — 协调调度：
  - `--fast`（默认）Guardian patrol + DriftChecker
  - `--drift-only` 只跑 DriftChecker
  - `--full` fast + SemanticAuditor（含 LLM）
- **`omni debt add <section> --fields '<JSON>' [--by AGENT] [--dedup-on KEY,...]`** — 主动登记条目

## 架构决策

### D1 — 为什么独立 tech_debt 包（不塞 guardian 或 semantic_auditor）

`REGISTRY.md` 的读/写已经出现在三处：`guardian/registry_updater.py`（写 §活跃违规）+ `semantic_auditor/routers.FindingWriterRouter`（写 §语义合规待审）+ `omni debt` CLI（读全文 + 写 §已解决）。

原则：**共享数据源需要独立 owner 包**，否则三处各自维护 I/O 模板→格式漂移。

### D2 — Phase C1 只加读 + resolve，不碰 producer 写路径

Guardian `registry_updater.py` 和 `FindingWriterRouter` 已经 ship 且稳定跑。C1 只新增"consumer 路径"（读 + resolve），不重构 producer 路径。未来如果 producer 出现格式不一致，再把 `_parse_row` / `_find_section` 等合并到本包。

### D3 — `omni debt scan` 推迟到 C2

Phase C 原计划 `omni debt scan --fast|--full` 协调 Guardian + SemanticAuditor。但该命令涉及 Sentinel / job runner 集成 + `--full` 的 LLM 预算控制，独立工作量。C1 先上 list/stats/resolve（纯本地读+改），用户已可用；scan 在 C2 上。

### D4 — resolve 是软移动，不硬删

`omni debt resolve D-001` 的语义：
- 从原 section（§活跃违规）删该行
- 在 §已解决 表末追加一行含 `<原 ID> | 类型 | 解决日期 | 解决方式`
- 同步写 `docs/ARCH-CHANGES.jsonl` 一条 `event_type=violation-resolved` 事件

好处：可追溯；不会丢失历史；stats 可区分 "历史总量" vs "当前活跃"。

### D6 — DriftChecker 强避重：同 (kind, target) open 则跳过（2026-04-18 用户明示）

用户铁律：*"不要重复在一个 design 上反反复复说这个没完成持续膨胀"*。

实现：`append_row(dedup_keys=("kind", "target"))` — 已存在 `status ∈ {open, needs_human_review}` 的条目则返回 `action="deduped"`，**不累计** scan_count / drift_days。条目只有在 resolve 后重新漂移才会再次入库。

代价：丢失"重复漂移频率"信号。未来若要"这个 DESIGN.md 第 3 次漂移了"这类信号，可加一个独立 `drift_resolved_count` 计数器，不改 open 条目语义。

### D8 — tech_debt.events 是 ARCH-CHANGES schema 的唯一 owner（2026-04-18 Phase C4）

C3 完成后，发现 ARCH-CHANGES.jsonl 写入代码在三处重复：
- `guardian/registry_updater.append_violation_found_events`（violation-found）
- `semantic_auditor/routers.FindingWriterRouter._append_arch_events`（finding-generated）
- `tech_debt/registry_io._append_resolved_event`（violation-resolved） + `tech_debt/events.append_event`（scan-*）

三份代码各自实现 `change_id` 自增、读取现有 jsonl 找 max_n、原子 append。schema 漂移风险高。

**C4 统一**：全部改用 `tech_debt.events.append_event(root, event_type, initiator, drawer, ..., change, payload)`。
guardian + semantic_auditor 保留原外部函数签名不变，内部循环调 `append_event`。这样：
- schema 所有者只有 `events.py` 一处
- 新增 event_type 只需改 `KNOWN_EVENT_TYPES`
- change_id 自增逻辑一致
- producer 不再关心 jsonl 解析

依赖方向 `guardian → tech_debt` 和 `semantic_auditor → tech_debt` 是合理的 —— `tech_debt` 是 REGISTRY + ARCH-CHANGES 的数据层 owner，作为公共库被消费。

### D7 — 主动登记走 `omni debt add` 或 Markdown 直接编辑

外部 agent / 人工发现问题，两条入口：

- **CLI**：`omni debt add <section> --fields '<JSON>' [--dedup-on ...]` — 字段按 section 定义，JSON 结构给 agent 用（机器友好）
- **Markdown**：直接编辑 `docs/tech_debt/REGISTRY.md` 对应表格（人友好）

两条都是合法的。CLI 会同步写 ARCH-CHANGES `event_type=violation-found`；直接编辑不写事件但下次 `omni debt list` 仍能读出。

### D5 — CLI 输出双模式：彩色终端默认 + `--json` 切换

对齐现有 CLI 约定（guardian/unified 都支持 `--json-out`）。默认人类可读（按 section 分组、严重度着色）；`--json` 供外部 agent / CI 消费。

## 数据流 / 拓扑

```
┌─ Guardian patrol ───→ registry_updater  ──┐
│                                           │
├─ SemanticAuditor   ─→ FindingWriterRouter ┼──→ docs/tech_debt/REGISTRY.md
│                                           │
└─ 人工手工 append (rare) ──────────────────┘
                                            │
                                            ▼
                              ┌──── tech_debt/registry_io ────┐
                              │                                │
                              │  load_registry                 │
                              │  list_rows / compute_stats     │
                              │  resolve_row                   │
                              │                                │
                              └──────┬───────────────────┬─────┘
                                     │                   │
                                     ▼                   ▼
                               omni debt list/    外部 agent
                                stats/resolve     Python API 调用
```

### 事件流
- resolve 动作 → 写 `docs/ARCH-CHANGES.jsonl` `event_type=violation-resolved`（补齐 C1 事件流）
- scan 动作（C2）→ 写 `event_type=scan-started/completed`

## 已知局限

1. **Phase C1 不含 scan 协调** — 用户手动跑 `omni guardian patrol` / semantic auditor 来更新 REGISTRY；C2 会加 `omni debt scan` 一键协调。

2. **ID 唯一性依赖 prefix 约定** — D-NNN / SA-NNN / P-NNN / G-NNN 分段，跨 section `resolve <ID>` 靠 prefix 判断来源 section。新增 section 需要更新 ID 前缀表。

3. **ARCH-CHANGES.jsonl 未统一格式** — 当前 Guardian `violation-found` / SemanticAuditor `finding-generated` / tech_debt `violation-resolved` 字段略有不同。统一 schema 待 C2 把 event types 整合时定稿。

4. **无并发写保护** — 两个 producer 同时写 REGISTRY.md 理论可能撞车。当前 Guardian patrol 是单进程、SemanticAuditor 按需，撞车概率低，C2 加文件锁。

5. **resolve 无 undo** — 一旦 resolve，只能手工改 REGISTRY.md 撤销。升级路径：加 `omni debt reopen <ID>` 命令（C2+）。

## 参考资料

- 关联数据源：`docs/tech_debt/REGISTRY.md`
- 关联 producer（guardian）：[`../guardian/registry_updater.py`](../../_core/guardian/registry_updater.py)
- 关联 producer（semantic_auditor）：`../semantic_auditor/routers.py`（`FindingWriterRouter`）
- 关联事件流：`docs/ARCH-CHANGES.jsonl`
- 关联 CLI：[`../../../cli/commands/debt.py`](../../../../cli/commands/debt.py)
- 关联计划：[`docs/plans/[2026-04-18]TECH-DEBT-AND-SEMANTIC-AUDIT/plan.md`](../../../../../docs/plans/[2026-04-18]TECH-DEBT-AND-SEMANTIC-AUDIT/plan.md)
