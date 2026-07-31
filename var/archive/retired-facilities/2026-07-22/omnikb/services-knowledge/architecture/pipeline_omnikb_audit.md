# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:39:34Z
---
omnikb_type: karch
id: kb.arch.pipeline.omnikb_audit
name: 'Pipeline: omnikb-audit'
tags:
- layer.pipeline
- topic.pipeline
- pipeline.omnikb-audit
- domain.knowledge
- architecture
maturity: living
summary: OmniKB 全量审计 — 校验知识库引用完整性、code_anchor 漂移、孤儿 Router、Format 覆盖
scope: omnicompany
code_anchors:
- src/omnicompany/core/pipelines.py:L212-L237
---

# Pipeline: omnikb-audit

> **id**: `kb.arch.pipeline.omnikb_audit` · **type**: karch · **maturity**: living

OmniKB 全量审计 — 校验知识库引用完整性、code_anchor 漂移、孤儿 Router、Format 覆盖

## Why this exists

OmniKB 是 Omnicompany 在 2026-04-07 复活的 Markdown + YAML 知识库。复活时设定了一条铁律: 写盘必须经 `guarded_write`, 字段必须有交叉引用。但人和 agent 写 KB 时容易出现三类问题: 1) 引用不存在的 entry id (例如 `related_decisions: [kb.decision.foo]` 但 foo 没建); 2) `code_anchors` 指向已被重命名或删除的文件; 3) 仓里新增了 Router/Format 但 KB 里没有对应描述, 知识慢慢失同步。`omnikb-audit` 这条管线就是 KB 的 self-check, 把这三类漂移变成可见的报告, 防止 KB 慢慢退化为不可信的文档堆。

## How it works

管线只有 1 个节点 `audit_all`, 类型 ANCHOR + HARD, 实现是 `KBAuditRouter` (位于 `src/omnicompany/packages/services/knowledge/routers.py`)。它调 `audit.run_full_audit()` 跑 5 类检查并聚合: (1) `validate()` — 校验所有 entry 的 id 唯一 + 引用完整性 (kformat/krouter/karch/kdec 之间的 relates_to_* 必须有效); (2) `check_code_anchors()` — 解析每个 KArch 的 `code_anchors`, 检查文件存在 + 行号未越界, 失败的进 anchor_drifts 列表; (3) `find_orphan_routers()` — 扫 `src/omnicompany/**/*.py` 找所有 `class XxxRouter(Router|LLMRouter|AgentNodeLoop):` 定义, 与 KB 里所有 `KRouter.relates_to_routers` 字段比对, 没有覆盖的进 orphan_routers; (4) `staleness_report()` — 反向找 KRouter 引用的 Router 类是否还存在; (5) `format_coverage_report()` — 对比 KFormat 与可执行 FormatRegistry。聚合 Verdict 规则: 任何 error 级别 → FAIL+HALT; 只有 warning/info → PARTIAL+EMIT (通过但带提醒); 全清 → PASS+EMIT。

## Public surface

外部调用者只需要 `omni run omnikb-audit -j '{}'`, 无任何参数。返回的 audit_report 是一个 dict, 顶层 5 个字段对应 5 类检查 (`validation_issues / anchor_drifts / orphan_routers / staleness / format_coverage`), 加 `summary` 一句话和 `has_issues` 布尔。其他管线 (例如 guardian patrol 中的 OMNI-017 规则) 可以通过 PipelineRunner 程序化调用并解析此 dict。

## Internal structure

管线本身极简: 只有 `pipeline.py:build_audit_pipeline()` 定义单节点 PipelineSpec, `run.py:build_audit_bindings()` 把 `audit_all` 绑到 `KBAuditRouter` 实例。所有重活在 `audit.py` 模块: `run_full_audit()` 是入口, 内部调 5 个独立的子函数, 每个返回各自的 dataclass。`AuditReport` dataclass 是聚合容器, 提供 `.summary()` 和 `.has_issues()` 便利方法。

## Files

- `src/omnicompany/packages/services/knowledge/pipeline.py` — 1 节点 PipelineSpec
- `src/omnicompany/packages/services/knowledge/run.py` — bindings 入口
- `src/omnicompany/packages/services/knowledge/routers.py` — `KBAuditRouter` 实现
- `src/omnicompany/packages/services/knowledge/audit.py` — 5 类审计的真实逻辑
- `src/omnicompany/core/pipelines.py:L211-L233` — 注册块

## Related

- `kb.arch.package.services_knowledge` — 本管线所属的 package, 解释整个 OmniKB 系统设计
- `kb.experiment.20260409_knowledge_revival_and_absorption_redesign` — 本管线的设计实验, 含为什么从 graveyard 复活而非新建
- `kb.experiment.retired_knowledge` — OmniKB 在 2026-04-07 被退役的原因, 直接驱动了 KBAuditRouter 的设计 (避免再次出现 `zero live callers`)
- 暂无关联 KDecision (待补)

## Known limitations

1. 当前 `find_orphan_routers` 是纯 regex 匹配 `class XxxRouter(Router):`, 不识别多重继承或泛型, 可能漏抓一些定义。2. `code_anchors` 的行号校验只检查 end 是否超出文件, 不检查 start-end 区间内容是否还和当时一致——文件被大幅改动但行号刚好没越界时, drift 不会被发现。3. 整个审计是单线程串行的, 仓库大时 (>500 entry) 可能慢, 但目前 100 量级无瓶颈。4. 没有 `--fix` 模式: 发现的 issue 必须人工或后续 enrich 管线解决, audit 本身不修。

## Change log

- 2026-04-08 — enriched by manual (golden sample)
