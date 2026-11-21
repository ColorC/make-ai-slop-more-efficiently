# [OMNI] origin=team_builder domain=services/csv_to_md/run ts=2026-04-25T00:00:00Z type=config
# [OMNI] material_id="material:utility.csv_to_md.binding_composer.config.py"
"""csv_to_md Team · build_bindings (team_builder 自动产出)."""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.format import create_builtin_registry

from .formats import register_formats  # 相对 import · 支持 tmp smoke + 正式部署两场景

from .workers.csv_reader import CsvReaderWorker
from .workers.markdown_writer import MarkdownWriterWorker


def build_bindings(input_dict: dict | None = None) -> dict[str, Worker]:
    """构建 csv_to_md 节点绑定."""
    registry = create_builtin_registry()
    register_formats(registry)
    return {
        "CsvReaderWorker": CsvReaderWorker(),
        "MarkdownWriterWorker": MarkdownWriterWorker(),
    }
