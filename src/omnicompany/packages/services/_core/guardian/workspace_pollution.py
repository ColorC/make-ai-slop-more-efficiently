# [OMNI] origin=claude-code domain=services/guardian ts=2026-05-04T00:00:00Z type=infrastructure
"""workspace_pollution — 盘根 + 工作区根的只读污染候选哨兵.

驱动: 用户 2026-05-03 反馈 bash 工具频繁误创建错误文件 (nul / -p / 反斜杠路径破坏 / 双层盘符).
即使 BashBus 加了防御层 (上游拦截), 仍可能有遗漏 (旧 subprocess 调用 / 第三方工具 / 历史残留).
本模块只产生顶层非登记项候选。未知项并不等于可删除垃圾；移动/删除必须由
带所有权、占用、hash 与回退信息的已验收 manifest mover 单独执行。

设计原则:
  - 只扫顶层 (depth=1), 不深入子目录 (P4 等大目录扫不动)
  - 登记驱动: 工作区直属、允许本地落位的 registry 项合法，其他仅报候选
  - 不在此设施备份、移动或删除；处置另走带 manifest 的流程
  - Windows 设备名仅作为命名候选，不在本模块单独处置
    (它们 os.listdir 看不到, 但 os.path.exists 看得到)

调用入口: 哨兵唤醒时调 ``run_workspace_pollution_scan()``。
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

import yaml

logger = logging.getLogger(__name__)


class WorkspaceRegistryError(ValueError):
    """Stable, fail-closed error while reading workspace.yaml."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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


# Root files are not workspace.yaml entries.  Keep this compact, explicit file
# exception set; directory names always come from the registry loader below.
_WORKSPACE_ROOT_STATIC_FILES: frozenset[str] = frozenset({
    "AGENTS.md", "CLAUDE.md", "workspace.yaml", ".gitignore", ".ignore",
})


# Windows 保留设备名（供候选分类与兼容调用者识别）
_WINDOWS_DEVICE_NAMES: frozenset[str] = frozenset({
    "nul", "con", "aux", "prn",
    *(f"lpt{i}" for i in range(1, 10)),
    *(f"com{i}" for i in range(1, 10)),
})


def _is_direct_child_name(name: str) -> bool:
    return (
        bool(name)
        and name == name.strip()
        and name.rstrip(" .") == name
        and name not in {".", ".."}
        and not any(mark in name for mark in ("/", "\\", ":"))
    )


