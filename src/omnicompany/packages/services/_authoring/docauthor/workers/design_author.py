# [OMNI] origin=claude-code domain=services/docauthor/workers ts=2026-04-25T00:00:00Z type=router
# [OMNI] material_id="material:authoring.docauthor.design_doc_author.worker.py"
"""DesignDocAuthorWorker — Phase B 的 DESIGN.md 作者.

输入:  docauthor.design-request   {target_package_path, upgrade_from_skeleton?, prior_draft?, review_feedback?}
输出:  docauthor.design-draft     {design_path, design_content, sections_filled, scan_evidence, notes}

**生成规范** (见 distributed-docs.md OMNI-034):
  七节齐全 (## 状态 / 核心目的 / 核心接口 / 架构决策 / 数据流 / 拓扑 / 已知局限 / 参考资料)
  基础设施模块 (bus/core/runtime/protocol/cli/tools/tracing/primitives) 另加第 8 节 `## 接收意愿` (OMNI-034g)
  无 TBD / 占位 (OMNI-034d 精神)
  status 四选一 (skeleton / design / active / deprecated)

反泄漏同 ManifestAuthorWorker (D3): 不扫 gold_samples.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker, call_llm_json
from omnicompany.protocol.anchor import Verdict, VerdictKind

# 复用 manifest_author 的扫描 + 反泄漏基础设施
from .manifest_author import (
    _FORBIDDEN_PATH_MARKERS,
    _PlanHit,
    _default_repo_root,
    _format_list,
    _format_plan_hits,
    _grep_plan_mentions_with_excerpts,
    _is_core_infra_target,
    _load_allowed_text,
    _read_text_bounded,
    _target_slug,
)


# ═══════════════════════════════════════════════════════════════════
# 反泄漏白名单 — 与 manifest 共享规范, 但黄金 DESIGN 样本换 guardian/DESIGN.md
# ═══════════════════════════════════════════════════════════════════

_SPEC_SOURCES = (
    "docs/standards/protocol/self_narrative_three_files.md",  # 权威规范 (三件套)
    "docs/standards/_global/distributed-docs.md",
    "docs/standards/protocol/design_md_template.md",  # DESIGN 模板细则 (从属三件套)
)

_GOLDEN_EXAMPLES = (
    "src/omnicompany/packages/services/_core/guardian/DESIGN.md",  # 结构完整公开示例 (旧版含核心目的, 不违规)
    "src/omnicompany/packages/services/_diagnosis/tech_debt/DESIGN.md",
)

_MAX_DESIGN_CHARS = 30_000
_MAX_EXISTING_MANIFEST = 10_000
_MAX_CODE_SNIPPET_CHARS = 1_200  # 读一份源文件前多少字 (看 docstring + top-level classes)

# 三件套规范 §五: "核心目的" 归 README, 不再是 DESIGN 必需节 (2026-06-13 以新规范为准)
_REQUIRED_SECTIONS = (
    "## 状态",
    "## 核心接口",
    "## 架构决策",
    "## 数据流",    # "数据流 / 拓扑" 也包含此前缀
    "## 已知局限",
    "## 参考资料",
)
_INFRA_EXTRA_SECTION = "## 接收意愿"


# ═══════════════════════════════════════════════════════════════════
# 扫描结果
# ═══════════════════════════════════════════════════════════════════

@dataclass
class _DesignScan:
    target_path: str
    is_service: bool
    is_core_infra: bool
    existing_design: str | None        # 若升级 skeleton, 保留现有内容
    existing_manifest: str | None
    src_files: list[str]               # target 下 *.py / *.md 清单
    src_docstrings: dict[str, str]     # 关键 src 文件顶部 docstring (供接口归纳)
    plan_mentions: list[_PlanHit]
    sibling_designs: list[str]         # 兄弟包 (同父目录下) 的 DESIGN.md 相对路径 (仅列, 不读)


# ═══════════════════════════════════════════════════════════════════
# Worker
# ═══════════════════════════════════════════════════════════════════

class DesignDocAuthorWorker(Worker):
    DESCRIPTION = (
        "扫描指定 service/package 代码结构 + 提取源文件 docstring + 读已有 DESIGN + grep plan 语义节选, "
        "调 qwen-3.6-plus LLM 生成三件套规范合规的 DESIGN.md draft (六必需节, 核心目的归 README). "
        "基础设施模块自动加 ## 接收意愿 节. 反泄漏: 不扫 gold_samples."
    )
    FORMAT_IN = "docauthor.design-request"
    FORMAT_OUT = "docauthor.design-draft"

    def __init__(self, *, repo_root: Path | None = None, web_bus: Any = None) -> None:
        self._repo_root = (repo_root or _default_repo_root()).resolve()
        self._web_bus = web_bus

    # ─────────────────────────────────────────────────────────────

    def run(self, input_data: dict[str, Any]) -> Verdict:
        req = input_data.get(self.FORMAT_IN) or input_data
        target = (req.get("target_package_path") or req.get("target_service_path") or "").strip()
        prior_draft = (req.get("prior_draft") or "").strip()
        review_feedback = (req.get("review_feedback") or "").strip()
        iter_num = int(req.get("iter") or 0)
        max_refine_iters = int(req.get("max_refine_iters") or 1)
        upgrade_from_skeleton = bool(req.get("upgrade_from_skeleton"))

        if not target:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="missing target_package_path in design-request")

        try:
            self._assert_path_allowed(target)
        except ValueError as e:
            return Verdict(kind=VerdictKind.FAIL, diagnosis=str(e))

        try:
            scan = self._scan(target)
        except FileNotFoundError as e:
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"target not found: {e}")
        except ValueError as e:
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"scan failed: {e}")

        prompt_system, prompt_user = self._build_prompt(
            scan, prior_draft=prior_draft, review_feedback=review_feedback,
        )

        result = call_llm_json(
            system=prompt_system,
            user=prompt_user,
            web_bus=self._web_bus,
            caller="docauthor.design_author",
            role="runtime_main",
            max_tokens=14000,
        )

        if "_parse_error" in result:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"LLM JSON parse failed: {result.get('_parse_error')}",
                details={"_raw": (result.get("_raw") or "")[:2000]},
            )

        design_content = (result.get("design_content") or "").strip()
        if not design_content:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="LLM returned empty design_content",
                           details={"llm_result": result})

        # 硬校验骨架: OmniMark 头 + 七节齐全 (feedback_100pct_required_goes_to_skeleton)
        missing_sections = [s for s in _REQUIRED_SECTIONS if s not in design_content]
        if not design_content.lstrip().startswith("<!-- [OMNI]"):
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="OmniMark HTML 注释头缺失 (应以 '<!-- [OMNI] ...' 开头)",
                           details={"design_content": design_content[:500]})
        if missing_sections:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"missing sections: {missing_sections}",
                           details={"design_content": design_content})
        if scan.is_core_infra and _INFRA_EXTRA_SECTION not in design_content:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"core infra missing '{_INFRA_EXTRA_SECTION}' (OMNI-034g)",
                           details={"design_content": design_content})

        sections_filled = [s for s in _REQUIRED_SECTIONS if s in design_content]
        if scan.is_core_infra and _INFRA_EXTRA_SECTION in design_content:
            sections_filled.append(_INFRA_EXTRA_SECTION)

        output = {
            "design_path": f"{target.rstrip('/')}/DESIGN.md",
            "design_content": design_content,
            "sections_filled": sections_filled,
            # 透传 refine 元数据
            "target_package_path": target,
            "iter": iter_num,
            "max_refine_iters": max_refine_iters,
            "upgrade_from_skeleton": upgrade_from_skeleton,
            "scan_evidence": {
                "target_path": scan.target_path,
                "is_service": scan.is_service,
                "is_core_infra": scan.is_core_infra,
                "has_existing_design": scan.existing_design is not None,
                "has_existing_manifest": scan.existing_manifest is not None,
                "src_files_count": len(scan.src_files),
                "src_files": scan.src_files,
                "src_docstrings_count": len(scan.src_docstrings),
                "plan_mentions": [h.path for h in scan.plan_mentions],
                "plan_excerpts_count": sum(1 for h in scan.plan_mentions if h.excerpt),
                "sibling_designs": scan.sibling_designs,
            },
            "notes": result.get("notes") or "",
        }
        return Verdict(kind=VerdictKind.PASS, output=output)

    # ─────────────────────────────────────────────────────────────
    # 反泄漏
    # ─────────────────────────────────────────────────────────────

    def _assert_path_allowed(self, target: str) -> None:
        norm = target.replace("\\", "/")
        for marker in _FORBIDDEN_PATH_MARKERS:
            if marker in norm:
                raise ValueError(
                    f"target contains forbidden marker '{marker}' (DESIGN.md D3 反泄漏)."
                )

    # ─────────────────────────────────────────────────────────────
    # 扫描
    # ─────────────────────────────────────────────────────────────

    def _scan(self, target: str) -> _DesignScan:
        target = target.replace("\\", "/").rstrip("/")
        abs_target = (self._repo_root / target).resolve()
        if not abs_target.exists() or not abs_target.is_dir():
            raise FileNotFoundError(target)
        try:
            abs_target.relative_to(self._repo_root)
        except ValueError:
            raise ValueError(f"target outside repo_root: {target}")

        is_service = "/packages/services/" in f"/{target}/"
        is_core_infra = _is_core_infra_target(target)

        existing_design = _read_text_bounded(abs_target / "DESIGN.md", _MAX_DESIGN_CHARS)
        existing_manifest = _read_text_bounded(
            abs_target / ".omni" / "manifest.yaml", _MAX_EXISTING_MANIFEST
        )

        src_files, src_docstrings = _scan_src(abs_target, self._repo_root)

        plan_mentions = _grep_plan_mentions_with_excerpts(
            self._repo_root, _target_slug(target), max_hits=10
        )

        sibling_designs = _list_sibling_designs(abs_target, self._repo_root)

        return _DesignScan(
            target_path=target,
            is_service=is_service,
            is_core_infra=is_core_infra,
            existing_design=existing_design,
            existing_manifest=existing_manifest,
            src_files=src_files,
            src_docstrings=src_docstrings,
            plan_mentions=plan_mentions,
            sibling_designs=sibling_designs,
        )

    # ─────────────────────────────────────────────────────────────
    # Prompt
    # ─────────────────────────────────────────────────────────────

    def _build_prompt(
        self, scan: _DesignScan, *, prior_draft: str = "", review_feedback: str = "",
    ) -> tuple[str, str]:
        spec_text = _load_allowed_text(self._repo_root, _SPEC_SOURCES)
        golden_text = _load_allowed_text(self._repo_root, _GOLDEN_EXAMPLES)

        if scan.is_core_infra:
            target_type_label = "core infrastructure module (必须加第 8 节 ## 接收意愿)"
        elif scan.is_service:
            target_type_label = "service"
        else:
            target_type_label = "domain package"

        refine_block = ""
        if prior_draft and review_feedback:
            refine_block = _REFINE_SECTION_TEMPLATE.format(
                prior_draft=prior_draft,
                review_feedback=review_feedback,
            )

        system = _SYSTEM_PROMPT
        user = _USER_PROMPT_TEMPLATE.format(
            target=scan.target_path,
            target_type=target_type_label,
            existing_design=scan.existing_design or "(DESIGN.md 不存在 / skeleton)",
            existing_manifest=scan.existing_manifest or "(无现有 manifest)",
            src_files=_format_list(scan.src_files),
            src_docstrings=_format_docstrings(scan.src_docstrings),
            plan_excerpts=_format_plan_hits(scan.plan_mentions),
            sibling_designs=_format_list(scan.sibling_designs),
            spec_text=spec_text,
            golden_text=golden_text,
            refine_block=refine_block,
            require_reception="必须" if scan.is_core_infra else "无需",
        )
        return system, user


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

_PY_SUFFIXES = {".py"}
_MD_SUFFIXES = {".md"}
_SKIP_DIRS = {"__pycache__", ".omni", "_archive", "_graveyard", "_legacy", ".git"}
_MAX_SRC_FILES = 60


def _scan_src(abs_target: Path, repo_root: Path) -> tuple[list[str], dict[str, str]]:
    """列 target 下所有 .py/.md (跳 __pycache__/_archive), 抽关键文件的顶部 docstring."""
    files: list[str] = []
    docstrings: dict[str, str] = {}
    for root, dirs, names in _walk(abs_target):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in names:
            p = Path(root) / fname
            suf = p.suffix.lower()
            if suf not in _PY_SUFFIXES | _MD_SUFFIXES:
                continue
            rel = p.relative_to(abs_target).as_posix()
            files.append(rel)
            if len(files) >= _MAX_SRC_FILES:
                files.append("(truncated)")
                return files, docstrings

    # 抽 docstring: __init__.py + 顶层 .py (非 tests) 中最靠前的 10 份
    py_files = [f for f in files if f.endswith(".py") and "test" not in f.lower()]
    py_sorted = sorted(py_files, key=lambda f: (f.count("/"), f))[:10]
    for rel in py_sorted:
        abs_p = abs_target / rel
        snippet = _read_top_snippet(abs_p, _MAX_CODE_SNIPPET_CHARS)
        if snippet:
            docstrings[rel] = snippet
    return files, docstrings


def _walk(root: Path):
    import os
    for entry in os.walk(root):
        yield entry


def _read_top_snippet(path: Path, max_chars: int) -> str:
    """取文件顶部 docstring + 开头若干行. 截断到 max_chars."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not text:
        return ""
    snippet = text[: max_chars + 1]
    if len(text) > max_chars:
        snippet = snippet[:max_chars].rstrip() + "\n...(truncated)"
    return snippet


