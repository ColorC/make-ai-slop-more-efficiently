# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:24Z
---
omnikb_type: kexp
id: kb.experiment.retired_knowledge
name: 'Retired: knowledge'
tags:
- topic.retired
- stage.abandoned
- retired_from.knowledge
maturity: deprecated
summary: OmniKB (the Markdown-backed knowledge base system) was designed and implemented
  but never wired into any live code path. Zero live callers across src/, tests/,
  scripts/ at retirement time. Moved here rather than deleted so the design can be
  revived if someone actually needs it.
date_concluded: '2026-04-07'
method_summary: see _graveyard/knowledge/
findings_summary:
- '原始位置: src/omnicompany/_graveyard/knowledge'
- '退役理由: 见 _RETIRED.md (本 entry body 含全文)'
status: abandoned
---

# Retired: knowledge

> Original location: `src/omnicompany/_graveyard/knowledge/`
> Retired on: 2026-04-07

## Retirement notes (verbatim from _RETIRED.md)

# Retired 2026-04-07

OmniKB (the Markdown-backed knowledge base system) was designed and
implemented but never wired into any live code path. Zero live callers across
src/, tests/, scripts/ at retirement time. Moved here rather than deleted so
the design can be revived if someone actually needs it.

Entry points were:
- `KBStore` (store.py) — file tree read/write
- `KBIndex` (search.py) — in-memory search
- `KBManager` (manager.py) — CRUD
- `KFormatEntry`, `KRouterEntry` (schema.py) — Markdown + YAML frontmatter
  mirrors of Format/Router.

## What was lost

_(待补充: 这次退役放弃了什么能力? 是否有替代方案?)_

## Why it failed / was abandoned

_(待补充: 退役的根本原因, 不只是表面的 "no callers")_

## Lessons for future attempts

_(待补充: 如果将来重做类似系统应该注意什么?)_

## Resurrection conditions

_(待补充: 在什么情况下值得复活? 如本次 OmniKB 的复活就是一个例子)_

## Change log

- 2026-04-08 — auto-seeded from `_graveyard/knowledge/_RETIRED.md`
