# [OMNI] origin=claude-code domain=omnicompany/core/remote ts=2026-10-17T00:00:00+08:00 type=lib status=active
# [OMNI] summary="omni remote 节点画像: 读 config/remote_nodes.toml, 按名字/别名/node_id 解析节点。"
"""Node profiles for ``omni remote`` (``config/remote_nodes.toml``)."""
from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

from omnicompany.core.config import omni_workspace_root

from .pstext import RemoteRefused

EXEC_CHANNELS = {"desktop-node"}
FILE_CHANNELS = {"http-pull", "scp", "local-copy"}


@dataclass(frozen=True)
class NodeProfile:
    name: str
    node_id: str
    aliases: tuple[str, ...]
    hostname: str
    ssh_alias: str
    exec_channel: str
    exec_mode: str
    readonly_mode: str
    l1_ops: tuple[str, ...]
    shell_program: str
    shell_path: str
    file_channel: str
    forbid_encoded_payload: bool
    write_roots: tuple[str, ...]
    staging_root: str
    jobs_root: str
    backup_root: str
    default_cwd: str
    env: dict[str, str] = field(default_factory=dict)
    http_pull_host: str = ""
    http_pull_ports: tuple[int, int] = (41000, 41999)
    #: where ``omni remote share`` puts files for the user to open on that machine (empty = not set; always writable)
    share_root: str = ""
    #: ``[nodes.<name>.occupancy]``: machine occupancy table / admission / watchdog policy (empty = off)
    occupancy: dict[str, Any] = field(default_factory=dict)

    @property
    def occupancy_root(self) -> str:
        return ntpath.join(self.staging_root, "occupancy")

    @property
    def owned_roots(self) -> list[str]:
        return [self.staging_root, self.jobs_root, self.backup_root]

    @property
    def runs_root(self) -> str:
        return ntpath.join(self.staging_root, "runs")

    def names(self) -> list[str]:
        return [self.name, self.node_id, self.hostname, self.ssh_alias, *self.aliases]

    def as_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["aliases"] = list(self.aliases)
        data["l1_ops"] = list(self.l1_ops)
        data["write_roots"] = list(self.write_roots)
        data["http_pull_ports"] = list(self.http_pull_ports)
        return data


def config_path() -> Path:
    raw = os.environ.get("OMNI_REMOTE_CONFIG", "").strip()
    return Path(raw) if raw else omni_workspace_root() / "config" / "remote_nodes.toml"


def _norm_root(value: str, *, key: str, node: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RemoteRefused("profile", f"节点画像 {node} 缺少 {key}")
    if not ntpath.isabs(text) or not ntpath.splitdrive(text)[0]:
        raise RemoteRefused("profile", f"节点画像 {node} 的 {key} 必须是带盘符的绝对路径: {text}")
    return ntpath.normpath(text)


def _profile(name: str, raw: dict[str, Any]) -> NodeProfile:
    exec_channel = str(raw.get("exec_channel") or "desktop-node")
    file_channel = str(raw.get("file_channel") or "http-pull")
    if exec_channel not in EXEC_CHANNELS:
        raise RemoteRefused("profile", f"节点画像 {name} 的 exec_channel 不认识: {exec_channel}")
    if file_channel not in FILE_CHANNELS:
        raise RemoteRefused("profile", f"节点画像 {name} 的 file_channel 不认识: {file_channel}")
    if file_channel == "scp" and not raw.get("ssh_alias"):
        raise RemoteRefused("profile", f"节点画像 {name} 用 scp 文件通道但没有 ssh_alias")
    ports = raw.get("http_pull_ports") or [41000, 41999]
    write_roots = tuple(_norm_root(r, key="write_roots", node=name) for r in raw.get("write_roots") or [])
    share_root = _norm_root(raw["share_root"], key="share_root", node=name) if str(raw.get("share_root") or "").strip() else ""
    if share_root and share_root.lower() not in {r.lower() for r in write_roots}:
        write_roots += (share_root,)
    if not write_roots:
        raise RemoteRefused("profile", f"节点画像 {name} 至少要有一个 write_roots")
    default_cwd = str(raw.get("default_cwd") or "").strip()
    return NodeProfile(
        name=name,
        node_id=str(raw.get("node_id") or "").strip(),
        aliases=tuple(str(a) for a in raw.get("aliases") or []),
        hostname=str(raw.get("hostname") or ""),
        ssh_alias=str(raw.get("ssh_alias") or ""),
        exec_channel=exec_channel,
        exec_mode=str(raw.get("exec_mode") or "trusted-exec-l2"),
        readonly_mode=str(raw.get("readonly_mode") or ""),
        l1_ops=tuple(str(op) for op in raw.get("l1_ops") or []),
        shell_program=str(raw.get("shell_program") or "powershell"),
        shell_path=str(raw.get("shell_path") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
        file_channel=file_channel,
        forbid_encoded_payload=bool(raw.get("forbid_encoded_payload", False)),
        write_roots=write_roots,
        staging_root=_norm_root(raw.get("staging_root", ""), key="staging_root", node=name),
        jobs_root=_norm_root(raw.get("jobs_root", ""), key="jobs_root", node=name),
        backup_root=_norm_root(raw.get("backup_root", ""), key="backup_root", node=name),
        default_cwd=ntpath.normpath(default_cwd) if default_cwd else "",
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        http_pull_host=str(raw.get("http_pull_host") or ""),
        http_pull_ports=(int(ports[0]), int(ports[1])),
        share_root=share_root,
        occupancy=dict(raw.get("occupancy") or {}),
    )


def load_profiles(path: Path | None = None) -> dict[str, NodeProfile]:
    path = path or config_path()
    if not path.is_file():
        raise RemoteRefused("profile", f"没有节点画像文件: {path}（或用 OMNI_REMOTE_CONFIG 指定）")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    nodes = data.get("nodes") or {}
    if not isinstance(nodes, dict) or not nodes:
        raise RemoteRefused("profile", f"节点画像文件里没有 [nodes.*]: {path}")
    return {name: _profile(name, raw) for name, raw in nodes.items()}


def resolve(name: str, profiles: dict[str, NodeProfile] | None = None) -> NodeProfile:
    profiles = profiles if profiles is not None else load_profiles()
    key = name.strip().lower()
    hits = [p for p in profiles.values() if key in {n.lower() for n in p.names() if n}]
    if not hits:
        hits = [p for p in profiles.values() if p.node_id and p.node_id.lower().startswith(key) and len(key) >= 12]
    if len(hits) == 1:
        return hits[0]
    known = ", ".join(sorted(profiles)) or "(无)"
    if not hits:
        raise RemoteRefused("profile", f"没有叫 {name} 的节点画像；已有: {known}（见 {config_path()}）")
    raise RemoteRefused("profile", f"{name} 同时命中多个节点画像: {', '.join(p.name for p in hits)}")
