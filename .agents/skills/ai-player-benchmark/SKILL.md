---
name: ai-player-benchmark
description: Run frozen AI-player acceptance scenarios and produce machine-readable results, evidence indexes, and an independent review package. Use for AFK Journey known-truth regression, Sanguo live validation, clean-database reruns, or final gate adjudication.
---

# AI Player Benchmark

Contract version: `1.0.0`.

Read the frozen acceptance manifest before executing. Never change truth, thresholds, budgets, or expected counts after seeing a result.

## Run

1. Verify initializer, validator, cleanup, game/build/environment identity, fixture hashes, code hash, and config hash.
2. Run isolated AFK known-truth fixtures and the authorized Sanguo live or replay scenarios with fresh run ids and evidence.
3. Measure state accuracy, merge/split errors, coverage gain, duplicate and no-change actions, path redundancy, idle time, skill success, false success, safety overrun, recovery, token use, latency, guide freshness, account behavior, and evidence completeness.
4. Inject the required reset, visual variant, unmet precondition, interruption, drift, login, popup, and process-failure cases.
5. Write `results.json`, `AI_PLAYER_ACCEPTANCE_REPORT.md`, traces, `evidence_index.json`, and `review_request.json` under one immutable run directory.

This Skill cannot create or sign `independent_review.md`. A separate reviewer identity samples the evidence and writes that file. The report headline remains FAIL until every AP, P, E2E, and G gate passes, safety violations are zero, the clean-database rerun passes, and the separately authored independent review passes.
