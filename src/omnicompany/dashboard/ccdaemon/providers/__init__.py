# [OMNI] origin=ai-ide ts=2026-05-10 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.providers.package_init.py"
"""ccdaemon/providers/ — LLM provider 抽象层.

各 provider 子类继承 `BaseProvider` (base.py), 实现自家 SDK / CLI 调用 + 把原始消息
转为 `NormalizedMessage` (normalized_protocol.py). ChatSession 通过 BaseProvider
接口跟具体 provider 交互, 不直接 import 任何 LLM SDK.

层级
----
- 阶段 1 (本): base.py BaseProvider ABC + ProviderOptions
- 阶段 2: claude.py ClaudeProvider (现 chat.py SDK 路径迁入)
- 阶段 6: codex.py / kimi.py / opencode.py (已实现); cursor.py (留 stub, 真接入另立 plan)

不允许从这里 re-export 旧的 chat.py 实现 (强制走 providers.X 路径).
"""

from .base import BaseProvider, ProviderOptions

__all__ = ["BaseProvider", "ProviderOptions"]
