"""projections.py 的契约测试,用 conftest 的 sample_project 夹具。

注意夹具事实:nodes 都没有 route 字段,故所有场景按 node_ref→route 口径
都进 unplaced(beat 网格无场景)。测试如实断言这一行为。
"""

from __future__ import annotations

import json

from omnicompany.packages.narrative_studio import projections as proj


def _json_roundtrip(obj):
    """断言返回值确实 JSON 可序列化。"""
    return json.loads(json.dumps(obj, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# timeline
# --------------------------------------------------------------------------- #
def test_timeline_shape_and_unplaced(sample_project):
    tl = proj.timeline(sample_project)
    _json_roundtrip(tl)
    assert set(tl.keys()) == {"beats", "lines", "cells", "unplaced"}
    # 2 个 beat,1 条 storyline
    assert {b["id"] for b in tl["beats"]} == {"b-act1", "b-meet"}
    assert {l["id"] for l in tl["lines"]} == {"sl-fz"}
    # 夹具 nodes 无 route → 两场景全进 unplaced
    assert set(tl["unplaced"]) == {"s-start", "s-mirror"}
    assert tl["cells"] == {}


def test_timeline_route_places_scene(sample_project):
    # 给 n-start 配上 route=b-meet,场景应落到对应格
    for n in sample_project.nodes:
        if n.id == "n-start":
            n.route = "b-meet"
    tl = proj.timeline(sample_project)
    assert "s-start" not in tl["unplaced"]
    assert tl["cells"]["b-meet"]["sl-fz"] == ["s-start"]


# --------------------------------------------------------------------------- #
# outline
# --------------------------------------------------------------------------- #
def test_outline_hierarchy(sample_project):
    ol = proj.outline(sample_project)
    _json_roundtrip(ol)
    ids = [b["id"] for b in ol]
    # b-act1 顶层在前,b-meet 是其子,深度递增
    assert ids == ["b-act1", "b-meet"]
    depths = {b["id"]: b["depth"] for b in ol}
    assert depths == {"b-act1": 0, "b-meet": 1}
    # 无 route 时各 beat 不挂场景
    assert all(b["scenes"] == [] for b in ol)


def test_outline_scene_attached_with_route(sample_project):
    for n in sample_project.nodes:
        if n.id == "n-mirror":
            n.route = "b-meet"
    ol = proj.outline(sample_project)
    meet = next(b for b in ol if b["id"] == "b-meet")
    assert [s["scene_id"] for s in meet["scenes"]] == ["s-mirror"]


# --------------------------------------------------------------------------- #
# route_graph
# --------------------------------------------------------------------------- #
def test_route_graph(sample_project):
    rg = proj.route_graph(sample_project)
    _json_roundtrip(rg)
    assert {n["id"] for n in rg["nodes"]} == {"n-start", "n-mirror", "n-end-good"}
    # type 是字符串值
    types = {n["id"]: n["type"] for n in rg["nodes"]}
    assert types["n-start"] == "scene"
    assert types["n-end-good"] == "ending"
    edges = {e["id"]: e for e in rg["edges"]}
    assert edges["e1"]["has_condition"] is False
    assert edges["e2"]["has_condition"] is True
    assert edges["e2"]["has_effects"] is True


# --------------------------------------------------------------------------- #
# relationship_graph
# --------------------------------------------------------------------------- #
def test_relationship_graph(sample_project):
    g = proj.relationship_graph(sample_project)
    _json_roundtrip(g)
    assert {n["id"] for n in g["nodes"]} == {"c-wangyang", "c-fengzhong"}
    edge = g["edges"][0]
    assert edge["from"] == "c-wangyang"
    assert edge["to"] == "c-fengzhong"
    assert edge["label"] == "同作攻略对象"  # 无 label → 回退 nature


# --------------------------------------------------------------------------- #
# character_scenes
# --------------------------------------------------------------------------- #
def test_character_scenes(sample_project):
    cs = proj.character_scenes(sample_project, "c-fengzhong")
    _json_roundtrip(cs)
    # 枫钟 是两场的 pov + character
    assert {x["scene_id"] for x in cs} == {"s-start", "s-mirror"}
    assert all(x["pov"] == "c-fengzhong" for x in cs)
    # 无路线时 beat 为 None
    assert all(x["beat"] is None for x in cs)


def test_character_scenes_empty_for_unknown(sample_project):
    assert proj.character_scenes(sample_project, "c-nobody") == []


# --------------------------------------------------------------------------- #
# variable_refs
# --------------------------------------------------------------------------- #
def test_variable_refs_reads_and_writes(sample_project):
    # 枫钟.好感度:被 e2 connection + ending 读;不写
    vr = proj.variable_refs(sample_project, "枫钟.好感度")
    _json_roundtrip(vr)
    read_wheres = {r["where"] for r in vr["reads"]}
    assert "connection:e2" in read_wheres
    assert "ending:n-end-good" in read_wheres
    assert vr["writes"] == []

    # meta.通关路线数:被 e2 写;被 reveal 层读
    vm = proj.variable_refs(sample_project, "meta.通关路线数")
    assert {w["where"] for w in vm["writes"]} == {"connection:e2"}
    assert any(r["kind"] == "reveal" for r in vm["reads"])


# --------------------------------------------------------------------------- #
# tag_occurrences
# --------------------------------------------------------------------------- #
def test_tag_occurrences(sample_project):
    occ = proj.tag_occurrences(sample_project, "t-hook-voice")
    _json_roundtrip(occ)
    # 两场都挂了这个 tag,各自有一条成文行
    scene_items = [o for o in occ if o["kind"] == "scene"]
    line_items = [o for o in occ if o["kind"] == "line"]
    assert {o["id"] for o in scene_items} == {"s-start", "s-mirror"}
    assert {o["id"] for o in line_items} == {"pl-1", "pl-2"}


# --------------------------------------------------------------------------- #
# idea_alignment
# --------------------------------------------------------------------------- #
def test_idea_alignment(sample_project):
    al = proj.idea_alignment(sample_project, "碳基与电子流的爱孰轻孰重")
    _json_roundtrip(al)
    assert [s["scene_id"] for s in al] == ["s-start"]
    assert al[0]["status"] == "tocomplete"


# --------------------------------------------------------------------------- #
# drilldown
# --------------------------------------------------------------------------- #
def test_drilldown(sample_project):
    dd = proj.drilldown(sample_project, "s-start")
    _json_roundtrip(dd)
    assert set(dd.keys()) == {"beat", "scene_semantic", "prose"}
    sem = dd["scene_semantic"]
    assert sem["objective_events"] == ["枫钟在论坛发了一段录音", "Vilo 回复了一句"]
    # value_shift 用 alias("from")
    assert sem["value_shift"]["from"] == "无关注"
    assert sem["value_shift"]["to"] == "被声音吸引"
    assert sem["intent"]["emotion"] == "克制的悸动"
    # 成文行经 line_refs
    assert [p["id"] for p in dd["prose"]] == ["pl-1"]


def test_drilldown_missing_scene(sample_project):
    dd = proj.drilldown(sample_project, "nope")
    assert dd["beat"] is None
    assert dd["prose"] == []


# --------------------------------------------------------------------------- #
# distribution
# --------------------------------------------------------------------------- #
def test_distribution(sample_project):
    d = proj.distribution(sample_project)
    _json_roundtrip(d)
    assert set(d.keys()) == {"character_matrix", "pov_ratio", "line_supply", "act_lengths"}
    # 枫钟 两场 pov → 比例 1.0
    assert d["pov_ratio"]["c-fengzhong"] == 1.0
    # storyline sl-fz 关联 1 场(s-start)
    assert d["line_supply"]["sl-fz"] == 1
    # 无 route 时 act_lengths 为空(场景无 beat 归属)
    assert d["act_lengths"] == {}


def test_distribution_act_lengths_with_route(sample_project):
    # b-meet 是 b-act1 的子;场景挂到 b-meet 应计入顶层 b-act1
    for n in sample_project.nodes:
        if n.id in ("n-start", "n-mirror"):
            n.route = "b-meet"
    d = proj.distribution(sample_project)
    assert d["act_lengths"]["b-act1"] == 2
    assert d["character_matrix"]["c-fengzhong"]["b-meet"] == 2


# --------------------------------------------------------------------------- #
# provenance_forward
# --------------------------------------------------------------------------- #
def test_provenance_forward(sample_project):
    # 夹具默认无 provenance,先注入
    from omnicompany.packages.narrative_studio import models as m
    sample_project.characters[0].provenance = m.Provenance(source="seeds/00.md")
    sample_project.scenes[0].provenance = m.Provenance(source="seeds/00.md")
    sample_project.scenes[1].provenance = m.Provenance(source="other.md")

    fwd = proj.provenance_forward(sample_project, "seeds/00.md")
    _json_roundtrip(fwd)
    kinds = {(x["kind"], x["id"]) for x in fwd}
    assert ("character", "c-wangyang") in kinds
    assert ("scene", "s-start") in kinds
    assert ("scene", "s-mirror") not in kinds


def test_provenance_forward_empty(sample_project):
    assert proj.provenance_forward(sample_project, "none") == []
