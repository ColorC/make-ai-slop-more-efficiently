# [OMNI] origin=claude-code domain=evolution/workflow ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.package_aggregator.py"
"""新进化工作流 — 假设驱动，类人学习

取代 auto_evolve.py 的思路，不是代码级兼容替换。

流程：
  Pain Signal → B.1 浅层追踪 → HypothesisBoard
    → B.2 深度诊断 (focus hypothesis)
    → B.3 受控实验 (modification lock)
    → B.4 结果分析
    → B.5 迭代 (更新黑板，选下一个 focus)
"""
