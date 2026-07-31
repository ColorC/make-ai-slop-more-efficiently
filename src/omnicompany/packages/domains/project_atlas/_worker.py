# [OMNI] origin=claude-code domain=project_atlas ts=2026-06-21 type=helper status=active
# [OMNI] summary="调 `omni worker run claude-code`(受审计的带工具 Claude Code 子 worker)的 subprocess 薄封装。"
# [OMNI] why="语义起草必须由带读工具的 agent 实地核实再写(否则结构性幻觉, 见 feedback_agents_need_readonly_tools)。permission/watch 可调, 供 enumerate(列清单) 与 author(写单个 SKILL) 复用。"
# [OMNI] tags=project_atlas,worker,claude-code
"""project_atlas: 委派 omni worker run claude-code(单次)。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ._paths import repo_root


def _omni_exe() -> str:
    root = repo_root()
    cand = root / "venv" / ("Scripts" if os.name == "nt" else "bin") / ("omni.exe" if os.name == "nt" else "omni")
    return str(cand) if cand.exists() else "omni"


def run_claude_worker(*, spec_path: Path, cwd: Path, run_root: Path,
                      permission: str = "workspace-write", watch_rel: str | None = None,
                      timeout_s: float = 900.0) -> dict:
    """前台跑一次 `omni worker run claude-code --json`,返回归一化结果 dict。

    单次只干一件小事(列清单 / 写一个 SKILL)——任务小才不会触发 Plan-mode 之类的交互、也才好续跑。
    stdout 解析尽力而为;最终以落盘产物为准(Collect 节点据 staging/objects.json 实际文件判断进度)。
    """
    run_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        _omni_exe(), "worker", "run", "claude-code",
        "--spec", str(spec_path),
        "--cwd", str(cwd),
        "--permission", permission,
        "--run-root", str(run_root),
        "--timeout", str(int(timeout_s)),
        "--json",
    ]
    if watch_rel:
        cmd[cmd.index("--run-root"):cmd.index("--run-root")] = ["--watch-path", watch_rel]
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s + 180, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timed_out"}
    out = (proc.stdout or "").strip()
    if out:
        try:
            return json.loads(out)
        except Exception:
            i = out.rfind("{")
            if i >= 0:
                try:
                    return json.loads(out[i:])
                except Exception:
                    pass
            return {"status": "parse_error", "stdout_tail": out[-600:], "stderr_tail": (proc.stderr or "")[-300:]}
    return {"status": "no_output", "code": proc.returncode, "stderr_tail": (proc.stderr or "")[-600:]}
