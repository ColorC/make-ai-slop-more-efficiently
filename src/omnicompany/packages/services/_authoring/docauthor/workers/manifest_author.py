# [OMNI] origin=claude-code domain=services/docauthor/workers ts=2026-04-25T00:00:00Z type=router
# [OMNI] material_id="material:authoring.docauthor.manifest_author.worker.py"
"""ManifestAuthorWorker — Phase A 最小可行.

单次 LLM 调用 (qwen-3.6-plus via call_llm_json) 生成 `.omni/manifest.yaml` draft.

输入:  docauthor.manifest-request   {target_service_path, notes_hint?}
输出:  docauthor.manifest-draft     {manifest_path, manifest_content, scan_evidence, notes}

反泄漏铁律 (DESIGN.md D3):
  Worker 只从 _SPEC_SOURCES / _GOLDEN_EXAMPLES 读取规范 + 公开范例.
  禁止读 docs/plans/[2026-04-25]AUTO-DOCAUTHOR-WORKER/gold_samples/
  也禁止读 docs/plans/ 其他 plan 的 gold_samples/
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker, call_llm_json
from omnicompany.protocol.anchor import Verdict, VerdictKind


# ═══════════════════════════════════════════════════════════════════
# 反泄漏白名单 — 仅这些路径被允许注入 prompt
# ═══════════════════════════════════════════════════════════════════

_SPEC_SOURCES = (
    "docs/standards/_global/distributed-docs.md",  # 规范权威
)

_GOLDEN_EXAMPLES = (
    "src/omnicompany/packages/services/_core/guardian/.omni/manifest.yaml",  # 合法公开范例
)

# 禁止路径 (显式拒绝, 即使有人传进来 target_service_path)
_FORBIDDEN_PATH_MARKERS = (
    "gold_samples",
    "AUTO-DOCAUTHOR-WORKER",  # 本 plan 目录下任何东西都不得扫
)

# 扫描上限 — 防 prompt 膨胀 (铁律 A 精神 · 不是"预防性截断", 是"硬边界报错")
_MAX_SCAN_ENTRIES = 200       # data_dir 下最多列 200 条目
_MAX_DESIGN_CHARS = 20_000    # DESIGN.md 太大时拒绝 (而非截断) · 0 表不限
_MAX_EXISTING_MANIFEST = 10_000  # 已有 manifest.yaml 同理


# ═══════════════════════════════════════════════════════════════════
# 扫描结果数据结构
# ═══════════════════════════════════════════════════════════════════

@dataclass
class _PlanHit:
    path: str          # repo-relative plan .md
    excerpt: str       # 命中段前后各约 6 行的上下文 (用于 LLM 读具体语义)


@dataclass
class _ScanResult:
    target_path: str                  # repo-relative (如 'src/omnicompany/packages/services/foo')
    is_service: bool                  # packages/services/ 下 vs domains/ 下
    is_core_infra: bool               # bus / core / protocol / runtime / cli 之一
    design_md: str | None             # DESIGN.md 全文 (若存在且 < 上限)
    existing_manifest: str | None     # 已有 manifest.yaml 全文 (若存在)
    data_dir_candidates: list[str]    # Worker 猜测的 data 目录 (repo-relative)
    data_dir_entries: dict[str, list[str]]  # 每个候选目录下的子目录/文件 (最多 _MAX_SCAN_ENTRIES)
    top_level_data_files: list[str]   # 核心基础设施场景: data/ 根下 *.db/*.jsonl 等 (LLM 判哪些属本包)
    plan_mentions: list[_PlanHit]     # docs/plans/ 下提到目标名的 plan (含节选)
    src_md_definitions: list[str]     # src/ 下本包内的 *.md 定义文件 (formats/ routers/ 下)


# ═══════════════════════════════════════════════════════════════════
# Worker
# ═══════════════════════════════════════════════════════════════════

class ManifestAuthorWorker(Worker):
    """Phase A 最小可行的 manifest 作者 Worker."""

    DESCRIPTION = (
        "扫描指定 service/package 结构 + 读 DESIGN + 读现有 manifest + grep plan history, "
        "调 qwen-3.6-plus LLM 生成三 kind (data_layout / aging_policy / size_limits) 的 "
        ".omni/manifest.yaml draft. 反泄漏铁律: 不扫 gold_samples."
    )
    FORMAT_IN = "docauthor.manifest-request"
    FORMAT_OUT = "docauthor.manifest-draft"

    def __init__(self, *, repo_root: Path | None = None, web_bus: Any = None) -> None:
        self._repo_root = (repo_root or _default_repo_root()).resolve()
        self._web_bus = web_bus

    # ─────────────────────────────────────────────────────────────
    # Router 接口
    # ─────────────────────────────────────────────────────────────

    def run(self, input_data: dict[str, Any]) -> Verdict:
        req = input_data.get(self.FORMAT_IN) or input_data
        target = req.get("target_service_path", "").strip()
        notes_hint = (req.get("notes_hint") or "").strip()
        prior_draft = (req.get("prior_draft") or "").strip()
        review_feedback = (req.get("review_feedback") or "").strip()
        iter_num = int(req.get("iter") or 0)
        max_refine_iters = int(req.get("max_refine_iters") or 1)

        if not target:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="missing target_service_path in manifest-request",
            )

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
            scan, notes_hint,
            prior_draft=prior_draft, review_feedback=review_feedback,
        )

        result = call_llm_json(
            system=prompt_system,
            user=prompt_user,
            web_bus=self._web_bus,
            caller="docauthor.manifest_author",
            role="runtime_main",     # qwen-3.6-plus
            max_tokens=12000,
        )

        if "_parse_error" in result:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"LLM JSON parse failed: {result.get('_parse_error')}",
                details={"_raw": result.get("_raw", "")[:2000]},
            )

        manifest_content = (result.get("manifest_content") or "").strip()
        if not manifest_content:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="LLM returned empty manifest_content",
                details={"llm_result": result},
            )

        # 硬校验: manifest 必须含三 kind (骨架管约束, feedback_100pct_required_goes_to_skeleton)
        missing_kinds = [k for k in ("data_layout", "aging_policy", "size_limits")
                         if f"kind: {k}" not in manifest_content]
        if missing_kinds:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"manifest missing kinds: {missing_kinds}",
                details={"manifest_content": manifest_content},
            )

        output = {
            "manifest_path": f"{target.rstrip('/')}/.omni/manifest.yaml",
            "manifest_content": manifest_content,
            # 透传 refine 元数据 (下游 Reviewer/Conductor 需要)
            "target_service_path": target,
            "iter": iter_num,
            "max_refine_iters": max_refine_iters,
            "notes_hint": notes_hint,
            "scan_evidence": {
                "target_path": scan.target_path,
                "is_service": scan.is_service,
                "is_core_infra": scan.is_core_infra,
                "has_design_md": scan.design_md is not None,
                "has_existing_manifest": scan.existing_manifest is not None,
                "data_dir_candidates": scan.data_dir_candidates,
                "data_dir_entries": scan.data_dir_entries,
                "top_level_data_files": scan.top_level_data_files,
                "plan_mentions": [h.path for h in scan.plan_mentions],
                "plan_excerpts_count": sum(1 for h in scan.plan_mentions if h.excerpt),
                "src_md_definitions": scan.src_md_definitions,
            },
            "notes": result.get("notes") or "",
        }
        return Verdict(kind=VerdictKind.PASS, output=output)

    # ─────────────────────────────────────────────────────────────
    # 反泄漏 gate
    # ─────────────────────────────────────────────────────────────

    def _assert_path_allowed(self, target: str) -> None:
        norm = target.replace("\\", "/")
        for marker in _FORBIDDEN_PATH_MARKERS:
            if marker in norm:
                raise ValueError(
                    f"target contains forbidden marker '{marker}'. "
                    "Worker refuses to read gold_samples or this plan's internals (DESIGN.md D3)."
                )

    # ─────────────────────────────────────────────────────────────
    # 扫描
    # ─────────────────────────────────────────────────────────────

    def _scan(self, target: str) -> _ScanResult:
        target = target.replace("\\", "/").rstrip("/")
        abs_target = (self._repo_root / target).resolve()
        if not abs_target.exists() or not abs_target.is_dir():
            raise FileNotFoundError(target)

        # 安全: abs_target 必须在 repo_root 下
        try:
            abs_target.relative_to(self._repo_root)
        except ValueError:
            raise ValueError(f"target outside repo_root: {target}")

        is_service = "/packages/services/" in f"/{target}/"
        is_core_infra = _is_core_infra_target(target)
        design_md = _read_text_bounded(abs_target / "DESIGN.md", _MAX_DESIGN_CHARS)
        existing_manifest = _read_text_bounded(
            abs_target / ".omni" / "manifest.yaml", _MAX_EXISTING_MANIFEST
        )

        data_candidates = _infer_data_dir_candidates(target)
        data_entries: dict[str, list[str]] = {}
        for cand in data_candidates:
            abs_cand = self._repo_root / cand
            if abs_cand.exists() and abs_cand.is_dir():
                data_entries[cand] = _list_dir_entries(abs_cand, _MAX_SCAN_ENTRIES)

        # 回流 #2: 核心基础设施可能产 data/ 根下单文件 (如 bus → data/events.db)
        top_level_data_files = (
            _scan_top_level_data_files(self._repo_root)
            if is_core_infra else []
        )

        plan_mentions = _grep_plan_mentions_with_excerpts(
            self._repo_root, _target_slug(target), max_hits=10
        )

        # 回流 #3: src/ 下本包内的 .md 定义文件 (formats/ routers/ 下)
        src_md_definitions = _list_md_definitions(abs_target)

        return _ScanResult(
            target_path=target,
            is_service=is_service,
            is_core_infra=is_core_infra,
            design_md=design_md,
            existing_manifest=existing_manifest,
            data_dir_candidates=data_candidates,
            data_dir_entries=data_entries,
            top_level_data_files=top_level_data_files,
            plan_mentions=plan_mentions,
            src_md_definitions=src_md_definitions,
        )

    # ─────────────────────────────────────────────────────────────
    # Prompt 构造
    # ─────────────────────────────────────────────────────────────

    def _build_prompt(
        self, scan: _ScanResult, notes_hint: str, *,
        prior_draft: str = "", review_feedback: str = "",
    ) -> tuple[str, str]:
        spec_text = _load_allowed_text(self._repo_root, _SPEC_SOURCES)
        golden_text = _load_allowed_text(self._repo_root, _GOLDEN_EXAMPLES)

        if scan.is_core_infra:
            target_type_label = "core infrastructure module"
        elif scan.is_service:
            target_type_label = "service"
        else:
            target_type_label = "domain package"

        system = _SYSTEM_PROMPT
        refine_block = ""
        if prior_draft and review_feedback:
            refine_block = _REFINE_SECTION_TEMPLATE.format(
                prior_draft=prior_draft,
                review_feedback=review_feedback,
            )
        user = _USER_PROMPT_TEMPLATE.format(
            target=scan.target_path,
            target_type=target_type_label,
            design_md=scan.design_md or "(DESIGN.md 不存在 / 读不到)",
            existing_manifest=scan.existing_manifest or "(无现有 manifest)",
            data_candidates=_format_list(scan.data_dir_candidates),
            data_entries=_format_entries(scan.data_dir_entries),
            top_level_data_files=_format_list(scan.top_level_data_files)
                if scan.is_core_infra else "(非核心基础设施 · 跳过根 data 扫描)",
            plan_excerpts=_format_plan_hits(scan.plan_mentions),
            src_md_definitions=_format_list(scan.src_md_definitions),
            notes_hint=notes_hint or "(无额外提示)",
            spec_text=spec_text,
            golden_text=golden_text,
            refine_block=refine_block,
        )
        return system, user


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _default_repo_root() -> Path:
    """从本文件回推 omnicompany 仓库根 (含 src/ + docs/).

    路径: <repo>/omnicompany/src/omnicompany/packages/services/docauthor/workers/manifest_author.py
    parents[0]=workers / [1]=docauthor / [2]=services / [3]=packages /
    [4]=omnicompany(inner) / [5]=src / [6]=omnicompany(repo root, 含 src+docs+data)
    """
    return Path(__file__).resolve().parents[6]


def _read_text_bounded(path: Path, max_chars: int) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if max_chars and len(text) > max_chars:
        raise ValueError(
            f"{path} exceeds {max_chars} chars ({len(text)}), refuse to proceed "
            "(bump _MAX_DESIGN_CHARS if legitimate)"
        )
    return text


def _list_dir_entries(p: Path, limit: int) -> list[str]:
    out: list[str] = []
    try:
        for entry in sorted(p.iterdir()):
            if entry.name.startswith("__pycache__"):
                continue
            tag = "d" if entry.is_dir() else "f"
            out.append(f"{tag} {entry.name}")
            if len(out) >= limit:
                out.append(f"(truncated at {limit} entries)")
                break
    except OSError as e:
        out.append(f"(list failed: {e})")
    return out


def _infer_data_dir_candidates(target: str) -> list[str]:
    """从 target 路径推断合理的 data/ 候选目录.

    约定 (distributed-docs):
      packages/services/<svc>/          → data/services/<svc>/
      packages/domains/<dom>/           → data/domains/<dom>/
      packages/domains/<dom>/<subpkg>/  → data/domains/<dom>/<subpkg>/
      bus/ core/ protocol/ runtime/...  → data/<name>/ (core infra; 多数无 data)
    """
    t = target.replace("\\", "/").strip("/")
    parts = t.split("/")
    out: list[str] = []

    try:
        idx_services = parts.index("services")
        # packages/services/<svc>/...
        if idx_services + 1 < len(parts):
            svc = parts[idx_services + 1]
            tail = parts[idx_services + 2 :]
            base = f"data/services/{svc}"
            out.append(base + ("/" + "/".join(tail) if tail else ""))
        return out
    except ValueError:
        pass

    try:
        idx_domains = parts.index("domains")
        tail = parts[idx_domains + 1 :]
        if tail:
            out.append("data/domains/" + "/".join(tail))
        return out
    except ValueError:
        pass

    # 核心基础设施 (bus / core / protocol / runtime / cli)
    # 尝试 data/<name>/ 但存在率低
    if parts[:2] == ["src", "omnicompany"] and len(parts) >= 3:
        name = parts[2]
        if name in {"bus", "core", "protocol", "runtime", "cli", "packages"}:
            # packages 已在前面两分支覆盖, bus 可能用 data/events.db 直接
            if name != "packages":
                out.append(f"data/{name}")
    return out


def _grep_plan_mentions_with_excerpts(
    repo_root: Path, slug: str, *, max_hits: int
) -> list[_PlanHit]:
    """搜 docs/plans/ 下 *.md 提到 slug 的文件 · 附命中段前后 6 行上下文.

    回流 #1 · 让 Worker 读到具体语义 (如 "samples/ 是 Worker 凭证不可删"),
    而不只是一串路径.

    反泄漏: skip 含 _FORBIDDEN_PATH_MARKERS 的路径.
    """
    plans_dir = repo_root / "docs" / "plans"
    if not plans_dir.exists() or not slug:
        return []
    hits: list[_PlanHit] = []
    try:
        for md in plans_dir.rglob("*.md"):
            rel_str = md.relative_to(repo_root).as_posix()
            if any(m in rel_str for m in _FORBIDDEN_PATH_MARKERS):
                continue
            try:
                txt = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if slug not in txt:
                continue
            excerpt = _extract_excerpt_around(txt, slug, context_lines=6, max_chars=1200)
            hits.append(_PlanHit(path=rel_str, excerpt=excerpt))
            if len(hits) >= max_hits:
                break
    except OSError:
        pass
    return hits


def _extract_excerpt_around(
    text: str, needle: str, *, context_lines: int, max_chars: int
) -> str:
    """找到 needle 所在行, 取其前后各 context_lines 行拼接; 多次命中合并去重."""
    lines = text.splitlines()
    ranges: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        if needle in line:
            lo = max(0, i - context_lines)
            hi = min(len(lines), i + context_lines + 1)
            if ranges and lo <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], hi)
            else:
                ranges.append((lo, hi))
            if len(ranges) >= 3:
                break
    if not ranges:
        return ""
    chunks = []
    for lo, hi in ranges:
        chunks.append("\n".join(lines[lo:hi]))
    excerpt = "\n  ---\n".join(chunks)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars] + "...(truncated excerpt)"
    return excerpt


def _is_core_infra_target(target: str) -> bool:
    """bus / core / protocol / runtime / cli 之一."""
    t = target.replace("\\", "/").strip("/")
    parts = t.split("/")
    if parts[:2] == ["src", "omnicompany"] and len(parts) >= 3:
        return parts[2] in {"bus", "core", "protocol", "runtime", "cli"}
    return False


def _scan_top_level_data_files(repo_root: Path) -> list[str]:
    """回流 #2 · 列出 data/ 根下所有 *.db / *.jsonl / *.sqlite 文件.

    基础设施 (bus / core / ...) 的 data 常常不在子目录而在 data/ 根 (events.db),
    需要 LLM 决定哪个文件属本包.
    """
    data_dir = repo_root / "data"
    if not data_dir.exists() or not data_dir.is_dir():
        return []
    out: list[str] = []
    try:
        for entry in sorted(data_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() in {".db", ".jsonl", ".sqlite", ".sqlite3"}:
                try:
                    size_mb = entry.stat().st_size / (1024 * 1024)
                except OSError:
                    size_mb = 0.0
                out.append(f"data/{entry.name} ({size_mb:.1f}MB)")
    except OSError:
        pass
    return out


def _list_md_definitions(abs_target: Path) -> list[str]:
    """回流 #3 · 列 src/ 下本包内的 .md 定义文件 (formats/ routers/ 下)."""
    out: list[str] = []
    for subdir_name in ("formats", "routers"):
        sub = abs_target / subdir_name
        if not sub.exists() or not sub.is_dir():
            continue
        try:
            for md in sorted(sub.glob("*.md")):
                out.append(f"{subdir_name}/{md.name}")
        except OSError:
            pass
    return out


