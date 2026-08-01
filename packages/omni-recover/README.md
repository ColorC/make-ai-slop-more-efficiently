# omni-recover

Alpha tooling for byte-safe recovery from Codex, Claude Code, Kimi Code,
OpenCode, and third-party AI coding-session evidence.

> **Alpha safety notice:** session transcripts and recovery archives can contain
> source code, prompts, local paths, and tool output. Archives are
> content-addressed but are **not encrypted by omni-recover**. Store them on an
> access-controlled, encrypted volume and never publish an archive or index
> without reviewing it. Credential files are excluded by the built-in provider
> manifests, but that is not a substitute for handling the archive as sensitive.

The default workflow is read-only:

```powershell
omni-recover sources
omni-recover archive create --provider codex --provider claude --dry-run
omni-recover index build --source codex=C:\path\to\sessions --out recovered-index
omni-recover plan --index recovered-index --workspace-root C:\workspace --out plan.json
omni-recover snapshot create --plan plan.json --out preapply-snapshot
omni-recover apply --plan plan.json --snapshot preapply-snapshot --confirm-workspace C:\workspace
```

The last command is also a dry-run until `--apply` is supplied. Existing files,
conflicting Reads, deletes, moves, and path escapes are isolated by default.
Historical shell commands are never replayed.

Only missing files and quarantined pathless artifacts are writable in this
alpha. Existing files, deletes, moves, and ambiguous candidates remain review
items. Always inspect the dry-run, keep the pre-apply snapshot and journal, and
run project-specific build/UI/data checks after byte recovery.

Provider archive locations can be extended with JSON manifests; parser packages
register `ProviderAdapter` factories through the `omni_recover.providers` entry
point. See the repository's unified evidence contract and recovery Skill for
the byte/image/timeline and project-probe boundaries.
