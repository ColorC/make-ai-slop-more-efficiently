---
omnikb_type: kexp
id: kb.experiment.sample
name: Sample Experiment
summary: 一个用于测试的实验记录, 仅 fixture.
tags:
  - topic.sample
maturity: living
date_started: "2026-04-09"
date_concluded: ""
hypothesis: "golden fixtures are sufficient to catch schema regressions"
method_summary: "parse each fixture file and assert type + id"
samples_run:
  - name: karch
    outcome: "ok"
  - name: kdec
    outcome: "ok"
findings_summary:
  - "parser handles all 4 new types correctly"
status: "in progress"
followups:
  - "add parser stress test after writing Routers"
related_karchs: []
related_decisions:
  - kb.decision.sample
---

# Sample Experiment

## Hypothesis

若每种类型都有合法 fixture, parse_kb_document 可以被测试覆盖 100%。

## Method

1. 写 4 份 fixture (karch/kdec/kexp/krepo)
2. 跑 test 解析每份
3. 检查 type/id/特有字段
