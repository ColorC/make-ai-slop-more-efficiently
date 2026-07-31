# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-18T00:00:00Z type=module
# [OMNI] material_id="material:learning.absorption.wiki_knowledge_assembly_engine.py"
"""OmniCompany 自知识（wiki）动态加载工具。

取代 spec_parser._build_omnicompany_summary() 的硬编码字符串方案。
从现行 wiki 组装"OmniCompany 当前有什么 / 希望有什么"的结构化摘要。

数据来源：
- src/omnicompany/README.md         — 能力五分类导航
- docs/gaps/INDEX.md + G1~G7.md     — 缺口档案（"希望有什么"）
- src/omnicompany/**/DESIGN.md      — 各模块 ## 核心目的 节（active/design only）

关键设计：
- 只收 status=active|design 的 DESIGN.md 的 ## 核心目的 节
  （skeleton 节是 TBD 占位，无信息价值）
- 进程级缓存（lru_cache），单次 dispatch 只读一次盘
- 失败降级：wiki 文件缺失时走兜底字符串（绝不抛异常阻塞主管线）

对应规范：F-15 / P-13（声明即消费）— 上游应把本函数的产出作为显式 Format 字段喂给
下游节点，不再靠 SpecParser 内部硬编码。暂存该函数为过渡方案。
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# 项目根：本文件位于 src/omnicompany/packages/services/absorption/wiki_loader.py
# → 向上 5 层到 omnicompany/ 仓库根
_REPO_ROOT = Path(__file__).resolve().parents[5]
_OMNI_SRC = _REPO_ROOT / "src" / "omnicompany"
_GAPS_DIR = _REPO_ROOT / "docs" / "gaps"

# 只扫这些根下的 DESIGN.md（避免扫到 data/ 等无关目录）
_DESIGN_SCAN_ROOTS = [
    _OMNI_SRC / "protocol",
    _OMNI_SRC / "core",
    _OMNI_SRC / "bus",
    _OMNI_SRC / "primitives",
    _OMNI_SRC / "tools",
    _OMNI_SRC / "tracing",
    _OMNI_SRC / "cli",
    _OMNI_SRC / "dashboard",
    _OMNI_SRC / "runtime",
    _OMNI_SRC / "packages" / "services",
    _OMNI_SRC / "packages" / "domains",
]

_OMNIMARK_STATUS = re.compile(r"status=([a-z]+)")
_SECTION_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_TBD_MARKER = re.compile(r"<!--\s*TBD\b|_待补充|\bTBD\s*[:：]", re.IGNORECASE)


def _extract_status(content: str) -> str | None:
    """从 OmniMark 头读 status 字段。"""
    first_line = content.splitlines()[0] if content else ""
    if "[OMNI]" not in first_line:
        return None
    m = _OMNIMARK_STATUS.search(first_line)
    return m.group(1).lower() if m else None


def _extract_section(content: str, heading: str) -> str:
    """抽取 `## heading` 到下一个 ## 之间的正文。未找到返回空字符串。"""
    pattern = rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)"
    m = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    body = m.group(1).strip()
    # 去 HTML 注释占位（避免把 <!-- TBD: ... --> 当正文）
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()
    return body


def _compact_lines(text: str, max_lines: int | None = None) -> str:
    """2026-04-18 零容忍截断：保留全部非空行。
    旧注释声称 LLM "随时可用 local_read 拉完整 DESIGN.md"——但 SpecParser 不是 agent-loop，
    没有 local_read，看到什么就是什么。违反 llm_first.md 原则 3。
    max_lines 参数保留兼容，但被忽略（若将来需要分片只能走统一 3 层办法）。"""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    return "\n".join(lines)


def _module_label(design_path: Path) -> str:
    """把 DESIGN.md 的绝对路径转成可读模块名。"""
    try:
        rel = design_path.relative_to(_OMNI_SRC).parent.as_posix()
    except ValueError:
        rel = design_path.parent.name
    return rel or design_path.parent.name


# ── Gaps ──────────────────────────────────────────────────────────────────

