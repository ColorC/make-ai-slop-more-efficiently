# [OMNI] origin=claude-code domain=services/knowledge/seed.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:learning.knowledge.rule_based_seeder.py"
"""omnikb.seed — 规则化提取知识库初稿。

不调 LLM, 纯文件系统扫描 + 正则提取, 把以下来源转化为 draft 状态的 entry 落到
data/knowledge/:

  1. core/pipelines.py 的 register(PipelineEntry(...)) → 每个管线一份 KArchitectureEntry (draft)
  2. packages/<ns>/<pkg>/__init__.py 的 docstring → 每个业务包一份 KArchitectureEntry (draft)
  3. docs/plans/<date>-<topic>/README.md 标题 → 每个 plan 一份 KExperimentEntry (draft)
  4. _graveyard/**/_RETIRED.md → 每个退役物一份 KExperimentEntry (draft, status='abandoned')
  5. CLAUDE.md 的章节 → 顶层 KArchitectureEntry (draft, kb.arch.codex_skill_root)

所有 entry 标 maturity=draft, 等人或 LLM 后续来填实质内容。

调用方式:
  python -m omnicompany.packages.services._learning.knowledge.seed
  或
  from omnicompany.packages.services._learning.knowledge.seed import seed_from_rules
  seed_from_rules(project_root)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from omnicompany.packages.services._learning.knowledge.schema import (
    KArchitectureEntry,
    KExperimentEntry,
)
from omnicompany.packages.services._learning.knowledge.store import KBStore

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 1. 从 core/pipelines.py 提取
# ═══════════════════════════════════════════════════════════

# 匹配 register(PipelineEntry( 起到 description= 的开始, 然后单独抓 description 字段。
# Python 的隐式字符串拼接 description=("a" "b" "c") 用一个独立的 _DESC_TOKEN_RE 处理。
_PIPELINE_NAME_RE = re.compile(
    r"register\(\s*PipelineEntry\(\s*name\s*=\s*[\"']([^\"']+)[\"']",
)

_DOMAIN_RE = re.compile(
    r'domain\s*=\s*[\"\']([^\"\']+)[\"\']',
)


def _extract_description_after(text: str, start: int) -> str:
    """从 text[start:] 中找下一个 description= 字段, 处理多种写法:
      - description="single line"
      - description=("part 1" "part 2" ...)
      - description=("part 1"\n  "part 2"\n)
      - description='single quote'
    返回拼接后的完整字符串, 拿不到时返回空字符串。
    """
    desc_start = text.find("description", start)
    if desc_start == -1:
        return ""
    # 找到 = 之后的第一个非空字符
    eq = text.find("=", desc_start)
    if eq == -1:
        return ""
    pos = eq + 1
    # 跳过空白和可选的左括号
    while pos < len(text) and text[pos] in " \t\n":
        pos += 1
    if pos < len(text) and text[pos] == "(":
        pos += 1

    parts: list[str] = []
    # 收集所有相邻的字符串字面量 (隐式拼接)
    while pos < len(text):
        # 跳过空白
        while pos < len(text) and text[pos] in " \t\n":
            pos += 1
        if pos >= len(text) or text[pos] not in ('"', "'"):
            break
        quote = text[pos]
        pos += 1
        chunk = []
        while pos < len(text) and text[pos] != quote:
            if text[pos] == "\\" and pos + 1 < len(text):
                chunk.append(text[pos + 1])
                pos += 2
                continue
            chunk.append(text[pos])
            pos += 1
        parts.append("".join(chunk))
        pos += 1  # 跳过结束引号
    return "".join(parts)


def _extract_domain_after(text: str, start: int, max_lookahead: int = 600) -> str | None:
    """在 text[start:start+lookahead] 内找 domain="..." """
    chunk = text[start:start + max_lookahead]
    m = _DOMAIN_RE.search(chunk)
    return m.group(1) if m else None


def seed_pipelines(store: KBStore) -> list[KArchitectureEntry]:
    """从 core/pipelines.py 提取所有 registered pipeline 为 KArch (draft)。

    每个 pipeline 都生成一份 KArch, 含:
      - 完整的多行 description 拼接
      - layer/topic/pipeline/domain 多维 tag
      - 至少 1 个 code_anchor 指向 core/pipelines.py 的注册块附近
    """
    src = store.project_root / "src" / "omnicompany" / "core" / "pipelines.py"
    if not src.exists():
        return []
    text = src.read_text(encoding="utf-8", errors="ignore")
    lines_count = text.count("\n") + 1

    created: list[KArchitectureEntry] = []
    for m in _PIPELINE_NAME_RE.finditer(text):
        name = m.group(1)
        # 计算所在行号 (1-indexed)
        line_no = text[:m.start()].count("\n") + 1

        full_desc = _extract_description_after(text, m.end()).strip()
        domain = _extract_domain_after(text, m.end())

        kb_id = f"kb.arch.pipeline.{name.replace('-', '_')}"
        if store.find_by_id(kb_id) is not None:
            continue

        # 构造 tag 列表 (多维, 让索引能按多个维度查)
        tags = ["layer.pipeline", "topic.pipeline", f"pipeline.{name}"]
        if domain:
            tags.append(f"domain.{domain}")

        # description 截断到 280 字符避免长摘要污染 frontmatter, 完整内容写入 body
        short_desc = full_desc if len(full_desc) <= 280 else (full_desc[:277] + "...")

        body = _build_pipeline_body(name, full_desc, domain)

        entry = KArchitectureEntry(
            id=kb_id,
            name=f"Pipeline: {name}",
            description=short_desc,
            tags=tags,
            maturity="draft",
            scope="omnicompany",
            code_anchors=[
                f"src/omnicompany/core/pipelines.py:L{line_no}-L{min(line_no + 25, lines_count)}",
            ],
        )
        store.write_entry(entry, body=body)
        created.append(entry)
    return created


def _build_pipeline_body(name: str, full_desc: str, domain: str | None) -> str:
    """为 pipeline KArch 生成结构化 body, 给后续人/LLM 补充用的脚手架。

    刻意把"已知信息"和"待补充"分开, 让 agent 一眼能看出哪些字段需要 enrich。
    """
    desc_block = full_desc if full_desc else "_(从 register block 未提取到 description)_"
    domain_block = f"`{domain}`" if domain else "_(未声明)_"

    return f"""# Pipeline: {name}

