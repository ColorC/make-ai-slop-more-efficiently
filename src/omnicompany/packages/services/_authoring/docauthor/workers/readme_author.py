# [OMNI] origin=ai-ide domain=services/docauthor/workers ts=2026-05-04T18:00:00Z type=router belongs_to_service=docauthor
# [OMNI] material_id="material:authoring.docauthor.readme_author.worker.py"
"""ReadmeAuthorWorker — README.md 作者 (自我叙事三件套).

输入:  docauthor.readme-request   {target_package_path, prior_draft?, review_feedback?}
输出:  docauthor.readme-draft     {readme_path, readme_content, sections_filled, scan_evidence, notes}

**生成规范** (见 self_narrative_three_files.md §四):
  6 节齐全 (## 这是什么 / 解决什么 / 不解决什么 / 设计目的与最终目标 / 规划 / 构成 / 想了解更多)
  顶部一句话定位 (≤ 30 字, 强动词开头, quote 块)
  指针式不复制下层认知

反泄漏同 ManifestAuthorWorker (D3): 不扫 gold_samples.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker, call_llm_json
from omnicompany.protocol.anchor import Verdict, VerdictKind

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
from .design_author import (
    _scan_src,
    _format_docstrings,
)


_SPEC_SOURCES = (
    "docs/standards/protocol/self_narrative_three_files.md",
)

# 金标范例 — tech_debt + registry README (严格按模板, lap_auditor 已归档不再用)
_GOLDEN_EXAMPLES = (
    "src/omnicompany/packages/services/_diagnosis/tech_debt/README.md",
    "src/omnicompany/packages/services/_core/registry/README.md",
)

_MAX_SPEC_CHARS = 30_000
_MAX_GOLDEN_CHARS = 12_000
_MAX_EXISTING_README = 10_000

_REQUIRED_SECTIONS = (
    "## 这是什么",
    "## 解决什么",  # "## 解决什么 / 不解决什么"
    "## 设计目的与最终目标",
    "## 规划",
    "## 构成",
    "## 想了解更多",
)


@dataclass
class _ReadmeScan:
    target_path: str
    is_service: bool
    is_core_infra: bool
    existing_readme: str | None
    existing_design: str | None
    existing_manifest: str | None
    src_files: list[str]
    src_docstrings: dict[str, str]
    plan_mentions: list[_PlanHit]
    sibling_readmes: list[str]


class ReadmeAuthorWorker(Worker):
    DESCRIPTION = (
        "扫指定 service/package 代码 + 读已有 DESIGN/manifest + grep plan 节选, "
        "调 qwen-3.6-plus 产合规 README.md draft (按 self_narrative_three_files.md §四 模板). "
        "6 节: 这是什么 / 解决什么 / 设计目的与最终目标 / 规划 / 构成 / 想了解更多."
    )
    FORMAT_IN = "docauthor.readme-request"
    FORMAT_OUT = "docauthor.readme-draft"

    def __init__(self, *, repo_root: Path | None = None, web_bus: Any = None) -> None:
        self._repo_root = (repo_root or _default_repo_root()).resolve()
        self._web_bus = web_bus

    def run(self, input_data: dict[str, Any]) -> Verdict:
        req = input_data.get(self.FORMAT_IN) or input_data
        target = (req.get("target_package_path") or req.get("target_service_path") or "").strip()
        prior_draft = (req.get("prior_draft") or "").strip()
        review_feedback = (req.get("review_feedback") or "").strip()
        iter_num = int(req.get("iter") or 0)
        max_refine_iters = int(req.get("max_refine_iters") or 1)

        if not target:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="missing target_package_path in readme-request")

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
            caller="docauthor.readme_author",
            role="runtime_main",
            max_tokens=10000,
        )

        if "_parse_error" in result:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"LLM JSON parse failed: {result.get('_parse_error')}",
                details={"_raw": (result.get("_raw") or "")[:2000]},
            )

        readme_content = (result.get("readme_content") or "").strip()
        if not readme_content:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="LLM returned empty readme_content",
                           details={"llm_result": result})

        # 硬校验: OmniMark 头 + 6 节齐全
        missing_sections = [s for s in _REQUIRED_SECTIONS if s not in readme_content]
        if not readme_content.lstrip().startswith("<!-- [OMNI]"):
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="OmniMark HTML 注释头缺失 (应以 '<!-- [OMNI] ...' 开头)",
                           details={"readme_content": readme_content[:500]})
        if missing_sections:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"missing sections: {missing_sections}",
                           details={"readme_content": readme_content})

        sections_filled = [s for s in _REQUIRED_SECTIONS if s in readme_content]

        output = {
            "readme_path": f"{target.rstrip('/')}/README.md",
            "readme_content": readme_content,
            "sections_filled": sections_filled,
            "target_package_path": target,
            "iter": iter_num,
            "max_refine_iters": max_refine_iters,
            "scan_evidence": {
                "target_path": scan.target_path,
                "is_service": scan.is_service,
                "is_core_infra": scan.is_core_infra,
                "has_existing_readme": scan.existing_readme is not None,
                "has_existing_design": scan.existing_design is not None,
                "has_existing_manifest": scan.existing_manifest is not None,
                "src_files_count": len(scan.src_files),
                "plan_mentions_count": len(scan.plan_mentions),
                "sibling_readmes": scan.sibling_readmes,
            },
            "notes": result.get("notes") or "",
        }
        return Verdict(kind=VerdictKind.PASS, output=output)

    def _assert_path_allowed(self, target: str) -> None:
        norm = target.replace("\\", "/")
        for marker in _FORBIDDEN_PATH_MARKERS:
            if marker in norm:
                raise ValueError(
                    f"target contains forbidden marker '{marker}' (反泄漏)."
                )

    def _scan(self, target: str) -> _ReadmeScan:
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

        existing_readme = _read_text_bounded(abs_target / "README.md", _MAX_EXISTING_README)
        existing_design = _read_text_bounded(abs_target / "DESIGN.md", _MAX_EXISTING_README)
        existing_manifest = _read_text_bounded(
            abs_target / ".omni" / "manifest.yaml", 10_000
        )

        src_files, src_docstrings = _scan_src(abs_target, self._repo_root)

        plan_mentions = _grep_plan_mentions_with_excerpts(
            self._repo_root, _target_slug(target), max_hits=10
        )

        sibling_readmes = _list_sibling_readmes(abs_target, self._repo_root)

        return _ReadmeScan(
            target_path=target,
            is_service=is_service,
            is_core_infra=is_core_infra,
            existing_readme=existing_readme,
            existing_design=existing_design,
            existing_manifest=existing_manifest,
            src_files=src_files,
            src_docstrings=src_docstrings,
            plan_mentions=plan_mentions,
            sibling_readmes=sibling_readmes,
        )

    def _build_prompt(
        self, scan: _ReadmeScan, *, prior_draft: str = "", review_feedback: str = "",
    ) -> tuple[str, str]:
        spec_text = _load_allowed_text(self._repo_root, _SPEC_SOURCES)
        golden_text = _load_allowed_text(self._repo_root, _GOLDEN_EXAMPLES)

        if scan.is_core_infra:
            target_type_label = "core infrastructure module"
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
            existing_readme=scan.existing_readme or "(README.md 不存在)",
            existing_design=scan.existing_design or "(DESIGN.md 不存在)",
            existing_manifest=scan.existing_manifest or "(无 manifest)",
            src_files=_format_list(scan.src_files),
            src_docstrings=_format_docstrings(scan.src_docstrings),
            plan_excerpts=_format_plan_hits(scan.plan_mentions),
            sibling_readmes=_format_list(scan.sibling_readmes),
            spec_text=spec_text,
            golden_text=golden_text,
            refine_block=refine_block,
        )
        return system, user


def _list_sibling_readmes(abs_target: Path, repo_root: Path) -> list[str]:
    parent = abs_target.parent
    out: list[str] = []
    try:
        for sib in sorted(parent.iterdir()):
            if not sib.is_dir() or sib == abs_target:
                continue
            r = sib / "README.md"
            if r.exists():
                out.append(r.relative_to(repo_root).as_posix())
    except OSError:
        pass
    return out[:10]


_SYSTEM_PROMPT = """\
你是 omnicompany 自我叙事三件套的 README.md 生成器.

