---
name: guardian
description: omnicompany 源码合规自动巡逻 service - 跑 OMNI-NNN 规则扫源码/文档/架构, 装 pre-commit hook, 跑 sentinel 常驻巡逻, 看违规清单跟罚单, 用 OMNI-093 防核心设施唯一权威漂移.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/guardian ts=2026-05-04T11:35:00Z type=doc status=active agent=ai-ide belongs_to_service=guardian -->
<!-- [OMNI] summary="guardian 操作手册 — 5 个场景 (一次性扫/装 hook/常驻 daemon/看违规处理罚单/管 archmap), 入口清单 (15+ 子命令), 故障排查" -->
<!-- [OMNI] why="DESIGN.md 偏架构, 缺'怎么用'段. guardian 命令多 (15+ 子命令), 用户/agent 想用得 grep 拼. 抽出独立 SKILL 让操作可定位" -->
<!-- [OMNI] tags=skill,guardian,how-to,cli,compliance -->
<!-- [OMNI] material_id="material:services._core.guardian.skill.operations_manual.md"-->

# guardian · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).
> 核心设施统一方向见 authority-confirmation.md, 长程执行门禁见 autonomous-execution-rules.md。本 skill 只说明怎么跑 guardian, 不复制第二套唯一权威。

---

## 适用范围

**用我**:
- 想扫一次本仓有哪些违规 (`omni guardian patrol`)
- 想装 pre-commit hook 拦未来违规 (`omni guardian hook-install`)
- 跑常驻 sentinel 持续巡逻 (`omni guardian daemon`)
- 看现有违规清单 / 处理罚单 (`omni guardian violations / tickets`)
- 管 archmap (合法 drawer 定义)
- 给文件加 OmniMark 头 (`omni guardian stamp`)
- 看哪个文件被谁写过 (`omni guardian who`)
- 防核心设施/唯一权威漂移 (`OMNI-093a~d`)

**不用我**:
- 想做运行时诊断 (Format/Worker/Team 健康) → 找 [doctor service](../../_diagnosis/doctor/)
- 想自动修代码 → 找 [services/repair/](../../) (guardian 主要 warn, 修是 repair 的事)
- 想注册新实体 → 找 register CLI, 不是 guardian

## 前置条件

- omnicompany 已装 (`omni --help` 确认)
- 在 git repo 内 (大部分规则依赖 git diff / git log)
- 有 `THE_COMPANY_API_KEY` (needs_judgment 规则要 LLM 复核)

## 操作步骤

### 场景 A · 一次性扫违规 (新人 onboarding / CI 用)

```bash
omni guardian patrol                              # 扫整个仓库
omni guardian patrol --scope=services/guardian    # 只扫某区
omni guardian patrol --rules=OMNI-007,OMNI-014    # 只跑某些规则
```

**验证**: 输出 `data/guardian/violations.jsonl` (append-only) + `data/guardian/reports/<date>.md` (人类可读). 同步到 docs/tech_debt/REGISTRY.md §活跃违规.

### 场景 B · 装 pre-commit hook 拦未来违规

```bash
omni guardian hook-install                # 幂等安装 (5 态管理: absent/managed-current/managed-stale/foreign/replaced-foreign)
omni guardian hook-install --dry-run      # 看会写啥不真改
omni guardian hook-check                  # 看当前 hook 状态
```

**验证**: `.git/hooks/pre-commit` 有 `# OMNI-GUARDIAN-MANAGED` marker. 之后 commit 撞 BLOCK_RULES (OMNI-014~018 / 035f~i HIGH) 会被拒, 受 hygiene_whitelist 豁免的违规自动跳过.

### 场景 C · 常驻 sentinel daemon (开发期持续巡逻)

```bash
omni guardian daemon                                   # 后台跑, 文件树变更才唤醒
omni guardian daemon --wake-interval=300 --once        # 跑一轮看效果
omni guardian daemon --cooldown=60 --llm-cooldown=180  # 调冷却时间
```

