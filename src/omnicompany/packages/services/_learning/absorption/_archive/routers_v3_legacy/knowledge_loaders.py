# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-18T00:00:00Z type=router
# [OMNI] material_id="material:learning.absorption.v3_legacy.knowledge_loaders.wiki_fanin.routers.py"
"""OmniCompany 自知识 Loader 家族（P-13/F-15 示范）。

7 个 Router（含 Stage3 入口分发 + 3 个 QueryBuilder + 3 个 Loader）：
- Stage3EntryBootstrapRouter (TRANSFORMER RULE，Stage3 专用)
    absorption.report.v3 → absorption.report.v3

Capability 家族（供 Stage3 / V3 主路共用）：
- CapabilityInventoryQueryBuilderRouter (TRANSFORMER RULE)
- CapabilityInventoryLoaderRouter (ANCHOR HARD)

Gap 家族：
- GapRegistryQueryBuilderRouter (TRANSFORMER RULE)
- GapRegistryLoaderRouter (ANCHOR HARD)

Reception 家族（2026-04-18 新增，供 V3 主路 ModuleExplorer composite 消费）：
- ReceptionIntentsQueryBuilderRouter (TRANSFORMER RULE)
    absorption.repomap → omni.self.reception_intent_query
- ReceptionIntentsLoaderRouter (ANCHOR HARD)
    omni.self.reception_intent_query → omni.self.reception_intents

注意：Capability / Gap 家族的 FORMAT_IN 目前写 absorption.report.v3（服务 Stage3
SpecParser 路径）。若 V3 主路 ModuleExplorer 分支要复用它们，需要 absorption.repomap
版本——但 QueryBuilder 内部只用 repo_name 做 trace 标识，二者接口兼容；未来扩到主路
时另起 router 或做 FORMAT_IN 泛化。本文件 Reception 家族直接服务主路，FORMAT_IN =
absorption.repomap。

设计原则：
- F-15 严格遵守：每个 Router 的 run() 只读声明 FORMAT_IN 的 schema 字段
- P-13 严格遵守：Router 不做 `return {**input_data, ...}` 透传暗管
- Query Builder 从 repomap 读 repo_name 做 trace 标识，产出默认 query 对象
- Loader 消费 query 字段，调 wiki_loader 结构化加载函数，产出 knowledge Format
"""
from __future__ import annotations

from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

from omnicompany.packages.services._learning.absorption.wiki_loader import (
    load_capability_inventory,
    load_gap_registry,
    load_reception_intents,
)


# ─── 入口分发器（TRANSFORMER RULE，identity）─────────────────────────────

class Stage3EntryBootstrapRouter(Router):
    """Stage 3 入口分发节点。identity 传递，把入口 absorption.report.v3 原样分发给
    三个下游（spec_parser 直连 + 两个 query builder 启动 loader 链）。

    存在原因：Stage 3 的 SpecParser 消费 composite Format（absorption.proposal.context），
    需要 3 路 fan-in。但 PipelineSpec.entry 只能一个节点，故用本节点作入口，fan-out 3 路。
    """

    DESCRIPTION = (
        "Stage 3 入口分发器：identity 传递 absorption.report.v3，3 路 fan-out 到 spec_parser + "
        "两个 query builder。不调 LLM，不改状态。"
    )
    FORMAT_IN = "absorption.report.v3"
    FORMAT_OUT = "absorption.report.v3"

    def run(self, input_data: Any) -> Verdict:
        return Verdict(
            kind=VerdictKind.PASS,
            output=input_data,
            confidence=1.0,
            diagnosis="Stage3EntryBootstrap: identity pass-through",
            granted_tags=["stage.v3.s3.entry"],
        )


# ─── 查询构造器（TRANSFORMER RULE）─────────────────────────────────────

class CapabilityInventoryQueryBuilderRouter(Router):
    """从 absorption.report.v3 派生 capability_inventory_query（全默认值）。

    本节点不调 LLM、不改状态；只产出结构化 query 对象。
    FORMAT_IN 只读 repo_name 字段做 trace 标识（F-15 合规）。
    """

    DESCRIPTION = (
        "从 absorption.report.v3 的 repo_name 派生 omni.self.capability_inventory_query，"
        "query 使用全默认值（收 active+design，带 README 能力表）。"
        "纯 RULE 节点，无 LLM 调用，无文件副作用。"
    )
    FORMAT_IN = "absorption.report.v3"
    FORMAT_OUT = "omni.self.capability_inventory_query"

    def run(self, input_data: Any) -> Verdict:
        repo_name = (input_data or {}).get("repo_name", "unknown")
        query = {
            "filter_maturity": ["active", "design"],
            # 不过滤 tags / 带 README map 均为默认值
            "include_readme_map": True,
            "requested_by": f"absorption.v3-stage3/{repo_name}",
        }
        return Verdict(
            kind=VerdictKind.PASS,
            output=query,
            confidence=1.0,
            diagnosis=f"CapabilityInventoryQueryBuilder: 生成默认 query (trace={repo_name})",
            granted_tags=["omni.self.query"],
        )


