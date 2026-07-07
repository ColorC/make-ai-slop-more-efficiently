"""queries 模块测试:用 conftest 的 sample_project 夹具,断言关键输出。"""

from __future__ import annotations

import json

from omnicompany.packages.narrative_studio import queries


# --------------------------------------------------------------------------- #
# completeness
# --------------------------------------------------------------------------- #
def test_completeness_shape_and_json_serializable(sample_project):
    res = queries.completeness(sample_project)
    # JSON 可序列化(HTTP 透传)
    json.dumps(res, ensure_ascii=False)

    assert "by_carrier" in res
    assert "overall" in res
    overall = res["overall"]
    for k in ("todo", "tocomplete", "done", "total", "percent_done"):
        assert k in overall

    # 各载体三态键齐全
    for carrier, bucket in res["by_carrier"].items():
        for k in ("todo", "tocomplete", "done", "empty", "total"):
            assert k in bucket, f"{carrier} 缺 {k}"


def test_completeness_scene_counts(sample_project):
    res = queries.completeness(sample_project)
    scenes = res["by_carrier"]["scenes"]
    # 夹具:s-start=tocomplete, s-mirror=todo
    assert scenes["total"] == 2
    assert scenes["tocomplete"] == 1
    assert scenes["todo"] == 1
    assert scenes["done"] == 0
    # s-mirror 缺 value_shift -> empty>=1
    assert scenes["empty"] >= 1


def test_completeness_characters(sample_project):
    res = queries.completeness(sample_project)
    chars = res["by_carrier"]["characters"]
    # 王杨 done,枫钟 todo
    assert chars["total"] == 2
    assert chars["done"] == 1
    assert chars["todo"] == 1
    # 枫钟无 arc.want/summary.sentence -> 至少 1 个 empty
    assert chars["empty"] >= 1


def test_completeness_overall_total_consistent(sample_project):
    res = queries.completeness(sample_project)
    overall = res["overall"]
    # overall.total 应等于各载体 total 之和
    s = sum(b["total"] for b in res["by_carrier"].values())
    assert overall["total"] == s
    # done=1(王杨) 含在 overall
    assert overall["done"] >= 1
    assert 0.0 <= overall["percent_done"] <= 100.0


# --------------------------------------------------------------------------- #
# empties
# --------------------------------------------------------------------------- #
def test_empties_hits_s_mirror_value_shift(sample_project):
    res = queries.empties(sample_project)
    json.dumps(res, ensure_ascii=False)

    by_id = {(e["entity_kind"], e["entity_id"]): e for e in res}
    assert ("scene", "s-mirror") in by_id
    assert "value_shift" in by_id[("scene", "s-mirror")]["missing_fields"]
    # s-mirror 还缺 intent.emotion
    assert "intent.emotion" in by_id[("scene", "s-mirror")]["missing_fields"]


def test_empties_hits_pl2_text(sample_project):
    res = queries.empties(sample_project)
    by_id = {(e["entity_kind"], e["entity_id"]): e for e in res}
    assert ("prose_line", "pl-2") in by_id
    assert "text" in by_id[("prose_line", "pl-2")]["missing_fields"]
    # pl-1 有 text,不应命中
    assert ("prose_line", "pl-1") not in by_id


def test_empties_character_fengzhong(sample_project):
    res = queries.empties(sample_project)
    by_id = {(e["entity_kind"], e["entity_id"]): e for e in res}
    # 枫钟缺 arc.want + summary.sentence
    assert ("character", "c-fengzhong") in by_id
    miss = by_id[("character", "c-fengzhong")]["missing_fields"]
    assert "arc.want" in miss
    assert "summary.sentence" in miss
    # 王杨齐全,不命中
    assert ("character", "c-wangyang") not in by_id


def test_empties_s_start_not_listed(sample_project):
    # s-start 三个关键字段都有(objective_events/value_shift/intent.emotion)
    res = queries.empties(sample_project)
    by_id = {(e["entity_kind"], e["entity_id"]): e for e in res}
    assert ("scene", "s-start") not in by_id


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
def test_search_empty_returns_empty(sample_project):
    assert queries.search(sample_project, "") == []
    assert queries.search(sample_project, "   ") == []
    assert queries.search(sample_project, None) == []


def test_search_jingchuang_hits_s_mirror(sample_project):
    res = queries.search(sample_project, "镜窗")
    json.dumps(res, ensure_ascii=False)
    hits = {(r["kind"], r["id"]) for r in res}
    # s-mirror 标题"镜窗初开"应命中
    assert ("scene", "s-mirror") in hits


def test_search_case_insensitive(sample_project):
    # 英文大小写不敏感:Vilo 出现在 premise.proposition / character.arc.need
    lower = queries.search(sample_project, "vilo")
    upper = queries.search(sample_project, "VILO")
    assert len(lower) > 0
    assert len(upper) > 0
    assert {(r["kind"], r["id"]) for r in lower} == {(r["kind"], r["id"]) for r in upper}


def test_search_objective_events(sample_project):
    # objective_events 里有"论坛发了一段录音"
    res = queries.search(sample_project, "录音")
    hits = {(r["kind"], r["id"]) for r in res}
    assert ("scene", "s-start") in hits


def test_search_result_shape(sample_project):
    res = queries.search(sample_project, "枫钟")
    assert len(res) > 0
    for r in res:
        for k in ("kind", "id", "title", "snippet"):
            assert k in r
