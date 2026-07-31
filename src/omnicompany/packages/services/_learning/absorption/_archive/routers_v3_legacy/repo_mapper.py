# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-13T00:00:00Z type=router
# [OMNI] material_id="material:learning.absorption.v3_legacy.repo_symbol_mapper.builder.py"
"""repo_mapper — V3 RepoMapperRouter（纯计算，无 LLM）

给整个本地 repo 建双层符号地图：
  coarse_view  全量，每文件 1 行，按 importance_score 降序
  detail_views 按文件存储完整符号树，供 ModulePicker 按需展开

设计文档：docs/plans/[2026-04-13]REPO-ABSORPTION-V3/DESIGN.md §三.Format 1
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

# ── 忽略目录和扩展名 ────────────────────────────────────────────────────────

_IGNORE_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".next", ".nuxt",
    "dist", "build", "target", "vendor", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache",
    "coverage", ".nyc_output", ".gradle", ".svn", ".hg",
    ".turbo", ".vercel", "out", ".idea", ".vscode",
    # 参考资料目录（不是源码）
    "references", "fixtures", "examples", "samples", "assets",
    "website", "docs_src",
})

_IGNORE_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".class", ".o", ".so", ".dll", ".exe",
    ".min.js", ".map", ".lock", ".sum",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".zip", ".tar", ".gz", ".rar",
    ".db", ".sqlite", ".sqlite3",
    # 媒体 / 数据 / 生成文件
    ".wav", ".mp3", ".mp4", ".ogg", ".flac",
    ".xsd", ".wsdl", ".proto",
    ".csv", ".parquet", ".arrow", ".bin",
    ".ipynb",  # notebook — 太大且格式噪音多
})

# 特定文件名忽略（无论扩展名如何）
_IGNORE_FILENAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Gemfile.lock", "Cargo.lock", "poetry.lock", "composer.lock",
    "go.sum", "packages.lock.json",
})

# coarse_view 极紧凑格式每行约 60-70 字符
# 1万文件 × 70字符 ≈ 700k字符，约 90k token
# 对于超大 repo，调用方应分片或先按目录过滤
# 不在此处截断——截断是最坏选项，宁可让 AgentNodeLoop 分批处理

# ── 符号提取 pattern（每种语言）────────────────────────────────────────────

_SYMBOL_PATTERNS: dict[str, list[tuple[re.Pattern[str], str]]] = {
    ".py": [
        (re.compile(r"^class\s+(\w+)"), "class"),
        (re.compile(r"^async\s+def\s+(\w+)"), "async def"),
        (re.compile(r"^def\s+(\w+)"), "def"),
        (re.compile(r"^    def\s+(\w+)"), "method"),
        (re.compile(r"^    async\s+def\s+(\w+)"), "async method"),
    ],
    ".ts": [
        (re.compile(r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"), "class"),
        (re.compile(r"^(?:export\s+)?interface\s+(\w+)"), "interface"),
        (re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)"), "function"),
        (re.compile(r"^(?:export\s+)?(?:const|let)\s+(\w+)\s*[:=]"), "const"),
        (re.compile(r"^(?:export\s+)?type\s+(\w+)\s*="), "type"),
        (re.compile(r"^(?:export\s+)?enum\s+(\w+)"), "enum"),
    ],
    ".tsx": [
        (re.compile(r"^(?:export\s+)?(?:default\s+)?(?:function|const)\s+(\w+)"), "component"),
        (re.compile(r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"), "class"),
    ],
    ".js": [
        (re.compile(r"^(?:export\s+)?class\s+(\w+)"), "class"),
        (re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)"), "function"),
        (re.compile(r"^(?:module\.exports\s*=\s*)?(?:const|let|var)\s+(\w+)\s*="), "const"),
    ],
    ".rs": [
        (re.compile(r"^(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)"), "type"),
        (re.compile(r"^(?:pub\s+)?fn\s+(\w+)"), "fn"),
        (re.compile(r"^impl(?:\s+\w+\s+for)?\s+(\w+)"), "impl"),
    ],
    ".go": [
        (re.compile(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)"), "func"),
        (re.compile(r"^type\s+(\w+)"), "type"),
    ],
    ".java": [
        (re.compile(r"^(?:public\s+|private\s+|protected\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+(\w+)"), "class"),
    ],
    ".rb": [
        (re.compile(r"^class\s+(\w+)"), "class"),
        (re.compile(r"^def\s+(\w+)"), "def"),
        (re.compile(r"^  def\s+(\w+)"), "method"),
    ],
    ".swift": [
        (re.compile(r"^(?:public\s+|private\s+|open\s+)?(?:class|struct|enum|protocol)\s+(\w+)"), "type"),
        (re.compile(r"^(?:public\s+|private\s+|open\s+)?func\s+(\w+)"), "func"),
    ],
}
# .jsx 与 .js 相同
_SYMBOL_PATTERNS[".jsx"] = _SYMBOL_PATTERNS[".js"]


def _extract_symbols(path: Path) -> list[dict[str, Any]]:
    """从文件中提取顶层符号（类/函数/接口等），返回 [{name, kind, line}]。"""
    ext = path.suffix.lower()
    patterns = _SYMBOL_PATTERNS.get(ext)
    if not patterns:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    symbols: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for pattern, kind in patterns:
            m = pattern.match(line)
            if m:
                symbols.append({"name": m.group(1), "kind": kind, "line": lineno})
                break  # 每行只匹配第一个
    return symbols


def _count_lines(path: Path) -> int:
    try:
        return path.read_text(encoding="utf-8", errors="replace").count("\n") + 1
    except Exception:
        return 0


def _should_ignore(path: Path, repo_root: Path) -> bool:
    """判断文件是否应当忽略。"""
    # 特定文件名
    if path.name in _IGNORE_FILENAMES:
        return True
    # 忽略扩展名
    if path.suffix.lower() in _IGNORE_EXTENSIONS:
        return True
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return True
    # 任意祖先目录名在忽略列表中
    for part in rel.parts[:-1]:
        if part in _IGNORE_DIRS or part.startswith("."):
            return True
    return False


def _extract_project_thesis(repo_root: Path) -> str:
    """读取 README，提取项目自述的核心特色段落（安装/使用/贡献等操作节之前的部分）。

    无人工截断：读取全文后按语义边界切割，LLM 获得完整的项目自述区段。
    若无 README 则返回空字符串。
    """
    for name in ("README.md", "README.MD", "Readme.md", "README.rst", "README.txt", "README"):
        p = repo_root / name
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # 在"安装/使用/贡献/许可"等操作节之前停止——这些节之后的内容不是特色宣称
            _SKIP_RE = re.compile(
                r"^#{1,3}\s+"
                r"(Installation|Getting\s+Started|Quick\s+Start|Usage|"
                r"Contributing|License|Changelog|Requirements?|Setup|"
                r"Building|Roadmap|FAQ|Support|Contact|Credits|Sponsors?|"
                r"安装|快速开始|使用方法|使用说明|贡献|许可|更新日志|依赖|部署)",
                re.MULTILINE | re.IGNORECASE,
            )
            m = _SKIP_RE.search(content)
            thesis = (content[: m.start()].strip() if m else content.strip())
            return thesis if thesis else content.strip()
    return ""


def _extract_keywords(self_portrait: str) -> frozenset[str]:
    """从 self_portrait 提取有意义的英文关键词（3字符以上，过滤停用词）。"""
    _STOPWORDS = frozenset({
        "the", "and", "for", "not", "are", "with", "this", "that",
        "from", "has", "have", "been", "will", "can", "its", "our",
        "their", "all", "but", "into", "more", "also", "each", "any",
        "which", "when", "what", "how", "via", "per", "yet",
    })
    words = re.findall(r"\b[a-zA-Z]{3,}\b", self_portrait.lower())
    return frozenset(w for w in words if w not in _STOPWORDS)


def _score_file(file_info: dict, keywords: frozenset[str]) -> float:
    """行数 × 关键词命中倍数（上限 3x）。"""
    line_count = file_info["line_count"]
    symbol_text = " ".join(s["name"].lower() for s in file_info["symbols"])
    path_lower = file_info["path"].lower()
    combined = symbol_text + " " + path_lower
    hits = sum(1 for kw in keywords if kw in combined)
    multiplier = 1.0 + min(hits * 0.3, 2.0)
    return float(line_count) * multiplier


def _build_coarse_view(repo_name: str, files: list[dict]) -> str:
    """生成极紧凑的全量符号地图，不截断。

    格式：path[行数]:symbol1·symbol2·symbol3
    目标：每行 ≤70 字符，全量覆盖所有文件。
    对于超大 repo，由 ModuleExplorer 分批处理目录，不在此处截断。
    """
    header = [
        f"## Repository Map: {repo_name}",
        f"## {len(files)} files | sorted by importance (line_count × keyword_score)",
        "",
    ]
    body_lines: list[str] = []
    for f in files:
        path = f["path"]
        lc = f["line_count"]
        syms = f["top_symbols"]
        sym_str = "·".join(syms[:4]) if syms else ""
        # 极紧凑：path[lc]:symbols
        if sym_str:
            line = f"{path}[{lc}]:{sym_str}"
        else:
            line = f"{path}[{lc}]"
        body_lines.append(line)

    return "\n".join(header + body_lines)


def _build_detail_view(file_info: dict) -> str:
    """生成细粒度符号树文本（含行号）。"""
    path = file_info["path"]
    symbols = file_info["symbols"]
    if not symbols:
        return f"{path}:\n  (no symbols extracted)\n"
    lines = [f"{path}:"]
    for s in symbols:
        indent = "  " if s["kind"] in ("method", "async method") else ""
        lines.append(f"{indent}  {s['kind']} {s['name']} (L{s['line']})")
    return "\n".join(lines)


def _scan_repo(repo_root: Path) -> list[dict]:
    """扫描 repo，返回所有非忽略文件的信息列表。"""
    results: list[dict] = []
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        if _should_ignore(path, repo_root):
            continue
        rel = str(path.relative_to(repo_root)).replace("\\", "/")
        symbols = _extract_symbols(path)
        line_count = _count_lines(path)
        results.append({
            "path": rel,
            "line_count": line_count,
            "symbols": symbols,
            "symbol_count": len(symbols),
            "top_symbols": [s["name"] for s in symbols[:8]],
            "importance_score": 0.0,  # 填充后更新
        })
    return results


class RepoMapperRouter(Router):
    """V3 仓库地图生成节点（纯计算，无 LLM）。

    扫描本地 repo 的所有文件，提取符号、统计行数，
    生成 absorption.repomap（coarse_view + detail_views + files[]）。

    解决 V2 Scout 系统性漏读正交基础设施的根因：
    coarse_view 覆盖所有文件，没有任何文件因为"不在主线上"而不可见。
    """

    DESCRIPTION = (
        "V3 仓库地图：纯计算扫描全 repo，按行数×关键词分数排序，"
        "产出双层地图（粗粒度全量 + 细粒度按需），解决正交模块不可见问题"
    )
    FORMAT_IN = "absorption.request"
    FORMAT_OUT = "absorption.repomap"

    def run(self, input_data: Any) -> Verdict:
        repo_local_path = input_data.get("repo_local_path", "")
        repo_name = input_data.get("repo_name", "unknown")
        self_portrait = input_data.get("self_portrait", "")

        if not repo_local_path:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="RepoMapper: input 缺少 repo_local_path",
            )

        repo_root = Path(repo_local_path)
        if not repo_root.exists():
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"RepoMapper: repo_local_path 不存在: {repo_local_path}",
            )

        # 扫描
        files = _scan_repo(repo_root)
        project_thesis = _extract_project_thesis(repo_root)

        # 评分
        keywords = _extract_keywords(self_portrait)
        for f in files:
            f["importance_score"] = _score_file(f, keywords)

        # 按重要性排序
        files.sort(key=lambda f: f["importance_score"], reverse=True)

        # 生成地图
        coarse_view = _build_coarse_view(repo_name, files)
        detail_views = {f["path"]: _build_detail_view(f) for f in files}

        # 从 files 里移除 symbols（太大，用 detail_views 代替）
        files_out = [
            {k: v for k, v in f.items() if k != "symbols"}
            for f in files
        ]

        n_with_symbols = sum(1 for f in files if f["symbol_count"] > 0)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "repo_name": repo_name,
                "coarse_view": coarse_view,
                "files": files_out,
                "detail_views": detail_views,
                "total_files": len(files),
                "coarse_token_count": len(coarse_view.split()),
                "project_thesis": project_thesis,
            },
            confidence=1.0,
            diagnosis=(
                f"RepoMapper: {len(files)} 文件, "
                f"{n_with_symbols} 有符号, "
                f"coarse_view {len(coarse_view)} chars, "
                f"keyword_hits from {len(keywords)} keywords, "
                f"project_thesis {len(project_thesis)} chars"
            ),
            granted_tags=["domain.absorption", "stage.v3.repomap"],
        )
