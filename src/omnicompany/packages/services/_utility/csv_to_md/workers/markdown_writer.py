# [OMNI] origin=claude-code domain=services/csv_to_md/workers/markdown_writer ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:utility.csv_to_md.markdown_writer_implementation.py"
"""MarkdownWriterWorker — 将 parsed_rows 转为 GFM Markdown 表格并落盘 (HARD).

Worker 协议:
  FORMAT_IN  = csv_to_md.parsed_rows
  FORMAT_OUT = csv_to_md.md_output

规则 (rule_spec):
  1. 从 csv_to_md.parsed_rows 取 headers 和 rows;
  2. headers 为空 → 路由 FAIL (diagnosis: 'no headers');
  3. 对每个 cell 执行 GFM 转义: | → \|, \n/\r → <br>, 首尾去空格;
  4. 构建 GFM 表头 / 对齐分隔行 / 数据行;
  5. 列数不一致 → 路由 PARTIAL (diagnosis: 'column count mismatch');
  6. 通过 DiskBus.write 落盘, 产出 csv_to_md.md_output: {content: str};
  7. 全部行通过校验 → 路由 PASS.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.buses.disk_bus import DiskBus

logger = logging.getLogger(__name__)


class MarkdownWriterWorker(Worker):
    """将 parsed_rows (headers + rows) 转为 GFM Markdown 表格并写入磁盘."""

    DESCRIPTION = "将 CSV 解析后的 headers/rows 转为 GFM Markdown 表格，执行单元格转义后通过 DiskBus 落盘"
    FORMAT_IN = "csv_to_md.parsed_rows"
    FORMAT_OUT = "csv_to_md.md_output"

    @staticmethod
    def _gfm_escape(cell: str) -> str:
        """对单个 cell 执行 GFM 表格转义: 去首尾空格 → 换行替换 → 管道转义."""
        s = cell.strip()
        # \r\n → <br> (先处理 \r\n 避免变成 <br><br>)
        s = s.replace("\r\n", "<br>")
        # \n → <br>
        s = s.replace("\n", "<br>")
        # \r → <br> (孤立 \r)
        s = s.replace("\r", "<br>")
        # | → \| (GFM 管道转义)
        s = s.replace("|", "\\|")
        return s

    def run(self, input_data: Any) -> Verdict:
        # ── 1. 解析输入: 兼容 payload 嵌套和直接平铺两种形式 ──
        if isinstance(input_data, dict) and self.FORMAT_IN in input_data:
            payload = input_data[self.FORMAT_IN]
        else:
            payload = input_data if isinstance(input_data, dict) else {}

        headers: list[str] = payload.get("headers", [])
        rows: list[list[str]] = payload.get("rows", [])

        # ── 2. 校验 headers 非空 ──
        if not headers:
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={"content": ""},
                diagnosis="no headers",
            )

        num_cols = len(headers)

        # ── 3. GFM 转义表头 ──
        escaped_headers = [self._gfm_escape(h) for h in headers]
        header_line = "| " + " | ".join(escaped_headers) + " |"

        # ── 4. 构建对齐分隔行 (默认左对齐) ──
        separator_line = "| " + " | ".join("---" for _ in range(num_cols)) + " |"

        # ── 5. 构建数据行 + 校验列数一致性 ──
        data_lines: list[str] = []
        mismatch_indices: list[int] = []

        for row_idx, row in enumerate(rows):
            if len(row) != num_cols:
                mismatch_indices.append(row_idx)
                continue
            escaped_cells = [self._gfm_escape(c) for c in row]
            data_line = "| " + " | ".join(escaped_cells) + " |"
            data_lines.append(data_line)

        # ── 6. 若存在列数不匹配 → 路由 PARTIAL ──
        if mismatch_indices:
            # 仍然组装已通过的行 (用于重试上下文)
            parts = [header_line, separator_line] + data_lines
            content = "\n".join(parts) + "\n"
            return Verdict(
                kind=VerdictKind.PARTIAL,
                confidence=1.0,
                output={"content": content},
                diagnosis=f"column count mismatch at rows: {mismatch_indices}",
            )

        # ── 7. 组装完整 GFM 表格 ──
        parts = [header_line, separator_line] + data_lines
        content = "\n".join(parts) + "\n"

        # ── 8. 通过 DiskBus 落盘 ──
        output_path_str = input_data.get("output_path") if isinstance(input_data, dict) else None
        if output_path_str:
            output_path = Path(output_path_str)
        else:
            # fallback: 使用输入 path 推导 .md 路径
            input_path = input_data.get("path", "") if isinstance(input_data, dict) else ""
            if input_path:
                output_path = Path(input_path).with_suffix(".md")
            else:
                output_path = Path("output.md")

        logger.info("MarkdownWriterWorker: writing %s (%d bytes)", output_path, len(content))
        disk_bus = DiskBus()
        disk_bus.write(str(output_path), content)

        # ── 9. 路由 PASS ──
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={"content": content},
        )