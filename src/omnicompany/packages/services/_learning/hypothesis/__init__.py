# [OMNI] origin=claude-code domain=services/hypothesis ts=2026-04-18T00:00:00Z type=module status=active
# [OMNI] material_id="material:services.learning.hypothesis.package.exports.py"
"""hypothesis — 假设-学习服务。

循环：外置只读 Experimenter → 外置零工具 Reflector → (loop)
真实多轮由 run_session 外部驱动；TeamSpec 仅声明单轮拓扑。

公开接口：
  - build_pipeline: TeamSpec 拓扑声明
  - build_bindings: Router 绑定
  - register_formats: 注册 4 个 Format 到 FormatRegistry
  - run_session / new_session: 外部驱动入口（当前实际执行路径）
"""

from omnicompany.packages.services._learning.hypothesis.formats import register_formats

__all__ = ["register_formats"]
