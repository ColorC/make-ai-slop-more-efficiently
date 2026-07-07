# [OMNI] origin=claude-code domain=services/_diagnosis/ux_audit ts=2026-06-30T00:00:00Z type=config status=active
# [OMNI] summary="ux_audit team 的 Material:target(source) / accum(internal 累积三维) / report(sink)。"
# [OMNI] material_id="material:services._diagnosis.ux_audit.formats"
"""ux_audit team · Material 定义(source / internal / sink)。

一件事:扫一个前端 src 目录,**确定性枚举**每个界面的交互(按钮/⋯/选择器/输入)、信息
(字号档=层级深度/说明段)、跳转(导航边),据「频率×重要性矩阵 + 信息层级」打错位标记,
出一份可复跑、可对比的 UX 审计报告。分类口径见 frostpane/REBUILD-STANDARD.md + INTERACTION-AUDIT.md。
"""
from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "ux_audit"

M_TARGET = Format(
    id=f"{DOMAIN}.target",
    name="UxAuditTarget",
    description=(
        "要审计的前端源码目录。\n"
        "- src_root (str, required): 前端 src 目录绝对路径(如 .../dashboard/frontend/src)\n"
        "- app (str, optional): 应用名(omnidashboard/lofa/poof/whatnow),仅用于报告标题\n"
        "- include_dirs (list[str], optional): 只扫这些子目录(默认 ['entities','shell'])\n"
        "- exclude (list[str], optional): 排除目录(默认 .git/node_modules/dist/__pycache__)"
    ),
    tags=["kind.source"],
)

M_ACCUM = Format(
    id=f"{DOMAIN}.accum",
    name="UxAuditAccum",
    description=(
        "三维枚举累积态(逐 worker 富化,透传 target)。\n"
        "- src_root (str) / app (str): 透传\n"
        "- interactions (dict[panel,dict]): 每面板 {buttons:[id...], kebab_uses, kebab_items:[label...], selects, inputs, onClick}\n"
        "- info (dict[panel,dict]): 每面板 {size_tiers, weight_tiers, long_text, mono}\n"
        "- nav (dict[panel,dict]): 每面板 {edge_name: {n, to:[...]}}"
    ),
    tags=["kind.internal"],
)

M_REPORT = Format(
    id=f"{DOMAIN}.report",
    name="UxAuditReport",
    description=(
        "UX 审计报告(sink)。\n"
        "- app (str) / src_root (str)\n"
        "- panel_count (int): 有交互/信息的界面数\n"
        "- totals (dict): 全局计数(交互总数/按钮露出/收纳率/跳转边数/错位数)\n"
        "- per_panel (list[dict]): 每界面 {panel, 露钮, kebab_uses, kebab_items, selects, inputs, size_tiers, long_text, nav_edges, flags:[...]}\n"
        "- offenders (list[dict]): 错位界面(平铺/删除无保护/无层级/说明冗余)+ 一句重组建议\n"
        "- markdown (str): 人读总表 markdown\n"
        "- report_path (str): 落盘的 markdown 报告路径"
    ),
    tags=["kind.sink"],
)


def register_formats(registry: FormatRegistry) -> None:
    for fmt in (M_TARGET, M_ACCUM, M_REPORT):
        registry.register(fmt)
