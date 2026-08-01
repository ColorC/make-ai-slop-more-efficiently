# Automatic baseline discovery

`session_recovery_cli.py baseline` turns complete file snapshots, Git, the
current worktree, source-like publish/build trees, and materialized local
session ledgers into one newest-to-oldest candidate chain per relative path.
It never executes commands found in session history.

It also makes a bounded, low-priority pass over the most relevant recent raw
provider transcripts to extract only completed `apply_patch` *Delete File*
operations. A later deletion is a tombstone: it prevents an older complete
post-image from being promoted. A delete followed by an add in the same patch
is treated as a replacement, not a tombstone. Shell deletion intent is recorded
by the wider forensic tool but is intentionally not used for automatic removal.

The scan is query-only by default. It lowers its process priority, clamps hash
workers to at most two, excludes caches, `__pycache__`, dependency trees and
data/generated outputs. It also excludes Playwright result directories and
Vite-style content-hashed `static/assets` bundles: those are diagnostic or
derivable deployment products, not source candidates. It stops extending a path's snapshot history after
two consecutive complete snapshots have the same SHA-256. The queues are:

By default, Git, worktree, and publish/build hashing is limited to paths already
discovered from complete manifests or materialized session ledgers. This is the
fast convergence path. Full enumeration is deliberately opt-in with
`--include-unreferenced-worktree`.

- `identical_converged`: current bytes equal the newest complete evidence, or
  independent evidence has converged on the same hash.
- `safe_promote`: the worktree path is missing and the newest complete
  post-image has one unambiguous hash.
- `post_baseline_overlay`: current bytes differ and a post-snapshot Git commit
  or materialized session post-image proves that exact hash. A later worktree
  mtime alone never proves an overlay because copying/restoring refreshes it.
- `conflict_manual`: hashes differ without safe temporal ordering.
- `missing_source`: evidence exists but its complete post-image is unavailable.
- `intentionally_removed`: a newer completed session deletion supersedes an
  older complete file. It is never eligible for promotion.
- `tombstone_conflict_manual`: a newer deletion conflicts with an extant file;
  preserve it and require review.
- `awaiting_tombstone_scan`: the source session is locally available but lies
  outside the bounded raw-session window. It is deliberately withheld from
  automatic promotion until that source has been scanned.
- `module_shadowed_manual`: the missing module has an extant `.ts`/`.tsx` or
  `.js`/`.jsx` sibling; automatic promotion would create ambiguous resolution.
- `ephemeral_evidence`: a one-off Dashboard audit, smoke, probe or visual
  test helper remains in the evidence chain but is excluded from source
  promotion and from the next-batch ranking.

## Query-only example

```powershell
python scripts/session_recovery_cli.py baseline scan `
  --workspace-root C:/workspace/omnicompany `
  --snapshot-prefix 73fd947 `
  --out C:/workspace/omnicompany-recovery-evidence\baseline-73fd947.json
```

Supply additional discovery entry points with repeatable `--snapshot-root`,
`--session-root`, and `--candidate-root`, or a JSON `--config`. A persistent
hash cache is opt-in so query-only scans do not silently write:

The checked-in example is
`config/recovery/baseline-discovery.example.json`; it is described by
`config/recovery/baseline-discovery.schema.json`.

```powershell
python scripts/session_recovery_cli.py baseline scan `
  --workspace-root C:/workspace/omnicompany `
  --config config/recovery/baseline-discovery.example.json `
  --hash-cache C:/workspace/omnicompany-recovery-evidence\hash-cache.json `
  --write-cache --out C:/workspace/omnicompany-recovery-evidence\baseline-plan.json
```

For repeated recovery work, keep the hash cache together with a separately
opt-in transcript tombstone cache. The latter reuses a completed extraction
only when the raw transcript has the same path, size and mtime; it never
replays historical commands.

```powershell
python scripts/session_recovery_cli.py baseline scan `
  --workspace-root C:/workspace/ `
  --path-prefix omnicompany/src/omnicompany/dashboard `
  --hash-cache C:/workspace/omnicompany-recovery-evidence\hash-cache.json `
  --tombstone-cache C:/workspace/omnicompany-recovery-evidence\tombstone-cache.json `
  --ledger-cache C:/workspace/omnicompany-recovery-evidence\ledger-cache.json `
  --progress-file C:/workspace/omnicompany-recovery-evidence\dashboard-progress.json `
  --out C:/workspace/omnicompany-recovery-evidence\dashboard-plan.json
```

`--path-prefix` is repeatable and should be used for normal per-project
recovery batches. The default plan is compact but still sufficient for guarded
promotion; use `--include-chains` only for a forensic review. Recursive
manifest and nested build-tree discovery are likewise opt-in through
`--deep-manifest-search` and `--discover-candidate-roots`.

The default tombstone window inspects eight ledger-prioritized transcripts;
increase `--raw-tombstone-files` for a deliberately broader historical pass,
or use `--no-raw-tombstones` when an entirely metadata-only scan is required.
To resolve a specific `awaiting_tombstone_scan` item without widening the
whole window, pass its exact transcript with repeatable
`--raw-tombstone-include <session.jsonl>`; it is appended to the bounded
recent set rather than replacing it.
An exact `.jsonl` file may be passed to `--session-root` for a fast, auditable
single-session probe. A missing file whose selected baseline comes from a
session is withheld as `awaiting_tombstone_scan` until that source session has
actually been covered; an unscanned session is never auto-promoted merely
because its complete-file ledger exists.

Every plan now also contains a `recovery_efficiency` section. It identifies
the newest fullest manifest cohort and separately labels a cohort `complete`
only when every source candidate is byte-available, reports automatic closure versus paths
that need a new content decision, records when older snapshot candidates were
skipped after hash convergence, and ranks at most twelve next batches by
benefit. The recommendation is calculated from evidence already indexed: it
does not launch another whole-workspace scan. Follow the listed session IDs
only for `awaiting_tombstone_scan`; for `conflict_manual`, run a focused
functional probe first, and only then inspect the exact differing post-image.

## Guarded promotion

Apply is also a dry run unless `--apply` is present. Only `safe_promote`
missing files are eligible. The plan SHA, exact workspace confirmation,
candidate SHA-256, target absence, pre-apply snapshot and write-ahead journal
are all mandatory; existing files are never overwritten.

```powershell
python scripts/session_recovery_cli.py baseline apply `
  --plan C:/workspace/omnicompany-recovery-evidence\baseline-plan.json `
  --workspace-root C:/workspace/omnicompany `
  --confirm-workspace C:/workspace/omnicompany
```

Review the dry run, then repeat with `--apply` only when the frozen plan is
approved.
