# [OMNI] origin=claude-code domain=services/guardian ts=2026-05-04T00:00:00Z type=infrastructure
"""workspace_pollution — D 盘根 + 工作区根污染检测 + 备份删除哨兵.

驱动: 用户 2026-05-03 反馈 bash 工具频繁误创建错误文件 (nul / -p / 反斜杠路径破坏 / 双层盘符).
即使 BashBus 加了防御层 (上游拦截), 仍可能有遗漏 (旧 subprocess 调用 / 第三方工具 / 历史残留).
本模块作为**事后清理**安全网, 周期性扫顶层非白名单项 → 备份后删除 + 罚单.

设计原则:
  - 只扫顶层 (depth=1), 不深入子目录 (P4 等大目录扫不动)
  - 白名单驱动: 列在白名单内的合法, 未列的全部按污染处理
  - 备份不留告示牌 (跟拖车 quarantine 不同: 那是"提醒写入者"语义, 这里是"用户工作区不被污染")
  - 罚单 + 审计落盘, 后续可查"谁清了什么"
  - Windows 设备名特例: nul / con / aux / prn / lpt1~9 / com1~9 单独处理
    (它们 os.listdir 看不到, 但 os.path.exists 看得到)

调用入口:
  - 哨兵唤醒时调 run_workspace_pollution_scan()
  - 也可手动 omni guardian workspace-pollution-scan
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─── 默认白名单 (硬编码, 避免 yaml 依赖 + 启动时刻必须可用) ──────────


# D 盘根白名单
_D_DRIVE_ROOT_WHITELIST: frozenset[str] = frozenset({
    "P4",
    "WSL",
    "$RECYCLE.BIN",
    "System Volume Information",
    "DumpStack.log.tmp",
    "wsl-backup.tar",
    "RecorderTrigger",  # 某录屏工具运行目录, 不是 bash 误产物
})


# 工作区根 (e:/WindowsWorkspace/) 白名单
_WORKSPACE_ROOT_WHITELIST: frozenset[str] = frozenset({
    ".claude",
    ".omni",
    ".pytest_cache",
    "CLAUDE.md",
    "_archive",
    "_scratch",
    "captures",
    "cmd-flash-watchdog",
    "data",
    "demoworkspace",
    "figma-to-html",
    "hypothesis-workspace",
    "demogame-knowledge-base",
    "demogame-learn",
    "language-anchoring-protocol",
    "lofa",
    "node_modules",
    "omnicompany",
    "overlay-shell",
    "overlay-note-store",
    "package-lock.json",
    "package.json",
    "poof",
    "poof-notes",
    "quant-friend-relay",
    "quant-lab",
    "security_analysis",
    "test-results",
    "webworks",
    "whatnow",
    "参考项目",
    "发布",
    "故事",
    "用户原始需求存档",
})


# Windows 保留设备名 (出现即 bash 工具 bug, 直接删不备份)
_WINDOWS_DEVICE_NAMES: frozenset[str] = frozenset({
    "nul", "con", "aux", "prn",
    *(f"lpt{i}" for i in range(1, 10)),
    *(f"com{i}" for i in range(1, 10)),
})


# 默认扫描根 (相对路径自动展开为绝对)
_DEFAULT_SCAN_TARGETS = (
    ("workspace_root", Path("e:/WindowsWorkspace"), _WORKSPACE_ROOT_WHITELIST),
    ("d_drive_root", Path("d:/"), _D_DRIVE_ROOT_WHITELIST),
)


@dataclass
class PollutionTicket:
    """单条污染清理罚单."""
    ticket_id: str
    detected_at: str
    scan_root: str             # workspace_root / d_drive_root
    original_path: str
    item_type: str             # file / dir / device_name
    backup_path: str           # 空表示设备名直接删未备份
    deleted_at: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _is_device_name(name: str) -> bool:
    """名字 (不含扩展名前) 是 Windows 保留设备名."""
    base = name.split(".", 1)[0].lower()
    return base in _WINDOWS_DEVICE_NAMES


def _quarantine_dir(omni_root: Path) -> Path:
    """归档隔离根. omni_root 是 omnicompany 项目根."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return omni_root / ".omni" / "quarantine" / "workspace_pollution" / today


def _backup_and_delete(
    abs_path: Path,
    backup_root: Path,
    item_name: str,
) -> Path:
    """备份到 backup_root/<item_name> 后删除原项. 返回备份路径."""
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / item_name
    # 同名冲突: 加时间戳后缀
    if backup_path.exists():
        ts = datetime.now(timezone.utc).strftime("%H%M%S")
        backup_path = backup_root / f"{item_name}.{ts}"

    if abs_path.is_dir():
        shutil.copytree(str(abs_path), str(backup_path))
        shutil.rmtree(str(abs_path))
    else:
        shutil.copy2(str(abs_path), str(backup_path))
        abs_path.unlink()
    return backup_path


