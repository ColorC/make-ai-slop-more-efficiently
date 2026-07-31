# [OMNI] origin=claude-code domain=services/knowledge/store.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:learning.knowledge.distributed_filetree.storage.py"
"""omnikb.store — 分布式 Markdown 文件树的读写层。

扫描 3 类根路径:
  1. data/knowledge/                               — 跨 package 共享的长期资产
  2. src/omnicompany/packages/*/knowledge/         — 挂在业务包下的领域知识
  3. src/omnicompany/packages/*/*/knowledge/       — 二级业务包 (如 domains/demogame/knowledge)

目录命名约定 (非强制, 但推荐遵守):
  data/knowledge/
    architecture/<topic>.md           — KArchitectureEntry (scope=omnicompany)
    decisions/<topic>.md              — KDecisionEntry
    experiments/<date>_<topic>.md     — KExperimentEntry
    external_repos/<owner>__<name>.md — KRepoArchitectEntry
    formats/<domain>/<name>.md        — KFormatEntry (通用)
    routers/<domain>/<name>.md        — KRouterEntry (通用)

  packages/<ns>/<pkg>/knowledge/
    formats/<name>.md                 — 业务特有 KFormatEntry
    routers/<name>.md                 — 业务特有 KRouterEntry

写入时自动推断目录: entry 的 omnikb_type 决定顶级目录, domain tag 决定二级。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from omnicompany.packages.services._learning.knowledge.schema import (
    KnowledgeEntry,
    parse_kb_document,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 常量: 顶级目录命名对照
# ═══════════════════════════════════════════════════════════

# omnikb_type → data/knowledge/ 下的顶级目录名
_DATA_TYPE_DIR = {
    "karch": "architecture",
    "kdec": "decisions",
    "kexp": "experiments",
    "krepo": "external_repos",
    "kformat": "formats",
    "krouter": "routers",
}

_EXCLUDED_DIR_COMPONENTS = {
    "__pycache__",
    "_graveyard",
    "_archive",
    "node_modules",
    ".git",
}


# ═══════════════════════════════════════════════════════════
# KBStore
# ═══════════════════════════════════════════════════════════

class KBStore:
    """读写 OmniKB 文件树。

    Args:
        project_root: 仓库根目录 (含 src/ 和 data/)
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.data_root = self.project_root / "data" / "knowledge"
        self.src_packages = self.project_root / "src" / "omnicompany" / "packages"

    # ── 枚举 ──────────────────────────────────────────────

    def iter_all_paths(self) -> Iterator[Path]:
        """遍历所有知识 md 文件的路径, 跳过下划线文件与排除目录。"""
        # 1. data/knowledge/
        if self.data_root.exists():
            yield from self._walk_md(self.data_root)

        # 2. packages/<ns>/<pkg>/knowledge/ (含任意深度嵌套)
        if self.src_packages.exists():
            for knowledge_dir in self.src_packages.rglob("knowledge"):
                if not knowledge_dir.is_dir():
                    continue
                if any(c in _EXCLUDED_DIR_COMPONENTS for c in knowledge_dir.parts):
                    continue
                yield from self._walk_md(knowledge_dir)

    def _walk_md(self, root: Path) -> Iterator[Path]:
        for p in root.rglob("*.md"):
            if p.name.startswith("_"):
                continue
            if any(c in _EXCLUDED_DIR_COMPONENTS for c in p.parts):
                continue
            yield p

    def iter_all_entries(self) -> Iterator[KnowledgeEntry]:
        """遍历并解析所有合法知识条目 (跳过无法解析的)。"""
        for path in self.iter_all_paths():
            entry = parse_kb_document(path)
            if entry is not None:
                yield entry

    # ── 查找 ──────────────────────────────────────────────

    def find_by_id(self, kb_id: str) -> Path | None:
        """按 id 定位 md 文件, 找不到返回 None。

        线性扫描, 对于大规模需要通过 KBIndex 加速。
        """
        for path in self.iter_all_paths():
            entry = parse_kb_document(path)
            if entry is not None and entry.id == kb_id:
                return path
        return None

    def read_entry(self, kb_id: str) -> KnowledgeEntry | None:
        """读取并解析一个条目, 找不到返回 None。"""
        path = self.find_by_id(kb_id)
        if path is None:
            return None
        return parse_kb_document(path)

    def read_raw(self, kb_id: str) -> str | None:
        """读取条目的原始 markdown 文本。"""
        path = self.find_by_id(kb_id)
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    # ── 写入 ──────────────────────────────────────────────

    def write_entry(
        self,
        entry: KnowledgeEntry,
        body: str = "",
        *,
        overwrite: bool = False,
    ) -> Path:
        """写入或更新一个条目。

        - 已存在同 id 条目时, overwrite=False 只更新 frontmatter 保留正文;
          overwrite=True 整体覆盖。
        - 新条目按 _infer_write_dir 推断路径, 写入由 guarded_write 经过审计。
        """
        existing = self.find_by_id(entry.id)
        if existing and existing.exists():
            if overwrite:
                content = _render_entry(entry, body)
            else:
                existing_text = existing.read_text(encoding="utf-8")
                content = _update_frontmatter(existing_text, entry)
            _safe_write(existing, content, entry)
            return existing

        dest_dir = self._infer_write_dir(entry)
        dest_dir.mkdir(parents=True, exist_ok=True)
        slug = _slug_from_id(entry.id)
        path = dest_dir / f"{slug}.md"
        _safe_write(path, _render_entry(entry, body), entry)
        return path

    # ── 内部 ──────────────────────────────────────────────

    def _infer_write_dir(self, entry: KnowledgeEntry) -> Path:
        """给一个新条目推断最佳写入目录。

        默认规则:
          1. scope=external:<owner>/<name> 的 KRepo → data/knowledge/external_repos/
          2. karch/kdec/kexp/krepo → data/knowledge/<typedir>/
          3. kformat/krouter 且 domain 对应包存在 → packages/.../knowledge/<typedir>/
          4. 其他情况 fallback 到 data/knowledge/<typedir>/
        """
        type_dir = _DATA_TYPE_DIR.get(entry.omnikb_type, "misc")

        # KRepoArchitectEntry 永远落在 data/knowledge/external_repos/
        if entry.omnikb_type == "krepo":
            return self.data_root / "external_repos"

        # KArchitectureEntry / KDecisionEntry / KExperimentEntry 永远落在 data/knowledge/
        if entry.omnikb_type in ("karch", "kdec", "kexp"):
            return self.data_root / type_dir

        # KFormat / KRouter 尝试落回 package
        pkg_dir = self._find_package_for_domain(entry)
        if pkg_dir is not None:
            return pkg_dir / "knowledge" / type_dir

        return self.data_root / type_dir

    def _find_package_for_domain(self, entry: KnowledgeEntry) -> Path | None:
        """从 entry.tags 中提取 domain.<name>, 尝试找到对应包目录。"""
        for t in entry.tags:
            if not t.startswith("domain."):
                continue
            domain = t.split(".", 1)[1]
            for candidate in (
                self.src_packages / "services" / domain,
                self.src_packages / "domains" / domain,
                self.src_packages / "vendors" / domain,
            ):
                if candidate.is_dir():
                    return candidate
        return None


