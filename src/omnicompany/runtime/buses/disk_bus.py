# [OMNI] origin=claude-code domain=runtime/buses ts=2026-04-23T00:00:00Z type=infrastructure
# [OMNI] material_id="material:runtime.buses.disk_bus.file_writer.py"
"""DiskBus · 文件写入统一入口.

覆盖 agent 常用能力之"磁盘写入". 收归散落的 `Path.write_text()` / `open('w')` / `json.dump()` 等.
读不走 bus (读不产生污染).

**基本审核** (拦明显危险, 非完备安全网):
  - 非目标路径: 拒写入系统敏感目录 (C:\\Windows / /etc / /usr/local 等)
  - 路径规范: 必须绝对路径, 必须落已知工作区内 (项目根 / 用户工作空间 / 临时目录)

**不管** (归 Guardian 合规规则):
  - _archive/ _graveyard/ 内部写入 (归档区规范)
  - 临时文件 / 空文件夹残留 (运行空间卫生)
  - 老化产物 (过期清理)
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Union

from omnicompany.core.config import omni_workspace_root
from omnicompany.runtime.buses.base import ServiceBus
from omnicompany.runtime.buses.workspace import Workspace

# 系统敏感目录黑名单 (大小写不敏感, 前缀匹配).
# 明确拦截: 不允许任何 agent 写入操作系统核心目录.
_SYSTEM_DENYLIST_PREFIXES = (
    # Windows
    "c:\\windows",
    "c:\\program files",
    "c:\\program files (x86)",
    "c:\\programdata",
    "c:\\boot",
    # Unix-like (即使在 Windows 上 Python 也可能遇到)
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/root",
    "/var/log",
    "/var/lib",
)

# 允许的工作区前缀 (大小写不敏感). 超出范围即视为"非目标路径".
#
# 工作区顶层 = omni 仓根的父目录 (开发机 = e:\windowsworkspace), 由权威解析器
# 派生而非写死. 额外的机器级工作区 (P4 工作区 / Users / Unix tmp) 通过
# 环境变量 OMNI_ALLOWED_WORKSPACE_PREFIXES (os.pathsep 分隔) 外置覆盖;
# 未配置时沿用开发机默认, 保证本机行为不变.
def _default_allowed_workspace_prefixes() -> tuple[str, ...]:
    prefixes: list[str] = [str(omni_workspace_root().parent)]
    env = os.environ.get("OMNI_ALLOWED_WORKSPACE_PREFIXES", "")
    if env:
        prefixes.extend(p for p in env.split(os.pathsep) if p)
    else:
        # 开发机兜底 (可被上面的 env 覆盖)
        prefixes.extend(["d:\\p4", "c:\\users"])
    # Unix-like tmp (跨平台始终允许)
    prefixes.extend(["/tmp", "/var/tmp", "/home"])
    return tuple(prefixes)


_DEFAULT_ALLOWED_WORKSPACE_PREFIXES = _default_allowed_workspace_prefixes()


def _normalize(path: Path) -> str:
    """返回规范化的小写绝对路径字符串, 用于前缀匹配."""
    resolved = path.resolve()
    return str(resolved).lower()


class DiskBus(ServiceBus):
    """文件写入总线.

    用法:
      bus = DiskBus()
      bus.write("/path/to/file.txt", "content")
      bus.append("/path/to/log.jsonl", '{"event": "x"}\\n')
    """

    bus_name = "disk"

    def __init__(
        self,
        audit_log_path: Path | None = None,
        extra_allowed_prefixes: tuple[str, ...] = (),
        *,
        workspace: Workspace | None = None,
    ):
        super().__init__(audit_log_path=audit_log_path, workspace=workspace)
        self._allowed_prefixes = tuple(
            p.lower() for p in (_DEFAULT_ALLOWED_WORKSPACE_PREFIXES + extra_allowed_prefixes)
        )

    def _precheck_path(self, action: str, path: Path) -> None:
        if not path.is_absolute():
            raise self._reject(
                action,
                "path must be absolute",
                {"path": str(path)},
            )
        norm = _normalize(path)
        # 1. 系统黑名单 (硬安全网, 即便有 workspace 也拦)
        for deny in _SYSTEM_DENYLIST_PREFIXES:
            if norm.startswith(deny):
                raise self._reject(
                    action,
                    f"system-sensitive path denied (prefix: {deny})",
                    {"path": str(path)},
                )
        # 2. 若声明 workspace, 只允许 workspace.write_prefixes (写紧)
        if self.workspace is not None:
            if self.workspace.allows_write(path):
                return
            raise self._reject(
                action,
                f"path outside workspace '{self.workspace.name}' write_prefixes",
                {
                    "path": str(path),
                    "workspace": self.workspace.name,
                    "write_prefixes": list(self.workspace.write_prefixes),
                },
            )
        # 3. Fallback: 没 workspace 声明, 走旧的 extra_allowed_prefixes + 临时目录
        tmp = tempfile.gettempdir().lower()
        if any(norm.startswith(p) for p in self._allowed_prefixes) or norm.startswith(tmp):
            return
        raise self._reject(
            action,
            "path outside known workspaces (declare workspace or add via extra_allowed_prefixes)",
            {"path": str(path), "allowed_prefixes": list(self._allowed_prefixes)},
        )

    def write(
        self,
        path: Union[str, Path],
        content: Union[str, bytes],
        *,
        atomic: bool = True,
        encoding: str = "utf-8",
    ) -> Path:
        """写入文件 · 默认原子写 (先写 tmp 再 rename).

        Returns: 写入的绝对 Path.
        """
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = target.resolve()
        self._precheck_path("write", target)
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "path": str(target),
            "bytes": len(content),
            "atomic": atomic,
            "mode": "binary" if isinstance(content, bytes) else "text",
        }

        if atomic:
            tmp_fd, tmp_name = tempfile.mkstemp(
                prefix=target.name + ".",
                suffix=".tmp",
                dir=str(target.parent),
            )
            try:
                with os.fdopen(tmp_fd, "wb" if isinstance(content, bytes) else "w", encoding=None if isinstance(content, bytes) else encoding) as fp:
                    fp.write(content)
                os.replace(tmp_name, target)
            except Exception:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        else:
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding=encoding)

        if isinstance(content, bytes):
            payload["sha256"] = hashlib.sha256(content).hexdigest()
        else:
            payload["sha256"] = hashlib.sha256(content.encode(encoding)).hexdigest()
        self._audit("write", payload)
        return target

    def append(
        self,
        path: Union[str, Path],
        content: Union[str, bytes],
        *,
        encoding: str = "utf-8",
    ) -> Path:
        """追加到文件 · 不做原子 (追加语义本身非原子)."""
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = target.resolve()
        self._precheck_path("append", target)
        target.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(content, bytes):
            with target.open("ab") as fp:
                fp.write(content)
        else:
            with target.open("a", encoding=encoding) as fp:
                fp.write(content)

        self._audit(
            "append",
            {
                "path": str(target),
                "bytes": len(content),
                "mode": "binary" if isinstance(content, bytes) else "text",
            },
        )
        return target

    def copy(self, src: Union[str, Path], dst: Union[str, Path]) -> Path:
        """复制文件 · src 只读无需审核, dst 走 write 审核."""
        src_p = Path(src).expanduser()
        dst_p = Path(dst).expanduser()
        if not dst_p.is_absolute():
            dst_p = dst_p.resolve()
        self._precheck_path("copy", dst_p)
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_p, dst_p)
        self._audit(
            "copy",
            {"src": str(src_p), "dst": str(dst_p), "bytes": dst_p.stat().st_size},
        )
        return dst_p

    def delete(self, path: Union[str, Path]) -> Path:
        """删除文件 · 也走审核避免误删系统文件."""
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = target.resolve()
        self._precheck_path("delete", target)
        existed = target.exists()
        if existed:
            target.unlink()
        self._audit(
            "delete",
            {"path": str(target), "existed": existed},
        )
        return target
