# [OMNI] origin=claude-code ts=2026-08-17T00:00:00+08:00 type=module summary="hr 包内部共用: 按(字节偏移+行号+mtime+size)水位线流式逐行读 jsonl, 坏行容错计数" why="P1 五家 collector 都要对持续追加/可能被截断重写的 jsonl 做增量读, 都要同一套'绝不整份 read()/json.load()、坏行不中断、evidence_ref 行号不因增量读而错位'的语义, 抽出来给五棒共用, 避免各写一份" tags=hr,jsonl,watermark,internal
"""hr 包内部工具: jsonl 按水位线流式增量读取。

不进 `hr/__init__.py` 的 `__all__`(不对外导出), 仅供包内各 `*_source.py` 共用。

设计约束(P1-implementation-contract.md 第三节):
    * 只许流式逐行读, 禁止对整份文件 `.read()` / `json.load()`;
    * 最大单文件实测 codex 1658MB、kiro 620MB, 峰值 RSS 必须维持在 ~20MB 量级,
      即不允许把一份文件的新增内容整段读进内存再切行 —— 必须真正按行流式迭代。

水位线语义: 每个文件的水位线是一个 `FileWatermark`(偏移量 + 行号 + mtime + size)。
    - `offset`: 已消费字节数, 下次从这里 `seek()` 继续。
    - `lineno`: 已消费的物理行号(1-based, 含空行), 用于 `evidence_ref` 在多次增量读取之间
      保持与文件实际行号一致(不会因为"这批只读了新增部分"而从 1 重新计数)。
    - `mtime` / `size`: 用于两件事 —— (1) 两者都与上次记录的一致时判定"文件未变化",
      不必再 `open()`; (2) 当前 `size` 小于已记录 `offset` 时判定文件被截断/重建,
      保守地从头全量重读整个文件(不假设中间状态, 宁可重读一次也不漏记)。

坏行: 单行 json 解析失败(截断/非法 json)或解析结果不是 dict, 一律跳过并计数
(`JsonlTail.skipped`), 不抛异常、不中断整份文件的读取。

已知局限(与 token_ledger 参照实现一致的权衡, 见其 `_jsonl_util.py` 注释):
    若水位线恰好落在"写入者正在追加、尚未写完整行"的中间, 断点之后重新 seek 读到的
    首个"行"可能是半行, 会被判定为坏行并计入 `skipped`, 且水位线会越过这段半行数据
    —— 之后即便写入者补全了这一行, 也不会被重读。这是字节水位线增量读取的固有代价,
    不是本模块的 bug。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass
class FileWatermark:
    """单个 jsonl 文件的水位线状态, 字段语义见模块 docstring。"""

    offset: int = 0
    lineno: int = 0
    mtime: float = 0.0
    size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "FileWatermark":
        if not d:
            return cls()
        return cls(
            offset=int(d.get("offset", 0) or 0),
            lineno=int(d.get("lineno", 0) or 0),
            mtime=float(d.get("mtime", 0.0) or 0.0),
            size=int(d.get("size", 0) or 0),
        )


class JsonlTail:
    """对单个 jsonl 文件做水位线增量流式读取。

    用法::

        wm = FileWatermark.from_dict(watermark_state.get(str(path)))
        tail = JsonlTail(path, wm)
        for lineno, record in tail:
            ...  # 逐行处理, 绝不整份 read()/json.load()
        watermark_state[str(path)] = tail.watermark.to_dict()
        # tail.skipped   —— 本次新增内容里坏行数(json 解析失败或非 dict)
        # tail.is_empty  —— 本次判定文件为 0 字节
        # tail.error     —— 非 None 表示 stat/open 失败, 调用方应计入 files_failed

    `for lineno, record in tail` 只能安全迭代一次(生成器语义); 迭代结束后
    `tail.watermark` / `tail.skipped` / `tail.is_empty` / `tail.error` 才是最终值。
    """

    def __init__(self, path: Path, watermark: FileWatermark | None = None) -> None:
        self.path = path
        self.watermark = watermark or FileWatermark()
        self.skipped = 0
        self.is_empty = False
        self.error: OSError | None = None
        self.was_reset = False
        self.lines_read = 0

    def __iter__(self) -> Iterator[tuple[int, dict[str, Any]]]:
        wm = self.watermark
        try:
            st = self.path.stat()
        except OSError as exc:
            self.error = exc
            return
        size = st.st_size
        mtime = st.st_mtime

        if size == 0:
            self.is_empty = True
            self.was_reset = wm.offset > 0
            self.watermark = FileWatermark(offset=0, lineno=0, mtime=mtime, size=0)
            return

        # 快路径: 大小和 mtime 都未变 -> 判定无新增, 不必再 open() 这个文件。
        if wm.size == size and wm.mtime == mtime and wm.offset >= size:
            self.watermark = FileWatermark(offset=wm.offset, lineno=wm.lineno, mtime=mtime, size=size)
            return

        start_offset, start_lineno = wm.offset, wm.lineno
        if size < wm.offset:
            # 截断/重写: 不信任旧水位线, 全量重读。
            start_offset, start_lineno = 0, 0
            self.was_reset = True

        lineno = start_lineno
        offset = start_offset
        try:
            with self.path.open("rb") as fh:
                fh.seek(start_offset)
                for raw in fh:
                    offset += len(raw)
                    lineno += 1
                    self.lines_read += 1
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        self.skipped += 1
                        continue
                    if not isinstance(obj, dict):
                        self.skipped += 1
                        continue
                    yield lineno, obj
        except OSError as exc:
            self.error = exc
            return
        self.watermark = FileWatermark(offset=offset, lineno=lineno, mtime=mtime, size=size)


def iter_jsonl(path: Path, watermark: FileWatermark | None = None) -> JsonlTail:
    """便捷入口: 构造一个 `JsonlTail`。

    `for lineno, record in iter_jsonl(path, wm): ...`
    """
    return JsonlTail(path, watermark)


__all__ = ["FileWatermark", "JsonlTail", "iter_jsonl"]
