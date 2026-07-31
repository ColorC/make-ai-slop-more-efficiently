# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-13T00:00:00Z type=router
# [OMNI] material_id="material:learning.absorption.v3_legacy.module_selector.llm_router.py"
"""module_picker — V3 ModulePickerRouter（LLM 单次调用）

输入：absorption.repomap（coarse_view + detail_views + files[]）
输出：absorption.important-modules（LLM 语义选出的重要模块 + 原因 + 展开的 detail_view）

不是 PageRank top-N，是 LLM 看 coarse_view + self_portrait 后的语义判断。
设计文档：docs/plans/[2026-04-13]REPO-ABSORPTION-V3/DESIGN.md §三.Format 2
"""
from __future__ import annotations

import json
import re
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

_MODEL = "qwen3.6-plus"

_SYSTEM = """你是 OmniCompany 的知识探索者。你会收到：
1. 整个 repo 的**粗粒度符号地图**（coarse_view）：每行一个文件，含行数和主要符号
2. OmniCompany 的**自画像**（G1-G7 已知缺口）

你的任务：从 coarse_view 中找出所有对 OmniCompany 有学习价值的模块。

## 工作方式

这是一个综合性的跨文件、跨模块探索任务。吸纳是理解一个系统——不是填表格。

**自由选择，不要人为约束**：
- 不需要每个缺口都选一个——同一个缺口可以选多个文件，因为它们解决了问题的不同侧面
- 不需要"平均分配"——如果某个缺口这个 repo 完全没有，就不要硬凑
- 没有数量限制——有多少值得选的就选多少，10个也行，25个也行
- 一个文件可以同时关联多个缺口——选主要的那个，理由里说明

**主动扫描，不要只看靠前的**：
- coarse_view 按行数×关键词排序，排名靠后的小文件可能更精密
- 看到 error/retry/classifier/registry/approval/compress/provider 等词就去看
- 大文件（1000行以上）几乎都值得考虑——它们存在必有原因
- 独立的小工具模块（几十行）往往是最直接可移植的

**优先级判断**：
- P0：OmniCompany 完全缺失，这个文件直接填了那个空白
- P1：OmniCompany 有类似实现但这里做得更好或不同
- P2：边缘参考，未来可能用到

## 输出格式

纯 JSON，无 markdown 代码块，无其他文字：
{
  "repo_name": "...",
  "modules": [
    {
      "path": "相对文件路径（必须来自 coarse_view，必须存在）",
      "gap_id": "G1",
      "priority": "P0",
      "reason": "这个文件做了什么，为什么值得关注（具体说，不要泛泛而谈）",
      "request_detail": true
    }
  ],
  "selection_rationale": "整体选择思路（1-2句）",
  "modules_skipped": ["path — 为什么主动跳过（对看起来重要但实际不选的文件说明）"]
}

所有选中模块 request_detail 设为 true——你选了就意味着需要看细节。"""


class ModulePickerRouter(Router):
    """V3 模块选择节点（LLM 单次调用）。

    看 coarse_view + self_portrait，语义判断哪些模块与 G1-G7 缺口相关，
    展开 detail_views，产出带理由的模块清单。
    """

    DESCRIPTION = (
        "V3 模块探索：LLM 单次调用，自由扫描 coarse_view，"
        "无数量约束地选出所有有学习价值的模块，展开 detail_view"
    )
    FORMAT_IN = "absorption.repomap"
    FORMAT_OUT = "absorption.important-modules"

    _MODEL = _MODEL

    def __init__(self, *, model: str | None = None, **kwargs: Any) -> None:
        self._model = model or self._MODEL

    def run(self, input_data: Any) -> Verdict:
        repo_name = input_data.get("repo_name", "unknown")
        coarse_view = input_data.get("coarse_view", "")
        detail_views: dict[str, str] = input_data.get("detail_views") or {}
        files: list[dict] = input_data.get("files") or []
        self_portrait = input_data.get("self_portrait", "")

        if not coarse_view:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis="ModulePicker: coarse_view 为空",
            )
        if not self_portrait:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis="ModulePicker: self_portrait 为空，无法判断缺口相关性",
            )

        # 文件总数和 top 文件提示
        total = input_data.get("total_files", len(files))
        shown = len([l for l in coarse_view.splitlines() if l and not l.startswith("#")])

        user_msg = f"""# 模块选择任务

**Repo**: {repo_name}
**文件总数**: {total}（coarse_view 展示了按重要性排名的前 {shown} 个）

## OmniCompany 自画像（G1-G7 缺口）

{self_portrait}

---

## Repo 符号地图（coarse_view）

{coarse_view}

---

## 任务

从上面的地图中选出 5-15 个与 G1-G7 缺口最相关的模块。
记住：排名靠后的小文件可能比排名靠前的大文件更有价值——主动在地图里搜索关键词。
输出 JSON，无其他文字。"""

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=_SYSTEM,
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw.strip())
            data = json.loads(raw)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis=f"ModulePicker LLM 调用失败: {type(e).__name__}: {e}",
            )

        modules_raw: list[dict] = data.get("modules") or []
        valid_paths = {f["path"] for f in files}

        # 展开 detail_views，校验路径存在
        modules_out: list[dict] = []
        invalid_paths: list[str] = []
        for m in modules_raw:
            path = m.get("path", "")
            if path not in valid_paths:
                invalid_paths.append(path)
                continue
            detail = detail_views.get(path, "") if m.get("request_detail", True) else ""
            modules_out.append({
                "path": path,
                "gap_id": m.get("gap_id", "?"),
                "priority": m.get("priority", "P2"),
                "reason": m.get("reason", ""),
                "detail_view": detail,
            })

        p0 = sum(1 for m in modules_out if m["priority"] == "P0")
        gaps_covered = sorted({m["gap_id"] for m in modules_out})

        diag = (
            f"ModulePicker: {len(modules_out)} 模块选出（{p0} P0），"
            f"覆盖缺口: {gaps_covered}"
        )
        if invalid_paths:
            diag += f"，{len(invalid_paths)} 个路径无效已丢弃: {invalid_paths[:3]}"

        kind = VerdictKind.PASS if modules_out else VerdictKind.FAIL

        return Verdict(
            kind=kind,
            output={
                **input_data,
                "repo_name": repo_name,
                "modules": modules_out,
                "selection_rationale": data.get("selection_rationale", ""),
                "modules_skipped": data.get("modules_skipped") or [],
            },
            confidence=0.85 if modules_out else 0.0,
            diagnosis=diag,
            granted_tags=["domain.absorption", "stage.v3.picker"],
        )
