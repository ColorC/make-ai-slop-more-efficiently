# [OMNI] origin=claude-code domain=omnifactory/guardian ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:guardian.service_patrol.inspector.worker.py"
"""PatrolWorker — Guardian LLM 巡查 Worker (C4 2026-04-21).

Worker 协议:
  FORMAT_IN  = guardian.patrol-request
  FORMAT_OUT = guardian.patrol-report

职责:
  Guardian 规则 (OMNI-*) 负责"仁慈但快速"的硬规则扫描 (正则/白名单/AST).
  PatrolWorker 负责**精准**的 LLM 审视 - 能看到规则看不见的问题:
    1. Clean Migration 真伪: workers/ 是独立文件架构还是 Diamond 假迁移?
       业务代码真的在 workers/ 里还是在 _archive 借壳?
    2. DESIGN.md 对齐度: status=active 但代码是 skeleton? 七节内容和实际代码
       匹配吗? 是否还有 TBD 占位?
    3. 目录污染: 超出 Guardian archmap 白名单定义的那些"不在规则但气味不对"的
       散落 (如 service 目录下冒出奇怪子目录)

使用:
  > omni run guardian-patrol

产出:
  data/services/guardian/patrol/<YYYY-MM-DD>-<timestamp>.md

背景:
  2026-04-21 用户指出 Guardian 过去漏检了大量污染 (data/ 18+ 非法 subdir + fs_scanner
  白名单与 archmap 严重不一致). 规则 (OMNI-040/041/042) 已补上 "仁慈快速" 那一层,
  此 Worker 补上 "LLM 精准" 那一层. 两层互补.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from omnifactory.packages.services._core.omnicompany import Worker
from omnifactory.protocol.anchor import Verdict, VerdictKind

from ..rules.stage3_completeness import _has_archive_import_via_ast

logger = logging.getLogger(__name__)


_PATROL_SYSTEM_PROMPT = """\
你是 OmniCompany 的架构巡查员。你的职责是审视给定的 service 包, 判断它的
"Clean Migration Stage 3" 完整性与 DESIGN.md 对齐度, 以及目录卫生。

对每个 service, 基于给定的以下上下文:
- DESIGN.md 原文
- workers/ 目录清单 (哪些独立 Worker 文件)
- workers/__init__.py 内容 (是真 re-export 还是含 Diamond class 定义)
- _archive/ 目录存在性 + 是否被 workers/ import
- formats.py / routers.py / team.py 摘要

给出诊断 (Markdown):
## <service> (状态判定)
**Stage 3 完整性**: Stage3-OK | Stage2-Diamond | Skeleton | Hybrid
  - 判据:
    - workers/__init__.py 含 `class XxxWorker(Worker, _XxxRouter)` → Stage 2 假迁移
    - workers/ 下只有 __init__.py 没独立文件 + _archive 被继承 → Stage 2
    - workers/ 下每个 Worker 独立文件 + _archive 不被 import → Stage 3 ✅
    - 没 workers/ 且 DESIGN.md skeleton → 从未开始迁移
**DESIGN.md 对齐**: aligned | drift | stale
  - 判据: status=active 但七节有 TBD 占位 → drift
    描述的 Worker 数量与 workers/ 实际文件数不符 → drift
    最新日期 < 最近改动 30 天 → stale
**目录卫生**: clean | dirty
  - 判据: 有无 service 目录下出现非标准子目录 (非 workers/, _archive/, __pycache__/)
**关键问题** (如有): [列出 top 1-3 具体问题 + 修复建议, 每条 1-2 句]

