# [OMNI] origin=ai-ide domain=decisions ts=2026-06-27T00:00:00Z type=module status=active
# [OMNI] summary="回填器:把决策探索图里『真本体缺失』的产物/设施/真源注册进 material registry(external_pointer),记台账;丢失的标 lost。让图不再有悬空叶子。"
# [OMNI] why="决策骨架强背书但产物叶子系统性空白(候选图/选型报告/酒馆资产没注册,VILO 清单磁盘已丢失)。权威=plan B4。"
# [OMNI] tags=decisions,exploration,backfill,registry,external_pointer
"""回填器 —— 把探索图里缺的真本体补登记进来。

做法(照 registration.py 范式,幂等):
  - 每个缺口 = 一条 gap 规格(节点标签/类型/真实路径/连到哪条决策)。
  - 路径存在 → get_registry().write(InstanceEntry(type=external_pointer, ...));不存在 → 标 status=lost。
  - 全部落台账 exploration/backfill_ledger.jsonl(node→material_id),投影器据此把产物/设施/真源
    节点注入图,并连回它所属的决策,版本链(候选图 v6→v7→v8→10分组)用显式 supersedes。
所有真实路径已在磁盘核对(见 plan 附录)。注册写的是 data/services/registry/,不动外部目标一字节。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._paths import BACKFILL_LEDGER_PATH, ensure_dirs

# 仓外真源根(webworks/故事 都与 omnicompany 同级)
_WS = Path(__file__).resolve().parents[7]  # exploration→decisions→domains→packages→omnicompany→src→omnicompany_repo→WindowsWorkspace


def _p(rel: str) -> str:
    return str((_WS / rel).resolve())


# aigc 领域的缺口规格(真实决策 id + 真实磁盘路径,见 plan 附录映射表)
# kind: 产物 / 设施 / 真源;rel: 产出(决策→产物/设施) / 依据(真源→工作决策)
# supersedes: 上一版的 gap key(版本链);version/version_family: 显式版本(命名约定不够干净时声明)
_AIGC_GAPS: list[dict[str, Any]] = [
    {"key": "fac_aigc_lab", "kind": "设施", "label": "aigc-lab 统一设施",
     "path": _p("webworks/apps/aigc-lab"), "link_to": "DEC-2026-06-18-176", "rel": "产出",
     "summary": "gen 矩阵引擎+project-cards 卡片管线+审阅台 一个 app", "version_family": "aigc-lab", "version": 2},
    {"key": "fac_gen_engine", "kind": "设施", "label": "gen 矩阵引擎",
     "path": _p("webworks/apps/aigc-lab/gen"), "link_to": "DEC-2026-06-18-201", "rel": "产出",
     "summary": "多模型多风格批量出图+轮询+记录(batch.mjs)"},
    {"key": "fac_review_station", "kind": "设施", "label": "审阅台(本地图像审阅 Web :8077)",
     "path": _p("webworks/apps/aigc-lab"), "link_to": "DEC-2026-06-18-202", "rel": "产出",
     "summary": "卡+N候选+状态+全量参数 的图像审阅台 server.mjs"},
    {"key": "prod_candidates_v6", "kind": "产物", "label": "第一批候选图 v6(face-matrix)",
     "path": _p("webworks/apps/aigc-lab/gen/tasks-vilo-male-leads-face-matrix-v6.json"),
     "link_to": "DEC-2026-06-18-201", "rel": "产出", "version_family": "vilo候选图", "version": 6,
     "summary": "vilo 男主多模型候选图第一批(脸矩阵 v6)"},
    {"key": "prod_candidates_v7", "kind": "产物", "label": "候选图 v7(controlled)",
     "path": _p("webworks/apps/aigc-lab/gen/tasks-vilo-male-leads-face-matrix-v7-controlled.json"),
     "link_to": "DEC-2026-06-18-201", "rel": "产出", "version_family": "vilo候选图", "version": 7,
     "supersedes": "prod_candidates_v6", "summary": "受控迭代 v7"},
    {"key": "prod_candidates_v8", "kind": "产物", "label": "候选图 v8(divergent-face)",
     "path": _p("webworks/apps/aigc-lab/gen/tasks-vilo-male-leads-divergent-face-v8.json"),
     "link_to": "DEC-2026-06-18-201", "rel": "产出", "version_family": "vilo候选图", "version": 8,
     "supersedes": "prod_candidates_v7", "summary": "发散脸 v8"},
    {"key": "prod_10_substyle", "kind": "产物", "label": "10 个独立 sub-style 卡分组",
     "path": _p("webworks/apps/aigc-lab/project-cards/cards.json"),
     "link_to": "DEC-2026-06-18-204", "rel": "产出", "version_family": "vilo候选图", "version": 9,
     "supersedes": "prod_candidates_v8", "summary": "DB 迁移后按主体拆的 10 个独立 sub-style 分组"},
    {"key": "seed_vilo", "kind": "真源", "label": "vilo 主角设定 01-protagonist-vilo.md",
     "path": _p("故事/vilo-wants-to-know/seeds/01-protagonist-vilo.md"),
     "link_to": "BLF-2026-06-18-046", "rel": "依据", "summary": "vilo 主角孙雅洛真设定(粉双马尾骷髅装)"},
    {"key": "prod_vilo_unknown", "kind": "产物", "label": "VILO 设定缺口清单(已丢失)",
     "path": _p("故事/vilo-wants-to-know/VILO-SETTING-UNKNOWN.md"),
     "link_to": "BLF-2026-06-18-046", "rel": "产出",
     "summary": "BLF-046 anchor 声称已生成,但磁盘已不存在——曾存在已丢失"},
    {"key": "prod_selection_report", "kind": "产物", "label": "AI 图像工具选型报告(未注册)",
     "path": None, "link_to": "CMT-2026-06-18-021", "rel": "产出",
     "summary": "被指正『要给口碑实意义』的第一版选型报告;registry 唯一选型报告是 narrative 域的,非此——未注册"},
    {"key": "prod_tavern_art", "kind": "产物", "label": "酒馆蓝图美术资产批(未定位)",
     "path": None, "link_to": "DEC-2026-06-19-1338", "rel": "产出",
     "summary": "酒馆重置用的蓝图风格地上物/地标资产;reviewstage 无匹配——未定位/未注册"},
]

_GAPS_BY_PROJECT: dict[str, list[dict]] = {"aigc": _AIGC_GAPS}


def _slug(key: str) -> str:
    return key.replace(" ", "_")


def plan_backfill(project: str = "aigc") -> list[dict]:
    """只算不写:返回每个缺口的状态(exists/lost/unlocated)+ 将分配的 material_id。"""
    gaps = _GAPS_BY_PROJECT.get(project, [])
    out = []
    for g in gaps:
        path = g.get("path")
        if path is None:
            status = "unlocated"      # 真本体里就没有(选型报告/酒馆资产)
        elif Path(path).exists():
            status = "registered"
        else:
            status = "lost"           # anchor 声称有但磁盘已丢失(VILO 清单)
        material_id = f"external_pointer:exploration.{project}.{_slug(g['key'])}"
        out.append({**g, "status": status, "material_id": material_id, "project": project})
    return out


def run_backfill(project: str = "aigc", dry_run: bool = False) -> dict:
    """注册存在的缺口为 external_pointer(幂等),全部落台账。返回汇总。"""
    ensure_dirs()
    rows = plan_backfill(project)
    registered = 0
    if not dry_run:
        from omnicompany.packages.services._core.registry import InstanceEntry, get_registry
        reg = get_registry()
        for r in rows:
            if r["status"] != "registered":
                continue
            reg.write(InstanceEntry(
                entity_id=r["material_id"],
                type="external_pointer",
                name=_slug(r["key"]),
                package=f"exploration.{project}",
                source_file=r["path"],
                attrs={
                    "kind_material": r["kind"],
                    "external_target": r["path"],
                    "summary": r.get("summary", ""),
                    "registered_via": "exploration-backfill",
                    "label": r["label"],
                },
                deps=[],
            ))
            registered += 1
        # 台账:全量重写(派生自规格,可重建)
        with BACKFILL_LEDGER_PATH.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({
                    "key": r["key"], "project": project, "material_id": r["material_id"],
                    "kind": r["kind"], "label": r["label"], "status": r["status"],
                    "path": r.get("path"), "link_to": r["link_to"], "rel": r["rel"],
                    "summary": r.get("summary", ""),
                    "version": r.get("version"), "version_family": r.get("version_family"),
                    "supersedes": r.get("supersedes"),
                }, ensure_ascii=False) + "\n")
    summary = {
        "project": project, "dry_run": dry_run,
        "total": len(rows), "registered": registered,
        "by_status": _count_status(rows),
        "ledger": str(BACKFILL_LEDGER_PATH),
    }
    return summary


def read_ledger() -> list[dict]:
    if not BACKFILL_LEDGER_PATH.is_file():
        return []
    out = []
    for line in BACKFILL_LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _count_status(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="回填探索图缺口(注册产物/设施/真源)")
    ap.add_argument("--project", default="aigc")
    ap.add_argument("--run", action="store_true", help="真注册+写台账(默认 dry-run)")
    args = ap.parse_args(argv)
    s = run_backfill(project=args.project, dry_run=not args.run)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
