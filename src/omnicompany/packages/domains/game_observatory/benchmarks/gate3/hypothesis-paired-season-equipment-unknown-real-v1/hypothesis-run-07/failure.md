# Hypothesis run07 failure — inspected-region ID propagation

- Session: `95b0979d-97c4-4074-9eba-dacb039792cd`
- Frozen observation: `art.device.1783965463663.7cbf035b`
- Observation SHA-256: `731631cf91bdde8a3593f309256147d375657ed85d9c5cf3d0936e5595a96ba0`
- Result: completion contract failed; `0` suggestions entered the ledger.
- Elapsed: `498.648177` seconds.

## Observed failure

The first pass inspected 18 regions and then attempted four grounded proposals. Each proposal supplied the matching inspection identifier in `evidence_ids`, but omitted the dedicated `region_inspection_id` field. The runtime rejected all four; the proposal tool fuse opened after four consecutive errors. The validation retry spent the remaining six region-inspection calls and still produced no ledger entry.

The new provenance and naming guards did work: the attempted proposals referenced locator candidate IDs and used visual-neutral names. The remaining failure was mechanical propagation between two tool fields after the model had already viewed the exact crop.

## Runtime correction under test

When `region_inspection_id` is omitted, the proposal router may recover it only from current-session inspections whose source-pixel `target_bounds` exactly equal the proposal bounds. Every exact duplicate must have the same non-empty rendered-crop SHA-256, and the selected inspection path must already be present in the model's delivered-image set. The latest delivered identical inspection is recorded in the generator metadata. Missing matches, undelivered crops, and same-bounds/different-hash ambiguity remain hard failures.

Run08 reuses the same frozen input, budgets, locator manifest, safety boundaries, and lack of manual-candidate access.