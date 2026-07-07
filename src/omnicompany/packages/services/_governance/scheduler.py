# [OMNI] origin=claude-code domain=services/_governance ts=2026-06-13T10:20:00Z type=router
# [OMNI] material_id="material:governance.scheduler.cron_tick_runner.py"
"""治理定时 runner — 让 .omni/cron/ 里的治理任务真正会跑(治本"想起来用")。

背景: ScheduleCronRouter 只**写** .omni/cron/<name>.json 任务定义, 仓里此前**没有执行消费者**
(sentinel 不跑它们), 任务是惰性的。本模块补上最小 runner:

  omni governance cron-tick   # 读全部 cron 任务, 跑到期的, 更新 last_run_at

由**一个**外部触发器(OS cron / Windows 任务计划 / sentinel)每隔几分钟调一次 cron-tick,
它就把所有到期的治理任务(每日 plans-run/docs-refs、每周 history-run/docs-timeliness、提交)分发掉。
schedulable 的根本不需要人"想起来"。

到期判定走 cadence 区间(@hourly/@daily/@weekly/@monthly): last_run_at 为空或已过区间即到期。
原始 5 段 cron 表达式保守按每日处理(治理用 preset 即可, 不引入完整 cron 解析)。

── 工作量触发字段(2026-07-03, 锚: docs/plans/[2026-07-02]SEMANTIC-OS-MAP/overnight-run.md
   第六节工作项3"工作里程表") ──────────────────────────────────────────────
真正容易腐化的是工作时间尺度, 不是自然时间尺度(实证: 资源中心月度重采十天没干活它没错过什么,
一夜高强度工作却坏了半个技能库, 却要等下个月才醒)。任务登记文件(.omni/cron/<name>.json)
可选新增字段:

  "work_trigger": {"metric": "commits", "every": 30, "min_interval_hours": 24}

- metric: 计量名, 见 work_meter.METRIC_NAMES(commits/ledger_events/llm_calls/sessions),
  全部零模型/毫秒级本地读数, 源缺失按 0 计不崩。
- every: 计量值相对水位线的增量达到多少就到期。
- min_interval_hours: 两次工作量触发之间的最小间隔(工作爆发时防连环触发)。

有此字段的任务, 原 schedule 字段**降级为最长间隔兜底**——工作量长期不达标时, 到了 schedule
周期(如 @monthly)仍会兜底跑一次, 避免长期零工作量时永不刷新。水位线与上次触发时间存同一
任务文件的 work_trigger_state 字段(仅在实际触发时推进, 跳过判断绝不推进):

  "work_trigger_state": {"baseline": 135, "last_triggered_at": "2026-07-03T12:00:00+00:00"}

判断入口 is_work_due(task, current_value, now) 是纯比较(不做 IO), tick() 内对整个"读计量
+ 判断 + 写水位线"包一层 try/except——任何异常(畸形阈值/计量源出错等)静默降级回原 is_due()
的自然时间逻辑, 不抛异常, 也不影响同一轮里其他任务的判断与执行。
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._governance.work_meter import read_metric

# Windows 后台子进程标志:
#  - CREATE_NO_WINDOW: 不弹控制台窗口(交互式 schtasks 心跳里跑 shell 命令会闪 cmd/conhost,抢前台焦点)。
#  - BELOW_NORMAL_PRIORITY_CLASS: 治理批处理(如 semantic sweep 可跑 20+min)降优先级,
#    让出 CPU 给交互编辑器,避免把 VSCode 扩展宿主压到无响应(断连根因)。子进程默认继承该优先级。
# 非 Windows 取 0(无影响)。
_BG_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def run_command_capped(cmd: str, timeout_s: int = 1800) -> subprocess.CompletedProcess:
    """subprocess.run(shell=True, timeout=...) 的 Windows 安全替身: 超时杀整棵进程树。

    直接用 subprocess.run 的坑: 超时只杀直接子进程(cmd.exe), 孙进程(真正的 omni 管线)
    握着 stdout/stderr 管道不死 → run() 内部的二次 communicate() 等 EOF 永远等不到 →
    整个心跳挂死(2026-07-02 实况: gov-history-weekly 挂住, tick 从 06-30 堵到 07-02)。
    """
    proc = subprocess.Popen(cmd, shell=True, cwd=str(omni_workspace_root()),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=_BG_FLAGS)
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return subprocess.CompletedProcess(cmd, proc.returncode, out or "", err or "")
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=_BG_FLAGS, timeout=30)
        else:
            proc.kill()
        try:
            out, err = proc.communicate(timeout=15)
        except Exception:  # noqa: BLE001  # 树杀后仍有脱管进程占管道 → 弃管道保心跳
            out, err = "", ""
        return subprocess.CompletedProcess(
            cmd, -9, out or "",
            (err or "") + f"\n[cron] 超时 {timeout_s}s, 已杀进程树(taskkill /T)")

_CADENCE_SECONDS = {
    "@every15m": 900,  # 2026-07-03 批3: 笔记消费任务水位线级扫描(纯确定性, 无变更零 LLM)
    "@hourly": 3600,
    "@daily": 86400,
    "@weekly": 604800,
    "@monthly": 2592000,
    "@yearly": 31536000,
}

# 本部门标准治理任务(ensure 时若缺则建)
_GOVERNANCE_TASKS = [
    {"name": "gov-plans-daily", "schedule": "@daily",
     "command": "omni governance plans-run --only-missing",
     "description": "每日: 新计划归属 + 中文标题 + 格式检查"},
    {"name": "gov-docs-refs-daily", "schedule": "@daily",
     "command": "omni governance docs-refs",
     "description": "每日: 文档引用完整性(断链/失效行锚, 确定性)"},
    {"name": "gov-commit-daily", "schedule": "@daily",
     "command": "omni governance commit-run --apply --model qwen3.6-plus",
     "description": "每日: 性价比模型严格分批提交(防 git 改动堆积; 默认 deepseek 对 commit-run 返空 JSON, 固定 qwen3.6-plus)"},
    {"name": "gov-decisions-daily", "schedule": "@daily",
     "command": "omni governance decisions-run",
     "description": "每日: 标记 llm_input 的札记 → 结构化决策(进总控 ctx)"},
    {"name": "gov-history-weekly", "schedule": "@weekly",
     "command": "omni governance history-run",
     "description": "每周: 对话重复需求/指正挖掘"},
    {"name": "gov-docs-timeliness-weekly", "schedule": "@weekly",
     "command": "omni governance docs-timeliness",
     "description": "每周: 规范/计划/报告时效性(过期/被取代/冲突)"},
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def cron_dir() -> Path:
    d = omni_workspace_root() / ".omni" / "cron"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_tasks() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in sorted(cron_dir().glob("*.json")):
        if f.name.startswith("."):
            continue  # 跳过内部状态/锁文件(.last_tick.json 等), 否则会被当成幽灵任务
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


# ── 运行历史(append-only) ────────────────────────────────────────────

def runs_log() -> Path:
    return cron_dir() / "runs.jsonl"


def runs_dir() -> Path:
    d = cron_dir() / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def record_run(name: str | None, *, ok: bool, trigger: str,
               returncode: int | None = None, output: str = "") -> None:
    """记一条运行历史: 摘要进 runs.jsonl(preview 尾巴 + log 指针), **完整输出**落
    runs/<name>/<ts>.log(全量保留, 按时间 prune_runs 清理)。trigger: scheduled(心跳/tick) | manual。"""
    if not name:
        return
    ts = _now()
    rec: dict[str, Any] = {"name": name, "ts": ts.isoformat(), "trigger": trigger, "ok": bool(ok)}
    if returncode is not None:
        rec["returncode"] = returncode
    output = output or ""
    if output.strip():
        rec["preview"] = output.strip()[-300:]  # 列表里的预览尾巴
        try:  # 全量落单独文件
            logdir = runs_dir() / name
            logdir.mkdir(parents=True, exist_ok=True)
            stamp = ts.strftime("%Y%m%dT%H%M%S%f")
            (logdir / f"{stamp}.log").write_text(output, encoding="utf-8")
            rec["log"] = f"runs/{name}/{stamp}.log"
        except OSError:
            pass
    try:
        with runs_log().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _prune_patrol_logs(days: int) -> int:
    """清 logs/patrol/ 下 > days 天的 patrol-*.json(patrol 提到心跳级 @every15m 后,
    一天最多几十份, 与 runs/ 用同款按 mtime 清理机制, 纳入每日 cron-prune-daily)。"""
    from datetime import timedelta
    cutoff = _now() - timedelta(days=days)
    deleted = 0
    pd = omni_workspace_root() / "logs" / "patrol"
    if not pd.is_dir():
        return 0
    for fp in pd.glob("patrol-*.json"):
        try:
            mt = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
            if mt < cutoff:
                fp.unlink()
                deleted += 1
        except OSError:
            continue
    return deleted


def prune_runs(days: int = 30, patrol_days: int = 7) -> dict[str, Any]:
    """按时间清理: 删 > days 天的完整日志文件 + 裁 runs.jsonl 到 days 内 +
    删 > patrol_days 天的 logs/patrol/ 巡逻日志(2026-07-04 密度分层批1: patrol 提到
    心跳级后日志量剧增, 7 天留存足够回看)。"""
    from datetime import timedelta
    cutoff = _now() - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()
    deleted = 0
    rd = cron_dir() / "runs"
    if rd.is_dir():
        for td in rd.iterdir():
            if not td.is_dir():
                continue
            for fp in td.glob("*.log"):
                try:
                    mt = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
                    if mt < cutoff:
                        fp.unlink()
                        deleted += 1
                except OSError:
                    continue
            try:
                if not any(td.iterdir()):
                    td.rmdir()
            except OSError:
                pass
    p = runs_log()
    kept = dropped = 0
    if p.is_file():
        keep: list[str] = []
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (r.get("ts") or "") >= cutoff_iso:
                    keep.append(line)
                    kept += 1
                else:
                    dropped += 1
            p.write_text(("\n".join(keep) + "\n") if keep else "", encoding="utf-8")
        except OSError:
            pass
    deleted_patrol_logs = _prune_patrol_logs(patrol_days)
    return {"deleted_log_files": deleted, "jsonl_kept": kept, "jsonl_dropped": dropped,
            "cutoff_days": days, "deleted_patrol_logs": deleted_patrol_logs,
            "patrol_cutoff_days": patrol_days}


def read_runs(name: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    """读运行历史(最近在前)。name 给了就只看该任务。"""
    p = runs_log()
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if name and r.get("name") != name:
                continue
            out.append(r)
    except OSError:
        return []
    return out[-limit:][::-1]


def _cadence_seconds(schedule: str) -> int:
    s = (schedule or "").strip().lower()
    if s in _CADENCE_SECONDS:
        return _CADENCE_SECONDS[s]
    return 86400  # 原始 cron 表达式保守按每日(治理用 preset)


def is_due(task: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or _now()
    last = task.get("last_run_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True
    return (now - last_dt).total_seconds() >= _cadence_seconds(task.get("schedule", ""))


def is_work_due(task: dict[str, Any], current_value: int, now: datetime | None = None) -> bool:
    """工作量触发判断(锚: overnight-run.md 第六节工作项3"工作里程表")。

    纯比较, 不做 IO(计量读数由调用方传入)。任务需带 work_trigger 字段:
      work_trigger: {"metric": <计量名>, "every": <阈值增量>, "min_interval_hours": <最小间隔小时数>}
    水位线存 work_trigger_state: {"baseline": <int>, "last_triggered_at": <iso8601|None>}。
    从未触发过(无 work_trigger_state)以 0 为 baseline 起算, 且不受 min_interval 约束。

    到期条件: 增量(current_value - baseline) >= every 且 距上次触发 >= min_interval_hours。
    """
    now = now or _now()
    wt = task.get("work_trigger") or {}
    every = int(wt.get("every"))
    min_interval_hours = float(wt.get("min_interval_hours", 0))

    state = task.get("work_trigger_state") or {}
    baseline = int(state.get("baseline", 0))
    last_triggered_at = state.get("last_triggered_at")

    delta = current_value - baseline
    if delta < every:
        return False

    if last_triggered_at:
        try:
            last_dt = datetime.fromisoformat(last_triggered_at)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed_hours = (now - last_dt).total_seconds() / 3600.0
            if elapsed_hours < min_interval_hours:
                return False
        except (ValueError, TypeError):
            pass  # 时间戳解析失败不阻断触发判断(交由上层 try/except 兜底)

    return True


def ensure_governance_tasks() -> list[str]:
    """缺失的标准治理 cron 任务补建, 返回新建的任务名。已存在的不动(保留其 last_run_at)。"""
    created: list[str] = []
    d = cron_dir()
    for t in _GOVERNANCE_TASKS:
        p = d / f"{t['name']}.json"
        if p.exists():
            continue
        p.write_text(json.dumps({
            **t, "prompt": "", "created_at": _now().isoformat(), "last_run_at": None,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        created.append(t["name"])
    return created


def tick(*, dry_run: bool = False, now: datetime | None = None,
         prompt_runner: Any = None) -> dict[str, Any]:
    """跑一遍: 找到期任务, 执行其 command(或 prompt 型 → 交给 prompt_runner), 更新 last_run_at。

    prompt_runner: 可选 callable(task)->str。给了它, 无 command 但有 prompt 的任务就交它消费
    (统一 cron tick 传入一个起 claude-code worker 的 runner);不给则 prompt 型任务跳过(保持
    governance cron-tick 老行为)。
    """
    now = now or _now()
    d = cron_dir()
    ran: list[dict[str, Any]] = []
    for f in sorted(d.glob("*.json")):
        if f.name.startswith("."):
            continue  # 跳过内部状态/锁文件(.last_tick.json 等)
        try:
            task = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        due_by_work = False
        current_metric_value: int | None = None
        work_trigger_error = False
        if task.get("work_trigger"):
            # 工作量触发: 有 work_trigger 时 schedule 降级为"最长间隔兜底"。判断整体
            # try/except 兜住——任何异常(畸形阈值/计量读数出错等)静默降级回 is_due()
            # 的自然时间逻辑, 不抛异常、不影响本任务之外其他任务的判断与执行。
            try:
                metric_name = task["work_trigger"]["metric"]
                current_metric_value = read_metric(metric_name)
                due_by_work = is_work_due(task, current_metric_value, now)
            except Exception:  # noqa: BLE001  # 静默降级, 走下面 is_due() 兜底
                due_by_work = False
                current_metric_value = None
                work_trigger_error = True

        has_work_trigger = bool(task.get("work_trigger"))
        if not due_by_work:
            if has_work_trigger and not work_trigger_error:
                # 兜底周期以"上次工作量触发时间"为准(未触发过则退回 created_at), 不看
                # command 本身的 last_run_at——工作量未达标不该被 command 的执行历史
                # 抢答, 只有自然时间周期(schedule)真正过了才兜底触发一次。
                state = task.get("work_trigger_state") or {}
                fallback_last = state.get("last_triggered_at") or task.get("created_at")
                fallback_task = {"schedule": task.get("schedule"), "last_run_at": fallback_last}
                schedule_fallback_due = is_due(fallback_task, now)
            else:
                schedule_fallback_due = is_due(task, now)
            if not schedule_fallback_due:
                if has_work_trigger:
                    # 工作量触发任务即便未到期也记一条(ran=False), 供调用方核实
                    # "阈值未到=判断了但不执行"(错误样本㊁), 而非静默不出现。
                    ran.append({"name": task.get("name"), "command": (task.get("command") or "").strip(),
                                "ran": False})
                continue
        cmd = (task.get("command") or "").strip()
        prompt = (task.get("prompt") or "").strip()
        rec = {"name": task.get("name"), "command": cmd, "ran": False}
        if not cmd:
            # prompt 型任务: 有 prompt_runner 就交它(起 worker 消费), 否则跳过
            if prompt and prompt_runner is not None:
                rec["kind"] = "prompt"
                if dry_run:
                    rec["would_run"] = True
                else:
                    try:
                        rec["tail"] = str(prompt_runner(task))[-300:]
                        rec["ran"] = True
                        task["last_run_at"] = now.isoformat()
                        f.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
                        record_run(task.get("name"), ok=True, trigger="scheduled", output=rec.get("tail", ""))
                    except Exception as e:  # noqa: BLE001
                        rec["error"] = f"{type(e).__name__}: {e}"[:300]
                        record_run(task.get("name"), ok=False, trigger="scheduled", output=rec["error"])
                ran.append(rec)
                continue
            rec["skipped"] = "无 command(prompt 型需 prompt_runner)"
            ran.append(rec)
            continue
        if dry_run:
            rec["would_run"] = True
            ran.append(rec)
            continue
        try:
            proc = run_command_capped(cmd)
            _full = (proc.stdout or "") + (proc.stderr or "")
            rec["ran"] = True
            rec["returncode"] = proc.returncode
            rec["tail"] = _full[-300:]
            task["last_run_at"] = now.isoformat()
            if due_by_work and current_metric_value is not None:
                # 工作量触发实际执行: 水位线推进到当前计量值, 触发时间戳记为本次 tick 时刻。
                # 只在触发时推进(跳过判断绝不推进), 保证同一份工作量不重复触发(错误样本㊀)。
                task["work_trigger_state"] = {
                    "baseline": current_metric_value,
                    "last_triggered_at": now.isoformat(),
                }
            f.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
            record_run(task.get("name"), ok=(proc.returncode == 0), trigger="scheduled",
                       returncode=proc.returncode, output=_full)
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"[:300]
            record_run(task.get("name"), ok=False, trigger="scheduled", output=rec["error"])
        ran.append(rec)
    return {"checked_at": now.isoformat(), "dry_run": dry_run, "ran": ran, "due_count": len(ran)}
