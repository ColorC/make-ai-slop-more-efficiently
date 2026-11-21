# [OMNI] origin=claude-code domain=services/code_runtime_test ts=2026-04-26T00:00:00Z type=helper
# [OMNI] material_id="material:utility.runtime_test.code.subprocess_dispatch_helper.py"
"""共享子进程 dispatch helper · 避嵌套 async loop 冲突."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[5]


_RUNNER_SCRIPT = """
import os, sys, json, asyncio
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

target_id = sys.argv[1]
input_path = sys.argv[2]
output_path = sys.argv[3]
max_steps = int(sys.argv[4]) if len(sys.argv) > 4 else 1000

with open(input_path, "r", encoding="utf-8") as f:
    input_data = json.load(f)

from omnicompany.core.dispatch import dispatch
from omnicompany.core.registry import discover
discover()

result = asyncio.run(dispatch(target_id, input_data, max_steps=max_steps))

verdict = "FAIL"
output = {}
diag = ""
if hasattr(result, "kind"):
    verdict = result.kind.value.upper() if hasattr(result.kind, "value") else str(result.kind).upper()
    if hasattr(result, "output") and isinstance(result.output, dict):
        output = result.output
    if hasattr(result, "diagnosis") and result.diagnosis:
        diag = result.diagnosis
elif isinstance(result, dict):
    output = result
    verdict = "PASS"

with open(output_path, "w", encoding="utf-8") as f:
    json.dump({"verdict": verdict, "output": output, "diagnosis": diag}, f, ensure_ascii=False)
"""


def run_target_subprocess(
    target_team_id: str,
    input_data: dict,
    timeout_sec: int = 600,
) -> dict:
    """跑一次 target dispatch · 子进程隔离 · 返 verdict/output/elapsed/diagnosis."""
    t0 = datetime.now()
    with tempfile.NamedTemporaryFile(mode="w", suffix="_in.json", delete=False, encoding="utf-8") as tin:
        json.dump(input_data, tin, ensure_ascii=False)
        in_path = tin.name
    out_path = in_path.replace("_in.json", "_out.json")
    with tempfile.NamedTemporaryFile(mode="w", suffix="_runner.py", delete=False, encoding="utf-8") as ts:
        ts.write(_RUNNER_SCRIPT)
        script_path = ts.name

    env = os.environ.copy()
    env["PYTHONPATH"] = str(_PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [sys.executable, script_path, target_team_id, in_path, out_path, "1000"]

    elapsed = 0.0
    verdict = "FAIL"
    output: dict = {}
    diag = ""

    try:
        proc = subprocess.run(
            cmd, cwd=str(_PROJECT_ROOT), env=env,
            capture_output=True, text=True,
            timeout=timeout_sec, encoding="utf-8", errors="replace",
        )
        elapsed = (datetime.now() - t0).total_seconds()
        if Path(out_path).is_file():
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    parsed = json.load(f)
                if isinstance(parsed, dict):
                    verdict = parsed.get("verdict", "FAIL")
                    output = parsed.get("output", {}) or {}
                    diag = parsed.get("diagnosis", "")
            except Exception as e:
                diag = f"输出 JSON 解析失败: {type(e).__name__}: {e}"
        else:
            diag = f"子进程未产输出 (rc={proc.returncode}; stderr 末段: {(proc.stderr or '')[-500:]})"
    except subprocess.TimeoutExpired:
        elapsed = timeout_sec
        diag = f"子进程超时 ({timeout_sec}s)"
    finally:
        for p in (in_path, out_path, script_path):
            try:
                os.unlink(p)
            except OSError:
                pass

    return {"verdict": verdict, "output": output, "elapsed_sec": elapsed, "diagnosis": diag}


def extract_actual(output: dict, extractor: str | None) -> str:
    """从 output dict 抽出实际 markdown 字符串."""
    if not isinstance(output, dict):
        if isinstance(output, str):
            return output
        return ""
    if extractor:
        return str(output.get(extractor, "") or "")
    # 默认尝试两个常用 key
    for k in ("report_markdown", "markdown", "md", "text"):
        v = output.get(k)
        if isinstance(v, str):
            return v
    # 没找到 → 空
    return ""
