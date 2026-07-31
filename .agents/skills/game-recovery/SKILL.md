---
name: game-recovery
description: Recover AI-player sessions after agent, game, emulator, login, popup, or evidence interruption without replaying uncertain side effects. Use whenever a pending action, stale device lease, identity drift, or interrupted skill is present.
---

# Game Recovery

Contract version: `1.0.0`.

Use `recovery.py`, session control, capsules, action history, and terminal evidence.

## Recover

1. Load the latest capsule and pending action. Verify environment identity and acquire a fresh device lease.
2. Observe the current device before issuing any action. Capture screenshot, UI tree, process state, and identity signals.
3. Decide whether the pending effect is confirmed, failed, or still unknown. Bind the decision to fresh evidence.
4. If confirmed, finalize canonical state, edge, task, memory, and capsule without executor access or another budget charge.
5. If failed, use an applicable verified recovery skill or return to a known state. Retry the original action only after objective evidence proves it did not take effect and its idempotency policy permits retry.
6. If unknown, preserve the pending capsule and stop with an exact observation gap.

Stop immediately on account/build/server drift, irreversible or external side effects, missing evidence files, or a recovery route outside its applicability scope.
