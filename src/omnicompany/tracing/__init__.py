# [OMNI] origin=human ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:tracing.package.exports.py"
"""omnicompany.tracing — 意图轨迹追踪子包

这是意图轨迹追踪功能的权威位置（六元语义接口规范 §6）。

公开接口：
    IntentTracer — 意图轨迹采集器，记录每次工具调用的语义意图
"""

from omnicompany.tracing.intent_tracer import IntentTracer  # noqa: F401

__all__ = ["IntentTracer"]