> **已知概要**: {desc_block}

## Identity

| Field | Value |
|---|---|
| Pipeline name | `{name}` |
| Domain tag | {domain_block} |
| Registered in | `src/omnicompany/core/pipelines.py` |
| Maturity stage (此 wiki 条目) | draft (auto-seeded) |

## Why this exists

_(待补充: 这条管线解决了什么问题, 为什么不能用其他已有管线?)_

## How it works

_(待补充: 主要节点流程, 关键 Format, 核心 Router, 输入输出契约)_

## Files

- `src/omnicompany/core/pipelines.py` — 注册条目所在
- _(待补充: build_pipeline / build_bindings 所在的 run.py)_
- _(待补充: pipeline.py / routers.py)_

## Related

- _(待补充: 关联的 KDecision, 例如设计选择 ADR)_
- _(待补充: 关联的 KArchitecture, 例如其依赖的核心抽象)_
- _(待补充: 关联的 KExperiment, 例如设计阶段的试验)_

## Known limitations

_(待补充: 当前已知 bug, 未实现部分, 不适用场景)_

## Change log

- {_today()} — auto-seeded from `core/pipelines.py` register block
"""


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


# ═══════════════════════════════════════════════════════════
# 2. 从 package __init__.py docstring 提取
# ═══════════════════════════════════════════════════════════

_DOCSTRING_RE = re.compile(r'^\s*(?:#[^\n]*\n)*\s*"""([\s\S]*?)"""', re.MULTILINE)


def seed_packages(store: KBStore) -> list[KArchitectureEntry]:
    """对每个 packages/<ns>/<pkg>/ 提取 __init__.py 顶部 docstring 作为 KArch。

    每个 KArch 含:
      - 短 description (头一段或 280 字截断)
      - 完整 docstring 写入 body 的 "Auto-seeded summary" 段
      - 多维 tags: layer.<ns>, topic.package, domain.<pkg_name>
      - code_anchors 指向 __init__.py + (pipeline.py / routers.py / 子目录) 列表
    """
    pkg_root = store.src_packages
    if not pkg_root.exists():
        return []

    created: list[KArchitectureEntry] = []
    for init_file in pkg_root.rglob("__init__.py"):
        if "__pycache__" in init_file.parts:
            continue
        if "_graveyard" in init_file.parts or "_archive" in init_file.parts:
            continue

        rel = init_file.parent.relative_to(pkg_root)
        if rel == Path("."):
            continue
        if not (init_file.parent / "pipeline.py").exists() and not (init_file.parent / "routers.py").exists():
            continue

        text = init_file.read_text(encoding="utf-8", errors="ignore")
        m = _DOCSTRING_RE.search(text)
        if not m:
            continue
        doc_full = m.group(1).strip()
        if not doc_full:
            continue

        pkg_slash = "/".join(rel.parts)            # services/absorption
        pkg_underscore = "_".join(rel.parts)
        pkg_name = rel.parts[-1]
        layer = rel.parts[0] if len(rel.parts) >= 2 else "unknown"  # services / domains / vendors

        kb_id = f"kb.arch.package.{pkg_underscore}"
        if store.find_by_id(kb_id) is not None:
            continue

        # 短描述: docstring 的第一段 (到第一个空行)
        first_para = _first_paragraph(doc_full)
        short_desc = first_para if len(first_para) <= 280 else (first_para[:277] + "...")

        # 收集额外的 code_anchor (邻近的 pipeline.py / routers.py / 子目录)
        anchors = [f"src/omnicompany/packages/{pkg_slash}/__init__.py"]
        for sib in ("pipeline.py", "routers.py", "run.py", "formats.py"):
            if (init_file.parent / sib).exists():
                anchors.append(f"src/omnicompany/packages/{pkg_slash}/{sib}")

        tags = [
            "topic.package",
            f"layer.{layer}",
            f"domain.{pkg_name}",
        ]

        body = _build_package_body(pkg_slash, layer, pkg_name, doc_full, anchors)

        entry = KArchitectureEntry(
            id=kb_id,
            name=f"Package: packages/{pkg_slash}",
            description=short_desc,
            tags=tags,
            maturity="draft",
            scope="omnicompany",
            code_anchors=anchors,
        )
        store.write_entry(entry, body=body)
        created.append(entry)
    return created


def _first_paragraph(text: str) -> str:
    """提取文本的第一段 (到第一个空行)。"""
    lines = []
    for line in text.splitlines():
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line.strip())
    return " ".join(lines)


def _build_package_body(
    pkg_slash: str,
    layer: str,
    pkg_name: str,
    doc_full: str,
    anchors: list[str],
) -> str:
    """package KArch 的 body 模板。

    "Auto-seeded summary" 段保留完整 docstring 让人/agent 能看到原始信息,
    其他段是结构化占位让 R3.2 LLM enrichment 时填充。
    """
    anchor_lines = "\n".join(f"- `{a}`" for a in anchors)
    return f"""# Package: packages/{pkg_slash}