## README.md 必备结构 (self_narrative_three_files.md §四 模板)

```markdown
<!-- [OMNI] origin=<origin> domain=<domain> ts=<YYYY-MM-DDTHH:MM:SSZ> type=doc status=active belongs_to_service=<service> -->
<!-- [OMNI] summary="..." -->
<!-- [OMNI] why="..." -->
<!-- [OMNI] tags=readme,<scope>,self-narrative -->
<!-- [OMNI] material_id="material:..." -->

# <模块名>

> 一句话定位 (≤ 30 字, 强动词开头)

## 这是什么
<2-3 段叙事 · 我是什么 / 在系统里扮演什么角色 · 不写实现细节, 不写架构, 不写具体怎么用>

## 解决什么 / 不解决什么
**解决**:
- ...

**不解决**:
- ...

## 设计目的与最终目标
<段落形式 · 为什么做 / 当下能认知的最终目标>

## 规划
- **当前**: ...
- **下一步**: ...
- **远景**: ...

## 构成
<指针式列表 · 指向子模块 README · 不复制子模块认知>
- [<子模块 1>](<路径>/README.md) — <一句话>
- ...

## 想了解更多
- 架构 → [DESIGN.md](DESIGN.md)
- 怎么用 → [SKILL.md](SKILL.md)
- ...
```

