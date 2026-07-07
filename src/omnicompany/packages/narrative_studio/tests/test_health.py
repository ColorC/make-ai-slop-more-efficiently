"""health.health_check 的契约测试:基于 conftest 的 sample_project 夹具。"""

from __future__ import annotations

import json

from omnicompany.packages.narrative_studio import health, models as m


def _codes(findings):
    return [f["code"] for f in findings]


def _by_code(findings, code):
    return [f for f in findings if f["code"] == code]


# --------------------------------------------------------------------------- #
# 基础:返回结构 + JSON 可序列化
# --------------------------------------------------------------------------- #
def test_returns_json_serializable_dicts(sample_project):
    findings = health.health_check(sample_project)
    assert isinstance(findings, list)
    # 整体可 JSON 透传
    json.dumps(findings, ensure_ascii=False)
    for f in findings:
        assert set(f.keys()) == {"code", "severity", "message", "location", "ref"}
        assert f["severity"] in ("high", "medium", "low")
        assert isinstance(f["message"], str) and f["message"]
        assert isinstance(f["location"], str) and f["location"]


# --------------------------------------------------------------------------- #
# 在 sample_project 上"应当触发"的检查
# --------------------------------------------------------------------------- #
def test_orphan_variable_fires_on_sample(sample_project):
    found = _by_code(health.health_check(sample_project), "orphan_variable")
    locs = {f["location"] for f in found}
    assert "variable:未用.孤儿变量" in locs
    # 被读/被写的变量不应被判孤儿
    assert "variable:枫钟.好感度" not in locs
    assert "variable:枫钟.了解程度" not in locs
    assert "variable:meta.通关路线数" not in locs


def test_scene_no_turn_fires_on_unfilled_value_shift(sample_project):
    found = _by_code(health.health_check(sample_project), "scene_no_turn")
    locs = {f["location"] for f in found}
    assert "scene:s-mirror" in locs       # 未填 value_shift
    assert "scene:s-start" not in locs    # from/to 都填了


def test_empty_prose_fires_on_null_text(sample_project):
    found = _by_code(health.health_check(sample_project), "empty_prose")
    locs = {f["location"] for f in found}
    assert "prose_line:pl-2" in locs      # text=None
    assert "prose_line:pl-1" not in locs  # 有成文


def test_required_empty_fires_on_empty_arc(sample_project):
    found = _by_code(health.health_check(sample_project), "required_empty")
    locs = {f["location"] for f in found}
    assert "character:c-fengzhong" in locs   # arc 四元全空
    assert "character:c-wangyang" not in locs  # 有 want/need
    assert "premise" not in locs               # proposition 已填


def test_reveal_no_rewrite_exempts_surface(sample_project):
    # 基线 surface 层无 rewrite 应豁免;非基线层无 rewrite 才报
    for r in sample_project.reveal_layers:
        if r.id == "rl-mid":
            r.rewrites = None
            r.rewrites_controlling_idea = None
    found = _by_code(health.health_check(sample_project), "reveal_no_rewrite")
    locs = {f["location"] for f in found}
    assert "reveal_layer:rl-mid" in locs          # 非基线层无 rewrite → 报
    assert "reveal_layer:rl-surface" not in locs  # 基线 surface 豁免


# --------------------------------------------------------------------------- #
# 在 sample_project 上"不应触发"的检查(数据健康)
# --------------------------------------------------------------------------- #
def test_clean_checks_silent_on_sample(sample_project):
    codes = _codes(health.health_check(sample_project))
    # 所有连接/结局/变量引用都健康
    assert "dangling_connection" not in codes
    assert "unreachable_node" not in codes
    assert "unreachable_ending" not in codes
    assert "ending_priority_conflict" not in codes
    assert "undefined_variable" not in codes
    assert "reveal_trigger_unreachable" not in codes
    # t-hook-voice 在两个场景出现(s-start + s-mirror),非"只埋未收"
    assert "unpaid_foreshadow" not in codes