def _load_gaps_section() -> str:
    """读 docs/gaps/INDEX.md + G*.md，组合"希望有什么"段落。"""
    if not _GAPS_DIR.exists():
        return "（gaps 档案缺失）"

    index_path = _GAPS_DIR / "INDEX.md"
    lines: list[str] = []
    if index_path.exists():
        # INDEX 通常很短，直接取前 30 行定位
        idx = index_path.read_text(encoding="utf-8").splitlines()
        lines.append("### gaps/INDEX 摘要")
        lines.extend(l for l in idx[:30] if l.strip())
        lines.append("")

    # 每个 G_.md 取首段 + 缺什么节
    for gap_file in sorted(_GAPS_DIR.glob("G*.md")):
        try:
            raw = gap_file.read_text(encoding="utf-8")
        except Exception:
            continue
        # 抽标题
        title_m = re.search(r"^# (.+)$", raw, re.MULTILINE)
        title = title_m.group(1).strip() if title_m else gap_file.stem
        # 抽 "缺什么" 节
        lacking = _extract_section(raw, "缺什么")
        if lacking:
            lines.append(f"### {title}")
            lines.append(_compact_lines(lacking, max_lines=8))
            lines.append("")
    return "\n".join(lines).strip() or "（gaps 解析为空）"


# ── Active/Design DESIGN.md ───────────────────────────────────────────────

_CAPABILITY_TAG_MAP = {
    # path prefix → 能力分类（取自 README §一 的五分类）
    "packages/services/absorption": "learning",
    "packages/services/hypothesis": "learning",
    "packages/services/trace_induction": "learning",
    "packages/services/pattern_discovery": "learning",
    "packages/services/knowledge": "learning",
    "packages/services/evolution": "learning",
    "runtime/agent_crystallize": "learning",
    "packages/services/doctor": "diagnosis",
    "packages/services/guardian": "diagnosis",
    "packages/services/selftest": "diagnosis",
    "runtime/info_audit": "diagnosis",
    "runtime/exec": "execution",
    "runtime/agent": "execution",
    "runtime/llm": "execution",
    "runtime/routing": "execution",
    "runtime/nodes": "execution",
    "packages/services/registry": "execution",
    "cli": "execution",
    "tools": "execution",
    "bus": "persistence",
    "runtime/storage": "persistence",
    "tracing": "persistence",
    "dashboard": "persistence",
    "protocol": "protocol",
    "core": "protocol",
    "primitives": "protocol",
    "runtime/signals": "protocol",
    "packages/domains": "domain",
}


def _infer_capability_tag(path: str) -> str:
    """根据模块相对路径推断能力分类标签。未命中返回 'unknown'。"""
    # 按前缀最长匹配
    best = ""
    for prefix in _CAPABILITY_TAG_MAP:
        if path.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    return _CAPABILITY_TAG_MAP.get(best, "unknown")


def _load_active_designs() -> str:
    """扫 _DESIGN_SCAN_ROOTS 下所有 DESIGN.md，取 active|design 的 ## 核心目的 节。

    返回字符串形式（向后兼容给 load_wiki_self_knowledge() 用）。
    结构化输出请用 load_capability_inventory()。
    """
    modules = _collect_modules()
    by_status: dict[str, list[tuple[str, str]]] = {"active": [], "design": []}
    for m in modules:
        by_status[m["maturity"]].append((m["path"], m["one_line"]))

    parts: list[str] = []
    for status in ("active", "design"):
        items = sorted(by_status[status])
        if not items:
            continue
        parts.append(f"### status={status}（{len(items)} 个模块）")
        for label, purpose in items:
            parts.append(f"**`{label}/`**")
            parts.append(purpose)
            parts.append("")
    return "\n".join(parts).strip() or "（无 active/design DESIGN.md）"


def _collect_modules() -> list[dict]:
    """扫 _DESIGN_SCAN_ROOTS 下的 DESIGN.md，返回 {path, maturity, one_line, tags} 列表。

    只返回 status=active|design 且 核心目的 非 TBD 的模块。
    """
    modules: list[dict] = []
    seen: set[str] = set()
    for root in _DESIGN_SCAN_ROOTS:
        if not root.exists():
            continue
        for design_path in root.rglob("DESIGN.md"):
            try:
                content = design_path.read_text(encoding="utf-8")
            except Exception:
                continue
            status = _extract_status(content)
            if status not in ("active", "design"):
                continue
            purpose = _extract_section(content, "核心目的")
            if not purpose or _TBD_MARKER.search(purpose):
                continue
            path = _module_label(design_path)
            if path in seen:
                continue
            seen.add(path)
            modules.append({
                "path": path,
                "maturity": status,
                "one_line": _compact_lines(purpose, max_lines=5),
                "tags": [_infer_capability_tag(path)],
            })
    modules.sort(key=lambda m: (m["maturity"], m["path"]))
    return modules