> **Layer**: `{layer}` · **Package name**: `{pkg_name}` · **Maturity (this wiki entry)**: draft (auto-seeded)

## Auto-seeded summary

This is the verbatim docstring from `__init__.py`, preserved here so future
agents and humans can see the package's own self-description without re-reading
the source. R3.2 LLM enrichment should keep this and add the structured
sections below.

```
{doc_full}
```

## Why this package exists

_(待补充: 这个包解决什么问题? 为什么是独立的包而不是其他包的子模块?)_

## Public surface

_(待补充: 哪些类/函数/管线是其他包应该 import 的? 哪些是内部细节?)_

## Internal structure

_(待补充: 子模块布局, 关键文件作用)_

## Files

{anchor_lines}

## Related

- _(待补充: 依赖的 KArchitecture / KDecision)_
- _(待补充: 调用此包的其他 KArchitecture)_

## Known limitations

_(待补充: 当前已知限制 / TODO / 设计妥协)_

## Change log

- {_today()} — auto-seeded from `__init__.py` docstring
"""


# ═══════════════════════════════════════════════════════════
# 3. 从 docs/plans/* 提取
# ═══════════════════════════════════════════════════════════

_PLAN_DIR_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\](.+)$")


def seed_plans(store: KBStore) -> list[KExperimentEntry]:
    """每个 docs/plans/[YYYY-MM-DD]TOPIC/ 目录提取一份 KExp draft。

    每份 KExp 含:
      - name: README.md 的 H1 (优先) 或目录名
      - 完整的 README 头几段写到 body 的 "Plan summary" 段
      - 列出 plan 目录里的所有 .md 文件作为 followup 锚点
    """
    plans_dir = store.project_root / "docs" / "plans"
    if not plans_dir.exists():
        return []

    created: list[KExperimentEntry] = []
    for plan_dir in plans_dir.iterdir():
        if not plan_dir.is_dir():
            continue
        m = _PLAN_DIR_RE.match(plan_dir.name)
        if not m:
            continue
        date = m.group(1)
        topic_raw = m.group(2)
        topic_slug = re.sub(r"[^a-zA-Z0-9_]+", "_", topic_raw).strip("_").lower()

        kb_id = f"kb.experiment.{date.replace('-', '')}_{topic_slug}"
        if store.find_by_id(kb_id) is not None:
            continue

        # 优先读 README.md 的 H1 + 第一段
        name = topic_raw.replace("-", " ").strip().title()
        readme_excerpt = ""
        readme = plan_dir / "README.md"
        if readme.exists():
            try:
                text = readme.read_text(encoding="utf-8", errors="ignore")
                h1 = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
                if h1:
                    name = h1.group(1).strip()
                # 取 README 的前 1500 字符作为 summary 来源
                readme_excerpt = text[:1500]
            except OSError:
                pass

        # 列出该 plan 目录下所有 md 文件
        plan_files: list[str] = []
        for f in sorted(plan_dir.iterdir()):
            if f.is_file() and f.suffix == ".md" and not f.name.startswith("_"):
                plan_files.append(str(f.relative_to(store.project_root)).replace("\\", "/"))

        # short description: H1 + 一句话
        short_desc = name
        if readme_excerpt:
            first_para = _first_paragraph(_strip_md_headers(readme_excerpt))
            if first_para:
                short_desc = (first_para[:277] + "...") if len(first_para) > 280 else first_para

        body = _build_plan_body(plan_dir.name, name, readme_excerpt, plan_files)

        entry = KExperimentEntry(
            id=kb_id,
            name=name,
            description=short_desc,
            tags=["topic.plan", f"date.{date}"],
            maturity="draft",
            date_started=date,
            method_summary=f"see docs/plans/{plan_dir.name}/",
            status="documented",
            followups=plan_files[:10],  # 最多 10 份计划文件作为 followup
        )
        store.write_entry(entry, body=body)
        created.append(entry)
    return created


def _strip_md_headers(text: str) -> str:
    """去掉 markdown header 行, 留下纯散文 (用于提取首段)。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith("#"))


