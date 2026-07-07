"""共享测试夹具:一个内容较全的样例 Project,兼作各模块契约范例。"""

from __future__ import annotations

import os
import sys

import pytest

# 保证 omnicompany 可导入(editable 安装时冗余但无害)
_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from omnicompany.packages.narrative_studio import models as m  # noqa: E402


@pytest.fixture
def sample_project() -> m.Project:
    p = m.Project(meta=m.ProjectMeta(id="demo", name="样例"))

    p.premise = m.Premise(
        proposition="一个渴望被理解的人造了一座镜子宫殿",
        controlling_ideas=["碳基与电子流的爱孰轻孰重"],
        stance="不惩罚欲望",
        locked=True,
    )
    p.reveal_layers = [
        m.RevealLayer(id="rl-surface", order=m.RevealOrder.surface, title="表层"),
        m.RevealLayer(
            id="rl-mid", order=m.RevealOrder.midpoint, title="中段揭示",
            trigger=[m.Expr(var="meta.通关路线数", op=">=", value=3)],
            rewrites="男主都是 Vilo 投影",
        ),
        m.RevealLayer(
            id="rl-true", order=m.RevealOrder.true_end, title="TrueEnd",
            trigger=[m.Expr(var="meta.通关路线数", op=">=", value=7)],
            rewrites="王杨奇点 + 玩家位置颠覆",
        ),
    ]

    p.world = [m.WorldNode(id="w-qingsha", name="青沙", children=[
        m.WorldNode(id="w-univ", name="青沙大学"),
    ])]

    p.characters = [
        m.Character(id="c-wangyang", name="王杨", importance="main",
                    arc=m.CharacterArc(want="与 Vilo 在一起", need="被以连续的人选择"),
                    summary=m.Summary(sentence="青梅竹马，真结局对象"),
                    status=m.Status.done),
        m.Character(id="c-fengzhong", name="枫钟", importance="main"),
    ]
    p.relationships = [
        m.Relationship(id="r1", a="c-wangyang", b="c-fengzhong", nature="同作攻略对象"),
    ]

    p.variables = [
        m.Variable(namespace="枫钟", name="好感度", type=m.VarType.int, default=0),
        m.Variable(namespace="枫钟", name="了解程度", type=m.VarType.int, default=0),
        m.Variable(namespace="meta", name="通关路线数", type=m.VarType.int, default=0),
        m.Variable(namespace="未用", name="孤儿变量", type=m.VarType.int, default=0),
    ]
    p.meta_progress = m.MetaProgress(fields=[
        m.Variable(namespace="meta", name="通关路线数", type=m.VarType.int, default=0),
    ])
    p.stat_blocks = [m.StatBlock(name="攻略对象数值",
                                 fields=["好感度", "了解程度"],
                                 applies_to=["c-wangyang", "c-fengzhong"])]

    p.beats = [
        m.Beat(id="b-act1", title="第一幕", position=0),
        m.Beat(id="b-meet", parent="b-act1", title="初遇", position=0),
    ]
    p.storylines = [m.StoryLine(id="sl-fz", title="枫钟线", color="#88c")]
    p.arc = m.Arc(emotional=["相遇", "好奇"])
    p.pacing = [m.PacingMarker(kind="phase", name="接触期", position=0)]

    p.nodes = [
        m.Node(id="n-start", type=m.NodeType.scene, title="声音进来了", x=0, y=0),
        m.Node(id="n-mirror", type=m.NodeType.scene, title="镜窗初开", x=200, y=0),
        m.Node(id="n-end-good", type=m.NodeType.ending, title="骄阳结局", x=400, y=-50),
    ]
    p.connections = [
        m.Connection(id="e1", source="n-start", target="n-mirror"),
        m.Connection(id="e2", source="n-mirror", target="n-end-good",
                     condition=[m.Expr(var="枫钟.好感度", op=">=", value=50)],
                     effects=[m.Expr(var="meta.通关路线数", op="+=", value=1)]),
    ]
    p.endings = [
        m.Ending(node_id="n-end-good", name="骄阳结局",
                 trigger=[m.Expr(var="枫钟.好感度", op=">=", value=50)], priority=10),
    ]

    p.scenes = [
        m.Scene(
            id="s-start", node_ref="n-start", title="声音进来了",
            links=m.SceneLinks(pov="c-fengzhong", characters=["c-fengzhong"], lines=["sl-fz"]),
            objective_events=["枫钟在论坛发了一段录音", "Vilo 回复了一句"],
            value_shift={"from": "无关注", "to": "被声音吸引"},
            intent=m.Intent(emotion="克制的悸动"),
            effects=[m.Expr(var="枫钟.了解程度", op="set", value=1)],
            line_refs=["pl-1"],
            tags=["t-hook-voice"],
            serves_ideas=["碳基与电子流的爱孰轻孰重"],
            status=m.Status.tocomplete,
        ),
        m.Scene(
            id="s-mirror", node_ref="n-mirror", title="镜窗初开",
            links=m.SceneLinks(pov="c-fengzhong", characters=["c-fengzhong"]),
            objective_events=["独处时窄聊天窗亮起"],
            # 故意不填 value_shift,触发健康检查
            line_refs=["pl-2"],
            tags=["t-hook-voice"],
            status=m.Status.todo,
        ),
    ]

    p.prose_lines = [
        m.ProseLine(id="pl-1", scene_ref="s-start", speaker="枫钟", text="今天公园的风里有金属声。"),
        m.ProseLine(id="pl-2", scene_ref="s-mirror", text=None, status=m.Status.todo),  # 空成文
    ]
    p.voices = [m.Voice(id="v-watch", register_id="scene_advance", syntax="短句")]
    p.registers = [m.Register(id="scene_advance", rule="极乐式多声部")]
    p.style_matrix = [m.StyleMatrixEntry(emotion="危险好奇", scene_type="独处镜窗", register_id="scene_advance")]

    p.tags = [m.Tag(id="t-hook-voice", name="声音伏笔", kind="foreshadow")]
    p.notes = [m.Note(id="note-1", text="随手记")]
    return p
