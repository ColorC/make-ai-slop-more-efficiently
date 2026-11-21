# [OMNI] origin=claude-code domain=services/_diagnosis/ux_audit ts=2026-06-30T00:00:00Z type=worker status=active
# [OMNI] summary="ux_audit 4 节点:InteractionEnumerator→InfoEnumerator→NavEnumerator→Consolidator(全确定性)。"
# [OMNI] material_id="material:services._diagnosis.ux_audit.nodes"
"""ux_audit Worker 节点(确定性,可重放)。三维枚举累积 → 汇总报告。"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.packages.services._core.omnicompany import Worker

from .enumerators import enum_interactions, enum_info, enum_nav, consolidate


def _payload(input_data: Any, fmt_in: str) -> dict:
    if isinstance(input_data, dict):
        v = input_data.get(fmt_in)
        if isinstance(v, dict):
            return v
        return input_data
    return {}


class InteractionEnumerator(Worker):
    """确定性扫 .tsx:露在外的按钮 / KebabMenu 收纳项 / select / input / onClick。"""
    DESCRIPTION = "枚举每个面板的交互:露出 <button>(testid/title/文字)、收进 ⋯ 的 KebabMenu items、select、input、onClick。"
    FORMAT_IN = "ux_audit.target"
    FORMAT_OUT = "ux_audit.accum"

    def run(self, input_data: Any) -> Verdict:
        p = _payload(input_data, self.FORMAT_IN)
        src_root = p.get("src_root")
        if not src_root or not Path(src_root).exists():
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"src_root 不存在: {src_root}", output={})
        inc = p.get("include_dirs"); exc = p.get("exclude")
        inter = enum_interactions(src_root, inc, exc)
        accum = {"src_root": src_root, "app": p.get("app"), "interactions": inter,
                 "include_dirs": inc, "exclude": exc}
        return Verdict(kind=VerdictKind.PASS, diagnosis=f"交互枚举: {len(inter)} 面板", output={self.FORMAT_OUT: accum})


class InfoEnumerator(Worker):
    """确定性:每面板信息信号(字号档=层级深度 / 字重档 / 整段说明文字 / mono)。"""
    DESCRIPTION = "枚举每面板信息层级信号:字号档数、字重档数、整段说明文字数(>=16 CJK 字)、mono 用量。"
    FORMAT_IN = "ux_audit.accum"
    FORMAT_OUT = "ux_audit.accum"

    def run(self, input_data: Any) -> Verdict:
        accum = _payload(input_data, self.FORMAT_IN)
        src_root = accum.get("src_root")
        if not src_root:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="accum 缺 src_root", output={})
        accum = dict(accum)
        accum["info"] = enum_info(src_root, accum.get("include_dirs"), accum.get("exclude"))
        return Verdict(kind=VerdictKind.PASS, diagnosis=f"信息枚举: {len(accum['info'])} 面板", output={self.FORMAT_OUT: accum})


class NavEnumerator(Worker):
    """确定性:枚举导航调用 → 跳转边(从面板→动作→去向)。"""
    DESCRIPTION = "枚举导航:openTab(目标type)/openInVscode/openInOmnidashboard/openChatui/postHostMessage(type)/window.open → 跳转边。"
    FORMAT_IN = "ux_audit.accum"
    FORMAT_OUT = "ux_audit.accum"

    def run(self, input_data: Any) -> Verdict:
        accum = _payload(input_data, self.FORMAT_IN)
        src_root = accum.get("src_root")
        if not src_root:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="accum 缺 src_root", output={})
        accum = dict(accum)
        accum["nav"] = enum_nav(src_root, accum.get("include_dirs"), accum.get("exclude"))
        return Verdict(kind=VerdictKind.PASS, diagnosis=f"跳转枚举: {len(accum['nav'])} 面板", output={self.FORMAT_OUT: accum})


class Consolidator(Worker):
    """确定性:汇总三维 → per_panel + 错位清单 + markdown 总表,落盘 data/services/ux_audit/。"""
    DESCRIPTION = "汇总交互/信息/跳转三维 → 每界面行 + 错位界面清单 + 人读 markdown 总表;落盘并返回报告。"
    FORMAT_IN = "ux_audit.accum"
    FORMAT_OUT = "ux_audit.report"

    def run(self, input_data: Any) -> Verdict:
        accum = _payload(input_data, self.FORMAT_IN)
        if not accum.get("src_root"):
            return Verdict(kind=VerdictKind.FAIL, diagnosis="accum 缺 src_root", output={})
        c = consolidate(accum)
        app = accum.get("app") or "frontend"
        safe = "".join(ch if ch.isalnum() else "_" for ch in app)
        out_dir = os.path.join("data", "services", "ux_audit")
        report_path = ""
        try:
            os.makedirs(out_dir, exist_ok=True)
            report_path = os.path.join(out_dir, f"{safe}_{int(time.time())}.md")
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(c["markdown"])
        except Exception as e:  # 落盘失败不阻断,报告仍在 output 里
            report_path = f"(落盘失败: {e})"
        report = {
            "app": app, "src_root": accum["src_root"],
            "panel_count": c["totals"]["panels"], "totals": c["totals"],
            "per_panel": c["per_panel"], "offenders": c["offenders"],
            "markdown": c["markdown"], "report_path": report_path,
        }
        return Verdict(kind=VerdictKind.PASS,
                       diagnosis=f"UX 审计: {c['totals']['panels']} 面板 · {c['totals']['offenders']} 错位 · {report_path}",
                       output={self.FORMAT_OUT: report})