def _list_sibling_designs(abs_target: Path, repo_root: Path) -> list[str]:
    """列兄弟包 DESIGN.md (仅路径, 不读内容) 供 LLM 感知上下文."""
    parent = abs_target.parent
    out: list[str] = []
    try:
        for sib in sorted(parent.iterdir()):
            if not sib.is_dir() or sib == abs_target:
                continue
            d = sib / "DESIGN.md"
            if d.exists():
                out.append(d.relative_to(repo_root).as_posix())
    except OSError:
        pass
    return out[:10]


def _format_docstrings(d: dict[str, str]) -> str:
    if not d:
        return "(无 .py 文件 / 未抽出 docstring)"
    parts = []
    for k, v in d.items():
        parts.append(f"### {k}\n```\n{v}\n```")
    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# Prompt 文本
# ═══════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """\
你是 omnicompany 分布式文档的 DESIGN.md 生成器.

你的任务: 为一个 service / domain 子包 / 核心基础设施模块生成合规 `DESIGN.md`.

## DESIGN.md 必备结构 (OMNI-034c · 六节字面精确; 三件套规范: "核心目的"归 README, 不写进 DESIGN)

```markdown
<!-- [OMNI] origin=<origin> domain=<domain> ts=<YYYY-MM-DDTHH:MM:SSZ> type=doc status=<status> -->

# <pkg name> · 设计文档

## 状态
- **版本**: V<n> (<日期 升级原因>)
- **成熟度**: <skeleton|design|active|deprecated>
- **下一步**: <具体 · 不写 TBD>

## 核心接口
<列公共接口/类/函数/Worker · 带源码链接>

## 架构决策
### D1 · <标题>
**决策**: ...
**理由**: ...
(基础设施 ≥ 5 条; 服务 ≥ 3 条; domain 子包 ≥ 2 条)

## 数据流 / 拓扑
<ASCII 图或具体调用链 · 非抽象描述>

## 已知局限
- <局限 1> · 升级路径: <具体>
- <局限 2> · 升级路径: <具体>
(≥ 2 条, 每条**必须**带升级路径)

## 参考资料
- 相关 plan: [...]
- 规范: [...]
```