## 硬规则

1. **OmniMark 头**: 第一行必须是 `<!-- [OMNI] ... -->`, 含 belongs_to_service 字段
2. **6 节齐全**: 字面精确 (`## 这是什么` / `## 解决什么 / 不解决什么` / `## 设计目的与最终目标` / `## 规划` / `## 构成` / `## 想了解更多`)
3. **一句话定位**: ≤ 30 字, 强动词开头, 跟在标题下用 `> ...` quote
4. **指针式不复制**: 构成段只指针 (链接 + 一句话简述), 不复制子模块完整描述
5. **不写架构细节**: 那是 DESIGN.md 的事
6. **不写操作步骤**: 那是 SKILL.md 的事
7. **远景段诚实**: "当下能认知的最终目标" 会随认知更新, 不假装一锤定音
8. **相对路径基准**: README 所在目录是基准, 不是上层. 计算 `..` 退层数:
   - `packages/services/<group>/<service>/` → repo_root: **6 个 `..`** (退 group/services/packages/omnicompany/src + 1 层)
   - `packages/services/<service>/` (无 group) → repo_root: **5 个 `..`**
   - `packages/domains/<area>/<pkg>/` → repo_root: **5 个 `..`**
   - 例: 从 `services/_authoring/docauthor/README.md` 引用 `data/`: `[路径](../../../../../../data/...)` 6 个
   - **算错就是死链**, Reviewer 会抓出来 → critical

## 输出格式

返回 JSON:
```json
{
  "readme_content": "<完整 Markdown 文本, 从 '<!-- [OMNI]' 开头, 6 节齐全>",
  "notes": "<Worker 自报: 哪里不确定; 依据什么做的选择>"
}
```
"""


_USER_PROMPT_TEMPLATE = """\
## 目标

- 路径: `{target}`
- 类型: {target_type}

## 现有 README.md (若存在 · 升级时保留合理内容)

```
{existing_readme}
```

## 现有 DESIGN.md (作为 README 一致性参考 · 不复制)

```
{existing_design}
```

## 现有 .omni/manifest.yaml

```
{existing_manifest}
```

## src/ 下本包文件清单

{src_files}

## 关键 .py 顶部 docstring

{src_docstrings}

## docs/plans/ 提到此目标的 plan 节选

{plan_excerpts}

## 兄弟包 README (仅路径, 用于 cross-reference 参考)

{sibling_readmes}

## 规范权威 (self_narrative_three_files.md 节选)

```
{spec_text}
```

## 金标范例 (tech_debt/registry README · 严格按模板, 参考不照抄)

```
{golden_text}
```

{refine_block}

## 任务

综合以上, 生成 `{target}/README.md` 的合规内容.

OmniMark 头: ts=2026-05-04T18:00:00Z · origin=ai-ide · domain 取 target 去 `src/omnicompany/` 前缀 · type=doc · status=active · belongs_to_service=<service 名 · 取路径 `packages/services/(_*/)?<name>/` 中的 name>.

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