def _target_slug(target: str) -> str:
    """取 target 的"最特征段"作 grep 关键词.

    - services/<svc>/<tail?>     → "<svc>" (通常独一无二)
    - domains/<dom>/<subpkg>/... → "<dom>/<subpkg>" (组合防 'item' 过泛)
    - 其他 (core infra like bus) → "omnicompany/<name>" (加前缀限缩)
    """
    t = target.replace("\\", "/").strip("/")
    parts = t.split("/")
    if "services" in parts:
        i = parts.index("services")
        if i + 1 < len(parts):
            return parts[i + 1]
    if "domains" in parts:
        i = parts.index("domains")
        rest = parts[i + 1 : i + 3]   # <dom>/<subpkg> 两段组合
        return "/".join(rest) if rest else t.rsplit("/", 1)[-1]
    if parts[:2] == ["src", "omnicompany"] and len(parts) >= 3:
        return f"omnicompany/{parts[2]}"
    return t.rsplit("/", 1)[-1] if "/" in t else t


def _load_allowed_text(repo_root: Path, rel_paths: tuple[str, ...]) -> str:
    """读取白名单路径的原文拼接. 缺失文件抛错 — 骨架保证."""
    chunks: list[str] = []
    for rel in rel_paths:
        p = repo_root / rel
        if not p.exists():
            raise FileNotFoundError(f"required allowed source missing: {rel}")
        chunks.append(f"─── {rel} ───\n{p.read_text(encoding='utf-8', errors='replace')}")
    return "\n\n".join(chunks)


