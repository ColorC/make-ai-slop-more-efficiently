---
name: docauthor
description: omnicompany 自动文档作者 service - 用它给新建 service 或 skeleton 自动产合规 manifest/DESIGN draft, 走盲审或 Reviewer 闭环.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/docauthor ts=2026-05-04T10:35:00Z type=doc status=active agent=ai-ide belongs_to_service=docauthor -->
<!-- [OMNI] summary="docauthor 操作手册 — 适用范围 + 5 条 CLI 子命令 (scan/run/run-all/observe/issues) 操作步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] why="DESIGN.md 七节没'怎么用'段, 用户/agent 想用 docauthor 得 grep 代码或 plan. 抽出独立 SKILL 让操作可定位" -->
<!-- [OMNI] tags=skill,docauthor,how-to,cli,authoring -->
<!-- [OMNI] material_id="material:services._authoring.docauthor.skill.operations_manual.md"-->

# docauthor · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- 新建 service / package, 想自动产合规 `.omni/manifest.yaml` draft
- 已有 skeleton DESIGN.md 想填补
- 批量给 N 份 skeleton 跑 draft (CI 集成 / 周期扫)
- 看某 target 的 docauthor 历史产出 (回放 / 审计)

**不用我**:
- 写业务代码 → 找 [workflow_factory](../../_diagnosis/) / team_builder
- 升级 PROGRESS.md → 另开 plan, 不归 docauthor
- 直接落盘 draft 到 service `.omni/` → Phase A 不直接落, 走盲审; Phase B 走 Reviewer 闭环
- 写金标样本 → 人类出品, 见 D3 反泄漏

## 前置条件

- omnicompany 已装 (`omni --help` 确认)
- target service 路径合法 (在 `packages/services/(_*/)?<svc>/` 或 `packages/domains/<dom>/<pkg>/` 形态)
- 跑 LLM Worker 需 `THE_COMPANY_API_KEY` 已配 (qwen-3.6-plus 走 the_company 聚合 API)
- skeleton 类目标在 docs/standards/distributed-docs.md 所列合法位置

## 操作步骤

### 场景 A · 给单个 target 跑 manifest draft

```bash
omni docauthor run --kind=manifest --target=packages/services/_core/registry
```

**参数**:
- `--kind`: 当前支持 `manifest` (Phase A); Phase B 起加 `design`
- `--target`: 相对项目根的 service 路径
- `--max-refine=<n>`: 最多 refine 几轮 (默认 0)
- `--dry-run`: 不落 audit, 只跑给看
- `--repo-root=<path>`: 跨仓库跑时显式指定

**验证**: 输出含 `manifest_path` (draft 应落到的位置) + `manifest_content` (draft YAML 内容) + `scan_evidence` (依据). 跑完去 data/services/docauthor/audit/ 查 audit 记录.

### 场景 B · 批量跑所有 skeleton (CI 模式)

```bash
omni docauthor run-all --kind=manifest --max-refine=0 --limit=10
```

**参数**:
- `--limit=<n>`: 限定本次最多跑几个 (避免一次跑 31 个超预算)
- 其余参数同场景 A

**验证**: 跑完输出 `data/services/docauthor/batch_reports/run_all_<ts>.json`, 含每 target 的 verdict / issue_counts / write_status.

### 场景 C · 扫描所有候选 target (跑前看清单)

```bash
omni docauthor scan --kind=manifest
omni docauthor scan --kind=manifest --json-output
```

**用途**: 列出当前所有合法 manifest target (skeleton / 没 manifest / 待补) — 跑 run-all 前看一下清单合不合理.

**验证**: 输出含 N 条候选, 每条标 `has_skeleton` / `has_manifest` / `data_dir_exists`.

### 场景 D · 看某 target 的历史产出

```bash
omni docauthor observe --target=packages/services/_core/registry --n=5
omni docauthor observe --target=... --json-output
```

**用途**: 回放某 target 最近 N 次 docauthor run 的 audit 记录 — prompt / response / verdict / 时间戳.

**验证**: 输出含每次 run 的 LLM prompt + response 摘要 + 落盘判定.

### 场景 E · 看某 target 当前发现的 issue

```bash
omni docauthor issues --target=packages/services/_core/registry
```

**用途**: 看 docauthor 对该 target 给出的 issue 列表 (manifest 不全 / DESIGN 缺节 / 等).

**验证**: 输出含 issue 清单 (severity / 描述 / 建议修法).

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `omni docauthor scan` | 列候选 target | `--kind` `--json-output` `--repo-root` |
| `omni docauthor run` | 跑单个 target | `--kind` `--target` `--max-refine` `--dry-run` |
| `omni docauthor run-all` | 批量跑 | `--kind` `--limit` `--max-refine` `--dry-run` |
| `omni docauthor observe` | 看历史产出 | `--target` `--n` `--json-output` |
| `omni docauthor issues` | 看 issue 清单 | `--target` |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| `omni docauthor run` 报 THE_COMPANY_API_KEY 缺失 | 环境变量没设 | 配 `~/.env` `THE_COMPANY_API_KEY=...` |
| draft 内容明显错乱 / 不合规 | LLM 一次调用质量不稳, Phase A 没 Reviewer | 跑 `--max-refine=2` 或等 Phase B Reviewer 闭环 |
| draft 引用了 plan.md 路径但路径不对 | grep_plan_history 简化版误匹配 | 升级路径在 D2 局限里, 当前手动修 draft |
| domain 多子包 target 漏部分 data 目录 | docauthor 当前按单 package 处理 | 当前局限, 拆子包分别跑 |
| prompt 撑爆 context (非常大 service) | 扫描结果直接拼进 prompt | 当前局限, Phase B 改 agent loop + search tool |
| audit 文件没产出 | 跑了 `--dry-run` | 去掉 `--dry-run` 重跑 |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 → [DESIGN.md](DESIGN.md)
- 文档规范权威 → docs/standards/distributed-docs.md
- 自我叙事三件套规范 → [docs/standards/protocol/self_narrative_three_files.md](../../../../../../docs/standards/protocol/self_narrative_three_files.md)
- 上游 plan + 金标样本 → [docs/plans/[2026-04-25]AUTO-DOCAUTHOR-WORKER/](../../../../../docs/plans/%5B2026-04-25%5DAUTO-DOCAUTHOR-WORKER/)