**用途**: sentinel 进冷却期不空转, 文件变更才扫. 唤醒时一次性扫 + 调用 `escalate_overdue_tickets()` 处理 7 天逾期罚单 (升级到 evolve-signal, 0 LLM 消耗).

### 场景 D · 看违规清单 / 处理罚单

```bash
omni guardian violations                          # 列所有违规
omni guardian violations --rule-id=OMNI-014      # 按规则过滤
omni guardian violations --last-n=20             # 最近 20 条
omni guardian tickets                             # 列罚单 (违规归档)
omni guardian tickets --status=open              # 只看 open
omni guardian tickets --ticket-id=<id>           # 看单条罚单详情
omni guardian restore --ticket-id=<id>           # 恢复罚单内容到原位 (撤销 quarantine)
omni guardian whitelist --ticket-id=<id> --reason="..." --hours=720    # 豁免 30 天
omni guardian apply-fixes                       # 跑 auto_comment_pilot_rules 批量修
omni guardian trace-violation <path>            # 查某文件违规历史
```

### 场景 E · 管 archmap (合法 drawer 定义)

```bash
omni guardian archmap show                                    # 看结构树
omni guardian archmap validate                                # 校验 yaml 格式 + 字段完整性
omni guardian archmap check <path> --writer=<identity>       # 试判: 这路径这身份能写吗
omni guardian archmap diff                                    # 跟上次比变化
```

**用途**: archmap.yaml 是合法 drawer 唯一权威, 改它需要 human 审 (agent 不能自动改). archmap 改了之后 OMNI-014/015 等会跟着变.

### 场景 F · 给文件加 OmniMark 头 / 找文件来源

```bash
omni guardian stamp <file>                              # 给单文件加 OmniMark 头 (基于 git log 推 origin)
omni guardian stamp-dir <dir> --ext=.py                 # 批量加, 按 ext 过滤
omni guardian stamp-sweep --target=services             # 扫整个 target 区批量加 (有 dry-run)
omni guardian who <file>                                # 这个文件谁加的 OmniMark / 谁动过
omni guardian metadata-report --by-package              # OmniMark 头的覆盖率统计
```

### 场景 G · 看演化信号 / 应用变更

```bash
omni guardian evolution-history --node-id=<id>         # 看某节点的演化历史
omni guardian evolution-apply --node-id=<id>           # 应用最新变更
omni guardian shield-status --tail=20                  # 守盾状态 (auto_comment 落盘 + GUARDIAN_ALERT.md 等)
omni guardian zombies                                   # 找 zombie 进程 (sentinel 残留 + 等)
omni guardian zombies --kill                           # 真杀
```

### 场景 H · 核心设施唯一权威防漂移

```bash
python -m pytest tests/guardian/test_authority_convergence.py
omni guardian patrol --json-out
```

**用途**: 单测精确确认 `authority-confirmation.md` 仍是 active 方向权威、`autonomous-execution-rules.md` 仍绑定确认表、分散 README / standards / templates / SKILL 只保留短锚点; patrol 走真实 guardian 路径。当前 CLI 不支持按 rule 过滤, patrol 输出里若命中 `OMNI-093a~d`, 不要在分散文件里补第二套规则, 回集中确认表或执行规范修。

## 入口清单