def _collect_gaps() -> list[dict]:
    """扫 docs/gaps/G*.md，返回结构化 gap 列表。

    过滤掉 INDEX.md / _template.md / archived/ 下的文件。
    从 <!-- [OMNI] ... verification=... --> 头读 verification 字段。
    从 ## 元信息 节读 priority / state。
    从 ## 缺什么 节读正文。
    """
    gaps: list[dict] = []
    if not _GAPS_DIR.exists():
        return gaps

    priority_re = re.compile(r"\*\*优先级\*\*\s*[:：]\s*(P[012])", re.IGNORECASE)
    state_re = re.compile(r"\*\*状态\*\*\s*[:：]\s*([^\n]+)")
    verification_re = re.compile(r"verification=([a-z_]+)")
    title_re = re.compile(r"^#\s+(.+)$", re.MULTILINE)
    id_re = re.compile(r"^G\d+")

    for gap_file in sorted(_GAPS_DIR.glob("G*.md")):
        if "archived" in gap_file.parts:
            continue
        try:
            raw = gap_file.read_text(encoding="utf-8")
        except Exception:
            continue

        gid_match = id_re.match(gap_file.stem)
        if not gid_match:
            continue
        gid = gid_match.group(0)

        # 标题
        title_m = title_re.search(raw)
        title = title_m.group(1).strip() if title_m else gap_file.stem
        # 去 "G1 · 标题" 前缀
        title = re.sub(rf"^{gid}\s*[·.]\s*", "", title).strip()

        # verification / priority / state
        verification = "partial"
        v_m = verification_re.search(raw.splitlines()[0] if raw else "")
        if v_m:
            verification = v_m.group(1)

        priority = "P1"
        p_m = priority_re.search(raw)
        if p_m:
            priority = p_m.group(1).upper()

        state = "未动"
        s_m = state_re.search(raw)
        if s_m:
            state = s_m.group(1).strip().strip("`").split("（")[0].strip()

        what_missing = _extract_section(raw, "缺什么")

        gaps.append({
            "id": gid,
            "title": title,
            "priority": priority,
            "state": state,
            "verification": verification,
            "what_missing": _compact_lines(what_missing, max_lines=12),
            "source_path": str(gap_file.relative_to(_REPO_ROOT)).replace("\\", "/"),
        })
    gaps.sort(key=lambda g: g["id"])
    return gaps


def _load_readme_capability_map() -> str:
    """README §一 能力分类原文。"""
    readme_path = _OMNI_SRC / "README.md"
    if not readme_path.exists():
        return ""
    try:
        readme_raw = readme_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.search(r"^## 一、能力分类.*?(?=^## [二三四])", readme_raw, re.MULTILINE | re.DOTALL)
    return m.group(0).strip() if m else ""


def _load_gaps_index_summary() -> str:
    """docs/gaps/INDEX.md 的可读摘要（前 30 行非空）。"""
    index_path = _GAPS_DIR / "INDEX.md"
    if not index_path.exists():
        return ""
    try:
        idx = index_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""
    # 2026-04-18 零容忍截断：返回全部非空行（llm_first.md 原则 3）
    lines = [l for l in idx if l.strip()]
    return "\n".join(lines)


# ── 结构化加载器（第三步新增，供 Loader Router 消费）────────────────────

from datetime import datetime, timezone


@lru_cache(maxsize=1)
def load_capability_inventory() -> dict:
    """产出 omni.self.capability_inventory Format 的结构化载荷。

    字段:
      - generated_at: ISO8601
      - source_root: "src/omnicompany"
      - source_commit: "working tree"
      - module_count: int
      - modules: list[{path, maturity, one_line, tags}]
      - readme_capability_map: str (README §一原文)
    """
    modules = _collect_modules()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_root": "src/omnicompany",
        "source_commit": "working tree",
        "module_count": len(modules),
        "modules": modules,
        "readme_capability_map": _load_readme_capability_map(),
    }


