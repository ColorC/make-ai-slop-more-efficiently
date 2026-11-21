# [OMNI] origin=team_builder domain=services/csv_to_md/workers/__init__ ts=2026-04-25T00:00:00Z type=config
# [OMNI] material_id="material:utility.csv_to_md.worker_exports.config.py"
"""csv_to_md Team · workers 子包导出."""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .csv_reader import CsvReaderWorker
from .markdown_writer import MarkdownWriterWorker

ALL_WORKERS: list[type[Worker]] = [CsvReaderWorker, MarkdownWriterWorker]

__all__ = ["CsvReaderWorker", "MarkdownWriterWorker", "ALL_WORKERS"]