# ═══════════════════════════════════════════════════════════
# Markdown 渲染 & frontmatter 更新
# ═══════════════════════════════════════════════════════════

def _entry_to_frontmatter_dict(entry: KnowledgeEntry) -> dict:
    """把 entry 转成 frontmatter 字典, 只保留非空字段以保持简洁。"""
    d: dict = {
        "omnikb_type": entry.omnikb_type,
        "id": entry.id,
        "name": entry.name,
        "tags": entry.tags,
        "maturity": entry.maturity,
    }
    if entry.description:
        d["summary"] = entry.description

    # 添加类型特有字段, pydantic model_dump 只保留非空
    model_data = entry.model_dump(exclude_unset=False, exclude_none=True)
    skip_keys = {"omnikb_type", "id", "name", "description", "tags",
                 "maturity", "source_path"}
    for k, v in model_data.items():
        if k in skip_keys:
            continue
        if v in (None, "", [], {}):
            continue
        d[k] = v
    return d


def _render_entry(entry: KnowledgeEntry, body: str) -> str:
    """渲染一个 entry 为完整 md 文本 (含 frontmatter)。"""
    fm_str = _dict_to_yaml(_entry_to_frontmatter_dict(entry))
    if not body:
        body = _default_body_for(entry)
    return f"---\n{fm_str}\n---\n\n{body}"