def _format_list(xs: list[str]) -> str:
    if not xs:
        return "(none)"
    return "\n".join(f"  - {x}" for x in xs)


def _format_entries(d: dict[str, list[str]]) -> str:
    if not d:
        return "(候选 data 目录均不存在或为空)"
    parts = []
    for k, entries in d.items():
        parts.append(f"{k}:\n" + "\n".join(f"  {e}" for e in entries))
    return "\n\n".join(parts)


def _format_plan_hits(hits: list[_PlanHit]) -> str:
    """回流 #1: 输出路径 + 节选而非仅路径."""
    if not hits:
        return "(none)"
    parts = []
    for h in hits:
        if h.excerpt:
            parts.append(f"### {h.path}\n```\n{h.excerpt}\n```")
        else:
            parts.append(f"### {h.path}  (找到 slug 但提取节选失败)")
    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# Prompt 文本
# ═══════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """\
你是 omnicompany 分布式文档的 manifest 生成器.

你的任务: 为一个 service / domain 子包 / 核心基础设施模块生成 `.omni/manifest.yaml`.
manifest 作用: 声明该 package 在 data/ 下的合法布局 + 老化策略 + 体积上限,
受 guardian 的 OMNI-049 / OMNI-050 / OMNI-051 守护.

## manifest 结构 (三个 YAML document, 必须齐全)

