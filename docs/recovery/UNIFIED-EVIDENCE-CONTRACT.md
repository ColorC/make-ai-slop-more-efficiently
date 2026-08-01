# Unified AI-session recovery evidence contract

## Boundary

The recovery engine reconstructs bytes and provenance. It does not decide that a
recovered application is semantically correct. Project-owned probes and the
project's AI/Skill perform that review after the byte-safe recovery phase.

Historical Bash, PowerShell, Python, Node, formatter, delete, move, and checkout
commands are evidence only. They are never replayed.

## Evidence classes

| Authority | Exact bytes | Trusted workspace path | May supersede a mutation | Recovery behavior |
|---|---:|---:|---:|---|
| `mutation-postimage` | yes, including zero bytes | yes | yes, by provider sequence/causality/time | normal candidate |
| `proved-command-postimage` | yes | yes | yes | normal candidate; the command itself is not run |
| `read-exact` | yes | yes | no | missing-file fallback only when no mutation exists |
| `attachment-exact-path` | yes | yes | no | same as an exact Read |
| `artifact-exact` | yes | no | no | quarantine under provider/session; never guess a workspace path |
| `derivative-artifact` | exact derivative only | no | no | preserve screenshot/preview/OCR artifact; never claim it is the original |
| `delete-intent` / `move-intent` | no | possibly | blocks automation | conflict isolation |
| `intent-only` / `unknown` | no | possibly | no | investigation lead only |

An image is therefore recoverable in three different senses:

1. Original bytes plus an exact path, for example a byte-mode Read or an
   original-detail image read: missing-file fallback.
2. Original attachment bytes without a trustworthy workspace path: recover to
   artifact quarantine.
3. Resized preview, screenshot, OCR, or textual description: recover only that
   derivative, never synthesize or overwrite the alleged original.

Empty files are represented by a present base64 payload whose decoded length is
zero. They must not be confused with absent content.

## Timeline rules

1. Inside one provider session, provider sequence wins over timestamps.
2. Explicit causal-parent edges win next.
3. Across independent sessions, normalized UTC time is used.
4. Equal-time independent mutation post-images with different hashes are a
   conflict, not an arbitrary tie-break.
5. A later Read that equals the mutation winner corroborates it. A later Read
   that differs blocks automatic recovery but never becomes the winner.
6. A later delete or move blocks automatic recovery.

## Extension boundary

Archive-only source extensions are JSON files in
`config/recovery/providers.d`. Parser extensions implement the read-only
`ProviderAdapter` protocol and register a factory in the Python entry-point group
`omni_recover.providers`. Adapters emit normalized `EvidenceRecord` objects;
provider-specific ranking is forbidden.

This lets independent developers add formats without editing the planner,
snapshot, apply, rollback, or probe implementations.

## Facility versus project AI

The facility guarantees:

- archive/index/plan/candidate hashes;
- byte identity for text, empty, binary, and image files;
- mutation/read/artifact precedence;
- path containment and no overwrite by default;
- pre-apply snapshot, write-ahead journal, crash inspection, and rollback guards;
- deterministic execution of versioned byte/build/test probes.

The project AI/Skill owns:

- selecting relevant build, unit, E2E, and live probes;
- interpreting UI, behavior, data meaning, and cross-file semantic consistency;
- investigating blocked conflicts and unknown provider envelopes;
- deciding whether any existing file may be overwritten in a future explicitly
  authorized recovery phase.

Passing facility tests means the requested bytes were reconstructed safely. It
does not by itself mean the recovered product is semantically complete.