class GapRegistryQueryBuilderRouter(Router):
    """从 absorption.report.v3 派生 gap_registry_query（全默认值）。"""

    DESCRIPTION = (
        "从 absorption.report.v3 的 repo_name 派生 omni.self.gap_registry_query，"
        "query 使用全默认值（收 P0+P1+P2，带 INDEX 摘要）。"
        "纯 RULE 节点，无 LLM 调用，无文件副作用。"
    )
    FORMAT_IN = "absorption.report.v3"
    FORMAT_OUT = "omni.self.gap_registry_query"

    def run(self, input_data: Any) -> Verdict:
        repo_name = (input_data or {}).get("repo_name", "unknown")
        query = {
            "filter_priority": ["P0", "P1", "P2"],
            "include_index_summary": True,
            "requested_by": f"absorption.v3-stage3/{repo_name}",
        }
        return Verdict(
            kind=VerdictKind.PASS,
            output=query,
            confidence=1.0,
            diagnosis=f"GapRegistryQueryBuilder: 生成默认 query (trace={repo_name})",
            granted_tags=["omni.self.query"],
        )


# ─── 加载器（ANCHOR HARD）──────────────────────────────────────────────

class CapabilityInventoryLoaderRouter(Router):
    """读 wiki 产出 omni.self.capability_inventory。

    实际工作委托给 wiki_loader.load_capability_inventory()（lru_cache 缓存）。
    根据 query 的 filter_maturity / filter_tags / include_readme_map 裁剪输出。
    HARD 节点：扫盘 + 按 query 过滤，无 LLM。
    """

    DESCRIPTION = (
        "扫 src/omnifactory/**/DESIGN.md，产出 omni.self.capability_inventory。"
        "按 query 的 filter_maturity / filter_tags 过滤；include_readme_map 控制是否带 README 表。"
        "数据来自 wiki_loader.load_capability_inventory()（lru_cache 进程级缓存）。"
    )
    FORMAT_IN = "omni.self.capability_inventory_query"
    FORMAT_OUT = "omni.self.capability_inventory"

    def run(self, input_data: Any) -> Verdict:
        query = input_data or {}
        filter_maturity = set(query.get("filter_maturity") or ["active", "design"])
        filter_tags = query.get("filter_tags")  # None = 不过滤
        include_readme = query.get("include_readme_map", True)

        try:
            inv = load_capability_inventory()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"error": f"wiki_loader 失败: {type(e).__name__}: {e}"},
                diagnosis=f"CapabilityInventoryLoader: 加载失败 {e}",
            )

        # 按 query 过滤（不改原 cache 对象）
        filtered_modules = [
            m for m in inv["modules"]
            if m["maturity"] in filter_maturity
            and (filter_tags is None or any(t in filter_tags for t in (m.get("tags") or [])))
        ]

        output = {
            "generated_at": inv["generated_at"],
            "source_root": inv["source_root"],
            "source_commit": inv["source_commit"],
            "module_count": len(filtered_modules),
            "modules": filtered_modules,
            "readme_capability_map": inv["readme_capability_map"] if include_readme else "",
        }

        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            confidence=1.0,
            diagnosis=(
                f"CapabilityInventoryLoader: {len(filtered_modules)} 模块 "
                f"(filter_maturity={sorted(filter_maturity)}, tags={filter_tags or 'all'})"
            ),
            granted_tags=["omni.self.knowledge"],
        )


class ReceptionIntentsQueryBuilderRouter(Router):
    """从 absorption.repomap 派生 reception_intent_query（全默认值）。

    用于 V3 主路 ModuleExplorer composite fan-in 的第 4 路：先从 RepoMapper 出来，
    再分到本节点构造 query → 下游 Loader。
    """

    DESCRIPTION = (
        "从 absorption.repomap 的 repo_name 派生 omni.self.reception_intent_query，"
        "query 使用全默认值（全部基础设施模块 + 带 soft_preferences）。"
        "纯 RULE 节点，无 LLM 调用，无文件副作用。"
    )
    FORMAT_IN = "absorption.repomap"
    FORMAT_OUT = "omni.self.reception_intent_query"

    def run(self, input_data: Any) -> Verdict:
        repo_name = (input_data or {}).get("repo_name", "unknown")
        query = {
            "filter_modules": None,  # 不过滤 → 收全部基础设施模块
            "include_soft_preferences": True,
            "requested_by": f"absorption.v3/{repo_name}",
        }
        return Verdict(
            kind=VerdictKind.PASS,
            output=query,
            confidence=1.0,
            diagnosis=f"ReceptionIntentsQueryBuilder: 生成默认 query (trace={repo_name})",
            granted_tags=["omni.self.query"],
        )


