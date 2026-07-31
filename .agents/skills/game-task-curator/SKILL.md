---
name: game-task-curator
description: Generate, deduplicate, prioritize, cool down, reactivate, and close canonical AI-player tasks from goals and coverage signals. Use when new evidence arrives, the frontier changes, a skill degrades, a guide becomes stale, or the queue appears empty.
---

# Game Task Curator

Contract version: `1.0.0`.

Operate `FrontierTaskV1`, `TaskBoard`, and `FrontierGenerator` through canonical store methods.

## Curate

1. Collect user goals, unknown interactions, missing transitions, stale states, interface-family gaps, new unlocks, failed skills, gameplay candidates, and coverage gaps. Accept guide updates only when they are current, applicable to this environment, and not contradicted.
2. Merge tasks that share environment, source state, objective, and target region. Preserve all source reasons and evidence.
3. Add dependencies, action/time/token budgets, attempt limits, value, novelty, expected coverage gain, risk, and a plain-language reason.
4. Rank only tasks whose dependencies and cooldowns permit execution. Keep task selection reproducible from scores and blockers.
5. Close a task only from terminal evidence. A failed attempt updates attempts and cooldown or failure state; it never disappears from the ledger.

When no task is ready, run a coverage audit before allowing idle. Report exact blocked reasons and reactivation conditions.

Do not clear the queue to stop a loop. Repeated state-target pairs feed `ActionHistoryGuard` and a different frontier choice.
