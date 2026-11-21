# [OMNI] origin=team-builder domain=services/csv_to_md/workers/csv_reader ts=2026-04-24T00:00:00Z type=worker
# [OMNI] material_id="material:utility.csv_to_md.csv_reader_implementation.py"
"""CsvReaderWorker — 读取 CSV 文件, 解析为 headers + rows 矩阵 (HARD).

Worker 协议:
  FORMAT_IN  = csv_to_md.file_input
  FORMAT_OUT = csv_to_md.parsed_rows

按照 rule_spec 逐步执行:
  1. 从 input_data 读取 {path, encoding}
  2. Path 验证文件存在性 → 不存在则 FAIL
  3. Path.read_text 读取全文 → UnicodeDecodeError 则 FAIL
  4. csv.reader 解析 → 空文件则 PARTIAL
  5. 提取首行 headers (strip), 余下为数据行
  6. 短行右补齐空串至 len(headers), 超长行截断, 每字段 strip
  7. Verdict(PASS, output={'headers': [...], 'rows': [[...], ...]})
"""
from __future__ import annotations

import csv
import logging
from io import StringIO
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

logger = logging.getLogger(__name__)


class CsvReaderWorker(Worker):
    """读取 CSV 文件, 解析为表头+数据行矩阵, 规范化每行长度与表头一致."""

    DESCRIPTION = "读取 CSV 文件, 解析表头和数据行, 规范化每行长度与表头一致, 输出结构化 parsed_rows 矩阵"
    FORMAT_IN = "csv_to_md.file_input"
    FORMAT_OUT = "csv_to_md.parsed_rows"

    def run(self, input_data: Any) -> Verdict:
        # ── Step 1: 读取 input_data 得 {path, encoding} ──────────────
        # 兼容两种输入形态: 嵌套在 FORMAT_IN 下 或 顶层直传
        payload = (
            input_data.get(self.FORMAT_IN)
            if isinstance(input_data, dict) and self.FORMAT_IN in input_data
            else input_data
        )
        if not isinstance(payload, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                diagnosis="CsvReaderWorker FAIL: input_data 不是 dict",
            )

        path_str = payload.get("path")
        if not path_str:
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                diagnosis="CsvReaderWorker FAIL: path 字段缺失",
            )

        encoding = payload.get("encoding", "utf-8")
        file_path = Path(path_str)

        # ── Step 2: 验证文件存在性 ───────────────────────────────────
        if not file_path.is_file():
            logger.error("CsvReaderWorker: 文件不存在: %s", path_str)
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                diagnosis=f"文件不存在: {path_str}",
            )

        # ── Step 3: 读取文件全文 ─────────────────────────────────────
        # 只读文件 → Path.read_text() 不过 Bus (铁律)
        try:
            content = file_path.read_text(encoding=encoding)
        except UnicodeDecodeError as e:
            logger.warning("CsvReaderWorker: 编码不匹配 %s: %s", path_str, e)
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                diagnosis=f"编码不匹配 ({encoding}): {e}",
            )
        except Exception as e:
            logger.error("CsvReaderWorker: 读取文件异常 %s: %s", path_str, e)
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                diagnosis=f"读取文件异常: {e}",
            )

        # ── Step 4: 使用 csv.reader 解析 ─────────────────────────────
        try:
            reader = csv.reader(StringIO(content))
            all_rows = list(reader)
        except csv.Error as e:
            logger.warning("CsvReaderWorker: CSV 解析异常: %s", e)
            return Verdict(
                kind=VerdictKind.PARTIAL,
                confidence=0.5,
                diagnosis=f"CSV 内容为空或格式异常: {e}",
            )

        if not all_rows:
            logger.warning("CsvReaderWorker: CSV 文件为空: %s", path_str)
            return Verdict(
                kind=VerdictKind.PARTIAL,
                confidence=0.5,
                diagnosis="CSV 内容为空或格式异常",
            )

        # ── Step 5: 提取首行为 headers (strip 去空白) ──────────────
        headers = [h.strip() for h in all_rows[0]]
        num_cols = len(headers)

        # ── Step 6: 数据规整 ────────────────────────────────────────
        rows: list[list[str]] = []
        for row in all_rows[1:]:
            # 每行字段均 strip
            cleaned = [field.strip() for field in row]
            # 短行右补齐空字符串至 len(headers)
            if len(cleaned) < num_cols:
                cleaned.extend([""] * (num_cols - len(cleaned)))
            # 超长行截断至 len(headers)
            elif len(cleaned) > num_cols:
                cleaned = cleaned[:num_cols]
            rows.append(cleaned)

        # ── Step 7: 输出 Verdict(PASS) ──────────────────────────────
        logger.info(
            "CsvReaderWorker PASS: %s → %d 列, %d 行",
            path_str, num_cols, len(rows),
        )
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "headers": headers,
                "rows": rows,
            },
            diagnosis=f"CsvReaderWorker PASS: {num_cols} 列, {len(rows)} 行, 编码={encoding}",
        )