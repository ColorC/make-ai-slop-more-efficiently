---
name: game-skill-crystallizer
description: Turn closed successful transition routes into structured L2-L4 procedural-skill candidates for separate independent validation. Use after a route repeats successfully or when a stable operation may reduce planning tokens and latency.
---

# Game Skill Crystallizer

Contract version: `1.0.0`.

Use `crystallizer.py`, `skills.py`, `skill_runtime.py`, and canonical skill tables.

## Create a candidate

1. Accept only continuous transitions with terminal evidence and verified state change.
2. Remove redundant waits and accidental coordinates while preserving the original target box and fallback locator evidence.
3. Declare typed parameters, exact applicability scope, safety, side effects, preconditions, action/assertion DAG, objective success, failure, recovery, and content hash.
4. Map L2 to one interaction, L3 to one surface flow, and L4 to one gameplay flow. Keep atomic/flow/strategy, scope, execution mode, and perception tier as separate fields.
5. Append status `candidate`. The crystallizer has no promotion authority.

## Hand off validation

Write a validation request for a different actor. This Skill must not execute validation runs, derive the aggregate, promote a candidate, create `independent_review.md`, or sign a gate it generated. The separate validator runs at least 20 independent cases; `skill_validation.py` derives every aggregate from immutable runs, and only `SkillLifecycle.promote_preferred` may append the preferred successor after a passed canonical validation.

On drift or a failed objective, append a degraded or invalidated successor with new evidence and create a failed-skill frontier task. Never silently fall back to an out-of-scope version.
