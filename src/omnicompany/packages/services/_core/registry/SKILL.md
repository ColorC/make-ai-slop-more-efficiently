---
name: registry
description: omnicompany 实体注册中心操作手册. 用它查 Material/Worker/Team/Agent/Tool/Hook 实例, 查健康档案, 跑增量扫描, 反查 G2 索引 (material_id ↔ 路径).
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/registry ts=2026-05-04T10:15:00Z type=doc status=active agent=ai-ide belongs_to_service=registry -->
<!-- [OMNI] summary="registry 操作手册 — 适用范围 + 9 条 CLI 子命令操作步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] why="DESIGN.md 七节没'怎么用'段, 用户/agent 想用 registry 得 grep 代码或 omni --help 拼. 抽出独立 SKILL 让操作可定位" -->
<!-- [OMNI] tags=skill,registry,how-to,cli -->
<!-- [OMNI] material_id="material:services._core.registry.skill.operations_manual.md"-->

# registry · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- 想知道系统里有什么 Material / Worker / Team / Agent / Tool / Hook
- 按 type / package / tag 查找实体
- 看实体健康档案 / 跨 commit 跟踪回归
- 给文件路径反查它的 `material_id` (G2 索引正查)
- 给 `material_id` 反查实现文件路径 (G2 索引反查)
- CI 跑增量扫描 (git diff 驱动)

**不用我**:
- 跑 Team / 调度 Worker → 找 [runtime/exec/PipelineRunner](../../../../runtime/exec/)
- 看 Worker 输出对不对 → 找 [doctor service](../../doctor/)
- 注册新实体 (写) → 用 [omni register](../../../../../../docs/standards/cli/registration.md), 不是 omni registry
- 查 Format/Worker 内部实现 → 直接读源码或 grep

## 前置条件

- omnicompany 已装 (omni CLI 可用) — 确认: `omni --help`
- 在 git repo 里 (增量扫描依赖 git diff) — 非 git 环境只能跑 full scan
- 首次用前先跑一次 rebuild 让 G2 索引就位 (老仓库) — 见场景 D

## 操作步骤

### 场景 A · 查所有实体 (按类型 / 包过滤)

```bash
omni registry list                               # 列所有实体
omni registry list --type=worker                 # 只列 Worker
omni registry list --type=worker --pkg=guardian  # guardian 包下的 Worker
omni registry list --as-json                     # JSON 输出 (给 agent 消费)
```

**验证**: 输出含每条实体的 id / type / package / status. 若数字看起来偏少 (例 0 个 Worker) → 跑场景 D rebuild.

### 场景 B · 看健康档案

```bash
omni registry health                                   # 全体健康概况
omni registry health --type=router --grade=critical    # 只看 critical 级别 Router
omni registry status --type=worker                     # 单类型状态汇总
omni registry regressions --reference-commit=<hash>    # 跟某 commit 对比, 看回归
```

**验证**: regressions 输出格式 `{added: [...], degraded: [...], improved: [...]}`. 全空数组 = 没回归.

### 场景 C · G2 索引正查反查

```bash
omni registry whois material:services.docauthor.manifest_author.py   # material_id → 文件路径
omni registry whoami src/omnicompany/packages/services/_authoring/docauthor/workers/manifest_author.py   # 路径 → material_id
omni registry materials                                              # 列全 1612 条 G2 索引
omni registry materials --pkg=guardian                               # 过滤
omni registry materials --kind=worker                                # 按 kind 过滤
```

**验证**: whois/whoami 应是双向一致 — `whois X` 返路径 P, `whoami P` 返 X.

### 场景 D · 重建索引 (新代码 / 大量改动后)

```bash
omni registry rebuild                            # 全量扫源码重建注册
omni registry rebuild --from-headers             # 只从 OmniMark 头扫 G2 索引 (1612 条快, 推荐)
omni registry rebuild --scopes=workers,materials # 只重建特定 scope
```

**验证**: rebuild 后跑场景 A 看实体数对得上预期, 或跑 `omni self-portrait stats` 看 G2 索引数对.

### 场景 E · 标实体为 strict member (升入 Phase 2 强制规范)

```bash
omni registry mark-strict <entity_id>      # 标 strict
omni registry mark-strict <entity_id> --unmark   # 取消标
```

**理由**: strict member 受 Guardian 强制规则 (OmniMark 头必填 / 命名纪律 / 别名禁用), 给已稳定实体加这层保护.

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `omni registry list` | 列实体 | `--type` `--pkg` `--as-json` |
| `omni registry health` | 健康档案 | `--type` `--grade` |
| `omni registry status` | 状态汇总 | `--type` |
| `omni registry regressions` | 回归对比 | `--reference-commit` |
| `omni registry rebuild` | 重建索引 | `--from-headers` `--scopes` |
| `omni registry whois <material_id>` | G2 反查 | — |
| `omni registry whoami <path>` | G2 正查 | — |
| `omni registry materials` | 列 G2 全量 | `--pkg` `--kind` |
| `omni registry mark-strict <id>` | 标 strict | `--unmark` |
| `omni lookup` (顶层命令) | 多维度查询 | `--package` `--tag` `--stage` |
| `omni self-portrait stats` (CORE-SELF-STABILITY 加) | 看 belongs_to_service 填写情况 | — |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| `omni registry list` 输出 0 条 | InstanceRegistry JSONL 没建或被清 | `omni registry rebuild` |
| 新加 Worker 查不到 | scanner 没扫该文件 (新文件未触发增量) | `omni registry rebuild --scopes=workers` |
| `whois` / `whoami` 报 not found | G2 索引过期 | `omni registry rebuild --from-headers` |
| `rebuild --from-headers` 数量比预期少 | 部分文件 OmniMark 头缺失或不合规 | `omni guardian patrol` 找出无头文件 |
| `regressions` 报 reference commit 不存在 | git 历史没那个 hash | `git log --oneline` 找正确 hash |
| 增量扫描在非 git 环境失败 | `IncrementalDiagnosis` 依赖 git | 用 `omni registry rebuild` 全量代替 |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 → [DESIGN.md](DESIGN.md)
- 注册体系规范 (写入侧) → [docs/standards/cli/registration.md](../../../../../../docs/standards/cli/registration.md)
- G2 索引来由 → [docs/plans/guardian/[2026-05-04]CORE-SELF-STABILITY/plan.md](../../../../../docs/plans/guardian/%5B2026-05-04%5DCORE-SELF-STABILITY/plan.md)
- 命名跟概念 → [terminology.md](../../../../../../docs/standards/_global/terminology.md)
