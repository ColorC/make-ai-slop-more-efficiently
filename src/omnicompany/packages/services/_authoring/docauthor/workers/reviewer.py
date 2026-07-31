# [OMNI] origin=claude-code domain=services/docauthor/workers ts=2026-04-25T00:00:00Z type=router
# [OMNI] material_id="material:authoring.docauthor.doc_reviewer.worker.py"
"""DocReviewerWorker — 他评 · 结构硬校 + 引用 grep + LLM 质量判.

独立审判 (feedback_forced_self_review_split_to_external): Author 产出不自审,
由本 Worker 他评.

**不打分** (用户 2026-04-25 硬指示: 分数没有统一尺度, 语义信号要保留完整).
输出: passed (critical==0) + issues 全量 (severity/field/message/evidence/fix_hint).
passed 决策: 任何 severity=critical 即 passed=False, 触发 refine.

输入:  docauthor.review-request
       {draft_content, target_type: "manifest"|"design", target_path, scan_evidence?}
输出:  docauthor.review-verdict
       {passed: bool, issues: list[{severity, field, message, evidence?, fix_hint}], counts, llm_notes}

**评审维度** (与金标 NOTES.md 的评审维度对齐):
  结构 (硬):   OmniMark 头 · 七节/三 kind · status 合法 · severity 词汇合法
  引用 (硬):   参考资料链接 grep 存在性
  内容 (LLM):  非占位 / 非 TBD / 决策真有理由 / 升级路径具体
  业务语义 (LLM): 与扫描证据一致 · 不编造

## 严重度语义 (非分数加权 · 仅类别标签)

- `critical`: 结构性违规 / 编造不存在的引用 / 缺核心节. 触发 refine
- `major`:    命名/引用错 / 违反 plan 语义 / 缺升级路径. 建议 refine, 不强制
- `minor`:    措辞/格式瑕疵. 不触发 refine
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker, call_llm_json
from omnicompany.protocol.anchor import Verdict, VerdictKind

from .manifest_author import (
    _FORBIDDEN_PATH_MARKERS,
    _default_repo_root,
)


# ═══════════════════════════════════════════════════════════════════
# 严重度语义 (仅类别标签 · 不做加权求和)
# ═══════════════════════════════════════════════════════════════════

_VALID_SEVERITY_SET = {"critical", "major", "minor"}

_VALID_STATUS = {"skeleton", "design", "active", "deprecated"}
_VALID_SEVERITY = {"info", "warn", "high", "medium", "low", "HIGH", "MEDIUM", "LOW"}

_REQUIRED_DESIGN_SECTIONS = (
    "## 状态",
    "## 核心目的",
    "## 核心接口",
    "## 架构决策",
    "## 数据流",
    "## 已知局限",
    "## 参考资料",
)
_INFRA_EXTRA_SECTION = "## 接收意愿"
_INFRA_PREFIXES = (
    "src/omnicompany/runtime/",
    "src/omnicompany/protocol/",
    "src/omnicompany/core/",
    "src/omnicompany/bus/",
    "src/omnicompany/primitives/",
    "src/omnicompany/tools/",
    "src/omnicompany/tracing/",
    "src/omnicompany/cli",
)
_REQUIRED_MANIFEST_KINDS = ("data_layout", "aging_policy", "size_limits")


# ═══════════════════════════════════════════════════════════════════
# Worker
# ═══════════════════════════════════════════════════════════════════

class DocReviewerWorker(Worker):
    DESCRIPTION = (
        "他评 Author 产出. 结构硬校 (OmniMark 头 / 七节 / 三 kind / status 合法 / severity 合法) + "
        "引用 grep (参考链接真实存在) + LLM 内容质量判 (非占位 / 决策有理由 / 升级路径具体 / 与扫描证据一致). "
        "输出 passed (critical==0) + 完整 issues + 客观 evidence; **不打分** (无统一尺度). "
        "有 critical 即触发 refine."
    )
    #: bus 驱动: 直接订阅四种 draft Material (OR 模式 · 任一到即激活).
    #: 2026-04-25: 放弃 review-request 中间 Material, Reviewer 直接读 Author 产物.
    #: 2026-05-04: 扩到 readme/skill 两 kind (自我叙事三件套).
    FORMAT_IN = [
        "docauthor.manifest-draft",
        "docauthor.design-draft",
        "docauthor.readme-draft",
        "docauthor.skill-draft",
    ]
    FORMAT_IN_MODE = "or"
    FORMAT_OUT = "docauthor.review-verdict"

    def __init__(self, *, repo_root: Path | None = None, web_bus: Any = None) -> None:
        self._repo_root = (repo_root or _default_repo_root()).resolve()
        self._web_bus = web_bus

    # ─────────────────────────────────────────────────────────────

    def run(self, input_data: dict[str, Any]) -> Verdict:
        # bus 驱动: input_data 里只会有一个 key (manifest-draft 或 design-draft · OR 模式)
        # 向后兼容: 也支持旧的 review-request 字段结构 (harness 同步模式调用)
        if "docauthor.manifest-draft" in input_data:
            target_type = "manifest"
            payload = input_data["docauthor.manifest-draft"]
            draft_content = (payload.get("manifest_content") or "").strip()
            draft_target_path = (payload.get("manifest_path") or "").strip()
            # manifest_path 形如 'src/omni.../service/.omni/manifest.yaml' → 剥 .omni/manifest.yaml
            if draft_target_path.endswith("/.omni/manifest.yaml"):
                target_path = draft_target_path[: -len("/.omni/manifest.yaml")]
            else:
                target_path = draft_target_path
            scan_evidence = payload.get("scan_evidence") or {}
            carry = _extract_carry_metadata(payload)
        elif "docauthor.design-draft" in input_data:
            target_type = "design"
            payload = input_data["docauthor.design-draft"]
            draft_content = (payload.get("design_content") or "").strip()
            draft_target_path = (payload.get("design_path") or "").strip()
            if draft_target_path.endswith("/DESIGN.md"):
                target_path = draft_target_path[: -len("/DESIGN.md")]
            else:
                target_path = draft_target_path
            scan_evidence = payload.get("scan_evidence") or {}
            carry = _extract_carry_metadata(payload)
        elif "docauthor.readme-draft" in input_data:
            target_type = "readme"
            payload = input_data["docauthor.readme-draft"]
            draft_content = (payload.get("readme_content") or "").strip()
            draft_target_path = (payload.get("readme_path") or "").strip()
            if draft_target_path.endswith("/README.md"):
                target_path = draft_target_path[: -len("/README.md")]
            else:
                target_path = draft_target_path
            scan_evidence = payload.get("scan_evidence") or {}
            carry = _extract_carry_metadata(payload)
        elif "docauthor.skill-draft" in input_data:
            target_type = "skill"
            payload = input_data["docauthor.skill-draft"]
            draft_content = (payload.get("skill_content") or "").strip()
            draft_target_path = (payload.get("skill_path") or "").strip()
            if draft_target_path.endswith("/SKILL.md"):
                target_path = draft_target_path[: -len("/SKILL.md")]
            else:
                target_path = draft_target_path
            scan_evidence = payload.get("scan_evidence") or {}
            carry = _extract_carry_metadata(payload)
        else:
            # legacy review-request path (harness 同步调用)
            req = input_data.get("docauthor.review-request") or input_data
            draft_content = (req.get("draft_content") or "").strip()
            target_type = (req.get("target_type") or "").strip().lower()
            target_path = (req.get("target_path") or "").strip()
            scan_evidence = req.get("scan_evidence") or {}
            carry = _extract_carry_metadata(req)

        if not draft_content:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="missing draft_content")
        if target_type not in {"manifest", "design", "readme", "skill"}:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"cannot infer target_type from input_data keys={list(input_data.keys())}")

        try:
            self._assert_path_allowed(target_path)
        except ValueError as e:
            return Verdict(kind=VerdictKind.FAIL, diagnosis=str(e))

        # 1. 结构硬校
        issues: list[dict] = []
        if target_type == "manifest":
            issues.extend(_check_manifest_structure(draft_content))
        elif target_type == "design":
            issues.extend(_check_design_structure(draft_content, target_path))
        elif target_type == "readme":
            issues.extend(_check_readme_structure(draft_content))
        elif target_type == "skill":
            issues.extend(_check_skill_structure(draft_content))

        # 2. 引用 grep 真实性
        issues.extend(self._check_references(draft_content, target_path))

        # 3. 与 scan_evidence 对齐 (硬: manifest 的 subdir 必须出现在 scan 或 plan 里)
        if target_type == "manifest":
            issues.extend(_check_manifest_evidence_alignment(draft_content, scan_evidence))

        # 4. LLM 内容质量判
        llm_issues, llm_notes = self._llm_review(
            draft_content=draft_content,
            target_type=target_type,
            target_path=target_path,
            scan_evidence=scan_evidence,
        )
        issues.extend(llm_issues)

        # 5. 判定 (binary · 按 critical 是否存在)
        counts = {
            "critical": sum(1 for i in issues if i.get("severity") == "critical"),
            "major":    sum(1 for i in issues if i.get("severity") == "major"),
            "minor":    sum(1 for i in issues if i.get("severity") == "minor"),
        }
        passed = counts["critical"] == 0

        output = {
            "passed": passed,
            "issues": issues,            # 全量保留 · 含 severity/field/message/evidence/fix_hint
            "counts": counts,            # 仅计数, 不作分数
            "llm_notes": llm_notes,      # Reviewer LLM 的总体语义描述 (非一句话好坏判)
            "target_type": target_type,
            "target_path": target_path,
            # 携带 draft 本体供下游 Relauncher / FinalLander
            "draft_content": draft_content,
            "scan_evidence": scan_evidence,
            # 元数据传播 (refine 循环必需)
            **carry,
        }
        return Verdict(kind=VerdictKind.PASS, output=output)

    # ─────────────────────────────────────────────────────────────

    def _assert_path_allowed(self, target: str) -> None:
        norm = (target or "").replace("\\", "/")
        for marker in _FORBIDDEN_PATH_MARKERS:
            if marker in norm:
                raise ValueError(f"target contains forbidden marker '{marker}'")

    # ─────────────────────────────────────────────────────────────
    # 引用 grep 真实性
    # ─────────────────────────────────────────────────────────────

    _LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

    def _check_references(self, draft: str, target_path: str) -> list[dict]:
        issues: list[dict] = []
        target_dir = self._repo_root / target_path
        if not target_dir.exists():
            target_dir = self._repo_root  # 退化: draft 里的相对路径按 repo_root 解析

        for m in self._LINK_RE.finditer(draft):
            raw = m.group(1).strip()
            if not raw or raw.startswith(("http://", "https://", "#", "mailto:")):
                continue
            # 去 anchor / querystring
            href = raw.split("#", 1)[0].split("?", 1)[0]
            if not href:
                continue
            # draft 里的链接一般是相对 draft 目录 (即 target_path/DESIGN.md 所在目录)
            candidates = [
                (target_dir / href).resolve() if target_dir.exists() else None,
                (self._repo_root / href).resolve(),
            ]
            if not any(c and c.exists() for c in candidates if c):
                checked_paths = [str(c.relative_to(self._repo_root)) if c and c.is_relative_to(self._repo_root)
                                 else str(c) for c in candidates if c]
                issues.append({
                    "severity": "major",
                    "field": "references",
                    "message": f"死链: [{raw}]",
                    "evidence": f"tried paths: {checked_paths} — none exist on disk",
                    "fix_hint": "参考资料链接需指向真实存在的文件; 若是目标实现未落地, 挪至 '已知局限' 或加 (TBD) 标注",
                })
        return issues

    # ─────────────────────────────────────────────────────────────
    # LLM 内容质量
    # ─────────────────────────────────────────────────────────────

    def _llm_review(
        self, *, draft_content: str, target_type: str,
        target_path: str, scan_evidence: dict,
    ) -> tuple[list[dict], str]:
        evidence_summary = _summarize_evidence(scan_evidence)
        system = _REVIEWER_SYSTEM_PROMPT.replace("{target_type}", target_type)
        user = _REVIEWER_USER_TEMPLATE.format(
            target_path=target_path or "(unknown)",
            target_type=target_type,
            draft=draft_content,
            evidence=evidence_summary,
        )

        result = call_llm_json(
            system=system, user=user,
            web_bus=self._web_bus,
            caller="docauthor.reviewer",
            role="runtime_main",
            max_tokens=6000,
        )
        if "_parse_error" in result:
            return (
                [{"severity": "minor", "field": "reviewer.llm",
                  "message": f"reviewer LLM JSON parse failed: {result['_parse_error']}",
                  "fix_hint": "(非 draft 问题, 重跑即可)"}],
                "(LLM parse failed)",
            )

        issues_raw = result.get("issues") or []
        issues: list[dict] = []
        for it in issues_raw:
            if not isinstance(it, dict):
                continue
            sev = (it.get("severity") or "minor").lower()
            if sev not in _VALID_SEVERITY_SET:
                sev = "minor"
            issues.append({
                "severity": sev,
                "field": str(it.get("field") or "content"),
                "message": str(it.get("message") or ""),
                "evidence": str(it.get("evidence") or ""),   # LLM 客观引用 (draft/scan 原文片段)
                "fix_hint": str(it.get("fix_hint") or ""),
            })
        return issues, str(result.get("overall_note") or "")


# ═══════════════════════════════════════════════════════════════════
# 结构硬校
# ═══════════════════════════════════════════════════════════════════

def _check_manifest_structure(draft: str) -> list[dict]:
    issues: list[dict] = []
    if not draft.lstrip().startswith("# [OMNI]"):
        issues.append({
            "severity": "critical", "field": "header",
            "message": "OmniMark 头缺失 (应以 `# [OMNI] origin=... domain=... ts=...` 开头)",
            "evidence": f"first 80 chars: {draft[:80]!r}",
            "fix_hint": "在文件首行添加合规 OmniMark 头",
        })
    for kind in _REQUIRED_MANIFEST_KINDS:
        if f"kind: {kind}" not in draft:
            issues.append({
                "severity": "critical", "field": f"kind.{kind}",
                "message": f"缺 kind: {kind} document",
                "evidence": f"grep 'kind: {kind}' → 0 hits in draft ({len(draft)} chars)",
                "fix_hint": f"追加 --- 分隔的 `kind: {kind}` YAML document",
            })
    # severity 词汇
    for m in re.finditer(r"severity:\s*([A-Za-z]+)", draft):
        sev = m.group(1).strip()
        if sev not in _VALID_SEVERITY:
            # 取匹配行作 evidence
            start = draft.rfind("\n", 0, m.start()) + 1
            end = draft.find("\n", m.end())
            if end < 0:
                end = len(draft)
            issues.append({
                "severity": "major", "field": "severity",
                "message": f"severity 词汇非法: {sev!r}",
                "evidence": draft[start:end].strip(),
                "fix_hint": f"改为 {sorted(_VALID_SEVERITY)} 之一",
            })
    return issues


def _check_design_structure(draft: str, target_path: str) -> list[dict]:
    issues: list[dict] = []
    if not draft.lstrip().startswith("<!-- [OMNI]"):
        issues.append({
            "severity": "critical", "field": "header",
            "message": "OmniMark HTML 注释头缺失 (应以 `<!-- [OMNI] ... -->` 开头)",
            "evidence": f"first 80 chars: {draft[:80]!r}",
            "fix_hint": "在文件首行添加 `<!-- [OMNI] origin=... domain=... ts=... type=doc status=... -->`",
        })

    # status
    m = re.search(r"status=([a-zA-Z]+)", draft[:500])
    if m:
        if m.group(1) not in _VALID_STATUS:
            issues.append({
                "severity": "major", "field": "status",
                "message": f"status 非法: {m.group(1)!r}",
                "evidence": f"head-match: {m.group(0)!r}",
                "fix_hint": f"改为 {sorted(_VALID_STATUS)} 之一",
            })
    else:
        issues.append({
            "severity": "major", "field": "status",
            "message": "OmniMark 头里缺 status= 字段",
            "evidence": f"header region (first 200 chars): {draft[:200]!r}",
            "fix_hint": "加 status=skeleton|design|active|deprecated",
        })

    # 七节
    for sec in _REQUIRED_DESIGN_SECTIONS:
        if sec not in draft:
            issues.append({
                "severity": "critical", "field": f"section.{sec}",
                "message": f"缺必需节: {sec}",
                "evidence": f"grep '{sec}' → 0 hits in draft",
                "fix_hint": f"添加 `{sec}` 二级标题并填实",
            })

    # 基础设施第 8 节
    norm_target = (target_path or "").replace("\\", "/")
    if any(norm_target.startswith(p) for p in _INFRA_PREFIXES):
        if _INFRA_EXTRA_SECTION not in draft:
            issues.append({
                "severity": "major", "field": f"section.{_INFRA_EXTRA_SECTION}",
                "message": f"核心基础设施模块缺 `{_INFRA_EXTRA_SECTION}` (OMNI-034g)",
                "evidence": f"target_path={target_path} matches infra prefix; grep '{_INFRA_EXTRA_SECTION}' → 0 hits",
                "fix_hint": "在末尾加 `## 接收意愿` 节, 写清 '接收/不接收/边界信号'",
            })

    # TBD / 占位
    for pat in (r"\bTBD\b", r"\bTODO\b", r"待补", r"待填", r"占位"):
        m = re.search(pat, draft)
        if m:
            start = max(0, m.start() - 40)
            end = min(len(draft), m.end() + 40)
            issues.append({
                "severity": "minor", "field": "content.placeholder",
                "message": f"存在占位词: {pat}",
                "evidence": f"...{draft[start:end]}...",
                "fix_hint": "把占位替换为具体内容, 或把该节挪到 '已知局限' 并给升级路径",
            })
            break

    return issues


_REQUIRED_README_SECTIONS = (
    "## 这是什么",
    "## 解决什么",
    "## 设计目的与最终目标",
    "## 规划",
    "## 构成",
    "## 想了解更多",
)

_REQUIRED_SKILL_SECTIONS = (
    "## 适用范围",
    "## 前置条件",
    "## 操作步骤",
    "## 入口清单",
    "## 故障排查",
    "## 想了解更多",
)


def _check_readme_structure(draft: str) -> list[dict]:
    """README.md 结构硬校 (self_narrative_three_files.md §四 模板)."""
    issues: list[dict] = []
    if not draft.lstrip().startswith("<!-- [OMNI]"):
        issues.append({
            "severity": "critical", "field": "header",
            "message": "OmniMark HTML 注释头缺失 (应以 `<!-- [OMNI] ... -->` 开头)",
            "evidence": f"first 80 chars: {draft[:80]!r}",
            "fix_hint": "在文件首行添加 OmniMark 头, 含 belongs_to_service 字段",
        })
    if "belongs_to_service=" not in draft[:1500]:
        issues.append({
            "severity": "major", "field": "header.belongs_to_service",
            "message": "OmniMark 头里缺 belongs_to_service 字段",
            "evidence": f"header region (first 500 chars): {draft[:500]!r}",
            "fix_hint": "加 belongs_to_service=<service 名>",
        })
    for sec in _REQUIRED_README_SECTIONS:
        if sec not in draft:
            issues.append({
                "severity": "critical", "field": f"section.{sec}",
                "message": f"缺必需节: {sec}",
                "evidence": f"grep '{sec}' → 0 hits in draft",
                "fix_hint": f"添加 `{sec}` 二级标题并填实",
            })
    return issues


def _check_skill_structure(draft: str) -> list[dict]:
    """SKILL.md 结构硬校 (self_narrative_three_files.md §六 模板)."""
    issues: list[dict] = []
    stripped = draft.lstrip()
    if not stripped.startswith("---"):
        issues.append({
            "severity": "critical", "field": "frontmatter",
            "message": "YAML frontmatter 缺失 (应以 `---` 开头)",
            "evidence": f"first 80 chars: {draft[:80]!r}",
            "fix_hint": "在文件首行添加 YAML frontmatter 含 name/description/user-invocable=false",
        })
    if "<!-- [OMNI]" not in draft[:2500]:
        issues.append({
            "severity": "critical", "field": "header",
            "message": "OmniMark 头缺失 (frontmatter 后应紧跟 `<!-- [OMNI] ... -->`)",
            "evidence": f"first 500 chars: {draft[:500]!r}",
            "fix_hint": "frontmatter 后加 OmniMark HTML 注释头, 含 belongs_to_service",
        })
    if "belongs_to_service=" not in draft[:2500]:
        issues.append({
            "severity": "major", "field": "header.belongs_to_service",
            "message": "OmniMark 头里缺 belongs_to_service 字段",
            "evidence": f"header region (first 500 chars): {draft[:500]!r}",
            "fix_hint": "加 belongs_to_service=<service 名>",
        })
    for sec in _REQUIRED_SKILL_SECTIONS:
        if sec not in draft:
            issues.append({
                "severity": "critical", "field": f"section.{sec}",
                "message": f"缺必需节: {sec}",
                "evidence": f"grep '{sec}' → 0 hits in draft",
                "fix_hint": f"添加 `{sec}` 二级标题并填实",
            })
    return issues


def _check_manifest_evidence_alignment(draft: str, evidence: dict) -> list[dict]:
    """硬校: manifest 里声明的 allowed_subdirs 必须出现在扫描证据或 plan_mentions 里."""
    issues: list[dict] = []
    if not evidence:
        return issues

    subdirs = _extract_manifest_subdirs(draft)
    scan_dirs: set[str] = set()
    for entries in (evidence.get("data_dir_entries") or {}).values():
        for e in entries:
            if e.startswith("d "):
                scan_dirs.add(e[2:].strip())

    # plan_mentions 只给了路径; 若 Worker 扫出了 excerpts, excerpts_count > 0 就说明有语义依据 (软)
    has_plan_semantic = (evidence.get("plan_excerpts_count") or 0) > 0

    for sd in subdirs:
        if sd in scan_dirs:
            continue  # 扫描里有, 合规
        if has_plan_semantic:
            issues.append({
                "severity": "minor", "field": f"allowed_subdirs.{sd}",
                "message": f"subdir `{sd}` 未出现在 data 扫描, 需来自 plan 语义约定",
                "evidence": f"scan_evidence.data_dir_entries keys={sorted(scan_dirs)} 不含 '{sd}'; plan_excerpts_count={evidence.get('plan_excerpts_count', 0)}",
                "fix_hint": "确认 plan_excerpts 确实提到此 subdir; 否则删除",
            })
        else:
            issues.append({
                "severity": "major", "field": f"allowed_subdirs.{sd}",
                "message": f"subdir `{sd}` 既不在 data 扫描也无 plan 语义依据 — 可能是胡编",
                "evidence": f"scan_evidence.data_dir_entries keys={sorted(scan_dirs)} 不含 '{sd}'; plan_excerpts_count=0",
                "fix_hint": "要么证明依据 (扫描/plan 引用) 要么删除此 subdir",
            })
    return issues


_SUBDIR_LINE_RE = re.compile(r"^\s{2}([A-Za-z_][A-Za-z0-9_\-]*)\s*:", re.MULTILINE)


def _extract_manifest_subdirs(draft: str) -> list[str]:
    """粗取 data_layout document 里 allowed_subdirs 的 key 集."""
    lines = draft.splitlines()
    out: list[str] = []
    in_layout = False
    in_subdirs = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("kind: data_layout"):
            in_layout = True
            continue
        if stripped.startswith("kind:") and in_layout:
            break
        if in_layout and stripped.startswith("allowed_subdirs"):
            in_subdirs = True
            # inline {} case
            if "{}" in stripped:
                in_subdirs = False
            continue
        if in_subdirs:
            # subdir key 必须是"两空格开头 + 名字 + 冒号"
            if not line.startswith("  "):
                in_subdirs = False
                continue
            m = re.match(r"^\s{2}([A-Za-z_][A-Za-z0-9_\-]*)\s*:", line)
            if m:
                out.append(m.group(1))
    return out


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

_CARRY_KEYS = (
    "target_service_path",      # manifest 用
    "target_package_path",      # design 用
    "iter",
    "max_refine_iters",
    "notes_hint",
    "upgrade_from_skeleton",
)


def _extract_carry_metadata(payload: dict) -> dict:
    """取 payload 里需要透传给下游 (RefineConductor / FinalLander) 的元数据."""
    return {k: payload[k] for k in _CARRY_KEYS if k in payload}


def _summarize_evidence(evidence: dict) -> str:
    if not evidence:
        return "(no evidence provided)"
    lines = []
    for k, v in evidence.items():
        if isinstance(v, (list, dict)) and v:
            lines.append(f"- {k}: {v}")
        elif isinstance(v, bool):
            lines.append(f"- {k}: {v}")
        elif v:
            lines.append(f"- {k}: {v}")
    return "\n".join(lines) if lines else "(empty evidence dict)"


# ═══════════════════════════════════════════════════════════════════
# Prompt
# ═══════════════════════════════════════════════════════════════════

_REVIEWER_SYSTEM_PROMPT = """\
你是 omnicompany 分布式文档的**独立 Reviewer** (不是作者的自审).