格式严格按上述 Markdown 模板, 每个 service 一个块, 其他都是杂音。不要总结, 不要重复代码。
"""


def _summarize_service(svc_dir: Path) -> dict[str, Any]:
    """为一个 service 目录收集 LLM 需要的审视上下文 (不截断内容, 仅结构描述)."""
    summary: dict[str, Any] = {
        "name": svc_dir.name,
        "has_design_md": False,
        "design_md_content": "",
        "has_workers_dir": False,
        "worker_files": [],
        "workers_init_py": "",
        "has_archive": False,
        "archive_imported_by_workers": False,
        "has_formats_py": False,
        "formats_py_size": 0,
        "has_routers_py": False,
        "routers_py_size": 0,
        "has_team_py": False,
        "team_py_size": 0,
        "unknown_subdirs": [],
    }
    # DESIGN.md
    design = svc_dir / "DESIGN.md"
    if design.exists():
        summary["has_design_md"] = True
        try:
            summary["design_md_content"] = design.read_text(encoding="utf-8")
        except Exception:
            pass
    # workers/
    workers_dir = svc_dir / "workers"
    if workers_dir.exists():
        summary["has_workers_dir"] = True
        for p in workers_dir.iterdir():
            if p.is_file() and p.suffix == ".py" and p.name not in ("__init__.py", "_shared.py"):
                summary["worker_files"].append(p.name)
        init_py = workers_dir / "__init__.py"
        if init_py.exists():
            try:
                summary["workers_init_py"] = init_py.read_text(encoding="utf-8")
            except Exception:
                pass
        # Diamond detection: does workers/ import _archive? (复用 OMNI-040 AST 解析, 避免
        # docstring/注释里的示例代码假阳性 · 同步覆盖单点 from ._archive 写法)
        for p in workers_dir.rglob("*.py"):
            try:
                txt = p.read_text(encoding="utf-8")
                if _has_archive_import_via_ast(txt):
                    summary["archive_imported_by_workers"] = True
                    break
            except Exception:
                continue
    # _archive/
    summary["has_archive"] = (svc_dir / "_archive").exists()
    # formats / routers / team
    for n in ("formats.py", "routers.py", "team.py"):
        p = svc_dir / n
        if p.exists():
            summary[f"has_{n.replace('.py', '_py')}"] = True
            try:
                summary[f"{n.replace('.py', '_py')}_size"] = len(p.read_text(encoding="utf-8").splitlines())
            except Exception:
                pass
    # 非标准子目录 (注意: .omni/ 是规范目录, 分布式文档 manifest 落此, 非污染)
    legal_subdirs = {"workers", "_archive", "_graveyard", "__pycache__", "rules", "checks",
                     "blackboard", "format", "router", "pipeline", "v1", "v2", "v3",
                     "knowledge", "routers", ".omni"}
    for p in svc_dir.iterdir():
        if p.is_dir() and p.name not in legal_subdirs:
            summary["unknown_subdirs"].append(p.name)
    return summary


class PatrolWorker(Worker):
    """Guardian LLM 巡查: Clean Migration 真伪 + DESIGN.md 对齐 + 目录卫生."""

    DESCRIPTION = (
        "Guardian LLM 巡查 Worker (2026-04-21 C4). 遍历 services/ 下每个 service, "
        "为每个 service 组装 DESIGN.md / workers/ 清单 / _archive 存在性等结构化上下文, "
        "调 qwen-3.6-plus 按 Stage 3 完整性/DESIGN.md 对齐/目录卫生三维评估, "
        "产出 Markdown 巡查报告到 data/services/guardian/patrol/<date>.md. "
        "规则 (OMNI-040/041/042) 负责仁慈快速, 本 Worker 负责 LLM 精准."
    )
    FORMAT_IN = "guardian.patrol-request"
    FORMAT_OUT = "guardian.patrol-report"
    INPUT_KEYS = ["services_root"]

    def run(self, input_data: dict[str, Any]) -> Verdict:
        # 1. 收集 services/ 下每个 service 的上下文
        services_root_str = input_data.get("services_root")
        if services_root_str:
            services_root = Path(services_root_str)
        else:
            # 默认走项目根 services/
            from omnifactory.core.config import _project_root
            services_root = _project_root() / "src" / "omnifactory" / "packages" / "services"

        if not services_root.exists():
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"services_root 不存在: {services_root}")

        service_summaries: list[dict[str, Any]] = []
        for p in sorted(services_root.iterdir()):
            if not p.is_dir() or p.name.startswith(("_", ".")) or p.name == "__pycache__":
                continue
            service_summaries.append(_summarize_service(p))

        if not service_summaries:
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"services_root 下无 service: {services_root}")

        # 2. 组装 LLM 请求 payload (不截断, 铁律 A 合规)
        user_content_parts = [
            f"以下是 {len(service_summaries)} 个 service 的结构化上下文, 请为每个 service 按约定格式输出巡查诊断。\n"
        ]
        for s in service_summaries:
            user_content_parts.append(f"\n=== service: {s['name']} ===\n")
            user_content_parts.append(f"DESIGN.md 存在: {s['has_design_md']}\n")
            if s["has_design_md"]:
                user_content_parts.append("DESIGN.md 内容:\n```\n" + s["design_md_content"] + "\n```\n")
            user_content_parts.append(f"workers/ 存在: {s['has_workers_dir']}\n")
            user_content_parts.append(f"workers/ 下独立文件: {s['worker_files']}\n")
            user_content_parts.append(f"workers/__init__.py 内容:\n```python\n{s['workers_init_py']}\n```\n")
            user_content_parts.append(f"_archive/ 存在: {s['has_archive']}\n")
            user_content_parts.append(f"workers/ 是否 import _archive: {s['archive_imported_by_workers']}\n")
            user_content_parts.append(f"formats.py: exists={s['has_formats_py']}, 行数={s['formats_py_size']}\n")
            user_content_parts.append(f"routers.py: exists={s['has_routers_py']}, 行数={s['routers_py_size']}\n")
            user_content_parts.append(f"team.py:    exists={s['has_team_py']}, 行数={s['team_py_size']}\n")
            user_content_parts.append(f"非标准子目录: {s['unknown_subdirs']}\n")
        user_content = "".join(user_content_parts)

        # 3. 调用 LLM — 走 role-based 构造, 让 ModelRegistry 统一解析
        # model → endpoint → api_key. role="runtime_main" 的 quality tier 当前
        # 即 qwen3.6-plus (铁律: 项目唯一模型). 不要硬传 model name 绕开 registry.
        try:
            from omnifactory.runtime.llm.llm import LLMClient
            client = LLMClient(role="runtime_main")
            response = client.call(
                messages=[{"role": "user", "content": user_content}],
                system=_PATROL_SYSTEM_PROMPT,
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"LLM 调用失败: {type(e).__name__}: {e}")

        report_md = "".join(
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", "text") == "text"
        )

        if not report_md:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="LLM 返回空响应")

        # 4. 落盘报告
        from omnifactory.core.config import resolve_service_data_dir
        patrol_dir = resolve_service_data_dir("guardian") / "patrol"
        patrol_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        report_path = patrol_dir / f"patrol-{ts}.md"
        header = f"# Guardian LLM Patrol Report\n\n生成时间: {datetime.now().isoformat()}\n巡查 services: {len(service_summaries)} 个\n\n---\n\n"
        report_path.write_text(header + report_md, encoding="utf-8")
        # I-20 data-provenance: 写 sidecar 记录合法写入者身份
        try:
            from omnifactory.core.omnimark import write_data_sidecar
            write_data_sidecar(
                report_path,
                written_by=f"{self.__class__.__module__}.{self.__class__.__name__}",
                source_path=__file__,
                ttl_days=90,
            )
        except Exception as e:
            logger.debug("sidecar 写入失败 (非致命): %s", e)
        logger.info("Patrol report written: %s", report_path)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "patrolled_services": len(service_summaries),
                "report_path": str(report_path),
                "report_md": report_md,
            },
            diagnosis=f"巡查了 {len(service_summaries)} 个 service, 报告: {report_path.name}",
        )