@lru_cache(maxsize=1)
def load_gap_registry() -> dict:
    """产出 omni.self.gap_registry Format 的结构化载荷。

    字段:
      - generated_at: ISO8601
      - source_dir: "docs/gaps"
      - gap_count: int
      - gaps: list[{id, title, priority, state, verification, what_missing, source_path}]
      - index_summary: str (docs/gaps/INDEX.md 摘要)
    """
    gaps = _collect_gaps()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_dir": "docs/gaps",
        "gap_count": len(gaps),
        "gaps": gaps,
        "index_summary": _load_gaps_index_summary(),
    }


def render_capability_inventory_for_prompt(inv: dict) -> str:
    """把结构化 inventory 渲染为可读 Markdown（塞进 LLM prompt 用）。"""
    lines = [
        f"# OmniCompany 能力清单（共 {inv.get('module_count', 0)} 个模块）",
        f"生成时间: {inv.get('generated_at', '?')}  ·  快照: {inv.get('source_commit', '?')}",
        "",
        "## 能力分类导航（README §一）",
        "",
        inv.get("readme_capability_map", "（缺失）") or "（缺失）",
        "",
        "## 各模块核心目的",
        "",
    ]
    by_maturity: dict[str, list[dict]] = {"active": [], "design": []}
    for m in inv.get("modules", []):
        by_maturity.setdefault(m.get("maturity", "unknown"), []).append(m)
    for maturity in ("active", "design"):
        items = by_maturity.get(maturity, [])
        if not items:
            continue
        lines.append(f"### {maturity}（{len(items)} 个）")
        for m in items:
            tags = ", ".join(m.get("tags") or [])
            lines.append(f"- `{m['path']}/` [{tags}] — {m.get('one_line', '')}")
        lines.append("")
    return "\n".join(lines)


# ── Reception Intents（第 8 节 "## 接收意愿"）──────────────────────────

# 基础设施模块前缀：仅这些路径下的 DESIGN.md 会被扫 reception_intent
_RECEPTION_SCAN_PREFIXES = (
    "src/omnicompany/runtime/",
    "src/omnicompany/protocol/",
    "src/omnicompany/core/",
    "src/omnicompany/bus/",
    "src/omnicompany/primitives/",
    "src/omnicompany/tools/",
    "src/omnicompany/tracing/",
)

_RECEPTION_MATURITY_VALUES = {"any", "stable_only", "production_validated"}


def _is_infrastructure_path(rel_path: str) -> bool:
    rel = rel_path.replace("\\", "/")
    return any(rel.startswith(p) for p in _RECEPTION_SCAN_PREFIXES)


def _parse_reception_section(body: str) -> dict:
    """解析 `## 接收意愿` 节正文。

    期望结构（参考 design_md_template.md §九）：
    - **welcome_themes**:
      - <item>
      - <item>
    - **hard_constraints**:
      - <item>
    - **soft_preferences**:
      - <item>
    - **maturity_preference**: <any|stable_only|production_validated>

    容错：字段缺失 → 空列表 / `any`；解析失败不抛异常。
    """
    result: dict = {
        "welcome_themes": [],
        "hard_constraints": [],
        "soft_preferences": [],
        "maturity_preference": "any",
    }
    if not body.strip():
        return result

    # 识别列表型字段块
    list_fields = {
        "welcome_themes": "welcome_themes",
        "hard_constraints": "hard_constraints",
        "soft_preferences": "soft_preferences",
    }
    # 把正文按一级项（- **xxx**: ）切分
    # pattern: -  **<name>**: [inline]\n 再跟若干缩进子项
    # 注意：
    #   1) 冒号后必须限定水平空白（[ \t]*），不能用 \s* 否则会吃掉 \n 吞并第一个子项
    #   2) 末尾必须允许 \n 或字符串终止（$），否则末行 maturity_preference 无 trailing \n 会漏
    item_re = re.compile(
        r"^-[ \t]*\*\*(?P<name>[a-z_]+)\*\*[ \t]*[:：][ \t]*(?P<inline>[^\n]*)(?:\n|$)(?P<children>(?:[ \t]+-.*\n?)*)",
        re.MULTILINE,
    )
    for m in item_re.finditer(body):
        name = m.group("name").strip().lower()
        inline_val = m.group("inline").strip()
        children_raw = m.group("children") or ""
        children = []
        for line in children_raw.splitlines():
            stripped = line.strip()
            if stripped.startswith("-"):
                child = stripped.lstrip("-").strip()
                if child:
                    children.append(child)
        if name in list_fields:
            items = children if children else ([inline_val] if inline_val else [])
            result[list_fields[name]] = items
        elif name == "maturity_preference":
            # 容许 "`stable_only`（原因...）" / "stable_only (原因)" 等形式
            value = inline_val.split("（")[0].split("(")[0]
            # 去掉所有反引号与空白
            value = value.replace("`", "").strip()
            if value in _RECEPTION_MATURITY_VALUES:
                result["maturity_preference"] = value
    return result