def load_workspace_registry_whitelist(registry_path: Path, workspace_root: Path) -> frozenset[str]:
    """Read only local direct children registered by ``workspace.yaml``.

    ``path`` is optional for legacy records, but if supplied it must resolve
    exactly to ``workspace_root / name``.  An external path and an explicit
    ``allow_local_root: false`` never authorize a same-named local child.  A
    direct local entry may be pre-registered before its directory exists.
    """
    try:
        raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceRegistryError("WORKSPACE_REGISTRY_MISSING", f"workspace registry missing: {registry_path}") from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise WorkspaceRegistryError("WORKSPACE_REGISTRY_INVALID", f"workspace registry unreadable: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list) or not raw["entries"]:
        raise WorkspaceRegistryError("WORKSPACE_REGISTRY_INVALID", "workspace.yaml entries must be a non-empty list")

    root = workspace_root.resolve()
    allowed: set[str] = set()
    seen: set[str] = set()
    for index, entry in enumerate(raw["entries"]):
        if not isinstance(entry, dict):
            raise WorkspaceRegistryError("WORKSPACE_REGISTRY_INVALID", f"entries[{index}] must be a mapping")
        name = entry.get("name")
        if not isinstance(name, str) or not _is_direct_child_name(name):
            raise WorkspaceRegistryError("WORKSPACE_REGISTRY_INVALID", f"entries[{index}].name must be one direct child name")
        name_key = name.casefold()
        if name_key in seen:
            raise WorkspaceRegistryError("WORKSPACE_REGISTRY_INVALID", f"duplicate workspace entry name: {name}")
        seen.add(name_key)
        allow_local_root = entry.get("allow_local_root", True)
        if type(allow_local_root) is not bool:
            raise WorkspaceRegistryError("WORKSPACE_REGISTRY_INVALID", f"entries[{index}].allow_local_root must be boolean")
        location_status = entry.get("location_status", "local")
        if not isinstance(location_status, str) or location_status not in {"local", "external", "needs-review"}:
            raise WorkspaceRegistryError("WORKSPACE_REGISTRY_INVALID", f"entries[{index}].location_status is invalid")
        if location_status != "local" and allow_local_root:
            raise WorkspaceRegistryError(
                "WORKSPACE_REGISTRY_INVALID",
                f"entries[{index}].location_status requires allow_local_root: false",
            )
        raw_path = entry.get("path", name)
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise WorkspaceRegistryError("WORKSPACE_REGISTRY_INVALID", f"entries[{index}].path must be a string when present")
        try:
            candidate = (Path(raw_path) if Path(raw_path).is_absolute() else root / raw_path).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspaceRegistryError("WORKSPACE_REGISTRY_INVALID", f"entries[{index}].path cannot resolve: {exc}") from exc
        if candidate.parent != root or candidate.name != name:
            continue  # valid external/non-direct catalogue record; never authorize a local namesake
        if allow_local_root:
            allowed.add(name)
    return frozenset(allowed)


def _workspace_registry_path(omni_root: Path) -> Path:
    return omni_root.resolve().parent / "workspace.yaml"


def _default_scan_targets(omni_root: Path) -> tuple:
    workspace_root = omni_root.resolve().parent
    registered = load_workspace_registry_whitelist(_workspace_registry_path(omni_root), workspace_root)
    workspace_whitelist = registered | _WORKSPACE_ROOT_STATIC_FILES
    return (
        ("workspace_root", workspace_root, workspace_whitelist),
        ("d_drive_root", Path("D:/"), _D_DRIVE_ROOT_WHITELIST),
    )


# Compatibility observation for older importers/tests.  Actual scans reload the
# registry for every pass, so this value is never a stale authority.
try:
    from omnicompany.core.config import omni_workspace_root

    _WORKSPACE_ROOT_WHITELIST = load_workspace_registry_whitelist(
        _workspace_registry_path(omni_workspace_root()), omni_workspace_root().parent,
    ) | _WORKSPACE_ROOT_STATIC_FILES
except Exception:  # import must not turn a bad registry into an unsafe scanner
    _WORKSPACE_ROOT_WHITELIST = _WORKSPACE_ROOT_STATIC_FILES


@dataclass
class PollutionTicket:
    """单条只读污染候选记录；历史字段名保持兼容。"""
    ticket_id: str
    detected_at: str
    scan_root: str             # workspace_root / d_drive_root
    original_path: str
    item_type: str             # file / dir / device_name
    backup_path: str           # 兼容字段；候选扫描固定为 "(dry-run)"
    deleted_at: str            # 兼容字段；候选扫描固定为 "(dry-run)"
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
    """Legacy manifest-mover helper; candidate scans never call it."""
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
    """Legacy device-name helper; read-only candidate scans never call it."""
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
    dry_run: bool = True,
    omni_root: Optional[Path] = None,
) -> list[PollutionTicket]:
    """扫一个根目录的顶层污染. 返回处置罚单列表.

    Args:
        scan_root: 要扫的目录 (顶层 only)
        whitelist: 该目录合法顶层项的集合
        dry_run: 兼容参数。无论传值为何，本设施只汇报不动文件。
        omni_root: omnicompany 项目根 (用于定位 .omni/quarantine/), None 用 scan_root
    """
    requested_dry_run = dry_run
    if not dry_run:
        logger.warning("[workspace_pollution] destructive request blocked; use an approved manifest mover")
    dry_run = True
    if not scan_root.exists():
        logger.debug("[workspace_pollution] 扫描根不存在: %s", scan_root)
        return []

    omni_root = omni_root or Path("C:/workspace/omnicompany")
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

    # Legacy append-only record branch. The forced candidate path above never
    # reaches it; remediation belongs to the separate manifest mover.
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
    targets: tuple | None = None,
    dry_run: bool = True,
    omni_root: Optional[Path] = None,
) -> dict:
    """跑一遍所有目标根的扫描. 哨兵唤醒时调.

    Returns:
        {"total_tickets": int, "by_root": {root_name: count, ...}, "tickets": [...]}
    """
    requested_dry_run = dry_run
    if not dry_run:
        logger.warning("[workspace_pollution] destructive run blocked; reporting candidates only")
    dry_run = True
    if omni_root is None:
        from omnicompany.core.config import omni_workspace_root

        omni_root = omni_workspace_root()
    if targets is None:
        try:
            targets = _default_scan_targets(omni_root)
        except WorkspaceRegistryError as exc:
            return {
                "ok": False, "error_code": exc.code, "error": str(exc),
                "total_tickets": 0, "by_root": {}, "tickets": [],
                "dry_run": True, "requested_dry_run": requested_dry_run,
                "blocked_reason": "workspace registry invalid; no roots scanned",
            }

    all_tickets: list[PollutionTicket] = []
    by_root: dict[str, int] = {}

    for root_name, scan_root, whitelist in targets:
        ts = scan_pollution(scan_root, whitelist, dry_run=dry_run, omni_root=omni_root)
        by_root[root_name] = len(ts)
        all_tickets.extend(ts)

    return {
        "ok": True,
        "total_tickets": len(all_tickets),
        "by_root": by_root,
        "tickets": [t.to_dict() for t in all_tickets],
        "dry_run": dry_run,
        "requested_dry_run": requested_dry_run,
        "blocked_reason": "candidates only; destructive remediation requires an approved manifest mover",
    }


__all__ = [
    "PollutionTicket",
    "scan_pollution",
    "run_workspace_pollution_scan",
    "load_workspace_registry_whitelist",
    "WorkspaceRegistryError",
    "_D_DRIVE_ROOT_WHITELIST",
    "_WORKSPACE_ROOT_WHITELIST",
    "_WINDOWS_DEVICE_NAMES",
]
