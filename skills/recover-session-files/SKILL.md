---
name: recover-session-files
description: Archive, normalize, plan, restore, roll back, and validate files from Codex, Claude Code, Kimi Code, OpenCode, and third-party provider evidence.
---

# Recover session files

Use `omni-recover` (or `python scripts/recover.py` in a checkout). Never execute
historical commands recovered from a session.

## Safe workflow

1. `sources` and `archive create --dry-run`; preserve source bytes before parsing.
2. `index build --source PROVIDER=PATH ... --out INDEX_DIR`.
3. `plan --index INDEX_DIR --workspace-root ROOT --out PLAN.json`.
4. Review `conflict-isolated`, destructive evidence, Read disagreements, pathless
   artifacts, and every read-fallback action.
5. `snapshot create --plan PLAN.json --out SNAPSHOT_DIR`, then `snapshot verify`.
6. Run `apply` without `--apply`; only after review pass the exact
   `--confirm-workspace`, snapshot, and `--apply`.
7. Run a versioned project probe with `probe --spec ...`. The probe file belongs
   to the project; use argv arrays, not shell strings.
8. On a failed or interrupted apply, inspect the journal and run `rollback`
   dry-run before `rollback --apply`.

## Evidence semantics

- Mutation post-images outrank Reads even when a Read timestamp is later.
- A differing later Read blocks automatic recovery; it never silently wins.
- An exact Read may restore a missing file only when no mutation post-image exists.
- Exact pathless attachments go to quarantine. Resized images/OCR/descriptions
  are derivatives and never overwrite an original.
- Empty and binary files are bytes, not truthy/falsy text.
- Existing conflicts, deletes, moves, reparse escapes, and changed guards are
  isolated by default.

## Provider extensions

For archive-only support, add a JSON manifest under
`config/recovery/providers.d` or set `OMNI_RECOVERY_PROVIDER_CONFIG`. For parsing,
implement `omnicompany.packages.services.recovery.providers.ProviderAdapter` and register it under
the `omni_recover.providers` Python entry-point group. Add a redacted native-shape
fixture proving text, empty, binary/image, sequence, Read precedence, destructive
blocking, and unknown-envelope quarantine.

## Responsibility split

The generic facility proves bytes, ordering, containment, snapshots, journals,
rollback, and deterministic test execution. It cannot decide UI or business
semantics. After facility probes, the project AI/Skill must select and interpret
unit/build/E2E/live checks and investigate remaining conflicts. Never call a
green byte probe full product acceptance.
