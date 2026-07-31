# [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-04-18T00:00:00Z
# [OMNI] material_id="material:diagnosis.semantic_auditor.legacy_router_archive.python"
"""SemanticAuditor · HARD Routers。

Phase B1 (确定性)：
  ArtifactSelectorRouter  把输入路径/扫描源 → Artifact 清单（带 kind）
  StandardMatcherRouter   每个 Artifact 匹配适用 standard id 列表
  ExcerptRetrieverRouter  按 excerpt_strategy 取标准摘录

Phase B2 (LLM + 落盘)：
  LLMAuditRouter          async HARD 外壳，循环 excerpts 调度 AuditAgent 单审
  FindingWriterRouter     验证 Finding 字段 → append REGISTRY.md §语义合规待审

约定：
  - 所有 Router 继承 omnifactory.runtime.routing.router.Router
  - HARD Router run() 同步；LLMAuditRouter run() 异步（await AuditAgent）
  - 返回 Verdict(kind=PASS, output={...}) / FAIL 当输入不合规
  - 事件流：HARD Router 不自己 publish bus（PipelineRunner 负责）；
    LLMAuditRouter 内部 AuditAgent 的 Format 自动进 bus
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.runtime.routing.router import Router

from .standards_loader import (
    load_standards_index,
    infer_kind,
    match_standards,
    retrieve_excerpt,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# ArtifactSelectorRouter — 路径/扫描源 → Artifact 清单
# ════════════════════════════════════════════════════════════════

class ArtifactSelectorRouter(Router):
    """把输入（path 列表 / git-diff / 全扫）转成 Artifact 清单。

    输入形态（择一）：
      - {"paths": ["src/.../foo.py", ...], "project_root": "..."}
      - {"source": "git-diff", "project_root": "..."}       # 读 git 变更
      - {"source": "full-scan", "project_root": "..."}      # 全扫 src/ + docs/

    每个 Artifact 打上 kind（router / design_md / format / ...），kind 由
    standards-index.yaml.kind_inference 推断。无法推断 kind 的文件也保留（kind=None），
    下游 StandardMatcherRouter 会按 path_match 单独判定。
    """

    INPUT_KEYS = ["project_root"]
    DESCRIPTION = (
        "收集待审 artifact：接受 paths 列表 / git-diff / full-scan 三种入口，"
        "按 kind_inference 打 kind 标签，输出 list[Artifact]"
    )
    FORMAT_IN = "semantic_auditor.artifact-request"
    FORMAT_OUT = "semantic_auditor.artifact-set"

    def __init__(self, project_root: str | Path | None = None):
        self._default_root = Path(project_root) if project_root else None

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "input_data 必须是 dict"},
            )

        root = Path(
            input_data.get("project_root")
            or (str(self._default_root) if self._default_root else ".")
        )

        try:
            index = load_standards_index(root)
        except (FileNotFoundError, ValueError) as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"加载 standards-index 失败: {e}"},
            )

        paths: list[str] = []
        if "paths" in input_data and isinstance(input_data["paths"], list):
            paths = [str(p).replace("\\", "/") for p in input_data["paths"]]
        else:
            source = str(input_data.get("source", ""))
            if source == "git-diff":
                paths = self._collect_git_diff(root)
            elif source == "full-scan":
                paths = self._collect_full_scan(root)
            else:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={
                        "reason": "缺少 paths 或 source；source 必须是 git-diff / full-scan",
                    },
                )

        artifacts: list[dict[str, Any]] = []
        for p in paths:
            kind = infer_kind(p, index)
            artifacts.append({"path": p, "kind": kind})

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "project_root": str(root),
                "artifacts": artifacts,
                "artifact_count": len(artifacts),
            },
        )

    def _collect_git_diff(self, root: Path) -> list[str]:
        """读 git status --porcelain 的变更文件。"""
        import subprocess
        try:
            out = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=str(root), text=True, stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []
        paths: list[str] = []
        for line in out.splitlines():
            if len(line) < 4:
                continue
            rel = line[3:].strip()
            if " -> " in rel:
                rel = rel.split(" -> ", 1)[1]
            paths.append(rel.replace("\\", "/"))
        return paths

    def _collect_full_scan(self, root: Path) -> list[str]:
        """扫 src/ 下 .py + docs/ 下 .md + 就近 DESIGN.md / knowledge/ .md"""
        results: list[str] = []
        for rel_dir in ("src", "docs"):
            d = root / rel_dir
            if not d.exists():
                continue
            for p in d.rglob("*"):
                if not p.is_file():
                    continue
                if "__pycache__" in p.parts:
                    continue
                if p.suffix not in (".py", ".md", ".yaml"):
                    continue
                rel = str(p.relative_to(root)).replace("\\", "/")
                results.append(rel)
        return results


# ════════════════════════════════════════════════════════════════
# StandardMatcherRouter — 每个 Artifact 匹配适用 standard id 列表
# ════════════════════════════════════════════════════════════════

class StandardMatcherRouter(Router):
    """为每个 Artifact 匹配适用的 standard id 列表。

    输入：上游 ArtifactSelectorRouter 的 output
    输出：audit_targets = list[{artifact: {...}, applicable_standards: [standard_id, ...]}]
    """

    INPUT_KEYS = ["artifacts"]
    DESCRIPTION = (
        "读 standards-index.yaml，为每个 artifact 按 kind + path_match "
        "匹配适用 standard id 列表"
    )
    FORMAT_IN = "semantic_auditor.artifact-set"
    FORMAT_OUT = "semantic_auditor.audit-target-set"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict) or "artifacts" not in input_data:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "input_data 需含 artifacts 字段"},
            )

        root = Path(input_data.get("project_root", "."))
        try:
            index = load_standards_index(root)
        except (FileNotFoundError, ValueError) as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"加载 standards-index 失败: {e}"},
            )

        artifacts = input_data["artifacts"]
        if not isinstance(artifacts, list):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "artifacts 必须是 list"},
            )

        targets: list[dict[str, Any]] = []
        unmatched = 0
        for a in artifacts:
            if not isinstance(a, dict):
                continue
            path = a.get("path", "")
            kind = a.get("kind")
            ids = match_standards(kind, path, index)
            if not ids:
                unmatched += 1
                continue
            targets.append({
                "artifact": a,
                "applicable_standards": ids,
            })

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "project_root": str(root),
                "audit_targets": targets,
                "target_count": len(targets),
                "unmatched_artifacts": unmatched,
            },
        )


# ════════════════════════════════════════════════════════════════
# ExcerptRetrieverRouter — 按 excerpt_strategy 取标准摘录
# ════════════════════════════════════════════════════════════════

class ExcerptRetrieverRouter(Router):
    """为每个 audit_target × standard_id 取标准内容摘录。

    输入：上游 StandardMatcherRouter 的 output
    输出：excerpts = list[{target: {...}, standard_id: "...", excerpt_text: "...", excerpt_len: N}]

    excerpt_strategy=full → 整份
    excerpt_strategy=section → 按 key_sections 切块
    """

    INPUT_KEYS = ["audit_targets"]
    DESCRIPTION = (
        "按 excerpt_strategy 取每条 standard 的摘录，"
        "产出 (target, standard_id, excerpt_text) 三元组清单"
    )
    FORMAT_IN = "semantic_auditor.audit-target-set"
    FORMAT_OUT = "semantic_auditor.audit-excerpt-set"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict) or "audit_targets" not in input_data:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "input_data 需含 audit_targets 字段"},
            )

        root = Path(input_data.get("project_root", "."))
        try:
            index = load_standards_index(root)
        except (FileNotFoundError, ValueError) as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"加载 standards-index 失败: {e}"},
            )

        targets = input_data["audit_targets"]
        if not isinstance(targets, list):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "audit_targets 必须是 list"},
            )

        excerpts: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []

        for t in targets:
            if not isinstance(t, dict):
                continue
            artifact = t.get("artifact", {})
            for sid in t.get("applicable_standards", []):
                try:
                    text = retrieve_excerpt(sid, index)
                    excerpts.append({
                        "target": artifact,
                        "standard_id": sid,
                        "excerpt_text": text,
                        "excerpt_len": len(text),
                    })
                except (ValueError, FileNotFoundError) as e:
                    failed.append({
                        "target_path": artifact.get("path", ""),
                        "standard_id": sid,
                        "reason": str(e),
                    })

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "project_root": str(root),
                "excerpts": excerpts,
                "excerpt_count": len(excerpts),
                "failed_retrievals": failed,
            },
        )


# ════════════════════════════════════════════════════════════════
# LLMAuditRouter — async HARD 外壳，循环 excerpts 调度 AuditAgent 单审
# ════════════════════════════════════════════════════════════════

# 单次审的 user task 模板（复杂字段走 task，避开 NODE_PROMPT 的 str.format）
_TASK_TEMPLATE = """审计以下 artifact 是否符合标准 {standard_id}。