def _default_body_for(entry: KnowledgeEntry) -> str:
    """为新 entry 提供一个带 section 占位的空 body。"""
    title = entry.name or entry.id
    desc = entry.description or "(待填写)"
    if entry.omnikb_type == "karch":
        return (
            f"# {title}\n\n"
            f"{desc}\n\n"
            "## Why\n\n(设计动机)\n\n"
            "## How it works\n\n(技术机制)\n\n"
            "## Files\n\n(主要源代码文件路径)\n\n"
            "## Related\n\n(相关概念、决策、实验)\n\n"
            "## Known limitations\n\n(当前局限)\n"
        )
    if entry.omnikb_type == "kdec":
        return (
            f"# {title}\n\n"
            f"{desc}\n\n"
            "## Drivers\n\n(为什么需要这个决策)\n\n"
            "## Options considered\n\n(考虑过的方案)\n\n"
            "## Decision\n\n(最终决定及理由)\n\n"
            "## Consequences\n\n(正负影响)\n"
        )
    if entry.omnikb_type == "kexp":
        return (
            f"# {title}\n\n"
            f"{desc}\n\n"
            "## Hypothesis\n\n(实验假设)\n\n"
            "## Method\n\n(方法概述)\n\n"
            "## Samples\n\n(运行的样本)\n\n"
            "## Findings\n\n(关键发现)\n\n"
            "## Followups\n\n(后续任务)\n"
        )
    if entry.omnikb_type == "krepo":
        return (
            f"# {title}\n\n"
            f"{desc}\n\n"
            "## Capability areas\n\n(识别到的能力领域)\n\n"
            "## Prior landmarks\n\n(历史 tier-1)\n\n"
            "## Known unread\n\n(明确承认没读的区域)\n"
        )
    if entry.omnikb_type == "kformat":
        return (
            f"# {title}\n\n"
            f"{desc}\n\n"
            "## 已知结构特征\n\n(待填写)\n\n"
            "## 验证要点\n\n(待填写)\n\n"
            "## 下游用途\n\n(待填写)\n"
        )
    if entry.omnikb_type == "krouter":
        return (
            f"# {title}\n\n"
            f"{desc}\n\n"
            "## 已知成功路径\n\n(待填写)\n\n"
            "## 已知失败模式\n\n(待填写)\n\n"
            "## 对应实现\n\n(待填写)\n"
        )
    return f"# {title}\n\n{desc}\n"


def _update_frontmatter(existing_text: str, entry: KnowledgeEntry) -> str:
    """更新已有文档的 frontmatter, 保留正文。

    必须先剥掉 guarded_write 添加的 ``# [OMNI] ...`` 头行，
    否则 ``^---`` 正则不匹配，整个旧文件会被错误地当作 body 叠加。
    """
    import re as _re
    # 剥掉 OmniMark 自动头（与 _extract_frontmatter 同逻辑）
    stripped = _re.sub(r"^#\s*\[OMNI\][^\n]*\n+", "", existing_text, count=1)
    m = _re.match(r"^---\s*\n(.*?)\n---\s*\n", stripped, _re.DOTALL)
    body = stripped[m.end():] if m else stripped
    fm_str = _dict_to_yaml(_entry_to_frontmatter_dict(entry))
    return f"---\n{fm_str}\n---\n\n{body}"


def _dict_to_yaml(d: dict) -> str:
    """把字典转为 YAML 字符串。优先用 pyyaml, 否则 fallback 到简单实现。"""
    try:
        import yaml
        return yaml.dump(d, allow_unicode=True, default_flow_style=False,
                         sort_keys=False).rstrip()
    except ImportError:  # pragma: no cover
        lines = []
        for k, v in d.items():
            if isinstance(v, list):
                if not v:
                    lines.append(f"{k}: []")
                else:
                    lines.append(f"{k}:")
                    for item in v:
                        lines.append(f"  - {_yaml_str(item)}")
            elif isinstance(v, dict):
                lines.append(f"{k}: {v}")
            else:
                lines.append(f"{k}: {_yaml_str(v)}")
        return "\n".join(lines)


def _yaml_str(v) -> str:
    if isinstance(v, str):
        if any(c in v for c in ('"', "'", ":", "#", "\n")):
            return f'"{v}"'
        return v
    return str(v)


# ═══════════════════════════════════════════════════════════
# guarded_write 集成
# ═══════════════════════════════════════════════════════════

def _safe_write(path: Path, content: str, entry: KnowledgeEntry) -> None:
    """通过 guarded_write 原子落盘 md 文件, 同时贴 OmniMark 头。

    使用 origin="internal-engine" 因为 OmniKB 的写入本质上是 pipeline 内部行为
    (KBWriteRouter / KBIndexRebuildRouter / seed 脚本) — 这与 archmap.yaml 里
    data/ drawer 的 writable_by=[internal-engine, internal-guardian] 一致。

    2026-04-09: 移除了 except 分支里的 path.write_text(content) 静默 fallback。
    它不是 error recovery, 是架构逃逸门 —— 一旦 guarded_write 导入失败会让
    OmniKB 悄悄绕过写入审计。现在改为让异常浮出, 便于问题立刻暴露。
    """
    from omnicompany.core.guarded_write import write_file

    write_file(
        str(path),
        content,
        origin="internal-engine",
        domain="services/knowledge",
        purpose=f"write {entry.omnikb_type} {entry.id}",
    )


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def _slug_from_id(kb_id: str) -> str:
    """从 id 提取文件名 slug: kb.arch.bus_unification → bus_unification"""
    parts = kb_id.split(".")
    if len(parts) >= 3:
        return "_".join(parts[2:])
    return kb_id.replace(".", "_")


def _domain_from_id(kb_id: str) -> str | None:
    """从 kb.<type>.<domain>.<name> 提取 domain, 无法提取时返回 None"""
    parts = kb_id.split(".")
    if len(parts) >= 4:
        return parts[2]
    return None
