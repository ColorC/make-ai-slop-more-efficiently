# [OMNI] origin=claude-code domain=runtime/buses ts=2026-04-23T00:00:00Z type=infrastructure
# [OMNI] material_id="material:runtime.buses.service_bus.base_class.py"
"""ServiceBus · 独立防控安全网 + 统一出口设施.

用户定位 (2026-04-23):
  1. **独立防控安全网** — ServiceBus 是 agent 操作 Disk/Web/Bash/Human 的唯一出口.
     每条业务 bus 通过 `precheck` + `BusRejection` 拦明显危险, 与后置合规 (Guardian) 独立
     构成双层防护: ServiceBus 管出口控制, Guardian 管散落+规范.
  2. **统一出口设施** — 所有 agent 新代码 MUST 走对应 bus, 不得直接 subprocess / open / requests.
     合规规则由 [`[2026-04-23]GUARDIAN-COMPLIANCE-HARDENING`] 第二/三波强制.
  3. **不负责存储** — ServiceBus 只处理 "审核 + 递交", 审计事件统一交给 `AuditEmitter`.
     存储归 `omnicompany.bus.EventBus` (事件总线), ServiceBus 自己不持有落盘实现.

AuditEmitter 的实现:
  - `EventBusAuditEmitter` (目标实现, A5 前对接): publish 到 EventBus 走 SQLiteBus 落盘
  - `LocalJsonlEmitter` (过渡 fallback, 当前默认): 写 `data/runtime/buses/audit.jsonl`
     纯粹是 EventBus 对接前的应急记录, **非 ServiceBus 职责**, 后续 phase 替换为前者

子类契约:
  - 业务方法内部: 先 `_precheck_*()` (基本审核拦明显危险), 再执行, 再 `_audit(action, payload)`
  - 明显危险 → `raise self._reject(...)` (封装审计 + 返回 BusRejection)
"""
from __future__ import annotations

import json
import os
import threading
import time
from abc import ABC
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from omnicompany.runtime.buses.workspace import Workspace


class BusError(Exception):
    """基础 bus 异常."""


class BusRejection(BusError):
    """基本审核拒绝 · 例: 写入系统敏感目录 / 执行 rm -rf /.

    reason 字段必须有, 指明拒绝的具体审核项.
    """

    def __init__(self, bus: str, action: str, reason: str, detail: dict | None = None):
        self.bus = bus
        self.action = action
        self.reason = (
            reason.strip()
            if isinstance(reason, str) and reason.strip()
            else "阻止层未提供具体理由"
        )
        self.detail = detail or {}
        context = _format_rejection_context(self.detail)
        super().__init__(f"[{bus}] {action} rejected: {self.reason}{context}")


_VISIBLE_REJECTION_DETAIL_KEYS = (
    "path",
    "paths",
    "cwd",
    "pattern",
    "checker",
    "workspace",
    "allowed_prefixes",
    "write_prefixes",
    "bash_cwd_prefixes",
)


def _format_rejection_context(detail: dict) -> str:
    """Expose bounded, useful policy context without echoing arbitrary payloads."""
    visible: list[str] = []
    for key in _VISIBLE_REJECTION_DETAIL_KEYS:
        value = detail.get(key)
        if isinstance(value, (str, int, float, bool)):
            safe_value = value
        elif isinstance(value, (list, tuple)) and all(
            isinstance(item, (str, int, float, bool)) for item in value
        ):
            safe_value = list(value)[:32]
        else:
            continue
        rendered = json.dumps(safe_value, ensure_ascii=False, separators=(",", ":"))
        visible.append(f"{key}={rendered[:1200]}")
    return f" | context: {', '.join(visible)}" if visible else ""


@dataclass
class AuditRecord:
    """bus 审计记录 · 一条关键动作一条."""

    bus: str  # "disk" | "web" | "bash" | "human"
    action: str  # "write" | "get" | "exec" | "ask" ...
    timestamp: float = field(default_factory=time.time)
    payload: dict = field(default_factory=dict)
    ok: bool = True
    rejection_reason: str | None = None

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


# ========== AuditEmitter 可插拔出口 ==========


@runtime_checkable
class AuditEmitter(Protocol):
    """审计事件出口 · ServiceBus 通过此接口递交事件, 不持有存储.

    目标实现: EventBusAuditEmitter (publish 到 omnicompany.bus.EventBus)
    过渡实现: LocalJsonlEmitter (本地 JSONL fallback)
    测试实现: InMemoryEmitter (用于单测)
    """

    def emit(self, record: AuditRecord) -> None:
        """递交一条审计事件. 必须非阻塞或短阻塞."""
        ...