# --------------------------------------------------------------------------- #
# 构造性:让"未在样例触发"的检查也真正命中一次
# --------------------------------------------------------------------------- #
def test_dangling_connection_detected(sample_project):
    sample_project.connections.append(
        m.Connection(id="e-bad", source="n-start", target="n-ghost")
    )
    found = _by_code(health.health_check(sample_project), "dangling_connection")
    assert any(f["location"] == "connection:e-bad" for f in found)
    assert all(f["severity"] == "high" for f in found)


def test_unreachable_node_detected(sample_project):
    sample_project.nodes.append(m.Node(id="n-island", title="孤岛"))
    found = _by_code(health.health_check(sample_project), "unreachable_node")
    assert any(f["location"] == "node:n-island" for f in found)


def test_undefined_variable_detected(sample_project):
    sample_project.connections.append(
        m.Connection(
            id="e-undef", source="n-start", target="n-mirror",
            condition=[m.Expr(var="不存在.变量", op="==", value=1)],
        )
    )
    found = _by_code(health.health_check(sample_project), "undefined_variable")
    assert any(f["location"] == "variable:不存在.变量" for f in found)


def test_ending_priority_conflict_detected(sample_project):
    # 加一个与现有结局同变量、同 priority 的结局
    sample_project.endings.append(
        m.Ending(
            node_id="n-end-good", name="影子结局",
            trigger=[m.Expr(var="枫钟.好感度", op=">=", value=50)],
            priority=10,
        )
    )
    found = _by_code(health.health_check(sample_project), "ending_priority_conflict")
    assert len(found) >= 1
    assert all(f["severity"] == "medium" for f in found)


def test_unreachable_ending_detected(sample_project):
    # 接线了(有入边)却从入口 BFS 不可达的结局 → 报;纯未接线占位则豁免
    sample_project.nodes.append(m.Node(id="n-island", type=m.NodeType.scene, title="孤岛"))
    sample_project.nodes.append(m.Node(id="n-end-island", type=m.NodeType.ending, title="孤岛结局"))
    sample_project.connections.append(
        m.Connection(id="e-island", source="n-island", target="n-end-island")
    )
    sample_project.endings.append(m.Ending(node_id="n-end-island", name="孤岛结局"))
    found = _by_code(health.health_check(sample_project), "unreachable_ending")
    assert any("n-end-island" in f["location"] for f in found)


def test_unpaid_foreshadow_detected(sample_project):
    # 加一个只在一个场景出现的伏笔标签
    sample_project.tags.append(m.Tag(id="t-lonely", name="孤伏笔", kind="foreshadow"))
    sample_project.scenes[0].tags.append("t-lonely")
    found = _by_code(health.health_check(sample_project), "unpaid_foreshadow")
    assert any(f["location"] == "tag:t-lonely" for f in found)


def test_reveal_trigger_unreachable_detected(sample_project):
    sample_project.reveal_layers.append(
        m.RevealLayer(
            id="rl-dead", order=m.RevealOrder.true_end,
            trigger=[m.Expr(var="meta.永不增长", op=">=", value=1)],
            rewrites="x",
        )
    )
    found = _by_code(health.health_check(sample_project), "reveal_trigger_unreachable")
    assert any(f["location"] == "reveal_layer:rl-dead" for f in found)


# --------------------------------------------------------------------------- #
# 稳健性:空项目不报错
# --------------------------------------------------------------------------- #
def test_empty_project_no_crash():
    p = m.Project(meta=m.ProjectMeta(id="empty", name="空"))
    findings = health.health_check(p)
    assert isinstance(findings, list)
    json.dumps(findings, ensure_ascii=False)
    # 空项目唯一确定会触发的是"立意命题为空"
    assert any(f["code"] == "required_empty" and f["location"] == "premise" for f in findings)
