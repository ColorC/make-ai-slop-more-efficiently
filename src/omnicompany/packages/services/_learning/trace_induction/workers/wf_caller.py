# [OMNI] origin=claude-code domain=services/trace_induction ts=2026-04-22T00:00:00Z type=worker
# [OMNI] OMNI-004 NOTE: run() 为 async 不可避免 — 继承 SubTeamWorker, 基类 run()
# [OMNI] material_id="material:learning.trace_induction.workflow_factory_subteam_caller.worker.py"
#   本身就是 async (runtime/exec/sub_pipeline.py:81), 需要 await dispatch 调子管线.
"""WFCallerWorker — SubTeamWorker 调用 workflow-factory (SOFT, Stage 3 2026-04-22)."""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.runtime.exec.sub_pipeline import SubTeamWorker


class WFCallerWorker(Worker, SubTeamWorker):
    """通过 SubTeamWorker 标准接口调用 workflow-factory。

    输入 ti.requirement: {requirement_doc, purpose, domain}
    输出 ti.wf-result: {pipeline_name, package_path, files, purpose, domain}
    """

    TARGET_PIPELINE = "workflow-factory"
    TARGET_MAX_STEPS = 30

    FORMAT_IN = "ti.requirement"
    FORMAT_OUT = "ti.wf-result"
    DESCRIPTION = (
        "通过 SubTeamWorker 标准接口调用 workflow-factory 元管线，"
        "共享父管线的 EventBus 保持事件可观测性。WF 内部执行需求分析 → "
        "Format 设计 → 节点规划 → 代码生成 → 编译/LAP/路由审计 → 产出代码包。"
    )

    def prepare_input(self, input_data: dict) -> dict:
        return {"text": input_data.get("requirement_doc", "")}

    def extract_output(self, sub_result: Any, input_data: dict) -> dict:
        if not isinstance(sub_result, dict):
            return input_data

        files = sub_result.get("files", {})
        required = {"formats.py", "routers.py", "pipeline.py", "run.py"}
        if not required.issubset(set(files.keys())):
            return sub_result

        return {
            "pipeline_name": sub_result.get("pipeline_name", ""),
            "package_path": sub_result.get("package_path", ""),
            "files": files,
            "purpose": input_data.get("purpose", ""),
            "domain": input_data.get("domain", ""),
            "db_path": input_data.get("db_path", ""),
        }