任务: 审一份 Author Worker 产出的 draft ({target_type}), 判质量并给具体 issue.

**不给分** · 保留完整语义信号. 判断是 binary: 有 critical 即触发 refine, 否则通过.

## 审查维度

### 非占位
- 每节/每字段有具体内容, 不是 "待补 / TBD / TODO / 占位"
- 架构决策**必须**有"理由"
- 已知局限**必须**有"升级路径"

### 与扫描证据一致
- draft 里声明的 subdir / 接口 / 模块关系必须能从 evidence 验证
- 编造 (evidence 里没有的文件 / subdir / 类名) 标 critical

### 业务语义合理
- 若 plan_excerpts 里提到特定 subdir 命名 (如 voxelcraft 的 samples/scratch), 作者用了自己发明的名字 → major
- plan 语义优先于 DESIGN 倒推

### 引用真实
- 参考资料的 [链接] 必须指向真实路径 (结构检查已 grep 过; 你额外判"引用该文件是否恰当")

### target_type 特有维度

**target_type=readme** (按 self_narrative_three_files.md §四):
- "构成"段必须**指针式不复制** — 子模块描述 ≤ 1 句, 不展开. 复制下层认知 → major
- 一句话定位 (quote 块) ≤ 30 字, 强动词开头. 超 30 字 → minor; 没强动词 → minor
- "设计目的与最终目标"段不假装一锤定音 — 远景应有"当下能认知" 这种诚实修饰. 写得过分笃定 → minor
- 不能写架构细节 (那是 DESIGN.md) / 不能写操作步骤 (那是 SKILL.md). 写了 → major

