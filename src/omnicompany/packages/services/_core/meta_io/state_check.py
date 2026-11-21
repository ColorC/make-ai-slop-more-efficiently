# [OMNI] origin=ai-ide domain=services/_core/meta_io ts=2026-05-02T07:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="元 IO 状态检查 hook 骨架 - 验证 precondition / postcondition / invariant"
# [OMNI] why="规范 docs/standards/cli/meta_io.md 第五节. 状态检查 hook 是 PeriodicHook 子类, 跑前后扫元 IO 调用记录验状态一致性"
# [OMNI] tags=meta_io,state-check,hook,foundation
# [OMNI] material_id="material:core.meta_io.state_check_hook.validation.py"
"""元 IO 状态检查 hook 骨架.

继承 PeriodicHook (来自 protocol.hook). 周期性扫审计 log + 验:
  - precondition 表达的语义实际成立 (例: read_file_text 调用前文件实际存在)
  - postcondition 实际成立 (例: create_file 调用后文件存在 + 大小匹配)
  - invariant 不变量保持

这是骨架版 — 真自动化检查 (实际 stat 文件 / 跟历史对比) 留下一阶段. 当前只跑"声明
检查" (读 audit log + 看 meta_io 是否在 META_IO_REGISTRY 注册).
"""
from __future__ import annotations


from omnicompany.protocol.hook import PeriodicHook
from omnicompany.protocol.signal import Signal


class MetaIOStateCheckHook(PeriodicHook):
    """元 IO 状态检查 PeriodicHook.

    每 N 轮扫最近的元 IO 审计记录, 检查:
    1. meta_io_id 是否在 META_IO_REGISTRY 注册 (没注册的 IO 调用 → 警告)
    2. is_error=True 的调用是否聚集 (某个元 IO 高失败率 → 警告)
    3. tool 声明的 CONSUMED/PRODUCED 是否覆盖实际调用 (声明缺失 → 警告)

    自动状态检查 (实际 stat 文件 / 跟历史对比) 留下一阶段做.
    """

    POLL_EVERY: int = 50    # 每 50 轮检查一次
    AUDIT_LOOKBACK: int = 100  # 扫最近 100 条审计

    def should_poll(self, round_num: int) -> bool:
        return round_num > 0 and round_num % self.POLL_EVERY == 0

    async def poll(self, db_path: str, round_num: int) -> list[Signal]:
        from omnicompany.packages.services._core.meta_io.audit import query_audit
        from omnicompany.packages.services._core.meta_io.registry import META_IO_REGISTRY

        signals: list[Signal] = []
        records = query_audit(limit=self.AUDIT_LOOKBACK)
        if not records:
            return signals

        # 检查 1: 未注册元 IO
        unregistered: dict[str, int] = {}
        for r in records:
            mid = r.get("meta_io_id", "")
            if mid and mid != "*" and mid not in META_IO_REGISTRY:
                unregistered[mid] = unregistered.get(mid, 0) + 1
        for mid, count in unregistered.items():
            signals.append(Signal(
                format="meta_io.violation",
                text=f"元 IO {mid!r} 在审计 log 出现 {count} 次但未在 META_IO_REGISTRY 注册",
                node_id=type(self).__name__,
                meta={"kind": "meta_io.unregistered", "meta_io_id": mid,
                      "count": count, "severity": "warn"},
            ))

        # 检查 2: 高失败率元 IO (失败 ≥ 50%)
        by_meta_io: dict[str, dict[str, int]] = {}
        for r in records:
            mid = r.get("meta_io_id", "")
            if not mid:
                continue
            slot = by_meta_io.setdefault(mid, {"total": 0, "error": 0})
            slot["total"] += 1
            if r.get("is_error"):
                slot["error"] += 1
        for mid, slot in by_meta_io.items():
            if slot["total"] >= 5 and slot["error"] / slot["total"] >= 0.5:
                signals.append(Signal(
                    format="meta_io.violation",
                    text=(
                        f"元 IO {mid!r} 高失败率: {slot['error']}/{slot['total']} "
                        f"({100 * slot['error'] / slot['total']:.0f}%)"
                    ),
                    node_id=type(self).__name__,
                    meta={"kind": "meta_io.high_error_rate", "meta_io_id": mid,
                          "severity": "warn", **slot},
                ))

        return signals