```yaml
# [OMNI] origin=<origin> domain=<domain> ts=<YYYY-MM-DDTHH:MM:SSZ>
# <一两行人类可读说明>

---
kind: data_layout
allowed_subdirs:
  <subdir_name>: "<description>"
  # 若本 package 不产 data/ 产物, allowed_subdirs 可为 {}
required_files: []       # 或列具体文件名
notes: "<可选>"

---
kind: aging_policy
policies:
  - path_pattern: "<glob>"
    max_age_days: <int>
    severity: <info|warn|high>

---
kind: size_limits
limits:
  - path_pattern: "<glob>"
    max_size_mb: <int>
    severity: <info|warn|high|medium|low>
```

## 硬规则

1. **严格三 kind**: 每个 document 必须以 `---` 分隔并含 `kind:` 字段之一
2. **OmniMark 头**: 第一行必须是 `# [OMNI] origin=... domain=... ts=...`
3. **不得胡编 subdir**: allowed_subdirs 里每个 key **必须**是扫描里实际存在的,
   或**plan 节选里有明确语义约定的** (如 voxelcraft 的 samples/scratch)
4. **不得编造不存在的引用**: notes/description 不能引用不在扫描结果里的文件
5. **policies / limits 可为 []**: 但三 kind 字段本身不得省略
6. **severity 词汇**: info / warn / high / medium / low 之一
7. **不编造业务字段**: manifest 只管"布局 / 老化 / 体积"

