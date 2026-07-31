---
name: session-self-bind
description: Bind an active Claude Code or Codex session to its Omnicompany plan, project, task, and topic, then leave progress and decision records through the existing CLI. Use when starting, resuming, or switching substantive work in this repository, or when the dashboard cannot tell what a native session is advancing.
---

# Session Self Bind

Use the existing identity ledger; do not create another session store. Read the authoritative design only when details are needed:

- [`docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md`](../../../docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md)

## Bind the session

1. Inspect current identity with `omni who --json` and identify the real plan/task from the user's work. Do not guess an unrelated plan. When a task was named, check it before binding:

   ```powershell
   omni task show <task-ref> --plan <plan-id> --json
   ```

   Do not restart a done/cancelled task or silently work through a blocked task; surface that state first.
   - If the ledger is already authoritatively bound to a different plan/task, do not silently overwrite it. Switch only when the user clearly named the new target; otherwise ask which workstream is current.
2. Bind only known fields; omitted fields are preserved:

   ```powershell
   omni session bind --provider codex --plan <plan-id> --task <task-id> --project <project> --topic "<one-line topic>"
   ```

   Use `--provider claude_code` in a Claude Code session. Plan-only work may omit `--task`; unplanned work may bind only project/topic.
3. Verify with `omni who --json`. Capture the canonical task id shown there; use that id, not an assumed short number, for subsequent task updates.

## Leave durable records

- At meaningful milestones, record either the plan timeline or bound task:

  ```powershell
  omni progress add plan <plan-id> "<what changed and what remains>"
  omni task update <canonical-task-id> --plan <plan-id> --note "<files, checks, result, next step>"
  ```

- Record consequential choices, not routine implementation chatter. Include accepted/rejected alternatives for a real decision:

  ```powershell
  omni decisions record --kind decision --statement "<decision>" --choose "<choice>:<reason>" --reject "<alternative>:<reason>" --rationale "<why>" --project <project> --track "plan:<plan-id>" --channel codex --authority <authority>
  ```

  Allowed authority values are `user_explicit`, `high`, `medium`, `low`, `derived`, and `unknown`. Use `user_explicit` only for a choice the user actually made; use `derived` for an agent inference, or an evidence-strength value when that is the intended provenance. The command automatically links the record to the current session; `--track` links it to the plan.

- At the review boundary, submit the actual artifact with all required routing fields:

  ```powershell
  omni review submit --kind <kind> --tier <tier> --title "<title>" --plan-id <plan-id> --file <path> --project <project> --track <track> --version <n>
  ```

Hooks provide automatic capture and reminders, but an explicit binding is authoritative and improves dashboard plan/project/task aggregation.
