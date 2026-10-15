# DeepSeek continuous-agent replay contract

Scope: only the twenty cases in `cases.json`; produce one offline decision trace per case. Do not inspect or operate the real game.

Decision rule: comply with every rule in `cases.json`, preserve continuous real-time battle semantics, and keep facility evidence separate from contestant choice.

Deliverable: exactly one JSON object matching `output-schema.json`, with exactly twenty results in input order.

Permissions: read only the attached case fixture and this contract. No workspace writes, network calls, game I/O, screenshots, OCR, or external knowledge lookup.

Forbidden: changing files, expanding scope, inventing candidates, future-stage spoilers, coordinate input, legacy tick/advance, or asking the supervisor a question.

Acceptance: `python scripts/arknights_replay_gate.py cases.json <structured-output.json>` exits 0; query-turn P50 <=4 and P95 <=7; every blind pair keeps the same selected operator; every complete recruit case lists the full candidate set and two alternatives.

Read the attached `cases.json`. Act as the contestant, not the facility. Return exactly one object matching the attached output schema and exactly one result per case in input order.

For every case, list the semantic CLI commands you would issue. A query command reads state/model/options; a progress command is `screen choose`, `battle deploy/skill/retreat/run-until/ensure-paused`, or stopping after a proven battle result. Set `query_turns` to the number of queries before the first progress command.

Never emit screen coordinates, `screen tap`, `screen tap-raw`, `screen tap-element`, `battle tick`, or `battle advance`. `battle deploy` grid coordinates are valid model coordinates and are allowed. Do not name or query future stages/nodes.

Recruit cases must populate all candidate IDs from the state, all material squad gaps, coverage reasoning, selected operator ID, and at least two alternatives with why-not explanations. If the candidate set is incomplete, use `squad candidates`/`recruit status` and refuse a fabricated final selection; still explain what is unknown. Blind-group pairs must make the same selection because their evidence is identical despite name/order masking.

Battle is continuous real time, not turns. Plan while paused, then use `battle run-until` with an event threshold and a bounded deadline. Use forecast/coverage only when they reduce a real decision.