核心基础设施 (bus / core / runtime / protocol / cli / tools / tracing / primitives) **必须**另加:

```markdown
## 接收意愿
- **接收**: <接新实现提案 / 边界清晰的扩展>
- **不接收**: <业务语义 / 跨层干预>
- **边界信号**: <违反本模块定位时的信号>
```

## 硬规则

1. **OmniMark 头**: 第一行必须是 `<!-- [OMNI] origin=... domain=... ts=... type=doc status=... -->`
2. **status 四选一**: skeleton / design / active / deprecated · 不写其他词
3. **六节齐全**: 二级标题字面精确 (`## 状态` 不是 `## Status` 或 `## 状态更新`); "核心目的"属于 README, 不要写进 DESIGN
4. **基础设施加 接收意愿**: 若是 core infra, `## 接收意愿` 必加 (OMNI-034g)
5. **无 TBD / 占位**: 每节都填**具体**内容 (无 "待补" / "TODO" / "TBD")
6. **引用真实**: 参考资料里的 [路径] 必须是扫描里看到的真实文件
7. **架构决策**: 不数够条数不如不写; 但每条必须含 "决策" + "理由"
8. **已知局限**: 每条**必须**给升级路径, 不是纯吐槽

## 升级 skeleton 的纪律

若 existing_design 段给出当前 skeleton 内容 (多为 7 节 TBD):
- **保留**人类已填的具体信息 (若有的话)
- 用扫描里的 src_files + src_docstrings + plan_excerpts 把每节填实
- status 升级: skeleton → active (若代码已跑过真 E2E) 或 design (仍在搭)