def _collect_reception_intents() -> list[dict]:
    """扫 _DESIGN_SCAN_ROOTS 下基础设施模块的 DESIGN.md，
    返回 status=active|design 且含 `## 接收意愿` 节的条目列表。

    返回字段: {module_path, maturity, welcome_themes, hard_constraints,
                soft_preferences, maturity_preference, source_path}
    """
    intents: list[dict] = []
    seen: set[str] = set()
    for root in _DESIGN_SCAN_ROOTS:
        if not root.exists():
            continue
        for design_path in root.rglob("DESIGN.md"):
            try:
                rel = design_path.relative_to(_REPO_ROOT).as_posix()
            except ValueError:
                continue
            if not _is_infrastructure_path(rel):
                continue
            try:
                content = design_path.read_text(encoding="utf-8")
            except Exception:
                continue
            status = _extract_status(content)
            if status not in ("active", "design"):
                continue
            section = _extract_section(content, "接收意愿")
            if not section:
                continue
            module_path = _module_label(design_path)
            if module_path in seen:
                continue
            seen.add(module_path)
            parsed = _parse_reception_section(section)
            # 空骨架（所有字段都空）跳过
            if (not parsed["welcome_themes"]
                and not parsed["hard_constraints"]
                and not parsed["soft_preferences"]):
                continue
            intents.append({
                "module_path": module_path,
                "maturity": status,
                **parsed,
                "source_path": rel,
            })
    intents.sort(key=lambda i: i["module_path"])
    return intents


@lru_cache(maxsize=1)
def load_reception_intents() -> dict:
    """产出 omni.self.reception_intents Format 的结构化载荷。

    字段:
      - generated_at: ISO8601
      - source_root: "src/omnicompany"
      - module_count: int
      - intents: list[{module_path, maturity, welcome_themes, hard_constraints,
                        soft_preferences, maturity_preference, source_path}]
    """
    intents = _collect_reception_intents()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_root": "src/omnicompany",
        "module_count": len(intents),
        "intents": intents,
    }


def render_reception_intents_for_prompt(obj: dict) -> str:
    """把结构化 reception_intents 渲染为可读 Markdown。"""
    lines = [
        f"# OmniCompany 接收意愿（共 {obj.get('module_count', 0)} 个基础设施模块）",
        f"生成时间: {obj.get('generated_at', '?')}  ·  源: {obj.get('source_root', '?')}",
        "",
        "本档声明各基础设施模块\"欢迎吸收什么主题\"（可能超出已识别缺口范围）。",
        "配合 capability_inventory / gap_registry 做四元判断：",
        "  1) 已有可改进  2) 已知缺口  3) 愿接收新主题  4) 架构冲突（hard_constraints 违反）",
        "",
    ]
    for it in obj.get("intents", []):
        lines.append(f"## `{it['module_path']}/`  ({it['maturity']})")
        wt = it.get("welcome_themes") or []
        if wt:
            lines.append("**欢迎主题 (welcome_themes)**:")
            for x in wt:
                lines.append(f"  - {x}")
        hc = it.get("hard_constraints") or []
        if hc:
            lines.append("**硬约束 (hard_constraints，违反即不吸纳)**:")
            for x in hc:
                lines.append(f"  - {x}")
        sp = it.get("soft_preferences") or []
        if sp:
            lines.append("**软偏好 (soft_preferences，违反降优先级)**:")
            for x in sp:
                lines.append(f"  - {x}")
        lines.append(f"**成熟度偏好**: `{it.get('maturity_preference', 'any')}`")
        lines.append(f"_档案: `{it.get('source_path', '?')}`_")
        lines.append("")
    return "\n".join(lines)


