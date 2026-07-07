# [OMNI] origin=claude-code domain=dashboard/controlplane ts=2026-06-24 type=infra status=active
# [OMNI] summary="controlplane/cron.py — 统一定时调度只读端点 + 立即跑。读 .omni/cron/ 任务 + .last_tick.json 心跳 + schtasks 触发器状态, 喂给前端「定时任务」视图; POST 立即跑一个任务(detached, 无窗口)。"
# [OMNI] why="omni cron 体系建好后, 需要一个网页面来看/管这些任务(11 个治理/卫生/资源任务); 这是它的后端数据端点, 与 /api/teams 同模式。"
# [OMNI] tags=dashboard,controlplane,cron,scheduler
"""controlplane/cron.py — 统一定时调度端点.

URL:
    GET  /api/cron             列任务 + 上次心跳 + 触发器是否已装
    POST /api/cron/run/{name}  立即跑一个任务(detached, 无窗口, 不阻塞请求)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._governance import scheduler

cron_router = APIRouter(tags=["cron"])

_TRIGGER = "OmniCronHeartbeat"
# 哪些命令用 LLM(便宜模型)→ 前端标「用LLM」; 其余是确定性代码扫描, 不花钱
_LLM_HINTS = ("commit-run", "decisions-run", "history-run", "docs-timeliness", "plans-run")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: 子进程不弹控制台窗

# 人话详细说明(任务 JSON 里的 description 太简/太黑话, 这里给前端一份能看懂的)
_DETAILS = {
    "gov-plans-daily":
        "每天扫一遍 docs/plans 里新写、还没归类的计划文档,用便宜模型判断它属于哪个项目、起一个中文标题、检查格式。只处理新的,已归类的不动。",
    "gov-docs-refs-daily":
        "每天扫所有规范/计划/报告文档里的链接和行号引用,挑出指向已不存在文件/行的『断链』。纯代码扫描,不调 AI、不花钱。",
    "gov-commit-daily":
        "每天把仓库里堆积、还没提交的 git 改动,让便宜模型分批写好提交信息并本地提交(只 commit 不 push),防止改动越堆越多。",
    "gov-decisions-daily":
        "每天把你在对话里标了『记下来 / 这是个决策』的零散札记,用模型抽成结构化的决策记录存进决策库。没有新札记就空跑。",
    "gov-history-weekly":
        "每周翻最近的对话历史,挖出你反复让 AI 干的同类活、反复纠正的同类问题,沉淀成清单——用来提醒哪些该做成固定能力或写进记忆。",
    "gov-docs-timeliness-weekly":
        "每周让模型读一遍规范/计划/报告,判断哪些已过期、被新版本取代、或自相矛盾,标出来提醒更新。",
    "refs-sync-daily":
        "每天重扫『参考项目』目录和研究记录,重建本地资源索引(有哪些现成工具、已拉的开源仓、资料),免得 AI 找不到已有的东西。纯扫描,不调 AI。",
    "atlas-refresh-monthly":
        "每月把资源中心那 83 个工具说明书(SKILL)重新导给 Claude 和 Codex 两个 AI,并补登新出现的工具。平时很轻(已有的跳过、基本不调 AI);要把所有说明书重新实地核对一遍,得手动跑 `omni atlas refresh --force`。",
    "guard-patrol-daily":
        "每天让 Guardian 用确定性规则扫一遍近期改动,抓违规(文件乱放、缺身份头、超大文件等)写成罚单/日志。3 秒跑完,不调 AI。要 AI 智能复核得手动加 --llm。",
    "guard-zombies-daily":
        "每天扫一遍没退干净的后台进程(开发时起的 uvicorn / http.server 之类残留),列出来好清理。",
    "guard-metadata-weekly":
        "每周统计所有 Format/Router 的描述和标签完整度,给一份质量报告——看代码的文档元数据补得齐不齐。",
    "cron-prune-daily":
        "每天清理定时任务自己的运行历史:删掉超过 30 天的完整输出日志、裁剪 runs.jsonl,防止历史无限占磁盘。",
}


def _omni_exe() -> str:
    cand = Path(sys.executable).with_name("omni.exe")
    if cand.exists():
        return str(cand)
    import shutil
    return shutil.which("omni") or "omni"


def _uses_llm(cmd: str) -> bool:
    return any(h in (cmd or "") for h in _LLM_HINTS)


def _trigger_installed() -> bool:
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", _TRIGGER],
                           capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


@cron_router.get("/cron")
def api_cron():
    """列全部 cron 任务(含标准治理任务) + 上次心跳 + 触发器状态。"""
    scheduler.ensure_governance_tasks()
    tasks = []
    for t in scheduler.load_tasks():
        cmd = (t.get("command") or "").strip()
        prompt = (t.get("prompt") or "").strip()
        tasks.append({
            "name": t.get("name"),
            "schedule": t.get("schedule"),
            "kind": "command" if cmd else ("prompt" if prompt else "?"),
            "command": cmd or prompt,
            "description": t.get("description") or "",
            "detail": _DETAILS.get(t.get("name"), ""),  # 人话详细说明
            "last_run_at": t.get("last_run_at"),
            "due": scheduler.is_due(t),
            "uses_llm": _uses_llm(cmd) if cmd else bool(prompt),  # prompt 型走 worker(LLM)
        })
    tasks.sort(key=lambda x: x["name"] or "")
    last_tick = None
    tp = scheduler.cron_dir() / ".last_tick.json"
    if tp.is_file():
        try:
            last_tick = json.loads(tp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            last_tick = None
    return {
        "tasks": tasks,
        "total": len(tasks),
        "last_tick": last_tick,
        "trigger_installed": _trigger_installed(),
    }


@cron_router.post("/cron/run/{name}")
def api_cron_run(name: str):
    """立即跑一个任务(detached: 起一个无窗口 omni 子进程跑它, 不阻塞请求)。"""
    p = scheduler.cron_dir() / f"{name}.json"
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"no such cron task: {name}")
    try:
        subprocess.Popen(
            [_omni_exe(), "cron", "run", name],
            cwd=str(omni_workspace_root()),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"started": True, "name": name}


@cron_router.get("/cron/history/{name}")
def api_cron_history(name: str, limit: int = 30):
    """读一个任务的运行历史(最近在前: 时间/触发方式/成功否/返回码/preview 尾 + log 指针)。"""
    return {"name": name, "runs": scheduler.read_runs(name, limit=limit)}


@cron_router.get("/cron/log")
def api_cron_log(path: str):
    """读某次运行的完整输出。path 来自 history 记录的 log 字段(形如 runs/<name>/<ts>.log)。"""
    base = scheduler.cron_dir()
    runs_root = (base / "runs").resolve()
    target = (base / path).resolve()
    if not str(target).startswith(str(runs_root)):  # 防路径穿越
        raise HTTPException(status_code=403, detail="path out of runs dir")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="log not found")
    try:
        return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace")}
    except OSError as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