## 读 plan 节选的纪律 (回流 #1)

plan_excerpts 部分给出具体上下文 (不只路径). **优先从节选里读取 subdir 约定/命名语义**:
- 例: 节选若说 "samples/ 是 Worker 质量凭证不可删, scratch/ 是临时工作区可清", 就该用 `samples` / `scratch` 而非你自己推一组
- DESIGN 里 material 名 (如 "build_artifact") 不等于 subdir 名; subdir 名以 plan 语义 + 现有目录结构为准

## 核心基础设施 (core infra) 的单文件 data (回流 #2)

若 target 是 bus / core / runtime / protocol / cli 等基础模块:
- data 常直接在 `data/` 根 (如 `data/events.db`), 不在 `data/<name>/` 子目录
- top_level_data_files 段列出了 data/ 根下可见的 *.db / *.jsonl, **请判断哪些属本模块**
- 属本模块的文件: 在 aging_policy / size_limits 给对应 path_pattern
  (例: bus → `data/events.db` 给 size_limits max_size_mb=1024)
- 不属本模块的文件不要声明

## src/ 内 .md 定义文件的软上限 (回流 #3)

若 src_md_definitions 段非空 (formats/*.md 或 routers/*.md), 说明本包产**定义类文档**:
- 这些 .md 不在 data/ 下, 但同样有 "单文件膨胀" 风险
- 在 size_limits 里加上 `path_pattern: "<target>/formats/**/*.md"` max_size_mb: 1 (severity: info)
  防单份 Format 定义爆膨

## 合法三条件 (自检)

- **活跃依赖链**: 每项 allowed_subdirs / policy / limit 都应对应扫描证据或 plan 节选
- **声明长期价值**: 不产占位; 若真不产 data, `allowed_subdirs: {}` + notes 说清楚
- **标准协议槽位**: 三 kind 齐全, `---` 分隔正确, 字段名字面精确

## 输出格式

返回 JSON:
```json
{
  "manifest_content": "<完整 YAML 文本, 从 '# [OMNI]' 开头, 三 kind 都在>",
  "notes": "<Worker 自报: 哪里不确定; 依据什么选择>"
}
```

manifest_content 是**原文 YAML 字符串** (带 \\n 换行, 不要 markdown fence).
"""


_USER_PROMPT_TEMPLATE = """\
## 目标

- 路径: `{target}`
- 类型: {target_type}
- 人类提示: {notes_hint}

## 现有 DESIGN.md

```
{design_md}
```

## 现有 .omni/manifest.yaml (若有 · 保留其中合理人类笔记)

```
{existing_manifest}
```

## data/ 候选子目录推断

{data_candidates}

## data/ 候选子目录实际条目

{data_entries}

## 核心基础设施 · data/ 根下单文件 (回流 #2)

{top_level_data_files}

## src/ 内本包 .md 定义文件 (回流 #3)

{src_md_definitions}

## docs/plans/ 提到此目标的 plan (含节选 · 回流 #1)

{plan_excerpts}

## 规范权威 (distributed-docs.md 节选)

```
{spec_text}
```

## 合法公开范例 (guardian service manifest · 参考不照抄)

```
{golden_text}
```

{refine_block}

## 任务

综合以上, 生成 `{target}/.omni/manifest.yaml` 的合规内容.

OmniMark 头: ts=2026-04-25T00:00:00Z · origin=claude-code · domain 取 target 去 `src/omnicompany/` 前缀的路径.

严格按 system_prompt 的输出 JSON 格式返回.
"""


_REFINE_SECTION_TEMPLATE = """\
## 上一轮 draft (需修正)

```yaml
{prior_draft}
```

## Reviewer 反馈 (按此修正)

{review_feedback}

**修正规则**: 保留合理的部分, 只改 Reviewer 指出的问题; 不要整体重写, 不要增 Reviewer 没提到的 subdir/policy.
"""
