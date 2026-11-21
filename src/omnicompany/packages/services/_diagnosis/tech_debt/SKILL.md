---
name: tech_debt
description: omnicompany 技术债登记处管理层 - 用 omni debt 列债务/统计/标解决/跑漂移检查/主动登记. 配套 docs/tech_debt/REGISTRY.md 跟 ARCH-CHANGES.jsonl 事件流.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/tech_debt ts=2026-05-04T12:20:00Z type=doc status=active agent=ai-ide belongs_to_service=tech_debt -->
<!-- [OMNI] summary="tech_debt 操作手册 — omni debt 5 子命令 (list/stats/resolve/scan/add) 操作步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §六 模板严格写. DESIGN 偏架构, 缺'怎么用'段, 抽出独立 SKILL 让操作可定位" -->
<!-- [OMNI] tags=skill,tech_debt,how-to,diagnosis -->
<!-- [OMNI] material_id="material:services._diagnosis.tech_debt.skill.operations_manual.md"-->

# tech_debt · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- 想列当前所有未处理债务 (`omni debt list`)
- 想看债务全局统计 (按 section / rule_id / status / severity)
- 想标某条债务为已解决 (`omni debt resolve`)
- 想跑漂移检查 (DESIGN.md 跟代码 mtime / plan.md 老化)
- 想主动登记新条目 (外部 agent / 人工)

**不用我**:
- 扫违规 (Guardian 自己写 §活跃违规) → [omni guardian patrol](../../_core/guardian/SKILL.md)
- 跑语义审计 (SemanticAuditor 自己写 §语义合规待审) → [semantic_auditor](../semantic_auditor/)
- 修代码 → 找 [services/repair/](../../) 或人工

## 前置条件

- omnicompany 已装 (`omni --help` 确认)
- docs/tech_debt/REGISTRY.md 文件存在 (项目根有, 不需要自建)
- 在 git repo 内 (DriftChecker 部分检查依赖 git)

## 操作步骤

### 场景 A · 列债务 (基本查询)

```bash
omni debt list                           # 列所有 status=open 的债务
omni debt list --section=活跃违规         # 按 section 过滤
omni debt list --status=needs_human_review
omni debt list --json --limit=20         # JSON 输出 + 限量
```

**验证**: 输出含每条债务的 ID / type / severity / target / detected_at / status. 没条目 = 干净.

### 场景 B · 看统计

```bash
omni debt stats                          # 按 section / rule_id / status / severity 全维统计
omni debt stats --json                   # JSON 给外部 agent 消费
```

**用途**: 看债务密度跟分布, 决定优先级.

### 场景 C · 标某条债务为已解决

```bash
omni debt resolve D-042 --reason="重构后 OMNI-014 不再触发" --by="ai-ide-2026-05-04"
```

**注意**:
- ID 前缀按 producer: D-NNN (Guardian) / SA-NNN (SemanticAuditor) / P-NNN (DriftChecker plan) / G-NNN (DriftChecker DESIGN)
- resolve 是**软移动**: 从原 section 删 + 在 §已解决 末尾加一行 + 同步写 ARCH-CHANGES `event_type=violation-resolved`
- 不可 undo (当前局限, Phase C2+ 才加 reopen 命令)

### 场景 D · 跑漂移检查 (DriftChecker)

```bash
omni debt scan --drift-only             # 只跑 DriftChecker (不调 Guardian / SemanticAuditor)
omni debt scan --fast                   # Guardian patrol + DriftChecker (默认)
omni debt scan --full                   # fast + SemanticAuditor (含 LLM 调用)
omni debt scan --fast --dry-run         # 不写 REGISTRY 看会出啥
```

**漂移检测两种**:
- `design_md_drift`: 代码 mtime 比 DESIGN.md mtime 新 14 天以上 (默认阈值)
- `plan_stale` / `plan_old`: status=active 但 plan.md mtime 距今 ≥ 14 天 → stale; 其他 non-archived 距今 ≥ 30 天 → old

**避重铁律 (D6)**: 同 (kind, target) status=open 则跳过, 不累计 scan_count. 想再次入库要先 resolve.

### 场景 E · 主动登记新条目

```bash
omni debt add 文档漂移 --fields '{"kind": "design_md_drift", "target": "src/omnicompany/packages/services/_authoring/docauthor", "drift_days": 21, "severity": "warn"}' --by="ai-ide" --dedup-on=kind,target
```

**两条入口** (都合法):
- CLI (机器友好): `omni debt add <section> --fields '<JSON>' [--dedup-on KEY,...]` — 同步写 ARCH-CHANGES `violation-found`
- Markdown (人友好): 直接编辑 docs/tech_debt/REGISTRY.md — 不写事件但下次 list 仍能读出

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `omni debt list` | 列债务 | `--section` `--status` `--json` `--limit` |
| `omni debt stats` | 统计 | `--json` |
| `omni debt resolve <ID>` | 标解决 | `--reason` `--by` |
| `omni debt scan` | 跑漂移 + 协调 producer | `--fast` `--full` `--drift-only` `--limit` `--dry-run` |
| `omni debt add <section>` | 主动登记 | `--fields '<JSON>'` `--by` `--dedup-on` |
| `load_registry` / `list_rows` / `resolve_row` / `append_row` (Python) | 库调用 | 见 [registry_io.py](registry_io.py) |
| `run_drift_audit` (Python) | 跑漂移检查 | 见 [drift_checker.py](drift_checker.py) |
| `append_event` / `read_events` (Python) | ARCH-CHANGES 读写 | 见 [events.py](events.py) |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| `list` / `stats` 输出 0 条 | REGISTRY.md 没条目, 或 producer 还没跑过 | 跑 `omni guardian patrol` 让 Guardian 写 §活跃违规 |
| `resolve <ID>` 报 ID not found | ID 拼错或前缀不对 (D / SA / P / G) | `omni debt list` 找正确 ID, 注意前缀 |
| `resolve` 完跑 `list` 还看到该条 | 软移动失败 (例 同时被外部 edit 撞) | 看 ARCH-CHANGES.jsonl 是否有 violation-resolved 事件; 没有就再 resolve 一次 |
| `scan --drift-only` 没产新条目 | 同 (kind, target) status=open 已存在被 dedup | 这是 D6 设计 (避重), 想再次入库先 resolve 老条目 |
| `scan --full` 跑得慢 | SemanticAuditor 调 LLM, 大仓库慢 | 用 `--fast` 跳过 LLM 部分, 或加 `--limit` |
| `add` 报 dedup_keys 字段不存在 | --fields JSON 没含 --dedup-on 指定的字段 | 检查 JSON 完整性 |
| ARCH-CHANGES.jsonl 字段不一致 | producer (Guardian/SA/tech_debt) 历史写入 schema 略有差 | 当前局限 (局限 3), Phase C2+ 统一 |
| 漂移检测想撤但没 reopen 命令 | 当前不支持 undo | 手工编辑 REGISTRY.md 撤回, 或 Phase C2+ 加 reopen |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 (D1-D8 决策 / 共享数据源拓扑) → [DESIGN.md](DESIGN.md)
- 数据源 REGISTRY.md → docs/tech_debt/REGISTRY.md
- 事件流 → docs/ARCH-CHANGES.jsonl
- producer guardian → [../../_core/guardian/SKILL.md](../../_core/guardian/SKILL.md)
- producer semantic_auditor → [../semantic_auditor/](../semantic_auditor/)
