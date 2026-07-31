# [OMNI] origin=claude-code domain=project_atlas ts=2026-06-21 type=helper status=active
# [OMNI] summary="调 `omni worker run <provider>`(受审计的带工具 agent)的 subprocess 薄封装。"
# [OMNI] why="语义起草必须由带读工具的 agent 实地核实再写；provider 可选且运行中有 heartbeat，避免硬编码单一 provider 后无输出长挂。"
# [OMNI] tags=project_atlas,worker,external-agent
"""project_atlas: 委派受审计的外部工具型 worker。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ._paths import repo_root


def _omni_exe() -> str:
    root = repo_root()
    cand = root / "venv" / ("Scripts" if os.name == "nt" else "bin") / ("omni.exe" if os.name == "nt" else "omni")
    return str(cand) if cand.exists() else "omni"


SUPPORTED_PROVIDERS = ("codex", "claude-code")


def _worker_command(
    *,
    provider: str,
    spec_path: Path,
    cwd: Path,
    run_root: Path,
    permission: str,
    watch_rel: str | None,
    timeout_s: float,
) -> list[str]:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported Atlas worker provider: {provider}")
    cmd = [
        _omni_exe(), "worker", "run", provider,
        "--spec", str(spec_path),
        "--cwd", str(cwd),
        "--permission", permission,
        "--run-root", str(run_root),
        "--timeout", str(int(timeout_s)),
        "--json",
    ]
    if provider == "codex":
        cmd.extend(["--model-policy", "cheap"])
    if watch_rel:
        cmd[cmd.index("--run-root"):cmd.index("--run-root")] = ["--watch-path", watch_rel]
    return cmd


def run_tool_worker(*, spec_path: Path, cwd: Path, run_root: Path,
                    provider: str = "codex", permission: str = "workspace-write",
                    watch_rel: str | None = None, timeout_s: float = 900.0,
                    heartbeat_s: float = 15.0) -> dict:
    """前台跑一次 `omni worker run <provider> --json`,返回归一化结果 dict。

    单次只干一件小事(列清单 / 写一个 SKILL)——任务小才不会触发 Plan-mode 之类的交互、也才好续跑。
    每 `heartbeat_s` 秒向 stderr 报一次存活和耗时；最终仍以落盘产物为准。
    """
    run_root.mkdir(parents=True, exist_ok=True)
    cmd = _worker_command(
        provider=provider,
        spec_path=spec_path,
        cwd=cwd,
        run_root=run_root,
        permission=permission,
        watch_rel=watch_rel,
        timeout_s=timeout_s,
    )
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    started = time.monotonic()
    deadline = started + timeout_s + 180
    next_heartbeat = started + heartbeat_s
    with tempfile.TemporaryDirectory(prefix="atlas-worker-") as tmp:
        stdout_path = Path(tmp) / "stdout.txt"
        stderr_path = Path(tmp) / "stderr.txt"
        with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_file:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            while proc.poll() is None:
                now = time.monotonic()
                if now >= deadline:
                    proc.kill()
                    proc.wait()
                    return {"status": "timed_out", "provider": provider}
                if now >= next_heartbeat:
                    elapsed = int(now - started)
                    print(
                        f"[project_atlas] worker provider={provider} running elapsed={elapsed}s "
                        f"spec={spec_path.name}",
                        file=sys.stderr,
                        flush=True,
                    )
                    next_heartbeat = now + heartbeat_s
                time.sleep(min(1.0, max(0.05, heartbeat_s)))
        out = stdout_path.read_text(encoding="utf-8", errors="replace").strip()
        err = stderr_path.read_text(encoding="utf-8", errors="replace")
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
            return {"status": "parse_error", "stdout_tail": out[-600:], "stderr_tail": err[-300:]}
    return {"status": "no_output", "code": proc.returncode, "stderr_tail": err[-600:]}
