# [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-04-18T00:00:00Z
# [OMNI] material_id="material:diagnosis.semantic_auditor.standards_index_engine.python"
"""SemanticAuditor · 标准索引加载与摘录提取。

读 `docs/standards/standards-index.yaml` → 给 (kind, path) 返回适用的
standard id 列表 + 按 `excerpt_strategy` 提取该标准的内容摘录。

边界：
  - 不调 LLM（纯确定性）
  - 不 publish event（让 Router 层去处理，保持本模块可单测）
  - 解析失败 → raise；调用方负责兜底
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


_DEFAULT_INDEX_RELPATH = "docs/standards/standards-index.yaml"


@dataclass
class StandardEntry:
    """单条 standard 记录。对应 YAML 里的一项 `standards:` 条目。"""
    id: str
    file: str
    applies_to: list[str] = field(default_factory=list)
    path_match: list[str] = field(default_factory=list)
    excerpt_strategy: str = "full"
    key_sections: list[str] = field(default_factory=list)


@dataclass
class KindInferenceRule:
    kind: str
    match: list[str]


@dataclass
class StandardsIndex:
    """standards-index.yaml 的内存视图。"""
    standards: list[StandardEntry]
    kind_inference: list[KindInferenceRule]
    project_root: Path

    def get(self, standard_id: str) -> StandardEntry | None:
        for s in self.standards:
            if s.id == standard_id:
                return s
        return None


# ─── 加载 ────────────────────────────────────────────────────────

def load_standards_index(
    project_root: str | Path,
    index_relpath: str = _DEFAULT_INDEX_RELPATH,
) -> StandardsIndex:
    """读取 standards-index.yaml。

    Raises:
        FileNotFoundError: index 文件不存在
        ValueError: YAML 结构不符合预期
    """
    import yaml

    root = Path(project_root)
    index_path = root / index_relpath
    if not index_path.exists():
        raise FileNotFoundError(f"standards-index 不存在: {index_path}")

    try:
        raw = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"standards-index YAML 解析失败: {e}") from e

    if not isinstance(raw, dict):
        raise ValueError("standards-index 顶层必须是 mapping")

    standards_raw = raw.get("standards", [])
    if not isinstance(standards_raw, list):
        raise ValueError("standards-index.standards 必须是 list")

    standards: list[StandardEntry] = []
    for item in standards_raw:
        if not isinstance(item, dict) or "id" not in item or "file" not in item:
            raise ValueError(f"standard 条目缺少 id 或 file: {item}")
        standards.append(StandardEntry(
            id=str(item["id"]),
            file=str(item["file"]),
            applies_to=list(item.get("applies_to") or []),
            path_match=list(item.get("path_match") or []),
            excerpt_strategy=str(item.get("excerpt_strategy") or "full"),
            key_sections=list(item.get("key_sections") or []),
        ))

    ki_raw = raw.get("kind_inference", [])
    if not isinstance(ki_raw, list):
        raise ValueError("standards-index.kind_inference 必须是 list")

    rules: list[KindInferenceRule] = []
    for item in ki_raw:
        if not isinstance(item, dict) or "kind" not in item:
            raise ValueError(f"kind_inference 条目缺少 kind: {item}")
        rules.append(KindInferenceRule(
            kind=str(item["kind"]),
            match=list(item.get("match") or []),
        ))

    return StandardsIndex(standards=standards, kind_inference=rules, project_root=root)


# ─── glob 匹配 ──────────────────────────────────────────────────

@lru_cache(maxsize=256)
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """把带 ** 的 POSIX glob 翻译成 regex。

    规则：
      **/   匹配任意多段（含 0 段）路径前缀，如 a/b/
      **    匹配任意字符（含 /）
      *     匹配非 / 字符
      ?     匹配单个非 / 字符
    其他字符字面量（regex 特殊字符转义）。
    """
    pat = pattern.replace("\\", "/")
    parts: list[str] = []
    i = 0
    while i < len(pat):
        if pat[i:i + 3] == "**/":
            parts.append(r"(?:.*/)?")
            i += 3
        elif pat[i:i + 2] == "**":
            parts.append(r".*")
            i += 2
        elif pat[i] == "*":
            parts.append(r"[^/]*")
            i += 1
        elif pat[i] == "?":
            parts.append(r"[^/]")
            i += 1
        else:
            parts.append(re.escape(pat[i]))
            i += 1
    return re.compile("^" + "".join(parts) + "$")


def _match_glob(path: str, pattern: str) -> bool:
    """POSIX 风格路径 glob 匹配，支持 ** 跨段。"""
    p = path.replace("\\", "/")
    return bool(_glob_to_regex(pattern).match(p))


# ─── kind 推断 ──────────────────────────────────────────────────

def infer_kind(path: str, index: StandardsIndex) -> str | None:
    """按 kind_inference 首条匹配返回 kind；无匹配返回 None。"""
    p = path.replace("\\", "/")
    for rule in index.kind_inference:
        for pat in rule.match:
            if _match_glob(p, pat):
                return rule.kind
    return None


# ─── standard 匹配 ──────────────────────────────────────────────

def match_standards(kind: str | None, path: str, index: StandardsIndex) -> list[str]:
    """返回 (kind, path) 适用的 standard id 列表。

    匹配条件（AND）：
      1. standard.applies_to 包含 kind（如果给了 kind）或 path_match 命中即可
      2. path 与 standard.path_match 任一 glob 匹配
    """
    p = path.replace("\\", "/")
    hits: list[str] = []
    for s in index.standards:
        kind_ok = (kind is None) or (kind in s.applies_to) or (not s.applies_to)
        path_ok = any(_match_glob(p, pat) for pat in s.path_match) if s.path_match else False
        if kind_ok and path_ok:
            hits.append(s.id)
    return hits


# ─── excerpt 提取 ───────────────────────────────────────────────

def retrieve_excerpt(
    standard_id: str,
    index: StandardsIndex,
) -> str:
    """按 excerpt_strategy 读取 standard 内容。

    - full: 整份返回
    - section: 只取 key_sections 里列出的 ## 二级标题块（连同其内容到下一个 ## 之前）
    - 任何异常 → fallback full（宁可多喂不错漏）

    Raises:
        ValueError: standard_id 不存在
        FileNotFoundError: standard 文件不存在
    """
    entry = index.get(standard_id)
    if entry is None:
        raise ValueError(f"未知 standard_id: {standard_id}")

    file_path = index.project_root / entry.file
    if not file_path.exists():
        raise FileNotFoundError(f"standard 文件不存在: {file_path}")

    content = file_path.read_text(encoding="utf-8")

    if entry.excerpt_strategy == "full" or not entry.key_sections:
        return content

    if entry.excerpt_strategy == "section":
        extracted = _extract_sections(content, entry.key_sections)
        if not extracted.strip():
            logger.warning(
                "standard %s key_sections 全部未命中，fallback full",
                standard_id,
            )
            return content
        return extracted

    logger.warning(
        "standard %s 未知 excerpt_strategy=%s，fallback full",
        standard_id, entry.excerpt_strategy,
    )
    return content


def _extract_sections(content: str, section_titles: list[str]) -> str:
    """从 markdown 文本里提取 ## 二级标题块。

    匹配规则：section_title 应该就是 ## 开头的那行完整文本；
    一个 title 命中时，返回从该 ## 行到下一个 ## 或 EOF 之间的所有内容。
    """
    lines = content.splitlines()
    # 先定位所有 ## 开头的行
    section_starts: list[int] = [i for i, ln in enumerate(lines) if ln.startswith("## ")]

    out_parts: list[str] = []
    wanted = set(title.strip() for title in section_titles)

    for idx, start in enumerate(section_starts):
        header = lines[start].strip()
        if header not in wanted:
            continue
        end = section_starts[idx + 1] if idx + 1 < len(section_starts) else len(lines)
        out_parts.append("\n".join(lines[start:end]).rstrip())

    return "\n\n".join(out_parts)