**target_type=skill** (按 §六):
- frontmatter 必含 name/description/user-invocable=false/disable-model-invocation. 缺 → critical (结构硬校已抓)
- "操作步骤" 必含可执行命令 (例 `omni run <team>` 或 `from ... import ...`). 全无具体命令 → major
- "入口清单"表里的命令必须真存在 (跟 cli/commands/<service>.py 对得上). 编造命令 → critical
- "故障排查"表至少 3 项, 每项含"现象 / 原因 / 修法" 三列. 缺 → minor
- 不能写设计目的 (那是 README) / 不能写内部架构 (那是 DESIGN). 写了 → major

## 严重度 (仅类别标签 · 不加权求和)

- `critical`: 结构违规 / 编造不存在信息 / 缺核心节 · **触发 refine**
- `major`: 命名/引用错; 违反 plan 语义; 缺升级路径
- `minor`: 措辞瑕疵; 格式小问题

## 客观 evidence 铁律

每个 issue 必须有 `evidence` 字段 · 是 draft 或 scan_evidence 中的**原文片段**或**确定性引用**, 不是你的判断.

例:
- Good evidence: `"draft 第 12 行: 'build_artifact: ...' 引用 plan 没提及"`
- Bad evidence: `"这里编造了"`

evidence 是让人/下一个 Worker 能自己核验, 不靠 Reviewer 权威.

## 输出格式

返回 JSON:
```json
{
  "issues": [
    {
      "severity": "critical|major|minor",
      "field": "<具体位置 · 如 'architecture_decisions.D2' 或 'allowed_subdirs.xxx'>",
      "message": "<具体错误>",
      "evidence": "<draft 或 scan 原文引用 · 可核验>",
      "fix_hint": "<具体怎么改>"
    }
  ],
  "overall_note": "<总体语义描述 · 不是好坏判词 · 例: 'draft 大部分对齐扫描, 仅 D3 处 bus 抽象与 __init__.py 导出冲突'>"
}
```

issues 若无, 返回空数组 []. **不瞎编凑数** — 真无 issue 就是空.
"""


_REVIEWER_USER_TEMPLATE = """\
## 审查目标

- target_path: `{target_path}`
- target_type: {target_type}

## Draft (Author 产出)

```
{draft}
```

## Author 的扫描证据 (判 draft 是否忠实于 evidence)

{evidence}

按 system_prompt 输出 JSON.
"""
