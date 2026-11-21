# [OMNI] origin=ai-ide domain=services/docauthor/workers ts=2026-05-04T18:05:00Z type=router belongs_to_service=docauthor
# [OMNI] material_id="material:authoring.docauthor.skill_author.worker.py"
"""SkillAuthorWorker — SKILL.md 作者 (自我叙事三件套).

输入:  docauthor.skill-request   {target_package_path, prior_draft?, review_feedback?}
输出:  docauthor.skill-draft     {skill_path, skill_content, sections_filled, scan_evidence, notes}

**生成规范** (见 self_narrative_three_files.md §六):
  YAML frontmatter (name / description / user-invocable / disable-model-invocation)
  6 节齐全 (## 适用范围 / 前置条件 / 操作步骤 / 入口清单 / 故障排查 / 想了解更多)

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

# 金标范例 — tech_debt + registry SKILL (lap_auditor 已归档不再用)
_GOLDEN_EXAMPLES = (
    "src/omnicompany/packages/services/_diagnosis/tech_debt/SKILL.md",
    "src/omnicompany/packages/services/_core/registry/SKILL.md",
)

_MAX_SPEC_CHARS = 30_000
_MAX_GOLDEN_CHARS = 12_000
_MAX_EXISTING_SKILL = 10_000

_REQUIRED_SECTIONS = (
    "## 适用范围",
    "## 前置条件",
    "## 操作步骤",
    "## 入口清单",
    "## 故障排查",
    "## 想了解更多",
)


@dataclass
class _SkillScan:
    target_path: str
    is_service: bool
    is_core_infra: bool
    existing_skill: str | None
    existing_readme: str | None
    existing_design: str | None
    src_files: list[str]
    src_docstrings: dict[str, str]
    plan_mentions: list[_PlanHit]
    sibling_skills: list[str]
    cli_commands: str  # 从 cli/commands/ grep 出来的入口提示


class SkillAuthorWorker(Worker):
    DESCRIPTION = (
        "扫指定 service/package 代码 + 读已有 README/DESIGN + grep plan 跟 cli 入口, "
        "调 qwen-3.6-plus 产合规 SKILL.md draft (按 self_narrative_three_files.md §六 模板). "
        "6 节: 适用范围 / 前置条件 / 操作步骤 / 入口清单 / 故障排查 / 想了解更多. "
        "顶部 YAML frontmatter (name/description/user-invocable=false)."
    )
    FORMAT_IN = "docauthor.skill-request"
    FORMAT_OUT = "docauthor.skill-draft"

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
                           diagnosis="missing target_package_path in skill-request")

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
            caller="docauthor.skill_author",
            role="runtime_main",
            max_tokens=10000,
        )

        if "_parse_error" in result:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"LLM JSON parse failed: {result.get('_parse_error')}",
                details={"_raw": (result.get("_raw") or "")[:2000]},
            )

        skill_content = (result.get("skill_content") or "").strip()
        if not skill_content:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="LLM returned empty skill_content",
                           details={"llm_result": result})

        # 硬校验: YAML frontmatter (name) + OmniMark 头 + 6 节齐全
        missing_sections = [s for s in _REQUIRED_SECTIONS if s not in skill_content]
        stripped = skill_content.lstrip()
        if not stripped.startswith("---"):
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="YAML frontmatter 缺失 (应以 '---' 开头)",
                           details={"skill_content": skill_content[:500]})
        if "<!-- [OMNI]" not in skill_content[:2000]:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="OmniMark 头缺失 (frontmatter 后应紧跟 '<!-- [OMNI] ...')",
                           details={"skill_content": skill_content[:500]})
        if missing_sections:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"missing sections: {missing_sections}",
                           details={"skill_content": skill_content})

        sections_filled = [s for s in _REQUIRED_SECTIONS if s in skill_content]

        output = {
            "skill_path": f"{target.rstrip('/')}/SKILL.md",
            "skill_content": skill_content,
            "sections_filled": sections_filled,
            "target_package_path": target,
            "iter": iter_num,
            "max_refine_iters": max_refine_iters,
            "scan_evidence": {
                "target_path": scan.target_path,
                "is_service": scan.is_service,
                "is_core_infra": scan.is_core_infra,
                "has_existing_skill": scan.existing_skill is not None,
                "has_existing_readme": scan.existing_readme is not None,
                "has_existing_design": scan.existing_design is not None,
                "src_files_count": len(scan.src_files),
                "plan_mentions_count": len(scan.plan_mentions),
                "sibling_skills": scan.sibling_skills,
                "cli_commands_found": bool(scan.cli_commands),
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

    def _scan(self, target: str) -> _SkillScan:
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

        existing_skill = _read_text_bounded(abs_target / "SKILL.md", _MAX_EXISTING_SKILL)
        existing_readme = _read_text_bounded(abs_target / "README.md", _MAX_EXISTING_SKILL)
        existing_design = _read_text_bounded(abs_target / "DESIGN.md", _MAX_EXISTING_SKILL)

        src_files, src_docstrings = _scan_src(abs_target, self._repo_root)
        plan_mentions = _grep_plan_mentions_with_excerpts(
            self._repo_root, _target_slug(target), max_hits=10
        )
        sibling_skills = _list_sibling_skills(abs_target, self._repo_root)
        cli_commands = _grep_cli_commands(self._repo_root, target)

        return _SkillScan(
            target_path=target,
            is_service=is_service,
            is_core_infra=is_core_infra,
            existing_skill=existing_skill,
            existing_readme=existing_readme,
            existing_design=existing_design,
            src_files=src_files,
            src_docstrings=src_docstrings,
            plan_mentions=plan_mentions,
            sibling_skills=sibling_skills,
            cli_commands=cli_commands,
        )

    def _build_prompt(
        self, scan: _SkillScan, *, prior_draft: str = "", review_feedback: str = "",
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
            existing_skill=scan.existing_skill or "(SKILL.md 不存在)",
            existing_readme=scan.existing_readme or "(README.md 不存在)",
            existing_design=scan.existing_design or "(DESIGN.md 不存在)",
            src_files=_format_list(scan.src_files),
            src_docstrings=_format_docstrings(scan.src_docstrings),
            plan_excerpts=_format_plan_hits(scan.plan_mentions),
            sibling_skills=_format_list(scan.sibling_skills),
            cli_commands=scan.cli_commands or "(未发现 cli/commands/ 内对应命令)",
            spec_text=spec_text,
            golden_text=golden_text,
            refine_block=refine_block,
        )
        return system, user


def _list_sibling_skills(abs_target: Path, repo_root: Path) -> list[str]:
    parent = abs_target.parent
    out: list[str] = []
    try:
        for sib in sorted(parent.iterdir()):
            if not sib.is_dir() or sib == abs_target:
                continue
            r = sib / "SKILL.md"
            if r.exists():
                out.append(r.relative_to(repo_root).as_posix())
    except OSError:
        pass
    return out[:10]


def _grep_cli_commands(repo_root: Path, target: str) -> str:
    """grep cli/commands/ 找跟 target 相关的命令名"""
    cli_dir = repo_root / "src" / "omnicompany" / "cli" / "commands"
    if not cli_dir.is_dir():
        return ""
    target_slug = _target_slug(target).lower()
    hits: list[str] = []
    try:
        for f in sorted(cli_dir.glob("*.py")):
            if target_slug in f.stem.lower():
                hits.append(f"- {f.relative_to(repo_root).as_posix()}")
    except OSError:
        pass
    return "\n".join(hits[:5])


_SYSTEM_PROMPT = """\
你是 omnicompany 自我叙事三件套的 SKILL.md 生成器.