| 入口 | 场景 | 主要参数 |
|---|---|---|
| `omni guardian patrol` | 一次性扫 | `--scope` `--rules` |
| `omni guardian hook-install` | 装 pre-commit | `--dry-run` `--force` |
| `omni guardian hook-check` | 看 hook 状态 | — |
| `omni guardian daemon` | 常驻 sentinel | `--wake-interval` `--cooldown` `--once` |
| `omni guardian violations` | 看违规 | `--rule-id` `--last-n` |
| `omni guardian tickets` | 看罚单 | `--status` `--ticket-id` |
| `omni guardian restore` | 撤销 quarantine | `--ticket-id` |
| `omni guardian whitelist` | 豁免 | `--ticket-id` `--reason` `--hours` |
| `omni guardian apply-fixes` | 批量修 | `--ticket` `--list-only` |
| `omni guardian trace-violation` | 文件违规历史 | `<path>` |
| `omni guardian archmap show / validate / check / diff` | 管 archmap | (见场景 E) |
| `omni guardian stamp / stamp-dir / stamp-sweep` | OmniMark 注入 | `--ext` `--target` `--dry-run` |
| `omni guardian who` | 文件来源 | `<path>` |
| `omni guardian metadata-report` | OmniMark 覆盖统计 | `--by-package` |
| `omni guardian evolution-history / apply` | 演化信号 | `--node-id` |
| `omni guardian shield-status / zombies` | 守盾 / zombies | (见场景 G) |
| `omni guardian register` | 注册新实体 | (Phase 1 后接通) |
| `omni guardian health` | 健康概况 | — |
| `omni guardian report` | 生成报告 | `--out` `--quiet` |
| `omni guardian prompt-scan` | 扫 prompt 反模式 | `--scope` `--rule-filter` |

详细 CLI 规范: [docs/standards/cli/omnicompany_cli.md](../../../../../../docs/standards/cli/omnicompany_cli.md)

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| `patrol` 报"archmap 不可加载" | docs/archmap.yaml 格式错或被删 | 跑 `omni guardian archmap validate` 看错在哪, 修 yaml |
| pre-commit hook 拦了真合理的改动 | 撞 BLOCK_RULES, 但实际是误报 | 用 `omni guardian whitelist --ticket-id=<id> --reason="..." --hours=720` 临时豁免 |
| sentinel daemon 跑不起来 | 已有 zombie 进程占着 | `omni guardian zombies --kill` 清掉, 再跑 daemon |
| daemon 一直在 LLM 调用花钱 | needs_judgment 规则太多 + cooldown 短 | 调 `--llm-cooldown` 高一点, 或先 disable LLM 规则 |
| `OMNI-007 src 下不放文档` 误报 | DESIGN.md / .omni/manifest.yaml / README.md 等合法文件 | 看是否在豁免列表里; 如不在, 是真违规, 移到 docs/ 或加豁免 |
| OmniMark 头加完文件不能跑 | stamp 注入位置不对 (覆盖了 shebang 等) | 用 `--overwrite=False` 默认避免覆盖, 或手工挪头到正确位置 |
| 罚单太多看不过来 | 没批量处理 | 跑 `omni guardian apply-fixes` 跑 auto_comment_pilot_rules 批量修, 或 `--list-only` 先看清单 |
| Guardian Agent LLM 复核拒绝大量真违规 | LLM 可能过严, 或 prompt 没提供足够上下文 | 看 `data/services/guardian/audit/records.jsonl` audit 看 LLM 判定记录, 必要时调 prompt |
| `omni guardian hook-install` 报 "foreign hook detected" | 之前装过别的 pre-commit, 不是 OMNI-GUARDIAN-MANAGED | `--force` 覆盖 (会备份旧 hook), 或手工合并 |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 (D1-D10 决策 / 巡逻管线拓扑 / 规则家族) → [DESIGN.md](DESIGN.md)
- doctor (运行时诊断, 跟 guardian 互补) → [../../_diagnosis/doctor/](../../_diagnosis/doctor/)
- archmap.yaml (合法 drawer 唯一权威) → docs/archmap.yaml
- ARCH-CHANGES.jsonl (架构变更日志) → docs/ARCH-CHANGES.jsonl
- 自稳第二阶段道路 (扩 guardian 规则) → [docs/plans/guardian/[2026-05-04]CORE-SELF-STABILITY/plan.md](../../../../../docs/plans/guardian/%5B2026-05-04%5DCORE-SELF-STABILITY/plan.md)
- 控制结构 (核心层四件武器) → docs/控制结构.md