def _build_plan_body(plan_dir_name: str, title: str, readme_excerpt: str, files: list[str]) -> str:
    files_block = "\n".join(f"- `{f}`" for f in files) if files else "- _(no files found)_"
    excerpt_block = readme_excerpt.strip() if readme_excerpt else "_(plan README missing)_"
    return f"""# {title}

> Plan directory: `docs/plans/{plan_dir_name}/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
{excerpt_block}
```

## Plan files

{files_block}

## Hypothesis

_(待补充: 这个 plan 的核心假设, 为什么需要做)_

## Method

_(待补充: 实施方法, 关键步骤)_

## Samples

_(待补充: 跑过的样本, 各自结果)_

## Findings

_(待补充: 关键发现, 哪些假设被验证, 哪些被推翻)_

## Followups

_(待补充: 后续 TODO, 关联其他 plan / 计划目录中的文件已自动列在 frontmatter)_

## Change log

- {_today()} — auto-seeded from plan README
"""


# ═══════════════════════════════════════════════════════════
# 4. 从 _graveyard/_RETIRED.md 提取
# ═══════════════════════════════════════════════════════════

def seed_retired(store: KBStore) -> list[KExperimentEntry]:
    """每个 _graveyard 下的 _RETIRED.md 转为一份 KExp (maturity=deprecated)。

    每份 KExp 含:
      - 完整的 _RETIRED.md 全文写到 body 的 "Retirement notes" 段
      - description 是从 _RETIRED.md 第一段提取的退役理由
      - tags 含 retired-from 路径
    """
    grave = store.project_root / "src" / "omnicompany" / "_graveyard"
    if not grave.exists():
        return []

    created: list[KExperimentEntry] = []
    for retired in grave.rglob("_RETIRED.md"):
        rel = retired.parent.relative_to(grave)
        slug = "_".join(rel.parts).lower()
        kb_id = f"kb.experiment.retired_{slug}"
        if store.find_by_id(kb_id) is not None:
            continue

        try:
            text = retired.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        # 提取退役日期 (第一行通常是 "Retired YYYY-MM-DD")
        lines = text.strip().splitlines()
        date = ""
        if lines:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", lines[0])
            if m:
                date = m.group(1)

        # 短描述 = _RETIRED.md 的第一段散文
        first_para = _first_paragraph(_strip_md_headers(text))
        short_desc = (first_para[:277] + "...") if len(first_para) > 280 else first_para
        if not short_desc:
            short_desc = f"Retired: {rel}"

        body = _build_retired_body(str(rel), text, date)

        entry = KExperimentEntry(
            id=kb_id,
            name=f"Retired: {rel}",
            description=short_desc,
            tags=[
                "topic.retired",
                "stage.abandoned",
                f"retired_from.{slug}",
            ],
            maturity="deprecated",
            date_started="",
            date_concluded=date,
            method_summary=f"see _graveyard/{rel}/",
            status="abandoned",
            findings_summary=[
                f"原始位置: src/omnicompany/_graveyard/{rel}",
                "退役理由: 见 _RETIRED.md (本 entry body 含全文)",
            ],
        )
        store.write_entry(entry, body=body)
        created.append(entry)
    return created


