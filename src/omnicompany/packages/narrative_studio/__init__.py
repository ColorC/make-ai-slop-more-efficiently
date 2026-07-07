"""narrative_studio — headless 叙事引擎设施 + 网页审阅端。

按 vilo wiki 05/07/08 的格式与交互设计实现。本包刻意与 UI 解耦:
models/storage/importer/projections/health/playthrough/queries 是纯库,
api 是薄 HTTP 层,webui 是独立前端。

权威设计:故事/vilo-wants-to-know/wiki/{05,07,08}.md
"""

from __future__ import annotations

__version__ = "0.1.0"

from .models import Project  # noqa: F401
