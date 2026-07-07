# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.registration_plan_builder.dry_run.py"
"""RegistrarWorker — Phase 10 · HARD (2026-04-23 · dry_run).

Worker 协议:
  FORMAT_IN  = team_builder.material.code_package
  FORMAT_OUT = team_builder.material.registration_plan

**职责**: HARD · 接 code_package, 产出 dry_run 注册计划:
  - files_to_write 清单 (abs path + size + sha256 preview)
  - pipeline_entry_code (要追加到 core/pipelines.py 的代码段)
  - dry_run=True · human_review_required=True
  - **V3 MVP 不真落盘** (保护 src/ 不被 agent 污染; 未来接 HumanBus 审批后再真写)

**不调 LLM** · 规则驱动:
  - 从 code_package.team_name 推 package path
  - 验 target_package_path 合规 (`src/omnicompany/packages/services/<team_name>/`)
  - 生成 PipelineEntry 模板代码

HARD 铁律:
  - 不执行任何写盘操作 (真 MVP)
  - 不调任何 ServiceBus write 方法
  - output 是 registration_plan material (sink · 终点)
"""
from __future__ import annotations

import hashlib
import py_compile
import tempfile
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


def _py_compile_files(files: dict[str, str]) -> list[dict]:
    """骨架接管 · 对每个 .py 内容跑 py_compile 检测语法错 (feedback_100pct_required_goes_to_skeleton).

    返回 compile errors list (空 = 全过).
    不修改原 files, 只 report.
    """
    errors = []
    with tempfile.TemporaryDirectory(prefix="team_builder_compile_") as tmpdir:
        tmp = Path(tmpdir)
        # 落盘所有 .py 到临时目录 (保持相对路径 · 允许 workers/ 子目录)
        written = []
        for rel, content in files.items():
            if not isinstance(content, str) or not rel.endswith(".py"):
                continue
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            written.append((rel, p))
        # 逐个 py_compile
        for rel, p in written:
            try:
                py_compile.compile(str(p), doraise=True)
            except py_compile.PyCompileError as e:
                errors.append({
                    "file": rel,
                    "error": str(e.msg if hasattr(e, "msg") else e)[:500],
                })
            except SyntaxError as e:
                errors.append({
                    "file": rel,
                    "error": f"SyntaxError line {e.lineno}: {e.msg}",
                })
    return errors


def _preview_hash(content: str) -> str:
    if not isinstance(content, str):
        return "n/a"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def _synthesize_testmap_yaml(team_name: str) -> str:
    """骨架接管(与 py_compile / DESIGN.md 章节规范化同款): code_package 若未含
    testmap.yaml, 确定性合成一份最小骨架(app=team_name, features 空), 与
    `templates/team/骨架/testmap.yaml` 内容对齐(见 OMNI-100 · 2026-07-03
    完成标准接线批)。不调 LLM, 不影响既有 files_to_write 结构。
    """
    return (
        "# [OMNI] origin=team-builder domain=services/" + team_name + " ts=" + _now_iso() + " type=data\n"
        "# testmap.yaml — 功能点-测试对照表。真源跟随本软件;omnicompany 只注册指针。\n"
        "# 契约: src/omnicompany/packages/services/_governance/testmap.py; 查询: omni testmap show <app>\n"
        "# 首个登记期限: 本软件首个功能性计划完成时,features 必须至少登记该计划交付的功能点"
        "(空表过巡检但过不了计划收尾门)。\n"
        f"app: {team_name}\n"
        "doc: DESIGN.md\n"
        "gates:\n"
        "  - id: unit\n"
        "    cmd: pytest tests/ -q\n"
        "    cwd: .\n"
        "features: []\n"
    )


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_pipeline_entry_code(team_name: str, description: str) -> str:
    """生成要追加到 core/pipelines.py 的 PipelineEntry 代码段."""
    snake = team_name.replace("-", "_")
    cli_name = snake.replace("_", "-")
    return f'''    # ── {cli_name} · 由 team_builder V3 生成 (dry_run · 2026-04-23) ──
    try:
        register(PipelineEntry(
            name="{cli_name}",
            description={description!r},
            domain="{snake}",
            build_team=_lazy(
                "omnicompany.packages.services.{snake}.team",
                "build_team",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services.{snake}.run",
                "build_bindings",
            ),
            default_db_dir="data/services/{snake}",
            default_max_steps=1000,
            cli_args=[
                CliArg(name="text", help="自然语言需求"),
            ],
        ))
    except Exception as e:
        logger.debug("skip {cli_name}: %s", e)
'''


