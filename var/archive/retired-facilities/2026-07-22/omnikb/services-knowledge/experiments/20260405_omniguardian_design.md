# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:22Z
---
omnikb_type: kexp
id: kb.experiment.20260405_omniguardian_design
name: OmniGuardian — 电子警察 + 统一文件身份信用 + 罚单体系
tags:
- topic.plan
- date.2026-04-05
maturity: draft
summary: '**日期**: 2026-04-05 **状态**: 设计阶段 **优先级**: P1（影响所有管线安全性）'
date_started: '2026-04-05'
method_summary: see docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/
status: documented
followups:
- docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/01-OMNIMARK.md
- docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/02-OMNISHIELD.md
- docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/03-OMNIPATROL.md
- docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/04-OMNITOW.md
- docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/05-OMNIEVOLVE.md
- docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/06-SENTINEL-AND-ARCH.md
- docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/AUDIT-CHECKLIST.md
- docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/HEALTH-SPEC.md
- docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/README.md
---

# OmniGuardian — 电子警察 + 统一文件身份信用 + 罚单体系

> Plan directory: `docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# OmniGuardian — 电子警察 + 统一文件身份信用 + 罚单体系

**日期**: 2026-04-05  
**状态**: 设计阶段  
**优先级**: P1（影响所有管线安全性）

---

## 背景与动机

Omnicompany 是一个**基于源码运行、会自我修改源码**的系统。这导致了普通软件没有的独特问题：
代码的修改者不只是人类，还有 LLM 管线和各种 Agent，修改频率很高，且修改意图隐含在
trace 链中而非 git commit message 里。

当前问题：
- 无法追踪某个文件到底是谁写的、出于什么意图
- LLM 偶尔绕过 LLMClient 直接 import anthropic，绕过 EventBus 直接写文件
- 临时脚本泄漏进 src/ 目录，数据库文件散落在非预期位置
- guardian 管线目前是手动触发的点检工具，缺乏自动巡逻能力

---

## 系统目标

1. **可溯源**：每个文件知道自己是谁写的、什么时候、出于什么管线意图
2. **可监控**：定期自动巡逻检查新变更是否符合架构规范
3. **可处置**：发现违规时有标准化的处置流程（警告 / 隔离 / 告示牌）
4. **可矫正**：内部管线反复违规时能自动修正其行为（prompt 进化）
5. **常驻**：随核心组件初始化自动启动，不需要人工触发

---

## 五大子系统

| 子系统 | 职责 | 类比 |
|--------|------|------|
| **OmniMark** | 文件身份头标签 | Apache RAT + Linux Signed-off-by |
| **OmniShield** | 统一写入拦截层 | Deno permissions + K8s Admission Webhook |
| **OmniPatrol** | 周期巡逻 Hook | Falco + Auditd |
| **OmniTow** | 违规处置（拖车+告示牌） | SpamAssassin quarantine |
| **OmniEvolve** | 内部行为矫正信号 | Kubernetes reconcile loop |
| **OmniSentinel** | 自动启动 + 版本刷新 | systemd socket activation |

---

## 实现路线图

| Phase | 内容 | 依赖 | 状态 |
|-------|------|------|------|
| **P0** | OmniMark 格式规范 + 扩展 ArchAuditor 检查头 | 无 | 待实现 |
| **P1** | OmniPatrol PeriodicHook + 7条核心规则引擎 | P0 | 待实现 |
| **P2** | OmniTow 处置系统（TOMBSTONE/QUARANTINE/罚单） | P1 | 待实现 |
| **P3** | OmniShield WriteIntent 包装层（AgentNodeLoop Tool层） | P0 | 待实现 |
| **P4** | OmniEvolve 内部矫正信号 + 禁写 Filter | P2+P3 | 待实现 |
| **P5** | OmniSentinel 自启动 + 版本刷新 | P1 | 待实现 |

---

## 文件结构

```
docs/plans/[2026-04-05]OMNIGUAR
```

## Plan files

- `docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/01-OMNIMARK.md`
- `docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/02-OMNISHIELD.md`
- `docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/03-OMNIPATROL.md`
- `docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/04-OMNITOW.md`
- `docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/05-OMNIEVOLVE.md`
- `docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/06-SENTINEL-AND-ARCH.md`
- `docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/AUDIT-CHECKLIST.md`
- `docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/HEALTH-SPEC.md`
- `docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/README.md`

## Hypothesis

_(待补充: 这个 plan 的核心假设, 为什么需要做)_

## Method

_(待补充: 实施方法, 关键步骤)_

## Samples

_(待补充: 跑过的样本, 各自结果)_

## Findings

_(待补充: 关键发现, 哪些假设被验证, 哪些被推翻)_

## Followups

_(待补充: 后续 TODO, 关联其他 plan / 计划目录中的文件已自动列在 frontmatter)_

## Change log

- 2026-04-08 — auto-seeded from plan README