def render_gap_registry_for_prompt(reg: dict) -> str:
    """把结构化 gap_registry 渲染为可读 Markdown。"""
    lines = [
        f"# OmniCompany 已识别缺口（共 {reg.get('gap_count', 0)} 条）",
        f"生成时间: {reg.get('generated_at', '?')}  ·  源: {reg.get('source_dir', '?')}",
        "",
    ]
    idx = reg.get("index_summary", "")
    if idx:
        lines.append("## INDEX 摘要")
        lines.append(idx)
        lines.append("")
    lines.append("## 逐条缺口")
    lines.append("")
    for g in reg.get("gaps", []):
        lines.append(f"### {g['id']} · {g['title']}  —  {g['priority']} / {g['state']} / verification={g['verification']}")
        lines.append(g.get("what_missing", "") or "_（缺什么节为空）_")
        lines.append(f"_档案: `{g.get('source_path', '?')}`_")
        lines.append("")
    return "\n".join(lines)


# ── 主入口（旧接口，向后兼容）────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_wiki_self_knowledge() -> str:
    """组装 OmniCompany 自知识摘要。

    进程级缓存（lru_cache）：单次 Python 进程只读一次盘。
    失败降级：任一组件抛异常→走兜底字符串。
    """
    try:
        readme_path = _OMNI_SRC / "README.md"
        readme_section = ""
        if readme_path.exists():
            # README 全文可读，但我们只塞能力五分类表（§一）——SpecParser
            # 判断"已有 vs 缺失"靠的是模块清单和各自 DESIGN.md 的核心目的，
            # README 补充的是"能力归属导航"。取 §一 这一节。
            readme_raw = readme_path.read_text(encoding="utf-8")
            m = re.search(r"^## 一、能力分类.*?(?=^## [二三四])", readme_raw, re.MULTILINE | re.DOTALL)
            readme_section = m.group(0).strip() if m else ""

        designs_section = _load_active_designs()
        gaps_section = _load_gaps_section()

        return (
            "# OmniCompany 自知识（从 wiki 动态加载）\n\n"
            "本摘要由 SpecParser 运行时从 DESIGN.md / README.md / docs/gaps/ 组装，"
            "反映当前 wiki 状态，非硬编码字符串。\n\n"
            "## 一、能力地图（README § 一）\n\n"
            f"{readme_section or '（README 缺失或未识别）'}\n\n"
            "## 二、各模块核心目的（active + design 级 DESIGN.md）\n\n"
            f"{designs_section}\n\n"
            "## 三、已识别的缺口（docs/gaps/）\n\n"
            f"{gaps_section}\n"
        )
    except Exception as e:
        logger.warning("wiki_loader 失败，走兜底: %s", e)
        return _FALLBACK_SUMMARY


# 降级用兜底（wiki 不可读时使用，保持 SpecParser 可工作）
_FALLBACK_SUMMARY = """# OmniCompany 自知识（兜底 · wiki 读取失败）

## 已有能力（硬编码兜底，可能过时）
- runtime/llm: LLMClient（RateLimiter + 令牌桶 + 指数退避重试）
- runtime/agent: AgentNodeLoop（4 层上下文压缩）
- runtime/info_audit: probe + post_hoc + piggyback tool + audit_store
- runtime/agent_crystallize: 经验沉淀（SpecPatch）
- protocol/format: Format 注册表（parent/components）
- packages/services/doctor: 管线健康诊断
- packages/services/guardian: 30+ 条 OMNI 规则

## 已知缺口
- 无可插拔记忆架构（无向量检索）
- 无 agent 委托/子 agent
- 无多模型 ensemble
- 无 agent 自建 skill 能力
- 无自动文件系统检查点

**建议**：若看到此兜底文字出现在 SpecParser 上下文里，说明 wiki_loader 读盘失败，
应排查 src/omnicompany/README.md 和 docs/gaps/ 是否存在。
"""