## SKILL.md 必备结构 (self_narrative_three_files.md §六 模板)

```markdown
---
name: <module-name>
description: <一句话, 让 AI 决定何时调起>
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=<origin> domain=<domain> ts=<...> type=doc status=active belongs_to_service=<service> -->
<!-- [OMNI] summary="..." -->
<!-- [OMNI] tags=skill,<scope> -->
<!-- [OMNI] material_id="material:..." -->

# <模块名> · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

## 适用范围
**用我**: ...
**不用我**: ...

## 前置条件
- ...

## 操作步骤

### 场景 A · ...
```bash
omni run ...
```

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| ... | ... | ... |

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| ... | ... | ... |

## 想了解更多
- 设计目的 → [README.md](README.md)
- 内部架构 → [DESIGN.md](DESIGN.md)
- ...
```

## 硬规则

1. **YAML frontmatter**: 第一行 `---`, 含 name/description/user-invocable=false/disable-model-invocation=false
2. **OmniMark 头**: frontmatter 后紧跟 `<!-- [OMNI] ... -->` (含 belongs_to_service)
3. **6 节齐全**: 字面精确 (`## 适用范围` / `## 前置条件` / `## 操作步骤` / `## 入口清单` / `## 故障排查` / `## 想了解更多`)
4. **顶部跳转 quote**: 标题下用 `> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).`
5. **场景化**: 操作步骤按场景分 (### 场景 A / B / C), 每场景含可执行命令 + 验证方式
6. **入口清单表**: 列所有 CLI / Python 入口
7. **故障排查表**: 现象 / 原因 / 怎么修 三列
8. **不写设计目的**: 那是 README 的事
9. **不写内部架构**: 那是 DESIGN 的事
10. **相对路径基准**: SKILL 所在目录是基准. 计算 `..` 退层数:
    - `packages/services/<group>/<service>/` → repo_root: **6 个 `..`**
    - `packages/services/<service>/` (无 group) → repo_root: **5 个 `..`**
    - `packages/domains/<area>/<pkg>/` → repo_root: **5 个 `..`**
    - 例: 从 `services/_authoring/docauthor/SKILL.md` 引 `docs/standards/`: `[X](../../../../../../docs/standards/...)` 6 个
    - 算错就是死链, Reviewer 会抓出来 → critical

## 输出格式

返回 JSON:
```json
{
  "skill_content": "<完整 Markdown 文本, 从 '---' 开头, 含 frontmatter + OmniMark 头 + 6 节>",
  "notes": "<Worker 自报: 哪里不确定; 依据什么做的选择>"
}
```
"""


_USER_PROMPT_TEMPLATE = """\
## 目标

- 路径: `{target}`
- 类型: {target_type}

## 现有 SKILL.md (若存在)

```
{existing_skill}
```

## 现有 README.md (作为目的语境参考)

```
{existing_readme}
```

## 现有 DESIGN.md (作为架构参考)

```
{existing_design}
```

## src/ 下本包文件清单

{src_files}

## 关键 .py 顶部 docstring

{src_docstrings}

## docs/plans/ 提到此目标的 plan 节选

{plan_excerpts}

## 兄弟 SKILL.md (cross-reference 参考)

{sibling_skills}

## cli/commands/ 内相关命令文件 (操作步骤可基于此写)

{cli_commands}

## 规范权威 (self_narrative_three_files.md 节选)

```
{spec_text}
```

## 金标范例 (tech_debt/registry SKILL · 严格按模板, 参考不照抄)

```
{golden_text}
```

{refine_block}

## 任务

综合以上, 生成 `{target}/SKILL.md` 的合规内容.

frontmatter: name=<service 名> · description=<一句话> · user-invocable=false · disable-model-invocation=false.
OmniMark 头: ts=2026-05-04T18:05:00Z · origin=ai-ide · domain 取 target 去 `src/omnicompany/` 前缀 · belongs_to_service=<service 名>.

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