## 输出格式

返回 JSON:
```json
{
  "design_content": "<完整 Markdown 文本, 从 '<!-- [OMNI]' 开头, 七节 (或 8 节) 齐全>",
  "notes": "<Worker 自报: 哪里不确定; 依据什么做的选择>"
}
```

design_content 是**原文 Markdown 字符串** (含 \\n 换行, 不要外层 markdown fence 包裹).
"""


_USER_PROMPT_TEMPLATE = """\
## 目标

- 路径: `{target}`
- 类型: {target_type}
- 第 8 节 ## 接收意愿 要求: **{require_reception}**

## 现有 DESIGN.md (若存在 · 升级 skeleton 要保留其中合理内容)

```
{existing_design}
```

## 现有 .omni/manifest.yaml (若有 · 作为"本包产什么 data"的参考)

```
{existing_manifest}
```

## src/ 下本包的文件清单

{src_files}

## 关键 .py 文件顶部片段 (docstring + top imports · 归纳"核心接口")

{src_docstrings}

## docs/plans/ 提到此目标的 plan (含节选 · 读出业务语义)

{plan_excerpts}

## 兄弟包的 DESIGN.md (仅路径 · 用于边界说明 "我 vs 兄弟" 的区别)

{sibling_designs}

## 规范权威 (distributed-docs.md 节选)

```
{spec_text}
```

## 合法公开范例 (guardian/DESIGN.md · 参考不照抄)

```
{golden_text}
```

{refine_block}

## 任务

综合以上, 生成 `{target}/DESIGN.md` 的合规内容.

OmniMark 头: ts=2026-04-25T00:00:00Z · origin=claude-code · domain 取 target 去 `src/omnicompany/` 前缀的路径.
status: 若 src_files 充裕且有 plan history 支撑 → active; 若仅目录存在无实体 → design; skeleton 只在实体全无时用.

严格按 system_prompt 的 JSON 格式返回.
"""


_REFINE_SECTION_TEMPLATE = """\
## 上一轮 draft (需修正)

```markdown
{prior_draft}
```

## Reviewer 反馈 (按此修正)

{review_feedback}

**修正规则**: 保留 Reviewer 没提到的合理部分; 只改被 Reviewer 指出的问题; 不整体重写.
"""
