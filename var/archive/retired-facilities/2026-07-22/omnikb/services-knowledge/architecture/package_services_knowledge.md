# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:40:36Z
---
omnikb_type: karch
id: kb.arch.package.services_knowledge
name: 'Package: packages/services/knowledge'
tags:
- topic.package
- layer.services
- domain.knowledge
- architecture
maturity: living
summary: omnicompany.packages.services.knowledge — OmniKB.
scope: omnicompany
code_anchors:
- src/omnicompany/packages/services/knowledge/__init__.py
- src/omnicompany/packages/services/knowledge/pipeline.py
- src/omnicompany/packages/services/knowledge/routers.py
- src/omnicompany/packages/services/knowledge/run.py
---

# Package: packages/services/knowledge

> **id**: `kb.arch.package.services_knowledge` · **type**: karch · **maturity**: living

omnicompany.packages.services.knowledge — OmniKB.

## Why this exists

Omnicompany 自身和外部 repo 都需要一份长期持久的、人/agent 共同可读写的知识沉淀, 而不是每次跑管线都从 FS 重新扫描。这份需求曾在 2026-04-07 被 OmniKB 实现但因为零调用而退役 (`kb.experiment.retired_knowledge`), 也曾被 narrative 计划 `[2026-04-07]NARRATIVE-CREATION-ENGINE/05-omnicompany-gaps.md` 期望复用 (作为 KFormat)。2026-04-09 复活时同时承担两个角色: 给 absorption v2 提供 Omnicompany 自知对照集, 给 narrative 提供事实账本基建。这就是 services/knowledge 包的理由——它不是一个新的子系统, 是一个被反复需要但反复延后的基建。

## How it works

整个 KB 用 6 个 Python 文件 + 5 个 Router 实现:

1. **存储层** (`store.py`): 扫描 `data/knowledge/` 和 `packages/*/knowledge/` 下的所有 .md 文件, 用 YAML frontmatter 区分 entry 类型, 经 `core.guarded_write` 原子落盘
2. **schema 层** (`schema.py`): 6 种 entry 类型 (kformat / krouter / karch / kdec / kexp / krepo), 共享 `_BaseEntry` 基类提供 id/name/tags/maturity, 每种类型各自 pydantic 模型
3. **索引层** (`index.py`): 内存里按 type 分桶 + tag/domain/scope 反向索引, 提供 6 维 `find()` 查询和轻量 `text_search()`, 持久化到 `.omni/knowledge_index.json`
4. **审计层** (`audit.py`): 5 类一致性检查 (validation / anchor drift / orphan routers / staleness / format coverage)
5. **路由层** (`routers.py`): 把上面 4 层包成 5 个 Router, 让其他管线通过 `KBQueryRouter` / `KBWriteRouter` / `KBLocateRouter` / `KBAuditRouter` / `KBIndexRebuildRouter` 调用, 不需要直接 import Python 类

## Public surface

### 给其他管线 import 的:

- `KBStore` — 直接读写 (适合 seed 脚本和测试)
- `KBIndex.find()` / `text_search()` — 内存查询 (适合需要批量过滤的场景)
- `load_or_rebuild()` — 一句话拿到当前 KB 的全图
- `run_full_audit()` — 一次跑完所有审计

### 给其他管线通过 PipelineRunner 调的:

- `omnikb-audit` 管线 (5 类审计聚合)
- 5 个 Router 类可独立绑到任何 pipeline 节点

### 用户/agent 直接编辑:

- `data/knowledge/**/*.md` — 直接 git diff / vim 编辑均可
- 改完后跑 `omni run omnikb-audit -j '{}'` 验证一致性

## Internal structure

```
src/omnicompany/packages/services/knowledge/
├── __init__.py        # 统一导出
├── schema.py          # 6 entry 类型 + parser
├── store.py           # 文件树读写 + guarded_write 集成
├── index.py           # 内存索引 + 持久化 + validate
├── audit.py           # 5 类一致性审计
├── routers.py         # 5 个 Router
├── pipeline.py        # omnikb-audit PipelineSpec
├── run.py             # build_*_pipeline / build_*_bindings
├── seed.py            # 规则提取脚本 (R3.1)
├── enrich.py          # LLM 补完脚本 (R3.2)
└── enrich_manual.py   # 手动 sections JSON enrich, 用于 golden samples
```

物理存储分两处:
1. `data/knowledge/` — 跨 package 的长期资产 (architecture / decisions / experiments / external_repos)
2. `packages/<ns>/<pkg>/knowledge/` — 业务包内部的领域知识 (workflow_factory 和 demogame 已有)

两边都被 `KBStore.iter_all_paths()` 扫到, 索引时无差别。

## Files

- `src/omnicompany/packages/services/knowledge/__init__.py`
- `src/omnicompany/packages/services/knowledge/schema.py`
- `src/omnicompany/packages/services/knowledge/store.py`
- `src/omnicompany/packages/services/knowledge/index.py`
- `src/omnicompany/packages/services/knowledge/audit.py`
- `src/omnicompany/packages/services/knowledge/routers.py`
- `src/omnicompany/packages/services/knowledge/pipeline.py`
- `src/omnicompany/packages/services/knowledge/run.py`
- `src/omnicompany/packages/services/knowledge/seed.py`
- `src/omnicompany/packages/services/knowledge/enrich.py`
- `src/omnicompany/packages/services/knowledge/enrich_manual.py`

## Related

- `kb.arch.pipeline.omnikb_audit` — 本包暴露的第一个管线
- `kb.experiment.20260409_knowledge_revival_and_absorption_redesign` — 本包的设计实验, 含为什么从 graveyard 复活、为什么用 markdown 而非 SQLite
- `kb.experiment.retired_knowledge` — OmniKB 退役教训, 直接驱动 'Router 暴露作为入口' 设计 (避免再次零调用)
- `kb.experiment.20260408_repo_absorption_workflow` — absorption 系统是本包的第一个真实 caller, 它通过 KBQueryRouter 替代了 snapshot.py

## Known limitations

1. **没有向量检索** — `text_search` 是子串匹配, 不识别同义词或语义相似 (例如 'context compression' 不会命中 'session compaction'). 这是有意的简化, 第一版避免引入 embedding 基建, 但等条目数过百时需要重新评估
2. **enrich.py 依赖外部 LLM** — 当前 LLM 代理 401/502 时无法批量生成, 只能用 enrich_manual.py 手写。设计上不应该依赖 LLM 才能 'long the KB', 但当前 200+ 条目还是需要 LLM 帮忙才现实
3. **没有 web UI** — 只能 CLI 或直接编辑 .md, 暂时够用但 KB 长大后人工查询会变慢
4. **6 类 entry 是闭集** — 如果将来需要新类型 (例如 KGlossary 词汇表), 必须改 schema.py 的 OMNIGB_TYPES 常量并扩 _TYPE_TO_CLASS, 不支持运行时动态注册
5. **跨 entry 引用是单向校验** — `karch.related_decisions` 验证 kdec 存在, 但 kdec 不会主动反向引用 karch, 双向图必须人工维护

## Change log

- 2026-04-08 — enriched by manual (golden sample)