def _build_retired_body(rel_path: str, retired_full: str, date: str) -> str:
    return f"""# Retired: {rel_path}

> Original location: `src/omnicompany/_graveyard/{rel_path}/`
> Retired on: {date or "unknown"}

## Retirement notes (verbatim from _RETIRED.md)

{retired_full.strip()}

## What was lost

_(待补充: 这次退役放弃了什么能力? 是否有替代方案?)_

## Why it failed / was abandoned

_(待补充: 退役的根本原因, 不只是表面的 "no callers")_

## Lessons for future attempts

_(待补充: 如果将来重做类似系统应该注意什么?)_

## Resurrection conditions

_(待补充: 在什么情况下值得复活? 如本次 OmniKB 的复活就是一个例子)_

## Change log

- {_today()} — auto-seeded from `_graveyard/{rel_path}/_RETIRED.md`
"""


# ═══════════════════════════════════════════════════════════
# 总入口
# ═══════════════════════════════════════════════════════════

def seed_from_rules(project_root: Path, *, dry_run: bool = False) -> dict[str, int]:
    """跑全部规则提取, 返回每类创建数量统计。

    dry_run=True 时只统计不写盘 (TODO 后续加, 当前实现总是真写)。
    """
    store = KBStore(project_root)

    pipelines = seed_pipelines(store)
    packages = seed_packages(store)
    plans = seed_plans(store)
    retired = seed_retired(store)

    stats = {
        "pipelines": len(pipelines),
        "packages": len(packages),
        "plans": len(plans),
        "retired": len(retired),
        "total": len(pipelines) + len(packages) + len(plans) + len(retired),
    }
    logger.info("[omnikb.seed] rule-based seed: %s", stats)
    return stats


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = Path(__file__).resolve().parents[5]
    stats = seed_from_rules(root)
    print(f"Seed complete: {stats}")
    sys.exit(0)
