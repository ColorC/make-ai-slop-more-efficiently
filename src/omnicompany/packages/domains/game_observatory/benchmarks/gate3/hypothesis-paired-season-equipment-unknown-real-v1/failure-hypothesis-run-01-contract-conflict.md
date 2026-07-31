# Hypothesis run 01 failure: contradictory scene contract

- Benchmark run: `pair.gate3.season-equipment.hypothesis.unknown-real.01`
- Frozen observation: `art.device.1783965463663.7cbf035b`
- Device actions: 0
- Suggestions recorded: 0
- Region inspection records retained: 60
- Artifact activity window: `2026-07-14T12:41:06.2089635+08:00` to `2026-07-14T12:49:32.6885847+08:00`
- Termination: the main agent stopped the exact matching `omni run hypothesis` process tree after the same deterministic contradiction kept recurring.

## Failure

The unknown-real scene enabled both `prior_fact_contract_mode=exact` and an empty `prior_verified_targets` list. Legal new candidates were therefore rejected for missing `prior_fact_id`. The run produced 60 exact-region inspection records and no suggestion ledger.

Observed router error:

```text
既有事实精确模式要求 prior_fact_id；候选未写入账本
```

## Facility change

Shadow session startup now rejects these contradictory contracts before any model call:

- `prior_fact_contract_mode=exact` with no identified prior facts;
- exact prior-fact replay combined with `expected_change_mode=unverified`.

Run 02 keeps the same frozen screenshot, action allowlist, exclusions and suggestion cap, and removes the inapplicable prior-fact replay mode.