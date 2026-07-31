# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:25Z
---
omnikb_type: kexp
id: kb.experiment.retired_tools
name: 'Retired: tools'
tags:
- topic.retired
- stage.abandoned
- retired_from.tools
maturity: deprecated
summary: '`src/omnicompany/tools/` contained two standalone CLI utilities with zero
  imports anywhere (no entry points in pyproject.toml, no runtime references): - `cli.py`
  — text summarization CLI - `skill_import.py` — skill→omnicompany importer CLI wrapper'
date_concluded: '2026-04-07'
method_summary: see _graveyard/tools/
findings_summary:
- '原始位置: src/omnicompany/_graveyard/tools'
- '退役理由: 见 _RETIRED.md (本 entry body 含全文)'
status: abandoned
---

# Retired: tools

> Original location: `src/omnicompany/_graveyard/tools/`
> Retired on: 2026-04-07

## Retirement notes (verbatim from _RETIRED.md)

# Retired 2026-04-07

`src/omnicompany/tools/` contained two standalone CLI utilities with zero
imports anywhere (no entry points in pyproject.toml, no runtime references):
- `cli.py` — text summarization CLI
- `skill_import.py` — skill→omnicompany importer CLI wrapper

These were never wired to the `omni` CLI entry point and no call sites
exercise them. Retired to reduce top-level clutter.

If either is needed again:
- Summarization: make a proper subcommand under `omni` and implement it.
- Skill importer: there's still an `omni run skill-import` pipeline.

## What was lost

_(待补充: 这次退役放弃了什么能力? 是否有替代方案?)_

## Why it failed / was abandoned

_(待补充: 退役的根本原因, 不只是表面的 "no callers")_

## Lessons for future attempts

_(待补充: 如果将来重做类似系统应该注意什么?)_

## Resurrection conditions

_(待补充: 在什么情况下值得复活? 如本次 OmniKB 的复活就是一个例子)_

## Change log

- 2026-04-08 — auto-seeded from `_graveyard/tools/_RETIRED.md`