def _resolve_default_audit_path() -> Path:
    """过渡 JSONL fallback 路径."""
    override = os.environ.get("OMNI_BUS_AUDIT_PATH")
    if override:
        return Path(override)
    cwd = Path.cwd()
    cursor = cwd
    for _ in range(6):
        if (cursor / "src" / "omnicompany").is_dir():
            break
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return cursor / "data" / "runtime" / "buses" / "audit.jsonl"


class LocalJsonlEmitter:
    """**过渡期 fallback** · 写本地 JSONL, 供 EventBus 对接前应急记录.

    **注意**: 这不是 ServiceBus 的职责边界 (ServiceBus 不负责存储),
    只是 EventBus 对接前的临时实现. A5 阶段对接 EventBus 后,
    默认 emitter 应替换为 `EventBusAuditEmitter`.
    """

    def __init__(self, path: Path | None = None):
        self._path = path or _resolve_default_audit_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, record: AuditRecord) -> None:
        line = record.to_jsonl()
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")

    def tail(self, limit: int = 50) -> list[AuditRecord]:
        """过渡期便利: 查最近 N 条. 正式 EventBus emitter 走 `EventBus.read_trace`."""
        if not self._path.exists():
            return []
        with self._path.open("r", encoding="utf-8") as fp:
            lines = fp.readlines()
        records: list[AuditRecord] = []
        for line in lines[-limit:]:
            try:
                data = json.loads(line)
                records.append(AuditRecord(**data))
            except (json.JSONDecodeError, TypeError):
                continue
        return records


class InMemoryEmitter:
    """测试用 · 内存 ring buffer, 不落盘."""

    def __init__(self, max_size: int = 1000):
        self._records: list[AuditRecord] = []
        self._max = max_size
        self._lock = threading.Lock()

    def emit(self, record: AuditRecord) -> None:
        with self._lock:
            self._records.append(record)
            if len(self._records) > self._max:
                self._records.pop(0)

    def tail(self, limit: int = 50) -> list[AuditRecord]:
        with self._lock:
            return list(self._records[-limit:])

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


# ========== ServiceBus 基类 ==========


class ServiceBus(ABC):
    """独立防控安全网 + 统一出口设施 · 四条业务 bus 共享基类.

    子类职责:
      - 业务方法调 `self._audit(action, payload, ok=True)` 记录成功动作
      - 基本审核失败 `raise self._reject(action, reason, detail)`
    """

    bus_name: str = "service"  # 子类必须覆盖

    def __init__(
        self,
        emitter: AuditEmitter | None = None,
        *,
        audit_log_path: Path | None = None,
        workspace: Workspace | None = None,
    ):
        """
        Args:
          emitter: 审计事件出口. 默认 None → 过渡 LocalJsonlEmitter.
                   A5 阶段应传 EventBusAuditEmitter 走 EventBus 落盘.
          audit_log_path: 仅 LocalJsonlEmitter 用. 传入非 None 时自动创建 LocalJsonlEmitter.
          workspace: 读写范围声明. 默认 None → 走子类自己的默认审核 (系统黑名单等).
                     子类 (DiskBus/BashBus) 优先用 workspace 做精细审核, workspace 未声明
                     时 fallback 到旧的 extra_allowed_prefixes / 系统黑名单.
        """
        if emitter is None:
            emitter = LocalJsonlEmitter(audit_log_path)
        self._emitter = emitter
        self.workspace: Workspace | None = workspace

    def _audit(self, action: str, payload: dict | None = None, *, ok: bool = True) -> AuditRecord:
        record = AuditRecord(
            bus=self.bus_name,
            action=action,
            payload=payload or {},
            ok=ok,
        )
        self._emitter.emit(record)
        return record

    def _reject(self, action: str, reason: str, detail: dict | None = None) -> BusRejection:
        """封装拒绝 + 审计 + 返回 exception (调用方 raise).

        用法: `raise self._reject(action, reason, detail)`
        """
        resolved_reason = (
            reason.strip() if isinstance(reason, str) and reason.strip() else "阻止层未提供具体理由"
        )
        record = AuditRecord(
            bus=self.bus_name,
            action=action,
            payload=detail or {},
            ok=False,
            rejection_reason=resolved_reason,
        )
        self._emitter.emit(record)
        return BusRejection(self.bus_name, action, resolved_reason, detail)

    def audit_tail(self, limit: int = 50) -> list[AuditRecord]:
        """便利方法: 查最近 N 条审计. 仅 emitter 支持 `tail` 时有效.

        正式 EventBus emitter 下走 EventBus query; 过渡期下走 LocalJsonlEmitter.tail().
        """
        if hasattr(self._emitter, "tail"):
            return self._emitter.tail(limit)
        return []
