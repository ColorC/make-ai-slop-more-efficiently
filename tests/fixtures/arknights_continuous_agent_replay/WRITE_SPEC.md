# DeepSeek continuous-agent replay correction contract

Scope: only the twenty cases in `cases.json`; produce one offline decision trace per case. Do not inspect or operate the real game.

Decision rule: comply with every rule in `cases.json`, preserve continuous real-time battle semantics, and keep facility evidence separate from contestant choice.

Deliverable: write exactly one UTF-8 JSON file at `var/runtime/arknights/facility-review/p4-deepseek-replay/result.json`. It must match `output-schema.json`, contain exactly twenty results in input order, and contain no Markdown fence or prose.

Permissions: read this contract, `cases.json`, `output-schema.json`, and `scripts/arknights_replay_gate.py`. Write only the one deliverable path above. You may run read-only validation commands against those files.

Forbidden: modifying any source, fixture, schema or gate; expanding scope; network calls; game I/O; screenshots/OCR; external knowledge lookup; inventing candidates; future-stage spoilers; coordinate input; legacy tick/advance; or asking the supervisor a question.

Acceptance: before finishing, run both JSON parsing and `python scripts/arknights_replay_gate.py tests/fixtures/arknights_continuous_agent_replay/cases.json var/runtime/arknights/facility-review/p4-deepseek-replay/result.json`. The gate must exit 0 with query-turn P50 <=4 and P95 <=7; every blind pair keeps the same selected operator; every complete recruit case lists the full candidate set and two alternatives.

Previous attempt feedback: the reasoning was useful, but the returned JSON was syntactically invalid because a comma was missing after the `commands` array for `menu-03`. Do not copy an unparsed response. Write the file, parse it, and run the real gate.