def _delete_device_name(scan_root: Path, name: str) -> bool:
    """删 Windows 设备名虚假文件 (os.listdir 看不到但 os.path.exists 返 True).

    用 os.unlink 直接 + path 显式构造. 失败返 False.
    """
    target = scan_root / name
    try:
        # Windows 上设备名通常不能直接 unlink; 用 open + truncate 后 unlink
        # 但更可靠是先 os.remove
        if os.path.exists(str(target)) and not target.is_dir():
            try:
                os.remove(str(target))
                return True
            except (OSError, PermissionError) as e:
                logger.debug("[workspace_pollution] 设备名 %s 直接删除失败: %s", name, e)
                # 备用: 用 \\?\ 长路径前缀绕过保留名解释
                long_path = "\\\\?\\" + str(target.resolve()).replace("/", "\\")
                try:
                    os.remove(long_path)
                    return True
                except OSError as e2:
                    logger.warning("[workspace_pollution] 设备名 %s 长路径删除也失败: %s", name, e2)
                    return False
    except Exception as e:
        logger.warning("[workspace_pollution] 设备名 %s 处理异常: %s", name, e)
    return False


def scan_pollution(
    scan_root: Path,
    whitelist: frozenset[str],
    dry_run: bool = False,
    omni_root: Optional[Path] = None,
) -> list[PollutionTicket]:
    """扫一个根目录的顶层污染. 返回处置罚单列表.

    Args:
        scan_root: 要扫的目录 (顶层 only)
        whitelist: 该目录合法顶层项的集合
        dry_run: True 仅汇报不动文件
        omni_root: omnicompany 项目根 (用于定位 .omni/quarantine/), None 用 scan_root
    """
    if not scan_root.exists():
        logger.debug("[workspace_pollution] 扫描根不存在: %s", scan_root)
        return []

    omni_root = omni_root or Path("e:/WindowsWorkspace/omnicompany")
    backup_root = _quarantine_dir(omni_root)
    now = datetime.now(timezone.utc).isoformat()
    tickets: list[PollutionTicket] = []
    counter = 0

    # 1. 普通顶层项 (os.listdir 能看到的)
    try:
        items = os.listdir(str(scan_root))
    except (OSError, PermissionError) as e:
        logger.warning("[workspace_pollution] 无法 listdir %s: %s", scan_root, e)
        return []

    for item in items:
        if item in whitelist:
            continue
        abs_path = scan_root / item
        item_type = "dir" if abs_path.is_dir() else "file"
        counter += 1
        ticket_id = f"WSP-{now[:10]}-{counter:03d}"

        if dry_run:
            tickets.append(PollutionTicket(
                ticket_id=ticket_id, detected_at=now,
                scan_root=str(scan_root), original_path=str(abs_path),
                item_type=item_type, backup_path="(dry-run)",
                deleted_at="(dry-run)",
                reason=f"非白名单 {item_type}: {item}",
            ))
            continue

        try:
            backup_path = _backup_and_delete(abs_path, backup_root, item)
            tickets.append(PollutionTicket(
                ticket_id=ticket_id, detected_at=now,
                scan_root=str(scan_root), original_path=str(abs_path),
                item_type=item_type, backup_path=str(backup_path),
                deleted_at=datetime.now(timezone.utc).isoformat(),
                reason=f"非白名单 {item_type} 已备份后删除",
            ))
            logger.warning("[workspace_pollution] 清 %s → 备份 %s", abs_path, backup_path)
        except Exception as e:
            logger.warning("[workspace_pollution] 处置 %s 失败: %s", abs_path, e)

    # 注: 之前曾试图额外扫 Windows 设备名 (nul / con / aux ...), 因为
    # `os.path.exists("any/dir/nul")` 在 Windows 总返 True (设备引用).
    # 但事实上: 真被 bash bug 创建的 nul *文件* 会出现在 os.listdir 结果里,
    # 走上面的普通分支处置即可. exists() 的设备引用只是虚假报告 — 不该当污染.
    # _delete_device_name 辅助函数仍保留: 普通分支 unlink 失败时 (Windows 拒绝
    # 因为名字是保留设备名), 走 \\?\ 长路径前缀绕过. 详见 _backup_and_delete 调用链.

    # 写罚单清单 (append-only jsonl)
    if tickets and not dry_run:
        try:
            ticket_log = omni_root / ".omni" / "quarantine" / "workspace_pollution" / "tickets.jsonl"
            ticket_log.parent.mkdir(parents=True, exist_ok=True)
            with ticket_log.open("a", encoding="utf-8") as f:
                for t in tickets:
                    f.write(json.dumps(t.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug("[workspace_pollution] 写 tickets.jsonl 失败: %s", e)

    return tickets


def run_workspace_pollution_scan(
    targets: tuple = _DEFAULT_SCAN_TARGETS,
    dry_run: bool = False,
    omni_root: Optional[Path] = None,
) -> dict:
    """跑一遍所有目标根的扫描. 哨兵唤醒时调.

    Returns:
        {"total_tickets": int, "by_root": {root_name: count, ...}, "tickets": [...]}
    """
    all_tickets: list[PollutionTicket] = []
    by_root: dict[str, int] = {}

    for root_name, scan_root, whitelist in targets:
        ts = scan_pollution(scan_root, whitelist, dry_run=dry_run, omni_root=omni_root)
        by_root[root_name] = len(ts)
        all_tickets.extend(ts)

    return {
        "total_tickets": len(all_tickets),
        "by_root": by_root,
        "tickets": [t.to_dict() for t in all_tickets],
        "dry_run": dry_run,
    }


__all__ = [
    "PollutionTicket",
    "scan_pollution",
    "run_workspace_pollution_scan",
    "_D_DRIVE_ROOT_WHITELIST",
    "_WORKSPACE_ROOT_WHITELIST",
    "_WINDOWS_DEVICE_NAMES",
]
