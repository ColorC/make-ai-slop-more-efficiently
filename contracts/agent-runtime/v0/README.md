# Omni AgentRuntime protocol v0

This directory is the language-neutral authority between OmniCompany and an
agent harness.  It deliberately contains no Python dataclass, Cordis service,
DeepSeek Harness session event, or provider SDK type.

`agent-runtime.schema.json` validates every message crossing the boundary.
Bindings may expose native types for ergonomics, but they must validate at the
boundary and may not add semantics that are absent from this schema.

## Ownership

- OmniCompany owns task, team, material, decision, identity, capability grant,
workspace lease, approval, and domain-event semantics.
- A runtime implementation owns its private loop, session log, model request,
  tool pipeline, and private telemetry.
- Only normalized `run.*` messages cross this boundary.  A DSH `SessionEvent`
is never a protocol message and must not be copied into DomainEventLog.

`CapabilityGrant.allowed_material_refs` is the explicit authority for runtime
Material reads. It is optional for compatibility with pre-tool v0 clients, but
an absent field grants no Material access; selected context references are not
an implicit permission grant.

Optional `RunInput.instructions` is trusted execution policy selected by the
Omni node binding. It remains optional for v0.1 compatibility; new Team
bindings must set it explicitly. `RunInput.text` is the business input, while `context.inline_context`
is reference data and must never be promoted to instructions. When
`expected_output_schema` is present, the runtime must parse exactly one JSON
value, validate it before emitting normalized output, and return it as
`final_output.structured`. Invalid model output fails with `invalid_output`;
it must not silently fall back to another runtime.

## Versioning

`protocol_version` is currently `0.1.0`.  A peer must reject a version it does
not explicitly support.  Additive metadata belongs under `metadata`; contract
objects otherwise reject unknown fields so vendor types cannot leak silently.

## Fixtures

- `fixtures/valid/` contains messages every implementation must accept.
- `fixtures/invalid/` contains counterexamples every implementation must reject.

Both the Python contract test and the TypeScript runtime package consume these
same files.