class ReceptionIntentsLoaderRouter(Router):
    """读 wiki 产出 omni.self.reception_intents。

    扫 src/omnifactory/{runtime|protocol|core|bus|primitives|tools|tracing}/**/DESIGN.md
    的第 8 节 ## 接收意愿，产出结构化 intents 列表。
    委托 wiki_loader.load_reception_intents()（lru_cache 进程级缓存）。
    HARD 节点：扫盘 + 按 query 过滤，无 LLM。
    """

    DESCRIPTION = (
        "扫基础设施模块 DESIGN.md 第 8 节 ## 接收意愿，产出 omni.self.reception_intents。"
        "按 query.filter_modules 过滤；include_soft_preferences 控制是否返回 soft_preferences 字段。"
        "数据来自 wiki_loader.load_reception_intents()（lru_cache 进程级缓存）。"
    )
    FORMAT_IN = "omni.self.reception_intent_query"
    FORMAT_OUT = "omni.self.reception_intents"

    def run(self, input_data: Any) -> Verdict:
        query = input_data or {}
        filter_modules = query.get("filter_modules")  # None = 不过滤
        include_soft = query.get("include_soft_preferences", True)

        try:
            obj = load_reception_intents()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"error": f"wiki_loader 失败: {type(e).__name__}: {e}"},
                diagnosis=f"ReceptionIntentsLoader: 加载失败 {e}",
            )

        raw_intents = obj.get("intents", [])
        if filter_modules:
            filter_set = set(filter_modules)
            raw_intents = [it for it in raw_intents if it.get("module_path") in filter_set]

        filtered_intents = []
        for it in raw_intents:
            entry = {
                "module_path": it["module_path"],
                "maturity": it["maturity"],
                "welcome_themes": it.get("welcome_themes", []),
                "hard_constraints": it.get("hard_constraints", []),
                "maturity_preference": it.get("maturity_preference", "any"),
                "source_path": it.get("source_path", ""),
            }
            if include_soft:
                entry["soft_preferences"] = it.get("soft_preferences", [])
            else:
                entry["soft_preferences"] = []
            filtered_intents.append(entry)

        output = {
            "generated_at": obj["generated_at"],
            "source_root": obj["source_root"],
            "module_count": len(filtered_intents),
            "intents": filtered_intents,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            confidence=1.0,
            diagnosis=(
                f"ReceptionIntentsLoader: {len(filtered_intents)} intents "
                f"(filter={filter_modules or 'all'}, soft={include_soft})"
            ),
            granted_tags=["omni.self.knowledge"],
        )


class GapRegistryLoaderRouter(Router):
    """读 docs/gaps/G*.md 产出 omni.self.gap_registry。"""

    DESCRIPTION = (
        "扫 docs/gaps/G*.md，产出 omni.self.gap_registry。"
        "按 query 的 filter_priority / filter_state 过滤；include_index_summary 控制摘要。"
        "数据来自 wiki_loader.load_gap_registry()（lru_cache 进程级缓存）。"
    )
    FORMAT_IN = "omni.self.gap_registry_query"
    FORMAT_OUT = "omni.self.gap_registry"

    def run(self, input_data: Any) -> Verdict:
        query = input_data or {}
        filter_priority = set(query.get("filter_priority") or ["P0", "P1", "P2"])
        filter_state = query.get("filter_state")  # None = 不过滤
        include_index = query.get("include_index_summary", True)

        try:
            reg = load_gap_registry()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"error": f"wiki_loader 失败: {type(e).__name__}: {e}"},
                diagnosis=f"GapRegistryLoader: 加载失败 {e}",
            )

        filtered_gaps = [
            g for g in reg["gaps"]
            if g.get("priority") in filter_priority
            and (filter_state is None or g.get("state") in filter_state)
        ]

        output = {
            "generated_at": reg["generated_at"],
            "source_dir": reg["source_dir"],
            "gap_count": len(filtered_gaps),
            "gaps": filtered_gaps,
            "index_summary": reg["index_summary"] if include_index else "",
        }

        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            confidence=1.0,
            diagnosis=(
                f"GapRegistryLoader: {len(filtered_gaps)} gaps "
                f"(priority={sorted(filter_priority)}, state={filter_state or 'all'})"
            ),
            granted_tags=["omni.self.knowledge"],
        )
