# [OMNI] origin=ai-ide domain=decisions ts=2026-06-27T00:00:00Z type=module status=active
# [OMNI] summary="版本号/版本族解析 + 从 supersedes 边拼版本链。让耐用物(设施/被改产物)在图里串成 DAG 版本列。"
# [OMNI] why="material registry 无 version/supersedes 字段;版本契约从命名约定(aigc-lab/gen *-v6/v7)与决策 links.supersedes 提取。权威=plan B3。"
# [OMNI] tags=decisions,exploration,version,supersedes
"""版本化:解析版本号 + 版本族,从 supersedes 边拼版本链。

铁律(对抗评审 R1 #1):版本语义只属【耐用物 material】(产物/设施会有版本演进)。
决策/信念/指正(DEC/BLF/CMT)绝不按 anchor.ref/statement 里的 vN token 推断版本——否则
reports/q1/v2/summary.md、src/api/v2/handler.py 这类路径会被误当版本号,把无关决策强行串成
凭空的 supersedes 链,违反『图=真本体的投影,不假装本体里有的东西』。

约定:
  - 版本号只取名字【末段文件名】里最后一个 `vN` token(目录段 api/v2/ 不算);face-matrix-v6 → 6。
  - 版本族 = 去掉该 token 后的规整名,同族多版本竖排成列。
  - 同族补链只对 material:跳过同号、只补严格递增、加环守卫,绝不把平行/反向项串成版本链。
"""

from __future__ import annotations

import re

# vN token:前面不接字母数字(避免匹配 "rev2"),v 后跟数字。取最后一个。
_VTOKEN = re.compile(r"(?<![A-Za-z0-9])v(\d+)", re.IGNORECASE)


def parse_version(name: str) -> tuple[str, int | None]:
    """从名字解析 (version_family, version_num)。只看末段文件名,目录段不算版本。无版本号则 (规整名, None)。"""
    name = (name or "").strip()
    if not name:
        return "", None
    base = re.split(r"[\\/]", name)[-1]          # 只看末段,目录段 api/v2/ 不当版本号
    matches = list(_VTOKEN.finditer(base))
    if not matches:
        return base, None
    m = matches[-1]
    num = int(m.group(1))
    family = (base[: m.start()] + base[m.end():]).strip(" -_/.")
    family = re.sub(r"[-_]{2,}", "-", family)
    return (family or base), num


def annotate_versions(nodes: list[dict]) -> None:
    """就地给【耐用物】节点补 version / version_family(若名字含 vN token)。

    已显式带 version 的节点(回填台账声明的)不覆盖;非 material 节点一律不推断版本(R1 #1 铁律)。
    """
    for n in nodes:
        if n.get("version") is not None:
            continue
        if n.get("record_kind") != "material":   # 只耐用物有版本演进;决策/信念/指正不参与
            continue
        # 只认受控字段 version_name(=material 的文件名,投影层设);绝不从人写的 label/name 猜版本
        # (R2:label 兜底会把『候选图 v3』这种自由文本误当版本号,凭空造假链)。
        fam, num = parse_version(n.get("version_name") or "")
        if num is not None:
            n["version"] = num
            n["version_family"] = fam


def _reaches(succ: dict[str, str], start: str, target: str) -> bool:
    """沿 succ 链 start 是否能到 target(环检测用)。"""
    seen: set[str] = set()
    cur = start
    while cur in succ:
        cur = succ[cur]
        if cur == target:
            return True
        if cur in seen:
            return False
        seen.add(cur)
    return False


def build_version_chains(nodes: list[dict], edges: list[dict]) -> list[list[str]]:
    """从 supersedes 边(source=老, target=新)拼版本链;返回 [[old_id, ..., new_id], ...]。

    再用同 version_family + version 号给【material】同族节点补链(严格递增、跳同号、环守卫)。
    """
    by_id = {n["id"]: n for n in nodes}
    succ: dict[str, str] = {}   # old -> new
    pred: dict[str, str] = {}   # new -> old
    for e in edges:
        if e.get("rel") == "supersedes":
            old, new = e.get("source"), e.get("target")
            if old in by_id and new in by_id:
                if succ.get(new) == old:    # 与已有边方向矛盾(显式 new→old)→ 跳过,避免环
                    continue
                succ[old] = new
                pred[new] = old

    # 同族按版本号补链:只对 material(version_family 只该出现在 material 上)
    fam_groups: dict[str, list[dict]] = {}
    for n in nodes:
        fam = n.get("version_family")
        if fam and n.get("version") is not None and n.get("record_kind") == "material":
            fam_groups.setdefault(fam, []).append(n)
    for fam, group in fam_groups.items():
        group.sort(key=lambda n: n["version"])
        for a, b in zip(group, group[1:]):
            if a["version"] == b["version"]:       # 同号不补(平行项不是彼此的版本)
                continue
            ai, bi = a["id"], b["id"]
            if ai in succ or bi in pred:           # 端已被显式边占用
                continue
            if _reaches(succ, bi, ai):             # 会成环 → 拒绝
                continue
            succ[ai] = bi
            pred[bi] = ai
            edges.append({"source": ai, "target": bi, "rel": "supersedes",
                          "note": f"同族版本号补链 v{a['version']}→v{b['version']}", "inferred": True})

    chains: list[list[str]] = []
    seen: set[str] = set()
    starts = [nid for nid in succ if nid not in pred]
    if not starts and succ:        # 全成环(异常):退而取所有起点,seen 断环输出部分链而非静默全丢
        starts = list(succ.keys())
    for start in starts:
        if start in seen:
            continue
        chain = [start]
        seen.add(start)
        cur = start
        while cur in succ:
            cur = succ[cur]
            if cur in seen:
                break
            chain.append(cur)
            seen.add(cur)
        if len(chain) > 1:
            chains.append(chain)
    return chains
