"""playthrough 演练求值器测试。复用 conftest 的 sample_project 夹具。"""

from __future__ import annotations

import copy

from omnicompany.packages.narrative_studio import models as m
from omnicompany.packages.narrative_studio import playthrough as pt


def _is_jsonable(obj) -> bool:
    import json
    try:
        json.dumps(obj, ensure_ascii=False)
        return True
    except (TypeError, ValueError):
        return False


def test_default_start_and_jsonable(sample_project):
    res = pt.playthrough(sample_project)
    # 默认起点应是无入边的 scene 节点 n-start
    assert res["visited"][0] == "n-start"
    # 全部返回值 JSON 可序列化
    assert _is_jsonable(res)
    # log 每条结构完整
    for entry in res["log"]:
        assert set(entry.keys()) == {"node_id", "title", "applied_effects", "chosen_edge"}


def test_low_favor_cannot_reach_ending(sample_project):
    """默认 好感度=0:e2 条件(>=50)不通过,走不到骄阳结局。"""
    res = pt.playthrough(sample_project)
    assert res["ending"] is None
    # 卡在 n-mirror,无可走边
    assert res["visited"] == ["n-start", "n-mirror"]
    assert res["stopped_reason"] == "no_edges"
    assert "n-end-good" not in res["visited"]
    # scene s-start 的 effects(了解程度 set 1)已应用
    assert res["state"]["枫钟.了解程度"] == 1


def test_high_favor_reaches_ending(sample_project):
    """把 枫钟.好感度 默认置 50:e2 条件通过,可走到骄阳结局。"""
    proj = copy.deepcopy(sample_project)
    for v in proj.variables:
        if v.namespace == "枫钟" and v.name == "好感度":
            v.default = 50
    res = pt.playthrough(proj)
    assert "n-end-good" in res["visited"]
    assert res["ending"] is not None
    assert res["ending"]["node_id"] == "n-end-good"
    assert res["ending"]["name"] == "骄阳结局"
    assert res["stopped_reason"] == "ending"
    # e2 的 effect(meta.通关路线数 += 1)已应用
    assert res["state"]["meta.通关路线数"] == 1


def test_available_choices(sample_project):
    """n-mirror 出发:好感度<50 时无可走边;>=50 时 e2 可走。"""
    state_low = {"枫钟.好感度": 0}
    assert pt.available_choices(sample_project, state_low, "n-mirror") == []

    state_high = {"枫钟.好感度": 50}
    avail = pt.available_choices(sample_project, state_high, "n-mirror")
    assert len(avail) == 1
    assert avail[0]["edge_id"] == "e2"
    assert avail[0]["target"] == "n-end-good"

    # n-start 无条件边 e1 恒可走
    avail_start = pt.available_choices(sample_project, {}, "n-start")
    assert [a["edge_id"] for a in avail_start] == ["e1"]


def test_reveal_triggers_when_meta_high(sample_project):
    """meta.通关路线数>=3 时 rl-mid 触发(>=7 时 rl-true 也触发)。"""
    proj = copy.deepcopy(sample_project)
    # 把好感度置高使能走通 + 让边把通关路线数推到 3
    for v in proj.variables:
        if v.namespace == "枫钟" and v.name == "好感度":
            v.default = 50
        if v.namespace == "meta" and v.name == "通关路线数":
            v.default = 2   # 走通 e2 再 +=1 -> 3
    for v in proj.meta_progress.fields:
        if v.namespace == "meta" and v.name == "通关路线数":
            v.default = 2
    res = pt.playthrough(proj)
    assert res["state"]["meta.通关路线数"] == 3
    assert "rl-mid" in res["reveals_triggered"]
    assert "rl-true" not in res["reveals_triggered"]


def test_reveal_not_triggered_when_low(sample_project):
    """通关路线数不到 3 时不触发 reveal。"""
    res = pt.playthrough(sample_project)
    assert res["reveals_triggered"] == []


def test_choices_consumed_in_order(sample_project):
    """构造分叉,验证 choices 按出现顺序消费 connection.id / target。"""
    proj = copy.deepcopy(sample_project)
    # 从 n-start 增加一条到 n-mirror2 的分叉
    proj.nodes.append(m.Node(id="n-mirror2", type=m.NodeType.scene, title="另一面镜"))
    proj.connections.append(m.Connection(id="e3", source="n-start", target="n-mirror2"))
    # 选 e3(按 id)
    res = pt.playthrough(proj, choices=["e3"])
    assert "n-mirror2" in res["visited"]
    assert "n-mirror" not in res["visited"]
    # 选 target n-mirror
    res2 = pt.playthrough(proj, choices=["n-mirror"])
    assert "n-mirror" in res2["visited"]
    assert "n-mirror2" not in res2["visited"]
    # 不给 choices -> 取第一条 e1(到 n-mirror)
    res3 = pt.playthrough(proj)
    assert "n-mirror" in res3["visited"]


def test_loop_protection():
    """自环必须被 loop 保护停住,不会无限。"""
    proj = m.Project(meta=m.ProjectMeta(id="loop", name="环"))
    proj.nodes = [m.Node(id="a", type=m.NodeType.hub, title="环点")]
    proj.connections = [m.Connection(id="self", source="a", target="a")]
    res = pt.playthrough(proj, max_steps=500)
    assert res["stopped_reason"] == "loop"
    assert res["ending"] is None


def test_empty_project_no_start():
    proj = m.Project(meta=m.ProjectMeta(id="empty", name="空"))
    res = pt.playthrough(proj)
    assert res["stopped_reason"] == "no_start"
    assert res["visited"] == []
