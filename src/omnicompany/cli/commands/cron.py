# [OMNI] origin=claude-code domain=omnicompany/cli ts=2026-06-23 type=cli status=active
# [OMNI] summary="omni cron —— 统一定时调度面: list/run/tick/status/install-trigger。tick 扇出治理 command 任务 + prompt 型(worker 消费)+ patrol-if-due, 持锁防重入; install-trigger 用 schtasks 建心跳。"
# [OMNI] why="omni 一直有 .omni/cron/ 任务存储与 tick 逻辑, 却无外部心跳触发器(8 个治理任务全靠人想起来)。本组补统一 tick + OS 心跳, 让定时与手动同源, 扁平调用链不复发 spawn 风暴。"
# [OMNI] tags=cli,cron,scheduler,heartbeat,governance,unified
"""omni cron —— 统一定时调度面(心跳 tick + 任务管理)。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._governance import scheduler

_TRIGGER_NAME = "OmniCronHeartbeat"
_LOCK_STALE_S = 1800  # 锁超 30min 视为僵死, 下次心跳接管(单 tick 不该跑这么久)
_WORKER_TIMEOUT_S = 1800
# Windows 后台子进程标志: 不弹控制台窗口(否则 cmd/conhost 闪屏抢焦点) + 降到 below-normal
# 优先级(治理批处理让出 CPU 给交互编辑器, 防扩展宿主被压到无响应)。非 Windows 取 0。
_BG_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _omni_exe() -> Path:
    """定位 omni.exe(venv/Scripts 里与 python.exe 同目录)。"""
    cand = Path(sys.executable).with_name("omni.exe")
    if cand.exists():
        return cand
    import shutil
    found = shutil.which("omni")
    return Path(found) if found else cand


def _status_path() -> Path:
    return scheduler.cron_dir() / ".last_tick.json"


def _lock_path() -> Path:
    return scheduler.cron_dir() / ".tick.lock"


def _acquire_lock() -> bool:
    """非阻塞获取 tick 锁; 已有新鲜锁(<30min)返回 False(跳过本次心跳)。"""
    p = _lock_path()
    if p.exists():
        try:
            held = json.loads(p.read_text(encoding="utf-8"))
            started = datetime.fromisoformat(held["started_at"])
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if (_now() - started).total_seconds() < _LOCK_STALE_S:
                return False  # 有活 tick 在跑
        except Exception:  # noqa: BLE001
            pass  # 锁损坏/过期 → 接管
    p.write_text(json.dumps({"pid": os.getpid(), "started_at": _now().isoformat()},
                            ensure_ascii=False), encoding="utf-8")
    return True


def _release_lock() -> None:
    try:
        _lock_path().unlink()
    except OSError:
        pass


def _run_prompt_task(task: dict) -> str:
    """prompt 型 cron 任务: 起一个 audited claude-code worker 消费(扁平子进程, 不递归)。"""
    prompt = (task.get("prompt") or "").strip()
    root = omni_workspace_root()
    proc = subprocess.run(
        [str(_omni_exe()), "worker", "run", "claude-code",
         "--prompt", prompt, "--permission", "workspace-write",
         "--cwd", str(root), "--timeout", str(_WORKER_TIMEOUT_S)],
        cwd=str(root), capture_output=True, text=True, timeout=_WORKER_TIMEOUT_S + 120,
        encoding="utf-8", errors="replace", creationflags=_BG_FLAGS,
    )
    return (proc.stdout or proc.stderr or "")[-300:]


def _patrol_if_due() -> dict:
    """到期则跑一次 guardian patrol(沿用 sentinel 活动+冷却门控; 无新活动自动跳过)。"""
    try:
        from omnicompany.packages.services._core.guardian.sentinel import _run_once
        did = _run_once(omni_workspace_root(), 300, 1800, False)
        return {"patrolled": bool(did)}
    except Exception as e:  # noqa: BLE001
        return {"patrol_error": f"{type(e).__name__}: {e}"[:200]}


def _trigger_installed() -> bool:
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", _TRIGGER_NAME],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


@click.group("cron")
def cmd_cron() -> None:
    """统一定时调度: list/run/tick/status/install-trigger(.omni/cron/ + OS 心跳)。"""


@cmd_cron.command("list")
@click.option("--json", "as_json", is_flag=True)
def cron_list(as_json: bool) -> None:
    """列出全部 cron 任务(含标准治理任务, 缺则补建)。"""
    scheduler.ensure_governance_tasks()
    tasks = scheduler.load_tasks()
    rows = []
    for t in tasks:
        rows.append({
            "name": t.get("name"), "schedule": t.get("schedule"),
            "kind": "command" if (t.get("command") or "").strip() else ("prompt" if (t.get("prompt") or "").strip() else "?"),
            "last_run_at": t.get("last_run_at"),
            "due": scheduler.is_due(t),
            "desc": (t.get("description") or t.get("command") or t.get("prompt") or "")[:70],
        })
    if as_json:
        click.echo(json.dumps({"items": rows, "total": len(rows)}, ensure_ascii=False, indent=2))
        return
    if not rows:
        click.echo("无 cron 任务。")
        return
    for r in rows:
        flag = " ▶到期" if r["due"] else ""
        last = r["last_run_at"] or "从未"
        click.echo(f"  {r['name']:<26} {r['schedule']:<9} [{r['kind']}] 上次={last}{flag}")
        click.echo(f"      {r['desc']}")
    click.echo(f"\n共 {len(rows)} 个 · 触发器(心跳){'已装' if _trigger_installed() else '未装(omni cron install-trigger)'}")


@cmd_cron.command("tick")
@click.option("--dry-run", is_flag=True, help="只看哪些到期, 不执行")
@click.option("--no-patrol", is_flag=True, help="本次不顺带跑 guardian patrol")
@click.option("--json", "as_json", is_flag=True)
def cron_tick(dry_run: bool, no_patrol: bool, as_json: bool) -> None:
    """统一心跳: 跑到期的 command/prompt 任务 + patrol-if-due(持锁防重入)。由 OS 心跳每几分钟调, 也可手动跑。"""
    scheduler.ensure_governance_tasks()
    if not dry_run and not _acquire_lock():
        click.echo("已有 tick 在跑(锁未释放), 跳过本次。")
        return
    try:
        res = scheduler.tick(dry_run=dry_run,
                             prompt_runner=None if dry_run else _run_prompt_task)
        patrol = {} if (no_patrol or dry_run) else _patrol_if_due()
    finally:
        if not dry_run:
            _release_lock()
    status = {"checked_at": res["checked_at"], "due_count": res["due_count"],
              "ran": res["ran"], "patrol": patrol}
    if not dry_run:
        try:
            _status_path().write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    if as_json:
        click.echo(json.dumps(status, ensure_ascii=False, indent=2))
        return
    ran = [r for r in res["ran"] if r.get("ran") or r.get("would_run")]
    click.echo(f"{'(dry-run) ' if dry_run else ''}检查 @ {res['checked_at']} · 到期 {res['due_count']} 个")
    for r in res["ran"]:
        mark = "would" if r.get("would_run") else ("✓" if r.get("ran") else ("✗" if r.get("error") else "skip"))
        click.echo(f"  [{mark}] {r.get('name')}" + (f"  err={r['error']}" if r.get("error") else ""))
    if patrol:
        click.echo(f"  patrol: {patrol}")


@cmd_cron.command("run")
@click.argument("name")
def cron_run(name: str) -> None:
    """立即跑一个任务(忽略档期, 手动触发)。"""
    p = scheduler.cron_dir() / f"{name}.json"
    if not p.exists():
        raise click.UsageError(f"无此任务: {name}(omni cron list 看全部)")
    task = json.loads(p.read_text(encoding="utf-8"))
    cmd = (task.get("command") or "").strip()
    if cmd:
        click.echo(f"跑 command: {cmd}")
        proc = scheduler.run_command_capped(cmd)
        _full = (proc.stdout or "") + (proc.stderr or "")
        click.echo(_full[-1000:])
        click.echo(f"returncode={proc.returncode}")
        scheduler.record_run(name, ok=(proc.returncode == 0), trigger="manual",
                             returncode=proc.returncode, output=_full)
    elif (task.get("prompt") or "").strip():
        click.echo("跑 prompt(起 claude-code worker)...")
        out = _run_prompt_task(task)
        click.echo(out)
        scheduler.record_run(name, ok=True, trigger="manual", output=out)
    else:
        raise click.UsageError(f"任务 {name} 既无 command 也无 prompt")
    task["last_run_at"] = _now().isoformat()
    p.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(f"已更新 last_run_at。")


@cmd_cron.command("status")
def cron_status() -> None:
    """看上次心跳 + 各任务上次跑 + 触发器是否已装。"""
    sp = _status_path()
    if sp.exists():
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
            click.echo(f"上次 tick: {st.get('checked_at')} · 到期 {st.get('due_count')} 个 · patrol={st.get('patrol')}")
        except Exception:  # noqa: BLE001
            click.echo("上次 tick: (状态文件损坏)")
    else:
        click.echo("上次 tick: 从未(心跳未跑过)")
    click.echo(f"触发器 {_TRIGGER_NAME}: {'✓ 已装' if _trigger_installed() else '✗ 未装 — omni cron install-trigger'}")
    scheduler.ensure_governance_tasks()
    click.echo("\n任务:")
    for t in scheduler.load_tasks():
        due = " ▶到期" if scheduler.is_due(t) else ""
        click.echo(f"  {t.get('name'):<26} {t.get('schedule'):<9} 上次={t.get('last_run_at') or '从未'}{due}")


@cmd_cron.command("install-trigger")
@click.option("--interval", type=int, default=10, show_default=True, help="心跳间隔(分钟)")
@click.option("--uninstall", is_flag=True, help="删除心跳计划任务")
def cron_install_trigger(interval: int, uninstall: bool) -> None:
    """用 schtasks 建/撤一条每 N 分钟调 `omni cron tick` 的 Windows 计划任务(扁平、无递归、重启自恢复)。"""
    if uninstall:
        r = subprocess.run(["schtasks", "/Delete", "/TN", _TRIGGER_NAME, "/F"],
                           capture_output=True, text=True, timeout=30)
        click.echo((r.stdout or r.stderr).strip() or f"已删除 {_TRIGGER_NAME}")
        return
    exe = _omni_exe()
    if not exe.exists():
        raise click.UsageError(f"找不到 omni.exe: {exe}")
    # 心跳只可靠派发 cron 任务: --no-patrol。patrol 留给 git 提交钩子 + 活跃会话 daemon
    # (实测: patrol 的 LLM 复核在无人值守 schtasks 上下文会挂住并握死 tick 锁, 故解耦)。
    # 无前台窗口: 直接跑 console 子系统的 omni.exe, 交互式会话里 Windows 必给它分配一个可见控制台 → 桌面弹窗。
    # 最外层包 GUI 子系统的 wscript.exe(scripts/run_hidden.vbs, 窗口样式 0)→ 子孙控制台从创建那一刻
    # 就不可见, 真正零闪窗; cron_tick_hidden.py 的 ShowWindow 自隐藏保留作第二道保险(单用它仍有
    # python.exe 启动到隐藏之间的一瞬闪窗)。本机 pythonw 是 0 字节存根不能用。
    py = exe.with_name("python.exe")
    scripts_dir = Path(__file__).resolve().parents[4] / "scripts"
    launcher = scripts_dir / "cron_tick_hidden.py"
    wscript = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wscript.exe"
    vbs = scripts_dir / "run_hidden.vbs"
    if py.exists() and launcher.exists() and wscript.exists() and vbs.exists():
        tr = f'"{wscript}" "{vbs}" "{py}" "{launcher}"'
    elif py.exists() and launcher.exists():
        tr = f'"{py}" "{launcher}"'  # 回退: 自隐藏(启动一瞬短闪)
    else:
        tr = f'"{exe}" cron tick --no-patrol'  # 回退: 没 python/launcher 就用老的(会弹窗)
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", _TRIGGER_NAME, "/TR", tr,
         "/SC", "MINUTE", "/MO", str(interval), "/F"],
        capture_output=True, text=True, timeout=30,
    )
    out = (r.stdout or r.stderr).strip()
    if r.returncode == 0:
        click.echo(f"✓ 已装心跳计划任务 {_TRIGGER_NAME}(每 {interval} 分钟 → omni cron tick)")
        click.echo(f"  {out}")
        click.echo("  撤销: omni cron install-trigger --uninstall · 手动跑一次: schtasks /Run /TN " + _TRIGGER_NAME)
    else:
        click.echo(f"✗ schtasks 失败(rc={r.returncode}): {out}", err=True)
        raise SystemExit(1)


@cmd_cron.command("create")
@click.option("--name", required=True)
@click.option("--schedule", required=True, help="@daily/@weekly/@monthly/@hourly 或 5 段 cron")
@click.option("--command", default=None, help="要跑的命令(与 --prompt 二选一)")
@click.option("--prompt", default=None, help="或: 交 claude-code worker 消费的 prompt")
@click.option("--description", default="")
def cron_create(name: str, schedule: str, command: str | None,
                prompt: str | None, description: str) -> None:
    """新建/覆盖一个 cron 任务(写 .omni/cron/<name>.json)。"""
    import re
    if any(c in name for c in r' /\:*?"<>|'):
        raise click.UsageError("name 需文件系统安全(无空格/路径符)")
    presets = {"@every15m", "@hourly", "@daily", "@weekly", "@monthly", "@yearly"}
    s = schedule.strip()
    if s not in presets and not re.match(r"^(\S+\s+){4}\S+$", s):
        raise click.UsageError(f"schedule 需 preset {sorted(presets)} 或 5 段 cron")
    if not ((command or "").strip() or (prompt or "").strip()):
        raise click.UsageError("需 --command 或 --prompt")
    p = scheduler.cron_dir() / f"{name}.json"
    p.write_text(json.dumps({
        "name": name, "schedule": s, "command": (command or "").strip(),
        "prompt": (prompt or "").strip(), "description": description,
        "created_at": _now().isoformat(), "last_run_at": None,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(f"✓ 建 cron 任务 {name}({s})")


@cmd_cron.command("delete")
@click.argument("name")
def cron_delete(name: str) -> None:
    """删除一个 cron 任务。"""
    p = scheduler.cron_dir() / f"{name}.json"
    if not p.exists():
        raise click.UsageError(f"无此任务: {name}")
    p.unlink()
    click.echo(f"已删除 {name}")


@cmd_cron.command("prune")
@click.option("--days", type=int, default=30, show_default=True, help="删超过这么多天的运行日志")
@click.option("--patrol-days", type=int, default=7, show_default=True,
              help="删超过这么多天的 logs/patrol/ 巡逻日志")
def cron_prune(days: int, patrol_days: int) -> None:
    """按时间清理运行历史(删旧的完整日志文件 + 裁 runs.jsonl + 删旧 patrol 巡逻日志)。"""
    r = scheduler.prune_runs(days=days, patrol_days=patrol_days)
    click.echo(f"清理 >{days} 天: 删完整日志 {r['deleted_log_files']} 个; "
               f"runs.jsonl 留 {r['jsonl_kept']} / 删 {r['jsonl_dropped']}")
    click.echo(f"清理 >{patrol_days} 天: 删 patrol 巡逻日志 {r['deleted_patrol_logs']} 个")


__all__ = ["cmd_cron"]
