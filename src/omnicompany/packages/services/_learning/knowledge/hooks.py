# [OMNI] origin=claude-code domain=services/knowledge/hooks ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:learning.knowledge.periodic_audit_hook.py"
"""omnikb.hooks — OmniKB 的可选 Hook, 用于 sentinel daemon 周期触发。

提供:
  - KBAuditHook (PeriodicHook): 每 N 小时跑一次 omnikb-audit, 把 issue 数量
    汇成 Signal 推到 EventBus, 不阻塞主 patrol。

设计原则:
  - **不接管 guardian patrol** — guardian 已有自己的 OMNI-017/018 规则,
    我们不去抢这两个 ID; KB 自身的健康靠 omnikb-audit 独立 pipeline 检查
  - **可选启用** — 默认不挂载到 sentinel, 用户/管理员主动调
    `KBAuditHook().run_sync()` 或在 sentinel 配置里加进去
  - **结果只发 Signal, 不动文件** — warn-only

用法:
    from omnicompany.packages.services._learning.knowledge.hooks import KBAuditHook
    hook = KBAuditHook(interval_seconds=3600)
    result = hook.run_sync()  # 同步跑一次
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from omnicompany.protocol.hook import PeriodicHook

if TYPE_CHECKING:
    from omnicompany.protocol.signal import Signal

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = Path("e:/WindowsWorkspace/omnicompany")


class KBAuditHook(PeriodicHook):
    """周期性 KB 一致性审计 Hook。

    每 INTERVAL_SECONDS 跑一次 ``run_full_audit`` (来自 ``omnikb.audit``),
    把 5 类一致性问题汇成 Signal 推到 EventBus。

    与 guardian patrol（GuardianPeriodicHook）的关系:
      - guardian patrol 扫源代码漂移 (OMNI-001 ~ OMNI-021)
      - KBAuditHook 扫知识库本身的漂移 (KB entry 之间的引用 / code anchors / coverage)
      - 两者**互不重叠**也**互不依赖**, 可以独立或同时运行
    """

    INTERVAL_SECONDS: int = 3600  # 1 小时

    def __init__(
        self,
        project_root: str | Path = _DEFAULT_ROOT,
        interval_seconds: int | None = None,
    ) -> None:
        self._root = Path(project_root)
        self._interval = interval_seconds or self.INTERVAL_SECONDS
        self._last_run_ts: float = 0.0  # 0 = 从未运行, 启动时立即触发一次

    # ── PeriodicHook 接口 ────────────────────────────────────────

    def should_poll(self, round_num: int) -> bool:
        return (time.time() - self._last_run_ts) >= self._interval

    async def poll(self, db_path: str, round_num: int) -> "list[Signal]":
        """异步触发一次 KB 审计, 返回 Signal 列表。

        ``db_path`` / ``round_num`` 仅用于日志, omnikb-audit 不依赖事件库。
        """
        self._last_run_ts = time.time()
        logger.info("KBAuditHook: 触发 KB 审计 (round=%d)", round_num)

        try:
            result = self._do_audit()
        except Exception as e:
            logger.error("KBAuditHook: KB 审计失败: %s", e)
            return []

        return _build_signals(result, round_num)

    # ── 同步入口 (供 sentinel daemon / CLI 调用) ──────────────────

    def run_sync(self) -> dict:
        """同步跑一次 KB 审计, 返回原始结果 dict。"""
        self._last_run_ts = time.time()
        return self._do_audit()

    # ── 内部 ──────────────────────────────────────────────────────

    def _do_audit(self) -> dict:
        from omnicompany.packages.services._learning.knowledge.audit import run_full_audit

        report = run_full_audit(self._root)
        return {
            "summary": report.summary(),
            "has_issues": report.has_issues(),
            "validation_count": len(report.validation_issues),
            "drift_count": len(report.anchor_drifts),
            "orphan_count": len(report.orphan_routers),
            "stale_count": len(report.staleness.stale_krouters),
            "coverage_code_only": len(report.format_coverage.code_only),
            "coverage_knowledge_only": len(report.format_coverage.knowledge_only),
            # 详细数据保留, 给 Signal meta 用
            "validation_issues": [
                {"id": i.entry_id, "field": i.field,
                 "message": i.message, "severity": i.severity}
                for i in report.validation_issues
            ],
            "anchor_drifts": [
                {"karch_id": d.karch_id, "anchor": d.anchor, "reason": d.reason}
                for d in report.anchor_drifts
            ],
        }


def _build_signals(result: dict, round_num: int) -> "list[Signal]":
    """把 audit 结果转 Signal。无问题返回空 list。"""
    from omnicompany.protocol.signal import Signal

    if not result.get("has_issues"):
        logger.debug("KBAuditHook: 本轮无问题 (round=%d)", round_num)
        return []

    text = (
        f"OmniKB 审计发现 "
        f"validation={result['validation_count']}, "
        f"drift={result['drift_count']}, "
        f"orphan={result['orphan_count']}, "
        f"stale={result['stale_count']}, "
        f"format-coverage gap (code only)={result['coverage_code_only']}. "
        f"详见 omni run omnikb-audit"
    )

    return [Signal(
        format="kb_audit_signal",
        text=text,
        node_id="kb_audit_hook",
        meta={
            "round": round_num,
            "summary": result["summary"],
            "validation_count": result["validation_count"],
            "drift_count": result["drift_count"],
            "orphan_count": result["orphan_count"],
            "stale_count": result["stale_count"],
            "coverage_code_only": result["coverage_code_only"],
            "coverage_knowledge_only": result["coverage_knowledge_only"],
            "validation_issues": result["validation_issues"],
            "anchor_drifts": result["anchor_drifts"],
        },
    )]
