# Four-provider live recovery contract — 2026-08-01

## Result

Codex CLI 0.146.0, Claude Code 2.1.220, Kimi Code 0.29.0, and OpenCode
1.18.10 were each asked in a fresh isolated workspace to create one non-empty
text file, one exact zero-byte file, and then read the non-empty file. The
files were indexed from the providers' persisted native session stores, deleted
from disk, restored through the public recovery command chain, hash-probed,
rolled back, restored again, and hash-probed again.

All eight files were restored byte-for-byte both times. The rollback removed all
eight journaled files and did not touch anything outside the isolated workspace.

## Command chain exercised

```text
provider native Write/patch + Read
  -> index build (session and path allow-lists)
  -> content-addressed evidence blobs
  -> frozen plan
  -> verified pre-apply snapshot
  -> dry-run
  -> apply missing files
  -> 8 SHA-256 probes
  -> rollback dry-run
  -> rollback apply
  -> second snapshot/apply
  -> 8 SHA-256 probes
```

The normalized live index contained 9 evidence records from all four providers,
5 deduplicated content blobs, and 58 raw content bytes. The frozen plan contained
8 `restore-missing-mutation` actions.

## Recovered bytes

| Provider | Non-empty bytes | Empty bytes | Result |
|---|---:|---:|---|
| Codex | 14 | 0 | exact SHA match |
| Claude Code | 15 | 0 | exact SHA match |
| Kimi Code | 12 | 0 | exact SHA match |
| OpenCode | 17 | 0 | exact SHA match |

Kimi was asked to append a newline but its actual Write call supplied 12 bytes
without one. The recovery system restored the 12 bytes the tool really wrote,
not the assistant's later claim that a newline existed. Semantic intent and
byte recovery are deliberately separate.

Claude used `qwen3.7-max` for this run. Kimi's default membership endpoint
returned HTTP 402, so the run used an already configured OpenAI-compatible
provider/model alias; no credentials were printed or copied.

## Bugs found by the live run

1. Claude relative `file_path` values must resolve against each transcript
   record's `cwd`, not the recovery process cwd.
2. Opening the active OpenCode database with SQLite `immutable=1` ignores its
   live WAL and silently loses the newest session. The adapter now opens the
   database with read-only `mode=ro`, which lets SQLite merge the existing WAL
   consistently without permitting writes.
3. `allowedTools` did not prevent Claude from using Bash for extra byte checks.
   Those shell calls remain intent/evidence only and are never replayed.

## Deterministic fixture coverage

The committed fixture suite additionally covers:

- latest mutation across multiple writes and providers;
- equal-time independent mutations with different bytes;
- later exact Read agreement and disagreement;
- Read-only missing-file fallback;
- exact PNG/binary recovery and pathless attachment quarantine;
- empty-file identity;
- delete-after-candidate blocking and shell intent non-execution;
- snapshot/apply failpoints before and after journal/replace boundaries;
- rollback of a crash after bytes were replaced but before the result journal;
- local provider manifest extension and the full public CLI chain.

These tests prove recovery mechanics and byte identity. They do not replace a
project's build, unit, E2E, live, UI, data, or human semantic acceptance probes.

## Package verification

The original `omni-recover 0.1.0a1` proof was superseded after the guarded
baseline scanner gained Git-blob materialization, Windows clean-filter and path
identity handling, and content-addressed post-image freezing. Release candidate
`0.1.0a2` is therefore a new version rather than a replacement artifact.

`0.1.0a2` is built both directly and by the standard sdist -> wheel chain. The
two wheels must be byte-identical. The wheel is installed with only its declared
PyYAML dependency into a clean virtual environment; `omni-recover --help`,
`omni-recover sources`, `omni-recover providers`, and `pip check` must succeed.
The wheel inventory is checked for session stores, data, runtime output, Git
metadata, and recovery archives before release. Exact candidate sizes and
SHA-256 values are recorded from the final build, not copied from an older
alpha.

Final local release candidate:

- wheel: `omni_recover-0.1.0a2-py3-none-any.whl`, 62,934 bytes, SHA-256
  `6f0ae75fd9c035ddc5c2396a0767d680a88c617bce70491971917093a56874cf`;
- sdist: `omni_recover-0.1.0a2.tar.gz`, 58,464 bytes, SHA-256
  `f5984899f108459c60990a45620353da61583aeef726e065c5bb64dbe969bb36`.

The direct monorepo wheel and the wheel rebuilt from that sdist have the same
SHA-256. These are local release candidates only; no package registry upload is
implied by this contract.

Recovery archives are content-addressed but not encrypted by omni-recover.
They must remain on an access-controlled encrypted volume and must never be
published without a separate content review.
