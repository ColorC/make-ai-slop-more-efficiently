# [OMNI] origin=claude-code domain=runtime/buses ts=2026-04-23T00:00:00Z type=infrastructure
# [OMNI] material_id="material:runtime.buses.module_aggregate.exports.py"
"""基础服务总线家族 — 统一 agent 四大常用能力.

与 `omnicompany.bus` (事件总线) 分工:
  - `omnicompany.bus.EventBus` — 审计底座, 全系统事件落盘
  - `omnicompany.runtime.buses.*` — 业务 bus, 每条对接一类 agent 能力,
    关键动作回流 EventBus

四条业务 bus (对应 agent 四大常用能力):
  - `DiskBus`   — 文件写入 (Path.write_text / open('w') 收归)
  - `WebBus`    — HTTP 请求 (requests / httpx / OpenAI SDK 收归)
  - `BashBus`   — subprocess 执行 (subprocess.run / Popen 收归)
  - `HumanBus`  — 人类审批 (暂存 / 核心层诊断 / 阻塞三类)

设计目的 (2026-04-23 用户明示):
  - 预防: 非目标路径 / 错误路径 / 危险指令 / 危险内容
  - 有序: 需人类审阅的材料和意见分类入 inbox, 不淹 L2
  - 审计: 所有关键动作回流 EventBus

安全网策略:
  - Disk / Bash: 路径+指令基本审核 (拦明显危险) + 全量审计
  - Web: 框架 + 基本原则审核, 暂不设安全网 (非长线独立工作)
  - Human: 无需安全网 (目的就是人类介入)
"""
from __future__ import annotations

# Re-export EventBus (审计底座) 方便统一从 runtime.buses 取所有 bus.
from omnicompany.bus import EventBus
from omnicompany.runtime.buses.base import (
    AuditEmitter,
    AuditRecord,
    BusError,
    BusRejection,
    InMemoryEmitter,
    LocalJsonlEmitter,
    ServiceBus,
)
from omnicompany.runtime.buses.bash_bus import BashBus
from omnicompany.runtime.buses.disk_bus import DiskBus
from omnicompany.runtime.buses.human_bus import (
    HumanBus,
    HumanKind,
    HumanQuestion,
    HumanTarget,
    NotifierProtocol,
    TARGET_ANY_HUMAN,
    TARGET_COLLEAGUE_FEISHU,
    TARGET_CORE_SELF_REPAIR,
    TARGET_L2_CLAUDE_CODE,
)
from omnicompany.runtime.buses.web_bus import WebBus
from omnicompany.runtime.buses.workspace import READ_ANY, Workspace, for_package, load_workspace

__all__ = [
    "EventBus",
    "ServiceBus",
    "AuditEmitter",
    "AuditRecord",
    "LocalJsonlEmitter",
    "InMemoryEmitter",
    "BusError",
    "BusRejection",
    "DiskBus",
    "WebBus",
    "BashBus",
    "HumanBus",
    "HumanKind",
    "HumanQuestion",
    "HumanTarget",
    "NotifierProtocol",
    "TARGET_ANY_HUMAN",
    "TARGET_COLLEAGUE_FEISHU",
    "TARGET_CORE_SELF_REPAIR",
    "TARGET_L2_CLAUDE_CODE",
    "Workspace",
    "READ_ANY",
    "for_package",
    "load_workspace",
]
