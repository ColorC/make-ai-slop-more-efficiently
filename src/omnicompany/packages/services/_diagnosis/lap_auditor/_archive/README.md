<!-- [OMNI] origin=ai-ide domain=services/lap_auditor ts=2026-05-04T12:00:00Z type=doc status=active agent=ai-ide belongs_to_service=lap_auditor -->
<!-- [OMNI] summary="lap_auditor service 自我叙事 README — LAP 协议合规审计, 通过 LLM 按四大红线 (事件总线驱动/Format 真实性/接口规范实现/Domain 隔离) 给 .py 代码归类 + 修复建议" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写 — 不再每份重写各异格式. 抽核心目的到 README, DESIGN 留架构性内容" -->
<!-- [OMNI] tags=readme,lap_auditor,diagnosis,self-narrative -->
<!-- [OMNI] material_id="material:services._diagnosis.lap_auditor.readme.self_narrative.md"-->

# lap_auditor · LAP 协议合规审计

> 给 Python 代码按 LAP 四大红线 (事件总线驱动 / Format 真实性 / 接口规范实现 / Domain 隔离) 做归类 + 修复建议. 三节点线性 Team (ContextGetter → SpecAuditor LLM → ReportFormatter).

---

## 这是什么

lap_auditor 是 omnicompany 的 **LAP 协议合规审计 service**. 接收 Python 源码路径, 读取代码, 通过 LLM 按 LAP 四大红线给代码归类 (规范 LAP 实现 / 有缺陷的 LAP / 绕过 LAP 的业务代码 / 基础设施代码), 输出 Markdown 形式的修复建议.

形态是**三节点线性 Team** (无分支, PASS 直穿). 不阻塞管线 — 即使审计结论是"缺陷代码", Verdict 仍 PASS, 结论在报告文本中, 调用方负责解读跟决策.

跟其他诊断 service 的边界:
- **doctor** 看 Format/Worker/Team 单对象语义健康
- **guardian** 看源码静态合规 (位置 / 命名 / 头)
- **lap_auditor** 看代码是否按 **LAP 协议** 写 (事件总线 + Format 真实性 + 接口规范 + Domain 隔离)
- **repair** 跟 lap_auditor 互补 — 它把 lap_auditor 发现的问题转修复候选

## 解决什么 / 不解决什么

**解决**:
- 快速识别哪些代码是规范 LAP / 有缺陷 LAP / 绕过 LAP / 基础设施
- 给出明确的修复方向 (按四大红线分类)
- 引导 LAP 重构方向

**不解决**:
- 自动修复 (那是 [services/repair/](../../) 职责)
- 非 Python 代码审计 (当前仅 `.py`)
- 业务正确性 (LAP 是协议合规, 不是业务对错)
- 替代 doctor 的 Format/Worker 健康诊断

## 设计目的与最终目标

**设计目的**: 把 LAP 协议合规变成可机器审计的事 — 不靠人逐文件读, 跑 lap_auditor 拿 Markdown 报告. L2 看报告决定重构优先级 / 派 repair / 派人工.

**理论锚点**: LAP 协议是 omnicompany 的核心契约 (事件总线驱动 / Format 真实性 / 接口规范 / Domain 隔离). 没 lap_auditor 这层独立审计, 协议合规靠人记忆, 必漂移.

**最终目标** (当下能认知的):
- 按需扩展审计维度 (例 Team / Material 规范检查)
- 跟 repair service 形成"审 → 修" 闭环
- 接入 CORE-SELF-STABILITY 第二阶段 自我画像漂移检测 — 让"代码漂离 LAP" 自动可见

## 规划

- **当前 V1** (active, 2026-04-21 Stage 3 Clean Migration 完成): 3 Worker (ContextGetter/SpecAuditor/ReportFormatter) + 4 Material + 1 Team
- **下一步**: 按需扩展审计维度 (例 Team/Material 规范检查), 跟 repair 接通形成闭环
- **远景**: 跟自我画像漂移检测协作

进度细节: docs/PROGRESS.md (项目级) + [DESIGN.md `## 状态`](DESIGN.md) (本 service 状态).

## 构成

- 入口与 Team → [team.py](team.py) (`build_team()`) + [run.py](run.py)
- Materials (4 条) → [formats.py](formats.py)
  - `lap_auditor.input` (kind.source) — 外部触发 + target_path
  - `lap_auditor.context` (kind.internal) — 拼装的代码上下文
  - `lap_auditor.report` (kind.internal) — LLM 产 Markdown 报告
  - `lap_auditor.done` (kind.sink) — 报告打印完成
- Workers → [workers/](workers/)
  - `ContextGetterWorker` (ANCHOR) — 递归读 `.py` 文件拼装代码上下文
  - `SpecAuditorWorker` (LLM) — 按四大红线审计 → Markdown 报告
  - `ReportFormatterWorker` (ANCHOR) — 格式化打印 + 保留 report 字段
- 旧名 compat shim → [routers.py](routers.py) (Stage 3 Clean Migration 后保留 alias)

技术架构详述见 [DESIGN.md](DESIGN.md), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- LAP 规范权威 → docs/standards/pipeline.md
- 跟 doctor / guardian 关系 → ../doctor/ / ../../_core/guardian/
- 项目根叙事 → ../../../../../README.md
