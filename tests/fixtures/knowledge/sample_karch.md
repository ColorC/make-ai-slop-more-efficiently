---
omnikb_type: karch
id: kb.arch.sample
name: Sample Architecture Topic
summary: 一个用于测试的架构主题, 仅 fixture.
tags:
  - topic.sample
  - stage.test
maturity: stable
scope: omnicompany
code_anchors:
  - src/omnicompany/packages/services/knowledge/schema.py
  - src/omnicompany/packages/services/knowledge/store.py
related_decisions:
  - kb.decision.sample
related_karchs: []
---

# Sample Architecture Topic

This is a golden fixture for the OmniKB test suite. It exercises the
KArchitectureEntry parser.

## Why

Because tests need at least one canonical example per entry type.

## How it works

The parser reads this file, extracts the YAML frontmatter, matches
`omnikb_type: karch`, and constructs a KArchitectureEntry instance.