class RegistrarWorker(Worker):
    """Phase 10 · HARD · 产 registration_plan (dry_run · V3 MVP 不真落盘)."""

    DESCRIPTION = (
        "Phase 10 · HARD · 接 code_package 产 registration_plan (dry_run=True · human_review) · "
        "列 files_to_write 清单 + PipelineEntry 代码段 · 不真落盘 (V3 MVP 保 src/ 不污染, 未来 HumanBus 审批)."
    )
    FORMAT_IN = "team_builder.material.code_package"
    FORMAT_OUT = "team_builder.material.registration_plan"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"input_data must be dict, got {type(input_data).__name__}",
            )

        # 从 input_data 平铺字段取 (上游 CodeGenerator/CodeAggregator output 平铺后)
        # V3 legacy: _from_code_generator (单体 AgentNodeLoop)
        # V3.2 sub-team: _from_code_aggregator (8 路 aggregator)
        upstream = (
            input_data.get("_from_code_aggregator")
            or input_data.get("_from_code_generator")
            or {}
        )
        if not isinstance(upstream, dict):
            upstream = {}
        team_name = input_data.get("team_name") or upstream.get("team_name")
        target_path = input_data.get("target_package_path") or upstream.get("target_package_path")
        files = input_data.get("files") or upstream.get("files")

        if not team_name or not target_path or not isinstance(files, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=(
                    f"code_package 缺字段 · team_name={bool(team_name)} "
                    f"target={bool(target_path)} files={isinstance(files, dict)}"
                ),
            )

        # 合规性校验 target_path
        expected_prefix = "src/omnicompany/packages/services/"
        if not target_path.startswith(expected_prefix):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"target_package_path 不合规 · 必须以 {expected_prefix!r} 开头 (got {target_path!r})",
            )

        # 骨架接管 · code_package 若未含 testmap.yaml, 确定性合成一份最小骨架
        # (OMNI-100 完成标准接线批 · 2026-07-03): 新 team 落盘即带功能点-测试台账占位,
        # 不调 LLM、不重构本管线, 只在 files dict 缺失时补一条。
        if not any(Path(rel).name == "testmap.yaml" for rel in files):
            files = dict(files)
            files["testmap.yaml"] = _synthesize_testmap_yaml(team_name)

        # 骨架接管 · 对所有 .py 跑 py_compile 检测语法错
        # 100% 必做: "落盘前要能 import" 是确定性约束, 不靠 LLM 自觉
        compile_errors = _py_compile_files(files)
        if compile_errors:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={
                    "team_name": team_name,
                    "target_package_path": target_path,
                    "compile_errors": compile_errors,
                    "dry_run": True,
                    "human_review_required": True,
                },
                diagnosis=(
                    f"code_package 有 {len(compile_errors)} 个文件 py_compile FAIL · "
                    f"LLM 产的代码有语法错, 无法 import. 前 3 条: "
                    f"{[e['file'] + ':' + e['error'][:60] for e in compile_errors[:3]]}"
                ),
            )

        # 构造 files_to_write 清单 (dry_run · 不真写)
        files_to_write = []
        for rel_path, content in files.items():
            if not isinstance(content, str):
                continue
            abs_path = target_path.rstrip("/") + "/" + rel_path.lstrip("/")
            files_to_write.append({
                "rel_path": rel_path,
                "abs_path": abs_path,
                "size_bytes": len(content.encode("utf-8")),
                "sha256_preview": _preview_hash(content),
            })

        # 推断 CLI description (从 team_name 或 fallback)
        description = (
            f"{team_name.replace('_', ' ').title()} · 由 team_builder 自动生成 "
            f"({len(files_to_write)} 个文件, "
            f"{sum(f['size_bytes'] for f in files_to_write)} bytes)"
        )
        pipeline_entry_code = _build_pipeline_entry_code(team_name, description)

        plan = {
            "team_name": team_name,
            "target_package_path": target_path,
            "files_to_write": files_to_write,
            "files": files,  # V3.2 · 保留 rel_path → content (deploy 直接落盘用, 免去 audit scrape)
            "pipeline_entry_code": pipeline_entry_code,
            "compile_check": {
                "status": "PASS",
                "files_checked": sum(1 for r in files if r.endswith(".py")),
            },
            "dry_run": True,
            "human_review_required": True,
            "notes": [
                "V3 MVP: **未真落盘** · 保护 src/ 不被 agent 污染",
                "已骨架校验: 每个 .py 通过 py_compile (无语法错 · 可 import)",
                "真落盘需 L1 人类审阅 files_to_write 清单 + 人工执行",
                "未来方向: 接 HumanBus 审批机制后自动执行 (方案 kind=human_blocking)",
                "pipeline_entry_code 已生成, 落盘时追加到 core/pipelines.py",
                "V3.2 (2026-04-24): plan['files'] 含完整 rel_path→content, deploy 不再需 scrape audit",
            ],
        }
        return Verdict(kind=VerdictKind.PASS, output=plan)
