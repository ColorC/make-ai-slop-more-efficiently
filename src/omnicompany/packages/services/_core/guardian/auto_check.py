# [OMNI] origin=claude-code domain=services/guardian ts=2026-04-14T00:00:00Z type=util
# [OMNI] material_id="material:core.guardian.auto_trigger_scheduler.implementation.py"
"""guardian.auto_check — Guardian 自动触发检查

触发条件（任一满足）：
  1. 距上次 Guardian 运行超过 TIME_THRESHOLD_HOURS 小时
  2. 核心包代码（src/omnicompany/packages/ + src/omnicompany/runtime/）
     自上次运行以来变更行数超过 LINES_THRESHOLD

防护机制：
  - 锁文件防止并发多例
  - Guardian 自身的文件变更不计入触发条件
  - 不递归：Guardian 管线本身不调用 auto_check

用法：
  python -m omnicompany.packages.services._core.guardian.auto_check
  或在 TeamRunner 中 import 并调用 maybe_run_guardian()
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from omnicompany.core.config import resolve_domain_data_dir

# ── 阈值配置 ─────────────────────────────────────────────────────────────────

TIME_THRESHOLD_HOURS = 6        # 超过 6 小时没跑，无论如何触发
LINES_THRESHOLD = 50            # 核心代码变更超过 50 行触发
LOCK_TIMEOUT_SECONDS = 300      # 锁文件超时（5 分钟），防止死锁

# 核心监控路径（相对于 repo root）
WATCH_PATHS = [
    "src/omnicompany/packages/",
    "src/omnicompany/runtime/",
    "src/omnicompany/protocol/",
    "src/omnicompany/core/",
]

# Guardian 自身的路径（不计入触发条件，避免递归）
GUARDIAN_PATH = "src/omnicompany/packages/services/guardian/"

# ── 状态存储 ─────────────────────────────────────────────────────────────────

def _state_dir() -> Path:
    return resolve_domain_data_dir("guardian")


def _lock_path() -> Path:
    return _state_dir() / ".lock"


def _state_path() -> Path:
    return _state_dir() / "auto_check_state.json"


def _load_state() -> dict:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_run_ts": None, "last_run_commit": None}


def _save_state(ts: str, commit: str) -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)
    _state_path().write_text(
        json.dumps({"last_run_ts": ts, "last_run_commit": commit}, indent=2),
        encoding="utf-8",
    )


# ── 条件检查 ─────────────────────────────────────────────────────────────────

def _repo_root() -> Path:
    """找 omnicompany repo 根目录。"""
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "pyproject.toml").exists():
            return p
    return here.parents[4]


def _current_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=_repo_root(),
        )
        return result.stdout.strip()[:12]
    except Exception:
        return "unknown"


def _changed_lines_since(since_commit: str | None) -> int:
    """统计自 since_commit 以来核心路径的变更行数（不含 guardian 自身）。"""
    if not since_commit or since_commit == "unknown":
        return LINES_THRESHOLD + 1  # 无基准，视为超阈值

    root = _repo_root()
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", since_commit, "HEAD", "--"],
            capture_output=True, text=True, cwd=root,
        )
        total_lines = 0
        for line in result.stdout.splitlines():
            # 只统计 WATCH_PATHS 里的文件，排除 GUARDIAN_PATH
            is_watched = any(wp in line for wp in WATCH_PATHS)
            is_guardian = GUARDIAN_PATH in line
            if is_watched and not is_guardian:
                # 从 "N insertions(+), M deletions(-)" 提取数字
                parts = line.split("|")
                if len(parts) >= 2:
                    nums = [int(x) for x in parts[-1].split() if x.isdigit()]
                    total_lines += sum(nums)
        return total_lines
    except Exception:
        return 0


def _hours_since_last_run(last_ts: str | None) -> float:
    if not last_ts:
        return float("inf")
    try:
        last = datetime.fromisoformat(last_ts)
        now = datetime.now(timezone.utc)
        return (now - last).total_seconds() / 3600
    except Exception:
        return float("inf")


def should_run() -> tuple[bool, str]:
    """判断是否应该触发 Guardian，返回 (should_run, reason)。"""
    state = _load_state()
    last_ts = state.get("last_run_ts")
    last_commit = state.get("last_run_commit")

    hours = _hours_since_last_run(last_ts)
    if hours >= TIME_THRESHOLD_HOURS:
        return True, f"距上次运行 {hours:.1f} 小时（阈值 {TIME_THRESHOLD_HOURS}h）"

    changed = _changed_lines_since(last_commit)
    if changed >= LINES_THRESHOLD:
        return True, f"核心代码变更 {changed} 行（阈值 {LINES_THRESHOLD}）"

    return False, f"无需触发（{hours:.1f}h 前运行，{changed} 行变更）"


# ── 锁机制 ───────────────────────────────────────────────────────────────────

def _acquire_lock() -> bool:
    lock = _lock_path()
    if lock.exists():
        # 检查锁是否超时（Guardian 崩溃后留下的死锁）
        try:
            mtime = lock.stat().st_mtime
            if time.time() - mtime < LOCK_TIMEOUT_SECONDS:
                return False  # 锁有效，另一个实例正在运行
        except Exception:
            pass
    # 写锁文件
    _state_dir().mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_lock() -> None:
    try:
        _lock_path().unlink(missing_ok=True)
    except Exception:
        pass


# ── 触发 Guardian ─────────────────────────────────────────────────────────────

def _run_guardian_scan(since_ts: str | None = None) -> bool:
    """实际执行 Guardian 扫描。

    两层：
    - Layer 1（确定性规则）：扫描自 since_ts 以来变更的文件
    - Layer 2（GuardianAgent LLM 复核）：对 needs_judgment 候选做语义判断
    - 新文件额外跑 LLM Judge（llm_new_only=True）
    """
    root = _repo_root()
    # 确保 .env 已加载（独立进程运行时可能没有继承环境变量）
    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env", override=False)
    except Exception:
        pass

    try:
        from omnicompany.packages.services._core.guardian import (
            run_patrol,
            format_patrol_report,
        )
        result = run_patrol(
            project_root=root,
            full_scan=False,           # diff 模式：只扫变更文件
            committed=True,
            uncommitted=True,
            n_commits=5,               # 回溯 5 个 commit，补抓最近漏掉的
            use_agent=True,            # LLM 复核 needs_judgment 候选
            use_llm=True,              # LLM Judge 也跑
            llm_new_only=True,         # LLM Judge 只对新增文件
            since_ts=since_ts,         # 按时间增量过滤
            auto_tow=False,            # 不自动修改，只报告
        )
        summary = format_patrol_report(result)
        print("[Guardian Auto] 扫描完成")
        print(summary)
        return True
    except Exception as e:
        print(f"[Guardian Auto] 运行失败: {type(e).__name__}: {e}")
        return False


def maybe_run_guardian(*, force: bool = False, verbose: bool = True) -> bool:
    """检查条件，满足时触发 Guardian。

    Args:
        force: 强制跑，忽略阈值（用于手动触发）
        verbose: 是否打印状态

    Returns:
        True = 触发了 Guardian 并完成，False = 未触发或被锁阻止
    """
    if not force:
        run, reason = should_run()
        if verbose:
            print(f"[Guardian Auto] {reason}")
        if not run:
            return False

    if not _acquire_lock():
        if verbose:
            print("[Guardian Auto] 另一个实例正在运行，跳过")
        return False

    try:
        if verbose:
            print("[Guardian Auto] 开始扫描...")
        state = _load_state()
        success = _run_guardian_scan(since_ts=state.get("last_run_ts"))
        if success:
            _save_state(
                ts=datetime.now(timezone.utc).isoformat(),
                commit=_current_commit(),
            )
        return success
    finally:
        _release_lock()


# ── 独立运行入口 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Guardian 自动检查触发器")
    parser.add_argument("--force", action="store_true", help="强制触发，忽略阈值")
    parser.add_argument("--check-only", action="store_true", help="只检查条件，不运行")
    args = parser.parse_args()

    if args.check_only:
        run, reason = should_run()
        print(f"应该触发: {run}")
        print(f"原因: {reason}")
    else:
        result = maybe_run_guardian(force=args.force)
        print(f"Guardian 触发结果: {'成功' if result else '跳过/失败'}")