artifact 路径: {artifact_path}
artifact 类型: {artifact_kind}

标准 {standard_id} 的摘录如下：
========= 标准摘录开始 =========
{excerpt_text}
========= 标准摘录结束 =========

请先用 read_file 读 artifact 全文，按需 grep/glob 取证，再对照标准判断违规。
最终通过 finish 工具提交 JSON: {{"findings": [...]}}（见 system prompt 协议）。
"""


class LLMAuditRouter(Router):
    """async HARD 外壳：对每条 excerpt 启动一次 AuditAgent，合并 Finding。

    设计取舍：
      - Pipeline 当前不支持 fan-out，所以本 Router 作为"薄循环调度"外壳
      - 单审逻辑在 AuditAgent（AgentNodeLoop 子类），保证"能 AgentNodeLoop 就 AgentNodeLoop"
      - 所有 LLM / tool 调用通过 AuditAgent 自动进 bus，审计优越
    """

    INPUT_KEYS = ["excerpts"]
    DESCRIPTION = (
        "循环调度 AuditAgent 对每条 excerpt 单审，"
        "合并 Finding 列表作为下游 FindingWriter 的输入"
    )
    FORMAT_IN = "semantic_auditor.audit-excerpt-set"
    FORMAT_OUT = "semantic_auditor.finding-set"

    def __init__(
        self,
        *,
        bus: Any = None,
        model: str | None = None,
        agent: Any = None,
    ):
        """bus 必须传（AuditAgent 的硬要求）。
        agent 允许注入（测试时可传 mock AuditAgent 实例，避免真调 LLM）。
        """
        self._bus = bus
        self._model = model
        self._injected_agent = agent

    def _build_agent(self) -> Any:
        if self._injected_agent is not None:
            return self._injected_agent
        from .audit_agent import AuditAgent
        return AuditAgent(bus=self._bus, model=self._model)

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict) or "excerpts" not in input_data:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "input_data 需含 excerpts 字段"},
            )

        excerpts = input_data["excerpts"]
        if not isinstance(excerpts, list):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "excerpts 必须是 list"},
            )

        try:
            agent = self._build_agent()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"AuditAgent 构造失败: {e}"},
            )

        findings: list[dict[str, Any]] = []
        parse_errors: list[dict[str, str]] = []
        audit_count = 0

        for ex in excerpts:
            if not isinstance(ex, dict):
                continue
            target = ex.get("target") or {}
            standard_id = ex.get("standard_id", "")
            excerpt_text = ex.get("excerpt_text", "")
            if not (standard_id and excerpt_text):
                continue

            task = _TASK_TEMPLATE.format(
                standard_id=standard_id,
                artifact_path=target.get("path", ""),
                artifact_kind=target.get("kind") or "unknown",
                excerpt_text=excerpt_text,
            )

            try:
                verdict = await agent.run({
                    "task": task,
                    "trace_id": f"audit-{standard_id}-{target.get('path', '')}",
                })
            except Exception as e:
                parse_errors.append({
                    "target_path": target.get("path", ""),
                    "standard_id": standard_id,
                    "reason": f"agent.run 异常: {e}",
                })
                continue

            audit_count += 1

            if not verdict or not isinstance(verdict.output, dict):
                parse_errors.append({
                    "target_path": target.get("path", ""),
                    "standard_id": standard_id,
                    "reason": "AuditAgent 未返回 dict output",
                })
                continue

            final_text = (verdict.output.get("text") or "").strip()
            if not final_text:
                continue  # 无违规 + 空 finish 也会落空，忽略

            try:
                data = json.loads(final_text)
            except json.JSONDecodeError as e:
                parse_errors.append({
                    "target_path": target.get("path", ""),
                    "standard_id": standard_id,
                    "reason": f"Finding JSON 解析失败: {e}",
                })
                continue

            batch = data.get("findings") if isinstance(data, dict) else None
            if not isinstance(batch, list):
                parse_errors.append({
                    "target_path": target.get("path", ""),
                    "standard_id": standard_id,
                    "reason": "findings 字段不是 list",
                })
                continue

            # 回填标准/路径（LLM 可能漏填），后续 FindingWriter 再做严格验证
            for f in batch:
                if not isinstance(f, dict):
                    continue
                f.setdefault("standard_id", standard_id)
                f.setdefault("target_path", target.get("path", ""))
                findings.append(f)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "project_root": input_data.get("project_root", "."),
                "findings": findings,
                "finding_count": len(findings),
                "audit_count": audit_count,
                "parse_errors": parse_errors,
            },
        )


# ════════════════════════════════════════════════════════════════
# FindingWriterRouter — 验证 Finding + append 到 REGISTRY.md / ARCH-CHANGES.jsonl
# ════════════════════════════════════════════════════════════════

_REGISTRY_RELPATH = "docs/tech_debt/REGISTRY.md"
_ARCH_RELPATH = "docs/ARCH-CHANGES.jsonl"
_SECTION_HEADER = "## §语义合规待审"
_TABLE_HEADER_PREFIX = "| ID |"
_SA_ID_PATTERN = re.compile(r"^SA-(\d+)$")

_CONF_HUMAN_REVIEW = 0.7  # <0.7 → status=needs_human_review
_REQUIRED_FINDING_FIELDS = (
    "standard_id", "target_path", "description",
    "confidence", "recommended_action",
)


def _validate_finding(f: dict, known_standard_ids: set[str]) -> str | None:
    """返回错误原因 str（未通过）或 None（通过）。"""
    for k in _REQUIRED_FINDING_FIELDS:
        v = f.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            return f"缺字段 {k}"
    sid = f["standard_id"]
    if sid not in known_standard_ids:
        return f"未知 standard_id={sid}"
    try:
        c = float(f["confidence"])
    except (TypeError, ValueError):
        return "confidence 非数字"
    if not 0.0 <= c <= 1.0:
        return "confidence 超出 [0,1]"
    return None


def _next_sa_id(existing_ids: list[str]) -> str:
    max_n = 0
    for s in existing_ids:
        m = _SA_ID_PATTERN.match(s.strip())
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return f"SA-{max_n + 1:03d}"


def _find_semantic_section(lines: list[str]) -> tuple[int, int] | None:
    """定位 §语义合规待审 表格范围，返回 (table_header_idx, table_end_idx)。"""
    section_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith(_SECTION_HEADER):
            section_start = i
            break
    if section_start is None:
        return None

    table_header = None
    for i in range(section_start + 1, len(lines)):
        line = lines[i].strip()
        if line.startswith("## ") or line == "---":
            break
        if line.startswith(_TABLE_HEADER_PREFIX):
            table_header = i
            break
    if table_header is None:
        return None

    data_start = table_header + 2
    table_end = data_start
    for i in range(data_start, len(lines)):
        line = lines[i].rstrip("\n").strip()
        if line.startswith("|"):
            table_end = i + 1
        else:
            break
    return (table_header, table_end)


def _parse_sa_row(line: str) -> dict | None:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) != 7:
        return None
    return {
        "id": cells[0],
        "standard_id": cells[1],
        "target_path": cells[2],
        "description": cells[3],
        "confidence": cells[4],
        "disposition": cells[5],
        "status": cells[6],
    }


def _format_sa_row(row: dict) -> str:
    def esc(s: str) -> str:
        return str(s).replace("|", "\\|").replace("\n", " ").strip()

    return (
        f"| {esc(row['id'])} | {esc(row['standard_id'])} | "
        f"{esc(row['target_path'])} | {esc(row['description'])} | "
        f"{esc(row['confidence'])} | {esc(row['disposition'])} | "
        f"{esc(row['status'])} |"
    )


class FindingWriterRouter(Router):
    """验证 Finding 字段 → append 到 REGISTRY.md §语义合规待审 + ARCH-CHANGES.jsonl。

    去重键：(standard_id, target_path)。已存在 open 条目不重复写（仅 log）。
    confidence < 0.7 → status=needs_human_review，不进入主 open 流。
    任何异常只产 FAIL Verdict，不抛（保证 pipeline 不崩）。
    """

    INPUT_KEYS = ["findings"]
    DESCRIPTION = (
        "验证 Finding 字段，append 到 REGISTRY.md §语义合规待审，"
        "同步写 ARCH-CHANGES.jsonl event_type=finding-generated"
    )
    FORMAT_IN = "semantic_auditor.finding-set"
    FORMAT_OUT = "semantic_auditor.finding-written"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict) or "findings" not in input_data:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "input_data 需含 findings 字段"},
            )

        findings = input_data["findings"]
        if not isinstance(findings, list):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "findings 必须是 list"},
            )

        root = Path(input_data.get("project_root", "."))
        try:
            index = load_standards_index(root)
            known_ids = {s.id for s in index.standards}
        except (FileNotFoundError, ValueError) as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"加载 standards-index 失败: {e}"},
            )

        registry_path = root / _REGISTRY_RELPATH
        arch_path = root / _ARCH_RELPATH

        try:
            content = registry_path.read_text(encoding="utf-8")
        except OSError as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"读 REGISTRY.md 失败: {e}"},
            )

        lines = content.splitlines()
        span = _find_semantic_section(lines)
        if span is None:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "REGISTRY.md 未找到 §语义合规待审 表格"},
            )
        header_idx, end_idx = span
        data_start = header_idx + 2

        existing_rows: list[dict] = []
        for i in range(data_start, end_idx):
            r = _parse_sa_row(lines[i])
            if r is not None:
                existing_rows.append(r)

        dedup_index: dict[tuple[str, str], int] = {}
        for idx, r in enumerate(existing_rows):
            if r["status"] in ("open", "needs_human_review"):
                dedup_index[(r["standard_id"], r["target_path"])] = idx

        existing_ids = [r["id"] for r in existing_rows]

        added = 0
        rejected: list[dict[str, str]] = []
        deduped = 0
        new_rows_for_arch: list[dict] = []

        for f in findings:
            if not isinstance(f, dict):
                rejected.append({"reason": "finding 非 dict"})
                continue
            err = _validate_finding(f, known_ids)
            if err is not None:
                rejected.append({
                    "standard_id": str(f.get("standard_id", "")),
                    "target_path": str(f.get("target_path", "")),
                    "reason": err,
                })
                continue

            key = (f["standard_id"], f["target_path"])
            if key in dedup_index:
                deduped += 1
                continue

            conf = float(f["confidence"])
            status = "needs_human_review" if conf < _CONF_HUMAN_REVIEW else "open"
            new_id = _next_sa_id(existing_ids + [r["id"] for r in new_rows_for_arch])

            line_hint = f.get("line_hint")
            desc_with_line = f["description"]
            if line_hint is not None:
                desc_with_line = f"{desc_with_line} (L{line_hint})"
            disposition = f.get("recommended_action", "")

            row = {
                "id": new_id,
                "standard_id": f["standard_id"],
                "target_path": f["target_path"],
                "description": desc_with_line,
                "confidence": f"{conf:.2f}",
                "disposition": disposition,
                "status": status,
            }
            existing_rows.append(row)
            new_rows_for_arch.append(row)
            dedup_index[key] = len(existing_rows) - 1
            added += 1

        if added == 0:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    "added": 0,
                    "deduped": deduped,
                    "rejected": rejected,
                    "arch_events": 0,
                },
            )

        new_table_lines = [_format_sa_row(r) for r in existing_rows]
        new_lines = lines[:data_start] + new_table_lines + lines[end_idx:]
        new_content = "\n".join(new_lines)
        if content.endswith("\n") and not new_content.endswith("\n"):
            new_content += "\n"

        try:
            registry_path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"写 REGISTRY.md 失败: {e}"},
            )

        arch_events = self._append_arch_events(new_rows_for_arch, arch_path)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "added": added,
                "deduped": deduped,
                "rejected": rejected,
                "arch_events": arch_events,
                "new_ids": [r["id"] for r in new_rows_for_arch],
            },
        )

    @staticmethod
    def _append_arch_events(new_rows: list[dict], arch_path: Path) -> int:
        """Phase C4：改用 tech_debt.events.append_event 统一 schema。

        arch_path 参数保留是因为旧接口兼容；内部反推 root = arch_path.parents[1]
        （假设 arch_path = root/docs/ARCH-CHANGES.jsonl）。
        """
        if not new_rows:
            return 0

        from omnifactory.packages.services._diagnosis.tech_debt import append_event

        # arch_path = root/docs/ARCH-CHANGES.jsonl → parents[1] = root
        try:
            root = arch_path.parents[1]
            arch_relpath = arch_path.relative_to(root).as_posix()
        except (IndexError, ValueError):
            logger.warning("FindingWriter: 反推 root 失败，arch_path=%s", arch_path)
            return 0

        count = 0
        for row in new_rows:
            ev = append_event(
                root,
                event_type="finding-generated",
                initiator="semantic_auditor",
                drawer="services/semantic_auditor",
                related_pipeline="semantic_auditor.baseline",
                change=(
                    f"{row['id']} {row['standard_id']} {row['status']} "
                    f"{row['target_path']}"
                ),
                arch_relpath=arch_relpath,
            )
            if ev is not None:
                count += 1
        return count
