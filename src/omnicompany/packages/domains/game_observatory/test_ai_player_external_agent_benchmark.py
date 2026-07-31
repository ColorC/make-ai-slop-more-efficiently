from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from omnicompany.packages.domains.game_observatory.ai_player.external_agent_benchmark import (
    AFKJExternalAgentBenchmarkRunner,
    B0_QUESTIONS,
    compare_b0_results,
    parse_json_object,
    score_b0_answers,
    score_warm_probe,
)
from omnicompany.packages.domains.game_observatory.ai_player.external_agent_runtime import (
    ContinuousExternalAgentRunner,
    ExternalAgentSessionLedger,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_b0_scoring_is_strict_and_machine_readable() -> None:
    payload = {
        "answers": [
            {"id": item.id, "choice": item.correct_choice} for item in B0_QUESTIONS
        ]
    }
    assert score_b0_answers(payload) == 10
    payload["answers"][0]["choice"] = "D"
    assert score_b0_answers(payload) == 9
    assert parse_json_object("```json\
{\"same_session\":true}\
```") == {
        "same_session": True
    }
    assert (
        score_warm_probe(
            {
                "continuity_token": "abc",
                "same_session": True,
                "would_reload_full_contract": False,
                "next_layer_for_known_action": "A0",
            },
            continuity_token="abc",
        )
        == 4
    )


def test_b0_runner_uses_one_native_session_and_writes_recomputable_result(
    tmp_path: Path,
) -> None:
    provider_script = tmp_path / "provider.py"
    token_path = tmp_path / "token.txt"
    provider_script.write_text(
        """
import json
import re
import sys
from pathlib import Path

operation, session_id, token_path = sys.argv[1:4]
prompt = sys.stdin.read()
token_file = Path(token_path)
if operation == "start":
    token = re.search(r"continuity_token=([0-9a-f]+)", prompt).group(1)
    token_file.write_text(token, encoding="utf-8")
    choices = {
        "Q01":"A","Q02":"B","Q03":"C","Q04":"C","Q05":"B",
        "Q06":"C","Q07":"B","Q08":"B","Q09":"C","Q10":"C"
    }
    message = {"answers":[{"id":key,"choice":value} for key,value in choices.items()],
               "continuity_token":token}
else:
    token = token_file.read_text(encoding="utf-8")
    message = {"continuity_token":token,"same_session":True,
               "would_reload_full_contract":False,
               "next_layer_for_known_action":"A0"}
print(json.dumps({"type":"thread.started","thread_id":session_id}))
print(json.dumps({"type":"turn.started"}))
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(message)}}))
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":4,
                                                       "output_tokens":3,"reasoning_output_tokens":2}}))
""".strip()
        + "\
",
        encoding="utf-8",
    )

    def runner_factory(ledger: ExternalAgentSessionLedger) -> ContinuousExternalAgentRunner:
        runner = ContinuousExternalAgentRunner(ledger)

        def command(session, *, operation, cwd, message_path, provider_session_id):
            del session, cwd, message_path
            return [
                sys.executable,
                str(provider_script),
                operation,
                provider_session_id or "benchmark-native-session",
                str(token_path),
            ]

        runner._build_command = command  # type: ignore[method-assign]
        return runner

    benchmark = AFKJExternalAgentBenchmarkRunner(
        REPOSITORY_ROOT,
        runner_factory=runner_factory,
    )
    result, result_path = asyncio.run(
        benchmark.run_b0(
            candidate_id="gpt-5.6-luna-medium",
            repetition=1,
            timeout_seconds=5,
            output_root=tmp_path / "runs",
            run_id="b0-luna-test",
        )
    )

    assert result.quality_pass is True
    assert result.b0_correct == 10
    assert result.warm_probe_correct == 4
    assert result.same_native_session is True
    assert result.external_session_id == "benchmark-native-session"
    assert [item.operation for item in result.turns] == ["start", "resume"]
    assert result_path.is_file()
    comparison = compare_b0_results([result])
    assert comparison["selection_allowed"] is True
    assert comparison["rows"][0]["b0_accuracy"] == 1.0