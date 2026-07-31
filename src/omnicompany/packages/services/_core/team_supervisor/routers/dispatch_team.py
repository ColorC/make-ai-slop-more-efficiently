# [OMNI] origin=claude-code domain=services/team_supervisor/routers ts=2026-04-26T00:00:00Z type=router
# [OMNI] material_id="material:core.team_supervisor.routers.dispatch_team_subprocess.py"
"""DispatchTeamRouter — 让 supervisor 的 TestExecutor 真 dispatch 任意 target team.

为什么走子进程: dispatch 是 async, AgentNodeLoop 运行在 async loop 里, 不能在 _execute()
里直接 asyncio.run (loop already running). 子进程隔离一并解决 SQLiteBus 状态污染.

工具协议:
- Input: {target_team_id, input_data, max_steps?}
- Output: JSON 字符串 {"verdict": "PASS|FAIL|PARTIAL", "output": dict, "diagnosis": str, "stdout": str, "stderr": str}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[6]


_DISPATCH_RUNNER_SCRIPT = """
import os, sys, json, asyncio
from pathlib import Path

# load .env from CWD (CWD set by parent to project root)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

target_id = sys.argv[1]
input_path = sys.argv[2]
output_path = sys.argv[3]
max_steps_str = sys.argv[4] if len(sys.argv) > 4 else "1000"
max_steps = int(max_steps_str)

with open(input_path, "r", encoding="utf-8") as f:
    input_data = json.load(f)

from omnicompany.core.dispatch import dispatch
from omnicompany.core.registry import discover
discover()

result = asyncio.run(dispatch(target_id, input_data, max_steps=max_steps))

verdict_kind = "FAIL"
output_dict = {}
diagnosis = ""
if hasattr(result, "kind"):
    verdict_kind = result.kind.value.upper() if hasattr(result.kind, "value") else str(result.kind).upper()
    if hasattr(result, "output") and isinstance(result.output, dict):
        output_dict = result.output
    if hasattr(result, "diagnosis") and result.diagnosis:
        diagnosis = result.diagnosis
elif isinstance(result, dict):
    output_dict = result
    verdict_kind = "PASS"

with open(output_path, "w", encoding="utf-8") as f:
    json.dump({
        "verdict": verdict_kind,
        "output": output_dict,
        "diagnosis": diagnosis,
    }, f, ensure_ascii=False)
print("dispatch_team_done")
"""


class DispatchTeamRouter(SingleToolRouter):
    """真 dispatch target team · 子进程隔离."""

    TOOL_NAME: ClassVar[str] = "dispatch_team"
    DESCRIPTION: ClassVar[str] = (
        "Dispatch a target team via subprocess and return its verdict + output. "
        "Used by team_supervisor's TestExecutor to actually run target teams "
        "(e.g. 'csv-to-md') and collect real outputs for hypothesis evaluation. "
        "Subprocess isolation avoids async loop conflicts and SQLiteBus state pollution."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "target_team_id": {
                "type": "string",
                "description": "Target team's registered id (e.g. 'csv-to-md')",
                "minLength": 1,
            },
            "input_data": {
                "type": "object",
                "description": "Input data for the target team's entry node",
            },
            "max_steps": {
                "type": "integer",
                "minimum": 1,
                "default": 1000,
                "description": "Max dispatch steps; default 1000 (loose budget)",
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 30,
                "default": 1800,
                "description": "Subprocess timeout; default 30 min",
            },
        },
        "required": ["target_team_id", "input_data"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        target_id = args.get("target_team_id")
        input_data = args.get("input_data") or {}
        max_steps = int(args.get("max_steps") or 1000)
        timeout = int(args.get("timeout_seconds") or 1800)

        if not target_id or not isinstance(target_id, str):
            raise ToolExecutionError("target_team_id required")
        if not isinstance(input_data, dict):
            raise ToolExecutionError("input_data must be a dict")

        # 写 input + 准备 output 路径
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="_input.json", delete=False, encoding="utf-8"
        ) as tin:
            json.dump(input_data, tin, ensure_ascii=False)
            input_path = tin.name
        output_path = input_path.replace("_input.json", "_output.json")

        # 写 runner 脚本
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="_runner.py", delete=False, encoding="utf-8"
        ) as tscript:
            tscript.write(_DISPATCH_RUNNER_SCRIPT)
            script_path = tscript.name

        env = os.environ.copy()
        # 确保子进程能 import omnicompany
        src_path = str(_PROJECT_ROOT / "src")
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            src_path + os.pathsep + existing_pp if existing_pp else src_path
        )

        cmd = [
            sys.executable,
            script_path,
            target_id,
            input_path,
            output_path,
            str(max_steps),
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(_PROJECT_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return json.dumps({
                "verdict": "FAIL",
                "output": {},
                "diagnosis": f"dispatch_team timeout after {timeout}s",
                "stdout": "",
                "stderr": "TIMEOUT",
            }, ensure_ascii=False)
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass

        # 读 output
        result_dict: dict[str, Any] = {
            "verdict": "FAIL",
            "output": {},
            "diagnosis": "",
            "stdout": (proc.stdout or "")[-2000:],
            "stderr": (proc.stderr or "")[-2000:],
        }

        if Path(output_path).is_file():
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    parsed = json.load(f)
                if isinstance(parsed, dict):
                    result_dict["verdict"] = parsed.get("verdict", "FAIL")
                    result_dict["output"] = parsed.get("output", {})
                    result_dict["diagnosis"] = parsed.get("diagnosis", "")
            except Exception as e:
                result_dict["diagnosis"] = (
                    f"failed to parse subprocess output: {type(e).__name__}: {e}"
                )
            finally:
                try:
                    os.unlink(output_path)
                except OSError:
                    pass
        else:
            result_dict["diagnosis"] = (
                f"subprocess produced no output file (rc={proc.returncode})"
            )

        try:
            os.unlink(input_path)
        except OSError:
            pass

        return json.dumps(result_dict, ensure_ascii=False)
