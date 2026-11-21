# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/tools ts=2026-05-06T00:25:00Z type=router status=skeleton agent=ai-ide-current
# [OMNI] summary="WriteHypothesisRouter 业务工具 V0 — HypothesisDeriverAgent 用它落一条 doctor.hypothesis.statement 实例 yaml 到假设库"
# [OMNI] why="阶段 2 后续 3: HypothesisDeriverAgent 需要把派生的假设持久化. 走业务工具不走 write_file 是为了 schema 校验跟 ID 生成统一"
# [OMNI] tags=tool,doctor,write-hypothesis,skeleton
# [OMNI] material_id="material:diagnosis.doctor.tools.write_hypothesis.skeleton.py"
"""WriteHypothesisRouter · doctor 业务工具 (V0 骨架)

HypothesisDeriverAgent 调本工具产 doctor.hypothesis.statement Material 实例.

落: yaml 文件 data/services/doctor/hypotheses/<id>.yaml

V0 行为: 只做 yaml 落盘 + 返 hypothesis id. bus 事件待 registry 接通后加.

## 待做 (V0 → V1)

[ ] **bus 事件**: 当前只 yaml 落盘. 加 SQLiteBus.publish 发 doctor.hypothesis.statement 事件
[ ] **id 冲突检测**: 当 LLM 给重复 id 时报错让重起
[ ] **registry 接通**: 当 registry 提供 hypothesis ingest API, 改成走 API
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import yaml

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    """omnicompany 项目根 (含 src/omnicompany + docs). find-up 不依赖具体层级数."""
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[6] if len(here.parents) > 6 else here.parent


_PROJECT_ROOT = _project_root()


class WriteHypothesisRouter(SingleToolRouter):
    """落一条 doctor.hypothesis.statement 实例 yaml 到 data/services/doctor/hypotheses/."""

    TOOL_NAME: ClassVar[str] = "write_hypothesis"
    DESCRIPTION: ClassVar[str] = (
        "Persist a doctor.hypothesis.statement instance — one health hypothesis derived from "
        "a standard/plan/code source. Each call writes one yaml file to "
        "data/services/doctor/hypotheses/<id>.yaml.\n"
        "用户铁律 (2026-05-05): 假设也是 material — 假设是'应满足什么 + 为什么'的自然语言句子. "
        "Required: id (e.g. 'H-2026-05-06-001'), source_kind (spec|plan|code|exemplar), "
        "source_path (path of the deriving source), source_excerpt (具体引用片段, 自然语言), "
        "statement (假设本体: '应满足什么', 自然语言), motivation (来龙去脉: '为什么这是必要项, 不满足会怎样'), "
        "applies_to (worker|material|team|agent|hook|tool|plan), evidence_query (自然语言指引: 怎么查代码确认假设是否满足).\n"
        "optional: status (active|draft|deprecated, default 'active'), tags (list of strings).\n"
        "Returns the assigned hypothesis path."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Hypothesis ID (e.g. 'H-2026-05-06-001'). Must be unique. Convention: H-<YYYY-MM-DD>-<NNN>",
            },
            "source_kind": {
                "type": "string",
                "enum": ["spec", "plan", "code", "exemplar"],
                "description": "Where this hypothesis was derived from",
            },
            "source_path": {
                "type": "string",
                "description": "Path of the deriving source (e.g. 'docs/standards/concepts/worker.md')",
            },
            "source_excerpt": {
                "type": "string",
                "description": "Concrete excerpt from the source (the sentence/paragraph that triggered this hypothesis). Natural language, ≥20 chars",
            },
            "statement": {
                "type": "string",
                "description": "The hypothesis itself: '应满足什么'. Natural-language sentence, ≥30 chars",
            },
            "motivation": {
                "type": "string",
                "description": "来龙去脉: '为什么这是必要项, 不满足会怎样'. Natural-language paragraph, ≥50 chars",
            },
            "applies_to": {
                "type": "string",
                "enum": ["worker", "material", "team", "agent", "hook", "tool", "plan"],
                "description": "Which kind of entity this hypothesis applies to",
            },
            "evidence_query": {
                "type": "string",
                "description": "Natural-language instruction: how to check if the hypothesis is satisfied (e.g., 'look for FORMAT_OUT class attribute assignment')",
            },
            "status": {
                "type": "string",
                "enum": ["active", "draft", "deprecated"],
                "default": "active",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
        },
        "required": [
            "id", "source_kind", "source_path", "source_excerpt",
            "statement", "motivation", "applies_to", "evidence_query",
        ],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = (
        "meta_io.fs.create_file",
    )

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        # ── 长度 gate ──
        for f, min_len in (("source_excerpt", 20), ("statement", 30), ("motivation", 50), ("evidence_query", 20)):
            val = (args.get(f) or "").strip()
            if len(val) < min_len:
                raise ToolExecutionError(
                    f"{f} too short (< {min_len} chars). 这是自然语言字段, "
                    f"写完整中文句子说明{'引用片段' if f=='source_excerpt' else '应满足什么' if f=='statement' else '为什么是必要项' if f=='motivation' else '怎么查证据'}."
                )

        hyp_id = args["id"].strip()
        if not hyp_id.startswith("H-"):
            raise ToolExecutionError(
                f"id {hyp_id!r} should follow convention 'H-<YYYY-MM-DD>-<NNN>' (e.g., 'H-2026-05-06-001')"
            )

        agent_id = getattr(ctx, "agent_id", None) or ""

        # commit_hash 自动填 (self_audit B-3 修)
        try:
            from omnicompany.packages.services._core.registry import git_head_short_hash
            commit_hash = git_head_short_hash(_PROJECT_ROOT)
        except Exception:
            commit_hash = ""

        hypothesis = {
            "id": hyp_id,
            "source_kind": args["source_kind"],
            "source_path": args["source_path"],
            "source_excerpt": args["source_excerpt"].strip(),
            "statement": args["statement"].strip(),
            "motivation": args["motivation"].strip(),
            "applies_to": args["applies_to"],
            "evidence_query": args["evidence_query"].strip(),
            "status": args.get("status", "active"),
            "tags": args.get("tags") or [],
            "commit_hash": commit_hash,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "created_by": agent_id or "ai-ide",
        }

        out_dir = _PROJECT_ROOT / "data" / "services" / "doctor" / "hypotheses"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{hyp_id}.yaml"
            if out_path.exists():
                raise ToolExecutionError(
                    f"hypothesis {hyp_id!r} already exists at {out_path}. "
                    f"Choose a different id (next sequence number) or update via separate update flow"
                )
            with open(out_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(hypothesis, f, allow_unicode=True, sort_keys=False)
        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(
                f"write_hypothesis failed to persist {hyp_id}: {e}. "
                f"Target: {out_dir}. Check disk / permissions / yaml availability."
            )

        logger.info(
            "[write_hypothesis] %s applies_to=%s source_kind=%s",
            hyp_id, args["applies_to"], args["source_kind"],
        )

        return (
            f"Hypothesis {hyp_id} persisted "
            f"(applies_to={args['applies_to']}, source={args['source_path']}). "
            f"Path: data/services/doctor/hypotheses/{hyp_id}.yaml"
        )
